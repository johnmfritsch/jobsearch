"""JSON API for the JobSearch portal.

Replaces api_handler.py's hand-rolled http.server dispatch. The endpoint names
and response shapes are preserved exactly, so portal.js needs only its base-URL
constant changed.

Security properties carried over deliberately:

* The profile is ALWAYS resolved from the session, never from request input.
  A `user` field in the body/query is accepted only to be compared against the
  session's profile and rejected on mismatch — it can never select a profile.
* `GET /credentials` always returns masked values. `POST /credentials/reveal`
  returns one named entry's plaintext, scoped to the caller's own session
  profile exactly like every other endpoint here — used by the portal's Edit
  button so a stored key can be copied. An unchanged mask on save leaves the
  stored secret untouched.

Dropped on purpose: the CORS `Access-Control-Allow-Origin: *` headers the old
handler sent on every response. Flask serves the frontend same-origin now, so
cross-origin access is neither needed nor desirable for a cookie-authenticated
API.
"""
import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO

from flask import Blueprint, current_app, jsonify, request, send_file
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import auto_apply
import document_generator
import manual_jobs
import profile_store
import role_documents
from auth import current_user
from database import request_db

api_bp = Blueprint('api', __name__, url_prefix='/api')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The pipeline runs on entware Python (sentence-transformers lives there); the
# web app runs on AppCentral Python (Flask lives there). They are different
# interpreters with different site-packages, so the subprocess must be given
# the entware environment explicitly or main.py cannot import its dependencies.
PIPELINE_PYTHON = '/opt/bin/python3'
PIPELINE_ENV = {
    'LD_LIBRARY_PATH': '/opt/lib',
    'PYTHONPATH': '/opt/lib/python3.11/site-packages',
}

TRIAGE_STATUSES = {'saved', 'applied', 'not_interested', 'none'}
TRACKER_STAGES = {'saved', 'applied', 'interviewing', 'offer', 'rejected', 'closed'}

# 'onsite' is accepted because that is what job boards say; the portal's filters
# spell the same thing 'local', so it is normalized on the way in.
REMOTE_STATUSES = {'remote', 'hybrid', 'local', 'onsite', 'unclear'}
HEX_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
NOTE_TEXT_LIMIT = 20000


# ── helpers ──────────────────────────────────────────────────────────────────

def _fail(message, status):
    return jsonify({'error': message}), status


