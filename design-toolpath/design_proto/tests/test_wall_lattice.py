"""
Wall lattice (route-aware stitching motifs, wall_lattice.py) — physical
quality of WALL infill, measured geometrically on the produced geometry and
route (route_quality.wall_metrics), never by labels:

  A interior retrace       0 wherever the wall leaves room for a return
  B cross-wall stitches    stitches run face → opposite face
  C congestion             no generated knots (≤ 2 beads anywhere)
  D tiny loops             no bow tie / diamond / box cells
  E/F target spacing       actual pitch near the target, even within a run
  G coherence              equivalent branches share one motif
  H stability              a spacing sweep never changes motif type
  I–L runs, travel, start/end distance, print length

Required fixtures (tests/wall_fixtures.py): straight, curved, one / two
dead-end arms, four-arm junction, four CURVED arms, six-arm star, closed
rectangular loop, openings, narrow wall, unequal branches, spacing sweep.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app  # noqa: F401  (path setup)
import route_quality as RQ
import wall_lattice as WL
import wall_fixtures as WF
from model import Vec2, PrintLayer, RectanglePath, RegionInfill, CirclePath
from tests.test_networks import _assert_internal_inside


_CACHE = {}


def M(name, sp=None):
    key = (name, sp)
    if key not in _CACHE:
        fn = WF.FIXTURES[name]
        L = fn() if sp is None else fn(sp)
        _CACHE[key] = (L, RQ.wall_metrics(L))
    return _CACHE[key]


def runs_of(layer, fid='I'):
    lat = layer.network_summary()['lattice'][fid]
    assert lat['motif'], 'expected the motif generator, got the field fallback'
    return [r for reg in lat['regions'] for r in reg['runs']]


NETWORKS = ['03 one dead-end arm', '04 two dead-end arms', '05 four-arm junction',
            '06 four curved arms', '07 six-arm star', '08 closed rect loop',
            '10 narrow wall', '11 unequal branches']


# ---------------------------------------------------------------------------
# Every fixture: A B C D, inside the wall, I J
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', list(WF.FIXTURES))
def test_physical_quality(name):
    L, q = M(name)
    assert q['interior_retrace'] < 1e-6                         # A
    assert q['exact_retrace'] < 1e-6
    assert q['cross_ratio'] >= 0.95 and q['same_face_stitches'] == 0   # B
    assert q['congestion_hotspots'] == 0                        # C
    assert q['congestion_max_generated'] <= RQ.MAX_GENERATED_BEADS + RQ.HOTSPOT_TOL
    assert q['tiny_cells'] == 0                                 # D
    _assert_internal_inside(L)                                  # never across a void / opening
    expect_runs = 2 if name == '09 wall with openings' else 1  # two separate wall pieces
    assert q['runs'] == expect_runs                             # I
    if expect_runs == 1:
        assert q['travel_length'] == 0                          # J


@pytest.mark.parametrize('name', NETWORKS)
def test_networks_close_start_on_end(name):
    # K: complementary out-and-back modules make the network route closed
    assert M(name)[1]['start_end_distance'] < 1e-6


def test_lone_wall_is_one_open_single_phase_run():
    # An ordinary wall run with no junction: one zigzag end to end (open
    # route), not doubled just to close the loop.
    L, q = M('01 straight wall')
    (r,) = runs_of(L)
    assert r['motif'] == 'lone' and r['passes'] == 1
    assert q['start_end_distance'] > 200


# ---------------------------------------------------------------------------
# E / F  target spacing, even distribution
# ---------------------------------------------------------------------------

def test_stitch_count_redistributes_the_remainder():
    # 91 in at 20 in → 5 stitches at 18.2 in (never 4 × 20 + remainder)
    assert WL._best_n(91, 20) == 5 and 91 / 5 == pytest.approx(18.2)
    # even count required: 6 × 16.7 (−17 %) beats 4 × 25 (+25 %)
    assert WL._best_n(100, 20) == 5 and WL._best_n(100, 20, parity=0) == 6
    assert WL._best_n(5, 20) == 1


@pytest.mark.parametrize('name', list(WF.FIXTURES))
def test_actual_pitch_near_target(name):
    L, _ = M(name)
    sp = L.infills[0].params['spacing']
    for r in runs_of(L):
        if r['length'] < 0.75 * sp:
            continue                    # a very short run: one stitch
        assert 0.75 * sp <= r['pitch'] <= 1.25 * sp, r


def test_even_landings_along_a_straight_wall():
    L, _ = M('01 straight wall')
    paths, meta = L._build_effective()
    top = sorted(q.x for p in paths if getattr(p, 'treatment_id', '') == 'infill'
                 for q in p.sample_points() if abs(q.y - 205) < 1e-6)
    # (pass 7: the cap V also lands both faces at each end station — the
    # end supports; the ordinary stitches between them are what is even)
    top = [x for x in top if 40 + 15 < x < 320 - 15]
    gaps = [b - a for a, b in zip(top, top[1:])]
    assert len(gaps) >= 5 and max(gaps) - min(gaps) < 0.005 * max(gaps)   # F: even


# ---------------------------------------------------------------------------
# Dead ends: complementary-phase out-and-back
# ---------------------------------------------------------------------------

def test_dead_end_return_uses_the_complementary_phase():
    # Pass 7 density semantics: Target Spacing is the COMBINED density. The
    # outgoing and return phases interleave — each at ~2 × target, offset by
    # ~one target — so support stations (either face) are ~target apart and
    # no station is landed twice (no doubled density, no retrace).
    L, q = M('05 four-arm junction')
    paths, _ = L._build_effective()
    pts = [q_ for p in paths if getattr(p, 'treatment_id', '') == 'infill'
           for q_ in p.sample_points()]
    # the arm along +x: walls at y = 195 / 205, from the junction (x ≈ 205)
    top = sorted({round(p.x, 6) for p in pts if abs(p.y - 205) < 1e-6 and p.x > 206})
    bot = sorted({round(p.x, 6) for p in pts if abs(p.y - 195) < 1e-6 and p.x > 206})
    # interleaved, not doubled: no station landed on both faces — except the
    # last one, where both phases end and the cap V joins them
    assert len(set(top) & set(bot)) <= 1
    stations = sorted(set(top + bot))
    gaps = [b - a for a, b in zip(stations, stations[1:])]
    assert len(stations) >= 4 and max(gaps) <= 1.375 * 20 + 1e-6
    assert 0.6 * 20 <= sum(gaps) / len(gaps) <= 1.25 * 20  # combined ≈ target
    for r in runs_of(L):
        assert r['motif'] == 'out_and_back' and r['passes'] == 2
    assert q['interior_retrace'] == 0
    # density not doubled: wall crossings (stitches) ≈ arm length / target
    # (≈ 105 / 20), not twice that as with two complementary passes at the
    # target. (Bead LENGTH stays ~1.8 × one zigzag: an out-and-back must
    # travel the arm twice whatever its pitch — but it is less than the
    # former doubled lattice.)
    assert 0.75 * 105 / 20 <= len(stations) <= 1.3 * 105 / 20 + 1
    per_arm = q['generated_length'] / 4
    doubled = 2 * 105 / 20 * math.hypot(20, 10)
    assert per_arm < doubled


def test_junction_centre_has_no_knot():
    # The passes hand over at the junction corners; nothing is generated in
    # the junction square and at most two generated beads meet anywhere.
    L, q = M('05 four-arm junction')
    paths, _ = L._build_effective()
    for p in paths:
        if getattr(p, 'treatment_id', '') != 'infill':
            continue
        sp_ = p.sample_points()
        for a, b in zip(sp_, sp_[1:]):
            m = a.lerp(b, 0.5)
            assert not (196 < m.x < 204 and 196 < m.y < 204)
    assert q['congestion_max_generated'] <= 2.0 + 1e-3


# ---------------------------------------------------------------------------
# G  coherence across equivalent branches
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['05 four-arm junction', '06 four curved arms',
                                  '07 six-arm star', '11 unequal branches'])
def test_equivalent_branches_share_one_strategy(name):
    L, _ = M(name)
    rs = runs_of(L)
    assert {r['motif'] for r in rs} == {'out_and_back'}
    assert {r['passes'] for r in rs} == {2}
    if name != '11 unequal branches':
        ns = [r['stitches'] for r in rs]
        assert max(ns) - min(ns) <= 1                        # similar arms ≈ same count
    pitches = [r['pitch'] for r in rs]
    assert max(pitches) / min(pitches) < 1.25


def test_coherence_measured_geometrically():
    # generated bead length per inch of arm is the same in all four arms
    L, _ = M('05 four-arm junction')
    paths, _ = L._build_effective()
    per = {}
    for p in paths:
        if getattr(p, 'treatment_id', '') != 'infill':
            continue
        sp_ = p.sample_points()
        for a, b in zip(sp_, sp_[1:]):
            m = a.lerp(b, 0.5)
            arm = (round(math.atan2(m.y - 200, m.x - 200) / (math.pi / 2)) % 4)
            per[arm] = per.get(arm, 0.0) + a.dist(b)
    assert len(per) == 4 and max(per.values()) / min(per.values()) < 1.1


# ---------------------------------------------------------------------------
# H  stability over a target-spacing sweep (12 … 24 in)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['02 curved wall', '05 four-arm junction',
                                  '06 four curved arms', '07 six-arm star'])
def test_spacing_sweep_is_topologically_stable(name):
    motifs = set()
    prev = None
    for sp in (12, 14, 16, 18, 20, 22, 24):
        L, q = M(name, sp)
        assert q['interior_retrace'] < 1e-6 and q['tiny_cells'] == 0
        assert q['congestion_hotspots'] == 0 and q['cross_ratio'] >= 0.95
        rs = runs_of(L)
        motifs.add(tuple(sorted(r['motif'] for r in rs)))
        for r in rs:
            # the count tracks length / target (redistribution, no jumps);
            # every fixed support point (pinned corner; even loop) adds at
            # most one stitch of rounding / parity
            ideal = r['length'] / sp
            slack = 1.0 + (1.0 if r['motif'] == 'loop' else 0.0) + r.get('corners', 0)
            assert abs(r['stitches'] - ideal) <= slack
        ns = [r['stitches'] for r in rs]
        if prev is not None:
            assert all(a >= b for a, b in zip(prev, ns))      # larger target → never more
        prev = ns
    assert len(motifs) == 1                                   # never a different motif


# ---------------------------------------------------------------------------
# Wave uses the same motif / phase architecture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('make', [lambda: WF.arms(4, sp=20, pat='wave'),
                                  lambda: WF.curved_arms(4, sp=20, pat='wave'),
                                  lambda: WF.rect_loop(pat='wave')],
                         ids=['four_arms', 'curved_arms', 'loop'])
def test_wave_shares_the_architecture(make):
    L = make()
    q = RQ.wall_metrics(L)
    assert q['interior_retrace'] < 1e-6 and q['tiny_cells'] == 0
    assert q['congestion_hotspots'] == 0 and q['cross_ratio'] >= 0.95
    assert q['runs'] == 1 and q['start_end_distance'] < 1e-6
    _assert_internal_inside(L)
    paths, _ = L._build_effective()
    # waves are curves (several points per stitch), not straight struts
    inf = [p for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    assert max(len(p.sample_points()) for p in inf) > 8


# ---------------------------------------------------------------------------
# Boundaries of the motif generator
# ---------------------------------------------------------------------------

def test_wide_region_falls_back_to_the_field_generator():
    # an AREA (not a wall): the field generator + local repair
    L = PrintLayer('t', source_paths=[RectanglePath(0, 0, 300, 160, id='O'),
                                      RectanglePath(40, 50, 70, 60, id='A')],
                   infills=[RegionInfill('I', 'O', 'zigzag', {'spacing': 22})])
    lat = L.network_summary()['lattice']['I']
    assert lat['motif'] is False


def test_solid_infill_is_not_a_wall_lattice():
    L = PrintLayer('t', source_paths=[RectanglePath(40, 40, 320, 220, id='R'),
                                      CirclePath(200, 150, 40, id='V')],
                   infills=[RegionInfill('S', 'R', 'rectilinear', {'spacing': 16}, kind='solid')])
    s = L.network_summary()
    assert 'S' not in (s.get('lattice') or {})
    assert any(getattr(p, 'treatment_id', '') == 'solid_infill' for p in L.effective_paths())


def test_report_exposes_target_and_actual_pitch():
    L, _ = M('05 four-arm junction')
    info = L.network_summary()['infills'][0]['lattice']
    assert info['motif'] is True and 15 <= info['pitch_min'] <= info['pitch_max'] <= 25


def test_delaunay_is_a_triangulation():
    import random
    rnd = random.Random(4)
    P = [(rnd.uniform(0, 100), rnd.uniform(0, 100)) for _ in range(300)]
    T = WL.delaunay(P)
    assert len(T) == 2 * 300 - 2 - len(_hull(P))           # Euler for a triangulation
    for a, b, c in T[:200]:                                 # empty circumcircles
        for d in range(0, 300, 7):
            if d in (a, b, c):
                continue
            assert WL._incircle(*P[a], *P[b], *P[c], *P[d]) <= 1e-6


def _hull(P):
    pts = sorted(P)
    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) -
                                   (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h
    return half(pts)[:-1] + half(pts[::-1])[:-1]


def test_performance_is_interactive():
    import time
    t = time.time()
    WF.curved_arms(6, length=160).effective_paths()
    assert time.time() - t < 3.0
