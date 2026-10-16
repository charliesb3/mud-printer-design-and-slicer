"""CHAINED LOOP CORNER + JUNCTION MOTIFS (2026-10-09).

- corners: every 90° corner of a ring (convex and reflex), of a lone L
  (doubled laps) and of a rounded rectangle gets a corner motif; the loops
  stay ½ bead inside the resolved envelope; one whole loop crest sits on the
  bisector (the outer lobe fills the outer corner on a single lap);
- LOCALITY: with the motifs off the chain is exactly the V1 chain, and with
  them on every changed point lies within the motif window of a corner /
  junction — geometry farther away is identical;
- junctions: a branch end pushed into a host ends in one eye that CROSSES the
  host's printed paths (or the canonical cap where it would not); the
  partition / branch / reference network keep one route, no travel, no
  retrace;
- material: no denser at a corner than the plain chain there;
- responsiveness: the motifs cost a bounded fraction of the generation time.
"""
import copy
import math
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app as A                                   # noqa: E402
import wall_systems as WS                         # noqa: E402

T = 10.0
BEAD = 3.0
MAT = {'bead_width': BEAD, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


def doc(paths, members=None, openings=(), **kw):
    d = {'id': 'l', 'source_paths': paths, 'offset_treatments': [], 'infills': [],
         'openings': list(openings), 'trims': [], 'material': dict(MAT),
         'wall_systems': [{'id': 'WS1', 'name': 'WS1', 'type': 'chain',
                           'members': members or [p['id'] for p in paths], 'thickness': T, 'params': {}}]}
    d.update(kw)
    return d


RECT = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True}])
LSHAPE = doc([{'id': 'P', 'type': 'ExplicitPath', 'closed': True,
               'points': [[0, 0], [200, 0], [200, 80], [100, 80], [100, 160], [0, 160]]}])
LONE_L = doc([{'id': 'P', 'type': 'ExplicitPath', 'closed': False, 'points': [[0, 0], [160, 0], [160, 120]]}])
RECT_R4 = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True,
                'corner_radius': 4}])
OPENING = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True}],
              openings=[{'id': 'o1', 'type': 'opening', 'source_path_id': 'R', 'center_s': 100, 'width': 30,
                         'end_treatment': 'inherit'}])
JUNCTIONED = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True},
                  {'id': 'L', 'type': 'LinePath', 'start': [100, 0], 'end': [100, 140]},
                  {'id': 'B', 'type': 'LinePath', 'start': [200, 70], 'end': [300, 70]}])
JUNC_ROUND = dict(copy.deepcopy(JUNCTIONED), junction_style='round', junction_radius=6)
CORNERS = {'rect': [(0, 0), (200, 0), (200, 140), (0, 140)],
           'lshape': [(0, 0), (200, 0), (200, 80), (100, 80), (100, 160), (0, 160)],
           'lone_L': [(160, 0)], 'rect_r4': [(0, 0), (200, 0), (200, 140), (0, 140)],
           'opening': [(0, 0), (200, 0), (200, 140), (0, 140)]}
FIX = {'rect': RECT, 'lshape': LSHAPE, 'lone_L': LONE_L, 'rect_r4': RECT_R4, 'opening': OPENING}
PARAMS = [{}, {'pitch': 3.0}, {'pitch': 6.0}, {'pitch': 20.0}, {'neck': 0.5}, {'loop_width': 4.0},
          {'loop_depth': 2.0}, {'phase': 0.25}, {'pitch': 30.0, 'loop_width': 40.0}]


@pytest.fixture
def client():
    A.app.config['TESTING'] = True
    A._GEOM_CACHE.clear()
    yield A.app.test_client()
    A._GEOM_CACHE.clear()


def with_params(d, prm):
    d = copy.deepcopy(d)
    for w in d['wall_systems']:
        w['params'] = dict(prm)
    return d


def post(client, url, d):
    r = client.post(url, json=d)
    j = r.get_json()
    assert r.status_code == 200 and not j.get('error'), (j.get('error') or '')[-1500:]
    return j


def motifs_off(client, monkeypatch, url, d):
    monkeypatch.setattr(WS, 'CHAIN_MOTIFS', False)
    A._GEOM_CACHE.clear()
    try:
        return post(client, url, d)
    finally:
        monkeypatch.setattr(WS, 'CHAIN_MOTIFS', True)
        A._GEOM_CACHE.clear()


