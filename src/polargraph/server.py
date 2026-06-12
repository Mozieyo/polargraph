"""Studio server: serves the frontend and plots straight from the browser.

    polargraph serve            (or: python tools/studio_server.py)

Endpoints (same-origin with the served Studio page):
    GET  /                static frontend/
    GET  /status          {state, acked, total, errors, detail}
    POST /plot?mode=placed|here[&dry=1]   body = SVG text
    POST /stop            abort the running job (feed-hold + soft-reset)

One job at a time; the stream runs in a background thread with the sender's
progress/abort callbacks feeding /status. ASCII-only output (cp949 console).
"""

from __future__ import annotations

import io
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import gcode as gc
from . import sender
from .profile import Profile
from .svgio import read_svg

# The studio ships inside the package, so this path holds in dev AND installed.
FRONTEND = Path(__file__).resolve().parent / "studio"

JOB = {"state": "idle", "acked": 0, "total": 0, "errors": 0, "detail": "", "abort": False}
_LOCK = threading.Lock()
SERIAL = {"port": None}


def _progress(acked, total, errors):
    JOB.update(acked=acked, total=total, errors=errors)


def _job(lines, preamble):
    try:
        rc = sender.stream(lines, port=SERIAL["port"], preamble=preamble,
                           progress=_progress, should_abort=lambda: JOB["abort"])
        JOB["state"] = {0: "done", 130: "aborted"}.get(rc, "error")
        JOB["detail"] = "" if rc == 0 else f"exit {rc}"
    except Exception as e:  # noqa: BLE001
        JOB.update(state="error", detail=str(e))


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
            return self._json(200, {k: JOB[k] for k in
                                    ("state", "acked", "total", "errors", "detail")})
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        if self.path.startswith("/stop"):
            JOB["abort"] = True
            return self._json(200, {"ok": True})
        if self.path.startswith("/plot"):
            return self._plot(body)
        return self._json(404, {"error": "unknown endpoint"})

    def _plot(self, svg_text):
        dry = "dry=1" in self.path
        mode = "here" if "mode=here" in self.path else "placed"
        layer = -1
        m = __import__("re").search(r"layer=(\d+)", self.path)
        if m:
            layer = int(m.group(1))
        with _LOCK:
            if not dry and JOB["state"] == "plotting":
                return self._json(409, {"error": "already plotting"})
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
            pre = ["$X"]
            if mode == "here":
                t = gc.first_target(lines)
                if t:
                    pre.append(f"G92 X{t[0]:.3f} Y{t[1]:.3f}")
            JOB.update(state="plotting", acked=0, total=stats["segments"],
                       errors=0, detail="", abort=False)
            threading.Thread(target=_job, args=(lines, pre), daemon=True).start()
            return self._json(200, {"ok": True, **stats})


def run(http_port=8770, serial_port=None, open_browser=False):
    SERIAL["port"] = serial_port
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
