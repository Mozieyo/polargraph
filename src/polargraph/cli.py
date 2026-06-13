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
import os
import re

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
    print(f"  draw {stats['draw_mm']:.0f} mm, travel {stats['travel_mm']:.0f} mm")
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
    svg = patterns.calibration_svg(prof.paper_w_mm, prof.paper_h_mm,
                                   square_mm=a.square, rings=a.rings)
    out = a.out or "calibration.svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {out}  (centered {a.square:.0f} mm square + {a.rings} rings + crosshair)")
    print(f"  next: polargraph gcode {out}  ->  plot  ->  measure the square vs "
          f"{a.square:.0f} mm to dial steps/mm")


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
    sp.add_argument("--ignore-limits", action="store_true",
                    help="bypass the safe-workspace (slack-belt) check")
    sp.set_defaults(func=cmd_stream)

    sp = sub.add_parser("serve", help="serve the Studio frontend with a PLOT button")
    sp.add_argument("--http-port", type=int, default=8770)
    sp.add_argument("--port", help="serial port (default: auto-detect)")
    sp.add_argument("--open", action="store_true", help="open the Studio in the default browser")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("calib", help="write a calibration pattern SVG (square + circles)")
    sp.add_argument("-o", "--out")
    sp.add_argument("--square", type=float, default=150.0, help="square side in mm")
    sp.add_argument("--rings", type=int, default=4, help="number of concentric circles")
    sp.add_argument("--profile", default=None)
    sp.set_defaults(func=cmd_calib)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
