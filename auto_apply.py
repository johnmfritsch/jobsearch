"""Durable, user-scoped state for the supervised application assistant."""

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


ATTEMPT_STATES = {
    'queued': 'Queued',
    'validating_url': 'Checking URL',
    'url_invalid': 'URL invalid',
    'preparing_documents': 'Preparing documents',
    'awaiting_document_approval': 'Awaiting document approval',
    'analyzing_form': 'Analyzing application',
    'needs_user_input': 'Needs answers',
    'needs_login': 'Needs login',
    'blocked_captcha': 'CAPTCHA or manual action required',
    'ready_for_review': 'Application filled and ready for review',
    'awaiting_final_confirmation': 'Awaiting final confirmation',
    'submitting': 'Submitting',
    'submitted': 'Submitted',
    'could_not_fill': 'Could not fill application',
    'submission_failed': 'Submission failed',
    'manual_required': 'Manual application required',
    'unsupported_site': 'Unsupported application site',
    'cancelled': 'Cancelled',
}

TERMINAL_STATES = {
    'url_invalid', 'submitted', 'submission_failed', 'manual_required',
    'unsupported_site', 'could_not_fill', 'cancelled',
}

ACTIVE_STATES = ATTEMPT_STATES.keys() - TERMINAL_STATES

ALLOWED_TRANSITIONS = {
    'queued': {'validating_url', 'preparing_documents', 'cancelled'},
    'validating_url': {'preparing_documents', 'analyzing_form', 'url_invalid',
                       'manual_required', 'unsupported_site', 'cancelled'},
    'preparing_documents': {'awaiting_document_approval', 'ready_for_review',
                            'could_not_fill', 'cancelled'},
    'awaiting_document_approval': {'preparing_documents', 'analyzing_form',
                                   'ready_for_review', 'cancelled'},
    'analyzing_form': {'needs_user_input', 'needs_login', 'blocked_captcha',
                       'ready_for_review', 'manual_required', 'unsupported_site',
                       'could_not_fill', 'cancelled'},
    'needs_user_input': {'analyzing_form', 'ready_for_review', 'cancelled'},
    'needs_login': {'analyzing_form', 'manual_required', 'cancelled'},
    'blocked_captcha': {'analyzing_form', 'manual_required', 'cancelled'},
    'ready_for_review': {'preparing_documents', 'awaiting_final_confirmation',
                         'analyzing_form', 'cancelled'},
    'awaiting_final_confirmation': {'submitting', 'ready_for_review', 'cancelled'},
    'submitting': {'submitted', 'submission_failed'},
    'could_not_fill': {'analyzing_form', 'manual_required', 'cancelled'},
    'submission_failed': {'ready_for_review', 'cancelled'},
    'manual_required': set(),
    'unsupported_site': set(),
    'url_invalid': set(),
    'submitted': set(),
    'cancelled': set(),
}

PROFILE_FIELDS = {
    'full_name': 160,
    'email': 254,
    'phone': 80,
    'address': 300,
    'city': 120,
    'state': 120,
    'postal_code': 30,
    'country': 80,
    'linkedin_url': 500,
    'portfolio_url': 500,
    'work_authorization': 300,
    'sponsorship': 300,
    'relocation': 300,
    'travel': 300,
    'salary_preference': 160,
    'additional_facts': 4000,
}

ATS_HOSTS = (
    ('greenhouse', ('greenhouse.io', 'boards.greenhouse.io')),
    ('lever', ('lever.co', 'jobs.lever.co')),
    ('ashby', ('ashbyhq.com', 'jobs.ashbyhq.com')),
    ('zoho_recruit', ('zohorecruit.com',)),
    ('workday', ('myworkdayjobs.com', 'workday.com')),
    ('linkedin', ('linkedin.com',)),
)

AGGREGATOR_HOSTS = (
    'ziprecruiter.com', 'indeed.com', 'glassdoor.com', 'tealhq.com',
    'google.com', 'linkedin.com',
)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _user_job(db, user_id, job_key):
    row = db.execute(
        'SELECT jr.job_json FROM job_results jr '
        'JOIN search_runs sr ON sr.id=jr.run_id '
        'WHERE sr.user_id=? AND jr.job_key=? '
        'ORDER BY jr.id DESC LIMIT 1',
        (user_id, str(job_key)),
    ).fetchone()
    if row:
        return _json(row['job_json'], {})
    row = db.execute(
        'SELECT record_json FROM application_records '
        'WHERE user_id=? AND job_key=?',
        (user_id, str(job_key)),
    ).fetchone()
    return _json(row['record_json'], {}) if row else None


