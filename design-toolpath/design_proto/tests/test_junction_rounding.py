"""
Rounded wall junctions behave as ONE wall assembly.

Regression for the manual failure (trimmed Circle × Rectangle, one 10 in
network wall, Junctions = Rounded, Junction R swept upward): the inner face
froze at a few inches while the outer face kept growing, and the outer
face kinked once its tangent length passed the rectangle's corner / ran
along the curved circle face. Causes: each face corner was filleted
independently, clamped to 45 % of its own CHAIN (chains are split at plain
arrangement nodes, e.g. where a join extension bead meets the wall bead),
and the arc was built from the first-segment directions with its ends
forced onto the polyline.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from model import Vec2, LinePath, NetworkWall, WallSpec, PrintLayer, Opening
import network as N
from trim_fixtures import circle_rect, pick
import test_infill_junctions as TJ

W = 10.0


def manual_fixture(R, cx=340.0, cy=150.0, rad=95.0, rect=(100, 100, 240, 160), w=W, trim=True):
    """The manual case: Circle + Rectangle, one network wall, the
    overlapping sections trimmed, rounded junctions."""
    base = circle_rect(cx=cx, cy=cy, r=rad, rect=rect)
    base.network_walls = [NetworkWall('NW', 'R', w)]
    trims = [pick(base, 'C', lambda s: s['inside'].get('R') is True, 't1'),
             pick(base, 'R', lambda s: s['inside'].get('C') is True, 't2')] if trim else []
    lay = circle_rect(cx=cx, cy=cy, r=rad, rect=rect, trims=trims)
    lay.network_walls = [NetworkWall('NW', 'R', w)]
    lay.junction_style, lay.junction_radius = 'round', R
    return lay


def L_corner(R, w=W, opening=None):
    """Two thick straight walls meeting end to end (a wall turn, hub)."""
    a = LinePath(Vec2(100, 100), Vec2(300, 100), id='A')
    b = LinePath(Vec2(300, 100), Vec2(300, 300), id='B')
    a.wall = WallSpec(w, 'center')
    b.wall = WallSpec(w, 'center')
    lay = PrintLayer('t', source_paths=[a, b], junction_style='round', junction_radius=R)
    if opening:
        lay.openings = [Opening('o', 'A', *opening)]
    return lay


def build(lay):
    return lay._build_effective()


def corners(meta):
    return [j for j in meta['network']['junctions'] if j['corner']]


def circle_through(pts):
    a, b, c = pts[0], pts[len(pts) // 2], pts[-1]
    d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    ux = ((a.x ** 2 + a.y ** 2) * (b.y - c.y) + (b.x ** 2 + b.y ** 2) * (c.y - a.y)
          + (c.x ** 2 + c.y ** 2) * (a.y - b.y)) / d
    uy = ((a.x ** 2 + a.y ** 2) * (c.x - b.x) + (b.x ** 2 + b.y ** 2) * (a.x - c.x)
          + (c.x ** 2 + c.y ** 2) * (b.x - a.x)) / d
    return Vec2(ux, uy), math.hypot(a.x - ux, a.y - uy)


def arc_circle(pts):
    """Centre / radius of a fillet arc from its INTERIOR samples (its two
    end points are the tangent feet on the sampled faces)."""
    return circle_through([pts[1], pts[len(pts) // 2], pts[-2]] if len(pts) >= 5 else pts)


def wall_paths(paths):
    return [p for p in paths if p.role != 'lattice' and len(p.sample_points()) >= 2]


def check_invariants(lay, expect_runs=None):
    """Continuity, no tiny pieces, no self-intersection, tangency,
    concentric assemblies, connected toolpath."""
    paths, meta = build(lay)
    walls = wall_paths(paths)
    faces = [p for p in walls if meta['cls'].get(id(p)) in (N.FACE, N.WIRE)]
    # 2. no malformed tiny pieces
    for p in walls:
        assert N._poly_len(p.sample_points()) > 1e-3, f'tiny piece {p.id}'
    # 3. no self-intersection: no face properly crosses a face
    beads = [N.Bead(p.id, p.sample_points(), p.closed, N.FACE, p.id) for p in faces]
    segs = N._seg_list(beads)
    events = [e for e in N._contact_events(segs, beads, 1e-7) if e[3] is not None]
    assert events == [], f'{len(events)} crossings, e.g. {events[0][2]}'
    # 1. continuity: every open face piece end meets another face end exactly
    ends = {}
    for p in faces:
        if p.closed:
            continue
        for q in (p.sample_points()[0], p.sample_points()[-1]):
            ends[(q.x, q.y)] = ends.get((q.x, q.y), 0) + 1
    assert all(n % 2 == 0 for n in ends.values()), 'a face is broken (dangling end)'
    # 4. tangency at every arc end
    arcs = {p.id[len('junction:'):]: p for p in paths if p.id.startswith('junction:')}
    for key, arc in arcs.items():
        pts = arc.sample_points()
        for end, nxt in ((pts[0], pts[1]), (pts[-1], pts[-2])):
            t_arc = (end - nxt).normalized()          # arc leaving at this end
            nb = [p for p in faces if p is not arc and
                  any(q.x == end.x and q.y == end.y for q in (p.sample_points()[0], p.sample_points()[-1]))]
            assert nb, f'arc {key} end not connected'
            angs = []
            for n_ in nb:                              # the face it continues into
                q = n_.sample_points()
                d = (q[1] - q[0]) if (q[0].x, q[0].y) == (end.x, end.y) else (q[-2] - q[-1])
                d = d.normalized()
                angs.append(math.degrees(math.acos(max(-1.0, min(1.0, t_arc.x * d.x + t_arc.y * d.y)))))
            assert min(angs) < 3.0, f'arc {key} meets its leg at {min(angs):.1f}°'
    # 5./6. assemblies: concentric, radii differ by the wall thickness
    js = {j['key']: j for j in corners(meta)}
    for j in js.values():
        if j['partner'] and not j['derived'] and j['partner'] in arcs and j['key'] in arcs:
            c1, r1 = arc_circle(arcs[j['key']].sample_points())
            c2, r2 = arc_circle(arcs[j['partner']].sample_points())
            assert c1.dist(c2) < 0.05 * max(1.0, r1) and abs((r1 - r2) - wall_thickness(lay)) < 0.3, \
                (j['key'], c1, c2, r1, r2)
    # 9. connected toolpath
    if expect_runs is not None:
        from graph import route_layer, compute_metrics
        m = compute_metrics(route_layer(lay.to_routing_layer(paths, meta)))
        assert m['print_runs'] == expect_runs and m['retrace_moves'] == 0, m
    return paths, meta


def wall_thickness(lay):
    if lay.network_walls:
        return lay.network_walls[0].thickness
    return next(p.wall.thickness for p in lay.source_paths if p.wall)


# ---------------------------------------------------------------------------
# The manual failure
# ---------------------------------------------------------------------------

SWEEP = [1.0, 2.0, 5.0, 9.0, 10.0, 12.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 150.0, 400.0]


@pytest.mark.parametrize('R', SWEEP)
def test_manual_fixture_invariants(R):
    lay = manual_fixture(R)
    _, meta = check_invariants(lay, expect_runs=2)   # two closed faces, no infill
    cs = corners(meta)
    assert len(cs) == 4
    assert sum(1 for j in cs if j['derived']) == 2   # two wall turns, each a pair


def test_manual_fixture_faces_never_diverge():
    """7./8. Actual radii grow monotonically and stop TOGETHER at the
    assembly's geometric limit; beyond it nothing changes."""
    hist = {}
    for R in SWEEP:
        _, meta = build(manual_fixture(R))
        for j in corners(meta):
            hist.setdefault(j['key'], []).append((R, j['actual_radius'], j['limited'], j['derived']))
    for key, h in hist.items():
        acts = [a for _, a, _, _ in h]
        assert all(b >= a - 1e-6 for a, b in zip(acts, acts[1:])), (key, acts)
        for R, a, lim, der in h:
            if not der:
                assert a <= R + 1e-9 and (lim or abs(a - R) < 1e-6), (key, R, a)
    # each outer / inner pair reacts together: when one is limited, so is
    # its partner, and the derived radius = outer − thickness (or sharp)
    for R in SWEEP:
        _, meta = build(manual_fixture(R))
        js = {j['key']: j for j in corners(meta)}
        for j in js.values():
            if j['derived']:
                o = js[j['partner']]
                assert j['limited'] == o['limited']
                assert abs(j['actual_radius'] - max(0.0, o['actual_radius'] - W)) < 0.3, (R, o, j)


