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


# Compose stamps these onto every container it creates, so a project's
# containers can be grouped long after the compose file itself is gone.
# Source: https://github.com/docker/compose/blob/main/pkg/api/labels.go
_PROJECT_LABEL = "com.docker.compose.project"
_SERVICE_LABEL = "com.docker.compose.service"
_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


def _parse_labels(raw):
    """Parse docker's Labels field.

    `docker ps --format '{{json .}}'` renders labels as a single
    comma-separated "key=value" string, not as a JSON object, so it needs
    splitting by hand. A value legitimately containing a comma cannot be
    recovered from this format; the compose keys we care about never do.
    """
    labels = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        labels[key.strip()] = value.strip()
    return labels


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
        labels = _parse_labels(row.get("Labels", ""))
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
            # Empty for containers started with plain `docker run`, which the
            # UI groups separately.
            "project": labels.get(_PROJECT_LABEL, ""),
            "service": labels.get(_SERVICE_LABEL, ""),
            # The path the project was created from. It may no longer exist —
            # deleting the directory does not affect the containers.
            "working_dir": labels.get(_WORKING_DIR_LABEL, ""),
        })

    return {"available": True, "containers": containers, "error": ""}


def run_group_action(container_ids, action):
    """Apply an action to every container in a Compose project.

    Each container is acted on individually rather than shelling out to
    `docker compose`, which would need the compose file to still exist on
    disk — deleting a project directory leaves its containers running, and
    this view should keep working for them.

    Every id is validated before anything runs, so a malformed one fails the
    whole request instead of leaving a group half-actioned.
    """
    if action not in VALID_ACTIONS:
        return {"ok": False, "error": "Unsupported action: %s" % action, "results": []}
    if not container_ids:
        return {"ok": False, "error": "No containers given", "results": []}
    for cid in container_ids:
        if not is_valid_container_id(cid):
            return {"ok": False, "error": "Invalid container id", "results": []}

    results = []
    for cid in container_ids:
        outcome = run_action(cid, action)
        results.append({"id": cid, "ok": outcome["ok"], "error": outcome["error"]})

    failed = [r for r in results if not r["ok"]]
    if not failed:
        return {"ok": True, "error": "", "results": results}
    # Report how much of the group succeeded; a partial failure is normal when
    # one container of a stack is already in the target state.
    return {
        "ok": False,
        "error": "%d of %d failed: %s" % (
            len(failed), len(results), failed[0]["error"]
        ),
        "results": results,
    }


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
