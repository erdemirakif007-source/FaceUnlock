"""Behavior checks for noisy gesture detections and face-loss safety."""

from unlock_agent import UnlockDecision


def feed(decision, now, gesture=None, matched=True):
    return decision.feed(locked=True, matched=matched, gesture=gesture, now=now)


def test_interrupted_hand_gesture():
    decision = UnlockDecision()
    assert not feed(decision, 0.0)
    assert not feed(decision, 0.2, "peace")
    assert not feed(decision, 0.4)
    assert not feed(decision, 0.6, "peace")
    assert not feed(decision, 0.8)
    assert feed(decision, 1.0, "peace")
    assert not feed(decision, 1.1, "peace")


def test_face_loss_resets():
    decision = UnlockDecision()
    assert not feed(decision, 0.0, "peace")
    assert not feed(decision, 0.4, "peace")
    assert not feed(decision, 0.5, matched=False)
    assert not feed(decision, 1.0, "peace")
    assert not feed(decision, 1.4, "peace")


def test_gesture_change_resets():
    decision = UnlockDecision()
    assert not feed(decision, 0.0, "peace")
    assert not feed(decision, 0.4, "peace")
    assert not feed(decision, 0.7, "thumbs_up")
    assert not feed(decision, 1.0, "peace")


def test_removed_motions_never_unlock():
    decision = UnlockDecision()
    for gesture in ("blink", "turn_left", "turn_right"):
        for step in range(10):
            assert not feed(decision, step * 0.1, gesture), gesture


def test_snap_needs_steady_face():
    decision = UnlockDecision()
    assert not feed(decision, 0.0, "snap")
    assert not feed(decision, 0.3, "snap")
    assert feed(decision, 0.6, "snap")


if __name__ == "__main__":
    test_snap_needs_steady_face()
    test_interrupted_hand_gesture()
    test_face_loss_resets()
    test_gesture_change_resets()
    test_removed_motions_never_unlock()
    print("decision smoke passed")
