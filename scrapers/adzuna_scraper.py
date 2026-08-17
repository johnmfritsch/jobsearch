# adzuna_scraper.py
import requests
import time


def fetch_single_adzuna_page(app_id, app_key, country, kw, page, results_per_page, location, remote):
    """Fetch a single page of Adzuna results."""
    base_url = f"https://api.adzuna.com/v1/api/jobs/{country.lower()}/search/{page}"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": kw,
        "results_per_page": results_per_page,
        "content-type": "application/json",
    }

    if location and not remote and "remote" not in location.lower():
        params["where"] = location
    else:
        params["where"] = "United States"

    try:
        r = requests.get(base_url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        return results
    except Exception as e:
        print(f"DEBUG: Adzuna fetch failed for keyword '{kw}' page {page}: {e}")
        return None


def fetch_jobs_adzuna(cfg, base_query, source_counts, user=None, test_mode=False):
    """
    Fetch jobs from Adzuna API.
    Includes latitude/longitude for downstream distance filtering.
    Fully driven by config.json (adzuna_max_pages, adzuna_results_per_page, etc.)
    """
    # Import write_status if user provided
    write_status = None
    if user:
        try:
            from main import write_status as ws
            write_status = ws
        except ImportError:
            pass

    # New format: credentials.adzuna[] — entry named "Active" is used
    # Fallback: legacy flat adzuna_credentials
    _creds = cfg.get("credentials", {}).get("adzuna", [])
    _active = [c for c in _creds if c.get("name", "").lower() == "active"]
    if _active:
        app_id = _active[0].get("app_id", "")
        app_key = _active[0].get("app_key", "")
    else:
        legacy = cfg.get("adzuna_credentials", {})
        app_id = legacy.get("app_id", "")
        app_key = legacy.get("app_key", "")
    if not app_id or not app_key:
        print("DEBUG: Missing Adzuna credentials.")
        source_counts["Adzuna"] = 0
        return []

    country = cfg.get("country", "us")
    pages = int(cfg.get("adzuna_max_pages", 1))
    results_per_page = int(cfg.get("adzuna_results_per_page", 20))
    results_per_page = min(results_per_page, 50)  # Adzuna API max is 50

    location = cfg.get("location", "")
    remote = cfg.get("remote", False)

    keywords = [k.strip() for k in base_query.split(" OR ") if k.strip()]
    jobs = []

    msg = f"Adzuna: starting fetch ({len(keywords)} keywords x {pages} pages)"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)

    for kw_idx, kw in enumerate(keywords, 1):
        msg = f"Adzuna: keyword {kw_idx}/{len(keywords)} '{kw}'"
        print(f"DEBUG: {msg}")
        if write_status:
            write_status(user, test_mode, "running", msg)

        for page in range(1, pages + 1):
            base_url = f"https://api.adzuna.com/v1/api/jobs/{country.lower()}/search/{page}"
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what": kw,
                "results_per_page": results_per_page,
                "content-type": "application/json",
            }

            if location and not remote and "remote" not in location.lower():
                params["where"] = location
            else:
                params["where"] = "United States"

            try:
                r = requests.get(base_url, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])
                msg = f"Adzuna: '{kw}' page {page}/{pages} -> {len(results)} results"
                print(f"DEBUG: {msg}")
                if write_status:
                    write_status(user, test_mode, "running", msg)

                for item in results:
                    jobs.append({
                        "title": item.get("title", "").strip(),
                        "company": (item.get("company") or {}).get("display_name", "").strip(),
                        "url": item.get("redirect_url", ""),
                        "salary": item.get("salary_min") or item.get("salary_max"),
                        "snippet": (item.get("description", "") or "")[:400].replace("\n", " ").strip(),
                        "description": item.get("description", "") or "",
                        "location": (item.get("location") or {}).get("display_name", "").strip(),
                        "latitude": item.get("latitude"),
                        "longitude": item.get("longitude"),
                        "source": "Adzuna",
                    })

                if not results:
                    break

                time.sleep(1.0)

            except Exception as e:
                print(f"DEBUG: Adzuna fetch failed for keyword '{kw}' page {page}: {e}")
                break

    total = len(jobs)
    msg = f"Adzuna: completed - {total} jobs total"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)
    source_counts["Adzuna"] = source_counts.get("Adzuna", 0) + total
    return jobs
