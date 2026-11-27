"""Everything specific to the Hytale dedicated server: its log format, its files, its commands,
its folder layout and where to find out about new versions.

Sources: the official Hytale Server Manual (support.hytale.com) and the behaviour of Hytale 0.7.x
servers as documented by existing open-source panels. See docs/hytale-notes.md.
"""
import json
import os
import re
import time
import zipfile
from xml.etree import ElementTree

# --------------------------------------------------------------------------------------------- log
# [2026/10/27 21:12:06   INFO]           [HytaleServer] Starting HytaleServer
LINE_RE = re.compile(r"^\[(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2}) +([A-Z]{1,7})\] *(?:\[([^\]\n]{1,80})\] ?)?(.*)$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Only the universe's lines: "Adding player 'Name (uuid)" and "Removing player 'Name' (uuid)". The
# world's own "Removing player 'Name (Name)' from world 'default' (uuid)" must not match.
JOIN_RE = re.compile(r"Adding player '([A-Za-z0-9_.\-]{1,32}) \(([0-9a-fA-F-]{36})\)")
LEAVE_RE = re.compile(r"Removing player '([A-Za-z0-9_.\-]{1,32})' \(([0-9a-fA-F-]{36})\)")
READY_TEXT = "Hytale Server Booted"
STARTING_TEXT = "Starting HytaleServer"
CMD_ECHO_RE = re.compile(r"Console executed command: (.*)$")
AUTH_URL_RE = re.compile(r"(https://\S*hytale\.com/\S*device\S*)")
AUTH_CODE_RE = re.compile(r"(?:Enter code|code):\s*([A-Z0-9]{3,}(?:-[A-Z0-9]{3,})+|[A-Z0-9]{6,})", re.I)
AUTH_OK_TEXT = "Authentication successful"
AUTH_MISSING_RE = re.compile(r"Server starting unauthenticated|No server tokens configured|not authenticated", re.I)
CHAT_RE = re.compile(r"^\[OrbisFox\|Chat\] (.+?): (.*)$")   # printed by the OrbisFox Bridge plugin
SAY_RE = re.compile(r"^(?:say|broadcast) (.*)$", re.I)
# The running server's own answer about the whitelist ("whitelist status", enable, disable). It keeps
# the setting in memory, so this beats reading config.json.
WHITELIST_STATE_RE = re.compile(r"Whitelist (?:is currently |is already )?(enabled|disabled)!")

# Signs that a world is falling behind (see docs/chunk-freeze.md).
STALL_PATTERNS = [
    ("tick_overrun", re.compile(r"Took too long to run .*> ?TICK_STEP")),
    ("world_rejecting", re.compile(r"World thread is not accepting tasks")),
    ("task_failed", re.compile(r"\[World\|[^\]]+\] Failed to run task")),
    ("chunk_gen_failed", re.compile(r"Failed to generate chunk")),
    ("ticking_abandoned", re.compile(r"Abandoning TickingThread")),
    ("out_of_memory", re.compile(r"OutOfMemoryError")),
]
_DUR_RE = re.compile(r"(?:(\d+)\s*min\s*)?(?:(\d+)\s*sec\s*)?(\d+)\s*ms")
WORLD_TAG_RE = re.compile(r"World\|([^\]|]+)")


def parse_line(raw):
    """Returns (text_without_ansi, level, logger, message)."""
    text = ANSI_RE.sub("", raw).rstrip()
    while text.startswith("> "):   # a JLine prompt glued to the next line
        text = text[2:]
    m = LINE_RE.match(text)
    if not m:
        lvl = "SEVERE" if text.startswith(("Exception", "Caused by", "\tat ", "java.", "[SERR]")) else ""
        return text, lvl, "", text
    return text, m.group(7), m.group(8) or "", m.group(9) or ""


def classify(level, logger, message):
    """The console colour class for one line."""
    if CHAT_RE.match(message):
        return "chat"
    if JOIN_RE.search(message):
        return "join"
    if LEAVE_RE.search(message):
        return "leave"
    if level in ("SEVERE", "ERROR", "FATAL") or "Exception" in message[:120]:
        return "error"
    if level in ("WARN", "WARNING"):
        return "warn"
    if CMD_ECHO_RE.search(message):
        return "cmdlog"
    return "info"


SLOW_TICK_RE = re.compile(r"^Took too long to run (.+?) for section: (.+?) > TICK_STEP, Has GC Run: (true|false)"
                          r"(?:.*Hook: ([\w.$]+))?", re.S)


def condense(level, message):
    """A short, readable version of a known giant log line, or None. The server's slow-tick report
    dumps the whole chunk section (thousands of characters); people need the gist."""
    if len(message) < 200 or not message.startswith("Took too long to run"):
        return None
    m = SLOW_TICK_RE.match(message)
    if not m:
        return None
    d = _DUR_RE.search(m.group(2))
    ms = int(d.group(1) or 0) * 60000 + int(d.group(2) or 0) * 1000 + int(d.group(3)) if d else None
    hook = (m.group(4) or "").split(".")[-1].split("$")[0]
    out = f"Slow tick: a chunk's {m.group(1)} took {ms if ms is not None else m.group(2)} ms"
    if m.group(3) == "true":
        out += " while Java was freeing memory"
    if hook:
        out += f" ({hook})"
    return out + ". Harmless now and then; the watchdog keeps count."


def stall_signal(message, logger=""):
    """(kind, duration_ms or None, world or None) when the line is a known stall sign."""
    for kind, rx in STALL_PATTERNS:
        if rx.search(message) or (logger and rx.search(f"[{logger}] {message}")):
            dur = None
            m = _DUR_RE.search(message)
            if m and kind == "tick_overrun":
                dur = int(m.group(1) or 0) * 60000 + int(m.group(2) or 0) * 1000 + int(m.group(3))
            w = WORLD_TAG_RE.search(logger or "") or WORLD_TAG_RE.search(message)
            return kind, dur, (w.group(1).strip() if w else None)
    return None


# ------------------------------------------------------------------------------------ commands
def command_text(text, max_len=256):
    """Free text (announcements, reasons) as an argument the Hytale command parser takes literally.
    It rejects unbalanced quotes, backslashes, brackets, double commas, words starting with "--"
    and treats a leading "{" as JSON, so those become look-alikes."""
    t = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))
    t = t.replace('"', "\u201d")
    t = re.sub(r"(^|[\s,])'", "\\1\u2019", t)
    t = t.replace("[", "(").replace("]", ")").replace("\\", "/")
    t = re.sub(r",(\s*,)+", ",", t)
    t = re.sub(r"(^|[\s,])--", "\\1\u2013", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[,\s]+", "", t)
    if t.startswith("{"):
        t = "(" + t[1:]
    return t[:max_len].strip()


PLAYER_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ITEM_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,96}$")


