#!/usr/bin/env bash
# Jiv Desktop installer for macOS and Linux
set -e
cd "$(dirname "$0")"
echo "=== Jiv Desktop installer ==="
OS="$(uname -s)"
find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      echo "$c"; return 0; fi
  done; return 1
}
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found."
  if [ "$OS" = "Darwin" ]; then
    echo "Pick one way to install it:"
    echo "  1) Homebrew:          brew install python@3.12"
    echo "  2) python.org:        https://www.python.org/downloads/macos/  (run the .pkg installer)"
    echo "  3) Xcode tools:       xcode-select --install   (gives python3 3.9+ on recent macOS; 3.10+ needed)"
    if command -v brew >/dev/null 2>&1; then
      read -r -p "Run 'brew install python@3.12' now? [y/N] " a
      if [[ "$a" =~ ^[Yy]$ ]]; then brew install python@3.12; PY="$(find_python || true)"; fi
    else
      read -r -p "Open the python.org download page? [y/N] " a
      [[ "$a" =~ ^[Yy]$ ]] && open "https://www.python.org/downloads/macos/"
    fi
  else
    echo "Install it with your package manager, e.g.:  sudo apt install python3 python3-venv   or   sudo dnf install python3"
  fi
  [ -z "$PY" ] && { echo "Re-run this installer after installing Python."; exit 1; }
fi
echo "Using $($PY --version) at $(command -v $PY)"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || { echo "venv failed. On Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
fi
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt
read -r -p "Install pywebview for a native app window (optional)? [y/N] " a
if [[ "$a" =~ ^[Yy]$ ]]; then .venv/bin/python -m pip install pywebview || echo "pywebview failed to install — Jiv will use your browser instead."; fi
chmod +x start.sh start.command install.command build_app.command 2>/dev/null || true
if [ "$OS" = "Darwin" ]; then
  read -r -p "Add 'Jiv Desktop' to ~/Applications (Launchpad/Spotlight) and the Dock? [Y/n] " a
  if [[ ! "$a" =~ ^[Nn]$ ]]; then ./scripts/mac_shortcut.sh "$(pwd)"; fi
else
  read -r -p "Create a desktop menu entry? [Y/n] " a
  if [[ ! "$a" =~ ^[Nn]$ ]]; then
    mkdir -p ~/.local/share/applications
    cat > ~/.local/share/applications/jiv-desktop.desktop <<EOD
[Desktop Entry]
Type=Application
Name=Jiv Desktop
Exec=$(pwd)/start.sh
Icon=$(pwd)/jiv/static/icon.svg
Terminal=false
Categories=Utility;
EOD
    echo "Menu entry created."
  fi
fi
echo; echo "Done! Start Jiv with ./start.sh (or double-click start.command on a Mac)."
