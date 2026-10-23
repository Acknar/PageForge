"""OCR text from images or PDFs using zones or columns.

Pick a Mode. "Columns" splits each page into evenly spaced columns (with page margins
and an optional gutter shift for book scans — content shifts away from the central
crease; set "Book pages?" to whether the book starts on a left or right page). "Draw
zones" instead reads only the red boxes you draw, per page: draw different boxes on
different pages to fit varying layouts; pages with no zones are skipped, and right-click
a zone to delete just that one. The Mode selector shows only the options that matter for
the mode you chose. Output text, CSV (one column per zone), or a searchable PDF. For
automatic whole-page layout, use "Dynamic OCR"."""
import re
from pathlib import Path

NAME = "OCR (zones)"
ACCEPTS = ("image", "pdf")
ORDER = 80
REQUIRES = ["pytesseract"]
SYSTEM_REQUIRES = ["tesseract", "tesseract-langpack-eng"]
MODULES = ["pytesseract"]
# Colour this tool draws its zones and column guides in (blue, matching EasyOCR).
ZONE_COLOR = "#1c70d1"

_COLUMNS_ONLY = {"key": "mode", "eq": "Columns"}
_ZONES_ONLY = {"key": "mode", "eq": "Draw zones"}

OPTIONS = [
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": ["Columns", "Draw zones"], "default": "Columns",
     "hint": "Columns splits each page into evenly spaced columns; Draw zones reads "
             "only the boxes you draw on each page."},
    {"key": "zones", "label": "Text zones", "type": "regions", "order_key": "order",
     "per_page": True, "color": ZONE_COLOR, "visible_when": _ZONES_ONLY},
    {"key": "order", "label": "Zone order", "type": "choice",
     "choices": ["Top \u2192 bottom, left \u2192 right", "As drawn"],
     "default": "Top \u2192 bottom, left \u2192 right", "visible_when": _ZONES_ONLY},
    {"key": "columns", "label": "Columns", "type": "int",
     "default": 2, "min": 1, "max": 8, "visible_when": _COLUMNS_ONLY},
    {"key": "col_spacing", "label": "Column spacing (px)", "type": "int",
     "default": 100, "min": 0, "max": 2000, "visible_when": _COLUMNS_ONLY},
    # col_widths has no visible row: drag the column dividers in the preview to set
    # asymmetric widths. The value is still read by _boxes_for and written by
    # on_handle_drag; it just isn't shown as an editable field.
    {"key": "col_widths", "label": "Column width ratios", "type": "text", "default": "",
     "hidden": True},
    {"key": "margin_top", "label": "Margin top (px)", "type": "int", "default": 250,
     "min": 0, "max": 4000, "link_with": "margin_bottom", "visible_when": _COLUMNS_ONLY},
    {"key": "margin_bottom", "label": "Margin bottom (px)", "type": "int", "default": 250,
     "min": 0, "max": 4000, "visible_when": _COLUMNS_ONLY},
    {"key": "margin_left", "label": "Margin left (px)", "type": "int", "default": 250,
     "min": 0, "max": 4000, "link_with": "margin_right", "visible_when": _COLUMNS_ONLY},
    {"key": "margin_right", "label": "Margin right (px)", "type": "int", "default": 250,
     "min": 0, "max": 4000, "visible_when": _COLUMNS_ONLY},
    {"key": "book", "label": "Book pages?", "type": "choice",
     "choices": ["Symmetric pages", "Left page start", "Right page start"],
     "default": "Symmetric pages", "visible_when": _COLUMNS_ONLY},
    {"key": "gutter", "label": "Gutter shift (px, book scans)", "type": "int",
     "default": 500, "min": 0, "max": 4000,
     "visible_when": _COLUMNS_ONLY,
     "enabled_when": {"key": "book", "in": ["Left page start", "Right page start"]}},
    {"key": "wrap_lines", "label": "Wrap lines", "type": "bool", "default": False},
    {"key": "lang", "label": "Language", "type": "text", "default": "eng"},
    {"key": "dpi", "label": "Render DPI (PDF)", "type": "int", "default": 300, "min": 72, "max": 600},
    {"key": "output", "label": "Output", "type": "choice",
     "choices": ["Text file", "CSV (zones as columns)", "Searchable PDF"], "default": "Text file"},
]


