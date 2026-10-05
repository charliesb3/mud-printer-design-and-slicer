"""
Route planning — make the layer continuous by LOCALLY editing the infill
field, instead of retracing or inventing long return beads.

Model
-----
Every wall region with infill has a WEB (infill.Web): sample points on the
wall faces and inside the wall, and every valid strut between Delaunay
neighbours. The printed infill is a selection S of those struts
(infill.select: degree-capped, at most one generated path through a
wall-face point and two through an interior point).

A continuous extrusion needs every junction even, except the route's two
ends. Odd junctions ("defects") come from fans, junction zones, branch
ends, … The repair edits S only by TOGGLING web struts:

    remove a printed strut          (a bay grows)
    add a neighbouring web strut    (a bay is split / a short support)

A toggle flips the parity of both its ends, so a chain of toggles from
defect u to defect v fixes exactly u and v. Alternating remove / add along
the chain leaves every interior point's strut count unchanged — that is a
local PHASE SHIFT of the zigzag between u and v, the preferred edit.
Same-type steps change a point's strut count by 2 and are penalised; no
step may exceed the point's degree cap (no generated knots).

Cost of a chain = Σ strut length × (ADD_W | REMOVE_W) + same-type
penalties. Chains costing more than LOCAL_LIMIT pitches are not
considered: a defect is never fixed by an edit that wanders across the
wall. Defects are paired by min-weight matching; with two free "route end"
slots the layer is an open route (A→B, alternating layer to layer); fully
paired it is a closed loop (start = end), chosen when that costs
≤ CLOSE_MAX_EXTRA of the print length more.

Defects that cannot be fixed locally (not in any web, or no local partner)
stay as route ends; beyond two per component the router's exact retrace is
the last resort. Every correction is recorded (defects, length added /
removed, longest added strut, farthest edit from its defects) for the
route-quality diagnostics.
"""
from __future__ import annotations
import heapq
import math
from dataclasses import dataclass, field

import networkx as nx

ADD_W = 1.0            # adding a strut deposits mud
REMOVE_W = 0.8         # removing one weakens a bay a little
DEGREE_PENALTY = 0.5   # × pitch, for a same-type step (strut count ±2)
LOCAL_LIMIT = 3.0      # × pitch: max cost of a local correction (tier 1)
PHASE_LIMIT = 20.0     # × pitch: tier 2 — pure phase shift only (strut
                       # counts unchanged everywhere along the chain)
CLOSE_MAX_EXTRA = 0.05
CROWD = 0.25           # × pitch: a new landing point keeps this clear of others
SUPPORT_MAX = 3.0      # × pitch: longest local support (straight or bent)
SUPPORT_W = 1.2        # a new strut outside the web pattern costs a little more


@dataclass
class PlanReport:
    defects: int = 0
    repaired: int = 0                 # pairs fixed by local edits
    route_end_defects: int = 0
    left_to_retrace: int = 0          # defects beyond the route ends
    corrections: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    returns: list = field(default_factory=list)   # tier-3 strands [[Vec2]]


def _toggle_paths(web, S, deg, src, limit, alternating_only=False):
    """
    Cheapest alternating toggle chains from web point src: returns
    {target: (cost, [edges])}. State = (point, type of the step that
    arrived) so same-type steps can be penalised / capped.
    """
    adj = {}
    for e in web.edges:
        adj.setdefault(e[0], []).append(e)
        adj.setdefault(e[1], []).append(e)
    pen = DEGREE_PENALTY * web.pitch
    start = (src, None)
    best = {start: 0.0}
    prev = {start: None}
    heap = [(0.0, 0, src, None)]
    out = {}
    tie = 0
    while heap:
        c, _, n, t_in = heapq.heappop(heap)
        if c > best.get((n, t_in), math.inf) + 1e-12:
            continue
        if n != src and t_in is not None:
            d = 1 if t_in == 'add' else -1          # endpoint change at n
            if 0 <= deg.get(n, 0) + d <= web.cap(n):
                if n not in out or c < out[n][0]:
                    out[n] = (c, (n, t_in))
        for e in adj.get(n, ()):
            m = e[1] if e[0] == n else e[0]
            t = 'remove' if e in S else 'add'
            step = web.length(e) * (REMOVE_W if t == 'remove' else ADD_W)
            if t_in is None:                       # leaving the defect
                d = 1 if t == 'add' else -1
                if not 0 <= deg.get(n, 0) + d <= web.cap(n):
                    continue
            elif t == t_in:                        # same type: count ±2 at n
                if alternating_only:
                    continue
                d = 2 if t == 'add' else -2
                if not 0 <= deg.get(n, 0) + d <= web.cap(n):
                    continue
                step += pen
            nc = c + step
            if nc > limit:
                continue
            key = (m, t)
            if nc < best.get(key, math.inf) - 1e-12:
                best[key] = nc
                prev[key] = ((n, t_in), e)
                tie += 1
                heapq.heappush(heap, (nc, tie, m, t))
    res = {}
    for tgt, (c, state) in out.items():
        edges = []
        s = state
        while prev.get(s) is not None:
            s, e = prev[s]
            edges.append(e)
        res[tgt] = (c, edges)
    return res


