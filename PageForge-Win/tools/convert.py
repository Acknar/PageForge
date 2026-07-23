"""Batch export between images and PDF."""
import re
from pathlib import Path

NAME = "Convert (image \u2194 PDF)"
ACCEPTS = ("image", "pdf")
ORDER = 60
BATCH = True
OPTIONS = [
    {"key": "mode", "label": "Mode", "type": "choice", "default": "Images \u2192 single PDF",
     "choices": ["Images \u2192 single PDF", "Images \u2192 one PDF each", "PDF \u2192 images"]},
    {"key": "format", "label": "Image format", "type": "choice",
     "choices": ["png", "webp", "jpeg"], "default": "png"},
    {"key": "dpi", "label": "Render DPI", "type": "int", "default": 200, "min": 72, "max": 600},
]

_IMG = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _natkey(p):
    m = re.search(r"(\d+)", Path(p).name)
    return (int(m.group(1)) if m else 0, Path(p).name.lower())


def warning(context, options):
    """Tell the host when the chosen Mode doesn't match the loaded file kinds
    (used by the PageForge warning tooltip). Returns a message or None."""
    kinds = {("pdf" if str(f).lower().endswith(".pdf") else "image")
             for f in context.get("files", [])}
    mode = options.get("mode", "")
    if mode == "PDF → images" and "pdf" not in kinds:
        return "This mode converts PDFs to images, but you've loaded images."
    if mode.startswith("Images") and "image" not in kinds:
        return "This mode converts images to PDF, but you've loaded PDF files."
    return None


def process(items, output_dir, options, overwrite, progress=None):
    from PIL import Image
    mode, fmt, dpi = options["mode"], options["format"], int(options["dpi"])
    imgs = [Path(f) for f in items if Path(f).suffix.lower() in _IMG]
    pdfs = [Path(f) for f in items if Path(f).suffix.lower() == ".pdf"]
    out = []
    if mode.startswith("Images"):
        if not imgs:
            return []
        if "single" in mode:
            ims = [Image.open(p).convert("RGB") for p in sorted(imgs, key=_natkey)]
            t = Path(output_dir) / "combined.pdf"
            ims[0].save(t, save_all=True, append_images=ims[1:])
            out.append(str(t))
        else:
            for p in imgs:
                t = Path(output_dir) / f"{p.stem}.pdf"
                Image.open(p).convert("RGB").save(t)
                out.append(str(t))
    else:
        import fitz
        for p in pdfs:
            z = dpi / 72
            pdoc = fitz.open(p)
            total = pdoc.page_count
            for page in pdoc:
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                t = Path(output_dir) / f"{p.stem}_p{page.number + 1:03d}.{fmt}"
                im.save(t)
                out.append(str(t))
                if progress:
                    progress(page.number + 1, total)
    return out
