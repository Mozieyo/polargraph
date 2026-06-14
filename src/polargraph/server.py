"""Studio server: serves the frontend and plots straight from the browser.

    polargraph serve            (or: python tools/studio_server.py)

Endpoints (same-origin with the served Studio page):
    GET  /                static studio/
    GET  /status          live job + grbl state, position, log, ETA
    POST /plot?mode=placed|here[&dry=1][&layer=N]   body = SVG text
    POST /console[?quiet=1]   body = one grbl/G-code line -> reply lines
    POST /stop            abort the running job (feed-hold + soft-reset)

One job at a time; the stream runs in a background thread with the sender's
progress/log/status callbacks feeding /status. /console is the manual serial
monitor - it talks to the board only while no plot is running. ASCII-only
output (cp949 console).
"""

from __future__ import annotations

import io
import json
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import gcode as gc
from . import sender
from .profile import Profile
from .svgio import read_svg

# The studio ships inside the package, so this path holds in dev AND installed.
FRONTEND = Path(__file__).resolve().parent / "studio"

LOG_MAX = 200
_LOCK = threading.Lock()          # guards job start (one job at a time)
_SERIAL_LOCK = threading.Lock()   # serializes ALL port access (session vs console)
_CONTINUE = threading.Event()     # set by /continue to advance a pen-check pause
SERIAL = {"port": None}

# states where the machine owns the port and must not be interrupted by a console
_ACTIVE = {"plotting", "homing", "centering", "pen_attach", "pen_position"}

JOB = {
    "state": "idle",      # idle | homing | centering | pen_attach | pen_position
    "task": "",           #        | plotting | done | aborted | error
    "acked": 0, "total": 0, "errors": 0,
    "detail": "",
    "grbl": "",           # grbl run-state word: Idle/Run/Hold/Alarm/...
    "mpos": [None, None],  # belt lengths X=L1, Y=L2 (mm)
    "feed": 0, "spindle": 0,
    "eta_s": None,
    "est_min": 0.0, "draw_mm": 0.0, "travel_mm": 0.0,
    "log": [],
    "port": None,
    "abort": False,
    "pause": False,       # feed-hold requested? (stream issues ! / ~)
    "motors": True,       # steppers energized? (False after end-of-job $SLP)
    "_t0": 0.0,
}


def _busy():
    return JOB["state"] in _ACTIVE

# stream() exit code -> (lifecycle state, human detail)
_RC = {0: ("done", "complete"), 130: ("aborted", "stopped"),
       2: ("error", "pyserial not installed"),
       3: ("error", "no machine / port busy")}


def _logline(msg: str) -> None:
    log = JOB["log"]
    log.append(f"{time.strftime('%H:%M:%S')}  {msg.strip()}")
    del log[:-LOG_MAX]


_STATUS_RE = re.compile(r"<([^|>]+)")
_MPOS_RE = re.compile(r"MPos:(-?[\d.]+),(-?[\d.]+)")
_FS_RE = re.compile(r"FS:(\d+),(\d+)")


def _parse_status(raw: str) -> None:
    """Pull the run-state word, belt position and feed/spindle out of a <...> line."""
    m = _STATUS_RE.search(raw)
    if m:
        JOB["grbl"] = m.group(1).strip()
    m = _MPOS_RE.search(raw)
    if m:
        JOB["mpos"] = [round(float(m.group(1)), 2), round(float(m.group(2)), 2)]
    m = _FS_RE.search(raw)
    if m:
        JOB["feed"], JOB["spindle"] = int(m.group(1)), int(m.group(2))


def _progress(acked, total, errors):
    JOB.update(acked=acked, total=total, errors=errors)
    elapsed = time.time() - JOB["_t0"]
    frac = acked / total if total else 0.0
    # warm-up: the first acks carry pen-settle dwells and skew a live-rate ETA,
    # so lean on the model estimate until enough lines have flowed, then blend
    # into the measured rate (which also captures the real dwell overhead).
    model = JOB["est_min"] * 60.0 * (1.0 - frac)
    if acked >= 8 and elapsed > 1.0:
        live = (total - acked) * elapsed / acked
        w = min(1.0, (acked - 8) / 40.0)  # ramp model->live over ~40 lines
        JOB["eta_s"] = max(0.0, (1.0 - w) * model + w * live)
    else:
        JOB["eta_s"] = max(0.0, model)


def _finish(state, detail):
    JOB["state"], JOB["detail"] = state, detail
    JOB["task"] = {"done": "idle", "aborted": "stopped"}.get(state, "error")
    JOB["eta_s"] = 0.0
    return state


def _wait_continue(timeout=900.0):
    """Block a pen-check pause until /continue (returns True) or abort (False)."""
    _CONTINUE.clear()
    end = time.time() + timeout
    while time.time() < end:
        if JOB["abort"]:
            return False
        if _CONTINUE.wait(0.2):
            return True
    return False


