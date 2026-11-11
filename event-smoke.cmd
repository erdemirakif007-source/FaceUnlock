@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /W4 event_smoke.cpp /link /OUT:event_smoke.exe ole32.lib user32.lib
if errorlevel 1 exit /b 1
.\event_smoke.exe
exit /b %ERRORLEVEL%
