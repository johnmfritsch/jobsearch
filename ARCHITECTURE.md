# JobSearch — Technical Architecture

**Last updated:** 2026-09-27  
**Canonical code:** NAS-hosted Flask applications

## System Overview

JobSearch is an authenticated, multi-user Flask/Gunicorn application. It stores accounts, configuration, resumes, search runs/results, saved views, credentials, application-tracker records, and supervised application-attempt history in per-environment SQLite databases. Role-document files live in their respective environment's protected data directory. The former Apache/PHP proxy and generated per-user HTML pages are legacy only and are no longer the active product path.

```text
Browser
  → Gunicorn HTTPS (:7788 DEV / :7787 PROD)
  → Flask PrefixMiddleware (/JobSearch_dev or /JobSearch)
  → auth/api/portal blueprints
  → SQLite + detached search pipeline
```

## Environments and URLs

| Environment | Code | Database | External URL |
|---|---|---|---|
| DEV | `/volume1/Web/JobSearch_dev` | `data/jobsearch_dev.db` | `https://fritsch-nas.myasustor.com:7788/JobSearch_dev` |
| PROD | `/volume1/Web/JobSearch` | `data/jobsearch.db` | `https://fritsch-nas.myasustor.com:7787/JobSearch` |

Both environments use the NAS certificate directly in Gunicorn. `config.py` selects the database, port, and URL prefix from `JOBSEARCH_ENV`; the launcher sets the environment before starting Gunicorn.

## Application Structure

| File or directory | Responsibility |
|---|---|
| `app.py` | Flask factory, prefix middleware, secure session-cookie configuration |
| `auth.py` | Registration, login/logout, reset-password activation, session revocation and login throttling |
| `api.py` | Authenticated JSON API; identity always comes from the server-side session |
| `auto_apply.py` | User-scoped application attempts, state transitions, event history and single-use confirmation tokens |
| `document_generator.py` | Optional truth-constrained document-generation providers, Structured Output validation and artifact rendering |
| `role_documents.py` | Versioned ATS text, PDF role documents and authenticated downloads |
| `portal.py` | Authenticated dashboard route |
| `profile_store.py` | SQLite persistence for configuration, resume, runs, results, tracker and saved views |
| `database.py` | Environment-aware connections with foreign keys enabled |
| `migrations/` | Numbered schema migrations |
| `templates/portal.html` | Dashboard, onboarding, results, tracker and search-setup screens |
| `static/portal.js` | Portal interaction, filtering/sorting, tracker, onboarding and run polling |
| `main.py` | Detached search pipeline orchestration |
| `filters.py`, `matcher.py`, `scrapers/` | Filtering, scoring and job-source integrations |
| `application_worker/` | Isolated Playwright worker for future, feature-flagged supported-ATS preparation and submission |
| `scripts/start_jobsearch.py` | Shared DEV/PROD Gunicorn launcher |
| `scripts/restart_jobsearch_{dev,prod}.sh` | PID-based restart plus readiness check |
| `deploy_to_prod.sh` | Interactive, user-run DEV→PROD release workflow |

## Authentication and User Isolation

- Passwords use Werkzeug hashing; minimum length is eight characters and registration asks for confirmation.
- Sessions are represented in the SQLite `sessions` table so logout and password changes can revoke them server-side.
- DEV and PROD use distinct session-cookie names and URL-prefix-scoped cookie paths, so both environments can remain logged in on the shared hostname even though browser cookies do not distinguish ports.
- Cookies are Secure, HttpOnly and SameSite=Lax with a 30-day lifetime.
- Login attempts are rate-limited and locked independently per account.
- Every authenticated API request resolves the profile from the session. A supplied `user` value may only match the session profile; it never selects another user.
- Imported users activate their account through Forgot password, proving control of the stored email address. No owner-generated temporary password or claim code is required.
- Scraper secrets are masked on reads and never rendered in portal summaries. Blank replacement fields preserve existing secrets.

