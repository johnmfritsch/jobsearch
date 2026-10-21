"""Saved answers to application questions (backlog 19.2).

A question the desktop agent meets that is not in the profile goes here, not
into the profile sections. An answer John gives in chat is stored `confirmed`
with source `agent_chat`; he can correct or delete it on the Saved answers
card. Only yes/no and choice answers are reusable on their own; a free-text
answer stays unsafe until he ticks "Reuse this answer".
"""
import json
import re
import uuid

import auto_apply
import role_history

QUESTION_LIMIT = 500
KEY_LIMIT = 200
ANSWER_LIMIT = 2000
OPTION_LIMIT = 200
MAX_OPTIONS = 40
FIELD_TYPES = {'yes_no', 'choice', 'text'}

SENSITIVE_QUESTION = re.compile(
    r"social security|\bssn\b|passport|driver.?s? licen[cs]e|national id|"
    r"tax identification|\bitin\b", re.I)
SSN_SHAPE = re.compile(r'\b\d{3}-?\d{2}-?\d{4}\b')


class BankError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def normalize_key(text):
    cleaned = re.sub(r'[^\w\s]', ' ', str(text or '').casefold())
    return ' '.join(cleaned.split())[:KEY_LIMIT]


def _refuse(question, answer):
    if SENSITIVE_QUESTION.search(question or '') or SSN_SHAPE.search(question or ''):
        raise BankError('Government ID questions are never stored or answered by the agent')
    if answer and SSN_SHAPE.search(answer):
        raise BankError('That answer looks like a government ID number and is not stored')


def _options(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:OPTION_LIMIT] for item in value[:MAX_OPTIONS]
            if str(item).strip()]


def public(row):
    options = []
    try:
        options = json.loads(row['options_json'] or '[]')
    except ValueError:
        pass
    return {
        'id': row['id'], 'question': row['question_text'], 'answer': row['answer_text'],
        'field_type': row['field_type'], 'options': options, 'source': row['source'],
        'status': row['status'], 'safe_to_autofill': bool(row['safe_to_autofill']),
        'times_seen': row['times_seen'],
        'blocking_attempt_id': row['blocking_attempt_id'], 'updated_at': row['updated_at'],
    }


def is_safe_to_autofill(question_key, field_type, options, answer, company):
    """yes/no and choice answers only, and only when the question does not name
    the company (an answer about one employer must not be typed for the next)."""
    answer = str(answer or '').strip()
    if not answer or field_type not in ('yes_no', 'choice'):
        return False
    if field_type == 'yes_no':
        if answer.casefold() not in ('yes', 'no'):
            return False
    elif answer.casefold() not in {item.casefold() for item in options}:
        return False
    name = role_history.employer_name_key(company)
    return not (name and f' {name} ' in f' {question_key} ')


def _requeue_if_unblocked(db, user_id, attempt_id):
    if not attempt_id:
        return None
    attempt = auto_apply.get_attempt(db, user_id, attempt_id, include_details=False)
    if not attempt or not attempt['agent_owned'] or attempt['state'] != 'needs_user_input':
        return None
    remaining = db.execute(
        'SELECT COUNT(*) FROM application_question_bank WHERE user_id=? '
        "AND status='needs_answer' AND blocking_attempt_id=?",
        (user_id, str(attempt_id))).fetchone()[0]
    if remaining:
        return None
    return auto_apply.transition(
        db, user_id, attempt_id, 'queued_for_agent',
        'Every question is answered; queued for the agent', allow_agent=True,
        extra_updates={'claim_id': None, 'claimed_at': None, 'lease_expires_at': None})


def confirmed_rows(db, user_id):
    return [public(row) for row in db.execute(
        "SELECT * FROM application_question_bank WHERE user_id=? AND status='confirmed' "
        'ORDER BY updated_at DESC', (user_id,))]


def list_rows(db, user_id):
    return [public(row) for row in db.execute(
        'SELECT * FROM application_question_bank WHERE user_id=? ORDER BY updated_at DESC',
        (user_id,))]


