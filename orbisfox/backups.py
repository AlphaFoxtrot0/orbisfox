"""World backups: zip the universe (worlds and players) plus the server's own settings files.

Backups are written to a temporary name and renamed when complete, so a half-written zip never
shows up as a backup. Automatic backups are pruned to `keep`; manual ones are kept until deleted.
"""
import datetime as dt
import json
import os
import re
import shutil
import threading
import time
import zipfile

# What a backup holds, relative to the server folder.
WORLD_DIRS = ["universe"]
SETTINGS_FILES = ["config.json", "permissions.json", "whitelist.json", "bans.json"]
OPTIONAL_DIRS = ["mods"]
SKIP_NAMES = {"session.lock", ".lock"}

NAME_RE = re.compile(r"^orbisfox-(\d{8}-\d{6})-([a-z0-9-]+)\.zip$")


PACKED = (".region.bin", ".zip", ".jar", ".png", ".jpg", ".ogg")


def _fmt_reason(reason):
    return re.sub(r"[^a-z0-9-]+", "-", (reason or "manual").lower()).strip("-") or "manual"


class Backups:
    def __init__(self, core):
        self.core = core
        self._lock = threading.Lock()
        self.busy = None   # {"reason", "done", "total", "started"} while a backup/restore runs

    # -- locations -------------------------------------------------------------------------
    @property
    def dir(self):
        d = self.core.settings["backups"].get("dir") or os.path.join(self.core.data_dir, "backups")
        os.makedirs(d, exist_ok=True)
        return d

    def list(self):
        out = []
        try:
            names = os.listdir(self.dir)
        except OSError:
            names = []
        for n in names:
            m = NAME_RE.match(n)
            if not m:
                continue
            p = os.path.join(self.dir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            created = dt.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
            out.append({"name": n, "created": created, "size": st.st_size, "reason": m.group(2),
                        "auto": m.group(2) != "manual"})
        out.sort(key=lambda b: b["created"], reverse=True)
        return out

    def last(self):
        lst = self.list()
        return lst[0] if lst else None

    # -- create ----------------------------------------------------------------------------
    def _collect(self, server_dir, include_mods):
        files = []
        roots = WORLD_DIRS + (OPTIONAL_DIRS if include_mods else [])
        for r in roots:
            base = os.path.join(server_dir, r)
            for root, dirs, fnames in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in fnames:
                    if f in SKIP_NAMES:
                        continue
                    full = os.path.join(root, f)
                    files.append((full, os.path.relpath(full, server_dir)))
        for f in SETTINGS_FILES:
            full = os.path.join(server_dir, f)
            if os.path.isfile(full):
                files.append((full, f))
        return files

    def create(self, reason="manual", wait_for_save=True):
        """Runs synchronously (call from a worker thread). Returns the backup entry."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A backup is already running.")
        try:
            core = self.core
            server_dir = core.server_dir()
            if not server_dir or not os.path.isdir(os.path.join(server_dir, "universe")):
                raise RuntimeError("No world found yet: set the server folder and start the server once.")
            reason = _fmt_reason(reason)
            self.busy = {"kind": "backup", "reason": reason, "done": 0, "total": 0, "started": time.time()}
            core.publish_backups()
            if wait_for_save and core.server.running:
                core.server.flush_world()   # asks the server to write everything to disk first
            files = self._collect(server_dir, core.settings["backups"].get("include_mods", True))
            total = 0
            for full, _ in files:
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass
            self.busy["total"] = total
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"orbisfox-{stamp}-{reason}.zip"
            final = os.path.join(self.dir, name)
            tmp = final + ".part"
            skipped, done, last_pub = [], 0, 0
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
                for full, rel in files:
                    try:
                        # Hytale's region files are already compressed: storing them as they are
                        # makes a backup several times faster and spares the CPU the server needs.
                        packed = rel.lower().endswith(PACKED)
                        z.write(full, rel.replace("\\", "/"), compress_type=zipfile.ZIP_STORED if packed else None)
                        done += os.path.getsize(full)
                    except OSError:
                        skipped.append(rel)
                    self.busy["done"] = done
                    if time.time() - last_pub > 0.5:
                        last_pub = time.time()
                        core.publish_backups()
                meta = {"created": time.time(), "reason": reason, "server_version": core.updates.installed_version(),
                        "files": len(files) - len(skipped), "skipped": skipped[:50], "app": "OrbisFox"}
                z.writestr("orbisfox-backup.json", json.dumps(meta, indent=2))
            os.replace(tmp, final)
            if reason != "manual":
                self.prune()
            entry = next((b for b in self.list() if b["name"] == name), None)
            msg = f"Backup saved: {name} ({_human(entry['size'] if entry else 0)})"
            if skipped:
                msg += f", {len(skipped)} file(s) were busy and skipped"
            core.log_app(msg)
            return entry
        finally:
            self.busy = None
            self._lock.release()
            self.core.publish_backups()

    def prune(self):
        keep = max(1, int(self.core.settings["backups"].get("keep", 20)))
        autos = [b for b in self.list() if b["auto"]]
        for b in autos[keep:]:
            try:
                os.remove(os.path.join(self.dir, b["name"]))
            except OSError:
                pass

    def delete(self, name):
        if not NAME_RE.match(name):
            raise ValueError("Not a backup name.")
        os.remove(os.path.join(self.dir, name))

    def path_of(self, name):
        if not NAME_RE.match(name):
            raise ValueError("Not a backup name.")
        p = os.path.join(self.dir, name)
        if not os.path.isfile(p):
            raise FileNotFoundError(name)
        return p

    # -- restore ---------------------------------------------------------------------------
    def restore(self, name):
        """Replaces the world (and settings files found in the backup). The server must be stopped."""
        core = self.core
        if core.server.alive:
            raise RuntimeError("Stop the server before restoring a backup.")
        src = self.path_of(name)
        server_dir = core.server_dir()
        # Safety net: keep what is there now.
        if os.path.isdir(os.path.join(server_dir, "universe")):
            self.create("before-restore", wait_for_save=False)
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A backup is already running.")
        try:
            self.busy = {"kind": "restore", "reason": name, "done": 0, "total": os.path.getsize(src),
                         "started": time.time()}
            core.publish_backups()
            stamp = int(time.time())
            moved = []
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
                tops = {n.split("/", 1)[0] for n in names}
                for top in tops & set(WORLD_DIRS + OPTIONAL_DIRS):
                    cur = os.path.join(server_dir, top)
                    if os.path.exists(cur):
                        aside = os.path.join(server_dir, f".{top}.before-restore-{stamp}")
                        os.replace(cur, aside)
                        moved.append(aside)
                root = os.path.realpath(server_dir)
                for info in z.infolist():
                    if info.filename == "orbisfox-backup.json" or info.is_dir():
                        continue
                    target = os.path.realpath(os.path.join(server_dir, info.filename))
                    if not target.startswith(root + os.sep):
                        continue   # never write outside the server folder
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as fi, open(target, "wb") as fo:
                        shutil.copyfileobj(fi, fo, 1 << 20)
                    self.busy["done"] += info.compress_size
            for aside in moved:
                shutil.rmtree(aside, ignore_errors=True)
            core.log_app(f"Restored backup {name}")
        finally:
            self.busy = None
            self._lock.release()
            core.publish_backups()


def _human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
