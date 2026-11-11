"""Local Windows desktop control panel. Camera frames never leave this process."""
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from settings import CONFIG, DATA_HOME, DEFAULTS, GESTURES, atomic_json, load_config, save_config

BG = "#0d111b"
PANEL = "#151c2b"
CARD = "#1b2537"
TEXT = "#eef2fc"
MUTED = "#93a2ba"
ACCENT = "#8cf0cb"
FROZEN = getattr(sys, "frozen", False)
SOURCE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def installed():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceUnlock") as key:
            return Path(winreg.QueryValueEx(key, "AgentPath")[0]).is_file()
    except OSError:
        return False


def label(parent, text, size=11, color=TEXT, bold=False, **kw):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=("Segoe UI", size, "bold" if bold else "normal"), **kw)


def button(parent, text, command, primary=False):
    return tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                     padx=18, pady=11, cursor="hand2", font=("Segoe UI", 10, "bold"),
                     bg=ACCENT if primary else CARD, fg=BG if primary else TEXT,
                     activebackground="#b1f7de" if primary else "#2a3952",
                     activeforeground=BG if primary else TEXT)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FaceUnlock · Yerel kontrol paneli")
        self.geometry("1180x820")
        self.minsize(1080, 760)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.events = queue.Queue()
        self.frames = queue.Queue(maxsize=1)
        self.stop_camera = threading.Event()
        self.camera_thread = None
        self.local_agent = None
        self.busy = False
        self.closing = False
        self.preview_until = 0
        self.cfg_error = None
        try:
            self.cfg = load_config()
        except (OSError, ValueError) as exc:
            self.cfg = dict(DEFAULTS)
            self.cfg_error = str(exc)
        self.gesture_vars = {key: tk.BooleanVar(value=key in self.cfg["enabled_gestures"]) for key in GESTURES}
        self.auto_lock = tk.BooleanVar(value=self.cfg["auto_lock_enabled"])
        self.unlock = tk.BooleanVar(value=self.cfg["unlock_enabled"])
        self.delay = tk.DoubleVar(value=self.cfg["away_seconds"])
        self.camera_index = tk.StringVar(value=str(self.cfg["camera_index"]))
        self.idle = tk.StringVar(value=str(self.cfg["camera_idle_seconds"]))
        self.message = tk.StringVar(value="Her şey bu bilgisayarda. Hesap yok, bulut yok.")
        self.camera_message = tk.StringVar(value="Kamera kapalı · Önizlemeyi sen başlatırsın")
        self.status = tk.StringVar(value="Durum kontrol ediliyor…")
        self.enrollment = tk.StringVar()
        self.model_status = tk.StringVar()
        self.provider_status = tk.StringVar()
        self.build_ui()
        self.show("Genel bakış")
        self.after(100, self.tick)
        if self.cfg_error:
            self.after(250, lambda: messagebox.showerror("Ayar dosyası okunamadı", self.cfg_error, parent=self))

    def build_ui(self):
        side = tk.Frame(self, bg=PANEL, width=224)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        label(side, "◉  FaceUnlock", 21, bold=True).pack(anchor="w", padx=22, pady=(34, 5))
        label(side, "SANA ÖZEL. YALNIZCA YEREL.", 8, MUTED).pack(anchor="w", padx=24)
        self.nav = {}
        for title in ("Genel bakış", "Hareketler", "Otomatik kilit", "Kurulum"):
            item = button(side, title, lambda name=title: self.show(name))
            item.pack(fill="x", padx=16, pady=(28 if not self.nav else 6, 0))
            self.nav[title] = item
        bottom = tk.Frame(side, bg=PANEL)
        bottom.pack(side="bottom", fill="x", padx=24, pady=28)
        label(bottom, "●  VERİLERİN SENDE", 9, ACCENT, True).pack(anchor="w")
        label(bottom, "Yüz kaydın ve ayarların\nyalnızca bu cihazda saklanır.", 9, MUTED, justify="left").pack(anchor="w", pady=(8, 0))
        body = tk.Frame(self, bg=BG)
        body.pack(side="left", fill="both", expand=True, padx=32, pady=26)
        header = tk.Frame(body, bg=BG)
        header.pack(fill="x")
        label(header, "KİŞİSEL ERİŞİM ALANIN", 9, MUTED, True).pack(side="left")
        label(header, "WINDOWS 11  /  LOCAL", 9, ACCENT).pack(side="right")
        self.heading = label(body, "", 29, bold=True, anchor="w")
        self.heading.pack(fill="x", pady=(12, 4))
        self.subtitle = label(body, "", 11, MUTED, anchor="w")
        self.subtitle.pack(fill="x", pady=(0, 22))
        footer = tk.Frame(body, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(15, 0))
        self.save_button = button(footer, "Değişiklikleri kaydet", self.save, True)
        self.save_button.pack(side="right")
        tk.Label(footer, textvariable=self.message, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9), wraplength=510, justify="left").pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.container = tk.Frame(body, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.pages = {name: tk.Frame(self.container, bg=BG) for name in self.nav}
        self.overview(self.pages["Genel bakış"])
        self.gestures_page(self.pages["Hareketler"])
        self.lock_page(self.pages["Otomatik kilit"])
        self.setup_page(self.pages["Kurulum"])

    def card(self, parent):
        return tk.Frame(parent, bg=PANEL, padx=22, pady=18, highlightbackground="#27334a", highlightthickness=1)

    def overview(self, page):
        banner = self.card(page)
        banner.pack(fill="x", pady=(0, 14))
        label(banner, "Yüzün tanınsın. Kontrol sende kalsın.", 17, bold=True).pack(anchor="w")
        tk.Label(banner, textvariable=self.status, bg=PANEL, fg=ACCENT, font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 0))
        row = tk.Frame(page, bg=BG)
        row.pack(fill="both", expand=True)
        left = self.card(row)
        left.pack(side="left", fill="both", expand=True, padx=(0, 14))
        label(left, "KAMERA VE YÜZ KAYDI", 9, MUTED, True).pack(anchor="w")
        self.video = tk.Label(left, bg="#0a101b", fg=MUTED,
                              text="◎\n\nKameran hazır olduğunda\nönizlemeyi başlat.", font=("Segoe UI", 15))
        self.video.pack(fill="both", expand=True, pady=16)
        tk.Label(left, textvariable=self.camera_message, bg=PANEL, fg=MUTED,
                 wraplength=440, font=("Segoe UI", 9)).pack(pady=(0, 10))
        controls = tk.Frame(left, bg=PANEL)
        controls.pack(fill="x")
        button(controls, "Önizleme", lambda: self.start_camera(False)).pack(side="left")
        button(controls, "Yüzümü kaydet", lambda: self.start_camera(True), True).pack(side="left", padx=8)
        button(controls, "Durdur", self.stop_preview).pack(side="right")
        right = tk.Frame(row, bg=BG, width=230)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        for title, variable, note in (
            ("01  YÜZ PROFİLİ", self.enrollment, "Fotoğraf yerine yüz özellikleri\nbu bilgisayarda saklanır."),
            ("02  TANIMA MODELLERİ", self.model_status, "İlk kurulumda indirilir.\nSonrasında çevrimdışı çalışır."),
            ("03  WINDOWS BAĞLANTISI", self.provider_status, "Kilit açmak için Windows\nsağlayıcısı kurulmalıdır."),
        ):
            tile = self.card(right)
            tile.pack(fill="x", pady=(0, 12))
            label(tile, title, 9, MUTED, True).pack(anchor="w")
            tk.Label(tile, textvariable=variable, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=8)
            label(tile, note, 9, MUTED, justify="left").pack(anchor="w")

    def check(self, parent, text, variable):
        return tk.Checkbutton(parent, text=text, variable=variable, bg=parent.cget("bg"),
                              fg=TEXT, selectcolor=CARD, activebackground=parent.cget("bg"),
                              activeforeground=ACCENT, font=("Segoe UI", 12, "bold"),
                              cursor="hand2", anchor="w", padx=0, pady=5)

    def gestures_page(self, page):
        banner = self.card(page)
        banner.pack(fill="x", pady=(0, 18))
        self.check(banner, "Yüz + hareket ile kilit aç", self.unlock).pack(anchor="w")
        label(banner, "Seçtiğin hareketlerden herhangi biri yeterli. Önce kayıtlı yüzün eşleşmeli.", 10, MUTED).pack(anchor="w", pady=(4, 0))
        grid = tk.Frame(page, bg=BG)
        grid.pack(fill="both", expand=True)
        for col in (0, 1):
            grid.columnconfigure(col, weight=1, uniform="gesture")
        for i, (key, (title, description)) in enumerate(GESTURES.items()):
            tile = self.card(grid)
            tile.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0, 7) if i % 2 == 0 else (7, 0), pady=(0, 14))
            grid.rowconfigure(i // 2, weight=1)
            self.check(tile, title, self.gesture_vars[key]).pack(anchor="w")
            label(tile, description, 10, MUTED, wraplength=320, justify="left").pack(anchor="w", pady=(8, 0))

    def lock_page(self, page):
        main = self.card(page)
        main.pack(fill="x", pady=(0, 16))
        self.check(main, "Uzaklaştığımda oturumu kilitle", self.auto_lock).pack(anchor="w")
        label(main, "Yüzün yeniden görünürse geri sayım sıfırlanır. Uygulamaların açık kalır.", 10, MUTED).pack(anchor="w", pady=(5, 20))
        self.delay_label = label(main, "", 32, ACCENT, True)
        self.delay_label.pack(anchor="w")
        self.delay.trace_add("write", self.update_delay)
        self.update_delay()
        scale = tk.Scale(main, from_=5, to=3600, resolution=1, orient="horizontal", variable=self.delay,
                         showvalue=False, bg=PANEL, fg=TEXT, troughcolor=CARD, highlightthickness=0,
                         activebackground=ACCENT, length=640)
        scale.pack(fill="x", pady=12)
        presets = tk.Frame(main, bg=PANEL)
        presets.pack(fill="x")
        for seconds in (10, 30, 40, 60, 120, 300):
            button(presets, f"{seconds} sn", lambda value=seconds: self.delay.set(value)).pack(side="left", padx=(0, 9))
        custom = tk.Frame(main, bg=PANEL)
        custom.pack(fill="x", pady=(12, 0))
        label(custom, "Özel süre (5–3600 sn)", 10, MUTED).pack(side="left")
        tk.Spinbox(custom, from_=5, to=3600, textvariable=self.delay, width=8,
                   bg=CARD, fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 11)).pack(side="left", padx=12)
        note = self.card(page)
        note.pack(fill="x")
        label(note, "Kısa bir mola, kesintisiz çalışma", 15, bold=True).pack(anchor="w")
        label(note, "Kamera başka bir uygulamada kullanılıyorsa veya görüntü alınamıyorsa\notomatik kilit bekler. Önizleme ve yüz kaydı sırasında izleme duraklar.\nKamera kapakla örtülmüşse de kilitlenmez.",
              11, MUTED, justify="left").pack(anchor="w", pady=12)

    def setup_page(self, page):
        top = self.card(page)
        top.pack(fill="x", pady=(0, 14))
        label(top, "Birkaç adımda kendi sistemin", 17, bold=True).pack(anchor="w")
        label(top, "1  Modelleri indir    →    2  Yüzünü kaydet    →    3  Windows'a bağla", 11, MUTED).pack(anchor="w", pady=12)
        actions = tk.Frame(top, bg=PANEL)
        actions.pack(fill="x")
        button(actions, "1 · Modelleri hazırla", self.download, True).pack(side="left")
        button(actions, "2 · Yüz kaydı", lambda: (self.show("Genel bakış"), self.start_camera(True))).pack(side="left", padx=10)
        button(actions, "3 · Windows kurulumu", self.install).pack(side="left")
        options = self.card(page)
        options.pack(fill="x", pady=(0, 14))
        label(options, "Kamera ayarları", 15, bold=True).pack(anchor="w", pady=(0, 12))
        line = tk.Frame(options, bg=PANEL)
        line.pack(fill="x")
        label(line, "Kamera numarası", 11, MUTED).pack(side="left")
        ttk.Combobox(line, textvariable=self.camera_index, values=list(range(21)), state="readonly", width=5).pack(side="left", padx=(12, 26))
        label(line, "Kilitliyken kamera beklemesi (sn)", 10, MUTED).pack(side="left")
        ttk.Combobox(line, textvariable=self.idle, values=["0", "0.5", "1", "2", "5", "10"], width=6).pack(side="left", padx=12)
        label(options, "0 = kilitliyken sürekli açık. Diğer değerlerde yüz kaybolunca kamera dinlenir.", 9, MUTED).pack(anchor="w", pady=(12, 0))
        local = self.card(page)
        local.pack(fill="x", pady=(0, 14))
        label(local, "Yerel izleme", 15, bold=True).pack(anchor="w")
        label(local, "Sağlayıcı kurulmadan yalnızca otomatik kilitlemeyi kullanabilirsin.", 10, MUTED).pack(anchor="w", pady=8)
        row = tk.Frame(local, bg=PANEL)
        row.pack(fill="x")
        button(row, "İzlemeyi başlat", self.start_monitor).pack(side="left")
        button(row, "Korunan yüz kaydını güncelle", lambda: self.install(refresh=True)).pack(side="left", padx=10)
        button(row, "Veri klasörü", lambda: os.startfile(DATA_HOME)).pack(side="left")
        label(page, "Windows kurulumu yönetici izni ve hesap parolanı ister; PIN kullanılmaz.\nRGB kamera, Windows Hello düzeyinde canlılık doğrulaması sunmaz.\nPIN/parola ile giriş kullanılabilir kalır. Kilit açma mevcut oturum içindir.",
              10, MUTED, justify="left").pack(anchor="w", pady=4)

    def show(self, name):
        descriptions = {
            "Genel bakış": "Yüz tanıma, hareketlerin ve cihazın. Hepsi tek yerde.",
            "Hareketler": "Kilit açma işaretini seç. Sana doğal gelen hareketlerle devam et.",
            "Otomatik kilit": "Sen uzaklaşınca ekranın güvende kalsın.",
            "Kurulum": "Bir kez hazırla. Kendi bilgisayarında, çevrimdışı kullan.",
        }
        for key, frame in self.pages.items():
            frame.pack_forget()
            self.nav[key].configure(bg=ACCENT if key == name else CARD, fg=BG if key == name else TEXT)
        self.pages[name].pack(fill="both", expand=True)
        self.heading.configure(text=name)
        self.subtitle.configure(text=descriptions[name])

    def update_delay(self, *_):
        try:
            self.delay_label.configure(text=f"{self.delay.get():g} saniye sonra")
        except tk.TclError:
            pass

    def save(self):
        try:
            # Re-read first so unrelated changes made by another tool survive.
            cfg = load_config()
            cfg.update(auto_lock_enabled=self.auto_lock.get(), unlock_enabled=self.unlock.get(),
                       away_seconds=self.delay.get(), camera_index=int(self.camera_index.get()),
                       camera_idle_seconds=float(self.idle.get()),
                       enabled_gestures=[key for key, variable in self.gesture_vars.items() if variable.get()])
            self.cfg = save_config(cfg)
            self.message.set("Kaydedildi · Yeni ajan ayarları yaklaşık 1 saniye içinde uygular.")
            return True
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Ayarlar kaydedilemedi", str(exc), parent=self)
            return False

    def background(self, operation):
        if self.busy:
            self.message.set("Devam eden işlem tamamlanınca tekrar dene.")
            return
        self.busy = True
        def worker():
            try:
                operation()
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("idle", None))
        threading.Thread(target=worker, daemon=True).start()

    def download(self):
        def operation():
            from model_setup import download_models
            download_models(lambda text: self.events.put(("message", text)))
        self.background(operation)

    def start_camera(self, enroll):
        if self.camera_thread and self.camera_thread.is_alive():
            self.message.set("Önce açık önizlemeyi durdur.")
            return
        from model_setup import MODELS
        if any(not (DATA_HOME / "models" / name).exists() for name in MODELS):
            self.show("Kurulum")
            self.message.set("Önce ‘Modelleri hazırla’ ile tanıma modellerini indir.")
            return
        if not self.save():
            return
        if enroll and (DATA_HOME / "my_face.npy").exists():
            if not messagebox.askyesno("Yüz kaydını yenile", "Mevcut yerel yüz kaydı yenisiyle değiştirilecek. Devam edilsin mi?", parent=self):
                return
        self.show("Genel bakış")
        self.stop_camera.clear()
        self.camera_message.set("Kamera hazırlanıyor…")
        self.lease()
        self.camera_thread = threading.Thread(target=self.camera_worker, args=(enroll, dict(self.cfg)), daemon=True)
        self.camera_thread.start()

    def lease(self):
        try:
            atomic_json(DATA_HOME / "preview.json", {"until": time.time() + 5})
        except OSError as exc:
            self.stop_camera.set()
            self.message.set(f"Kamera için izleme duraklatılamadı: {exc}")

    def camera_worker(self, enroll, cfg):
        cap = detector = None
        try:
            import cv2
            import numpy as np
            import facelock as fl
            from unlock_gestures import UnlockGestureDetector
            from recognition import calibrate_threshold
            # The running agent notices the lease before this camera opens.
            if self.stop_camera.wait(2.0):
                return
            face = fl.Face()
            detector = UnlockGestureDetector(cfg["enabled_gestures"]) if not enroll else None
            known = np.load(fl.FACE_DB, allow_pickle=False) if fl.FACE_DB.exists() and not enroll else None
            threshold = calibrate_threshold(known) if known is not None else 1
            cap = fl.open_camera(cfg["camera_index"])
            if not cap.isOpened():
                raise RuntimeError("Kamera açılamadı. Kamera numarasını ve Windows kamera izinlerini kontrol et.")
            samples = []
            last_sample = 0
            started = time.monotonic()
            while not self.stop_camera.is_set():
                if enroll and time.monotonic() - started > 90:
                    raise RuntimeError("Yüz kaydı zaman aşımına uğradı. Işığı ve kamera açısını kontrol et.")
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Kamera görüntüsü alınamadı. Diğer kamera uygulamalarını kapatıp tekrar dene.")
                found = face.faces(frame)
                message = "Kamerada yalnızca sen olmalısın." if len(found) > 1 else "Yüzünü kameraya çevir."
                if len(found) == 1 and not fl.is_covered(frame, cfg):
                    embedding = face.embed(frame, found[0])
                    if enroll:
                        consistent = not samples or face.similarity(embedding, samples[0]) >= 0.5
                        if consistent and time.monotonic() - last_sample > 0.25:
                            samples.append(embedding)
                            last_sample = time.monotonic()
                        message = f"Yüz kaydı: {len(samples)}/25 · Başını hafifçe sağa ve sola çevir."
                    else:
                        match = known is not None and max(face.similarity(embedding, item.reshape(1, -1)) for item in known) >= threshold
                        gesture = detector.detect(frame, found[0][2]) if match else None
                        if not match:
                            detector.reset()
                        message = ("Yüz eşleşti · " + (GESTURES[gesture][0] if gesture else "Seçtiğin hareketi dene")) if match else "Kayıtlı yüz eşleşmedi." if known is not None else "Yüz bulundu · Önce yüzünü kaydet."
                    x, y, w, h = map(int, found[0][:4])
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (170, 240, 140), 2)
                elif detector:
                    detector.reset()
                rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
                if self.frames.empty():
                    self.frames.put((rgb, message))
                if enroll and len(samples) >= 25:
                    temporary = fl.FACE_DB.with_suffix(".tmp")
                    with temporary.open("wb") as handle:
                        np.save(handle, np.vstack(samples))
                    os.replace(temporary, fl.FACE_DB)
                    (DATA_HOME / "my_face_adaptive.npy").unlink(missing_ok=True)
                    self.events.put(("enrolled", "Yüzün kaydedildi. Windows kuruluysa ‘Korunan yüz kaydını güncelle’ düğmesini kullan."))
                    break
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            if cap is not None:
                cap.release()
            if detector is not None:
                detector.close()
            self.events.put(("camera_done", None))

    def stop_preview(self):
        self.stop_camera.set()
        self.camera_message.set("Kamera durduruluyor…")

    def start_monitor(self):
        if self.camera_thread and self.camera_thread.is_alive():
            self.message.set("İzlemeyi başlatmadan önce önizlemeyi durdur.")
            return
        if installed():
            result = subprocess.run(["schtasks", "/Run", "/TN", "FaceUnlockAgent"], capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.message.set("Başlatma isteği gönderildi; durumu Genel bakış'ta görebilirsin." if result.returncode == 0 else "Ajan başlatılamadı. Windows kurulumunu kontrol et.")
            return
        if self.local_agent and self.local_agent.poll() is None:
            self.message.set("Yerel izleme zaten çalışıyor.")
            return
        if not (DATA_HOME / "my_face.npy").exists():
            self.message.set("Önce modelleri hazırla ve yüzünü kaydet.")
            return
        if not self.save():
            return
        if FROZEN:
            command = [str(SOURCE.parent / "agent" / "FaceUnlockAgent.exe"), "--lock-only"]
        else:
            command = [sys.executable, str(SOURCE / "unlock_agent.py"), "--lock-only"]
        try:
            self.local_agent = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
            self.message.set("Yerel izleme başlatılıyor · Paneli kapatınca bu yerel ajan durur.")
        except OSError as exc:
            messagebox.showerror("İzleme başlatılamadı", str(exc), parent=self)

    def install(self, refresh=False):
        self.stop_preview()
        script = (SOURCE.parent if FROZEN else SOURCE) / "install.ps1"
        protected_root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "FaceUnlock"
        if FROZEN and SOURCE.parent == protected_root and not refresh:
            self.message.set("Windows bileşeni kurulu. Yeni yüz kaydı için ‘Korunan yüz kaydını güncelle’yi kullan.")
            return
        if not script.is_file():
            self.message.set("Kurulum betiği bulunamadı. Tam kaynak kodu veya dağıtım paketini kullan.")
            return
        if not self.save():
            return
        if not refresh and not FROZEN:
            dll = SOURCE / "FaceUnlockCP.dll"
            build = Path(os.environ["LOCALAPPDATA"]) / "FaceUnlockBuild" / "dist"
            if not dll.exists() or not (build / "FaceUnlockAgent" / "FaceUnlockAgent.exe").exists() or not (build / "FaceUnlock" / "FaceUnlock.exe").exists():
                self.message.set("Windows bileşenleri derleniyor. Python 3.12 ve Visual Studio C++ Build Tools gerekli.")
                def prepare():
                    log_path = DATA_HOME / "build.log"
                    with log_path.open("w", encoding="utf-8") as output:
                        for filename in ("build.cmd", "build-agent.cmd"):
                            result = subprocess.run(["cmd.exe", "/d", "/c", str(SOURCE / filename)],
                                                    cwd=SOURCE, stdout=output, stderr=subprocess.STDOUT,
                                                    creationflags=subprocess.CREATE_NO_WINDOW)
                            if result.returncode:
                                raise RuntimeError(f"Derleme tamamlanamadı. Python 3.12 ve Visual Studio C++ Build Tools gerekli. Ayrıntılar: {log_path}")
                    self.events.put(("install_ready", None))
                self.background(prepare)
                return
        if self.local_agent and self.local_agent.poll() is None:
            self.local_agent.terminate()
        args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", str(script)]
        if refresh:
            args.append("-RefreshData")
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe",
                                                     subprocess.list2cmdline(args), str(script.parent), 1)
        self.message.set("Windows kurulum penceresindeki adımları tamamla." if result > 32 else "Windows kurulum izni verilmedi veya pencere açılamadı.")

    def tick(self):
        if self.closing:
            return
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind in ("message", "enrolled"):
                self.message.set(value)
                if kind == "enrolled" and self.local_agent and self.local_agent.poll() is None:
                    self.local_agent.terminate()
                    self.message.set(value + " Yerel izlemeyi yeniden başlat.")
            elif kind == "error":
                self.message.set(value)
                messagebox.showerror("İşlem tamamlanamadı", value, parent=self)
            elif kind == "idle":
                self.busy = False
            elif kind == "install_ready":
                self.install()
            elif kind == "camera_done":
                self.camera_message.set("Kamera kapalı · Önizleme hiçbir zaman kilit açmaz")
                self.video.configure(image="", text="◎\n\nÖnizleme tamamlandı.")
                self.video.image = None
                while not self.frames.empty():
                    self.frames.get_nowait()
        if not self.frames.empty():
            from PIL import Image, ImageTk
            frame, message = self.frames.get_nowait()
            picture = Image.fromarray(frame)
            picture.thumbnail((max(1, self.video.winfo_width()), max(1, self.video.winfo_height())))
            photo = ImageTk.PhotoImage(picture)
            self.video.configure(image=photo, text="")
            self.video.image = photo
            self.camera_message.set(message)
        if time.monotonic() > self.preview_until:
            self.preview_until = time.monotonic() + 1
            if self.camera_thread and self.camera_thread.is_alive():
                self.lease()
            self.enrollment.set("Kayıtlı" if (DATA_HOME / "my_face.npy").exists() else "Henüz kayıt yok")
            from model_setup import MODELS
            count = sum((DATA_HOME / "models" / name).exists() for name in MODELS)
            self.model_status.set("Hazır" if count == len(MODELS) else f"{count}/{len(MODELS)} model")
            self.provider_status.set("Kurulu" if installed() else "Kurulmadı")
            try:
                status = json.loads((DATA_HOME / "agent_status.json").read_text(encoding="utf-8"))
                live = 0 <= time.time() - status["at"] < 8
                self.status.set(("Önizleme veya ayar hatası nedeniyle duraklatıldı" if status["paused"] else "İzleme ajanı çalışıyor") if live else "Ajan çevrimdışı · Kurulum sekmesinden başlat")
            except (OSError, ValueError, KeyError, TypeError):
                self.status.set("Ajan çevrimdışı · Önizleme ve ayarları kullanabilirsin")
        self.after(60, self.tick)

    def close(self):
        self.stop_camera.set()
        if self.camera_thread and self.camera_thread.is_alive():
            self.message.set("Kamera serbest bırakılıyor…")
            self.after(150, self.close)
            return
        self.closing = True
        if self.local_agent and self.local_agent.poll() is None:
            self.local_agent.terminate()
        self.destroy()


