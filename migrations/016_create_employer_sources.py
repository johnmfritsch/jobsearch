"""Migration 016 — account-scoped priority employer sources."""

VERSION = '016'
DESCRIPTION = 'Create priority employer sources'


def up(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS employer_sources (
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
        CREATE INDEX IF NOT EXISTS idx_employer_sources_user_enabled
            ON employer_sources(user_id, enabled, enabled_all_roles);
    ''')
    db.commit()


def verify(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(employer_sources)')}
    required = {'user_id', 'company_name', 'company_key', 'company_aliases_json',
                'careers_url', 'careers_url_key', 'adapter', 'enabled_all_roles',
                'enabled', 'last_success_at', 'last_error', 'last_checked_at'}
    indexes = {row[1]: row[2] for row in db.execute('PRAGMA index_list(employer_sources)')}
    unique_columns = []
    for name, unique in indexes.items():
        if unique:
            unique_columns.append({row[2] for row in db.execute('PRAGMA index_info({})'.format(name))})
    return (required.issubset(columns)
            and any({'user_id', 'company_key', 'careers_url_key'}.issubset(item)
                    for item in unique_columns)
            and 'idx_employer_sources_user_enabled' in indexes)


def down(db):
    db.execute('DROP TABLE IF EXISTS employer_sources')
    db.commit()
