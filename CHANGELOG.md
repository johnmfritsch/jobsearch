# JobSearch — Changelog

## [Unreleased]
### Added
### Fixed
### Changed

---

## [v0.2.7 — September 27, 2026]
### Added
- **Rejected** is now a distinct stage for saved and manually added jobs, with its own badge and stage filter. Existing Closed jobs keep their stage.
- Saved jobs and Search setup now use folder-style role tabs. Saved jobs opens on All and can filter by each saved role; Search setup switches active roles in the same tab style while retaining its criteria and credentials sections.
### Fixed
### Changed

---

## [v0.2.6 — September 11, 2026]
### Added
- **Direct Jibe/iCIMS priority employers**: verified public Jibe/iCIMS careers pages can now be checked directly, without SerpAPI credits. Support is fixture-backed across independent PPL and Booking.com contracts rather than tied to either employer.
- **Generic structured-data priority employers**: qualifying careers pages with valid `schema.org/JobPosting` data can be checked directly, with bounded coverage and clear unknown-field handling.
- Priority-employer cards identify **Direct Jibe/iCIMS** and **Generic structured-data page** checks, and test results distinguish the available total from the bounded fetched sample.
### Fixed
- Saving or testing a careers URL now revalidates its platform server-side. Unknown or JavaScript-only sites remain an explicit company-search fallback instead of being incorrectly claimed as direct support.
- Direct employer collection now keeps same-host traversal, request deadlines, stable job identities, company/alias agreement, complete descriptions, keyword provenance, and global collection limits intact. Test/no-credit runs do not contact employer sites or SerpAPI.
- Migration 021 preserves priority-employer rows and role memberships while adding the new adapter values.
### Changed

---

## [v0.2.5 — September 1, 2026]
### Added
- **Priority employers**: mark specific companies to be checked on every live search, independent of that role's normal keyword, location, pay, and resume-match rules. SAP SuccessFactors and Workday career sites are checked directly, at no SerpAPI cost; any other company is instead searched by name through SerpAPI, sharing the same credit balance as regular keyword searches.
- Add a priority employer by careers URL (the company name is read from the page automatically) or by company name alone — JobSearch first tries to find a direct careers page itself, for free, using common URL patterns, before falling back to a name-only search.
- Each priority employer can be scoped to one saved role, to every role, or removed from a role entirely, editable per role from Search setup.
- A manual "Test source" check reports real, paginated posting counts; a check also runs automatically right after adding or editing a priority employer.
### Fixed
### Changed

---

## [v0.2.4 — August 19, 2026]
### Added
### Fixed
- Pasting bulleted or numbered text into a Notes field (the saved-job notes log, or Add a job manually) lost its list markers and often its line breaks — a plain textarea only holds text, so the browser's own paste conversion flattened it. Pasted rich text is now converted to plain text ourselves first, preserving bullets (as •), numbering, and line breaks.
### Changed

---

## [v0.2.3 — August 19, 2026]
### Added
- A saved job's Notes field is now a chronological, timestamped log instead of one editable block: type a note, click **Save note**, and it's appended to the job's history with a date and time — earlier notes stay intact and are shown newest first. The per-note limit also grew from 5,000 to 20,000 characters.
### Fixed
### Changed

---

## [v0.2.2 — August 18, 2026]
### Added
- Saved jobs & applications now lets you edit a tracked job's title, company, location, work style, salary, posting address, application address, and role from its expanded details, alongside the existing stage, dates, and notes — most useful for manually added jobs, which have no other source for these fields.
- The Role field, on both Add a job manually and a saved job's edit form, now offers an explicit **N/A** choice for jobs that do not belong to any of your existing named search roles/presets, instead of always defaulting to the active one.
### Fixed
### Changed

---

