"""Memories: which creature memories exist, which the server has restored, who carries which, and
where the creatures behind the missing ones live.

How Hytale keeps them (0.7.x):
  * The list of memories is built from the creature roles in Assets.zip (Server/NPC/Roles/**.json
    with "IsMemory": true; "MemoriesNameOverride" merges several roles into one memory).
  * Memories a player has caught but not yet restored at the Heart of Orbis are in that player's
    file: universe/players/<uuid>.json -> Components.PlayerMemories.Memories.
  * Restored memories are server-wide, in universe/resources/Memories.json (older servers:
    universe/memories.json). The game doesn't record who restored them.
  * Where a creature lives comes from the world spawn rules (Server/NPC/Spawn/World/**.json:
    Environments + NPCs) and the environments' zone tags; creatures placed by dungeons and
    structures use spawn markers instead.
Everything is read from files, so it works while the server is stopped and needs no plugin. The
parsed Assets.zip catalog is cached in OrbisFox's data folder until Assets.zip changes.
"""
import json
import os
import re
import threading
import time
import zipfile

ZONES = {"Zone0": "Oceans", "Zone1": "Emerald_Wilds", "Zone2": "Howling_Sands", "Zone3": "Whisperfrost_Frontiers",
         "Zone4": "Devastated_Lands", "Void": "Void", "Oceans": "Oceans"}
CATALOG_VERSION = 2


def _json(z, name):
    try:
        return json.loads(z.read(name).decode("utf-8-sig", "replace"))
    except (KeyError, ValueError):
        return None


def _lang(z, name="Server/Languages/en-US/server.lang", prefix="server."):
    out = {}
    try:
        text = z.read(name).decode("utf-8-sig", "replace")
    except KeyError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out.setdefault(prefix + k.strip(), v.strip())
    return out


