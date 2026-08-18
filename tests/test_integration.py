import json
import threading
import urllib.request

import pytest

from sysview.sampler import Sampler
from sysview.server import make_server


@pytest.fixture
def server():
    sampler = Sampler(interval=0.05)
    # Port 0 lets the OS pick a free port, so tests never collide.
    httpd = make_server("127.0.0.1", 0, sampler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        sampler.stop()


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_index_is_served(server):
    with urllib.request.urlopen(server + "/", timeout=5) as resp:
        body = resp.read().decode("utf-8")
    assert resp.status == 200
    assert "sysview" in body


def test_static_assets_are_served(server):
    for path in ("/style.css", "/app.js"):
        with urllib.request.urlopen(server + path, timeout=5) as resp:
            assert resp.status == 200
            assert resp.read()


def test_resources_endpoint_returns_valid_json(server):
    status, body = _get_json(server + "/api/resources")
    assert status == 200
    for key in ("cpu", "memory", "swap", "disks", "network", "uptime_seconds"):
        assert key in body


def test_processes_endpoint_returns_valid_json(server):
    status, body = _get_json(server + "/api/processes")
    assert status == 200
    assert isinstance(body["processes"], list)
    assert body["total"] >= 1


def test_files_endpoint_lists_root(server):
    status, body = _get_json(server + "/api/files?path=/")
    assert status == 200
    assert body["path"] == "/"
    assert body["parent"] is None
    assert isinstance(body["entries"], list)


def test_docker_endpoint_always_answers(server):
    # Passes whether or not Docker is installed on the machine running tests.
    status, body = _get_json(server + "/api/docker")
    assert status == 200
    assert "available" in body


def test_unknown_endpoint_returns_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(server + "/api/nope", timeout=5)
    assert exc.value.code == 404