def self_test(output):
    """Validate a packaged build without touching user settings or the camera."""
    import tempfile
    import cv2
    import numpy
    import mediapipe
    from PIL import ImageTk
    from unlock_gestures import UnlockGestureDetector
    global DATA_HOME, load_config, save_config, installed
    original_load, original_save = load_config, save_config
    with tempfile.TemporaryDirectory(prefix="FaceUnlock-panel-check-") as folder:
        DATA_HOME = Path(folder)
        load_config = lambda: original_load(DATA_HOME / "config.json")
        save_config = lambda cfg: original_save(cfg, DATA_HOME / "config.json")
        installed = lambda: False
        app = App()
        try:
            app.withdraw()
            for name in app.pages:
                app.show(name)
                app.update_idletasks()
            app.delay.set(120)
            if not app.save() or load_config()["away_seconds"] != 120:
                raise RuntimeError("Packaged settings round trip failed")
            atomic_json(Path(output), {"ok": True, "pages": len(app.pages),
                                       "opencv": cv2.__version__, "numpy": numpy.__version__,
                                       "mediapipe": mediapipe.__version__, "camera_opened": False})
        finally:
            app.close()


if __name__ == "__main__":
    from windows_runtime import single_instance
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        self_test(sys.argv[2])
    else:
        DATA_HOME.mkdir(parents=True, exist_ok=True)
        with single_instance("Panel") as first:
            if first:
                App().mainloop()
