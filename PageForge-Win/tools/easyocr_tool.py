"""OCR (EasyOCR) — automatic OCR with a neural engine (much stronger than Tesseract).

EasyOCR finds text as many small pieces; this tool runs a cleanup pass that first
segments the page into layout regions (columns and sections) with a recursive
XY-cut — projecting the detected boxes onto each axis and splitting on whitespace
gaps: horizontal gaps separate sections, and column gutters are detected as
tall low-coverage channels, so a heading or a sloppy detection box crossing the
gutter does not hide it. Within each region, pieces
are merged into lines and then into blocks/paragraphs using geometry cues (line
height/size, left-alignment, vertical gaps). Regions are emitted in reading
order: top to bottom, left to right within a band. "Join wrapped lines" then
reflows each block into continuous text (de-hyphenating line breaks).

It also reads a PDF's real text layer when present (perfect on clean/digital PDFs,
no OCR needed). When a text layer is available the detected-block preview is drawn
automatically (it's instant); otherwise click "Generate preview" to run the engine.
Enabling this tool installs EasyOCR (pulls in PyTorch) and downloads models on first
run."""
from pathlib import Path

NAME = "OCR (EasyOCR)"
ACCEPTS = ("image", "pdf")
ORDER = 70
REQUIRES = ["easyocr"]
MODULES = ["easyocr"]
PREVIEW_ON_DEMAND = True
OPTIONS = [
    {"key": "use_text_layer", "label": "Use PDF text layer when present", "type": "bool", "default": True},
    {"key": "group_blocks", "label": "Merge pieces into blocks", "type": "bool", "default": True},
    {"key": "columns", "label": "Detect columns / sections", "type": "bool", "default": True},
    {"key": "join_lines", "label": "Join wrapped lines into paragraphs", "type": "bool", "default": True},
    {"key": "lang", "label": "Language code (e.g. en, fr)", "type": "text", "default": "en"},
    {"key": "dpi", "label": "Render DPI (scans)", "type": "int", "default": 300, "min": 72, "max": 600},
    {"key": "output", "label": "Output", "type": "choice",
     "choices": ["Text file", "CSV (blocks)", "Searchable PDF"], "default": "Text file"},
]

# The colour this tool draws its detected-block overlay in (blue).
PREVIEW_COLOR = "#1c70d1"

# --- layout tuning (all sizes in units of the median detected line height) ---
Y_CUT = 2.0      # horizontal whitespace band this tall splits sections
X_CUT = 0.6      # a column gutter must be at least this wide...
X_LEAK = 0.05    # ...but may still be crossed by up to this fraction of the
                 # boxes (a heading spanning the gutter, OCR box overshoot)
X_MIN_SIDE = 2   # a column split must leave at least this many pieces per side
X_MIN_H = 3.0    # only look for columns in regions taller than this
SHRINK = 0.2     # trim box edges by this before profiling (OCR boxes overshoot)

_R = {"reader": None, "lang": None}


def _reader(lang):
    import easyocr
    if _R["reader"] is None or _R["lang"] != lang:
        _R["reader"] = easyocr.Reader([lang])
        _R["lang"] = lang
    return _R["reader"]


def _render(page, dpi):
    import fitz
    from PIL import Image
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _join_para(lines):
    para = ""
    for lt in lines:
        lt = lt.strip()
        if not lt:
            continue
        if not para:
            para = lt
        elif para.endswith("-") and len(para) >= 2 and para[-2].isalpha() and lt[:1].islower():
            para = para[:-1] + lt   # de-hyphenate a real word break only
        else:
            para += " " + lt
    return para


# ---------------------------------------------------------------------------
# Layout segmentation: recursive XY-cut over the detected boxes
# ---------------------------------------------------------------------------

def _gaps(boxes, axis):
    """Whitespace gaps in the projection of boxes onto an axis (0=x, 1=y).
    Returns [(gap_start, gap_end), ...] between merged occupied intervals."""
    ivs = sorted((b[axis], b[axis + 2]) for b in boxes)
    merged = [list(ivs[0])]
    for a, c in ivs[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], c)
        else:
            merged.append([a, c])
    return [(p[1], q[0]) for p, q in zip(merged, merged[1:])]


