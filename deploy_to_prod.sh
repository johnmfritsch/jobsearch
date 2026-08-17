#!/opt/bin/bash
#
# deploy_to_prod.sh — Job Search Agent DEV → PROD release deployment
#
# Release contract: changelog → preflight → preview → confirm → backup →
# stamp → sync → restart → readiness check → welcome-page regeneration →
# backup retention.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$SCRIPT_DIR"
PROD_DIR="$(dirname "$DEV_DIR")/PROD"
BACKUP_DIR="$PROD_DIR/backups"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
DEV_STATIC="/volume1/Web/johnmfritsch/JobSearch/DEV/static"
PROD_STATIC="/volume1/Web/johnmfritsch/JobSearch/static"
CHANGELOG="$DEV_DIR/CHANGELOG.md"
PRE_DEPLOY="$DEV_DIR/scripts/pre_deploy_check.sh"
POST_DEPLOY="$DEV_DIR/scripts/post_deploy_check.sh"
STAMP_CHANGELOG="$DEV_DIR/scripts/stamp_changelog.py"
CLEANUP_BACKUPS="$DEV_DIR/scripts/cleanup_backups.sh"

RSYNC_EXCLUDES=(
    --exclude='configs/' --exclude='data/' --exclude='logs/' --exclude='TRASH/' --exclude='backups/'
    --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.backup*' --exclude='*.bak'
    --exclude='api_server.pid' --exclude='deploy_to_prod.sh' --exclude='test_input.txt'
    --exclude='test_output.txt'
)

die() { echo "ERROR: $*" >&2; exit 1; }
print_list() {
    local heading="$1" list="$2"
    [ -n "$list" ] || return 0
    echo "  $heading"
    while IFS= read -r line; do echo "    $line"; done <<< "$list"
    echo ""
}

[[ "$DEV_DIR" == */DEV ]] || die "This script must run from the DEV directory (found $DEV_DIR)."
[ -d "$PROD_DIR" ] || die "PROD directory not found: $PROD_DIR"
[ -f "$CHANGELOG" ] || die "Missing required changelog: $CHANGELOG"
[ -x "$PRE_DEPLOY" ] || die "Missing pre-deploy hook: $PRE_DEPLOY"
[ -x "$POST_DEPLOY" ] || die "Missing post-deploy hook: $POST_DEPLOY"
[ -f "$STAMP_CHANGELOG" ] || die "Missing changelog stamper: $STAMP_CHANGELOG"

echo ""
echo "======================================"
echo "  Job Search Agent: Release DEV → PROD"
echo "======================================"
echo "DEV:  $DEV_DIR"
echo "PROD: $PROD_DIR"
echo ""

echo "Step 1: Release preflight..."
"$PRE_DEPLOY" "$DEV_DIR"

