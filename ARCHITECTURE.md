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
| `agent_queue.py`, `agent_packet.py`, `desktop_keys.py`, `question_bank.py`, `tracker_sync.py` | Desktop apply agent: the Apply for me queue and the claim, review, approval and outcome rules; the apply packet; revocable desktop session keys; saved question answers; the one-row tracker write |
| `document_generator.py` | Optional truth-constrained document-generation providers, Structured Output validation and artifact rendering |
| `role_documents.py` | Versioned ATS text, PDF role documents and authenticated downloads |
| `portal.py` | Authenticated dashboard route |
| `profile_store.py` | SQLite persistence for configuration, resume, runs, results, tracker and saved views |
| `database.py` | Environment-aware connections with foreign keys enabled |
| `migrations/` | Numbered schema migrations |
| `templates/portal.html` | Dashboard, onboarding, results, tracker and search-setup screens |
| `static/portal.js` | Portal interaction, filtering/sorting, tracker, onboarding and run polling |
| `static/agent_ui.js` | Apply for me and Open apply session controls, the Desktop session key card and the Saved answers card |
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
- **Run a new search** — no-credit fixture test, live search and cancellation. A search runs as a detached process, so leaving the page never stops it. `portal.js` `resumeRun()` checks both modes on load and, if one is running, restores the status line and Cancel button and resumes polling; a spinner in the top bar shows while a run is in progress. `profile_store.latest_results` skips runs still in state `running`, so the results page keeps showing the last finished run until the new one completes (falls back to the newest run only if none has finished).
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

The reusable application profile (`application_profiles.profile_json`) is account-level and holds three kinds of user-approved facts: stable contact and work-preference fields (first, middle and last name, with `full_name` composed from them on save so older readers keep working; `auto_apply.name_parts` splits a profile that only has a full name and marks a guess as ambiguous); standard `screening` answers (current or former employee, 18 or older, restrictive agreements, U.S. person, conflict of interest as yes/no/ask-me, plus shift availability and notes; "18 or older" and "U.S. person" are preselected Yes in the form until the user saves); and an optional `eeo` section for voluntary self-identification (gender, race/ethnicity, veteran, disability, each from a fixed list). `eeo` is stored only when the user explicitly opts in, unticking and saving discards it, and the answers must still be shown to the user on every review before a submit. Saving a request that omits `screening` or `eeo` preserves the stored section, so a stale browser tab cannot erase it. Employer-specific questions, attestations and compensation questions still require the user's answer and review.

Only `auto_apply.shareable_profile` (the flat contact and preference fields) is passed to an AI document provider or to the form-filling worker; screening, self-identification, work history and education are never sent to either. The one exception is the user's own Claude desktop session: when the user queues a job with Apply for me, the apply packet (see Desktop Apply Agent) carries the role's work history and education, the screening answers and, only when the user opted in, the self-identification answers marked review-required. `auto_apply.redact_profile` replaces screening and EEO values with a marker for any log, event or summary.

Work history and education are role-level, because each search role can have its own resume. They live in `role_application_history` (migration 022, primary key `user_id` + `role_key`, the same role keys as `role_documents`: `default` or `preset:<id>`), with rows of job title, employer as the application form shows it, location, `MM/YYYY` start and end (or "current"), description, and education level, school, degree, field, location, graduation date. `role_history.py` validates every row on the server (at most 15 jobs and 8 education entries, month required, start not after end; overlapping jobs and the same employer with different dates in two roles only warn), assigns stable row ids, stores a "dates verified" date that is cleared when a date changes, and keeps one previous version per role for a one-step undo. The resume importer is deterministic and produces draft rows with visible issues; drafts are never stored until the user confirms them. Copying from another role (merge or replace) builds a preview only; the user edits it and saves through the same validated endpoint. Creating a role can start clean or start from another role's history. Deleting a role deletes its history. Routes: `/api/role_history`, `/api/role_history/import_resume`, `/api/role_history/copy_preview`, `/api/role_history/undo`. UI: the Application assistant's profile card and its "Work history and education" card (`static/profile_answers.js`).

The Playwright worker is isolated from Flask and uses signed, scoped requests. Form preparation and real submission are both disabled by default, even though the worker code is installed; they require separate explicit environment flags and acceptance testing before activation. CAPTCHA, MFA, login, assessments and LinkedIn remain manual handoff conditions. A worker submission requires a single-use final confirmation token. The desktop-agent path has no token: the server has no agent submit endpoint, and it refuses a `submitted` outcome until the user's approval of that exact review is recorded.