def _session(plan):
    """One serial session: wake -> [home -> centre -> pen-check] -> [stream] ->
    [disable steppers]. Each phase updates JOB so the monitor reflects it live."""
    prof = plan["prof"]
    geo = prof.geometry
    abort = lambda: JOB["abort"]  # noqa: E731
    pen_up = f"M3 S{prof.pen_up_s:.0f}"
    pen_down = f"M3 S{prof.pen_down_s:.0f}"
    try:
        with _SERIAL_LOCK:  # never open the port while a console ping holds it
            ser, port = sender.open_port(SERIAL["port"])
            JOB["port"], JOB["motors"] = port, True
            try:
                sender.wake(ser, on_log=_logline)
                if abort():
                    return _finish("aborted", "stopped")

                if plan["home"]:
                    JOB.update(state="homing", task="homing to endstops", grbl="")
                    if not sender.home(ser, prof, on_log=_logline,
                                       on_status=_parse_status, should_abort=abort):
                        return _finish("aborted" if abort() else "error",
                                       "homing stopped" if abort() else "homing failed")
                    JOB.update(state="centering", task="moving to centre")
                    l1, l2 = geo.ik(*prof.center_xy)
                    sender._send_and_wait(ser, pen_up)
                    sender._send_and_wait(ser, f"G0 X{l1:.3f} Y{l2:.3f}")
                    sender._wait_idle(ser)
                    _logline(f"# centred at ({prof.center_xy[0]:.0f},{prof.center_xy[1]:.0f})")

                if plan["pen_check"]:
                    JOB.update(state="pen_attach",
                               task="pen raised - attach the pen, then press Continue")
                    sender._send_and_wait(ser, pen_up)
                    if not _wait_continue():
                        return _finish("aborted", "stopped")
                    JOB.update(state="pen_position",
                               task="pen lowered - set it just touching, then press Continue")
                    sender._send_and_wait(ser, pen_down)
                    if not _wait_continue():
                        return _finish("aborted", "stopped")
                    sender._send_and_wait(ser, pen_up)

                if plan["program"] is not None:
                    JOB.update(state="plotting", task=plan["task"], acked=0,
                               errors=0, detail="", _t0=time.time())
                    rc = sender._run(ser, sender._program(plan["program"]),
                                     plan["preamble"], progress=_progress,
                                     should_abort=abort, on_log=_logline,
                                     on_status=_parse_status,
                                     is_paused=lambda: JOB["pause"])
                    _finish(*_RC.get(rc, ("error", f"exit {rc}")))
                    # post-print: lift the pen and park at the start point
                    if JOB["state"] == "done" and plan.get("return_to"):
                        JOB["task"] = "pen up, returning to start"
                        sender._send_and_wait(ser, pen_up)
                        rl1, rl2 = plan["return_to"]
                        sender._send_and_wait(ser, f"G0 X{rl1:.3f} Y{rl2:.3f}")
                        sender._wait_idle(ser)
                        _logline("# pen up, returned to start")
                else:
                    _finish("done", "homed & centred")

                if plan["disable_after"] and JOB["state"] == "done":
                    sender.disable_steppers(ser, on_log=_logline)
                    JOB["motors"] = False
            finally:
                ser.close()
    except Exception as e:  # noqa: BLE001
        JOB.update(state="error", detail=str(e), task="error")
        _logline(f"!! {e}")


