#!/usr/bin/env bash
# Optional: build "dist/Jiv Desktop.app" with PyInstaller (run install.command first)
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run install.command first"; exit 1; }
.venv/bin/python -m pip install -q pyinstaller
.venv/bin/pyinstaller --noconfirm --windowed --name "Jiv Desktop" \
  --add-data "jiv/static:jiv/static" --collect-submodules uvicorn \
  --osx-bundle-identifier com.jivdesktop.app run.py || { echo "Build failed"; exit 1; }
# ad-hoc sign so Apple Silicon will launch it locally
codesign --force --deep -s - "dist/Jiv Desktop.app" 2>/dev/null || true
xattr -dr com.apple.quarantine "dist/Jiv Desktop.app" 2>/dev/null || true
echo "Built: dist/Jiv Desktop.app"
read -r -p "Copy it to ~/Applications (and optionally the Dock)? [Y/n] " a
[[ "$a" =~ ^[Nn]$ ]] || ./scripts/mac_shortcut.sh "$(pwd)"
read -r -p "Press Enter to close…" _
