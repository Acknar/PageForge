#!/bin/sh
# Install PageForge from source — no rpm, no dnf, no root.
# Run:  sh install-from-source.sh
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Apps/PageForge"

echo "Installing PageForge from: $SRC"

# 1. copy the app + its tools to a stable location
mkdir -p "$HOME/Apps"
rm -rf "$APP"
mkdir -p "$APP"
cp "$SRC/pageforge.py" "$APP/"
cp -r "$SRC/tools" "$APP/"
[ -d "$SRC/icons" ] && cp -r "$SRC/icons" "$APP/"

# 2. install the app icon
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$SRC/io.github.acknar.PageForge.svg" \
   "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.acknar.PageForge.svg"

# 3. install a launcher pointing at the copy above
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/io.github.acknar.PageForge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PageForge
Comment=Batch divide, crop, auto-crop, rename, background-removal and convert for PDFs and images
Exec=python3 $APP/pageforge.py
Icon=io.github.acknar.PageForge
Terminal=false
Categories=Graphics;Utility;
Keywords=pdf;crop;split;rename;background;convert;batch;
StartupNotify=true
EOF

# 4. refresh caches (harmless if they fail)
gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo
echo "Done. Launch 'PageForge' from the app grid, or run:"
echo "    python3 $APP/pageforge.py"
echo
echo "If you don't already have the runtime dependencies, install them once:"
if command -v apt >/dev/null 2>&1; then
    echo "    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1"
elif command -v dnf >/dev/null 2>&1; then
    echo "    sudo dnf install python3-gobject gtk4 libadwaita python3-cairo"
elif command -v pacman >/dev/null 2>&1; then
    echo "    sudo pacman -S python-gobject gtk4 libadwaita python-cairo"
else
    echo "    (install the GTK4 + Libadwaita + PyGObject stack for your distro)"
fi
echo "    pip install --user pymupdf pillow numpy"
