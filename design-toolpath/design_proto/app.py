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
    OffsetTreatment, LatticeInstance, TraversalConstraints,
    GENERATORS, Vec2
)

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

def _deserialise_path(d: dict):
    kind = d.get('type', 'ExplicitPath')
    kwargs = dict(
        id=d.get('id'),
        label=d.get('label', ''),
        closed=d.get('closed', False),
        role=d.get('role', 'free'),
        visible=d.get('visible', True),
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
        return RectanglePath(d['x'], d['y'], d['w'], d['h'], **kwargs)
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


# ---------------------------------------------------------------------------
# API — effective paths (geometry only, no routing)
# ---------------------------------------------------------------------------

@app.route('/api/effective_paths', methods=['POST'])
def api_effective_paths():
    try:
        data = request.get_json(force=True)
        layer = _deserialise_layer(data)
        paths = layer.effective_paths()
        return jsonify({
            'paths': [p.to_dict() for p in paths],
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

        # Build routing layer
        routing_layer = layer.to_routing_layer()

        # Import routing engine from toolpath_proto
        proto_dir = os.path.join(os.path.dirname(__file__),
                                 '..', 'toolpath_proto')
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)

        from graph import route_layer, compute_metrics, graph_info

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

        metrics = compute_metrics(moves)
        ginfo = graph_info(routing_layer)

        return jsonify({
            'layer': {
                'id': layer.id,
                'label': layer.label,
                'paths': [p.to_dict() for p in layer.effective_paths()],
            },
            'moves': [m.to_dict() for m in moves],
            'metrics': metrics,
            'graph': ginfo,
        })
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5051)
