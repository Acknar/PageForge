# Writing a PageForge tool

Every tool in PageForge — including the six that ship with it — is a single Python
file in your **scripts folder** (Settings → Scripts folder → Open). To add a tool,
drop a `.py` file in there and hit **Reload** in Settings. To remove one, delete it
(from Settings or the folder). There is no difference between "built-in" and "your"
tools; the starting six can be edited, replaced, or deleted like any other.

A file becomes a tool if it defines at least `NAME` and `process()`. Everything else
is optional.

**The app is only a host.** It never hard-codes anything about a specific tool. A tool
declares its own options, its own dependencies, its own preview and the exact
colour/dash/fill that preview is drawn in, and even whether the "Generate preview"
button appears — all through the attributes and hooks below. That means a tool can
restyle its preview, add options, or change behaviour with **no change to the app**, and
a brand-new tool with a completely different feature set drops in beside the built-ins
and is treated identically. If you find yourself wanting to edit `pageforge.py` to change
how one tool looks or behaves, there's almost certainly a hook here that does it from the
script instead.

---

## Module attributes

```python
NAME = "My Tool"                 # required — shown in the Tools list
ACCEPTS = ("image", "pdf")       # which file kinds this handles (default: both)
BATCH = False                    # True → process() receives the whole file list
OPTIONS = [ ... ]                # parameters, auto-rendered in the Options panel
REQUIRES = ["somepkg"]           # pip packages, installed on demand from Settings
SYSTEM_REQUIRES = ["somelib"]    # dnf packages (installed via pkexec, needs password)
MODULES = ["somepkg"]            # import names used to check whether deps are present
PREVIEW_ON_DEMAND = False        # True → heavy preview runs only when the user clicks
```

### Dependencies

If your tool needs libraries that may not be installed, declare them:

```python
REQUIRES = ["rembg", "onnxruntime"]   # what pip should install
MODULES  = ["rembg", "onnxruntime"]   # what to import-test to know if it's there
```

**Import heavy libraries lazily, inside your functions — not at the top of the file.**
The app imports your module to read its metadata; if a top-level `import rembg` fails,
the whole tool fails to load and can't offer to install anything. Do this instead:

```python
def process(item, output_dir, options, overwrite):
    from rembg import remove          # imported only when actually run
    ...
```

When `MODULES` aren't importable, the tool shows up in Settings with an **Install deps**
button but stays inactive until the install succeeds. Pip packages install to your user
site (or the active venv); `SYSTEM_REQUIRES` go through `pkexec dnf`, which pops the
system password dialog.

---

## OPTIONS

Each entry is a dict. Supported `type` values:

| type     | widget        | extra keys                     | value passed to you |
|----------|---------------|--------------------------------|---------------------|
| `int`    | spin button   | `min`, `max`, `default`        | `int`               |
| `float`  | spin button   | `min`, `max`, `default`        | `float`             |
| `bool`   | switch        | `default`                      | `bool`              |
| `choice` | dropdown      | `choices` (list), `default`    | the chosen string   |
| `text`   | entry         | `default`                      | `str`               |
| `file`   | file picker   | `default`                      | path `str` (or "")  |
| `color`  | colour picker | `default` (e.g. `"#000000"`)   | hex `str`           |
| `regions`| drag-on-preview | —                            | `list` of `(x0,y0,x1,y1)` fractions |

Two int/float options can be **linked** with `"link_with": "other_key"` — this adds a
chain toggle so editing one mirrors the other (e.g. Top↔Bottom).

```python
OPTIONS = [
    {"key": "factor", "label": "Scale factor", "type": "int",
     "default": 2, "min": 1, "max": 8},
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": ["fast", "best"], "default": "best"},
    {"key": "list", "label": "Name list", "type": "file", "default": ""},
]
```

At run time you receive an `options` dict keyed by each `key`.

A `regions` option turns the preview into a zone-drawing surface: the user drags
boxes on the page and you receive them as a list of `(x0, y0, x1, y1)` tuples in 0–1
page fractions (empty list if none drawn). Multiply by the page/image width and height
to get pixels. The OCR tool uses this to bound where text is read.

A `regions` option may also set:

- `"color"` — the hex colour the zones (and any column guides you draw) are painted in,
  e.g. `"#1c70d1"`. Defaults to red.
- `"hint"` — the caption shown under the preview while drawing. Defaults to the generic
  per-page zone hint.
- `"order_key"` — a choice option key the app reads to decide whether zone badges are
  numbered in draw order or sorted top→bottom, left→right.

---

## process()

```python
def process(item, output_dir, options, overwrite):
    # item        : path str of ONE file  (or list[str] when BATCH = True)
    # output_dir  : folder to write into (str)
    # options     : dict of your OPTIONS values
    # overwrite   : bool — user asked to overwrite originals
    # return      : list of output paths you wrote (str or Path)
    ...
    return [out_path]
```

- **Per-file tools** (`BATCH = False`): called once per input file; return the file(s)
  you produced. Progress is tracked per file.
- **Batch tools** (`BATCH = True`): called once with the full list — use this when the
  output depends on all inputs together (combining, renaming with sequence numbers, etc.).

Write outputs into `output_dir`. Respect `overwrite` if it makes sense for your tool.

**Optional progress:** add a `progress=None` parameter to `process` and call
`progress(done, total)` as you go (e.g. per page). The progress bar then shows
page-level progress — especially useful on a single large PDF. It's fully optional and
backward compatible: tools without the parameter still work.

