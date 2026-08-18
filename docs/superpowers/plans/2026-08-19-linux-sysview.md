# linux-sysview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web-based Linux system monitor with four views — system resources, system processes, Docker processes, and a read-only file explorer.

**Architecture:** A Python 3 `ThreadingHTTPServer` serves a static HTML/CSS/JS
frontend and a small JSON API. Collector modules (`metrics`, `processes`,
`docker`, `files`) are pure functions returning plain dicts and never touch
HTTP; `server.py` routes and encodes JSON and never reads `/proc`. A background
sampler thread holds the latest CPU/network/disk snapshot so per-second rates
are correct regardless of request timing.

**Tech Stack:** Python 3.8+, `psutil`, stdlib `http.server`, vanilla JavaScript,
hand-written CSS, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-19-linux-system-resource-design.md`

## Global Constraints

- **Python 3.8+.** No `match` statements, no `X | Y` type unions in annotations.
- **Exactly one runtime dependency: `psutil`.** No web framework, no template
  engine, no frontend package manager, no build step.
- **Development happens on macOS; the app only runs on Linux.** Every test must
  pass on macOS. Tests must never read `/proc`, call `docker`, or assert on real
  system values — mock `psutil` and `subprocess` instead.
- **Only use `psutil.virtual_memory()` fields present on both macOS and Linux:**
  `total`, `available`, `percent`, `used`, `free`. `wired` is macOS-only;
  `buffers`, `cached`, `shared` are Linux-only. Touching either breaks the other
  platform.
- **Read-only except Docker.** No process signals (kill/renice/stop). No file
  writes, downloads, or deletes. The only state-changing endpoint is
  `POST /api/docker/<id>/<action>`.
- **Frontend uses no framework and loads no remote asset.** All CSS and JS are
  local files served from `sysview/static/`.
- **Always call `.resolve()` on pytest's `tmp_path` in filesystem tests.** On
  macOS `tmp_path` sits under `/private/var` but is handed to you via the `/var`
  symlink, so `list_directory()` correctly returns the resolved path and a naive
  `assert result["path"] == str(tmp_path)` fails for the wrong reason. Verified:
  the Task 7 tests fail on macOS without it.
- **All tests run with `.venv/bin/python -m pytest`** (Homebrew Python forbids
  global installs).

---

## File Structure

| File | Responsibility |
|---|---|
| `sysview/__init__.py` | Package marker, version string |
| `sysview/__main__.py` | CLI arg parsing (`--host`, `--port`, `--interval`), starts sampler + server |
| `sysview/sampler.py` | Background thread; holds latest CPU/net/disk snapshot, computes rates |
| `sysview/metrics.py` | CPU, memory, swap, disk, network, uptime, load collectors |
| `sysview/processes.py` | Process table snapshot |
| `sysview/docker.py` | Container listing and start/stop/restart via `docker` CLI |
| `sysview/files.py` | Directory listing, path resolution and safety guards |
| `sysview/server.py` | Route table, JSON responses, static file serving |
| `sysview/static/index.html` | Nav and four view containers |
| `sysview/static/style.css` | Dark theme, bars, tables |
| `sysview/static/app.js` | Hash router, polling loop, table rendering |
| `tests/test_*.py` | One test module per collector, plus routing tests |

Rationale: each collector is independently testable and small enough to read in
one sitting. `server.py` depends on collectors; no collector imports `server`.

---

### Task 1: Project scaffolding and dev environment

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`
- Create: `sysview/__init__.py`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `sysview` package with `sysview.__version__` (str).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_package.py
import sysview


def test_package_exposes_version():
    assert isinstance(sysview.__version__, str)
    assert sysview.__version__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_package.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview'`

- [ ] **Step 3: Create the package and config files**

```python
# sysview/__init__.py
"""A lightweight web-based Linux system monitor."""

__version__ = "0.1.0"
```

```
# requirements.txt
psutil>=5.9
```

```
# requirements-dev.txt
-r requirements.txt
pytest>=7.0
```

```ini
# pytest.ini
[pytest]
testpaths = tests
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_package.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini sysview/__init__.py tests/test_package.py
git commit -m "feat: add package scaffolding and dev requirements"
```

---

### Task 2: Rate calculation

Isolated first because it is pure arithmetic with no psutil involvement, and it
is the logic most likely to be subtly wrong.

**Files:**
- Create: `sysview/sampler.py`
- Test: `tests/test_rates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `compute_rate(prev_value: int, cur_value: int, elapsed: float) -> float`
  — bytes per second, `0.0` when elapsed <= 0 or the counter went backwards.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rates.py
from sysview.sampler import compute_rate


def test_rate_is_delta_over_elapsed():
    assert compute_rate(1000, 3000, 2.0) == 1000.0


def test_rate_zero_when_no_change():
    assert compute_rate(5000, 5000, 1.0) == 0.0


def test_rate_zero_when_elapsed_is_zero():
    # First sample has no previous timestamp; must not divide by zero.
    assert compute_rate(0, 5000, 0.0) == 0.0


def test_rate_zero_when_counter_resets():
    # Interface counters reset on reconnect; a negative delta is not a rate.
    assert compute_rate(9000, 100, 1.0) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.sampler'`

- [ ] **Step 3: Write minimal implementation**

```python
# sysview/sampler.py
"""Background sampling of counter-based system metrics."""


def compute_rate(prev_value, cur_value, elapsed):
    """Return per-second rate between two counter readings.

    Returns 0.0 when elapsed time is non-positive or the counter decreased
    (which happens when an interface resets), since neither yields a
    meaningful rate.
    """
    if elapsed <= 0:
        return 0.0
    delta = cur_value - prev_value
    if delta < 0:
        return 0.0
    return delta / elapsed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rates.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/sampler.py tests/test_rates.py
git commit -m "feat: add per-second rate calculation with reset handling"
```

---

### Task 3: Sampler thread

**Files:**
- Modify: `sysview/sampler.py`
- Test: `tests/test_sampler.py`

