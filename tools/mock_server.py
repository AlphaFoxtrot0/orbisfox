"""A pretend Hytale server for trying OrbisFox without one (demo mode) and for testing.

It prints the real Hytale log format, answers the common console commands, keeps config.json,
permissions.json and bans.json like the real server, lets a few made-up players wander about,
writes a Java-style GC log, and serves the OrbisFox Bridge API (players, chunks, map tiles).

Type `stress` in the OrbisFox console to make the made-up players scatter and load chunks all
over the map: memory climbs, ticks slow down and the server prints the same warnings a real one
does, so you can watch the watchdog step in. `calm` brings them back together.

Run by OrbisFox in demo mode; not useful on its own.
"""
import json
import math
import os
import random
import re
import struct
import sys
import threading
import time
import uuid as uuidlib
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

if "-version" in sys.argv or "--version" in sys.argv:
    sys.stderr.write('openjdk version "25.0.1" 2025-10-21 LTS\nOpenJDK Runtime Environment (mock)\n')
    sys.exit(0)

OUT_LOCK = threading.Lock()


def _jar_version():
    try:
        import zipfile
        with zipfile.ZipFile("HytaleServer.jar") as z:
            for line in z.read("META-INF/MANIFEST.MF").decode().splitlines():
                if line.startswith("Implementation-Version:"):
                    return line.split(":", 1)[1].strip()
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    return "0.7.3"


VERSION = _jar_version()
START = time.time()
CHUNK = 32
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from orbisfox.demo import CREATURES as _DEMO   # the made-up creatures in the demo Assets.zip
    DEMO_IDS = [c[0] for c in _DEMO]
except ImportError:
    DEMO_IDS = []
RESTORED = set(DEMO_IDS[:7:2])
BRIDGE = {"world": None}       # set when the bridge API starts (for events from console commands)


def log(level, logger, msg):
    ts = time.strftime("%Y/%m/%d %H:%M:%S")
    tag = f"[{logger}] " if logger else ""
    line = f"[{ts} {level:>6}] {tag}{msg}"
    if level == "SEVERE":
        line = f"\x1b[31m{line}\x1b[0m"
    elif level == "WARN":
        line = f"\x1b[33m{line}\x1b[0m"
    with OUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ------------------------------------------------------------------------------------- world
NAMES = ["Kweebec_Kid", "TrorkSlayer", "Feran_Fox", "Varyn", "MossyMaven", "Scarak_Bane"]


class Player:
    def __init__(self, name):
        self.name = name
        self.uuid = str(uuidlib.uuid5(uuidlib.NAMESPACE_DNS, "orbisfox-" + name))
        a = random.random() * math.tau
        self.x, self.z = math.cos(a) * 80, math.sin(a) * 80
        self.y = 72.0
        self.heading = random.random() * math.tau
        self.ping = random.randint(18, 90)
        self.stats = {"distance": 0.0, "deaths": 0, "mob_kills": 0, "player_kills": 0, "damage_dealt": 0,
                      "damage_taken": 0, "blocks_broken": 0, "blocks_placed": 0, "chat_messages": 0}
        self.target = None


