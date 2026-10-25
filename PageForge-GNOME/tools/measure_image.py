"""Measure distances, areas, perimeters and angles on an image or PDF page.

Two ways to set the scale.

**2D scale** — for flat, straight-on subjects (scans, plans, elevations). Draw
one "Set scale" line over something whose real length you know and type that
length. Everything else then reads out in real units.

**3D perspective** — for photographs and drawings seen at an angle. The scale
is set the same way, with a "Set scale" line, but first the perspective has to
be known, and that is what the "3D setup" mode is for.

*The box.* Six yellow corners: the near corner of a building (or of a room) at
ground level, the far end of each wall at ground level, and the same three at
the top. Drag each onto the matching corner in the picture. Each wall's top and
bottom edges are parallel in the world, so where they meet on the page is that
wall's vanishing point, and two of those are all a camera needs: the focal
length falls out of them and the vertical is their cross product. The edges are
carried on towards their vanishing point while the mode is open, and the grids
on the three planes show whether the solve fits the rest of the picture.

Nothing here is assumed about the building's proportions. When a wall's edges
are parallel on the page its vanishing point is at infinity; when both are, the
picture is a parallel projection (an axonometric drawing) and is solved from the
directions of the three edges alone. One wall converging and the other not is
bridged so that the solve never jumps between the two (see _implied).

The vertical edges are not used for the solve; they check it. With the
perspective taken from the walls each vertical has one right direction, and the
setup view says when the ones drawn are off.

*The scale.* A "Set scale" line over something whose length you know, a door's
width or a wall's height, measured on the plane it is drawn on.

The hint above the preview lists the steps for the current mode with the one to
do now in bold; the amber warning is kept for things that are actually wrong.

A wide-angle or fisheye lens bends straight lines and will throw the solve off
however carefully the corners are placed, and so does a photo cropped off
centre, because the solve assumes the lens's centre is the page's.

The box, its edges and the grid are only drawn while the "3D setup" mode is
selected. They never appear in the other modes and are never exported.

**Laying out on paper.** With a 2D scale set, "Lay out on paper" writes a PDF
with the drawing placed at a true scale — 1:100, 1:200, anything you type — on
the sheet size you pick, inside a printing margin, and converts an image to PDF
on the way. It is 2D only on purpose: a drawing scale is one
millimetres-per-pixel figure for the whole sheet, and a photograph taken at an
angle doesn't have one. One sheet per source page that carries measurements, and
always a new file.

The drawing belongs to the page it was drawn on, so a multi-page PDF needs its
own scale or box on each page you measure.

Line width and label size are given at a 1000-px reference and scale with the
image, so a drawing looks the same on a 900-px scan and a 6000-px photo. On a
PDF they are plain points.
"""
from pathlib import Path
import math

NAME = "Measure image"
ACCEPTS = ("image", "pdf")
ORDER = 95

SCALE_COLOR = "#e5a50a"
DIST_COLOR = "#1c70d1"
AREA_COLOR = "#9141ac"
ANGLE_COLOR = "#c01c28"
RIG_COLOR = "#f6d32d"

AXIS_X = "#e01b24"
AXIS_Y = "#33d17a"
AXIS_Z = "#3584e4"
HORIZON = "#c0bfbc"

ROLE_COLOR = {"scale": SCALE_COLOR, "distance": DIST_COLOR, "area": AREA_COLOR,
              "angle": ANGLE_COLOR, "rig": RIG_COLOR}

SETUP_ROLES = ("rig",)             # never measured, never exported

# metres per unit — the whole unit system is this table
UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
         "in": 0.0254, "ft": 0.3048, "yd": 0.9144}
AREA_UNITS = ("auto", "mm²", "cm²", "m²", "in²", "ft²")

MODE_2D = "2D scale"
MODE_3D = "3D perspective"
_IF_2D = {"key": "mode", "eq": MODE_2D}
_IF_3D = {"key": "mode", "eq": MODE_3D}

# conditions on the drawing mode, so an option only appears where it does
# something. "@shape_kind" is the host's reserved key for the active kind.
_KIND = "@shape_kind"
_ON_SCALE = {"key": _KIND, "eq": "scale"}
_ON_SETUP = [_IF_3D, {"key": _KIND, "eq": "rig"}]
_ON_MEASURE_3D = [_IF_3D, {"key": _KIND, "in": ["distance", "area", "angle"]}]
_ON_AREA = {"key": _KIND, "eq": "area"}

PLANES = ("Ground", "Face along X", "Face along Y")
PLANE_AUTO = "Auto (from where it is drawn)"

# Paper output. Sizes in millimetres, portrait.
SHEET_OFF = "Off (keep the original size)"
SHEETS = {
    SHEET_OFF: None,
    "A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0),
    "A1": (594.0, 841.0), "A0": (841.0, 1189.0),
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8),
}
ORIENT_AUTO = "Auto (whichever fits)"
ORIENT_PORTRAIT = "Portrait"
ORIENT_LANDSCAPE = "Landscape"
MM_PT = 72.0 / 25.4
_IF_SHEET = {"key": "sheet", "not_in": [SHEET_OFF]}

OPTIONS = [
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": [MODE_2D, MODE_3D], "default": MODE_2D},
    {"key": "shapes", "label": "Draw", "type": "shapes",
     "kinds": [
         {"id": "rig", "label": "3D setup", "shape": "handles", "max": 1,
          "color": RIG_COLOR, "setup": True, "visible_when": _IF_3D,
          # the box's corners: near corner, far end of each wall, then the same
          # three at the top. seed_shape() fits it to the real page; the literal
          # is only the last-resort fallback.
          "seed": [(0.50, 0.74), (0.80, 0.64), (0.25, 0.62),
                   (0.50, 0.40), (0.80, 0.30), (0.25, 0.28)]},
         # the same scale line in both modes: in 3D it is measured on the plane
         # it is drawn on
         {"id": "scale", "label": "Set scale", "shape": "line", "max": 1,
          "color": SCALE_COLOR},
         {"id": "distance", "label": "Distance", "shape": "line", "color": DIST_COLOR},
         {"id": "area", "label": "Area", "shape": "polygon", "color": AREA_COLOR},
         {"id": "angle", "label": "Angle", "shape": "angle", "color": ANGLE_COLOR},
     ],
     # only a fallback: preview() replaces it with steps for the active mode
     "hint": "Click to place points · right-click a shape to delete it · "
             "hold Shift to snap to 0/45/90°"},

    # first, so the unit is already chosen when a length is typed under it
    {"key": "unit", "label": "Unit", "type": "choice",
     "choices": list(UNITS.keys()), "default": "m"},
    {"key": "ref_length", "label": "Reference length", "type": "float",
     "default": 1.0, "min": 0.001, "max": 1000000.0, "visible_when": _ON_SCALE},

    {"key": "grid_step", "label": "Grid step (0 = auto)", "type": "float",
     "default": 0.0, "min": 0.0, "max": 1000000.0, "visible_when": _ON_SETUP,
     "advanced": True},
    {"key": "grid_extent", "label": "Grid reach (× box)", "type": "float",
     "default": 1.25, "min": 0.5, "max": 10.0, "visible_when": _ON_SETUP,
     "advanced": True},
    {"key": "fov", "label": "Field of view ° (0 = solve)", "type": "float",
     "default": 0.0, "min": 0.0, "max": 179.0, "visible_when": _ON_SETUP,
     "advanced": True},

    {"key": "plane", "label": "Measure on", "type": "choice",
     "choices": [PLANE_AUTO] + list(PLANES), "default": PLANE_AUTO,
     "visible_when": _ON_MEASURE_3D},

    {"key": "area_unit", "label": "Area in", "type": "choice",
     "choices": list(AREA_UNITS), "default": "m²", "visible_when": _ON_AREA},
    {"key": "decimals", "label": "Decimals", "type": "int",
     "default": 2, "min": 0, "max": 6},
    {"key": "segment_labels", "label": "Label each side", "type": "bool",
     "default": True, "visible_when": _ON_AREA},
    {"key": "show_perimeter", "label": "Show polygon perimeter", "type": "bool",
     "default": False, "visible_when": _ON_AREA},
    # Paper output. 2D only, and deliberately so: a drawing scale is one
    # millimetres-per-pixel number for the whole sheet, and a perspective photo
    # doesn't have one — things further away are smaller. See _sheet_geometry.
    {"key": "sheet", "label": "Lay out on paper", "type": "choice",
     "choices": list(SHEETS.keys()), "default": SHEET_OFF, "visible_when": _IF_2D},
    {"key": "sheet_scale", "label": "Drawing scale", "type": "text",
     "default": "1:100", "visible_when": [_IF_2D, _IF_SHEET]},
    {"key": "sheet_orient", "label": "Orientation", "type": "choice",
     "choices": [ORIENT_AUTO, ORIENT_PORTRAIT, ORIENT_LANDSCAPE],
     "default": ORIENT_AUTO, "visible_when": [_IF_2D, _IF_SHEET]},
    {"key": "sheet_margin", "label": "Printing margin (mm)", "type": "float",
     "default": 10.0, "min": 0.0, "max": 100.0,
     "visible_when": [_IF_2D, _IF_SHEET]},
    {"key": "sheet_crop", "label": "Crop to fit the margins", "type": "bool",
     "default": False, "visible_when": [_IF_2D, _IF_SHEET]},
    {"key": "sheet_caption", "label": "Print the scale on the sheet", "type": "bool",
     "default": True, "visible_when": [_IF_2D, _IF_SHEET]},

    {"key": "label_size", "label": "Label size", "type": "int",
     "default": 14, "min": 6, "max": 96},
    {"key": "line_width", "label": "Line width", "type": "float",
     "default": 2.0, "min": 0.5, "max": 20.0},
    {"key": "burn_labels", "label": "Include labels in the export", "type": "bool",
     "default": True},
]


# --------------------------------------------------------------------------- #
# page / shape access
# --------------------------------------------------------------------------- #
_SIZE_CACHE = {}


def _page_size(item, page_index):
    """Page size in native units, and what those units are called. Cached: the
    live preview re-measures on every pointer move during a drag, and reopening
    a big PDF that often would stutter."""
    item = Path(item)
    try:
        stamp = item.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = (str(item), int(page_index), stamp)
    hit = _SIZE_CACHE.get(key)
    if hit is not None:
        return hit
    if item.suffix.lower() == ".pdf":
        import fitz
        doc = fitz.open(item)
        r = doc[max(0, min(page_index, doc.page_count - 1))].rect
        out = (float(r.width), float(r.height), "pt")
        doc.close()
    else:
        from PIL import Image
        with Image.open(item) as im:
            w, h = im.size
        out = (float(w), float(h), "px")
    if len(_SIZE_CACHE) > 64:
        _SIZE_CACHE.clear()
    _SIZE_CACHE[key] = out
    return out


def _shapes_for(item, page_index, options):
    """The shapes the user drew on this file/page, in draw order."""
    data = options.get("shapes") or {}
    per_file = data.get(str(item))
    if per_file is None:
        return []
    return list(per_file.get(page_index) or per_file.get(str(page_index)) or [])


def _native(points, w, h):
    return [(float(x) * w, float(y) * h) for x, y in points]


