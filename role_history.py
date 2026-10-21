"""Role-level work history and education for the apply agent (backlog 19.1).

Each search role (search_presets row, or the Default role) can carry its own
resume, so each role carries its own work history and education. Rows live in
role_application_history keyed by (user_id, role_key) using the same role keys
as role_documents ('default', 'preset:<id>', 'name:<hash>').

Nothing here talks to an AI provider. The resume importer is deterministic and
only ever produces DRAFT rows; the user edits and confirms them. The server
re-validates every row it is sent, so a copy preview that was edited in the
browser is never trusted.
"""

import json
import re
import uuid
from datetime import date, datetime

MAX_WORK_ROWS = 15
MAX_EDUCATION_ROWS = 8
MAX_HISTORY_BYTES = 64 * 1024

WORK_LIMITS = {'title': 160, 'employer': 200, 'location': 160, 'description': 2000}
EDU_LIMITS = {'school': 200, 'degree': 160, 'field': 160, 'location': 160}
EDU_LEVELS = ('high_school', 'associate', 'bachelor', 'master', 'doctorate', 'other')
EDU_LEVEL_LABELS = {
    'high_school': 'High school', 'associate': 'Associate', 'bachelor': "Bachelor's",
    'master': "Master's", 'doctorate': 'Doctorate', 'other': 'Other',
}

_MONTH_YEAR = re.compile(r'^(0[1-9]|1[0-2])/(\d{4})$')
_GRADUATED = re.compile(r'^(?:(0[1-9]|1[0-2])/)?(\d{4})$')
_MIN_YEAR = 1950


def _max_year():
    return date.today().year + 1


def _year_ok(year):
    return _MIN_YEAR <= int(year) <= _max_year()


def _short_id():
    return uuid.uuid4().hex[:10]


def _text(value, limit):
    return str(value or '').strip()[:limit]


def month_key(value):
    """'MM/YYYY' -> sortable int (YYYY*12+MM-1), or None."""
    match = _MONTH_YEAR.match(str(value or ''))
    if not match:
        return None
    return int(match.group(2)) * 12 + int(match.group(1)) - 1


def _clean_work_row(row, index):
    if not isinstance(row, dict):
        raise ValueError(f'Work history row {index}: invalid row')
    label = f'Work history row {index}'
    cleaned = {'id': _text(row.get('id'), 20) or _short_id()}
    for field, limit in WORK_LIMITS.items():
        cleaned[field] = _text(row.get(field), limit)
    if not cleaned['title']:
        raise ValueError(f'{label}: job title is required')
    if not cleaned['employer']:
        raise ValueError(f'{label}: employer is required')
    current = row.get('current') is True
    cleaned['current'] = current
    start = _text(row.get('from'), 7)
    end = _text(row.get('to'), 7)
    m = _MONTH_YEAR.match(start)
    if not m:
        raise ValueError(f'{label} ({cleaned["employer"]}): start date must be MM/YYYY')
    if not _year_ok(m.group(2)):
        raise ValueError(f'{label} ({cleaned["employer"]}): start year is out of range')
    cleaned['from'] = start
    if current:
        cleaned['to'] = ''
    else:
        m = _MONTH_YEAR.match(end)
        if not m:
            raise ValueError(
                f'{label} ({cleaned["employer"]}): end date must be MM/YYYY, or tick "current"')
        if not _year_ok(m.group(2)):
            raise ValueError(f'{label} ({cleaned["employer"]}): end year is out of range')
        if month_key(start) > month_key(end):
            raise ValueError(f'{label} ({cleaned["employer"]}): start date is after the end date')
        cleaned['to'] = end
    copied = _text(row.get('copied_from'), 80)
    if copied:
        cleaned['copied_from'] = copied
    return cleaned