**Interfaces:**
- Consumes: `compute_rate` from Task 2.
- Produces: class `Sampler(interval: float = 1.0)` with:
  - `start() -> None` — launches the daemon thread
  - `stop() -> None` — signals shutdown and joins
  - `snapshot() -> dict` — thread-safe copy of the latest sample
  - `sample_once() -> None` — takes one sample synchronously (used by tests)

  `snapshot()` returns
  `{"cpu_percent": float, "cpu_per_core": list, "net": {iface: {"sent_rate": float, "recv_rate": float}}, "disk_io": {"read_rate": float, "write_rate": float}, "timestamp": float}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sampler.py
from unittest.mock import patch

from sysview.sampler import Sampler


class FakeNetIO:
    def __init__(self, sent, recv):
        self.bytes_sent = sent
        self.bytes_recv = recv
        self.packets_sent = 0
        self.packets_recv = 0


class FakeDiskIO:
    def __init__(self, read, write):
        self.read_bytes = read
        self.write_bytes = write


def test_snapshot_before_sampling_has_safe_defaults():
    s = Sampler()
    snap = s.snapshot()
    assert snap["cpu_percent"] == 0.0
    assert snap["cpu_per_core"] == []
    assert snap["net"] == {}


def test_two_samples_produce_net_rates():
    s = Sampler()
    times = [100.0, 102.0]
    net_readings = [
        {"eth0": FakeNetIO(1000, 2000)},
        {"eth0": FakeNetIO(3000, 6000)},
    ]
    with patch("sysview.sampler.time.monotonic", side_effect=times), \
         patch("sysview.sampler.psutil.net_io_counters", side_effect=net_readings), \
         patch("sysview.sampler.psutil.cpu_percent", return_value=[10.0, 20.0]), \
         patch("sysview.sampler.psutil.disk_io_counters", return_value=FakeDiskIO(0, 0)):
        s.sample_once()
        s.sample_once()

    snap = s.snapshot()
    # 2000 bytes sent over 2 seconds, 4000 received over 2 seconds.
    assert snap["net"]["eth0"]["sent_rate"] == 1000.0
    assert snap["net"]["eth0"]["recv_rate"] == 2000.0
    assert snap["cpu_per_core"] == [10.0, 20.0]
    assert snap["cpu_percent"] == 15.0


def test_snapshot_returns_a_copy():
    s = Sampler()
    first = s.snapshot()
    first["cpu_percent"] = 99.0
    assert s.snapshot()["cpu_percent"] == 0.0


def test_missing_disk_io_counters_does_not_raise():
    # disk_io_counters() returns None on some systems and in containers.
    s = Sampler()
    with patch("sysview.sampler.time.monotonic", return_value=1.0), \
         patch("sysview.sampler.psutil.net_io_counters", return_value={}), \
         patch("sysview.sampler.psutil.cpu_percent", return_value=[]), \
         patch("sysview.sampler.psutil.disk_io_counters", return_value=None):
        s.sample_once()
    assert s.snapshot()["disk_io"]["read_rate"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sampler.py -v`
Expected: FAIL — `ImportError: cannot import name 'Sampler'`

- [ ] **Step 3: Write the implementation**

Add to `sysview/sampler.py` (keep `compute_rate` as written in Task 2):

```python
import copy
import threading
import time

import psutil


def _empty_snapshot():
    return {
        "cpu_percent": 0.0,
        "cpu_per_core": [],
        "net": {},
        "disk_io": {"read_rate": 0.0, "write_rate": 0.0},
        "timestamp": 0.0,
    }


class Sampler:
    """Samples counter-based metrics on a fixed tick.

    CPU percentages and I/O counters are only meaningful as deltas between
    readings, so a single background thread samples at a steady interval and
    request handlers read the most recent result. This keeps the UI refresh
    rate independent of sampling cost.
    """

    def __init__(self, interval=1.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._snapshot = _empty_snapshot()
        self._prev_net = None
        self._prev_disk = None
        self._prev_time = None
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 2)
            self._thread = None

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _run(self):
        # Prime cpu_percent so the first real reading is a delta, not a
        # since-boot average.
        psutil.cpu_percent(percpu=True)
        while not self._stop_event.is_set():
            try:
                self.sample_once()
            except Exception:
                # A sampling failure must never kill the thread; the UI shows
                # the previous snapshot as stale instead.
                pass
            self._stop_event.wait(self.interval)

    def sample_once(self):
        now = time.monotonic()
        elapsed = 0.0 if self._prev_time is None else now - self._prev_time

        per_core = list(psutil.cpu_percent(percpu=True))
        total = sum(per_core) / len(per_core) if per_core else 0.0

        cur_net = psutil.net_io_counters(pernic=True)
        net = {}
        for iface, cur in cur_net.items():
            prev = (self._prev_net or {}).get(iface)
            if prev is None:
                net[iface] = {"sent_rate": 0.0, "recv_rate": 0.0}
            else:
                net[iface] = {
                    "sent_rate": compute_rate(prev.bytes_sent, cur.bytes_sent, elapsed),
                    "recv_rate": compute_rate(prev.bytes_recv, cur.bytes_recv, elapsed),
                }

        cur_disk = psutil.disk_io_counters()
        if cur_disk is None or self._prev_disk is None:
            disk_io = {"read_rate": 0.0, "write_rate": 0.0}
        else:
            disk_io = {
                "read_rate": compute_rate(
                    self._prev_disk.read_bytes, cur_disk.read_bytes, elapsed
                ),
                "write_rate": compute_rate(
                    self._prev_disk.write_bytes, cur_disk.write_bytes, elapsed
                ),
            }

        with self._lock:
            self._snapshot = {
                "cpu_percent": round(total, 1),
                "cpu_per_core": per_core,
                "net": net,
                "disk_io": disk_io,
                "timestamp": now,
            }
        self._prev_net = cur_net
        self._prev_disk = cur_disk
        self._prev_time = now
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sampler.py tests/test_rates.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/sampler.py tests/test_sampler.py
git commit -m "feat: add background sampler thread for CPU and I/O rates"
```

---

### Task 4: Resource metrics collector

