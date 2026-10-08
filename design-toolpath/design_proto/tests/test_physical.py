"""
Physical-bead rules (Material / Bead, 2026-10-06): Contact Overlap, Return-
Lane Overlap, NO exact retrace, every connected component one closed route.
Checks physical behaviour (centreline separations, bead overlap), not only
graph legality. Fixtures: tests/physical_fixtures.py.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import network as N
from model import Vec2, WallSpec, Opening, RegionInfill, NetworkWall
from material import MaterialSpec
import physical_fixtures as PF
from trim_fixtures import circle_rect, pick
from graph import route_layer, compute_metrics, build_graph, closure_report

W = 3.0


def build(lay):
    return lay._build_effective()


def route(lay):
    p, m = build(lay)
    rl = lay.to_routing_layer(p, m)
    moves = route_layer(rl, allow_retrace=not lay.material.physical)
    return p, m, moves, closure_report(build_graph(rl))


def lattice(m):
    return [r for v in (m['network'].get('lattice') or {}).values() for r in v['regions']]


def faces(p, m):
    return [x.sample_points() for x in p if m['cls'].get(id(x)) == N.FACE]


def landing_minima(p, m):
    """Local minima of the lattice's distance to the face centrelines
    (where a stitch lands), excluding exact route connections (≈ 0)."""
    fs = faces(p, m)
    out = []
    for x in p:
        if getattr(x, 'treatment_id', '') != 'infill':
            continue
        q = x.sample_points()
        ds = [min(N.dist_to_polyline(v, f, False) for f in fs) for v in q]
        for i, d in enumerate(ds):
            if d <= (ds[i - 1] if i else 1e9) and d <= (ds[i + 1] if i + 1 < len(ds) else 1e9) and d > 0.05:
                out.append(d)
    return out


ALL = ['wave_ring', 'lone_wall', 'x_walls', 'dead_end_line', 'thin_x', 'star', 'islands', 'dead_end_branch']


# ---------------------------------------------------------------------------
# Closed routes, no retrace
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ALL)
def test_every_component_is_one_closed_route_without_retrace(name):
    p, m, moves, cl = route(getattr(PF, name)())
    met = compute_metrics(moves)
    assert cl['open'] == [], cl                      # every node even: closable
    assert met['retrace_moves'] == 0                 # no exact print retrace
    assert met['travel_moves'] == cl['components'] - 1   # travel only BETWEEN components
    runs, cur = [], []
    for mv in moves:
        if mv.kind == 'travel':
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(mv)
    runs.append(cur)
    assert len(runs) == cl['components']
    for r in runs:                                    # start = end, by the print itself
        assert (r[0].start.x, r[0].start.y) == (r[-1].end.x, r[-1].end.y)


@pytest.mark.parametrize('name', ['lone_wall', 'dead_end_line', 'thin_x', 'islands'])
def test_legacy_mode_shows_what_physical_rules_fix(name):
    """Control: with zero-width (legacy) rules these fixtures are open or
    retrace — the physical rules are what closes them."""
    lay = getattr(PF, name)(physical=False)
    _, _, moves, cl = route(lay)
    assert cl['open'] or compute_metrics(moves)['retrace_moves'] > 0


def test_router_never_retraces_and_reports_an_unclosable_component():
    """Geometry the generators could not close (here: physical rules off
    for the geometry, router in no-retrace mode) is paired by visible
    travel, never printed over."""
    lay = PF.thin_x(physical=False)
    p, m = build(lay)
    rl = lay.to_routing_layer(p, m)
    met = compute_metrics(route_layer(rl, allow_retrace=False))
    assert met['retrace_moves'] == 0 and met['travel_moves'] > 0
    assert closure_report(build_graph(rl))['open']


# ---------------------------------------------------------------------------
# Contact Overlap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name,pat', [('wave_ring', 'zigzag'), ('wave_ring', 'wave'),
                                      ('lone_wall', 'zigzag'), ('lone_wall', 'wave')])
@pytest.mark.parametrize('O', [0.75, 1.5, 2.25])
def test_contact_landings_reach_the_requested_overlap(name, pat, O):
    p, m = build(getattr(PF, name)(pat=pat, O=O))
    sep = W - O
    mins = landing_minima(p, m)
    assert mins
    good = [d for d in mins if abs(d - sep) < 0.06 * sep + 0.02]
    assert len(good) >= 0.85 * len(mins), (sep, sorted(mins))
    assert all(d > 0.9 * sep for d in mins), (sep, sorted(mins))
    rep = lattice(m)[0]
    assert rep['contact_violations'] == 0 and abs(rep['contact'] - sep) < 1e-9


def test_corner_motif_keeps_the_contact_at_a_ring_corner():
    """(2026-10-07) The canonical single-pass corner motif sets its
    contacts squarely off each face leg: the ring's reflex corners no
    longer fall short of a large contact separation (they used to — the
    case was in the shortfall test below)."""
    p, m = build(PF.wave_ring(pat='wave', O=0.25))
    assert lattice(m)[0]['contact_violations'] == 0


@pytest.mark.parametrize('name,pat', [('lone_wall', 'zigzag'), ('lone_wall', 'wave')])
def test_contact_shortfall_is_reported_not_hidden(name, pat):
    """Limitation: with a large separation (O = 0.25 → 2.75 in in a 10 in
    wall) a reflex corner / cap V cannot keep the full clearance; every
    such point is COUNTED in the report."""
    p, m = build(getattr(PF, name)(pat=pat, O=0.25))
    sep = W - 0.25
    rep = lattice(m)[0]
    close = [d for d in landing_minima(p, m) if d < 0.98 * sep]
    assert rep['contact_violations'] > 0 and close
    assert rep['contact_min'] <= min(close) + 1e-3       # (report rounds to 0.001)


def test_changing_contact_overlap_moves_the_landings():
    a = landing_minima(*build(PF.lone_wall(O=0.5)))
    b = landing_minima(*build(PF.lone_wall(O=1.75)))
    med = lambda xs: sorted(xs)[len(xs) // 2]
    assert abs((med(a) - med(b)) - 1.25) < 0.05


def test_full_contact_overlap_lands_on_the_face_centreline():
    p, m = build(PF.lone_wall(O=3.0))
    assert landing_minima(p, m) == []                 # every landing at ≈ 0


def test_contact_overlap_does_not_touch_crossings():
    """Out-and-back phases cross mid-wall (structural crossings) at any O;
    wall faces (and their X) do not move with O."""
    for O in (0.25, 2.0):
        p, m = build(PF.x_walls(O=O))
        lat = [x.sample_points() for x in p if getattr(x, 'treatment_id', '') == 'infill']
        beads = [N.Bead(f'l{k}', q, False, N.INTERNAL, f'l{k}') for k, q in enumerate(lat)]
        segs = N._seg_list(beads)
        crossings = [e for e in N._contact_events(segs, beads) if e[3] is not None]
        assert len(crossings) > 10
    key = lambda lay: sorted((x.id, [(round(q.x, 6), round(q.y, 6)) for q in x.sample_points()])
                             for x in build(lay)[0] if getattr(x, 'treatment_id', '') != 'infill')
    assert key(PF.x_walls(O=0.25)) == key(PF.x_walls(O=2.0))


def test_transfers_connect_lattice_and_faces():
    _, m = build(PF.wave_ring())
    assert lattice(m)[0]['transfers'] == 2            # one per face ring
    _, m = build(PF.lone_wall())
    assert lattice(m)[0]['transfers'] == 1


# ---------------------------------------------------------------------------
# Return-Lane Overlap / dead-end walls
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('R', [0.0, 0.5, 0.75, 1.5, 2.5, 2.75])
def test_single_line_wall_becomes_two_passes_return_lane_apart(R):
    lay = PF.dead_end_line(R=R)
    p, m = build(lay)
    assert m['network']['return_lanes'] == ['L']
    ids = [x.id for x in p]
    assert 'L' not in ids                              # the drawn line is the reference
    a = next(x for x in p if x.id == 'L.wall+').sample_points()
    b = next(x for x in p if x.id == 'L.wall-').sample_points()
    sep = max(0.25, W - R)
    assert abs(N.dist_to_polyline(a[len(a) // 2], b, False) - sep) < 1e-9
    # U-turn: semicircular caps of radius sep / 2
    for cap in (x for x in p if x.id in ('L_cs', 'L_ce')):
        q = cap.sample_points()
        c = q[0].lerp(q[-1], 0.5)
        assert all(abs(v.dist(c) - sep / 2) < 1e-6 for v in q)
    # physical width of the two-pass wall ≈ 2W − R
    pr = lay.printable_centerlines(p, m)
    ys = [y for c in pr for _, y in c['pts']]
    assert abs((max(ys) - min(ys)) + W - lay.material.two_pass_width()) < 1e-6


def test_return_lane_and_contact_are_independent():
    key = lambda lay: [(x.id, [(round(q.x, 6), round(q.y, 6)) for q in x.sample_points()])
                       for x in build(lay)[0]]
    assert key(PF.dead_end_line(O=0.0)) == key(PF.dead_end_line(O=2.5))      # O: no effect
    assert key(PF.lone_wall(R=0.0)) == key(PF.lone_wall(R=2.5))              # R: no effect


def test_two_pass_walls_join_as_a_network():
    _, m, moves, cl = route(PF.thin_x())
    assert m['network']['return_lanes'] == ['A', 'B'] and cl['components'] == 1 and not cl['open']


def test_wall_with_thickness_is_not_turned_into_a_return_lane():
    lay = PF.dead_end_line()
    lay.source_paths[0].wall = WallSpec(10, 'center')
    _, m = build(lay)
    assert m['network']['return_lanes'] == []


def test_closed_single_bead_path_stays_one_bead():
    lay = circle_rect()
    lay.material = PF.mat()
    _, m = build(lay)
    assert m['network']['return_lanes'] == []


# ---------------------------------------------------------------------------
# Lattice second wave
# ---------------------------------------------------------------------------

def test_lone_wall_lattice_gets_a_closed_second_wave():
    p, m = build(PF.lone_wall())
    assert [r['motif'] for r in lattice(m)[0]['runs']] == ['lone_loop']
    lat = [x.sample_points() for x in p if getattr(x, 'treatment_id', '') == 'infill']
    assert len(lat) == 1 and lat[0][0].dist(lat[0][-1]) < 1e-9     # one closed loop
    p0, m0 = build(PF.lone_wall(physical=False))
    assert [r['motif'] for r in lattice(m0)[0]['runs']] == ['lone']  # legacy: one open pass


# ---------------------------------------------------------------------------
# Existing features under physical rules
# ---------------------------------------------------------------------------

def test_trim_and_rounded_junctions_under_physical_rules():
    base = circle_rect(wall=10, cy=150, r=95)          # contacts off the rect corners
    lay = circle_rect(wall=10, cy=150, r=95,
                      trims=[pick(base, 'C', lambda s: s['inside'].get('R') is True, 't1'),
                             pick(base, 'R', lambda s: s['inside'].get('C') is True, 't2')])
    lay.junction_style, lay.junction_radius = 'round', 20
    lay.infills = [RegionInfill('I', 'C', 'wave', {'spacing': 20})]
    lay.material = PF.mat()
    p, m, moves, cl = route(lay)
    assert not cl['open'] and compute_metrics(moves)['retrace_moves'] == 0
    assert all(v['status'] == 'ok' for v in m['network']['trims'].values())
    assert any(x.id.startswith('junction:') for x in p)


def test_opening_under_physical_rules():
    lay = PF.wave_ring()
    lay.openings = [Opening('o', 'R', 100.0, 30.0)]
    p, m, moves, cl = route(lay)
    assert not cl['open'] and compute_metrics(moves)['retrace_moves'] == 0


def test_api_physical_route():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    payload = {'id': 'l', 'source_paths': [
        {'id': 'L', 'type': 'LinePath', 'start': [100, 200], 'end': [300, 200]}],
        'offset_treatments': [], 'lattice_instances': [],
        'material': {'bead_width': 3, 'contact_overlap': 0.75, 'return_overlap': 1.0, 'physical': True}}
    with flask_app.test_client() as c:
        d = c.post('/api/route', json=payload).get_json()
        assert d['closure'] == {'components': 1, 'closed': 1, 'open': [], 'physical': True}
        assert d['metrics']['retrace_moves'] == 0 and d['metrics']['travel_moves'] == 0
        assert d['material']['return_overlap'] == 1.0
        assert 'L' not in [x['id'] for x in d['printable']]
