"""MACHINE PROFILE (2026-10-15) — the Design + Toolpath side.

The machine a PROJECT is designed and planned for: authoring state, saved in
the `.muddesign` project file (project_file.py, schema 5). It is a REFERENCE
and a CHECK, never a constraint on geometry: nothing is clipped, snapped,
resized or replanned to fit it; designs may deliberately exceed it and are
warned.

Machine profiles are independent configuration on each side. The printer
controller (pi-interface) keeps its OWN profile of the physical machine; the
`.mudjob` only carries what THIS profile assumed (`prepared_for`), the job's
actual `requirements` and this side's informational `preflight` — it never
synchronizes the applications.

Values: inches (the only unit of the current machine). X / Y extents are
the printable area; the envelope is taken as [0, X] × [0, Y] from the PRINT
ORIGIN (work X0 Y0) — a provisional convention (where the print origin sits
on the physical machine is not decided), so only the job's SIZE against the
extents fails a check; its placement relative to the origin warns.
`max_build_z` (total building height) and `z_travel_per_frame` (one frame
position = one lift) are distinct constraints. A value of None is "not set"
(not checked). `provisional` lists values that are placeholders, not
measured facts.
"""
from __future__ import annotations

import copy

PROFILE_VERSION = 1
UNITS = ('in',)
DIMENSIONS = ('x_extent', 'y_extent', 'max_build_z', 'z_travel_per_frame')
ENVELOPE_ORIGIN = 'print_origin'          # [0, X] × [0, Y] in work coordinates (provisional convention)
TOL = 1e-4                                # in


class MachineProfileError(ValueError):
    pass


def default_profile(z_travel_per_frame: float = 18.0) -> dict:
    """A clearly PROVISIONAL starting profile: X / Y from "approximately 10 ft ×
    10 ft or larger" (PROJECT_OVERVIEW), maximum building Z not documented
    (unset), Z travel per frame position ~18 in (documented)."""
    return {
        'version': PROFILE_VERSION,
        'name': 'Mud printer (provisional)',
        'units': 'in',
        'x_extent': 120.0,
        'y_extent': 120.0,
        'max_build_z': None,
        'z_travel_per_frame': float(z_travel_per_frame),
        'provisional': ['x_extent', 'y_extent'],
        'envelope_origin': ENVELOPE_ORIGIN,
    }


