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

import bisect
import itertools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..grbl.parser import lift_gcode_reason, prepare_gcode_line, program_control_reason
from .layout import LiftLayout
from .preflight import job_preflight
from .motion import analyze


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
    # indices into `lines` of non-printing moves (job `travel_lines`), if given
    travel: Optional[list] = None

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


_JOB_SEQ = itertools.count(1)


@dataclass
class CurrentJob:
    filename: str
    filepath: Path
    lines: list[str] = field(default_factory=list)   # all sendable lines, in order
    lifts: list[JobLift] = field(default_factory=list)   # the EXPORTED lifts (never changed)
    kind: str = "gcode"   # gcode (flat file, one lift) | building (.mudjob)
    name: Optional[str] = None
    units: Optional[str] = None   # informational only — never converted
    executable: bool = True       # False: structure only (no G-code) — displayed, never run
    xy_bounds: Optional[dict] = None   # building XY extent in work coordinates (display only)
    lift_plan: Optional[dict] = None   # {target_lift_height, max_z_travel, layer_height} (display / checks)
    reentry: bool = False              # every layer mark starts a re-entry segment
    machine: Optional[dict] = None     # the job's `machine` block (display: planned feeds)
    phase: Optional[str] = None        # "origin" while returning to the print origin
    unresolved: list = field(default_factory=list)   # [{id, summary}] why not executable
    state: str = "ready"  # ready | running | held | awaiting_reposition | stopped | completed | error
    current_line: int = 0         # lines acknowledged, across the whole job
    current_lift: int = 0         # the EFFECTIVE lift being (or last) streamed
    awaiting_lift: Optional[int] = None   # the effective lift waiting for reposition confirmation
    started_at: Optional[float] = None
    error: Optional[str] = None
    # MACHINE (2026-10-15): what the job SAYS (reported, never trusted over the
    # controller's own check) and the controller's own profile (authoritative)
    prepared_for: Optional[dict] = None
    designer_preflight: Optional[dict] = None
    declared_requirements: Optional[dict] = None
    printer: Optional[dict] = None           # THIS controller's machine profile (attached by the server)
    observed_layer: Optional[float] = None   # operator's layer thickness (in) — the physical one; default nominal
    target_lift: Optional[float] = None      # operator's target lift height (in); default lift_plan
    rapid_in_min: Optional[float] = None     # GRBL max rate, for G0 estimates (flat G-code)

    def __post_init__(self) -> None:
        if not self.lifts:
            self.lifts = [JobLift(index=0, lines=list(self.lines))]
        else:
            self.lines = [line for lift in self.lifts for line in lift.lines]
        self.job_id = next(_JOB_SEQ)
        self.layout = LiftLayout(self.lifts, self.lift_plan, self.reentry)
        travel = None
        if any(l.travel is not None for l in self.lifts):
            travel = {self.layout.offsets[i] + t for i, l in enumerate(self.lifts) for t in (l.travel or [])}
        self.profile = analyze(self.lines, travel, self.rapid_in_min)
        self.waiting_s = 0.0                     # time spent awaiting frame repositions
        self.awaiting_since: Optional[float] = None
        self.adjustments: list[dict] = []        # operator lift-boundary changes (log)

    @property
    def total_lines(self) -> int:
        return len(self.lines)

    @property
    def total_lifts(self) -> int:
        """Number of EFFECTIVE lifts (the exported ones unless the operator moved a boundary)."""
        return self.layout.count

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def lift_start_line(self, lift_index: int) -> int:
        """Global line offset of the first line of an effective lift."""
        return self.layout.range_of(lift_index)[0]

    def reset_progress(self) -> None:
        self.phase = None
        self.state = "ready"
        self.current_line = 0
        self.current_lift = 0
        self.awaiting_lift = None
        self.error = None
        self.waiting_s = 0.0
        self.awaiting_since = None

    # --- frame-reposition waits (excluded from motion time) ---

    def begin_wait(self) -> None:
        self.awaiting_since = time.time()

    def end_wait(self) -> None:
        if self.awaiting_since is not None:
            self.waiting_s += time.time() - self.awaiting_since
            self.awaiting_since = None

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
        self.end_wait()
        return None

    # --- operator-observed layer thickness and lift-boundary adjustment ---

    def max_z_travel(self) -> Optional[float]:
        """Z travel per frame position: THIS printer's profile when set (it is
        authoritative), else what the job was planned with."""
        z = (self.printer or {}).get("z_travel_per_frame")
        return z if z else (self.lift_plan or {}).get("max_z_travel")

    def motion_bounds(self) -> Optional[dict]:
        """XY extent of every move of the G-code (work coordinates, inches; cached)."""
        if not hasattr(self, "_motion_bounds"):
            self._motion_bounds = self.profile.bounds()
        return self._motion_bounds

    def layer_thickness(self) -> Optional[float]:
        """The thickness used for physical estimates: observed, else nominal."""
        return self.observed_layer or self.layout.nominal_layer

    def target_lift_height(self) -> Optional[float]:
        return self.target_lift or (self.lift_plan or {}).get("target_lift_height")

    def propose_lift_end(self, target: float, thickness: float) -> dict:
        """
        From the operator's physical variables to a SAFE lift end (2026-10-14
        UI pass). Layers needed = floor(target / thickness); the proposal is
        the clean-cut end of the adjustable lift with the most layers not
        above that (else the earliest end still available). The Pi never
        invents layers: when the exact count is not available it says why.
        Same rule in the UI (app.js `proposeLiftEnd`) for the live preview.
        """
        k = self.adjustable_lift()
        out = {"lift": k, "needed": None, "choice": None, "exact": False, "reason": None}
        if k is None:
            out["reason"] = ("Lift ends cannot be adjusted for this job now"
                             + ("" if self.layout.adjustable else " (no re-entry layer marks — re-export it)") + ".")
            return out
        needed = max(1, int(target / thickness + 1e-9))
        out["needed"] = needed
        mz = self.max_z_travel()
        cands = [c for c in self.end_candidates()
                 if mz is None or c["layers"] * thickness <= mz + 1e-9]
        if not cands:
            out["reason"] = "No safe whole-layer end is available for this lift."
            return out
        below = [c for c in cands if c["layers"] <= needed]
        choice = max(below, key=lambda c: c["layers"]) if below else min(cands, key=lambda c: c["layers"])
        out["choice"], out["exact"] = choice, choice["layers"] == needed
        if choice["layers"] > needed:
            out["reason"] = (f"This lift already has {choice['layers'] - 1} or more layers under way — "
                             f"the earliest end still possible is after {choice['layers']} layers.")
        elif choice["layers"] < needed:
            if choice["end_of_job"]:
                out["reason"] = f"Only {choice['layers']} layers remain in the job."
            elif any(c["layers"] > choice["layers"] for c in cands):
                # longer ends exist, but none at exactly `needed`: no clean cut there
                out["reason"] = (f"No safe whole-layer end at {needed} layers here (layers of different islands "
                                 f"interleave) — nearest below: {choice['layers']}.")
            else:
                out["reason"] = (f"{needed} layers × {thickness:g} in would exceed the machine's Z travel — "
                                 f"limited to {choice['layers']}.")
        return out

    def apply_lift_parameters(self, lift_index, target: float, thickness: float) -> tuple:
        """Store the operator's target / thickness and move the adjustable
        lift's end to the proposed safe cut. Returns (refusal, info)."""
        k = self.adjustable_lift()
        if k is None or lift_index != k:
            return (self.propose_lift_end(target, thickness)["reason"] if k is None
                    else f"The parameters apply to lift {k + 1} now.", None)
        mz = self.max_z_travel()
        if mz is not None and target > mz + 1e-9:
            return (f"The target lift height cannot exceed the machine's Z travel ({mz:g} in).", None)
        prop = self.propose_lift_end(target, thickness)
        c = prop["choice"]
        if c is None:
            return (prop["reason"], None)
        self.observed_layer, self.target_lift = thickness, target
        if c["planned"]:
            return (None, f"Lift {k + 1} keeps its end after {c['layers']} layers." +
                    (f" {prop['reason']}" if prop["reason"] else ""))
        refusal, warning = self.adjust_lift_end(k, c["at"])
        if refusal:
            return (refusal, None)
        info = f"Lift {k + 1} now ends after building layer {c['end_layer'] + 1} ({c['layers']} layers)."
        return (None, " ".join(x for x in (info, prop["reason"], warning) if x))

    def adjustable_lift(self) -> Optional[int]:
        """The effective lift whose END the operator may move now, if any."""
        if not (self.layout.adjustable and self.executable):
            return None
        if self.state == "awaiting_reposition" and self.awaiting_lift is not None:
            return self.awaiting_lift - 1            # just printed: may continue into more layers
        if self.state in ("ready", "running", "held"):
            return self.current_lift
        return None

    def end_candidates(self) -> list:
        k = self.adjustable_lift()
        if k is None:
            return []
        lp = self.lift_plan or {}
        after = self.current_line if self.state in ("running", "held", "awaiting_reposition") else 0
        if self.state == "awaiting_reposition":
            after = self.layout.range_of(k)[1]       # the lift is printed through its current end
        return self.layout.candidates(k, after, self.layer_thickness(),
                                      self.max_z_travel(), lp.get("target_lift_height"))

    def adjust_lift_end(self, lift_index, at) -> tuple:
        """Move the end of the adjustable lift to clean cut `at`.
        Returns (refusal or None, warning or None)."""
        k = self.adjustable_lift()
        if k is None:
            return ("Lift boundaries cannot be adjusted for this job now"
                    + ("" if self.layout.adjustable else
                       " (the job has no re-entry layer marks — re-export it from the Designer)") + ".", None)
        if lift_index != k:
            return (f"Only the end of lift {k + 1} can be adjusted now.", None)
        cands = {c["at"]: c for c in self.end_candidates()}
        c = cands.get(at)
        if c is None:
            return ("That is not an allowed lift end (only whole-layer clean cuts after what has been sent).", None)
        if c["exceeds_max"]:
            return (f"Refused: {c['layers']} layers at {self.layer_thickness():g} in ≈ {c['physical_estimate']:g} in "
                    f"exceeds the machine's Z travel.", None)
        start, old_end = self.layout.range_of(k)
        self.layout.set_end(k, at)
        self.adjustments.append({"lift": k, "from_line": old_end, "to_line": at, "end_layer": c["end_layer"],
                                 "layers": c["layers"], "thickness": self.layer_thickness(), "t": time.time()})
        warning = None
        if self.state == "awaiting_reposition" and not self.layout.is_boundary(old_end):
            # the operator continues the just-printed lift: no reposition here
            self.awaiting_lift = None
            self.state = "running"
            self.end_wait()
        if k + 1 < self.layout.count:
            nxt = self.layout.layers_in(*self.layout.range_of(k + 1))
            t = self.layer_thickness()
            mz = self.max_z_travel()
            if t and mz and len(nxt) * t > mz + 1e-9:
                warning = (f"Lift {k + 2} now holds {len(nxt)} layers ≈ {len(nxt) * t:g} in at {t:g} in/layer — "
                           f"more than the {mz:g} in Z travel: end it early when you reach it.")
        return (None, warning)

    def restore_planned_lifts(self) -> Optional[str]:
        if self.is_active and self.state != "awaiting_reposition":
            return "Cannot restore the planned lifts while the job is running."
        if self.state == "awaiting_reposition":
            return "Cannot restore the planned lifts while awaiting a frame reposition."
        self.layout.boundaries = list(self.layout.offsets)
        self.layout._bset = set(self.layout.boundaries)
        self.adjustments.clear()
        self.observed_layer = None        # back to the Slicer's plan: nominal thickness, planned target
        self.target_lift = None
        return None

    # --- outline (controller feature; never changes the job) ---

    def outline(self) -> Optional[dict]:
        """The rectangle Outline traces (work coordinates, inches): the job's
        `xy_bounds`, else the extent of its G-code moves."""
        if not self.executable:
            return None
        if hasattr(self, "_outline"):
            return self._outline
        self._outline = self._compute_outline()
        return self._outline

    def _compute_outline(self) -> Optional[dict]:
        b, source = self.xy_bounds, "job xy_bounds"
        if not b:
            b = self.profile.bounds()
            source = "G-code moves" + ("" if self.kind == "building" else
                                       " (flat file: assumes its coordinates are work coordinates)")
        if not b:
            return None
        x0, y0, x1, y1 = b["min_x"], b["min_y"], b["max_x"], b["max_y"]
        corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        return {"corners": [[round(x, 4), round(y, 4)] for x, y in corners], "source": source,
                "width": round(x1 - x0, 4), "height": round(y1 - y0, 4)}

    # --- progress ---

    def progress(self) -> dict:
        """Estimates (motion only, 100 % feed) and progress at job / lift / layer level."""
        P, L = self.profile, self.layout
        g = min(self.current_line, self.total_lines)
        k = min(self.current_lift, L.count - 1)
        start, end = L.range_of(k)
        job_t, lift_t = P.between(0, self.total_lines), P.between(start, end)
        done_t, lift_done = P.between(0, g), P.between(start, max(start, min(g, end)))
        now = time.time()
        elapsed = (now - self.started_at) if self.started_at else None
        waiting = self.waiting_s + ((now - self.awaiting_since) if self.awaiting_since else 0.0)
        out = {
            "estimate": True,
            "unknown_lines": P.unknown_lines,
            "classified": P.classified,
            "total_s": round(job_t["time_s"], 1),
            "remaining_s": round(job_t["time_s"] - done_t["time_s"], 1),
            "lift_total_s": round(lift_t["time_s"], 1),
            "lift_remaining_s": round(lift_t["time_s"] - lift_done["time_s"], 1),
            "fraction": round(done_t["time_s"] / job_t["time_s"], 4) if job_t["time_s"] > 0
            else (round(g / self.total_lines, 4) if self.total_lines else 0.0),
            "lift_fraction": round(lift_done["time_s"] / lift_t["time_s"], 4) if lift_t["time_s"] > 0 else 0.0,
            "print_in": round(job_t["print_in"], 1),
            "print_done_in": round(done_t["print_in"], 1),
            "lift_print_in": round(lift_t["print_in"], 1),
            "lift_print_done_in": round(lift_done["print_in"], 1),
            "path_in": round(job_t["path_in"], 1),
            "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
            "waiting_s": round(waiting, 1),
            "motion_elapsed_s": round(elapsed - waiting, 1) if elapsed is not None else None,
        }
        lp = self.lift_plan or {}
        t_obs = self.layer_thickness()
        layers = L.layers_in(start, end)
        seg = L.segment_at(g)
        lift = {
            "nominal_layer": L.nominal_layer,
            "observed_layer": self.observed_layer,
            "target_lift_height": self.target_lift_height(),
            "planned_target_lift_height": lp.get("target_lift_height"),
            "max_z_travel": self.max_z_travel(),
            "layers": len(layers) or None,
            "adjustable": L.adjustable,
        }
        if layers:
            done_layers = L.completed_layers(start, end, g)
            base = L.frame_base(k)
            lift["completed_layers"] = done_layers
            lift["planned_height_nominal"] = (round(L.layer_top[layers[-1]] - base, 4)
                                              if base is not None and layers[-1] in L.layer_top else None)
            if t_obs:
                lift["physical_lift_estimate"] = round(len(layers) * t_obs, 3)
                lift["physical_done_estimate"] = round(done_layers * t_obs, 3)
            if seg is not None and start <= seg[0] < end:
                lift["layer"] = seg[1]
                lift["layer_in_lift"] = layers.index(seg[1]) + 1 if seg[1] in layers else None
                if isinstance(seg[2], (int, float)):
                    lift["z"] = seg[2]
                    if base is not None:
                        lift["z_in_lift"] = round(seg[2] - base, 4)
        out["lift"] = lift
        return out

    def to_dict(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at else None
        L = self.layout
        k = min(self.current_lift, L.count - 1)
        start, end = L.range_of(k)
        lift_line = max(0, self.current_line - start)
        orig = self.lifts[min(bisect.bisect_right(L.offsets, start) - 1, len(self.lifts) - 1)]
        mark = None
        seg = L.segment_at(min(self.current_line, self.total_lines))
        if seg is not None:
            base = L.frame_base(k)
            mark = {"layer": seg[1], "z": seg[2],
                    "z_in_lift": round(seg[2] - base, 6) if base is not None and seg[2] is not None else None}
        elif orig.marks:
            mark = orig.mark_at(max(0, self.current_line - L.offsets[orig.index]))
        # First commanded XY destination (inches, work coordinates) + whether
        # it is a travel (G0) or feed (G1/G2/G3) move. Twelfth-pass incident:
        # the operator didn't realize the first move was a 42-inch diagonal
        # travel to the start of the toolpath. Exposing this + the expected
        # distance from the current WPos lets the UI say "the next motion
        # will travel 42 inches" before Start.
        first_motion = None
        if self.profile and len(self.profile.pt_x):
            first_motion = {
                "x_in": round(float(self.profile.pt_x[0]), 4),
                "y_in": round(float(self.profile.pt_y[0]), 4),
                "kind": "travel" if int(self.profile.pt_kind[0]) == 1 else "print",
                "line": int(self.profile.pt_line[0]),
            }
        d: dict = {
            "job_id": self.job_id,
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
            "lift_line": min(lift_line, end - start),
            "lift_total_lines": end - start,
            "lifts": [L.describe(i) for i in range(L.count)],
            "lifts_adjusted": bool(self.adjustments),
            "phase": self.phase,
            "xy_bounds": self.xy_bounds,
            "lift_plan": self.lift_plan,
            "machine": self.machine,
            "current_mark": mark,
            "progress": self.progress(),
            "outline": self.outline(),
            "first_motion": first_motion,
            "preflight": job_preflight(self, self.printer) if self.printer else None,
            "prepared_for": self.prepared_for,
            "adjust": {"lift": self.adjustable_lift(), "candidates": self.end_candidates()}
            if self.layout.adjustable else None,
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
    reentry = doc.get("reentry_marks") is True

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
        travel = _travel_lines(raw, where) if executable else None
        if executable and reentry:
            _check_reentry(lines, marks, travel, where)
        lifts.append(JobLift(index=pos, lines=lines, description=desc, reposition_before=repo, meta=meta,
                             marks=marks, travel=travel))

    name = doc.get("name")
    units = doc.get("units")
    bounds = doc.get("xy_bounds")
    if bounds is not None and not (isinstance(bounds, dict) and all(
            isinstance(bounds.get(k), (int, float)) for k in ("min_x", "min_y", "max_x", "max_y"))):
        raise JobFormatError("'xy_bounds' must be {min_x, min_y, max_x, max_y} numbers")
    lift_plan = doc.get("lift_plan")
    if lift_plan is not None:
        if not isinstance(lift_plan, dict):
            raise JobFormatError("'lift_plan' must be an object")
        for key in ("target_lift_height", "max_z_travel", "layer_height"):
            v = lift_plan.get(key)
            if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
                raise JobFormatError(f"'lift_plan.{key}' must be a positive number or null")
    machine = doc.get("machine") if isinstance(doc.get("machine"), dict) else None
    extra = {}
    for key in ("prepared_for", "preflight", "requirements"):        # optional, reported only
        v = doc.get(key)
        if v is not None and not isinstance(v, dict):
            raise JobFormatError(f"'{key}' must be an object")
        extra[key] = v
    return {
        "prepared_for": extra["prepared_for"],
        "designer_preflight": extra["preflight"],
        "declared_requirements": extra["requirements"],
        "name": name if isinstance(name, str) else None,
        "units": units if isinstance(units, str) else None,
        "xy_bounds": bounds,
        "lift_plan": lift_plan,
        "reentry": reentry,
        "machine": machine,
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
    sendable_before = _sendable_index(gcode)
    out = []
    for m in sorted(marks, key=lambda m: m["line"]):
        d = {k: m[k] for k in ("layer", "pass", "z", "z_in_lift") if isinstance(m.get(k), (int, float))}
        d["line"] = sendable_before[m["line"]]
        out.append(d)
    return out


def _sendable_index(gcode: list) -> list:
    """sendable_before[i] = number of sendable lines before raw index i (len + 1 entries)."""
    out, n = [], 0
    for g in gcode:
        out.append(n)
        if prepare_gcode_line(g):
            n += 1
    out.append(n)
    return out


def _travel_lines(raw: dict, where: str) -> Optional[list]:
    """A lift's optional `travel_lines` (raw gcode indices) as sendable-line indices."""
    tl = raw.get("travel_lines")
    if tl is None:
        return None
    gcode = raw["gcode"]
    if not isinstance(tl, list) or not all(isinstance(i, int) and not isinstance(i, bool)
                                           and 0 <= i < len(gcode) for i in tl):
        raise JobFormatError(f"{where}: 'travel_lines' must be a list of indices into 'gcode'")
    idx = _sendable_index(gcode)
    return sorted({idx[i] for i in tl if prepare_gcode_line(gcode[i])})


_XY_MOTION = ("X", "Y")


def _check_reentry(lines: list, marks: list, travel: Optional[list], where: str) -> None:
    """`reentry_marks`: every layer mark must begin a self-contained segment —
    units + distance mode, then a travel with an explicit feed as its first move."""
    travel_set = set(travel or [])
    for m in marks:
        i = m["line"]
        j = i
        while j < len(lines) and not any(a in lines[j].upper() for a in _XY_MOTION):
            j += 1
        if j >= len(lines):
            raise JobFormatError(f"{where}: layer mark at line {i} has no motion after it")
        reason = lift_gcode_reason(lines[i:j + 1])
        if reason or j not in travel_set or "F" not in lines[j].upper():
            raise JobFormatError(f"{where}: layer mark at line {i} is not a re-entry point "
                                 f"(needs units + distance mode, then a travel with a feed)")


def load_job_from_file(filepath: Path, rapid_in_min: Optional[float] = None) -> CurrentJob:
    """Read a flat G-code file or a building job and return a CurrentJob.
    `rapid_in_min`: GRBL's max rate (in/min) if known — for G0 time estimates."""
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
            lift_plan=parsed["lift_plan"],
            reentry=parsed["reentry"],
            machine=parsed["machine"],
            rapid_in_min=rapid_in_min,
            prepared_for=parsed["prepared_for"],
            designer_preflight=parsed["designer_preflight"],
            declared_requirements=parsed["declared_requirements"],
        )
    return CurrentJob(
        filename=filepath.name,
        filepath=filepath,
        lines=_clean_lines(raw.splitlines()),
        rapid_in_min=rapid_in_min,
    )
