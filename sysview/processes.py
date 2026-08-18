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
