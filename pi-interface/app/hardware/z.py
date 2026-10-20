"""
Manual Z — the ONE Z controller (Pi GPIO → Cytron MD13S → 12 V actuator).

Every Z request — touchscreen / browser button, RF remote Z UP / Z DOWN —
is an input to this controller; nothing else drives the Z outputs.

Z is OPEN-LOOP: no position, no homing, no limits. Nothing here estimates
height from run time, and nothing here ever will.

Requests are DEAD-MAN: each source (one per browser client, `remote_up`,
`remote_down`) holds a request only while it keeps refreshing it (UI
heartbeat, remote poll). The resolved command is:

- no live request                          → STOP (and the block is cleared)
- requests in two directions               → STOP + BLOCKED until every Z
  (UP + DOWN, or a source reversing)          input is released
- refusal (Z disabled, output fault,
  REMOTE STOP latched)                     → STOP
- a run longer than max_run_s              → STOP + BLOCKED until released
- otherwise                                → the requested direction at the
                                             FIXED configured duty

STOP = PWM duty 0 (MD13S: PWM LOW → both outputs LOW). Startup, release,
dead-man timeout, exception and shutdown all end in STOP.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import pins
from .backend import HardwareBackend

log = logging.getLogger(__name__)

UP, DOWN = 1, -1
_NAMES = {UP: "UP", DOWN: "DOWN", 0: "STOP"}


@dataclass
class _Request:
    direction: int
    refreshed: float


class ZController:
    def __init__(self, backend: HardwareBackend, *, duty: float, timeout_s: float, max_run_s: float,
                 dir_invert: bool = False, interlock: Callable[[], Optional[str]] = lambda: None,
                 event: Callable[[str, str], None] = lambda kind, text: None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._backend = backend
        self.duty = max(0.0, min(1.0, duty))
        self.timeout_s = timeout_s
        self.max_run_s = max_run_s
        self._dir_invert = dir_invert
        self._interlock = interlock
        self._event = event
        self._clock = clock
        self._requests: dict = {}
        self.command: int = 0                    # resolved: UP, DOWN or 0 (STOP)
        self.source: Optional[str] = None        # who is driving
        self.blocked: Optional[str] = None       # why Z waits for all inputs to be released
        self.fault: Optional[str] = None         # output failure (Z refused until restart)
        self.last_stop: Optional[str] = None
        self._run_started: Optional[float] = None
        self._dir_level = False
        self._write(0.0, False)                  # STARTUP = STOP

    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return self._backend.has_z

    def refusal(self) -> Optional[str]:
        """Why Z cannot move right now (None: it can)."""
        if not self._backend.has_z:
            return "No Z hardware on this controller (HARDWARE_BACKEND is not gpio/mock)."
        if self.fault:
            return f"Z output fault: {self.fault} — restart the printer service after checking the wiring."
        if self.duty <= 0:
            return "Manual Z is disabled: Z_PWM_DUTY is not set (commissioning)."
        return self._interlock()

    def request(self, source: str, direction: int) -> Optional[str]:
        """Start (or keep) a held Z request. Returns a refusal, or None."""
        if direction not in (UP, DOWN):
            return "Z direction must be up or down."
        now = self._clock()
        old = self._requests.get(source)
        if old is not None and old.direction != direction:
            self._block(f"{source} reversed direction while held")
        self._requests[source] = _Request(direction, now)
        self._resolve(now)
        if self.command == 0:
            return self.blocked or self.refusal()
        return None

    def refresh(self, source: str) -> None:
        """Dead-man heartbeat for a held request."""
        req = self._requests.get(source)
        if req is not None:
            req.refreshed = self._clock()

    def release(self, source: str) -> None:
        if self._requests.pop(source, None) is not None:
            self._resolve(self._clock(), reason=f"released ({source})")

    def stop_all(self, reason: str) -> None:
        """Immediate STOP, dropping every request (REMOTE STOP, shutdown)."""
        self._requests.clear()
        self._resolve(self._clock(), reason=reason)

    def tick(self) -> None:
        """Expire stale requests and enforce the run limit (called by the watchdog loop)."""
        self._resolve(self._clock())

    def close(self) -> None:
        self.stop_all("shutdown")
        try:
            self._backend.set_z(0.0, False)
        except Exception as exc:          # pragma: no cover - best effort
            log.error("Z STOP on shutdown failed: %s", exc)

    # ------------------------------------------------------------------ #

    def _level(self, direction: int) -> bool:
        up = pins.Z_DIR_LEVEL_UP != self._dir_invert
        return up if direction == UP else (not up)

    def _block(self, why: str) -> None:
        if not self.blocked:
            self.blocked = f"Z stopped: {why}. Release every Z button before moving Z again."
            self._event("z", self.blocked)
            log.warning("%s", self.blocked)

    def _resolve(self, now: float, reason: Optional[str] = None) -> None:
        for src, req in list(self._requests.items()):
            if now - req.refreshed > self.timeout_s:
                del self._requests[src]
                reason = f"dead-man timeout ({src}: no refresh for {now - req.refreshed:.2f} s)"
                log.warning("Z %s", reason)
        if not self._requests:
            self.blocked = None
            self._set(0, None, reason or "released")
            return
        dirs = {r.direction for r in self._requests.values()}
        if len(dirs) > 1:
            self._block("contradictory Z requests (" + ", ".join(
                f"{s} {_NAMES[r.direction]}" for s, r in sorted(self._requests.items())) + ")")
        if self.command and self._run_started is not None and now - self._run_started > self.max_run_s:
            self._block(f"maximum continuous run {self.max_run_s:g} s reached")
        if self.blocked:
            self._set(0, None, self.blocked)
            return
        refusal = self.refusal()
        if refusal:
            # a held request must never start Z later on its own (e.g. when a
            # REMOTE STOP is cleared while Z UP is still held): block it
            self._block(refusal.rstrip("."))
            self._set(0, None, refusal)
            return
        direction = dirs.pop()
        src = min(s for s, r in self._requests.items() if r.direction == direction)
        self._set(direction, src, None)

    def _set(self, direction: int, source: Optional[str], reason: Optional[str]) -> None:
        if direction == self.command:
            if direction:
                self.source = source
            return
        if direction == 0:
            self._write(0.0, self._dir_level)
            self.last_stop = reason
            self._event("z", f"Z STOP ({reason})")
            log.info("Z STOP (%s)", reason)
            self.command, self.source, self._run_started = 0, None, None
            return
        level = self._level(direction)
        if self.command:                       # reversing: PWM 0 first (backend also guards)
            self._write(0.0, self._dir_level)
        if not self._write(self.duty, level):
            return
        self.command, self.source = direction, source
        self._run_started = self._clock()
        self._event("z", f"Z {_NAMES[direction]} start ({source}, duty {self.duty:.0%})")
        log.info("Z %s start (%s, duty %.2f)", _NAMES[direction], source, self.duty)

    def _write(self, duty: float, level: bool) -> bool:
        try:
            self._backend.set_z(duty, level)
            self._dir_level = level
            return True
        except Exception as exc:
            self.fault = str(exc) or type(exc).__name__
            log.error("Z output failure (%s) — forcing STOP", self.fault)
            self._event("z", f"Z output FAULT: {self.fault} — STOP")
            try:
                self._backend.set_z(0.0, level)
            except Exception as exc2:
                log.error("Z STOP after failure also failed: %s", exc2)
            self.command, self.source, self._run_started = 0, None, None
            self._requests.clear()
            return False

    def to_dict(self) -> dict:
        out = self._backend.z_outputs()
        return {
            "available": self._backend.has_z,
            "command": _NAMES[self.command],
            "source": self.source,
            "duty_config": self.duty,
            "pwm_duty": round(out["duty"], 3),
            "dir_level": "HIGH" if out["dir_level"] else "LOW",
            "dir_invert": self._dir_invert,
            "timeout_s": self.timeout_s,
            "max_run_s": self.max_run_s,
            "blocked": self.blocked,
            "fault": self.fault,
            "refusal": self.refusal(),
            "last_stop": self.last_stop,
            "requests": sorted(self._requests),
        }