**Files:**
- Create: `sysview/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `Sampler.snapshot()` from Task 3 (passed in as a dict).
- Produces: `collect_resources(snapshot: dict) -> dict` returning keys
  `cpu`, `memory`, `swap`, `disks`, `network`, `uptime_seconds`, `load_average`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
from collections import namedtuple
from contextlib import ExitStack
from unittest.mock import patch
import pytest
from sysview.metrics import collect_resources

VMem=namedtuple("VMem","total available percent used free")
SMem=namedtuple("SMem","total used free percent sin sout")
DUsage=namedtuple("DUsage","total used free percent")
Part=namedtuple("Part","device mountpoint fstype opts")
SNAPSHOT={"cpu_percent":12.5,"cpu_per_core":[10.0,15.0],
          "net":{"eth0":{"sent_rate":100.0,"recv_rate":200.0}},
          "disk_io":{"read_rate":1.0,"write_rate":2.0},"timestamp":5.0}

@pytest.fixture
def fake_system():
    def _apply(stack, partitions, usage_side_effect):
        p = lambda t, **k: stack.enter_context(patch("sysview.metrics."+t, **k))
        p("psutil.virtual_memory", return_value=VMem(16000,8000,50.0,8000,8000))
        p("psutil.swap_memory", return_value=SMem(2000,500,1500,25.0,0,0))
        p("psutil.disk_partitions", return_value=partitions)
        p("psutil.disk_usage", side_effect=usage_side_effect)
        p("psutil.boot_time", return_value=1000.0)
        p("psutil.cpu_count", return_value=2)
        p("time.time", return_value=4600.0)
        p("psutil.getloadavg", return_value=(0.5,0.4,0.3))
    return _apply

def test_collect_resources_shape(fake_system):
    with ExitStack() as stack:
        fake_system(stack,[Part("/dev/sda1","/","ext4","rw")],[DUsage(100,40,60,40.0)])
        r = collect_resources(SNAPSHOT)
    assert r["cpu"]["percent"]==12.5
    assert r["cpu"]["per_core"]==[10.0,15.0]
    assert r["memory"]["total"]==16000
    assert r["swap"]["total"]==2000
    assert r["uptime_seconds"]==3600.0
    assert r["load_average"]==[0.5,0.4,0.3]
    assert r["network"]["eth0"]["recv_rate"]==200.0
    assert r["disks"][0]["mountpoint"]=="/"
    assert r["disks"][0]["percent"]==40.0

def test_unreadable_mount_is_skipped_not_fatal(fake_system):
    parts=[Part("/dev/sda1","/","ext4","rw"),Part("/dev/sr0","/mnt/cd","iso9660","ro")]
    with ExitStack() as stack:
        fake_system(stack,parts,[DUsage(100,40,60,40.0),PermissionError("denied")])
        r = collect_resources(SNAPSHOT)
    mounts=[d["mountpoint"] for d in r["disks"]]
    assert "/" in mounts and "/mnt/cd" not in mounts

def test_missing_getloadavg_is_tolerated(fake_system):
    with ExitStack() as stack:
        fake_system(stack,[],[])
        stack.enter_context(patch("sysview.metrics.psutil.getloadavg", side_effect=OSError))
        r = collect_resources(SNAPSHOT)
    assert r["load_average"]==[0.0,0.0,0.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.metrics'`

- [ ] **Step 3: Write the implementation**

```python
# sysview/metrics.py
"""Point-in-time system resource collectors.

Only reads psutil fields that exist on both Linux and macOS, so the same code
can be unit-tested on a development Mac and run on the target Linux host.
"""

import time

import psutil

# Pseudo-filesystems that are not interesting as "disks".
_SKIP_FSTYPES = {
    "autofs", "cgroup", "cgroup2", "configfs", "debugfs", "devpts", "devtmpfs",
    "efivarfs", "fuse.gvfsd-fuse", "fusectl", "hugetlbfs", "mqueue", "overlay",
    "proc", "pstore", "securityfs", "squashfs", "sysfs", "tracefs",
}


def _memory():
    vm = psutil.virtual_memory()
    # Deliberately limited to cross-platform fields; `wired` is macOS-only and
    # `buffers`/`cached` are Linux-only.
    return {
        "total": vm.total,
        "available": vm.available,
        "used": vm.used,
        "free": vm.free,
        "percent": vm.percent,
    }


def _swap():
    sm = psutil.swap_memory()
    return {
        "total": sm.total,
        "used": sm.used,
        "free": sm.free,
        "percent": sm.percent,
    }


def _disks():
    disks = []
    for part in psutil.disk_partitions():
        if part.fstype in _SKIP_FSTYPES or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            # Unreadable or disconnected mount: omit it rather than fail the
            # entire view.
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
        })
    return disks


def _load_average():
    try:
        return [round(v, 2) for v in psutil.getloadavg()]
    except (OSError, AttributeError):
        return [0.0, 0.0, 0.0]


def collect_resources(snapshot):
    """Build the payload for the System Resource view.

    `snapshot` is a Sampler snapshot supplying rate-based values (CPU percent,
    network throughput) that cannot be computed from a single reading.
    """
    return {
        "cpu": {
            "percent": snapshot.get("cpu_percent", 0.0),
            "per_core": snapshot.get("cpu_per_core", []),
            "count": psutil.cpu_count() or 0,
        },
        "memory": _memory(),
        "swap": _swap(),
        "disks": _disks(),
        "disk_io": snapshot.get("disk_io", {"read_rate": 0.0, "write_rate": 0.0}),
        "network": snapshot.get("net", {}),
        "uptime_seconds": round(time.time() - psutil.boot_time(), 1),
        "load_average": _load_average(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/metrics.py tests/test_metrics.py
git commit -m "feat: add system resource metrics collector"
```

---

### Task 5: Process collector

**Files:**
- Create: `sysview/processes.py`
- Test: `tests/test_processes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `collect_processes(limit: int = 200) -> dict` returning
  `{"processes": list, "total": int}`. Each entry has `pid`, `name`, `user`,
  `cpu_percent`, `memory_percent`, `rss`, `status`, `cmdline`. Sorted by
  `cpu_percent` descending; `total` is the full count before truncation.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_processes.py
from unittest.mock import patch
import psutil
from sysview.processes import collect_processes

class FakeMemInfo:
    def __init__(self, rss): self.rss = rss

class FakeProc:
    def __init__(self, pid, name, cpu, mem_pct, rss):
        self.info = {"pid":pid,"name":name,"username":"root","cpu_percent":cpu,
                     "memory_percent":mem_pct,"status":"running",
                     "memory_info":FakeMemInfo(rss),
                     "cmdline":[name,"--flag"] if name else None}

def test_processes_sorted_by_cpu_desc():
    procs=[FakeProc(1,"init",0.5,0.1,1000),FakeProc(2,"hog",90.0,5.0,500000),FakeProc(3,"idle",1.0,0.2,2000)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r=collect_processes()
    assert [p["name"] for p in r["processes"]]==["hog","idle","init"]
    assert r["total"]==3
    assert r["processes"][0]["rss"]==500000
    assert r["processes"][0]["cmdline"]=="hog --flag"
    assert r["processes"][0]["user"]=="root"

def test_limit_truncates_but_total_reports_full_count():
    procs=[FakeProc(i,"p%d"%i,float(i),0.1,100) for i in range(10)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r=collect_processes(limit=3)
    assert len(r["processes"])==3 and r["total"]==10

def test_vanished_process_is_skipped():
    good=FakeProc(1,"alive",1.0,0.1,100)
    def _iter(attrs=None):
        yield good
        raise psutil.NoSuchProcess(999)
    with patch("sysview.processes.psutil.process_iter", side_effect=_iter):
        r=collect_processes()
    assert [p["name"] for p in r["processes"]]==["alive"]

def test_missing_fields_get_safe_defaults():
    proc=FakeProc(1,None,None,None,0)
    proc.info["username"]=None; proc.info["memory_info"]=None
    with patch("sysview.processes.psutil.process_iter", return_value=iter([proc])):
        r=collect_processes()
    e=r["processes"][0]
    assert e["cpu_percent"]==0.0 and e["user"]=="?" and e["cmdline"]=="" and e["rss"]==0 and e["name"]=="?"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_processes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.processes'`

- [ ] **Step 3: Write the implementation**

