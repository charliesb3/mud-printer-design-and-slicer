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
        if report.mpos_y is not None:
            self.mpos_y = report.mpos_y
        if report.feed_rate is not None:
            self.feed_rate = report.feed_rate

    def mark_disconnected(self) -> None:
        self.connected = False
        self.grbl_state = "Disconnected"

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
        }
