# Calibration — fixing a stretched or wrong-sized plot

If a square comes out as a rectangle, or your art is stretched (e.g. a 13:10 image
prints as 93:85), the machine's two constants are off:

- **`motor_spacing_mm`** (distance between the two pulleys) sets the **aspect ratio**.
- **steps/mm** (grbl `$100`/`$101`) sets the **absolute size**.

A square + a measurement is enough to solve both.

## Easiest: double-click the runner

- **Windows:** double-click **`calibrate.bat`**
- **macOS:** double-click **`calibrate.command`** (first time: right-click → Open)

It will:

1. Generate a **20 × 20 cm grid of 1 cm squares**.
2. **Home** the machine to the endstops, then plot the grid.
3. Ask for the **measured width and height** of the outer border (ruler, outer edges).
4. Print the corrected `motor_spacing_mm` and `$100`/`$101`.

> The grid homes first, so it lands at a known spot. The **outer border** (200 mm) gives
> the size/aspect fix; the **inner 1 cm cells** reveal *local* warp — if cells stretch or
> skew unevenly across the sheet, photograph it and we map the distortion field for a
> per-region geometric correction (beyond the single motor-spacing/steps fix).

## Apply the result

The runner prints two lines, e.g.:

```
profiles/machine.toml  [geometry] motor_spacing_mm = 350.1   (was 318.0)
grbl board             $100=109.884  $101=109.884            (was 100.000)
```

1. **`motor_spacing_mm`** → edit `profiles/machine.toml`, `[geometry]` section.
2. **`$100` / `$101`** → in the Studio's right-column **serial box**, type each and press SEND:
   ```
   $100=109.884
   $101=109.884
   ```
   grbl saves these to flash automatically — they survive power-off.

Then run the calibration again to confirm the square measures **100 × 100**. Two passes
usually nails it.

## Manual (no double-click)

```
python -m polargraph calib --grid --square 200 --cell 10 --out calibration.svg
python -m polargraph stream calibration.svg --auto-home   # home, then plot
# measure the outer border W and H, then:
python -m polargraph calib-solve --commanded 200 200 --measured <W> <H>
```

(Set `PYTHONPATH=src` first if running from the repo, like the launchers do.)

## Why a square works

`motor_spacing` mostly skews the **aspect** (width ÷ height); steps/mm scales the
**overall size**. The solver simulates the V-plotter kinematics and searches for the
`(motor_spacing, steps/mm)` pair that turns your commanded 100 × 100 into exactly the
width × height you measured — so one rectangle pins down both.
