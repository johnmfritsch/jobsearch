"""Apply queue and desktop-agent operations (backlog 19.2).

The server never opens a browser and never submits anything. A desktop Claude
session claims a queued attempt, works the page in the user's own browser, and
reports back; this module records those facts. Every function takes the
request's connection and acts only for `user_id`, which comes from the key row
or the session, never from a request body.

Lease rule: `claim_id` is a plain UUID kept after an outcome so a client that
retries a lost response still matches. Whether a claim is live is decided by
state (analyzing_form, ready_for_review, awaiting_final_confirmation,
submitting), never by claim_id being non-null.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import auto_apply
import question_bank
import role_documents
import role_history
import tracker_sync

LEASE_HOURS = 2
LIVE_STATES = ('analyzing_form', 'ready_for_review', 'awaiting_final_confirmation', 'submitting')
SSN_SHAPE = re.compile(r'\b\d{3}-?\d{2}-?\d{4}\b')
PROGRESS_LIMIT = 300
SUMMARY_LIMIT = 8000
FIELD_LIMIT = 300
MAX_FIELDS = 80
NOTE_LIMIT = 300
OUTCOME_MESSAGE_LIMIT = 500

# agent outcome -> (final state, error_code)
OUTCOMES = {
    'needs_login': ('needs_login', 'needs_login'),
    'captcha': ('blocked_captcha', 'captcha'),
    'needs_answer': ('needs_user_input', 'needs_answer'),
    'manual': ('manual_required', 'manual_required'),
    'could_not_fill': ('could_not_fill', 'agent_could_not_fill'),
    'submitted': ('submitted', None),
    'submit_failed': ('submission_failed', 'agent_submit_failed'),
}
NO_LEASE = {'claimed_at': None, 'lease_expires_at': None}


class AgentError(ValueError):
    """A queue or agent rule was broken. Carries the HTTP status and a code."""

    def __init__(self, message, status=409, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def new_lease():
    return (datetime.now(timezone.utc) + timedelta(hours=LEASE_HOURS)).replace(
        tzinfo=None, microsecond=0).isoformat()


# ── session side: Apply for me ───────────────────────────────────────────────

def _tracker_role_name(db, user_id, job_key):
    row = db.execute('SELECT record_json FROM application_records WHERE user_id=? AND job_key=?',
                     (user_id, str(job_key))).fetchone()
    return str((auto_apply._json(row['record_json'], {}) or {}).get('search_role') or '').strip() if row else ''


def resolve_queue_role(db, user_id, job_key, job, role_key):
    """('default' | 'preset:<id>', display name). No silent Default fallback."""
    need = AgentError('Choose which role to apply under', 409, 'role_required')
    if role_key not in (None, ''):
        key = str(role_key).strip()
        if key == 'default':
            return 'default', 'Default'
        match = re.fullmatch(r'preset:(\d+)', key)
        if match:
            row = db.execute('SELECT id,name FROM search_presets WHERE id=? AND user_id=?',
                             (int(match.group(1)), user_id)).fetchone()
            if row:
                return key, row['name']
        raise need
    name = _tracker_role_name(db, user_id, job_key) or str(job.get('search_role') or '').strip()
    if not name or name.casefold() in ('before roles', 'na'):
        raise need
    role = role_documents.resolve_role(db, user_id, role_name=name)
    if role['role_key'] == 'default' or role['role_key'].startswith('preset:'):
        return role['role_key'], role['role_name']
    raise need


def queue_for_agent(db, user_id, job_key, role_key=None):
    """Queue a tracked job for the desktop agent. Returns (attempt, created)."""
    job_key = str(job_key or '').strip()
    if not job_key:
        raise AgentError('Choose a job to apply for', 400, 'job_required')
    job = auto_apply._user_job(db, user_id, job_key)
    if not job:
        raise LookupError('That job is not available in your results or tracker')
    role_key, role_name = resolve_queue_role(db, user_id, job_key, job, role_key)
    history = role_history.get_history(db, user_id, role_key)
    if not history.get('verified_at') or not history.get('work_history'):
        raise AgentError(
            f'Confirm work history for the {role_name} role first (Application assistant)',
            409, 'history_required')
    if not role_documents.resume_text_for_role_key(db, user_id, role_key):
        raise AgentError(f'The {role_name} role has no saved resume text', 409, 'resume_required')
    url = job.get('apply_url') or job.get('url') or ''
    if not auto_apply.valid_public_url(url):
        raise AgentError('This job has no usable application URL', 409, 'url_invalid')
    host = (urlparse(str(url)).hostname or '').lower()
    if auto_apply.detect_ats(url) == 'linkedin' or host == 'linkedin.com' or host.endswith('.linkedin.com'):
        raise AgentError('LinkedIn applications stay manual', 409, 'linkedin_refused')
    attempt, created = auto_apply.create_attempt(db, user_id, job_key, agent={'role_key': role_key})
    if created:
        return attempt, True
    state = attempt['state']
    if not attempt['agent_owned']:
        raise AgentError('This job already has an application in progress', 409, 'attempt_in_progress')
    if state == 'queued_for_agent':
        return attempt, False
    if state in ('needs_login', 'needs_user_input', 'blocked_captcha'):
        updated = auto_apply.transition(
            db, user_id, attempt['id'], 'queued_for_agent', 'Queued for agent again',
            allow_agent=True,
            extra_updates={'claim_id': None, 'role_key': role_key, **NO_LEASE})
        return updated, False
    if state == 'submitted':
        raise AgentError('This job was already submitted', 409, 'already_submitted')
    raise AgentError('This job already has an application in progress', 409, 'attempt_in_progress')


def list_agent_attempts(db, user_id):
    rows = db.execute(
        'SELECT id,job_json,state,updated_at FROM application_attempts '
        'WHERE user_id=? AND agent_owned=1 ORDER BY updated_at DESC LIMIT 200', (user_id,))
    items = []
    for row in rows:
        job = auto_apply._json(row['job_json'], {}) or {}
        items.append({'id': row['id'], 'title': job.get('title'), 'company': job.get('company'),
                      'state': row['state'],
                      'state_label': auto_apply.ATTEMPT_STATES.get(row['state'], row['state']),
                      'updated_at': row['updated_at']})
    waiting = sum(1 for item in items if item['state'] == 'queued_for_agent')
    return items, waiting


# ── agent side ───────────────────────────────────────────────────────────────

def claim_next(db, user_id):
    """Give the oldest queued attempt to the caller. None when nothing is queued.

    This is the first write on the connection: no BEGIN, the first UPDATE opens
    the transaction and commit() ends it. SQLite lets one writer commit, and
    every update names the state it expects, so two sessions have one winner.
    """
    now = auto_apply.utcnow()
    expired = [r['id'] for r in db.execute(
        "SELECT id FROM application_attempts WHERE user_id=? AND agent_owned=1 "
        "AND state='analyzing_form' AND lease_expires_at<?", (user_id, now))]
    for attempt_id in expired:
        changed = db.execute(
            "UPDATE application_attempts SET state='queued_for_agent',claim_id=NULL,"
            'claimed_at=NULL,lease_expires_at=NULL,status_message=?,completed_at=NULL,'
            "updated_at=? WHERE id=? AND user_id=? AND state='analyzing_form' "
            'AND lease_expires_at<?',
            ('Queued for agent', now, attempt_id, user_id, now)).rowcount
        if changed:
            auto_apply._event(db, attempt_id, 'queued_for_agent',
                              'Claim expired; queued for the agent again')
    claimed = None
    for _ in range(5):
        candidate = db.execute(
            "SELECT id FROM application_attempts WHERE user_id=? AND agent_owned=1 "
            "AND state='queued_for_agent' ORDER BY updated_at,id LIMIT 1",
            (user_id,)).fetchone()
        if not candidate:
            break
        claim_id = str(uuid.uuid4())
        won = db.execute(
            "UPDATE application_attempts SET state='analyzing_form',claim_id=?,claimed_at=?,"
            'lease_expires_at=?,status_message=?,completed_at=NULL,updated_at=? '
            "WHERE id=? AND user_id=? AND state='queued_for_agent'",
            (claim_id, now, new_lease(), 'Analyzing application', now,
             candidate['id'], user_id)).rowcount
        if won:
            auto_apply._event(db, candidate['id'], 'analyzing_form',
                              'Claimed by the desktop agent')
            claimed = candidate['id']
            break
        db.commit()  # lost the race; end any open transaction, then look again
    db.commit()
    return auto_apply.get_attempt(db, user_id, claimed, include_details=False) if claimed else None


def held(db, user_id, attempt_id, claim_id):
    """The attempt, if it is this user's, agent-owned and the claim matches."""
    attempt = auto_apply.get_attempt(db, user_id, attempt_id, include_details=False)
    if not attempt or not attempt['agent_owned']:
        raise LookupError('Application attempt not found')
    if not claim_id or str(claim_id) != str(attempt.get('claim_id') or ''):
        raise AgentError('This attempt is not held by that claim', 409, 'claim_mismatch')
    return attempt


