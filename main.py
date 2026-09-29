# main.py -- Job Search Agent (multi-user extension with test mode)

import json
import os
import sys
SCRAPERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scrapers')
sys.path.insert(0, SCRAPERS_DIR)
import time
from collections import Counter
from matcher import score_jobs as score_jobs_against_resume
from filters import evaluate_simple_filters
from resume_parser import load_resume_text
from profile_store import load_config as load_profile_config, load_resume
from profile_store import (write_run_status, save_run_results, save_run_audit,
                           load_ignored_job_keys, stable_job_key,
                           active_employer_sources, record_employer_source_status)
from html_output import write_results_html
from deduplicate_jobs import deduplicate_jobs


# === CONFIG LOADER ===
def write_status(user, test_mode, state, message):
    """Write job status to status file with message history"""
    from datetime import datetime
    import json
    import os

    user_dir = os.path.join("configs", user)
    status_file = os.path.join(user_dir, "job_status_test.json" if test_mode else "job_status.json")

    try:
        write_run_status(user, test_mode, state, message)
        # Database-only profiles intentionally have no compatibility folder.
        if not os.path.isdir(user_dir):
            return
        # Read existing status to preserve message history
        messages = []
        if os.path.exists(status_file):
            try:
                with open(status_file, "r") as f:
                    existing = json.load(f)
                    messages = existing.get("messages", [])
            except:
                pass

        # Add new message to history (keep last 50)
        messages.append({
            "text": message,
            "timestamp": str(datetime.now())
        })
        messages = messages[-500:]  # Keep last 500 messages

        # Write updated status atomically (temp file + os.replace) to avoid
        # race condition with api_handler reading the file simultaneously,
        # which can cause an indefinite stall on this NAS filesystem.
        tmp_file = status_file + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump({
                "state": state,
                "message": message,
                "messages": messages,
                "timestamp": str(datetime.now())
            }, f)
        os.replace(tmp_file, status_file)
    except Exception as e:
        print(f"Warning: Could not write status file: {e}")

def load_config(config_path):
    if not os.path.exists(config_path):
        print(f"Missing {config_path} -- exiting.")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pipeline_check(job, key, label, status, evidence, **details):
    audit = job.setdefault("_decision", {"checks": [], "outcome": "unknown"})
    check = {"key": key, "label": label, "status": status, "evidence": evidence}
    check.update({k: v for k, v in details.items() if v is not None})
    audit.setdefault("checks", []).append(check)


def _job_identities(job):
    """Every identity string this job could be keyed by.

    save_run_results() and save_run_audit() both key a job as
    `id or url or "title|company"` -- first non-empty wins. Which field wins
    depends on the source, and the same posting reaches us from several: a run
    that returns it with an `id` keys it differently from a run that returns
    only a URL, and differently again from a job the user added by hand from
    the posting URL. Testing only the winning identity against the ignore list
    therefore let dismissed jobs reappear whenever the winner changed between
    runs. Test all three instead -- a match on any one is a match.
    """
    identities = []
    for value in (job.get("id"), job.get("url"),
                  f"{job.get('title', '')}|{job.get('company', '')}"):
        if value and value != "|":
            identities.append(str(value))
    return identities


