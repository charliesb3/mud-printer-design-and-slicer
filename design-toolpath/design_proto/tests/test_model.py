"""
Unit tests for the design canvas data model.

Tests cover:
- Primitive identity: LinePath, CirclePath, EllipsePath, RectanglePath
- ExplicitPath sample points
- OffsetTreatment — generates offset without mutating source
- ZigzagGenerator — correct number of paths, variation_index changes output
- WaveGenerator — correct output structure
- PrintLayer.effective_paths — assembles sources + offset + lattice
- PrintLayer.to_routing_layer — converts to Strand/Layer format
- TraversalConstraints — stored separately from geometry
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import math
import pytest
from model import (
    Vec2, ExplicitPath, LinePath, CirclePath, EllipsePath, RectanglePath,
    QuadBezierPath,
    OffsetTreatment, ZigzagGenerator, WaveGenerator, LatticeInstance,
    PrintLayer, TraversalConstraints, DerivedPath, _offset_polyline,
    _resample, _point_in_polygon, _polygon_area, _lattice_valid_in_cavity,
    _apply_corner_rounding, _fillet_vertex, _semicircle_cap,
    _eligible_for_rounding, _processed_source_pts, _wall_system_end_pts,
    _trim_offset, _segments_intersect,
)


# ---------------------------------------------------------------------------
# Vec2
# ---------------------------------------------------------------------------

class TestVec2:
    def test_dist(self):
        assert Vec2(0, 0).dist(Vec2(3, 4)) == pytest.approx(5.0)

    def test_lerp(self):
        a, b = Vec2(0, 0), Vec2(10, 10)
        m = a.lerp(b, 0.5)
        assert m.x == pytest.approx(5.0)
        assert m.y == pytest.approx(5.0)

    def test_add_sub_mul(self):
        a = Vec2(1, 2)
        b = Vec2(3, 4)
        assert (a + b).x == 4
        assert (b - a).y == 2
        assert (a * 2.0).x == 2.0

    def test_normalized(self):
        v = Vec2(3, 4).normalized()
        assert math.hypot(v.x, v.y) == pytest.approx(1.0)

    def test_perpendicular_is_perpendicular(self):
        v = Vec2(3, 4)
        p = v.perpendicular()
        assert v.x * p.x + v.y * p.y == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Path primitives — identity preservation
# ---------------------------------------------------------------------------

class TestLinePath:
    def test_sample_returns_two_endpoints(self):
        lp = LinePath(Vec2(0, 0), Vec2(10, 20))
        pts = lp.sample_points()
        assert len(pts) == 2
        assert pts[0].x == 0 and pts[1].x == 10

    def test_type_preserved(self):
        lp = LinePath(Vec2(0, 0), Vec2(1, 1))
        assert isinstance(lp, LinePath)
        assert lp.__class__.__name__ == 'LinePath'

    def test_to_dict_has_start_end(self):
        lp = LinePath(Vec2(1, 2), Vec2(3, 4))
        d = lp.to_dict()
        assert d['type'] == 'LinePath'
        assert d['start'] == [1, 2]
        assert d['end'] == [3, 4]


class TestCirclePath:
    def test_sample_returns_n_points(self):
        cp = CirclePath(50, 50, 30)
        pts = cp.sample_points(n=32)
        assert len(pts) == 32

    def test_points_lie_on_circle(self):
        cp = CirclePath(100, 100, 40)
        for pt in cp.sample_points(n=16):
            r = math.hypot(pt.x - cp.cx, pt.y - cp.cy)
            assert r == pytest.approx(cp.radius, abs=1e-9)

    def test_is_closed_by_default(self):
        cp = CirclePath(0, 0, 10)
        assert cp.closed is True

    def test_type_preserved(self):
        cp = CirclePath(0, 0, 10)
        assert cp.__class__.__name__ == 'CirclePath'


class TestEllipsePath:
    def test_sample_length(self):
        ep = EllipsePath(0, 0, 30, 20)
        assert len(ep.sample_points(n=48)) == 48

    def test_closed(self):
        assert EllipsePath(0, 0, 5, 3).closed is True

    def test_to_dict_has_axes(self):
        ep = EllipsePath(1, 2, 10, 5, rotation=0.3)
        d = ep.to_dict()
        assert d['rx'] == 10
        assert d['ry'] == 5
        assert d['rotation'] == pytest.approx(0.3)


class TestRectanglePath:
    def test_sample_four_corners(self):
        rp = RectanglePath(10, 20, 30, 40)
        pts = rp.sample_points()
        assert len(pts) == 4
        assert pts[0] == Vec2(10, 20)
        assert pts[2] == Vec2(40, 60)

    def test_is_closed(self):
        assert RectanglePath(0, 0, 10, 10).closed is True

    def test_arc_length_approx_perimeter(self):
        rp = RectanglePath(0, 0, 10, 20)
        assert rp.arc_length() == pytest.approx(60.0, abs=1e-9)


# ---------------------------------------------------------------------------
# ExplicitPath
# ---------------------------------------------------------------------------

class TestExplicitPath:
    def test_sample_returns_control_points(self):
        pts = [Vec2(0, 0), Vec2(5, 5), Vec2(10, 0)]
        ep = ExplicitPath(pts)
        assert ep.sample_points() == pts

    def test_insert_move_remove(self):
        ep = ExplicitPath([Vec2(0, 0), Vec2(10, 0), Vec2(20, 0)])
        ep.move_point(1, Vec2(10, 5))
        assert ep.points[1].y == 5
        ep.insert_point(1, Vec2(5, 2))
        assert len(ep.points) == 4
        ep.remove_point(1)
        assert len(ep.points) == 3


# ---------------------------------------------------------------------------
# OffsetTreatment — non-destructive
# ---------------------------------------------------------------------------

class TestOffsetTreatment:
    def _make_rect_path(self):
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)]
        return ExplicitPath(pts, closed=True, id='src')

    def test_generate_returns_derived_path(self):
        src = self._make_rect_path()
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-5)
        result = ot.generate(src.sample_points(128), src.closed)
        assert isinstance(result, DerivedPath)

    def test_source_not_mutated(self):
        src = self._make_rect_path()
        original_pts = [Vec2(p.x, p.y) for p in src.points]
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-10)
        ot.generate(src.sample_points(128), src.closed)
        assert src.points == original_pts

    def test_offset_changes_geometry(self):
        src = self._make_rect_path()
        src_pts = src.sample_points()
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-5)
        derived = ot.generate(src.sample_points(128), src.closed)
        off_pts = derived.sample_points()
        # Offset points must differ from source points
        assert any(
            abs(s.x - o.x) > 0.1 or abs(s.y - o.y) > 0.1
            for s, o in zip(src_pts, off_pts)
        )

    def test_derived_path_role(self):
        src = self._make_rect_path()
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-5, role='inner')
        result = ot.generate(src.sample_points(128), src.closed)
        assert result.role == 'inner'


# ---------------------------------------------------------------------------
# ZigzagGenerator
# ---------------------------------------------------------------------------

class TestZigzagGenerator:
    def _two_lines(self):
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='pa')
        b = ExplicitPath([Vec2(0, 20), Vec2(100, 20)], id='pb')
        return a, b

    def test_returns_at_least_one_path(self):
        gen = ZigzagGenerator()
        a, b = self._two_lines()
        result = gen.generate(a, b, {'segments': 4, 'connect_ends': False})
        assert len(result) >= 1

    def test_variation_changes_output(self):
        gen = ZigzagGenerator()
        a, b = self._two_lines()
        r0 = gen.generate(a, b, {'segments': 4, 'connect_ends': False}, variation_index=0)
        r1 = gen.generate(a, b, {'segments': 4, 'connect_ends': False}, variation_index=1)
        pts0 = r0[0].sample_points()
        pts1 = r1[0].sample_points()
        # Variations should produce different starting points
        assert pts0[0].y != pts1[0].y

    def test_connect_ends_adds_path(self):
        gen = ZigzagGenerator()
        a, b = self._two_lines()
        r_no = gen.generate(a, b, {'segments': 4, 'connect_ends': False})
        r_yes = gen.generate(a, b, {'segments': 4, 'connect_ends': True})
        assert len(r_yes) > len(r_no)

    def test_parameters_declared(self):
        specs = ZigzagGenerator().parameters()
        names = [s.name for s in specs]
        assert 'segments' in names
        assert 'connect_ends' in names

    def test_variation_count(self):
        gen = ZigzagGenerator()
        a, b = self._two_lines()
        assert gen.variation_count(a, b, {}) == 2

    def test_role_is_lattice(self):
        gen = ZigzagGenerator()
        a, b = self._two_lines()
        result = gen.generate(a, b, {'segments': 4, 'connect_ends': False})
        assert all(p.role == 'lattice' for p in result)


# ---------------------------------------------------------------------------
# WaveGenerator — redesigned: touches both boundaries, Cycles param
# ---------------------------------------------------------------------------

class TestWaveGenerator:
    def _two_lines(self):
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='wa')
        b = ExplicitPath([Vec2(0, 30), Vec2(100, 30)], id='wb')
        return a, b

    def test_returns_one_path(self):
        gen = WaveGenerator()
        a, b = self._two_lines()
        result = gen.generate(a, b, {'cycles': 3.0})
        assert len(result) == 1

    def test_parameters_declared(self):
        specs = WaveGenerator().parameters()
        names = [s.name for s in specs]
        assert 'cycles' in names
        # Phase removed in UX pass 4 (breaks seam bridge; user-verified useless)
        assert 'phase' not in names
        # Old amplitude/samples/frequency must not be present
        assert 'amplitude' not in names
        assert 'samples' not in names
        assert 'frequency' not in names

    def test_variation_count(self):
        gen = WaveGenerator()
        a, b = self._two_lines()
        assert gen.variation_count(a, b, {}) == 2

    def test_variation_changes_output(self):
        gen = WaveGenerator()
        a, b = self._two_lines()
        r0 = gen.generate(a, b, {'cycles': 3.0}, 0)
        r1 = gen.generate(a, b, {'cycles': 3.0}, 1)
        pts0 = r0[0].sample_points()
        pts1 = r1[0].sample_points()
        assert any(
            abs(pts0[i].x - pts1[i].x) > 0.1 or abs(pts0[i].y - pts1[i].y) > 0.1
            for i in range(len(pts0))
        )

    def test_wave_touches_boundary_a(self):
        """Wave alpha=0 → point exactly on boundary A."""
        gen = WaveGenerator()
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='wa')
        b = ExplicitPath([Vec2(0, 30), Vec2(100, 30)], id='wb')
        # V1: wave starts at alpha=0 → on A
        result = gen.generate(a, b, {'cycles': 3.0}, 0)
        pts = result[0].sample_points()
        # First point: t=0, alpha = 0.5*(1-cos(0)) = 0 → on A (y=0)
        assert abs(pts[0].y - 0.0) < 0.01

    def test_wave_touches_boundary_b(self):
        """Wave alpha=1 → point exactly on boundary B."""
        gen = WaveGenerator()
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='wa')
        b = ExplicitPath([Vec2(0, 30), Vec2(100, 30)], id='wb')
        # V1: first minimum of alpha=1 at t = 0.5/cycles
        result = gen.generate(a, b, {'cycles': 1.0}, 0)
        pts = result[0].sample_points()
        # At t=0.5 (midpoint), alpha = 0.5*(1-cos(π)) = 1 → on B (y=30)
        mid_idx = len(pts) // 2
        assert abs(pts[mid_idx].y - 30.0) < 0.5

    def test_wave_all_points_between_boundaries(self):
        """All wave points must lie between boundaries A and B (alpha ∈ [0,1])."""
        gen = WaveGenerator()
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='wa')
        b = ExplicitPath([Vec2(0, 50), Vec2(100, 50)], id='wb')
        result = gen.generate(a, b, {'cycles': 5.0}, 0)
        pts = result[0].sample_points()
        for p in pts:
            assert -0.01 <= p.y <= 50.01, f"Point y={p.y:.4f} outside [0, 50]"


# ---------------------------------------------------------------------------
# LatticeInstance
# ---------------------------------------------------------------------------

class TestLatticeInstance:
    def test_generate_returns_paths(self):
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='la')
        b = ExplicitPath([Vec2(0, 20), Vec2(100, 20)], id='lb')
        li = LatticeInstance(id='li1', generator_name='zigzag',
                             path_a_id='la', path_b_id='lb',
                             params={'segments': 4, 'connect_ends': False})
        from model import GENERATORS
        gen = GENERATORS['zigzag']
        result = li.generate(a, b, gen)
        assert len(result) >= 1

    def test_source_id_set(self):
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='la')
        b = ExplicitPath([Vec2(0, 20), Vec2(100, 20)], id='lb')
        li = LatticeInstance(id='li1', generator_name='zigzag',
                             path_a_id='la', path_b_id='lb',
                             params={'segments': 4})
        from model import GENERATORS
        result = li.generate(a, b, GENERATORS['zigzag'])
        assert all(p.source_id == 'li1' for p in result)


# ---------------------------------------------------------------------------
# PrintLayer
# ---------------------------------------------------------------------------

class TestPrintLayer:
    def _case_d_layer(self):
        """Recreates Case D geometry: wall perimeter + zigzag web."""
        perim = ExplicitPath(
            [Vec2(0,0),Vec2(25,0),Vec2(50,0),Vec2(75,0),Vec2(100,0),
             Vec2(100,10),Vec2(75,10),Vec2(50,10),Vec2(25,10),Vec2(0,10)],
            id='perim', closed=True, role='outer',
        )
        web = ExplicitPath(
            [Vec2(25,10),Vec2(25,0),Vec2(50,10),Vec2(50,0),Vec2(75,10),Vec2(75,0)],
            id='web', closed=False, role='lattice',
        )
        layer = PrintLayer(id='test', label='Case D')
        layer.source_paths = [perim, web]
        return layer

    def test_effective_paths_includes_sources(self):
        layer = self._case_d_layer()
        paths = layer.effective_paths()
        ids = [p.id for p in paths]
        assert 'perim' in ids
        assert 'web' in ids

    def test_offset_adds_derived_path(self):
        layer = self._case_d_layer()
        ot = OffsetTreatment(id='ot1', source_path_id='perim', distance=-2, role='inner')
        layer.offset_treatments.append(ot)
        paths = layer.effective_paths()
        assert len(paths) == 3  # 2 source + 1 derived

    def test_source_not_mutated_by_offset(self):
        layer = self._case_d_layer()
        original_len = len(layer.source_paths[0].sample_points())
        ot = OffsetTreatment(id='ot1', source_path_id='perim', distance=-2)
        layer.offset_treatments.append(ot)
        layer.effective_paths()
        assert len(layer.source_paths[0].sample_points()) == original_len

    def test_lattice_adds_derived_paths(self):
        layer = self._case_d_layer()
        li = LatticeInstance(id='li1', generator_name='zigzag',
                             path_a_id='perim', path_b_id='web',
                             params={'segments': 4, 'connect_ends': False})
        layer.lattice_instances.append(li)
        paths = layer.effective_paths()
        assert len(paths) > 2

    def test_to_routing_layer_returns_strands(self):
        layer = self._case_d_layer()
        rl = layer.to_routing_layer()
        assert len(rl.strands) == 2
        assert any(s.closed for s in rl.strands)
        assert any(not s.closed for s in rl.strands)

    def test_invisible_path_excluded(self):
        layer = self._case_d_layer()
        layer.source_paths[0].visible = False
        paths = layer.effective_paths()
        ids = [p.id for p in paths]
        assert 'perim' not in ids
        assert 'web' in ids

    def test_to_dict_roundtrip_keys(self):
        layer = self._case_d_layer()
        d = layer.to_dict()
        assert 'source_paths' in d
        assert 'offset_treatments' in d
        assert 'lattice_instances' in d
        assert 'constraints' in d


# ---------------------------------------------------------------------------
# TraversalConstraints
# ---------------------------------------------------------------------------

class TestTraversalConstraints:
    def test_defaults(self):
        tc = TraversalConstraints()
        assert tc.reverse_direction is False
        assert tc.start_path_id is None
        assert tc.component_order is None

    def test_to_dict(self):
        tc = TraversalConstraints(start_path_id='p1', start_t=0.25, reverse_direction=True)
        d = tc.to_dict()
        assert d['start_path_id'] == 'p1'
        assert d['start_t'] == pytest.approx(0.25)
        assert d['reverse_direction'] is True

    def test_constraints_separate_from_geometry(self):
        """Changing constraints must not alter source geometry."""
        layer = PrintLayer(id='test')
        path = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='p1')
        layer.source_paths.append(path)
        layer.constraints.reverse_direction = True
        layer.constraints.start_path_id = 'p1'
        # Source geometry unchanged
        assert layer.source_paths[0].points[0] == Vec2(0, 0)


# ---------------------------------------------------------------------------
# Offset geometry — true perpendicular distance verification
# ---------------------------------------------------------------------------

class TestOffsetGeometry:
    """Verify that _offset_polyline produces true constant-distance parallel offsets."""

    def test_rectangle_inside_offset_exact_corners(self):
        """100×100 closed rectangle offset inward by 10 → corners at (10,10),(90,10),(90,90),(10,90)."""
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        result = _offset_polyline(pts, 10, closed=True)
        assert len(result) == 4
        xs = sorted(p.x for p in result)
        ys = sorted(p.y for p in result)
        assert xs == pytest.approx([10, 10, 90, 90], abs=1e-9)
        assert ys == pytest.approx([10, 10, 90, 90], abs=1e-9)

    def test_rectangle_inside_offset_each_side_exactly_10in(self):
        """Every side of the offset rectangle is exactly 10 in from the source side."""
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 80), Vec2(0, 80)]
        result = _offset_polyline(pts, 10, closed=True)
        assert len(result) == 4
        # Verify corners — inner rect should be (10,10)(90,10)(90,70)(10,70)
        xs = sorted(p.x for p in result)
        ys = sorted(p.y for p in result)
        assert xs == pytest.approx([10, 10, 90, 90], abs=1e-9)
        assert ys == pytest.approx([10, 10, 70, 70], abs=1e-9)

    def test_rectangle_outside_offset_exact_corners(self):
        """100×100 rectangle offset outward (negative dist) by 10 → corners at (-10,-10) etc."""
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        result = _offset_polyline(pts, -10, closed=True)
        assert len(result) == 4
        xs = sorted(p.x for p in result)
        ys = sorted(p.y for p in result)
        assert xs == pytest.approx([-10, -10, 110, 110], abs=1e-9)
        assert ys == pytest.approx([-10, -10, 110, 110], abs=1e-9)

    def test_circle_inside_offset_reduces_radius(self):
        """Circle r=60, offset dist=10: all result points within 0.2 in of radius 50."""
        cp = CirclePath(200, 200, 60)
        ot = OffsetTreatment(id='ot', source_path_id='c', distance=10)
        derived = ot.generate(cp.sample_points(128), cp.closed)
        pts = derived.sample_points()
        for p in pts:
            r = math.hypot(p.x - 200, p.y - 200)
            assert abs(r - 50) < 0.2, f"Point radius {r:.4f} deviates from expected 50"

    def test_circle_outside_offset_increases_radius(self):
        """Circle r=60, offset dist=-10: all result points within 0.2 in of radius 70."""
        cp = CirclePath(0, 0, 60)
        ot = OffsetTreatment(id='ot', source_path_id='c', distance=-10)
        derived = ot.generate(cp.sample_points(128), cp.closed)
        pts = derived.sample_points()
        for p in pts:
            r = math.hypot(p.x, p.y)
            assert abs(r - 70) < 0.2, f"Point radius {r:.4f} deviates from expected 70"

    def test_open_polyline_left_offset_perpendicular(self):
        """Open horizontal segment: left offset shifts y by +dist."""
        pts = [Vec2(0, 50), Vec2(100, 50)]
        result = _offset_polyline(pts, 10, closed=False)
        assert len(result) == 2
        assert result[0].y == pytest.approx(60.0, abs=1e-9)
        assert result[1].y == pytest.approx(60.0, abs=1e-9)
        assert result[0].x == pytest.approx(0.0, abs=1e-9)
        assert result[1].x == pytest.approx(100.0, abs=1e-9)

    def test_open_polyline_right_offset(self):
        """Open horizontal segment: right offset (negative dist) shifts y by -dist."""
        pts = [Vec2(0, 50), Vec2(100, 50)]
        result = _offset_polyline(pts, -10, closed=False)
        assert len(result) == 2
        assert result[0].y == pytest.approx(40.0, abs=1e-9)
        assert result[1].y == pytest.approx(40.0, abs=1e-9)


class TestLatticeWithOffsetBoundary:
    """Verify that lattice can reference offset-derived paths as boundaries."""

    def test_lattice_can_use_offset_derived_path(self):
        """PrintLayer: source + offset + lattice where path_b_id = offset.id → lattice paths generated."""
        src = RectanglePath(0, 0, 100, 100, id='wall')
        ot = OffsetTreatment(id='inner', source_path_id='wall', distance=10)
        li = LatticeInstance(
            id='lat1', generator_name='zigzag',
            path_a_id='wall', path_b_id='inner',
            params={'segments': 4, 'connect_ends': False},
        )
        layer = PrintLayer(id='test')
        layer.source_paths = [src]
        layer.offset_treatments = [ot]
        layer.lattice_instances = [li]

        paths = layer.effective_paths()
        ids = {p.id for p in paths}
        # wall (source), inner (offset-derived), and at least one lattice path
        assert 'wall' in ids
        assert 'inner' in ids
        lattice_paths = [p for p in paths if p.role == 'lattice']
        assert len(lattice_paths) >= 1

    def test_offset_derived_id_is_treatment_id(self):
        """Derived path from OffsetTreatment must use the treatment's own id."""
        src = ExplicitPath([Vec2(0,0), Vec2(100,0), Vec2(100,50), Vec2(0,50)],
                           id='src', closed=True)
        ot = OffsetTreatment(id='my_offset_id', source_path_id='src', distance=5)
        derived = ot.generate(src.sample_points(128), src.closed)
        assert derived.id == 'my_offset_id'


