"""FaceUnlock camera agent. Runs only in the signed-in user's session.

This process never handles a Windows password. A successful camera check sends
one short-lived signal to the credential provider on the lock desktop.
"""

import ctypes
import os
import sys
import time
import threading
from collections import deque
from pathlib import Path

import numpy as np
from settings import DATA_HOME, GESTURES, PresenceTimer, atomic_json, preview_active
from recognition import calibrate_threshold, MIN_FACE_THRESHOLD

FROZEN = getattr(sys, "frozen", False)
if FROZEN and "--lock-only" not in sys.argv:
    # Installed build: code, models and the enrollment live under Program Files,
    # where this user cannot write. Only config, log and learned samples stay in
    # the user's FaceLock folder, and nothing is imported from there.
    APP_HOME = DATA_HOME
    PROTECTED = Path(sys.executable).resolve().parent.parent / "data"
else:
    APP_HOME = DATA_HOME

import facelock as fl  # noqa: E402

if FROZEN and "--lock-only" not in sys.argv:
    fl.BASE = APP_HOME
    fl.CONFIG = APP_HOME / "config.json"
    fl.LOG = APP_HOME / "facelock.log"
    fl.MODELS = PROTECTED / "models"
    fl.FACE_DB = PROTECTED / "my_face.npy"

from unlock_gestures import UnlockGestureDetector  # noqa: E402

PIPE = r"\\.\pipe\FaceUnlock"
IDLE_POLL_INTERVAL = 0.25
LOCK_CHECK_INTERVAL = 0.75  # tasklist is slow; do not call it for every camera frame
HAND_GESTURES = {"spiderman", "peace", "thumbs_up"}
# A single blink remains disabled: looking down at a phone can resemble one.
MOTION_GESTURES = {"look_up"}
ADAPTIVE_DB = APP_HOME / "my_face_adaptive.npy"
ADAPTIVE_MAX = 40          # rolling samples; the original enrollment is never replaced
ADAPTIVE_ANCHOR_FLOOR = 0.45  # new sample must still resemble the original enrollment
ADAPTIVE_DUPLICATE = 0.93  # skip samples that add nothing new
ADAPTIVE_CONFIRM = 10.0    # seconds for Windows to accept the unlock after the signal
CAMERA_IDLE_SECONDS = 0.5  # release camera shortly after the face disappears
CAMERA_PROBE_SECONDS = 4.0  # periodically look for a face without keyboard or mouse input
CAMERA_PROBE_WINDOW = 1.8  # leave enough time for camera start-up and face detection
CAMERA_HANDOFF_SECONDS = 1.5  # let FaceLock release the camera after Windows locks


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def seconds_since_input():
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def camera_needed(now, input_idle, idle_seconds, camera_open, face_seen_at,
                  camera_opened_at, last_probe_at):
    if idle_seconds <= 0 or input_idle < idle_seconds:
        return True
    if camera_open:
        return (now - face_seen_at < idle_seconds or
                now - camera_opened_at < CAMERA_PROBE_WINDOW)
    return now - last_probe_at >= CAMERA_PROBE_SECONDS


class LockStatePoller:
    def __init__(self, probe=None):
        self.probe = probe or fl.is_session_locked
        self.locked = False
        self.checked_at = 0.0

    def read(self, now=None):
        now = time.monotonic() if now is None else now
        # Poll promptly while unlocked, then reuse the locked result for a
        # short interval so ordinary blinks can be sampled at camera speed.
        if not self.locked or now - self.checked_at >= LOCK_CHECK_INTERVAL:
            self.locked = self.probe()
            self.checked_at = now
        return self.locked


