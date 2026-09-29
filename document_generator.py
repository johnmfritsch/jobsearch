"""Truth-constrained multi-provider generation and local DOCX/PDF rendering."""

import hashlib
import json
import os
import re
import shutil
import textwrap
import time
import urllib.error
import urllib.request
import zipfile
from html import escape
from pathlib import Path

import auto_apply


MAX_OUTPUT_TOKENS = 4500

PROVIDERS = {
    'openai': {
        'label': 'OpenAI',
        'model': 'gpt-5.6-terra',
        'input_usd_per_million': 2.00,
        'output_usd_per_million': 12.00,
    },
    'anthropic': {
        'label': 'Anthropic Claude',
        'model': 'claude-sonnet-5',
        # Introductory Sonnet 5 pricing through 2026-08-31.
        'input_usd_per_million': 2.00,
        'output_usd_per_million': 10.00,
    },
    'xai': {
        'label': 'xAI Grok',
        'model': 'grok-4.5',
        'input_usd_per_million': 2.00,
        'output_usd_per_million': 6.00,
    },
}

# Backward-compatible name used by older callers and release tests.
MODEL = PROVIDERS['openai']['model']

OUTPUT_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'tailored_resume_text': {'type': 'string'},
        'cover_letter_text': {'type': 'string'},
        'changes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'summary': {'type': 'string'},
                    'resume_evidence': {'type': 'string'},
                },
                'required': ['summary', 'resume_evidence'],
            },
        },
        'claim_trace': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'generated_claim': {'type': 'string'},
                    'resume_evidence': {'type': 'string'},
                },
                'required': ['generated_claim', 'resume_evidence'],
            },
        },
    },
    'required': ['tailored_resume_text', 'cover_letter_text', 'changes', 'claim_trace'],
}

SYSTEM_PROMPT = """You tailor application documents using only facts explicitly present in
the supplied master resume and application profile. You may reorder, shorten, clarify, and
emphasize existing experience. Never invent or infer an employer, date, credential, degree,
skill, accomplishment, metric, responsibility, work authorization fact, or personal detail.
Every entry in changes and claim_trace must quote a short, exact passage from the master
resume as evidence. claim_trace must enumerate every substantive experience, skill,
credential, accomplishment, and quantitative claim in the generated documents.
If the source lacks a desired qualification, omit it rather than filling the gap. Return plain
text suitable for a professional resume and cover letter. Do not add Markdown fences."""


class GenerationError(RuntimeError):
    pass


