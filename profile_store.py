"""SQLite canonical storage for imported JobSearch profiles (DEV)."""
import hashlib
import json
import re
import colorsys
import sqlite3
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from database import get_db

ROLE_COLORS = (
    "#2563EB", "#7C3AED", "#0F766E", "#B45309", "#BE123C",
    "#0369A1", "#4D7C0F", "#A21CAF", "#C2410C", "#4338CA",
    "#047857", "#9F1239",
)
DEFAULT_ROLE_COLOR = "#64748B"


def _next_role_color(db, user_id):
    used = {str(row[0]).upper() for row in db.execute(
        "SELECT color FROM search_presets WHERE user_id=? AND color IS NOT NULL",
        (user_id,)) if row[0]}
    for color in ROLE_COLORS:
        if color.upper() not in used:
            return color
    seed = len(used)
    for attempt in range(360):
        hue = ((seed * 137.508) + (attempt * 29)) % 360 / 360
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.68)
        color = f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"
        if color not in used:
            return color
    return DEFAULT_ROLE_COLOR


def _role_metadata(db, user_id, config):
    config = config or {}
    name = str(config.get("active_search_preset_name") or "Default")
    color = config.get("active_search_preset_color")
    if not color and name.casefold() != "default":
        preset = db.execute(
            "SELECT color FROM search_presets WHERE user_id=? AND name=? COLLATE NOCASE",
            (user_id, name)).fetchone()
        color = preset["color"] if preset and preset["color"] else None
    return name, str(color or DEFAULT_ROLE_COLOR)

def _stable_job_key(value):
    """Return the tracker-safe stable ID used for every result shape."""
    text = str(value or "").strip()
    if re.fullmatch(r"[a-fA-F0-9]{16,64}", text):
        return text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_job_key(value):
    """Return the public stable identifier shared by results and ignore rules."""
    return _stable_job_key(value)

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


def normalize_employer_name(value):
    """Stable punctuation/space-insensitive comparison key for an employer.

    "&" and "+" are treated as the word "and" first so that "Landis+Gyr",
    "Landis & Gyr", and "Landis and Gyr" all normalize to the same key --
    job boards and press vary that spelling for the same employer.
    """
    text = re.sub(r'[&+]', ' and ', str(value or '').casefold())
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def normalize_employer_url(value):
    """Validate and normalize a public HTTPS careers URL without fetching it."""
    text = str(value or '').strip()
    if not text:
        return '', ''
    parsed = urlparse(text)
    if (parsed.scheme.lower() != 'https' or not parsed.hostname
            or parsed.username or parsed.password):
        raise ValueError('Careers URL must be a public https address without credentials')
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError('Careers URL has an invalid port') from error
    if port not in (None, 443):
        raise ValueError('Careers URL must use port 443')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.local', '.internal', '.lan', '.home')):
        raise ValueError('Careers URL must use a public web host')
    path = parsed.path or '/'
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    normalized = urlunparse(('https', host, path, '', query, ''))
    return normalized, normalized.casefold()


def _employer_adapter(careers_url):
    # scrapers/adapter_registry.py is the single source of truth for URL ->
    # adapter classification, shared with the fetch/test dispatch -- see its
    # module docstring for why (Round 10's test/fetch dispatch drift).
    from scrapers.adapter_registry import classify_url
    return classify_url(careers_url)


def _clean_employer_aliases(values):
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError('Employer aliases must be a list')
    aliases, keys = [], set()
    for value in values:
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        key = normalize_employer_name(text)
        if not key:
            continue
        if len(text) > 120:
            raise ValueError('Employer aliases are limited to 120 characters')
        if key not in keys:
            aliases.append(text)
            keys.add(key)
    if len(aliases) > 20:
        raise ValueError('Use at most 20 employer aliases')
    return aliases


