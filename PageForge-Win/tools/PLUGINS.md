# Writing a PageForge tool

Every tool in PageForge — including the ones that ship with it — is a single Python
file in your **scripts folder** (Settings → Scripts folder → Open). To add a tool,
drop a `.py` file in there and hit **Reload** in Settings. To remove one, delete it
(from Settings or the folder). There is no difference between "built-in" and "your"
tools; the starting tools can be edited, replaced, or deleted like any other.

A file becomes a tool if it defines at least `NAME` and `process()`. Everything else
is optional.

The tool contract is **identical on Windows and Linux** — the same `.py` runs on both
hosts unchanged (tools import no UI toolkit). You can move scripts between a Linux and
a Windows PageForge scripts folder freely.

---

## Module attributes

```python
NAME = "My Tool"                 # required — shown in the Tools list
ACCEPTS = ("image", "pdf")       # which file kinds this handles (default: both)
BATCH = False                    # True → process() receives the whole file list
OPTIONS = [ ... ]                # parameters, auto-rendered in the Options panel
REQUIRES = ["somepkg"]           # pip packages, installed on demand from Settings
SYSTEM_REQUIRES = ["somelib"]    # system components (see the note below on Windows)
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
site (or the active venv).

**`SYSTEM_REQUIRES` on Windows:** the Linux build installs these with `pkexec dnf`.
Windows has no such mechanism, so PageForge for Windows does **not** auto-install system
components — it lists them and asks you to install them yourself (or use a build that
bundles them, e.g. tesseract for OCR). Pip (`REQUIRES`) still installs automatically.

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

A choice option's *displayed* strings are translated, but its **logical value stays the
English choice string** — your `process()` / rules always see the English value.

Rules: `"enabled_when": {"key": other, "in": [...]}` (or `eq` / `not_in`) greys a row
out; `"visible_when": {...}` shows/hides it; `"hidden": True` keeps a value with no row.

At run time you receive an `options` dict keyed by each `key`.

A `regions` option turns the preview into a zone-drawing surface: the user drags boxes
on the page and you receive them as `(x0, y0, x1, y1)` tuples in 0–1 page fractions.

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

**Optional progress:** add a `progress=None` parameter and call `progress(done, total)`.

---

## Previews (optional)

### 1. Cheap overlay — `preview(item, page_index, options, context)`

Return one of: `{"grid": (cols, rows)}`, `{"rect": (x0,y0,x1,y1), "space": "points"}`,
`{"text": (old_name, new_name)}`, `{"boxes": [...], "space": "fraction"}`, or `None`.
`space` is `"points"` (PDF), `"pixels"` (image), or `"fraction"` (0–1).

`context` gives `{"files": [...], "index": i, "page_index": p, "page_w": W, "page_h": H}`.

Draggable **handles**: add `"handles": [{"id","kind":"vline|hline|point","x","y"}]` to the
dict, and implement `on_handle_drag(handle_id, fx, fy, options, context)` returning option
writes. The host does all drawing and hit-testing.

### 1b. Heavy detected overlay — `preview_regions(item, page_index, options)`

Return a list of `(x0,y0,x1,y1)` fractions (or `{"boxes": [...], "color", "dash"}`);
shown behind a **Generate preview** button.

### 2. Heavy visual — `preview_image(item, page_index, options)`

Return a PIL image (RGBA shown on a checkerboard). Runs in a background thread. Add
`COMPARE_PREVIEW = True` to get a draggable before/after slider.

### 3. Thumbnail grid — `preview_grid(context, options)`

Set `PREVIEW_KIND = "grid"` (or `preview_kind(item, options)`), and return
`{"source": "files"|"pages", "selectable": bool, "reorderable": bool, "split_bars": bool,
"splits": [...]}`. Selection/reorder/splits come back via `on_select(indices, ...)`,
`on_reorder(order, ...)`, `on_split(points, ...)`. `process()` receives the resolved
order as `options["_grid_order"]`, selection as `options["_grid_selected"]`, and a flat
`options["_grid_sequence"]` of ordered `(file_index, pageno|None)` tuples.

---

## Notes

- Broken tools are skipped (with a message in the terminal) without taking down the rest.
- Keep tools self-contained; import what you need lazily inside functions.
- Read the `context` keys you need and ignore the rest — new keys may be added over time.
- Available in the app already: `fitz` (PyMuPDF), `PIL`, `numpy`.
