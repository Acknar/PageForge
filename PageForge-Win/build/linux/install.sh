#!/bin/sh
# PageForge installer for Linux. Ships inside the PageForge payload folder (next
# to python/ and app/) and is run by the self-extracting .run file, or directly
# from an extracted .tar.gz:
#
#   ./install.sh                  per-user install (no root needed)
#   ./install.sh --prefix DIR     install the app into DIR instead
#   ./install.sh --uninstall      remove an existing install
#
# Per-user (default):  app in ~/.local/opt/PageForge, launcher in ~/.local/bin,
#                      menu entry + icons in ~/.local/share
# As root:             app in /opt/PageForge, launcher in /usr/local/bin,
#                      menu entry + icons in /usr/share
#
# Your settings and scripts folder (~/.config/PageForge, ~/.local/share/PageForge)
# are never touched by install, upgrade or uninstall.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="PageForge"

if [ "$(id -u)" = "0" ]; then
    PREFIX="/opt/$APP_NAME"
    BIN_DIR="/usr/local/bin"
    SHARE_DIR="/usr/share"
else
    PREFIX="$HOME/.local/opt/$APP_NAME"
    BIN_DIR="$HOME/.local/bin"
    SHARE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
fi
APPS_DIR="$SHARE_DIR/applications"
ICON_ROOT="$SHARE_DIR/icons/hicolor"
ICON_SIZES="16 32 48 64 128 256 512"
MODE="install"

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --prefix=*) PREFIX="${1#--prefix=}"; shift ;;
        --uninstall) MODE="uninstall"; shift ;;
        -h|--help)
            sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
        && gtk-update-icon-cache -q -t "$ICON_ROOT" 2>/dev/null || true
}

remove_install() {
    # Only delete a folder that is recognisably a PageForge install.
    if [ -d "$PREFIX" ]; then
        if [ -f "$PREFIX/app/pageforge.py" ] && [ -d "$PREFIX/python" ]; then
            rm -rf "$PREFIX"
        else
            echo "ERROR: $PREFIX exists but does not look like a PageForge install; not touching it." >&2
            exit 1
        fi
    fi
    rm -f "$APPS_DIR/pageforge.desktop"
    for s in $ICON_SIZES; do
        rm -f "$ICON_ROOT/${s}x${s}/apps/pageforge.png"
    done
    # Remove the launcher link only if it points at this install.
    if [ -L "$BIN_DIR/pageforge" ] && [ "$(readlink "$BIN_DIR/pageforge")" = "$PREFIX/pageforge" ]; then
        rm -f "$BIN_DIR/pageforge"
    fi
}

if [ "$MODE" = "uninstall" ]; then
    echo "Removing $APP_NAME from $PREFIX ..."
    remove_install
    refresh_caches
    echo "$APP_NAME has been uninstalled. Settings and scripts were kept."
    exit 0
fi

# --------------------------------------------------------------------------- #
# Install / upgrade
# --------------------------------------------------------------------------- #
if [ ! -f "$SRC/app/pageforge.py" ] || [ ! -x "$SRC/python/bin/python3" ]; then
    echo "ERROR: run this from the extracted PageForge folder (python/ and app/ missing)." >&2
    exit 1
fi
VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo "?")"
echo "Installing $APP_NAME $VERSION into $PREFIX ..."

if [ -d "$PREFIX" ]; then
    echo "  Replacing the existing install (optional tool libraries you added"
    echo "  from inside the app will need the Install button again)."
    remove_install
fi

mkdir -p "$(dirname "$PREFIX")"
# The .run installer unpacks to a throwaway temp folder, so it can be moved
# (no second copy on disk). An extracted .tar.gz is copied and left as it was.
if [ "${PAGEFORGE_SETUP_MOVE:-0}" = "1" ] && mv "$SRC" "$PREFIX" 2>/dev/null; then
    :
else
    mkdir -p "$PREFIX"
    cp -a "$SRC/." "$PREFIX/"
fi

# Launcher on PATH.
mkdir -p "$BIN_DIR"
ln -sfn "$PREFIX/pageforge" "$BIN_DIR/pageforge"

# Icons.
for s in $ICON_SIZES; do
    if [ -f "$PREFIX/app/icons/hicolor/pageforge_$s.png" ]; then
        mkdir -p "$ICON_ROOT/${s}x${s}/apps"
        cp "$PREFIX/app/icons/hicolor/pageforge_$s.png" "$ICON_ROOT/${s}x${s}/apps/pageforge.png"
    fi
done

# Menu entry.
mkdir -p "$APPS_DIR"
sed -e "s|@EXEC@|$PREFIX/pageforge|g" -e "s|@VERSION@|$VERSION|g" \
    "$PREFIX/pageforge.desktop.in" > "$APPS_DIR/pageforge.desktop"
chmod 644 "$APPS_DIR/pageforge.desktop"
refresh_caches

# Record the paths used, so the bundled uninstall.sh removes the right files.
cat > "$PREFIX/uninstall.sh" <<UNINSTALL
#!/bin/sh
# Removes $APP_NAME installed in $PREFIX. Settings and scripts are kept.
set -eu
PREFIX="$PREFIX"
APPS_DIR="$APPS_DIR"
ICON_ROOT="$ICON_ROOT"
BIN_DIR="$BIN_DIR"
rm -f "\$APPS_DIR/pageforge.desktop"
for s in $ICON_SIZES; do rm -f "\$ICON_ROOT/\${s}x\${s}/apps/pageforge.png"; done
if [ -L "\$BIN_DIR/pageforge" ] && [ "\$(readlink "\$BIN_DIR/pageforge")" = "\$PREFIX/pageforge" ]; then
    rm -f "\$BIN_DIR/pageforge"
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "\$APPS_DIR" 2>/dev/null || true
rm -rf "\$PREFIX"
echo "$APP_NAME has been uninstalled. Settings and scripts were kept."
UNINSTALL
chmod +x "$PREFIX/uninstall.sh"

# --------------------------------------------------------------------------- #
# Check the system libraries Qt needs (they are not part of the bundle).
# --------------------------------------------------------------------------- #
MISSING=""
for lib in "$PREFIX"/python/lib/python3*/site-packages/PySide6/Qt/lib/libQt6Gui.so.6 \
           "$PREFIX"/python/lib/python3*/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so \
           "$PREFIX"/python/lib/python3*/site-packages/PySide6/Qt/plugins/platforms/libqwayland*.so; do
    [ -f "$lib" ] || continue
    m="$(ldd "$lib" 2>/dev/null | awk '/not found/ { print $1 }')"
    [ -n "$m" ] && MISSING="$MISSING $m"
done
MISSING="$(echo "$MISSING" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ')"

echo ""
echo "$APP_NAME $VERSION installed."
echo "  Start it from your applications menu, or run: pageforge"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  ($BIN_DIR is not on your PATH; the full path is $PREFIX/pageforge)" ;;
esac
echo "  Uninstall with: $PREFIX/uninstall.sh"

if [ -n "$MISSING" ]; then
    echo ""
    echo "WARNING: some system libraries Qt needs are missing:"
    echo "  $MISSING"
    echo "Install them with your package manager, for example:"
    echo "  Debian/Ubuntu: sudo apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libfontconfig1 libdbus-1-3"
    echo "  Fedora:        sudo dnf install mesa-libEGL mesa-libGL libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms fontconfig dbus-libs"
    echo "  Arch:          sudo pacman -S libglvnd libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms fontconfig"
fi
