"""One-way DEV importer; does not alter the existing per-user files."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from database import get_db

BASE = Path(__file__).parent

def read_json(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default

db = get_db()
for user_dir in sorted((BASE / "configs").iterdir()):
    if not user_dir.is_dir(): continue
    name = user_dir.name
    email = f"{name.lower()}@local.jobsearch"
    db.execute("INSERT OR IGNORE INTO users (email,password_hash,display_name,legacy_directory) VALUES (?,?,?,?)", (email, "!migration-pending-password!", name, name))
    db.execute("UPDATE users SET legacy_directory=? WHERE email=? AND legacy_directory IS NULL", (name, email))
    db.execute("UPDATE users SET profile_key=? WHERE legacy_directory=? AND profile_key IS NULL", (name, name))
    # A claimed account replaces the migration placeholder email, while its
    # legacy directory remains stable for the lifetime of this bridge.
    user_id = db.execute("SELECT id FROM users WHERE legacy_directory=?", (name,)).fetchone()[0]
    config = read_json(user_dir / "config.json", {})
    test_config = read_json(user_dir / "config_test.json", config)
    config_prev = read_json(user_dir / "config.json.prev", None)
    test_config_prev = read_json(user_dir / "config_test.json.prev", None)
    resume = (user_dir / "resume.txt").read_text(encoding="utf-8") if (user_dir / "resume.txt").exists() else ""
    # This is a one-way bootstrap.  Once SQLite owns the profile, a later
    # accidental importer run must never overwrite user edits from legacy files.
    db.execute("INSERT OR IGNORE INTO user_profiles (user_id,config_json,test_config_json,config_prev_json,test_config_prev_json,resume_text,updated_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)", (user_id, json.dumps(config), json.dumps(test_config), json.dumps(config_prev) if config_prev is not None else None, json.dumps(test_config_prev) if test_config_prev is not None else None, resume))
    for job_key, record in read_json(user_dir / "job_triage.json", {"jobs": {}}).get("jobs", {}).items():
        db.execute("INSERT OR REPLACE INTO application_records (user_id,job_key,record_json,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)", (user_id, job_key, json.dumps(record)))
    for view in read_json(user_dir / "saved_views.json", {"views": []}).get("views", []):
        if isinstance(view, dict) and view.get("name"):
            db.execute("INSERT OR REPLACE INTO saved_views (user_id,name,filters_json) VALUES (?,?,?)", (user_id, view["name"], json.dumps(view.get("filters", {}))))
db.commit(); print("Imported", db.execute("SELECT count(*) FROM users").fetchone()[0], "users")
db.close()
