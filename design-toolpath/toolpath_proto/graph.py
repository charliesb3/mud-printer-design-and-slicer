"""
Print graph construction and continuity-first routing.

Printable geometry is modeled as an undirected multigraph:
  - nodes  = point coordinates (x, y)
  - edges  = printable segments (kind='print') or travel gaps (kind='travel')

A fully continuous print corresponds to an Eulerian traversal.

Objective, lexicographic:
  1. never create false printable connections (only real contact merges
     nodes: shared vertices, or a vertex lying on another segment),
  2. print every edge,
  3. minimise print runs / travel moves — travel happens ONLY between
     disconnected components,
  4. minimise travel distance,
  5. minimise retracing.

Within a connected component that is not Eulerian, odd-degree nodes are
paired by RETRACING existing printable edges along shortest in-graph paths
(route inspection / Chinese postman), leaving one pair as the trail's
start and end. The result is one continuous run per component; the
re-traversed edges are reported as 'retrace' moves.
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


JUNCTION_TOL = 1e-6     # a vertex this close to a segment touches it


def _junction_splits(layer: Layer) -> dict:
    """
    T-junctions: for each segment (strand_id, seg_idx), the vertices of any
    strand that lie on its INTERIOR. Geometry that physically meets in the
    middle of a segment (e.g. a lattice vertex on a wall) must share a graph
    node, or the router would see disconnected geometry. Only exact contact
    (within JUNCTION_TOL) qualifies — no gaps are bridged.
    """
    verts = {_pt(p) for st in layer.strands for p in st.points}
    cell = 10.0
    grid: dict = {}
    for v in verts:
        grid.setdefault((math.floor(v[0] / cell), math.floor(v[1] / cell)),
                        []).append(v)
    splits: dict = {}
    for st in layer.strands:
        for idx, (a, b) in enumerate(st.segments()):
            dx, dy = b.x - a.x, b.y - a.y
            L2 = dx * dx + dy * dy
            if L2 < 1e-18:
                continue
            L = math.sqrt(L2)
            x0 = math.floor((min(a.x, b.x) - JUNCTION_TOL) / cell)
            x1 = math.floor((max(a.x, b.x) + JUNCTION_TOL) / cell)
            y0 = math.floor((min(a.y, b.y) - JUNCTION_TOL) / cell)
            y1 = math.floor((max(a.y, b.y) + JUNCTION_TOL) / cell)
            hits = []
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    for v in grid.get((gx, gy), ()):
                        t = ((v[0] - a.x) * dx + (v[1] - a.y) * dy) / L2
                        if t * L <= JUNCTION_TOL or (1 - t) * L <= JUNCTION_TOL:
                            continue
                        if abs((v[0] - a.x) * dy - (v[1] - a.y) * dx) / L \
                                <= JUNCTION_TOL:
                            hits.append((t, v))
            if hits:
                splits[(st.id, idx)] = [v for _, v in sorted(hits)]
    return splits


def build_graph(layer: Layer) -> nx.MultiGraph:
    G = nx.MultiGraph()
    splits = _junction_splits(layer)
    for strand in layer.strands:
        for idx, (a, b) in enumerate(strand.segments()):
            chain = [_pt(a)] + splits.get((strand.id, idx), []) + [_pt(b)]
            for sub, (u, v) in enumerate(zip(chain, chain[1:])):
                G.add_edge(
                    u, v,
                    strand_id=strand.id,
                    seg_idx=idx,
                    sub_idx=sub,
                    kind='print',
                    length=round(_euclid(u, v), 6),
                )
    return G


# ---------------------------------------------------------------------------
# Augmentation (Chinese Postman / Route Inspection)
# ---------------------------------------------------------------------------

def _euclid(u: tuple, v: tuple) -> float:
    return math.sqrt((u[0] - v[0]) ** 2 + (u[1] - v[1]) ** 2)


def _augment_by_retrace(G: nx.MultiGraph,
                        pinned: tuple | None = None) -> nx.MultiGraph:
    """
    Route inspection for an OPEN trail on one connected component.

    Pair odd-degree nodes by minimum total shortest-path length THROUGH the
    printable graph and duplicate those edges (to be re-traversed while
    still printing). Exactly two odd nodes are left unpaired — the trail's
    start and end — via two zero-cost dummy terminals in the matching. If
    a pinned start is odd it is forced to be one of them.

    No edge is added that is not already printable geometry: retracing
    never jumps across a gap or an opening.
    """
    odd = [n for n, d in G.degree() if d % 2 == 1]
    if len(odd) <= 2:
        return G
    dist, paths = {}, {}
    for u in odd:
        d, p = nx.single_source_dijkstra(G, u, weight='length')
        dist[u], paths[u] = d, p
    H = nx.Graph()
    for i, u in enumerate(odd):
        for v in odd[i + 1:]:
            H.add_edge(u, v, weight=dist[u][v])
    ends = ('__end_a__', '__end_b__')
    for v in odd:
        if pinned in odd:
            if v == pinned:
                H.add_edge(ends[0], v, weight=0.0)
            else:
                H.add_edge(ends[1], v, weight=0.0)
        else:
            H.add_edge(ends[0], v, weight=0.0)
            H.add_edge(ends[1], v, weight=0.0)
    matching = nx.min_weight_matching(H)

    aug = G.copy()
    for u, v in matching:
        if u in ends or v in ends:
            continue
        route = paths[u][v]
        for x, y in zip(route, route[1:]):
            key = min(G[x][y], key=lambda k: G[x][y][k]['length'])
            aug.add_edge(x, y, **G[x][y][key])
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
    G_work = _augment_by_retrace(comp, pinned_start)

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

    return label_passes(moves)


def label_passes(moves: list[PrintMove]) -> list[PrintMove]:
    """First traversal of each printable edge is 'print'; any later
    traversal of the same edge is 'retrace'. Call again after reordering
    (e.g. reversing) a route."""
    seen = set()
    out = []
    for m in moves:
        if m.kind == 'travel':
            out.append(m)
            continue
        ends = tuple(sorted(((m.start.x, m.start.y), (m.end.x, m.end.y))))
        key = (m.strand_id, m.seg_idx, ends)
        kind = 'retrace' if key in seen else 'print'
        seen.add(key)
        out.append(m if m.kind == kind else
                   PrintMove(kind, m.strand_id, m.seg_idx, m.start, m.end))
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(moves: list[PrintMove]) -> dict:
    print_dist = sum(m.length for m in moves if m.kind == 'print')
    travel_dist = sum(m.length for m in moves if m.kind == 'travel')
    retrace_dist = sum(m.length for m in moves if m.kind == 'retrace')

    # A run is continuous extrusion: printing and retracing, broken only by
    # travel.
    print_runs = 0
    in_run = False
    for m in moves:
        if m.kind != 'travel' and not in_run:
            print_runs += 1
            in_run = True
        elif m.kind == 'travel':
            in_run = False

    travel_count = sum(1 for m in moves if m.kind == 'travel')
    total = print_dist + travel_dist + retrace_dist

    return {
        'print_distance': round(print_dist, 2),
        'travel_distance': round(travel_dist, 2),
        'retrace_distance': round(retrace_dist, 2),
        'print_runs': print_runs,
        'travel_moves': travel_count,
        'retrace_moves': sum(1 for m in moves if m.kind == 'retrace'),
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
