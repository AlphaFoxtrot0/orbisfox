"""The panel's JSON API. Every handler gets (core, body, query) and returns something JSON-able."""
import os
import threading
import time

from . import hytale, watchdog as wdmod
from .httpd import ApiError, Router, hash_password

r = Router()


def _q(query, key, default=None):
    v = query.get(key)
    return v[0] if v else default


def _need_running(core):
    if not core.server.running:
        raise ApiError(409, "Start the server first.")


# ------------------------------------------------------------------------------- state
@r.get("/api/state")
def state(core, body, query):
    return core.snapshot()


# ------------------------------------------------------------------------------- server control
@r.post("/api/server/start")
def server_start(core, body, query):
    if core.orphan:
        raise ApiError(409, "A server from an earlier OrbisFox session is still running. Stop it first.")
    core.server.start("started from the panel", force=bool(body.get("force")))


@r.post("/api/server/stop")
def server_stop(core, body, query):
    core.server.schedule("stop", int(body.get("warn_seconds") or 0), "stopped from the panel")


@r.post("/api/server/restart")
def server_restart(core, body, query):
    if not core.server.alive:
        core.server.start("restart from the panel")
        return
    core.server.schedule("restart", int(body.get("warn_seconds") or 0), "restarted from the panel")


@r.post("/api/server/cancel")
def server_cancel(core, body, query):
    core.server.cancel_pending()


@r.post("/api/server/kill")
def server_kill(core, body, query):
    core.server.kill()


@r.post("/api/server/kill-orphan")
def server_kill_orphan(core, body, query):
    core.kill_orphan()


@r.post("/api/command")
def command(core, body, query):
    line = core.server.send(str(body.get("line") or ""))
    low = line.lower()
    if low.startswith(("op ", "whitelist", "ban ", "unban ", "perm ")):
        core.refresh_access_soon()
    return {"sent": line}


@r.post("/api/say")
def say(core, body, query):
    _need_running(core)
    return {"sent": core.server.say(body.get("message"), body.get("style") or "chat", body.get("level") or "")}


@r.post("/api/auth/login")
def auth_login(core, body, query):
    _need_running(core)
    core.server.send("auth login device", source="orbisfox")


@r.post("/api/auth/persist")
def auth_persist(core, body, query):
    _need_running(core)
    core.server.send("auth persistence Encrypted", source="orbisfox")


# ------------------------------------------------------------------------------- players
@r.post("/api/players/action")
def player_action(core, body, query):
    _need_running(core)
    name = hytale.player_arg(body.get("name"))
    act = body.get("action")
    sv = core.server
    if act == "kick":
        sv.send(f"kick {name}")
    elif act == "ban":
        reason = hytale.command_text(body.get("reason") or "")
        sv.send(f"ban {name}" + (f" {reason}" if reason else ""))
        core.refresh_access_soon()
    elif act == "unban":
        sv.send(f"unban {name}")
        core.refresh_access_soon()
    elif act == "op":
        sv.send(f"op {'add' if body.get('on', True) else 'remove'} {name}")
        core.refresh_access_soon()
    elif act == "whitelist":
        sv.send(f"whitelist {'add' if body.get('on', True) else 'remove'} {name}")
        core.refresh_access_soon()
    elif act == "gamemode":
        mode = str(body.get("mode") or "").lower()
        if mode not in hytale.GAMEMODES:
            raise ValueError("Game mode must be adventure or creative.")
        sv.send(f"gamemode {mode} {name}")
    elif act == "tp":
        if body.get("target"):
            sv.send(f"tp {name} {hytale.player_arg(body['target'])}")
        else:
            x, y, z = (float(body[k]) for k in ("x", "y", "z"))
            sv.send(f"tp {name} {x:g} {y:g} {z:g}")
    elif act == "give":
        item = str(body.get("item") or "").strip()
        if not hytale.ITEM_RE.match(item):
            raise ValueError("That is not a valid item id.")
        qty = max(1, min(9999, int(body.get("qty") or 1)))
        sv.send(f"give {name} {item} --quantity={qty}")
    elif act == "kill":
        sv.send(f"kill {name}")
    elif act == "respawn":
        sv.send(f"player respawn {name}")
    elif act == "locate":
        sv.send(f"whereami {name}")
    else:
        raise ValueError("Unknown player action.")


