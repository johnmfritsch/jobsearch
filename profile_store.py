"""SQLite canonical storage for imported JobSearch profiles (DEV)."""
import hashlib
import json
import re
from datetime import datetime
from database import get_db

def _stable_job_key(value):
    """Return the tracker-safe stable ID used for every result shape."""
    text = str(value or "").strip()
    if re.fullmatch(r"[a-fA-F0-9]{16,64}", text):
        return text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _profile(directory):
    db = get_db()
    row = db.execute("SELECT u.id, p.* FROM users u JOIN user_profiles p ON p.user_id=u.id WHERE u.profile_key=?", (directory,)).fetchone()
    if not row:
        db.close(); raise ValueError("Profile not found")
    return db, row

def load_config(directory, test_mode=False, previous=False):
    db, row = _profile(directory)
    try:
        column = "test_config_prev_json" if test_mode and previous else "config_prev_json" if previous else "test_config_json" if test_mode else "config_json"
        value = row[column]
        return json.loads(value) if value else None
    finally: db.close()

def save_config(directory, config, test_mode=False):
    db, row = _profile(directory)
    try:
        current = "test_config_json" if test_mode else "config_json"
        previous = "test_config_prev_json" if test_mode else "config_prev_json"
        db.execute(f"UPDATE user_profiles SET {previous}={current}, {current}=?, updated_at=? WHERE user_id=?", (json.dumps(config, ensure_ascii=False), datetime.utcnow().isoformat(), row["id"]))
        db.commit()
    finally: db.close()

def load_resume(directory):
    db, row = _profile(directory)
    try: return row["resume_text"]
    finally: db.close()

def save_resume(directory, text):
    db, row = _profile(directory)
    try:
        db.execute("UPDATE user_profiles SET resume_text=?, updated_at=? WHERE user_id=?", (text.strip() + "\n", datetime.utcnow().isoformat(), row["id"]))
        db.commit()
    finally: db.close()

def load_triage(directory):
    db, row = _profile(directory)
    try:
        jobs = {r["job_key"]: json.loads(r["record_json"]) for r in db.execute("SELECT job_key, record_json FROM application_records WHERE user_id=?", (row["id"],))}
        return {"version": 1, "jobs": jobs}
    finally: db.close()

def save_triage(directory, triage):
    db, row = _profile(directory)
    try:
        db.execute("DELETE FROM application_records WHERE user_id=?", (row["id"],))
        for key, record in triage.get("jobs", {}).items():
            db.execute("INSERT INTO application_records (user_id,job_key,record_json,updated_at) VALUES (?,?,?,?)", (row["id"], key, json.dumps(record, ensure_ascii=False), datetime.utcnow().isoformat()))
        db.commit()
    finally: db.close()

def latest_job_metadata(directory, job_keys):
    """Return the newest stored search-result payload for each requested job."""
    wanted = {str(key) for key in job_keys if key}
    if not wanted:
        return {}
    db, row = _profile(directory)
    try:
        found = {}
        for result in db.execute(
            "SELECT jr.job_key, jr.job_json FROM job_results jr "
            "JOIN search_runs sr ON sr.id=jr.run_id "
            "WHERE sr.user_id=? ORDER BY sr.id DESC, jr.id DESC",
            (row["id"],),
        ):
            key = str(result["job_key"])
            if key in wanted and key not in found:
                found[key] = json.loads(result["job_json"])
                if len(found) == len(wanted):
                    break
        return found
    finally:
        db.close()

def load_saved_views(directory):
    db, row = _profile(directory)
    try:
        return {"version": 1, "views": [{"name": r["name"], "filters": json.loads(r["filters_json"])} for r in db.execute("SELECT name, filters_json FROM saved_views WHERE user_id=? ORDER BY name", (row["id"],))]}
    finally: db.close()

