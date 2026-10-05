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
    OffsetTreatment, ZigzagGenerator, WaveGenerator, LatticeInstance,
    PrintLayer, TraversalConstraints, DerivedPath, _offset_polyline,
    _resample, _point_in_polygon, _polygon_area, _lattice_valid_in_cavity,
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
        result = ot.generate(src)
        assert isinstance(result, DerivedPath)

    def test_source_not_mutated(self):
        src = self._make_rect_path()
        original_pts = [Vec2(p.x, p.y) for p in src.points]
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-10)
        ot.generate(src)
        assert src.points == original_pts

    def test_offset_changes_geometry(self):
        src = self._make_rect_path()
        src_pts = src.sample_points()
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-5)
        derived = ot.generate(src)
        off_pts = derived.sample_points()
        # Offset points must differ from source points
        assert any(
            abs(s.x - o.x) > 0.1 or abs(s.y - o.y) > 0.1
            for s, o in zip(src_pts, off_pts)
        )

    def test_derived_path_role(self):
        src = self._make_rect_path()
        ot = OffsetTreatment(id='ot1', source_path_id='src', distance=-5, role='inner')
        result = ot.generate(src)
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
        assert 'phase' in names
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
        # phase=0, V1: wave starts at alpha=0 → on A
        result = gen.generate(a, b, {'cycles': 3.0, 'phase': 0.0}, 0)
        pts = result[0].sample_points()
        # First point: t=0, alpha = 0.5*(1-cos(0)) = 0 → on A (y=0)
        assert abs(pts[0].y - 0.0) < 0.01

    def test_wave_touches_boundary_b(self):
        """Wave alpha=1 → point exactly on boundary B."""
        gen = WaveGenerator()
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='wa')
        b = ExplicitPath([Vec2(0, 30), Vec2(100, 30)], id='wb')
        # phase=0, V1: first minimum of alpha=1 at t = 0.5/cycles
        result = gen.generate(a, b, {'cycles': 1.0, 'phase': 0.0}, 0)
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
        derived = ot.generate(cp)
        pts = derived.sample_points()
        for p in pts:
            r = math.hypot(p.x - 200, p.y - 200)
            assert abs(r - 50) < 0.2, f"Point radius {r:.4f} deviates from expected 50"

    def test_circle_outside_offset_increases_radius(self):
        """Circle r=60, offset dist=-10: all result points within 0.2 in of radius 70."""
        cp = CirclePath(0, 0, 60)
        ot = OffsetTreatment(id='ot', source_path_id='c', distance=-10)
        derived = ot.generate(cp)
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
        derived = ot.generate(src)
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
