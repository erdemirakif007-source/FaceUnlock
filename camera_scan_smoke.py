"""Check automatic camera probes for hands-free unlock."""

from unlock_agent import LockStatePoller, camera_needed


def needed(now, *, open_camera=False, face_seen_at=0.0, opened_at=0.0,
           last_probe_at=0.0, input_idle=100.0, idle_seconds=0.5):
    return camera_needed(now, input_idle, idle_seconds, open_camera,
                         face_seen_at, opened_at, last_probe_at)


assert needed(100.0)  # first locked check opens without any input
assert needed(101.0, open_camera=True, opened_at=100.0)
assert not needed(102.0, open_camera=True, opened_at=100.0)
assert not needed(102.0, last_probe_at=100.0)
assert needed(104.0, last_probe_at=100.0)  # next hands-free scan
assert needed(110.0, open_camera=True, opened_at=100.0, face_seen_at=109.8)
assert needed(110.0, input_idle=0.1, last_probe_at=109.0)
assert needed(110.0, idle_seconds=0, last_probe_at=109.0)

checks = iter((True, False))
poller = LockStatePoller(lambda: next(checks))
assert poller.read(0.0) is True
assert poller.read(0.4) is True  # cached while locked
assert poller.read(0.75) is False  # unlocked state noticed within 0.75 s

checks = iter((False, True))
poller = LockStatePoller(lambda: next(checks))
assert poller.read(0.0) is False
assert poller.read(0.1) is True  # lock acquisition is still polled promptly
print("camera scan smoke passed")