def _require_state(attempt, states, action):
    if attempt['state'] not in states:
        raise AgentError(f"Cannot {action} while the attempt is {attempt['state']}", 409, 'wrong_state')


def _lease():
    return {'lease_expires_at': new_lease()}


def record_posting(db, user_id, attempt, payload, role_name):
    status = str(payload.get('status') or '').strip().lower()
    if status not in ('open', 'closed', 'unreadable'):
        raise AgentError('status must be open, closed or unreadable', 400, 'bad_request')
    now = auto_apply.utcnow()
    note = str(payload.get('note') or '')[:PROGRESS_LIMIT]
    if status == 'closed':
        if attempt['state'] == 'cancelled' and attempt.get('error_code') == 'posting_closed':
            tracker_sync.apply_agent_outcome(db, user_id, attempt, 'posting_closed', role_name)
            return attempt
        _require_state(attempt, ('analyzing_form', 'ready_for_review'), 'record the posting')
        updated = auto_apply.transition(
            db, user_id, attempt['id'], 'cancelled',
            'Posting closed before apply', 'posting_closed', {'note': note},
            allow_agent=True,
            extra_updates={'posting_status': 'closed', 'posting_checked_at': now, **NO_LEASE})
        tracker_sync.apply_agent_outcome(db, user_id, updated, 'posting_closed', role_name)
        return updated
    _require_state(attempt, ('analyzing_form', 'ready_for_review'), 'record the posting')
    resolved = str(payload.get('resolved_url') or '').strip()
    if resolved:
        host = (urlparse(resolved).hostname or '').lower()
        if not auto_apply.valid_public_url(resolved) or host == 'linkedin.com' \
                or host.endswith('.linkedin.com'):
            raise AgentError('resolved_url must be a public, non-LinkedIn web address', 400, 'bad_request')
        db.execute('UPDATE application_attempts SET resolved_url=?,ats_kind=? '
                   'WHERE id=? AND user_id=?',
                   (resolved, auto_apply.detect_ats(resolved), attempt['id'], user_id))
    return auto_apply.transition(
        db, user_id, attempt['id'], attempt['state'], f'Posting checked: {status}',
        metadata={'posting_status': status, 'note': note}, allow_agent=True,
        extra_updates={'posting_status': status, 'posting_checked_at': now, **_lease()})