```python
# sysview/processes.py
"""Read-only process table snapshot."""

import psutil

_ATTRS = [
    "pid", "name", "username", "cpu_percent", "memory_percent",
    "status", "memory_info", "cmdline",
]


def _entry(proc):
    info = proc.info
    mem_info = info.get("memory_info")
    cmdline = info.get("cmdline")
    return {
        "pid": info.get("pid"),
        "name": info.get("name") or "?",
        "user": info.get("username") or "?",
        "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
        "memory_percent": round(info.get("memory_percent") or 0.0, 2),
        "rss": getattr(mem_info, "rss", 0) or 0,
        "status": info.get("status") or "?",
        "cmdline": " ".join(cmdline) if cmdline else "",
    }


def collect_processes(limit=200):
    """Return processes sorted by CPU usage, descending.

    Only `limit` entries are returned so the browser does not rebuild a
    thousand table rows per refresh, but `total` always reports the true count.
    """
    entries = []
    try:
        for proc in psutil.process_iter(_ATTRS):
            try:
                entries.append(_entry(proc))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Process exited or is not ours to inspect; skip it.
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    entries.sort(key=lambda e: e["cpu_percent"], reverse=True)
    return {"processes": entries[:limit], "total": len(entries)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_processes.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/processes.py tests/test_processes.py
git commit -m "feat: add read-only process table collector"
```

---

### Task 6: Docker container listing and actions

**Files:**
- Create: `sysview/docker.py`
- Test: `tests/test_docker.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `VALID_ACTIONS: frozenset` — exactly `{"start", "stop", "restart"}`
  - `is_valid_container_id(value: str) -> bool`
  - `collect_containers() -> dict` — `{"available": bool, "containers": list, "error": str}`
  - `run_action(container_id: str, action: str) -> dict` — `{"ok": bool, "error": str}`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_docker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.docker'`

- [ ] **Step 3: Write the implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_docker.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/docker.py tests/test_docker.py
git commit -m "feat: add docker container listing and lifecycle actions"
```

---

### Task 7: File explorer collector

**Files:**
- Create: `sysview/files.py`
- Test: `tests/test_files.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `list_directory(path: str) -> dict` returning
  `{"path": str, "parent": str or None, "entries": list, "error": str}`.
  Each entry: `name`, `is_dir`, `size`, `mtime`, `mode`. Directories sort
  before files, each group alphabetically. `parent` is `None` at `/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_files.py
import os
import pytest
from sysview.files import list_directory

@pytest.fixture
def tree(tmp_path):
    real = tmp_path.resolve()
    (real/"adir").mkdir(); (real/"zdir").mkdir()
    (real/"bfile.txt").write_text("hello"); (real/"afile.txt").write_text("hi")
    return real

def test_lists_directories_before_files_alphabetically(tree):
    r=list_directory(str(tree))
    assert [e["name"] for e in r["entries"]]==["adir","zdir","afile.txt","bfile.txt"]
    assert r["error"]==""

def test_entry_metadata(tree):
    by={e["name"]:e for e in list_directory(str(tree))["entries"]}
    assert by["adir"]["is_dir"] is True
    assert by["bfile.txt"]["is_dir"] is False
    assert by["bfile.txt"]["size"]==5
    assert by["bfile.txt"]["mtime"]>0
    assert len(by["bfile.txt"]["mode"])==9

def test_parent_is_none_at_root():
    r=list_directory("/")
    assert r["parent"] is None and r["path"]=="/"

def test_parent_points_one_level_up(tree):
    assert list_directory(str(tree))["parent"]==str(tree.parent)

def test_traversal_is_resolved_not_escaped(tree):
    r=list_directory(str(tree/"adir"/".."))
    assert r["path"]==str(tree) and ".." not in r["path"]

def test_symlink_reports_resolved_target_as_path(tmp_path):
    base=tmp_path.resolve()
    real=base/"real"; real.mkdir(); (real/"inside.txt").write_text("x")
    os.symlink(real, base/"link")
    r=list_directory(str(base/"link"))
    assert r["path"]==str(real)
    assert [e["name"] for e in r["entries"]]==["inside.txt"]

def test_nonexistent_path_returns_error_not_exception():
    r=list_directory("/definitely/does/not/exist/anywhere")
    assert r["entries"]==[] and "not found" in r["error"].lower()

def test_file_path_returns_error(tree):
    r=list_directory(str(tree/"bfile.txt"))
    assert r["entries"]==[] and "not a directory" in r["error"].lower()

def test_unreadable_directory_returns_permission_error(tmp_path):
    if os.geteuid()==0: pytest.skip("root bypasses directory permissions")
    locked=tmp_path/"locked"; locked.mkdir(); os.chmod(locked,0o000)
    try:
        r=list_directory(str(locked))
        assert r["entries"]==[] and "permission denied" in r["error"].lower()
    finally: os.chmod(locked,0o755)

def test_unstattable_entry_is_listed_with_zero_size(tmp_path):
    os.symlink(tmp_path/"missing-target", tmp_path/"broken")
    e=list_directory(str(tmp_path))["entries"][0]
    assert e["name"]=="broken" and e["size"]==0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_files.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.files'`

- [ ] **Step 3: Write the implementation**

```python
# sysview/files.py
"""Read-only directory listing for the file explorer.

The requested path is untrusted input, so it is always resolved to a real
absolute path before use and the resolved value is what gets reported back.
Nothing here opens, reads, writes, or deletes file contents.
"""

import os
import stat


def _mode_string(mode):
    """Render the permission bits like the tail of `ls -l`, e.g. rw-r--r--."""
    chars = []
    for who in ("USR", "GRP", "OTH"):
        for what, ch in (("R", "r"), ("W", "w"), ("X", "x")):
            bit = getattr(stat, "S_I%s%s" % (what, who))
            chars.append(ch if mode & bit else "-")
    return "".join(chars)


def _entry(dir_entry):
    name = dir_entry.name
    try:
        is_dir = dir_entry.is_dir()
    except OSError:
        is_dir = False
    try:
        st = dir_entry.stat()
        size = 0 if is_dir else st.st_size
        mtime = st.st_mtime
        mode = _mode_string(st.st_mode)
    except OSError:
        # Broken symlink or vanished entry: still list it, with no metadata.
        size, mtime, mode = 0, 0.0, "---------"
    return {
        "name": name,
        "is_dir": is_dir,
        "size": size,
        "mtime": mtime,
        "mode": mode,
    }


def _error(path, message):
    return {"path": path, "parent": None, "entries": [], "error": message}


def list_directory(path):
    """List a directory's immediate children.

    Returns an error string rather than raising, so a bad path leaves the UI
    on its previous location with a message.
    """
    raw = path or "/"
    try:
        resolved = os.path.realpath(raw)
    except OSError:
        return _error(raw, "Invalid path")

    if not os.path.exists(resolved):
        return _error(resolved, "Path not found")
    if not os.path.isdir(resolved):
        return _error(resolved, "Not a directory")

    try:
        with os.scandir(resolved) as it:
            entries = [_entry(e) for e in it]
    except PermissionError:
        return _error(resolved, "Permission denied")
    except OSError as exc:
        return _error(resolved, "Cannot read directory: %s" % exc)

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    parent = os.path.dirname(resolved)
    if resolved == os.path.sep or parent == resolved:
        parent = None

    return {"path": resolved, "parent": parent, "entries": entries, "error": ""}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_files.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/files.py tests/test_files.py
git commit -m "feat: add read-only directory listing with path resolution"
```

