#!/opt/bin/bash
# stop_api.sh - Stop the API server

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${BASE_DIR}/api_server.pid"

# Auto-detect port: DEV→8766, PROD→8765
if echo "$BASE_DIR" | grep -q '/DEV'; then
    PORT=8766
else
    PORT=8765
fi

# Step 1: Kill the process from the PID file if present
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" > /dev/null 2>&1; then
        echo "Stopping API server (PID: $PID)..."
        kill "$PID"
        sleep 1
        # Force kill if still running
        if kill -0 "$PID" > /dev/null 2>&1; then
            echo "Force killing API server..."
            kill -9 "$PID"
        fi
        echo "API server stopped"
    else
        echo "API server is not running (stale PID file)"
    fi
    rm -f "$PID_FILE"
else
    echo "No PID file found"
fi

# Step 2: Kill any remaining api_handler.py process on this port (orphan guard)
ORPHAN=$(ps aux 2>/dev/null | grep "api_handler.py $PORT" | grep -v grep | awk '{print $1}')
if [ -n "$ORPHAN" ]; then
    echo "Killing orphaned api_handler.py process (PID: $ORPHAN)..."
    kill "$ORPHAN" 2>/dev/null
    sleep 1
    kill -9 "$ORPHAN" 2>/dev/null
    echo "Orphan killed"
fi

# Step 3: Wait until port is actually free (up to 8 seconds)
WAIT=0
while netstat -tuln 2>/dev/null | grep -q ":${PORT} "; do
    if [ "$WAIT" -ge 8 ]; then
        echo "WARNING: Port $PORT still in use after 8 seconds"
        break
    fi
    sleep 1
    WAIT=$((WAIT + 1))
done

if ! netstat -tuln 2>/dev/null | grep -q ":${PORT} "; then
    echo "Port $PORT is now free"
fi
