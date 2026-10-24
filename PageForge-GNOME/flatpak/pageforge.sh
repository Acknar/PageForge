#!/bin/sh
# PageForge launcher inside the Flatpak. Tools install their extra Python
# packages with pip --user: keep those in the app's own data folder
# (~/.var/app/io.github.acknar.PageForge/data/python) rather than the shared ~/.local.
export PYTHONUSERBASE="${XDG_DATA_HOME:-$HOME/.local/share}/python"

# One-time move from the old app ID (io.local.PageForge, 1.1.0): copy its
# settings, scripts folder and tool libraries, and point the saved scripts
# folder at the new location. The old folder is left as it was.
OLD="$HOME/.var/app/io.local.PageForge"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
if [ -d "$OLD" ] && [ ! -e "$CONF/PageForge" ]; then
    mkdir -p "$CONF" "$DATA"
    [ -d "$OLD/config/PageForge" ] && cp -a "$OLD/config/PageForge" "$CONF/"
    [ -d "$OLD/data/PageForge" ] && [ ! -e "$DATA/PageForge" ] && cp -a "$OLD/data/PageForge" "$DATA/"
    [ -d "$OLD/data/python" ] && [ ! -e "$DATA/python" ] && cp -a "$OLD/data/python" "$DATA/"
    if [ -f "$CONF/PageForge/config.json" ]; then
        sed -i 's#/\.var/app/io\.local\.PageForge/#/.var/app/io.github.acknar.PageForge/#g' \
            "$CONF/PageForge/config.json"
    fi
fi

exec python3 /app/share/pageforge/pageforge.py "$@"
