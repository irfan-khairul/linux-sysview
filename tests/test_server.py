# tests/test_server.py
from unittest.mock import patch

from sysview.server import route_get, route_post


class FakeSampler:
    def snapshot(self):
        return {
            "cpu_percent": 5.0,
            "cpu_per_core": [5.0],
            "net": {},
            "disk_io": {"read_rate": 0.0, "write_rate": 0.0},
            "timestamp": 1.0,
        }


def test_resources_route_returns_200_and_payload():
    with patch("sysview.server.collect_resources", return_value={"cpu": {}}) as m:
        status, body = route_get("/api/resources", {}, FakeSampler())
    assert status == 200
    assert body == {"cpu": {}}
    m.assert_called_once()


def test_processes_route_returns_200():
    with patch("sysview.server.collect_processes",
               return_value={"processes": [], "total": 0}):
        status, body = route_get("/api/processes", {}, FakeSampler())
    assert status == 200
    assert body["total"] == 0


def test_docker_route_returns_200_even_when_unavailable():
    payload = {"available": False, "containers": [], "error": "Docker is not installed"}
    with patch("sysview.server.collect_containers", return_value=payload):
        status, body = route_get("/api/docker", {}, FakeSampler())
    # Docker being absent is a normal state, not an HTTP error.
    assert status == 200
    assert body["available"] is False


def test_files_route_passes_path_query():
    with patch("sysview.server.list_directory", return_value={"path": "/tmp"}) as m:
        status, body = route_get("/api/files", {"path": ["/tmp"]}, FakeSampler())
    assert status == 200
    m.assert_called_once_with("/tmp")


def test_files_route_defaults_to_root():
    with patch("sysview.server.list_directory", return_value={"path": "/"}) as m:
        route_get("/api/files", {}, FakeSampler())
    m.assert_called_once_with("/")


def test_unknown_api_route_returns_404():
    status, body = route_get("/api/nonsense", {}, FakeSampler())
    assert status == 404
    assert "error" in body


def test_docker_action_route_dispatches():
    with patch("sysview.server.run_action", return_value={"ok": True, "error": ""}) as m:
        status, body = route_post("/api/docker/abc123/restart")
    assert status == 200
    assert body["ok"] is True
    m.assert_called_once_with("abc123", "restart")


def test_docker_action_with_bad_action_returns_400():
    status, body = route_post("/api/docker/abc123/exec")
    assert status == 400
    assert body["ok"] is False


def test_docker_action_with_bad_id_returns_400():
    status, body = route_post("/api/docker/bad;id/stop")
    assert status == 400
    assert body["ok"] is False


def test_docker_action_failure_returns_500():
    with patch("sysview.server.run_action",
               return_value={"ok": False, "error": "No such container"}):
        status, body = route_post("/api/docker/abc123/stop")
    assert status == 500
    assert body["error"] == "No such container"


def test_unknown_post_route_returns_404():
    status, body = route_post("/api/whatever")
    assert status == 404
