"""Single-session Playwright worker for confirmation-gated application forms.

The Flask app owns all durable state. This process holds at most one ephemeral
browser session, returns sanitized field/status metadata, and never submits
without a separately signed ``submit`` request whose payload says confirmed.
"""

import ipaddress
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from playwright.sync_api import sync_playwright


PORT = int(os.environ.get('APPLICATION_WORKER_PORT', '3011'))
SIGNING_SECRET = os.environ.get('APPLICATION_SIGNING_SECRET', '')
TOKEN_MAX_AGE = int(os.environ.get('APPLICATION_TOKEN_MAX_AGE', '1800'))
ALLOWLIST = {
    item.strip() for item in os.environ.get(
        'APPLICATION_ATS_ALLOWLIST',
        'ashby,zoho_recruit,workday,greenhouse,lever',
    ).split(',') if item.strip()
}
GENERIC_ENABLED = os.environ.get('APPLICATION_GENERIC_ENABLED', '').lower() == 'true'

ATS_HOSTS = (
    ('greenhouse', ('greenhouse.io',)),
    ('lever', ('lever.co',)),
    ('ashby', ('ashbyhq.com',)),
    ('zoho_recruit', ('zohorecruit.com',)),
    ('workday', ('myworkdayjobs.com', 'workday.com')),
    ('linkedin', ('linkedin.com',)),
)

ADAPTERS = {
    'greenhouse': {
        'submit_selector': '#submit_app, form button[type="submit"], form input[type="submit"]',
        'submission_supported': True,
    },
    'lever': {
        'submit_selector': 'form button[type="submit"], .application-submit button',
        'submission_supported': True,
    },
    'ashby': {
        'submit_selector': 'form button[type="submit"], form input[type="submit"]',
        'submission_supported': True,
    },
    # These platforms commonly use multi-step flows. Detection and safe field
    # preparation are useful, but final clicking remains a manual handoff until
    # a dedicated multi-step adapter has been accepted in DEV.
    'zoho_recruit': {
        'submit_selector': '',
        'submission_supported': False,
    },
    'workday': {
        'submit_selector': '',
        'submission_supported': False,
    },
}

BOT_SIGNALS = (
    'captcha', 'verify you are human', 'prove you are human', 'are you a robot',
    'security check', 'access denied', 'challenge-form', 'cf-challenge',
)
LOGIN_SIGNALS = ('sign in', 'log in', 'create an account', 'login required')
PROTECTED_SIGNALS = (
    'gender', 'race', 'ethnicity', 'disability', 'veteran', 'sexual orientation',
    'religion', 'date of birth', 'age', 'pronoun',
)

_lock = threading.Lock()
_playwright = None
_browser = None
_session = None


def detect_ats(url):
    host = (urlparse(str(url or '')).hostname or '').lower()
    for kind, suffixes in ATS_HOSTS:
        if any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes):
            return kind
    return 'unknown'


def validate_public_url(url):
    parsed = urlparse(str(url or ''))
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Application URL is invalid')
    if parsed.username or parsed.password:
        raise ValueError('Application URLs containing credentials are not allowed')
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as exc:
        raise ValueError('Application host could not be resolved') from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError('Private or local application URLs are not allowed')


def _serializer():
    if not SIGNING_SECRET:
        raise RuntimeError('Application signing secret is not configured')
    return URLSafeTimedSerializer(SIGNING_SECRET, salt='jobsearch-application-worker')


def _verify(token, body):
    try:
        signed = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except SignatureExpired as exc:
        raise PermissionError('Worker token expired') from exc
    except BadSignature as exc:
        raise PermissionError('Invalid worker token') from exc
    if (str(signed.get('attempt_id')) != str(body.get('attempt_id')) or
            signed.get('action') != body.get('action')):
        raise PermissionError('Worker token does not match the request')
    return signed


def _close_session():
    global _session
    if _session:
        try:
            _session['context'].close()
        except Exception:
            pass
    _session = None