# ---------------------------------------------------------------------------
# _resample — closed-path coverage
# ---------------------------------------------------------------------------

class TestResampleClosed:
    def test_open_path_unchanged_behavior(self):
        """Open path: n points from start to end inclusive."""
        pts = [Vec2(0, 0), Vec2(100, 0)]
        result = _resample(pts, 3, closed=False)
        assert len(result) == 3
        assert result[0].x == pytest.approx(0.0)
        assert result[2].x == pytest.approx(100.0)

    def test_closed_rectangle_full_perimeter(self):
        """Closed rectangle: _resample with closed=True covers all 4 sides."""
        # Rectangle perimeter = 400 units
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        # 5 evenly-spaced points → at 0, 100, 200, 300, 400 along perimeter
        result = _resample(pts, 5, closed=True)
        assert len(result) == 5
        # pt[0] at position 0 → (0,0)
        assert result[0].x == pytest.approx(0.0)
        assert result[0].y == pytest.approx(0.0)
        # pt[4] at position 400 = full perimeter = wrap-back to (0,0)
        assert result[4].x == pytest.approx(0.0)
        assert result[4].y == pytest.approx(0.0)
        # pt[1] at position 100 → (100,0) (end of first side)
        assert result[1].x == pytest.approx(100.0)
        assert result[1].y == pytest.approx(0.0)

    def test_closed_resample_covers_fourth_side(self):
        """Closed rectangle: without closed=True, 4th side is missed."""
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        # With closed=True and 9 samples, should have points at all 4 corners
        result = _resample(pts, 9, closed=True)
        xs = [round(p.x, 1) for p in result]
        ys = [round(p.y, 1) for p in result]
        # The 4th side (x=0, y going from 100→0) must be sampled
        has_fourth_side = any(x == pytest.approx(0.0, abs=1.0) and y < 99.0
                              for x, y in zip(xs, ys))
        assert has_fourth_side, "4th side not covered"

    def test_open_vs_closed_different_results(self):
        """Open and closed resampling of the same points produce different results."""
        pts = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        r_open = _resample(pts, 5, closed=False)
        r_closed = _resample(pts, 5, closed=True)
        # Closed version wraps back, open ends at (0,100)
        assert r_open[-1].x == pytest.approx(0.0)
        assert r_open[-1].y == pytest.approx(100.0)
        assert r_closed[-1].x == pytest.approx(0.0)
        assert r_closed[-1].y == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# ZigzagGenerator — closed loop coverage
