"""Bounded public Jibe/iCIMS (Jibe API v1) priority-employer adapter."""
import json
import re
import time
from urllib.parse import urlencode, urljoin, urlparse, urlunparse, parse_qsl

import safe_fetch
try:
    from .adapter_utils import employer_name_matches, html_to_text
    from .successfactors_scraper import _detect_company_name
except ImportError:
    from adapter_utils import employer_name_matches, html_to_text
    from successfactors_scraper import _detect_company_name


USER_AGENT = 'JobSearchPriorityEmployer/1.0 (+https://fritsch-nas.myasustor.com/)'
PAGE_SIZE = 20
MAX_LIST_PAGES = 3
MAX_DETAIL_JOBS = 120
DETAIL_DELAY_SECONDS = 0.20


class JibeError(RuntimeError):
    pass


def _host(url):
    return (urlparse(str(url or '')).hostname or '').lower().rstrip('.')


def _remaining_timeout(deadline, default=15):
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise JibeError('Priority-employer request reached its time limit')
    return max(1, min(default, remaining))


def _fetch(url, host, *, deadline=None, allowed_content=('text/html', 'application/json')):
    try:
        return safe_fetch.fetch_text(
            url, timeout=_remaining_timeout(deadline), max_redirects=3, max_bytes=2_000_000,
            allowed_schemes=('https',), allowed_ports=(443,), allowed_hosts=(host,),
            allowed_content=allowed_content, user_agent=USER_AGENT)
    except safe_fetch.SafeFetchError as error:
        raise JibeError(str(error)) from error


def _api_url(page_url, html):
    host = _host(page_url)
    marker = re.search(r'''["']([^"']*/api/jobs(?:\?[^"']*)?)["']''', html or '', re.I)
    candidate = urljoin(page_url, marker.group(1)) if marker else urljoin(page_url, '/api/jobs')
    if _host(candidate) != host:
        raise JibeError('The Jibe listing API is not on the validated careers host')
    return candidate


def _query(url, **values):
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in values.items() if value not in (None, '')})
    return urlunparse(parsed._replace(query=urlencode(query)))


def _job_data(item):
    return item.get('data') if isinstance(item, dict) and isinstance(item.get('data'), dict) else item


def _organization(data):
    return (data.get('hiring_organization') or data.get('company') or data.get('organization')
            or ((data.get('meta_data') or {}).get('icims') or {}).get('config_keys', {}).get(
                'jobposting.external.company.name') or '')


def _listing_page(api, host, keyword, location, page, deadline=None):
    result = _fetch(_query(api, page=page, keywords=keyword, location=location), host,
                    deadline=deadline, allowed_content=('application/json',))
    try:
        data = json.loads(result.text)
    except ValueError as error:
        raise JibeError('The Jibe listing response was invalid') from error
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise JibeError('The Jibe listing response had an unexpected shape')
    return data


def probe(url, deadline=None):
    """Positive, conservative detection; never claims Jibe from a URL alone."""
    host = _host(url)
    page = _fetch(url, host, deadline=deadline, allowed_content=('text/html',))
    html = page.text
    markers = ('data-jibe-search-version' in html.lower() or
               bool(re.search(r'window\.searchConfig', html, re.I)) or
               bool(re.search(r'(?:jibecdn|jibe).*?(?:search|jobs)', html, re.I)))
    if not markers:
        return None
    try:
        listing = _listing_page(_api_url(page.url, html), host, '', '', 1, deadline=deadline)
    except JibeError:
        return None
    if not isinstance(listing.get('totalCount'), (int, float)) and not listing.get('jobs'):
        return None
    first = _job_data((listing.get('jobs') or [{}])[0]) or {}
    company = _organization(first) or _detect_company_name(html)
    return {'platform': 'Jibe/iCIMS', 'adapter': 'jibe', 'company_name': str(company).strip(),
            'capabilities': {'listing_api': True, 'detail_api': True, 'pagination': True,
                             'structured_data': False},
            'explanation': 'Verified a same-host public Jibe jobs API response.'}


def is_jibe_url(url):
    return False  # Jibe is probe-only; arbitrary URLs must not be claimed by suffix.


def detect_company(url, deadline=None):
    record = probe(url, deadline=deadline) or {}
    return {'company_name': record.get('company_name', '')}


def _listings(source, keyword='', location='', deadline=None, max_pages=MAX_LIST_PAGES):
    page_url = source.get('careers_url') or ''
    host = _host(page_url)
    page = _fetch(page_url, host, deadline=deadline, allowed_content=('text/html',))
    api = _api_url(page.url, page.text)
    found, seen, total = [], set(), None
    for page_number in range(1, max_pages + 1):
        envelope = _listing_page(api, host, keyword, location, page_number, deadline=deadline)
        if total is None:
            try:
                total = int(envelope.get('totalCount'))
            except (TypeError, ValueError):
                total = None
        items = envelope.get('jobs') or []
        for raw in items:
            item = _job_data(raw) or {}
            key = str(item.get('req_id') or item.get('slug') or '')
            if not key or key in seen:
                continue
            seen.add(key)
            item['_keyword'] = keyword
            item['_api'] = api
            found.append(item)
        if not items or (total is not None and len(found) >= total):
            break
    return found, total, page.url