def validate(p) -> dict:
    """A normalized copy of profile `p` (raises MachineProfileError). Unknown
    keys are kept as they are (forward compatibility)."""
    if not isinstance(p, dict):
        raise MachineProfileError('The machine profile must be an object.')
    out = copy.deepcopy(p)
    out.setdefault('version', PROFILE_VERSION)
    if not isinstance(out['version'], int) or out['version'] > PROFILE_VERSION:
        raise MachineProfileError(f'The machine profile version {out["version"]!r} is newer than this software.')
    name = out.get('name', '')
    if not isinstance(name, str):
        raise MachineProfileError('The machine profile name must be text.')
    out['name'] = name.strip()
    units = out.get('units', 'in')
    if units not in UNITS:
        raise MachineProfileError(f'Machine profile units must be inches ("in"), not {units!r}.')
    out['units'] = units
    for k in DIMENSIONS:
        v = out.get(k)
        if v is None:
            out[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
            raise MachineProfileError(f'Machine profile {k.replace("_", " ")} must be a positive number or empty.')
        out[k] = float(v)
    prov = out.get('provisional', [])
    if not isinstance(prov, list) or not all(isinstance(x, str) for x in prov):
        raise MachineProfileError('The machine profile "provisional" list is damaged.')
    out['provisional'] = [x for x in prov if x in DIMENSIONS]
    out['envelope_origin'] = out.get('envelope_origin') or ENVELOPE_ORIGIN
    return out


# ---------------------------------------------------------------------------
# requirements of a planned print and the preflight against a profile
# ---------------------------------------------------------------------------

def _bounds(pts) -> dict | None:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if not xs:
        return None
    r = lambda v: round(v, 4)                                      # noqa: E731
    return {'min_x': r(min(xs)), 'min_y': r(min(ys)), 'max_x': r(max(xs)), 'max_y': r(max(ys))}


def requirements(plan) -> dict:
    """What the AUTHORITATIVE PrintPlan needs from a machine (inches): the XY
    bounds of all motion and of the extrusion alone, the building height (the
    highest layer top) and the tallest lift (= Z travel needed per frame)."""
    from layer_assembly.print_plan import items             # (the plan's own move stream)
    motion, printed = [], []
    for it in items(plan):
        if it[0] == 'frame':
            continue
        motion.extend(it[3])
        if it[0] in ('print', 'retrace'):
            printed.extend(it[3])
    return {
        'units': plan.units,
        'xy_bounds': _bounds(motion),
        'print_bounds': _bounds(printed),
        'building_height': round(max((L.z_top for L in plan.layers), default=0.0), 6),
        'max_lift_height': round(max((L.z_top - L.z_bottom for L in plan.lifts), default=0.0), 6),
        'lifts': len(plan.lifts),
        'layers': len(plan.layers),
    }


def _check(cid, required, limit, label, provisional, fail_msg, ok_msg):
    if limit is None:
        return {'id': cid, 'status': 'unchecked', 'required': required, 'limit': None,
                'message': f'{label}: not set in the machine profile — not checked.'}
    status = 'fail' if required > limit + TOL else 'ok'
    msg = (fail_msg if status == 'fail' else ok_msg).format(r=required, l=limit)
    if provisional:
        msg += ' (provisional machine value)'
    return {'id': cid, 'status': status, 'required': required, 'limit': limit, 'message': msg}


def preflight(req: dict, profile: dict) -> dict:
    """Compare a print's requirements with a machine profile. Informational:
    nothing is changed to make it fit."""
    p = validate(profile)
    prov = set(p['provisional'])
    checks = []
    b = req.get('xy_bounds')
    for axis, ext in (('x', 'x_extent'), ('y', 'y_extent')):
        lim = p[ext]
        if not b:
            continue
        lo, hi = b[f'min_{axis}'], b[f'max_{axis}']
        size = round(hi - lo, 4)
        c = _check(axis, size, lim, f'{axis.upper()} extent', ext in prov,
                   f'Exceeds {axis.upper()}: the job spans {{r:g}} in, the printer {{l:g}} in.',
                   f'{axis.upper()}: the job spans {{r:g}} of {{l:g}} in.')
        c['bounds'] = [lo, hi]
        if c['status'] == 'ok' and (lo < -TOL or hi > lim + TOL):
            c['status'] = 'warn'
            c['message'] = (f'{axis.upper()}: the job fits ({size:g} of {lim:g} in) but lies at {lo:g} – {hi:g} in from '
                            f'the print origin, outside the envelope placed there — set the origin so that it fits.')
        checks.append(c)
    checks.append(_check('z', req.get('building_height', 0.0), p['max_build_z'], 'Maximum building Z',
                         'max_build_z' in prov,
                         'Exceeds the maximum building Z: the building is {r:g} in tall, the printer allows {l:g} in.',
                         'Building height {r:g} of {l:g} in.'))
    checks.append(_check('lift', req.get('max_lift_height', 0.0), p['z_travel_per_frame'], 'Z travel per frame',
                         'z_travel_per_frame' in prov,
                         'A lift needs {r:g} in of Z travel; one frame position gives {l:g} in.',
                         'Tallest lift {r:g} of {l:g} in Z travel per frame.'))
    return {'fits': not any(c['status'] == 'fail' for c in checks),
            'warnings': sum(1 for c in checks if c['status'] == 'warn'),
            'unchecked': [c['id'] for c in checks if c['status'] == 'unchecked'],
            'checks': checks}


def prepared_for(profile: dict) -> dict:
    """The profile snapshot a `.mudjob` records (the assumptions, not a sync)."""
    p = validate(profile)
    return {k: p.get(k) for k in ('name', 'units', 'x_extent', 'y_extent', 'max_build_z', 'z_travel_per_frame',
                                  'provisional', 'envelope_origin')}