def _split_at(dets, cuts, axis):
    """Partition dets at the given cut positions along an axis (by box centre).
    Groups come back in ascending order: top->bottom or left->right."""
    cuts = sorted(cuts)
    groups = [[] for _ in range(len(cuts) + 1)]
    for d in dets:
        c = (d[0][axis] + d[0][axis + 2]) / 2
        groups[sum(1 for x in cuts if c > x)].append(d)
    return [g for g in groups if g]


def _coverage_valleys(boxes, hmed, tol):
    """Maximal x-intervals covered by at most `tol` boxes (gutter candidates).
    Box edges are trimmed by SHRINK*hmed first, since OCR boxes overshoot and
    can close a narrow gutter."""
    eps = SHRINK * hmed
    ev = []
    for x0, _y0, x1, _y1 in boxes:
        a, b = x0 + eps, x1 - eps
        if b <= a:
            a = b = (x0 + x1) / 2
        ev.append((a, 1))
        ev.append((b, -1))
    ev.sort()
    valleys, cov, vstart = [], 0, None
    for x, d in ev:
        cov += d
        if cov <= tol and vstart is None:
            vstart = x
        elif cov > tol and vstart is not None:
            if x > vstart:
                valleys.append((vstart, x))
            vstart = None
    return valleys


def _xy_cut(dets, hmed, img_w, depth=0):
    """Recursively split detections into layout regions.
    Sections split on horizontal whitespace bands. Columns split on tall
    low-coverage channels: unlike a strict projection gap, a gutter is still
    found when a heading or a sloppy OCR box crosses it (up to a small leak).
    Leaf regions come back in reading order: top->bottom, left->right."""
    if depth > 8 or len(dets) <= 1:
        return [dets]
    boxes = [b for b, _ in dets]

    # 1) sections: big horizontal whitespace bands
    ycuts = [(g0 + g1) / 2 for g0, g1 in _gaps(boxes, 1) if g1 - g0 > Y_CUT * hmed]
    if ycuts:
        return [r for band in _split_at(dets, ycuts, 1)
                for r in _xy_cut(band, hmed, img_w, depth + 1)]

    # 2) columns: low-coverage vertical channels. Regions only a few lines
    # tall are skipped, otherwise a single line would split at its word gaps.
    ry0 = min(b[1] for b in boxes)
    ry1 = max(b[3] for b in boxes)
    if ry1 - ry0 > X_MIN_H * hmed:
        tol = max(1, min(4, int(X_LEAK * len(boxes))))
        xcuts = []
        for v0, v1 in _coverage_valleys(boxes, hmed, tol):
            if v1 - v0 < X_CUT * hmed:
                continue
            left = sum(1 for b in boxes if (b[0] + b[2]) / 2 < v0)
            right = sum(1 for b in boxes if (b[0] + b[2]) / 2 > v1)
            if left >= X_MIN_SIDE and right >= X_MIN_SIDE:
                xcuts.append((v0 + v1) / 2)
        if xcuts:
            return [r for col in _split_at(dets, xcuts, 0)
                    for r in _xy_cut(col, hmed, img_w, depth + 1)]
    return [dets]


# ---------------------------------------------------------------------------
# Within one region: pieces -> lines -> blocks (the original merge logic)
# ---------------------------------------------------------------------------