def _seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _segments(pts, closed=False):
    segs = list(zip(pts, pts[1:]))
    if closed and len(pts) > 2:
        segs.append((pts[-1], pts[0]))
    return segs


def _path_len(pts, closed=False):
    return sum(_seg_len(a, b) for a, b in _segments(pts, closed))


def _polygon_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def _centroid(pts):
    """Area centroid, falling back to the average vertex for degenerate rings."""
    n = len(pts)
    a = cx = cy = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-9:
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    a *= 0.5
    return (cx / (6 * a), cy / (6 * a))


def _angle_between(v1, v2):
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-12 or n2 < 1e-12:
        return 0.0
    c = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(c))


def _label_str(lb):
    """A label's text as one string. preview() may hand the host a list of
    segments so the prose parts can be translated around a number; the export
    has no language to translate into and just joins them."""
    t = lb.get("text", "")
    if isinstance(t, (list, tuple)):
        return "".join(str(p) for p in t)
    return str(t)


# --------------------------------------------------------------------------- #
# projective geometry
# --------------------------------------------------------------------------- #
def _apply(h, p, depth=None):
    """Projective map of a 2D point. None when the point maps to infinity, or —
    when a depth sign is given — when it lies beyond the vanishing line and so
    inverts to a finite but entirely fictitious coordinate behind the camera."""
    x, y = p
    d = h[2, 0] * x + h[2, 1] * y + h[2, 2]
    if abs(d) < 1e-12 or (depth is not None and d * depth <= 0):
        return None
    return ((h[0, 0] * x + h[0, 1] * y + h[0, 2]) / d,
            (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / d)


# A vanishing point further out than this many short sides of the page is taken
# as being at infinity. Edges that close to parallel carry no usable perspective,
# only noise from where exactly the corners were dropped, and solving a lens
# from them amplifies that noise: with corners dropped within 3 px on an
# axonometric drawing, 40 here gave a worst reading 10% off in one try in ten,
# 12 gives 2%. A real photo's vanishing points sit well inside it; only a wall
# seen almost square-on, or a long telephoto, goes past, and for those a
# parallel projection is the right model anyway.
VP_MAX = 12.0

# the box's six corners, in the order they're stored on the shape: the near
# corner at ground level, the far end of each wall at ground level, and the
# same three at the top
BOX_O, BOX_X, BOX_Y, BOX_Z, BOX_XZ, BOX_YZ = range(6)
BOX_EDGES = ((BOX_O, BOX_X, AXIS_X), (BOX_Z, BOX_XZ, AXIS_X),
             (BOX_O, BOX_Y, AXIS_Y), (BOX_Z, BOX_YZ, AXIS_Y),
             (BOX_O, BOX_Z, AXIS_Z), (BOX_X, BOX_XZ, AXIS_Z), (BOX_Y, BOX_YZ, AXIS_Z))


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else None


def _meet(a0, a1, b0, b1, ref):
    """Where two edges that are parallel in the world meet in the picture: their
    vanishing point, or None when they are parallel on the page too (or so
    nearly that the point is past VP_MAX)."""
    l1 = (a0[1] - a1[1], a1[0] - a0[0], a0[0] * a1[1] - a0[1] * a1[0])
    l2 = (b0[1] - b1[1], b1[0] - b0[0], b0[0] * b1[1] - b0[1] * b1[0])
    x = l1[1] * l2[2] - l1[2] * l2[1]
    y = l1[2] * l2[0] - l1[0] * l2[2]
    z = l1[0] * l2[1] - l1[1] * l2[0]
    if abs(z) <= 1e-12 * max(1.0, math.hypot(l1[0], l1[1]) * math.hypot(l2[0], l2[1])):
        return None
    vx, vy = x / z, y / z
    if math.hypot(vx - a0[0], vy - a0[1]) > VP_MAX * ref:
        return None
    return (vx, vy)


def _axo_columns(dirs):
    """A box in parallel projection, from the directions of its three edges
    alone.

    An orthographic view of three perpendicular unit axes puts them on the page
    at a_i = l_i·u_i, and the projection's two rows have to be perpendicular and
    equally long: Σ l_i²(u_ix² − u_iy²) = 0 and Σ l_i²·u_ix·u_iy = 0. Two linear
    equations in the three l_i², so the foreshortening of each axis follows from
    the angles between the edges (Gauss's theorem of axonometry), without
    assuming anything about how long the edges are.

    Returns the three screen vectors for a projection of unit scale, or None when
    no box seen in parallel projection has edges running these ways."""
    a = [ux * ux - uy * uy for ux, uy in dirs]
    b = [ux * uy for ux, uy in dirs]
    sq = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    if all(v < 0 for v in sq):
        sq = tuple(-v for v in sq)
    if not all(v > 1e-9 for v in sq):
        return None
    s2 = sum(sq[i] * dirs[i][0] ** 2 for i in range(3))
    if s2 <= 1e-12:
        return None
    return [(math.sqrt(sq[i] / s2) * dirs[i][0], math.sqrt(sq[i] / s2) * dirs[i][1])
            for i in range(3)]


def _tilt(col):
    """tan of how far an axis leans out of the picture, from its column in a
    unit-scale parallel projection (whose 2D length is the cosine)."""
    c = min(1.0, math.hypot(*col))
    return math.sqrt(max(0.0, 1.0 - c * c)) / max(c, 1e-9)


def _implied(o, fin, u_par, tan_fin, edge_len, diag):
    """The vanishing point of a wall whose edges are parallel on the page, when
    the other wall's edges converge.

    A lone vanishing point leaves the lens unknown. Taking the converging axis's
    tilt from the parallel-projection solve gives it (|v| = f/tan tilt, so f
    grows without bound as that vanishing point runs off to infinity and the
    solve meets the parallel one again); the parallel axis then gets the point on
    its own edge that makes the two axes perpendicular, v_fin·v_par = −f². It is
    far out, so that wall converges only slightly, as it would in a photo.

    Returns (vanishing point, f) or None."""
    f = math.hypot(*fin) * tan_fin
    if not (0.15 * diag <= f <= 1000.0 * diag):
        return None
    den = fin[0] * u_par[0] + fin[1] * u_par[1]
    if abs(den) < 1e-12:
        return None
    t = (-f * f - (fin[0] * o[0] + fin[1] * o[1])) / den
    if 0.0 <= t <= 1.05 * edge_len:
        return None          # it would sit on the wall itself
    return (o[0] + u_par[0] * t, o[1] + u_par[1] * t), f


def _focal(p1, p2, diag):
    """Focal length from two perpendicular vanishing points, with the principal
    point at the page centre: p1·p2 = −f². None outside any real lens."""
    f2 = -(p1[0] * p2[0] + p1[1] * p2[1])
    if f2 <= 0:
        return None
    f = math.sqrt(f2)
    return f if 0.15 * diag <= f <= 1000.0 * diag else None


_RIG_CACHE = {}


def box_points(item, page_index, options):
    """The box's six corners in native units, or None."""
    w, h, _unit_name = _page_size(item, page_index)
    for s in _shapes_for(item, page_index, options):
        if s.get("role") == "rig" and len(s.get("points") or ()) >= 6:
            return _native(s["points"][:6], w, h)
    return None


def _setup_shape(item, page_index, options, role):
    for s in _shapes_for(item, page_index, options):
        if s.get("role") == role:
            return s
    return None


def _seeded(item, page_index, options, role):
    """Whether a setup shape is still where it was seeded. The host marks seeded
    geometry and clears the mark as soon as the user drags it."""
    s = _setup_shape(item, page_index, options, role)
    return bool(s and s.get("seeded"))


def rig(item, page_index, options):
    """Solve the camera from the box.

    The box's edges come in pairs that are parallel in the world: the bottom and
    top of each wall. Where each pair meets on the page is that wall's vanishing
    point. Two perpendicular vanishing points and a centred principal point are a
    complete rectilinear camera: v1·v2 = −f² gives the focal length, each axis
    direction is K⁻¹v normalised, and the vertical is their cross product. When
    a wall's edges are parallel on the page its vanishing point is at infinity,
    and a box whose walls are both parallel is a parallel projection, solved from
    the directions of its three edges (_axo_columns). One wall converging and the
    other not is bridged by _implied, so the solve never jumps between the two.

    The vertical edges aren't used for the solve; they are its check. "vert_err"
    is how far, in degrees, the drawn verticals are from the ones the solved
    camera predicts.

    World units are arbitrary ("one unit of camera translation", or the near
    wall's length in parallel projection); the scale line converts them, see
    _metric_scale.

    Returns None without a usable box. Otherwise a dict:

      H        ground (X, Y) → image, in centred image coordinates
      vert     image column per unit of height
      planes / inv   the three plane homographies, and the invertible ones
      f        focal length in px    exact   whether it was solved
      squared  whether the frame is orthogonal by solve rather than by projection
      centre   (cx, cy)              size    (w, h) of the page
      depth    sign of the homogeneous depth meaning "in front of the camera"
      octant   always (1, 1): the box's corners say which way each axis runs
      arms     the box's three edge lengths in world units
      parallel (bool, bool) — whether each wall's edges are parallel on the page
      implied  whether one wall's perspective was inferred from the other's
      vps      the vanishing points used, in page units, or None per axis
      box      the six corners in page units
      vert_err degrees between the drawn and the predicted verticals

    Cached: a drag re-solves this on every motion event, twice a frame."""
    import numpy as np
    pts = box_points(item, page_index, options)
    if pts is None:
        return None
    w, h, _unit_name = _page_size(item, page_index)
    key = (str(item), int(page_index), float(options.get("fov") or 0.0),
           tuple(round(v, 4) for p in pts for v in p))
    hit = _RIG_CACHE.get(key)
    if hit is not None:
        return hit

    cx, cy = w / 2.0, h / 2.0
    ref = min(w, h)
    diag = math.hypot(w, h)
    c = [(p[0] - cx, p[1] - cy) for p in pts]
    o = c[BOX_O]
    ux, uy, uz = _unit(o, c[BOX_X]), _unit(o, c[BOX_Y]), _unit(o, c[BOX_Z])
    if ux is None or uy is None or uz is None:
        return None
    p1 = _meet(c[BOX_O], c[BOX_X], c[BOX_Z], c[BOX_XZ], ref)
    p2 = _meet(c[BOX_O], c[BOX_Y], c[BOX_Z], c[BOX_YZ], ref)
    parallel = (p1 is None, p2 is None)
    axo = _axo_columns([ux, uy, uz])
    len_x = math.hypot(c[BOX_X][0] - o[0], c[BOX_X][1] - o[1])
    len_y = math.hypot(c[BOX_Y][0] - o[0], c[BOX_Y][1] - o[1])

    fov = float(options.get("fov") or 0.0)
    fallback = (w / 2.0) / math.tan(math.radians(50.0) / 2.0)
    f, exact, solved, implied = fallback, False, False, False
    if fov > 0 and (p1 or p2):
        # a typed field of view fixes the lens but says nothing about whether the
        # two walls drawn are really perpendicular, so the frame is squared after
        f, exact = (w / 2.0) / max(math.tan(math.radians(fov) / 2.0), 1e-6), True
        if p1 is None or p2 is None:
            # one vanishing point and a lens: the other wall's follows
            fin, u_par, edge = (p1, uy, len_y) if p2 is None else (p2, ux, len_x)
            den = fin[0] * u_par[0] + fin[1] * u_par[1]
            if abs(den) > 1e-12:
                t = (-f * f - (fin[0] * o[0] + fin[1] * o[1])) / den
                other = (o[0] + u_par[0] * t, o[1] + u_par[1] * t)
                p1, p2 = (p1, other) if p2 is None else (other, p2)
    else:
        if p1 and p2:
            fs = _focal(p1, p2, diag)
            if fs is not None:
                f, exact, solved = fs, True, True
            else:
                # no lens fits both: trust the nearer one, whose edges converge
                # clearly, and infer the farther as if it were parallel
                d1 = math.hypot(p1[0] - o[0], p1[1] - o[1])
                d2 = math.hypot(p2[0] - o[0], p2[1] - o[1])
                if d1 <= d2:
                    p2 = None
                else:
                    p1 = None
        if not solved and (p1 is None) != (p2 is None) and axo is not None:
            if p2 is None:
                got = _implied(o, p1, uy, _tilt(axo[0]), len_y, diag)
            else:
                got = _implied(o, p2, ux, _tilt(axo[1]), len_x, diag)
            if got is not None:
                vp, f = got
                p1, p2 = (p1, vp) if p2 is None else (vp, p2)
                exact = solved = implied = True

    origin = np.array([o[0], o[1], 1.0])
    affine = p1 is None and p2 is None
    squared = True
    if affine:
        if axo is None:
            return None              # no box in parallel projection looks like this
        # the near wall's length is the world unit: any unit will do, the scale
        # line converts it
        k = len_x / max(math.hypot(*axo[0]), 1e-12)
        c1 = np.array([axo[0][0] * k, axo[0][1] * k, 0.0])
        c2 = np.array([axo[1][0] * k, axo[1][1] * k, 0.0])
        c3 = np.array([axo[2][0] * k, axo[2][1] * k, 0.0])
        f, exact = fallback, False
    else:
        # with one vanishing point and nothing to infer the other from, the
        # ordinary lens assumed above stands and the frame is squared below

        def homog(p, u):
            """The vanishing point as a homogeneous vector, signed so the axis
            runs from the near corner towards the far one: a wall coming towards
            the camera has its vanishing point behind the near corner."""
            if p is None:
                return np.array([u[0], u[1], 0.0])
            ahead = (p[0] - o[0]) * u[0] + (p[1] - o[1]) * u[1] > 0
            v = np.array([p[0], p[1], 1.0])
            return v if ahead else -v

        def column(v):
            kv = np.array([v[0] / f, v[1] / f, v[2]])
            n = np.linalg.norm(kv)
            if n < 1e-12:
                return None, None
            return np.array(v, dtype=float) / n, kv / n

        v1, v2 = homog(p1, ux), homog(p2, uy)
        c1, d1 = column(v1)
        c2, d2 = column(v2)
        if c1 is None or c2 is None:
            return None
        if not solved:
            # nothing has made these two directions perpendicular (one wall
            # parallel with no tilt to infer from, or a typed lens): project one
            # off the other. Never the parallel one, whose direction is exact.
            move_second = not (v2[2] == 0.0 and v1[2] != 0.0)
            keep, move = (d1, d2) if move_second else (d2, d1)
            moved = move - float(np.dot(move, keep)) * keep
            n = np.linalg.norm(moved)
            if n < 1e-6:
                return None
            moved = moved / n
            if move_second:
                d2 = moved
                c2 = np.array([f * d2[0], f * d2[1], d2[2]])
            else:
                d1 = moved
                c1 = np.array([f * d1[0], f * d1[1], d1[2]])
            squared = False
        d3 = np.cross(d1, d2)
        n3 = np.linalg.norm(d3)
        if n3 < 1e-3:
            return None
        d3 = d3 / n3
        c3 = np.array([f * d3[0], f * d3[1], d3[2]])
    # +Z runs from the near corner towards the top corner drawn above it
    tz = (c3[0] - o[0] * c3[2], c3[1] - o[1] * c3[2])
    if tz[0] * uz[0] + tz[1] * uz[1] < 0:
        c3 = -c3

    ground = np.column_stack([c1, c2, origin])
    planes = {
        PLANES[0]: ground,
        PLANES[1]: np.column_stack([c1, c3, origin]),
        PLANES[2]: np.column_stack([c2, c3, origin]),
    }
    inv = {}
    for name, P in planes.items():
        try:
            # relative, not absolute: compared against the volume the columns
            # would span if they were orthogonal, which is scale-free
            vol = float(np.linalg.norm(P[:, 0]) * np.linalg.norm(P[:, 1])
                        * np.linalg.norm(P[:, 2]))
            if vol > 0 and abs(float(np.linalg.det(P))) > 1e-6 * vol:
                inv[name] = np.linalg.inv(P)
        except Exception:
            pass
    if PLANES[0] not in inv:
        return None

    # the box's edge lengths in world units, read back through the planes
    arms = []
    for idx, plane, coord in ((BOX_X, PLANES[0], 0), (BOX_Y, PLANES[0], 1),
                              (BOX_Z, PLANES[1], 1)):
        m = inv.get(plane)
        q = _apply(m, c[idx], 1.0) if m is not None else None
        arms.append(abs(q[coord]) if q else 1.0)

    # the check: the vertical the camera predicts at each bottom corner, against
    # the one drawn there
    err = 0.0
    for base, top, world in ((BOX_O, BOX_Z, (0.0, 0.0)), (BOX_X, BOX_XZ, (arms[0], 0.0)),
                             (BOX_Y, BOX_YZ, (0.0, arms[1]))):
        q = ground @ np.array([world[0], world[1], 1.0])
        if abs(q[2]) < 1e-12:
            continue
        t = (c3[0] * q[2] - q[0] * c3[2], c3[1] * q[2] - q[1] * c3[2])
        drawn = _unit(c[base], c[top])
        if drawn is None or math.hypot(*t) < 1e-12:
            continue
        cosang = abs(t[0] * drawn[0] + t[1] * drawn[1]) / math.hypot(*t)
        err = max(err, math.degrees(math.acos(min(1.0, cosang))))

    out = {"H": ground, "vert": c3, "planes": planes, "inv": inv, "f": f,
           "exact": exact, "squared": squared, "centre": (cx, cy), "size": (w, h),
           # the origin's homogeneous coordinate is 1.0 in every branch, so "in
           # front of the camera" is always the positive side
           "depth": 1.0, "octant": (1.0, 1.0), "arms": arms,
           "origin_img": pts[BOX_O], "tip_img": (pts[BOX_X], pts[BOX_Y]),
           "up_img": (float(tz[0]), float(tz[1])) if tz[0] * uz[0] + tz[1] * uz[1] >= 0
           else (float(-tz[0]), float(-tz[1])),
           "parallel": parallel, "implied": implied,
           "vps": tuple(None if p is None else (p[0] + cx, p[1] + cy) for p in (p1, p2)),
           "box": pts, "vert_err": err}
    if len(_RIG_CACHE) > 8:
        _RIG_CACHE.clear()
    _RIG_CACHE[key] = out
    return out


def _scale_line(item, page_index, options):
    for s in _shapes_for(item, page_index, options):
        if s.get("role") == "scale" and len(s.get("points") or ()) >= 2:
            return s
    return None


def _metric_scale(item, page_index, options, r):
    """World units → the chosen unit, from the "Set scale" line: the same line
    as in 2D, measured on the plane it was drawn on."""
    s = _scale_line(item, page_index, options)
    ref_len = float(options.get("ref_length") or 0.0)
    if s is None or r is None or ref_len <= 0:
        return None
    w, h = r["size"]
    pts = _native(s["points"][:2], w, h)
    name = str(options.get("plane", PLANE_AUTO))
    if name == PLANE_AUTO:
        name = _auto_plane(r, pts)
    inv = r["inv"].get(name)
    if inv is None:
        inv = r["inv"].get(PLANES[0])
    cx, cy = r["centre"]
    ends = [_apply(inv, (p[0] - cx, p[1] - cy), r["depth"]) for p in pts]
    if any(q is None for q in ends):
        return None
    length = _seg_len(ends[0], ends[1])
    return ref_len / length if length > 1e-12 else None


# --------------------------------------------------------------------------- #
# a metric frame: image points in, real-world coordinates out
# --------------------------------------------------------------------------- #
class _Frame:
    """Turns image points into coordinates you can do plain geometry on.

    In 2D that's a uniform scale. In 3D it's the inverse homography of the
    chosen plane, which also removes the perspective — so a distance, an area
    and an angle are all just their textbook formulas afterwards."""

    def __init__(self, unit, scale=None, inv=None, centre=(0.0, 0.0), depth=1.0,
                 k=1.0):
        self.unit = unit
        self.scale = scale
        self.inv = inv
        self.centre = centre
        self.depth = depth
        self.k = k          # world units → the chosen unit, from the scale line

    @property
    def metric(self):
        return self.scale is not None or self.inv is not None

    @property
    def projective(self):
        return self.inv is not None

    def to_world(self, p):
        if self.inv is None:
            k = self.scale if self.scale else 1.0
            return (p[0] * k, p[1] * k)
        x, y = p[0] - self.centre[0], p[1] - self.centre[1]
        m = self.inv
        d = m[2, 0] * x + m[2, 1] * y + m[2, 2]
        # A point above the vanishing line is behind the camera: it inverts to a
        # finite, entirely plausible-looking coordinate. Reject it rather than
        # report a confident wrong number.
        if abs(d) < 1e-9 or d * self.depth <= 0:
            return None
        return ((m[0, 0] * x + m[0, 1] * y + m[0, 2]) / d * self.k,
                (m[1, 0] * x + m[1, 1] * y + m[1, 2]) / d * self.k)

    def many(self, pts):
        out = []
        for p in pts:
            q = self.to_world(p)
            if q is None:
                return None
            out.append(q)
        return out


def _scale_factor(shapes, w, h, options):
    """Native units → the chosen unit, from the 2D reference line."""
    ref = float(options.get("ref_length") or 0.0)
    if ref <= 0:
        return None
    for s in shapes:
        if s.get("role") == "scale" and len(s.get("points") or ()) >= 2:
            length = _seg_len(*_native(s["points"][:2], w, h))
            if length > 1e-9:
                return ref / length
    return None


def _auto_plane(r, pts):
    """Which of the three planes a shape was drawn on, from where it sits.

    The rig already says: the origin goes on the corner where two walls meet the
    ground, and each arm runs along one of those wall bases. So the image is cut
    into three regions by the vertical through the origin and by the ground
    itself, and a shape lands in one of them. Reading the face off the drawing
    is the difference between a per-shape answer and one global setting that
    silently changes every other measurement on the page when you touch it."""
    o = r["origin_img"]
    px = sum(p[0] for p in pts) / len(pts) - o[0]
    py = sum(p[1] for p in pts) / len(pts) - o[1]
    ux, uy = r["up_img"]
    arms = []
    for tip in r["tip_img"]:
        arms.append((tip[0] - o[0], tip[1] - o[1]))

    def climbs(arm):
        """Is the point on the same side of this wall's base line as 'up'?"""
        here = arm[0] * py - arm[1] * px
        up = arm[0] * uy - arm[1] * ux
        return here * up > 0

    if not any(climbs(a) for a in arms):
        # below both wall bases: the floor. Tested against the base lines rather
        # than the horizon, because the horizon is where the ground *ends*, not
        # where the walls begin, and the two are far apart in an ordinary photo.
        if r["inv"].get(PLANES[0]) is not None:
            return PLANES[0]
    # otherwise it is on one of the two walls: whichever arm it lies furthest
    # along. Projecting onto the arm rather than comparing image x handles arms
    # drawn in any direction, including one running straight up the frame.
    proj = []
    for ax, ay in arms:
        n = math.hypot(ax, ay) or 1.0
        proj.append((px * ax + py * ay) / n)
    return PLANES[1] if proj[0] >= proj[1] else PLANES[2]


def _frame(item, page_index, options, shapes, w, h, native_unit):
    """The frame measurements are taken in, plus the solved rig when there is
    one (the caller needs it for the grids).

    In 3D the plane can be per shape, so this returns a callable that yields a
    frame for a given set of points rather than a single frame."""
    unit = options.get("unit", "m")
    if str(options.get("mode", MODE_2D)) == MODE_3D:
        r = rig(item, page_index, options)
        if r is None:
            return (lambda _pts=None: _Frame(native_unit)), None
        k = _metric_scale(item, page_index, options, r)
        if k is None:
            return (lambda _pts=None: _Frame(native_unit)), r
        chosen = str(options.get("plane", PLANE_AUTO))

        def frame_for(pts=None):
            name = chosen
            if name == PLANE_AUTO:
                name = _auto_plane(r, pts) if pts else PLANES[0]
            inv = r["inv"].get(name if name in r["inv"] else PLANES[0])
            if inv is None:
                return _Frame(native_unit)
            return _Frame(unit, inv=inv, centre=r["centre"], depth=r["depth"], k=k)

        return frame_for, r
    k = _scale_factor(shapes, w, h, options)
    flat = _Frame(unit, scale=k) if k else _Frame(native_unit)
    return (lambda _pts=None: flat), None


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #
def _area_conversion(options, metric):
    """(factor, label) taking a length-unit² area to the chosen area unit."""
    unit = options.get("unit", "m")
    target = options.get("area_unit", "m²")
    if not metric or target == "auto" or target not in AREA_UNITS[1:]:
        return 1.0, ("%s²" % unit if metric else None)
    src_m = UNITS.get(unit, 1.0)
    dst_m = UNITS.get(target[:-1], 1.0)
    return (src_m / dst_m) ** 2, target


def _fmt(value, decimals):
    return f"{value:.{int(decimals)}f}"


def _text_len(text, size):
    """Roughly how long a label is, in the units its font size is given in."""
    return 0.6 * size * len(text) + size      # characters plus the pill's padding


def _label_box(x, y, text, size):
    """Roughly the pill a label is drawn in, centred on (x, y), a little
    generous: the preview draws labels at a fixed screen size, which on a large
    page is bigger than this estimate."""
    hw = _text_len(str(text), size) * 0.65
    hh = size * 0.95
    return (x - hw, y - hh, x + hw, y + hh)


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _offset(a, b, w, h, amount, away_from=None):
    """A point beside the middle of segment a→b. Pushed away from `away_from`
    when one is given — the centre of a polygon, so side labels sit outside it —
    and otherwise to the upper side, so a label never sits on its own line."""
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    px, py = -dy / n, dx / n
    if away_from is not None:
        if (mx - away_from[0]) * px + (my - away_from[1]) * py < 0:
            px, py = -px, -py
    elif py > 0:
        px, py = -px, -py
    return ((mx + px * amount) / w, (my + py * amount) / h)


OFF_PLANE = "not on the measuring plane"


def measurements(item, page_index, options):
    """Every label this page's shapes produce.

    Returns a list of {"x", "y", "text", "color", "role"} with x/y as 0–1 page
    fractions. Shared by the live preview and the burned-in export so the two
    can never disagree."""
    w, h, native = _page_size(item, page_index)
    shapes = _shapes_for(item, page_index, options)
    if not shapes:
        return []
    frame_for, _r = _frame(item, page_index, options, shapes, w, h, native)
    base = frame_for()
    unit = base.unit
    dec = int(options.get("decimals", 2))
    afac, aunit = _area_conversion(options, base.metric)
    if aunit is None:
        aunit = "%s²" % native
    pad = 0.022 * max(w, h)
    _lw, fs = _style(options, w, h, native == "pt")
    out = []

    def add(x, y, text, color, role):
        out.append({"x": max(0.0, min(1.0, x)), "y": max(0.0, min(1.0, y)),
                    "text": text, "color": color, "role": role})

    def txt(value):
        return "%s %s" % (_fmt(value, dec), unit)

    for s in shapes:
        role = s.get("role", "distance")
        if role in SETUP_ROLES:
            continue
        shape = s.get("shape", "line")
        color = ROLE_COLOR.get(role, DIST_COLOR)
        pts = _native(s.get("points") or (), w, h)
        if len(pts) < 2:
            continue

        if role == "scale":
            # it reads the length typed in, whichever mode set the scale from it
            ref = float(options.get("ref_length") or 0.0)
            body = txt(ref) if base.metric else "%s %s" % (
                _fmt(_seg_len(pts[0], pts[1]), dec), native)
            x, y = _offset(pts[0], pts[1], w, h, pad)
            add(x, y, body, color, role)
            continue

        # each shape is measured on its own plane, so setting the face for one
        # wall no longer moves every other reading on the page
        frame = frame_for(pts)
        world = frame.many(pts)
        if world is None:
            # Every point that inverts to somewhere behind the camera is a point
            # off the plane being measured on — anything above the horizon when
            # the ground is selected, which is most of a photograph of a
            # building. Say so where the shape is, rather than showing nothing
            # at all and leaving the mode looking broken.
            mid = _centroid(pts) if len(pts) > 2 else (
                (pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0)
            add(mid[0] / w, mid[1] / h, OFF_PLANE, ANGLE_COLOR, role)
            continue

        if shape == "angle" and len(pts) >= 3 and len(world) >= 3:
            a, v, b = world[0], world[1], world[2]
            deg = _angle_between((a[0] - v[0], a[1] - v[1]), (b[0] - v[0], b[1] - v[1]))
            ia, iv, ib = pts[0], pts[1], pts[2]
            d1 = math.hypot(ia[0] - iv[0], ia[1] - iv[1]) or 1.0
            d2 = math.hypot(ib[0] - iv[0], ib[1] - iv[1]) or 1.0
            bx = (ia[0] - iv[0]) / d1 + (ib[0] - iv[0]) / d2
            by = (ia[1] - iv[1]) / d1 + (ib[1] - iv[1]) / d2
            bn = math.hypot(bx, by)
            if bn < 1e-9:
                bx, by, bn = 0.0, -1.0, 1.0
            rr = pad * 2.0
            add((iv[0] + bx / bn * rr) / w, (iv[1] + by / bn * rr) / h,
                "%s°" % _fmt(deg, min(dec, 2)), color, role)
            continue

        if shape == "polygon" and len(world) >= 3:
            # One shape, both readings: the enclosed area at the middle and the
            # real length of every side around the outside. A polygon drawn to
            # get a floor area is usually the same polygon you want the wall
            # lengths of, and drawing it twice was the old way round.
            area = _polygon_area(world) * afac
            cx, cy = _centroid(pts)
            add(cx / w, cy / h, "%s %s" % (_fmt(area, dec), aunit), color, role)
            if options.get("segment_labels", True):
                # A side label that would land on the area or on another side's
                # label is left out: a small or foreshortened window drawn as a
                # polygon is one area, not five labels in a heap.
                taken = [_label_box(cx, cy, out[-1]["text"], fs)]
                n = len(pts)
                for i in range(n):
                    j = (i + 1) % n
                    text = txt(_seg_len(world[i], world[j]))
                    if _seg_len(pts[i], pts[j]) < _text_len(text, fs) * 1.3:
                        continue
                    x, y = _offset(pts[i], pts[j], w, h, pad, away_from=(cx, cy))
                    box = _label_box(x * w, y * h, text, fs)
                    if any(_overlap(box, b) for b in taken):
                        continue
                    taken.append(box)
                    add(x, y, text, color, role)
            if options.get("show_perimeter", False):
                per = _path_len(world, closed=True)
                add(cx / w, (cy + pad * 1.5) / h, ["P ", txt(per)], color, role)
            continue

        for i, (a, b) in enumerate(_segments(pts)):
            x, y = _offset(a, b, w, h, pad)
            add(x, y, txt(_seg_len(world[i], world[i + 1])), color, role)

    return out


# --------------------------------------------------------------------------- #
# the setup view: axes, grids, horizon
# --------------------------------------------------------------------------- #
MAX_GRID_LINES = 60


def _project(P, world, centre, depth, w, h):
    """Project a run of world (u, w) samples, in one matrix multiply, into image
    fractions — split wherever the line crosses behind the camera or shoots off
    past the horizon. Returns a list of runs, each a list of (fx, fy).

    Vectorised because this is on the live-drag path: a full three-plane grid is
    several thousand points and gets rebuilt on every pointer motion."""
    import numpy as np
    cx, cy = centre
    q = np.asarray(world, dtype=float) @ np.asarray(P, dtype=float).T
    d = q[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = q[:, 0] / d + cx
        ys = q[:, 1] / d + cy
    ok = (np.abs(d) > 1e-12) & (d * depth > 0)
    ok &= np.isfinite(xs) & np.isfinite(ys)
    ok &= (np.abs(xs) < 4.0 * w) & (np.abs(ys) < 4.0 * h)
    if not ok.any():
        return []
    # Split into runs of consecutive good samples without walking the array in
    # Python. A full three-plane grid is tens of thousands of samples and this
    # runs on every pointer motion event, twice a frame: the per-point loop that
    # used to be here was the single most expensive thing in the setup view.
    fx, fy = xs / w, ys / h
    idx = np.flatnonzero(ok)
    cuts = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], cuts + 1))
    ends = np.concatenate((cuts + 1, [len(idx)]))
    runs = []
    for s, e in zip(starts, ends):
        if e - s > 1:
            run = idx[s:e]
            runs.append(list(zip(fx[run].tolist(), fy[run].tolist())))
    return runs


