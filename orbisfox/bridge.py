"""Talks to the OrbisFox Bridge plugin running inside the Hytale server.

The plugin listens on 127.0.0.1 only and checks a token that OrbisFox hands to the server process
through environment variables (ORBISFOX_BRIDGE_PORT / ORBISFOX_BRIDGE_TOKEN). It reports players
with position and ping, per-world tick rate and loaded chunks, how quickly each world thread
answers, death/kill events, and map images of loaded chunks.
"""
import json
import os
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.request

PLUGIN_FILE = "OrbisFoxBridge.jar"


class Bridge:
    def __init__(self, core):
        self.core = core
        self.token = secrets.token_urlsafe(24)
        self.connected = False
        self.version = None
        self.load_error = None    # the server's own complaint about loading the plugin, if any
        self.last = None          # last /status payload
        self.last_ok = None
        self.events_since = 0
        self._stop = threading.Event()
        threading.Thread(target=self._loop, name="bridge", daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{int(self.core.settings['plugin_port'])}"

    def _post(self, path, body, timeout=3):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"X-OrbisFox-Token": self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def notify_ops(self, text):
        """Private message to every online operator (players allowed to /veto). Returns how many got it."""
        if not self.connected:
            return 0
        try:
            return int(self._post("/notify", {"to": "ops", "text": text}).get("sent", 0))
        except (OSError, ValueError, urllib.error.URLError):
            return 0

    def _get(self, path, timeout=3, raw=False):
        req = urllib.request.Request(self.base + path, headers={"X-OrbisFox-Token": self.token})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return data if raw else json.loads(data)

    def _loop(self):
        while not self._stop.is_set():
            sv = self.core.server
            if sv.alive and sv.state in ("running", "stopping"):
                try:
                    st = self._get(f"/status?since={self.events_since}")
                    if not self.connected:
                        self.core.log_app(f"OrbisFox Bridge plugin connected (v{st.get('version')}).")
                    self.connected, self.last, self.last_ok = True, st, time.time()
                    self.version = st.get("version")
                    evs = st.get("events") or []
                    if evs:
                        self.events_since = max(e.get("seq", 0) for e in evs)
                    self.core.on_bridge_status(st)
                except (OSError, ValueError, urllib.error.URLError):
                    if self.connected and self.last_ok and time.time() - self.last_ok > 10:
                        self.connected = False
                        self.core.publish_server()
            else:
                if self.connected:
                    self.connected = False
                self.events_since = 0
            self._stop.wait(2.0)

    def on_server_start(self):
        self.connected, self.last, self.last_ok, self.events_since = False, None, None, 0
        self.load_error = None

    def check_line(self, level, message):
        """Notices the server refusing or failing to load the plugin."""
        if "OrbisFoxBridge" not in message and "orbisfox" not in message.lower():
            return False
        low = message.lower()
        if level in ("SEVERE", "ERROR") or "failed to load" in low or "could not register" in low:
            self.load_error = message.strip()[:300]
            return True
        return False

    # ---------------------------------------------------------------------------- map
    def chunks(self, world):
        if not self.connected:
            raise RuntimeError("The OrbisFox Bridge plugin is not connected.")
        from urllib.parse import quote
        return self._get(f"/chunks?world={quote(world or '')}", timeout=8)

    # ---------------------------------------------------------------------------- install
    @staticmethod
    def bundled_jar():
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(here, "plugin", "dist", PLUGIN_FILE)
        return p if os.path.isfile(p) else None

    def installed(self):
        sd = self.core.server_dir()
        if not sd:
            return False
        mods = os.path.join(sd, "mods")
        try:
            return any(n.lower().startswith("orbisfoxbridge") and n.lower().endswith(".jar")
                       for n in os.listdir(mods))
        except OSError:
            return False

    def install(self):
        src = self.bundled_jar()
        if not src:
            raise RuntimeError("The plugin has not been built yet (plugin/dist/OrbisFoxBridge.jar is missing). "
                               "See plugin/README.md.")
        sd = self.core.server_dir()
        if not sd:
            raise RuntimeError("Set the server folder first.")
        mods = os.path.join(sd, "mods")
        os.makedirs(mods, exist_ok=True)
        for n in os.listdir(mods):
            if n.lower().startswith("orbisfoxbridge") and n.lower().endswith(".jar"):
                os.remove(os.path.join(mods, n))
        shutil.copy2(src, os.path.join(mods, PLUGIN_FILE))
        return True

    def info(self):
        st = self.last or {}
        return {"connected": self.connected, "version": self.version, "installed": self.installed(),
                "load_error": self.load_error,
                "bundled": bool(self.bundled_jar()), "worlds": st.get("worlds") or [],
                "heap": st.get("heap")}
