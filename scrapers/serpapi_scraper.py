import os
if "/DEV" in os.path.dirname(os.path.abspath(__file__)) or os.path.dirname(os.path.abspath(__file__)).endswith("/DEV"):
    environment = "DEV"
else:
    environment = "PROD"
os.environ["JOB_SEARCH_ENV"] = environment
# serpapi_scraper.py
import requests
import time


def extract_best_url(item):
    """
    Determine the best available URL for a job posting from SerpAPI.
    Checks apply_link, apply_options, related_links, and job_highlights_url.
    """
    apply_link = item.get("apply_link")

    if not apply_link and "apply_options" in item:
        for opt in item["apply_options"]:
            if isinstance(opt, dict) and "link" in opt and opt["link"].startswith("http"):
                apply_link = opt["link"]
                break

    if not apply_link and "related_links" in item:
        for linkset in item["related_links"]:
            if isinstance(linkset, dict) and "link" in linkset and linkset["link"].startswith("http"):
                apply_link = linkset["link"]
                break

    if not apply_link and "job_highlights_url" in item:
        link = item["job_highlights_url"]
        if isinstance(link, str) and link.startswith("http"):
            apply_link = link

    return apply_link or ""


def fetch_jobs_serpapi(cfg, base_query, source_counts, user=None, test_mode=False):
    """
    Fetch job listings from SerpAPI's Google Jobs engine.
    Fully driven by config.json (serpapi_max_pages, serpapi_local_max_pages, etc.)
    Note: serpapi_results_per_page has no effect on Google Jobs (hard limit: 10/page).
    """
    # Import write_status if user provided
    write_status = None
    if user:
        try:
            from main import write_status as ws
            write_status = ws
        except ImportError:
            pass

    # New format: credentials.serpapi[] — entry named "Active" is used
    # Fallback: legacy flat serpapi_key
    _creds = cfg.get("credentials", {}).get("serpapi", [])
    _active = [c for c in _creds if c.get("name", "").lower() == "active"]
    if _active:
        api_key = _active[0].get("key", "")
    else:
        api_key = cfg.get("serpapi_key", "")  # legacy fallback
    if not api_key:
        print("DEBUG: Missing SerpAPI key — skipping SerpAPI fetch.")
        return []

    base_url = "https://serpapi.com/search.json"
    engine = "google_jobs"
    keywords = cfg.get("keywords", [])
    max_pages = int(cfg.get("serpapi_max_pages", 5))
    location = cfg.get("location", "")
    remote = cfg.get("remote", False)

    all_jobs = []
    msg = f"SerpAPI: starting fetch ({len(keywords)} keywords x {max_pages} pages)"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)

    for kw_idx, kw in enumerate(keywords, 1):
        msg = f"SerpAPI: keyword {kw_idx}/{len(keywords)} '{kw}'"
        print(f"DEBUG: {msg}")
        if write_status:
            write_status(user, test_mode, "running", msg)

        for page in range(1, max_pages + 1):
            try:
                params = {
                    "engine": engine,
                    "q": kw,
                    "hl": "en",
                    "gl": "us",
                    "api_key": api_key,
                }

                if location and not remote:
                    params["location"] = location
                else:
                    print(f"DEBUG: Omitting location parameter for REMOTE search (keyword='{kw}')")

                r = requests.get(base_url, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()

                if "error" in data:
                    print(f"DEBUG: SerpAPI error for '{kw}': {data['error']}")
                    continue

                results = data.get("jobs_results", [])
                msg = f"SerpAPI: '{kw}' page {page}/{max_pages} -> {len(results)} results"
                print(f"DEBUG: {msg}")
                if write_status:
                    write_status(user, test_mode, "running", msg)

                for item in results:
                    final_url = extract_best_url(item)
                    all_jobs.append({
                        "title": item.get("title", "").strip(),
                        "company": item.get("company_name", "").strip(),
                        "url": final_url,
                        "salary": item.get("detected_extensions", {}).get("salary", ""),
                        "snippet": (item.get("description", "") or "")[:400].replace("\n", " ").strip(),
                        "description": item.get("description", "") or "",
                        "location": item.get("location", ""),
                        "latitude": item.get("latitude"),
                        "longitude": item.get("longitude"),
                        "source": "SerpAPI",
                    })

                time.sleep(1.2)

            except requests.HTTPError as e:
                print(f"DEBUG: SerpAPI fetch failed for '{kw}' page {page}: {e}")
            except Exception as e:
                print(f"DEBUG: Unexpected error for '{kw}' page {page}: {e}")

    total = len(all_jobs)
    msg = f"SerpAPI: completed - {total} jobs total"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)

    source_counts["SerpAPI"] = source_counts.get("SerpAPI", 0) + total
    return all_jobs
