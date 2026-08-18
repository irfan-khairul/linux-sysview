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