def detect_ats(url):
    host = (urlparse(str(url or '')).hostname or '').lower()
    for kind, suffixes in ATS_HOSTS:
        if any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes):
            return kind
    return 'unknown'


def valid_public_url(url):
    parsed = urlparse(str(url or ''))
    return parsed.scheme in {'http', 'https'} and bool(parsed.hostname)


def application_url_kind(source_url, resolved_url):
    url = str(resolved_url or source_url or '')
    host = (urlparse(url).hostname or '').lower()
    if any(host == item or host.endswith('.' + item) for item in AGGREGATOR_HOSTS):
        return 'job_listing'
    return 'application_page' if valid_public_url(url) else 'unavailable'


def _event(db, attempt_id, state, message, metadata=None):
    db.execute(
        'INSERT INTO application_events '
        '(attempt_id,state,message,metadata_json,created_at) VALUES (?,?,?,?,?)',
        (attempt_id, state, str(message)[:1000],
         json.dumps(metadata or {}, ensure_ascii=False), utcnow()),
    )


def _record_dict(row):
    if not row:
        return None
    value = dict(row)
    value['job'] = _json(value.pop('job_json'), {})
    value['answers'] = _json(value.pop('answers_json'), {})
    value['automation_used'] = bool(value.get('automation_used'))
    value['state_label'] = ATTEMPT_STATES.get(value['state'], value['state'])
    value['application_url_kind'] = application_url_kind(
        value.get('source_url'), value.get('resolved_url'))
    return value


def get_attempt(db, user_id, attempt_id, include_details=True):
    row = db.execute(
        'SELECT * FROM application_attempts WHERE id=? AND user_id=?',
        (str(attempt_id), user_id),
    ).fetchone()
    result = _record_dict(row)
    if not result or not include_details:
        return result
    result['events'] = [
        dict(item, metadata=_json(item['metadata_json'], {}))
        for item in db.execute(
            'SELECT id,state,message,metadata_json,created_at '
            'FROM application_events WHERE attempt_id=? ORDER BY id',
            (str(attempt_id),),
        )
    ]
    for item in result['events']:
        item.pop('metadata_json', None)
        item['state_label'] = ATTEMPT_STATES.get(item['state'], item['state'])
    result['artifacts'] = [
        dict(item) for item in db.execute(
            'SELECT id,kind,format,file_name,mime_type,approval_state,version,'
            'provider,model,input_tokens,output_tokens,estimated_cost_usd,actual_cost_usd,'
            'generated_at,approved_at FROM application_artifacts '
            'WHERE attempt_id=? AND user_id=? ORDER BY kind,version DESC,format',
            (str(attempt_id), user_id),
        )
    ]
    for artifact in result['artifacts']:
        artifact['selected_for_submission'] = artifact['id'] in {
            result.get('selected_resume_id'), result.get('selected_cover_id')}
    return result


def list_attempts(db, user_id, limit=100):
    rows = db.execute(
        'SELECT * FROM application_attempts WHERE user_id=? '
        'ORDER BY updated_at DESC LIMIT ?',
        (user_id, max(1, min(int(limit), 250))),
    ).fetchall()
    return [_record_dict(row) for row in rows]


