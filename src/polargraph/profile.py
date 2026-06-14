"""Load the machine profile (profiles/machine.toml) into a typed object.

Tolerant of missing keys (uses sane defaults) and of the older field names, so it
keeps working as the profile evolves. tomllib is stdlib (Python 3.11+).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .geometry import GondolaGeometry, MachineGeometry, steps_per_mm
from .warp import Warp

PACKAGED_DEFAULT = Path(__file__).resolve().parent / "data" / "machine.toml"


def resolve_profile(path: str | Path | None = None) -> Path:
    """Profile search order: explicit > ./profiles/machine.toml > ~/.polargraph/ > packaged default."""
    if path:
        return Path(path)
    for cand in (Path("profiles/machine.toml"),
                 Path.home() / ".polargraph" / "machine.toml"):
        if cand.exists():
            return cand
    return PACKAGED_DEFAULT


@dataclass(frozen=True)
class Homing:
    seek_mm: float          # max belt travel to hunt for the switch
    feed_mm_min: float      # homing jog speed
    pull_off_mm: float      # back off after the switch trips
    x_seek_sign: int        # jog sign that drives belt L1 toward its endstop
    y_seek_sign: int        # jog sign for L2
    home_xy: tuple[float, float]  # gondola machine-XY after homing settles


@dataclass(frozen=True)
class Profile:
    geometry: MachineGeometry | GondolaGeometry
    belt_steps_per_mm: float
    segment_length_mm: float
    draw_feed_mm_min: float
    travel_feed_mm_min: float
    pen_up_s: float
    pen_down_s: float
    pen_settle_ms: float
    paper_w_mm: float
    paper_h_mm: float
    paper_origin_mm: tuple[float, float]  # machine coords of the paper's top-left
    # safe workspace box in machine coords (slack-belt guard); None = unchecked
    safe_box: tuple[float, float, float, float] | None  # (x_min, y_min, x_max, y_max)
    homing: Homing | None                # endstop homing config; None = no homing
    warp: Warp | None                    # TPS distortion correction; None = off

    @property
    def center_xy(self) -> tuple[float, float]:
        """Machine coords of the paper centre - where 'home' parks the gondola."""
        ox, oy = self.paper_origin_mm
        return (ox + self.paper_w_mm / 2.0, oy + self.paper_h_mm / 2.0)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Profile":
        prof_path = resolve_profile(path)
        data = tomllib.loads(prof_path.read_text(encoding="utf-8"))
        geo = data.get("geometry", {})
        st = data.get("steppers", {})
        mo = data.get("motion", {})
        pen = data.get("pen", {})
        pa = data.get("paper", {})
        gon = data.get("gondola", {})
        lim = data.get("limits", {})

        D = float(geo.get("motor_spacing_mm", 400.0))
        mount = float(gon.get("mount_spacing_mm", 0.0))
        drop = float(gon.get("pen_drop_mm", 0.0))
        geometry = (GondolaGeometry(D, mount, drop) if (mount or drop)
                    else MachineGeometry(D))
        spm = steps_per_mm(
            int(st.get("pulley_teeth", 20)),
            int(st.get("microsteps", 16)),
            int(st.get("motor_full_steps_per_rev", 200)),
            float(st.get("belt_pitch_mm", 2.0)),
        )
        paper_w = float(pa.get("width_mm", 210.0))
        paper_h = float(pa.get("height_mm", 297.0))
        ox = pa.get("origin_x_mm")
        ox = (D - paper_w) / 2.0 if ox is None else float(ox)  # default: centered
        oy = float(pa.get("top_offset_mm", 120.0))

        # servo safety: clamp pen positions into [min_s, max_s] (runaway protection)
        s_lo = float(pen.get("min_s", 0))
        s_hi = float(pen.get("max_s", 1000))
        clamp_s = lambda v: min(max(float(v), s_lo), s_hi)  # noqa: E731

        safe = None
        if lim:
            safe = (float(lim.get("safe_x_min", -1e9)), float(lim.get("safe_y_min", -1e9)),
                    float(lim.get("safe_x_max", 1e9)), float(lim.get("safe_y_max", 1e9)))

        hm = data.get("homing", {})
        homing = None
        if hm:
            homing = Homing(
                seek_mm=float(hm.get("seek_mm", 360.0)),
                feed_mm_min=float(hm.get("feed_mm_min", 300.0)),
                pull_off_mm=float(hm.get("pull_off_mm", 4.0)),
                x_seek_sign=int(hm.get("x_seek_sign", -1)),
                y_seek_sign=int(hm.get("y_seek_sign", -1)),
                home_xy=(float(hm.get("home_x_mm", D / 2.0)),
                         float(hm.get("home_y_mm", oy))),
            )

        return cls(
            geometry=geometry,
            belt_steps_per_mm=spm,
            segment_length_mm=float(mo.get("segment_length_mm", 1.0)),
            draw_feed_mm_min=float(mo.get("draw_feed_mm_min", 800.0)),
            travel_feed_mm_min=float(mo.get("travel_feed_mm_min", 2500.0)),
            # accept new (up_s/down_s/settle_ms) or old (servo_up/.../dwell_*) names
            pen_up_s=clamp_s(pen.get("up_s", pen.get("servo_up", 350))),
            pen_down_s=clamp_s(pen.get("down_s", pen.get("servo_down", 600))),
            pen_settle_ms=float(pen.get("settle_ms", pen.get("dwell_after_down_ms", 150))),
            paper_w_mm=paper_w,
            paper_h_mm=paper_h,
            paper_origin_mm=(ox, oy),
            safe_box=safe,
            homing=homing,
            warp=Warp.load(prof_path.parent / "warp.json"),
        )
