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
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .grbl.parser import StatusReport


@dataclass
class JobInfo:
    filename: str
    total_lines: int
    state: str = "ready"   # ready | running | held | stopped | completed | error
    current_line: int = 0
    started_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at else None
        d: dict = {
            "filename": self.filename,
            "total_lines": self.total_lines,
            "state": self.state,
            "current_line": self.current_line,
            "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        }
        if self.error:
            d["error"] = self.error
        return d


class AppState:
    def __init__(self) -> None:
        self.connected: bool = False
        self.grbl_state: str = "Disconnected"
        self.mpos_x: float = 0.0
        self.mpos_y: float = 0.0
        self.feed_rate: float = 0.0
        self.grbl_version: Optional[str] = None
        self.job: Optional[JobInfo] = None
        self._console: deque = deque(maxlen=config.CONSOLE_MAX_LINES)

    # --- GRBL state updates ---

    def update_from_status(self, report: StatusReport) -> None:
        """Apply a parsed GRBL status report to the current state."""
        self.connected = True
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
        self.connected = True
        self.grbl_state = "Idle"

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
