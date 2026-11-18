"""Unit tests for the parts of OrbisFox that do not need a running server.  python -m unittest"""
import collections
import json
import os
import tempfile
import time
import types
import unittest
import zipfile

from orbisfox import hytale, jvm
from orbisfox.backups import Backups
from orbisfox.settings import Settings
from orbisfox.watchdog import Watchdog


class LogParsing(unittest.TestCase):
    def test_real_line_format(self):
        text, lvl, logger, msg = hytale.parse_line(
            "\x1b[32m[2026/10/27 21:12:06   INFO]           [HytaleServer] Starting HytaleServer\x1b[0m")
        self.assertEqual((lvl, logger, msg), ("INFO", "HytaleServer", "Starting HytaleServer"))

    def test_join_and_leave(self):
        _, lvl, lg, msg = hytale.parse_line(
            "[2026/10/27 21:13:00   INFO] [Universe|P] Adding player 'Some_One (0a1b2c3d-1111-2222-3333-444455556666)")
        self.assertEqual(hytale.classify(lvl, lg, msg), "join")
        m = hytale.JOIN_RE.search(msg)
        self.assertEqual(m.group(1), "Some_One")
        _, lvl, lg, msg = hytale.parse_line(
            "[2026/10/27 21:20:00   INFO] [Universe|P] Removing player 'Some_One' (0a1b2c3d-1111-2222-3333-444455556666)")
        self.assertEqual(hytale.classify(lvl, lg, msg), "leave")

    def test_stall_signal(self):
        _, lvl, lg, msg = hytale.parse_line(
            "[2026/01/25 21:16:03 SEVERE] [World|default] Took too long to run pre-load process hook for chunk: "
            "2sec 63ms 195us 733ns > TICK_STEP")
        self.assertEqual(hytale.classify(lvl, lg, msg), "error")
        self.assertEqual(hytale.stall_signal(msg, lg), ("tick_overrun", 2063, "default"))

    def test_jline_prompt_stripped(self):
        _, lvl, _, msg = hytale.parse_line("> [2026/10/27 21:12:06   WARN] [X] hello")
        self.assertEqual((lvl, msg), ("WARN", "hello"))


class Commands(unittest.TestCase):
    def test_command_text(self):
        self.assertEqual(hytale.command_text('Say "hi" [now] --force'), "Say ”hi” (now) –force")
        self.assertEqual(hytale.command_text("{json}"), "(json}")
        self.assertEqual(hytale.command_text("a,, b\\c"), "a, b/c")

    def test_stdin_escape(self):
        self.assertEqual(hytale.stdin_escape("say hi!"), "say hi\\!")

    def test_player_arg(self):
        self.assertEqual(hytale.player_arg("Fox_123"), "Fox_123")
        with self.assertRaises(ValueError):
            hytale.player_arg("bad name; stop")


class Versions(unittest.TestCase):
    def test_ordering(self):
        self.assertTrue(hytale.is_newer("0.7.7", "0.7.3"))
        self.assertTrue(hytale.is_newer("0.8.0", "0.8.0-pre.5"))
        self.assertTrue(hytale.is_newer("0.8.0-pre.5", "0.7.7"))
        self.assertTrue(hytale.is_newer("0.7.7", "2026.01.22-6f8bdbdc4"))
        self.assertFalse(hytale.is_newer("0.7.3", "0.7.3"))


class GcLog(unittest.TestCase):
    def test_g1_lines(self):
        g = jvm.GcMonitor()
        g.max_heap_mb = 4096
        now = time.time()
        g.feed("[2026-11-18T01:36:20.000-0600][13.0s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) "
               "2865M->882M(4096M) 7.754ms", now - 10)
        g.feed("[..][info][gc] GC(1) Pause Full (G1 Compaction Pause) 3.9G->3800M(4G) 2400.0ms", now - 5)
        s = g.summary(60, now)
        self.assertEqual(s["collections"], 2)
        self.assertEqual(s["full_gcs"], 1)
        self.assertAlmostEqual(s["live_percent"], 100 * 882 / 4096, places=1)
        self.assertAlmostEqual(s["gc_overhead_percent"], 100 * 2407.754 / 60000, places=1)


