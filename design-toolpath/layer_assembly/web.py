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

DEFERRED PRINT PATHS (2026-10-11; design-toolpath/DEFERRED_TOOLPATH_ARCHITECTURE.md
M0 + M1): with `envelope: true` (what the Assembly page sends while
editing) /resolve and /geometry work on ARCHITECTURAL ENVELOPES — every
layer's walls / junctions / openings / cuts resolved by the Designer up to
its material rings, no Wall Systems / lattice / routing
(DesignerSource.envelope) — and the support report is the PRELIMINARY
envelope-level one (support.analyse_support level 'envelope'). Instances
then name their ENVELOPE key (`geometry_key`; the full print key in
`print_key`) and `variants` are envelope variants (`envelope: true`).
Envelope requests use their own adapter and lock (`_ENV_SOURCES`,
`_ENV_LOCK`): they neither wait for nor pre-empt a print preparation.
Without `envelope` both endpoints behave exactly as before (full printable
geometry, bead-level support) — PRINT PREPARATION is /print_plan (the
Print Simulation's "Generate Print Paths"), which also returns the
authoritative BEAD-LEVEL support report (`support`).
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
# ENVELOPE editing (deferred print paths): its own adapters + lock — an
# Assembly edit never waits for, nor cancels, a print preparation. Both
# adapters of one design set share its canonical builds (_CANON).
_ENV_SOURCES: OrderedDict = OrderedDict()
_ENV_LOCK = threading.RLock()
_CANON: OrderedDict = OrderedDict()       # designs JSON → {design id: canonical LayerGeometry}


def _preempt_background():
    """An Assembly request: background plans in progress give way."""
    _JOBS['epoch'] += 1


_ARCH_LIBS: OrderedDict = OrderedDict()   # designs JSON → the ARCHITECTURE library (envelopes; _ENV_LOCK)


def _arch_lib(designs, dkey):
    """The architecture library of a design set (the documents composed
    without construction) — shared by its editing adapters for every set of
    Wall Systems: a Wall System edit never rebuilds an envelope."""
    from .designer_source import arch_library
    lib = _ARCH_LIBS.get(dkey)
    if lib is None:
        lib = _ARCH_LIBS[dkey] = arch_library(designs)
        while len(_ARCH_LIBS) > 4:
            _ARCH_LIBS.popitem(last=False)
    _ARCH_LIBS.move_to_end(dkey)
    return lib


def _source(designs, envelope=False, wall_systems=None, junctions=None):
    """The Designer adapter for this set of designs (+ the project's Wall
    Systems, 2026-10-11), reused while they are unchanged (a layer-height /
    transform / header edit never rebuilds a design). `envelope`: the
    adapter of Assembly EDITING (envelopes from the shared architecture
    library; its own caches, guarded by _ENV_LOCK)."""
    from .designer_source import DesignerSource
    dkey = json.dumps(designs, sort_keys=True)
    key = dkey if wall_systems is None else json.dumps([designs, wall_systems], sort_keys=True)
    reg = _ENV_SOURCES if envelope else _SOURCES
    if key in reg:
        reg.move_to_end(key)
        return reg[key]
    src = DesignerSource.from_designs(designs, wall_systems, junctions)
    if envelope:
        src.env_lib = _arch_lib(designs, dkey)
    else:                                  # (print adapters: their OWN architecture library — the
        from .designer_source import arch_library   # generation's preliminary report; _STACK_LOCK)
        src.env_lib = arch_library(designs)
    shared = _CANON.get(key)
    if shared is None:
        shared = _CANON[key] = {}
        while len(_CANON) > 6:
            _CANON.popitem(last=False)
    src.shared_plain = shared
    reg[key] = src
    while len(reg) > 4:
        reg.popitem(last=False)
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


def _resolve_stack(data, lap=lambda name: None, envelope=False) -> dict:
    """The resolved stack exactly as /api/assembly/resolve computes it (and
    the print plan uses): the Assembly, designs, footprints, transform
    groups, resolved instances WITH their profile cut specs. Raises the
    model / profile errors. Placements come from the CANONICAL designs'
    printed footprints in both modes (`envelope`: the editing adapter), so
    an envelope resolve places every layer exactly where the print does."""
    src = None
    assembly = Assembly.from_dict(data['assembly'])
    fps = {k: Footprint.of_bbox(*b) for k, b in (data.get('footprints') or {}).items() if b}
    geo, grp = {}, None
    if data.get('designs'):
        src = _source(data['designs'], envelope, data.get('wall_systems'), data.get('junctions'))
        used = {s.design_id for s in assembly.sections} & set(src.design_ids())
        # ARCHITECTURE (2026-10-11): placement pivots (footprints), transform
        # groups and labels come from the canonical ENVELOPES — never from a
        # construction, so a Wall System change cannot move the Assembly
        arch = bool(used)
        archg = src.envelope
        labels = {}
        for d in used:
            a = archg(d)
            labels.update(a.sources or {})
            b = bbox_of(a.polylines)
            if b:
                fps[d] = Footprint.of_bbox(*b)
            g = a if envelope else src.geometry(d)      # (print: the canonical PRINTED geometry)
            geo[d] = (g.polylines, g.bead_width)
        if assembly.center_mode == 'multiple':
            parent_of = {d: src.info(d).parent for d in src.design_ids()}
            cgeo = {}
            for d in set(used) | {root_of(d, parent_of) for d in used}:
                a = archg(d)
                cgeo[d] = (a.polylines, a.bead_width)
                labels.update(a.sources or {})
            grp = build_groups(cgeo, parent_of, sorted(used), assembly.centers, labels)
            grp.by_tag = arch and not envelope
    lap('designs_and_groups')
    res = resolve(assembly, data.get('design_ids'), fps, grp)
    res0 = res                                 # (before the profiles: the construction re-check)
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
        if assembly.profiles or assembly.opening_alignment == 'plumb':
            res, presults = apply_profiles(res, assembly.profiles, targets, bead,
                                           assembly.max_overhang, sources_out, openings_out,
                                           assembly.opening_alignment)
            # the HEADERS of Header-upper openings: generated objects, used
            # exactly like placed ones (support, preview, print plan)
            assembly.objects = list(assembly.objects) + profile_headers(presults)
        lap('profiles')
    if geo and not envelope:
        # CONSTRUCTION CHAIN (WALL_ARCHITECTURE.md §Z.0): bottom-up over the
        # final instances, each layer's webs printing the landings deposited
        # below (construction_chain.py) — the print adapter builds on it
        from .construction_chain import run as run_chain
        res = run_chain(src, res, max_overhang=assembly.max_overhang)
        lap('construction_chain')
    return {'assembly': assembly, 'res': res, 'res0': res0, 'geo': geo, 'grp': grp, 'src': src, 'presults': presults,
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
    if data.get('envelope') and data.get('designs'):
        with _ENV_LOCK:                        # (never waits for / pre-empts a print preparation)
            return _resolve_locked(data, T, lap, t_all, envelope=True)
    _preempt_background()
    with _STACK_LOCK:
        return _resolve_locked(data, T, lap, t_all)


def _envelope_stack(st):
    """ENVELOPE geometry of every layer (Assembly editing): ({envelope key:
    (polylines, bead)}, {instance index: envelope key}, envelope variants)."""
    from .designer_source import architectural_cuts
    src, res = st['src'], st['res']
    geo, key_of, variants = {}, {}, {}
    for i in res.instances:
        d, tr, cu = variant_request(i)[:3]
        k = key_of[i.index] = src.envelope_key(d, tr or None, cu or None)
        if k in geo:
            continue
        g = src.envelope(d, tr or None, cu or None)
        geo[k] = (g.polylines, g.bead_width)
        if k != d:
            sem = g.diagnostics.get('semantic') or {}
            arch = architectural_cuts(cu) or []
            variants[k] = {'design_id': d, 'envelope': True,
                           'transforms': {s_: list(t) for s_, t in (tr or {}).items()},
                           **({'cuts': arch, 'profile': g.diagnostics.get('profile') or []} if arch else {}),
                           'connectors': sem.get('connectors', []), 'junctions': sem.get('junctions', [])}
    return geo, key_of, variants


def _support_report(st, geo, **kw):
    """The support report of a resolved stack + its geometry (bead-level by
    default; `level='envelope'` the preliminary one), findings tagged with
    the Assembly / Designer openings they belong to."""
    rep = analyse_support(st['res'], geo, st['assembly'], st['grp'],
                          cache=getattr(st['src'], 'support_cache', None), **kw)
    _tag_profile_findings(rep, st['res'], st['assembly'], st['presults'], st['targets'], st['bead'])
    _tag_designer_openings(rep, st['res'], st['assembly'], st['presults'], st['targets'], st['bead'],
                           st['openings_out'])
    return rep


def _resolve_locked(data, T, lap, t_all, envelope=False):
    import time
    try:
        st = _resolve_stack(data, lap, envelope)
        assembly, res, geo, grp, src = st['assembly'], st['res'], st['geo'], st['grp'], st['src']
        presults, targets_out, targets = st['presults'], st['targets_out'], st['targets']
        bead, sources_out = st['bead'], st['sources_out']
        variants = {}
        built_before = len(getattr(src, '_variants', {})) if geo else 0
        env_key = None
        if geo and envelope:
            # ARCHITECTURAL ENVELOPES (deferred print paths): no Wall System,
            # lattice or route is generated for an Assembly edit
            n0 = len(src._envelopes)
            geo, env_key, variants = _envelope_stack(st)
            T['variants'] = len(variants)
            T['envelopes_new'] = max(0, len(src._envelopes) - n0)
            lap('envelope_geometry')
        elif geo:
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
                        # the CONSTRUCTION STATE this layer's webs were built on (stage Z4):
                        # the page asks /geometry for exactly this variant
                        **({'state': i.state} if i.state else {}),
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
    if env_key is not None:
        for d_ in out['instances']:            # the page draws / keys ENVELOPES
            d_['print_key'] = d_['geometry_key']
            d_['geometry_key'] = env_key[d_['index']]
        out['geometry'] = 'envelope'
    if variants:
        out['variants'] = variants
    if geo:
        out['profile_targets'] = targets_out
        out['profile_sources'] = sources_out
        out['profiles'] = [r.to_dict() for r in presults if r.kind != 'alignment']
        # the DESIGNER's openings of the stack (Opening Alignment is set per id)
        # + the alignment results of the plumb ones
        dops = {}
        for d, ops in st['openings_out'].items():
            for o in ops:
                e = dops.setdefault(str(o['id']), {'id': str(o['id']), 'label': o.get('label') or o['id'],
                                                   'source': o.get('source'), 'width': o.get('width'),
                                                   'curved': o.get('at') is None, 'designs': []})
                e['designs'].append(d)
        out['designer_openings'] = list(dops.values())
        out['alignments'] = [dict(r.to_dict(), opening=r.id.split(':', 1)[1]) for r in presults
                             if r.kind == 'alignment']
        if env_key is not None:                # PRELIMINARY: architectural (envelope-level)
            rep = _support_report(st, geo, level='envelope', key_of=lambda i: env_key[i.index])
        else:
            rep = _support_report(st, geo)
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
    if data.get('envelope'):
        with _ENV_LOCK:
            return _envelope_geometry_locked(data)
    _preempt_background()
    with _STACK_LOCK:
        return _geometry_locked(data)


def _envelope_geometry_locked(data):
    """/geometry with `envelope`: the ARCHITECTURAL ENVELOPES of designs
    (`ids`) or of envelope variants (`variants`: design id + transforms +
    cuts) — what the Assembly page draws while editing."""
    try:
        src = _source(data['designs'], envelope=True, wall_systems=data.get('wall_systems'),
                      junctions=data.get('junctions'))
        ids = data.get('ids') or ([] if data.get('variants') else src.design_ids())
        tf = lambda v: {s_: tuple(t) for s_, t in (v.get('transforms') or {}).items()}    # noqa: E731
        out = {}
        for key, v in (data.get('variants') or {}).items():
            g = src.envelope(v['design_id'], tf(v) or None, v.get('cuts') or None)
            inf = src.info(v['design_id'])
            out[key] = {'name': inf.name, 'parent': inf.parent, 'polylines': g.polylines,
                        'bead_width': g.bead_width, 'sources': g.sources, 'variant_of': v['design_id'],
                        'envelope': True}
        for i in ids:
            g = src.envelope(i)
            inf = src.info(i)
            out[i] = {'name': inf.name, 'parent': inf.parent, 'polylines': g.polylines,
                      'bead_width': g.bead_width, 'sources': g.sources, 'envelope': True}
    except Exception as e:      # a design the Designer cannot build: say so
        return jsonify({'error': str(e)}), 400
    return jsonify({'designs': out})


def _geometry_locked(data):
    try:
        src = _source(data['designs'], wall_systems=data.get('wall_systems'), junctions=data.get('junctions'))
        ids = data.get('ids') or ([] if data.get('variants') else src.design_ids())
        out = {}
        req = data.get('variants') or {}
        tf = lambda v: {s_: tuple(t) for s_, t in (v.get('transforms') or {}).items()}
        src.prefetch([(v['design_id'], tf(v), v.get('cuts') or None, v.get('state') or None) for v in req.values()])
        for key, v in req.items():
            g = src.geometry(v['design_id'], tf(v) or None, v.get('cuts') or None, v.get('state') or None)
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


def _plan_key(data) -> str:
    """The plan cache key: everything that changes the ORDERED GEOMETRY (not the speeds)."""
    asm = dict(data['assembly'])
    asm.pop('print_speeds', None)
    return json.dumps([asm, data.get('designs'), data.get('wall_systems'), data.get('start')], sort_keys=True)


def _planned(data, cancelled=None):
    """(plan, resolved stack, extras) for this request, cached by everything
    that changes the ORDERED GEOMETRY — not the speeds (a speed edit only
    re-times the same plan). `rebuild`: drop this request's cached plan and
    routes first (the Simulation's recovery control). Caller holds
    _STACK_LOCK."""
    from .print_plan import build_plan
    key = _plan_key(data)
    if data.get('rebuild'):
        _PLANS.pop(key, None)
        ws_ = data.get('wall_systems')
        src = _SOURCES.get(json.dumps(data.get('designs'), sort_keys=True) if ws_ is None
                           else json.dumps([data.get('designs'), ws_], sort_keys=True))
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
    # the AUTHORITATIVE BEAD-LEVEL support check of the prepared print
    # geometry (the very geometry just planned; the Assembly's live report is
    # the preliminary envelope-level one)
    if cancelled is not None and cancelled():
        from .print_plan import PlanCancelled
        raise PlanCancelled('print plan cancelled')
    pgeo = dict(st['geo'])
    for i in res.instances:
        if i.is_variant and i.geometry_key not in pgeo:
            g = layer_geometry(src, i)
            pgeo[i.geometry_key] = (g.polylines, g.bead_width)
    support = _support_report(st, pgeo).to_dict() if pgeo else None
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
              'designs': {d: src.info(d).name for d in src.design_ids()},
              **({'support': support} if support is not None else {})}
    _PLANS[key] = (plan, extras)
    while len(_PLANS) > 4:
        _PLANS.popitem(last=False)
    return _PLANS[key]


# ---- WALL SYSTEMS GENERATION (2026-10-11: the Wall Systems workspace) ---------------
# "Generate Wall Systems" = the existing full-resolution pipeline for the whole
# assembled building: every layer's printable geometry (Wall Systems, lattice,
# caps, junctions — the variants of the print adapter) + the bead-level
# support report. A BACKGROUND job (`job`, cancellable like a print plan; it
# never blocks envelope editing). The result is cached by its GENERATION KEY
# (designs + the project's Wall Systems + the Assembly without speeds / lift
# settings); the Print Simulation plans only from a current generation.
_GENERATED: OrderedDict = OrderedDict()


def generation_key(data) -> str:
    asm = dict(data['assembly'])
    asm.pop('print_speeds', None)
    asm.pop('machine', None)
    return json.dumps([asm, data.get('designs'), data.get('wall_systems')], sort_keys=True)


def _generated_current(data) -> bool:
    """Is the request's design generated, with every layer's geometry still
    in the print adapter (so planning builds nothing)?"""
    g = _GENERATED.get(generation_key(data))
    if g is None:
        return False
    src = g['src']
    for i in g['res'].instances:
        if i.is_variant and _variant_key(i) not in getattr(src, '_variants', {}):
            return False
    return True


def _variant_key(inst):
    """The print adapter's cache key of a layer's variant — design, transforms,
    cuts AND its construction state (stage Z4: a stateful layer is a variant
    keyed by its state; a key without it names a build that never happened)."""
    from .designer_source import _vkey
    req = variant_request(inst)
    d, tr, cu = req[:3]
    return _vkey(d, tr or None, cu or None, req[3] if len(req) > 3 else None)


@bp.route('/api/wallsystems/generate', methods=['POST'])
def api_wallsystems_generate():
    """GENERATE WALL SYSTEMS: {assembly, designs, wall_systems, job} → {key,
    layers [{index, design_id, section_index, z_bottom, z_top, transform,
    geometry_key}], support (BEAD-LEVEL), stats}. 409 {cancelled} when a
    newer job replaced it or the page cancelled it (/print_plan/cancel)."""
    import time
    from .print_plan import PlanCancelled
    data = request.get_json(force=True)
    job, cancelled = data.get('job'), None
    if job is not None:
        _JOBS['latest'] = job
        epoch = _JOBS['epoch']
        cancelled = lambda: _JOBS['latest'] != job or _JOBS['epoch'] != epoch    # noqa: E731
    t0 = time.perf_counter()
    try:
        if not data.get('designs'):
            raise AssemblyError('generating Wall Systems needs the Layer Designs')
        key = generation_key(data)
        with _STACK_LOCK:
            hit = _GENERATED.get(key)
            if hit is None or not _generated_current(data):
                st = _resolve_stack(data)
                src, res = st['src'], st['res']
                n0 = len(getattr(src, '_variants', {}))
                src.prefetch([variant_request(i) for i in res.instances if i.is_variant], cancelled=cancelled)
                if cancelled is not None and cancelled():
                    raise PlanCancelled('generation cancelled')
                geo = dict(st['geo'])
                for i in res.instances:
                    if i.is_variant and i.geometry_key not in geo:
                        g = layer_geometry(src, i)
                        geo[i.geometry_key] = (g.polylines, g.bead_width)
                if cancelled is not None and cancelled():
                    raise PlanCancelled('generation cancelled')
                rep = _support_report(st, geo)
                if cancelled is not None and cancelled():
                    raise PlanCancelled('generation cancelled')
                inspection = _inspection(st, geo, rep)
                for d in src.design_ids():               # (the Base design view: its canonical build)
                    if src.info(d).parent is None:
                        src.geometry(d)
                hit = {'src': src, 'res': res, 'support': rep.to_dict(), 'inspection': inspection,
                       'stats': {'layers': len(res.instances),
                                 'variants': sum(1 for i in res.instances if i.is_variant),
                                 'built': max(0, len(getattr(src, '_variants', {})) - n0),
                                 'seconds': round(time.perf_counter() - t0, 3)}}
                _GENERATED[key] = hit
                while len(_GENERATED) > 4:
                    _GENERATED.popitem(last=False)
            _GENERATED.move_to_end(key)
            res = hit['res']
            layers = [{'index': i.index, 'design_id': i.design_id, 'section_index': i.section_index,
                       'z_bottom': i.z_bottom, 'z_top': i.z_top, 'transform': i.transform.to_dict(),
                       'geometry_key': i.geometry_key if i.is_variant else i.design_id}
                      for i in res.instances]
            systems = {}
            for d in dict.fromkeys(i.design_id for i in res.instances):
                for w in hit['src'].lib.document(d).get('wall_systems') or []:
                    systems[w.get('id')] = {'name': w.get('name') or w.get('id'), 'type': w.get('type')}
        return jsonify({'key': key, 'layers': layers, 'support': hit['support'], 'stats': hit['stats'],
                        'systems': systems, 'inspection': hit['inspection']})
    except PlanCancelled as e:
        return jsonify({'error': str(e), 'cancelled': True}), 409
    except (AssemblyError, ProfileError, KeyError, ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400


# PRINTABILITY INSPECTION (M2, 2026-10-11): what a final BEAD-LEVEL finding is
# about, from data the generation already has — never a threshold or a
# suppression: 'architectural' = the PRELIMINARY envelope check of the same
# stack flags the same place (the wall body itself overhangs / an opening
# closes); otherwise by the printed strand under it: 'lattice' (Wall Infill
# lattice, generated Wall System paths) or 'construction' (skins, caps, jambs
# — construction detail of a supported wall). CONTINUITY: the generated
# Wall System bands whose route cannot close (Designer report).
FINDING_CATEGORIES = ('architectural', 'lattice', 'construction')
_LATTICE_ROLES = ('lattice', 'wall_system')


def _nearest_strand(geom, inst, p):
    """(index, role) of the printed polyline of a layer nearest to a placed point."""
    import math
    t = inst.transform
    x, y = (p[0] - t.tx) / t.scale, (p[1] - t.ty) / t.scale
    best, bi = math.inf, None
    for i, pl in enumerate(geom.polylines):
        pts = pl['pts'] + ([pl['pts'][0]] if pl.get('closed') else [])
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L2))
            d = math.hypot(x - a[0] - u * dx, y - a[1] - u * dy)
            if d < best:
                best, bi = d, i
    strands = (geom.diagnostics.get('routing') or {}).get('strands') or []
    role = strands[bi].get('role') if bi is not None and bi < len(strands) else None
    return bi, role


