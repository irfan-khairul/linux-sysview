# tests/test_metrics.py
from collections import namedtuple
from contextlib import ExitStack
from unittest.mock import patch

import psutil
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


# --- sensors -----------------------------------------------------------

shwtemp = namedtuple("shwtemp", "label current high critical")
sfan = namedtuple("sfan", "label current")
sbattery = namedtuple("sbattery", "percent secsleft power_plugged")

# Verbatim from a Dell Inspiron 5558 running Linux.
DELL_TEMPS = {
    "acpitz": [shwtemp("", 25.0, None, None)],
    "dell_smm": [
        shwtemp("CPU", 45.0, None, None),
        shwtemp("Other", 44.0, None, None),
        shwtemp("SODIMM", 38.0, None, None),
        # An unwired sensor on a machine with no discrete GPU.
        shwtemp("GPU", 2.0, None, None),
    ],
    "coretemp": [
        shwtemp("Package id 0", 54.0, 105.0, 105.0),
        shwtemp("Core 0", 54.0, 105.0, 105.0),
        shwtemp("Core 1", 50.0, 105.0, 105.0),
    ],
}

# One physical fan, reported four times: once labelled, three times blank.
DELL_FANS = {"dell_smm": [sfan("Processor Fan", 2200), sfan("", 2200),
                          sfan("", 2200), sfan("", 2200)]}


def test_temperatures_sorted_stably_by_name_not_by_value():
    """Sorting by value would make rows swap places as readings drift."""
    with patch.object(psutil, "sensors_temperatures", create=True,
                      return_value=DELL_TEMPS):
        temps = collect_resources(SNAPSHOT)["temperatures"]

    order = [(t["chip"], t["label"]) for t in temps]
    assert order == sorted(order, key=lambda p: (p[0].lower(), p[1].lower()))
    # Thresholds survive for the sensors that publish them.
    core = next(t for t in temps if t["label"] == "Core 0")
    assert core["critical"] == 105.0
    # An unlabelled sensor falls back to its chip name.
    assert any(t["label"] == "acpitz" for t in temps)


def test_implausible_temperature_readings_are_dropped():
    """An unwired sensor reports a value no real component would show."""
    with patch.object(psutil, "sensors_temperatures", create=True,
                      return_value=DELL_TEMPS):
        temps = collect_resources(SNAPSHOT)["temperatures"]
    assert all(t["label"] != "GPU" for t in temps)
    assert all(5.0 <= t["current"] <= 150.0 for t in temps)


def test_duplicate_fan_entries_collapse_to_one():
    with patch.object(psutil, "sensors_fans", create=True, return_value=DELL_FANS):
        fans = collect_resources(SNAPSHOT)["fans"]
    assert len(fans) == 1
    assert fans[0]["label"] == "Processor Fan"
    assert fans[0]["rpm"] == 2200


def test_genuinely_distinct_fans_are_both_kept():
    two = {"dell_smm": [sfan("CPU Fan", 2200), sfan("Chassis Fan", 1500)]}
    with patch.object(psutil, "sensors_fans", create=True, return_value=two):
        fans = collect_resources(SNAPSHOT)["fans"]
    assert [f["label"] for f in fans] == ["CPU Fan", "Chassis Fan"]


def test_battery_sentinel_secsleft_becomes_none():
    # psutil reports POWER_TIME_UNLIMITED as a negative sentinel, not seconds.
    with patch.object(psutil, "sensors_battery", create=True,
                      return_value=sbattery(99.2, -2, True)):
        batt = collect_resources(SNAPSHOT)["battery"]
    assert batt["percent"] == 99.2
    assert batt["plugged"] is True
    assert batt["secsleft"] is None


def test_missing_sensor_support_degrades_to_empty():
    """macOS has no sensors_temperatures/sensors_fans at all."""
    with patch.object(psutil, "sensors_temperatures", create=True,
                      side_effect=AttributeError), \
         patch.object(psutil, "sensors_fans", create=True,
                      side_effect=OSError), \
         patch.object(psutil, "sensors_battery", create=True, return_value=None):
        d = collect_resources(SNAPSHOT)
    assert d["temperatures"] == []
    assert d["fans"] == []
    assert d["battery"] is None
