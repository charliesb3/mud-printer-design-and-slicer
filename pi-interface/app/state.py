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


MM_PER_INCH = 25.4


def mm_min_to_in_s(mm_min: float) -> float:
    """Convert mm/min → in/s (positive values; direction handled separately)."""
    return mm_min / MM_PER_INCH / 60.0


def in_s_to_mm_min(in_s: float) -> float:
    """Convert in/s → mm/min."""
    return in_s * MM_PER_INCH * 60.0


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
        self.feed_override_pct: int = 100
        self.grbl_version: Optional[str] = None
        self.job: Optional[JobInfo] = None
        self._console: deque = deque(maxlen=config.CONSOLE_MAX_LINES)

        # Operator-adjustable jog speed. Starts at the configured default
        # (JOG_SPEED_MM_MIN); clamped to [0, JOG_SPEED_MAX_MM_MIN] from the UI.
        self.current_jog_speed_mm_min: Optional[float] = config.JOG_SPEED_MM_MIN

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
        if report.feed_override_pct is not None:
            self.feed_override_pct = report.feed_override_pct

    def mark_disconnected(self) -> None:
        self.connected = False
        self.grbl_state = "Disconnected"

    def mark_connected_idle(self) -> None:
        self.connected = True
        self.grbl_state = "Idle"

    # --- Jog speed control ---

    def set_jog_speed_mm_min(self, speed_mm_min: float) -> float:
        """Clamp and store a new current jog speed. Returns the stored value."""
        max_speed = config.JOG_SPEED_MAX_MM_MIN
        if max_speed is None or max_speed <= 0:
            # No ceiling configured means jogging is disabled.
            return 0.0
        if speed_mm_min < 0:
            speed_mm_min = 0.0
        if speed_mm_min > max_speed:
            speed_mm_min = max_speed
        self.current_jog_speed_mm_min = speed_mm_min if speed_mm_min > 0 else None
        return speed_mm_min

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
        # Jog warning
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

        # Envelope warning
        envelope_warning: Optional[str]
        env_x = config.PRINTER_ENVELOPE_X_MM
        env_y = config.PRINTER_ENVELOPE_Y_MM
        if env_x is None or env_y is None:
            envelope_warning = (
                "Unconfigured printer (extents not set). "
                "Set PRINTER_ENVELOPE_X_IN / PRINTER_ENVELOPE_Y_IN in .env. "
                "These are nominal values only — travel limits are enforced by GRBL."
            )
        elif not config.PRINTER_EXTENTS_VERIFIED:
            envelope_warning = (
                "Printer envelope is a nominal design value — physical travel limits "
                "have not been verified. Jog with care."
            )
        else:
            envelope_warning = None

        # Current jog speed (fall back to configured default)
        cur_jog = self.current_jog_speed_mm_min
        max_jog = config.JOG_SPEED_MAX_MM_MIN or 0.0

        return {
            "type": "state",
            "connected": self.connected,
            "grbl_state": self.grbl_state,
            "x": round(self.mpos_x, 3),
            "y": round(self.mpos_y, 3),
            "feed_rate": round(self.feed_rate, 1),
            "feed_rate_in_s": round(mm_min_to_in_s(self.feed_rate), 3),
            "feed_override_pct": self.feed_override_pct,
            "planned_feed_rate": round(
                self.feed_rate / (self.feed_override_pct / 100.0), 1
            ) if self.feed_override_pct > 0 else round(self.feed_rate, 1),
            "planned_feed_rate_in_s": round(
                mm_min_to_in_s(self.feed_rate / (self.feed_override_pct / 100.0)), 3
            ) if self.feed_override_pct > 0 else round(mm_min_to_in_s(self.feed_rate), 3),
            "grbl_version": self.grbl_version,
            "jog_enabled": config.JOG_SPEED_MM_MIN is not None and max_jog > 0,
            "jog_warning": jog_warning,
            "jog_speed_mm_min": round(cur_jog, 1) if cur_jog else 0.0,
            "jog_speed_in_s": round(mm_min_to_in_s(cur_jog), 3) if cur_jog else 0.0,
            "jog_speed_max_mm_min": round(max_jog, 1),
            "jog_speed_max_in_s": round(mm_min_to_in_s(max_jog), 3) if max_jog else 0.0,
            "envelope_x_mm": round(env_x, 1) if env_x else None,
            "envelope_y_mm": round(env_y, 1) if env_y else None,
            "envelope_x_in": round(env_x / MM_PER_INCH, 2) if env_x else None,
            "envelope_y_in": round(env_y / MM_PER_INCH, 2) if env_y else None,
            "envelope_verified": config.PRINTER_EXTENTS_VERIFIED,
            "envelope_warning": envelope_warning,
            "job": self.job.to_dict() if self.job else None,
        }
