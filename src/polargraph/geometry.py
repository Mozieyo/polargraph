"""Polargraph geometry: the fixed coordinate math.

Two coordinate domains (see ARCHITECTURE.md §2):

* **Cartesian (paper) space** ``(x, y)`` in mm — where the pen is on the page.
* **Belt-length space** ``(L1, L2)`` in mm — the two belt lengths the motors drive.

Machine frame: origin at the left anchor ``A1 = (0, 0)``; right anchor
``A2 = (D, 0)``; ``x`` points right, ``y`` points **down** (the gondola hangs
below the anchors, so drawing points have ``y > 0``).

This module is independent of where the inverse kinematics actually *runs*
(PC-side vs. grblHAL wall-plotter firmware — the open decision in §6). The math
is identical either way; we need it on the PC regardless for preview,
calibration, and the IK spike. Stdlib-only on purpose: it runs with zero install.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "MachineGeometry",
    "GondolaGeometry",
    "inverse_kinematics",
    "forward_kinematics",
    "steps_per_mm",
]


def inverse_kinematics(x: float, y: float, motor_spacing_mm: float) -> tuple[float, float]:
    """Pen position ``(x, y)`` → belt lengths ``(L1, L2)`` in mm.

    ``L1`` is the distance to the left anchor ``(0, 0)``; ``L2`` to the right
    anchor ``(D, 0)``. Pure Euclidean distance — belt-as-rack means constant
    pitch radius and zero slip, so belt length *is* the pen-to-anchor distance.
    """
    L1 = math.hypot(x, y)
    L2 = math.hypot(x - motor_spacing_mm, y)
    return L1, L2


def forward_kinematics(L1: float, L2: float, motor_spacing_mm: float) -> tuple[float, float]:
    """Belt lengths ``(L1, L2)`` → pen position ``(x, y)`` in mm.

    The intersection of two circles (radii ``L1``/``L2``, centres at the
    anchors). Takes the lower intersection (``y > 0``) because the gondola hangs
    below the anchors. Used for preview and for verifying segmentation error
    without hardware.

    Raises ``ValueError`` if the belt lengths cannot reach a common point
    (triangle inequality violated) — i.e. an unreachable pose.
    """
    D = motor_spacing_mm
    x = (L1 * L1 - L2 * L2 + D * D) / (2.0 * D)
    y_squared = L1 * L1 - x * x
    if y_squared < -1e-6:
        raise ValueError(
            f"unreachable pose: L1={L1:.3f}, L2={L2:.3f}, D={D:.3f} "
            "(circles do not intersect)"
        )
    y = math.sqrt(max(0.0, y_squared))
    return x, y


def steps_per_mm(
    pulley_teeth: int,
    microsteps: int = 16,
    full_steps_per_rev: int = 200,
    belt_pitch_mm: float = 2.0,
) -> float:
    """Motor steps per mm of **belt** travel.

    ``(full_steps * microsteps) / (pulley_teeth * belt_pitch)``. For the default
    200-step motor at 1/16 µstep on 2 mm GT2 this is ``1600 / teeth`` —
    so **20T ⇒ 80**, **16T ⇒ 100** (resolves the 16T-vs-20T question in the
    project doc; verify the pulley before calibration).
    """
    return (full_steps_per_rev * microsteps) / (pulley_teeth * belt_pitch_mm)


@dataclass(frozen=True)
class MachineGeometry:
    """Belt-anchor geometry. ``motor_spacing_mm`` is ``D`` (calibrated)."""

    motor_spacing_mm: float

    def __post_init__(self) -> None:
        if self.motor_spacing_mm <= 0:
            raise ValueError("motor_spacing_mm must be positive")

    @property
    def left_anchor(self) -> tuple[float, float]:
        return (0.0, 0.0)

    @property
    def right_anchor(self) -> tuple[float, float]:
        return (self.motor_spacing_mm, 0.0)

    def ik(self, x: float, y: float) -> tuple[float, float]:
        """Pen ``(x, y)`` → belts ``(L1, L2)``."""
        return inverse_kinematics(x, y, self.motor_spacing_mm)

    def fk(self, L1: float, L2: float) -> tuple[float, float]:
        """Belts ``(L1, L2)`` → pen ``(x, y)``."""
        return forward_kinematics(L1, L2, self.motor_spacing_mm)


@dataclass(frozen=True)
class GondolaGeometry:
    """Two-point gondola, level-hang model.

    The belts don't meet at the pen: they attach at two mounts
    ``mount_spacing_mm`` apart, and the pen tip sits ``pen_drop_mm`` *below* the
    midpoint of the mount line. Assuming the gondola hangs level (no rotation),
    the left belt ends at ``(x - s/2, y - d)`` and the right at
    ``(x + s/2, y - d)`` — which is *exactly* the point model with the motor
    spacing reduced by ``s`` and the pen coordinates shifted by ``(s/2, d)``.
    (Residual error = actual gondola rotation; check the calibration circles.)
    """

    motor_spacing_mm: float
    mount_spacing_mm: float = 0.0
    pen_drop_mm: float = 0.0

    def __post_init__(self) -> None:
        if self.motor_spacing_mm - self.mount_spacing_mm <= 0:
            raise ValueError("mount_spacing_mm must be smaller than motor_spacing_mm")

    @property
    def _effective(self) -> MachineGeometry:
        return MachineGeometry(self.motor_spacing_mm - self.mount_spacing_mm)

    def ik(self, x: float, y: float) -> tuple[float, float]:
        """Pen ``(x, y)`` → belts ``(L1, L2)`` (to the mounts)."""
        return self._effective.ik(x - self.mount_spacing_mm / 2.0, y - self.pen_drop_mm)

    def fk(self, L1: float, L2: float) -> tuple[float, float]:
        """Belts ``(L1, L2)`` → pen ``(x, y)``."""
        x, y = self._effective.fk(L1, L2)
        return x + self.mount_spacing_mm / 2.0, y + self.pen_drop_mm
