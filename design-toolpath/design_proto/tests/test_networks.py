"""
Wall networks — connected wall systems / printable regions.

Geometry that physically touches or crosses is connected. Systems whose
beads touch form a wall network: junctions become shared nodes, attached
ends splice into their host instead of being capped, the network's
material is one printable region (faces = its boundary, internal geometry
inside it), and the router prints it continuously wherever it is
physically connected.

Lettered classes follow the feature brief (A–O).
"""
import sys, os, math, json, time, shutil, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as design_app          # before toolpath_proto (it has an app.py too)
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..',
                             'toolpath_proto'))

import pytest
from graph import route_layer, compute_metrics, build_graph, graph_info
import network as N
from model import (
    Vec2, ExplicitPath, LinePath, CirclePath, EllipsePath, RectanglePath,
    QuadBezierPath, OffsetTreatment, LatticeInstance, PrintLayer, Opening,
    RegionOverride, RegionInfill, _processed_source_pts, _point_in_polygon,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ot(oid, sid, d):
    return OffsetTreatment(id=oid, source_path_id=sid, distance=d)


def _layer(srcs, offsets=(), lattices=(), openings=(), overrides=(), **kw):
    return PrintLayer(id='t', source_paths=list(srcs),
                      offset_treatments=list(offsets),
                      lattice_instances=list(lattices),
                      openings=list(openings),
                      region_overrides=list(overrides), **kw)


def _build(layer):
    paths, meta = layer._build_effective()
    return paths, meta


def _pts(path, meta):
    return meta['pts'].get(id(path)) or path.sample_points()


def _route(layer):
    paths, meta = _build(layer)
    rl = layer.to_routing_layer(paths, meta)
    moves = route_layer(rl)
    return moves, compute_metrics(moves), graph_info(rl), build_graph(rl)


def _metrics(layer):
    return _route(layer)[1]


def _by_id(paths):
    return {p.id: p for p in paths}


def _material_at(summary, p):
    """Is p wall material according to the network's classified regions?"""
    for r in summary['regions']:
        if not r['material']:
            continue
        outer = [Vec2(*q) for q in r['outer']]
        if not _point_in_polygon(p, outer):
            continue
        if any(_point_in_polygon(p, [Vec2(*q) for q in h]) for h in r['holes']):
            continue
        return True
    return False


def _segments(paths, meta, cls=None):
    out = []
    for p in paths:
        if cls is not None and meta['cls'].get(id(p)) not in cls:
            continue
        pts = _pts(p, meta)
        n = len(pts)
        for i in range(n if p.closed and n > 2 else n - 1):
            a, b = pts[i], pts[(i + 1) % n]
            if a.dist(b) > 1e-9:
                out.append((p.id, a, b))
    return out


def _sides(a, b, eps=1e-3):
    m = a.lerp(b, 0.5)
    L = a.dist(b)
    nx, ny = -(b.y - a.y) / L, (b.x - a.x) / L
    return Vec2(m.x + nx * eps, m.y + ny * eps), Vec2(m.x - nx * eps, m.y - ny * eps)


def _assert_faces_bound_material(layer):
    """Every printed FACE segment separates wall from void: no wall face is
    left buried inside the combined wall or floating in a void."""
    paths, meta = _build(layer)
    summ = meta['network']
    for pid, a, b in _segments(paths, meta, {N.FACE}):
        # (junction fillet arcs add/remove a sliver of wall that the region
        # summary — classified before rounding — does not include)
        if a.dist(b) < 1e-3 or pid.startswith('junction:'):
            continue
        l, r = _sides(a, b)
        assert _material_at(summ, l) != _material_at(summ, r), \
            f'face {pid} {a}->{b} does not bound the wall region'


def _in_final_regions(meta, p):
    """Wall material after junction rounding (the regions infill and
    return paths are built in)."""
    return any(sum(1 for r in rings if _point_in_polygon(p, r)) % 2 == 1
               for rings in meta.get('regions', []))


def _assert_internal_inside(layer, eps=1e-3):
    """Internal print geometry (lattice, centre lines, joins, return
    paths) never runs through a void: sampled points are wall material."""
    paths, meta = _build(layer)
    summ = meta['network']
    for pid, a, b in _segments(paths, meta, {N.INTERNAL}):
        L = a.dist(b)
        if L < 4 * eps:
            continue
        for k in range(1, 10):
            q = a.lerp(b, k / 10)
            l, r = _sides(q, q.lerp(b, 0.01) if k < 10 else b)
            assert (_material_at(summ, l) or _material_at(summ, r) or
                    _in_final_regions(meta, l) or _in_final_regions(meta, r)), \
                f'internal {pid} passes through a void at {q}'


def _collinear_overlap(a, b, c, d, tol=1e-6):
    """Length over which segments a-b and c-d overlap collinearly."""
    L = a.dist(b)
    if L < 1e-12:
        return 0.0
    ux, uy = (b.x - a.x) / L, (b.y - a.y) / L
    for p in (c, d):
        if abs((p.x - a.x) * uy - (p.y - a.y) * ux) > tol:
            return 0.0
    t0 = (c.x - a.x) * ux + (c.y - a.y) * uy
    t1 = (d.x - a.x) * ux + (d.y - a.y) * uy
    lo, hi = max(0.0, min(t0, t1)), min(L, max(t0, t1))
    return max(0.0, hi - lo)


def _assert_no_duplicate_walls(layer):
    """No two printed beads overlap along a length (no duplicate walls)."""
    paths, meta = _build(layer)
    segs = _segments(paths, meta)
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if segs[i][0] == segs[j][0]:
                continue
            ov = _collinear_overlap(segs[i][1], segs[i][2], segs[j][1], segs[j][2])
            assert ov < 1e-6, f'{segs[i][0]} and {segs[j][0]} overlap by {ov}'


def _dist_to(paths, meta, pid_prefix, p):
    best = float('inf')
    for path in paths:
        if path.id == pid_prefix or path.id.startswith(pid_prefix + '~'):
            best = min(best, N.dist_to_polyline(p, _pts(path, meta), path.closed))
    return best


# Standard scene: 200 × 120 room (CCW), wall thickness 10 (inside offset),
# with a branch leaving its bottom wall at x = 200.
def ROOM():
    return RectanglePath(100, 140, 200, 120, id='R')


def ROOM_OFFS():
    return [_ot('Ri', 'R', 10)]


# ---------------------------------------------------------------------------
# A  Endpoint → midpoint T junction
# ---------------------------------------------------------------------------

class TestA_TJunction:

    def test_wire_t_shares_a_node(self):
        L = _layer([LinePath(Vec2(0, 0), Vec2(200, 0), id='M'),
                    LinePath(Vec2(73.25, 0), Vec2(73.25, 90), id='B')])
        _, m, g, G = _route(L)
        assert g['component_count'] == 1
        assert G.degree((73.25, 0)) == 3
        assert m['travel_moves'] == 0

    def test_t_reported_as_network_junction(self):
        L = _layer([LinePath(Vec2(0, 0), Vec2(200, 0), id='M'),
                    LinePath(Vec2(50, 0), Vec2(80, 90), id='B')])
        summ = L.network_summary()
        assert summ['components'] == [{'sources': ['B', 'M'], 'lattices': []}]
        assert [50, 0] in [[round(j['x'], 6), round(j['y'], 6)] for j in summ['junctions']]

    def test_thick_t_on_open_host_one_continuous_run(self):
        # Host line ±6, branch ±5 at an oblique angle: one region.
        L = _layer([LinePath(Vec2(0, 0), Vec2(240, 0), id='M'),
                    LinePath(Vec2(97.3, 0), Vec2(140, 110), id='B')],
                   [_ot('Ml', 'M', 6), _ot('Mr', 'M', -6),
                    _ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        m = _metrics(L)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)

    def test_attached_end_has_no_cap(self):
        L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(200, 60), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        ids = _by_id(L.effective_paths())
        assert 'B_cs' not in ids                 # attached start: no cap
        assert 'B_ce' in ids                     # free end keeps its cap


# ---------------------------------------------------------------------------
# B  Midpoint crossing → X junction
# ---------------------------------------------------------------------------

class TestB_XJunction:

    @pytest.mark.parametrize('b', [((100, 0), (100, 200)),
                                   ((37.1, -13.9), (161.7, 211.3))])
    def test_wire_crossing_is_a_degree_four_node(self, b):
        L = _layer([LinePath(Vec2(0, 100), Vec2(200, 100), id='A'),
                    LinePath(Vec2(*b[0]), Vec2(*b[1]), id='B')])
        _, m, g, G = _route(L)
        assert g['component_count'] == 1
        assert max(d for _, d in G.degree()) == 4
        assert m['travel_moves'] == 0 and m['print_runs'] == 1

    def test_graph_splits_crossing_without_shared_vertex(self):
        # The routing graph itself recognises crossings (any geometry).
        from geometry import Strand, Layer as RL, Vec2 as RV
        lay = RL(0, [Strand('a', 'free', [RV(0, 0), RV(10, 10)], False),
                     Strand('b', 'free', [RV(0, 10), RV(10, 0)], False)])
        G = build_graph(lay)
        assert G.degree((5.0, 5.0)) == 4
        assert graph_info(lay)['component_count'] == 1

    def test_thick_x_forms_plus_shaped_region(self):
        L = _layer([LinePath(Vec2(0, 100), Vec2(200, 100), id='A'),
                    LinePath(Vec2(100, 0), Vec2(130, 200), id='B')],
                   [_ot('Al', 'A', 5), _ot('Ar', 'A', -5),
                    _ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        _assert_faces_bound_material(L)
        _assert_internal_inside(L)
        _assert_no_duplicate_walls(L)
        paths, meta = _build(L)
        # every face inside the other wall's band was removed
        for pid, a, b in _segments(paths, meta, {N.FACE}):
            m = a.lerp(b, 0.5)
            assert not (95 + 1e-6 < m.y < 105 - 1e-6 and 100 < m.x < 135), pid
        m = _metrics(L)
        assert m['travel_moves'] == 0 and m['print_runs'] == 1

    def test_x_retrace_is_internal_only(self):
        L = _layer([LinePath(Vec2(0, 100), Vec2(200, 100), id='A'),
                    LinePath(Vec2(100, 0), Vec2(130, 200), id='B')],
                   [_ot('Al', 'A', 5), _ot('Ar', 'A', -5),
                    _ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        paths, meta = _build(L)
        cls = {p.id: meta['cls'].get(id(p)) for p in paths}
        moves = route_layer(L.to_routing_layer(paths, meta))
        retraced = {cls[m.strand_id] for m in moves if m.kind == 'retrace'}
        assert retraced <= {N.INTERNAL}


# ---------------------------------------------------------------------------
# C  Line attached to a closed rectangle
# ---------------------------------------------------------------------------

class TestC_LineOnRectangle:

    def test_wire_line_on_wire_rectangle(self):
        L = _layer([ROOM(), LinePath(Vec2(243.7, 140), Vec2(275, 30), id='B')])
        _, m, g, G = _route(L)
        assert g['component_count'] == 1
        assert G.degree((243.7, 140)) == 3
        assert m['travel_moves'] == 0

    def test_wire_line_on_thick_rectangle(self):
        L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(200, 60), id='B')],
                   ROOM_OFFS())
        _, m, g, _ = _route(L)
        assert g['component_count'] == 2         # outer face + line | inner face
        assert m['travel_moves'] == 1

    def test_thick_branch_joins_room_into_one_run(self):
        # Branch centre line reaches the inner face: everything connected.
        L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(200, 60), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves'], m['retrace_distance']) == (1, 0, 0)

    def test_line_attached_to_inner_face_from_inside(self):
        # Snapped onto the visible inner offset face (y = 250).
        L = _layer([ROOM(), LinePath(Vec2(150, 180), Vec2(222.2, 250), id='W')],
                   ROOM_OFFS())
        _, _, g, G = _route(L)
        assert G.degree((222.2, 250)) == 3
        assert g['component_count'] == 2         # outer face is separate

    def test_line_at_rectangle_corner_vertex(self):
        L = _layer([ROOM(), LinePath(Vec2(300, 140), Vec2(360, 80), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        assert _metrics(L)['travel_moves'] == 0


# ---------------------------------------------------------------------------
# D  Multiple branches attached to a closed shape
# ---------------------------------------------------------------------------

class TestD_MultipleBranches:

    def _layer(self, **kw):
        return _layer(
            [ROOM(),
             LinePath(Vec2(150, 140), Vec2(120, 40), id='B1'),
             LinePath(Vec2(300, 200), Vec2(390, 230), id='B2'),
             LinePath(Vec2(210, 260), Vec2(210, 340), id='B3')],
            ROOM_OFFS() + [_ot('B1l', 'B1', 6), _ot('B2l', 'B2', 8),
                           _ot('B3l', 'B3', 5), _ot('B3r', 'B3', -5)], **kw)

    def test_one_network_with_all_branches(self):
        summ = self._layer().network_summary()
        assert len(summ['components']) == 1
        assert summ['components'][0]['sources'] == ['B1', 'B2', 'B3', 'R']

    def test_outer_face_is_one_closed_loop(self):
        # The room's outer face and every branch's two faces + far cap form
        # ONE closed boundary (the inner face stays separate).
        L = self._layer()
        paths, meta = _build(L)
        import networkx as nx
        G = nx.MultiGraph()
        for pid, a, b in _segments(paths, meta, {N.FACE}):
            G.add_edge((round(a.x, 6), round(a.y, 6)), (round(b.x, 6), round(b.y, 6)))
        comps = list(nx.connected_components(G))
        assert len(comps) == 2                    # outer boundary + inner face
        assert all(d == 2 for _, d in G.degree())  # each a simple closed loop

    def test_geometry_invariants(self):
        L = self._layer()
        _assert_faces_bound_material(L)
        _assert_internal_inside(L)
        _assert_no_duplicate_walls(L)

    @pytest.mark.parametrize('style,r', [('flat', 0), ('rounded_corners', 3),
                                         ('full_round', 0)])
    def test_with_corner_radius_and_cap_styles(self, style, r):
        L = self._layer(corner_radius=12, cap_style=style, cap_corner_radius=r)
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        assert len(L.network_summary()['components']) == 1


# ---------------------------------------------------------------------------
# E  Curved geometry participating in junctions
# ---------------------------------------------------------------------------

def _on_polyline(path, i, f, corner_radius=0.0):
    pts = _processed_source_pts(path, corner_radius)
    return pts[i].lerp(pts[(i + 1) % len(pts)], f)


class TestE_Curves:

    def test_line_on_circle_uses_canvas_polyline(self):
        C = CirclePath(200, 200, 80, id='C')
        e = _on_polyline(C, 100, 0.3)
        L = _layer([C, LinePath(e, Vec2(e.x - 60, e.y - 90), id='B')])
        _, m, g, G = _route(L)
        assert g['component_count'] == 1 and m['travel_moves'] == 0
        assert G.degree((e.x, e.y)) == 3
        rl = L.to_routing_layer()
        circle = next(s for s in rl.strands if s.id == 'C')
        assert len(circle.points) == 128         # = canvas / offset polyline

    def test_skewed_thick_branch_on_circle_has_no_gap(self):
        C = CirclePath(200, 200, 80, id='C')
        e = _on_polyline(C, 100, 0.3)
        L = _layer([C, LinePath(e, Vec2(e.x - 60, e.y - 90), id='B')],
                   [_ot('Ci', 'C', 10), _ot('Bl', 'B', 6), _ot('Br', 'B', -6)])
        paths, meta = _build(L)
        # both branch faces (trimmed or extended) end exactly on the
        # circle's outer face — no notch, no overlap
        joins = [p for p in paths if p.id.startswith('B_sj_f')]
        for fid in ('Bl', 'Br'):
            face = [p for p in paths if p.id == fid or p.id.startswith(fid + '~')]
            ends = [q for p in face + joins for q in (_pts(p, meta)[0], _pts(p, meta)[-1])]
            assert min(_dist_to(paths, meta, 'C', q) for q in ends) < 1e-6, fid
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves']) == (1, 0)

    def test_branch_on_bezier_far_face(self):
        Q = QuadBezierPath(Vec2(0, 0), Vec2(300, 0), Vec2(150, 120), id='Q')
        e = _on_polyline(Q, 40, 0.5)
        L = _layer([Q, LinePath(e, Vec2(e.x + 20, e.y + 110), id='B')],
                   [_ot('Ql', 'Q', 10), _ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves'], m['retrace_distance']) == (1, 0, 0)

    def test_curve_attached_to_rounded_rectangle(self):
        R = ROOM()
        s = _on_polyline(R, 3, 0.5, corner_radius=15)
        C = QuadBezierPath(s, Vec2(s.x - 70, s.y + 10), Vec2(s.x - 60, s.y + 80), id='K')
        L = _layer([R, C], ROOM_OFFS() + [_ot('Kl', 'K', 6), _ot('Kr', 'K', -6)],
                   corner_radius=15)
        assert len(L.network_summary()['components']) == 1
        _assert_faces_bound_material(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_circle_crossing_line(self):
        L = _layer([CirclePath(100, 100, 50, id='C'),
                    LinePath(Vec2(0, 103.3), Vec2(200, 91.7), id='A')])
        _, m, g, G = _route(L)
        assert g['component_count'] == 1
        assert sum(1 for _, d in G.degree() if d == 4) == 2
        assert m['travel_moves'] == 0


# ---------------------------------------------------------------------------
# F / G  Offsets at junctions integrate into one wall boundary
# ---------------------------------------------------------------------------

class TestFG_OffsetsAtJunctions:

    def _t(self, branch_offsets, end=(200, 60)):
        return _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(*end), id='B')],
                      ROOM_OFFS() + branch_offsets)

    def test_host_face_removed_across_the_mouth(self):
        L = self._t([_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        paths, meta = _build(L)
        for pid, a, b in _segments(paths, meta):
            if pid.startswith('R'):
                m = a.lerp(b, 0.5)
                assert not (abs(m.y - 140) < 1e-9 and 195 < m.x < 205), \
                    'host face still printed across the T mouth'
        assert 'R' in L.network_summary()['modified_sources']

    def test_branch_faces_end_on_host_face(self):
        L = self._t([_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        paths, meta = _build(L)
        ends = {(round(_pts(p, meta)[0].x, 6), round(_pts(p, meta)[0].y, 6))
                for p in paths if p.id in ('Bl', 'Br')}
        assert ends == {(195.0, 140.0), (205.0, 140.0)}
        host = [p for p in paths if p.id.startswith('R~n')]
        tips = {(round(q.x, 6), round(q.y, 6))
                for p in host for q in (_pts(p, meta)[0], _pts(p, meta)[-1])}
        assert ends <= tips                      # spliced, not overlapping

    def test_centre_line_continues_to_next_host_wall(self):
        L = self._t([_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        ids = _by_id(L.effective_paths())
        ext = ids['B_sj_i1']
        pts = ext.sample_points()
        assert (pts[0].x, pts[0].y) == (200, 140)
        assert (round(pts[-1].x, 9), round(pts[-1].y, 9)) == (200, 150)

    @pytest.mark.parametrize('end', [(200, 60), (260, 70), (130, 50), (205, 30)])
    def test_invariants_any_angle(self, end):
        L = self._t([_ot('Bl', 'B', 5), _ot('Br', 'B', -5)], end=end)
        _assert_faces_bound_material(L)
        _assert_internal_inside(L)
        _assert_no_duplicate_walls(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_skewed_branch_closes_the_shortfall_gap(self):
        # 35° branch: one face falls short of the host face, the other
        # overshoots. The short one is extended, the gap becomes wall.
        L = self._t([_ot('Bl', 'B', 8), _ot('Br', 'B', -8)], end=(257, 58.6))
        paths, meta = _build(L)
        ext = [p for p in paths if p.id.startswith('B_sj_f')]
        assert ext, 'expected a face extension'
        for p in ext:
            assert abs(_pts(p, meta)[-1].y - 140) < 1e-9
        _assert_faces_bound_material(L)

    def test_one_sided_branch(self):
        L = self._t([_ot('Bl', 'B', 10)])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        m = _metrics(L)
        assert m['print_runs'] == 2 and m['travel_moves'] == 1   # inner face separate

    def test_branch_ending_inside_host_wall(self):
        L = _layer([ROOM(), LinePath(Vec2(200, 146), Vec2(200, 60), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        _assert_faces_bound_material(L)
        _assert_internal_inside(L)
        m = _metrics(L)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_branch_onto_centre_line_host(self):
        # Host wall ±10 around its centre line; branch ends ON the centre line.
        L = _layer([LinePath(Vec2(0, 0), Vec2(300, 0), id='M'),
                    LinePath(Vec2(150, 0), Vec2(170, -120), id='B')],
                   [_ot('Ml', 'M', 10), _ot('Mr', 'M', -10),
                    _ot('Bl', 'B', 6), _ot('Br', 'B', -6)])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        _, m, _, G = _route(L)
        assert G.degree((150, 0)) >= 3           # centre lines meet: T
        # (a hidden return path may also land there to close the route)
        assert m['travel_moves'] == 0

    def test_corner_join_is_mitred(self):
        # Two one-sided walls meeting end to end at 53°: a true corner.
        L = _layer([LinePath(Vec2(0, 0), Vec2(100, 0), id='A'),
                    LinePath(Vec2(100, 0), Vec2(160, 80), id='B')],
                   [_ot('Al', 'A', 10), _ot('Bl', 'B', 10)])
        paths, meta = _build(L)
        ids = _by_id(paths)
        assert 'A_ce' not in ids and 'B_cs' not in ids
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        _, m, g, G = _route(L)
        assert g['odd_degree_nodes'] == 0 and m['print_runs'] == 1
        # inner corner: A's face y=10 meets B's left face
        inner = [n for n in G.nodes if abs(n[1] - 10) < 1e-9 and G.degree(n) == 2
                 and 80 < n[0] < 100]
        assert inner

    def test_straight_continuation_with_step(self):
        L = _layer([LinePath(Vec2(0, 0), Vec2(100, 0), id='A'),
                    LinePath(Vec2(100, 0), Vec2(200, 0), id='B')],
                   [_ot('Al', 'A', 10), _ot('Bl', 'B', 6)])
        _assert_faces_bound_material(L)
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves']) == (1, 0)

    def test_y_hub(self):
        L = _layer([LinePath(Vec2(0, 0), Vec2(100, 100), id='A'),
                    LinePath(Vec2(200, 0), Vec2(100, 100), id='B'),
                    LinePath(Vec2(100, 100), Vec2(100, 220), id='C')],
                   [_ot(f'{s}{d}', s, v) for s in 'ABC'
                    for d, v in (('l', 5), ('r', -5))])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        assert _metrics(L)['travel_moves'] == 0

    def test_overlapping_rings_merge(self):
        L = _layer([RectanglePath(0, 0, 120, 100, id='R1'),
                    RectanglePath(80, 40, 120, 100, id='R2')],
                   [_ot('R1i', 'R1', 10), _ot('R2i', 'R2', 10)])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)


# ---------------------------------------------------------------------------
# H  Lattice through a combined wall region
# ---------------------------------------------------------------------------

class TestH_LatticeInRegions:

    def _t_lattice(self, gen='zigzag', params=None):
        params = params or {'segments': 12, 'connect_ends': 1}
        return _layer(
            [ROOM(), LinePath(Vec2(200, 140), Vec2(230, 40), id='B')],
            ROOM_OFFS() + [_ot('Bl', 'B', 8), _ot('Br', 'B', -8)],
            [LatticeInstance('LR', gen, 'R', 'Ri', dict(params)),
             LatticeInstance('LB', gen, 'Bl', 'Br', dict(params))])

    @pytest.mark.parametrize('gen,params', [('zigzag', {'segments': 12, 'connect_ends': 1}),
                                            ('wave', {'cycles': 6})])
    def test_lattices_stay_in_the_combined_region(self, gen, params):
        L = self._t_lattice(gen, params)
        _assert_internal_inside(L)
        _assert_faces_bound_material(L)

    def test_lattices_connect_through_the_junction(self):
        _, m, g, _ = _route(self._t_lattice())
        assert g['component_count'] == 1
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    def test_lattice_continues_around_an_island(self):
        # One region (C) between the outer wall and two islands (A, B).
        # The lattice spans outer↔A and must not enter island B.
        O = RectanglePath(0, 0, 300, 160, id='O')
        A = RectanglePath(40, 50, 70, 60, id='A')
        B = RectanglePath(180, 30, 80, 70, id='B')
        L = _layer([O, A, B], lattices=[LatticeInstance(
            'L', 'zigzag', 'O', 'A', {'segments': 10, 'connect_ends': 1})])
        paths, meta = _build(L)
        lat = [p for p in paths if p.role == 'lattice']
        Bpts = B.sample_points()
        for p in lat:
            pts = _pts(p, meta)
            for u, v in zip(pts, pts[1:]):
                for k in range(1, 20):
                    q = u.lerp(v, k / 20)
                    assert not (180 + 1e-6 < q.x < 260 - 1e-6 and
                                30 + 1e-6 < q.y < 100 - 1e-6), 'lattice inside island B'
        # it continues on both sides of B (lands on B's wall)
        _, _, G = None, None, _route(L)[3]
        on_b = [n for n in G.nodes if N.dist_to_polyline(Vec2(*n), Bpts, True) < 1e-6
                and G.degree(n) >= 3]
        assert len(on_b) >= 2
        assert _metrics(L)['travel_moves'] == 0

    def test_lattice_without_foreign_contact_untouched(self):
        # Ordinary wall + lattice (no other system): exactly the old output.
        L = _layer([ROOM()], ROOM_OFFS(),
                   [LatticeInstance('LR', 'zigzag', 'R', 'Ri',
                                    {'segments': 12, 'connect_ends': 1})])
        assert L.network_summary()['components'] == []


# ---------------------------------------------------------------------------
# I  Dead-end branches
# ---------------------------------------------------------------------------

class TestI_DeadEnds:

    def test_double_wall_branch_folds_into_the_outline(self):
        # ======== main wall with a one-sided double-wall branch: the
        # branch goes down one side, round its end, back up the other and
        # rejoins — no retrace, no travel.
        L = _layer([LinePath(Vec2(0, 100), Vec2(300, 100), id='M'),
                    LinePath(Vec2(150, 100), Vec2(150, 0), id='B')],
                   [_ot('Ml', 'M', 12), _ot('Bl', 'B', 12)])
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves'], m['retrace_distance']) == (1, 0, 0)

    def test_branch_with_centre_line_needs_no_retrace(self):
        L = _layer([LinePath(Vec2(0, 100), Vec2(300, 100), id='M'),
                    LinePath(Vec2(150, 100), Vec2(150, 0), id='B')],
                   [_ot('Ml', 'M', 12), _ot('Bl', 'B', 6), _ot('Br', 'B', -6)])
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves'], m['retrace_distance']) == (1, 0, 0)

    def test_wire_dead_end_is_retraced(self):
        # A single-bead branch has no other way back: retrace it.
        L = _layer([LinePath(Vec2(0, 100), Vec2(300, 100), id='M'),
                    LinePath(Vec2(150, 100), Vec2(150, 0), id='B')])
        m = _metrics(L)
        assert m['travel_moves'] == 0
        assert m['retrace_distance'] == pytest.approx(100.0)

    def test_fallback_retrace_prefers_internal_geometry(self):
        # With return paths disabled (the old model) the router retraces,
        # mostly on lattice / centre lines. (Since lattice crossings are
        # structural, not junctions, a little face retrace can appear in
        # this opt-out mode — the default mode adds return paths instead.)
        L = TestH_LatticeInRegions()._t_lattice()
        L.return_paths = False
        paths, meta = _build(L)
        cls = {p.id: meta['cls'].get(id(p)) for p in paths}
        moves = route_layer(L.to_routing_layer(paths, meta))
        by = {}
        for m in moves:
            if m.kind == 'retrace':
                by[cls[m.strand_id]] = by.get(cls[m.strand_id], 0) + m.length
        assert by and by.get(N.INTERNAL, 0) > by.get(N.FACE, 0)

    def test_region_infill_repair_replaces_the_retrace(self):
        # With REGION infill the field is repaired locally, so nothing is
        # printed twice. (Legacy pairwise lattice is not a repairable field.)
        L = TestH_LatticeInRegions()._t_lattice()
        L.lattice_instances = []
        L.infills = [RegionInfill('I', 'R', 'zigzag', {'spacing': 20})]
        m = _metrics(L)
        assert m['retrace_distance'] == 0
        assert (m['print_runs'], m['travel_moves']) == (1, 0)


# ---------------------------------------------------------------------------
# J / K  Continuous when connected, travel only when disconnected
# ---------------------------------------------------------------------------

class TestJK_Connectivity:

    @pytest.mark.parametrize('case', ['t', 'x', 'corner', 'curve'])
    def test_connected_network_prints_with_zero_travel(self, case):
        if case == 't':
            L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(200, 60), id='B')],
                       ROOM_OFFS() + [_ot('Bl', 'B', 5), _ot('Br', 'B', -5)])
        elif case == 'x':
            L = _layer([LinePath(Vec2(0, 100), Vec2(200, 100), id='A'),
                        LinePath(Vec2(100, 0), Vec2(130, 200), id='B')],
                       [_ot('Al', 'A', 5), _ot('Ar', 'A', -5)])
        elif case == 'corner':
            # open chain of three one-sided walls joined end to end
            L = _layer([LinePath(Vec2(0, 0), Vec2(100, 0), id='A'),
                        LinePath(Vec2(100, 0), Vec2(130, 90), id='B'),
                        LinePath(Vec2(130, 90), Vec2(40, 140), id='C')],
                       [_ot('Al', 'A', 6), _ot('Bl', 'B', 6), _ot('Cl', 'C', 6)])
        else:
            C = CirclePath(200, 200, 80, id='C')
            e = _on_polyline(C, 20, 0.5)
            L = _layer([C, QuadBezierPath(e, Vec2(e.x + 90, e.y + 40),
                                          Vec2(e.x + 60, e.y - 30), id='Q')],
                       [_ot('Ci', 'C', 10), _ot('Ql', 'Q', 5), _ot('Qr', 'Q', -5)])
        m = _metrics(L)
        assert m['travel_moves'] == 0 and m['print_runs'] == 1

    def test_closed_chain_of_one_sided_walls_is_two_loops(self):
        # Like a ring with one offset and no lattice: outer and inner face
        # are genuinely separate — travel between them is correct.
        L = _layer([LinePath(Vec2(0, 0), Vec2(100, 0), id='A'),
                    LinePath(Vec2(100, 0), Vec2(100, 90), id='B'),
                    LinePath(Vec2(100, 90), Vec2(0, 0), id='C')],
                   [_ot('Al', 'A', 6), _ot('Bl', 'B', 6), _ot('Cl', 'C', 6)])
        _assert_faces_bound_material(L)
        _assert_no_duplicate_walls(L)
        _, m, g, _ = _route(L)
        assert g['component_count'] == 2 and g['odd_degree_nodes'] == 0
        assert m['travel_moves'] == 1

    def test_two_separate_networks_travel_once(self):
        L = _layer([LinePath(Vec2(0, 0), Vec2(100, 0), id='A1'),
                    LinePath(Vec2(50, 0), Vec2(50, 60), id='B1'),
                    LinePath(Vec2(200, 0), Vec2(300, 0), id='A2'),
                    LinePath(Vec2(250, 0), Vec2(250, 60), id='B2')])
        _, m, g, _ = _route(L)
        assert g['component_count'] == 2
        assert m['travel_moves'] == 1 and m['print_runs'] == 2
        assert len(L.network_summary()['components']) == 2

    def test_near_miss_is_not_connected(self):
        # 0.01 in short of the wall: a real gap — never bridged.
        L = _layer([ROOM(), LinePath(Vec2(200, 139.99), Vec2(200, 60), id='B')])
        _, m, g, _ = _route(L)
        assert g['component_count'] == 2 and m['travel_moves'] == 1
        assert L.network_summary()['components'] == []

    def test_float_noise_contact_is_snapped(self):
        # Within SNAP_TOL (0.001 in): float noise, treated as contact.
        L = _layer([ROOM(), LinePath(Vec2(200, 139.9996), Vec2(200, 60), id='B')])
        _, m, g, _ = _route(L)
        assert g['component_count'] == 1 and m['travel_moves'] == 0

    def test_band_overlap_without_bead_contact_is_not_a_network(self):
        # A small free-standing wire inside a thick wall's cavity touches
        # nothing: it is not connected (and not removed).
        L = _layer([ROOM(), LinePath(Vec2(150, 144), Vec2(170, 144), id='W')],
                   ROOM_OFFS())
        assert L.network_summary()['components'] == []
        assert 'W' in _by_id(L.effective_paths())


# ---------------------------------------------------------------------------
# L / M  Existing behaviour preserved
# ---------------------------------------------------------------------------

def _without_networks(layer):
    """Effective paths with the network pass disabled (the pre-network
    pipeline), for comparison."""
    orig = PrintLayer._apply_networks
    PrintLayer._apply_networks = lambda self, N_, result, *a, **k: (
        result, {'components': [], 'junctions': [], 'modified_sources': [],
                 'regions': []})
    try:
        return layer.effective_paths()
    finally:
        PrintLayer._apply_networks = orig


def _sig(paths):
    """Comparable signature (lattice ids are random uuids → use role)."""
    return [(p.role if p.role == 'lattice' else p.id, p.closed,
             [(round(q.x, 9), round(q.y, 9)) for q in p.sample_points()])
            for p in paths]


class TestLM_Preserved:

    @pytest.mark.parametrize('layer_fn', [
        lambda: _layer([ROOM()], ROOM_OFFS()),
        lambda: _layer([ROOM()], ROOM_OFFS(),
                       [LatticeInstance('LR', 'zigzag', 'R', 'Ri',
                                        {'segments': 12, 'connect_ends': 1})],
                       [Opening('o1', 'R', 100, 30), Opening('o2', 'R', 400, 20)],
                       cap_style='rounded_corners', cap_corner_radius=3),
        lambda: _layer([LinePath(Vec2(0, 0), Vec2(200, 0), id='L')],
                       [_ot('Ll', 'L', 10), _ot('Lm', 'L', 5)],
                       openings=[Opening('o', 'L', 100, 24)], cap_style='full_round'),
        lambda: _layer([CirclePath(100, 100, 60, id='C'),
                        CirclePath(400, 400, 30, id='D')],
                       [_ot('Ci', 'C', 12)],
                       [LatticeInstance('LC', 'wave', 'C', 'Ci', {'cycles': 4})]),
    ])
    def test_isolated_systems_identical_to_pre_network_output(self, layer_fn):
        assert _sig(layer_fn().effective_paths()) == _sig(_without_networks(layer_fn()))

    def test_openings_on_ordinary_wall_unchanged_next_to_a_network(self):
        ring = lambda: [RectanglePath(0, 0, 160, 120, id='R')]
        offs = [_ot('Ri', 'R', 10)]
        ops = [Opening('o', 'R', 80, 24)]
        alone = _layer(ring(), offs, openings=ops).effective_paths()
        far = _layer(ring() + [LinePath(Vec2(400, 0), Vec2(500, 0), id='A'),
                               LinePath(Vec2(450, 0), Vec2(450, 50), id='B')],
                     offs, openings=ops).effective_paths()
        mine = [p for p in far if p.id.startswith('R')]
        assert _sig(mine) == _sig(alone)

    def test_existing_retrace_case_still_one_run(self):
        # The reported opening case (ring + zigzag + 1 opening, Flat).
        L = _layer([RectanglePath(0, 0, 200, 120, id='R')], [_ot('Ri', 'R', 10)],
                   [LatticeInstance('Z', 'zigzag', 'R', 'Ri',
                                    {'segments': 8, 'connect_ends': 1})],
                   [Opening('o', 'R', 60, 12)])
        m = _metrics(L)
        assert (m['print_runs'], m['travel_moves']) == (1, 0)
        assert m['retrace_distance'] == pytest.approx(10 * math.sqrt(2), abs=0.01)


# ---------------------------------------------------------------------------
# N  Openings interacting with connected geometry
# ---------------------------------------------------------------------------

class TestN_OpeningsInNetworks:

    def _assert_doorway_clear(self, layer, x0, x1, y0, y1):
        paths, meta = _build(layer)
        for pid, a, b in _segments(paths, meta):
            for k in range(1, 20):
                q = a.lerp(b, k / 20)
                assert not (x0 + 1e-6 < q.x < x1 - 1e-6 and y0 + 1e-6 < q.y < y1 - 1e-6), \
                    f'{pid} prints inside the doorway at {q}'

    def test_opening_at_the_t_mouth_frees_the_branch_end(self):
        # The host is cut where the branch lands: no host material there,
        # so the branch end is free and keeps its cap; the doorway (clear
        # 10 in through the host wall) stays empty.
        L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(200, 60), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 8), _ot('Br', 'B', -8)],
                   openings=[Opening('o', 'R', 100, 10)])
        paths = L.effective_paths()
        assert any(p.id == 'B_cs' or p.id.startswith('B_cs~') for p in paths)
        self._assert_doorway_clear(L, 195, 205, 140, 150)
        _assert_faces_bound_material(L)

    def test_wall_through_a_doorway_is_cut_by_it(self):
        # A 30 in wall crosses the room wall where it has a 20 in door: the
        # door's clear width is void for every wall of the network, and the
        # crossing wall is cut and faced along the doorway.
        L = _layer([ROOM(), LinePath(Vec2(200, 100), Vec2(200, 190), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 15), _ot('Br', 'B', -15)],
                   openings=[Opening('o', 'R', 100, 20)])
        self._assert_doorway_clear(L, 190, 210, 140, 150)
        _assert_faces_bound_material(L)
        _assert_internal_inside(L)

    def test_wall_passing_freely_through_a_doorway_is_separate(self):
        # Narrower than the door, it touches nothing: a separate wall.
        L = _layer([ROOM(), LinePath(Vec2(200, 100), Vec2(200, 190), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 3), _ot('Br', 'B', -3)],
                   openings=[Opening('o', 'R', 100, 20)])
        assert L.network_summary()['components'] == []

    @pytest.mark.parametrize('gen', ['zigzag', 'wave'])
    def test_openings_near_a_junction_with_lattice(self, gen):
        params = {'segments': 12, 'connect_ends': 1} if gen == 'zigzag' else {'cycles': 6}
        L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(230, 40), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 8), _ot('Br', 'B', -8)],
                   [LatticeInstance('LR', gen, 'R', 'Ri', dict(params)),
                    LatticeInstance('LB', gen, 'Bl', 'Br', dict(params))],
                   [Opening('o1', 'R', 130, 24), Opening('o2', 'B', 60, 20)])
        _assert_internal_inside(L)
        _assert_faces_bound_material(L)
        self._assert_doorway_clear(L, 218 + 1e-3, 242 - 1e-3, 140, 150)
        _, m, g, _ = _route(L)
        # the branch piece beyond its own opening is genuinely separate
        assert g['component_count'] == 2 and m['travel_moves'] == 1

    def test_no_travel_or_retrace_crosses_a_doorway(self):
        L = _layer([ROOM(), LinePath(Vec2(200, 140), Vec2(200, 60), id='B')],
                   ROOM_OFFS() + [_ot('Bl', 'B', 5), _ot('Br', 'B', -5)],
                   [LatticeInstance('LR', 'zigzag', 'R', 'Ri',
                                    {'segments': 12, 'connect_ends': 1})],
                   [Opening('o', 'R', 270, 30)])
        moves, m, _, _ = _route(L)
        assert m['travel_moves'] == 0
        # doorway on the right wall: s = 270 ± 15 → y 195 … 225, x 290 … 300
        for mv in moves:
            for k in range(1, 10):
                q = mv.start.lerp(mv.end, k / 10)
                assert not (290 + 1e-6 < q.x < 300 - 1e-6 and
                            195 + 1e-6 < q.y < 225 - 1e-6), \
                    f'a {mv.kind} move crosses the doorway at {q}'


# ---------------------------------------------------------------------------
# Region overrides (paint bucket) — data model + classification
# ---------------------------------------------------------------------------

class TestRegionOverrides:

    def _abc(self, overrides=()):
        O = RectanglePath(0, 0, 300, 160, id='O')
        A = RectanglePath(40, 50, 70, 60, id='A')
        B = RectanglePath(180, 30, 80, 70, id='B')
        return _layer([O, A, B], lattices=[LatticeInstance(
            'L', 'zigzag', 'O', 'A', {'segments': 10, 'connect_ends': 1})],
            overrides=overrides)

    def test_regions_are_reported_and_classified(self):
        summ = self._abc().network_summary()
        regions = {r['region'] for r in summ['regions']}
        mat = {r['region'] for r in summ['regions'] if r['material']}
        assert len(mat) == 1 and len(regions) >= 3   # C | island B (| A)

    def test_painting_island_wall_keeps_lattice_inside_it(self):
        base = self._abc()
        painted = self._abc([RegionOverride('r', 'B', s=10, offset=5, kind='wall')])
        def inside(L):
            n = 0
            for p in L.effective_paths():
                if p.role != 'lattice':
                    continue
                pts = p.sample_points()
                for u, v in zip(pts, pts[1:]):
                    for k in range(1, 20):
                        q = u.lerp(v, k / 20)
                        n += 180 < q.x < 260 and 30 < q.y < 100
            return n
        assert inside(base) == 0
        assert inside(painted) > 0
        summ = painted.network_summary()
        assert any(r['override'] == 'wall' for r in summ['regions'])

    def test_override_is_path_relative(self):
        # Moving B carries its override with it.
        L = self._abc([RegionOverride('r', 'B', s=10, offset=5, kind='wall')])
        L.source_paths[2].x += 10
        assert any(r['override'] == 'wall' and r['material']
                   for r in L.network_summary()['regions'])

    def test_painting_wall_region_void_removes_its_lattice(self):
        # Paint C (the lattice's own region) void: no lattice survives.
        L = self._abc([RegionOverride('r', 'O', s=5, offset=3, kind='void')])
        assert not [p for p in L.effective_paths() if p.role == 'lattice']


# ---------------------------------------------------------------------------
# API / serialisation
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    design_app.app.config['TESTING'] = True
    with design_app.app.test_client() as c:
        yield c


def _payload(**extra):
    d = {
        'id': 't',
        'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'x': 100, 'y': 140, 'w': 200,
             'h': 120, 'closed': True},
            {'id': 'B', 'type': 'LinePath', 'start': [200, 140], 'end': [200, 60]},
        ],
        'offset_treatments': [
            {'id': 'Ri', 'source_path_id': 'R', 'distance': 10},
            {'id': 'Bl', 'source_path_id': 'B', 'distance': 5},
            {'id': 'Br', 'source_path_id': 'B', 'distance': -5},
        ],
    }
    d.update(extra)
    return d


class TestApi:

    def test_route_reports_network(self, client):
        r = client.post('/api/route', data=json.dumps(_payload()),
                        content_type='application/json')
        d = r.get_json()
        assert r.status_code == 200, d
        assert d['network']['components'][0]['sources'] == ['B', 'R']
        assert d['network']['modified_sources'] == ['R']
        assert d['metrics']['travel_moves'] == 0
        pieces = [p for p in d['layer']['paths'] if p.get('treatment_id') == 'network_src']
        assert pieces and all(p['source_id'] == 'R' for p in pieces)

    def test_effective_paths_reports_network(self, client):
        r = client.post('/api/effective_paths', data=json.dumps(_payload()),
                        content_type='application/json')
        assert r.get_json()['network']['junctions']

    def test_region_overrides_round_trip(self, client):
        ov = [{'id': 'r1', 'path_id': 'R', 's': 30, 'offset': 4, 'kind': 'void'}]
        layer = design_app._deserialise_layer(_payload(region_overrides=ov))
        d = layer.to_dict()
        assert d['region_overrides'][0] == {'id': 'r1', 'type': 'region_override',
                                            'path_id': 'R', 's': 30.0,
                                            'offset': 4.0, 'kind': 'void'}

    def test_payload_without_overrides_still_works(self, client):
        p = _payload()
        p['region_overrides'] = None
        r = client.post('/api/route', data=json.dumps(p),
                        content_type='application/json')
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class TestRobustness:

    def test_deterministic(self):
        mk = lambda: TestD_MultipleBranches()._layer(corner_radius=8)
        assert _sig(mk().effective_paths()) == _sig(mk().effective_paths())

    def test_complex_network_is_fast_enough(self):
        L = TestD_MultipleBranches()._layer(corner_radius=10)
        L.lattice_instances = [
            LatticeInstance('LR', 'wave', 'R', 'Ri', {'cycles': 10}),
            LatticeInstance('LB', 'zigzag', 'B3l', 'B3r', {'segments': 8, 'connect_ends': 1})]
        t = time.time()
        _route(L)
        assert time.time() - t < 3.0

    def test_branch_attached_to_itself_is_ignored(self):
        # A drawn path whose end lands on its own middle: no self-network.
        P = ExplicitPath([Vec2(0, 0), Vec2(100, 0), Vec2(100, 80),
                          Vec2(50, 80), Vec2(50, 0)], id='P')
        L = _layer([P], [_ot('Pl', 'P', 5)])
        assert L.network_summary()['components'] == []

    def test_hidden_paths_do_not_join(self):
        B = LinePath(Vec2(200, 140), Vec2(200, 60), id='B')
        B.visible = False
        assert _layer([ROOM(), B]).network_summary()['components'] == []


# ---------------------------------------------------------------------------
# UI smoke test — snapping, network drawing, payload (node)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_network_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_network_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI NETWORK SMOKE PASSED' in proc.stdout
