"""
REFERENCE NETWORK through the Assembly (stabilization, 2026-10-06): Circle 1
→ C1, Rect 1 → C2, Rect 2 → C3, Line 1 unassigned, groups scaling
independently through Z, Base then the derived "gaps" design. Through the
real Designer. The support checker is a TEST of the geometry: nothing is
suppressed at junctions — these tests pin that every grouped layer is the
Designer's closed, regenerated geometry and that findings sit on it.
"""
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto')))

import pytest

import reference_network as RN


def designs():
    base = RN.document('wave')
    base['junction_style'], base['junction_radius'] = 'round', 6
    cut = {'rect1_top': dict(zip(('source_path_id', 'center_s', 'width'), RN.OPENINGS['rect1_top']))}
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': base},
            {'id': 'gaps', 'name': 'gaps', 'parent': 'base', 'patch': {'openings': cut}, 'settings': {}}]


def assembly(layers=16, scale_in=0.6):
    h = layers * 1.5
    return {'layer_height': 1.5, 'max_overhang': 1.5,
            'sections': [{'design_id': 'base', 'height': h / 2}, {'design_id': 'gaps', 'height': h / 2}],
            'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': scale_in},
            'center_mode': 'multiple', 'center_counter': 3,
            'centers': [{'id': 'c1', 'x': RN.CX, 'y': RN.CY, 'sources': ['C1']},
                        {'id': 'c2', 'x': 200, 'y': 170, 'sources': ['R1']},
                        {'id': 'c3', 'x': 90, 'y': 260, 'sources': ['R2']}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')
    from app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def test_every_grouped_layer_is_closed_regenerated_geometry(client):
    r = client.post('/api/assembly/resolve', json={'assembly': assembly(), 'designs': designs()}).get_json()
    assert r['variants'] and all(set(v['transforms']) <= {'C1', 'R1', 'R2'} for v in r['variants'].values())
    # Line 1 is an explicit connector: regenerated between its moved hosts
    assert any(c['source'] == 'L1' for v in r['variants'].values() for c in v['connectors'])
    # the forms that merely intersect are DERIVED junctions, reported per variant
    assert all({tuple(j['sources']) for j in v['junctions']} == {('C1', 'R1'), ('R1', 'R2')}
               for v in r['variants'].values())
    from layer_design import DesignLibrary
    from app import _deserialise_layer
    from graph import build_graph, closure_report
    lib = DesignLibrary(designs())
    for v in r['variants'].values():
        tf = {s: tuple(t) for s, t in v['transforms'].items()}
        lay = _deserialise_layer(lib.transformed(v['design_id'], tf)[0])
        lay.lattice_reference = lib.lattice_reference(v['design_id'], tf) or None
        p, m = lay._build_effective()
        c = closure_report(build_graph(lay.to_routing_layer(p, m)))
        assert not c['open'] and c['components'] == 1, v['design_id']
    # support findings lie ON the placed regenerated geometry of their layer
    geo = {}
    for f in r['support']['findings'][:8]:
        inst = r['instances'][f['layer']]
        k = inst['geometry_key']
        if k not in geo:
            v = r['variants'].get(k)
            body = ({'variants': {k: {'design_id': v['design_id'], 'transforms': v['transforms']}}} if v
                    else {'ids': [k]})
            geo[k] = client.post('/api/assembly/geometry', json={'designs': designs(), **body}).get_json()['designs'][k]['polylines']
        t = inst['transform']
        segs = [((t['scale'] * a[0] + t['tx'], t['scale'] * a[1] + t['ty']),
                 (t['scale'] * b[0] + t['tx'], t['scale'] * b[1] + t['ty']))
                for pl in geo[k] for a, b in zip(pl['pts'], pl['pts'][1:] + (pl['pts'][:1] if pl['closed'] else []))]
        assert all(any(_segd(q, *s) < 1e-6 for s in segs) for run in f['runs'] for q in run[:3])
