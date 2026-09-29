"""
Migration 001 — Create users table

legacy_directory is kept as a plain informational/idempotency key for the
one-time profile importer (maps a migrated account back to its old
configs/<name> directory) — it is NOT part of an account-claim mechanism.
Per the 2026-08-03 cutover decision, existing users activate via the normal
password-reset flow, not a claim code, so there is no imported_profile_claims
table here.
"""

VERSION     = '001'
DESCRIPTION = 'Create users table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            email             TEXT NOT NULL UNIQUE,
            password_hash     TEXT NOT NULL,
            display_name      TEXT NOT NULL,
            profile_key       TEXT UNIQUE,
            legacy_directory  TEXT UNIQUE,
            is_admin          INTEGER NOT NULL DEFAULT 0,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            is_active         INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS users')
    db.commit()
