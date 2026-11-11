"""Download public model assets once; verify SHA-256 before atomic installation."""
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

from settings import DATA_HOME

MODELS = {
    "yunet.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
    "hand_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
        "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"),
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"),
}


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download_models(progress=lambda message: None, directory=None):
    directory = Path(directory or DATA_HOME / "models")
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in MODELS.items():
        target = directory / name
        if target.exists() and digest(target) == expected:
            progress(f"Doğrulandı: {name}")
            continue
        progress(f"İndiriliyor: {name}")
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".download")
        try:
            with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=30) as response:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 60 * 1024 * 1024:
                        raise ValueError(f"Model beklenenden büyük: {name}")
                    output.write(chunk)
            if digest(temporary) != expected:
                raise ValueError(f"Model doğrulanamadı: {name}. Tekrar deneyin.")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    progress("Modeller hazır. Şimdi yüzünü kaydedebilirsin.")


if __name__ == "__main__":
    download_models(print)
