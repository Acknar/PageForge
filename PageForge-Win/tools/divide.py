"""Divide each page/image into an N×M grid, optionally rotating the resulting pieces.

The four crop margins (cloned from the Crop tool) trim the page first — drag the red
edges freely in the preview. Whatever is left inside the red rectangle is then split
evenly into Columns × Rows. The trimmed-away margin is shown hatched in red and is
discarded. Leave the margins at 0 (the default) to divide the whole page.
PDF margins are points; image margins are pixels. Chain buttons link Top↔Bottom and
Left↔Right."""
from pathlib import Path

NAME = "Divide into grid"
ACCEPTS = ("image", "pdf")
ORDER = 40
OPTIONS = [
    {"key": "cols", "label": "Columns", "type": "int", "default": 3, "min": 1, "max": 40},
    {"key": "rows", "label": "Rows", "type": "int", "default": 3, "min": 1, "max": 40},
    {"key": "rotate", "label": "Rotate result", "type": "choice",
     "choices": ["None", "90° clockwise", "90° counter-clockwise", "180°"],
     "default": "None"},
    # inner gutter: whitespace trimmed from between the cells (half off each
    # side of every cut), so tiles separated by gaps come out clean. 0 = none.
    {"key": "gutter", "label": "Gutter between cells", "type": "int",
     "default": 0, "min": 0, "max": 10000},
    # crop margins (cloned from Crop) — trim before dividing. Default 0 = no crop.
    {"key": "top", "label": "Crop top", "type": "int", "default": 0, "min": 0, "max": 10000,
     "link_with": "bottom"},
    {"key": "bottom", "label": "Crop bottom", "type": "int", "default": 0, "min": 0, "max": 10000},
    {"key": "left", "label": "Crop left", "type": "int", "default": 0, "min": 0, "max": 10000,
     "link_with": "right"},
    {"key": "right", "label": "Crop right", "type": "int", "default": 0, "min": 0, "max": 10000},
]

_PDF_ROT = {"90° clockwise": 90, "90° counter-clockwise": 270, "180°": 180}


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


def _margins(options):
    return (float(options.get(k, 0) or 0) for k in ("top", "right", "bottom", "left"))


def preview(item, page_index, options, context):
    t, r, b, l = _margins(options)
    W, H, space = _page_size(item, page_index)
    W = max(W, 1.0); H = max(H, 1.0)
    x1 = max(l + 1, W - r); y1 = max(t + 1, H - b)
    handles = [
        {"id": "top",    "kind": "hline", "y": t / H},
        {"id": "bottom", "kind": "hline", "y": (H - b) / H},
        {"id": "left",   "kind": "vline", "x": l / W},
        {"id": "right",  "kind": "vline", "x": (W - r) / W},
    ]
    return {"cropgrid": {"rect": (l, t, x1, y1),
                         "cols": int(options["cols"]), "rows": int(options["rows"]),
                         "gutter": float(options.get("gutter", 0) or 0)},
            "space": space, "handles": handles}


def on_handle_drag(handle_id, fx, fy, options, context):
    """Drag a crop edge → write the matching margin (native units). Freely
    positioned; the grid re-proportions itself inside whatever is left."""
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


def _rotate_image(img, rot):
    from PIL import Image
    if rot == "90° clockwise":
        return img.transpose(Image.ROTATE_270)
    if rot == "90° counter-clockwise":
        return img.transpose(Image.ROTATE_90)
    if rot == "180°":
        return img.transpose(Image.ROTATE_180)
    return img


def process(item, output_dir, options, overwrite, progress=None):
    item = Path(item)
    cols, rows = int(options["cols"]), int(options["rows"])
    t, r, b, l = _margins(options)
    gut = float(options.get("gutter", 0) or 0)
    rot = options.get("rotate", "None")
    if item.suffix.lower() == ".pdf":
        import fitz
        doc, out = fitz.open(item), fitz.open()
        total = doc.page_count
        for page in doc:
            R = page.rect
            if R.is_empty or R.is_infinite:
                R = fitz.Rect(page.mediabox)
            kept = fitz.Rect(R.x0 + l, R.y0 + t, R.x1 - r, R.y1 - b) & R
            if kept.is_empty or kept.width < 1 or kept.height < 1:
                kept = R
            tw, th = kept.width / cols, kept.height / rows
            ins = gut / 2.0
            for ri in range(rows):
                for ci in range(cols):
                    clip = fitz.Rect(kept.x0 + ci * tw + ins, kept.y0 + ri * th + ins,
                                     kept.x0 + (ci + 1) * tw - ins, kept.y0 + (ri + 1) * th - ins)
                    if clip.width < 1 or clip.height < 1:   # gutter ate the cell
                        continue
                    p = out.new_page(width=clip.width, height=clip.height)
                    p.show_pdf_page(p.rect, doc, page.number, clip=clip)
                    if rot in _PDF_ROT:
                        p.set_rotation(_PDF_ROT[rot])
            if progress:
                progress(page.number + 1, total)
        target = item if overwrite else Path(output_dir) / f"{item.stem}-divided.pdf"
        out.save(target)
        return [str(target)]
    from PIL import Image
    img = Image.open(item)
    W, H = img.size
    box = (max(0, int(l)), max(0, int(t)), min(W, W - int(r)), min(H, H - int(b)))
    if box[2] <= box[0] or box[3] <= box[1]:
        box = (0, 0, W, H)
    region = img.crop(box)
    rw, rh = region.size
    ins = int(gut) // 2
    outs, idx = [], 1
    for ri in range(rows):
        for ci in range(cols):
            cell = (ci * rw // cols + ins, ri * rh // rows + ins,
                    (ci + 1) * rw // cols - ins, (ri + 1) * rh // rows - ins)
            if cell[2] <= cell[0] or cell[3] <= cell[1]:   # gutter ate the cell
                continue
            tile = _rotate_image(region.crop(cell), rot)
            tpath = Path(output_dir) / f"{item.stem}_{idx:02d}{item.suffix}"
            tile.save(tpath)
            outs.append(str(tpath))
            idx += 1
    if progress:
        progress(1, 1)
    return outs
