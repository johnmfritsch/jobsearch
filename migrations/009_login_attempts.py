"""Track failed logins so the login endpoint can be rate limited.

Neither the pre-re-platform JobSearch nor GardenBuddy limited failed logins —
the password-reset flow was the only rate-limited path in either codebase.
Recorded per (email, ip) so one attacker cannot lock out a real user by
hammering their address from elsewhere.
"""

VERSION = '009'
DESCRIPTION = 'Create login_attempts table for login rate limiting'


def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            ip TEXT NOT NULL DEFAULT '',
            attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup '
               'ON login_attempts(email, ip, attempted_at)')


def down(db):
    db.execute('DROP INDEX IF EXISTS idx_login_attempts_lookup')
    db.execute('DROP TABLE IF EXISTS login_attempts')


def verify(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='login_attempts'"
    ).fetchone()
    return row is not None
