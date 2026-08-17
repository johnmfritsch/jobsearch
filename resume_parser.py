
import os
if "/DEV" in os.path.dirname(os.path.abspath(__file__)) or os.path.dirname(os.path.abspath(__file__)).endswith("/DEV"):
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

