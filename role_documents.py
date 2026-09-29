"""User-scoped, versioned document library for search roles."""

import hashlib
import os
import re
import uuid
from pathlib import Path

import auto_apply


MAX_PDF_BYTES = 12 * 1024 * 1024


def _safe_name(value):
    value = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value or '')).strip('._')
    return value[:100] or 'document'


def resolve_role(db, user_id, preset_id=None, role_name=None):
    row = None
    if preset_id not in (None, ''):
        try:
            preset_id = int(preset_id)
        except (TypeError, ValueError):
            raise ValueError('Choose a valid role') from None
        row = db.execute(
            'SELECT id,name FROM search_presets WHERE id=? AND user_id=?',
            (preset_id, user_id)).fetchone()
        if not row:
            raise LookupError('Role not found')
    elif role_name:
        row = db.execute(
            'SELECT id,name FROM search_presets WHERE user_id=? AND name=? COLLATE NOCASE',
            (user_id, str(role_name).strip())).fetchone()
    if row:
        return {'preset_id': row['id'], 'role_key': f"preset:{row['id']}",
                'role_name': row['name']}
    name = str(role_name or 'Default').strip()[:80] or 'Default'
    if name.casefold() == 'default':
        return {'preset_id': None, 'role_key': 'default', 'role_name': 'Default'}
    key = hashlib.sha256(name.casefold().encode('utf-8')).hexdigest()[:24]
    return {'preset_id': None, 'role_key': 'name:' + key, 'role_name': name}


def _next_version(db, user_id, role_key, kind, file_format):
    row = db.execute(
        'SELECT COALESCE(MAX(version),0)+1 FROM role_documents '
        'WHERE user_id=? AND role_key=? AND kind=? AND format=?',
        (user_id, role_key, kind, file_format)).fetchone()
    return int(row[0])


def _current(db, user_id, role_key, kind, file_format):
    return db.execute(
        'SELECT * FROM role_documents WHERE user_id=? AND role_key=? '
        'AND kind=? AND format=? AND is_current=1 ORDER BY version DESC LIMIT 1',
        (user_id, role_key, kind, file_format)).fetchone()


def save_text(db, user_id, role, kind, text, source='search_setup'):
    if kind not in {'resume', 'cover_letter'}:
        raise ValueError('Choose resume or cover letter')
    clean = str(text or '').strip() + '\n'
    if not clean.strip():
        raise ValueError('Document text is required')
    current = _current(db, user_id, role['role_key'], kind, 'txt')
    if current and str(current['content_text'] or '').strip() == clean.strip():
        db.execute(
            'UPDATE role_documents SET role_name=?,preset_id=? WHERE user_id=? '
            'AND role_key=?',
            (role['role_name'], role['preset_id'], user_id, role['role_key']))
        db.commit()
        return dict(db.execute('SELECT * FROM role_documents WHERE id=?',
                               (current['id'],)).fetchone())
    now = auto_apply.utcnow()
    version = _next_version(db, user_id, role['role_key'], kind, 'txt')
    db.execute(
        'UPDATE role_documents SET is_current=0,superseded_at=? WHERE user_id=? '
        'AND role_key=? AND kind=? AND format=? AND is_current=1',
        (now, user_id, role['role_key'], kind, 'txt'))
    document_id = str(uuid.uuid4())
    db.execute(
        'INSERT INTO role_documents '
        '(id,user_id,preset_id,role_key,role_name,kind,format,version,is_current,'
        'source,file_name,mime_type,content_text,created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (document_id, user_id, role['preset_id'], role['role_key'],
         role['role_name'], kind, 'txt', version, 1, source,
         f"{_safe_name(role['role_name'])}_{kind}_v{version}.txt",
         'text/plain; charset=utf-8', clean, now))
    db.commit()
    return dict(db.execute('SELECT * FROM role_documents WHERE id=?',
                           (document_id,)).fetchone())


