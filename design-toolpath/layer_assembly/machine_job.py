"""MACHINE JOB EXPORT (2026-10-14, Pi P3; executable since the current-machine
pass): the authoritative PrintPlan serialized as a `.mudjob` — the ONLY
interface between Design + Toolpath and the printer controller (Pi
interface). Contract: `contracts/mudjob/README.md` at the repository root
(version 1). Nothing here imports, calls or knows the Pi interface; the file
is the whole boundary.

`.muddesign` (project_file.py) = AUTHORING state, Save / Open in the Designer.
`.mudjob` (this module)        = MACHINE JOB, one whole-building print.

What is exported is read straight off the PrintPlan — one job lift per
`plan.lifts` entry, in order — and its moves are `print_plan.items()` in
print order. Routing, layer order, starts, runs, lifts and toolpaths are
never reconstructed.

CURRENT MACHINE (decided 2026-10-14) — executable, XY-only G-code:
- inches: every lift starts with `G20` + `G90` (never depends on the
  controller's previous modal state — the operator may jog / re-zero between
  lifts);
- coordinates = the plan's world XY = the WORK coordinate system whose
  origin the operator establishes at the print origin (no machine offset is
  invented);
- extrusion runs (print, retrace) → `G1` at the print speed; non-printing
  travel and layer / island transitions → `G1` at the travel speed (not G0:
  GRBL's rapid rate is not validated for this machine). Nothing is paused or
  altered for the pump, which is manual and independent;
- NO Z words: Z is manual. Layer / pass / Z information is kept as metadata
  (`layer_marks`) for the operator and for future automatic Z;
- no prime moves, no pump / extrusion commands, no program pause / end words.

RE-ENTRY SEGMENTS (2026-10-14, adaptive lift boundaries): every PASS (one
layer piece, `layer_marks` entry) begins a self-contained segment — `G20 G90`
then an absolute travel `G1 X… Y… F<travel>` to the pass start (emitted even
when the nozzle is already there) — so the controller may pause before any
pass (a frame reposition at an adjusted lift boundary) and resume exactly,
without generating motion of its own (`reentry_marks: true`). The first pass
of a lift therefore opens the lift. `travel_lines` lists every non-printing
move (display / progress only); `lift_plan` carries the planning targets.
"""
from __future__ import annotations

import datetime as _dt
import math
import re

MUDJOB_FORMAT = 'mudprinter-job'
MUDJOB_VERSION = 1
MUDJOB_EXTENSION = '.mudjob'
GENERATOR = 'Mud Printer Design + Toolpath'
PASS_PREAMBLE = 'G20 G90'                # inches, absolute — explicit at every pass (re-entry)
COORD_DIGITS = 4                        # 0.0001 in
_TOL = 1e-6


class ExportError(ValueError):
    pass


def _r(v, d=6):
    return round(float(v), d)


def _num(v, d=COORD_DIGITS) -> str:
    s = f'{round(float(v), d):.{d}f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


class _Writer:
    """G-code for one lift: absolute G1 moves, F only when it changes."""

    def __init__(self, print_feed, travel_feed):
        self.lines = []
        self.travel = []                 # indices of non-printing moves
        self.feeds = {'print': print_feed, 'retrace': print_feed, 'travel': travel_feed, 'transition': travel_feed}
        self.pos = None                 # the rounded position after the last move (None: unknown)
        self.feed = None

    def entry(self):
        """Start a re-entry segment: explicit modal state, position and feed unknown."""
        self.lines.append(PASS_PREAMBLE)
        self.pos = None
        self.feed = None

    def move(self, kind, p):
        x, y = _num(p[0]), _num(p[1])
        if self.pos == (x, y):
            return                       # nothing to move after rounding
        line = f'G1 X{x} Y{y}'
        f = self.feeds[kind]
        if f != self.feed:
            line += f' F{_num(f, 1)}'
            self.feed = f
        if kind in ('travel', 'transition'):
            self.travel.append(len(self.lines))
        self.lines.append(line)
        self.pos = (x, y)


def _lift_gcode(plan, items, print_feed, travel_feed):
    """(gcode lines, layer_marks, travel line indices) of one lift's items (print order)."""
    w = _Writer(print_feed, travel_feed)
    marks, cur_pass, last, entering = [], None, None, False
    for it in items:
        kind, layer, pts, ps = it[0], it[1], it[3], it[11]
        if kind == 'frame':
            continue
        if ps != cur_pass:
            P = plan.passes[ps]
            Lf = plan.lifts[P.lift]
            marks.append({'line': len(w.lines), 'pass': ps, 'layer': P.layer, 'z': _r(P.z),
                          'z_in_lift': _r(P.z - Lf.z_bottom)})
            cur_pass = ps
            w.entry()                    # a re-entry segment per pass
            entering = True
        start = pts[0]
        if kind in ('travel', 'transition'):
            w.move(kind, pts[-1])        # a travel goes to its end, from wherever the nozzle is
        else:
            if entering:
                w.move('travel', start)  # pass start: explicit travel to its first point
            elif math.hypot(start[0] - last[0], start[1] - last[1]) > _TOL:
                raise ExportError(f'print plan discontinuity before layer {layer + 1} (pass {ps})')
            for p in pts[1:]:
                w.move(kind, p)
        entering = False
        last = pts[-1]
    return w.lines, marks, w.travel


