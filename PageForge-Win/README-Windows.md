# PageForge — Windows edition

A batch PDF & image workbench with a **native Windows 11 look**. This is a standalone
Windows fork of PageForge, re-hosted on PySide6/Qt so it uses the system title bar,
controls, accent colour and light/dark theme directly — no translated GNOME/Adwaita
styling.

## The one thing that stays the same: your tools

Every feature is a plain Python script in your **scripts folder**
(`%LOCALAPPDATA%\PageForge\tools` by default). The app is just the host/framework: it
loads whatever `.py` files are in that folder, and you can add, edit, or delete them
freely and hit **Reload**. Tools are never compiled into the app.

**Tools are cross-platform.** The tool contract is byte-for-byte identical to the Linux
build, and tools import no UI toolkit, so the same script runs on both — copy scripts
between a Linux and a Windows PageForge scripts folder in either direction. See
`tools\PLUGINS.md` (also seeded into your scripts folder) for the contract.

## Run from source

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python pageforge.py
```

## Build an installer

See `build\BUILD-Windows.md` — PyInstaller (`build\pageforge.spec`) then Inno Setup
(`build\installer.iss`) produce `PageForge-Setup-<version>.exe`.

## What's different from the Linux build

- **UI toolkit:** PySide6/Qt instead of GTK4/Libadwaita → native Windows theming.
- **Config:** `%APPDATA%\PageForge\config.json`; default tools in `%LOCALAPPDATA%\PageForge\tools`.
- **Packaging:** the installer bundles a real, relocatable standalone Python plus the
  app (not a frozen `.exe`), so pip extras (`REQUIRES`) install **on demand at runtime**
  into the app's own Python and load immediately — exactly like the Linux from-source
  model. Native components (`SYSTEM_REQUIRES`, e.g. Tesseract) still can't come from pip;
  drop a `tesseract\` folder into `app\` to bundle it (see build docs). There is no
  `pkexec` step.
- **Everything else** — the four-step Files/Tools/Options/Output flow, every option type,
  the canvas preview (overlays, drag handles, zones, before/after compare slider), the
  thumbnail grid (reorder, select, split bars, insertion bar), naming/numbering, English/
  French — is the same.

## Status

The host is verified headlessly (`test_headless.py`, run under
`QT_QPA_PLATFORM=offscreen`): tool loading, every option type + rules, all preview
overlays, handle-drag, zones, the grid + sequence handoff, before/after compare, detected
regions, a real end-to-end `process()` with numbering, and the live language switch all
pass. The sandbox has no display, so **actual mouse dragging** (resize handles, grid
drag-drop, split-bar drag, compare divider) still needs a quick manual pass on Windows —
see the live-test checklist in the project handoff.
