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
import time

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


def _travel_of(order: list[list[Point]], start: Point) -> float:
    """Total pen-up travel for an ordered, oriented list of polylines."""
    t = 0.0
    cur = start
    for p in order:
        t += _dist(cur, p[0])
        cur = p[-1]
    return t


def _two_opt(order: list[list[Point]], start: Point,
             max_n: int = 2500, time_budget: float = 2.5) -> list[list[Point]]:
    """Refine a path order with first-improvement 2-opt (reverses a block and flips
    each polyline's direction). The reversal's internal travel is unchanged, so each
    move's gain is an O(1) check on the two cut gaps. Bounded by ``max_n``/``time_budget``
    so dense art stays responsive; greedy order is the starting point."""
    n = len(order)
    if n < 4 or n > max_n:
        return order
    s = [p[0] for p in order]
    e = [p[-1] for p in order]

    def gap(a: Point, b: Point) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    deadline = time.time() + time_budget
    improved = True
    while improved and time.time() < deadline:
        improved = False
        for i in range(n - 1):
            pe = e[i - 1] if i > 0 else start   # end of the node before the block
            si = s[i]
            d_before_i = gap(pe, si)
            for j in range(i + 1, n):
                ej = e[j]
                if j + 1 < n:
                    sn = s[j + 1]
                    before = d_before_i + gap(ej, sn)
                    after = gap(pe, ej) + gap(si, sn)
                else:                            # block runs to the end: one cut only
                    before = d_before_i
                    after = gap(pe, ej)
                if after + 1e-9 < before:
                    order[i:j + 1] = [p[::-1] for p in reversed(order[i:j + 1])]
                    for k in range(i, j + 1):
                        s[k], e[k] = order[k][0], order[k][-1]
                    improved = True
                    break                        # restart the j-scan from this i
            if improved:
                break
    return order


def _pen(lines: list[str], s: float, settle_ms: float) -> None:
    lines.append(f"M3 S{s:.0f}")
    lines.append(f"G4 P{settle_ms / 1000.0:.3f}")


def _inside(p: Point, box) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _clip_seg(a: Point, b: Point, box):
    """Liang-Barsky: the inside portion ``(p, q)`` of segment a->b vs the box, or None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]),
                   (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if pp == 0:
            if qq < 0:
                return None
        else:
            r = qq / pp
            if pp < 0:
                if r > t1:
                    return None
                t0 = max(t0, r)
            else:
                if r < t0:
                    return None
                t1 = min(t1, r)
    return ((a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy))


def _clip_runs(pts: list[Point], box) -> list[list[Point]]:
    """Crop a polyline to the box: a list of inside sub-polylines, split where it
    crosses out of the no-go zone (endpoints interpolated to the boundary)."""
    runs: list[list[Point]] = []
    cur: list[Point] = []
    for a, b in zip(pts, pts[1:]):
        seg = _clip_seg(a, b, box)
        if seg is None:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
            continue
        pa, pb = seg
        if cur and abs(cur[-1][0] - pa[0]) < 1e-6 and abs(cur[-1][1] - pa[1]) < 1e-6:
            cur.append(pb)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [pa, pb]
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def generate(layers: list[dict], profile: Profile, optimize: bool = True,
             ignore_limits: bool = False):
    """Return ``(gcode_lines, stats)``.

    Raises ``ValueError`` if any point leaves the profile's safe box (the
    slack-belt danger zone near the workspace sides), unless ``ignore_limits``.
    """
    geo = profile.geometry
    ox, oy = profile.paper_origin_mm
    seg = profile.segment_length_mm
    warp = profile.warp  # TPS distortion pre-correction (paper coords), or None

    box = None if ignore_limits else profile.safe_box
    clipped_pts = 0  # points cropped out of the no-go zone

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

    draw_mm = travel_mm = opt_saved_mm = 0.0
    nseg = 0
    last: Point | None = None

    for layer in layers:
        polys = [[(ox + px, oy + py) for px, py in poly] for poly in layer["polylines"]]
        if optimize and polys:
            anchor = last or (ox, oy)
            polys = _greedy_order(polys, anchor)
            t_greedy = _travel_of(polys, anchor)
            polys = _two_opt(polys, anchor)
            opt_saved_mm += max(0.0, t_greedy - _travel_of(polys, anchor))
        if layer.get("stroke"):
            L.append(f"; --- pen layer: {layer['stroke']} ---")
        for poly in polys:
            pts = segment_polyline(poly, seg)
            if len(pts) < 2:
                continue
            if warp:  # pre-warp each command point (paper coords) so output lands true
                pts = [(ox + wx, oy + wy) for wx, wy in
                       (warp.apply(p[0] - ox, p[1] - oy) for p in pts)]
            if box:  # crop out any part in the no-go zone instead of failing
                clipped_pts += sum(1 for p in pts if not _inside(p, box))
                runs = _clip_runs(pts, box)
            else:
                runs = [pts]
            for run in runs:
                if last is not None:
                    travel_mm += _dist(last, run[0])
                l1, l2 = geo.ik(*run[0])
                L.append(f"G0 X{l1:.3f} Y{l2:.3f}")
                _pen(L, profile.pen_down_s, profile.pen_settle_ms)
                prev, pl1, pl2 = run[0], l1, l2
                for p in run[1:]:
                    cart = _dist(prev, p)
                    cl1, cl2 = geo.ik(*p)
                    belt = math.hypot(cl1 - pl1, cl2 - pl2)
                    f = profile.draw_feed_mm_min * (belt / cart) if cart > 1e-9 else profile.draw_feed_mm_min
                    L.append(f"G1 X{cl1:.3f} Y{cl2:.3f} F{f:.0f}")
                    draw_mm += cart
                    nseg += 1
                    prev, pl1, pl2 = p, cl1, cl2
                _pen(L, profile.pen_up_s, profile.pen_settle_ms)
                last = run[-1]

    L.append("; end")
    est_min = (draw_mm / profile.draw_feed_mm_min
               + travel_mm / max(profile.travel_feed_mm_min, 1.0))
    stats = {"draw_mm": draw_mm, "travel_mm": travel_mm, "segments": nseg,
             "est_min": est_min, "opt_saved_mm": opt_saved_mm, "clipped_pts": clipped_pts}
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
