"""The heart of OrbisFox: owns every part and turns server output into events for the panel."""
import collections
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import threading
import time

from . import __version__, hytale, metrics, watchdog as wdmod
from .backups import Backups
from .bridge import Bridge
from .events import Hub
from .jvm import GcMonitor
from .players import Players
from .server import Supervisor
from .settings import APP_DIR, DATA_DIR, Settings
from .updates import Updates
from .veto import Vetoes
from .memories import Memories
from .worldmap import WorldMap


class Core:
    def __init__(self, data_dir=None):
        self.data_dir = data_dir or DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        self.settings = Settings(os.path.join(self.data_dir, "settings.json"))
        self.hub = Hub()
        self._seq = 0
        self._console = collections.deque(maxlen=5000)
        self._clock = threading.Lock()
        self.sampler = metrics.Sampler()
        self.history = collections.deque(maxlen=450)   # 15 minutes at 2 s
        self.latest = {}
        self.gc = GcMonitor()
        self.server = Supervisor(self)
        self.players = Players(self)
        self.bridge = Bridge(self)
        self.backups = Backups(self)
        self.updates = Updates(self)
        self.watchdog = wdmod.Watchdog(self)
        self.vetoes = Vetoes(self)
        self.memories = Memories(self)
        self.worldmap = WorldMap(self)
        self._daily_retry = None      # (label) of a daily restart an operator vetoed
        self.peak_players = 0
        self.auth_prompt = None       # {"url", "code"} when the server asks to be signed in
        self.auth_needed = False
        self._access = None
        self._access_at = 0
        self._schedule_done = set()
        self.orphan = None
        self._started = time.time()
        self.players.scan_files()
        self._check_orphan()

    # ---------------------------------------------------------------------------- basics
    def server_dir(self):
        lay = hytale.locate(self.settings["server_dir"])
        return lay["server_dir"] if lay["jar"] else None

    def map_dir(self):
        return self.worldmap.dir

    def start_threads(self):
        threading.Thread(target=self._metrics_loop, name="metrics", daemon=True).start()
        threading.Thread(target=self._schedule_loop, name="schedule", daemon=True).start()
        threading.Thread(target=self._map_loop, name="map", daemon=True).start()
        self.worldmap.start_share()
        if self.server_dir():
            threading.Thread(target=self.memories.catalog, daemon=True).start()
        if self.settings["auto_start"] and self.server_dir() and not self.orphan:
            threading.Thread(target=lambda: self._safe_start("OrbisFox opened"), daemon=True).start()
        threading.Thread(target=self.updates.check, daemon=True).start()

    def _safe_start(self, reason):
        try:
            self.server.start(reason)
        except RuntimeError as e:
            self.log_app(f"Could not start the server: {e}", level="error")

    # ---------------------------------------------------------------------------- console
    def _push(self, text, kind, lvl=""):
        with self._clock:
            self._seq += 1
            ln = {"id": self._seq, "t": time.time(), "text": text, "kind": kind, "lvl": lvl}
            self._console.append(ln)
        self.hub.publish("line", ln)
        return ln

    def console_seq(self):
        return self._seq

    def console_since(self, seq):
        with self._clock:
            return [ln for ln in self._console if ln["id"] > seq]

    def log_app(self, msg, level="info"):
        self._push(msg, "app", level)
        try:
            with open(os.path.join(self.data_dir, "orbisfox.log"), "a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {level.upper():5} {msg}\n")
        except OSError:
            pass

    def echo_command(self, line, source):
        self._push(line, "cmd", source)

    def toast(self, title, body="", level="info", ms=6000):
        self.hub.publish("toast", {"title": title, "body": body, "level": level, "ms": ms})

    def on_line(self, raw):
        text, lvl, logger, msg = hytale.parse_line(raw)
        if not text.strip():
            return
        kind = hytale.classify(lvl, logger, msg)
        if kind == "cmdlog":
            # The server confirms every console command; OrbisFox already echoed its own.
            m = hytale.CMD_ECHO_RE.search(msg)
            last = [ln for ln in list(self._console)[-30:] if ln["kind"] == "cmd"]
            if m and any(ln["text"] == m.group(1).strip() for ln in last):
                return
        self._push(text, kind, lvl)
        if hytale.READY_TEXT in msg:
            self.server.mark_ready()
        m = hytale.JOIN_RE.search(msg)
        if m:
            self.players.joined(m.group(1), m.group(2))
            self.peak_players = max(self.peak_players, len(self.players.online))
            return
        m = hytale.LEAVE_RE.search(msg)
        if m:
            self.players.left(m.group(1), m.group(2))
            return
        if "executed command" in msg and self.vetoes.check_line(msg):
            return
        stall = hytale.stall_signal(msg, logger)
        if stall:
            self.watchdog.on_stall(*stall)
        self._auth_watch(msg)
        if kind in ("join", "leave") or "permission" in msg.lower() or "whitelist" in msg.lower():
            self._access = None

    def _auth_watch(self, msg):
        u = hytale.AUTH_URL_RE.search(msg)
        c = hytale.AUTH_CODE_RE.search(msg) if ("code" in msg.lower()) else None
        if u or c:
            self.auth_prompt = dict(self.auth_prompt or {})
            if u:
                self.auth_prompt["url"] = u.group(1).rstrip(".,)")
            if c:
                self.auth_prompt["code"] = c.group(1)
            self.publish_server()
        elif hytale.AUTH_OK_TEXT in msg:
            if self.auth_prompt or self.auth_needed:
                self.toast("Server signed in", "The server is signed in to Hytale. Click 'Remember sign-in' "
                           "so it stays signed in after restarts.")
            self.auth_prompt, self.auth_needed = None, False
            self.publish_server()
        elif hytale.AUTH_MISSING_RE.search(msg):
            self.auth_needed = True
            self.publish_server()

    # ---------------------------------------------------------------------------- lifecycle hooks
    def on_server_starting(self):
        self.bridge.on_server_start()
        self.watchdog.on_server_start()
        self.auth_prompt = None

    def on_server_ready(self):
        self.log_app("Server is ready for players.")
        self.toast("Server is up", "Players can join now.")
        self._access = None
        threading.Thread(target=self.players.scan_files, daemon=True).start()

    def on_server_exit(self, code):
        self._daily_retry = None
        self.players.all_left()
        self.watchdog.on_server_stop()
        self.players.save(force=True)

    def on_bridge_status(self, st):
        self.players.apply_bridge(st)
        for ev in st.get("events") or []:
            if ev.get("type") == "chat":
                self._push(f"[Chat] {ev.get('player')}: {ev.get('message')}", "chat")
            elif ev.get("type") == "veto":
                self.vetoes.on_veto(ev.get("player") or "An operator")
            elif ev.get("type") == "death":
                killer = f" by {ev['killer']}" if ev.get("killer") else ""
                self._push(f"{ev.get('player')} died ({ev.get('cause', 'unknown')}){killer}", "death")

    # ---------------------------------------------------------------------------- pid file
    def write_pidfile(self, pid):
        try:
            with open(os.path.join(self.data_dir, "server.pid"), "w") as f:
                f.write(str(pid))
        except OSError:
            pass

    def clear_pidfile(self):
        try:
            os.remove(os.path.join(self.data_dir, "server.pid"))
        except OSError:
            pass

    def _check_orphan(self):
        """A server started by an earlier OrbisFox that is still running (we cannot reattach)."""
        try:
            with open(os.path.join(self.data_dir, "server.pid")) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return
        if _pid_is_java(pid):
            self.orphan = {"pid": pid}
        else:
            self.clear_pidfile()

    def kill_orphan(self):
        if not self.orphan:
            return
        pid = self.orphan["pid"]
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           creationflags=0x08000000)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        self.orphan = None
        self.clear_pidfile()
        self.publish_server()

    # ---------------------------------------------------------------------------- metrics
    def _map_loop(self):
        while True:
            try:
                self.worldmap.tick()
            except Exception as e:  # noqa: BLE001
                self.log_app(f"Map housekeeping error: {e}", level="error")
            time.sleep(10)

    def _metrics_loop(self):
        n = 0
        while True:
            try:
                s = self.sampler.sample(self.server.pid, gpu=self.settings.get("track_gpu", False))
                gc = self.gc.summary(window_s=60)
                s["heap_used"] = gc.get("used_after_gc_mb")
                s["heap_max"] = gc.get("max_mb")
                s["gc_overhead"] = gc.get("gc_overhead_percent")
                br = self.bridge.last if self.bridge.connected else None
                if br:
                    worlds = br.get("worlds") or []
                    s["chunks"] = sum(w.get("chunks") or 0 for w in worlds) if worlds else None
                    s["worlds"] = [{k: w.get(k) for k in ("name", "tps", "target_tps", "chunks", "players",
                                                          "latency_ms", "entities")} for w in worlds]
                    tps = [w.get("tps") for w in worlds if w.get("tps") is not None]
                    s["tps"] = min(tps) if tps else None
                    heap = br.get("heap") or {}
                    if heap.get("used"):
                        s["heap_now"] = heap["used"] / 1024 / 1024
                s["players"] = len(self.players.online)
                self.latest = s
                self.history.append(s)
                self.hub.publish("metrics", {"m": s, "gc": gc, "wd": self.watchdog.state})
                n += 1
                if n % 30 == 0:
                    self.players.save()
            except Exception as e:  # noqa: BLE001
                self.log_app(f"Metrics error: {e}", level="error")
            time.sleep(2)

    # ---------------------------------------------------------------------------- schedule
    def _schedule_loop(self):
        while True:
            time.sleep(10)
            try:
                self._daily_restarts()
                self._periodic_backup()
                self._periodic_update_check()
            except Exception as e:  # noqa: BLE001
                self.log_app(f"Scheduler error: {e}", level="error")

    def _daily_restarts(self):
        r = self.settings["restarts"]
        if not self.server.running or not r.get("daily"):
            self._daily_retry = None
            return
        tmpl = r.get("message") or "Server restarting in {time}."
        if self._daily_retry and not self.server.pending and not self.vetoes.blocked("daily"):
            # An operator cancelled it; the cool-down is over, so try again (with a short warning).
            label, self._daily_retry = self._daily_retry, None
            if not self.server.schedule("restart", 60, label, message=tmpl, kind="daily"):
                self._daily_retry = label
            return
        warn = max([int(x) for x in r.get("warn_minutes") or [0]] + [0]) * 60
        now = dt.datetime.now()
        for hhmm in r["daily"]:
            try:
                h, m = (int(x) for x in str(hhmm).split(":"))
            except ValueError:
                continue
            at = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if at < now - dt.timedelta(minutes=1):
                at += dt.timedelta(days=1)
            key = f"{at:%Y-%m-%d %H:%M}"
            left = (at - now).total_seconds()
            if left <= warn and key not in self._schedule_done and not self.server.pending:
                self._schedule_done.add(key)
                label = f"daily restart at {hhmm}"
                if not self.server.schedule("restart", max(0, int(left)), label, message=tmpl, kind="daily"):
                    self._daily_retry = label

    def on_vetoed(self, kind, label):
        """Called after an operator cancels an automated action."""
        if kind == "daily":
            self._daily_retry = label

    def _periodic_backup(self):
        every = int(self.settings["backups"].get("every_minutes") or 0)
        if every <= 0 or not self.server.running or self.backups.busy:
            return
        last = next((b for b in self.backups.list() if b["reason"] == "scheduled"), None)
        if not last or time.time() - last["created"] >= every * 60:
            threading.Thread(target=self._bg_backup, args=("scheduled",), daemon=True).start()

    def _bg_backup(self, reason):
        try:
            self.backups.create(reason)
        except RuntimeError as e:
            self.log_app(f"Backup failed: {e}", level="error")
            self.toast("Backup failed", str(e), "bad")

    def _periodic_update_check(self):
        u = self.settings["updates"]
        hours = float(u.get("check_every_hours") or 0)
        if hours <= 0:
            return
        if not self.updates.checked_at or time.time() - self.updates.checked_at >= hours * 3600:
            self.updates.check()
            if self.updates.available():
                self.toast("Server update available",
                           f"{self.updates.installed_version()} → {self.updates.latest['version']}", "info", 0)
        mode = u.get("auto_install")
        mode = "empty" if mode is True else (mode or "off")
        if mode == "off" or not self.updates.available() or self.updates.busy or not self.server.running:
            return
        if self.vetoes.blocked("update"):
            return
        if not self.players.online:
            self.log_app("Installing the server update automatically (nobody is online).")
            self.updates.install("auto", warn=False)
        elif mode == "always":
            self.log_app("Installing the server update automatically; players get 5 minutes' warning.")
            self.updates.install("auto", warn=True, automated=True)

    # ---------------------------------------------------------------------------- access
    def access_cache(self, fresh=False):
        sd = self.server_dir()
        if not sd:
            return {"whitelist": {"enabled": None, "entries": []}, "ops": [], "bans": []}
        if fresh or not self._access or time.time() - self._access_at > 20:
            self._access = hytale.read_access(sd, self.players.names())
            self._access_at = time.time()
        return self._access

    def refresh_access_soon(self, delay=1.5):
        def go():
            time.sleep(delay)
            self._access = None
            self.publish_access()
        threading.Thread(target=go, daemon=True).start()

    # ---------------------------------------------------------------------------- publishing
    def server_info(self):
        lay = hytale.locate(self.settings["server_dir"])
        cfg = hytale.read_config(lay["server_dir"]) if lay["jar"] else {}
        return dict(self.server.info(), problems=lay["problems"], server_dir=lay["server_dir"],
                    game_dir=lay["game_dir"], version=self.updates.installed_version(),
                    name=cfg.get("ServerName"), max_players=cfg.get("MaxPlayers"),
                    view_radius=cfg.get("MaxViewRadius"), auth_prompt=self.auth_prompt,
                    auth_needed=self.auth_needed, orphan=self.orphan, bridge=self.bridge.info(),
                    vetoes=self.vetoes.info(), imported=self.settings.get("imported"),
                    other_servers=self.server.other_servers)

    def publish_server(self):
        self.hub.publish("server", self.server_info())

    def publish_players(self):
        self.hub.publish("players", {"online": self.players.online_list()})

    def publish_access(self):
        self.hub.publish("access", self.access_cache())

    def publish_backups(self):
        self.hub.publish("backups", self.backups_info())

    def publish_updates(self):
        self.hub.publish("updates", self.updates.info())

    def publish_watchdog(self):
        self.hub.publish("watchdog", self.watchdog.info())

    def backups_info(self):
        own = self.backups.list()
        sd = self.server_dir()
        b = self.settings["backups"]
        builtin = hytale.hytale_backups(sd, b.get("hytale_dir") or "backups") if sd else []
        for x in builtin:
            x.pop("path", None)
        newest = max([x["created"] for x in own + builtin] or [0]) or None
        return {"list": own, "builtin": builtin[:30], "busy": self.backups.busy, "last": newest,
                "dir": self.backups.dir}

    def snapshot(self):
        return {
            "app": {"version": __version__, "data_dir": self.data_dir, "app_dir": APP_DIR,
                    "os": platform.system(), "python": sys.version.split()[0], "started": self._started},
            "settings": self.settings.public(),
            "server": self.server_info(),
            "console": list(self._console)[-1500:],
            "players": {"online": self.players.online_list()},
            "metrics": {"latest": self.latest, "history": list(self.history)},
            "watchdog": self.watchdog.info(),
            "backups": self.backups_info(),
            "updates": self.updates.info(),
            "access": self.access_cache(),
            "catalog": [{"cmd": c, "args": a, "desc": d} for c, a, d in hytale.CATALOG],
        }

    def take_over_server_updates(self):
        """The server can install updates by itself (config.json Update.AutoApplyMode), which would
        skip OrbisFox's warnings and the operators' veto. OrbisFox switches that off before each start
        and does the same job itself (Updates panel: install automatically)."""
        if not self.settings["updates"].get("manage_server_updates", True):
            return
        sd = self.server_dir()
        cfg = hytale.read_config(sd) if sd else {}
        u = cfg.get("Update") if isinstance(cfg.get("Update"), dict) else None
        mode = (u or {}).get("AutoApplyMode")
        if u is None or mode in (None, "Disabled"):
            return
        if self.settings["updates"].get("auto_install") in (None, False, "off"):
            self.settings.update({"updates": {"auto_install": "always" if mode == "Scheduled" else "empty"}})
        u["AutoApplyMode"] = "Disabled"
        try:
            hytale.write_json(os.path.join(sd, "config.json"), cfg)
            self.log_app(f"The server was set to install updates by itself ({mode}). OrbisFox does this now, "
                         "with warnings and the operators' veto; see Updates.")
        except OSError as e:
            self.log_app(f"Could not change the server's own update setting: {e}", level="warn")

    def import_server(self, server_dir):
        from . import importer
        notes = importer.apply(self, server_dir)
        if notes:
            self.log_app("Imported your server's existing setup: " + "; ".join(notes))
            self.toast("Imported your server's settings", "; ".join(notes[:6]), "info", 15000)
        self.players.scan_files()
        self._access = None
        self.hub.publish("settings", self.settings.public())
        self.publish_server()
        return notes

    def open_path(self, path):
        if not path or not os.path.exists(path):
            raise FileNotFoundError("That folder does not exist yet.")
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 - opening Explorer on the server PC is the point
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def shutdown(self):
        """OrbisFox is closing: stop the server properly first."""
        if self.server.alive:
            self.server.stop("OrbisFox is closing")
            end = time.time() + int(self.settings["stop_timeout_s"]) + 10
            while self.server.alive and time.time() < end:
                time.sleep(0.5)
        self.players.save(force=True)


def _pid_is_java(pid):
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=10, creationflags=0x08000000).stdout.lower()
            return "java" in out
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"java" in f.read()
    except OSError:
        return False


def dump(obj):
    return json.dumps(obj, default=str)
