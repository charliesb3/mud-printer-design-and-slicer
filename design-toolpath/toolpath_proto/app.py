"""
Toolpath prototype — Flask application.

Routes:
  GET  /                          serve the UI
  GET  /api/cases                 list available test cases
  GET  /api/cases/<id>            geometry for one test case
  POST /api/route/<id>            compute toolpath, optional overrides in JSON body
  GET  /api/graph/<id>            graph topology info (node/edge/odd counts)
"""
from __future__ import annotations
import os
from flask import Flask, jsonify, request, send_from_directory

from geometry import Vec2
from graph import route_layer, compute_metrics, graph_info
from test_cases import ALL_CASES, get_case

app = Flask(__name__, static_folder='static')


# ---------------------------------------------------------------------------
# Static / UI
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route('/api/cases')
def list_cases():
    return jsonify([
        {'id': k, 'label': v.label}
        for k, v in ALL_CASES.items()
    ])


@app.route('/api/cases/<case_id>')
def get_case_geometry(case_id: str):
    try:
        layer = get_case(case_id)
    except KeyError:
        return jsonify({'error': f'Unknown case: {case_id}'}), 404
    return jsonify(layer.to_dict())


@app.route('/api/route/<case_id>', methods=['POST', 'GET'])
def route_case(case_id: str):
    try:
        layer = get_case(case_id)
    except KeyError:
        return jsonify({'error': f'Unknown case: {case_id}'}), 404

    body = request.get_json(silent=True) or {}
    start = None
    if 'start' in body:
        sx, sy = body['start']
        start = Vec2(sx, sy)

    component_order = body.get('component_order')

    moves = route_layer(layer, start=start, component_order=component_order)
    metrics = compute_metrics(moves)
    info = graph_info(layer)

    return jsonify({
        'case_id': case_id,
        'layer': layer.to_dict(),
        'moves': [m.to_dict() for m in moves],
        'graph': info,
        'metrics': metrics,
    })


@app.route('/api/graph/<case_id>')
def get_graph_info(case_id: str):
    try:
        layer = get_case(case_id)
    except KeyError:
        return jsonify({'error': f'Unknown case: {case_id}'}), 404
    return jsonify(graph_info(layer))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f'\nToolpath prototype running at http://localhost:{port}\n')
    app.run(debug=True, port=port)
