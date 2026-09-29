"""Manual job entry: turn a user-supplied posting URL into tracker metadata.

Why this lives outside the pipeline
-----------------------------------
main.py runs on entware Python (sentence-transformers lives there) as a
detached subprocess. This module runs inside a Flask request on AppCentral
Python. Everything here is therefore deliberately dependency-light --
`requests` and `beautifulsoup4` only, both already installed in the web
interpreter -- and it does no resume scoring at all. A manually added job has
no match score by design: the scorer is not importable here, and inventing a
number would be worse than leaving the field empty.

Extraction is tiered, best source first:

1. ATS public JSON APIs (Greenhouse, Lever, Ashby). Structured and exact --
   no HTML parsing and no guessing.
2. schema.org JobPosting JSON-LD embedded in the page. Most corporate career
   sites emit this so Google Jobs can index them.
3. OpenGraph meta tags, then the <title> element. Genuinely a guess, and
   tagged as such.

Every field carries an entry in `provenance` so the modal can distinguish a
Greenhouse API response from a <title> split instead of presenting both with
the same confidence.
"""
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import safe_fetch

TIMEOUT = 10
MAX_REDIRECTS = 3
MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = ('Mozilla/5.0 (compatible; JobSearchManualEntry/1.0; '
              '+https://fritsch-nas.myasustor.com/)')
ALLOWED_CONTENT = ('text/html', 'application/xhtml', 'application/json',
                   'text/plain', 'application/ld+json')

# Query parameters that identify the campaign, not the posting. Stripping them
# matters for more than tidiness: the job key is derived from this URL, so a
# posting saved from a newsletter link and the same posting seen by a scraper
# must normalize to the same string or the ignore list will not match them.
TRACKING_PARAMS = re.compile(
    r'^(utm_[a-z_]+|fbclid|gclid|mc_[a-z]+|ref|referrer|trk|trackingId|'
    r'src|source_id|campaign_id)$', re.I)

# urlparse is happy to call 'not a url' a hostname, which then reaches DNS and
# the fetch path. A posting URL always has a dotted name or a literal IP.
HOSTNAME = safe_fetch.HOSTNAME

REMOTE_WORDS = re.compile(r'\b(remote|work from home|wfh|distributed|anywhere)\b', re.I)
HYBRID_WORDS = re.compile(r'\bhybrid\b', re.I)
TITLE_SPLIT = re.compile(r'\s+(?:[-–—|·•]|at|@)\s+')


class ManualJobError(Exception):
    """A user-fixable problem with the supplied URL or the response to it."""


# -- SSRF guard --------------------------------------------------------------

def _valid_hostname(host):
    """True for a dotted DNS name or a literal IP address, nothing else."""
    return safe_fetch.valid_hostname(host)


def _public_host(hostname):
    """Compatibility wrapper for the shared DNS-pinned public-host guard."""
    return safe_fetch.public_host(hostname)


# -- URL handling ------------------------------------------------------------

def normalize_url(url):
    """Return a canonical form of the posting URL, or '' if it is unusable.

    Kept deliberately conservative. Anything beyond case-folding the host and
    dropping tracking noise risks normalizing away the very parameter that
    identifies the job (Greenhouse's `gh_jid`, Workday's job id).
    """
    text = str(url or '').strip()
    if not text:
        return ''
    if not re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I):
        text = 'https://' + text
    parsed = urlparse(text)
    if (parsed.scheme.lower() not in ('http', 'https')
            or not _valid_hostname(parsed.hostname)):
        return ''
    query = [(key, value)
             for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not TRACKING_PARAMS.match(key)]
    host = parsed.hostname.lower()
    if parsed.port:
        host = '{}:{}'.format(host, parsed.port)
    path = parsed.path.rstrip('/') or '/'
    return urlunparse((parsed.scheme.lower(), host, path, parsed.params,
                       urlencode(query), ''))


def _get(url):
    """Fetch a one-off preview through the shared DNS-pinned text reader."""
    try:
        result = safe_fetch.fetch_text(
            url, timeout=TIMEOUT, max_redirects=MAX_REDIRECTS,
            max_bytes=MAX_BYTES, allowed_schemes=('http', 'https'),
            allowed_content=ALLOWED_CONTENT, user_agent=USER_AGENT)
        return result.url, result.content_type, result.text
    except safe_fetch.SafeFetchError as error:
        message = str(error).replace('which is not an allowed document type',
                                     'which is not a job posting page')
        raise ManualJobError(message) from error


def _get_json(url):
    try:
        _, _, text = _get(url)
        return json.loads(text)
    except (ManualJobError, ValueError):
        return None


# -- field helpers -----------------------------------------------------------

def _clean(value, limit=300):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text[:limit]


def _slug_to_name(slug):
    """Turn an ATS board slug into something presentable ('acme-corp' -> 'Acme Corp')."""
    words = re.split(r'[-_]+', str(slug or '').strip())
    return ' '.join(word[:1].upper() + word[1:] for word in words if word)


