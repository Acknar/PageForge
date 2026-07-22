"""Rename files: optional name list, remove existing numbering, find & replace,
lowercase, and space handling. (Prefix/suffix and new numbering live in Output.)"""
import re
import shutil
from pathlib import Path

NAME = "Rename"
ACCEPTS = ("image", "pdf")
ORDER = 100
BATCH = True
OPTIONS = [
    {"key": "list", "label": "Name list (.txt, optional)", "type": "file", "default": ""},
    {"key": "remove_numbering", "label": "Remove existing numbering", "type": "bool", "default": False},
    {"key": "find", "label": "Find", "type": "text", "default": ""},
    {"key": "replace", "label": "Replace with", "type": "text", "default": ""},
    {"key": "lowercase", "label": "Make lowercase", "type": "bool", "default": False},
    {"key": "spaces", "label": "Spaces", "type": "choice",
     "choices": ["keep", "\u2192 underscore _", "\u2192 hyphen -"], "default": "keep"},
]

# a run of digits with optional surrounding separators, anchored to start or end
_PREFIX_NUM = re.compile(r"^\s*[\(\[]?\s*\d+\s*[\)\]]?\s*[-_.)\]\s]*")
_SUFFIX_NUM = re.compile(r"[-_.\(\[\s]*[\(\[]?\s*\d+\s*[\)\]]?\s*$")


def _san(name):
    name = name.replace("/", "-").replace("\\", "-")
    name = name.replace("\u201c", "").replace("\u201d", "").replace('"', "")
    name = re.sub(r"[:*?<>|]", "", name)
    return re.sub(r"\s+", " ", name).strip().rstrip(".")


def _natkey(p):
    m = re.search(r"(\d+)", Path(p).name)
    return (int(m.group(1)) if m else 0, Path(p).name.lower())


def _names(path):
    if not path:
        return []
    return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _detect_numbering_side(stems):
    """Decide whether the batch is numbered as a prefix or a suffix (or neither)."""
    pre = sum(1 for s in stems if _PREFIX_NUM.match(s) and _PREFIX_NUM.match(s).end() < len(s))
    suf = sum(1 for s in stems if _SUFFIX_NUM.search(s) and _SUFFIX_NUM.search(s).start() > 0)
    if pre == 0 and suf == 0:
        return None
    return "prefix" if pre >= suf else "suffix"


def _strip_numbering(stem, side):
    if side == "prefix":
        out = _PREFIX_NUM.sub("", stem, count=1)
    elif side == "suffix":
        out = _SUFFIX_NUM.sub("", stem, count=1)
    else:
        return stem
    return out.strip() or stem


def _transform(base, options):
    find = options.get("find", "") or ""
    if find:
        base = base.replace(find, options.get("replace", "") or "")
    if options.get("lowercase"):
        base = base.lower()
    sp = options.get("spaces", "keep")
    if "underscore" in sp:
        base = base.replace(" ", "_")
    elif "hyphen" in sp:
        base = base.replace(" ", "-")
    return _san(base)


def _plan(files, options):
    names = _names(options.get("list"))
    items = sorted([Path(f) for f in files], key=_natkey)
    bases = [names[i] if i < len(names) else items[i].stem for i in range(len(items))]
    if options.get("remove_numbering"):
        side = _detect_numbering_side(bases)
        bases = [_strip_numbering(b, side) for b in bases]
    plan, used = {}, {}
    for src, base in zip(items, bases):
        stem = _transform(base, options) or src.stem
        key = stem.lower()
        if key in used:
            used[key] += 1
            stem = f"{stem} ({used[key]})"
        else:
            used[key] = 1
        plan[str(src)] = f"{stem}{src.suffix}"
    return plan


def preview(item, page_index, options, context):
    plan = _plan(context.get("files", []), options)
    return {"text": (Path(item).name, plan.get(str(item), Path(item).name))}


def process(items, output_dir, options, overwrite, progress=None):
    plan = _plan(items, options)
    out = []
    total = len(items)
    for i, f in enumerate(items, start=1):
        f = Path(f)
        new = plan.get(str(f))
        if new:
            dest = f.with_name(new) if overwrite else Path(output_dir) / new
            shutil.copy2(f, dest)
            out.append(str(dest))
        if progress:
            progress(i, total)
    return out
