"""Reads an existing server's own launch setup so OrbisFox starts it the same way.

Looks at:
  * the start script (start.bat / start.sh, or any .bat/.cmd/.sh/.ps1 next to the server that runs
    HytaleServer.jar): Java path, memory, JVM flags, --assets, --bind, --backup options and any
    other server options;
  * jvm.options (one JVM flag per line), which the official scripts load with @jvm.options;
  * config.json: the server's own Backup and Update settings, and its view distance.
Returns (settings changes, list of plain-language notes).
"""
import os
import re
import shlex
import time

from . import hytale, jvm

MANAGED_JVM = re.compile(r"^-(Xm[sx]|XX:AOTCache=|Xlog:gc|Dstdout\.encoding|Dstderr\.encoding|Dfile\.encoding|"
                         r"XX:\+ExitOnOutOfMemoryError)")
SCRIPT_EXT = (".bat", ".cmd", ".sh", ".ps1")


def _gb(token):
    m = re.match(r"^-Xm[sx](\d+(?:\.\d+)?)([kKmMgG]?)$", token)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return round(n / (1024 * 1024) if unit == "k" else n / 1024 if unit == "m" else n if unit == "g" else n / 1024 ** 3, 2)


def find_script(lay):
    """The newest script near the server that launches HytaleServer.jar."""
    dirs = [d for d in (lay.get("game_dir"), lay.get("server_dir")) if d]
    best = None
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if not n.lower().endswith(SCRIPT_EXT) or not os.path.isfile(p) or os.path.getsize(p) > 200_000:
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            if "hytaleserver.jar" not in text.lower():
                continue
            score = (n.lower().startswith("start"), os.path.getmtime(p))
            if not best or score > best[0]:
                best = (score, p, text)
    return (best[1], best[2]) if best else (None, None)


def _expand(line, env, windows):
    if windows:
        return re.sub(r"%(\w+)%", lambda m: env.get(m.group(1).upper(), ""), line)
    return re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m.group(1).upper(), ""), line)


def parse_script(text, windows):
    """Returns the tokens of the java command line, with simple variables expanded."""
    env = {}
    java_line = None
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if not line or low.startswith(("rem ", "::", "#", "echo")):
            continue
        m = re.match(r'^(?:set\s+"?|export\s+)?(\w+)=(.*?)"?$', line, re.I) if ("=" in line and " -jar " not in low) else None
        if m and not low.startswith(("if ", "for ")):
            env[m.group(1).upper()] = _expand(m.group(2).strip().strip('"'), env, windows)
            continue
        if "hytaleserver.jar" in low and "-jar" in low and "copy" not in low and "cp " not in low:
            java_line = _expand(line, env, windows)
    if not java_line:
        return None
    java_line = re.sub(r"^\s*(?:start\s+(?:\"[^\"]*\"\s+)?(?:/\w+\s+)*|exec\s+|&\s*)", "", java_line, flags=re.I)
    try:
        return shlex.split(java_line, posix=not windows)
    except ValueError:
        return java_line.split()


