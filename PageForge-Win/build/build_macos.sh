#!/usr/bin/env bash
# ============================================================================
#  PageForge - macOS build (embedded-Python), same design as the Windows build:
#  a relocatable standalone CPython + the app inside PageForge.app, so tool
#  dependencies install on demand at runtime (into the app's own Python).
#
#  Run on a Mac:   PageForge-Win/build/build_macos.sh
#  Produces (in PageForge-Win/Output/):
#    PageForge-<version>-macos-<arm64|x86_64>.dmg   drag-to-Applications disk image
#
#  Builds for the architecture of the Mac it runs on (Apple silicon -> arm64,
#  Intel -> x86_64). Needs: macOS 12+, curl, internet. Uses only built-in tools
#  (sips, iconutil, codesign, hdiutil).
#  The app is ad-hoc signed, not notarized: see BUILD-macOS-Linux.md for the
#  first-launch steps.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYVER="${PYVER:-3.12}"
VERSION="$(sed -nE 's/^APP_VERSION = "([^"]+)".*/\1/p' "$ROOT/pageforge.py" | head -n 1)"
[ -n "$VERSION" ] || { echo "ERROR: APP_VERSION not found in pageforge.py" >&2; exit 1; }

case "$(uname -m)" in
    arm64)  ARCH="arm64";  TRIPLE="aarch64-apple-darwin" ;;
    x86_64) ARCH="x86_64"; TRIPLE="x86_64-apple-darwin" ;;
    *) echo "ERROR: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

WORK="$ROOT/dist/macos"
APP="$WORK/PageForge.app"
CONTENTS="$APP/Contents"
RES="$CONTENTS/Resources"
OUT="$ROOT/Output"
echo "=== PageForge $VERSION for macOS $ARCH ==="
rm -rf "$WORK"
mkdir -p "$CONTENTS/MacOS" "$RES/app" "$OUT"

echo
echo "=== [1/5] Standalone Python $PYVER ==="
"$HERE/get_python.sh" "$TRIPLE" "$RES/python" "$PYVER"
PY="$RES/python/bin/python3"

echo
echo "=== [2/5] Base dependencies ==="
"$PY" -m pip install --disable-pip-version-check --upgrade pip
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location -r "$ROOT/requirements.txt"

echo
echo "=== [3/5] Assembling PageForge.app ==="
cp "$ROOT/pageforge.py" "$RES/app/"
cp -R "$ROOT/tools" "$RES/app/tools"
cp -R "$ROOT/icons" "$RES/app/icons"
find "$RES/app" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$HERE/macos/launcher" "$CONTENTS/MacOS/PageForge"
chmod +x "$CONTENTS/MacOS/PageForge"
sed -e "s|@VERSION@|$VERSION|g" "$HERE/macos/Info.plist.in" > "$CONTENTS/Info.plist"
printf 'APPL????' > "$CONTENTS/PkgInfo"
cp "$ROOT/../LICENSE" "$RES/LICENSE" 2>/dev/null || true

# .icns from the SVG (rendered by the bundled Qt), via the system iconutil.
ICONS_TMP="$WORK/icons"
ICONSET="$WORK/pageforge.iconset"
"$PY" "$HERE/render_icons.py" "$ICONS_TMP" 16 32 64 128 256 512 1024
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
    cp "$ICONS_TMP/pageforge_$s.png" "$ICONSET/icon_${s}x${s}.png"
    cp "$ICONS_TMP/pageforge_$((s * 2)).png" "$ICONSET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$RES/pageforge.icns"
rm -rf "$ICONS_TMP" "$ICONSET"

# Precompile so first launch does not write .pyc files inside the signed bundle.
"$PY" -m compileall -q "$RES/app" "$RES/python/lib" >/dev/null || true

echo
echo "=== [4/5] Ad-hoc code signing ==="
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" && echo "Signature OK (ad-hoc)."

echo
echo "=== [5/5] Disk image ==="
DMG="$OUT/PageForge-$VERSION-macos-$ARCH.dmg"
STAGE="$WORK/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/PageForge.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "PageForge $VERSION" -srcfolder "$STAGE" -fs HFS+ \
    -format UDZO -imagekey zlib-level=9 -ov "$DMG"
rm -rf "$STAGE"

echo
echo "Done:"
ls -lh "$DMG"
echo "Run the app without installing:  open \"$APP\""
