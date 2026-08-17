# html_output.py

import os
if "/DEV" in os.path.dirname(os.path.abspath(__file__)) or os.path.dirname(os.path.abspath(__file__)).endswith("/DEV"):
  environment = "DEV"
else:
  environment = "PROD"
os.environ["JOB_SEARCH_ENV"] = environment
static_base = "/JobSearch/DEV/static" if environment == "DEV" else "/JobSearch/static"
static_dir = "/volume1/Web/johnmfritsch/JobSearch/DEV/static" if environment == "DEV" else "/volume1/Web/johnmfritsch/JobSearch/static"
from datetime import datetime
import html
import hashlib
import json
import re
from urllib.parse import urlsplit


def _clean_text(value, limit=6000):
    """Collapse fetched descriptions into a safe, bounded detail-panel excerpt."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _job_id(job):
    """Stable across reruns when the source posting URL remains the same."""
    raw_url = str(job.get("url") or job.get("apply_url") or "")
    parsed = urlsplit(raw_url)
    canonical_url = f"{parsed.netloc.lower()}{parsed.path}" if parsed.netloc else raw_url
    identity = "|".join([
        str(job.get("source") or "").lower(), canonical_url.lower(),
        str(job.get("title") or "").strip().lower(), str(job.get("company") or "").strip().lower(),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _matched_terms(job, cfg):
    """Return direct evidence only; semantic similarity itself remains a score, not a claim."""
    text = " ".join([
        str(job.get("title") or ""), str(job.get("description") or ""),
        str(job.get("full_description") or ""),
    ]).lower()
    def found(terms):
        return [str(term) for term in terms if str(term).strip() and str(term).lower() in text][:8]
    return {
        "keywords": found((cfg or {}).get("keywords", [])),
        "boost_terms": found((cfg or {}).get("boost_terms", [])),
    }


def _detail_payload(job, cfg):
    job_id = _job_id(job)
    description = _clean_text(job.get("full_description") or job.get("description") or job.get("snippet"))
    posted = job.get("posted_at") or job.get("date_posted") or job.get("created") or ""
    employment_type = job.get("employment_type") or job.get("job_type") or job.get("contract_type") or ""
    return job_id, {
        "id": job_id,
        "title": _clean_text(job.get("title"), 500),
        "company": _clean_text(job.get("company"), 500),
        "location": _clean_text(job.get("location"), 500),
        "source": _clean_text(job.get("source"), 200),
        "salary": _clean_text(job.get("salary"), 200),
        "remote_status": _clean_text(job.get("remote_status"), 100),
        "posted": _clean_text(posted, 200),
        "employment_type": _clean_text(employment_type, 200),
        "distance_miles": job.get("distance_miles"),
        "score": round(float(job.get("score", 0)), 2),
        "scoring_method": str((cfg or {}).get("scoring_method", "semantic")),
        "match_threshold": (cfg or {}).get("match_threshold"),
        "matches": _matched_terms(job, cfg),
        "description": description,
        "url": str(job.get("url") or ""),
        "apply_url": str(job.get("apply_url") or ""),
    }


def write_results_html(results, output_path, cfg=None, user=None, runtime_seconds=None, source_counts=None, pipeline_stats=None, test_mode=False):
    """
    Job Search Agent results page with:
      • Two-frame main layout (top scrollable results, bottom fixed footer)
      • 'Edit / Run' modal: draggable, resizable from every edge/corner, remembers position+size
          – Top: scrollable parameter editor (all keys rendered)
          – Bottom: fixed bar with cost + buttons (Load Previous, Undo, Save, Run)
          – Tooltips per parameter
          – Checkboxes aligned with the input column
          – SerpAPI cost estimator (generic: uses onscreen values)
          – Displays SerpAPI Requests Remaining (fetched once when modal opens)
    CSS and JS served from /JobSearch/static/ (PROD) or /JobSearch/DEV/static/ (DEV).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_results = len(results)
    cfg_json = json.dumps(cfg or {}, indent=2, ensure_ascii=False)
    job_details = {}
    for job in results:
        job_id, detail = _detail_payload(job, cfg or {})
        job_details[job_id] = detail
    job_details_json = json.dumps(job_details, ensure_ascii=False).replace("</", "<\\/")
    user_name = user or "Unknown"
    try:
        asset_version = str(int(max(
            os.path.getmtime(os.path.join(static_dir, "modal.css")),
            os.path.getmtime(os.path.join(static_dir, "jobsearch.js")),
        )))
    except OSError:
        asset_version = "1"

    html_output = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Search Results</title>
