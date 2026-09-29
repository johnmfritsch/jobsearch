import os
# Environment: prefer JOBSEARCH_ENV (set by config.py / the Flask app that
# spawns this as a subprocess); fall back to a path check matching the
# /volume1/Web/JobSearch[_dev] layout for standalone invocation. The old
# "/DEV" substring check predates the re-platform and no longer matches
# any real path.
if os.environ.get("JOBSEARCH_ENV") == "production":
    environment = "PROD"
elif os.environ.get("JOBSEARCH_ENV") == "development":
    environment = "DEV"
elif any(p.endswith("_dev") for p in os.path.abspath(__file__).split(os.sep)):
    environment = "DEV"
else:
    environment = "PROD"
os.environ["JOB_SEARCH_ENV"] = environment
# filters.py
import re
import json
import time
import requests
from math import radians, sin, cos, asin, sqrt
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("WARNING: beautifulsoup4 not installed - full page fetch disabled")


def _distance_miles(lat1, lon1, lat2, lon2):
    """Return great-circle distance between two coordinates in miles."""
    R = 3958.8  # Earth radius in miles
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * asin(sqrt(a))


_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# =====================================================================
# Apply URL extraction — find the actual company job posting URL
# =====================================================================

# Known aggregator and intermediary job board domains (NOT the actual employer)
_JOB_BOARD_DOMAINS = {
    # Primary aggregators
    "adzuna.com", "indeed.com", "linkedin.com", "glassdoor.com",
    "ziprecruiter.com", "monster.com", "careerbuilder.com",
    "simplyhired.com", "dice.com",
    # Intermediary boards / scrapers
    "career.io", "talent.com", "neuvoo.com", "jooble.org",
    "jobrapido.com", "adzuna.co.uk", "reed.co.uk",
    "getwork.com", "lensa.com", "recruiter.com",
    "whatjobs.com", "learn4good.com", "jobcase.com",
    "snagajob.com", "postjobfree.com", "jobs2careers.com",
    "jobing.com", "trovit.com", "jobisland.com",
    # Social / general
    "google.com", "facebook.com", "twitter.com",
}

# "Easy Apply" stays on the aggregator — always skip
_EASY_APPLY_RE = re.compile(r"\beasy\s+apply\b", re.IGNORECASE)

# Preferred: text that explicitly says "apply on company site" / "apply externally"
_APPLY_EXTERNAL_RE = re.compile(
    r"apply\s+(on\s+company|on\s+employer|externally|at\s+\w+\.\w+)",
    re.IGNORECASE
)

# General: any "apply" text (but not "easy apply")
_APPLY_GENERAL_RE = re.compile(r"\bapply\b", re.IGNORECASE)

MAX_APPLY_HOPS = 3


