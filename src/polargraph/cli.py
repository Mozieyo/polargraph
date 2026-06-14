"""polargraph CLI.

    polargraph gcode   drawing.svg  [-o out.gcode] [--no-optimize]
    polargraph verify  drawing.svg  [-o overlay.svg]
    polargraph stream  drawing.svg|out.gcode  [--port COM3] [--home X,Y]

`verify` is the no-hardware proof: it reports the worst SVG -> G-code -> XY error
and writes an overlay (green = input, red = reconstructed). `stream` runs a job on
the machine with character-counting flow control. ASCII output (cp949-safe).
"""

from __future__ import annotations

import argparse
import math
import os
import re
from pathlib import Path

from . import gcode as gc
from . import patterns
from . import sender
from .profile import Profile
from .segment import segment_polyline
from .svgio import read_svg

_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def _round_trip(layers, profile):
    geo = profile.geometry
    ox, oy = profile.paper_origin_mm
    worst = 0.0
    recon = []
    for layer in layers:
        for poly in layer["polylines"]:
            mpts = segment_polyline([(ox + x, oy + y) for x, y in poly], profile.segment_length_mm)
            rp = []
            for mx, my in mpts:
                l1, l2 = geo.ik(mx, my)
                rx, ry = geo.fk(round(l1, 3), round(l2, 3))
                worst = max(worst, ((rx - mx) ** 2 + (ry - my) ** 2) ** 0.5)
                rp.append((rx - ox, ry - oy))
            recon.append(rp)
    return worst, recon


def _compare_svg(path, layers, recon, profile):
    w, h = profile.paper_w_mm, profile.paper_h_mm

    def pl(pts):
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',
           f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fff" stroke="#ddd"/>']
    for layer in layers:
        for p in layer["polylines"]:
            out.append(f'<polyline points="{pl(p)}" fill="none" stroke="#2ca02c" stroke-width="0.4"/>')
    for rp in recon:
        out.append(f'<polyline points="{pl(rp)}" fill="none" stroke="#d62728" stroke-width="0.25" stroke-dasharray="1 1"/>')
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def _load_lines(path, profile, ignore_limits=False):
    if path.lower().endswith(".svg"):
        lines, _ = gc.generate(read_svg(path), profile, ignore_limits=ignore_limits)
        return lines
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def cmd_gcode(a):
    prof = Profile.load(a.profile)
    layers = read_svg(a.svg)
    lines, stats = gc.generate(layers, prof, optimize=not a.no_optimize,
                               ignore_limits=a.ignore_limits)
    out = a.out or os.path.splitext(a.svg)[0] + ".gcode"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    npoly = sum(len(layer["polylines"]) for layer in layers)
    print(f"wrote {out}")
    print(f"  {len(layers)} layer(s), {npoly} paths, {stats['segments']} segments")
    print(f"  draw {stats['draw_mm']:.0f} mm, travel {stats['travel_mm']:.0f} mm"
          + (f" (2-opt saved {stats['opt_saved_mm']:.0f} mm)" if stats.get('opt_saved_mm') else ""))
    print(f"  est {stats['est_min']:.1f} min (+ pen dwells)")


def cmd_verify(a):
    prof = Profile.load(a.profile)
    layers = read_svg(a.svg)
    worst, recon = _round_trip(layers, prof)
    out = a.out or os.path.splitext(a.svg)[0] + "_verify.svg"
    _compare_svg(out, layers, recon, prof)
    npoly = sum(len(layer["polylines"]) for layer in layers)
    print(f"verify: {len(layers)} layer(s), {npoly} paths")
    print(f"  max IK->gcode->XY error: {worst * 1000:.3f} um  (pen line ~200 um)")
    print(f"  overlay: {out}  (green=input, red=reconstructed)")


def cmd_stream(a):
    prof = Profile.load(a.profile)
    lines = _load_lines(a.input, prof, ignore_limits=a.ignore_limits)
    if getattr(a, "auto_home", False):  # endstop-home first, then draw in absolute coords
        prog = sender._program(lines)
        try:
            ser, port = sender.open_port(a.port)
        except Exception as e:  # noqa: BLE001
            print(e)
            raise SystemExit(3)
        log = print
        print(f"# port {port}: homing to endstops, then streaming {len(prog)} lines")
        with ser:
            sender.wake(ser, on_log=log)
            if not sender.home(ser, prof, on_log=log):
                print("homing failed - aborting (check [homing] direction/wiring)")
                raise SystemExit(4)
            raise SystemExit(sender._run(ser, prog, preamble=[], on_log=log))
    pre = []
    if not a.no_unlock:
        pre.append("$X")
    if a.home:
        hx, hy = (float(v) for v in a.home.replace(" ", "").split(","))
        ox, oy = prof.paper_origin_mm
        l1, l2 = prof.geometry.ik(ox + hx, oy + hy)
        pre.append(f"G92 X{l1:.3f} Y{l2:.3f}")
    elif not a.no_g92:
        t = gc.first_target(lines)
        if t:
            pre.append(f"G92 X{t[0]:.3f} Y{t[1]:.3f}")  # gentle: start where you are
    raise SystemExit(sender.stream(lines, port=a.port, preamble=pre))