<link rel="stylesheet" href="{static_base}/modal.css?v={asset_version}">
<style>
.pipeline-summary {{ margin-top: 4px; font-size: 12px; color: #555; }}
.pipeline-title {{ font-weight: 600; color: #333; font-size: 13px; }}
.pipeline-table {{ border-collapse: collapse; margin-top: 6px; font-size: 12px; width: auto; }}
.pipeline-table th {{ background: #2c3e50; color: #fff; padding: 4px 10px; text-align: center; font-weight: 600; }}
.pipeline-table td {{ padding: 3px 10px; text-align: center; border-bottom: 1px solid #e0e0e0; }}
.pipeline-table td:first-child {{ text-align: left; font-weight: 500; color: #333; }}
.pipeline-table tr:nth-child(even) td {{ background: #f7f9fc; }}
.pipeline-table tr.final-row td {{ background: #eaf4ea; font-weight: 600; }}
.pipeline-table .pct {{ color: #888; font-size: 11px; margin-left: 2px; }}
</style>

<script>
const API_BASE = '/api.php';
const API_ENV = '{environment}';
const userName = "{user_name}";
const isTestMode = window.location.pathname.includes('index_test.html');
const JOB_STATUS_STORAGE_KEY = 'jobsearch_job_status_' + userName;
const configData = {cfg_json};
const jobDetails = {job_details_json};
</script>
</head>
<body>
<div id="top-frame">
  <div class="results-heading"><h1>Job Search Results ({total_results})</h1><button type="button" class="application-tracker-launch" onclick="openApplicationTracker()">Application tracker</button></div>
  <div class="timestamp">Generated {timestamp}</div>
  <section class="result-filters" aria-label="Filter job results">
    <div class="result-filter-row">
      <label>Work style <select id="filterWorkStyle" onchange="applyResultFilters()"><option value="">Any</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="local">Local / on-site</option></select></label>
      <label>Minimum score <select id="filterMinimumScore" onchange="applyResultFilters()"><option value="">Any</option><option value="0.7">0.70+</option><option value="0.5">0.50+</option></select></label>
      <label>Salary <select id="filterSalary" onchange="applyResultFilters()"><option value="">Any</option><option value="listed">Listed</option><option value="100000">$100k+</option></select></label>
      <label>Source <select id="filterSource" onchange="applyResultFilters()"><option value="">Any</option></select></label>
      <label>Application <select id="filterApplicationState" onchange="applyResultFilters()"><option value="">Any</option><option value="saved">Saved</option><option value="applied">Applied / tracking</option><option value="unsaved">Not saved</option></select></label>
    </div>
    <div class="result-view-row"><span id="filterResultCount" aria-live="polite"></span><select id="savedResultViews" onchange="applySavedResultView(this.value)"><option value="">Saved views…</option></select><button type="button" class="filter-secondary" onclick="saveCurrentResultView()">Save this view</button><button type="button" class="filter-secondary" onclick="clearResultFilters()">Clear filters</button></div>
  </section>

  <table id="jobTable" data-sort-dir="asc">
    <tr>
      <th onclick="sortTable(0)">Title</th>
      <th onclick="sortTable(1)">Remote</th>
      <th onclick="sortTable(2)">Company</th>
      <th onclick="sortTable(3)">Location</th>
      <th onclick="sortTable(4)">Source</th>
      <th onclick="sortTable(5)">Salary</th>
      <th onclick="sortTable(6)">Score ▼</th>
    </tr>
"""
    for job in results:
        job_id = _job_id(job)
        safe_job_id = html.escape(job_id, quote=True)
        title = html.escape(job.get("title", ""))
        company = html.escape(job.get("company", ""))
        apply_url = job.get("apply_url", "")
        if apply_url:
            safe_apply = html.escape(apply_url, quote=True)
            company_cell = f'<a href="{safe_apply}" target="_blank" class="company-link" title="View on company site">{company}</a>'
        else:
            company_cell = company
        location = html.escape(job.get("location", ""))
        source = html.escape(job.get("source", ""))
        salary = html.escape(str(job.get("salary", "")))
        score = float(job.get("score", 0))
        url = job.get("url", "")
        remote_status = job.get("remote_status", "")

        # Score color
        if score >= 0.7:
            score_class = "green"
        elif score >= 0.5:
            score_class = "yellow"
        else:
            score_class = "red"

        # Remote indicator
        if remote_status == "remote":
            remote_html = '<span style="color:green;" title="Remote position">✅</span>'
        elif remote_status == "hybrid":
            remote_html = '<span style="color:#007BFF;" title="Hybrid work">H</span>'
        elif remote_status == "unclear":
            remote_html = '<span style="color:#333;" title="Unclear / PA-based">❓</span>'
        else:
            remote_html = ""

        title_html = f'<a href="{html.escape(url, quote=True)}" target="_blank">{title}</a>' if url else title

        html_output += f"""
    <tr data-job-id="{safe_job_id}">
      <td data-label="Role"><div class="job-title-cell"><span>{title_html}</span><button type="button" class="job-details-btn" data-job-id="{safe_job_id}" onclick="openJobDetails('{safe_job_id}')" aria-label="View details for {title}">Details</button></div></td>
      <td data-label="Work style" class="remote">{remote_html}</td>
      <td data-label="Company">{company_cell}</td>
      <td data-label="Location">{location}</td>
      <td data-label="Source">{source}</td>
      <td data-label="Salary">{salary}</td>
      <td data-label="Match score"><span class="score {score_class}">{score:.2f}</span></td>
    </tr>
"""

    runtime_line = f"&nbsp;|&nbsp; ⏱ {runtime_seconds:.0f}s" if runtime_seconds else ""

    # --- Build pipeline funnel table ---
    ps = pipeline_stats or {}
    source_fetched = ps.get("source_fetched", {})
    fetched_total = ps.get("fetched_total", sum(source_fetched.values()) if source_fetched else "—")
    after_dedup = ps.get("after_dedup", "—")
    after_keyword_filter = ps.get("after_keyword_filter", "—")
    after_loc_recheck = ps.get("after_loc_recheck", "—")
    after_scoring = ps.get("after_scoring", total_results)

    # Source columns: only sources that returned > 0 jobs
    active_srcs = [s for s, c in source_fetched.items() if c > 0]

    def pct(n, total):
        try:
            return f"<span class='pct'>({int(n)/int(total)*100:.0f}%)</span>" if int(total) > 0 else ""
        except (TypeError, ValueError, ZeroDivisionError):
            return ""

    # Build header cells
    src_header_cells = "".join(f"<th>{s.capitalize()}</th>" for s in active_srcs)
    # Build fetched row (per-source available, others N/A)
    src_fetch_cells = "".join(
        f"<td>{source_fetched.get(s, '—'):,}</td>" for s in active_srcs
    )
    # Subsequent stages are post-merge — show em-dash per source
    src_dash_cells = "".join("<td>—</td>" for _ in active_srcs)

    def fmt(n):
        try:
            return f"{int(n):,}"
        except (TypeError, ValueError):
            return str(n)

    funnel_table = f"""
<table class="pipeline-table">
  <thead>
    <tr>
      <th>Pipeline Stage</th>
      {src_header_cells}
      <th>Total</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Fetched</td>
      {src_fetch_cells}
      <td><strong>{fmt(fetched_total)}</strong></td>
    </tr>
    <tr>
      <td>After Dedup</td>
      {src_dash_cells}
      <td><strong>{fmt(after_dedup)}</strong> {pct(after_dedup, fetched_total)}</td>
    </tr>
    <tr>
      <td>After Keyword Filter</td>
      {src_dash_cells}
      <td><strong>{fmt(after_keyword_filter)}</strong> {pct(after_keyword_filter, after_dedup)}</td>
    </tr>
    <tr>
      <td>After Location Re-check</td>
      {src_dash_cells}
      <td><strong>{fmt(after_loc_recheck)}</strong> {pct(after_loc_recheck, after_keyword_filter)}</td>
    </tr>
    <tr class="final-row">
      <td>After Semantic Score</td>
      {src_dash_cells}
      <td><strong>{fmt(after_scoring)}</strong> {pct(after_scoring, after_loc_recheck)}</td>
    </tr>
  </tbody>
</table>"""

    html_output += f"""
  </table>
</div> <!-- end top-frame -->

<aside id="jobDetailsPanel" class="job-details-panel" aria-label="Job details" aria-hidden="true">
  <div class="job-details-header">
    <div><p id="jobDetailsEyebrow" class="job-details-eyebrow"></p><h2 id="jobDetailsTitle">Job details</h2><p id="jobDetailsCompany" class="job-details-company"></p></div>
    <button type="button" class="job-details-close" onclick="closeJobDetails()" aria-label="Close job details">×</button>
  </div>
  <div class="job-details-content">
    <div id="jobDetailsMeta" class="job-details-meta"></div>
    <section><h3>Why it matched</h3><div id="jobDetailsMatch" class="job-details-match"></div></section>
    <section><h3>Job description</h3><p id="jobDetailsDescription" class="job-details-description"></p></section>
  </div>
  <div class="job-details-actions">
    <a id="jobPostingLink" class="job-link-btn secondary" target="_blank" rel="noopener">View posting</a>
    <a id="jobApplyLink" class="job-link-btn primary" target="_blank" rel="noopener">Open application</a>
    <button type="button" id="saveJobBtn" onclick="setJobTriage('saved')">Save</button>
    <button type="button" id="appliedJobBtn" onclick="setJobTriage('applied')">Mark applied</button>
    <button type="button" id="dismissJobBtn" class="danger-btn" onclick="setJobTriage('not_interested')">Not interested</button>
  </div>
</aside>
<aside id="applicationTrackerPanel" class="application-tracker-panel" aria-label="Application tracker" aria-hidden="true">
  <div class="application-tracker-header">
    <div><p class="job-details-eyebrow">Your job search workspace</p><h2>Application tracker</h2><p>Move saved roles forward, keep notes, and see what needs follow-up.</p></div>
    <button type="button" class="job-details-close" onclick="closeApplicationTracker()" aria-label="Close application tracker">×</button>
  </div>
  <div class="application-tracker-content">
    <div id="applicationTrackerSummary" class="application-tracker-summary"></div>
    <div id="applicationTrackerList" class="application-tracker-list"></div>
  </div>
</aside>
<div id="jobTriageToast" class="job-triage-toast" role="status" aria-live="polite"></div>

<div id="bottom-frame">
  <div class="legend">
    ✅ Remote &nbsp;|&nbsp; H Hybrid &nbsp;|&nbsp; ❓ Unclear / PA-based &nbsp;|&nbsp; (blank) Local/On-site
  </div>
  <div class="footer">
    <div class="pipeline-summary">
      <span class="pipeline-title">Pipeline Summary</span> &nbsp;—&nbsp; {user_name}{runtime_line} &nbsp;|&nbsp; Generated {timestamp}
      {funnel_table}
    </div>
  </div>
  <div class="bottom-buttons">
    <button id="editRunBtn" onclick="openEditRunModal()">Edit / Run</button>
  </div>
</div>

<!-- ===== Edit / Run Modal ===== -->
<div id="editModal" class="modal centered" role="dialog" aria-modal="true" aria-label="Edit and Run">
  <div class="modal-header">
    <h2>Edit / Run Configuration</h2>
    <span class="close" onclick="closeEditRunModal()">&times;</span>
  </div>

  <!-- TOP (scrollable parameters) -->
  <div id="modal-content"></div>

  <!-- Progress section (hidden by default) -->
  <div id="progress-section" style="display: none;">
    <div class="run-progress-header"><p>Job search in progress</p><h3 id="job-status">Ready to begin</h3><span id="run-progress-message">Review the estimate, then start when ready.</span></div>
    <ol id="runTimeline" class="run-timeline"><li data-stage="fetch">Fetching</li><li data-stage="dedupe">Deduplicating</li><li data-stage="location">Checking location</li><li data-stage="match">Matching resume</li><li data-stage="publish">Publishing</li></ol>

    <div style="padding: 10px; margin: 10px 0; background: #f8f9fa; border-radius: 4px;">
      <button id="startJobBtn" onclick="startJobSearch()" style="background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; margin-right: 10px;">▶ Start Job Search</button>
      <button id="cancelJobBtn" onclick="cancelJobSearch()" style="background: #6c757d; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer;">◀ Back to Config</button>
    </div>

    <details class="run-technical-details"><summary>Technical details</summary><div id="job-output">Waiting for job to start...</div></details>
  </div>


  <!-- BOTTOM (fixed controls) -->
  <div id="modal-bottom">
    <div class="bottom-toprow">
      <button id="recalcBtn" onclick="calculateSerpApiCost()">💰 Recalculate</button>
      <div id="serpapiCost">Estimated SerpAPI usage: pending…</div>
      <div id="serpapiRemaining">SerpAPI Requests Remaining: …</div>
    </div>

    <div class="bottom-buttonrow">
      <button id="loadPrevBtn" title="Load config.json.prev (if present)">🔎 Load Previous</button>
      <button id="undoBtn" title="Revert unsaved edits in this window">↩️ Undo Changes</button>
      <button id="saveBtn" title="Save the parameters shown above">💾 Save</button>
      <button id="runBtn" class="green-btn" title="Run the job search now (stub)">Run Search</button>
    </div>
  </div>

  <!-- Multi-edge resize handles -->
  <div class="resize-handle resize-n"></div>
  <div class="resize-handle resize-s"></div>
  <div class="resize-handle resize-e"></div>
  <div class="resize-handle resize-w"></div>
  <div class="resize-handle resize-ne"></div>
  <div class="resize-handle resize-nw"></div>
  <div class="resize-handle resize-se"></div>
  <div class="resize-handle resize-sw"></div>
</div>

<script src="{static_base}/jobsearch.js?v={asset_version}"></script>

</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_output)
    print(f"✅ Wrote {total_results} results to {output_path}")
