# Polargraph Plotter — Project Document

**Project:** DIY polargraph (hanging V-plotter) for archival pen art
**Status:** Phase 1 — hardware build, in progress
**Companion doc:** *Polargraph — Hardware Build & Bring-Up* (execution detail: BOM, wiring, bench tests)
**Last updated:** June 2026

---

## 1. Overview
A wall-mounted polargraph: a pen-holding gondola hangs from two GT2 belts driven by stepper motors at the top corners of a frame, and the pen position is the intersection of the two belt lengths. Paper mounts to a glass picture frame; a PC generates the toolpath and an RP2040 executes the motion. The purpose is to produce small, high-quality archival pen drawings to give to friends abroad — and to lean into the polargraph medium's character (organic line, gentle geometric softness) rather than fight it.

## 2. Goals & Non-Goals
**Goals**
- Clean, repeatable, archival line art at A4/A5 — crisp lines, no blotching or feathering.
- Reuse materials on hand (Anet A6 donor parts, existing RP2040, K1C-printed plastics) where sensible.
- Treat the medium's quirks as a feature.
- PC-driven workflow with the microcontroller as a real-time executor.

**Non-goals (v1)**
- Large format (A1) — deferred.
- Color / multi-pen — deferred.
- Auto-homing — manual homing is acceptable for v1.
- Generative / ML toolpath layer — inspiration only, out of scope.

## 3. Scope
**In scope (v1)**
- Glass-frame machine, ~30 × 41 cm draw area, single black archival pen.
- Mechanical build, wiring, and firmware-free bench bring-up (see companion doc).
- grblHAL on RP2040; PC-side toolpath + polar inverse kinematics; calibration; first plots.

**Out of scope (v1 / future)**
- A1 scale-up; endstop auto-homing; TMC2209 (silence / StallGuard); color; PC-hosted web UI; generative front-end.

## 4. Requirements
**Functional** — draw vector/line art from the PC onto A4/A5 paper; servo pen up/down; positionable across the full aperture; repeatable origin (manual home v1).
**Quality** — lightfast pigment ink on cotton paper; lines crisp and even; positional error well under the pen's line width; no dwell-blobs or feathering.
**Constraints** — 3.3 V logic throughout the signal chain; mains safety on the PSU side (fused / switched / earthed inlet); reuse of donor and existing parts.

## 5. System Architecture
**Mechanical** — glass frame as flat backing; 2× NEMA17 at the top corners, spacing D ≈ 400 mm; 6 mm GT2 belt run over each pulley (constant pitch radius, zero slip) with a counterweight tail; printed gondola (~120 g) carrying the pen and an SG90 lift; frame tilted back ~10–15° for even pen contact.

**Electronics** — S-240-12 (12 V) → A4988 motor supply; 12 V→5 V buck → servo; Pico 3V3 → A4988 logic; single common ground.

**Control split**
```
PC (toolpath gen + polar IK)  --USB-->  RP2040 / grblHAL (planner buffer + step/dir gen + servo PWM)  -->  2x A4988  -->  steppers -> belts -> gondola/pen
```
The PC does the heavy lifting; the RP2040 buffers queued moves (grbl's planner look-ahead — the "cache") and generates the real-time pulses. Position is the intersection of two belt lengths; the inverse kinematics (each belt L = √((x − aₓ)² + (y − a_y)²)) runs PC-side (streamed as 2-axis belt-length moves) or via grblHAL's wall-plotter kinematics.

## 6. Key Components
| Subsystem | Part | Source |
|---|---|---|
| Surface | Glass picture frame (~45×55 cm; ~30×41 cm aperture) | have |
| Motors | 2× Hanpose 17HS4401 (NEMA17, 1.8°, 1.7 A) | have |
| Drivers | 2× A4988 on a perfboard mini-shield, 1/16 µstep | have |
| Controller | RP2040 (Raspberry Pi Pico) + grblHAL | have |
| Host | PC (toolpath + IK + streaming) | have |
| Power | S-240-12 (12 V) + 12 V→5 V buck | have |
| Belt / pulleys | 6 mm GT2 (10 m roll); A6 20T pulleys + idlers | buy belt / donor |
| Homing (opt) | A6 endstops ×2 | donor |
| Pen lift | SG90 servo (MG90S optional) | have |
| Pen | Sakura Pigma Micron (archival pigment); Rotring Isograph as refillable upgrade | buy |
| Paper | 100% cotton, hot-press / smooth | buy |
| Plastics | Motor mounts, gondola, pen clamp, counterweight cups, belt guides | K1C print |

## 7. Roadmap
**Phase 1 — Hardware build & bring-up (current).** Assemble, wire, and bench-verify (motor lock + manual step, servo, mechanical dry run, record D). Exit: a wired, hardware-verified machine. *(Companion doc.)*

**Phase 2 — Firmware & calibration.** grblHAL on the Pico, pin map matched to the wiring; polar IK (PC-side or firmware); calibrate D, steps/mm, and segment length; first calibration plot (square + concentric circles).

**Phase 3 — Toolpath & production.** vpype pipeline; line-art sources (stipple, flow-field, continuous-line, drawingbots.net); tune pen pressure / speed / dwell for clean output; produce pieces for mailing.

**Phase 4 — Optional future.** A1 scale-up; endstop auto-homing; TMC2209; MG90S; color / multi-pen; PC-hosted UI / generative layer.

## 8. Risks & Open Questions
- RP2040 must sit on a Pico-style board (external flash / crystal / USB) — confirm board vs bare chip.
- A6 pulley tooth count (16T vs 20T) sets steps/mm — verify before calibration.
- grblHAL's pin map must match the wiring; wall-plotter homing config is the fiddly part (manual home for v1 sidesteps it).
- Line quality (blur / blot) is an iterative tuning problem — pen pressure, dwell, feed rate, paper, rigidity.
- Sag / sway are minor at this scale but managed with a light (~120 g) gondola and slow, smooth motion.

## 9. Design Decisions (rationale)
- **Polar V-plotter over Cartesian** — mechanically simpler at size; the medium's character is the point.
- **Glass-frame build over A1** — flat glass kills the flatness error; smaller scale shrinks sag, sway, and belt-stretch; A4/A5 is easily mailable. A1 deferred, not abandoned.
- **Belt-as-rack + counterweight** — constant pitch radius and zero slip, vs. a spool's diameter drift.
- **RP2040 + grblHAL over ESP32-C3 / FluidNC** — FluidNC is ESP32-only (the C3 is unsupported); grblHAL has a mature RP2040 driver, and doing the IK on the PC makes the controller choice portable and the MCU's job simple.
- **Archival pigment fineliner + cotton paper** — lightfast, low-maintenance, crisp lines.

## 10. Success Criteria
The build is done when it repeatably produces clean, blotch-free, archival A4/A5 line art worth giving away — and is reproducible from its recorded calibration (D, steps/mm).

## 11. References
- Companion: *Polargraph — Hardware Build & Bring-Up* (this project's execution doc).
- Reference build: Teddy Warner's "GPenT" polargraph.
- Firmware / software: grblHAL (RP2040 driver); FluidNC WallPlotter (kinematics reference); Makelangelo (firmware/software lineage); vpype; drawingbots.net.
