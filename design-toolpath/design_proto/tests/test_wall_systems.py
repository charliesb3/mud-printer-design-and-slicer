"""
WALL SYSTEMS V1 (2026-10-07; wall_systems.py): a wall's envelope (Wall
Thickness) filled by Skin + Web (default, unchanged) or by Interleaved Waves,
Linked Waves or a Chained Loop — no separate skins. Paths follow the wall
in wall-relative coordinates; openings cut them; every bead stays inside
the envelope; the Minimum effective thickness is reported.
"""
import math
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer                  # noqa: E402
import network as N                                # noqa: E402
import physical_fixtures as PF                     # noqa: E402
import wall_systems as WS                          # noqa: E402
from infill import _Region                         # noqa: E402
from layer_design import DesignLibrary             # noqa: E402
from model import (Vec2, PrintLayer, RectanglePath, LinePath, QuadBezierPath, ExplicitPath,   # noqa: E402
                   WallSpec, NetworkWall, Opening, RegionInfill)

BEAD = 3.0
T = 12.0
SYSTEMS = [{'type': 'interleaved'}, {'type': 'interleaved', 'pattern': 'zigzag', 'paths': 4},
           {'type': 'linked'}, {'type': 'linked', 'pattern': 'zigzag', 'paths': 4}, {'type': 'chain'}]
IDS = ['interleaved-wave', 'interleaved-zigzag4', 'linked-wave', 'linked-zigzag4', 'chain']


def make(kind, system, openings=(), t=T):
    if kind == 'line':
        P = LinePath(Vec2(40, 200), Vec2(320, 200), id='W')
    elif kind == 'curve':
        P = QuadBezierPath(Vec2(40, 120), Vec2(200, 300), Vec2(360, 120), id='W')
    elif kind == 'corner':
        P = ExplicitPath([Vec2(60, 100), Vec2(220, 100), Vec2(220, 260)], closed=False, id='W')
    else:
        P = RectanglePath(100, 140, 200, 120, id='W')
    P.wall = WallSpec(t, system=system)
    L = PrintLayer('t', source_paths=[P])
    L.openings = [Opening(f'o{k}', 'W', c, w) for k, (c, w) in enumerate(openings)]
    return PF._phys(L)


def build(L):
    paths, meta = L._build_effective()
    return paths, meta


def system_paths(paths):
    return [p for p in paths if getattr(p, 'treatment_id', None) == 'wall_system_path']


def inside_envelope(meta, polys, clearance):
    rings = [r for reg in meta['regions'] for r in reg]
    region = _Region(rings, 4.0)
    for pl in polys:
        for q in pl:
            if not region.inside(q):
                return False, q
            d = min(N.dist_to_polyline(q, r, True) for r in rings)
            if d < clearance:
                return False, (q, d)
    return True, None


@pytest.mark.parametrize('system', SYSTEMS, ids=IDS)
@pytest.mark.parametrize('kind', ['line', 'curve', 'corner'])
def test_paths_fill_the_envelope_without_skins(kind, system):
    paths, meta = build(make(kind, system))
    ws = meta['network']['wall_systems']
    assert len(ws) == 1 and ws[0]['status'] == 'ok' and ws[0]['system'] == system['type']
    sp = system_paths(paths)
    # a wall with two free ends: the strands are turned through its end caps
    # and joined — ONE path (closed for an even count / the chain)
    assert len(sp) == 1
    closable = system['type'] == 'chain' or system.get('paths', 3) % 2 == 0
    assert sp[0].closed == closable and ws[0]['closable'] == closable
    # no skins / caps of this wall are printed
    assert not [p for p in paths if p is not None and getattr(p, 'treatment_id', None) not in ('wall_system_path',)]
    # every bead centreline half a bead inside the envelope (edge inside)
    ok, bad = inside_envelope(meta, [p.sample_points() for p in sp], 0.5 * BEAD - 0.05)
    assert ok, bad
    # the minimum effective thickness: a real cross-section, bead included
    m = ws[0]['min_effective_thickness']
    assert BEAD - 1e-6 <= m <= ws[0]['envelope'] + 1e-6


def _big_ring(system):
    P = RectanglePath(0, 0, 600, 200, id='W')
    P.wall = WallSpec(T, system=system)
    return PF._phys(PrintLayer('t', source_paths=[P]))


def test_interleaved_paths_are_phase_offset_and_reach_both_faces():
    # (a ring: no free ends, the strands stay separate closed loops; the
    # bottom wall's centre line is y = 194)
    paths, meta = build(_big_ring({'type': 'interleaved', 'paths': 3, 'period': 30}))
    sp = [[Vec2(q.x, q.y - 194 + 200) for q in p.sample_points() if q.y > 150 and 150 < q.x < 450]
          for p in system_paths(paths)]
    assert len(sp) == 3
    H = 0.5 * T - 0.5 * BEAD
    for pl in sp:
        ys = [q.y - 200 for q in pl]
        assert max(ys) > 0.95 * H and min(ys) < -0.95 * H          # each path full depth
    # same period, phases a third apart: the crest positions are shifted by 10 in
    crest = [min((q for q in pl if 200 < q.x < 240), key=lambda q: q.y).x for pl in sp]
    gaps = sorted((b - a) % 30 for a, b in zip(crest, crest[1:] + crest[:1]))
    assert all(abs(g - 10) < 1.0 or abs(g - 20) < 1.0 for g in gaps)
    assert meta['network']['wall_systems'][0]['min_effective_thickness'] > 0.7 * T


def test_linked_neighbours_meet_at_their_extrema():
    paths, meta = build(make('line', {'type': 'linked', 'paths': 3, 'period': 24}))
    rep = meta['network']['wall_systems'][0]
    assert abs(rep['closest_approach']) < 1e-6                    # auto amplitude: centrelines meet
    sp = sorted((p.sample_points() for p in system_paths(paths)), key=lambda pl: sum(q.y for q in pl))
    for a, b in zip(sp, sp[1:]):
        dmin = min(min(q.dist(r) for r in b if abs(r.x - q.x) < 2) for q in a if 80 < q.x < 280)
        assert dmin < 0.6                                         # they touch (sampling tolerance)
    # more amplitude interlocks deeper (neighbours cross)
    _, meta2 = build(make('line', {'type': 'linked', 'paths': 3, 'period': 24, 'amplitude': 2.5}))
    assert meta2['network']['wall_systems'][0]['closest_approach'] < 0


@pytest.mark.parametrize('system', SYSTEMS, ids=IDS)
def test_an_opening_cuts_the_wall_system(system):
    paths, meta = build(make('rect', system, openings=[(100.0, 30.0)]))
    sp = system_paths(paths)
    assert sp and meta['network']['wall_systems'][0]['status'] == 'ok'
    # nothing printed across the doorway (top wall, x 185 … 215)
    for p in sp:
        assert not [q for q in p.sample_points() if 187 < q.x < 213 and q.y < 152]
    ok, bad = inside_envelope(meta, [p.sample_points() for p in sp], 0.5 * BEAD - 0.05)
    assert ok, bad
    # a closed ring without an opening: closed paths
    paths2, _ = build(make('rect', system))
    assert all(p.closed for p in system_paths(paths2))


def test_skin_web_is_unchanged():
    def sig(L):
        return sorted((getattr(p, 'treatment_id', ''), tuple((round(q.x, 6), round(q.y, 6)) for q in p.sample_points()))
                      for p in build(L)[0])
    a = make('rect', None)
    a.infills = [RegionInfill('I', 'W', 'zigzag', {'spacing': 20})]
    b = make('rect', {'type': 'skin_web'})
    b.infills = [RegionInfill('I', 'W', 'zigzag', {'spacing': 20})]
    assert sig(a) == sig(b)


