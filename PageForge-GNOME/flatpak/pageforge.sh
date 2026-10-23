#!/bin/sh
# PageForge launcher inside the Flatpak. Tools install their extra Python
# packages with pip --user: keep those in the app's own data folder
# (~/.var/app/io.local.PageForge/data/python) rather than the shared ~/.local.
export PYTHONUSERBASE="${XDG_DATA_HOME:-$HOME/.local/share}/python"
exec python3 /app/share/pageforge/pageforge.py "$@"
