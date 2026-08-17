#!/opt/bin/bash
# start_api.sh - Start the API server for job search configuration management

PY_BIN="/opt/bin/python3"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-detect port: DEV→8766, PROD→8765
if echo "$BASE_DIR" | grep -q '/DEV'; then
    PORT=8766
else
    PORT=8765
fi

PID_FILE="${BASE_DIR}/api_server.pid"
LOG_FILE="${BASE_DIR}/logs/api_server.log"

# Ensure logs directory exists
mkdir -p "${BASE_DIR}/logs"

# Step 1: Check if port is already in use
if netstat -tuln 2>/dev/null | grep -q ":${PORT} "; then
    # Port is bound — check if it's our process
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" > /dev/null 2>&1; then
            echo "API server is already running (PID: $PID, port: $PORT)"
            exit 0
        fi
    fi
    echo "ERROR: Port $PORT is already in use by another process. Run stop_api.sh first."
    exit 1
fi

# Step 2: Clean up any stale PID file
if [ -f "$PID_FILE" ]; then
    echo "Removing stale PID file"
    rm -f "$PID_FILE"
fi

# Step 3: Start the server in background
echo "Starting API server on port ${PORT}..."
nohup "$PY_BIN" "${BASE_DIR}/api_handler.py" "$PORT" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

echo "API server started on port ${PORT} (PID: $(cat $PID_FILE))"
echo "Logs: $LOG_FILE"
echo ""
echo "To stop: ${BASE_DIR}/stop_api.sh"