def _process_alive(pid):
    """True if pid is running, or exists but is owned by another user."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _log_tail(key, nbytes=2000):
    log_path = os.path.join(BASE_DIR, 'logs', key, 'run_job_search.log')
    try:
        with open(log_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            return f.read().decode('utf-8', errors='replace').strip()
    except OSError:
        return ''


def _profile(supplied=None):
    """Return (profile_key, None) or (None, error_response).

    The profile comes from the session. `supplied` is only ever checked for
    agreement, so a caller cannot reach another account's data by passing a
    different name.
    """
    user = current_user()
    if not user:
        return None, _fail('Sign in required', 401)
    key = user['profile_key']
    if not key:
        return None, _fail('This account has no profile yet', 403)
    if supplied and supplied != key:
        return None, _fail('That profile belongs to another account', 403)
    return key, None


def _body():
    return request.get_json(silent=True) or {}


def _masked(value):
    return ('••••' + value[-4:]) if isinstance(value, str) and value else 'Not set'


# ── identity / onboarding ────────────────────────────────────────────────────

@api_bp.route('/me')
def me():
    user = current_user()
    if not user:
        return _fail('Sign in required', 401)
    row = request_db().execute(
        'SELECT onboarding_state FROM user_profiles WHERE user_id = ?',
        (user['id'],)).fetchone()
    return jsonify({'success': True, 'user': {
        'id': user['id'],
        'display_name': user['display_name'],
        'email': user['email'],
        'profile_key': user['profile_key'],
        'legacy_directory': user['legacy_directory'],
        'onboarding_state': row['onboarding_state'] if row else 'complete',
    }})


@api_bp.route('/onboarding', methods=['POST'])
def onboarding():
    user = current_user()
    if not user:
        return _fail('Sign in required', 401)
    state = _body().get('state')
    if state not in ('complete', 'dismissed'):
        return _fail('Invalid onboarding state', 400)
    db = request_db()
    db.execute('UPDATE user_profiles SET onboarding_state = ?, updated_at = ? '
               'WHERE user_id = ?', (state, datetime.utcnow().isoformat(), user['id']))
    db.commit()
    return jsonify({'success': True, 'state': state})


@api_bp.route('/logout', methods=['POST'])
def logout():
    """JSON logout for portal.js. The HTML form logout lives in auth.py."""
    from flask import session
    from auth import _revoke
    _revoke(session.get('token'))
    session.clear()
    return jsonify({'success': True})


# ── config ───────────────────────────────────────────────────────────────────

@api_bp.route('/load_config')
def load_config():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    test_mode = request.args.get('test_mode', 'false').lower() == 'true'
    config = profile_store.load_config(key, test_mode)
    if config is None:
        return _fail('Config not found', 404)
    return jsonify({'success': True,
                    'config': {k: v for k, v in config.items() if k != 'credentials'}})


@api_bp.route('/load_previous')
def load_previous():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    test_mode = request.args.get('test_mode', 'false').lower() == 'true'
    config = profile_store.load_config(key, test_mode, previous=True)
    if config is None:
        return _fail('No previous config found', 404)
    return jsonify({'success': True,
                    'config': {k: v for k, v in config.items() if k != 'credentials'},
                    'message': 'Loaded previous configuration'})


@api_bp.route('/save', methods=['POST'])
def save():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    config = data.get('config')
    if not isinstance(config, dict):
        return _fail('Missing config', 400)
    current = profile_store.load_config(key, data.get('test_mode', False)) or {}
    # Credentials are never sent by the config editor; preserve what is stored
    # rather than letting a save silently wipe them.
    if 'credentials' not in config:
        config['credentials'] = current.get('credentials', {})
    profile_store.save_config(key, config, data.get('test_mode', False))
    return jsonify({'success': True, 'message': f'Configuration saved for {key}'})


# ── priority employers ──────────────────────────────────────────────────────

@api_bp.route('/employer_sources')
def employer_sources():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    return jsonify({'success': True,
                    'sources': profile_store.list_employer_sources(key)})


@api_bp.route('/employer_sources', methods=['POST'])
def save_employer_source():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    source = data.get('source')
    try:
        if not isinstance(source, dict):
            raise ValueError('Invalid priority employer')
        careers_url, _ = profile_store.normalize_employer_url(source.get('careers_url'))
        detection = None
        if careers_url:
            from scrapers.adapter_registry import probe_url
            from scrapers.adapter_utils import employer_name_matches
            detection = probe_url(careers_url, deadline=time.monotonic() + 15)
            source['_server_adapter'] = detection['adapter']
            detected_name = detection.get('company_name', '')
            if detected_name and not employer_name_matches(detected_name, source):
                raise ValueError('The careers page organization does not match this priority employer or its aliases')
        saved = profile_store.save_employer_source(key, source)
        if isinstance(source, dict) and 'role_ids' in source:
            profile_store.set_employer_source_roles(key, saved['id'], source.get('role_ids'))
        current = profile_store.list_employer_sources(key)
        saved = next(item for item in current if item['id'] == saved['id'])
    except (ValueError, LookupError) as error:
        return _fail(str(error), 400 if isinstance(error, ValueError) else 404)
    return jsonify({'success': True, 'source': saved, 'sources': current,
                    'detection': detection})


@api_bp.route('/employer_sources/<int:source_id>/roles')
def get_employer_source_roles(source_id):
    key, err = _profile()
    if err:
        return err
    try:
        result = profile_store.employer_source_roles(key, source_id)
    except LookupError as error:
        return _fail(str(error), 404)
    return jsonify({'success': True, 'enabled_all_roles': result['enabled_all_roles'],
                    'roles': result['roles']})


@api_bp.route('/employer_sources/detect', methods=['POST'])
def detect_employer_source():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    try:
        careers_url, _ = profile_store.normalize_employer_url(data.get('careers_url'))
    except ValueError as error:
        return _fail(str(error), 400)
    if not careers_url:
        return _fail('Enter a careers URL to detect from', 400)
    from scrapers.adapter_registry import probe_url
    # Same 20s worker-safe deadline pattern as the /test endpoint below --
    # this also runs synchronously in a Gunicorn worker.
    try:
        result = probe_url(careers_url, deadline=time.monotonic() + 15)
    except Exception as error:
        return _fail(str(error)[:500] or 'Could not read that careers page', 502)
    return jsonify({'success': True, 'company_name': result.get('company_name', ''),
                    'adapter': result['adapter'], 'platform': result.get('platform', ''),
                    'capabilities': result.get('capabilities', {}),
                    'explanation': result.get('explanation', ''),
                    'supported': result['adapter'] != 'unsupported'})


@api_bp.route('/employer_sources/classify', methods=['POST'])
def classify_employer_source_url():
    """Instant, network-free URL -> adapter classification (registry
    pattern matching only, no fetch) so the UI can flag an unrecognized
    job-board platform the moment a URL is entered -- before Add is even
    clicked, and independent of the slower, network-bound /detect call."""
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    try:
        careers_url, _ = profile_store.normalize_employer_url(data.get('careers_url'))
    except ValueError as error:
        return _fail(str(error), 400)
    if not careers_url:
        return jsonify({'success': True, 'adapter': 'serpapi_company', 'supported': True})
    from scrapers.adapter_registry import classify_url
    adapter = classify_url(careers_url)
    return jsonify({'success': True, 'adapter': adapter, 'supported': adapter != 'unsupported',
                    'pending_probe': adapter == 'unsupported'})


@api_bp.route('/employer_sources/discover', methods=['POST'])
def discover_employer_source():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    company_name = re.sub(r'\s+', ' ', str(data.get('company_name') or '')).strip()
    if not company_name:
        return _fail('Enter a company name to search for', 400)
    # Best-effort only, and free (no paid search API) -- any failure here
    # (nothing found, a timeout) is reported back as simply "nothing found,"
    # never an error; the caller falls back to the company-name-only SerpAPI
    # adapter either way.
    careers_url = ''
    try:
        from scrapers.employer_source_scraper import discover_careers_url
        careers_url = discover_careers_url(company_name, deadline=time.monotonic() + 15)
    except Exception:
        careers_url = ''
    return jsonify({'success': True, 'careers_url': careers_url})


@api_bp.route('/employer_sources/<int:source_id>', methods=['DELETE'])
def delete_employer_source(source_id):
    key, err = _profile()
    if err:
        return err
    if not profile_store.delete_employer_source(key, source_id):
        return _fail('Priority employer not found', 404)
    return jsonify({'success': True, 'sources': profile_store.list_employer_sources(key)})


@api_bp.route('/employer_sources/<int:source_id>/test', methods=['POST'])
def test_employer_source(source_id):
    key, err = _profile()
    if err:
        return err
    source = next((item for item in profile_store.list_employer_sources(key)
                   if item['id'] == source_id), None)
    if not source:
        return _fail('Priority employer not found', 404)
    # This endpoint runs in a Gunicorn worker, unlike detached live searches.
    # The adapter receives a hard monotonic deadline far below --timeout 120.
    try:
        # Probe-only adapters are revalidated server-side here too. A positive
        # result is persisted; an unrecognized URL remains the explicit
        # fallback rather than trusting an old client-side classification.
        if source.get('adapter') in ('jibe', 'structured_data', 'unsupported'):
            from scrapers.adapter_registry import probe_url
            detection = probe_url(source.get('careers_url') or '', deadline=time.monotonic() + 15)
            if detection['adapter'] != source.get('adapter'):
                profile_store.reclassify_employer_source(key, source_id, detection['adapter'])
                source['adapter'] = detection['adapter']
        from scrapers.employer_source_scraper import test_employer_source as test_source
        result = test_source(source, deadline=time.monotonic() + 20)
    except Exception as error:
        return _fail(str(error)[:500] or 'Could not test that priority employer', 502)
    return jsonify({'success': True, 'result': result})


# ── resume ───────────────────────────────────────────────────────────────────

@api_bp.route('/resume')
def load_resume():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    return jsonify({'success': True, 'text': profile_store.load_resume(key)})


@api_bp.route('/resume', methods=['POST'])
def save_resume():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    text = data.get('text')
    if not isinstance(text, str):
        return _fail('Invalid resume text', 400)
    profile_store.save_resume(key, text)
    user = current_user()
    role_documents.sync_resume_text(
        request_db(), user['id'], None, 'Default', text)
    return jsonify({'success': True})


# ── named search presets ─────────────────────────────────────────────────────

@api_bp.route('/search_presets')
def search_presets():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    return jsonify({
        'success': True,
        'presets': profile_store.list_search_presets(key),
    })


@api_bp.route('/search_presets', methods=['POST'])
def save_search_preset():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    name = str(data.get('name') or '').strip()
    config = data.get('config')
    resume = data.get('resume')
    if not name:
        return _fail('Enter a preset name', 400)
    if len(name) > 80:
        return _fail('Preset names are limited to 80 characters', 400)
    if not isinstance(config, dict):
        return _fail('Missing preset configuration', 400)
    if not isinstance(resume, str) or not resume.strip():
        return _fail('A resume is required in every search preset', 400)
    preset = profile_store.save_search_preset(key, name, config, resume)
    user = current_user()
    role_documents.sync_resume_text(
        request_db(), user['id'], preset['id'], preset['name'], resume)
    return jsonify({'success': True, 'preset': preset})


@api_bp.route('/search_presets/load', methods=['POST'])
def load_search_preset():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    try:
        preset_id = int(data.get('preset_id'))
    except (TypeError, ValueError):
        return _fail('Choose a search preset', 400)
    preset = profile_store.activate_search_preset(key, preset_id)
    if not preset:
        return _fail('Search preset not found', 404)
    return jsonify({'success': True, 'preset': preset})


@api_bp.route('/search_presets/delete', methods=['POST'])
def delete_search_preset():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    try:
        preset_id = int(data.get('preset_id'))
    except (TypeError, ValueError):
        return _fail('Choose a search preset', 400)
    if not profile_store.delete_search_preset(key, preset_id):
        return _fail('Search preset not found', 404)
    return jsonify({'success': True})


# ── versioned role documents ─────────────────────────────────────────────────

def _requested_role(db, user, values):
    attempt_id = values.get('attempt_id')
    if attempt_id:
        attempt = auto_apply.get_attempt(db, user['id'], attempt_id, False)
        if not attempt:
            raise LookupError('Application attempt not found')
        return role_documents.resolve_role(
            db, user['id'], role_name=attempt['job'].get('search_role') or 'Default')
    return role_documents.resolve_role(
        db, user['id'], values.get('preset_id'), values.get('role_name'))


@api_bp.route('/role_documents')
def get_role_documents():
    user, err = _application_user()
    if err:
        return err
    try:
        role = _requested_role(request_db(), user, request.args)
    except ValueError as exc:
        return _fail(str(exc), 400)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, **role_documents.list_documents(
        request_db(), user['id'], role)})


@api_bp.route('/role_documents/upload', methods=['POST'])
def upload_role_document():
    user, err = _application_user()
    if err:
        return err
    upload = request.files.get('file')
    if not upload or not upload.filename:
        return _fail('Choose a PDF file', 400)
    db = request_db()
    try:
        role = _requested_role(db, user, request.form)
        role_documents.save_pdf(
            db, current_app.config['DATABASE'], user['id'], role,
            str(request.form.get('kind') or ''), upload.read(), upload.filename)
    except ValueError as exc:
        return _fail(str(exc), 400)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, **role_documents.list_documents(
        db, user['id'], role)})


@api_bp.route('/role_documents/<document_id>/current', methods=['POST'])
def make_role_document_current(document_id):
    user, err = _application_user()
    if err:
        return err
    try:
        document = role_documents.make_current(request_db(), user['id'], document_id)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, 'document': document})


@api_bp.route('/role_documents/<document_id>/download')
def download_role_document(document_id):
    user, err = _application_user()
    if err:
        return err
    row = role_documents.get_document(request_db(), user['id'], document_id)
    if not row:
        return _fail('Role document not found', 404)
    if row['format'] == 'txt':
        data = BytesIO(str(row['content_text'] or '').encode('utf-8'))
        return send_file(data, mimetype=row['mime_type'], as_attachment=True,
                         download_name=row['file_name'])
    try:
        path = role_documents.safe_path(
            role_documents.library_root(current_app.config['DATABASE']),
            row['relative_path'])
    except ValueError as exc:
        return _fail(str(exc), 400)
    if not path.is_file() or auto_apply.file_sha256(path) != row['sha256']:
        return _fail('Role document file is missing or damaged', 409)
    return send_file(path, mimetype=row['mime_type'], as_attachment=True,
                     download_name=row['file_name'], conditional=True)


# ── credentials ──────────────────────────────────────────────────────────────

@api_bp.route('/credentials')
def credentials():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    creds = (profile_store.load_config(key) or {}).get('credentials', {})
    return jsonify({
        'success': True,
        'serpapi': [{'name': x.get('name', ''), 'key_mask': _masked(x.get('key'))}
                    for x in creds.get('serpapi', []) if isinstance(x, dict)],
        'adzuna': [{'name': x.get('name', ''),
                    'app_id_mask': _masked(x.get('app_id')),
                    'app_key_mask': _masked(x.get('app_key'))}
                   for x in creds.get('adzuna', []) if isinstance(x, dict)],
    })


@api_bp.route('/credentials/reveal', methods=['POST'])
def reveal_credential():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    service = data.get('service')
    name = data.get('name')
    if service not in ('serpapi', 'adzuna'):
        return _fail('Invalid service', 400)
    creds = (profile_store.load_config(key) or {}).get('credentials', {})
    entry = next((x for x in creds.get(service, [])
                  if isinstance(x, dict) and x.get('name') == name), None)
    if not entry:
        return _fail('Not found', 404)
    if service == 'serpapi':
        return jsonify({'success': True, 'key': entry.get('key', '')})
    return jsonify({'success': True, 'app_id': entry.get('app_id', ''), 'app_key': entry.get('app_key', '')})


@api_bp.route('/credentials', methods=['POST'])
def save_credentials():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    incoming = data.get('credentials')
    if not isinstance(incoming, dict):
        return _fail('Invalid credentials', 400)
    config = profile_store.load_config(key) or {}
    stored = config.get('credentials', {})

    def merge(kind, secret_fields):
        existing = {x.get('name'): x for x in stored.get(kind, []) if isinstance(x, dict)}
        result = []
        for item in incoming.get(kind, []):
            if not isinstance(item, dict) or not item.get('name'):
                continue
            prior = existing.get(item['name'], {})
            entry = {'name': item['name']}
            for field in secret_fields:
                value = item.get(field)
                # A masked or omitted value means "unchanged" — never overwrite
                # a real secret with the bullets the UI displays.
                entry[field] = (prior.get(field, '')
                                if not value or value.startswith('••••')
                                else value)
            result.append(entry)
        return result

    config['credentials'] = {
        'serpapi': merge('serpapi', ['key']),
        'adzuna': merge('adzuna', ['app_id', 'app_key']),
        # Application credentials are managed from the application assistant,
        # not this scraper form. Preserve them across scraper edits.
        'openai': stored.get('openai', {}),
        'anthropic': stored.get('anthropic', {}),
        'xai': stored.get('xai', {}),
    }
    source_choices = data.get('sources')
    if source_choices is not None:
        if not isinstance(source_choices, dict):
            return _fail('Invalid source choices', 400)
        sources = config.get('sources', {})
        for source in ('serpapi', 'adzuna'):
            if source in source_choices:
                sources[source] = bool(source_choices[source])
        config['sources'] = sources
    profile_store.save_config(key, config)
    return jsonify({'success': True})


# ── supervised application assistant ─────────────────────────────────────────

def _application_user():
    user = current_user()
    if not user:
        return None, _fail('Sign in required', 401)
    return user, None


def _provider_key(profile_key, provider):
    config = profile_store.load_config(profile_key) or {}
    value = (config.get('credentials', {}).get(provider) or {}).get('api_key')
    return value if isinstance(value, str) else ''


def _provider_summaries(profile_key):
    result = {}
    for provider, info in document_generator.PROVIDERS.items():
        key = _provider_key(profile_key, provider)
        result[provider] = {
            'label': info['label'], 'model': info['model'],
            'is_set': bool(key), 'key_mask': _masked(key),
        }
    return result


def _resume_for_attempt(db, user, attempt):
    """Resolve the resume from the role recorded on the server-side job snapshot."""
    role_name = str(attempt.get('job', {}).get('search_role') or 'Default').strip() or 'Default'
    role = role_documents.resolve_role(db, user['id'], role_name=role_name)
    row = db.execute(
        'SELECT content_text AS resume_text FROM role_documents WHERE user_id=? '
        'AND role_key=? AND kind=? AND format=? AND is_current=1 '
        'ORDER BY version DESC LIMIT 1',
        (user['id'], role['role_key'], 'resume', 'txt')).fetchone()
    if not row:
        row = db.execute(
            'SELECT resume_text FROM search_presets WHERE user_id=? AND name=? COLLATE NOCASE',
            (user['id'], role_name)).fetchone()
    if row and str(row['resume_text'] or '').strip():
        return row['resume_text'], role_name
    return profile_store.load_resume(user['profile_key']), role_name


def _worker_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],
                                  salt='jobsearch-application-worker')


def _worker_request(action, user_id, attempt, extra=None):
    token = _worker_serializer().dumps({
        'attempt_id': attempt['id'], 'user_id': user_id, 'action': action,
    })
    payload = {
        'action': action,
        'attempt_id': attempt['id'],
        'url': attempt.get('resolved_url') or attempt.get('source_url'),
        'ats_kind': attempt.get('ats_kind') or 'unknown',
        'job': attempt.get('job') or {},
        'answers': attempt.get('answers') or {},
        'resume_artifact_id': attempt.get('selected_resume_id'),
        'cover_artifact_id': attempt.get('selected_cover_id'),
    }
    payload.update(extra or {})
    req = urllib.request.Request(
        current_app.config['APPLICATION_WORKER_URL'] + '/attempt',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8')).get('error')
        except Exception:
            detail = None
        raise RuntimeError(detail or f'Application worker failed ({exc.code})') from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError('Application worker is unavailable') from exc


@api_bp.route('/application_credentials')
def application_credentials():
    user, err = _application_user()
    if err:
        return err
    providers = _provider_summaries(user['profile_key'])
    return jsonify({'success': True, 'providers': providers,
                    'openai': providers['openai']})


@api_bp.route('/application_credentials', methods=['POST'])
def save_application_credentials():
    user, err = _application_user()
    if err:
        return err
    data = _body()
    config = profile_store.load_config(user['profile_key']) or {}
    credentials = config.setdefault('credentials', {})
    provider = str(data.get('provider') or 'openai').strip().lower()
    if provider not in document_generator.PROVIDERS:
        return _fail('Choose OpenAI, Anthropic Claude, or xAI Grok', 400)
    stored = credentials.get(provider) or {}
    if data.get('remove'):
        credentials.pop(provider, None)
    else:
        value = data.get('api_key')
        if value is None or (isinstance(value, str) and value.startswith('••••')):
            value = stored.get('api_key', '')
        if not isinstance(value, str) or not value.strip():
            return _fail(f"Enter a {document_generator.PROVIDERS[provider]['label']} API key", 400)
        if len(value.strip()) > 500:
            return _fail('API key is too long', 400)
        credentials[provider] = {'api_key': value.strip()}
    profile_store.save_config(user['profile_key'], config)
    providers = _provider_summaries(user['profile_key'])
    return jsonify({'success': True, 'providers': providers,
                    'openai': providers['openai']})


@api_bp.route('/application_profile')
def application_profile():
    user, err = _application_user()
    if err:
        return err
    return jsonify({'success': True, **auto_apply.load_profile(request_db(), user['id'])})


@api_bp.route('/application_profile', methods=['POST'])
def save_application_profile():
    user, err = _application_user()
    if err:
        return err
    try:
        result = auto_apply.save_profile(request_db(), user['id'], _body().get('profile'))
    except ValueError as exc:
        return _fail(str(exc), 400)
    return jsonify({'success': True, **result})


@api_bp.route('/application_attempts')
def application_attempts():
    user, err = _application_user()
    if err:
        return err
    return jsonify({'success': True,
                    'attempts': auto_apply.list_attempts(request_db(), user['id'])})


@api_bp.route('/application_attempts', methods=['POST'])
def create_application_attempt():
    user, err = _application_user()
    if err:
        return err
    try:
        attempt, created = auto_apply.create_attempt(
            request_db(), user['id'], _body().get('job_key'))
    except ValueError as exc:
        return _fail(str(exc), 400)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, 'created': created, 'attempt': attempt}), (201 if created else 200)


@api_bp.route('/application_attempts/<attempt_id>')
def application_attempt(attempt_id):
    user, err = _application_user()
    if err:
        return err
    attempt = auto_apply.get_attempt(request_db(), user['id'], attempt_id)
    if not attempt:
        return _fail('Application attempt not found', 404)
    role = role_documents.resolve_role(
        request_db(), user['id'],
        role_name=attempt['job'].get('search_role') or 'Default')
    return jsonify({'success': True, 'attempt': attempt,
                    'role_documents': role_documents.list_documents(
                        request_db(), user['id'], role),
                    'features': {
                        'preparation_enabled': current_app.config['APPLICATION_PREP_ENABLED'],
                        'submission_enabled': current_app.config['APPLICATION_SUBMIT_ENABLED'],
                    }})


@api_bp.route('/application_attempts/<attempt_id>/cancel', methods=['POST'])
def cancel_application_attempt(attempt_id):
    user, err = _application_user()
    if err:
        return err
    try:
        attempt = auto_apply.transition(
            request_db(), user['id'], attempt_id, 'cancelled', 'Application attempt cancelled')
    except LookupError as exc:
        return _fail(str(exc), 404)
    except ValueError as exc:
        return _fail(str(exc), 409)
    return jsonify({'success': True, 'attempt': attempt})


@api_bp.route('/application_attempts/<attempt_id>/retry', methods=['POST'])
def retry_application_attempt(attempt_id):
    user, err = _application_user()
    if err:
        return err
    try:
        attempt, created = auto_apply.retry_attempt(request_db(), user['id'], attempt_id)
    except LookupError as exc:
        return _fail(str(exc), 404)
    except ValueError as exc:
        return _fail(str(exc), 409)
    return jsonify({'success': True, 'created': created, 'attempt': attempt})


@api_bp.route('/application_attempts/<attempt_id>/answers', methods=['POST'])
def save_application_answers(attempt_id):
    user, err = _application_user()
    if err:
        return err
    try:
        attempt = auto_apply.save_answers(
            request_db(), user['id'], attempt_id, _body().get('answers'))
    except ValueError as exc:
        return _fail(str(exc), 400)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, 'attempt': attempt})


@api_bp.route('/application_attempts/<attempt_id>/documents/estimate')
def estimate_application_documents(attempt_id):
    user, err = _application_user()
    if err:
        return err
    attempt = auto_apply.get_attempt(request_db(), user['id'], attempt_id, False)
    if not attempt:
        return _fail('Application attempt not found', 404)
    provider = str(request.args.get('provider') or 'openai').strip().lower()
    generate_resume = request.args.get('resume', '1') not in {'0', 'false'}
    generate_cover = request.args.get('cover_letter', '1') not in {'0', 'false'}
    profile = auto_apply.load_profile(request_db(), user['id'])['profile']
    resume, role_name = _resume_for_attempt(request_db(), user, attempt)
    try:
        estimate = document_generator.estimate_cost(
            resume, attempt['job'], profile, provider,
            generate_resume, generate_cover)
    except document_generator.GenerationError as exc:
        return _fail(str(exc), 400)
    return jsonify({'success': True, 'estimate': estimate,
                    'role_name': role_name,
                    'has_saved_resume': bool(str(resume or '').strip())})


@api_bp.route('/application_attempts/<attempt_id>/documents/use-saved', methods=['POST'])
def use_saved_application_resume(attempt_id):
    user, err = _application_user()
    if err:
        return err
    db = request_db()
    attempt = auto_apply.get_attempt(db, user['id'], attempt_id, False)
    if not attempt:
        return _fail('Application attempt not found', 404)
    resume, role_name = _resume_for_attempt(db, user, attempt)
    role = role_documents.resolve_role(db, user['id'], role_name=role_name)
    data = _body()

    def selected_pdf(field, kind):
        document_id = data.get(field)
        if not document_id:
            return None
        row = role_documents.get_document(
            db, user['id'], document_id, role['role_key'])
        if not row or row['kind'] != kind or row['format'] != 'pdf':
            raise ValueError(f'Choose a valid saved {kind.replace("_", " ")} PDF')
        path = role_documents.safe_path(
            role_documents.library_root(current_app.config['DATABASE']),
            row['relative_path'])
        if not path.is_file() or auto_apply.file_sha256(path) != row['sha256']:
            raise ValueError('A selected role document is missing or damaged')
        return {'file_name': row['file_name'], 'content': path.read_bytes()}
    try:
        resume_pdf = selected_pdf('resume_document_id', 'resume')
        cover_pdf = selected_pdf('cover_document_id', 'cover_letter')
        attempt = document_generator.use_saved_documents(
            db, current_app.config['DATABASE'], user['id'], attempt_id,
            resume, role_name, resume_pdf, cover_pdf)
    except LookupError as exc:
        return _fail(str(exc), 404)
    except (ValueError, document_generator.GenerationError) as exc:
        return _fail(str(exc), 400)
    return jsonify({'success': True, 'attempt': attempt, 'role_name': role_name})


@api_bp.route('/application_attempts/<attempt_id>/documents/generate', methods=['POST'])
def generate_application_documents(attempt_id):
    user, err = _application_user()
    if err:
        return err
    data = _body()
    provider = str(data.get('provider') or 'openai').strip().lower()
    if provider not in document_generator.PROVIDERS:
        return _fail('Choose OpenAI, Anthropic Claude, or xAI Grok', 400)
    generate_resume = data.get('generate_resume', True) is True
    generate_cover = data.get('generate_cover_letter', True) is True
    if not generate_resume and not generate_cover:
        return _fail('Select resume, cover letter, or both', 400)
    db = request_db()
    current = auto_apply.get_attempt(db, user['id'], attempt_id, False)
    if not current:
        return _fail('Application attempt not found', 404)
    profile = auto_apply.load_profile(db, user['id'])['profile']
    resume, _role_name = _resume_for_attempt(db, user, current)
    try:
        attempt = document_generator.generate_documents(
            db, current_app.config['DATABASE'], user['id'], attempt_id,
            provider, _provider_key(user['profile_key'], provider), resume, profile,
            generate_resume, generate_cover,
        )
    except LookupError as exc:
        return _fail(str(exc), 404)
    except (ValueError, document_generator.GenerationError) as exc:
        return _fail(str(exc), 400)
    return jsonify({'success': True, 'attempt': attempt})


@api_bp.route('/application_artifacts/<artifact_id>/decision', methods=['POST'])
def decide_application_artifact(artifact_id):
    user, err = _application_user()
    if err:
        return err
    try:
        attempt = auto_apply.approve_artifact(
            request_db(), user['id'], artifact_id, _body().get('decision'))
    except ValueError as exc:
        return _fail(str(exc), 400)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, 'attempt': attempt})


@api_bp.route('/application_artifacts/<artifact_id>/save-to-role', methods=['POST'])
def save_application_artifact_to_role(artifact_id):
    user, err = _application_user()
    if err:
        return err
    db = request_db()
    anchor = auto_apply.artifact_for_download(db, user['id'], artifact_id)
    if not anchor:
        return _fail('Document not found', 404)
    if anchor['kind'] not in {'resume', 'cover_letter'}:
        return _fail('Only resumes and cover letters can become role defaults', 400)
    if anchor['approval_state'] != 'approved':
        return _fail('Approve the generated document before saving it to the role', 409)
    attempt = auto_apply.get_attempt(db, user['id'], anchor['attempt_id'], False)
    role = role_documents.resolve_role(
        db, user['id'], role_name=attempt['job'].get('search_role') or 'Default')
    rows = db.execute(
        'SELECT * FROM application_artifacts WHERE attempt_id=? AND user_id=? '
        'AND kind=? AND version=? AND approval_state=? AND format IN (?,?)',
        (anchor['attempt_id'], user['id'], anchor['kind'], anchor['version'],
         'approved', 'txt', 'pdf')).fetchall()
    root = auto_apply.artifact_root(current_app.config['DATABASE'])
    try:
        prepared = []
        for row in rows:
            path = auto_apply.safe_artifact_path(root, row['relative_path'])
            if not path.is_file() or auto_apply.file_sha256(path) != row['sha256']:
                raise ValueError('Generated document is missing or damaged')
            prepared.append((row, path.read_bytes()))
        for row, content in prepared:
            if row['format'] == 'txt':
                role_documents.save_text(
                    db, user['id'], role, row['kind'], content.decode('utf-8'),
                    'ai_generated')
            else:
                role_documents.save_pdf(
                    db, current_app.config['DATABASE'], user['id'], role,
                    row['kind'], content, row['file_name'], 'ai_generated')
    except (UnicodeDecodeError, ValueError) as exc:
        return _fail(str(exc), 400)
    return jsonify({'success': True, **role_documents.list_documents(
        db, user['id'], role)})


@api_bp.route('/application_artifacts/<artifact_id>/download')
def download_application_artifact(artifact_id):
    user, err = _application_user()
    if err:
        return err
    row = auto_apply.artifact_for_download(request_db(), user['id'], artifact_id)
    if not row:
        return _fail('Document not found', 404)
    root = auto_apply.artifact_root(current_app.config['DATABASE'])
    try:
        path = auto_apply.safe_artifact_path(root, row['relative_path'])
    except ValueError as exc:
        return _fail(str(exc), 400)
    if not path.is_file():
        return _fail('Document file is missing', 404)
    if auto_apply.file_sha256(path) != row['sha256']:
        return _fail('Document integrity check failed', 409)
    return send_file(path, mimetype=row['mime_type'], as_attachment=True,
                     download_name=row['file_name'], conditional=True)


@api_bp.route('/application_attempts/<attempt_id>/prepare', methods=['POST'])
def prepare_application(attempt_id):
    user, err = _application_user()
    if err:
        return err
    if not current_app.config['APPLICATION_PREP_ENABLED']:
        return _fail('Form preparation is installed but disabled until DEV acceptance', 409)
    db = request_db()
    attempt = auto_apply.get_attempt(db, user['id'], attempt_id)
    if not attempt:
        return _fail('Application attempt not found', 404)
    if attempt['ats_kind'] == 'linkedin':
        attempt = auto_apply.transition(
            db, user['id'], attempt_id, 'manual_required',
            'LinkedIn applications use document preparation and manual handoff')
        return jsonify({'success': True, 'attempt': attempt})
    try:
        if attempt['state'] == 'queued':
            attempt = auto_apply.transition(
                db, user['id'], attempt_id, 'validating_url',
                'Checking the application URL', automation_used=True)
        auto_apply.transition(db, user['id'], attempt_id, 'analyzing_form',
                              'Analyzing the application form', automation_used=True)
        artifact_paths = {}
        for kind, artifact_id in (
                ('resume', attempt.get('selected_resume_id')),
                ('cover_letter', attempt.get('selected_cover_id'))):
            if not artifact_id:
                continue
            artifact = auto_apply.artifact_for_download(db, user['id'], artifact_id)
            if artifact:
                artifact_paths[kind] = '/artifacts/' + artifact['relative_path'].lstrip('/')
        result = _worker_request('prepare', user['id'], attempt, {
            'profile': auto_apply.load_profile(db, user['id'])['profile'],
            'artifact_paths': artifact_paths,
        })
        state = result.get('state')
        if state not in {'needs_user_input', 'needs_login', 'blocked_captcha',
                         'ready_for_review', 'manual_required', 'unsupported_site',
                         'could_not_fill'}:
            state = 'could_not_fill'
        db.execute(
            'UPDATE application_attempts SET resolved_url=?,ats_kind=?,answers_json=? '
            'WHERE id=? AND user_id=?',
            (result.get('resolved_url') or attempt.get('resolved_url'),
             result.get('ats_kind') or attempt.get('ats_kind') or 'unknown',
             json.dumps(result.get('answers') or attempt.get('answers') or {},
                        ensure_ascii=False), attempt_id, user['id']),
        )
        db.commit()
        submission_metadata = dict(result.get('metadata') or {})
        submission_metadata['selected_resume_id'] = attempt.get('selected_resume_id')
        submission_metadata['selected_cover_id'] = attempt.get('selected_cover_id')
        attempt = auto_apply.transition(
            db, user['id'], attempt_id, state,
            result.get('message') or auto_apply.ATTEMPT_STATES[state],
            result.get('error_code'), submission_metadata, automation_used=True)
    except (RuntimeError, ValueError) as exc:
        attempt = auto_apply.transition(
            db, user['id'], attempt_id, 'could_not_fill', str(exc),
            'worker_unavailable', automation_used=True)
        return jsonify({'success': False, 'attempt': attempt, 'error': str(exc)}), 502
    return jsonify({'success': True, 'attempt': attempt})


@api_bp.route('/application_attempts/<attempt_id>/confirmation-token', methods=['POST'])
def application_confirmation_token(attempt_id):
    user, err = _application_user()
    if err:
        return err
    try:
        token, expires = auto_apply.create_confirmation_token(
            request_db(), user['id'], attempt_id)
    except LookupError as exc:
        return _fail(str(exc), 404)
    except ValueError as exc:
        return _fail(str(exc), 409)
    return jsonify({'success': True, 'confirmation_token': token, 'expires_at': expires})


@api_bp.route('/application_attempts/<attempt_id>/submit', methods=['POST'])
def submit_application(attempt_id):
    user, err = _application_user()
    if err:
        return err
    if not current_app.config['APPLICATION_SUBMIT_ENABLED']:
        return _fail('Application submission is disabled until DEV acceptance', 409)
    db = request_db()
    data = _body()
    if not auto_apply.consume_confirmation_token(
            db, user['id'], attempt_id, data.get('confirmation_token')):
        return _fail('Final confirmation is missing, expired, or already used', 409)
    attempt = auto_apply.get_attempt(db, user['id'], attempt_id)
    if not attempt:
        return _fail('Application attempt not found', 404)
    try:
        attempt = auto_apply.transition(
            db, user['id'], attempt_id, 'submitting',
            'Submitting after explicit confirmation', automation_used=True)
        result = _worker_request('submit', user['id'], attempt,
                                 {'confirmed': True})
        state = 'submitted' if result.get('state') == 'submitted' else 'submission_failed'
        db.execute(
            'UPDATE application_attempts SET confirmation_url=?,confirmation_text=? '
            'WHERE id=? AND user_id=?',
            (result.get('confirmation_url'),
             str(result.get('confirmation_text') or '')[:1000], attempt_id, user['id']),
        )
        db.commit()
        attempt = auto_apply.transition(
            db, user['id'], attempt_id, state,
            result.get('message') or auto_apply.ATTEMPT_STATES[state],
            result.get('error_code'), result.get('metadata'), automation_used=True)
        if state == 'submitted':
            tracker_row = db.execute(
                'SELECT record_json FROM application_records '
                'WHERE user_id=? AND job_key=?',
                (user['id'], attempt['job_key']),
            ).fetchone()
            tracker = (json.loads(tracker_row['record_json']) if tracker_row else
                       dict(attempt.get('job') or {}))
            tracker.update({
                'status': 'applied', 'stage': 'applied',
                'applied_date': datetime.utcnow().date().isoformat(),
                'application_attempt_id': attempt_id,
                'submitted_resume_id': attempt.get('selected_resume_id'),
                'submitted_cover_id': attempt.get('selected_cover_id'),
                'updated_at': datetime.utcnow().isoformat(),
            })
            db.execute(
                'INSERT INTO application_records '
                '(user_id,job_key,record_json,updated_at) VALUES (?,?,?,?) '
                'ON CONFLICT(user_id,job_key) DO UPDATE SET '
                'record_json=excluded.record_json,updated_at=excluded.updated_at',
                (user['id'], attempt['job_key'],
                 json.dumps(tracker, ensure_ascii=False), tracker['updated_at']),
            )
            db.commit()
    except (RuntimeError, ValueError) as exc:
        attempt = auto_apply.transition(
            db, user['id'], attempt_id, 'submission_failed', str(exc),
            'worker_error', automation_used=True)
        return jsonify({'success': False, 'attempt': attempt, 'error': str(exc)}), 502
    return jsonify({'success': True, 'attempt': attempt})


@api_bp.route('/application_worker/callback', methods=['POST'])
def application_worker_callback():
    token = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    try:
        signed = _worker_serializer().loads(token, max_age=1800)
    except SignatureExpired:
        return _fail('Worker token expired', 401)
    except BadSignature:
        return _fail('Invalid worker token', 401)
    data = _body()
    if str(data.get('attempt_id')) != str(signed.get('attempt_id')):
        return _fail('Worker token does not match this attempt', 403)
    state = data.get('state')
    try:
        attempt = auto_apply.transition(
            request_db(), int(signed['user_id']), signed['attempt_id'], state,
            data.get('message'), data.get('error_code'), data.get('metadata'),
            automation_used=True)
    except (KeyError, TypeError, ValueError) as exc:
        return _fail(str(exc), 400)
    except LookupError as exc:
        return _fail(str(exc), 404)
    return jsonify({'success': True, 'attempt_id': attempt['id'], 'state': attempt['state']})


# ── triage / tracker / views ─────────────────────────────────────────────────

@api_bp.route('/job_triage')
def load_triage():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    triage = profile_store.load_triage(key)
    jobs = triage.setdefault('jobs', {})
    changed = False
    for record in jobs.values():
        nested = record.pop('job', None)
        if isinstance(nested, dict):
            for field, value in nested.items():
                if value not in (None, '') and record.get(field) in (None, ''):
                    record[field] = value
            changed = True
        status = record.get('status') or 'saved'
        stage = ('not_interested' if status == 'not_interested'
                 else record.get('stage') or status)
        if record.get('stage') != stage:
            record['stage'] = stage
            changed = True
        if stage in TRACKER_STAGES - {'saved'} and not record.get('applied_date'):
            record['applied_date'] = str(record.get('updated_at') or datetime.utcnow().isoformat())[:10]
            changed = True
    if changed:
        profile_store.save_triage(key, triage)
    return jsonify({'success': True, 'triage': triage})


@api_bp.route('/job_triage', methods=['POST'])
def save_triage():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    job_key, status = data.get('job_key'), data.get('status')
    if not job_key or status not in TRIAGE_STATUSES:
        return _fail('Invalid triage request', 400)
    triage = profile_store.load_triage(key)
    jobs = triage.setdefault('jobs', {})
    record = None
    if status == 'none':
        jobs.pop(str(job_key), None)
    else:
        record = {} if data.get('remove_tracker') else jobs.get(str(job_key), {})
        record['status'] = status
        record['updated_at'] = datetime.utcnow().isoformat()
        if isinstance(data.get('job'), dict):
            for field in ('title', 'company', 'url', 'apply_url', 'source', 'location',
                          'remote_status', 'salary', 'score', 'posted', 'posted_at',
                          'search_role', 'search_role_color'):
                value = data['job'].get(field)
                if value not in (None, ''):
                    if field in ('search_role', 'search_role_color') and record.get(field):
                        continue
                    record[field] = value
        record.pop('job', None)
        if status == 'applied':
            record['stage'] = 'applied'
            record.setdefault('applied_date', datetime.now().date().isoformat())
        elif status == 'not_interested':
            record['stage'] = 'not_interested'
        elif status == 'saved':
            record['stage'] = 'saved'
        jobs[str(job_key)] = record
    profile_store.save_triage(key, triage)
    return jsonify({'success': True, 'triage': triage, 'record': record})


@api_bp.route('/application_tracker', methods=['POST'])
def application_tracker():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    job_key = data.get('job_key')
    if not job_key:
        return _fail('Missing job_key', 400)
    stage = data.get('stage')
    if stage is not None and stage not in TRACKER_STAGES:
        return _fail('Invalid stage', 400)
    triage = profile_store.load_triage(key)
    jobs = triage.setdefault('jobs', {})
    record = jobs.get(str(job_key))
    if record is None:
        return _fail('Save the job before editing its application details', 404)
    if stage:
        history = record.setdefault('stage_history', [])
        if record.get('stage') != stage:
            history.append({'stage': stage, 'at': datetime.utcnow().isoformat()})
        record['stage'] = stage

    # Job-detail fields are optional and only touched when the caller sends
    # them, so this same route serves both the notes/stage-only quick save
    # and the full details editor without clobbering fields the editor
    # didn't show.
    if 'url' in data:
        supplied_url = str(data.get('url') or '').strip()
        if supplied_url:
            normalized = manual_jobs.normalize_url(supplied_url)
            if not normalized:
                return _fail('That does not look like a web address', 400)
            record['url'] = normalized
    for field, limit in (('title', 300), ('company', 300), ('location', 200),
                          ('apply_url', 2000), ('posted_at', 40)):
        if field in data:
            value = str(data.get(field) or '').strip()[:limit]
            if value:
                record[field] = value
    if 'remote_status' in data:
        remote = str(data.get('remote_status') or '').strip().lower()
        if remote in REMOTE_STATUSES:
            record['remote_status'] = 'local' if remote == 'onsite' else remote
    if 'salary' in data:
        raw_salary = str(data.get('salary') or '').strip()
        if not raw_salary:
            record['salary'] = None
        else:
            try:
                salary = float(raw_salary.replace(',', '').replace('$', ''))
                record['salary'] = salary if salary > 0 else None
            except ValueError:
                return _fail('Salary must be a number', 400)
    if 'search_role' in data:
        role_name = str(data.get('search_role') or '').strip()[:120]
        if role_name:
            role_color = str(data.get('search_role_color') or '').strip()
            record['search_role'] = role_name
            record['search_role_color'] = (role_color if HEX_COLOR.match(role_color)
                                           else profile_store.DEFAULT_ROLE_COLOR)

    if 'applied_at' in data and 'applied_date' not in data:
        data['applied_date'] = data['applied_at']
    for field in ('notes', 'applied_date', 'follow_up_date'):
        if field in data:
            record[field] = data[field]
    current_stage = record.get('stage') or record.get('status') or 'saved'
    record['status'] = 'saved' if current_stage == 'saved' else 'applied'
    if current_stage != 'saved' and not record.get('applied_date'):
        record['applied_date'] = datetime.now().date().isoformat()
    if current_stage != 'saved':
        user = current_user()
        attempt = request_db().execute(
            'SELECT id,selected_resume_id,selected_cover_id FROM application_attempts '
            'WHERE user_id=? AND job_key=? ORDER BY updated_at DESC LIMIT 1',
            (user['id'], str(job_key))).fetchone()
        if attempt and attempt['selected_resume_id']:
            record['application_attempt_id'] = attempt['id']
            record['submitted_resume_id'] = attempt['selected_resume_id']
            record['submitted_cover_id'] = attempt['selected_cover_id']
            record.setdefault('document_snapshot_at', datetime.utcnow().isoformat())
    record['updated_at'] = datetime.utcnow().isoformat()
    jobs[str(job_key)] = record
    profile_store.save_triage(key, triage)
    return jsonify({'success': True, 'record': record})


@api_bp.route('/application_tracker/note', methods=['POST'])
def application_tracker_note():
    """Append a dated note to a tracked job instead of overwriting the field.

    Kept separate from application_tracker so the notes history can grow by
    appending entries; that route still owns the legacy single-string 'notes'
    field for backward compatibility with older records and callers.
    """
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    job_key = data.get('job_key')
    if not job_key:
        return _fail('Missing job_key', 400)
    text = str(data.get('text') or '').strip()[:NOTE_TEXT_LIMIT]
    if not text:
        return _fail('Enter a note before saving', 400)
    triage = profile_store.load_triage(key)
    jobs = triage.setdefault('jobs', {})
    record = jobs.get(str(job_key))
    if record is None:
        return _fail('Save the job before adding notes', 404)
    entry = {'text': text, 'at': datetime.utcnow().isoformat()}
    record.setdefault('notes_log', []).append(entry)
    record['updated_at'] = entry['at']
    jobs[str(job_key)] = record
    profile_store.save_triage(key, triage)
    return jsonify({'success': True, 'record': record, 'entry': entry})


@api_bp.route('/manual_jobs/preview', methods=['POST'])
def manual_job_preview():
    """Read a posting URL and report what could be extracted from it.

    Read-only on purpose: nothing is stored until the user has seen the values
    and pressed Add. Extraction is best-effort, so a page that yields nothing
    is a 200 with warnings, not an error -- the user can still type the title
    and company by hand. Only a malformed, unreachable, or LAN-facing URL is a
    4xx.
    """
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    url = str(data.get('url') or '').strip()
    if not url:
        return _fail('Enter the job posting address', 400)
    try:
        preview = manual_jobs.extract(url)
    except manual_jobs.ManualJobError as error:
        return _fail(str(error), 400)
    except Exception:
        current_app.logger.exception('Manual job preview failed')
        return _fail('That page could not be read. You can still enter the '
                     'job details by hand.', 502)
    return jsonify({'success': True, 'url': preview['url'],
                    'fields': preview['fields'],
                    'provenance': preview['provenance'],
                    'warnings': preview['warnings']})


@api_bp.route('/manual_jobs', methods=['POST'])
def manual_job_create():
    """Add a job the user applied for outside JobSearch to the tracker.

    A manual add is authoritative. If a record already exists for this job --
    including one the user previously dismissed as Not interested -- the stage
    and status chosen here replace it outright, and the job returns to the
    tracker. Notes, stage history and any document selections survive; the
    dismissal does not.

    No match score is written. Scoring needs sentence-transformers, which lives
    only on the pipeline interpreter, and a fabricated score would be worse
    than an empty one.
    """
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err

    supplied_url = str(data.get('url') or '').strip()
    url = manual_jobs.normalize_url(supplied_url) if supplied_url else ''
    if supplied_url and not url:
        return _fail('That does not look like a web address', 400)
    title = str(data.get('title') or '').strip()[:300]
    company = str(data.get('company') or '').strip()[:300]
    if not url and not (title and company):
        return _fail('Enter the posting address, or both a job title and a '
                     'company name', 400)
    stage = data.get('stage')
    if stage not in TRACKER_STAGES:
        return _fail('Choose the stage this job is at', 400)

    # Key on the URL when there is one so this record collides with the same
    # posting seen by a scraper; fall back to the identity the pipeline itself
    # uses when there is not.
    job_key = profile_store.stable_job_key(url or '{}|{}'.format(title, company))
    existing = profile_store.load_record(key, job_key) or {}
    cleared_ignore = existing.get('status') == 'not_interested'

    now = datetime.utcnow().isoformat()
    record = dict(existing)
    record.update({
        'origin': 'manual',
        'stage': stage,
        'status': 'saved' if stage == 'saved' else 'applied',
        'updated_at': now,
    })
    record.setdefault('added_at', now)
    record.setdefault('score', None)
    record['source'] = str(existing.get('source') or 'Manual entry')[:80]
    if url:
        record['url'] = url
    if title:
        record['title'] = title
    if company:
        record['company'] = company

    for field, limit in (('location', 200), ('apply_url', 2000), ('posted_at', 40)):
        value = str(data.get(field) or '').strip()[:limit]
        if value:
            record[field] = value
    remote = str(data.get('remote_status') or '').strip().lower()
    if remote in REMOTE_STATUSES:
        record['remote_status'] = 'local' if remote == 'onsite' else remote
    else:
        record.setdefault('remote_status', 'unclear')
    try:
        salary = float(str(data.get('salary')).replace(',', '').replace('$', ''))
        record['salary'] = salary if salary > 0 else None
    except (TypeError, ValueError):
        record.setdefault('salary', None)

    role_name = str(data.get('search_role') or '').strip()[:120]
    role_color = str(data.get('search_role_color') or '').strip()
    if not role_name:
        config = profile_store.load_config(key) or {}
        role_name = str(config.get('active_search_preset_name') or 'Default')
        role_color = role_color or str(config.get('active_search_preset_color') or '')
    record['search_role'] = role_name
    record['search_role_color'] = (role_color if HEX_COLOR.match(role_color)
                                   else profile_store.DEFAULT_ROLE_COLOR)

    if 'notes' in data:
        record['notes'] = str(data.get('notes') or '')[:5000]
    for field in ('applied_date', 'follow_up_date'):
        if data.get(field):
            record[field] = str(data[field])[:10]
    if stage != 'saved' and not record.get('applied_date'):
        record['applied_date'] = datetime.now().date().isoformat()

    history = record.setdefault('stage_history', [])
    if existing.get('stage') != stage:
        history.append({'stage': stage, 'at': now})

    profile_store.upsert_record(key, job_key, record)
    return jsonify({'success': True, 'job_key': job_key, 'record': record,
                    'replaced': bool(existing), 'cleared_ignore': cleared_ignore})


@api_bp.route('/saved_views')
def load_saved_views():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    return jsonify({'success': True, 'saved_views': profile_store.load_saved_views(key)})


@api_bp.route('/saved_views', methods=['POST'])
def save_saved_views():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    views = data.get('saved_views')
    if not isinstance(views, dict) or not isinstance(views.get('views'), list):
        return _fail('Invalid saved views', 400)
    cleaned = [{'name': str(v['name'])[:120], 'filters': v.get('filters', {})}
               for v in views['views']
               if isinstance(v, dict) and v.get('name')]
    profile_store.save_saved_views(key, {'version': 1, 'views': cleaned})
    return jsonify({'success': True, 'saved_views': profile_store.load_saved_views(key)})


# ── results / history ────────────────────────────────────────────────────────

@api_bp.route('/latest_results')
def latest_results():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    return jsonify({'success': True, 'run': profile_store.latest_results(key)})


@api_bp.route('/search_history')
def search_history():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    return jsonify({'success': True, 'runs': profile_store.search_history(key)})


@api_bp.route('/decision_report')
def decision_report():
    key, err = _profile(request.args.get('user'))
    if err:
        return err
    run_id = request.args.get('run_id')
    try:
        run_id = int(run_id) if run_id else None
    except (TypeError, ValueError):
        return _fail('Invalid run_id', 400)
    return jsonify({
        'success': True,
        'report': profile_store.decision_report(key, run_id),
        'runs': profile_store.decision_report_history(key),
    })


@api_bp.route('/serpapi_status')
def serpapi_status():
    key, err = _profile()
    if err:
        return err
    config = profile_store.load_config(key) or {}
    entries = config.get('credentials', {}).get('serpapi', [])
    active = [entry for entry in entries
              if isinstance(entry, dict)
              and entry.get('name', '').lower() == 'active']
    api_key = active[0].get('key') if active else None
    keywords = [value for value in config.get('keywords', []) if str(value).strip()]
    def page_count(name):
        try:
            return max(0, int(config.get(name, 0)))
        except (TypeError, ValueError):
            return 0
    estimate = len(keywords) * (
        (page_count('serpapi_max_pages')
         if config.get('search_remote') else 0) +
        (page_count('serpapi_local_max_pages')
         if config.get('search_local') else 0)
    ) if config.get('sources', {}).get('serpapi') else 0
    from scrapers.adapter_registry import SERPAPI_FALLBACK_ADAPTERS
    priority_sources = profile_store.active_employer_sources(key, config)
    targeted_count = sum(1 for source in priority_sources
                         if source.get('adapter') in SERPAPI_FALLBACK_ADAPTERS)
    targeted_pages = max(1, min(3, page_count('priority_employer_serpapi_max_pages') or 1))
    if targeted_count and config.get('sources', {}).get('serpapi'):
        estimate += len(keywords) * targeted_count * targeted_pages * (
            int(bool(config.get('search_remote'))) + int(bool(config.get('search_local'))))
    if not api_key:
        return jsonify({
            'success': True, 'has_key': False, 'enabled': bool(
                config.get('sources', {}).get('serpapi')),
            'estimated_credits': estimate, 'searches_left': None,
            'this_month_usage': None, 'plan_name': None,
            'plan_renewal_date': None, 'searches_per_month': None,
            'priority_employer_fallbacks': targeted_count,
        })
    try:
        import urllib.request
        import urllib.parse
        url = 'https://serpapi.com/account.json?' + urllib.parse.urlencode(
            {'api_key': api_key})
        with urllib.request.urlopen(
                url, timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception:
        return _fail('Could not reach SerpAPI', 502)
    return jsonify({
        'success': True,
        'has_key': True,
        'enabled': bool(config.get('sources', {}).get('serpapi')),
        'estimated_credits': estimate,
        'searches_left': data.get('total_searches_left',
                                  data.get('plan_searches_left', 'N/A')),
        'this_month_usage': data.get('this_month_usage', 0),
        'plan_name': data.get('plan_name', 'Unknown'),
        'plan_renewal_date': data.get('plan_renewal_date'),
        'searches_per_month': data.get('searches_per_month'),
        'priority_employer_fallbacks': targeted_count,
    })


# ── run control ──────────────────────────────────────────────────────────────

@api_bp.route('/run', methods=['POST'])
def run():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    test_mode = bool(data.get('test_mode', False))

    resume = (profile_store.load_resume(key) or '').strip()
    if not resume or resume.lower().startswith("[replace this with the user's resume"):
        return _fail('Add your resume in Search setup before running a search. '
                     'It is required to calculate match scores.', 400)

    config = profile_store.load_config(key) or {}
    if not test_mode:
        from scrapers.adapter_registry import ADAPTERS, SERPAPI_FALLBACK_ADAPTERS
        direct_adapter_names = {adapter.name for adapter in ADAPTERS}
        sources = config.get('sources', {})
        priority_sources = profile_store.active_employer_sources(key, config)
        direct_sources = [item for item in priority_sources
                          if item.get('adapter') in direct_adapter_names]
        fallback_sources = [item for item in priority_sources
                            if item.get('adapter') in SERPAPI_FALLBACK_ADAPTERS]
        if not [n for n, v in sources.items() if v] and not direct_sources and not fallback_sources:
            return _fail('Enable at least one live job source in Search setup before '
                         'starting a live search. The built-in test does not need a '
                         'source.', 400)
        creds = config.get('credentials', {})
        has_serpapi_key = any(isinstance(i, dict) and i.get('key')
                              for i in creds.get('serpapi', []))
        if sources.get('serpapi') and not has_serpapi_key:
            return _fail('SerpAPI is enabled but has no API key. Add one under Search '
                         'setup → Scraper credentials, or disable SerpAPI.', 400)
        if fallback_sources and (not sources.get('serpapi') or not has_serpapi_key):
            if not direct_sources and not [name for name, enabled in sources.items()
                                           if enabled and name != 'serpapi']:
                return _fail('A company-name priority employer needs enabled SerpAPI '
                             'with an active API key, or add a supported careers URL.', 400)
        if sources.get('adzuna') and not any(
                isinstance(i, dict) and i.get('app_id') and i.get('app_key')
                for i in creds.get('adzuna', [])):
            return _fail('Adzuna is enabled but needs both an App ID and App Key. Add '
                         'them under Search setup → Scraper credentials, or disable '
                         'Adzuna.', 400)

    log_dir = os.path.join(BASE_DIR, 'logs', key)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'run_job_search.log')

    env = dict(os.environ)
    env.update(PIPELINE_ENV)
    env['JOBSEARCH_ENV'] = current_app.config.get('JOBSEARCH_ENV', 'development')

    cmd = [PIPELINE_PYTHON, 'main.py', key] + (['--test-mode'] if test_mode else [])
    try:
        # Output goes straight to a file, never a pipe. Piping stdout back here
        # deadlocked the old handler at ~200 jobs when the 64KB buffer filled
        # and nothing drained it. start_new_session detaches the run so a
        # Gunicorn restart during a deploy cannot kill an in-flight search.
        with open(log_path, 'a') as log:
            process = subprocess.Popen(
                cmd, cwd=BASE_DIR, env=env,
                stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
    except Exception:
        current_app.logger.exception('failed to start search')
        return _fail('Could not start the search', 500)

    profile_store.write_run_status(key, test_mode, 'running', 'Job started', process.pid)
    return jsonify({'success': True, 'job_started': True, 'pid': process.pid,
                    'message': f'Job search started for {key}'})


@api_bp.route('/job_status', methods=['POST'])
def job_status():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    test_mode = bool(data.get('test_mode', False))
    status = profile_store.latest_run_status(key, test_mode)

    # A crashed pipeline subprocess never gets to write its own terminal
    # status, so a bad launch (wrong interpreter, broken PYTHONPATH, OOM,
    # disk full) leaves the row stuck at "running" forever and portal.js
    # polls it indefinitely. Caught here, at 3s poll cadence, rather than
    # with a fixed wait in /run: some of these failures (e.g. importing
    # sentence-transformers off a bad PYTHONPATH) take 10+ seconds to surface
    # — too slow to hold that request open for.
    pid = status.get('pid')
    if status.get('state') == 'running' and pid and not _process_alive(pid):
        tail = _log_tail(key)
        message = ('The search process stopped unexpectedly.'
                   + (f' Last log output: {tail[-300:]}' if tail
                      else ' No log output was captured — check server logs.'))
        status, _ = profile_store.write_run_status(key, test_mode, 'error', message, pid)
        current_app.logger.error('search process %s died unexpectedly for %s: %s',
                                  pid, key, tail)

    return jsonify({'success': True, 'status': status})


@api_bp.route('/cancel', methods=['POST'])
def cancel():
    data = _body()
    key, err = _profile(data.get('user'))
    if err:
        return err
    test_mode = bool(data.get('test_mode', False))
    status = profile_store.latest_run_status(key, test_mode)
    pid, killed, kill_error = status.get('pid'), False, None
    if pid:
        try:
            # The run is its own process group (start_new_session), so this
            # takes the Python process and anything it spawned.
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            kill_error = 'Process already exited'
        except Exception as exc:
            kill_error = str(exc)
    # Recorded regardless of whether the kill landed, so a stale PID cannot
    # leave the UI showing a run that is no longer going anywhere.
    profile_store.write_run_status(key, test_mode, 'cancelled', 'Job cancelled by user')
    return jsonify({'success': True, 'killed': killed, 'kill_error': kill_error})