def _match(nodes, cost, ends):
    H = nx.Graph()
    H.add_nodes_from(nodes)
    for (u, v), c in cost.items():
        H.add_edge(u, v, weight=c)
    for k in range(ends):
        for u in nodes:
            H.add_edge(('__end__', k), u, weight=0.0)
    if H.number_of_edges() == 0:
        return []
    real = lambda x: not (isinstance(x, tuple) and x and x[0] == '__end__')
    return [(u, v) for u, v in nx.min_weight_matching(H) if real(u) and real(v)]


def _key(u, v):
    return (u, v) if str(u) < str(v) else (v, u)


def repair(webs, fixed_graph, fixed_segs, prefer_closed=True):
    """
    webs: [(web, S)] — S is edited in place. fixed_graph: routing graph of
    everything except the region infill (faces, caps, walls, wires, legacy
    lattice). fixed_segs: those segments (to attach web points lying on
    them). Returns a PlanReport.
    """
    rep = PlanReport()
    uf = {}

    def find(x):
        uf.setdefault(x, x)
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            uf[max(ra, rb, key=str)] = min(ra, rb, key=str)

    for comp in nx.connected_components(fixed_graph):
        comp = list(comp)
        for n in comp[1:]:
            union(comp[0], n)
    grid = {}
    cell = 8.0
    for a, b in fixed_segs:
        for gx in range(math.floor(min(a[0], b[0]) / cell), math.floor(max(a[0], b[0]) / cell) + 1):
            for gy in range(math.floor(min(a[1], b[1]) / cell), math.floor(max(a[1], b[1]) / cell) + 1):
                grid.setdefault((gx, gy), []).append((a, b))

    def fixed_degree(p):
        """Degree the fixed geometry gives point p (node, or T on a bead)."""
        if p in fixed_graph:
            return fixed_graph.degree(p), p
        for a, b in grid.get((math.floor(p[0] / cell), math.floor(p[1] / cell)), ()):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 < 1e-18:
                continue
            t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
            if 0 < t < 1 and math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy) < 1e-6:
                return 2, a
        return 0, None

    fdegs = []
    for wi, (web, S) in enumerate(webs):
        fdeg = []
        for k, p in enumerate(web.pts):
            d, anchor = fixed_degree((p.x, p.y))
            fdeg.append(d)
            if anchor is not None:
                union(('w', wi, k), anchor)
        for i, j in S:
            union(('w', wi, i), ('w', wi, j))
        fdegs.append(fdeg)

    def sdeg(S):
        d = {}
        for i, j in S:
            d[i] = d.get(i, 0) + 1
            d[j] = d.get(j, 0) + 1
        return d

    defects = {}        # component root → [defect]; ('w', wi, k) or a fixed node
    on_web = set()
    for wi, (web, S) in enumerate(webs):
        d = sdeg(S)
        for k, p in enumerate(web.pts):
            on_web.add((p.x, p.y))
            if (fdegs[wi][k] + d.get(k, 0)) % 2 == 1:
                defects.setdefault(find(('w', wi, k)), []).append(('w', wi, k))
    for n in fixed_graph.nodes:
        if fixed_graph.degree(n) % 2 == 1 and n not in on_web:
            defects.setdefault(find(n), []).append(n)
    total_print = sum(d['length'] for _, _, d in fixed_graph.edges(data=True))

    for root, ds in sorted(defects.items(), key=lambda kv: str(kv[0])):
        rep.defects += len(ds)
        remaining = list(ds)
        closed = False
        # One JOINT matching over every candidate fix, so a cheap local
        # fix never orphans a defect that only a longer edit could reach
        # (matching maximises the number of pairs first):
        #   tier 1  local toggle chain (≤ LOCAL_LIMIT) or a short support
        #   tier 2  pure phase shift (alternating, ≤ PHASE_LIMIT) — strut
        #           counts unchanged, material roughly neutral
        # Repeated while it makes progress (applied fixes change the web).
        for _round in range(4):
            if len(remaining) <= (0 if prefer_closed else 2):
                break
            cost, chain = {}, {}
            for wi, (web, S) in enumerate(webs):
                mine = [x for x in remaining
                        if isinstance(x[0], str) and x[0] == 'w' and x[1] == wi]
                if len(mine) < 2:
                    continue
                d = sdeg(S)
                segs = [(web.pts[i], web.pts[j]) for i, j in S] + \
                    [(_V(a), _V(b)) for a, b in fixed_segs]

                def offer(x, y, c, edges, tier):
                    k = _key(x, y)
                    if k not in cost or c < cost[k]:
                        cost[k], chain[k] = c, (wi, edges, tier)
                for x in mine:
                    for lim, alt, tier in ((LOCAL_LIMIT, False, 1), (PHASE_LIMIT, True, 2)):
                        paths = _toggle_paths(web, S, d, x[2], lim * web.pitch, alt)
                        for y in mine:
                            if y != x and y[2] in paths:
                                c, edges = paths[y[2]]
                                offer(x, y, c, edges, tier)
                for i, x in enumerate(mine):
                    for y in mine[i + 1:]:
                        if d.get(x[2], 0) + 1 > web.cap(x[2]) or \
                                d.get(y[2], 0) + 1 > web.cap(y[2]):
                            continue
                        sup = _support(web, x[2], y[2], S, segs)
                        if sup is not None:
                            offer(x, y, sup[0] * SUPPORT_W, sup[1], 1)
            open_m = _match(remaining, cost, 2)
            closed_m = _match(remaining, cost, 0) if prefer_closed else []
            open_c = sum(cost[_key(u, v)] for u, v in open_m)
            closed_c = sum(cost[_key(u, v)] for u, v in closed_m)
            use_closed = (prefer_closed and 2 * len(closed_m) == len(remaining) and
                          closed_c - open_c <= CLOSE_MAX_EXTRA * max(total_print, 1.0))
            chosen = closed_m if use_closed else open_m
            progress = False
            for u, v in sorted(chosen, key=lambda p: cost[_key(*p)]):
                wi, edges, tier = chain[_key(u, v)]
                edges = list(edges)
                web, S = webs[wi]
                if not _apply(web, S, edges, fdegs[wi]):
                    continue
                rep.corrections.append(_record(web, edges, S, u, v, tier))
                rep.repaired += 1
                remaining.remove(u)
                remaining.remove(v)
                progress = True
            closed = not remaining
            if not progress:
                break
        # Tier 3 — last resort before retrace: what parity forces across a
        # longer distance (e.g. a dead-end infilled arm: three strands enter
        # it, so it must hold an odd junction) gets an INTERLEAVED return
        # zigzag: it follows the web's strut path but lands at new face
        # points midway between existing samples (one generated path per
        # landing, structural crossings with the existing struts). Extra
        # infill along that stretch — never a straight bead, never retrace.
        if len(remaining) > 2:
            remaining = _tier3(webs, remaining, fdegs, fixed_segs, rep)
            closed = not remaining
        left = len(remaining)
        ends = min(2, left)
        rep.route_end_defects += ends
        rep.left_to_retrace += left - ends
        rep.closed.append(closed)
    return rep