def record_progress(db, user_id, attempt, payload):
    _require_state(attempt, LIVE_STATES, 'record progress')
    message = SSN_SHAPE.sub('[redacted]', str(payload.get('message') or '').strip())[:PROGRESS_LIMIT]
    if not message:
        raise AgentError('message is required', 400, 'bad_request')
    metadata = {}
    for key in ('step', 'page'):
        if payload.get(key) not in (None, ''):
            metadata[key] = SSN_SHAPE.sub('[redacted]', str(payload[key]))[:80]
    try:
        if payload.get('field_count') is not None:
            metadata['field_count'] = int(payload['field_count'])
    except (TypeError, ValueError):
        pass
    return auto_apply.transition(
        db, user_id, attempt['id'], attempt['state'], message, metadata=metadata,
        allow_agent=True, extra_updates=_lease())


def _clean_fields(value):
    if not isinstance(value, list):
        raise AgentError('filled_fields must be a list', 400, 'bad_request')
    cleaned = []
    for item in value[:MAX_FIELDS]:
        if not isinstance(item, dict):
            continue
        cleaned.append({'label': str(item.get('label') or '')[:FIELD_LIMIT],
                        'value': str(item.get('value') if item.get('value') is not None else '')[:FIELD_LIMIT]})
    return cleaned


