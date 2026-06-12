# Polargraph

Software for a DIY glass-frame polargraph (hanging V-plotter) that draws archival pen art: a browser
**Studio** of generative/image art engines, and the PC-side pipeline that turns SVG into belt-length
G-code and streams it to an RP2040 running grblHAL.

- **Project doc:** [`polargraph_project_document.md`](polargraph_project_document.md) — goals, hardware, roadmap.
- **Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md) — the stack and the decisions.
- **Build journal:** [`BUILD_LOG.md`](BUILD_LOG.md) — the whole adventure, gremlins included.
- **Studio guide:** [`src/polargraph/studio/README.md`](src/polargraph/studio/README.md) — engines, masking, plotting.

## Quick start

**Windows (this PC):** double-click **`studio.bat`** — launches the Studio server and opens the browser.

**macOS:** copy the repo over (or `git clone`), then:
```sh
chmod +x studio.command     # first time only
./studio.command            # or double-click it in Finder
```

**Any machine, properly installed:**
```sh
pip install .               # or: pip install -e . for development
polargraph serve --open     # the Studio, from anywhere
```

Python 3.11+ required. `pyserial` (installed automatically with pip; or `pip install pyserial` for the
zero-install launchers) is only needed to actually plot.

## CLI

```sh
polargraph calib  -o calibration.svg      # calibration pattern (square + circles)
polargraph gcode  art.svg -o art.gcode    # SVG -> belt-length G-code
polargraph verify art.svg                 # no-hardware fidelity check + overlay
polargraph stream art.svg                 # stream a job to the machine
polargraph serve  --open                  # the Studio with the PLOT button
```

The machine profile resolves in this order: `--profile PATH` → `./profiles/machine.toml` →
`~/.polargraph/machine.toml` → the packaged default ([`src/polargraph/data/machine.toml`](src/polargraph/data/machine.toml)).
On a new machine, copy your calibrated `profiles/machine.toml` to `~/.polargraph/machine.toml`.

## Layout

```
src/polargraph/        the package: geometry/IK, segmenting, gcode, sender, server, CLI
src/polargraph/studio/ the browser Studio (single file, no build step)
src/polargraph/data/   packaged default machine profile
profiles/              this machine's working profile (calibration record)
firmware/              grblHAL bring-up guide + pin map
tools/                 grbl serial console, bench self-test, studio launcher
tests/                 unit tests (geometry, pipeline, sender vs mock grbl)
```

## Status

Machine built and bench-proven (M2/M3 done, first calibration plot drawn). Studio + pipeline + streaming
verified end-to-end. In progress: gravity pen-holder rebuild, then geometry recalibration for gift-grade plots.