def _canonical(data, page_url):
    host = _host(page_url)
    candidate = ((data.get('meta_data') or {}).get('canonical_url') or data.get('canonical_url') or '')
    if candidate and _host(candidate) == host:
        return candidate
    slug, language = data.get('slug') or data.get('req_id'), data.get('language') or 'en-us'
    return _query(urljoin(page_url, '/jobs/{}'.format(slug)), lang=language) if slug else page_url


def _needs_detail(data):
    return not (str(data.get('title') or '').strip() and str(data.get('location_name') or '').strip()
                and len(html_to_text(data.get('description', ''))) >= 400)


def _detail(data, host, deadline=None):
    slug, language = data.get('slug') or data.get('req_id'), data.get('language') or 'en-us'
    if not slug:
        return data
    page = _fetch(urljoin('https://{}'.format(host), '/api/jobs/{}/{}'.format(slug, language)), host,
                  deadline=deadline, allowed_content=('application/json',))
    try:
        value = json.loads(page.text)
    except ValueError as error:
        raise JibeError('The Jibe job-detail response was invalid') from error
    return value if isinstance(value, dict) else data


def _normalize(data, source, page_url):
    company = _organization(data)
    if company and not employer_name_matches(company, source):
        raise JibeError('The careers page organization does not match this priority employer')
    description = html_to_text(data.get('description', ''))
    extras = [('Qualifications', data.get('qualifications')), ('Responsibilities', data.get('responsibilities'))]
    for heading, value in extras:
        text = html_to_text(value)
        if text and text not in description:
            description = '{}\n\n{}\n{}'.format(description, heading, text).strip()
    slug, req_id, language = data.get('slug') or '', data.get('req_id') or '', data.get('language') or 'en-us'
    stable = req_id or slug
    if not stable:
        raise JibeError('A Jibe job did not contain a stable requisition or slug')
    label = '{} careers'.format(source['company_name'])
    canonical = _canonical(data, page_url)
    return {'id': 'jibe:{}:{}:{}'.format(_host(page_url), stable, language),
            'title': str(data.get('title') or '')[:300], 'company': source['company_name'],
            'location': str(data.get('location_name') or data.get('full_location') or '')[:300],
            'employment_type': str(data.get('employment_type') or '')[:100],
            'posted_at': str(data.get('posted_date') or data.get('update_date') or '')[:80],
            'salary': None, 'url': canonical, 'apply_url': str(data.get('apply_url') or canonical)[:2000],
            'snippet': description[:800], 'description': description[:30000],
            'full_description': description[:30000], 'source': label, '_sources': [label],
            '_target_employers': [source['company_name']], '_employer_direct': True,
            '_search_keywords': [data.get('_keyword')] if data.get('_keyword') else [],
            '_search_modes': ['employer']}


def sample(source, deadline=None):
    listings, total, _ = _listings(source, deadline=deadline)
    return {'adapter': 'jibe', 'platform': 'Jibe/iCIMS', 'count': len(listings),
            'total_available': total, 'titles': [item.get('title', '') for item in listings[:3]],
            'bounded': total is not None and total > len(listings)}


def fetch_jobs(source, cfg, source_counts, user=None, test_mode=False):
    if test_mode:
        return []
    keywords = [str(value).strip() for value in cfg.get('keywords', []) if str(value).strip()] or ['']
    location = str(cfg.get('location') or '').strip() if cfg.get('search_local') else ''
    maximum = min(MAX_DETAIL_JOBS, max(1, int(cfg.get('priority_employer_max_details', MAX_DETAIL_JOBS))))
    jobs, seen = [], set()
    for keyword in keywords:
        listings, _total, page_url = _listings(source, keyword, location)
        for listing in listings:
            identity = str(listing.get('req_id') or listing.get('slug') or '')
            if identity in seen:
                continue
            seen.add(identity)
            if len(jobs) >= maximum:
                break
            data = _detail(listing, _host(page_url)) if _needs_detail(listing) else listing
            data['_keyword'] = keyword
            jobs.append(_normalize(data, source, page_url))
            if len(jobs) < maximum:
                time.sleep(DETAIL_DELAY_SECONDS)
        if len(jobs) >= maximum:
            break
    label = '{} careers'.format(source['company_name'])
    source_counts[label] = source_counts.get(label, 0) + len(jobs)
    return jobs
