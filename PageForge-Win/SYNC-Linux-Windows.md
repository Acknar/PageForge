# PageForge — Linux ↔ Windows Sync & Divergence Log

This document tracks how the **Windows edition** (PySide6/Qt, this repo's
`PageForge-Win/`) differs from the **Linux original** (GTK4/Libadwaita), so a
feature written on one side can be mirrored on the other, and so intentional,
platform-justified divergences are never "fixed" by mistake.

Read this before porting anything in either direction.

**How to use it**

- **Adding a feature?** Check the *Tool-file changes* and *Host features to
  keep in sync* tables — if it lives in a `tools/*.py` file it is almost always
  back-portable verbatim; if it lives in the host, note whether the other
  platform needs the mirror.
- **Touching a listed system-specific divergence?** Read *why* it diverges
  first. Those are deliberate and must stay per-platform.
- **Versioning:** last digit = fixes to existing features, middle = new
  features, first = milestone (ask before bumping the first). Keep the two
  editions' feature numbers conceptually aligned even if the exact strings
  differ; note the mapping here when they drift.

Windows edition version at last update: **1.9.1**.
Linux edition baseline this was reconciled against: **1.7.x** (split/extract
grid-bars fix integrated).

---

## 0. The invariant that makes syncing possible

The **tool contract is byte-for-byte identical** on both platforms. Every
`tools/*.py` is pure Python, imports no UI toolkit, and communicates only through
plain dicts/primitives. A script written or edited on Linux drops into the
Windows scripts folder unchanged, and vice-versa.

Shared, unchanged on both sides:

- `ToolSpec` attributes: `NAME`, `ACCEPTS`, `BATCH`, `ORDER`, `OPTIONS`,
  `REQUIRES` / `MODULES` / `SYSTEM_REQUIRES`, `PREVIEW_KIND`, `ZONE_COLOR`,
  `COMPARE_PREVIEW`, `PREVIEW_ON_DEMAND`.
- Functions: `process`, `preview`, `preview_image`, `preview_regions`,
  `preview_grid`, `preview_kind`, `on_handle_drag`, `on_select`, `on_reorder`,
  `on_split`.
- `context` keys: `files`, `index`, `page_index`, `page_w`, `page_h`
  (and the `_grid_order` / `_grid_selected` reserved option keys).
- Option types: `int`, `float`, `bool`, `choice`, `color`, `text`, `file`,
  `regions`; plus `link_with`, `enabled_when`, `visible_when`, `hidden`.
- i18n rule: a `choice` option's **logical value stays the English string**;
  only the displayed label is translated.

**Golden rule:** anything a tool file relies on must exist on both hosts. When a
new host capability is added (see the two generic hooks below), it must be added
as *optional* — a host that doesn't implement it ignores the tool function, and
a tool that doesn't implement it is unaffected. That is what keeps scripts
cross-compatible in both directions.

---

## 1. Tool-file changes (back-portable — should exist on BOTH platforms)

These live in `tools/*.py`, so they are portable by definition. The seed copies
in this repo's `tools/` carry the Windows-side edits; copy the same files into
the Linux `tools/` to converge. All are backward-compatible: older hosts simply
don't call the new functions.

| Tool file | Change | Contract surface | Back-port |
|---|---|---|---|
| `upscale.py` | Added a real before/after slider preview: `PREVIEW_ON_DEMAND = True`, `COMPARE_PREVIEW = True`, and a `preview_image()`. `process()` unchanged. | Uses existing `preview_image` + compare contract already present on Linux. | Copy file over. No host change needed. |
| `convert.py` | Added `warning(context, options)` — flags a Mode/file-kind mismatch. Added `suggest_options(context, options)` — auto-selects "PDF → images" for PDFs and an images-mode for images. | Two **new optional host hooks** (see §2). | Copy file over. Fully works once the host implements the hooks; harmless if not. |
| `split_extract.py` | Added `warning(context, options)` — "No pages selected…" when an Extract mode resolves to zero pages. (The grid-bars split logic itself came *from* Linux and is already shared.) | New optional `warning` hook. | Copy file over. |