class World:
    def __init__(self, root):
        self.root = root
        self.players = {}
        self.pending_join = list(NAMES[:4])
        random.shuffle(self.pending_join)
        self.stress = False
        self.radius = 12
        self.time = "midday"
        self.paused = False
        self.events = []
        self.seq = 0
        self.heap_live = 900.0      # MB that is really in use
        self.heap_max = self._xmx()
        self.tps = 30.0
        self.latency = 2
        self.generated = set()
        self.trail = {}             # chunk -> time it may unload
        self.lock = threading.RLock()
        self.cfg_path = os.path.join(root, "config.json")
        cfg = read_json(self.cfg_path, None)
        if cfg is None:
            cfg = {"Version": 4, "ServerName": "Orbis Demo Realm", "MOTD": "Welcome to the demo!", "Password": "",
                   "MaxPlayers": 20, "MaxViewRadius": 12, "Defaults": {"World": "default", "GameMode": "Adventure"},
                   "RequireJoinPermission": False, "Update": {}, "Backup": {}}
            write_json(self.cfg_path, cfg)
        self.radius = int(cfg.get("MaxViewRadius") or 12)
        self.whitelist = bool(cfg.get("RequireJoinPermission"))   # the real server keeps this in memory
        for f, d in (("permissions.json", {"users": {}, "groups": {"Default": [], "OP": ["*"]}}),
                     ("bans.json", {"Version": 1, "Bans": []})):
            if not os.path.exists(os.path.join(root, f)):
                write_json(os.path.join(root, f), d)
        os.makedirs(os.path.join(root, "universe", "worlds", "default"), exist_ok=True)
        os.makedirs(os.path.join(root, "universe", "players"), exist_ok=True)
        wc = os.path.join(root, "universe", "worlds", "default", "config.json")
        if not os.path.exists(wc):
            write_json(wc, {"Seed": 1234567, "IsPvpEnabled": False, "GameTime": 0})
        region = os.path.join(root, "universe", "worlds", "default", "chunks.region.bin")
        if not os.path.exists(region):
            with open(region, "wb") as f:
                f.write(os.urandom(256 * 1024))

    @staticmethod
    def _xmx():
        for a in sys.argv:
            m = re.match(r"-Xmx(\d+)([MG])", a)
            if m:
                return int(m.group(1)) * (1024 if m.group(2) == "G" else 1)
        return 4096

    def emit(self, ev):
        with self.lock:
            self.seq += 1
            ev["seq"], ev["t"] = self.seq, time.time()
            self.events.append(ev)
            self.events = self.events[-300:]

    def _in_view(self):
        out = set()
        r = self.radius
        for cx in range(-3, 4):
            for cz in range(-3, 4):
                out.add((cx, cz))    # spawn stays loaded
        for p in self.players.values():
            pcx, pcz = int(math.floor(p.x / CHUNK)), int(math.floor(p.z / CHUNK))
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if dx * dx + dz * dz <= r * r:
                        out.add((pcx + dx, pcz + dz))
        return out

    def loaded_chunks(self):
        """Chunks in view plus the ones that have not been unloaded yet (the lingering-chunk
        problem real servers show when players fly through new terrain)."""
        with self.lock:
            return {k: (0 if k in self.generated else 1) for k in self.trail}

    def step(self, dt):
        with self.lock:
            for p in list(self.players.values()):
                speed = 4.3 if not self.stress else 22.0      # flying through new terrain
                if self.stress and p.target is None:
                    a = random.random() * math.tau
                    p.target = (math.cos(a) * random.uniform(1500, 3500), math.sin(a) * random.uniform(1500, 3500))
                if not self.stress:
                    p.target = None
                    p.heading += random.uniform(-0.5, 0.5) * dt
                    if math.hypot(p.x, p.z) > 260:
                        p.heading = math.atan2(-p.z, -p.x) + random.uniform(-0.6, 0.6)
                else:
                    p.heading = math.atan2(p.target[1] - p.z, p.target[0] - p.x)
                dx, dz = math.cos(p.heading) * speed * dt, math.sin(p.heading) * speed * dt
                p.x += dx
                p.z += dz
                p.stats["distance"] += math.hypot(dx, dz)
                p.ping = max(12, min(250, p.ping + random.randint(-4, 4) + (6 if self.stress and random.random() < .2 else 0)))
                if random.random() < 0.02 * dt:
                    p.stats["mob_kills"] += 1
                    victim = random.choice(["Trork", "Skeleton", "Kweebec Sapling", "Scarak", "Outlander"])
                    self.emit({"type": "kill", "player": p.name, "uuid": p.uuid, "victim": victim})
                if random.random() < 0.002 * dt:
                    p.stats["deaths"] += 1
                    cause = random.choice(["Fall", "Drowning", "Physical"])
                    killer = random.choice([None, "Trork Warrior", "Feran Hunter"]) if cause == "Physical" else None
                    self.emit({"type": "death", "player": p.name, "uuid": p.uuid, "cause": cause, "killer": killer,
                               "world": "default", "x": round(p.x), "z": round(p.z)})
                if random.random() < 0.05 * dt:
                    p.stats["blocks_broken"] += random.randint(1, 4)
                if random.random() < 0.04 * dt:
                    p.stats["blocks_placed"] += random.randint(1, 3)
            now = time.time()
            linger = 150 if self.stress else 6
            for k in self._in_view():
                self.trail[k] = now + linger
            for k in [k for k, t in self.trail.items() if t < now]:
                del self.trail[k]
            loaded = self.loaded_chunks()
            new = [k for k, v in loaded.items() if v == 1]
            for k in new[: (40 if self.stress else 200)]:
                self.generated.add(k)
            # Memory: base + 0.55 MB per loaded chunk; the generator adds churn.
            want = 650 + len(loaded) * 0.6 + len(new) * 0.4
            self.heap_live += (want - self.heap_live) * min(1.0, dt * 0.15)
            self.heap_live = min(self.heap_live, self.heap_max * 0.985)
            fill = self.heap_live / self.heap_max
            self.tps = max(2.0, 30.0 - max(0, fill - 0.75) * 120 - len(new) * 0.01)
            self.latency = int(2 + max(0, fill - 0.85) * 60000 * random.random())
            return loaded, new, fill


