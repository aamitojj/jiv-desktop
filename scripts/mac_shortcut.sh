#!/usr/bin/env bash
# Creates ~/Applications/Jiv Desktop.app (a tiny launcher) and optionally adds it to the Dock.
DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
APP="$HOME/Applications/Jiv Desktop.app"
mkdir -p "$HOME/Applications"
if [ -d "$DIR/dist/Jiv Desktop.app" ]; then
  rm -rf "$APP"; cp -R "$DIR/dist/Jiv Desktop.app" "$APP"
else
  rm -rf "$APP"
  osacompile -o "$APP" -e "do shell script \"cd \" & quoted form of \"$DIR\" & \" && ./start.sh >/dev/null 2>&1 &\"" || { echo "osacompile failed"; exit 1; }
fi
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
echo "Created: $APP  (find it in Launchpad / Spotlight)"
read -r -p "Add it to the Dock too? [y/N] " a
if [[ "$a" =~ ^[Yy]$ ]]; then
  defaults write com.apple.dock persistent-apps -array-add "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$APP</string><key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>"
  killall Dock; echo "Added to the Dock."
fi
