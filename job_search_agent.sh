#!/opt/bin/bash
# job_search_agent.sh — Unified management for Job Search Agent
# Auto-detects DEV vs PROD from script location.
# Usage: ./job_search_agent.sh

# =====================================================
# Environment Detection
# =====================================================
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if echo "$BASE_DIR" | grep -q '/DEV'; then
    ENV="DEV"
    API_PORT=8766
    PW_PORT=3001
    PW_CONTAINER="playwright-jobsearch-dev"
else
    ENV="PROD"
    API_PORT=8765
    PW_PORT=3000
    PW_CONTAINER="playwright-jobsearch-prod"
fi

PID_FILE="$BASE_DIR/api_server.pid"

# =====================================================
# Color Constants
# =====================================================
GREEN='\033[0;32m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

# =====================================================
# Sudo / Root Detection
# =====================================================
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
    $SUDO -v 2>/dev/null || printf "${RED}WARNING: sudo unavailable. Docker commands will fail.${RESET}\n"
fi

# =====================================================
# Uptime Formatting
# =====================================================
# Converts ps etime ([-DD:]HH:MM:SS or MM:SS) or Docker status
# ("13 minutes", "2 hours", "1 day") into "X mins XX secs" / "Xh Xm" / "Xd Xh"
format_uptime() {
    local raw="$1"
    # --- ps etime format: [[DD-]HH:]MM:SS ---
    if echo "$raw" | grep -qE '^([0-9]+-)?([0-9]+:)?[0-9]+:[0-9]+$'; then
        local days=0 hours=0 mins=0 secs=0
        # Strip optional days prefix (e.g. "2-03:15:00" -> days=2, rest="03:15:00")
        if echo "$raw" | grep -q '-'; then
            days="${raw%%-*}"
            raw="${raw#*-}"
        fi
        # Count colons to determine HH:MM:SS vs MM:SS
        local colon_count
        colon_count=$(echo "$raw" | tr -cd ':' | wc -c)
        if [ "$colon_count" -eq 2 ]; then
            hours="${raw%%:*}"; raw="${raw#*:}"
            mins="${raw%%:*}";  secs="${raw#*:}"
        else
            mins="${raw%%:*}"; secs="${raw#*:}"
        fi
        # Strip leading zeros for arithmetic
        days=$((10#$days)); hours=$((10#$hours))
        mins=$((10#$mins)); secs=$((10#$secs))
        if [ "$days" -gt 0 ]; then
            printf "%dd %dh" "$days" "$hours"
        elif [ "$hours" -gt 0 ]; then
            printf "%dh %dm" "$hours" "$mins"
        else
            printf "%d mins %02d secs" "$mins" "$secs"
        fi
        return
    fi
    # --- Docker status format: "13 minutes", "2 hours", "1 day", "About an hour" ---
    local num unit
    if echo "$raw" | grep -qi 'about an hour'; then
        printf "~1h 0m"; return
    fi
    num=$(echo "$raw" | awk '{print $1}')
    unit=$(echo "$raw" | awk '{print $2}')
    case "$unit" in
        second|seconds) printf "0 mins %02d secs" "$num" ;;
        minute|minutes) printf "%d mins 00 secs" "$num" ;;
        hour|hours)     printf "%dh 0m" "$num" ;;
        day|days)       printf "%dd 0h" "$num" ;;
        *)              printf "%s" "$raw" ;;  # fallback: print as-is
    esac
}

