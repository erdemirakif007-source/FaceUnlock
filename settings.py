"""Shared, validated local settings. No camera or Windows imports."""
import json
import math
import os
import tempfile
import time
from pathlib import Path


def data_home():
    # Keep existing installations working; fresh installs use LocalAppData.
    legacy = Path.home() / "FaceLock"
    if (legacy / "config.json").exists():
        return legacy
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FaceUnlock"


DATA_HOME = data_home()
CONFIG = DATA_HOME / "config.json"
GESTURES = {
    "double_blink": ("Çift göz kırpma", "İki kez göz kırp. Ellerini kullanmadan aç."),
    "snap": ("Parmak şıklatma", "Başparmak ve orta parmağını birleştirip ayır."),
    "peace": ("Zafer işareti", "İşaret ve orta parmağınla V işareti yap."),
    "thumbs_up": ("Başparmak yukarı", "Başparmağını yukarı kaldır."),
    "spiderman": ("Spiderman", "Başparmak, işaret ve serçe parmağını aç."),
    "look_up": ("Yukarı bakma", "Başını sabit tutup gözlerinle yukarı bak."),
}
DEFAULTS = {
    "camera_index": 0, "away_seconds": 40, "check_interval": 0.7,
    "match_threshold": 0.5, "covered_brightness": 15, "covered_stddev": 12,
    "camera_idle_seconds": 0.5, "auto_lock_enabled": True,
    "unlock_enabled": True, "enabled_gestures": list(GESTURES),
}


def validate(values):
    if not isinstance(values, dict):
        raise ValueError("Ayar dosyası bir JSON nesnesi olmalı.")
    cfg = dict(DEFAULTS, **values)  # Preserve older, unrelated keys.
    for key, low, high in (
        ("camera_index", 0, 20), ("away_seconds", 5, 3600),
        ("check_interval", 0.1, 5), ("match_threshold", 0.36, 0.9),
        ("covered_brightness", 0, 255), ("covered_stddev", 0, 128),
        ("camera_idle_seconds", 0, 60),
    ):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key}: {low}–{high} aralığında bir sayı gerekli.")
    if int(cfg["camera_index"]) != cfg["camera_index"]:
        raise ValueError("Kamera numarası tam sayı olmalı.")
    cfg["camera_index"] = int(cfg["camera_index"])
    for key in ("auto_lock_enabled", "unlock_enabled"):
        if not isinstance(cfg[key], bool):
            raise ValueError(f"{key}: açık veya kapalı olmalı.")
    selected = cfg["enabled_gestures"]
    if not isinstance(selected, list) or any(not isinstance(g, str) or g not in GESTURES for g in selected):
        raise ValueError("Bilinmeyen hareket seçimi.")
    cfg["enabled_gestures"] = list(dict.fromkeys(selected))
    if cfg["unlock_enabled"] and not selected:
        raise ValueError("Kilit açma için en az bir hareket seç.")
    return cfg


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_config(path=None):
    path = Path(path or CONFIG)
    values = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if isinstance(values, dict) and "auto_lock_enabled" not in values:
        try:
            enabled = json.loads((path.parent / "state.json").read_text(encoding="utf-8-sig"))["enabled"]
            if isinstance(enabled, bool):
                values["auto_lock_enabled"] = enabled
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return validate(values)


def save_config(values, path=None):
    cfg = validate(values)
    atomic_json(path or CONFIG, cfg)
    return cfg


def preview_active(base=DATA_HOME):
    try:
        until = json.loads((base / "preview.json").read_text(encoding="utf-8"))["until"]
        return 0 < float(until) - time.time() <= 6
    except (OSError, ValueError, KeyError, TypeError):
        return False


class PresenceTimer:
    """No locking on camera failures; reset the grace period on every pause."""
    def __init__(self):
        self.last_seen = None

    def reset(self):
        self.last_seen = None

    def feed(self, now, present, delay, *, usable=True, enabled=True):
        if not enabled or not usable:
            self.reset()
            return False
        if present or self.last_seen is None:
            self.last_seen = now
        return now - self.last_seen >= delay
