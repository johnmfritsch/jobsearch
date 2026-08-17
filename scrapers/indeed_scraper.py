# indeed_scraper_improved.py
import requests
from bs4 import BeautifulSoup
import time
import random

def fetch_jobs_indeed(cfg, query, source_counts, user=None, test_mode=False):
    """
    Improved Indeed scraper with better anti-bot evasion.
    Warning: Still may get blocked. Use at your own risk.
    """
    # Import write_status if user provided
    write_status = None
    if user:
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..'))
            from main import write_status as ws
            write_status = ws
        except ImportError:
            pass

    msg = "Indeed: starting fetch"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)

    jobs = []
    base_url = "https://www.indeed.com/jobs"
    results_per_page = int(cfg.get("indeed_results_per_page", 20))
    max_pages = int(cfg.get("indeed_max_pages", 1))
    location = cfg.get("location", "")
    remote = cfg.get("remote", False)

    # Create a session to maintain cookies
    session = requests.Session()

    # More realistic headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }

    for page in range(max_pages):
        params = {
            "q": query,
            "l": location,
            "limit": results_per_page
        }

        if page > 0:
            params["start"] = page * results_per_page

        if remote and "remote" not in query.lower():
            params["q"] += " remote"

        try:
            # Random delay between requests (2-5 seconds)
            if page > 0:
                time.sleep(random.uniform(2, 5))

            msg = f"Indeed: fetching page {page + 1}/{max_pages}"
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)

            r = session.get(
                base_url,
                params=params,
                timeout=30,
                headers=headers
            )

            # Check for captcha or blocking
            if "captcha" in r.text.lower() or r.status_code == 403:
                print(f"DEBUG: Indeed blocked request on page {page+1}")
                print("DEBUG: Detected captcha or 403. Try using a proxy or reducing request frequency.")
                break

            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Indeed uses multiple possible selectors, try them all
            cards = soup.select("a.tapItem")
            if not cards:
                cards = soup.select("div.job_seen_beacon")  # Alternative selector
            if not cards:
                cards = soup.select("td.resultContent")  # Another alternative

            if not cards:
                print(f"DEBUG: Page {page+1} returned no results — stopping pagination.")
                break

            for c in cards:
                # Try multiple selector patterns for title
                title_el = (c.select_one("h2.jobTitle span") or
                           c.select_one("h2.jobTitle") or
                           c.select_one("a[data-jk]"))

                company_el = (c.select_one("span.companyName") or
                             c.select_one("span[data-testid='company-name']"))

                snippet_el = (c.select_one("div.job-snippet") or
                             c.select_one("div.summary"))

                location_el = c.select_one("div.companyLocation")

                # Get link
                link = ""
                if c.name == "a":
                    link = c.get("href", "")
                else:
                    link_el = c.select_one("a[data-jk]")
                    if link_el:
                        link = link_el.get("href", "")

                if not link.startswith("http"):
                    link = f"https://www.indeed.com{link}"

                job_title = title_el.get_text(strip=True) if title_el else ""
                if not job_title:
                    continue  # Skip if no title found

                jobs.append({
                    "title": job_title,
                    "company": company_el.get_text(strip=True) if company_el else "",
                    "url": link,
                    "salary": "",
                    "snippet": snippet_el.get_text(' ', strip=True) if snippet_el else "",
                    "description": snippet_el.get_text(' ', strip=True) if snippet_el else "",
                    "location": location_el.get_text(strip=True) if location_el else location,
                    "source": "Indeed"
                })

            msg = f"Indeed: page {page+1} -> {len(cards)} cards (total {len(jobs)} jobs)"
            print(f"DEBUG: {msg}")
            if write_status:
                write_status(user, test_mode, "running", msg)

            if len(cards) < results_per_page:
                print("DEBUG: Fewer results returned than expected — stopping pagination.")
                break

        except requests.exceptions.HTTPError as http_err:
            print(f"DEBUG: Indeed HTTP error on page {page+1}: {http_err}")
            if r.status_code == 403:
                print("DEBUG: 403 Forbidden — Indeed detected scraping.")
                print("DEBUG: Consider using rotating proxies or switching to Adzuna API")
            break
        except Exception as e:
            print(f"DEBUG: Indeed fetch failed on page {page+1}: {e}")
            break

    total = len(jobs)
    source_counts["Indeed"] = total
    msg = f"Indeed: completed - {total} jobs total"
    print(f"DEBUG: {msg}")
    if write_status:
        write_status(user, test_mode, "running", msg)
    return jobs
