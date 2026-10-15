# Pi Building Job Format — version 1

The Pi interface runs a whole building as ONE job: an ordered list of
**lifts**. Lift boundaries are decided upstream by the PrintPlan
(Design + Toolpath); the Pi never recalculates them. Between lifts the Pi
stops and waits for the operator to confirm the physical frame reposition.

Status: implemented on the Pi (P1). The PrintPlan exporter (P3) is not built
yet; Z handling, travel behaviour, XY origin and units inside the G-code are
undecided and are NOT defined by this format.

Reference example: `tests/fixtures/sample_building.mudjob`.

## File

UTF-8 JSON, extension `.mudjob` (also accepted: `.json`, or any upload whose
content starts with `{`). Anything else is treated as an ordinary flat G-code
file, which runs as a single-lift job exactly as in Milestone 1.

```json
{
  "format": "mudprinter-job",
  "version": 1,
  "name": "optional display name",
  "units": "optional, informational only (e.g. \"in\")",
  "source": { "optional": "provenance, ignored by the Pi" },
  "lifts": [
    {
      "index": 0,
      "description": "optional, shown to the operator",
      "gcode": ["G90", "G1 X0 Y0 F120", "..."]
    },
    {
      "index": 1,
      "reposition_before": { "rise": 18, "note": "optional text" },
      "gcode": ["..."]
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `format` | yes | must be `"mudprinter-job"` |
| `version` | yes | must be `1` |
| `lifts` | yes | non-empty list, in print order |
| `lifts[].gcode` | yes | list of G-code lines; comments/blank lines are stripped; must contain at least one sendable line |
| `lifts[].index` | no | if present must equal the lift's position (0-based) — guards against mis-ordered export |
| `lifts[].description` | no | display text |
| `lifts[].reposition_before` | no | object shown to the operator before this lift (only lifts after the first). The Pi displays `rise` (with `units`) and `note`; it never interprets them |
| `name`, `units`, `source` | no | display / provenance only — the Pi converts nothing |

## Rules for lift G-code

Lift G-code is ordinary GRBL G-code. The Pi owns pauses and the end of the
job, so a lift may NOT contain: `M0`, `M1`, `M2`, `M30` (incl. `M00` etc.),
`%`, or `$` system commands. Such a job is rejected at upload.

## Runtime behaviour (Pi)

1. Lifts stream in order, line by line (send → ok → next).
2. After every lift except the last, the Pi sends `G4 P0` (GRBL acks it only
   once all buffered motion has finished), waits for a fresh `Idle` status,
   then enters job state `awaiting_reposition` (`awaiting_lift` = next lift).
3. Nothing more is sent until a client sends
   `{"type": "confirm_reposition", "lift": <awaiting_lift>}` while GRBL is
   Idle. A wrong/stale lift number, or GRBL not Idle, is refused.
   `resume` never confirms a reposition.
4. A GRBL disconnect or reset while awaiting ends the job in `error`.
   Stop ends it in `stopped`. Restarting at a given lift is not implemented.
