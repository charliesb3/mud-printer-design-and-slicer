"""
AN UNASSIGNED WALL DOES NOT INHERIT ITS HOST'S TRANSFORM (2026-10-16
correctness pass, issue 3). Through the real Designer.

DEFERRED BUG (open, 2026-10-16): these tests reproduce it and are marked
strict xfail until a separate, narrow fix is designed.

A THIN vertical Out-and-Back line L starts on the bottom edge of a rectangle
R; R is in transform group c1, L is in NO group. Old rule
(semantic_transform V1 connectors, current): L's attached end follows R's
group map (foot on R mapped by R's similarity) while its free end stays — the
SIMILARITY fixing both end images rotates and scales the whole line, so the
straight wall leans further on every layer of the stack. Expected: L stays
unchanged through Z except for a local adjustment at its attached end.
"""
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

DEFERRED = pytest.mark.xfail(strict=True, reason='DEFERRED: an unassigned one-end-attached wall is rotated by its host group (open bug)')

X = 50.0             # L's line (design x)
TOP = 60.0           # L's free end


def designs():
    doc = {'id': 'design', 'label': '',
           'source_paths': [
               {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect', 'closed': True, 'role': 'free',
                'visible': True, 'x': 0, 'y': 0, 'w': 100, 'h': 100,
                'envelope': {'align': 'center', 'print_reference': False, 'thickness': 8}},
               {'id': 'L', 'type': 'LinePath', 'label': 'Line', 'closed': False, 'role': 'free',
                'visible': True, 'start': [X, 0.0], 'end': [X, TOP]}],
           'openings': [], 'trims': [], 'route_origins': [], 'region_overrides': [], 'infills': [],
           'constraints': {'start_path_id': None, 'start_t': None, 'reverse_direction': False,
                           'component_order': None},
           'corner_radius': 0, 'cap_style': 'flat', 'cap_corner_radius': 0, 'junction_style': 'miter',
           'junction_radius': 2, 'junction_overrides': [], 'wall_relations': [],
           'material': {'bead_width': 3, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True},
           'return_paths': True, 'prefer_closed': True}
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}]


def assembly(sources=('R',)):
    return {'layer_height': 1.5, 'sections': [{'design_id': 'base', 'height': 30,
                                               'transform': {'profile': 'linear', 'shift': [0, 0],
                                                             'scale_in': 0.4}}],
            'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0},
            'opening_alignment': 'follow', 'center_mode': 'multiple', 'center_counter': 1,
            # an OFF-AXIS pivot: R's bottom edge moves along AND across L's line
            'centers': [{'id': 'c1', 'x': 160, 'y': 140, 'sources': list(sources)}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')
    from app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _layers(c, envelope):
    a = assembly()
    body = {'assembly': a, 'designs': designs(), 'design_ids': ['base'], **({'envelope': True} if envelope else {})}
    r = c.post('/api/assembly/resolve', json=body).get_json()
    vs = r.get('variants') or {}
    keys = list(dict.fromkeys(i['geometry_key'] for i in r['instances']))
    g = c.post('/api/assembly/geometry', json={'designs': designs(), 'ids': ['base'],
                                               **({'envelope': True} if envelope else {}),
                                               'variants': {k: vs[k] for k in keys if k in vs}}).get_json()['designs']
    return r, vs, [g[i['geometry_key']]['polylines'] for i in r['instances']]


def _line_pts(polys):
    """L's printed / envelope vertices (per-vertex source tags)."""
    return [tuple(q) for p in polys for q, s in zip(p['pts'], p.get('src') or ()) if s == 'L']


def _segs(polys):
    out = []
    for p in polys:
        q = p['pts'] + (p['pts'][:1] if p.get('closed') else [])
        out += [(a, b) for a, b in zip(q, q[1:]) if 'L' in (p.get('src') or ())]
    return out


def _dist(p, segs):
    best = math.inf
    for a, b in segs:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best


@DEFERRED
def test_unassigned_line_keeps_its_direction(client):
    r, vs, _ = _layers(client, envelope=True)
    top = r['instances'][-1]
    v = vs[top['geometry_key']]
    assert set(v['transforms']) == {'R'}                         # only the grouped wall is moved
    (con,) = v['connectors']
    assert con['source'] == 'L'
    (s, e) = con['ends']
    assert math.isclose(s[0], X, abs_tol=1e-9) and math.isclose(e[0], X, abs_tol=1e-9)   # still vertical
    assert e == [X, TOP]                                         # the free end has not moved
    k, tx, ty = v['transforms']['R']
    assert math.isclose(s[1], ty, abs_tol=1e-9)                  # on R's transformed bottom edge (y = 0 → ty)


@DEFERRED
@pytest.mark.parametrize('envelope', [True, False])
def test_unassigned_out_and_back_is_unchanged_through_z(client, envelope):
    """L (envelope band / both printed lanes) on every layer coincides with
    layer 0 everywhere except within 6 in of its attached end — vertical,
    same position, same free end; the support rule then judges the moving
    junction like any other change."""
    r, vs, layers = _layers(client, envelope)
    ref = _segs(layers[0])
    assert ref
    xs0 = sorted({round(x, 6) for x, _ in _line_pts(layers[0])})
    assert len(r['instances']) == 20
    for k, polys in enumerate(layers[1:], 1):
        v = vs.get(r['instances'][k]['geometry_key']) or {}
        edge = (v.get('transforms', {}).get('R') or [1, 0, 0])[2]       # R's bottom edge y on this layer
        pts = [p for p in _line_pts(polys) if p[1] > max(edge, 0.0) + 6.0]
        assert pts, k
        assert max(_dist(p, ref) for p in pts) < 1e-6, k                 # identical to layer 0 there
        assert sorted({round(x, 6) for x, y in _line_pts(polys) if y > edge + 6.0}) == \
            [x for x in xs0], k                                          # the same vertical lanes / faces
