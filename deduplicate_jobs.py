
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
    seen = {}
    unique_jobs = []

    for job in jobs:
        title = normalize_text(job.get("title", ""))
        company = normalize_text(job.get("company", ""))
        key = f"{title}-{company}"

        job.setdefault("_search_keywords", [])
        job.setdefault("_search_modes", [])
        job.setdefault("_sources", [job.get("source", "Unknown")])
        job.setdefault("_target_employers", [])

        if key not in seen:
            seen[key] = job
            unique_jobs.append(job)
        else:
            # Preserve every query/source that surfaced the posting so the
            # decision report can explain discovery even after deduplication.
            kept = seen[key]
            # An employer's own posting has canonical details and should win
            # over an earlier aggregator copy, while preserving all evidence.
            if job.get("_employer_direct") and not kept.get("_employer_direct"):
                combined = {
                    field: list(dict.fromkeys(list(kept.get(field, [])) +
                                               list(job.get(field, []))))
                    for field in ("_search_keywords", "_search_modes", "_sources",
                                  "_target_employers")
                }
                kept.clear()
                kept.update(job)
                kept.update(combined)
            for field in ("_search_keywords", "_search_modes", "_sources",
                          "_target_employers"):
                kept[field] = list(dict.fromkeys(
                    list(kept.get(field, [])) + list(job.get(field, []))))

    print(f"DEBUG: Deduplication reduced {len(jobs)} → {len(unique_jobs)} jobs")
    return unique_jobs
