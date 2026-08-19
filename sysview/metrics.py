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
    "proc", "pstore", "ramfs", "securityfs", "squashfs", "sysfs", "tmpfs",
    "tracefs",
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
        "temperatures": _temperatures(),
        "fans": _fans(),
        "battery": _battery(),
    }

# Sensor readings are Linux-only (psutil has no sensors_* on macOS) and
# hardware-dependent, so every reader here degrades to an empty list rather
# than raising when a machine cannot report.

# A sensor that is present but unwired reports an implausible value: a Dell
# Inspiron with no discrete GPU reports its "GPU" at 2 degrees. Anything
# outside this range is a wiring artefact, not a reading.
_MIN_PLAUSIBLE_TEMP = 5.0
_MAX_PLAUSIBLE_TEMP = 150.0


def _temperatures():
    """Return per-sensor temperatures, hottest first."""
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return []
    try:
        groups = reader() or {}
    except (OSError, AttributeError, NotImplementedError):
        return []

    readings = []
    for chip, entries in groups.items():
        for entry in entries:
            current = getattr(entry, "current", None)
            if current is None:
                continue
            if not (_MIN_PLAUSIBLE_TEMP <= current <= _MAX_PLAUSIBLE_TEMP):
                continue
            readings.append({
                "chip": chip,
                # Some sensors report no label at all; the chip name is the
                # only thing left to call them.
                "label": (getattr(entry, "label", "") or chip),
                "current": round(current, 1),
                # Only coretemp tends to publish these; the rest give None.
                "high": getattr(entry, "high", None),
                "critical": getattr(entry, "critical", None),
            })

    readings.sort(key=lambda r: r["current"], reverse=True)
    return readings


def _fans():
    """Return fan speeds in RPM, de-duplicated.

    dell_smm reports one physical fan four times, once labelled and three
    times blank, so identical speeds collapse into a single entry.
    """
    reader = getattr(psutil, "sensors_fans", None)
    if reader is None:
        return []
    try:
        groups = reader() or {}
    except (OSError, AttributeError, NotImplementedError):
        return []

    fans = []
    seen = set()
    for chip, entries in groups.items():
        for index, entry in enumerate(entries):
            rpm = getattr(entry, "current", None)
            if rpm is None:
                continue
            # Dedupe on chip and speed, deliberately ignoring the label:
            # dell_smm reports one physical fan four times at an identical
            # RPM, once labelled and three times blank. Including the label
            # in the key would keep a spurious second entry.
            key = (chip, rpm)
            if key in seen:
                continue
            seen.add(key)
            label = getattr(entry, "label", "") or ""
            fans.append({
                "chip": chip,
                "label": label or ("Fan %d" % (index + 1)),
                "rpm": int(rpm),
            })
    return fans


def _battery():
    """Return battery state, or None on a desktop."""
    reader = getattr(psutil, "sensors_battery", None)
    if reader is None:
        return None
    try:
        battery = reader()
    except (OSError, AttributeError, NotImplementedError):
        return None
    if battery is None:
        return None

    secsleft = getattr(battery, "secsleft", None)
    # psutil uses sentinel constants for "unlimited" and "unknown"; neither is
    # a real number of seconds.
    if not isinstance(secsleft, int) or secsleft < 0:
        secsleft = None

    return {
        "percent": round(battery.percent, 1),
        "plugged": bool(battery.power_plugged),
        "secsleft": secsleft,
    }

