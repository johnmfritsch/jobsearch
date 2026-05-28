# JobSearch 🔍

A self-hosted, multi-user job search aggregator with per-user keyword filters, blocked-word rules, and personal dashboards for tracking job applications. Built to scratch my own itch during an active job search.

## Features

- Multi-user support (each user has their own dashboard and config)
- Keyword-based job filtering (include and block lists per user)
- Job status tracking per user (applied, interested, dismissed)
- PHP frontend proxies to a Python API backend
- Dev and production environments
- Access logging per user

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | HTML / CSS / JavaScript |
| API Proxy | PHP |
| Backend API | Python |
| Hosting | Self-hosted on ASUSTOR NAS |
| Web Server | Apache (via Nginx reverse proxy) |
| SSL | Let's Encrypt (auto-renewed) |

## Architecture

```
Browser
  └── Nginx (443) → Apache (7779) → /JobSearch/<user>/index.html
                                  → api.php (PHP proxy)
                                        └── Python API (port 8765 PROD / 8766 DEV)
```

Each user has their own `index.html` with personalized keyword and block-word configuration baked in. The PHP proxy routes to dev (8766) or prod (8765) based on the `env` query parameter.

## User Environments

```
/JobSearch/John/     → Production dashboard
/JobSearch/DEV/John/ → Development dashboard
```

## Project Structure

```
John/index.html         # John's production dashboard
DEV/John/index.html     # John's dev dashboard
static/                 # Shared CSS/JS assets
api.php                 # PHP → Python API proxy
```
