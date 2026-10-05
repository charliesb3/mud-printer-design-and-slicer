"""
Route planning (hidden return paths instead of exact retrace), closed /
open layer routes, parametric wall thickness and network-level walls.

Exact retrace — printing back over the same deposited bead — is measured
GEOMETRICALLY here (collinear overlap between printed moves, in either
direction), never by move labels, so relabelling cannot fake a result.
"""
import sys, os, math, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as design_app
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..',
                             'toolpath_proto'))

import pytest
from graph import (route_layer, compute_metrics, build_graph, route_ends,
                   orient_for_previous)
import network as N
from model import (
    Vec2, LinePath, RectanglePath, CirclePath, ExplicitPath, OffsetTreatment,
    PrintLayer, Opening, RegionInfill, WallSpec, NetworkWall,
    _processed_source_pts,
)
from tests.test_networks import (_build, _pts, _assert_internal_inside,
                                 _assert_faces_bound_material,
                                 _assert_no_duplicate_walls)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _route(layer):
    paths, meta = _build(layer)
    moves = route_layer(layer.to_routing_layer(paths, meta))
    return moves, compute_metrics(moves), meta, paths


def geometric_overlap(moves):
    """Total length printed over an already printed segment (same or
    reverse direction): the physical meaning of exact retrace."""
    segs = [(m.start, m.end) for m in moves if m.kind != 'travel' and m.length > 1e-9]
    tot = 0.0
    for i in range(len(segs)):
        a, b = segs[i]
        L = a.dist(b)
        ux, uy = (b.x - a.x) / L, (b.y - a.y) / L
        for c, d in segs[i + 1:]:
            if abs((c.x - a.x) * uy - (c.y - a.y) * ux) > 1e-6 or \
                    abs((d.x - a.x) * uy - (d.y - a.y) * ux) > 1e-6:
                continue
            t0 = (c.x - a.x) * ux + (c.y - a.y) * uy
            t1 = (d.x - a.x) * ux + (d.y - a.y) * uy
            tot += max(0.0, min(L, max(t0, t1)) - max(0.0, min(t0, t1)))
    return tot


def _returns(paths):
    return [p for p in paths if getattr(p, 'treatment_id', '') == 'return_path']


def TWO_HOLES(pattern='zigzag'):
    return PrintLayer('t', source_paths=[
        RectanglePath(0, 0, 300, 160, id='O'),
        RectanglePath(40, 50, 70, 60, id='A'),
        RectanglePath(180, 30, 80, 70, id='B')],
        infills=[RegionInfill('I', 'O', pattern, {'spacing': 22})])


def STAR(infill=True, arms=6, **kw):
    lines = [LinePath(Vec2(200, 200),
                      Vec2(200 + 120 * math.cos(k * 2 * math.pi / arms + 0.2),
                           200 + 120 * math.sin(k * 2 * math.pi / arms + 0.2)),
                      id=f'L{k + 1}') for k in range(arms)]
    return PrintLayer('t', source_paths=lines,
                      network_walls=[NetworkWall('W', 'L1', 10)],
                      junction_style='round', junction_radius=3,
                      cap_style='full_round',
                      infills=[RegionInfill('I', 'L1', 'zigzag', {'spacing': 16})]
                      if infill else [], **kw)


def RECT_BRANCHES(n, infill=True, **kw):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(10)
    srcs = [R, LinePath(Vec2(200, 260), Vec2(200, 340), id='B1')]
    if n == 2:
        srcs.append(LinePath(Vec2(300, 200), Vec2(380, 200), id='B2'))
    return PrintLayer('t', source_paths=srcs,
                      network_walls=[NetworkWall('W', 'B1', 10)],
                      infills=[RegionInfill('I', 'R', 'zigzag', {'spacing': 20})]
                      if infill else [], **kw)


def _old_and_new(make):
    old = make()
    old.return_paths = False
    new = make()
    o = _route(old)
    n = _route(new)
    return o, n


# ---------------------------------------------------------------------------
# Local infill repair replaces exact retrace (physical quality: see
# test_route_quality.py)
# ---------------------------------------------------------------------------

def RING_INFILL(**kw):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(10)
    return PrintLayer('t', source_paths=[R],
                      infills=[RegionInfill('I', 'R', 'zigzag', {'spacing': 20})], **kw)