def test_manual_fixture_limit_is_stable():
    p1, m1 = build(manual_fixture(150.0))
    p2, m2 = build(manual_fixture(400.0))
    lim = [j for j in corners(m1) if j['limited']]
    assert lim, 'the rectangle corner limits one wall turn'
    key = lambda ps: {p.id: [(round(q.x, 6), round(q.y, 6)) for q in p.sample_points()]
                      for p in ps if p.id.startswith('junction:')}
    a, b = key(p1), key(p2)
    for j in lim:
        for k in (j['key'], j['partner']):
            if 'junction:' + k in a:
                assert a['junction:' + k] == b['junction:' + k]


@pytest.mark.parametrize('cx,cy,rad', [(330, 150, 95), (340, 160, 90), (350, 140, 100)])
def test_moved_resized_circle_after_trim(cx, cy, rad):
    for R in (5.0, 20.0, 60.0, 400.0):
        check_invariants(manual_fixture(R, cx=cx, cy=cy, rad=rad), expect_runs=2)


@pytest.mark.parametrize('rect', [(110, 90, 240, 160), (100, 100, 250, 180)])
def test_moved_resized_rect_after_trim(rect):
    for R in (5.0, 20.0, 60.0, 400.0):
        check_invariants(manual_fixture(R, rect=rect), expect_runs=2)


