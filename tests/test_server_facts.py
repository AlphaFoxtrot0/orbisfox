"""Tests against what the real Hytale 0.7.7 server prints and stores.  python -m unittest"""
import os
import shutil
import tempfile
import time
import unittest

from orbisfox import hytale

UUID = "20faf85e-088b-4a87-a9eb-22ba12be89cb"


class JoinLeaveLines(unittest.TestCase):
    def test_only_the_universe_lines_count(self):
        join = f"[2026/11/06 00:03:03   INFO]  [Universe|P] Adding player 'AlphaFoxtrot ({UUID})"
        world_join = f"[2026/11/06 00:03:03   INFO]  [World|default] Adding player 'AlphaFoxtrot' to world 'default' at location null ({UUID})"
        world_leave = f"[2026/11/06 00:03:38   INFO]  [PlayerSystems] Removing player 'AlphaFoxtrot (AlphaFoxtrot)' from world 'default' ({UUID})"
        leave = f"[2026/11/06 00:03:38   INFO]  [Universe|P] Removing player 'AlphaFoxtrot' ({UUID})"
        self.assertEqual(hytale.JOIN_RE.search(join).groups(), ("AlphaFoxtrot", UUID))
        self.assertIsNone(hytale.JOIN_RE.search(world_join))
        self.assertIsNone(hytale.LEAVE_RE.search(world_leave))
        self.assertEqual(hytale.LEAVE_RE.search(leave).groups(), ("AlphaFoxtrot", UUID))


class LogHistory(unittest.TestCase):
    def test_sessions_and_playtime_before_a_cutoff(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        logs = os.path.join(tmp, "logs")
        os.makedirs(logs)
        other = "11111111-2222-3333-4444-555555555555"
        with open(os.path.join(logs, "2026-04-09_18-49-47_server.log"), "w", encoding="utf-8") as f:
            f.write(f"[2026/04/09 18:50:00   INFO]  [Universe|P] Adding player 'AlphaFoxtrot ({UUID})\n"
                    f"[2026/04/09 18:51:00   INFO]  [Universe|P] Adding player 'Tasher ({other})\n"
                    f"[2026/04/09 19:50:00   INFO]  [PlayerSystems] Removing player 'AlphaFoxtrot (AlphaFoxtrot)' from world 'default' ({UUID})\n"
                    f"[2026/04/09 19:50:00   INFO]  [Universe|P] Removing player 'AlphaFoxtrot' ({UUID})\n"
                    "[2026/04/09 20:21:00   INFO]  [HytaleServer] Shutting down\n")   # Tasher still on: 90 min
        with open(os.path.join(logs, "2026-04-10_10-00-00_server.log"), "w", encoding="utf-8") as f:
            f.write(f"[2026/04/10 10:00:00   INFO]  [Universe|P] Adding player 'AlphaFoxtrot ({UUID})\n"
                    f"[2026/04/10 12:00:00   INFO]  [Universe|P] Removing player 'AlphaFoxtrot' ({UUID})\n")
        with open(os.path.join(logs, "orbisfox-gc.log"), "w") as f:
            f.write("not a server log\n")
        cutoff = time.mktime((2026, 4, 10, 11, 0, 0, 0, 0, -1))   # OrbisFox took over at 11:00
        h = hytale.read_log_sessions(tmp, cutoff)
        self.assertEqual(h[UUID]["name"], "AlphaFoxtrot")
        self.assertEqual(h[UUID]["sessions"], 2)
        self.assertAlmostEqual(h[UUID]["playtime"], 3600 + 3600, delta=1)   # 1 h + 1 h up to the cutoff
        self.assertEqual(h[other]["sessions"], 1)
        self.assertAlmostEqual(h[other]["playtime"], 90 * 60, delta=1)


class ServerReplies(unittest.TestCase):
    def test_whitelist_state(self):
        for line, state in (("Whitelist is currently enabled!", "enabled"), ("Whitelist is already enabled!", "enabled"),
                            ("Whitelist disabled!", "disabled"), ("Whitelist enabled!", "enabled")):
            self.assertEqual(hytale.WHITELIST_STATE_RE.search(line).group(1), state)
        self.assertIsNone(hytale.WHITELIST_STATE_RE.search("Added Feran_Fox to the whitelist!"))

    def test_slow_tick_line_is_shortened_and_still_counted(self):
        msg = ("Took too long to run pre-load process hook for section: 63ms 247us 900ns > TICK_STEP, Has GC Run: true, "
               "Holder{archetype=Archetype{componentTypes=[" + "null, " * 60 + "]}}, Hook: "
               "com.hypixel.hytale.builtin.adventure.farming.FarmingPlugin$$Lambda/0x00000000b8fee5f8@63ff69e0")
        short = hytale.condense("SEVERE", msg)
        self.assertIn("63 ms", short)
        self.assertIn("FarmingPlugin", short)
        self.assertLess(len(short), 200)
        self.assertEqual(hytale.stall_signal(msg, "World|default")[:2], ("tick_overrun", 63))
        self.assertIsNone(hytale.condense("INFO", "Saved 1 world."))

    def test_player_arguments_accept_uuids(self):
        self.assertEqual(hytale.player_arg(UUID.upper()), UUID)
        self.assertEqual(hytale.player_arg("Feran_Fox"), "Feran_Fox")
        with self.assertRaises(ValueError):
            hytale.player_arg("two words")


class ConfigEdits(unittest.TestCase):
    def test_checked_and_written(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        hytale.write_json(os.path.join(tmp, "config.json"), {"ServerName": "Old", "Defaults": {"World": "default"}})
        ch = hytale.config_changes({"ServerName": "New", "MaxPlayers": "5000", "GameMode": "Creative"})
        self.assertEqual(ch["MaxPlayers"], 1000)
        cfg = hytale.update_config(tmp, ch)
        self.assertEqual((cfg["ServerName"], cfg["Defaults"]["GameMode"], cfg["Defaults"]["World"]), ("New", "Creative", "default"))
        with self.assertRaises(ValueError):
            hytale.config_changes({"Modules": {}})
        with self.assertRaises(ValueError):
            hytale.config_changes({"GameMode": "Hardcore"})


if __name__ == "__main__":
    unittest.main()
