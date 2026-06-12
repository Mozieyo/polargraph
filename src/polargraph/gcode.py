"""Turn paper-space polylines into belt-length G-code for grblHAL.

Pipeline: paper mm -> place on the machine -> segment to short chords -> inverse
kinematics to belt lengths (X=L1 left, Y=L2 right) -> G-code with pen up/down and
per-segment feed compensation (belt-space velocity != pen velocity).

The output is absolute belt-length coordinates. At the machine you manual-home the
gondola to a known reference and `G92` grbl's position to that point's belt lengths;
then these absolute moves are correct. Stdlib only.
"""

from __future__ import annotations

import math
import re

from .profile import Profile
from .segment import segment_polyline

Point = tuple[float, float]


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _greedy_order(polys: list[list[Point]], start: Point = (0.0, 0.0)) -> list[list[Point]]:
    """Greedy nearest-neighbour ordering (with optional reversal) to cut pen-up travel."""
    remaining = list(polys)
    ordered: list[list[Point]] = []
    cur = start
    while remaining:
        bi, brev, bd = 0, False, float("inf")
        for i, p in enumerate(remaining):
            d0, d1 = _dist(cur, p[0]), _dist(cur, p[-1])
            if d0 < bd:
                bi, brev, bd = i, False, d0
            if d1 < bd:
                bi, brev, bd = i, True, d1
        p = remaining.pop(bi)
        if brev:
            p = p[::-1]
        ordered.append(p)
        cur = p[-1]
    return ordered


def _pen(lines: list[str], s: float, settle_ms: float) -> None:
    lines.append(f"M3 S{s:.0f}")
    lines.append(f"G4 P{settle_ms / 1000.0:.3f}")


def generate(layers: list[dict], profile: Profile, optimize: bool = True):
    """Return ``(gcode_lines, stats)``."""
    geo = profile.geometry
    ox, oy = profile.paper_origin_mm
    seg = profile.segment_length_mm

    L: list[str] = [
        "; PolarGraph G-code  (axes: X = left belt L1, Y = right belt L2, mm)",
        f"; D={geo.motor_spacing_mm:.2f}mm  belt_steps/mm={profile.belt_steps_per_mm:.4f}  segment={seg}mm",
        f"; paper {profile.paper_w_mm:.0f}x{profile.paper_h_mm:.0f} at origin "
        f"({ox:.1f},{oy:.1f})  draw_feed={profile.draw_feed_mm_min:.0f}mm/min",
        "; manual-home the gondola, then G92 X<L1> Y<L2> to its belt lengths before running",
        "G21",
        "G90",
    ]
    _pen(L, profile.pen_up_s, profile.pen_settle_ms)

    draw_mm = travel_mm = 0.0
    nseg = 0
    last: Point | None = None

    for layer in layers:
        polys = [[(ox + px, oy + py) for px, py in poly] for poly in layer["polylines"]]
        if optimize:
            polys = _greedy_order(polys, last or (ox, oy))
        if layer.get("stroke"):
            L.append(f"; --- pen layer: {layer['stroke']} ---")
        for poly in polys:
            pts = segment_polyline(poly, seg)
            if len(pts) < 2:
                continue
            if last is not None:
                travel_mm += _dist(last, pts[0])
            l1, l2 = geo.ik(*pts[0])
            L.append(f"G0 X{l1:.3f} Y{l2:.3f}")
            _pen(L, profile.pen_down_s, profile.pen_settle_ms)
            prev = pts[0]
            pl1, pl2 = l1, l2
            for p in pts[1:]:
                cart = _dist(prev, p)
                cl1, cl2 = geo.ik(*p)
                belt = math.hypot(cl1 - pl1, cl2 - pl2)
                f = profile.draw_feed_mm_min * (belt / cart) if cart > 1e-9 else profile.draw_feed_mm_min
                L.append(f"G1 X{cl1:.3f} Y{cl2:.3f} F{f:.0f}")
                draw_mm += cart
                nseg += 1
                prev, pl1, pl2 = p, cl1, cl2
            _pen(L, profile.pen_up_s, profile.pen_settle_ms)
            last = pts[-1]

    L.append("; end")
    est_min = (draw_mm / profile.draw_feed_mm_min
               + travel_mm / max(profile.travel_feed_mm_min, 1.0))
    stats = {"draw_mm": draw_mm, "travel_mm": travel_mm, "segments": nseg, "est_min": est_min}
    return L, stats


def first_target(gcode_lines) -> Point | None:
    """First (L1, L2) belt target in a program - for a gentle 'start here' G92."""
    l1 = l2 = None
    for ln in gcode_lines:
        ln = ln.split(";", 1)[0].strip()
        if not (ln.startswith("G0") or ln.startswith("G1")):
            continue
        mx = re.search(rf"X({_NUM})", ln)
        my = re.search(rf"Y({_NUM})", ln)
        if mx:
            l1 = float(mx.group(1))
        if my:
            l2 = float(my.group(1))
        if l1 is not None and l2 is not None:
            return (l1, l2)
    return None


def reconstruct(gcode_lines, geometry) -> list[Point]:
    """Forward-kinematics the G0/G1 belt coords back to machine XY (for preview/verify)."""
    xy: list[Point] = []
    l1 = l2 = None
    for ln in gcode_lines:
        ln = ln.split(";", 1)[0].strip()
        if not (ln.startswith("G0") or ln.startswith("G1")):
            continue
        mx = re.search(rf"X({_NUM})", ln)
        my = re.search(rf"Y({_NUM})", ln)
        if mx:
            l1 = float(mx.group(1))
        if my:
            l2 = float(my.group(1))
        if l1 is not None and l2 is not None:
            xy.append(geometry.fk(l1, l2))
    return xy


_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
