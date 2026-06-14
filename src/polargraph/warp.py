"""Thin-plate-spline warp correction from a plotted calibration grid.

The V-plotter draws straight Cartesian lines (we IK per 1 mm), yet the physical
output still bows - belt sag, gondola geometry, an off reference. Rather than chase
each cause, we *measure* the distortion: plot the 1 cm grid, scan it, and fit a
thin-plate spline that maps where a point landed back to where it was commanded.
Pre-warping every toolpath point by that spline makes the output land true.

Fitting needs numpy+scipy+pymupdf (``warp-fit``); applying needs only the stdlib:
the spline is baked into a regular displacement lattice over the paper and sampled
with bilinear interpolation at plot time.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Warp:
    x0: float
    y0: float
    step: float
    nx: int
    ny: int
    dx: list[float]   # command-minus-desired, row-major (ny rows of nx)
    dy: list[float]
    n_points: int = 0  # control points used (for reporting)

    def apply(self, x: float, y: float) -> tuple[float, float]:
        """Map a desired paper-mm point to the command that draws it there."""
        fx = (x - self.x0) / self.step
        fy = (y - self.y0) / self.step
        i = min(max(int(math.floor(fx)), 0), self.nx - 2)
        j = min(max(int(math.floor(fy)), 0), self.ny - 2)
        tx = min(max(fx - i, 0.0), 1.0)
        ty = min(max(fy - j, 0.0), 1.0)
        nx = self.nx

        def s(arr):
            return (arr[j * nx + i] * (1 - tx) * (1 - ty)
                    + arr[j * nx + i + 1] * tx * (1 - ty)
                    + arr[(j + 1) * nx + i] * (1 - tx) * ty
                    + arr[(j + 1) * nx + i + 1] * tx * ty)

        return x + s(self.dx), y + s(self.dy)

    def to_json(self) -> str:
        return json.dumps({"x0": self.x0, "y0": self.y0, "step": self.step,
                           "nx": self.nx, "ny": self.ny, "dx": self.dx, "dy": self.dy,
                           "n_points": self.n_points})

    @classmethod
    def load(cls, path: str | Path) -> "Warp | None":
        p = Path(path)
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        return cls(d["x0"], d["y0"], d["step"], d["nx"], d["ny"],
                   d["dx"], d["dy"], d.get("n_points", 0))


def _grid_centers(paper_w, paper_h, extent_mm, cell_mm):
    """Ideal cell-centre paper coords - must match patterns.calibration_grid_svg."""
    extent = min(extent_mm, paper_w - 10.0, paper_h - 10.0)
    n = max(1, round(extent / cell_mm))
    extent = n * cell_mm
    x0 = paper_w / 2.0 - extent / 2.0
    y0 = paper_h / 2.0 - extent / 2.0
    centers, idx = [], []
    for i in range(n):           # row (top->bottom)
        for j in range(n):       # col (left->right)
            centers.append((x0 + (j + 0.5) * cell_mm, y0 + (i + 0.5) * cell_mm))
            idx.append((i, j))
    return centers, n


def fit_from_scan(pdf_path, paper_w, paper_h, extent_mm=200.0, cell_mm=10.0,
                  dpi=120, step=5.0, smoothing=1.0, edge_trim=12.0):
    """Detect grid cells in the scan, fit a TPS (observed -> commanded), and bake it
    into a displacement lattice. Returns ``(Warp, stats_dict)``. Needs numpy/scipy/fitz.
    """
    import fitz
    import numpy as np
    from scipy import ndimage
    from scipy.interpolate import RBFInterpolator

    pix = fitz.open(pdf_path)[0].get_pixmap(dpi=dpi)
    W, H, nch = pix.width, pix.height, pix.n
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(H, W, nch)[:, :, :3].mean(axis=2)
    px_x, px_y = W / paper_w, H / paper_h

    dark = img < (img.mean() * 0.78)
    cells = ~ndimage.binary_dilation(dark, iterations=1)
    lbl, ncomp = ndimage.label(cells)
    areas = ndimage.sum(np.ones_like(lbl), lbl, range(1, ncomp + 1))
    cell_px = (cell_mm * px_x) * (cell_mm * px_y)
    keep = [k + 1 for k, a in enumerate(areas) if 0.25 * cell_px < a < 2.5 * cell_px]
    coms = ndimage.center_of_mass(np.ones_like(lbl), lbl, keep)
    obs = np.array([(c / px_x, r / px_y) for r, c in coms])  # observed paper-mm

    ideal_list, n = _grid_centers(paper_w, paper_h, extent_mm, cell_mm)
    ideal = np.array(ideal_list)

    # rough per-axis align ideal->observed bbox, then nearest-neighbour assignment
    ob0, ob1 = obs.min(0), obs.max(0)
    id0, id1 = ideal.min(0), ideal.max(0)
    aligned = ob0 + (ideal - id0) / (id1 - id0) * (ob1 - ob0)
    pitch = cell_mm * (ob1[0] - ob0[0]) / (id1[0] - id0[0])
    tol = 0.55 * pitch
    src, tgt, used = [], [], set()
    for k, a in enumerate(aligned):
        d = np.hypot(obs[:, 0] - a[0], obs[:, 1] - a[1])
        m = int(np.argmin(d))
        if d[m] < tol and m not in used:
            used.add(m)
            src.append(obs[m])
            tgt.append(ideal[k])
    src, tgt = np.array(src), np.array(tgt)
    # drop the outermost ring of cells: edge detection is least reliable there and a
    # thin-plate spline develops wild gradients near the data boundary (it was
    # over-compressing the bottom rows). The lattice extrapolates smoothly past the trim.
    if edge_trim > 0 and len(tgt) > 12:
        lo, hi = tgt.min(0) + edge_trim, tgt.max(0) - edge_trim
        keepm = ((tgt[:, 0] >= lo[0]) & (tgt[:, 0] <= hi[0])
                 & (tgt[:, 1] >= lo[1]) & (tgt[:, 1] <= hi[1]))
        if keepm.sum() >= 12:
            src, tgt = src[keepm], tgt[keepm]
    if len(src) < 12:
        raise ValueError(f"only matched {len(src)} grid cells - check the scan/grid size")

    tps = RBFInterpolator(src, tgt, kernel="thin_plate_spline", smoothing=smoothing)
    resid = np.hypot(*(tps(src) - tgt).T)
    rawwarp = np.hypot(*(src - tgt).T)

    nx = int(paper_w // step) + 1
    ny = int(paper_h // step) + 1
    gx = np.arange(nx) * step
    gy = np.arange(ny) * step
    GX, GY = np.meshgrid(gx, gy)                     # desired paper points
    desired = np.column_stack([GX.ravel(), GY.ravel()])
    cmd = tps(desired)                              # command = TPS(desired)
    disp = cmd - desired
    warp = Warp(0.0, 0.0, float(step), nx, ny,
                disp[:, 0].tolist(), disp[:, 1].tolist(), int(len(src)))
    stats = {"cells": len(src), "resid_mm": float(resid.mean()),
             "resid_max_mm": float(resid.max()), "warp_mean_mm": float(rawwarp.mean()),
             "warp_max_mm": float(rawwarp.max())}
    return warp, stats
