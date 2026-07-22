"""Split a PDF into several files, or extract/delete a page list — one tool.

A single **Operation** dropdown picks what happens; the two fields below adapt to it:

  • "Pages" is a page list — e.g. ``1-5, 8, 11-13``.
      – Extract modes use it as the pages to keep or delete.
      – "Split — at pages" uses it as the split points (cut *after* those pages).
  • "N" is a count, used only by "Split — into N files" and "Split — every N pages".

Whichever field an operation doesn't need is simply ignored, so there's never more
than one number and one list to think about.

Extract writes one file (``name-extract.pdf``); the split modes write several
(``name_part01.pdf`` …).

Every operation previews on the page grid. Extract modes let you click pages to
select what's kept/dropped. All split modes draw draggable split bars between the
pages that show where the document will be cut:

  • "Split — at pages": free cuts — click a gap to add one, click a bar to remove
    it, drag to move it.
  • "Split — into N files" / "Split — every N pages": the bars sit at the evenly
    computed cuts; dragging any bar changes N, so every bar re-spaces to match.
"""
from pathlib import Path

NAME = "Split / Extract"
ACCEPTS = ("pdf",)
ORDER = 30

# ---- operations -----------------------------------------------------------
OP_KEEP   = "Extract — keep pages"
OP_DROP   = "Extract — delete pages"
OP_INTO_N = "Split — into N files"
OP_EVERY  = "Split — every N pages"
OP_AT     = "Split — at pages"

_EXTRACT  = (OP_KEEP, OP_DROP)
_SPLIT    = (OP_INTO_N, OP_EVERY, OP_AT)
_COMPUTED = (OP_INTO_N, OP_EVERY)   # evenly-spaced cuts derived from N

OPTIONS = [
    {"key": "operation", "label": "Operation", "type": "choice",
     "choices": [OP_KEEP, OP_DROP, OP_INTO_N, OP_EVERY, OP_AT],
     "default": OP_KEEP},
    {"key": "pages", "label": "Pages / split points (e.g. 1-5, 8, 11-13)",
     "type": "text", "default": ""},
    {"key": "count", "label": "N  (for split into / every N)", "type": "int",
     "default": 2, "min": 1, "max": 999},
]


# ---- shared parsing -------------------------------------------------------
def _parse_pages(text, n):
    """A page list like '1-5, 8, 11-13' → ordered, de-duped 1-based pages in range."""
    picked = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                picked += list(range(min(a, b), max(a, b) + 1))
            else:
                picked.append(int(part))
        except ValueError:
            continue
    picked = [p for p in picked if 1 <= p <= n]
    seen, out = set(), []
    for p in picked:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _split_points(text, n):
    """Cut points for 'Split — at pages': integers 1..n-1, sorted and de-duped."""
    pts = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            p = int(part)
        except ValueError:
            continue
        if 1 <= p < n:
            pts.append(p)
    return sorted(set(pts))


def _keep_pages(operation, pages_text, n):
    """1-based pages an Extract op keeps, in page order."""
    listed = _parse_pages(pages_text, n)
    if operation == OP_KEEP:
        return listed
    drop = set(listed)
    return [p for p in range(1, n + 1) if p not in drop]


def _chunks(n_pages, operation, count, at_text):
    """A split op → list of page-index chunks (0-based)."""
    pages = list(range(n_pages))
    if operation == OP_EVERY:
        size = max(1, count)
        return [pages[i:i + size] for i in range(0, n_pages, size)]
    if operation == OP_AT:
        out, prev = [], 0
        for pt in _split_points(at_text, n_pages):
            out.append(pages[prev:pt])
            prev = pt
        out.append(pages[prev:])
        return [c for c in out if c]
    # OP_INTO_N: as-even-as-possible parts
    parts = max(1, min(count, n_pages))
    base, extra = divmod(n_pages, parts)
    out, i = [], 0
    for k in range(parts):
        size = base + (1 if k < extra else 0)
        out.append(pages[i:i + size])
        i += size
    return [c for c in out if c]


# ---- preview --------------------------------------------------------------
def preview_kind(item, options):
    """Every operation previews on the page grid: Extract selects pages, the
    split modes draw split bars where the document will be cut."""
    return "grid"


def _computed_cuts(n, operation, count):
    """Cut positions (1-based cut-after-page) for a computed split op — i.e. the
    cumulative page counts of every chunk except the last."""
    chunks = _chunks(n, operation, count, "")
    cuts, acc = [], 0
    for c in chunks[:-1]:
        acc += len(c)
        cuts.append(acc)
    return cuts


def _page_count(item):
    import fitz
    try:
        return fitz.open(str(item)).page_count
    except Exception:
        return 0


def _ctx_page_count(context):
    files = context.get("files") or []
    idx = context.get("index", 0)
    item = files[idx] if 0 <= idx < len(files) else None
    return _page_count(item) if item else 0


