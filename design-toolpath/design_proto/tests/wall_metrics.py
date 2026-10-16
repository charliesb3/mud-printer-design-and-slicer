"""
WALL-LATTICE GEOMETRIC QUALITY — a TEST oracle (moved out of production in
the 2026-10-16 cleanup: no production caller; tests/test_wall_lattice.py,
test_pass7.py, test_pass8.py and test_wall_regions.py measure the produced
geometry and route with it). It uses route_quality's shared primitives
(_SegGrid, congestion, geometric_retrace) and its production constants.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from route_quality import (CLEARANCE_RADIUS, TINY_CELL, TINY_LOOP_GEN, _SegGrid,   # noqa: E402
                           congestion, geometric_retrace)

FACE_CORNER = 60.0   # degrees: a boundary turn this sharp separates two faces
CROSS_ARC = 2.0      # a stitch whose ends are this much farther apart along
                     # the wall boundary than in a straight line crosses the
                     # wall; otherwise it runs face → same face


def wall_metrics(layer, target=None, rho=CLEARANCE_RADIUS):
    """
    Geometric quality of the WALL lattice of a layer (measured on the
    produced geometry and route, whatever generated it):
      interior_retrace   printed length over an already printed generated
                         (field) bead
      stitches / cross_ratio   generated strands split at their landings on
                         the wall boundary; share (by length) of stitches
                         that cross the wall (different faces) rather than
                         return to the same face
      tiny_cells         cells of the printed arrangement inside the wall,
                         touching generated beads, smaller than
                         TINY_CELL × thickness × target spacing
      start_end_distance, runs, travel, print length, congestion, degree.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto'))
    from graph import route_layer, compute_metrics, route_ends
    import network as N
    paths, meta = layer._build_effective()
    rl = layer.to_routing_layer(paths, meta)
    moves = route_layer(rl)
    m = compute_metrics(moves)
    kind = {s.id: s.kind for s in rl.strands}
    # interior retrace: overlap of printed moves with printed FIELD moves
    pm = [mv for mv in moves if mv.kind != 'travel' and mv.length > 1e-9]
    retr = 0.0
    seen = []
    for mv in pm:
        a, b = (mv.start.x, mv.start.y), (mv.end.x, mv.end.y)
        L = math.dist(a, b)
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        for (c, d, is_field) in seen:
            if not (is_field or kind.get(mv.strand_id) == 'field'):
                continue
            if abs((c[0] - a[0]) * uy - (c[1] - a[1]) * ux) > 1e-6 or \
                    abs((d[0] - a[0]) * uy - (d[1] - a[1]) * ux) > 1e-6:
                continue
            t0 = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
            t1 = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
            retr += max(0.0, min(L, max(t0, t1)) - max(0.0, min(t0, t1)))
        seen.append((a, b, kind.get(mv.strand_id) == 'field'))
    # material rings (after rounding) and thickness estimate
    rings = []
    area = perim = 0.0
    for r in meta['network'].get('regions', []):
        if not r.get('material'):
            continue
        for ring in [r['outer']] + list(r['holes']):
            rr = [tuple(q) for q in ring]
            rings.append(rr)
            perim += sum(math.dist(rr[i], rr[(i + 1) % len(rr)]) for i in range(len(rr)))
        o = r['outer']
        area += abs(sum(o[i][0] * o[(i + 1) % len(o)][1] - o[(i + 1) % len(o)][0] * o[i][1]
                        for i in range(len(o)))) / 2
        for h in r['holes']:
            area -= abs(sum(h[i][0] * h[(i + 1) % len(h)][1] - h[(i + 1) % len(h)][0] * h[i][1]
                            for i in range(len(h)))) / 2
    thick = 2 * area / perim if perim > 0 else 0.0
    if target is None:
        sp = [float(f.params.get('spacing', 20.0)) for f in layer.infills]
        target = sp[0] if sp else 20.0
    # stitches: generated strands split where they land on printed
    # (non-generated) geometry; a stitch stays on ONE face if the printed
    # boundary between its ends is short and nearly straight
    import networkx as nx
    user_segs = [((a.x, a.y), (b.x, b.y)) for s in rl.strands if s.kind != 'field'
                 for a, b in s.segments()]
    UG = nx.Graph()
    for u, v in user_segs:
        UG.add_edge(u, v, weight=math.dist(u, v))
    ugrid = _SegGrid(user_segs, 8.0)

    def on_user(p):
        for k in ugrid.near(p, 1e-6):
            u, v = user_segs[k]
            L = math.dist(u, v)
            if L < 1e-12:
                continue
            t = ((p[0] - u[0]) * (v[0] - u[0]) + (p[1] - u[1]) * (v[1] - u[1])) / (L * L)
            if -1e-9 <= t <= 1 + 1e-9 and \
                    abs((p[0] - u[0]) * (v[1] - u[1]) - (p[1] - u[1]) * (v[0] - u[0])) / L < 1e-6:
                return u, v
        return None

    def one_face(a, b):
        sa, sb = on_user(a), on_user(b)
        if not sa or not sb:
            return False
        chord = math.dist(a, b)
        added = []
        for p_, (u, v) in ((a, sa), (b, sb)):
            if p_ not in UG:
                UG.add_edge(p_, u, weight=math.dist(p_, u))
                UG.add_edge(p_, v, weight=math.dist(p_, v))
                added.append(p_)
        try:
            d, path = nx.single_source_dijkstra(UG, a, b, cutoff=CROSS_ARC * chord + 1e-9)
        except nx.NetworkXNoPath:
            path = None
        finally:
            for p_ in added:
                UG.remove_node(p_)
        if not path:
            return False
        turn = 0.0
        for p0, p1, p2 in zip(path, path[1:], path[2:]):
            v1 = (p1[0] - p0[0], p1[1] - p0[1])
            v2 = (p2[0] - p1[0], p2[1] - p1[1])
            l1, l2 = math.hypot(*v1), math.hypot(*v2)
            if l1 > 1e-12 and l2 > 1e-12:
                turn += math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2))))
        # ONE face = no corner between the two ends (faces are separated by
        # corners of ≥ FACE_CORNER: a leg from a side face to the wall's end
        # face, or across a wall corner, ties two different faces)
        return turn < math.radians(FACE_CORNER)

    gen_polys = [[(q.x, q.y) for q in s.points] for s in rl.strands if s.kind == 'field']
    cross_len = same_len = 0.0
    n_cross = n_same = 0
    for poly in gen_polys:
        cur = [poly[0]]
        for i, q in enumerate(poly[1:], 1):
            cur.append(q)
            if on_user(q) is not None or i == len(poly) - 1:
                L = sum(math.dist(x, y) for x, y in zip(cur, cur[1:]))
                if one_face(cur[0], cur[-1]):
                    same_len += L; n_same += 1
                else:
                    cross_len += L; n_cross += 1
                cur = [q]
    # tiny cells of the printed arrangement
    beads = []
    for s in rl.strands:
        pts = [q for q in s.points]
        if len(pts) >= 2:
            beads.append(N.Bead(s.id, pts, s.closed, N.FACE if s.kind != 'field' else N.INTERNAL,
                                'G' if s.kind == 'field' else 'U'))
    tiny = 0
    tiny_at = []
    smallest = math.inf
    if beads and thick > 0:
        arr = N.Arrangement(beads)
        gen_len = {}                       # face id → its generated perimeter
        for bi, bd in enumerate(beads):
            if bd.element != 'G':
                continue
            for u, v in arr.bead_edges[bi]:
                L = arr.nodes[u].dist(arr.nodes[v])
                for fid in set(arr.edge_faces(u, v)):
                    gen_len[fid] = gen_len.get(fid, 0.0) + L
        lim = TINY_CELL * thick * target
        for f in arr.faces[1:]:
            if f.id not in gen_len or not f.outer:
                continue
            per = sum(f.outer[i].dist(f.outer[(i + 1) % len(f.outer)]) for i in range(len(f.outer)))
            # a corrective LOOP is enclosed mostly by generated beads (bow
            # tie, diamond, box); a small gap between a lattice turn and
            # the wall face is not
            if gen_len[f.id] < TINY_LOOP_GEN * per:
                continue
            smallest = min(smallest, f.area)
            if f.area < lim:
                tiny += 1
                tiny_at.append((round(sum(q.x for q in f.outer) / len(f.outer), 2),
                                round(sum(q.y for q in f.outer) / len(f.outer), 2)))
    gen, user = [], []
    for s in rl.strands:
        segs = [((a.x, a.y), (b.x, b.y)) for a, b in s.segments()]
        (gen if s.kind == 'field' else user).extend(segs)
    cg = congestion(gen, user, rho)
    ends = route_ends(moves)
    return {
        'runs': m['print_runs'], 'travel_moves': m['travel_moves'],
        'travel_length': round(m['travel_distance'], 2),
        'print_length': round(m['print_distance'], 2),
        'start_end_distance': round(math.dist(ends.start, ends.end), 2) if ends else 0.0,
        'interior_retrace': round(retr, 3),
        'exact_retrace': round(geometric_retrace(moves), 3),
        'stitches': n_cross + n_same, 'same_face_stitches': n_same,
        'cross_ratio': round(cross_len / (cross_len + same_len), 3) if cross_len + same_len else 1.0,
        'tiny_cells': tiny, 'tiny_at': tiny_at,
        'smallest_cell': round(smallest, 2) if smallest < math.inf else None,
        'thickness': round(thick, 2),
        'congestion_max_generated': cg['max_generated'],
        'congestion_hotspots': cg['hotspots'],
        'generated_length': round(sum(math.dist(a, b) for a, b in gen), 1),
    }
