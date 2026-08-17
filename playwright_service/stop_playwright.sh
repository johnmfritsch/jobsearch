#!/opt/bin/bash
# stop_playwright.sh — Stop the Playwright URL resolution container
# Auto-detects DEV vs PROD from script location.

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if echo "$BASE_DIR" | grep -q '/DEV'; then
    CONTAINER="playwright-jobsearch-dev"
else
    CONTAINER="playwright-jobsearch-prod"
fi

# Check if running
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER}$"; then
    echo "Stopping Playwright container ($CONTAINER)..."
    docker stop "$CONTAINER" 2>/dev/null
    echo "Playwright container stopped."
else
    echo "Playwright container ($CONTAINER) is not running."
fi