def _remote_status(*texts):
    joined = ' '.join(_clean(text, 2000) for text in texts if text)
    if HYBRID_WORDS.search(joined):
        return 'hybrid'
    if REMOTE_WORDS.search(joined):
        return 'remote'
    return 'unclear'


def _annual_salary(node):
    """Return an annual figure from a schema.org baseSalary, or None.

    Only yearly amounts are usable: the tracker's minimum-salary filter
    compares raw numbers, so letting an hourly rate through would make a
    $75/hr job look like it pays $75.
    """
    base = node.get('baseSalary') if isinstance(node, dict) else None
    if isinstance(base, (int, float)):
        return float(base) if base >= 1000 else None
    if not isinstance(base, dict):
        return None
    value = base.get('value')
    if isinstance(value, (int, float)):
        return float(value) if value >= 1000 else None
    if not isinstance(value, dict):
        return None
    unit = str(value.get('unitText') or '').upper()
    if unit and unit != 'YEAR':
        return None
    for field in ('minValue', 'value', 'maxValue'):
        amount = value.get(field)
        if isinstance(amount, (int, float)) and amount >= 1000:
            return float(amount)
    return None


def _address(node):
    location = node.get('jobLocation')
    if isinstance(location, list):
        location = location[0] if location else None
    if isinstance(location, str):
        return _clean(location)
    if not isinstance(location, dict):
        return ''
    address = location.get('address')
    if isinstance(address, str):
        return _clean(address)
    if not isinstance(address, dict):
        return _clean(location.get('name'))
    parts = [address.get('addressLocality'), address.get('addressRegion'),
             address.get('addressCountry')]
    if isinstance(parts[2], dict):
        parts[2] = parts[2].get('name')
    return _clean(', '.join(_clean(part, 80) for part in parts if part))


# -- tier 1: ATS JSON APIs ---------------------------------------------------

def _greenhouse(parsed):
    match = re.search(r'/([^/]+)/jobs/(\d+)', parsed.path)
    if not match:
        return None
    board, job_id = match.group(1), match.group(2)
    job = _get_json(
        'https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}'.format(board, job_id))
    if not isinstance(job, dict) or not job.get('title'):
        return None
    meta = _get_json(
        'https://boards-api.greenhouse.io/v1/boards/{}'.format(board)) or {}
    company = _clean(meta.get('name')) or _slug_to_name(board)
    location = _clean((job.get('location') or {}).get('name'))
    return {
        'title': _clean(job.get('title')),
        'company': company,
        'location': location,
        'apply_url': _clean(job.get('absolute_url'), 2000),
        'posted_at': _clean(job.get('updated_at') or job.get('first_published'), 40),
        'remote_status': _remote_status(location, job.get('title')),
        'ats': 'greenhouse',
    }


def _lever(parsed):
    match = re.match(r'^/([^/]+)/([0-9a-f-]{16,})', parsed.path, re.I)
    if not match:
        return None
    company, posting = match.group(1), match.group(2)
    job = _get_json(
        'https://api.lever.co/v0/postings/{}/{}'.format(company, posting))
    if not isinstance(job, dict) or not job.get('text'):
        return None
    categories = job.get('categories') or {}
    location = _clean(categories.get('location'))
    return {
        'title': _clean(job.get('text')),
        'company': _slug_to_name(company),
        'location': location,
        'apply_url': _clean(job.get('applyUrl') or job.get('hostedUrl'), 2000),
        'posted_at': _clean(job.get('createdAt'), 40),
        'remote_status': _remote_status(location, categories.get('commitment'),
                                        job.get('workplaceType')),
        'ats': 'lever',
    }


def _ashby(parsed):
    match = re.match(r'^/([^/]+)/([0-9a-f-]{16,})', parsed.path, re.I)
    if not match:
        return None
    org, posting = match.group(1), match.group(2)
    board = _get_json(
        'https://api.ashbyhq.com/posting-api/job-board/{}'
        '?includeCompensation=true'.format(org))
    jobs = board.get('jobs') if isinstance(board, dict) else None
    if not isinstance(jobs, list):
        return None
    job = next((item for item in jobs
                if str(item.get('id', '')).lower() == posting.lower()), None)
    if not isinstance(job, dict) or not job.get('title'):
        return None
    location = _clean(job.get('location'))
    return {
        'title': _clean(job.get('title')),
        'company': _clean(board.get('name')) or _slug_to_name(org),
        'location': location,
        'apply_url': _clean(job.get('applyUrl') or job.get('jobUrl'), 2000),
        'posted_at': _clean(job.get('publishedAt'), 40),
        'remote_status': 'remote' if job.get('isRemote') else _remote_status(location),
        'ats': 'ashby',
    }


ATS_EXTRACTORS = (
    ('greenhouse', ('greenhouse.io',), _greenhouse),
    ('lever', ('lever.co',), _lever),
    ('ashby', ('ashbyhq.com',), _ashby),
)


