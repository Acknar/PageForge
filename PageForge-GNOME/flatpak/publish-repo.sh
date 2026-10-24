#!/bin/sh
# Assemble the PageForge Flatpak repository for GitHub Pages.
#
#   FLATPAK_SITE_URL=https://acknar.github.io/PageForge \
#   publish-repo.sh <site_dir> <gpg_key_id> <repo.tar>...
#
# Each <repo.tar> holds a "repo/" OSTree repository exported by flatpak-builder
# for one architecture (commits already signed with the same key). This merges
# them into <site_dir>/repo, signs the summary, and writes the files people
# install from:
#   <site_dir>/pageforge.flatpakrepo   adds the repository
#   <site_dir>/pageforge.flatpakref    installs PageForge (one click in a browser)
#   <site_dir>/index.html              short install page
# Needs ostree, flatpak and gpg with the signing key in $GNUPGHOME.
set -eu

SITE="$1"
KEY="$2"
shift 2
BASE="${FLATPAK_SITE_URL:?set FLATPAK_SITE_URL, e.g. https://acknar.github.io/PageForge}"
BASE="${BASE%/}"
APP_ID="io.github.acknar.PageForge"
REPO="$SITE/repo"

mkdir -p "$SITE"
rm -rf "$REPO"
ostree init --mode=archive-z2 --repo="$REPO"

for tarball in "$@"; do
    tmp="$(mktemp -d)"
    tar -C "$tmp" -xf "$tarball"
    # The app itself only: the debug extension and per-repo appstream branches
    # are left out (build-update-repo regenerates appstream for the merged repo).
    for ref in $(ostree --repo="$tmp/repo" refs | grep "^app/$APP_ID/"); do
        echo "Adding $ref"
        ostree --repo="$REPO" pull-local "$tmp/repo" "$ref"
    done
    rm -rf "$tmp"
done

flatpak build-update-repo \
    --gpg-sign="$KEY" ${GNUPGHOME:+--gpg-homedir="$GNUPGHOME"} \
    --title="PageForge" \
    --comment="Batch tools for PDFs and images" \
    --homepage="https://github.com/Acknar/PageForge" \
    --icon="$BASE/icon.svg" \
    --prune --prune-depth=1 \
    "$REPO"

GPGKEY="$(gpg --export "$KEY" | base64 -w0)"

cat > "$SITE/pageforge.flatpakrepo" <<EOF
[Flatpak Repo]
Title=PageForge
Url=$BASE/repo/
Homepage=https://github.com/Acknar/PageForge
Comment=Batch tools for PDFs and images
Description=Updates for the GNOME edition of PageForge.
Icon=$BASE/icon.svg
GPGKey=$GPGKEY
EOF

cat > "$SITE/pageforge.flatpakref" <<EOF
[Flatpak Ref]
Name=$APP_ID
Branch=master
Title=PageForge
Url=$BASE/repo/
SuggestRemoteName=pageforge
RuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo
IsRuntime=false
GPGKey=$GPGKEY
EOF

HERE="$(cd "$(dirname "$0")" && pwd)"
cp "$HERE/../$APP_ID.svg" "$SITE/icon.svg"
touch "$SITE/.nojekyll"

cat > "$SITE/index.html" <<EOF
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PageForge for GNOME</title>
<style>
  :root { color-scheme: light dark; --fg: #231f1f; --bg: #faf8f7; --muted: #6d6563; --accent: #c8423f; }
  @media (prefers-color-scheme: dark) { :root { --fg: #efe9e7; --bg: #181515; --muted: #a59c99; --accent: #f07a75; } }
  body { font: 16px/1.6 system-ui, sans-serif; color: var(--fg); background: var(--bg); max-width: 40rem; margin: 0 auto; padding: 3rem 1rem; }
  img { width: 96px; height: 96px; }
  h1 { margin: .5rem 0 0; }
  a.button { display: inline-block; background: var(--accent); color: var(--bg); padding: .6rem 1.1rem; border-radius: .5rem; text-decoration: none; font-weight: 600; }
  code { font-size: .9em; }
  pre { overflow-x: auto; padding: .8rem; border-radius: .5rem; background: rgba(127,127,127,.12); }
  p.muted { color: var(--muted); }
</style>
<img src="icon.svg" alt="">
<h1>PageForge</h1>
<p>Batch tools for PDFs and images, in the GNOME edition.</p>
<p><a class="button" href="pageforge.flatpakref">Install PageForge</a></p>
<p class="muted">Opens in GNOME Software. Updates arrive through this repository.</p>
<p>Or from a terminal:</p>
<pre><code>flatpak install --user $BASE/pageforge.flatpakref</code></pre>
<p>To add the repository only:</p>
<pre><code>flatpak remote-add --user --if-not-exists pageforge $BASE/pageforge.flatpakrepo</code></pre>
<p><a href="https://github.com/Acknar/PageForge">Source code and other platforms</a></p>
</html>
EOF

echo "Site ready in $SITE:"
ostree --repo="$REPO" refs
du -sh "$REPO"
