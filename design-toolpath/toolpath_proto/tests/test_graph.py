"""
Tests for graph construction, Eulerian routing, and metrics.

Key invariants verified:
  - All print edges are covered exactly once in a valid traversal
  - Consecutive moves are connected (no teleporting)
  - Metrics correctly reflect the traversal
  - Each test case produces the expected structural properties
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import math
import pytest
import networkx as nx

from geometry import Vec2, Strand, Layer, Role
from graph import build_graph, route_layer, compute_metrics, graph_info
from test_cases import case_a, case_b, case_c, case_d, case_e, ALL_CASES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_print_edges_covered(layer: Layer, moves) -> bool:
    """Every print edge (strand segment) must appear exactly once."""
    from collections import Counter
    expected = Counter()
    for strand in layer.strands:
        for idx, (a, b) in enumerate(strand.segments()):
            key = (strand.id, idx)
            expected[key] += 1

    actual = Counter()
    for m in moves:
        if m.kind == 'print' and m.strand_id is not None:
            actual[(m.strand_id, m.seg_idx)] += 1

    return actual == expected


def _moves_connected(moves) -> bool:
    """Each move must start where the previous one ended."""
    for i in range(1, len(moves)):
        prev_end = moves[i - 1].end
        curr_start = moves[i].start
        if abs(prev_end.x - curr_start.x) > 1e-9 or abs(prev_end.y - curr_start.y) > 1e-9:
            return False
    return True


def _count_by_kind(moves, kind: str) -> int:
    return sum(1 for m in moves if m.kind == kind)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

class TestBuildGraph:
    def test_single_closed_rect_node_count(self):
        layer = case_a()
        G = build_graph(layer)
        assert G.number_of_nodes() == 4

    def test_single_closed_rect_edge_count(self):
        layer = case_a()
        G = build_graph(layer)
        assert G.number_of_edges() == 4

    def test_all_nodes_even_degree_for_closed_loop(self):
        layer = case_a()
        G = build_graph(layer)
        odd = [n for n, d in G.degree() if d % 2 == 1]
        assert len(odd) == 0

    def test_two_components_for_case_b(self):
        G = build_graph(case_b())
        assert len(list(nx.connected_components(G))) == 2

    def test_case_d_is_connected(self):
        G = build_graph(case_d())
        assert nx.is_connected(G)

    def test_case_d_has_exactly_two_odd_nodes(self):
        G = build_graph(case_d())
        odd = [n for n, d in G.degree() if d % 2 == 1]
        assert len(odd) == 2

    def test_case_d_has_eulerian_path(self):
        G = build_graph(case_d())
        assert nx.has_eulerian_path(G)
        assert not nx.is_eulerian(G)  # path, not circuit

    def test_case_e_three_components(self):
        G = build_graph(case_e())
        # square + zigzag + T-intersection = 3 components
        assert len(list(nx.connected_components(G))) == 3


class TestGraphInfo:
    def test_case_a_eulerian(self):
        info = graph_info(case_a())
        assert info['is_eulerian'] is True
        assert info['odd_degree_nodes'] == 0
        assert info['component_count'] == 1

    def test_case_d_path_not_circuit(self):
        info = graph_info(case_d())
        assert info['has_eulerian_path'] is True
        assert info['is_eulerian'] is False
        assert info['odd_degree_nodes'] == 2

    def test_case_b_two_components(self):
        info = graph_info(case_b())
        assert info['component_count'] == 2


# ---------------------------------------------------------------------------
# Routing — valid traversal invariants
# ---------------------------------------------------------------------------

class TestRouteLayerValidity:
    @pytest.mark.parametrize('case_fn', [case_a, case_b, case_c, case_d, case_e])
    def test_all_print_edges_covered(self, case_fn):
        layer = case_fn()
        moves = route_layer(layer)
        assert _all_print_edges_covered(layer, moves), \
            "Not all print edges covered or some covered more than once"

    @pytest.mark.parametrize('case_fn', [case_a, case_b, case_c, case_d, case_e])
    def test_moves_are_connected(self, case_fn):
        layer = case_fn()
        moves = route_layer(layer)
        assert _moves_connected(moves), "Moves are not spatially connected"

    @pytest.mark.parametrize('case_fn', [case_a, case_b, case_c, case_d, case_e])
    def test_no_unknown_move_kinds(self, case_fn):
        layer = case_fn()
        moves = route_layer(layer)
        for m in moves:
            assert m.kind in ('print', 'travel', 'retrace'), \
                f"Unknown move kind: {m.kind}"


# ---------------------------------------------------------------------------
# Routing — expected properties per case
# ---------------------------------------------------------------------------

class TestCaseA:
    def test_zero_travel_moves(self):
        moves = route_layer(case_a())
        assert _count_by_kind(moves, 'travel') == 0

    def test_single_print_run(self):
        moves = route_layer(case_a())
        metrics = compute_metrics(moves)
        assert metrics['print_runs'] == 1

    def test_full_length(self):
        layer = case_a()
        moves = route_layer(layer)
        # 100 + 50 + 100 + 50 = 300 units perimeter
        metrics = compute_metrics(moves)
        assert metrics['print_distance'] == pytest.approx(300.0)

    def test_hundred_percent_printing(self):
        moves = route_layer(case_a())
        metrics = compute_metrics(moves)
        assert metrics['pct_printing'] == pytest.approx(100.0)


class TestCaseB:
    def test_exactly_one_travel_move(self):
        moves = route_layer(case_b())
        assert _count_by_kind(moves, 'travel') == 1

    def test_two_print_runs(self):
        moves = route_layer(case_b())
        metrics = compute_metrics(moves)
        assert metrics['print_runs'] == 2


class TestCaseC:
    def test_exactly_one_travel_move(self):
        moves = route_layer(case_c())
        assert _count_by_kind(moves, 'travel') == 1


class TestCaseD:
    def test_zero_travel_moves(self):
        """Geometry D has exactly 2 odd-degree nodes — Eulerian path — zero travel."""
        moves = route_layer(case_d())
        assert _count_by_kind(moves, 'travel') == 0

    def test_single_print_run(self):
        moves = route_layer(case_d())
        metrics = compute_metrics(moves)
        assert metrics['print_runs'] == 1

    def test_hundred_percent_printing(self):
        moves = route_layer(case_d())
        metrics = compute_metrics(moves)
        assert metrics['pct_printing'] == pytest.approx(100.0)


class TestCaseE:
    def test_two_inter_component_travel_moves(self):
        """Three disconnected components require 2 travel moves between them."""
        moves = route_layer(case_e())
        assert _count_by_kind(moves, 'travel') >= 2


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_all_keys_present(self):
        moves = route_layer(case_a())
        m = compute_metrics(moves)
        for key in ('print_distance', 'travel_distance', 'retrace_distance',
                    'print_runs', 'travel_moves', 'pct_printing', 'total_moves'):
            assert key in m

    def test_zero_travel_for_case_a(self):
        moves = route_layer(case_a())
        m = compute_metrics(moves)
        assert m['travel_distance'] == 0.0
        assert m['travel_moves'] == 0

    def test_pct_printing_between_0_and_100(self):
        for case_fn in [case_a, case_b, case_c, case_d, case_e]:
            moves = route_layer(case_fn())
            m = compute_metrics(moves)
            assert 0.0 <= m['pct_printing'] <= 100.0

    def test_total_moves_count(self):
        moves = route_layer(case_a())
        m = compute_metrics(moves)
        assert m['total_moves'] == len(moves)

    def test_distances_non_negative(self):
        for case_fn in [case_a, case_b, case_c, case_d, case_e]:
            moves = route_layer(case_fn())
            m = compute_metrics(moves)
            assert m['print_distance'] >= 0
            assert m['travel_distance'] >= 0
            assert m['retrace_distance'] >= 0
