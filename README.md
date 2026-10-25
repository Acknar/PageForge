<p align="center">
  <img src="PageForge-Win/icons/pageforge.png" width="128" alt="PageForge icon">
</p>

<h1 align="center">PageForge</h1>

<p align="center">A batch workbench for PDFs and images, for Windows, macOS and Linux.</p>

PageForge loads a set of PDFs and images, applies one tool to all of them with a live
preview, and writes the results to an output folder. Cropping scanned pages, splitting
a PDF, converting images to PDF, OCR on a stack of scans, removing backgrounds: each
is one tool, run on the whole batch at once.

Every tool is a plain Python script in a folder you control. The built-in tools can be
edited, replaced or deleted, and a new tool is a single `.py` file dropped into that
folder.

The interface is available in English and French.

PageForge comes in two editions that share the same tools (Measure image is in the GNOME edition only for now):

- **GNOME edition** (`PageForge-GNOME`): GTK4 and Libadwaita, for GNOME desktops
  such as Fedora Workstation and Ubuntu. Installed as a Flatpak.
- **Qt edition** (`PageForge-Win`): for Windows, macOS, and Linux desktops other than
  GNOME. It follows each system's native look.

## Download

Get the installer for your system from the
[latest release](https://github.com/Acknar/PageForge/releases/latest).

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `PageForge-Setup-<version>.exe` |
| macOS 12+, Apple silicon (M1 and later) | `PageForge-<version>-macos-arm64.dmg` |
| macOS 12+, Intel | `PageForge-<version>-macos-x86_64.dmg` |
| Linux with GNOME, x86_64 | `PageForge-GNOME-<version>-x86_64.flatpak` |
| Linux with GNOME, ARM 64-bit | `PageForge-GNOME-<version>-aarch64.flatpak` |
| Other Linux desktops, x86_64 | `PageForge-Setup-<version>-linux-x86_64.run` |
| Other Linux desktops, ARM 64-bit | `PageForge-Setup-<version>-linux-aarch64.run` |

No separate Python install is needed: each installer carries its own.

## Install

### Windows

Run `PageForge-Setup-<version>.exe`. It installs for the current user without
administrator rights and adds a Start menu shortcut (and a desktop shortcut if you tick
it). Windows SmartScreen may warn that the publisher is unknown: choose **More info**,
then **Run anyway**. Uninstall from **Settings > Apps**.

### macOS

1. Open the `.dmg` and drag **PageForge** onto **Applications**.
2. Open PageForge. The app is not notarized by Apple, so macOS blocks the first launch.
   Go to **System Settings > Privacy & Security** and click **Open Anyway** next to the
   PageForge message. On macOS 14 and earlier, right-click the app and choose **Open**
   instead.

To uninstall, move PageForge from Applications to the Trash.

### Linux with GNOME (Flatpak)

Open <https://acknar.github.io/PageForge/> and click **Install PageForge**. GNOME
Software opens and installs it, and offers new versions as they come out. From a
terminal:

```sh
flatpak install --user https://acknar.github.io/PageForge/pageforge.flatpakref
```

The `.flatpak` file attached to each release works too (double-click it, or run
`flatpak install --user PageForge-GNOME-<version>-x86_64.flatpak`) and also receives
updates from the same repository.

The GNOME runtime it needs is downloaded from Flathub the first time. Fedora
Workstation has Flatpak and Flathub set up already. On other distributions, set up
Flatpak first: <https://flathub.org/setup>.

Tesseract is included, so the OCR (zones) tool works without installing anything
else. Uninstall from GNOME Software or with `flatpak uninstall io.github.acknar.PageForge`.

Inside the Flatpak, the scripts folder defaults to
`~/.var/app/io.github.acknar.PageForge/data/PageForge/tools`. PageForge can read and write
files in your home folder and on external drives.

Version 1.1.0 used the app ID `io.local.PageForge`. The current version copies its
settings, scripts and installed tool libraries on first launch; remove the old one
afterwards with `flatpak uninstall io.local.PageForge`.

### Other Linux desktops

```sh
sh PageForge-Setup-<version>-linux-x86_64.run
```

This installs for the current user (no root needed): the app goes to
`~/.local/opt/PageForge`, PageForge appears in the applications menu, and the
`pageforge` command is added to `~/.local/bin`. Run it as root to install for all users
in `/opt/PageForge`, or pass `--prefix DIR` to choose the folder.

To uninstall, run `~/.local/opt/PageForge/uninstall.sh` (or the installer with
`--uninstall`).

The installer checks for the few system libraries Qt needs and prints the command to
install any that are missing. It needs glibc 2.35 or newer: Ubuntu 22.04+, Debian 12+,
Fedora 36+, Linux Mint 21+, and current Arch and openSUSE Tumbleweed.

## Using PageForge

The main window works top to bottom:

1. **Files**: add files or a folder, or drop them on the window. PDFs and images
   (PNG, JPEG, WebP, BMP, TIFF) can be mixed.
2. **Tools**: pick what to do.
3. **Options**: set the tool's parameters. Many tools also let you adjust them directly
   in the preview, for example by dragging crop edges or drawing OCR zones.
4. **Output**: choose the output folder (by default an `output` folder next to your
   files), a base name, numbering, or overwriting the originals.

Then click **Process files**. The results can be loaded back in as new files to chain
another tool.

On first launch PageForge asks where to keep its tools. The default folder works for
most people.

## Built-in tools

| Tool | What it does |
|---|---|
| Auto-crop border | Straightens tilted pages, with a manual rotation adjustment, and trims a border of any chosen colour around the content. |
| Convert (image / PDF) | Images to a single PDF, images to one PDF each, or PDF pages to images. |
| Crop margins | Trims fixed margins from every page or image, with linked top/bottom and left/right values. |
| Divide into grid | Cuts each page into an N x M grid, after optional margin cropping, and can rotate the pieces. |
| OCR (EasyOCR) | Automatic OCR with a neural engine, with layout detection for columns and sections. Outputs text, CSV or a searchable PDF. |
| OCR (zones) | Tesseract OCR limited to evenly spaced columns or to zones you draw on each page. Suited to book scans and forms. Outputs text, CSV or a searchable PDF. |
| Organize / Merge | Reorders pages and combines any mix of PDFs and images into one PDF, by dragging thumbnails. |
| Remove background | Makes backgrounds transparent, either with an AI model (ISNet) or by picking a colour. Outputs WebP. |
| Rename | Renames files from a name list, with find and replace, numbering removal, lowercase and space handling. |
| Split / Extract | Splits a PDF into N files, every N pages, one file per page or at given pages; or keeps or deletes a page list. |
| Upscale | Enlarges images by a factor or to a target size, with a choice of resampling and optional sharpening. |
| Measure image | Measures distances, areas and angles on an image or PDF page, scaled from a line of known length or from the perspective of a photo, and lays the drawing out on paper at a true scale. GNOME edition only. |

### Tools that need extra components

Some tools need libraries that are not part of the installer, to keep it small. They
show up in **Settings** with an **Install deps** button; one click downloads and
installs what they need, and the tool is enabled.

| Tool | Installs |
|---|---|
| Remove background | `rembg`, `onnxruntime` (the AI model downloads on first use) |
| OCR (EasyOCR) | `easyocr`, which includes PyTorch (a large download; models download on first use) |
| OCR (zones) | `pytesseract`, plus the Tesseract program |

Tesseract is a separate program. The Windows installer and the GNOME Flatpak include
it. On macOS install it with `brew install tesseract`; for the Qt edition on Linux, with
`sudo apt install tesseract-ocr` or `sudo dnf install tesseract`.

## Adding your own tools

Open **Settings > Scripts folder > Open**. Every `.py` file in that folder that defines
a `NAME` and a `process()` function is a tool. Add a file, click **Reload** in Settings,
and it appears in the Tools list with its options rendered automatically.

The full contract (options, previews, batch tools, dependencies) is in
[`PLUGINS.md`](PageForge-Win/tools/PLUGINS.md), which is also copied into the scripts
folder.

Where PageForge keeps its files:

| System | Settings | Scripts folder (default) |
|---|---|---|
| Windows | `%APPDATA%\PageForge` | `%LOCALAPPDATA%\PageForge\tools` |
| macOS | `~/Library/Application Support/PageForge` | `~/Library/Application Support/PageForge/tools` |
| Linux | `~/.config/PageForge` | `~/.local/share/PageForge/tools` |
| Linux, GNOME Flatpak | `~/.var/app/io.github.acknar.PageForge/config/PageForge` | `~/.var/app/io.github.acknar.PageForge/data/PageForge/tools` |

Uninstalling PageForge leaves these in place.

## Running from source

Qt edition (Python 3.12 or newer; the installers bundle 3.12):

```sh
cd PageForge-Win
python -m pip install -r requirements.txt
python pageforge.py
```

GNOME edition (uses the system's GTK4, Libadwaita and PyGObject):

```sh
sudo dnf install python3-gobject gtk4 libadwaita python3-cairo   # Fedora
pip install --user pymupdf pillow numpy
python3 PageForge-GNOME/pageforge.py
```

`PageForge-GNOME/install-from-source.sh` installs it with a menu entry, and
`PageForge-GNOME/pageforge.spec` builds an RPM.

`test_headless.py` runs the whole window offscreen and exercises every tool:

```sh
python test_headless.py
```

## Building the installers

The [Build installers](.github/workflows/build-installers.yml) workflow builds all
the installers on GitHub Actions on every push to `main`, and publishes them as a GitHub
Release when a `v*` tag is pushed. Each installer bundles a standalone Python, so tools
can install their dependencies at runtime.

To build locally:

- Windows: [`BUILD-Windows.md`](PageForge-Win/build/BUILD-Windows.md), or run
  `PageForge-Win\build_local.bat`
- macOS and Linux (Qt edition): [`BUILD-macOS-Linux.md`](PageForge-Win/build/BUILD-macOS-Linux.md)
- GNOME Flatpak update repository: [`UPDATES.md`](PageForge-GNOME/flatpak/UPDATES.md)
  (signing key and GitHub Pages setup)
- GNOME Flatpak: run `sh PageForge-GNOME/flatpak/build.sh` on a Linux machine with
  `flatpak-builder` installed (`sudo dnf install flatpak-builder`). It builds and
  installs the Flatpak; add `--bundle` to also write a `.flatpak` file.

## License

PageForge is released under the [GNU Affero General Public License v3.0](LICENSE).