def _token_estimate(text):
    return max(1, (len(str(text)) + 3) // 4)


def provider_info(provider):
    value = PROVIDERS.get(str(provider or '').strip().lower())
    if not value:
        raise GenerationError('Choose OpenAI, Anthropic Claude, or xAI Grok')
    return value


def _requested(generate_resume=True, generate_cover_letter=True):
    requested = []
    if generate_resume:
        requested.append('resume')
    if generate_cover_letter:
        requested.append('cover_letter')
    if not requested:
        raise GenerationError('Select resume, cover letter, or both')
    return requested


def _output_schema(requested):
    schema = json.loads(json.dumps(OUTPUT_SCHEMA))
    if 'resume' not in requested:
        schema['properties'].pop('tailored_resume_text')
        schema['required'].remove('tailored_resume_text')
    if 'cover_letter' not in requested:
        schema['properties'].pop('cover_letter_text')
        schema['required'].remove('cover_letter_text')
    return schema


def _max_output_tokens(requested):
    if len(requested) == 2:
        return MAX_OUTPUT_TOKENS
    return 3200 if 'resume' in requested else 2200


def estimate_cost(master_resume, job, profile, provider='openai',
                  generate_resume=True, generate_cover_letter=True):
    info = provider_info(provider)
    requested = _requested(generate_resume, generate_cover_letter)
    output_tokens = _max_output_tokens(requested)
    prompt = json.dumps({'master_resume': master_resume, 'job': job, 'profile': profile},
                        ensure_ascii=False)
    input_tokens = _token_estimate(SYSTEM_PROMPT + prompt)
    return {
        'provider': provider,
        'provider_label': info['label'],
        'model': info['model'],
        'estimated_input_tokens': input_tokens,
        'generated_documents': requested,
        'estimated_output_tokens': output_tokens,
        'estimated_cost_usd': round(
            input_tokens * info['input_usd_per_million'] / 1_000_000 +
            output_tokens * info['output_usd_per_million'] / 1_000_000, 4),
    }


def _response_text(payload):
    if isinstance(payload.get('output_text'), str):
        return payload['output_text']
    for item in payload.get('output', []):
        for content in item.get('content', []):
            if content.get('type') == 'output_text' and isinstance(content.get('text'), str):
                return content['text']
    raise GenerationError('OpenAI returned no document content')


def _user_payload(master_resume, job, profile, requested):
    return {
        'master_resume': master_resume,
        'application_profile': profile,
        'generate': requested,
        'job': {
            key: job.get(key) for key in (
                'title', 'company', 'location', 'description', 'salary',
                'remote_status', 'url', 'apply_url', 'search_role'
            )
        },
    }


def _read_json_response(request, provider_label):
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode('utf-8'))
            detail = body.get('error')
            if isinstance(detail, dict):
                detail = detail.get('message')
        except Exception:
            detail = None
        raise GenerationError(detail or f'{provider_label} request failed ({exc.code})') from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise GenerationError(f'{provider_label} could not be reached') from exc


def _call_openai(api_key, master_resume, job, profile, requested=None):
    info = PROVIDERS['openai']
    requested = requested or ['resume', 'cover_letter']
    user_payload = _user_payload(master_resume, job, profile, requested)
    request_payload = {
        'model': info['model'],
        'store': False,
        'input': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps(user_payload, ensure_ascii=False)},
        ],
        'max_output_tokens': _max_output_tokens(requested),
        'text': {
            'format': {
                'type': 'json_schema',
                'name': 'tailored_application_documents',
                'strict': True,
                'schema': _output_schema(requested),
            }
        },
    }
    request = urllib.request.Request(
        'https://api.openai.com/v1/responses',
        data=json.dumps(request_payload).encode('utf-8'),
        headers={
            'Authorization': 'Bearer ' + api_key,
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    response_payload = _read_json_response(request, 'OpenAI')
    try:
        generated = json.loads(_response_text(response_payload))
    except (TypeError, ValueError) as exc:
        raise GenerationError('OpenAI returned invalid structured content') from exc
    usage = response_payload.get('usage') or {}
    input_tokens = int(usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('output_tokens') or 0)
    return generated, _usage(
        'openai', input_tokens, output_tokens, master_resume, job, profile,
        requested)


def _call_anthropic(api_key, master_resume, job, profile, requested=None):
    info = PROVIDERS['anthropic']
    requested = requested or ['resume', 'cover_letter']
    request_payload = {
        'model': info['model'],
        'max_tokens': _max_output_tokens(requested),
        'system': SYSTEM_PROMPT,
        'messages': [{'role': 'user', 'content': json.dumps(
            _user_payload(master_resume, job, profile, requested), ensure_ascii=False)}],
        'output_config': {
            'format': {
                'type': 'json_schema',
                'schema': _output_schema(requested),
            },
        },
    }
    request = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=json.dumps(request_payload).encode('utf-8'),
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        }, method='POST')
    payload = _read_json_response(request, 'Anthropic Claude')
    try:
        text = ''.join(item.get('text', '') for item in payload.get('content', [])
                       if item.get('type') == 'text')
        generated = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise GenerationError('Anthropic Claude returned invalid structured content') from exc
    usage = payload.get('usage') or {}
    return generated, _usage(
        'anthropic', int(usage.get('input_tokens') or 0),
        int(usage.get('output_tokens') or 0), master_resume, job, profile,
        requested)


