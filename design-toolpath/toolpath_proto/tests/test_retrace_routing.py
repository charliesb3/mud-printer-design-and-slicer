"""
Routing over the ACTUAL printable graph:

  - T-junctions (a vertex lying on another strand's segment) are real
    contact and become shared graph nodes; near-misses do not.
  - A connected non-Eulerian component prints as ONE continuous run by
    retracing printable edges (route inspection, open trail); travel moves
    occur only between disconnected components.
"""
import sys, os, itertools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import networkx as nx

from geometry import Vec2, Strand, Layer, Role, PrintMove
from graph import (build_graph, route_layer, compute_metrics, graph_info,
                   label_passes)
from test_cases import case_e


def _layer(*strands):
    return Layer(z_height=0.0, strands=list(strands))


def _s(sid, pts, closed=False):
    return Strand(sid, Role.FREE, [Vec2(*p) for p in pts], closed=closed)


def _edges_printed_once(layer, moves):
    """Every edge of the routing graph is printed exactly once ('print');
    re-traversals are 'retrace'."""
    from collections import Counter
    G = build_graph(layer)
    expected = Counter()
    for u, v, d in G.edges(data=True):
        expected[(d['strand_id'], d['seg_idx'], tuple(sorted((u, v))))] += 1
    got = Counter()
    for m in moves:
        if m.kind == 'print':
            got[(m.strand_id, m.seg_idx,
                 tuple(sorted(((m.start.x, m.start.y),
                               (m.end.x, m.end.y)))))] += 1
    return got == expected


def _connected(moves):
    return all(abs(a.end.x - b.start.x) < 1e-9 and
               abs(a.end.y - b.start.y) < 1e-9
               for a, b in zip(moves, moves[1:]))


def _retraced_edges_exist(layer, moves):
    G = build_graph(layer)
    for m in moves:
        if m.kind == 'retrace':
            u, v = (m.start.x, m.start.y), (m.end.x, m.end.y)
            if not G.has_edge(u, v):
                return False
    return True


# ---------------------------------------------------------------------------
# T-junctions
# ---------------------------------------------------------------------------

class TestJunctions:

    def test_vertex_on_segment_interior_becomes_shared_node(self):
        wall = _s('wall', [(0, 0), (100, 0)])
        web = _s('web', [(50, 0), (50, 30)])           # touches mid-wall
        G = build_graph(_layer(wall, web))
        assert nx.number_connected_components(G) == 1
        assert G.degree((50, 0)) == 3
        assert G.number_of_edges() == 3                # wall split in two

    def test_near_miss_is_not_merged(self):
        wall = _s('wall', [(0, 0), (100, 0)])
        web = _s('web', [(50, 1e-3), (50, 30)])        # 0.001 in gap
        G = build_graph(_layer(wall, web))
        assert nx.number_connected_components(G) == 2
        m = compute_metrics(route_layer(_layer(wall, web)))
        assert m['print_runs'] == 2 and m['travel_moves'] == 1

    def test_lattice_touching_walls_mid_segment_is_connected(self):
        """Zigzag between two straight walls touching each only mid-segment."""
        a = _s('A', [(0, 0), (200, 0)])
        b = _s('B', [(0, 30), (200, 30)])
        zz = _s('zz', [(20, 0), (60, 30), (100, 0), (140, 30), (180, 0)])
        layer = _layer(a, b, zz)
        assert graph_info(layer)['component_count'] == 1
        moves = route_layer(layer)
        m = compute_metrics(moves)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0
        assert _edges_printed_once(layer, moves)

    def test_existing_case_d_unchanged(self):
        from test_cases import case_d
        info = graph_info(case_d())
        assert info['odd_degree_nodes'] == 2 and info['component_count'] == 1


# ---------------------------------------------------------------------------
# Retrace augmentation (open-trail route inspection)
# ---------------------------------------------------------------------------

class TestRetraceRouting:

    def _star(self):
        """Arms of length 10, 20, 30 from (0,0): 4 odd nodes, connected."""
        return _layer(_s('a', [(0, 0), (10, 0)]),
                      _s('b', [(0, 0), (0, 20)]),
                      _s('c', [(0, 0), (-30, 0)]))

    def test_connected_non_eulerian_is_one_run_zero_travel(self):
        layer = self._star()
        info = graph_info(layer)
        assert info['component_count'] == 1 and info['odd_degree_nodes'] == 4
        moves = route_layer(layer)
        m = compute_metrics(moves)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0
        assert _connected(moves)
        assert _edges_printed_once(layer, moves)
        assert _retraced_edges_exist(layer, moves)

    def test_retrace_is_minimal(self):
        """Trail ends at the two longest arms; only the 10 in arm repeats."""
        m = compute_metrics(route_layer(self._star()))
        assert m['retrace_distance'] == pytest.approx(10.0)

    def test_retrace_minimal_matches_brute_force(self):
        """Square with two diagonals' halves: compare with exhaustive pairing."""
        layer = _layer(
            _s('sq', [(0, 0), (40, 0), (40, 40), (0, 40)], closed=True),
            _s('d1', [(0, 0), (20, 20)]), _s('d2', [(20, 20), (40, 40)]),
            _s('d3', [(40, 0), (20, 20)]), _s('t', [(0, 40), (-15, 55)]))
        G = build_graph(layer)
        odd = [n for n, d in G.degree() if d % 2]
        D = dict(nx.all_pairs_dijkstra_path_length(G, weight='length'))

        def best(nodes):          # min-weight perfect matching, exhaustive
            if not nodes:
                return 0.0
            first, rest = nodes[0], nodes[1:]
            opts = []
            for i, other in enumerate(rest):
                opts.append(D[first][other] + best(rest[:i] + rest[i + 1:]))
            return min(opts)

        brute = min(best([o for o in odd if o not in pair])
                    for pair in itertools.combinations(odd, 2))
        m = compute_metrics(route_layer(layer))
        assert m['travel_moves'] == 0
        assert m['retrace_distance'] == pytest.approx(brute, abs=0.01)  # metrics round to 0.01

    def test_disconnected_geometry_still_travels(self):
        layer = _layer(_s('a', [(0, 0), (10, 0)]), _s('b', [(50, 0), (60, 0)]))
        m = compute_metrics(route_layer(layer))
        assert m['print_runs'] == 2 and m['travel_moves'] == 1
        assert m['retrace_distance'] == 0

    def test_case_e_travel_only_between_components(self):
        """3 components → exactly 2 travels; the T-component retraces."""
        moves = route_layer(case_e())
        m = compute_metrics(moves)
        assert m['travel_moves'] == 2 and m['print_runs'] == 3
        assert m['retrace_distance'] > 0
        assert _edges_printed_once(case_e(), moves)

    def test_pinned_odd_start_is_honoured(self):
        layer = self._star()
        moves = route_layer(layer, start=Vec2(0, 20))
        assert (moves[0].start.x, moves[0].start.y) == (0, 20)
        m = compute_metrics(moves)
        assert m['travel_moves'] == 0 and m['print_runs'] == 1

    def test_eulerian_cases_have_no_retrace(self):
        from test_cases import case_a, case_d
        for case in (case_a, case_d):
            assert compute_metrics(route_layer(case()))['retrace_distance'] == 0

    def test_label_passes_after_reversal(self):
        moves = route_layer(self._star())
        rev = label_passes([PrintMove(m.kind, m.strand_id, m.seg_idx,
                                      m.end, m.start)
                            for m in reversed(moves)])
        assert _edges_printed_once(self._star(), rev)
        assert compute_metrics(rev)['retrace_distance'] == pytest.approx(10.0)
