# PolarGraph — Build Log

A running journal of building the polargraph: the software stack and the hardware bring-up — the wins, and
especially the gremlins (those are the useful part). Newest at the bottom.

---

## Phase 0 — Software architecture

- Set the stack ([ARCHITECTURE.md](ARCHITECTURE.md)): the PC generates the toolpath + polar inverse kinematics
  and streams to an **RP2040 running grblHAL**, which buffers and pulses the steppers + pen servo.
- **IK decided PC-side** from a numbers spike ([spikes/ik_spike.py](spikes/ik_spike.py)): worst-case belt-bow is
  ~1.3 µm at a 1 mm segment length versus a ~200 µm pen line, and the PC-side data volume is trivial. So grblHAL
  stays a plain 2-axis machine and we own the kinematics in Python.
- Resolved the open hardware question on paper: `steps/mm = 1600 / pulley_teeth` → **20T = 80, 16T = 100**.

## Phase 1 — The Studio (generative art front-end)

- Built [frontend/index.html](frontend/index.html): a single-file, zero-dependency browser studio for *plottable*
  line art. Generators: **flow field, variable hatch, rotor/moiré, ASCII dither**, and **Image → art**
  (dither / tonal hatch / gradient-following contour flow). Seeded + reproducible, 1–3 pen layers, exports
  mm-true, stroke-only SVG straight into the pipeline. Verified all generators + SVG export in a real browser.

## Phase 2 — Firmware bring-up

- Flashed **grblHAL 1.1f** (RP2040 **generic** pin map) via UF2 / BOOTSEL.
- Built [tools/grbl.py](tools/grbl.py) — a serial console so we can drive grblHAL directly (jog, sweep, read
  `$pins`/status) right over the COM port.

## Phase 3 — The bench bring-up saga (a.k.a. the gremlins)

A long, honest debugging arc. In the order we hit them:

1. **cp949 console.** Non-ASCII stdout crashes Python scripts on this Windows box — keep all script output ASCII.
2. **`$33` needs a COLD BOOT.** The RP2040 spindle-PWM frequency only re-clocks on a *power-cycle*, not a
   soft-reset or live write. The servo did a useless "3 mm nudge" until we power-cycled — then a real ~60–90°
   sweep. (RC-servo settings: `$33=50`, `$32=0`, `$35`/`$36` = pulse window.)
3. **`error:79` — floating E-stop.** grblHAL treats control/limit inputs as normally-closed, so *unconnected*
   pins read **triggered**. With nothing wired, inverting them (`$5=7`, `$6=1`, `$14=70`) + a soft-reset cleared
   the latched alarm → `Idle`.
4. **The pin-map confusion (the recurring one).** The hardware doc's interleaved layout
   (step, dir, step, dir on GP2–5) fights the RP2040, which generates steps with the **PIO** and needs the two
   STEP pins **adjacent**. The firmware's generic map is **step, step, dir, dir**:
   `X step GP2 · Y step GP3 · X dir GP5 · Y dir GP6 · EN GP8 · servo GP15 · limits GP9/GP10`. Corrected the
   hardware doc + [firmware/pinmap.md](firmware/pinmap.md).
5. **Cold solder joints** (classic perfboard gremlin). Motor #2 STEP measured **60 Ω** (should be ~0) → it came
   and went. The Y endstop had landed on **GP8 — the enable pin**. Reflow + re-pin fixed both. Diagnosed motor
   wiring live by jogging X/Y/Z and watching which motor moved on which pin.
6. **Flaky enable.** Both A4988 EN pins were on **GP6**, which the firmware uses as **Y-direction** — so the
   enable wiggled with every move. Final rewire put EN on **GP8** (software-held via `$1=255`).

Tooling that came out of it: [tools/total_test.py](tools/total_test.py) — a repeatable
steppers + servo + endstops self-test you run after any wiring change.

## Phase 4 — PC-side pipeline