def review_sha(review_json):
    return hashlib.sha256(str(review_json or '').encode('utf-8')).hexdigest()


def record_review(db, user_id, attempt, payload):
    _require_state(attempt, ('analyzing_form', 'ready_for_review', 'awaiting_final_confirmation'),
                   'record a review')
    summary = str(payload.get('summary') or '').strip()[:SUMMARY_LIMIT]
    if not summary:
        raise AgentError('summary is required', 400, 'bad_request')
    fields = _clean_fields(payload.get('filled_fields', []))
    document_id = str(payload.get('resume_document_id') or '').strip()
    version = None
    if document_id:
        doc = role_documents.get_document(db, user_id, document_id, attempt.get('role_key'))
        if not doc or doc['kind'] != 'resume':
            raise AgentError('resume_document_id is not a resume of this role', 400, 'bad_request')
        version = doc['version']
    review_json = json.dumps({
        'summary': summary, 'filled_fields': fields, 'eeo_listed': payload.get('eeo_listed') is True,
        'resume_document_id': document_id or None, 'resume_version': version,
    }, ensure_ascii=False, sort_keys=True)
    updated = auto_apply.transition(
        db, user_id, attempt['id'], 'ready_for_review', 'Review recorded',
        metadata={'eeo_listed': payload.get('eeo_listed') is True, 'field_count': len(fields)},
        allow_agent=True,
        extra_updates={'review_json': review_json, 'user_approved_at': None,
                       'approval_note': None, **_lease()})
    return updated, review_sha(review_json)


def record_approval(db, user_id, attempt, payload, eeo_opted_in):
    note = str(payload.get('note') or '').strip()[:NOTE_LIMIT]
    if not note:
        raise AgentError("note (the user's words) is required", 400, 'bad_request')
    row = db.execute('SELECT review_json,user_approved_at,approval_note FROM application_attempts '
                     'WHERE id=? AND user_id=?', (attempt['id'], user_id)).fetchone()
    sha = review_sha(row['review_json'])
    if attempt['state'] == 'awaiting_final_confirmation':
        if row['user_approved_at'] and row['approval_note'] == note \
                and str(payload.get('review_sha') or '') == sha:
            return attempt
        raise AgentError('That approval does not match the recorded one', 409, 'approval_mismatch')
    _require_state(attempt, ('ready_for_review',), 'record an approval')
    if not row['review_json']:
        raise AgentError('Post the review before the approval', 409, 'review_required')
    if str(payload.get('review_sha') or '') != sha:
        raise AgentError('The approval is for a different review than the one recorded', 409, 'review_changed')
    review = auto_apply._json(row['review_json'], {})
    if eeo_opted_in and not review.get('eeo_listed'):
        raise AgentError('Self-identification answers must be listed in the review first', 409,
                         'eeo_not_listed')
    return auto_apply.transition(
        db, user_id, attempt['id'], 'awaiting_final_confirmation', 'Approved by the user',
        allow_agent=True,
        extra_updates={'user_approved_at': auto_apply.utcnow(), 'approval_note': note, **_lease()})


