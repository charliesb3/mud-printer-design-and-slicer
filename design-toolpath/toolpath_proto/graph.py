"""
Print graph construction and continuity-first routing.

Printable geometry is modeled as an undirected multigraph:
  - nodes  = point coordinates (x, y)
  - edges  = printable segments (kind='print') or travel gaps (kind='travel')

A fully continuous print corresponds to an Eulerian traversal.
When the graph is non-Eulerian, minimum-weight travel edges are added to
pair up odd-degree nodes (analogous to the Chinese Postman / Route Inspection
problem), so the total non-printing travel is minimised.
"""
from __future__ import annotations
import math
import networkx as nx

from geometry import Vec2, Layer, PrintMove


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _pt(v: Vec2) -> tuple[float, float]:
    return (v.x, v.y)


def build_graph(layer: Layer) -> nx.MultiGraph:
    G = nx.MultiGraph()
    for strand in layer.strands:
        for idx, (a, b) in enumerate(strand.segments()):
            G.add_edge(
                _pt(a), _pt(b),
                strand_id=strand.id,
                seg_idx=idx,
                kind='print',
                length=round(a.dist(b), 6),
            )
    return G


# ---------------------------------------------------------------------------
# Augmentation (Chinese Postman / Route Inspection)
# ---------------------------------------------------------------------------

def _euclid(u: tuple, v: tuple) -> float:
    return math.sqrt((u[0] - v[0]) ** 2 + (u[1] - v[1]) ** 2)


def _augment(G: nx.MultiGraph) -> nx.MultiGraph:
    """
    Return a copy of G with minimum-weight travel edges added between
    odd-degree node pairs so that the result has an Eulerian circuit.
    Uses minimum-weight perfect matching on the complete graph of odd nodes.
    """
    odd = [n for n, d in G.degree() if d % 2 == 1]
    if not odd:
        return G

    pairs_graph = nx.Graph()
    for i, u in enumerate(odd):
        for v in odd[i + 1:]:
            pairs_graph.add_edge(u, v, weight=_euclid(u, v))

    matching = nx.min_weight_matching(pairs_graph)

    aug = G.copy()
    for u, v in matching:
        aug.add_edge(u, v,
                     strand_id=None, seg_idx=None,
                     kind='travel',
                     length=round(_euclid(u, v), 6))
    return aug


# ---------------------------------------------------------------------------
# Source-node selection
# ---------------------------------------------------------------------------

def _nearest(candidates: list[tuple], pos: tuple | None) -> tuple:
    if pos is None:
        return candidates[0]
    return min(candidates, key=lambda n: _euclid(pos, n))


def _choose_source(G: nx.MultiGraph,
                   current: tuple | None,
                   pinned: tuple | None) -> tuple:
    odd = [n for n, d in G.degree() if d % 2 == 1]
    if pinned and pinned in G.nodes:
        # Must start at an odd-degree node for Eulerian path (if 2 odd nodes).
        # If it's a circuit (0 odd), any node is valid.
        if not odd or pinned in odd:
            return pinned
    candidates = odd if odd else list(G.nodes)
    return _nearest(candidates, current)


# ---------------------------------------------------------------------------
# Component routing
# ---------------------------------------------------------------------------

def _route_component(
    comp: nx.MultiGraph,
    current: tuple | None,
    pinned_start: tuple | None,
) -> tuple[nx.MultiGraph, list[tuple[tuple, tuple, int]]]:
    """
    Return (G_work, path) as (u, v, key) triples.
    G_work is the possibly-augmented graph; callers MUST use it for edge
    attribute lookup — augmented travel edges do not exist in the original comp.
    """
    odd = [n for n, d in comp.degree() if d % 2 == 1]
    G_work = _augment(comp) if len(odd) > 2 else comp

    source = _choose_source(G_work, current, pinned_start)

    residual_odd = [n for n, d in G_work.degree() if d % 2 == 1]
    if not residual_odd:
        path = list(nx.eulerian_circuit(G_work, source=source, keys=True))
    else:
        path = list(nx.eulerian_path(G_work, source=source, keys=True))

    return G_work, path


# ---------------------------------------------------------------------------
# Full-layer routing
# ---------------------------------------------------------------------------

def route_layer(
    layer: Layer,
    start: Vec2 | None = None,
    component_order: list[int] | None = None,
) -> list[PrintMove]:
    """
    Route all strands in layer, returning an ordered list of PrintMove objects.

    start            – optional starting position (nozzle park position)
    component_order  – optional list of component indices to visit first
    """
    G = build_graph(layer)
    raw_comps = [G.subgraph(c).copy() for c in nx.connected_components(G)]

    # Apply optional component ordering
    if component_order:
        indices = [i for i in component_order if i < len(raw_comps)]
        remaining = [i for i in range(len(raw_comps)) if i not in indices]
        ordered_comps = [raw_comps[i] for i in indices + remaining]
    else:
        ordered_comps = raw_comps

    moves: list[PrintMove] = []
    current_pos: tuple | None = _pt(start) if start else None
    pinned = _pt(start) if start else None

    for comp_idx, comp in enumerate(ordered_comps):
        # Only apply pinned start to the very first component
        pin = pinned if comp_idx == 0 else None

        G_work, path = _route_component(comp, current_pos, pin)
        if not path:
            continue

        first_node = path[0][0]
        if current_pos is not None and current_pos != first_node:
            moves.append(PrintMove(
                'travel', None, None,
                Vec2(*current_pos), Vec2(*first_node),
            ))

        for u, v, key in path:
            attrs = G_work[u][v][key]
            moves.append(PrintMove(
                attrs.get('kind', 'print'),
                attrs.get('strand_id'),
                attrs.get('seg_idx'),
                Vec2(*u), Vec2(*v),
            ))

        current_pos = path[-1][1]

    return moves


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(moves: list[PrintMove]) -> dict:
    print_dist = sum(m.length for m in moves if m.kind == 'print')
    travel_dist = sum(m.length for m in moves if m.kind == 'travel')
    retrace_dist = sum(m.length for m in moves if m.kind == 'retrace')

    print_runs = 0
    in_print = False
    for m in moves:
        if m.kind == 'print' and not in_print:
            print_runs += 1
            in_print = True
        elif m.kind != 'print':
            in_print = False

    travel_count = sum(1 for m in moves if m.kind == 'travel')
    total = print_dist + travel_dist + retrace_dist

    return {
        'print_distance': round(print_dist, 2),
        'travel_distance': round(travel_dist, 2),
        'retrace_distance': round(retrace_dist, 2),
        'print_runs': print_runs,
        'travel_moves': travel_count,
        'pct_printing': round(100 * print_dist / total, 1) if total > 0 else 0.0,
        'total_moves': len(moves),
    }


def graph_info(layer: Layer) -> dict:
    G = build_graph(layer)
    comps = list(nx.connected_components(G))
    odd = [n for n, d in G.degree() if d % 2 == 1]
    return {
        'node_count': G.number_of_nodes(),
        'edge_count': G.number_of_edges(),
        'component_count': len(comps),
        'odd_degree_nodes': len(odd),
        'is_eulerian': nx.is_eulerian(G),
        'has_eulerian_path': nx.has_eulerian_path(G) if G.number_of_edges() > 0 else False,
    }