---

### Task 8: HTTP server and routing

**Files:**
- Create: `sysview/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Sampler` (Task 3), `collect_resources` (Task 4),
  `collect_processes` (Task 5), `collect_containers`/`run_action`/
  `VALID_ACTIONS` (Task 6), `list_directory` (Task 7).
- Produces:
  - `route_get(path: str, query: dict, sampler) -> tuple(int, dict)`
  - `route_post(path: str) -> tuple(int, dict)`
  - `make_server(host: str, port: int, sampler) -> ThreadingHTTPServer`

  Routing is factored out of the handler class so it can be tested without
  opening a socket.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.server'`

- [ ] **Step 3: Write the implementation**

```python
# sysview/server.py
"""HTTP layer: routing, JSON encoding, and static file serving.

Routing is a plain function so it can be tested without binding a socket. This
module never reads /proc or invokes docker directly; it only calls collectors.
"""

import json
import os
import posixpath
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .docker import VALID_ACTIONS, collect_containers, is_valid_container_id, run_action
from .files import list_directory
from .metrics import collect_resources
from .processes import collect_processes

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def route_get(path, query, sampler):
    """Return (status_code, payload_dict) for a GET API path."""
    if path == "/api/resources":
        return 200, collect_resources(sampler.snapshot())
    if path == "/api/processes":
        return 200, collect_processes()
    if path == "/api/docker":
        # An unreachable Docker daemon is a normal state reported in the body,
        # not an HTTP error.
        return 200, collect_containers()
    if path == "/api/files":
        requested = query.get("path", ["/"])[0] or "/"
        return 200, list_directory(requested)
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

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            status, payload = route_get(
                parsed.path, parse_qs(parsed.query), self.sampler
            )
            self._send_json(status, payload)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            status, payload = route_post(parsed.path)
            self._send_json(status, payload)
            return
        self._send_json(404, {"ok": False, "error": "Not found"})

    def translate_path(self, path):
        # Serve only from STATIC_DIR; SimpleHTTPRequestHandler already strips
        # traversal, but this keeps the root explicit.
        path = posixpath.normpath(urlparse(path).path)
        parts = [p for p in path.split("/") if p and p not in (os.curdir, os.pardir)]
        return os.path.join(STATIC_DIR, *parts)


def make_server(host, port, sampler):
    """Build (but do not start) the HTTP server."""
    handler = type("BoundHandler", (Handler,), {"sampler": sampler})
    return ThreadingHTTPServer((host, port), handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_server.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/server.py tests/test_server.py
git commit -m "feat: add HTTP routing and static file serving"
```

---

### Task 9: CLI entry point

**Files:**
- Create: `sysview/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `make_server` (Task 8), `Sampler` (Task 3).
- Produces: `parse_args(argv: list) -> argparse.Namespace` with `host`, `port`,
  `interval`; and `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import pytest

from sysview.__main__ import parse_args


def test_defaults():
    args = parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8080
    assert args.interval == 2.0


def test_overrides():
    args = parse_args(["--host", "127.0.0.1", "--port", "9000", "--interval", "5"])
    assert args.host == "127.0.0.1"
    assert args.port == 9000
    assert args.interval == 5.0


def test_rejects_invalid_port():
    with pytest.raises(SystemExit):
        parse_args(["--port", "not-a-number"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sysview.__main__'`

- [ ] **Step 3: Write the implementation**

```python
# sysview/__main__.py
"""Command-line entry point: python -m sysview"""

import argparse
import sys

from . import __version__
from .sampler import Sampler
from .server import make_server


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="sysview",
        description="Web-based Linux system resource and process viewer.",
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to bind (default: 0.0.0.0; use 127.0.0.1 for localhost only)")
    parser.add_argument("--port", type=int, default=8080,
                        help="port to listen on (default: 8080)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="default UI refresh interval in seconds (default: 2)")
    parser.add_argument("--version", action="version", version="sysview %s" % __version__)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    sampler = Sampler(interval=1.0)
    sampler.start()

    try:
        httpd = make_server(args.host, args.port, sampler)
    except OSError as exc:
        sampler.stop()
        print("Cannot bind %s:%d — %s" % (args.host, args.port, exc), file=sys.stderr)
        return 1

    print("sysview %s serving on http://%s:%d" % (__version__, args.host, args.port))
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.shutdown()
        httpd.server_close()
        sampler.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/__main__.py tests/test_cli.py
git commit -m "feat: add CLI entry point"
```

---

### Task 10: Frontend shell — HTML, CSS, hash router

**Files:**
- Create: `sysview/static/index.html`, `sysview/static/style.css`, `sysview/static/app.js`
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: the JSON API from Task 8.
- Produces: four view containers with ids `view-resources`, `view-processes`,
  `view-docker`, `view-files`; hash routes `#/resources`, `#/processes`,
  `#/docker`, `#/files`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_static.py -v`
Expected: FAIL — static files do not exist

- [ ] **Step 3: Write the files**

```html
<!-- sysview/static/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sysview</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header>
  <h1>sysview</h1>
  <nav>
    <a href="#/resources" data-view="resources">System Resource</a>
    <a href="#/processes" data-view="processes">System Processes</a>
    <a href="#/docker" data-view="docker">Docker Processes</a>
    <a href="#/files" data-view="files">File Explorer</a>
  </nav>
  <div class="controls">
    <label>Refresh
      <select id="interval">
        <option value="1">1s</option>
        <option value="2" selected>2s</option>
        <option value="5">5s</option>
        <option value="10">10s</option>
        <option value="0">Paused</option>
      </select>
    </label>
    <span id="status"></span>
  </div>
</header>

<main>
  <section id="view-resources" class="view"></section>
  <section id="view-processes" class="view">
    <div class="toolbar">
      <input id="proc-filter" type="search" placeholder="Filter by name or PID">
      <span id="proc-count"></span>
    </div>
    <div class="table-wrap"><table id="proc-table"></table></div>
  </section>
  <section id="view-docker" class="view">
    <div class="table-wrap"><table id="docker-table"></table></div>
  </section>
  <section id="view-files" class="view">
    <div class="toolbar">
      <button id="files-back" type="button">Back</button>
      <span id="files-path" class="breadcrumb"></span>
    </div>
    <div id="files-error" class="error"></div>
    <div class="table-wrap"><table id="files-table"></table></div>
  </section>
</main>

<script src="/app.js"></script>
</body>
</html>
```

```css
/* sysview/static/style.css */
:root {
  --bg: #14161a;
  --panel: #1c1f26;
  --border: #2b2f38;
  --text: #e6e8ec;
  --muted: #8b91a1;
  --accent: #4c9aff;
  --bar: #2f3542;
  --ok: #35c46b;
  --warn: #f5a623;
  --crit: #e5484d;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
}

h1 { font-size: 15px; margin: 0; color: var(--accent); }

nav { display: flex; gap: 4px; flex-wrap: wrap; }

nav a {
  color: var(--muted);
  text-decoration: none;
  padding: 5px 10px;
  border-radius: 4px;
}

nav a:hover { background: var(--bar); color: var(--text); }
nav a.active { background: var(--accent); color: #fff; }

.controls { margin-left: auto; display: flex; align-items: center; gap: 10px; }

select, input, button {
  background: var(--bar);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
  font: inherit;
}

button { cursor: pointer; }
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: 0.5; cursor: default; }

#status { color: var(--warn); font-size: 12px; min-width: 60px; }

main { padding: 16px; }
.view { display: none; }
.view.active { display: block; }

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
}

.card h2 {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin: 0 0 10px;
}

.metric { display: flex; justify-content: space-between; margin-bottom: 4px; }
.metric .label { color: var(--muted); }

.bar {
  height: 7px;
  background: var(--bar);
  border-radius: 4px;
  overflow: hidden;
  margin: 3px 0 9px;
}

.bar > span {
  display: block;
  height: 100%;
  background: var(--ok);
  transition: width 0.3s;
}

.bar.warn > span { background: var(--warn); }
.bar.crit > span { background: var(--crit); }

.cores { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 6px; }
.core { font-size: 11px; color: var(--muted); }

.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
#proc-filter { min-width: 240px; }
#proc-count, .breadcrumb { color: var(--muted); font-size: 12px; }
.breadcrumb { word-break: break-all; }

.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; }

