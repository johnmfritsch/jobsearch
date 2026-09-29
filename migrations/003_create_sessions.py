"""
Migration 003 — Create sessions table

Server-side session store, kept deliberately instead of relying solely on
Flask's signed-cookie session: a signed cookie cannot be revoked, and the
2026-08-03 auth decision requires being able to force-logout an account
(e.g. after a password reset or suspected compromise). Only the SHA-256 hash
of the token is stored, matching the original auth_store.py design.
"""

VERSION     = '003'
DESCRIPTION = 'Create sessions table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash  TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at  TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS sessions')
    db.commit()
