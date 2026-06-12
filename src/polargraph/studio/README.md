# Polargraph Studio (frontend)

A single-file, zero-dependency browser studio for generating **plottable** line art — stroke-only polylines
sized in millimetres, exported as SVG that drops straight into the plotting pipeline.

Open it by double-clicking `index.html` (works offline), or serve the folder and visit it. No build step.

## Why it's decoupled from the hardware

The studio only emits **vector geometry**. It knows nothing about kinematics, G-code, or firmware — so it can be
built and enjoyed long before the machine exists. The handoff is one clean artifact:

```
Studio  →  export.svg  →  vpype (fit + optimise travel)  →  PC-side IK + segment  →  belt-length G-code  →  grblHAL
```

Everything is **stroke, no fill** (a pen can't fill), in real mm, so what you preview is what the pen draws.

## Generators

| | What it makes | Your words it answers |
|---|---|---|
| **Flow field** | streamlines following a noise field — organic, the polargraph's sweet spot | "rotating lines / flow" |
| **Variable hatch** | parallel lines whose spacing/overlap is modulated by a field; optional cross-hatch | "parallel lines that vary in overlap" |
| **Rotor / moiré** | stacked line bundles at incrementing angles → moiré interference | "rotating lines", "patterns" |
| **ASCII dither** | ordered (Bayer) dithering of a procedural field into dots/dashes/crosses | "ASCII-art-type dithered" |
| **Image → art** | a photo rendered as **dither**, **tonal hatch**, or **contour flow** (lines follow the image's gradients) | turn a friend's portrait into plottable line art |
| **Squiggle** | boustrophedon rows whose wave amplitude+frequency follow image darkness; **mono or CMY** (3 color passes) | the iconic plotter-portrait look; "non-grid" organic shading |
| **Scribble** | darkness-weighted stipple connected into **one continuous smoothed line** (nearest-neighbour tour + Chaikin) | the hand-scribbled "pen on paper" feel; 1–3 tone pens |
| **Spiral shade** | a single spiral from the centre, wobble-modulated by darkness | hypnotic single-line portraits |
| **Topo glyphs** | marching-squares **contour isolines** + a jittered **letter field** (configurable ramp, e.g. `.:x#` or any word); darkness picks the glyph, its size, or both | ASCII-shaded topo maps (feed it an altitude map) |
| **Trace / ink** | posterize → contour-trace each level + tonal hatch in the darks | turn flat-colour character art into clean ink line-art |
| **Typography** | type text in any browser font **or drop a .ttf/.otf**; glyphs are rasterised → contour-traced into plottable outlines, with optional hatch fill | real typefaces, case by case |

### Image mode

Pick **Image → art** (a sample image loads automatically). Use **Load sample image** or the file picker to choose
your own, then switch **Mode**: `dither` (Bayer stipple/ASCII), `hatch` (tonal lines, cross-hatched in shadow),
or `flow` (streamlines following the image gradients). Tune **Contrast**, **Density**, **Invert**. The image is
fit (letterboxed) into the margin rect; everything stays in mm. Note: `flow`/`hatch` get **dense** — raise
**Density** and lower **Flow stroke** for a file you'd actually plot (a plot-time estimate is on the roadmap).

The image engines share an **Edge fade (mm)** parameter: lines and glyphs probabilistically dissolve near
the boundary instead of stopping at a hard frame — the piece blends into the blank paper. 0 = off.

## Mask / retouch (post-processing before export)

Paint density control directly on the preview — the mask applies to the generated strokes *before*
preview/export/plot, so what you see is exactly what the pen draws:

- **BRUSH** — drag on the page. **Effect** sets what the brush paints: `cut` (remove), `thin N%`
  (probabilistic density reduction — lines fray organically, no hard clip), or `restore`.
  **Size** and **Softness** shape the brush.
- **LASSO** — click points around a region, double-click to close and fill with the current effect.
  Esc cancels.
- **SMEAR / BLUR** — box-blurs the whole mask ~3 mm per click: melts abrupt density transitions.
- **CLEAR** resets; **Ctrl+Z** undoes strokes (last 10); **OVERLAY** toggles the grey mask preview.
- Glyph-sized marks (letters, dither stamps) are kept/dropped whole — no half-erased letters.
- The mask resets when paper size/orientation changes. Regenerating (new seed/params) keeps the mask.

## Plotting from the browser

Run the studio server — `python tools/studio_server.py` (or `polargraph serve`) — and open
http://127.0.0.1:8770. The **► PLOT** button quotes the job first (est. minutes, segments, metres of ink),
then streams it; the button becomes **■ STOP** with live progress. Selectors below it:

- **anchor** — `paper-placed` uses the machine's homed coordinates (art lands where the preview shows);
  `start from current spot` begins wherever the pen hangs (safe after a power-cycle).
- **plot pass** — `all passes` for single-pen work; for **multicolor** (CMY squiggle, tone-pen scribble),
  plot **pass 1**, swap pens when it finishes, plot **pass 2**, etc. The machine holds position between
  passes, so registration is preserved.

## Controls

- **Seed** + dice — every result is reproducible from its seed; the dice rolls a new one (same params, new variation).
- **Pens / palette** — 1–3 ink colours; generators split their output into per-pen layers (one `<g>` per pen in the SVG, ready for pen changes).
- **Paper** — A4/A5/A6, portrait/landscape, margin. Coordinates are true mm.
- **Export SVG** — downloads `polargraph_<gen>_<seed>.svg` with a comment recording the generator, seed, and params (reproducible). **Copy params** puts the same JSON on your clipboard.

## Roadmap (easy to extend)

Generators are a registry (`GEN.<name>` with a `params` list + a `gen()` function) — adding one is ~30 lines.
Next candidates: continuous-line / TSP single-stroke, Voronoi/Delaunay hatching, Truchet tiles, and a per-layer
**plot-time estimate**. Color overlap/blend modes for the "artistic overlap" idea. (Image → art shipped.)
