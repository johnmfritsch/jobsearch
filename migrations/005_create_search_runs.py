"""
Migration 005 — Create search_runs table

One row per job-search run (live or test mode). status_json carries the
progress-message history that the old JSON job_status.json file held;
main.py / profile_store.write_run_status updates this row while a run is
in flight. state is one of: running, complete, error, cancelled.
"""

VERSION     = '005'
DESCRIPTION = 'Create search_runs table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS search_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            test_mode     INTEGER NOT NULL,
            state         TEXT NOT NULL,
            status_json   TEXT NOT NULL,
            pid           INTEGER,
            started_at    TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at  TEXT
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_search_runs_user_id ON search_runs(user_id)')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='search_runs'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS search_runs')
    db.commit()