def create_attempt(db, user_id, job_key):
    job_key = str(job_key or '').strip()
    if not job_key:
        raise ValueError('Choose a job to apply for')
    existing = db.execute(
        'SELECT id FROM application_attempts WHERE user_id=? AND job_key=? '
        'AND state NOT IN (?,?,?,?,?,?) ORDER BY updated_at DESC LIMIT 1',
        (user_id, job_key, 'url_invalid', 'manual_required', 'unsupported_site',
         'could_not_fill', 'cancelled', 'submission_failed'),
    ).fetchone()
    if existing:
        return get_attempt(db, user_id, existing['id']), False
    job = _user_job(db, user_id, job_key)
    if not job:
        raise LookupError('That job is not available in your results or tracker')
    source_url = job.get('apply_url') or job.get('url') or ''
    state = 'queued' if valid_public_url(source_url) else 'url_invalid'
    message = (ATTEMPT_STATES[state] if source_url else
               'This result does not contain an application URL')
    attempt_id = str(uuid.uuid4())
    now = utcnow()
    db.execute(
        'INSERT INTO application_attempts '
        '(id,user_id,job_key,job_json,source_url,resolved_url,ats_kind,state,'
        'status_message,error_code,automation_used,answers_json,created_at,updated_at,'
        'completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (attempt_id, user_id, job_key, json.dumps(job, ensure_ascii=False),
         source_url, source_url, detect_ats(source_url), state, message,
         'missing_or_invalid_url' if state == 'url_invalid' else None, 0, '{}',
         now, now, now if state == 'url_invalid' else None),
    )
    _event(db, attempt_id, state, message, {'automation_used': False})
    existing_tracker = db.execute(
        'SELECT record_json FROM application_records WHERE user_id=? AND job_key=?',
        (user_id, job_key),
    ).fetchone()
    tracker = dict(job)
    if existing_tracker:
        # Preserve notes, dates, and an advanced tracker stage while refreshing
        # the job metadata from the latest result.
        tracker.update(_json(existing_tracker['record_json'], {}))
    tracker.update({'job_key': job_key, 'application_attempt_id': attempt_id,
                    'updated_at': now})
    tracker.setdefault('status', 'saved')
    tracker.setdefault('stage', tracker.get('status') or 'saved')
    db.execute(
        'INSERT INTO application_records (user_id,job_key,record_json,updated_at) '
        'VALUES (?,?,?,?) ON CONFLICT(user_id,job_key) DO UPDATE SET '
        'record_json=excluded.record_json,updated_at=excluded.updated_at',
        (user_id, job_key, json.dumps(tracker, ensure_ascii=False), now),
    )
    db.commit()
    return get_attempt(db, user_id, attempt_id), True


def transition(db, user_id, attempt_id, state, message=None, error_code=None,
               metadata=None, automation_used=None, force=False):
    if state not in ATTEMPT_STATES:
        raise ValueError('Invalid application state')
    current = get_attempt(db, user_id, attempt_id, include_details=False)
    if not current:
        raise LookupError('Application attempt not found')
    if not force and state != current['state'] and state not in ALLOWED_TRANSITIONS[current['state']]:
        raise ValueError(f"Cannot move from {current['state']} to {state}")
    now = utcnow()
    final_message = str(message or ATTEMPT_STATES[state])[:1000]
    used = current['automation_used'] if automation_used is None else bool(automation_used)
    db.execute(
        'UPDATE application_attempts SET state=?,status_message=?,error_code=?,'
        'automation_used=?,updated_at=?,completed_at=? WHERE id=? AND user_id=?',
        (state, final_message, error_code, int(used), now,
         now if state in TERMINAL_STATES else None, str(attempt_id), user_id),
    )
    _event(db, str(attempt_id), state, final_message, metadata)
    db.commit()
    return get_attempt(db, user_id, attempt_id)


def retry_attempt(db, user_id, attempt_id):
    previous = get_attempt(db, user_id, attempt_id, include_details=False)
    if not previous:
        raise LookupError('Application attempt not found')
    if previous['state'] == 'submitted':
        raise ValueError('A submitted application cannot be retried')
    if previous['state'] not in TERMINAL_STATES | {'could_not_fill'}:
        return get_attempt(db, user_id, attempt_id), False
    return create_attempt(db, user_id, previous['job_key'])


def save_answers(db, user_id, attempt_id, answers):
    if not isinstance(answers, dict):
        raise ValueError('Answers must be an object')
    cleaned = {}
    for key, value in answers.items():
        name = str(key).strip()[:160]
        if not name:
            continue
        if isinstance(value, bool):
            cleaned[name] = value
        else:
            cleaned[name] = str(value).strip()[:4000]
    now = utcnow()
    result = db.execute(
        'UPDATE application_attempts SET answers_json=?,updated_at=? '
        'WHERE id=? AND user_id=?',
        (json.dumps(cleaned, ensure_ascii=False), now, str(attempt_id), user_id),
    )
    if not result.rowcount:
        raise LookupError('Application attempt not found')
    _event(db, str(attempt_id), 'needs_user_input', 'Application answers saved',
           {'answer_count': len(cleaned)})
    db.commit()
    return get_attempt(db, user_id, attempt_id)


