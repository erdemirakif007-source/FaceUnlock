@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /W4 provider_smoke.cpp /link /OUT:provider_smoke.exe ole32.lib gdi32.lib
if errorlevel 1 exit /b 1
.\provider_smoke.exe
exit /b %ERRORLEVEL%