## Desktop Apply Agent

Direction decided 2026-10-02: a Claude session on the user's own PC works in the user's own signed-in browser, and JobSearch is the queue and the system of record. The Playwright worker stays dormant. Built as backlog 19.2 to 19.5 and shipped in v0.2.10 to v0.2.12.

**Queue.** Apply for me (`POST /api/agent_queue {job_key, role_key?}`, session auth) creates or reuses an attempt inserted directly in the state `queued_for_agent` with `agent_owned = 1` and the role key recorded (`default` or `preset:<id>`), so the worker can never see it as `queued`. It refuses, with a `code`, a job with no role (`role_required`), a role without confirmed work history (`history_required`) or without saved resume text (`resume_required`, the account-level resume is never a fallback), a LinkedIn or invalid URL, and a job that already has a worker-path or in-progress attempt. `transition()` refuses every move of an `agent_owned` row except cancel unless the caller passes `allow_agent`, and prepare, confirmation-token, submit, document approval and retry return before writing on such a row. Migration 023 added the attempt columns (`role_key`, `agent_owned`, `claim_id`, `claimed_at`, `lease_expires_at`, `posting_status`, `posting_checked_at`, `review_json`, `user_approved_at`, `approval_note`) and the tables `desktop_session_keys` and `application_question_bank`.

**Desktop session keys.** A revocable login-style key for the Claude session, not an AI-provider key and never used for an LLM call. Created, listed and revoked on the Application assistant card (`/api/desktop_session_keys`); shown once; only its SHA-256 is stored; 90 days; at most five per user. Each environment has its own key.

