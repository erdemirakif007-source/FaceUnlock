"""Checks for a fresh visual snap and immediate unlock decision."""

from unlock_agent import UnlockDecision
from unlock_gestures import SnapTracker


def check_snap_sequence():
    tracker = SnapTracker()
    wrist = (0.5, 0.5)
    assert not tracker.update(0.82, wrist, 0.0)
    assert not tracker.update(0.30, wrist, 0.1)
    assert tracker.update(0.75, wrist, 0.3)
    assert not tracker.update(0.80, wrist, 0.4)


def check_timeout_and_hand_switch():
    tracker = SnapTracker()
    wrist = (0.5, 0.5)
    assert not tracker.update(0.30, wrist, 0.0)
    assert not tracker.update(0.80, wrist, 1.3)
    assert not tracker.update(0.30, wrist, 2.0)
    assert not tracker.update(0.80, (0.9, 0.9), 2.2)


def check_immediate_decision():
    decision = UnlockDecision()
    assert not decision.feed(locked=True, matched=False, gesture="snap", now=0.0)
    assert not decision.feed(locked=True, matched=True, gesture=None, now=0.1)
    assert decision.feed(locked=True, matched=True, gesture="snap", now=0.6)
    assert not decision.feed(locked=True, matched=True, gesture="snap", now=0.7)


if __name__ == "__main__":
    check_snap_sequence()
    check_timeout_and_hand_switch()
    check_immediate_decision()
    print("snap smoke passed")
