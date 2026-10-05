"""
Openings — path-relative gaps cut through a whole wall assembly.

An Opening is an arc-length interval along a SOURCE path. It cuts the
source, every offset of it and any lattice built on those walls; the cut
faces are closed by the same wall-end builder as open wall ends, and the
router then solves whatever topology results.
"""
import sys, os, json, math, shutil, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as design_app          # before toolpath_proto (it has an app.py too)
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..',
                             'toolpath_proto'))

import pytest
import networkx as nx
from graph import route_layer, compute_metrics, build_graph
from model import (
    Vec2, ExplicitPath, LinePath, CirclePath, EllipsePath, RectanglePath,
    QuadBezierPath, OffsetTreatment, LatticeInstance, PrintLayer, Opening,
    _processed_source_pts, _cum_lengths, _locate_s, _project_to_polyline,
    _segments_intersect,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _layer(src, offsets=(), openings=(), lattices=(), **kw):
    srcs = src if isinstance(src, list) else [src]
    return PrintLayer(id='t', source_paths=srcs,
                      offset_treatments=list(offsets),
                      openings=list(openings),
                      lattice_instances=list(lattices), **kw)


def _ot(oid, sid, d):
    return OffsetTreatment(id=oid, source_path_id=sid, distance=d)


def _metrics(layer):
    return compute_metrics(route_layer(layer.to_routing_layer()))


def _graph(layer):
    return build_graph(layer.to_routing_layer())


def _by_id(paths):
    return {p.id: p for p in paths}


def _pieces(paths, pid):
    """Wall pieces of a source/offset id, in piece order."""
    out = [p for p in paths
           if p.id.startswith(pid + '~') and '_c' not in p.id[len(pid):]]
    return sorted(out, key=lambda p: int(p.id.split('~')[1]))


def _plen(pts, closed=False):
    n = len(pts)
    return sum(pts[i].dist(pts[(i + 1) % n])
               for i in range(n if closed else n - 1))


def _xy(p, nd=6):
    return (round(p.x, nd), round(p.y, nd))


def _src_frame(path, corner_radius=0.0):
    pts = _processed_source_pts(path, corner_radius)
    return pts, _cum_lengths(pts, path.closed)


def _in_interval(s, a, b, L, closed, tol=1e-6):
    """Strictly inside the removed interval [a, b] (b may exceed L)."""
    if a + tol < s < b - tol:
        return True
    return closed and a + tol < s + L < b - tol


def _face_skew(paths, src_pts, cum, closed, a, b):
    """How far the actual cut faces deviate (in source arc length) from the
    nominal cut positions a, b. On a polygon-sampled concave wall a cut lands
    on a miter vertex (see PROJECT_MEMORY), skewing the face slightly; the
    FACE is the physical boundary."""
    L = cum[-1]
    skew = 0.0
    for p in paths:
        if p.role != 'cap':
            continue
        for q in p.sample_points():
            s = _project_to_polyline(q, src_pts, cum, closed)[0]
            for c in (a % L, b % L):
                d = min(abs(s - c), L - abs(s - c)) if closed else abs(s - c)
                if d < 2.0:
                    skew = max(skew, d)
    return skew


def _assert_no_lattice_in_gap(paths, src_pts, cum, closed, a, b):
    """Dense sampling: no lattice point lies between the opening's faces."""
    L = cum[-1]
    tol = 1e-4 + _face_skew(paths, src_pts, cum, closed, a, b)
    lattice = [p for p in paths if p.role == 'lattice']
    assert lattice, 'expected surviving lattice'
    for p in lattice:
        pts = p.sample_points()
        for u, v in zip(pts, pts[1:]):
            for k in range(41):
                q = u.lerp(v, k / 40)
                s = _project_to_polyline(q, src_pts, cum, closed)[0]
                assert not _in_interval(s, a, b, L, closed, tol=tol), \
                    f'lattice point {q} (s={s:.3f}) inside opening [{a}, {b}]'


# Straight wall used by several tests: y = 0, x 0→200, opening at x 100.
LINE = lambda: LinePath(Vec2(0, 0), Vec2(200, 0), id='L')
# 200 × 120 rectangle, CCW: (0,0)→(200,0)→(200,120)→(0,120); perimeter 640.
RECT = lambda: RectanglePath(0, 0, 200, 120, id='R')


# ---------------------------------------------------------------------------
# 1–3  Straight single / double / three-wall
# ---------------------------------------------------------------------------

class TestStraightWalls:

    def test_single_wall_becomes_two_strands(self):
        paths = _layer(LINE(), openings=[Opening('o', 'L', 100, 30)]
                       ).effective_paths()
        pcs = _pieces(paths, 'L')
        assert [[_xy(q) for q in p.sample_points()] for p in pcs] == \
            [[(0, 0), (85, 0)], [(115, 0), (200, 0)]]
        assert not any(p.role == 'cap' for p in paths)
        assert 'L' not in _by_id(paths)            # full wall no longer printed

    def test_single_wall_routes_two_runs_one_travel(self):
        m = _metrics(_layer(LINE(), openings=[Opening('o', 'L', 100, 30)]))
        assert m['print_runs'] == 2 and m['travel_moves'] == 1

    def test_double_wall_both_walls_cut_and_faces_capped(self):
        layer = _layer(LINE(), [_ot('a', 'L', 10)],
                       [Opening('o', 'L', 100, 30)])
        paths = layer.effective_paths()
        ids = _by_id(paths)
        assert [[_xy(q) for q in p.sample_points()]
                for p in _pieces(paths, 'a')] == \
            [[(0, 10), (85, 10)], [(115, 10), (200, 10)]]
        # Opening faces: flat caps spanning the full wall at x = 85 / 115.
        assert [_xy(q) for q in ids['L~0_ce'].sample_points()] == \
            [(85, 10), (85, 0)]
        assert [_xy(q) for q in ids['L~1_cs'].sample_points()] == \
            [(115, 10), (115, 0)]
        # Original ends keep their caps.
        assert [_xy(q) for q in ids['L~0_cs'].sample_points()] == \
            [(0, 10), (0, 0)]
        assert sum(1 for p in paths if p.role == 'cap') == 4

    def test_double_wall_two_closed_loops(self):
        layer = _layer(LINE(), [_ot('a', 'L', 10)],
                       [Opening('o', 'L', 100, 30)])
        G = _graph(layer)
        assert nx.number_connected_components(G) == 2
        assert all(d % 2 == 0 for _, d in G.degree())
        m = _metrics(layer)
        assert m['print_runs'] == 2 and m['travel_moves'] == 1

    def test_three_wall_all_walls_cut_one_cap_per_face(self):
        layer = _layer(LINE(), [_ot('a', 'L', 10), _ot('b', 'L', -10)],
                       [Opening('o', 'L', 100, 30)])
        paths = layer.effective_paths()
        for wid, y in (('L', 0), ('a', 10), ('b', -10)):
            pcs = _pieces(paths, wid)
            assert [_xy(pcs[0].sample_points()[-1]),
                    _xy(pcs[1].sample_points()[0])] == [(85, y), (115, y)]
        face = _by_id(paths)['L~0_ce'].sample_points()
        assert [_xy(q) for q in face] == [(85, 10), (85, 0), (85, -10)]
        # Each half is one continuous wall system.
        m = _metrics(layer)
        assert m['print_runs'] == 2 and m['travel_moves'] == 1


# ---------------------------------------------------------------------------
# 4–8  Every source type
# ---------------------------------------------------------------------------

class TestSourceTypes:

    def test_rectangle(self):
        paths = _layer(RECT(), openings=[Opening('o', 'R', 100, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'R')
        pts = pc.sample_points()
        assert not pc.closed
        assert _xy(pts[0]) == (115, 0) and _xy(pts[-1]) == (85, 0)
        assert _plen(pts) == pytest.approx(640 - 30, abs=1e-9)

    @pytest.mark.parametrize('path', [
        CirclePath(0, 0, 100, id='S'),
        EllipsePath(0, 0, 120, 70, rotation=0.3, id='S'),
    ])
    def test_circle_and_ellipse(self, path):
        src, cum = _src_frame(path)
        L = cum[-1]
        paths = _layer(path, openings=[Opening('o', 'S', 150, 40)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'S')
        pts = pc.sample_points()
        assert _plen(pts) == pytest.approx(L - 40, abs=1e-6)
        assert pts[0].dist(_locate_s(src, cum, 170, True)[0]) < 1e-9
        assert pts[-1].dist(_locate_s(src, cum, 130, True)[0]) < 1e-9

    def test_quadratic_bezier(self):
        b = QuadBezierPath(Vec2(0, 0), Vec2(200, 0), Vec2(100, 150), id='B')
        src, cum = _src_frame(b)
        L = cum[-1]
        paths = _layer(b, openings=[Opening('o', 'B', L / 2, 30)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'B')
        assert _plen(p0.sample_points()) == pytest.approx(L / 2 - 15, abs=1e-6)
        assert _plen(p1.sample_points()) == pytest.approx(L / 2 - 15, abs=1e-6)
        assert _xy(p0.sample_points()[0]) == (0, 0)
        assert _xy(p1.sample_points()[-1]) == (200, 0)

    def test_explicit_path(self):
        e = ExplicitPath([Vec2(0, 0), Vec2(60, 40), Vec2(130, 10),
                          Vec2(170, 90), Vec2(240, 60)], id='E')
        src, cum = _src_frame(e)
        L = cum[-1]
        paths = _layer(e, openings=[Opening('o', 'E', 140, 24)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'E')
        assert _plen(p0.sample_points()) == pytest.approx(128, abs=1e-6)
        assert _plen(p1.sample_points()) == pytest.approx(L - 152, abs=1e-6)


# ---------------------------------------------------------------------------
# 9–12  Corners and the seam
# ---------------------------------------------------------------------------

class TestCornersAndSeam:

    def test_spans_90_degree_rectangle_corner(self):
        """30 in centred 5 in before the (200,0) corner: 20 in of the bottom
        side and 10 in of the right side are removed."""
        paths = _layer(RECT(), openings=[Opening('o', 'R', 195, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'R')
        pts = pc.sample_points()
        assert _xy(pts[0]) == (200, 10) and _xy(pts[-1]) == (180, 0)
        assert (200, 0) not in {_xy(q) for q in pts}
        assert _plen(pts) == pytest.approx(610, abs=1e-9)

    def test_corner_span_double_wall_offset_follows_cross_section(self):
        layer = _layer(RECT(), [_ot('a', 'R', 10)],
                       [Opening('o', 'R', 195, 30)])
        paths = layer.effective_paths()
        (inner,) = _pieces(paths, 'a')
        ip = inner.sample_points()
        # Start face at s = 210 (right side, y = 10): inner wall at x = 190.
        assert _xy(ip[0]) == (190, 10)
        # End face at s = 180 (bottom, x = 180): inner wall at y = 10.
        assert _xy(ip[-1]) == (180, 10)
        m = _metrics(layer)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_spans_acute_polyline_corner(self):
        v = ExplicitPath([Vec2(0, 0), Vec2(100, 0), Vec2(20, 40)], id='V')
        paths = _layer(v, openings=[Opening('o', 'V', 100, 20)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'V')
        assert _xy(p0.sample_points()[-1]) == (90, 0)
        d = (Vec2(20, 40) - Vec2(100, 0)).normalized()
        expect = Vec2(100 + 10 * d.x, 0 + 10 * d.y)
        assert p1.sample_points()[0].dist(expect) < 1e-9
        # The acute vertex itself is gone.
        assert all(q.dist(Vec2(100, 0)) > 1 for p in (p0, p1)
                   for q in p.sample_points())

    def test_spans_rounded_corner(self):
        rect = RECT()
        src, cum = _src_frame(rect, 20.0)
        L = cum[-1]
        # Centre of the opening = middle of the first fillet arc (BR corner).
        arc_mid = 200 - 20 + (math.pi * 20 / 2) / 2
        paths = _layer(rect, openings=[Opening('o', 'R', arc_mid, 30)],
                       corner_radius=20.0).effective_paths()
        (pc,) = _pieces(paths, 'R')
        pts = pc.sample_points()
        assert pts[0].dist(_locate_s(src, cum, arc_mid + 15, True)[0]) < 1e-9
        assert pts[-1].dist(_locate_s(src, cum, arc_mid - 15, True)[0]) < 1e-9
        assert _plen(pts) == pytest.approx(L - 30, abs=1e-6)
        # Both cut points lie on the fillet arc / its tangent runs.
        for q in (pts[0], pts[-1]):
            assert _project_to_polyline(q, src, cum, True)[1] < 1e-9

    def test_rounded_corner_double_wall_offset_stays_parallel(self):
        layer = _layer(RECT(), [_ot('a', 'R', 10)],
                       [Opening('o', 'R', 195, 30)], corner_radius=20.0)
        paths = layer.effective_paths()
        (sp,) = _pieces(paths, 'R')
        (ip,) = _pieces(paths, 'a')
        s_pts = sp.sample_points()
        s_cum = _cum_lengths(s_pts, False)
        for q in ip.sample_points():
            d = _project_to_polyline(q, s_pts, s_cum, False)[1]
            assert d == pytest.approx(10.0, abs=0.05)
        assert _metrics(layer)['travel_moves'] == 0

    def test_wraps_across_rectangle_seam(self):
        """Centre 5 in after the seam: removes s ∈ [630, 640) ∪ [0, 20]."""
        paths = _layer(RECT(), openings=[Opening('o', 'R', 5, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'R')
        pts = pc.sample_points()
        assert _xy(pts[0]) == (20, 0) and _xy(pts[-1]) == (0, 10)
        assert (0, 0) not in {_xy(q) for q in pts}
        assert _plen(pts) == pytest.approx(610, abs=1e-9)

    def test_negative_centre_wraps_the_same(self):
        a = _layer(RECT(), openings=[Opening('o', 'R', 5, 30)]).effective_paths()
        b = _layer(RECT(), openings=[Opening('o', 'R', 5 - 640, 30)]
                   ).effective_paths()
        assert [_xy(q) for q in _pieces(a, 'R')[0].sample_points()] == \
            [_xy(q) for q in _pieces(b, 'R')[0].sample_points()]

    def test_wraps_across_circle_seam_double_wall(self):
        c = CirclePath(0, 0, 100, id='C')
        src, cum = _src_frame(c)
        L = cum[-1]
        layer = _layer(c, [_ot('a', 'C', 10)], [Opening('o', 'C', 0, 40)])
        paths = layer.effective_paths()
        (pc,) = _pieces(paths, 'C')
        pts = pc.sample_points()
        assert pts[0].dist(_locate_s(src, cum, 20, True)[0]) < 1e-9
        assert pts[-1].dist(_locate_s(src, cum, L - 20, True)[0]) < 1e-9
        assert all(q.dist(Vec2(100, 0)) > 15 for q in pts)   # seam point gone
        (ip,) = _pieces(paths, 'a')
        assert all(q.dist(Vec2(90, 0)) > 10 for q in ip.sample_points())
        G = _graph(layer)
        assert nx.number_connected_components(G) == 1
        m = _metrics(layer)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0


# ---------------------------------------------------------------------------
# 13–16  Editing: move, resize, source edits, delete
# ---------------------------------------------------------------------------

class TestEditing:

    def _cut_xs(self, opening):
        paths = _layer(LINE(), openings=[opening]).effective_paths()
        p0, p1 = _pieces(paths, 'L')
        return p0.sample_points()[-1].x, p1.sample_points()[0].x

    def test_moving_opening_slides_cut(self):
        assert self._cut_xs(Opening('o', 'L', 100, 30)) == \
            pytest.approx((85, 115))
        assert self._cut_xs(Opening('o', 'L', 140, 30)) == \
            pytest.approx((125, 155))

    def test_resize_from_end_keeps_start(self):
        # start fixed at 85; end dragged 115 → 135  ⇒ width 50, centre 110
        assert self._cut_xs(Opening('o', 'L', 110, 50)) == \
            pytest.approx((85, 135))

    def test_resize_from_start_keeps_end(self):
        # end fixed at 115; start dragged 85 → 60  ⇒ width 55, centre 87.5
        assert self._cut_xs(Opening('o', 'L', 87.5, 55)) == \
            pytest.approx((60, 115))

    def test_moving_source_carries_opening(self):
        moved = LinePath(Vec2(50, 20), Vec2(250, 20), id='L')
        paths = _layer(moved, openings=[Opening('o', 'L', 100, 30)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'L')
        assert _xy(p0.sample_points()[-1]) == (135, 20)
        assert _xy(p1.sample_points()[0]) == (165, 20)

    def test_reshaping_source_keeps_path_relative_position(self):
        """Rotate the wall: the opening stays 100 in along it."""
        rot = LinePath(Vec2(0, 0), Vec2(0, 200), id='L')
        paths = _layer(rot, openings=[Opening('o', 'L', 100, 30)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'L')
        assert _xy(p0.sample_points()[-1]) == (0, 85)
        assert _xy(p1.sample_points()[0]) == (0, 115)

    def test_resizing_closed_source_keeps_distance_from_start(self):
        wide = RectanglePath(0, 0, 300, 120, id='R')
        paths = _layer(wide, openings=[Opening('o', 'R', 100, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'R')
        assert _xy(pc.sample_points()[-1]) == (85, 0)
        assert _xy(pc.sample_points()[0]) == (115, 0)

    def test_opening_beyond_shortened_open_path_is_clamped(self):
        short = LinePath(Vec2(0, 0), Vec2(90, 0), id='L')
        paths = _layer(short, openings=[Opening('o', 'L', 100, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'L')
        assert [_xy(q) for q in pc.sample_points()] == [(0, 0), (75, 0)]

    def test_deleting_opening_restores_original_geometry(self):
        def snap(layer):
            return sorted((p.id, p.role, tuple(_xy(q) for q in
                                               p.sample_points()))
                          for p in layer.effective_paths())
        base = _layer(RECT(), [_ot('a', 'R', 10)], corner_radius=8)
        cut = _layer(RECT(), [_ot('a', 'R', 10)],
                     [Opening('o', 'R', 100, 30)], corner_radius=8)
        assert snap(cut) != snap(base)
        cut.openings.clear()
        assert snap(cut) == snap(base)

    def test_opening_on_missing_or_hidden_path_is_ignored(self):
        def snap(layer):
            return [(p.id, tuple(_xy(q) for q in p.sample_points()))
                    for p in layer.effective_paths()]
        base = snap(_layer(LINE()))
        assert snap(_layer(LINE(), openings=[Opening('o', 'nope', 100, 30)])) \
            == base
        hidden = LINE()
        hidden.visible = False
        assert _layer(hidden, openings=[Opening('o', 'L', 100, 30)]
                      ).effective_paths() == []


# ---------------------------------------------------------------------------
# 17–18  Routing consequences
# ---------------------------------------------------------------------------

class TestRoutingTopology:

    def test_one_opening_closed_double_wall_is_one_continuous_loop(self):
        """outer → face cap → inner (reversed) → face cap: an Eulerian circuit."""
        layer = _layer(RECT(), [_ot('a', 'R', 10)],
                       [Opening('o', 'R', 100, 30)])
        G = _graph(layer)
        assert nx.number_connected_components(G) == 1
        assert all(d % 2 == 0 for _, d in G.degree())
        m = _metrics(layer)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_two_openings_split_into_two_assemblies(self):
        layer = _layer(RECT(), [_ot('a', 'R', 10)],
                       [Opening('o1', 'R', 100, 30),
                        Opening('o2', 'R', 420, 30)])
        G = _graph(layer)
        assert nx.number_connected_components(G) == 2
        m = _metrics(layer)
        assert m['print_runs'] == 2 and m['travel_moves'] == 1

    def test_three_openings_three_assemblies(self):
        layer = _layer(RECT(), [_ot('a', 'R', 10)],
                       [Opening('o1', 'R', 100, 30), Opening('o2', 'R', 300, 30),
                        Opening('o3', 'R', 500, 30)])
        assert nx.number_connected_components(_graph(layer)) == 3
        m = _metrics(layer)
        assert m['print_runs'] == 3 and m['travel_moves'] == 2

    def test_overlapping_openings_merge(self):
        paths = _layer(RECT(), openings=[Opening('o1', 'R', 100, 30),
                                         Opening('o2', 'R', 120, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'R')
        assert _xy(pc.sample_points()[0]) == (135, 0)
        assert _xy(pc.sample_points()[-1]) == (85, 0)

    def test_opening_wider_than_loop_removes_wall_assembly(self):
        paths = _layer(RECT(), [_ot('a', 'R', 10)],
                       [Opening('o', 'R', 0, 700)]).effective_paths()
        assert not [p for p in paths if p.role != 'lattice']

    def test_opening_covering_open_path_end_moves_the_end(self):
        layer = _layer(LINE(), [_ot('a', 'L', 10)], [Opening('o', 'L', 5, 30)])
        paths = layer.effective_paths()
        (pc,) = _pieces(paths, 'L')
        assert _xy(pc.sample_points()[0]) == (20, 0)
        caps = [p for p in paths if p.role == 'cap']
        assert len(caps) == 2                  # start face at 20 + orig end
        assert _metrics(layer)['travel_moves'] == 0


# ---------------------------------------------------------------------------
# 19–21  Lattice around openings
# ---------------------------------------------------------------------------

def _ring(gen, params, opening_s=100, width=40, cap_style='flat', r=0.0):
    c = CirclePath(0, 0, 100, id='C')
    return c, _layer(c, [_ot('a', 'C', 20)],
                     [Opening('o', 'C', opening_s, width)],
                     [LatticeInstance('li', gen, 'C', 'a', params)],
                     cap_style=cap_style, cap_corner_radius=r)


ZIG = ('zigzag', {'segments': 12, 'connect_ends': 1})
WAVE = ('wave', {'cycles': 4})


class TestLatticeAroundOpenings:

    @pytest.mark.parametrize('gen', [ZIG, WAVE], ids=['zigzag', 'wave'])
    @pytest.mark.parametrize('centre', [100, 0], ids=['mid', 'seam'])
    def test_no_lattice_inside_opening(self, gen, centre):
        c, layer = _ring(*gen, opening_s=centre)
        src, cum = _src_frame(c)
        a = (centre - 20) % cum[-1]
        _assert_no_lattice_in_gap(layer.effective_paths(), src, cum, True,
                                  a, a + 40)

    @pytest.mark.parametrize('gen', [ZIG, WAVE], ids=['zigzag', 'wave'])
    def test_lattice_does_not_bridge_the_opening(self, gen):
        """No lattice segment crosses either cut face."""
        _, layer = _ring(*gen)
        paths = layer.effective_paths()
        faces = [p.sample_points() for p in paths
                 if p.role == 'cap' and p.label in ('cap_start', 'cap_end')]
        for p in paths:
            if p.role != 'lattice':
                continue
            pts = p.sample_points()
            for u, v in zip(pts, pts[1:]):
                for f in faces:
                    for c0, c1 in zip(f, f[1:]):
                        assert not _segments_intersect(u, v, c0, c1)

    def test_narrow_opening_removes_spanning_lattice_segment(self):
        """A zigzag diagonal whose ends are both outside a narrow opening
        but passes through it must still be cut."""
        c, layer = _ring('zigzag', {'segments': 6, 'connect_ends': 0},
                         opening_s=60, width=8)
        src, cum = _src_frame(c)
        _assert_no_lattice_in_gap(layer.effective_paths(), src, cum, True,
                                  56, 64)

    @pytest.mark.parametrize('gen', [ZIG, WAVE], ids=['zigzag', 'wave'])
    def test_lattice_terminates_on_the_opening_caps(self, gen):
        _, layer = _ring(*gen)
        paths = layer.effective_paths()
        cap_vertices = {_xy(q, 9) for p in paths if p.role == 'cap'
                        for q in p.sample_points()}
        wall_vertices = {_xy(q, 9) for p in paths
                         if p.role not in ('cap', 'lattice')
                         for q in p.sample_points()}
        face_ends = 0
        for p in paths:
            if p.role != 'lattice' or '~' not in p.id:
                continue
            for q in (p.sample_points()[0], p.sample_points()[-1]):
                if _xy(q, 9) in cap_vertices and _xy(q, 9) not in wall_vertices:
                    face_ends += 1
        assert face_ends == 2      # one landing on each opening face
        # Landed lattice and walls form ONE connected assembly.
        assert nx.number_connected_components(_graph(layer)) == 1

    def test_lattice_landing_with_rounded_caps_has_extension(self):
        _, layer = _ring(*ZIG, cap_style='rounded_corners', r=6.0)
        paths = layer.effective_paths()
        exts = [p for p in paths if p.label.endswith('_ext')]
        assert len(exts) == 2
        lattice_ends = {_xy(q, 9) for p in paths if p.role == 'lattice'
                        for q in (p.sample_points()[0], p.sample_points()[-1])}
        for e in exts:
            ep = e.sample_points()
            assert _xy(ep[0], 9) in lattice_ends
            assert e.sample_points()[0].dist(ep[1]) > 1e-6
        assert nx.number_connected_components(_graph(layer)) == 1

    def test_lattice_without_openings_unchanged(self):
        c = CirclePath(0, 0, 100, id='C')
        layer = _layer(c, [_ot('a', 'C', 20)],
                       lattices=[LatticeInstance('li', *ZIG[:1], 'C', 'a',
                                                 ZIG[1])])
        lat = [p for p in layer.effective_paths() if p.role == 'lattice']
        assert all('~' not in p.id for p in lat)
        assert _metrics(layer)['travel_moves'] == 0


# ---------------------------------------------------------------------------
# 22–25  End treatment at openings (inherits the layer cap style)
# ---------------------------------------------------------------------------

class TestOpeningEndTreatment:
    """3-wall straight system (y = +10, 0, −10; W = 20), clear opening 30
    centred at x = 100. Width is the CLEAR opening: the cut face moves out
    by the cap reach so the finished end stops at x = 85 / 115."""

    def _paths(self, style, r=0.0):
        return _layer(LINE(), [_ot('a', 'L', 10), _ot('b', 'L', -10)],
                      [Opening('o', 'L', 100, 30)],
                      cap_style=style, cap_corner_radius=r).effective_paths()

    def _face(self, paths, which='left'):
        pid = 'L~0_ce' if which == 'left' else 'L~1_cs'
        return _by_id(paths)[pid].sample_points()

    def test_flat(self):
        paths = self._paths('flat')
        assert [_xy(q) for q in self._face(paths)] == \
            [(85, 10), (85, 0), (85, -10)]
        assert [_xy(q) for q in self._face(paths, 'right')] == \
            [(115, 10), (115, 0), (115, -10)]

    def test_rounded_corners(self):
        paths = self._paths('rounded_corners', 6.0)
        face = self._face(paths)
        # Cut face at x = 79 (85 − 6); fillets centred (79, ±4), radius 6;
        # end face at x = 85 = the clear-opening line.
        assert _xy(face[0]) == (79, 10) and _xy(face[-1]) == (79, -10)
        assert max(q.x for q in face) == pytest.approx(85, abs=1e-9)
        for q in face:
            if q.y > 4 + 1e-9:
                assert q.dist(Vec2(79, 4)) == pytest.approx(6, abs=1e-6)
            elif q.y < -4 - 1e-9:
                assert q.dist(Vec2(79, -4)) == pytest.approx(6, abs=1e-6)
            else:
                assert q.x == pytest.approx(85, abs=1e-9)
        # Centre wall extends onto the face.
        exts = [p for p in paths if p.id.startswith('L~0_ce_x')]
        assert [[_xy(q) for q in e.sample_points()] for e in exts] == \
            [[(79, 0), (85, 0)]]

    def test_full_round(self):
        paths = self._paths('full_round')
        face = self._face(paths)
        assert _xy(face[0]) == (75, 10) and _xy(face[-1]) == (75, -10)
        for q in face:
            assert q.dist(Vec2(75, 0)) == pytest.approx(10, abs=1e-6)
        assert max(q.x for q in face) == pytest.approx(85, abs=1e-9)
        right = self._face(paths, 'right')
        assert min(q.x for q in right) == pytest.approx(115, abs=1e-9)

    @pytest.mark.parametrize('r', [2.0, 6.0])
    def test_end_r_moves_cut_but_keeps_clear_width(self, r):
        paths = self._paths('rounded_corners', r)
        left, right = self._face(paths), self._face(paths, 'right')
        assert _xy(left[0]) == (85 - r, 10)
        assert min(q.x for q in right) - max(q.x for q in left) == \
            pytest.approx(30, abs=1e-9)

    def test_end_r_clamped_matches_full_round(self):
        a = self._paths('rounded_corners', 50.0)
        b = self._paths('full_round')
        assert [_xy(q) for q in self._face(a)] == \
            [_xy(q) for q in self._face(b)]

    @pytest.mark.parametrize('style,r', [('flat', 0), ('rounded_corners', 6),
                                         ('full_round', 0)])
    def test_each_half_routes_continuously(self, style, r):
        layer = _layer(LINE(), [_ot('a', 'L', 10), _ot('b', 'L', -10)],
                       [Opening('o', 'L', 100, 30)],
                       cap_style=style, cap_corner_radius=r)
        m = _metrics(layer)
        assert m['print_runs'] == 2 and m['travel_moves'] == 1

    def test_full_round_narrow_opening_caps_never_cross(self):
        """Clear width 4 in on a 20 in wall: Full Round caps must not
        overlap (each reaches 10 in past its cut face)."""
        paths = _layer(LINE(), [_ot('a', 'L', 10), _ot('b', 'L', -10)],
                       [Opening('o', 'L', 100, 4)], cap_style='full_round'
                       ).effective_paths()
        left, right = self._face(paths), self._face(paths, 'right')
        assert max(q.x for q in left) == pytest.approx(98, abs=1e-9)
        assert min(q.x for q in right) == pytest.approx(102, abs=1e-9)
        for u, v in zip(left, left[1:]):
            for c0, c1 in zip(right, right[1:]):
                assert not _segments_intersect(u, v, c0, c1)

    def test_single_wall_opening_has_no_cap_reach(self):
        paths = _layer(LINE(), openings=[Opening('o', 'L', 100, 30)],
                       cap_style='full_round').effective_paths()
        p0, p1 = _pieces(paths, 'L')
        assert p0.sample_points()[-1].x == pytest.approx(85)
        assert p1.sample_points()[0].x == pytest.approx(115)


# ---------------------------------------------------------------------------
# 26  Dimensions — JS arc-length helpers agree with the backend
# ---------------------------------------------------------------------------

_JS = os.path.join(os.path.dirname(__file__), '..', 'static', 'app.js')


def _run_js(snippet: str):
    src = open(_JS).read()
    start = src.index('// Openings — path-relative gaps')
    end = src.index('// Tool management')
    section = src[src.rindex('\n// ----', 0, start):src.rindex('\n// ----', 0, end)]
    prelude = """
      const HIT_DIST = 10;
      function worldToCanvas(x, y) { return [x, y]; }
      function distToSeg() { return 1e9; }
      let _idCounter = 1;
    """
    proc = subprocess.run(['node', '-e', prelude + section + snippet],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
class TestJsOpeningDimensions:

    def _geom(self, style, r, offsets):
        return _run_js(f"""
          const layer = {{
            source_paths: [{{ id: 'L', type: 'LinePath', closed: false,
                              visible: true, points: [[0, 0], [200, 0]] }}],
            offset_treatments: {json.dumps(offsets)},
            openings: [{{ id: 'o', source_path_id: 'L', center_s: 100,
                          width: 36 }}],
            cap_style: '{style}', cap_corner_radius: {r},
          }};
          const g = _openingGeom(layer.openings[0]);
          console.log(JSON.stringify({{ width: g.width, a: g.a, b: g.b,
                                        label: _fmtIn(g.width),
                                        pieces: _survivingPieces(layer.source_paths[0]) }}));
        """)

    def test_width_label_single_wall(self):
        g = self._geom('flat', 0, [])
        assert g['label'] == '36 in'
        assert g['pieces'] == [[[0, 0], [82, 0]], [[118, 0], [200, 0]]]

    def test_width_label_is_clear_width_with_full_round(self):
        offs = [{'id': 'a', 'source_path_id': 'L', 'direction': 'left', 'distance': 10},
                {'id': 'b', 'source_path_id': 'L', 'direction': 'right', 'distance': 10}]
        g = self._geom('full_round', 0, offs)
        assert g['label'] == '36 in'
        assert (g['a'], g['b']) == (72, 128)   # cut faces = backend's
        paths = _layer(LINE(), [_ot('a', 'L', 10), _ot('b', 'L', -10)],
                       [Opening('o', 'L', 100, 36)], cap_style='full_round'
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'L')
        assert p0.sample_points()[-1].x == pytest.approx(g['a'])
        assert p1.sample_points()[0].x == pytest.approx(g['b'])

    def test_closed_seam_pieces_match_backend(self):
        g = _run_js("""
          const layer = {
            source_paths: [{ id: 'R', type: 'RectanglePath', closed: true, visible: true,
                             points: [[0,0],[200,0],[200,120],[0,120]] }],
            offset_treatments: [], cap_style: 'flat', cap_corner_radius: 0,
            openings: [{ id: 'o', source_path_id: 'R', center_s: 5, width: 30 }],
          };
          console.log(JSON.stringify(_survivingPieces(layer.source_paths[0])));
        """)
        paths = _layer(RECT(), openings=[Opening('o', 'R', 5, 30)]
                       ).effective_paths()
        (pc,) = _pieces(paths, 'R')
        assert [[round(x, 6), round(y, 6)] for x, y in g[0]] == \
            [list(_xy(q)) for q in pc.sample_points()]


# ---------------------------------------------------------------------------
# 27–28  Serialisation and backwards compatibility
# ---------------------------------------------------------------------------

class TestSerialisation:

    def _client(self):
        design_app.app.config['TESTING'] = True
        return design_app.app.test_client()

    def _payload(self, **extra):
        p = {
            'id': 'x', 'label': '',
            'source_paths': [{'id': 'L', 'type': 'LinePath', 'label': 'L',
                              'closed': False, 'role': 'free',
                              'visible': True, 'start': [0, 0],
                              'end': [200, 0]}],
            'offset_treatments': [{'id': 'a', 'source_path_id': 'L',
                                   'distance': 10, 'role': 'inner',
                                   'label': ''}],
            'lattice_instances': [],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False,
                            'component_order': None},
        }
        p.update(extra)
        return p

    def test_api_applies_openings(self):
        op = {'id': 'o', 'source_path_id': 'L', 'center_s': 100, 'width': 30,
              'end_treatment': 'inherit'}
        r = self._client().post('/api/effective_paths',
                                data=json.dumps(self._payload(openings=[op])),
                                content_type='application/json')
        assert r.status_code == 200
        ids = {p['id']: p for p in r.get_json()['paths']}
        assert ids['L~0']['points'] == [[0, 0], [85, 0]]
        assert ids['L~0']['treatment_id'] == 'opening_cut'
        assert 'L~0_ce' in ids and 'L~1_cs' in ids

    def test_api_route_with_openings(self):
        op = {'id': 'o', 'source_path_id': 'L', 'center_s': 100, 'width': 30}
        r = self._client().post('/api/route',
                                data=json.dumps(self._payload(openings=[op])),
                                content_type='application/json')
        assert r.status_code == 200
        m = r.get_json()['metrics']
        assert m['print_runs'] == 2 and m['travel_moves'] == 1

    def test_round_trip_preserves_opening_fields(self):
        _deserialise_layer = design_app._deserialise_layer
        op = {'id': 'o', 'source_path_id': 'L', 'center_s': 84.5,
              'width': 36, 'end_treatment': 'inherit',
              'z_min': 0, 'z_max': 84, 'label': 'door'}
        layer = _deserialise_layer(self._payload(openings=[op]))
        d = layer.to_dict()['openings'][0]
        assert d == {**op, 'type': 'opening'}
        again = _deserialise_layer({**self._payload(), 'openings': [d]})
        assert again.to_dict()['openings'] == [d]

    def test_payload_without_openings_still_works(self):
        r = self._client().post('/api/effective_paths',
                                data=json.dumps(self._payload()),
                                content_type='application/json')
        assert r.status_code == 200
        ids = {p['id'] for p in r.get_json()['paths']}
        assert {'L', 'a', 'L_cs', 'L_ce'} <= ids
        assert not any('~' in i for i in ids)

    def test_null_openings_field_is_tolerated(self):
        r = self._client().post('/api/effective_paths',
                                data=json.dumps(self._payload(openings=None)),
                                content_type='application/json')
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Edge cases found during implementation
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_offset_cut_corresponds_by_projection(self):
        """Each wall endpoint at a cut projects back onto the source at the
        cut position. Exception, inherent to polyline offsets: on the
        concave side a miter vertex covers a short arc-length range of the
        source (no offset point projects strictly inside it); a cut there
        lands on that miter vertex."""
        c = CirclePath(0, 0, 100, id='C')
        src, cum = _src_frame(c)
        layer = _layer(c, [_ot('a', 'C', 20), _ot('b', 'C', -15)],
                       [Opening('o', 'C', 60, 8)])
        paths = layer.effective_paths()
        full = {d.id: {_xy(q, 9) for q in d.sample_points()} for d in
                _layer(c, [_ot('a', 'C', 20), _ot('b', 'C', -15)]
                       ).effective_paths()}
        for wid in ('a', 'b'):
            (wp,) = _pieces(paths, wid)
            for q, s_cut in ((wp.sample_points()[0], 64),
                             (wp.sample_points()[-1], 56)):
                s = _project_to_polyline(q, src, cum, True)[0]
                if abs(s - s_cut) > 1e-6:
                    assert _xy(q, 9) in full[wid], (wid, s, s_cut)
                    assert abs(s - s_cut) < 1.0
        # Convex side (outer offset): always exact.
        (wp,) = _pieces(paths, 'b')
        for q, s_cut in ((wp.sample_points()[0], 64),
                         (wp.sample_points()[-1], 56)):
            assert _project_to_polyline(q, src, cum, True)[0] == \
                pytest.approx(s_cut, abs=1e-6)

    def test_lattice_lands_on_both_faces_with_every_cap_style(self):
        for style, r in (('flat', 0), ('rounded_corners', 6), ('full_round', 0)):
            _, layer = _ring(*ZIG, cap_style=style, r=r)
            paths = layer.effective_paths()
            lattice_ends = {_xy(q, 9) for p in paths if p.role == 'lattice'
                            for q in (p.sample_points()[0],
                                      p.sample_points()[-1])}
            for cid in ('C~0_cs', 'C~0_ce'):
                touched = {_xy(q, 9) for p in paths
                           if p.id == cid or p.id.startswith(cid + '_x')
                           for q in p.sample_points()}
                assert lattice_ends & touched, (style, cid)
            assert nx.number_connected_components(_graph(layer)) == 1

    def test_lattice_between_independent_walls_is_clipped(self):
        """Opening in wall A of a lattice spanning to a separate wall B:
        the lattice is cut along A's cross-section through the opening."""
        a = LinePath(Vec2(0, 0), Vec2(200, 0), id='A')
        b = LinePath(Vec2(0, 30), Vec2(200, 30), id='B')
        layer = _layer([a, b], openings=[Opening('o', 'A', 100, 30)],
                       lattices=[LatticeInstance('li', 'zigzag', 'A', 'B',
                                                 {'segments': 10,
                                                  'connect_ends': 0})])
        paths = layer.effective_paths()
        for p in paths:
            if p.role == 'lattice':
                for u, v in zip(p.sample_points(), p.sample_points()[1:]):
                    for k in range(41):
                        q = u.lerp(v, k / 40)
                        assert not (85 + 1e-6 < q.x < 115 - 1e-6), q
        assert _pieces(paths, 'B') == []            # B itself is not cut

    def test_double_wall_ellipse_one_opening_continuous(self):
        e = EllipsePath(0, 0, 150, 90, id='E')
        layer = _layer(e, [_ot('a', 'E', 12)], [Opening('o', 'E', 200, 36)],
                       cap_style='full_round')
        m = _metrics(layer)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_rounded_explicit_wall_with_offsets_and_corner_opening(self):
        pts = [Vec2(0, 0), Vec2(160, 0), Vec2(160, 60), Vec2(240, 60),
               Vec2(240, 140), Vec2(0, 140)]
        e = ExplicitPath(pts, id='E', closed=True)
        layer = _layer(e, [_ot('i', 'E', 10), _ot('o', 'E', -10)],
                       [Opening('op', 'E', 160 + 60, 30)],   # at a convex corner
                       corner_radius=8, cap_style='rounded_corners',
                       cap_corner_radius=4)
        paths = layer.effective_paths()
        (sp,) = _pieces(paths, 'E')
        s_pts = sp.sample_points()
        s_cum = _cum_lengths(s_pts, False)
        for wid in ('i', 'o'):
            (wp,) = _pieces(paths, wid)
            for q in wp.sample_points():
                d = _project_to_polyline(q, s_pts, s_cum, False)[1]
                assert d >= 10 - 0.05
        m = _metrics(layer)
        assert m['travel_moves'] == 0 and m['print_runs'] == 1


# ---------------------------------------------------------------------------
# UI interaction smoke test (JS, run under node with a DOM stub)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_opening_interactions_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js',
                          'ui_openings_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI SMOKE PASSED' in proc.stdout


# ---------------------------------------------------------------------------
# Routing over opening geometry — the reported case and general invariants
# ---------------------------------------------------------------------------

import itertools


def _min_open_trail_retrace(layer):
    """Exhaustive optimum: least retrace length for ONE continuous trail of
    a connected component (pair all odd nodes but two, via shortest
    in-graph paths). Independent of the router's implementation."""
    G = _graph(layer)
    odd = [n for n, d in G.degree() if d % 2]
    D = dict(nx.all_pairs_dijkstra_path_length(G, weight='length'))

    def match(nodes):
        if not nodes:
            return 0.0
        f, rest = nodes[0], nodes[1:]
        return min(D[f][o] + match(rest[:i] + rest[i + 1:])
                   for i, o in enumerate(rest))
    if len(odd) <= 2:
        return 0.0
    return min(match([o for o in odd if o not in pair])
               for pair in itertools.combinations(odd, 2))


def _removed_with_reach(layer, sid):
    """Removed intervals exactly as PrintLayer computes them (incl. the
    cap reach that makes width the clear opening)."""
    from model import _opening_removed_intervals
    src = next(p for p in layer.source_paths if p.id == sid)
    pts, cum = _src_frame(src, layer.corner_radius)
    offs = [(ot, ot.generate(pts, src.closed)) for ot in layer.offset_treatments
            if ot.source_path_id == sid]
    removed = _opening_removed_intervals(
        [o for o in layer.openings if o.source_path_id == sid], cum[-1],
        src.closed, layer._opening_cap_reach(offs))
    return src, pts, cum, removed


def _assert_moves_avoid_openings(layer, moves, src_id):
    """No printing/retracing move passes between an opening's cut faces."""
    src, pts, cum, removed = _removed_with_reach(layer, src_id)
    paths = layer.effective_paths()
    L = cum[-1]
    for a, b in removed:
        tol = 1e-3 + _face_skew(paths, pts, cum, src.closed, a, b)
        for m in moves:
            if m.kind == 'travel':
                continue
            for k in range(1, 20):
                q = Vec2(m.start.x, m.start.y).lerp(Vec2(m.end.x, m.end.y),
                                                     k / 20)
                s = _project_to_polyline(q, pts, cum, src.closed)[0]
                assert not _in_interval(s, a, b, L, src.closed, tol), \
                    f'{m.kind} move through opening at {q}'


def _reported_case(cap='flat', r=0.0, lattice=True, openings=None):
    """UI defaults: 120 in rect, 10 in inside offset, zigzag (6 segs,
    connect ends), one 12 in opening on the bottom side."""
    R = RectanglePath(140, 140, 120, 120, id='p1')
    lat = ([LatticeInstance('li', 'zigzag', 'p1', 'p2',
                            {'segments': 6, 'connect_ends': 1})]
           if lattice else [])
    return _layer(R, [_ot('p2', 'p1', 10)],
                  openings or [Opening('o', 'p1', 60, 12)], lat,
                  cap_style=cap, cap_corner_radius=r)


class TestReportedRoutingCase:
    """
    Reported: Requires backtracking, 3 runs, 2 travels. Diagnosis: ONE
    connected component with 4 odd-degree nodes (zigzag start corner,
    inner corner on the end connector, the two lattice landings on the
    opening faces). Euler: no trail covers every edge exactly once (> 2 odd
    nodes), so some re-traversal is unavoidable — but zero TRAVEL is
    possible because the graph is connected. The old router paired all
    odd nodes with straight travel jumps (one of them across the opening).
    """

    @pytest.mark.parametrize('cap,r', [('flat', 0), ('rounded_corners', 3),
                                       ('full_round', 0)])
    def test_one_component_four_odd_nodes(self, cap, r):
        G = _graph(_reported_case(cap, r))
        assert nx.number_connected_components(G) == 1
        assert sum(1 for _, d in G.degree() if d % 2) == 4

    @pytest.mark.parametrize('cap,r', [('flat', 0), ('rounded_corners', 3),
                                       ('full_round', 0)])
    def test_one_continuous_run_zero_travel(self, cap, r):
        m = _metrics(_reported_case(cap, r))
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_flat_retrace_is_the_proven_minimum(self):
        layer = _reported_case()
        m = _metrics(layer)
        # The only unavoidable repeat: the 10√2 in zigzag end connector.
        assert m['retrace_distance'] == pytest.approx(10 * math.sqrt(2),
                                                      abs=0.01)
        assert m['retrace_distance'] == pytest.approx(
            _min_open_trail_retrace(layer), abs=0.01)

    @pytest.mark.parametrize('cap,r', [('rounded_corners', 3),
                                       ('full_round', 0)])
    def test_retrace_is_minimal_for_other_caps(self, cap, r):
        layer = _reported_case(cap, r)
        assert _metrics(layer)['retrace_distance'] == pytest.approx(
            _min_open_trail_retrace(layer), abs=0.01)

    def test_no_move_crosses_the_opening(self):
        layer = _reported_case()
        _assert_moves_avoid_openings(
            layer, route_layer(layer.to_routing_layer()), 'p1')

    def test_one_opening_without_lattice_is_a_clean_loop(self):
        layer = _reported_case(lattice=False)
        G = _graph(layer)
        assert all(d % 2 == 0 for _, d in G.degree())
        m = _metrics(layer)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0
        assert m['retrace_distance'] == 0

    def test_two_openings_genuinely_split_and_travel_once(self):
        layer = _reported_case(openings=[Opening('o1', 'p1', 60, 12),
                                         Opening('o2', 'p1', 300, 12)])
        G = _graph(layer)
        assert nx.number_connected_components(G) == 2
        moves = route_layer(layer.to_routing_layer())
        m = compute_metrics(moves)
        assert m['print_runs'] == 2 and m['travel_moves'] == 1
        _assert_moves_avoid_openings(layer, moves, 'p1')


# ---------------------------------------------------------------------------
# Multiple openings per source path
# ---------------------------------------------------------------------------

def _removed(layer, sid):
    from model import _opening_removed_intervals
    src = next(p for p in layer.source_paths if p.id == sid)
    pts, cum = _src_frame(src, layer.corner_radius)
    return _opening_removed_intervals(
        [o for o in layer.openings if o.source_path_id == sid],
        cum[-1], src.closed), cum[-1]


class TestMultipleOpenings:

    def test_two_openings_straight_wall(self):
        paths = _layer(LINE(), openings=[Opening('a', 'L', 50, 20),
                                         Opening('b', 'L', 150, 20)]
                       ).effective_paths()
        assert [[_xy(q) for q in p.sample_points()]
                for p in _pieces(paths, 'L')] == \
            [[(0, 0), (40, 0)], [(60, 0), (140, 0)], [(160, 0), (200, 0)]]

    def test_three_openings_straight_double_wall(self):
        layer = _layer(LINE(), [_ot('i', 'L', 10)],
                       [Opening('a', 'L', 40, 10), Opening('b', 'L', 100, 10),
                        Opening('c', 'L', 160, 10)])
        paths = layer.effective_paths()
        assert len(_pieces(paths, 'L')) == 4 and len(_pieces(paths, 'i')) == 4
        assert sum(1 for p in paths if p.role == 'cap') == 8
        assert nx.number_connected_components(_graph(layer)) == 4
        m = _metrics(layer)
        assert m['print_runs'] == 4 and m['travel_moves'] == 3

    def test_two_openings_rectangle_different_sides(self):
        paths = _layer(RECT(), openings=[Opening('door', 'R', 100, 36),
                                         Opening('win', 'R', 260, 24)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'R')
        assert _xy(p0.sample_points()[0]) == (118, 0)
        assert _xy(p0.sample_points()[-1]) == (200, 48)
        assert _xy(p1.sample_points()[0]) == (200, 72)
        assert _xy(p1.sample_points()[-1]) == (82, 0)

    @pytest.mark.parametrize('path', [
        CirclePath(0, 0, 100, id='S'),
        EllipsePath(0, 0, 140, 80, rotation=0.4, id='S'),
    ])
    def test_several_openings_closed_curves(self, path):
        _, cum = _src_frame(path)
        L = cum[-1]
        ops = [Opening(f'o{k}', 'S', L * k / 4 + 10, 20) for k in range(4)]
        layer = _layer(path, [_ot('i', 'S', 12)], ops)
        paths = layer.effective_paths()
        pcs = _pieces(paths, 'S')
        assert len(pcs) == 4
        assert sum(_plen(p.sample_points()) for p in pcs) == \
            pytest.approx(L - 80, abs=1e-6)
        assert len(_pieces(paths, 'i')) == 4
        assert nx.number_connected_components(_graph(layer)) == 4

    def test_multiple_openings_explicit_path(self):
        e = ExplicitPath([Vec2(0, 0), Vec2(80, 30), Vec2(160, 0),
                          Vec2(240, 40), Vec2(320, 0)], id='E')
        _, cum = _src_frame(e)
        ops = [Opening('a', 'E', 40, 12), Opening('b', 'E', cum[2], 16),
               Opening('c', 'E', cum[-1] - 50, 12)]
        paths = _layer(e, openings=ops).effective_paths()
        pcs = _pieces(paths, 'E')
        assert len(pcs) == 4
        assert sum(_plen(p.sample_points()) for p in pcs) == \
            pytest.approx(cum[-1] - 40, abs=1e-6)

    def test_corner_span_plus_another_opening(self):
        paths = _layer(RECT(), openings=[Opening('c', 'R', 195, 30),
                                         Opening('d', 'R', 400, 20)]
                       ).effective_paths()
        p0, p1 = _pieces(paths, 'R')
        assert _xy(p0.sample_points()[0]) == (200, 10)
        assert _xy(p0.sample_points()[-1]) == (130, 120)   # s = 390
        assert _xy(p1.sample_points()[0]) == (110, 120)    # s = 410
        assert _xy(p1.sample_points()[-1]) == (180, 0)

    def test_seam_wrap_plus_another_opening(self):
        paths = _layer(RECT(), openings=[Opening('s', 'R', 0, 20),
                                         Opening('t', 'R', 100, 20)]
                       ).effective_paths()
        # Pieces follow removed-interval order: [90,110] then [630,650).
        p0, p1 = _pieces(paths, 'R')
        assert [_xy(p0.sample_points()[0]), _xy(p0.sample_points()[-1])] == \
            [(110, 0), (0, 10)]
        assert [_xy(p1.sample_points()[0]), _xy(p1.sample_points()[-1])] == \
            [(10, 0), (90, 0)]

    def test_moving_one_opening_leaves_the_other(self):
        def cuts(ops):
            ps = _pieces(_layer(LINE(), openings=ops).effective_paths(), 'L')
            return [(_xy(p.sample_points()[0]), _xy(p.sample_points()[-1]))
                    for p in ps]
        before = cuts([Opening('a', 'L', 50, 20), Opening('b', 'L', 150, 20)])
        after = cuts([Opening('a', 'L', 70, 20), Opening('b', 'L', 150, 20)])
        assert before[2] == after[2] and before[0] != after[0]
        resized = cuts([Opening('a', 'L', 50, 20), Opening('b', 'L', 155, 30)])
        assert resized[0] == before[0]
        assert resized[1][1] == (140, 0) and resized[2][0] == (170, 0)

    def test_deleting_one_opening_keeps_the_others(self):
        three = [Opening('a', 'R', 100, 20), Opening('b', 'R', 300, 20),
                 Opening('c', 'R', 500, 20)]
        layer = _layer(RECT(), [_ot('i', 'R', 10)], three)
        layer.openings = [o for o in layer.openings if o.id != 'b']
        ref = _layer(RECT(), [_ot('i', 'R', 10)], [three[0], three[2]])
        snap = lambda l: sorted((p.id, tuple(_xy(q) for q in p.sample_points()))
                                for p in l.effective_paths())
        assert snap(layer) == snap(ref)
        assert nx.number_connected_components(_graph(layer)) == 2

    def test_overlapping_openings_union(self):
        """20–50 ∪ 40–70 → one removed interval 20–70, caps only outside."""
        layer = _layer(LINE(), [_ot('i', 'L', 10)],
                       [Opening('a', 'L', 35, 30), Opening('b', 'L', 55, 30)])
        assert _removed(layer, 'L')[0] == [(20, 70)]
        paths = layer.effective_paths()
        assert [(_xy(p.sample_points()[0]), _xy(p.sample_points()[-1]))
                for p in _pieces(paths, 'L')] == [((0, 0), (20, 0)),
                                                  ((70, 0), (200, 0))]
        face_xs = sorted(round(q.x, 6) for p in paths if p.role == 'cap'
                         for q in p.sample_points())
        assert 50 not in face_xs and 40 not in face_xs   # no internal caps
        assert sum(1 for p in paths if p.role == 'cap') == 4

    def test_contained_opening_union(self):
        layer = _layer(LINE(), openings=[Opening('a', 'L', 100, 60),
                                         Opening('b', 'L', 100, 10)])
        assert _removed(layer, 'L')[0] == [(70, 130)]

    @pytest.mark.parametrize('gap', [0.0, 1e-9, 5e-4])
    def test_touching_openings_union_no_sliver(self, gap):
        layer = _layer(LINE(), [_ot('i', 'L', 10)],
                       [Opening('a', 'L', 35, 30),
                        Opening('b', 'L', 65 + gap, 30)])
        removed, _ = _removed(layer, 'L')
        assert len(removed) == 1
        paths = layer.effective_paths()
        assert len(_pieces(paths, 'L')) == 2 and len(_pieces(paths, 'i')) == 2
        assert all(_plen(p.sample_points()) > 1 for p in paths
                   if p.role != 'cap')
        assert sum(1 for p in paths if p.role == 'cap') == 4

    def test_real_wall_between_close_openings_survives(self):
        layer = _layer(LINE(), openings=[Opening('a', 'L', 35, 30),
                                         Opening('b', 'L', 66, 30)])
        assert len(_removed(layer, 'L')[0]) == 2       # 1 in of wall remains
        assert len(_pieces(layer.effective_paths(), 'L')) == 3

    def test_overlapping_openings_across_seam(self):
        """[630, 650) and [−5, 25) on a 640 perimeter → one interval."""
        layer = _layer(RECT(), [_ot('i', 'R', 10)],
                       [Opening('a', 'R', 640, 20), Opening('b', 'R', 10, 30)])
        removed, L = _removed(layer, 'R')
        assert len(removed) == 1
        a, b = removed[0]
        assert (a % L, b - a) == pytest.approx((630, 35))
        paths = layer.effective_paths()
        (pc,) = _pieces(paths, 'R')
        assert _xy(pc.sample_points()[0]) == (25, 0)
        assert _xy(pc.sample_points()[-1]) == (0, 10)
        assert sum(1 for p in paths if p.role == 'cap') == 2
        m = _metrics(layer)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_touching_across_seam(self):
        layer = _layer(RECT(), openings=[Opening('a', 'R', 630, 20),
                                         Opening('b', 'R', 10, 20)])
        removed, _ = _removed(layer, 'R')
        assert len(removed) == 1
        (pc,) = _pieces(layer.effective_paths(), 'R')
        assert _xy(pc.sample_points()[0]) == (20, 0)

    def test_offsets_respect_all_openings(self):
        layer = _layer(RECT(), [_ot('i', 'R', 10), _ot('o', 'R', -10)],
                       [Opening('a', 'R', 100, 30), Opening('b', 'R', 380, 30)],
                       corner_radius=12)
        paths = layer.effective_paths()
        src = next(p for p in layer.source_paths)
        pts, cum = _src_frame(src, 12)
        removed, L = _removed(layer, 'R')
        for wid in ('i', 'o'):
            assert len(_pieces(paths, wid)) == 2
            for p in _pieces(paths, wid):
                for q in p.sample_points():
                    s = _project_to_polyline(q, pts, cum, True)[0]
                    for a, b in removed:
                        assert not _in_interval(s, a, b, L, True, 1e-3)

    @pytest.mark.parametrize('gen', [ZIG, WAVE], ids=['zigzag', 'wave'])
    def test_lattice_respects_all_openings(self, gen):
        c = CirclePath(0, 0, 100, id='C')
        ops = [Opening('a', 'C', 60, 30), Opening('b', 'C', 300, 30),
               Opening('c', 'C', 0, 30)]                    # one on the seam
        layer = _layer(c, [_ot('a2', 'C', 20)], ops,
                       [LatticeInstance('li', gen[0], 'C', 'a2', gen[1])])
        paths = layer.effective_paths()
        src, cum = _src_frame(c)
        for a, b in _removed(layer, 'C')[0]:
            _assert_no_lattice_in_gap(paths, src, cum, True, a, b)
        # Three openings split the ring into three assemblies; lattice never
        # reconnects them.
        assert nx.number_connected_components(_graph(layer)) == 3
        m = _metrics(layer)
        assert m['print_runs'] == 3 and m['travel_moves'] == 2
        _assert_moves_avoid_openings(layer,
                                     route_layer(layer.to_routing_layer()), 'C')

    def test_serialisation_preserves_multiple_openings(self):
        ops = [{'id': f'o{k}', 'source_path_id': 'L', 'center_s': 30 + 50 * k,
                'width': 12 + k, 'end_treatment': 'inherit',
                'z_min': None if k else 0, 'z_max': None if k else 84,
                'label': f'w{k}'} for k in range(3)]
        layer = design_app._deserialise_layer(
            TestSerialisation()._payload(openings=ops))
        assert layer.to_dict()['openings'] == [{**o, 'type': 'opening'}
                                               for o in ops]
        r = TestSerialisation()._client().post(
            '/api/route', data=json.dumps(TestSerialisation()._payload(
                openings=ops)), content_type='application/json')
        m = r.get_json()['metrics']
        assert m['print_runs'] == 4 and m['travel_moves'] == 3


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_union_matches_backend_for_touching_and_seam_openings():
    """Drawn gaps (JS) and printed cuts (backend) agree for touching
    openings, near-touching openings and openings unioned across the seam."""
    cases = [
        ('L', False, [[0, 0], [200, 0]],
         [(35, 30), (65.0004, 30), (150, 10)]),
        ('R', True, [[0, 0], [200, 0], [200, 120], [0, 120]],
         [(640, 20), (10, 30), (300, 12)]),
    ]
    for pid, closed, pts, ops in cases:
        js_ops = [{'id': f'o{k}', 'source_path_id': pid, 'center_s': c,
                   'width': w} for k, (c, w) in enumerate(ops)]
        got = _run_js(f"""
          const layer = {{
            source_paths: [{{ id: '{pid}', closed: {str(closed).lower()},
                              visible: true, points: {json.dumps(pts)} }}],
            offset_treatments: [], cap_style: 'flat', cap_corner_radius: 0,
            openings: {json.dumps(js_ops)},
          }};
          console.log(JSON.stringify(_survivingPieces(layer.source_paths[0])));
        """)
        src = (LinePath(Vec2(0, 0), Vec2(200, 0), id='L') if pid == 'L'
               else RECT())
        paths = _layer(src, openings=[Opening(f'o{k}', pid, c, w)
                                      for k, (c, w) in enumerate(ops)]
                       ).effective_paths()
        want = [[list(_xy(q)) for q in p.sample_points()]
                for p in _pieces(paths, pid)]
        assert [[[round(x, 6), round(y, 6)] for x, y in pc] for pc in got] \
            == want
