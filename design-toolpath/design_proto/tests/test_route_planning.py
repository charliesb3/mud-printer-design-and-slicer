"""
ROUTE START + TRAVEL ORDER (2026-10-07, toolpath_proto/graph.py):
  * a closed component's DEFAULT start (its seam) hides in the wall: web /
    lattice / internal first, wall-system paths, inner skins, exterior
    skins / caps / corners last — weighed against travel;
  * disconnected components are ordered (nearest neighbour from every
    start + 2-opt) and open ones oriented to minimise travel;
  * a Route Origin (manual seam) and a manual start stay constraints.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app  # noqa: F401,E402  (path setup)
import graph as G                                  # noqa: E402
import physical_fixtures as PF                     # noqa: E402
import wall_fixtures as WF                         # noqa: E402
from model import Vec2, PrintLayer, RectanglePath, WallSystem   # noqa: E402


def _routed(L, **kw):
    p, m = L._build_effective()
    rl = L.to_routing_layer(p, m)
    return rl, G.route_layer(rl, allow_retrace=False, **kw)


def test_the_default_seam_hides_in_the_web():
    """Skin + Web: the closed route starts on the web lattice, not on a
    visible skin or corner."""
    rl, mv = _routed(PF._phys(WF.one_void()))
    strands = {s.id: s for s in rl.strands}
    gr = G.build_graph(rl)
    assert G._node_exposure(gr, (mv[0].start.x, mv[0].start.y), strands) == 0.0
    assert strands[mv[0].strand_id].kind == 'field' or strands[mv[0].strand_id].role in G._CONCEALED_ROLES


def test_skins_only_prefer_the_inner_skin():
    R = RectanglePath(100, 100, 120, 80, id='R')
    L = PF._phys(PrintLayer('t', source_paths=[R]))
    L.wall_systems = [WallSystem('W', 'hollow', {}, ['R'], thickness=8)]
    rl, mv = _routed(L)
    strands = {s.id: s for s in rl.strands}
    assert strands[mv[0].strand_id].role != 'outer'


def _scatter(n, seed):
    rnd = random.Random(seed)
    ps = [RectanglePath(rnd.uniform(0, 600), rnd.uniform(0, 600), 60, 40, id=f'R{k}') for k in range(n)]
    L = PF._phys(PrintLayer('t', source_paths=ps))
    L.wall_systems = [WallSystem('W', 'hollow', {}, [p.id for p in ps], thickness=8)]
    return L


def _nn_travel(rl):
    """Travel of the plain nearest-neighbour order (the previous rule)."""
    gr = G.build_graph(rl)
    comps = [gr.subgraph(c) for c in __import__('networkx').connected_components(gr)]
    reps = [list(c.nodes) for c in comps]
    cur, left, tot = reps[0][0], list(range(1, len(reps))), 0.0
    while left:
        k = min(left, key=lambda i: min(G._euclid(cur, n) for n in reps[i]))
        n = min(reps[k], key=lambda q: G._euclid(cur, q))
        tot += G._euclid(cur, n)
        cur = n
        left.remove(k)
    return tot


def test_component_order_reduces_travel():
    for seed in (1, 2, 3):
        rl, mv = _routed(_scatter(12, seed))
        mt = G.compute_metrics(mv)
        assert mt['travel_distance'] <= _nn_travel(rl) + 1e-6, seed


def test_every_component_is_printed_once_and_closed():
    rl, mv = _routed(_scatter(10, 4))
    mt = G.compute_metrics(mv)
    assert mt['retrace_distance'] == 0
    assert mt['print_runs'] == len(list(__import__('networkx').connected_components(G.build_graph(rl))))


def test_a_route_origin_stays_the_seam():
    """The user's Route Origin wins over the default seam."""
    L = _scatter(4, 5)
    p, m = L._build_effective()
    rl = L.to_routing_layer(p, m)
    s = rl.strands[0]
    o = s.points[1]
    mv = G.route_layer(rl, allow_retrace=False, origins=[o])
    comp_moves = [mv_ for mv_ in mv if mv_.strand_id and any(st.id == mv_.strand_id for st in rl.strands)]
    starts = [m_.start for m_ in mv if m_.kind == 'print']
    assert any(abs(q.x - o.x) < 1e-9 and abs(q.y - o.y) < 1e-9 for q in starts)
    # the origin's component begins and ends there
    gr = G.build_graph(rl)
    comp = next(c for c in __import__('networkx').connected_components(gr) if (o.x, o.y) in c)
    seq = [m_ for m_ in mv if m_.kind == 'print' and (m_.start.x, m_.start.y) in comp]
    assert (seq[0].start.x, seq[0].start.y) == (o.x, o.y) and (seq[-1].end.x, seq[-1].end.y) == (o.x, o.y)


def test_a_manual_start_position_is_honoured():
    L = _scatter(6, 6)
    p, m = L._build_effective()
    rl = L.to_routing_layer(p, m)
    from geometry import Vec2 as RV
    far = RV(1000.0, 1000.0)
    mv = G.route_layer(rl, allow_retrace=False, start=far)
    first = next(m_ for m_ in mv if m_.kind == 'print')
    gr = G.build_graph(rl)
    nearest = min(gr.nodes, key=lambda n: G._euclid((far.x, far.y), n))
    comp = next(c for c in __import__('networkx').connected_components(gr) if nearest in c)
    assert (first.start.x, first.start.y) in comp          # begins at the component nearest the start
