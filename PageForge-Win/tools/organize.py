"""Combine and/or reorder pages into a single PDF — one tool for both jobs.

Load any mix of PDFs and images. The preview shows every page of every file in
one continuous grid (toggle "Show individual pages" off to arrange whole files
instead). Drag thumbnails into the order you want, click to select (Ctrl/Shift
for several) and press Delete to leave pages out. The result is one PDF built
from whatever remains, in the order shown.

With a single PDF loaded it behaves like "organize this document"
(``name-organized.pdf``); with several files it behaves like "merge these"
(``merged.pdf``). Images become full pages.
"""
from pathlib import Path

NAME = "Organize / Merge"
ACCEPTS = ("image", "pdf")
ORDER = 20
PREVIEW_KIND = "grid"
BATCH = True                     # process() receives the whole file list at once

OPTIONS = [                      # output name is handled by the app's output section
    {"key": "_show_pages", "label": "Show individual pages", "type": "bool",
     "default": True},
]


def preview_grid(context, options):
    """Every page of every loaded file, continuously (click to select, drag to
    reorder, Delete to remove) — or, with the toggle off, one thumbnail per
    whole file for pure merging."""
    src = "pages" if options.get("_show_pages", True) else "files"
    return {"source": src, "reorderable": True, "selectable": True}


def on_reorder(order, options, context):
    # the host keeps the live order and passes it to process() as _grid_order /
    # the resolved _grid_sequence.
    return None


def process(items, output_dir, options, overwrite, progress=None):
    """Rebuild one PDF from the pages that remain, in the order shown. The host
    hands us `_grid_sequence` = ordered (file_index, pageno|None) tuples across
    every loaded file (pageno None = the whole file / a single image)."""
    import fitz
    files = [Path(x) for x in items]
    docs = {}

    def _doc(fi):
        if fi not in docs:
            docs[fi] = fitz.open(files[fi])
        return docs[fi]

    def _emit(out, fi, pageno):
        d = _doc(fi)
        if d.is_pdf:
            if pageno is None:
                out.insert_pdf(d)
            elif 0 <= pageno < d.page_count:
                out.insert_pdf(d, from_page=pageno, to_page=pageno)
        else:                                   # image → one full page
            out.insert_pdf(fitz.open("pdf", d.convert_to_pdf()))

    gseq = options.get("_grid_sequence")
    if isinstance(gseq, list) and gseq:
        seq = [(fi, pageno) for fi, pageno in gseq if 0 <= fi < len(files)]
    else:
        # fall back: every page of every file, in file order
        seq = []
        for fi in range(len(files)):
            d = _doc(fi)
            seq += [(fi, p) for p in range(d.page_count)] if d.is_pdf else [(fi, None)]

    if not seq:
        for d in docs.values():
            d.close()
        return []

    out = fitz.open()
    total = len(seq)
    for k, (fi, pageno) in enumerate(seq, start=1):
        _emit(out, fi, pageno)
        if progress:
            progress(k, total)

    # One input file → "organize this document"; several → "merge these".
    if len(files) == 1:
        stem = files[0].stem
        target = files[0] if overwrite else Path(output_dir) / f"{stem}-organized.pdf"
    else:
        target = Path(output_dir) / "merged.pdf"
        j = 2
        while target.exists():
            target = Path(output_dir) / f"merged ({j}).pdf"
            j += 1

    out.save(target)
    out.close()
    for d in docs.values():
        d.close()
    return [str(target)]