# Wall-lattice pass: wall NETWORKS (star, dead ends) get route-aware motif
# lattices (wall_lattice.py) that need no repair, so only the wide region
# (field generator) still exercises repair here; the networks are checked
# by test_wall_networks_need_no_repair below.
@pytest.mark.parametrize('make', [TWO_HOLES], ids=['two_holes'])
def test_repair_report_and_no_retrace(make):
    (om, o, _, _), (nm, n, meta, paths) = _old_and_new(make)
    assert geometric_overlap(om) > 0
    assert geometric_overlap(nm) < 1e-6 and n['retrace_distance'] == 0
    rp = meta['network']['route_plan']
    assert rp['repaired_pairs'] > 0 and rp['left_to_retrace'] == 0
    for c in rp['corrections']:
        assert c['tier'] in (1, 2, 3) and c['edits'] >= 1
    assert not _returns(paths)              # no long return beads any more


def test_single_bead_dead_end_still_retraces():
    # No wall material beside a single-bead branch: exact retrace is the
    # only option left (the last resort).
    L = PrintLayer('t', source_paths=[LinePath(Vec2(0, 100), Vec2(300, 100), id='M'),
                                      LinePath(Vec2(150, 100), Vec2(150, 0), id='B')])
    moves, m, _, _ = _route(L)
    assert m['retrace_distance'] == pytest.approx(100.0)


def test_repair_can_be_disabled():
    # (was STAR: a wall network no longer uses repair at all; the wide
    # region still does)
    L = TWO_HOLES()
    L.return_paths = False
    paths, meta = _build(L)
    assert meta['network']['route_plan'] is None


@pytest.mark.parametrize('make', [STAR, lambda: RECT_BRANCHES(2)], ids=['star', 'two_dead_ends'])
@pytest.mark.parametrize('repair', [True, False])
def test_wall_networks_need_no_repair(make, repair):
    # Superseded expectation: these used to retrace without repair and be
    # fixed by local edits. The motif lattice is continuous by design:
    # no exact retrace with OR without repair, no edits, no return beads.
    L = make()
    L.return_paths = repair
    moves, m, meta, paths = _route(L)
    assert geometric_overlap(moves) < 1e-6 and m['retrace_distance'] == 0
    assert m['print_runs'] == 1 and m['travel_moves'] == 0
    rp = meta['network']['route_plan']
    assert rp['repaired_pairs'] == 0 and rp['corrections'] == [] and rp['motif_regions'] == 1
    runs = meta['network']['lattice']['I']['regions'][0]['runs']
    assert all(r['motif'] == 'out_and_back' for r in runs if 'end' in r['ends'])
    assert not _returns(paths)


def test_isolated_wall_without_infill_unchanged():
    L = PrintLayer('t', source_paths=[RectanglePath(0, 0, 100, 80, id='R')],
                   offset_treatments=[OffsetTreatment('i', 'R', 10)])
    paths, meta = _build(L)
    assert meta['network']['route_plan'] is None


# ---------------------------------------------------------------------------
# Structural crossings vs routing junctions
# ---------------------------------------------------------------------------

class TestCrossings:

    def _graph(self, kinds):
        from geometry import Strand, Layer as RL, Vec2 as RV
        return build_graph(RL(0, [
            Strand('a', 'free', [RV(0, 0), RV(10, 10)], False, kind=kinds[0]),
            Strand('b', 'free', [RV(0, 10), RV(10, 0)], False, kind=kinds[1])]))

    @pytest.mark.parametrize('kinds', [('field', 'field'), ('field', 'internal')])
    def test_hidden_infill_crossing_is_not_a_junction(self, kinds):
        assert (5.0, 5.0) not in self._graph(kinds).nodes

    @pytest.mark.parametrize('kinds', [('face', 'face'), ('field', 'face'),
                                       ('internal', 'internal')])
    def test_wall_crossings_remain_junctions(self, kinds):
        assert self._graph(kinds).degree((5.0, 5.0)) == 4

    def test_return_paths_cross_infill_without_joining_it(self):
        L = TWO_HOLES()
        paths, meta = _build(L)
        rl = L.to_routing_layer(paths, meta)
        kinds = {s.id: s.kind for s in rl.strands}
        assert all(kinds[p.id] == 'field' for p in _returns(paths))
        assert all(kinds[p.id] == 'field' for p in paths if p.role == 'lattice')


