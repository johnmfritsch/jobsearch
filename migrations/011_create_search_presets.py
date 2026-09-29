"""Migration 011 — Named criteria and resume presets per user."""

VERSION = '011'
DESCRIPTION = 'Create search presets table'


def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS search_presets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT NOT NULL COLLATE NOCASE,
            config_json  TEXT NOT NULL,
            resume_text  TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, name)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_search_presets_user_id '
               'ON search_presets(user_id)')
    db.commit()


def verify(db):
    columns = {
        row[1] for row in db.execute('PRAGMA table_info(search_presets)')
    }
    return {
        'id', 'user_id', 'name', 'config_json', 'resume_text',
        'created_at', 'updated_at'
    }.issubset(columns)


def down(db):
    db.execute('DROP TABLE IF EXISTS search_presets')
    db.commit()
