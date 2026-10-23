"""Render icons/pageforge.svg to square PNGs at the sizes the macOS .icns and the
Linux hicolor theme need. Uses the bundled PySide6 (QtSvg), so it runs with the
same interpreter that is being packaged; no extra tools required.

    python render_icons.py <out_dir> [size ...]

Writes <out_dir>/pageforge_<size>.png for each size (default: 16 32 48 64 128 256 512 1024).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SVG = Path(__file__).resolve().parent.parent / "icons" / "pageforge.svg"


def main():
    out = Path(sys.argv[1])
    sizes = [int(s) for s in sys.argv[2:]] or [16, 32, 48, 64, 128, 256, 512, 1024]
    out.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication(sys.argv[:1])  # noqa: F841  (QtSvg needs a Gui app)
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        raise SystemExit(f"cannot read {SVG}")
    for size in sizes:
        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        renderer.render(p)
        p.end()
        dst = out / f"pageforge_{size}.png"
        if not img.save(str(dst)):
            raise SystemExit(f"failed to write {dst}")
        print(f"  {dst.name}")


if __name__ == "__main__":
    main()
