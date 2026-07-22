"""Test fixture: heavy detected-regions overlay (preview_regions). Not shipped."""
from pathlib import Path

NAME = "Fixture Detect"
ACCEPTS = ("image", "pdf")
PREVIEW_ON_DEMAND = True

OPTIONS = [
    {"key": "n", "label": "N", "type": "int", "default": 2, "min": 1, "max": 5},
]


def preview_regions(item, page_index, options):
    return {"boxes": [(0.1, 0.1, 0.5, 0.4), (0.5, 0.5, 0.9, 0.9)],
            "color": "#1c70d1", "dash": True}


def process(item, output_dir, options, overwrite):
    out = Path(output_dir) / (Path(item).stem + "_d.txt")
    out.write_text("ok", encoding="utf-8")
    return [str(out)]
