"""
Print graph construction and continuity-first routing.

Printable geometry is modeled as an undirected multigraph:
  - nodes  = point coordinates (x, y)
  - edges  = printable segments (kind='print'); augmentation may add
    travel edges (kind='travel', see below)

A fully continuous print corresponds to an Eulerian traversal.

Objective, lexicographic:
  1. never create false printable connections (only real contact merges
     nodes: shared vertices, a vertex lying on another segment, or two
     segments crossing — except hidden 'field' strands crossing hidden
     geometry, which pass over each other),
  2. print every edge,
  3. minimise print runs / travel moves — travel happens between
     disconnected components, and inside a component only to pair odd
     ends of `travel_pairing` strands (solid infill),
  4. minimise travel distance,
  5. minimise retracing.

Within a connected component that is not Eulerian, odd-degree nodes are
paired by RETRACING existing printable edges along the cheapest in-graph
paths (cost = length × the strand's retrace_cost; route inspection /
Chinese postman), leaving one pair as the trail's start and end. A pair
involving a `travel_pairing` strand is joined by a TRAVEL edge instead
when that is shorter. Re-traversed edges are reported as 'retrace' moves.
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
    Junctions inside segments: for each segment (strand_id, seg_idx), the
    points where other geometry meets its INTERIOR — vertices of any strand
    lying on it (T junctions, e.g. a lattice vertex on a wall) and points
    where another segment crosses it (X junctions). Geometry that
    physically meets must share a graph node, or the router would see
    disconnected geometry. Only exact contact (within JUNCTION_TOL)
    qualifies — no gaps are bridged.
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
                splits[(st.id, idx)] = hits
    for key, t, p in _crossings(layer):
        splits.setdefault(key, []).append((t, p))
    out = {}
    for key, hits in splits.items():
        seq = []
        for _, v in sorted(hits):
            if not seq or _euclid(seq[-1], v) > JUNCTION_TOL:
                seq.append(v)
        out[key] = seq
    return out


def _crossings(layer: Layer):
    """
    X junctions: two segments crossing in both their interiors physically
    meet there, so both are split at a shared node. Yields
    ((strand_id, seg_idx), t, point) for each side of each crossing.

    Exception — structural crossings: where a hidden FIELD strand (infill,
    lattice, return path) crosses another hidden strand (field or
    internal), the strands simply pass over each other; that is not a
    routing junction.
    """
    segs = []
    kind_of = {}
    for st in layer.strands:
        kind_of[st.id] = getattr(st, 'kind', 'face')
        n_seg = len(st.segments())
        for idx, (a, b) in enumerate(st.segments()):
            segs.append((st.id, idx, n_seg, st.closed, a, b))
    cell = 10.0
    grid: dict = {}
    for k, (_, _, _, _, a, b) in enumerate(segs):
        for gx in range(math.floor(min(a.x, b.x) / cell),
                        math.floor(max(a.x, b.x) / cell) + 1):
            for gy in range(math.floor(min(a.y, b.y) / cell),
                            math.floor(max(a.y, b.y) / cell) + 1):
                grid.setdefault((gx, gy), []).append(k)
    seen = set()
    for cands in grid.values():
        for i in range(len(cands)):
            for j in range(i + 1, len(cands)):
                k1, k2 = min(cands[i], cands[j]), max(cands[i], cands[j])
                if (k1, k2) in seen:
                    continue
                seen.add((k1, k2))
                s1, i1, n1, c1, a, b = segs[k1]
                s2, i2, _, _, c, d = segs[k2]
                if s1 == s2 and (abs(i1 - i2) == 1 or
                                 (c1 and abs(i1 - i2) == n1 - 1)):
                    continue                     # consecutive: share a vertex
                if not _crossing_is_junction(kind_of[s1], kind_of[s2]):
                    continue
                rx, ry = b.x - a.x, b.y - a.y
                qx, qy = d.x - c.x, d.y - c.y
                L1, L2 = math.hypot(rx, ry), math.hypot(qx, qy)
                cross = rx * qy - ry * qx
                if L1 < 1e-12 or L2 < 1e-12 or abs(cross) <= 1e-9 * L1 * L2:
                    continue
                wx, wy = c.x - a.x, c.y - a.y
                t = (wx * qy - wy * qx) / cross
                u = (wx * ry - wy * rx) / cross
                if JUNCTION_TOL < t * L1 < L1 - JUNCTION_TOL and \
                        JUNCTION_TOL < u * L2 < L2 - JUNCTION_TOL:
                    p = (a.x + t * rx, a.y + t * ry)
                    yield (s1, i1), t, p
                    yield (s2, i2), u, p


def _crossing_is_junction(k1: str, k2: str) -> bool:
    hidden = {'internal', 'field'}
    return not (k1 in hidden and k2 in hidden and 'field' in (k1, k2))


def build_graph(layer: Layer) -> nx.MultiGraph:
    G = nx.MultiGraph()
    splits = _junction_splits(layer)
    for strand in layer.strands:
        factor = getattr(strand, 'retrace_cost', 1.0)
        for idx, (a, b) in enumerate(strand.segments()):
            chain = [_pt(a)] + splits.get((strand.id, idx), []) + [_pt(b)]
            for sub, (u, v) in enumerate(zip(chain, chain[1:])):
                if u == v:
                    continue
                length = round(_euclid(u, v), 6)
                G.add_edge(
                    u, v,
                    strand_id=strand.id,
                    seg_idx=idx,
                    sub_idx=sub,
                    kind='print',
                    length=length,
                    cost=length * factor,
                    travel_pairing=getattr(strand, 'travel_pairing', False),
                )
    return G


# ---------------------------------------------------------------------------
# Augmentation (Chinese Postman / Route Inspection)
# ---------------------------------------------------------------------------

def _euclid(u: tuple, v: tuple) -> float:
    return math.sqrt((u[0] - v[0]) ** 2 + (u[1] - v[1]) ** 2)


def _cost(u, v, attrs) -> float:
    """Retrace cost of an edge (dijkstra weight; also handles the
    multigraph's {key: attrs} form)."""
    if 'length' not in attrs:
        return min(_cost(u, v, a) for a in attrs.values())
    return attrs.get('cost', attrs['length'])


TRAVEL_W = 1.0   # for odd ends of travel_pairing strands (area infill): a
                 # pair is joined by a TRAVEL hop (a new run inside the
                 # component) when that is shorter than its weighted retrace.
                 # Every other odd pair keeps the retrace behaviour.


def _augment_by_retrace(G: nx.MultiGraph,
                        pinned: tuple | None = None) -> nx.MultiGraph:
    """
    Route inspection for an OPEN trail on one connected component.

    Pair odd-degree nodes by minimum total shortest-path COST THROUGH the
    printable graph and duplicate those edges (to be re-traversed while
    still printing). Cost = length × the strand's retrace_cost, so the
    router prefers retracing internal geometry over visible wall faces. Exactly two odd nodes are left unpaired — the trail's
    start and end — via two zero-cost dummy terminals in the matching. If
    a pinned start is odd it is forced to be one of them.

    No printed edge is added that is not already printable geometry:
    retracing never jumps across a gap or an opening. A pair whose retrace
    would cost more than TRAVEL_W × its straight distance is joined by a
    TRAVEL edge instead (not printed).
    """
    odd = [n for n, d in G.degree() if d % 2 == 1]
    if len(odd) <= 2:
        return G
    dist, paths = {}, {}
    for u in odd:
        d, p = nx.single_source_dijkstra(G, u, weight=_cost)
        dist[u], paths[u] = d, p
    H = nx.Graph()
    by_travel = set()
    tp = {n for x, y, d in G.edges(data=True) if d.get('travel_pairing') for n in (x, y)}
    for i, u in enumerate(odd):
        for v in odd[i + 1:]:
            hop = TRAVEL_W * _euclid(u, v)
            w = dist[u].get(v, float('inf'))
            if (u in tp or v in tp) and hop < w:
                by_travel.add((u, v))
                by_travel.add((v, u))
                w = hop
            H.add_edge(u, v, weight=w)
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
        if (u, v) in by_travel:
            aug.add_edge(u, v, kind='travel', strand_id=None, seg_idx=None,
                         length=_euclid(u, v), cost=0.0)
            continue
        route = paths[u][v]
        for x, y in zip(route, route[1:]):
            key = min(G[x][y], key=lambda k: _cost(x, y, G[x][y][k]))
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


# ---------------------------------------------------------------------------
# Route ends — what a future multi-layer planner needs from one layer
# ---------------------------------------------------------------------------

from dataclasses import dataclass as _dataclass


@_dataclass
class RouteEnds:
    """
    Where a layer's continuous extrusion starts and ends. A layer planner
    minimises non-printing movement over the WHOLE print: a closed route
    (start == end) lets layer N+1 start right above where N ended; an open
    route A→B can alternate (N: A→B, N+1: B→A) with no XY travel either.
    """
    start: tuple
    end: tuple
    closed: bool

    def to_dict(self) -> dict:
        return {'start': list(self.start), 'end': list(self.end),
                'closed': self.closed}


def route_ends(moves: list[PrintMove]) -> RouteEnds | None:
    if not moves:
        return None
    s, e = moves[0].start, moves[-1].end
    return RouteEnds((s.x, s.y), (e.x, e.y),
                     _euclid((s.x, s.y), (e.x, e.y)) < 1e-6)


def reverse_route(moves: list[PrintMove]) -> list[PrintMove]:
    """The same route printed backwards (alternating-layer strategy)."""
    return label_passes([PrintMove(m.kind, m.strand_id, m.seg_idx, m.end, m.start)
                         for m in reversed(moves)])


def orient_for_previous(moves: list[PrintMove], prev_end: tuple | None):
    """
    Orient a layer route to follow a previous layer that ended at prev_end:
    forward or reversed, whichever starts nearer. Returns
    (moves, XY transition distance). Closed routes need no reversal when
    they already start at prev_end.
    """
    if not moves or prev_end is None:
        return moves, 0.0
    ends = route_ends(moves)
    d_fwd = _euclid(prev_end, ends.start)
    d_rev = _euclid(prev_end, ends.end)
    if d_rev < d_fwd - 1e-9:
        return reverse_route(moves), d_rev
    return moves, d_fwd
