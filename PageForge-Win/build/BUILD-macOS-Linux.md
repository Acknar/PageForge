# Building and installing PageForge on macOS and Linux

The macOS and Linux installers use the same embedded-Python design as the Windows
installer (see `BUILD-Windows.md`): a relocatable standalone CPython 3.12 from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) plus
the app. The installed PageForge runs on a real Python, so tool dependencies
(rembg, onnxruntime, EasyOCR, ...) install on demand from inside the app.

The same `pageforge.py` runs on all three platforms. Only the config location, the
"Open scripts folder" command and the system-dependency guidance differ.

| Platform | Config | Default scripts folder |
|---|---|---|
| Windows | `%APPDATA%\PageForge` | `%LOCALAPPDATA%\PageForge\tools` |
| macOS | `~/Library/Application Support/PageForge` | `~/Library/Application Support/PageForge/tools` |
| Linux | `~/.config/PageForge` | `~/.local/share/PageForge/tools` |

## CI (all platforms)

`.github/workflows/build-installers.yml` builds every installer. Open the
**Actions** tab, pick "Build installers", **Run workflow**, then download from the
run's Artifacts:

| Artifact | File |
|---|---|
| PageForge-windows | `PageForge-Setup-<version>.exe` |
| PageForge-macos-arm64 | `PageForge-<version>-macos-arm64.dmg` (Apple silicon) |
| PageForge-macos-x86_64 | `PageForge-<version>-macos-x86_64.dmg` (Intel Macs) |
| PageForge-linux-x86_64 | `PageForge-Setup-<version>-linux-x86_64.run` and `.tar.gz` |
| PageForge-linux-aarch64 | `PageForge-Setup-<version>-linux-aarch64.run` and `.tar.gz` |

It also runs on every push and pull request to `main`. Pushing a tag such as `v1.1.0` additionally
publishes all installers as a GitHub Release.

Each job runs `test_headless.py` on the bundled Python before packaging. The Linux
jobs also install the `.run`, import the installed app and uninstall it again.

The version comes from `APP_VERSION` in `pageforge.py`. The Windows installer gets it
through `/DAppVersion`; the default in `build/installer.iss` is only used for a manual
compile and should be kept in sync.

## macOS

### Build locally

On a Mac (macOS 12 or later), from the repo:

```sh
bash PageForge-Win/build/build_macos.sh
```

This produces `PageForge-Win/Output/PageForge-<version>-macos-<arch>.dmg` for the
architecture of the Mac it runs on. Only built-in tools are used (`sips`,
`iconutil`, `codesign`, `hdiutil`).

Layout of the app:

```
PageForge.app/Contents/
  Info.plist
  MacOS/PageForge          shell launcher: Resources/python/bin/python3 Resources/app/pageforge.py
  Resources/
    pageforge.icns
    python/                standalone CPython + PySide6, pymupdf, pillow, numpy
    app/                   pageforge.py, tools/, icons/
```

### Install

1. Open the `.dmg` and drag **PageForge** onto **Applications**.
2. The app is ad-hoc signed but not notarized by Apple, so the first launch is
   blocked. Open it once, then go to **System Settings > Privacy & Security** and
   click **Open Anyway** next to the PageForge message. On macOS 14 and earlier,
   right-click the app in Applications and choose **Open** instead.
3. Alternative: remove the download quarantine flag in Terminal:
   ```sh
   xattr -dr com.apple.quarantine /Applications/PageForge.app
   ```

Optional tool libraries are installed into the app's own Python, so they are lost
when the app is replaced by a newer version. Reinstall them with the tool's
Install button.

The OCR (zones) tool needs Tesseract, which is not bundled on macOS:
`brew install tesseract`.

Removing a Gatekeeper prompt for good requires signing with a Developer ID
certificate and notarizing (`codesign --options runtime` with that identity, then
`xcrun notarytool submit` and `xcrun stapler staple`). That needs a paid Apple
Developer account and is not part of the build.

## Linux

### Build locally

On x86_64 or aarch64 Linux:

```sh
bash PageForge-Win/build/build_linux.sh
```

Outputs, in `PageForge-Win/Output/`:

- `PageForge-Setup-<version>-linux-<arch>.run`: self-extracting installer.
- `PageForge-<version>-linux-<arch>.tar.gz`: the same payload, for manual installs.

The build machine needs the Qt system libraries listed below, because the icons are
rendered with the bundled Qt. `PAGEFORGE_PYTHON_DIR=/path/to/python` makes the
script copy that Python tree instead of downloading one (useful offline).

### Install

```sh
sh PageForge-Setup-<version>-linux-x86_64.run
```

As a normal user this installs to `~/.local/opt/PageForge`, adds a `pageforge`
command in `~/.local/bin`, and a menu entry and icons in `~/.local/share`. No root
needed. Run as root, it installs to `/opt/PageForge`, `/usr/local/bin` and
`/usr/share`.

Options:

```sh
sh PageForge-Setup-...run --prefix ~/Apps/PageForge   # choose the install folder
sh PageForge-Setup-...run --uninstall                 # remove it
~/.local/opt/PageForge/uninstall.sh                   # same, without the installer
```

From the `.tar.gz`: extract it and run `PageForge/install.sh` with the same options.

Installing over an existing copy replaces it. Settings and the scripts folder are
never touched by install, upgrade or uninstall. Optional tool libraries installed
from inside the app are part of the replaced copy and need the Install button again.

### System libraries

The bundle contains Qt itself, but Qt needs a few libraries from the system. Most
desktops already have them. The installer checks and prints what is missing.

- Debian/Ubuntu: `sudo apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libfontconfig1 libdbus-1-3`
- Fedora: `sudo dnf install mesa-libEGL mesa-libGL libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms fontconfig dbus-libs`
- Arch: `sudo pacman -S libglvnd libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms fontconfig`

The OCR (zones) tool needs Tesseract from the distribution:
`sudo apt install tesseract-ocr` or `sudo dnf install tesseract`.

The CI build runs on Ubuntu 22.04, so the bundled libraries need glibc 2.35 or
newer on the target: Ubuntu 22.04+, Debian 12+, Fedora 36+, Linux Mint 21+ and
current Arch and openSUSE Tumbleweed.
