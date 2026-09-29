"""Dispatch direct employer sites and company-qualified provider fallback."""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import safe_fetch
from profile_store import normalize_employer_name
try:  # imported as scrapers.employer_source_scraper by Flask API requests
    from .successfactors_scraper import (SuccessFactorsError,
        _detect_company_name, _host, _listings_for, _html_listings)
    from .adapter_registry import get_adapter, SERPAPI_FALLBACK_ADAPTERS
    from .adapter_utils import employer_name_matches
except ImportError:  # imported from SCRAPERS_DIR by detached main.py
    from successfactors_scraper import (SuccessFactorsError,
        _detect_company_name, _host, _listings_for, _html_listings)
    from adapter_registry import get_adapter, SERPAPI_FALLBACK_ADAPTERS
    from adapter_utils import employer_name_matches


class EmployerSourceError(RuntimeError):
    pass


def _active_serpapi_key(cfg):
    entries = cfg.get('credentials', {}).get('serpapi', [])
    active = next((entry for entry in entries if isinstance(entry, dict)
                   and entry.get('name', '').casefold() == 'active'), None)
    return active.get('key', '') if active else cfg.get('serpapi_key', '')


def _company_matches(candidate, source):
    return employer_name_matches(candidate, source)


def _targeted_serpapi(source, cfg, source_counts):
    api_key = _active_serpapi_key(cfg)
    if not api_key or not cfg.get('sources', {}).get('serpapi'):
        raise EmployerSourceError('SerpAPI is required for this company-name priority employer')
    keywords = [str(value).strip() for value in cfg.get('keywords', []) if str(value).strip()]
    if not keywords:
        return []
    pages = max(1, min(3, int(cfg.get('priority_employer_serpapi_max_pages', 1))))
    modes = []
    if cfg.get('search_local'):
        modes.append(('local', False))
    if cfg.get('search_remote'):
        modes.append(('remote', True))
    jobs = []
    for keyword in keywords:
        query = '{} {}'.format(keyword, source['company_name'])
        for mode, remote in modes:
            for _ in range(pages):
                params = {'engine': 'google_jobs', 'q': query, 'hl': 'en', 'gl': 'us',
                          'api_key': api_key}
                if cfg.get('location') and not remote:
                    params['location'] = cfg['location']
                try:
                    response = requests.get('https://serpapi.com/search.json', params=params, timeout=30)
                    response.raise_for_status()
                    data = response.json()
                except requests.RequestException as error:
                    raise EmployerSourceError('SerpAPI company search could not be completed') from error
                if data.get('error'):
                    raise EmployerSourceError('SerpAPI company search was refused: {}'.format(data['error']))
                for item in data.get('jobs_results', []):
                    if not _company_matches(item.get('company_name', ''), source):
                        continue
                    url = item.get('apply_link') or ''
                    if not url:
                        for option in item.get('apply_options') or []:
                            if isinstance(option, dict) and str(option.get('link', '')).startswith('http'):
                                url = option['link']
                                break
                    jobs.append({
                        'title': str(item.get('title') or '').strip(),
                        'company': str(item.get('company_name') or '').strip(),
                        'url': url,
                        'salary': (item.get('detected_extensions') or {}).get('salary', ''),
                        'snippet': str(item.get('description') or '')[:400],
                        'description': str(item.get('description') or ''),
                        'location': str(item.get('location') or ''),
                        'latitude': item.get('latitude'), 'longitude': item.get('longitude'),
                        'source': 'SerpAPI', '_sources': ['SerpAPI'],
                        '_target_employers': [source['company_name']],
                        '_search_keywords': [keyword], '_search_modes': [mode],
                    })
                time.sleep(1.2)
    source_counts['SerpAPI'] = source_counts.get('SerpAPI', 0) + len(jobs)
    return jobs


def _domain_slug(company_name):
    return re.sub(r'[^a-z0-9]+', '', str(company_name or '').casefold())


