"""Stream G-code to grbl / grblHAL with character-counting flow control.

Keeps grbl's serial RX buffer full (so the planner look-ahead stays fed) without
overflowing it: track the byte-length of every sent-but-unacked line and only send
the next line while it still fits; each ack ('ok'/'error:') frees the oldest line.
Real-time bytes ('?', '!', '~', 0x18) bypass the line buffer. Ctrl-C feed-holds
then soft-resets. ASCII output (cp949-safe).

`_run(ser, ...)` works on any serial-like object, so the flow control is testable
against a mock grbl with no hardware (see tests/test_sender.py).
"""

from __future__ import annotations

import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None

RX_BUFFER = 1024  # grblHAL RX buffer (status shows "Bf:...,1023")


def find_port(explicit=None):
    if explicit:
        return explicit
    if serial is None:
        return None
    cands = [p.device for p in list_ports.comports()
             if any(k in (str(p.description) + " " + str(p.hwid)).lower()
                    for k in ("2e8a", "pico", "rp2040", "usb serial", "cdc"))]
    return cands[0] if len(cands) == 1 else None


def _program(lines):
    out = []
    for ln in lines:
        ln = ln.split(";", 1)[0].strip()
        if ln:
            out.append(ln)
    return out


def _send_and_wait(ser, line, timeout=4.0):
    ser.write((line + "\n").encode())
    ser.flush()
    end = time.time() + timeout
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        t = raw.decode("ascii", "replace").strip()
        if t == "ok" or t.startswith("error:"):
            print(f"  {line} -> {t}")
            return t
    print(f"  {line} -> (no ack)")
    return None


def _run(ser, prog, preamble=None, rx_buffer=RX_BUFFER, status_every=1.0,
         progress=None, should_abort=None):
    """Character-counting stream loop over an open serial-like object. Returns exit code.

    ``progress(acked, total, errors)`` is called about once per second;
    ``should_abort()`` is polled each loop - return True to feed-hold + reset.
    """
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b"\r\n")
    time.sleep(0.2)
    ser.reset_input_buffer()
    print(f"# streaming {len(prog)} lines")
    for p in (preamble or []):
        _send_and_wait(ser, p)

    inflight = []
    i = acked = sent = errors = 0
    t0 = time.time()
    last = 0.0
    try:
        while acked < len(prog):
            if should_abort is not None and should_abort():
                print("\n# ABORT (requested): feed-hold + soft-reset")
                ser.write(b"!")
                time.sleep(0.3)
                ser.write(b"\x18")
                time.sleep(0.3)
                return 130
            while i < len(prog):
                need = len(prog[i]) + 1
                if sum(inflight) + need >= rx_buffer:
                    break
                ser.write((prog[i] + "\n").encode())
                ser.flush()
                inflight.append(need)
                i += 1
                sent += 1
            raw = ser.readline()
            if raw:
                t = raw.decode("ascii", "replace").strip()
                if t == "ok":
                    if inflight:
                        inflight.pop(0)
                    acked += 1
                elif t.startswith("error:"):
                    if inflight:
                        inflight.pop(0)
                    acked += 1
                    errors += 1
                    print(f"\n  ! {t} on line {acked}: {prog[acked - 1]}")
                elif t.startswith("ALARM"):
                    print(f"\n  !! {t} - aborting")
                    break
            now = time.time()
            if now - last >= status_every:
                last = now
                try:
                    ser.write(b"?")
                    ser.flush()
                except Exception:  # noqa: BLE001
                    pass
                if progress is not None:
                    progress(acked, len(prog), errors)
                print(f"\r  {acked}/{len(prog)} ({100.0 * acked / len(prog):3.0f}%)"
                      f"  err={errors}   ", end="", flush=True)
        print(f"\r  {acked}/{len(prog)} (100%)  err={errors}        ")
        print(f"# done in {time.time() - t0:.1f}s, {errors} error(s)")
        if progress is not None:
            progress(acked, len(prog), errors)
        return 1 if errors else 0
    except KeyboardInterrupt:
        print("\n# ABORT: feed-hold + soft-reset")
        ser.write(b"!")
        time.sleep(0.3)
        ser.write(b"\x18")
        time.sleep(0.3)
        return 130


def stream(lines, port=None, baud=115200, preamble=None, rx_buffer=RX_BUFFER,
           status_every=1.0, progress=None, should_abort=None):
    """Find/open the port and stream G-code lines. Returns an exit code (0 = clean)."""
    if serial is None:
        print("pyserial not installed: python -m pip install pyserial")
        return 2
    port = find_port(port)
    if not port:
        print("no Pico found - plug in USB and power, or pass --port COMx")
        return 3
    prog = _program(lines)
    if not prog:
        print("nothing to send")
        return 1
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:  # noqa: BLE001
        print(f"could not open {port}: {e}  (serial monitor open?)")
        return 3
    print(f"# port {port} @ {baud}")
    with ser:
        return _run(ser, prog, preamble, rx_buffer, status_every,
                    progress=progress, should_abort=should_abort)