def _call_xai(api_key, master_resume, job, profile, requested=None):
    info = PROVIDERS['xai']
    requested = requested or ['resume', 'cover_letter']
    request_payload = {
        'model': info['model'],
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps(
                _user_payload(master_resume, job, profile, requested), ensure_ascii=False)},
        ],
        'max_tokens': _max_output_tokens(requested),
        'response_format': {
            'type': 'json_schema',
            'json_schema': {
                'name': 'tailored_application_documents',
                'strict': True,
                'schema': _output_schema(requested),
            },
        },
    }
    request = urllib.request.Request(
        'https://api.x.ai/v1/chat/completions',
        data=json.dumps(request_payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + api_key,
                 'Content-Type': 'application/json'}, method='POST')
    payload = _read_json_response(request, 'xAI Grok')
    try:
        generated = json.loads(payload['choices'][0]['message']['content'])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GenerationError('xAI Grok returned invalid structured content') from exc
    usage = payload.get('usage') or {}
    return generated, _usage(
        'xai', int(usage.get('prompt_tokens') or 0),
        int(usage.get('completion_tokens') or 0), master_resume, job, profile,
        requested)


def _usage(provider, input_tokens, output_tokens, master_resume, job, profile,
           requested):
    info = provider_info(provider)
    estimate = estimate_cost(
        master_resume, job, profile, provider,
        'resume' in requested, 'cover_letter' in requested)
    return {
        'provider': provider,
        'model': info['model'],
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'estimated_cost_usd': estimate['estimated_cost_usd'],
        'actual_cost_usd': round(
            input_tokens * info['input_usd_per_million'] / 1_000_000 +
            output_tokens * info['output_usd_per_million'] / 1_000_000, 4),
    }


def _call_provider(provider, api_key, master_resume, job, profile, requested):
    return {
        'openai': _call_openai,
        'anthropic': _call_anthropic,
        'xai': _call_xai,
    }[provider](api_key, master_resume, job, profile, requested)


