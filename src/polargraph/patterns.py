"""Calibration / test patterns as SVG (flows straight through the pipeline).

The calibration plot is a centered square + concentric circles + a crosshair:

* the **square** is a known commanded size -> draw it, measure it, and the ratio
  tells you the steps/mm correction;
* the **circles** expose distortion -> if `D` or steps/mm are off they come out
  as ovals or shift off-centre;
* the **crosshair** marks the centre for registration.

Emitted as `<polyline>` (circles are pre-flattened) so `svgio` reads them directly.
"""

from __future__ import annotations

import math


def _poly(pts) -> str:
    return " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)


def calibration_svg(paper_w_mm: float, paper_h_mm: float, square_mm: float = 150.0,
                    rings: int = 4, segments: int = 120, stroke: str = "#111111") -> str:
    cx, cy = paper_w_mm / 2.0, paper_h_mm / 2.0
    h = square_mm / 2.0
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{paper_w_mm}mm" '
        f'height="{paper_h_mm}mm" viewBox="0 0 {paper_w_mm} {paper_h_mm}">',
        f'<!-- polargraph calibration: centered {square_mm}mm square, {rings} rings -->',
        f'<g stroke="{stroke}" fill="none" stroke-width="0.3">',
    ]
    # centered square
    sq = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h), (cx - h, cy - h)]
    out.append(f'<polyline points="{_poly(sq)}"/>')
    # concentric circles
    r_max = h * 0.92
    for k in range(1, rings + 1):
        r = r_max * k / rings
        pts = [(cx + r * math.cos(2 * math.pi * i / segments),
                cy + r * math.sin(2 * math.pi * i / segments)) for i in range(segments + 1)]
        out.append(f'<polyline points="{_poly(pts)}"/>')
    # crosshair through the centre
    a = h + 8.0
    out.append(f'<polyline points="{_poly([(cx - a, cy), (cx + a, cy)])}"/>')
    out.append(f'<polyline points="{_poly([(cx, cy - a), (cx, cy + a)])}"/>')
    out.append("</g></svg>")
    return "\n".join(out) + "\n"
