#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
RELEASE_DIR="$PROJECT_DIR/release/linux"
BUILD_DIR="$PROJECT_DIR/.build/pyinstaller-linux"

if [[ ! -x "$PYTHON" ]]; then
  python3 -m venv "$PROJECT_DIR/.venv"
fi

"$PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt" pyinstaller
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name micxio \
  --distpath "$RELEASE_DIR" \
  --workpath "$BUILD_DIR/work" \
  --specpath "$BUILD_DIR/spec" \
  --add-data "$PROJECT_DIR/assets:assets" \
  --collect-all imageio_ffmpeg \
  "$PROJECT_DIR/main.py"

cp "$PROJECT_DIR/INSTALL.md" "$PROJECT_DIR/QUICK-START.md" "$PROJECT_DIR/LICENSE" "$PROJECT_DIR/THIRD-PARTY-NOTICES.md" \
  "$RELEASE_DIR/micxio/"

echo "Built: $RELEASE_DIR/micxio"
echo "Zip the micxio folder inside that directory before uploading it to Gumroad."
