"""Server updates.

Checking: the newest version for the chosen patchline comes from Hytale's public Maven metadata
(no sign-in needed) and is compared with the version inside HytaleServer.jar.

Installing, two ways:
  * "builtin" (server running, official folder layout): the server's own updater downloads the
    update to updater/staging, `update apply --confirm` makes the server exit with code 8, and
    OrbisFox copies the staged files in (exactly what the official start scripts do) and starts it.
  * "downloader" (works while stopped too): the official hytale-downloader fetches the release zip,
    OrbisFox stops the server, backs up, replaces only the server binaries and assets, and starts.
World, settings, mods and the server's sign-in are never touched.
"""
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile

from . import hytale

DOWNLOADER_ZIP = "https://downloader.hytale.com/hytale-downloader.zip"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Updates:
    def __init__(self, core):
        self.core = core
        self.latest = None        # {"version", "patchline", "updated"}
        self.checked_at = None
        self.check_error = None
        self.busy = False
        self.steps = []           # [{"id", "label", "state": pending|active|done|fail|skip}]
        self.auth = None          # {"url", "code"} while the downloader waits for a sign-in
        self.message = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------------- versions
    def installed_version(self):
        lay = hytale.locate(self.core.settings["server_dir"])
        return hytale.jar_version(lay["jar"])

    def check(self):
        line = self.core.settings["updates"].get("patchline", "release")
        try:
            self.latest = hytale.latest_version(line)
            self.check_error = None
        except Exception as e:  # noqa: BLE001
            self.check_error = f"Could not reach Hytale's update server: {e}"
        self.checked_at = time.time()
        self.core.publish_updates()
        return self.info()

    def available(self):
        if not self.latest:
            return None
        return hytale.is_newer(self.latest.get("version"), self.installed_version())

    def info(self):
        lay = hytale.locate(self.core.settings["server_dir"])
        return {"installed": self.installed_version(), "latest": self.latest, "checked_at": self.checked_at,
                "error": self.check_error, "available": self.available(), "busy": self.busy,
                "steps": self.steps, "auth": self.auth, "message": self.message,
                "patchline": self.core.settings["updates"].get("patchline", "release"),
                "builtin_ok": bool(lay["game_dir"]), "downloader": self.downloader_path(),
                "staged": self.staged_dir() is not None}

    # ---------------------------------------------------------------------------- helpers
    def _game_dir(self):
        lay = hytale.locate(self.core.settings["server_dir"])
        return lay["game_dir"], lay

    def staged_dir(self):
        gd, _ = self._game_dir()
        if gd and os.path.isfile(os.path.join(gd, "updater", "staging", "Server", "HytaleServer.jar")):
            return os.path.join(gd, "updater", "staging")
        return None

    def downloader_path(self):
        p = self.core.settings["updates"].get("downloader")
        if p and os.path.isfile(p):
            return p
        gd, lay = self._game_dir()
        names = ["hytale-downloader-windows-amd64.exe", "hytale-downloader.exe"] if os.name == "nt" else \
            ["hytale-downloader-linux-amd64", "hytale-downloader"]
        for base in filter(None, [os.path.join(self.core.data_dir, "downloader"), gd,
                                  gd and os.path.dirname(gd), lay["server_dir"]]):
            for n in names:
                if os.path.isfile(os.path.join(base, n)):
                    return os.path.join(base, n)
        return None

    def _steps(self, ids):
        labels = {"warn": "Warn players", "check": "Check version", "fetch": "Download update",
                  "backup": "Back up the world", "stop": "Stop the server", "apply": "Install files",
                  "start": "Start the server"}
        self.steps = [{"id": i, "label": labels[i], "state": "pending"} for i in ids]
        self.core.publish_updates()

    def _step(self, sid, state, label=None):
        for s in self.steps:
            if s["id"] == sid:
                s["state"] = state
                if label:
                    s["label"] = label
        self.core.publish_updates()

    def _warn_players(self, seconds=60):
        sv = self.core.server
        if not sv.running or not self.core.players.online:
            return
        for left in (60, 30, 10, 5):
            if left > seconds:
                continue
            try:
                sv.say(f"The server will restart for an update in {left} seconds.", style="notify", level="warning")
            except (RuntimeError, ValueError):
                return
            nxt = {60: 30, 30: 10, 10: 5, 5: 0}[left]
            time.sleep(left - nxt)

    # ---------------------------------------------------------------------------- install
    def install(self, method="auto"):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("An update is already being installed.")
        self.busy = True
        self.message = None
        threading.Thread(target=self._install, args=(method,), name="update", daemon=True).start()

    def _install(self, method):
        try:
            gd, _lay = self._game_dir()
            sv = self.core.server
            if method == "auto":
                method = "builtin" if (sv.running and gd) else "downloader"
            if method == "builtin":
                self._install_builtin()
            else:
                self._install_downloader()
        except Exception as e:  # noqa: BLE001
            self.message = str(e)
            for s in self.steps:
                if s["state"] == "active":
                    s["state"] = "fail"
            self.core.log_app(f"Update failed: {e}", level="error")
            self.core.toast("Update failed", str(e), "bad", 0)
        finally:
            self.busy = False
            self.auth = None
            self._lock.release()
            self.core.publish_updates()

    def _backup(self):
        if self.core.settings["backups"].get("before_update", True):
            self._step("backup", "active")
            self.core.backups.create("before-update", wait_for_save=True)
            self._step("backup", "done")
        else:
            self._step("backup", "skip")

    def _install_builtin(self):
        sv = self.core.server
        if not sv.running:
            raise RuntimeError("The built-in updater needs the server running.")
        self._steps(["fetch", "backup", "warn", "apply", "start"])
        self._step("fetch", "active", "Server downloads the update")
        mark = time.time()
        was_staged = self.staged_dir() is not None
        sv.send("update download", source="orbisfox")
        staged = None
        end = time.time() + 30 * 60
        last_size, stable = -1, 0
        while time.time() < end:
            sd = self.staged_dir()
            if sd:
                jar = os.path.join(sd, "Server", "HytaleServer.jar")
                try:
                    size, mtime = os.path.getsize(jar), os.path.getmtime(jar)
                except OSError:
                    size, mtime = -1, 0
                # A jar that was already staged before we asked only counts once it is rewritten.
                fresh = not was_staged or mtime >= mark - 5
                stable = stable + 1 if (fresh and size > 0 and size == last_size) else 0
                last_size = size
                if stable >= 3:
                    staged = sd
                    break
            if not sv.running:
                raise RuntimeError("The server stopped while downloading the update.")
            time.sleep(2)
        if not staged:
            raise RuntimeError("The server did not finish downloading the update within 30 minutes. "
                               "Check the console (try `update status`).")
        self._step("fetch", "done")
        self._backup()
        self._step("warn", "active")
        self._warn_players()
        self._step("warn", "done")
        self._step("apply", "active", "Server applies the update and restarts")
        self.core.server.send("update apply --confirm", source="orbisfox")
        # The server exits with code 8; Supervisor calls apply_staged() and starts it again.
        end = time.time() + 300
        while time.time() < end and sv.alive:
            time.sleep(1)
        if sv.alive:
            raise RuntimeError("The server did not stop to apply the update. Check the console.")
        while time.time() < end and self.staged_dir():
            time.sleep(1)
        self._step("apply", "done")
        self._step("start", "active")
        end = time.time() + 600
        while time.time() < end and not sv.running:
            time.sleep(1)
        self._step("start", "done" if sv.running else "fail")
        self.message = f"Updated to {self.installed_version()}."
        self.core.toast("Server updated", self.message)

    def apply_staged(self):
        """What the official start scripts do on exit code 8."""
        st = self.staged_dir()
        gd, lay = self._game_dir()
        if not st or not gd:
            return False
        sd = lay["server_dir"]
        self.core.log_app("Applying the staged update...")
        shutil.copy2(os.path.join(st, "Server", "HytaleServer.jar"), os.path.join(sd, "HytaleServer.jar"))
        aot = os.path.join(st, "Server", "HytaleServer.aot")
        if os.path.isfile(aot):
            shutil.copy2(aot, os.path.join(sd, "HytaleServer.aot"))
        lic = os.path.join(st, "Server", "Licenses")
        if os.path.isdir(lic):
            shutil.rmtree(os.path.join(sd, "Licenses"), ignore_errors=True)
            shutil.copytree(lic, os.path.join(sd, "Licenses"))
        for n in ("Assets.zip", "start.bat", "start.sh"):
            if os.path.isfile(os.path.join(st, n)):
                shutil.copy2(os.path.join(st, n), os.path.join(gd, n))
        shutil.rmtree(st, ignore_errors=True)
        self.core.log_app(f"Update installed: now {self.installed_version()}.")
        return True

    # ---------------------------------------------------------------------------- downloader
    def fetch_downloader(self):
        dest = os.path.join(self.core.data_dir, "downloader")
        os.makedirs(dest, exist_ok=True)
        tmp = os.path.join(dest, "hytale-downloader.zip")
        req = urllib.request.Request(DOWNLOADER_ZIP, headers={"User-Agent": "OrbisFox"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        root = os.path.realpath(dest)
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                target = os.path.realpath(os.path.join(dest, info.filename))
                if not target.startswith(root + os.sep) or info.is_dir():
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as fi, open(target, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
        os.remove(tmp)
        if os.name != "nt":
            for n in os.listdir(dest):
                if n.startswith("hytale-downloader"):
                    os.chmod(os.path.join(dest, n), 0o755)
        self.core.publish_updates()
        return self.downloader_path()

    def _run_downloader(self, args, on_line=None, timeout=3600):
        exe = self.downloader_path()
        if not exe:
            raise RuntimeError("hytale-downloader was not found. Use 'Get hytale-downloader' in Updates first.")
        cwd = os.path.join(self.core.data_dir, "downloader")
        os.makedirs(cwd, exist_ok=True)
        proc = subprocess.Popen([exe] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        out = []
        start = time.time()
        for raw in iter(proc.stdout.readline, b""):
            line = hytale.ANSI_RE.sub("", raw.decode("utf-8", "replace")).rstrip()
            if not line:
                continue
            out.append(line)
            self.core.log_app(f"[downloader] {line}")
            url = re.search(r"https://\S+", line)
            code = hytale.AUTH_CODE_RE.search(line)
            if url and ("device" in url.group(0) or "oauth" in url.group(0) or "login" in url.group(0)):
                self.auth = dict(self.auth or {}, url=url.group(0).rstrip(".,)"))
                self.core.publish_updates()
            if code:
                self.auth = dict(self.auth or {}, code=code.group(1))
                self.core.publish_updates()
            if on_line:
                on_line(line)
            if time.time() - start > timeout:
                proc.kill()
                raise RuntimeError("hytale-downloader took too long.")
        proc.wait()
        self.auth = None
        if proc.returncode != 0:
            tail = " ".join(out[-3:])
            if "invalid_grant" in tail:
                raise RuntimeError("The downloader's Hytale sign-in expired. Run the update again to sign in.")
            raise RuntimeError(f"hytale-downloader failed (exit {proc.returncode}): {tail}")
        return out

    def _install_downloader(self):
        sv = self.core.server
        gd, lay = self._game_dir()
        line = self.core.settings["updates"].get("patchline", "release")
        self._steps(["fetch", "warn", "stop", "backup", "apply", "start"])
        self._step("fetch", "active")
        work = os.path.join(self.core.data_dir, "downloader", "work")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        zip_path = os.path.join(work, "server.zip")
        args = ["-download-path", zip_path, "-skip-update-check"]
        if line == "pre-release":
            args += ["-patchline", "pre-release"]
        self._run_downloader(args)
        if not os.path.isfile(zip_path):
            cands = [f for f in os.listdir(work) if f.endswith(".zip")]
            if not cands:
                raise RuntimeError("The downloader finished but no zip was found.")
            zip_path = os.path.join(work, cands[0])
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            jar_name = next((n for n in names if n.replace("\\", "/").endswith("Server/HytaleServer.jar")), None)
            if not jar_name:
                raise RuntimeError("The downloaded zip has no Server/HytaleServer.jar.")
            prefix = jar_name[: -len("Server/HytaleServer.jar")]
        self._step("fetch", "done")
        was_running = sv.alive
        self._step("warn", "active")
        self._warn_players()
        self._step("warn", "done")
        self._step("stop", "active")
        if sv.alive:
            sv.stop("installing an update")
            end = time.time() + int(self.core.settings["stop_timeout_s"]) + 30
            while sv.alive and time.time() < end:
                time.sleep(1)
            if sv.alive:
                raise RuntimeError("The server did not stop.")
        self._step("stop", "done")
        self._backup_stopped()
        self._step("apply", "active")
        sd = lay["server_dir"]
        target_game = gd or (os.path.dirname(lay["assets"]) if lay["assets"] else sd)
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                rel = info.filename.replace("\\", "/")
                if not rel.startswith(prefix) or info.is_dir():
                    continue
                rel = rel[len(prefix):]
                if rel.startswith("Server/"):
                    sub = rel[len("Server/"):]
                    if not (sub in ("HytaleServer.jar", "HytaleServer.aot") or sub.startswith("Licenses/")):
                        continue
                    dest = os.path.join(sd, sub)
                elif rel == "Assets.zip":
                    dest = lay["assets"] or os.path.join(target_game, "Assets.zip")
                elif rel in ("start.bat", "start.sh") and gd:
                    dest = os.path.join(gd, rel)
                else:
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                tmp = dest + ".orbisfox-new"
                with z.open(info) as fi, open(tmp, "wb") as fo:
                    shutil.copyfileobj(fi, fo, 1 << 20)
                os.replace(tmp, dest)
        shutil.rmtree(work, ignore_errors=True)
        self._step("apply", "done")
        if was_running or self.core.settings["auto_start"]:
            self._step("start", "active")
            sv.start("after an update")
            end = time.time() + 600
            while time.time() < end and not sv.running and sv.alive:
                time.sleep(1)
            self._step("start", "done" if sv.running else "fail")
        else:
            self._step("start", "skip")
        self.message = f"Updated to {self.installed_version()}."
        self.core.toast("Server updated", self.message)
        self.check()

    def _backup_stopped(self):
        if self.core.settings["backups"].get("before_update", True):
            self._step("backup", "active")
            try:
                self.core.backups.create("before-update", wait_for_save=False)
                self._step("backup", "done")
            except RuntimeError as e:
                if "No world" in str(e):
                    self._step("backup", "skip")
                else:
                    raise
        else:
            self._step("backup", "skip")