def _browser_instance():
    global _playwright, _browser
    if _playwright is None:
        _playwright = sync_playwright().start()
    if _browser is None or not _browser.is_connected():
        _browser = _playwright.chromium.launch(
            headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    return _browser


def _page_text(page):
    return page.locator('body').inner_text(timeout=5000).lower()[:12000]


def _field_label(field):
    try:
        label = field.evaluate("""el => {
          const direct = el.labels && el.labels.length ? el.labels[0].innerText : '';
          const wrapped = el.closest('label');
          return (direct || (wrapped && wrapped.innerText) || el.getAttribute('aria-label') ||
                  el.getAttribute('placeholder') || el.name || el.id || '').trim();
        }""")
    except Exception:
        label = ''
    return ' '.join(str(label).split())[:300]


def _profile_value(label, field_type, profile, answers):
    text = label.casefold()
    for question, answer in answers.items():
        if question.casefold() == text:
            return answer
    mappings = (
        (('full name', 'legal name', 'your name'), 'full_name'),
        (('email',), 'email'),
        (('phone', 'mobile'), 'phone'),
        (('street', 'address'), 'address'),
        (('city',), 'city'),
        (('state', 'province'), 'state'),
        (('postal', 'zip'), 'postal_code'),
        (('country',), 'country'),
        (('linkedin',), 'linkedin_url'),
        (('portfolio', 'website', 'personal site'), 'portfolio_url'),
        (('work authorization', 'authorized to work'), 'work_authorization'),
        (('sponsor', 'sponsorship'), 'sponsorship'),
        (('relocat',), 'relocation'),
        (('travel',), 'travel'),
    )
    for phrases, key in mappings:
        if any(phrase in text for phrase in phrases):
            return profile.get(key)
    if field_type == 'email':
        return profile.get('email')
    if field_type == 'tel':
        return profile.get('phone')
    return None


def _inspect_and_fill(page, payload):
    text = _page_text(page)
    if any(signal in text for signal in BOT_SIGNALS):
        return {'state': 'blocked_captcha',
                'message': 'The site presented a CAPTCHA or bot check'}
    if any(signal in text for signal in LOGIN_SIGNALS) and page.locator(
            'input[type="password"]').count():
        return {'state': 'needs_login', 'message': 'The site requires a login'}

    profile = payload.get('profile') or {}
    answers = payload.get('answers') or {}
    artifact_paths = payload.get('artifact_paths') or {}
    filled = []
    unanswered = []
    fields = page.locator('input:not([type=hidden]), textarea, select')
    for index in range(min(fields.count(), 250)):
        field = fields.nth(index)
        try:
            if not field.is_visible() or field.is_disabled():
                continue
            field_type = (field.get_attribute('type') or field.evaluate(
                'el => el.tagName.toLowerCase()')).lower()
            label = _field_label(field) or f'Field {index + 1}'
            lower = label.casefold()
            required = bool(field.get_attribute('required') is not None or
                            field.get_attribute('aria-required') == 'true')
            if any(signal in lower for signal in PROTECTED_SIGNALS):
                if required:
                    unanswered.append({'label': label, 'reason': 'protected_or_voluntary'})
                continue
            if field_type == 'file':
                kind = 'cover_letter' if 'cover' in lower else 'resume' if 'resume' in lower or 'cv' in lower else None
                path = artifact_paths.get(kind) if kind else None
                if path:
                    field.set_input_files(path)
                    filled.append({'label': label, 'source': kind})
                elif required:
                    unanswered.append({'label': label, 'reason': 'file_required'})
                continue
            if field_type in {'checkbox', 'radio', 'submit', 'button', 'reset'}:
                if required and field_type in {'checkbox', 'radio'}:
                    unanswered.append({'label': label, 'reason': 'confirmation_required'})
                continue
            value = _profile_value(label, field_type, profile, answers)
            if value not in (None, ''):
                if field_type == 'select':
                    try:
                        field.select_option(label=str(value))
                    except Exception:
                        try:
                            field.select_option(value=str(value))
                        except Exception:
                            unanswered.append({'label': label, 'reason': 'choice_not_matched'})
                            continue
                else:
                    field.fill(str(value))
                filled.append({'label': label, 'source': 'saved_profile_or_answer'})
            elif required:
                unanswered.append({'label': label, 'reason': 'answer_required'})
        except Exception:
            unanswered.append({'label': _field_label(field) or f'Field {index + 1}',
                               'reason': 'could_not_fill'})
    if unanswered:
        return {
            'state': 'needs_user_input',
            'message': f'{len(unanswered)} required field(s) need your review',
            'metadata': {'filled_fields': filled, 'unanswered_fields': unanswered},
        }
    return {
        'state': 'ready_for_review',
        'message': f'{len(filled)} field(s) prepared for final review',
        'metadata': {'filled_fields': filled, 'unanswered_fields': []},
    }


def prepare(payload):
    global _session
    url = payload.get('url')
    validate_public_url(url)
    requested_kind = detect_ats(url)
    if requested_kind == 'linkedin':
        return {'state': 'manual_required', 'ats_kind': 'linkedin',
                'message': 'LinkedIn automation is not supported'}
    _close_session()
    context = _browser_instance().new_context(
        user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 Chrome/126 Safari/537.36'))
    page = context.new_page()
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(1500)
        resolved = page.url
        kind = detect_ats(resolved)
        if kind == 'linkedin':
            context.close()
            return {'state': 'manual_required', 'ats_kind': kind,
                    'resolved_url': resolved,
                    'message': 'LinkedIn automation is not supported'}
        if kind not in ALLOWLIST and not (kind == 'unknown' and GENERIC_ENABLED):
            context.close()
            return {'state': 'unsupported_site', 'ats_kind': kind,
                    'resolved_url': resolved,
                    'message': 'The destination is not on the automation allowlist'}
        result = _inspect_and_fill(page, payload)
        result.update({'resolved_url': resolved, 'ats_kind': kind})
        _session = {
            'attempt_id': str(payload['attempt_id']), 'context': context,
            'page': page, 'created_at': time.time(), 'result': result,
            'ats_kind': kind,
        }
        return result
    except Exception:
        context.close()
        raise


def submit(payload):
    global _session
    if not payload.get('confirmed'):
        raise PermissionError('Submission confirmation is required')
    if not _session or _session['attempt_id'] != str(payload.get('attempt_id')):
        return {'state': 'submission_failed', 'error_code': 'session_expired',
                'message': 'The prepared browser session expired; prepare the form again'}
    if time.time() - _session['created_at'] > 1800:
        _close_session()
        return {'state': 'submission_failed', 'error_code': 'session_expired',
                'message': 'The prepared browser session expired; prepare the form again'}
    page = _session['page']
    adapter = ADAPTERS.get(_session.get('ats_kind')) or {}
    if not adapter.get('submission_supported'):
        return {'state': 'submission_failed', 'error_code': 'manual_submit_required',
                'message': 'This ATS requires manual final submission'}
    before = page.url
    buttons = page.locator(adapter['submit_selector'])
    if not buttons.count():
        return {'state': 'submission_failed', 'error_code': 'submit_not_found',
                'message': 'A safe application submit control was not found'}
    buttons.last.click()
    try:
        page.wait_for_load_state('domcontentloaded', timeout=30000)
    except Exception:
        page.wait_for_timeout(2500)
    text = _page_text(page)
    confirmation = next((line.strip() for line in text.splitlines()
                         if any(term in line for term in (
                             'application submitted', 'thank you for applying',
                             'application received', 'successfully submitted'))), '')
    if not confirmation and page.url == before:
        return {'state': 'submission_failed', 'error_code': 'confirmation_not_detected',
                'message': 'The site did not provide a recognizable submission confirmation'}
    result = {
        'state': 'submitted', 'message': 'Application submitted',
        'confirmation_url': page.url,
        'confirmation_text': confirmation[:1000] or 'Application destination changed after submit',
    }
    _close_session()
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'status': 'ok', 'session_active': bool(_session),
                                 'allowlist': sorted(ALLOWLIST),
                                 'generic_enabled': GENERIC_ENABLED})
        else:
            self.send_json(404, {'error': 'not found'})

    def do_POST(self):
        if self.path != '/attempt':
            self.send_json(404, {'error': 'not found'})
            return
        try:
            length = min(int(self.headers.get('Content-Length', '0')), 2_000_000)
            body = json.loads(self.rfile.read(length))
            token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
            _verify(token, body)
            with _lock:
                result = prepare(body) if body.get('action') == 'prepare' else submit(body)
            self.send_json(200, result)
        except PermissionError as exc:
            self.send_json(403, {'error': str(exc)})
        except ValueError as exc:
            self.send_json(400, {'error': str(exc)})
        except Exception as exc:
            self.send_json(500, {'error': str(exc)[:500]})

    def send_json(self, status, payload):
        content = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, _format, *_args):
        pass


if __name__ == '__main__':
    print(f'Application worker listening on localhost:{PORT}', flush=True)
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