**Agent API** (`/api/agent/...`, `Authorization: Bearer` key only, a session cookie is ignored): list the queue; `queue/next` claims the oldest queued attempt (a single guarded UPDATE on the request connection, one winner between two sessions, a two-hour lease that only an `analyzing_form` attempt loses) and returns the packet; then per attempt `posting` (open, closed or unreadable; closed cancels the attempt and notes the tracker), `progress`, `review`, `approval`, `outcome`, `release`, `resume` (the role's own PDF only) and `questions`. Every call carries the `claim_id`; an outcome keeps it so a retried outcome is idempotent, and whether a claim is live is decided by state. Request bodies over 64 KB are refused.

**Packet** (`agent_packet.build`): job, role, the role's resume text and ids, profile contact fields, `name_parts`, the attempt role's work history and education only, UI labels for education and self-identification, screening answers each with `safe` / `ask_user`, saved answers, `employer_match`, and a fixed stop list. `eeo` is present only when the user opted in and is always `review_required`. A fuzzy match of the job's employer against confirmed history across every role nulls the current-employee, former-employee and conflict-of-interest answers so they must be asked.

**What the server enforces.** The approval must carry the SHA-256 of the stored review, and must follow a review that listed any opted-in self-identification; a new review clears an earlier approval; `submitted` is refused without an approval; the outcome writes exactly one tracker row (`tracker_sync`, one-row upsert on the request connection, never `save_triage`), moves Saved to Applied, never moves a later or closed stage, and adds one note per attempt. The server cannot see the browser, so the clicks are kept by the skill.

**Saved answers** (`question_bank.py`, `/api/question_bank`): questions the agent meets that are not in the profile. Government-identifier questions and SSN-shaped answers are refused. Only yes/no and choice answers are reusable on their own, and never when the question names the company; free text is reusable only after the user ticks Reuse. An answer given in chat is stored `confirmed` with source `agent_chat`, and the user can correct or delete it on the Saved answers card. The last blocking answer returns a waiting attempt to the queue.

**Skill and launch.** The skill lives with the project docs, not the NAS: `.claude/skills/jobsearch-apply/` (the instructions, a Workday playbook, a stub for other portals, and `scripts/js-api.ps1`, which reads the key inside itself, accepts only `/api/agent/` paths and picks `desktop_key.dev` or `desktop_key.prod` by `target.txt`). The Project Portal bridge allowlist has a `jobsearch-apply` entry, and the portal's **Open apply session** link is the constant `jppp://claude/jobsearch-apply`. The skill's fixed rules: no passwords or account creation, no CAPTCHA, no terms-of-use beyond the user's standing authorizations, no certification or submit without a yes for that application, LinkedIn manual.

**Availability.** The API answers 503 unless `JOBSEARCH_AGENT_API_ENABLED` is true. `scripts/start_jobsearch.py` sets it true by default for both environments, so reboots, restarts and deploys keep it on; `false` in the caller's environment turns it off. The form-prep and submit worker flags are separate and remain off.

**Known gaps.** A site whose last click submits without a review page (seen on one recruiter portal) can send an application before the review and approval; the skill now treats such a click as the submit, but the server has no outcome for "submitted outside the review" and the manual outcome leaves the tracker to be set by hand. Setup of a new PC (key file, bridge, skill location) is manual until backlog 25, and roles cannot yet follow a resume file on the PC (backlog 26).

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

Schema changes are applied through `migrate.py` and numbered modules in `migrations/`. Major tables include `users`, `user_profiles`, `sessions`, `login_attempts`, `password_reset_requests`, `search_runs`, `job_results`, `search_run_audits`, `job_decisions`, `application_records`, `saved_views`, `search_presets`, `application_profiles`, `application_attempts`, `application_events`, `application_artifacts`, `application_confirmation_tokens`, `role_documents`, `role_application_history`, `desktop_session_keys`, `application_question_bank` and `schema_migrations`.

Any ad-hoc raw SQLite connection that deletes relational data must first execute `PRAGMA foreign_keys = ON`; `database.py` already does this. Use `JOBSEARCH_ENV=production` for hand-run PROD maintenance scripts so environment resolution cannot select the DEV database.

## Backlog and Admin Page

The product backlog is one markdown file per item under `backlog/items/<id>.md` in the DEV directory; each holds the item's prose, plan (under a plan-stage marker), acceptance criteria and optional `Status`, `Prerequisites` and `Related items` lines. `scripts/generate_backlog_index.py` is a thin command over the shared `backlog-kit` (pinned in `requirements.txt`, configured by `backlog.config.json`) and builds the index `backlog.md` from those files (`--check` detects a stale index). The kit is the one implementation used by every project; the format matches the sibling GardenBuddy app.

`backlog_admin.py` renders `/backlog` (`templates/admin_backlog.html`) through the kit, which owns the parser, the validation notices and the page markup: items grouped by priority with a legend, parent/child families, plan-stage and status badges, blocked-by/unblocks/related callouts, a display-only active-claim badge from `backlog/active_claim.md`, and an expandable full text for every item in escaped form. The app carries no parser of its own. The page is read-only and requires a signed-in user with `users.is_admin = 1`; everyone else receives 403 and the dashboard shows the Backlog card only to admins. The flag is a one-row update on `users.is_admin`, done with explicit approval per use (`scripts/set_admin.py <email> --env production|development` also exists, but plain SQL is preferred). No request data reaches the filesystem as a path. DEV and PROD both read the DEV copy by absolute path (overridable through the `BACKLOG_ROOT` app setting for tests); `deploy_to_prod.sh` excludes `backlog/`, `backlog.md` and `plans/`.

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

**v0.2.10 to v0.2.12 shipped 2026-10-02 and 2026-10-03:** John deployed the desktop apply agent (migration 023, the queue and agent API, desktop session keys, saved answers, the Apply for me and Open apply session controls), then the always-on agent API default and per-environment key naming (v0.2.11), then first, middle and last name (v0.2.12). After the v0.2.12 deploy the 19 compared code, template, script, migration and test files match between DEV and PROD by MD5, both changelogs show v0.2.12 with `[Unreleased]` empty, migration 023 is applied (23 applied, 0 pending), PROD `/JobSearch/health` returned `ok`, and the PROD agent route answered 401 without a key (the API is on). A real DEV run on 2026-10-02 exercised the key, Apply for me, claim, packet, posting check, progress and a resume upload; it also showed a recruiter portal submitting on its last Next, recorded as a manual outcome. The review, approval and approved-submit path, the launch link and a multi-job batch have not been exercised against a real site.

## Operational Gotchas

- Internal links must use `url_for()` or include `request.script_root`; PrefixMiddleware strips the external prefix from `request.path`.
- `static/portal.js` is deployed verbatim to both environments and derives its API prefix from `location.pathname`; never hardcode `/JobSearch_dev`.
- `application_tracker` and `job_triage` use `job_key`, not `job_id`.
- Indeed frequently returns 401/403; disabling it is normal.
- Adzuna coordinates may be snapped to a search center; the page-location re-filter corrects these when a usable posting location can be extracted.
