#!/usr/bin/env bash
# PolarGraph Studio - double-click to launch (macOS).
# Needs Python 3.11+ (python3); pyserial only for plotting: pip3 install pyserial
# First time: make it executable ->  chmod +x studio.command
cd "$(dirname "$0")"
export PYTHONPATH=src
python3 -m polargraph serve --open