def _candidate_root_urls(company_name):
    slug = _domain_slug(company_name)
    if not slug:
        return []
    return ['https://careers.{}.com/'.format(slug), 'https://jobs.{}.com/'.format(slug)]


def _single_board_link(html, page_url, host):
    """A bare careers-subdomain landing page for a /go/-style SF board
    typically carries exactly one link into the actual numbered job-family
    board -- that ID isn't guessable on its own (e.g. Landis+Gyr's bare
    careers.landisgyr.com/go/ page links to .../View-All-Jobs-Available/4488801/)."""
    soup = BeautifulSoup(html, 'html.parser')
    found = set()
    for anchor in soup.find_all('a', href=True):
        target = urljoin(page_url, anchor['href'])
        if _host(target) == host and re.search(r'/go/[^/]+/\d+/?', urlparse(target).path):
            found.add(target)
    return next(iter(found)) if len(found) == 1 else None


def discover_careers_url(company_name, deadline=None):
    """Free direct-careers-page guess for a company name alone -- no paid
    search API involved, just the careers./jobs. subdomain conventions most
    SuccessFactors-hosted sites use. Returns '' on anything short of a
    confirmed match; callers should treat that as "couldn't determine one"
    and fall back to the company-name-only SerpAPI adapter, exactly as if no
    URL had ever been given.
    """
    source = {'company_name': company_name}
    for root in _candidate_root_urls(company_name):
        if deadline is not None and time.monotonic() >= deadline:
            break
        host = _host(root)
        try:
            page = safe_fetch.fetch_text(root, allowed_schemes=('https',), allowed_ports=(443,),
                                         timeout=8, max_bytes=1_500_000)
        except safe_fetch.SafeFetchError:
            continue
        found_name = _detect_company_name(page.text)
        if not found_name or not _company_matches(found_name, source):
            continue  # resolves, but isn't actually this company's site
        try:
            listings, total = _listings_for(page.url, page.text, source, '', deadline=deadline)
        except SuccessFactorsError:
            listings, total = [], None
        if listings or total:
            return page.url
        board_url = _single_board_link(page.text, page.url, host)
        if not board_url:
            continue
        try:
            listings, _, total = _html_listings(board_url, source, '', deadline=deadline)
        except SuccessFactorsError:
            continue
        if listings or total:
            return board_url
    return ''


SERPAPI_FALLBACK_MESSAGES = {
    'serpapi_company': 'This employer uses a company-qualified SerpAPI search.',
    'unsupported': ("This company's careers site isn't a job-board platform this app "
                    "can check directly yet, so it's searched by name on Google Jobs "
                    "through SerpAPI instead. Ask your administrator to add direct "
                    "support for this site if you'd like it checked precisely."),
}


def fetch_employer_jobs(source, cfg, source_counts, user=None, test_mode=False):
    # scrapers/adapter_registry.py is the single source of truth for which
    # function handles which adapter -- see its module docstring for why
    # (this dispatch and test_employer_source()'s below drifted out of sync
    # once already, Round 10).
    if test_mode:
        return []
    adapter = get_adapter(source.get('adapter'))
    if adapter:
        try:
            return adapter.fetch_jobs(source, cfg, source_counts, user=user, test_mode=test_mode)
        except adapter.errors as error:
            raise EmployerSourceError(str(error)) from error
    if source.get('adapter') in SERPAPI_FALLBACK_ADAPTERS:
        return _targeted_serpapi(source, cfg, source_counts)
    raise EmployerSourceError('That priority employer uses an unsupported source type')


def test_employer_source(source, deadline):
    adapter = get_adapter(source.get('adapter'))
    if adapter:
        try:
            return adapter.sample(source, deadline=deadline)
        except adapter.errors as error:
            raise EmployerSourceError(str(error)) from error
    message = SERPAPI_FALLBACK_MESSAGES.get(
        source.get('adapter'), SERPAPI_FALLBACK_MESSAGES['serpapi_company'])
    return {'adapter': source.get('adapter') or 'serpapi_company', 'count': 0, 'message': message}
