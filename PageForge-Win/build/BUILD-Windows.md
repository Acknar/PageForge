# Building PageForge for Windows (embedded-Python build)

The installer bundles a **real, relocatable standalone Python** plus the app, so the
installed PageForge runs on an actual Python interpreter. This keeps PageForge's core
design intact on Windows: **tool dependencies install on demand at runtime** (the
"Install deps" button runs the bundled Python's own pip, and the packages are
immediately importable). No PyInstaller freeze, no multi-GB pre-bundle.

The easy path is CI — see `.github/workflows/build-windows.yml`. Push the repo, open the
**Actions** tab → "Build Windows app + installer" → **Run workflow**, and download
`PageForge-Setup-<version>.exe` from the run's Artifacts. Everything below is the manual
equivalent, on a Windows machine.

## What the build assembles

```
dist\PageForge\
  python\        a relocatable standalone CPython 3.12 (from python-build-standalone),
                 with the base deps (PySide6, pymupdf, pillow, numpy) pre-installed
  app\
    pageforge.py
    tools\       SEED scripts + PLUGINS.md
    icons\
```

The Start-menu shortcut launches `python\pythonw.exe "app\pageforge.py"` (no console).
At runtime, enabling a tool that needs extra libraries installs them into the same
Python via pip (to the per-user site), so they load immediately.

## Manual build

1. Download a relocatable CPython from
   [python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases)
   — the `cpython-3.12.*-x86_64-pc-windows-msvc-install_only.tar.gz` asset — and extract
   it so you have a `python\` folder with `python.exe`.
2. Install the base deps into it:
   ```bat
   python\python.exe -m pip install --upgrade pip
   python\python.exe -m pip install -r requirements.txt
   ```
3. Assemble the layout above into `dist\PageForge\` (`app\` gets `pageforge.py`, `tools\`,
   `icons\`; move `python\` in beside it).
4. Compile the installer with Inno Setup 6:
   ```bat
   iscc /O"Output" build\installer.iss
   ```
   → `Output\PageForge-Setup-1.7.1.exe` (per-user install, no admin; Start-menu shortcut).

## Optional tool dependencies

Because the app runs on a real Python, heavy/optional stacks install **only when the user
enables that tool**, on demand:

- **Remove background** → `rembg` + `onnxruntime` (pip, on demand).
- **OCR (EasyOCR)** → `easyocr` (pulls CPU PyTorch; downloads its model on first run).
- **OCR (zones)**, if any tool still uses it → needs the native **Tesseract** program,
  which pip can't provide. `pageforge.py` has `_wire_bundled_tesseract()`: drop a
  `tesseract\` folder (with `tesseract.exe` + `tessdata\`) into `app\` and it's put on
  PATH automatically. Only needed if a tool imports `pytesseract`.

## Notes / alternatives

- `build\pageforge.spec` is a PyInstaller spec kept for reference (a frozen one-file-ish
  build). It is **not** used by the CI workflow and does **not** support on-demand
  installs — prefer the embedded-Python build above.
- Version bumps: keep `APP_VERSION` in `pageforge.py` and `AppVersion` in
  `build\installer.iss` in sync.