def _get_domain(url):
    """Extract domain from URL."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _get_parent_domain(url):
    """Extract parent domain (e.g., 'adzuna.com' from 'www.adzuna.com')."""
    domain = _get_domain(url)
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else domain


def _is_job_board(url):
    """Check if URL is a known aggregator or intermediary job board."""
    parent = _get_parent_domain(url)
    return parent in _JOB_BOARD_DOMAINS


# Reuse the `environment` resolved at import time rather than re-deriving it
# from __file__ — the old "/DEV" substring check silently selected the PROD
# container (3000) from the dev app, since /volume1/Web/JobSearch_dev has no
# "/DEV" path segment.
PLAYWRIGHT_SERVICE_URL = "http://127.0.0.1:" + ("3001" if environment == "DEV" else "3000")


def _playwright_available():
    """Check if the Playwright microservice is reachable (quick 2s health check)."""
    try:
        r = requests.get(f"{PLAYWRIGHT_SERVICE_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _resolve_with_playwright(url, timeout=40):
    """Send URL to Playwright service for JS-redirect resolution.
    Returns resolved URL, or original on failure."""
    try:
        r = requests.post(
            f"{PLAYWRIGHT_SERVICE_URL}/resolve",
            json={"url": url},
            timeout=timeout
        )
        if r.status_code == 200:
            return r.json().get("resolved_url", url)
    except Exception:
        pass
    return url


def _find_apply_link_in_soup(soup, page_url, source):
    """
    Parse HTML soup for the best apply link that points off-site.
    Returns URL string or None.
    """
    page_parent = _get_parent_domain(page_url)

    # Adzuna-specific: /land/ad/ links are JS-based redirects to the employer.
    # We cannot follow them server-side (403), but they work in a browser.
    # Store the /land/ad/ URL — downstream agents with browser access can resolve it.
    if source == "Adzuna" or "adzuna.com" in page_url:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "/land/" in href and "adzuna" in href and "apply" in text.lower():
                return href  # Store the /land/ad/ URL directly

    # General: find <a> tags with apply-related text
    preferred = []   # "apply on company site", "apply externally", etc.
    general = []     # generic "apply" (but not "easy apply")

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if not href.startswith("http"):
            continue
        # Skip "Easy Apply" links — they stay on the aggregator
        if _EASY_APPLY_RE.search(text):
            continue
        link_parent = _get_parent_domain(href)
        if link_parent == page_parent:
            continue  # Same site — not useful
        # Prioritize explicit external-apply text
        if _APPLY_EXTERNAL_RE.search(text):
            preferred.append(href)
        elif _APPLY_GENERAL_RE.search(text):
            general.append(href)

    # Return best candidate: prefer explicit external links, fall back to general
    best = preferred + general
    return best[0] if best else None


def _extract_apply_url(soup, original_url, final_url, source, job_name=""):
    """
    Extract the actual company apply URL by following the apply-link chain.

    Logic:
    1. If the page we fetched redirected to a non-board domain, that's the company URL.
    2. Otherwise, parse HTML for apply links pointing off-site.
    3. If the found link is a job board, fetch it and repeat (up to MAX_APPLY_HOPS).
    4. For Adzuna: store /land/ad/ URL (JS redirect, works in browser but not server-side).

    Returns: company URL string, or None.
    """
    # Step 1: Check if page itself redirected to a non-board site
    if final_url != original_url and not _is_job_board(final_url):
        return final_url

    # Step 2: Find apply link from HTML
    apply_href = _find_apply_link_in_soup(soup, original_url, source)

    if not apply_href:
        return None

    # For Adzuna /land/ links: these are JS redirects that 403 server-side.
    # Store them directly — they work in a browser and can be resolved by
    # downstream agents with browser capabilities.
    if "adzuna.com/land/" in apply_href:
        return apply_href

    # Step 3: If apply link is not a job board, we're done
    if not _is_job_board(apply_href):
        return apply_href

    # Step 4: Multi-hop — follow through intermediary boards
    current_url = apply_href
    for hop in range(1, MAX_APPLY_HOPS):
        try:
            resp = requests.get(current_url, timeout=10,
                                allow_redirects=True, headers=_FETCH_HEADERS)

            # Check if redirect landed on company site
            if not _is_job_board(resp.url):
                return resp.url

            # If still on a job board AND Playwright is available, try JS resolution
            if _playwright_available() and hop == 1:
                resolved = _resolve_with_playwright(current_url)
                if resolved != current_url and not _is_job_board(resolved):
                    print(f"  [APPLY-URL-PLAYWRIGHT] {current_url[:60]} -> {resolved[:60]}")
                    return resolved

            # Parse this intermediary page for its apply link
            hop_soup = BeautifulSoup(resp.text, "html.parser")
            next_href = _find_apply_link_in_soup(hop_soup, current_url, "")

            if not next_href:
                # No further apply link found; return current if non-board
                return resp.url if not _is_job_board(resp.url) else None

            if not _is_job_board(next_href):
                return next_href

            current_url = next_href
        except Exception:
            # Network error on hop — try Playwright as fallback for JS redirects
            if _playwright_available():
                resolved = _resolve_with_playwright(current_url)
                if resolved != current_url and not _is_job_board(resolved):
                    print(f"  [APPLY-URL-PLAYWRIGHT] {current_url[:60]} -> {resolved[:60]}")
                    return resolved
            return current_url if not _is_job_board(current_url) else None

    # Exhausted hops — return last URL if non-board
    return current_url if not _is_job_board(current_url) else None


# =====================================================================
# Job description fetching
# =====================================================================

# Common job description container selectors (tried in order)
_JOB_CONTENT_SELECTORS = [
    {"id": "job-description"},
    {"id": "jobDescription"},
    {"id": "job-details"},
    {"id": "jobDetails"},
    {"class_": "job-description"},
    {"class_": "jobDescription"},
    {"class_": "job-details"},
    {"class_": "description"},
    {"class_": "posting-description"},
    {"role": "main"},
]


def _adzuna_details_url(url):
    """
    Convert an Adzuna /land/ad/ URL to a /details/ URL so we can fetch the page.
    /land/ad/ URLs return 403, but /details/<id> pages are fetchable.
    Returns the converted URL, or the original if not an Adzuna /land/ URL.
    """
    m = re.match(r'https?://www\.adzuna\.com/land/ad/(\d+)', url)
    if m:
        job_id = m.group(1)
        return f"https://www.adzuna.com/details/{job_id}"
    return url


def _fetch_full_description(job, timeout=10):
    """
    Fetch the actual job posting page and extract job description text.
    Returns extracted text, or None if fetch fails.
    Sets job['full_fetch'] to 'success' or 'failed'.
    Also sets job['apply_url'] if a company apply link is found.
    """
    if not BS4_AVAILABLE:
        job["full_fetch"] = "no_bs4"
        return None

    url = job.get("url", "")
    if not url:
        job["full_fetch"] = "no_url"
        return None

    # Convert Adzuna /land/ad/ URLs to /details/ URLs (the /land/ ones 403)
    fetch_url = _adzuna_details_url(url)

    job_name = job.get("title", "N/A")

    try:
        resp = requests.get(fetch_url, timeout=timeout, allow_redirects=True,
                            headers=_FETCH_HEADERS)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract apply URL BEFORE stripping navigation elements
        # (apply buttons are often in nav/header that get decomposed below)
        apply_url = _extract_apply_url(soup, fetch_url, resp.url, job.get("source", ""), job_name)
        if apply_url:
            job["apply_url"] = apply_url
            print(f"  [APPLY-URL] '{job_name[:50]}' -> {apply_url[:80]}")

        # Remove noise elements
        for tag in soup.find_all(["script", "style", "nav", "header",
                                   "footer", "aside", "noscript"]):
            tag.decompose()

        # Try targeted selectors first
        content = None
        for selector in _JOB_CONTENT_SELECTORS:
            content = soup.find("div", **selector)
            if not content:
                content = soup.find("section", **selector)
            if content:
                break

        # Try <article> or <main> tags
        if not content:
            content = soup.find("article") or soup.find("main")

        # Fallback: use body with noise already stripped
        if not content:
            content = soup.find("body")

        if not content:
            job["full_fetch"] = "no_content"
            print(f"DEBUG: [FETCH-FAIL] '{job_name}' - no content found on page")
            return None

        text = content.get_text(separator=" ", strip=True)

        # Sanity check: if extracted text is too short, it's probably junk
        if len(text) < 50:
            job["full_fetch"] = "too_short"
            print(f"DEBUG: [FETCH-FAIL] '{job_name}' - extracted text too short ({len(text)} chars)")
            return None

        job["full_fetch"] = "success"
        print(f"DEBUG: [FETCH-OK] '{job_name}' - extracted {len(text)} chars from {resp.url[:80]}")
        return text

    except requests.exceptions.Timeout:
        job["full_fetch"] = "timeout"
        print(f"DEBUG: [FETCH-FAIL] '{job_name}' - timeout after {timeout}s")
        return None
    except requests.exceptions.RequestException as e:
        job["full_fetch"] = "failed"
        error_msg = str(e)[:100]
        print(f"DEBUG: [FETCH-FAIL] '{job_name}' - {error_msg}")
        return None
    except Exception as e:
        job["full_fetch"] = "error"
        print(f"DEBUG: [FETCH-FAIL] '{job_name}' - unexpected error: {str(e)[:100]}")
        return None


def _classify_remote_status(job, use_full=False):
    """
    Classify a job's remote status using a score-based approach.
    Returns one of: 'remote', 'hybrid', 'onsite', 'unclear'.

    Priority order:
      1. Location field (structured data from API - most reliable)
      2. Anti-remote phrases in description (negation detection)
      3. Score-based analysis of title + description text

    Args:
        job: Job dict with title, location, description, and optionally full_description
        use_full: If True, also use job['full_description'] for scoring
    """
    loc_text = job.get("location", "").lower().strip()
    desc_text = job.get("description", "").lower()
    title_text = job.get("title", "").lower()
    job_name = job.get("title", "N/A")

    # If full description available and requested, combine with API description
    if use_full and job.get("full_description"):
        desc_text = desc_text + " " + job["full_description"].lower()

    # -----------------------------------------------------------------
    # Step 1: LOCATION FIELD check (highest priority - structured data)
    # -----------------------------------------------------------------
    loc_stripped = loc_text.strip().rstrip(".")
    # Check if location field starts with "remote" (catches "Remote", "Remote - US",
    # "Remote / Eastern Time Zone", "Remote, United States", etc.)
    if loc_stripped.startswith("remote") or loc_stripped in ("anywhere", "work from home"):
        print(f"DEBUG: [REMOTE-loc] '{job_name}' - location field = '{loc_text}'")
        return "remote"

    hybrid_from_location = False
    if "hybrid" in loc_text:
        hybrid_from_location = True

    # -----------------------------------------------------------------
    # Step 2: ANTI-REMOTE check (description only - negation detection)
    # -----------------------------------------------------------------
    anti_remote_phrases = [
        "not remote", "no remote", "non-remote", "not a remote",
        "on-site only", "onsite only", "in-office only", "in office only",
        "no telecommut", "no work from home",
        "this is not a remote position", "remote work is not available",
        "does not offer remote", "not eligible for remote",
        "no remote options", "remote options are not available",
        "not a work from home", "cannot be performed remotely",
        "must work on-site", "must work onsite", "required to be on-site",
        "required to be onsite", "not available for remote",
    ]
    for phrase in anti_remote_phrases:
        if phrase in desc_text:
            print(f"DEBUG: [ONSITE-neg] '{job_name}' - anti-remote phrase '{phrase}' found")
            return "onsite"

    # -----------------------------------------------------------------
    # Step 3: TITLE + DESCRIPTION scoring
    # -----------------------------------------------------------------
    remote_score = 0
    hybrid_score = 0
    onsite_score = 0

    # --- Title signals (reliable - curated by poster) ---
    if re.search(r"\bremote\b", title_text):
        remote_score += 1
    if re.search(r"\bhybrid\b", title_text):
        hybrid_score += 1

    # --- Description: Strong remote signals (+2 each) ---
    strong_remote = [
        r"fully\s+remote", r"100%?\s*remote", r"remote\s+position",
        r"remote\s+role", r"remote\s+opportunity", r"work\s+from\s+home",
        r"work\s+remotely", r"remote[\-\s]first", r"remote[\-\s]friendly",
        r"permanently\s+remote", r"all[\-\s]remote",
    ]
    for pattern in strong_remote:
        if re.search(pattern, desc_text):
            remote_score += 2

    # --- Description: Weak remote signals (+1 each) ---
    weak_remote = [
        r"\bremote\b",
        r"\btelecommut\w*",
        r"\btelework\w*",
        r"virtual\s+position",
    ]
    for pattern in weak_remote:
        if re.search(pattern, desc_text):
            remote_score += 1

    # --- Hybrid signals (exclude "hybrid cloud", etc.) ---
    hybrid_technical_context = re.search(
        r"hybrid\s+(cloud|infrastructure|environment|network|architecture|solution|system|model|approach|deployment|storage|setup)",
        desc_text
    )
    hybrid_patterns = [
        r"\bhybrid\b", r"partially\s+remote",
        r"days?\s+in\s+office", r"remote\s*/\s*onsite", r"onsite\s*/\s*remote",
        r"remote\s*/\s*on-site", r"on-site\s*/\s*remote",
        r"hybrid\s+remote", r"remote\s+hybrid",
        r"hybrid\s+work", r"hybrid\s+role", r"hybrid\s+position",
        r"hybrid\s+schedule",
    ]
    for pattern in hybrid_patterns:
        if re.search(pattern, desc_text):
            hybrid_score += 1
    # If "hybrid" only appears in technical context (hybrid cloud), discount it
    if hybrid_score == 1 and hybrid_technical_context and not re.search(
        r"hybrid\s+(work|role|position|schedule|remote|arrangement)", desc_text
    ):
        hybrid_score = 0

    # --- Description: Strong hybrid signals (+2 each) ---
    strong_hybrid = [
        r"\d+\s+days?\s+(a|per)\s+week\s+in",
        r"\d+\s+days?\s+(a|per)\s+week\s+on[\-\s]?site",
        r"work\s+arrangement[:\s]+hybrid",
        r"working\s+arrangement[:\s]+hybrid",
        r"hybrid\s+work\s+arrangement",
        r"hybrid\s+work\s+model",
        r"minimum\s+\d+\s+days?\s+(a|per)\s+week\s+in",
        r"at\s+least\s+\d+\s+days?\s+(a|per)\s+week\s+in",
        r"required\s+to\s+be\s+in\s+(the\s+)?office",
        r"in\s+(the\s+)?office\s+\d+\s+days?",
    ]
    for pattern in strong_hybrid:
        if re.search(pattern, desc_text):
            hybrid_score += 2
            break

    # --- Onsite signals (+1 each) ---
    onsite_patterns = [
        r"\bon[\-\s]?site\b", r"\bonsite\b", r"\bin[\-\s]?office\b",
        r"\bin\s+office\b", r"\bin[\-\s]?person\b", r"on\s+site\s+required",
        r"office[\-\s]based",
    ]
    for pattern in onsite_patterns:
        if re.search(pattern, desc_text):
            onsite_score += 1

    # -----------------------------------------------------------------
    # Step 4: Decision
    # -----------------------------------------------------------------
    if hybrid_from_location:
        print(f"DEBUG: [HYBRID-loc] '{job_name}' - location field contains 'hybrid'")
        return "hybrid"

    if hybrid_score >= 2:
        print(f"DEBUG: [HYBRID-desc] '{job_name}' - strong hybrid signal (score={hybrid_score})")
        return "hybrid"

    if remote_score >= 2 and remote_score > onsite_score and hybrid_score == 0:
        print(f"DEBUG: [REMOTE-desc] '{job_name}' - remote={remote_score}, onsite={onsite_score}")
        return "remote"

    if hybrid_score > 0:
        print(f"DEBUG: [HYBRID-desc] '{job_name}' - hybrid signals found (score={hybrid_score})")
        return "hybrid"

    if onsite_score > 0 and onsite_score >= remote_score:
        print(f"DEBUG: [ONSITE-desc] '{job_name}' - onsite={onsite_score}, remote={remote_score}")
        return "onsite"

    if remote_score == 1 and onsite_score == 0:
        print(f"DEBUG: [UNCLEAR] '{job_name}' - single weak remote signal, no counter")
        return "unclear"

    print(f"DEBUG: [UNCLEAR] '{job_name}' - remote={remote_score}, hybrid={hybrid_score}, onsite={onsite_score}")
    return "unclear"


_ZIP_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zip_coords_cache.json")

_zip_coords_memory = {}


def _resolve_zip_coords(zip_code):
    """
    Return (lat, lon) for a US ZIP code.
    Checks in-process cache, then disk cache (data/zip_coords_cache.json),
    then fetches from Zippopotam.us API. Falls back to (40.0, -75.0) only
    if the API is unreachable and no cached value exists.
    """
    global _zip_coords_memory

    if zip_code in _zip_coords_memory:
        return _zip_coords_memory[zip_code]

    disk_cache = {}
    if os.path.exists(_ZIP_CACHE_FILE):
        try:
            with open(_ZIP_CACHE_FILE, "r") as f:
                disk_cache = json.load(f)
        except Exception:
            pass

    if zip_code in disk_cache:
        coords = tuple(disk_cache[zip_code])
        _zip_coords_memory[zip_code] = coords
        return coords

    try:
        r = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5)
        r.raise_for_status()
        data = r.json()
        place = data["places"][0]
        coords = (float(place["latitude"]), float(place["longitude"]))
        print(f"DEBUG: ZIP {zip_code} resolved to {coords} via Zippopotam.us")
    except Exception as e:
        print(f"DEBUG: W ZIP lookup failed for '{zip_code}': {e} — using fallback (40.0, -75.0)")
        coords = (40.0, -75.0)

    disk_cache[zip_code] = list(coords)
    try:
        os.makedirs(os.path.dirname(_ZIP_CACHE_FILE), exist_ok=True)
        with open(_ZIP_CACHE_FILE, "w") as f:
            json.dump(disk_cache, f, indent=2)
    except Exception as e:
        print(f"DEBUG: W Could not write ZIP cache: {e}")

    _zip_coords_memory[zip_code] = coords
    return coords



_city_coords_memory = {}


def _resolve_city_coords(city, state):
    """
    Return (lat, lon) for a US city/state, or None if lookup fails.
    Cache key: city:<city_lower>:<state_lower> in shared zip_coords_cache.json.
    Uses Zippopotam.us: http://api.zippopotam.us/us/<STATE>/<City>
    """
    import urllib.parse
    cache_key = f"city:{city.lower()}:{state.lower()}"

    if cache_key in _city_coords_memory:
        return _city_coords_memory[cache_key]

    disk_cache = {}
    if os.path.exists(_ZIP_CACHE_FILE):
        try:
            with open(_ZIP_CACHE_FILE, "r") as f:
                disk_cache = json.load(f)
        except Exception:
            pass

    if cache_key in disk_cache:
        coords = tuple(disk_cache[cache_key])
        _city_coords_memory[cache_key] = coords
        return coords

    try:
        encoded_city = urllib.parse.quote(city)
        r = requests.get(
            f"http://api.zippopotam.us/us/{state.lower()}/{encoded_city}",
            timeout=5
        )
        r.raise_for_status()
        data = r.json()
        place = data["places"][0]
        coords = (float(place["latitude"]), float(place["longitude"]))
        print(f"DEBUG: City '{city}, {state}' resolved to {coords} via Zippopotam.us")
    except Exception as e:
        print(f"DEBUG: W City lookup failed for '{city}, {state}': {e}")
        return None

    disk_cache[cache_key] = list(coords)
    try:
        os.makedirs(os.path.dirname(_ZIP_CACHE_FILE), exist_ok=True)
        with open(_ZIP_CACHE_FILE, "w") as f:
            json.dump(disk_cache, f, indent=2)
    except Exception as e:
        print(f"DEBUG: W Could not write city cache: {e}")

    _city_coords_memory[cache_key] = coords
    return coords


_VALID_STATES = {
    "al","ak","az","ar","ca","co","ct","dc","de","fl","ga","hi","id","il","in","ia",
    "ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj","nm",
    "ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt","va","wa",
    "wv","wi","wy"
}
_SKIP_CITIES = {"remote", "anywhere", "virtual", "nationwide", "united states", "us", "work from home"}
_REMOTE_PAGE_RE = re.compile(
    r"\b(fully\s+remote|100%\s*remote|remote[\-\s]first|work\s+from\s+home|"
    r"remote\s+position|remote\s+role|remote\s+opportunity|permanently\s+remote)\b",
    re.IGNORECASE
)
_LABEL_LINE_RE = re.compile(
    r"(?:job\s+)?(?:work\s+)?(?:office\s+)?location\s*[:\-\u2013]\s*(.+)",
    re.IGNORECASE
)
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_CITY_ST_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:[ \-][A-Z][a-zA-Z]+){0,3}),\s*([A-Z]{2})\b"
)


def _extract_location_from_page_text(text):
    """
    Parse page text for a real job location.
    Returns one of:
      (city, Dallas, TX)  -- city/state found
      (zip, 75201, None)  -- ZIP found in labeled marker
      None               -- ambiguous, remote signaled, or nothing found
    """
    if not text:
        return None

    if _REMOTE_PAGE_RE.search(text):
        return None

    labeled_zips = []
    labeled_cities = []
    for line in text.splitlines():
        m = _LABEL_LINE_RE.match(line.strip())
        if not m:
            continue
        value = m.group(1).strip()

        if re.search(r"\bremote\b|\banywhere\b|\bvirtual\b", value, re.IGNORECASE):
            return None

        zm = _ZIP_RE.search(value)
        if zm:
            labeled_zips.append(zm.group(1))

        for cm in _CITY_ST_RE.finditer(value):
            city, state = cm.group(1), cm.group(2)
            if state.lower() in _VALID_STATES and city.lower() not in _SKIP_CITIES:
                labeled_cities.append((city, state.upper()))

    unique_zips = list(dict.fromkeys(labeled_zips))
    unique_cities = list(dict.fromkeys(labeled_cities))

    if unique_zips and len(unique_zips) == 1:
        print(f"DEBUG: [LOC-EXTRACT] Labeled ZIP found: {unique_zips[0]}")
        return ("zip", unique_zips[0], None)
    if unique_cities and len(unique_cities) == 1:
        print(f"DEBUG: [LOC-EXTRACT] Labeled city found: {unique_cities[0][0]}, {unique_cities[0][1]}")
        return ("city", unique_cities[0][0], unique_cities[0][1])
    if (unique_zips and len(unique_zips) > 1) or (unique_cities and len(unique_cities) > 1):
        return None

    snippet = text[:15000]
    counts = {}
    for cm in _CITY_ST_RE.finditer(snippet):
        city, state = cm.group(1), cm.group(2)
        if state.lower() not in _VALID_STATES:
            continue
        if city.lower() in _SKIP_CITIES:
            continue
        key = (city, state.upper())
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return None

    distinct_states = {s for (_, s) in counts}
    if len(distinct_states) > 2:
        return None

    sorted_pairs = sorted(counts.items(), key=lambda x: -x[1])
    top_pair, top_count = sorted_pairs[0]
    if len(sorted_pairs) == 1:
        print(f"DEBUG: [LOC-EXTRACT] Bare scan found: {top_pair[0]}, {top_pair[1]}")
        return ("city", top_pair[0], top_pair[1])
    second_count = sorted_pairs[1][1]
    if top_count >= 2 * second_count:
        print(f"DEBUG: [LOC-EXTRACT] Bare scan dominant: {top_pair[0]}, {top_pair[1]} ({top_count}x vs {second_count}x)")
        return ("city", top_pair[0], top_pair[1])

    return None


def _record_check(audit, key, label, status, evidence, **details):
    check = {"key": key, "label": label, "status": status, "evidence": evidence}
    check.update({k: v for k, v in details.items() if v is not None})
    audit["checks"].append(check)


def evaluate_simple_filters(job, cfg):
    """Apply basic filters and attach an exact, reportable decision trail."""
    title = job.get("title", "").lower()
    snippet = job.get("snippet", "").lower()
    description = job.get("description", "").lower()
    text = " ".join([title, snippet, description])
    job_name = job.get("title", "N/A")
    configured_keywords = [str(k) for k in cfg.get("keywords", []) if str(k).strip()]
    blocked_words = [str(b) for b in cfg.get("blocked_words", []) if str(b).strip()]
    matched_keywords = [k for k in configured_keywords if k.lower() in text]
    matched_blocked = [b for b in blocked_words if b.lower() in title]
    audit = {
        "checks": [],
        "search_keywords": list(dict.fromkeys(job.get("_search_keywords", []))),
        "search_modes": list(dict.fromkeys(job.get("_search_modes", []))),
        "sources": list(dict.fromkeys(job.get("_sources", [job.get("source", "Unknown")]))),
        "target_employers": list(dict.fromkeys(job.get("_target_employers", []))),
        "matched_keywords": matched_keywords,
        "stage_reached": "basic_filters",
        "outcome": "unknown",
    }
    job["_decision"] = audit

    if matched_blocked:
        _record_check(audit, "blocked_words", "Blocked title words", "fail",
                      "Title contains: " + ", ".join(matched_blocked),
                      matched=matched_blocked)
        audit["outcome"] = "blocked_word"
        print(f"DEBUG: X '{job_name}' filtered - title contains blocked term '{matched_blocked[0]}'")
        return False, audit
    _record_check(audit, "blocked_words", "Blocked title words", "pass",
                  "No blocked title words found")

    if configured_keywords and not matched_keywords:
        _record_check(audit, "keywords", "Active keywords", "fail",
                      "None of the active keywords appeared in the captured posting text")
        audit["outcome"] = "keyword"
        print(f"DEBUG: X '{job_name}' filtered - missing required keywords")
        return False, audit
    _record_check(audit, "keywords", "Active keywords", "pass",
                  "Matched: " + ", ".join(matched_keywords) if matched_keywords
                  else "No active keyword requirement", matched=matched_keywords)

    salary_min = cfg.get("salary_min", 0)
    salary = job.get("salary")
    if isinstance(salary, (int, float)):
        if salary < salary_min:
            _record_check(audit, "salary_min", "Minimum salary", "fail",
                          f"Listed salary {salary:,.0f} is below {salary_min:,.0f}",
                          value=salary, threshold=salary_min)
            audit["outcome"] = "salary"
            print(f"DEBUG: X '{job_name}' filtered - salary {salary} < {salary_min}")
            return False, audit
        _record_check(audit, "salary_min", "Minimum salary", "pass",
                      f"Listed salary {salary:,.0f} meets {salary_min:,.0f}",
                      value=salary, threshold=salary_min)
    else:
        _record_check(audit, "salary_min", "Minimum salary", "not_applicable",
                      "No numeric salary was published, so the job remains eligible",
                      threshold=salary_min)

    radius = float(cfg.get("radius_miles", 50))
    zip_code = cfg.get("zip_code", "18080")
    center_lat, center_lon = _resolve_zip_coords(zip_code)
    job["remote_status"] = _classify_remote_status(job)
    if job["remote_status"] == "remote":
        _record_check(audit, "location", "Work style and location", "pass",
                      "Classified remote; distance limit does not apply",
                      value="remote", threshold=radius)
        audit["stage_reached"] = "enrichment"
        audit["outcome"] = "passed_basic_filters"
        return True, audit

    lat = job.get("latitude")
    lon = job.get("longitude")
    if lat is not None and lon is not None:
        try:
            distance = _distance_miles(center_lat, center_lon, float(lat), float(lon))
            job["distance_miles"] = round(distance, 1)
            if distance <= radius:
                _record_check(audit, "location", "Work style and location", "pass",
                              f"{distance:.1f} miles from the search ZIP, within {radius:g}",
                              value=round(distance, 1), threshold=radius)
                audit["stage_reached"] = "enrichment"
                audit["outcome"] = "passed_basic_filters"
                print(f"DEBUG: OK '{job_name}' passes - {distance:.1f}mi within {radius}mi radius")
                return True, audit
            _record_check(audit, "location", "Work style and location", "fail",
                          f"{distance:.1f} miles from the search ZIP, outside {radius:g}",
                          value=round(distance, 1), threshold=radius)
            audit["outcome"] = "location"
            print(f"DEBUG: X '{job_name}' filtered - {distance:.1f}mi outside {radius}mi radius")
            return False, audit
        except Exception as exc:
            _record_check(audit, "location", "Work style and location", "fail",
                          f"Coordinates could not be evaluated: {exc}")
            audit["outcome"] = "location"
            print(f"DEBUG: W '{job_name}' invalid coordinates - rejecting ({exc})")
            return False, audit

    normalized_loc = job.get("location", "").lower().replace(',', ' ').replace('.', ' ')
    states = {
        "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia","ks","ky",
        "la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj","nm","ny","nc","nd",
        "oh","ok","or","ri","sc","sd","tn","tx","ut","vt","va","wa","wv","wi","wy"
    }
    for state in states:
        if re.search(rf"\b{state}\b", normalized_loc) and state != "pa":
            job["remote_status"] = "unclear"
            _record_check(audit, "location", "Work style and location", "fail",
                          f"Posting names {state.upper()} and has no usable coordinates",
                          value=state.upper(), threshold=radius)
            audit["outcome"] = "location"
            print(f"DEBUG: X '{job_name}' filtered - state '{state.upper()}' not PA (no coords)")
            return False, audit

    job["remote_status"] = job.get("remote_status", "unclear")
    _record_check(audit, "location", "Work style and location", "fail",
                  "Location was ambiguous and had no usable coordinates",
                  value=job.get("location", ""), threshold=radius)
    audit["outcome"] = "location"
    print(f"DEBUG: W '{job_name}' unclear location - marking as ambiguous")
    return False, audit


def passes_simple_filters(job, cfg):
    """Compatibility wrapper for callers that only need the boolean result."""
    passed, _ = evaluate_simple_filters(job, cfg)
    return passed
