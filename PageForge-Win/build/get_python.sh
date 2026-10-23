#!/usr/bin/env bash
# Download a relocatable standalone CPython (python-build-standalone) and extract
# it as <dest_dir> (the folder that holds bin/python3). macOS/Linux counterpart of
# get_python.ps1.
#
#   get_python.sh <target-triple> <dest_dir> [python-minor, default 3.12]
#
# Triples: x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu,
#          x86_64-apple-darwin, aarch64-apple-darwin
# Set GH_TOKEN to avoid GitHub API rate limits (CI does this). Written for
# bash 3.2 too (the macOS system bash).
set -euo pipefail

TRIPLE="$1"
DEST="$2"
PYVER="${3:-3.12}"

API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
AUTH=()
if [ -n "${GH_TOKEN:-}" ]; then AUTH=(-H "Authorization: Bearer $GH_TOKEN"); fi

echo "Querying python-build-standalone latest release..."
JSON="$(curl -fsSL ${AUTH[@]+"${AUTH[@]}"} -H "Accept: application/vnd.github+json" -H "User-Agent: pageforge-build" "$API")"

# Exact triple + install_only (not _stripped, not the x86_64_v2/v3 variants).
URL="$(printf '%s' "$JSON" \
  | grep -oE '"browser_download_url": *"[^"]+"' \
  | sed -E 's/.*"(https[^"]+)"/\1/' \
  | grep -E "/cpython-${PYVER//./\\.}\.[0-9]+\+[0-9]+-${TRIPLE}-install_only\.tar\.gz$" \
  | head -n 1 || true)"
if [ -z "$URL" ]; then
  echo "ERROR: no standalone CPython $PYVER asset for $TRIPLE in the latest release." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Downloading $(basename "$URL")"
curl -fL --retry 4 --retry-delay 2 -o "$TMP/py.tar.gz" "$URL"
tar -xzf "$TMP/py.tar.gz" -C "$TMP"          # extracts to $TMP/python/
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
mv "$TMP/python" "$DEST"
if [ ! -x "$DEST/bin/python3" ]; then
  echo "ERROR: $DEST/bin/python3 missing after extract." >&2
  exit 1
fi
echo "Python ready: $("$DEST/bin/python3" --version)"
