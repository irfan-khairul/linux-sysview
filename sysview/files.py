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
    except (OSError, ValueError):
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
