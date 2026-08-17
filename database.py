"""DEV SQLite foundation for the JobSearch account migration."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("data") / "jobsearch_dev.db"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL, profile_key TEXT UNIQUE, legacy_directory TEXT UNIQUE,
  is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  config_json TEXT NOT NULL DEFAULT '{}', test_config_json TEXT NOT NULL DEFAULT '{}',
  config_prev_json TEXT, test_config_prev_json TEXT,
  resume_text TEXT NOT NULL DEFAULT '', onboarding_state TEXT NOT NULL DEFAULT 'complete',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS application_records (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_key TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, job_key)
);
CREATE TABLE IF NOT EXISTS saved_views (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, filters_json TEXT NOT NULL, UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  test_mode INTEGER NOT NULL, state TEXT NOT NULL, status_json TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS job_results (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
  job_key TEXT NOT NULL, job_json TEXT NOT NULL, score REAL, UNIQUE(run_id, job_key)
);
CREATE TABLE IF NOT EXISTS imported_profile_claims (
  legacy_directory TEXT PRIMARY KEY, code_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

def get_db(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    # Existing DEV databases predate legacy_directory.  Keep this migration
    # idempotent so opening the database never requires manual intervention.
    columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    if "legacy_directory" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN legacy_directory TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_legacy_directory_unique ON users(legacy_directory) WHERE legacy_directory IS NOT NULL")
        db.commit()
    if "profile_key" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN profile_key TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_profile_key_unique ON users(profile_key) WHERE profile_key IS NOT NULL")
        db.execute("UPDATE users SET profile_key=legacy_directory WHERE profile_key IS NULL AND legacy_directory IS NOT NULL")
        db.commit()
    profile_columns = {row["name"] for row in db.execute("PRAGMA table_info(user_profiles)")}
    for name, definition in (("test_config_json", "TEXT NOT NULL DEFAULT '{}'"), ("config_prev_json", "TEXT"), ("test_config_prev_json", "TEXT"), ("onboarding_state", "TEXT NOT NULL DEFAULT 'complete'")):
        if name not in profile_columns:
            db.execute(f"ALTER TABLE user_profiles ADD COLUMN {name} {definition}")
    db.commit()
    return db