def enrich_jobs_with_full_pages(prefiltered, user, test_mode, cfg):
    """Returns count of jobs removed by location re-check."""
    """
    Fetch full job posting pages for filtered jobs and re-classify
    remote status with the enriched description data.
    """
    from filters import (
        _fetch_full_description, _classify_remote_status,
        _extract_location_from_page_text, _resolve_zip_coords,
        _resolve_city_coords, _distance_miles,
    )

    if not prefiltered:
        return 0

    write_status(user, test_mode, "running",
                 f"Enriching {len(prefiltered)} jobs with full descriptions...")
    enriched = 0
    failed = 0
    skipped = 0
    removed_by_loc = 0

    for idx, job in enumerate(prefiltered):
        url = job.get("url", "")
        if not url:
            job["full_fetch"] = "no_url"
            _pipeline_check(job, "page_location", "Enriched page location",
                            "not_evaluated", "No posting URL was available")
            skipped += 1
            continue

        # Direct priority-employer adapters already fetched the canonical job
        # page under the DNS-pinned JobSearch user agent. Reuse that text for
        # the existing classification/location re-check rather than fetching
        # the page a second time through the generic provider extractor.
        full_text = job.get("full_description")
        if full_text:
            job["full_fetch"] = "source_provided"
        else:
            full_text = _fetch_full_description(job)
        if full_text:
            job["full_description"] = full_text
            # Re-classify with enriched data
            old_status = job.get("remote_status", "unclear")
            job["remote_status"] = _classify_remote_status(job, use_full=True)
            enriched += 1
            if old_status != job["remote_status"]:
                print(f"DEBUG: [RECLASSIFIED] '{job.get('title', 'N/A')}' "
                      f"{old_status} -> {job['remote_status']}")

            # Location re-check: only for non-remote jobs
            if job.get("remote_status") != "remote":
                loc_result = _extract_location_from_page_text(full_text)
                if loc_result is not None:
                    kind = loc_result[0]
                    page_coords = None
                    if kind == "zip":
                        page_coords = _resolve_zip_coords(loc_result[1])
                        loc_label = f"ZIP {loc_result[1]}"
                    elif kind == "city":
                        page_coords = _resolve_city_coords(loc_result[1], loc_result[2])
                        loc_label = f"{loc_result[1]}, {loc_result[2]}"

                    if page_coords is not None:
                        center_lat, center_lon = _resolve_zip_coords(cfg.get("zip_code", "18080"))
                        page_dist = _distance_miles(
                            center_lat, center_lon, page_coords[0], page_coords[1]
                        )
                        job["distance_miles"] = round(page_dist, 1)
                        radius = float(cfg.get("radius_miles", 50))
                        # Update coords and displayed location to page-extracted values
                        job["latitude"] = page_coords[0]
                        job["longitude"] = page_coords[1]
                        job["location"] = loc_label
                        job["location_override"] = "page"

                        if page_dist > radius:
                            _pipeline_check(
                                job, "page_location", "Enriched page location", "fail",
                                f"Posting page location {loc_label} is {page_dist:.1f} miles away, outside {radius:g}",
                                value=round(page_dist, 1), threshold=radius)
                            print(
                                f"DEBUG: [LOC-REFILTER] '{job.get('title','N/A')}' "
                                f"page location '{loc_label}' is {page_dist:.1f}mi "
                                f"(radius={radius}mi) — REMOVING"
                            )
                            job["_remove"] = True
                            job["_decision"]["outcome"] = "page_location"
                            job["_decision"]["stage_reached"] = "enrichment"
                            removed_by_loc += 1
                        else:
                            _pipeline_check(
                                job, "page_location", "Enriched page location", "pass",
                                f"Posting page location {loc_label} is {page_dist:.1f} miles away, within {radius:g}",
                                value=round(page_dist, 1), threshold=radius)
                            print(
                                f"DEBUG: [LOC-OK] '{job.get('title','N/A')}' "
                                f"page location '{loc_label}' is {page_dist:.1f}mi — OK"
                            )
                    else:
                        _pipeline_check(job, "page_location", "Enriched page location",
                                        "not_evaluated",
                                        "A page location was found but could not be geocoded")
                else:
                    _pipeline_check(job, "page_location", "Enriched page location",
                                    "not_evaluated",
                                    "No reliable location was found in the full posting page")
            else:
                _pipeline_check(job, "page_location", "Enriched page location",
                                "not_applicable",
                                "Full posting remained classified as remote")
        else:
            _pipeline_check(job, "page_location", "Enriched page location",
                            "not_evaluated", "The full posting page could not be fetched")
            failed += 1

        if not job.get("_remove"):
            job["_decision"]["stage_reached"] = "scoring"
            job["_decision"]["outcome"] = "passed_enrichment"

        # Be polite between fetches
        time.sleep(0.5)

        # Progress update every 5 jobs
        if (idx + 1) % 5 == 0:
            write_status(user, test_mode, "running",
                         f"Enriching... {idx + 1}/{len(prefiltered)} jobs fetched")

    if removed_by_loc:
        prefiltered[:] = [j for j in prefiltered if not j.get("_remove")]
        print(f"DEBUG: [LOC-REFILTER] Removed {removed_by_loc} jobs by page location re-check")

    summary = f"Enriched {enriched}/{len(prefiltered)} jobs"
    apply_count = sum(1 for j in prefiltered if j.get("apply_url"))
    if apply_count:
        summary += f" ({apply_count} apply URLs found)"
    if failed:
        summary += f" ({failed} fetch failures)"
    if skipped:
        summary += f" ({skipped} no URL)"
    if removed_by_loc:
        summary += f" ({removed_by_loc} removed by location re-check)"
    print(summary)
    write_status(user, test_mode, "running", summary)
    return removed_by_loc


