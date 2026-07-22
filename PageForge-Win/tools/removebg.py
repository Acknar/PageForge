"""Remove image/PDF-page backgrounds and save transparent WebP (ISNet model)."""
from pathlib import Path

NAME = "Remove background"
ACCEPTS = ("image", "pdf")
ORDER = 90
REQUIRES = ["rembg", "onnxruntime"]      # pip packages installed on demand
MODULES = ["rembg", "onnxruntime"]        # import names used to detect if installed
PREVIEW_ON_DEMAND = True
COMPARE_PREVIEW = True            # host shows a before/after divider slider
OPTIONS = [
    {"key": "dpi", "label": "Render DPI (PDF)", "type": "int", "default": 300, "min": 72, "max": 600},
    {"key": "quality", "label": "WebP quality", "type": "int", "default": 90, "min": 1, "max": 100},
    {"key": "lossless", "label": "Lossless", "type": "bool", "default": False},
]

_S = {"session": None}


def _cut(img):
    from rembg import remove, new_session
    if _S["session"] is None:
        _S["session"] = new_session("isnet-general-use")
    return remove(img, session=_S["session"])


def preview_image(item, page_index, options):
    from PIL import Image
    item = Path(item)
    if item.suffix.lower() == ".pdf":
        import fitz
        z = 150 / 72
        pix = fitz.open(item)[page_index].get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        img = Image.open(item)
        img.thumbnail((1200, 1200))
    return _cut(img).convert("RGBA")


def process(item, output_dir, options, overwrite, progress=None):
    from PIL import Image
    item = Path(item)
    dpi, q, loss = int(options["dpi"]), int(options["quality"]), bool(options["lossless"])
    out = []
    if item.suffix.lower() == ".pdf":
        import fitz
        pdoc = fitz.open(item)
        total = pdoc.page_count
        z = dpi / 72
        for page in pdoc:
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            cut = _cut(img)
            t = Path(output_dir) / f"{item.stem}_p{page.number + 1:03d}.webp"
            cut.save(t, "WEBP", lossless=True) if loss else cut.save(t, "WEBP", quality=q)
            out.append(str(t))
            if progress:
                progress(page.number + 1, total)
    else:
        cut = _cut(Image.open(item))
        t = item.with_suffix(".webp") if overwrite else Path(output_dir) / f"{item.stem}.webp"
        cut.save(t, "WEBP", lossless=True) if loss else cut.save(t, "WEBP", quality=q)
        out.append(str(t))
    return out
