"""
Graph construction and retrace weighting for wall networks:

  - X junctions: segments crossing in both their interiors physically
    meet, so they share a graph node (like T junctions already did).
  - retrace_cost: when odd junctions must be paired by retracing, the
    router minimises length × retrace_cost, so visible wall faces (cost > 1)
    are avoided in favour of internal geometry (cost 1).
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from geometry import Vec2, Strand, Layer, Role
from graph import build_graph, route_layer, compute_metrics, graph_info


def _layer(*strands):
    return Layer(z_height=0.0, strands=list(strands))


def _s(sid, pts, closed=False, cost=1.0):
    return Strand(sid, Role.FREE, [Vec2(*p) for p in pts], closed=closed,
                  retrace_cost=cost)


class TestCrossings:

    def test_two_strands_crossing_share_a_node(self):
        lay = _layer(_s('a', [(0, 0), (10, 10)]), _s('b', [(0, 10), (10, 0)]))
        G = build_graph(lay)
        assert G.degree((5.0, 5.0)) == 4
        assert graph_info(lay)['component_count'] == 1
        m = compute_metrics(route_layer(lay))
        assert m['travel_moves'] == 0 and m['print_runs'] == 1

    def test_line_crossing_closed_loop_twice(self):
        lay = _layer(_s('sq', [(0, 0), (10, 0), (10, 10), (0, 10)], closed=True),
                     _s('l', [(-5, 3.7), (15, 6.1)]))
        G = build_graph(lay)
        assert sum(1 for _, d in G.degree() if d == 4) == 2
        assert graph_info(lay)['component_count'] == 1

    def test_self_crossing_strand(self):
        lay = _layer(_s('z', [(0, 0), (10, 10), (10, 0), (0, 10)]))
        G = build_graph(lay)
        assert G.degree((5.0, 5.0)) == 4

    def test_parallel_and_touching_segments_not_split_spuriously(self):
        lay = _layer(_s('a', [(0, 0), (10, 0)]), _s('b', [(0, 1), (10, 1)]),
                     _s('c', [(10, 0), (20, 0)]))
        G = build_graph(lay)
        assert G.number_of_nodes() == 5
        assert graph_info(lay)['component_count'] == 2

    def test_near_miss_crossing_is_not_a_junction(self):
        # b stops 0.01 short of a: no contact, no node.
        lay = _layer(_s('a', [(0, 0), (10, 0)]), _s('b', [(5, 0.01), (5, 10)]))
        assert graph_info(lay)['component_count'] == 2


class TestRetraceCost:

    def _scene(self, face_cost):
        # A face line with a node in the middle, an internal arch beside it
        # connecting the same two nodes, and two face spurs making four odd
        # nodes. Pairing the two junctions needs one retrace: along the
        # face (20 in) or along the arch (2·√164 ≈ 25.61 in).
        return _layer(
            _s('face', [(0, 0), (10, 0), (20, 0)], cost=face_cost),
            _s('arch', [(0, 0), (10, 8), (20, 0)], cost=1.0),
            _s('spur1', [(0, 0), (0, -30)], cost=face_cost),
            _s('spur2', [(20, 0), (20, -30)], cost=face_cost))

    def test_default_cost_retraces_the_shortest_edges(self):
        moves = route_layer(self._scene(1.0))
        m = compute_metrics(moves)
        assert m['retrace_distance'] == pytest.approx(20.0)
        assert {mv.strand_id for mv in moves if mv.kind == 'retrace'} == {'face'}

    def test_face_cost_moves_the_retrace_inside(self):
        moves = route_layer(self._scene(3.0))
        m = compute_metrics(moves)
        assert m['retrace_distance'] == pytest.approx(2 * math.sqrt(164), abs=0.01)
        assert {mv.strand_id for mv in moves if mv.kind == 'retrace'} == {'arch'}
        assert m['travel_moves'] == 0 and m['print_runs'] == 1

    def test_strand_default_retrace_cost_is_one(self):
        assert Strand('x', Role.FREE, [Vec2(0, 0), Vec2(1, 0)], False).retrace_cost == 1.0


class TestStrandKinds:
    """Hidden infill ('field') crossing hidden geometry is structural, not a
    routing junction; walls ('face', 'internal') still join where they cross."""

    def _g(self, k1, k2):
        return build_graph(_layer(
            Strand('a', Role.FREE, [Vec2(0, 0), Vec2(10, 10)], False, kind=k1),
            Strand('b', Role.FREE, [Vec2(0, 10), Vec2(10, 0)], False, kind=k2)))

    def test_default_kind_is_face(self):
        assert Strand('x', Role.FREE, [Vec2(0, 0), Vec2(1, 0)], False).kind == 'face'

    def test_field_crossings(self):
        assert (5.0, 5.0) not in self._g('field', 'field').nodes
        assert (5.0, 5.0) not in self._g('field', 'internal').nodes
        assert self._g('field', 'face').degree((5.0, 5.0)) == 4
        assert self._g('internal', 'internal').degree((5.0, 5.0)) == 4

    def test_touch_still_joins_field_strands(self):
        G = build_graph(_layer(
            Strand('a', Role.FREE, [Vec2(0, 0), Vec2(10, 0)], False, kind='field'),
            Strand('b', Role.FREE, [Vec2(5, 0), Vec2(5, 10)], False, kind='field')))
        assert G.degree((5.0, 0.0)) == 3


class TestRouteEnds:

    def test_open_route_ends_and_alternation(self):
        from graph import route_ends, orient_for_previous, reverse_route
        moves = route_layer(_layer(_s('l', [(0, 0), (10, 0), (10, 10)])))
        e = route_ends(moves)
        assert not e.closed and {e.start, e.end} == {(0, 0), (10, 10)}
        nxt, d = orient_for_previous(moves, e.end)
        assert d == 0 and route_ends(nxt).start == e.end
        assert compute_metrics(reverse_route(moves))['retrace_distance'] == 0

    def test_closed_route(self):
        from graph import route_ends
        moves = route_layer(_layer(_s('sq', [(0, 0), (10, 0), (10, 10), (0, 10)],
                                      closed=True)))
        assert route_ends(moves).closed