def _ticks(lo, hi, step):
    """Multiples of step across [lo, hi], so grid lines land on round values and
    line up with the rectangle's own edges."""
    out = []
    v = math.ceil(lo / step - 1e-9) * step
    while v <= hi + 1e-9:
        out.append(v)
        v += step
    return out


GRID_CHUNK = 4           # samples per faded piece of a grid line


def _plane_paths(r, plane_h, u_range, w_range, step, color_u, color_w, w, h,
                 samples=24, alpha=0.45, fade=None, width=1.0):
    """Grid lines on one plane, drawn as two families of coloured polylines.

    With `fade` = (reach along u, reach along w) each line is cut into pieces
    that grow fainter away from the origin, so the grid is clear where the edges
    are being matched and doesn't cover the rest of the picture."""
    import numpy as np
    out = []
    ts = np.linspace(0.0, 1.0, samples + 1)
    # coarsen once, off the longer side, so both families keep the same spacing
    # and the grid still reads as square
    span = max(u_range[1] - u_range[0], w_range[1] - w_range[0], 1e-9)
    while span / step > MAX_GRID_LINES:
        step *= 2.0

    def family(fixed_range, run_range, fixed_is_u, color):
        along = run_range[0] + (run_range[1] - run_range[0]) * ts
        for v in _ticks(fixed_range[0], fixed_range[1], step):
            fixed = np.full_like(along, v)
            a, b = (fixed, along) if fixed_is_u else (along, fixed)
            world = np.column_stack([a, b, np.ones_like(along)])
            if not fade:
                for seg in _project(plane_h, world, r["centre"], r["depth"], w, h):
                    out.append({"points": seg, "color": color, "width": 1.0,
                                "alpha": alpha})
                continue
            near = np.maximum(np.abs(a) / max(fade[0], 1e-12),
                              np.abs(b) / max(fade[1], 1e-12))
            for i0 in range(0, samples, GRID_CHUNK):
                part = slice(i0, min(i0 + GRID_CHUNK, samples) + 1)
                d = min(1.0, float(near[part].mean()))
                fa = alpha * (1.0 - 0.85 * d)
                for seg in _project(plane_h, world[part], r["centre"], r["depth"],
                                    w, h):
                    out.append({"points": seg, "color": color, "width": width,
                                "alpha": fa})

    family(u_range, w_range, True, color_u)
    family(w_range, u_range, False, color_w)
    return out