@pytest.mark.parametrize('w', [6.0, 16.0])
def test_other_wall_thicknesses(w):
    for R in (3.0, 20.0, 60.0, 400.0):
        lay = manual_fixture(R, w=w)
        _, meta = check_invariants(lay, expect_runs=2)
        js = {j['key']: j for j in corners(meta)}
        for j in js.values():
            if j['derived']:
                assert abs(j['actual_radius'] - max(0.0, js[j['partner']]['actual_radius'] - w)) < 0.3


@pytest.mark.parametrize('R', [2.0, 20.0, 400.0])
def test_without_trim(R):
    check_invariants(manual_fixture(R, trim=False))


# ---------------------------------------------------------------------------
# General wall junctions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('R', [0.0, 3.0, 10.0, 25.0, 80.0, 1000.0])
def test_L_corner_is_one_concentric_assembly(R):
    paths, meta = check_invariants(L_corner(R))
    cs = corners(meta)
    if R == 0:
        assert not [p for p in paths if p.id.startswith('junction:')]
        return
    outer = [j for j in cs if not j['derived'] and j['partner']]
    assert len(outer) == 1
    o = outer[0]
    i = next(j for j in cs if j['key'] == o['partner'])
    # the inner face is concentric: R − W, sharp while R ≤ W
    assert abs(i['actual_radius'] - max(0.0, o['actual_radius'] - W)) < 1e-6
    if R <= W:
        assert not [p for p in paths if p.id == 'junction:' + i['key']]


def test_L_corner_huge_radius_is_limited_coherently():
    _, m = build(L_corner(1000.0))
    o = next(j for j in corners(m) if not j['derived'] and j['partner'])
    assert o['limited'] and o['actual_radius'] < 1000.0
    _, m2 = build(L_corner(5000.0))
    o2 = next(j for j in corners(m2) if not j['derived'] and j['partner'])
    assert abs(o2['actual_radius'] - o['actual_radius']) < 1e-6


def test_miter_equals_zero_radius():
    a, _ = build(L_corner(0.0))
    lay = L_corner(0.0)
    lay.junction_style = 'miter'
    b, _ = build(lay)
    assert [(p.id, [(q.x, q.y) for q in p.sample_points()]) for p in a] == \
           [(p.id, [(q.x, q.y) for q in p.sample_points()]) for p in b]


@pytest.mark.parametrize('R', [2.0, 8.0, 30.0])
def test_T_corners_are_rounded_with_R_itself(R):
    lay = TJ.T_LAYER(branch_end=(200, 40), junction_style='round', junction_radius=R)
    paths, meta = check_invariants(lay)
    for j in corners(meta):
        assert not j['derived'] and j['partner'] is None
        arc = next(p for p in paths if p.id == 'junction:' + j['key'])
        assert abs(circle_through(arc.sample_points())[1] - j['actual_radius']) < 1e-3


def test_short_join_bead_no_longer_clamps_a_T_corner():
    """Before: 2 in requested built a 1.45 in arc (45 % of the 0.9 in join
    extension chain). The face continues past that chain split."""
    lay = TJ.T_LAYER(junction_style='round', junction_radius=2)
    paths, meta = check_invariants(lay)
    arc = next(p for p in paths if p.id == 'junction:Br|R#0')
    assert abs(TJ._circle_radius(arc.sample_points()) - 2.0) < 1e-3


@pytest.mark.parametrize('R', [3.0, 12.0])
def test_multi_arm_junctions(R):
    check_invariants(TJ.Y_LAYER(junction_style='round', junction_radius=R))
    check_invariants(TJ.X_LAYER(junction_style='round', junction_radius=R))


def test_opening_near_a_rounded_corner():
    for R in (5.0, 30.0):
        lay = L_corner(R, opening=(150.0, 30.0))
        paths, meta = check_invariants(lay)
        gap = Vec2(250, 100)
        assert all(min(q.dist(gap) for q in p.sample_points()) > 10
                   for p in paths if p.id.startswith('A~') or p.id == 'A')
