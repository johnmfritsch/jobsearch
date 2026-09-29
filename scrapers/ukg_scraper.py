"""Public UKG/UltiPro priority-employer adapter.

Targets the same internal JSON search endpoint the job board's own
Knockout.js SPA calls for its "opportunities" list -- confirmed against a
real tenant (Buckeye Partners, recruiting2.ultipro.com) rather than assumed
from documentation. The landing page never embeds a working example request
in static markup or in any of its downloadable JS bundles (the call site
lives inside `US.Opportunity.OpportunitiesViewModel`, minified into the
shared site.min.js bundle); the real payload shape -- POST
{"opportunitySearch": {"QueryString","Filters","Top","Skip"}} to
.../JobBoardView/LoadSearchResults -- was recovered by reading that
constructor's source, not guessed. No session/cookie state is required: the
endpoint is reachable statelessly despite the page also setting an
ASP.NET Core antiforgery cookie for its own (unrelated, authenticated-write)
forms.
"""
import json
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import safe_fetch
try:  # imported as scrapers.ukg_scraper by Flask API requests
    from .successfactors_scraper import _detect_company_name
except ImportError:  # imported from SCRAPERS_DIR by detached main.py
    from successfactors_scraper import _detect_company_name


USER_AGENT = 'JobSearchPriorityEmployer/1.0 (+https://fritsch-nas.myasustor.com/)'
PAGE_SIZE = 50
MAX_LIST_PAGES = 3
MAX_DETAIL_JOBS = 120
DETAIL_DELAY_SECONDS = 0.20

HOST_RE = re.compile(r'^recruiting\d*\.ultipro\.com$', re.I)
PATH_RE = re.compile(r'^/([A-Za-z0-9]+)/JobBoard/([0-9a-fA-F-]{36})')


class UKGError(RuntimeError):
    pass


def is_ukg_url(url):
    parsed = urlparse(str(url or ''))
    return bool(HOST_RE.match((parsed.hostname or '').lower()) and PATH_RE.match(parsed.path or ''))


def _parse(url):
    """(tenant_alias, job_board_id, base) from a UKG/UltiPro job-board URL, e.g.
    https://recruiting2.ultipro.com/BUC1000BPL/JobBoard/adf51551-.../  ->
    ('BUC1000BPL', 'adf51551-...', 'https://recruiting2.ultipro.com')."""
    parsed = urlparse(str(url or ''))
    if not HOST_RE.match((parsed.hostname or '').lower()):
        raise UKGError('That address is not a ultipro.com job-board page')
    match = PATH_RE.match(parsed.path or '')
    if not match:
        raise UKGError('That address is missing the job-board path, e.g. /TENANT/JobBoard/<id>/')
    return match.group(1), match.group(2), '{}://{}'.format(parsed.scheme or 'https', parsed.hostname)


def _board_url(base, tenant, board_id):
    return '{}/{}/JobBoard/{}'.format(base, tenant, board_id)


def _fetch(url, *, method='GET', body=None, timeout=15, max_bytes=2_000_000,
           deadline=None, allowed_content=('text/html', 'application/json')):
    if deadline is not None and time.monotonic() >= deadline:
        raise UKGError('Priority-employer test reached its time limit')
    headers = {'Content-Type': 'application/json; charset=utf-8'} if method == 'POST' and body else None
    try:
        return safe_fetch.fetch_text(
            url, method=method, body=body, headers=headers, timeout=timeout, max_redirects=3,
            max_bytes=max_bytes, allowed_schemes=('https',), allowed_ports=(443,),
            allowed_content=allowed_content, user_agent=USER_AGENT)
    except safe_fetch.SafeFetchError as error:
        raise UKGError(str(error)) from error


def _search(base, tenant, board_id, keyword, skip, top=PAGE_SIZE, deadline=None):
    endpoint = '{}/JobBoardView/LoadSearchResults'.format(_board_url(base, tenant, board_id))
    payload = json.dumps({'opportunitySearch': {
        'QueryString': keyword or '', 'Filters': [], 'Top': top, 'Skip': skip}})
    page = _fetch(endpoint, method='POST', body=payload, deadline=deadline,
                  allowed_content=('application/json',))
    try:
        data = json.loads(page.text)
    except ValueError as error:
        raise UKGError('The UKG listing response was invalid') from error
    if 'opportunities' not in data or 'totalCount' not in data:
        raise UKGError('The UKG listing response had an unexpected shape')
    return data


def _location_text(locations):
    for location in locations or []:
        text = str(location.get('LocalizedDescription') or '').strip()
        if text:
            address = location.get('Address') or {}
            state = (address.get('State') or {}).get('Code') or ''
            return '{}, {}'.format(text, state) if state else text
    return ''


def _strip_html(html):
    return re.sub(r'\s+', ' ', BeautifulSoup(html or '', 'html.parser').get_text(' ', strip=True)).strip()


