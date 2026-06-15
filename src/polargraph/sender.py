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
    pico, loose = [], []
    for p in list_ports.comports():
        s = (str(p.description) + " " + str(p.hwid)).lower()
        if "2e8a" in s:  # Raspberry Pi USB VID - the RP2040 itself
            pico.append(p.device)
        elif any(k in s for k in ("pico", "rp2040", "usb serial", "cdc")):
            loose.append(p.device)
    cands = pico if pico else loose
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


def _state(status_line):
    return status_line.strip("<>").split("|")[0] if status_line else ""


def _pn(status_line):
    for part in status_line.strip("<>").split("|"):
        if part.startswith("Pn:"):
            return part[3:]
    return ""


def _query(ser, timeout=0.5):
    """Send '?' and return the next <...> status line (or '')."""
    ser.write(b"?")
    ser.flush()
    end = time.time() + timeout
    while time.time() < end:
        raw = ser.readline()
        if raw:
            t = raw.decode("ascii", "replace").strip()
            if t.startswith("<"):
                return t
    return ""


def _wait_idle(ser, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        if _state(_query(ser)).startswith(("Idle", "Alarm", "Check")):
            return True
        time.sleep(0.08)
    return False


def _wait_done(ser, timeout=30.0):
    """Wait for a *just-issued* move to finish. Unlike _wait_idle, this first waits for
    motion to BEGIN: grbl keeps reporting Idle for a few ms after it accepts a jog/move,
    before the planner starts it, so a naive idle-check races ahead while the move is
    still pending. We wait (briefly) for Run/Jog, then for Idle."""
    t_start = time.time() + 1.2          # let the planner start (a tiny move may finish first)
    while time.time() < t_start:
        st = _state(_query(ser))
        if st.startswith(("Run", "Jog", "Hold")):
            break
        if st.startswith("Alarm"):
            return False
        time.sleep(0.03)
    end = time.time() + timeout
    while time.time() < end:
        if _state(_query(ser)).startswith(("Idle", "Alarm", "Check")):
            return True
        time.sleep(0.05)
    return False


def open_port(port=None, baud=115200):
    """Open the machine's serial port. Returns ``(ser, port)``; raises on failure."""
    if serial is None:
        raise RuntimeError("pyserial not installed (python -m pip install pyserial)")
    port = find_port(port)
    if not port:
        raise RuntimeError("no machine found - plug in the Pico's USB and power")
    return serial.Serial(port, baud, timeout=0.1), port


def wake(ser, on_log=None):
    """Ready the board for a session: only soft-reset if it's asleep (from a prior
    $SLP) or unresponsive - a reset drops holding torque, so we avoid it when the
    board is already awake. Always $X-unlock afterwards."""
    s = _query(ser)
    if "Sleep" in s or not s:        # asleep / unresponsive -> needs a reset to revive
        ser.write(b"\x18")           # Ctrl-X soft reset
        time.sleep(0.6)
        ser.reset_input_buffer()
        if on_log:
            on_log("# board revived from sleep (reset)")
    _send_and_wait(ser, "$X")
    if on_log:
        on_log("# board ready")


def home(ser, profile, on_log=None, on_status=None, should_abort=None):
    """Lower the gondola to its endstops with BOTH belts paying out together, so it
    descends down the centreline and never wanders into the slack-belt zone (where a
    belt jumps the pulley). Each axis drops out of the jog as its switch trips; the
    other keeps going. Then back off and G92 to the homed belt lengths.

    Relies on hard limits being OFF ($21=0): jogging into a switch reports it on the
    Pn field without alarming, so we cancel the jog the instant it trips."""
    def log(m):
        print(m)
        if on_log:
            on_log(m)

    h = getattr(profile, "homing", None)
    if h is None:
        log("! no [homing] section in the profile - cannot home")
        return False

    _send_and_wait(ser, "$X")
    geo = profile.geometry
    pending = {"X": h.x_seek_sign, "Y": h.y_seek_sign}  # axes still seeking their switch

    def jog_pending():
        ser.reset_input_buffer()
        parts = " ".join(f"{ax}{sign * h.seek_mm:.1f}" for ax, sign in pending.items())
        ser.write(f"$J=G91 G21 {parts} F{h.feed_mm_min:.0f}\n".encode())
        ser.flush()

    log("# homing: lowering the gondola (both belts together) to the endstops")
    jog_pending()
    deadline = time.time() + (h.seek_mm / max(h.feed_mm_min, 1) * 60) + 8
    while pending and time.time() < deadline:
        if should_abort and should_abort():
            ser.write(b"\x85")
            log("# homing aborted")
            return False
        s = _query(ser)
        if on_status and s:
            on_status(s)
        pn = _pn(s)
        tripped = [ax for ax in pending if ax in pn]
        if tripped:
            ser.write(b"\x85")  # cancel the jog the instant a switch trips
            _wait_idle(ser)
            for ax in tripped:
                log(f"# {ax} endstop reached")
                del pending[ax]
            if pending:
                jog_pending()  # keep lowering whichever belt hasn't tripped yet
                deadline = time.time() + (h.seek_mm / max(h.feed_mm_min, 1) * 60) + 8
        time.sleep(0.04)

    if pending:
        log(f"! endstop(s) not reached: {','.join(pending)} - check direction/wiring")
        return False

    _wait_idle(ser)
    ser.write(f"$J=G91 G21 X{-h.x_seek_sign * h.pull_off_mm:.1f} "
              f"Y{-h.y_seek_sign * h.pull_off_mm:.1f} F{h.feed_mm_min:.0f}\n".encode())
    ser.flush()
    if not _wait_done(ser):   # pull-off MUST finish before we set the reference
        log("! pull-off did not complete cleanly")
    log(f"# both homed, backed off {h.pull_off_mm:.0f} mm")

    # set the home reference only once fully stopped, and confirm grbl accepted it
    # (a G92 sent while still moving is rejected/applied at the wrong spot - the cause of
    #  the intermittent "drifts to a weird angle / drives further down" homing failures)
    l1, l2 = geo.ik(*h.home_xy)
    if _send_and_wait(ser, f"G92 X{l1:.3f} Y{l2:.3f}") != "ok":
        _wait_done(ser)
        _send_and_wait(ser, f"G92 X{l1:.3f} Y{l2:.3f}")
    _send_and_wait(ser, "G90")   # restore absolute mode after the G91 jogs
    log(f"# home set: gondola ({h.home_xy[0]:.0f},{h.home_xy[1]:.0f}) -> L1={l1:.1f} L2={l2:.1f}")
    return True


def disable_steppers(ser, on_log=None):
    """Release the servo and de-energize the stepper drivers.

    1. M5            — release servo (stops PWM)
    2. $1=0          — set idle-disable timeout to zero so steppers cut off
                       when the motion buffer empties
    3. G91 G0 X0.001 — tiny relative move; when it completes the $1=0
                       timeout fires and actually disables the drivers
    4. $SLP          — put controller to sleep

    The G0 is the critical step — without a move the idle-disable
    mechanism never triggers.  M18/M84 are NOT used (grblHAL returns
    error:20 — unsupported command).

    The next session must call wake() (sends Ctrl-X soft reset) to bring
    the drivers back — wake() restores the pre-sleep $1 value.

    Prerequisites if steppers still hold:
      • A4988 EN pin wired to Pico GP8 (NOT GND).  firmware/pinmap.md
      • grbl $4=3  (invert X+Y enable — A4988 EN is active-LOW).
        Check with  > $4 ; set with  $4=3
    """
    _send_and_wait(ser, "M5")
    _send_and_wait(ser, "$1=0")
    _send_and_wait(ser, "G91 G0 X0.001")  # tiny move → idle → $1=0 fires
    ser.write(b"$SLP\n")
    ser.flush()
    time.sleep(0.3)
    if on_log:
        on_log("# steppers disabled (M5 + $1=0 + tiny move + $SLP)")
        on_log("# if motors still hold: check $4=3 (A4988 EN active-LOW), "
               "EN→GP8 (not GND)")


def _run(ser, prog, preamble=None, rx_buffer=RX_BUFFER, status_every=1.0,
         progress=None, should_abort=None, on_log=None, on_status=None,
         is_paused=None):
    """Character-counting stream loop over an open serial-like object. Returns exit code.

    ``progress(acked, total, errors)`` is called about once per second;
    ``should_abort()`` is polled each loop - return True to feed-hold + reset.
    ``on_log(text)`` receives notable events; ``on_status(raw)`` receives each
    grbl ``<...>`` status report (both optional, for a live monitor).
    """
    def log(msg):
        print(msg)
        if on_log:
            on_log(msg)

    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b"\r\n")
    time.sleep(0.2)
    ser.reset_input_buffer()
    log(f"# streaming {len(prog)} lines")
    for p in (preamble or []):
        r = _send_and_wait(ser, p)
        log(f"  {p} -> {r}")

    inflight = []
    i = acked = sent = errors = 0
    t0 = time.time()
    last = 0.0
    paused = False
    try:
        while acked < len(prog):
            if should_abort is not None and should_abort():
                print("\n# ABORT (requested): feed-hold + soft-reset")
                ser.write(b"!")
                time.sleep(0.3)
                ser.write(b"\x18")
                time.sleep(0.3)
                return 130
            if is_paused is not None:  # feed-hold (!) / resume (~) as the flag flips
                want = is_paused()
                if want and not paused:
                    ser.write(b"!")
                    paused = True
                    log("# paused (feed hold)")
                elif not want and paused:
                    ser.write(b"~")
                    paused = False
                    log("# resumed")
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
                    log(f"  ! {t} on line {acked}: {prog[acked - 1]}")
                elif t.startswith("ALARM"):
                    log(f"  !! {t} - aborting")
                    break
                elif t.startswith("<") and on_status is not None:
                    on_status(t)
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
        log(f"# done in {time.time() - t0:.1f}s, {errors} error(s)")
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
           status_every=1.0, progress=None, should_abort=None,
           on_log=None, on_status=None):
    """Find/open the port and stream G-code lines. Returns an exit code (0 = clean)."""
    def log(msg):
        print(msg)
        if on_log:
            on_log(msg)

    if serial is None:
        log("pyserial not installed: python -m pip install pyserial")
        return 2
    port = find_port(port)
    if not port:
        log("no machine found - plug in the Pico's USB (and power), or pass --port COMx")
        return 3
    prog = _program(lines)
    if not prog:
        log("nothing to send")
        return 1
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:  # noqa: BLE001
        log(f"could not open {port}: {e}  (is a serial monitor holding the port?)")
        return 3
    log(f"# port {port} @ {baud}")
    with ser:
        return _run(ser, prog, preamble, rx_buffer, status_every,
                    progress=progress, should_abort=should_abort,
                    on_log=on_log, on_status=on_status)


