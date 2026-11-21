"""OrbisFox updating itself from GitHub.

Checks the VERSION file on the repository's main branch (so a pushed version bump is seen right away)
and offers the update in the panel. Installing downloads that version (the release tag if there is
one, otherwise main), replaces OrbisFox's own files, never touches the data folder, then restarts
OrbisFox; a running Hytale server is stopped first (players warned when online) and started again.
A copy that is a git checkout is updated with `git pull` instead.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import zipfile

from . import __version__, hytale, net
from .settings import APP_DIR

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
KEEP = {"data", "data-demo", ".git", ".github", "__pycache__"}


class AppUpdates:
    def __init__(self, core):
        self.core = core
        self.latest = None
        self.checked_at = None
        self.error = None
        self.busy = False
        self.message = None
        self.failed = None           # the version whose install failed (no automatic retries for it)
        self.installed = None        # a version already downloaded, waiting for OrbisFox to restart
        self._checking = False
        self._told = None
        self._lock = threading.Lock()

    @property
    def repo(self):
        return (self.core.settings.get("app_updates", {}).get("repo") or "AlphaFoxtrot0/orbisfox").strip()

    def available(self):
        return bool(self.latest) and hytale.version_key(self.latest) > hytale.version_key(__version__)

    def info(self):
        return {"current": __version__, "latest": self.latest, "available": self.available(), "checked_at": self.checked_at,
                "error": self.error, "busy": self.busy, "message": self.message, "repo": self.repo,
                "installed": self.installed,
                "git": os.path.isdir(os.path.join(APP_DIR, ".git"))}

    def check(self):
        if self._checking:
            return self.info()
        self._checking = True
        try:
            ver = net.fetch(f"https://raw.githubusercontent.com/{self.repo}/main/VERSION", timeout=15).decode().strip()
            if not ver or len(ver) > 40 or hytale.version_key(ver)[0] != 1:
                raise ValueError("unexpected VERSION file")
            self.latest, self.error = ver, None
        except Exception as e:  # noqa: BLE001
            self.error = f"Could not check for OrbisFox updates: {e}"
        finally:
            self._checking = False
        self.checked_at = time.time()
        if self.available() and self._told != self.latest:
            self._told = self.latest
            self.core.log_app(f"OrbisFox {self.latest} is available (you have {__version__}).")
            self.core.toast("OrbisFox update available", f"Version {self.latest} is out. You have {__version__}.", "info", 12000)
        self.core.hub.publish("appupdate", self.info())
        return self.info()

    # ---------------------------------------------------------------------------- install
    def install(self, warn=True):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("OrbisFox is already updating.")
        self.busy = True
        self.message = "Downloading..."
        self.core.hub.publish("appupdate", self.info())
        threading.Thread(target=self._install, args=(warn,), name="app-update", daemon=True).start()

    def _install(self, warn):
        try:
            if not self.installed or self.installed != self.latest:
                if os.path.isdir(os.path.join(APP_DIR, ".git")):
                    self._git_pull()
                else:
                    self._replace_files()
                self.installed = self.latest
            self.message = "Restarting OrbisFox..."
            self.core.hub.publish("appupdate", self.info())
            self._restart(warn)
        except Exception as e:  # noqa: BLE001
            self.failed = self.latest
            self.message = f"Update failed: {e}"
            self.core.log_app(self.message, level="error")
            self.core.toast("OrbisFox update failed", str(e), "bad", 0)
        finally:
            self.busy = False
            self._lock.release()
            self.core.hub.publish("appupdate", self.info())

    def _git_pull(self):
        git = shutil.which("git")
        if not git:
            raise RuntimeError("This copy is a git checkout but git isn't installed. Download the release ZIP instead.")
        out = subprocess.run([git, "-C", APP_DIR, "pull", "--ff-only"], capture_output=True, text=True, timeout=120,
                             creationflags=NO_WINDOW)
        if out.returncode != 0:
            raise RuntimeError((out.stderr or out.stdout).strip()[-300:] or "git pull failed")
        self.core.log_app("Updated OrbisFox with git pull: " + (out.stdout.strip().splitlines() or [""])[-1])

    def _replace_files(self):
        work = os.path.join(self.core.data_dir, "update")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        zpath = os.path.join(work, "orbisfox.zip")
        ver = self.latest or ""
        last = None
        for url in (f"https://github.com/{self.repo}/archive/refs/tags/v{ver}.zip",
                    f"https://github.com/{self.repo}/archive/refs/heads/main.zip"):
            try:
                net.download(url, zpath)
                break
            except Exception as e:  # noqa: BLE001
                last = e
        else:
            raise RuntimeError(f"Download failed: {last}")
        dest = os.path.join(work, "new")
        root = os.path.realpath(dest)
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                target = os.path.realpath(os.path.join(dest, info.filename))
                if not target.startswith(root + os.sep):
                    continue
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as fi, open(target, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
        tops = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
        src = os.path.join(dest, tops[0]) if len(tops) == 1 else dest
        if not os.path.isfile(os.path.join(src, "orbisfox", "__init__.py")):
            raise RuntimeError("The download doesn't look like OrbisFox.")
        copied = 0
        for base, dirs, files in os.walk(src):
            rel = os.path.relpath(base, src)
            dirs[:] = [d for d in dirs if not (rel == "." and d in KEEP) and d != "__pycache__"]
            for f in files:
                s = os.path.join(base, f)
                t = os.path.normpath(os.path.join(APP_DIR, rel, f))
                os.makedirs(os.path.dirname(t), exist_ok=True)
                tmp = t + ".orbisfox-new"
                shutil.copy2(s, tmp)
                os.replace(tmp, t)
                copied += 1
        shutil.rmtree(work, ignore_errors=True)
        self.core.log_app(f"Installed OrbisFox {ver or '(main)'}: {copied} files replaced. Your data folder was not touched.")

    def _restart(self, warn):
        sv = self.core.server
        resume = os.path.join(self.core.data_dir, "resume-server.json")
        if sv.alive:
            with open(resume, "w", encoding="utf-8") as f:
                json.dump({"t": time.time()}, f)
            if warn and self.core.players.online:
                self.message = "Stopping the server first (players have a 1-minute warning)..."
                self.core.hub.publish("appupdate", self.info())
            sv.schedule("stop", 60 if warn else 0, "OrbisFox is updating",
                        message="Server restarting in {time} for an OrbisFox update.")
            countdown = sv._pending_cancel
            end = time.time() + 60 + int(self.core.settings["stop_timeout_s"]) + 30
            while sv.alive and time.time() < end:
                if countdown is not None and countdown.is_set():
                    # Someone pressed Cancel during the warning: keep the server up. The new files are
                    # already in place and are used the next time OrbisFox restarts.
                    try:
                        os.remove(resume)
                    except OSError:
                        pass
                    self.core.log_app("OrbisFox's restart was cancelled. The update is installed and takes "
                                      "effect the next time OrbisFox restarts.", level="warn")
                    self.message = None
                    return
                time.sleep(1)
            if sv.alive:
                sv.kill()
                time.sleep(2)
        self.core.players.save(force=True)
        relaunch(self.core)


def relaunch(core):
    """Starts a fresh OrbisFox (it waits for this one to let go of the port) and exits this one."""
    from .desktop import _pythonw
    args = [a for a in getattr(core, "argv", []) if a not in ("--after-update", "--background", "--no-window")]
    cmd = [_pythonw(), os.path.join(APP_DIR, "orbisfox.pyw")] + args + ["--background", "--after-update"]
    flags = (0x00000008 | 0x00000200 | NO_WINDOW) if os.name == "nt" else 0   # DETACHED, NEW_GROUP
    subprocess.Popen(cmd, cwd=APP_DIR, creationflags=flags, close_fds=True)
    core.log_app("OrbisFox is restarting on the new version.")
    time.sleep(0.5)
    os._exit(0)


def resume_server_if_asked(core):
    path = os.path.join(core.data_dir, "resume-server.json")
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError:
        pass
    time.sleep(3)   # let "start the server when OrbisFox opens" go first if it is on
    if not core.server.alive:
        core.log_app("Starting the server again after the OrbisFox update.")
        try:
            core.server.start("after an OrbisFox update")
        except RuntimeError as e:
            core.log_app(f"Could not start the server: {e}", level="error")

