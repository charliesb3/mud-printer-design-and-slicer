"""
Regression (manual test 2026-10-06): a closed SINGLE-BEAD rectangle + one
open single-bead branch + an opening, Physical rules ON, used to route as
one component with 4 odd nodes (1 travel, Start ≠ End, Closed 0/1):
  * the branch became two lanes, but they landed on the rectangle's bead,
    which stayed whole across the branch mouth (degree-3 landings), and
  * the opening left the single-bead rectangle with two dead ends.
Fix (geometry, not routing): a single-bead wall that an opening opens is a
return-lane (two-pass) wall too; a two-pass branch ending on a single-bead
host cuts the host exactly across its mouth and joins it (no U-turn there).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import network as N
from model import Vec2, resolve_route_origins
import physical_fixtures as PF
from graph import route_layer, compute_metrics, build_graph, closure_report

W = 3.0


def route(lay, origins=None):
    p, m = lay._build_effective()
    rl = lay.to_routing_layer(p, m)
    pts, rep = resolve_route_origins(rl.strands, origins or [])
    moves = route_layer(rl, allow_retrace=False, origins=pts)
    return p, m, moves, closure_report(build_graph(rl)), rl


def assert_closed_one_run(moves, cl):
    met = compute_metrics(moves)
    assert cl == {'components': 1, 'closed': 1, 'open': []}
    assert met['print_runs'] == 1 and met['travel_moves'] == 0 and met['retrace_moves'] == 0
    assert (moves[0].start.x, moves[0].start.y) == (moves[-1].end.x, moves[-1].end.y)


@pytest.mark.parametrize('opening', [True, False])
def test_regression_fixture_is_one_closed_route(opening):
    p, m, moves, cl, _ = route(PF.rect_branch(opening=opening))
    assert_closed_one_run(moves, cl)
    assert 'B' in m['network']['return_lanes']
    assert ('R' in m['network']['return_lanes']) == opening   # the opening makes the rect two-pass


@pytest.mark.parametrize('R', [0.0, 0.75, 1.5, 2.5])
@pytest.mark.parametrize('opening', [True, False])
def test_branch_is_two_lanes_return_lane_apart(R, opening):
    p, m, moves, cl, _ = route(PF.rect_branch(opening=opening, R=R))
    assert_closed_one_run(moves, cl)
    lanes = [x.sample_points() for x in p if x.id.startswith('B.wall')]
    assert len(lanes) == 2
    a, b = lanes
    mid = a[0].lerp(a[-1], 0.5)
    assert abs(N.dist_to_polyline(mid, b, False) - (W - R)) < 1e-6
    assert 'B' not in [x.id for x in p]                        # the drawn line: reference only


def test_lanes_join_the_single_bead_host_exactly():
    p, m, moves, cl, rl = route(PF.rect_branch(opening=False))
    host = [x.sample_points() for x in p if x.id.startswith('R')]
    ends = {(q.x, q.y) for h in host for q in (h[0], h[-1])}
    lane_ends = {(x.sample_points()[0].x, x.sample_points()[0].y) for x in p if x.id.startswith('B.wall')}
    assert lane_ends <= ends                                    # host cut exactly at the landings
    assert not any(x.id.startswith('B_cs') for x in p)          # no U-turn at the host end
    assert any(x.id.startswith('B_ce') for x in p)              # the far end turns round
    # the mouth is NOT printed: no printable passes through the branch root
    root = Vec2(300, 200)
    assert all(N.dist_to_polyline(root, s.points, s.closed) > 0.5 for s in rl.strands)


def test_opening_stays_open():
    p, m, moves, cl, rl = route(PF.rect_branch(opening=True))
    gap = Vec2(200, 140)                                        # the opening's centre
    assert all(N.dist_to_polyline(gap, s.points, s.closed) > 10 for s in rl.strands)


@pytest.mark.parametrize('variant', [dict(branch_end=(370, 250)), dict(branch_end=(240, 200)),
                                     dict(host='circle'), dict(opening=True, branch_end=(370, 250))])
def test_generalises(variant):
    if variant.get('host') == 'circle':
        lay = PF.rect_branch(opening=False, host='circle')
    else:
        lay = PF.rect_branch(**{'opening': False, **variant})
    _, _, moves, cl, _ = route(lay)
    assert_closed_one_run(moves, cl)


def test_route_origin_around_the_closed_circuit_changes_only_the_start():
    lay = PF.rect_branch(opening=True)
    _, _, auto, cl, rl0 = route(lay)
    ids = sorted({mv.strand_id for mv in auto if mv.strand_id})
    geom = lambda moves: round(compute_metrics(moves)['print_distance'], 6)
    seen = set()
    for sid in ids:
        for u in (0.3, 0.7):
            _, _, mv, cl, rl = route(lay, [{'strand': sid, 'u': u}])
            assert_closed_one_run(mv, cl)
            assert geom(mv) == geom(auto)
            seen.add((round(mv[0].start.x, 3), round(mv[0].start.y, 3)))
    assert len(seen) > 4                                        # the start really moves


def test_legacy_mode_unchanged():
    lay = PF.rect_branch(opening=True, physical=False)
    p, m, moves, cl, _ = route(lay)
    assert m['network']['return_lanes'] == [] and cl['open']
