"""Demo mode: a throwaway server folder in the official layout, run by tools/mock_server.py.

The demo Assets.zip holds a small, made-up set of creatures in the same file layout the real game
uses (roles, world spawn rules, environments, English names), so the Memories panel works too.
"""
import json
import os
import zipfile

from .settings import APP_DIR, Settings

# id, name, category, zone key, biomes, time (day/night/any), unlock, placed-only
CREATURES = [
    ("Boar", "Boar", "Livestock", "Zone1", ["Forests", "Plains"], "day", "Proximity", False),
    ("Rabbit", "Rabbit", "Critter", "Zone1", ["Plains"], "any", "Proximity", False),
    ("Duck", "Duck", "Avian", "Zone1", ["Plains", "Swamps"], "day", "Proximity", False),
    ("Frog_Green", "Frog", "Reptile", "Zone1", ["Swamps"], "any", "Proximity", False),
    ("Kweebec_Sapling", "Kweebec Sapling", "Kweebec", "Zone1", ["Forests"], "day", "Proximity", False),
    ("Trork_Warrior", "Trork Warrior", "Trork", "Zone1", ["Forests"], "any", "Kill", False),
    ("Goblin_Scrapper", "Goblin Scrapper", "Goblin", "Zone1", ["Caves", "Goblins"], "any", "Kill", False),
    ("Bat", "Bat", "Avian", "Zone1", ["Caves", "Goblins"], "any", "Proximity", False),
    ("Salmon", "Salmon", "Freshwater", "Zone3", ["Forests"], "any", "Proximity", False),
    ("Crocodile", "Crocodile", "Reptile", "Zone2", ["Oasis"], "day", "Proximity", False),
    ("Camel", "Camel", "Livestock", "Zone2", ["Desert"], "day", "Proximity", False),
    ("Scarak_Fighter", "Scarak Fighter", "Scarak", "Zone2", ["Desert"], "any", "Kill", False),
    ("Feran_Hunter", "Feran Hunter", "Feran", "Zone2", ["Savanna"], "any", "Proximity", False),
    ("Yeti", "Yeti", "Mythic", "Zone3", ["Mountains"], "night", "Proximity", False),
    ("Penguin", "Penguin", "Avian", "Zone3", ["Tundra"], "day", "Proximity", False),
    ("Wolf_White", "White Wolf", "Predator", "Zone3", ["Forests"], "any", "Proximity", False),
    ("Skeleton_Burnt", "Burnt Skeleton", "Undead", "Zone4", ["Volcanic"], "night", "Kill", False),
    ("Emberwulf", "Emberwulf", "Elemental", "Zone4", ["Volcanic"], "any", "Proximity", False),
    ("Shark", "Shark", "Abyssal", "Zone0", ["Deep"], "any", "Proximity", False),
    ("Skeleton_Soldier", "Skeleton Soldier", "Undead", None, [], None, "Proximity", True),
    ("Outlander_Cultist", "Outlander Cultist", "Outlander", None, [], None, "Kill", True),
    ("Void_Spawn", "Voidspawn", "Voidspawn", None, [], None, "Kill", True),
]
ZONE_NAMES = {"Zone0": ("Oceans", "Oceans"), "Zone1": ("Emerald_Wilds", "Emerald Wilds"),
              "Zone2": ("Howling_Sands", "Howling Sands"), "Zone3": ("Whisperfrost_Frontiers", "Whisperfrost Frontiers"),
              "Zone4": ("Devastated_Lands", "Devastated Lands")}
TIMES = {"day": [6, 18], "night": [20, 4], "any": [0, 23]}


