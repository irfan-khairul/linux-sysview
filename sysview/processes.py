"""Read-only process table snapshot."""

import psutil

_ATTRS = [
    "pid", "name", "username", "cpu_percent", "memory_percent",
    "status", "memory_info", "cmdline", "create_time", "num_threads", "ppid",
]

# Sorting and filtering happen here, over the full process list, before the
# result is truncated. Doing either in the browser would only ever see the
# truncated slice: sorting by memory would show the largest memory user among
# the top CPU consumers, not the largest overall.
SORT_KEYS = frozenset({
    "pid", "name", "user", "cpu_percent", "memory_percent", "rss", "status",
    "threads", "started",
})

DEFAULT_SORT = "cpu_percent"


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
        # These three come from the same /proc read as the rest, so they are
        # effectively free.
        "started": info.get("create_time") or 0.0,
        "threads": info.get("num_threads") or 0,
        "ppid": info.get("ppid"),
    }


def _matches(entry, needle):
    """True if the process matches a case-insensitive name/PID/cmdline search."""
    if str(entry["pid"]).startswith(needle):
        return True
    if needle in entry["name"].lower():
        return True
    return needle in entry["cmdline"].lower()


def _sort_value(entry, key):
    value = entry.get(key)
    # Strings sort case-insensitively; everything else compares natively.
    return value.lower() if isinstance(value, str) else value


def collect_processes(limit=200, sort=DEFAULT_SORT, desc=True, query=""):
    """Return the process table, sorted and filtered before truncation.

    `total` counts every visible process, `matched` counts those passing the
    filter, and `processes` holds at most `limit` of them — so the caller can
    tell the difference between "nothing matched" and "there is more to see".
    """
    if sort not in SORT_KEYS:
        sort = DEFAULT_SORT

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

    total = len(entries)

    needle = (query or "").strip().lower()
    if needle:
        entries = [e for e in entries if _matches(e, needle)]

    entries.sort(key=lambda e: _sort_value(e, sort), reverse=bool(desc))

    return {
        "processes": entries[:limit],
        "total": total,
        "matched": len(entries),
        "sort": sort,
        "desc": bool(desc),
    }
