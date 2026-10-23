#!/bin/sh
# Build the PageForge Flatpak and install it for the current user.
#
#   sh build.sh            build + install
#   sh build.sh --bundle   also write PageForge.flatpak (a single file to share)
#
# Needs flatpak-builder:  sudo dnf install flatpak-builder
set -eu
cd "$(dirname "$0")"

flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --user --install-deps-from=flathub --force-clean \
    --repo=repo --install build-dir io.local.PageForge.yml

if [ "${1:-}" = "--bundle" ]; then
    flatpak build-bundle repo PageForge.flatpak io.local.PageForge \
        --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
    echo "Bundle written: $(pwd)/PageForge.flatpak"
fi

echo "Installed. Launch PageForge from the app grid, or run: flatpak run io.local.PageForge"
