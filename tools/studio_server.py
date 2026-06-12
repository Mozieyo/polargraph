#!/usr/bin/env python3
"""Launcher for the Studio server (frontend + PLOT button). Equivalent to
`polargraph serve` without needing an install. Usage: python tools/studio_server.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polargraph.cli import main  # noqa: E402

main(["serve"] + sys.argv[1:])
