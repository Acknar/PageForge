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
OPTIONAL_REQUIRES = ["extrapkg"] # extras for one feature; never gate the tool
OPTIONAL_MODULES = ["extrapkg"]  # import names for those extras
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

`OPTIONAL_REQUIRES` / `OPTIONAL_MODULES` are for a package only one feature needs. They
show in Settings and install through the same button, but they never make the tool
inactive — so a missing extra costs you that one feature and nothing else. Import them
inside the feature and tell the user what to do when the import fails.

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
| `shapes` | draw-on-preview | `kinds` (list), `hint`       | `dict` of shapes, see below |
| `button` | push button     | `text`, `suggested`          | nothing — calls `on_action()` |

Number entries accept a full stop as the decimal separator whatever the system
locale, so `1.5` and `1,5` both work.

Any option may set `"advanced": True`. Those rows go into one collapsed "Advanced"
group, placed where the first of them is declared, for settings most people never
touch. The group hides itself when none of its rows are visible.

A `button` holds no value and never appears in your `options` dict. Clicking it calls
`on_action(key, options, context)`; return the option writes you want applied, and
optionally a message:

```python
def on_action(key, options, context):
    if key != "detect":
        return None
    return {"options": {"shapes": new_shapes}, "message": "Done."}   # or just a dict of writes
```

The message is translated like any other string, so keep it fixed — no interpolated
numbers.

### Reacting to a changed option — `on_option_changed()`

```python
def on_option_changed(key, options, context):
    # return a dict of option writes, or None
    ...
```

Called with the key of whatever the user just changed. Most options only need reading at
render time; use this for the ones that have to *do* something — a setting that has to
move geometry the tool owns, say. The `shapes` option is excluded from the comparison,
since it changes on every pointer move.

### Stopping a run before it starts — `confirm_process()`

```python
def confirm_process(context, options):
    # return a sentence to make the app ask before running, or None to go ahead
    return "No scale has been set, so values will be in pixels. Continue?"
```

The app shows it with Cancel / Process files and only runs if the user agrees. This is
the one thing a tool can't arrange itself: `process()` runs on a worker thread and can
only return paths or raise, so without this the best it could do is fail after the fact.

### Showing an option only where it applies

`enabled_when` greys a row out; `visible_when` hides it. Both take
`{"key": <other option>, "in"/"eq"/"not_in": …}`, **or a list of those, meaning all of
them**. The reserved key `"@shape_kind"` matches the drawing mode selected in a `shapes`
toolbar rather than an option value, which is how an option can belong to one drawing
mode:

```python
{"key": "area_unit", "label": "Area in", "type": "choice", "choices": [...],
 "visible_when": {"key": "@shape_kind", "eq": "area"}},

{"key": "rect_w", "label": "Rectangle width", "type": "float",
 "visible_when": [{"key": "mode", "eq": "3D"}, {"key": "@shape_kind", "eq": "rig"}]},
```

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
- `"hint"` — the caption shown above the preview while drawing. Defaults to the generic
  per-page zone hint.
- `"order_key"` — a choice option key the app reads to decide whether zone badges are
  numbered in draw order or sorted top→bottom, left→right.

### A `shapes` option — vector drawing on the preview

Where `regions` gives you rectangles, `shapes` gives you points, lines, polylines,
polygons and angles. The app owns the whole interaction — placing, moving vertices,
dragging a shape, deleting, snapping — and hands you the finished geometry. It knows
nothing about what the shapes mean; that's yours.

Declare the kinds you want as buttons:

```python
{"key": "shapes", "label": "Draw", "type": "shapes",
 "kinds": [
     {"id": "scale", "label": "Set scale", "shape": "line", "max": 1, "color": "#e5a50a"},
     {"id": "area",  "label": "Area",      "shape": "polygon", "color": "#9141ac"},
 ],
 "hint": "Shown above the preview while drawing"}
```

Each kind takes:

- `"id"` — comes back on every shape as its `role`, so you can tell a scale line from
  a measurement line.
- `"shape"` — one of `point` (1 vertex), `line` (2), `angle` (3), `polyline` (2+, open),
  `polygon` (3+, closed).
- `"max"` — optional cap on how many of this kind may exist on a page.
- `"color"` — hex stroke colour for shapes of this kind.
- `"setup"` — `True` marks the kind as scaffolding: its shapes are drawn, hit-tested and
  draggable **only while that kind is the selected mode**, and vanish the moment the user
  picks another one. Use it for a rig the tool needs but the drawing shouldn't show.
- `"group"` — setup kinds sharing a group string are live together, so one mode can own
  several separate pieces of geometry (a perspective gizmo and the rectangle that scales
  it) without the user having to switch between them.
- `"button"` — `False` gives the kind no toolbar button. Its geometry still exists and is
  still editable, but only through the mode it is grouped with.
- `"seed"` — a list of `(x, y)` fractions placed automatically the first time the mode is
  used on a page, so a rig arrives ready to drag instead of needing to be drawn.