def load_profile(db, user_id):
    row = db.execute(
        'SELECT profile_json,updated_at FROM application_profiles WHERE user_id=?',
        (user_id,),
    ).fetchone()
    return {'profile': _json(row['profile_json'], {}) if row else {},
            'updated_at': row['updated_at'] if row else None}


def save_profile(db, user_id, profile):
    if not isinstance(profile, dict):
        raise ValueError('Application profile must be an object')
    cleaned = {}
    for field, limit in PROFILE_FIELDS.items():
        value = profile.get(field)
        if value is not None:
            cleaned[field] = str(value).strip()[:limit]
    now = utcnow()
    db.execute(
        'INSERT INTO application_profiles (user_id,profile_json,updated_at) '
        'VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET '
        'profile_json=excluded.profile_json,updated_at=excluded.updated_at',
        (user_id, json.dumps(cleaned, ensure_ascii=False), now),
    )
    db.commit()
    return {'profile': cleaned, 'updated_at': now}


def next_artifact_version(db, attempt_id, kind):
    row = db.execute(
        'SELECT COALESCE(MAX(version),0)+1 AS version FROM application_artifacts '
        'WHERE attempt_id=? AND kind=?', (str(attempt_id), kind),
    ).fetchone()
    return int(row['version'])


def add_artifact(db, user_id, attempt_id, kind, file_format, file_name,
                 relative_path, sha256, mime_type, version, usage,
                 approval_state='pending'):
    artifact_id = str(uuid.uuid4())
    db.execute(
        'INSERT INTO application_artifacts '
        '(id,attempt_id,user_id,kind,format,file_name,relative_path,sha256,'
        'mime_type,approval_state,version,provider,model,input_tokens,output_tokens,'
        'estimated_cost_usd,actual_cost_usd,generated_at,approved_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (artifact_id, str(attempt_id), user_id, kind, file_format, file_name,
         relative_path, sha256, mime_type, approval_state, version,
         str(usage.get('provider', '')), str(usage.get('model', '')),
         int(usage.get('input_tokens', 0)), int(usage.get('output_tokens', 0)),
         float(usage.get('estimated_cost_usd', 0)),
         float(usage.get('actual_cost_usd', 0)), utcnow(),
         utcnow() if approval_state == 'approved' else None),
    )
    return artifact_id


