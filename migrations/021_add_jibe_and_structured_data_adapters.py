"""Migration 021 — add probe-backed Jibe and structured-data adapters."""

VERSION = '021'
DESCRIPTION = 'Allow jibe and structured_data employer-source adapters'


_CURRENT = "'successfactors','workday','ukg','serpapi_company','unsupported'"
_WIDENED = "'successfactors','workday','ukg','jibe','structured_data','serpapi_company','unsupported'"


def _rebuild(db, adapters, downgrade=False):
    # Migrate.py normally opens SQLite with foreign keys disabled. Explicitly
    # suspend and restore it anyway so the rebuild is safe in focused tests or
    # future runner changes, while IDs keep employer_source_roles intact.
    enabled = bool(db.execute('PRAGMA foreign_keys').fetchone()[0])
    if enabled:
        db.commit()
        db.execute('PRAGMA foreign_keys=OFF')
    db.executescript('''
        CREATE TABLE employer_sources_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            company_name TEXT NOT NULL,
            company_key TEXT NOT NULL,
            company_aliases_json TEXT NOT NULL DEFAULT '[]',
            careers_url TEXT,
            careers_url_key TEXT NOT NULL DEFAULT '',
            adapter TEXT NOT NULL CHECK(adapter IN ({})),
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
            (id,user_id,company_name,company_key,company_aliases_json,careers_url,careers_url_key,
             adapter,enabled_all_roles,enabled,last_success_at,last_error,last_checked_at,created_at,updated_at)
        SELECT id,user_id,company_name,company_key,company_aliases_json,careers_url,careers_url_key,
               {},enabled_all_roles,enabled,last_success_at,last_error,last_checked_at,created_at,updated_at
        FROM employer_sources;
        DROP TABLE employer_sources;
        ALTER TABLE employer_sources_new RENAME TO employer_sources;
        CREATE INDEX IF NOT EXISTS idx_employer_sources_user_enabled
            ON employer_sources(user_id, enabled, enabled_all_roles);
    '''.format(adapters, "CASE WHEN adapter IN ('jibe','structured_data') THEN 'unsupported' ELSE adapter END" if downgrade else 'adapter'))
    db.commit()
    if enabled:
        db.execute('PRAGMA foreign_keys=ON')


def up(db):
    _rebuild(db, _WIDENED)


def verify(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='employer_sources'").fetchone()
    sql = (row[0] or '') if row else ''
    columns = {item[1] for item in db.execute('PRAGMA table_info(employer_sources)')}
    indexes = {item[1] for item in db.execute('PRAGMA index_list(employer_sources)')}
    required = {'user_id', 'company_name', 'company_key', 'company_aliases_json', 'careers_url',
                'careers_url_key', 'adapter', 'enabled_all_roles', 'enabled', 'last_success_at',
                'last_error', 'last_checked_at', 'created_at', 'updated_at'}
    # Validate the relationships this table rebuild can affect. A whole-DB
    # check would make an unrelated historic record elsewhere block this
    # migration, while these two scoped checks prove both sides of the
    # employer-source/role relationship survived the parent-table rebuild.
    return ("'jibe'" in sql and "'structured_data'" in sql and required.issubset(columns)
            and 'idx_employer_sources_user_enabled' in indexes
            and not list(db.execute('PRAGMA foreign_key_check(employer_sources)'))
            and not list(db.execute('PRAGMA foreign_key_check(employer_source_roles)')))


def down(db):
    # Keep every source and every role membership: old code renders these as
    # the already-supported explicit unsupported->SerpAPI fallback.
    _rebuild(db, _CURRENT, downgrade=True)