- Built the **SVG → belt-length G-code** path, all stdlib, **verified with no hardware**:
  [profile.py](src/polargraph/profile.py) (machine.toml loader), [segment.py](src/polargraph/segment.py) (chord
  segmentation), [svgio.py](src/polargraph/svgio.py) (reads the Studio's polylines + basic paths),
  [gcode.py](src/polargraph/gcode.py) (place on paper → IK → segment → G-code with pen up/down, per-segment
  **feed compensation**, and greedy travel ordering), and [cli.py](src/polargraph/cli.py)
  (`polargraph gcode` / `polargraph verify`).
- **Fidelity:** SVG → G-code → forward-kinematics round-trips to **~1 µm** worst-case (vs a ~200 µm pen line).
  `verify` writes a green/red overlay so you can eyeball it before a belt moves. Tests in
  [tests/test_pipeline.py](tests/test_pipeline.py). (Pen up/down S-values stay placeholders until the gondola.)
- Built the **streamer** ([sender.py](src/polargraph/sender.py)): character-counting flow control (keeps grbl's
  RX buffer full without overflow), `$X` unlock + a gentle `G92` start, live progress, Ctrl-C feed-hold/reset.
  `polargraph stream art.svg` runs a job; the flow control is unit-tested against a mock grbl
  ([tests/test_sender.py](tests/test_sender.py)). **The full chain Studio -> SVG -> G-code -> stream is complete
  and tested.**
- Added a **calibration pattern** ([patterns.py](src/polargraph/patterns.py), `polargraph calib`): a centered
  square + concentric circles + crosshair for the first plot and steps/mm tuning.

## Where it stands

- ✅ **Both steppers** jog cleanly on X/Y (generic pins), with an always-hold enable.
- ✅ **Both endstops** register (`Pn:X` on GP9, `Pn:Y` on GP10). Manual homing for v1, so limits sit unused.
  (Noted: motor noise can falsely flicker a limit during motion — add a 100 nF cap / shielding before enabling
  homing.)
- ✅ **Servo** (GP15): sweeps correctly — a perfboard connection was the last gremlin. **Electronics complete.**
- **Data link:** USB serial for v1 (reliable streaming, tooling ready). Pico W + grblHAL WiFi is the future
  wireless upgrade if wanted; a USB Bluetooth dongle is a dead end (Pico is a USB device, not a host).

## Phase 5 — FIRST PLOT (machine alive end-to-end)

Machine assembled and calibrated live over the serial link: axes were crossed (motor cables swapped to fix),
X direction inverted post-swap (`$3=1`), pulleys confirmed **16T** (100 steps/mm; 304 mm belt travel — full-A4
corners exceed it by ~56 mm, mapped later via soft limits). Measured: **D = 318 mm**, pulley line → paper top
**85 mm**, gondola mounts **80 mm** apart, pen drop **27 mm** — the **gondola-aware IK** (level-hang model:
point-IK with D−80 and a (40,27) offset) went live in the pipeline the same day. Servo: down S350 / up S850
(lift mechanically marginal — gravity pen-holder redesign queued). Touch-off at a marked paper center + `G92`,
then **the first calibration plot streamed: 2,171 lines in 174.6 s, zero errors.** Gremlins en route: another
cold joint (driver #1's 3.3 V VDD feed) found by Vref-pinned-at-0 diagnosis.

## Phase 6 — Studio goes live + the art deepens

- **PLOT from the browser:** new studio server ([server.py](src/polargraph/server.py), `polargraph serve` /
  `tools/studio_server.py`) serves the frontend and adds `/plot` (with a dry-run quote: minutes, segments,
  metres of ink), `/status` (live progress), `/stop` (feed-hold + reset). The Studio's **► PLOT** button
  streams art straight to the machine; **■ STOP** aborts. Sender gained progress/abort callbacks.
- **Three continuous-line image engines** (the "non-grid scribble feel", after Theodore Warner's polargraph +
  dcode): **Squiggle** (amplitude/frequency-modulated boustrophedon waves; mono or **CMY three-pass color**),
  **Scribble** (darkness-weighted stipple → greedy nearest-neighbour tour → Chaikin smoothing = one continuous
  organic line; 1–3 tone pens), **Spiral shade** (wobble-modulated single spiral). RGB channels now ingested
  for color separation; CMY pen palette added.
- **Per-pass plotting** for real multicolor: `?layer=N` on `/plot` + a pass selector in the UI — plot pass 1,
  swap pens, plot pass 2; the machine holds position so passes stay registered. Verified: CMY art = 3 SVG
  layers; single pass ≈ 1/3 the segments; bad pass index errors cleanly.

## Phase 7 — The Algorist engines (one vectorizer, many worlds)

Built the universal foundation: **marching-squares contour extraction** (raster field → stitched polylines)
plus a **glyph vectorizer** (any browser font — or a dropped .ttf/.otf via FontFace — rasterised and traced
into plottable, decimated outlines; cached). Three engines on top, all browser-verified:

- **Topo glyphs** — contour isolines + a jittered letter-field (configurable ramp like `.:x#` or any word);
  darkness picks glyph, size, or both. The Cascadia ASCII-topo-map engine (43.7k segs after decimation).
- **Trace / ink** — posterize → per-level contour trace + tonal hatch: digital character art → ink line-art.
- **Typography** — type in any font / drop a font file → outline + hatch-filled plottable text.

Plus the **edge-fade primitive** (lines/glyphs probabilistically dissolve near boundaries — art blends into
blank paper, no hard frame) and `text`-type param inputs. Parked designs: focus-portrait (structured shading
decaying to organic sparse + motif glyphs), Schotter/ranstack order→chaos pack, Joy-Division ridgelines from
heightmaps, motif weaving, composition via registered multi-pass plots.

## Phase 8 — Mask / retouch (post-processing)

A paintable **density mask** over the preview, applied to generated strokes before preview/export/plot
(WYSIWYG): **brush** (cut / thin-N% / restore, sized + soft-edged), **lasso** fill, **smear/blur** (separable
box blur, melts abrupt density transitions), **Ctrl+Z undo** (snapshot stack), grey overlay toggle. Thinning is
*probabilistic* — lines fray organically like the edge-fade primitive instead of clipping; glyph-sized marks
drop atomically (no half-erased letters); long segments are resampled at 1.5 mm so the mask bites mid-line.
Verified numerically: cut/thin reduce segments, undo restores the exact pristine count, SVG export matches the
masked preview 1:1.

## Phase 9 — Packaged (v0.1.0)

Made it a proper pip-installable package that runs identically on Windows and macOS:
- **Studio + default profile now ship inside the package** (`src/polargraph/studio/`, `src/polargraph/data/`),
  so paths hold in dev and installed; profile resolution: `--profile` > `./profiles/machine.toml` >
  `~/.polargraph/machine.toml` > packaged default (carries this machine's calibrated numbers).
- `pip install .` → a real `polargraph` command (`gcode`/`verify`/`stream`/`serve --open`/`calib`);
  `pyserial` declared as the sole dependency.
- **Zero-install launchers:** double-click `studio.bat` (Windows) / `studio.command` (macOS, exec bit recorded
  in git, LF pinned via `.gitattributes` — CRLF would break the shebang).
- Verified: full test suite green; CLI works from *outside* the repo on the packaged profile (the
  macOS-experience proof); Studio serves from its in-package home with all 11 generators + mask + plot API.

## Next

- Mechanical build (in progress): frame, motor mounts (pulleys protrude through drilled holes — no router),
  belt routing + counterweights, gondola (leaning toward a compact, high-CoM ballast near the pen).
- (Optional) **Untethered runs:** SD-card job-from-file (grblHAL SD plugin) or a dedicated Raspberry Pi streamer
  for multi-hour unattended plots.
- Calibrate `D` (anchor spacing) and steps/mm against real belt travel; first calibration plot (square +
  concentric circles).
- **First real stream + calibration:** once the machine's reassembled, run `calibration.svg`, measure the drawn
  square against its commanded size to set steps/mm, and refine `D` from the circles. (Streamer is built +
  flow-control-tested; it just needs the board powered and a manual-home/`G92`.)
- vpype only later, to flatten curvy third-party SVGs (the Studio's own output is already polylines).
