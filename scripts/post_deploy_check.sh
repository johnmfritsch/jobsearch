#!/opt/bin/bash
# Confirm that an API environment is genuinely serving its health endpoint.

set -euo pipefail

BASE_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ "$BASE_DIR" == */DEV ]]; then PORT=8766; else PORT=8765; fi
PID_FILE="$BASE_DIR/api_server.pid"
LOG_FILE="$BASE_DIR/logs/api_server.log"
ATTEMPTS=15

echo "  Waiting for API health on port $PORT..."
for ((attempt=1; attempt<=ATTEMPTS; attempt++)); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "  [OK] API healthy (PID $(cat "$PID_FILE"), port $PORT)"
        else
            echo "  [OK] API healthy (port $PORT; PID file unavailable)"
        fi
        exit 0
    fi
    sleep 1
done

echo "  [FAIL] API did not become healthy on port $PORT after $ATTEMPTS seconds." >&2
if [ -f "$LOG_FILE" ]; then
    echo "  Recent log:" >&2
    tail -n 20 "$LOG_FILE" | sed 's/^/    /' >&2
fi
exit 1
