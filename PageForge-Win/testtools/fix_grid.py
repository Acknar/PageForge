"""Test fixture: thumbnail-grid surface with reorder, select and split bars.
Consumes _grid_sequence in a BATCH process(). Not shipped."""
from pathlib import Path

NAME = "Fixture Grid"
ACCEPTS = ("image", "pdf")
BATCH = True


def preview_kind(item, options):
    return "grid"


def preview_grid(context, options):
    return {"source": "pages", "selectable": True, "reorderable": True,
            "split_bars": True, "splits": []}


def on_select(indices, options, context):
    return {}


def on_reorder(order, options, context):
    return None


def on_split(points, options, context):
    return {}


def process(items, output_dir, options, overwrite):
    seq = options.get("_grid_sequence", [])
    out = Path(output_dir) / "grid_sequence.txt"
    out.write_text("\n".join(f"{fi}:{pg}" for fi, pg in seq), encoding="utf-8")
    return [str(out)]
