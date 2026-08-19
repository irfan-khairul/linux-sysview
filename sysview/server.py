# sysview/server.py
"""HTTP layer: routing, JSON encoding, and static file serving.

Routing is a plain function so it can be tested without binding a socket. This
module never reads /proc or invokes docker directly; it only calls collectors.
"""

import json
import os
import posixpath
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .docker import VALID_ACTIONS, collect_containers, is_valid_container_id, run_action
from .files import list_directory
from .metrics import collect_resources
from .processes import DEFAULT_SORT, collect_processes

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


DEFAULT_UI_INTERVAL = 2.0


def route_get(path, query, sampler, ui_interval=DEFAULT_UI_INTERVAL):
    """Return (status_code, payload_dict) for a GET API path."""
    if path == "/api/resources":
        return 200, collect_resources(sampler.snapshot())
    if path == "/api/processes":
        # Sorting and filtering are server-side so they apply to every process,
        # not just the truncated slice the browser receives.
        return 200, collect_processes(
            sort=query.get("sort", [DEFAULT_SORT])[0],
            desc=query.get("desc", ["1"])[0] != "0",
            query=query.get("q", [""])[0],
        )
    if path == "/api/docker":
        # An unreachable Docker daemon is a normal state reported in the body,
        # not an HTTP error.
        return 200, collect_containers()
    if path == "/api/files":
        requested = query.get("path", ["/"])[0] or "/"
        return 200, list_directory(requested)
    if path == "/api/config":
        return 200, {"interval": ui_interval}
    return 404, {"error": "Unknown endpoint: %s" % path}


def route_post(path):
    """Return (status_code, payload_dict) for a POST API path."""
    parts = [p for p in path.strip("/").split("/") if p]
    # Expected shape: api / docker / <id> / <action>
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "docker":
        container_id, action = parts[2], parts[3]
        if action not in VALID_ACTIONS:
            return 400, {"ok": False, "error": "Unsupported action: %s" % action}
        if not is_valid_container_id(container_id):
            return 400, {"ok": False, "error": "Invalid container id"}
        result = run_action(container_id, action)
        return (200 if result["ok"] else 500), result
    return 404, {"ok": False, "error": "Unknown endpoint: %s" % path}


class Handler(SimpleHTTPRequestHandler):
    sampler = None
    ui_interval = DEFAULT_UI_INTERVAL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        # Polling every 2 seconds would otherwise flood the console.
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _log_unhandled_exception(self):
        # log_message() is silenced above to avoid flooding the console with
        # routine per-request access logs, but an unexpected collector
        # failure is exactly the kind of thing the operator needs to see, so
        # print it directly to stderr rather than routing it through the
        # silenced logger.
        traceback.print_exc(file=sys.stderr)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                status, payload = route_get(
                    parsed.path, parse_qs(parsed.query), self.sampler, self.ui_interval
                )
            except Exception:
                self._log_unhandled_exception()
                self._send_json(500, {"error": "Internal server error"})
                return
            self._send_json(status, payload)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                status, payload = route_post(parsed.path)
            except Exception:
                self._log_unhandled_exception()
                self._send_json(500, {"error": "Internal server error"})
                return
            self._send_json(status, payload)
            return
        self._send_json(404, {"ok": False, "error": "Not found"})

    def translate_path(self, path):
        # Serve only from STATIC_DIR. Deliberately do NOT percent-decode the
        # path (unlike the stdlib base class, which calls unquote()): this is
        # what keeps encoded traversal sequences such as "%2e%2e%2f" inert as
        # a literal filename instead of resolving to "..". The tradeoff is
        # that a legitimately percent-encoded asset filename (e.g. a space
        # encoded as "%20") would not resolve either.
        path = posixpath.normpath(urlparse(path).path)
        parts = [p for p in path.split("/") if p and p not in (os.curdir, os.pardir)]
        return os.path.join(STATIC_DIR, *parts)


def make_server(host, port, sampler, ui_interval=DEFAULT_UI_INTERVAL):
    """Build (but do not start) the HTTP server."""
    handler = type("BoundHandler", (Handler,), {"sampler": sampler, "ui_interval": ui_interval})
    return ThreadingHTTPServer((host, port), handler)
