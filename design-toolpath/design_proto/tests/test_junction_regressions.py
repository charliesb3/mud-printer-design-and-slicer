"""CONNECTED-WALL ROUTING REGRESSIONS (2026-10-09, manual review).

1. A straight single-bead line drawn THROUGH a generated wall (outside →
   room → outside) split the route: its exterior stubs are joined by the host
   (extend_through) AND the skin side emitted a second `~cross` bead at the
   same mouths — its ends landed on the joined run's vertices as odd nodes →
   open component, extra runs, travel. Now one closed route, no travel.
2. A circle grazing a rectangle (bands overlapping by less than ~a bead):
   each generated pattern keeps ½ bead inside its OWN faces, so the walls
   printed no crossing — a physical gap and a separate run although the
   network calls them connected. Now a CONTACT STAPLE bonds them (crossings,
   one route); walls whose paths already cross get none.
3. Float noise: strand ends a few 1e-5 in apart (a ring closing after a cut)
   were two graph nodes → a 0.00002 in "travel" splitting the extrusion.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app as A                                   # noqa: E402
import reference_network as RN                    # noqa: E402

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
GENERATED = ['interleaved', 'linked', 'chain', 'parallel']


def _params(t):
    return {'paths': 4} if t in ('interleaved', 'linked') else ({'walls': 4} if t == 'parallel' else {})


def doc(paths, systems):
    return {'id': 'l', 'source_paths': paths, 'offset_treatments': [], 'infills': [],
            'openings': [], 'trims': [], 'material': dict(MAT), 'wall_systems': systems}


def ws(i, t, members):
    return {'id': i, 'name': i, 'type': t, 'members': members, 'thickness': 10, 'align': 'auto',
            'params': _params(t), 'web': {}}


@pytest.fixture
def client():
    A.app.config['TESTING'] = True
    A._GEOM_CACHE.clear()
    return A.app.test_client()


def route(client, d):
    r = client.post('/api/route', json=d).get_json()
    assert not r.get('error'), r.get('error', '')[-1500:]
    return r


def by_source(r):
    out = {}
    for p in r['printable']:
        out.setdefault(p['id'].split('~')[0].split('.')[0].split('_')[0], []).append(
            p['pts'] + ([p['pts'][0]] if p['closed'] else []))
    return out


def crossings(A_, B_):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    n = 0
    for pa in A_:
        for a, b in zip(pa, pa[1:]):
            for pb in B_:
                for c, d in zip(pb, pb[1:]):
                    if max(a[0], b[0]) < min(c[0], d[0]) or min(a[0], b[0]) > max(c[0], d[0]):
                        continue
                    if o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0:
                        n += 1
    return n


RECT = {'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True}


# --- 1. a line through a generated wall ---------------------------------------------------

@pytest.mark.parametrize('host', GENERATED + ['skin_web'])
@pytest.mark.parametrize('ends', [((100, -40), (100, 180)), ((-40, 70), (240, 70)), ((30, -30), (170, 170))],
                         ids=['vertical', 'horizontal', 'diagonal'])
def test_line_through_a_generated_wall_is_one_route(client, host, ends):
    line = {'id': 'L', 'type': 'LinePath', 'start': list(ends[0]), 'end': list(ends[1])}
    d = doc([RECT, line], [ws('W', host, ['R'])])
    if host == 'skin_web':
        d['wall_systems'][0]['web'] = {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}
    r = route(client, d)
    m = r['metrics']
    assert m['print_runs'] == 1 and m['travel_distance'] == 0 and m['retrace_distance'] == 0, m
    assert not r['closure']['open']
    assert not any('~cross' in p['id'] and abs(p['pts'][0][1]) < 1e-6 and abs(p['pts'][0][0] - 100) < 3
                   for p in r['printable'] if ends[0][0] == 100)


@pytest.mark.parametrize('x', [120, 200, 260])
def test_line_through_the_reference_network(client, x):
    """The manual case: an Interleaved Waves network (both rectangles, the
    circle, Line 1) and a new straight line across it."""
    d = RN.document('zigzag')
    d['infills'] = []
    d['material'] = dict(MAT)
    d['wall_systems'] = [ws('H', 'interleaved', ['R1', 'R2', 'C1', 'L1'])]
    d['source_paths'].append({'id': 'p9', 'type': 'LinePath', 'start': [x, 60], 'end': [x, 340]})
    m = route(client, d)['metrics']
    assert m['print_runs'] == 1 and m['travel_distance'] == 0, m


def test_a_line_bridging_two_walls_still_gets_its_crossing_beads(client):
    """The interior piece of a crossing line (its faces on two rings) keeps
    its `~cross` beads at the inner faces — only the doubled outer mouths go."""
    line = {'id': 'L', 'type': 'LinePath', 'start': [100, -40], 'end': [100, 180]}
    r = route(client, doc([RECT, line], [ws('W', 'interleaved', ['R'])]))
    cross = [p for p in r['printable'] if '~cross' in p['id']]
    assert cross and all(min(abs(q[1] - 10), abs(q[1] - 130)) < 1e-6 for p in cross for q in (p['pts'][0], p['pts'][-1]))


# --- 2. walls that just touch ---------------------------------------------------------------

def _circle_rect(cx, kind):
    circ = {'id': 'C', 'type': 'CirclePath', 'cx': cx, 'cy': 70, 'radius': 80, 'closed': True}
    return doc([RECT, circ], [ws('W', kind, ['R', 'C'])])


@pytest.mark.parametrize('kind', ['interleaved', 'chain', 'parallel', 'linked'])
@pytest.mark.parametrize('cx', [277.0, 278.0, 279.0, 279.5, 279.9])
def test_grazing_walls_are_bonded(client, kind, cx):
    """Bands overlapping by 0.1 … 3 in: the walls' printed paths cross (real
    contact) and print as one route."""
    r = route(client, _circle_rect(cx, kind))
    b = by_source(r)
    assert crossings(b['R'], b['C']) >= 2
    m = r['metrics']
    assert m['print_runs'] == 1 and m['travel_distance'] == 0 and m['retrace_distance'] == 0, m
    staples = [s for w in r['network']['wall_systems'] for s in w.get('contact_staples', [])]
    assert len(staples) == 1


@pytest.mark.parametrize('kind', ['interleaved', 'chain', 'parallel'])
def test_crossing_walls_get_no_staple(client, kind):
    """Deeper overlaps already cross: nothing is added."""
    r = route(client, _circle_rect(265.0, kind))
    assert not any(w.get('contact_staples') for w in r['network']['wall_systems'])
    assert r['metrics']['print_runs'] == 1


def test_staple_is_small_and_inside_the_material(client):
    r = route(client, _circle_rect(278.0, 'interleaved'))
    rings = [rg for reg in r['network']['regions'] for rg in [reg['outer']] + list(reg.get('holes') or [])]
    loops = [p for p in r['printable'] if len(p['pts']) < 40 and p['closed']]
    assert loops
    st = min(loops, key=lambda p: len(p['pts']))
    xs = [q[0] for q in st['pts']]
    assert max(xs) - min(xs) < 25                  # local: a staple, not a new wall

    def inside(q):
        c = False
        for rg in rings:
            for (ax, ay), (bx, by) in zip(rg, rg[1:] + rg[:1]):
                if (ay > q[1]) != (by > q[1]) and q[0] < ax + (q[1] - ay) * (bx - ax) / (by - ay):
                    c = not c
        return c
    assert all(inside(q) for q in st['pts'])


def test_separate_walls_are_not_stapled(client):
    r = route(client, _circle_rect(290.0, 'interleaved'))
    assert not any(w.get('contact_staples') for w in r['network']['wall_systems'])


# --- 3. float noise at strand ends ------------------------------------------------------

def test_near_coincident_strand_ends_are_one_node():
    import model as M
    from geometry import Strand, Vec2
    s = Strand('a', 'free', [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10), Vec2(2e-5, 1e-5)], False)
    t = Strand('b', 'free', [Vec2(20, 0), Vec2(30, 0)], False)
    u = Strand('c', 'free', [Vec2(30.00004, 0.0), Vec2(40, 0)], False)
    M._snap_strand_ends([s, t, u], Vec2)
    assert s.points[-1] == s.points[0]                 # its own ends: a closed loop
    assert u.points[0] == t.points[-1]                 # two strands meeting
    far = Strand('d', 'free', [Vec2(50, 0), Vec2(60, 0), Vec2(50.01, 0.0)], False)
    M._snap_strand_ends([far], Vec2)
    assert far.points[-1] != far.points[0]             # 0.01 in: real geometry, untouched
