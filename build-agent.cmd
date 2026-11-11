@echo off
setlocal
cd /d "%~dp0"
rem Freeze the camera agent so install.ps1 can place it under Program Files.
rem Uses a clean venv so only the agent's own dependencies are bundled.
rem Output stays outside OneDrive: %LOCALAPPDATA%\FaceUnlockBuild\dist\FaceUnlockAgent
set OUT=%LOCALAPPDATA%\FaceUnlockBuild
set PY=%OUT%\venv\Scripts\python.exe
if not exist "%PY%" (
  python -m venv "%OUT%\venv" || exit /b 1
)
python -m pip --python "%PY%" install -r requirements.txt pyinstaller==6.22.3 || exit /b 1
rem PyInstaller's isolated helper processes hang behind the venv launcher on
rem this machine, so run the base interpreter with the venv packages first.
for /f "usebackq tokens=1,* delims== " %%a in ("%OUT%\venv\pyvenv.cfg") do if "%%a"=="home" set BASEPY=%%b\python.exe
set PYTHONPATH=%OUT%\venv\Lib\site-packages
set PYTHONNOUSERSITE=1
"%BASEPY%" -m PyInstaller --noconfirm --clean --windowed --onedir --name FaceUnlockAgent ^
  --paths "%~dp0." --collect-all mediapipe ^
  --exclude-module torch --exclude-module torchvision --exclude-module torchaudio ^
  --exclude-module scipy --exclude-module h5py --exclude-module onnxruntime ^
  --exclude-module tensorflow --exclude-module jax --exclude-module jaxlib ^
  --exclude-module pandas --exclude-module sklearn --exclude-module yt_dlp ^
  --exclude-module IPython --exclude-module transformers ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%" ^
  unlock_agent.py
if errorlevel 1 exit /b 1
"%BASEPY%" -m PyInstaller --noconfirm --clean --windowed --onedir --name FaceUnlock ^
  --paths "%~dp0." --collect-all mediapipe ^
  --exclude-module torch --exclude-module torchvision --exclude-module torchaudio ^
  --exclude-module scipy --exclude-module h5py --exclude-module onnxruntime ^
  --exclude-module tensorflow --exclude-module jax --exclude-module jaxlib ^
  --exclude-module pandas --exclude-module sklearn --exclude-module yt_dlp ^
  --exclude-module IPython --exclude-module transformers ^
  --distpath "%OUT%\dist" --workpath "%OUT%\build" --specpath "%OUT%" ^
  faceunlock_app.py
exit /b %ERRORLEVEL%
