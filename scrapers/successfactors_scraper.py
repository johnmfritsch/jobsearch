"""Public SAP SuccessFactors priority-employer adapter.

Supports server-rendered /go/ boards (Landis+Gyr) and the public unified
recruiting endpoint exposed by sites such as B. Braun.  It deliberately does
not use a browser or employee/login-only pages.
"""
import json
import re
import time
from datetime import datetime
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

import safe_fetch


USER_AGENT = 'JobSearchPriorityEmployer/1.0 (+https://fritsch-nas.myasustor.com/)'
MAX_LIST_PAGES = 3
MAX_DETAIL_JOBS = 120
DETAIL_DELAY_SECONDS = 0.20


class SuccessFactorsError(RuntimeError):
    pass


def _host(url):
    return (urlparse(url).hostname or '').lower().rstrip('.')


def _fetch(url, host, *, method='GET', body=None, timeout=15, max_bytes=1_500_000,
           deadline=None, allowed_content=('text/html', 'application/xhtml+xml')):
    if deadline is not None and time.monotonic() >= deadline:
        raise SuccessFactorsError('Priority-employer test reached its time limit')
    # SF's unified recruiting API 415s a JSON POST that omits this -- it was
    # missing entirely before, so that endpoint had never actually worked.
    headers = {'Content-Type': 'application/json'} if method == 'POST' and body else None
    try:
        return safe_fetch.fetch_text(
            url, method=method, body=body, headers=headers, timeout=timeout, max_redirects=3,
            max_bytes=max_bytes, allowed_schemes=('https',), allowed_ports=(443,),
            allowed_hosts={host}, allowed_content=allowed_content,
            user_agent=USER_AGENT)
    except safe_fetch.SafeFetchError as error:
        raise SuccessFactorsError(str(error)) from error


def _with_keyword(url, keyword):
    parsed = urlparse(url)
    pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key != 'q']
    pairs.append(('q', keyword))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params,
                       urlencode(pairs), ''))


