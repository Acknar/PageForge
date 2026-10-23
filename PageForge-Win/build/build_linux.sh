#!/usr/bin/env bash
# ============================================================================
#  PageForge - Linux build (embedded-Python), same design as the Windows build:
#  a relocatable standalone CPython + the app, so tool dependencies install on
#  demand at runtime.
#
#  Run from anywhere:   PageForge-Win/build/build_linux.sh
#  Produces (in PageForge-Win/Output/):
#    PageForge-Setup-<version>-linux-<arch>.run   self-extracting installer
#    PageForge-<version>-linux-<arch>.tar.gz      same payload, with install.sh
#
#  Needs: x86_64 or aarch64 Linux, bash, curl, tar, internet.
#  PAGEFORGE_PYTHON_DIR=/path/to/python  uses that Python tree instead of
#  downloading one (it must be relocatable and have bin/python3).
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYVER="${PYVER:-3.12}"
VERSION="$(sed -nE 's/^APP_VERSION = "([^"]+)".*/\1/p' "$ROOT/pageforge.py" | head -n 1)"
[ -n "$VERSION" ] || { echo "ERROR: APP_VERSION not found in pageforge.py" >&2; exit 1; }

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x86_64";  TRIPLE="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) ARCH="aarch64"; TRIPLE="aarch64-unknown-linux-gnu" ;;
    *) echo "ERROR: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

WORK="$ROOT/dist/linux"
STAGE="$WORK/PageForge"
OUT="$ROOT/Output"
echo "=== PageForge $VERSION for Linux $ARCH ==="
rm -rf "$WORK"
mkdir -p "$STAGE/app" "$OUT"

echo
echo "=== [1/4] Standalone Python $PYVER ==="
if [ -n "${PAGEFORGE_PYTHON_DIR:-}" ]; then
    cp -a "$PAGEFORGE_PYTHON_DIR" "$STAGE/python"
else
    "$HERE/get_python.sh" "$TRIPLE" "$STAGE/python" "$PYVER"
fi
PY="$STAGE/python/bin/python3"

echo
echo "=== [2/4] Base dependencies ==="
"$PY" -m pip install --disable-pip-version-check --upgrade pip
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location -r "$ROOT/requirements.txt"

echo
echo "=== [3/4] Assembling the app ==="
cp "$ROOT/pageforge.py" "$STAGE/app/"
cp -R "$ROOT/tools" "$STAGE/app/tools"
cp -R "$ROOT/icons" "$STAGE/app/icons"
find "$STAGE/app" -name "__pycache__" -type d -prune -exec rm -rf {} +
"$PY" "$HERE/render_icons.py" "$STAGE/app/icons/hicolor" 16 32 48 64 128 256 512
cp "$HERE/linux/install.sh" "$HERE/linux/pageforge" "$HERE/linux/pageforge.desktop.in" "$STAGE/"
chmod +x "$STAGE/install.sh" "$STAGE/pageforge"
echo "$VERSION" > "$STAGE/VERSION"
cp "$ROOT/../LICENSE" "$STAGE/LICENSE" 2>/dev/null || true
"$PY" -m compileall -q "$STAGE/app" >/dev/null || true

echo
echo "=== [4/4] Packaging ==="
TARBALL="$OUT/PageForge-$VERSION-linux-$ARCH.tar.gz"
RUNFILE="$OUT/PageForge-Setup-$VERSION-linux-$ARCH.run"
tar -C "$WORK" -czf "$TARBALL" PageForge
sed -e "s|@VERSION@|$VERSION|g" -e "s|@ARCH@|$ARCH|g" "$HERE/linux/run-header.sh" > "$RUNFILE"
cat "$TARBALL" >> "$RUNFILE"
chmod +x "$RUNFILE"

echo
echo "Done:"
ls -lh "$RUNFILE" "$TARBALL"
echo "Run the app without installing:  $STAGE/pageforge"