def _inspection(st, geo, rep):
    """{findings: [{id, category, role, architectural}], continuity: [...],
    architectural: the preliminary envelope report of the same stack, counts}."""
    import math
    src, res = st['src'], st['res']
    try:
        egeo, key_of, _ = _envelope_stack(st)
        arch = _support_report(st, egeo, level='envelope', key_of=lambda i: key_of[i.index]).to_dict()
    except Exception as e:                     # (reported, never blocks the generation)
        arch = {'findings': [], 'level': 'envelope', 'error': str(e)}
    bead = st['bead']
    out = []
    for f in rep.findings:
        link, role, cat = None, None, None
        for a in arch.get('findings') or []:
            if a['layer'] == f.layer and math.dist(a['centre'], f.centre) <= (a['span'] + f.span) / 2 + 2 * bead:
                link, cat = a['id'], 'architectural'
                break
        inst = res.instances[f.layer]
        pts = [q for run in f.runs for q in run]
        if pts:
            _, role = _nearest_strand(layer_geometry(src, inst), inst, pts[len(pts) // 2])
        if cat is None:
            cat = 'lattice' if role in _LATTICE_ROLES else 'construction'
        out.append({'id': f.id, 'category': cat, 'role': role, 'architectural': link})
    cont = {}
    for i in res.instances:
        g = layer_geometry(src, i)
        for w in g.diagnostics.get('wall_systems') or []:
            if w.get('closable') is False or w.get('status') == 'mixed':
                why = ('odd number of paths: the route cannot close at its wall ends / openings'
                       if w.get('closable') is False else
                       'a wall of another construction spans this band (prints Skin + Web there)')
                c = cont.setdefault((w.get('system_id'), why), {'system': w.get('system_id'), 'reason': why,
                                                                'sources': w.get('sources') or [], 'layers': []})
                c['layers'].append(i.index)
    counts = {k: sum(1 for x in out if x['category'] == k) for k in FINDING_CATEGORIES}
    return {'findings': out, 'continuity': list(cont.values()), 'architectural': arch, 'counts': counts,
            'openings': _construction_checks(st)}


def _construction_checks(st):
    """FINAL construction check of the Assembly's OPENING / TAPER profiles
    (2026-10-11): the Assembly validates them against the ARCHITECTURE only
    (pier and retained wall in beads); here the same profiles are judged
    again with each wall's CONSTRUCTION minimum (WallTarget.min_length:
    2 × thickness for generated constructions — Interleaved, Parallel,
    Linked, Chain) — every error the construction adds, per profile, with
    the openings concerned and the layer it names. Reported, never applied:
    the cuts stay the architectural ones."""
    import re
    src, A = st['src'], st['assembly']
    if not A.profiles or not hasattr(src, 'construction_walls'):
        return []
    ct, adds = {}, False
    for d in st['targets']:
        cw = src.construction_walls(d)
        ct[d] = {t.key: t for t in map(WallTarget.from_dict, cw)}
        adds = adds or any(w.get('min_length') for w in cw)
    if not adds:
        return []
    _, cres = apply_profiles(st['res0'], A.profiles, ct, st['bead'], A.max_overhang, st['sources_out'],
                             st['openings_out'], A.opening_alignment)
    arch = {r.id: r for r in st['presults']}
    index = {p.id: n for n, p in enumerate(A.profiles)}
    out = []
    for r in cres:
        a = arch.get(r.id)
        extra = [e for e in r.errors if a is None or e not in a.errors]
        if not extra:
            continue
        m = re.search(r'layer (\d+)', ' '.join(extra))
        out.append({'profile': r.id, 'index': index.get(r.id), 'kind': r.kind, 'errors': extra,
                    'members': [j for j, mm in enumerate(r.members or []) if set(mm.get('errors') or ()) & set(extra)],
                    'layer': int(m.group(1)) - 1 if m else None, 'wall': r.wall})
    return out


def _layer_polys(geom):
    """The printable polylines of generated geometry, each with its Wall
    System record (the routing description's strand identity)."""
    polys = [{'id': p.get('id'), 'pts': p['pts'], 'closed': p.get('closed', False), 'src': p.get('src')}
             for p in geom.polylines]
    strands = (geom.diagnostics.get('routing') or {}).get('strands') or []
    for p, a in zip(polys, strands):
        sy = a.get('system') if isinstance(a, dict) else None
        if sy:
            p['system'] = sy.get('id') if isinstance(sy, dict) else sy
    return polys


@bp.route('/api/wallsystems/layer', methods=['POST'])
def api_wallsystems_layer():
    """The GENERATED printable geometry of one layer ({…the generate body,
    index}) — read from the current generation only (nothing is built):
    409 {outdated} when that design is not generated; 423 {busy} while a
    generation holds the Designer adapter."""
    data = request.get_json(force=True)
    if not _STACK_LOCK.acquire(timeout=0.5):
        return jsonify({'error': 'generating', 'busy': True}), 423
    try:
        g = _GENERATED.get(generation_key(data))
        if g is None:
            return jsonify({'error': 'not generated for this design', 'outdated': True}), 409
        if data.get('design') is not None:
            # a design's CANONICAL generated geometry (the Base design view):
            # built by the generation's resolve (its footprints) — read only
            src, d = g['src'], data['design']
            if d not in getattr(src, '_plain', {}):
                return jsonify({'error': 'not generated for this design', 'outdated': True}), 409
            geom = src.geometry(d)
            return jsonify({'index': -1, 'design_id': d, 'bead_width': geom.bead_width,
                            'transform': {'scale': 1.0, 'tx': 0.0, 'ty': 0.0},
                            'polylines': _layer_polys(geom)})
        idx = int(data.get('index', 0))
        insts = g['res'].instances
        if not 0 <= idx < len(insts):
            return jsonify({'error': f'no layer {idx + 1}'}), 400
        inst = insts[idx]
        src = g['src']
        if inst.is_variant and _variant_key(inst) not in src._variants:
            return jsonify({'error': 'no longer cached — generate again', 'outdated': True}), 409
        geom = layer_geometry(src, inst)
        polys = _layer_polys(geom)
        return jsonify({'index': idx, 'polylines': polys, 'bead_width': geom.bead_width,
                        'transform': inst.transform.to_dict(), 'design_id': inst.design_id})
    finally:
        _STACK_LOCK.release()


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
            if data.get('require_generated') and not _generated_current(data):
                # the Print Simulation consumes the CURRENT generated Wall
                # Systems only — it never generates them itself
                return jsonify({'error': 'The Wall Systems are not generated for the current design — '
                                         'generate them in the Wall Systems workspace.',
                                'outdated': True}), 409
            plan, extras = _planned(data, cancelled)
        speeds = PrintSpeeds.from_dict(data.get('speeds') or (data['assembly'].get('print_speeds') or {}))
        est = estimate(plan, speeds)
        detail = data.get('detail') or 'summary'
        if detail == 'stream':
            out = {'stream': stream(plan, speeds), 'plan': plan.to_dict('summary'), 'estimate': est, **extras}
            # MACHINE (2026-10-15): what the plan needs + the check against the
            # project's machine profile (informational; the plan is never changed)
            from machine_profile import requirements as _req, preflight as _pf
            out['requirements'] = _req(plan)
            if isinstance(data.get('machine_profile'), dict):
                out['preflight'] = _pf(out['requirements'], data['machine_profile'])
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


@bp.route('/api/assembly/print_job', methods=['POST'])
def api_print_job():
    """EXPORT PRINT JOB (2026-10-14): the CURRENT planned print as a `.mudjob`
    machine job (machine_job.py; contract: contracts/mudjob/README.md).
    Body: the same request that planned it (/api/assembly/print_plan:
    `assembly`, `designs`, `wall_systems`, `start`) + `name` (project name)
    + `speeds` (optional; default the Assembly's print_speeds — feeds of the
    G-code). Exports ONLY a plan already made for exactly this request (Plan
    Print) — it never plans, routes or re-derives anything; otherwise 409
    `outdated`. → {job: <executable .mudjob document>, filename, executable,
    lines, placeholder_speeds}."""
    from .machine_job import ExportError, export_job, job_filename
    from .print_plan import PrintSpeeds, PlanError
    data = request.get_json(force=True)
    try:
        if not data.get('designs') or not isinstance(data.get('assembly'), dict):
            raise AssemblyError('a print job needs the planned Layer Designs and Assembly')
        with _STACK_LOCK:
            cached = _PLANS.get(_plan_key(data))
        if cached is None:
            return jsonify({'error': 'There is no current print plan for this design — plan the print '
                                     '(Plan Print) and export again.', 'outdated': True}), 409
        name = data.get('name') if isinstance(data.get('name'), str) else None
        given = data.get('speeds') or (data['assembly'].get('print_speeds') or {})
        placeholder = not (given.get('print') and given.get('travel'))
        doc = export_job(cached[0], PrintSpeeds.from_dict(given), name=(name or '').strip() or None,
                         placeholder_speeds=placeholder, machine_profile=data.get('machine_profile'))
    except (AssemblyError, ExportError, PlanError, KeyError, ValueError, TypeError) as e:   # (incl. a bad machine profile)
        return jsonify({'error': str(e)}), 400
    return jsonify({'job': doc, 'filename': job_filename(name), 'executable': doc['executable'],
                    'lines': sum(len(L['gcode']) for L in doc['lifts']), 'placeholder_speeds': placeholder,
                    'preflight': doc.get('preflight'), 'requirements': doc['requirements']})


@bp.route('/api/assembly/print_plan/cancel', methods=['POST'])
def api_print_plan_cancel():
    """The page no longer needs background plan `job` (it left the Print
    Simulation, or the project changed): stop it before its next layer."""
    job = (request.get_json(force=True, silent=True) or {}).get('job')
    if job is not None and _JOBS['latest'] == job:
        _JOBS['latest'] = None
    return jsonify({'ok': True})


@bp.route('/api/assembly/junctions_shadow', methods=['POST'])
def api_junctions_shadow():
    """PERSISTENT JUNCTIONS report (M4; M5 trims; read only): body {project}
    (current schema). The registry (stored ids kept — members may join /
    leave a junction), per Layer Design the consistency with the production
    topology, per resolved layer which junctions exist (ceasing / appearing
    = valid topology), and per layout state each trim's status and the
    junction ids it is bounded by (`trims`)."""
    import junctions as J
    from .junction_shadow import presence
    P = (request.get_json(force=True) or {}).get('project') or {}
    try:
        designs = P['designer']['designs']
        ws = P.get('wall_systems') or []
        reg, rep = J.build_registry(designs, ws, P.get('junctions'))
        cons = J.consistency_all(designs, ws, reg['records'])
        # (`layers: false` — the Designer's inspector: no stack resolve)
        layers = presence(dict(P, junctions=reg), reg)['layers'] \
            if (request.get_json(force=True) or {}).get('layers', True) else None
        trims = J.trim_links(designs, ws, reg['records'])
    except (KeyError, TypeError, ValueError, AssemblyError, ProfileError) as e:
        return jsonify({'error': f'{type(e).__name__}: {e}'}), 400
    return jsonify({'registry': reg, 'report': rep, 'consistency': cons, 'layers': layers, 'trims': trims})