> If you edit any of these on Linux later, bring the change back here the same
> way — the files are meant to stay identical.

---

## 2. New generic host hooks (contract additions — mirror on Linux)

Two **optional, tool-facing** functions were added to the contract on Windows.
They are generic (not tool-specific), so implementing them on the Linux host
lets the same tool files light up there too. Both are no-ops when absent on
either side.

### `warning(context, options) -> str | None`
The tool returns a short human sentence explaining why, in the current state, it
would produce nothing useful (wrong file kind for the mode, no pages selected,
etc.), or `None` if all is well. The host surfaces it in the warning system
(§3). Priority when the host computes what to show: a **strong file-type
mismatch** (tool's `ACCEPTS` vs. loaded kinds) wins over the tool's own
`warning()`, which wins over a partial-skip notice. (Reason for that order:
PyMuPDF opens an image as a 1-page document, so a page-based tool would
otherwise emit "no pages" before the clearer "wrong file type" message.)

### `suggest_options(context, options) -> dict`
Called by the host **only when the files or the selected tool change** (never on
every keystroke). Returns option writes to apply as smart defaults, or `{}`.
Must only override an *obviously wrong* value so a deliberate user choice is left
alone (e.g. `convert.py` flips Mode to "PDF → images" for a pure-PDF load, but
won't fight a user who picked an images mode with images loaded).

**Linux back-port checklist for these two hooks:**

1. In the host's `ToolSpec`, detect `has_warning` / `has_suggest_options` like
   the other optional functions.
2. Call `suggest_options()` from wherever the file list or active tool changes,
   applying writes through the existing option write-back path (`_set_option` /
   the setter map), guarded so it doesn't recurse into a refresh loop.
3. Compute and render `warning()` output in the host's warning UI (Linux would
   use its own Adwaita surface; see §3 for the message catalogue to reuse).

---

## 3. The warning / "why did nothing happen?" system (Windows-first — port to Linux)

Implemented on the Windows host as an amber warning icon left of the settings
cogwheel, toggling a small floating bubble with an × to close. The icon is shown
whenever the current tool + file combination has a caveat, and hidden otherwise.
Messages are translated (English catalogue + French `TRANSLATIONS["fr"]`).

The **logic is platform-agnostic** and should be mirrored on Linux (the widget
is not — Linux would present the same messages via its own toolkit). The message
set to reuse verbatim:

| Situation | Message (EN) |
|---|---|
| Tool accepts only PDFs, images loaded | "This tool works on PDFs, but you've loaded images." |
| Tool accepts only images, PDFs loaded | "This tool works on images, but you've loaded PDF files." |
| Convert: mode "PDF → images" with images loaded | "This mode converts PDFs to images, but you've loaded images." |
| Convert: an images mode with PDFs loaded | "This mode converts images to PDF, but you've loaded PDF files." |
| Split/Extract: Extract mode, no pages resolved | "No pages selected — click pages in the grid, or type a page list like 1-5, 8." |
| Mixed selection, some files will be skipped | partial-skip notice: only the matching files will be processed. |
| Tool ran but wrote nothing | Shown in the Processing ("Traitement") popup: "The tool ran but produced no files…" |

The first four are driven by `ACCEPTS` + the tool `warning()` hook; the rest are
host-computed. When porting, keep the priority order described in §2.

---

## 4. Intentional system-specific host divergences (do NOT converge)

These exist **only** on the Windows host and are correct as-is. They have no
Linux equivalent by design; do not try to unify them.

| Area | Windows behaviour | Why it must diverge |
|---|---|---|
| Config path | `%APPDATA%\PageForge\config.json` | Linux uses `GLib.get_user_config_dir()`. Each platform's native location. |
| Packaging | Embedded relocatable standalone CPython 3.12 (`python\` + `app\`), installed via Inno Setup | Gives Windows a real Python so on-demand `pip install` of tool deps works like Linux-from-source. Linux ships from source / distro packaging. |
| Dependency install | `_pip_cmds` installs into the bundled env (drops `--user` when `sys.prefix` is writable); swaps `pythonw.exe`→`python.exe` for the pip call | Matches the embedded-Python layout; meaningless on Linux. |
| Subprocess windows | `CREATE_NO_WINDOW` on all spawned processes | Stops a stray console flashing over the in-app install log. No analogue on Linux. |
| std streams | `_ensure_std_streams()` redirects `None` stdout/stderr to `%APPDATA%\PageForge\pageforge.log` | Under `pythonw.exe`, `sys.stdout`/`stderr` are `None`; libraries that write to them crash (this caused the Remove-background freeze). Linux always has real streams. |
| Tesseract | `_wire_bundled_tesseract()` hook adds `app\tesseract\` to PATH if present | OCR uses EasyOCR; tesseract isn't bundled, but the hook is harmless. Linux relies on the system tesseract package. |
| Thread→GUI marshalling | `_Bridge` QSignal / `QTimer.singleShot(0, …)` | Qt equivalent of Linux `GLib.idle_add`. Same intent, different toolkit. |
| UI shell | Themed cogwheel top-right (`_make_gear_icon`); native title bar; no header strip | Native Win11 look; replaces Linux `Adw.HeaderBar` / `Adw.WindowTitle`. |
| Section cards | `_apply_section_style` theme-aware rounded frames; taller headers | Qt reimplementation of the Adwaita `.card` sidebar. |
| Left panel chrome | Sidebar `QScrollArea` has **no frame**; a 2px `QSplitter` handle is the only divider between panel and preview. Section cards are an inner styled `QFrame` with the inter-card gap OUTSIDE it, so a collapsed card is exactly its header (title vertically centred) | Native-Qt styling choice; Linux draws its own panel separator / accordion. |
| Zone hint | Translated **plain-text** row shown **above** the preview (its own row between page-nav and the preview widget) — no background, not overlaid, not between the nav bars | Windows UX request; place it wherever reads best per toolkit. |
| Output folder name | Auto-created folder localizes: `output` (EN) / `Résultat` (FR) via `default_output_name()` | Same idea is portable to Linux; the FR string is shared. |
| Installer wizard | `[Code] CurPageChanged` nudges the "Additional tasks" checklist a few px inward so the checkbox glyph isn't clipped on its left edge at some DPI scales (not a width issue) | Inno Setup (Windows installer) only; no Linux analogue. |
| Link toggle | Tall vertical rectangle with painted chain glyph (`_link_pixmap`) | Qt-painted replacement for the Adwaita linked-spin control. |
| Warning bubble widget | `WarningBubble(QFrame)` floating panel + amber `QToolButton` | Qt widget; the *messages/logic* (§3) are shared, the widget is not. |
| Debug logging | `PAGEFORGE_DEBUG`-gated `_dbg` logger | Parity helper; fine to have on both but not required. |

---

## 5. Shared host change that originated on Linux (already in both)

- **`_grid_fire_split` → `_refresh_preview()`** after applying a tool's
  `on_split` writes: an even-split re-derives and re-spaces every bar when one is
  dragged (explicit cuts just repaint; grid signature unchanged, so no thumbnail
  rebuild). This came from the user's Linux `split_extract.py` work and is
  present on both hosts. Keep them in step if either changes.

---

## 6. Quick sync procedure

**Linux → Windows (a tool changed on Linux):**
copy the `tools/*.py` file into this repo's `tools/`. Done — no host change
unless the tool starts using a brand-new contract surface (then add it as an
optional hook on the Windows host and record it in §2).

**Windows → Linux (mirror what's here):**
1. Copy the three edited tool files (`upscale.py`, `convert.py`,
   `split_extract.py`) into the Linux `tools/`.
2. Implement the two optional host hooks (§2) on the Linux host.
3. Port the warning system *logic + messages* (§3) to the Linux UI.
4. Leave everything in §4 alone.

Whenever either side gains a feature, add a row to §1/§2/§3 (portable) or §4
(intentionally platform-specific) so this log stays the single source of truth.