def _support(web, i, j, S, segs):
    """A short local support between web points i and j: straight, or
    bent once through a point inside the wall (so it can turn round a
    corner instead of crossing a void). Returns (length, edges) where a
    bend is ('bend', Vec2), or None."""
    a, b = web.pts[i], web.pts[j]
    L = a.dist(b)
    lim = SUPPORT_MAX * web.pitch
    if L > lim:
        return None
    e = (min(i, j), max(i, j))
    if e not in S and _strut_ok_web(web, a, b) and not _overlaps(a, b, segs):
        return L, [e]
    m = a.lerp(b, 0.5)
    n = (b - a).normalized()
    n = type(a)(-n.y, n.x)
    for k in (0.5, 1.0, 1.5, 2.0):
        for side in (1.0, -1.0):
            q = m + n * (side * k * web.pitch)
            tot = a.dist(q) + q.dist(b)
            if tot > lim or not web.region.inside(q):
                continue
            if min(q.dist(p) for p in web.pts) < CROWD * web.pitch:
                continue
            if not (_strut_ok_web(web, a, q) and _strut_ok_web(web, q, b)):
                continue
            if _overlaps(a, q, segs) or _overlaps(q, b, segs):
                continue
            return tot, [('bend', q, i, j)]
    return None


def _strut_ok_web(web, a, b):
    from infill import _strut_ok
    return _strut_ok(a, b, web.region)


