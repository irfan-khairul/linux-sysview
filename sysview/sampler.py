"""Background sampling of counter-based system metrics."""

import copy
import threading
import time

import psutil


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
        # since-boot average. Guarded the same as sample_once() below: a
        # failure here must not prevent the thread from entering the loop.
        try:
            psutil.cpu_percent(percpu=True)
        except Exception:
            pass
        while not self._stop_event.is_set():
            try:
                self.sample_once()
            except Exception:
                # A sampling failure must never kill the thread; the UI shows
                # the previous snapshot as stale instead.
                pass
            self._stop_event.wait(self.interval)

    def sample_once(self):
        """Take one sample and store it as the latest snapshot.

        Called by exactly one sampler thread (the loop in `_run`, or a test
        driving it directly). Because there is never more than one caller in
        flight, `_prev_net`, `_prev_disk`, and `_prev_time` are deliberately
        not lock-protected — only `_snapshot` is, since `snapshot()` may be
        read concurrently from other threads.
        """
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
