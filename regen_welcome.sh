#!/usr/local/AppCentral/entware/opt/bin/bash
# regen_welcome.sh — Regenerate welcome pages for all DEV users using new static asset format

BASE_DIR="/volume1/util/job_search_agent/DEV"
ENV="DEV"
WEB_BASE="/volume1/Web/johnmfritsch/JobSearch/DEV"
STATIC_BASE="/JobSearch/DEV/static"
CONFIGS_DIR="$BASE_DIR/configs"

generate_welcome_page() {
    local username="$1"
    local web_dir="$WEB_BASE/$username"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

    local files_to_write=("$web_dir/index.html" "$web_dir/index_test.html")

    local config_file="$CONFIGS_DIR/$username/config.json"
    local config_json
    config_json="$(cat "$config_file" 2>/dev/null || echo '{}')"

    mkdir -p "$web_dir"

    for target_file in "${files_to_write[@]}"; do

        cat > "$target_file" << CSSBLOCK
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Job Search Results</title>
<link rel="stylesheet" href="${STATIC_BASE}/modal.css">
CSSBLOCK

        cat >> "$target_file" << BODYHTML
</head>
<body>

<!-- Top Frame -->
<div id="top-frame">
  <h1>Job Search Results (0)</h1>
  <div class="timestamp">Generated ${timestamp}</div>

  <div class="welcome-container">
    <div class="welcome-box">
      <h2>Welcome, ${username}!</h2>
      <p>Start with a short setup, then run a no-credit test before your first live search.</p>
      <ol class="readiness-checklist"><li><strong>Resume:</strong> add a current, text-based resume.</li><li><strong>Preferences:</strong> confirm target roles, location, work style, and salary.</li><li><strong>Sources:</strong> review sources only if you need to adjust coverage.</li><li><strong>Test:</strong> run a no-credit test search first.</li><li><strong>Live search:</strong> review the estimate, then run when ready.</li></ol>
      <button type="button" onclick="openEditRunModal()">Start setup</button>
    </div>
  </div>

  <table id="jobTable" data-sort-dir="asc">
    <tr>
      <th>Title</th>
      <th>Remote</th>
      <th>Company</th>
      <th>Location</th>
      <th>Source</th>
      <th>Salary</th>
      <th>Score</th>
    </tr>
  </table>
</div>

<div id="bottom-frame">
  <div class="legend">
    &#x2705; Remote &nbsp;|&nbsp; H Hybrid &nbsp;|&nbsp; &#x2753; Unclear / PA-based &nbsp;|&nbsp; (blank) Local/On-site
  </div>
  <div class="footer">
    No search results yet &mdash; click Edit / Run to get started
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
  <div id="modal-content"></div>

  <div id="progress-section" style="display: none;">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px; border-radius: 8px; margin: 10px 0;">
      <h3 style="margin: 0 0 10px 0;">&#x1F50D; Job Search Progress</h3>
      <div>User: <strong>${username}</strong> | Status: <span id="job-status">Ready</span></div>
    </div>
    <div style="padding: 10px; margin: 10px 0; background: #f8f9fa; border-radius: 4px;">
      <button id="startJobBtn" onclick="startJobSearch()" style="background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; margin-right: 10px;">&#x25B6; Start Job Search</button>
      <button onclick="hideProgressSection()" style="background: #6c757d; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer;">&#x25C0; Back to Config</button>
    </div>
    <div id="job-output" style="background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 4px; font-family: 'Courier New', monospace; font-size: 13px; max-height: 400px; overflow-y: auto; white-space: pre-wrap;">Waiting for job to start...</div>
  </div>

  <div id="modal-bottom">
    <div class="bottom-toprow">
      <button id="recalcBtn" onclick="calculateSerpApiCost()">&#x1F4B0; Recalculate SerpAPI Cost</button>
      <div id="serpapiCost">Estimated SerpAPI usage: pending...</div>
      <div id="serpapiRemaining">SerpAPI Requests Remaining: ...</div>
    </div>
    <div class="bottom-buttonrow">
      <button id="loadPrevBtn" title="Load config.json.prev (if present)">&#x1F50E; Load Previous</button>
      <button id="undoBtn" title="Revert unsaved edits in this window">&#x21A9;&#xFE0F; Undo Changes</button>
      <button id="saveBtn" title="Save the parameters shown above">&#x1F4BE; Save</button>
      <button id="runBtn" class="green-btn" title="Run the job search now">Run Search</button>
    </div>
  </div>

  <div class="resize-handle resize-n"></div>
  <div class="resize-handle resize-s"></div>
  <div class="resize-handle resize-e"></div>
  <div class="resize-handle resize-w"></div>
  <div class="resize-handle resize-ne"></div>
  <div class="resize-handle resize-nw"></div>
  <div class="resize-handle resize-se"></div>
  <div class="resize-handle resize-sw"></div>
</div>
BODYHTML

        cat >> "$target_file" << CONFIGJS
<script>
const API_BASE = '/api.php';
const API_ENV = '${ENV}';
const userName = "${username}";
const isTestMode = window.location.pathname.includes('index_test.html');
const JOB_STATUS_STORAGE_KEY = 'jobsearch_job_status_' + userName;
const configData = ${config_json};
</script>
CONFIGJS

        cat >> "$target_file" << STATICJS
<script src="${STATIC_BASE}/jobsearch.js"></script>

</body>
</html>
STATICJS

        echo "  Written: $target_file"
    done
}

echo "Regenerating DEV welcome pages..."
for user_dir in "$CONFIGS_DIR"/*/; do
    [ -d "$user_dir" ] || continue
    username="$(basename "$user_dir")"
    echo "Processing: $username"
    generate_welcome_page "$username"
done
echo "Done."