@r.get("/api/stats")
def stats(core, body, query):
    return core.players.table()


# ------------------------------------------------------------------------------- access lists
@r.get("/api/access")
def access(core, body, query):
    return core.access_cache(fresh=True)


@r.post("/api/access/whitelist")
def access_whitelist(core, body, query):
    _need_running(core)
    act = body.get("action")
    if act in ("enable", "disable"):
        core.server.send(f"whitelist {act}")
    elif act in ("add", "remove"):
        core.server.send(f"whitelist {act} {hytale.player_arg(body.get('name'))}")
    else:
        raise ValueError("Unknown whitelist action.")
    core.refresh_access_soon()


# ------------------------------------------------------------------------------- world
@r.post("/api/world/time")
def world_time(core, body, query):
    _need_running(core)
    if body.get("pause"):
        core.server.send("time pause")
        return
    preset = str(body.get("preset") or "")
    if preset not in hytale.TIME_PRESETS:
        raise ValueError("Pick dawn, midday, dusk or midnight.")
    core.server.send(f"time set {preset}")


@r.post("/api/world/weather")
def world_weather(core, body, query):
    _need_running(core)
    wid = str(body.get("id") or "").strip()
    if not wid or wid == "reset":
        core.server.send("weather reset")
    elif hytale.ITEM_RE.match(wid):
        core.server.send(f"weather set {wid}")
    else:
        raise ValueError("That is not a valid weather id.")


@r.post("/api/world/save")
def world_save(core, body, query):
    _need_running(core)
    core.server.send("world save --all --confirm")


@r.get("/api/world/config")
def world_config(core, body, query):
    sd = core.server_dir()
    if not sd:
        raise ApiError(409, "Set the server folder first.")
    cfg = hytale.read_config(sd)
    keys = list(hytale.EDITABLE_CONFIG)
    out = {k: cfg.get(k) for k in keys}
    out["GameMode"] = (cfg.get("Defaults") or {}).get("GameMode")
    return {"config": out, "exists": bool(cfg), "worlds": hytale.worlds(sd), "running": core.server.alive}


@r.post("/api/world/config")
def world_config_save(core, body, query):
    sd = core.server_dir()
    if not sd:
        raise ApiError(409, "Set the server folder first.")
    changes = dict(body.get("changes") or {})
    live = []
    if core.server.running:
        # These two can change live; everything else is written now and used on the next start.
        if "MaxViewRadius" in changes:
            n = max(1, min(32, int(changes.pop("MaxViewRadius"))))
            core.server.send(f"maxviewradius {n}", source="orbisfox")
            core.watchdog.baseline_radius = core.watchdog.current_radius = n
            core.settings.update({"watchdog": {"baseline_radius": None}})
            live.append("view distance")
        if "MaxPlayers" in changes:
            n = max(1, min(1000, int(changes.pop("MaxPlayers"))))
            core.server.send(f"maxplayers --amount {n}", source="orbisfox")
            live.append("player limit")
        if changes:
            raise ApiError(409, "Stop the server to change the name, message, password, game mode or "
                                "whitelist setting: it rewrites config.json while running.")
    else:
        hytale.update_config(sd, changes)
    core.publish_server()
    return {"live": live}


# ------------------------------------------------------------------------------- backups
@r.get("/api/backups")
def backups(core, body, query):
    return core.backups_info()


@r.post("/api/backups/create")
def backups_create(core, body, query):
    if core.backups.busy:
        raise ApiError(409, "A backup is already running.")
    if body.get("builtin"):
        _need_running(core)
        core.server.send("backup")
        return
    threading.Thread(target=core._bg_backup, args=("manual",), daemon=True).start()


@r.post("/api/backups/restore")
def backups_restore(core, body, query):
    name = str(body.get("name") or "")
    core.backups.path_of(name)
    if core.server.alive:
        raise ApiError(409, "Stop the server before restoring a backup.")

    def go():
        try:
            core.backups.restore(name)
            core.toast("Backup restored", name)
            if body.get("start"):
                core.server.start("after restoring a backup")
        except Exception as e:  # noqa: BLE001
            core.log_app(f"Restore failed: {e}", level="error")
            core.toast("Restore failed", str(e), "bad", 0)
    threading.Thread(target=go, daemon=True).start()


