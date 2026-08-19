#!/usr/bin/env sh
# Start sysview using this repo's virtualenv.
#
#   ./run.sh                 # default port 8090
#   ./run.sh --port 9000     # any sysview flag is passed straight through
#   PORT=9000 ./run.sh       # or set the port via the environment
#
# Override the interpreter with SYSVIEW_PYTHON if you are not using .venv.

set -eu

dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${SYSVIEW_PYTHON:-"$dir/.venv/bin/python"}
port=${PORT:-8090}

if [ ! -x "$python" ]; then
    echo "No interpreter at $python" >&2
    echo "Create one with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    echo "Or point SYSVIEW_PYTHON at the interpreter you want to use." >&2
    exit 1
fi

# Any arguments given win over the default port.
if [ "$#" -gt 0 ]; then
    exec "$python" -m sysview "$@"
fi

exec "$python" -m sysview --port "$port"
