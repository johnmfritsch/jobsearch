"""Authentication for JobSearch — the GardenBuddy pattern, with two additions.

1. Sessions are ALSO recorded server-side in the `sessions` table. Flask's
   signed-cookie session cannot be revoked: if an account is compromised there
   is no way to force a logout before the cookie expires. Every request
   re-validates the cookie's token against that table, so deleting the row ends
   the session immediately. This is the one piece of the pre-re-platform
   auth_store.py worth keeping.

2. Login is rate limited. Neither the old JobSearch nor GardenBuddy limited
   failed logins — only the password-reset path was throttled.

Password hashing is werkzeug's, not the old pbkdf2_sha256$... format. Nothing
needs to verify a legacy hash: migrated profiles land with an unusable
placeholder and establish a real password through the reset flow.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from html import escape

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from database import request_db as get_db

auth_bp = Blueprint('auth', __name__)

SESSION_LIFETIME_DAYS = 30
RESET_TOKEN_MAX_AGE = 300        # seconds — 5 minutes, matching GardenBuddy
RESET_EMAIL_COOLDOWN = 600       # seconds — 1 reset email per address / 10 min
LOGIN_WINDOW_SECONDS = 900       # 15 minutes
LOGIN_MAX_ATTEMPTS = 8           # failures per (email, ip) inside that window
MIN_PASSWORD_LENGTH = 8


# ── session plumbing ─────────────────────────────────────────────────────────

def _new_session_token(user_id):
    """Mint a server-side session and return its raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute(
        'INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)',
        (hashlib.sha256(token.encode()).hexdigest(), user_id,
         (datetime.utcnow() + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()))
    db.commit()
    return token


def _revoke(token):
    if not token:
        return
    db = get_db()
    db.execute('DELETE FROM sessions WHERE token_hash = ?',
               (hashlib.sha256(token.encode()).hexdigest(),))
    db.commit()


def _sign_in(user):
    """Establish both halves of the session: Flask cookie + server-side row."""
    session.clear()
    session.permanent = True
    session['user_id'] = user['id']
    session['display_name'] = user['display_name']
    session['token'] = _new_session_token(user['id'])


def current_user():
    """Resolve the signed-in user, or None. Cached per request in `g`.

    Requires BOTH a valid Flask cookie and a live server-side session row, so a
    deleted row logs the user out on their very next request.
    """
    if 'current_user' in g:
        return g.current_user
    g.current_user = None
    user_id, token = session.get('user_id'), session.get('token')
    if user_id and token:
        row = get_db().execute(
            'SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id '
            'WHERE s.token_hash = ? AND s.user_id = ? AND s.expires_at > ? '
            'AND COALESCE(u.is_active, 1) = 1',
            (hashlib.sha256(token.encode()).hexdigest(), user_id,
             datetime.utcnow().isoformat())).fetchone()
        if row is None:
            session.clear()
        else:
            g.current_user = row
    return g.current_user


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user() is None:
            # PrefixMiddleware strips the app's URL prefix off PATH_INFO before
            # Flask builds the request, so request.path alone is missing it
            # (e.g. '/dashboard' instead of '/JobSearch/dashboard'). Without
            # script_root, redirect(next) after login lands on the bare path,
            # which PrefixMiddleware then 404s as a plain-text page.
            return redirect(url_for('auth.login', next=request.script_root + request.path))
        return f(*args, **kwargs)
    return decorated


# ── login rate limiting ──────────────────────────────────────────────────────

def _client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    return (forwarded.split(',')[0].strip() if forwarded
            else (request.remote_addr or ''))


def _login_blocked(email, ip):
    since = (datetime.utcnow() - timedelta(seconds=LOGIN_WINDOW_SECONDS)).isoformat()
    count = get_db().execute(
        'SELECT count(*) FROM login_attempts '
        'WHERE email = ? AND ip = ? AND attempted_at > ?',
        (email, ip, since)).fetchone()[0]
    return count >= LOGIN_MAX_ATTEMPTS


def _record_failure(email, ip):
    db = get_db()
    db.execute('INSERT INTO login_attempts (email, ip, attempted_at) VALUES (?, ?, ?)',
               (email, ip, datetime.utcnow().isoformat()))
    db.commit()


def _clear_failures(email, ip):
    db = get_db()
    db.execute('DELETE FROM login_attempts WHERE email = ? AND ip = ?', (email, ip))
    db.commit()


# ── password reset tokens ────────────────────────────────────────────────────

def _reset_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='password-reset')


