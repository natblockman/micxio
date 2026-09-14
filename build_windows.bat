@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name micxio ^
  --distpath "release\windows" ^
  --workpath ".build\pyinstaller-windows\work" ^
  --specpath ".build\pyinstaller-windows\spec" ^
  --add-data "assets;assets" ^
  --collect-all imageio_ffmpeg ^
  main.py

copy /Y "INSTALL.md" "release\windows\micxio\" >nul
copy /Y "QUICK-START.md" "release\windows\micxio\" >nul
copy /Y "LICENSE" "release\windows\micxio\" >nul
copy /Y "THIRD-PARTY-NOTICES.md" "release\windows\micxio\" >nul

echo Built: release\windows\micxio
echo Zip the micxio folder inside that directory before uploading it to Gumroad.
endlocal
