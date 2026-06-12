#!/usr/bin/env python3
"""PolarGraph bench self-test: steppers, servo, endstops.

A repeatable hardware smoke-test for the grblHAL control board. Run it any time
you change wiring:

    python tools/total_test.py            # auto-detect the Pico
    python tools/total_test.py --port COM3

It walks through, with a couple of seconds' warning before each motion so you can
watch:
    1. servo sweep            (visual)
    2. jog motor X / #1       (visual)
    3. jog motor Y / #2       (visual)
    4. endstops X and Y       (auto PASS/FAIL - press each switch when prompted)

ASCII-only output (this Windows console is cp949). Ctrl+C aborts safely.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial not installed. Run: python -m pip install pyserial")
    sys.exit(2)

BAUD = 115200


def find_port(explicit=None):
    if explicit:
        return explicit
    cands = []
    for p in list_ports.comports():
        s = (str(p.description) + " " + str(p.hwid)).lower()
        if any(k in s for k in ("2e8a", "rp2040", "pico", "usb serial", "cdc")):
            cands.append(p.device)
    if len(cands) == 1:
        return cands[0]
    if not cands:
        print("No Pico found. Plug in its USB (or pass --port COMx). Ports seen:")
        for p in list_ports.comports():
            print("  " + p.device + "  " + str(p.description))
        sys.exit(2)
    print("Multiple candidate ports (" + ", ".join(cands) + "); pass --port COMx")
    sys.exit(2)


class Grbl:
    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=0.2)
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def send(self, line, settle=0.6, echo=False):
        if echo:
            print("  > " + line)
        self.ser.write((line + "\n").encode())
        self.ser.flush()
        deadline = time.time() + settle
        while time.time() < deadline:
            raw = self.ser.readline()
            if not raw:
                continue
            t = raw.decode("ascii", "replace").rstrip("\r\n")
            if not t:
                continue
            if echo:
                print("    < " + t)
            deadline = time.time() + settle
            if t == "ok" or t.startswith("error:") or t.startswith("ALARM"):
                break

    def status(self):
        self.ser.write(b"?")
        self.ser.flush()
        deadline = time.time() + 0.6
        while time.time() < deadline:
            raw = self.ser.readline()
            if not raw:
                continue
            t = raw.decode("ascii", "replace").strip()
            if t.startswith("<"):
                return t
        return ""


def pn_field(status_line):
    for part in status_line.strip("<>").split("|"):
        if part.startswith("Pn:"):
            return part[3:]
    return ""


def state(status_line):
    return status_line.strip("<>").split("|")[0] if status_line else ""


def countdown(msg, secs=2):
    print("\n" + msg)
    for n in range(secs, 0, -1):
        print("  ...%d" % n)
        time.sleep(1)


def jog(g, axis, dist=40, feed=350):
    g.send("$J=G91 G21 %s%d F%d" % (axis, dist, feed), settle=0.4)
    timeout = time.time() + abs(dist) / feed * 60 + 4
    time.sleep(0.4)
    while time.time() < timeout:
        if state(g.status()).startswith("Idle"):
            return
        time.sleep(0.2)


def test_endstop(g, letter, seconds=10.0):
    print("\n  >> press and release the %s endstop now (%0.0fs window)..." % (letter, seconds))
    end = time.time() + seconds
    while time.time() < end:
        if letter in pn_field(g.status()):
            print("  [PASS] %s endstop triggered" % letter)
            rel = time.time() + 4
            while time.time() < rel and letter in pn_field(g.status()):
                time.sleep(0.15)
            return True
        time.sleep(0.15)
    print("  [FAIL] %s endstop not detected" % letter)
    return False


def main():
    ap = argparse.ArgumentParser(description="PolarGraph bench self-test")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--no-endstops", action="store_true", help="skip the endstop checks")
    a = ap.parse_args()

    port = find_port(a.port)
    print("=== PolarGraph bench self-test ===")
    print("port: %s @ %d" % (port, BAUD))
    g = Grbl(port)
    results = {}
    try:
        g.send("$X", settle=0.8)  # unlock any startup alarm
        print("status: " + g.status())

        countdown("[1/4] SERVO - watch the SG90 sweep")
        for s in (150, 850, 150, 850, 500):
            g.send("M3 S%d" % s, settle=0.4)
            time.sleep(0.7)
        g.send("M5", settle=0.4)

        countdown("[2/4] MOTOR X (#1, left) - watch it spin")
        jog(g, "X", 40)
        jog(g, "X", -40)

        countdown("[3/4] MOTOR Y (#2, right) - watch it spin")
        jog(g, "Y", 40)
        jog(g, "Y", -40)

        if not a.no_endstops:
            print("\n[4/4] ENDSTOPS")
            results["endstop_X"] = test_endstop(g, "X")
            results["endstop_Y"] = test_endstop(g, "Y")

        print("\n=== RESULTS ===")
        for k, v in results.items():
            print("  %-12s %s" % (k, "PASS" if v else "FAIL"))
        print("  (servo + motors are visual - confirm by eye above)")
        if results:
            ok = all(results.values())
            print("  endstops: " + ("ALL PASS" if ok else "CHECK FAILS ABOVE"))
    except KeyboardInterrupt:
        print("\naborted")
    finally:
        g.send("M5", settle=0.3)  # release the servo
        g.close()


if __name__ == "__main__":
    main()