def pts(r):
    return [(x, y) for p in r['printable'] for x, y in p['pts']]


def rings(r):
    return [rg for reg in r['network']['regions'] if reg.get('material') is not False
            for rg in [reg['outer']] + list(reg.get('holes') or [])]


def _inside(q, ring):
    x, y, c = q[0], q[1], False
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            c = not c
    return c


def _seg_d(q, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
    return math.hypot(a[0] + t * dx - q[0], a[1] + t * dy - q[1])


def clearance(q, rs):
    """Signed: distance to the envelope boundary, negative outside the material."""
    d = min(_seg_d(q, a, b) for rg in rs for a, b in zip(rg, rg[1:] + rg[:1]))
    inside = sum(_inside(q, rg) for rg in rs) % 2 == 1
    return d if inside else -d


def chain_runs(r):
    return [i for w in r['network']['wall_systems'] if w.get('system') == 'chain' for i in w['per_run']]


def _crossings(A_, B_):
    n = 0
    for pa in A_:
        for a, b in zip(pa, pa[1:]):
            for pb in B_:
                for c, d in zip(pb, pb[1:]):
                    t = WS._cross(a, b, c, d)
                    if t is not None and 1e-7 < t < 1 - 1e-7:
                        u = WS._cross(c, d, a, b)
                        if u is not None and 1e-7 < u < 1 - 1e-7:
                            n += 1
    return n


# --- corners ------------------------------------------------------------------------------

@pytest.mark.parametrize('name,count', [('rect', 4), ('lshape', 6), ('lone_L', 1), ('rect_r4', 4)])
def test_every_corner_gets_a_motif(client, name, count):
    r = post(client, '/api/effective_paths', FIX[name])
    assert sum(i.get('corner_motifs', 0) for i in chain_runs(r)) == count
    assert r['network']['wall_systems'][0]['chain']['corner_motifs'] == count


@pytest.mark.parametrize('name', sorted(FIX))
@pytest.mark.parametrize('prm', PARAMS, ids=[str(p) for p in PARAMS])
def test_corner_motifs_stay_inside_the_envelope(client, name, prm):
    """Every bead centreline near a corner keeps ½ bead inside the resolved
    faces (inside and outside corners, Corner R, doubled laps)."""
    r = post(client, '/api/effective_paths', with_params(FIX[name], prm))
    rs = rings(r)
    near = [q for q in pts(r) if min(math.hypot(q[0] - c[0], q[1] - c[1]) for c in CORNERS[name]) < 4 * T]
    assert near
    worst = min(clearance(q, rs) for q in near)
    assert worst >= 0.5 * BEAD - 0.06, worst


def _polylines(r):
    return [[tuple(q) for q in p['pts']] + ([tuple(p['pts'][0])] if p['closed'] else []) for p in r['printable']]


def _grid(pls, h=2.0):
    g = {}
    for pl in pls:
        for a, b in zip(pl, pl[1:]):
            for cx in range(int(min(a[0], b[0]) // h) - 1, int(max(a[0], b[0]) // h) + 2):
                for cy in range(int(min(a[1], b[1]) // h) - 1, int(max(a[1], b[1]) // h) + 2):
                    g.setdefault((cx, cy), []).append((a, b))
    return g


def _dist(q, g, h=2.0):
    return min((_seg_d(q, a, b) for a, b in g.get((int(q[0] // h), int(q[1] // h)), [])), default=9.0)


@pytest.mark.parametrize('name', sorted(FIX))
@pytest.mark.parametrize('prm', PARAMS, ids=[str(p) for p in PARAMS])
def test_motifs_are_local(client, monkeypatch, name, prm):
    """Geometry farther than the motif window (≤ CHAIN_CORNER_WINDOW pitches,
    ≥ 1.5 t, each side, + half a loop) from every corner is the plain chain's
    (within the output simplification tolerance, 0.01 in: the greedy
    simplifier may keep other vertices of the same curve)."""
    d = with_params(FIX[name], prm)
    r1 = post(client, '/api/effective_paths', d)
    r0 = motifs_off(client, monkeypatch, '/api/effective_paths', d)
    a, b = _polylines(r1), _polylines(r0)
    ga, gb = _grid(a), _grid(b)
    moved = [q for pl in a for q in pl if _dist(q, gb) > 0.02] + [q for pl in b for q in pl if _dist(q, ga) > 0.02]
    assert moved                                        # (the corners did change)
    run = max(chain_runs(r1), key=lambda i: i['pitch'])
    reach = max(1.5 * T, WS.CHAIN_CORNER_WINDOW * run['pitch']) + 0.5 * run['loop_width'] + T
    for q in moved:
        assert min(math.hypot(q[0] - c[0], q[1] - c[1]) for c in CORNERS[name]) < reach, q


def test_straight_wall_is_unchanged(client, monkeypatch):
    d = doc([{'id': 'L', 'type': 'LinePath', 'start': [0, 0], 'end': [240, 0]}])
    assert pts(post(client, '/api/effective_paths', d)) == pts(motifs_off(client, monkeypatch, '/api/effective_paths', d))


@pytest.mark.parametrize('prm', PARAMS, ids=[str(p) for p in PARAMS])
def test_ring_corner_holds_the_outer_lobe(client, monkeypatch, prm):
    """On a ring (single lap) the loop crest on each corner is the OUTER one:
    the path reaches into the outer corner — at least as close to it as the
    plain chain (whose strip frame crushed the corner loop onto the diagonal)."""
    d = with_params(RECT, prm)
    r1 = post(client, '/api/effective_paths', d)
    r0 = motifs_off(client, monkeypatch, '/api/effective_paths', d)
    for (cx, cy) in CORNERS['rect']:
        d1 = min(math.hypot(x - cx, y - cy) for x, y in pts(r1))
        d0 = min(math.hypot(x - cx, y - cy) for x, y in pts(r0))
        D = min(prm.get('loop_depth') or 3.5, 3.5)        # (a shallower loop reaches less far)
        assert d1 <= d0 + 0.05 and d1 < 0.5 * T + 1.5 * (3.5 - D), (cx, cy, d1, d0)


def test_corner_crest_on_the_bisector():
    """The timing shift puts a crest exactly on the corner (as far as the
    progress-rate bounds allow) and is monotone."""
    from model import Vec2
    rs = [[Vec2(0, 0), Vec2(200, 0), Vec2(200, 140), Vec2(0, 140)],
          [Vec2(10, 10), Vec2(10, 130), Vec2(190, 130), Vec2(190, 10)]]
    import wall_lattice as WL
    thick = WL._thickness(rs)
    sk = WL.skeleton(rs, max(0.5, min(thick / 3.0, 20.0 / 3.0)), thick)
    geo = WL._Geo(sk)
    env = WS._Envelope(rs, BEAD)
    st = WS._Strip(geo, sk.runs[0], BEAD, thick, env)
    for (sc, _, _) in WS._corners(st):
        fr = WS._CornerFrame(st, sc - 20, sc, sc + 20, 3.5, BEAD, env, 3.5)
        assert fr.ok
        for crest in (sc - 7, sc - 3, sc + 4, sc + 8):
            fr.set_crest(crest)
            assert fr.shift is not None and abs(fr._warp(crest) - sc) < 1e-9
            ws = [fr._warp(fr.sa + k * 0.1) for k in range(int((fr.sb - fr.sa) / 0.1) + 1)]
            assert all(b > a for a, b in zip(ws, ws[1:]))
            assert abs(fr.progress(crest) - fr.L1) < 1e-9


# --- junctions ----------------------------------------------------------------------------

def _by_source(r):
    by = {}
    for p in r['printable']:
        pl = [WS.Vec2(*q) for q in p['pts']] + ([WS.Vec2(*p['pts'][0])] if p['closed'] else [])
        by.setdefault(p['id'].split('~')[0], []).append(pl)
    return by


JPARAMS = [{}, {'pitch': 3.0}, {'pitch': 6.0}, {'pitch': 15.0}, {'pitch': 30.0}, {'neck': 0.5},
           {'loop_width': 4.0}, {'loop_depth': 2.0}, {'phase': 0.25}, {'phase': 0.75}]


TIPS = (('L', (100, 2.75)), ('L', (100, 137.25)), ('B', (192.75, 70)))


@pytest.mark.parametrize('d', [JUNCTIONED, JUNC_ROUND], ids=['flat', 'round'])
@pytest.mark.parametrize('prm', JPARAMS, ids=[str(p) for p in JPARAMS])
def test_junction_ends_cross_the_host(client, monkeypatch, d, prm):
    """Every branch end that touched the host's printed paths without the
    motifs still CROSSES them (eye or canonical cap); with flat (mitred)
    junctions that is every pushed end and the network prints as one route.
    Never more runs / travel / retrace than without the motifs. (Round
    junctions: the partition is not pushed at all — unchanged, V1.)"""
    dd = with_params(d, prm)
    r = post(client, '/api/route', dd)
    r0 = motifs_off(client, monkeypatch, '/api/route', dd)
    by, by0 = _by_source(r), _by_source(r0)
    for src, tip in TIPS:
        had = WS.contact_near(by0[src], [pl for k, v in by0.items() if k != src for pl in v], WS.Vec2(*tip), 18.0)
        has = WS.contact_near(by[src], [pl for k, v in by.items() if k != src for pl in v], WS.Vec2(*tip), 18.0)
        assert has or not had, (src, tip)
        if d is JUNCTIONED:
            assert has, (src, tip)
    mt, m0 = r['metrics'], r0['metrics']
    assert mt['print_runs'] <= m0['print_runs'] and mt['retrace_distance'] == 0
    assert mt['travel_distance'] <= 1.05 * m0['travel_distance'] + 1.0
    if d is JUNCTIONED:
        assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and not r['closure']['open']


def test_junction_eye_is_used(client):
    """The default T / partition ends are eyes (not the face-lane leads + cap)."""
    r = post(client, '/api/effective_paths', JUNCTIONED)
    reps = {tuple(w['sources']): w for w in r['network']['wall_systems']}
    eyes = {k: sum(i.get('junction_motifs', 0) for i in w['per_run']) for k, w in reps.items()}
    assert eyes[('B',)] == 1 and eyes[('L',)] == 2 and eyes[('R',)] == 0


def test_eye_without_contact_falls_back_to_the_cap(client, monkeypatch):
    """An eye that would not cross the host is replaced by the canonical cap
    (contact is never traded for the motif)."""
    real = WS.contact_near
    calls = []

    def never(polys, others, tip, radius):
        calls.append(tip)
        return False
    monkeypatch.setattr(WS, 'contact_near', never)
    r = post(client, '/api/effective_paths', JUNCTIONED)
    monkeypatch.setattr(WS, 'contact_near', real)
    assert calls
    for w in r['network']['wall_systems']:
        assert sum(i.get('junction_motifs', 0) for i in w['per_run']) == 0
        if w['sources'] != ['R']:
            assert w.get('junction_motif_fallbacks')


@pytest.mark.parametrize('prm', [{}, {'pitch': 6.0}, {'pitch': 20.0}, {'phase': 0.25}])
def test_reference_network_all_chain(client, prm):
    """The circle + rectangles + Line 1 reference network, all Chained Loop,
    mitred junctions: one route, no travel, no retrace; Line 1 crosses the
    host at both ends."""
    import reference_network as RN
    d = RN.document('zigzag')
    d['infills'] = []
    d['material'] = dict(MAT)
    d['wall_systems'] = [
        {'id': 'H', 'name': 'Host', 'type': 'chain', 'thickness': 10, 'align': 'auto', 'params': dict(prm),
         'members': ['R1', 'R2', 'C1']},
        {'id': 'B', 'name': 'Line', 'type': 'chain', 'thickness': 10, 'align': 'auto', 'params': dict(prm),
         'members': ['L1']}]
    r = post(client, '/api/route', d)
    mt = r['metrics']
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and mt['retrace_distance'] == 0, mt
    by = _by_source(r)
    assert _crossings(by['L1'], by['R2'] + by['C1']) >= 2


@pytest.mark.parametrize('prm', [{}, {'pitch': 6.0}])
def test_reference_network_round_junctions_no_worse(client, monkeypatch, prm):
    """Round junctions (Junction R 6): the bands are not pushed there (V1, as
    before) — the motifs change nothing about runs / travel / retrace."""
    import reference_network as RN
    d = RN.document('zigzag')
    d['infills'] = []
    d['material'] = dict(MAT)
    d['wall_systems'] = [
        {'id': 'H', 'name': 'Host', 'type': 'chain', 'thickness': 10, 'align': 'auto', 'params': dict(prm),
         'members': ['R1', 'R2', 'C1']},
        {'id': 'B', 'name': 'Line', 'type': 'chain', 'thickness': 10, 'align': 'auto', 'params': dict(prm),
         'members': ['L1']}]
    d.update(junction_style='round', junction_radius=6)
    mt = post(client, '/api/route', d)['metrics']
    m0 = motifs_off(client, monkeypatch, '/api/route', d)['metrics']
    assert mt['print_runs'] <= m0['print_runs'] and mt['retrace_distance'] == 0
    assert mt['travel_distance'] <= 1.05 * m0['travel_distance'] + 1.0


@pytest.mark.parametrize('name', ['rect', 'lshape', 'lone_L', 'opening'])
@pytest.mark.parametrize('prm', [{}, {'pitch': 3.0}, {'pitch': 20.0}, {'phase': 0.25}])
def test_corner_motifs_keep_one_closed_route(client, name, prm):
    r = post(client, '/api/route', with_params(FIX[name], prm))
    mt = r['metrics']
    assert mt['retrace_distance'] == 0 and not r['closure']['open']
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0


# --- material and cost --------------------------------------------------------------------

def _deposit(pls, box):
    x0, y0, x1, y1 = box
    L = 0.0
    for pl in pls:
        for (ax, ay), (bx, by) in zip(pl, pl[1:]):
            n = max(1, int(math.hypot(bx - ax, by - ay) / 0.1))
            for k in range(n):
                t = (k + 0.5) / n
                x, y = ax + (bx - ax) * t, ay + (by - ay) * t
                if x0 <= x <= x1 and y0 <= y <= y1:
                    L += math.hypot(bx - ax, by - ay) / n
    return L


@pytest.mark.parametrize('prm', [{}, {'pitch': 3.0}, {'pitch': 6.0}, {'pitch': 20.0}, {'neck': 0.5}])
def test_no_mud_buildup_at_corners(client, monkeypatch, prm):
    """Deposited bead area in each corner square stays near the plain chain's
    (+15 %) or, where the plain chain left the corner nearly empty (large
    pitch), below 1.6 × a straight stretch's — the outer lobe fills the corner,
    nothing piles up."""
    d = with_params(RECT, prm)
    r1 = post(client, '/api/effective_paths', d)
    r0 = motifs_off(client, monkeypatch, '/api/effective_paths', d)
    pl1 = [p['pts'] + [p['pts'][0]] for p in r1['printable']]
    pl0 = [p['pts'] + [p['pts'][0]] for p in r0['printable']]
    straight = _deposit(pl1, (90, 0, 100, 10)) / 100.0
    for (cx, cy) in CORNERS['rect']:
        box = (cx - 10 if cx else 0, cy - 10 if cy else 0, cx + 10 if not cx else cx, cy + 10 if not cy else cy)
        a1, a0 = _deposit(pl1, box) / 100.0, _deposit(pl0, box) / 100.0
        assert a1 <= max(1.15 * a0, 1.6 * straight) + 0.05, ((cx, cy), a1, a0, straight)


def test_motif_cost_is_bounded(client, monkeypatch):
    """The motifs add a bounded share to generating a cornered, junctioned
    chain at a fine pitch (the responsiveness work stays intact)."""
    d = with_params(JUNCTIONED, {'pitch': 1.0})

    def best(f, n=3):
        ts = []
        for _ in range(n):
            A._GEOM_CACHE.clear()
            t0 = time.perf_counter()
            f()
            ts.append(time.perf_counter() - t0)
        return min(ts)
    t_on = best(lambda: post(client, '/api/effective_paths', d))
    monkeypatch.setattr(WS, 'CHAIN_MOTIFS', False)
    t_off = best(lambda: post(client, '/api/effective_paths', d))
    monkeypatch.setattr(WS, 'CHAIN_MOTIFS', True)
    assert t_on <= 1.35 * t_off + 0.1, (t_on, t_off)
