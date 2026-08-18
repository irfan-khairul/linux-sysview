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
