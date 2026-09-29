"""Migration 018 — allow 'workday' as an employer_sources adapter.

SQLite can't ALTER a CHECK constraint in place, so this rebuilds the table
with the widened constraint and copies the data across. Runs with foreign
keys off (migrate.py's connection default, unlike the app's own connections
in database.py) so the brief window without an employer_sources table
doesn't trip employer_source_roles' FK to it; ids are preserved exactly via
the INSERT...SELECT, so that relationship (and the app's own
PRAGMA foreign_keys=ON connections) resume working unchanged afterward.
"""

VERSION = '018'
DESCRIPTION = "Allow workday as an employer_sources adapter"


def up(db):
    db.executescript('''
        CREATE TABLE employer_sources_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            company_name TEXT NOT NULL,
            company_key TEXT NOT NULL,
            company_aliases_json TEXT NOT NULL DEFAULT '[]',
            careers_url TEXT,
            careers_url_key TEXT NOT NULL DEFAULT '',
            adapter TEXT NOT NULL CHECK(adapter IN ('successfactors','workday','serpapi_company')),
            enabled_all_roles INTEGER NOT NULL DEFAULT 0 CHECK(enabled_all_roles IN (0,1)),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            last_success_at TEXT,
            last_error TEXT,
            last_checked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, company_key, careers_url_key)
        );
        INSERT INTO employer_sources_new
            (id,user_id,company_name,company_key,company_aliases_json,careers_url,
             careers_url_key,adapter,enabled_all_roles,enabled,last_success_at,
             last_error,last_checked_at,created_at,updated_at)
        SELECT id,user_id,company_name,company_key,company_aliases_json,careers_url,
               careers_url_key,adapter,enabled_all_roles,enabled,last_success_at,
               last_error,last_checked_at,created_at,updated_at
        FROM employer_sources;
        DROP TABLE employer_sources;
        ALTER TABLE employer_sources_new RENAME TO employer_sources;
        CREATE INDEX IF NOT EXISTS idx_employer_sources_user_enabled
            ON employer_sources(user_id, enabled, enabled_all_roles);
    ''')
    db.commit()


def verify(db):
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='employer_sources'"
    ).fetchone()
    sql = (row[0] or '') if row else ''
    columns = {r[1] for r in db.execute('PRAGMA table_info(employer_sources)')}
    indexes = {r[1] for r in db.execute('PRAGMA index_list(employer_sources)')}
    required = {'user_id', 'company_name', 'company_key', 'company_aliases_json',
                'careers_url', 'careers_url_key', 'adapter', 'enabled_all_roles',
                'enabled', 'last_success_at', 'last_error', 'last_checked_at'}
    return ("'workday'" in sql and required.issubset(columns)
            and 'idx_employer_sources_user_enabled' in indexes)


def down(db):
    db.executescript('''
        CREATE TABLE employer_sources_old (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            company_name TEXT NOT NULL,
            company_key TEXT NOT NULL,
            company_aliases_json TEXT NOT NULL DEFAULT '[]',
            careers_url TEXT,
            careers_url_key TEXT NOT NULL DEFAULT '',
            adapter TEXT NOT NULL CHECK(adapter IN ('successfactors','serpapi_company')),
            enabled_all_roles INTEGER NOT NULL DEFAULT 0 CHECK(enabled_all_roles IN (0,1)),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            last_success_at TEXT,
            last_error TEXT,
            last_checked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, company_key, careers_url_key)
        );
        INSERT INTO employer_sources_old
            SELECT * FROM employer_sources WHERE adapter != 'workday';
        DROP TABLE employer_sources;
        ALTER TABLE employer_sources_old RENAME TO employer_sources;
        CREATE INDEX IF NOT EXISTS idx_employer_sources_user_enabled
            ON employer_sources(user_id, enabled, enabled_all_roles);
    ''')
    db.commit()
