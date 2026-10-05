"""
Wall-region infill, per-source Corner R and network junction corners.

  * Infill belongs to the printable WALL REGION (a polygon with any number
    of holes, incl. branches / junctions / openings), not to a pair of
    boundaries: one coherent field per region, never across a void.
  * Corner R belongs to each source path (its own original corners).
  * Corners CREATED by the wall network (junctions) have their own
    treatment — Miter or Rounded + radius — independent of Corner R.

Lettered classes follow the brief (A–N).
"""
import sys, os, math, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as design_app
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..',
                             'toolpath_proto'))

import pytest
from graph import route_layer, compute_metrics
import network as N
from model import (
    Vec2, ExplicitPath, LinePath, CirclePath, RectanglePath, QuadBezierPath,
    OffsetTreatment, LatticeInstance, PrintLayer, Opening, RegionInfill,
    JunctionSetting, WallSpec, _processed_source_pts,
)
from tests.test_networks import (
    _ot, _build, _route, _metrics, _pts, _segments, _sig,
    _assert_faces_bound_material, _assert_internal_inside,
    _assert_no_duplicate_walls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _layer(srcs, offsets=(), infills=(), openings=(), lattices=(), **kw):
    return PrintLayer(id='t', source_paths=list(srcs),
                      offset_treatments=list(offsets), infills=list(infills),
                      openings=list(openings), lattice_instances=list(lattices),
                      **kw)


def _inf(pid, spacing=20.0, pattern='zigzag', fid='I', variation=0):
    return RegionInfill(fid, pid, pattern, {'spacing': spacing}, variation)


def _infill(layer):
    paths, meta = _build(layer)
    return [p for p in paths if getattr(p, 'treatment_id', '') == 'infill'], paths, meta


def _strut_segments(layer):
    inf, _, meta = _infill(layer)
    return [(p.source_id, a, b) for p in inf
            for a, b in zip(_pts(p, meta), _pts(p, meta)[1:])]


def _samples(layer, k=12):
    for _, a, b in _strut_segments(layer):
        for i in range(1, k):
            yield a.lerp(b, i / k)


def _in_rect(q, x, y, w, h, tol=1e-6):
    return x + tol < q.x < x + w - tol and y + tol < q.y < y + h - tol


def ROOM(**kw):
    return RectanglePath(100, 140, 200, 120, id='R', **kw)


def T_LAYER(branch_end=(230, 40), **kw):
    return _layer([ROOM(corner_radius=kw.pop('room_r', None)),
                   LinePath(Vec2(200, 140), Vec2(*branch_end), id='B')],
                  [_ot('Ri', 'R', 10), _ot('Bl', 'B', 8), _ot('Br', 'B', -8)],
                  **kw)


def _circle_radius(pts):
    """Radius of the circle through the first, middle and last point."""
    a, b, c = pts[0], pts[len(pts) // 2], pts[-1]
    d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    ux = ((a.x ** 2 + a.y ** 2) * (b.y - c.y) + (b.x ** 2 + b.y ** 2) * (c.y - a.y)
          + (c.x ** 2 + c.y ** 2) * (a.y - b.y)) / d
    uy = ((a.x ** 2 + a.y ** 2) * (c.x - b.x) + (b.x ** 2 + b.y ** 2) * (a.x - c.x)
          + (c.x ** 2 + c.y ** 2) * (b.x - a.x)) / d
    return math.hypot(a.x - ux, a.y - uy)


def _junction_arcs(layer):
    paths, meta = _build(layer)
    return [p for p in paths if p.id.startswith('junction:')]


def _corners(layer):
    return [j for j in layer.network_summary()['junctions'] if j['corner']]


# ---------------------------------------------------------------------------
# A  Simple isolated double wall
# ---------------------------------------------------------------------------

class TestA_IsolatedDoubleWall:

    def _ring(self, **kw):
        return _layer([ROOM()], [_ot('Ri', 'R', 10)], **kw)

    def test_legacy_pairwise_lattice_output_unchanged(self):
        # The old Boundary A/B lattice still produces exactly its old output
        # (backend only — the UI no longer creates it).
        lat = [LatticeInstance('LR', 'zigzag', 'R', 'Ri',
                               {'segments': 12, 'connect_ends': 1})]
        from tests.test_networks import _without_networks
        L = self._ring(lattices=lat)
        assert _sig(L.effective_paths()) == _sig(_without_networks(self._ring(lattices=lat)))

    def test_region_infill_struts_span_the_wall(self):
        L = self._ring(infills=[_inf('R', 20)])
        outer = _processed_source_pts(ROOM(), 0)
        inner = [Vec2(110, 150), Vec2(290, 150), Vec2(290, 250), Vec2(110, 250)]
        face = lambda q: min(N.dist_to_polyline(q, outer, True),
                             N.dist_to_polyline(q, inner, True))
        # stitches = polylines split where they land on a face (round a
        # corner a stitch bends with the wall: interior vertices)
        inf, _, meta = _infill(L)
        stitches = []
        for p in inf:
            pts = _pts(p, meta)
            cur = [pts[0]]
            for q in pts[1:]:
                cur.append(q)
                if face(q) < 1e-6:
                    stitches.append(cur)
                    cur = [q]
            assert len(cur) == 1, 'a stitch ends off the faces'
        # ≈ centre line / target spacing (600 / 20 = 30), ±20 % (target
        # spacing; a closed ring needs an even count)
        assert 0.8 * 30 <= len(stitches) <= 1.2 * 30
        for st in stitches:
            a, b = st[0], st[-1]
            # each stitch runs face to face: one end on each face
            assert face(a) < 1e-6 and face(b) < 1e-6
            assert (N.dist_to_polyline(a, outer, True) < 1e-6) != \
                (N.dist_to_polyline(b, outer, True) < 1e-6)
            # no long struts (no starburst across the region). Spacing is a
            # TARGET; the structural bound is the maximum unsupported
            # distance (1.375 × target) along the wall, and a stitch into a
            # pinned corner adds the corner diagonal (thickness × √2)
            assert a.dist(b) <= math.hypot(10, 1.375 * 20) + 10 * math.sqrt(2) + 1e-6
        _assert_internal_inside(L)

    def test_routes_as_one_run(self):
        m = _metrics(self._ring(infills=[_inf('R', 20)]))
        assert (m['print_runs'], m['travel_moves']) == (1, 0)

    def test_without_infill_output_identical(self):
        assert _sig(self._ring().effective_paths()) == \
            _sig(_layer([ROOM()], [_ot('Ri', 'R', 10)]).effective_paths())


# ---------------------------------------------------------------------------
# B  Rectangle + attached double-wall branch
# ---------------------------------------------------------------------------

class TestB_RectangleWithBranch:

    def test_one_region_one_field(self):
        L = T_LAYER(infills=[_inf('R')])
        info = L.network_summary()['infills']
        # (region / kind / voids / islands: added reporting, this pass)
        lat = info[0].pop('lattice')             # (wall-lattice diagnostics)
        assert lat['motif'] is True and 0.75 * 20 <= lat['pitch_min'] <= lat['pitch_max'] <= 1.25 * 20
        assert info == [{'id': 'I', 'regions': 1, 'holes': [1],
                         'shadowed_by': None, 'status': 'ok', 'region': 'R',
                         'kind': 'wall', 'voids': [], 'islands': []}]
        inf, _, _ = _infill(L)
        assert {p.source_id for p in inf} == {'I'}

    def test_fills_room_wall_and_branch(self):
        L = T_LAYER(infills=[_inf('R')])
        pts = list(_samples(L))
        assert any(q.y < 130 for q in pts), 'branch not filled'
        assert any(q.y > 250 for q in pts), 'room wall not filled'

    def test_no_starburst_and_never_across_void(self):
        L = T_LAYER(infills=[_inf('R', 20)])
        _assert_internal_inside(L)
        for _, a, b in _strut_segments(L):
            assert a.dist(b) < 2.5 * 20, 'long strut across the region'
        for q in _samples(L):
            assert not _in_rect(q, 110, 150, 180, 100), 'infill inside the room'

    def test_continuous_route_retrace_inside(self):
        L = T_LAYER(infills=[_inf('R')])
        paths, meta = _build(L)
        cls = {p.id: meta['cls'].get(id(p)) for p in paths}
        moves = route_layer(L.to_routing_layer(paths, meta))
        m = compute_metrics(moves)
        assert (m['print_runs'], m['travel_moves']) == (1, 0)
        assert {cls[mv.strand_id] for mv in moves if mv.kind == 'retrace'} <= {N.INTERNAL}

    def test_infill_anchored_on_the_branch_fills_the_same_region(self):
        a = T_LAYER(infills=[_inf('R')])
        b = T_LAYER(infills=[_inf('B')])
        assert _sig(_infill(a)[0]) == _sig(_infill(b)[0])

    def test_second_infill_on_same_region_is_shadowed(self):
        L = T_LAYER(infills=[_inf('R', fid='I1'), _inf('B', fid='I2')])
        info = {i['id']: i for i in L.network_summary()['infills']}
        assert info['I1']['status'] == 'ok'
        assert info['I2']['status'] == 'shadowed' and info['I2']['shadowed_by'] == 'I1'

    @pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
    def test_patterns(self, pattern):
        L = T_LAYER(infills=[_inf('R', pattern=pattern)])
        _assert_internal_inside(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_variation_shifts_the_phase(self):
        # Superseded for networks: the phase of a run between junctions is
        # fixed by junction coherence (passes hand over at shared corners),
        # so V1/V2 shift the phase where it is free — a closed wall ring.
        R = ROOM()
        R.wall = WallSpec(10)
        a = _infill(_layer([R], infills=[_inf('R', variation=0)]))[0]
        R2 = ROOM()
        R2.wall = WallSpec(10)
        b = _infill(_layer([R2], infills=[_inf('R', variation=1)]))[0]
        assert _sig(a) != _sig(b)


# ---------------------------------------------------------------------------
# C / D / E  Junction treatment independent of source corners
# ---------------------------------------------------------------------------

def Y_LAYER(**kw):
    return _layer([LinePath(Vec2(0, 0), Vec2(100, 100), id='A'),
                   LinePath(Vec2(200, 0), Vec2(100, 100), id='B'),
                   LinePath(Vec2(100, 100), Vec2(100, 220), id='C')],
                  [_ot(f'{s}{d}', s, v) for s in 'ABC' for d, v in (('l', 6), ('r', -6))],
                  **kw)


def X_LAYER(**kw):
    return _layer([LinePath(Vec2(0, 100), Vec2(200, 100), id='A'),
                   LinePath(Vec2(100, 0), Vec2(130, 200), id='B')],
                  [_ot('Al', 'A', 8), _ot('Ar', 'A', -8),
                   _ot('Bl', 'B', 8), _ot('Br', 'B', -8)], **kw)


class TestCDE_Junctions:

    def test_t_corners_found_and_mitre_by_default(self):
        L = T_LAYER(branch_end=(200, 40))
        cs = _corners(L)
        assert len(cs) == 2
        assert all(c['treatment'] == 'miter' for c in cs)
        assert not _junction_arcs(L)
        keys = {c['key'] for c in cs}
        assert keys == {'Bl|R#0', 'Br|R#0'}

    def test_t_rounded_junction_has_that_radius(self):
        L = T_LAYER(branch_end=(200, 40), junction_style='round', junction_radius=3)
        arcs = _junction_arcs(L)
        assert len(arcs) == 2
        for a in arcs:
            assert _circle_radius(a.sample_points()) == pytest.approx(3.0, abs=1e-6)
        _assert_faces_bound_material(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_t_rounded_source_keeps_its_own_radius(self):
        # Rectangle Corner R 30 with a 2 in junction: both valid together.
        mk = lambda **kw: T_LAYER(branch_end=(200, 40), room_r=30, **kw)
        sharp, soft = mk(), mk(junction_style='round', junction_radius=2)
        arcs = _junction_arcs(soft)
        assert arcs and all(_circle_radius(a.sample_points()) == pytest.approx(2.0, abs=1e-6)
                            for a in arcs)
        # The room's own rounded corners are identical either way.
        far = lambda L: [q for p in _build(L)[0] if p.id.startswith('R')
                         for q in _pts(p, _build(L)[1]) if q.y > 200]
        assert sorted((round(q.x, 9), round(q.y, 9)) for q in far(sharp)) == \
            sorted((round(q.x, 9), round(q.y, 9)) for q in far(soft))
        # …and they really are R 30 arcs, not the junction radius.
        room = _processed_source_pts(ROOM(corner_radius=30), 30)
        arc = [q for q in room if q.x > 270 and q.y > 230]
        assert _circle_radius(arc) == pytest.approx(30.0, abs=1e-6)

    def test_rounded_junction_arc_is_tangent_and_joins_the_faces(self):
        # The arc replaces the corner: its ends are where the two faces now
        # end (shared points), and it stays within r of the old corner.
        L = T_LAYER(branch_end=(200, 40), junction_style='round', junction_radius=3)
        paths, meta = _build(L)
        ends = {(round(q.x, 9), round(q.y, 9)) for p in paths
                if not p.id.startswith('junction:') and p.role != 'lattice'
                for q in (_pts(p, meta)[0], _pts(p, meta)[-1])}
        for a in _junction_arcs(L):
            pts = a.sample_points()
            for q in (pts[0], pts[-1]):
                assert (round(q.x, 9), round(q.y, 9)) in ends
            corner = Vec2(192, 140) if a.id.startswith('junction:Br') else Vec2(208, 140)
            assert all(q.dist(corner) <= 3 * math.sqrt(2) + 1e-6 for q in pts)

    def test_per_junction_override(self):
        L = T_LAYER(branch_end=(200, 40),
                    junction_overrides=[JunctionSetting('Bl|R#0', 'round', 4)])
        cs = {c['key']: c for c in _corners(L)}
        assert cs['Bl|R#0']['treatment'] == 'round' and cs['Bl|R#0']['override']
        assert cs['Br|R#0']['treatment'] == 'miter'
        arcs = _junction_arcs(L)
        assert [a.id for a in arcs] == ['junction:Bl|R#0']
        assert _circle_radius(arcs[0].sample_points()) == pytest.approx(4.0, abs=1e-6)

    def test_y_junction_rounding_independent_of_sources(self):
        L = Y_LAYER(junction_style='round', junction_radius=5)
        arcs = _junction_arcs(L)
        assert len(arcs) == 3
        for a in arcs:
            assert _circle_radius(a.sample_points()) == pytest.approx(5.0, abs=1e-3)
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_x_junction_stays_connected_with_infill(self):
        L = X_LAYER(infills=[_inf('A', 18)], junction_style='round', junction_radius=3)
        assert len(_junction_arcs(L)) == 4
        _assert_internal_inside(L)
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves']) == (1, 0)

    def test_junction_key_survives_moving_the_branch(self):
        a = {c['key'] for c in _corners(T_LAYER(branch_end=(200, 40)))}
        L = T_LAYER(branch_end=(260, 40))
        L.source_paths[1].start = Vec2(250, 140)
        b = {c['key'] for c in _corners(L)}
        assert a == b

    def test_internal_junctions_are_not_corners(self):
        # The centre line meeting the inner face is a junction, not a corner.
        L = T_LAYER(branch_end=(200, 40))
        js = L.network_summary()['junctions']
        assert any(not j['corner'] for j in js)


# ---------------------------------------------------------------------------
# F / G / H / I  Regions with holes
# ---------------------------------------------------------------------------

def _holes_layer(n, thick_outer=False, branch=False, spacing=22):
    O = RectanglePath(0, 0, 60 + 95 * n, 200, id='O')
    holes = [RectanglePath(30 + i * 95, 60, 60, 70, id=f'H{i}') for i in range(n)]
    srcs = [O] + holes
    offs = [_ot('Oi', 'O', 10)] if thick_outer else []
    if branch:
        srcs.append(LinePath(Vec2(60 + 95 * n, 100), Vec2(140 + 95 * n, 140), id='Br'))
        offs += [_ot('Bl', 'Br', 6), _ot('Brr', 'Br', -6)]
    return _layer(srcs, offs, [_inf('O', spacing)]), holes


class TestFGHI_Holes:

    @pytest.mark.parametrize('n', [1, 2, 3, 5])
    def test_every_hole_is_respected(self, n):
        L, holes = _holes_layer(n)
        info = L.network_summary()['infills'][0]
        assert info['status'] == 'ok' and info['holes'] == [n]
        pts = list(_samples(L))
        assert pts
        for h in holes:
            assert not any(_in_rect(q, h.x, h.y, h.w, h.h) for q in pts), \
                f'infill inside hole {h.id}'
        # and inside the outer wall only
        assert all(_in_rect(q, -1e-6, -1e-6, 60 + 95 * n + 2e-6, 200 + 2e-6) for q in pts)

    @pytest.mark.parametrize('n', [1, 2, 3])
    def test_infill_lands_on_every_hole(self, n):
        L, holes = _holes_layer(n)
        segs = _strut_segments(L)
        for h in holes:
            ring = _processed_source_pts(h, 0)
            assert any(min(N.dist_to_polyline(a, ring, True),
                           N.dist_to_polyline(b, ring, True)) < 1e-6
                       for _, a, b in segs), f'nothing lands on {h.id}'

    def test_two_holes_route_continuously(self):
        L, _ = _holes_layer(2)
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves']) == (1, 0)

    def test_struts_never_cross_a_hole_edge(self):
        L, holes = _holes_layer(3)
        from model import _segments_intersect
        for _, a, b in _strut_segments(L):
            for h in holes:
                ring = _processed_source_pts(h, 0)
                for c, d in zip(ring, ring[1:] + ring[:1]):
                    assert not _segments_intersect(a, b, c, d)

    def test_hole_plus_branch_is_one_coherent_field(self):
        L, holes = _holes_layer(2, branch=True)
        info = L.network_summary()['infills'][0]
        assert info['regions'] == 1 and info['holes'] == [2]
        pts = list(_samples(L))
        assert any(q.x > 60 + 95 * 2 + 5 for q in pts), 'branch not filled'
        for h in holes:
            assert not any(_in_rect(q, h.x, h.y, h.w, h.h) for q in pts)
        assert _metrics(L)['travel_moves'] == 0

    def test_thick_outer_wall_is_the_region_not_its_room(self):
        # With an offset the outer rectangle is a double wall: its band is
        # the region; the room (with the islands) is void.
        L, _ = _holes_layer(2, thick_outer=True)
        info = L.network_summary()['infills'][0]
        assert info['holes'] == [1]
        for q in _samples(L):
            assert not _in_rect(q, 10, 10, 60 + 95 * 2 - 20, 180)


# ---------------------------------------------------------------------------
# J  Opening through a network wall
# ---------------------------------------------------------------------------

class TestJ_Openings:

    def test_infill_clipped_from_doorways_and_caps_intact(self):
        L = T_LAYER(infills=[_inf('R')],
                    openings=[Opening('o', 'R', 130, 24), Opening('o2', 'R', 420, 30)])
        for q in _samples(L):
            assert not _in_rect(q, 218, 140, 24, 10), 'infill in doorway 1'
        ids = {p.id for p in L.effective_paths()}
        assert any(i.endswith('_cs') or '_cs' in i for i in ids)   # cut faces capped
        info = L.network_summary()['infills'][0]
        assert info['regions'] == 2                # two wall pieces, both filled
        _assert_internal_inside(L)

    def test_opening_cap_style_still_applies(self):
        L = T_LAYER(infills=[_inf('R')], cap_style='full_round',
                    openings=[Opening('o', 'R', 130, 24)])
        caps = [p for p in L.effective_paths() if p.role == 'cap' and 'R~' in p.id]
        assert caps and all(len(p.sample_points()) > 2 for p in caps)


# ---------------------------------------------------------------------------
# K / L / M  Source Corner R per path
# ---------------------------------------------------------------------------

class TestKLM_SourceCorners:

    def test_corner_radius_belongs_to_each_path(self):
        R1 = RectanglePath(0, 0, 160, 120, id='R1', corner_radius=25)
        R2 = RectanglePath(300, 0, 160, 120, id='R2', corner_radius=0)
        paths = {p.id: p for p in _layer([R1, R2]).effective_paths()}
        assert len(paths['R1'].sample_points()) > 4      # rounded
        assert len(paths['R2'].sample_points()) == 4     # sharp

    def test_changing_one_path_leaves_others(self):
        mk = lambda r: _layer([RectanglePath(0, 0, 160, 120, id='R1', corner_radius=r),
                               RectanglePath(300, 0, 160, 120, id='R2', corner_radius=8)])
        a = {p.id: p for p in mk(5).effective_paths()}
        b = {p.id: p for p in mk(40).effective_paths()}
        assert _sig([a['R2']]) == _sig([b['R2']])
        assert _sig([a['R1']]) != _sig([b['R1']])

    def test_legacy_layer_corner_radius_still_applies(self):
        L = _layer([RectanglePath(0, 0, 160, 120, id='R1')], corner_radius=20)
        assert len(L.effective_paths()[0].sample_points()) > 4

    def test_path_value_overrides_legacy_layer_value(self):
        L = _layer([RectanglePath(0, 0, 160, 120, id='R1', corner_radius=0)],
                   corner_radius=20)
        assert len(L.effective_paths()[0].sample_points()) == 4

    def test_drawn_path_corner_radius(self):
        P = ExplicitPath([Vec2(0, 0), Vec2(100, 0), Vec2(100, 80)], id='P',
                         corner_radius=15)
        assert len(_layer([P]).effective_paths()[0].sample_points()) > 3

    def test_different_radii_in_one_network(self):
        R1 = RectanglePath(0, 0, 160, 120, id='R1', corner_radius=25)
        P = ExplicitPath([Vec2(160, 60), Vec2(240, 60), Vec2(240, 140), Vec2(300, 140)],
                         id='P', corner_radius=0)
        L = _layer([R1, P], [_ot('R1i', 'R1', 10), _ot('Pl', 'P', 6), _ot('Pr', 'P', -6)],
                   [_inf('R1')])
        assert len(L.network_summary()['components']) == 1
        paths, meta = _build(L)
        p_pts = [q for p in paths if p.id == 'P' or p.id.startswith('P~')
                 for q in _pts(p, meta)]
        assert any(q.x == 240 and q.y == 60 for q in p_pts), 'P lost its sharp corner'
        _assert_faces_bound_material(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_offsets_follow_the_rounded_source(self):
        # Wall thickness stays correct: offset of the rounded source.
        R1 = RectanglePath(0, 0, 160, 120, id='R1', corner_radius=25)
        paths = {p.id: p for p in _layer([R1], [_ot('i', 'R1', 10)]).effective_paths()}
        src = paths['R1'].sample_points()
        for q in paths['i'].sample_points():
            assert N.dist_to_polyline(q, src, True) == pytest.approx(10.0, abs=0.05)

    def test_rounded_source_with_sharp_junction(self):
        L = T_LAYER(branch_end=(200, 40), room_r=30)
        assert len(_corners(L)) == 2 and not _junction_arcs(L)

    def test_rounded_source_with_small_rounded_junction(self):
        L = T_LAYER(branch_end=(200, 40), room_r=30, junction_style='round',
                    junction_radius=2)
        arcs = _junction_arcs(L)
        assert len(arcs) == 2
        assert all(_circle_radius(a.sample_points()) == pytest.approx(2.0, abs=1e-6)
                   for a in arcs)


# ---------------------------------------------------------------------------
# N  Disconnected networks
# ---------------------------------------------------------------------------

class TestN_Disconnected:

    def _two(self, infills):
        srcs = [RectanglePath(0, 0, 120, 100, id='R1'),
                RectanglePath(300, 0, 120, 100, id='R2')]
        return _layer(srcs, [_ot('a', 'R1', 10), _ot('b', 'R2', 10)], infills)

    def test_each_network_has_its_own_field(self):
        L = self._two([_inf('R1', fid='I1'), _inf('R2', fid='I2')])
        inf, _, meta = _infill(L)
        for p in inf:
            xs = [q.x for q in _pts(p, meta)]
            if p.source_id == 'I1':
                assert max(xs) <= 120 + 1e-6
            else:
                assert min(xs) >= 300 - 1e-6

    def test_infill_does_not_leak_into_another_network(self):
        L = self._two([_inf('R1')])
        inf, _, meta = _infill(L)
        assert inf and all(q.x <= 120 + 1e-6 for p in inf for q in _pts(p, meta))

    def test_router_travels_between_networks(self):
        m = _metrics(self._two([_inf('R1', fid='I1'), _inf('R2', fid='I2')]))
        assert m['travel_moves'] == 1 and m['print_runs'] == 2


# ---------------------------------------------------------------------------
# Material / void inference edge cases (documented ambiguity)
# ---------------------------------------------------------------------------

class TestMaterialInference:

    def test_open_single_bead_path_has_no_material(self):
        L = _layer([LinePath(Vec2(0, 0), Vec2(100, 0), id='W')], infills=[_inf('W')])
        assert L.network_summary()['infills'][0]['status'] == 'no wall material'

    def test_lone_closed_single_bead_wall_fills_its_inside(self):
        # The only possible reading of "infill this closed line": a slab.
        L = _layer([RectanglePath(0, 0, 100, 60, id='S')], infills=[_inf('S', 20)])
        info = L.network_summary()['infills'][0]
        assert info['status'] == 'ok' and info['holes'] == [0]

    def test_island_inside_a_hole_is_wall_again(self):
        # Even-odd nesting: outer (wall) ⊃ hole (void) ⊃ island (wall).
        O = RectanglePath(0, 0, 300, 200, id='O')
        H = RectanglePath(50, 40, 200, 120, id='H')
        I = RectanglePath(120, 80, 60, 40, id='I')
        L = _layer([O, H, I], infills=[_inf('O', 20)])
        info = L.network_summary()['infills'][0]
        # Pass 5: the island is now actually FILLED (it was declared wall but
        # never reached: regions == 1). Outer band (1 hole) + the island.
        assert info['regions'] == 2 and sorted(info['holes']) == [0, 1]
        pts = list(_samples(L))
        assert not any(_in_rect(q, 50, 40, 200, 120) and not _in_rect(q, 120, 80, 60, 40)
                       for q in pts)
        assert any(_in_rect(q, 120, 80, 60, 40) for q in pts)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    design_app.app.config['TESTING'] = True
    with design_app.app.test_client() as c:
        yield c


class TestApi:

    def _payload(self):
        return {
            'id': 't',
            'source_paths': [
                {'id': 'R', 'type': 'RectanglePath', 'x': 100, 'y': 140, 'w': 200,
                 'h': 120, 'closed': True, 'corner_radius': 20},
                {'id': 'B', 'type': 'LinePath', 'start': [200, 140], 'end': [200, 40]},
            ],
            'offset_treatments': [
                {'id': 'Ri', 'source_path_id': 'R', 'distance': 10},
                {'id': 'Bl', 'source_path_id': 'B', 'distance': 8},
                {'id': 'Br', 'source_path_id': 'B', 'distance': -8},
            ],
            'infills': [{'id': 'I', 'path_id': 'R', 'pattern': 'wave',
                         'params': {'spacing': 18}, 'variation_index': 1}],
            'junction_style': 'round', 'junction_radius': 2,
            'junction_overrides': [{'key': 'Bl|R#0', 'treatment': 'miter', 'radius': 0}],
        }

    def test_patterns_endpoint(self, client):
        d = client.get('/api/infill_patterns').get_json()
        # pass 5: patterns carry their KIND; solid patterns were added
        assert {p['name'] for p in d if p['kind'] == 'wall'} == {'zigzag', 'wave'}
        # (pass 7 adds the solid 'serpentine' pattern — a distinct name: the
        # UI keys patterns by name, so it must not collide with wall 'wave')
        assert {p['name'] for p in d if p['kind'] == 'solid'} == {'rectilinear', 'serpentine'}
        assert len({p['name'] for p in d}) == len(d)
        assert d[0]['parameters'][0]['name'] == 'spacing'

    def test_round_trip(self):
        layer = design_app._deserialise_layer(self._payload())
        d = layer.to_dict()
        assert d['infills'][0]['pattern'] == 'wave'
        assert d['junction_overrides'] == [{'key': 'Bl|R#0', 'treatment': 'miter', 'radius': 0.0}]
        assert d['source_paths'][0]['corner_radius'] == 20

    def test_route(self, client):
        r = client.post('/api/route', data=json.dumps(self._payload()),
                        content_type='application/json')
        d = r.get_json()
        assert r.status_code == 200, d
        assert d['metrics']['travel_moves'] == 0
        js = {j['key']: j for j in d['network']['junctions'] if j['corner']}
        assert js['Bl|R#0']['treatment'] == 'miter' and js['Br|R#0']['treatment'] == 'round'
        assert any(p['id'] == 'junction:Br|R#0' for p in d['layer']['paths'])
        assert any(p.get('treatment_id') == 'infill' for p in d['layer']['paths'])
        assert d['network']['infills'][0]['status'] == 'ok'
