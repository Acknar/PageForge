# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PageForge (Windows edition) — FULL build.
#
# This "bundle everything" build freezes the heavy optional stacks (Tesseract via
# pytesseract, EasyOCR/PyTorch, rembg/onnxruntime) into the app so every tool works
# with no user setup. It is large. Build it with the CI workflow
# (.github/workflows/build-windows.yml), which installs the CPU-only heavy deps and
# fetches the Tesseract binary first.
#
#     pyinstaller build\pageforge.spec --noconfirm
#
# Output: dist\PageForge\PageForge.exe  (one-folder). Feed dist\PageForge to Inno
# Setup (build\installer.iss) to produce the installer.
#
# Tools are NOT compiled in — tools\ is bundled only as SEED scripts copied into the
# user's editable scripts folder on first run.

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

datas = [
    (os.path.join(ROOT, "tools"), "tools"),
    (os.path.join(ROOT, "icons"), "icons"),
]
binaries = []
hiddenimports = ["PIL", "numpy"] + collect_submodules("fitz")

# A bundled Tesseract (tesseract.exe + tessdata) placed at ROOT\tesseract by the
# CI workflow. Optional: if absent, the build still works (OCR-zones then needs a
# system Tesseract). _wire_bundled_tesseract() in pageforge.py adds it to PATH.
_tess = os.path.join(ROOT, "tesseract")
if os.path.isdir(_tess):
    datas.append((_tess, "tesseract"))

# Pull each heavy package in fully (code, data files, dynamic submodules, binaries).
# Any not installed at build time is skipped, so a lighter build still works.
for pkg in ("torch", "torchvision", "easyocr", "onnxruntime", "rembg",
            "skimage", "scipy", "cv2", "shapely", "pooch", "llvmlite", "numba"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # package not present in this build env
        print(f"[pageforge.spec] skipping {pkg}: {exc}")

a = Analysis(
    [os.path.join(ROOT, "pageforge.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PageForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # GUI app — no console window
    icon=os.path.join(ROOT, "icons", "pageforge.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PageForge",
)