def _status_view():
    keys = ("state", "task", "acked", "total", "errors", "detail", "grbl",
            "mpos", "feed", "spindle", "eta_s", "est_min", "draw_mm",
            "travel_mm", "port", "motors", "pause")
    out = {k: JOB[k] for k in keys}
    out["log"] = JOB["log"][-40:]
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(FRONTEND), **kw)

    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            return self._json(200, _status_view())
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        if self.path.startswith("/stop"):
            JOB["abort"] = True
            _CONTINUE.set()  # also release any pen-check pause
            _logline("# stop requested")
            return self._json(200, {"ok": True})
        if self.path.startswith("/continue"):
            _CONTINUE.set()
            return self._json(200, {"ok": True})
        if self.path.startswith("/pause"):
            JOB["pause"] = not JOB["pause"]
            _logline("# pause" if JOB["pause"] else "# resume")
            return self._json(200, {"ok": True, "paused": JOB["pause"]})
        if self.path.startswith("/home"):
            return self._home()
        if self.path.startswith("/motors"):
            return self._motors()
        if self.path.startswith("/console"):
            return self._console(body)
        if self.path.startswith("/plot"):
            return self._plot(body)
        return self._json(404, {"error": "unknown endpoint"})

    def _motors(self):
        """Toggle the stepper drivers: $SLP off, or wake them back on. Idle only."""
        if _busy():
            return self._json(409, {"error": "machine busy"})
        try:
            with _SERIAL_LOCK:
                ser, port = sender.open_port(SERIAL["port"])
                JOB["port"] = port
                try:
                    if JOB["motors"]:
                        sender.disable_steppers(ser, on_log=_logline)
                        JOB["motors"] = False
                    else:
                        sender.wake(ser, on_log=_logline)
                        JOB["motors"] = True
                finally:
                    ser.close()
        except Exception as e:  # noqa: BLE001
            return self._json(400, {"error": str(e)})
        return self._json(200, {"ok": True, "motors": JOB["motors"]})

    def _home(self):
        with _LOCK:
            if _busy():
                return self._json(409, {"error": "machine busy"})
            prof = Profile.load()
            if prof.homing is None:
                return self._json(400, {"error": "no [homing] section in profile"})
            plan = {"prof": prof, "home": True, "pen_check": False, "program": None,
                    "preamble": [], "task": "homing", "disable_after": False}
            JOB.update(state="homing", task="homing to endstops", acked=0, total=0,
                       errors=0, detail="", eta_s=None, abort=False, pause=False,
                       motors=True)
            _logline("# HOME requested")
            threading.Thread(target=_session, args=(plan,), daemon=True).start()
            return self._json(200, {"ok": True})

    def _console(self, body):
        """Manual serial monitor: one line in, reply lines out. Idle only."""
        if _busy():
            return self._json(409, {"error": "machine busy"})
        cmd = body.strip()
        if not cmd:
            return self._json(400, {"error": "empty command"})
        quiet = "quiet=1" in self.path
        # non-blocking: if a plot is mid-open we already returned 409 above; this
        # lock only closes the brief stream-start vs ping race.
        with _SERIAL_LOCK:
            port, lines = sender.console(cmd, port=SERIAL["port"])
        if port:
            JOB["port"] = port
        for ln in lines:
            if ln.startswith("<"):
                _parse_status(ln)
        if not quiet:
            _logline(f"> {cmd}")
            for ln in lines:
                _logline(f"  {ln}")
        return self._json(200, {"ok": True, "port": port, "lines": lines})

    def _plot(self, svg_text):
        dry = "dry=1" in self.path
        mode = "here" if "mode=here" in self.path else "placed"
        layer = -1
        m = re.search(r"layer=(\d+)", self.path)
        if m:
            layer = int(m.group(1))
        with _LOCK:
            if not dry and _busy():
                return self._json(409, {"error": "machine busy"})
            try:
                prof = Profile.load()  # cwd profile > ~/.polargraph > packaged default
                layers = read_svg(io.StringIO(svg_text))
                if layer >= 0:  # single pass for pen swaps (multicolor)
                    if layer >= len(layers):
                        return self._json(400, {"error": f"no pass {layer + 1} (art has {len(layers)})"})
                    layers = [layers[layer]]
                lines, stats = gc.generate(layers, prof)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"error": str(e)})
            if not stats["segments"]:
                return self._json(400, {"error": "no drawable paths in SVG"})
            if dry:
                return self._json(200, {"ok": True, "dry": True, **stats})

            task = "drawing" if layer < 0 else f"drawing pass {layer + 1}"
            if mode == "here":              # start where the pen is: G92 to first target
                pre = []
                t = gc.first_target(lines)
                if t:
                    pre = [f"G92 X{t[0]:.3f} Y{t[1]:.3f}"]
                plan = {"prof": prof, "home": False, "pen_check": False,
                        "program": lines, "preamble": pre, "task": task,
                        "disable_after": True, "return_to": t}
                first_state = "plotting"
            else:                           # placed: home -> centre -> pen-check -> draw
                if prof.homing is None:
                    return self._json(400, {"error": "no [homing] section in profile - "
                                                     "use 'start from current spot'"})
                plan = {"prof": prof, "home": True, "pen_check": True,
                        "program": lines, "preamble": [], "task": task,
                        "disable_after": True,
                        "return_to": prof.geometry.ik(*prof.homing.home_xy)}
                first_state = "homing"

            JOB.update(state=first_state, task="starting", acked=0,
                       total=stats["segments"], errors=0, detail="",
                       eta_s=stats["est_min"] * 60.0, est_min=stats["est_min"],
                       draw_mm=stats["draw_mm"], travel_mm=stats["travel_mm"],
                       abort=False, pause=False, _t0=time.time(), motors=True)
            _logline(f"# {'homed' if plan['home'] else mode} plot: {stats['segments']} segs, "
                     f"~{stats['est_min']:.1f} min"
                     + (f", 2-opt saved {stats['opt_saved_mm']:.0f}mm"
                        if stats.get("opt_saved_mm") else ""))
            threading.Thread(target=_session, args=(plan,), daemon=True).start()
            return self._json(200, {"ok": True, **stats})


def run(http_port=8770, serial_port=None, open_browser=False):
    SERIAL["port"] = serial_port
    JOB["port"] = serial_port
    srv = ThreadingHTTPServer(("127.0.0.1", http_port), Handler)
    url = f"http://127.0.0.1:{http_port}"
    print(f"# studio server: {url}  (frontend: {FRONTEND})")
    print("# POST /plot streams to the machine - Ctrl-C to quit")
    if open_browser:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n# bye")
