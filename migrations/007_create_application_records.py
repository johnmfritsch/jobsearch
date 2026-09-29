"""
Migration 007 — Create application_records table

Application-tracker state per job per user: Saved / Applied / Interviewing /
Closed, notes, dates, stage history — stored as record_json, one row per
(user, job_key).
"""

VERSION     = '007'
DESCRIPTION = 'Create application_records table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS application_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            job_key     TEXT NOT NULL,
            record_json TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, job_key)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_application_records_user_id ON application_records(user_id)')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='application_records'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS application_records')
    db.commit()
