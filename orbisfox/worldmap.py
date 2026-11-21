"""The explored-world map.

The OrbisFox Bridge plugin copies map images the server has already drawn for players and saves
them as region tiles in OrbisFox's map folder (data/map by default). OrbisFox adds a small viewer
page and a few tiny JSON files, so the folder is a complete static website:

    index.html, livemap.js, livemap.css, fonts/, img/    the viewer (all drawing happens in the browser)
    worlds.json                                         which worlds have tiles
    <world>/index.json, <world>/<1|4|16>/<rx>_<rz>.png    tiles at three zoom levels (from the plugin)
    players.json, status.json                           optional live extras, rewritten every 10 s

It is shown inside the panel, can be shared read-only on your network (for OBS or another PC) on its
own port, and can be uploaded as-is to any static web host. None of this puts work on the server:
the plugin only copies images that exist anyway, and serving files is trivial.
"""
import json
import os
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .settings import APP_DIR

VIEWER_SRC = os.path.join(APP_DIR, "web", "livemap")
VIEWER_FILES = ["index.html", "livemap.js", "livemap.css"]
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2", ".ico": "image/x-icon"}


def lan_addresses():
    """This PC's likely LAN address(es), for showing the share link."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # no packet is sent; picks the outgoing interface
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return [a for a in out if not a.startswith("127.")]


class WorldMap:
    def __init__(self, core):
        self.core = core
        self.httpd = None
        self._last_players = None
        self._last_worlds = None
        self._started = False

    # ---------------------------------------------------------------------------- folder
    @property
    def dir(self):
        d = self.core.settings.get("map", {}).get("dir") or os.path.join(self.core.data_dir, "map")
        os.makedirs(d, exist_ok=True)
        return d

    def publish_viewer(self):
        """Copies the viewer into the map folder (so the folder alone is a working website)."""
        d = self.dir
        pairs = [(os.path.join(VIEWER_SRC, f), os.path.join(d, f)) for f in VIEWER_FILES]
        pairs += [(os.path.join(APP_DIR, "web", "fonts", f), os.path.join(d, "fonts", f))
                  for f in ("Genos-400.woff2", "Genos-600.woff2", "Genos-700.woff2", "OFL.txt")]
        pairs += [(os.path.join(APP_DIR, "web", "img", "mark-64.png"), os.path.join(d, "img", "mark-64.png"))]
        for src, dst in pairs:
            try:
                if not os.path.isfile(src):
                    continue
                if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src) and \
                        os.path.getmtime(dst) >= os.path.getmtime(src):
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            except OSError:
                pass

    def worlds(self):
        out = []
        d = self.dir
        try:
            for name in sorted(os.listdir(d)):
                idx = os.path.join(d, name, "index.json")
                if os.path.isfile(idx):
                    try:
                        with open(idx, encoding="utf-8") as f:
                            data = json.load(f)
                        out.append({"name": name, "regions": len(data.get("regions") or []),
                                    "updated": data.get("updated")})
                    except (OSError, ValueError):
                        continue
        except OSError:
            pass
        return out

    def _write(self, name, data):
        path = os.path.join(self.dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)

    def tick(self):
        """Every 10 s: keep worlds.json, players.json and status.json current (only when changed)."""
        if not self._started:
            self._started = True
            self.publish_viewer()
        worlds = self.worlds()
        if worlds != self._last_worlds:
            self._last_worlds = worlds
            self._write("worlds.json", {"worlds": worlds, "app": "OrbisFox"})
        mp = self.core.settings.get("map", {})
        players = []
        if mp.get("show_players", True):
            for p in self.core.players.online_list():
                if p.get("x") is not None:
                    players.append({"name": p["name"], "world": p.get("world"), "x": round(p["x"]), "z": round(p["z"])})
        if players != self._last_players:
            self._last_players = players
            self._write("players.json", {"players": players, "t": time.time()})
        sv = self.core.server
        status = {"online": sv.running, "players": len(self.core.players.online)}
        if status != getattr(self, "_last_status", None) or time.time() - getattr(self, "_status_at", 0) > 60:
            self._last_status, self._status_at = status, time.time()
            self._write("status.json", dict(status, t=round(time.time())))

    def info(self):
        mp = self.core.settings.get("map", {})
        port = int(mp.get("port", 8767))
        urls = [f"http://{a}:{port}/" for a in lan_addresses()] if self.httpd else []
        key = mp.get("key") or ""
        if key:
            urls = [u + f"?key={key}" for u in urls]
        worlds = self.worlds()
        return {"dir": self.dir, "worlds": worlds, "regions": sum(w["regions"] for w in worlds),
                "public": bool(self.httpd), "port": port, "urls": urls, "show_players": mp.get("show_players", True),
                "backfill": mp.get("backfill", False), "bridge": self.core.bridge.connected}

    # ---------------------------------------------------------------------------- sharing
    def start_share(self):
        mp = self.core.settings.get("map", {})
        port = int(mp.get("port", 8767))
        if self.httpd and mp.get("public") and self.httpd.server_address[1] == port:
            return port     # already sharing on this port (the key is checked per request)
        self.stop_share()
        if not mp.get("public"):
            return None
        handler = _make_handler(self)
        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
        except OSError as e:
            self.core.log_app(f"Could not share the map on port {port}: {e}", level="warn")
            self.httpd = None
            return None
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="map-share", daemon=True).start()
        self.core.log_app(f"Sharing the map read-only on port {port}.")
        return port

    def stop_share(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def serve_file(self, rel):
        """(bytes, content type) for a path inside the map folder, or None."""
        rel = rel.lstrip("/") or "index.html"
        base = os.path.realpath(self.dir)
        full = os.path.realpath(os.path.join(base, rel))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return None
        if os.path.basename(full).endswith((".tmp", ".bin")):
            return None
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            return f.read(), TYPES.get(ext, "application/octet-stream")


def _make_handler(wm):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            key = wm.core.settings.get("map", {}).get("key") or ""
            if key and (parse_qs(u.query).get("key") or [""])[0] != key:
                return self._send(403, b"This map needs its key.", "text/plain")
            got = wm.serve_file(u.path)
            if not got:
                return self._send(404, b"Not found", "text/plain")
            body, ctype = got
            cache = "no-cache" if ctype.startswith(("application/json", "text/html")) else "max-age=60"
            self._send(200, body, ctype, cache)

        def _send(self, status, body, ctype, cache="no-cache"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return H
