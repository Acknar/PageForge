"""Measure distances, areas, perimeters and angles on an image or PDF page.

Two ways to set the scale.

**2D scale** — for flat, straight-on subjects (scans, plans, elevations). Draw
one "Set scale" line over something whose real length you know and type that
length. Everything else then reads out in real units.

**3D perspective** — for photographs taken at an angle. The "3D setup" mode
holds everything the perspective needs, and nothing else does.

*The perspective.* A gizmo of five yellow grab points: an origin, two axis arms,
and a vanishing handle riding on each arm. Put the origin on a corner in the
photo and swing the arms along the two edges running away from it. It opens
axonometric — every axis parallel, no convergence — and you add perspective by
pulling a vanishing handle *in* along its arm. Push it back out past the far end
and that axis goes parallel again. The vertical axis isn't yours to drag: it's
derived, so it stays perpendicular to the other two. The grid deforms as you go;
when its lines run along the real edges in the photo, the perspective is right.

*The scale.* The orange rectangle lives in the same mode and is independent of
the gizmo. It already knows the perspective, so drag it onto a window, a door, a
paving slab — anything whose size you know — and it stays a true rectangle on
its plane while you match the corners up. Say which plane it's on, type its
width and height, and everything is in real units. Its two edges each measure
the same scale, so if they disagree the setup view says by how much: that number
is the honest measure of how good the rig is.

Two orthogonal vanishing points are all the camera needs — the focal length
falls out of them and the third axis is their cross product — so nothing here is
assumed about the shape of anything. With both axes parallel the camera is
orthographic instead and the third axis comes from the axonometric relation.
With only one axis converging there is nothing to solve the focal length from,
so an ordinary lens is assumed and the frame squared up against the axis that
does converge; the setup view says when that has happened.

A wide-angle or fisheye lens bends straight lines and will throw the solve off
no matter how carefully the rig is placed.

The gizmo, the rectangle, the grid and the axes are only drawn while the "3D
setup" mode is selected. They never appear in the other modes and are never
exported.

**Laying out on paper.** With a 2D scale set, "Lay out on paper" writes a PDF
with the drawing placed at a true scale — 1:100, 1:200, anything you type — on
the sheet size you pick, inside a printing margin, and converts an image to PDF
on the way. It is 2D only on purpose: a drawing scale is one
millimetres-per-pixel figure for the whole sheet, and a photograph taken at an
angle doesn't have one. One sheet per source page that carries measurements, and
always a new file.

The drawing belongs to the page it was drawn on, so a multi-page PDF needs its
own scale or rig on each page you measure.

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
              "angle": ANGLE_COLOR, "rig": RIG_COLOR, "srect": SCALE_COLOR}

SETUP_ROLES = ("rig", "srect")     # never measured, never exported
SETUP_GROUP = "3d"                 # both live together, under one mode

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
_ON_SCALE = [_IF_2D, {"key": _KIND, "eq": "scale"}]
_ON_SETUP = [_IF_3D, {"key": _KIND, "eq": "rig"}]
_ON_MEASURE_3D = [_IF_3D, {"key": _KIND, "in": ["distance", "area", "angle"]}]
_ON_AREA = {"key": _KIND, "eq": "area"}

PLANES = ("Ground", "Face along X", "Face along Y")
PLANE_AUTO = "Auto (from where it is drawn)"

# which plane the scale rectangle sits on
RECT_GROUND = "Ground (floor, table)"
RECT_WALL_X = "Face along X (wall)"
RECT_WALL_Y = "Face along Y (wall)"
RECT_PLANE = {RECT_GROUND: PLANES[0], RECT_WALL_X: PLANES[1], RECT_WALL_Y: PLANES[2]}

VIEW_INTERIOR = "Interior (inside a room)"
VIEW_EXTERIOR = "Exterior (facing a building)"

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

# the rig's grab points, in the order they're stored on the shape
RIG_ORIGIN, RIG_X, RIG_XV, RIG_Y, RIG_YV = range(5)

OPTIONS = [
    {"key": "mode", "label": "Mode", "type": "choice",
     "choices": [MODE_2D, MODE_3D], "default": MODE_2D},
    {"key": "shapes", "label": "Draw", "type": "shapes",
     "kinds": [
         {"id": "scale", "label": "Set scale", "shape": "line", "max": 1,
          "color": SCALE_COLOR, "visible_when": _IF_2D},
         {"id": "rig", "label": "3D setup", "shape": "handles", "max": 1,
          "color": RIG_COLOR, "setup": True, "group": SETUP_GROUP,
          "visible_when": _IF_3D,
          # origin, X tip, X vanishing point, Y tip, Y vanishing point.
          # Both vanishing handles start far out along their arm, which is what
          # makes the opening view axonometric. seed_shape() recomputes this
          # from the real page; the literal is only the last-resort fallback.
          "seed": [(0.50, 0.62), (0.76, 0.68), (0.97, 0.735),
                   (0.24, 0.68), (0.03, 0.735)]},
         # the scale rectangle has no button of its own: it belongs to the 3D
         # setup mode, which is the only place it means anything
         {"id": "srect", "label": "Scale rect", "shape": "polygon", "max": 1,
          "color": SCALE_COLOR, "setup": True, "group": SETUP_GROUP,
          "button": False, "visible_when": _IF_3D,
          "seed": [(0.42, 0.40), (0.58, 0.40), (0.58, 0.55), (0.42, 0.55)]},
         {"id": "distance", "label": "Distance", "shape": "line", "color": DIST_COLOR},
         {"id": "area", "label": "Area", "shape": "polygon", "color": AREA_COLOR},
         {"id": "angle", "label": "Angle", "shape": "angle", "color": ANGLE_COLOR},
     ],
     "hint": "Click to place points · drag for a straight line · double-click or "
             "right-click to finish · right-click a shape to delete it · "
             "hold Shift to snap to 0/45/90° · in 3D setup: origin on a corner, "
             "arms along its edges, pull a vanishing handle in along its arm to "
             "add perspective, then drag the orange rectangle onto something "
             "whose size you know"},

    {"key": "ref_length", "label": "Reference length", "type": "float",
     "default": 1.0, "min": 0.001, "max": 1000000.0, "visible_when": _ON_SCALE},

    {"key": "view", "label": "Looking at", "type": "choice",
     "choices": [VIEW_EXTERIOR, VIEW_INTERIOR], "default": VIEW_EXTERIOR,
     "visible_when": _ON_SETUP},
    {"key": "rect_on", "label": "Scale rect lies on", "type": "choice",
     "choices": [RECT_GROUND, RECT_WALL_X, RECT_WALL_Y], "default": RECT_WALL_Y,
     "visible_when": _ON_SETUP},
    {"key": "rect_w", "label": "Scale rect: width", "type": "float",
     "default": 1.0, "min": 0.001, "max": 1000000.0, "visible_when": _ON_SETUP},
    {"key": "rect_d", "label": "Scale rect: height", "type": "float",
     "default": 2.0, "min": 0.001, "max": 1000000.0, "visible_when": _ON_SETUP},
    {"key": "grid_step", "label": "Grid step (0 = auto)", "type": "float",
     "default": 0.0, "min": 0.0, "max": 1000000.0, "visible_when": _ON_SETUP},
    {"key": "grid_extent", "label": "Grid reach (× arm)", "type": "int",
     "default": 4, "min": 1, "max": 30, "visible_when": _ON_SETUP},
    {"key": "fov", "label": "Field of view ° (0 = solve)", "type": "float",
     "default": 0.0, "min": 0.0, "max": 179.0, "visible_when": _ON_SETUP},

    {"key": "plane", "label": "Measure on", "type": "choice",
     "choices": [PLANE_AUTO] + list(PLANES), "default": PLANE_AUTO,
     "visible_when": _ON_MEASURE_3D},

    {"key": "unit", "label": "Unit", "type": "choice",
     "choices": list(UNITS.keys()), "default": "m"},
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


# How far out along its axis a vanishing handle has to sit before that axis is
# treated as parallel, in units of the page's *short* side. Normalising on the
# short side rather than the diagonal is what makes the threshold reachable
# inside the page whatever the aspect ratio: a ray leaving the page centre at a
# shallow angle always has at least 0.5 short-sides of room, portrait or
# landscape. That reachability is what lets the opening state be a true
# axonometric instead of a slight, arbitrary perspective.
VP_PARALLEL = 0.42
# and the furthest a vanishing point is allowed to actually be, in the same
# units. Past this the axis is called parallel outright: at u just under the
# threshold the vanishing point runs to ~1e8 px, f² = −(v1·v2) becomes a
# difference of enormous magnitudes, and the read-out flickers between a solved
# field of view and a warning as the handle crosses.
VP_MAX = 40.0


def _vanishing(origin, tip, handle, ref):
    """One axis's vanishing point as a homogeneous 3-vector, in the same
    coordinates as the points given.

    The handle slides along the axis ray: near the origin the vanishing point is
    close and the axis converges hard; past VP_PARALLEL it goes to infinity and
    the axis stays parallel. The mapping d = u·k/(k−u) is chosen so that
    "parallel" is an exact, reachable position rather than a limit you can only
    approach."""
    dx, dy = tip[0] - origin[0], tip[1] - origin[1]
    n = math.hypot(dx, dy)
    if n < 1e-9 or ref <= 0:
        return None
    ux, uy = dx / n, dy / n
    # project the handle onto the ray, so one that has drifted off it still reads
    u = ((handle[0] - origin[0]) * ux + (handle[1] - origin[1]) * uy) / ref
    if u >= VP_PARALLEL - 1e-4:
        return (ux, uy, 0.0)                     # at infinity: a parallel axis
    u = max(u, 1e-3)
    d = u * VP_PARALLEL / (VP_PARALLEL - u)
    if d >= VP_MAX:
        return (ux, uy, 0.0)                     # too far out to be worth solving
    d *= ref
    # the vanishing point has to stay beyond the arm tip: pulled inside it, the
    # tip itself would sit past the line's point at infinity — behind the camera
    # — and the whole positive octant would be culled with no explanation
    d = max(d, n * 1.05)
    return (origin[0] + ux * d, origin[1] + uy * d, 1.0)


def _focal_from_vps(v1, v2, ref, fallback):
    """Focal length from two orthogonal vanishing points, assuming the principal
    point is the image centre, square pixels and no skew: in centred coordinates
    two finite orthogonal vanishing points satisfy v1·v2 = −f².

    Only finite vanishing points carry the information — an axis that stays
    parallel says nothing about the focal length, which is exactly why a purely
    axonometric rig has none and falls back to an assumed field of view."""
    if v1 is None or v2 is None or v1[2] == 0.0 or v2[2] == 0.0:
        return fallback, False
    f2 = -(v1[0] * v2[0] + v1[1] * v2[1])
    if f2 <= 0:
        return fallback, False
    f = math.sqrt(f2)
    # tested against the image diagonal, not its width: the same lens on a
    # portrait page would otherwise be rejected for being too long
    if not (0.15 * ref <= f <= 20.0 * ref):     # outside any real lens
        return fallback, False
    return f, True


def _convex(quad):
    """True when the four corners are in order around a convex quadrilateral —
    i.e. the user hasn't dragged one across another into a bowtie."""
    n = len(quad)
    if n != 4:
        return False
    sign = 0
    for i in range(n):
        ax, ay = quad[i]
        bx, by = quad[(i + 1) % n]
        cx, cy = quad[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-9:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return sign != 0


_RIG_CACHE = {}


def rig_points(item, page_index, options):
    """The rig's five grab points in native units, or None."""
    w, h, _unit = _page_size(item, page_index)
    for s in _shapes_for(item, page_index, options):
        if s.get("role") == "rig" and len(s.get("points") or ()) >= 5:
            return _native(s["points"][:5], w, h)
    return None


def _scale_quad(item, page_index, options):
    """The scale rectangle's four corners in native units, or None."""
    w, h, _unit = _page_size(item, page_index)
    for s in _shapes_for(item, page_index, options):
        if s.get("role") == "srect" and len(s.get("points") or ()) >= 4:
            return _native(s["points"][:4], w, h)
    return None


def rig(item, page_index, options):
    """Solve the camera from the vanishing-point gizmo.

    Two orthogonal vanishing points and a centred principal point are enough for
    a complete rectilinear camera: v1·v2 = −f² gives the focal length, each axis
    direction is K⁻¹v normalised, and the third axis is their cross product — so
    the frame is orthogonal by construction rather than by assumption, and the
    third axis needs no input at all. The plane homographies then come straight
    out of the vanishing vectors and the origin, and because a vanishing point at
    infinity is just a direction with a zero third coordinate, a parallel axis
    needs no special case: axonometric is simply where every axis is parallel.

    Everything here is in world units of "one unit of camera translation", which
    is a real but arbitrary length. The scale rectangle is what converts it to
    the chosen unit — see _metric_scale.

    Returns None without a usable rig. Otherwise a dict:

      H        ground (X, Y) → image, in centred image coordinates
      vert     image column per unit of height, so world (X, Y, Z) images at
               H·(X,Y,1) + Z·vert
      planes / inv   the three plane homographies, and the invertible ones
      f        focal length in px    exact   whether it was solved or assumed
      squared  whether the frame is orthogonal by solve or by projection
      centre   (cx, cy)              size    (w, h) of the page
      depth    sign of the homogeneous depth meaning "in front of the camera"
      octant   (±1, ±1) — which way the grids spread. Always (1, 1) now: the
               view toggle mirrors the arms themselves instead
      arms     the three axis arm lengths in world units, for drawing
      parallel (bool, bool) — whether each of X, Y stayed parallel

    Cached: a pointer drag re-solves this on every motion event, twice a frame.
    """
    import numpy as np
    pts = rig_points(item, page_index, options)
    if pts is None:
        return None
    w, h, _unit = _page_size(item, page_index)
    # the view toggle moves the handles rather than the solve, so it is not part
    # of the cache key: the points it changes already are
    key = (str(item), int(page_index), float(options.get("fov") or 0.0),
           tuple(round(v, 4) for p in pts for v in p))
    hit = _RIG_CACHE.get(key)
    if hit is not None:
        return hit

    cx, cy = w / 2.0, h / 2.0
    ref = min(w, h)
    diag = math.hypot(w, h)
    o = (pts[RIG_ORIGIN][0] - cx, pts[RIG_ORIGIN][1] - cy)
    cen = [(p[0] - cx, p[1] - cy) for p in pts]
    v1 = _vanishing(cen[RIG_ORIGIN], cen[RIG_X], cen[RIG_XV], ref)
    v2 = _vanishing(cen[RIG_ORIGIN], cen[RIG_Y], cen[RIG_YV], ref)
    if v1 is None or v2 is None:
        return None

    fov = float(options.get("fov") or 0.0)
    fallback = (w / 2.0) / math.tan(math.radians(50.0) / 2.0)
    solved_f, solved = _focal_from_vps(v1, v2, diag, fallback)
    if fov > 0:
        # a typed field of view fixes the lens but says nothing about whether the
        # two axes the user drew are actually perpendicular, so the frame still
        # has to be squared up afterwards
        f, exact, solved = (w / 2.0) / max(math.tan(math.radians(fov) / 2.0), 1e-6), True, False
    else:
        f, exact = solved_f, solved

    # axis directions in camera coordinates, and the image columns they imply.
    # col = K·d for a unit d, which works out to v / |K⁻¹v| — finite or not.
    def column(v):
        # K·d for the unit direction d = K⁻¹v/‖K⁻¹v‖ is simply v/‖K⁻¹v‖ —
        # the K and K⁻¹ cancel. Right for a finite vanishing point (v₂ = 1) and
        # for one at infinity (v₂ = 0) alike.
        kv = np.array([v[0] / f, v[1] / f, v[2]])
        n = np.linalg.norm(kv)
        if n < 1e-12:
            return None, None
        return np.array([v[0], v[1], v[2]], dtype=float) / n, kv / n

    origin = np.array([o[0], o[1], 1.0])
    affine = v1[2] == 0.0 and v2[2] == 0.0
    squared = True
    if affine:
        # Both axes parallel: the camera is orthographic, not perspective, and
        # the perspective construction below would fail badly here — with d1 and
        # d2 both lying in the z = 0 plane their cross product is the optical
        # axis itself, so the "vertical" would converge on the principal point.
        #
        # Solve the third axis properly instead. For an orthographic projection
        # of an orthonormal frame the 2×3 screen matrix is s·(the first two rows
        # of a rotation), whose columns a1, a2, a3 satisfy
        #     |a1x|²+|a2x|²+p² = |a1y|²+|a2y|²+q² = s²   and   a1·a1' + a2·a2' + pq = 0.
        # Writing z = p + iq those become z² = D − 2iC, so one complex square
        # root gives the missing axis outright (up to the sign the climb test
        # settles). Arm lengths matter here in a way they don't under
        # perspective: with no translation to set depth, they are the
        # foreshortening.
        a1 = np.array([cen[RIG_X][0] - o[0], cen[RIG_X][1] - o[1]])
        a2 = np.array([cen[RIG_Y][0] - o[0], cen[RIG_Y][1] - o[1]])
        cross = a1[0] * a2[1] - a1[1] * a2[0]
        if abs(cross) < 1e-6 * max(1.0, np.linalg.norm(a1) * np.linalg.norm(a2)):
            return None                     # arms collinear: no frame at all
        dd = (a1[1] ** 2 + a2[1] ** 2) - (a1[0] ** 2 + a2[0] ** 2)
        cc = a1[0] * a1[1] + a2[0] * a2[1]
        root = complex(dd, -2.0 * cc) ** 0.5
        c1 = np.array([a1[0], a1[1], 0.0])
        c2 = np.array([a2[0], a2[1], 0.0])
        c3 = np.array([root.real, root.imag, 0.0])
        f, exact = fallback, False
    else:
        c1, d1 = column(v1)
        c2, d2 = column(v2)
        if c1 is None or c2 is None:
            return None
        if not solved:
            # The focal length wasn't solved from the pair, so nothing has made
            # these two directions perpendicular — one axis parallel and the
            # other converging carries too little information to, and a typed
            # field of view doesn't try. Project one off the other rather than
            # leaving the frame skewed by whatever angle happened to be drawn.
            #
            # Never correct the parallel one. "Parallel" means the axis is
            # perpendicular to the optical axis, which is exact and is a state
            # the user parked a handle to reach; a finite axis's tilt out of the
            # image plane, by contrast, is entirely a function of the focal
            # length that just had to be assumed. Squaring against the parallel
            # axis also enforces a real constraint: with one axis perpendicular
            # to the view, the other's vanishing point has to lie along the
            # screen direction perpendicular to it. With both finite there is
            # nothing to choose between them, so the first stays put.
            move_second = not (v2[2] == 0.0 and v1[2] != 0.0)
            keep, move = (d1, d2) if move_second else (d2, d1)
            moved = move - float(np.dot(move, keep)) * keep
            n = np.linalg.norm(moved)
            if n < 1e-6:
                return None                 # the two axes are the same direction
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
        if n3 < 1e-3:        # |sin θ| between unit vectors: arms within ~0.06°
            return None      # of collinear give a numerically explosive frame
        d3 = d3 / n3
        c3 = np.array([f * d3[0], f * d3[1], d3[2]])
    # +Z must climb the image: d/dZ of image-y at the origin ∝ c3.y·Ô.z − Ô.y·c3.z
    if c3[1] * origin[2] - origin[1] * c3[2] > 0:
        c3 = -c3
    # The cube is always built along the arms, so an axis line always runs
    # through the grab point that defines it. The view toggle doesn't change
    # this: it mirrors the arms themselves (see on_option_changed), which moves
    # the handles and the cube together instead of pulling them apart.
    oct_x = oct_y = 1.0

    ground = np.column_stack([c1, c2, origin])
    planes = {
        PLANES[0]: ground,
        PLANES[1]: np.column_stack([c1, c3, origin]),
        PLANES[2]: np.column_stack([c2, c3, origin]),
    }
    inv = {}
    for name, P in planes.items():
        try:
            # relative, not absolute: |det| > 1e-14 on a matrix with entries in
            # the thousands is true of everything, including a plane seen exactly
            # edge-on, which then inverts to wild coordinates. Comparing against
            # the product of the column norms is comparing against the volume the
            # columns would span if they were orthogonal, which is scale-free.
            vol = float(np.linalg.norm(P[:, 0]) * np.linalg.norm(P[:, 1])
                        * np.linalg.norm(P[:, 2]))
            if vol > 0 and abs(float(np.linalg.det(P))) > 1e-6 * vol:
                inv[name] = np.linalg.inv(P)
        except Exception:
            pass
    if PLANES[0] not in inv:
        return None

    # arm lengths in world units: how far along each axis the user's tip sits.
    # Under the affine solve the arms *are* the world unit, so they're 1 by
    # construction and there's nothing to invert.
    if affine:
        arms = [1.0, 1.0, 1.0]
    else:
        arms = []
        for idx in (RIG_X, RIG_Y):
            q = _apply(inv[PLANES[0]], cen[idx], 1.0)
            arms.append(abs(q[0] if idx == RIG_X else q[1]) if q else 1.0)
        arms.append(max(arms))               # Z has no tip of its own

    out = {"H": ground, "vert": c3, "planes": planes, "inv": inv, "f": f,
           "exact": exact, "squared": squared, "centre": (cx, cy), "size": (w, h),
           # the origin's homogeneous coordinate is the literal 1.0 in every
           # branch, so "in front of the camera" is always the positive side.
           # Kept as a named value because every projection below tests against it.
           "depth": 1.0,
           "octant": (oct_x, oct_y), "arms": arms,
           # the grab points as drawn, in page units: _auto_plane needs to know
           # which side of the origin each arm runs to
           "origin_img": pts[RIG_ORIGIN],
           "tip_img": (pts[RIG_X], pts[RIG_Y]),
           "vh_img": (pts[RIG_XV], pts[RIG_YV]),
           # which way +Z leaves the origin on screen: the exact image tangent,
           # d(image)/dZ at the origin, which is what tells a wall from a floor
           "up_img": (float(c3[0] * origin[2] - origin[0] * c3[2]),
                      float(c3[1] * origin[2] - origin[1] * c3[2])),
           "parallel": (v1[2] == 0.0, v2[2] == 0.0)}
    if len(_RIG_CACHE) > 8:
        _RIG_CACHE.clear()
    _RIG_CACHE[key] = out
    return out


def _metric_scale(item, page_index, options, r):
    """World units → the chosen unit, from the scale rectangle.

    The rectangle's two edges give two independent readings of the same scale.
    Their disagreement is the single most useful number in the whole setup: it
    is zero exactly when the drawn quad really is a rectangle under the current
    perspective, so it says whether the rig is right, not just whether the
    numbers are typed in."""
    quad = _scale_quad(item, page_index, options)
    if quad is None or r is None:
        return None, None
    plane = RECT_PLANE.get(str(options.get("rect_on", RECT_WALL_Y)), PLANES[2])
    inv = r["inv"].get(plane)
    if inv is None:
        return None, None
    cx, cy = r["centre"]
    w = [_apply(inv, (p[0] - cx, p[1] - cy), r["depth"]) for p in quad]
    if any(p is None for p in w):
        return None, None
    e1 = (_seg_len(w[0], w[1]) + _seg_len(w[3], w[2])) / 2.0
    e2 = (_seg_len(w[0], w[3]) + _seg_len(w[1], w[2])) / 2.0
    rw = float(options.get("rect_w") or 0.0)
    rd = float(options.get("rect_d") or 0.0)
    if e1 < 1e-9 or e2 < 1e-9 or rw <= 0 or rd <= 0:
        return None, None
    k1, k2 = rw / e1, rd / e2
    k = (k1 + k2) / 2.0
    return k, abs(k1 - k2) / k if k > 0 else None


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
        self.k = k          # world units → the chosen unit, from the scale rect

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
        k, _mismatch = _metric_scale(item, page_index, options, r)
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
    in_3d = str(options.get("mode", MODE_2D)) == MODE_3D
    dec = int(options.get("decimals", 2))
    afac, aunit = _area_conversion(options, base.metric)
    if aunit is None:
        aunit = "%s²" % native
    pad = 0.022 * max(w, h)
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
            if in_3d:
                continue          # a 2D reference line means nothing under a rig
            ref = float(options.get("ref_length") or 0.0)
            body = txt(ref) if base.scale else "%s %s" % (
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
                n = len(pts)
                for i in range(n):
                    j = (i + 1) % n
                    x, y = _offset(pts[i], pts[j], w, h, pad, away_from=(cx, cy))
                    add(x, y, txt(_seg_len(world[i], world[j])), color, role)
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


def _plane_paths(r, plane_h, u_range, w_range, step, color_u, color_w, w, h,
                 samples=24):
    """Grid lines on one plane, drawn as two families of coloured polylines."""
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
            for seg in _project(plane_h, world, r["centre"], r["depth"], w, h):
                out.append({"points": seg, "color": color, "width": 1.0,
                            "alpha": 0.45})

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
    """Everything the 3D setup mode draws: the three plane grids, the cube in
    the positive octant, the axes and the horizon. Only ever called while that
    mode is active, and never exported."""
    import numpy as np
    r = rig(item, page_index, options)
    if r is None:
        # The grid and the axes are the only feedback the setup mode gives, so
        # dropping them silently is the worst possible failure: the mode looks
        # broken rather than misconfigured. Say what went wrong instead.
        return [], [{"x": 0.5, "y": 0.035, "role": "rig", "color": ANGLE_COLOR,
                     "text": ["the axes don't describe a camera: the arms are "
                              "too short, or too close to lying along each "
                              "other. Drag the origin and the two arm tips "
                              "apart"]}]
    w, h = r["size"]
    cx, cy = r["centre"]
    sign = r["depth"]
    sx, sy = r["octant"]
    ax, ay, az = r["arms"]

    # the grid step is typed in real units but drawn in world units, so it needs
    # the scale rectangle before it can be honoured; without one, fall back to
    # quarters of an arm, which is always drawable
    step = 0.0
    k, _mismatch = _metric_scale(item, page_index, options, r)
    typed = float(options.get("grid_step") or 0.0)
    if typed > 0 and k:
        step = typed / k
    if step <= 0:
        step = max(ax, ay) / 4.0
    reach = max(1, int(options.get("grid_extent", 4)))
    paths = []

    def spans(arm, s, n):
        """The world range a grid family covers, on the octant side or behind."""
        far = arm * n * s
        return (0.0, far) if far >= 0 else (far, 0.0)

    def plane_grid(plane, u_arm, w_arm, us, ws, cu, cw):
        P = r["planes"][plane]
        # everything, desaturated: this is the part of the plane behind the cube
        full_u = (-u_arm * reach, u_arm * reach)
        full_w = (-w_arm * reach, w_arm * reach)
        back = _plane_paths(r, P, full_u, full_w, step, HORIZON, HORIZON, w, h)
        for p in back:
            p["alpha"] = 0.16
        # then the positive octant on top, in the axis colours, so the near
        # faces of the cube read solid and the rest falls away behind them
        front = _plane_paths(r, P, spans(u_arm, us, reach), spans(w_arm, ws, reach),
                             step, cu, cw, w, h)
        return back + front

    paths += plane_grid(PLANES[0], ax, ay, sx, sy, AXIS_Y, AXIS_X)
    paths += plane_grid(PLANES[1], ax, az, sx, 1.0, AXIS_Z, AXIS_X)
    paths += plane_grid(PLANES[2], ay, az, sy, 1.0, AXIS_Z, AXIS_Y)

    def to_img(P, p):
        q = P @ np.array([p[0], p[1], 1.0])
        if abs(q[2]) < 1e-12 or q[2] * sign < 0:
            return None
        x, y = q[0] / q[2] + cx, q[1] / q[2] + cy
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        if abs(x) > 4.0 * w or abs(y) > 4.0 * h:
            return None          # heading for a vanishing point; not drawable
        return (x, y)

    def world(pt):
        """Image point of a full 3-D world point, via the ground plane plus Z."""
        q = r["H"] @ np.array([pt[0], pt[1], 1.0]) + r["vert"] * pt[2]
        if abs(q[2]) < 1e-12 or q[2] * sign < 0:
            return None
        x, y = q[0] / q[2] + cx, q[1] / q[2] + cy
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        if abs(x) > 4.0 * w or abs(y) > 4.0 * h:
            return None
        return (x / w, y / h)

    # the unit cube of one arm per side, on the side the view toggle chooses
    bx, by, bz = ax * sx, ay * sy, az
    corners = [(0, 0, 0), (bx, 0, 0), (bx, by, 0), (0, by, 0),
               (0, 0, bz), (bx, 0, bz), (bx, by, bz), (0, by, bz)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    for a, b in edges:
        pa, pb = world(corners[a]), world(corners[b])
        if pa and pb:
            paths.append({"points": [pa, pb], "color": RIG_COLOR, "width": 1.6,
                          "alpha": 0.75})

    # the three axes, thick, from the origin
    labels = []
    origin = to_img(r["H"], (0.0, 0.0))
    axes = [((bx, 0.0, 0.0), AXIS_X, "X"), ((0.0, by, 0.0), AXIS_Y, "Y"),
            ((0.0, 0.0, bz), AXIS_Z, "Z")]
    if origin is not None:
        off = 0.05 * max(w, h)
        # X and Y each carry a vanishing handle that rides further out along the
        # same ray than the arm tip does. The axis is drawn out to whichever is
        # further, so both grab points sit on the line they belong to instead of
        # the handle floating past its end.
        handles = list(r.get("vh_img") or ()) + [None]
        for axis_i, (target, color, text) in enumerate(axes):
            tip = world(target)
            if tip is None:
                continue
            tip = (tip[0] * w, tip[1] * h)
            end = tip
            vh = handles[axis_i] if axis_i < len(handles) else None
            if vh is not None:
                if (math.hypot(vh[0] - origin[0], vh[1] - origin[1])
                        > math.hypot(tip[0] - origin[0], tip[1] - origin[1])):
                    end = (vh[0], vh[1])
            paths.append({"points": [(origin[0] / w, origin[1] / h),
                                     (end[0] / w, end[1] / h)],
                          "color": color, "width": 3.0, "alpha": 0.95})
            # push the read-out past the end of the arm: an axis tip is also a
            # grab point, and a label sitting on it hides what you're aiming for
            dx, dy = tip[0] - origin[0], tip[1] - origin[1]
            arm_px = math.hypot(dx, dy) or 1.0
            labels.append({"x": (tip[0] + dx / arm_px * off) / w,
                           "y": (tip[1] + dy / arm_px * off) / h,
                           "text": text, "color": color, "role": "rig"})

    # Horizon: the vanishing line of the ground plane. Its vanishing points can
    # sit millions of pixels off-frame, so the line is taken from the third row
    # of H⁻¹ (points p with that row · p = 0 are the images of the ground's
    # points at infinity) and cut against the page edges.
    seg = _horizon_segment(r["H"], w, h, cx, cy)
    if seg:
        paths.append({"points": seg, "color": HORIZON, "width": 1.5,
                      "dash": True, "alpha": 0.8})

    labels += _setup_labels(item, page_index, options, r, w)
    return paths, labels


def _setup_labels(item, page_index, options, r, w):
    """The read-outs that tell you whether the setup is actually any good.

    Prose is split from numbers so the host can translate the words: it renders
    a list of segments by translating each one and joining them."""
    out = []
    px, py = r["parallel"]
    if px and py:
        note, tone = (["axonometric — pull a vanishing handle in along its arm "
                       "to add perspective"], HORIZON)
    elif px or py:
        note, tone = (["one axis is parallel, so there is nothing to solve the "
                       "lens from: the frame has been squared up against that "
                       "axis and the converging one swung round to suit. Give "
                       "both axes perspective for a solved camera"], SCALE_COLOR)
    elif not r["exact"]:
        note, tone = (["the two vanishing points don't describe a real lens, so "
                       "an ordinary one was assumed — check the arms are on "
                       "genuinely perpendicular edges"], ANGLE_COLOR)
    elif not r["squared"]:
        note, tone = (["field of view fixed at ",
                       "%.0f°" % (2 * math.degrees(math.atan((w / 2.0) / r["f"]))),
                       ", frame squared up against the first axis"], SCALE_COLOR)
    else:
        note, tone = (["field of view ",
                       "%.0f°" % (2 * math.degrees(math.atan((w / 2.0) / r["f"])))],
                      RIG_COLOR)
    out.append({"x": 0.5, "y": 0.035, "text": note, "color": tone, "role": "rig"})

    k, mismatch = _metric_scale(item, page_index, options, r)
    if k is None:
        out.append({"x": 0.5, "y": 0.085, "color": SCALE_COLOR, "role": "rig",
                    "text": ["drag the orange rectangle onto a known feature"]})
    elif mismatch is not None and mismatch > 0.02:
        # Each edge of the rectangle measures the same scale on its own. They
        # only agree when the shape's proportions on its plane match the width
        # and height typed in, so their disagreement is a direct read-out of how
        # far off those two numbers are.
        out.append({"x": 0.5, "y": 0.085, "color": ANGLE_COLOR, "role": "rig",
                    "text": ["scale rectangle is ", "%.0f%%" % (mismatch * 100),
                             " out of proportion - set the width and height "
                             "values"]})
    else:
        out.append({"x": 0.5, "y": 0.085, "color": SCALE_COLOR, "role": "rig",
                    "text": ["scale set, edges agree"]})
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
    return out


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
    if in_3d:
        r = rig(item, page, options)
        if r is None:
            return ("Open the 3D setup mode and line the axes up with the photo: "
                    "drag the origin to a corner, the two arms along the edges "
                    "running away from it.")
        quad = _scale_quad(item, page, options)
        if quad is not None and not _convex(quad):
            return ("The scale rectangle's corners have crossed over. Drag them "
                    "back into order around the shape.")
        k, mismatch = _metric_scale(item, page, options, r)
        if k is None:
            return ("Drag the orange rectangle onto something whose size you know "
                    "and type its width and height — that's what sets the scale.")
        if mismatch is not None and mismatch > 0.05:
            return ("The scale rectangle's proportions don't match the width and "
                    "height typed in, so the measurements will be off by about "
                    "as much. Set them to the real dimensions, or move the "
                    "corners to match.")
        if not measurable:
            return "Perspective is set — pick a measuring mode and click on the preview."
        return None
    if not measurable:
        return "Nothing drawn on this page yet — pick a mode and click on the preview."
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

    Exporting a 2D page with no reference line drawn is almost never what
    somebody meant: the numbers come out in pixels or points, and on a sheet
    there is no scale to lay the drawing out at, so nothing is produced at all.
    The amber warning says so, but a warning is easy to walk past."""
    if str(options.get("mode", MODE_2D)) != MODE_2D:
        return None
    files = context.get("files") or []
    if not files:
        return None
    item = files[min(int(context.get("index", 0)), len(files) - 1)]
    page = int(context.get("page_index", 0))
    shapes = _shapes_for(item, page, options)
    if not [s for s in shapes if s.get("role") not in SETUP_ROLES]:
        return None
    w, h, _n = _page_size(item, page)
    if _scale_factor(shapes, w, h, options) is not None:
        return None
    if SHEETS.get(str(options.get("sheet", SHEET_OFF))):
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
SEED_ARM = 0.26          # arm length, in short sides
SEED_HANDLE = 0.48       # vanishing handle distance, in short sides
SEED_TILT = 12.0         # degrees below horizontal the arms run at


def seed_shape(kind_id, options, context):
    """Opening geometry for a setup kind, computed from the page.

    Fixed screen fractions are only right for one aspect ratio: the same numbers
    that put a vanishing handle safely past the parallel threshold on a 3:2
    landscape put it inside the threshold on a square page, so the rig would
    open with a slight, arbitrary perspective instead of the axonometric it is
    supposed to. Both distances here are in short sides, which is the unit
    VP_PARALLEL is measured in, so the result is axonometric on any page."""
    w = float(context.get("page_w") or 0.0)
    h = float(context.get("page_h") or 0.0)
    if w <= 0 or h <= 0:
        return None
    if kind_id == "rig":
        seed = _rig_seed(w, h)
        # A rig placed after the toggle was flipped has to arrive on the side the
        # toggle says, or the control and the geometry start out disagreeing.
        # Mirrored for the exterior: the unmirrored seed puts both walls in front
        # of you, which is what standing inside a room looks like.
        if str(options.get("view", VIEW_EXTERIOR)) == VIEW_EXTERIOR:
            seed = _mirror_rig(seed)
        return seed
    if kind_id == "srect":
        return _srect_seed(w, h, options, context)
    return None


def _rig_seed(w, h):
    ref = min(w, h)
    ox, oy = 0.50 * w, 0.62 * h
    ca = math.cos(math.radians(SEED_TILT))
    sa = math.sin(math.radians(SEED_TILT))
    out = [(ox, oy)]
    for sign in (1.0, -1.0):                 # X to the right, Y to the left
        ux, uy = sign * ca, sa
        for dist in (SEED_ARM, SEED_HANDLE):
            out.append((ox + ux * dist * ref, oy + uy * dist * ref))
    # stored as origin, X tip, X handle, Y tip, Y handle
    return [(x / w, y / h) for x, y in out]


def _srect_seed(w, h, options, context):
    """The scale rectangle, centred on the middle of the face it is declared to
    lie on. Placing it on a face rather than flat on the screen means it arrives
    already in perspective, so lining it up with a window is a nudge rather than
    a reconstruction — and putting it on the *declared* face means the first
    constrained drag doesn't have to move it there."""
    import numpy as np
    files = context.get("files") or []
    idx = min(int(context.get("index", 0)), max(len(files) - 1, 0))
    fallback = [(0.42, 0.40), (0.58, 0.40), (0.58, 0.55), (0.42, 0.55)]
    if not files:
        return fallback
    r = rig(files[idx], int(context.get("page_index", 0)), options)
    if r is None:
        return fallback
    plane = RECT_PLANE.get(str(options.get("rect_on", RECT_WALL_Y)), PLANES[2])
    P = r["planes"].get(plane)
    if P is None:
        return fallback
    cx, cy = r["centre"]
    ax, ay, az = r["arms"]
    sx, sy = r["octant"]
    # the two axes each plane is spanned by, in the order rig() builds them, on
    # the side the view toggle put the cube
    if plane == PLANES[0]:
        spans = (ax * sx, ay * sy)           # ground: X then Y
    elif plane == PLANES[1]:
        spans = (ax * sx, az)                # X face: X then Z
    else:
        spans = (ay * sy, az)                # Y face: Y then Z
    u0, u1 = 0.25 * spans[0], 0.75 * spans[0]
    v0, v1 = 0.25 * spans[1], 0.75 * spans[1]
    out = []
    for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
        q = P @ np.array([u, v, 1.0])
        if abs(q[2]) < 1e-12 or q[2] * r["depth"] <= 0:
            return fallback
        fx, fy = (q[0] / q[2] + cx) / w, (q[1] / q[2] + cy) / h
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return fallback
        if not (-0.2 <= fx <= 1.2 and -0.2 <= fy <= 1.2):
            return fallback          # the Y face is off-screen; keep it grabbable
        out.append((min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))))
    return out


def on_option_changed(key, options, context):
    """Flipping the point of view mirrors the rig about its origin.

    The two arms mark the walls running away from the corner the origin sits on.
    Standing on the other side of that corner puts both walls on the other side
    of it too, so the honest thing is to move the arms — the grab points and the
    axes and the cube all together, which is the only arrangement where a handle
    is on the line it controls. Building the cube on the far side of unmoved arms
    was the same idea done to the drawing instead of to the geometry, and it left
    the two in different places."""
    if key != "view":
        return None
    files = context.get("files") or []
    if not files:
        return None
    item = files[min(int(context.get("index", 0)), len(files) - 1)]
    page = int(context.get("page_index", 0))
    data = options.get("shapes") or {}
    moved = False
    # every rig, not just the one on screen: the setting is one dropdown for the
    # whole tool, so leaving other pages on the old orientation would put the
    # control and the geometry permanently out of step
    for per_file in data.values():
        for shapes in (per_file or {}).values():
            for s in shapes or []:
                if s.get("role") != "rig":
                    continue
                pts = [tuple(p) for p in (s.get("points") or ())]
                if len(pts) < 5:
                    continue
                s["points"] = _mirror_rig(pts)
                moved = True
    return {"shapes": data} if moved else None


def _mirror_rig(pts):
    """The rig reflected through its own origin. Shortened along each ray where
    that would put a grab point off the page, since a handle you can't reach is
    worse than one slightly closer in than its mirror image."""
    o = pts[RIG_ORIGIN]
    return [o] + [_on_page(o, (2.0 * o[0] - p[0], 2.0 * o[1] - p[1]))
                  for p in pts[1:5]]


def on_shape_drag(role, vertex, points, original, options, context):
    """Keep the setup geometry honest while it's being dragged.

    A vanishing handle only means anything on its own axis, and the scale
    rectangle is only a rectangle if it stays one in the world rather than on
    the screen. Both are constraints the host can't know, so they're applied
    here and the corrected points go back."""
    if role == "rig":
        return _drag_rig(vertex, points, original)
    if role == "srect":
        return _drag_srect(vertex, points, original, options, context)
    return points


def _project_on_ray(origin, tip, handle):
    """The handle's nearest point on the ray from origin through tip, never
    behind the origin."""
    dx, dy = tip[0] - origin[0], tip[1] - origin[1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return handle
    ux, uy = dx / n, dy / n
    t = max(1e-4, (handle[0] - origin[0]) * ux + (handle[1] - origin[1]) * uy)
    return (origin[0] + ux * t, origin[1] + uy * t)


def _on_page(o, p):
    """`p` pulled back along the ray from `o` until it is inside the page.

    The host clamps every vertex to the page, and a clamp applied to each
    coordinate on its own would swing a vanishing handle off its axis — which is
    the one thing about it that has to stay true. Shortening along the ray keeps
    the direction and only weakens the convergence, which is at least visible in
    the grid."""
    dx, dy = p[0] - o[0], p[1] - o[1]
    t = 1.0
    for c, d in ((o[0], dx), (o[1], dy)):
        if d > 1e-12:
            t = min(t, (1.0 - c) / d)
        elif d < -1e-12:
            t = min(t, -c / d)
    t = max(0.0, min(1.0, t))
    return (o[0] + dx * t, o[1] + dy * t)


def _drag_rig(vertex, points, original):
    """Vanishing handles ride their own axis. Moving the origin takes the whole
    gizmo with it; moving an arm tip swings its vanishing handle round to follow,
    at the same distance out — the distance is what sets how hard the axis
    converges, so the perspective must not change when only the arm does."""
    p = [tuple(q) for q in points]
    if len(p) < 5 or len(original) < 5:
        return p                       # a truncated rig from a restored session
    if vertex is None:
        return p                       # whole-rig drag: everything moved together
    if vertex == RIG_ORIGIN:
        # Translate everything, but clamp the translation rather than the points:
        # the host rejects a correction outright when any point leaves the page,
        # so letting a handle wander off would freeze the origin instead of
        # sliding it along the edge.
        dx = p[RIG_ORIGIN][0] - original[RIG_ORIGIN][0]
        dy = p[RIG_ORIGIN][1] - original[RIG_ORIGIN][1]
        xs = [q[0] for q in original]
        ys = [q[1] for q in original]
        dx = max(-min(xs), min(dx, 1.0 - max(xs)))
        dy = max(-min(ys), min(dy, 1.0 - max(ys)))
        return [(q[0] + dx, q[1] + dy) for q in original]
    for tip, vh in ((RIG_X, RIG_XV), (RIG_Y, RIG_YV)):
        if vertex == vh:
            # projecting an on-page point onto the ray can still land off the
            # page when the ray leaves through a near edge, and the host would
            # then throw the whole drag away
            p[vh] = _on_page(p[RIG_ORIGIN],
                             _project_on_ray(p[RIG_ORIGIN], p[tip], p[vh]))
        elif vertex == tip:
            # hold the handle's distance from the origin, not its ratio to the
            # arm: _vanishing reads the absolute distance, so scaling it with the
            # arm would move the vanishing point every time the arm was resized
            o, t1 = original[RIG_ORIGIN], p[tip]
            hd = math.hypot(original[vh][0] - o[0], original[vh][1] - o[1])
            d1 = math.hypot(t1[0] - o[0], t1[1] - o[1])
            if d1 > 1e-9 and hd > 1e-9:
                ux, uy = (t1[0] - o[0]) / d1, (t1[1] - o[1]) / d1
                p[vh] = _on_page(o, (o[0] + ux * hd, o[1] + uy * hd))
    return p


def _drag_srect(vertex, points, original, options, context):
    """Hold the scale rectangle to a real rectangle on its plane. Dragging a
    corner slides the two edges that meet it; the opposite corner stays put. So
    you line the shape up with a window by its edges, and it can't stop being a
    rectangle while you do."""
    files = context.get("files") or []
    idx = min(context.get("index", 0), max(len(files) - 1, 0))
    if not files:
        return points
    item = files[idx]
    page = context.get("page_index", 0)
    r = rig(item, page, options)
    if r is None:
        return points
    plane = RECT_PLANE.get(str(options.get("rect_on", RECT_WALL_Y)), PLANES[2])
    inv, fwd = r["inv"].get(plane), r["planes"].get(plane)
    if inv is None or fwd is None:
        return points
    w, h = r["size"]
    cx, cy = r["centre"]

    def to_plane(fp):
        return _apply(inv, (fp[0] * w - cx, fp[1] * h - cy), r["depth"])

    def to_frac(q):
        import numpy as np
        v = fwd @ np.array([q[0], q[1], 1.0])
        if abs(v[2]) < 1e-12 or v[2] * r["depth"] < 0:
            return None
        fx, fy = (v[0] / v[2] + cx) / w, (v[1] / v[2] + cy) / h
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return None
        return (fx, fy)

    world = [to_plane(p) for p in points]
    if any(q is None for q in world):
        return points
    if vertex is None:                 # whole-shape drag: translate on the plane
        base = [to_plane(p) for p in original]
        if any(q is None for q in base):
            return points
        dx = world[0][0] - base[0][0]
        dy = world[0][1] - base[0][1]
        moved = [(b[0] + dx, b[1] + dy) for b in base]
    else:
        i = vertex % 4
        cur, opp = world[i], world[(i + 2) % 4]
        if abs(cur[0] - opp[0]) < 1e-9 or abs(cur[1] - opp[1]) < 1e-9:
            return points
        # Build the rectangle straight from the dragged corner and the one
        # opposite it, rather than sorting and then trying to work out which way
        # round it went: corner 0 shares its u with corner 3 and its v with
        # corner 1, which fixes the winding for whichever corner was grabbed.
        ua, ub = (cur[0], opp[0]) if i in (0, 3) else (opp[0], cur[0])
        va, vb = (cur[1], opp[1]) if i in (0, 1) else (opp[1], cur[1])
        moved = [(ua, va), (ub, va), (ub, vb), (ua, vb)]
    out = [to_frac(q) for q in moved]
    if any(q is None for q in out):
        return points
    return out


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
      fits       whether it all lands inside the margins
      cropped    whether anything had to be cut off to make it

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
    w, h, _native = _page_size(item, page_index)
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
        fits_p, fits_l = (iw <= aw and ih <= ah), (iw <= bw and ih <= bh)
        if fits_l and not fits_p:
            pw, ph = ph, pw
        elif not fits_p and not fits_l:
            # neither fits: pick the one that overflows least, so the overhang is
            # at least as small as it can be
            if max(iw / bw, ih / bh) < max(iw / aw, ih / ah):
                pw, ph = ph, pw
    cw, ch = printable(pw, ph)
    fits = iw <= cw + 1e-6 and ih <= ch + 1e-6
    printbox = ((pw - cw) / 2.0, (ph - ch) / 2.0, (pw + cw) / 2.0, (ph + ch) / 2.0)
    box = ((pw - iw) / 2.0, (ph - ih) / 2.0, (pw + iw) / 2.0, (ph + ih) / 2.0)
    clip = None
    cropped = False
    if not fits and options.get("sheet_crop", False):
        # Keep the scale and lose the edges rather than the other way round: a
        # sheet that has been cropped is still true to the ruler, a sheet that
        # has been shrunk to fit is not a scale drawing at all.
        keep_w = min(w, cw / pt_per_native)
        keep_h = min(h, ch / pt_per_native)
        clip = ((w - keep_w) / 2.0, (h - keep_h) / 2.0,
                (w + keep_w) / 2.0, (h + keep_h) / 2.0)
        iw, ih = keep_w * pt_per_native, keep_h * pt_per_native
        box = ((pw - iw) / 2.0, (ph - ih) / 2.0, (pw + iw) / 2.0, (ph + ih) / 2.0)
        cropped = keep_w < w - 1e-9 or keep_h < h - 1e-9
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

    if geo["clip"]:
        # what a crop keeps. Shading the rest is the clearest way to say "this
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