def player_arg(name):
    name = str(name or "").strip()
    if UUID_RE.match(name):
        return name.lower()
    if not PLAYER_RE.match(name):
        raise ValueError("That is not a valid player name.")
    return name


def stdin_escape(line):
    """The console reads stdin through JLine, which expands ! (history) and eats backslashes."""
    return re.sub(r"([\\!])", r"\\\1", line)


TIME_PRESETS = {"dawn": "dawn", "midday": "midday", "dusk": "dusk", "midnight": "midnight"}
GAMEMODES = ("adventure", "creative")
NOTIFY_STYLES = ("", "default", "warning", "danger", "success")

# Console autocomplete: (command, arguments, what it does). Hytale 0.7.x vanilla commands.
CATALOG = [
    ("who", "", "List online players per world"),
    ("whoami", "<player>", "Show a player's UUID"),
    ("whereami", "<player>", "Show a player's world and position"),
    ("kick", "<player>", "Disconnect a player"),
    ("ban", "<player> [reason]", "Ban a player"),
    ("unban", "<player>", "Lift a ban"),
    ("op add", "<player>", "Make a player an operator"),
    ("op remove", "<player>", "Take operator rights away"),
    ("whitelist add", "<player>", "Allow a player to join"),
    ("whitelist remove", "<player>", "Remove a player from the whitelist"),
    ("whitelist enable", "", "Only whitelisted players may join"),
    ("whitelist disable", "", "Anyone may join"),
    ("whitelist list", "", "List whitelisted players"),
    ("whitelist status", "", "Is the whitelist on?"),
    ("say", "<message>", "Chat message to everyone (alias broadcast)"),
    ("notify", "[warning|danger|success] <message>", "On-screen notice to everyone"),
    ("eventtitle", "<title> [--secondary=<text>]", "Big title on every screen"),
    ("time set", "<dawn|midday|dusk|midnight>", "Set the time of day"),
    ("time pause", "", "Pause or resume the day cycle"),
    ("weather set", "<WeatherId>", "Force a weather, e.g. Zone1_Rain"),
    ("weather reset", "", "Back to natural weather"),
    ("weather get", "", "Show the current weather"),
    ("tp", "<player> <target|x y z>", "Teleport a player"),
    ("gamemode", "<adventure|creative> <player>", "Change a player's game mode (alias gm)"),
    ("give", "<player> <item> --quantity=<n>", "Give a player items"),
    ("kill", "<player>", "Kill a player"),
    ("player respawn", "<player>", "Respawn a player"),
    ("world list", "", "List loaded worlds"),
    ("world save", "--all --confirm", "Save every world now"),
    ("world perf", "[--all]", "Ticks per second per world"),
    ("world perf graph", "", "Tick time graph"),
    ("world add", "<name>", "Create a world"),
    ("world load", "<name>", "Load a world"),
    ("maxviewradius", "<chunks>|reset", "Change the view distance live (1-32)"),
    ("maxplayers", "--amount <n>", "Change the player limit"),
    ("server stats", "<memory|cpu|gc>", "Memory, CPU or GC statistics"),
    ("chunk unload", "", "Unload chunks nobody needs"),
    ("worldgen2 concurrency", "<level>", "Limit world generation threads (0 = default)"),
    ("backup", "", "Run the server's built-in backup"),
    ("version", "", "Show the server version"),
    ("update check", "", "Look for a server update"),
    ("update download", "", "Download an update to staging"),
    ("update apply", "--confirm", "Apply a staged update (restarts the server)"),
    ("update status", "", "Updater status"),
    ("auth login device", "", "Sign the server in to Hytale"),
    ("auth persistence", "Encrypted", "Remember the server's sign-in"),
    ("auth status", "", "Show the server's sign-in"),
    ("plugin list", "", "Loaded plugins and mods"),
    ("perm user add", "<uuid> <permission>", "Give a player a permission"),
    ("perm group add", "<group> <permission>", "Give a group a permission"),
    ("help", "[command]", "Command help (or add --help to any command)"),
    ("stop", "", "Stop the server"),
]