# ---------------------------------------------------------------------------
# Closed vs open layer routes (multi-layer readiness)
# ---------------------------------------------------------------------------

class TestLayerEnds:

    def test_closed_when_cheap(self):
        moves, m, meta, _ = _route(RING_INFILL())
        assert route_ends(moves).closed
        assert all(meta['network']['route_plan']['closed_components'])

    def test_open_when_closing_is_expensive(self):
        # Plain rectangle + one centred branch: 2 odd ends; closing would
        # need a long parallel return (> 5 %) → open route, ends reported.
        moves, m, meta, _ = _route(RECT_BRANCHES(1, infill=False))
        e = route_ends(moves)
        assert not e.closed
        assert m['retrace_distance'] == 0

    def test_prefer_closed_off_keeps_an_open_route(self):
        L = RECT_BRANCHES(2, prefer_closed=False)
        moves, m, meta, _ = _route(L)
        assert not route_ends(moves).closed
        assert m['retrace_distance'] == 0 and m['travel_moves'] == 0

    def test_open_route_alternates_between_layers(self):
        moves, _, _, _ = _route(RECT_BRANCHES(1, infill=False))
        e = route_ends(moves)
        nxt, d = orient_for_previous(moves, e.end)      # layer N+1 after N
        assert d == pytest.approx(0.0)
        assert route_ends(nxt).start == e.end and route_ends(nxt).end == e.start
        assert geometric_overlap(nxt) < 1e-6

    def test_closed_route_needs_no_reversal(self):
        moves, _, _, _ = _route(RING_INFILL())
        e = route_ends(moves)
        nxt, d = orient_for_previous(moves, e.end)
        assert d == pytest.approx(0.0) and nxt is moves


# ---------------------------------------------------------------------------
# Parametric wall thickness (WallSpec) and network walls
# ---------------------------------------------------------------------------

def _offset_of(paths, oid):
    return next(p for p in paths if p.id == oid)


class TestWallSpec:

    @pytest.mark.parametrize('w,h', [(200, 120), (260, 90), (150, 150)])
    def test_inner_boundary_stays_ten_inches_inside(self, w, h):
        R = RectanglePath(100, 100, w, h, id='R')
        R.wall = WallSpec(10)
        paths = PrintLayer('t', source_paths=[R]).effective_paths()
        inner = _offset_of(paths, 'R.wall').sample_points()
        src = _processed_source_pts(R, 0)
        for q in inner:
            assert N.dist_to_polyline(q, src, True) == pytest.approx(10.0, abs=1e-6)
            assert 100 < q.x < 100 + w and 100 < q.y < 100 + h        # inside

    def test_resizing_the_outer_updates_the_inner(self):
        R = RectanglePath(0, 0, 200, 120, id='R')
        R.wall = WallSpec(10)
        L = PrintLayer('t', source_paths=[R])
        before = _offset_of(L.effective_paths(), 'R.wall').sample_points()
        R.w, R.h, R.x = 300, 80, 40                     # edit the source only
        after = _offset_of(L.effective_paths(), 'R.wall').sample_points()
        assert before != after
        xs = [q.x for q in after]
        ys = [q.y for q in after]
        assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((50, 330, 10, 70))

    def test_alignments(self):
        R = RectanglePath(0, 0, 100, 100, id='R')
        R.wall = WallSpec(10, 'outside')
        out = _offset_of(PrintLayer('t', source_paths=[R]).effective_paths(), 'R.wall')
        assert min(q.x for q in out.sample_points()) == pytest.approx(-10)
        Ln = LinePath(Vec2(0, 0), Vec2(100, 0), id='L')
        Ln.wall = WallSpec(10)                          # auto: open → centre
        ps = {p.id: p for p in PrintLayer('t', source_paths=[Ln]).effective_paths()}
        ys = sorted(ps[k].sample_points()[0].y for k in ('L.wall+', 'L.wall-'))
        assert ys == pytest.approx([-5, 5])

    def test_clockwise_rectangle_inside_is_still_inside(self):
        P = ExplicitPath([Vec2(0, 0), Vec2(0, 100), Vec2(100, 100), Vec2(100, 0)],
                         id='P', closed=True)
        P.wall = WallSpec(10)
        inner = _offset_of(PrintLayer('t', source_paths=[P]).effective_paths(), 'P.wall')
        assert all(0 < q.x < 100 and 0 < q.y < 100 for q in inner.sample_points())

    def test_wall_spec_and_extra_offsets_coexist(self):
        R = RectanglePath(0, 0, 100, 100, id='R')
        R.wall = WallSpec(10)
        L = PrintLayer('t', source_paths=[R],
                       offset_treatments=[OffsetTreatment('x', 'R', 5)])
        ids = {p.id for p in L.effective_paths()}
        assert {'R.wall', 'x'} <= ids


