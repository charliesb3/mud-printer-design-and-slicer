"""
Represents the one currently-loaded G-code job.

Milestone 1 supports exactly one job at a time.
Uploading a new file replaces the current job when safe to do so.

The job metadata here is intentionally minimal and provisional.
Layer/lift/section concepts will be designed in a later milestone,
informed by the Design + Toolpath subproject.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..grbl.parser import is_end_of_program, prepare_gcode_line


ACTIVE_STATES = {"running", "held"}


@dataclass
class CurrentJob:
    filename: str
    filepath: Path
    lines: list[str]      # pre-processed sendable lines (comments stripped, blanks removed)
    state: str = "ready"  # ready | running | held | stopped | completed | error
    current_line: int = 0
    started_at: Optional[float] = None
    error: Optional[str] = None

    @property
    def total_lines(self) -> int:
        return len(self.lines)

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def to_dict(self) -> dict:
        import time
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


def load_job_from_file(filepath: Path) -> CurrentJob:
    """Read a G-code file and return a CurrentJob with pre-processed lines."""
    raw = filepath.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for raw_line in raw.splitlines():
        cleaned = prepare_gcode_line(raw_line)
        if cleaned:
            lines.append(cleaned)
    return CurrentJob(
        filename=filepath.name,
        filepath=filepath,
        lines=lines,
    )
