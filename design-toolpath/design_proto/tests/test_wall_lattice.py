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
from model import Vec2, PrintLayer, RectanglePath, RegionInfill, CirclePath, LinePath, WallSpec
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
    # Through the generator: a lone straight wall whose usable run is
    # ~91 in at a 20 in target gets 5 stitches at ~18.2 in, evenly spread
    # (never 4 × 20 + a remainder stitch).
    W = LinePath(Vec2(0, 0), Vec2(101, 0), id='W')
    W.wall = WallSpec(10)
    L = PrintLayer('t', source_paths=[W],
                   infills=[RegionInfill('I', 'W', 'zigzag', {'spacing': 20})])
    (run,) = runs_of(L)
    assert run['length'] == pytest.approx(91, abs=0.1)
    assert run['stitches'] == 5 and run['pitch'] == pytest.approx(18.2, abs=0.05)
    # geometry: the stations (landings on either face, cap-V landings at
    # the very ends excluded) are evenly spaced at that pitch
    paths, _ = L._build_effective()
    xs = sorted({round(q.x, 6) for p in paths if getattr(p, 'treatment_id', '') == 'infill'
                 for q in p.sample_points() if abs(abs(q.y) - 5) < 1e-6})
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    assert len(gaps) == 5
    # (even within 0.5 % — the end gaps differ slightly via the station
    # parameter near the caps; same tolerance as the straight-wall test)
    assert max(gaps) - min(gaps) < 0.005 * max(gaps)
    assert sum(gaps) / len(gaps) == pytest.approx(run['pitch'], abs=0.05)


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
# Dead ends: mirrored-phase out-and-back (Pass 8)
# ---------------------------------------------------------------------------

def _face_stations(L, y_face, x_min=206):
    """Landing x positions on one face (y = y_face) of the +x arm."""
    paths, _ = L._build_effective()
    return sorted({round(q.x, 6) for p in paths if getattr(p, 'treatment_id', '') == 'infill'
                   for q in p.sample_points() if abs(q.y - y_face) < 1e-6 and q.x > x_min})


@pytest.mark.parametrize('sp', [14, 16, 20, 24])
def test_dead_end_out_and_back_supports_each_face_evenly(sp):
    # Pass 8: outgoing and return phases are half a cycle apart (mirrored):
    # every station lands BOTH faces (one phase each), so the combined
    # support on EACH face is regular at ≈ the target — no alternating
    # tight / wide gaps (pass 7's quarter-cycle interleave gave ~S / ~3S).
    L = WF.arms(4, sp=sp)
    q = RQ.wall_metrics(L)
    for y in (195, 205):                       # both faces of the +x arm
        st = _face_stations(L, y)
        gaps = [b - a for a, b in zip(st, st[1:])]
        assert len(gaps) >= 2
        assert max(gaps) <= 1.1 * min(gaps)                     # even
        assert 0.8 * sp <= sum(gaps) / len(gaps) <= 1.25 * sp   # ≈ target
        assert max(gaps) <= 1.375 * sp + 1e-6                   # max unsupported
    assert _face_stations(L, 195) == _face_stations(L, 205)     # mirrored
    for r in runs_of(L):
        assert r['motif'] == 'out_and_back' and r['passes'] == 2
    assert q['interior_retrace'] < 1e-6
    assert q['tiny_cells'] == 0
    assert q['congestion_max_generated'] <= 2.0 + RQ.HOTSPOT_TOL


@pytest.mark.parametrize('sp', [12, 16, 20, 24])
@pytest.mark.parametrize('name', ['05 four-arm junction', '06 four curved arms',
                                  '07 six-arm star', '11 unequal branches'])
def test_dead_end_networks_support_every_face_evenly(name, sp):
    # Networks made only of out-and-back arms: the longest stretch of any
    # face between landings stays near the target everywhere (incl. curved
    # arms and junction corners). Pass 7 measured 2.3–3.3 × target here.
    L = WF.FIXTURES[name](sp)
    gaps = [g for ring in RQ.face_support(L) for g in ring['gaps']]
    assert max(gaps) <= 1.8 * sp
    q = RQ.wall_metrics(L)
    assert q['interior_retrace'] < 1e-6
    assert q['congestion_hotspots'] == 0 and q['tiny_cells'] == 0


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


# ---------------------------------------------------------------------------
# Pass 8: WAVE is a smooth wave (measured on the printable geometry)
# ---------------------------------------------------------------------------

