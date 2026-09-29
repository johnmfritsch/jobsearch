"""Migration 020 — allow 'ukg' and 'unsupported' as employer_sources adapters.

'ukg' is the new direct UKG/UltiPro scrape adapter (scrapers/ukg_scraper.py).
'unsupported' is not a scraper at all -- it's the value profile_store now
assigns (via scrapers/adapter_registry.classify_url) when a user saves a
real careers URL that doesn't match any registered adapter. It behaves
identically to 'serpapi_company' at runtime (name-only SerpAPI search) but
is shown to the user with a distinct "ask your administrator to add support
for this site" message rather than the generic company-name-search copy,
since the two situations (never gave a URL vs. gave one this app can't
parse yet) call for different explanations.

SQLite can't ALTER a CHECK constraint in place, so this rebuilds the table
with the widened constraint and copies the data across -- same pattern as
migration 018, which added 'workday'.
"""

VERSION = '020'
DESCRIPTION = "Allow ukg and unsupported as employer_sources adapters"


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
            adapter TEXT NOT NULL CHECK(adapter IN ('successfactors','workday','ukg','serpapi_company','unsupported')),
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
    # Existing rows already saved as 'serpapi_company' with a real careers_url
    # were classified under the old, narrower rules (before 'ukg'/'unsupported'
    # existed) -- reclassify them with the current registry so a previously
    # -added UKG/UltiPro board (or any other now-recognized/still-unsupported
    # URL) gets the right adapter and messaging without the user re-saving it.
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scrapers.adapter_registry import classify_url
    rows = db.execute(
        "SELECT id, careers_url FROM employer_sources "
        "WHERE adapter='serpapi_company' AND careers_url IS NOT NULL AND careers_url != ''"
    ).fetchall()
    for row in rows:
        reclassified = classify_url(row['careers_url'])
        if reclassified != 'serpapi_company':
            db.execute('UPDATE employer_sources SET adapter=? WHERE id=?',
                      (reclassified, row['id']))
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
    return ("'ukg'" in sql and "'unsupported'" in sql and required.issubset(columns)
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
        INSERT INTO employer_sources_old
            SELECT id,user_id,company_name,company_key,company_aliases_json,careers_url,
                   careers_url_key,
                   CASE WHEN adapter = 'unsupported' THEN 'serpapi_company' ELSE adapter END,
                   enabled_all_roles,enabled,last_success_at,last_error,last_checked_at,
                   created_at,updated_at
            FROM employer_sources WHERE adapter != 'ukg';
        DROP TABLE employer_sources;
        ALTER TABLE employer_sources_old RENAME TO employer_sources;
        CREATE INDEX IF NOT EXISTS idx_employer_sources_user_enabled
            ON employer_sources(user_id, enabled, enabled_all_roles);
    ''')
    db.commit()
