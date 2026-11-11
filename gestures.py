"""Gizli isaret algilama (MediaPipe).

Desteklenen isaretler:
  spiderman, peace, thumbs_up, look_up, blink, turn_left, turn_right
"""
import math

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mpt
from mediapipe.tasks.python import vision

from facelock import MODELS

GESTURES = ["spiderman", "peace", "thumbs_up", "look_up", "blink", "turn_left", "turn_right"]


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def _finger_states(lm):
    """[thumb, index, middle, ring, pinky] -> acik mi."""
    wrist = lm[0]
    states = [_dist(lm[4], lm[17]) > _dist(lm[3], lm[17]) * 1.1]
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        states.append(_dist(lm[tip], wrist) > _dist(lm[pip], wrist) * 1.15)
    return states


def hand_gesture(lm):
    thumb, index, middle, ring, pinky = _finger_states(lm)
    if index and pinky and not middle and not ring and thumb:
        return "spiderman"
    if index and middle and not ring and not pinky:
        return "peace"
    if thumb and not index and not middle and not ring and not pinky and lm[4].y < lm[2].y:
        return "thumbs_up"
    return None


def _hand_size(lm):
    return _dist(lm[0], lm[9])


class GestureDetector:
    def __init__(self):
        self.hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(MODELS / "hand_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO, num_hands=1))
        self.face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(MODELS / "face_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            output_face_blendshapes=True, output_facial_transformation_matrixes=True))
        self.ts = 0

    def close(self):
        self.face.close()
        self.hands.close()

    def detect(self, frame):
        """Karedeki isareti dondurur (veya None) ve yuz boyutunu kontrol eder."""
        self.ts += 33
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        face = self.face.detect_for_video(image, self.ts)
        hand = self.hands.detect_for_video(image, self.ts)
        if not face.face_landmarks:
            return None

        flm = face.face_landmarks[0]
        face_w = abs(flm[234].x - flm[454].x)

        if hand.hand_landmarks:
            hlm = hand.hand_landmarks[0]
            # El yuzle ayni mesafede olmali (fotograf + baskasinin eli onlemi)
            ratio = _hand_size(hlm) / max(face_w, 1e-6)
            if 0.35 < ratio < 1.6:
                g = hand_gesture(hlm)
                if g:
                    return g

        shapes = {c.category_name: c.score for c in face.face_blendshapes[0]}
        if shapes["eyeLookUpLeft"] > 0.45 and shapes["eyeLookUpRight"] > 0.45:
            return "look_up"
        if shapes["eyeBlinkLeft"] > 0.6 and shapes["eyeBlinkRight"] > 0.6:
            return "blink"

        m = face.facial_transformation_matrixes[0]
        yaw = math.degrees(math.atan2(-m[2][0], math.hypot(m[2][1], m[2][2])))
        if yaw > 25:
            return "turn_left"
        if yaw < -25:
            return "turn_right"
        return None


class SequenceMatcher:
    """Isaretlerin belirli sirayla, her biri birkac kare tutularak yapilmasini bekler."""

    def __init__(self, sequence, hold_frames=4, timeout=8.0):
        self.sequence = sequence
        self.hold_frames = hold_frames
        self.timeout = timeout
        self.reset()

    def reset(self):
        self.step = 0
        self.count = 0
        self.started = None

    def feed(self, gesture, now):
        if self.started and now - self.started > self.timeout:
            self.reset()
        if self.step >= len(self.sequence):
            return True
        if gesture == self.sequence[self.step]:
            self.count += 1
            if self.count >= self.hold_frames:
                self.step += 1
                self.count = 0
                self.started = self.started or now
        elif gesture is not None and self.step > 0 and gesture != self.sequence[self.step - 1]:
            # Yanlis isaret -> bastan
            self.reset()
        return self.step >= len(self.sequence)


if __name__ == "__main__":
    import time
    import facelock as fl

    det = GestureDetector()
    cap = fl.open_camera(0)
    print("Isaretleri dene. q = cikis")
    while True:
        frame = fl.grab(cap)
        if frame is None:
            continue
        g = det.detect(frame)
        cv2.putText(frame, str(g), (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.imshow("Isaret testi", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
