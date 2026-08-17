# Changelog

All notable user-facing changes are recorded here. Add entries under **Unreleased** while working in DEV; `deploy_to_prod.sh` stamps that section into the next version only after deployment is confirmed.

## [Unreleased]
### Added
- Saved jobs and applications now have search-result-style filters for work style, score, salary, source, stage, and posting age, plus explicit removal from the tracker.
- Onboarding now explains live job providers, lets users connect Adzuna or SerpAPI credentials, and links directly to the providers’ official signup/key pages; the same guidance and links appear in Scraper credentials.
- First-login onboarding for new accounts, with one explained setup question at a time, a review step, and a persistent option to skip directly to the dashboard.
- Restored authenticated result filters, named saved views, full job detail/match explanations, Not Interested, and undo.
- Interactive saved-job and application details with stage, dates, notes, posting links, and follow-up tracking.
- A saved keyword bank for retaining inactive search ideas while experimenting with active terms.
- Masked SerpAPI and Adzuna credential management for authenticated DEV profiles, preserving unchanged secrets and active/spare entries.
- DEV authentication milestone: signed-in portal, per-session profile binding, and one-time-code claiming for imported user profiles.
- DEV portal now shows authenticated SQLite-backed search history and latest result snapshots.
- SQLite-native DEV accounts can save resume and core job-search preferences from the portal.
- SQLite-native DEV profiles can run a no-credit built-in sample search without legacy fixture files.
### Changed
- Tracker stages now use distinct badge, border, and card-highlight colors; Closed is explained as retained history for an ended opportunity rather than deletion.
- Search setup now groups related decisions, explains dependencies and beginner-facing consequences in expanded help, and disables controls that cannot currently have an effect.
- First-login guidance now defines provider credits and states clearly that a resume is required before any search can produce ranked matches.
- Search-result rows already marked Saved, Applied, or Not Interested are subtly grayed and labeled so untouched jobs stand out.
- Latest SQLite result snapshots now return the complete result set so filters and saved views are not limited to twelve rows.
- Saved-job details now expand and collapse within each card, and posting actions use the same button styling as the rest of the portal.
- Results can be sorted ascending or descending by every column, with Score descending as the default and a visible sort indicator.
- Keyword, blocked-word, and boost-term editors are larger and easier to review.
- Dashboard functions now open as dedicated same-tab screens with top and bottom navigation back to the dashboard.
- Reorganized the authenticated portal into a dashboard with separate application, result, run-search, and search-setup workspaces.
- DEV profile configuration, resumes, saved views, and application-tracker decisions now use SQLite as their canonical store; legacy folders remain only for run status and test fixtures during migration.
- DEV run progress and published result snapshots are now recorded in SQLite; the JSON status files remain a temporary compatibility mirror.
- Database-only DEV profiles can start confirmed live searches from the portal without a legacy user directory.
- Release safeguards: changelog stamping, pre-deploy validation, post-deploy readiness checks, and backup retention.
- Job result details now explain direct keyword and boost-term matches and let you Save, mark Applied, or dismiss a role with Undo.
- Application tracker workspace for saved and applied jobs, with pipeline stages, notes, applied and follow-up dates, stage history, and direct posting/application links.
- Phone-friendly result cards and a guided search setup that keeps advanced source and credential controls collapsed by default.
### Fixed
- Saved-job titles and metadata are recovered from stored search results when available, and Applied dates are recorded and displayed automatically when a job enters an active application stage.
- Live searches now stop with an actionable message when no source is enabled or an enabled Adzuna/SerpAPI source lacks required credentials.
- New accounts now begin with a genuinely empty resume, and attempts to run without real resume text return a clear explanation instead of starting a doomed search.
- URL-only job results now receive deterministic tracker-safe IDs, and failed Save/Applied actions show a visible error instead of failing silently.
- Work-style cells now display their actual Remote, Hybrid, On-site, or Unclear category so the visible alphabetical order changes correctly in both directions.
- Restored hover and keyboard-focus help icons across the search-setup fields.
- Work-style sorting is alphabetical by the displayed category in both directions.
- Work-style sorting now uses an explicit Remote, Hybrid, On-site, and Unclear order instead of unreliable raw-text comparison.
- Restored structured job-result formatting, metadata, links, score badges, and application actions in the authenticated results screen.
### Changed

---

## [Baseline — July 20, 2026]

The Job Search Agent was already running in production before release versioning was introduced. Earlier changes are intentionally not reconstructed here; this section establishes the changelog baseline.
