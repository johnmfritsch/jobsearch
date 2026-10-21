"""The apply packet handed to the desktop agent when it claims a job (backlog 19.2).

Built from the profile, the attempt's own role history, the role's resume and
the question bank at claim time; nothing is stored as a copy. The server cannot
click anything in the agent's browser. It enforces the stops by having no agent
submit endpoint and by refusing a `submitted` outcome without the user's yes.
"""
import auto_apply
import question_bank
import role_documents
import role_history

DESCRIPTION_LIMIT = 20000
EMPLOYMENT_FIELDS = ('current_employee', 'former_employee', 'conflict_of_interest')

STOP_LIST = [
    'Sign-in, MFA or account creation: John does these himself in his browser. Never type a password.',
    'CAPTCHA or any "I am human" check: stop and report it.',
    'Terms, consent, certification or "I agree": ask John in chat and continue only on an explicit yes.',
    'Submit: only after the review is posted and John says yes for this application.',
    'LinkedIn applications are manual.',
    'Social security, passport, driver\'s license, national id, ITIN: never type these. Stop and report manual.',
]


def _screening(profile, match):
    stored = profile.get('screening') or {}
    result = {}
    for key in auto_apply.SCREENING_YES_NO:
        value = str(stored.get(key) or '')
        if match['matched'] and key in EMPLOYMENT_FIELDS:
            result[key] = {'value': None, 'safe': False, 'ask_user': True,
                           'reason': 'employer_match'}
        elif value in ('yes', 'no'):
            result[key] = {'value': value, 'safe': True, 'ask_user': False, 'reason': ''}
        else:
            result[key] = {'value': None, 'safe': False, 'ask_user': True,
                           'reason': 'ask_each_time'}
    shift = str(stored.get('shift_availability') or '')
    result['shift_availability'] = {
        'value': shift or None, 'safe': bool(shift), 'ask_user': not shift,
        'reason': '' if shift else 'ask_each_time'}
    result['agent_notes'] = {'value': str(stored.get('notes') or ''), 'safe': False,
                             'ask_user': False, 'reason': 'context_only'}
    return result


def _eeo(profile):
    eeo = profile.get('eeo') or {}
    if eeo.get('opt_in') is not True:
        return None
    values = {key: eeo[key] for key in auto_apply.EEO_CHOICES if eeo.get(key)}
    return {'values': values, 'labels': auto_apply.EEO_LABELS,
            'ask_user': [key for key in auto_apply.EEO_CHOICES if not eeo.get(key)],
            'review_required': True}


def _document(db, user_id, role_key, kind):
    txt = role_documents.current_document(db, user_id, role_key, kind, 'txt')
    pdf = role_documents.current_document(db, user_id, role_key, kind, 'pdf')
    if not txt and not pdf:
        return None
    return {
        'text': (txt or {}).get('text'),
        'document_id': (txt or {}).get('document_id'),
        'version': (txt or {}).get('version'),
        'sha256': (txt or {}).get('sha256'),
        'pdf_document_id': (pdf or {}).get('document_id'),
        'pdf_version': (pdf or {}).get('version'),
        'pdf_sha256': (pdf or {}).get('sha256'),
        'pdf_file_name': (pdf or {}).get('file_name'),
    }


def build(db, user_id, attempt):
    job = attempt.get('job') or {}
    role_key = attempt.get('role_key') or ''
    profile = auto_apply.load_profile(db, user_id)['profile']
    history = role_history.get_history(db, user_id, role_key)
    match = role_history.matching_employers(db, user_id, job.get('company'))
    resume = role_documents.resume_text_for_role_key(db, user_id, role_key) or {}
    resume_doc = _document(db, user_id, role_key, 'resume') or {}
    resume_packet = {
        'text': resume.get('text'),
        'document_id': resume.get('document_id'),
        'version': resume.get('version'),
        'sha256': resume.get('sha256'),
        'pdf_document_id': resume_doc.get('pdf_document_id'),
        'pdf_version': resume_doc.get('pdf_version'),
        'pdf_sha256': resume_doc.get('pdf_sha256'),
        'pdf_file_name': resume_doc.get('pdf_file_name'),
    }
    packet = {
        'attempt': {'id': attempt['id'], 'claim_id': attempt.get('claim_id'),
                    'lease_expires_at': attempt.get('lease_expires_at')},
        'job': {
            'title': job.get('title'), 'company': job.get('company'),
            'location': job.get('location'), 'url': job.get('url'),
            'apply_url': attempt.get('resolved_url') or job.get('apply_url'),
            'source': job.get('source'), 'job_key': attempt['job_key'],
            'description': str(job.get('description') or '')[:DESCRIPTION_LIMIT],
        },
        'role': {'role_key': role_key,
                 'role_name': role_documents.role_key_name(db, user_id, role_key)},
        'resume': resume_packet,
        'cover_letter': _document(db, user_id, role_key, 'cover_letter'),
        'profile': auto_apply.shareable_profile(profile),
        'name_parts': auto_apply.name_parts(profile),
        'work_history': history.get('work_history', []),
        'education': history.get('education', []),
        'verified_at': history.get('verified_at') or None,
        'labels': {'education_level': role_history.EDU_LEVEL_LABELS,
                   'eeo': auto_apply.EEO_LABELS,
                   'yes_no': {'yes': 'Yes', 'no': 'No'}},
        'screening': _screening(profile, match),
        'bank': [{'question': row['question'], 'answer': row['answer'],
                  'field_type': row['field_type'],
                  'safe_to_autofill': row['safe_to_autofill']}
                 for row in question_bank.confirmed_rows(db, user_id)],
        'employer_match': match,
        'stop_list': STOP_LIST,
    }
    eeo = _eeo(profile)
    if eeo is not None:
        packet['eeo'] = eeo
    return packet
