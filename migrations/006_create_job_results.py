"""
Migration 006 — Create job_results table

Published results for a search run. One row per matched job.
"""

VERSION     = '006'
DESCRIPTION = 'Create job_results table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS job_results (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id   INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
            job_key  TEXT NOT NULL,
            job_json TEXT NOT NULL,
            score    REAL,
            UNIQUE(run_id, job_key)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_job_results_run_id ON job_results(run_id)')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_results'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS job_results')
    db.commit()