def sync_resume_text(db, user_id, preset_id, role_name, text, source='search_setup'):
    role = resolve_role(db, user_id, preset_id, role_name)
    return save_text(db, user_id, role, 'resume', text, source)


def library_root(database_path):
    root = Path(database_path).resolve().parent / 'role_documents'
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path(root, relative_path):
    root = Path(root).resolve()
    candidate = (root / str(relative_path)).resolve()
    if os.path.commonpath((str(root), str(candidate))) != str(root):
        raise ValueError('Invalid role document path')
    return candidate


def save_pdf(db, database_path, user_id, role, kind, content, original_name,
             source='uploaded'):
    if kind not in {'resume', 'cover_letter'}:
        raise ValueError('Choose resume or cover letter')
    if not isinstance(content, bytes) or not content.startswith(b'%PDF-'):
        raise ValueError('Upload a valid PDF file')
    if len(content) > MAX_PDF_BYTES:
        raise ValueError('PDF files are limited to 12 MB')
    version = _next_version(db, user_id, role['role_key'], kind, 'pdf')
    document_id = str(uuid.uuid4())
    file_name = (f"{_safe_name(role['role_name'])}_{kind}_v{version}.pdf")
    relative = Path(str(user_id)) / hashlib.sha256(
        role['role_key'].encode()).hexdigest()[:24] / kind / file_name
    root = library_root(database_path)
    target = safe_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    with open(temp, 'wb') as handle:
        handle.write(content)
    os.replace(temp, target)
    digest = hashlib.sha256(content).hexdigest()
    now = auto_apply.utcnow()
    try:
        db.execute(
            'UPDATE role_documents SET is_current=0,superseded_at=? WHERE user_id=? '
            'AND role_key=? AND kind=? AND format=? AND is_current=1',
            (now, user_id, role['role_key'], kind, 'pdf'))
        db.execute(
            'INSERT INTO role_documents '
            '(id,user_id,preset_id,role_key,role_name,kind,format,version,is_current,'
            'source,file_name,relative_path,sha256,mime_type,created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (document_id, user_id, role['preset_id'], role['role_key'],
             role['role_name'], kind, 'pdf', version, 1, source, file_name,
             relative.as_posix(), digest, 'application/pdf', now))
        db.commit()
    except Exception:
        db.rollback()
        if target.exists():
            target.unlink()
        raise
    return dict(db.execute('SELECT * FROM role_documents WHERE id=?',
                           (document_id,)).fetchone())


def list_documents(db, user_id, role):
    rows = db.execute(
        'SELECT id,role_name,kind,format,version,is_current,source,file_name,'
        'mime_type,LENGTH(content_text) AS text_length,created_at '
        'FROM role_documents WHERE user_id=? AND role_key=? '
        'ORDER BY kind,format,version DESC',
        (user_id, role['role_key'])).fetchall()
    return {'role': role, 'documents': [dict(row) for row in rows]}


def get_document(db, user_id, document_id, role_key=None):
    query = 'SELECT * FROM role_documents WHERE id=? AND user_id=?'
    args = [str(document_id), user_id]
    if role_key:
        query += ' AND role_key=?'
        args.append(role_key)
    return db.execute(query, args).fetchone()


def make_current(db, user_id, document_id):
    row = get_document(db, user_id, document_id)
    if not row:
        raise LookupError('Role document not found')
    now = auto_apply.utcnow()
    db.execute(
        'UPDATE role_documents SET is_current=0,superseded_at=? WHERE user_id=? '
        'AND role_key=? AND kind=? AND format=? AND is_current=1',
        (now, user_id, row['role_key'], row['kind'], row['format']))
    db.execute(
        'UPDATE role_documents SET is_current=1,superseded_at=NULL WHERE id=?',
        (row['id'],))
    db.commit()
    return dict(db.execute('SELECT * FROM role_documents WHERE id=?',
                           (row['id'],)).fetchone())