def _lines_to_blocks(dets, hmed, img_w):
    if not dets:
        return []
    # 1) merge pieces into lines (same row = vertical centres within 0.5 * median height)
    lines = []
    for b, t in sorted(dets, key=lambda d: ((d[0][1] + d[0][3]) / 2, d[0][0])):
        cy = (b[1] + b[3]) / 2
        placed = False
        for ln in lines:
            if abs(cy - ln["cy"]) <= 0.5 * hmed:
                ln["items"].append((b, t))
                ys = [bb[1] for bb, _ in ln["items"]] + [bb[3] for bb, _ in ln["items"]]
                ln["cy"] = sum(ys) / len(ys)
                placed = True
                break
        if not placed:
            lines.append({"cy": cy, "items": [(b, t)]})
    line_objs = []
    for ln in lines:
        its = sorted(ln["items"], key=lambda d: d[0][0])
        x0 = min(bb[0] for bb, _ in its); y0 = min(bb[1] for bb, _ in its)
        x1 = max(bb[2] for bb, _ in its); y1 = max(bb[3] for bb, _ in its)
        line_objs.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "h": y1 - y0,
                          "text": " ".join(t for _, t in its)})
    line_objs.sort(key=lambda l: (l["y0"], l["x0"]))

    # 2) merge lines into blocks (small gap + aligned left edge + similar height).
    # Left-edge tolerance is relative to the region (column) width, not the page,
    # so narrow columns don't get over-merged; floored at 1.5 line heights.
    reg_w = max(l["x1"] for l in line_objs) - min(l["x0"] for l in line_objs)
    tolx = max(0.06 * (reg_w or img_w), 1.5 * hmed)
    blocks, cur = [], None
    for l in line_objs:
        if cur is None:
            cur = {"lines": [l], "x0": l["x0"], "y0": l["y0"], "x1": l["x1"], "y1": l["y1"]}
            continue
        prev = cur["lines"][-1]
        gap = l["y0"] - prev["y1"]
        hr = (l["h"] / prev["h"]) if prev["h"] else 1.0
        same = gap <= 0.9 * hmed and abs(l["x0"] - cur["x0"]) <= tolx and 0.6 <= hr <= 1.6
        if same:
            cur["lines"].append(l)
            cur["x0"] = min(cur["x0"], l["x0"]); cur["y0"] = min(cur["y0"], l["y0"])
            cur["x1"] = max(cur["x1"], l["x1"]); cur["y1"] = max(cur["y1"], l["y1"])
        else:
            blocks.append(cur)
            cur = {"lines": [l], "x0": l["x0"], "y0": l["y0"], "x1": l["x1"], "y1": l["y1"]}
    if cur:
        blocks.append(cur)
    return [{"bbox": (b["x0"], b["y0"], b["x1"], b["y1"]),
             "lines": [l["text"] for l in b["lines"]]} for b in blocks]