def _horizon_segment(H, w, h, cx, cy):
    """The ground plane's vanishing line, clipped to a band around the page and
    returned in page fractions. None when it isn't anywhere near the frame."""
    import numpy as np
    try:
        line = np.linalg.inv(H)[2, :]
    except Exception:
        return None
    a, b, c = float(line[0]), float(line[1]), float(line[2])
    if not all(math.isfinite(v) for v in (a, b, c)):
        return None
    hw, hh = w / 2.0, h / 2.0
    if abs(b) >= abs(a):                      # shallow: cut the left/right edges
        if abs(b) < 1e-15:
            return None
        pts = [(-hw, -(a * -hw + c) / b), (hw, -(a * hw + c) / b)]
    else:                                     # steep: cut the top/bottom edges
        pts = [(-(b * -hh + c) / a, -hh), (-(b * hh + c) / a, hh)]
    if any(not math.isfinite(v) for p in pts for v in p):
        return None
    if all(abs(y) > 3.0 * h for _x, y in pts) or all(abs(x) > 3.0 * w for x, _y in pts):
        return None                           # nowhere near the visible page
    return [((x + cx) / w, (y + cy) / h) for x, y in pts]


def _setup_paths(item, page_index, options):
    """Everything the 3D setup mode draws: the box's edges, each wall's edges
    carried on towards their vanishing point, the plane grids and the horizon.
    Only ever called while that mode is active, and never exported."""
    r = rig(item, page_index, options)
    if r is None:
        # the box is the only feedback the setup mode gives, so say why there is
        # no solve instead of leaving the mode looking broken
        return [], [{"x": 0.5, "y": 0.035, "role": "rig", "color": ANGLE_COLOR,
                     "text": ["These corners don't make a box: check each one "
                              "is on the right corner of the picture."]}]
    w, h = r["size"]
    ax, ay, az = r["arms"]
    paths, labels = [], []

    # the grid: the step is typed in real units but drawn in world units, so it
    # needs the scale line; without one, eighths of the box
    k = _metric_scale(item, page_index, options, r)
    typed = float(options.get("grid_step") or 0.0)
    step = typed / k if typed > 0 and k else max(ax, ay, az) / 8.0
    reach = max(0.5, float(options.get("grid_extent", 1.25)))

    def plane_grid(plane, u_arm, w_arm, cu, cw):
        # only on the box's own faces and a little past them: the box already
        # shows the walls, and a grid behind it was clutter. Strongest at the
        # near corner and fading out from there.
        P = r["planes"][plane]
        fade = (u_arm * reach, w_arm * reach)
        return _plane_paths(r, P, (0.0, u_arm * reach), (0.0, w_arm * reach), step,
                            cu, cw, w, h, alpha=0.7, fade=fade, width=1.2)

    paths += plane_grid(PLANES[0], ax, ay, AXIS_Y, AXIS_X)
    paths += plane_grid(PLANES[1], ax, az, AXIS_Z, AXIS_X)
    paths += plane_grid(PLANES[2], ay, az, AXIS_Z, AXIS_Y)

    # each wall's two edges carried on to where they meet: the clearest picture
    # of what the corners are doing to the perspective
    box = r["box"]
    for vp, pairs, color in ((r["vps"][0], ((BOX_O, BOX_X), (BOX_Z, BOX_XZ)), AXIS_X),
                             (r["vps"][1], ((BOX_O, BOX_Y), (BOX_Z, BOX_YZ)), AXIS_Y)):
        if vp is None or r["implied"]:
            continue
        for a, b in pairs:
            near, far = box[a], box[b]
            # towards the vanishing point from whichever end is closer to it
            start = far if (math.hypot(vp[0] - far[0], vp[1] - far[1])
                            < math.hypot(vp[0] - near[0], vp[1] - near[1])) else near
            paths.append({"points": [(start[0] / w, start[1] / h), (vp[0] / w, vp[1] / h)],
                          "color": color, "width": 1.2, "alpha": 0.6, "dash": True})

    for a, b, color in BOX_EDGES:
        paths.append({"points": [(box[a][0] / w, box[a][1] / h), (box[b][0] / w, box[b][1] / h)],
                      "color": color, "width": 3.0, "alpha": 0.95})

    # axis letters just past the far end of each edge from the near corner, so
    # they never sit on a grab point
    off = 0.04 * max(w, h)
    for idx, text, color in ((BOX_X, "X", AXIS_X), (BOX_Y, "Y", AXIS_Y), (BOX_Z, "Z", AXIS_Z)):
        u = _unit(box[BOX_O], box[idx])
        if u is not None:
            labels.append({"x": (box[idx][0] + u[0] * off) / w,
                           "y": (box[idx][1] + u[1] * off) / h,
                           "text": text, "color": color, "role": "rig"})

    seg = _horizon_segment(r["H"], w, h, *r["centre"])
    if seg:
        paths.append({"points": seg, "color": HORIZON, "width": 1.5,
                      "dash": True, "alpha": 0.8})

    labels += _setup_labels(r, w)
    return paths, labels