def _normalize(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def _validate(generated, master_resume, requested=None):
    requested = requested or ['resume', 'cover_letter']
    if not isinstance(generated, dict):
        raise GenerationError('Generated document payload is invalid')
    resume = str(generated.get('tailored_resume_text') or '').strip()
    cover = str(generated.get('cover_letter_text') or '').strip()
    changes = generated.get('changes')
    claim_trace = generated.get('claim_trace')
    if (('resume' in requested and not resume) or
            ('cover_letter' in requested and not cover) or
            not isinstance(changes, list) or not isinstance(claim_trace, list)):
        raise GenerationError('Generated documents were incomplete')
    source = _normalize(master_resume)
    cleaned_changes = []
    for item in changes[:100]:
        if not isinstance(item, dict):
            raise GenerationError('Generated change evidence was invalid')
        summary = str(item.get('summary') or '').strip()
        evidence = str(item.get('resume_evidence') or '').strip()
        if not summary or len(evidence) < 8 or _normalize(evidence) not in source:
            raise GenerationError(
                'A generated change could not be traced to the master resume; no files were saved')
        cleaned_changes.append({'summary': summary[:1000],
                                'resume_evidence': evidence[:1000]})
    combined_output = _normalize(resume + '\n' + cover)
    cleaned_trace = []
    for item in claim_trace[:300]:
        if not isinstance(item, dict):
            raise GenerationError('Generated claim evidence was invalid')
        claim = str(item.get('generated_claim') or '').strip()
        evidence = str(item.get('resume_evidence') or '').strip()
        if (len(claim) < 4 or _normalize(claim) not in combined_output or
                len(evidence) < 8 or _normalize(evidence) not in source):
            raise GenerationError(
                'A generated claim could not be traced to the master resume; no files were saved')
        cleaned_trace.append({'generated_claim': claim[:1000],
                              'resume_evidence': evidence[:1000]})
    if not cleaned_trace:
        raise GenerationError('The AI provider did not provide the required claim trace')
    return resume, cover, cleaned_changes, cleaned_trace


def _docx_bytes(text):
    from io import BytesIO
    paragraphs = []
    for raw in str(text).replace('\r\n', '\n').split('\n'):
        value = escape(raw)
        paragraphs.append(
            '<w:p><w:r><w:t xml:space="preserve">' + value + '</w:t></w:r></w:p>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + ''.join(paragraphs) +
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/>'
                '</w:sectPr></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    relationships = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" '
                     'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                     'Target="word/document.xml"/></Relationships>')
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', relationships)
        archive.writestr('word/document.xml', document)
    return output.getvalue()


def _pdf_escape(value):
    return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _pdf_bytes(text):
    lines = []
    for paragraph in str(text).replace('\r\n', '\n').split('\n'):
        wrapped = textwrap.wrap(paragraph, width=92, replace_whitespace=False,
                                drop_whitespace=True) or ['']
        lines.extend(wrapped)
    pages = [lines[index:index + 52] for index in range(0, len(lines), 52)] or [[]]
    objects = []
    page_ids = []
    content_ids = []
    font_id = 3
    next_id = 4
    for _ in pages:
        page_ids.append(next_id)
        content_ids.append(next_id + 1)
        next_id += 2
    kids = ' '.join(f'{page_id} 0 R' for page_id in page_ids)
    objects.append((1, '<< /Type /Catalog /Pages 2 0 R >>'))
    objects.append((2, f'<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>'))
    objects.append((font_id, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'))
    for page_id, content_id, page_lines in zip(page_ids, content_ids, pages):
        stream_lines = ['BT', '/F1 10 Tf', '12 TL', '54 756 Td']
        for index, line in enumerate(page_lines):
            value = line.encode('latin-1', errors='replace').decode('latin-1')
            if index:
                stream_lines.append('T*')
            stream_lines.append(f'({_pdf_escape(value)}) Tj')
        stream_lines.append('ET')
        stream = '\n'.join(stream_lines).encode('latin-1')
        objects.append((page_id,
                        f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                        f'/Resources << /Font << /F1 {font_id} 0 R >> >> '
                        f'/Contents {content_id} 0 R >>'))
        objects.append((content_id, (f'<< /Length {len(stream)} >>\nstream\n').encode('ascii') +
                        stream + b'\nendstream'))
    objects.sort(key=lambda item: item[0])
    output = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0] * (max(item[0] for item in objects) + 1)
    for object_id, content in objects:
        offsets[object_id] = len(output)
        output.extend(f'{object_id} 0 obj\n'.encode('ascii'))
        output.extend(content if isinstance(content, bytes) else content.encode('latin-1'))
        output.extend(b'\nendobj\n')
    xref = len(output)
    output.extend(f'xref\n0 {len(offsets)}\n'.encode('ascii'))
    output.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    output.extend((f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n'
                   f'startxref\n{xref}\n%%EOF\n').encode('ascii'))
    return bytes(output)


def _write_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(content)
    return hashlib.sha256(content).hexdigest()


def _safe_name(value):
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value or '')).strip('._')
    return cleaned[:80] or 'application'


def cleanup_stale_temporary_files(root, older_than_hours=24):
    """Remove only abandoned generation directories created by this module."""
    root = Path(root).resolve()
    cutoff = time.time() - max(1, older_than_hours) * 3600
    removed = 0
    for candidate in root.glob('*/*/.v*.tmp'):
        resolved = candidate.resolve()
        if (candidate.is_dir() and candidate.name.startswith('.v') and
                candidate.name.endswith('.tmp') and
                os.path.commonpath((str(root), str(resolved))) == str(root) and
                candidate.stat().st_mtime < cutoff):
            shutil.rmtree(candidate)
            removed += 1
    return removed


def _save_rendered_files(db, database_path, user_id, attempt, version, files,
                         usage, approval_state='pending'):
    root = auto_apply.artifact_root(database_path)
    cleanup_stale_temporary_files(root)
    final_dir = root / str(user_id) / str(attempt['id']) / f'v{version}'
    temp_dir = (root / str(user_id) / str(attempt['id']) /
                (f'.v{version}-{os.getpid()}.tmp'))
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=False)
    written = []
    try:
        for kind, file_format, file_name, content, mime_type in files:
            digest = _write_file(temp_dir / file_name, content)
            written.append((kind, file_format, file_name, digest, mime_type))
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            raise GenerationError('This document version already exists')
        os.replace(temp_dir, final_dir)
        for kind, file_format, file_name, digest, mime_type in written:
            relative = str((Path(str(user_id)) / str(attempt['id']) /
                            f'v{version}' / file_name).as_posix())
            auto_apply.add_artifact(
                db, user_id, attempt['id'], kind, file_format, file_name,
                relative, digest, mime_type, version, usage, approval_state,
            )
        db.commit()
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        if final_dir.exists() and not db.execute(
                'SELECT 1 FROM application_artifacts WHERE attempt_id=? AND version=?',
                (str(attempt['id']), version)).fetchone():
            shutil.rmtree(final_dir)
        raise


