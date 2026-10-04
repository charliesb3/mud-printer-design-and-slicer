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
    PrintLayer, TraversalConstraints, DerivedPath,
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
# WaveGenerator
# ---------------------------------------------------------------------------

class TestWaveGenerator:
    def _two_lines(self):
        a = ExplicitPath([Vec2(0, 0), Vec2(100, 0)], id='wa')
        b = ExplicitPath([Vec2(0, 30), Vec2(100, 30)], id='wb')
        return a, b

    def test_returns_one_path(self):
        gen = WaveGenerator()
        a, b = self._two_lines()
        result = gen.generate(a, b, {'amplitude': 10, 'frequency': 3, 'samples': 32})
        assert len(result) == 1

    def test_parameters_declared(self):
        specs = WaveGenerator().parameters()
        names = [s.name for s in specs]
        assert 'amplitude' in names
        assert 'frequency' in names

    def test_variation_count(self):
        gen = WaveGenerator()
        a, b = self._two_lines()
        assert gen.variation_count(a, b, {}) == 2

    def test_variation_changes_output(self):
        gen = WaveGenerator()
        a, b = self._two_lines()
        r0 = gen.generate(a, b, {'amplitude': 10, 'frequency': 3, 'samples': 32}, 0)
        r1 = gen.generate(a, b, {'amplitude': 10, 'frequency': 3, 'samples': 32}, 1)
        pts0 = r0[0].sample_points()
        pts1 = r1[0].sample_points()
        # Different phases must produce different point positions
        assert any(
            abs(pts0[i].x - pts1[i].x) > 0.1 or abs(pts0[i].y - pts1[i].y) > 0.1
            for i in range(len(pts0))
        )


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