## [v0.2.1 — August 18, 2026]
### Added
- Saved jobs & applications has an **Add a job manually** form for jobs applied to outside JobSearch. Pasting the posting address reads the job title, company, location, work style, salary, and posting date from it; entering a job title and company by hand works when there is no address.
- Manual entry reads Greenhouse, Lever, and Ashby postings through their public APIs, falls back to schema.org JobPosting data embedded in the page, then to OpenGraph tags and the page title. Each field reports where its value came from, and values guessed from the page title are flagged before saving.
- Manually added jobs are marked **Added manually** in the tracker, take the active search role by default, and are left out of future searches so an application already in progress is not offered again as a new match.
- Adding a job by hand overrides a previous **Not interested** dismissal for the same posting: the job returns to the tracker at the chosen stage, keeping its earlier notes and stage history.
### Fixed
- Jobs marked Not interested could reappear in later searches. The ignore list was compared against only one identity per job — the first of the source id, the posting address, or title and company that was present — so a posting that arrived from a different source, keyed differently, slipped past it. All three identities are now compared, on both sides of the check.
- The Minimum score filter in Saved jobs & applications treated an unscored job as scoring zero, which would have hidden every manually added job. An unscored job is now exempt from that filter rather than failing it.
### Changed
- Single tracker records are written individually instead of by rewriting every record for the account, so a concurrent change elsewhere in the tracker can no longer be lost.

---

## [v0.2.0 — August 10, 2026]
### Added
- Search results and saved-job details now include an Apply action that starts a supervised application attempt without submitting anything.
- Application assistant dashboard provides reusable contact and work-preference facts while reserving employer attestations and voluntary demographic answers for the user.
- Durable application attempts now retain user-scoped status, sanitized errors, automation provenance, document selections, and an append-only progress timeline.
- Account-level masked OpenAI, Anthropic Claude, and xAI Grok credentials support selectable truth-constrained document generation.
- Each application defaults to the resume saved with the search role that produced the job; AI tailoring is an explicit per-attempt checkbox.
- Saved role resumes are rendered as DOCX and PDF without being sent to an AI provider.
- Every search role now keeps versioned ATS-text and PDF resume files, with current-version replacement controls in Search setup and Application assistant.
- Role cover-letter PDFs can be uploaded, versioned, selected per attempt, or replaced by an approved AI-generated version.
- AI document generation can target the resume, cover letter, or both, with estimates adjusted to the selected output.
- Saved-document selection is visually separate from the optional AI-generation controls.
- Selected files are copied into immutable attempt history and recorded when an application is submitted or marked applied.
- Work-authorization, sponsorship, relocation, travel, and stable-fact profile fields now include examples, help, or clear reusable choices.
- Document generation now displays estimated and actual GPT-5.6 Terra token cost and sends Responses API requests with structured output and `store:false`.
- Every generated resume and cover-letter version requires approval before it can be selected for an application.
- Single-use, expiring final-confirmation tokens prevent accidental or duplicate assisted submissions.
- A separate single-session Playwright application worker detects supported ATS forms, fills conservative known fields, uploads approved artifacts, and pauses for missing answers, login, or CAPTCHA.
- Initial ATS detection covers the observed Ashby, Zoho Recruit, and Workday destinations plus Greenhouse and Lever; unsupported and LinkedIn destinations use manual handoff.
- Version-stamper regression coverage locks the 9-item patch and 10-item minor boundary, and deploy previews now explain the chosen bump.
### Fixed
- Scraper credential edits preserve all account-level document-generation keys instead of replacing unrelated credentials.
- Failed document generations clean up partial artifacts and retain a visible failure state.
- Aggregator destinations such as ZipRecruiter are labeled as available job listings instead of being misrepresented as direct application pages.
### Changed
- Form preparation and real submission are installed behind separate disabled-by-default feature flags pending DEV acceptance.
- Application attempt analytics explicitly distinguish manual workflows from attempts that used browser automation.

---

## [v0.1.1 — August 5, 2026]
### Added
- Scraper credentials' Edit action now reveals the stored value as copyable text (instead of a blank replacement field), for both SerpAPI keys and Adzuna App ID/Key.
- Run a new search now includes a color-coded Role dropdown so a different saved role can be activated and searched without visiting Search setup first.
### Fixed
- DEV and PROD now use separate URL-scoped session cookies, allowing both environments to stay logged in concurrently in the same browser.
### Changed

---