# ---------------------------------------------------------------------------

class TestZigzagClosedLoop:
    def _concentric_rects(self):
        outer = RectanglePath(0, 0, 100, 100, id='outer')
        inner = RectanglePath(20, 20, 60, 60, id='inner')
        return outer, inner

    def _concentric_circles(self, r_outer=60, r_inner=40):
        outer = CirclePath(200, 200, r_outer, id='outer')
        inner = CirclePath(200, 200, r_inner, id='inner')
        return outer, inner

    def test_closed_rect_zigzag_has_all_four_sides(self):
        """Zigzag on closed rectangle must visit all 4 sides (not stop at 3)."""
        gen = ZigzagGenerator()
        outer, inner = self._concentric_rects()
        result = gen.generate(outer, inner, {'segments': 8, 'connect_ends': False}, 0)
        assert len(result) >= 1
        pts = result[0].sample_points()
        # All zigzag points that land on the outer boundary must cover all 4 sides.
        # Outer rect corners: (0,0),(100,0),(100,100),(0,100)
        # Expect at least one outer point on each side
        outer_pts = [pts[i] for i in range(0, len(pts), 2)]  # even = on outer A
        # Check we have points near each side
        near_bottom = any(p.y < 5 for p in outer_pts)
        near_right   = any(p.x > 95 for p in outer_pts)
        near_top     = any(p.y > 95 for p in outer_pts)
        near_left    = any(p.x < 5 for p in outer_pts)
        assert near_bottom, "No points on bottom side"
        assert near_right,  "No points on right side"
        assert near_top,    "No points on top side"
        assert near_left,   "No points on left side"

    def test_concentric_circles_zigzag_full_perimeter(self):
        """Zigzag on concentric circles covers full 360°."""
        gen = ZigzagGenerator()
        outer, inner = self._concentric_circles()
        result = gen.generate(outer, inner, {'segments': 12, 'connect_ends': False}, 0)
        pts = result[0].sample_points()
        outer_pts = [pts[i] for i in range(0, len(pts), 2)]
        # Convert to angles
        angles = [math.atan2(p.y - 200, p.x - 200) for p in outer_pts]
        angle_range = max(angles) - min(angles)
        # Full circle coverage: range of angles should span most of 2π
        assert angle_range > math.pi, f"Angle range only {math.degrees(angle_range):.1f}°"

    def test_zigzag_valid_in_cavity_concentric_circles(self):
        """Zigzag between concentric circles: all segments must stay in the cavity."""
        gen = ZigzagGenerator()
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        result = gen.generate(outer, inner, {'segments': 16, 'connect_ends': False}, 0)
        assert _lattice_valid_in_cavity(result, outer, inner), \
            "Zigzag segments leave the annular cavity"

    def test_zigzag_auto_increases_segments_when_invalid(self):
        """Low segment count on tight circles triggers auto-increase."""
        gen = ZigzagGenerator()
        # Very small gap between walls — low segments will produce chord through inner
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 55, id='inner')  # only 5 unit gap
        # With 2 segments, the diagonal chords will likely cut through inner circle
        result = gen.generate(outer, inner, {'segments': 2, 'connect_ends': False}, 0)
        # Regardless of what was requested, the result must be valid
        assert _lattice_valid_in_cavity(result, outer, inner), \
            "Auto-increased zigzag still invalid in tight cavity"

    def test_zigzag_rect_validity(self):
        """Zigzag between nested rectangles: all segments inside cavity."""
        gen = ZigzagGenerator()
        outer, inner = self._concentric_rects()
        result = gen.generate(outer, inner, {'segments': 10, 'connect_ends': False}, 0)
        assert _lattice_valid_in_cavity(result, outer, inner)


# ---------------------------------------------------------------------------
# WaveGenerator — closed-loop boundary contact
# ---------------------------------------------------------------------------

class TestWaveClosedLoop:
    def test_wave_concentric_circles_all_points_in_cavity(self):
        """Wave between concentric circles: all points in annular cavity."""
        gen = WaveGenerator()
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        result = gen.generate(outer, inner, {'cycles': 4.0, 'phase': 0.0}, 0)
        pts = result[0].sample_points()
        outer_pts = outer.sample_points(256)
        inner_pts = inner.sample_points(256)
        for p in pts:
            r = math.hypot(p.x - 200, p.y - 200)
            assert r <= 60.5, f"Point r={r:.3f} outside outer circle"
            assert r >= 39.5, f"Point r={r:.3f} inside inner circle"

    def test_wave_concentric_rects_all_points_in_cavity(self):
        """Wave between nested rectangles: all points in cavity (including boundary)."""
        gen = WaveGenerator()
        outer = RectanglePath(0, 0, 100, 100, id='outer')
        inner = RectanglePath(20, 20, 60, 60, id='inner')
        result = gen.generate(outer, inner, {'cycles': 3.0}, 0)
        pts = result[0].sample_points()
        # Wave touches boundaries (alpha=0 → on outer, alpha=1 → on inner).
        # Use axis-aligned bounds with small tolerance.
        EPS = 0.5
        for p in pts:
            assert outer.x - EPS <= p.x <= outer.x + outer.w + EPS, \
                f"Point x={p.x:.2f} outside outer rect x-bounds"
            assert outer.y - EPS <= p.y <= outer.y + outer.h + EPS, \
                f"Point y={p.y:.2f} outside outer rect y-bounds"
            # Should not be strictly inside the inner rect (shrunk by EPS)
            strictly_inside_inner = (
                inner.x + EPS < p.x < inner.x + inner.w - EPS and
                inner.y + EPS < p.y < inner.y + inner.h - EPS
            )
            assert not strictly_inside_inner, \
                f"Point ({p.x:.1f},{p.y:.1f}) strictly inside inner rect"

    def test_wave_both_variations_valid(self):
        """Both wave variations must produce geometrically valid paths."""
        gen = WaveGenerator()
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        for vi in [0, 1]:
            result = gen.generate(outer, inner, {'cycles': 3.0}, vi)
            pts = result[0].sample_points()
            for p in pts:
                r = math.hypot(p.x - 200, p.y - 200)
                assert r <= 60.5 and r >= 39.5, \
                    f"V{vi+1} point r={r:.3f} outside cavity"


# ---------------------------------------------------------------------------
# Polygon helpers
# ---------------------------------------------------------------------------

class TestPolygonHelpers:
    def test_polygon_area_square(self):
        pts = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)]
        assert abs(_polygon_area(pts)) == pytest.approx(100.0)

    def test_point_in_polygon_inside(self):
        square = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        assert _point_in_polygon(Vec2(50, 50), square) is True

    def test_point_in_polygon_outside(self):
        square = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]
        assert _point_in_polygon(Vec2(150, 50), square) is False

    def test_point_in_polygon_circle_approx(self):
        """Circle approximated as 64-gon: test inside/outside."""
        cp = CirclePath(0, 0, 50)
        poly = cp.sample_points(64)
        assert _point_in_polygon(Vec2(0, 0), poly) is True
        assert _point_in_polygon(Vec2(60, 0), poly) is False


# ---------------------------------------------------------------------------
# Integration — route Case D geometry via routing engine
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# WaveGenerator — seam bridge (closed boundaries → zero travel)
# ---------------------------------------------------------------------------