def _apply(web, S, edges, fdeg) -> bool:
    real = []
    for e in edges:
        if e[0] == 'bend':                  # a bent support: new web point
            _, q, i, j = e
            if min(q.dist(p) for p in web.pts) < CROWD * web.pitch:
                return False                # another support took the spot
            k = web.add_point(q)
            real += [(min(i, k), max(i, k)), (min(j, k), max(j, k))]
        else:
            real.append(e)
    edges[:] = real
    for e in real:
        web.edges.add(e)                # a support strut joins the web
    before = set(S)
    S.symmetric_difference_update(set(real))
    added = [e for e in real if e in S]
    if _healthy(web, S, fdeg) and _uncongested(web, S, added):
        return True
    S.clear()
    S.update(before)
    return False


def _uncongested(web, S, added, extra=()):
    """Hard rule: around anything a correction adds (its ends and where
    it crosses other generated beads) at most MAX_GENERATED_BEADS generated
    beads pass within the clearance radius — no generated mud knots."""
    import route_quality as RQ
    gen = [((web.pts[i].x, web.pts[i].y), (web.pts[j].x, web.pts[j].y)) for i, j in S]
    gen += [((p.x, p.y), (q.x, q.y)) for poly in extra for p, q in zip(poly, poly[1:])]
    new = [((web.pts[i].x, web.pts[i].y), (web.pts[j].x, web.pts[j].y)) for i, j in added]
    return _max_multiplicity(gen, new, RQ.CLEARANCE_RADIUS) <= RQ.MAX_GENERATED_BEADS + 0.05


def _max_multiplicity(gen, new, rho):
    import route_quality as RQ
    if not new:
        return 0.0
    grid = RQ._SegGrid(gen, max(2 * rho, 4.0))
    probes = {p for s in new for p in s}
    for a, b in new:
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for k in grid.near(mid, math.dist(a, b) / 2):
            x = RQ._cross(a, b, *gen[k])
            if x is not None:
                probes.add(x)
    worst = 0.0
    for p in probes:
        m = sum(RQ._seg_disk_len(*gen[k], p, rho) for k in grid.near(p, rho)) / (2 * rho)
        worst = max(worst, m)
    return worst


def _record(web, edges, S, u, v, tier):
    added = [e for e in edges if e in S]
    removed = [e for e in edges if e not in S]
    pu, pv = web.pts[u[2]], web.pts[v[2]]
    far = max((min(web.pts[e[0]].lerp(web.pts[e[1]], 0.5).dist(pu),
                   web.pts[e[0]].lerp(web.pts[e[1]], 0.5).dist(pv))
               for e in edges), default=0.0)
    return {
        'defects': [[pu.x, pu.y], [pv.x, pv.y]],
        'tier': tier,
        'added': round(sum(web.length(e) for e in added), 3),
        'removed': round(sum(web.length(e) for e in removed), 3),
        'max_segment': round(max((web.length(e) for e in added), default=0.0), 3),
        'max_distance': round(far, 3),
        'edits': len(edges),
    }


def _healthy(web, S, fdeg):
    """No strut-count cap exceeded, and every printed strut cluster still
    touches a wall face (no floating islands of infill)."""
    d = {}
    for i, j in S:
        d[i] = d.get(i, 0) + 1
        d[j] = d.get(j, 0) + 1
    if any(c > web.cap(k) for k, c in d.items()):
        return False
    G = nx.Graph()
    G.add_edges_from(S)
    for comp in nx.connected_components(G):
        if not any(web.boundary[k] or (fdeg[k] if k < len(fdeg) else 0)
                   for k in comp):
            return False
    return True


