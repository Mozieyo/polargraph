"""Streamer flow-control test against a mock grbl (no hardware).

Verifies character-counting accounts for every line and that errors surface.
Run: python tests/test_sender.py  (or pytest). ASCII output (cp949-safe).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polargraph.sender import _program, _run  # noqa: E402


class MockGrbl:
    """Minimal grbl: 'ok' for each command line, status for '?', errors for `bad` lines."""

    def __init__(self, bad: set[str] | None = None):
        self.buf = b""
        self.q: list[bytes] = []
        self.bad = bad or set()
        self.lines_seen = 0

    def write(self, b: bytes):
        for byte in b:
            ch = bytes([byte])
            if ch == b"?":
                self.q.append(b"<Idle|MPos:0.000,0.000,0.000|Bf:100,1023>\n")
                continue
            if ch in (b"!", b"~", b"\x18"):
                continue
            self.buf += ch
            if ch == b"\n":
                line = self.buf.decode("ascii", "replace").strip()
                self.buf = b""
                if not line:
                    continue
                self.lines_seen += 1
                self.q.append(b"error:20\n" if line in self.bad else b"ok\n")

    def flush(self):
        pass

    def readline(self) -> bytes:
        return self.q.pop(0) if self.q else b""

    def reset_input_buffer(self):
        self.q.clear()

    def close(self):
        pass


def test_all_lines_acked() -> None:
    prog = _program(["G21", "G90", "G1 X1 Y1 F100", "G1 X2 Y2 F100", "M5"])
    m = MockGrbl()
    rc = _run(m, prog, preamble=["$X"], status_every=100)
    assert rc == 0, f"expected clean exit, got {rc}"
    # 5 program lines + 1 preamble ($X) all reached the controller
    assert m.lines_seen == 6, f"controller saw {m.lines_seen} lines"


def test_error_is_reported() -> None:
    prog = _program(["G1 X1 Y1", "BADCMD", "M5"])
    rc = _run(MockGrbl(bad={"BADCMD"}), prog, preamble=[], status_every=100)
    assert rc == 1, f"expected error exit (1), got {rc}"


def _main() -> int:
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'OK' if not fails else 'FAILED'} - {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_main())
