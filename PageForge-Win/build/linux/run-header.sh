#!/bin/sh
# PageForge @VERSION@ installer for Linux (@ARCH@).
#
#   sh PageForge-Setup-@VERSION@-linux-@ARCH@.run                 install for this user
#   sh PageForge-Setup-@VERSION@-linux-@ARCH@.run --prefix DIR    install into DIR
#   sh PageForge-Setup-@VERSION@-linux-@ARCH@.run --uninstall     remove it
#
# Everything after the __PAGEFORGE_PAYLOAD__ line is a .tar.gz of the app.
set -eu
LINE="$(awk '/^__PAGEFORGE_PAYLOAD__$/ { print NR + 1; exit }' "$0")"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pageforge-setup.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM
echo "Unpacking PageForge @VERSION@ ..."
tail -n +"$LINE" "$0" | tar -xzf - -C "$TMP"
PAGEFORGE_SETUP_MOVE=1 sh "$TMP/PageForge/install.sh" "$@"
exit 0
__PAGEFORGE_PAYLOAD__
