#!/opt/bin/bash
set -e
PID_FILE="/volume1/Web/JobSearch/application_worker_prod.pid"
if [ ! -f "$PID_FILE" ]; then
    echo "PROD application worker is not running (no PID file)."
    exit 0
fi
PID=$(cat "$PID_FILE")
case "$PID" in *[!0-9]*|'') echo "Invalid PROD worker PID file."; exit 1;; esac
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
fi
rm -f "$PID_FILE"
echo "PROD application worker stopped."