class TestWaveSeamBridge:
    def test_closed_boundaries_produce_two_paths(self):
        """Wave on closed boundaries returns wave + seam bridge."""
        gen = WaveGenerator()
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        result = gen.generate(outer, inner, {'cycles': 3.0}, 0)
        assert len(result) == 2
        labels = [dp.label for dp in result]
        assert 'wave' in labels
        assert 'wave_seam' in labels

    def test_open_boundaries_produce_one_path(self):
        """Wave on open boundaries returns only the wave (no seam bridge)."""
        gen = WaveGenerator()
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='a')
        b = ExplicitPath([Vec2(0, 30), Vec2(100, 30)], id='b')
        result = gen.generate(a, b, {'cycles': 3.0}, 0)
        assert len(result) == 1

    def test_seam_bridge_v1_endpoint_matches_path_b_start(self):
        """V1 seam bridge end = path_b.sample_points()[0] exactly."""
        gen = WaveGenerator()
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        result = gen.generate(outer, inner, {'cycles': 3.0}, 0)
        bridge = next(dp for dp in result if dp.label == 'wave_seam')
        bridge_pts = bridge.sample_points()
        expected = inner.sample_points()[0]
        assert bridge_pts[-1].x == pytest.approx(expected.x, abs=1e-9)
        assert bridge_pts[-1].y == pytest.approx(expected.y, abs=1e-9)

    def test_seam_bridge_v2_endpoint_matches_path_a_start(self):
        """V2 seam bridge end = path_a.sample_points()[0] exactly."""
        gen = WaveGenerator()
        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        result = gen.generate(outer, inner, {'cycles': 3.0}, 1)
        bridge = next(dp for dp in result if dp.label == 'wave_seam')
        bridge_pts = bridge.sample_points()
        expected = outer.sample_points()[0]
        assert bridge_pts[-1].x == pytest.approx(expected.x, abs=1e-9)
        assert bridge_pts[-1].y == pytest.approx(expected.y, abs=1e-9)

    def test_wave_closed_zero_travel(self):
        """Wave on concentric circles routes with zero travel moves."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics

        outer = CirclePath(200, 200, 60, id='outer')
        inner = CirclePath(200, 200, 40, id='inner')
        li = LatticeInstance(
            id='wli', generator_name='wave',
            path_a_id='outer', path_b_id='inner',
            params={'cycles': 3.0}, variation_index=0,
        )
        pl = PrintLayer(id='wave_test')
        pl.source_paths = [outer, inner]
        pl.lattice_instances = [li]
        rl = pl.to_routing_layer()
        moves = route_layer(rl)
        metrics = compute_metrics(moves)
        assert metrics['travel_moves'] == 0, \
            f"Wave on closed circles should have 0 travel, got {metrics['travel_moves']}"
        assert metrics['print_runs'] == 1, \
            f"Wave on closed circles should have 1 run, got {metrics['print_runs']}"


# ---------------------------------------------------------------------------
# Open wall end caps
# ---------------------------------------------------------------------------

class TestOpenWallEndCaps:
    def test_open_source_offset_generates_caps(self):
        """Open source + offset generates cap_start and cap_end paths."""
        src = LinePath(Vec2(0, 0), Vec2(100, 0), id='line')
        ot = OffsetTreatment(id='ot1', source_path_id='line', distance=10)
        layer = PrintLayer(id='test')
        layer.source_paths = [src]
        layer.offset_treatments = [ot]
        paths = layer.effective_paths()
        cap_roles = [p.role for p in paths if p.role == 'cap']
        assert len(cap_roles) == 2

    def test_closed_source_offset_no_caps(self):
        """Closed source + offset does NOT generate caps."""
        src = RectanglePath(0, 0, 100, 100, id='rect')
        ot = OffsetTreatment(id='ot1', source_path_id='rect', distance=10)
        layer = PrintLayer(id='test')
        layer.source_paths = [src]
        layer.offset_treatments = [ot]
        paths = layer.effective_paths()
        cap_paths = [p for p in paths if p.role == 'cap']
        assert len(cap_paths) == 0

    def test_cap_start_endpoints_match_boundaries(self):
        """cap_start spans outermost → innermost wall endpoint exactly."""
        src = LinePath(Vec2(0, 0), Vec2(100, 0), id='line')
        ot = OffsetTreatment(id='ot1', source_path_id='line', distance=10)
        layer = PrintLayer(id='test')
        layer.source_paths = [src]
        layer.offset_treatments = [ot]
        paths = layer.effective_paths()
        cap_start = next(p for p in paths if p.label == 'cap_start')
        pts = cap_start.sample_points()
        # Wall endpoints at the start are {src[0]=(0,0), derived[0]=(0,10)}.
        # Cap polyline must contain both as exact endpoints (in some order).
        endpts = {(pts[0].x, pts[0].y), (pts[-1].x, pts[-1].y)}
        assert (0.0, 0.0) in endpts
        assert any(abs(e[0]) < 1e-9 and abs(e[1] - 10.0) < 1e-9 for e in endpts)

    def test_open_double_wall_routes_single_run_zero_travel(self):
        """Open source + offset + auto caps → routing gives 1 run, 0 travel."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics

        src = LinePath(Vec2(0, 50), Vec2(200, 50), id='line')
        ot = OffsetTreatment(id='ot1', source_path_id='line', distance=20)
        layer = PrintLayer(id='test')
        layer.source_paths = [src]
        layer.offset_treatments = [ot]
        rl = layer.to_routing_layer()
        moves = route_layer(rl)
        metrics = compute_metrics(moves)
        assert metrics['travel_moves'] == 0
        assert metrics['print_runs'] == 1


# ---------------------------------------------------------------------------
# QuadBezierPath
# ---------------------------------------------------------------------------

