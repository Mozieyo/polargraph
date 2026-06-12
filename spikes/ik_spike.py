"""IK-location decision spike (ARCHITECTURE.md Section 6).

Question: should the polar inverse kinematics run PC-side (we stream short
belt-length moves) or in grblHAL wall-plotter firmware (we stream Cartesian XY
and the MCU segments + converts)?

Both solve the *same* problem the same way: a straight line in XY is NOT a
straight line in belt-length space, so long moves must be chopped into short
chords. The residual error is how far the belt-space-interpolated path bows away
from the intended straight line within one chord. This spike measures that bow
vs. segment length across the whole draw area, and estimates the PC-side data
volume, so we choose from numbers instead of vibes.

Outputs:
  * an ASCII table to the console (cp949-safe), and
  * output/ik_spike.svg visualising intended vs. belt-interpolated paths.

Run: python spikes/ik_spike.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polargraph.geometry import MachineGeometry  # noqa: E402

D = 400.0                       # motor spacing (nominal)
GEO = MachineGeometry(D)
# Draw area (mm), matches profiles/machine.toml: x across, y down from anchors.
X_MIN, X_MAX = 50.0, 350.0
Y_MIN, Y_MAX = 60.0, 410.0


def line_bow(ax: float, ay: float, bx: float, by: float, samples: int = 17) -> float:
    """Max distance the belt-interpolated A->B path bows off the straight line.

    Linear interpolation in belt space (what grbl does between two points),
    forward-kinematics each sample back to XY, measure perpendicular distance to
    the intended straight chord. Returns the max over the chord (mm).
    """
    L1a, L2a = GEO.ik(ax, ay)
    L1b, L2b = GEO.ik(bx, by)
    seg_len = math.hypot(bx - ax, by - ay)
    if seg_len == 0.0:
        return 0.0
    worst = 0.0
    for i in range(samples + 1):
        t = i / samples
        x, y = GEO.fk(L1a + t * (L1b - L1a), L2a + t * (L2b - L2a))
        # perpendicular distance from (x,y) to infinite line A->B
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        worst = max(worst, abs(cross) / seg_len)
    return worst


def worst_bow_for_segment(S: float):
    """Scan chords of length S (4 orientations) across the area; return worst."""
    worst = 0.0
    where = (0.0, 0.0, 0)
    for mx in range(int(X_MIN) + 10, int(X_MAX) - 9, 20):
        for my in range(int(Y_MIN) + 10, int(Y_MAX) - 9, 20):
            for deg in (0, 45, 90, 135):
                th = math.radians(deg)
                hx, hy = (S / 2) * math.cos(th), (S / 2) * math.sin(th)
                bow = line_bow(mx - hx, my - hy, mx + hx, my + hy)
                if bow > worst:
                    worst, where = bow, (float(mx), float(my), deg)
    return worst, where


def fmt_um(mm: float) -> str:
    return f"{mm * 1000.0:8.2f}"


def main() -> int:
    print("IK spike  --  belt-space interpolation error vs. segment length")
    print(f"machine: D={D:.0f} mm, draw area x[{X_MIN:.0f},{X_MAX:.0f}] "
          f"y[{Y_MIN:.0f},{Y_MAX:.0f}] mm\n")

    # 1) Worst-case single long move (no segmentation) -- why we must segment.
    big = line_bow(80.0, 90.0, 320.0, 90.0)
    print(f"unsegmented 240 mm move near top (y=90): bow = {big:.2f} mm "
          f"({big * 1000:.0f} um)  <-- unacceptable, hence segmentation\n")

    # 2) Worst-case residual bow vs segment length, + PC-side data volume.
    print("segment   worst-bow   worst-bow   where (x,y,deg)        segs/   bytes/")
    print(" len mm    (mm)         (um)                              metre   metre")
    print("-" * 74)
    pen_width_um = 200.0  # Sakura Pigma Micron ~0.20 mm reference line
    rows = []
    for S in (4.0, 2.0, 1.0, 0.5, 0.25):
        worst, (wx, wy, wdeg) = worst_bow_for_segment(S)
        segs_per_m = 1000.0 / S
        bytes_per_m = segs_per_m * 22.0  # ~"G1 X-123.456 Y-654.321\n"
        rows.append((S, worst, segs_per_m, bytes_per_m))
        print(f"  {S:5.2f}   {worst:8.4f}   {fmt_um(worst)}   "
              f"({wx:5.0f},{wy:5.0f},{wdeg:3d})        {segs_per_m:6.0f}  "
              f"{bytes_per_m:7.0f}")
    print("-" * 74)
    print(f"(pen line width for scale: {pen_width_um:.0f} um)\n")

    # 3) PC-side streaming reality check at a chosen segment length.
    S = 1.0
    for path_m in (5, 20):
        segs = path_m * 1000.0 / S
        kb = segs * 22.0 / 1024.0
        # ~115200 8N1, derate to ~6 KB/s effective with ok round-trips
        stream_s = (segs * 22.0) / 6000.0
        print(f"PC-side @ S={S:.0f}mm, {path_m:2d} m of ink: {segs:6.0f} segments, "
              f"{kb:6.1f} KB, ~{stream_s:4.1f}s pure stream (overlaps motion)")
    print("grblHAL kinematics: over-the-wire is just XY endpoints (~KB total); "
          "the MCU does the same segmentation internally.\n")

    write_svg(os.path.join(os.path.dirname(__file__), "..", "output", "ik_spike.svg"))
    print("wrote output/ik_spike.svg (intended vs belt-interpolated paths)")
    return 0


def _belt_path(ax, ay, bx, by, n=60):
    L1a, L2a = GEO.ik(ax, ay)
    L1b, L2b = GEO.ik(bx, by)
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append(GEO.fk(L1a + t * (L1b - L1a), L2a + t * (L2b - L2a)))
    return pts


def _poly(pts) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)


def write_svg(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ax, ay, bx, by = 80.0, 90.0, 320.0, 90.0      # worst-case demo line
    one_move = _belt_path(ax, ay, bx, by)          # single move -> big bow
    # segmented at 1 mm -> negligible bow (overlays the straight line)
    seg = []
    n = int(math.hypot(bx - ax, by - ay) / 1.0)
    for i in range(n + 1):
        t = i / n
        seg.append((ax + t * (bx - ax), ay + t * (by - ay)))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="-30 -30 460 480" font-family="sans-serif">
<rect x="-30" y="-30" width="460" height="480" fill="#fafafa"/>
<rect x="0" y="0" width="{D:.0f}" height="410" fill="none" stroke="#ccc" stroke-dasharray="4 4"/>
<circle cx="0" cy="0" r="5" fill="#333"/><circle cx="{D:.0f}" cy="0" r="5" fill="#333"/>
<text x="0" y="-12" font-size="12" fill="#333">A1 (left motor)</text>
<text x="{D:.0f}" y="-12" font-size="12" fill="#333" text-anchor="end">A2 (right motor)</text>
<polyline points="{_poly(one_move)}" fill="none" stroke="#d62728" stroke-width="2"/>
<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="#2ca02c" stroke-width="2"/>
<polyline points="{_poly(seg)}" fill="none" stroke="#2ca02c" stroke-width="1" stroke-dasharray="2 2"/>
<text x="200" y="70" font-size="13" fill="#2ca02c" text-anchor="middle">intended straight line (also = segmented @1mm)</text>
<text x="200" y="135" font-size="13" fill="#d62728" text-anchor="middle">single belt-space move bows down to here</text>
</svg>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)


if __name__ == "__main__":
    raise SystemExit(main())
