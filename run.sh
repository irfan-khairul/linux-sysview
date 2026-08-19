#!/usr/bin/env sh
# Start, stop, and check sysview using this repo's virtualenv.
#
#   ./run.sh                 # run in the foreground (Ctrl+C to stop)
#   ./run.sh start           # run in the background, survives logout
#   ./run.sh stop            # stop the background instance
#   ./run.sh status          # is it running, and on which port
#   ./run.sh restart         # stop then start
#
#   ./run.sh --port 9000     # any sysview flag is passed straight through
#   ./run.sh start --port 9000
#   PORT=9000 ./run.sh start # or set the port via the environment
#
# Override the interpreter with SYSVIEW_PYTHON if you are not using .venv.

set -eu

dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${SYSVIEW_PYTHON:-"$dir/.venv/bin/python"}
port=${PORT:-8080}
pidfile="$dir/.sysview.pid"
logfile="$dir/sysview.log"

require_python() {
    if [ ! -x "$python" ]; then
        echo "No interpreter at $python" >&2
        echo "Create one with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
        echo "Or point SYSVIEW_PYTHON at the interpreter you want to use." >&2
        exit 1
    fi
}

# Echoes the PID if a live process is recorded, otherwise nothing.
running_pid() {
    [ -f "$pidfile" ] || return 0
    pid=$(cat "$pidfile" 2>/dev/null) || return 0
    [ -n "$pid" ] || return 0
    if kill -0 "$pid" 2>/dev/null; then
        echo "$pid"
    else
        # Stale file from a crash or a reboot.
        rm -f "$pidfile"
    fi
}

case "${1:-}" in
start)
    shift
    require_python
    pid=$(running_pid)
    if [ -n "$pid" ]; then
        echo "Already running (pid $pid). Use './run.sh stop' first, or './run.sh restart'."
        exit 1
    fi
    if [ "$#" -gt 0 ]; then
        nohup "$python" -m sysview "$@" >"$logfile" 2>&1 &
    else
        nohup "$python" -m sysview --port "$port" >"$logfile" 2>&1 &
    fi
    pid=$!
    echo "$pid" > "$pidfile"
    # Give it a moment to fail loudly (a taken port is the usual cause).
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        echo "Started (pid $pid). Logging to $logfile"
        sed -n '1p' "$logfile" 2>/dev/null || true
    else
        rm -f "$pidfile"
        echo "Failed to start. Log says:" >&2
        cat "$logfile" >&2
        exit 1
    fi
    ;;
stop)
    pid=$(running_pid)
    if [ -z "$pid" ]; then
        echo "Not running."
        exit 0
    fi
    kill "$pid" 2>/dev/null || true
    # Wait up to ~5s for a clean shutdown before forcing it.
    i=0
    while [ "$i" -lt 50 ] && kill -0 "$pid" 2>/dev/null; do
        sleep 0.1
        i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
        echo "Force-stopped (pid $pid)."
    else
        echo "Stopped (pid $pid)."
    fi
    rm -f "$pidfile"
    ;;
status)
    pid=$(running_pid)
    if [ -n "$pid" ]; then
        echo "Running (pid $pid)."
        sed -n '1p' "$logfile" 2>/dev/null || true
    else
        echo "Not running."
        exit 1
    fi
    ;;
restart)
    shift
    "$0" stop
    "$0" start "$@"
    ;;
*)
    require_python
    if [ "$#" -gt 0 ]; then
        exec "$python" -m sysview "$@"
    fi
    exec "$python" -m sysview --port "$port"
    ;;
esac
