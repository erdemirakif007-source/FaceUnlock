"""Collect numeric eye signals for a short local blink diagnostic; no images saved."""

import csv
import statistics
import sys
import time
import winsound
from pathlib import Path

import numpy as np

sys.path.append(str(Path.home() / "FaceLock"))
import facelock as fl  # noqa: E402
from unlock_gestures import UnlockGestureDetector  # noqa: E402


def main():
    output = Path.home() / "AppData/Local/FaceUnlockBuild/blink-probe.csv"
    cfg = fl.load_config()
    known = np.load(fl.FACE_DB, allow_pickle=False)
    adaptive = Path.home() / "FaceLock/my_face_adaptive.npy"
    if adaptive.exists():
        known = np.vstack([known, np.load(adaptive, allow_pickle=False)])
    face = fl.Face()
    detector = UnlockGestureDetector()
    camera = fl.open_camera(cfg["camera_index"])
    if not camera.isOpened():
        raise RuntimeError("Camera could not open")
    rows = []
    started = None
    deadline = time.monotonic() + 10.0
    original_update = detector.blink.update

    def capture(left_score, right_score, left_ratio, right_ratio, now):
        previous = detector.blink.phase
        result = original_update(left_score, right_score, left_ratio, right_ratio, now)
        rows.append((now, left_score, right_score, left_ratio, right_ratio,
                     previous, detector.blink.phase, result))
        return result

    detector.blink.update = capture
    try:
        while time.monotonic() < deadline or (started is not None and time.monotonic() - started < 15):
            ok, frame = camera.read()
            if not ok or frame is None:
                continue
            found = face.faces(frame)
            if len(found) != 1:
                detector.reset()
                continue
            embedding = face.embed(frame, found[0])
            score = max(face.similarity(embedding, sample.reshape(1, -1)) for sample in known)
            if score < 0.625:
                detector.reset()
                continue
            if started is None:
                started = time.monotonic()
                winsound.Beep(800, 160)
                winsound.Beep(800, 160)
            detector.detect(frame, found[0][2])
    finally:
        camera.release()

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("time", "left_score", "right_score", "left_ratio", "right_ratio",
                         "phase_before", "phase_after", "double_blink"))
        writer.writerows(rows)
    if not rows:
        print("No matched face frames; numeric file is empty")
        return
    intervals = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
    print(f"frames={len(rows)} fps={1 / statistics.median(intervals):.1f} "
          f"max_scores=({max(row[1] for row in rows):.2f}, {max(row[2] for row in rows):.2f}) "
          f"min_ratios=({min(row[3] for row in rows):.3f}, {min(row[4] for row in rows):.3f})")
    print("transitions=" + str([(round(row[0] - rows[0][0], 2), row[6])
                                  for row in rows if row[5] != row[6]]))
    print("saved numeric data: " + str(output))


if __name__ == "__main__":
    main()