class TestQuadBezierPath:
    def _curve(self):
        return QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 80), id='qb')

    def test_preserves_parameters(self):
        p = self._curve()
        assert p.start.x == pytest.approx(0.0)
        assert p.start.y == pytest.approx(0.0)
        assert p.end.x == pytest.approx(100.0)
        assert p.end.y == pytest.approx(0.0)
        assert p.control.x == pytest.approx(50.0)
        assert p.control.y == pytest.approx(80.0)
        assert p.__class__.__name__ == 'QuadBezierPath'

    def test_sample_starts_at_start_ends_at_end(self):
        p = QuadBezierPath(Vec2(10, 20), Vec2(90, 30), Vec2(50, 80))
        pts = p.sample_points()
        assert pts[0].x == pytest.approx(10.0, abs=1e-9)
        assert pts[0].y == pytest.approx(20.0, abs=1e-9)
        assert pts[-1].x == pytest.approx(90.0, abs=1e-9)
        assert pts[-1].y == pytest.approx(30.0, abs=1e-9)

    def test_straight_aligned_control_near_straight(self):
        """Control at midpoint on the segment → all y values ≈ 0."""
        p = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 0))
        for pt in p.sample_points():
            assert abs(pt.y) < 1e-9

    def test_curve_length_exceeds_chord(self):
        """A bent curve must be longer than the straight chord."""
        p = self._curve()
        chord = math.hypot(100, 0)
        assert p.arc_length() > chord

    def test_is_open_by_default(self):
        assert self._curve().closed is False

    def test_sample_count_matches_n(self):
        p = self._curve()
        assert len(p.sample_points(32)) == 32
        assert len(p.sample_points(128)) == 128

    def test_offset_left_shifts_positively(self):
        """Horizontal bezier (control on line) offset left → y increases."""
        p = QuadBezierPath(Vec2(0, 50), Vec2(100, 50), Vec2(50, 50), id='crv')
        ot = OffsetTreatment(id='ot1', source_path_id='crv', distance=10)
        derived = ot.generate(p.sample_points(128), p.closed)
        for pt in derived.sample_points():
            assert pt.y > 50 - 0.5  # all points shifted upward

    def test_left_right_offsets_opposite_sides(self):
        """Left (positive) and right (negative) offsets land on opposite sides."""
        p = QuadBezierPath(Vec2(0, 50), Vec2(100, 50), Vec2(50, 50), id='crv')
        ot_left  = OffsetTreatment(id='otl', source_path_id='crv', distance=+10)
        ot_right = OffsetTreatment(id='otr', source_path_id='crv', distance=-10)
        left_pts  = ot_left.generate(p.sample_points(128), p.closed).sample_points()
        right_pts = ot_right.generate(p.sample_points(128), p.closed).sample_points()
        assert all(pt.y > 50 for pt in left_pts)
        assert all(pt.y < 50 for pt in right_pts)

    def test_curve_offset_generates_caps(self):
        """Open curve + offset → 2 cap paths."""
        crv = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 50), id='crv')
        ot = OffsetTreatment(id='ot1', source_path_id='crv', distance=10)
        layer = PrintLayer(id='test')
        layer.source_paths = [crv]
        layer.offset_treatments = [ot]
        paths = layer.effective_paths()
        caps = [p for p in paths if p.role == 'cap']
        assert len(caps) == 2

    def test_curve_offset_routes_zero_travel(self):
        """Curve + offset + auto-caps → 1 run, 0 travel moves."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        crv = QuadBezierPath(Vec2(0, 50), Vec2(200, 50), Vec2(100, 80), id='crv')
        ot = OffsetTreatment(id='ot1', source_path_id='crv', distance=15)
        layer = PrintLayer(id='test')
        layer.source_paths = [crv]
        layer.offset_treatments = [ot]
        rl = layer.to_routing_layer()
        moves = route_layer(rl)
        metrics = compute_metrics(moves)
        assert metrics['travel_moves'] == 0
        assert metrics['print_runs'] == 1

    def test_zigzag_between_curve_and_offset(self):
        """Zigzag lattice can be placed between a curve and its offset."""
        crv = QuadBezierPath(Vec2(0, 50), Vec2(200, 50), Vec2(100, 80), id='crv')
        ot = OffsetTreatment(id='ot1', source_path_id='crv', distance=20)
        li = LatticeInstance(id='li1', generator_name='zigzag',
                             path_a_id='crv', path_b_id='ot1',
                             params={'segments': 6, 'connect_ends': False})
        layer = PrintLayer(id='test')
        layer.source_paths = [crv]
        layer.offset_treatments = [ot]
        layer.lattice_instances = [li]
        paths = layer.effective_paths()
        assert len([p for p in paths if p.role == 'lattice']) >= 1

    def test_wave_between_curve_and_offset(self):
        """Wave lattice can be placed between a curve and its offset."""
        crv = QuadBezierPath(Vec2(0, 50), Vec2(200, 50), Vec2(100, 80), id='crv')
        ot = OffsetTreatment(id='ot1', source_path_id='crv', distance=20)
        li = LatticeInstance(id='li1', generator_name='wave',
                             path_a_id='crv', path_b_id='ot1',
                             params={'cycles': 3.0})
        layer = PrintLayer(id='test')
        layer.source_paths = [crv]
        layer.offset_treatments = [ot]
        layer.lattice_instances = [li]
        paths = layer.effective_paths()
        assert len([p for p in paths if p.role == 'lattice']) >= 1

    def test_delete_cascade_removes_dependents(self):
        """Removing curve from layer removes dependent offset and lattice."""
        crv = QuadBezierPath(Vec2(0, 50), Vec2(200, 50), Vec2(100, 80), id='crv')
        ot = OffsetTreatment(id='ot1', source_path_id='crv', distance=20)
        li = LatticeInstance(id='li1', generator_name='zigzag',
                             path_a_id='crv', path_b_id='ot1',
                             params={'segments': 4})
        layer = PrintLayer(id='test')
        layer.source_paths = [crv]
        layer.offset_treatments = [ot]
        layer.lattice_instances = [li]
        # Simulate cascade delete
        layer.source_paths = [p for p in layer.source_paths if p.id != 'crv']
        layer.offset_treatments = [o for o in layer.offset_treatments
                                    if o.source_path_id != 'crv']
        layer.lattice_instances = [l for l in layer.lattice_instances
                                    if l.path_a_id not in ('crv', 'ot1')
                                    and l.path_b_id not in ('crv', 'ot1')]
        assert layer.effective_paths() == []

    def test_to_dict_preserves_type(self):
        """to_dict must report type as 'QuadBezierPath', not 'Path' or 'ExplicitPath'."""
        p = self._curve()
        d = p.to_dict()
        assert d['type'] == 'QuadBezierPath'
        assert d['start'] == [0, 0]
        assert d['end'] == [100, 0]
        assert d['control'] == [50, 80]


# ---------------------------------------------------------------------------
# Curve chord dimensions
# ---------------------------------------------------------------------------

class TestCurveChord:
    def test_chord_equals_euclidean(self):
        """Chord length is simply the straight-line distance between endpoints."""
        p = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 80))
        chord = math.hypot(p.end.x - p.start.x, p.end.y - p.start.y)
        assert chord == pytest.approx(100.0)

    def test_chord_independent_of_control(self):
        """Moving the bend/control point changes curve length but NOT chord length."""
        p1 = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 10))
        p2 = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 200))
        chord1 = math.hypot(p1.end.x - p1.start.x, p1.end.y - p1.start.y)
        chord2 = math.hypot(p2.end.x - p2.start.x, p2.end.y - p2.start.y)
        assert chord1 == pytest.approx(chord2)
        assert chord1 == pytest.approx(100.0)
        assert p2.arc_length() > p1.arc_length() + 10.0  # obvious difference

    def test_chord_not_in_effective_geometry(self):
        """Chord is a display-only dimension — never appears as effective/routing geometry."""
        p = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 80), id='qb')
        layer = PrintLayer(id='t', source_paths=[p])
        paths = layer.effective_paths()
        assert len(paths) == 1
        pts = paths[0].sample_points()
        # If chord were present as a path, it would be a 2-point straight line
        assert len(pts) > 2


# ---------------------------------------------------------------------------
# Corner rounding (global fillets)
# ---------------------------------------------------------------------------

class TestCornerRounding:
    def _rect_pts(self):
        return [Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)]

    def test_zero_radius_rectangle_unchanged(self):
        """corner_radius = 0 leaves the raw 4-corner polyline alone."""
        pts = _apply_corner_rounding(self._rect_pts(), 0.0, closed=True)
        assert len(pts) == 4

    def test_nonzero_radius_rectangle_more_points(self):
        pts = _apply_corner_rounding(self._rect_pts(), 10.0, closed=True)
        assert len(pts) > 4

    def test_fillet_radius_approximate(self):
        """Each 90-degree fillet arc has points at ~radius from its arc center."""
        pts = _apply_corner_rounding(self._rect_pts(), 10.0, closed=True)
        # Bottom-left corner's fillet center is at (10, 10). Pick points near it.
        bl_arc_pts = [p for p in pts if p.x < 15 and p.y < 15]
        assert len(bl_arc_pts) >= 2
        for p in bl_arc_pts:
            r = math.hypot(p.x - 10, p.y - 10)
            assert abs(r - 10.0) < 0.3, f"Fillet pt {p} radius {r:.3f} != 10"

    def test_fillet_tangent_points_on_segment(self):
        """Fillet tangent points lie exactly on the adjacent straight segments."""
        pts = _apply_corner_rounding(self._rect_pts(), 10.0, closed=True)
        # Points on the bottom edge y=0 should have 10 <= x <= 90 (within tangent range)
        bottom_pts = [p for p in pts if abs(p.y) < 0.01]
        for p in bottom_pts:
            assert 9.9 <= p.x <= 90.1, f"Bottom tangent {p} outside expected range"

    def test_excessive_radius_clamped(self):
        """R larger than half any adjacent segment is clamped, not degenerate."""
        # 20x20 rectangle; R=50 would overrun — must clamp to 10 (segment/2)
        small = [Vec2(0, 0), Vec2(20, 0), Vec2(20, 20), Vec2(0, 20)]
        pts = _apply_corner_rounding(small, 50.0, closed=True)
        assert len(pts) >= 4
        # All points stay inside the 20x20 bounding box (within tiny float tolerance)
        for p in pts:
            assert -0.1 <= p.x <= 20.1, f"Clamped-fillet pt {p} x out of range"
            assert -0.1 <= p.y <= 20.1, f"Clamped-fillet pt {p} y out of range"

    def test_open_polyline_endpoints_preserved(self):
        """Open polyline rounding keeps the first/last points unchanged."""
        src = [Vec2(0, 0), Vec2(50, 50), Vec2(100, 0)]
        pts = _apply_corner_rounding(src, 10.0, closed=False)
        assert pts[0].x == 0 and pts[0].y == 0
        assert pts[-1].x == 100 and pts[-1].y == 0

    def test_closed_polygon_rounds_all_corners(self):
        """Every vertex of a closed polygon receives a fillet arc."""
        n = 5
        pentagon = [Vec2(100 * math.cos(2 * math.pi * i / n),
                        100 * math.sin(2 * math.pi * i / n)) for i in range(n)]
        pts = _apply_corner_rounding(pentagon, 10.0, closed=True)
        # Each corner should produce ≥3 arc samples (~5°/sample for a 108° interior fillet)
        assert len(pts) > n * 3

    def test_rounded_geometry_continuous(self):
        """
        Rounded polyline is a single continuous ribbon. Within arcs, points are
        densely spaced (<= radius). Between arcs are straight segments represented
        by two tangent points — those gaps match the straight-edge length.
        No segment should exceed the raw straight edge length of the source.
        """
        pts = _apply_corner_rounding(self._rect_pts(), 10.0, closed=True)
        # Longest raw edge of a 100x100 rectangle is 100 inches.
        for a, b in zip(pts, pts[1:]):
            assert a.dist(b) <= 100.0 + 1e-6, \
                f"Gap {a.dist(b):.2f} between {a} and {b} exceeds edge length"
        # And every pair must have a non-zero joint — no coincident consecutive duplicates
        for a, b in zip(pts, pts[1:]):
            assert a.dist(b) > 1e-9, "Duplicate consecutive point in rounded polyline"

    def test_rounded_rect_offset_approx_constant_spacing(self):
        """
        Rounded rect + offset: every offset sample point lies within |D| ± tol
        of the nearest point on the rounded source. Verifies perpendicular wall
        spacing is approximately preserved under corner rounding.
        """
        rect = RectanglePath(0, 0, 120, 120, id='r')
        ot = OffsetTreatment(id='o', source_path_id='r', distance=10, role='inner')
        layer = PrintLayer(id='t', source_paths=[rect],
                           offset_treatments=[ot], corner_radius=20.0)
        paths = layer.effective_paths()
        rect_pts = next(p for p in paths if p.id == 'r').sample_points()
        off_pts  = next(p for p in paths if p.id == 'o').sample_points()
        assert rect_pts and off_pts
        for op in off_pts[::4]:
            min_d = min(op.dist(rp) for rp in rect_pts)
            assert abs(min_d - 10.0) < 2.0, f"Wall spacing {min_d:.2f} != 10 at {op}"


# ---------------------------------------------------------------------------
# Open double-wall end-cap styles
# ---------------------------------------------------------------------------

class TestCapStyles:
    def _straight_wall_layer(self, cap_style='flat'):
        line = LinePath(Vec2(0, 50), Vec2(200, 50), id='L')
        ot = OffsetTreatment(id='O', source_path_id='L', distance=20, role='inner')
        return PrintLayer(id='t', source_paths=[line],
                          offset_treatments=[ot], cap_style=cap_style)

    def test_flat_cap_is_two_points(self):
        paths = self._straight_wall_layer('flat').effective_paths()
        caps = [p for p in paths if p.role == 'cap']
        assert len(caps) == 2
        for cap in caps:
            assert len(cap.sample_points()) == 2

    def test_round_cap_has_many_points(self):
        paths = self._straight_wall_layer('round').effective_paths()
        caps = [p for p in paths if p.role == 'cap']
        assert len(caps) == 2
        for cap in caps:
            assert len(cap.sample_points()) > 10

    def test_round_cap_radius_half_wall_spacing(self):
        """Round cap has radius equal to half the perpendicular wall distance."""
        paths = self._straight_wall_layer('round').effective_paths()
        start_cap = next(p for p in paths if p.label == 'cap_start')
        pts = start_cap.sample_points()
        c = pts[0].lerp(pts[-1], 0.5)
        R = pts[0].dist(c)
        assert R == pytest.approx(10.0, abs=0.01)  # wall distance = 20, radius = 10
        for p in pts:
            assert p.dist(c) == pytest.approx(R, abs=0.1)

    def test_round_cap_tangential_endpoints(self):
        """Full-round cap's endpoints match the two extreme wall endpoints exactly."""
        line = LinePath(Vec2(0, 50), Vec2(200, 50), id='L')
        ot = OffsetTreatment(id='O', source_path_id='L', distance=20)
        layer = PrintLayer(id='t', source_paths=[line],
                           offset_treatments=[ot], cap_style='full_round')
        paths = layer.effective_paths()
        src_first = line.sample_points()[0]
        der_first = next(p for p in paths if p.id == 'O').sample_points()[0]
        start_cap = next(p for p in paths if p.label == 'cap_start').sample_points()
        endpts = {(round(start_cap[0].x, 6), round(start_cap[0].y, 6)),
                  (round(start_cap[-1].x, 6), round(start_cap[-1].y, 6))}
        assert (round(src_first.x, 6), round(src_first.y, 6)) in endpts
        assert (round(der_first.x, 6), round(der_first.y, 6)) in endpts

    def test_straight_wall_round_caps_zero_travel(self):
        """Straight open wall + offset + round caps = 1 run, 0 travel."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        layer = self._straight_wall_layer('round')
        moves = route_layer(layer.to_routing_layer())
        m = compute_metrics(moves)
        assert m['travel_moves'] == 0
        assert m['print_runs'] == 1

    def test_quadbezier_round_caps_zero_travel(self):
        """Curved open wall + offset + round caps = 1 run, 0 travel."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        qb = QuadBezierPath(Vec2(0, 100), Vec2(200, 100), Vec2(100, 180), id='qb')
        ot = OffsetTreatment(id='O', source_path_id='qb', distance=20)
        layer = PrintLayer(id='t', source_paths=[qb],
                           offset_treatments=[ot], cap_style='round')
        moves = route_layer(layer.to_routing_layer())
        m = compute_metrics(moves)
        assert m['travel_moves'] == 0
        assert m['print_runs'] == 1

    def test_reverse_still_zero_travel(self):
        """Reversed route of a round-cap wall also has zero travel."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        layer = self._straight_wall_layer('round')
        moves = route_layer(layer.to_routing_layer())
        # Simulate api_route's reverse post-processing
        from geometry import PrintMove as _PM
        reversed_moves = [
            _PM(kind=m.kind, strand_id=m.strand_id, seg_idx=m.seg_idx,
                start=m.end, end=m.start)
            for m in reversed(moves)
        ]
        rm = compute_metrics(reversed_moves)
        assert rm['travel_moves'] == 0


# ---------------------------------------------------------------------------
# Lattice interaction with rounded cavity
# ---------------------------------------------------------------------------

class TestLatticeWithRounding:
    def test_lattice_inside_rounded_cavity(self):
        """Zigzag lattice between rounded source + inner offset still generates paths."""
        rect = RectanglePath(0, 0, 160, 160, id='r', role='outer')
        ot = OffsetTreatment(id='in', source_path_id='r', distance=20, role='inner')
        li = LatticeInstance(id='L', generator_name='zigzag',
                             path_a_id='r', path_b_id='in',
                             params={'segments': 8, 'connect_ends': False})
        layer = PrintLayer(id='t', source_paths=[rect],
                           offset_treatments=[ot], lattice_instances=[li],
                           corner_radius=20.0)
        paths = layer.effective_paths()
        lattice = [p for p in paths if p.role == 'lattice']
        assert len(lattice) >= 1

    def test_lattice_contained_in_rounded_cavity(self):
        """
        Lattice between rounded outer and inner must be valid in the cavity:
        every segment midpoint inside outer, outside inner, and no crossing of
        either rounded boundary. Uses _lattice_valid_in_cavity (same rule the
        zigzag auto-increase enforces) so boundary-touching endpoints are OK.
        """
        rect = RectanglePath(0, 0, 160, 160, id='r', role='outer')
        ot = OffsetTreatment(id='in', source_path_id='r', distance=20, role='inner')
        li = LatticeInstance(id='L', generator_name='zigzag',
                             path_a_id='r', path_b_id='in',
                             params={'segments': 6, 'connect_ends': False})
        layer = PrintLayer(id='t', source_paths=[rect],
                           offset_treatments=[ot], lattice_instances=[li],
                           corner_radius=20.0)
        paths = layer.effective_paths()
        outer = next(p for p in paths if p.id == 'r')
        inner = next(p for p in paths if p.id == 'in')
        lattice = [p for p in paths if p.role == 'lattice']
        assert _lattice_valid_in_cavity(lattice, outer, inner)


# ---------------------------------------------------------------------------
# Geometry correctness refactor pass — issues 1/2/3
# ---------------------------------------------------------------------------

def _min_dist_to_polyline(pt, poly, closed):
    """Shortest distance from pt to any segment of poly (treating closed if needed)."""
    best = float('inf')
    n = len(poly)
    for i in range(n - 1):
        a, b = poly[i], poly[i + 1]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        if L2 < 1e-18:
            d = pt.dist(a)
        else:
            t = ((pt.x - a.x) * dx + (pt.y - a.y) * dy) / L2
            tc = max(0, min(1, t))
            fx, fy = a.x + tc * dx, a.y + tc * dy
            d = math.hypot(pt.x - fx, pt.y - fy)
        if d < best:
            best = d
    if closed and n >= 2:
        a, b = poly[-1], poly[0]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        if L2 >= 1e-18:
            t = ((pt.x - a.x) * dx + (pt.y - a.y) * dy) / L2
            tc = max(0, min(1, t))
            fx, fy = a.x + tc * dx, a.y + tc * dy
            d = math.hypot(pt.x - fx, pt.y - fy)
            if d < best:
                best = d
    return best


class TestAcuteWallSpacing:
    """Issue 3: offsets must not silently thin walls at acute corners."""

    def test_15deg_v_outside_offset_preserves_spacing(self):
        """At a sharp 15° V, outside offset must stay at |D| from every wall point."""
        tip = Vec2(100, 50)
        left  = Vec2(0, 50 + 100 * math.tan(math.radians(7.5)))
        right = Vec2(0, 50 - 100 * math.tan(math.radians(7.5)))
        src = [left, tip, right]
        offset = _offset_polyline(src, -10.0, closed=False)
        offset = _trim_offset(offset, src, -10.0, closed=False)
        # Every offset point perpendicular to a wall segment must be exactly 10 ± tol
        for p in offset:
            d = _min_dist_to_polyline(p, src, closed=False)
            assert d >= 10.0 - 0.05, \
                f"Wall thinned to {d:.3f} < 10 (acute-corner bevel was triggered)"

    def test_30deg_v_outside_offset_no_bevel(self):
        """At a 30° V, offset result must have exactly 3 vertices (no bevel = no 4th)."""
        tip = Vec2(100, 50)
        left  = Vec2(0, 50 + 100 * math.tan(math.radians(15)))
        right = Vec2(0, 50 - 100 * math.tan(math.radians(15)))
        src = [left, tip, right]
        offset = _offset_polyline(src, -10.0, closed=False)
        # 3 original vertices → 3 offset vertices (no bevel split)
        assert len(offset) == 3, f"Expected 3 vertices (true miter), got {len(offset)}"

    def test_acute_rectangle_offset_wall_spacing(self):
        """Thin acute triangle outside-offset preserves wall spacing on all pts."""
        tri = [Vec2(0, 0), Vec2(100, 2), Vec2(0, 4)]  # very thin triangle
        offset = _offset_polyline(tri, -5.0, closed=True)
        offset = _trim_offset(offset, tri, -5.0, closed=True)
        for p in offset:
            d = _min_dist_to_polyline(p, tri, closed=True)
            assert d >= 5.0 - 0.1, f"Wall thinned to {d:.3f} < 5 at thin-triangle corner"


class TestRoundedSourceOffsetParity:
    """
    Issue 1: inside/outside/both offsets must all be true parallel offsets of
    the SAME processed source geometry.
    """

    def test_processed_source_pts_is_canonical(self):
        """_processed_source_pts returns the same result for every call."""
        rect = RectanglePath(0, 0, 120, 120)
        a = _processed_source_pts(rect, 20.0)
        b = _processed_source_pts(rect, 20.0)
        assert len(a) == len(b)
        for pa, pb in zip(a, b):
            assert pa.x == pb.x and pa.y == pb.y

    def test_inside_and_outside_derive_from_same_source(self):
        """Multiple offsets of the same source share the identical processed polyline."""
        rect = RectanglePath(0, 0, 120, 120, id='r')
        ot_in  = OffsetTreatment(id='in',  source_path_id='r', distance=+10)
        ot_out = OffsetTreatment(id='out', source_path_id='r', distance=-10)
        layer = PrintLayer(id='t', source_paths=[rect],
                           offset_treatments=[ot_in, ot_out],
                           corner_radius=20.0)
        paths = layer.effective_paths()
        source = next(p for p in paths if p.id == 'r')
        inner  = next(p for p in paths if p.id == 'in')
        outer  = next(p for p in paths if p.id == 'out')
        src_pts = source.sample_points()
        # Inner points: each must be within |D| ± 0.6 of the source polyline.
        for p in inner.sample_points():
            d = _min_dist_to_polyline(p, src_pts, closed=True)
            assert abs(d - 10.0) < 0.6, f"Inner wall off source by {d:.3f} (should be 10)"
        for p in outer.sample_points():
            d = _min_dist_to_polyline(p, src_pts, closed=True)
            assert abs(d - 10.0) < 0.6, f"Outer wall off source by {d:.3f} (should be 10)"

    def test_rounded_rectangle_inside_offset_is_concentric(self):
        """Inside offset of rounded rect: offset arc radius ≈ R - D."""
        rect = RectanglePath(0, 0, 200, 200, id='r')
        ot = OffsetTreatment(id='in', source_path_id='r', distance=+10)
        layer = PrintLayer(id='t', source_paths=[rect], offset_treatments=[ot],
                           corner_radius=30.0)
        paths = layer.effective_paths()
        inner = next(p for p in paths if p.id == 'in')
        # BL arc center at (30, 30); inside-offset arc expected radius = 30 - 10 = 20.
        bl_pts = [p for p in inner.sample_points() if p.x < 40 and p.y < 40]
        assert len(bl_pts) >= 3, "No arc samples captured at BL corner"
        for p in bl_pts:
            r = math.hypot(p.x - 30, p.y - 30)
            assert abs(r - 20.0) < 0.5, f"Inner-arc radius {r:.3f} != R - D = 20"

    def test_rounded_rectangle_outside_offset_is_concentric(self):
        """Outside offset of rounded rect: offset arc radius ≈ R + D."""
        rect = RectanglePath(0, 0, 200, 200, id='r')
        ot = OffsetTreatment(id='out', source_path_id='r', distance=-10)
        layer = PrintLayer(id='t', source_paths=[rect], offset_treatments=[ot],
                           corner_radius=30.0)
        paths = layer.effective_paths()
        outer = next(p for p in paths if p.id == 'out')
        bl_pts = [p for p in outer.sample_points() if p.x < 40 and p.y < 40]
        assert len(bl_pts) >= 3
        for p in bl_pts:
            r = math.hypot(p.x - 30, p.y - 30)
            assert abs(r - 40.0) < 0.5, f"Outer-arc radius {r:.3f} != R + D = 40"

    def test_concave_shape_rounds_in_correct_direction(self):
        """
        L-shape (CCW) with a reflex vertex at (100,100). The fillet there must
        be tangent to both adjacent segments; the arc "fills the notch"
        (correct CAD convention for a concave fillet). Specifically: tangent
        points lie on the actual segments and arc samples are at ~radius
        from the arc center (verifies the fillet is a true circular arc).
        """
        L = [Vec2(0, 0), Vec2(200, 0), Vec2(200, 200),
             Vec2(100, 200), Vec2(100, 100), Vec2(0, 100)]
        R = 10.0
        rounded = _apply_corner_rounding(L, R, closed=True)
        # Collect points near the reflex vertex (within 2R of (100,100)).
        reflex_region = [p for p in rounded
                         if math.hypot(p.x - 100, p.y - 100) < 2 * R]
        # Must have multiple arc samples at the reflex corner.
        assert len(reflex_region) > 3
        # Reflex-fillet arc center is at (90, 110) for this L-shape
        # (bisector of up+left, R away from vertex / sin(45°)).
        # Every point on the arc portion must be at radius ≈ R from (90, 110).
        arc_samples = [p for p in reflex_region
                       if abs(math.hypot(p.x - 90, p.y - 110) - R) < 0.5]
        assert len(arc_samples) >= 3, \
            "Fillet at reflex vertex did not produce a proper arc"


class TestWallSystemCaps:
    """Issue 2: wall system end treatment — ONE cap per end, spanning all walls."""

    def _3wall_payload(self, cap_style='flat', cap_corner_radius=0.0):
        src = LinePath(Vec2(0, 50), Vec2(200, 50), id='L')
        ot_out = OffsetTreatment(id='O_out', source_path_id='L', distance=+15, role='outer')
        ot_in  = OffsetTreatment(id='O_in',  source_path_id='L', distance=-15, role='inner')
        return PrintLayer(id='t', source_paths=[src],
                          offset_treatments=[ot_in, ot_out],
                          cap_style=cap_style,
                          cap_corner_radius=cap_corner_radius)

    def test_three_wall_system_gets_one_cap_per_end(self):
        """Source + 2 offsets must produce exactly 2 cap paths total (one per end)."""
        paths = self._3wall_payload('flat').effective_paths()
        caps = [p for p in paths if p.role == 'cap']
        assert len(caps) == 2, f"Expected 2 caps for 3-wall system, got {len(caps)}"

    def test_three_wall_flat_cap_contains_all_three_endpoints(self):
        """Flat cap polyline passes through outer, source, and inner wall endpoints."""
        paths = self._3wall_payload('flat').effective_paths()
        cap_start = next(p for p in paths if p.label == 'cap_start')
        pts = cap_start.sample_points()
        xs = sorted({(round(p.x, 6), round(p.y, 6)) for p in pts})
        # Expected endpoints at x=0: (0, 65) outer, (0, 50) source, (0, 35) inner
        assert (0.0, 65.0) in xs
        assert (0.0, 50.0) in xs
        assert (0.0, 35.0) in xs

    def test_four_wall_system_still_gets_one_cap_per_end(self):
        """Source + 3 offsets (4-wall system) still gets exactly 2 caps total."""
        src = LinePath(Vec2(0, 50), Vec2(200, 50), id='L')
        ots = [
            OffsetTreatment(id='A', source_path_id='L', distance=+10),
            OffsetTreatment(id='B', source_path_id='L', distance=+20),
            OffsetTreatment(id='C', source_path_id='L', distance=-10),
        ]
        layer = PrintLayer(id='t', source_paths=[src],
                           offset_treatments=ots, cap_style='flat')
        paths = layer.effective_paths()
        caps = [p for p in paths if p.role == 'cap']
        assert len(caps) == 2

    def test_full_round_cap_spans_total_wall_thickness(self):
        """Full-round cap radius = half the perpendicular span (outermost → innermost)."""
        paths = self._3wall_payload('full_round').effective_paths()
        cap_start = next(p for p in paths if p.label == 'cap_start')
        pts = cap_start.sample_points()
        # Outer wall is at y=65, inner at y=35 → span = 30, radius should be 15
        c = Vec2((pts[0].x + pts[-1].x) / 2, (pts[0].y + pts[-1].y) / 2)
        R = pts[0].dist(c)
        assert R == pytest.approx(15.0, abs=0.5), \
            f"Full-round cap radius {R:.3f} != span/2 = 15"

    def test_three_wall_round_cap_system_zero_travel(self):
        """3-wall system with full_round cap routes with zero travel."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        moves = route_layer(self._3wall_payload('full_round').to_routing_layer())
        m = compute_metrics(moves)
        assert m['travel_moves'] == 0

    def test_three_wall_flat_cap_system_zero_travel(self):
        """3-wall system with flat cap also routes with zero travel."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        moves = route_layer(self._3wall_payload('flat').to_routing_layer())
        m = compute_metrics(moves)
        assert m['travel_moves'] == 0

    def test_cap_style_round_is_alias_for_full_round(self):
        """Legacy cap_style='round' still works, treated as full_round."""
        layer = self._3wall_payload('round')
        norm = layer._normalized_cap_style()
        assert norm == 'full_round'


class TestOffsetInversionPruning:
    """When D exceeds the local radius of curvature, the inverted
    (swallowtail) portion of the raw offset must be trimmed away."""

    def test_direction_reversed_segments_are_dropped(self):
        """
        Rounded rect R=5, inside D=10: the 90°-worth of arc samples at each
        corner invert. Trimming must remove them so the result retains
        only edges whose direction matches the source.
        """
        rect = RectanglePath(0, 0, 100, 100, id='r')
        ot = OffsetTreatment(id='in', source_path_id='r', distance=10)
        layer = PrintLayer(id='t', source_paths=[rect], offset_treatments=[ot],
                           corner_radius=5.0)
        paths = layer.effective_paths()
        inner = next(p for p in paths if p.id == 'in')
        inner_pts = inner.sample_points()
        src_pts = next(p for p in paths if p.id == 'r').sample_points()
        # Trimming should have removed many points (the 4 inverted arc regions).
        assert len(inner_pts) < len(src_pts) / 2, \
            f"Trimming left {len(inner_pts)} of {len(src_pts)} pts — missed inversions"
        # No point can be at distance < 0 (on the wrong side of source).
        # Compute signed perpendicular distance to the nearest source segment.
        import math as _m
        for p in inner_pts:
            best_signed = None
            best_abs = float('inf')
            for i in range(len(src_pts)):
                a, b = src_pts[i], src_pts[(i + 1) % len(src_pts)]
                dx, dy = b.x - a.x, b.y - a.y
                L2 = dx * dx + dy * dy
                if L2 < 1e-18:
                    continue
                t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
                fx, fy = a.x + t * dx, a.y + t * dy
                d = _m.hypot(p.x - fx, p.y - fy)
                if d < best_abs:
                    best_abs = d
                    cross = dx * (p.y - a.y) - dy * (p.x - a.x)
                    best_signed = 1.0 if cross >= 0 else -1.0
            assert best_signed is not None
            # Positive dist = left of travel = inside for CCW polygon.
            # Inside offset uses dist=+10, so surviving pts must be on the
            # inside (positive) side. No pt on the wrong side.
            assert best_signed > 0, \
                f"Surviving offset pt {p} is on the wrong side of source"


# ---------------------------------------------------------------------------
# Regression: inside offsets of rounded concave geometry (manual-test failure)
# ---------------------------------------------------------------------------

# Irregular CCW "W" with three deep notches: reflex (concave) vertices at
# the notch bottoms, acute convex teeth between them.
_W_POLY = [Vec2(0, 0), Vec2(200, 0), Vec2(200, 150), Vec2(160, 150),
           Vec2(140, 60), Vec2(110, 130), Vec2(80, 40), Vec2(50, 140),
           Vec2(30, 70), Vec2(0, 150)]


def _w_layer(corner_radius, distance):
    src = ExplicitPath(list(_W_POLY), id='w', closed=True)
    ot = OffsetTreatment(id='in', source_path_id='w', distance=distance)
    layer = PrintLayer(id='t', source_paths=[src], offset_treatments=[ot],
                       corner_radius=corner_radius)
    paths = layer.effective_paths()
    source = next(p for p in paths if p.id == 'w').sample_points()
    offset = next(p for p in paths if p.id == 'in').sample_points()
    return source, offset


def _closed_edges(pts):
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def _count_self_intersections(pts):
    edges = _closed_edges(pts)
    n = len(edges)
    return sum(1 for i in range(n) for j in range(i + 2, n)
               if not (i == 0 and j == n - 1)
               and _segments_intersect(*edges[i], *edges[j]))


_W_CASES = [(6.0, 10.0), (6.0, 20.0), (20.0, 10.0), (0.0, 10.0),
            (6.0, -10.0)]


class TestRoundedConcaveInsideOffset:
    """
    Manual failure: irregular closed path, 10 in INSIDE offset, Corner R = 6
    → the inside wall hooked / looped / crossed toward the source at concave
    corners. Root cause: the raw miter offset forms swallowtail loops
    wherever D exceeds the local feature size, and the old direction-based
    pruner could not remove them.
    """

    def test_raw_miter_offset_reproduces_the_failure(self):
        """The fixture really exercises the bug: the untrimmed offset loops."""
        src = _processed_source_pts(ExplicitPath(list(_W_POLY), closed=True),
                                    6.0)
        raw = _offset_polyline(src, 10.0, closed=True)
        assert _count_self_intersections(raw) > 0

    @pytest.mark.parametrize('cr,d', _W_CASES)
    def test_offset_has_no_self_intersections(self, cr, d):
        _, offset = _w_layer(cr, d)
        assert len(offset) >= 3
        assert _count_self_intersections(offset) == 0

    @pytest.mark.parametrize('cr,d', _W_CASES)
    def test_offset_does_not_cross_source(self, cr, d):
        source, offset = _w_layer(cr, d)
        crossings = sum(1 for e in _closed_edges(offset)
                        for s in _closed_edges(source)
                        if _segments_intersect(*e, *s))
        assert crossings == 0

    @pytest.mark.parametrize('cr,d', _W_CASES)
    def test_offset_stays_on_requested_side(self, cr, d):
        source, offset = _w_layer(cr, d)
        want_inside = d > 0          # CCW source: left of travel = inside
        for p in offset:
            assert _point_in_polygon(p, source) == want_inside, p
        # Orientation preserved (an inverted loop would wind backwards).
        assert _polygon_area(offset) > 0

    @pytest.mark.parametrize('cr,d', _W_CASES)
    def test_offset_never_closer_than_requested_distance(self, cr, d):
        """Every vertex and edge midpoint is ≥ |D| from the processed source."""
        source, offset = _w_layer(cr, d)
        for a, b in _closed_edges(offset):
            for p in (a, a.lerp(b, 0.5)):
                dist = _min_dist_to_polyline(p, source, closed=True)
                assert dist >= abs(d) - 0.01, \
                    f"offset point {p} only {dist:.3f} from source"

    def test_straight_runs_are_exactly_parallel(self):
        """Representative locations: bottom and right walls sit exactly 10 in in."""
        _, offset = _w_layer(6.0, 10.0)
        bottom = [p for p in offset if abs(p.y - 10.0) < 1e-6]
        right = [p for p in offset if abs(p.x - 190.0) < 1e-6]
        # Convex corners with R=6 < D=10 resolve to sharp miters at (10,10),
        # (190,10): the bottom inner wall spans the full width.
        assert min(p.x for p in bottom) == pytest.approx(10.0, abs=1e-6)
        assert max(p.x for p in bottom) == pytest.approx(190.0, abs=1e-6)
        assert len(right) >= 2

    def test_rounded_concave_corner_follows_source_direction(self):
        """
        Notch bottom (80,40) is a reflex vertex. Its R=6 fillet centre lies
        outside the polygon; the inside offset must be the CONCENTRIC arc of
        radius R + D = 16 — curving the same way as the source fillet, never
        hooking back toward the centre.
        """
        A, B, C = Vec2(110, 130), Vec2(80, 40), Vec2(50, 140)
        R, D = 6.0, 10.0
        d_ba, d_bc = (A - B).normalized(), (C - B).normalized()
        theta = math.acos(d_ba.x * d_bc.x + d_ba.y * d_bc.y)
        bis = (d_ba + d_bc).normalized()
        centre = B + bis * (R / math.sin(theta / 2))
        _, offset = _w_layer(R, D)
        near = [p for p in offset if p.dist(centre) < R + D + 2.0]
        on_arc = [p for p in near if abs(p.dist(centre) - (R + D)) < 0.05]
        assert len(on_arc) >= 5, "no concentric offset arc at the notch"
        for p in near:
            assert p.dist(centre) >= R + D - 0.05, \
                f"offset hooks toward the fillet centre at {p}"
        # Same side of the centre as the source fillet (toward the vertex,
        # against the bisector) — i.e. it curves the same way, not reversed.
        for p in on_arc:
            assert (p.x - centre.x) * bis.x + (p.y - centre.y) * bis.y < 0

    def test_collapsed_closed_offset_is_empty_not_inverted(self):
        """A 20×20 square cannot take a 15 in inside offset → explicit empty."""
        sq = RectanglePath(0, 0, 20, 20, id='sq')
        ot = OffsetTreatment(id='in', source_path_id='sq', distance=15)
        layer = PrintLayer(id='t', source_paths=[sq], offset_treatments=[ot],
                           corner_radius=4.0)
        inner = next(p for p in layer.effective_paths() if p.id == 'in')
        assert inner.sample_points() == []

    def test_collapsed_open_offset_is_empty(self):
        """Open U 30 in wide, inside offset 20 in: no valid wall exists."""
        u = ExplicitPath([Vec2(0, 0), Vec2(100, 0), Vec2(100, 30), Vec2(0, 30)],
                         id='u')
        ot = OffsetTreatment(id='in', source_path_id='u', distance=20)
        for cr in (0.0, 6.0):
            layer = PrintLayer(id='t', source_paths=[u], offset_treatments=[ot],
                               corner_radius=cr)
            inner = next(p for p in layer.effective_paths() if p.id == 'in')
            assert inner.sample_points() == []

    def test_open_curve_inversion_trimmed(self):
        """Tight open Bézier offset beyond its curvature radius: no loop."""
        b = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 300), id='b')
        src = b.sample_points(128)
        raw = _offset_polyline(src, -40.0, closed=False)
        trimmed = _trim_offset(raw, src, -40.0, closed=False)

        def n_x(pts):
            return sum(1 for i in range(len(pts) - 1)
                       for j in range(i + 2, len(pts) - 1)
                       if _segments_intersect(pts[i], pts[i + 1],
                                              pts[j], pts[j + 1]))
        assert n_x(raw) > 0
        assert n_x(trimmed) == 0
        for a, c in zip(trimmed, trimmed[1:]):
            assert _min_dist_to_polyline(a.lerp(c, 0.5), src, False) >= 40 - 0.05


# ---------------------------------------------------------------------------
# Regression: Rounded Corners end treatment ignored End R
# ---------------------------------------------------------------------------

class TestRoundedCornersEndRadius:
    """
    Manual failure: straight source + 10 in left + 10 in right offsets
    (walls at y = +10, 0, −10; 20 in total thickness), End caps = Rounded
    Corners, End R = 6 → ends stayed square and End R had no effect.
    Root cause: the cap builder filleted the joints BETWEEN wall endpoints
    on the end face, which are collinear for parallel walls, so every
    fillet degenerated and End R was a no-op.
    """

    def _layer(self, style='rounded_corners', r=6.0):
        src = LinePath(Vec2(0, 0), Vec2(200, 0), id='L')
        ots = [OffsetTreatment(id='left', source_path_id='L', distance=10),
               OffsetTreatment(id='right', source_path_id='L', distance=-10)]
        return PrintLayer(id='t', source_paths=[src], offset_treatments=ots,
                          cap_style=style, cap_corner_radius=r)

    def _cap(self, style='rounded_corners', r=6.0, label='cap_start'):
        paths = self._layer(style, r).effective_paths()
        return next(p for p in paths if p.label == label).sample_points()

    def test_cap_is_not_a_straight_segment(self):
        pts = self._cap(r=6.0)
        assert len(pts) > 3
        assert min(p.x for p in pts) == pytest.approx(-6.0, abs=1e-6)
        assert any(abs(p.x) > 1.0 for p in pts)

    def test_fillet_arcs_at_both_corners(self):
        """Outer fillet centred (0, 4), inner fillet centred (0, −4), radius 6."""
        pts = self._cap(r=6.0)
        upper = [p for p in pts if p.y > 4.0 + 1e-9]
        lower = [p for p in pts if p.y < -4.0 - 1e-9]
        assert len(upper) >= 5 and len(lower) >= 5
        for p in upper:
            assert p.dist(Vec2(0, 4)) == pytest.approx(6.0, abs=1e-6)
        for p in lower:
            assert p.dist(Vec2(0, -4)) == pytest.approx(6.0, abs=1e-6)
        # Straight end face between the fillets, 6 in beyond the wall ends.
        face = [p for p in pts if -4.0 - 1e-9 <= p.y <= 4.0 + 1e-9]
        assert all(p.x == pytest.approx(-6.0, abs=1e-6) for p in face)
        assert max(p.y for p in face) == pytest.approx(4.0, abs=1e-6)
        assert min(p.y for p in face) == pytest.approx(-4.0, abs=1e-6)

    def test_cap_is_tangent_to_the_walls(self):
        """First/last cap segments leave the walls along the wall direction."""
        pts = self._cap(r=6.0)
        assert pts[0] == Vec2(0, 10) and pts[-1] == Vec2(0, -10)
        for a, b in ((pts[0], pts[1]), (pts[-1], pts[-2])):
            d = (b - a).normalized()
            assert d.x < -math.cos(math.radians(5))   # heading outward (−x)

    def test_end_r_changes_coordinates(self):
        p2, p6 = self._cap(r=2.0), self._cap(r=6.0)
        assert min(p.x for p in p2) == pytest.approx(-2.0, abs=1e-6)
        assert min(p.x for p in p6) == pytest.approx(-6.0, abs=1e-6)
        assert [(p.x, p.y) for p in p2] != [(p.x, p.y) for p in p6]

    def test_end_r_zero_is_flat(self):
        assert self._cap('rounded_corners', 0.0) == self._cap('flat', 0.0)

    def test_end_r_clamped_to_half_thickness_equals_full_round(self):
        clamped = self._cap('rounded_corners', 50.0)
        full = self._cap('full_round', 0.0)
        assert len(clamped) == len(full)
        for a, b in zip(clamped, full):
            assert a.x == pytest.approx(b.x, abs=1e-9)
            assert a.y == pytest.approx(b.y, abs=1e-9)
        for p in full:                      # true semicircle, radius W/2
            assert p.dist(Vec2(0, 0)) == pytest.approx(10.0, abs=1e-6)

    def test_end_cap_mirrors_at_far_end(self):
        pts = self._cap(r=6.0, label='cap_end')
        assert max(p.x for p in pts) == pytest.approx(206.0, abs=1e-6)

    @pytest.mark.parametrize('style,r', [('rounded_corners', 6.0),
                                         ('full_round', 0.0)])
    def test_intermediate_wall_joins_cap_without_its_own_cap(self, style, r):
        """
        The centre wall continues straight onto the cap profile — no spike
        back into the cap, no independent cap, no dangling end.
        """
        paths = self._layer(style, r).effective_paths()
        assert sum(1 for p in paths if p.label == 'cap_start') == 1
        assert sum(1 for p in paths if p.label == 'cap_end') == 1
        reach = 6.0 if style == 'rounded_corners' else 10.0
        for label, wall_end, land in (
                ('cap_start', Vec2(0, 0), Vec2(-reach, 0)),
                ('cap_end', Vec2(200, 0), Vec2(200 + reach, 0))):
            cap = next(p for p in paths if p.label == label).sample_points()
            ext = [p.sample_points() for p in paths
                   if p.label == label + '_ext']
            assert ext == [[wall_end, land]]
            assert land in cap                      # joined at a cap vertex
            assert all(p.dist(wall_end) > 1.0 for p in cap)   # no spike

    @pytest.mark.parametrize('style,r', [('rounded_corners', 2.0),
                                         ('rounded_corners', 6.0),
                                         ('rounded_corners', 0.0),
                                         ('rounded_corners', 50.0),
                                         ('full_round', 0.0),
                                         ('flat', 0.0)])
    def test_wall_system_routes_as_one_run_zero_travel(self, style, r):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics
        m = compute_metrics(route_layer(self._layer(style, r).to_routing_layer()))
        assert m['travel_moves'] == 0
        assert m['print_runs'] == 1

    def test_two_wall_system_rounded_corners(self):
        """Source + one 20 in offset, End R 6: fillets at both corners, face between."""
        src = LinePath(Vec2(0, 0), Vec2(200, 0), id='L')
        ot = OffsetTreatment(id='o', source_path_id='L', distance=20)
        layer = PrintLayer(id='t', source_paths=[src], offset_treatments=[ot],
                           cap_style='rounded_corners', cap_corner_radius=6.0)
        paths = layer.effective_paths()
        cap = next(p for p in paths if p.label == 'cap_start').sample_points()
        assert {(cap[0].x, cap[0].y), (cap[-1].x, cap[-1].y)} == {(0, 20), (0, 0)}
        assert min(p.x for p in cap) == pytest.approx(-6.0, abs=1e-6)
        assert not any(p.label.endswith('_ext') for p in paths)


class TestCaseDRouting:
    def test_case_d_zero_travel_via_print_layer(self):
        """Case D must route with 0 travel moves via the PrintLayer pipeline."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))
        from graph import route_layer, compute_metrics

        perim = ExplicitPath(
            [Vec2(0,0),Vec2(25,0),Vec2(50,0),Vec2(75,0),Vec2(100,0),
             Vec2(100,10),Vec2(75,10),Vec2(50,10),Vec2(25,10),Vec2(0,10)],
            id='perim', closed=True, role='outer',
        )
        web = ExplicitPath(
            [Vec2(25,10),Vec2(25,0),Vec2(50,10),Vec2(50,0),Vec2(75,10),Vec2(75,0)],
            id='web', closed=False, role='lattice',
        )
        pl = PrintLayer(id='caseD')
        pl.source_paths = [perim, web]

        rl = pl.to_routing_layer()
        moves = route_layer(rl)
        metrics = compute_metrics(moves)

        assert metrics['travel_moves'] == 0
        assert metrics['pct_printing'] == 100.0
