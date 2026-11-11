"""Safety regression checks: validation, selected gestures, pause and grace periods."""
import json
from pathlib import Path
import tempfile
import unittest

from settings import PresenceTimer, load_config, save_config, validate
from unlock_agent import UnlockDecision


class SettingsTests(unittest.TestCase):
    def test_round_trip_preserves_legacy_options(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            saved = save_config({"away_seconds": 120, "custom_option": "keep", "enabled_gestures": ["peace"]}, path)
            self.assertEqual(load_config(path), saved)
            self.assertEqual(saved["custom_option"], "keep")
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_invalid_values_never_replace_good_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            save_config({}, path)
            original = path.read_bytes()
            for bad in ({"away_seconds": -1}, {"away_seconds": float("nan")},
                        {"camera_index": True}, {"camera_index": 0.5},
                        {"unlock_enabled": "yes"}, {"enabled_gestures": []},
                        {"enabled_gestures": ["blink"]}, {"enabled_gestures": [None]}):
                with self.assertRaises(ValueError):
                    save_config(bad, path)
                self.assertEqual(path.read_bytes(), original)

    def test_explicitly_disabled_unlock_may_have_no_gestures(self):
        self.assertEqual(validate({"unlock_enabled": False, "enabled_gestures": []})["enabled_gestures"], [])

    def test_legacy_pause_migrates_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text('{"away_seconds": 40}', encoding="utf-8")
            (path.parent / "state.json").write_text('{"enabled": false}', encoding="utf-8")
            self.assertFalse(load_config(path)["auto_lock_enabled"])
            self.assertNotIn("auto_lock_enabled", json.loads(path.read_text()))

    def test_disabled_gestures_cannot_unlock(self):
        for gesture in ("snap", "double_blink", "look_up", "thumbs_up", "spiderman"):
            decision = UnlockDecision(["peace"])
            for step in range(20):
                self.assertFalse(decision.feed(locked=True, matched=True, gesture=gesture, now=step * 0.2))
        decision = UnlockDecision(["peace"])
        results = [decision.feed(locked=True, matched=True, gesture="peace", now=t) for t in (0, 0.3, 0.6)]
        self.assertTrue(results[-1])

    def test_gesture_selection_change_resets_in_agent_contract(self):
        decision = UnlockDecision(["peace"])
        for t in (0, 0.3):
            self.assertFalse(decision.feed(locked=True, matched=True, gesture="peace", now=t))
        decision.enabled_gestures = {"double_blink"}
        decision.reset()
        self.assertFalse(decision.feed(locked=True, matched=True, gesture="peace", now=0.7))
        self.assertTrue(decision.feed(locked=True, matched=True, gesture="double_blink", now=1.3))

    def test_presence_timeout_and_return(self):
        timer = PresenceTimer()
        self.assertFalse(timer.feed(0, True, 30))
        self.assertFalse(timer.feed(29, False, 30))
        self.assertFalse(timer.feed(29.5, True, 30))
        self.assertFalse(timer.feed(59, False, 30))
        self.assertTrue(timer.feed(59.5, False, 30))

    def test_failure_pause_and_resume_get_full_grace_period(self):
        for option in ({"usable": False}, {"enabled": False}):
            timer = PresenceTimer()
            timer.feed(0, True, 5)
            self.assertFalse(timer.feed(10, False, 5, **option))
            self.assertFalse(timer.feed(20, False, 5))
            self.assertFalse(timer.feed(24.9, False, 5))
            self.assertTrue(timer.feed(25, False, 5))


if __name__ == "__main__":
    unittest.main()
