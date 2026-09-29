#!/opt/bin/bash
# _jobsearch_server_lib.sh — shared stop / start / readiness / warmup logic
# for JobSearch dev + prod. SOURCED by the thin per-env wrappers:
#   stop_jobsearch_{dev,prod}.sh, restart_jobsearch_{dev,prod}.sh
#
# Everything env-specific (APPDIR, PORT, PREFIX, PID file, env name) is passed in
# by the wrapper, so this file never needs to change per environment. This file
# has NO dev/prod excludes → it deploys to prod.

# ── Shared constants (same on both envs) ──────────────────────────────────────
APP_PYTHON=/volume1/.@plugins/AppCentral/python3/bin/python3
APP_WORKERS=4
APP_READY_TIMEOUT=30   # seconds; cold workers can take ~6s on first hit (warmup + TLS)

# app_stop <pid_file> <port>
# Kill the Gunicorn master by PID (never by name), then ensure the port is freed.
app_stop() {
    local pid_file="$1" port="$2" pid
    if [ ! -f "$pid_file" ]; then
        echo "No PID file found — server may not be running."
    else
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping JobSearch server (PID $pid)..."
            kill "$pid"
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                echo "Process still running, sending SIGKILL..."
                kill -9 "$pid"
            fi
            echo "Stopped."
        else
            echo "Process $pid not running. Removing stale PID file."
        fi
        rm -f "$pid_file"
    fi
    # Ensure port is fully released before returning (workers may linger briefly).
    fuser -k "${port}/tcp" 2>/dev/null
    sleep 1
}

# app_restart <env> <appdir> <pid_file> <port> <prefix> <log>
#   env = dev|prod (passed to the shared start_jobsearch.py launcher)
# Stops, starts (under su - johnmfritsch when root, so user-site gunicorn resolves),
# polls until the app actually serves /health, then warms all workers.
app_restart() {
    local env="$1" appdir="$2" pid_file="$3" port="$4" prefix="$5" log="$6"

    mkdir -p "$appdir/logs"
    app_stop "$pid_file" "$port"

    echo "Starting JobSearch $env server..."
    cd "$appdir" || return 1

    if [ "$(id -u)" = "0" ]; then
        su -s /opt/bin/bash johnmfritsch -c "cd $appdir && HOME=/home/<user> nohup $APP_PYTHON -u scripts/start_jobsearch.py $env >> $log 2>&1 &"
    else
        HOME=/home/<user> nohup "$APP_PYTHON" -u scripts/start_jobsearch.py "$env" >> "$log" 2>&1 &
    fi

    # ── Readiness poll: verify the app actually serves, don't guess with a sleep ──
    local health_url="https://127.0.0.1:${port}${prefix}/health"
    local deadline=$(( $(date +%s) + APP_READY_TIMEOUT ))
    local last_pid="-" last_port="no" last_http="000" ready=0
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file" 2>/dev/null)" 2>/dev/null; then
            last_pid="$(cat "$pid_file" 2>/dev/null)"
            if netstat -tln 2>/dev/null | grep -q ":${port} "; then
                last_port="yes"
                last_http=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "$health_url" 2>/dev/null)
                if [ "$last_http" = "200" ]; then
                    ready=1
                    break
                fi
            fi
        fi
        sleep 0.5
    done

    if [ "$ready" -ne 1 ]; then
        echo ""
        echo "ERROR: JobSearch $env did not become ready within ${APP_READY_TIMEOUT}s."
        echo "  last state: pid=${last_pid} port_listening=${last_port} http=${last_http}"
        echo "  --- last 20 log lines ($log) ---"
        tail -n 20 "$log" 2>/dev/null
        return 1
    fi

    local elapsed=$(( APP_READY_TIMEOUT - (deadline - $(date +%s)) ))
    echo "Ready in ~${elapsed}s. PID $(cat "$pid_file"). Log: $log"

    # ── Warm ALL workers so no user hits a cold one ──
    local warm=$(( APP_WORKERS * 2 )) i=0
    while [ "$i" -lt "$warm" ]; do
        curl -sk -o /dev/null --max-time 10 "$health_url" 2>/dev/null
        i=$(( i + 1 ))
    done
    echo "Warmed $warm health hits across $APP_WORKERS workers."
}
