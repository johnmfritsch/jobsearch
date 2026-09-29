"""Migration 017 — per-role membership for priority employer sources.

Role membership previously lived inside the swappable `employer_source_ids`
field of `user_profiles.config_json` / `search_presets.config_json`, so it
only ever reflected whichever role happened to be active at save time and
could not be edited for a role other than the current one. This table makes
membership an independent fact: (source_id, preset_id) rows, with preset_id=0
standing in for the unsaved "Default" role (search_presets ids start at 1).
"""
import json

VERSION = '017'
DESCRIPTION = 'Create per-role employer source membership'


def up(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS employer_source_roles (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES employer_sources(id) ON DELETE CASCADE,
            preset_id INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(source_id, preset_id)
        );
        CREATE INDEX IF NOT EXISTS idx_employer_source_roles_source
            ON employer_source_roles(source_id);
        CREATE INDEX IF NOT EXISTS idx_employer_source_roles_preset
            ON employer_source_roles(preset_id);
    ''')
    # Best-effort backfill: the old model only ever exposed the selection for
    # whichever role/preset was active at the moment a profile or preset was
    # saved, so this recovers whatever is visible in that data rather than a
    # true history. Anything not captured here just needs to be re-checked
    # once in the (now-persistent, per-role) UI.
    now = __import__('datetime').datetime.utcnow().isoformat()
    seen = set()

    def seed(source_id, preset_id):
        key = (source_id, preset_id)
        if key in seen:
            return
        seen.add(key)
        db.execute(
            'INSERT OR IGNORE INTO employer_source_roles(source_id,preset_id,created_at) '
            'VALUES (?,?,?)', (source_id, preset_id, now))

    for row in db.execute('SELECT user_id, config_json FROM user_profiles'):
        try:
            config = json.loads(row['config_json'] or '{}')
        except (TypeError, ValueError):
            continue
        preset_id = int(config.get('active_search_preset_id') or 0)
        for source_id in config.get('employer_source_ids', []):
            if str(source_id).isdigit():
                seed(int(source_id), preset_id)
    for row in db.execute('SELECT id, config_json FROM search_presets'):
        try:
            config = json.loads(row['config_json'] or '{}')
        except (TypeError, ValueError):
            continue
        for source_id in config.get('employer_source_ids', []):
            if str(source_id).isdigit():
                seed(int(source_id), row['id'])
    db.commit()


def verify(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(employer_source_roles)')}
    required = {'source_id', 'preset_id', 'created_at'}
    indexes = {row[1]: row[2] for row in db.execute('PRAGMA index_list(employer_source_roles)')}
    unique_columns = []
    for name, unique in indexes.items():
        if unique:
            unique_columns.append({row[2] for row in db.execute('PRAGMA index_info({})'.format(name))})
    return (required.issubset(columns)
            and any({'source_id', 'preset_id'}.issubset(item) for item in unique_columns)
            and 'idx_employer_source_roles_source' in indexes)


def down(db):
    db.execute('DROP TABLE IF EXISTS employer_source_roles')
    db.commit()