class UnlockDecision:
    """Require a fresh gesture while the enrolled face is continuously present."""

    def __init__(self, enabled_gestures=None):
        self.enabled_gestures = set(GESTURES if enabled_gestures is None else enabled_gestures)
        self.face_since = None
        self.last_gesture = None
        self.gesture_hits = deque()
        self.cooldown_until = 0.0

    def reset(self):
        self.face_since = None
        self.last_gesture = None
        self.gesture_hits.clear()

    def feed(self, *, locked, matched, gesture, now):
        if not locked or not matched:
            self.reset()
            return False
        if self.face_since is None:
            self.face_since = now
        if gesture is not None and gesture not in self.enabled_gestures:
            self.last_gesture = None
            self.gesture_hits.clear()
            return False
        if gesture in {"snap", "double_blink"}:
            if now - self.face_since >= 0.5 and now >= self.cooldown_until:
                self.cooldown_until = now + 12.0
                self.reset()
                return True
            return False
        # The hand detector occasionally misses a frame while a gesture is
        # held. Keep recent sightings, but never carry them across face loss.
        while self.gesture_hits and now - self.gesture_hits[0] > 1.5:
            self.gesture_hits.popleft()
        if gesture is None:
            if self.gesture_hits and now - self.gesture_hits[-1] > 0.8:
                self.last_gesture = None
                self.gesture_hits.clear()
            return False
        if gesture not in HAND_GESTURES | MOTION_GESTURES:
            self.last_gesture = None
            self.gesture_hits.clear()
            return False
        if gesture != self.last_gesture:
            self.last_gesture = gesture
            self.gesture_hits.clear()
        self.gesture_hits.append(now)
        enough = len(self.gesture_hits) >= 3
        enough = enough and now - self.gesture_hits[0] >= (0.30 if gesture in HAND_GESTURES else 0.12)
        if enough and now - self.face_since >= 0.5 and now >= self.cooldown_until:
            self.cooldown_until = now + 12.0
            self.reset()
            return True
        return False


def signal_provider():
    try:
        with open(PIPE, "wb", buffering=0) as pipe:
            pipe.write(b"UNLOCK\n")
        return True
    except OSError:
        return False


def _unit(rows):
    rows = np.asarray(rows, dtype=np.float32).reshape(-1, 128)
    return rows / np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-9)


def load_adaptive(anchors):
    if not ADAPTIVE_DB.exists():
        return np.empty((0, 128), np.float32)
    try:
        data = np.load(ADAPTIVE_DB, allow_pickle=False)
    except (OSError, ValueError):
        data = None
    if data is None or data.ndim != 2 or data.shape[1] != 128 or not np.isfinite(data).all():
        fl.log("FaceUnlock: adaptive face data unreadable; ignoring it")
        return np.empty((0, 128), np.float32)
    data = data[-ADAPTIVE_MAX:].astype(np.float32)
    # The file is user-writable; drop anything that would not have been learned.
    keep = np.max(_unit(data) @ _unit(anchors).T, axis=1) >= ADAPTIVE_ANCHOR_FLOOR
    if not keep.all():
        fl.log(f"FaceUnlock: ignored {int((~keep).sum())} adaptive samples unlike the enrollment")
    return data[keep]


def add_adaptive_sample(anchors, adaptive, embedding):
    """Return the updated adaptive set, or None when the sample is not worth keeping."""
    sample = _unit(embedding)
    anchor_score = float(np.max(_unit(anchors) @ sample.T))
    if anchor_score < ADAPTIVE_ANCHOR_FLOOR:
        return None
    if len(adaptive) and float(np.max(_unit(adaptive) @ sample.T)) >= ADAPTIVE_DUPLICATE:
        return None
    updated = np.vstack([adaptive, np.asarray(embedding, np.float32).reshape(1, 128)])
    return updated[-ADAPTIVE_MAX:]


def save_adaptive(adaptive):
    temp = ADAPTIVE_DB.with_suffix(".tmp")
    with temp.open("wb") as handle:
        np.save(handle, adaptive)
    os.replace(temp, ADAPTIVE_DB)