table { width: 100%; border-collapse: collapse; background: var(--panel); }

th, td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

th { color: var(--muted); font-weight: normal; cursor: pointer; user-select: none; position: sticky; top: 0; background: var(--panel); }
th:hover { color: var(--text); }
tbody tr:hover { background: var(--bar); }
td.num, th.num { text-align: right; }
td.cmd { white-space: normal; word-break: break-all; color: var(--muted); }

tr.dir { cursor: pointer; }
tr.dir td:first-child { color: var(--accent); }
tr.file td:first-child { color: var(--text); }

.error { color: var(--crit); margin-bottom: 10px; min-height: 20px; }
.empty { padding: 16px; color: var(--muted); }
```

```javascript
// sysview/static/app.js
'use strict';

var VIEWS = ['resources', 'processes', 'docker', 'files'];
var ROUTES = {
  '#/resources': 'resources',
  '#/processes': 'processes',
  '#/docker': 'docker',
  '#/files': 'files'
};

var state = {
  view: 'resources',
  interval: 2,
  timer: null,
  filesPath: '/',
  filesParent: null,
  procSort: { key: 'cpu_percent', desc: true },
  procFilter: ''
};

// ---- helpers ----------------------------------------------------------

function el(id) { return document.getElementById(id); }

function bytes(n) {
  if (n === null || n === undefined) { return '-'; }
  var units = ['B', 'K', 'M', 'G', 'T', 'P'];
  var i = 0;
  var v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return (i === 0 ? v : v.toFixed(1)) + units[i];
}

function rate(n) { return bytes(n) + '/s'; }

function duration(seconds) {
  var s = Math.floor(seconds);
  var d = Math.floor(s / 86400);
  var h = Math.floor((s % 86400) / 3600);
  var m = Math.floor((s % 3600) / 60);
  if (d > 0) { return d + 'd ' + h + 'h ' + m + 'm'; }
  if (h > 0) { return h + 'h ' + m + 'm'; }
  return m + 'm';
}

function stamp(mtime) {
  if (!mtime) { return '-'; }
  var d = new Date(mtime * 1000);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}

function severity(pct) {
  if (pct >= 90) { return ' crit'; }
  if (pct >= 70) { return ' warn'; }
  return '';
}

function bar(pct) {
  var p = Math.max(0, Math.min(100, pct || 0));
  return '<div class="bar' + severity(p) + '"><span style="width:' + p + '%"></span></div>';
}

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setStatus(text) { el('status').textContent = text; }

function get(url) {
  return fetch(url, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) { throw new Error('HTTP ' + r.status); }
    return r.json();
  });
}

// ---- views -----------------------------------------------------------

function renderResources(d) {
  var cards = [];

  var cores = (d.cpu.per_core || []).map(function (v, i) {
    return '<div class="core">cpu' + i + ' ' + v.toFixed(0) + '%' + bar(v) + '</div>';
  }).join('');
  cards.push(
    '<div class="card"><h2>CPU</h2>' +
    '<div class="metric"><span class="label">Total (' + d.cpu.count + ' cores)</span><span>' +
    d.cpu.percent.toFixed(1) + '%</span></div>' + bar(d.cpu.percent) +
    '<div class="cores">' + cores + '</div></div>'
  );

  cards.push(
    '<div class="card"><h2>Memory</h2>' +
    '<div class="metric"><span class="label">Used</span><span>' +
    bytes(d.memory.used) + ' / ' + bytes(d.memory.total) + '</span></div>' +
    bar(d.memory.percent) +
    '<div class="metric"><span class="label">Available</span><span>' +
    bytes(d.memory.available) + '</span></div>' +
    '<div class="metric"><span class="label">Swap</span><span>' +
    bytes(d.swap.used) + ' / ' + bytes(d.swap.total) + '</span></div>' +
    bar(d.swap.percent) + '</div>'
  );

  var la = d.load_average;
  cards.push(
    '<div class="card"><h2>System</h2>' +
    '<div class="metric"><span class="label">Uptime</span><span>' +
    duration(d.uptime_seconds) + '</span></div>' +
    '<div class="metric"><span class="label">Load avg</span><span>' +
    la[0] + ' ' + la[1] + ' ' + la[2] + '</span></div>' +
    '<div class="metric"><span class="label">Disk read</span><span>' +
    rate(d.disk_io.read_rate) + '</span></div>' +
    '<div class="metric"><span class="label">Disk write</span><span>' +
    rate(d.disk_io.write_rate) + '</span></div></div>'
  );

  var disks = (d.disks || []).map(function (k) {
    return '<div class="metric"><span class="label">' + esc(k.mountpoint) +
      ' <small>' + esc(k.fstype) + '</small></span><span>' +
      bytes(k.used) + ' / ' + bytes(k.total) + '</span></div>' + bar(k.percent);
  }).join('') || '<div class="empty">No disks reported</div>';
  cards.push('<div class="card"><h2>Disks</h2>' + disks + '</div>');

  var nets = Object.keys(d.network || {}).sort().map(function (name) {
    var n = d.network[name];
    return '<div class="metric"><span class="label">' + esc(name) + '</span><span>&uarr; ' +
      rate(n.sent_rate) + ' &darr; ' + rate(n.recv_rate) + '</span></div>';
  }).join('') || '<div class="empty">No interfaces</div>';
  cards.push('<div class="card"><h2>Network</h2>' + nets + '</div>');

  el('view-resources').innerHTML = '<div class="cards">' + cards.join('') + '</div>';
}

