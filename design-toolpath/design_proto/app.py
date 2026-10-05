"""
Design Canvas Prototype — Flask application.

Serves the design canvas UI and exposes a stateless API:
  GET  /api/generators          list available lattice generators + params
  POST /api/route               route a PrintLayer from JSON body
  POST /api/effective_paths     compute effective paths from a PrintLayer JSON
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json
import traceback
from flask import Flask, jsonify, request, send_from_directory

from model import (
    PrintLayer, ExplicitPath, LinePath, CirclePath, EllipsePath, RectanglePath,
    QuadBezierPath,
    OffsetTreatment, LatticeInstance, TraversalConstraints, Opening,
    RegionOverride, RegionInfill, JunctionSetting, WallSpec, NetworkWall,
    InsetPath, WallRelation,
    GENERATORS, Vec2
)
import infill as infill_mod

app = Flask(__name__, static_folder='static', static_url_path='/static')


# ---------------------------------------------------------------------------
# Static / index
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _wall(d):
    w = d.get('wall')
    if not w or float(w.get('thickness', 0) or 0) <= 0:
        return None
    return WallSpec(float(w['thickness']), w.get('align', 'auto') or 'auto',
                    bool(w.get('print_reference', False)))


def _deserialise_path(d: dict):
    p = _deserialise_path_geom(d)
    p.wall = _wall(d)
    return p


def _deserialise_path_geom(d: dict):
    kind = d.get('type', 'ExplicitPath')
    kwargs = dict(
        id=d.get('id'),
        label=d.get('label', ''),
        closed=d.get('closed', False),
        role=d.get('role', 'free'),
        visible=d.get('visible', True),
        corner_radius=(None if d.get('corner_radius') is None
                       else float(d['corner_radius'])),
    )
    if kind == 'LinePath':
        s, e = d['start'], d['end']
        return LinePath(Vec2(*s), Vec2(*e), **kwargs)
    if kind == 'CirclePath':
        return CirclePath(d['cx'], d['cy'], d['radius'], **kwargs)
    if kind == 'EllipsePath':
        return EllipsePath(d['cx'], d['cy'], d['rx'], d['ry'],
                           d.get('rotation', 0.0), **kwargs)
    if kind == 'RectanglePath':
        return RectanglePath(d['x'], d['y'], d['w'], d['h'],
                             float(d.get('rotation', 0.0) or 0.0), **kwargs)
    if kind == 'InsetPath':
        pts = [Vec2(p[0], p[1]) for p in d.get('points', [])]
        kwargs.pop('closed', None)
        return InsetPath(d['parent_id'], float(d.get('distance', 10.0)),
                         d.get('mode', 'inset'), pts, **kwargs)
    if kind == 'QuadBezierPath':
        s, e, c = d['start'], d['end'], d['control']
        return QuadBezierPath(Vec2(*s), Vec2(*e), Vec2(*c), **kwargs)
    # Default: ExplicitPath
    pts = [Vec2(p[0], p[1]) for p in d.get('control_points', d.get('points', []))]
    return ExplicitPath(pts, **kwargs)


def _deserialise_layer(data: dict) -> PrintLayer:
    layer = PrintLayer(
        id=data.get('id', 'layer'),
        label=data.get('label', ''),
    )
    for pd in data.get('source_paths', []):
        layer.source_paths.append(_deserialise_path(pd))

    for od in data.get('offset_treatments', []):
        layer.offset_treatments.append(OffsetTreatment(
            id=od['id'],
            source_path_id=od['source_path_id'],
            distance=od['distance'],
            role=od.get('role', 'inner'),
            label=od.get('label', ''),
        ))

    for ld in data.get('lattice_instances', []):
        layer.lattice_instances.append(LatticeInstance(
            id=ld['id'],
            generator_name=ld['generator'],
            path_a_id=ld['path_a_id'],
            path_b_id=ld['path_b_id'],
            params=ld.get('params', {}),
            variation_index=ld.get('variation_index', 0),
            label=ld.get('label', ''),
        ))

    cd = data.get('constraints', {})
    layer.constraints = TraversalConstraints(
        start_path_id=cd.get('start_path_id'),
        start_t=cd.get('start_t'),
        reverse_direction=cd.get('reverse_direction', False),
        component_order=cd.get('component_order'),
    )

    layer.corner_radius = float(data.get('corner_radius', 0.0))
    layer.cap_style = data.get('cap_style', 'flat')
    layer.cap_corner_radius = float(data.get('cap_corner_radius', 0.0))

    # Openings (absent in older payloads → none)
    for od in data.get('openings', []) or []:
        layer.openings.append(Opening(
            id=od['id'],
            source_path_id=od['source_path_id'],
            center_s=float(od.get('center_s', 0.0)),
            width=float(od.get('width', 12.0)),
            end_treatment=od.get('end_treatment', 'inherit'),
            z_min=od.get('z_min'),
            z_max=od.get('z_max'),
            label=od.get('label', ''),
        ))

    # Wall-network region overrides (paint bucket); absent → none
    for rd in data.get('region_overrides', []) or []:
        layer.region_overrides.append(RegionOverride(
            id=rd.get('id', ''),
            path_id=rd['path_id'],
            s=float(rd.get('s', 0.0)),
            offset=float(rd.get('offset', 0.0)),
            kind=rd.get('kind', 'wall'),
        ))

    # Region infill (wall-region lattice) and junction-corner treatment
    for fd in data.get('infills', []) or []:
        layer.infills.append(RegionInfill(
            id=fd['id'], path_id=fd['path_id'],
            pattern=fd.get('pattern', 'zigzag'),
            params=dict(fd.get('params') or {}),
            variation_index=int(fd.get('variation_index', 0) or 0),
            kind=fd.get('kind', 'wall') or 'wall',
        ))
    layer.junction_style = data.get('junction_style', 'miter') or 'miter'
    layer.junction_radius = float(data.get('junction_radius', 0.0) or 0.0)
    for rd in data.get('wall_relations', []) or []:
        layer.wall_relations.append(WallRelation(
            id=rd.get('id') or 'wr', outer_id=rd['outer_id'], inner_id=rd['inner_id'],
            thickness=float(rd.get('thickness', 10.0)), driver=rd.get('driver', 'outer')))
    for wd in data.get('network_walls', []) or []:
        layer.network_walls.append(NetworkWall(
            id=wd['id'], path_id=wd['path_id'],
            thickness=float(wd.get('thickness', 0) or 0),
            align=wd.get('align', 'auto') or 'auto',
            print_reference=bool(wd.get('print_reference', False))))
    layer.return_paths = bool(data.get('return_paths', True))
    layer.prefer_closed = bool(data.get('prefer_closed', True))
    for jd in data.get('junction_overrides', []) or []:
        layer.junction_overrides.append(JunctionSetting(
            key=jd['key'], treatment=jd.get('treatment', 'miter'),
            radius=float(jd.get('radius', 0.0) or 0.0)))

    return layer


# ---------------------------------------------------------------------------
# API — generators
# ---------------------------------------------------------------------------

@app.route('/api/generators')
def api_generators():
    result = []
    for name, gen in GENERATORS.items():
        result.append({
            'name': name,
            'parameters': [
                {
                    'name': p.name,
                    'label': p.label,
                    'default': p.default,
                    'min': p.min,
                    'max': p.max,
                    'step': p.step,
                }
                for p in gen.parameters()
            ],
        })
    return jsonify(result)


@app.route('/api/infill_patterns')
def api_infill_patterns():
    import solid as solid_mod
    return jsonify([{'name': k, 'description': v, 'kind': 'wall',
                     'parameters': infill_mod.PARAMETERS}
                    for k, v in infill_mod.PATTERNS.items()] +
                   [{'name': k, 'description': v, 'kind': 'solid',
                     'parameters': solid_mod.PARAMETERS}
                    for k, v in solid_mod.PATTERNS.items()])


# ---------------------------------------------------------------------------
# API — effective paths (geometry only, no routing)
# ---------------------------------------------------------------------------

@app.route('/api/effective_paths', methods=['POST'])
def api_effective_paths():
    try:
        data = request.get_json(force=True)
        layer = _deserialise_layer(data)
        paths, meta = layer._build_effective()
        return jsonify({
            'paths': [p.to_dict() for p in paths],
            'network': meta['network'],
        })
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 400


# ---------------------------------------------------------------------------
# API — route (geometry + toolpath)
# ---------------------------------------------------------------------------

@app.route('/api/route', methods=['POST'])
def api_route():
    try:
        data = request.get_json(force=True)
        layer = _deserialise_layer(data)

        # Build routing layer (effective geometry computed once)
        paths, meta = layer._build_effective()
        routing_layer = layer.to_routing_layer(paths, meta)

        # Import routing engine from toolpath_proto
        proto_dir = os.path.join(os.path.dirname(__file__),
                                 '..', 'toolpath_proto')
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)

        from graph import (route_layer, compute_metrics, graph_info,
                           label_passes, route_ends)

        # Apply traversal constraints
        c = layer.constraints
        start = None
        if c.start_path_id and c.start_t is not None:
            src = next((p for p in layer.source_paths
                        if p.id == c.start_path_id), None)
            if src:
                pt = src.point_at(c.start_t)
                from geometry import Vec2 as RVec2
                start = RVec2(pt.x, pt.y)

        moves = route_layer(
            routing_layer,
            start=start,
            component_order=c.component_order,
        )

        # Reverse direction: reverse move order and swap each move's start/end
        if c.reverse_direction and moves:
            from geometry import PrintMove as _PM
            moves = [
                _PM(kind=m.kind, strand_id=m.strand_id, seg_idx=m.seg_idx,
                    start=m.end, end=m.start)
                for m in reversed(moves)
            ]
            moves = label_passes(moves)   # first pass prints, later passes retrace

        metrics = compute_metrics(moves)
        ends = route_ends(moves)
        ginfo = graph_info(routing_layer)

        return jsonify({
            'layer': {
                'id': layer.id,
                'label': layer.label,
                'paths': [p.to_dict() for p in paths],
            },
            'network': meta['network'],
            'moves': [m.to_dict() for m in moves],
            'metrics': metrics,
            'graph': ginfo,
            # For a future layer planner: closed → next layer starts here;
            # open → next layer can run this route reversed (end → start).
            'route_ends': ends.to_dict() if ends else None,
        })
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5051)
