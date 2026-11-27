"""Lets in-game operators cancel anything OrbisFox does on its own that would take the server down
(scheduled restarts, watchdog recovery restarts, automatic updates).

While such an action counts down with players online, operators are told (privately, through the
OrbisFox Bridge plugin) that they can type /veto to cancel it. A cancelled action of that kind
then waits a cool-down (5 minutes by default) before it may try again. Actions you start yourself
from the panel are never vetoable, and nothing is announced when nobody is online.
"""
import re
import threading
import time

LABELS = {"daily": "scheduled restart", "watchdog": "recovery restart", "update": "update install"}
# "<name> executed command: veto" is printed by the server (INFO) when an operator runs the command.
EXEC_RE = re.compile(r"^(?:\[[^\]]*\]\s*)*(\S+) executed command: /?(?:veto|orbisfox veto)\b", re.I)


class Vetoes:
    def __init__(self, core):
        self.core = core
        self._lock = threading.Lock()
        self.active = None        # {"kind", "label", "at", "cancel": callable, "event": Event}
        self.cooldowns = {}       # kind -> time it may run again
        self.history = []         # recent vetoes for the panel
        self._last = (None, 0)    # the console line and the plugin both report a veto

    @property
    def enabled(self):
        return self.core.settings.get("veto", {}).get("enabled", True)

    def cooldown_s(self):
        return max(0, float(self.core.settings.get("veto", {}).get("cooldown_minutes", 5))) * 60

    def blocked(self, kind):
        """Seconds left in this kind's cool-down, or 0."""
        until = self.cooldowns.get(kind, 0)
        return max(0, until - time.time())

    def begin(self, kind, seconds, cancel, reason=None):
        """An automated action of `kind` starts counting down. Returns an Event set on veto."""
        ev = threading.Event()
        if not self.enabled or not self.core.players.online:
            return ev
        label = LABELS.get(kind, kind)
        with self._lock:
            self.active = {"kind": kind, "label": label, "at": time.time() + seconds, "cancel": cancel, "event": ev,
                           "reason": reason}
        mins = seconds / 60
        when = f"{int(round(mins))} minute{'s' if round(mins) != 1 else ''}" if seconds >= 90 else f"{int(seconds)} seconds"
        cmd = self.core.settings.get("veto", {}).get("command", "veto")
        self.core.bridge.notify_ops(f"[OrbisFox] Automatic {label} in {when}. Operators: type /{cmd} to cancel it.")
        self.core.publish_server()
        return ev

    def can_veto(self):
        """Operators can only type /veto when the Bridge plugin (which adds the command) is loaded."""
        return self.enabled and self.core.bridge.connected

    def hint(self):
        """The bit added to a countdown warning in chat, when a veto is possible."""
        if not self.can_veto():
            return ""
        cmd = self.core.settings.get("veto", {}).get("command", "veto")
        return f" Operators can type /{cmd} to cancel."

    def end(self, kind):
        with self._lock:
            if self.active and self.active["kind"] == kind:
                self.active = None
        self.core.publish_server()

    def on_veto(self, player):
        """An operator ran /veto (seen through the plugin or the console)."""
        if self._last[0] == player.lower() and time.time() - self._last[1] < 10:
            return False
        self._last = (player.lower(), time.time())
        with self._lock:
            act = self.active
            self.active = None
        if not act:
            self.core.bridge.notify_ops(f"[OrbisFox] {player}: there is nothing to cancel right now.")
            return False
        cd = self.cooldown_s()
        self.cooldowns[act["kind"]] = time.time() + cd
        act["event"].set()
        try:
            act["cancel"]()
        except Exception as e:  # noqa: BLE001
            self.core.log_app(f"Cancelling the {act['label']} failed: {e}", level="error")
        mins = cd / 60
        again = f" OrbisFox will try again in {mins:g} minute{'s' if mins != 1 else ''}." if cd else ""
        msg = f"{player} cancelled the automatic {act['label']}.{again}"
        self.history = (self.history + [{"t": time.time(), "player": player, "kind": act["kind"]}])[-20:]
        self.core.on_vetoed(act["kind"], act.get("reason") or act["label"])
        self.core.log_app(msg, level="warn")
        self.core.toast("Cancelled in game", msg, "warn", 12000)
        if self.core.server.running:
            self.core.server.warn_players(msg, level="success")
        self.core.publish_server()
        return True

    def check_line(self, message):
        """The server logs "<name> executed command: veto" before it checks permissions, so a player
        who isn't an operator produces the same line. Only operators count."""
        m = EXEC_RE.search(message)
        if not m or m.group(1).lower() in ("console", "server"):
            return False
        if not self.core.is_op(m.group(1)):
            self.core.log_app(f"{m.group(1)} typed /veto but isn't an operator, so it was ignored.", level="warn")
            return True
        self.on_veto(m.group(1))
        return True

    def info(self):
        a = self.active
        now = time.time()
        return {"enabled": self.enabled,
                "active": {"kind": a["kind"], "label": a["label"], "at": a["at"]} if a else None,
                "cooldowns": {k: round(v - now) for k, v in self.cooldowns.items() if v > now},
                "history": self.history[-5:], "bridge": self.core.bridge.connected}
