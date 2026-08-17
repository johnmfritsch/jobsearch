# JobSearch 🔍

A self-hosted, multi-user job search aggregator. It pulls listings from several job APIs on a schedule, filters them against each user's own keyword and block-word rules, deduplicates across sources, and publishes a personal dashboard for tracking applications.

Built to scratch my own itch during an active job search, then opened up to a handful of other people running their own searches on the same instance.

## Features

- **Multi-user** — each user has their own keyword rules, credentials, dashboard, and saved jobs
- **Multi-source aggregation** — SerpAPI, Adzuna, and Indeed scrapers behind a common interface
- **Keyword filtering** — per-user include and block lists, applied before anything reaches the dashboard
- **Deduplication** — the same posting syndicated across sources collapses to one entry
- **Resume matching** — parses a resume and scores listings against it
- **Application tracking** — per-user status (interested, applied, dismissed)
- **Authentication** — SQLite-backed user store with claim codes for onboarding
- **Dev and production environments** with a scripted deploy and pre/post-deploy checks

## Architecture

```
Browser
  └── Nginx → Apache → /JobSearch/<user>/index.html
                     → api.php  (PHP proxy)
                          └── Python API  (prod / dev instances)
                                   ├── scrapers/   SerpAPI · Adzuna · Indeed
                                   ├── matcher.py  resume scoring
                                   ├── filters.py  keyword + block rules
                                   └── SQLite      users, jobs, status
```

The PHP layer is a thin proxy — it handles session auth and routes to either the dev or production Python API based on a query parameter, so both environments serve from the same frontend without duplicating it.

A separate containerized Playwright service (`playwright_service/`) handles sources that require a real browser, isolated from the main API process so a hung page load can't take the API down with it.

Each user's dashboard is generated as a static page with their configuration baked in, rather than fetched at runtime — the dashboards stay fast and keep working even if the API is mid-restart.

## Project Structure

```
main.py                  # Search orchestration — fetch, filter, dedupe, publish
api_handler.py           # HTTP API surface
database.py              # SQLite access layer
auth_store.py            # User accounts and sessions
profile_store.py         # Per-user profile and preferences
matcher.py               # Resume-to-listing scoring
filters.py               # Keyword and block-word rules
resume_parser.py         # Resume text extraction
deduplicate_jobs.py      # Cross-source deduplication
html_output.py           # Dashboard generation
scrapers/                # One module per job source
playwright_service/      # Containerized browser scraping
scripts/                 # Deploy checks, changelog stamping, backup cleanup
web/                     # PHP proxy, auth page, shared CSS/JS
```

## Running from Source

```bash
pip install -r requirements.txt
cp default_config.json configs/<user>/config.json
./start_api.sh
```

You'll need your own API credentials. `default_config.json` ships with empty
`credentials.serpapi` and `credentials.adzuna` arrays — fill those in per user.

**Nothing under `configs/` is committed** — those files hold live API keys. Neither are user resumes, the database, or generated dashboards. See `.gitignore`.

LAN addresses in this repo are masked as `192.168.x.x`.
