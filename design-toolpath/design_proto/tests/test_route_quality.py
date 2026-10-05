"""
Physical route quality — not just graph correctness.

A route that is one run with zero exact retrace is still a FAILED route if
it lays a long arbitrary bead through the wall or piles generated beads
into a small area. These tests assert the route_quality diagnostics on
representative walls (A–H of the brief):

  * one run / zero travel where reasonably achievable
  * no exact geometric retrace (measured on the moves, either direction)
  * no generated mud knot: ≤ MAX_GENERATED_BEADS generated beads through
    any point within CLEARANCE_RADIUS, generated junction degree ≤ 4
  * corrections are LOCAL edits of the infill field: no long added strut,
    no edit far from its defect (beyond the phase-shift reach)
  * user-authored high-degree junctions stay legal
  * generated crossings are structural, not junctions
  * generated geometry stays inside the wall material, out of voids /
    doorways
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as design_app
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..',
                             'toolpath_proto'))

import pytest
from graph import route_layer, build_graph
import route_quality as RQ
import route_plan as RP
from model import (Vec2, LinePath, RectanglePath, OffsetTreatment, PrintLayer,
                   LatticeInstance, Opening, RegionInfill, WallSpec, NetworkWall,
                   _processed_source_pts)
from tests.test_networks import _assert_internal_inside, _build


# ---------------------------------------------------------------------------
# Representative walls
# ---------------------------------------------------------------------------

def _inf(pid, sp=20, pat='zigzag'):
    return [RegionInfill('I', pid, pat, {'spacing': sp})]


def rect_branches(n, t=10, sp=20, **kw):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(t)
    srcs = [R, LinePath(Vec2(200, 260), Vec2(200, 340), id='B1')]
    if n >= 2:
        srcs.append(LinePath(Vec2(300, 200), Vec2(380, 200), id='B2'))
    return PrintLayer('t', source_paths=srcs, network_walls=[NetworkWall('W', 'B1', t)],
                      infills=_inf('R', sp), **kw)


def rect_wall(**kw):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(10)
    return PrintLayer('t', source_paths=[R], infills=_inf('R'), **kw)


def two_voids(pat='zigzag', **kw):
    return PrintLayer('t', source_paths=[
        RectanglePath(0, 0, 300, 160, id='O'), RectanglePath(40, 50, 70, 60, id='A'),
        RectanglePath(180, 30, 80, 70, id='B')], infills=_inf('O', 22, pat), **kw)


def star(**kw):
    arms = [LinePath(Vec2(200, 200),
                     Vec2(200 + 120 * math.cos(k * math.pi / 3 + 0.2),
                          200 + 120 * math.sin(k * math.pi / 3 + 0.2)), id=f'L{k + 1}')
            for k in range(6)]
    return PrintLayer('t', source_paths=arms, network_walls=[NetworkWall('W', 'L1', 10)],
                      junction_style='round', junction_radius=3,
                      cap_style='full_round', infills=_inf('L1', 16), **kw)


def nearby_branches(**kw):
    R = RectanglePath(100, 140, 240, 120, id='R')
    R.wall = WallSpec(10)
    srcs = [R] + [LinePath(Vec2(150 + 25 * k, 260), Vec2(150 + 25 * k + 8 * (k - 1), 330),
                           id=f'B{k}') for k in range(3)]
    return PrintLayer('t', source_paths=srcs, network_walls=[NetworkWall('W', 'B0', 8)],
                      infills=_inf('R', 18), **kw)


def narrow(**kw):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(4)
    srcs = [R, LinePath(Vec2(200, 260), Vec2(200, 320), id='B')]
    return PrintLayer('t', source_paths=srcs, network_walls=[NetworkWall('W', 'B', 4)],
                      infills=_inf('R', 20), **kw)


CASES = {
    'A1_one_dead_end': lambda **kw: rect_branches(1, **kw),
    'A2_two_dead_ends': lambda **kw: rect_branches(2, **kw),
    'B_rect_wall_zigzag': rect_wall,
    'C_two_voids_zigzag': lambda **kw: two_voids('zigzag', **kw),
    'D_two_voids_wave': lambda **kw: two_voids('wave', **kw),
    'E_six_arm_star': star,
    'F_nearby_branches': nearby_branches,
    'G_narrow_wall': narrow,
}

_cache = {}


def Q(name):
    if name not in _cache:
        _cache[name] = RQ.quality(CASES[name]())
    return _cache[name]


def _pitch(name):
    return {'C_two_voids_zigzag': 22, 'D_two_voids_wave': 22, 'E_six_arm_star': 16,
            'F_nearby_branches': 18}.get(name, 20)


# ---------------------------------------------------------------------------
# Quality on every representative wall
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', sorted(CASES))
class TestPhysicalQuality:

    def test_continuous(self, name):
        q = Q(name)
        assert q['runs'] == 1 and q['travel_moves'] == 0 and q['travel_length'] == 0

    def test_no_exact_retrace(self, name):
        assert Q(name)['exact_retrace'] < 1e-6

    def test_no_generated_mud_knots(self, name):
        q = Q(name)
        assert q['congestion_max_generated'] <= RQ.MAX_GENERATED_BEADS + 0.05
        assert q['congestion_hotspots'] == 0

    def test_generated_junction_degree_bounded(self, name):
        # at most two generated paths through any exact point
        assert Q(name)['max_generated_degree'] <= 4

    def test_no_long_corrective_strut(self, name):
        # every strut a correction adds is a local field strut, never a
        # long bead across the wall
        assert Q(name)['max_correction_segment'] <= 2.5 * _pitch(name)

    def test_corrections_stay_near_their_defect(self, name):
        # local edits within a few pitches; phase shifts (tier 2) may run
        # the length of a dead-end arm but never further than PHASE_LIMIT
        assert Q(name)['max_correction_distance'] <= RP.PHASE_LIMIT * _pitch(name) / 2

    def test_added_extrusion_is_modest(self, name):
        q = Q(name)
        net = q['correction_added'] - q['correction_removed']
        assert net <= 0.10 * q['print_length']

    def test_generated_geometry_inside_the_wall(self, name):
        _assert_internal_inside(CASES[name]())


def test_most_corrections_are_local():
    tiers = {}
    for name in CASES:
        _, meta = CASES[name]()._build_effective()
        for c in (meta['network']['route_plan'] or {}).get('corrections', []):
            tiers[c['tier']] = tiers.get(c['tier'], 0) + 1
    assert tiers.get(1, 0) > tiers.get(2, 0) + tiers.get(3, 0)


# ---------------------------------------------------------------------------
# Before / after: the old long-return model vs local repair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['C_two_voids_zigzag'])
def test_without_repair_the_router_retraces(name):
    q = RQ.quality(CASES[name](return_paths=False))
    assert q['exact_retrace'] > 0          # parity really needed fixing
    assert Q(name)['exact_retrace'] < 1e-6


@pytest.mark.parametrize('name', ['E_six_arm_star', 'A2_two_dead_ends', 'F_nearby_branches'])
def test_wall_networks_continuous_without_repair(name):
    # Superseded: wall networks used to retrace without repair. Their motif
    # lattice is continuous by construction — repair on or off.
    q = RQ.quality(CASES[name](return_paths=False))
    assert q['exact_retrace'] < 1e-6 and q['runs'] == 1 and q['travel_moves'] == 0


# ---------------------------------------------------------------------------
# User-authored vs generated topology
# ---------------------------------------------------------------------------

def test_user_authored_high_degree_junction_is_legal():
    # Six single-bead walls drawn into one point: a user-authored degree-6
    # junction. It stays a junction, and it is not "generated congestion".
    lines = [LinePath(Vec2(0, 0), Vec2(80 * math.cos(k * math.pi / 3),
                                       80 * math.sin(k * math.pi / 3)), id=f'W{k}')
             for k in range(6)]
    L = PrintLayer('t', source_paths=lines)
    G = build_graph(L.to_routing_layer())
    assert G.degree((0, 0)) == 6
    q = RQ.quality(L)
    assert q['congestion_max_generated'] == 0 and q['max_generated_degree'] == 0


def test_printed_reference_lines_keep_their_junction():
    # Centred walls whose reference IS printed (opt-in): the six reference
    # lines still meet at their user-authored degree-6 hub.
    L = star()
    L.infills = []
    L.network_walls[0].print_reference = True
    paths, meta = _build(L)
    G = build_graph(L.to_routing_layer(paths, meta))
    assert G.degree((200, 200)) == 6


def test_centred_reference_is_not_printed_by_default():
    paths, meta = _build(star())
    ids = {p.id for p in paths}
    assert not any(i == f'L{k}' or i.startswith(f'L{k}~') for k in range(1, 7) for i in ids)
    assert meta['network']['reference_only'] == [f'L{k}' for k in range(1, 7)]


class TestGeneratedCrossings:

    def H(self):
        # H: two requested lattices crossing each other in one wall
        R = RectanglePath(0, 0, 200, 120, id='R')
        return PrintLayer('t', source_paths=[R],
                          offset_treatments=[OffsetTreatment('Ri', 'R', 12)],
                          lattice_instances=[
                              LatticeInstance('Z', 'zigzag', 'R', 'Ri',
                                              {'segments': 12, 'connect_ends': 1}),
                              LatticeInstance('V', 'wave', 'R', 'Ri', {'cycles': 7})])

    def test_crossings_are_not_junctions(self):
        L = self.H()
        rl = L.to_routing_layer()
        G = build_graph(rl)
        z = [((a.x, a.y), (b.x, b.y)) for s in rl.strands if s.kind == 'field'
             for a, b in s.segments()]
        crossings = set()
        for i in range(len(z)):
            for j in range(i + 1, len(z)):
                x = RQ._cross(*z[i], *z[j])
                if x is not None:
                    crossings.add((round(x[0], 6), round(x[1], 6)))
        assert crossings
        nodes = {(round(n[0], 6), round(n[1], 6)) for n in G.nodes}
        assert not (crossings & nodes), 'a generated crossing became a junction'

    def test_crossing_lattices_still_route_continuously(self):
        q = RQ.quality(self.H())
        assert q['runs'] == 1 and q['travel_moves'] == 0

    def test_repair_corrections_cross_without_joining(self):
        # support struts / phase shifts that cross printed struts add no node
        L = star()
        paths, meta = _build(L)
        rl = L.to_routing_layer(paths, meta)
        G = build_graph(rl)
        field = [((a.x, a.y), (b.x, b.y)) for s in rl.strands if s.kind == 'field'
                 for a, b in s.segments()]
        nodes = {(round(n[0], 6), round(n[1], 6)) for n in G.nodes}
        for i in range(len(field)):
            for j in range(i + 1, len(field)):
                x = RQ._cross(*field[i], *field[j])
                if x is not None:
                    assert (round(x[0], 6), round(x[1], 6)) not in nodes


# ---------------------------------------------------------------------------
# Voids and openings
# ---------------------------------------------------------------------------

def _all_generated(L):
    paths, meta = _build(L)
    return [p for p in paths if p.role == 'lattice']


def test_two_voids_stay_empty():
    for p in _all_generated(two_voids()):
        pts = p.sample_points()
        for a, b in zip(pts, pts[1:]):
            for k in range(1, 20):
                q = a.lerp(b, k / 20)
                assert not (40 + 1e-6 < q.x < 110 - 1e-6 and 50 + 1e-6 < q.y < 110 - 1e-6)
                assert not (180 + 1e-6 < q.x < 260 - 1e-6 and 30 + 1e-6 < q.y < 100 - 1e-6)


def test_doorways_stay_empty_and_route_avoids_them():
    L = rect_branches(1)
    L.openings = [Opening('o1', 'R', 60, 24)]
    moves = route_layer(L.to_routing_layer())
    for mv in moves:
        if mv.kind == 'travel':
            continue
        for k in range(1, 10):
            q = mv.start.lerp(mv.end, k / 10)
            assert not (148 + 1e-6 < q.x < 172 - 1e-6 and 140 + 1e-6 < q.y < 150 - 1e-6)
    _assert_internal_inside(L)
    assert RQ.geometric_retrace(moves) < 1e-6


# ---------------------------------------------------------------------------
# Remaining unavoidable cases (documented)
# ---------------------------------------------------------------------------

def test_single_bead_dead_end_still_retraces():
    # No wall material beside a single-bead branch: nothing to edit, the
    # only continuous route retraces the branch (the last resort).
    L = PrintLayer('t', source_paths=[LinePath(Vec2(0, 100), Vec2(300, 100), id='M'),
                                      LinePath(Vec2(150, 100), Vec2(150, 0), id='B')])
    q = RQ.quality(L)
    assert q['exact_retrace'] == pytest.approx(100.0)
    assert q['runs'] == 1 and q['travel_moves'] == 0


def test_separate_faces_without_infill_travel():
    # A ring wall WITHOUT infill: outer and inner face are genuinely
    # separate printed components → one travel (no hidden bridge is
    # invented).
    R = RectanglePath(0, 0, 160, 100, id='R')
    R.wall = WallSpec(10)
    q = RQ.quality(PrintLayer('t', source_paths=[R]))
    assert q['runs'] == 2 and q['travel_moves'] == 1 and q['exact_retrace'] == 0


# ---------------------------------------------------------------------------
# Diagnostics themselves
# ---------------------------------------------------------------------------

def test_congestion_metric_counts_beads():
    # three generated beads through one point → ~3; one bead → 1
    star3 = [((-10, 0), (10, 0)), ((0, -10), (0, 10)), ((-7, -7), (7, 7))]
    assert RQ.congestion(star3, [])['max_generated'] == pytest.approx(3.0, abs=0.01)
    # one bead passing through its vertex → 1; at a bead's END only half a
    # bead lies in the disk → 0.5
    one = [((-10, 0), (0, 0)), ((0, 0), (10, 0))]
    assert RQ.congestion(one, [])['max_generated'] == pytest.approx(1.0, abs=0.01)


def test_geometric_retrace_detects_reversal():
    from geometry import PrintMove, Vec2 as RV
    mv = [PrintMove('print', 'a', 0, RV(0, 0), RV(10, 0)),
          PrintMove('print', 'b', 0, RV(10, 0), RV(4, 0))]       # back over 6 in
    assert RQ.geometric_retrace(mv) == pytest.approx(6.0)