# ------------------------------------------------------------------------------------ layout
def locate(server_dir):
    """Works out the layout from the folder the user picked: either the "Server" folder (holding
    HytaleServer.jar) or the game folder above it. Returns a dict of paths and problems."""
    d = os.path.abspath(server_dir) if server_dir else ""
    info = {"server_dir": d, "game_dir": None, "jar": None, "assets": None, "aot": None,
            "jvm_options": None, "problems": []}
    if not d or not os.path.isdir(d):
        info["problems"].append("Pick the folder that holds HytaleServer.jar.")
        return info
    if not os.path.isfile(os.path.join(d, "HytaleServer.jar")) and \
            os.path.isfile(os.path.join(d, "Server", "HytaleServer.jar")):
        d = os.path.join(d, "Server")
        info["server_dir"] = d
    jar = os.path.join(d, "HytaleServer.jar")
    if os.path.isfile(jar):
        info["jar"] = jar
    else:
        info["problems"].append("HytaleServer.jar is not in this folder.")
    parent = os.path.dirname(d)
    for cand in (os.path.join(parent, "Assets.zip"), os.path.join(d, "Assets.zip")):
        if os.path.isfile(cand):
            info["assets"] = cand
            break
    if not info["assets"]:
        info["problems"].append("Assets.zip was not found next to the Server folder.")
    if os.path.isfile(os.path.join(parent, "Assets.zip")):
        info["game_dir"] = parent
        if os.path.isfile(os.path.join(parent, "jvm.options")):
            info["jvm_options"] = os.path.join(parent, "jvm.options")
    aot = os.path.join(d, "HytaleServer.aot")
    if os.path.isfile(aot):
        info["aot"] = aot
    return info