def _pw_fingerprint(password_hash):
    """Short digest of the CURRENT hash; a token dies when the password changes."""
    return hashlib.sha256((password_hash or '').encode()).hexdigest()[:8]


def _external_base():
    from config import Config
    cfg = Config.get_config()
    base = ('https://fritsch-nas.myasustor.com:7788' if cfg.ENV == 'development'
            else 'https://fritsch-nas.myasustor.com:7787')
    return f'{base}{cfg.URL_PREFIX}'


def _send_reset_email(to_addr, display_name, reset_url):
    from email_utils import build_html_email, load_gmail_credentials, send_email
    sender, _ = load_gmail_credentials()
    body_html = f'''
<p>Hi {escape(display_name)},</p>
<p>We received a request to set or reset your JobSearch password.
   This link is valid for <b>5&nbsp;minutes</b>:</p>
<p style="margin:24px 0;">
  <a href="{reset_url}" style="background:#2d5f8a;color:#ffffff;padding:12px 24px;
     border-radius:6px;text-decoration:none;font-weight:bold;">Set my password</a>
</p>
<p style="font-size:13px;color:#666;">If the button doesn't work, copy this link
   into your browser:<br>{reset_url}</p>
<p style="font-size:13px;color:#666;">If you didn't request this, you can ignore
   this email &mdash; your password is unchanged.</p>
'''
    send_email(build_html_email(sender, to_addr, 'JobSearch password reset', body_html))