VERT_TOLERANCE = 2.0      # degrees the drawn verticals may be off before it says so
VERT_WARN = 5.0           # and before it is worth the amber warning


def _setup_labels(r, w):
    """What the solve made of the box, and whether its verticals agree.

    Prose is split from numbers so the host can translate the words: it renders
    a list of segments by translating each one and joining them."""
    out = []
    px, py = r["parallel"]
    if px and py:
        # not a problem: a real axonometric drawing needs nothing more
        note, tone = (["Parallel projection (axonometric)."], HORIZON)
    elif r["implied"]:
        note, tone = (["One wall's edges are parallel, so its perspective is "
                       "inferred from the other's."], HORIZON)
    elif not r["exact"]:
        note, tone = (["No real lens fits these edges: check the corners."],
                      ANGLE_COLOR)
    else:
        note, tone = (["field of view ",
                       "%.0f°" % (2 * math.degrees(math.atan((w / 2.0) / r["f"])))],
                      RIG_COLOR)
    out.append({"x": 0.5, "y": 0.035, "text": note, "color": tone, "role": "rig"})
    if r["vert_err"] > VERT_TOLERANCE:
        # the top corners are the box's own check: with the perspective solved
        # from the walls, each vertical edge has only one right direction
        out.append({"x": 0.5, "y": 0.085, "color": ANGLE_COLOR, "role": "rig",
                    "text": ["the vertical edges are ", "%.0f°" % r["vert_err"],
                             " off: check the top corners"]})
    return out


# --------------------------------------------------------------------------- #
# host hooks
# --------------------------------------------------------------------------- #
def preview(item, page_index, options, context):
    """The host draws the shapes; this supplies the read-outs, and the grid and
    axes while the 3D setup mode is the one being used."""
    size = max(9, min(28, int(options.get("label_size", 14))))
    try:
        labels = measurements(item, page_index, options)
    except Exception:
        # preview() runs on every navigation and every pointer move; a file that
        # moved under us must not take the whole panel down
        labels = []
    paths = []
    outset = None
    if (str(options.get("mode", MODE_2D)) == MODE_2D
            and SHEETS.get(str(options.get("sheet", SHEET_OFF)))):
        try:
            paths, extra, outset = _sheet_preview(item, page_index, options)
            labels = labels + extra
        except Exception:
            import traceback
            traceback.print_exc()
    if (str(options.get("mode", MODE_2D)) == MODE_3D
            and context.get("shape_kind") in SETUP_ROLES):
        try:
            paths, extra = _setup_paths(item, page_index, options)
            labels = labels + extra
        except Exception:
            # never swallow this quietly: the setup view disappearing with no
            # explanation is indistinguishable from the feature not working
            import traceback
            traceback.print_exc()
            paths = []
            labels = labels + [{"x": 0.5, "y": 0.035, "role": "rig",
                                "color": ANGLE_COLOR,
                                "text": ["the setup view hit an error — run "
                                         "PageForge from a terminal to see it"]}]
    out = {"labels": [dict(lb, size=size) for lb in labels], "paths": paths}
    if outset:
        out["outset"] = outset
    try:
        out["hint"] = _hint(item, page_index, options, context)
    except Exception:
        import traceback
        traceback.print_exc()
    return out


# The hint above the preview: the steps for the active mode, the one to do now
# in bold, and how the active drawing mode is used on a line of its own.
STEPS_2D = ("1. Draw a “Set scale” line over a length you know",
            "2. Type that length in Reference length",
            "3. Measure with Distance, Area or Angle")
STEPS_3D = ("1. In 3D setup, drag the box's corners onto the matching corners "
            "in the picture",
            "2. Draw a “Set scale” line over a known length, such as a door, and "
            "type it",
            "3. Measure with Distance, Area or Angle")
_DELETE = " Right-click a shape to delete it."
KIND_NOTES = {
    "scale": "Drag from one end of the known length to the other, or click both "
             "ends. Hold Shift to snap to 0/45/90°.",
    "distance": "Drag, or click both ends. Hold Shift to snap to 0/45/90°." + _DELETE,
    "area": "Click each corner, then double-click or right-click to close the "
            "shape." + _DELETE,
    "angle": "Click one end, then the corner, then the other end." + _DELETE,
    "rig": "Near corner at the bottom, the far end of each wall, then the same "
           "three at the top.",
}