# ------------------------------------------------------------------------------------- GC log
class GcLog:
    def __init__(self, path, world):
        self.path, self.world, self.n = path, world, 0
        self.young = 0.0
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.f = open(path, "w", encoding="utf-8", buffering=1)
            self._w(f"Using G1")
        else:
            self.f = None

    def _w(self, text):
        if not self.f:
            return
        up = time.time() - START
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000%z")
        self.f.write(f"[{stamp}][{up:.3f}s][info][gc] {text}\n")

    def tick(self, dt, fill):
        if not self.f:
            return
        w = self.world
        alloc = 180 * dt * (2.5 if w.stress else 1)
        self.young += alloc
        cap = w.heap_max
        live = w.heap_live
        free = cap - live
        trigger = max(40.0, free * 0.6)
        if self.young >= trigger:
            before = live + self.young
            if fill > 0.93 and random.random() < 0.5:
                pause = random.uniform(900, 2600)
                self._w(f"GC({self.n}) Pause Full (G1 Compaction Pause) {int(before)}M->{int(live)}M({cap}M) {pause:.3f}ms")
            else:
                pause = random.uniform(4, 25) + max(0, fill - 0.8) * 900
                self._w(f"GC({self.n}) Pause Young (Normal) (G1 Evacuation Pause) {int(before)}M->{int(live)}M({cap}M) {pause:.3f}ms")
            self.n += 1
            self.young = 0.0


