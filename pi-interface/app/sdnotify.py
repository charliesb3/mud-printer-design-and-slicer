"""
Minimal systemd notification (sd_notify protocol; no dependency).

With `Type=notify` + `WatchdogSec=` in the unit (deploy/mudprinter.service),
the application reports READY once started and then pings the watchdog from
the asyncio event loop. If the loop hangs (so the Z dead-man checks no longer
run), systemd kills the service and its ExecStopPost forces the Z PWM line
low. Outside systemd (Mac, tests) every call is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger(__name__)


def notify(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
        return True
    except OSError as exc:
        log.warning("sd_notify failed: %s", exc)
        return False


def watchdog_interval_s() -> float | None:
    """Half of systemd's WatchdogSec (WATCHDOG_USEC), or None when not supervised."""
    usec = os.environ.get("WATCHDOG_USEC")
    pid = os.environ.get("WATCHDOG_PID")
    if not usec or (pid and pid != str(os.getpid())):
        return None
    return int(usec) / 2e6


async def watchdog_loop(interval_s: float) -> None:
    while True:
        notify("WATCHDOG=1")
        await asyncio.sleep(interval_s)
