#!/usr/local/AppCentral/entware/opt/bin/bash
#
# user_maint.sh — User Maintenance Script for Job Search Agent
# Auto-detects DEV vs PROD from script location.
# Usage: ./user_maint.sh
#

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect environment
if echo "$BASE_DIR" | grep -q '/DEV'; then
    ENV="DEV"
    WEB_BASE="/volume1/Web/johnmfritsch/JobSearch/DEV"
    STATIC_BASE="/JobSearch/DEV/static"
else
    ENV="PROD"
    WEB_BASE="/volume1/Web/johnmfritsch/JobSearch"
    STATIC_BASE="/JobSearch/static"
fi

CONFIGS_DIR="$BASE_DIR/configs"
TRASH_DIR="$BASE_DIR/TRASH"
DEFAULT_CONFIG="$BASE_DIR/default_config.json"
DEFAULT_RESUME="$BASE_DIR/default_resume.txt"
IDLE_STATUS='{"state": "idle", "message": "Ready to run"}'

# ─── helpers ────────────────────────────────────────────────────────────────

print_header() {
    echo ""
    echo "============================"
    echo "  Job Search - User Maintenance"
    echo "  Environment: $ENV"
    echo "============================"
    echo ""
}

press_enter() {
    echo ""
    echo -n "Press Enter to continue..."
    read -r _dummy || exit 0
}

# List existing users (directories under configs/)
list_users() {
    local count=0
    for d in "$CONFIGS_DIR"/*/; do
        [ -d "$d" ] || continue
        local name
        name="$(basename "$d")"
        count=$((count + 1))
        echo "  $count. $name"
    done
    if [ "$count" -eq 0 ]; then
        echo "  (no users found)"
    fi
}

# Get array of user names
get_user_list() {
    USER_LIST=()
    for d in "$CONFIGS_DIR"/*/; do
        [ -d "$d" ] || continue
        USER_LIST+=("$(basename "$d")")
    done
}

# ─── generate welcome page ──────────────────────────────────────────────────
#
# Generates an index.html (and index_test.html for DEV) that uses env-aware
# static assets: /JobSearch/DEV/static/ (DEV) or /JobSearch/static/ (PROD).
# Only user-specific globals (configData, userName, API_ENV, etc.) are
# injected inline; all other logic is served from the shared static files.
#

generate_welcome_page() {
    local username="$1"
    local web_dir="$WEB_BASE/$username"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

    local files_to_write=("$web_dir/index.html")
    if [ "$ENV" = "DEV" ]; then
        files_to_write+=("$web_dir/index_test.html")
    fi

    local config_file="$CONFIGS_DIR/$username/config.json"
    local config_json
    config_json="$(cat "$config_file" 2>/dev/null || echo '{}')"

    for target_file in "${files_to_write[@]}"; do

        # ── Part 1: HTML head with shared CSS ──
        cat > "$target_file" << CSSBLOCK
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Job Search Results</title>
<link rel="stylesheet" href="${STATIC_BASE}/modal.css">
CSSBLOCK

        # ── Part 2: Body HTML with dynamic username/timestamp ──
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
      <button id="cancelJobBtn" onclick="cancelJobSearch()" style="background: #6c757d; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer;">&#x25C0; Back to Config</button>
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

        # ── Part 3: Dynamic globals (user-specific JS variables) ──
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

        # ── Part 4: Shared static JS + close tags ──
        cat >> "$target_file" << STATICJS
<script src="${STATIC_BASE}/jobsearch.js"></script>

</body>
</html>
STATICJS
    done
}

# ─── add user ────────────────────────────────────────────────────────────────