def cmd_serve(a):
    from . import server
    server.run(http_port=a.http_port, serial_port=a.port, open_browser=a.open)


def cmd_calib(a):
    prof = Profile.load(a.profile)
    if a.grid:
        svg = patterns.calibration_grid_svg(prof.paper_w_mm, prof.paper_h_mm,
                                             extent_mm=a.square, cell_mm=a.cell)
        desc = f"{a.cell:.0f} mm grid over a {a.square:.0f} mm square"
    else:
        svg = patterns.calibration_svg(prof.paper_w_mm, prof.paper_h_mm,
                                       square_mm=a.square, rings=a.rings)
        desc = f"centered {a.square:.0f} mm square + {a.rings} rings + crosshair"
    out = a.out or "calibration.svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {out}  ({desc})")
    print(f"  next: plot it, measure the OUTER square's width and height, then:")
    print(f"        polargraph calib-solve --commanded {a.square:.0f} {a.square:.0f} "
          f"--measured <W> <H>")


def cmd_calib_solve(a):
    """Back-solve motor_spacing (sets the aspect) and steps/mm (sets the size) from a
    plotted rectangle measured on paper. Commanded size in, measured size out."""
    prof = Profile.load(a.profile)
    geo = prof.geometry
    M = prof.belt_steps_per_mm                 # current grbl $100/$101 (steps/mm)
    cx, cy = prof.center_xy
    wc, hc = a.commanded
    wm, hm = a.measured
    corners = [(cx - wc / 2, cy - hc / 2), (cx + wc / 2, cy - hc / 2),
               (cx + wc / 2, cy + hc / 2), (cx - wc / 2, cy + hc / 2)]
    cmd_belts = [geo.ik(*c) for c in corners]  # belts we command (model D)

    def sim(D, s):
        xs, ys = [], []
        for l1, l2 in cmd_belts:               # real machine: belts scaled by s, FK with real D
            L1, L2 = s * l1, s * l2
            x = (L1 * L1 - L2 * L2 + D * D) / (2 * D)
            xs.append(x)
            ys.append(math.sqrt(max(0.0, L1 * L1 - x * x)))
        return max(xs) - min(xs), max(ys) - min(ys)

    lo_d, hi_d, lo_s, hi_s = 150.0, 480.0, 0.6, 1.5
    best = (1e18, geo.motor_spacing_mm, 1.0)
    for _ in range(7):                         # coarse-to-fine grid search
        for di in range(41):
            D = lo_d + (hi_d - lo_d) * di / 40
            for si in range(41):
                s = lo_s + (hi_s - lo_s) * si / 40
                w, h = sim(D, s)
                e = (w - wm) ** 2 + (h - hm) ** 2
                if e < best[0]:
                    best = (e, D, s)
        _, D, s = best
        dd, ds = (hi_d - lo_d) / 40 * 1.5, (hi_s - lo_s) / 40 * 1.5
        lo_d, hi_d, lo_s, hi_s = D - dd, D + dd, s - ds, s + ds

    _, D, s = best
    new_spm = M / s
    w, h = sim(D, s)
    print("calibration solve:")
    print(f"  commanded {wc:.1f} x {hc:.1f} mm  ->  measured {wm:.1f} x {hm:.1f} mm")
    print(f"  fit residual: {best[0] ** 0.5:.2f} mm  (model now prints {w:.1f} x {h:.1f})")
    print("  --- apply these ---")
    print(f"  profiles/machine.toml  [geometry] motor_spacing_mm = {D:.1f}"
          f"   (was {geo.motor_spacing_mm:.1f})")
    print(f"  grbl board             $100={new_spm:.3f}  $101={new_spm:.3f}"
          f"   (was {M:.3f}; steps/mm sets absolute size)")
    if abs(s - 1.0) < 0.01:
        print("  (size was already accurate; only the aspect/motor_spacing needed fixing)")


def cmd_warp_fit(a):
    from . import warp as warpmod
    from .profile import resolve_profile
    prof = Profile.load(a.profile)
    try:
        w, st = warpmod.fit_from_scan(a.scan, prof.paper_w_mm, prof.paper_h_mm,
                                      extent_mm=a.extent, cell_mm=a.cell, dpi=a.dpi,
                                      step=a.step, smoothing=a.smoothing)
    except ImportError as e:
        raise SystemExit(f"warp-fit needs numpy, scipy and pymupdf: pip install numpy scipy pymupdf ({e})")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"warp fit failed: {e}")
    out = Path(a.out) if a.out else resolve_profile(a.profile).parent / "warp.json"
    out.write_text(w.to_json())
    print(f"fit {st['cells']} grid cells")
    print(f"  raw warp:     mean {st['warp_mean_mm']:.1f} mm, max {st['warp_max_mm']:.1f} mm")
    print(f"  TPS residual: mean {st['resid_mm']:.2f} mm, max {st['resid_max_mm']:.2f} mm")
    print(f"  wrote {out}  ({w.nx}x{w.ny} lattice @ {w.step:.0f}mm)")
    print("  warp is now ACTIVE for plots from this profile. Re-plot the grid to verify;")
    print("  run 'polargraph warp-clear' to remove it.")