def _clean_education_row(row, index):
    if not isinstance(row, dict):
        raise ValueError(f'Education row {index}: invalid row')
    label = f'Education row {index}'
    cleaned = {'id': _text(row.get('id'), 20) or _short_id()}
    level = _text(row.get('level'), 20)
    if level not in EDU_LEVELS:
        raise ValueError(f'{label}: choose an education level')
    cleaned['level'] = level
    for field, limit in EDU_LIMITS.items():
        cleaned[field] = _text(row.get(field), limit)
    if not cleaned['school']:
        raise ValueError(f'{label}: school is required')
    graduated = _text(row.get('graduated'), 7)
    if graduated:
        m = _GRADUATED.match(graduated)
        if not m or not _year_ok(m.group(2)):
            raise ValueError(f'{label}: graduation date must be YYYY or MM/YYYY')
    cleaned['graduated'] = graduated
    copied = _text(row.get('copied_from'), 80)
    if copied:
        cleaned['copied_from'] = copied
    return cleaned


def clean_history(payload):
    """Validate a history payload; returns (cleaned, warnings) or raises ValueError."""
    if not isinstance(payload, dict):
        raise ValueError('History must be an object')
    work = payload.get('work_history', [])
    education = payload.get('education', [])
    if not isinstance(work, list) or not isinstance(education, list):
        raise ValueError('Work history and education must be lists')
    if len(work) > MAX_WORK_ROWS:
        raise ValueError(f'Work history is limited to {MAX_WORK_ROWS} jobs')
    if len(education) > MAX_EDUCATION_ROWS:
        raise ValueError(f'Education is limited to {MAX_EDUCATION_ROWS} entries')
    cleaned = {
        'work_history': [_clean_work_row(row, i + 1) for i, row in enumerate(work)],
        'education': [_clean_education_row(row, i + 1) for i, row in enumerate(education)],
        'verified': payload.get('verified') is True,
    }
    ids = [row['id'] for row in cleaned['work_history']] + [row['id'] for row in cleaned['education']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate row ids')
    return cleaned, overlap_warnings(cleaned['work_history'])


def overlap_warnings(work):
    """Warn (never block) when two jobs at different employers overlap in time."""
    warnings = []
    today = date.today().year * 12 + date.today().month - 1
    spans = []
    for row in work:
        start = month_key(row['from'])
        end = today if row.get('current') else month_key(row['to'])
        spans.append((start, end, row))
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a, b = spans[i], spans[j]
            if a[2]['employer'].casefold() == b[2]['employer'].casefold():
                continue
            if a[0] <= b[1] and b[0] <= a[1]:
                warnings.append(
                    f"{a[2]['employer']} ({a[2]['from']}) and {b[2]['employer']} ({b[2]['from']}) "
                    'overlap in time. Check the dates.')
    return warnings


# ── storage ──────────────────────────────────────────────────────────────────

def _empty():
    return {'work_history': [], 'education': [], 'verified_at': '', 'source': '', 'previous': None}


def get_history(db, user_id, role_key):
    row = db.execute(
        'SELECT history_json,updated_at FROM role_application_history '
        'WHERE user_id=? AND role_key=?', (user_id, role_key)).fetchone()
    data = _empty()
    if row:
        try:
            stored = json.loads(row['history_json'] or '{}')
        except ValueError:
            stored = {}
        for key in data:
            if key in stored:
                data[key] = stored[key]
    data['updated_at'] = row['updated_at'] if row else None
    data['has_previous'] = bool(data.get('previous'))
    data.pop('previous', None)
    return data


def _stored_raw(db, user_id, role_key):
    row = db.execute(
        'SELECT history_json FROM role_application_history WHERE user_id=? AND role_key=?',
        (user_id, role_key)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['history_json'] or '{}')
    except ValueError:
        return None


def _dates_signature(work):
    return [(r['employer'].casefold(), r['from'], r['to'], bool(r.get('current'))) for r in work]


def save_history(db, user_id, role, payload, source='manual'):
    """Validate and store a role's history. Keeps the prior value for one undo.

    'Dates verified' is stored only when the caller sends verified=true (the
    browser unticks the box whenever a date is edited). If the dates are unchanged
    from the stored copy the original verification date is kept; otherwise it is
    stamped today. A verification is therefore only ever stated, never inferred.
    """
    cleaned, warnings = clean_history(payload)
    previous = _stored_raw(db, user_id, role['role_key'])
    today = date.today().isoformat()
    verified_at = ''
    if cleaned['verified']:
        same_dates = previous is not None and _dates_signature(
            previous.get('work_history', [])) == _dates_signature(cleaned['work_history'])
        verified_at = (previous or {}).get('verified_at') if same_dates and (previous or {}).get(
            'verified_at') else today
    stored = {
        'work_history': cleaned['work_history'],
        'education': cleaned['education'],
        'verified_at': verified_at,
        'source': source,
        'previous': ({k: previous.get(k) for k in ('work_history', 'education', 'verified_at', 'source')}
                     if previous else None),
    }
    encoded = json.dumps(stored, ensure_ascii=False)
    if len(encoded.encode('utf-8')) > MAX_HISTORY_BYTES:
        raise ValueError('History is too large to save')
    now = datetime.utcnow().isoformat()
    db.execute(
        'INSERT INTO role_application_history (user_id,role_key,history_json,updated_at) '
        'VALUES (?,?,?,?) ON CONFLICT(user_id,role_key) DO UPDATE SET '
        'history_json=excluded.history_json,updated_at=excluded.updated_at',
        (user_id, role['role_key'], encoded, now))
    db.commit()
    result = get_history(db, user_id, role['role_key'])
    result['warnings'] = warnings + cross_role_warnings(db, user_id, role['role_key'])
    return result


def undo_history(db, user_id, role):
    """Swap the stored history with its one-step 'previous' copy."""
    current = _stored_raw(db, user_id, role['role_key'])
    if not current or not current.get('previous'):
        raise LookupError('There is nothing to undo for this role')
    restored = current['previous']
    stored = {
        'work_history': restored.get('work_history', []),
        'education': restored.get('education', []),
        'verified_at': restored.get('verified_at', ''),
        'source': restored.get('source', ''),
        'previous': {k: current.get(k) for k in ('work_history', 'education', 'verified_at', 'source')},
    }
    db.execute(
        'UPDATE role_application_history SET history_json=?,updated_at=? '
        'WHERE user_id=? AND role_key=?',
        (json.dumps(stored, ensure_ascii=False), datetime.utcnow().isoformat(),
         user_id, role['role_key']))
    db.commit()
    result = get_history(db, user_id, role['role_key'])
    result['warnings'] = cross_role_warnings(db, user_id, role['role_key'])
    return result


def delete_for_role(db, user_id, role_key):
    db.execute('DELETE FROM role_application_history WHERE user_id=? AND role_key=?',
               (user_id, role_key))
    db.commit()


# ── copy between roles (preview only; saving goes through save_history) ──────

def _same_job(a, b):
    return (a['employer'].casefold() == b['employer'].casefold()
            and a['title'].casefold() == b['title'].casefold())


def _same_school(a, b):
    return a['school'].casefold() == b['school'].casefold() and a['level'] == b['level']


def copy_preview(db, user_id, source_role, target_role, mode):
    """Build the rows a copy WOULD produce. Nothing is written.

    replace: the source rows replace the target's.
    merge:   the target's rows stay; source rows the target lacks are added
             (status 'new'); a source job that matches a target job but differs
             is added as status 'conflict' so the user decides which to keep.
    Every returned row has a 'status' and 'copied_from' for the preview; the
    browser sends the edited rows back through save_history.
    """
    if mode not in ('merge', 'replace'):
        raise ValueError('Choose merge or replace')
    if source_role['role_key'] == target_role['role_key']:
        raise ValueError('Choose a different role to copy from')
    source = get_history(db, user_id, source_role['role_key'])
    target = get_history(db, user_id, target_role['role_key'])
    label = source_role['role_name']

    def tag(row, status):
        copy = dict(row)
        copy['id'] = _short_id()
        copy['copied_from'] = label
        copy['status'] = status
        return copy

    work, education = [], []
    if mode == 'replace':
        work = [tag(r, 'new') for r in source['work_history']]
        education = [tag(r, 'new') for r in source['education']]
        removed = {'work_history': len(target['work_history']), 'education': len(target['education'])}
    else:
        work = [dict(r, status='kept') for r in target['work_history']]
        education = [dict(r, status='kept') for r in target['education']]
        for row in source['work_history']:
            match = next((t for t in target['work_history'] if _same_job(row, t)), None)
            if match is None:
                work.append(tag(row, 'new'))
            elif (match['from'], match['to'], bool(match.get('current'))) != (
                    row['from'], row['to'], bool(row.get('current'))):
                work.append(tag(row, 'conflict'))
        for row in source['education']:
            if not any(_same_school(row, t) for t in target['education']):
                education.append(tag(row, 'new'))
        removed = {'work_history': 0, 'education': 0}
    return {'mode': mode, 'source_role': label, 'work_history': work, 'education': education,
            'will_remove': removed}


# ── cross-role consistency ───────────────────────────────────────────────────

def cross_role_warnings(db, user_id, role_key):
    """Warn when the same employer has different dates in another role."""
    mine = _stored_raw(db, user_id, role_key)
    if not mine:
        return []
    warnings = []
    names = {}
    for row in db.execute(
            'SELECT role_key,history_json FROM role_application_history '
            'WHERE user_id=? AND role_key<>?', (user_id, role_key)):
        try:
            other = json.loads(row['history_json'] or '{}')
        except ValueError:
            continue
        names[row['role_key']] = other
    labels = _role_labels(db, user_id)
    for key, other in names.items():
        for a in mine.get('work_history', []):
            for b in other.get('work_history', []):
                if a.get('employer', '').casefold() != b.get('employer', '').casefold():
                    continue
                if (a['from'], a['to'], bool(a.get('current'))) == (
                        b['from'], b['to'], bool(b.get('current'))):
                    continue
                if _titles_differ(a, b):
                    continue
                where = labels.get(key, 'another role')
                note = f' (copied from {a["copied_from"]})' if a.get('copied_from') else ''
                warnings.append(
                    f"{a['employer']}: dates {a['from']}-{a['to'] or 'present'}{note} differ from "
                    f"{b['from']}-{b['to'] or 'present'} in {where}. Applications under different "
                    'roles should agree on dates.')
    return list(dict.fromkeys(warnings))


_LEGAL_TOKENS = {'inc', 'llc', 'corp', 'corporation', 'co', 'company', 'ltd', 'the'}


def _employer_tokens(name):
    """casefold, strip punctuation, drop legal suffixes (and a leading 'the')."""
    text = re.sub(r'[^a-z0-9]+', ' ', str(name or '').casefold().replace("'", ''))
    tokens = text.split()
    while tokens and tokens[-1] in _LEGAL_TOKENS:
        tokens.pop()
    if tokens and tokens[0] == 'the':
        tokens.pop(0)
    return tokens


def _employers_match(a, b):
    ta, tb = _employer_tokens(a), _employer_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return long_[:len(short)] == short and len(' '.join(short)) >= 3


def matching_employers(db, user_id, company):
    """Employers in the user's CONFIRMED history (any role) that look like this
    company. A match does not say current versus former, so the caller asks."""
    matched = ''
    role_keys = []
    for row in db.execute(
            'SELECT role_key,history_json FROM role_application_history WHERE user_id=?',
            (user_id,)):
        try:
            data = json.loads(row['history_json'] or '{}')
        except ValueError:
            continue
        if not data.get('verified_at'):
            continue
        for job in data.get('work_history', []):
            if _employers_match(company, job.get('employer')):
                matched = matched or job.get('employer', '')
                if row['role_key'] not in role_keys:
                    role_keys.append(row['role_key'])
    return {'matched': bool(role_keys), 'matched_employer': matched, 'role_keys': role_keys}


def employer_name_key(name):
    """Normalized company name, used to keep company-specific answers unsafe."""
    return ' '.join(_employer_tokens(name))


def _titles_differ(a, b):
    """Two different positions at one employer may legitimately have different dates."""
    return a.get('title', '').casefold() != b.get('title', '').casefold()


def _role_labels(db, user_id):
    labels = {'default': 'Default'}
    for row in db.execute('SELECT id,name FROM search_presets WHERE user_id=?', (user_id,)):
        labels[f"preset:{row['id']}"] = row['name']
    return labels


# ── resume importer (deterministic draft rows) ───────────────────────────────

_MONTHS = {m: i + 1 for i, m in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}
_DATE = r'(?:(?P<{p}m>[A-Za-z]{{3,9}}\.?|\d{{1,2}})[\s/.,-]+)?(?P<{p}y>(?:19|20)\d{{2}})'
_RANGE = re.compile(
    r'(?P<a>' + _DATE.format(p='a') + r')\s*(?:-|–|—|to|through)\s*'
    r'(?P<b>(?:present|current|now|today)|' + _DATE.format(p='b') + r')',
    re.I)
_EDU_WORDS = re.compile(
    r"\b(bachelor|master|associate|doctor|ph\.?d|mba|b\.?s\.?|b\.?a\.?|m\.?s\.?|m\.?a\.?|diploma|"
    r"university|college|institute)\b", re.I)
_SECTION = re.compile(r'^\s*(education|experience|work history|employment|professional experience|'
                      r'skills|certifications|projects|summary|objective)\b', re.I)


def _month_number(token):
    token = str(token or '').strip(' .').lower()
    if not token:
        return None
    if token.isdigit():
        number = int(token)
        return number if 1 <= number <= 12 else None
    return _MONTHS.get(token[:3])


def _fmt(month, year):
    return f'{month:02d}/{year}' if month else ''


_SCHOOL_WORDS = re.compile(r'university|college|institute|school|academy', re.I)
_BULLET = ('-', '•', '*')


def _split_header(text):
    """Split a job header into (title, employer, location). Best effort."""
    text = text.strip(' |-–—,;()')
    location = ''
    for splitter in (r'\s*\|\s*', r'\s+[-–—]\s+', r'\s+at\s+', r',\s+(?=[A-Z])'):
        parts = [p.strip() for p in re.split(splitter, text) if p.strip()]
        if len(parts) > 1:
            title, employer = parts[0], parts[1]
            break
    else:
        title, employer = text, ''
    gap = re.match(r'^(.+?)\s{2,}(.+,\s*[A-Z]{2})$', employer)
    if gap:
        employer, location = gap.group(1).strip(), gap.group(2).strip()
    return title, employer, location


def _is_heading(line):
    return bool(_SECTION.match(line.strip())) and len(line.strip()) < 40


def _employer_header(lines, index):
    """(employer, location) if the nearest non-blank line above `index` looks like
    an employer heading ("Company | City, ST"), not a bullet or wrapped sentence."""
    k = index - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k < 0:
        return None
    text = lines[k].strip()
    if (_RANGE.search(text) or _is_heading(text) or text.startswith(_BULLET) or len(text) > 100
            or not re.match(r'[A-Z0-9]', text) or text.endswith(('.', ';', ','))):
        return None
    employer, _, location = (p.strip() for p in text.partition('|'))
    if not location:
        trailing = re.match(r'^(.*?),\s*([^,]+,\s*[A-Z]{2})$', employer)
        if trailing:
            employer, location = trailing.group(1).strip(), trailing.group(2).strip()
    return (employer, location) if employer else None


def draft_from_resume(text):
    """Best-effort draft rows from plain resume text. Never trusted: every row
    carries 'issues' telling the user what to check, and nothing is saved."""
    lines = [line.rstrip() for line in str(text or '').splitlines()]
    work, education = [], []
    in_education = False
    last_employer = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _is_heading(line):
            in_education = _SECTION.match(line).group(1).lower() == 'education'
            i += 1
            continue
        match = _RANGE.search(line)
        if match and not in_education and len(work) < MAX_WORK_ROWS:
            start_m = _month_number(match.group('am'))
            start_y = int(match.group('ay'))
            current = match.group('b').lower() in ('present', 'current', 'now', 'today')
            end_m = None if current else _month_number(match.group('bm'))
            end_y = None if current else int(match.group('by'))
            head = (line[:match.start()] + ' ' + line[match.end():]).strip(' |-–—,;()')
            head_from_above = not head
            if not head:
                # Dates alone on the line: the header is the line above.
                k = i - 1
                while k >= 0 and not lines[k].strip():
                    k -= 1
                if k >= 0 and not _RANGE.search(lines[k]) and not _is_heading(lines[k]):
                    head = lines[k].strip()
            # An explicit "Title | Employer" in the head wins over a heading line above.
            above = (_employer_header(lines, i)
                     if head and not head_from_above and '|' not in head else None)
            if above:
                # Common layout: the employer sits on its own line above one or more positions.
                title, (employer, location) = head, above
                last_employer = above
            else:
                title, employer, location = _split_header(head)
                if not employer and last_employer:
                    employer, location = last_employer
            description = []
            j = i + 1
            while j < len(lines):
                cur = lines[j].strip()
                if _RANGE.search(cur) or _is_heading(cur):
                    break
                # A header-looking line directly above the next date line starts the next job.
                nxt = next((lines[k].strip() for k in range(j + 1, min(j + 3, len(lines)))
                            if lines[k].strip()), '')
                if (cur and not cur.startswith(_BULLET) and nxt and _RANGE.search(nxt)
                        and not _RANGE.search(cur)):
                    break
                if cur:
                    description.append(cur.lstrip('-•* ').strip())
                if len(description) >= 12:
                    j += 1
                    break
                j += 1
            issues = []
            if not start_m:
                issues.append(f'start month missing (resume shows {start_y})')
            if not current and not end_m:
                issues.append(f'end month missing (resume shows {end_y})')
            if not title:
                issues.append('job title not found')
            if not employer:
                issues.append('employer not found')
            work.append({
                'id': _short_id(), 'title': title[:160], 'employer': employer[:200],
                'location': location[:160], 'from': _fmt(start_m, start_y),
                'to': '' if current else _fmt(end_m, end_y), 'current': current,
                'description': ' '.join(description)[:2000], 'issues': issues, 'status': 'draft'})
            i = j
            continue
        if in_education and len(education) < MAX_EDUCATION_ROWS and _EDU_WORDS.search(line):
            low = line.lower()
            level = 'other'
            if re.search(r'\b(mba|master|m\.?s\.?|m\.?a\.?)\b', low):
                level = 'master'
            elif re.search(r'\b(bachelor|b\.?s\.?|b\.?a\.?)\b', low):
                level = 'bachelor'
            elif re.search(r'\bassociate\b', low):
                level = 'associate'
            elif re.search(r'\b(ph\.?d|doctor)', low):
                level = 'doctorate'
            year = re.search(r'\b((?:19|20)\d{2})\b', line)
            strip_year = lambda text: re.sub(r'\b(?:19|20)\d{2}\b', '', text).strip(' ,-–—')
            school = degree = field = place = ''
            if '|' in line:
                # "Degree, Field | School, City, ST" (either side may hold the school).
                left, right = [strip_year(p) for p in line.split('|', 1)]
                if _SCHOOL_WORDS.search(left) and not _SCHOOL_WORDS.search(right):
                    left, right = right, left
                degree, _, field = (p.strip() for p in left.partition(','))
                school, _, place = (p.strip() for p in right.partition(','))
            else:
                parts = [strip_year(p) for p in re.split(r'\s*,\s*|\s+[-–—]\s+', line) if p.strip()]
                school = next((p for p in parts if _SCHOOL_WORDS.search(p)), '')
                degree = next((p for p in parts if p and p != school), '')
            if not school and i + 1 < len(lines) and _SCHOOL_WORDS.search(lines[i + 1]):
                nxt = lines[i + 1]
                school = strip_year(nxt)
                year = year or re.search(r'\b((?:19|20)\d{2})\b', nxt)
                i += 1
            education.append({
                'id': _short_id(), 'level': level, 'school': school[:200], 'degree': degree[:160],
                'field': field[:160], 'location': place[:160],
                'graduated': year.group(1) if year else '',
                'issues': ['check school, degree and field'], 'status': 'draft'})
        i += 1
    return {'work_history': work, 'education': education}