def generate_documents(db, database_path, user_id, attempt_id, provider, api_key,
                       master_resume, profile, generate_resume=True,
                       generate_cover_letter=True):
    attempt = auto_apply.get_attempt(db, user_id, attempt_id, include_details=False)
    if not attempt:
        raise LookupError('Application attempt not found')
    info = provider_info(provider)
    if not api_key:
        raise GenerationError(f"Add a {info['label']} API key in Application assistant setup")
    if not str(master_resume or '').strip():
        raise GenerationError('A master resume is required')
    requested = _requested(generate_resume, generate_cover_letter)
    auto_apply.transition(db, user_id, attempt_id, 'preparing_documents',
                          f"Generating tailored documents with {info['label']}")
    try:
        generated, usage = _call_provider(
            provider, api_key, master_resume, attempt['job'], profile, requested)
        resume, cover, changes, claim_trace = _validate(
            generated, master_resume, requested)
    except Exception as exc:
        auto_apply.transition(
            db, user_id, attempt_id, 'could_not_fill',
            'Document generation failed; no files were saved',
            error_code='document_generation_failed',
            metadata={'reason': str(exc)[:300]},
        )
        raise
    primary_kind = 'resume' if 'resume' in requested else 'cover_letter'
    version = auto_apply.next_artifact_version(db, attempt_id, primary_kind)
    title = _safe_name(attempt['job'].get('title'))
    company = _safe_name(attempt['job'].get('company'))
    base = _safe_name(f'{title}_{company}')
    files = []
    if 'resume' in requested:
        files.extend([
            ('resume', 'txt', f'{base}_resume_ATS.txt', resume.encode('utf-8'),
             'text/plain; charset=utf-8'),
            ('resume', 'docx', f'{base}_resume.docx', _docx_bytes(resume),
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
            ('resume', 'pdf', f'{base}_resume.pdf', _pdf_bytes(resume), 'application/pdf'),
        ])
    if 'cover_letter' in requested:
        files.extend([
            ('cover_letter', 'txt', f'{base}_cover_letter.txt', cover.encode('utf-8'),
             'text/plain; charset=utf-8'),
            ('cover_letter', 'docx', f'{base}_cover_letter.docx', _docx_bytes(cover),
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
            ('cover_letter', 'pdf', f'{base}_cover_letter.pdf', _pdf_bytes(cover),
             'application/pdf'),
        ])
    files.append(('change_summary', 'txt', f'{base}_changes.txt',
         (('DOCUMENT CHANGES\n\n' + ('\n\n'.join(
             f"Change: {item['summary']}\nResume evidence: {item['resume_evidence']}"
             for item in changes) or 'No substantive changes were reported.')) +
          '\n\nCLAIM TRACE\n\n' + '\n\n'.join(
             f"Generated claim: {item['generated_claim']}\nResume evidence: {item['resume_evidence']}"
             for item in claim_trace)).encode('utf-8'),
         'text/plain; charset=utf-8'))
    _save_rendered_files(
        db, database_path, user_id, attempt, version, files, usage)
    auto_apply.transition(
        db, user_id, attempt_id, 'awaiting_document_approval',
        'Tailored documents are ready for approval',
        metadata={'version': version, 'provider': provider, 'model': info['model'],
                  'generated_documents': requested,
                  'actual_cost_usd': usage['actual_cost_usd']},
    )
    return auto_apply.get_attempt(db, user_id, attempt_id)


def use_saved_documents(db, database_path, user_id, attempt_id, resume_text,
                        role_name, resume_pdf=None, cover_pdf=None):
    """Copy the role's selected files into immutable attempt history."""
    attempt = auto_apply.get_attempt(db, user_id, attempt_id, include_details=False)
    if not attempt:
        raise LookupError('Application attempt not found')
    if not str(resume_text or '').strip():
        raise GenerationError(f'The {role_name or "Default"} role has no saved resume')
    auto_apply.transition(
        db, user_id, attempt_id, 'preparing_documents',
        f'Loading the saved resume from role {role_name or "Default"}')
    version = auto_apply.next_artifact_version(db, attempt_id, 'resume')
    title = _safe_name(attempt['job'].get('title'))
    company = _safe_name(attempt['job'].get('company'))
    base = _safe_name(f'{title}_{company}')
    files = [
        ('resume', 'txt', f'{base}_resume_ATS.txt', str(resume_text).encode('utf-8'),
         'text/plain; charset=utf-8'),
        ('resume', 'pdf',
         (resume_pdf or {}).get('file_name') or f'{base}_resume.pdf',
         (resume_pdf or {}).get('content') or _pdf_bytes(resume_text), 'application/pdf'),
    ]
    if cover_pdf:
        files.append(('cover_letter', 'pdf',
                      cover_pdf.get('file_name') or f'{base}_cover_letter.pdf',
                      cover_pdf['content'], 'application/pdf'))
    usage = {'provider': 'saved_role', 'model': '', 'input_tokens': 0,
             'output_tokens': 0, 'estimated_cost_usd': 0, 'actual_cost_usd': 0}
    _save_rendered_files(
        db, database_path, user_id, attempt, version, files, usage, 'approved')
    selected_resume = db.execute(
        'SELECT id FROM application_artifacts WHERE attempt_id=? AND user_id=? '
        'AND kind=? AND version=? AND format=?',
        (str(attempt_id), user_id, 'resume', version, 'pdf')).fetchone()
    selected_cover = db.execute(
        'SELECT id FROM application_artifacts WHERE attempt_id=? AND user_id=? '
        'AND kind=? AND version=? AND format=?',
        (str(attempt_id), user_id, 'cover_letter', version, 'pdf')).fetchone()
    db.execute(
        'UPDATE application_attempts SET selected_resume_id=?,'
        'selected_cover_id=COALESCE(?,selected_cover_id),updated_at=? '
        'WHERE id=? AND user_id=?',
        (selected_resume['id'], selected_cover['id'] if selected_cover else None,
         auto_apply.utcnow(), str(attempt_id), user_id))
    db.commit()
    return auto_apply.transition(
        db, user_id, attempt_id, 'ready_for_review',
        f'Saved role documents from {role_name or "Default"} are selected',
        metadata={'version': version, 'document_source': 'saved_role',
                  'role': role_name or 'Default',
                  'cover_letter_included': bool(cover_pdf)})


def use_saved_resume(db, database_path, user_id, attempt_id, resume_text, role_name):
    """Backward-compatible wrapper for existing callers and tests."""
    return use_saved_documents(
        db, database_path, user_id, attempt_id, resume_text, role_name)