def _hint(item, page_index, options, context):
    kind = context.get("shape_kind")
    note = KIND_NOTES.get("rig" if kind in SETUP_ROLES else kind, "")
    shapes = _shapes_for(item, page_index, options)
    if str(options.get("mode", MODE_2D)) == MODE_3D:
        r = rig(item, page_index, options)
        # still on the box while 3D setup is open and nothing has been scaled:
        # one corner moved is not a box placed
        if (r is None or _seeded(item, page_index, options, "rig")
                or (kind in SETUP_ROLES and _scale_line(item, page_index, options) is None)):
            cur = 0
        else:
            cur = 1 if _metric_scale(item, page_index, options, r) is None else 2
        return {"steps": list(STEPS_3D), "current": cur, "note": note}
    has_scale = any(s.get("role") == "scale" and len(s.get("points") or ()) >= 2
                    for s in shapes)
    cur = 0 if not has_scale else (1 if kind == "scale" else 2)
    return {"steps": list(STEPS_2D), "current": cur, "note": note}


def warning(context, options):
    """One sentence saying why nothing is being measured. Fixed strings, no
    interpolation: the host looks each one up in its translation catalog."""
    files = context.get("files") or []
    if not files:
        return None
    item = files[min(context.get("index", 0), len(files) - 1)]
    page = context.get("page_index", 0)
    shapes = _shapes_for(item, page, options)
    in_3d = str(options.get("mode", MODE_2D)) == MODE_3D
    skip = SETUP_ROLES + (("scale",) if in_3d else ())
    measurable = [s for s in shapes if s.get("role") not in skip]
    # Next steps are the hint's job; this is only for something actually wrong.
    if in_3d:
        r = rig(item, page, options)
        k = _metric_scale(item, page, options, r) if r is not None else None
        # only once something is measured: while the corners are still being
        # placed the setup view's own read-out says how far off they are
        if measurable and r is not None and r["vert_err"] > VERT_WARN:
            return ("The box's vertical edges don't match its perspective, so "
                    "measurements will be off. Check its top corners.")
        if measurable and k is None:
            return ("The 3D setup isn't finished, so these values are in the "
                    "page's own units. Follow the steps in 3D setup.")
        return None
    if not measurable:
        return None
    w, h, _native = _page_size(item, page)
    if _scale_factor(shapes, w, h, options) is None:
        if SHEETS.get(str(options.get("sheet", SHEET_OFF))):
            return ("A drawing scale needs a real length to work from. Draw a "
                    "“Set scale” line over something you know the size of and "
                    "type that length.")
        return ("No reference line, so values are in the page's own units. Draw a "
                "“Set scale” line over a known length and type that length.")
    if SHEETS.get(str(options.get("sheet", SHEET_OFF))):
        if _parse_ratio(options.get("sheet_scale", "1:100")) is None:
            return ("That drawing scale can't be read. Write it as 1:100, 1/100 "
                    "or just 100.")
        geo = _sheet_geometry(item, page, options)
        if geo is not None and geo["cropped"]:
            return ("At this scale the drawing is bigger than the paper, so the "
                    "edges outside the margins have been cropped off. The scale "
                    "is still true; turn cropping off to see how much is being "
                    "lost.")
        if geo is not None and not geo["fits"]:
            return ("At this scale the drawing is bigger than the paper, so it "
                    "will run off the sheet. Use a larger sheet, a smaller "
                    "scale, narrower margins, or turn on cropping.")
    return None


def confirm_process(context, options):
    """Stop the export and say why, before anything is written.

    Exporting a page with no scale set is almost never what somebody meant: the
    numbers come out in pixels or points, and on a sheet there is no scale to
    lay the drawing out at, so nothing is produced at all. The amber warning
    says so, but a warning is easy to walk past."""
    files = context.get("files") or []
    if not files:
        return None
    item = files[min(int(context.get("index", 0)), len(files) - 1)]
    page = int(context.get("page_index", 0))
    shapes = _shapes_for(item, page, options)
    if not [s for s in shapes if s.get("role") not in SETUP_ROLES]:
        return None
    w, h, _n = _page_size(item, page)
    if str(options.get("mode", MODE_2D)) == MODE_3D:
        r = rig(item, page, options)
        if r is not None and _metric_scale(item, page, options, r) is not None:
            return None
    elif _scale_factor(shapes, w, h, options) is not None:
        return None
    if (str(options.get("mode", MODE_2D)) == MODE_2D
            and SHEETS.get(str(options.get("sheet", SHEET_OFF)))):
        return ("No scale has been set on this page, so there is nothing to lay "
                "the drawing out at and no sheet will be written. Draw a “Set "
                "scale” line over a known length and type that length first.")
    return ("No scale has been set on this page, so the measurements will be "
            "exported in pixels rather than real units. Draw a “Set scale” line "
            "over a known length and type that length first.")


def suggest_options(context, options):
    """A photograph is almost never a straight-on scan, and a PDF page almost
    always is — so start each in the mode it most likely needs."""
    files = context.get("files") or []
    if not files:
        return {}
    kinds = {Path(f).suffix.lower() for f in files}
    if kinds and kinds <= {".pdf"} and options.get("mode") != MODE_2D:
        return {"mode": MODE_2D}
    return {}


# --------------------------------------------------------------------------- #
# opening geometry
# --------------------------------------------------------------------------- #
SEED_BOX = (0.40, 0.32, 0.36)   # the two walls and the height, in short sides
SEED_RISE = (18.0, 22.0)        # degrees above horizontal each wall runs at


def seed_shape(kind_id, options, context):
    """The opening box, fitted to the page so it fits whatever its shape."""
    w = float(context.get("page_w") or 0.0)
    h = float(context.get("page_h") or 0.0)
    if kind_id != "rig" or w <= 0 or h <= 0:
        return None
    return _box_seed(w, h)


def _box_seed(w, h):
    """A box in parallel projection in the middle of the page, so the opening
    solve assumes nothing about a lens. Its corners are then dragged onto the
    picture one by one, and the perspective appears as its edges stop being
    parallel."""
    ref = min(w, h)
    ox, oy = 0.5 * w, 0.5 * h + 0.24 * ref
    rx, ry = math.radians(SEED_RISE[0]), math.radians(SEED_RISE[1])
    x = (ox + SEED_BOX[0] * ref * math.cos(rx), oy - SEED_BOX[0] * ref * math.sin(rx))
    y = (ox - SEED_BOX[1] * ref * math.cos(ry), oy - SEED_BOX[1] * ref * math.sin(ry))
    up = SEED_BOX[2] * ref
    pts = [(ox, oy), x, y, (ox, oy - up), (x[0], x[1] - up), (y[0], y[1] - up)]
    return [(min(1.0, max(0.0, px / w)), min(1.0, max(0.0, py / h))) for px, py in pts]


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def _hex_rgb(value, fallback=(0.11, 0.44, 0.82)):
    try:
        v = str(value).lstrip("#")
        if len(v) == 3:
            v = "".join(c * 2 for c in v)
        return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:
        return fallback


def _style(options, w, h, is_pdf):
    """Line width and font size in the page's own units."""
    ref = 1.0 if is_pdf else max(w, h) / 1000.0
    lw = max(0.6, float(options.get("line_width", 2.0)) * ref)
    fs = max(6.0, float(options.get("label_size", 14)) * ref)
    return lw, fs


def _exportable(shapes):
    return [s for s in shapes if s.get("role") not in SETUP_ROLES]


# --------------------------------------------------------------------------- #
# laying the drawing out on paper at a true scale
# --------------------------------------------------------------------------- #
def _parse_ratio(text):
    """A drawing scale as the number of real units per drawn unit.

    Accepts the three ways people write it — "1:100", "1/100", "100" — and both
    decimal separators, because a spin box isn't involved here and the field is
    free text."""
    s = str(text or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return None
    for sep in (":", "/"):
        if sep in s:
            a, _, b = s.partition(sep)
            try:
                a, b = float(a), float(b)
            except ValueError:
                return None
            return b / a if a > 0 and b > 0 else None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


_CONTENT_CACHE = {}
CONTENT_PX = 600         # long side of the render the blank margins are found on


def _content_box(item, page_index):
    """The part of the page with something on it, in native units, found on a
    small render: anything clearly different from the colour in the corners. A
    plan is usually drawn on a sheet much larger than the drawing, and it is the
    drawing that has to fit the paper, not the empty paper around it. None when
    the page is blank or can't be read."""
    import numpy as np
    item = Path(item)
    try:
        stamp = item.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = (str(item), int(page_index), stamp)
    if key in _CONTENT_CACHE:
        return _CONTENT_CACHE[key]
    box = None
    try:
        w, h, _n = _page_size(item, page_index)
        if item.suffix.lower() == ".pdf":
            import fitz
            doc = fitz.open(item)
            try:
                page = doc[max(0, min(page_index, doc.page_count - 1))]
                z = CONTENT_PX / max(page.rect.width, page.rect.height, 1.0)
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
                arr = np.frombuffer(pix.samples, np.uint8).reshape(
                    pix.height, pix.width, pix.n)[:, :, :3]
            finally:
                doc.close()
        else:
            from PIL import Image
            with Image.open(item) as im:
                im = im.convert("RGBA")
                im.thumbnail((CONTENT_PX, CONTENT_PX))
                flat = Image.new("RGB", im.size, (255, 255, 255))
                flat.paste(im, mask=im.getchannel("A"))
                arr = np.asarray(flat)
        corners = np.array([arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]], float)
        bg = np.median(corners, axis=0)
        ink = np.abs(arr.astype(float) - bg).max(axis=2) > 24
        ys, xs = np.nonzero(ink)
        if len(xs):
            rh, rw = ink.shape
            box = (xs.min() / rw * w, ys.min() / rh * h,
                   (xs.max() + 1) / rw * w, (ys.max() + 1) / rh * h)
    except Exception:
        import traceback
        traceback.print_exc()
    if len(_CONTENT_CACHE) > 32:
        _CONTENT_CACHE.clear()
    _CONTENT_CACHE[key] = box
    return box


