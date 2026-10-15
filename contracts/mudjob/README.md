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

## Document

UTF-8 JSON, extension `.mudjob`.

```json
{
  "format": "mudprinter-job",
  "version": 1,
  "executable": false,
  "unresolved": [{"id": "machine_units", "summary": "…"}],
  "name": "optional display name",
  "units": "in",
  "source": {"generator": "…", "generated_at": "…"},
  "lifts": [
    {"index": 0, "description": "Layers 1–12", "layers": {"first": 0, "last": 11, "count": 12},
     "z_bottom": 0, "z_top": 18},
    {"index": 1, "reposition_before": {"rise": 18, "frame_z_from": 0, "frame_z_to": 18},
     "z_bottom": 18, "z_top": 36}
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
| `source` | no | provenance (generator, time, planning settings); ignored by the Pi |

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

Unknown keys are ignored (optional, display-only additions are allowed
within version 1). Any change to a required field or to meaning bumps the
version; readers reject versions they do not know.

### Rules for lift G-code (executable jobs)

Ordinary GRBL G-code. The controller owns pauses and the end of the job, so
a lift may NOT contain `M0`, `M1`, `M2`, `M30` (incl. `M00`…), `%`, or `$`
system commands.

## Producer: Design + Toolpath (`layer_assembly/machine_job.py`)

"Export Print Job" (Print Simulation workspace) serializes the CURRENT,
successfully planned PrintPlan: one lift per PrintPlan lift, in order, with
its layer range, Z range, summary numbers and the PrintPlan's `FrameMove` as
`reposition_before`. It reconstructs nothing.

Currently every export is `executable: false` with these `unresolved` ids
(undecided machine semantics — no G-code is generated until they are
decided): `z_within_lift`, `travel_with_pump`, `xy_origin`,
`machine_units`, `frame_reposition`.

## Consumer: Pi interface (`app/jobs/current_job.py`)

1. Validates the document (rejects `.muddesign` project files explicitly).
2. Non-executable job: shows the building / lift structure and the
   unresolved list; refuses to start.
3. Executable job: streams lifts in order. After every lift except the last
   it sends `G4 P0` (acked only when all buffered motion has finished), waits
   for a fresh `Idle`, enters `awaiting_reposition`, and sends nothing until a
   client sends `{"type": "confirm_reposition", "lift": <awaiting_lift>}`
   while GRBL is Idle (stale / wrong lift refused; `resume` never confirms).
   A GRBL disconnect or reset while awaiting ends the job in `error`.
   The Pi never recalculates lift boundaries.

## Fixtures

- `fixtures/executable-3-lifts.mudjob` — hand-written executable job (Pi
  execution tests). Not a real print.
- `fixtures/designer-export-3-lifts.mudjob` — produced by the Designer's
  exporter from a small test building (Design + Toolpath golden test; Pi
  import tests). Regenerate with
  `MUDJOB_UPDATE_FIXTURE=1 design_proto/.venv/bin/python -m pytest layer_assembly/tests/test_machine_job.py`
  (from `design-toolpath/`).