def cmd_warp_clear(a):
    from .profile import resolve_profile
    p = resolve_profile(a.profile).parent / "warp.json"
    if p.exists():
        p.unlink()
        print(f"removed {p}  (warp correction off)")
    else:
        print("no warp.json found - nothing to clear")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="polargraph",
                                 description="SVG -> belt-length G-code for the polargraph")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, helptext in (("gcode", cmd_gcode, "convert an SVG to belt-length G-code"),
                               ("verify", cmd_verify, "no-hardware fidelity check + overlay SVG")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("svg")
        sp.add_argument("-o", "--out")
        sp.add_argument("--profile", default=None,
                        help="machine profile (default: ./profiles/machine.toml > ~/.polargraph > packaged)")
        if name == "gcode":
            sp.add_argument("--no-optimize", action="store_true", help="skip travel reordering")
            sp.add_argument("--ignore-limits", action="store_true",
                            help="bypass the safe-workspace (slack-belt) check")
        sp.set_defaults(func=fn)

    sp = sub.add_parser("stream", help="stream a .svg or .gcode to the machine")
    sp.add_argument("input")
    sp.add_argument("--port", help="serial port (default: auto-detect)")
    sp.add_argument("--home", help="paper-coords 'X,Y' (mm) of the gondola's parked point")
    sp.add_argument("--profile", default=None)
    sp.add_argument("--no-g92", action="store_true", help="don't set position (assume already homed)")
    sp.add_argument("--no-unlock", action="store_true", help="don't send $X first")
    sp.add_argument("--auto-home", action="store_true",
                    help="run the endstop homing cycle first, then draw in absolute coords")
    sp.add_argument("--ignore-limits", action="store_true",
                    help="bypass the safe-workspace (slack-belt) check")
    sp.set_defaults(func=cmd_stream)

    sp = sub.add_parser("serve", help="serve the Studio frontend with a PLOT button")
    sp.add_argument("--http-port", type=int, default=8770)
    sp.add_argument("--port", help="serial port (default: auto-detect)")
    sp.add_argument("--open", action="store_true", help="open the Studio in the default browser")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("calib", help="write a calibration pattern SVG (square+circles, or a grid)")
    sp.add_argument("-o", "--out")
    sp.add_argument("--square", type=float, default=150.0,
                    help="square side / grid extent in mm")
    sp.add_argument("--rings", type=int, default=4, help="number of concentric circles")
    sp.add_argument("--grid", action="store_true",
                    help="draw a grid of --cell squares (warp is visible per cell)")
    sp.add_argument("--cell", type=float, default=10.0, help="grid cell size in mm (with --grid)")
    sp.add_argument("--profile", default=None)
    sp.set_defaults(func=cmd_calib)

    sp = sub.add_parser("warp-fit",
                        help="fit a TPS distortion correction from a scanned calibration grid")
    sp.add_argument("--scan", required=True,
                    help="PDF/image of the plotted grid, cropped edge-to-edge to the paper")
    sp.add_argument("--extent", type=float, default=200.0, help="grid extent mm (match calib --square)")
    sp.add_argument("--cell", type=float, default=10.0, help="grid cell mm (match calib --cell)")
    sp.add_argument("--dpi", type=int, default=120, help="raster dpi for cell detection")
    sp.add_argument("--step", type=float, default=5.0, help="displacement lattice step mm")
    sp.add_argument("--smoothing", type=float, default=1.0,
                    help="TPS smoothing - raise to ignore friction jitter")
    sp.add_argument("-o", "--out", help="output warp.json (default: beside the profile)")
    sp.add_argument("--profile", default=None)
    sp.set_defaults(func=cmd_warp_fit)

    sp = sub.add_parser("warp-clear", help="remove the active warp correction (warp.json)")
    sp.add_argument("--profile", default=None)
    sp.set_defaults(func=cmd_warp_clear)

    sp = sub.add_parser("calib-solve",
                        help="solve motor_spacing + steps/mm from a measured plotted rectangle")
    sp.add_argument("--commanded", type=float, nargs=2, metavar=("W", "H"), required=True,
                    help="the rectangle size you plotted, mm (e.g. --commanded 150 150)")
    sp.add_argument("--measured", type=float, nargs=2, metavar=("W", "H"), required=True,
                    help="what you measured on paper, mm (e.g. --measured 150 178)")
    sp.add_argument("--profile", default=None)
    sp.set_defaults(func=cmd_calib_solve)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
