@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)

".venv\Scripts\python.exe" -c "import imageio_ffmpeg, sounddevice, numpy" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" main.py
endlocal