# === MAIN ===
def main():
    # --- usage ---
    if len(sys.argv) < 2:
        print("Usage: python3 main.py <UserName> [--test-mode]")
        sys.exit(1)

    user = sys.argv[1]
    test_mode = "--test-mode" in sys.argv

    base_dir = os.path.dirname(os.path.abspath(__file__))
    user_dir = os.path.join(base_dir, "configs", user)

    # --- paths ---
    # SQLite is canonical for profile settings and resumes.  The legacy user
    # directory remains only for run status and DEV test fixtures during this
    # migration stage.
    try:
        cfg = load_profile_config(user, test_mode) or {}
        if test_mode:
            active_cfg = load_profile_config(user, False) or {}
            for field in ("active_search_preset_name", "active_search_preset_id",
                          "active_search_preset_color"):
                if active_cfg.get(field) is not None:
                    cfg[field] = active_cfg[field]
        resume_text = load_resume(user)
    except ValueError as exc:
        print(f"Profile data unavailable for {user}: {exc}")
        sys.exit(1)

    # --- Detect environment ---
    # Prefer JOBSEARCH_ENV (set by config.py / the Flask app that spawns this
    # as a subprocess); fall back to the /volume1/Web/JobSearch[_dev] dirname
    # for standalone invocation. The old "/DEV" substring check predates the
    # re-platform and no longer matches any real path.
    if os.environ.get("JOBSEARCH_ENV") == "production":
        environment = "PROD"
    elif os.environ.get("JOBSEARCH_ENV") == "development":
        environment = "DEV"
    elif os.path.basename(base_dir).endswith("_dev"):
        environment = "DEV"
    else:
        environment = "PROD"
    os.environ["JOB_SEARCH_ENV"] = environment
    print(f"Environment detected: {environment}")

    # --- Output directory ---
    # SQLite (save_run_results) is canonical; this static HTML is the legacy
    # dual-write, kept only until Flask templates replace html_output.py.
    # It deliberately writes INSIDE the app now: the old destination was
    # /volume1/Web/johnmfritsch/JobSearch/<user>/index.html, which is the LIVE
    # production page tree — running this from the new app would have
    # overwritten real users' result pages.
    web_base = os.path.join(base_dir, "legacy_output")
    output_dir = os.path.join(web_base, user)
    os.makedirs(output_dir, exist_ok=True)
    print(f"{environment}: legacy HTML output to {output_dir}")

    # --- set test/normal output path ---
    output_html = os.path.join(output_dir, "index_test.html" if test_mode else "index.html")

    start_time = time.time()

    # --- config values ---
    match_threshold = cfg.get("match_threshold", 0.85)
    sources = cfg.get("sources", {})

    if not resume_text.strip():
        print(f"Resume is empty for {user} -- exiting.")
        sys.exit(1)

    base_query = " OR ".join(cfg.get("keywords", []))
    location = "Remote/Anywhere" if cfg.get("remote", False) else cfg.get("location", "US")
    print(f"Running job search for {user}: {base_query} in {location}")
    write_status(user, test_mode, "running", f"Starting job search for {user}")

    source_counts = {}
    jobs = []
    active_sources = [src for src, enabled in sources.items() if enabled]
    print(f"Active sources: {', '.join(active_sources)}")
    if active_sources:
        write_status(user, test_mode, "running", f"Active sources: {', '.join(active_sources)}")
    else:
        write_status(user, test_mode, "running", "No API sources enabled")

    # --- TEST MODE (no API calls) ---
    if test_mode:
        stub_path = os.path.join(user_dir, "test_job_data.json")
        print(f"TEST MODE ENABLED -- loading {stub_path}")
        if not os.path.exists(stub_path):
            # SQLite-native profiles have no legacy directory.  Use a tiny,
            # deterministic no-credit sample so their real filters and resume
            # matching can be exercised without network/API calls.
            jobs = [
                {"title": "Sample Remote Software Engineer", "company": "JobSearch Demo", "location": "Remote, United States", "remote_status": "remote", "description": "Python automation cloud infrastructure API development", "source": "Built-in test", "url": "", "_search_keywords": [], "_search_modes": ["test"], "_sources": ["Built-in test"]},
                {"title": "Sample Data Analyst", "company": "JobSearch Demo", "location": "Remote, United States", "remote_status": "remote", "description": "SQL reporting analytics dashboards stakeholder communication", "source": "Built-in test", "url": "", "_search_keywords": [], "_search_modes": ["test"], "_sources": ["Built-in test"]},
                {"title": "Sample Operations Coordinator", "company": "JobSearch Demo", "location": "Allentown, PA", "remote_status": "local", "description": "Operations process improvement project coordination", "source": "Built-in test", "url": "", "_search_keywords": [], "_search_modes": ["test"], "_sources": ["Built-in test"]},
            ]
            print(f"No legacy fixture for {user}; using {len(jobs)} built-in no-credit test jobs")
        else:
            with open(stub_path, "r", encoding="utf-8") as f:
                jobs = json.load(f)
        print(f"Loaded {len(jobs)} test jobs for {user}")
        write_status(user, test_mode, "running", f"Loaded {len(jobs)} no-credit test jobs for {user}")
    else:
        # --- Dual-pass search: local + remote ---
        search_remote = cfg.get("search_remote", cfg.get("remote", False))
        search_local = cfg.get("search_local", not cfg.get("remote", False))

        if not search_remote and not search_local:
            print("WARNING: Neither search_remote nor search_local enabled")
            write_status(user, test_mode, "error", "No search mode selected (enable search_remote and/or search_local)")
            sys.exit(1)

        mode_desc = []
        if search_local:
            mode_desc.append("LOCAL")
        if search_remote:
            mode_desc.append("REMOTE")
        write_status(user, test_mode, "running", f"Search mode: {' + '.join(mode_desc)}")

        scraper_list = [
            ("serpapi", "fetch_jobs_serpapi", "serpapi_scraper"),
            ("adzuna", "fetch_jobs_adzuna", "adzuna_scraper"),
            ("indeed", "fetch_jobs_indeed", "indeed_scraper"),
        ]

        for source_name, func_name, module_name in scraper_list:
            if not sources.get(source_name, False):
                continue

            mod = __import__(module_name)
            fetch_func = getattr(mod, func_name)

            if search_local:
                write_status(user, test_mode, "running", f"Fetching LOCAL {source_name} jobs...")
                local_cfg = dict(cfg)
                local_cfg["remote"] = False
                local_pages_key = f"{source_name}_local_max_pages"
                if local_pages_key in cfg:
                    local_cfg[f"{source_name}_max_pages"] = cfg[local_pages_key]
                try:
                    jobs += fetch_func(local_cfg, base_query, source_counts, user=user, test_mode=test_mode)
                except Exception as e:
                    print(f"DEBUG: {source_name} local fetch failed: {e}")

            if search_remote:
                write_status(user, test_mode, "running", f"Fetching REMOTE {source_name} jobs...")
                remote_cfg = dict(cfg)
                remote_cfg["remote"] = True
                try:
                    jobs += fetch_func(remote_cfg, base_query, source_counts, user=user, test_mode=test_mode)
                except Exception as e:
                    print(f"DEBUG: {source_name} remote fetch failed: {e}")

        # Priority employers use a per-record dispatcher rather than the
        # fixed provider tuple above. They intentionally run after the normal
        # provider passes and before the common deduplication/filter pipeline.
        from employer_source_scraper import fetch_employer_jobs
        for employer in active_employer_sources(user, cfg):
            label = employer.get("company_name", "Priority employer")
            write_status(user, test_mode, "running", f"Checking priority employer: {label}...")
            try:
                employer_jobs = fetch_employer_jobs(
                    employer, cfg, source_counts, user=user, test_mode=test_mode)
                jobs += employer_jobs
                record_employer_source_status(user, employer["id"], True)
                write_status(user, test_mode, "running",
                             f"Priority employer {label}: {len(employer_jobs)} job(s) found")
            except Exception as e:
                message = str(e)[:500] or "Could not check this priority employer"
                print(f"DEBUG: priority employer {label} fetch failed: {message}")
                record_employer_source_status(user, employer["id"], False, message)
                write_status(user, test_mode, "running",
                             f"Priority employer warning for {label}: {message}")

    fetched_total = len(jobs)
    print(f"Fetched {fetched_total} jobs{' (test data)' if test_mode else ''}")
    write_status(user, test_mode, "running", f"Fetched {fetched_total} jobs, processing...")

    # --- dedup ---
    if jobs:
        jobs = deduplicate_jobs(jobs)
        write_status(user, test_mode, "running", "Removing duplicate jobs...")
    after_dedup = len(jobs)
    audited_jobs = list(jobs)

    # The ignore list holds jobs the user dismissed AND jobs the user added to
    # the tracker by hand -- both are already decided, so re-presenting them as
    # fresh matches is noise. Keep them in the decision audit, but do not
    # enrich, score, or publish them.
    ignored_keys = load_ignored_job_keys(user)
    searchable_jobs = []
    ignored_count = 0
    for job in jobs:
        if any(stable_job_key(identity) in ignored_keys
               for identity in _job_identities(job)):
            _pipeline_check(
                job, "ignore_list", "Ignore list", "fail",
                "Already decided: dismissed or added to the tracker by hand")
            decision = job.setdefault("_decision", {"checks": []})
            decision["stage_reached"] = "ignore_list"
            decision["outcome"] = "ignore_list"
            ignored_count += 1
        else:
            _pipeline_check(
                job, "ignore_list", "Ignore list", "pass",
                "Job is not on the ignore list")
            searchable_jobs.append(job)
    jobs = searchable_jobs
    after_ignore_list = len(jobs)
    if ignored_count:
        write_status(user, test_mode, "running",
                     f"Ignored {ignored_count} previously dismissed job(s)")

    # Filter jobs with progress updates
    write_status(user, test_mode, "running", f"Filtering {len(jobs)} jobs...")
    prefiltered = []
    for idx, j in enumerate(jobs):
        passed, _ = evaluate_simple_filters(j, cfg)
        if passed:
            prefiltered.append(j)
        # Update status every 100 jobs
        if (idx + 1) % 100 == 0:
            write_status(user, test_mode, "running", f"Filtering... {idx + 1}/{len(jobs)} jobs processed, {len(prefiltered)} passed so far")
    after_keyword_filter = len(prefiltered)
    print(f"{after_keyword_filter} jobs passed keyword filters")
    write_status(user, test_mode, "running", f"{after_keyword_filter} jobs passed keyword filters")

    if not prefiltered:
        print("No jobs matched criteria. Generating empty results page.")
        write_status(user, test_mode, "running", "No jobs matched criteria; preparing the empty result and decision report")
        # Generate empty results page
        runtime = time.time() - start_time
        write_status(user, test_mode, "running", "Generating HTML report...")
        pipeline_stats = {
            "fetched_total": fetched_total,
            "after_dedup": after_dedup,
            "after_ignore_list": after_ignore_list,
            "ignored_count": ignored_count,
            "after_keyword_filter": 0,
            "after_loc_recheck": 0,
            "after_scoring": 0,
            "source_fetched": dict(source_counts),
            "outcomes": dict(Counter(
                j.get("_decision", {}).get("outcome", "unknown")
                for j in audited_jobs)),
        }
        write_results_html([], output_html, cfg, user=user, runtime_seconds=runtime, source_counts={}, pipeline_stats=pipeline_stats, test_mode=test_mode)
        run_id = save_run_results(user, test_mode, [], cfg)
        save_run_audit(user, run_id, cfg, audited_jobs, pipeline_stats)
        write_status(user, test_mode, "complete", "No jobs found matching criteria")
        print(f"Complete -- 0 matches written to {output_html}")
        return

    # --- Enrich filtered jobs with full page descriptions ---
    removed_by_loc = enrich_jobs_with_full_pages(prefiltered, user, test_mode, cfg)
    after_loc_recheck = len(prefiltered)

    scored = score_jobs_against_resume(cfg, prefiltered, resume_text, user=user, test_mode=test_mode)
    write_status(user, test_mode, "running", f"Scoring {len(prefiltered)} jobs against resume...")
    matches = [s for s in scored if s["score"] >= match_threshold]

    for job in scored:
        audit = job.setdefault("_decision", {"checks": []})
        score = job.get("score", 0.0)
        details = job.get("match_details", {})
        passed = score >= match_threshold
        _pipeline_check(
            job, "resume_score", "Resume match threshold",
            "pass" if passed else "fail",
            f"Final score {score:.3f} {'meets' if passed else 'is below'} {match_threshold:.3f}",
            value=score, threshold=match_threshold,
            resume_similarity=details.get("resume_similarity"),
            boost_points=details.get("boost_points"))
        audit["stage_reached"] = "complete"
        audit["matched_keywords"] = job.get("matches", {}).get("keywords", [])
        audit["matched_boost_terms"] = job.get("matches", {}).get("boost_terms", [])
        audit["outcome"] = "included" if passed else "resume_score"

    # Log per-job scores so the user can see what passed/failed the threshold
    scored_sorted = sorted(scored, key=lambda s: s["score"], reverse=True)
    for job in scored_sorted:
        title = job.get("title", "Unknown")[:60]
        score = job.get("score", 0.0)
        passed = score >= match_threshold
        status_icon = "PASS" if passed else "FAIL"
        write_status(user, test_mode, "running",
                     f"[SCORE] {status_icon} {score:.3f} (threshold {match_threshold}) — {title}")

    print(f"Wrote {len(matches)} matches to {output_html}")

    match_counts = Counter([job.get("source", "Unknown") for job in matches])
    total_counts = Counter(source_counts) + match_counts

    runtime = time.time() - start_time

    pipeline_stats = {
        "fetched_total": fetched_total,
        "after_dedup": after_dedup,
        "after_ignore_list": after_ignore_list,
        "ignored_count": ignored_count,
        "after_keyword_filter": after_keyword_filter,
        "after_loc_recheck": after_loc_recheck,
        "after_scoring": len(matches),
        "source_fetched": dict(source_counts),
        "outcomes": dict(Counter(
            j.get("_decision", {}).get("outcome", "unknown")
            for j in audited_jobs)),
    }

    write_status(user, test_mode, "running", "Generating HTML report...")
    write_results_html(matches, output_html, cfg, user=user, runtime_seconds=runtime, source_counts=total_counts, pipeline_stats=pipeline_stats, test_mode=test_mode)
    run_id = save_run_results(user, test_mode, matches, cfg)
    save_run_audit(user, run_id, cfg, audited_jobs, pipeline_stats)
    print(f"Complete -- {len(matches)} matches written to {output_html}")
    write_status(user, test_mode, "complete", f"Job search complete! Found {len(matches)} matches.")


# === ENTRY POINT ===
if __name__ == "__main__":
    main()
