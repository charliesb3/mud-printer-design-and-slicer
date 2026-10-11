"""
HTTP endpoints of the Layer Assembly workspace (a Flask blueprint mounted
by the Designer app). Stateless like the Designer: the page holds the
project (Layer Designs + assembly) and posts it.

  POST /api/assembly/resolve   {assembly, design_ids?, footprints?, designs?}
                               → sections, instances (with their transforms),
                               totals; with `designs` also the footprints and
                               — for layers with transform groups — the
                               SEMANTIC variant each layer prints
                               (`geometry_key`, `variants`: design id +
                               source transforms + regenerated connectors),
                               and the vertical SUPPORT report (support.py:
                               HEADER NEEDED / INSUFFICIENT LAYER SUPPORT,
                               placed Headers); in 'multiple' centre mode
                               also the transform GROUPS (sources + labels +
                               their group, member centres, suggestions from
                               components) and one placement per group per
                               instance (`parts`)
                               With ASSEMBLY PROFILES (profiles.py): each layer's
                               cut spec is attached after resolve (placements
                               unchanged), `profiles` reports every profile
                               (valid / errors / per-layer widths or retained
                               lengths / hints), `profile_targets` lists the
                               straight walls of the designs used, and layers
                               with cuts print their cut VARIANT (in
                               `variants`, with `cuts`) — support uses it too.
  POST /api/assembly/geometry  {designs, ids | variants} → resolved 2D printable geometry
                                                         per design (or per variant key;
                                                         a variant may carry `cuts`)
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict

from flask import Blueprint, jsonify, request

from .model import Assembly, resolve, AssemblyError, Footprint
from .profiles import apply_profiles, profile_headers, WallTarget, ProfileError
from .source import bbox_of, variant_request, layer_geometry
from .support import analyse_support
from .components import root_of
from .groups import build_groups

bp = Blueprint('layer_assembly', __name__)

_SOURCES: OrderedDict = OrderedDict()      # designs JSON → DesignerSource (builds cached inside)

# CONCURRENCY (2026-10-09, reliability pass): the dev server is threaded, and
# the Designer adapter's caches (DesignerSource, DesignLibrary, the route
# memo) are not thread-safe — every request that touches them holds
# _STACK_LOCK. A Print Simulation plan is a BACKGROUND job (`job` id) that
# must never hold that lock against Assembly editing: an Assembly request
# bumps _JOBS['epoch'] before it waits, a newer plan request replaces
# _JOBS['latest'], /print_plan/cancel clears it — the running plan notices
# before its next layer (print_plan.build_plan `cancelled`) and returns 409.
_STACK_LOCK = threading.RLock()
_JOBS = {'latest': None, 'epoch': 0}


def _preempt_background():
    """An Assembly request: background plans in progress give way."""
    _JOBS['epoch'] += 1


def _source(designs):
    """The Designer adapter for this set of designs, reused while the
    designs are unchanged (a layer-height / transform / header edit never
    rebuilds a design)."""
    from .designer_source import DesignerSource
    key = json.dumps(designs, sort_keys=True)
    if key in _SOURCES:
        _SOURCES.move_to_end(key)
        return _SOURCES[key]
    src = DesignerSource.from_designs(designs)
    _SOURCES[key] = src
    while len(_SOURCES) > 4:
        _SOURCES.popitem(last=False)
    return src


def resolved_to_dict(res) -> dict:
    return {
        'layer_height': res.layer_height,
        'sections': [{'index': s.index, 'design_id': s.design_id, 'label': s.label,
                      'desired_height': s.desired_height, 'layers': s.layers,
                      'actual_height': s.actual_height, 'error': s.error,
                      'first_layer': s.first_layer, 'z_bottom': s.z_bottom, 'z_top': s.z_top}
                     for s in res.sections],
        'instances': [{'index': i.index, 'design_id': i.design_id, 'section_index': i.section_index,
                       'z_bottom': i.z_bottom, 'z_top': i.z_top, 'transform': i.transform.to_dict(),
                       'geometry_key': i.geometry_key,
                       **({'parts': {k: t.to_dict() for k, t in i.parts.items()}} if i.parts else {})}
                      for i in res.instances],
        'total_layers': res.total_layers, 'total_height': res.total_height,
        'desired_height': res.desired_height, 'warnings': res.warnings,
    }


def _resolve_stack(data, lap=lambda name: None) -> dict:
    """The resolved stack exactly as /api/assembly/resolve computes it (and
    the print plan uses): the Assembly, designs, footprints, transform
    groups, resolved instances WITH their profile cut specs. Raises the
    model / profile errors."""
    src = None
    assembly = Assembly.from_dict(data['assembly'])
    fps = {k: Footprint.of_bbox(*b) for k, b in (data.get('footprints') or {}).items() if b}
    geo, grp = {}, None
    if data.get('designs'):
        src = _source(data['designs'])
        used = {s.design_id for s in assembly.sections} & set(src.design_ids())
        labels = {}
        for d in used:
            g = src.geometry(d)
            geo[d] = (g.polylines, g.bead_width)
            labels.update(g.sources or {})
            b = bbox_of(g.polylines)
            if b:
                fps[d] = Footprint.of_bbox(*b)
        if assembly.center_mode == 'multiple':
            parent_of = {d: src.info(d).parent for d in src.design_ids()}
            cgeo = dict(geo)
            for r in {root_of(d, parent_of) for d in used}:
                if r not in cgeo:
                    g = src.geometry(r)
                    cgeo[r] = (g.polylines, g.bead_width)
                    labels.update(g.sources or {})
            grp = build_groups(cgeo, parent_of, sorted(used), assembly.centers, labels)
    lap('designs_and_groups')
    res = resolve(assembly, data.get('design_ids'), fps, grp)
    lap('resolve_layers')
    presults, targets_out, targets, bead, sources_out, openings_out = [], {}, {}, 3.0, {}, {}
    if geo:
        # ASSEMBLY PROFILES: which source geometry survives at each Z → a cut
        # spec per layer (placements above are unchanged by profiles)
        used_ids = list(dict.fromkeys(i.design_id for i in res.instances))
        for d in used_ids:
            ws = src.walls(d) if hasattr(src, 'walls') else []
            targets_out[d] = ws
            targets[d] = {t.key: t for t in map(WallTarget.from_dict, ws)}
            sources_out[d] = src.sources(d) if hasattr(src, 'sources') else []
            openings_out[d] = src.openings(d) if hasattr(src, 'openings') else []
        bead = next((g[1] for g in geo.values() if g[1]), None) or 3.0
        if assembly.profiles:
            res, presults = apply_profiles(res, assembly.profiles, targets, bead,
                                           assembly.max_overhang, sources_out, openings_out)
            # the HEADERS of Header-upper openings: generated objects, used
            # exactly like placed ones (support, preview, print plan)
            assembly.objects = list(assembly.objects) + profile_headers(presults)
        lap('profiles')
    return {'assembly': assembly, 'res': res, 'geo': geo, 'grp': grp, 'src': src, 'presults': presults,
            'targets_out': targets_out, 'targets': targets, 'bead': bead, 'sources_out': sources_out,
            'openings_out': openings_out}


@bp.route('/api/assembly/resolve', methods=['POST'])
def api_resolve():
    import time
    data = request.get_json(force=True)
    T = {}                                     # stage timings (s): `debug_timing` / ASSEMBLY_TIMING=1
    t_all = t0 = time.perf_counter()

    def lap(name):
        nonlocal t0
        t = time.perf_counter()
        T[name] = round(t - t0, 4)
        t0 = t
    _preempt_background()
    with _STACK_LOCK:
        return _resolve_locked(data, T, lap, t_all)


def _resolve_locked(data, T, lap, t_all):
    import time
    try:
        st = _resolve_stack(data, lap)
        assembly, res, geo, grp, src = st['assembly'], st['res'], st['geo'], st['grp'], st['src']
        presults, targets_out, targets = st['presults'], st['targets_out'], st['targets']
        bead, sources_out = st['bead'], st['sources_out']
        variants = {}
        built_before = len(getattr(src, '_variants', {})) if geo else 0
        if geo:
            # grouped layers print their SEMANTIC variant (the Designer moves the
            # architecture and re-resolves it), profiled layers their CUT variant
            # — support / headers use it too
            sem = [i for i in res.instances if i.is_variant]
            src.prefetch([variant_request(i) for i in sem])
            for i in sem:
                if i.geometry_key not in geo:
                    g = layer_geometry(src, i)
                    geo[i.geometry_key] = (g.polylines, g.bead_width)
                    variants[i.geometry_key] = {
                        'design_id': i.design_id,
                        'transforms': {s_: list(t) for s_, t in i.transforms().items()},
                        **({'cuts': i.cut_spec(), 'profile': g.diagnostics.get('profile') or []}
                           if i.cuts else {}),
                        'connectors': (g.diagnostics.get('semantic') or {}).get('connectors', []),
                        # DERIVED junctions (forms that merely intersect): recomputed
                        # per layer; present False = the forms have separated here
                        'junctions': (g.diagnostics.get('semantic') or {}).get('junctions', [])}
            T['variants'] = len(variants)
            T['variants_new'] = max(0, len(getattr(src, '_variants', {})) - built_before)
        lap('variant_geometry')
    except (AssemblyError, ProfileError, KeyError, ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400
    out = resolved_to_dict(res)
    if variants:
        out['variants'] = variants
    if geo:
        out['profile_targets'] = targets_out
        out['profile_sources'] = sources_out
        out['profiles'] = [r.to_dict() for r in presults]
        rep = analyse_support(res, geo, assembly, grp, cache=getattr(src, 'support_cache', None))
        _tag_profile_findings(rep, res, assembly, presults, targets, bead)
        _tag_designer_openings(rep, res, assembly, presults, targets, bead, st['openings_out'])
        out['support'] = rep.to_dict()
        lap('support_analysis')
    if grp is not None:
        out['groups'] = grp.to_dict()
    resp = jsonify(out)
    lap('serialize')
    T['total'] = round(time.perf_counter() - t_all, 4)
    if data.get('debug_timing') or os.environ.get('ASSEMBLY_TIMING') == '1':
        if os.environ.get('ASSEMBLY_TIMING') == '1':
            print('assembly/resolve timing', T, flush=True)
        out['timing'] = T
        resp = jsonify(out)
    return resp


@bp.route('/api/assembly/geometry', methods=['POST'])
def api_geometry():
    data = request.get_json(force=True)
    _preempt_background()
    with _STACK_LOCK:
        return _geometry_locked(data)


def _geometry_locked(data):
    try:
        src = _source(data['designs'])
        ids = data.get('ids') or ([] if data.get('variants') else src.design_ids())
        out = {}
        req = data.get('variants') or {}
        tf = lambda v: {s_: tuple(t) for s_, t in (v.get('transforms') or {}).items()}
        src.prefetch([(v['design_id'], tf(v), v.get('cuts') or None) for v in req.values()])
        for key, v in req.items():
            g = src.geometry(v['design_id'], tf(v) or None, v.get('cuts') or None)
            out[key] = {'name': src.info(v['design_id']).name, 'parent': src.info(v['design_id']).parent,
                        'polylines': g.polylines, 'bead_width': g.bead_width, 'sources': g.sources,
                        'variant_of': v['design_id']}
        for i in ids:
            g = src.geometry(i)
            inf = src.info(i)
            out[i] = {'name': inf.name, 'parent': inf.parent, 'polylines': g.polylines,
                      'bead_width': g.bead_width, 'sources': g.sources}
    except Exception as e:      # a design the Designer cannot build: say so
        return jsonify({'error': str(e)}), 400
    return jsonify({'designs': out})


def _tag_profile_findings(report, res, assembly, presults, targets, bead):
    """Mark support findings AT a self-supporting Assembly opening (`profile`
    = its id): on a layer it cuts or on the layer closing it, within the
    opening's half width + the wall thickness + 2 beads of its centre. The
    checker is not changed and nothing is hidden: a valid profile is
    self-supporting by construction, so such a finding is a real geometric
    problem of that layer — reported with the profile; the UI never proposes
    a header for it."""
    import math
    valid = {r.id for r in presults if r.valid and r.kind == 'opening'}
    if not valid:
        return
    profs = {p.id: p for p in assembly.profiles if p.id in valid}
    width = {r.id: (r.conversion or {}).get('width') for r in presults}
    for f in report.findings:
        here = res.instances[f.layer]
        below = res.instances[f.layer - 1] if f.layer > 0 else None
        # every opening cut on this layer or the one below (each clone of an
        # array at its own position)
        ops = sorted({(c[5], c[3]) for L in (here, below) if L is not None for c in L.cuts
                      if c[0] == 'opening' and c[5] in valid})
        for pid, at in ops:
            p = profs[pid]
            t = (targets.get(here.design_id) or {}).get(p.wall.key)
            if t is None or t.length <= 0:
                continue
            u = ((t.b[0] - t.a[0]) / t.length, (t.b[1] - t.a[1]) / t.length)
            c = (t.a[0] + u[0] * at, t.a[1] + u[1] * at)
            if math.dist(c, f.local_centre) <= (width.get(pid) or p.width) / 2 + t.thickness + 2 * bead + f.span / 2:
                f.profile = pid
                break


def _tag_designer_openings(report, res, assembly, presults, targets, bead, openings):
    """Mark HEADER NEEDED findings caused by an ordinary DESIGNER opening
    (`opening` = which one: id, wall, position, clear width, a Z inside it)
    — the layer below prints it, the finding's layer does not, and the span
    lies over it. The UI then offers "Convert to self-supporting opening"
    beside Add Header. A conversion that exists (valid or not) is named
    (`converted`). Nothing is hidden or reclassified."""
    import math
    conv = {p.source_opening['id']: p.id for p in assembly.profiles
            if p.kind == 'opening' and getattr(p, 'source_opening', None)}
    for f in report.findings:
        if f.kind != 'bridge' or f.profile or f.layer <= 0:
            continue
        here, below = res.instances[f.layer], res.instances[f.layer - 1]
        above_ids = {o['id'] for o in openings.get(here.design_id) or ()}
        for o in openings.get(below.design_id) or ():
            if o['id'] in above_ids:
                continue
            q = o.get('point')
            t = (targets.get(below.design_id) or {}).get(
                o['source'] if o.get('edge') is None else f"{o['source']}#{o['edge']}") if 'at' in o else None
            th = t.thickness if t is not None else 0.0
            if q is None:                            # (not on a straight edge: still named, not convertible)
                f.opening = {'id': o['id'], 'source': o['source'], 'convertible': False,
                             'why': 'the opening is not on a straight wall edge'}
                break
            if math.dist(q, f.local_centre) <= o['width'] / 2 + th + 2 * bead + f.span / 2:
                f.opening = {'id': o['id'], 'source': o['source'], 'edge': o.get('edge'), 'at': o['at'],
                             'width': o['width'], 'label': o.get('label') or o['id'],
                             'z_ref': round(0.5 * (below.z_bottom + below.z_top), 6),
                             'convertible': t is not None, 'converted': conv.get(o['id'], '')}
                break


_PLANS: OrderedDict = OrderedDict()        # stack request (without speeds) → planned stack


def _planned(data, cancelled=None):
    """(plan, resolved stack, extras) for this request, cached by everything
    that changes the ORDERED GEOMETRY — not the speeds (a speed edit only
    re-times the same plan). `rebuild`: drop this request's cached plan and
    routes first (the Simulation's recovery control). Caller holds
    _STACK_LOCK."""
    from .print_plan import build_plan
    asm = dict(data['assembly'])
    asm.pop('print_speeds', None)
    key = json.dumps([asm, data.get('designs'), data.get('start')], sort_keys=True)
    if data.get('rebuild'):
        _PLANS.pop(key, None)
        src = _SOURCES.get(json.dumps(data.get('designs'), sort_keys=True))
        if src is not None and hasattr(src, '_prepared'):
            src._prepared.clear()             # routes (and their memo) again
    if key in _PLANS:
        _PLANS.move_to_end(key)
        return _PLANS[key]
    from .lifts import LiftSettings
    st = _resolve_stack(data)
    plan = build_plan(st['res'], st['src'], data.get('start'), cancelled=cancelled,
                      lift_settings=LiftSettings.from_dict(st['assembly'].machine))
    src, res, assembly = st['src'], st['res'], st['assembly']
    labels, systems = {}, {}
    for d in dict.fromkeys(i.design_id for i in res.instances):
        g = src.geometry(d)
        labels.update(g.sources or {})
        doc = src.lib.document(d) if hasattr(src, 'lib') else {}
        for w in doc.get('wall_systems') or []:
            systems[w.get('id')] = {'name': w.get('name') or w.get('id'), 'type': w.get('type')}
    headers = []
    by_index = {i.index: i for i in res.instances}
    for o in assembly.objects:
        if getattr(o, 'kind', '') != 'header' or not (0 <= o.section < len(res.sections)):
            continue
        sec = res.sections[o.section]
        inst = by_index.get(sec.first_layer + o.layer)
        if inst is None:
            continue
        headers.append({'id': o.id, 'section': o.section, 'layer': inst.index,
                        'corners': [list(c) for c in o.support_rect(inst.transform)],
                        'z_top': inst.z_bottom, 'z_bottom': inst.z_bottom - o.thickness,
                        **({'profile': o.profile} if getattr(o, 'profile', '') else {})})
    extras = {'sections': [{'index': x.index, 'design_id': x.design_id, 'label': x.label,
                            'first_layer': x.first_layer, 'layers': x.layers,
                            'z_bottom': x.z_bottom, 'z_top': x.z_top} for x in res.sections],
              'labels': labels, 'systems': systems, 'headers': headers,
              'designs': {d: src.info(d).name for d in src.design_ids()}}
    _PLANS[key] = (plan, extras)
    while len(_PLANS) > 4:
        _PLANS.popitem(last=False)
    return _PLANS[key]


@bp.route('/api/assembly/print_plan', methods=['POST'])
def api_print_plan():
    """The WHOLE-PRINT PLAN (print_plan.py): every resolved layer — exactly
    the geometry /api/assembly/resolve displays and checks (variants, cuts) —
    routed by the Designer's layer router, ordered bottom-up with automatic
    starts / component order / layer transitions (manual Route Origins /
    starts win), plus time ESTIMATES.
    Body: as /api/assembly/resolve (`assembly`, `designs`, `footprints`?)
      + `speeds` {print, travel} (in/s; default: the Assembly's print_speeds)
      + `detail`: 'summary' (default) | 'full' | [layer indices in full]
               | 'stream' (compact arrays + item times: the Print Simulation)
               | 'times' (item times + estimate only, for new speeds)
      + `start`: [x, y] nozzle position before the first layer (optional)
      + `job`: a BACKGROUND job id (the Print Simulation): abandoned — 409
        {cancelled: true} — when a newer job starts, the page cancels it
        (/print_plan/cancel) or an Assembly request needs the Designer adapter
      + `rebuild`: true = plan again from scratch (cached plan + routes dropped).
    The planned stack is cached (without the speeds): a speed change re-times
    it without routing again."""
    from .print_plan import estimate, PrintSpeeds, PlanError, PlanCancelled, stream, item_times
    data = request.get_json(force=True)
    job, cancelled = data.get('job'), None
    if job is not None:
        _JOBS['latest'] = job
        epoch = _JOBS['epoch']
        cancelled = lambda: _JOBS['latest'] != job or _JOBS['epoch'] != epoch    # noqa: E731
    try:
        if not data.get('designs'):
            raise AssemblyError('a print plan needs the Layer Designs')
        with _STACK_LOCK:
            if cancelled is not None and cancelled():
                raise PlanCancelled('print plan cancelled')
            plan, extras = _planned(data, cancelled)
        speeds = PrintSpeeds.from_dict(data.get('speeds') or (data['assembly'].get('print_speeds') or {}))
        est = estimate(plan, speeds)
        detail = data.get('detail') or 'summary'
        if detail == 'stream':
            out = {'stream': stream(plan, speeds), 'plan': plan.to_dict('summary'), 'estimate': est, **extras}
        elif detail == 'times':
            t0, dt = item_times(plan, speeds)
            out = {'t0': [round(v, 6) for v in t0], 'dt': [round(v, 6) for v in dt], 'estimate': est}
        else:
            out = {'plan': plan.to_dict(detail), 'estimate': est}
    except PlanCancelled as e:
        return jsonify({'error': str(e), 'cancelled': True}), 409
    except (AssemblyError, ProfileError, PlanError, KeyError, ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(out)


@bp.route('/api/assembly/print_plan/cancel', methods=['POST'])
def api_print_plan_cancel():
    """The page no longer needs background plan `job` (it left the Print
    Simulation, or the project changed): stop it before its next layer."""
    job = (request.get_json(force=True, silent=True) or {}).get('job')
    if job is not None and _JOBS['latest'] == job:
        _JOBS['latest'] = None
    return jsonify({'ok': True})
