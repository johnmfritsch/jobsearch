#!/opt/bin/python3
# api_handler.py - Backend API for job search configuration management
"""
Simple HTTP API handler for job search agent operations:
- GET  /api/load_previous?user=<name>  → Returns config.json.prev
- POST /api/save with JSON body        → Saves config.json and rotates prev
- POST /api/run with user=<name>       → Runs job search and returns status
"""

import json
from datetime import datetime
import os
import sys
import signal
import socket
import subprocess
import shutil
import tempfile
import re
import auth_store
import profile_store
import database
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs



# Auto-detect BASE_DIR from script location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if "/DEV" in BASE_DIR or BASE_DIR.endswith("/DEV"):
    environment = "DEV"
else:
    environment = "PROD"
os.environ["JOB_SEARCH_ENV"] = environment
CONFIG_DIR = os.path.join(BASE_DIR, "configs")
TRIAGE_FILENAME = "job_triage.json"
SAVED_VIEWS_FILENAME = "saved_views.json"
TRIAGE_STATUSES = {"saved", "applied", "not_interested", "none"}
TRACKER_STAGES = {"saved", "applied", "interviewing", "offer", "closed"}


def get_safe_user_dir(user):
    """Return a user config directory only for an expected user-name token."""
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", user):
        return None
    user_dir = os.path.join(CONFIG_DIR, user)
    return user_dir if os.path.isdir(user_dir) else None


def load_triage(user_dir):
    """Read a user's decision state; malformed/missing files behave as empty."""
    return profile_store.load_triage(os.path.basename(user_dir))


def save_triage_atomic(user_dir, triage):
    """Persist triage without leaving a partial JSON file after an interruption."""
    profile_store.save_triage(os.path.basename(user_dir), triage)


def load_saved_views(user_dir):
    return profile_store.load_saved_views(os.path.basename(user_dir))


def save_saved_views_atomic(user_dir, saved_views):
    profile_store.save_saved_views(os.path.basename(user_dir), saved_views)

def get_config_filename(test_mode=False):
    """Return appropriate config filename based on mode"""
    return "config_test.json" if test_mode else "config.json"

def get_config_prev_filename(test_mode=False):
    """Return appropriate config.prev filename based on mode"""
    return "config_test.json.prev" if test_mode else "config.json.prev"



