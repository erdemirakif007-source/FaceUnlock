"""FaceLock - kamera yuzunu gormezse Windows'u kilitler.

Kullanim:
  python facelock.py enroll   -> yuzunu kaydet (kameraya bak, basini hafif oynat)
  python facelock.py run      -> arka planda izle (pythonw ile calistir)
  python facelock.py test     -> kilitlemeden, pencerede canli durum goster
"""
import ctypes
from ctypes import wintypes
import json
import os
import sys
import threading
import time
from pathlib import Path
import winsound

import cv2
import numpy as np

from settings import DATA_HOME, DEFAULTS, load_config as read_config

BASE = DATA_HOME
MODELS = BASE / "models"
FACE_DB = BASE / "my_face.npy"
CONFIG = BASE / "config.json"
LOG = BASE / "facelock.log"
STATE = BASE / "state.json"
HOTKEY = "Ctrl+Alt+F12"

user32 = ctypes.windll.user32


def load_config():
    return read_config(CONFIG)


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    if sys.stdout is not None:
        print(line, flush=True)
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_enabled():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))["enabled"] is True
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log(f"FaceLock durum dosyasi okunamadi: {exc}; izleme acik")
        return True


def save_enabled(enabled):
    from settings import save_config
    cfg = load_config()
    cfg["auto_lock_enabled"] = enabled
    save_config(cfg, CONFIG)
    temporary = STATE.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps({"enabled": enabled}) + "\n", encoding="utf-8")
        os.replace(temporary, STATE)
    finally:
        temporary.unlink(missing_ok=True)


def hotkey_loop(enabled_event):
    # RegisterHotKey registers on this thread; GetMessage keeps it responsive
    # even when camera reads on the main thread take time.
    hotkey_id = 1
    modifiers = 0x0001 | 0x0002 | 0x4000  # ALT | CONTROL | NOREPEAT
    if not user32.RegisterHotKey(None, hotkey_id, modifiers, 0x7B):  # F12
        log(f"FaceLock kisayolu kaydedilemedi: {HOTKEY}")
        return
    log(f"FaceLock kisayolu hazir: {HOTKEY}")
    message = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message != 0x0312 or message.wParam != hotkey_id:
                continue
            new_enabled = not load_config()["auto_lock_enabled"]
            try:
                save_enabled(new_enabled)
            except OSError as exc:
                log(f"FaceLock durumu kaydedilemedi: {exc}")
                continue
            if new_enabled:
                enabled_event.set()
            else:
                enabled_event.clear()
            log(f"FaceLock izleme {'acik' if new_enabled else 'kapali'}")
            try:
                winsound.Beep(880 if new_enabled else 440, 180)
            except RuntimeError:
                pass
    finally:
        user32.UnregisterHotKey(None, hotkey_id)


def is_session_locked():
    # Kilit ekrani gorunurken bu oturumda LogonUI.exe calisir
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq LogonUI.exe", "/FI", f"SESSION eq {_session_id()}", "/NH"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return "LogonUI.exe" in out


def _session_id():
    sid = ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(ctypes.windll.kernel32.GetCurrentProcessId(),
                                                ctypes.byref(sid))
    return sid.value


class Face:
    def __init__(self):
        self.detector = cv2.FaceDetectorYN.create(
            str(MODELS / "yunet.onnx"), "", (320, 320), 0.8, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(MODELS / "sface.onnx"), "")

    def faces(self, frame):
        h, w = frame.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(frame)
        return [] if faces is None else list(faces)

    def embed(self, frame, face):
        aligned = self.recognizer.alignCrop(frame, face)
        return self.recognizer.feature(aligned)

    def similarity(self, a, b):
        return self.recognizer.match(a, b, cv2.FaceRecognizerSF_FR_COSINE)


def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


def grab(cap):
    # Tampondaki eski kareleri at, guncel kareyi al
    for _ in range(2):
        cap.grab()
    ok, frame = cap.read()
    return frame if ok else None