@r.post("/api/backups/delete")
def backups_delete(core, body, query):
    core.backups.delete(str(body.get("name") or ""))
    core.publish_backups()


@r.get("/api/backups/download")
def backups_download(core, body, query):
    name = _q(query, "name", "")
    return ("file", core.backups.path_of(name), name)


# ------------------------------------------------------------------------------- updates
@r.post("/api/updates/check")
def updates_check(core, body, query):
    return core.updates.check()


@r.post("/api/updates/install")
def updates_install(core, body, query):
    core.updates.install(body.get("method") or "auto", warn=body.get("warn", True) is not False)


@r.post("/api/updates/get-downloader")
def updates_get_downloader(core, body, query):
    return {"path": core.updates.fetch_downloader()}


# ------------------------------------------------------------------------------- performance
@r.get("/api/perf")
def perf(core, body, query):
    br = core.bridge.last if core.bridge.connected else None
    return {"watchdog": core.watchdog.info(), "tips": wdmod.recommend(core),
            "worlds": (br or {}).get("worlds") or [], "history": list(core.history),
            "system": {"cpus": os.cpu_count(), "mem_total": core.sampler.system_memory()[0]},
            "launch": core.server.launch_info}


@r.post("/api/perf/apply")
def perf_apply(core, body, query):
    tip = next((t for t in wdmod.recommend(core) if t["id"] == body.get("id")), None)
    if not tip:
        raise ValueError("That suggestion no longer applies.")
    if tip.get("apply"):
        core.settings.update(tip["apply"])
    if tip.get("apply_config"):
        world_config_save(core, {"changes": tip["apply_config"]}, {})
    core.hub.publish("settings", core.settings.public())
    return {"restart_needed": bool(tip.get("apply")) and core.server.alive}


# ------------------------------------------------------------------------------- map
@r.get("/api/map/chunks")
def map_chunks(core, body, query):
    """Chunks in memory right now (the 'Loaded chunks' overlay). Needs the OrbisFox Bridge plugin."""
    if not core.bridge.connected:
        raise ApiError(409, "The loaded-chunk overlay needs the OrbisFox Bridge plugin and a running server.")
    return core.bridge.chunks(_q(query, "world", ""))


@r.get("/api/map/info")
def map_info(core, body, query):
    return core.worldmap.info()


@r.get("/api/memories")
def memories(core, body, query):
    return core.memories.view()


@r.post("/api/bridge/install")
def bridge_install(core, body, query):
    core.bridge.install()
    core.publish_server()
    return {"restart_needed": core.server.alive}


@r.post("/api/chunks/unload")
def chunks_unload(core, body, query):
    _need_running(core)
    core.server.send("chunk unload")


# ------------------------------------------------------------------------------- files
@r.get("/api/logs")
def logs(core, body, query):
    sd = core.server_dir()
    return {"files": hytale.log_files(sd) if sd else []}


@r.get("/api/logs/read")
def logs_read(core, body, query):
    sd = core.server_dir()
    name = _q(query, "name", "")
    if not sd or not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Unknown log file.")
    p = os.path.join(sd, "logs", name)
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        if size > 400_000:
            f.seek(size - 400_000)
        text = f.read().decode("utf-8", "replace")
    return {"name": name, "text": hytale.ANSI_RE.sub("", text), "truncated": size > 400_000}


@r.get("/api/mods")
def mods(core, body, query):
    sd = core.server_dir()
    return {"mods": hytale.mods(sd) if sd else [], "bridge": core.bridge.info()}


@r.post("/api/mods/toggle")
def mods_toggle(core, body, query):
    sd = core.server_dir()
    f = str(body.get("file") or "")
    if not sd or not f or "/" in f or "\\" in f or ".." in f:
        raise ValueError("Unknown mod file.")
    src = os.path.join(sd, "mods", f)
    if not os.path.isfile(src):
        raise FileNotFoundError(f)
    dst = src[: -len(".disabled")] if f.endswith(".disabled") else src + ".disabled"
    os.replace(src, dst)
    return {"restart_needed": core.server.alive}


@r.post("/api/open")
def open_folder(core, body, query):
    if not _q(query, "_local"):
        raise ApiError(403, "Folders can only be opened on the server PC.")
    which = body.get("which")
    sd = core.server_dir()
    paths = {"server": sd, "logs": sd and os.path.join(sd, "logs"), "mods": sd and os.path.join(sd, "mods"),
             "backups": core.backups.dir, "data": core.data_dir, "map": core.worldmap.dir,
             "builtin-backups": sd and os.path.join(sd, core.settings["backups"].get("hytale_dir") or "backups")}
    if which not in paths:
        raise ValueError("Unknown folder.")
    core.open_path(paths[which])


