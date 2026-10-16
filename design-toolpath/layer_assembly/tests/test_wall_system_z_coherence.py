"""
WALL SYSTEM VERTICAL COHERENCE THROUGH Z (2026-10-16 correctness pass,
issue 6). Through the real Designer.

A circle × square building (the overlap trimmed, rounded junctions) with a
Skin + Web zigzag web; the CIRCLE alone is scaled gradually through the stack
by its transform group. Every layer is a transformed variant whose lattice
is planned against the untransformed one (cross-Z tracking: counts, sides,
seam carried).

DEFERRED BUG (open, 2026-10-16): strict xfail reproduction. Observed: the planner re-detected the run's CORNERS on
every layer; the circle (radius ≈ 2.4 thicknesses) turns close to the corner
threshold, so "corners" on it appeared / vanished from layer to layer, the
run's segments no longer matched the reference and its stations were
re-planned — the web's landings on the circle jumped by up to 16 in (web
pitch 20 in) between successive layers, a tangle instead of a stack. (A
diagnosis, not yet a decided fix: the planner re-detects the run's corners
per layer; a circle of radius ≈ 2.4 thicknesses sits near the corner
threshold.)
"""
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

CX, CY, CR = 24.0, 96.0, 19.556623995902918


def document():
    return {'id': 'design', 'label': '',
            'source_paths': [
                {'id': 'C', 'type': 'CirclePath', 'label': 'Circle', 'closed': True, 'role': 'free',
                 'visible': True, 'cx': CX, 'cy': CY, 'radius': CR,
                 'envelope': {'align': 'center', 'print_reference': False, 'thickness': 8}},
                {'id': 'R', 'type': 'RectanglePath', 'label': 'Square', 'closed': True, 'role': 'free',
                 'visible': True, 'x': 18, 'y': 12, 'w': 90, 'h': 90, 'corner_radius': 5,
                 'envelope': {'align': 'center', 'print_reference': False, 'thickness': 8}}],
            'openings': [],
            'trims': [{'id': 't1', 'source_path_id': 'R', 'start': ['C'], 'end': ['C'], 'inside': {'C': True},
                       'u_mid': 0.75},
                      {'id': 't2', 'source_path_id': 'C', 'start': ['R'], 'end': ['R'], 'inside': {'R': True},
                       'u_mid': 0.875}],
            'route_origins': [], 'region_overrides': [], 'infills': [],
            'constraints': {'start_path_id': None, 'start_t': None, 'reverse_direction': False,
                            'component_order': None},
            'corner_radius': 0, 'cap_style': 'flat', 'cap_corner_radius': 0,
            'junction_style': 'round', 'junction_radius': 7.5, 'junction_overrides': [], 'wall_relations': [],
            'material': {'bead_width': 3, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True},
            'return_paths': True, 'prefer_closed': True}


def designs(variant):
    out = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': document()}]
    if variant:                          # (a lineage: the layers print the family SCAFFOLD)
        out.append({'id': 'd1', 'name': 'Door', 'parent': 'base', 'settings': {},
                    'patch': {'openings': {'o1': {'id': 'o1', 'source_path_id': 'R', 'center_s': 336.6,
                                                  'width': 24.0, 'end_treatment': 'inherit'}}}})
    return out


SYSTEMS = [{'id': 'S1', 'name': 'Wall System 1', 'type': 'skin_web', 'params': {}, 'members': ['C', 'R'],
            'web': {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}}]


def assembly(n_layers):
    return {'layer_height': 1.5, 'sections': [{'design_id': 'base', 'height': 1.5 * n_layers}],
            'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0},
            'opening_alignment': 'follow', 'center_mode': 'multiple', 'center_counter': 1,
            'centers': [{'id': 'c1', 'x': CX, 'y': CY, 'sources': ['C']}],
            'objects': [], 'overrides': [], 'profiles': []}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')
    from app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _stack(c, variant, n_layers=20, scale_in=0.15):
    a = assembly(n_layers)
    a['sections'][0]['transform'] = {'profile': 'linear', 'shift': [0, 0], 'scale_in': scale_in}
    ds = designs(variant)
    r = c.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds, 'wall_systems': SYSTEMS}).get_json()
    vs = r.get('variants') or {}
    keys = list(dict.fromkeys(i['geometry_key'] for i in r['instances']))
    g = c.post('/api/assembly/geometry', json={'designs': ds, 'wall_systems': SYSTEMS, 'ids': ['base'],
                                               'variants': {k: vs[k] for k in keys if k in vs}}).get_json()['designs']
    return [(vs.get(i['geometry_key']), g[i['geometry_key']]['polylines']) for i in r['instances']]


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _web(polys):
    return [p for p in polys if '~web~' in (p.get('id') or '')]


def _drift(v, web, below):
    """How far each web vertex on the circle's FREE arc (outside the square,
    clear of the junction fillets) lies from the web of the layer below."""
    k, tx, ty = (v['transforms']['C'] if v and v.get('transforms', {}).get('C') else (1.0, 0.0, 0.0))
    c = (CX * k + tx, CY * k + ty)
    segs = [(a, b) for p in below for a, b in zip(p['pts'], p['pts'][1:])]
    return [min(_segd(q, a, b) for a, b in segs) for p in web for q in p['pts']
            if math.dist(q, c) < CR * k + 6 and (q[0] < 10 or q[1] > 110)]


@pytest.mark.xfail(strict=True, reason='DEFERRED: Skin + Web phase not coherent through Z on a gradually scaled circle (open bug)')
@pytest.mark.parametrize('variant', [True, False], ids=['lineage-scaffold', 'planned-alone'])
def test_web_evolves_continuously_on_a_gradually_scaled_circle(client, variant):
    """Measured 2026-10-16 on a11fcc1: max 3.18 / 3.83 in between successive
    layers (most layers > 0.5 in); the untransformed control drifts 0.155 in."""
    layers = _stack(client, variant)
    assert all(v and v.get('transforms') for v, _ in layers[1:])          # every layer a transformed variant
    d = []
    for (_, below), (v, polys) in zip(layers, layers[1:]):
        dd = _drift(v, _web(polys), _web(below))
        assert dd
        d += dd
    # the circle's radius changes ~0.08 in per layer: a coherent web moves by
    # a fraction of an inch from layer to layer, never by a stitch
    assert max(d) < 0.6, max(d)
    assert statistics.median(d) < 0.2
