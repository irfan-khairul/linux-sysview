# sysview/docker.py
"""Docker container listing and lifecycle actions via the docker CLI.

Uses the CLI rather than the HTTP API to avoid a second dependency. Every
invocation passes an argv list (never a shell string) and validates the
container id first, so a hostile id cannot become a command.
"""

import json
import re
import subprocess

VALID_ACTIONS = frozenset({"start", "stop", "restart"})

_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_TIMEOUT = 10


def is_valid_container_id(value):
    """True if `value` is a plausible container id or name.

    Rejects anything outside [A-Za-z0-9_.-], which excludes shell
    metacharacters, whitespace, and path traversal sequences.
    """
    if not value or len(value) > 128:
        return False
    if value.startswith("-"):
        # Would be parsed as a docker flag rather than an id.
        return False
    return bool(_ID_RE.match(value))


def _run(args):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=_TIMEOUT,
    )


def _parse_json_lines(text):
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _unavailable(message):
    return {"available": False, "containers": [], "error": message}


def collect_containers():
    """List all containers with per-container CPU and memory where available."""
    try:
        ps = _run(["docker", "ps", "--all", "--format", "{{json .}}"])
    except FileNotFoundError:
        return _unavailable("Docker is not installed")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _unavailable("Docker did not respond: %s" % exc)

    if ps.returncode != 0:
        return _unavailable((ps.stderr or "docker ps failed").strip())

    stats_by_id = {}
    try:
        stats = _run([
            "docker", "stats", "--no-stream", "--format", "{{json .}}",
        ])
        if stats.returncode == 0:
            for row in _parse_json_lines(stats.stdout):
                stats_by_id[row.get("ID", "")] = row
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # Stats are a nice-to-have; the listing is still useful without them.
        pass

    containers = []
    for row in _parse_json_lines(ps.stdout):
        cid = row.get("ID", "")
        stat = stats_by_id.get(cid, {})
        containers.append({
            "id": cid,
            "name": row.get("Names", ""),
            "image": row.get("Image", ""),
            "state": row.get("State", ""),
            "status": row.get("Status", ""),
            "ports": row.get("Ports", ""),
            "cpu_percent": stat.get("CPUPerc", "-"),
            "memory": stat.get("MemUsage", "-"),
            "memory_percent": stat.get("MemPerc", "-"),
        })

    return {"available": True, "containers": containers, "error": ""}


def run_action(container_id, action):
    """Start, stop, or restart a container."""
    if action not in VALID_ACTIONS:
        return {"ok": False, "error": "Unsupported action: %s" % action}
    if not is_valid_container_id(container_id):
        return {"ok": False, "error": "Invalid container id"}

    try:
        result = _run(["docker", action, container_id])
    except FileNotFoundError:
        return {"ok": False, "error": "Docker is not installed"}
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": "Docker did not respond: %s" % exc}

    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or "docker %s failed" % action).strip()}
    return {"ok": True, "error": ""}
