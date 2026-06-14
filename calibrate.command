#!/usr/bin/env bash
# PolarGraph calibration - double-click (macOS). Homes, plots a 1cm grid, solves the fix.
# Needs Python 3.11+ and pyserial (pip3 install pyserial) with the machine plugged in.
cd "$(dirname "$0")" || exit 1
export PYTHONPATH=src
echo "============================================================"
echo "  PolarGraph calibration"
echo "  Homes, plots a 15x15 cm grid of 1 cm squares, you measure"
echo "  it, and it solves the size/aspect fix."
echo "============================================================"
echo
echo "[1/4] Generating the grid (1 cm cells over a 150 mm square)..."
python3 -m polargraph calib --grid --square 150 --cell 10 --out calibration.svg || { echo "failed"; read -n1 -s; exit 1; }
echo
echo "[2/4] Homing, then plotting the grid."
echo "  - Machine on, pen mounted, paper in place."
echo "  - It HOMES to the endstops first, so the grid lands at a known spot."
echo
read -p "Press Enter to home + plot..." _
python3 -m polargraph stream calibration.svg --auto-home || { echo "failed"; read -n1 -s; exit 1; }
echo
echo "[3/4] Measure the grid's OUTER border with a ruler (in mm)."
echo "      (the inner 1 cm cells show local warp - photograph them for the warp fix)"
read -p "        measured WIDTH  (mm): " W
read -p "        measured HEIGHT (mm): " H
echo
echo "[4/4] Solving..."
echo
python3 -m polargraph calib-solve --commanded 150 150 --measured "$W" "$H"
echo
echo "------------------------------------------------------------"
echo "Apply the two values above:"
echo "  * motor_spacing_mm  -> profiles/machine.toml  [geometry]"
echo "  * \$100 / \$101       -> type into the Studio serial box (right column),"
echo "                         e.g.  \$100=109.9  then  \$101=109.9"
echo "Then run this again to confirm the border measures 150 x 150."
echo "------------------------------------------------------------"
echo
read -n1 -s -p "Press any key to close..."
