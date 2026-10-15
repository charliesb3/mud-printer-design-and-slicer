"""
Represents the one currently-loaded job.

Exactly one job is loaded at a time. Uploading a new file replaces the
current job when safe to do so.

A job is an ordered sequence of LIFTS. The `.mudjob` file is the only
interface to Design + Toolpath — contract: contracts/mudjob/README.md at the
repository root. Nothing here imports or knows the Designer.
- A building job (.mudjob JSON) carries its lifts explicitly. The lifts are
  decided upstream by the authoritative PrintPlan; the Pi never recalculates
  them. Between lifts the runner stops and waits for the operator to confirm
  the physical frame reposition.
- A NON-EXECUTABLE building job (`executable: false`) describes the building
  and its lifts but carries no G-code and names what is unresolved: it is
  displayed, never run.
- An ordinary flat G-code file is a job with exactly one lift, so it runs
  exactly as in Milestone 1.
- A Designer project file (.muddesign) is authoring state, never a job.

Underneath, every lift is ordinary GRBL G-code.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..grbl.parser import lift_gcode_reason, prepare_gcode_line, program_control_reason


JOB_FORMAT = "mudprinter-job"
JOB_FORMAT_VERSION = 1

# awaiting_reposition counts as active: the job owns the machine between lifts.
ACTIVE_STATES = {"running", "held", "awaiting_reposition"}


# Display-only lift metadata passed through to clients (never interpreted).
LIFT_META_KEYS = ("layers", "z_bottom", "z_top", "stages", "passes", "runs", "print_length", "travel_length")

# A Designer project file, recognised only to refuse it with a clear message.
PROJECT_FILE_SUFFIX = ".muddesign"
PROJECT_FILE_FORMAT = "mud-printer-project"


class JobFormatError(ValueError):
    """The uploaded building job is not a valid job file."""


@dataclass
class JobLift:
    index: int                                   # 0-based position in the job
    lines: list[str]                             # sendable lines (comments stripped)
    description: Optional[str] = None
    reposition_before: Optional[dict] = None     # shown to the operator, never interpreted
    meta: dict = field(default_factory=dict)     # LIFT_META_KEYS, display only
    # layer / Z metadata for the operator (manual Z): [{line, layer, z,
    # z_in_lift, pass}] with `line` = index into `lines` where it begins
    marks: list = field(default_factory=list)

    def mark_at(self, line: int) -> Optional[dict]:
        """The layer mark in effect once `line` lines of this lift have been sent."""
        cur = None
        for m in self.marks:
            if m["line"] <= line:
                cur = m
            else:
                break
        return cur if cur is not None else (self.marks[0] if self.marks else None)

    def to_dict(self) -> dict:
        d: dict = {"index": self.index, "total_lines": len(self.lines)}
        if self.description:
            d["description"] = self.description
        if self.reposition_before:
            d["reposition_before"] = self.reposition_before
        d.update(self.meta)
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
    executable: bool = True       # False: structure only (no G-code) — displayed, never run
    xy_bounds: Optional[dict] = None   # building XY extent in work coordinates (display only)
    phase: Optional[str] = None        # "origin" while returning to the print origin
    unresolved: list = field(default_factory=list)   # [{id, summary}] why not executable
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
        self.phase = None
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
            "executable": self.executable,
            "unresolved": list(self.unresolved),
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
            "phase": self.phase,
            "xy_bounds": self.xy_bounds,
            "current_mark": lift.mark_at(min(lift_line, len(lift.lines))),
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
    """A .mudjob/.json/.muddesign file, or any upload whose content is a JSON object."""
    suffix = Path(filename).suffix.lower()
    return suffix in (".mudjob", ".json", PROJECT_FILE_SUFFIX) or text.lstrip().startswith("{")


_PROJECT_FILE_MESSAGE = (
    "this is a Designer project file (.muddesign): authoring state, not a print job. "
    "In the Designer, plan the print and use Export Print Job to create a .mudjob."
)


def _string_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(g, str) for g in v)


def parse_building_job(text: str, filename: str = "") -> dict:
    """
    Validate a `.mudjob` document (contracts/mudjob/README.md, version 1) and
    return its normalised content: {name, units, executable, unresolved,
    lifts: [JobLift…]}. Raises JobFormatError.
    """
    if Path(filename).suffix.lower() == PROJECT_FILE_SUFFIX:
        raise JobFormatError(_PROJECT_FILE_MESSAGE)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JobFormatError(f"not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise JobFormatError("job must be a JSON object")
    if doc.get("format") == PROJECT_FILE_FORMAT:
        raise JobFormatError(_PROJECT_FILE_MESSAGE)
    if doc.get("format") != JOB_FORMAT:
        raise JobFormatError(f"'format' must be '{JOB_FORMAT}'")
    if doc.get("version") != JOB_FORMAT_VERSION:
        raise JobFormatError(
            f"unsupported job version {doc.get('version')!r} (this interface reads version {JOB_FORMAT_VERSION})"
        )
    executable = doc.get("executable")
    if not isinstance(executable, bool):
        raise JobFormatError("'executable' must be true or false")
    unresolved = doc.get("unresolved") or []
    if not isinstance(unresolved, list) or not all(
        isinstance(u, dict) and isinstance(u.get("id"), str) and isinstance(u.get("summary"), str)
        for u in unresolved
    ):
        raise JobFormatError("'unresolved' must be a list of {id, summary} strings")
    if executable and unresolved:
        raise JobFormatError("an executable job cannot list unresolved items")
    if not executable and not unresolved:
        raise JobFormatError("a non-executable job must say what is unresolved")
    raw_lifts = doc.get("lifts")
    if not isinstance(raw_lifts, list) or not raw_lifts:
        raise JobFormatError("'lifts' must be a non-empty list")

    lifts: list[JobLift] = []
    for pos, raw in enumerate(raw_lifts):
        where = f"lift {pos + 1}"
        if not isinstance(raw, dict):
            raise JobFormatError(f"{where}: must be an object")
        if raw.get("index") != pos or isinstance(raw.get("index"), bool):
            raise JobFormatError(
                f"{where}: 'index' is {raw.get('index')!r} but lifts must be listed in order (expected {pos})"
            )
        lines: list[str] = []
        if executable:
            gcode = raw.get("gcode")
            if not _string_list(gcode):
                raise JobFormatError(f"{where}: 'gcode' must be a list of strings")
            lines = _clean_lines(gcode)
            if not lines:
                raise JobFormatError(f"{where}: contains no sendable G-code")
            for n, line in enumerate(lines, start=1):
                reason = program_control_reason(line)
                if reason:
                    raise JobFormatError(f"{where}, line {n} ({line}): {reason} is not allowed inside a lift")
            reason = lift_gcode_reason(lines)
            if reason:
                raise JobFormatError(f"{where}, {reason}")
        elif "gcode" in raw:
            raise JobFormatError(f"{where}: a non-executable job carries no 'gcode'")
        repo = raw.get("reposition_before")
        if pos == 0 and repo is not None:
            raise JobFormatError("lift 1: 'reposition_before' is only valid for later lifts")
        if pos > 0 and not isinstance(repo, dict):
            raise JobFormatError(f"{where}: 'reposition_before' (the frame reposition before it) must be an object")
        desc = raw.get("description")
        if desc is not None and not isinstance(desc, str):
            raise JobFormatError(f"{where}: 'description' must be a string")
        meta = {k: raw[k] for k in LIFT_META_KEYS if k in raw}
        marks = _layer_marks(raw, where) if executable else []
        lifts.append(JobLift(index=pos, lines=lines, description=desc, reposition_before=repo, meta=meta,
                             marks=marks))

    name = doc.get("name")
    units = doc.get("units")
    bounds = doc.get("xy_bounds")
    if bounds is not None and not (isinstance(bounds, dict) and all(
            isinstance(bounds.get(k), (int, float)) for k in ("min_x", "min_y", "max_x", "max_y"))):
        raise JobFormatError("'xy_bounds' must be {min_x, min_y, max_x, max_y} numbers")
    return {
        "name": name if isinstance(name, str) else None,
        "units": units if isinstance(units, str) else None,
        "xy_bounds": bounds,
        "executable": executable,
        "unresolved": [{"id": u["id"], "summary": u["summary"]} for u in unresolved],
        "lifts": lifts,
    }


def _layer_marks(raw: dict, where: str) -> list:
    """A lift's optional `layer_marks` (display only), with `line` converted
    from an index into the raw `gcode` list to an index into the sendable
    (comment-stripped) lines."""
    marks = raw.get("layer_marks")
    if marks is None:
        return []
    gcode = raw["gcode"]
    if not isinstance(marks, list) or not all(
            isinstance(m, dict) and isinstance(m.get("line"), int) and not isinstance(m.get("line"), bool)
            and 0 <= m["line"] <= len(gcode) for m in marks):
        raise JobFormatError(f"{where}: 'layer_marks' must be a list of {{line, …}} with line indices into 'gcode'")
    sendable_before, n = [], 0
    for g in gcode:
        sendable_before.append(n)
        if prepare_gcode_line(g):
            n += 1
    sendable_before.append(n)
    out = []
    for m in sorted(marks, key=lambda m: m["line"]):
        d = {k: m[k] for k in ("layer", "pass", "z", "z_in_lift") if isinstance(m.get(k), (int, float))}
        d["line"] = sendable_before[m["line"]]
        out.append(d)
    return out


def load_job_from_file(filepath: Path) -> CurrentJob:
    """Read a flat G-code file or a building job and return a CurrentJob."""
    raw = filepath.read_text(encoding="utf-8", errors="replace")
    if is_building_job(filepath.name, raw):
        parsed = parse_building_job(raw, filepath.name)
        return CurrentJob(
            filename=filepath.name,
            filepath=filepath,
            lifts=parsed["lifts"],
            kind="building",
            name=parsed["name"],
            units=parsed["units"],
            executable=parsed["executable"],
            unresolved=parsed["unresolved"],
            xy_bounds=parsed["xy_bounds"],
        )
    return CurrentJob(
        filename=filepath.name,
        filepath=filepath,
        lines=_clean_lines(raw.splitlines()),
    )