- `"visible_when"` — same schema as an option row (below), evaluated against the current
  options. A hidden kind's button disappears, and if it was the active one the app moves
  to the first visible kind.

`context["shape_kind"]` tells `preview()` which kind is selected, which is how a tool
draws setup guides in one mode and not the others.

**Computed seeds — `seed_shape()`.** A seed written in screen fractions is only right for
one page aspect ratio. Add

```python
def seed_shape(kind_id, options, context):
    # return [(fx, fy), …] or None to fall back to the static "seed"
```

and the app asks the tool where to put the geometry instead, with the page size in
`context["page_w"]` / `context["page_h"]`.

**Untouched seeds.** A seeded shape carries `"seeded": True` in the value you receive
until the user drags it, so a tool can tell geometry the user placed from geometry that
is only sitting where the seed put it (a scale rectangle that hasn't been put on
anything yet is not a scale). While a seeded shape is untouched, dragging another shape
of the same `group` places it again from `seed_shape()`, so it follows the geometry its
seed depends on. A tool that wants it placed again after an option change can rewrite
its points from `on_option_changed()` and return the shapes; the mark is kept.

How the user draws: click to place each vertex; for a 2-point kind, dragging draws it in
one go. A polyline or polygon is finished by double-clicking, right-clicking, or (for a
polygon) clicking its first vertex again. Right-clicking an existing shape deletes it,
dragging a vertex moves it, dragging the body moves the whole shape, and holding Shift
snaps the new point to 0/45/90° from the previous one.

The value you receive is keyed by file and then by page, because a drawing belongs to
the page it was drawn on:

```python
{"/path/to/file.pdf": {0: [{"id": "s1", "role": "scale", "shape": "line",
                            "points": [(0.12, 0.44), (0.51, 0.44)]}]}}
```

Points are `(x, y)` fractions of the page/image, from the top-left. Multiply by
`context["page_w"]` / `context["page_h"]` (points for a PDF, pixels for an image) to work
in the page's own units.

One option per tool, and don't combine it with a `regions` option: while shapes are
active the canvas routes every click to them, so zones would show but not respond.

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

    # Text read-outs placed on the preview (0–1 fractions):
    return {"labels": [{"x": 0.5, "y": 0.4, "text": "12.4 cm",
                        "color": "#1c70d1", "size": 14}]}

    # Vector guides you draw yourself (0–1 fractions), e.g. a perspective grid:
    return {"paths": [{"points": [(0.1, 0.2), (0.9, 0.3)], "color": "#3584e4",
                       "width": 1.0, "alpha": 0.45, "dash": True}]}

    # A drawing hint that follows what the user is doing (with a shapes option):
    return {"hint": {"steps": ["1. Draw a line", "2. Type its length", "3. Measure"],
                     "current": 1, "note": "Drag, or click both ends."}}

    # Nothing special — just show the source:
    return None
```

`labels` and `paths` can be returned on their own or alongside any of the overlays above.

**The drawing hint — `"hint"`.** With a `shapes` option, `preview()` may replace the
static `hint` above the preview. A string is shown as it is. A dict shows `steps` on one
line, wrapping only between steps, with the step at index `current` in bold, and `note`
on a line of its own: what to do next, and how the active drawing mode is used. Each
step and the note are translated. Next steps belong here rather than in `warning()`,
which is for things that are actually wrong. Without a `hint` the static one stays.

Each label is drawn as a rounded pill at a fixed on-screen size, so it stays readable at
any zoom; `size` is in screen pixels and `anchor` may be `"center"` (default), `"left"` or
`"right"`. This is how a drawing tool reports values back to the user: the app draws the
geometry, the script decides what the numbers say.

**Room around the page — `"outset"`.** By default the page fills the preview and paths
are clipped to it, so anything you draw outside is invisible. Return
`"outset": 0.5` (all four sides) or `"outset": (left, top, right, bottom)` in page
fractions and the page shrinks to make space, with the clip widened to match. 0–1
fractions still mean the page, so drawing, pointer mapping and hit-testing are unchanged.
Use it when what you need to show is bigger than the page — a sheet of paper the drawing
is being laid out on, for instance.

Label text is translated before it is drawn, so declare it in English like every other
string. A number inside a sentence would never match a catalog entry, so `"text"` may
also be a **list of segments**: each is translated on its own and the results joined.
`["field of view ", "52°"]` translates the words and leaves the value alone.

Each path is a polyline in page fractions, with optional `color`, `width` (screen pixels),
`alpha`, `dash`, `closed`, `fill` and `fill_alpha`. Paths are clipped to the page, so a
construction line heading for a vanishing point can be sent well off the edge without
spilling over the rest of the window. Unlike shapes, they aren't interactive — they're the
tool drawing on the page, not the user.

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

`context` gives you `{"files": [all loaded paths], "index": current_index, "page_index":
current_page, "page_w": …, "page_h": …}` — handy when a per-file preview depends on the
whole set (e.g. computing a sequence number), and `page_w`/`page_h` are the page size in
its own units (points for a PDF, pixels for an image), which is what turns a 0–1 fraction
back into something measurable. `rect`
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