def test_a_wall_infill_is_not_printed_on_a_wall_system():
    L = make('rect', {'type': 'interleaved'})
    L.infills = [RegionInfill('I', 'W', 'zigzag', {'spacing': 20})]
    paths, meta = build(L)
    assert not [p for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    info = next(i for i in meta['network']['infills'] if i['id'] == 'I')
    assert info['status'] == 'wall system'


def test_network_wall_system_is_inherited():
    A = LinePath(Vec2(40, 200), Vec2(320, 200), id='A')
    B = LinePath(Vec2(180, 200), Vec2(180, 320), id='B')
    L = PF._phys(PrintLayer('t', source_paths=[A, B],
                            network_walls=[NetworkWall('NW', 'A', 12, system={'type': 'linked'})]))
    paths, meta = build(L)
    ws = meta['network']['wall_systems']
    # (pass 10: every generated wall reports its own band)
    assert ws and all(w['status'] == 'ok' for w in ws) and {s_ for w in ws for s_ in w['sources']} == {'A', 'B'}
    assert system_paths(paths)


# --- state / serialisation / determinism --------------------------------------------------

def _doc(system):
    return {'id': 'd', 'source_paths': [
        {'id': 'W', 'type': 'RectanglePath', 'x': 100, 'y': 140, 'w': 200, 'h': 120, 'closed': True,
         'wall': {'thickness': 12, 'align': 'auto', 'system': system}}],
        'offset_treatments': [], 'lattice_instances': [], 'infills': [], 'openings': [],
        'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}}


def test_wall_system_round_trips_and_is_deterministic():
    sysd = {'type': 'interleaved', 'pattern': 'zigzag', 'paths': 5, 'period': 26, 'depth': 3.5}
    L = _deserialise_layer(_doc(sysd))
    assert L.source_paths[0].wall.system == sysd
    assert L.source_paths[0].wall.to_dict()['system'] == sysd
    nw = NetworkWall('N', 'W', 10, system={'type': 'chain'})
    assert nw.to_dict()['system'] == {'type': 'chain'}
    key = lambda L: [[(round(q.x, 9), round(q.y, 9)) for q in p.sample_points()] for p in system_paths(build(L)[0])]
    assert key(_deserialise_layer(_doc(sysd))) == key(_deserialise_layer(_doc(sysd)))


def test_layer_designs_carry_the_wall_system():
    base = _doc({'type': 'linked', 'paths': 3})
    lib = DesignLibrary([{'id': 'b', 'name': 'Base', 'document': base},
                         {'id': 'g', 'name': 'gaps', 'parent': 'b', 'patch': {'openings': {
                             'o': {'source_path_id': 'W', 'center_s': 100, 'width': 30}}}}])
    for d in ('b', 'g'):
        bl = lib.build(d)
        assert bl.network['wall_systems'] and bl.network['wall_systems'][0]['system'] == 'linked'
    assert lib.document('g')['source_paths'][0]['wall']['system']['type'] == 'linked'


def test_normalize_clamps_parameters():
    assert WS.normalize({'type': 'linked', 'paths': 1})['paths'] == 2
    assert WS.normalize({'type': 'interleaved', 'paths': 99})['paths'] == 12
    assert WS.normalize({'type': 'skin_web'}) is None and WS.normalize(None) is None


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_wall_system_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_wall_system_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI WALL SYSTEM SMOKE PASSED' in proc.stdout


# --- correction pass (2026-10-07): corners, chain ∞∞∞ --------------------------------------

def _corner_sets(system, period):
    """Each corner's lane mitre points, in that corner's own frame (offsets
    from the ring's outer corner, measured inwards)."""
    sysd = dict(system, period=period)
    _, meta = build(make('rect', sysd))
    (rep,) = meta['network']['wall_systems']
    cps = [mp for r in rep['per_run'] for mp in r.get('corner_points', [])]
    out = []
    for mp in cps:
        cx = 100 if min(x for x, _ in mp) < 200 else 300
        cy = 140 if min(y for _, y in mp) < 200 else 260
        out.append(sorted((round(abs(x - cx), 2), round(abs(y - cy), 2)) for x, y in mp))
    return out


@pytest.mark.parametrize('system', [{'type': 'interleaved'}, {'type': 'interleaved', 'paths': 4, 'pattern': 'zigzag'},
                                    {'type': 'linked'}, {'type': 'linked', 'paths': 4}],
                         ids=['interleaved', 'interleaved4z', 'linked', 'linked4'])
def test_equivalent_corners_are_identical_whatever_the_period(system):
    a = _corner_sets(system, 24.0)
    b = _corner_sets(system, 31.0)
    assert len(a) == 4 and all(len(c) == system.get('paths', 3) for c in a)
    assert all(c == a[0] for c in a)                 # the four corners: one treatment
    assert a == b                                    # independent of the period / phase


@pytest.mark.parametrize('system', [{'type': 'interleaved'}, {'type': 'linked'}], ids=['interleaved', 'linked'])
def test_corner_lanes_keep_the_paths_apart_and_inside(system):
    paths, meta = build(make('rect', system))
    (rep,) = meta['network']['wall_systems']
    for mp in (m for r in rep['per_run'] for m in r.get('corner_points', [])):
        d = min(math.dist(p, q) for i, p in enumerate(mp) for q in mp[i + 1:])
        assert d > 0.5                               # distinct lanes round the corner (no overlap)
    ok, bad = inside_envelope(meta, [p.sample_points() for p in system_paths(paths)], 0.5 * BEAD - 0.05)
    assert ok, bad
    # the spans between corners hold whole periods
    for r in rep['per_run']:
        assert r['periods'] and all(p > 0 for p in r['periods'])


def test_wall_system_parameters_survive_a_thickness_change():
    L = make('rect', {'type': 'linked', 'paths': 4, 'period': 30})
    L.source_paths[0].wall = WallSpec(16, system=L.source_paths[0].wall.system)     # (the UI keeps it)
    _, meta = build(L)
    (rep,) = meta['network']['wall_systems']
    assert rep['system'] == 'linked' and rep['params']['paths'] == 4 and abs(rep['envelope'] - 16) < 0.6


# --- Chained Loop: topology (2026-10-07 replacement) ----------------------------------------

def _self_crossings(pts):
    """Proper self-intersections of an open / closed polyline:
    [(point, index along the path)]."""
    segs = list(zip(pts, pts[1:]))
    cell = 6.0
    grid = {}
    for i, (a, b) in enumerate(segs):
        for gx in range(math.floor(min(a.x, b.x) / cell), math.floor(max(a.x, b.x) / cell) + 1):
            for gy in range(math.floor(min(a.y, b.y) / cell), math.floor(max(a.y, b.y) / cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    seen, out = set(), []
    for ids in grid.values():
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                i, j = sorted((ids[x], ids[y]))
                if j - i < 2 or (i == 0 and j == len(segs) - 1) or (i, j) in seen:
                    continue
                seen.add((i, j))
                (a, b), (c, d) = segs[i], segs[j]
                r, q = b - a, d - c
                den = r.x * q.y - r.y * q.x
                if abs(den) < 1e-12:
                    continue
                w = c - a
                t = (w.x * q.y - w.y * q.x) / den
                u = (w.x * r.y - w.y * r.x) / den
                if 1e-7 < t < 1 - 1e-7 and 1e-7 < u < 1 - 1e-7:      # (a shared vertex is a touch)
                    out.append((a + r * t, i + t))
    return sorted(out, key=lambda c: c[1])


def _side(centre_pts, q):
    """Signed distance of q from the wall centre line (+ = left of its direction)."""
    best = None
    for a, b in zip(centre_pts, centre_pts[1:]):
        d = b - a
        L2 = d.x * d.x + d.y * d.y
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((q.x - a.x) * d.x + (q.y - a.y) * d.y) / L2))
        f = a + d * t
        dist = f.dist(q)
        if best is None or dist < best[0]:
            best = (dist, math.copysign(dist, d.x * (q.y - a.y) - d.y * (q.x - a.x)))
    return best[1]


def _along(centre_pts, q):
    """Arc position of q's foot on the wall centre line (longitudinal)."""
    best, acc = None, 0.0
    for a, b in zip(centre_pts, centre_pts[1:]):
        d = b - a
        L = d.length()
        t = 0.0 if L < 1e-12 else max(0.0, min(1.0, ((q.x - a.x) * d.x + (q.y - a.y) * d.y) / (L * L)))
        dist = (a + d * t).dist(q)
        if best is None or dist < best[0]:
            best = (dist, acc + t * L)
        acc += L
    return best[1]


def _chain(kind='line', **params):
    """The approved single-lap chain on a CLOSED wall (no free ends):
    'line' = the long bottom side of a 600 × 200 ring (window away from its
    corners), 'curve' = a circle wall."""
    from model import CirclePath
    sysd = dict({'type': 'chain'}, **params)
    if kind == 'line':
        L = _big_ring(sysd)
        centre = [Vec2(-100, 194), Vec2(700, 194)]
        win = lambda q: q.y > 150 and 120 < q.x < 480
    else:
        C = CirclePath(300, 300, 160, id='W')
        C.wall = WallSpec(T, system=sysd)
        L = PF._phys(PrintLayer('t', source_paths=[C]))
        centre = [Vec2(300 + 154 * math.cos(2 * math.pi * k / 720), 300 + 154 * math.sin(2 * math.pi * k / 720))
                  for k in range(721)]
        win = lambda q: True
    paths, meta = build(L)
    return system_paths(paths), meta['network']['wall_systems'][0], centre, win


def _topology(sp, rep, centre, win=lambda q: True):
    """The approved chain topology over a contiguous stretch of the path:
    ONE continuous path; loop crests alternate + / − and sit about one pitch
    apart (staggered); each loop crosses itself exactly once, on its own
    side, near its crest; no other crossings."""
    assert len(sp) == 1                                           # ONE path, no other components
    p = sp[0]
    allp = p.sample_points() + ([p.sample_points()[0]] if p.closed else [])
    pitch = rep['per_run'][0]['pitch']
    assert all(a.dist(b) < max(pitch, 6.0) for a, b in zip(allp, allp[1:]))   # continuous (no jump)
    # the longest contiguous stretch inside the window (a ring's seam may lie in it)
    blocks, cur = [], []
    for i, q in enumerate(allp):
        if win(q):
            cur.append(i)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    best = max(blocks, key=len)
    pts = allp[best[0]:best[-1] + 1]
    ys = [_side(centre, q) for q in pts]
    peak = max(abs(y) for y in ys)
    crest = [i for i in range(1, len(ys) - 1) if abs(ys[i]) >= abs(ys[i - 1]) and abs(ys[i]) > abs(ys[i + 1])
             and abs(ys[i]) > 0.8 * peak]
    crest = [i for k, i in enumerate(crest) if k == 0 or i - crest[k - 1] > 3]
    assert len(crest) >= 4
    assert all((ys[a] > 0) != (ys[b] > 0) for a, b in zip(crest, crest[1:]))          # alternating
    circ = sum(a.dist(b) for a, b in zip(centre, centre[1:])) if centre[0].dist(centre[-1]) < 1e-6 else None

    def dlong(q1, q2):
        d = abs(_along(centre, q2) - _along(centre, q1))
        return min(d, circ - d) if circ else d
    gaps = [dlong(pts[a], pts[b]) for a, b in zip(crest, crest[1:])]
    assert all(0.6 * pitch < g < 1.5 * pitch for g in gaps), (gaps, pitch)            # staggered
    xs = _self_crossings(pts)
    inner = crest[1:-1]                                           # whole loops (not cut by the window)
    for i in inner:
        near = [c for c, _ in xs if dlong(c, pts[i]) < 0.5 * pitch]
        assert len(near) == 1, (len(near), pts[i])               # ONE self-intersection per loop
        assert (_side(centre, near[0]) > 0) == (ys[i] > 0)       # … at its own neck
    lo_, hi_ = pts[inner[0]], pts[inner[-1]]
    between = [c for c, _ in xs if min(dlong(c, pts[i]) for i in inner) < 0.5 * pitch]
    assert len(between) == len(inner)                             # no other crossings there
    return xs


def test_chained_loop_topology_on_a_straight_wall():
    sp, rep, centre, win = _chain('line')
    _topology(sp, rep, centre, win)
    ok, bad = inside_envelope(build(_big_ring({'type': 'chain'}))[1], [sp[0].sample_points()], 0.5 * BEAD - 0.05)
    assert ok, bad


def test_chained_loop_topology_on_a_gentle_curve():
    sp, rep, centre, win = _chain('curve')
    _topology(sp, rep, centre, win)


@pytest.mark.parametrize('params', [{'pitch': 16}, {'pitch': 22}, {'loop_depth': 3.0}, {'loop_width': 6},
                                    {'loop_width': 18}, {'neck': 0.4}, {'phase': 0.5}, {'pitch': 0.8}],
                         ids=['pitch16', 'pitch22', 'depth3', 'width6', 'width18', 'neck04', 'phase05', 'pitch08'])
def test_chained_loop_parameters_preserve_the_topology(params):
    sp, rep, centre, win = _chain('line', **params)
    if params.get('pitch', 99) < 1:                               # (tight: a short window is plenty)
        win = lambda q: q.y > 150 and 280 < q.x < 300
    _topology(sp, rep, centre, win)


def test_chained_loop_parameters_do_what_they_say():
    _, a, _, _ = _chain('line', pitch=16)
    _, b, _, _ = _chain('line', pitch=24)
    assert a['per_run'][0]['loops'] > b['per_run'][0]['loops']
    _, c, _, _ = _chain('line', loop_width=8)
    assert abs(c['per_run'][0]['loop_width'] - 8) < 0.2
    _, d, _, _ = _chain('line', neck=0.5)
    assert abs(d['per_run'][0]['neck'] - 0.5) < 0.02


def test_chained_loop_period_below_one_inch():
    """Pitch may go well below 1 in (safety floor CHAIN_PITCH_MIN = 0.1 in);
    the minimum effective thickness keeps updating."""
    assert WS.normalize({'type': 'chain', 'pitch': 0.4})['pitch'] == pytest.approx(0.4)
    assert WS.normalize({'type': 'chain', 'pitch': 0.01})['pitch'] == pytest.approx(WS.CHAIN_PITCH_MIN)
    _, r1, _, _ = _chain('line', pitch=0.6)
    _, r2, _, _ = _chain('line', pitch=12)
    assert r1['per_run'][0]['pitch'] < 1.0 and r1['per_run'][0]['loops'] > 10 * r2['per_run'][0]['loops']
    assert r1['min_effective_thickness'] is not None and r1['min_effective_thickness'] >= BEAD - 1e-6


def test_chained_loop_on_a_ring_is_one_closed_path():
    L = make('rect', {'type': 'chain'})
    paths, meta = build(L)
    (p,) = system_paths(paths)
    rep = meta['network']['wall_systems'][0]
    assert p.closed and rep['per_run'][0]['loops'] % 2 == 0      # upper / lower pairs close the ring
    assert BEAD - 1e-6 <= rep['min_effective_thickness'] <= rep['envelope'] + 1e-6


# --- correction pass 3 (2026-10-07): rounded envelope, end caps, closed routes --------------

from graph import route_layer, compute_metrics, build_graph, closure_report   # noqa: E402

CLOSABLE = [{'type': 'interleaved', 'paths': 4}, {'type': 'linked', 'paths': 4},
            {'type': 'interleaved', 'paths': 6, 'pattern': 'zigzag'}, {'type': 'chain'}]
CLOSABLE_IDS = ['interleaved4', 'linked4', 'interleaved6z', 'chain']
OPENINGS_CASES = {'one': [(100.0, 30.0)], 'three': [(100.0, 24.0), (300.0, 30.0), (430.0, 30.0)],
                  'near_corner': [(222.0, 24.0)]}


def _route(L):
    p, m = L._build_effective()
    rl = L.to_routing_layer(p, m)
    return p, m, closure_report(build_graph(rl)), compute_metrics(route_layer(rl, allow_retrace=False))


def _ring(system, openings=(), cr=0.0, cap=None):
    P = RectanglePath(100, 140, 200, 120, id='W', corner_radius=cr)
    P.wall = WallSpec(T, system=system)
    L = PrintLayer('t', source_paths=[P])
    L.openings = [Opening(f'o{k}', 'W', c, w) for k, (c, w) in enumerate(openings)]
    if cap:
        L.cap_style = cap
    return PF._phys(L)


@pytest.mark.parametrize('cap', [None, 'full_round'], ids=['flat', 'round'])
@pytest.mark.parametrize('case', list(OPENINGS_CASES))
@pytest.mark.parametrize('system', CLOSABLE, ids=CLOSABLE_IDS)
def test_openings_are_capped_and_each_piece_is_one_closed_route(system, case, cap):
    """An opening makes two real wall ends: the strands are turned through
    the resolved end caps, so every connected piece prints as ONE closed
    route — no free strand ends, no travel inside a piece, no retrace."""
    L = _ring(system, OPENINGS_CASES[case], cap=cap)
    p, m, closure, mt = _route(L)
    pieces = len(m['regions'])
    sp = system_paths(p)
    assert len(sp) == pieces and all(x.closed for x in sp)        # one closed path per piece
    assert not closure['open']                                    # no free ends
    assert mt['print_runs'] == pieces and mt['retrace_distance'] == 0
    ok, bad = inside_envelope(m, [x.sample_points() for x in sp], 0.5 * BEAD - 0.05)
    assert ok, bad
    assert all(w['closable'] for w in m['network']['wall_systems'])


@pytest.mark.parametrize('cap', [None, 'full_round'], ids=['flat', 'round'])
def test_the_turnaround_follows_the_resolved_end_cap(cap):
    """Skin + Web and the wall system agree where the wall ends: the outer
    turnaround runs half a bead inside the very cap / opening face the
    envelope resolves (flat or full round)."""
    L = _ring({'type': 'linked', 'paths': 4}, [(100.0, 30.0)], cap=cap)
    p, m = L._build_effective()
    ring = m['regions'][0][0]
    apex = max((q for q in ring if q.y < 160 and q.x < 200), key=lambda q: q.x)   # the left jamb's cap tip
    pts = [q for x in system_paths(p) for q in x.sample_points()]
    d = min(q.dist(apex) for q in pts)
    assert 0.5 * BEAD - 0.3 < d < 0.5 * BEAD * math.sqrt(2) + 0.3, d


@pytest.mark.parametrize('system', [{'type': 'interleaved'}, {'type': 'linked'}], ids=['interleaved3', 'linked3'])
def test_odd_path_counts_cannot_close_at_free_ends(system):
    """Each free end would hold an odd number of strand ends: a closed route
    is impossible (every connector graph has an even total degree). The
    strands are still joined into ONE path per piece, open at the two jambs,
    and the report says so."""
    p, m, closure, mt = _route(_ring(system, [(100.0, 30.0)]))
    sp = system_paths(p)
    assert len(sp) == 1 and not sp[0].closed
    assert m['network']['wall_systems'][0]['closable'] is False
    assert mt['print_runs'] == 1 and mt['retrace_distance'] == 0


@pytest.mark.parametrize('system', [{'type': 'interleaved'}, {'type': 'linked', 'paths': 4}, {'type': 'chain'}],
                         ids=['interleaved', 'linked', 'chain'])
@pytest.mark.parametrize('cr', [10.0, 20.0, 40.0])
def test_corner_r_is_followed(system, cr):
    """Corner R: the system follows the RESOLVED rounded envelope — every
    bead half a bead inside; corner lanes follow the round instead of
    mitring outside it; changing Corner R regenerates the geometry."""
    p, m = _ring(system, cr=cr)._build_effective()
    sp = system_paths(p)
    ok, bad = inside_envelope(m, [x.sample_points() for x in sp], 0.5 * BEAD - 0.05)
    assert ok, bad
    rep = m['network']['wall_systems'][0]
    kinds = [k for r in rep['per_run'] for k in r.get('corner_kinds', [])]
    assert 'mitre' not in kinds
    p0, _ = _ring(system, cr=0.0)._build_effective()
    key = lambda ps: [[(round(q.x, 3), round(q.y, 3)) for q in x.sample_points()] for x in system_paths(ps)]
    assert key(p) != key(p0)


@pytest.mark.parametrize('style', ['round', 'miter'])
@pytest.mark.parametrize('system', [{'type': 'interleaved', 'paths': 4}, {'type': 'linked', 'paths': 4}, {'type': 'chain'}],
                         ids=['interleaved', 'linked', 'chain'])
def test_rounded_junctions_are_followed(system, style):
    import reference_network as RN
    d = RN.document('zigzag')
    d['infills'] = []
    d['network_walls'][0]['system'] = system
    d['junction_style'], d['junction_radius'] = style, 6
    p, m = build(_deserialise_layer(d))
    ok, bad = inside_envelope(m, [x.sample_points() for x in system_paths(p)], 0.5 * BEAD - 0.05)
    assert ok, bad


# --- pass 4 (2026-10-07): canonical end motifs, Wall System membership ---------------------

from model import WallSystem, RegionInfill, CirclePath   # noqa: E402
import wall_systems as WSm      # noqa: E402

H_ = 0.5 * T - 0.5 * BEAD       # how far a bead centreline may go off the wall's middle


def _jamb_pts(system, cap=None):
    """System path vertices near the left piece's jamb of a 30 in doorway in
    the top wall (jamb face x = 185, wall band y 140 … 140 + T)."""
    p, m = _ring(system, [(100.0, 30.0)], cap=cap)._build_effective()
    pts = [q for x in system_paths(p) for q in x.sample_points()]
    return [q for q in pts if 160 < q.x < 186 and 139 < q.y < 141 + T], m


@pytest.mark.parametrize('system', [{'type': 'interleaved', 'paths': 4}, {'type': 'linked', 'paths': 4},
                                    {'type': 'linked', 'paths': 6}], ids=['interleaved4', 'linked4', 'linked6'])
def test_flat_end_motif_is_one_straight_cap_with_nested_returns(system):
    """FLAT end: the outermost lanes (half a bead inside each face) are
    joined by ONE straight cap half a bead inside the cut; every inner lane
    pair r returns r lane spacings further in, parallel to it (concentric
    rectangles) — the same at every jamb / wall end."""
    N = system['paths']
    g = 2 * H_ / (N - 1)
    pts, _ = _jamb_pts(system)
    xcap = 185 - 0.5 * BEAD
    for r in range(N // 2):
        x = xcap - r * g
        ys = sorted(q.y for q in pts if abs(q.x - x) < 0.02)
        assert ys, (r, x)
        assert abs(ys[0] - (140 + 0.5 * BEAD + r * g)) < 0.05 and abs(ys[-1] - (140 + T - 0.5 * BEAD - r * g)) < 0.05, (r, ys)
    # nothing between the cap and the cut
    assert max(q.x for q in pts) < xcap + 0.02


@pytest.mark.parametrize('system', [{'type': 'interleaved', 'paths': 4}, {'type': 'linked', 'paths': 4}],
                         ids=['interleaved4', 'linked4'])
def test_rounded_end_motif_nests_concentric_arcs(system):
    """ROUNDED (full round) end: the cap follows the round end half a bead
    inside it; the inner pair returns on the concentric arc one lane
    spacing further in."""
    N = system['paths']
    g = 2 * H_ / (N - 1)
    pts, m = _jamb_pts(system, cap='full_round')
    ring = [q for reg in m['regions'] for r in reg for q in r]
    apex = max((q for q in ring if q.x < 200 and 139 < q.y < 141 + T), key=lambda q: q.x)
    c = Vec2(apex.x - 0.5 * T, 140 + 0.5 * T)
    beyond = [q for q in pts if q.x > c.x + 0.3]
    for r in range(N // 2):
        rad = 0.5 * T - 0.5 * BEAD - r * g
        on = [q for q in beyond if abs(q.dist(c) - rad) < 0.15]       # (a polygonal arc's offset)
        assert len(on) >= 5, (r, rad, sorted(round(q.dist(c), 2) for q in beyond))
    assert all(q.dist(c) < 0.5 * T - 0.5 * BEAD + 0.15 for q in beyond)


def test_chain_end_motif_runs_its_laps_into_the_face_lanes_and_one_cap():
    """Chained Loop at a jamb: the forward lap leads out to one face lane,
    the return lap to the other, and one straight cap joins them."""
    pts, _ = _jamb_pts({'type': 'chain'})
    xcap = 185 - 0.5 * BEAD
    ys = sorted(q.y for q in pts if abs(q.x - xcap) < 0.02)
    assert abs(ys[0] - (140 + 0.5 * BEAD)) < 0.05 and abs(ys[-1] - (140 + T - 0.5 * BEAD)) < 0.05
    for yl in (140 + 0.5 * BEAD, 140 + T - 0.5 * BEAD):           # each face lane reaches the cap
        lane = [q.x for q in pts if abs(q.y - yl) < 0.05]
        assert lane and max(lane) > xcap - 0.05 and min(lane) < xcap - 2.0


@pytest.mark.parametrize('N', range(1, 13))
def test_end_lane_permutation_joins_every_strand_with_the_fewest_crossings(N):
    """Nested end motifs pair lane r with N−1−r at both ends; every strand's
    antipodal partner holds the mirror lane, so without a permutation each
    pair would close on its own. The hi-end permutation joins all strands
    into one circuit / path using N//2 − 1 adjacent swaps (the minimum)."""
    perm = WSm._end_perm(N)
    assert sorted(perm) == list(range(N))
    inversions = sum(1 for i in range(N) for j in range(i + 1, N) if perm[i] > perm[j])
    assert inversions == max(0, (N + 1) // 2 - 1)
    # walk: lo lane → (strand) hi lane → (motif) partner → (strand) lo lane → (motif) partner …
    inv = {v: k for k, v in enumerate(perm)}
    seen, r, steps = set(), (0 if N % 2 == 0 else N // 2), 0
    while steps < 2 * N:
        seen.add(r)
        h = perm[r]
        h2 = N - 1 - h
        if h2 == h:
            break
        r2 = inv[h2]
        seen.add(r2)
        r = N - 1 - r2
        steps += 1
        if r == r2 or r in seen and N % 2 == 0:
            break
    assert seen == set(range(N))


def _rect_line(line_wall, systems):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(10)
    Ln = LinePath(Vec2(200, 140), Vec2(200, 80), id='L1')
    Ln.wall = line_wall
    L = PF._phys(PrintLayer('t', source_paths=[R, Ln]))
    L.wall_systems = systems
    return L


def test_a_touching_line_is_not_forced_into_the_wall_system():
    """Construction membership is EXPLICIT: an attached line joins the Wall
    Network (connectivity) but not the rect's Wall System."""
    L = _rect_line(None, [WallSystem('WS1', 'chain', {}, ['R'], thickness=10)])
    p, m = L._build_effective()
    net = m['network']
    assert any(set(n['sources']) == {'R', 'L1'} for n in net['source_networks'])
    assert set(net['wall_system_membership']) == {'R'}
    assert net['wall_systems'][0]['sources'] == ['R']
    assert net['source_walls']['L1'] is None                     # a single bead


def test_a_removed_line_can_use_its_own_wall_system():
    L = _rect_line(None, [WallSystem('WS1', 'chain', {}, ['R'], thickness=10),
                          WallSystem('WS2', 'linked', {'paths': 2}, ['L1'], thickness=6)])
    p, m = L._build_effective()
    reps = {r['system_id']: r for r in m['network']['wall_systems']}
    assert set(reps) == {'WS1', 'WS2'} and reps['WS1']['sources'] == ['R'] and reps['WS2']['sources'] == ['L1']
    # (pass 8) the line's generated wall no longer stops at the rect's nominal
    # face: its face lanes run straight into the rect's printed material and
    # both systems print as ONE route
    # (pass 10) the line's band is pushed into the rect: its paths CROSS the rect's
    assert _crossing_count([x.sample_points() for x in system_paths(p) if x.source_id == 'L1'],
                           [x.sample_points() + [x.sample_points()[0]] for x in system_paths(p)
                            if x.source_id == 'R']) >= 1
    ok, bad = inside_envelope(m, [x.sample_points() for x in system_paths(p)], 0.5 * BEAD - 0.05)
    assert ok, bad


def test_a_partition_of_another_construction_keeps_its_skins_and_the_host_its_system():
    """(pass 9; previously the whole region fell back to Skin + Web) A wall of
    another construction spanning between two walls of the system: the host's
    envelope is stitched across the partition's band (the two rooms' rings
    join at its mouths), the host keeps its system, the partition its skins.
    (V1: the partition prints as its own route — not yet spliced.)"""
    R = RectanglePath(100, 140, 200, 120, id='R')
    P = LinePath(Vec2(200, 140), Vec2(200, 260), id='P')
    L = PF._phys(PrintLayer('t', source_paths=[R, P]))
    L.wall_systems = [WallSystem('WS1', 'chain', {}, ['R'], thickness=10),
                      WallSystem('WS2', 'hollow', {}, ['P'], thickness=10)]
    p, m = L._build_effective()
    reps = m['network']['wall_systems']
    assert [r['status'] for r in reps] == ['ok'] and reps[0]['sources'] == ['R']
    sp = system_paths(p)
    assert sp and all(not (196 < q.x < 204 and 152 < q.y < 248) for x in sp for q in x.sample_points())
    assert any(getattr(x, 'source_id', None) == 'P' for x in p)               # the partition's skins


def test_wall_systems_round_trip_through_the_document():
    d = _rect_line(None, [WallSystem('WS1', 'chain', {'pitch': 0.5}, ['R'], name='Outer', thickness=10,
                                     align='inside'),
                          WallSystem('WS2', 'skin_web', {}, ['L1'], name='Line', thickness=6,
                                     web={'pattern': 'truss', 'params': {'seed': 2}})]).to_dict()
    L2 = _deserialise_layer(d)
    assert [w.to_dict() for w in L2.wall_systems] == [
        {'id': 'WS1', 'name': 'Outer', 'type': 'chain', 'params': {'pitch': 0.5}, 'members': ['R'],
         'align': 'inside', 'print_reference': False, 'web': {}, 'thickness': 10},
        {'id': 'WS2', 'name': 'Line', 'type': 'skin_web', 'params': {}, 'members': ['L1'],
         'align': 'auto', 'print_reference': False, 'web': {'pattern': 'truss', 'params': {'seed': 2}},
         'thickness': 6}]
    inf = RegionInfill('I', 'L1', 'wave', {'spacing': 12}, 0, 'wall', 'WS2')
    assert _deserialise_layer(dict(d, infills=[inf.to_dict()])).infills[0].owner == 'WS2'


# --- pass 5 (2026-10-07): the Wall System OWNS the construction ------------------------------

def _two_rects(systems, walls=(None, None)):
    A = RectanglePath(40, 40, 120, 80, id='A')
    B = RectanglePath(260, 40, 120, 80, id='B')            # does NOT touch A
    A.wall, B.wall = walls
    L = PF._phys(PrintLayer('t', source_paths=[A, B]))
    L.wall_systems = systems
    return L


def test_the_wall_system_owns_thickness_and_alignment():
    """A member path needs no wall of its own: the system's Wall Thickness /
    Alignment make its envelope (and win over a legacy path wall)."""
    L = _two_rects([WallSystem('WS1', 'skin_web', {}, ['A'], thickness=8, align='outside')],
                   walls=(WallSpec(20), None))
    p, m = L._build_effective()
    sw = m['network']['source_walls']
    assert sw['A'] == {'thickness': 8, 'align': 'outside', 'print_reference': False, 'system': None,
                       'from': 'system'}
    assert sw['B'] is None                                     # not a member: a single bead
    outer = [x for x in p if getattr(x, 'source_id', None) == 'A' or getattr(x, 'id', '') == 'A']
    xs = [q.x for x in outer for q in x.sample_points()]
    assert abs(min(xs) - 32) < 0.05                            # 8 in OUTSIDE the 40 in edge


def test_members_need_not_touch():
    L = _two_rects([WallSystem('WS1', 'interleaved', {'paths': 4}, ['A', 'B'], thickness=10)])
    p, m = L._build_effective()
    reps = m['network']['wall_systems']
    assert len(reps) == 2 and {tuple(r['sources']) for r in reps} == {('A',), ('B',)}
    assert all(r['system_id'] == 'WS1' for r in reps)
    assert {x.source_id for x in system_paths(p)} == {'A', 'B'}


def test_a_path_belongs_to_one_system_first_wins():
    L = _two_rects([WallSystem('WS1', 'chain', {}, ['A'], thickness=10),
                    WallSystem('WS2', 'linked', {'paths': 2}, ['A', 'B'], thickness=10)])
    p, m = L._build_effective()
    mem = m['network']['wall_system_membership']
    assert mem['A']['id'] == 'WS1' and mem['B']['id'] == 'WS2'


@pytest.mark.parametrize('pattern', ['zigzag', 'wave', 'truss'])
def test_skin_web_system_owns_its_web(pattern):
    """Skin + Web: the web lattice is configured on the system. Without an
    owned infill record (API layer) one is synthesised per member; an owned
    record (the Designer's materialisation, one per connected group, id kept
    for Layer-Design lineage) is used as is."""
    ws = WallSystem('WS1', 'skin_web', {}, ['A', 'B'], thickness=10,
                    web={'pattern': pattern, 'params': {'spacing': 18}})
    p, m = _two_rects([ws])._build_effective()
    infos = {i['id']: i for i in m['network']['infills']}
    assert set(infos) == {'WS1~web~A', 'WS1~web~B'}
    assert all(i.get('regions', 0) >= 1 for i in infos.values()), infos
    assert any(x.role not in ('outer', 'inner', 'free', 'cap') for x in p)    # lattice beads exist
    # an owned record wins over synthesis (and keeps its id)
    L = _two_rects([ws])
    L.infills = [RegionInfill('legacy-id', 'A', pattern, {'spacing': 18}, 0, 'wall', 'WS1')]
    p2, m2 = L._build_effective()
    assert [i['id'] for i in m2['network']['infills']] == ['legacy-id']


def test_skin_web_without_web_prints_skins_only_and_other_types_ignore_owned_webs():
    L = _two_rects([WallSystem('WS1', 'skin_web', {}, ['A'], thickness=10, web={'pattern': 'none'})])
    p, m = L._build_effective()
    assert m['network']['infills'] == []
    L = _two_rects([WallSystem('WS1', 'chain', {}, ['A'], thickness=10, web={'pattern': 'zigzag'})])
    L.infills = [RegionInfill('w', 'A', 'zigzag', {}, 0, 'wall', 'WS1')]
    p, m = L._build_effective()
    assert m['network']['infills'] == [] and system_paths(p)        # kept for later, not built


def test_legacy_walls_still_resolve():
    """Un-migrated designs (own WallSpec / Network Wall / unowned infill)
    build exactly as before; the effective wall is reported for migration."""
    L = _two_rects([], walls=(WallSpec(10, system={'type': 'chain'}), None))
    p, m = L._build_effective()
    sw = m['network']['source_walls']['A']
    assert sw['from'] == 'path' and sw['thickness'] == 10 and sw['system']['type'] == 'chain'
    assert system_paths(p)


# --- migration of legacy designs ---------------------------------------------------------

import wall_fixtures as WF        # noqa: E402
import reference_network as RNM   # noqa: E402


def _apply_migration(L, res):
    """What the Designer does with /api/migrate_wall_systems."""
    import copy
    M = copy.deepcopy(L)
    M.wall_systems = [_deserialise_layer({'source_paths': [], 'wall_systems': [w]}).wall_systems[0]
                      for w in res['wall_systems']]
    for f in M.infills:
        if f.id in res['owners']:
            f.owner = res['owners'][f.id]
    for p in M.source_paths:
        p.wall = None
    M.network_walls = []
    return M


def _printable(L):
    p, m = L._build_effective()
    pc = L.printable_centerlines(p, m)
    pts = sorted(tuple((round(a, 3), round(b, 3)) for a, b in c['pts']) for c in pc)
    assert pts and all(pts)
    rl = L.to_routing_layer(p, m)
    mt = compute_metrics(route_layer(rl, allow_retrace=False))
    return pts, mt['print_runs'], round(mt['travel_distance'], 2)


MIGRATION_CASES = {
    'own_walls_infill': lambda: PF._phys(WF.rect_branches(1)),
    'network_wall_infill_ref': lambda: _deserialise_layer(RNM.document('zigzag')),
    'network_wall_single_bead_line': lambda: _deserialise_layer(RNM.document('wave', line_thickness=0)),
    'network_wall_line_override': lambda: _deserialise_layer(RNM.document('zigzag', line_thickness=6)),
    'legacy_wall_system': lambda: _ring({'type': 'linked', 'paths': 4}, [(100.0, 30.0)]),
    'previous_pass_layer_system': lambda: _two_rects([WallSystem('WSx', 'chain', {}, ['A'])],
                                                     walls=(WallSpec(10), WallSpec(10))),
    'truss_web': lambda: PF._phys(WF.rect_loop(pat='truss')),
}


@pytest.mark.parametrize('case', list(MIGRATION_CASES))
def test_migration_keeps_the_printed_geometry(case):
    """Legacy walls → explicit Wall Systems: the printable centrelines and
    the route are exactly the same after migration; no path wall / Network
    Wall remains; wall infills become owned webs (same ids)."""
    L = MIGRATION_CASES[case]()
    res = L.migrate_wall_systems()
    assert res['migrated']
    M = _apply_migration(L, res)
    assert not M.migrate_wall_systems()['migrated']               # idempotent
    assert _printable(M) == _printable(L)
    walls = [f for f in L.infills if f.kind == 'wall']
    assert all(f.id in res['owners'] for f in walls)
    members = [pid for w in res['wall_systems'] for pid in w['members']]
    assert len(members) == len(set(members))                    # one system per path


def test_migration_groups_by_construction_and_keeps_old_system_ids():
    L = _two_rects([WallSystem('WSx', 'chain', {}, ['A'])], walls=(WallSpec(10), WallSpec(10)))
    res = L.migrate_wall_systems()
    by = {w['id']: w for w in res['wall_systems']}
    assert by['WSx']['members'] == ['A'] and by['WSx']['type'] == 'chain' and by['WSx']['thickness'] == 10
    other = [w for w in res['wall_systems'] if w['id'] != 'WSx']
    assert len(other) == 1 and other[0]['members'] == ['B']
    assert other[0]['type'] == 'hollow'               # skins, no web: Hollow / Skins Only


# --- pass 6 (2026-10-07): construction types; mixed junctions meet ACTUAL material -----------

LANE = 0.5 * (BEAD - 0.75)          # half the out-and-back lane separation (W − R) / 2


def _junction(host, x0=200.0, R=0.0, branch=('single', None)):
    """A 200 × 120 rect (10 in, host construction) + a line attached to its
    top face at x0, of another construction."""
    Rc = RectanglePath(100, 140, 200, 120, id='R')
    Ln = LinePath(Vec2(x0, 140), Vec2(x0, 80), id='L1')
    L = PF._phys(PrintLayer('t', source_paths=[Rc, Ln]))
    L.wall_systems = [WallSystem('H', host['type'], {k: v for k, v in host.items() if k != 'type'}, ['R'],
                                 thickness=10),
                      WallSystem('B', branch[0], {}, ['L1'], thickness=branch[1])]
    if R:
        L.junction_style, L.junction_radius = 'round', R
    return L


def _junction_report(L):
    p, m, closure, mt = _route(L)
    reps = [r for r in m['network']['wall_systems'] if r.get('system_id') == 'H']
    js = [j for r in reps for j in (r.get('junctions') or [])]
    return p, m, closure, mt, js


HOSTS = {'interleaved4': {'type': 'interleaved', 'paths': 4}, 'linked4': {'type': 'linked', 'paths': 4},
         'chain': {'type': 'chain'}, 'parallel4': {'type': 'parallel', 'walls': 4}}


def _xs_at(polys, y):
    """x of every crossing of the polylines with the horizontal line at y."""
    out = []
    for pl in polys:
        for a, b in zip(pl, pl[1:]):
            if (a.y - y) * (b.y - y) <= 0 and abs(b.y - a.y) > 1e-12:
                out.append(a.x + (b.x - a.x) * (y - a.y) / (b.y - a.y))
    return out


def _ys_at(polys, x):
    """y of every crossing of the polylines with the vertical line at x."""
    out = []
    for pl in polys:
        for a, b in zip(pl, pl[1:]):
            if (a.x - x) * (b.x - x) <= 0 and abs(b.x - a.x) > 1e-12:
                out.append(a.y + (b.y - a.y) * (x - a.x) / (b.x - a.x))
    return out












def test_single_construction_is_the_ordinary_out_and_back_wall():
    """Single / Out-and-Back: no Wall Thickness needed; with Physical rules
    an open path prints the two separated return lanes + U-turn."""
    Ln = LinePath(Vec2(100, 200), Vec2(300, 200), id='L')
    L = PF._phys(PrintLayer('t', source_paths=[Ln]))
    L.wall_systems = [WallSystem('S', 'single', {}, ['L'], thickness=10)]     # (thickness ignored)
    p, m, closure, mt = _route(L)
    assert m['network']['source_walls']['L'] is None and 'L' in m['network']['return_lanes']
    ys = sorted(round(y, 3) for y in _ys_at([x.sample_points() for x in p], 180.0))
    assert ys == [round(200 - LANE, 3), round(200 + LANE, 3)]
    assert mt['print_runs'] == 1 and not closure['open']


def test_hollow_construction_prints_skins_without_a_web():
    L = _two_rects([WallSystem('W', 'hollow', {}, ['A'], thickness=10, web={'pattern': 'zigzag'})])
    L.infills = [RegionInfill('w', 'A', 'zigzag', {}, 0, 'wall', 'W')]          # an inactive owned web
    p, m = L._build_effective()
    assert m['network']['infills'] == [] and not system_paths(p)
    assert m['network']['source_walls']['A']['thickness'] == 10


@pytest.mark.parametrize('N', [2, 3, 4, 6])
def test_parallel_walls_spread_evenly(N):
    """Parallel Walls: N longitudinal walls evenly across the thickness, the
    outermost half a bead inside the faces."""
    P = LinePath(Vec2(40, 200), Vec2(320, 200), id='W')
    L = PF._phys(PrintLayer('t', source_paths=[P]))
    L.wall_systems = [WallSystem('P', 'parallel', {'walls': N}, ['W'], thickness=T)]
    p, m = L._build_effective()
    H = 0.5 * T - 0.5 * BEAD
    ys = sorted({round(y, 2) for y in _ys_at([x.sample_points() for x in system_paths(p)], 110.0)})   # (rungs mid-span)
    want = sorted({round(200 - H + k * 2 * H / (N - 1), 2) for k in range(N)})
    assert ys == want, (ys, want)
    rep = m['network']['wall_systems'][0]
    assert rep['closable'] == (N % 2 == 0)


def test_parallel_walls_close_at_openings_for_even_counts():
    L = _ring({'type': 'parallel', 'walls': 4}, [(100.0, 30.0)])
    p, m, closure, mt = _route(L)
    assert mt['print_runs'] == 1 and mt['retrace_distance'] == 0 and not closure['open']
    ok, bad = inside_envelope(m, [x.sample_points() for x in system_paths(p)], 0.5 * BEAD - 0.05)
    assert ok, bad


# --- pass 7 (2026-10-07): Parallel Walls ends on the canonical motif; rungs ---------------

def _seam_clustered(pts, zone=None):
    """Parallel Walls (pass 9): crossings are allowed only where the return
    diagonal crosses the lane steps — all inside ONE seam zone."""
    xs = [q for q, _ in _self_crossings(pts)]
    zone = zone or 4.0 * BEAD + T
    return all(a.dist(b) <= zone for a in xs for b in xs), xs

@pytest.mark.parametrize('cap', [None, 'full_round'], ids=['flat', 'round'])
@pytest.mark.parametrize('case', list(OPENINGS_CASES))
@pytest.mark.parametrize('N', [2, 3, 4, 5, 6])
def test_parallel_walls_ends_are_clean_nested_motifs(N, case, cap):
    """Free ends / jambs: the outermost lanes form the cap, inner lanes nest
    behind it — no crossing anywhere (the wave lane permutation is NOT used),
    nothing outside the envelope; neighbouring loops are joined by parallel
    rungs so an even count prints as ONE closed route per piece (odd: one
    open route, by parity)."""
    L = _ring({'type': 'parallel', 'walls': N}, OPENINGS_CASES[case], cap=cap)
    p, m, closure, mt = _route(L)
    pieces = len(m['regions'])
    sp = system_paths(p)
    assert len(sp) == pieces
    for x in sp:
        pts = x.sample_points() + ([x.sample_points()[0]] if x.closed else [])
        ok_, xs_ = _seam_clustered(pts)
        assert ok_, (N, case, cap, xs_)
    ok, bad = inside_envelope(m, [x.sample_points() for x in sp], 0.5 * BEAD - 0.05)
    assert ok, bad
    assert mt['print_runs'] == pieces and mt['retrace_distance'] == 0
    if N % 2 == 0:
        assert all(x.closed for x in sp) and not closure['open']
        assert pieces > 1 or mt['travel_distance'] == 0          # (travel only BETWEEN pieces)
    else:
        assert not any(x.closed for x in sp)


@pytest.mark.parametrize('N', [2, 3, 4, 5])
def test_parallel_walls_on_a_ring_print_as_one_route(N):
    """A closed ring: the N concentric walls are joined by rung pairs into
    ONE closed route without crossings."""
    p, m, closure, mt = _route(_ring({'type': 'parallel', 'walls': N}))
    sp = system_paths(p)
    assert len(sp) == 1 and sp[0].closed and _seam_clustered(sp[0].sample_points() + [sp[0].sample_points()[0]])[0]
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and not closure['open']
    assert m['network']['wall_systems'][0]['per_run'][0]['transitions'] == N - 1


def test_parallel_walls_lone_line_free_ends():
    P = LinePath(Vec2(40, 200), Vec2(320, 200), id='W')
    for cap in (None, 'full_round'):
        L = PF._phys(PrintLayer('t', source_paths=[P]))
        if cap:
            L.cap_style = cap
        L.wall_systems = [WallSystem('P', 'parallel', {'walls': 4}, ['W'], thickness=T)]
        p, m, closure, mt = _route(L)
        sp = system_paths(p)
        assert len(sp) == 1 and sp[0].closed and _seam_clustered(sp[0].sample_points() + [sp[0].sample_points()[0]])[0]
        assert mt['print_runs'] == 1 and not closure['open']


# --- pass 8 (2026-10-07): consistent end motifs; exterior-lane continuity -----------------

def _end_insets(L):
    """Per free end: distance (along the end's outward direction) from the
    cap tip to the outermost system-path point there — half a bead for the
    canonical motif."""
    import wall_lattice as WL
    p, m = L._build_effective()
    pts = [q for x in system_paths(p) for q in x.sample_points()]
    out = []
    for rings in m['regions']:
        thick = WL._thickness(rings)
        sk = WL.skeleton(rings, max(0.5, min(thick / 3, 20 / 3)), thick)
        geo = WL._Geo(sk)
        env = WSm._Envelope(rings, BEAD)
        for r in sk.runs:
            if r.cycle:
                continue
            st = WSm._Strip(geo, r, BEAD, thick, env)
            for end, on in (('lo', r.a[0] == 'E'), ('hi', r.b[0] == 'E')):
                if not on:
                    continue
                s_e, f0, f1, t = WSm._end_frame(st, end)
                rail = env.cap_rail(f0, f1, st.at(s_e, 0), inset=0.0)
                along = lambda q: (q - f0).x * t.x + (q - f0).y * t.y
                tip = max(rail, key=along)
                out.append(along(tip) - max(along(q) for q in pts if q.dist(tip) < 1.5 * thick))
    return out


@pytest.mark.parametrize('cap', [None, 'full_round'], ids=['flat', 'round'])
def test_equivalent_parallel_ends_get_the_same_motif(cap):
    """Regression (live review: three equivalent ends correct, one inset).
    Root cause: the end frame used the skeleton centre line, which runs askew
    near a free end on a short leg (a jamb some 20 in from a corner) — that
    end was judged degenerate and got the inset fallback. Every clean jamb,
    whatever its distance from a corner, now ends half a bead from the cut."""
    dists = (8, 10, 12, 14, 18, 24, 40) if cap else (4, 6, 8, 10, 12, 14, 18, 24, 40)
    for d in dists:
        ins = _end_insets(_ring({'type': 'parallel', 'walls': 4}, [(200.0 + d + 15.0, 30.0)], cap=cap))
        assert all(abs(x - 0.5 * BEAD) < 0.15 for x in ins), (d, ins)
    for ops in ([(100.0, 30.0)], [(60.0, 24.0), (140.0, 24.0)], [(100.0, 30.0), (400.0, 30.0)]):
        ins = _end_insets(_ring({'type': 'parallel', 'walls': 4}, ops, cap=cap))
        assert all(abs(x - 0.5 * BEAD) < 0.15 for x in ins), (ops, ins)


def _covered(polys, on, lo, hi, tol=1e-3):
    """Union of the polyline segments lying ON a curve (predicate per
    segment) — as intervals of `key`; returns the uncovered gaps of [lo, hi]."""
    iv = sorted(on(a, b) for pl in polys for a, b in zip(pl, pl[1:]) if on(a, b) is not None)
    gaps, cur = [], lo
    for a, b in iv:
        if a > cur + tol:
            gaps.append((cur, a))
        cur = max(cur, b)
    if cur < hi - tol:
        gaps.append((cur, hi))
    return [g for g in gaps if g[1] > lo and g[0] < hi]


def _lane_on_y(y0, xmin, xmax):
    def on(a, b):
        if abs(a.y - y0) < 1e-3 and abs(b.y - y0) < 1e-3:
            return (max(xmin, min(a.x, b.x)), min(xmax, max(a.x, b.x)))
        return None
    return on


def _lane_gaps_ok(gaps, d=2.0 * BEAD):
    """(pass 9) A lane is complete except ONE seam window (≤ d long)."""
    return len(gaps) <= 1 and all(b - a <= d + 0.1 for a, b in gaps)


@pytest.mark.parametrize('N', [2, 3, 4, 5])
def test_parallel_ring_lanes_are_complete_but_for_one_seam_window(N):
    """Each lane is one clean, continuous path; the only interruption is the
    single seam window where the lane changes happen (pass 9 — supersedes the
    distributed kisses)."""
    p, m, closure, mt = _route(_ring({'type': 'parallel', 'walls': N}))
    polys = [x.sample_points() + [x.sample_points()[0]] for x in system_paths(p)]
    H = 0.5 * T - 0.5 * BEAD
    for k in range(N):
        y0 = 140 + 0.5 * BEAD + (k * 2 * H / (N - 1))
        gaps = _covered(polys, _lane_on_y(round(y0, 6), 100 + T, 300 - T), 100 + T, 300 - T)
        assert _lane_gaps_ok(gaps), (N, k, gaps)
    assert mt['print_runs'] == 1 and not closure['open']


def test_parallel_curved_wall_outer_lane_is_continuous():
    """Regression (live review: a discontinuity on the outer lane of a
    curved wall): every direction from the centre meets the outer lane."""
    C = CirclePath(300, 300, 160, id='C')
    L = PF._phys(PrintLayer('t', source_paths=[C]))
    L.wall_systems = [WallSystem('P', 'parallel', {'walls': 4}, ['C'], thickness=T)]
    p, m, closure, mt = _route(L)
    polys = [x.sample_points() + [x.sample_points()[0]] for x in system_paths(p)]
    r_out = 160 - 0.5 * BEAD                       # (inside-aligned ring: outer face = the circle)
    segs = [(a, b) for pl in polys for a, b in zip(pl, pl[1:])]
    misses = []
    for deg in range(0, 360):
        a_ = math.radians(deg + 0.5)
        d = Vec2(math.cos(a_), math.sin(a_))
        best = 0.0
        for a, b in segs:
            t = WSm._cross(Vec2(300, 300), Vec2(300, 300) + d * 400, a, b)
            if t is not None:
                best = max(best, 400 * t)
        if abs(best - r_out) >= 0.3:
            misses.append(deg)
    # (pass 9) only the one seam window (2 beads ≈ 1.1° here) interrupts it
    assert len(misses) <= 2 and (not misses or max(misses) - min(misses) <= 2), misses
    assert mt['print_runs'] == 1 and not closure['open']


def test_parallel_piece_face_lanes_run_end_to_end():
    """A piece cut by an opening: its two face lanes run unbroken along the
    straight walls (the kisses sit on interior lanes)."""
    p, m, closure, mt = _route(_ring({'type': 'parallel', 'walls': 4}, [(100.0, 30.0)]))
    polys = [x.sample_points() + ([x.sample_points()[0]] if x.closed else []) for x in system_paths(p)]
    for y0 in (260 - 0.5 * BEAD, 260 - T + 0.5 * BEAD):           # the bottom wall: no opening
        assert _lane_gaps_ok(_covered(polys, _lane_on_y(y0, 100 + T, 300 - T), 100 + T, 300 - T)), y0


@pytest.mark.parametrize('branch', [('parallel', 10, {'walls': 4}), ('parallel', 6, {'walls': 2}),
                                    ('interleaved', 8, {'paths': 2}), ('chain', 8, {})],
                         ids=['parallel4', 'parallel2', 'interleaved2', 'chain'])


# --- pass 9 (2026-10-07): lane-by-lane Parallel Walls; mixed regions on the reference network --

def _lane_index(q, N):
    """Lane of a point of the 200 × 120 rect ring (T in, inside): by its
    distance from the outer face."""
    dist = min(q.x - 100, 300 - q.x, q.y - 140, 260 - q.y)
    g = (T - BEAD) / (N - 1)
    return round((dist - 0.5 * BEAD) / g)


@pytest.mark.parametrize('N', [3, 4, 5])
def test_parallel_route_is_lane_by_lane_with_one_seam_zone(N):
    """Lane 1 complete → step → lane 2 → … → lane N → return: every segment
    that changes lane lies in ONE seam zone (N − 1 steps + the return)."""
    p, m, closure, mt = _route(_ring({'type': 'parallel', 'walls': N}))
    (x,) = system_paths(p)
    pts = x.sample_points() + [x.sample_points()[0]]
    changes = [(a, b) for a, b in zip(pts, pts[1:]) if _lane_index(a, N) != _lane_index(b, N)]
    assert len(changes) == N                                   # N − 1 steps + 1 return
    mids = [a.lerp(b, 0.5) for a, b in changes]
    assert max(u.dist(v) for u in mids for v in mids) <= 2.0 * BEAD + T
    # the lanes are visited in order 0 → 1 → … → N−1 (then the return)
    seq = [(_lane_index(a, N), _lane_index(b, N)) for a, b in changes]
    steps = sorted(seq, key=lambda t: min(t))
    assert {tuple(sorted(t)) for t in seq} == {(k, k + 1) for k in range(N - 1)} | {(0, N - 1)}
    assert mt['print_runs'] == 1 and mt['retrace_distance'] == 0 and not closure['open']


def test_the_seam_zone_follows_the_route_origin():
    """Move Start: a route origin placed on the wall (its position is stored)
    moves the lane-change seam zone there, and the route starts there."""
    L = _ring({'type': 'parallel', 'walls': 4})
    target = Vec2(220.0, 258.5)                            # on the bottom wall
    L.route_origins = [{'strand': 'W~ws0.0', 'u': 0.0, 'pos': [target.x, target.y]}]
    p, m, closure, mt = _route(L)
    (x,) = system_paths(p)
    pts = x.sample_points() + [x.sample_points()[0]]
    changes = [a.lerp(b, 0.5) for a, b in zip(pts, pts[1:]) if _lane_index(a, 4) != _lane_index(b, 4)]
    assert changes and all(q.dist(target) < 1.5 * T for q in changes), changes
    rl = L.to_routing_layer(p, m)
    from model import resolve_route_origins
    opts, rep_ = resolve_route_origins(rl.strands, [dict(L.route_origins[0], strand=x.id)])
    mv = route_layer(rl, allow_retrace=False, origins=opts)
    assert mv[0].start.dist(target) < 2.0 * BEAD


LINE_TYPES = ['single', 'hollow', 'skin_web', 'parallel', 'interleaved', 'linked', 'chain']


def _refdoc(line_type):
    import reference_network as RN
    d = RN.document('zigzag')
    d['infills'], d['network_walls'] = [], []
    d['material'] = {'bead_width': 3, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
    web = {'pattern': 'zigzag', 'params': {'spacing': 20}}
    params = {'parallel': {'walls': 4}, 'interleaved': {'paths': 4}, 'linked': {'paths': 4}}.get(line_type, {})
    d['wall_systems'] = [
        {'id': 'H', 'name': 'Host', 'type': 'skin_web', 'thickness': 10, 'align': 'auto', 'params': {},
         'members': ['R1', 'R2', 'C1'], 'web': web},
        {'id': 'B', 'name': 'Line', 'type': line_type, 'thickness': 10, 'align': 'auto', 'params': params,
         'members': ['L1'], 'web': web}]
    return d


@pytest.mark.parametrize('line_type', LINE_TYPES)
def test_reference_network_line_through_every_construction(line_type):
    """Regression (live review): circle + rectangles in a Skin + Web system,
    Line 1 (bridging Rect 2 and the circle) switched through every
    construction. No phantom connector walls (Line 1's faces lie on two rings —
    the bridge used to be closed off ring by ring into two flat slivers, and
    the host skins were spliced INTO the line); the host keeps its web; the
    line's generated geometry stays in its own band and meets the host's
    skins at one point per end; one route, no travel."""
    import reference_network as RN
    L = _deserialise_layer(_refdoc(line_type))
    p, m, closure, mt = _route(L)
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and mt['retrace_distance'] == 0
    assert not closure['open']
    web = next(i for i in m['network']['infills'] if i['id'] == 'H~web~R1')
    assert web['status'] == 'ok' and web['regions'] >= 1                      # the host web is printed
    assert any(getattr(x, 'role', '') == 'lattice' for x in p)
    (ax, ay), (bx, by) = RN.LINE
    A, B = Vec2(ax, ay), Vec2(bx, by)

    def dline(q):
        d = B - A
        t = max(0.0, min(1.0, ((q.x - A.x) * d.x + (q.y - A.y) * d.y) / (d.x * d.x + d.y * d.y)))
        return (A + d * t).dist(q)
    for x in system_paths(p):                                               # no geometry elsewhere
        # (pass 10: the line's ends reach into the host to about its centre line)
        assert all(dline(q) <= 5.0 + 0.6 or min(q.dist(A), q.dist(B)) < 12.0 for q in x.sample_points()), \
            (line_type, x.id)
    if line_type in WSm.GENERATED:
        rep = next(r for r in m['network']['wall_systems'] if r.get('system_id') == 'B')
        assert len([j for j in rep.get('junctions', []) if 'crossing_into' in j]) == 2     # both ends


# --- pass 10 (2026-10-07): generated walls meet by CROSSING printed paths -------------------

def _crossing_count(A, B):
    """Proper crossings between two sets of polylines."""
    n = 0
    for pa in A:
        for a, b in zip(pa, pa[1:]):
            for pb in B:
                for c, d in zip(pb, pb[1:]):
                    t = WSm._cross(a, b, c, d)
                    if t is not None and 1e-7 < t < 1 - 1e-7:
                        u = WSm._cross(c, d, a, b)
                        if u is not None and 1e-7 < u < 1 - 1e-7:
                            n += 1
    return n


BRANCHES = {'single': ('single', None, {}), 'hollow': ('hollow', 6, {}),
            'parallel': ('parallel', 10, {'walls': 4}), 'interleaved': ('interleaved', 8, {'paths': 2}),
            'chain': ('chain', 8, {})}


@pytest.mark.parametrize('R', [0.0, 6.0], ids=['R0', 'R6'])
@pytest.mark.parametrize('branch', list(BRANCHES))
@pytest.mark.parametrize('host', list(HOSTS))
def test_t_junction_walls_touch_by_crossing(host, branch, R):
    """A branch meeting a host (any constructions): nothing of the host is
    cut, the branch's lanes keep straight PAST the host's face to about its
    centre line and CROSS the host's printed paths there — real deposited
    contact, solved by the router through the crossings: one route."""
    b = BRANCHES[branch]
    L = _junction(HOSTS[host], 203.0, R, branch=(b[0], b[1]))
    L.wall_systems[1].params = b[2]
    p, m, closure, mt = _route(L)
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and mt['retrace_distance'] == 0
    assert not closure['open']
    sp = system_paths(p)
    closed_ = lambda x: x.sample_points() + ([x.sample_points()[0]] if x.closed else [])
    # (a skin branch's extended loop is emitted with the host's paths: classify by place)
    br_p = [closed_(x) for x in sp if any(q.y < 130 for q in x.sample_points())]
    host_p = [closed_(x) for x in sp if not any(q.y < 130 for q in x.sample_points())]
    assert br_p and host_p
    assert _crossing_count(br_p, host_p) >= 2                               # printed material touches
    deepest = max(q.y for pl in br_p for q in pl)
    assert deepest >= 140 + 0.4 * 10                                        # into the host (≈ its centre)
    # the host's own paths are complete: no gap under the branch
    if host == 'parallel4':
        for k in range(4):
            y0 = round(140 + 1.5 + k * (10 - 3.0) / 3, 6)
            gaps = _covered(host_p, _lane_on_y(y0, 110, 290), 110, 290)
            assert _lane_gaps_ok(gaps, d=0.5 * BEAD), (k, gaps)


def _cross_layer(sa, sb):
    A = LinePath(Vec2(60, 200), Vec2(340, 200), id='A')
    B = LinePath(Vec2(200, 80), Vec2(200, 320), id='B')
    L = PF._phys(PrintLayer('t', source_paths=[A, B]))
    L.wall_systems = [WallSystem('P', sa[0], sa[1], ['A'], thickness=10),
                      WallSystem('Q', sb[0], sb[1], ['B'], thickness=10)]
    return L


@pytest.mark.parametrize('sa,sb', [(('parallel', {'walls': 4}), ('parallel', {'walls': 4})),
                                   (('interleaved', {'paths': 4}), ('linked', {'paths': 4})),
                                   (('parallel', {'walls': 4}), ('chain', {}))],
                         ids=['parallel_x_parallel', 'interleaved_x_linked', 'parallel_x_chain'])
def test_x_junction_both_walls_pass_through_and_cross(sa, sb):
    """An X: both walls keep their native paths straight through the
    crossing square; their crossings connect them — one route."""
    p, m, closure, mt = _route(_cross_layer(sa, sb))
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and not closure['open']
    sp = system_paths(p)
    pa = [x.sample_points() + ([x.sample_points()[0]] if x.closed else []) for x in sp if x.source_id == 'A']
    pb = [x.sample_points() + ([x.sample_points()[0]] if x.closed else []) for x in sp if x.source_id == 'B']
    assert _crossing_count(pa, pb) >= 4
    if sa[0] == 'parallel':                       # wall A's lanes run straight through the square
        for k in range(4):
            y0 = round(200 - 3.5 + k * 7.0 / 3, 6)
            gaps = _covered(pa, _lane_on_y(y0, 150, 250), 150, 250)
            assert _lane_gaps_ok(gaps, d=0.5 * BEAD), (k, gaps)


def test_parallel_branch_into_a_curved_parallel_host():
    C = CirclePath(200, 220, 90, id='C')
    Ln = LinePath(Vec2(200, 130), Vec2(200, 40), id='L1')
    L = PF._phys(PrintLayer('t', source_paths=[C, Ln]))
    L.wall_systems = [WallSystem('H', 'parallel', {'walls': 4}, ['C'], thickness=10),
                      WallSystem('B', 'parallel', {'walls': 4}, ['L1'], thickness=10)]
    p, m, closure, mt = _route(L)
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and not closure['open']
    sp = system_paths(p)
    assert _crossing_count([x.sample_points() for x in sp if x.source_id == 'L1'],
                           [x.sample_points() + [x.sample_points()[0]] for x in sp if x.source_id == 'C']) >= 2


@pytest.mark.parametrize('host', ['skin_web', 'interleaved', 'parallel'])
@pytest.mark.parametrize('line_type', LINE_TYPES)
def test_every_architectural_junction_has_printed_contact(host, line_type):
    """Reference network (both rects + circle in one system, Line 1 in
    another): every pair of walls that touch architecturally has touching
    PRINTED material (crossings, or shared skins), and the whole network
    prints as one route."""
    d = _refdoc(line_type)
    d['wall_systems'][0]['type'] = host
    d['wall_systems'][0]['params'] = {'paths': 4} if host == 'interleaved' else ({'walls': 4} if host == 'parallel' else {})
    L = _deserialise_layer(d)
    p, m, closure, mt = _route(L)
    assert mt['print_runs'] == 1 and mt['travel_distance'] == 0 and not closure['open']
    by = {}
    for x in p:
        pts = x.sample_points() + ([x.sample_points()[0]] if getattr(x, 'closed', False) else [])
        by.setdefault((getattr(x, 'source_id', None) or x.id).split('~')[0], []).append(pts)
    gen = WSm.GENERATED
    kind = {'R1': host, 'R2': host, 'C1': host, 'L1': line_type}
    for a, b in (('R1', 'R2'), ('R1', 'C1'), ('R2', 'L1'), ('C1', 'L1')):
        if kind[a] in gen or kind[b] in gen:
            assert _crossing_count(by.get(a, []), by.get(b, [])) >= 1, (a, b)


def test_parallel_seam_is_compact():
    """The one-zone lane changes take half a bead of each lane (was two
    beads); the steps may cross — every lane is otherwise complete."""
    p, m, closure, mt = _route(_ring({'type': 'parallel', 'walls': 4}))
    (x,) = system_paths(p)
    pts = x.sample_points() + [x.sample_points()[0]]
    changes = [(a, b) for a, b in zip(pts, pts[1:]) if _lane_index(a, 4) != _lane_index(b, 4)]
    span = max(max(abs((a - c).x) + abs((a - c).y), abs((b - c).x) + abs((b - c).y))
               for a, b in changes for c in [changes[0][0]])
    along = max(abs(a.x - b.x) for a, b in changes)               # (top-wall seam: x is along the wall)
    assert along <= 0.5 * BEAD + 0.05, along
    H = 0.5 * T - 0.5 * BEAD
    for k in range(4):
        y0 = round(140 + 0.5 * BEAD + k * 2 * H / 3, 6)
        gaps = _covered([pts], _lane_on_y(y0, 100 + T, 300 - T), 100 + T, 300 - T)
        assert _lane_gaps_ok(gaps, d=0.5 * BEAD), (k, gaps)