class APIHandler(BaseHTTPRequestHandler):
    def current_session_user(self):
        cookie = self.headers.get("Cookie", "")
        token = next((part.split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith("jobsearch_session=")), None)
        return auth_store.session_user(token)

    def require_session(self, data):
        user = self.current_session_user()
        if not user:
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Sign in required"}).encode())
            return None
        data["user"] = user["display_name"]
        return user

    def authorized_user_dir(self, supplied_user=None):
        """Resolve a filesystem profile from the session, never from request input."""
        user = self.current_session_user()
        if not user:
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Sign in required"}).encode())
            return None
        directory = user.get("profile_key")
        if not directory:
            self._set_headers(403)
            self.wfile.write(json.dumps({"error": "This account has no profile yet"}).encode())
            return None
        if supplied_user and supplied_user != directory:
            self._set_headers(403)
            self.wfile.write(json.dumps({"error": "That profile belongs to another account"}).encode())
            return None
        return directory, directory
    
    def _set_headers(self, status=200, content_type="application/json", cookie=None):
        """Set HTTP headers"""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def do_OPTIONS(self):
        """Handle preflight CORS requests"""
        self._set_headers(200)

    def do_GET(self):
        """Handle GET requests"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/health":
            self.handle_health_check()
        elif path == "/api/me":
            self.handle_me()
        elif path == "/api/load_config":
            self.handle_load_config(params)
        elif path == "/api/load_previous":
            self.handle_load_previous(params)
        elif path == "/api/serpapi_status":
            self.handle_serpapi_status(params)
        elif path == "/api/job_triage":
            self.handle_load_triage(params)
        elif path == "/api/saved_views":
            self.handle_load_saved_views(params)
        elif path == "/api/latest_results":
            self.handle_latest_results(params)
        elif path == "/api/search_history":
            self.handle_search_history(params)
        elif path == "/api/resume":
            self.handle_load_resume(params)
        elif path == "/api/credentials":
            self.handle_credentials(params)
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def do_POST(self):
        """Handle POST requests"""
        parsed = urlparse(self.path)
        path = parsed.path

        # Read POST body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return

        if path == "/api/save":
            self.handle_save(data)
        elif path == "/api/register":
            self.handle_register(data)
        elif path == "/api/claim_imported":
            self.handle_claim_imported(data)
        elif path == "/api/login":
            self.handle_login(data)
        elif path == "/api/logout":
            self.handle_logout()
        elif path == "/api/run":
            self.handle_run(data)
        elif path == "/api/job_status":
            self.handle_job_status(data)
        elif path == "/api/cancel":
            self.handle_cancel(data)
        elif path == "/api/job_triage":
            self.handle_save_triage(data)
        elif path == "/api/application_tracker":
            self.handle_save_tracker(data)
        elif path == "/api/saved_views":
            self.handle_save_saved_views(data)
        elif path == "/api/resume":
            self.handle_save_resume(data)
        elif path == "/api/credentials":
            self.handle_save_credentials(data)
        elif path == "/api/onboarding":
            self.handle_onboarding(data)
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def handle_me(self):
        user = self.current_session_user()
        if not user:
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Sign in required"}).encode())
            return
        db = database.get_db()
        try:
            profile = db.execute("SELECT onboarding_state FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
            onboarding_state = profile["onboarding_state"] if profile else "complete"
        finally:
            db.close()
        self._set_headers(200)
        self.wfile.write(json.dumps({"success": True, "user": {"id": user["id"], "display_name": user["display_name"], "email": user["email"], "profile_key": user.get("profile_key"), "legacy_directory": user.get("legacy_directory"), "onboarding_state": onboarding_state}}).encode())

    def handle_onboarding(self, data):
        """Persist completion/dismissal for the signed-in account only."""
        user = self.current_session_user()
        if not user:
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Sign in required"}).encode())
            return
        state = data.get("state")
        if state not in ("complete", "dismissed"):
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid onboarding state"}).encode())
            return
        db = database.get_db()
        try:
            db.execute("UPDATE user_profiles SET onboarding_state=?, updated_at=? WHERE user_id=?", (state, datetime.now().isoformat(), user["id"]))
            db.commit()
        finally:
            db.close()
        self._set_headers(200)
        self.wfile.write(json.dumps({"success": True, "state": state}).encode())

    def handle_register(self, data):
        try:
            if data.get("password") != data.get("password_confirmation"):
                raise ValueError("Passwords do not match")
            user_id = auth_store.create_user(data.get("email"), data.get("password"), data.get("display_name"))
            token = auth_store.create_session(user_id)
            self._set_headers(201, cookie=f"jobsearch_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000")
            self.wfile.write(json.dumps({"success": True}).encode())
        except ValueError as e:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
        except Exception:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Could not create account"}).encode())

    def handle_claim_imported(self, data):
        try:
            if data.get("password") != data.get("password_confirmation"):
                raise ValueError("Passwords do not match")
            user_id = auth_store.claim_imported_account(data.get("legacy_directory"), data.get("claim_code"), data.get("email"), data.get("password"), data.get("display_name"))
            token = auth_store.create_session(user_id)
            self._set_headers(200, cookie=f"jobsearch_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000")
            self.wfile.write(json.dumps({"success": True}).encode())
        except (ValueError, Exception):
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Could not claim imported profile"}).encode())

    def handle_login(self, data):
        user = auth_store.authenticate(data.get("email", ""), data.get("password", ""))
        if not user:
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Invalid email or password"}).encode())
            return
        token = auth_store.create_session(user["id"])
        self._set_headers(200, cookie=f"jobsearch_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000")
        self.wfile.write(json.dumps({"success": True, "user": {"display_name": user["display_name"], "email": user["email"]}}).encode())

    def handle_logout(self):
        cookie = self.headers.get("Cookie", "")
        token = next((part.split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith("jobsearch_session=")), None)
        auth_store.revoke_session(token)
        self._set_headers(200, cookie="jobsearch_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
        self.wfile.write(json.dumps({"success": True}).encode())

    def handle_health_check(self):
        """Simple health check endpoint"""
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "status": "healthy",
            "service": "job_search_api",
            "timestamp": datetime.now().isoformat()
        }).encode())

    def handle_load_config(self, params):
        """Load current config.json for a user"""
        user = params.get("user", [None])[0]
        test_mode = params.get("test_mode", ["false"])[0].lower() == "true"

        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, user_dir = authorized
        try:
            config = profile_store.load_config(user, test_mode)
            if config is None: raise ValueError("Config not found")
            public_config = {key: value for key, value in config.items() if key != "credentials"}

            self._set_headers(200)
            self.wfile.write(json.dumps({
                "success": True,
                "config": public_config
            }).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_load_previous(self, params):
        """Load config.json.prev for a user"""
        user = params.get("user", [None])[0]
        test_mode = params.get("test_mode", ["false"])[0].lower() == "true"
        
        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, user_dir = authorized
        try:
            config = profile_store.load_config(user, test_mode, previous=True)
            if config is None:
                self._set_headers(404); self.wfile.write(json.dumps({"error": "No previous config found"}).encode()); return
            
            public_config = {key: value for key, value in config.items() if key != "credentials"}
            self._set_headers(200)
            self.wfile.write(json.dumps({
                "success": True,
                "config": public_config,
                "message": "Loaded previous configuration"
            }).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_serpapi_status(self, params):
        """Fetch SerpAPI account status (proxy to avoid CORS)"""
        if not self.authorized_user_dir(): return
        api_key = params.get("api_key", [None])[0]
        if not api_key:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Missing api_key parameter"}).encode())
            return

        try:
            import urllib.request
            url = f"https://serpapi.com/account.json?api_key={api_key}"
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode())
            
            # Extract relevant info
            remaining = data.get("total_searches_left", data.get("plan_searches_left", "N/A"))
            
            self._set_headers(200)
            self.wfile.write(json.dumps({
                "success": True,
                "searches_left": remaining,
                "this_month_usage": data.get("this_month_usage", 0),
                "plan_name": data.get("plan_name", "Unknown")
            }).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_load_triage(self, params):
        """Load the persisted Saved / Applied / Not Interested state for a user."""
        user = params.get("user", [None])[0]
        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, user_dir = authorized
        try:
            triage = load_triage(user_dir)
            jobs = triage.get("jobs", {})
            metadata = profile_store.latest_job_metadata(user, jobs.keys())
            changed = False
            recoverable_fields = ("title", "company", "url", "apply_url", "source", "location", "remote_status", "salary", "score", "posted", "posted_at")
            for job_id, record in jobs.items():
                source = metadata.get(job_id, {})
                for field in recoverable_fields:
                    if (record.get(field) is None or record.get(field) == "") and source.get(field) is not None and source.get(field) != "":
                        record[field] = source[field]
                        changed = True
                stage = record.get("tracker_stage") or record.get("status")
                if stage in {"applied", "interviewing", "offer", "closed"} and not record.get("applied_at"):
                    record["applied_at"] = str(record.get("updated_at") or datetime.now().date().isoformat())[:10]
                    changed = True
            if changed:
                save_triage_atomic(user_dir, triage)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "triage": triage}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_load_saved_views(self, params):
        user = params.get("user", [None])[0]
        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, user_dir = authorized
        self._set_headers(200)
        self.wfile.write(json.dumps({"success": True, "saved_views": load_saved_views(user_dir)}).encode())

    def handle_latest_results(self, params):
        authorized = self.authorized_user_dir(params.get("user", [None])[0])
        if not authorized: return
        user, _ = authorized
        self._set_headers(200)
        self.wfile.write(json.dumps({"success": True, "run": profile_store.latest_results(user)}).encode())

    def handle_search_history(self, params):
        authorized = self.authorized_user_dir(params.get("user", [None])[0])
        if not authorized: return
        user, _ = authorized
        self._set_headers(200)
        self.wfile.write(json.dumps({"success": True, "runs": profile_store.search_history(user)}).encode())

    def handle_load_resume(self, params):
        authorized = self.authorized_user_dir(params.get("user", [None])[0])
        if not authorized: return
        user, _ = authorized
        self._set_headers(200)
        self.wfile.write(json.dumps({"success": True, "text": profile_store.load_resume(user)}).encode())

    def handle_credentials(self, params):
        authorized = self.authorized_user_dir(params.get("user", [None])[0])
        if not authorized: return
        user, _ = authorized
        cfg = profile_store.load_config(user)
        def masked(value):
            return ("••••" + value[-4:]) if isinstance(value, str) and value else "Not set"
        creds = cfg.get("credentials", {})
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "success": True,
            "serpapi": [
                {"name": x.get("name", ""), "key_mask": masked(x.get("key"))}
                for x in creds.get("serpapi", []) if isinstance(x, dict)
            ],
            "adzuna": [
                {
                    "name": x.get("name", ""),
                    "app_id_mask": masked(x.get("app_id")),
                    "app_key_mask": masked(x.get("app_key")),
                }
                for x in creds.get("adzuna", []) if isinstance(x, dict)
            ],
        }).encode())

    def handle_save_credentials(self, data):
        authorized = self.authorized_user_dir(data.get("user"))
        if not authorized: return
        user, _ = authorized
        cfg = profile_store.load_config(user)
        incoming = data.get("credentials")
        if not isinstance(incoming, dict):
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid credentials"}).encode())
            return

        existing = cfg.get("credentials", {})

        def merge_entries(service, secret_fields):
            requested = incoming.get(service, [])
            if not isinstance(requested, list) or len(requested) > 20:
                raise ValueError(f"Invalid {service} credentials")
            old_by_name = {
                str(item.get("name", "")).strip().lower(): item
                for item in existing.get(service, [])
                if isinstance(item, dict) and item.get("name")
            }
            merged = []
            seen = set()
            for item in requested:
                if not isinstance(item, dict):
                    raise ValueError(f"Invalid {service} credential entry")
                name = str(item.get("name", "")).strip()
                name_key = name.lower()
                if not name or len(name) > 80 or name_key in seen:
                    raise ValueError(f"Each {service} credential needs a unique name")
                seen.add(name_key)
                prior = old_by_name.get(name_key, {})
                record = {"name": name}
                for field in secret_fields:
                    replacement = item.get(field, "")
                    if not isinstance(replacement, str) or len(replacement) > 500:
                        raise ValueError(f"Invalid {service} credential value")
                    if replacement.startswith("••••"):
                        raise ValueError("Masked values cannot be saved as credentials")
                    value = replacement.strip() or prior.get(field, "")
                    if value:
                        record[field] = value
                merged.append(record)
            if "active" in old_by_name and "active" not in seen:
                raise ValueError(f"The active {service} credential cannot be removed")
            return merged

        try:
            cfg["credentials"] = {
                "serpapi": merge_entries("serpapi", ("key",)),
                "adzuna": merge_entries("adzuna", ("app_id", "app_key")),
            }
            profile_store.save_config(user, cfg)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True}).encode())
        except ValueError as exc:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": str(exc)}).encode())

    def handle_save_saved_views(self, data):
        user = data.get("user")
        views = data.get("views")
        authorized = self.authorized_user_dir(user)
        if not authorized or not isinstance(views, list) or len(views) > 20:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid saved views"}).encode())
            return
        user, user_dir = authorized
        clean_views = []
        allowed_filter_keys = {"work_style", "minimum_score", "salary", "source", "application_state", "posted"}
        for view in views:
            if not isinstance(view, dict) or not isinstance(view.get("name"), str) or not view["name"].strip() or len(view["name"].strip()) > 80:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Each view needs a name up to 80 characters"}).encode())
                return
            filters = view.get("filters", {})
            if not isinstance(filters, dict) or any(key not in allowed_filter_keys for key in filters):
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Invalid saved-view filters"}).encode())
                return
            clean_views.append({"name": view["name"].strip(), "filters": filters})
        try:
            saved_views = {"version": 1, "views": clean_views}
            save_saved_views_atomic(user_dir, saved_views)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "saved_views": saved_views}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_save_resume(self, data):
        user = data.get("user")
        text = data.get("text")
        authorized = self.authorized_user_dir(user)
        if not authorized or not isinstance(text, str) or not text.strip() or len(text) > 200000:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Resume text must be between 1 and 200,000 characters"}).encode())
            return
        user, user_dir = authorized
        try:
            profile_store.save_resume(user, text)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "characters": len(text.strip())}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_save_triage(self, data):
        """Persist one job decision for a user, with minimal metadata for the future tracker."""
        user = data.get("user")
        job_id = data.get("job_id")
        status = data.get("status")
        authorized = self.authorized_user_dir(user)
        if not authorized or not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{16,64}", job_id):
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid user or job id"}).encode())
            return
        user, user_dir = authorized
        if status not in TRIAGE_STATUSES:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid triage status"}).encode())
            return

        try:
            triage = load_triage(user_dir)
            jobs = triage["jobs"]
            if status == "none":
                jobs.pop(job_id, None)
            else:
                old = jobs.get(job_id, {})
                record = {
                    "status": status,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                if status == "applied":
                    record["applied_at"] = str(old.get("applied_at") or record["updated_at"])[:10]

                metadata = data.get("job") if isinstance(data.get("job"), dict) else {}
                for field in ("title", "company", "url", "apply_url", "source", "location", "remote_status", "posted", "posted_at"):
                    value = metadata.get(field)
                    if isinstance(value, str) and value:
                        record[field] = value[:500]
                    elif field in old:
                        record[field] = old[field]
                for field in ("salary", "score"):
                    value = metadata.get(field)
                    if isinstance(value, (int, float)):
                        record[field] = value
                    elif field in old:
                        record[field] = old[field]
                jobs[job_id] = record

            save_triage_atomic(user_dir, triage)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "job_id": job_id, "status": status, "record": jobs.get(job_id)}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_save_tracker(self, data):
        """Update tracker fields on an existing saved/applied job record."""
        user = data.get("user")
        job_id = data.get("job_id")
        stage = data.get("stage")
        authorized = self.authorized_user_dir(user)
        if not authorized or not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{16,64}", job_id):
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid user or job id"}).encode())
            return
        user, user_dir = authorized
        if stage not in TRACKER_STAGES:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid tracker stage"}).encode())
            return

        notes = data.get("notes", "")
        follow_up_date = data.get("follow_up_date", "")
        applied_at = data.get("applied_at", "")
        if not isinstance(notes, str) or len(notes) > 5000:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Notes must be text up to 5000 characters"}).encode())
            return
        for value, label in ((follow_up_date, "follow-up date"), (applied_at, "applied date")):
            if not isinstance(value, str) or (value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": f"Invalid {label}"}).encode())
                return

        try:
            triage = load_triage(user_dir)
            record = triage["jobs"].get(job_id)
            if not isinstance(record, dict) or record.get("status") not in {"saved", "applied"}:
                self._set_headers(404)
                self.wfile.write(json.dumps({"error": "Save or apply this job before adding it to the tracker"}).encode())
                return

            now = datetime.now().isoformat(timespec="seconds")
            previous_stage = record.get("tracker_stage") or record.get("status")
            record["tracker_stage"] = stage
            record["status"] = "saved" if stage == "saved" else "applied"
            record["notes"] = notes
            record["follow_up_date"] = follow_up_date
            if stage != "saved":
                record["applied_at"] = applied_at or record.get("applied_at") or now[:10]
            elif applied_at:
                record["applied_at"] = applied_at
            if previous_stage != stage:
                history = record.get("stage_history")
                history = history if isinstance(history, list) else []
                history.append({"stage": stage, "at": now})
                record["stage_history"] = history[-100:]
            record["updated_at"] = now
            save_triage_atomic(user_dir, triage)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "job_id": job_id, "record": record}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_save(self, data):
        """Save configuration and rotate backup"""
        user = data.get("user")
        config = data.get("config")
        test_mode = data.get("test_mode", False)

        authorized = self.authorized_user_dir(user)
        if not authorized or not config:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Missing user or config"}).encode())
            return
        user, user_dir = authorized
        try:
            current = profile_store.load_config(user, test_mode) or {}
            if "credentials" not in config:
                config["credentials"] = current.get("credentials", {})
            profile_store.save_config(user, config, test_mode)

            self._set_headers(200)
            self.wfile.write(json.dumps({
                "success": True,
                "message": f"Configuration saved for {user}",
                "backup": "Previous config rotated to config.json.prev"
            }).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_run(self, data):
        """Execute job search for a user (non-blocking)"""
        user = data.get("user")
        test_mode = data.get("test_mode", False)

        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, user_dir = authorized

        try:
            resume_text = (profile_store.load_resume(user) or "").strip()
            if not resume_text or resume_text.lower().startswith("[replace this with the user's resume"):
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Add your resume in Search setup before running a search. It is required to calculate match scores."}).encode())
                return
            cfg = profile_store.load_config(user) or {}
            if not test_mode:
                sources = cfg.get("sources", {})
                enabled = [name for name, value in sources.items() if value]
                if not enabled:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Enable at least one live job source in Search setup before starting a live search. The built-in test does not need a source."}).encode())
                    return
                credentials = cfg.get("credentials", {})
                if sources.get("serpapi") and not any(isinstance(item, dict) and item.get("key") for item in credentials.get("serpapi", [])):
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "SerpAPI is enabled but has no API key. Add one under Search setup → Scraper credentials, or disable SerpAPI."}).encode())
                    return
                if sources.get("adzuna") and not any(isinstance(item, dict) and item.get("app_id") and item.get("app_key") for item in credentials.get("adzuna", [])):
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Adzuna is enabled but needs both an App ID and App Key. Add them under Search setup → Scraper credentials, or disable Adzuna."}).encode())
                    return
            # Legacy JSON is only a compatibility mirror; database-only
            # profiles have no directory and use SQLite status exclusively.
            status_file = os.path.join(CONFIG_DIR, user, "job_status_test.json" if test_mode else "job_status.json")
            if os.path.isdir(os.path.dirname(status_file)):
                with open(status_file, "w") as f:
                    json.dump({"state": "running", "message": "Job started", "messages": [{"text": "Job started", "timestamp": str(datetime.now())}], "start_time": str(datetime.now())}, f)

            # Build command
            script_path = os.path.join(BASE_DIR, "run_job_search.sh")
            cmd = [script_path, user]
            if test_mode:
                cmd.append("--test-mode")

            # Execute in background (truly non-blocking)
            # Use DEVNULL — run_job_search.sh writes all output to log files;
            # the pipe back to api_handler was never read, causing a 64KB buffer
            # deadlock that hung job searches after ~200 jobs of output.
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=BASE_DIR,
                start_new_session=True  # Detach from parent
            )

            if os.path.exists(status_file):
                with open(status_file, "r") as f: status_data = json.load(f)
                status_data["pid"] = process.pid
                with open(status_file, "w") as f: json.dump(status_data, f)
            profile_store.write_run_status(user, test_mode, "running", "Job started", process.pid)

            # Return immediately with job started confirmation
            self._set_headers(200)
            self.wfile.write(json.dumps({
                "success": True,
                "message": f"Job search started for {user}",
                "job_started": True,
                "pid": process.pid
            }).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_job_status(self, data):
        """Get job status from status file"""
        user = data.get("user")
        test_mode = data.get("test_mode", False)
        
        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, config_dir = authorized
        
        try:
            status_file = os.path.join(config_dir, "job_status_test.json" if test_mode else "job_status.json")
            
            status_data = profile_store.latest_run_status(user, test_mode)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "status": status_data}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def handle_cancel(self, data):
        """Cancel a running job search by killing its process group"""
        user = data.get("user")
        test_mode = data.get("test_mode", False)

        authorized = self.authorized_user_dir(user)
        if not authorized: return
        user, config_dir = authorized

        try:
            status_file = os.path.join(config_dir, "job_status_test.json" if test_mode else "job_status.json")

            status_data = profile_store.latest_run_status(user, test_mode)

            pid = status_data.get("pid")
            killed = False
            kill_error = None

            if pid:
                try:
                    # Kill the entire process group (shell + python3 child)
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    killed = True
                except ProcessLookupError:
                    kill_error = "Process already exited"
                except Exception as e:
                    kill_error = str(e)

            # Write cancelled state regardless of whether kill succeeded
            with open(status_file, "w") as f:
                json.dump({
                    "state": "cancelled",
                    "message": "Job cancelled by user",
                    "messages": status_data.get("messages", []) + [
                        {"text": "Job cancelled by user", "timestamp": str(datetime.now())}
                    ]
                }, f)
            profile_store.write_run_status(user, test_mode, "cancelled", "Job cancelled by user")

            self._set_headers(200)
            self.wfile.write(json.dumps({
                "success": True,
                "killed": killed,
                "kill_error": kill_error
            }).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        """Override to customize logging"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sys.stderr.write(f"[{timestamp}] {format % args}\n")


def run_server(port=8765):
    """Start the API server"""
    server_address = ("", port)
    httpd = HTTPServer(server_address, APIHandler)
    
    # Allow socket reuse to prevent "Address already in use" errors during restarts
    httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    print(f"API server running on port {port}...")
    print(f"Endpoints:")
    print(f"  GET  http://localhost:{port}/api/load_previous?user=<name>")
    print(f"  POST http://localhost:{port}/api/save")
    print(f"  POST http://localhost:{port}/api/run")
    httpd.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server(port)
