"""Read plottable polylines (in mm) from an SVG.

Handles the Studio's own output (`<polyline>` inside per-pen `<g>` groups) plus
`<polygon>`, `<line>`, and straight-segment `<path>` (M/L/H/V/Z, abs + rel).
Paths containing curves (C/S/Q/T/A) are skipped for now (flatten upstream, e.g.
vpype, or use the Studio's polyline export). Stdlib only.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

Point = tuple[float, float]
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def _floats(s: str) -> list[float]:
    return [float(t) for t in re.findall(_NUM, s or "")]


def _pairs(s: str) -> list[Point]:
    f = _floats(s)
    return list(zip(f[0::2], f[1::2]))


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _unit_scale(root) -> float:
    """mm-per-user-unit, from width="NNmm" vs the viewBox width. Else 1.0."""
    w = root.get("width", "")
    vb = root.get("viewBox") or root.get("viewbox")
    m = re.match(rf"\s*({_NUM})\s*mm", w)
    if m and vb:
        f = _floats(vb)
        if len(f) >= 3 and f[2]:
            return float(m.group(1)) / f[2]
    return 1.0


def _path_polys(d: str) -> list[list[Point]]:
    if re.search(r"[CcSsQqTtAa]", d):  # curves: not flattened yet
        return []
    toks = re.findall(rf"[MmLlHhVvZz]|{_NUM}", d)
    polys: list[list[Point]] = []
    cur: list[Point] = []
    x = y = sx = sy = 0.0
    cmd = ""
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in "MmLlHhVvZz":
            cmd = t
            i += 1
            if cmd in "Zz" and cur:
                cur.append((sx, sy))
                polys.append(cur)
                cur = []
            continue
        if cmd in "Mm":
            nx, ny = float(toks[i]), float(toks[i + 1]); i += 2
            if cmd == "m":
                nx, ny = x + nx, y + ny
            if cur:
                polys.append(cur)
            cur = [(nx, ny)]
            x, y = sx, sy = nx, ny
            cmd = "l" if cmd == "m" else "L"  # subsequent pairs are implicit lineto
        elif cmd in "Ll":
            nx, ny = float(toks[i]), float(toks[i + 1]); i += 2
            if cmd == "l":
                nx, ny = x + nx, y + ny
            cur.append((nx, ny)); x, y = nx, ny
        elif cmd in "Hh":
            nx = float(toks[i]); i += 1
            if cmd == "h":
                nx += x
            cur.append((nx, y)); x = nx
        elif cmd in "Vv":
            ny = float(toks[i]); i += 1
            if cmd == "v":
                ny += y
            cur.append((x, ny)); y = ny
        else:
            i += 1
    if cur:
        polys.append(cur)
    return [p for p in polys if len(p) >= 2]


def _handle(el, layer) -> None:
    t = _tag(el)
    if t in ("polyline", "polygon"):
        pts = _pairs(el.get("points", ""))
        if t == "polygon" and pts:
            pts = pts + [pts[0]]
        if len(pts) >= 2:
            layer["polylines"].append(pts)
    elif t == "line":
        layer["polylines"].append([
            (float(el.get("x1", 0)), float(el.get("y1", 0))),
            (float(el.get("x2", 0)), float(el.get("y2", 0))),
        ])
    elif t == "path":
        layer["polylines"].extend(_path_polys(el.get("d", "")))


def read_svg(path) -> list[dict]:
    """Return ``[{'stroke': color|None, 'polylines': [[(x,y),...]]}]`` in mm."""
    root = ET.parse(path).getroot()
    scale = _unit_scale(root)
    layers: list[dict] = []
    default = {"stroke": None, "polylines": []}
    for el in root:
        if _tag(el) == "g":
            layer = {"stroke": el.get("stroke"), "polylines": []}
            for child in el:
                _handle(child, layer)
            if layer["polylines"]:
                layers.append(layer)
        else:
            _handle(el, default)
    if default["polylines"]:
        layers.append(default)
    if scale != 1.0:
        for layer in layers:
            layer["polylines"] = [[(x * scale, y * scale) for x, y in p]
                                  for p in layer["polylines"]]
    return layers