def from_agent(db, user_id, attempt, payload):
    """Look up or store a question for a held attempt. Returns the public row."""
    question = str(payload.get('question') or '').strip()
    if not question:
        raise BankError('Question text is required')
    answer = payload.get('answer')
    answer = str(answer).strip()[:ANSWER_LIMIT] if answer is not None else ''
    _refuse(question, answer)
    key = normalize_key(question)
    if not key:
        raise BankError('Question text is required')
    field_type = payload.get('field_type')
    field_type = field_type if field_type in FIELD_TYPES else 'text'
    options = _options(payload.get('options'))
    company = (attempt.get('job') or {}).get('company')
    now = auto_apply.utcnow()
    row = db.execute(
        'SELECT * FROM application_question_bank WHERE user_id=? AND question_key=?',
        (user_id, key)).fetchone()
    unblocked = None
    if row and row['status'] == 'confirmed':
        db.execute('UPDATE application_question_bank SET times_seen=times_seen+1 WHERE id=?',
                   (row['id'],))
        row_id = row['id']
    elif row:
        if answer:
            stored_options = options or json.loads(row['options_json'] or '[]')
            safe = is_safe_to_autofill(key, row['field_type'], stored_options, answer, company)
            db.execute(
                'UPDATE application_question_bank SET answer_text=?,status=?,source=?,'
                'safe_to_autofill=?,blocking_attempt_id=NULL,times_seen=times_seen+1,'
                'updated_at=? WHERE id=?',
                (answer, 'confirmed', 'agent_chat', int(safe), now, row['id']))
            unblocked = row['blocking_attempt_id']
        else:
            db.execute('UPDATE application_question_bank SET times_seen=times_seen+1 WHERE id=?',
                       (row['id'],))
        row_id = row['id']
    else:
        row_id = str(uuid.uuid4())
        confirmed = bool(answer)
        safe = confirmed and is_safe_to_autofill(key, field_type, options, answer, company)
        db.execute(
            'INSERT INTO application_question_bank (id,user_id,question_key,question_text,'
            'answer_text,field_type,options_json,source,status,safe_to_autofill,times_seen,'
            'blocking_attempt_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (row_id, user_id, key, question[:QUESTION_LIMIT], answer, field_type,
             json.dumps(options, ensure_ascii=False), 'agent_chat',
             'confirmed' if confirmed else 'needs_answer', int(safe), 1,
             None if confirmed else str(attempt['id']), now, now))
    db.commit()
    if unblocked:
        _requeue_if_unblocked(db, user_id, unblocked)
    return public(db.execute('SELECT * FROM application_question_bank WHERE id=?',
                             (row_id,)).fetchone())


def user_update(db, user_id, row_id, answer=None, safe=None):
    row = db.execute('SELECT * FROM application_question_bank WHERE id=? AND user_id=?',
                     (str(row_id), user_id)).fetchone()
    if not row:
        raise LookupError('Saved answer not found')
    blocking = row['blocking_attempt_id']
    now = auto_apply.utcnow()
    new_answer = row['answer_text']
    status, source, safe_value = row['status'], row['source'], bool(row['safe_to_autofill'])
    if answer is not None:
        new_answer = str(answer).strip()[:ANSWER_LIMIT]
        _refuse(row['question_text'], new_answer)
        if new_answer != row['answer_text']:
            if not new_answer:
                raise BankError('Enter an answer, or delete this saved answer')
            status, source, blocking = 'confirmed', 'user_ui', None
            # The company is known only while an attempt is waiting on this
            # question. Without it, an answer is never newly marked safe here;
            # John ticks "Reuse this answer" to do that.
            company = None
            if row['blocking_attempt_id']:
                job_row = db.execute(
                    'SELECT job_json FROM application_attempts WHERE id=? AND user_id=?',
                    (row['blocking_attempt_id'], user_id)).fetchone()
                if job_row:
                    company = (auto_apply._json(job_row['job_json'], {}) or {}).get('company')
            rule = is_safe_to_autofill(
                row['question_key'], row['field_type'],
                json.loads(row['options_json'] or '[]'), new_answer, company)
            safe_value = rule and (bool(company) or bool(row['safe_to_autofill']))
    if safe is not None:
        if status != 'confirmed':
            raise BankError('Answer the question before choosing to reuse it', 409)
        safe_value = bool(safe)
    db.execute(
        'UPDATE application_question_bank SET answer_text=?,status=?,source=?,'
        'safe_to_autofill=?,blocking_attempt_id=?,updated_at=? WHERE id=?',
        (new_answer, status, source, int(safe_value), blocking, now, row['id']))
    db.commit()
    if row['blocking_attempt_id'] and status == 'confirmed':
        _requeue_if_unblocked(db, user_id, row['blocking_attempt_id'])
    return public(db.execute('SELECT * FROM application_question_bank WHERE id=?',
                             (row['id'],)).fetchone())


def delete(db, user_id, row_id):
    row = db.execute('SELECT * FROM application_question_bank WHERE id=? AND user_id=?',
                     (str(row_id), user_id)).fetchone()
    if not row:
        raise LookupError('Saved answer not found')
    db.execute('DELETE FROM application_question_bank WHERE id=?', (row['id'],))
    db.commit()
    if row['blocking_attempt_id'] and row['status'] == 'needs_answer':
        _requeue_if_unblocked(db, user_id, row['blocking_attempt_id'])


def block(db, user_id, attempt_id, question_ids):
    """Mark these still-unanswered rows as what the attempt is waiting on."""
    for question_id in (question_ids or [])[:40]:
        db.execute(
            'UPDATE application_question_bank SET blocking_attempt_id=? '
            "WHERE id=? AND user_id=? AND status='needs_answer'",
            (str(attempt_id), str(question_id), user_id))
    db.commit()


def waiting_count(db, user_id, attempt_id):
    return db.execute(
        'SELECT COUNT(*) FROM application_question_bank WHERE user_id=? '
        "AND status='needs_answer' AND blocking_attempt_id=?",
        (user_id, str(attempt_id))).fetchone()[0]
