#!/usr/bin/env python3
"""Headless verification for the PageForge Windows (Qt) host.

Runs under QT_QPA_PLATFORM=offscreen: constructs the whole window, loads real +
fixture tools, exercises every host<->tool contract call, renders each preview
surface, and runs a real end-to-end process(). The sandbox has no display, so
this cannot verify actual mouse dragging — but it drives every code path the
drags call into. GUI drag/drop still needs a live check on Windows.
"""
import os
import sys
import time
import json
import shutil
import tempfile
import traceback
from pathlib import Path

# --- isolate config into a temp %APPDATA% and force offscreen Qt ---
_tmp = Path(tempfile.mkdtemp(prefix="pf-test-"))
os.environ["APPDATA"] = str(_tmp / "roaming")
os.environ["LOCALAPPDATA"] = str(_tmp / "local")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = Path(__file__).resolve().parent
SCRIPTS = _tmp / "scripts"
SCRIPTS.mkdir(parents=True, exist_ok=True)
for src in list((HERE / "tools").glob("*.py")) + list((HERE / "testtools").glob("*.py")):
    shutil.copy(src, SCRIPTS / src.name)

# pre-seed config so first-run dialog never triggers
cfgdir = Path(os.environ["APPDATA"]) / "PageForge"
cfgdir.mkdir(parents=True, exist_ok=True)
(cfgdir / "config.json").write_text(json.dumps({
    "scripts_dir": str(SCRIPTS), "seeded": True, "disabled": {}}), encoding="utf-8")

sys.path.insert(0, str(HERE))
import pageforge as pf  # noqa: E402
# tools with unmet deps would pop a modal on launch; suppress it in headless runs
pf.MainWindow._prompt_new_dep_tools = lambda self: None
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PIL import Image  # noqa: E402
import fitz  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def pump(win, seconds=4.0, until=None):
    """Process events until `until()` is true or the time budget elapses."""
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


# --- synthetic media ---
media = _tmp / "media"
media.mkdir()
img1 = media / "a.png"
img2 = media / "b.png"
Image.new("RGB", (320, 200), (200, 120, 60)).save(img1)
Image.new("RGB", (240, 360), (60, 120, 200)).save(img2)
pdf = media / "doc.pdf"
d = fitz.open()
for _ in range(3):
    d.new_page(width=400, height=500)
d.save(str(pdf))
d.close()


