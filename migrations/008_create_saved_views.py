"""
Migration 008 — Create saved_views table

Named result-filter presets per user (e.g. "High-confidence remote roles").
"""

VERSION     = '008'
DESCRIPTION = 'Create saved_views table'

def up(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS saved_views (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            filters_json TEXT NOT NULL,
            UNIQUE(user_id, name)
        )
    ''')
    db.commit()

def verify(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='saved_views'")
    return cursor.fetchone() is not None

def down(db):
    db.execute('DROP TABLE IF EXISTS saved_views')
    db.commit()