echo ""
echo "Step 2: Previewing release changes..."
DRY_RUN_OUTPUT=$(rsync -avn --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PROD_DIR/" 2>&1)
DELETIONS=$(echo "$DRY_RUN_OUTPUT" | grep '^deleting ' | sed 's/^deleting //' | sort || true)
DEPLOYS=$(echo "$DRY_RUN_OUTPUT" | grep -vE '^deleting |^sending |^sent |^total |^$|^\./$|^building file list' | sort || true)

STATIC_DELETIONS=""
STATIC_DEPLOYS=""
if [ -d "$DEV_STATIC" ]; then
    STATIC_DRY_OUTPUT=$(rsync -avn --delete "$DEV_STATIC/" "$PROD_STATIC/" 2>&1)
    STATIC_DELETIONS=$(echo "$STATIC_DRY_OUTPUT" | grep '^deleting ' | sed 's/^deleting //' | sort || true)
    STATIC_DEPLOYS=$(echo "$STATIC_DRY_OUTPUT" | grep -vE '^deleting |^sending |^sent |^total |^$|^\./$|^building file list' | sort || true)
else
    die "DEV static directory not found: $DEV_STATIC"
fi

if [ -z "$DELETIONS$DEPLOYS$STATIC_DELETIONS$STATIC_DEPLOYS" ]; then
    echo "  DEV and PROD are in sync — nothing to deploy."
    exit 0
fi

print_list "Files to deploy:" "$DEPLOYS"
print_list "Files to delete from PROD:" "$DELETIONS"
print_list "Static assets to deploy:" "$STATIC_DEPLOYS"
print_list "Static assets to delete from PROD:" "$STATIC_DELETIONS"

echo "Changelog stamp preview:"
if /opt/bin/python3 "$STAMP_CHANGELOG" --preview "$CHANGELOG"; then
    :
else
    stamp_rc=$?
    [ "$stamp_rc" -eq 2 ] && die "No Unreleased changelog entries are available to stamp."
    exit "$stamp_rc"
fi

echo ""
read -r -p "Deploy this release to PROD? Type yes: " confirm
[ "$confirm" = "yes" ] || { echo "Cancelled."; exit 0; }

echo ""
echo "Step 3: Backing up PROD..."
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/PROD_backup_${TIMESTAMP}.tar.gz"
tar -czf "$BACKUP_FILE" -C "$(dirname "$PROD_DIR")" \
    --exclude='PROD/configs' --exclude='PROD/data' --exclude='PROD/logs' --exclude='PROD/TRASH' \
    --exclude='PROD/backups' --exclude='PROD/__pycache__' --exclude='PROD/*.pyc' \
    --exclude='PROD/*.backup*' --exclude='PROD/*.bak' --exclude='PROD/api_server.pid' \
    --exclude='PROD/test_input.txt' --exclude='PROD/test_output.txt' PROD/
echo "  [OK] Backup: $BACKUP_FILE"

echo ""
echo "Step 4: Stamping CHANGELOG.md..."
/opt/bin/python3 "$STAMP_CHANGELOG" "$CHANGELOG"

echo ""
echo "Step 5: Stopping production API..."
if [ -f "$PROD_DIR/stop_api.sh" ]; then
    "$PROD_DIR/stop_api.sh" || echo "  (PROD API was not running)"
else
    echo "  (No stop_api.sh found — continuing)"
fi

echo ""
echo "Step 6: Syncing DEV code and static assets..."
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$DEV_DIR/" "$PROD_DIR/"
rsync -a --delete "$DEV_STATIC/" "$PROD_STATIC/"
echo "  [OK] DEV code and shared static assets synced"

echo ""
echo "Step 7: Starting and verifying production API..."
[ -f "$PROD_DIR/start_api.sh" ] || die "start_api.sh not found in PROD after sync."
"$PROD_DIR/start_api.sh"
if ! "$PROD_DIR/scripts/post_deploy_check.sh" "$PROD_DIR"; then
    echo ""
    echo "DEPLOY FAILED: code was copied but PROD did not pass readiness."
    echo "Rollback:"
    echo "  1. cd $PROD_DIR && ./stop_api.sh"
    echo "  2. tar -xzf $BACKUP_FILE -C $(dirname "$PROD_DIR")"
    echo "  3. cd $PROD_DIR && ./start_api.sh"
    exit 1
fi

echo ""
echo "Step 8: Regenerating missing PROD welcome pages..."
PROD_WEB_BASE="/volume1/Web/johnmfritsch/JobSearch"
PROD_CONFIGS="$PROD_DIR/configs"
regenerated=0
for user_dir in "$PROD_CONFIGS"/*/; do
    [ -d "$user_dir" ] || continue
    user_name="$(basename "$user_dir")"
    page="$PROD_WEB_BASE/$user_name/index.html"
    [ ! -f "$page" ] || continue
    echo "  Generating welcome page for $user_name"
    mkdir -p "$PROD_WEB_BASE/$user_name"
    (
        BASE_DIR="$PROD_DIR" ENV="PROD" WEB_BASE="$PROD_WEB_BASE" CONFIGS_DIR="$PROD_CONFIGS"
        . "$PROD_DIR/user_maint.sh" 2>/dev/null || true
        generate_welcome_page "$user_name"
    ) 2>/dev/null || echo "  WARNING: could not generate welcome page for $user_name"
    regenerated=$((regenerated + 1))
done
[ "$regenerated" -gt 0 ] || echo "  (all PROD users already have a page)"

echo ""
echo "Step 9: Applying backup retention..."
if [ -x "$CLEANUP_BACKUPS" ]; then
    "$CLEANUP_BACKUPS" || echo "  WARNING: backup cleanup failed; backups were retained."
else
    echo "  WARNING: backup cleanup hook missing; backups were retained."
fi

echo ""
echo "======================================"
echo "  Release deployed and PROD is healthy"
echo "======================================"
echo "  Backup: $BACKUP_FILE"
echo "  Rollback: stop API → tar -xzf backup → start API"
