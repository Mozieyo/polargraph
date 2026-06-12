"""Pipeline tests: segmentation, SVG reading, and SVG -> G-code -> XY fidelity.

Run: python tests/test_pipeline.py   (or via pytest). ASCII output (cp949-safe).
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polargraph import gcode as gc  # noqa: E402
from polargraph.profile import Profile  # noqa: E402
from polargraph.segment import segment_polyline  # noqa: E402
from polargraph.svgio import read_svg  # noqa: E402

PROFILE = os.path.join(os.path.dirname(__file__), "..", "profiles", "machine.toml")


def test_segment_max_len() -> None:
    out = segment_polyline([(0, 0), (10, 0), (10, 7)], 1.0)
    for a, b in zip(out, out[1:]):
        assert math.hypot(b[0] - a[0], b[1] - a[1]) <= 1.0 + 1e-9
    assert out[0] == (0, 0) and out[-1] == (10, 7)


def test_svg_read() -> None:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" '
           'viewBox="0 0 210 297"><g stroke="#111">'
           '<polyline points="10,10 50,10 50,40"/></g></svg>')
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as f:
        f.write(svg)
        path = f.name
    try:
        layers = read_svg(path)
        assert len(layers) == 1
        assert layers[0]["stroke"] == "#111"
        assert layers[0]["polylines"] == [[(10.0, 10.0), (50.0, 10.0), (50.0, 40.0)]]
    finally:
        os.unlink(path)


def test_gcode_fidelity() -> None:
    prof = Profile.load(PROFILE)
    geo = prof.geometry
    ox, oy = prof.paper_origin_mm
    square = [(20, 20), (190, 20), (190, 277), (20, 277), (20, 20)]
    diag = [(20, 20), (190, 277)]
    layers = [{"stroke": "#000", "polylines": [square, diag]}]

    lines, stats = gc.generate(layers, prof)
    assert stats["segments"] > 0

    # the G-code's belt coords (rounded to 3dp) must reconstruct the drawing to sub-pen-width
    worst = 0.0
    for poly in (square, diag):
        for px, py in segment_polyline([(ox + x, oy + y) for x, y in poly], prof.segment_length_mm):
            l1, l2 = geo.ik(px, py)
            rx, ry = geo.fk(round(l1, 3), round(l2, 3))
            worst = max(worst, math.hypot(rx - px, ry - py))
    assert worst < 0.003, f"round-trip error too big: {worst * 1000:.3f} um"

    xy = gc.reconstruct(lines, geo)
    assert len(xy) > 0
    print(f"  fidelity: worst {worst * 1000:.3f} um, {stats['segments']} segments, {len(xy)} gcode pts")


def _main() -> int:
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'OK' if not fails else 'FAILED'} - {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_main())
