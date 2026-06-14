@echo off
rem PolarGraph calibration - double-click (Windows). Homes, plots a 1cm grid, solves the fix.
rem Needs Python 3.11+ and pyserial (pip install pyserial) with the machine plugged in.
cd /d "%~dp0"
set PYTHONPATH=src
echo ============================================================
echo   PolarGraph calibration
echo   Homes, plots a 20x20 cm grid of 1 cm squares, you measure
echo   it, and it solves the size/aspect fix.
echo ============================================================
echo.
echo [1/4] Generating the grid (1 cm cells over a 200 mm square)...
python -m polargraph calib --grid --square 200 --cell 10 --out calibration.svg
if errorlevel 1 goto err
echo.
echo [2/4] Homing, then plotting the grid.
echo   - Machine on, pen mounted, paper in place.
echo   - It HOMES to the endstops first, so the grid lands at a known spot.
echo.
pause
python -m polargraph stream calibration.svg --auto-home
if errorlevel 1 goto err
echo.
echo [3/4] Measure the grid's OUTER border with a ruler (in mm).
echo       (the inner 1 cm cells show local warp - photograph them for the warp fix)
set /p W=        measured WIDTH  (mm):
set /p H=        measured HEIGHT (mm):
echo.
echo [4/4] Solving...
echo.
python -m polargraph calib-solve --commanded 200 200 --measured %W% %H%
if errorlevel 1 goto err
echo.
echo ------------------------------------------------------------
echo Apply the two values above:
echo   * motor_spacing_mm  -^> profiles\machine.toml  [geometry]
echo   * $100 / $101       -^> type into the Studio serial box (right column),
echo                          e.g.  $100=109.9  then  $101=109.9
echo Then run this again to confirm the border measures 200 x 200.
echo If cells warp unevenly across the sheet, send a photo - that maps the
echo distortion field for a geometric (per-region) correction.
echo ------------------------------------------------------------
echo.
pause
goto end
:err
echo.
echo Something failed. Check: Python installed, pyserial installed, machine plugged in,
echo and that homing moved the right way (see [homing] in profiles\machine.toml).
pause
:end
