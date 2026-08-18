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
    }
