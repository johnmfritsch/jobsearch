"""Conservative same-host schema.org/JobPosting priority-employer adapter."""
import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

import safe_fetch
from manual_jobs import _address, _annual_salary, _iter_nodes
try:
    from .adapter_utils import employer_name_matches, html_to_text
except ImportError:
    from adapter_utils import employer_name_matches, html_to_text


USER_AGENT = 'JobSearchPriorityEmployer/1.0 (+https://fritsch-nas.myasustor.com/)'
MAX_DETAIL_LINKS = 12
MAX_LIST_PAGES = 3
MAX_JOBS = 120


class StructuredDataError(RuntimeError):
    pass


def _host(url):
    return (urlparse(str(url or '')).hostname or '').lower().rstrip('.')


def _timeout(deadline, default=15):
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise StructuredDataError('Priority-employer request reached its time limit')
    return max(1, min(default, remaining))


def _fetch(url, host, deadline=None):
    try:
        return safe_fetch.fetch_text(url, timeout=_timeout(deadline), max_redirects=3,
                                     max_bytes=2_000_000, allowed_schemes=('https',),
                                     allowed_ports=(443,), allowed_hosts=(host,),
                                     allowed_content=('text/html',), user_agent=USER_AGENT)
    except safe_fetch.SafeFetchError as error:
        raise StructuredDataError(str(error)) from error


def _is_job_posting(node):
    types = node.get('@type') if isinstance(node, dict) else None
    types = types if isinstance(types, list) else [types]
    return any(str(value).casefold() == 'jobposting' for value in types)


def _nodes(html):
    soup = BeautifulSoup(html or '', 'html.parser')
    result = []
    for script in soup.find_all('script', type=re.compile(r'ld\+json', re.I)):
        try:
            payload = json.loads(script.string or script.get_text() or '')
        except (TypeError, ValueError):
            continue
        result.extend(node for node in _iter_nodes(payload) if _is_job_posting(node))
    return result


def _value_url(value, page_url):
    if isinstance(value, dict):
        value = value.get('@id') or value.get('url') or value.get('id') or ''
    text = str(value or '').strip()
    return urljoin(page_url, text) if text else ''


def _organization(node):
    value = node.get('hiringOrganization') if isinstance(node, dict) else None
    if isinstance(value, list):
        value = value[0] if value else None
    return value.get('name', '') if isinstance(value, dict) else str(value or '')


def _usable(node):
    return bool(str(node.get('title') or '').strip() and html_to_text(node.get('description', '')))


def _detail_links(html, page_url, host):
    soup = BeautifulSoup(html or '', 'html.parser')
    links, seen = [], set()
    for anchor in soup.find_all('a', href=True):
        target = urljoin(page_url, anchor['href'])
        parsed = urlparse(target)
        if _host(target) != host or parsed.scheme != 'https':
            continue
        if target in seen or not re.search(r'(job|career|position|opening)', parsed.path, re.I):
            continue
        seen.add(target)
        links.append(target)
        if len(links) >= MAX_DETAIL_LINKS:
            break
    return links


def _pagination_link(html, page_url, host):
    soup = BeautifulSoup(html or '', 'html.parser')
    for anchor in soup.find_all('a', href=True):
        rel = ' '.join(anchor.get('rel') or [])
        label = anchor.get_text(' ', strip=True)
        if not (rel.casefold() == 'next' or re.fullmatch(r'(next|older|more)', label, re.I)):
            continue
        target = urljoin(page_url, anchor['href'])
        if _host(target) == host and urlparse(target).scheme == 'https':
            return target
    return ''


def _canonical(node, page_url):
    candidate = _value_url(node.get('url') or node.get('mainEntityOfPage'), page_url)
    return candidate or page_url


