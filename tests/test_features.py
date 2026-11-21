"""Tests for importing an existing server, Memories and operator vetoes.  python -m unittest"""
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
import zipfile

from orbisfox import demo, importer
from orbisfox.memories import Memories, build_catalog
from orbisfox.settings import Settings
from orbisfox.veto import Vetoes


def make_server(tmp, script=None, jvm_options=None, config=None):
    game = os.path.join(tmp, "game")
    sd = os.path.join(game, "Server")
    os.makedirs(sd)
    with zipfile.ZipFile(os.path.join(sd, "HytaleServer.jar"), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Implementation-Version: 0.7.7\n")
    with zipfile.ZipFile(os.path.join(game, "Assets.zip"), "w") as z:
        z.writestr("x", "y")
    if script:
        with open(os.path.join(game, "start.bat"), "w") as f:
            f.write(script)
    if jvm_options:
        with open(os.path.join(game, "jvm.options"), "w") as f:
            f.write(jvm_options)
    if config is not None:
        with open(os.path.join(sd, "config.json"), "w") as f:
            json.dump(config, f)
    return sd


OFFICIAL = """@echo off
if exist ..\\jvm.options set JVM_OPTS=@..\\jvm.options
cd Server
set DEFAULT_ARGS=--assets ../Assets.zip --backup --backup-dir backups --backup-frequency 30
java %JVM_OPTS% -XX:AOTCache=HytaleServer.aot -jar HytaleServer.jar %DEFAULT_ARGS% %*
"""


class ImportExisting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_official_layout_with_jvm_options(self):
        sd = make_server(self.tmp, OFFICIAL, "# memory\n-Xms2G\n-Xmx8G\n-XX:+UseZGC\n",
                         {"MaxViewRadius": 12, "Update": {"Patchline": "pre-release", "AutoApplyMode": "WhenEmpty"}})
        ch, notes = importer.analyse(sd)
        self.assertEqual(ch["ram_max_gb"], 8)
        self.assertEqual(ch["ram_min_gb"], 2)
        self.assertTrue(ch["aot_cache"])
        self.assertEqual(ch["backups"], {"hytale_builtin": True, "hytale_dir": "backups", "hytale_frequency": 30})
        self.assertEqual(ch["updates"], {"patchline": "pre-release", "auto_install": "empty"})
        self.assertEqual(ch["server_args"], "")
        self.assertTrue(notes)

    def test_custom_script_flags(self):
        script = ('@echo off\ncd Server\n"C:\\Java\\bin\\java.exe" -Xmx6G -Xms6G -XX:+UseG1GC -Dfoo=bar '
                  '-jar HytaleServer.jar --assets ../Assets.zip --bind 0.0.0.0:5521 --auth-mode offline --disable-sentry\n')
        sd = make_server(self.tmp, script, config={})
        ch, _ = importer.analyse(sd)
        self.assertEqual(ch["ram_max_gb"], 6)
        self.assertEqual(ch["bind"], "0.0.0.0:5521")
        self.assertIn("-XX:+UseG1GC", ch["jvm_args"])
        self.assertIn("-Dfoo=bar", ch["jvm_args"])
        self.assertEqual(ch["server_args"], "--auth-mode offline --disable-sentry")
        self.assertFalse(ch["backups"]["hytale_builtin"])

    def test_config_backup_block(self):
        sd = make_server(self.tmp, config={"Backup": {"Enabled": True, "FrequencyMinutes": 15, "Directory": "bk"}})
        ch, _ = importer.analyse(sd)
        self.assertEqual(ch["backups"], {"hytale_builtin": True, "hytale_frequency": 15, "hytale_dir": "bk"})


class MemoriesFromFiles(unittest.TestCase):
    def test_catalog_and_status(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        sd = make_server(tmp, config={})
        assets = os.path.join(tmp, "game", "Assets.zip")
        demo.write_assets(assets)
        cat = build_catalog(assets)
        ids = {e["id"]: e for e in cat["entries"]}
        self.assertEqual(len(ids), len(demo.CREATURES))
        self.assertEqual(ids["Bat"]["where"], [{"zone": "Emerald Wilds", "biomes": ["Caves (Goblins)"]}])
        self.assertEqual(ids["Yeti"]["time"], "night")
        self.assertEqual(ids["Trork_Warrior"]["unlock"], "Kill")
        self.assertTrue(ids["Skeleton_Soldier"]["placed"])
        # restored + carried from the save files
        os.makedirs(os.path.join(sd, "universe", "resources"))
        os.makedirs(os.path.join(sd, "universe", "players"))
        with open(os.path.join(sd, "universe", "resources", "Memories.json"), "w") as f:
            json.dump({"Memories": [{"Id": "NPC", "NPCRole": "Boar"}]}, f)
        with open(os.path.join(sd, "universe", "players", "0a1b2c3d-1111-2222-3333-444455556666.json"), "w") as f:
            json.dump({"Components": {"Nameplate": {"Text": "Kit"}, "PlayerMemories": {"Memories": [
                {"Id": "NPC", "NPCRole": "Yeti", "FoundLocationNameKey": "server.map.region.Zone3_Tier1"}]}}}, f)
        s = Settings(os.path.join(tmp, "s.json"))
        s.update({"server_dir": sd})
        core = types.SimpleNamespace(settings=s, data_dir=tmp, server_dir=lambda: sd,
                                     players=types.SimpleNamespace(names=lambda: {}),
                                     log_app=lambda *a, **k: None, hub=types.SimpleNamespace(publish=lambda *a: None))
        m = Memories(core)
        m.catalog(wait=True)
        v = m.view()
        self.assertTrue(v["ready"])
        st = {e["id"]: e for e in v["entries"]}
        self.assertEqual(st["Boar"]["status"], "restored")
        self.assertEqual(st["Yeti"]["status"], "carried")
        self.assertEqual(st["Yeti"]["carried_by"][0]["where"], "Frostmarch Tundra")
        self.assertEqual(v["totals"]["missing"], len(demo.CREATURES) - 2)


class Vetoing(unittest.TestCase):
    def make(self, online=True):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = Settings(os.path.join(tmp, "s.json"))
        sent = []
        core = types.SimpleNamespace(
            settings=s, players=types.SimpleNamespace(online={"u": {}} if online else {}),
            bridge=types.SimpleNamespace(notify_ops=lambda t: sent.append(t) or 1, connected=True),
            publish_server=lambda: None, log_app=lambda *a, **k: None, toast=lambda *a, **k: None,
            server=types.SimpleNamespace(running=False), on_vetoed=lambda k, l: sent.append(("vetoed", k)))
        return Vetoes(core), sent

    def test_veto_cancels_and_cools_down(self):
        v, sent = self.make()
        cancelled = threading.Event()
        ev = v.begin("watchdog", 60, cancel=cancelled.set)
        self.assertIn("/veto", sent[0])
        self.assertTrue(v.check_line("Feran_Fox executed command: veto"))
        self.assertTrue(ev.is_set() and cancelled.is_set())
        self.assertGreater(v.blocked("watchdog"), 290)
        self.assertEqual(v.blocked("daily"), 0)
        self.assertIn(("vetoed", "watchdog"), sent)
        # the plugin's own report of the same veto is ignored
        self.assertFalse(v.on_veto("Feran_Fox"))

    def test_no_prompt_when_nobody_online(self):
        v, sent = self.make(online=False)
        v.begin("daily", 600, cancel=lambda: None)
        self.assertEqual(sent, [])
        self.assertIsNone(v.active)

    def test_console_is_not_an_operator_veto(self):
        v, _ = self.make()
        v.begin("update", 300, cancel=lambda: None)
        self.assertFalse(v.check_line("Console executed command: veto"))
        self.assertIsNotNone(v.active)


if __name__ == "__main__":
    unittest.main()