def _turns(poly):
    out = [0.0]
    for a, b, c in zip(poly, poly[1:], poly[2:]):
        v1, v2 = b - a, c - b
        l1, l2 = v1.length(), v2.length()
        out.append(0.0 if l1 < 1e-9 or l2 < 1e-9 else math.degrees(math.acos(
            max(-1.0, min(1.0, (v1.x * v2.x + v1.y * v2.y) / (l1 * l2))))))
    return out + [0.0]


def _wave_defects(L):
    """(spikes, max turn) of the wave infill AWAY from sharp boundary
    points (wall corners, cap corners, junction corners: within 1.5 ×
    thickness the structural brace / cap V / hand-off may turn sharply).
    A spike is a vertex turning much more than both neighbours — the V a
    polyline zigzag has at every landing; a sampled smooth curve has none."""
    paths, meta = L._build_effective()
    thick = max(r['thickness'] for v in meta['network']['lattice'].values() for r in v['regions'])
    sharp = [(c[0], c[1]) for c in RQ.corner_support(L, 30.0)]
    spikes, worst = [], 0.0
    for p in paths:
        if getattr(p, 'treatment_id', '') != 'infill':
            continue
        poly = p.sample_points()
        T = _turns(poly)
        for k in range(1, len(poly) - 1):
            q = poly[k]
            if any(math.dist((q.x, q.y), s) <= 1.5 * thick for s in sharp):
                continue
            worst = max(worst, T[k])
            if T[k] > max(12.0, 2.2 * max(T[k - 1], T[k + 1])):
                spikes.append((round(q.x, 1), round(q.y, 1), round(T[k], 1)))
    return spikes, worst


WAVE_FIXTURES = {
    'straight': lambda sp: WF.straight(sp, pat='wave'),
    'curved': lambda sp: WF.curved(sp, pat='wave'),
    'rect loop': lambda sp: WF.rect_loop(sp, pat='wave'),
    'rounded rect': lambda sp: WF.rounded_rect_loop(sp, pat='wave'),
    'triangle': lambda sp: WF.polygon_wall(3, sp=sp, pat='wave'),
    'four arms': lambda sp: WF.arms(4, sp=sp, pat='wave'),
    'four curved arms': lambda sp: WF.curved_arms(4, sp=sp, pat='wave'),
    'six-arm star': lambda sp: WF.star(sp, pat='wave'),
    'rect + branch': lambda sp: WF.rect_branches(1, sp, pat='wave'),
    'openings': lambda sp: WF.openings(sp, pat='wave'),
}


@pytest.mark.parametrize('sp', [12, 16, 20, 24])
@pytest.mark.parametrize('name', list(WAVE_FIXTURES))
def test_wave_is_smooth_away_from_structural_transitions(name, sp):
    # Pass 7's wave (0.4 straight + 0.6 sine, coarse samples) had a ~40° V
    # at every landing: 8–42 spikes per fixture. A wave is a half sine
    # TANGENT to both faces, finely sampled: no spikes, gentle turning.
    L = WAVE_FIXTURES[name](sp)
    spikes, worst = _wave_defects(L)
    assert spikes == []
    assert worst <= 25.0


@pytest.mark.parametrize('name', list(WAVE_FIXTURES))
def test_wave_keeps_structure(name):
    # smoothness never costs support, congestion or retrace
    L = WAVE_FIXTURES[name](20)
    q = RQ.wall_metrics(L)
    assert q['interior_retrace'] < 1e-6
    assert q['congestion_hotspots'] == 0 and q['tiny_cells'] == 0
    assert q['cross_ratio'] >= 0.95
    for reg in L.network_summary()['lattice']['I']['regions']:
        assert reg['max_unsupported'] <= reg['max_unsupported_limit'] + 1e-6
    _assert_internal_inside(L)
    thick = 10.0
    for x, y, turn, d in RQ.corner_support(L):
        assert d <= thick + 1e-6, (x, y, turn, d)        # corners / caps landed
    # the angular share is small: long straight infill segments only where
    # a brace / cap leg must be straight
    paths, _ = L._build_effective()
    polys = [p.sample_points() for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    tot = sum(a.dist(b) for pl in polys for a, b in zip(pl, pl[1:]))
    straight = sum(a.dist(b) for pl in polys for a, b in zip(pl, pl[1:]) if a.dist(b) >= 10.0)
    assert straight <= 0.1 * tot
