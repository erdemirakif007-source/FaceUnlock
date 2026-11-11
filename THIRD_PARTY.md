# External components and model provenance

Original copyright notices in `helpers.cpp` and `helpers.h` are retained. These helper files derive from Microsoft's [Credential Provider sample](https://github.com/microsoft/Windows-classic-samples/tree/main/Samples/CredentialProvider/cpp); consult its [repository license](https://github.com/microsoft/Windows-classic-samples/blob/main/LICENSE).

The model downloader fetches public, version-named assets directly from upstream hosts. SHA-256 digests are pinned in `model_setup.py`; upstream content changes fail verification. Models and enrollment data are not bundled in source or release ZIPs.

| Component | Upstream |
| --- | --- |
| YuNet, 2023mar | [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) |
| SFace, 2021dec | [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) |
| Hand landmarker, float16 v1 | [MediaPipe](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) |
| Face landmarker, float16 v1 | [MediaPipe](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) |
| OpenCV Python | [opencv-python](https://github.com/opencv/opencv-python) |
| MediaPipe Python | [mediapipe](https://github.com/google-ai-edge/mediapipe) |
| NumPy | [numpy](https://github.com/numpy/numpy) |
| Pillow | [Pillow](https://github.com/python-pillow/Pillow) |
| Python / Tcl / Tk | [Python](https://www.python.org/), [Tcl/Tk](https://www.tcl.tk/) |
| PyInstaller | [PyInstaller](https://github.com/pyinstaller/pyinstaller) |

Each component retains its own upstream license and notices. Retain collected package metadata/license files in binary distributions. This project does not change third-party terms.