def _source_row(row, selected_ids=()):
    item = dict(row)
    try:
        item['company_aliases'] = json.loads(item.pop('company_aliases_json') or '[]')
    except (TypeError, ValueError):
        item['company_aliases'] = []
    item['enabled'] = bool(item.get('enabled'))
    item['enabled_all_roles'] = bool(item.get('enabled_all_roles'))
    item['enabled_for_role'] = (item['enabled_all_roles']
                                or item.get('id') in set(selected_ids))
    return item


def _member_source_ids(db, user_id, preset_id):
    """Sources with an explicit membership row for this role (0 = unsaved Default)."""
    rows = db.execute(
        'SELECT esr.source_id FROM employer_source_roles esr '
        'JOIN employer_sources es ON es.id=esr.source_id '
        'WHERE es.user_id=? AND esr.preset_id=?', (user_id, int(preset_id))).fetchall()
    return {row['source_id'] for row in rows}


def list_employer_sources(directory, preset_id=None):
    db, profile = _profile(directory)
    try:
        if preset_id is None:
            config = json.loads(profile['config_json'] or '{}')
            preset_id = int(config.get('active_search_preset_id') or 0)
        selected = _member_source_ids(db, profile['id'], preset_id)
        rows = db.execute(
            'SELECT * FROM employer_sources WHERE user_id=? ORDER BY company_name COLLATE NOCASE, id',
            (profile['id'],)).fetchall()
        return [_source_row(row, selected) for row in rows]
    finally:
        db.close()


def active_employer_sources(directory, config):
    preset_id = int((config or {}).get('active_search_preset_id') or 0)
    db, profile = _profile(directory)
    try:
        selected = _member_source_ids(db, profile['id'], preset_id)
        rows = db.execute(
            'SELECT * FROM employer_sources WHERE user_id=? AND enabled=1 '
            'ORDER BY company_name COLLATE NOCASE, id', (profile['id'],)).fetchall()
        return [_source_row(row, selected) for row in rows
                if bool(row['enabled_all_roles']) or row['id'] in selected]
    finally:
        db.close()


def employer_source_roles(directory, source_id):
    """List every saved role, each flagged with whether this source currently
    applies to it -- independent of whichever role happens to be active in
    the session right now -- plus the source's own enabled_all_roles flag.
    There is no separate "Default" pseudo-role: applying everywhere is
    enabled_all_roles, a real column on the source, not role membership."""
    db, profile = _profile(directory)
    try:
        source_id = int(source_id)
        source = db.execute('SELECT enabled_all_roles FROM employer_sources WHERE id=? AND user_id=?',
                            (source_id, profile['id'])).fetchone()
        if not source:
            raise LookupError('Priority employer not found')
        member_ids = {row['preset_id'] for row in db.execute(
            'SELECT preset_id FROM employer_source_roles WHERE source_id=?', (source_id,)).fetchall()}
        presets = db.execute(
            'SELECT id,name,color FROM search_presets WHERE user_id=? ORDER BY name COLLATE NOCASE',
            (profile['id'],)).fetchall()
        return {
            'enabled_all_roles': bool(source['enabled_all_roles']),
            'roles': [{'id': preset['id'], 'name': preset['name'], 'color': preset['color'],
                      'enabled': preset['id'] in member_ids} for preset in presets],
        }
    finally:
        db.close()


def set_employer_source_roles(directory, source_id, preset_ids):
    """Replace which saved roles a priority employer applies to. Unlike the
    old config-blob approach, this doesn't require the target role to be the
    one currently active in the session."""
    db, profile = _profile(directory)
    try:
        source_id = int(source_id)
        if not db.execute('SELECT id FROM employer_sources WHERE id=? AND user_id=?',
                          (source_id, profile['id'])).fetchone():
            raise LookupError('Priority employer not found')
        valid = {0} | {row['id'] for row in db.execute(
            'SELECT id FROM search_presets WHERE user_id=?', (profile['id'],)).fetchall()}
        wanted = {int(value) for value in (preset_ids or []) if str(value).isdigit()} & valid
        now = datetime.utcnow().isoformat()
        db.execute('DELETE FROM employer_source_roles WHERE source_id=?', (source_id,))
        db.executemany(
            'INSERT INTO employer_source_roles(source_id,preset_id,created_at) VALUES (?,?,?)',
            [(source_id, preset_id, now) for preset_id in wanted])
        db.commit()
        return sorted(wanted)
    finally:
        db.close()


