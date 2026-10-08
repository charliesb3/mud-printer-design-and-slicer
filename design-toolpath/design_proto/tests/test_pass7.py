"""
Pass 7 — structural infill rules (geometric tests):

  WALL lattice   corners as first-class features (brace / pinned corner
                 points), maximum unsupported distance, combined-density
                 out-and-back, cap-V turnaround / end support.
  SOLID infill   boundary contact (turns land ON the outer and void
                 boundaries), the 'serpentine' pattern, voids empty, no retrace.
  Wall relations two nested closed boundaries defining a wall thickness.

Pattern parameters are preferences; structural support and topology are
constraints — these tests measure the produced geometry, not labels.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app as design_app
import route_quality as RQ
import solid as SO
import wall_lattice as WL
import wall_fixtures as WF
from model import (Vec2, PrintLayer, RectanglePath, CirclePath, EllipsePath, LinePath,
                   RegionInfill, WallRelation, InsetPath, WallSpec)
from tests.test_networks import _assert_internal_inside

_C = {}


def CM(name, sp=None):
    key = (name, sp)
    if key not in _C:
        fn = WF.CORNER_FIXTURES[name]
        L = fn() if sp is None else fn(sp)
        _C[key] = (L, RQ.wall_metrics(L))
    return _C[key]


def _regions(L):
    return L.network_summary()['lattice']['I']['regions']


# ---------------------------------------------------------------------------
# 1. Corners: first-class structural features
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', list(WF.CORNER_FIXTURES))
def test_corner_fixture_quality(name):
    L, q = CM(name)
    assert q['interior_retrace'] < 1e-6
    assert q['tiny_cells'] == 0 and q['congestion_hotspots'] == 0
    assert q['cross_ratio'] >= 0.95
    _assert_internal_inside(L)


@pytest.mark.parametrize('name', [n for n in WF.CORNER_FIXTURES if 'rounded' not in n])
def test_every_sharp_corner_is_supported(name):
    # every sharp vertex of the wall boundary (wall corners, junction
    # corners, cap corners) has a lattice landing within one thickness.
    # (2026-10-07, canonical corner motifs: a single-pass corner is landed
    # on BOTH sides of the outer and the inner corner — MOTIF_OUTER /
    # MOTIF_INNER × t away — a double-pass corner exactly at both corner
    # points; pass 7 landed every wall corner exactly with one brace.)
    L, q = CM(name)
    cs = RQ.corner_support(L)
    assert cs, 'fixture has corners'
    assert max(c[3] for c in cs) <= q['thickness'] + 1e-6
    regs = _regions(L)
    if any(run['corners'] for r in regs for run in r['runs']):
        assert min(c[3] for c in cs) <= WL.MOTIF_OUTER * q['thickness'] + 1e-6


def test_rounded_corners_are_braced_at_their_apexes():
    # (2026-10-07) a tight rounded corner is still a CORNER: its canonical
    # single motif lands both sides of the outer arc's apex and of the
    # inner arc's apex (pass 7 landed the two apexes with one brace)
    L, q = CM('rounded rect wall')
    paths, _ = L._build_effective()
    lands = [p for x in paths if getattr(x, 'treatment_id', '') == 'infill' for p in x.sample_points()]
    # outer arcs: R 20 round (100,140)-(300,260); inner: R 10 (offset 10)
    for (cx, cy, sx, sy) in ((120, 160, -1, -1), (280, 160, 1, -1), (280, 240, 1, 1), (120, 240, -1, 1)):
        k = math.sqrt(0.5)
        outer_apex = Vec2(cx + sx * 20 * k, cy + sy * 20 * k)
        inner_apex = Vec2(cx + sx * 10 * k, cy + sy * 10 * k)
        near_o = [p for p in lands if 1.0 < outer_apex.dist(p) < 0.5 * q['thickness'] and
                  abs(math.hypot(p.x - cx, p.y - cy) - 20) < 0.3]
        near_i = [p for p in lands if 1.0 < inner_apex.dist(p) < 0.8 * q['thickness'] and
                  abs(math.hypot(p.x - cx, p.y - cy) - 10) < 0.5]
        # one landing either side of each apex (the motif flanks it)
        side = lambda p, a: (p.x - a.x) * sy - (p.y - a.y) * sx
        assert {side(p, outer_apex) > 0 for p in near_o} == {True, False}
        assert {side(p, inner_apex) > 0 for p in near_i} == {True, False}


@pytest.mark.parametrize('name', ['rect wall', 'rect with one void', 'acute (triangle) wall',
                                  'obtuse (hexagon) wall', 'curve into corner'])
def test_corner_support_does_not_depend_on_spacing(name):
    for sp in (12, 14, 16, 18, 20, 22, 24):
        L, q = CM(name, sp)
        cs = RQ.corner_support(L)
        assert max(c[3] for c in cs) <= q['thickness'] + 1e-6, sp
        assert q['interior_retrace'] < 1e-6 and q['tiny_cells'] == 0 and q['congestion_hotspots'] == 0


def test_corner_brace_is_a_diagonal_not_a_box():
    # SUPERSEDED 2026-10-07 (canonical corner motifs): a single-pass corner
    # is no longer one outer ↔ inner diagonal but the motif Ia → Oa → Ob → Ib
    # (tests/test_corner_motifs.py). What remains: no tiny box / bow tie
    # cell at the corner, and every motif landing on a face.
    L, q = CM('rect wall')
    assert q['tiny_cells'] == 0 and q['congestion_hotspots'] == 0
    regs = _regions(L)
    assert sum(r['corner_motifs']['single'] for r in regs) == 4


# ---------------------------------------------------------------------------
# 2. Maximum unsupported distance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', list(WF.FIXTURES) + list(WF.CORNER_FIXTURES))
def test_max_unsupported_never_exceeded(name):
    fn = WF.FIXTURES.get(name) or WF.CORNER_FIXTURES[name]
    for sp in (12, 18, 24):
        regs = _regions(fn(sp))
        for r in regs:
            assert r['max_unsupported_limit'] == pytest.approx(WL.DMAX_RATIO * sp)
            assert r['max_unsupported'] <= r['max_unsupported_limit'] + 1e-6


def _stations_on_x_arm(L, y0=195, y1=205, x0=206):
    paths, _ = L._build_effective()
    pts = [q for p in paths if getattr(p, 'treatment_id', '') == 'infill' for q in p.sample_points()]
    return sorted({round(p.x, 6) for p in pts if (abs(p.y - y0) < 1e-6 or abs(p.y - y1) < 1e-6) and p.x > x0})


def test_user_max_unsupported_wins_over_target():
    L = WF.arms(4, sp=20)
    L.infills[0].params['max_unsupported'] = 14
    st = _stations_on_x_arm(L)
    gaps = [b - a for a, b in zip(st, st[1:])]
    assert max(gaps) <= 14 + 1e-6
    reg = _regions(L)[0]
    assert reg['max_unsupported_limit'] == 14 and reg['max_unsupported'] <= 14 + 1e-6
    # redistributed (more stitches), not a tiny corrective stitch at the end
    assert min(gaps) > 0.5 * max(gaps)


# ---------------------------------------------------------------------------
# 3 / 4. Out-and-back density and the cap-V turnaround
# ---------------------------------------------------------------------------

def test_out_and_back_density_is_the_target():
    for sp in (14, 20):
        L = WF.arms(4, sp=sp)
        st = _stations_on_x_arm(L)
        # combined supports ≈ arm length / target (not twice that)
        assert 0.75 * 105 / sp <= len(st) - 1 <= 1.3 * 105 / sp + 1


def test_turnaround_lands_on_the_cap_and_is_not_a_rung():
    L = WF.arms(4, sp=20)
    paths, _ = L._build_effective()
    pts = [q for p in paths if getattr(p, 'treatment_id', '') == 'infill' for q in p.sample_points()]
    # the +x arm's cap is the face x = 310 (y 195…205): one landing near its middle
    cap = [p for p in pts if abs(p.x - 310) < 1e-6]
    assert cap and all(197 < p.y < 203 for p in cap)
    # no rung: no generated segment straight across the wall at one station
    for p in paths:
        if getattr(p, 'treatment_id', '') != 'infill':
            continue
        sp_ = p.sample_points()
        for a, b in zip(sp_, sp_[1:]):
            if a.x > 260 and abs(a.x - b.x) < 1e-6 and abs(abs(a.y - b.y) - 10) < 1e-6:
                pytest.fail(f'rung at x={a.x}')


def test_lone_wall_ends_are_supported_and_route_stays_open():
    L = WF.straight()
    q = RQ.wall_metrics(L)
    cs = RQ.corner_support(L)
    assert max(c[3] for c in cs) <= q['thickness'] + 1e-6       # cap corners
    assert q['start_end_distance'] > 200                        # still an open zigzag
    run = _regions(L)[0]['runs'][0]
    assert run['motif'] == 'lone' and run['passes'] == 1


# ---------------------------------------------------------------------------
# 5 / 6. Solid infill: boundary contact, both patterns
# ---------------------------------------------------------------------------

def _solid(outer, voids=(), pat='rectilinear', **prm):
    params = {'spacing': 16, 'angle': 45}
    params.update(prm)
    return PrintLayer('t', source_paths=[outer, *voids],
                      infills=[RegionInfill('S', outer.id, pat, params, kind='solid')])


SOLIDS = {
    'rectangle': lambda pat: _solid(RectanglePath(40, 40, 320, 220, id='O'), pat=pat),
    'ellipse': lambda pat: _solid(EllipsePath(200, 150, 160, 100, id='O'), pat=pat, angle=0),
    'rect + circular void': lambda pat: _solid(RectanglePath(40, 40, 320, 220, id='O'),
                                               [CirclePath(200, 150, 40, id='V')], pat=pat),
    'ellipse + circular void': lambda pat: _solid(EllipsePath(200, 150, 160, 100, id='O'),
                                                  [CirclePath(200, 150, 35, id='V')], pat=pat, angle=0),
    'multiple voids': lambda pat: _solid(RectanglePath(0, 0, 320, 200, id='O'),
                                         [CirclePath(80, 100, 30, id='A'), CirclePath(240, 100, 30, id='B'),
                                          RectanglePath(140, 60, 40, 80, id='C')], pat=pat),
    'nested island': lambda pat: PrintLayer('t', source_paths=[
        EllipsePath(200, 200, 50, 30, id='E'), RectanglePath(80, 110, 240, 180, id='O'),
        InsetPath('O', 10, id='C')],
        infills=[RegionInfill('S', 'O', pat, {'spacing': 16, 'angle': 30}, kind='solid')]),
}


@pytest.mark.parametrize('pat', ['rectilinear', 'serpentine'])
@pytest.mark.parametrize('name', list(SOLIDS))
def test_solid_attaches_to_its_boundaries(name, pat):
    L = SOLIDS[name](pat)
    m = RQ.solid_metrics(L)
    assert m['void_violations'] == 0
    assert m['exact_retrace'] < 1e-6
    assert m['congestion_hotspots'] == 0
    assert m['rings'] and all(r['contacts'] >= 1 for r in m['rings'])   # outer AND every void
    outer = [r for r in m['rings'] if not r['void']]
    for r in outer:                                   # repeated contact all round
        assert r['contacts'] >= r['length'] / (8 * 16)
    assert m['longest_connector'] <= SO.JOIN_MAX * 16 + 1e-6
    info = next(i for i in L.network_summary()['infills'] if i['id'] == 'S')
    assert info['solid']['untouched_boundaries'] == 0
    n_voids = sum(1 for r in m['rings'] if r['void'])
    n_regions = sum(1 for r in m['rings'] if not r['void'])
    assert m['runs'] <= n_regions + 2 * n_voids       # short travel only where split


def test_solid_turns_land_on_the_boundary_not_short_of_it():
    # rectangle at angle 0: every turn apex lies ON the left / right edge
    L = _solid(RectanglePath(0, 0, 200, 160, id='O'), spacing=20, angle=0)
    paths, _ = L._build_effective()
    pts = [q for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill' for q in p.sample_points()]
    left = [q for q in pts if abs(q.x) < 1e-6]
    right = [q for q in pts if abs(q.x - 200) < 1e-6]
    assert len(left) >= 3 and len(right) >= 3
    # between lines (mid-hatch), never several on one point
    ys = sorted(round(q.y, 6) for q in left + right)
    assert len(ys) == len(set(ys))


def test_solid_serpentine_is_smooth_and_bounded():
    L = _solid(RectanglePath(0, 0, 300, 200, id='O'), pat='serpentine', spacing=20, angle=0)
    paths, _ = L._build_effective()
    lines = [p.sample_points() for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill']
    pts = [q for l in lines for q in l]
    assert max(len(l) for l in lines) > 30                  # curved, not straight
    # every point stays within the wave amplitude of its hatch line (y = 10 + 20k)
    # (Pass 8 correction: the serpentine is a WEB — amplitude ½ spacing so
    # neighbours touch; the web invariants are tested in test_pass8.py)
    for q in pts:
        if 1e-6 < q.x < 300 - 1e-6 and 1e-6 < q.y < 200 - 1e-6:
            dev = abs((q.y - 10) - 20 * round((q.y - 10) / 20))
            assert dev <= SO.WEB_AMP * 20 + 1e-6 or abs(q.x) < 25 or abs(q.x - 300) < 25
    # smooth: no sharp kinks along a line body (away from the boundary turns)
    for l in lines:
        for a, b, c in zip(l, l[1:], l[2:]):
            if 30 < b.x < 270 and a.dist(b) > 1e-6 and b.dist(c) > 1e-6:
                v1, v2 = b - a, c - b
                cs = (v1.x * v2.x + v1.y * v2.y) / (v1.length() * v2.length())
                assert cs > math.cos(math.radians(40))


def test_solid_patterns_api():
    d = design_app.app.test_client().get('/api/infill_patterns').get_json()
    assert {p['name'] for p in d if p['kind'] == 'solid'} == {'rectilinear', 'serpentine'}
    assert len({p['name'] for p in d}) == len(d)          # names unique (UI keys by name)


# ---------------------------------------------------------------------------
# 7. Parametric wall relationship between nested closed boundaries
# ---------------------------------------------------------------------------

def _rel_layer(o, i, t=10, driver='outer'):
    return PrintLayer('t', source_paths=[o, i],
                      wall_relations=[WallRelation('w', o.id, i.id, t, driver)])


class TestWallRelation:

    def test_rect_in_rect(self):
        O, I = RectanglePath(0, 0, 200, 120, id='O'), RectanglePath(30, 20, 100, 60, id='I')
        L = _rel_layer(O, I)
        L.effective_paths()
        assert (I.x, I.y, I.w, I.h) == (10, 10, 180, 100)
        O.w = 300                                      # edit the driver
        L.effective_paths()
        assert (I.x, I.w) == (10, 280)

    def test_rotated_and_rounded(self):
        O = RectanglePath(0, 0, 200, 120, id='O', corner_radius=25, rotation=0.4)
        I = RectanglePath(0, 0, 10, 10, id='I')
        L = _rel_layer(O, I)
        L.effective_paths()
        assert I.rotation == 0.4 and I.corner_radius == 15
        assert (I.x + I.w / 2, I.y + I.h / 2) == pytest.approx((100, 60))

    def test_inner_drives(self):
        O, I = RectanglePath(0, 0, 50, 50, id='O'), RectanglePath(30, 20, 100, 60, id='I')
        L = _rel_layer(O, I, 12, 'inner')
        L.effective_paths()
        assert (O.x, O.y, O.w, O.h) == (18, 8, 124, 84)

    def test_circle_and_ellipse(self):
        O, I = CirclePath(0, 0, 60, id='O'), CirclePath(5, 5, 10, id='I')
        L = _rel_layer(O, I)
        L.effective_paths()
        assert (I.cx, I.cy, I.radius) == (0, 0, 50)
        O, I = EllipsePath(0, 0, 100, 60, id='O'), EllipsePath(3, 3, 10, 10, id='I')
        L = _rel_layer(O, I)
        L.effective_paths()
        st = L.network_summary()['wall_relations']['w']
        assert (I.rx, I.ry) == (90, 50) and st['status'] == 'ok'
        assert st['max_gap'] == pytest.approx(10, abs=1e-6) and st['min_gap'] < 10   # honest

    def test_unsupported_and_too_thick_fail_clearly(self):
        O, I = RectanglePath(0, 0, 200, 120, id='O'), CirclePath(50, 50, 10, id='I')
        L = _rel_layer(O, I)
        L.effective_paths()
        assert L.network_summary()['wall_relations']['w']['status'] == 'unsupported'
        assert (I.cx, I.radius) == (50, 10)            # untouched
        O, I = RectanglePath(0, 0, 30, 30, id='O'), RectanglePath(5, 5, 10, 10, id='I')
        L = _rel_layer(O, I, 20)
        L.effective_paths()
        assert L.network_summary()['wall_relations']['w']['status'] == 'thickness too large'

    def test_relation_wall_takes_wall_infill(self):
        O, I = RectanglePath(0, 0, 200, 120, id='O'), RectanglePath(30, 20, 100, 60, id='I')
        L = _rel_layer(O, I)
        L.infills = [RegionInfill('I1', 'O', 'zigzag', {'spacing': 20})]
        q = RQ.wall_metrics(L, 20)
        assert q['thickness'] == pytest.approx(10, abs=0.3)   # a 10 in wall between them
        assert q['interior_retrace'] < 1e-6 and q['runs'] == 1

    def test_api_round_trip(self):
        payload = {'id': 't', 'source_paths': [
            {'id': 'O', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 120, 'closed': True},
            {'id': 'I', 'type': 'RectanglePath', 'x': 30, 'y': 30, 'w': 50, 'h': 50, 'closed': True}],
            'wall_relations': [{'id': 'w', 'outer_id': 'O', 'inner_id': 'I', 'thickness': 8}]}
        L = design_app._deserialise_layer(payload)
        assert L.to_dict()['wall_relations'] == [{'id': 'w', 'outer_id': 'O', 'inner_id': 'I',
                                                  'thickness': 8.0, 'driver': 'outer'}]
        c = design_app.app.test_client()
        d = c.post('/api/effective_paths', data=json.dumps(payload),
                   content_type='application/json').get_json()
        assert d['network']['wall_relations']['w']['status'] == 'ok'


# ---------------------------------------------------------------------------
# Router: solid strands pair leftover odd ends by travel, nothing else changes
# ---------------------------------------------------------------------------

def test_router_travel_pairing_is_scoped():
    from geometry import Strand, Layer, Vec2 as RV
    from graph import route_layer, compute_metrics

    def lay(tp):
        # a square (face) with two field strands hanging off opposite sides
        sq = Strand('sq', 'free', [RV(0, 0), RV(100, 0), RV(100, 100), RV(0, 100)], True,
                    retrace_cost=3.0)
        a = Strand('a', 'free', [RV(0, 50), RV(40, 50)], False, kind='field', travel_pairing=tp)
        b = Strand('b', 'free', [RV(100, 50), RV(60, 50)], False, kind='field', travel_pairing=tp)
        return Layer(0, [sq, a, b])
    m0 = compute_metrics(route_layer(lay(False)))
    m1 = compute_metrics(route_layer(lay(True)))
    assert m0['retrace_distance'] > 0 and m0['travel_moves'] == 0      # unchanged default
    assert m1['retrace_distance'] == 0 and m1['travel_moves'] == 1     # a short hop instead