```python
def process(item, output_dir, options, overwrite, progress=None):
    doc = fitz.open(item)
    for page in doc:
        ...
        if progress:
            progress(page.number + 1, doc.page_count)
```

---

## Previews (optional)

Two hooks. Use whichever fits; skip both for a plain "show the source file" preview.

### 1. Cheap overlay — `preview()`

Runs synchronously on every navigation, so keep it fast. Return one of:

```python
def preview(item, page_index, options, context):
    # A grid of red lines:
    return {"grid": (cols, rows)}

    # A red rectangle. space = "points" (PDF), "pixels" (image), or "fraction" (0–1):
    return {"rect": (x0, y0, x1, y1), "space": "points"}

    # A red→green name change (old on top, new below):
    return {"text": (old_name, new_name)}

    # Several rectangles at once (e.g. columns/margins):
    return {"boxes": [(x0, y0, x1, y1), ...], "space": "fraction"}

    # Nothing special — just show the source:
    return None
```

**Styling is yours to set.** The app only supplies default colours; every overlay dict
above accepts optional style keys so a tool controls its own appearance without any app
change:

- `"color"` — hex stroke colour, e.g. `"#1c70d1"`. Defaults to red.
- `"fill"` — hex colour for a translucent fill (currently honoured for `boxes`).
- `"dash"` — `True` for a dashed outline, `False` for solid, or an explicit dash list
  like `[6, 4]`. `boxes` default to dashed; `grid`/`rect` default to solid.

```python
return {"boxes": [...], "space": "fraction", "color": "#1c70d1", "dash": True}
```

`context` gives you `{"files": [all loaded paths], "index": current_index}` — handy when
a per-file preview depends on the whole set (e.g. computing a sequence number). `rect`
coordinates are measured from the top-left; for a PDF page use points, for an image use
pixels, and the app maps them to the display for you.

### 1b. Heavy detected overlay — `preview_regions()`

For layout analysis that's too slow to run live (e.g. an OCR engine finding text blocks).
Return a list of `(x0, y0, x1, y1)` fractions and the app draws them in blue. To style
them yourself, return a dict instead — `{"boxes": [...], "color": "#..", "dash": bool}`.
Combine with `PREVIEW_ON_DEMAND = True` (or `needs_preview_button`, below) to gate it
behind a **Generate preview** button.

```python
def preview_regions(item, page_index, options):
    return [(0.1, 0.1, 0.5, 0.4), ...]           # detected blocks, in page fractions
    # or, styled:
    return {"boxes": [(0.1, 0.1, 0.5, 0.4), ...], "color": "#1c70d1"}
```

### 1c. Deciding when the button appears — `needs_preview_button()`

`PREVIEW_ON_DEMAND` is a fixed choice. If whether the heavy preview is cheap depends on
the actual file (e.g. reading a PDF's text layer is instant but running OCR is slow),
define `needs_preview_button(item, page_index, options)` instead. Return `True` to show
the **Generate preview** button (manual), or `False` to generate the preview
automatically. It's checked on every navigation, so keep it fast. When absent, the app
falls back to the `PREVIEW_ON_DEMAND` constant.

```python
def needs_preview_button(item, page_index, options):
    # instant when a PDF text layer exists → auto; otherwise OCR must run → button
    import fitz
    if str(item).lower().endswith(".pdf") and options.get("use_text_layer", True):
        return not fitz.open(item)[page_index].get_text("text").strip()
    return True
```

### 2. Heavy visual — `preview_image()`

For tools that transform the pixels (filters, background removal). Return a PIL image;
if it's `RGBA`, the app shows it on a checkerboard so transparency is visible. This runs
in a background thread. Set `PREVIEW_ON_DEMAND = True` so it only runs when the user
clicks **Generate preview** (recommended for anything slow).

```python
PREVIEW_ON_DEMAND = True

def preview_image(item, page_index, options):
    from PIL import Image
    img = Image.open(item)          # or render a PDF page with fitz
    return my_transform(img)        # a PIL image; RGBA for transparency
```

---

## Minimal complete example

```python
"""Rotate images by a fixed angle."""
from pathlib import Path

NAME = "Rotate"
ACCEPTS = ("image",)
OPTIONS = [
    {"key": "angle", "label": "Angle", "type": "choice",
     "choices": ["90", "180", "270"], "default": "90"},
]

def preview(item, page_index, options, context):
    return None   # plain source preview is fine

def process(item, output_dir, options, overwrite):
    from PIL import Image
    item = Path(item)
    img = Image.open(item).rotate(-int(options["angle"]), expand=True)
    out = item if overwrite else Path(output_dir) / f"{item.stem}-rot{item.suffix}"
    img.save(out)
    return [str(out)]
```

Drop it in the scripts folder, hit **Reload**, and "Rotate" appears in the Tools list.

---

## Notes on robustness / future-proofing

- Tools are isolated files, so a broken one is skipped (with a message in the terminal)
  without taking down the rest.
- Keep tools self-contained: import what you need lazily inside functions.
- The `context` argument to `preview()` is a dict — new keys may be added over time, so
  read the ones you need and ignore the rest rather than unpacking positionally.
- Available in the app's own environment already: `fitz` (PyMuPDF), `PIL`, `numpy`. You
  can rely on those without declaring them.