def save_employer_source(directory, source):
    if not isinstance(source, dict):
        raise ValueError('Invalid priority employer')
    company_name = re.sub(r'\s+', ' ', str(source.get('company_name') or '')).strip()
    company_key = normalize_employer_name(company_name)
    if not company_key or len(company_name) > 120:
        raise ValueError('Employer name must be 1–120 characters')
    aliases = _clean_employer_aliases(source.get('company_aliases', []))
    careers_url, careers_url_key = normalize_employer_url(source.get('careers_url'))
    # The API may supply a server-produced, positive bounded probe result.
    # Never honor a browser-supplied adapter; absent that result retain the
    # established network-free classification for non-API callers/tests.
    adapter = source.get('_server_adapter') if careers_url else 'serpapi_company'
    if not isinstance(adapter, str) or not adapter:
        adapter = _employer_adapter(careers_url)
    enabled = bool(source.get('enabled', True))
    enabled_all_roles = bool(source.get('enabled_all_roles', False))
    try:
        source_id = int(source.get('id')) if source.get('id') is not None else None
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid priority employer') from error
    db, profile = _profile(directory)
    try:
        now = datetime.utcnow().isoformat()
        if source_id is None:
            cursor = db.execute(
                'INSERT INTO employer_sources '
                '(user_id,company_name,company_key,company_aliases_json,careers_url,careers_url_key,'
                'adapter,enabled_all_roles,enabled,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (profile['id'], company_name, company_key, json.dumps(aliases, ensure_ascii=False),
                 careers_url or None, careers_url_key, adapter, int(enabled_all_roles),
                 int(enabled), now, now))
            source_id = cursor.lastrowid
        else:
            owned = db.execute('SELECT id FROM employer_sources WHERE id=? AND user_id=?',
                               (source_id, profile['id'])).fetchone()
            if not owned:
                raise LookupError('Priority employer not found')
            db.execute(
                'UPDATE employer_sources SET company_name=?,company_key=?,company_aliases_json=?, '
                'careers_url=?,careers_url_key=?,adapter=?,enabled_all_roles=?,enabled=?,updated_at=? '
                'WHERE id=? AND user_id=?',
                (company_name, company_key, json.dumps(aliases, ensure_ascii=False),
                 careers_url or None, careers_url_key, adapter, int(enabled_all_roles),
                 int(enabled), now, source_id, profile['id']))
        db.commit()
        row = db.execute('SELECT * FROM employer_sources WHERE id=? AND user_id=?',
                         (source_id, profile['id'])).fetchone()
        return _source_row(row)
    except sqlite3.IntegrityError as error:
        raise ValueError('That priority employer is already saved') from error
    finally:
        db.close()


