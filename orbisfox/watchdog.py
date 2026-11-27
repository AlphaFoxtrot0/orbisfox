"""Spots the "too many chunks loaded in too many places" slowdown and recovers from it.

What it watches (see docs/chunk-freeze.md for the why):
  * Java heap that is still nearly full right after garbage collection, and the share of time the
    server spends paused in GC (from the GC log OrbisFox asks Java to write);
  * the server's own warnings that a world tick ran long ("... > TICK_STEP") or a world thread
    stopped accepting work;
  * with the OrbisFox Bridge plugin: each world's tick rate and how long the world thread takes
    to pick up a trivial task (a stuck world can't).

What it does:
  1. Adaptive view distance: while memory is tight it lowers the server's view radius two chunks
     at a time (live, with the `maxviewradius` command), so chunks far from players unload. When
     things have been calm for a while it raises it back.
  2. If the server stays critical anyway, it warns the players and restarts the server.
"""
import collections
import threading
import time

from . import hytale

MIN_RADIUS = 6
STEP = 2


class Watchdog:
    def __init__(self, core):
        self.core = core
        self.state = "idle"        # idle | ok | strained | critical
        self.reasons = []
        self.stalls = collections.deque(maxlen=500)   # (t, kind, dur_ms, world)
        self.events = collections.deque(maxlen=100)   # shown in the Performance panel
        self._critical_since = None
        self._strained_since = None
        self._calm_since = None
        self._last_radius_change = 0
        self._last_unload = 0
        self._restarts = []
        self.current_radius = None
        self.baseline_radius = None
        threading.Thread(target=self._loop, name="watchdog", daemon=True).start()

    # ---------------------------------------------------------------------------- inputs
    def on_stall(self, kind, dur, world):
        self.stalls.append((time.time(), kind, dur, world))
        if kind == "out_of_memory":
            self.note("critical", "The server ran out of memory (OutOfMemoryError).")

    def on_server_start(self):
        self.stalls.clear()
        self._critical_since = self._strained_since = None
        self._calm_since = time.time()
        cfg = hytale.read_config(self.core.server_dir()) if self.core.server_dir() else {}
        wd = self.core.settings["watchdog"]
        configured = cfg.get("MaxViewRadius")
        # If an earlier session lowered the radius and could not put it back, start from the baseline.
        self.baseline_radius = wd.get("baseline_radius") or configured or 32
        self.current_radius = configured or self.baseline_radius

    def on_server_stop(self):
        self.state = "idle"
        self.reasons = []
        self._restore_radius_file()

    def note(self, level, msg):
        self.events.append({"t": time.time(), "level": level, "msg": msg})
        self.core.publish_watchdog()

    # ---------------------------------------------------------------------------- evaluation
    def evaluate(self, now=None):
        now = now or time.time()
        wd = self.core.settings["watchdog"]
        gc = self.core.gc.summary(window_s=int(wd.get("window_s", 120)), now=now)
        reasons, level = [], "ok"

        def bump(lv, why):
            nonlocal level
            order = {"ok": 0, "strained": 1, "critical": 2}
            if order[lv] > order[level]:
                level = lv
            reasons.append(why)

        live = gc.get("live_percent")
        if live is not None:
            if live >= wd.get("heap_percent", 92):
                bump("critical", f"Java heap is {live:.0f}% full even after garbage collection")
            elif live >= 78:
                bump("strained", f"Java heap is {live:.0f}% full after garbage collection")
        over = gc.get("gc_overhead_percent") or 0
        if over >= wd.get("gc_overhead_percent", 40):
            bump("critical", f"{over:.0f}% of the time is spent paused for garbage collection")
        elif over >= 12:
            bump("strained", f"{over:.0f}% of the time is spent paused for garbage collection")
        if gc.get("full_gcs", 0) >= 2:
            bump("strained", f"{gc['full_gcs']} full garbage collections in the last {wd.get('window_s', 120)} s")

        # Only the last minute of warnings can make things critical, so a server that has
        # recovered is not restarted because of what it printed earlier.
        recent = [s for s in self.stalls if now - s[0] <= 120]
        last_min = [s for s in recent if now - s[0] <= 60]
        long_min = [s for s in last_min if s[1] == "tick_overrun" and (s[2] or 0) >= 1000]
        long_ticks = [s for s in recent if s[1] == "tick_overrun" and (s[2] or 0) >= 1000]
        if any(s[1] in ("world_rejecting", "ticking_abandoned") for s in last_min):
            bump("critical", "A world thread stopped accepting work")
        elif len(long_min) >= 8:
            bump("critical", f"{len(long_min)} world ticks took over a second in the last minute")
        elif len(long_ticks) >= 3:
            bump("strained", f"{len(long_ticks)} world ticks took over a second in the last 2 minutes")
        elif len(recent) >= 5 or any(s[1] != "tick_overrun" for s in recent):
            # A few short overruns (a save, a GC) are normal; a steady stream is not.
            bump("strained", f"{len(recent)} slow-tick warnings in the last 2 minutes")

        br = self.core.bridge
        if br.connected and br.last:
            for w in br.last.get("worlds") or []:
                lat = w.get("latency_ms") or 0
                if lat >= int(wd.get("unresponsive_s", 60)) * 1000:
                    bump("critical", f"World '{w.get('name')}' has not responded for {lat / 1000:.0f} s")
                elif lat >= 3000:
                    bump("strained", f"World '{w.get('name')}' is answering slowly ({lat / 1000:.1f} s)")
                tps, target = w.get("tps"), w.get("target_tps") or 30
                if tps is not None and w.get("players"):
                    if tps < target * 0.4:
                        bump("critical" if tps < target * 0.2 else "strained",
                             f"World '{w.get('name')}' runs at {tps:.0f} of {target} ticks per second")
                    elif tps < target * 0.7:
                        bump("strained", f"World '{w.get('name')}' runs at {tps:.0f} of {target} ticks per second")
        return level, reasons, gc

    def _loop(self):
        while True:
            time.sleep(5)
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self.core.log_app(f"Watchdog error: {e}", level="error")

    def tick(self):
        sv = self.core.server
        wd = self.core.settings["watchdog"]
        if not sv.running:
            if self.state != "idle":
                self.state, self.reasons = "idle", []
                self.core.publish_watchdog()
            return
        if sv.ready_at and time.time() - sv.ready_at < 60:
            return   # boot is always heavy
        now = time.time()
        level, reasons, _gc = self.evaluate(now)
        changed = level != self.state or reasons != self.reasons
        self.state, self.reasons = level, reasons
        if level == "ok":
            self._critical_since = self._strained_since = None
            self._calm_since = self._calm_since or now
        else:
            self._calm_since = None
            self._strained_since = self._strained_since or now
            if level == "critical":
                self._critical_since = self._critical_since or now
            else:
                self._critical_since = None
        if changed:
            if level != "ok":
                self.note(level, "; ".join(reasons))
            self.core.publish_watchdog()
        if not wd.get("enabled", True):
            return
        self._adapt_radius(level, reasons, now)
        if level == "critical" and wd.get("try_chunk_unload", True) and now - self._last_unload >= 300:
            # A cheap first aid before any restart: ask the server to drop chunks nobody is near.
            self._last_unload = now
            try:
                self.core.server.send("chunk unload", source="watchdog")
                self.note("info", "Asked the server to unload chunks no player needs")
            except RuntimeError:
                pass
        if level == "critical" and self._critical_since and now - self._critical_since >= int(wd.get("window_s", 120)):
            self._recover(reasons, now)

    # ---------------------------------------------------------------------------- actions
    def _adapt_radius(self, level, reasons, now):
        wd = self.core.settings["watchdog"]
        if not wd.get("adaptive_view", True) or self.current_radius is None:
            return
        memoryish = any(("heap" in r or "garbage" in r or "tick" in r or "responding" in r) for r in reasons)
        if level != "ok" and memoryish and self._strained_since and now - self._strained_since >= 30 \
                and now - self._last_radius_change >= 90 and self.current_radius > MIN_RADIUS:
            new = max(MIN_RADIUS, self.current_radius - STEP)
            self._set_radius(new, f"Lowered view distance to {new} chunks to free memory")
        elif level == "ok" and self._calm_since and now - self._calm_since >= 600 \
                and now - self._last_radius_change >= 600 and self.current_radius < (self.baseline_radius or 32):
            new = min(self.baseline_radius, self.current_radius + STEP)
            self._set_radius(new, f"Raised view distance back to {new} chunks")

    def _set_radius(self, n, msg):
        try:
            self.core.server.send(f"maxviewradius {int(n)}", source="watchdog")
        except RuntimeError:
            return
        if self.baseline_radius and n < self.baseline_radius:
            self.core.settings.update({"watchdog": {"baseline_radius": self.baseline_radius}})
        elif n >= (self.baseline_radius or 0):
            self.core.settings.update({"watchdog": {"baseline_radius": None}})
        self.current_radius = n
        self._last_radius_change = time.time()
        self.core.log_app(msg)
        self.note("info", msg)

    def _restore_radius_file(self):
        """After the server stops, put the configured view distance back in config.json."""
        base = self.core.settings["watchdog"].get("baseline_radius")
        sd = self.core.server_dir()
        if not base or not sd or self.core.server.alive:
            return
        try:
            cfg = hytale.read_config(sd)
            if cfg and cfg.get("MaxViewRadius") != base:
                hytale.update_config(sd, {"MaxViewRadius": base})
            self.core.settings.update({"watchdog": {"baseline_radius": None}})
        except (OSError, RuntimeError, ValueError):
            pass

    def _recover(self, reasons, now):
        wd = self.core.settings["watchdog"]
        sv = self.core.server
        if sv.pending:
            return
        self._restarts = [t for t in self._restarts if now - t < 3600]
        if self._restarts and now - self._restarts[-1] < 900:
            return
        why = "; ".join(reasons)
        if wd.get("action", "restart") != "restart" or len(self._restarts) >= 3:
            if len(self._restarts) >= 3:
                why += " (3 automatic restarts in the last hour: waiting for you)"
            self.core.toast("Server is struggling", why, "bad", 0)
            self._critical_since = now   # remind again after another window
            return
        left = self.core.vetoes.blocked("watchdog")
        if left:
            if now - getattr(self, "_veto_noted", 0) > 60:
                self._veto_noted = now
                self.note("info", f"An operator cancelled the last recovery restart; trying again in {left / 60:.0f} min")
            return
        warn = int(wd.get("warn_seconds", 60)) if wd.get("warn_players", True) else 0
        if not sv.schedule("restart", warn, "watchdog: server overloaded", kind="watchdog",
                           message="The server is overloaded and will restart in {time} to recover."):
            return
        self._restarts.append(now)
        self._critical_since = None
        self.note("critical", f"Restarting to recover: {why}")
        self.core.toast("Restarting to recover", why, "warn", 15000)

    def info(self):
        gc = self.core.gc.summary(window_s=int(self.core.settings["watchdog"].get("window_s", 120)))
        recent = [s for s in self.stalls if time.time() - s[0] <= 600]
        return {"state": self.state, "reasons": self.reasons, "gc": gc,
                "stalls_10min": len(recent), "events": list(self.events)[-30:],
                "view_radius": self.current_radius, "baseline_radius": self.baseline_radius,
                "restarts_last_hour": len([t for t in self._restarts if time.time() - t < 3600])}


