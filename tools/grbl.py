#!/usr/bin/env python3
"""Minimal grbl / grblHAL serial console for PolarGraph bring-up.

ASCII-only output on purpose (this Windows console is cp949).

Usage:
  python tools/grbl.py --list                       # list serial ports
  python tools/grbl.py [--port COM5] CMD [CMD ...]   # send lines, print replies
  python tools/grbl.py [--port COM5] --file cmds.txt
  echo "$X" | python tools/grbl.py [--port COM5] -

Each CMD is one line sent to the controller; after each we read replies until
'ok' / 'error:' / 'ALARM' (or it goes quiet). Realtime bytes ('?' status,
'~' resume, '!' hold, 0x18 soft-reset) are sent without a newline.

In a POSIX shell, SINGLE-QUOTE grbl commands so the shell doesn't eat them
('$$' would otherwise expand to the PID).
"""
import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial not installed. Run: python -m pip install pyserial")
    sys.exit(2)

REALTIME = {"?", "~", "!", "\x18"}
# friendly aliases for control bytes (so you can pass them as plain args)
ALIASES = {"RESET": "\x18", "CTRL-X": "\x18", "^X": "\x18", "0X18": "\x18",
           "STATUS": "?", "HOLD": "!", "RESUME": "~"}


def cmd_list():
    ports = list(list_ports.comports())
    if not ports:
        print("no serial ports found")
        return
    for p in ports:
        print(f"{p.device}  |  {p.description}  |  {p.hwid}")


def autodetect():
    pico, loose = [], []
    for p in list_ports.comports():
        s = (str(p.description) + " " + str(p.hwid)).lower()
        if "2e8a" in s:  # Raspberry Pi USB VID - the RP2040 itself
            pico.append(p.device)
        elif any(k in s for k in ("rp2040", "pico", "grbl", "usb serial", "cdc")):
            loose.append(p.device)
    return pico if pico else loose


def send_line(ser, line, settle):
    line = line.rstrip("\r\n")
    if line == "":
        return
    tx = ALIASES.get(line.upper(), line)
    if tx in REALTIME:
        ser.write(tx.encode())
    else:
        ser.write((tx + "\n").encode())
    ser.flush()
    deadline = time.time() + settle
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        txt = raw.decode("ascii", "replace").rstrip("\r\n")
        if txt == "":
            continue
        print("  < " + txt)
        deadline = time.time() + settle  # extend on activity (long $$ dumps)
        if txt == "ok" or txt.startswith("error:") or txt.startswith("ALARM"):
            break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--file")
    ap.add_argument("--settle", type=float, default=1.0,
                    help="seconds to wait for replies after the last line")
    ap.add_argument("cmds", nargs="*")
    a = ap.parse_args()

    if a.list:
        cmd_list()
        return

    port = a.port
    if not port:
        c = autodetect()
        if len(c) == 1:
            port = c[0]
        elif not c:
            print("no --port given and none auto-detected; try --list")
            sys.exit(2)
        else:
            print("multiple candidate ports (" + ", ".join(c) + "); pick one with --port")
            sys.exit(2)

    cmds = list(a.cmds)
    if a.file:
        with open(a.file) as f:
            cmds += f.read().splitlines()
    if cmds == ["-"] or (not cmds and not sys.stdin.isatty()):
        cmds = sys.stdin.read().splitlines()

    try:
        ser = serial.Serial(port, a.baud, timeout=0.2)
    except Exception as e:
        print(f"could not open {port}: {e}")
        print("If your serial monitor is connected, close it (one program per COM port).")
        sys.exit(3)

    with ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        print(f"# connected {port} @ {a.baud}")
        for c in cmds:
            print("> " + c)
            send_line(ser, c, a.settle)
    print("# done")


if __name__ == "__main__":
    main()
