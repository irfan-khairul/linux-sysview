# tests/test_sampler.py
import time
from unittest.mock import patch

from sysview.sampler import Sampler
import sysview.sampler as sampler_module


def _wait_until(predicate, timeout=2.0, interval=0.005):
    """Poll `predicate` until it is truthy or `timeout` seconds elapse.

    Used instead of a bare fixed sleep so thread-lifecycle tests are fast
    and don't depend on guessing how long a background thread needs.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


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


def test_start_then_stop_runs_thread_and_updates_snapshot():
    s = Sampler(interval=0.01)
    try:
        s.start()
        assert _wait_until(lambda: s.snapshot()["timestamp"] != 0.0)
        assert s._thread is not None
        assert s._thread.is_alive()
    finally:
        s.stop()
    assert s._thread is None


def test_sample_once_failure_mid_loop_does_not_kill_thread():
    s = Sampler(interval=0.01)
    calls = {"n": 0}
    real_net_io_counters = sampler_module.psutil.net_io_counters

    def flaky_net_io_counters(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real_net_io_counters(*args, **kwargs)

    try:
        with patch.object(
            sampler_module.psutil, "net_io_counters", side_effect=flaky_net_io_counters
        ):
            s.start()
            # Wait until the flaky call has definitely fired at least once.
            assert _wait_until(lambda: calls["n"] >= 2)
            # Give the thread a chance to keep looping past the failure.
            assert _wait_until(lambda: calls["n"] >= 3)
        assert s._thread.is_alive()
    finally:
        s.stop()


def test_priming_failure_does_not_prevent_loop():
    s = Sampler(interval=0.01)
    calls = {"n": 0}
    real_cpu_percent = sampler_module.psutil.cpu_percent

    def flaky_cpu_percent(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # The priming call in _run() is always the first call.
            raise RuntimeError("boom")
        return real_cpu_percent(*args, **kwargs)

    try:
        with patch.object(sampler_module.psutil, "cpu_percent", side_effect=flaky_cpu_percent):
            s.start()
            assert _wait_until(lambda: calls["n"] >= 2)
        assert s._thread.is_alive()
        assert _wait_until(lambda: s.snapshot()["timestamp"] != 0.0)
    finally:
        s.stop()


def test_start_is_idempotent_and_stop_is_a_safe_no_op():
    s = Sampler(interval=0.01)

    # stop() before start() must not raise.
    s.stop()
    assert s._thread is None

    s.start()
    first_thread = s._thread
    s.start()
    assert s._thread is first_thread

    s.stop()
    assert s._thread is None
    # stop() called twice must not raise.
    s.stop()
    assert s._thread is None
