"""Detect content and trim a border of a chosen colour (any colour, not just black).
Tilted pages (a document lying crooked on the scanner) are straightened first:
the angle is detected automatically and can be adjusted by hand."""
from pathlib import Path
import numpy as np

NAME = "Auto-crop border"
ACCEPTS = ("image", "pdf")
ORDER = 10
OPTIONS = [
    {"key": "color", "label": "Border colour", "type": "color", "default": "#000000"},
    {"key": "thresh", "label": "Tolerance", "type": "int", "default": 40, "min": 0, "max": 255},
    {"key": "pad", "label": "Padding", "type": "int", "default": 0, "min": -200, "max": 200},
    {"key": "straighten", "label": "Straighten automatically", "type": "bool", "default": True},
    {"key": "rotate", "label": "Rotation adjustment (°)", "type": "float", "default": 0.0,
     "min": -15.0, "max": 15.0},
]

MAX_ANGLE = 15.0      # degrees; larger tilts are left alone (likely not a page)
MIN_ANGLE = 0.1       # below this the page counts as straight


def _rgb(hexstr):
    h = (hexstr or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# Rendered PDF pages often carry a sliver of white along an edge (a scan image a
# fraction of a point smaller than its page); ignore that many pixels at the edge.
PDF_FRAME = 3


def _mask(arr, color, thr, frame=0):
    # content = pixels that DIFFER from the border colour by more than the tolerance
    m = np.abs(arr.astype(np.int16) - np.array(color, dtype=np.int16)).max(axis=2) > thr
    if frame:
        m[:frame, :] = m[-frame:, :] = False
        m[:, :frame] = m[:, -frame:] = False
    return m


def _content_bbox(arr, color, thr, frame=0):
    ys, xs = np.where(_mask(arr, color, thr, frame))
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _hull(points):
    """Convex hull (monotone chain) of an (N, 2) array; returns hull points."""
    pts = sorted(set(map(tuple, points)))
    if len(pts) < 3:
        return np.array(pts, dtype=float)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1], dtype=float)


def _tilt(arr, color, thr, frame=0):
    """Angle in degrees (counter-clockwise positive, as PIL rotates) that makes the
    content upright: the minimum-area rectangle around the content, found by
    trying every hull edge direction. 0 when the content is straight, too small
    to measure, or tilted more than MAX_ANGLE."""
    mask = _mask(arr, color, thr, frame)
    h, w = mask.shape
    step = max(1, int(max(h, w) / 600))          # analyse at ~600 px
    m = mask[::step, ::step]
    # keep only the outline rows/columns extremes: cheap and enough for the hull
    ys, xs = np.nonzero(m)
    if len(xs) < 50:
        return 0.0
    pts = []
    for y in np.unique(ys):
        row = xs[ys == y]
        pts += [(row.min(), y), (row.max(), y)]
    hull = _hull(np.array(pts))
    if len(hull) < 3:
        return 0.0
    best_area, best_angle = None, 0.0
    edges = np.roll(hull, -1, axis=0) - hull
    for ex, ey in edges:
        if ex == 0 and ey == 0:
            continue
        a = np.arctan2(ey, ex)
        c, s = np.cos(-a), np.sin(-a)
        rx = hull[:, 0] * c - hull[:, 1] * s
        ry = hull[:, 0] * s + hull[:, 1] * c
        area = (rx.max() - rx.min()) * (ry.max() - ry.min())
        if best_area is None or area < best_area:
            best_area, best_angle = area, a
    deg = np.degrees(best_angle)
    deg = (deg + 45.0) % 90.0 - 45.0                 # nearest axis
    if abs(deg) < MIN_ANGLE or abs(deg) > MAX_ANGLE:
        return 0.0
    return float(deg)                                # image y points down: PIL angle = +deg


def _angle(arr, color, thr, options, frame=0):
    """Rotation to apply, counter-clockwise positive as PIL rotates: the detected
    tilt (when automatic straightening is on) plus the user's adjustment, which is
    clockwise positive as in most image editors."""
    auto = _tilt(arr, color, thr, frame) if options.get("straighten", True) else 0.0
    return auto - float(options.get("rotate", 0) or 0)


def _straightened(img, color, thr, pad, options, frame=0):
    """PIL image -> (cropped PIL image, angle used)."""
    from PIL import Image
    img = img.convert("RGB")
    if frame:
        # paint the ignored edge in the border colour so rotation carries it along
        from PIL import ImageDraw
        ImageDraw.Draw(img).rectangle([0, 0, img.width - 1, img.height - 1], outline=color, width=frame)
    angle = _angle(np.asarray(img), color, thr, options)
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=color)
    b = _content_bbox(np.asarray(img), color, thr)
    if not b:
        return img, angle
    W, H = img.size
    box = (max(0, b[0] - pad), max(0, b[1] - pad), min(W, b[2] + pad), min(H, b[3] + pad))
    return img.crop(box), angle