def delete_employer_source(directory, source_id):
    db, profile = _profile(directory)
    try:
        source_id = int(source_id)
        # employer_source_roles rows cascade on delete (PRAGMA foreign_keys=ON
        # per connection, see database.py) -- no manual scrub needed here.
        cursor = db.execute('DELETE FROM employer_sources WHERE id=? AND user_id=?',
                            (source_id, profile['id']))
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def reclassify_employer_source(directory, source_id, adapter):
    """Persist only an API-validated adapter after a bounded re-test."""
    from scrapers.adapter_registry import ADAPTER_NAMES
    if adapter not in ADAPTER_NAMES:
        raise ValueError('Invalid priority-employer adapter')
    db, profile = _profile(directory)
    try:
        cursor = db.execute('UPDATE employer_sources SET adapter=?,updated_at=? WHERE id=? AND user_id=?',
                            (adapter, datetime.utcnow().isoformat(), int(source_id), profile['id']))
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def record_employer_source_status(directory, source_id, success, error=''):
    """Best-effort operational status; the durable search-run audit is primary."""
    try:
        db, profile = _profile(directory)
        try:
            now = datetime.utcnow().isoformat()
            if success:
                db.execute('UPDATE employer_sources SET last_success_at=?,last_error=?,last_checked_at=?,updated_at=? '
                           'WHERE id=? AND user_id=?',
                           (now, '', now, now, int(source_id), profile['id']))
            else:
                db.execute('UPDATE employer_sources SET last_error=?,last_checked_at=?,updated_at=? '
                           'WHERE id=? AND user_id=?',
                           (str(error or '')[:500], now, now, int(source_id), profile['id']))
            db.commit()
        finally:
            db.close()
    except (sqlite3.Error, ValueError):
        return False
    return True

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

def list_search_presets(directory):
    db, row = _profile(directory)
    try:
        return [dict(item) for item in db.execute(
            "SELECT id,name,color,created_at,updated_at FROM search_presets "
            "WHERE user_id=? ORDER BY name COLLATE NOCASE", (row["id"],))]
    finally: db.close()

def save_search_preset(directory, name, config, resume_text):
    """Upsert a credential-free preset and make it the active search setup."""
    db, profile = _profile(directory)
    try:
        now = datetime.utcnow().isoformat()
        preset_config = _audit_config(config)
        for field in ("active_search_preset_name", "active_search_preset_id",
                      "active_search_preset_color"):
            preset_config.pop(field, None)
        current_config = json.loads(profile["config_json"] or "{}")
        active_config = json.loads(json.dumps(preset_config))
        active_config["credentials"] = current_config.get("credentials", {})
        clean_resume = resume_text.strip() + "\n"
        existing = db.execute(
            "SELECT id,created_at,color FROM search_presets WHERE user_id=? AND name=? COLLATE NOCASE",
            (profile["id"], name)).fetchone()
        if existing:
            preset_id = existing["id"]
            color = existing["color"] or _next_role_color(db, profile["id"])
            db.execute(
                "UPDATE search_presets SET name=?,color=?,config_json=?,resume_text=?,updated_at=? "
                "WHERE id=? AND user_id=?",
                (name, color, json.dumps(preset_config, ensure_ascii=False), clean_resume,
                 now, preset_id, profile["id"]))
        else:
            color = _next_role_color(db, profile["id"])
            cursor = db.execute(
                "INSERT INTO search_presets "
                "(user_id,name,color,config_json,resume_text,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (profile["id"], name, color, json.dumps(preset_config, ensure_ascii=False),
                 clean_resume, now, now))
            preset_id = cursor.lastrowid
        active_config["active_search_preset_name"] = name
        active_config["active_search_preset_id"] = preset_id
        active_config["active_search_preset_color"] = color
        db.execute(
            "UPDATE user_profiles SET config_prev_json=config_json,config_json=?,"
            "resume_text=?,updated_at=? WHERE user_id=?",
            (json.dumps(active_config, ensure_ascii=False), clean_resume, now,
             profile["id"]))
        db.commit()
        return {"id": preset_id, "name": name, "color": color,
                "updated_at": now}
    finally: db.close()

