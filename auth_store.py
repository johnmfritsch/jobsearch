"""SQLite-backed account primitives for the JobSearch DEV migration."""
import hashlib
import hmac
import os
import secrets
import re
import json
from pathlib import Path
from datetime import datetime, timedelta

from database import get_db

ITERATIONS = 310_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"

def verify_password(password, encoded):
    try:
        _, iterations, salt, digest = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False

def create_user(email, password, display_name):
    if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()) or not isinstance(password, str) or not isinstance(display_name, str) or len(password) < 8 or not display_name.strip() or len(display_name.strip()) > 80:
        raise ValueError("Use an email, display name, and a password of at least 8 characters")
    db = get_db()
    try:
        cur = db.execute("INSERT INTO users (email,password_hash,display_name) VALUES (?,?,?)", (email.strip().lower(), hash_password(password), display_name.strip()))
        user_id = cur.lastrowid
        profile_key = f"account-{user_id}"
        base = Path(__file__).parent
        config = json.loads((base / "default_config.json").read_text(encoding="utf-8"))
        # A new user can run the built-in test without external accounts.
        # Live providers remain off until onboarding explains them and the
        # user supplies (or deliberately chooses) the appropriate access.
        config["sources"] = {"adzuna": False, "serpapi": False, "indeed": False}
        # A new account has no resume until the user supplies one during
        # onboarding.  Do not store the instructional legacy placeholder as
        # though it were real resume content.
        resume = ""
        db.execute("UPDATE users SET profile_key=? WHERE id=?", (profile_key, user_id))
        db.execute("INSERT INTO user_profiles (user_id,config_json,test_config_json,resume_text,onboarding_state) VALUES (?,?,?,?,?)", (user_id, json.dumps(config), json.dumps(config), resume, "pending"))
        db.commit()
        return user_id
    finally: db.close()

def issue_import_claim_code(legacy_directory, lifetime_hours=72):
    """Issue a one-time high-entropy code for a locally imported DEV profile."""
    if not isinstance(legacy_directory, str) or not DIRECTORY_RE.fullmatch(legacy_directory):
        raise ValueError("Invalid imported profile")
    code = secrets.token_urlsafe(24)
    db = get_db()
    try:
        row = db.execute("SELECT id FROM users WHERE legacy_directory=?", (legacy_directory,)).fetchone()
        if not row:
            raise ValueError("Imported profile not found")
        expiry = (datetime.utcnow() + timedelta(hours=lifetime_hours)).isoformat()
        db.execute("INSERT OR REPLACE INTO imported_profile_claims (legacy_directory,code_hash,expires_at) VALUES (?,?,?)", (legacy_directory, hashlib.sha256(code.encode()).hexdigest(), expiry))
        db.commit()
        return code
    finally: db.close()

def claim_imported_account(legacy_directory, claim_code, email, password, display_name):
    """Turn a migration placeholder into a real account only with its claim code."""
    if not isinstance(legacy_directory, str) or not DIRECTORY_RE.fullmatch(legacy_directory):
        raise ValueError("Invalid imported profile")
    if not isinstance(claim_code, str) or not claim_code:
        raise ValueError("A claim code is required")
    if not isinstance(email, str) or not EMAIL_RE.fullmatch(email.strip()) or not isinstance(password, str) or len(password) < 8 or not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 80:
        raise ValueError("Use an email, display name, and a password of at least 8 characters")
    db = get_db()
    try:
        claim = db.execute("SELECT code_hash FROM imported_profile_claims WHERE legacy_directory=? AND expires_at > ?", (legacy_directory, datetime.utcnow().isoformat())).fetchone()
        user = db.execute("SELECT id FROM users WHERE legacy_directory=?", (legacy_directory,)).fetchone()
        if not claim or not user or not hmac.compare_digest(claim["code_hash"], hashlib.sha256(claim_code.encode()).hexdigest()):
            raise ValueError("Invalid or expired claim code")
        db.execute("UPDATE users SET email=?, password_hash=?, display_name=? WHERE id=?", (email.strip().lower(), hash_password(password), display_name.strip(), user["id"]))
        db.execute("DELETE FROM imported_profile_claims WHERE legacy_directory=?", (legacy_directory,))
        db.commit()
        db.execute("UPDATE users SET profile_key=legacy_directory WHERE id=? AND profile_key IS NULL", (user["id"],))
        db.commit()
        return user["id"]
    finally: db.close()

def authenticate(email, password):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM users WHERE email=? AND is_active=1", (email.strip().lower(),)).fetchone()
        return dict(row) if row and verify_password(password, row["password_hash"]) else None
    finally: db.close()

def create_session(user_id):
    token = secrets.token_urlsafe(32)
    db = get_db()
    try:
        db.execute("INSERT INTO sessions (token_hash,user_id,expires_at) VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, (datetime.utcnow()+timedelta(days=30)).isoformat()))
        db.commit()
    finally: db.close()
    return token

def session_user(token):
    if not token: return None
    db = get_db()
    try:
        row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at > ? AND u.is_active=1", (hashlib.sha256(token.encode()).hexdigest(), datetime.utcnow().isoformat())).fetchone()
        return dict(row) if row else None
    finally: db.close()

def revoke_session(token):
    if not token: return
    db = get_db()
    try:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        db.commit()
    finally: db.close()
