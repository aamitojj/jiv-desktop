#!/usr/bin/env bash
# Double-click in Finder to install Jiv Desktop on macOS
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine . 2>/dev/null || true
chmod +x install.sh start.sh start.command build_app.command scripts/*.sh 2>/dev/null || true
./install.sh
echo; read -r -p "Press Enter to close this window…" _