def is_covered(frame, cfg):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return gray.mean() < cfg["covered_brightness"] or gray.std() < cfg["covered_stddev"]


def enroll(cfg):
    face = Face()
    cap = open_camera(cfg["camera_index"])
    print("Kameraya bak. Basini yavasca hafif sag/sol/yukari/asagi oynat. (q = iptal)")
    samples = []
    last = 0.0
    while len(samples) < 25:
        frame = grab(cap)
        if frame is None:
            continue
        found = face.faces(frame)
        if len(found) == 1 and time.time() - last > 0.25:
            samples.append(face.embed(frame, found[0]))
            last = time.time()
        for f in found:
            x, y, w, h = map(int, f[:4])
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(frame, f"Kayit: {len(samples)}/25", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow("FaceLock - Kayit", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    if len(samples) < 10:
        print("Yeterli ornek alinamadi, tekrar dene.")
        return
    np.save(FACE_DB, np.vstack(samples))
    print(f"Kaydedildi: {FACE_DB} ({len(samples)} ornek)")


def recognize(face, frame, known, cfg):
    """(durum, en iyi skor) dondurur: 'me', 'other', 'none'."""
    found = face.faces(frame)
    if not found:
        return "none", 0.0
    best = 0.0
    for f in found:
        emb = face.embed(frame, f)
        score = max(face.similarity(emb, k.reshape(1, -1)) for k in known)
        best = max(best, score)
    return ("me" if best >= cfg["match_threshold"] else "other"), best


def run(cfg, preview=False):
    if not FACE_DB.exists():
        print("Once yuzunu kaydet: python facelock.py enroll")
        return
    enabled_event = threading.Event()
    if load_enabled():
        enabled_event.set()
    if not preview:
        threading.Thread(target=hotkey_loop, args=(enabled_event,), daemon=True).start()
    known = np.load(FACE_DB)
    face = Face()
    cap = None
    last_seen = time.monotonic()
    was_enabled = enabled_event.is_set()
    log(f"FaceLock basladi; izleme {'acik' if was_enabled else 'kapali'}")

    while True:
        if not enabled_event.is_set():
            if cap is not None:
                cap.release()
                cap = None
            was_enabled = False
            time.sleep(0.5)
            continue
        if not was_enabled:
            last_seen = time.monotonic()
            was_enabled = True

        if is_session_locked():
            # Kilitliyken kamerayi birak, acilinca sayaci sifirla
            if cap is not None:
                cap.release()
                cap = None
            time.sleep(2)
            last_seen = time.monotonic()
            continue

        if cap is None:
            cap = open_camera(cfg["camera_index"])
            last_seen = time.monotonic()

        frame = grab(cap)
        if frame is None:
            # Kamera okunamiyor (baska uygulama kullaniyor vb.) -> kilitleme
            cap.release()
            cap = None
            last_seen = time.monotonic()
            time.sleep(3)
            continue

        if is_covered(frame, cfg):
            state, score = "covered", 0.0
            last_seen = time.monotonic()
        else:
            state, score = recognize(face, frame, known, cfg)
            if state == "me":
                last_seen = time.monotonic()

        away = time.monotonic() - last_seen
        if preview:
            color = {"me": (0, 255, 0), "covered": (255, 200, 0)}.get(state, (0, 0, 255))
            cv2.putText(frame, f"{state} skor={score:.2f} yok={away:.0f}s", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
            cv2.imshow("FaceLock - Test (q = cikis)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        elif enabled_event.is_set() and away >= cfg["away_seconds"]:
            log(f"{away:.0f} sn yuz gorulmedi ({state}) -> kilitleniyor")
            cap.release()
            cap = None
            user32.LockWorkStation()
            time.sleep(3)

        time.sleep(cfg["check_interval"])

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = load_config()
    if cmd == "enroll":
        enroll(cfg)
    elif cmd == "status":
        print(f"FaceLock izleme {'acik' if load_enabled() else 'kapali'}; kisayol: {HOTKEY}")
    elif cmd == "test":
        run(cfg, preview=True)
    else:
        run(cfg)
