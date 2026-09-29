"""
Migration 002 — Create user_profiles table

One row per user: search config, resume text, and onboarding state. Carried
forward from the old DEV database.py schema unchanged.
"""

VERSION     = '002'
DESCRIPTION = 'Create user_profiles table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            config_json         TEXT NOT NULL DEFAULT '{}',
            test_config_json    TEXT NOT NULL DEFAULT '{}',
            config_prev_json    TEXT,
            test_config_prev_json TEXT,
            resume_text         TEXT NOT NULL DEFAULT '',
            onboarding_state    TEXT NOT NULL DEFAULT 'complete',
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_profiles'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS user_profiles')
    db.commit()