def _cluster_blocks(dets, img_w, columns=True):
    """dets: list of ((x0,y0,x1,y1), text) in pixels. Returns blocks in reading
    order: [{"bbox":(x0,y0,x1,y1), "lines":[str,...]}]. Segments the page into
    columns/sections first (XY-cut), then merges pieces -> lines -> blocks
    inside each region so nothing is ever merged across a column boundary."""
    if not dets:
        return []
    heights = sorted((b[3] - b[1]) for b, _ in dets)
    hmed = heights[len(heights) // 2] or 10
    regions = _xy_cut(dets, hmed, img_w) if columns else [dets]
    blocks = []
    for reg in regions:
        blocks.extend(_lines_to_blocks(reg, hmed, img_w))
    return blocks


def _block_text(block, join):
    return _join_para(block["lines"]) if join else "\n".join(block["lines"])


def _detect_page(item, page_index, options):
    """Return (dets in pixels, W, H). Uses text layer if present, else EasyOCR."""
    item = Path(item)
    if item.suffix.lower() == ".pdf":
        import fitz
        page = fitz.open(item)[page_index]
        if options.get("use_text_layer", True) and page.get_text("text").strip():
            R = page.rect
            dets = [((b[0], b[1], b[2], b[3]), b[4].strip())
                    for b in page.get_text("blocks") if b[4].strip()]
            return dets, R.width, R.height
        img = _render(page, int(options.get("dpi", 300)))
    else:
        from PIL import Image
        img = Image.open(item).convert("RGB")
    import numpy as np
    W, H = img.size
    reader = _reader((options.get("lang") or "en").strip())
    dets = []
    for poly, text, _c in reader.readtext(np.asarray(img)):
        xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
        dets.append(((min(xs), min(ys), max(xs), max(ys)), text))
    return dets, W, H


def _page_blocks(item, page_index, options):
    dets, W, H = _detect_page(item, page_index, options)
    if options.get("group_blocks", True):
        blocks = _cluster_blocks(dets, W, columns=bool(options.get("columns", True)))
    else:
        blocks = [{"bbox": b, "lines": [t]} for b, t in
                  sorted(dets, key=lambda d: (d[0][1], d[0][0]))]
    return blocks, W, H


def needs_preview_button(item, page_index, options):
    """Whether the preview must be triggered manually. Reading a PDF's real text
    layer is instant, so in that case the preview is generated automatically (no
    button). When the neural engine has to run (an image, or a scanned page with
    no text layer), it's slow, so gate it behind the Generate-preview button."""
    if not options.get("use_text_layer", True):
        return True
    if Path(item).suffix.lower() != ".pdf":
        return True
    try:
        import fitz
        return not fitz.open(item)[page_index].get_text("text").strip()
    except Exception:
        return True


def preview_regions(item, page_index, options):
    blocks, W, H = _page_blocks(item, page_index, options)
    boxes = [(b["bbox"][0] / W, b["bbox"][1] / H, b["bbox"][2] / W, b["bbox"][3] / H)
             for b in blocks]
    # declare our own overlay colour rather than relying on an app default
    return {"boxes": boxes, "color": PREVIEW_COLOR}


def process(item, output_dir, options, overwrite, progress=None):
    item = Path(item)
    outmode = options.get("output", "Text file")
    join = bool(options.get("join_lines", True))
    is_pdf = item.suffix.lower() == ".pdf"
    if is_pdf:
        import fitz
        pages = list(range(fitz.open(item).page_count))
    else:
        pages = [0]
    total = len(pages)

    if outmode.startswith("Searchable"):
        import fitz
        out = fitz.open()
        doc = fitz.open(item) if is_pdf else None
        for i, pi in enumerate(pages, start=1):
            if is_pdf and options.get("use_text_layer", True) and doc[pi].get_text("text").strip():
                out.insert_pdf(doc, from_page=pi, to_page=pi)
            else:
                from PIL import Image
                img = _render(doc[pi], int(options.get("dpi", 300))) if is_pdf \
                    else Image.open(item).convert("RGB")
                blocks, W, H = _page_blocks(item, pi, options)
                page = out.new_page(width=W, height=H)
                bio = __import__("io").BytesIO(); img.save(bio, "PNG")
                page.insert_image(page.rect, stream=bio.getvalue())
                for blk in blocks:
                    x0, y0, x1, y1 = blk["bbox"]
                    try:
                        page.insert_textbox(fitz.Rect(x0, y0, x1, y1), _block_text(blk, join),
                                            fontsize=max(6, (y1 - y0) / max(1, len(blk["lines"])) * 0.8),
                                            render_mode=3)
                    except Exception:
                        pass
            if progress:
                progress(i, total)
        target = item if overwrite else Path(output_dir) / f"{item.stem}.pdf"
        out.save(target)
        return [str(target)]

    text_blocks, csv_rows = [], []
    for i, pi in enumerate(pages, start=1):
        blocks, _, _ = _page_blocks(item, pi, options)
        page_txt = [_block_text(b, join) for b in blocks]
        text_blocks.append(f"----- page {pi + 1} -----\n" + "\n\n".join(t for t in page_txt if t))
        for bi, t in enumerate(page_txt, start=1):
            csv_rows.append((pi + 1, bi, t))
        if progress:
            progress(i, total)

    if outmode.startswith("CSV"):
        import csv
        target = item.with_suffix(".csv") if overwrite else Path(output_dir) / f"{item.stem}.csv"
        with open(target, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["page", "block", "text"])
            for page, block, t in csv_rows:
                w.writerow([page, block, t.replace("\n", " ")])
        return [str(target)]

    target = item.with_suffix(".txt") if overwrite else Path(output_dir) / f"{item.stem}.txt"
    Path(target).write_text("\n\n".join(text_blocks), encoding="utf-8")
    return [str(target)]
