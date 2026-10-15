"""
Represents the one currently-loaded job.

Exactly one job is loaded at a time. Uploading a new file replaces the
current job when safe to do so.

A job is an ordered sequence of LIFTS (see docs/job-format.md):
- A building job (.mudjob JSON) carries its lifts explicitly. The lifts are
  decided upstream by the authoritative PrintPlan; the Pi never recalculates
  them. Between lifts the runner stops and waits for the operator to confirm
  the physical frame reposition.
- An ordinary flat G-code file is a job with exactly one lift, so it runs
  exactly as in Milestone 1.

Underneath, every lift is ordinary GRBL G-code.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..grbl.parser import prepare_gcode_line, program_control_reason


JOB_FORMAT = "mudprinter-job"
JOB_FORMAT_VERSION = 1

# awaiting_reposition counts as active: the job owns the machine between lifts.
ACTIVE_STATES = {"running", "held", "awaiting_reposition"}


class JobFormatError(ValueError):
    """The uploaded building job is not a valid job file."""


@dataclass
class JobLift:
    index: int                                   # 0-based position in the job
    lines: list[str]                             # sendable lines (comments stripped)
    description: Optional[str] = None
    reposition_before: Optional[dict] = None     # shown to the operator, never interpreted

    def to_dict(self) -> dict:
        d: dict = {"index": self.index, "total_lines": len(self.lines)}
        if self.description:
            d["description"] = self.description
        if self.reposition_before:
            d["reposition_before"] = self.reposition_before
        return d


@dataclass
class CurrentJob:
    filename: str
    filepath: Path
    lines: list[str] = field(default_factory=list)   # all sendable lines, in order
    lifts: list[JobLift] = field(default_factory=list)
    kind: str = "gcode"   # gcode (flat file, one lift) | building (.mudjob)
    name: Optional[str] = None
    units: Optional[str] = None   # informational only — never converted
    state: str = "ready"  # ready | running | held | awaiting_reposition | stopped | completed | error
    current_line: int = 0         # lines acknowledged, across the whole job
    current_lift: int = 0         # the lift being (or last) streamed
    awaiting_lift: Optional[int] = None   # the lift waiting for reposition confirmation
    started_at: Optional[float] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.lifts:
            self.lifts = [JobLift(index=0, lines=list(self.lines))]
        else:
            self.lines = [line for lift in self.lifts for line in lift.lines]

    @property
    def total_lines(self) -> int:
        return len(self.lines)

    @property
    def total_lifts(self) -> int:
        return len(self.lifts)

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def lift_start_line(self, lift_index: int) -> int:
        """Global line offset of the first line of a lift."""
        return sum(len(l.lines) for l in self.lifts[:lift_index])

    def reset_progress(self) -> None:
        self.state = "ready"
        self.current_line = 0
        self.current_lift = 0
        self.awaiting_lift = None
        self.error = None

    def confirm_reposition(self, lift_index) -> Optional[str]:
        """
        Operator confirmation that the frame has been repositioned for
        `lift_index` (0-based: the lift about to start). Naming the lift makes
        a stale or repeated confirmation harmless. Returns a refusal reason,
        or None when accepted (the runner then starts that lift).
        """
        if self.state != "awaiting_reposition" or self.awaiting_lift is None:
            return "No frame reposition is awaiting confirmation."
        if lift_index != self.awaiting_lift:
            return (f"Confirmation was for lift {lift_index + 1 if isinstance(lift_index, int) else lift_index}, "
                    f"but lift {self.awaiting_lift + 1} is the one waiting.")
        self.awaiting_lift = None
        self.state = "running"
        return None

    def to_dict(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at else None
        lift = self.lifts[self.current_lift]
        lift_line = max(0, self.current_line - self.lift_start_line(self.current_lift))
        d: dict = {
            "filename": self.filename,
            "kind": self.kind,
            "name": self.name,
            "units": self.units,
            "total_lines": self.total_lines,
            "state": self.state,
            "current_line": self.current_line,
            "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
            "total_lifts": self.total_lifts,
            "current_lift": self.current_lift,
            "awaiting_lift": self.awaiting_lift,
            "lift_line": min(lift_line, len(lift.lines)),
            "lift_total_lines": len(lift.lines),
            "lifts": [l.to_dict() for l in self.lifts],
        }
        if self.error:
            d["error"] = self.error
        return d


def _clean_lines(raw_lines) -> list[str]:
    lines: list[str] = []
    for raw_line in raw_lines:
        cleaned = prepare_gcode_line(raw_line)
        if cleaned:
            lines.append(cleaned)
    return lines


def is_building_job(filename: str, text: str) -> bool:
    """A .mudjob/.json file, or any upload whose content is a JSON object."""
    suffix = Path(filename).suffix.lower()
    return suffix in (".mudjob", ".json") or text.lstrip().startswith("{")


def parse_building_job(text: str) -> dict:
    """
    Validate a building job document and return its normalised content:
    {name, units, lifts: [JobLift…]}. Raises JobFormatError.
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JobFormatError(f"not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise JobFormatError("job must be a JSON object")
    if doc.get("format") != JOB_FORMAT:
        raise JobFormatError(f"'format' must be '{JOB_FORMAT}'")
    if doc.get("version") != JOB_FORMAT_VERSION:
        raise JobFormatError(
            f"unsupported job version {doc.get('version')!r} (this interface reads version {JOB_FORMAT_VERSION})"
        )
    raw_lifts = doc.get("lifts")
    if not isinstance(raw_lifts, list) or not raw_lifts:
        raise JobFormatError("'lifts' must be a non-empty list")

    lifts: list[JobLift] = []
    for pos, raw in enumerate(raw_lifts):
        where = f"lift {pos + 1}"
        if not isinstance(raw, dict):
            raise JobFormatError(f"{where}: must be an object")
        if "index" in raw and raw["index"] != pos:
            raise JobFormatError(
                f"{where}: 'index' is {raw['index']!r} but lifts must be listed in order (expected {pos})"
            )
        gcode = raw.get("gcode")
        if not isinstance(gcode, list) or not all(isinstance(g, str) for g in gcode):
            raise JobFormatError(f"{where}: 'gcode' must be a list of strings")
        lines = _clean_lines(gcode)
        if not lines:
            raise JobFormatError(f"{where}: contains no sendable G-code")
        for n, line in enumerate(lines, start=1):
            reason = program_control_reason(line)
            if reason:
                raise JobFormatError(f"{where}, line {n} ({line}): {reason} is not allowed inside a lift")
        repo = raw.get("reposition_before")
        if repo is not None:
            if pos == 0:
                raise JobFormatError("lift 1: 'reposition_before' is only valid for later lifts")
            if not isinstance(repo, dict):
                raise JobFormatError(f"{where}: 'reposition_before' must be an object")
        desc = raw.get("description")
        if desc is not None and not isinstance(desc, str):
            raise JobFormatError(f"{where}: 'description' must be a string")
        lifts.append(JobLift(index=pos, lines=lines, description=desc, reposition_before=repo))

    name = doc.get("name")
    units = doc.get("units")
    return {
        "name": name if isinstance(name, str) else None,
        "units": units if isinstance(units, str) else None,
        "lifts": lifts,
    }


def load_job_from_file(filepath: Path) -> CurrentJob:
    """Read a flat G-code file or a building job and return a CurrentJob."""
    raw = filepath.read_text(encoding="utf-8", errors="replace")
    if is_building_job(filepath.name, raw):
        parsed = parse_building_job(raw)
        return CurrentJob(
            filename=filepath.name,
            filepath=filepath,
            lifts=parsed["lifts"],
            kind="building",
            name=parsed["name"],
            units=parsed["units"],
        )
    return CurrentJob(
        filename=filepath.name,
        filepath=filepath,
        lines=_clean_lines(raw.splitlines()),
    )
