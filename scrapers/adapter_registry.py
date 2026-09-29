"""Single source of truth for priority-employer adapter dispatch.

Before this existed, "which adapter handles this URL" and "which function do
I call for that adapter" were both decided by hand-copied if/elif chains in
three separate places (profile_store._employer_adapter, and
employer_source_scraper's fetch_employer_jobs/test_employer_source) -- and
those chains had already drifted out of sync once (test_employer_source fell
through to the SuccessFactors scraper for every non-Workday adapter,
including serpapi_company, misreporting supported-but-unsupported sources).

Adding a new direct-scrape platform now means: write a module exposing
is_<x>_url/sample/fetch_jobs/detect_company (see workday_scraper.py,
ukg_scraper.py, successfactors_scraper.py for the shape), add one Adapter
entry below, and add its name to the CHECK constraint via a migration
(SQLite can't ALTER a CHECK list -- see migrations/018 and 020 for the
table-rebuild pattern). No other file needs to change.
"""
from collections import namedtuple

try:  # imported as scrapers.adapter_registry by Flask API requests
    from . import successfactors_scraper as sf
    from . import workday_scraper as wd
    from . import ukg_scraper as ukg
    from . import jibe_scraper as jibe
    from . import structured_data_scraper as structured
except ImportError:  # imported from SCRAPERS_DIR by detached main.py
    import successfactors_scraper as sf
    import workday_scraper as wd
    import ukg_scraper as ukg
    import jibe_scraper as jibe
    import structured_data_scraper as structured


Adapter = namedtuple('Adapter', ['name', 'is_url', 'sample', 'fetch_jobs', 'detect_company', 'errors'])


def _is_successfactors_url(url):
    # The two public SuccessFactors result shapes supported in release one:
    # server-rendered /go/ listing pages and unified /search/ job widgets.
    # Unlike Workday/UKG there's no distinguishing hostname -- this is
    # deliberately a loose, optimistic match, not a strict one.
    path = (str(url or '').split('?', 1)[0]).casefold()
    return '/go/' in path or '/search/' in path


ADAPTERS = (
    Adapter('workday', wd.is_workday_url, wd.sample, wd.fetch_jobs, wd.detect_company,
            (wd.WorkdayError,)),
    Adapter('ukg', ukg.is_ukg_url, ukg.sample, ukg.fetch_jobs, ukg.detect_company,
            (ukg.UKGError,)),
    Adapter('jibe', jibe.is_jibe_url, jibe.sample, jibe.fetch_jobs, jibe.detect_company,
            (jibe.JibeError,)),
    Adapter('structured_data', structured.is_structured_data_url, structured.sample,
            structured.fetch_jobs, structured.detect_company, (structured.StructuredDataError,)),
    Adapter('successfactors', _is_successfactors_url, sf.sample, sf.fetch_jobs, sf.detect_company,
            (sf.SuccessFactorsError,)),
)

_BY_NAME = {adapter.name: adapter for adapter in ADAPTERS}

# Adapter values that share the name-only SerpAPI search at runtime rather
# than a direct scrape -- see classify_url's docstring for why there are two.
SERPAPI_FALLBACK_ADAPTERS = ('serpapi_company', 'unsupported')

# The full set of legal values for employer_sources.adapter.
ADAPTER_NAMES = tuple(adapter.name for adapter in ADAPTERS) + SERPAPI_FALLBACK_ADAPTERS


def get_adapter(name):
    return _BY_NAME.get(name)


def classify_url(careers_url):
    """Decide which employer_sources.adapter value a careers URL gets.

    'serpapi_company' means "no careers URL was given -- deliberately
    searched by name only." 'unsupported' means "a careers URL was given,
    but it doesn't match any adapter this app knows how to scrape directly"
    -- both fall back to the same name-only SerpAPI search at runtime, but a
    user who pasted a real (just unrecognized) URL deserves a different,
    more actionable message than one who never gave one.
    """
    if not careers_url:
        return 'serpapi_company'
    for adapter in ADAPTERS:
        if adapter.is_url(careers_url):
            return adapter.name
    return 'unsupported'


def detect_company_for_url(careers_url, deadline=None):
    """Best-effort company-name guess for a careers URL that hasn't been
    saved yet. Direct-adapter URLs use that adapter's own detector; anything
    else (including 'unsupported' -- a real page, just not one this app can
    scrape for job listings) still gets the generic SuccessFactors-style
    fetch-and-guess detector, which is deliberately platform-agnostic (og
    meta tag / copyright footer / <title> / UKG bootstrap-JSON brand name)
    and works reasonably well on many career pages regardless of platform."""
    adapter = get_adapter(classify_url(careers_url))
    if adapter:
        return adapter.detect_company(careers_url, deadline=deadline)
    return sf.detect_company(careers_url, deadline=deadline)


def probe_url(careers_url, deadline=None):
    """Return a positive platform detection record, or an honest fallback.

    Jibe and structured-data support is content-proven, not suffix-proven. Run
    these probes before the legacy loose SuccessFactors path matcher so a
    `/search/` URL cannot preempt a positively identified new platform.
    """
    for probe in (jibe.probe, structured.probe):
        record = probe(careers_url, deadline=deadline)
        if record:
            return record
    adapter = classify_url(careers_url)
    try:
        company = detect_company_for_url(careers_url, deadline=deadline)
    except Exception:
        company = {'company_name': ''}
    return {'platform': adapter if adapter != 'unsupported' else 'Unknown pending probe',
            'adapter': adapter, 'company_name': company.get('company_name', ''),
            'capabilities': {},
            'explanation': ('No supported direct platform was positively identified.'
                            if adapter == 'unsupported' else 'Recognized existing direct platform.')}
