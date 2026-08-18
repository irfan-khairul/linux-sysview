# Linux System Resource Viewer — Design

**Date:** 2026-08-19
**Status:** Approved for planning

## Purpose

A web-based system monitor for a remote Dell Linux machine. The agent runs on the
Linux box; the user views it in a browser from their Mac over the local network.

Four views: system resources, system processes, Docker processes, file explorer.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Access model | Web UI served from the Linux box | Machine is reached over the network; a browser gives a real file explorer |
| Backend | Python 3 + `psutil` | Python is preinstalled on virtually every distro; one dependency, no build step |
| Frontend | Vanilla JS, hand-written CSS | Lightest option; no bundler, no framework, no `node_modules` |
| Process management | View only | User's explicit choice — no kill, renice, or signal endpoints |
| Docker management | View + start/stop/restart | Day-to-day need |
| Auth | None; binds to LAN | User's choice for a trusted private network. Bind address is configurable |
| Refresh | Polling, user-adjustable interval | Simplest correct approach at these rates |
| File explorer root | Whole filesystem from `/` | OS permissions still apply; read-only |

### Accepted risk

The Docker write endpoints are unauthenticated. Anyone who can reach the port on
the LAN can start, stop, or restart containers. The user accepted this for a
trusted network. Mitigations: write surface is limited to exactly three Docker
actions, and `--host` allows binding to `127.0.0.1` for use over an SSH tunnel
without any code change.

## Architecture

```
linux-system-resource/
  sysview/
    __init__.py
    __main__.py        # arg parsing (--host --port --interval), starts server
    server.py          # ThreadingHTTPServer + route table, JSON helpers
    sampler.py         # background thread holding the latest snapshot
    metrics.py         # cpu, memory, disk, network -> dicts
    processes.py       # process table snapshot
    docker.py          # container list + start/stop/restart via docker CLI
    files.py           # directory listing, path resolution and guards
    static/
      index.html       # nav + four view containers
      app.js           # hash router, polling loop, table rendering
      style.css        # hand-written, dark
  tests/               # pytest, one file per module
  README.md
  requirements.txt     # psutil
```

**Layering rule:** collectors never touch HTTP; `server.py` never reads `/proc`.
Every collector is a pure-ish function returning a plain dict, so it can be
tested without an HTTP server.

**Data flow:** browser polls `GET /api/<view>` on the selected interval ->
route table dispatches to a collector -> collector returns a dict ->
server JSON-encodes it. Only the active view polls.

### The sampler thread

`psutil.cpu_percent()` is a delta between successive calls, so a stateless
request handler returns meaningless values on first call and races under
concurrent requests. Likewise, network and disk I/O are cumulative counters,
not rates.

A single background thread refreshes a snapshot on a fixed 1s tick and computes
per-second deltas for network and disk I/O. Request handlers read the latest
snapshot under a lock. This decouples the UI interval from sampling cost — a 1s
UI setting does not increase load on the box.

## HTTP API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/resources` | cpu (total + per-core), memory, swap, disks, net rates, uptime, load |
| GET | `/api/processes` | pid, name, user, cpu%, mem%, rss, state, cmdline |
| GET | `/api/docker` | containers: name, image, status, cpu%, memory, ports |
| POST | `/api/docker/<id>/<start\|stop\|restart>` | action result — the only write endpoint |
| GET | `/api/files?path=<dir>` | entries: name, is_dir, size, mtime, mode |
| GET | `/` and `/static/*` | static files from disk |

Unknown routes return 404 JSON. Unsupported methods return 405.

## Views

Single page, hash-routed (`#/resources`, `#/processes`, `#/docker`, `#/files`):
bookmarkable URLs and a working back button without tearing down the poller on
each switch. Nav labels use the user's names.

**System Resource** — card grid: CPU with per-core bars, memory and swap as
used/total bars, load average and uptime, disk table (mount, filesystem,
used/total, % bar), network per interface with live up/down rates.

**System Processes** — sortable table (PID, user, CPU%, MEM%, RSS, state,
command). Sort by clicking a header; filter box matches name or PID. Read-only.
Rendered rows capped at 200 after sorting, so a 900-process box does not rebuild
900 DOM rows every second.

**Docker Processes** — container table with start/stop/restart per row. Buttons
disable while a request is in flight and the view refreshes on completion. If the
Docker socket is unreachable, the view shows "Docker not available" rather than
repeated errors.

**File Explorer** — breadcrumb of the current path plus a Back button; table of
name, size, modified, permissions; folders sorted first. Double-click a folder to
descend. Clicking a file does nothing. An unreadable directory shows "Permission
denied" inline and leaves the user where they are.

## Error handling

Errors are contained per view, never global. A failed poll keeps the
last-known values on screen with a small "stale" indicator rather than blanking
the page — a transient 500 must not wipe the CPU display.

Collectors tolerate per-item failure: a process that exits mid-iteration
(`psutil.NoSuchProcess`, `AccessDenied`) is skipped, not fatal; an unreadable
mount point is reported unavailable rather than raising.

**Path safety.** The `path` parameter is untrusted input. `files.py` resolves it
to an absolute real path and confirms it is a directory before listing, so
traversal sequences and symlink surprises cannot misrepresent the location.

**Command safety.** Docker actions invoke the `docker` CLI with the container ID
as a separate argv element — never interpolated into a shell string — and the ID
is validated against `^[a-zA-Z0-9_.-]+$` first. The action is checked against an
allowlist of exactly `start`, `stop`, `restart`.

## Testing

`pytest`. Most tests need no HTTP server, since collectors return plain dicts.

- Shape and type assertions for each collector.
- Rate arithmetic tested with two synthetic counter snapshots — the logic most
  likely to be subtly wrong.
- Failure paths with mocked `psutil` / `subprocess`: Docker absent, permission
  denied, process vanished mid-iteration.
- Path-safety tests: traversal attempts and a symlink pointing outside the
  listed directory.
- Docker ID validation rejects shell metacharacters.
- A few end-to-end route-table tests for status codes and JSON validity.

## Out of scope

Authentication and user accounts. HTTPS. Process signals (kill, renice, stop).
Docker logs and exec. File download, upload, edit, delete. Historical or
persisted metrics. Alerting. Multi-machine monitoring.