def _sheet_geometry(item, page_index, options):
    """Where the page lands on the sheet, at true scale. None when it can't be
    worked out. Otherwise a dict of plain numbers, so warning() and preview()
    can call this on every refresh without dragging PyMuPDF onto the path for an
    image workflow:

      page       (w, h) of the sheet in points, already turned to its orientation
      printable  the margin box on the sheet
      box        where the drawing lands on the sheet
      clip       the part of the source that is kept, in its own units, or None
      ratio      the drawing scale, N in 1:N
      fits       whether the drawing lands inside the margins
      cropped    whether any of the drawing had to be cut off to make it

    What has to fit is the drawing, not the page it is on: the content found on
    the page plus everything measured on it. When only blank paper overflows,
    the page is trimmed round the drawing and that is not counted as a crop.

    The whole thing rests on one number: how many real millimetres a native unit
    of the source is worth. That comes from the 2D reference line, which is why
    this is 2D-only — under perspective there is no single such number, and a
    sheet that claimed 1:100 while being right on one plane and wrong everywhere
    else would be worse than no sheet at all."""
    size = SHEETS.get(str(options.get("sheet", SHEET_OFF)))
    if not size:
        return None
    ratio = _parse_ratio(options.get("sheet_scale", "1:100"))
    if not ratio:
        return None
    w, h, _unit = _page_size(item, page_index)
    shapes = _shapes_for(item, page_index, options)
    k = _scale_factor(shapes, w, h, options)      # chosen units per native unit
    if not k:
        return None
    unit_m = UNITS.get(str(options.get("unit", "m")), 1.0)
    mm_per_native = k * unit_m * 1000.0
    pt_per_native = mm_per_native / ratio * MM_PT
    iw, ih = w * pt_per_native, h * pt_per_native
    if not (iw > 0 and ih > 0 and math.isfinite(iw) and math.isfinite(ih)):
        return None
    # the drawing's own extent: what is on the page, and what is drawn over it
    ext = [_content_box(item, page_index)]
    for sh in _exportable(shapes):
        pts = _native(sh.get("points") or (), w, h)
        if pts:
            ext.append((min(p[0] for p in pts), min(p[1] for p in pts),
                        max(p[0] for p in pts), max(p[1] for p in pts)))
    ext = [b for b in ext if b]
    if ext:
        cb = (max(0.0, min(b[0] for b in ext)), max(0.0, min(b[1] for b in ext)),
              min(w, max(b[2] for b in ext)), min(h, max(b[3] for b in ext)))
    else:
        cb = (0.0, 0.0, w, h)
    dw, dh = (cb[2] - cb[0]) * pt_per_native, (cb[3] - cb[1]) * pt_per_native

    margin = max(0.0, float(options.get("sheet_margin", 10.0))) * MM_PT
    pw, ph = size[0] * MM_PT, size[1] * MM_PT
    want = str(options.get("sheet_orient", ORIENT_AUTO))

    def printable(sheet_w, sheet_h):
        return max(sheet_w - 2 * margin, 1.0), max(sheet_h - 2 * margin, 1.0)

    if want == ORIENT_LANDSCAPE:
        pw, ph = ph, pw
    elif want == ORIENT_AUTO:
        aw, ah = printable(pw, ph)
        bw, bh = printable(ph, pw)
        fits_p, fits_l = (dw <= aw and dh <= ah), (dw <= bw and dh <= bh)
        if fits_l and not fits_p:
            pw, ph = ph, pw
        elif not fits_p and not fits_l:
            # neither fits: pick the one that overflows least, so the overhang is
            # at least as small as it can be
            if max(dw / bw, dh / bh) < max(dw / aw, dh / ah):
                pw, ph = ph, pw
    cw, ch = printable(pw, ph)
    fits = iw <= cw + 1e-6 and ih <= ch + 1e-6
    printbox = ((pw - cw) / 2.0, (ph - ch) / 2.0, (pw + cw) / 2.0, (ph + ch) / 2.0)
    box = ((pw - iw) / 2.0, (ph - ih) / 2.0, (pw + iw) / 2.0, (ph + ih) / 2.0)
    clip = None
    cropped = False
    drawing_fits = dw <= cw + 1e-6 and dh <= ch + 1e-6
    if not fits and (drawing_fits or options.get("sheet_crop", False)):
        # Keep the scale and lose the edges rather than the other way round: a
        # sheet that has been cropped is still true to the ruler, a sheet that
        # has been shrunk to fit is not a scale drawing at all. The window kept
        # is centred on the drawing rather than the page, so when only blank
        # paper is too big, only blank paper goes.
        keep_w = min(w, cw / pt_per_native)
        keep_h = min(h, ch / pt_per_native)
        x0 = max(0.0, min(w - keep_w, (cb[0] + cb[2] - keep_w) / 2.0))
        y0 = max(0.0, min(h - keep_h, (cb[1] + cb[3] - keep_h) / 2.0))
        clip = (x0, y0, x0 + keep_w, y0 + keep_h)
        iw, ih = keep_w * pt_per_native, keep_h * pt_per_native
        box = ((pw - iw) / 2.0, (ph - ih) / 2.0, (pw + iw) / 2.0, (ph + ih) / 2.0)
        cropped = not drawing_fits
        fits = True
    return {"page": (pw, ph), "printable": printbox, "box": box, "clip": clip,
            "ratio": ratio, "fits": fits, "cropped": cropped,
            "pt_per_native": pt_per_native}


def _ratio_text(ratio):
    return "1:%s" % (("%.4f" % ratio).rstrip("0").rstrip("."))


def _sheet_preview(item, page_index, options):
    """The page layout drawn over the image itself, at the place everything will
    actually land: the paper edge, the margin, whatever a crop is about to
    remove, and the scale caption exactly where it prints.

    Everything is mapped from sheet points back into the image's own fractions
    through pt_per_native, so what you see over the photograph is the sheet, not
    a picture of a sheet.

    A sheet is nearly always bigger than the drawing on it, so this also asks
    the host for room around the page — the third return value, in page
    fractions — and the page shrinks to make space. Without that the whole
    layout falls outside the image and is clipped away, which is exactly what it
    did on the first attempt: nothing appeared at all."""
    geo = _sheet_geometry(item, page_index, options)
    if geo is None:
        return [], [], None
    w, h, _n = _page_size(item, page_index)
    ppn = geo.get("pt_per_native") or 0.0
    if ppn <= 0 or w <= 0 or h <= 0:
        return [], [], None
    bx = geo["box"]
    # `box` is where the *kept* part of the drawing lands, so when a crop is in
    # play the sheet's origin sits at the crop's top-left in the image, not at
    # the image's own. Leaving that out slid the whole overlay sideways by
    # exactly the amount that was being cropped off.
    ox = geo["clip"][0] if geo["clip"] else 0.0
    oy = geo["clip"][1] if geo["clip"] else 0.0
    paths, labels = [], []

    def to_frac(sx, sy):
        """A point on the sheet, in the image's own 0-1 coordinates."""
        return ((ox + (sx - bx[0]) / ppn) / w, (oy + (sy - bx[1]) / ppn) / h)

    def rect(box, colour, width, dash=False, fill=None, alpha=0.95):
        p0, p1 = to_frac(box[0], box[1]), to_frac(box[2], box[3])
        pts = [p0, (p1[0], p0[1]), p1, (p0[0], p1[1])]
        p = {"points": pts + [pts[0]], "color": colour, "width": width,
             "alpha": alpha, "dash": dash}
        if fill:
            p["fill"] = fill
            p["fill_alpha"] = 0.25
        paths.append(p)
        return p0, p1

    pw, ph = geo["page"]
    rect((0.0, 0.0, pw, ph), HORIZON, 2.0)                  # the paper edge
    pr = geo["printable"]
    _p0, p1 = rect(pr, HORIZON, 1.2, dash=True)             # the margin

    if geo["clip"] and geo["cropped"]:
        # what a crop keeps. (A clip that only trims blank paper isn't shown:
        # the paper edge already says where the sheet ends.) Shading the rest is the clearest way to say "this
        # is going, that is staying" without hiding the photograph underneath.
        c = geo["clip"]
        k0, k1 = (c[0] / w, c[1] / h), (c[2] / w, c[3] / h)
        for band in ((0.0, 0.0, 1.0, k0[1]), (0.0, k1[1], 1.0, 1.0),
                     (0.0, k0[1], k0[0], k1[1]), (k1[0], k0[1], 1.0, k1[1])):
            if band[2] - band[0] > 1e-6 and band[3] - band[1] > 1e-6:
                pts = [(band[0], band[1]), (band[2], band[1]),
                       (band[2], band[3]), (band[0], band[3])]
                paths.append({"points": pts + [pts[0]], "color": "#000000",
                              "width": 0.0, "alpha": 0.0, "closed": True,
                              "fill": "#000000", "fill_alpha": 0.45})
        pts = [k0, (k1[0], k0[1]), k1, (k0[0], k1[1])]
        paths.append({"points": pts + [pts[0]], "color": SCALE_COLOR,
                      "width": 2.0, "alpha": 0.95})

    if options.get("sheet_caption", True):
        # bottom right of the margin box, the same corner and the same size the
        # export puts it at, so the preview is the sheet and not an impression
        labels.append({"x": p1[0], "y": p1[1] + 0.02, "role": "sheet",
                       "color": HORIZON, "anchor": "right",
                       "text": [_ratio_text(geo["ratio"])]})

    # how much room the sheet needs beyond the page, per side, plus a little
    e0, e1 = to_frac(0.0, 0.0), to_frac(pw, ph)
    outset = (max(0.0, -e0[0]) + 0.04, max(0.0, -e0[1]) + 0.04,
              max(0.0, e1[0] - 1.0) + 0.04, max(0.0, e1[1] - 1.0) + 0.04)
    return paths, labels, outset


def _sheet_pages(item, options):
    """Which source pages get a sheet: the ones with something drawn on them,
    the same set the ordinary export annotates. A page that has a scale line but
    no measurements still counts — laying a drawing out at scale is a reason on
    its own. Pages with no scale are dropped later, in _process_sheet, because
    only then is it known whether one can be worked out."""
    data = (options.get("shapes") or {}).get(str(item)) or {}
    out = []
    for p, shapes in data.items():
        if _exportable(shapes or []):
            out.append(int(p))
    return sorted(out)


def _place_source(page, box, item, src, page_index, clip):
    """Put the source drawing on the sheet. True when something landed.

    Images go through PIL rather than straight into insert_image(filename=...).
    That path is at the mercy of whatever the file happens to be — a CMYK JPEG,
    a 16-bit PNG, a palette image with transparency — and when it declines it
    does so quietly, leaving a sheet with the measurements floating on nothing.
    Re-encoding through PIL normalises all of that, and gives the crop for free.
    """
    import fitz
    if src is not None:
        try:
            page.show_pdf_page(box, src, page_index,
                               clip=fitz.Rect(*clip) if clip else None)
            return True
        except Exception:
            import traceback
            traceback.print_exc()
            return False
    import io
    from PIL import Image
    try:
        with Image.open(item) as im:
            im.load()
            alpha = im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if alpha else "RGB")
            if clip:
                im = im.crop((int(round(clip[0])), int(round(clip[1])),
                              int(round(clip[2])), int(round(clip[3]))))
            buf = io.BytesIO()
            if alpha:
                im.save(buf, format="PNG")
            else:
                im.save(buf, format="JPEG", quality=92, subsampling=0)
        page.insert_image(box, stream=buf.getvalue())
        return True
    except Exception:
        import traceback
        traceback.print_exc()
    try:                                   # last resort: let PyMuPDF try itself
        page.insert_image(box, filename=str(item))
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        return False