def _locale(url, html):
    parsed = urlparse(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == 'locale' and re.fullmatch(r'[a-z]{2}_[A-Z]{2}', value or ''):
            return value
    match = re.search(r'currentLocale[^\n]{0,100}?["\']([a-z]{2}_[A-Z]{2})["\']', html)
    return match.group(1) if match else 'en_US'


def _text(soup):
    root = (soup.find('main') or soup.find(id=re.compile(r'(job|content)', re.I))
            or soup.body or soup)
    return re.sub(r'\s+', ' ', root.get_text(' ', strip=True)).strip()


def _label_value(text, label):
    match = re.search(r'%s\s*:?\s*([^|]{1,160})' % re.escape(label), text, re.I)
    return match.group(1).strip() if match else ''


def _annual_salary(text):
    match = re.search(r'\$\s*([\d,]{4,})\s*(?:-|to)\s*\$?\s*([\d,]{4,}).{0,60}(?:year|annual)', text, re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', ''))
    except ValueError:
        return None


def _apply_url(soup, page_url):
    for anchor in soup.find_all('a', href=True):
        if 'apply' in anchor.get_text(' ', strip=True).casefold():
            return urljoin(page_url, anchor['href'])
    return ''


def _detail_job(listing, source, host, deadline=None):
    page = _fetch(listing['url'], host, deadline=deadline, max_bytes=2_000_000)
    soup = BeautifulSoup(page.text, 'html.parser')
    title = (soup.find('h1').get_text(' ', strip=True)
             if soup.find('h1') else listing.get('title', ''))
    body = _text(soup)
    path_match = re.search(r'/([0-9]+)(?:[-_][a-z]{2}_[A-Z]{2})?/?$',
                           urlparse(page.url).path)
    requisition = (_label_value(body, 'Requisition ID') or listing.get('id')
                   or (path_match.group(1) if path_match else ''))
    location = _label_value(body, 'Location') or listing.get('location', '')
    posted = _label_value(body, 'Date Posted') or listing.get('posted_at', '')
    workplace = _label_value(body, 'Workplace Type')
    stable_id = 'successfactors:{}:{}'.format(host, requisition or page.url)
    company = source['company_name']
    return {
        'id': stable_id,
        'title': title[:300],
        'company': company,
        'location': location[:300],
        'employment_type': workplace[:100],
        'posted_at': posted[:80],
        'salary': _annual_salary(body),
        'url': page.url,
        'apply_url': _apply_url(soup, page.url),
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


def _html_total(html):
    # SF Career Site Builder's own results-summary text, e.g. "Page 1 of 2,
    # Results 1 to 25 of 43" -- gives the true company-wide count from page 1
    # alone, without needing to fetch every page just to count them.
    match = re.search(r'Results\s+\d+\s+to\s+\d+\s+of\s+(\d+)', html, re.I)
    return int(match.group(1)) if match else None


def _html_next_page_urls(html, page_url, page_size, max_pages):
    # These boards don't render a "Next" link -- only First/Last -- so the
    # only reliable next-page URL comes from reading the "Last Page" link's
    # offset segment and reusing that same template for the pages between.
    soup = BeautifulSoup(html, 'html.parser')
    last = soup.find('a', class_='paginationItemLast')
    if not last or not last.get('href') or not page_size:
        return []
    last_url = urljoin(page_url, last['href'])
    parsed = urlparse(last_url)
    parts = parsed.path.rstrip('/').split('/')
    try:
        last_offset = int(parts[-1])
    except ValueError:
        return []
    if last_offset <= 0:
        return []
    base_path = '/'.join(parts[:-1])
    total_pages = last_offset // page_size + 1
    urls = []
    for page_index in range(1, min(total_pages, max_pages)):
        offset = page_index * page_size
        urls.append(urlunparse((parsed.scheme, parsed.netloc, f'{base_path}/{offset}/',
                                '', parsed.query, '')))
    return urls


def _html_listings(search_url, source, keyword, deadline=None, max_pages=MAX_LIST_PAGES):
    host = _host(search_url)
    page = _fetch(search_url, host, deadline=deadline)
    listings, seen = [], set()

    def collect(html, page_url):
        soup = BeautifulSoup(html, 'html.parser')
        added = 0
        for anchor in soup.find_all('a', href=True):
            detail_url = urljoin(page_url, anchor['href'])
            if _host(detail_url) != host or '/job/' not in urlparse(detail_url).path:
                continue
            if detail_url in seen:
                continue
            seen.add(detail_url)
            listings.append({'url': detail_url,
                             'title': anchor.get_text(' ', strip=True),
                             'keywords': [keyword] if keyword else []})
            added += 1
        return added

    page_size = collect(page.text, page.url)
    total = _html_total(page.text)
    for next_url in _html_next_page_urls(page.text, page.url, page_size, max_pages):
        try:
            next_page = _fetch(next_url, host, deadline=deadline)
        except SuccessFactorsError:
            break
        collect(next_page.text, next_page.url)
    return listings, page.text, total


def _unified_listings(search_url, html, source, keyword, deadline=None, max_pages=MAX_LIST_PAGES):
    host = _host(search_url)
    locale = _locale(search_url, html)
    endpoint = urljoin(search_url, '/services/recruiting/v1/jobs')
    listings, seen = [], set()
    for page_number in range(max_pages):
        payload = json.dumps({'keywords': keyword, 'locale': locale, 'location': '',
                              'pageNumber': page_number, 'sortBy': 'recent'})
        response = _fetch(endpoint, host, method='POST', body=payload, deadline=deadline,
                          allowed_content=('application/json',))
        try:
            data = json.loads(response.text)
        except ValueError as error:
            raise SuccessFactorsError('The SuccessFactors listing response was invalid') from error
        results = data.get('jobSearchResult') or []
        for item in results:
            job = item.get('response', {}) if isinstance(item, dict) else {}
            job_id = str(job.get('id') or '')
            title_slug = str(job.get('unifiedUrlTitle') or job.get('urlTitle') or 'job')
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            location_values = job.get('jobLocationShort') or job.get('sfstd_jobLocation_obj') or []
            location = location_values[0] if isinstance(location_values, list) and location_values else ''
            listings.append({
                'id': job_id,
                'title': str(job.get('unifiedStandardTitle') or title_slug),
                'location': str(location),
                'posted_at': str(job.get('unifiedStandardStart') or ''),
                'url': urljoin(search_url, '/job/{}/{}-{}'.format(
                    quote(title_slug, safe='-()'), quote(job_id), locale)),
                'keywords': [keyword] if keyword else [],
            })
        total = int(data.get('totalJobs') or 0)
        if not results or len(listings) >= total:
            break
    return listings, total


def _initial_page(source, keyword, deadline=None):
    url = _with_keyword(source['careers_url'], keyword)
    host = _host(url)
    page = _fetch(url, host, deadline=deadline)
    return url, page.text


def _detect_company_name(html):
    soup = BeautifulSoup(html, 'html.parser')
    site_name = soup.find('meta', attrs={'property': 'og:site_name'})
    if site_name and site_name.get('content', '').strip():
        return site_name['content'].strip()[:120]
    # Careers-page footers reliably carry "(c) YYYY Employer Name. All rights
    # reserved." boilerplate even when there is no usable <title> or meta tag.
    copyright_match = re.search(
        r'[©]\s*\d{4}\s+([A-Z][\w&.,\'’+-]{1,80}?)\.?\s*(?:all rights reserved|inc\.?\s*$)',
        html, re.I)
    if copyright_match:
        return re.sub(r'\s+', ' ', copyright_match.group(1)).strip().rstrip('.,')[:120]
    if soup.title and soup.title.string and soup.title.string.strip():
        title = re.sub(r'\s+', ' ', soup.title.string).strip()
        title = re.split(r'\s*[-|–]\s*(?:careers|jobs|job search|current openings)\b',
                         title, maxsplit=1, flags=re.I)[0]
        return title.strip()[:120]
    # UKG/UltiPro career boards are a Knockout.js SPA with no static <title> or
    # meta tags -- everything is bound client-side -- but the page's bootstrap
    # JSON embeds the board's own branding name next to its BrandId, e.g.
    # "BrandId":"...","Name":"Buckeye Career Opportunities". That's the portal
    # name, not always the exact legal company name (a logo-rendered name like
    # "Buckeye Partners" can differ), so this is a best-effort starting point,
    # not a guarantee -- the field stays editable either way.
    brand_match = re.search(r'"BrandId"\s*:\s*"[0-9a-f-]+"\s*,\s*"Name"\s*:\s*"([^"]{2,120})"',
                            html, re.I)
    if brand_match:
        name = re.sub(r'\s*(?:career opportunities|careers|jobs)\s*$', '',
                      brand_match.group(1).strip(), flags=re.I)
        return name.strip()[:120]
    return ''


def detect_company(url, deadline=None):
    """Best-effort company-name guess for a careers URL that hasn't been saved yet."""
    host = _host(url)
    if not host:
        raise SuccessFactorsError('Enter a valid careers URL')
    page = _fetch(url, host, deadline=deadline)
    return {'company_name': _detect_company_name(page.text)}


def _listings_for(url, html, source, keyword, deadline=None):
    """Try the unified recruiting API first, fall back to HTML-listing scraping.

    Some SF "Career Site Builder" templates (React-widget boards, e.g. B.
    Braun) never embed a literal reference to /services/recruiting/v1/jobs in
    their static HTML -- they call it dynamically from a JS bundle -- so
    sniffing the initial page's markup for that string (the original
    approach) silently misses them. Probing the endpoint directly and only
    falling back on a real SuccessFactorsError (404/415/bad JSON -- i.e. this
    installation doesn't expose it there) is what a server-rendered /go/
    board would raise, telling apart "unsupported here" from "supported and
    genuinely zero results," which is a real, honest answer either way.
    """
    try:
        return _unified_listings(url, html, source, keyword, deadline=deadline)
    except SuccessFactorsError:
        listings, _, total = _html_listings(url, source, keyword, deadline=deadline)
        return listings, total


def sample(source, deadline=None):
    """Bounded direct-source test used by the synchronous management endpoint."""
    if not source.get('careers_url'):
        return {'adapter': 'serpapi_company', 'count': 0,
                'message': 'This employer uses a company-qualified SerpAPI search.'}
    url, html = _initial_page(source, '', deadline=deadline)
    listings, total = _listings_for(url, html, source, '', deadline=deadline)
    return {'adapter': 'successfactors', 'count': len(listings), 'total_available': total,
            'titles': [item.get('title', '') for item in listings[:3]]}


def fetch_jobs(source, cfg, source_counts, user=None, test_mode=False):
    if test_mode:
        return []
    if not source.get('careers_url'):
        raise SuccessFactorsError('A direct SuccessFactors source needs a careers URL')
    keywords = [str(value).strip() for value in cfg.get('keywords', []) if str(value).strip()] or ['']
    max_details = min(MAX_DETAIL_JOBS, max(1, int(cfg.get('priority_employer_max_details', MAX_DETAIL_JOBS))))
    seen, jobs = set(), []
    for keyword in keywords:
        url, html = _initial_page(source, keyword)
        listings, _ = _listings_for(url, html, source, keyword)
        for listing in listings:
            detail_url = listing['url']
            if detail_url in seen:
                continue
            seen.add(detail_url)
            if len(jobs) >= max_details:
                break
            jobs.append(_detail_job(listing, source, _host(url)))
            time.sleep(DETAIL_DELAY_SECONDS)
        if len(jobs) >= max_details:
            break
    label = '{} careers'.format(source['company_name'])
    source_counts[label] = source_counts.get(label, 0) + len(jobs)
    return jobs
