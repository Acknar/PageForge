"""Upscale images — by a fixed factor, or to a uniform target size.

A self-contained PageForge tool — needs nothing beyond Pillow (already available in
the app), so it works as soon as you drop it in your scripts folder and hit Reload.

Two modes:
  • Scale factor — multiply every image by the same ratio (e.g. 2x).
  • Target size  — bring every image up to a uniform size along one dimension, so a
    batch of mixed-resolution images comes out matching. Each image gets its own
    ratio computed from its starting size. Aspect ratio is always preserved.

Lanczos is best for photos, Nearest keeps hard edges for pixel art. Optional
post-sharpen counters the softening that any enlargement introduces. Output keeps the
source format by default, or force PNG/JPEG/WebP.
"""
from pathlib import Path

NAME = "Upscale"
ACCEPTS = ("image",)

OPTIONS = [
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": ["Scale factor", "Target size"], "default": "Scale factor"},
    {"key": "scale", "label": "Scale factor (Scale factor mode)", "type": "float",
     "default": 2.0, "min": 1.0, "max": 8.0},
    {"key": "target", "label": "Target size in px (Target size mode)", "type": "int",
     "default": 2000, "min": 16, "max": 20000},
    {"key": "fit", "label": "Fit target to", "type": "choice",
     "choices": ["Longest side", "Shortest side", "Width", "Height"],
     "default": "Longest side"},
    {"key": "no_downscale", "label": "Never shrink (upscale only)", "type": "bool",
     "default": True},
    {"key": "filter", "label": "Resampling", "type": "choice",
     "choices": ["Lanczos (best for photos)", "Bicubic (smooth)",
                 "Bilinear (fast)", "Nearest (pixel art)"],
     "default": "Lanczos (best for photos)"},
    {"key": "sharpen", "label": "Post-sharpen (counter softening)", "type": "bool",
     "default": False},
    {"key": "format", "label": "Output format", "type": "choice",
     "choices": ["Same as input", "PNG", "JPEG", "WebP"], "default": "Same as input"},
]

# Opt into the host's Generate-preview button + before/after compare slider.
PREVIEW_ON_DEMAND = True
COMPARE_PREVIEW = True

_FILTERS = {
    "Lanczos": "LANCZOS",
    "Bicubic": "BICUBIC",
    "Bilinear": "BILINEAR",
    "Nearest": "NEAREST",
}
_EXT = {"PNG": ".png", "JPEG": ".jpg", "WebP": ".webp"}


def _resample(name):
    from PIL import Image
    res = getattr(Image, "Resampling", Image)   # Pillow >= 9.1 moved these to an enum
    key = _FILTERS.get(str(name).split()[0], "LANCZOS")
    return getattr(res, key, res.LANCZOS)


def _ratio(w, h, options):
    """Per-image scale ratio, from the chosen mode/options."""
    if str(options.get("mode", "Scale factor")) == "Target size":
        target = float(options.get("target", 2000))
        fit = str(options.get("fit", "Longest side"))
        if fit == "Width":
            base = w
        elif fit == "Height":
            base = h
        elif fit == "Shortest side":
            base = min(w, h)
        else:  # Longest side
            base = max(w, h)
        r = target / base if base else 1.0
    else:
        r = float(options.get("scale", 2.0))

    if options.get("no_downscale", True):
        r = max(r, 1.0)
    return r


def preview_image(item, page_index, options):
    """Upscaled result for the host's before/after slider. Work on a capped copy
    so a big target size stays responsive — the user only judges quality here."""
    from PIL import Image, ImageFilter
    img = Image.open(item)
    img.load()
    w, h = img.size
    src = img
    cap = 1400
    if max(w, h) > cap:
        src = img.copy()
        src.thumbnail((cap, cap))
    sw, sh = src.size
    r = _ratio(w, h, options)
    new_size = (max(1, round(sw * r)), max(1, round(sh * r)))
    out = src.resize(new_size, _resample(options.get("filter"))) if new_size != (sw, sh) else src
    if options.get("sharpen"):
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
    return out


def process(item, output_dir, options, overwrite):
    from PIL import Image, ImageFilter
    item = Path(item)

    img = Image.open(item)
    img.load()
    w, h = img.size
    r = _ratio(w, h, options)
    new_size = (max(1, round(w * r)), max(1, round(h * r)))

    if new_size != (w, h):
        img = img.resize(new_size, _resample(options.get("filter")))

    if options.get("sharpen"):
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))

    # decide destination path — keep the app's filename; only change the extension
    # when a specific output format is forced.
    fmt = options.get("format", "Same as input")
    if overwrite:
        out = item
    else:
        ext = _EXT.get(fmt, item.suffix or ".png")
        out = Path(output_dir) / f"{item.stem}{ext}"

    # JPEG has no alpha channel — flatten if needed
    if out.suffix.lower() in (".jpg", ".jpeg") and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    save_kwargs = {}
    if out.suffix.lower() in (".jpg", ".jpeg", ".webp"):
        save_kwargs["quality"] = 95
    img.save(out, **save_kwargs)
    return [str(out)]
