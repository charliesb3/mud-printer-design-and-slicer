# `.mudjob` — Machine Job Interchange Contract, version 1

The ONLY integration boundary between **Design + Toolpath** (the Designer /
Slicer) and the **Pi interface** (printer controller). The two are independent
applications: neither imports, calls, or requires the other. Design + Toolpath
serializes its authoritative PrintPlan into this format; the Pi parses and
validates it. A `.mudjob` exported on one computer can be copied (USB, email,
network) to an independent Raspberry Pi and opened there with no knowledge of
the Designer.

| File | Role |
|---|---|
| `.muddesign` | AUTHORING project (Designer Save / Open). Never a print job; the Pi refuses it. |
| `.mudjob` | MACHINE JOB: one whole-building print, as ordered first-class lifts. |
| plain G-code | Less-rich external input; the Pi runs it as a single-lift job. |

This directory holds the contract: this spec and `fixtures/` (example files
both subsystems test against, as data — never shared code).

Version history: version 1 was drafted during Pi P1 (lifts with `gcode`
only) and finalized in P3 (2026-10-14) — adding `executable` / `unresolved`,
requiring `index` and later lifts' `reposition_before` — before any exporter
existed. The P1 draft is superseded; no files in that form were produced.
Current-machine pass (2026-10-14): the Designer now exports EXECUTABLE jobs;
executable lifts must set units + distance mode before motion and carry no Z
/ G10 / G92; optional `machine`, `xy_bounds`, `layer_marks` added
(display-only, within version 1). Controller feature pass (2026-10-14):
optional `lift_plan`, `reentry_marks` and per-lift `travel_lines` added
(within version 1; older files stay valid — a controller simply offers no
lift-boundary adjustment without `reentry_marks`). Machine profile pass
(2026-10-15): optional `requirements`, `prepared_for` and `preflight`
(within version 1; informational — see "Machine assumptions").

## Document

UTF-8 JSON, extension `.mudjob`.

```json
{
  "format": "mudprinter-job",
  "version": 1,
  "executable": true,
  "name": "optional display name",
  "units": "in",
  "source": {"generator": "…", "generated_at": "…", "speeds_in_s": {"print": 2, "travel": 6},
             "placeholder_speeds": true},
  "machine": {"axes": "XY", "z": "manual", "extrusion": "manual",
              "feeds_in_min": {"print": 120, "travel": 360}},
  "xy_bounds": {"min_x": 0, "min_y": 0, "max_x": 140, "max_y": 30},
  "lift_plan": {"target_lift_height": 18, "max_z_travel": 18, "layer_height": 1.5},
  "reentry_marks": true,
  "lifts": [
    {"index": 0, "description": "Layers 1–12", "layers": {"first": 0, "last": 11, "count": 12},
     "z_bottom": 0, "z_top": 18,
     "layer_marks": [{"line": 0, "pass": 0, "layer": 0, "z": 1.5, "z_in_lift": 1.5}, "…"],
     "travel_lines": [1, "…"],
     "gcode": ["G20 G90", "G1 X0 Y0 F360", "G1 X0 Y30 F120", "…", "G20 G90", "G1 X0 Y0 F360", "…"]},
    {"index": 1, "reposition_before": {"rise": 18, "frame_z_from": 0, "frame_z_to": 18},
     "z_bottom": 18, "z_top": 36, "layer_marks": ["…"], "travel_lines": ["…"], "gcode": ["G20 G90", "…"]}
  ]
}
```

### Top level

