"""Tests for geometry data structures."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import math
import pytest
from geometry import Vec2, Strand, Layer, PrintMove, Role


class TestVec2:
    def test_dist_zero(self):
        assert Vec2(0, 0).dist(Vec2(0, 0)) == 0.0

    def test_dist_axis(self):
        assert Vec2(0, 0).dist(Vec2(3, 4)) == pytest.approx(5.0)

    def test_lerp_half(self):
        a, b = Vec2(0, 0), Vec2(10, 20)
        mid = a.lerp(b, 0.5)
        assert mid == Vec2(5, 10)

    def test_lerp_endpoints(self):
        a, b = Vec2(1, 2), Vec2(7, 8)
        assert a.lerp(b, 0.0) == a
        assert a.lerp(b, 1.0) == b

    def test_hashable(self):
        s = {Vec2(1, 2), Vec2(1, 2), Vec2(3, 4)}
        assert len(s) == 2


class TestStrand:
    def _rect(self, closed=True):
        pts = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)]
        return Strand('r', Role.OUTER, pts, closed=closed)

    def test_closed_segment_count(self):
        s = self._rect(closed=True)
        assert len(s.segments()) == 4

    def test_open_segment_count(self):
        s = self._rect(closed=False)
        assert len(s.segments()) == 3

    def test_closed_length(self):
        s = self._rect(closed=True)
        assert s.length() == pytest.approx(40.0)

    def test_open_length(self):
        s = self._rect(closed=False)
        # 3 sides of a 10x10 square
        assert s.length() == pytest.approx(30.0)

    def test_single_segment_closed(self):
        s = Strand('s', Role.FREE, [Vec2(0, 0), Vec2(5, 0)], closed=True)
        segs = s.segments()
        assert len(segs) == 2
        assert segs[0] == (Vec2(0, 0), Vec2(5, 0))
        assert segs[1] == (Vec2(5, 0), Vec2(0, 0))

    def test_to_dict_round_trip(self):
        s = self._rect()
        d = s.to_dict()
        assert d['id'] == 'r'
        assert d['role'] == Role.OUTER
        assert len(d['points']) == 4
        assert d['closed'] is True

    def test_start_end(self):
        pts = [Vec2(1, 2), Vec2(3, 4), Vec2(5, 6)]
        s = Strand('x', Role.FREE, pts, closed=False)
        assert s.start == Vec2(1, 2)
        assert s.end == Vec2(5, 6)


class TestPrintMove:
    def test_length(self):
        m = PrintMove('print', 's1', 0, Vec2(0, 0), Vec2(3, 4))
        assert m.length == pytest.approx(5.0)

    def test_to_dict(self):
        m = PrintMove('travel', None, None, Vec2(1, 2), Vec2(4, 6))
        d = m.to_dict()
        assert d['kind'] == 'travel'
        assert d['start'] == [1, 2]
        assert d['end'] == [4, 6]
        assert d['length'] == pytest.approx(5.0)


class TestLayer:
    def test_to_dict(self):
        pts = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10)]
        s = Strand('s', Role.OUTER, pts, closed=False)
        layer = Layer(z_height=12.5, strands=[s], label='test')
        d = layer.to_dict()
        assert d['z_height'] == 12.5
        assert d['label'] == 'test'
        assert len(d['strands']) == 1
