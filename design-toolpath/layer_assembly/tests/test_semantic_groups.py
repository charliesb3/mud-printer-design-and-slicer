"""
CONTRACT: transform groups are SEMANTIC (2026-10-06). A grouped layer asks
the Designer for its design with the groups' source transforms applied
BEFORE resolution (LayerSource.geometry(design, transforms)); a wall joining
two differently moved hosts is regenerated, and support / header analysis
runs on that regenerated geometry. Through the real Designer.
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests')))

import pytest

import multi_opening_fixtures as F

CUTS = {o['id']: o for o in F.document()['openings']}          # o2: 40 in on the rect at x 200…240, y 100


def designs():
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': F.document(())},
            {'id': 'cuts', 'name': 'Cuts', 'parent': 'base', 'patch': {'openings': CUTS}, 'settings': {}}]


def assembly(sections, max_overhang=1.5):
    return {'layer_height': 1.5, 'sections': sections, 'max_overhang': max_overhang,
            'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0.5},      # assembly-wide scale
            'center_mode': 'multiple', 'center_counter': 2,
            'centers': [{'id': 'c1', 'x': 480, 'y': 180, 'sources': ['C']},
                        {'id': 'c2', 'x': 220, 'y': 180, 'sources': ['R']}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')         # in-process (the pool is tested separately)
    from app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def post(c, a):
    r = c.post('/api/assembly/resolve', json={'assembly': a, 'designs': designs()})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def geometry(c, r, key):
    v = r['variants'][key]
    g = c.post('/api/assembly/geometry', json={'designs': designs(),
                                               'variants': {key: {'design_id': v['design_id'],
                                                                  'transforms': v['transforms']}}}).get_json()
    return g['designs'][key]['polylines']


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def test_grouped_layers_print_a_designer_resolved_variant_with_a_regenerated_connector(client):
    r = post(client, assembly([{'design_id': 'base', 'height': 12}]))
    top = r['instances'][-1]
    assert '|' in top['geometry_key'] and top['geometry_key'] in r['variants']
    v = r['variants'][top['geometry_key']]
    assert set(v['transforms']) == {'C', 'R'}                       # ONLY the grouped sources move
    (con,) = v['connectors']
    assert con['source'] == 'L' and con['hosts'] == ['R', 'C']      # L: attached to both, regenerated
    polys = geometry(client, r, top['geometry_key'])
    faces = [p for p in polys if p['id'].startswith('L.wall')]
    ys = sorted({round(y, 9) for p in faces for _, y in p['pts']})
    assert len(faces) == 2 and len(ys) == 2 and math.isclose(ys[1] - ys[0], 10.0)   # a 10 in wall, not a fan
    (x0, x1) = sorted(round(e[0], 6) for e in con['ends'])
    assert all(min(x for x, _ in p['pts']) <= x0 + 1e-6 and max(x for x, _ in p['pts']) >= x1 - 5.1 for p in faces)
    assert r['instances'][0]['geometry_key'] == 'base'              # layer 0: nothing moved yet


def test_a_derived_design_keeps_its_groups_and_its_openings(client):
    r = post(client, assembly([{'design_id': 'base', 'height': 6}, {'design_id': 'cuts', 'height': 6}]))
    top = r['instances'][-1]
    assert top['design_id'] == 'cuts' and top['geometry_key'].startswith('cuts|')
    v = r['variants'][top['geometry_key']]
    assert set(v['transforms']) == {'C', 'R'} and [c['source'] for c in v['connectors']] == ['L']
    polys = geometry(client, r, top['geometry_key'])
    assert any(p['id'].startswith('IR') for p in polys)            # its lattice, resolved for the variant


def test_support_and_headers_use_the_regenerated_geometry(client):
    r = post(client, assembly([{'design_id': 'base', 'height': 6}, {'design_id': 'cuts', 'height': 6},
                               {'design_id': 'base', 'height': 6}]))
    sup = r['support']
    # every unsupported run lies ON the placed variant of its own layer
    cache = {}
    for f in sup['findings'][:6]:
        inst = r['instances'][f['layer']]
        k = inst['geometry_key']
        if k not in cache:
            cache[k] = geometry(client, r, k) if k in r['variants'] else None
        t = inst['transform']
        segs = [((t['scale'] * a[0] + t['tx'], t['scale'] * a[1] + t['ty']),
                 (t['scale'] * b[0] + t['tx'], t['scale'] * b[1] + t['ty']))
                for p in cache[k] for a, b in zip(p['pts'], p['pts'][1:] + (p['pts'][:1] if p['closed'] else []))]
        assert all(any(_segd(q, *s) < 1e-6 for s in segs) for run in f['runs'] for q in run[:3])
    # the closing of the rect's doorway (o2 at x 200…240 on y 100) needs a header,
    # found on the transformed rectangle; its local centre maps back to the design
    hdr = [f for f in sup['findings'] if f['kind'] == 'bridge' and f['layer'] == 8 and
           abs(f['local_centre'][0] - 220) < 3 and abs(f['local_centre'][1] - 100) < 8]
    assert hdr, [(f['kind'], f['layer'], f['local_centre']) for f in sup['findings'] if f['kind'] == 'bridge']


def test_changing_max_overhang_reuses_the_variants(client):
    secs = [{'design_id': 'base', 'height': 12}]
    a = post(client, assembly(secs))
    t = time.time()
    b = post(client, assembly(secs, max_overhang=2.5))
    assert time.time() - t < 2.0                                    # no Designer rebuild
    assert [i['geometry_key'] for i in a['instances']] == [i['geometry_key'] for i in b['instances']]


def test_variants_resolve_in_worker_processes(monkeypatch):
    """prefetch resolves several missing variants in parallel processes:
    the same geometry as in-process."""
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '2')
    from layer_assembly.designer_source import DesignerSource
    tfs = [{'C': (0.9 - 0.05 * k, 48.0 + 24 * k, 18.0 + 9 * k)} for k in range(3)]
    par = DesignerSource.from_designs(designs())
    par.prefetch([('base', t) for t in tfs])
    seq = DesignerSource.from_designs(designs())
    for t in tfs:
        assert par.geometry('base', t).polylines == seq.geometry('base', t).polylines


def test_zeroing_every_transform_restores_the_canonical_design_exactly(client):
    """IDENTITY IS EXACT: groups assigned, strong transforms, then all
    transforms back to zero → every layer prints the canonical design (no
    variant, same geometry and source tags as the design itself), and the
    support report equals that of the same stack without groups."""
    secs = [{'design_id': 'base', 'height': 6}, {'design_id': 'cuts', 'height': 6}]
    moved = post(client, dict(assembly(secs), sections=[dict(s_, transform={'profile': 'quadratic',
                                                                         'shift': [0.5, 0], 'scale_in': 0.4})
                                                     for s_ in secs]))
    assert any('|' in i['geometry_key'] for i in moved['instances'])
    zero = dict(assembly(secs), transform={'profile': 'linear', 'shift': [0, 0], 'scale_in': 0})
    r = post(client, zero)
    assert [i['geometry_key'] for i in r['instances']] == [i['design_id'] for i in r['instances']]
    assert not r.get('variants') and all(i['transform'] == {'scale': 1, 'tx': 0, 'ty': 0} for i in r['instances'])
    plain = post(client, dict(zero, center_mode='footprint', centers=[]))
    assert r['support']['findings'] == plain['support']['findings']
    g = client.post('/api/assembly/geometry', json={'designs': designs(), 'ids': ['base', 'cuts']}).get_json()
    from layer_assembly.designer_source import DesignerSource
    src = DesignerSource.from_designs(designs())
    for d in ('base', 'cuts'):
        assert g['designs'][d]['polylines'] == src.geometry(d).polylines
        assert src.geometry(d, {'C': (1.0, 0.0, 0.0), 'R': (1 + 1e-12, 1e-9, 0.0)}).polylines == \
            src.geometry(d).polylines                                  # numeric noise = identity


def test_a_strongly_scaled_stack_keeps_its_lattice_registered(client):
    """CROSS-Z: 32 layers, two groups scaling about their own centres. The
    lattice keeps the reference's stations through Z, so no layer is left
    with lattice spanning nothing below because the planner re-phased (the
    only overlap findings are mild shape changes at the rectangle's corners)."""
    a = assembly([{'design_id': 'base', 'height': 48}])
    a['transform'] = {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0.6}
    r = post(client, a)
    f = r['support']['findings']
    assert all(x['kind'] == 'overlap' for x in f)
    assert min((x['overlap'] for x in f), default=3.0) > 0.9