def _moved_bar(old, new):
    """One bar was dragged: return (rank, new_pos) of the bar that appears at a
    position not in the old set (rank is its 1-based order among the new cuts),
    or None if the change isn't a single clean move."""
    old_s, new_s = sorted(set(old)), sorted(set(new))
    if len(old_s) != len(new_s):
        return None
    added = [p for p in new_s if p not in set(old_s)]
    if len(added) != 1:
        return None
    p = added[0]
    return (new_s.index(p) + 1, p)


def _compact(pages):
    """[1,2,3,5,8,9] -> '1-3, 5, 8-9'."""
    pages = sorted(set(int(p) for p in pages))
    if not pages:
        return ""
    runs, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        runs.append((start, prev)); start = prev = p
    runs.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def preview_grid(context, options):
    """Extract: click pages to select them (Ctrl toggles, Shift ranges); the
    selection fills the Pages field. Split — at pages: editable split bars —
    click a gap to add a cut, click a bar to remove it, drag a bar to move it.
    The computed split modes draw the evenly-spaced cuts as draggable bars;
    dragging one recomputes N (see on_split)."""
    n = _ctx_page_count(context)
    op = options.get("operation", OP_KEEP)
    if op == OP_AT:
        return {"source": "pages", "selectable": False, "reorderable": False,
                "split_bars": True, "splits": _split_points(options.get("pages", ""), n)}
    if op in _COMPUTED:                                   # into N / every N
        cuts = _computed_cuts(n, op, int(options.get("count", 2) or 2))
        return {"source": "pages", "selectable": False, "reorderable": False,
                "split_bars": True, "splits": cuts}
    listed = _parse_pages(options.get("pages", ""), n)   # Extract: whatever's typed
    return {"source": "pages", "selectable": True, "reorderable": False,
            "selected": [p - 1 for p in listed]}


def on_select(indices, options, context):
    """Selected 0-based page indices -> compacted 1-based list into 'pages'."""
    return {"pages": _compact(i + 1 for i in indices)}


def on_split(points, options, context):
    """The host hands back the full set of cut positions after any bar edit.

    • Split — at pages: store them verbatim in the 'pages' field.
    • Split — into N files / every N pages: the bars are evenly spaced, so a
      single parameter (N) drives them. Infer N from the dragged bar and write
      'count'; the preview then re-derives every bar evenly, so moving one bar
      re-spaces the rest. Adding/removing a bar changes the piece count."""
    op = options.get("operation", OP_KEEP)
    pts = sorted({int(p) for p in points})
    if op not in _COMPUTED:
        return {"pages": ", ".join(str(p) for p in pts)}

    n = _ctx_page_count(context)
    if n <= 1:
        return {}
    pts = [p for p in pts if 1 <= p < n]
    old = _computed_cuts(n, op, int(options.get("count", 2) or 2))

    moved = _moved_bar(old, pts)
    if moved:
        rank, pos = moved
        if op == OP_EVERY:                 # cut `rank` sits at rank*N
            n_val = round(pos / rank)
        else:                              # OP_INTO_N: cut `rank` at rank*n/N
            n_val = round(rank * n / pos)
    else:                                  # a bar was added or removed
        pieces = len(pts) + 1
        n_val = pieces if op == OP_INTO_N else round(n / max(1, pieces))

    return {"count": max(1, min(n, int(n_val)))}


def preview(item, page_index, options, context):
    op = options.get("operation", OP_KEEP)
    if op not in _EXTRACT:
        return None  # split keeps every page — a plain source preview is clearer
    n = _page_count(item)
    kept = (page_index + 1) in set(_keep_pages(op, options.get("pages", ""), n))
    return {"rect": (0.012, 0.012, 0.988, 0.988), "space": "fraction",
            "color": "#2a9d3a" if kept else "#c0392b"}


# ---- run ------------------------------------------------------------------
def process(item, output_dir, options, overwrite, progress=None):
    import fitz
    item = Path(item)
    op = options.get("operation", OP_KEEP)
    doc = fitz.open(item)
    n = doc.page_count

    if op in _EXTRACT:
        keep = _keep_pages(op, options.get("pages", ""), n)
        if not keep:
            doc.close()
            return []
        out = fitz.open()
        for p in keep:
            out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        target = item if overwrite else Path(output_dir) / f"{item.stem}-extract.pdf"
        out.save(target)
        out.close()
        doc.close()
        if progress:
            progress(1, 1)
        return [str(target)]

    # split
    chunks = _chunks(n, op, int(options.get("count", 2) or 2), options.get("pages", ""))
    written = []
    total = len(chunks)
    width = max(2, len(str(total)))
    for idx, pages in enumerate(chunks, start=1):
        part = fitz.open()
        for p in pages:
            part.insert_pdf(doc, from_page=p, to_page=p)
        target = Path(output_dir) / f"{item.stem}_part{idx:0{width}d}.pdf"
        part.save(target)
        part.close()
        written.append(str(target))
        if progress:
            progress(idx, total)
    doc.close()
    return written