# ── routes ───────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('portal.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        ip = _client_ip()
        if _login_blocked(email, ip):
            flash('Too many failed sign-in attempts. Try again in 15 minutes.', 'error')
            return render_template('login.html', email=email), 429
        user = get_db().execute(
            'SELECT * FROM users WHERE LOWER(email) = ? AND COALESCE(is_active, 1) = 1',
            (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            _clear_failures(email, ip)
            _sign_in(user)
            nxt = request.args.get('next', '')
            # Only ever redirect within this app — an absolute or scheme-relative
            # "next" would make this an open redirect.
            if nxt.startswith('/') and not nxt.startswith('//'):
                return redirect(nxt)
            return redirect(url_for('portal.dashboard'))
        _record_failure(email, ip)
        # Same message either way — never reveal whether the address exists.
        flash('Invalid email or password.', 'error')
        return render_template('login.html', email=email), 401
    return render_template('login.html', email='')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    _revoke(session.get('token'))
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user():
        return redirect(url_for('portal.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        display_name = request.form.get('display_name', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('password_confirmation', '')
        error = None
        if not email or '@' not in email:
            error = 'Enter a valid email address.'
        elif not display_name:
            error = 'Enter a display name.'
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f'Use a password of at least {MIN_PASSWORD_LENGTH} characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        if error:
            flash(error, 'error')
            return render_template('register.html', email=email,
                                   display_name=display_name), 400
        db = get_db()
        if db.execute('SELECT 1 FROM users WHERE LOWER(email) = ?', (email,)).fetchone():
            flash('That email is already registered. Try signing in instead.', 'error')
            return render_template('register.html', email=email,
                                   display_name=display_name), 400
        cur = db.execute(
            'INSERT INTO users (email, password_hash, display_name) VALUES (?, ?, ?)',
            (email, generate_password_hash(password), display_name))
        user_id = cur.lastrowid
        db.execute('UPDATE users SET profile_key = ? WHERE id = ?',
                   (f'account-{user_id}', user_id))
        _seed_profile(db, user_id)
        db.commit()
        _sign_in(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())
        return redirect(url_for('portal.dashboard'))
    return render_template('register.html', email='', display_name='')


def _seed_profile(db, user_id):
    """New accounts start with API sources on and an empty resume.

    The built-in sample remains available without provider credentials. A live
    run will direct the user to add credentials or turn a provider off. An empty
    resume is stored as empty rather than as the old instructional placeholder,
    which the run guard used to mistake for real resume text.
    """
    import json
    import os
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'default_config.json')
    try:
        with open(default_path, 'r', encoding='utf-8') as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        config = {}
    config['sources'] = {'adzuna': True, 'serpapi': True, 'indeed': False}
    payload = json.dumps(config)
    db.execute(
        'INSERT INTO user_profiles (user_id, config_json, test_config_json, '
        'resume_text, onboarding_state) VALUES (?, ?, ?, ?, ?)',
        (user_id, payload, payload, '', 'pending'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user():
        return redirect(url_for('portal.dashboard'))
    sent = False
    if request.method == 'POST':
        # Set before the lookup: the response is identical whether or not the
        # address exists, so this page cannot be used to enumerate accounts.
        sent = True
        email = request.form.get('email', '').strip().lower()
        if email:
            db = get_db()
            user = db.execute(
                'SELECT * FROM users WHERE LOWER(email) = ? AND COALESCE(is_active, 1) = 1',
                (email,)).fetchone()
            if user:
                now = datetime.utcnow()
                row = db.execute(
                    'SELECT requested_at FROM password_reset_requests WHERE email = ?',
                    (email,)).fetchone()
                on_cooldown = False
                if row:
                    try:
                        last = datetime.fromisoformat(row['requested_at'])
                        on_cooldown = (now - last).total_seconds() < RESET_EMAIL_COOLDOWN
                    except (TypeError, ValueError):
                        pass
                if not on_cooldown:
                    token = _reset_serializer().dumps(
                        {'uid': user['id'],
                         'fp': _pw_fingerprint(user['password_hash'])})
                    try:
                        _send_reset_email(user['email'], user['display_name'],
                                          f'{_external_base()}/reset-password/{token}')
                        # Written only on a real send, so probing unknown
                        # addresses cannot grow this table.
                        db.execute(
                            'INSERT OR REPLACE INTO password_reset_requests '
                            '(email, requested_at) VALUES (?, ?)',
                            (email, now.isoformat()))
                        db.commit()
                    except Exception:
                        current_app.logger.exception('reset email failed')
                        # Swallowed on purpose — a send failure must not change
                        # the response and reveal that the address is real.
    return render_template('forgot_password.html', sent=sent)


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user():
        return redirect(url_for('portal.dashboard'))
    try:
        payload = _reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        flash('That link has expired. Request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))
    except BadSignature:
        flash('That link is not valid. Request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ? AND COALESCE(is_active, 1) = 1',
                      (payload.get('uid'),)).fetchone()
    # The fingerprint check is what makes the token single-use: once the
    # password changes, the hash changes, and this stops matching.
    if not user or payload.get('fp') != _pw_fingerprint(user['password_hash']):
        flash('That link has already been used. Request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('password_confirmation', '')
        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f'Use a password of at least {MIN_PASSWORD_LENGTH} characters.', 'error')
            return render_template('reset_password.html', token=token), 400
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token), 400
        db.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                   (generate_password_hash(password), user['id']))
        # Any other live session for this account dies with the password.
        db.execute('DELETE FROM sessions WHERE user_id = ?', (user['id'],))
        db.execute('DELETE FROM password_reset_requests WHERE email = ?',
                   (user['email'],))
        db.commit()
        flash('Password set. You can sign in now.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token)