## Portal and Onboarding

The portal dashboard links to separate same-tab workspaces with top and bottom Back controls. Workspace titles are centered and the top Back action stays in the upper-left. Search setup opened from Run a new search returns to that workspace; direct dashboard visits return to the dashboard.

- **Saved jobs & applications** — All and named-role folder tabs above expandable job cards, notes, dates and stages. All is selected whenever the workspace opens from the dashboard.
- **Search results** — sortable result table, filters, saved views, details and triage actions.
- **Run a new search** — no-credit fixture test, live search and cancellation.
- **Search setup** — named-role folder tabs above grouped criteria/resume settings and masked account-level provider credentials; there is no All role tab.
- **Search insights** — per-run job decision trails and search-criteria performance reports with live filters.
- **Application assistant** — supervised attempt status, document selection/review and manual-handoff progress.

New accounts receive a guided onboarding wizard. It explains each requested value, gathers role/work-style/location/pay/resume preferences one step at a time, explains optional Adzuna and SerpAPI providers, links to their signup/key pages, and can be skipped to the dashboard. A resume is required before running a search because match scoring depends on it.

## Search Results

Results are stored in SQLite and rendered by the portal rather than generated as user-specific HTML files. The default sort is descending score. Every visible column can sort ascending or descending, with an indicator on the active column. Work style sorts alphabetically by its displayed label.

Filters cover work style, minimum score, minimum salary, source, application state and posting age. Named filter combinations are stored as saved views. Expanded details include job metadata, description, match evidence, job/application links and Save, Applied, Not interested actions. Triaged rows are subdued so untouched results stand out. Not interested is a durable ignore state: matching stable job keys are excluded from future runs before filtering and scoring.

## Application Tracker

Tracker records reuse the search result's stable `job_key`; they are not independent copies of a job. Metadata is stored at the record's top level so titles and links survive future searches.

Stages and colors:

| Stage | Meaning | Color |
|---|---|---|
| Saved for review | Interesting, not yet applied | Blue |
| Applied | Application submitted | Green |
| Interviewing | Interview process underway | Purple |
| Offer | Offer received | Amber |
| Rejected | Employer declined an application; retained as history | Red / rose |
| Closed | Other ended or abandoned opportunity, such as withdrawn, filled, or no longer pursued; retained as history | Gray |

Moving a record beyond Saved, including to Rejected, automatically fills `applied_date` using the local calendar date if it is blank. Existing Closed records remain Closed. Tracker filters mirror the result-screen dimensions: work style, score, salary, source and posting age, plus stage. The role tab filter combines with those controls; All also retains N/A, Default and deleted-role records. Role tabs match the saved `search_role` snapshot by name, case-insensitively. **Remove from tracker** deletes the tracker record and notes, then offers to add the job to the ignore list. Choosing removal only allows the job to appear again; choosing ignore stores a clean Not interested record without tracker notes or dates. Neither Closed nor Rejected deletes a record.

**Notes (v0.2.3, 2026-08-19).** A record's notes are an append-only, timestamped log (`record['notes_log']`, a list of `{text, at}`), not a single editable field. The detail panel's **Save note** button posts to `POST /api/application_tracker/note` (separate from the general `application_tracker` save), which appends an entry and returns the updated record; entries render newest-first, up to 20,000 characters each. The legacy single-string `notes` field is still read for older records with no `notes_log` yet — including the one-time note captured by **Add a job manually** — and is shown as that record's oldest entry; `application_tracker` still accepts a `notes` overwrite for backward compatibility but the portal no longer sends it.