def _tier3(webs, remaining, fdegs, fixed_segs, rep):
    from infill import _strut_ok
    for wi, (web, S) in enumerate(webs):
        mine = [x for x in remaining
                if isinstance(x[0], str) and x[0] == 'w' and x[1] == wi]
        if len(mine) < 3:
            continue
        G = nx.Graph()
        for i, j in web.edges:
            G.add_edge(i, j, weight=web.length((i, j)))
        dist, paths = {}, {}
        for x in mine:
            if x[2] in G:
                dist[x], paths[x] = nx.single_source_dijkstra(G, x[2])
        cost = {}
        for i, x in enumerate(mine):
            for y in mine[i + 1:]:
                if x in dist and y[2] in dist[x]:
                    cost[_key(x, y)] = dist[x][y[2]]
        segs = [(web.pts[i], web.pts[j]) for i, j in S] + \
            [(_V(a), _V(b)) for a, b in fixed_segs]
        taken = list(web.pts)
        for u, v in sorted(_match(mine, cost, 2), key=lambda p: cost[_key(*p)]):
            poly = _interleave(web, paths[u][v[2]], segs, taken)
            if poly is None:
                continue
            gen = [((web.pts[i].x, web.pts[i].y), (web.pts[j].x, web.pts[j].y))
                   for i, j in S]
            gen += [((p.x, p.y), (q.x, q.y)) for r in rep.returns
                    for p, q in zip(r, r[1:])]
            new = [((p.x, p.y), (q.x, q.y)) for p, q in zip(poly, poly[1:])]
            import route_quality as RQ
            if _max_multiplicity(gen + new, new, RQ.CLEARANCE_RADIUS) > \
                    RQ.MAX_GENERATED_BEADS + 0.05:
                continue
            for p, q in zip(poly, poly[1:]):
                segs.append((p, q))
            taken.extend(poly[1:-1])
            rep.returns.append(poly)
            L = sum(p.dist(q) for p, q in zip(poly, poly[1:]))
            pu, pv = poly[0], poly[-1]
            rep.corrections.append({
                'defects': [[pu.x, pu.y], [pv.x, pv.y]], 'tier': 3,
                'added': round(L, 3), 'removed': 0.0,
                'max_segment': round(max(p.dist(q) for p, q in zip(poly, poly[1:])), 3),
                'max_distance': round(max(min(q.dist(pu), q.dist(pv)) for q in poly), 3),
                'edits': len(poly) - 1,
            })
            rep.repaired += 1
            remaining.remove(u)
            remaining.remove(v)
    return remaining


def _V(t):
    from model import Vec2
    return Vec2(t[0], t[1])


def _interleave(web, route, segs, taken):
    """Return strand for a web route: every interior route point is replaced
    by a new landing beside it (face points: midway to a ring neighbour;
    interior points: nudged a third of a pitch along the route), so the
    strand runs between the existing struts instead of on them."""
    from infill import _strut_ok
    pts = web.pts
    out = [pts[route[0]]]
    for k in range(1, len(route) - 1):
        n = route[k]
        options = []
        if web.boundary[n]:
            nxt = web.ring_next.get(n)
            prv = next((a for a, b in web.ring_next.items() if b == n), None)
            for m in (nxt, prv):
                if m is not None:
                    options.append(web.face_midpoint(n, m) if m == nxt
                                   else web.face_midpoint(m, n))
        else:
            d = (pts[route[k + 1]] - pts[route[k - 1]]).normalized()
            options.append(pts[n] + d * (web.pitch / 3.0))
            options.append(pts[n] - d * (web.pitch / 3.0))
        best = None
        for q in options:
            if min(q.dist(t) for t in taken) < CROWD * web.pitch:
                continue
            if not _strut_ok(out[-1], q, web.region) or _overlaps(out[-1], q, segs):
                continue
            best = q
            break
        if best is None:
            return None
        out.append(best)
    end = pts[route[-1]]
    if not _strut_ok(out[-1], end, web.region) or _overlaps(out[-1], end, segs):
        return None
    out.append(end)
    return out


def _overlaps(a, b, segs, tol=1e-3):
    L = a.dist(b)
    if L < 1e-9:
        return True
    ux, uy = (b.x - a.x) / L, (b.y - a.y) / L
    for c, d in segs:
        if abs((c.x - a.x) * uy - (c.y - a.y) * ux) > tol or \
                abs((d.x - a.x) * uy - (d.y - a.y) * ux) > tol:
            continue
        t0 = (c.x - a.x) * ux + (c.y - a.y) * uy
        t1 = (d.x - a.x) * ux + (d.y - a.y) * uy
        if min(L, max(t0, t1)) - max(0.0, min(t0, t1)) > tol:
            return True
    return False