do_add_user() {
    echo ""
    echo "--- Add User ---"
    echo ""
    echo -n "Enter new username: "
    read -r username || { echo ""; echo "EOF reached. Exiting."; exit 0; }

    # Validate: non-empty
    if [ -z "$username" ]; then
        echo "ERROR: Username cannot be empty."
        press_enter
        return
    fi

    # Validate: no spaces
    case "$username" in
        *\ *)
            echo "ERROR: Username cannot contain spaces."
            press_enter
            return
            ;;
    esac

    # Validate: not already existing
    user_dir="$CONFIGS_DIR/$username"
    if [ -d "$user_dir" ]; then
        echo "ERROR: User '$username' already exists at $user_dir"
        press_enter
        return
    fi

    # Validate: default templates exist
    if [ ! -f "$DEFAULT_CONFIG" ]; then
        echo "ERROR: default_config.json not found at $DEFAULT_CONFIG"
        press_enter
        return
    fi
    if [ ! -f "$DEFAULT_RESUME" ]; then
        echo "ERROR: default_resume.txt not found at $DEFAULT_RESUME"
        press_enter
        return
    fi

    echo ""
    echo "Creating user '$username'..."

    # Create config directory
    mkdir -p "$user_dir"
    echo "  [OK] Created $user_dir"

    # Copy config templates
    cp "$DEFAULT_CONFIG" "$user_dir/config.json"
    echo "  [OK] Created config.json"

    cp "$DEFAULT_CONFIG" "$user_dir/config_test.json"
    echo "  [OK] Created config_test.json"

    # Copy resume placeholder
    cp "$DEFAULT_RESUME" "$user_dir/resume.txt"
    echo "  [OK] Created resume.txt"

    # Write idle job_status files
    printf '%s\n' "$IDLE_STATUS" > "$user_dir/job_status.json"
    echo "  [OK] Created job_status.json"

    printf '%s\n' "$IDLE_STATUS" > "$user_dir/job_status_test.json"
    echo "  [OK] Created job_status_test.json"

    # Create web output directory and welcome page
    web_dir="$WEB_BASE/$username"
    mkdir -p "$web_dir"
    echo "  [OK] Created web output dir: $web_dir"

    generate_welcome_page "$username"
    echo "  [OK] Generated welcome page: $web_dir/index.html"

    echo ""
    echo "User '$username' created successfully."
    echo ""
    echo "Next steps:"
    echo "  1. Edit $user_dir/config.json with search settings"
    echo "  2. Replace $user_dir/resume.txt with the user's resume"
    echo "  3. Results will appear at: $web_dir/index.html"

    press_enter
}

# ─── delete user ─────────────────────────────────────────────────────────────

do_delete_user() {
    echo ""
    echo "--- Delete User ---"
    echo ""

    get_user_list
    if [ ${#USER_LIST[@]} -eq 0 ]; then
        echo "No users found."
        press_enter
        return
    fi

    echo "Existing users:"
    local i=1
    for u in "${USER_LIST[@]}"; do
        echo "  $i. $u"
        i=$((i + 1))
    done
    echo ""
    echo -n "Enter number of user to delete (or Q to cancel): "
    read -r selection || { echo ""; echo "EOF reached. Exiting."; exit 0; }

    # Cancel
    case "$selection" in
        [qQ]) return ;;
    esac

    # Validate selection is a number in range
    if ! echo "$selection" | grep -qE '^[0-9]+$'; then
        echo "ERROR: Invalid selection."
        press_enter
        return
    fi

    if [ "$selection" -lt 1 ] || [ "$selection" -gt ${#USER_LIST[@]} ]; then
        echo "ERROR: Selection out of range."
        press_enter
        return
    fi

    local target_user="${USER_LIST[$((selection - 1))]}"
    local user_config_dir="$CONFIGS_DIR/$target_user"
    local user_web_dir="$WEB_BASE/$target_user"

    echo ""
    echo "WARNING: This will move all data for user '$target_user' to TRASH."
    echo "  Config dir: $user_config_dir"
    echo "  Web dir:    $user_web_dir"
    echo ""
    echo -n "Are you sure? (y/N): "
    read -r confirm || { echo ""; echo "EOF reached. Exiting."; exit 0; }

    case "$confirm" in
        [yY])
            ;;
        *)
            echo "Cancelled."
            press_enter
            return
            ;;
    esac

    # Create trash directory with timestamp to avoid collisions
    local ts
    ts="$(date '+%Y%m%d_%H%M%S')"
    local trash_dest="$TRASH_DIR/${target_user}_${ts}"
    mkdir -p "$trash_dest"
    echo ""
    echo "Moving user '$target_user' to trash..."

    # Move config dir
    if [ -d "$user_config_dir" ]; then
        mv "$user_config_dir" "$trash_dest/configs/"
        echo "  [OK] Moved configs to $trash_dest/configs/"
    fi

    # Move web dir
    if [ -d "$user_web_dir" ]; then
        mv "$user_web_dir" "$trash_dest/web/"
        echo "  [OK] Moved web output to $trash_dest/web/"
    fi

    echo ""
    echo "User '$target_user' deleted. Data moved to:"
    echo "  $trash_dest"

    press_enter
}

# ─── main menu ───────────────────────────────────────────────────────────────

while true; do
    print_header
    echo "1. Add User"
    echo "2. Delete User"
    echo "Q. Quit"
    echo ""
    echo -n "Select an option: "
    read -r choice || break

    case "$choice" in
        1)
            do_add_user
            ;;
        2)
            do_delete_user
            ;;
        [qQ])
            echo ""
            echo "Goodbye."
            echo ""
            exit 0
            ;;
        *)
            echo "Invalid option: '$choice'"
            press_enter
            ;;
    esac
done
