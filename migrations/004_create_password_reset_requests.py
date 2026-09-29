"""
Migration 004 — Create password_reset_requests table

Supports the GardenBuddy-pattern forgot-password cooldown (one reset email
per address per 10 minutes). A row is written only on an actual send, so
probing unknown addresses can't grow this table. This same flow is the
existing-user activation path chosen 2026-08-03 (see jobsearch_backlog #18)
in place of a bespoke claim-code mechanism.
"""

VERSION     = '004'
DESCRIPTION = 'Create password_reset_requests table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_requests (
            email         TEXT PRIMARY KEY,
            requested_at  TEXT NOT NULL
        )
    ''')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='password_reset_requests'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS password_reset_requests')
    db.commit()
