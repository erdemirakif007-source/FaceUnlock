@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv || goto :fail
)
if not exist ".venv\ready.txt" (
  .venv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
  echo ready> .venv\ready.txt
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0faceunlock_app.py"
exit /b 0
:fail
echo Kurulum tamamlanamadi. Python 3.12 x64 ve internet baglantisini kontrol edin.
pause
exit /b 1
