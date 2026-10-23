"""Remove image/PDF-page backgrounds → transparent WebP.

Two modes:
  • AI (ISNet)   — the isnet-general-use model picks the subject automatically.
  • Manual color — key out a chosen background colour by tolerance, with an
    optional "contiguous" restriction (only remove the region connected to the
    image border) and an optional soft-edge feather.
"""
from pathlib import Path

NAME = "Remove background"
ACCEPTS = ("image", "pdf")
ORDER = 90
REQUIRES = ["rembg", "onnxruntime"]      # pip packages installed on demand
MODULES = ["rembg", "onnxruntime"]        # import names used to detect if installed
PREVIEW_ON_DEMAND = True
COMPARE_PREVIEW = True            # host shows a before/after divider slider
OPTIONS = [
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": ["AI (ISNet)", "Manual color"], "default": "AI (ISNet)"},
    {"key": "bg_color", "label": "Background color (manual)", "type": "color",
     "default": "#ffffff"},
    {"key": "tolerance", "label": "Tolerance (manual)", "type": "int",
     "default": 30, "min": 0, "max": 255},
    {"key": "contiguous", "label": "Contiguous (manual)", "type": "bool",
     "default": True},
    {"key": "soften", "label": "Soften edges (manual)", "type": "bool",
     "default": False},
    {"key": "dpi", "label": "Render DPI (PDF)", "type": "int", "default": 300, "min": 72, "max": 600},
    {"key": "quality", "label": "WebP quality", "type": "int", "default": 90, "min": 1, "max": 100},
    {"key": "lossless", "label": "Lossless", "type": "bool", "default": False},
]

_S = {"session": None}


def _hex_rgb(h):
    h = str(h).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _border_connected(mask):
    """Keep only the True regions of `mask` that touch the image border."""
    import numpy as np
    try:
        from scipy import ndimage
        lbl, _ = ndimage.label(mask)
        edge = np.concatenate([lbl[0, :], lbl[-1, :], lbl[:, 0], lbl[:, -1]])
        keep = np.unique(edge)
        keep = keep[keep != 0]
        return np.isin(lbl, keep)
    except Exception:
        pass
    # Pure-numpy fallback: iterative flood fill from the border.
    h, w = mask.shape
    visited = np.zeros_like(mask)
    stack = []
    for x in range(w):
        if mask[0, x]:
            stack.append((0, x))
        if mask[h - 1, x]:
            stack.append((h - 1, x))
    for y in range(h):
        if mask[y, 0]:
            stack.append((y, 0))
        if mask[y, w - 1]:
            stack.append((y, w - 1))
    while stack:
        y, x = stack.pop()
        if visited[y, x]:
            continue
        visited[y, x] = True
        if y > 0 and mask[y - 1, x] and not visited[y - 1, x]:
            stack.append((y - 1, x))
        if y < h - 1 and mask[y + 1, x] and not visited[y + 1, x]:
            stack.append((y + 1, x))
        if x > 0 and mask[y, x - 1] and not visited[y, x - 1]:
            stack.append((y, x - 1))
        if x < w - 1 and mask[y, x + 1] and not visited[y, x + 1]:
            stack.append((y, x + 1))
    return visited


def _manual_cut(img, bg_hex, tol, contiguous, soften):
    """Key out `bg_hex` within `tol` (per-channel), return an RGBA image.

    The input's existing alpha is preserved: we never flatten to RGB (that would
    reveal the black/garbage RGB stored under transparent pixels and turn them
    opaque), and already-transparent pixels are treated as background so they
    stay transparent and let the contiguous flood fill travel through them.
    """
    import numpy as np
    from PIL import Image
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba)
    rgb = arr[..., :3].astype(np.int16)
    src_alpha = arr[..., 3]
    bg = np.array(_hex_rgb(bg_hex), dtype=np.int16)
    # Chebyshev distance: each channel must be within `tol` of the background.
    diff = np.abs(rgb - bg).max(axis=2)
    mask = (diff <= tol) | (src_alpha == 0)   # True where background
    if contiguous:
        mask = _border_connected(mask)
    # Remove where masked; elsewhere keep the pixel's original alpha (so an
    # already-transparent or anti-aliased input edge is respected, not forced
    # fully opaque).
    alpha = np.where(mask, 0, src_alpha).astype(np.uint8)
    if soften:
        from PIL import ImageFilter
        alpha = np.asarray(
            Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(1.2))
        )
    out = np.dstack([arr[..., :3], alpha]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _cut(img, options):
    if options.get("mode", "AI (ISNet)") == "Manual color":
        return _manual_cut(
            img,
            options.get("bg_color", "#ffffff"),
            int(options.get("tolerance", 30)),
            bool(options.get("contiguous", True)),
            bool(options.get("soften", False)),
        )
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
    return _cut(img, options).convert("RGBA")


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
            cut = _cut(img, options)
            t = Path(output_dir) / f"{item.stem}_p{page.number + 1:03d}.webp"
            cut.save(t, "WEBP", lossless=True) if loss else cut.save(t, "WEBP", quality=q)
            out.append(str(t))
            if progress:
                progress(page.number + 1, total)
    else:
        cut = _cut(Image.open(item), options)
        t = item.with_suffix(".webp") if overwrite else Path(output_dir) / f"{item.stem}.webp"
        cut.save(t, "WEBP", lossless=True) if loss else cut.save(t, "WEBP", quality=q)
        out.append(str(t))
    return out