var PROC_COLS = [
  { key: 'pid', label: 'PID', num: true },
  { key: 'user', label: 'User' },
  { key: 'cpu_percent', label: 'CPU%', num: true },
  { key: 'memory_percent', label: 'MEM%', num: true },
  { key: 'rss', label: 'RSS', num: true, fmt: bytes },
  { key: 'status', label: 'State' },
  { key: 'name', label: 'Name' },
  { key: 'cmdline', label: 'Command', cls: 'cmd' }
];

function renderProcesses(d) {
  var rows = d.processes || [];
  var q = state.procFilter.trim().toLowerCase();
  if (q) {
    rows = rows.filter(function (p) {
      return String(p.pid).indexOf(q) === 0 ||
        (p.name || '').toLowerCase().indexOf(q) !== -1;
    });
  }

  var s = state.procSort;
  rows = rows.slice().sort(function (a, b) {
    var x = a[s.key];
    var y = b[s.key];
    if (typeof x === 'string' || typeof y === 'string') {
      x = String(x).toLowerCase();
      y = String(y).toLowerCase();
    }
    if (x < y) { return s.desc ? 1 : -1; }
    if (x > y) { return s.desc ? -1 : 1; }
    return 0;
  });

  var head = PROC_COLS.map(function (c) {
    var mark = s.key === c.key ? (s.desc ? ' ▾' : ' ▴') : '';
    return '<th data-key="' + c.key + '"' + (c.num ? ' class="num"' : '') + '>' +
      c.label + mark + '</th>';
  }).join('');

  var body = rows.map(function (p) {
    return '<tr>' + PROC_COLS.map(function (c) {
      var v = c.fmt ? c.fmt(p[c.key]) : p[c.key];
      var cls = c.cls || (c.num ? 'num' : '');
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + esc(v) + '</td>';
    }).join('') + '</tr>';
  }).join('');

  el('proc-count').textContent = rows.length + ' shown of ' + d.total + ' total';
  el('proc-table').innerHTML = '<thead><tr>' + head + '</tr></thead><tbody>' +
    (body || '<tr><td colspan="8" class="empty">No processes match</td></tr>') +
    '</tbody>';
}

function renderDocker(d) {
  if (!d.available) {
    el('docker-table').innerHTML =
      '<tbody><tr><td class="empty">Docker not available &mdash; ' +
      esc(d.error) + '</td></tr></tbody>';
    return;
  }
  if (!d.containers.length) {
    el('docker-table').innerHTML =
      '<tbody><tr><td class="empty">No containers</td></tr></tbody>';
    return;
  }

  var head = '<thead><tr><th>Name</th><th>Image</th><th>State</th><th>Status</th>' +
    '<th class="num">CPU</th><th class="num">Memory</th><th>Ports</th><th>Actions</th></tr></thead>';

  var body = d.containers.map(function (c) {
    var running = c.state === 'running';
    var btn = function (action, label) {
      return '<button type="button" data-id="' + esc(c.id) + '" data-action="' +
        action + '">' + label + '</button>';
    };
    var actions = running
      ? btn('stop', 'Stop') + ' ' + btn('restart', 'Restart')
      : btn('start', 'Start');
    return '<tr><td>' + esc(c.name) + '</td><td>' + esc(c.image) + '</td><td>' +
      esc(c.state) + '</td><td>' + esc(c.status) + '</td><td class="num">' +
      esc(c.cpu_percent) + '</td><td class="num">' + esc(c.memory) + '</td><td>' +
      esc(c.ports) + '</td><td>' + actions + '</td></tr>';
  }).join('');

  el('docker-table').innerHTML = head + '<tbody>' + body + '</tbody>';
}

function renderFiles(d) {
  el('files-error').textContent = d.error || '';
  if (d.error) { return; }

  state.filesPath = d.path;
  state.filesParent = d.parent;
  el('files-path').textContent = d.path;
  el('files-back').disabled = !d.parent;

  var head = '<thead><tr><th>Name</th><th class="num">Size</th>' +
    '<th>Modified</th><th>Mode</th></tr></thead>';

  var body = (d.entries || []).map(function (e) {
    var cls = e.is_dir ? 'dir' : 'file';
    var name = (e.is_dir ? '▸ ' : '  ') + esc(e.name);
    return '<tr class="' + cls + '" data-name="' + esc(e.name) +
      '" data-dir="' + (e.is_dir ? '1' : '0') + '"><td>' + name +
      '</td><td class="num">' + (e.is_dir ? '-' : bytes(e.size)) +
      '</td><td>' + stamp(e.mtime) + '</td><td>' + esc(e.mode) + '</td></tr>';
  }).join('');

  el('files-table').innerHTML = head + '<tbody>' +
    (body || '<tr><td colspan="4" class="empty">Empty directory</td></tr>') + '</tbody>';
}

// ---- polling ---------------------------------------------------------

var LOADERS = {
  resources: function () { return get('/api/resources').then(renderResources); },
  processes: function () { return get('/api/processes').then(renderProcesses); },
  docker: function () { return get('/api/docker').then(renderDocker); },
  files: function () {
    return get('/api/files?path=' + encodeURIComponent(state.filesPath)).then(renderFiles);
  }
};

function refresh() {
  return LOADERS[state.view]().then(function () {
    setStatus('');
  }).catch(function (err) {
    // Keep the last good values on screen; just flag them as stale.
    setStatus('stale (' + err.message + ')');
  });
}

function schedule() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  if (state.interval > 0) {
    state.timer = setInterval(refresh, state.interval * 1000);
  }
}

// The file explorer is navigated, not monitored; re-polling it every 2s would
// fight the user's clicks for no benefit.
function isPolled(view) { return view !== 'files'; }

function showView(name) {
  state.view = name;
  VIEWS.forEach(function (v) {
    el('view-' + v).classList.toggle('active', v === name);
  });
  var links = document.querySelectorAll('nav a');
  Array.prototype.forEach.call(links, function (a) {
    a.classList.toggle('active', a.getAttribute('data-view') === name);
  });
  setStatus('');
  refresh();
  if (isPolled(name)) { schedule(); }
  else if (state.timer) { clearInterval(state.timer); state.timer = null; }
}

