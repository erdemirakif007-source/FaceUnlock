"""Run the real agent loop with fake camera/Windows APIs; never lock this machine."""
from contextlib import ExitStack
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np
import unlock_agent as agent
from settings import validate


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        self.now += 0.1
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def simulate(config, *, locked, gesture="snap", usable=True, paused=False, config_error=False):
    clock = Clock()
    loops = 0
    def read_lock():
        return locked
    def lease(_):
        nonlocal loops
        loops += 1
        if loops > 45:
            raise KeyboardInterrupt
        return paused
    anchors = np.zeros((1, 128), np.float32)
    anchors[0, 0] = 1
    face = Mock()
    face.faces.return_value = [np.array([0, 0, 100, 100])]
    face.embed.return_value = anchors.copy()
    face.similarity.return_value = 1.0
    camera = Mock()
    camera.isOpened.return_value = True
    camera.read.return_value = (True, np.zeros((8, 8, 3), np.uint8))
    detector = Mock()
    detector.detect.return_value = gesture
    signal = Mock(return_value=True)
    lock = Mock(return_value=True)
    read_cfg = Mock(return_value=config)
    if config_error:
        read_cfg.side_effect = [config] + [ValueError("bad config")] * 60
    fake_time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep, time=lambda: clock.now)
    with ExitStack() as stack:
        for target, value in (
            ("time", fake_time), ("signal_provider", signal), ("atomic_json", Mock()),
            ("preview_active", lease), ("seconds_since_input", lambda: 0),
            ("load_adaptive", lambda _: np.empty((0, 128), np.float32)),
            ("LockStatePoller", lambda: types.SimpleNamespace(read=read_lock, locked=locked)),
            ("UnlockGestureDetector", lambda _: detector),
        ):
            stack.enter_context(patch.object(agent, target, value))
        stack.enter_context(patch.object(agent.np, "load", return_value=anchors))
        stack.enter_context(patch.object(agent.threading, "Thread"))
        stack.enter_context(patch.object(agent.ctypes.windll.user32, "LockWorkStation", lock))
        for target, value in (
            ("load_config", read_cfg), ("Face", lambda: face), ("open_camera", lambda _: camera),
            ("grab", lambda _: camera.read()[1] if usable else None),
            ("recognize", lambda *_: ("none", 0)), ("is_covered", lambda *_: False), ("log", Mock()),
        ):
            stack.enter_context(patch.object(agent.fl, target, value))
        try:
            agent.run()
        except KeyboardInterrupt:
            pass
    return signal, lock, detector, camera


class AgentTests(unittest.TestCase):
    def test_selected_gesture_signals_provider(self):
        signal, lock, detector, _ = simulate(validate({"enabled_gestures": ["snap"]}), locked=True)
        self.assertGreater(signal.call_count, 0)
        lock.assert_not_called()
        detector.close.assert_called_once()

    def test_disabled_gesture_and_disabled_unlock_never_signal(self):
        for cfg in ({"enabled_gestures": ["peace"]}, {"unlock_enabled": False}):
            signal, lock, _, _ = simulate(validate(cfg), locked=True)
            signal.assert_not_called()
            lock.assert_not_called()

    def test_auto_lock_only_after_absence(self):
        _, lock, _, _ = simulate(validate({"away_seconds": 5}), locked=False)
        self.assertGreater(lock.call_count, 0)

    def test_disabled_auto_lock_and_camera_failure_never_lock(self):
        for cfg, usable in (({"auto_lock_enabled": False}, True), ({}, False)):
            signal, lock, _, _ = simulate(validate(cfg), locked=False, usable=usable)
            signal.assert_not_called()
            lock.assert_not_called()

    def test_preview_and_invalid_settings_block_actions(self):
        for locked in (True, False):
            for kwargs in ({"paused": True}, {"config_error": True}):
                signal, lock, _, _ = simulate(validate({}), locked=locked, **kwargs)
                signal.assert_not_called()
                lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
