# grblHAL pin map (RP2040) — verified against the driver's generic map

> **Correction to the hardware action plan.** On RP2040, grblHAL generates step pulses with the **PIO**, which
> requires the **STEP pins to sit on consecutive GPIOs**. The hardware doc's interleaved layout
> (step,dir,step,dir = GP2,3,4,5) puts the two STEP pins on GP2 **and GP4** (not consecutive), and the pen servo
> on GP7 (not the spindle-PWM pin) — a stock build can't drive that. Wire to the driver's **generic map** below
> instead; on a breadboard it's a few jumper moves. Verified against `grblHAL/RP2040 → boards/generic_map.h`.

## The map (generic_map.h)

| Signal | GPIO | Notes |
|---|---|---|
| X STEP | **GP2** | step base — PIO needs step pins consecutive |
| Y STEP | **GP3** | = base + 1 |
| (Z STEP) | GP4 | unused on the 2-axis plotter |
| X DIR | **GP5** | |
| Y DIR | **GP6** | |
| (Z DIR) | GP7 | unused |
| STEPPERS ENABLE | **GP8** | shared, **active-LOW**; or tie A4988 EN→GND for always-on bench |
| Pen servo | **GP15** | spindle PWM (AUXOUTPUT3) → RC servo |
| X LIMIT | GP9 | unused in v1 (homing off) |
| Y LIMIT | GP10 | unused in v1 |
| Probe | GP28 | unused |

## Servo (SG90 on the spindle PWM, GP15)

`$33=50` (50 Hz), `$34=5`, `$35=5`, `$36=10` (1–2 ms RC-servo pulse), and `$32=0` (laser mode off, so the servo
**holds** position during moves). Drive with `M3 S<val>` to set the pen angle and `M5` to release; sweep S
(`0`..`$30`, default 1000) to find pen-down (just touching) and pen-up (~3–5 mm clear), then record both.

## How it's configured

Pins are **compile-time** (board map) on RP2040 — use the grblHAL **Web Builder** with **no specific board**
selected (→ generic_map.h) and download the `.uf2`. Only behaviour is runtime `$`-settings: `$100/$101` steps/mm,
`$3` direction-invert, `$4` stepper-enable-invert, `$32`/`$33`–`$36` servo. Confirm the live map with `$pins`.

Source: `generic_map.h`, grblHAL/RP2040.
