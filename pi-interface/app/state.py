"""
Application-wide state shared between the GRBL communicator,
job runner, and WebSocket broadcaster.

All mutations occur inside the asyncio event loop — no threading locks needed
provided the SerialTransport reader thread only touches its asyncio.Queue via
call_soon_threadsafe (which it does).
"""
from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Optional

from . import config
from .grbl.parser import StatusReport

if TYPE_CHECKING:
    from .jobs.current_job import CurrentJob


MM_PER_IN = 25.4


class MachineSetup:
    """
    The operator-established XY references (Pi-owned, in memory; 2026-10-14).

    - ORIGIN: the print origin = work XY zero (G54), established by the
      operator with "Set Origin" (the Pi sends `G10 L20 P1 X0 Y0`). The
      `.mudjob` building toolpath is in these work coordinates.
    - PRIME position: a nearby XY location the operator chose with "Set Prime
      Position", stored in WORK coordinates (inches) — so it moves with the
      origin when the operator re-zeroes. Priming is machine operation, never
      part of the building toolpath.

    Cleared (`invalidate`) when machine position can no longer be trusted:
    a GRBL disconnect (incl. the Uno's reset on serial reconnect) or any GRBL
    ALARM. A GRBL soft reset while Idle keeps position and keeps the origin.
    The prime position is kept (it is relative to the origin) but is only
    usable while the origin is set.
    """

    def __init__(self) -> None:
        self.origin_set: bool = False
        self.origin_mpos: Optional[tuple] = None    # machine position at Set Origin (report units)
        self.prime: Optional[tuple] = None          # (x, y) work coordinates, inches
        self.moving: Optional[str] = None           # "prime" while a positioning move runs
        self.note: Optional[str] = None             # why the origin was cleared / last setup error

    def invalidate(self, reason: str) -> None:
        if self.origin_set:
            self.note = f"Origin cleared: {reason}. Re-establish it with Set Origin."
        self.origin_set = False
        self.origin_mpos = None


class AppState:
    def __init__(self) -> None:
        self.connected: bool = False
        self.grbl_state: str = "Disconnected"
        self.mpos_x: float = 0.0
        self.mpos_y: float = 0.0
        self.feed_rate: float = 0.0
        self.grbl_version: Optional[str] = None
        # The loaded job (CurrentJob). Shared by reference with the HTTP layer
        # and the runner, so every broadcast reflects the live job state.
        self.job: Optional["CurrentJob"] = None
        # Incremented on every status report / GRBL welcome (reset). The job
        # runner uses them to wait for a FRESH Idle report after draining a lift
        # and to notice a reset or disconnect while awaiting a frame reposition.
        self.status_seq: int = 0
        self.reset_seq: int = 0
        # Work position (only if GRBL reports WPos instead of MPos: $10).
        self.wpos_x: Optional[float] = None
        self.wpos_y: Optional[float] = None
        self.pos_kind: Optional[str] = None          # "mpos" | "wpos" — what reports carry
        # GRBL `$N=value` settings seen in `$$` listings (read only — never written).
        self.grbl_settings: dict[int, str] = {}
        self.setup = MachineSetup()
        self._console: deque = deque(maxlen=config.CONSOLE_MAX_LINES)

    # --- GRBL state updates ---

    def update_from_status(self, report: StatusReport) -> None:
        """Apply a parsed GRBL status report to the current state."""
        self.connected = True
        self.status_seq += 1
        state = report.state
        if report.sub_state:
            self.grbl_state = f"{state}:{report.sub_state}"
        else:
            self.grbl_state = state

        if report.mpos_x is not None:
            self.mpos_x = report.mpos_x
            self.pos_kind = "mpos"
        if report.mpos_y is not None:
            self.mpos_y = report.mpos_y
        if report.wpos_x is not None and report.wpos_y is not None:
            self.wpos_x, self.wpos_y = report.wpos_x, report.wpos_y
            self.pos_kind = "wpos"
        if report.feed_rate is not None:
            self.feed_rate = report.feed_rate
        if state.lower().startswith("alarm"):
            self.setup.invalidate("GRBL alarm (machine position may be lost)")

    def note_alarm(self, line: str) -> None:
        """An `ALARM:n` message from GRBL."""
        self.grbl_state = line
        self.setup.invalidate(f"GRBL {line} (machine position may be lost)")

    def mark_disconnected(self) -> None:
        self.connected = False
        self.grbl_state = "Disconnected"
        self.setup.invalidate("GRBL disconnected")

    # --- Work coordinates ---

    @property
    def report_units(self) -> Optional[str]:
        """Units of GRBL's position reports ($13: 0 = mm, 1 = inch), if known."""
        v = self.grbl_settings.get(13)
        if v is None:
            return None
        return "in" if v.strip() == "1" else "mm"

    def work_position_in(self) -> Optional[tuple]:
        """Current XY in WORK coordinates (inches) relative to the established origin, if knowable."""
        units = self.report_units
        if not self.setup.origin_set or units is None:
            return None
        f = 1.0 if units == "in" else 1.0 / MM_PER_IN
        if self.pos_kind == "wpos" and self.wpos_x is not None:
            return (self.wpos_x * f, self.wpos_y * f)
        if self.pos_kind == "mpos" and self.setup.origin_mpos is not None:
            ox, oy = self.setup.origin_mpos
            return ((self.mpos_x - ox) * f, (self.mpos_y - oy) * f)
        return None

    def mark_connected_idle(self) -> None:
        """GRBL welcome message: a (re)start or reset of the controller."""
        self.connected = True
        self.grbl_state = "Idle"
        self.reset_seq += 1

    # --- Console ---

    def console_add(self, direction: str, line: str) -> None:
        """Record a TX or RX line in the rolling console buffer."""
        self._console.append({
            "direction": direction,
            "line": line,
            "t": time.time(),
        })

    @property
    def console_recent(self) -> list:
        return list(self._console)

    # --- Serialisation ---

    def to_dict(self) -> dict:
        jog_warning: Optional[str]
        if config.JOG_SPEED_MM_MIN is None:
            jog_warning = (
                "Jogging disabled: JOG_SPEED_MM_MIN is not set. "
                "Set a safe value in .env after validating on the physical machine."
            )
        else:
            jog_warning = (
                "Jog speed configured but not yet validated on the physical machine. "
                "Use with caution."
            )

        return {
            "type": "state",
            "connected": self.connected,
            "grbl_state": self.grbl_state,
            "x": round(self.mpos_x, 3),
            "y": round(self.mpos_y, 3),
            "feed_rate": round(self.feed_rate, 1),
            "grbl_version": self.grbl_version,
            "jog_enabled": config.JOG_SPEED_MM_MIN is not None,
            "jog_warning": jog_warning,
            "job": self.job.to_dict() if self.job else None,
            "setup": self._setup_dict(),
        }

    def _setup_dict(self) -> dict:
        s = self.setup
        work = self.work_position_in()
        return {
            "origin_set": s.origin_set,
            "prime": [round(v, 4) for v in s.prime] if s.prime else None,
            "prime_ready": bool(s.origin_set and s.prime),
            "work_x": round(work[0], 4) if work else None,
            "work_y": round(work[1], 4) if work else None,
            "report_units": self.report_units,
            "moving": s.moving,
            "note": s.note,
            "positioning_enabled": config.JOG_SPEED_MM_MIN is not None,
        }