function onHashChange() {
  showView(ROUTES[window.location.hash] || 'resources');
}

// ---- events ----------------------------------------------------------

window.addEventListener('hashchange', onHashChange);

el('interval').addEventListener('change', function (e) {
  state.interval = parseFloat(e.target.value);
  if (isPolled(state.view)) { schedule(); }
});

el('proc-filter').addEventListener('input', function (e) {
  state.procFilter = e.target.value;
  refresh();
});

el('proc-table').addEventListener('click', function (e) {
  var th = e.target.closest('th');
  if (!th || !th.dataset.key) { return; }
  var key = th.dataset.key;
  if (state.procSort.key === key) { state.procSort.desc = !state.procSort.desc; }
  else { state.procSort = { key: key, desc: true }; }
  refresh();
});

el('docker-table').addEventListener('click', function (e) {
  var btn = e.target.closest('button');
  if (!btn) { return; }
  var buttons = el('docker-table').querySelectorAll('button');
  Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
  setStatus(btn.dataset.action + 'ing...');
  fetch('/api/docker/' + encodeURIComponent(btn.dataset.id) + '/' + btn.dataset.action,
        { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      setStatus(res.ok ? '' : res.error);
      return refresh();
    })
    .catch(function (err) { setStatus(err.message); })
    .then(function () {
      var again = el('docker-table').querySelectorAll('button');
      Array.prototype.forEach.call(again, function (b) { b.disabled = false; });
    });
});

el('files-table').addEventListener('dblclick', function (e) {
  var tr = e.target.closest('tr');
  if (!tr || tr.dataset.dir !== '1') { return; }
  var base = state.filesPath === '/' ? '' : state.filesPath;
  state.filesPath = base + '/' + tr.dataset.name;
  refresh();
});

el('files-back').addEventListener('click', function () {
  if (!state.filesParent) { return; }
  state.filesPath = state.filesParent;
  refresh();
});

onHashChange();
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_static.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add sysview/static/
git add tests/test_static.py
git commit -m "feat: add frontend shell with hash routing and four views"
```

---

### Task 11: End-to-end smoke test and README update

**Files:**
- Create: `tests/test_integration.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `make_server` (Task 8), `Sampler` (Task 3).
- Produces: nothing new; verifies the assembled server over a real socket.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_integration.py
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
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `.venv/bin/python -m pytest tests/test_integration.py -v`
Expected: PASS — every dependency exists by now. If anything fails, the defect
is in Tasks 8–10, not this test.

- [ ] **Step 3: Update the README status line**

Replace the status blockquote near the top of `README.md`:

```markdown
> **Status:** working. Tested on Linux; the test suite also runs on macOS.
> See [the design spec](docs/superpowers/specs/2026-08-19-linux-system-resource-design.md).
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS — all tests across all modules.

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration.py README.md
git commit -m "test: add end-to-end server smoke tests"
```

---

### Task 12: Verify on the Dell Linux box

The only task that cannot be done from the development Mac. Everything above is
mocked or cross-platform; this confirms the app works against real `/proc`,
real mounts, and a real Docker daemon.

**Files:** none (verification only; fixes get their own commits).

- [ ] **Step 1: Push and pull**

```bash
# on the Mac
git push origin main

# on the Dell box
git clone https://github.com/irfan-khairul/linux-sysview.git   # first time
cd linux-sysview && git pull
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

- [ ] **Step 2: Run the test suite on Linux**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS. A failure here means a macOS-only assumption slipped through —
most likely a `psutil.virtual_memory()` field, per Global Constraints.

- [ ] **Step 3: Start the server**

Run: `.venv/bin/python -m sysview`
Expected: `sysview 0.1.0 serving on http://0.0.0.0:8080`

- [ ] **Step 4: Check each view from the Mac's browser**

Open `http://<dell-ip>:8080` and confirm:
- **System Resource** — per-core bars move; memory matches `free -h`; disk rows
  match `df -h`; network rates rise when you copy a file.
- **System Processes** — count is plausible against `ps aux | wc -l`; sorting by
  each column works; the filter box narrows rows.
- **Docker Processes** — containers match `docker ps -a`; Stop then Start on a
  throwaway container works and the row updates.
- **File Explorer** — double-click descends; Back ascends; Back is disabled at
  `/`; a file click does nothing; `/root` as a non-root user shows
  "Permission denied" without breaking the view.

- [ ] **Step 5: Record the outcome**

Note anything broken and fix it with a normal test-first cycle. If everything
works, no commit is needed for this task.

---

## Self-Review

**1. Spec coverage**

| Spec requirement | Task |
|---|---|
| Python 3 + psutil, one dependency | 1 |
| Sampler thread, rates from counters | 2, 3 |
| CPU / memory / swap / disk / network / uptime / load | 4 |
| Process table, read-only, 200-row cap | 5 |
| Docker list + start/stop/restart, argv safety, id validation | 6 |
| File explorer, path resolution, folders first, permission errors | 7 |
| Route table, all six endpoints, 404/405 | 8 |
| `--host` / `--port` / `--interval` | 9 |
| Four hash-routed views, no framework, no remote assets | 10 |
| Per-view error containment, stale indicator | 10 (`refresh`) |
| Testing: shapes, rate arithmetic, failure paths, path safety, ID validation, routing | 2–11 |
| Linux verification | 12 |

No uncovered requirement found.

**2. Placeholder scan** — no TBD/TODO; every code step contains complete code.

**2b. Executed validation** — the test blocks for Tasks 4, 5, and 7 were not just
written but run against their implementations on macOS (Python 3.14.6, psutil
7.2.2, pytest 9.1.1): 3, 4, and 10 tests pass respectively. Two defects were
found and fixed this way: Task 4's original test called its patch helper once per
patcher (now a single `ExitStack` fixture), and Task 7's tests failed on macOS
until `tmp_path` was resolved — now a Global Constraint. Tasks 2, 3, 6, and 8–11
are reviewed but not executed; their implementer runs them first.

**3. Type consistency** — `compute_rate(prev, cur, elapsed)` (Task 2) is called
with that signature in Task 3. `Sampler.snapshot()` keys (`cpu_percent`,
`cpu_per_core`, `net`, `disk_io`, `timestamp`) are consumed identically in
Tasks 4 and 8 and by the fake sampler in Task 8's tests. `collect_resources`
output keys match `renderResources` in Task 10. `collect_processes` entry keys
match `PROC_COLS`. `collect_containers` keys match `renderDocker`.
`list_directory` keys match `renderFiles`. `VALID_ACTIONS` is defined once in
Task 6 and imported in Task 8.

**Deviation from spec, deliberate:** the spec implies all views poll. Task 10
excludes the File Explorer from polling — re-fetching a directory every 2
seconds would fight the user's navigation and serves no monitoring purpose.
The view still refreshes on navigation. Flagged rather than silently applied.