@r.post("/api/pick-folder")
def pick_folder(core, body, query):
    """Shows a folder picker on the server PC (Windows)."""
    if not _q(query, "_local") or os.name != "nt":
        raise ApiError(403, "Type the folder path instead.")
    import subprocess
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true;ShowInTaskbar=$false};"
          "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$d.Description='Pick the folder that holds HytaleServer.jar';$d.ShowNewFolderButton=$false;"
          "if($env:OF_START -and (Test-Path $env:OF_START)){$d.SelectedPath=$env:OF_START};"
          "if($d.ShowDialog($o) -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}")
    env = dict(os.environ, OF_START=str(body.get("start") or ""))
    out = subprocess.run(["powershell", "-NoProfile", "-STA", "-NonInteractive", "-Command", ps], env=env,
                         capture_output=True, text=True, timeout=600, creationflags=0x08000000)
    path = out.stdout.strip()
    return {"path": path or None}


# ------------------------------------------------------------------------------- settings
SETTINGS_KEYS = {"server_dir", "java", "bind", "ram_min_gb", "ram_max_gb", "aot_cache", "gc_log", "exit_on_oom",
                 "jvm_args", "server_args", "auto_start", "auto_restart", "stop_timeout_s", "backups", "restarts",
                 "watchdog", "updates", "panel", "plugin_port", "start_with_windows", "track_gpu", "veto", "map",
                 "assets"}


@r.get("/api/settings")
def settings_get(core, body, query):
    from .jvm import find_java
    java, major = find_java(core.settings["java"])
    lay = hytale.locate(core.settings["server_dir"])
    return {"settings": core.settings.public(), "java": {"path": java, "version": major}, "layout": lay}


@r.post("/api/settings")
def settings_save(core, body, query):
    changes = {k: v for k, v in (body.get("changes") or {}).items() if k in SETTINGS_KEYS}
    if "panel" in changes:
        changes["panel"] = {k: v for k, v in changes["panel"].items() if k in ("port", "lan")}
        if changes["panel"].get("lan") and not core.settings["panel"].get("password_hash"):
            raise ApiError(400, "Set a panel password before allowing other devices.")
    for k in ("ram_min_gb", "ram_max_gb"):
        if k in changes:
            changes[k] = max(1.0, min(512.0, float(changes[k])))
    if "server_dir" in changes:
        lay = hytale.locate(changes["server_dir"])
        if lay["jar"]:
            changes["server_dir"] = lay["server_dir"]
    old_sw = core.settings["start_with_windows"]
    old_dir = hytale.locate(core.settings["server_dir"])["server_dir"]
    core.settings.update(changes)
    if "start_with_windows" in changes and changes["start_with_windows"] != old_sw:
        from . import desktop
        desktop.set_startup(bool(changes["start_with_windows"]))
    imported = []
    if "map" in changes:
        core.worldmap.start_share()
    if "server_dir" in changes and hytale.locate(changes["server_dir"])["jar"] and             os.path.normcase(changes["server_dir"]) != os.path.normcase(old_dir or ""):
        imported = core.import_server(changes["server_dir"])
    core.hub.publish("settings", core.settings.public())
    core.publish_server()
    restart_panel = "panel" in changes
    return {"settings": core.settings.public(), "panel_restart_needed": restart_panel, "imported": imported}


@r.post("/api/settings/import")
def settings_import(core, body, query):
    sd = core.server_dir()
    if not sd:
        raise ApiError(409, "Set the server folder first.")
    return {"imported": core.import_server(sd)}


@r.post("/api/settings/password")
def settings_password(core, body, query):
    pw = str(body.get("password") or "")
    if len(pw) < 8:
        raise ValueError("Use at least 8 characters.")
    h, salt = hash_password(pw)
    core.settings.update({"panel": {"password_hash": h, "salt": salt}})
    return {"ok": True}


@r.post("/api/app/quit")
def app_quit(core, body, query):
    def go():
        time.sleep(0.5)
        core.shutdown()
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()


router = r
