"""Write an agent outcome onto exactly one tracker row (backlog 19.2).

Uses the request connection and the one-row upsert, never save_triage (which
deletes every row for the user). The note check makes a repeat safe, so a
client that retries an outcome after a lost response does not add a second note.
"""
from datetime import datetime

import profile_store

_RANK = {'saved': 0, 'applied': 1, 'interviewing': 2, 'offer': 3}
_NEVER_CHANGE = {'rejected', 'closed', 'not_interested'}


def _has_note(record, attempt_id):
    return any(attempt_id in str(entry.get('text', ''))
               for entry in record.get('notes_log', []) if isinstance(entry, dict))


def _add_note(record, text):
    record.setdefault('notes_log', []).append(
        {'text': text, 'at': datetime.utcnow().isoformat(), 'source': 'agent'})


def _resume_label(attempt, role_name):
    review = attempt.get('review') or {}
    version = review.get('resume_version')
    return f'resume {role_name} v{version}' if version else f'resume {role_name}'


def apply_agent_outcome(db, user_id, attempt, kind, role_name='Default'):
    """kind is 'submitted' or 'posting_closed'."""
    job_key = attempt['job_key']
    record = profile_store.load_record_on(db, user_id, job_key)
    if record is None:
        record = dict(attempt.get('job') or {})
        record.setdefault('job_key', job_key)
        record.setdefault('status', 'saved')
        record.setdefault('stage', record.get('status') or 'saved')
    attempt_id = str(attempt['id'])
    if kind == 'submitted':
        stage = record.get('stage') or record.get('status') or 'saved'
        if stage not in _NEVER_CHANGE and _RANK.get(stage, 0) < 1:
            record.setdefault('stage_history', []).append(
                {'stage': 'applied', 'at': datetime.utcnow().isoformat()})
            record['stage'] = 'applied'
            record['status'] = 'applied'
            stage = 'applied'
        if stage == 'applied' and not record.get('applied_date'):
            record['applied_date'] = datetime.now().date().isoformat()
        record['application_attempt_id'] = attempt_id
        if not _has_note(record, attempt_id):
            _add_note(record, f'Applied via the desktop agent, attempt {attempt_id}, '
                              f'{_resume_label(attempt, role_name)}')
    elif kind == 'posting_closed':
        if not _has_note(record, attempt_id):
            _add_note(record, f'Posting closed before apply, attempt {attempt_id}')
    else:
        raise ValueError('Unknown outcome')
    record['updated_at'] = datetime.utcnow().isoformat()
    return profile_store.upsert_record_on(db, user_id, job_key, record)