def record_outcome(db, user_id, attempt, payload, role_name):
    """Returns (attempt, extras dict)."""
    outcome = str(payload.get('outcome') or '').strip()
    if outcome not in OUTCOMES:
        raise AgentError('Unknown outcome', 400, 'bad_request')
    final_state, error_code = OUTCOMES[outcome]
    message = str(payload.get('message') or '').strip()
    message = SSN_SHAPE.sub('[redacted]', message)[:OUTCOME_MESSAGE_LIMIT]
    state = attempt['state']

    if outcome == 'submitted':
        approved = db.execute('SELECT user_approved_at FROM application_attempts '
                              'WHERE id=? AND user_id=?', (attempt['id'], user_id)).fetchone()
        if state == 'submitted':
            tracker_sync.apply_agent_outcome(db, user_id, attempt, 'submitted', role_name)
            return attempt, {'repeated': True}
        if not approved['user_approved_at']:
            raise AgentError('The user has not approved this application', 409, 'approval_required')
        if state == 'awaiting_final_confirmation':
            attempt = auto_apply.transition(
                db, user_id, attempt['id'], 'submitting', 'Submitting after the user said yes',
                allow_agent=True, extra_updates=_lease())
        elif state != 'submitting':
            raise AgentError(f'Cannot record a submission while the attempt is {state}', 409, 'wrong_state')
        tracker_sync.apply_agent_outcome(db, user_id, attempt, 'submitted', role_name)
        attempt = auto_apply.transition(
            db, user_id, attempt['id'], 'submitted', message or 'Submitted', allow_agent=True,
            extra_updates=dict(NO_LEASE))
        return attempt, {}

    if outcome == 'submit_failed':
        if state == 'submission_failed':
            return attempt, {'repeated': True}
        if state == 'awaiting_final_confirmation':
            attempt = auto_apply.transition(
                db, user_id, attempt['id'], 'submitting', 'Submitting after the user said yes',
                allow_agent=True, extra_updates=_lease())
        elif state != 'submitting':
            raise AgentError(f'Cannot record a failed submission while the attempt is {state}',
                             409, 'wrong_state')
        attempt = auto_apply.transition(
            db, user_id, attempt['id'], 'submission_failed', message or 'Submission failed',
            error_code, allow_agent=True, extra_updates=dict(NO_LEASE))
        return attempt, {}

    if outcome == 'needs_answer':
        if state in ('needs_user_input', 'queued_for_agent') and not attempt.get('lease_expires_at'):
            return attempt, {'repeated': True, 'requeued': state == 'queued_for_agent'}
        _require_state(attempt, ('analyzing_form',), 'record this outcome')
        ids = payload.get('question_ids')
        question_bank.block(db, user_id, attempt['id'], ids if isinstance(ids, list) else [])
        if question_bank.waiting_count(db, user_id, attempt['id']) == 0:
            updated = auto_apply.transition(
                db, user_id, attempt['id'], 'queued_for_agent',
                'Answers are already saved; queued for the agent', allow_agent=True,
                extra_updates={'claim_id': None, **NO_LEASE})
            return updated, {'requeued': True}
        updated = auto_apply.transition(
            db, user_id, attempt['id'], 'needs_user_input',
            message or 'Waiting for your answers', error_code, allow_agent=True,
            extra_updates=dict(NO_LEASE))
        return updated, {'requeued': False}

    if state == final_state:
        return attempt, {'repeated': True}
    _require_state(attempt, ('analyzing_form',), 'record this outcome')
    updated = auto_apply.transition(
        db, user_id, attempt['id'], final_state, message or auto_apply.ATTEMPT_STATES[final_state],
        error_code, allow_agent=True, extra_updates=dict(NO_LEASE))
    return updated, {}


def release(db, user_id, attempt):
    _require_state(attempt, ('analyzing_form', 'ready_for_review'), 'release')
    extra = {'claim_id': None, **NO_LEASE}
    if attempt['state'] == 'ready_for_review':
        extra.update({'review_json': None, 'user_approved_at': None, 'approval_note': None})
    return auto_apply.transition(
        db, user_id, attempt['id'], 'queued_for_agent', 'Released by the desktop agent',
        allow_agent=True, extra_updates=extra)


def extend_lease(db, attempt_id):
    db.execute('UPDATE application_attempts SET lease_expires_at=? WHERE id=?',
               (new_lease(), attempt_id))
    db.commit()
