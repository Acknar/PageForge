"""Detect content and trim a border of a chosen colour (any colour, not just black)."""
from pathlib import Path
import numpy as np

NAME = "Auto-crop border"
ACCEPTS = ("image", "pdf")
ORDER = 10
OPTIONS = [
    {"key": "color", "label": "Border colour", "type": "color", "default": "#000000"},
    {"key": "thresh", "label": "Tolerance", "type": "int", "default": 40, "min": 0, "max": 255},
    {"key": "pad", "label": "Padding", "type": "int", "default": 0, "min": -200, "max": 200},
]


def _rgb(hexstr):
    h = (hexstr or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _content_bbox(arr, color, thr):
    # content = pixels that DIFFER from the border colour by more than the tolerance
    diff = np.abs(arr.astype(np.int16) - np.array(color, dtype=np.int16)).max(axis=2)
    mask = diff > thr
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def preview(item, page_index, options, context):
    """Show the auto-detected keep-region the same way Crop/Divide do: the kept
    rectangle outlined, with the trimmed-away border hatched red (a cropgrid with
    no interior lines). The region is detected automatically, so there are no
    draggable handles."""
    item = Path(item)
    color = _rgb(options.get("color", "#000000"))
    thr, pad = int(options["thresh"]), float(options["pad"])
    if item.suffix.lower() == ".pdf":
        import fitz
        z = 100 / 72
        pix = fitz.open(item)[page_index].get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        b = _content_bbox(arr, color, thr)
        if not b:
            return None
        rect = (b[0] / z - pad, b[1] / z - pad, b[2] / z + pad, b[3] / z + pad)
        space = "points"
    else:
        from PIL import Image
        arr = np.asarray(Image.open(item).convert("RGB"))
        b = _content_bbox(arr, color, thr)
        if not b:
            return None
        rect = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
        space = "pixels"
    x0, y0, x1, y1 = rect
    return {"cropgrid": {"rect": (x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)),
                         "cols": 1, "rows": 1},
            "space": space}


def process(item, output_dir, options, overwrite, progress=None):
    item = Path(item)
    color = _rgb(options.get("color", "#000000"))
    thr, pad = int(options["thresh"]), int(options["pad"])
    if item.suffix.lower() == ".pdf":
        import fitz
        doc, out = fitz.open(item), fitz.open()
        z = 150 / 72
        total = doc.page_count
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csRGB, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            b = _content_bbox(arr, color, thr)
            if not b:
                out.insert_pdf(doc, from_page=page.number, to_page=page.number)
                continue
            R = page.rect
            clip = fitz.Rect(R.x0 + b[0] / z - pad, R.y0 + b[1] / z - pad,
                             R.x0 + b[2] / z + pad, R.y0 + b[3] / z + pad) & R
            if clip.is_empty or clip.width < 1 or clip.height < 1:
                out.insert_pdf(doc, from_page=page.number, to_page=page.number)
                continue
            p = out.new_page(width=clip.width, height=clip.height)
            p.show_pdf_page(p.rect, doc, page.number, clip=clip)
            if progress:
                progress(page.number + 1, total)
        target = item if overwrite else Path(output_dir) / f"{item.stem}.pdf"
        out.save(target)
        return [str(target)]
    from PIL import Image
    img = Image.open(item).convert("RGB")
    b = _content_bbox(np.asarray(img), color, thr)
    target = item if overwrite else Path(output_dir) / f"{item.stem}{item.suffix}"
    if not b:
        Image.open(item).save(target)
        return [str(target)]
    W, H = img.size
    box = (max(0, b[0] - pad), max(0, b[1] - pad), min(W, b[2] + pad), min(H, b[3] + pad))
    Image.open(item).crop(box).save(target)
    return [str(target)]
