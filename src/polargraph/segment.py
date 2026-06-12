"""Chord segmentation.

A straight line in Cartesian space is a *curve* in belt-length space, so long
moves must be split into short chords (<= segment length) to keep the belt-space
interpolation error tiny. See ARCHITECTURE.md S6 / the IK spike.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def segment_polyline(pts: list[Point], max_len: float) -> list[Point]:
    """Return a denser polyline whose every segment is <= ``max_len`` mm.

    Endpoints and original vertices are preserved; long edges get interior points
    added by linear interpolation.
    """
    if max_len <= 0:
        raise ValueError("max_len must be positive")
    if len(pts) < 2:
        return list(pts)
    out: list[Point] = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        n = max(1, math.ceil(dist / max_len))
        for i in range(1, n + 1):
            t = i / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out