class TestNetworkWall:

    def test_star_gets_one_wall_thickness(self):
        L = STAR(infill=False)
        ids = {p.id.split('~')[0] for p in L.effective_paths()}
        for k in range(1, 7):
            assert {f'L{k}.wall+', f'L{k}.wall-'} <= ids
        nets = L.network_summary()['source_networks']
        assert nets == [{'id': 'N1', 'sources': [f'L{k}' for k in range(1, 7)],
                         'wall': {'id': 'W', 'type': 'network_wall', 'path_id': 'L1',
                                  'thickness': 10, 'align': 'auto',
                                  'print_reference': False}}]
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)

    def test_path_wall_overrides_network_wall(self):
        L = STAR(infill=False)
        L.source_paths[2].wall = WallSpec(20)
        src = L.source_paths[2]
        face = [q for p in L.effective_paths() if p.id.startswith('L3.wall+')
                for q in p.sample_points()]
        far = max(face, key=lambda q: q.dist(Vec2(200, 200)))   # away from the hub
        assert N.dist_to_polyline(far, [src.start, src.end], False) == pytest.approx(10)

    def test_newly_connected_path_inherits(self):
        L = STAR(infill=False)
        L.source_paths.append(LinePath(Vec2(200, 200), Vec2(200, 40), id='L7'))
        assert 'L7.wall+' in {p.id.split('~')[0] for p in L.effective_paths()}

    def test_disconnected_path_does_not_inherit(self):
        L = STAR(infill=False)
        L.source_paths.append(LinePath(Vec2(600, 600), Vec2(700, 600), id='X'))
        assert not any(p.id.startswith('X.wall') for p in L.effective_paths())

    def test_network_is_defined_by_source_contact(self):
        L = PrintLayer('t', source_paths=[RectanglePath(0, 0, 100, 100, id='R'),
                                          LinePath(Vec2(50, 0), Vec2(50, -60), id='B'),
                                          LinePath(Vec2(300, 0), Vec2(400, 0), id='C')])
        nets = L.network_summary()['source_networks']
        assert [n['sources'] for n in nets] == [['R', 'B']]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    design_app.app.config['TESTING'] = True
    with design_app.app.test_client() as c:
        yield c


def test_api_round_trip_and_route_ends(client):
    payload = {
        'id': 't',
        'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'x': 100, 'y': 140, 'w': 200, 'h': 120,
             'closed': True, 'wall': {'thickness': 10, 'align': 'inside'}},
            {'id': 'B1', 'type': 'LinePath', 'start': [200, 260], 'end': [200, 340]},
            {'id': 'B2', 'type': 'LinePath', 'start': [300, 200], 'end': [380, 200]},
        ],
        'network_walls': [{'id': 'W', 'path_id': 'B1', 'thickness': 10, 'align': 'auto'}],
        'infills': [{'id': 'I', 'path_id': 'R', 'pattern': 'zigzag', 'params': {'spacing': 20}}],
        'return_paths': True, 'prefer_closed': True,
    }
    layer = design_app._deserialise_layer(payload)
    d = layer.to_dict()
    assert d['source_paths'][0]['wall'] == {'thickness': 10.0, 'align': 'inside',
                                            'print_reference': False}
    assert d['network_walls'][0]['thickness'] == 10.0
    assert d['return_paths'] is True and d['prefer_closed'] is True
    r = client.post('/api/route', data=json.dumps(payload), content_type='application/json')
    out = r.get_json()
    assert r.status_code == 200, out
    assert isinstance(out['route_ends']['closed'], bool)
    assert out['metrics']['retrace_distance'] == 0
    # (superseded: was > 0 — the motif lattice needs no repair edits)
    assert out['network']['route_plan']['repaired_pairs'] == 0
    assert out['network']['lattice']['I']['motif'] is True
    assert out['network']['reference_only'] == ['B1', 'B2']
    assert out['network']['source_networks'][0]['sources'] == ['R', 'B1', 'B2']
