"""Gesture detection for FaceUnlock, including snap and double blink."""

import math
import time
from collections import deque

import cv2
import mediapipe as mp

from gestures import GestureDetector, _dist, _hand_size, hand_gesture
from settings import GESTURES


class SnapTracker:
    """Recognize thumb and middle finger closing, then separating."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.armed_at = None
        self.arm_wrist = None
        self.min_gap = 1.0

    def update(self, gap, wrist, now):
        if self.armed_at is not None and now - self.armed_at > 1.2:
            self.reset()
        if gap <= 0.38:
            if self.armed_at is None:
                self.armed_at = now
                self.arm_wrist = wrist
            self.min_gap = min(self.min_gap, gap)
            return False
        if self.armed_at is None:
            return False
        moved = math.hypot(wrist[0] - self.arm_wrist[0], wrist[1] - self.arm_wrist[1])
        if moved > 0.25:
            self.reset()
            return False
        if gap >= 0.68 and gap - self.min_gap >= 0.30 and moved <= 0.25:
            self.reset()
            return True
        return False


def _eye_ratio(landmarks, corners, lids):
    width = _dist(landmarks[corners[0]], landmarks[corners[1]])
    return (_dist(landmarks[lids[0]], landmarks[lids[1]]) +
            _dist(landmarks[lids[2]], landmarks[lids[3]])) / max(2 * width, 1e-6)


class DoubleBlinkTracker:
    """Require two separate closed-open cycles after an initial open frame."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.left_ratios = deque(maxlen=30)
        self.right_ratios = deque(maxlen=30)
        self.phase = "waiting_open"
        self.closed_at = None
        self.first_closed_at = None
        self.opened_at = None

    @staticmethod
    def _upper_ratio(samples):
        if len(samples) < 4:
            return None
        ordered = sorted(samples)
        return ordered[int(0.8 * (len(ordered) - 1))]

    def update(self, left_score, right_score, left_ratio, right_ratio, now):
        # A recent open-eye reference makes the landmark measurement adapt to
        # the user's face and glasses. Blendshapes remain a second signal.
        self.left_ratios.append(left_ratio)
        self.right_ratios.append(right_ratio)
        left_open = self._upper_ratio(self.left_ratios)
        right_open = self._upper_ratio(self.right_ratios)
        geometry_ready = (left_open is not None and right_open is not None and
                          left_open > 0.16 and right_open > 0.16)
        geometry_closed = (geometry_ready and left_ratio < left_open * 0.66 and
                           right_ratio < right_open * 0.66)
        geometry_open = (geometry_ready and left_ratio > left_open * 0.82 and
                         right_ratio > right_open * 0.82)
        closed = geometry_closed or (left_score > 0.60 and right_score > 0.60)
        opened = not closed and (geometry_open or
                                 (left_score < 0.25 and right_score < 0.25))

        if self.phase == "waiting_open":
            if opened:
                self.phase = "ready"
        elif self.phase == "ready":
            if closed:
                self.phase = "first_closed"
                self.closed_at = self.first_closed_at = now
        elif self.phase == "first_closed":
            if now - self.closed_at > 0.55:
                self.phase = "waiting_open"
            elif opened:
                self.phase = "between"
                self.opened_at = now
        elif self.phase == "between":
            if now - self.opened_at > 1.1:
                self.phase = "ready"
            elif closed:
                if now - self.opened_at >= 0.05:
                    self.phase = "second_closed"
                    self.closed_at = now
                else:
                    self.phase = "ready"
        elif self.phase == "second_closed":
            if now - self.closed_at > 0.55:
                self.phase = "waiting_open"
            elif opened:
                complete = now - self.first_closed_at <= 2.2
                self.phase = "waiting_open" if complete else "ready"
                return complete
        return False


class UnlockGestureDetector(GestureDetector):
    def __init__(self, enabled_gestures=None):
        super().__init__()
        self.enabled_gestures = set(GESTURES if enabled_gestures is None else enabled_gestures)
        self.snap = SnapTracker()
        self.blink = DoubleBlinkTracker()
        self.frame_count = 0

    def reset(self):
        self.snap.reset()
        self.blink.reset()

    def detect(self, frame, face_width_px):
        """Return a gesture for a frame whose single face already matched."""
        self.frame_count += 1
        now = time.monotonic()
        self.ts = max(self.ts + 1, int(now * 1000))
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        face = self.face.detect_for_video(image, self.ts)
        shapes = None
        if not face.face_landmarks:
            self.blink.reset()
        else:
            shapes = {item.category_name: item.score for item in face.face_blendshapes[0]}
            landmarks = face.face_landmarks[0]
            left_ratio = _eye_ratio(landmarks, (33, 133), (159, 145, 158, 153))
            right_ratio = _eye_ratio(landmarks, (362, 263), (386, 374, 385, 380))
            if self.blink.update(shapes["eyeBlinkLeft"], shapes["eyeBlinkRight"],
                                 left_ratio, right_ratio, now):
                if "double_blink" in self.enabled_gestures:
                    return "double_blink"
        # During a blink sequence, spend the frame budget on the eyes. The
        # hand model is still used for the existing hand gestures otherwise.
        if "double_blink" in self.enabled_gestures and self.blink.phase in {"first_closed", "between", "second_closed"}:
            return None
        hand = self.hands.detect_for_video(image, self.ts) if self.frame_count % 2 == 0 else None
        if hand is not None and hand.hand_landmarks:
            hand_landmarks = hand.hand_landmarks[0]
            size = _hand_size(hand_landmarks)
            face_width = face_width_px / frame.shape[1]
            relative_size = size / max(face_width, 1e-6)
            if size > 1e-6 and 0.25 < relative_size < 2.0:
                gap = _dist(hand_landmarks[4], hand_landmarks[12]) / size
                wrist = (hand_landmarks[0].x, hand_landmarks[0].y)
                if self.snap.update(gap, wrist, now):
                    if "snap" in self.enabled_gestures:
                        return "snap"
                if "snap" in self.enabled_gestures and self.snap.armed_at is not None:
                    return None
                gesture = hand_gesture(hand_landmarks)
                if gesture in self.enabled_gestures:
                    return gesture
        if ("look_up" in self.enabled_gestures and shapes is not None and shapes["eyeLookUpLeft"] > 0.45 and
                shapes["eyeLookUpRight"] > 0.45):
            return "look_up"
        return None