## [v0.1.0 — August 4, 2026]
### Added
- SerpAPI credit status now shows the account plan's renewal date and monthly renewal allowance when provided by the SerpAPI Account API.
- Removing a saved job now offers to add it to the durable ignore list so future searches exclude it.
- Named search presets can save, load, update, and delete complete criteria-and-resume combinations for different target roles while keeping scraper credentials account-level.
- Search setup now groups scoring method, plain-language resume-match selectivity, and boost influence with a live explanation and latest-search cutoff context.
- Saved roles now receive distinct persistent colors, and saved-job cards show the originating role with the same color.
- Run a new search now includes an Edit search setup shortcut.
### Fixed
- Saving criteria while a named role is active now updates that role's stored criteria and resume, so switching roles no longer restores stale values.
- Search setup opened from Run a new search now returns there, while direct Search setup visits still return to the dashboard.
- The live SerpAPI balance and next-run estimate now remain visible at the top of Search setup instead of appearing below collapsed source settings.
- Saved-search-preset controls now appear above the credit estimate and Search criteria in a compact switcher.
- Preset buttons now remain in a stable alphabetical position; selection is indicated only by a high-contrast button style without changing text width or moving the preset name.
- The live SerpAPI estimate is repeated at the bottom of Criteria & resume, and comma-separated fields now state their required format directly.
- Search setup now restores and highlights the last active saved preset; legacy Default state falls back to the most recently updated preset, whose name is shown above Search criteria.
- Help icons for remote/local choices and Saved keyword bank now sit immediately after their field labels.
- Workspace headers now keep Back in the upper-left and center each page title.
### Changed
- Jobs marked Not interested are treated as indefinitely retained ignore entries and are recorded as an exclusion in Search insights.
- SerpAPI and Adzuna credentials now have distinct boxed sections, saved values display as masked text with an Edit action, and the shared Save credentials action sits outside both provider sections.
- SerpAPI and Adzuna can be enabled or disabled from checkboxes in their credential boxes; both default to enabled for new profiles, and disabled providers are grayed out without removing saved credentials.
- Saved search presets now use a compact, prominent switcher above the credit estimate; each named preset loads with one click, while Save as and Delete remain single actions for the active setup.
- The selected preset is now the dark button; inactive preset buttons use a lighter treatment.
- Criteria & resume and Scraper credentials now appear as attached tabs above one bordered setup area.
- The preset strip is now labeled Role, the selected role is slightly enlarged without shifting the other buttons, and the redundant Current preset banner has been removed.
- Minimum salary now belongs to section 2, while section 3 is dedicated to match-quality controls.
- Inactive role buttons now use softer gray text so the enlarged dark active role is unmistakable.
- Match quality now sits inside section 1 and follows the pipeline visually: scoring method, adjacent Boost terms and Boost influence, then resume-match selectivity; the remaining sections were renumbered.

---

## [v0.0.4 — August 4, 2026]
### Added
- A live SerpAPI balance and next-run credit estimate now appear on the dashboard, Run a new search, and Search setup; the estimate updates immediately as keywords, search modes, provider state, or page counts change.
- Search insights now records every deduplicated candidate's decision trail and provides job-first and search-setup-first reports with live filters for run, outcome, source, keyword, work style, criterion result, score, and title/company.
### Fixed
- A search with no jobs passing its configured filters no longer bypasses those filters and scores every fetched job.
### Changed
- SerpAPI account checks now use the authenticated profile's stored active key server-side instead of putting an API key in a browser query string.

---

## [v0.0.3 — August 4, 2026]
### Added
- Saved jobs and applications can now be filtered by work style, score, salary, source, stage, and posting age.
- Application stages now have distinct card and badge colors, and jobs can be removed from the tracker from their expanded details.
### Fixed
- Saving a job now preserves its real title and other search-result details instead of allowing it to appear as "Saved job."
- Marking a job applied now records and displays the applied date.
### Changed
- The Closed stage is now explained as a retained final state for ended opportunities, distinct from removing a job from the tracker.

---

## [v0.0.2 — August 3, 2026]
### Added
### Fixed
- "Save", "Applied", "Not interested", and the tracker's "Save job details" now actually record the change instead of failing with "Invalid triage request."
### Changed

---

## [v0.0.1 — August 3, 2026]
### Added
### Fixed
- Signing in after being bounced to the login page (e.g. from a stale session) now correctly returns you to the page you were on, instead of a blank "Not Found" page.
### Changed

---
