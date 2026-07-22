"""Test fixture: exercises every option type, link/enabled/visible/hidden rules,
a cropgrid overlay, draggable handles, and a regions option. Not shipped."""
from pathlib import Path

NAME = "Fixture Options"
ACCEPTS = ("image", "pdf")
PREVIEW_KIND = "canvas"

OPTIONS = [
    {"key": "count", "label": "Count", "type": "int", "default": 2, "min": 1, "max": 9},
    {"key": "amount", "label": "Amount", "type": "float", "default": 1.5, "min": 0.0, "max": 9.0},
    {"key": "top", "label": "Top", "type": "int", "default": 10, "min": 0, "max": 500,
     "link_with": "bottom"},
    {"key": "bottom", "label": "Bottom", "type": "int", "default": 10, "min": 0, "max": 500},
    {"key": "flag", "label": "Flag", "type": "bool", "default": True},
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": ["grid", "rect", "boxes", "zones"], "default": "grid"},
    {"key": "col", "label": "Colour", "type": "color", "default": "#3388ff"},
    {"key": "note", "label": "Note", "type": "text", "default": "hi"},
    {"key": "namelist", "label": "File", "type": "file", "default": ""},
    {"key": "onlywhenrect", "label": "Only when rect", "type": "int", "default": 5,
     "min": 0, "max": 10, "enabled_when": {"key": "mode", "eq": "rect"}},
    {"key": "zones", "label": "Zones", "type": "regions",
     "visible_when": {"key": "mode", "eq": "zones"}, "order_key": "zorder"},
    {"key": "zorder", "label": "Zone order", "type": "choice",
     "choices": ["As drawn", "Top → bottom, left → right"], "default": "As drawn",
     "visible_when": {"key": "mode", "eq": "zones"}},
    {"key": "kept", "label": "kept", "type": "int", "default": 0, "hidden": True},
]


def preview(item, page_index, options, context):
    mode = options.get("mode", "grid")
    if mode == "grid":
        return {"grid": (options.get("count", 2), options.get("count", 2)),
                "handles": [{"id": "v", "kind": "vline", "x": 0.5},
                            {"id": "h", "kind": "hline", "y": 0.4},
                            {"id": "p", "kind": "point", "x": 0.3, "y": 0.3}]}
    if mode == "rect":
        return {"cropgrid": {"rect": (0.1, 0.1, 0.9, 0.9),
                             "cols": options.get("count", 2),
                             "rows": options.get("count", 2), "gutter": 0.02},
                "space": "fraction", "color": options.get("col")}
    if mode == "boxes":
        return {"boxes": [(0.1, 0.1, 0.4, 0.4), (0.5, 0.5, 0.9, 0.9)],
                "space": "fraction", "fill": options.get("col")}
    return None


def on_handle_drag(handle_id, fx, fy, options, context):
    if handle_id == "h":
        return {"top": round(fy * context["page_h"])}
    if handle_id == "v":
        return {"count": max(1, round(fx * 9))}
    return {}


def process(item, output_dir, options, overwrite):
    out = Path(output_dir) / (Path(item).stem + ".txt")
    out.write_text(f"mode={options.get('mode')} count={options.get('count')} "
                   f"zones={options.get('zones')}\n", encoding="utf-8")
    return [str(out)]
