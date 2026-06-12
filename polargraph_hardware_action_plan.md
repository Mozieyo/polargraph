# Polargraph — Hardware Build & Bring-Up (glass-frame edition)

**Scope:** hardware only. Firmware and the toolpath/software pipeline are the *next* phase — parked for now. This gets you a fully assembled, fully wired machine that you've verified electrically and mechanically, ready to flash later.

**Machine at a glance**
- Surface: your picture frame — full ~45 × 55 cm, glass aperture ~30 × 41 cm. Glass is the flat backing; paper (A4 or A5) tapes to the front of the glass.
- Motors: 2× 17HS4401 at the top corners, spacing **D ≈ 400 mm**, mounted just above the glass top edge.
- Drive: 6 mm GT2 belt run over the pulley + counterweight tail. Drivers: 2× A4988 on a perfboard mini-shield. Logic board: **RP2040 (Raspberry Pi Pico)**, wired now, configured later.
- Pen lift: SG90 servo.
- Draw zone: center the paper left-right; the upper-middle of the aperture is sharpest. A5 leaves more room in the sweet spot than A4.

---

## Buy
- 6 mm GT2 open-ended belt — a 10 m roll (you'll use ~0.9 m/side; rest is spares)
- 2× electrolytic caps, 47–100 µF, ≥ 35 V (one per driver, across VMOT–GND)
- Perfboard + 2× 2×8 female headers (to socket the A4988s) + pin headers
- 12 V→5 V buck module for the servo (or use the ATX's 5 V rail)
- Fused, switched IEC mains inlet + mains lead for the S-240-12 (if not already)
- Hookup wire, screw terminals or JST, zip ties
- Optional: a $2–3 RC servo tester (lets you bench-test the SG90 with no firmware)

## Print (K1C)
- 2× motor-mount brackets (clamp the NEMA17, clip/screw to the frame's top corners, position the pulley with a clear path for the belt tail down the back)
- Gondola: belt-attach points (~40 mm apart) + pen clamp on the centerline + SG90 mount. Swivel arms on bearings if you want guaranteed-upright motion (the GPenT / Makelangelo-5 remix approach)
- Pen clamp sized to your Micron / Isograph
- 2× counterweight cups
- Belt guides for the pulleys (keep the belt from walking off sideways)

---

## A · Mechanical assembly
1. Pulleys onto the motor shafts, set-screw on the flat.
2. Motor brackets to the frame's top corners. Aim for **D ≈ 400 mm** center-to-center, motors just above the glass top edge. Leave a clear drop behind the frame for the belt tails.
3. Belt guides / idlers (A6 bearings) by each pulley so the belt wraps the pulley and can't walk off.
4. Build the gondola: attach points, pen clamp, servo. Balance it so the pen hangs vertical. Target **~120 g** total (bare ~50–70 g + added ballast); ~30 gf pen pressure at a 15° tilt.
5. Route each belt: clamp at the gondola → up to the pulley → wrap over the top (teeth meshed) → tail down the back with a counterweight.
   - Counterweight ≈ 0.39 × gondola weight each. For a ~120 g gondola that's **~45 g each**. Err slightly heavy so the teeth never skip.
6. Tape the paper to the glass (glass is slick — tape all edges). Prop/mount the frame with **~10–15° backward tilt** so the pen presses gently and evenly.

---

## B · Wiring guide

### Mains safety (the only genuinely dangerous part)
- Feed the S-240-12 from a **fused, switched IEC inlet**. (Use the S-240-12, not the suspect adjustable supply.)
- Earth the inlet's earth pin to the PSU chassis and any metal frame parts.
- Cover/insulate the L and N screw terminals — no exposed live metal.
- Triple-check L / N / E before first power-on.

### Power tree
- S-240-12 **12 V** → VMOT bus on the perfboard.
- 12 V → buck → **5 V** → servo V+. (Power the Pico from USB during testing; for standalone, feed the buck's 5 V into the Pico's **VSYS** pin — add a series Schottky if USB might also be plugged in.)
- Pico **3V3(OUT)** → A4988 VDD bus.
- **One common ground:** PSU −, buck −, Pico GND, driver GND all tied (star ground).
- **Power-on order during testing: 12 V PSU ON first, *then* plug in USB.** The reverse risks back-powering the board through USB.

### Each A4988 (×2)
- VMOT + GND → 12 V bus. **47–100 µF cap across VMOT–GND right at the driver** (mandatory — prevents the spike that kills the chip).
- VDD + GND → **3.3 V** bus. (3.3 V, not 5 V — it sets the logic threshold so the Pico's 3.3 V STEP/DIR signals register cleanly.)
- 1A/1B and 2A/2B → the two motor coils (find each pair by continuity).
- RESET ↔ SLEEP jumpered together.
- MS1/MS2/MS3 → all HIGH (3.3 V) = 1/16 microstep. *(Set them all LOW = full step for the bench-step test in §C-E, then back to HIGH.)*
- ENABLE → a Pico GP pin (the Pico has pins to spare, so you can let the firmware disable the motors when idle), or tie to GND for always-on.
- STEP + DIR → Pico GP (table below).

### RP2040 (Pi Pico) harness (wire these now; the firmware learns them later)
Pin numbers are Pico **GP** labels. Avoid GP23/24/25 (used internally for the SMPS, VBUS sense, and the onboard LED); everything else on the header is fair game, so there's no pin pressure here. This block keeps the step/dir pins contiguous and leaves GP0/1 (UART0) free.

> **CORRECTED to grblHAL's generic pin map** (the original interleaved map was wrong: the RP2040 PIO needs STEP
> pins on *consecutive* GPIOs, and the spindle-PWM/servo output is fixed to **GP15**). Wire to this — it matches
> `firmware/pinmap.md`. (The pre-correction map had Y STEP=GP4, DIRs=GP3/5, EN=GP6, **servo=GP7** — all superseded.)

| Signal | Pico GP | To |
|---|---|---|
| X STEP | GP2 | A4988 #1 STEP |
| Y STEP | GP3 | A4988 #2 STEP |
| X DIR | GP5 | A4988 #1 DIR |
| Y DIR | GP6 | A4988 #2 DIR |
| ENABLE (shared) | GP8 | both A4988 EN (or tie to GND) |
| Servo (pen) | **GP15** | SG90 signal (spindle PWM) |
| X limit (optional) | GP9 | A6 endstop |
| Y limit (optional) | GP10 | A6 endstop |

These are your physical connections; at the firmware phase you'll point grblHAL at exactly these pins (a custom pin map, or match a board map to this wiring). RP2040 GPIO is 3.3 V and not 5 V-tolerant — the same reason the A4988 VDD sits at 3.3 V.

*(Assumes a Pi Pico board. A bare RP2040 needs external flash, a crystal, and USB — i.e. a Pico-style board — before any of this applies.)*

### Servo (SG90)
- V+ → **5 V** (buck). GND → common. Signal → GP7.
- Don't run it off 3.3 V (too low) or off USB 5 V under load (stall current browns it out).

### Endstops (optional — decide now)
- A6 switches: COM → GND, NO → GP8 / GP9.
- Skip them and home by hand later, **or** mount them now (GPenT-style, on the covers over the protruding pulley/axle area) for repeatable homing. Either is fine; wiring them now costs nothing if you might want them.

---

## C · Hardware test procedure (no firmware needed)
Run in order. Stop at the first failure and fix before continuing.

**A. Pre-power continuity (everything off).** No short between VMOT↔GND, 3.3 V↔GND, or 5 V↔GND. Cap polarity correct. Coil pairs land on 1A/1B and 2A/2B correctly.

**B. Rails.** PSU on: VMOT bus reads **12 V**; buck output **5.0 V**. Plug in USB: VDD bus reads **3.3 V**. Confirm all grounds are common.

**C. Driver current (motors disconnected).** Set each A4988's trimpot, measuring wiper-to-GND. Target ~0.5–0.65 A. Check the sense-resistor marking first:

| Rsense marking | Vref target |
|---|---|
| R100 (0.10 Ω) | ~0.48 V |
| R068 (0.068 Ω) | ~0.33 V |
| R050 (0.050 Ω) | ~0.24 V |

(`Vref = Itrip × 8 × Rsense`. Measure with motors *off*.)

**D. Motor lock test (motors connected, EN low, no step signal).** Each shaft should *hold* — clear resistance when you try to turn it by hand. That alone confirms 12 V, VDD, enable, coil wiring, and current are all good. If it spins freely, you have an enable/power/coil fault — fix before stepping.

**E. Manual step + direction test (no code).** Set MS1/2/3 all LOW (full step) so motion is visible. Tie DIR to 3.3 V. Tap a jumper from 3.3 V to the STEP pin — each tap advances the shaft ~1.8° (a distinct click). Move DIR to GND — taps now reverse. That proves the STEP and DIR paths end-to-end. Restore MS1/2/3 to HIGH (1/16) afterward. *(Want continuous spin instead? Any tiny step-loop on a spare Arduino/Pico will do it — a throwaway diagnostic, not your motion firmware.)*

**F. Servo.** Confirm it's mechanically free and the lift gear meshes with the pen carrier. A full sweep needs a PWM source: with the servo tester, sweep it and mark the pen-up and pen-down positions (target ~3–5 mm of tip lift). Otherwise this gets finalized when the controller comes up.

**G. Mechanical dry run (belts + gondola on, no pen yet).** Step or hand-turn the motors and check: belts stay taut with no tooth-skip (adjust counterweights, err heavy); the gondola hangs pen-vertical and glides on the glass without scuffing; the lift mechanism raises/lowers cleanly ~3–5 mm; nothing binds anywhere across the aperture. Tune gondola balance and tilt for gentle, even contact.

**H. Record D.** Measure pulley center-to-center precisely and write it down — it's the single most important number for the firmware/calibration phase.

---

**Stop here.** The machine is now built, wired, and hardware-verified. The next phase is firmware + the first calibration plot — pick that up when you're ready.

**Firmware (next phase):** the brain is grblHAL, which has a maintained RP2040 driver. The polar (wall-plotter) math can run on the PC — your toolpath emits the two belt lengths and grblHAL just executes coordinated moves — or via grblHAL's own kinematics. Either way it's a config/build step, not a hardware one; none of the wiring above changes.