def console(cmds, port=None, baud=115200, read_for=0.6):
    """One-shot: open the port, send each command, collect reply lines.

    For the browser serial monitor / idle status pings. Real-time bytes ('?',
    '!', '~', 0x18) are sent without a newline. Returns ``(port, lines)``;
    ``lines`` is empty and ``port`` falsy if no machine / port busy.
    """
    if serial is None:
        return None, ["pyserial not installed"]
    port = find_port(port)
    if not port:
        return None, ["no machine found"]
    if isinstance(cmds, str):
        cmds = [cmds]
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:  # noqa: BLE001
        return port, [f"could not open {port}: {e}"]
    out: list[str] = []
    with ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        for c in cmds:
            c = c.strip()
            if not c:
                continue
            realtime = c in ("?", "!", "~") or c == "\x18"
            ser.write(c.encode() if realtime else (c + "\n").encode())
            ser.flush()
            end = time.time() + read_for
            while time.time() < end:
                raw = ser.readline()
                if not raw:
                    continue
                t = raw.decode("ascii", "replace").strip()
                if t:
                    out.append(t)
                # an 'ok'/'error' closes a normal command; '<...>' closes a '?'
                if not realtime and (t == "ok" or t.startswith("error:")):
                    break
                if realtime and t.startswith("<"):
                    break
    return port, out
