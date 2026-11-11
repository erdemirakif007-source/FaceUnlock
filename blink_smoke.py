"""Checks deliberate double blinks, including landmark fallback for glasses."""

from unlock_agent import UnlockDecision
from unlock_gestures import DoubleBlinkTracker


def sample(tracker, now, *, closed=False, weak_scores=False):
    score = (0.40 if closed else 0.30) if weak_scores else (0.85 if closed else 0.10)
    ratio = 0.09 if closed else 0.28
    return tracker.update(score, score, ratio, ratio, now)


def ready(tracker, *, weak_scores=False):
    for now in (0.00, 0.05, 0.10, 0.15):
        assert not sample(tracker, now, weak_scores=weak_scores)


def test_double_blink(weak_scores=False):
    tracker = DoubleBlinkTracker()
    ready(tracker, weak_scores=weak_scores)
    assert not sample(tracker, 0.30, closed=True, weak_scores=weak_scores)
    assert not sample(tracker, 0.42, weak_scores=weak_scores)
    assert not sample(tracker, 0.62, closed=True, weak_scores=weak_scores)
    assert sample(tracker, 0.74, weak_scores=weak_scores)
    assert not sample(tracker, 0.85, weak_scores=weak_scores)


def test_single_and_slow_blinks():
    tracker = DoubleBlinkTracker()
    ready(tracker)
    assert not sample(tracker, 0.30, closed=True)
    assert not sample(tracker, 0.42)
    assert not sample(tracker, 0.70)
    assert not sample(tracker, 1.60, closed=True)
    assert not sample(tracker, 1.72)


def test_quick_blinks():
    tracker = DoubleBlinkTracker()
    ready(tracker)
    assert not sample(tracker, 0.30, closed=True)
    assert not sample(tracker, 0.33)
    assert not sample(tracker, 0.40, closed=True)
    assert sample(tracker, 0.43)


def test_face_loss_resets_sequence():
    tracker = DoubleBlinkTracker()
    ready(tracker)
    assert not sample(tracker, 0.30, closed=True)
    assert not sample(tracker, 0.42)
    tracker.reset()
    assert not sample(tracker, 0.62, closed=True)
    assert not sample(tracker, 0.74)


def test_decision_requires_matched_face():
    decision = UnlockDecision()
    assert not decision.feed(locked=True, matched=True, gesture=None, now=0.0)
    assert not decision.feed(locked=True, matched=False, gesture="double_blink", now=0.6)
    assert not decision.feed(locked=True, matched=True, gesture="double_blink", now=0.7)
    assert decision.feed(locked=True, matched=True, gesture="double_blink", now=1.3)
    assert not decision.feed(locked=True, matched=True, gesture="double_blink", now=1.4)


if __name__ == "__main__":
    test_double_blink()
    test_double_blink(weak_scores=True)
    test_single_and_slow_blinks()
    test_quick_blinks()
    test_face_loss_resets_sequence()
    test_decision_requires_matched_face()
    print("blink smoke passed")
