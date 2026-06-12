"""Geometry round-trip + steps/mm tests.

Runnable two ways so M0 verifies with zero install:
* ``python -m pytest tests/`` (once pytest is installed), or
* ``python tests/test_geometry.py`` (plain script — used now).
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polargraph.geometry import (  # noqa: E402
    GondolaGeometry,
    MachineGeometry,
    forward_kinematics,
    inverse_kinematics,
    steps_per_mm,
)

D = 400.0  # nominal motor spacing for the tests


def test_round_trip_over_draw_area() -> None:
    """xy → belts → xy must be identity across a realistic draw region.

    Sample a grid inside the ~300×410 mm aperture, offset below the anchors
    (y > 0). Max error must be sub-micron — far under any pen line width.
    """
    geo = MachineGeometry(motor_spacing_mm=D)
    worst = 0.0
    for x in range(50, 351, 25):          # 50..350 mm across
        for y in range(60, 411, 25):      # 60..410 mm down (below anchors)
            L1, L2 = geo.ik(float(x), float(y))
            xr, yr = geo.fk(L1, L2)
            worst = max(worst, math.hypot(xr - x, yr - y))
    assert worst < 1e-6, f"round-trip error too large: {worst:.3e} mm"


def test_ik_known_values() -> None:
    """Symmetric point straight below the midpoint: equal belts, 3-4-5 check."""
    # Midpoint x = 200; pick y so each belt is a clean number.
    # left belt = hypot(200, 150) = 250; right belt = hypot(-200, 150) = 250.
    L1, L2 = inverse_kinematics(200.0, 150.0, D)
    assert math.isclose(L1, 250.0, abs_tol=1e-9)
    assert math.isclose(L2, 250.0, abs_tol=1e-9)


def test_fk_unreachable_raises() -> None:
    """Belt lengths that can't span the anchors are rejected."""
    try:
        forward_kinematics(10.0, 10.0, D)  # 10 + 10 << 400
    except ValueError:
        return
    raise AssertionError("expected ValueError for unreachable pose")


def test_gondola_round_trip_and_offsets() -> None:
    """Gondola model: ik/fk are inverses; mounts land where geometry says."""
    g = GondolaGeometry(D, mount_spacing_mm=80.0, pen_drop_mm=27.0)
    worst = 0.0
    for x in range(60, 341, 40):
        for y in range(80, 401, 40):
            L1, L2 = g.ik(float(x), float(y))
            xr, yr = g.fk(L1, L2)
            worst = max(worst, math.hypot(xr - x, yr - y))
    assert worst < 1e-6, f"gondola round-trip error: {worst:.3e} mm"
    # explicit check: belts end at the mounts, not the pen.
    # pen at (200, 227) -> left mount (160, 200): L1 = hypot(160,200) ~ 256.125
    L1, L2 = g.ik(200.0, 227.0)
    assert math.isclose(L1, math.hypot(160, 200), abs_tol=1e-9)
    assert math.isclose(L2, math.hypot(160 - (D - 80), 200), abs_tol=1e-9)
    # symmetric point -> equal belts
    assert math.isclose(L1, L2, abs_tol=1e-9)


def test_steps_per_mm_pulley_options() -> None:
    """Resolves the project doc's 16T-vs-20T open question numerically."""
    assert math.isclose(steps_per_mm(20), 80.0)
    assert math.isclose(steps_per_mm(16), 100.0)


def _main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'OK' if not failures else 'FAILED'} - {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
