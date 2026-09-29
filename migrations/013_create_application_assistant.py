"""Migration 013 — Supervised application assistant state and artifacts."""

VERSION = '013'
DESCRIPTION = 'Create supervised application assistant tables'


def up(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS application_profiles (
            user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            profile_json  TEXT NOT NULL DEFAULT '{}',
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS application_attempts (
            id                    TEXT PRIMARY KEY,
            user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            job_key               TEXT NOT NULL,
            job_json              TEXT NOT NULL,
            source_url            TEXT,
            resolved_url          TEXT,
            ats_kind              TEXT NOT NULL DEFAULT 'unknown',
            state                 TEXT NOT NULL,
            status_message        TEXT NOT NULL,
            error_code            TEXT,
            automation_used       INTEGER NOT NULL DEFAULT 0,
            answers_json          TEXT NOT NULL DEFAULT '{}',
            selected_resume_id    TEXT,
            selected_cover_id     TEXT,
            confirmation_url      TEXT,
            confirmation_text     TEXT,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            completed_at          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_application_attempts_user_updated
            ON application_attempts(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_application_attempts_user_job
            ON application_attempts(user_id, job_key, updated_at DESC);

        CREATE TABLE IF NOT EXISTS application_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id     TEXT NOT NULL REFERENCES application_attempts(id) ON DELETE CASCADE,
            state          TEXT NOT NULL,
            message        TEXT NOT NULL,
            metadata_json  TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_application_events_attempt
            ON application_events(attempt_id, id);

        CREATE TABLE IF NOT EXISTS application_artifacts (
            id                  TEXT PRIMARY KEY,
            attempt_id          TEXT NOT NULL REFERENCES application_attempts(id) ON DELETE CASCADE,
            user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind                TEXT NOT NULL,
            format              TEXT NOT NULL,
            file_name           TEXT NOT NULL,
            relative_path       TEXT NOT NULL,
            sha256              TEXT NOT NULL,
            mime_type           TEXT NOT NULL,
            approval_state      TEXT NOT NULL DEFAULT 'pending',
            version             INTEGER NOT NULL DEFAULT 1,
            input_tokens        INTEGER NOT NULL DEFAULT 0,
            output_tokens       INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd  REAL NOT NULL DEFAULT 0,
            actual_cost_usd     REAL NOT NULL DEFAULT 0,
            generated_at        TEXT NOT NULL,
            approved_at         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_application_artifacts_attempt
            ON application_artifacts(attempt_id, kind, version);

        CREATE TABLE IF NOT EXISTS application_confirmation_tokens (
            token_hash   TEXT PRIMARY KEY,
            attempt_id   TEXT NOT NULL REFERENCES application_attempts(id) ON DELETE CASCADE,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at   TEXT NOT NULL,
            used_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_application_confirmation_attempt
            ON application_confirmation_tokens(attempt_id, user_id);
    ''')
    db.commit()


def verify(db):
    required = {
        'application_profiles', 'application_attempts', 'application_events',
        'application_artifacts', 'application_confirmation_tokens',
    }
    present = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    columns = {
        row[1] for row in db.execute('PRAGMA table_info(application_attempts)')
    }
    return required.issubset(present) and {
        'automation_used', 'state', 'job_key', 'selected_resume_id',
        'selected_cover_id',
    }.issubset(columns)


def down(db):
    db.executescript('''
        DROP TABLE IF EXISTS application_confirmation_tokens;
        DROP TABLE IF EXISTS application_artifacts;
        DROP TABLE IF EXISTS application_events;
        DROP TABLE IF EXISTS application_attempts;
        DROP TABLE IF EXISTS application_profiles;
    ''')
    db.commit()