def recommend(core):
    """Plain-language advice for the Performance panel."""
    s = core.settings
    tips = []
    total, _used = core.sampler.system_memory()
    total_gb = (total or 0) / 1024 ** 3
    peak = max(len(core.players.online), core.peak_players)
    want = 6 if peak <= 3 else 8 if peak <= 6 else 10 if peak <= 10 else 12
    if total_gb:
        want = min(want, max(4, int(total_gb * 0.6)))
    have = float(s["ram_max_gb"])
    if have < want:
        tips.append({"level": "warn", "id": "ram",
                     "text": f"Give the server more memory: {want} GB instead of {have:g} GB. "
                             f"Every loaded chunk lives in this memory, so players exploring apart need more.",
                     "apply": {"ram_max_gb": want, "ram_min_gb": min(float(s['ram_min_gb']), want)}})
    elif total_gb and have > total_gb * 0.75:
        tips.append({"level": "warn", "id": "ram-high",
                     "text": f"{have:g} GB is most of this PC's {total_gb:.0f} GB. Windows and Java's other memory "
                             f"need room too; {int(total_gb * 0.6)} GB is safer.",
                     "apply": {"ram_max_gb": int(total_gb * 0.6)}})
    sd = core.server_dir()
    cfg = hytale.read_config(sd) if sd else {}
    radius = cfg.get("MaxViewRadius")
    if radius and radius > 12:
        tips.append({"level": "warn", "id": "radius",
                     "text": f"View distance is {radius} chunks ({radius * 32} blocks). Hytale recommends 12. "
                             f"Memory grows with the square of this number, per area players are in.",
                     "apply_config": {"MaxViewRadius": 12}})
    if not s["exit_on_oom"]:
        tips.append({"level": "info", "id": "oom", "text": "Turn on 'Exit when out of memory' so a server that runs "
                     "out of memory restarts instead of hanging.", "apply": {"exit_on_oom": True}})
    if not s["gc_log"]:
        tips.append({"level": "info", "id": "gclog", "text": "Turn on the GC log so OrbisFox can see memory pressure.",
                     "apply": {"gc_log": True}})
    if not s["watchdog"].get("adaptive_view", True):
        tips.append({"level": "info", "id": "adaptive", "text": "Turn on adaptive view distance: OrbisFox lowers the "
                     "view distance while memory is tight instead of letting the server stall.",
                     "apply": {"watchdog": {"adaptive_view": True}}})
    ver = core.updates.installed_version()
    if ver and hytale.version_key(ver) < hytale.version_key("0.8.0"):
        tips.append({"level": "info", "id": "update8",
                     "text": "Hytale Update 8 fixes the server running out of memory and stalling while players fly "
                             "through new terrain, unloads unused chunk sections and fixes two memory leaks. "
                             "Install it when it leaves pre-release."})
    if not core.bridge.connected:
        tips.append({"level": "info", "id": "bridge",
                     "text": "Install the OrbisFox Bridge plugin to see each world's tick rate, loaded chunks "
                             "and pings, and to catch a stuck world within seconds."})
    return tips