# =====================================================
# Status Check Functions
# =====================================================
check_api_status() {
    API_STATUS="DOWN"
    API_PID="-"
    API_PORT_VAL="-"
    API_UPTIME="-"

    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$pid" ] && $SUDO kill -0 "$pid" > /dev/null 2>&1; then
            if netstat -tuln 2>/dev/null | grep -q ":${API_PORT} "; then
                API_STATUS="UP"
                API_PID="$pid"
                API_PORT_VAL="$API_PORT"
                local etime
                etime=$(/opt/bin/ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
                API_UPTIME="$(format_uptime "$etime")"
            else
                API_PID="$pid"
                API_PORT_VAL="?"
            fi
        fi
    fi
}

check_pw_status() {
    PW_STATUS="DOWN"
    PW_PID="N/A"
    PW_PORT_VAL="-"
    PW_UPTIME="-"

    local docker_status
    docker_status=$($SUDO docker ps --filter "name=^${PW_CONTAINER}$" --format "{{.Status}}" 2>/dev/null) || docker_status=""

    if [ -n "$docker_status" ]; then
        PW_STATUS="UP"
        PW_PORT_VAL="$PW_PORT"
        # docker_status is like "Up 3 minutes" — strip leading "Up " then format
        PW_UPTIME="$(format_uptime "${docker_status#Up }")"
    fi
}

# =====================================================
# Display Function
# =====================================================
print_status_screen() {
    local api_color="$RED"
    [ "$API_STATUS" = "UP" ] && api_color="$GREEN"
    local pw_color="$RED"
    [ "$PW_STATUS" = "UP" ] && pw_color="$GREEN"

    printf "\n"
    printf "${BOLD}============================================================${RESET}\n"
    printf "${BOLD}  Job Search Agent - %s${RESET}\n" "$ENV"
    printf "${BOLD}============================================================${RESET}\n"
    printf "\n"
    printf "  %-16s %-8s %-8s %-6s %s\n" "Component" "Status" "PID" "Port" "Uptime"
    printf "  %-16s %-8s %-8s %-6s %s\n" "----------------" "--------" "--------" "------" "------------"
    printf "  %-16s ${api_color}%-8s${RESET} %-8s %-6s %s\n" \
        "Python API" "$API_STATUS" "$API_PID" "$API_PORT_VAL" "$API_UPTIME"
    printf "  %-16s ${pw_color}%-8s${RESET} %-8s %-6s %s\n" \
        "Playwright" "$PW_STATUS" "$PW_PID" "$PW_PORT_VAL" "$PW_UPTIME"
    printf "\n"
    printf "${BOLD}============================================================${RESET}\n"
}

# =====================================================
# Action Functions
# =====================================================
stop_all() {
    printf "\n--- Stopping all components ---\n\n"

    # Stop API server
    printf "Stopping API server...\n"
    if [ -f "$BASE_DIR/stop_api.sh" ]; then
        $SUDO "$BASE_DIR/stop_api.sh"
    else
        printf "  (stop_api.sh not found -- skipping)\n"
    fi

    # Stop Playwright container
    printf "Stopping Playwright container ($PW_CONTAINER)...\n"
    if [ -f "$BASE_DIR/playwright_service/stop_playwright.sh" ]; then
        $SUDO "$BASE_DIR/playwright_service/stop_playwright.sh"
    else
        $SUDO docker stop "$PW_CONTAINER" 2>/dev/null && printf "  Container stopped.\n" || printf "  Container was not running.\n"
    fi

    printf "\nAll components stopped.\n"
}

start_all() {
    # Stop everything first (clean slate)
    stop_all

    printf "\n--- Starting all components ---\n\n"

    # Start API server
    printf "Starting API server...\n"
    if [ -f "$BASE_DIR/start_api.sh" ]; then
        $SUDO "$BASE_DIR/start_api.sh"
    else
        printf "  (start_api.sh not found -- skipping)\n"
    fi

    # Start Playwright container
    printf "Starting Playwright container ($PW_CONTAINER)...\n"
    $SUDO docker start "$PW_CONTAINER" 2>/dev/null && printf "  Container started.\n" || {
        printf "  Container not found. Running full setup...\n"
        if [ -f "$BASE_DIR/playwright_service/start_playwright.sh" ]; then
            $SUDO "$BASE_DIR/playwright_service/start_playwright.sh"
        else
            printf "  (start_playwright.sh not found -- cannot create container)\n"
        fi
    }

    # Brief pause for services to initialize
    sleep 2
    printf "\nAll components started.\n"
}

# =====================================================
# Helper Functions
# =====================================================
press_enter() {
    printf "\n"
    printf "Press Enter to continue..."
    read -r _dummy || exit 0
}

# =====================================================
# Main Loop
# =====================================================
while true; do
    clear
    check_api_status
    check_pw_status
    print_status_screen

    printf "\n"
    printf "  1. Start App\n"
    printf "  2. Stop App\n"
    printf "  3. Quit\n"
    printf "\n"
    printf "  Choice [1-3]: "
    read -r choice

    case "$choice" in
        1) start_all; press_enter ;;
        2) stop_all; press_enter ;;
        3) printf "\nGoodbye.\n"; exit 0 ;;
        *) printf "  Invalid choice.\n"; sleep 1 ;;
    esac
done
