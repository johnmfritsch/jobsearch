# JobSearch 🔍

A self-hosted, multi-user job search platform. It pulls listings from job APIs and directly from employer career sites, filters and scores them against each user's own criteria and résumé, and tracks every application from "saved" to "offer" — with an optional, supervised assistant that prepares applications and tailored documents without ever submitting on its own.

Built to scratch my own itch during an active job search, then opened up to a handful of other people running their own searches on the same instance. In August 2026 it was rebuilt from a PHP-proxy + generated-dashboard design into a single authenticated Flask application; the earlier version is preserved in this repo's git history.

## Features

- **Multi-user** — accounts with server-side sessions, login throttling, and email-based activation; every API call resolves the user from the session, never from a request parameter
- **Saved roles** — named search setups (keywords, blocked words, boost terms, location/remote, pay, scoring), each with its own versioned résumé and cover letter, switched from folder-style tabs
- **Multi-source search** — SerpAPI, Adzuna, and Indeed behind a common interface, with a live SerpAPI credit balance and per-run cost estimate
- **Priority employers** — specific companies checked on every run straight from their career sites (Workday, SAP SuccessFactors, UKG, Jibe/iCIMS, or any page publishing `schema.org/JobPosting` data), at no API cost; anything else falls back to a company-scoped search
- **Résumé-match scoring** — semantic (sentence-transformers), TF-IDF, or hybrid scoring against the active role's résumé
- **Search insights** — a full decision trail for every candidate job: which filter, keyword, or score threshold let it through or ruled it out
- **Application tracker** — stages from Saved through Applied, Interviewing, Offer, Rejected, and Closed; timestamped notes log; jobs applied to elsewhere can be added by pasting the posting URL (Greenhouse, Lever, and Ashby read via their public APIs, then structured data, then page metadata)
- **Supervised application assistant** — prepares an application attempt with the right documents; optional AI tailoring (OpenAI, Anthropic Claude, or xAI Grok) is opt-in per attempt, constrained to facts in the source résumé, and every generated document needs approval. A separate, isolated Playwright worker can fill known ATS fields, but form preparation and submission are both behind disabled-by-default flags and a single-use final confirmation
- **Dev and production environments** with a scripted, health-gated deploy

## Architecture

```
Browser
  └── Gunicorn (HTTPS, dedicated port)
        └── Flask  (PrefixMiddleware → auth / api / portal blueprints)
              ├── SQLite            accounts, roles, runs, results, tracker, attempts
              ├── main.py           detached search pipeline (survives a server restart)
              │     ├── scrapers/   SerpAPI · Adzuna · Indeed · employer career-site adapters
              │     ├── filters.py  keyword, blocked-word, salary, location rules
              │     └── matcher.py  résumé scoring
              └── application_worker/   isolated Playwright container (signed, scoped requests)
```

The search pipeline runs as a detached process so a server restart doesn't kill an in-flight run; the portal polls its status, and a run whose process has died is marked failed with a log excerpt instead of spinning forever.

Employer career-site fetching goes through `safe_fetch.py`: HTTPS-only, DNS-pinned, same-host, and bounded in pages and time, so a user-supplied careers URL can't be used to reach internal addresses. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

## Project Structure

```
app.py                  # Flask factory, prefix middleware, session cookies
auth.py                 # Registration, login, activation, throttling
api.py                  # Authenticated JSON API
portal.py               # Dashboard route
main.py                 # Search pipeline orchestration
filters.py, matcher.py  # Filtering and résumé scoring
scrapers/               # Job APIs + employer career-site adapters (adapter_registry.py)
safe_fetch.py           # Hardened outbound fetch for user-supplied URLs
manual_jobs.py          # Add-a-job-by-URL extraction
auto_apply.py           # Application attempts, state machine, confirmation tokens
document_generator.py   # Optional AI résumé / cover-letter tailoring
role_documents.py       # Versioned per-role résumé and cover-letter files
profile_store.py        # Persistence for config, résumé, runs, results, tracker
database.py, migrate.py # SQLite access and numbered migrations
migrations/             # Schema migrations
application_worker/     # Playwright application worker (Docker)
playwright_service/     # Headless-browser service that resolves JS-redirecting job links
templates/, static/     # Portal UI
scripts/                # Start/stop/restart, changelog stamping, backup cleanup
tests/                  # Unit tests and adapter fixtures
```

## Running from Source

```bash
pip install flask gunicorn itsdangerous requests beautifulsoup4 numpy scikit-learn sentence-transformers
python migrate.py
python scripts/start_jobsearch.py dev
```

Provider credentials (SerpAPI, Adzuna, and any AI provider keys) are entered per account in the app and stored masked in the database — none ship with the repo. `default_config.json` is the empty template new accounts start from.

The database, uploaded résumés, logs, and backups are runtime data and are not committed — see `.gitignore`.

LAN addresses in this repo are masked as `192.168.x.x`.

_Source snapshot: JobSearch v0.2.7 (2026-09-27)._
