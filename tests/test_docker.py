# tests/test_docker.py
import json
import subprocess
from unittest.mock import patch

from sysview.docker import (
    VALID_ACTIONS,
    collect_containers,
    is_valid_container_id,
    run_action,
)


def test_valid_actions_are_exactly_three():
    assert VALID_ACTIONS == frozenset({"start", "stop", "restart"})


def test_container_id_validation_rejects_shell_metacharacters():
    assert is_valid_container_id("a1b2c3d4")
    assert is_valid_container_id("my_container-1.0")
    assert not is_valid_container_id("abc; rm -rf /")
    assert not is_valid_container_id("$(whoami)")
    assert not is_valid_container_id("a b")
    assert not is_valid_container_id("")
    assert not is_valid_container_id("../etc")


def test_collect_containers_parses_ps_and_stats():
    ps_lines = "\n".join([
        json.dumps({"ID": "abc123", "Names": "web", "Image": "nginx",
                    "State": "running", "Status": "Up 2 hours", "Ports": "80/tcp"}),
        json.dumps({"ID": "def456", "Names": "db", "Image": "postgres",
                    "State": "exited", "Status": "Exited (0)", "Ports": ""}),
    ])
    stats_lines = json.dumps(
        {"ID": "abc123", "CPUPerc": "12.50%", "MemUsage": "50MiB / 1GiB", "MemPerc": "5.00%"}
    )

    def fake_run(cmd, **kwargs):
        out = ps_lines if "ps" in cmd else stats_lines
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    with patch("sysview.docker.subprocess.run", side_effect=fake_run):
        result = collect_containers()

    assert result["available"] is True
    by_name = {c["name"]: c for c in result["containers"]}
    assert by_name["web"]["cpu_percent"] == "12.50%"
    assert by_name["web"]["state"] == "running"
    # A stopped container has no stats line; it must still be listed.
    assert by_name["db"]["cpu_percent"] == "-"


def test_docker_missing_reports_unavailable_not_exception():
    with patch("sysview.docker.subprocess.run", side_effect=FileNotFoundError):
        result = collect_containers()
    assert result["available"] is False
    assert result["containers"] == []
    assert "not" in result["error"].lower()


def test_daemon_not_running_reports_unavailable():
    err = subprocess.CompletedProcess(["docker"], 1, stdout="",
                                      stderr="Cannot connect to the Docker daemon")
    with patch("sysview.docker.subprocess.run", return_value=err):
        result = collect_containers()
    assert result["available"] is False
    assert "daemon" in result["error"].lower()


def test_run_action_rejects_invalid_action():
    result = run_action("abc123", "exec")
    assert result["ok"] is False
    assert "action" in result["error"].lower()


def test_run_action_rejects_invalid_id():
    result = run_action("abc; rm -rf /", "stop")
    assert result["ok"] is False
    assert "id" in result["error"].lower()


def test_run_action_passes_id_as_separate_argv():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="abc123", stderr="")

    with patch("sysview.docker.subprocess.run", side_effect=fake_run):
        result = run_action("abc123", "restart")

    assert result["ok"] is True
    # The command must be an argv list with the id as its own element, never a
    # shell string.
    assert calls[0] == ["docker", "restart", "abc123"]


def test_run_action_reports_docker_failure():
    fail = subprocess.CompletedProcess(["docker"], 1, stdout="",
                                       stderr="No such container: abc123")
    with patch("sysview.docker.subprocess.run", return_value=fail):
        result = run_action("abc123", "stop")
    assert result["ok"] is False
    assert "No such container" in result["error"]
