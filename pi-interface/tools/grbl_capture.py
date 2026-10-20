#!/usr/bin/env python3
"""
READ-ONLY GRBL capture: records the controller's identity and configuration
to a text file before anything is changed (docs/grbl-capture-checklist.md).

Sends ONLY these GRBL 1.1 queries, nothing else:
    $I   build info / version          $$  settings
    $#   coordinate offsets            $G  parser (modal) state
plus `?` (one status report). Never `$N=`, `$X`, `$H`, G-code or resets.

Opening the port resets an Uno (DTR) — GRBL restarts from EEPROM; settings
are not changed. Stop the printer service first so the port is free:

    sudo systemctl stop mudprinter
    .venv/bin/python tools/grbl_capture.py /dev/mudpr-grbl
    sudo systemctl start mudprinter
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path

QUERIES = ("$I", "$$", "$#", "$G")          # the whitelist — read-only queries only


def is_read_only(command: str) -> bool:
    return command in QUERIES


def run_query(ser, command: str, timeout_s: float = 5.0) -> list:
    """Send one whitelisted query; return every line up to and including ok / error."""
    if not is_read_only(command):
        raise ValueError(f"refusing non-read-only command {command!r}")
    ser.write((command + "\n").encode("ascii"))
    lines, deadline = [], time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or line.startswith("<"):
            continue
        lines.append(line)
        if line == "ok" or line.startswith("error:") or line.startswith("ALARM:"):
            return lines
    lines.append(f"(no ok within {timeout_s:g} s)")
    return lines


def capture(ser, wait_welcome_s: float = 4.0) -> list:
    out = []
    deadline = time.monotonic() + wait_welcome_s
    while time.monotonic() < deadline:            # the welcome after the open-reset
        raw = ser.readline()
        if raw:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                out.append(f"# startup: {line}")
                if line.lower().startswith("grbl"):
                    break
    ser.write(b"?")
    time.sleep(0.3)
    status = ser.readline().decode("utf-8", errors="replace").strip()
    out.append(f"# status: {status}")
    for q in QUERIES:
        out.append(f"> {q}")
        out.extend(run_query(ser, q))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="serial device, e.g. /dev/mudpr-grbl or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", type=Path, default=None,
                    help="output file (default: grbl-capture-<timestamp>.txt in the current directory)")
    args = ap.parse_args(argv)

    import serial  # pyserial (already a pi-interface dependency)

    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = args.out or Path(f"grbl-capture-{stamp}.txt")
    with serial.Serial(args.port, args.baud, timeout=0.5) as ser:
        lines = capture(ser)
    header = [f"# GRBL read-only capture {stamp}", f"# port {args.port} @ {args.baud}",
              f"# queries: {' '.join(QUERIES)} (read-only)"]
    out_path.write_text("\n".join(header + lines) + "\n")
    print("\n".join(lines))
    print(f"\nSaved to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