**Formatted paste (v0.2.4, 2026-08-19).** Both notes textareas (the saved-job Add a note field and Add a job manually's Notes field) intercept `paste` and convert `text/html` clipboard content to plain text themselves (`htmlClipboardToPlainText`/`wireFormattedPaste` in `portal.js`) — `<li>` becomes a `•`/numbered line, block elements become line breaks — before inserting it, because a plain `<textarea>` can only hold text and the browser's own default HTML→text paste conversion drops list markers and often line breaks too. Falls through to the browser's normal paste when the clipboard carries no `text/html`.

## Supervised Application Assistant

v0.2.0 added an **Apply** action that creates a durable, user-scoped application attempt; it never submits an application immediately. Attempts retain the original job/role, URL resolution, selected-document snapshots, status, sanitized errors and an append-only event history. The tracker can therefore retain exactly which resume and optional cover letter were selected when an application was marked Applied or submitted.

Each search role supplies the default resume for jobs it found. Roles retain versioned ATS-text resumes and protected PDF resume/cover-letter files; a replacement creates a new current version while preserving prior versions. Saved documents can be selected without an AI call. AI tailoring is separately opt-in for a resume, a cover letter, or both; provider keys are account-level, masked on reads, and no generation occurs unless the user explicitly requests it. Generated documents require approval, must trace substantive claims to the source resume/profile, and their artifacts remain outside the public web root.

The reusable application profile holds user-approved stable contact and work-preference facts only. Employer-specific questions, attestations, compensation questions and protected or voluntary demographic disclosures always require the user's answer and review.

The Playwright worker is isolated from Flask and uses signed, scoped requests. Form preparation and real submission are both disabled by default, even though the worker code is installed; they require separate explicit environment flags and acceptance testing before activation. CAPTCHA, MFA, login, assessments and LinkedIn remain manual handoff conditions. Every eventual submission requires a single-use final confirmation token.

## Search Configuration and Providers

Configuration includes keywords, parked keywords, blocked words, boost terms/weight, remote/local choices, location/ZIP/radius, salary, scoring method/threshold, enabled sources and pagination. Dependent controls are disabled when their prerequisite is absent (for example, boost weight without boost terms and local controls when local search is off). Context help describes effects and interdependencies in plain language.

Users can save the complete criteria and resume as named roles, switch them through blue/slate folder tabs with role-color accents, update the active role through Save criteria, or delete a role. Search setup has only named-role tabs; its Criteria/Resume and Credentials section tabs remain nested below. Switching a role uses the existing activation API and prompts before discarding unsaved criteria or resume edits; cancellation or load failure leaves the prior role selected. Credentials, including optional document-generation provider keys, remain account-level and are not copied into role snapshots. The active role name and color are attached to search results and carried into tracker records.

Supported sources are Adzuna, SerpAPI and experimental Indeed. Adzuna requires an App ID and App Key; SerpAPI requires one API key and charges its own request credits during live runs. The built-in no-credit test uses fixture data and does not call providers.

The dashboard, Run a new search workspace and Search setup all show the authenticated profile's current SerpAPI balance and estimated next-run use. The estimate updates immediately from active keywords, enabled remote/local passes and their SerpAPI page counts. The account lookup uses the stored active key only on the server; credentials are never added to a browser URL.

## Search Execution

`api.py` starts `main.py` as a detached process so a Gunicorn restart does not automatically kill an in-flight run. Flask uses AppCentral Python; the ML pipeline uses Entware Python with the required `LD_LIBRARY_PATH` and `PYTHONPATH`.

Pipeline sequence:

1. Load the authenticated user's configuration and resume.
2. Query enabled sources.
3. Deduplicate and exclude stable job keys on the user's Not interested ignore list.
4. Apply simple filters.
5. Fetch/enrich descriptions and re-check page-derived locations.
6. Score against the resume.
7. Save run state, published results and the complete decision audit to SQLite.

Each deduplicated candidate retains its source, provider-query keywords and remote/local discovery passes. The audit records ordered ignore-list, blocked-word, keyword, salary, location, enriched-page-location and resume-score checks, including pass/fail/not-applicable evidence and the final outcome. Search insights can therefore show why an individual job was included or excluded and aggregate the same data by active keyword, blocked word, boost term, source and pipeline criterion. Runs created before migration 010 have no reconstructed audit; reports begin with the first post-migration search.

The portal polls job status. If SQLite says a run is active but its PID has died, the API changes the run to error and includes a bounded log tail.

## Priority employers and direct career adapters

Priority employers are account-level sources stored in `employer_sources`, with role membership in `employer_source_roles`. `scrapers/adapter_registry.py` is the only URL-to-adapter and dispatch registry: existing SuccessFactors, Workday, and UKG integrations remain deterministic, while Jibe/iCIMS and generic structured-data support are positively identified by bounded server-side probes before they are saved or re-tested. A URL that is not positively supported remains the explicit company-qualified SerpAPI fallback.

Jibe/iCIMS support uses the same-host public `/api/jobs` and `/api/jobs/{slug}/{language}` contract, with PPL and Booking.com fixtures proving the reusable variant. The structured-data adapter accepts only valid `schema.org/JobPosting` JSON-LD from a page or a bounded set of same-host detail links. Both adapters use `safe_fetch` for HTTPS-only, DNS-pinned, same-host requests; enforce bounded pages/details and monotonic endpoint budgets; verify company/alias agreement; preserve stable IDs, canonical and application URLs, full descriptions, and direct-source provenance; and return to the existing deduplication, filtering, scoring, audit, Search Results, Search Insights, ignore, and tracker paths. Test/no-credit mode makes no employer-site or SerpAPI request.

Migration 021 widens the `employer_sources.adapter` check constraint for `jibe` and `structured_data` through an ID-preserving rebuild. It preserves role memberships and maps those values back to the explicit unsupported fallback on downgrade. User-facing source cards distinguish Direct Jibe/iCIMS and Generic structured-data page checks, including bounded test-result counts.

## Database

Schema changes are applied through `migrate.py` and numbered modules in `migrations/`. Major tables include `users`, `user_profiles`, `sessions`, `login_attempts`, `password_reset_requests`, `search_runs`, `job_results`, `search_run_audits`, `job_decisions`, `application_records`, `saved_views`, `search_presets`, `application_profiles`, `application_attempts`, `application_events`, `application_artifacts`, `application_confirmation_tokens`, `role_documents` and `schema_migrations`.

Any ad-hoc raw SQLite connection that deletes relational data must first execute `PRAGMA foreign_keys = ON`; `database.py` already does this. Use `JOBSEARCH_ENV=production` for hand-run PROD maintenance scripts so environment resolution cannot select the DEV database.

## Deployment and Service Management

The user runs deployment interactively from DEV:

```bash
cd /volume1/Web/JobSearch_dev
./deploy_to_prod.sh
```

The script previews diffs and the changelog stamp, requires `yes`, backs up PROD, excludes environment data/secrets/logs, copies code, applies migrations, restarts PROD and requires `/health` to return 200. DEV's `data/` directory is never copied to PROD: each environment retains its own criteria, roles, credentials, resumes, cover letters, results and application history. It retains recent tarball backups and prints a rollback recipe on failure.

Service commands:

```bash
cd /volume1/Web/JobSearch_dev
./scripts/restart_jobsearch_dev.sh

cd /volume1/Web/JobSearch
./scripts/restart_jobsearch_prod.sh
```

## Operational Gotchas

- Internal links must use `url_for()` or include `request.script_root`; PrefixMiddleware strips the external prefix from `request.path`.
- `static/portal.js` is deployed verbatim to both environments and derives its API prefix from `location.pathname`; never hardcode `/JobSearch_dev`.
- `application_tracker` and `job_triage` use `job_key`, not `job_id`.
- Indeed frequently returns 401/403; disabling it is normal.
- Adzuna coordinates may be snapped to a search center; the page-location re-filter corrects these when a usable posting location can be extracted.