| Field | Required | Meaning |
|---|---|---|
| `format` | yes | `"mudprinter-job"` |
| `version` | yes | `1` |
| `executable` | yes | `true`: every lift carries machine G-code and the job may run. `false`: the job describes the building / lift structure only and MUST NOT be run |
| `unresolved` | if not executable | non-empty list of `{id, summary}` strings: the decisions that prevent producing executable machine instructions. Absent or empty when executable |
| `lifts` | yes | non-empty list, in print order |
| `name` | no | display name |
| `units` | no | unit of every length in the job's METADATA (`z_*`, `rise`, lengths). Display only — consumers convert nothing |
| `source` | no | provenance (generator, time, planning settings, speeds, `placeholder_speeds`); ignored by the Pi |
| `machine` | no | what the job assumes, display only (current exports: `axes` "XY", `z` "manual", `extrusion` "manual", `feeds_in_min`) |
| `xy_bounds` | no | `{min_x, min_y, max_x, max_y}` of every move, in work coordinates (`units`) — shown to the operator; the controller's Outline traces it |
| `lift_plan` | no | `{target_lift_height, max_z_travel, layer_height}` — the planning targets (`units`; `layer_height` = the nominal thickness when uniform, else null). Display, progress and the controller's lift-height checks |
| `requirements` | no | what the job needs (`units`): `xy_bounds` (all motion), `print_bounds` (extrusion), `building_height` (highest layer top), `max_lift_height` (tallest lift = Z travel per frame needed), `lifts`, `layers` |
| `prepared_for` | no | the producer's machine profile the job was prepared against: `name`, `units`, `x_extent`, `y_extent`, `max_build_z`, `z_travel_per_frame` (null = not set), `provisional` (placeholder values), `envelope_origin` |
| `preflight` | no | the producer's check of `requirements` against `prepared_for`: `fits`, `checks` [{id x / y / z / lift, status ok / warn / fail / unchecked, required, limit, message}], `informational: true` |
| `reentry_marks` | no | `true`: every `layer_marks` line begins a RE-ENTRY segment (see below), so a controller may pause before any pass |

### Lift

| Field | Required | Meaning |
|---|---|---|
| `index` | yes | equals the lift's position (0-based) — lift identity and order |
| `gcode` | iff executable | list of G-code lines (comments / blanks stripped by the reader; at least one sendable line). Forbidden in a non-executable job |
| `reposition_before` | lifts after the first | the frame reposition that must happen before this lift (an object). Forbidden on the first lift. Display only; known keys: `rise`, `frame_z_from`, `frame_z_to`, `nozzle_at` [x, y], `nozzle_z`, `note` |
| `description` | no | display text |
| `layers` | no | `{first, last, count}` — the PrintPlan's global layer indices (0-based) in this lift |
| `z_bottom`, `z_top` | no | building Z range of the lift (`units`) |
| `stages`, `passes`, `runs`, `print_length`, `travel_length` | no | PrintPlan summary numbers, display only |
| `layer_marks` | no | `[{line, layer, pass, z, z_in_lift}]` in print order — `line` = index into this lift's `gcode` where that layer pass begins; nozzle Z (building, and above the lift's frame base) for the operator / future automatic Z. Never a command |
| `travel_lines` | no | indices into this lift's `gcode` of the non-printing moves (all other XY moves extrude) — progress / print distance / 2D view |

Unknown keys are ignored (optional, display-only additions are allowed
within version 1). Any change to a required field or to meaning bumps the
version; readers reject versions they do not know.

### Coordinates and execution semantics (current machine, 2026-10-14)

- G-code coordinates are WORK coordinates. Their origin (X0 Y0) is the print
  origin the operator establishes on the machine before printing; the
  building's own XY (the Designer's world coordinates) is written unchanged —
  the producer never invents a machine offset. All lifts share the same work
  coordinate system; the operator may re-establish it between lifts.
- XY-only: Z is manual (no Z words); Z / layer information travels only as
  metadata (`layer_marks`, `z_*`).
- Extrusion is manual and independent: no pump / extrusion commands, and
  travel is not altered for it.
- Priming is controller operation, never part of the job's toolpath.

### Machine assumptions (2026-10-15)

Machine profiles are independent configuration on each side. `.mudjob`
communicates job assumptions and requirements; it does not synchronize
applications. The producer records what it assumed (`prepared_for`), what
the job needs (`requirements`) and its own informational `preflight`; a
consumer controlling a physical machine checks the job against ITS OWN
profile, which is authoritative — it must never treat a producer's
`preflight.fits` as permission, and it computes the job's needs from the
job itself (G-code motion, lift / layer Z), using `requirements` only to
report discrepancies. Envelope convention (provisional): [0, X] × [0, Y]
from the print origin; a job larger than an extent fails, a job that fits
but lies outside the envelope placed at the origin warns.

### Re-entry segments and adjustable lift boundaries

With `reentry_marks: true`, the G-code at every layer mark is a
self-contained segment: a line setting units + distance mode (`G20 G90`),
then, as its first XY move, a TRAVEL (listed in `travel_lines`) with an
explicit `F` to the pass start (emitted even when the nozzle is already
there). A controller may therefore stop before any layer mark and resume
there after the operator has jogged / re-zeroed / primed, without generating
motion of its own; readers reject a file whose marks do not satisfy this.