def _wrap_text(text):
    """Join lines that wrap within a paragraph into one line (de-hyphenating split
    words), keeping blank lines as paragraph breaks. Used when "Wrap lines" is on;
    with it off the OCR line breaks are preserved as-is."""
    out = []
    for para in re.split(r"\n[ \t]*\n", text):
        buf = ""
        for ln in para.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if not buf:
                buf = ln
            elif buf.endswith("-"):
                buf = buf[:-1] + ln          # de-hyphenate across the break
            else:
                buf += " " + ln
        if buf:
            out.append(buf)
    return "\n\n".join(out)


def _truthy(v):
    """Robust truthiness: treat the strings 'False'/'0'/'no'/'off'/'' as False.
    Guards against the option arriving as a string (bool('False') is True)."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _parse_ratios(spec, cols):
    """Parse a 'Column width ratios' string like '2,1,1' into a list of positive
    floats, one per column. Returns None (use equal widths) if it doesn't match."""
    if not spec:
        return None
    parts = [p for p in re.split(r"[,\s]+", str(spec).strip()) if p]
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if len(vals) != cols or any(v <= 0 for v in vals):
        return None
    return vals


def _reading_order(boxes):
    # strictly top-to-bottom, then left-to-right: a box higher up always reads first
    return sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))


def _page_size_px(item, page_index, dpi):
    item = Path(item)
    if item.suffix.lower() == ".pdf":
        import fitz
        r = fitz.open(item)[page_index].rect
        return r.width * dpi / 72, r.height * dpi / 72
    from PIL import Image
    return Image.open(item).size


def _margins(options):
    """Four page margins in px: (top, bottom, left, right). Falls back to the
    old single 'margin' value if the four keys aren't present."""
    if "margin_top" in options or "margin_left" in options:
        return (int(options.get("margin_top", 0)), int(options.get("margin_bottom", 0)),
                int(options.get("margin_left", 0)), int(options.get("margin_right", 0)))
    m = int(options.get("margin", 0))
    return m, m, m, m


def _book_mode(options):
    """Normalise the book setting, accepting the legacy 'odd_side' values too.
    Returns 'symmetric', 'left' (left page starts the book) or 'right'."""
    book = options.get("book")
    if book is None:                       # legacy option name
        od = str(options.get("odd_side", "")).lower()
        if od.startswith("right"):
            return "right"
        if od.startswith("left"):
            return "left"
        return "symmetric"
    b = str(book).lower()
    if b.startswith("left"):
        return "left"
    if b.startswith("right"):
        return "right"
    return "symmetric"


def _gutter_of(options):
    """Gutter shift only applies to a book layout; symmetric pages get none."""
    return int(options.get("gutter", 0)) if _book_mode(options) != "symmetric" else 0


def _side_of(page_index, options):
    """Which side this page's content sits toward (away from the crease)."""
    mode = _book_mode(options)
    if mode == "symmetric":
        return "center"
    is_odd_page = ((page_index + 1) % 2 == 1)
    if mode == "right":                    # book opens on a right-hand page
        return "right" if is_odd_page else "left"
    return "left" if is_odd_page else "right"


def _zone_mode(options):
    """True when the tool should read hand-drawn zones rather than columns.
    Driven by the explicit Mode selector, but falls back to "are there zones?"
    for any older saved state that predates the selector."""
    mode = options.get("mode")
    if mode is not None:
        m = str(mode).lower()
        if "zone" in m or "draw" in m:
            return True
        if "column" in m:
            return False
    return bool(options.get("zones"))


def _zones_for_page(zones, page_index):
    if isinstance(zones, dict):
        return zones.get(page_index) or zones.get(str(page_index)) or []
    return zones or []   # legacy flat list applies to every page


def _ordered(zones, order):
    if "drawn" in (order or "").lower():
        return list(zones)
    return [zones[i] for i in _reading_order(zones)]