def export_job(plan, speeds, name: str | None = None, generated_at: str | None = None,
               placeholder_speeds: bool = False, machine_profile: dict | None = None) -> dict:
    """The executable `.mudjob` document of `plan` (a print_plan.PrintPlan)
    with `speeds` (print_plan.PrintSpeeds, in/s). Pure: the same plan,
    speeds and `generated_at` give the same document.

    MACHINE (2026-10-15): `requirements` (what the plan needs: XY bounds of
    all motion / of extrusion, building height, tallest lift) is always
    recorded; with the project's `machine_profile`, also `prepared_for` (the
    profile it was prepared against) and this side's informational
    `preflight`. Nothing is changed to make the job fit; the controller
    re-checks against its own profile."""
    from .print_plan import items
    if plan.units != 'in':
        raise ExportError(f'the print plan is in {plan.units!r}; the machine job is written in inches')
    if not plan.lifts:
        raise ExportError('the print plan has no lifts — plan the print first')
    for pos, L in enumerate(plan.lifts):
        # the plan's own invariants the contract relies on — reported, never repaired
        if L.index != pos:
            raise ExportError(f'print plan lift {pos} has index {L.index}')
        if (L.reposition_in is None) != (pos == 0):
            raise ExportError(f'print plan lift {pos}: a frame reposition must precede every lift but the first')
    print_feed, travel_feed = speeds.print_speed * 60.0, speeds.travel_speed * 60.0     # in/min
    by_lift = {}
    for it in items(plan):
        by_lift.setdefault(it[9], []).append(it)
    xs, ys = [], []
    for its in by_lift.values():
        for it in its:
            if it[0] != 'frame':
                xs.extend(p[0] for p in it[3])
                ys.extend(p[1] for p in it[3])
    if generated_at is None:
        generated_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    doc = {'format': MUDJOB_FORMAT, 'version': MUDJOB_VERSION, 'executable': True}
    if name:
        doc['name'] = name
    doc['units'] = 'in'
    doc['source'] = {'generator': GENERATOR, 'generated_at': generated_at,
                     'print_plan_version': plan.version, 'lift_settings': dict(plan.settings),
                     'layers': len(plan.layers), 'passes': len(plan.passes),
                     'speeds_in_s': {'print': speeds.print_speed, 'travel': speeds.travel_speed},
                     'placeholder_speeds': bool(placeholder_speeds)}
    doc['machine'] = {'axes': 'XY', 'z': 'manual', 'extrusion': 'manual',
                      'coordinates': 'work coordinates; origin = the print origin set by the operator',
                      'feeds_in_min': {'print': _r(print_feed, 1), 'travel': _r(travel_feed, 1)}}
    if xs:
        doc['xy_bounds'] = {'min_x': _r(min(xs), 4), 'min_y': _r(min(ys), 4),
                            'max_x': _r(max(xs), 4), 'max_y': _r(max(ys), 4)}
    hs = {round(L.z_top - L.z_bottom, 9) for L in plan.layers}
    from machine_profile import prepared_for, preflight, requirements
    req = requirements(plan)
    doc['requirements'] = req
    if machine_profile is not None:
        doc['prepared_for'] = prepared_for(machine_profile)
        doc['preflight'] = dict(preflight(req, machine_profile), informational=True)
    doc['lift_plan'] = {'target_lift_height': _r(plan.settings.get('lift_height')) if plan.settings.get('lift_height') else None,
                        'max_z_travel': _r(plan.settings.get('max_z_travel')) if plan.settings.get('max_z_travel') else None,
                        'layer_height': _r(hs.pop()) if len(hs) == 1 else None}
    doc['reentry_marks'] = True
    lifts = []
    for L in plan.lifts:
        gcode, marks, travel = _lift_gcode(plan, by_lift.get(L.index, []), print_feed, travel_feed)
        lifts.append(_lift(plan, L, gcode, marks, travel))
    doc['lifts'] = lifts
    return doc


def _lift(plan, L, gcode, marks, travel_lines) -> dict:
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
    out['layer_marks'] = marks
    out['travel_lines'] = travel_lines
    out['gcode'] = gcode
    return out


def job_filename(name: str | None) -> str:
    base = re.sub(r'[^A-Za-z0-9._ -]+', '', (name or '').strip()).strip(' .') or 'print-job'
    return base + MUDJOB_EXTENSION
