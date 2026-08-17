#!/usr/local/AppCentral/entware/opt/bin/bash
# run_job_search.sh — Unified Safe Runner (2025 edition)
# Works with BusyBox + bash + per-user configs/log directories

# --- PYTHON ENVIRONMENT ---
PY_BIN="/opt/bin/python3"
PY_LIB="/opt/lib"
PY_SITE="/opt/lib/python3.11/site-packages"

# --- DETECT ENVIRONMENT (DEV or PROD) ---

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" =~ "/DEV$" || "$SCRIPT_DIR" =~ "/DEV/" || "$SCRIPT_DIR" =~ "/DEV" ]]; then
  ENVIRONMENT="DEV"
  echo "🔧 DEV ENVIRONMENT DETECTED"
else
  ENVIRONMENT="PROD"
  echo "🔧 PROD ENVIRONMENT DETECTED"
fi

# --- BASE DIRECTORIES ---
BASE_DIR="$SCRIPT_DIR"
LOG_ROOT="${BASE_DIR}/logs"

# --- ARGUMENT HANDLING ---
USER_NAME="$1"
MODE_FLAG="$2"   # optional --test-mode

if [ -z "$USER_NAME" ]; then
  echo "Usage: $0 <UserName> [--test-mode]"
  exit 1
fi

USER_LOG_DIR="${LOG_ROOT}/${USER_NAME}"
mkdir -p "$USER_LOG_DIR"

DATESTAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
MAIN_LOG="${USER_LOG_DIR}/run_job_search.log"
ROTATED_LOG="${USER_LOG_DIR}/run_job_search_${DATESTAMP}.log"

# --- ROTATE OLD LOGS (keep last 5) ---
LOG_COUNT=$(ls -1t "$USER_LOG_DIR"/run_job_search_*.log 2>/dev/null | wc -l)
if [ "$LOG_COUNT" -gt 5 ]; then
  ls -1t "$USER_LOG_DIR"/run_job_search_*.log 2>/dev/null | tail -n +6 | while read -r old; do
    [ -f "$old" ] && rm -f "$old"
  done
fi

# --- HEADER ---
echo "------------------------------------------------------------"
echo "Job Search Agent Run Started for user: ${USER_NAME}"
echo "Start Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo "------------------------------------------------------------"

{
  echo "------------------------------------------------------------"
  echo "Job Search Agent Run Started for user: ${USER_NAME}"
  echo "Start Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "------------------------------------------------------------"
} >> "$MAIN_LOG"

# --- PYTHON ENVIRONMENT SETUP ---
export LD_LIBRARY_PATH="$PY_LIB"
export PYTHONPATH="$PY_SITE"
export JOB_SEARCH_ENV="$ENVIRONMENT"
cd "$BASE_DIR" || exit 1

# --- COMMAND EXECUTION ---
CMD=("$PY_BIN" main.py "$USER_NAME")
if [ "$MODE_FLAG" = "--test-mode" ]; then
  CMD+=("--test-mode")
fi

echo "Running: ${CMD[*]}"
{
  echo "Running: ${CMD[*]}"
  echo
} >> "$MAIN_LOG"

# Direct file redirect — NO pipes. Any pipe (while-read, tee, etc.) risks
# a 64KB buffer deadlock when the downstream can't drain fast enough.
# Write to rotated log during the run, then append a copy to the main log.
"${CMD[@]}" >> "$ROTATED_LOG" 2>&1
LD_LIBRARY_PATH= /bin/cat "$ROTATED_LOG" >> "$MAIN_LOG"

# --- CLEANUP & SUMMARY ---
unset LD_LIBRARY_PATH

{
  echo "------------------------------------------------------------"
  echo "Job Search Agent Run Completed for user: ${USER_NAME}"
  echo "End Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "Logs saved to: $MAIN_LOG and $ROTATED_LOG"
  echo "------------------------------------------------------------"
} >> "$MAIN_LOG"

echo "------------------------------------------------------------"
echo "Job Search Agent Run Completed for user: ${USER_NAME}"
echo "End Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Logs saved to: $MAIN_LOG and $ROTATED_LOG"
echo "------------------------------------------------------------"