def _extract_balanced_json(text, marker):
    """The UKG detail page embeds the full opportunity record as a plain
    JSON object literal passed straight into a constructor call, e.g.
    `new US.Opportunity.CandidateOpportunityDetail({...})` -- there is no
    separate JSON detail API. Regex can't safely bound a JSON blob that may
    itself contain "})" inside description text, so this counts braces."""
    start = text.find(marker)
    if start == -1:
        return None
    brace_start = text.find('{', start)
    if brace_start == -1:
        return None
    depth = 0
    for index in range(brace_start, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                return text[brace_start:index + 1]
    return None


def _annual_salary(detail):
    if not detail.get('PayRangeVisible'):
        return None
    for key in ('CompensationAnnualMinimum', 'CompensationAmount'):
        value = detail.get(key)
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _listings(base, tenant, board_id, keyword, deadline=None, max_pages=MAX_LIST_PAGES):
    listings, seen, total = [], set(), None
    for page_index in range(max_pages):
        data = _search(base, tenant, board_id, keyword, page_index * PAGE_SIZE, deadline=deadline)
        if total is None:
            total = int(data.get('totalCount') or 0)
        opportunities = data.get('opportunities') or []
        for item in opportunities:
            item_id = item.get('Id')
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            listings.append({'id': item_id, 'title': item.get('Title', ''),
                             'requisition': item.get('RequisitionNumber', ''),
                             'location': _location_text(item.get('Locations')),
                             'posted_at': item.get('PostedDate', ''),
                             'brief': item.get('BriefDescription', ''),
                             'full_time': bool(item.get('FullTime')),
                             'keywords': [keyword] if keyword else []})
        if not opportunities or len(listings) >= total:
            break
    return listings, total


def _detail_job(base, tenant, board_id, listing, source, deadline=None):
    detail_url = '{}/OpportunityDetail?opportunityId={}'.format(
        _board_url(base, tenant, board_id), listing['id'])
    page = _fetch(detail_url, deadline=deadline, allowed_content=('text/html',))
    blob = _extract_balanced_json(page.text, 'new US.Opportunity.CandidateOpportunityDetail(')
    detail = {}
    if blob:
        try:
            detail = json.loads(blob)
        except ValueError:
            detail = {}
    company = source['company_name']
    body = _strip_html(detail.get('Description') or listing.get('brief', ''))
    return {
        'id': 'ukg:{}:{}'.format(board_id, listing['id']),
        'title': str(detail.get('Title') or listing.get('title') or '')[:300],
        'company': company,
        'location': _location_text(detail.get('Locations')) or listing.get('location', ''),
        'employment_type': 'Full-time' if detail.get('FullTime', listing.get('full_time')) else 'Part-time',
        'posted_at': str(detail.get('PostedDate') or listing.get('posted_at') or '')[:80],
        'salary': _annual_salary(detail),
        'url': detail_url,
        'apply_url': detail_url,
        'snippet': body[:800],
        'description': body[:30000],
        'full_description': body[:30000],
        'source': '{} careers'.format(company),
        '_sources': ['{} careers'.format(company)],
        '_target_employers': [company],
        '_employer_direct': True,
        '_search_keywords': list(listing.get('keywords', [])),
        '_search_modes': ['employer'],
    }


def detect_company(url, deadline=None):
    """Best-effort company-name guess for a UKG/UltiPro job-board URL that
    hasn't been saved yet -- these boards are a Knockout.js SPA with no
    static <title>/meta tags, so this reuses the shared brand-name fallback
    (bootstrap-JSON "BrandId"/"Name") rather than a second copy of the same
    regex."""
    tenant, board_id, base = _parse(url)
    page = _fetch(_board_url(base, tenant, board_id), deadline=deadline, allowed_content=('text/html',))
    return {'company_name': _detect_company_name(page.text)}


def sample(source, deadline=None):
    """Bounded direct-source test used by the synchronous management endpoint."""
    tenant, board_id, base = _parse(source.get('careers_url'))
    listings, total = _listings(base, tenant, board_id, '', deadline=deadline)
    return {'adapter': 'ukg', 'count': len(listings), 'total_available': total,
            'titles': [item.get('title', '') for item in listings[:3]]}


def fetch_jobs(source, cfg, source_counts, user=None, test_mode=False):
    if test_mode:
        return []
    if not source.get('careers_url'):
        raise UKGError('A direct UKG source needs a careers URL')
    tenant, board_id, base = _parse(source['careers_url'])
    keywords = [str(value).strip() for value in cfg.get('keywords', []) if str(value).strip()] or ['']
    max_details = min(MAX_DETAIL_JOBS, max(1, int(cfg.get('priority_employer_max_details', MAX_DETAIL_JOBS))))
    seen, jobs = set(), []
    for keyword in keywords:
        listings, _ = _listings(base, tenant, board_id, keyword)
        for listing in listings:
            if listing['id'] in seen:
                continue
            seen.add(listing['id'])
            if len(jobs) >= max_details:
                break
            jobs.append(_detail_job(base, tenant, board_id, listing, source))
            time.sleep(DETAIL_DELAY_SECONDS)
        if len(jobs) >= max_details:
            break
    label = '{} careers'.format(source['company_name'])
    source_counts[label] = source_counts.get(label, 0) + len(jobs)
    return jobs
