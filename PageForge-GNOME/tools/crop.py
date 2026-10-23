"""Trim fixed margins off every page/image. PDF values are points; image values are pixels.
Use the chain buttons to link Top↔Bottom and Left↔Right."""
from pathlib import Path

NAME = "Crop margins"
ACCEPTS = ("image", "pdf")
ORDER = 20
OPTIONS = [
    {"key": "top", "label": "Top", "type": "int", "default": 25, "min": 0, "max": 10000,
     "link_with": "bottom"},
    {"key": "bottom", "label": "Bottom", "type": "int", "default": 150, "min": 0, "max": 10000},
    {"key": "left", "label": "Left", "type": "int", "default": 25, "min": 0, "max": 10000,
     "link_with": "right"},
    {"key": "right", "label": "Right", "type": "int", "default": 25, "min": 0, "max": 10000},
]


def _page_size(item, page_index):
    item = Path(item)
    if item.suffix.lower() == ".pdf":
        import fitz
        doc = fitz.open(item)
        R = doc[min(page_index, doc.page_count - 1)].rect
        return float(R.width), float(R.height), "points"
    from PIL import Image
    W, H = Image.open(item).size
    return float(W), float(H), "pixels"


def preview(item, page_index, options, context):
    t, r, b, l = (float(options[k]) for k in ("top", "right", "bottom", "left"))
    W, H, space = _page_size(item, page_index)
    W = max(W, 1.0); H = max(H, 1.0)
    # the four margin edges, as draggable lines in 0..1 fractions
    handles = [
        {"id": "top",    "kind": "hline", "y": t / H},
        {"id": "bottom", "kind": "hline", "y": (H - b) / H},
        {"id": "left",   "kind": "vline", "x": l / W},
        {"id": "right",  "kind": "vline", "x": (W - r) / W},
    ]
    # cropgrid (1×1 = no interior lines) draws the kept rect outlined and hatches
    # the trimmed-away margin in red, matching the Divide tool.
    return {"cropgrid": {"rect": (l, t, max(l + 1, W - r), max(t + 1, H - b)),
                         "cols": 1, "rows": 1},
            "space": space, "handles": handles}


def on_handle_drag(handle_id, fx, fy, options, context):
    """Drag an edge → write the matching margin (in the page's native units)."""
    W = float(context.get("page_w") or 1.0)
    H = float(context.get("page_h") or 1.0)
    if handle_id == "top":
        return {"top": max(0, min(round(fy * H), round(H) - 1))}
    if handle_id == "bottom":
        return {"bottom": max(0, min(round((1.0 - fy) * H), round(H) - 1))}
    if handle_id == "left":
        return {"left": max(0, min(round(fx * W), round(W) - 1))}
    if handle_id == "right":
        return {"right": max(0, min(round((1.0 - fx) * W), round(W) - 1))}
    return {}


def process(item, output_dir, options, overwrite, progress=None):
    item = Path(item)
    t, r, b, l = (float(options[k]) for k in ("top", "right", "bottom", "left"))
    if item.suffix.lower() == ".pdf":
        import fitz
        doc, out = fitz.open(item), fitz.open()
        total = doc.page_count
        for page in doc:
            R = page.rect
            if R.is_empty or R.is_infinite:
                R = fitz.Rect(page.mediabox)
            clip = fitz.Rect(R.x0 + l, R.y0 + t, R.x1 - r, R.y1 - b) & R
            if clip.is_empty or clip.width < 1 or clip.height < 1:
                out.insert_pdf(doc, from_page=page.number, to_page=page.number)
            else:
                p = out.new_page(width=clip.width, height=clip.height)
                p.show_pdf_page(p.rect, doc, page.number, clip=clip)
            if progress:
                progress(page.number + 1, total)
        target = item if overwrite else Path(output_dir) / f"{item.stem}.pdf"
        out.save(target)
        return [str(target)]
    from PIL import Image
    img = Image.open(item)
    W, H = img.size
    box = (max(0, int(l)), max(0, int(t)), min(W, W - int(r)), min(H, H - int(b)))
    if box[2] <= box[0] or box[3] <= box[1]:
        box = (0, 0, W, H)
    target = item if overwrite else Path(output_dir) / f"{item.stem}{item.suffix}"
    img.crop(box).save(target)
    if progress:
        progress(1, 1)
    return [str(target)]
