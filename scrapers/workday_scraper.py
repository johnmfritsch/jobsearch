"""Public Workday priority-employer adapter.

Targets the CXS (candidate experience) JSON API every myworkdayjobs.com
tenant exposes for its own career-site search UI -- confirmed against a real
tenant (St. Luke's University Health Network / sluhn) rather than assumed
from documentation. Workday's own <site>/jobs landing page is just an
SPA-redirect stub with no useful static content ({"widget":"redirect",
"externalSpa":true}), so this talks to the JSON API directly rather than
scraping rendered HTML the way the SuccessFactors adapter does.
"""
import json
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import safe_fetch


USER_AGENT = 'JobSearchPriorityEmployer/1.0 (+https://fritsch-nas.myasustor.com/)'
PAGE_SIZE = 20
MAX_LIST_PAGES = 3
MAX_DETAIL_JOBS = 120
DETAIL_DELAY_SECONDS = 0.20

HOST_RE = re.compile(r'^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$', re.I)


class WorkdayError(RuntimeError):
    pass


def is_workday_url(url):
    return bool(HOST_RE.match((urlparse(str(url or '')).hostname or '').lower()))


def _parse(url):
    """(tenant, wd_host, site, base) from a Workday careers URL, e.g.
    https://sluhn.wd1.myworkdayjobs.com/SLUHN/jobs -> ('sluhn','wd1','SLUHN',...)."""
    parsed = urlparse(str(url or ''))
    match = HOST_RE.match((parsed.hostname or '').lower())
    if not match:
        raise WorkdayError('That address is not a myworkdayjobs.com careers page')
    tenant, wd_host = match.group(1), match.group(2)
    segments = [part for part in parsed.path.split('/') if part]
    if not segments:
        raise WorkdayError('That address is missing the career-site name, e.g. /SLUHN/jobs')
    base = 'https://{}.{}.myworkdayjobs.com'.format(tenant, wd_host)
    return tenant, wd_host, segments[0], base


def _fetch(url, *, method='GET', body=None, timeout=15, max_bytes=1_500_000, deadline=None):
    if deadline is not None and time.monotonic() >= deadline:
        raise WorkdayError('Priority-employer test reached its time limit')
    headers = {'Content-Type': 'application/json'} if method == 'POST' and body else None
    try:
        return safe_fetch.fetch_text(
            url, method=method, body=body, headers=headers, timeout=timeout, max_redirects=3,
            max_bytes=max_bytes, allowed_schemes=('https',), allowed_ports=(443,),
            allowed_content=('application/json',), user_agent=USER_AGENT)
    except safe_fetch.SafeFetchError as error:
        raise WorkdayError(str(error)) from error


def _search(base, tenant, site, keyword, offset, deadline=None):
    endpoint = '{}/wday/cxs/{}/{}/jobs'.format(base, tenant, site)
    payload = json.dumps({'appliedFacets': {}, 'limit': PAGE_SIZE, 'offset': offset,
                          'searchText': keyword or ''})
    page = _fetch(endpoint, method='POST', body=payload, deadline=deadline)
    try:
        data = json.loads(page.text)
    except ValueError as error:
        raise WorkdayError('The Workday listing response was invalid') from error
    if 'jobPostings' not in data:
        raise WorkdayError('The Workday listing response had an unexpected shape')
    return data