def activate_search_preset(directory, preset_id):
    """Make a saved preset active while retaining account-level credentials."""
    db, profile = _profile(directory)
    try:
        preset = db.execute(
            "SELECT id,name,color,config_json,resume_text,updated_at FROM search_presets "
            "WHERE id=? AND user_id=?", (preset_id, profile["id"])).fetchone()
        if not preset:
            return None
        current_config = json.loads(profile["config_json"] or "{}")
        config = json.loads(preset["config_json"])
        active_config = json.loads(json.dumps(config))
        active_config["credentials"] = current_config.get("credentials", {})
        active_config["active_search_preset_name"] = preset["name"]
        active_config["active_search_preset_id"] = preset["id"]
        active_config["active_search_preset_color"] = preset["color"] or DEFAULT_ROLE_COLOR
        now = datetime.utcnow().isoformat()
        db.execute(
            "UPDATE user_profiles SET config_prev_json=config_json,config_json=?,"
            "resume_text=?,updated_at=? WHERE user_id=?",
            (json.dumps(active_config, ensure_ascii=False), preset["resume_text"],
             now, profile["id"]))
        db.commit()
        return {
            "id": preset["id"], "name": preset["name"],
            "color": preset["color"] or DEFAULT_ROLE_COLOR,
            "config": {key: value for key, value in active_config.items()
                       if key != "credentials"},
            "resume": preset["resume_text"], "updated_at": preset["updated_at"]
        }
    finally: db.close()

def delete_search_preset(directory, preset_id):
    db, profile = _profile(directory)
    try:
        preset = db.execute(
            "SELECT name FROM search_presets WHERE id=? AND user_id=?",
            (preset_id, profile["id"])).fetchone()
        if not preset:
            return False
        cursor = db.execute(
            "DELETE FROM search_presets WHERE id=? AND user_id=?",
            (preset_id, profile["id"]))
        # No FK from employer_source_roles.preset_id to search_presets (preset_id=0
        # means the unsaved Default role, which can't be a real FK target), so this
        # cleanup has to be explicit rather than relying on ON DELETE CASCADE.
        db.execute("DELETE FROM employer_source_roles WHERE preset_id=?", (preset_id,))
        config = json.loads(profile["config_json"] or "{}")
        active_name = str(config.get("active_search_preset_name", "Default"))
        if active_name.casefold() == str(preset["name"]).casefold():
            config["active_search_preset_name"] = "Default"
            config.pop("active_search_preset_id", None)
            config.pop("active_search_preset_color", None)
            db.execute(
                "UPDATE user_profiles SET config_json=?,updated_at=? WHERE user_id=?",
                (json.dumps(config, ensure_ascii=False), datetime.utcnow().isoformat(),
                 profile["id"]))
        db.commit()
        return cursor.rowcount > 0
    finally: db.close()

def load_triage(directory):
    db, row = _profile(directory)
    try:
        jobs = {r["job_key"]: json.loads(r["record_json"]) for r in db.execute("SELECT job_key, record_json FROM application_records WHERE user_id=?", (row["id"],))}
        for job_key, record in jobs.items():
            if record.get("search_role") and record.get("search_role_color"):
                continue
            source = db.execute(
                "SELECT jr.job_json,a.config_json FROM job_results jr "
                "JOIN search_runs r ON r.id=jr.run_id "
                "LEFT JOIN search_run_audits a ON a.run_id=r.id "
                "WHERE r.user_id=? AND jr.job_key=? ORDER BY r.id DESC LIMIT 1",
                (row["id"], job_key)).fetchone()
            public_job = json.loads(source["job_json"]) if source else {}
            run_config = (json.loads(source["config_json"])
                          if source and source["config_json"] else {})
            role_name = public_job.get("search_role")
            role_color = public_job.get("search_role_color")
            if not role_name:
                if run_config.get("active_search_preset_name"):
                    role_name, inferred_color = _role_metadata(
                        db, row["id"], run_config)
                    role_color = role_color or inferred_color
                else:
                    role_name = "Before roles"
            record.setdefault("search_role", role_name or "Before roles")
            record.setdefault("search_role_color", role_color or DEFAULT_ROLE_COLOR)
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