_version_cache = {}


def jar_version(jar):
    """The server version written in HytaleServer.jar's manifest."""
    if not jar or not os.path.isfile(jar):
        return None
    key = (jar, os.path.getmtime(jar))
    if key in _version_cache:
        return _version_cache[key]
    ver = None
    try:
        with zipfile.ZipFile(jar) as z:
            mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
        fields = {}
        for line in mf.splitlines():
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
        ver = fields.get("Implementation-Version") or fields.get("Specification-Version") or \
            fields.get("Hytale-Version") or fields.get("Build-Version")
    except (OSError, KeyError, zipfile.BadZipFile):
        ver = None
    _version_cache[key] = ver
    return ver


# ------------------------------------------------------------------------------- versions
MAVEN = "https://maven.hytale.com/{line}/com/hypixel/hytale/Server/maven-metadata.xml"


def latest_version(patchline="release", timeout=10):
    """Newest server version published for a patchline (public, no sign-in needed)."""
    line = "pre-release" if patchline == "pre-release" else "release"
    from . import net
    root = ElementTree.fromstring(net.fetch(MAVEN.format(line=line), timeout=timeout))
    ver = root.findtext("versioning/release") or root.findtext("versioning/latest")
    versions = [v.text for v in root.findall("versioning/versions/version") if v.text]
    if versions:
        ver = max(versions, key=version_key) if not ver else ver
    updated = root.findtext("versioning/lastUpdated")
    return {"version": ver, "patchline": line, "updated": updated}


def version_key(v):
    """Sort key: old date-style builds (2026.01.22-abc) sort before semantic versions (0.7.7);
    a pre-release sorts before its final release."""
    v = (v or "").strip()
    m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})", v)
    if m:
        return (0, tuple(int(x) for x in m.groups()), 0, ())
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-(.+))?", v)
    if m:
        nums = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
        pre = m.group(4)
        pre_key = tuple(int(x) if x.isdigit() else x for x in re.split(r"[.\-]", pre)) if pre else ()
        return (1, nums, 0 if pre else 1, pre_key)
    return (-1, (), 0, (v,))


def is_newer(latest, installed):
    if not latest or not installed:
        return None
    if version_key(installed)[0] == -1 or version_key(latest)[0] == -1:
        return latest != installed
    return version_key(latest) > version_key(installed)


# ---------------------------------------------------------------------------------- files
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".orbisfox.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


OP_GROUPS = {"OP", "hytale:Admin", "Admin", "hytale:admin"}
JOIN_PERMISSION = "hytale.server.join"


def read_access(server_dir, names):
    """Whitelist, operators and bans as stored on disk. `names` maps uuid -> player name."""
    nm = lambda u: names.get(str(u).lower()) or names.get(str(u))  # noqa: E731
    out = {"whitelist": {"enabled": None, "entries": [], "source": None}, "ops": [], "bans": []}
    cfg = read_json(os.path.join(server_dir, "config.json"), {}) or {}
    perms = read_json(os.path.join(server_dir, "permissions.json"), {}) or {}
    users = perms.get("users") or {}
    # Operators: users in the admin/OP group.
    for uuid, u in users.items():
        groups = set(u.get("groups") or [])
        if groups & OP_GROUPS or "*" in (u.get("permissions") or []):
            out["ops"].append({"uuid": uuid, "name": nm(uuid)})
    # Whitelist: legacy whitelist.json, or (0.7+) the join permission + RequireJoinPermission.
    wl = read_json(os.path.join(server_dir, "whitelist.json"))
    if isinstance(wl, dict) and "list" in wl:
        out["whitelist"]["enabled"] = bool(wl.get("enabled"))
        out["whitelist"]["entries"] = [{"uuid": u, "name": nm(u)} for u in wl.get("list") or []]
        out["whitelist"]["source"] = "whitelist.json"
    else:
        out["whitelist"]["enabled"] = bool(cfg.get("RequireJoinPermission")) if "RequireJoinPermission" in cfg else None
        for uuid, u in users.items():
            if JOIN_PERMISSION in (u.get("permissions") or []):
                out["whitelist"]["entries"].append({"uuid": uuid, "name": nm(uuid)})
        out["whitelist"]["source"] = "permissions.json"
    bans = read_json(os.path.join(server_dir, "bans.json"))
    items = bans.get("Bans") if isinstance(bans, dict) else bans if isinstance(bans, list) else []
    for b in items or []:
        if not isinstance(b, dict):
            continue
        target = b.get("Target") or b.get("target") or b.get("uuid")
        out["bans"].append({"uuid": target, "name": nm(target) or b.get("name"),
                            "reason": b.get("Reason") or b.get("reason"),
                            "since": b.get("Timestamp") or b.get("created"),
                            "until": b.get("ExpiresOn") or b.get("expires")})
    return out


