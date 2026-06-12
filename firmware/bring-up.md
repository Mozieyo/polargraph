# Motors-Alive Firmware Bring-Up (M2)

The **firmware** bench step: flash grblHAL, jog a motor a known distance, and wiggle the pen servo. This is M2 in
`../ARCHITECTURE.md`. Manual homing only (v1). Verified against current grblHAL/RP2040 docs.

> **Prerequisite — hardware first.** Assumes the build/wiring/electrical checks of
> `../polargraph_hardware_action_plan.md` §A–H pass (mains safety, power tree, A4988 Vref/current §C-C, caps,
> grounding, power-on order **12 V PSU first, then USB**, motor-lock §D, manual step §E). Current bench state:
> **one stepper + the SG90 installed, no endstops** → homing stays off, we jog one axis.

> **Pin correction (read this).** On RP2040 grblHAL drives steps via the **PIO**, which needs the **STEP pins on
> consecutive GPIOs**. The hardware doc's interleaved layout won't run on a stock build. Wire to the driver's
> **generic map** — see [`pinmap.md`](pinmap.md). On a breadboard it's a few jumper moves, and then a stock
> "generic" UF2 works with **no custom build**.

## 1. Rewire to the generic map

For what's installed now (one stepper + servo):

| Signal | from (hardware-doc wiring) | to (generic map) |
|---|---|---|
| stepper STEP | GP2 | **GP2** (no change) |
| stepper DIR | GP3 | **GP5** |
| stepper ENABLE | GP6 | **GP8**, or tie A4988 EN→GND (simplest for the bench) |
| servo signal | GP7 | **GP15** |

Full 2-axis target map is in [`pinmap.md`](pinmap.md). Keep A4988 **RESET↔SLEEP jumpered** and **MS1/2/3 HIGH**
(1/16 µstep). Servo V+ on **5 V** (buck), common ground.

## 2. Flash grblHAL (RP2040)

1. **Build the UF2** with the grblHAL **Web Builder**: pick the **RP2040** driver, leave the board as **none /
   generic** (→ uses `generic_map.h`), keep defaults, download the `.uf2`. (Or compile from source with the Pico
   SDK if you prefer.)
2. For the **first flash, disconnect the A4988 from the Pico** (safety). Hold **BOOTSEL**, plug USB → an
   `RPI-RP2` drive appears → drag the `.uf2` onto it → it reboots into grblHAL. Reconnect the driver after.

## 3. Connect and read the firmware back

- **Power order: 12 V PSU on first, then USB.** Open the USB serial `COMx` at **115200 8N1**
  (`python -m serial.tools.miniterm COMx 115200`, or a grbl GUI sender).
- Banner: `GrblHAL <version> ['$' for help]`. Capture:
  - `$$` (record originals), `$I` (build/board), `$pins` → **confirm** X step=**2**, X dir=**5**, enable=**8**,
    spindle PWM=**15**. If they differ, you flashed the wrong map — reflash the generic build.

## 4. Behavioural config (runtime `$`-settings)

```
$X            ; clear startup ALARM (homing off, expected)
$22=0         ; homing off (manual home, v1)
$20=0         ; soft limits off
$21=0         ; hard limits off
$100=80       ; X steps/mm  -> 80 for 20T pulley, 100 for 16T   (= 1600 / teeth)
$110=1000     ; X max rate mm/min (conservative for the ~120 g gondola)
$120=50       ; X accel mm/s^2 (gentle)
```

Enable: A4988 EN is **active-LOW**. Simplest on the bench — **tie EN→GND** (always energised) and ignore it.
If you wired EN→GP8 and the motor won't hold, toggle the stepper-enable-invert `$4`.

### No endstops / E-stop wired? Clear the phantom alarm (error:79)

grblHAL treats control + limit inputs as **normally-closed**, so *unconnected* pins read as **triggered** — a
floating E-stop reports `Pn:...E...` and gives **`error:79`** on `$X` (and `error:9` on M-codes). With nothing
wired, invert those inputs so open = inactive, then **soft-reset** to clear the *latched* E-stop:

```
$5=7      ; invert X/Y/Z limit inputs   (X=1 + Y=2 + Z=4)
$6=1      ; invert probe
$14=70    ; invert control inputs  (feed-hold 2 + cycle-start 4 + e-stop 64)
0x18      ; Ctrl-X soft-reset  -> re-arms with E-stop now reading inactive
$X        ; -> [MSG:Caution: Unlocked], then ? shows <Idle>
```

Verified on this build (grblHAL 1.1f): after this, `?` reports `<Idle>` with no `Pn` field. Revisit `$5`/`$14`
if you later wire real NC/NO switches. (Drive all this from `../tools/grbl.py`, e.g.
`python tools/grbl.py --port COM3 '$14=70' RESET '$X' '?'`.)

## 5. Jog the stepper, verify steps/mm

1. Mark the belt (tape flag) against a ruler.
2. `$J=G91 G21 X100 F500`  — relative, mm, X +100 mm at 500 mm/min. Motor turns; belt advances.
3. **Measure actual travel.** Off? `corrected = 80 × (100 / measured_mm)`. A 1.25× miss means the other pulley
   (16T → 100) or a microstep slip. Set `$100`, re-jog, land on 100. This physically settles the 16T-vs-20T question.
4. **Direction:** if `X+` reels the wrong way, flip the axis bit in `$3`.
5. Return: `$J=G91 X-100 F500`.

## 6. Second motor (later)

When motor 2 goes in: Y STEP → **GP3** (consecutive with X), Y DIR → **GP6**, set `$101`, verify as in §5.

## 7. Pen servo (GP15, spindle PWM)

```
$33=50   $34=5   $35=5   $36=10   ; RC-servo timing: 50 Hz, ~1-2 ms pulse
$32=0                             ; laser mode off -> servo HOLDS position
M3 S400      ; pen toward one end - watch it move
M3 S700      ; the other end; find 'down' (just touching) and 'up' (~3-5 mm clear)
M5           ; release
```

Sweep S (`0`..`$30`, default 1000) to find pen-down / pen-up values; record them in `../profiles/machine.toml`
`[pen]`. Don't drive past the servo's mechanical stop (it buzzes/heats).

## 8. Record results

Into `../profiles/machine.toml`: confirmed `pulley_teeth` + verified `steps/mm`, measured `motor_spacing_mm`
(`D`, hardware §H), pen `servo_up`/`servo_down`. **Exit M2** when the motor jogs accurate distances in the right
direction and the servo lifts/drops cleanly — the reproducible record (project doc success criterion).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `$pins` shows different GPIOs | wrong board map flashed | rebuild generic (no board) and reflash |
| Motor buzzes/locks, no motion | step pin / current | `$pins` vs `pinmap.md`; check Vref (hardware §C-C) |
| Motor spins free / won't hold | enable polarity | tie EN→GND, or toggle `$4` |
| Wrong direction | direction mask | flip the axis bit in `$3` |
| Half / double the mm | steps/mm wrong | pulley teeth + MS jumpers; re-set `$100` |
| `ALARM` on connect | homing/limits | `$X` (homing disabled for the bench) |
| Servo jitters / won't hold | laser mode / PWM freq | `$32=0`, `$33=50`; check `$34/$35/$36` |

## Sources

- grblHAL/RP2040 driver + flashing — github.com/grblHAL/RP2040
- pin numbers — boards/generic_map.h (grblHAL/RP2040)
- RC-servo settings — OpenBuilds "connect RC servo"; buildlog.net "Grbl spindle PWM to control a servo"
