"""MACHINE JOB EXPORT (2026-10-14, Pi P3): the authoritative PrintPlan
serialized as a `.mudjob` — the ONLY interface between Design + Toolpath and
the printer controller (Pi interface). Contract: `contracts/mudjob/README.md`
at the repository root (version 1). Nothing here imports, calls or knows the
Pi interface; the file is the whole boundary.

`.muddesign` (project_file.py) = AUTHORING state, Save / Open in the Designer.
`.mudjob` (this module)        = MACHINE JOB, one whole-building print.

What is exported is read straight off the PrintPlan — one job lift per
`plan.lifts` entry, in order, with its layer range, Z range, summary numbers
and its `FrameMove` as `reposition_before`. Routing, layer order, starts,
lifts and toolpaths are never reconstructed.

EXECUTABLE OUTPUT IS BLOCKED (deliberately): Z within a lift, travel with the
independently controlled pump, XY origin, machine units and the physical
frame-reposition behaviour are NOT decided, so no G-code is generated. Every
export is `executable: false` and names what remains unresolved
(`UNRESOLVED`). Add executable G-code only after those decisions are made.
"""
from __future__ import annotations

import datetime as _dt
import re

MUDJOB_FORMAT = 'mudprinter-job'
MUDJOB_VERSION = 1
MUDJOB_EXTENSION = '.mudjob'
GENERATOR = 'Mud Printer Design + Toolpath'

# Undecided machine semantics that prevent executable machine instructions
# (project decision 2026-10-14: report them, never choose silently).
UNRESOLVED = (
    ('z_within_lift',
     'Z behaviour within a lift is not decided. The plan changes nozzle height at every layer, can '
     'move back down to a lower layer of another island, and rises to clear heights for travel; Z is '
     'manual today, so how Z appears in machine instructions is open.'),
    ('travel_with_pump',
     'Non-printing travel is not decided. The pump is controlled independently, so whether travel and '
     'transitions are plain moves or need operator action is open.'),
    ('xy_origin',
     'XY work zero / origin is not decided. Plan coordinates are building-world coordinates; how they '
     'map to machine coordinates is open.'),
    ('machine_units',
     'Machine units are not decided. The plan is in inches; the units of the machine G-code are open.'),
    ('frame_reposition',
     'Physical frame-reposition behaviour is not decided (whether XY registration and the controller '
     'position survive a frame raise, and any re-zeroing).'),
)


class ExportError(ValueError):
    pass


def _r(v, d=6):
    return round(float(v), d)


def _lift(plan, L) -> dict:
    passes = [plan.passes[i] for i in L.passes]
    runs = [r for P in passes for r in P.runs]
    travel = sum(t.length for P in passes for t in P.travels) + \
        sum(P.transition_in.length for P in passes if P.transition_in is not None)
    out = {
        'index': L.index,
        'description': f'Layers {L.first_layer + 1}–{L.last_layer + 1}',
        'layers': {'first': L.first_layer, 'last': L.last_layer, 'count': L.last_layer - L.first_layer + 1},
        'z_bottom': _r(L.z_bottom), 'z_top': _r(L.z_top),
        'stages': len(L.stages), 'passes': len(passes), 'runs': len(runs),
        'print_length': _r(sum(r.print_length + r.retrace_length for r in runs), 3),
        'travel_length': _r(travel, 3),
    }
    fm = L.reposition_in
    if fm is not None:
        out['reposition_before'] = {
            'from_lift': fm.from_lift, 'rise': _r(fm.rise),
            'frame_z_from': _r(fm.frame_z_from), 'frame_z_to': _r(fm.frame_z_to),
            'nozzle_at': [_r(fm.at[0]), _r(fm.at[1])], 'nozzle_z': _r(fm.z)}
    return out


def export_job(plan, name: str | None = None, generated_at: str | None = None) -> dict:
    """The `.mudjob` document of `plan` (a print_plan.PrintPlan). Pure: the
    same plan (and `generated_at`) gives the same document."""
    if not plan.lifts:
        raise ExportError('the print plan has no lifts — plan the print first')
    for pos, L in enumerate(plan.lifts):
        # the plan's own invariants the contract relies on — reported, never repaired
        if L.index != pos:
            raise ExportError(f'print plan lift {pos} has index {L.index}')
        if (L.reposition_in is None) != (pos == 0):
            raise ExportError(f'print plan lift {pos}: a frame reposition must precede every lift but the first')
    if generated_at is None:
        generated_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    doc = {'format': MUDJOB_FORMAT, 'version': MUDJOB_VERSION, 'executable': False,
           'unresolved': [{'id': i, 'summary': s} for i, s in UNRESOLVED]}
    if name:
        doc['name'] = name
    doc['units'] = plan.units
    doc['source'] = {'generator': GENERATOR, 'generated_at': generated_at,
                     'print_plan_version': plan.version, 'lift_settings': dict(plan.settings),
                     'layers': len(plan.layers), 'passes': len(plan.passes)}
    doc['lifts'] = [_lift(plan, L) for L in plan.lifts]
    return doc


def job_filename(name: str | None) -> str:
    base = re.sub(r'[^A-Za-z0-9._ -]+', '', (name or '').strip()).strip(' .') or 'print-job'
    return base + MUDJOB_EXTENSION
