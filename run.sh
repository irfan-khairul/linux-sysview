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

# The server cannot relaunch itself, so this loop does it. Exit code 42 means
# "restart requested from the UI"; every other exit means stay down, so a
# deliberate stop or a crash-on-startup is not fought by the supervisor.
supervise() {
    while :; do
        # `set -e` would abort the script the moment the server exits
        # non-zero, before the code below could inspect why — so the call is
        # guarded to keep the exit status inspectable.
        code=0
        "$python" -m sysview "$@" || code=$?
        [ "$code" -eq 42 ] || exit "$code"
        # A moment for the listening socket to be released before rebinding.
        sleep 1
    done
}

require_python() {
    if [ ! -x "$python" ]; then
        echo "No interpreter at $python" >&2
        echo "Create one with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
        echo "Or point SYSVIEW_PYTHON at the interpreter you want to use." >&2
        exit 1
    fi
}

# Echoes the working directory of a process, or nothing if it cannot be read.
# /proc is the Linux route; lsof covers macOS, where it may be absent.
process_cwd() {
    if [ -r "/proc/$1/cwd" ]; then
        readlink "/proc/$1/cwd" 2>/dev/null
    elif command -v lsof >/dev/null 2>&1; then
        lsof -a -d cwd -p "$1" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
    fi
}

# Echoes every sysview PID belonging to THIS checkout, one per line.
#
# The PID file alone is not enough: a process started before the file existed,
# or superseded by a later start, keeps running while `stop` reports success.
# Scoping by working directory rather than by the command line matters because
# the command line holds only the interpreter path and "-m sysview" — nothing
# identifying the repo — so a blind pattern kill would take out sysview
# instances belonging to other checkouts.
stray_pids() {
    self=$$
    pgrep -f "m sysview" 2>/dev/null | while read -r pid; do
        [ "$pid" = "$self" ] && continue
        cwd=$(process_cwd "$pid")
        # With no readable cwd, leave it alone rather than risk killing
        # something that is not ours.
        [ "$cwd" = "$dir" ] && echo "$pid"
    done
}

# Ends one process, escalating to SIGKILL only if it ignores SIGTERM.
terminate() {
    kill "$1" 2>/dev/null || true
    i=0
    while [ "$i" -lt 50 ] && kill -0 "$1" 2>/dev/null; do
        sleep 0.1
        i=$((i + 1))
    done
    if kill -0 "$1" 2>/dev/null; then
        kill -9 "$1" 2>/dev/null || true
        return 1
    fi
    return 0
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
        nohup "$0" __supervise "$@" >"$logfile" 2>&1 &
    else
        nohup "$0" __supervise --port "$port" >"$logfile" 2>&1 &
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
    forced=0

    # Anything belonging to this checkout, whether or not the pid file knows
    # about it. The supervisor is killed here too, so it cannot relaunch the
    # server on the way out.
    targets=$(stray_pids)
    tracked=$(running_pid)
    case " $targets " in
        *" $tracked "*) ;;
        *) [ -n "$tracked" ] && targets="$targets $tracked" ;;
    esac
    rm -f "$pidfile"

    stopped=0
    for pid in $targets; do
        terminate "$pid" || forced=$((forced + 1))
        stopped=$((stopped + 1))
    done

    # The count includes the supervisor shell alongside the server(s) it
    # watches, so it is normally one higher than the number of listening
    # servers. Saying so avoids it reading like a miscount.
    if [ "$stopped" -eq 0 ]; then
        echo "Not running."
    elif [ "$forced" -gt 0 ]; then
        echo "Stopped. $stopped process(es) ended, $forced needed SIGKILL."
    else
        echo "Stopped. $stopped process(es) ended (server plus supervisor)."
    fi
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
__supervise)
    # Internal: the supervised loop itself, invoked by `start`.
    shift
    require_python
    supervise "$@"
    ;;
*)
    require_python
    if [ "$#" -gt 0 ]; then
        supervise "$@"
    else
        supervise --port "$port"
    fi
    ;;
esac
