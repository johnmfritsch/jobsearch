
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
from typing import Optional

def load_resume_text(resume_path: str) -> str:
    # Expecting plain text file for highest reliability.
    with open(resume_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def enrich_with_keywords(text: str, extra_keywords: Optional[list] = None) -> str:
    # Optionally append keywords to improve matching signal (keeps it simple/transparent).
    if extra_keywords:
        return text + "\n" + " ".join(extra_keywords)
    return text