def from_ats(url):
    """Return structured fields from a known ATS API, or None."""
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    for _, suffixes, extractor in ATS_EXTRACTORS:
        if any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes):
            try:
                return extractor(parsed)
            except ManualJobError:
                raise
            except Exception:
                # An ATS that changed its API shape is a reason to fall back to
                # HTML parsing, not a reason to fail the whole entry.
                return None
    return None


# -- tier 2: JSON-LD, OpenGraph, <title> -------------------------------------

def _iter_nodes(payload):
    if isinstance(payload, list):
        for item in payload:
            for node in _iter_nodes(item):
                yield node
    elif isinstance(payload, dict):
        yield payload
        for key in ('@graph', 'mainEntity', 'itemListElement'):
            if key in payload:
                for node in _iter_nodes(payload[key]):
                    yield node


def _job_posting_node(soup):
    for script in soup.find_all('script', type=re.compile(r'ld\+json', re.I)):
        raw = script.string or script.get_text() or ''
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        for node in _iter_nodes(payload):
            types = node.get('@type')
            types = types if isinstance(types, list) else [types]
            if any(str(item).lower() == 'jobposting' for item in types):
                return node
    return None


def from_json_ld(soup):
    node = _job_posting_node(soup)
    if not node:
        return None
    organization = node.get('hiringOrganization')
    if isinstance(organization, list):
        organization = organization[0] if organization else None
    company = (organization.get('name') if isinstance(organization, dict)
               else organization)
    location = _address(node)
    remote = ('remote'
              if str(node.get('jobLocationType') or '').upper() == 'TELECOMMUTE'
              else _remote_status(location, node.get('title'),
                                  node.get('employmentType')))
    return {
        'title': _clean(node.get('title')),
        'company': _clean(company),
        'location': location,
        'posted_at': _clean(node.get('datePosted'), 40),
        'salary': _annual_salary(node),
        'remote_status': remote,
    }


def from_opengraph(soup):
    def meta(*names):
        for name in names:
            tag = (soup.find('meta', property=name)
                   or soup.find('meta', attrs={'name': name}))
            if tag and tag.get('content'):
                return _clean(tag['content'])
        return ''
    title = meta('og:title', 'twitter:title')
    company = meta('og:site_name', 'application-name')
    description = meta('og:description', 'description')
    if not (title or company):
        return None
    return {
        'title': title,
        'company': company,
        'remote_status': _remote_status(title, description),
    }


def from_title(soup):
    """Split a page <title> on the usual separators. Genuinely a guess."""
    raw = _clean(soup.title.get_text() if soup.title else '')
    if not raw:
        return None
    parts = [part for part in TITLE_SPLIT.split(raw) if part]
    if len(parts) < 2:
        return {'title': raw}
    return {'title': parts[0], 'company': parts[-1]}


# -- orchestration -----------------------------------------------------------

FIELDS = ('title', 'company', 'location', 'remote_status', 'salary',
          'posted_at', 'apply_url')

# 'unclear' is a placeholder, not an answer: a later tier that actually knows
# the work style should be allowed to overwrite it.
WEAK_VALUES = (None, '', 'unclear')


def _merge(target, provenance, values, label):
    """Fill only fields still empty, recording which tier supplied each one."""
    if not values:
        return
    for field in FIELDS:
        value = values.get(field)
        if value in WEAK_VALUES:
            continue
        if target.get(field) not in WEAK_VALUES:
            continue
        target[field] = value
        provenance[field] = label


def extract(url):
    """Return {'url', 'fields', 'provenance', 'warnings'} for a posting URL.

    Never raises for a merely unhelpful page -- an unparseable posting comes
    back with empty fields and a warning so the user can type the two fields
    that matter by hand. It does raise ManualJobError for a URL that is
    malformed, unreachable, or aimed at the LAN.
    """
    normalized = normalize_url(url)
    if not normalized:
        raise ManualJobError('That does not look like a web address.')

    fields, provenance, warnings = {}, {}, []
    ats_values = from_ats(normalized)
    if ats_values:
        _merge(fields, provenance, ats_values, ats_values.get('ats', 'ats'))

    if not (fields.get('title') and fields.get('company')):
        final_url, content_type, body = _get(normalized)
        if 'json' in (content_type or ''):
            warnings.append(
                'That address returned raw data rather than a job posting page.')
        else:
            soup = BeautifulSoup(body, 'html.parser')
            _merge(fields, provenance, from_json_ld(soup), 'json-ld')
            _merge(fields, provenance, from_opengraph(soup), 'opengraph')
            _merge(fields, provenance, from_title(soup), 'page-title')
        if final_url and final_url != normalized:
            normalized = normalize_url(final_url) or normalized

    if not fields.get('title'):
        warnings.append('No job title could be read from that page. Please type one.')
    if not fields.get('company'):
        warnings.append('No company name could be read from that page. Please type one.')
    if 'page-title' in (provenance.get('title'), provenance.get('company')):
        warnings.append('Some values were guessed from the page title. '
                        'Check them before saving.')

    fields.setdefault('remote_status', 'unclear')
    return {'url': normalized, 'fields': fields, 'provenance': provenance,
            'warnings': warnings}