def write_assets(path):
    lang = ["# demo translations"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "OrbisFox demo assets (made-up creatures in the game's file layout).")
        cats = sorted({c[2] for c in CREATURES})
        for cid, name, cat, zone, biomes, time_, unlock, placed in CREATURES:
            role = {"Type": "Generic", "IsMemory": True, "MemoriesCategory": cat,
                    "NameTranslationKey": f"server.npcRoles.{cid}.name"}
            if unlock == "Kill":
                role["MemoriesUnlockedBy"] = "Kill"
            z.writestr(f"Server/NPC/Roles/{cat}/{cid}.json", json.dumps(role, indent=2))
            lang.append(f"npcRoles.{cid}.name = {name}")
            if placed:
                z.writestr(f"Server/NPC/Spawn/Markers/Demo/{cid}.json",
                           json.dumps({"Model": "NPC_Spawn_Marker", "NPCs": [{"Name": cid, "Weight": 1}]}))
            else:
                envs = [f"Env_{zone}_{b}" for b in biomes[:1]] if len(biomes) == 2 and biomes[1] == "Goblins" \
                    else [f"Env_{zone}_{b}" for b in biomes]
                z.writestr(f"Server/NPC/Spawn/World/{zone}/Spawns_{cid}.json", json.dumps(
                    {"Environments": envs, "NPCs": [{"Id": cid, "Weight": 10}], "DayTimeRange": TIMES[time_]}, indent=2))
        envs = set()
        for _, _, _, zone, biomes, _, _, placed in CREATURES:
            if placed:
                continue
            if len(biomes) == 2 and biomes[1] == "Goblins":
                envs.add((zone, biomes[0], tuple(biomes)))
            else:
                envs.update((zone, b, (b,)) for b in biomes)
        for zone, b, tags in envs:
            z.writestr(f"Server/Environments/{zone}/Env_{zone}_{b}.json",
                       json.dumps({"Tags": {zone: list(tags)}, "SpawnDensity": 0.5}, indent=2))
        for c in cats:
            lang.append(f"memories.categories.{c}.title = {c}")
        for key, label in ZONE_NAMES.values():
            lang.append(f"map.zone.{key} = {label}")
        lang += ["map.region.Zone1_Tier1 = Drifting Plains", "map.region.Zone1_Tier2 = Seedling Woods",
                 "map.region.Zone2_Tier1 = Golden Steppes", "map.region.Zone3_Tier1 = Frostmarch Tundra"]
        z.writestr("Server/Languages/en-US/server.lang", "\n".join(lang) + "\n")


def prepare(data_dir):
    """Creates (or refreshes) the demo server folder and settings."""
    game = os.path.join(data_dir, "demo-server")
    sd = os.path.join(game, "Server")
    os.makedirs(os.path.join(sd, "mods"), exist_ok=True)
    jar = os.path.join(sd, "HytaleServer.jar")
    if not os.path.exists(jar):
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nImplementation-Version: 0.7.3\n")
    assets = os.path.join(game, "Assets.zip")
    needs = True
    if os.path.exists(assets):
        try:
            with zipfile.ZipFile(assets) as z:
                needs = "Server/Languages/en-US/server.lang" not in z.namelist()
        except zipfile.BadZipFile:
            needs = True
    if needs:
        write_assets(assets)
    plugin = os.path.join(sd, "mods", "OrbisFoxBridge.jar")
    if not os.path.exists(plugin):
        with zipfile.ZipFile(plugin, "w") as z:
            z.writestr("manifest.json", '{"Name": "OrbisFoxBridge", "Version": "demo"}')
    start = os.path.join(game, "start.bat")
    if not os.path.exists(start):
        with open(start, "w", encoding="utf-8") as f:
            f.write("@echo off\r\ncd Server\r\nset DEFAULT_ARGS=--assets ../Assets.zip --backup --backup-dir backups "
                    "--backup-frequency 30\r\njava -Xms1G -Xmx4G -XX:AOTCache=HytaleServer.aot -jar HytaleServer.jar "
                    "%DEFAULT_ARGS% %*\r\n")
    s = Settings(os.path.join(data_dir, "settings.json"))
    if not s["server_dir"]:
        s.update({"server_dir": sd, "java": os.path.join(APP_DIR, "tools", "mock_server.py"), "auto_start": True,
                  "ram_max_gb": 4, "ram_min_gb": 1, "panel": {"port": 8770}, "map": {"port": 8777},
                  "updates": {"check_every_hours": 0}})
    return sd
