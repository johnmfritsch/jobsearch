"""Desktop session keys: a revocable, login-style key for the Claude session on
the user's own PC (backlog 19.2).

It is not an LLM provider key and this module makes no LLM call. The pattern is
auth.py's session token: only the SHA-256 of the token is stored, a key is
revoked by deleting its row, and expiry is an ISO string compared against
auto_apply.utcnow().

The raw token ('jsa_...') appears exactly once, in the create response.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, request

import auto_apply

TOKEN_PREFIX = 'jsa_'
KEY_LIFETIME_DAYS = 90
MAX_KEYS_PER_USER = 5
LABEL_LIMIT = 80


class KeyRuleError(ValueError):
    """A key rule was broken. `status` is the HTTP status to answer with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def _expiry():
    return (datetime.now(timezone.utc) + timedelta(days=KEY_LIFETIME_DAYS)).replace(
        tzinfo=None, microsecond=0).isoformat()


def create_key(db, user_id, label=None):
    label = str(label or '').strip()[:LABEL_LIMIT] or 'Desktop'
    count = db.execute('SELECT COUNT(*) FROM desktop_session_keys WHERE user_id=?',
                       (user_id,)).fetchone()[0]
    if count >= MAX_KEYS_PER_USER:
        raise KeyRuleError(f'You can have at most {MAX_KEYS_PER_USER} desktop keys. '
                        'Revoke one first', 409)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    key_id = str(uuid.uuid4())
    expires = _expiry()
    db.execute(
        'INSERT INTO desktop_session_keys '
        '(key_id,token_hash,user_id,label,created_at,expires_at) VALUES (?,?,?,?,?,?)',
        (key_id, _hash(token), user_id, label, auto_apply.utcnow(), expires))
    db.commit()
    return {'token': token, 'key_id': key_id, 'label': label, 'expires_at': expires}


def list_keys(db, user_id):
    """Never includes the hash or the token."""
    return [dict(row) for row in db.execute(
        'SELECT key_id,label,created_at,last_used_at,expires_at '
        'FROM desktop_session_keys WHERE user_id=? ORDER BY created_at DESC', (user_id,))]


def revoke_key(db, user_id, key_id):
    result = db.execute('DELETE FROM desktop_session_keys WHERE key_id=? AND user_id=?',
                        (str(key_id), user_id))
    db.commit()
    return bool(result.rowcount)


def authenticate(db, token):
    """The key's owner as {'id', 'profile_key', 'token_hash'}, or None. The user
    id comes from the key row alone."""
    token = str(token or '')
    if not token.startswith(TOKEN_PREFIX):
        return None
    row = db.execute(
        'SELECT k.token_hash AS token_hash, u.id AS id, u.profile_key AS profile_key '
        'FROM desktop_session_keys k JOIN users u ON u.id=k.user_id '
        'WHERE k.token_hash=? AND k.expires_at>? AND COALESCE(u.is_active,1)=1',
        (_hash(token), auto_apply.utcnow())).fetchone()
    return dict(row) if row else None


def touch(db, user):
    db.execute('UPDATE desktop_session_keys SET last_used_at=? WHERE token_hash=?',
               (auto_apply.utcnow(), user['token_hash']))
    db.commit()


def agent_user(db):
    """(user, None) or (None, error response) for an agent API call.

    Flag off -> 503 without looking at the header. Only `Authorization: Bearer
    jsa_...` is read; a session cookie is ignored.
    """
    if not current_app.config.get('AGENT_API_ENABLED'):
        return None, (jsonify({'error': 'The agent API is turned off'}), 503)
    header = request.headers.get('Authorization', '')
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        return None, (jsonify({'error': 'A desktop session key is required'}), 401)
    user = authenticate(db, token.strip())
    if not user:
        return None, (jsonify({'error': 'That desktop session key is missing, revoked or expired'}), 401)
    return user, None