def _pretty(s):
    return re.sub(r"\s+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(s).replace("_", " "))).strip()


def build_catalog(assets_zip):
    """Parses Assets.zip into {entries, categories, zones, regions}. Takes a few seconds."""
    with zipfile.ZipFile(assets_zip) as z:
        names = z.namelist()
        lang = _lang(z)
        roles = {}
        for n in names:
            if n.startswith("Server/NPC/Roles/") and n.endswith(".json"):
                d = _json(z, n)
                if isinstance(d, dict):
                    roles[n.rsplit("/", 1)[-1][:-5]] = d

        def params(name, seen=()):
            d = roles.get(name) or {}
            own = {k: (v.get("Value") if isinstance(v, dict) else v) for k, v in (d.get("Parameters") or {}).items()}
            ref = d.get("Reference")
            if d.get("Type") == "Variant" and ref in roles and ref not in seen:
                base = params(ref, seen + (name,))
                base.update(own)
                return base
            return own

        def field(name, key, prm, seen=()):
            d = roles.get(name) or {}
            if d.get("Type") == "Variant":
                mod = d.get("Modify") or {}
                if key in mod:
                    v = mod[key]
                elif d.get("Reference") in roles and d.get("Reference") not in seen:
                    return field(d["Reference"], key, prm, seen + (name,))
                else:
                    return None
            else:
                v = d.get(key)
            if isinstance(v, dict) and "Compute" in v:
                return prm.get(v["Compute"])
            return v

        entries = {}
        for name, d in roles.items():
            if d.get("Type") == "Abstract":
                continue
            prm = params(name)
            if field(name, "IsMemory", prm) is not True:
                continue
            eid = field(name, "MemoriesNameOverride", prm) or name
            e = entries.setdefault(eid, {"id": eid, "roles": [], "category": None, "unlock": None, "name_key": None})
            e["roles"].append(name)
            e["category"] = e["category"] or field(name, "MemoriesCategory", prm)
            e["unlock"] = e["unlock"] or field(name, "MemoriesUnlockedBy", prm)
            nk = field(name, "NameTranslationKey", prm)
            if isinstance(nk, str) and (not e["name_key"] or name == eid):
                e["name_key"] = nk

        # Environments: zone and biome tags, with parent inheritance.
        envs = {}
        for n in names:
            if n.startswith("Server/Environments/") and n.endswith(".json"):
                d = _json(z, n)
                if isinstance(d, dict):
                    envs[n.rsplit("/", 1)[-1][:-5]] = d

        def env_tags(eid, seen=()):
            d = envs.get(eid) or {}
            tags = d.get("Tags") if isinstance(d.get("Tags"), dict) else None
            if tags:
                return tags
            p = d.get("Parent")
            return env_tags(p, seen + (eid,)) if p in envs and p not in seen else {}

        def env_place(eid):
            tags = env_tags(eid)
            zone_key = next((k for k in tags if k in ZONES or k.startswith("Zone")), None) or next(iter(tags), None)
            m = re.match(r"^Env_(Zone\d|Void|Oceans?|[A-Za-z]+?)_(.*)$", eid)
            if not zone_key and m:
                zone_key = m.group(1)
            biome = None
            parts = [_pretty(t) for t in (tags.get(zone_key) or [])] if zone_key else []
            if parts:
                biome = parts[0] + (f" ({', '.join(parts[1:])})" if len(parts) > 1 else "")
            elif m and m.group(2):
                biome = _pretty(m.group(2))
            zone = ZONES.get(zone_key or "")
            zone_name = lang.get(f"server.map.zone.{zone}") if zone else None
            if not zone_name and zone_key == "Portals":
                zone_name = "Portal worlds"
            return zone_name or (_pretty(zone_key) if zone_key else None), biome

        role_env, role_time, role_placed = {}, {}, set()
        for n in names:
            if not (n.startswith("Server/NPC/Spawn/") and n.endswith(".json")):
                continue
            d = _json(z, n)
            if not isinstance(d, dict):
                continue
            npcs = d.get("NPCs") or []
            ids = [x.get("Id") or x.get("Name") for x in npcs if isinstance(x, dict)]
            if n.startswith("Server/NPC/Spawn/World/") and d.get("Environments"):
                for r in ids:
                    role_env.setdefault(r, set()).update(d["Environments"])
                    if isinstance(d.get("DayTimeRange"), list) and len(d["DayTimeRange"]) == 2:
                        role_time.setdefault(r, []).append(tuple(d["DayTimeRange"]))
                    else:
                        role_time.setdefault(r, []).append((0, 24))
            else:
                role_placed.update(r for r in ids if r)

    out = []
    for e in entries.values():
        places = {}
        times = []
        placed = False
        for r in e["roles"]:
            for env in role_env.get(r, ()):
                zone, biome = env_place(env)
                key = zone or "Unknown zone"
                places.setdefault(key, set())
                if biome:
                    places[key].add(biome)
            times += role_time.get(r, [])
            placed = placed or r in role_placed
        e["name"] = lang.get(e["name_key"] or "") or _pretty(e["id"])
        e["category_name"] = lang.get(f"server.memories.categories.{e['category']}.title") or _pretty(e["category"] or "Other")
        e["where"] = [{"zone": zn, "biomes": sorted(b)} for zn, b in sorted(places.items())]
        e["time"] = _time_label(times)
        e["placed"] = placed and not places
        e["unlock"] = e["unlock"] or "Proximity"
        out.append(e)
    out.sort(key=lambda x: (x["category_name"], x["name"]))
    regions = {k: v for k, v in lang.items() if k.startswith(("server.map.region.", "server.map.zone.", "server.portals."))}
    return {"v": CATALOG_VERSION, "entries": out, "regions": regions}


def _time_label(ranges):
    if not ranges:
        return None
    hours = set()
    for a, b in ranges:
        a, b = int(a) % 24, int(b)
        if b >= 24 or (a == 0 and b >= 23):
            return "any"
        h = a
        for _ in range(25):
            hours.add(h)
            if h == b % 24:
                break
            h = (h + 1) % 24
    day = any(6 <= h <= 18 for h in hours)
    night = any(h >= 20 or h <= 4 for h in hours)
    return "any" if day and night else "day" if day else "night" if night else None


class Memories:
    def __init__(self, core):
        self.core = core
        self._catalog = None
        self._building = False
        self._lock = threading.Lock()
        self.error = None

    def _cache_path(self):
        return os.path.join(self.core.data_dir, "memories-catalog.json")

    def catalog(self, wait=False):
        """The parsed catalog; built in the background the first time (and when Assets.zip changes)."""
        from . import hytale
        lay = hytale.locate(self.core.settings["server_dir"])
        assets = self.core.settings.get("assets") or lay.get("assets")
        if not assets or not os.path.isfile(assets):
            self.error = "Assets.zip was not found, so the list of memories isn't available."
            return None
        st = os.stat(assets)
        stamp = f"{st.st_size}:{int(st.st_mtime)}:{CATALOG_VERSION}"
        if self._catalog and self._catalog.get("stamp") == stamp:
            return self._catalog
        try:
            with open(self._cache_path(), encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("stamp") == stamp:
                self._catalog = cached
                return cached
        except (OSError, ValueError):
            pass
        if not self._building:
            self._building = True
            t = threading.Thread(target=self._build, args=(assets, stamp), name="memories", daemon=True)
            t.start()
            if wait:
                t.join()
                return self._catalog
        return None

    def _build(self, assets, stamp):
        try:
            started = time.time()
            cat = build_catalog(assets)
            cat["stamp"] = stamp
            cat["built"] = time.time()
            tmp = self._cache_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cat, f)
            os.replace(tmp, self._cache_path())
            self._catalog = cat
            self.error = None
            self.core.log_app(f"Read {len(cat['entries'])} memories from Assets.zip in {time.time() - started:.1f} s.")
            self.core.hub.publish("memories", {"ready": True})
        except Exception as e:  # noqa: BLE001
            self.error = f"Could not read memories from Assets.zip: {e}"
        finally:
            self._building = False

    # ---------------------------------------------------------------------------- saves
    def restored(self, sd):
        for rel in (("universe", "resources", "Memories.json"), ("universe", "memories.json")):
            p = os.path.join(sd, *rel)
            try:
                with open(p, encoding="utf-8-sig") as f:
                    data = json.load(f)
                items = data.get("Memories") if isinstance(data, dict) else data
                return {m.get("NPCRole") for m in items or [] if isinstance(m, dict) and m.get("NPCRole")}, p
            except (OSError, ValueError):
                continue
        return set(), None

    def carried(self, sd):
        """{entry id: [{name, uuid, when, where_key}]} from the player files."""
        out = {}
        base = os.path.join(sd, "universe", "players")
        names = self.core.players.names()
        try:
            files = os.listdir(base)
        except OSError:
            return out
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(base, fn), encoding="utf-8-sig") as f:
                    comps = (json.load(f) or {}).get("Components") or {}
            except (OSError, ValueError):
                continue
            pm = comps.get("PlayerMemories") or {}
            uuid = fn[:-5].lower()
            np = comps.get("Nameplate") or {}
            pname = names.get(uuid) or (np.get("Text") if isinstance(np, dict) else None) or uuid[:8]
            for m in pm.get("Memories") or []:
                if not isinstance(m, dict) or not m.get("NPCRole"):
                    continue
                out.setdefault(m["NPCRole"], []).append({
                    "name": pname, "uuid": uuid, "when": (m.get("CapturedTimestamp") or 0) / 1000 or None,
                    "where_key": m.get("FoundLocationNameKey") or m.get("FoundLocationZoneNameKey")})
        return out

    def view(self):
        sd = self.core.server_dir()
        if not sd:
            return {"ready": False, "error": "Set the server folder first."}
        cat = self.catalog()
        restored, restored_file = self.restored(sd)
        carried = self.carried(sd)
        if not cat:
            return {"ready": False, "building": self._building, "error": self.error,
                    "restored_count": len(restored), "carried_count": len(carried)}
        regions = cat.get("regions", {})
        rows = []
        for e in cat["entries"]:
            c = carried.get(e["id"], [])
            for x in c:
                x["where"] = regions.get(x["where_key"] or "") or (x["where_key"] or "").rsplit(".", 1)[-1].replace("_", " ") or None
            status = "restored" if e["id"] in restored else "carried" if c else "missing"
            rows.append(dict(e, status=status, carried_by=c))
        known = {e["id"] for e in cat["entries"]}
        totals = {"total": len(rows), "restored": sum(r["status"] == "restored" for r in rows),
                  "carried": sum(r["status"] == "carried" for r in rows),
                  "missing": sum(r["status"] == "missing" for r in rows),
                  "unknown_restored": len(restored - known)}
        players = {}
        for eid, lst in carried.items():
            for x in lst:
                players.setdefault(x["name"], 0)
                players[x["name"]] += 1
        return {"ready": True, "totals": totals, "entries": rows, "carriers": players,
                "restored_file": restored_file, "saved_note": "Player files and the restored list update when the "
                "server saves (every few minutes and when it stops)."}
