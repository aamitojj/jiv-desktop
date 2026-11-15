#!/bin/sh
# Build JivSetup-<version>.exe on Linux: needs makensis (apt install nsis), x86_64-w64-mingw32-gcc (apt install gcc-mingw-w64-x86-64),
# python3 + pip, curl. Usage: installer/build_installer.sh [outdir]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
OUT="${1:-$ROOT/dist}"; mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
PYVER="${PYVER:-3.12.10}"; PY="${PYTHON:-python3}"
VERSION="$($PY -c "import re;print(re.search(r'\"(.+)\"', open('$ROOT/jiv/__init__.py').read()).group(1))")"
CACHE="${JIV_BUILD_CACHE:-$HOME/.cache/jiv-build}"; mkdir -p "$CACHE"
STAGE="${JIV_STAGE:-$CACHE/stage}"; rm -rf "$STAGE"; mkdir -p "$STAGE/python" "$STAGE/app"
echo "== Jiv $VERSION: embedded Python $PYVER"
EMB="$CACHE/python-$PYVER-embed-amd64.zip"
[ -f "$EMB" ] || curl -fsSL -o "$EMB" "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip"
(cd "$STAGE/python" && unzip -q "$EMB")
PTH="$(ls "$STAGE"/python/python3*._pth)"
ZIPN="$(basename "$PTH" ._pth).zip"
printf '%s\r\n.\r\nLib\\site-packages\r\n..\\app\r\nimport site\r\n' "$ZIPN" > "$PTH"
echo "== Windows wheels (requirements.txt + pip)"
WH="$CACHE/wheels-$VERSION"; rm -rf "$WH"; mkdir -p "$WH"
$PY -m pip download -q --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp -d "$WH" \
    -r "$ROOT/requirements.txt" colorama pip
SP="$STAGE/python/Lib/site-packages"; mkdir -p "$SP"
for w in "$WH"/*.whl; do (cd "$SP" && unzip -qo "$w"); done
echo "== app source (plain .py files, updated in place by the updater)"
(cd "$ROOT" && tar --exclude=.venv --exclude=__pycache__ --exclude=screenshots --exclude=dist --exclude=build \
   --exclude='*.pem' -cf - .) | (cd "$STAGE/app" && tar -xf -)
echo "== launcher"
x86_64-w64-mingw32-windres "$HERE/launcher.rc" -O coff -o "$STAGE/launcher.res" -I "$HERE"
x86_64-w64-mingw32-gcc -O2 -s -municode -mwindows "$HERE/launcher.c" "$STAGE/launcher.res" -o "$STAGE/JivDesktop.exe"
echo "== makensis"
(cd "$HERE" && makensis -V2 -DVERSION="$VERSION" -DSTAGE="$STAGE" -DOUTFILE="$OUT/JivSetup-$VERSION.exe" jiv.nsi)
ls -la "$OUT/JivSetup-$VERSION.exe"
