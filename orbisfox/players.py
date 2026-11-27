"""Who is online, who has ever played, and their lifetime stats.

Join and leave come from the server's log. Positions, pings and the counters (distance walked,
deaths, kills, blocks) come from the OrbisFox Bridge plugin when it is installed. The plugin's
counters restart with the server, so they are merged here as deltas into data/players.json.
"""
import json
import os
import threading
import time

from . import hytale

STAT_KEYS = ("distance", "deaths", "mob_kills", "player_kills", "damage_dealt", "damage_taken",
             "blocks_broken", "blocks_placed", "chat_messages")


class Players:
    def __init__(self, core):
        self.core = core
        self.path = os.path.join(core.data_dir, "players.json")
        self._lock = threading.RLock()
        self.online = {}       # uuid -> {uuid, name, joined_at, world, x, y, z, ping}
        self.known = {}        # uuid -> {name, first_seen, last_seen, playtime, sessions, stats{}}
        self._bridge_seen = {}  # (run, uuid) -> last counters
        self._dirty = False
        self.events = []       # recent deaths/kills feed
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.known = data.get("players", {})
            self.events = data.get("events", [])[-200:]
        except (OSError, ValueError):
            pass
        for p in self.known.values():   # names garbled by OrbisFox 1.2.0 and older (see hytale.LEAVE_RE)
            n = p.get("name")
            if isinstance(n, str) and ("'" in n or " (" in n):
                p["name"] = n.split(" (")[0].split("'")[0] or n

    # ---------------------------------------------------------------------------- names
    def names(self):
        with self._lock:
            m = {u: p.get("name") for u, p in self.known.items() if p.get("name")}
            m.update({u: p["name"] for u, p in self.online.items()})
            return m

    def uuid_of(self, name):
        low = (name or "").lower()
        with self._lock:
            for u, p in list(self.online.items()) + list(self.known.items()):
                if (p.get("name") or "").lower() == low:
                    return u
        return None

    def _entry(self, uuid, name=None):
        p = self.known.get(uuid)
        if not p:
            p = self.known[uuid] = {"name": name, "first_seen": time.time(), "last_seen": time.time(),
                                    "playtime": 0.0, "sessions": 0, "stats": {k: 0 for k in STAT_KEYS}}
        if name:
            p["name"] = name
        p.setdefault("stats", {})
        for k in STAT_KEYS:
            p["stats"].setdefault(k, 0)
        return p

    def scan_files(self):
        """Learns names (and seed data) from the server's saved player files."""
        sd = self.core.server_dir()
        if not sd:
            return
        saved = hytale.read_players(sd)
        with self._lock:
            for uuid, info in saved.items():
                p = self._entry(uuid, info.get("name") or self.known.get(uuid, {}).get("name"))
                p["saved"] = {k: info.get(k) for k in ("world", "x", "y", "z", "gamemode", "death_markers")}
                p["last_seen"] = max(p.get("last_seen") or 0, info.get("saved") or 0)
            self._dirty = True

    def import_history(self, server_dir, before):
        """Adds playtime and sessions from the server's logs up to `before` (when OrbisFox started
        counting). Returns how many players it found."""
        hist = hytale.read_log_sessions(server_dir, before)
        with self._lock:
            for uuid, h in hist.items():
                p = self._entry(uuid, self.known.get(uuid, {}).get("name") or h["name"])
                p["playtime"] = p.get("playtime", 0) + h["playtime"]
                p["sessions"] = p.get("sessions", 0) + h["sessions"]
                p["first_seen"] = min(p.get("first_seen") or h["first_seen"], h["first_seen"])
                p["last_seen"] = max(p.get("last_seen") or 0, h["last_seen"] or 0)
                p["from_logs"] = {"sessions": h["sessions"], "playtime": round(h["playtime"])}
            self._dirty = True
        self.save()
        return len(hist)

    # ---------------------------------------------------------------------------- log events
    def joined(self, name, uuid):
        uuid = uuid.lower()
        with self._lock:
            if uuid in self.online:
                return
            p = self._entry(uuid, name)
            p["sessions"] = p.get("sessions", 0) + 1
            p["last_seen"] = time.time()
            self.online[uuid] = {"uuid": uuid, "name": name, "joined_at": time.time(), "world": None,
                                 "x": None, "y": None, "z": None, "ping": None}
            self._dirty = True
        self.core.publish_players()

    def left(self, name, uuid):
        uuid = uuid.lower()
        with self._lock:
            o = self.online.pop(uuid, None)
            if not o:
                return
            p = self._entry(uuid, name)
            p["playtime"] = p.get("playtime", 0) + (time.time() - o["joined_at"])
            p["last_seen"] = time.time()
            if o.get("x") is not None:
                p["saved"] = {"world": o.get("world"), "x": o["x"], "y": o["y"], "z": o["z"]}
            self._dirty = True
        self.core.publish_players()
        self.save()

    def all_left(self):
        """Server stopped or crashed: close every open session."""
        for uuid, o in list(self.online.items()):
            self.left(o["name"], uuid)

    # ---------------------------------------------------------------------------- bridge data
    def apply_bridge(self, status):
        run = status.get("run")
        changed = False
        with self._lock:
            for pl in status.get("players") or []:
                uuid = (pl.get("uuid") or "").lower()
                if not uuid:
                    continue
                o = self.online.get(uuid)
                if not o:   # joined before OrbisFox saw the log line
                    self.online[uuid] = o = {"uuid": uuid, "name": pl.get("name"), "joined_at": time.time()}
                    self._entry(uuid, pl.get("name"))["sessions"] += 1
                for k in ("world", "x", "y", "z", "yaw"):
                    o[k] = pl.get(k)
                o["ping"] = pl.get("ping_ms")
                changed = True
            for uuid, counters in (status.get("stats") or {}).items():
                uuid = uuid.lower()
                key = (run, uuid)
                last = self._bridge_seen.get(key, {})
                p = self._entry(uuid, (self.online.get(uuid) or {}).get("name"))
                for k in STAT_KEYS:
                    cur = counters.get(k)
                    if cur is None:
                        continue
                    delta = cur - last.get(k, 0)
                    if delta > 0:
                        p["stats"][k] = round(p["stats"].get(k, 0) + delta, 2)
                        self._dirty = True
                self._bridge_seen[key] = dict(counters)
            for ev in status.get("events") or []:
                if ev.get("type") in ("death", "kill"):
                    self.events.append(ev)
                    self.events = self.events[-200:]
                    self._dirty = True
        if changed:
            self.core.publish_players()

    # ---------------------------------------------------------------------------- views
    def online_list(self):
        with self._lock:
            out = []
            ops = {o["uuid"] for o in self.core.access_cache().get("ops", [])}
            for o in self.online.values():
                d = dict(o)
                d["op"] = o["uuid"] in ops
                d["session"] = time.time() - o["joined_at"]
                out.append(d)
            out.sort(key=lambda x: (x.get("name") or "").lower())
            return out

    def table(self):
        with self._lock:
            rows = []
            now = time.time()
            for uuid, p in self.known.items():
                o = self.online.get(uuid)
                playtime = p.get("playtime", 0) + ((now - o["joined_at"]) if o else 0)
                rows.append({"uuid": uuid, "name": p.get("name") or uuid[:8], "online": bool(o),
                             "first_seen": p.get("first_seen"), "last_seen": now if o else p.get("last_seen"),
                             "sessions": p.get("sessions", 0), "playtime": playtime,
                             "stats": p.get("stats", {}), "saved": p.get("saved")})
            rows.sort(key=lambda r: r["playtime"], reverse=True)
            return {"players": rows, "events": list(reversed(self.events[-50:])),
                    "bridge": self.core.bridge.connected}

    # ---------------------------------------------------------------------------- persistence
    def save(self, force=False):
        with self._lock:
            if not (self._dirty or force):
                return
            data = {"players": self.known, "events": self.events[-200:]}
            self._dirty = False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