def _clean_company_name(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    # hiringOrganization.name sometimes carries an internal HR cost-center
    # code prefix (observed: "CO39 St. Lukes Hospital") that isn't part of
    # the employer's real name.
    return re.sub(r'^[A-Z]{1,4}\d{1,5}\s+', '', text).strip()


def _annual_salary(text):
    match = re.search(r'\$\s*([\d,]{4,})\s*(?:-|to)\s*\$?\s*([\d,]{4,}).{0,60}(?:year|annual)', text, re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', ''))
    except ValueError:
        return None


def _strip_html(html):
    return re.sub(r'\s+', ' ', BeautifulSoup(html or '', 'html.parser').get_text(' ', strip=True)).strip()


def _detail_job(base, tenant, site, listing, source, deadline=None):
    external_path = listing.get('externalPath') or ''
    detail_url = '{}/wday/cxs/{}/{}{}'.format(base, tenant, site, external_path)
    page = _fetch(detail_url, deadline=deadline)
    try:
        data = json.loads(page.text)
    except ValueError as error:
        raise WorkdayError('The Workday job-detail response was invalid') from error
    info = data.get('jobPostingInfo', {})
    body = _strip_html(info.get('jobDescription', ''))
    company = source['company_name']
    stable_id = 'workday:{}:{}:{}'.format(tenant, site, info.get('jobReqId') or external_path)
    external_url = info.get('externalUrl') or detail_url
    return {
        'id': stable_id,
        'title': str(info.get('title') or listing.get('title') or '')[:300],
        'company': company,
        'location': str(info.get('location') or listing.get('locationsText') or '')[:300],
        'employment_type': str(info.get('timeType') or '')[:100],
        'posted_at': str(info.get('postedOn') or listing.get('postedOn') or '')[:80],
        'salary': _annual_salary(body),
        'url': external_url,
        'apply_url': external_url,
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


def _listings(base, tenant, site, keyword, deadline=None, max_pages=MAX_LIST_PAGES):
    listings, seen, total = [], set(), None
    for page_index in range(max_pages):
        data = _search(base, tenant, site, keyword, page_index * PAGE_SIZE, deadline=deadline)
        if total is None:
            total = int(data.get('total') or 0)
        postings = data.get('jobPostings') or []
        for item in postings:
            path = item.get('externalPath')
            if not path or path in seen:
                continue
            seen.add(path)
            listings.append({'externalPath': path, 'title': item.get('title', ''),
                             'locationsText': item.get('locationsText', ''),
                             'postedOn': item.get('postedOn', ''),
                             'keywords': [keyword] if keyword else []})
        if not postings or len(listings) >= total:
            break
    return listings, total


def detect_company(url, deadline=None):
    """Best-effort company-name guess for a Workday careers URL that hasn't
    been saved yet -- read from the tenant's own API, since the landing page
    itself is just an SPA-redirect stub with no useful static content."""
    tenant, wd_host, site, base = _parse(url)
    data = _search(base, tenant, site, '', 0, deadline=deadline)
    postings = data.get('jobPostings') or []
    if not postings:
        return {'company_name': ''}
    detail_url = '{}/wday/cxs/{}/{}{}'.format(base, tenant, site, postings[0].get('externalPath') or '')
    page = _fetch(detail_url, deadline=deadline)
    try:
        detail = json.loads(page.text)
    except ValueError:
        return {'company_name': ''}
    return {'company_name': _clean_company_name((detail.get('hiringOrganization') or {}).get('name', ''))}


def sample(source, deadline=None):
    """Bounded direct-source test used by the synchronous management endpoint."""
    tenant, wd_host, site, base = _parse(source.get('careers_url'))
    listings, total = _listings(base, tenant, site, '', deadline=deadline)
    return {'adapter': 'workday', 'count': len(listings), 'total_available': total,
            'titles': [item.get('title', '') for item in listings[:3]]}


def fetch_jobs(source, cfg, source_counts, user=None, test_mode=False):
    if test_mode:
        return []
    if not source.get('careers_url'):
        raise WorkdayError('A direct Workday source needs a careers URL')
    tenant, wd_host, site, base = _parse(source['careers_url'])
    keywords = [str(value).strip() for value in cfg.get('keywords', []) if str(value).strip()] or ['']
    max_details = min(MAX_DETAIL_JOBS, max(1, int(cfg.get('priority_employer_max_details', MAX_DETAIL_JOBS))))
    seen, jobs = set(), []
    for keyword in keywords:
        listings, _ = _listings(base, tenant, site, keyword)
        for listing in listings:
            path = listing['externalPath']
            if path in seen:
                continue
            seen.add(path)
            if len(jobs) >= max_details:
                break
            jobs.append(_detail_job(base, tenant, site, listing, source))
            time.sleep(DETAIL_DELAY_SECONDS)
        if len(jobs) >= max_details:
            break
    label = '{} careers'.format(source['company_name'])
    source_counts[label] = source_counts.get(label, 0) + len(jobs)
    return jobs
