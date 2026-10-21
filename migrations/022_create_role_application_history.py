"""Migration 022 — role-level work history and education for the apply agent."""

VERSION = '022'
DESCRIPTION = 'Create role_application_history (per-role work history and education)'


def up(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS role_application_history (
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_key      TEXT NOT NULL,
            history_json  TEXT NOT NULL DEFAULT '{}',
            updated_at    TEXT NOT NULL,
            PRIMARY KEY (user_id, role_key)
        );
    ''')
    db.commit()


def down(db):
    db.execute('DROP TABLE IF EXISTS role_application_history')