The exported lifts are the PLANNED boundaries. A controller may let the
operator move the end of a lift (physical layers thinner or thicker than
nominal) ONLY to a CLEAN CUT: a layer mark before which every pass has a
lower `layer` than every pass after it (in global print order). This keeps
every line and the global order exactly as exported — including the
planner's island order — and only moves where the print pauses for the frame
reposition. Inside lifts whose islands interleave there are no clean cuts.

### Rules for lift G-code (executable jobs)

Ordinary GRBL G-code. The controller owns pauses, the end of the job and the
work coordinate system, so a lift may NOT contain `M0`, `M1`, `M2`, `M30`
(incl. `M00`…), `%`, `$` system commands, `G10` or `G92`, nor any `Z` word.
Every lift must set its units (`G20` / `G21`) and distance mode (`G90` /
`G91`) itself before any X/Y motion — it may not rely on modal state left by
an earlier lift or by the operator's jogging / re-zeroing.

## Producer: Design + Toolpath (`layer_assembly/machine_job.py`)

"Export Print Job" (Print Simulation workspace) serializes the CURRENT,
successfully planned PrintPlan: one lift per PrintPlan lift, in order, with
its layer range, Z range, summary numbers, the PrintPlan's `FrameMove` as
`reposition_before`, and its moves (`print_plan.items()` in print order) as
G-code. It reconstructs nothing.

Current output (`executable: true`, `reentry_marks: true`):
- every pass (layer piece): `G20 G90`, then an absolute travel `G1 X… Y…
  F<travel>` to its start, then absolute `G1` moves (4 decimals, F only when
  it changes): extrusion (print / retrace) at the print speed, travel and
  layer / island transitions at the travel speed (`G1`, not `G0`: the
  controller's rapid rate is not validated for this machine);
- `travel_lines`, `layer_marks`, `lift_plan` (lift height + max Z travel
  from the project's lift settings, the nominal layer height);
- speeds = the project's print / travel speeds (`source.placeholder_speeds`
  is true when they are the unmeasured defaults).
The `unresolved` / non-executable form remains part of the contract for a
producer that cannot (yet) produce machine instructions.

## Consumer: Pi interface (`app/jobs/current_job.py`, `runner.py`)

1. Validates the document (rejects `.muddesign` project files explicitly),
   including the lift G-code rules above.
2. Non-executable job: shows the building / lift structure and the
   unresolved list; refuses to start.
3. Executable job: needs the print origin established on the controller
   (Set Origin); Start first moves to the origin (`G54 G20 G90 G1 X0 Y0`),
   then streams lifts in order. After every lift except the last it sends
   `G4 P0` (acked only when all buffered motion has finished), waits for a
   fresh `Idle`, enters `awaiting_reposition` — the operator may jog,
   re-establish the origin and prime — and sends nothing of the next lift
   until a client sends `{"type": "confirm_reposition", "lift":
   <awaiting_lift>}` while GRBL is Idle and the origin is established
   (stale / wrong lift refused; `resume` never confirms). A GRBL disconnect
   or reset while awaiting ends the job in `error`. The Pi never recalculates
   lift boundaries on its own; with `reentry_marks` the OPERATOR may move the
   end of the current lift to another clean cut (or, while awaiting a
   reposition, continue the lift into more layers) — same lines, same order.
4. Progress / time estimates, the 2D path view and Outline (trace
   `xy_bounds`) are derived from the job's own G-code and metadata.
5. Machine preflight against the controller's own profile; a native job
   that exceeds a configured limit cannot start (see "Machine assumptions").

## Fixtures

- `fixtures/executable-3-lifts.mudjob` — hand-written executable job (Pi
  execution tests). Not a real print.
- `fixtures/designer-export-3-lifts.mudjob` — produced by the Designer's
  exporter from a small two-island test building (islands interleave inside
  each lift → clean cuts only at the exported boundaries).
- `fixtures/designer-export-single-wall.mudjob` — a single connected wall,
  12 layers in 4 lifts of 3 (every layer boundary is a clean cut).
- `fixtures/designer-export-prepared-for.mudjob` — the same wall exported
  with a machine profile (`prepared_for` "Designer assumption" 120 × 120 in,
  max building Z 100 in; its `preflight` says it fits): the controller's
  own, possibly different, profile must decide.
  Both are Design + Toolpath golden tests and Pi import / execution tests. Regenerate with
  `MUDJOB_UPDATE_FIXTURE=1 design_proto/.venv/bin/python -m pytest layer_assembly/tests/test_machine_job.py`
  (from `design-toolpath/`).
