"""Folder browsing for picking the server folder inside the OrbisFox window.

A native folder dialog opened by a background process tends to appear behind the app window, so the
panel has its own browser instead: list folders, flag the ones that hold a Hytale server, and a quick
search of the usual places.
"""
import os
import string
import time

from . import hytale

SKIP = {"windows", "$recycle.bin", "system volume information", "programdata", "recovery", "node_modules",
        "appdata", ".git", "$windows.~ws", "$windows.~bt", "msocache", "perflogs", "__pycache__"}


def _server_info(path):
    """Returns {version, world} when `path` (or its Server subfolder) holds HytaleServer.jar."""
    for sd in (path, os.path.join(path, "Server")):
        if os.path.isfile(os.path.join(sd, "HytaleServer.jar")):
            return {"server_dir": sd, "version": hytale.jar_version(os.path.join(sd, "HytaleServer.jar")),
                    "world": os.path.isdir(os.path.join(sd, "universe"))}
    return None


def _drives(fixed_only=False):
    """Drive roots; network and removable drives are left out of searches (they can hang)."""
    out = []
    if os.name != "nt":
        return ["/"]
    import ctypes
    for letter in string.ascii_uppercase:
        d = f"{letter}:\\"
        kind = ctypes.windll.kernel32.GetDriveTypeW(d)   # 2 removable, 3 fixed, 4 network, 5 cd
        if kind in (3,) or (not fixed_only and kind in (2, 4)):
            out.append(d)
    return out


def places():
    home = os.path.expanduser("~")
    out = []
    for label, p in (("Home", home), ("Desktop", os.path.join(home, "Desktop")),
                     ("Documents", os.path.join(home, "Documents")), ("Downloads", os.path.join(home, "Downloads"))):
        if os.path.isdir(p):
            out.append({"name": label, "path": p})
    for d in _drives():
        out.append({"name": d.rstrip("\\") or d, "path": d})
    return out


def listdir(path):
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"'))) if path else ""
    if not path:
        return {"path": "", "parent": None, "places": places(), "folders": [], "server": None}
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        raise FileNotFoundError("That folder doesn't exist.")
    folders = []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except PermissionError:
        raise PermissionError("Windows won't let OrbisFox look inside that folder.")
    for n in names:
        p = os.path.join(path, n)
        if n.startswith(".") or n.lower() in SKIP or not os.path.isdir(p):
            continue
        info = _server_info(p)
        folders.append({"name": n, "path": p, "server": bool(info), "version": info and info["version"]})
        if len(folders) >= 400:
            break
    parent = os.path.dirname(path.rstrip("\\/")) or None
    if parent and os.path.normcase(parent) == os.path.normcase(path):
        parent = None
    return {"path": path, "parent": parent, "places": places(), "folders": folders, "server": _server_info(path)}


def find_servers(budget_s=5.0, limit=12):
    """Looks for Hytale servers in the usual places: the home folder first, then the top of each local
    drive (system drive first). Returns what it found when the time is up; a slow drive can't stall it."""
    import threading
    found, stop = [], threading.Event()
    roots = [(os.path.expanduser("~"), 4)]
    if os.name == "nt":
        sysdrive = (os.environ.get("SystemDrive", "C:") + "\\").upper()
        drives = sorted(_drives(fixed_only=True), key=lambda d: d.upper() != sysdrive)
        roots += [(d, 3) for d in drives]
    else:
        roots += [("/srv", 3), ("/opt", 3), ("/home", 4)]

    def walk():
        seen = set()
        for root, depth in roots:
            stack = [(root, 0)]
            while stack and not stop.is_set() and len(found) < limit:
                p, d = stack.pop()
                key = os.path.normcase(p)
                if key in seen:
                    continue
                seen.add(key)
                jar = os.path.join(p, "HytaleServer.jar")
                if os.path.isfile(jar):
                    found.append({"path": p, "version": hytale.jar_version(jar),
                                  "world": os.path.isdir(os.path.join(p, "universe")), "modified": os.path.getmtime(jar)})
                    continue
                if d >= depth:
                    continue
                try:
                    with os.scandir(p) as it:
                        for e in it:
                            if stop.is_set():
                                break
                            n = e.name.lower()
                            skip = n.startswith(".") or n in SKIP or n.startswith("program files")
                            if not skip and e.is_dir(follow_symlinks=False):
                                stack.append((e.path, d + 1))
                except OSError:
                    continue
    t = threading.Thread(target=walk, name="find-servers", daemon=True)
    t.start()
    t.join(budget_s)
    complete = not t.is_alive()
    stop.set()
    servers = sorted(list(found), key=lambda f: (not f["world"], -f["modified"]))
    return {"servers": servers, "complete": complete}