def _boxes_for(item, page_index, options):
    """Return (pixel_boxes, (W,H), skip). skip=True means this page has no zones."""
    dpi = int(options.get("dpi", 300))
    W, H = _page_size_px(item, page_index, dpi)
    zones = options.get("zones") or {}
    zone_mode = _zone_mode(options)
    page_zones = _zones_for_page(zones, page_index)
    if zone_mode:
        if not page_zones:
            return [], (W, H), True
        z = _ordered(page_zones, options.get("order"))
        return [(int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)) for x0, y0, x1, y1 in z], (W, H), False
    cols = int(options.get("columns", 1))
    spacing = int(options.get("col_spacing", 0))
    m_top, m_bot, m_left, m_right = _margins(options)
    gutter = _gutter_of(options)
    side = _side_of(page_index, options)
    left = m_left + (gutter if side == "right" else 0)
    right = W - m_right - (gutter if side == "left" else 0)
    top, bot = m_top, H - m_bot
    ratios = _parse_ratios(options.get("col_widths"), cols)
    if cols <= 1:
        boxes = [(left, top, right, bot)]
    elif ratios:
        # asymmetric columns: widths shared out by the given ratios
        avail = right - left - (cols - 1) * spacing
        total = sum(ratios)
        boxes, x = [], left
        for c in range(cols):
            w = avail * ratios[c] / total
            boxes.append((x, top, x + w, bot))
            x += w + spacing
    else:
        colw = (right - left - (cols - 1) * spacing) / cols
        boxes = [(left + c * (colw + spacing), top, left + c * (colw + spacing) + colw, bot)
                 for c in range(cols)]
    return [tuple(map(int, b)) for b in boxes], (W, H), False


def preview(item, page_index, options, context):
    if _zone_mode(options):
        return None   # zone mode: the app draws this page's zones
    boxes, (W, H), _ = _boxes_for(item, page_index, options)
    m_top, m_bot, m_left, m_right = _margins(options)
    if int(options.get("columns", 1)) <= 1 and max(m_top, m_bot, m_left, m_right) <= 0 \
            and _gutter_of(options) <= 0:
        return None
    out = {"boxes": [(x0 / W, y0 / H, x1 / W, y1 / H) for x0, y0, x1, y1 in boxes],
           "space": "fraction", "color": ZONE_COLOR, "dash": True}
    handles = [
        # solid outer-margin edges (drag to trim the whole column block)
        {"id": "m_left",   "kind": "vline", "x": m_left / W,        "color": ZONE_COLOR},
        {"id": "m_right",  "kind": "vline", "x": (W - m_right) / W, "color": ZONE_COLOR},
        {"id": "m_top",    "kind": "hline", "y": m_top / H,         "color": ZONE_COLOR},
        {"id": "m_bottom", "kind": "hline", "y": (H - m_bot) / H,   "color": ZONE_COLOR},
    ]
    # one dashed divider per gutter between adjacent columns
    if int(options.get("columns", 1)) >= 2 and len(boxes) >= 2:
        handles += [
            {"id": f"col{i}", "kind": "vline", "dash": True,
             "x": (boxes[i][2] + boxes[i + 1][0]) / 2 / W, "color": ZONE_COLOR}
            for i in range(len(boxes) - 1)
        ]
    out["handles"] = handles
    return out


def _page_wh_px(context, options):
    """Page size in px (at render DPI) for the current preview file, or None."""
    files = context.get("files") or []
    idx = context.get("index", 0)
    if not (0 <= idx < len(files)):
        return None, None, None
    item = files[idx]
    page_index = context.get("page_index", 0)
    W, H = _page_size_px(item, page_index, int(options.get("dpi", 300)))
    return item, W, H


def on_handle_drag(handle_id, fx, fy, options, context):
    """Drag a divider → re-apportion its two columns (writing 'col_widths').
    Drag a margin edge → write that margin (px). Both reproduce on process."""
    hid = str(handle_id)
    item, W, H = _page_wh_px(context, options)
    if W is None:
        return {}
    if hid == "m_left":
        return {"margin_left": max(0, min(round(fx * W), round(W) - 1))}
    if hid == "m_right":
        return {"margin_right": max(0, min(round((1.0 - fx) * W), round(W) - 1))}
    if hid == "m_top":
        return {"margin_top": max(0, min(round(fy * H), round(H) - 1))}
    if hid == "m_bottom":
        return {"margin_bottom": max(0, min(round((1.0 - fy) * H), round(H) - 1))}
    if not hid.startswith("col"):
        return {}
    try:
        i = int(hid[3:])
    except ValueError:
        return {}
    page_index = context.get("page_index", 0)
    boxes, (W, H), _ = _boxes_for(item, page_index, options)
    if not (0 <= i < len(boxes) - 1):
        return {}
    spacing = int(options.get("col_spacing", 0))
    widths = [b[2] - b[0] for b in boxes]
    # move the divider (its centre sits in the gutter between box i and i+1)
    left_edge = boxes[i][0]
    span = widths[i] + widths[i + 1]          # width shared by the two columns
    minw = max(1.0, 0.02 * W)
    new_wi = (fx * W - spacing / 2.0) - left_edge
    new_wi = max(minw, min(new_wi, span - minw))
    widths[i] = new_wi
    widths[i + 1] = span - new_wi
    ratios = ",".join(f"{w:.1f}" for w in widths)
    return {"col_widths": ratios}