def main():
    app = QApplication(sys.argv)
    win = pf.MainWindow(app)
    win.resize(1240, 820)

    # 1) tools loaded
    ids = set(win.tools)
    for t in ("upscale", "fix_options", "fix_grid", "fix_compare"):
        check(f"tool loaded: {t}", t in ids)
    # every real tool must register as a ToolSpec (dep tools load but stay inactive)
    for t in ("autocrop", "convert", "crop", "divide", "organize", "rename",
              "split_extract", "ocr", "easyocr_tool", "removebg"):
        check(f"real tool registered: {t}", t in ids, str(sorted(ids)))

    # 2) pure-logic parity
    check("grid_move basic", pf.grid_move([0, 1, 2, 3], 3, 1) == [0, 3, 1, 2],
          str(pf.grid_move([0, 1, 2, 3], 3, 1)))
    check("grid_move to-end", pf.grid_move([0, 1, 2], 0, 0) == [1, 2, 0])
    naming = {"keep": False, "name": "x", "add_number": True, "pad": 2, "prefix": True}
    check("name_with_number", win._name_with_number("orig", 3, naming) == "03 - x")
    check("reading_order", pf.reading_order([(0, 10, 1, 1), (0, 0, 1, 1)]) == [1, 0])

    # 3) contract-compatibility: upscale (a Linux-built tool) resolves fully
    up = win.tools["upscale"]
    check("upscale contract: process", callable(getattr(up.module, "process", None)))
    check("upscale accepts image", "image" in up.accepts)

    # 4) canvas tool: drive every preview mode + handles + zones + paint
    win._set_files([img1, img2, pdf])
    win.tool = "fix_options"
    win._rebuild_options()
    opts = win._read_opts()
    check("options: all getters present",
          {"count", "amount", "top", "bottom", "flag", "mode", "col", "note",
           "namelist", "onlywhenrect", "zorder", "kept"} <= set(opts),
          str(sorted(opts)))
    check("hidden option stored", "kept" in opts and opts["kept"] == 0)

    for mode in ("grid", "rect", "boxes", "zones"):
        win._set_option("mode", mode)
        win._refresh_preview()
        # render the canvas to force a full paintEvent for this overlay kind
        try:
            win.canvas.resize(600, 500)
            win.canvas.grab()
            painted = True
        except Exception:
            traceback.print_exc()
            painted = False
        check(f"canvas paints (mode={mode})", painted)

    # enabled_when: onlywhenrect only enabled in rect mode
    win._set_option("mode", "rect")
    win._refresh_preview()
    row = win._opt_rows.get("onlywhenrect")
    check("enabled_when active in rect", row is not None and row.isEnabled())
    win._set_option("mode", "grid")
    win._refresh_preview()
    check("enabled_when greys out otherwise",
          win._opt_rows.get("onlywhenrect") is not None
          and not win._opt_rows["onlywhenrect"].isEnabled())

    # visible_when: zones row hidden unless mode==zones
    win._set_option("mode", "zones")
    win._refresh_preview()
    check("regions enabled in zones mode", win._regions_enabled)
    win._regions[0] = [(0.1, 0.1, 0.5, 0.5), (0.2, 0.6, 0.9, 0.9)]
    zorder = win._zone_order(win._regions[0])
    check("zone_order as-drawn", zorder == [1, 2], str(zorder))
    win._set_option("zorder", "Top → bottom, left → right")
    check("zone_order sorted", win._zone_order(win._regions[0]) == [1, 2])
    # handle drag writes options
    win._set_option("mode", "grid")
    win._refresh_preview()
    win._apply_handle_drag("v", 0.75, 0.5)
    check("on_handle_drag wrote count", win._read_opts()["count"] == 7,
          str(win._read_opts()["count"]))

    # link toggle mirrors top<->bottom
    win.tool = "fix_options"
    win._rebuild_options()
    row = win._opt_rows["top"]
    # find the QToolButton link toggle in the linked-pair row
    from PySide6.QtWidgets import QToolButton
    tgl = row.findChild(QToolButton)
    tgl.setChecked(True)
    win._opt_setters["top"](42)
    check("link mirrors value", abs(win._read_opts()["bottom"] - 42) < 0.5,
          str(win._read_opts()["bottom"]))

    # 5) grid tool: build grid, reorder, select, split, sequence handoff
    win.tool = "fix_grid"
    win._rebuild_options()
    win._set_files([pdf, img1])          # 3 pages + 1 image = 4 units
    win._refresh_preview()
    check("grid built (4 units)", len(win._grid_items) == 4, str(len(win._grid_items)))
    check("grid order length", len(win._grid_order) == 4)
    order0 = list(win._grid_order)
    # simulate a reorder: move first unit to the end using the same code the drop uses
    win._grid_reorder_drop(order0[0], 99999, 99999)   # far point → end gap
    check("grid reorder changed order", win._grid_order != order0, str(win._grid_order))
    # selection
    win._grid_cell_click(win._grid_order[0], Qt.NoModifier)
    win._grid_cell_click(win._grid_order[2], Qt.ShiftModifier)
    check("grid shift-range selected 3", len(win._grid_selected) == 3,
          str(win._grid_selected))
    # split bars add/remove
    win._grid_splits = set()
    win._grid_split_mode = True
    win._grid_splits.add(2)
    win._grid_fire_split()
    check("split point recorded", 2 in win._grid_splits)
    # sequence handoff (what _process injects)
    units = win._grid_items
    seq = [(units[i]["file"], units[i]["pageno"]) for i in win._grid_order]
    check("grid_sequence well-formed", all(isinstance(t, tuple) and len(t) == 2 for t in seq),
          str(seq))

    # 6) compare tool: run heavy preview in background, confirm compare arms
    win.tool = "fix_compare"
    win._rebuild_options()
    win._set_files([img1])
    win._refresh_preview()
    win._run_image_preview()
    ok = pump(win, 6.0, until=lambda: bool(win._img_cache))
    check("preview_image produced a surface", ok and bool(win._img_cache))
    check("compare mode armed", win._compare_mode is True)
    try:
        win.canvas.resize(600, 500)
        win.canvas.grab()
        cok = True
    except Exception:
        traceback.print_exc()
        cok = False
    check("compare paints", cok)

    # 6b) detected-regions overlay (preview_regions)
    win.tool = "fix_detect"
    win._rebuild_options()
    win._set_files([img1])
    win._refresh_preview()
    win._run_image_preview()
    dok = pump(win, 6.0, until=lambda: bool(win._detected))
    check("preview_regions produced boxes", dok and len(win._detected) == 2,
          str(win._detected))
    try:
        win.canvas.resize(600, 500)
        win.canvas.grab()
        dpok = True
    except Exception:
        traceback.print_exc()
        dpok = False
    check("detected overlay paints", dpok)

    # 7) end-to-end real process() with output naming (upscale on an image)
    win.tool = "upscale"
    win._rebuild_options()
    win._set_files([img1, img2])
    outdir = _tmp / "out"
    win.output_dir = outdir
    win.out_number_switch.setChecked(True)          # add numbering
    win._process()
    # wait for the FINAL (renamed) result — numbering renames happen after write,
    # so wait until two files exist whose names begin with a digit.
    done = pump(win, 15.0, until=lambda: (outdir.exists() and
                len([p for p in outdir.glob("*") if p.name[:1].isdigit()]) >= 2))
    produced = sorted(outdir.glob("*")) if outdir.exists() else []
    check("process wrote 2 files", len(produced) == 2, str([p.name for p in produced]))
    if produced:
        im = Image.open(produced[0])
        check("upscaled 2x", im.size[0] >= 480, str(im.size))
        check("numbering applied", produced[0].name[:1].isdigit(), produced[0].name)

    # 8) language switch round-trips without crashing
    try:
        win._on_language_changed("fr")
        win._on_language_changed("en")
        lok = True
    except Exception:
        traceback.print_exc()
        lok = False
    check("language switch live-rebuild", lok)

    # 9) BATCH grid process end-to-end (sequence consumed by fix_grid)
    win.tool = "fix_grid"
    win._rebuild_options()
    win._set_files([pdf, img1])
    win._refresh_preview()
    gout = _tmp / "gout"
    win.output_dir = gout
    win.out_number_switch.setChecked(False)
    win._process()
    gdone = pump(win, 10.0, until=lambda: (gout / "grid_sequence.txt").exists())
    check("grid BATCH process wrote sequence", (gout / "grid_sequence.txt").exists())

    # 10) Split/Extract: computed-split bars re-derive after on_split (the host fix)
    check("split_extract tool loaded", "split_extract" in win.tools)
    if "split_extract" in win.tools:
        p6 = media / "six.pdf"
        d6 = fitz.open()
        for _ in range(6):
            d6.new_page(width=300, height=400)
        d6.save(str(p6))
        d6.close()
        win.tool = "split_extract"
        win._rebuild_options()
        win._set_option("operation", "Split — into N files")
        win._set_option("count", 3)
        win._set_files([p6])
        win._refresh_preview()
        check("split: computed bars at even cuts", win._grid_splits == {2, 4},
              str(win._grid_splits))
        # simulate dragging the first bar from gap 2 → gap 3
        win._grid_splits = {3, 4}
        win._grid_split_mode = True
        win._grid_fire_split()
        check("split: on_split rewrote N", win._read_opts().get("count") == 2,
              str(win._read_opts().get("count")))
        check("split: bars re-spaced after refresh", win._grid_splits == {3},
              str(win._grid_splits))
        # explicit cuts (OP_AT) write the 'pages' field verbatim
        win._set_option("operation", "Split — at pages")
        win._refresh_preview()
        win._grid_splits = {2, 4}
        win._grid_split_mode = True
        win._grid_fire_split()
        check("split OP_AT writes pages verbatim",
              win._read_opts().get("pages") == "2, 4",
              repr(win._read_opts().get("pages")))
        # extract mode selection writes a compacted page list
        win._set_option("operation", "Extract — keep pages")
        win._refresh_preview()
        win._grid_cell_click(0, Qt.NoModifier)
        win._grid_cell_click(2, Qt.ShiftModifier)
        check("extract: selection fills pages", win._read_opts().get("pages") == "1-3",
              repr(win._read_opts().get("pages")))

    # 11) warning system (file-type mismatch, extract-empty, convert mode, partial)
    p3 = media / "three.pdf"
    d3 = fitz.open()
    for _ in range(3):
        d3.new_page(width=300, height=400)
    d3.save(str(p3))
    d3.close()
    if "split_extract" in win.tools:
        win.tool = "split_extract"
        win._rebuild_options()
        win._set_files([img1])
        win._refresh_preview()
        check("warn: split/extract on image",
              win._compute_warning() == "This tool works on PDF files, but you've loaded images.",
              repr(win._compute_warning()))
        win._set_option("operation", "Extract — keep pages")
        win._set_option("pages", "")
        win._set_files([p3])
        win._refresh_preview()
        check("warn: extract with no pages",
              (win._compute_warning() or "").startswith("No pages selected"),
              repr(win._compute_warning()))
        win._set_option("pages", "1-2")
        win._refresh_preview()
        check("warn: extract clears once pages set", win._compute_warning() is None,
              repr(win._compute_warning()))
        win._set_option("operation", "Split — into N files")
        win._set_files([p3, img1])
        win._refresh_preview()
        w = win._compute_warning()
        check("warn: split mixed → partial-skip", bool(w) and "skipped" in w, repr(w))
    if "upscale" in win.tools:
        win.tool = "upscale"
        win._rebuild_options()
        win._set_files([p3])
        win._refresh_preview()
        check("warn: upscale on pdf",
              win._compute_warning() == "This tool works on images, but you've loaded PDF files.",
              repr(win._compute_warning()))
    if "convert" in win.tools:
        win.tool = "convert"
        win._rebuild_options()
        win._set_option("mode", "PDF → images")
        win._set_files([img1])
        win._refresh_preview()
        check("warn: convert mode vs file type",
              win._compute_warning() == "This mode converts PDFs to images, but you've loaded images.",
              repr(win._compute_warning()))
    # upscale now exposes the compare preview hooks
    up = win.tools.get("upscale")
    check("upscale has preview_image", bool(up and up.has_preview_image))
    check("upscale opts into compare", bool(up and up.has_compare))

    print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
