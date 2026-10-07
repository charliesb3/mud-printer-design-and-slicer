"""
CONTRACT / integration: the Assembly workspace endpoints (layer_assembly/
web.py, mounted by the Designer app). Resolution stays the Assembly's
model; geometry comes through the Designer adapter.
"""
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

from layer_assembly.demo import demo_designs


@pytest.fixture
def client():
    from app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_resolve_endpoint(client):
    a = {'layer_height': 1.75, 'sections': [{'design_id': 'A', 'height': 36}, {'design_id': 'B', 'height': 24},
                                           {'design_id': 'A', 'height': 12}]}
    r = client.post('/api/assembly/resolve', json={'assembly': a, 'design_ids': ['A', 'B']}).get_json()
    assert [s['layers'] for s in r['sections']] == [21, 13, 7]
    assert [round(s['error'], 6) for s in r['sections']] == [0.75, -1.25, 0.25]
    assert r['total_layers'] == 41 and r['total_height'] == 71.75 and r['desired_height'] == 72
    assert len(r['instances']) == 41 and r['instances'][21]['design_id'] == 'B'
    assert r['instances'][-1]['z_top'] == 71.75


def test_resolve_rejects_unknown_designs_and_bad_heights(client):
    bad = client.post('/api/assembly/resolve', json={'assembly': {'layer_height': 1.5, 'sections': [
        {'design_id': 'X', 'height': 10}]}, 'design_ids': ['A']})
    assert bad.status_code == 400 and 'unknown' in bad.get_json()['error']
    bad = client.post('/api/assembly/resolve', json={'assembly': {'layer_height': 0, 'sections': []}})
    assert bad.status_code == 400


def test_geometry_endpoint(client):
    r = client.post('/api/assembly/geometry', json={'designs': demo_designs(), 'ids': ['A', 'B']}).get_json()
    assert set(r['designs']) == {'A', 'B'}
    assert r['designs']['B']['parent'] == 'A' and r['designs']['B']['name'] == 'Door Gap'
    assert r['designs']['A']['polylines'] and r['designs']['A']['bead_width'] == 3.0
    assert r['designs']['A']['polylines'] != r['designs']['B']['polylines']


def test_resolve_with_designs_reports_support_and_headers(client):
    ds = demo_designs() + [{'id': 'D', 'name': 'Closed', 'parent': 'B', 'patch': {'openings': {'door': None}}}]
    a = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 12}, {'design_id': 'B', 'height': 24},
                                           {'design_id': 'D', 'height': 12}]}
    r = client.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds}).get_json()
    f = r['support']['findings'][0]
    assert r['support']['needs_header'] == 1 and f['status'] == 'needs_header' and f['layer'] == 24
    a['objects'] = [{'kind': 'header', 'id': 'h1', 'section': f['section'], 'layer': f['local_layer'],
                     'x': f['local_centre'][0], 'y': f['local_centre'][1], 'angle': f['angle'],
                     'span': f['span'], 'bearing': 8, 'depth': 13, 'thickness': 6}]
    r = client.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds}).get_json()
    assert r['support']['supported'] == 1 and r['support']['headers'][0]['supports'] == [f['id']]
    plain = client.post('/api/assembly/resolve', json={'assembly': a}).get_json()
    assert 'support' not in plain                                 # no designs → no support analysis


def connected_designs():
    """Rect + line + circle: ONE connected printed mass (one wave infill on
    the rect reaches all three walls) + a derived design with openings."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests'))
    import multi_opening_fixtures as F
    ops = {o['id']: o for o in F.document()['openings']}
    return [{'id': 'base', 'name': 'Base', 'document': F.document(())},
            {'id': 'd1', 'name': 'Openings', 'parent': 'base', 'patch': {'openings': ops}}]


def _verts(client, ds, d):
    g = client.post('/api/assembly/geometry', json={'designs': ds, 'ids': [d]}).get_json()['designs'][d]
    return g, [(p, s) for pl in g['polylines'] for p, s in zip(pl['pts'], pl['src'])]


def test_transform_groups_inside_one_connected_mass_through_the_real_designer(client):
    ds = connected_designs()
    g, verts = _verts(client, ds, 'base')
    assert g['sources'] == {'R': 'Rect 1', 'C': 'Circle 1', 'L': 'Line 1'}
    lat = [pl for pl in g['polylines'] if pl['id'].startswith('IR')]
    in_circle = [s for pl in lat for p, s in zip(pl['pts'], pl['src']) if math.dist(p, (480, 180)) < 76]
    assert in_circle and set(in_circle) == {'C'}                           # the lattice follows ITS wall
    a = {'layer_height': 1.5, 'center_mode': 'multiple', 'centers': [],
         'sections': [{'design_id': 'base', 'height': 9}, {'design_id': 'd1', 'height': 9}],
         'transform': {'profile': 'quadratic', 'shift': [0, 0], 'scale_in': 0.5}}
    r = client.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds}).get_json()
    G = r['groups']
    assert len(G['suggestions']) == 1 and G['suggestions'][0]['sources'] == ['C', 'L', 'R']   # one mass
    assert all('parts' not in i for i in r['instances'])                   # nothing assigned yet
    a['centers'] = [{'id': 'c1', 'x': 480, 'y': 180, 'sources': ['C']},
                    {'id': 'c2', 'x': 220, 'y': 180, 'sources': ['R', 'L']}]
    r = client.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds}).get_json()
    G = r['groups']
    assert G['sources']['C']['group'] == 'c1' and G['sources']['L']['group'] == 'c2'
    assert abs(G['centres']['c1']['auto'][0] - 480) < 2                    # member bbox centre (↺ target)
    top = r['instances'][-1]
    assert set(top['parts']) == {'c1', 'c2'} and top['design_id'] == 'd1'  # openings keep the assignment
    for cid, (cx, cy) in (('c1', (480, 180)), ('c2', (220, 180))):
        t = top['parts'][cid]
        assert abs(t['scale'] * cx + t['tx'] - cx) < 1e-6                    # each about its own pivot
    assert top['parts']['c1']['scale'] < top['parts']['c2']['scale']       # the smaller circle shrinks faster
    a['centers'][0] = dict(a['centers'][0], x=500, y=170)                  # drag C1
    r2 = client.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds}).get_json()
    assert r2['groups']['group_of'] == r['groups']['group_of']             # membership unchanged
    assert r2['instances'][-1]['parts']['c2'] == top['parts']['c2']         # C2 unaffected
    t = r2['instances'][-1]['parts']['c1']
    assert abs(t['scale'] * 500 + t['tx'] - 500) < 1e-6
    assert client.post('/api/assembly/resolve', json={'assembly': a, 'designs': ds}).get_json() == r2   # deterministic