def run():
    cfg = fl.load_config()
    anchors = np.load(fl.FACE_DB, allow_pickle=False)
    if anchors.ndim != 2 or anchors.shape[1] != 128 or len(anchors) < 1 or not np.isfinite(anchors).all():
        raise ValueError("Invalid enrolled face data")
    # Calibrate on the original enrollment only, so learned samples cannot loosen it.
    face_threshold = calibrate_threshold(anchors)
    adaptive = load_adaptive(anchors)
    known = np.vstack([anchors, adaptive])
    idle_seconds = float(cfg.get("camera_idle_seconds", CAMERA_IDLE_SECONDS))
    pending_sample = None
    face_seen_at = 0.0
    camera_opened_at = 0.0
    last_probe_at = 0.0
    camera_ready_at = 0.0
    camera_paused = False
    face = fl.Face()
    detector = UnlockGestureDetector(cfg["enabled_gestures"])
    decision = UnlockDecision(cfg["enabled_gestures"])
    lock_state = LockStatePoller()
    camera = None
    last_report = 0.0
    was_locked = False
    presence = PresenceTimer()
    refreshed_at = 0.0
    status_at = 0.0
    paused = False
    config_ok = True
    lock_only = "--lock-only" in sys.argv
    hotkey_event = threading.Event()
    if cfg["auto_lock_enabled"]:
        hotkey_event.set()
    threading.Thread(target=fl.hotkey_loop, args=(hotkey_event,), daemon=True).start()
    fl.log(f"FaceUnlock agent started, threshold={face_threshold:.3f} adaptive={len(adaptive)}")
    try:
        while True:
            try:
                now = time.monotonic()
                if now - refreshed_at >= 1:
                    refreshed_at = now
                    try:
                        updated = fl.load_config()
                        config_ok = True
                        if updated != cfg:
                            if camera is not None:
                                camera.release()
                                camera = None
                            cfg = updated
                            idle_seconds = cfg["camera_idle_seconds"]
                            decision.enabled_gestures = set(cfg["enabled_gestures"])
                            detector.enabled_gestures = set(cfg["enabled_gestures"])
                            decision.reset()
                            detector.reset()
                            presence.reset()
                    except (OSError, ValueError, TypeError) as exc:
                        config_ok = False
                        fl.log(f"Settings rejected; monitoring paused: {exc}")
                paused = preview_active(APP_HOME) or not config_ok
                if now - status_at >= 1:
                    atomic_json(APP_HOME / "agent_status.json", {
                        "at": time.time(), "pid": os.getpid(), "paused": paused,
                        "mode": "installed" if FROZEN else "local",
                        "locked": lock_state.locked,
                    })
                    status_at = now
                if paused:
                    if camera is not None:
                        camera.release()
                        camera = None
                    decision.reset()
                    detector.reset()
                    presence.reset()
                    pending_sample = None
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue
                locked = lock_state.read()
                if not locked:
                    if was_locked:
                        fl.log("FaceUnlock: session unlocked")
                        if pending_sample is not None and time.monotonic() <= pending_sample[1]:
                            updated = add_adaptive_sample(anchors, adaptive, pending_sample[0])
                            if updated is not None:
                                save_adaptive(updated)
                                adaptive = updated
                                known = np.vstack([anchors, adaptive])
                                fl.log(f"FaceUnlock: face sample learned ({len(adaptive)}/{ADAPTIVE_MAX})")
                    pending_sample = None
                    was_locked = False
                    decision.reset()
                    detector.reset()
                    if not cfg["auto_lock_enabled"]:
                        presence.reset()
                        if camera is not None:
                            camera.release()
                            camera = None
                        time.sleep(IDLE_POLL_INTERVAL)
                        continue
                    if camera is None:
                        camera = fl.open_camera(cfg["camera_index"])
                    frame = fl.grab(camera)
                    if frame is None:
                        presence.reset()
                        camera.release()
                        camera = None
                        time.sleep(2)
                        continue
                    covered = fl.is_covered(frame, cfg)
                    state, _ = ("covered", 0) if covered else fl.recognize(face, frame, anchors, cfg)
                    if presence.feed(time.monotonic(), state == "me", cfg["away_seconds"], usable=not covered):
                        camera.release()
                        camera = None
                        presence.reset()
                        if not ctypes.windll.user32.LockWorkStation():
                            fl.log("FaceUnlock: Windows rejected the lock request")
                        time.sleep(1)
                    time.sleep(cfg["check_interval"])
                    continue
                now = time.monotonic()
                presence.reset()
                if not was_locked:
                    if camera is not None:
                        camera.release()
                        camera = None
                    fl.log("FaceUnlock: session locked")
                    camera_paused = False
                    face_seen_at = 0.0
                    last_probe_at = 0.0
                    camera_ready_at = now + CAMERA_HANDOFF_SECONDS
                was_locked = True
                if not cfg["unlock_enabled"] or lock_only:
                    if camera is not None:
                        camera.release()
                        camera = None
                    decision.reset()
                    detector.reset()
                    pending_sample = None
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue
                if now < camera_ready_at:
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue
                if idle_seconds > 0:
                    # Briefly probe for a face even without input, so a double
                    # blink can unlock hands-free. Keep the camera on while a
                    # face is visible or while the probe window is still open.
                    if not camera_needed(now, seconds_since_input(), idle_seconds,
                                         camera is not None, face_seen_at,
                                         camera_opened_at, last_probe_at):
                        if not camera_paused:
                            fl.log("FaceUnlock: camera off until input or next face scan")
                            camera_paused = True
                        decision.reset()
                        detector.reset()
                        if camera is not None:
                            camera.release()
                            camera = None
                        time.sleep(IDLE_POLL_INTERVAL)
                        continue
                    if camera_paused:
                        fl.log("FaceUnlock: camera on for face scan")
                        camera_paused = False
                if camera is None:
                    camera = fl.open_camera(cfg["camera_index"])
                    if not camera.isOpened():
                        camera.release()
                        camera = None
                        time.sleep(2.0)
                        continue
                    camera_opened_at = time.monotonic()
                    last_probe_at = camera_opened_at
                # Read every frame here: discarding two buffered frames can
                # skip an ordinary blink entirely.
                frame_ok, frame = camera.read()
                if not frame_ok or frame is None:
                    decision.reset()
                    detector.reset()
                    camera.release()
                    camera = None
                    time.sleep(2.0)
                    continue
                matched = False
                gesture = None
                score = 0.0
                face_count = 0
                embedding = None
                covered = fl.is_covered(frame, cfg)
                if not covered:
                    found = face.faces(frame)
                    face_count = len(found)
                    if found:
                        face_seen_at = time.monotonic()
                    if len(found) == 1:
                        embedding = face.embed(frame, found[0])
                        score = max(face.similarity(embedding, sample.reshape(1, -1)) for sample in known)
                        matched = score >= face_threshold
                        if matched:
                            gesture = detector.detect(frame, found[0][2])
                if not matched:
                    detector.reset()
                now = time.monotonic()
                if now - last_report >= 3.0:
                    fl.log(f"FaceUnlock check: faces={face_count} covered={covered} score={score:.3f} matched={matched} gesture={gesture}")
                    last_report = now
                if decision.feed(locked=True, matched=matched, gesture=gesture, now=now):
                    if signal_provider():
                        fl.log(f"FaceUnlock: face and {gesture} verified; signal sent")
                        # Learn this face only once Windows actually unlocks.
                        pending_sample = (embedding, now + ADAPTIVE_CONFIRM)
                    else:
                        fl.log(f"FaceUnlock: {gesture} verified but provider pipe unavailable")
            except Exception as exc:
                # A camera/model error must fail closed; leave PIN sign-in intact.
                decision.reset()
                detector.reset()
                fl.log(f"FaceUnlock agent error: {type(exc).__name__}: {exc}")
                presence.reset()
                if camera is not None:
                    camera.release()
                    camera = None
                time.sleep(3.0)
    finally:
        if camera is not None:
            camera.release()
        detector.close()


if __name__ == "__main__":
    from windows_runtime import single_instance
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        # Exercise a frozen build's imports/decision without camera or Win32 actions.
        check = UnlockDecision(["peace"])
        for step in range(10):
            if check.feed(locked=True, matched=True, gesture="snap", now=step):
                raise RuntimeError("Disabled gesture accepted")
        atomic_json(Path(sys.argv[2]), {"ok": True, "camera_opened": False, "frozen": FROZEN})
    else:
        with single_instance("Agent") as first:
            if first:
                run()