def analyse(server_dir):
    """Returns (changes, notes) for OrbisFox's settings from the server's existing setup."""
    lay = hytale.locate(server_dir)
    changes, notes = {}, []
    if not lay["jar"]:
        return changes, notes
    sd = lay["server_dir"]
    script, text = find_script(lay)
    tokens = parse_script(text, script.lower().endswith((".bat", ".cmd", ".ps1"))) if script else None
    jvm_args, server_args = [], []
    if tokens:
        try:
            jar_i = next(i for i, t in enumerate(tokens) if t.lower().endswith("hytaleserver.jar"))
        except StopIteration:
            jar_i = None
        if jar_i is not None:
            first = tokens[0].strip('"')
            pre = tokens[1:jar_i]
            if pre and pre[-1] == "-jar":
                pre = pre[:-1]
            post = tokens[jar_i + 1:]
            if first.lower().endswith(("java", "java.exe", "javaw.exe")) and os.path.isabs(first) and os.path.isfile(first):
                changes["java"] = first
                notes.append(f"Java: {first}")
            for t in pre:
                t = t.strip('"')
                if t.startswith("@"):
                    continue          # jvm.options, read below
                if t.startswith("-Xmx") and _gb(t):
                    changes["ram_max_gb"] = _gb(t)
                elif t.startswith("-Xms") and _gb(t):
                    changes["ram_min_gb"] = _gb(t)
                elif t.startswith("-XX:AOTCache"):
                    changes["aot_cache"] = True
                elif t.startswith("-") and not MANAGED_JVM.match(t) and not t.startswith("%") and not t.startswith("$"):
                    jvm_args.append(t)
            i = 0
            backup = {}
            while i < len(post):
                t = post[i].strip('"')
                nxt = post[i + 1].strip('"') if i + 1 < len(post) else None
                if t in ("%*", '"$@"', "$@", "%~1"):
                    pass
                elif t == "--assets" and nxt:
                    p = nxt if os.path.isabs(nxt) else os.path.normpath(os.path.join(sd, nxt))
                    if os.path.isfile(p) and os.path.normcase(p) != os.path.normcase(lay["assets"] or ""):
                        changes["assets"] = p
                        notes.append(f"Assets: {p}")
                    i += 1
                elif t in ("--bind", "-b") and nxt:
                    changes["bind"] = nxt
                    i += 1
                elif t == "--backup":
                    backup["hytale_builtin"] = True
                elif t == "--backup-dir" and nxt:
                    backup["hytale_dir"] = nxt
                    i += 1
                elif t == "--backup-frequency" and nxt and nxt.isdigit():
                    backup["hytale_frequency"] = int(nxt)
                    i += 1
                elif t.startswith("-"):
                    server_args.append(t)
                    if nxt and not nxt.startswith("-") and "=" not in t:
                        server_args.append(nxt)
                        i += 1
                i += 1
            if "hytale_builtin" not in backup:
                backup["hytale_builtin"] = False
            changes["backups"] = backup
            notes.append(f"Launch options from {os.path.basename(script)}")
    # jvm.options (the official way to set memory)
    if lay["jvm_options"]:
        opts = jvm.read_options_file(lay["jvm_options"])
        for t in opts:
            if t.startswith("-Xmx") and _gb(t):
                changes["ram_max_gb"] = _gb(t)
            elif t.startswith("-Xms") and _gb(t):
                changes["ram_min_gb"] = _gb(t)
        if opts:
            notes.append("Memory and Java flags from jvm.options (still applied at every start)")
    if "ram_max_gb" in changes:
        notes.append(f"Memory: {changes.get('ram_min_gb', '?')}–{changes['ram_max_gb']} GB")
    changes["jvm_args"] = " ".join(jvm_args)
    changes["server_args"] = " ".join(shlex.quote(a) if " " in a else a for a in server_args)
    if jvm_args:
        notes.append("Extra Java flags: " + " ".join(jvm_args))
    if server_args:
        notes.append("Extra server options: " + changes["server_args"])
    # config.json: the server's own backup and update settings
    cfg = hytale.read_config(sd)
    b = cfg.get("Backup") or {}
    if isinstance(b, dict) and b.get("Enabled") and not (changes.get("backups") or {}).get("hytale_builtin"):
        bk = changes.setdefault("backups", {})
        bk["hytale_builtin"] = True
        if b.get("FrequencyMinutes"):
            bk["hytale_frequency"] = int(b["FrequencyMinutes"])
        if b.get("Directory"):
            bk["hytale_dir"] = b["Directory"]
    bk = changes.get("backups") or {}
    if bk.get("hytale_builtin"):
        notes.append(f"Server's own backups: every {bk.get('hytale_frequency', 30)} min to {bk.get('hytale_dir', 'backups')}")
    u = cfg.get("Update") or {}
    if isinstance(u, dict):
        upd = {}
        if u.get("Patchline") in ("release", "pre-release"):
            upd["patchline"] = u["Patchline"]
            notes.append(f"Update channel: {u['Patchline']}")
        mode = u.get("AutoApplyMode")
        if mode == "WhenEmpty":
            upd["auto_install"] = "empty"
            notes.append("Automatic updates when nobody is online (OrbisFox runs them now, so operators can veto)")
        elif mode == "Scheduled":
            upd["auto_install"] = "always"
            notes.append("Automatic updates even with players on (OrbisFox runs them now: 5 minutes' warning, operators can veto)")
        if upd:
            changes["updates"] = upd
    if cfg.get("MaxViewRadius"):
        notes.append(f"View distance: {cfg['MaxViewRadius']} chunks")
    changes["watchdog"] = {"baseline_radius": None}
    return changes, notes


def apply(core, server_dir):
    """Imports into OrbisFox's settings and remembers what was imported."""
    changes, notes = analyse(server_dir)
    if not changes:
        return []
    lay = hytale.locate(server_dir)
    changes["imported"] = {"server_dir": lay["server_dir"], "at": time.time(), "notes": notes}
    core.settings.update(changes)
    return notes