# ------------------------------------------------------------------------------------- PNG tiles
def png(w, h, rgb_rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rgb_rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def png_rgba(w, h, rgba_rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rgba_rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class MapTiles:
    """Plays the plugin's map capture: region tiles of explored chunks in ORBISFOX_MAP_DIR."""
    REGION, PX = 16, 4          # demo tiles are small (4 px per chunk) to keep the mock light

    def __init__(self, world, out_dir):
        self.world, self.dir = world, out_dir
        self.done = set()
        self.last = 0

    def tick(self):
        if not self.dir or time.time() - self.last < 20:
            return
        self.last = time.time()
        with self.world.lock:
            explored = set(self.world.generated) | set(self.world.trail)
        new = explored - self.done
        regions = sorted({(cx // self.REGION, cz // self.REGION) for cx, cz in new},
                         key=lambda r: abs(r[0]) + abs(r[1]))[:8]
        if not regions:
            return
        self.done |= {c for c in new if (c[0] // self.REGION, c[1] // self.REGION) in regions}
        base = os.path.join(self.dir, "default")
        size = self.REGION * self.PX
        for rx, rz in regions:
            rows = []
            for py in range(size):
                cz = rz * self.REGION + py // self.PX
                row = bytearray()
                for px in range(size):
                    cx = rx * self.REGION + px // self.PX
                    if (cx, cz) in self.done:
                        r, g, b = color(cx * CHUNK + (px % self.PX) * 8 + 4, cz * CHUNK + (py % self.PX) * 8 + 4)
                        row += bytes((r, g, b, 255))
                    else:
                        row += b"\x00\x00\x00\x00"
                rows.append(row)
            data = png_rgba(size, size, rows)
            for level in ("1", "4", "16"):
                os.makedirs(os.path.join(base, level), exist_ok=True)
                with open(os.path.join(base, level, f"{rx}_{rz}.png"), "wb") as f:
                    f.write(data)
        files = os.listdir(os.path.join(base, "1"))
        regs = []
        for n in files:
            if n.endswith(".png"):
                x, z = n[:-4].split("_")
                regs.append([int(x), int(z), int(os.path.getmtime(os.path.join(base, "1", n)))])
        write_json(os.path.join(base, "index.json"), {"world": "default", "region_chunks": self.REGION,
                                                      "chunk_blocks": CHUNK, "levels": [1, 4, 16],
                                                      "updated": int(time.time()), "regions": regs})


def _hash(x, z, s=0):
    n = (x * 374761393 + z * 668265263 + s * 982451653) & 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


def _noise(x, z, scale, s=0):
    fx, fz = x / scale, z / scale
    ix, iz = math.floor(fx), math.floor(fz)
    tx, tz = fx - ix, fz - iz
    tx, tz = tx * tx * (3 - 2 * tx), tz * tz * (3 - 2 * tz)
    a, b = _hash(ix, iz, s), _hash(ix + 1, iz, s)
    c, d = _hash(ix, iz + 1, s), _hash(ix + 1, iz + 1, s)
    return (a + (b - a) * tx) + ((c + (d - c) * tx) - (a + (b - a) * tx)) * tz


def height(x, z):
    return (_noise(x, z, 400, 1) * 0.55 + _noise(x, z, 120, 2) * 0.3 + _noise(x, z, 30, 3) * 0.15)


def color(x, z):
    hgt = height(x, z)
    temp = _noise(x, z, 900, 7) - (0.25 if math.hypot(x, z) > 1800 and z < 0 else 0)
    moist = _noise(x, z, 600, 9)
    if hgt < 0.38:
        d = (0.38 - hgt) / 0.38
        return (int(40 - 20 * d), int(96 - 40 * d), int(140 - 30 * d))       # water
    if hgt < 0.405:
        return (196, 182, 132)                                                  # beach
    if temp < 0.3:
        base = (226, 232, 236)                                                  # snow
    elif temp > 0.7 and moist < 0.45:
        base = (204, 170, 110)                                                  # desert
    elif moist > 0.62:
        base = (54, 104, 52)                                                    # forest
    else:
        base = (98, 142, 66)                                                    # grassland
    if hgt > 0.72:
        base = (128, 124, 118) if hgt < 0.8 else (200, 200, 205)                # rock / peaks
    shade = (height(x + 2, z + 2) - hgt) * -900
    shade = max(-38, min(38, shade))
    return tuple(max(0, min(255, int(c + shade))) for c in base)


TILE = 32


_TILES = {}


def tile_png(cx, cz):
    if (cx, cz) in _TILES:
        return _TILES[(cx, cz)]
    rows = []
    for pz in range(TILE):
        row = []
        for px in range(TILE):
            row += color(cx * CHUNK + px, cz * CHUNK + pz)
        rows.append(row)
    data = png(TILE, TILE, rows)
    if len(_TILES) < 20000:
        _TILES[(cx, cz)] = data
    return data


# ------------------------------------------------------------------------------------- bridge API
def make_bridge(world, port, token):
    run = str(uuidlib.uuid4())

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("X-OrbisFox-Token") != token:
                self.send_response(401)
                self.end_headers()
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/status":
                since = int((q.get("since") or ["0"])[0])
                with world.lock:
                    loaded = world.loaded_chunks()
                    body = {"version": "1.0.0-mock", "run": run, "t": time.time(),
                            "worlds": [{"name": "default", "tps": round(world.tps, 1), "target_tps": 30,
                                        "tick_ms": round(1000 / max(world.tps, 1) * 0.9, 1),
                                        "players": len(world.players), "entities": 180 + len(loaded) // 3,
                                        "chunks": len(loaded), "latency_ms": world.latency}],
                            "players": [{"uuid": p.uuid, "name": p.name, "world": "default", "x": round(p.x, 1),
                                         "y": p.y, "z": round(p.z, 1), "yaw": math.degrees(p.heading),
                                         "ping_ms": p.ping} for p in world.players.values()],
                            "stats": {p.uuid: dict(p.stats) for p in world.players.values()},
                            "events": [e for e in world.events if e["seq"] > since],
                            "heap": {"used": int(world.heap_live * 1024 * 1024),
                                     "max": int(world.heap_max * 1024 * 1024)}}
                return self._json(body)
            if u.path == "/chunks":
                with world.lock:
                    loaded = world.loaded_chunks()
                    body = {"world": "default", "chunk_size": CHUNK,
                            "chunks": [[x, z, s] for (x, z), s in loaded.items()],
                            "players": [{"name": p.name, "uuid": p.uuid, "x": p.x, "z": p.z,
                                         "yaw": math.degrees(p.heading)} for p in world.players.values()],
                            "spawn": {"x": 0, "z": 0}, "radius": world.radius}
                return self._json(body)
            if u.path == "/tile":
                x, z = int(q["x"][0]), int(q["z"][0])
                data = tile_png(x, z)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.headers.get("X-OrbisFox-Token") != token:
                self.send_response(401)
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if urlparse(self.path).path == "/notify":
                text = json.loads(body or b"{}").get("text", "")
                log("INFO", "OrbisFoxBridge", f"To operators: {text}")
                return self._json({"sent": 1})
            self.send_response(404)
            self.end_headers()

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------------------------- main
def bridge_on_early(root):
    return os.path.exists(os.path.join(root, "mods", "OrbisFoxBridge.jar"))


def main():
    root = os.getcwd()
    world = World(root)
    gc_path = None
    for a in sys.argv:
        m = re.search(r"-Xlog:gc:file=([^:]+)", a)
        if m:
            gc_path = m.group(1).strip('"')
    gclog = GcLog(gc_path, world)
    tiles = MapTiles(world, os.environ.get("ORBISFOX_MAP_DIR")) if bridge_on_early(root) else None
    res = os.path.join(root, "universe", "resources")
    os.makedirs(res, exist_ok=True)
    if not os.path.exists(os.path.join(res, "Memories.json")):
        write_json(os.path.join(res, "Memories.json"), {"Memories": [
            {"Id": "NPC", "NPCRole": c, "TranslationKey": f"server.npcRoles.{c}.name",
             "CapturedTimestamp": int(time.time() * 1000) - 86400000} for c in sorted(RESTORED)]})
    bridge_on = os.path.exists(os.path.join(root, "mods", "OrbisFoxBridge.jar"))
    log("INFO", "HytaleServer", "Starting HytaleServer")
    log("INFO", "HytaleServer", f"Version {VERSION} (mock), Java 25, max heap {world.heap_max} MB")
    log("WARN", "org.jline", "Unable to create a system terminal, creating a dumb terminal")
    time.sleep(0.6)
    log("INFO", "PluginManager", "Loading plugins from mods/")
    if bridge_on:
        log("INFO", "OrbisFoxBridge", "OrbisFox Bridge 1.0.0 (mock) listening on 127.0.0.1")
    time.sleep(0.6)
    log("INFO", "Universe", "Loading world 'default'")
    log("INFO", "World|default", "Generating spawn area")
    time.sleep(0.8)
    log("INFO", "ServerAuth", "Authentication successful! Mode: OAUTH_DEVICE")
    log("INFO", "HytaleServer", "Hytale Server Booted! Listening on 0.0.0.0:5520 (QUIC)")
    if bridge_on and os.environ.get("ORBISFOX_BRIDGE_PORT"):
        make_bridge(world, int(os.environ["ORBISFOX_BRIDGE_PORT"]), os.environ.get("ORBISFOX_BRIDGE_TOKEN", ""))

    stop = threading.Event()
    exit_code = [0]

    def sim():
        last = time.time()
        next_join = time.time() + 3
        last_warn = 0
        while not stop.is_set():
            time.sleep(0.5)
            now = time.time()
            dt, last = now - last, now
            if world.pending_join and now >= next_join:
                name = world.pending_join.pop()
                p = Player(name)
                with world.lock:
                    world.players[p.uuid] = p
                carried = random.sample([c for c in DEMO_IDS if c not in RESTORED], k=random.randint(0, 3))
                write_json(os.path.join(root, "universe", "players", p.uuid + ".json"),
                           {"Components": {"Nameplate": {"Text": name},
                                           "Transform": {"Position": {"X": p.x, "Y": p.y, "Z": p.z}},
                                           "Player": {"GameMode": "Adventure", "PlayerData": {"World": "default"}},
                                           "PlayerMemories": {"Capacity": 48, "Memories": [
                                               {"Id": "NPC", "NPCRole": c, "TranslationKey": f"server.npcRoles.{c}.name",
                                                "CapturedTimestamp": int(time.time() * 1000) - random.randint(0, 9) * 3600000,
                                                "FoundLocationNameKey": random.choice(["server.map.region.Zone1_Tier1",
                                                                                       "server.map.region.Zone1_Tier2"])}
                                               for c in carried]}}})
                log("INFO", "Universe|P", f"Adding player '{name} ({p.uuid})")
                log("INFO", "World|default", f"Adding player '{name}' to world 'default' at location null ({p.uuid})")
                next_join = now + random.uniform(6, 20)
            loaded, new, fill = world.step(dt)
            gclog.tick(dt, fill)
            if tiles:
                tiles.tick()
            if world.stress and fill > 0.8 and now - last_warn > (2 if fill > 0.92 else 6):
                last_warn = now
                ms = int(200 + (fill - 0.8) * 20000 * random.random())
                sec, ms = divmod(ms, 1000)
                log("SEVERE", "World|default", f"Took too long to run pre-load process hook for chunk: "
                    f"{sec}sec {ms}ms {random.randint(1, 999)}us {random.randint(1, 999)}ns > TICK_STEP")
            if random.random() < 0.01:
                with world.lock:
                    ps = list(world.players.values())
                if ps:
                    p = random.choice(ps)
                    msg = random.choice(["anyone seen a Kweebec village?", "heading to the desert", "gg",
                                         "need iron!", "found a Trork camp at the river"])
                    p.stats["chat_messages"] += 1
                    world.emit({"type": "chat", "player": p.name, "message": msg})

    threading.Thread(target=sim, daemon=True).start()

    def remove_player(p, reason=""):
        with world.lock:
            world.players.pop(p.uuid, None)
        # The real server prints the world's line first, then the universe's.
        log("INFO", "PlayerSystems", f"Removing player '{p.name} ({p.name})' from world 'default' ({p.uuid})")
        log("INFO", "Universe|P", f"Removing player '{p.name}' ({p.uuid})")

    def find(name):
        with world.lock:
            return next((p for p in world.players.values() if p.name.lower() == name.lower()), None)

    def perms_edit(fn):
        path = os.path.join(root, "permissions.json")
        d = read_json(path, {"users": {}, "groups": {}})
        fn(d)
        write_json(path, d)

    def uuid_for(name):
        p = find(name)
        return p.uuid if p else str(uuidlib.uuid5(uuidlib.NAMESPACE_DNS, "orbisfox-" + name))

    for raw in sys.stdin:
        line = raw.strip().replace("\\!", "!").replace("\\\\", "\\")
        if not line:
            continue
        if line.startswith("/"):
            line = line[1:]
        log("INFO", "CommandManager", f"Console executed command: {line}")
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("stop", "shutdown"):
                log("INFO", "HytaleServer", "Shutting down...")
                with world.lock:
                    for p in list(world.players.values()):
                        remove_player(p, "server shutting down")
                log("INFO", "Universe", "Saving worlds")
                time.sleep(1.0)
                log("INFO", "HytaleServer", "Server stopped.")
                break
            elif cmd == "who":
                with world.lock:
                    names = ", ".join(p.name for p in world.players.values()) or "nobody"
                log("INFO", "WhoCommand", f"default ({len(world.players)}): {names}")
            elif cmd in ("say", "broadcast"):
                log("INFO", "Chat", f"[Server] {' '.join(args)}")
            elif cmd == "notify":
                # Like the real server: a first word naming a style is the style, not the message.
                words = list(args)
                style = "default"
                if len(words) > 1 and words[0].lower() in ("default", "warning", "danger", "success"):
                    style = words.pop(0).lower()
                log("INFO", "Notify", f"({style}) {' '.join(words)}")
            elif cmd == "eventtitle":
                log("INFO", "EventTitle", " ".join(args))
            elif cmd == "kick" and args:
                p = find(args[0])
                if p:
                    remove_player(p, "kicked")
                    world.pending_join.insert(0, p.name)
                else:
                    log("WARN", "CommandManager", f"Player not found: {args[0]}")
            elif cmd == "ban" and args:
                u = uuid_for(args[0])
                path = os.path.join(root, "bans.json")
                d = read_json(path, {"Version": 1, "Bans": []})
                d["Bans"].append({"Target": u, "By": "00000000-0000-0000-0000-000000000000",
                                  "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  "Reason": " ".join(args[1:]) or "No reason."})
                write_json(path, d)
                p = find(args[0])
                if p:
                    remove_player(p, "banned")
                log("INFO", "BanCommand", f"Banned {args[0]}")
            elif cmd == "unban" and args:
                u = uuid_for(args[0])
                path = os.path.join(root, "bans.json")
                d = read_json(path, {"Version": 1, "Bans": []})
                d["Bans"] = [b for b in d["Bans"] if b.get("Target") != u]
                write_json(path, d)
                log("INFO", "BanCommand", f"Unbanned {args[0]}")
            elif cmd == "op" and len(args) >= 2:
                # Like the real server: a UUID, or the name of a player who is online right now.
                target = args[1]
                if re.match(r"^[0-9a-fA-F-]{36}$", target):
                    u = target.lower()
                elif find(target):
                    u = uuid_for(target)
                else:
                    log("INFO", "CommandManager", f'"{target}" is not a valid UUID or online player username')
                    continue

                def f(d, add=args[0] == "add"):
                    user = d.setdefault("users", {}).setdefault(u, {"groups": []})
                    groups = set(user.get("groups") or [])
                    groups.add("OP") if add else groups.discard("OP")
                    user["groups"] = sorted(groups)
                perms_edit(f)
                log("INFO", "CommandManager", f"{target} is now an operator!" if args[0] == "add" else f"{target} is no longer an operator.")
            elif cmd == "whitelist" and args:
                sub = args[0]
                if sub in ("enable", "on", "disable", "off"):
                    on = sub in ("enable", "on")
                    if on == world.whitelist:
                        log("INFO", "CommandManager", f"Whitelist is already {'enabled' if on else 'disabled'}!")
                    else:
                        world.whitelist = on
                        cfg = read_json(world.cfg_path, {})
                        cfg["RequireJoinPermission"] = on
                        write_json(world.cfg_path, cfg)
                        log("INFO", "CommandManager", f"Whitelist {'enabled' if on else 'disabled'}!")
                elif sub == "status":
                    log("INFO", "CommandManager", f"Whitelist is currently {'enabled' if world.whitelist else 'disabled'}!")
                elif sub in ("add", "remove") and len(args) >= 2:
                    u = uuid_for(args[1])

                    def f(d, add=sub == "add"):
                        user = d.setdefault("users", {}).setdefault(u, {"permissions": [], "groups": []})
                        perms = set(user.get("permissions") or [])
                        perms.add("hytale.server.join") if add else perms.discard("hytale.server.join")
                        user["permissions"] = sorted(perms)
                    perms_edit(f)
                    log("INFO", "WhitelistCommand", f"{'Added' if sub == 'add' else 'Removed'} {args[1]}")
                else:
                    log("INFO", "WhitelistCommand", "Whitelist: see permissions.json")
            elif cmd == "time":
                if args and args[0] in ("pause", "stop"):
                    world.paused = not world.paused
                    log("INFO", "TimeCommand", f"Time {'paused' if world.paused else 'resumed'}")
                elif len(args) >= 2 and args[0] == "set":
                    world.time = args[1]
                    log("INFO", "TimeCommand", f"Time set to {args[1]}")
            elif cmd == "weather":
                log("INFO", "WeatherCommand", f"Weather: {' '.join(args)}")
            elif cmd == "maxviewradius" and args:
                world.radius = 12 if args[0] == "reset" else max(1, min(32, int(args[0])))
                cfg = read_json(world.cfg_path, {})
                cfg["MaxViewRadius"] = world.radius
                write_json(world.cfg_path, cfg)
                log("INFO", "MaxViewRadiusCommand", f"Max view radius set to {world.radius} chunks")
            elif cmd == "maxplayers":
                n = next((a for a in args if a.isdigit()), None)
                if n:
                    cfg = read_json(world.cfg_path, {})
                    cfg["MaxPlayers"] = int(n)
                    write_json(world.cfg_path, cfg)
                log("INFO", "MaxPlayersCommand", f"Max players: {n or read_json(world.cfg_path, {}).get('MaxPlayers')}")
            elif cmd == "world" and args and args[0] == "save":
                log("INFO", "Universe", "Saving all worlds...")
                time.sleep(0.4)
                # A real save sometimes makes one chunk miss its tick while Java frees memory.
                comps = ", ".join("null" if i % 3 else f"ComponentType{{registry=class com.hypixel.hytale.component.ComponentRegistry@2139788441, index={i}}}" for i in range(26))
                log("SEVERE", "World|default", "Took too long to run pre-load process hook for section: 63ms 247us 900ns > "
                    f"TICK_STEP, Has GC Run: true, Holder{{archetype=Archetype{{componentTypes=[{comps}]}}}}, Hook: "
                    "com.hypixel.hytale.builtin.adventure.farming.FarmingPlugin$$Lambda/0x00000000b8fee5f8@63ff69e0")
                log("INFO", "Universe", "Saved 1 world.")
            elif cmd == "world" and args and args[0] == "perf":
                log("INFO", "WorldPerf", f"default: {world.tps:.1f} TPS")
            elif cmd == "chunk" and args and args[0] == "unload":
                with world.lock:
                    view = world._in_view()
                    gone = [k for k in world.trail if k not in view]
                    for k in gone:
                        del world.trail[k]
                log("INFO", "ChunkCommand", f"Unloaded {len(gone)} chunks nobody needs ({len(view)} remain)")
            elif cmd == "version":
                log("INFO", "VersionCommand", f"HytaleServer {VERSION} (mock)")
            elif cmd == "whereami" and args:
                p = find(args[0])
                if p:
                    log("INFO", "WhereAmI", f"{p.name} is in default at {p.x:.1f} {p.y:.1f} {p.z:.1f}")
            elif cmd == "backup":
                bdir = os.path.join(root, "backups")
                os.makedirs(bdir, exist_ok=True)
                name = time.strftime("%Y-%m-%d_%H-%M-%S") + ".zip"
                with open(os.path.join(bdir, name), "wb") as f:
                    f.write(os.urandom(64 * 1024))
                log("INFO", "Backup", f"Backup created: {name}")
            elif cmd == "update":
                gd = os.path.dirname(root)
                if args and args[0] == "download":
                    st = os.path.join(gd, "updater", "staging", "Server")
                    os.makedirs(st, exist_ok=True)
                    import zipfile
                    with zipfile.ZipFile(os.path.join(st, "HytaleServer.jar"), "w") as z:
                        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nImplementation-Version: 0.7.7\n")
                    log("INFO", "Updater", "Update 0.7.7 downloaded to staging")
                elif args and args[0] == "apply":
                    log("INFO", "Updater", "Applying update: shutting down with exit code 8")
                    exit_code[0] = 8
                    break
                else:
                    log("INFO", "Updater", "Update 0.7.7 available")
            elif cmd == "mockveto":
                # Pretend an operator typed /veto in game (what the OrbisFox Bridge plugin reports).
                who = args[0] if args else "Feran_Fox"
                log("INFO", "CommandManager", f"{who} executed command: veto")
                world.emit({"type": "veto", "player": who})
            elif cmd == "stress":
                world.stress = True
                log("WARN", "OrbisFoxDemo", "Players scatter in every direction, loading terrain everywhere...")
            elif cmd == "calm":
                world.stress = False
                log("INFO", "OrbisFoxDemo", "Players regroup near spawn.")
            elif cmd == "server" and args[:1] == ["stats"]:
                log("INFO", "ServerStats", f"Heap Memory Usage: Used: {world.heap_live / 1024:.1f} GiB of "
                    f"{world.heap_max / 1024:.1f} GiB")
            elif cmd in ("tp", "gamemode", "gm", "give", "kill", "auth", "perm", "help", "plugin", "player"):
                log("INFO", cmd.capitalize() + "Command", "Done.")
            else:
                log("WARN", "CommandManager", f"Unknown command: {cmd}")
        except Exception as e:  # noqa: BLE001
            log("SEVERE", "CommandManager", f"Command failed: {e}")
    stop.set()
    sys.exit(exit_code[0])


if __name__ == "__main__":
    main()
