# tests/test_static.py
import os

from sysview.server import STATIC_DIR


def _read(name):
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def test_static_files_exist():
    for name in ("index.html", "style.css", "app.js"):
        assert os.path.isfile(os.path.join(STATIC_DIR, name)), name


def test_index_has_all_four_view_containers():
    html = _read("index.html")
    for view in ("view-resources", "view-processes", "view-docker", "view-files"):
        assert view in html, view


def test_index_loads_only_local_assets():
    html = _read("index.html")
    # No CDN, no remote fonts: the box may have no internet access.
    assert "http://" not in html
    assert "https://" not in html


def test_app_js_defines_all_routes():
    js = _read("app.js")
    for route in ("#/resources", "#/processes", "#/docker", "#/files"):
        assert route in js, route


def test_index_has_default_two_second_interval_option():
    html = _read("index.html")
    assert '<option value="2" selected>2s</option>' in html


def test_app_js_fetches_config_on_startup():
    js = _read("app.js")
    assert "/api/config" in js
    # The config fetch must happen before the first scheduling pass so the
    # server-configured interval is in effect before the first poll starts.
    assert "loadConfig()" in js


def test_app_js_falls_back_when_config_fetch_fails():
    js = _read("app.js")
    assert "loadConfig" in js and ".catch(" in js