def _normalize(node, source, page_url, keywords=()):
    company = _organization(node)
    if company and not employer_name_matches(company, source):
        raise StructuredDataError('The structured-data organization does not match this priority employer')
    canonical = _canonical(node, page_url)
    identity_value = (str(node.get('identifier') or '').strip() or canonical)
    if isinstance(node.get('identifier'), dict):
        identity_value = str(node['identifier'].get('value') or canonical)
    digest = hashlib.sha256(identity_value.encode('utf-8')).hexdigest()[:24]
    description = html_to_text(node.get('description', ''))
    apply_url = _value_url(node.get('applicationUrl'), page_url) or canonical
    label = '{} careers'.format(source['company_name'])
    return {'id': 'structured:{}:{}'.format(_host(page_url), digest),
            'title': str(node.get('title') or '')[:300], 'company': source['company_name'],
            'location': _address(node)[:300],
            'employment_type': str(node.get('employmentType') or '')[:100],
            'posted_at': str(node.get('datePosted') or '')[:80], 'valid_through': str(node.get('validThrough') or '')[:80],
            'salary': _annual_salary(node), 'url': canonical[:2000], 'apply_url': apply_url[:2000],
            'direct_apply': bool(node.get('directApply')), 'snippet': description[:800],
            'description': description[:30000], 'full_description': description[:30000],
            'source': label, '_sources': [label], '_target_employers': [source['company_name']],
            '_employer_direct': True, '_search_keywords': list(keywords), '_search_modes': ['employer']}


def _collect(source, deadline=None, max_pages=MAX_LIST_PAGES):
    start = source.get('careers_url') or ''
    host, current = _host(start), start
    pages, records, seen_links = 0, [], set()
    while current and pages < max_pages:
        page = _fetch(current, host, deadline=deadline)
        pages += 1
        nodes = _nodes(page.text)
        if not nodes:
            for link in _detail_links(page.text, page.url, host):
                if link in seen_links:
                    continue
                seen_links.add(link)
                detail = _fetch(link, host, deadline=deadline)
                nodes.extend(_nodes(detail.text))
                if len(nodes) >= MAX_DETAIL_LINKS:
                    break
        for node in nodes:
            if _usable(node):
                records.append((node, page.url))
                if len(records) >= MAX_JOBS:
                    return records, pages
        current = _pagination_link(page.text, page.url, host)
    return records, pages


def probe(url, deadline=None):
    source = {'careers_url': url, 'company_name': '', 'company_aliases': []}
    host = _host(url)
    page = _fetch(url, host, deadline=deadline)
    nodes = _nodes(page.text)
    if not nodes:
        for link in _detail_links(page.text, page.url, host):
            detail = _fetch(link, host, deadline=deadline)
            nodes = _nodes(detail.text)
            if nodes:
                break
    usable = next((node for node in nodes if _usable(node)), None)
    if not usable:
        return None
    return {'platform': 'Generic structured-data page', 'adapter': 'structured_data',
            'company_name': _organization(usable).strip(),
            'capabilities': {'listing_api': False, 'detail_api': bool(not _nodes(page.text)),
                             'pagination': bool(_pagination_link(page.text, page.url, host)),
                             'structured_data': True},
            'explanation': 'Verified valid schema.org JobPosting structured data on the careers host.'}


def is_structured_data_url(url):
    return False  # positively probe content; never take arbitrary URL suffixes.


def detect_company(url, deadline=None):
    record = probe(url, deadline=deadline) or {}
    return {'company_name': record.get('company_name', '')}


def sample(source, deadline=None):
    records, pages = _collect(source, deadline=deadline)
    normalized = [_normalize(node, source, page_url) for node, page_url in records]
    return {'adapter': 'structured_data', 'platform': 'Generic structured-data page',
            'count': len(normalized), 'total_available': None, 'pages_fetched': pages,
            'titles': [job['title'] for job in normalized[:3]], 'bounded': True}


def fetch_jobs(source, cfg, source_counts, user=None, test_mode=False):
    if test_mode:
        return []
    records, _pages = _collect(source)
    keywords = [str(value).strip() for value in cfg.get('keywords', []) if str(value).strip()]
    jobs = [_normalize(node, source, page_url, keywords) for node, page_url in records]
    label = '{} careers'.format(source['company_name'])
    source_counts[label] = source_counts.get(label, 0) + len(jobs)
    return jobs