def approve_artifact(db, user_id, artifact_id, decision):
    if decision not in {'approved', 'rejected'}:
        raise ValueError('Decision must be approved or rejected')
    row = db.execute(
        'SELECT * FROM application_artifacts WHERE id=? AND user_id=?',
        (str(artifact_id), user_id),
    ).fetchone()
    if not row:
        raise LookupError('Document not found')
    now = utcnow()
    # DOCX and PDF are two renderings of the same approved content version.
    # One decision applies to the pair so the user is not asked to approve the
    # same words twice merely because two file formats are available.
    db.execute(
        'UPDATE application_artifacts SET approval_state=?,approved_at=? '
        'WHERE attempt_id=? AND user_id=? AND kind=? AND version=?',
        (decision, now if decision == 'approved' else None,
         row['attempt_id'], user_id, row['kind'], row['version']),
    )
    if decision == 'approved':
        column = 'selected_resume_id' if row['kind'] == 'resume' else 'selected_cover_id'
        if row['kind'] in {'resume', 'cover_letter'}:
            selected_file = db.execute(
                'SELECT id FROM application_artifacts WHERE attempt_id=? AND user_id=? '
                'AND kind=? AND version=? AND format=?',
                (row['attempt_id'], user_id, row['kind'], row['version'], 'pdf'),
            ).fetchone()
            db.execute(
                f'UPDATE application_attempts SET {column}=?,updated_at=? '
                'WHERE id=? AND user_id=?',
                ((selected_file['id'] if selected_file else str(artifact_id)), now,
                 row['attempt_id'], user_id),
            )
    elif row['kind'] in {'resume', 'cover_letter'}:
        column = 'selected_resume_id' if row['kind'] == 'resume' else 'selected_cover_id'
        selected_row = db.execute(
            f'SELECT {column} AS selected_id FROM application_attempts '
            'WHERE id=? AND user_id=?',
            (row['attempt_id'], user_id)).fetchone()
        rejected_ids = {item['id'] for item in db.execute(
            'SELECT id FROM application_artifacts WHERE attempt_id=? AND user_id=? '
            'AND kind=? AND version=?',
            (row['attempt_id'], user_id, row['kind'], row['version']))}
        if selected_row and selected_row['selected_id'] in rejected_ids:
            db.execute(
                f'UPDATE application_attempts SET {column}=NULL,updated_at=? '
                'WHERE id=? AND user_id=?',
                (now, row['attempt_id'], user_id),
            )
    _event(db, row['attempt_id'], 'awaiting_document_approval',
           f"{row['kind'].replace('_', ' ').title()} {decision}",
           {'artifact_id': str(artifact_id), 'format': row['format']})
    db.commit()
    selected = db.execute(
        'SELECT selected_resume_id,selected_cover_id FROM application_attempts '
        'WHERE id=? AND user_id=?', (row['attempt_id'], user_id),
    ).fetchone()
    pending_document = db.execute(
        'SELECT 1 FROM application_artifacts WHERE attempt_id=? AND user_id=? '
        "AND kind IN ('resume','cover_letter') AND approval_state=? LIMIT 1",
        (row['attempt_id'], user_id, 'pending')).fetchone()
    if selected and selected['selected_resume_id'] and not pending_document:
        return transition(
            db, user_id, row['attempt_id'], 'ready_for_review',
            ('Resume and cover letter approved; review what will be submitted'
             if selected['selected_cover_id'] else
             'Resume approved; no cover letter is selected'))
    return get_attempt(db, user_id, row['attempt_id'])


def artifact_for_download(db, user_id, artifact_id):
    return db.execute(
        'SELECT * FROM application_artifacts WHERE id=? AND user_id=?',
        (str(artifact_id), user_id),
    ).fetchone()


def create_confirmation_token(db, user_id, attempt_id, lifetime_minutes=10):
    attempt = get_attempt(db, user_id, attempt_id, include_details=False)
    if not attempt:
        raise LookupError('Application attempt not found')
    if attempt['state'] != 'ready_for_review':
        raise ValueError('The application must be ready for review first')
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=lifetime_minutes)).replace(
        tzinfo=None, microsecond=0).isoformat()
    db.execute(
        'DELETE FROM application_confirmation_tokens '
        'WHERE attempt_id=? AND user_id=? AND used_at IS NULL',
        (str(attempt_id), user_id),
    )
    db.execute(
        'INSERT INTO application_confirmation_tokens '
        '(token_hash,attempt_id,user_id,expires_at) VALUES (?,?,?,?)',
        (digest, str(attempt_id), user_id, expires),
    )
    db.commit()
    transition(db, user_id, attempt_id, 'awaiting_final_confirmation',
               'Final submission confirmation requested')
    return raw, expires


def consume_confirmation_token(db, user_id, attempt_id, token):
    digest = hashlib.sha256(str(token or '').encode()).hexdigest()
    row = db.execute(
        'SELECT * FROM application_confirmation_tokens '
        'WHERE token_hash=? AND attempt_id=? AND user_id=?',
        (digest, str(attempt_id), user_id),
    ).fetchone()
    if not row or row['used_at'] or row['expires_at'] < utcnow():
        return False
    db.execute(
        'UPDATE application_confirmation_tokens SET used_at=? WHERE token_hash=?',
        (utcnow(), digest),
    )
    db.commit()
    return True


def artifact_root(database_path):
    root = Path(database_path).resolve().parent / 'application_artifacts'
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_artifact_path(root, relative_path):
    root = Path(root).resolve()
    candidate = (root / str(relative_path)).resolve()
    if os.path.commonpath((str(root), str(candidate))) != str(root):
        raise ValueError('Invalid document path')
    return candidate


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
