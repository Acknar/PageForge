"""Test fixture: heavy image preview + before/after compare, plus a detected
regions overlay. Not shipped."""
from pathlib import Path

NAME = "Fixture Compare"
ACCEPTS = ("image",)
PREVIEW_ON_DEMAND = True
COMPARE_PREVIEW = True

OPTIONS = [
    {"key": "blur", "label": "Blur", "type": "int", "default": 3, "min": 0, "max": 20},
]


def preview_image(item, page_index, options):
    from PIL import Image, ImageFilter
    img = Image.open(item).convert("RGB")
    return img.filter(ImageFilter.GaussianBlur(options.get("blur", 3)))


def needs_preview_button(item, page_index, options):
    return True


def process(item, output_dir, options, overwrite):
    from PIL import Image, ImageFilter
    item = Path(item)
    img = Image.open(item).convert("RGB").filter(
        ImageFilter.GaussianBlur(options.get("blur", 3)))
    out = Path(output_dir) / f"{item.stem}_blur{item.suffix}"
    img.save(out)
    return [str(out)]
