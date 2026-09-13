@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_release.ps1"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo.
  echo ECHEC DU BUILD ^(code %RC%^).
  pause
  exit /b %RC%
)
echo.
echo Build termine avec succes.
pause
