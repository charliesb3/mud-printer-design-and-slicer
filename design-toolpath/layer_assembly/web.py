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
  POST /api/assembly/geometry  {designs, ids | variants} → resolved 2D printable geometry
                                                         per design (or per variant key)
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

from flask import Blueprint, jsonify, request

from .model import Assembly, resolve, AssemblyError, Footprint
from .source import bbox_of
from .support import analyse_support
from .components import root_of
from .groups import build_groups

bp = Blueprint('layer_assembly', __name__)

_SOURCES: OrderedDict = OrderedDict()      # designs JSON → DesignerSource (builds cached inside)


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
    try:
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
        variants = {}
        built_before = len(getattr(src, '_variants', {})) if geo else 0
        if geo:
            # grouped layers print their SEMANTIC variant (the Designer moves the
            # architecture and re-resolves it) — support / headers use it too
            sem = [i for i in res.instances if i.semantic]
            src.prefetch([(i.design_id, i.transforms()) for i in sem])
            for i in sem:
                if i.geometry_key not in geo:
                    g = src.geometry(i.design_id, i.transforms())
                    geo[i.geometry_key] = (g.polylines, g.bead_width)
                    variants[i.geometry_key] = {
                        'design_id': i.design_id,
                        'transforms': {s_: list(t) for s_, t in i.transforms().items()},
                        'connectors': (g.diagnostics.get('semantic') or {}).get('connectors', []),
                        # DERIVED junctions (forms that merely intersect): recomputed
                        # per layer; present False = the forms have separated here
                        'junctions': (g.diagnostics.get('semantic') or {}).get('junctions', [])}
            T['variants'] = len(variants)
            T['variants_new'] = max(0, len(getattr(src, '_variants', {})) - built_before)
        lap('variant_geometry')
    except (AssemblyError, KeyError, ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400
    out = resolved_to_dict(res)
    if variants:
        out['variants'] = variants
    if geo:
        out['support'] = analyse_support(res, geo, assembly, grp).to_dict()
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
    try:
        src = _source(data['designs'])
        ids = data.get('ids') or ([] if data.get('variants') else src.design_ids())
        out = {}
        req = data.get('variants') or {}
        src.prefetch([(v['design_id'], {s_: tuple(t) for s_, t in v['transforms'].items()})
                      for v in req.values()])
        for key, v in req.items():
            g = src.geometry(v['design_id'], {s_: tuple(t) for s_, t in v['transforms'].items()})
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