def load_record(directory, job_key):
    """Return one tracker record, or None. Used where rewriting every row would
    be wrong -- see upsert_record."""
    db, row = _profile(directory)
    try:
        item = db.execute(
            "SELECT record_json FROM application_records WHERE user_id=? AND job_key=?",
            (row["id"], str(job_key))).fetchone()
        if not item:
            return None
        try:
            return json.loads(item["record_json"])
        except (TypeError, ValueError):
            return None
    finally:
        db.close()


def upsert_record(directory, job_key, record):
    """Write exactly one tracker record.

    save_triage() DELETEs every row for the user and reinserts the whole set,
    which is fine when the caller is holding the whole set but destructive for
    a single-record write: any row added concurrently between the load and the
    save would be dropped. Manual job entry writes one record, so it writes
    one row.
    """
    db, row = _profile(directory)
    try:
        db.execute(
            "INSERT INTO application_records (user_id,job_key,record_json,updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id,job_key) DO UPDATE SET "
            "record_json=excluded.record_json,updated_at=excluded.updated_at",
            (row["id"], str(job_key), json.dumps(record, ensure_ascii=False),
             datetime.utcnow().isoformat()))
        db.commit()
        return record
    finally:
        db.close()


def _record_identities(job_key, record):
    """Every identity string a search result could plausibly derive for a record.

    main.py builds a candidate identity from `id`, then `url`, then
    `title|company`, and which one it lands on depends on the source. Storing
    all three forms here means a match on any of them suppresses the job,
    rather than only the one form that happened to create the record.
    """
    identities = {str(job_key)}
    url = record.get("url")
    if url:
        identities.add(str(url))
    title, company = record.get("title"), record.get("company")
    if title or company:
        identities.add(f"{title or ''}|{company or ''}")
    return identities


def load_ignored_job_keys(directory):
    """Return the job keys future searches must leave out.

    Two different reasons land a job here, and conflating them caused a bug
    worth naming: jobs the user dismissed (status 'not_interested'), and jobs
    the user added by hand (origin 'manual'). A manually added job is already
    in the tracker at a real stage -- often Applied -- so it must not resurface
    as a fresh result, but it must NOT be marked not_interested to achieve
    that, because the tracker UI hides that status and the job would vanish
    from the very screen it was added to.
    """
    db, row = _profile(directory)
    try:
        ignored = set()
        for item in db.execute(
                "SELECT job_key,record_json FROM application_records WHERE user_id=?",
                (row["id"],)):
            try:
                record = json.loads(item["record_json"])
            except (TypeError, ValueError):
                continue
            if (record.get("status") == "not_interested"
                    or record.get("origin") == "manual"):
                for identity in _record_identities(item["job_key"], record):
                    ignored.add(_stable_job_key(identity))
        return ignored
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

def save_run_results(directory, test_mode, jobs, config=None):
    status, run_id = write_run_status(directory, test_mode, "running", "Publishing result snapshot")
    db=get_db()
    try:
        run = db.execute("SELECT user_id FROM search_runs WHERE id=?", (run_id,)).fetchone()
        role_name, role_color = _role_metadata(
            db, run["user_id"], config or {}) if run else ("Default", DEFAULT_ROLE_COLOR)
        db.execute("DELETE FROM job_results WHERE run_id=?", (run_id,))
        for job in jobs:
            identity = (job.get("id") or job.get("url") or
                        f"{job.get('title', '')}|{job.get('company', '')}")
            key = _stable_job_key(identity)
            public_job = {k: v for k, v in job.items() if not k.startswith("_")}
            public_job["search_role"] = role_name
            public_job["search_role_color"] = role_color
            if key: db.execute("INSERT OR REPLACE INTO job_results (run_id,job_key,job_json,score) VALUES (?,?,?,?)", (run_id, str(key), json.dumps(public_job, ensure_ascii=False), job.get("score")))
        db.commit()
    finally: db.close()
    return run_id


def _audit_config(config):
    """Return the criteria snapshot without credentials or legacy secrets."""
    safe = json.loads(json.dumps(config or {}))
    for key in ("credentials", "serpapi_key", "adzuna_credentials"):
        safe.pop(key, None)
    return safe