def _pages(item, dpi):
    from PIL import Image
    item = Path(item)
    if item.suffix.lower() == ".pdf":
        import fitz
        for page in fitz.open(item):
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            yield page.number, Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        yield 0, Image.open(item).convert("RGB")


def process(item, output_dir, options, overwrite, progress=None):
    import pytesseract
    item = Path(item)
    dpi = int(options.get("dpi", 300))
    lang = options.get("lang") or "eng"
    zones = options.get("zones") or {}
    outmode = options.get("output", "Text file")
    wrap = _truthy(options.get("wrap_lines"))

    if outmode.startswith("Searchable"):
        import fitz
        merged = fitz.open()
        total = fitz.open(item).page_count if item.suffix.lower() == ".pdf" else 1
        for i, (_, img) in enumerate(_pages(item, dpi), start=1):
            merged.insert_pdf(fitz.open("pdf", pytesseract.image_to_pdf_or_hocr(img, extension="pdf", lang=lang)))
            if progress:
                progress(i, total)
        target = item if overwrite else Path(output_dir) / f"{item.stem}.pdf"
        merged.save(target)
        return [str(target)]

    if item.suffix.lower() == ".pdf":
        import fitz
        total = fitz.open(item).page_count
    else:
        total = 1
    rows, blocks, done = [], [], 0
    for idx, img in _pages(item, dpi):
        W, H = img.size
        boxes, _, skip = _boxes_for(item, idx, options)
        done += 1
        if progress:
            progress(done, total)
        if skip:
            continue
        psm = 6 if (_zone_mode(options) or len(boxes) > 1) else 3
        cells = []
        for b in boxes:
            b = (max(0, b[0]), max(0, b[1]), min(W, b[2]), min(H, b[3]))
            if b[2] <= b[0] or b[3] <= b[1]:
                cells.append("")
                continue
            txt = pytesseract.image_to_string(img.crop(b), lang=lang, config=f"--psm {psm}").strip()
            cells.append(txt)   # keep raw; each output mode formats it below
        rows.append((idx + 1, cells))
        disp = [_wrap_text(c) if wrap else c for c in cells]
        blocks.append(f"----- page {idx + 1} -----\n" + "\n\n".join(c for c in disp if c))

    if outmode.startswith("CSV"):
        import csv
        target = item.with_suffix(".csv") if overwrite else Path(output_dir) / f"{item.stem}.csv"
        maxc = max((len(c) for _, c in rows), default=1)
        with open(target, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["page"] + [f"zone {i + 1}" for i in range(maxc)])
            for pageno, c in rows:
                if wrap:
                    # wrap on: one flattened cell per zone (lines merged into a
                    # single line, de-hyphenated) — no embedded newlines
                    flat = [_wrap_text(x).replace("\n", " ") for x in c]
                    w.writerow([pageno] + flat + [""] * (maxc - len(c)))
                    continue
                # wrap off: a proper table — each zone is a column, each OCR line a
                # row, aligned by line number across zones (blank lines dropped)
                per_zone = [[ln.strip() for ln in x.splitlines() if ln.strip()] for x in c]
                nrows = max((len(z) for z in per_zone), default=0)
                for i in range(nrows):
                    line = [per_zone[z][i] if i < len(per_zone[z]) else "" for z in range(len(per_zone))]
                    w.writerow([pageno] + line + [""] * (maxc - len(per_zone)))
        return [str(target)]

    target = item.with_suffix(".txt") if overwrite else Path(output_dir) / f"{item.stem}.txt"
    Path(target).write_text("\n\n".join(blocks), encoding="utf-8")
    return [str(target)]
