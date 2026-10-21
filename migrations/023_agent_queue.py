"""Migration 023 — apply queue for the desktop agent: attempt columns, session keys, question bank."""

VERSION = '023'
DESCRIPTION = 'Add agent queue columns, desktop session keys and the application question bank'

ATTEMPT_COLUMNS = (
    ('role_key', 'TEXT'),
    ('agent_owned', 'INTEGER NOT NULL DEFAULT 0'),
    ('claim_id', 'TEXT'),
    ('claimed_at', 'TEXT'),
    ('lease_expires_at', 'TEXT'),
    ('posting_status', 'TEXT'),
    ('posting_checked_at', 'TEXT'),
    ('review_json', 'TEXT'),
    ('user_approved_at', 'TEXT'),
    ('approval_note', 'TEXT'),
)


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def up(db):
    existing = _columns(db, 'application_attempts')
    for name, definition in ATTEMPT_COLUMNS:
        if name not in existing:
            db.execute(f'ALTER TABLE application_attempts ADD COLUMN {name} {definition}')
    db.executescript('''
        CREATE INDEX IF NOT EXISTS idx_application_attempts_agent_queue
            ON application_attempts(user_id, state, updated_at);

        CREATE TABLE IF NOT EXISTS desktop_session_keys (
            key_id        TEXT NOT NULL UNIQUE,
            token_hash    TEXT PRIMARY KEY,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label         TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            last_used_at  TEXT,
            expires_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_desktop_session_keys_user
            ON desktop_session_keys(user_id);

        CREATE TABLE IF NOT EXISTS application_question_bank (
            id                   TEXT PRIMARY KEY,
            user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            question_key         TEXT NOT NULL,
            question_text        TEXT NOT NULL,
            answer_text          TEXT NOT NULL DEFAULT '',
            field_type           TEXT NOT NULL DEFAULT 'text',
            options_json         TEXT NOT NULL DEFAULT '[]',
            source               TEXT NOT NULL,
            status               TEXT NOT NULL,
            safe_to_autofill     INTEGER NOT NULL DEFAULT 0,
            times_seen           INTEGER NOT NULL DEFAULT 1,
            blocking_attempt_id  TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL,
            UNIQUE(user_id, question_key)
        );
    ''')
    db.commit()


def verify(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return ({'desktop_session_keys', 'application_question_bank'}.issubset(tables)
            and {name for name, _ in ATTEMPT_COLUMNS}.issubset(_columns(db, 'application_attempts')))


def down(db):
    db.execute('DROP TABLE IF EXISTS application_question_bank')
    db.execute('DROP TABLE IF EXISTS desktop_session_keys')
    # The new application_attempts columns stay, as in migration 014: older NAS
    # SQLite builds cannot drop a column safely, and they are additive.