def save_saved_views(directory, views):
    db, row = _profile(directory)
    try:
        db.execute("DELETE FROM saved_views WHERE user_id=?", (row["id"],))
        for view in views.get("views", []): db.execute("INSERT INTO saved_views (user_id,name,filters_json) VALUES (?,?,?)", (row["id"], view["name"], json.dumps(view["filters"], ensure_ascii=False)))
        db.commit()
    finally: db.close()

def write_run_status(directory, test_mode, state, message, pid=None):
    db, row = _profile(directory)
    try:
        latest = db.execute("SELECT id,status_json FROM search_runs WHERE user_id=? AND test_mode=? ORDER BY id DESC LIMIT 1", (row["id"], int(test_mode))).fetchone()
        old = json.loads(latest["status_json"]) if latest else {}
        messages = old.get("messages", [])[-499:]
        now = datetime.utcnow().isoformat()
        messages.append({"text": message, "timestamp": now})
        status = {"state": state, "message": message, "messages": messages, "timestamp": now}
        if pid is not None: status["pid"] = pid
        elif old.get("pid"): status["pid"] = old["pid"]
        if latest and old.get("state") == "running":
            db.execute("UPDATE search_runs SET state=?,status_json=?,completed_at=? WHERE id=?", (state, json.dumps(status), now if state in {"complete","error","cancelled"} else None, latest["id"])); run_id=latest["id"]
        else:
            db.execute("INSERT INTO search_runs (user_id,test_mode,state,status_json) VALUES (?,?,?,?)", (row["id"], int(test_mode), state, json.dumps(status))); run_id=db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit(); return status, run_id
    finally: db.close()

def latest_run_status(directory, test_mode):
    db, row = _profile(directory)
    try:
        r=db.execute("SELECT status_json FROM search_runs WHERE user_id=? AND test_mode=? ORDER BY id DESC LIMIT 1", (row["id"], int(test_mode))).fetchone()
        return json.loads(r["status_json"]) if r else {"state":"not_started"}
    finally: db.close()

def save_run_results(directory, test_mode, jobs):
    status, run_id = write_run_status(directory, test_mode, "running", "Publishing result snapshot")
    db=get_db()
    try:
        db.execute("DELETE FROM job_results WHERE run_id=?", (run_id,))
        for job in jobs:
            key = _stable_job_key(job.get("id") or job.get("url") or job.get("title"))
            if key: db.execute("INSERT OR REPLACE INTO job_results (run_id,job_key,job_json,score) VALUES (?,?,?,?)", (run_id, str(key), json.dumps(job, ensure_ascii=False), job.get("score")))
        db.commit()
    finally: db.close()

def latest_results(directory, limit=500):
    db, row = _profile(directory)
    try:
        run = db.execute("SELECT id,test_mode,state,status_json,started_at,completed_at FROM search_runs WHERE user_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
        if not run: return None
        jobs = [dict(json.loads(r["job_json"]), job_key=_stable_job_key(r["job_key"])) for r in db.execute("SELECT job_key,job_json FROM job_results WHERE run_id=? ORDER BY score DESC LIMIT ?", (run["id"], limit))]
        status=json.loads(run["status_json"])
        return {"id":run["id"],"test_mode":bool(run["test_mode"]),"state":run["state"],"started_at":run["started_at"],"completed_at":run["completed_at"],"message":status.get("message",""),"result_count":db.execute("SELECT count(*) FROM job_results WHERE run_id=?", (run["id"],)).fetchone()[0],"jobs":jobs}
    finally: db.close()

def search_history(directory, limit=10):
    db, row = _profile(directory)
    try:
        return [{"id":r["id"],"test_mode":bool(r["test_mode"]),"state":r["state"],"started_at":r["started_at"],"completed_at":r["completed_at"],"result_count":r["result_count"]} for r in db.execute("SELECT r.id,r.test_mode,r.state,r.started_at,r.completed_at,count(j.id) result_count FROM search_runs r LEFT JOIN job_results j ON j.run_id=r.id WHERE r.user_id=? GROUP BY r.id ORDER BY r.id DESC LIMIT ?", (row["id"],limit))]
    finally: db.close()
