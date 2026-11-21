"""OrbisFox's own settings (not the Hytale server's config.json), kept in data/settings.json."""
import copy
import json
import os
import threading

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("ORBISFOX_DATA") or os.path.join(APP_DIR, "data")

DEFAULTS = {
    # --- the Hytale server -------------------------------------------------------------------
    "server_dir": "",          # folder holding HytaleServer.jar (the "Server" folder of the download)
    "java": "",                # blank = find Java 25 automatically
    "jar": "HytaleServer.jar",
    "assets": "",              # blank = Assets.zip next to the jar or one folder up
    "bind": "0.0.0.0:5520",
    "ram_min_gb": 2,
    "ram_max_gb": 6,
    "aot_cache": True,         # -XX:AOTCache=HytaleServer.aot when the file exists (faster boot)
    "gc_log": True,            # lets OrbisFox watch memory pressure (see docs/chunk-freeze.md)
    "exit_on_oom": True,       # a server that runs out of memory exits (and is restarted) instead of hanging
    "jvm_args": "",
    "server_args": "",
    "auto_start": False,       # start the server when OrbisFox opens
    "auto_restart": True,      # restart after a crash
    "stop_timeout_s": 90,
    # --- backups -----------------------------------------------------------------------------
    "backups": {
        "dir": "",             # blank = <server_dir>/orbisfox-backups
        "keep": 20,
        "every_minutes": 0,    # 0 = only on demand / before updates
        "before_update": True,
        "include_mods": True,
        "hytale_builtin": True,    # also let the server make its own backups (--backup)
        "hytale_dir": "backups",   # relative to the Server folder, like the official scripts
        "hytale_frequency": 30,    # minutes
    },
    # --- scheduled restarts ------------------------------------------------------------------
    "restarts": {
        "daily": [],           # e.g. ["05:00"]
        "warn_minutes": [10, 5, 1],
        "message": "Server restarting in {time}.",
    },
    # --- watchdog (the "too many chunks" freeze) -----------------------------------------------
    "watchdog": {
        "enabled": True,
        "heap_percent": 92,            # live heap after a full GC above this ...
        "gc_overhead_percent": 40,     # ... or this share of wall time spent paused in GC
        "window_s": 120,               # ... sustained for this long
        "unresponsive_s": 90,          # console stops answering for this long
        "action": "restart",           # "restart" | "warn"
        "warn_players": True,
        "warn_seconds": 60,            # countdown before a recovery restart (operators can /veto)
        "adaptive_view": True,         # lower the view radius live while memory is tight
        "try_chunk_unload": True,      # send `chunk unload` once before a recovery restart
        "baseline_radius": None,       # the configured radius while OrbisFox has it lowered
    },
    # --- updates -----------------------------------------------------------------------------
    "updates": {
        "downloader": "",      # path to hytale-downloader(.exe); blank = look in server_dir/..
        "patchline": "release",
        "check_every_hours": 6,
        "auto_install": "off",  # "off" | "empty" (when nobody is online) | "always" (warn, operators can veto)
        "manage_server_updates": True,   # switch off the server's own AutoApplyMode; OrbisFox does it
    },
    # --- in-game operators can cancel automated restarts and updates -------------------------------
    "veto": {
        "enabled": True,
        "cooldown_minutes": 5,     # after a veto, that kind of action waits this long before trying again
        "command": "veto",
    },
    # --- the OrbisFox panel itself ------------------------------------------------------------
    "panel": {
        "port": 8765,
        "lan": False,          # allow other devices to open the panel (needs a password)
        "password_hash": "",
        "salt": "",
    },
    "start_with_windows": False,
    "window": "app",           # "app" (chromeless window), "browser" (normal tab) or "none"
    "track_gpu": False,        # GPU counters cost a little CPU; the server itself doesn't render
    # --- explored-world map (tiles come from the OrbisFox Bridge plugin) -------------------------------
    "map": {
        "dir": "",                 # blank = <OrbisFox data>/map
        "public": False,           # share it read-only on your network (for OBS, another PC, a phone)
        "port": 8767,
        "key": "",                 # optional: viewers need ?key=... in the link
        "show_players": True,      # player markers on the map
        "show_memories": True,     # the Memories page next to the map
        "show_memory_carriers": True,   # who is carrying which memory
        "backfill": False,         # fill in old areas slowly while nobody is online (the server loads them)
    },
    # --- OrbisFox updating itself from GitHub ------------------------------------------------------
    "app_updates": {
        "check_every_hours": 6,
        "auto": "off",             # "off" (ask in the panel) or "empty" (install when nobody is online)
        "repo": "AlphaFoxtrot0/orbisfox",
    },
    "plugin_port": 8766,       # where the OrbisFox companion plugin reports (localhost only)
    "player_names": {},        # uuid -> last known name (filled in as players are seen)
}


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Settings:
    def __init__(self, path=None):
        self.path = path or os.path.join(DATA_DIR, "settings.json")
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                _merge(self.data, json.load(f))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        with self._lock:
            return self.data[key]

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def update(self, changes):
        with self._lock:
            _merge(self.data, changes)
            self.save()

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def public(self):
        """Settings as sent to the panel: never the password hash."""
        with self._lock:
            d = copy.deepcopy(self.data)
        d["panel"].pop("password_hash", None)
        d["panel"].pop("salt", None)
        d["panel"]["has_password"] = bool(self.data["panel"].get("password_hash"))
        return d