def fake_core(tmp, server_dir):
    s = Settings(os.path.join(tmp, "settings.json"))
    s.update({"server_dir": server_dir})
    core = types.SimpleNamespace(settings=s, data_dir=tmp, published=[], logs=[])
    core.server_dir = lambda: server_dir
    core.publish_backups = lambda: core.published.append(1)
    core.publish_watchdog = lambda: None
    core.log_app = lambda msg, level="info": core.logs.append(msg)
    core.toast = lambda *a, **k: None
    core.server = types.SimpleNamespace(alive=False, running=False, flush_world=lambda: True, pending=None)
    core.updates = types.SimpleNamespace(installed_version=lambda: "0.7.3")
    return core


class BackupRoundTrip(unittest.TestCase):
    def test_create_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = os.path.join(tmp, "Server")
            os.makedirs(os.path.join(sd, "universe", "worlds", "default"))
            with open(os.path.join(sd, "universe", "worlds", "default", "chunk.bin"), "wb") as f:
                f.write(b"original")
            with open(os.path.join(sd, "config.json"), "w") as f:
                json.dump({"ServerName": "A"}, f)
            core = fake_core(tmp, sd)
            b = Backups(core)
            entry = b.create("manual", wait_for_save=False)
            self.assertTrue(entry and entry["size"] > 0)
            with zipfile.ZipFile(os.path.join(b.dir, entry["name"])) as z:
                self.assertIn("universe/worlds/default/chunk.bin", z.namelist())
                self.assertIn("config.json", z.namelist())
            with open(os.path.join(sd, "universe", "worlds", "default", "chunk.bin"), "wb") as f:
                f.write(b"changed")
            time.sleep(1.1)   # backup names have one-second resolution
            b.restore(entry["name"])
            with open(os.path.join(sd, "universe", "worlds", "default", "chunk.bin"), "rb") as f:
                self.assertEqual(f.read(), b"original")
            # the world as it was before the restore was kept as a safety backup
            self.assertTrue(any(x["reason"] == "before-restore" for x in b.list()))

    def test_restore_refuses_running_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = os.path.join(tmp, "Server")
            os.makedirs(os.path.join(sd, "universe"))
            core = fake_core(tmp, sd)
            core.server.alive = True
            with self.assertRaises(RuntimeError):
                Backups(core).restore("orbisfox-20260101-000000-manual.zip")


class WatchdogEscalation(unittest.TestCase):
    def make(self, live_mb, pauses_ms):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        core = fake_core(tmp, tmp)
        core.gc = jvm.GcMonitor()
        core.gc.max_heap_mb = 4096
        now = time.time()
        for i, p in enumerate(pauses_ms):
            core.gc.feed(f"GC({i}) Pause Young (Normal) (G1 Evacuation Pause) 4000M->{live_mb}M(4096M) {p}ms",
                         now - 100 + i)
        core.bridge = types.SimpleNamespace(connected=False, last=None)
        wd = Watchdog.__new__(Watchdog)   # skip __init__: no background thread
        wd.core, wd.stalls, wd.events = core, collections.deque(), collections.deque()
        return wd, now

    def test_healthy(self):
        wd, now = self.make(1500, [10] * 20)
        level, _, _ = wd.evaluate(now)
        self.assertEqual(level, "ok")

    def test_full_heap_is_critical(self):
        wd, now = self.make(3900, [300] * 20)
        level, reasons, _ = wd.evaluate(now)
        self.assertEqual(level, "critical")
        self.assertTrue(any("heap" in r for r in reasons))

    def test_old_stalls_do_not_keep_it_critical(self):
        wd, now = self.make(1500, [10] * 20)
        for i in range(12):
            wd.stalls.append((now - 100 + i, "tick_overrun", 2000, "default"))
        level, _, _ = wd.evaluate(now)
        self.assertEqual(level, "strained")
        for i in range(12):
            wd.stalls.append((now - 20 + i, "tick_overrun", 2000, "default"))
        level, _, _ = wd.evaluate(now)
        self.assertEqual(level, "critical")


if __name__ == "__main__":
    unittest.main()