def _audit_job(job):
    fields = (
        "title", "company", "url", "apply_url", "source", "location",
        "remote_status", "salary", "score", "posted", "posted_at",
        "distance_miles", "employment_type", "matches", "match_details",
        "search_role", "search_role_color",
    )
    return {field: job[field] for field in fields
            if job.get(field) is not None and job.get(field) != ""}


def save_run_audit(directory, run_id, config, jobs, summary):
    """Persist every deduplicated candidate and the decisions made about it."""
    db, row = _profile(directory)
    try:
        owned = db.execute(
            "SELECT id FROM search_runs WHERE id=? AND user_id=?",
            (run_id, row["id"])).fetchone()
        if not owned:
            raise ValueError("Search run does not belong to profile")
        db.execute(
            "INSERT OR REPLACE INTO search_run_audits "
            "(run_id,config_json,summary_json) VALUES (?,?,?)",
            (run_id, json.dumps(_audit_config(config), ensure_ascii=False),
             json.dumps(summary or {}, ensure_ascii=False)))
        db.execute("DELETE FROM job_decisions WHERE run_id=?", (run_id,))
        for job in jobs:
            decision = job.get("_decision", {})
            identity = (job.get("id") or job.get("url") or
                        f"{job.get('title', '')}|{job.get('company', '')}")
            key = _stable_job_key(identity)
            db.execute(
                "INSERT OR REPLACE INTO job_decisions "
                "(run_id,job_key,job_json,decision_json,outcome,score) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, key, json.dumps(_audit_job(job), ensure_ascii=False),
                 json.dumps(decision, ensure_ascii=False),
                 decision.get("outcome", "unknown"), job.get("score")))
        db.commit()
    finally:
        db.close()


def decision_report(directory, run_id=None, limit=2500):
    db, row = _profile(directory)
    try:
        params = [row["id"]]
        where = "r.user_id=?"
        if run_id is not None:
            where += " AND r.id=?"
            params.append(int(run_id))
        run = db.execute(
            "SELECT r.id,r.test_mode,r.state,r.started_at,r.completed_at,"
            "a.config_json,a.summary_json "
            "FROM search_runs r JOIN search_run_audits a ON a.run_id=r.id "
            f"WHERE {where} ORDER BY r.id DESC LIMIT 1", params).fetchone()
        if not run:
            return None
        jobs = []
        for item in db.execute(
                "SELECT job_key,job_json,decision_json,outcome,score "
                "FROM job_decisions WHERE run_id=? ORDER BY "
                "CASE WHEN outcome='included' THEN 0 ELSE 1 END, score DESC, id",
                (run["id"],)).fetchmany(limit):
            jobs.append({
                "job_key": item["job_key"],
                "job": json.loads(item["job_json"]),
                "decision": json.loads(item["decision_json"]),
                "outcome": item["outcome"],
                "score": item["score"],
            })
        return {
            "id": run["id"], "test_mode": bool(run["test_mode"]),
            "state": run["state"], "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "config": json.loads(run["config_json"]),
            "summary": json.loads(run["summary_json"]), "jobs": jobs,
        }
    finally:
        db.close()


def decision_report_history(directory, limit=20):
    db, row = _profile(directory)
    try:
        return [dict(r) for r in db.execute(
            "SELECT r.id,r.test_mode,r.state,r.started_at,r.completed_at,"
            "count(d.id) candidate_count,"
            "sum(CASE WHEN d.outcome='included' THEN 1 ELSE 0 END) included_count "
            "FROM search_runs r JOIN search_run_audits a ON a.run_id=r.id "
            "LEFT JOIN job_decisions d ON d.run_id=r.id WHERE r.user_id=? "
            "GROUP BY r.id ORDER BY r.id DESC LIMIT ?", (row["id"], limit))]
    finally:
        db.close()

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