def _log_time(line):
    """Seconds since the epoch for a "[yyyy/MM/dd HH:mm:ss" log line, or None."""
    if len(line) < 21 or line[0] != "[" or line[5] != "/":
        return None
    try:
        return time.mktime((int(line[1:5]), int(line[6:8]), int(line[9:11]), int(line[12:14]), int(line[15:17]),
                            int(line[18:20]), 0, 0, -1))
    except ValueError:
        return None


def read_log_sessions(server_dir, before):
    """Playtime from the server's own logs (logs/*.log) up to `before`: who joined when and for how
    long. Hytale doesn't keep lifetime stats, so this is the only record of play before OrbisFox.
    Returns {uuid: {name, sessions, playtime, first_seen, last_seen}}."""
    base = os.path.join(server_dir, "logs")
    try:
        files = sorted(f for f in os.listdir(base) if f.endswith(".log") and not f.startswith("orbisfox"))
    except OSError:
        return {}
    out = {}

    def close(uuid, start, end):
        if end is None or end < start:
            return
        h = out[uuid]
        h["playtime"] += min(end, before) - start
        h["last_seen"] = max(h["last_seen"] or 0, min(end, before))

    for f in files:
        open_ = {}
        last = None
        try:
            fh = open(os.path.join(base, f), encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if line.startswith("[2"):
                    t = _log_time(line)
                    if t is not None:
                        last = t
                if last is not None and last >= before:
                    break
                if "player '" not in line:
                    continue
                m = JOIN_RE.search(line)
                if m and last is not None:
                    uuid = m.group(2).lower()
                    h = out.setdefault(uuid, {"name": m.group(1), "sessions": 0, "playtime": 0.0,
                                              "first_seen": last, "last_seen": last})
                    h["name"] = m.group(1)
                    if uuid not in open_:
                        h["sessions"] += 1
                        open_[uuid] = last
                    continue
                m = LEAVE_RE.search(line)
                if m and m.group(2).lower() in open_:
                    uuid = m.group(2).lower()
                    close(uuid, open_.pop(uuid), last)
        for uuid, start in open_.items():   # the log ends (stop or crash) with them still on
            close(uuid, start, last)
    return out


def read_players(server_dir):
    """Saved players: universe/players/<uuid>.json -> name, world, position, deaths on record."""
    out = {}
    base = os.path.join(server_dir, "universe", "players")
    try:
        files = os.listdir(base)
    except OSError:
        return out
    for f in files:
        if not f.endswith(".json"):
            continue
        uuid = f[:-5].lower()
        if not UUID_RE.match(uuid):
            continue
        data = read_json(os.path.join(base, f), {}) or {}
        comps = data.get("Components") or {}
        name = None
        np = comps.get("Nameplate") or {}
        if isinstance(np, dict):
            name = np.get("Text")
        dn = comps.get("DisplayName") or {}
        if not name and isinstance(dn, dict):
            name = dn.get("DisplayName") or dn.get("Text")
            if isinstance(name, dict):
                name = name.get("RawText") or name.get("Text")
        pos = ((comps.get("Transform") or {}).get("Position")) or {}
        player = comps.get("Player") or {}
        pdata = player.get("PlayerData") or {}
        deaths = 0
        for wd in (pdata.get("PerWorldData") or {}).values():
            if isinstance(wd, dict):
                deaths += len(wd.get("DeathPositions") or [])
        try:
            mtime = os.path.getmtime(os.path.join(base, f))
        except OSError:
            mtime = None
        out[uuid] = {"uuid": uuid, "name": name if isinstance(name, str) else None,
                     "world": pdata.get("World"), "gamemode": player.get("GameMode"),
                     "x": pos.get("X"), "y": pos.get("Y"), "z": pos.get("Z"),
                     "death_markers": deaths, "saved": mtime}
    return out


def read_config(server_dir):
    return read_json(os.path.join(server_dir, "config.json"), {}) or {}


EDITABLE_CONFIG = {
    "ServerName": str, "MOTD": str, "Password": str, "MaxPlayers": int, "MaxViewRadius": int,
    "RequireJoinPermission": bool,
}


def config_changes(changes):
    """Checks and converts edits to config.json. Returns {key: value}; raises ValueError."""
    out = {}
    for k, v in (changes or {}).items():
        if k == "GameMode":
            if v not in ("Adventure", "Creative"):
                raise ValueError("Game mode must be Adventure or Creative.")
            out[k] = v
            continue
        typ = EDITABLE_CONFIG.get(k)
        if not typ:
            raise ValueError(f"{k} can't be changed here.")
        v = typ(v)
        if k == "MaxViewRadius":
            v = max(1, min(32, v))
        if k == "MaxPlayers":
            v = max(1, min(1000, v))
        out[k] = v
    return out


def update_config(server_dir, changes):
    path = os.path.join(server_dir, "config.json")
    cfg = read_json(path, None)
    if cfg is None:
        raise RuntimeError("config.json does not exist yet: start the server once first.")
    for k, v in config_changes(changes).items():
        if k == "GameMode":
            cfg.setdefault("Defaults", {})["GameMode"] = v
        else:
            cfg[k] = v
    write_json(path, cfg)
    return cfg


def worlds(server_dir):
    base = os.path.join(server_dir, "universe", "worlds")
    out = []
    try:
        for name in sorted(os.listdir(base)):
            wdir = os.path.join(base, name)
            if os.path.isdir(wdir):
                cfg = read_json(os.path.join(wdir, "config.json"), {}) or {}
                out.append({"name": name, "seed": cfg.get("Seed"), "pvp": cfg.get("IsPvpEnabled"),
                            "pregen": (cfg.get("ChunkConfig") or {}).get("PregenerateRegion")})
    except OSError:
        pass
    return out


def log_files(server_dir):
    base = os.path.join(server_dir, "logs")
    out = []
    try:
        for n in os.listdir(base):
            p = os.path.join(base, n)
            if os.path.isfile(p) and n.endswith((".log", ".txt")):
                st = os.stat(p)
                out.append({"name": n, "size": st.st_size, "modified": st.st_mtime})
    except OSError:
        pass
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out


def mods(server_dir):
    base = os.path.join(server_dir, "mods")
    out = []
    try:
        for n in sorted(os.listdir(base), key=str.lower):
            p = os.path.join(base, n)
            low = n.lower()
            if os.path.isfile(p) and (low.endswith((".jar", ".zip")) or low.endswith((".jar.disabled", ".zip.disabled"))):
                out.append({"file": n, "name": re.sub(r"\.(jar|zip)(\.disabled)?$", "", n, flags=re.I),
                            "enabled": not low.endswith(".disabled"), "size": os.path.getsize(p),
                            "modified": os.path.getmtime(p)})
    except OSError:
        pass
    return out


def hytale_backups(server_dir, backup_dir="backups"):
    """Backups written by the server itself (--backup)."""
    base = backup_dir if os.path.isabs(backup_dir) else os.path.join(server_dir, backup_dir)
    out = []
    try:
        for n in os.listdir(base):
            p = os.path.join(base, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            size = st.st_size
            if os.path.isdir(p):
                size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)
            out.append({"name": n, "created": st.st_mtime, "size": size, "source": "hytale",
                        "path": p})
    except OSError:
        pass
    out.sort(key=lambda b: b["created"], reverse=True)
    return out


def now():
    return time.time()
