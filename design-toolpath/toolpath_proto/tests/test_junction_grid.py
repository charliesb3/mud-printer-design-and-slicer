"""
Junction search scale (2026-10-08, Chained Loop editing performance): the
candidate grid follows the geometry's own segment length and every pair is
tested once — the junctions found are EXACTLY those of a brute-force pairwise
search; the junction splits of one routing layer are reused by content.
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import graph as G
from geometry import Vec2, Strand, Layer


def _chain(n_loops, pitch, depth, sid='c', step=0.35, x0=0.0):
    """A dense self-crossing stroke (Chained Loop-like): figure-of-eight loops."""
    pts = []
    n = int(n_loops * pitch / step)
    for k in range(n + 1):
        t = k / n * n_loops * 2 * math.pi
        pts.append(Vec2(x0 + t / (2 * math.pi) * pitch + 0.6 * pitch * math.sin(t), depth * math.sin(2 * t)))
    return Strand(sid, 'wall', pts, False)


def _brute(layer):
    segs = [(st.id, i, len(st.segments()), st.closed, a, b) for st in layer.strands for i, (a, b) in enumerate(st.segments())]
    out = []
    for k1 in range(len(segs)):
        for k2 in range(k1 + 1, len(segs)):
            s1, i1, n1, c1, a, b = segs[k1]
            s2, i2, _, _, c, d = segs[k2]
            if s1 == s2 and (abs(i1 - i2) == 1 or (c1 and abs(i1 - i2) == n1 - 1)):
                continue
            rx, ry, qx, qy = b.x - a.x, b.y - a.y, d.x - c.x, d.y - c.y
            L1, L2 = math.hypot(rx, ry), math.hypot(qx, qy)
            cross = rx * qy - ry * qx
            if L1 < 1e-12 or L2 < 1e-12 or abs(cross) <= 1e-9 * L1 * L2:
                continue
            wx, wy = c.x - a.x, c.y - a.y
            t, u = (wx * qy - wy * qx) / cross, (wx * ry - wy * rx) / cross
            if G.JUNCTION_TOL < t * L1 < L1 - G.JUNCTION_TOL and G.JUNCTION_TOL < u * L2 < L2 - G.JUNCTION_TOL:
                p = (a.x + t * rx, a.y + t * ry)
                out += [((s1, i1), t, p), ((s2, i2), u, p)]
    return sorted(out)


def test_grid_crossings_equal_brute_force_on_dense_self_crossing_strokes():
    layer = Layer(0.0, [_chain(6, 12.0, 4.0), _chain(4, 9.0, 3.0, 'd', 0.5, 7.0),
                        Strand('w', 'wall', [Vec2(-5, -6), Vec2(90, 6)], False)])
    cell = G._grid_cell(layer)
    assert cell < G.GRID_MAX                         # dense: a finer grid than the 10 in default
    found = sorted(G._crossings(layer, cell))
    assert found and found == _brute(layer)
    assert sorted(G._crossings(layer, G.GRID_MAX)) == found      # any cell size: the same junctions


def test_junction_splits_are_reused_by_content_only():
    layer = Layer(0.0, [_chain(3, 12.0, 4.0)])
    G._SPLITS_CACHE.clear()
    a = G._junction_splits(layer)
    assert G._junction_splits(layer) is a                           # same content → reused
    assert a == G._junction_splits_uncached(layer)
    moved = Layer(0.0, [_chain(3, 12.5, 4.0)])                      # any change → recomputed
    assert G._junction_splits(moved) == G._junction_splits_uncached(moved) != a
