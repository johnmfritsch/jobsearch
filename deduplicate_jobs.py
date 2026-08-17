
import os
if "/DEV" in os.path.dirname(os.path.abspath(__file__)) or os.path.dirname(os.path.abspath(__file__)).endswith("/DEV"):
    environment = "DEV"
else:
    environment = "PROD"
os.environ["JOB_SEARCH_ENV"] = environment
import re

def normalize_text(text):
    """Normalize text for fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

def deduplicate_jobs(jobs):
    """
    Remove duplicates across all job sources (e.g., SerpAPI + Adzuna).
    Two jobs are considered duplicates if they share similar title+company.
    """
    seen = set()
    unique_jobs = []

    for job in jobs:
        title = normalize_text(job.get("title", ""))
        company = normalize_text(job.get("company", ""))
        key = f"{title}-{company}"

        if key not in seen:
            seen.add(key)
            unique_jobs.append(job)
        else:
            # You can uncomment this for debugging
            # print(f"DEBUG: Skipped duplicate: {job.get('title')} @ {job.get('company')}")
            pass

    print(f"DEBUG: Deduplication reduced {len(jobs)} → {len(unique_jobs)} jobs")
    return unique_jobs

