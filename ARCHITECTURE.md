# Polargraph — Software Architecture

**Scope:** the software stack for the glass-frame polargraph (see `polargraph_project_document.md`).
**Status:** v0 — architecture set, foundation (geometry math) landed. **IK: PC-side (decided, §6).**
**Last updated:** 2026-06-05

---

## 1. Control split (what runs where)

```
 ART SOURCE          PC  (Python, this repo)                          MCU (RP2040 / grblHAL)        MACHINE
 ─────────────────   ───────────────────────────────────────────     ────────────────────────     ─────────────────
 SVG / generative ─► load ─► fit/optimize ─► [IK + segment?] ─► gcode ─► stream ──USB──► planner ─► step/dir ─► A4988 ─► steppers ─► belts
                     (vpype)                  (geometry)       (gcode)  (sender)  look-ahead   servo PWM ─────────────► SG90 pen
```

The PC does the heavy lifting and *streams*; the MCU buffers queued moves (grbl's planner look-ahead) and
generates the real-time pulses. The one architectural fork — **whether the polar inverse kinematics runs on
the PC or in firmware** — is deferred to a spike (§6). Everything else is independent of that choice.

Two coordinate domains exist and must never be confused:
- **Cartesian (paper) space** — `(x, y)` in mm, where the pen actually is on the page.
- **Belt-length space** — `(L1, L2)` in mm, the two belt lengths the motors control.

The IK fork is simply *where the `xy → (L1,L2)` conversion happens.* The math is identical either way and lives
in `geometry.py` regardless (we need it for preview, calibration, and the spike even if firmware does it live).

## 2. Coordinate convention (fixed)

Machine frame, origin at the **left belt anchor** (the point the left belt leaves its pulley):

```
   A1 (0,0) ●───────── D ─────────● A2 (D,0)      x → right
            │\                    /                y → DOWN  (gondola hangs below)
            │ \ L1            L2 /
            │  \                /
            │   ● P (x,y)      /
            │
            ▼ y
```

- `A1 = (0, 0)`, `A2 = (D, 0)` — the two anchors, horizontal spacing `D` (≈400 mm, refined by calibration).
- Pen point `P = (x, y)`, `y > 0` (below the anchors).
- **Inverse kinematics:** `L1 = √(x² + y²)`, `L2 = √((x − D)² + y²)`.
- **Forward kinematics** (belt → xy, for preview/verification): `x = (L1² − L2² + D²) / 2D`, `y = √(L1² − x²)` (positive root — gondola hangs below).

Paper is placed as a rectangle inside the draw area with a chosen origin/margins (a calibration step, §5).

## 3. PC pipeline (layered)

| Layer | Module | Responsibility |
|---|---|---|
| Input | `pipeline.py` | Load SVG (Inkscape, drawingbots.net, generative). Later: generative sources directly. |
| Prep | `pipeline.py` (vpype) | Fit/scale to paper, merge lines, **sort to minimize pen-up travel**, simplify, dedupe. Output: ordered polylines in paper-mm + pen up/down between paths. |
| Geometry | `geometry.py` | The fixed coordinate math: `xy ↔ (L1,L2)`, steps/mm, machine-geometry dataclass. |
| Segment | `segment.py` | Split long chords into short segments so a straight pen line stays straight in belt space. Bypassed if firmware does kinematics. Segment length is a calibration param. |
| Emit | `gcode.py` | Emit G-code in the chosen mode (Cartesian `XY`, or belt-length axes). Insert pen up/down (servo) + settle dwells. |
| Stream | `sender.py` | grbl streaming over `pyserial` with character-counting flow control to keep the planner buffer full; parse `ok`/`error`/`ALARM`, status polling (`?`), real-time hold/resume/soft-reset. |
| Preview | `preview.py` | Render the toolpath **and** the forward-kinematics of the belt-space moves, so we can see/measure segmentation error with **no hardware**. The primary test harness pre-bench. |
| Profile | `profile.py` | Load/save the machine profile (`profiles/machine.toml`): `D`, steps/mm, pen positions, limits, segment length. The doc's reproducibility requirement. |
| CLI | `cli.py` | Entrypoints: `preview`, `plot`, `jog`, `calibrate`. |

## 4. Firmware layer (grblHAL on RP2040)

- **Board:** confirm a Pico-style board (external flash/crystal/USB) vs a bare RP2040 — open risk in the project doc.
- **Driver:** grblHAL RP2040 driver. Wiring is fixed by the hardware action plan §B and captured in [`firmware/pinmap.md`](firmware/pinmap.md) — STEP/DIR on GP2–GP5, shared EN on GP6, servo GP7, limits GP8/9. On RP2040 this is a *build-time board map*, not `$`-settings, so the firmware task is a board map matching that wiring.
- **Pen servo:** SG90 driven from a PWM pin, mapped through grblHAL (spindle-PWM-as-servo, `M3 S<val>` / `M5`, with up/down values in the profile).
- **Kinematics:** plain 2-axis — IK is PC-side (decided, §6). No wall-plotter build needed.
- **Homing:** manual for v1 (set origin by hand) — sidesteps the fiddly wall-plotter homing config the project doc flags.

Firmware notes/pin map live in `firmware/`. Flashing + on-bench config is **M2 (motors alive)** — it needs the wired machine, so it follows the PC work we can do now.

## 5. Calibration (recorded → reproducible)

All of this lands in `profiles/machine.toml` so a build is reproducible from its profile (success criterion).

- **`D` (motor spacing)** — measured, then refined by drawing a known square/circle and correcting from measured error.
- **steps/mm (belt)** = `(full_steps × microsteps) / (pulley_teeth × belt_pitch)` = `1600 / teeth` for 200-step motors, 1/16 µstep, 2 mm GT2.
  → **20T ⇒ 80 steps/mm, 16T ⇒ 100 steps/mm.** (The 16T-vs-20T pulley question in the project doc lands here — verify before calibration.)
- **segment length** — chord length for curve segmentation: smoothness vs. G-code size/throughput.
- **pen up/down + dwell** — servo positions and settle dwells, tuned for blob-free starts/stops (the "no dwell-blobs" quality bar).
- **feed / accel** — tuned for the ~120 g gondola: smooth, sag/sway-free motion.

First calibration plot: a square + concentric circles (Phase 2 exit).

## 6. IK location — decided: PC-side

The spike (`spikes/ik_spike.py`) settled this from numbers. The phenomenon: a straight XY line is *curved* in
belt-length space, so linear belt-space interpolation bows off the intended path — an unsegmented 240 mm move near
the top bows **16 mm**. Segmentation kills it; the residual is the per-chord bow. Both candidate architectures
segment identically — the spike just priced PC-side.

| | **PC-side IK** (grbl = dumb 2-axis) | **grblHAL wall-plotter kinematics** |
|---|---|---|
| Custom code | We write segmentation + IK (well-understood, unit-tested) | Almost none — stream Cartesian `XY` |
| Testable now | **Yes** — fully, on the PC, no hardware | Needs the wired machine to verify config |
| MCU role | Dumb/portable (project doc's stated lean) | Bound to grblHAL's wall-plotter mode |
| Risk | More G-code volume / streaming bandwidth | Config is "the fiddly part" (project doc) |

Decision criteria: segmentation error, G-code volume vs. streaming bandwidth, config complexity, portability.

**Spike result** — worst-case residual flatness error across the draw area (pen line ≈ 200 µm):

| segment length | worst bow | vs. pen line |
|---|---|---|
| 4 mm | 20 µm | 1/10 |
| 2 mm | 5 µm | 1/40 |
| **1 mm** | **1.3 µm** | **1/150** |
| 0.5 mm | 0.3 µm | 1/600 |

At 1 mm the flatness error is ~150× finer than the pen line, and PC-side data volume is trivial (~107 KB for 5 m of
ink, streamed *over* a multi-minute plot — bandwidth and segment-rate are non-issues). PC-side therefore wins on
every measured axis and on portability + testable-now (the project doc's lean); firmware kinematics' lone edge —
less wire data — is moot here.

**Decision: PC-side IK, `segment_length_mm = 1.0`.** grblHAL stays a plain 2-axis machine. Implementation note for
`segment.py`/`gcode.py`: belt-space moves distort pen feed (constant belt velocity ≠ constant pen velocity), so
scale each segment's `F` by `cartesian_len / belt_len` to hold true pen speed — cheap, per-segment. The layout in §7
is unchanged; only `segment.py` + `gcode.py` realise this choice.

## 7. Repository layout

```
PolarGraph/
  polargraph_project_document.md   # project doc (existing)
  ARCHITECTURE.md                  # this file
  README.md
  pyproject.toml
  profiles/machine.toml            # calibration profile (the reproducible record)
  src/polargraph/
    geometry.py                    # ← landed: IK/FK + steps/mm (fork-independent)
    profile.py  pipeline.py  segment.py  gcode.py  sender.py  preview.py  cli.py
  firmware/                        # grblHAL build/flash notes + pin map
  spikes/ik_spike.py               # the IK-location decision spike
  tests/                           # unit tests (geometry round-trip first)
  examples/                        # calibration_square.svg, concentric_circles.svg
  output/                          # generated gcode/previews (gitignored)
```

## 8. Tooling

Python 3.13 (present) · `vpype` (geometry/SVG/optimize) · `numpy` (vector math) · `pyserial` (streaming) ·
`matplotlib` (preview) · `click` (CLI) · `pytest` (tests). The core geometry math is stdlib-only so it runs
with zero install; the rest comes in as each layer is built.

## 9. Bring-up roadmap

| Milestone | What | Needs HW? |
|---|---|---|
| **M0** | Architecture + geometry foundation + profile schema | no — *done* |
| **M1** | IK spike → **decide IK location** | no |
| **M2** | **Motors alive:** grblHAL flashed, pin map, jog one motor, confirm direction + steps/mm by measuring belt travel | **yes** |
| **M3** | Two-motor coordinated move; pen servo up/down; manual origin | yes |
| **M4** | First calibration plot (square + concentric circles); refine `D` + steps/mm | yes |
| **M5** | vpype pipeline + real line-art test prints; tune pen/dwell/feed for clean output | yes |
| **M6** | Production pieces; freeze the recorded profile | yes |

We can build and unit-test M0–M1 entirely on the PC now; M2+ begins the moment the motors/drivers/Pico are wired.