def _process_sheet(item, output_dir, options, progress=None):
    """Write a PDF of the source laid out at a true drawing scale.

    Always a PDF, whatever went in — that is what "auto-convert to PDF" means
    here — and always a new file: overwriting a photograph with a sheet of paper
    isn't a thing anyone wants."""
    import fitz
    pages = _sheet_pages(item, options)
    if not pages:
        return []
    is_pdf = item.suffix.lower() == ".pdf"
    src = doc = None
    total = max(len(pages), 1)
    try:
        src = fitz.open(item) if is_pdf else None
        doc = fitz.open()
        for n, page_index in enumerate(pages, start=1):
            geo = _sheet_geometry(item, page_index, options)
            # no scale on this page means no true-scale sheet for it. Skipping
            # is the only honest option, but keep the progress bar moving so a
            # skipped page doesn't look like a stall.
            if geo is None or (is_pdf and not (0 <= page_index < src.page_count)):
                if progress:
                    progress(n, total)
                continue
            pw, ph = geo["page"]
            box = fitz.Rect(*geo["box"])
            ratio = geo["ratio"]
            page = doc.new_page(width=pw, height=ph)
            if not _place_source(page, box, item, src, page_index, geo["clip"]):
                # a sheet with the measurements on it and no drawing underneath
                # is worse than no sheet: it looks like a successful export
                raise RuntimeError("could not place the source on the sheet")
            shapes = _exportable(
                (options.get("shapes") or {}).get(str(item), {}).get(page_index)
                or (options.get("shapes") or {}).get(str(item), {}).get(str(page_index))
                or [])
            labels = measurements(item, page_index, options)
            # the drawing is placed in `box`, so that rect is the coordinate
            # space the 0–1 fractions map into, not the page
            _draw_on_page(page, shapes, labels, options, box.width, box.height,
                          org=box.tl, derot=fitz.Identity)
            if options.get("sheet_caption", True):
                try:
                    text = _ratio_text(ratio)
                    # the same size as the measurement labels: it is one of the
                    # read-outs, not a footnote
                    size = max(6.0, float(options.get("label_size", 14)))
                    try:
                        tw = fitz.get_text_length(text, fontname="helv", fontsize=size)
                    except Exception:
                        tw = size * len(text) * 0.55
                    # bottom right, inside the margin: where a title block goes
                    x1, y1 = geo["printable"][2], geo["printable"][3]
                    page.insert_text(fitz.Point(max(2.0, x1 - tw), min(ph - 3.0, y1 + size)),
                                     text, fontsize=size, fontname="helv",
                                     color=(0.2, 0.2, 0.2))
                except Exception:
                    pass
            if progress:
                progress(n, total)
        if doc.page_count == 0:
            return []
        out_dir = Path(output_dir)
        # the host only creates the output folder when it isn't overwriting, and
        # a sheet is always a new file whatever the overwrite setting says
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{item.stem}.pdf"
        if target.resolve() == item.resolve():
            target = out_dir / f"{item.stem} (sheet).pdf"
        doc.save(str(target))
    finally:
        if doc is not None:
            doc.close()
        if src is not None:
            src.close()
    return [str(target)]


def _draw_on_image(img, shapes, labels, options, w, h):
    """Annotate an RGBA image in place.

    Everything is drawn onto a transparent layer and composited at the end
    rather than straight onto the image. ImageDraw's "RGBA" blend mode only
    applies when the target is RGB — on an RGBA image it silently switches to
    replacing pixels, so a translucent polygon fill came out as a flat block of
    near-transparent colour instead of a tint, and the export didn't match the
    preview."""
    from PIL import Image, ImageDraw, ImageFont
    lw, fs = _style(options, w, h, False)
    lw = max(1, int(round(lw)))
    fs = int(round(fs))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = None
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "C:\\Windows\\Fonts\\arial.ttf"):
        try:
            font = ImageFont.truetype(name, fs)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    for s in _exportable(shapes):
        pts = _native(s.get("points") or (), w, h)
        if not pts:
            continue
        rgb = tuple(int(c * 255) for c in _hex_rgb(ROLE_COLOR.get(s.get("role"), DIST_COLOR)))
        shape = s.get("shape", "line")
        if shape == "polygon" and len(pts) >= 3:
            draw.polygon(pts, fill=rgb + (34,))
            draw.line(list(pts) + [pts[0]], fill=rgb + (255,), width=lw, joint="curve")
        elif len(pts) >= 2:
            draw.line(pts, fill=rgb + (255,), width=lw, joint="curve")
        if shape == "angle" and len(pts) >= 3:
            a, v, b = pts[0], pts[1], pts[2]
            rad = fs * 1.6
            a1 = math.degrees(math.atan2(a[1] - v[1], a[0] - v[0]))
            a2 = math.degrees(math.atan2(b[1] - v[1], b[0] - v[0]))
            sweep = (a2 - a1 + 180) % 360 - 180
            start, end = (a1, a1 + sweep) if sweep >= 0 else (a1 + sweep, a1)
            draw.arc([v[0] - rad, v[1] - rad, v[0] + rad, v[1] + rad], start, end,
                     fill=rgb + (255,), width=max(1, lw - 1))
        vr = max(2, lw + 1)
        for (px, py) in pts:
            draw.ellipse([px - vr, py - vr, px + vr, py + vr],
                         fill=(255, 255, 255, 235), outline=rgb + (255,), width=max(1, lw - 1))

    if not options.get("burn_labels", True):
        img.paste(layer, (0, 0), layer)
        return
    pad = max(3, int(fs * 0.35))
    for lb in labels:
        text = _label_str(lb)
        if not text:
            continue
        rgb = tuple(int(c * 255) for c in _hex_rgb(lb.get("color"), (0.11, 0.44, 0.82)))
        x, y = lb["x"] * w, lb["y"] * h
        try:
            bx0, by0, bx1, by1 = draw.textbbox((0, 0), text, font=font)
        except Exception:
            bx0, by0, bx1, by1 = 0, 0, fs * len(text) * 0.55, fs
        tw, th = bx1 - bx0, by1 - by0
        x0, y0 = x - tw / 2 - pad, y - th / 2 - pad
        box = [x0, y0, x0 + tw + pad * 2, y0 + th + pad * 2]
        try:
            draw.rounded_rectangle(box, radius=pad + 1, fill=rgb + (235,))
        except Exception:
            draw.rectangle(box, fill=rgb + (235,))
        draw.text((x0 + pad - bx0, y0 + pad - by0), text, font=font, fill=(255, 255, 255, 255))
    img.paste(layer, (0, 0), layer)


def _draw_on_page(page, shapes, labels, options, w, h, org=None, derot=None):
    """`org` and `w`/`h` are the rectangle the 0–1 fractions map into. They
    default to the page itself; the paper layout passes the box the drawing was
    placed in instead, which is the whole difference between annotating a page
    and annotating a drawing sitting on a sheet."""
    import fitz
    lw, fs = _style(options, w, h, True)
    if derot is None:
        derot = page.derotation_matrix
    if org is None:
        org = page.rect.tl

    def P(pt):
        return fitz.Point(org.x + pt[0], org.y + pt[1]) * derot

    for s in _exportable(shapes):
        pts = [P(p) for p in _native(s.get("points") or (), w, h)]
        if not pts:
            continue
        rgb = _hex_rgb(ROLE_COLOR.get(s.get("role"), DIST_COLOR))
        shape = s.get("shape", "line")
        if shape == "polygon" and len(pts) >= 3:
            page.draw_polyline(pts + [pts[0]], color=rgb, fill=rgb, width=lw,
                               fill_opacity=0.13)
        elif len(pts) >= 2:
            page.draw_polyline(pts, color=rgb, width=lw)
        if shape == "angle" and len(pts) >= 3:
            a, v, b = pts[0], pts[1], pts[2]
            rad = fs * 1.6
            a1 = math.atan2(a.y - v.y, a.x - v.x)
            a2 = math.atan2(b.y - v.y, b.x - v.x)
            sweep = math.degrees((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
            start = fitz.Point(v.x + rad * math.cos(a1), v.y + rad * math.sin(a1))
            try:
                page.draw_sector(v, start, sweep, color=rgb, width=lw * 0.8,
                                 fullSector=False)
            except Exception:
                pass
        for p in pts:
            page.draw_circle(p, lw + 1.2, color=rgb, fill=(1, 1, 1), width=lw * 0.7)

    if not options.get("burn_labels", True):
        return
    pad = fs * 0.35
    rot = page.rotation
    for lb in labels:
        text = _label_str(lb)
        if not text:
            continue
        rgb = _hex_rgb(lb.get("color"), (0.11, 0.44, 0.82))
        cx, cy = org.x + lb["x"] * w, org.y + lb["y"] * h
        try:
            tw = fitz.get_text_length(text, fontname="helv", fontsize=fs)
        except Exception:
            tw = fs * len(text) * 0.55
        # build the pill upright in display space, then map it into the page's
        # own (unrotated) space so it stays level on a rotated page
        rect = (fitz.Rect(cx - tw / 2 - pad, cy - fs * 0.72 - pad,
                          cx + tw / 2 + pad, cy + fs * 0.72 + pad) * derot).normalize()
        try:
            page.draw_rect(rect, color=None, fill=rgb, radius=0.25, fill_opacity=0.92)
        except TypeError:            # radius needs PyMuPDF ≥ 1.22
            page.draw_rect(rect, color=None, fill=rgb, fill_opacity=0.92)
        placed = -1
        try:
            # insert_textbox returns < 0 (and writes nothing) if the text can't
            # fit, so its result has to be checked rather than trusted
            placed = page.insert_textbox(rect, text, fontsize=fs, fontname="helv",
                                         color=(1, 1, 1), align=1, rotate=rot)
        except Exception:
            placed = -1
        if placed < 0:
            page.insert_text(fitz.Point(rect.x0 + pad, rect.y0 + pad + fs * 0.78),
                             text, fontsize=fs, fontname="helv", color=(1, 1, 1))


def process(item, output_dir, options, overwrite, progress=None):
    item = Path(item)
    # laying out on paper replaces the ordinary export: it produces a PDF at a
    # true drawing scale rather than an annotated copy of the original
    if (str(options.get("mode", MODE_2D)) == MODE_2D
            and SHEETS.get(str(options.get("sheet", SHEET_OFF)))):
        return _process_sheet(item, output_dir, options, progress)
    data = (options.get("shapes") or {}).get(str(item)) or {}
    pages = {}
    for p, shapes in data.items():
        keep = _exportable(shapes or [])
        if keep:
            pages[int(p)] = keep
    if not pages:
        return []

    if item.suffix.lower() == ".pdf":
        import fitz
        doc = fitz.open(item)
        total = max(len(pages), 1)
        for n, (page_index, shapes) in enumerate(sorted(pages.items()), start=1):
            if 0 <= page_index < doc.page_count:
                page = doc[page_index]
                w, h = float(page.rect.width), float(page.rect.height)
                labels = measurements(item, page_index, options)
                _draw_on_page(page, shapes, labels, options, w, h)
            if progress:
                progress(n, total)
        if overwrite:
            doc.save(str(item), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            target = item
        else:
            target = Path(output_dir) / f"{item.stem}.pdf"
            doc.save(str(target))
        doc.close()
        return [str(target)]

    from PIL import Image
    shapes = pages.get(0) or next(iter(pages.values()))
    with Image.open(item) as src:
        src.load()
        opaque = not (src.mode in ("RGBA", "LA") or
                      (src.mode == "P" and "transparency" in src.info))
        img = src.convert("RGBA")
    w, h = float(img.width), float(img.height)
    _draw_on_image(img, shapes, measurements(item, 0, options), options, w, h)
    target = item if overwrite else Path(output_dir) / f"{item.stem}{item.suffix}"
    if item.suffix.lower() in (".jpg", ".jpeg") or opaque:
        img = img.convert("RGB")
    img.save(target)
    if progress:
        progress(1, 1)
    return [str(target)]
