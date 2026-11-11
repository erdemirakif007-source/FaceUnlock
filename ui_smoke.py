"""Exercise the real desktop panel against temporary data; never open a camera."""
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import faceunlock_app as panel
from settings import load_config, save_config


def main():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        cfg_path = base / "config.json"
        with patch.object(panel, "DATA_HOME", base), \
             patch.object(panel, "load_config", lambda: load_config(cfg_path)), \
             patch.object(panel, "save_config", lambda cfg: save_config(cfg, cfg_path)), \
             patch.object(panel, "installed", lambda: False):
            app = panel.App()
            app.geometry("1180x820+60+60")
            app.update()
            try:
                for name in app.pages:
                    app.show(name)
                    app.update()
                    assert app.pages[name].winfo_ismapped(), name
                app.delay.set(120)
                for key, var in app.gesture_vars.items():
                    var.set(key == "peace")
                assert app.save()
                assert load_config(cfg_path)["away_seconds"] == 120
                assert load_config(cfg_path)["enabled_gestures"] == ["peace"]
                with patch.object(panel.messagebox, "showerror") as error:
                    app.gesture_vars["peace"].set(False)
                    assert not app.save()
                    error.assert_called_once()
                assert load_config(cfg_path)["enabled_gestures"] == ["peace"]
                app.gesture_vars["peace"].set(True)
                app.show("Genel bakış")
                app.update()
                time.sleep(0.3)
                app.update()
                if "--screenshots" in __import__("sys").argv:
                    from PIL import ImageGrab
                    output = Path("artifacts")
                    output.mkdir(exist_ok=True)
                    for name, filename in (("Genel bakış", "overview.png"), ("Hareketler", "gestures.png"),
                                           ("Otomatik kilit", "auto-lock.png"), ("Kurulum", "setup.png")):
                        app.show(name)
                        app.update()
                        time.sleep(0.2)
                        # Capture only this window, even when another app is in front.
                        hwnd = panel.ctypes.windll.user32.GetParent(app.winfo_id())
                        ImageGrab.grab(window=hwnd).save(output / filename)
            finally:
                app.close()
    print("UI smoke passed: navigation, persisted settings, invalid selection rejected; no camera opened")


if __name__ == "__main__":
    main()