def _render_page(path, page_index, dpi):
    import fitz
    from PIL import Image
    z = dpi / 72
    pix = fitz.open(path)[page_index].get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def preview(item, page_index, options, context):
    """Show the auto-detected keep-region the same way Crop/Divide do: the kept
    rectangle outlined, with the trimmed-away border hatched red (a cropgrid with
    no interior lines). The region is detected automatically, so there are no
    draggable handles. When the page gets rotated, preview_image shows it instead."""
    if options.get("straighten", True) or float(options.get("rotate", 0) or 0):
        return None
    item = Path(item)
    color = _rgb(options.get("color", "#000000"))
    thr, pad = int(options["thresh"]), float(options["pad"])
    if item.suffix.lower() == ".pdf":
        import fitz
        z = 100 / 72
        pix = fitz.open(item)[page_index].get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        b = _content_bbox(arr, color, thr, PDF_FRAME)
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


def needs_preview_button(item, page_index, options):
    """The rotated page is shown as soon as the options change: it is quick at
    preview resolution, and the rotation is what the user is adjusting."""
    return not (options.get("straighten", True) or float(options.get("rotate", 0) or 0))


def _hatched(img, box, color=(192, 28, 40)):
    """img with everything outside box hatched and box outlined, in the style of
    the host's crop overlays (so the before/after view reads like the outline
    preview)."""
    from PIL import Image, ImageDraw
    img = img.convert("RGB")
    W, H = img.size
    step = max(8, round(max(W, H) / 70))
    width = max(1, round(max(W, H) / 900))
    hatch = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(hatch)
    for k in range(-H, W, step):
        d.line([(k, 0), (k + H, H)], fill=color + (150,), width=width)
    keep = Image.new("L", img.size, 255)
    ImageDraw.Draw(keep).rectangle(box, fill=0)
    shade = Image.new("RGBA", img.size, color + (40,))
    over = Image.alpha_composite(shade, hatch)
    out = img.convert("RGBA")
    out.paste(over, (0, 0), Image.composite(over, Image.new("RGBA", img.size), keep).split()[3])
    out = out.convert("RGB")
    ImageDraw.Draw(out).rectangle(box, outline=color, width=max(2, width * 2))
    return out


def preview_image(item, page_index, options):
    """The page rotated in place (detected tilt plus the adjustment), with the
    area that stays outlined and the border that goes hatched, like the outline
    preview."""
    item = Path(item)
    color = _rgb(options.get("color", "#000000"))
    thr, pad = int(options["thresh"]), int(options["pad"])
    frame = 0
    if item.suffix.lower() == ".pdf":
        img = _render_page(item, page_index, 100)
        pad = round(pad * 100 / 72)
        frame = PDF_FRAME
    else:
        from PIL import Image
        img = Image.open(item)
        img.load()
    from PIL import Image, ImageDraw
    img = img.convert("RGB")
    if frame:
        ImageDraw.Draw(img).rectangle([0, 0, img.width - 1, img.height - 1], outline=color, width=frame)
    angle = _angle(np.asarray(img), color, thr, options)
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=color)
    b = _content_bbox(np.asarray(img), color, thr)
    if not b:
        return img
    W, H = img.size
    box = (max(0, b[0] - pad), max(0, b[1] - pad), min(W, b[2] + pad) - 1, min(H, b[3] + pad) - 1)
    return _hatched(img, box)


def process(item, output_dir, options, overwrite, progress=None):
    item = Path(item)
    color = _rgb(options.get("color", "#000000"))
    thr, pad = int(options["thresh"]), int(options["pad"])
    if item.suffix.lower() == ".pdf":
        import io
        import fitz
        doc, out = fitz.open(item), fitz.open()
        dpi = 150
        z = dpi / 72
        total = doc.page_count
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csRGB, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            angle = _angle(arr, color, thr, options, PDF_FRAME)
            if angle:
                # A tilted page has to be re-rendered: rotate at 300 dpi, crop,
                # and place the result as the new page (same physical size).
                from PIL import Image
                img = _render_page(item, page.number, 300)
                cropped, _ = _straightened(img, color, thr, round(pad * 300 / 72), options, PDF_FRAME)
                buf = io.BytesIO()
                cropped.save(buf, "JPEG", quality=92)
                p = out.new_page(width=cropped.width * 72 / 300, height=cropped.height * 72 / 300)
                p.insert_image(p.rect, stream=buf.getvalue())
            else:
                b = _content_bbox(arr, color, thr, PDF_FRAME)
                R = page.rect
                clip = (fitz.Rect(R.x0 + b[0] / z - pad, R.y0 + b[1] / z - pad,
                                  R.x0 + b[2] / z + pad, R.y0 + b[3] / z + pad) & R) if b else None
                if not clip or clip.is_empty or clip.width < 1 or clip.height < 1:
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
    src = Image.open(item)
    src.load()
    target = item if overwrite else Path(output_dir) / f"{item.stem}{item.suffix}"
    if not _content_bbox(np.asarray(src.convert("RGB")), color, thr):
        src.save(target)
        return [str(target)]
    cropped, _ = _straightened(src, color, thr, pad, options)
    cropped.save(target)
    return [str(target)]
