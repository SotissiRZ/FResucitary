@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement developpeur absent. Lancez BUILD_WINDOWS.bat ou creez .venv avec Python 3.11 à 3.13.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py
