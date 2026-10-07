"""
Wall lattice — MOTIF-based, route-aware stitching of wall faces.

The interior lattice of a wall exists to STITCH ITS TWO FACES TOGETHER
while being printable in one continuous run. Instead of generating a
fixed-pitch field and repairing its graph parity afterwards, this module
designs the topology first:

1. SKELETON (chordal axis). The wall material is triangulated (Delaunay of
   densely sampled face points). Triangles with one internal chord are
   wall ENDS, with two are SLEEVES (a wall run), with three are JUNCTIONS.
   Sleeve chains are WALL RUNS; every chord of a run spans face to face,
   so a run carries an exact correspondence between its two faces, on
   straight and curved walls alike. Tiny side branches (convex corners)
   are pruned; junction triangles joined by very short runs form one
   junction CLUSTER (a 4-way crossing, a rounded hub).

2. MOTIFS. A pass through a run is a zigzag (or wave) landing alternately
   on its two faces at evenly distributed stations. Every interior landing
   adds degree 2 to the face (even), so the parity of the whole layer
   depends only on where passes END — which this module chooses:
     - ordinary run between junctions: one pass (single phase);
     - closed loop with no junction: one circulating pass (closes on
       itself);
     - dead-end arm: OUT-AND-BACK — two INTERLEAVED phases on one station
       grid (each at ~2 × target, offset by one station, so the combined
       supports are ~target apart), joined at the dead end by a CAP V (the
       last station is landed on the wall's end face); no interior
       retrace, and the module returns to the junction it left;
     - a lone wall run (no junction): one pass, open route, with a cap V
       at each end.
   Motif vocabulary: ordinary stitch, CORNER BRACE (corners are detected
   along a run and both corner points are landed), junction hand-off and
   cap V.
   Which runs get two passes is decided for the whole network at once
   (route inspection on the skeleton: the cheapest set of doubled runs
   making every junction even), so equivalent branches get the same motif.

3. JUNCTIONS. Pass ends meet at the junction's corners: the pass leaving a
   run and the pass entering the next one land on the same corner point
   (no connector, two generated beads there — never a knot in the
   middle). Where that is impossible a short connector inside the junction
   is used (cross-wall preferred). Single-pass runs choose their start
   face and stitch-count parity jointly with the corner pairing.

4. TARGET SPACING. A run is split into SEGMENTS between fixed support
   points (junction ends, dead ends, corners); a segment of usable length
   U gets n ≈ U / S stitches (the nearest admissible integer) and actual
   pitch U / n, so a 91 in segment at 20 in gets 5 stitches at 18.2 in,
   never 4 + a remainder. U is measured in a station parameter weighted
   towards the shorter face (no crowding on the inside of bends). The
   MAXIMUM UNSUPPORTED DISTANCE (default 1.375 × target) wins over the
   target: stitches are added until no gap exceeds it.

5. LINEAGE JAMBS (stations=…). A Layer Design lineage shares ONE lattice
   scaffold (layer_design.py). Every end wall (cap) that an opening of a
   lineage member creates is given as a station line (its two face
   points). Topology: a member prints the scaffold clipped at that line,
   so for Base AND the member to both route closed, the scaffold must
   cross each jamb line an EVEN number of times with all crossings at ONE
   point — a single pass crosses once (the member is left with odd cut
   ends, an open route, however the stitches are arranged locally). So:
     - a run crossed by a jamb line carries TWO mirrored passes (a ring:
       two circulating passes; between junctions: a double run; a dead-end
       arm: its out-and-back) — it adds an even degree at its ends, route
       inspection pairs the remaining runs;
     - at every jamb line both passes cross EXACTLY at the line's midpoint
       M (a shared vertex, side JAMB): the two neighbouring stations are
       re-centred about the line where free (limited; never a corner /
       junction / wall-end station) and the ordinary stitch is bent
       smoothly through M (two jambs between the same stations get one
       extra station between them — the passes' tails swap, every later
       landing keeps its face).
   The member then ends BOTH passes at M on its end wall — the planner's
   cap V — and closes; Base carries the crossing as a relic (no extra
   material there: mirrored passes cross mid-wall anyway). The second pass
   along the whole run is the price of the closed route (reported:
   'double_loop' / doubled runs). Jambs at a corner (the crossing replaces
   the corner station) and just short of a junction end (both passes end at
   M — a cap V into the junction) are resolved; one that cannot be crossed
   is reported in report['jambs']. (layer_design then checks the member's
   closure and plans it alone if the shared scaffold cannot close it.)

6. CROSS-Z TRACKING (track=, track_map=). A semantically transformed
   design does not rediscover its lattice per layer: the plan of the
   untransformed reference is mapped wall-relatively onto this skeleton
   (_match_track) and every matched run keeps the reference's passes, start
   side / parity, per-segment stitch counts, loop seam, stitch construction
   and junction PAIRINGS. A count changes only where impossible (gap > max
   unsupported, pitch < ½ target) or where the requested parity demands it.
   PARTIAL when the skeleton changed (a junction lens shrinking away):
   unmatched runs are planned afresh, pass counts are repaired for parity
   through untracked runs first, carried sides are re-searched only if they
   cannot pair. The junction TRANSITIONS themselves are still constructed
   per layer (the known source of cross-Z support findings at moving
   junctions — see the Designer memory).

7. PHYSICAL VALIDITY. bead= : a run whose faces are ≤ one bead apart has
   NO CAVITY and gets no passes (a single-bead path's return lanes, a wall
   overridden to a bead's width). No emitted stitch leaves the material:
   where the wave / Hermite / straight constructions all do (a landing on a
   concave rounded-junction fillet), the stitch follows the wall centre
   line (dogleg). With contact rails, transfer landings join every face
   ring AND every separate lattice system, so a connected region is one
   closed route.

Wide regions (local thickness ≫ spacing — not a wall but an area) are not
stitched here; the caller falls back to the field generator (infill.py).
"""
from __future__ import annotations
import itertools
import math
from dataclasses import dataclass, field

import networkx as nx

from model import Vec2
from infill import _Region, _strut_ok

_DEBUG = False
CAP = 2              # landing 'side' meaning: on the wall's end face (cap)
MID = 3              # 'side': mid-wall apex of a turnaround with no cap landing
JAMB = 4             # 'side': both phases cross exactly at a lineage jamb line's midpoint
WAVE_SAMPLES = 16    # points per wave stitch (smooth: ≲ 10° turn per vertex)
WIDE = 1.6            # local thickness > WIDE × spacing → not a wall (fallback)
PRUNE = 1.2           # × thickness: shorter side branches are corner noise
MERGE = 1.0           # × thickness: junctions closer than this are one cluster
SPACING_W = 4.0       # cost of pitch error: SPACING_W × L × (relative error)²
SAME_FACE_W = 2.5     # a connector along one face costs more than one across
DMAX_RATIO = 1.375    # default maximum unsupported distance = 1.375 × target
CORNER_TURN = math.radians(30.0)   # centre line turning within CORNER_WIN (a 140° interior corner turns ~35° there)
CORNER_WIN = 2.0      # × thickness: window over which a corner turns


# ---------------------------------------------------------------------------
# Delaunay triangulation (incremental Bowyer–Watson with walking location)
# ---------------------------------------------------------------------------

def _orient(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _incircle(ax, ay, bx, by, cx, cy, dx, dy):
    adx, ady = ax - dx, ay - dy
    bdx, bdy = bx - dx, by - dy
    cdx, cdy = cx - dx, cy - dy
    ad, bd, cd = adx * adx + ady * ady, bdx * bdx + bdy * bdy, cdx * cdx + cdy * cdy
    return (adx * (bdy * cd - bd * cdy) - ady * (bdx * cd - bd * cdx)
            + ad * (bdx * cdy - bdy * cdx))


def delaunay(P):
    """Triangles (i, j, k), CCW, of points P [(x, y)]."""
    n = len(P)
    if n < 3:
        return []
    xs = [p[0] for p in P]
    ys = [p[1] for p in P]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    X, Y = [], []
    for i, (x, y) in enumerate(P):          # deterministic tie-breaking jitter
        h = math.sin(i * 12.9898 + 78.233) * 43758.5453
        g = math.sin(i * 39.3467 + 11.135) * 24634.6345
        X.append(x + (h - math.floor(h) - 0.5) * 1e-7 * span)
        Y.append(y + (g - math.floor(g) - 0.5) * 1e-7 * span)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    big = span * 1000          # (50 left hull triangles missing)
    X += [cx - big, cx + big, cx]
    Y += [cy - big, cy - big, cy + big]
    V = [[n, n + 1, n + 2]]
    NB = [[-1, -1, -1]]
    alive = [True]
    cell = span / max(1.0, math.sqrt(n))
    order = sorted(range(n), key=lambda i: (math.floor(Y[i] / cell),
                                            X[i] if math.floor(Y[i] / cell) % 2 == 0 else -X[i]))
    last = 0

    def locate(t, px, py):
        for _ in range(4 * len(V) + 16):
            moved = False
            tv = V[t]
            for k in range(3):
                u, v = tv[(k + 1) % 3], tv[(k + 2) % 3]
                if _orient(X[u], Y[u], X[v], Y[v], px, py) < 0 and NB[t][k] >= 0:
                    t = NB[t][k]
                    moved = True
                    break
            if not moved:
                return t
        for t, tv in enumerate(V):          # (degenerate walk) linear scan
            if alive[t] and all(_orient(X[tv[(k + 1) % 3]], Y[tv[(k + 1) % 3]],
                                        X[tv[(k + 2) % 3]], Y[tv[(k + 2) % 3]], px, py) >= 0
                                for k in range(3)):
                return t
        return t

    for i in order:
        px, py = X[i], Y[i]
        t = locate(last, px, py)
        bad = {t}
        stack = [t]
        while stack:
            u = stack.pop()
            for k in range(3):
                w = NB[u][k]
                if w >= 0 and w not in bad and alive[w]:
                    a, b, c = V[w]
                    if _incircle(X[a], Y[a], X[b], Y[b], X[c], Y[c], px, py) > 0:
                        bad.add(w)
                        stack.append(w)
        bnd = []
        for u in bad:
            for k in range(3):
                w = NB[u][k]
                if w < 0 or w not in bad:
                    bnd.append((V[u][(k + 1) % 3], V[u][(k + 2) % 3], w, u))
        for u in bad:
            alive[u] = False
        starts, ends, new = {}, {}, []
        for a, b, w, u in bnd:
            tid = len(V)
            V.append([a, b, i])
            NB.append([-1, -1, w])
            alive.append(True)
            if w >= 0:
                for kk in range(3):
                    if NB[w][kk] == u:
                        NB[w][kk] = tid
            starts[a] = tid
            ends[b] = tid
            new.append(tid)
        for tid in new:
            a, b, _ = V[tid]
            NB[tid][0] = starts.get(b, -1)        # across (b, i)
            NB[tid][1] = ends.get(a, -1)          # across (i, a)
        last = new[-1]
    return [tuple(V[t]) for t in range(len(V)) if alive[t] and max(V[t]) < n]


# ---------------------------------------------------------------------------
# Sampling the wall faces
# ---------------------------------------------------------------------------

class _Rings:
    """The wall boundary rings with arc-length parametrisation."""

    def __init__(self, rings):
        self.rings = rings
        self.cum = []
        for r in rings:
            c = [0.0]
            for i in range(len(r)):
                c.append(c[-1] + r[i].dist(r[(i + 1) % len(r)]))
            self.cum.append(c)

    def length(self, ri):
        return self.cum[ri][-1]

    def point(self, ri, s):
        r, c = self.rings[ri], self.cum[ri]
        L = c[-1]
        s %= L
        lo, hi = 0, len(r) - 1
        while lo < hi:                       # last vertex with c[k] <= s
            mid = (lo + hi + 1) // 2
            if c[mid] <= s:
                lo = mid
            else:
                hi = mid - 1
        k = lo
        seg = c[k + 1] - c[k]
        f = (s - c[k]) / seg if seg > 1e-12 else 0.0
        return r[k].lerp(r[(k + 1) % len(r)], f)


def _sample(R, h):
    """Points on every ring: all sharp / spaced vertices plus subdivisions
    so no gap exceeds h. Returns (pts, ring index, arc position, next)."""
    pts, ring_of, arc_of = [], [], []
    nxt = {}
    for ri, r in enumerate(R.rings):
        n = len(r)
        c = R.cum[ri]
        first = len(pts)
        last_s = None
        for k in range(n):
            a, b = r[k], r[(k + 1) % n]
            prev = r[k - 1]
            d1, d2 = a - prev, b - a
            sharp = False
            if d1.length() > 1e-9 and d2.length() > 1e-9:
                cs = (d1.x * d2.x + d1.y * d2.y) / (d1.length() * d2.length())
                sharp = cs < math.cos(math.radians(20))
            if last_s is None or sharp or c[k] - last_s >= 0.3 * h:
                pts.append(a)
                ring_of.append(ri)
                arc_of.append(c[k])
                last_s = c[k]
            seg = c[k + 1] - c[k]
            m = int(math.ceil(seg / h))
            for j in range(1, m):
                s = c[k] + seg * j / m
                if s - last_s >= 0.3 * h and c[k + 1] - s >= 0.3 * h:
                    pts.append(a.lerp(b, j / m))
                    ring_of.append(ri)
                    arc_of.append(s)
                    last_s = s
        cnt = len(pts) - first
        for k in range(cnt):
            nxt[first + k] = first + (k + 1) % cnt
    return pts, ring_of, arc_of, nxt


# ---------------------------------------------------------------------------
# Skeleton: wall runs, ends, junction clusters
# ---------------------------------------------------------------------------

@dataclass
class Run:
    chords: list                  # [(side0 idx, side1 idx)] in order
    s: list                       # centre-line arc length at each chord
    a: object = None              # end node at chords[0] (None for a cycle)
    b: object = None              # end node at chords[-1]
    cycle: bool = False
    mids: list = None             # smoothed centre line (one point per chord)

    @property
    def length(self):
        return self.s[-1]


@dataclass
class Skeleton:
    pts: list
    ring_of: list
    arc_of: list
    R: _Rings
    runs: list
    nodes: dict                   # node id → 'end' | 'junction'
    thickness: float


def _thickness(rings):
    area = perim = 0.0
    for r in rings:
        n = len(r)
        area += sum(r[i].x * r[(i + 1) % n].y - r[(i + 1) % n].x * r[i].y for i in range(n)) / 2
        perim += sum(r[i].dist(r[(i + 1) % n]) for i in range(n))
    return 2 * abs(area) / perim if perim > 0 else 0.0


def skeleton(rings, h, thick):
    R = _Rings(rings)
    region = _Region(rings, max(h * 2, 2.0))
    pts, ring_of, arc_of, nxt = _sample(R, h)
    tris = []
    for _ in range(4):                          # conforming refinement
        tris = delaunay([(p.x, p.y) for p in pts])
        edges = set()
        for t in tris:
            for k in range(3):
                u, v = t[k], t[(k + 1) % 3]
                edges.add((min(u, v), max(u, v)))
        missing = [(i, j) for i, j in nxt.items() if (min(i, j), max(i, j)) not in edges]
        if not missing:
            break
        pts, ring_of, arc_of, nxt = _refine(R, pts, ring_of, arc_of, nxt, missing)
    inside = []
    for t in tris:
        a, b, c = (pts[k] for k in t)
        m = Vec2((a.x + b.x + c.x) / 3, (a.y + b.y + c.y) / 3)
        if region.inside(m):
            inside.append(t)
    bnd = lambda u, v: nxt.get(u) == v or nxt.get(v) == u
    etri = {}
    for ti, t in enumerate(inside):
        for k in range(3):
            u, v = t[k], t[(k + 1) % 3]
            etri.setdefault((min(u, v), max(u, v)), []).append(ti)
    chords = {}
    for ti, t in enumerate(inside):
        cs = []
        for k in range(3):
            u, v = t[k], t[(k + 1) % 3]
            e = (min(u, v), max(u, v))
            if not bnd(u, v) and len(etri[e]) == 2:
                cs.append(e)
        chords[ti] = cs
    other = lambda e, ti: etri[e][0] if etri[e][1] == ti else etri[e][1]
    mid = lambda e: pts[e[0]].lerp(pts[e[1]], 0.5)

    # chains between non-sleeve triangles
    nodes = {ti: ('end' if len(cs) == 1 else 'junction')
             for ti, cs in chords.items() if len(cs) in (1, 3)}
    seen = set()
    chains = []                                   # [a, b, [edges], cycle]
    for ti in sorted(nodes):
        for e in chords[ti]:
            if (ti, e) in seen:
                continue
            seq, cur = [e], other(e, ti)
            seen.add((ti, e))
            while len(chords[cur]) == 2:
                e2 = chords[cur][0] if chords[cur][1] == seq[-1] else chords[cur][1]
                seq.append(e2)
                cur = other(e2, cur)
            seen.add((cur, seq[-1]))
            chains.append([ti, cur, seq, False])
    visited = {e for c in chains for e in c[2]}
    for ti, cs in chords.items():                # pure cycles (closed loop)
        if len(cs) == 2 and cs[0] not in visited:
            seq, cur, e = [cs[0]], other(cs[0], ti), cs[0]
            visited.add(e)
            while cur != ti:
                e2 = chords[cur][0] if chords[cur][1] == seq[-1] else chords[cur][1]
                if e2 in visited:
                    break
                visited.add(e2)
                seq.append(e2)
                cur = other(e2, cur)
            chains.append([None, None, seq, True])

    def clen(seq):
        return sum(mid(x).dist(mid(y)) for x, y in zip(seq, seq[1:]))

    # prune corner noise: short branches ending in a wall end
    changed = True
    while changed:
        changed = False
        deg = {}
        for c in chains:
            if not c[3]:
                deg[c[0]] = deg.get(c[0], 0) + 1
                deg[c[1]] = deg.get(c[1], 0) + 1
        for c in list(chains):
            if c[3]:
                continue
            for x, y in ((c[0], c[1]), (c[1], c[0])):
                if nodes.get(x) == 'end' and nodes.get(y) == 'junction' and deg.get(y, 0) >= 3 \
                        and clen(c[2]) < PRUNE * thick:
                    chains.remove(c)
                    nodes.pop(x, None)
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue
        deg = {}
        for c in chains:
            if not c[3]:
                deg[c[0]] = deg.get(c[0], 0) + 1
                deg[c[1]] = deg.get(c[1], 0) + 1
        for nd, d in deg.items():
            if nodes.get(nd) == 'junction' and d == 2:
                cs = [c for c in chains if not c[3] and nd in (c[0], c[1])]
                if len(cs) == 1:                  # both ends of one chain: a loop
                    c = cs[0]
                    c[0] = c[1] = None
                    c[3] = True
                else:
                    c1, c2 = cs
                    s1 = c1[2] if c1[1] == nd else c1[2][::-1]
                    a1 = c1[0] if c1[1] == nd else c1[1]
                    s2 = c2[2] if c2[0] == nd else c2[2][::-1]
                    b2 = c2[1] if c2[0] == nd else c2[0]
                    chains.remove(c1)
                    chains.remove(c2)
                    chains.append([a1, b2, s1 + s2, False])
                nodes.pop(nd)
                changed = True
                break
            if nodes.get(nd) == 'junction' and d == 1:
                nodes[nd] = 'end'
                changed = True
                break

    # junction clusters: junctions joined by very short runs
    uf = {nd: nd for nd in nodes}

    def find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x
    keep = []
    for c in chains:
        if not c[3] and nodes.get(c[0]) == 'junction' and nodes.get(c[1]) == 'junction' \
                and clen(c[2]) < MERGE * thick:
            uf[find(c[0])] = find(c[1])
        else:
            keep.append(c)
    runs = []
    for a, b, seq, cyc in keep:
        if len(seq) < 1:
            continue
        orient = []
        p, q = seq[0]
        orient.append((p, q))
        for e in seq[1:]:
            if e[0] == e[1]:
                continue
            pp, qq = orient[-1]
            if pp in e and qq in e:
                continue
            if pp in e:
                orient.append((pp, e[1] if e[0] == pp else e[0]))
            elif qq in e:
                orient.append((e[1] if e[0] == qq else e[0], qq))
            else:                                  # (should not happen) restart
                orient.append((e[0], e[1]) if pts[e[0]].dist(pts[pp]) <= pts[e[1]].dist(pts[pp])
                              else (e[1], e[0]))
        # Centre line: chord midpoints, smoothed (Delaunay chords are skewed,
        # so raw midpoints wobble along and across the wall); ends fixed.
        mids = [pts[x[0]].lerp(pts[x[1]], 0.5) for x in orient]
        iters = max(2, int(round(thick / max(h, 1e-6))))
        n_m = len(mids)
        for _ in range(iters):
            if n_m < 3:
                break
            new = list(mids)
            rng = range(n_m) if cyc else range(1, n_m - 1)
            for i in rng:
                a_, b_ = mids[i - 1], mids[(i + 1) % n_m]
                new[i] = Vec2((a_.x + 2 * mids[i].x + b_.x) / 4, (a_.y + 2 * mids[i].y + b_.y) / 4)
            mids = new
        s = [0.0]
        for m1, m2 in zip(mids, mids[1:]):
            s.append(s[-1] + m1.dist(m2))
        if cyc:
            s.append(s[-1] + mids[-1].dist(mids[0]))
        runs.append(Run(orient, s,
                        None if cyc else ('J', find(a)) if nodes.get(a) == 'junction' else ('E', a),
                        None if cyc else ('J', find(b)) if nodes.get(b) == 'junction' else ('E', b),
                        cyc, mids))
    node_kind = {}
    for r in runs:
        for nd in (r.a, r.b):
            if nd is not None:
                node_kind[nd] = 'junction' if nd[0] == 'J' else 'end'
    return Skeleton(pts, ring_of, arc_of, R, runs, node_kind, thick)


def _refine(R, pts, ring_of, arc_of, nxt, missing):
    """Split boundary edges missing from the triangulation (conforming)."""
    split = {i for i, j in missing} | {j for i, j in missing}
    new_pts, new_ring, new_arc = [], [], []
    order = sorted(range(len(pts)), key=lambda k: (ring_of[k], arc_of[k]))
    for k in order:
        new_pts.append(pts[k]); new_ring.append(ring_of[k]); new_arc.append(arc_of[k])
        j = nxt[k]
        if (k, j) in missing or (j, k) in missing:
            L = R.length(ring_of[k])
            s1 = arc_of[j] if arc_of[j] > arc_of[k] else arc_of[j] + L
            sm = (arc_of[k] + s1) / 2
            new_pts.append(R.point(ring_of[k], sm)); new_ring.append(ring_of[k]); new_arc.append(sm % L)
    n_nxt = {}
    by_ring = {}
    for k in range(len(new_pts)):
        by_ring.setdefault(new_ring[k], []).append(k)
    for ri, ks in by_ring.items():
        ks.sort(key=lambda k: new_arc[k])
        for a, b in zip(ks, ks[1:] + ks[:1]):
            n_nxt[a] = b
    return new_pts, new_ring, new_arc, n_nxt


# ---------------------------------------------------------------------------
# Planning: passes per run, junction pairing, stitch counts
# ---------------------------------------------------------------------------

@dataclass
class LatticePlan:
    polylines: list = field(default_factory=list)
    report: dict = field(default_factory=dict)


class _Geo:
    """
    Face positions along a run. The centre line is the polyline of chord
    midpoints (arc length s). The face point of a station is the NEAREST
    point of that face to the centre-line point — searched only along the
    stretch of the face that this run's chords span — so straight walls
    get perpendicular, evenly spaced landings and curved walls follow the
    normal correspondence. Junction corners (run ends at a junction) start
    as the exact chord vertices, shared with the neighbouring run (plan()
    may later replace a shared corner by a merged point slightly inside a
    rounded junction).
    """

    def __init__(self, sk):
        self.sk = sk
        self.cache = {}
        self.mids = {}

    def centre(self, run, s):
        mids = self.mids.get(id(run))
        if mids is None:
            mids = list(run.mids) if run.mids else \
                [self.sk.pts[p].lerp(self.sk.pts[q], 0.5) for p, q in run.chords]
            if run.cycle:
                mids = mids + [mids[0]]
            self.mids[id(run)] = mids
        k, f = self._locate(run, s)
        return mids[k].lerp(mids[min(k + 1, len(mids) - 1)], f)

    def _locate(self, run, s):
        sk = run.s
        if run.cycle:
            s %= run.length
        if s <= sk[0]:
            return 0, 0.0
        if s >= sk[-1]:
            return len(sk) - 1, 0.0
        lo, hi = 0, len(sk) - 1
        while lo < hi:
            m = (lo + hi + 1) // 2
            if sk[m] <= s:
                lo = m
            else:
                hi = m - 1
        seg = sk[lo + 1] - sk[lo]
        return lo, ((s - sk[lo]) / seg if seg > 1e-12 else 0.0)

    def _chord_arc(self, run, side, s):
        """(ring, arc) of the face by chord interpolation (estimate)."""
        ch = run.chords
        k, f = self._locate(run, s)
        a = ch[k % len(ch)][side]
        b = ch[(k + 1) % len(ch)][side] if k + 1 < len(run.s) else a
        ra = self.sk.ring_of[a]
        if self.sk.ring_of[b] != ra:
            b = a
        L = self.sk.R.length(ra)
        d = (self.sk.arc_of[b] - self.sk.arc_of[a]) % L
        if d > L / 2:
            d -= L
        return ra, self.sk.arc_of[a] + f * d

    def side_point(self, run, side, s):
        return self._project(run, side, s)[0]

    def perpendicular(self, run, s):
        """Both faces found squarely across the wall at s (no cap)."""
        (p, ok0), (q, ok1) = self._project(run, 0, s), self._project(run, 1, s)
        return ok0 and ok1 and p.dist(q) > 0.5 * self.sk.thickness

    def _project(self, run, side, s):
        if not run.cycle:
            if s <= 1e-9 and run.a[0] == 'J':
                return self.sk.pts[run.chords[0][side]], True
            if s >= run.length - 1e-9 and run.b[0] == 'J':
                return self.sk.pts[run.chords[-1][side]], True
        ri, est = self._chord_arc(run, side, s)
        c = self.centre(run, s)
        # local tangent of the centre line, and which side of it is `side`
        d = max(0.5, 0.5 * self.sk.thickness)
        t0 = self.centre(run, s - d) if (run.cycle or s - d > 0) else c
        t1 = self.centre(run, s + d) if (run.cycle or s + d < run.length) else c
        tv = t1 - t0
        if tv.length() < 1e-9:
            tv = self.centre(run, min(run.length, s + 2 * d)) - self.centre(run, max(0.0, s - 2 * d))
        tl = tv.length() or 1.0
        tx, ty = tv.x / tl, tv.y / tl
        sign = self._side_sign(run, side)
        R = self.sk.R
        ring, cum = R.rings[ri], R.cum[ri]
        L = cum[-1]
        win = 2.0 * self.sk.thickness + 2.0
        best = loose = None
        n = len(ring)
        for k in self._segs(ri, est, win):
            a, b = ring[k], ring[(k + 1) % n]
            dx, dy = b.x - a.x, b.y - a.y
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((c.x - a.x) * dx + (c.y - a.y) * dy) / L2))
            q = a.lerp(b, t)
            vx, vy = q.x - c.x, q.y - c.y
            dd = math.hypot(vx, vy)
            if loose is None or dd < loose[0]:
                loose = (dd, q)
            across = (tx * vy - ty * vx) * sign          # >0: on this side
            along = abs(tx * vx + ty * vy)
            if across <= 0 or along > 0.6 * across:      # wrong side / a cap
                continue
            if best is None or dd < best[0] - 1e-12:
                best = (dd, q)
        if best:
            return best[1], True
        return (loose[1] if loose else R.point(ri, est)), False

    def _segs(self, ri, est, win):
        """Indices of ring segments within arc window est ± win."""
        import bisect
        cum = self.sk.R.cum[ri]
        n = len(cum) - 1
        L = cum[-1]
        if 2 * win >= L:
            return range(n)
        a, b = (est - win) % L, (est + win) % L
        ka = max(0, bisect.bisect_right(cum, a) - 1)
        kb = min(n - 1, bisect.bisect_right(cum, b) - 1)
        if ka <= kb:
            return range(ka, kb + 1)
        return list(range(ka, n)) + list(range(0, kb + 1))

    def table(self, run):
        """Station parameter u(s): half centre-line length, half the
        progress of the SHORTER face — on a straight wall u = s; in a tight
        bend or round a reflex corner the inner face hardly moves, so fewer
        stitches land there (no crowding on the inside of a curve)."""
        key = ('table', id(run))
        if key not in self.cache:
            L = run.length
            dh = max(0.5, 0.25 * self.sk.thickness)
            n = max(2, int(math.ceil(L / dh)))
            ss = [L * k / n for k in range(n + 1)]
            p0 = [self.side_point(run, 0, x) for x in ss]
            p1 = [self.side_point(run, 1, x) for x in ss]
            us = [0.0]
            for k in range(n):
                ds = ss[k + 1] - ss[k]
                face = min(p0[k].dist(p0[k + 1]), p1[k].dist(p1[k + 1]))
                us.append(us[-1] + 0.5 * ds + 0.5 * min(face, ds))
            self.cache[key] = (ss, us)
        return self.cache[key]

    def u_of_s(self, run, s):
        ss, us = self.table(run)
        return _interp(ss, us, s)

    def s_of_u(self, run, u):
        ss, us = self.table(run)
        return _interp(us, ss, u)

    def _side_sign(self, run, side):
        key = ('sign', id(run), side)
        if key not in self.cache:
            p, q = run.chords[len(run.chords) // 2]
            m = self.sk.pts[p].lerp(self.sk.pts[q], 0.5)
            k = len(run.chords) // 2
            sm = run.s[k]
            a = self.centre(run, max(0.0, sm - 1.0))
            b = self.centre(run, min(run.length, sm + 1.0))
            tv = b - a
            v = self.sk.pts[run.chords[k][side]] - m
            cr = tv.x * v.y - tv.y * v.x
            self.cache[key] = 1.0 if cr >= 0 else -1.0
        return self.cache[key]

    def landing(self, run, side, s):
        key = (id(run), side, round(s % run.length if run.cycle else s, 9))
        if key not in self.cache:
            self.cache[key] = self.side_point(run, side, s)
        return self.cache[key]


def _interp(xs, ys, x):
    import bisect
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    k = bisect.bisect_right(xs, x) - 1
    dx = xs[k + 1] - xs[k]
    f = (x - xs[k]) / dx if dx > 1e-12 else 0.0
    return ys[k] + f * (ys[k + 1] - ys[k])


def _spacing_cost(L, n, S):
    e = (L / n - S) / S
    return SPACING_W * L * e * e


def plan(rings, spacing=20.0, pattern='zigzag', variation=0, prefer_closed=True,
         max_unsupported=None, contact=0.0, closed=False, stations=None, track=None, track_map=None,
         bead=0.0):
    """
    Route-aware wall lattice for one wall region (rings: material on the
    left). Returns a LatticePlan, or None if the region is not wall-like
    (the caller falls back to the field generator).

    PHYSICAL BEADS (material.py):
      contact — centreline separation of a CONTACT landing (bead width −
        Contact Overlap). Interior stitch landings stop this far off the
        face centreline, measured across the wall (the stitch is built on
        these "contact rails", so wave / zigzag keep their shape). Route
        CONNECTIONS stay exact: pass ends at junction corners and one
        TRANSFER landing per face ring the lattice would otherwise not
        touch (converting an interior landing is parity-neutral: +2).
        Crossings between passes are untouched. 0 = legacy (on the face).
      closed — every route must close: a lone wall run (no junction) gets
        a closed OUT-AND-BACK loop (two phases joined by a cap V at both
        ends — the second wave) instead of one open pass.
      stations — lineage jamb lines [(P, Q)] (face points of an end wall
        some lineage member has here); see 5. in the module doc.
      bead — physical bead width: NO CAVITY, NO LATTICE. A run whose faces
        are at most one bead apart (a single-bead path's return lanes, a
        wall overridden down to a bead's width) has no room for internal
        structure: it gets no passes (the lattice ends at the junction
        inside the thick wall it meets). 0 = legacy (every run).
      track, track_map — CROSS-Z STATION TRACKING (6. in the module doc):
        the 'track' of a REFERENCE plan of the same wall network (the
        untransformed design) and a map from reference to this geometry
        (wall-relative). The plan keeps the reference's discrete choices.
    """
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return None
    S = max(1.0, float(spacing))
    # Maximum unsupported distance: structural upper bound on the distance
    # along the wall between consecutive lattice supports (either face).
    # Target spacing is a preference; this one wins when they conflict.
    DMAX = float(max_unsupported) if max_unsupported else DMAX_RATIO * S
    DMAX = max(DMAX, 0.5 * S)
    thick = _thickness(rings)
    # PROVISIONAL wall / area heuristic (local thickness vs target spacing);
    # to be replaced by a medial-axis opposing-face classification.
    if thick <= 0 or thick > WIDE * S:
        return None
    h = max(0.5, min(thick / 3.0, S / 3.0))
    sk = skeleton(rings, h, thick)
    runs = [r for r in sk.runs if r.length > 1e-6 or r.cycle]
    if not runs:
        return None
    region = _Region(rings, max(S / 2, 2.0))
    geo = _Geo(sk)
    no_cavity = []
    if bead > 0:
        def separation(r):
            n = max(4, int(r.length / max(0.5, 0.25 * thick)))
            ds = sorted(geo.side_point(r, 0, r.length * k / n).dist(geo.side_point(r, 1, r.length * k / n))
                        for k in range(1, n))
            return ds[len(ds) // 2] if ds else 0.0
        keep = [r for r in runs if separation(r) > bead + 1e-6]
        no_cavity = [round(r.length, 3) for r in runs if r not in keep]
        if not keep:
            return LatticePlan([], {'runs': [], 'no_cavity': no_cavity, 'motif': None})
        runs = keep
    tracked, same_topology = _match_track(runs, geo, track, track_map, thick) \
        if track and track_map else ({}, True)
    # PARTIAL TRACKING: where the skeleton changed (a junction lens shrinking
    # away, a hub regrouping) only the changed runs are planned afresh;
    # every run that still corresponds keeps the reference's choices
    track_note = {'tracked_runs': len(tracked), 'changed_segments': 0,
                  'untracked_runs': (len(runs) - len(tracked)) if track else 0,
                  'replanned': (None if not track or same_topology else
                                'reference topology differs' if not tracked else
                                'topology changed: unmatched runs planned afresh')}

    span_cache = {}

    def span(r):
        """Usable station range: a dead end stops where the wall is still
        square across (half a thickness or more short of the cap), where
        both faces are landed before the cap V turnaround."""
        if id(r) in span_cache:
            return span_cache[id(r)]
        def back(s, step):
            for _ in range(12):
                if geo.perpendicular(r, s):
                    return s
                s += step
            return s
        lo = 0.0 if r.cycle or r.a[0] == 'J' else back(min(0.5 * thick, 0.25 * r.length), 0.25 * thick)
        hi = r.length if r.cycle or r.b[0] == 'J' else \
            back(r.length - min(0.5 * thick, 0.25 * r.length), -0.25 * thick)
        span_cache[id(r)] = (lo, max(hi, lo + 1e-6))
        return span_cache[id(r)]

    # -- corners: first-class structural features ---------------------------
    corner_cache = {}

    def corners(r):
        """Corners along a run: the centre line turns more than CORNER_TURN
        within CORNER_WIN thicknesses. Each gets its two face points: the
        INNER corner (reflex vertex / inner arc apex) and the OUTER corner
        (along the ray from the inner corner through the centre line), and
        a landing is pinned on both — a corner is never left unsupported
        by where the nominal spacing happens to fall."""
        if id(r) in corner_cache:
            return corner_cache[id(r)]
        L = r.length
        lo, hi = span(r)
        W = CORNER_WIN * thick
        d = max(0.5, 0.25 * thick)
        n = max(4, int(L / d))
        ss = [L * k / n for k in range(n + 1)]

        def ang(s_):
            a = geo.centre(r, s_ - 0.5 * thick) if (r.cycle or s_ - 0.5 * thick > 0) else geo.centre(r, 0.0)
            b = geo.centre(r, s_ + 0.5 * thick) if (r.cycle or s_ + 0.5 * thick < L) else geo.centre(r, L)
            return math.atan2(b.y - a.y, b.x - a.x)

        def turn(s_):
            t = ang(s_ + W / 2) - ang(s_ - W / 2)
            return (t + math.pi) % (2 * math.pi) - math.pi
        tv = [turn(x) for x in ss]
        found = []
        for k, x in enumerate(ss):
            if abs(tv[k]) < CORNER_TURN:
                continue
            if not r.cycle and (x < lo + 0.75 * thick or x > hi - 0.75 * thick):
                continue
            w_ = int(W / d)
            if r.cycle:                           # the seam is not an edge
                m_ = len(ss) - 1
                nb = [abs(tv[j % m_]) for j in range(k - w_, k + w_ + 1)]
            else:
                nb = [abs(tv[j]) for j in range(max(0, k - w_), min(len(ss), k + w_ + 1))]
            if abs(tv[k]) + 1e-12 < max(nb):
                continue
            if found and abs(x - found[-1][0]) < W:
                continue
            if r.cycle and k == len(ss) - 1:
                continue                          # = sample 0 (the seam)
            found.append((x, tv[k]))
        if r.cycle and len(found) > 1 and (found[0][0] + L - found[-1][0]) < W:
            found.pop()
        out = []
        for x, tsign in found:
            c = geo.centre(r, x)
            # the inner side is the one the wall turns towards; the corner
            # bisector points from the turn's inside to its outside
            inner = 0 if tsign * geo._side_sign(r, 0) > 0 else 1
            a1, a2 = ang(x - 1.5 * thick), ang(x + 1.5 * thick)
            t1 = Vec2(math.cos(a1), math.sin(a1))
            t2 = Vec2(math.cos(a2), math.sin(a2))
            bis = t1 - t2
            if bis.length() < 1e-9:
                continue
            bis = bis * (1.0 / bis.length())
            I = _nearest_on_side(sk, c, bis, 1.6 * thick)
            if I is not None:
                # refine the bisector from the inner face itself: the
                # directions to its points ~1.5 t either side of I (exact
                # for a sharp corner and for a symmetric arc)
                rb = _inner_bisector(sk, I, 1.5 * thick)
                if rb is not None and rb.x * bis.x + rb.y * bis.y > 0.5:
                    bis = rb
            O = _extreme_face_point(sk, c, bis, 1.6 * thick)
            if I is None or O is None or not _strut_ok(I, O, region):
                continue
            out.append({'s': x, 'inner': inner, 'points': {inner: I, 1 - inner: O},
                        'turn': round(math.degrees(abs(tsign)), 1)})
        for cn in out:
            for side, pnt in cn['points'].items():
                geo.cache[(id(r), side, round(cn['s'] % L if r.cycle else cn['s'], 9))] = pnt
        corner_cache[id(r)] = out
        return out

    cap_cache = {}

    def cap_point(r, s_end):
        """Where the wall's end face (cap) is landed: the centre line
        carried on from s_end to the boundary, if both legs from the
        faces stay inside the wall."""
        key = (id(r), round(s_end, 9))
        if key in cap_cache:
            return cap_cache[key]
        L = r.length
        toward_end = s_end > 0.5 * L
        a0, a1 = geo.side_point(r, 0, s_end), geo.side_point(r, 1, s_end)
        c = a0.lerp(a1, 0.5)                    # mid-wall at the last station
        back = geo.centre(r, max(0.0, s_end - thick) if toward_end else min(L, s_end + thick))
        d = c - back                            # the wall's direction there
        res = None
        if d.length() > 1e-6:
            hit = _ray_hit(sk, c, d * (1.0 / d.length()), 3.0 * thick)
            if hit is not None and _strut_ok(a0, hit, region) and _strut_ok(hit, a1, region):
                res = hit
        cap_cache[key] = res
        if res is not None:
            geo.cache[(id(r), CAP, round(s_end, 9))] = res
        return res

    def mid_apex(r, s_end):
        """Fallback turnaround apex: the centre line half a thickness on
        from s_end (towards the dead end), if both legs stay inside."""
        L = r.length
        toward_end = s_end > 0.5 * L
        q = geo.centre(r, min(L, s_end + 0.5 * thick) if toward_end else max(0.0, s_end - 0.5 * thick))
        a0, a1 = geo.landing(r, 0, s_end), geo.landing(r, 1, s_end)
        if q.dist(a0.lerp(a1, 0.5)) < 0.2 * thick or not (_strut_ok(a0, q, region) and _strut_ok(q, a1, region)):
            return None
        geo.cache[(id(r), MID, round(s_end, 9))] = q
        return q

    def segments(r):
        """Fixed support points of a run (ends / corners) → segments with
        their station-parameter length U."""
        cs = [c['s'] for c in corners(r)]
        lo, hi = span(r)
        if r.cycle:
            pts_ = cs if cs else [0.0]
            tr = tracked.get(id(r))
            if tr is not None and tr.get('seam_s') is not None:
                if not cs:
                    pts_ = [tr['seam_s']]                  # the reference's seam, carried
                else:                                      # the reference's FIRST corner first
                    k0 = min(range(len(cs)), key=lambda i: min(abs(cs[i] - tr['seam_s']),
                                                              r.length - abs(cs[i] - tr['seam_s'])))
                    pts_ = cs[k0:] + [c + r.length for c in cs[:k0]]
            bounds = [(pts_[i], pts_[i + 1] if i + 1 < len(pts_) else pts_[0] + r.length)
                      for i in range(len(pts_))]
        else:
            pts_ = [lo] + cs + [hi]
            bounds = list(zip(pts_, pts_[1:]))

        def u(x):
            if r.cycle and x > r.length:
                return geo.u_of_s(r, r.length) + geo.u_of_s(r, x - r.length)
            return geo.u_of_s(r, x)
        return [(a, b, max(1e-6, u(b) - u(a))) for a, b in bounds]

    def gap_ok(r, a, b, n):
        st = seg_stations(r, a, b, n)
        return max(y - x for x, y in zip(st, st[1:])) <= DMAX + 1e-6

    def seg_options(U, parity=None, lo_n=1, Ls=None, r=None, a=None, b=None):
        """Admissible stitch counts for a segment, cheapest first: never a
        support gap beyond the maximum unsupported distance (measured on
        the real centre-line length Ls) if avoidable."""
        n_min = max(lo_n, int(math.ceil(max(U, Ls or 0.0) / DMAX - 1e-9)))
        cands = [n for n in range(n_min, max(n_min, int(math.ceil(U / S))) + 4)
                 if parity is None or n % 2 == parity]
        if r is not None:
            # stations are even in u (inner-face weighted): in a bend one
            # real gap can exceed the mean — add stitches until none does
            while cands and not any(gap_ok(r, a, b, n) for n in cands):
                cands = [n + 2 for n in cands]
            good = [n for n in cands if gap_ok(r, a, b, n)]
            cands = good or cands
        return sorted(cands, key=lambda n: (_spacing_cost(U, n, S), n))

    def _tracked_counts(r, segs, tr, mode):
        """The reference's stitch count of each segment (matched by its mapped
        midpoint), changed ONLY where it is physically impossible here: a
        gap beyond the maximum unsupported distance, or a pitch under half
        the target (congestion). A single pass changes by 2 (keeps its
        parity, so junction pairing is unchanged)."""
        L = r.length
        out, changed = [], 0
        mids = tr['segmid']
        for a, b, U in segs:
            m = geo.centre(r, ((a + b) / 2) % L if r.cycle else (a + b) / 2)
            j = min(range(len(mids)), key=lambda i: mids[i].dist(m))
            n0 = tr['counts'][j]
            step = 1 if mode == 'two' else 2
            ok = lambda n: n >= 1 and gap_ok(r, a, b, n) and U / n >= 0.5 * S - 1e-9
            n = n0
            if not ok(n):
                cands = sorted({n0 + step * k for k in range(-20, 21) if n0 + step * k >= 1},
                               key=lambda x: (abs(x - n0), x))
                n = next((x for x in cands if ok(x)), n0)
                changed += n != n0
            out.append(n)
        return out, changed

    count_cache = {}

    def counts(r, mode, parity=None):
        """Stitch counts per segment and their spacing cost.
        mode 'single': one pass; total stitches (incl. one brace per
        corner) of the given parity. mode 'two': two complementary
        interleaved phases — segments between fixed points odd, the
        dead-end segment even."""
        key = (id(r), mode, parity)
        if key in count_cache:
            return count_cache[key]
        segs = segments(r)
        ncorner = len(corners(r))
        tr = tracked.get(id(r))
        if tr is not None and tr.get('segmid') is not None and len(tr['segmid']) == len(segs):
            res, changed = _tracked_counts(r, segs, tr, mode)
            if mode != 'two' and parity is not None and (sum(res) + ncorner) % 2 != parity:
                # the requested parity (junction pairing) differs from the
                # reference's here: ONE segment changes by one stitch — the
                # cheapest feasible — never a pass ending on the wrong face
                opts = [(abs(_spacing_cost(U, n + d, S) - _spacing_cost(U, n, S)), i, n + d)
                        for i, ((a, b, U), n) in enumerate(zip(segs, res)) for d in (1, -1)
                        if n + d >= 1 and gap_ok(r, a, b, n + d)]
                if opts:
                    _, i, n = min(opts)
                    res[i] = n
                    changed += 1
            track_note['changed_segments'] += changed
            tr['changed'] = tr.get('changed') or bool(changed)
            cost = sum(_spacing_cost(U, n, S) for (a, b, U), n in zip(segs, res))
            count_cache[key] = (res, cost)
            return count_cache[key]
        if mode == 'two':
            # mirrored phases land every station (one on each face), so
            # any count works: both phases always end on opposite faces
            res = [seg_options(U, None, 1, b - a, r, a, b)[0] for a, b, U in segs]
            cost = sum(_spacing_cost(U, n, S) for (a, b, U), n in zip(segs, res))
            count_cache[key] = (res, cost)
            return count_cache[key]
        res = [seg_options(U, Ls=b - a, r=r, a=a, b=b)[0] for a, b, U in segs]
        if parity is not None and (sum(res) + ncorner) % 2 != parity:
            best = None
            for i, (a, b, U) in enumerate(segs):
                for n in seg_options(U, Ls=b - a, r=r, a=a, b=b):
                    if n % 2 != res[i] % 2:
                        dc = _spacing_cost(U, n, S) - _spacing_cost(U, res[i], S)
                        if best is None or dc < best[0]:
                            best = (dc, i, n)
                        break
            if best:
                res[best[1]] = best[2]
        cost = sum(_spacing_cost(U, n, S) for (a, b, U), n in zip(segs, res))
        count_cache[key] = (res, cost)
        return count_cache[key]

    def _dead_end_last(r):
        return not r.cycle and (r.b[0] == 'E' or r.a[0] == 'E')

    jamb_cache = {}
    jamb_end_cache = {}               # run → [(s of a junction end, (P, Q))]: jamb lines through it
    jamb_matched = set()              # station lines lying across some run of this region

    def jambs_of(r):
        """Lineage jamb lines lying across this run: [(s, {side: point})]
        (the line's exact face points)."""
        if id(r) in jamb_cache:
            return jamb_cache[id(r)]
        out = []
        if stations:
            L = r.length
            lo, hi = span(r)
            n = max(8, int(L / max(0.5, 0.25 * thick)))
            ss = [L * k / n for k in range(n + 1)]
            for P, Q in stations:
                M = P.lerp(Q, 0.5)
                d, s0 = min((geo.centre(r, x).dist(M), x) for x in ss)
                if d > 0.75 * thick:
                    continue                                 # not across this run
                a, b = max(0.0, s0 - L / n), min(L, s0 + L / n)
                for _ in range(40):                          # refine (golden section)
                    m1, m2 = a + (b - a) * 0.382, a + (b - a) * 0.618
                    if geo.centre(r, m1).dist(M) <= geo.centre(r, m2).dist(M):
                        b = m2
                    else:
                        a = m1
                sj = (a + b) / 2
                if not r.cycle and not (lo + 0.5 * thick + 1e-6 < sj < hi - 0.5 * thick - 1e-6):
                    # a jamb line AT a junction end of the run (an opening
                    # cutting the host where this wall attaches): the run's two
                    # passes turn round through the line's midpoint there (a
                    # cap V into the junction) instead of handing off
                    for nd, s_end in ((r.a, 0.0), (r.b, L)):
                        if nd[0] != 'J' or geo.centre(r, s_end).dist(M) > thick:
                            continue
                        jamb_end_cache.setdefault(id(r), []).append((s_end, (P, Q)))
                        jamb_matched.add((round(P.x, 6), round(P.y, 6), round(Q.x, 6), round(Q.y, 6)))
                    continue
                p0 = geo.side_point(r, 0, sj)
                pts_ = {0: P, 1: Q} if P.dist(p0) <= Q.dist(p0) else {0: Q, 1: P}
                if not _strut_ok(pts_[0], pts_[1], region):
                    continue
                out.append((sj, pts_))
                jamb_matched.add((round(P.x, 6), round(P.y, 6), round(Q.x, 6), round(Q.y, 6)))
        jamb_cache[id(r)] = out
        return out

    def seg_stations(r, a, b, n):
        """n + 1 station positions from a to b, even in the station
        parameter u (b may run past the seam of a loop)."""
        L = r.length
        def u(x):
            return geo.u_of_s(r, x) if x <= L else geo.u_of_s(r, L) + geo.u_of_s(r, x - L)
        def s_of(uu):
            UL = geo.u_of_s(r, L)
            return geo.s_of_u(r, uu) if uu <= UL else L + geo.s_of_u(r, uu - UL)
        ua, ub = u(a), u(b)
        return [a] + [s_of(ua + (ub - ua) * j / n) for j in range(1, n)] + [b]

    # -- 1. how many passes per run (route inspection on the skeleton) -----
    # A run crossed by a lineage JAMB line carries TWO passes (module doc
    # §5): it contributes an even degree at both ends, so route inspection
    # runs on the remaining runs only.
    jammed = {ri for ri, r in enumerate(runs) if jambs_of(r) or jamb_end_cache.get(id(r))} \
        if stations else set()
    K = nx.MultiGraph()
    for ri, r in enumerate(runs):
        if not r.cycle and ri not in jammed:
            K.add_edge(r.a, r.b, key=ri, weight=r.length)
    mult = {ri: 2 if ri in jammed else 1 for ri in range(len(runs))}
    for comp in nx.connected_components(K):
        sub = K.subgraph(comp)
        odd = [v for v in comp if sub.degree(v) % 2 == 1]
        if not odd:
            continue
        has_junction = any(v[0] == 'J' for v in comp)
        if not has_junction:
            if closed:                   # a lone run closes as an out-and-back loop
                for u, v, k in sub.edges(keys=True):
                    mult[k] = 2
            continue                     # (legacy: a lone run, single pass, open route)
        H = nx.Graph()
        simple = nx.Graph()
        for u, v, k, d in sub.edges(keys=True, data=True):
            if not simple.has_edge(u, v) or d['weight'] < simple[u][v]['weight']:
                simple.add_edge(u, v, weight=d['weight'], key=k)
        dist = dict(nx.all_pairs_dijkstra(simple))
        for i, u in enumerate(odd):
            for v in odd[i + 1:]:
                H.add_edge(u, v, weight=dist[u][0][v])
        ends = 0 if prefer_closed else 2
        for k in range(ends):
            for u in odd:
                H.add_edge(('__end__', k), u, weight=0.0)
        for u, v in nx.min_weight_matching(H):
            if isinstance(u, tuple) and u[0] == '__end__' or \
                    isinstance(v, tuple) and v[0] == '__end__':
                continue
            p = dist[u][1][v]
            for x, y in zip(p, p[1:]):
                ri = simple[x][y]['key']
                mult[ri] = 2 if mult[ri] == 1 else 1


    jamb_report = {}
    jamb_lines = {}                   # (run, s) → (P, Q): the jamb line's face points

    def between_jambs(A, B, fixed, k, sv):
        """A[k] is the previous jamb, between the same two stations as sv:
        add a station half way between the two jambs (the tails of A and B
        swap, so every later landing keeps its face)."""
        ins = (A[k][0] + sv) / 2
        a_side = next(A[i][1] for i in range(k, -1, -1) if A[i][1] in (0, 1))
        A, B = (A[:k + 1] + [(ins, 1 - a_side)] + B[k + 1:],
                B[:k + 1] + [(ins, a_side)] + A[k + 1:])
        fixed = {i + 1 if i > k else i for i in fixed} | {k + 1}
        return A, B, fixed, k + 1

    def cross_at_jambs(r, A, B, fixed):
        """JAMB CROSSINGS (module doc §5): the two mirrored phases A, B of a
        doubled run cross mid-wall between consecutive stations; at every
        lineage jamb line they are made to cross EXACTLY at the line's
        midpoint M (a shared vertex, side JAMB). The two neighbouring
        stations are re-centred about the line where they are free to move
        (limited, never a segment bound), so the stitches are bent only
        slightly. Where two jambs fall between the same two stations, one
        station is added between them (both phases: their tails swap, so
        every later landing keeps its face). Returns the new (A, B);
        unresolved jambs are reported in jamb_report."""
        L = r.length
        rep = jamb_report.setdefault(id(r), {'resolved': 0, 'unresolved': []})
        A, B = list(A), list(B)
        fixed = set(fixed)
        sgn = 1.0 if A[-1][0] >= A[0][0] else -1.0
        lo_s, hi_s = sorted((A[0][0], A[-1][0]))
        todo = []
        for sj, pts_ in jambs_of(r):
            cands = [sj, sj + L] if r.cycle else [sj]
            sv = next((c for c in cands if lo_s + 1e-6 < c < hi_s - 1e-6), None)
            if sv is None:
                rep['unresolved'].append({'s': round(sj, 3), 'why': 'outside the pass'})
                continue
            todo.append((sv, pts_))
        todo.sort(key=lambda t: sgn * t[0])
        margin = max(0.5 * thick, 1e-3)
        for sv, pts_ in todo:
            pos = lambda e: sgn * e[0]
            # interval k … k+1 (indices into A; JAMB entries are not stations)
            k = None
            for i in range(len(A) - 1):
                if pos(A[i]) < sgn * sv < pos(A[i + 1]):
                    k = i
                    break
            if k is None:
                rep['unresolved'].append({'s': round(sv % L if r.cycle else sv, 3),
                                          'why': 'on a station'})
                continue
            if A[k][1] == JAMB:
                # the previous jamb is between the same two stations: add a
                # station half way between the two jambs
                A, B, fixed, k = between_jambs(A, B, fixed, k, sv)
            # re-centre the two stations about the line where free
            def gap(i, j):
                return abs(A[j][0] - A[i][0])
            p = gap(k, k + 1)
            want = {k: sv - sgn * p / 2, k + 1: sv + sgn * p / 2}
            for i, nb in ((k, k - 1), (k + 1, k + 2)):
                if i in fixed or A[i][1] not in (0, 1) or not (0 <= nb < len(A)):
                    continue
                cur = A[i][0]
                lim = 0.35 * p
                tgt = cur + max(-lim, min(lim, want[i] - cur))
                # keep the neighbouring gap within [½ its length, DMAX]
                g0 = abs(cur - A[nb][0])
                g1 = abs(tgt - A[nb][0])
                if g1 < 0.5 * g0:
                    tgt = A[nb][0] + (cur - A[nb][0]) * 0.5
                elif g1 > DMAX:
                    tgt = A[nb][0] + (cur - A[nb][0]) * (DMAX / max(g0, 1e-9))
                if not (min(A[nb][0], sv) < tgt < max(A[nb][0], sv)):
                    continue
                A[i] = (tgt, A[i][1])
                B[i] = (tgt, B[i][1])
            near = [i for i in (k, k + 1) if abs(A[i][0] - sv) < margin]
            if near and all(0 < i < len(A) - 1 and A[i][1] in (0, 1) and A[i + 1][1] != JAMB
                            for i in near) and len(near) == 1:
                # a FIXED station (a corner) right at the jamb: the crossing
                # replaces it — drop that station from both phases (their tails
                # swap, so every later landing keeps its face). Beside the
                # previous jamb, a station then goes half way between the two.
                i = near[0]
                if abs(A[i + 1][0] - A[i - 1][0]) <= 1.5 * DMAX:
                    A, B = A[:i] + B[i + 1:], B[:i] + A[i + 1:]
                    fixed = {j - 1 if j > i else j for j in fixed if j != i}
                    k = next(j for j in range(len(A) - 1) if pos(A[j]) < sgn * sv < pos(A[j + 1]))
                    if A[k][1] == JAMB:
                        A, B, fixed, k = between_jambs(A, B, fixed, k, sv)
                    rep.setdefault('replaced_stations', 0)
                    rep['replaced_stations'] += 1
            if min(abs(sv - A[k][0]), abs(A[k + 1][0] - sv)) < margin:
                rep['unresolved'].append({'s': round(sv % L if r.cycle else sv, 3),
                                          'why': 'too close to a corner / junction / wall end'})
                continue
            M = pts_[0].lerp(pts_[1], 0.5)
            geo.cache[(id(r), JAMB, round(sv % L if r.cycle else sv, 9))] = M
            A.insert(k + 1, (sv, JAMB))
            B.insert(k + 1, (sv, JAMB))
            fixed = {i + 1 if i > k else i for i in fixed}
            jamb_lines[(id(r), round(sv % L if r.cycle else sv, 9))] = (pts_[0], pts_[1])
            rep['resolved'] += 1
            rep.setdefault('lines', set()).add(_line_key(pts_[0], pts_[1]))
        return A, B

    # the reference's passes per run. Where they leave a skeleton node odd
    # (the reference had another junction structure there), parity is
    # repaired along the cheapest paths — through UNTRACKED (changed) runs
    # first, tracked ones only if unavoidable — so the route still closes
    # and unchanged walls keep their passes.
    tmult = dict(mult)
    for ri, r in enumerate(runs):
        tr = tracked.get(id(r))
        if tr is not None and ri not in jammed and not r.cycle and tr.get('mult') in (1, 2):
            tmult[ri] = tr['mult']
    if tmult != mult:
        deg = {}
        for ri, r in enumerate(runs):
            if not r.cycle:
                for v in (r.a, r.b):
                    deg[v] = deg.get(v, 0) + tmult[ri]
        odd = sorted(v for v, d in deg.items() if d % 2)
        if odd and prefer_closed:
            Kw = nx.Graph()
            for ri, r in enumerate(runs):
                if r.cycle or ri in jammed:
                    continue
                w = r.length * (100.0 if id(r) in tracked else 1.0)
                if not Kw.has_edge(r.a, r.b) or w < Kw[r.a][r.b]['weight']:
                    Kw.add_edge(r.a, r.b, weight=w, key=ri)
            Hm = nx.Graph()
            for i, u in enumerate(odd):
                for v in odd[i + 1:]:
                    if u in Kw and v in Kw and nx.has_path(Kw, u, v):
                        Hm.add_edge(u, v, weight=nx.dijkstra_path_length(Kw, u, v))
            m_ = nx.min_weight_matching(Hm) if Hm.number_of_edges() else set()
            if 2 * len(m_) == len(odd):
                for u, v in m_:
                    pth = nx.dijkstra_path(Kw, u, v)
                    for x, y in zip(pth, pth[1:]):
                        ri = Kw[x][y]['key']
                        tmult[ri] = 3 - tmult[ri]
                        if id(runs[ri]) in tracked:
                            track_note['mult_changed'] = track_note.get('mult_changed', 0) + 1
                mult = tmult
            else:
                track_note['mult_replanned'] = True          # keep route inspection's solution
        else:
            mult = tmult

    def layout(r, mode, s0=0, parity=None):
        """Landing sequences [(s, side)] of a run: one pass (single /
        loop) or the two phases (A, B) of an out-and-back / double run,
        oriented from the junction end."""
        ns_, _ = counts(r, 'two' if mode == 'two' else 'single', parity)
        segs = segments(r)
        cmap = {round(c['s'], 9): c for c in corners(r)}
        if mode != 'two':
            seq = []
            side = s0
            start = segs[0][0]
            if r.cycle and round(start, 9) in cmap:
                seq += [(start, side), (start, 1 - side)]     # brace at the seam corner
                side = 1 - side
            else:
                seq.append((start, side))
            for i, ((a, b, U), n) in enumerate(zip(segs, ns_)):
                st = seg_stations(r, a, b, n)
                for x in st[1:]:
                    side = 1 - side
                    seq.append((x, side))
                last = i == len(segs) - 1
                if not last and round(b % r.length if r.cycle else b, 9) in cmap:   # corner brace
                    side = 1 - side
                    seq.append((b, side))
            # CAP V at open wall ends: the end face is landed through the
            # cap between both faces (the run's own sides at the ends are
            # unchanged, so junction pairing is not affected)
            if not r.cycle:
                if r.a[0] == 'E' and cap_point(r, seq[0][0]) is not None:
                    x0, sd0 = seq[0]
                    seq = [(x0, 1 - sd0), (x0, CAP)] + seq
                if r.b[0] == 'E' and cap_point(r, seq[-1][0]) is not None:
                    x1, sd1 = seq[-1]
                    seq = seq + [(x1, CAP), (x1, 1 - sd1)]
            return [seq]
        # Two MIRRORED phases (half a cycle apart), oriented from the
        # junction end: at every station A lands one face and B the other,
        # so each face is landed at EVERY station (combined support on a
        # face ≈ the target, evenly) and the phases cross mid-wall between
        # stations. (Pass 7 interleaved them a quarter cycle apart: each
        # face then had alternating ~S / ~3S gaps.)
        rev = (not r.cycle and r.a[0] == 'E')
        segs_o = [(b, a, U) for a, b, U in reversed(segs)] if rev else segs
        ns_o = list(reversed(ns_)) if rev else ns_
        A, B = [(segs_o[0][0], s0)], [(segs_o[0][0], 1 - s0)]
        sa = s0
        jamb_report[id(r)] = {'resolved': 0, 'unresolved': []}         # (the last layout wins)
        fixed = {0}                       # segment bounds: ends, corners
        for (a, b, U), n in zip(segs_o, ns_o):
            st = seg_stations(r, a, b, n) if not rev else \
                list(reversed(seg_stations(r, b, a, n)))
            for k in range(1, n + 1):
                sa = 1 - sa
                A.append((st[k], sa))
                B.append((st[k], 1 - sa))
            fixed.add(len(A) - 1)
        if jambs_of(r):
            A, B = cross_at_jambs(r, A, B, fixed)
        for s_end, (P, Q) in jamb_end_cache.get(id(r), ()):
            # both phases END at the jamb line's midpoint M instead of handing
            # off at the junction corners (their two slots leave the junction
            # pairing): a cap V into the junction. A member whose host is cut
            # there ends its wall on that line — both passes meet at M on it.
            M = P.lerp(Q, 0.5)
            geo.cache[(id(r), JAMB, round(s_end, 9))] = M
            jamb_lines[(id(r), round(s_end, 9))] = (P, Q)
            for seq in (A, B):
                if abs(seq[-1][0] - s_end) < 1e-9:
                    seq[-1] = (s_end, JAMB)
                elif abs(seq[0][0] - s_end) < 1e-9:
                    seq[0] = (s_end, JAMB)
            jamb_report[id(r)]['resolved'] += 1
            jamb_report[id(r)].setdefault('lines', set()).add(_line_key(P, Q))
        if _dead_end_last(r):
            # TURNAROUND = CAP V: both phases end at the last station on
            # opposite faces, joined through a landing ON the cap — the end,
            # both cap corners and the cap face are supported; no rung, no
            # retrace. Without a cap landing the V's apex is the centre line
            # half a thickness on (still no rung across the wall).
            x = A[-1][0]
            if cap_point(r, x) is not None:
                loop = A + [(x, CAP)] + list(reversed(B))
            elif mid_apex(r, x) is not None:
                loop = A + [(x, MID)] + list(reversed(B))
            else:
                loop = A + list(reversed(B))
            if r.a[0] == 'E' and r.b[0] == 'E':
                # a LONE run: the loop also turns round at the start end
                # (second cap V) and closes on itself — the second wave
                x0 = A[0][0]
                if cap_point(r, x0) is not None:
                    loop = loop + [(x0, CAP), A[0]]
                elif mid_apex(r, x0) is not None:
                    loop = loop + [(x0, MID), A[0]]
                else:
                    loop = loop + [A[0]]
            return [loop]
        return [A, B]

    # -- 2. sides at run ends, junction pairing, stitch counts -------------
    # slot = (run index, end 0|1, side 0|1)
    single = [ri for ri, r in enumerate(runs) if mult[ri] == 1 and not r.cycle]
    flip = 1 if variation % 2 else 0
    junction_ends = {}
    for ri, r in enumerate(runs):
        if r.cycle:
            continue
        for end, nd in ((0, r.a), (1, r.b)):
            if nd[0] == 'J' and not any(abs(s_e - (0.0 if end == 0 else r.length)) < 1e-9
                                        for s_e, _ in jamb_end_cache.get(id(r), ())):
                junction_ends.setdefault(nd, []).append((ri, end))      # (a junction-end jamb: no hand-off)

    def slot_idx(ri, end, side):
        return runs[ri].chords[0 if end == 0 else -1][side]

    def slot_point(ri, end, side):
        return sk.pts[slot_idx(ri, end, side)]

    def corner_arc(x, y):
        """Arc length along the boundary between two slots if they sit on
        one short corner stretch (a junction fillet / corner), else None."""
        i, j = slot_idx(*x), slot_idx(*y)
        if i == j:
            return 0.0
        ri = sk.ring_of[i]
        if sk.ring_of[j] != ri:
            return None
        L = sk.R.length(ri)
        d = abs(sk.arc_of[i] - sk.arc_of[j])
        d = min(d, L - d)
        return d if d <= 1.5 * thick else None

    def conn_cost(p, q):
        if p is q or p.dist(q) < 1e-9:
            return 0.0, None
        if _strut_ok(p, q, region):
            same = _same_face(sk, p, q)
            return p.dist(q) * (SAME_FACE_W if same else 1.0), [p, q]
        return 1e6, None

    # CROSS-Z: the reference's junction pairing, in this plan's run / end /
    # side terms — strongly preferred where still feasible, so a hub keeps
    # its route identity instead of flipping on near-equal costs
    preferred = set()
    if tracked:
        loc = {tuple(tr['uid']): (ri, tr) for ri, r in enumerate(runs)
               for tr in [tracked.get(id(r))] if tr is not None and tr.get('uid')}
        def to_local(tr, e, sd):
            return (e if tr['fwd'] else 1 - e), (sd if tr['same_side'] else 1 - sd)
        for u_, (ri, tr) in loc.items():
            for key_, (puid, pe, ps) in (tr.get('partners') or {}).items():
                if tuple(puid) not in loc:
                    continue
                e, sd = (int(v) for v in key_.split(','))
                rj, trj = loc[tuple(puid)]
                a_ = (ri,) + to_local(tr, e, sd)
                b_ = (rj,) + to_local(trj, pe, ps)
                preferred.add(frozenset((a_, b_)))

    cache = {}

    def cluster_cost(nd, slots):
        key = (nd, tuple(sorted(slots)))
        if key in cache:
            return cache[key]
        if len(slots) % 2:
            cache[key] = (1e9, [])
            return cache[key]
        G = nx.Graph()
        pts_ = {sl: slot_point(*sl) for sl in slots}
        for x, y in itertools.combinations(slots, 2):
            ca = corner_arc(x, y) if x[0] != y[0] or x[1] != y[1] else None
            if ca is not None:
                c = 0.05 * ca           # both land on the corner apex
            else:
                c, _ = conn_cost(pts_[x], pts_[y])
            if c < 1e5 and frozenset((x, y)) in preferred:
                c *= 0.01
            G.add_edge(x, y, weight=c)
        m = nx.min_weight_matching(G) if G.number_of_edges() else set()
        tot = sum(G[x][y]['weight'] for x, y in m)
        if 2 * len(m) != len(slots):
            tot += 1e9
        cache[key] = (tot, sorted(m))
        return cache[key]

    def solve(choice):
        """choice: run → (start side, parity) for single-pass runs."""
        total = 0.0
        pairs = {}
        for nd, ends_ in junction_ends.items():
            slots = []
            for ri, end in ends_:
                if mult[ri] == 2:
                    slots += [(ri, end, 0), (ri, end, 1)]
                else:
                    s0, par = choice[ri]
                    slots.append((ri, end, s0 if end == 0 else s0 ^ par))
            c, m = cluster_cost(nd, slots)
            total += c
            pairs[nd] = m
        for ri in single:
            s0, par = choice[ri]
            total += counts(runs[ri], 'single', par)[1]
        return total, pairs

    options = [(s0 ^ flip, par) for s0 in (0, 1) for par in (0, 1)]

    def search(fixed_choice):
        """Best start sides / parities of the single-pass runs, the runs in
        fixed_choice kept (the reference's sides)."""
        free = [ri for ri in single if ri not in fixed_choice]
        if len(free) <= 6:
            best = None
            for combo in itertools.product(options, repeat=len(free)):
                choice = dict(fixed_choice)
                choice.update(zip(free, combo))
                tot, pairs = solve(choice)
                if best is None or tot < best[0] - 1e-9:
                    best = (tot, choice, pairs)
            return best
        choice = dict(fixed_choice)                          # coordinate descent
        choice.update({ri: (flip, (sum(counts(runs[ri], 'single')[0]) + len(corners(runs[ri]))) % 2)
                       for ri in free})
        tot0, pairs0 = solve(choice)
        best = (tot0, dict(choice), pairs0)
        for _ in range(4):
            improved = False
            for ri in free:
                for opt in options:
                    trial = dict(best[1]); trial[ri] = opt
                    tot, pairs = solve(trial)
                    if tot < best[0] - 1e-9:
                        best = (tot, trial, pairs); improved = True
            if not improved:
                break
        return best

    kept = {ri: tracked[id(runs[ri])]['choice'] for ri in single
            if id(runs[ri]) in tracked and tracked[id(runs[ri])].get('choice')}
    best = search(kept) if single else (0.0, {}, solve({})[1])
    if kept and best[0] >= 1e9:                   # the carried sides cannot pair here
        best = search({})
        track_note['choice_replanned'] = True
    _, choice, pairs = best

    # -- 3. geometry --------------------------------------------------------
    # PHYSICAL CONTACT: landings on the faces stop `contact` off the face
    # centreline (across the wall); pass ends at junctions and TRANSFER
    # landings stay exact (they are where the route joins the faces).
    transfers = set()
    contact_clamped = [False]

    def _toward(p, q, d, clear=None):
        """p moved towards q until it is d clear of its face (clear(x) =
        distance to that face): along a slanted chord (a corner brace) the
        straight offset d would sit closer than d to the face."""
        v = q - p
        L = v.length()
        if d <= 0 or L < 1e-12:
            return p
        lim = 0.45 * L                      # never past the middle of the wall
        t = min(d, lim)
        if clear is not None:
            for _ in range(8):
                c = clear(p + v * (t / L))
                if c >= d - 1e-6 or t >= lim - 1e-12:
                    break
                t = min(lim, t + (d - c) * 1.5)
        if t < d - 1e-9 and (clear is None or clear(p + v * (t / L)) < d - 1e-3):
            contact_clamped[0] = True
        return p + v * (t / L)

    # all face segments, gridded: a stitch must stay `contact` clear of the
    # faces except at its own exact (route-connection) ends
    _fsegs = [(ring[i], ring[(i + 1) % len(ring)]) for ring in rings for i in range(len(ring))]
    _cell = max(2.0, 2.0 * contact)
    _fgrid: dict = {}
    for k_, (a_, b_) in enumerate(_fsegs):
        for gx in range(int(math.floor(min(a_.x, b_.x) / _cell)), int(math.floor(max(a_.x, b_.x) / _cell)) + 1):
            for gy in range(int(math.floor(min(a_.y, b_.y) / _cell)), int(math.floor(max(a_.y, b_.y) / _cell)) + 1):
                _fgrid.setdefault((gx, gy), []).append(k_)

    def _face_d(q):
        gx, gy = int(math.floor(q.x / _cell)), int(math.floor(q.y / _cell))
        best = float('inf')
        for dx_ in (-1, 0, 1):
            for dy_ in (-1, 0, 1):
                for k_ in _fgrid.get((gx + dx_, gy + dy_), ()):
                    best = min(best, _seg_d(q, *_fsegs[k_]))
        return best

    def _clear_poly(pts, exact_a, exact_b):
        """Every sample at least `contact` from the faces. A stitch into or
        out of an EXACT end (transfer / junction hand-off) is the route
        connection itself and is not held off the face."""
        if contact <= 0 or exact_a or exact_b:
            return True
        return all(_face_d(q) >= contact * 0.98 for q in pts[1:-1])

    def _clear_fn(r, side, s):
        """Distance to `side`'s face near station s (windowed)."""
        ri, est = geo._chord_arc(r, side, s)
        ring = sk.R.rings[ri]
        n = len(ring)
        ks = list(geo._segs(ri, est, 3.0 * thick + 2.0 * contact))
        return lambda q: min(_seg_d(q, ring[k], ring[(k + 1) % n]) for k in ks)

    def is_contact(r, side, s):
        if contact <= 0 or side not in (0, 1, CAP):
            return False
        if not r.cycle and ((s <= 1e-9 and r.a[0] == 'J') or (s >= r.length - 1e-9 and r.b[0] == 'J')):
            return False                    # a junction hand-off: route connection
        return (id(r), side, round(s % r.length if r.cycle else s, 9)) not in transfers

    def lp(r, side, s):
        """The landing point actually used (contact rail or exact)."""
        p = geo.landing(r, side, s)
        if not is_contact(r, side, s):
            return p
        if side == CAP:                     # off the end face, into the wall
            return _toward(p, geo.centre(r, s), contact)
        return _toward(p, geo.landing(r, 1 - side, s), contact, _clear_fn(r, side, s))

    def rail(r, side, s):
        """Nominal stitch point on `side` at s: the face, or its contact rail."""
        p = geo.side_point(r, side, s)
        if contact <= 0:
            return p
        return _toward(p, geo.side_point(r, 1 - side, s), contact, _clear_fn(r, side, s))

    def stitch(r, s_a, side_a, s_b, side_b):
        """One stitch from face side_a at station s_a to the opposite face
        at s_b, following the wall between them: each point lies on the
        local face-to-face chord (straight walls: a straight stitch; curved
        walls: it bends with the wall). Wave: a half sine tangent to both
        faces, so the pass is one smooth wave through its landings. A
        corner brace (same station, both faces) and cap-V legs are
        straight."""
        pa, pb = lp(r, side_a, s_a), lp(r, side_b, s_b)
        if abs(s_a - s_b) < 1e-9 or CAP in (side_a, side_b) or MID in (side_a, side_b) \
                or JAMB in (side_a, side_b):
            return [pa, pb]
        at_junction = (not r.cycle) and ((min(s_a, s_b) <= 1e-9 and r.a[0] == 'J') or
                                         (max(s_a, s_b) >= r.length - 1e-9 and r.b[0] == 'J'))
        at_corner = any(abs(c['s'] - x) < 1e-9 for c in corners(r) for x in (s_a, s_b % r.length if r.cycle else s_b))
        straight_ok = _strut_ok(pa, pb, region)
        wave = pattern == 'wave'
        curved = wave or not _straight(geo, r, s_a, s_b)
        if not wave and (at_junction or at_corner) and straight_ok:
            curved = False              # zigzag junction / corner stitches: straight
        if not curved:
            return [pa, pb]
        if wave:
            # WAVE: a half sine TANGENT to both faces — consecutive stitches
            # join smoothly at every landing (a continuous wave, no V). The
            # ends are blended onto the actual landing points (junction /
            # corner points may sit off the nominal face point) with a
            # smoothstep, which keeps the tangency.
            n = WAVE_SAMPLES
            ea = pa - rail(r, side_a, s_a)
            eb = pb - rail(r, side_b, s_b)
            out = [pa]
            for k in range(1, n):
                f = k / n
                s_ = s_a + f * (s_b - s_a)
                w = (1 - math.cos(math.pi * f)) / 2
                q = rail(r, side_a, s_).lerp(rail(r, side_b, s_), w)
                out.append(q + ea * (1 - w) + eb * w)
            out.append(pb)
            ex_a, ex_b = not is_contact(r, side_a, s_a), not is_contact(r, side_b, s_b)
            # beside a corner the face-to-face construction can leave the
            # wall (or, with contact rails, come closer to a face than the
            # contact): a Hermite curve leaving / meeting each face along it
            # (still tangent, still a wave) before falling back to straight.
            # CROSS-Z: the reference's construction of THIS stitch is tried
            # first (when still valid), so the shape does not flip per layer.
            order = ['wave', 'hermite', 'straight']
            if kind_ctx['pref'] in order:
                order.remove(kind_ctx['pref'])
                order.insert(0, kind_ctx['pref'])
            h = None
            for kd in order:
                if kd == 'wave':
                    if all(_strut_ok(u, v, region) or u.dist(v) < 1e-9 for u, v in zip(out, out[1:])) \
                            and _clear_poly(out, ex_a, ex_b):
                        kind_ctx['used'] = 'wave'
                        return _no_foldback(out)
                elif kd == 'hermite':
                    h = hermite(r, s_a, side_a, pa, s_b, side_b, pb)
                    if h is not None and _clear_poly(h, ex_a, ex_b):
                        kind_ctx['used'] = 'hermite'
                        return h
                elif straight_ok and (contact <= 0 or _clear_poly(_dense(pa, pb), ex_a, ex_b)):
                    kind_ctx['used'] = 'straight'
                    return [pa, pb]
            kind_ctx['used'] = 'fallback'
            if h is not None:
                return h
            if straight_ok:
                return [pa, pb]
            return dogleg(r, s_a, pa, s_b, pb) or out
        out = [pa]
        for k in range(1, 8):
            f = k / 8
            s_ = s_a + f * (s_b - s_a)
            out.append(rail(r, side_a, s_).lerp(rail(r, side_b, s_), f))
        out.append(pb)
        if not all(_strut_ok(u, v, region) or u.dist(v) < 1e-9 for u, v in zip(out, out[1:])):
            if straight_ok:
                return [pa, pb]             # the curve would leave the wall
            return dogleg(r, s_a, pa, s_b, pb) or out
        # tidy the ends: no curve point hugging a landing (a curve running
        # into a tight inner corner would overshoot it and double back)
        tidy = 0.35 * thick
        while len(out) > 2 and out[-2].dist(out[-1]) < tidy and _strut_ok(out[-3], out[-1], region):
            del out[-2]
        while len(out) > 2 and out[1].dist(out[0]) < tidy and _strut_ok(out[0], out[2], region):
            del out[1]
        return out

    def dogleg(r, s_a, pa, s_b, pb):
        """A stitch that stays IN the material where neither the face-to-face
        construction nor a straight strut does (e.g. a junction landing on a
        rounded fillet, next to a room's inner corner): it follows the
        wall's centre line between the stations, shortcut wherever a
        straight strut stays inside. None if even that leaves the wall.
        (A shortest two-strut path was tried: it hugs the fillet, grazes it
        and is split by clipping — closure comes first.)"""
        chain = [pa] + [geo.centre(r, s_a + f * (s_b - s_a)) for f in
                        (0.02, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.98)] + [pb]
        if not all(_strut_ok(u, v, region) for u, v in zip(chain, chain[1:])):
            return None
        out, i = [pa], 0
        while i < len(chain) - 1:
            j = next(j for j in range(len(chain) - 1, i, -1) if j == i + 1 or _strut_ok(chain[i], chain[j], region))
            out.append(chain[j])
            i = j
        kind_ctx['used'] = 'dogleg'
        return out

    def hermite(r, s_a, side_a, pa, s_b, side_b, pb):
        """Cubic Hermite stitch from pa to pb, tangent to the face at each
        landing (direction of travel along the wall), inside the wall."""
        sg = 1.0 if s_b > s_a else -1.0
        d = max(0.5, 0.25 * thick)
        # from the actual landing (a pinned corner point is not the
        # nominal face point of its station)
        ta = geo.side_point(r, side_a, s_a + sg * d) - pa
        tb = pb - geo.side_point(r, side_b, s_b - sg * d)
        if ta.length() < 1e-9 or tb.length() < 1e-9:
            return None
        ta, tb = ta * (1.0 / ta.length()), tb * (1.0 / tb.length())
        D = pa.dist(pb)
        for mag in (1.0, 0.6, 0.35):
            m = mag * D
            pts = [pa]
            for k in range(1, WAVE_SAMPLES):
                t = k / WAVE_SAMPLES
                t2, t3 = t * t, t * t * t
                h00, h10 = 2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t
                h01, h11 = -2 * t3 + 3 * t2, t3 - t2
                pts.append(Vec2(h00 * pa.x + h10 * m * ta.x + h01 * pb.x + h11 * m * tb.x,
                                h00 * pa.y + h10 * m * ta.y + h01 * pb.y + h11 * m * tb.y))
            pts.append(pb)
            if all(_strut_ok(u, v, region) or u.dist(v) < 1e-9 for u, v in zip(pts, pts[1:])):
                return _no_foldback(pts)
        return None

    stitch_memo = {}
    kind_ctx = {'pref': None, 'used': None}   # cross-Z: preferred / used stitch construction

    def one_stitch(r, xa, sa, xb, sb):
        # memoised by the actual landing points (junction hand-off
        # points may be moved after a first emission)
        pa, pb = lp(r, sa, xa), lp(r, sb, xb)
        key = (id(r), round(xa, 9), sa, round(xb, 9), sb, pa.x, pa.y, pb.x, pb.y, kind_ctx['pref'])
        if key not in stitch_memo:
            kind_ctx['used'] = None
            stitch_memo[key] = (stitch(r, xa, sa, xb, sb), kind_ctx['used'])
        kind_ctx['used'] = stitch_memo[key][1]
        return stitch_memo[key][0]

    def through_jamb(r, xa, sa, xj, xb, sb):
        """The ordinary stitch xa → xb, bent smoothly so that it crosses
        the jamb line exactly at its midpoint M (a vertex): the crossing X
        of the stitch with the line moves to M, the displacement fading to
        zero at both landings (raised cosine in arc length). None if the
        bent stitch would leave the wall or crowd a face."""
        full = one_stitch(r, xa, sa, xb, sb)
        P, Q = jamb_lines[(id(r), round(xj % r.length if r.cycle else xj, 9))]
        M = P.lerp(Q, 0.5)
        d = Q - P
        side = lambda q: d.x * (q.y - P.y) - d.y * (q.x - P.x)
        best = None
        for i, (u, v) in enumerate(zip(full, full[1:])):
            fu, fv = side(u), side(v)
            if (fu <= 0 <= fv or fv <= 0 <= fu) and fu != fv:
                X = u.lerp(v, fu / (fu - fv))
                if best is None or X.dist(M) < best[0]:
                    best = (X.dist(M), i, X)
        if best is None or best[0] > thick:
            if _DEBUG: print('jamb: no crossing', best and best[0])
            return None
        _, i, X = best
        pts = full[:i + 1] + [X] + full[i + 1:]
        cum = [0.0]
        for u, v in zip(pts, pts[1:]):
            cum.append(cum[-1] + u.dist(v))
        uX, U = cum[i + 1], cum[-1]
        dv = M - X
        out = []
        for q, c in zip(pts, cum):
            if c <= uX:
                w = 0.0 if uX <= 1e-12 else (1 - math.cos(math.pi * c / uX)) / 2
            else:
                w = 0.0 if U - uX <= 1e-12 else (1 - math.cos(math.pi * (U - c) / (U - uX))) / 2
            out.append(q + dv * w)
        out[0], out[i + 1], out[-1] = full[0], M, full[-1]
        if not all(_strut_ok(u, v, region) or u.dist(v) < 1e-6 for u, v in zip(out, out[1:])):
            if _DEBUG: print('jamb: leaves wall')
            return None
        if contact > 0 and not all(_face_d(q) >= contact * 0.98 for q in out[1:-1]
                                   if q.dist(out[0]) > contact and q.dist(out[-1]) > contact):
            if _DEBUG: print('jamb: crowds face', min(_face_d(q) for q in out[1:-1]), contact, X, M)
            return None
        return out[:i + 2], out[i + 1:]

    def emit(r, seq, prefs=None, record=None):
        poly = [lp(r, seq[0][1], seq[0][0])]
        k = 0
        while k < len(seq) - 1:
            (xa, sa), (xb, sb) = seq[k], seq[k + 1]
            kind_ctx['pref'] = prefs[k] if prefs and k < len(prefs) else None
            if sb == JAMB and k + 2 < len(seq):
                xc, sc = seq[k + 2]
                halves = through_jamb(r, xa, sa, xb, xc, sc)
                if halves is None:
                    rep_ = jamb_report.setdefault(id(r), {'resolved': 0, 'unresolved': []})
                    rep_['bend_failed'] = rep_.get('bend_failed', 0) + 1
                    halves = (one_stitch(r, xa, sa, xb, sb), one_stitch(r, xb, sb, xc, sc))
                poly.extend(halves[0][1:])
                poly.extend(halves[1][1:])
                if record is not None:
                    record += [kind_ctx['used'], kind_ctx['used']]
                k += 2
                continue
            poly.extend(one_stitch(r, xa, sa, xb, sb)[1:])
            if record is not None:
                record.append(kind_ctx['used'])
            k += 1
        kind_ctx['pref'] = None
        return poly

    def run_crowding(r, sq):
        """Quality of a run's candidate lattice measured like the route
        checks: (generated-bead hotspots, tiny generated-bounded cells,
        worst multiplicity) — used to orient two-phase runs at corners."""
        import route_quality as RQ
        import network as N_
        segs_, polys_ = [], []
        for one in sq:
            poly = emit(r, one)
            polys_.append(poly)
            segs_ += [((p_.x, p_.y), (q_.x, q_.y)) for p_, q_ in zip(poly, poly[1:])]
        grid = RQ._SegGrid(segs_, 2 * RQ.CLEARANCE_RADIUS)
        probes = {p_ for sg in segs_ for p_ in sg}
        for k, (a_, b_) in enumerate(segs_):
            mid = ((a_[0] + b_[0]) / 2, (a_[1] + b_[1]) / 2)
            for j in grid.near(mid, math.dist(a_, b_) / 2):
                if j > k:
                    x_ = RQ._cross(a_, b_, *segs_[j])
                    if x_:
                        probes.add(x_)
        rho = RQ.CLEARANCE_RADIUS
        mults = [sum(RQ._seg_disk_len(*segs_[k], p_, rho) for k in grid.near(p_, rho)) / (2 * rho)
                 for p_ in probes]
        hot = sum(1 for m_ in mults if m_ > RQ.MAX_GENERATED_BEADS + RQ.HOTSPOT_TOL)
        beads = [N_.Bead(f'f{k}', ring, True, N_.FACE, 'U') for k, ring in enumerate(rings)]
        beads += [N_.Bead(f'g{k}', poly, False, N_.INTERNAL, 'G') for k, poly in enumerate(polys_)]
        arr = N_.Arrangement(beads)
        gen_len = {}
        for bi, bd in enumerate(beads):
            if bd.element != 'G':
                continue
            for u_, v_ in arr.bead_edges[bi]:
                for fid in set(arr.edge_faces(u_, v_)):
                    gen_len[fid] = gen_len.get(fid, 0.0) + arr.nodes[u_].dist(arr.nodes[v_])
        tiny = 0
        for f in arr.faces[1:]:
            if f.id not in gen_len or not f.outer:
                continue
            per = sum(f.outer[i].dist(f.outer[(i + 1) % len(f.outer)]) for i in range(len(f.outer)))
            if gen_len[f.id] >= RQ.TINY_LOOP_GEN * per and f.area < RQ.TINY_CELL * thick * S:
                tiny += 1
        return (hot, tiny, round(max(mults, default=0.0), 3))

    seqs = {}
    for ri, r in enumerate(runs):
        if r.cycle:
            if ri in jammed:
                opts = [layout(r, 'two', s0_) for s0_ in (flip, 1 - flip)]
                if corners(r) and id(r) not in tracked:
                    scores = [run_crowding(r, q) for q in opts]
                    seqs[ri] = ('two', opts[min(range(2), key=lambda k: scores[k])])
                else:
                    seqs[ri] = ('two', opts[0])
            else:
                seqs[ri] = ('single', layout(r, 'single', flip, 0))
        elif mult[ri] == 2:
            # Which phase lands which corner point follows from the start
            # side: pick the one keeping other beads clear of the inner
            # corner (the phase arriving two stations back passes wide).
            opts = [layout(r, 'two', s0_) for s0_ in (0, 1)]
            if corners(r) and id(r) not in tracked:
                scores = [run_crowding(r, q) for q in opts]
                if _DEBUG:
                    print('orient', ri, round(r.length, 2), [c['s'] for c in corners(r)], scores)
                seqs[ri] = ('two', opts[min(range(2), key=lambda k: scores[k])])
            else:
                seqs[ri] = ('two', opts[0])
        else:
            s0, par = choice[ri] if ri in choice else (flip, None)
            seqs[ri] = ('single', layout(r, 'single', s0, par))

    def neighbour(ri, end, side):
        """The landing a junction slot's stitch goes to (for the corner
        meeting point check)."""
        r = runs[ri]
        sv = 0.0 if end == 0 else r.length
        for sq in seqs[ri][1]:
            for k, (x, sd) in enumerate(sq):
                if abs(x - sv) < 1e-9 and sd == side:
                    nb = sq[k + 1] if k + 1 < len(sq) else sq[k - 1]
                    if abs(nb[0] - sv) < 1e-9 and k - 1 >= 0:
                        nb = sq[k - 1]
                    return nb
        return None

    # Pass ends sharing a junction corner land on ONE point: the corner
    # vertex, or on a rounded corner the apex of its arc (nearest the
    # junction centre) — two generated beads meet there, nothing more.
    merged = set()
    for nd, m in pairs.items():
        ends_ = junction_ends[nd]
        cx = sum(slot_point(ri, e, sd).x for ri, e in ends_ for sd in (0, 1)) / (2 * len(ends_))
        cy = sum(slot_point(ri, e, sd).y for ri, e in ends_ for sd in (0, 1)) / (2 * len(ends_))
        centre = Vec2(cx, cy)
        for x, y in m:
            ca = corner_arc(x, y) if x[:2] != y[:2] else None
            if ca is None:
                continue
            i, j = slot_idx(*x), slot_idx(*y)
            if i == j:
                apex = sk.pts[i]
            else:
                ri = sk.ring_of[i]
                L = sk.R.length(ri)
                a0, a1 = sk.arc_of[i], sk.arc_of[j]
                d = (a1 - a0) % L
                if d > L / 2:
                    d -= L
                # the arc point nearest the junction centre — CONTINUOUS in
                # the geometry (coarse samples, then golden section), so the
                # meeting point moves smoothly from layer to layer
                ks = min(range(17), key=lambda k: sk.R.point(ri, a0 + d * k / 16).dist(centre))
                lo_, hi_ = max(0.0, (ks - 1) / 16), min(1.0, (ks + 1) / 16)
                f_ = lambda t: sk.R.point(ri, a0 + d * t).dist(centre)
                for _ in range(30):
                    m1, m2 = lo_ + (hi_ - lo_) * 0.382, lo_ + (hi_ - lo_) * 0.618
                    if f_(m1) <= f_(m2):
                        hi_ = m2
                    else:
                        lo_ = m1
                apex = sk.R.point(ri, a0 + d * (lo_ + hi_) / 2)
            # The stitches leaving the shared point run straight into each
            # arm; on a rounded corner they would cut the fillet, so the
            # point moves inside the wall (towards the junction centre)
            # just enough — the lattice meets there, off the face.
            nexts = []
            for ri_, end, side in (x, y):
                nb = neighbour(ri_, end, side)
                if nb is not None:
                    nexts.append(geo.landing(runs[ri_], nb[1], nb[0]))
            dirv = centre - apex
            dl = dirv.length() or 1.0
            point = apex
            at = lambda k: apex + dirv * (k * thick / dl)
            ok_k = lambda k: (k == 0 or region.inside(at(k))) and all(_strut_ok(at(k), nx_, region) for nx_ in nexts)
            if not ok_k(0.0):
                # the SMALLEST move inside that frees every stitch (bisection:
                # continuous in the geometry — no jumps between layers)
                good = next((k for k in (0.2, 0.3, 0.4, 0.5, 0.65) if ok_k(k)), None)
                if good is not None:
                    lo_k = 0.0
                    for _ in range(20):
                        mk = (lo_k + good) / 2
                        if ok_k(mk):
                            good = mk
                        else:
                            lo_k = mk
                    # a margin past the threshold (still continuous): a strut
                    # exactly grazing the fillet would be split by clipping
                    good = next((g for g in (good + 0.1, good + 0.05) if ok_k(g)), good)
                    point = at(good)
            for ri_, end, side in (x, y):
                r = runs[ri_]
                sv = 0.0 if end == 0 else r.length
                geo.cache[(id(r), side, round(sv, 9))] = point
            merged.add((x, y))

    if contact > 0:
        # One TRANSFER per face ring that no exact pass end touches: the
        # lattice and that ring stay one closed route (+2 at the node).
        def ring_of(p):
            best = None
            for k, ring in enumerate(rings):
                d = min(_seg_d(p, ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
                if best is None or d < best[0]:
                    best = (d, k)
            return best
        touched = set()
        cands: dict = {}
        for ri, r in enumerate(runs):
            cs_ = {round(c['s'] % r.length if r.cycle else c['s'], 6) for c in corners(r)}
            for one in seqs[ri][1]:
                for j, (x, sd) in enumerate(one):
                    if sd not in (0, 1):
                        continue
                    p = geo.landing(r, sd, x)
                    d, k = ring_of(p)
                    if d > 1e-6:
                        continue                # moved inside (merged hand-off)
                    if not is_contact(r, sd, x):
                        touched.add(k)
                    else:
                        poor = j in (0, len(one) - 1) or \
                            round(x % r.length if r.cycle else x, 6) in cs_
                        cands.setdefault(k, []).append((ri, sd, x, poor))
        # CONNECTIVITY: passes / rings joined by exact contacts (shared pass
        # end points, exact landings on a ring). With rounded junctions the
        # hand-off points sit inside the wall, so per-ring transfers alone
        # can leave the lattice as two closed systems (two print runs).
        par = {}
        def find(a):
            par.setdefault(a, a)
            while par[a] != a:
                par[a] = par[par[a]]
                a = par[a]
            return a
        def union(a, b):
            par[find(a)] = find(b)
        at_pt = {}
        pass_of = []                             # (pass id, ri, landing index list)
        for ri, r in enumerate(runs):
            for pi, one in enumerate(seqs[ri][1]):
                pid = ('P', ri, pi)
                find(pid)
                for j, (x, sd) in enumerate(one):
                    q = lp(r, sd, x)
                    key_ = (round(q.x, 6), round(q.y, 6))
                    if key_ in at_pt:
                        union(pid, at_pt[key_])
                    else:
                        at_pt[key_] = pid
                    if sd in (0, 1) and not is_contact(r, sd, x):
                        d, k = ring_of(geo.landing(r, sd, x))
                        if d <= 1e-6:
                            union(pid, ('R', k))
                pass_of.append((pid, ri, one))
        def transfer(k, cs):
            # mid-sequence: away from ends / corners (the nearest candidate
            # to the middle that is neither a pass end nor a corner)
            m = len(cs) // 2
            order = sorted(range(len(cs)), key=lambda i: (abs(i - m), i))
            ri, sd, x, _ = cs[next((i for i in order if not cs[i][3]), m)]
            r = runs[ri]
            transfers.add((id(r), sd, round(x % r.length if r.cycle else x, 9)))
            union(('R', k), next(pid for pid, ri_, _ in pass_of if ri_ == ri and
                                 any(abs(xx - x) < 1e-9 and ss == sd for xx, ss in _)))
        for k, cs in sorted(cands.items()):
            if k in touched or not cs:
                continue
            transfer(k, cs)
        # one more transfer for every lattice system still apart: on a
        # ring it lands on with a contact landing, joining that ring's system
        for _ in range(len(rings) + len(pass_of)):
            comps = {find(pid) for pid, _, _ in pass_of}
            if len(comps) <= 1:
                break
            main = find(pass_of[0][0])
            done = False
            for k, cs in sorted(cands.items()):
                own = [c for c in cs if find(next(pid for pid, ri_, one in pass_of if ri_ == c[0] and
                                                   any(abs(xx - c[2]) < 1e-9 and ss == c[1] for xx, ss in one)))
                       != find(('R', k))]
                if own and (find(('R', k)) == main or any(
                        find(next(pid for pid, ri_, one in pass_of if ri_ == c[0] and
                                  any(abs(xx - c[2]) < 1e-9 and ss == c[1] for xx, ss in one))) == main
                        for c in own)):
                    transfer(k, own)
                    done = True
                    break
            if not done:
                break

    for v_ in jamb_report.values():
        v_['bend_failed'] = 0             # count the final emission only
    polys = []
    run_rep = []
    pitches = []
    unsupported = []

    kinds_out = {}
    for ri, r in enumerate(runs):
        mode, sq = seqs[ri]
        tr = tracked.get(id(r))
        ref_k = tr.get('kinds') if tr and tr.get('fwd') and not tr.get('changed') else None
        kinds_out[ri] = []
        for si, one in enumerate(sq):
            prefs = ref_k[si] if ref_k and si < len(ref_k) and len(ref_k[si]) == len(one) - 1 else None
            rec = []
            polys.append(emit(r, one, prefs, rec))
            kinds_out[ri].append(rec)
        if r.cycle and mode != 'two':
            polys[-1][-1] = polys[-1][0]
        # supports along the wall: every landing station (either face)
        stn = sorted({round(x % r.length if r.cycle else x, 6) for one in sq for x, sd in one if sd != JAMB})
        gaps = [b_ - a_ for a_, b_ in zip(stn, stn[1:])]
        if r.cycle and stn:
            gaps.append(stn[0] + r.length - stn[-1])
        unsupported.append(max(gaps, default=0.0))
        ns_, _ = counts(r, 'two' if mode == 'two' else 'single',
                        None if mode == 'two' else (choice[ri][1] if ri in choice else
                                                     (0 if r.cycle else None)))
        segs = segments(r)
        for (a_, b_, U), n in zip(segs, ns_):
            pitches.append(U / n)
        if r.cycle:
            motif = 'double_loop' if mode == 'two' else 'loop'
        elif mode == 'two':
            motif = ('lone_loop' if all(nd[0] == 'E' for nd in (r.a, r.b)) else
                     'out_and_back' if any(nd[0] == 'E' for nd in (r.a, r.b)) else 'double')
        else:
            motif = ('single' if all(nd[0] == 'J' for nd in (r.a, r.b))
                     else 'lone' if all(nd[0] == 'E' for nd in (r.a, r.b)) else 'open_end')
        L = sum(U for _, _, U in segs)
        N = sum(ns_)
        run_rep.append({'length': round(L, 2), 'stitches': N, 'pitch': round(L / N, 2),
                        'passes': 2 if mode == 'two' else 1, 'motif': motif,
                        'corners': len(corners(r)),
                        'max_unsupported': round(unsupported[-1], 2),
                        'ends': [None if r.cycle else ('junction' if nd[0] == 'J' else 'end')
                                 for nd in (r.a, r.b)]})
    connectors = []
    for nd, m in pairs.items():
        for x, y in m:
            if (x, y) in merged:
                continue
            p, q = slot_point(*x), slot_point(*y)
            if p is q or p.dist(q) < 1e-9:
                continue
            polys.append([p, q])
            connectors.append(round(p.dist(q), 2))
    keep = {(lambda q: (q.x, q.y))(geo.cache[(id(r), JAMB, round(x % r.length if r.cycle else x, 9))])
            for ri, r in enumerate(runs) for one in seqs[ri][1] for x, sd in one if sd == JAMB}
    polys = [_simplify(p, keep) for p in polys if len(p) >= 2]
    # contact check: lattice points nearer the faces than the contact
    # separation, away from the exact route connections (reported, not hidden)
    c_min, c_bad = None, 0
    if contact > 0:
        exact = [geo.landing(r, sd, x) for ri, r in enumerate(runs) for one in seqs[ri][1]
                 for x, sd in one if sd in (0, 1) and not is_contact(r, sd, x)]
        for poly in polys:
            for q in poly:
                if any(q.dist(e) < S for e in exact):
                    continue            # the stitches of a route connection
                d = _face_d(q)
                c_min = d if c_min is None else min(c_min, d)
                if d < 0.98 * contact:
                    c_bad += 1
    track_out = []
    uid = {ri: _xy(geo.centre(r, 0.5 * r.length)) for ri, r in enumerate(runs)}
    partners = {ri: {} for ri in range(len(runs))}
    for nd, m in pairs.items():                    # the junction pairing (route identity at a hub)
        for x, y in m:
            partners[x[0]][f'{x[1]},{x[2]}'] = [uid[y[0]], y[1], y[2]]
            partners[y[0]][f'{y[1]},{y[2]}'] = [uid[x[0]], x[1], x[2]]
    for ri, r in enumerate(runs):
        L = r.length
        segs_ = segments(r)
        mode_ = seqs[ri][0]
        ns_, _ = counts(r, 'two' if mode_ == 'two' else 'single',
                        None if mode_ == 'two' else (choice[ri][1] if ri in choice else (0 if r.cycle else None)))
        track_out.append({
            'cycle': r.cycle, 'mult': mult[ri],
            'pts': [_xy(geo.centre(r, f * L)) for f in (0.1, 0.3, 0.5, 0.7, 0.9)],
            'seam': _xy(geo.centre(r, segs_[0][0] % L if r.cycle else 0.0)),
            'left0': geo._side_sign(r, 0) > 0,
            'segmid': [_xy(geo.centre(r, ((a_ + b_) / 2) % L if r.cycle else (a_ + b_) / 2)) for a_, b_, _ in segs_],
            'counts': list(ns_),
            'kinds': kinds_out.get(ri),
            'choice': list(choice[ri]) if ri in choice else None,
            'uid': uid[ri], 'partners': partners[ri]})
    report = {
        'no_cavity': no_cavity,
        'target': S, 'thickness': round(thick, 2), 'max_unsupported_limit': round(DMAX, 2),
        'max_unsupported': round(max(unsupported, default=0.0), 2),
        'pitch_min': round(min(pitches), 2) if pitches else None,
        'pitch_max': round(max(pitches), 2) if pitches else None,
        'runs': run_rep, 'junctions': len(junction_ends),
        'dead_ends': sum(1 for r in runs if not r.cycle for nd in (r.a, r.b) if nd[0] == 'E'),
        'connectors': connectors,
        'contact': round(contact, 4), 'transfers': len(transfers),
        'contact_clamped': contact_clamped[0],
        'contact_min': None if c_min is None else round(c_min, 3),
        'contact_violations': c_bad,
        'track': {'runs': track_out, **track_note},
        'jambs': {'resolved': len(set().union(*(v.get('lines', set()) for v in jamb_report.values()))),
                  'lines': sorted(set().union(*(v.get('lines', set()) for v in jamb_report.values()))),
                  'unresolved': [u for v in jamb_report.values() for u in v['unresolved']],
                  'bend_failed': sum(v.get('bend_failed', 0) for v in jamb_report.values()),
                  'matched': sorted(jamb_matched)}
        if stations else None,
    }
    return LatticePlan(polys, report)


def _straight(geo, r, s_a, s_b):
    """Both faces straight and parallel-moving between the two stations."""
    for side in (0, 1):
        p0 = geo.side_point(r, side, s_a)
        p1 = geo.side_point(r, side, s_b)
        pm = geo.side_point(r, side, (s_a + s_b) / 2)
        d = p1 - p0
        L = d.length()
        if L < 1e-9:
            continue
        if abs((pm.x - p0.x) * d.y - (pm.y - p0.y) * d.x) / L > 1e-3:
            return False
    return True


def _no_foldback(poly):
    """Drop interior points where the polyline reverses on itself (a
    curve sample overshooting a landing in a tight inner bend)."""
    out = list(poly)
    changed = True
    while changed and len(out) > 2:
        changed = False
        for k in range(1, len(out) - 1):
            a, b, c = out[k - 1], out[k], out[k + 1]
            v1, v2 = b - a, c - b
            if v1.length() > 1e-9 and v2.length() > 1e-9 and v1.x * v2.x + v1.y * v2.y < 0:
                del out[k]
                changed = True
                break
    return out


def _xy(p):
    return (round(p.x, 9), round(p.y, 9))


def _match_track(runs, geo, track, track_map, thick):
    """({id(run): tracked reference data}, same topology?). Each local run a
    reference run maps onto (distinct, within half a thickness, wall-relative
    through track_map) is matched; directions and sides are aligned (a
    reference side keeps its physical side). PARTIAL when the skeleton
    changed (a run appeared / vanished): the unmatched runs are planned
    afresh by the caller, the matched ones keep the reference's choices."""
    ref = (track or {}).get('runs') or []          # (may span several regions)
    if not ref:
        return {}, False
    polys = {}

    def poly(r):
        if id(r) not in polys:
            geo.centre(r, 0.0)
            mids = geo.mids[id(r)]
            ss = list(r.s) + ([r.length] if r.cycle else [])
            polys[id(r)] = (mids, ss)
        return polys[id(r)]

    def project(r, p):
        mids, ss = poly(r)
        best = (math.inf, 0.0)
        for k in range(len(mids) - 1):
            a, b = mids[k], mids[k + 1]
            d = b - a
            L2 = d.x * d.x + d.y * d.y
            t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p.x - a.x) * d.x + (p.y - a.y) * d.y) / L2))
            q = a.lerp(b, t)
            dd = q.dist(p)
            if dd < best[0]:
                best = (dd, ss[k] + t * (ss[min(k + 1, len(ss) - 1)] - ss[k]))
        return best
    cand = []
    for i, tr in enumerate(ref):
        mp = [track_map(Vec2(*p)) for p in tr['pts']]
        for r in runs:
            if bool(r.cycle) != bool(tr['cycle']):
                continue
            pr = [project(r, q) for q in mp]
            cost = sum(d for d, _ in pr) / len(pr)
            cand.append((cost, i, id(r), r, pr))
    cand.sort(key=lambda c: c[0])
    used_i, used_r, out = set(), set(), {}
    for cost, i, rid, r, pr in cand:
        if i in used_i or rid in used_r or cost > 0.5 * thick:
            continue
        used_i.add(i); used_r.add(rid)
        tr = dict(ref[i])
        ss = [s_ for _, s_ in pr]
        if r.cycle:
            fwd = ((ss[1] - ss[0]) % r.length) < r.length / 2
        else:
            fwd = ss[-1] >= ss[0]
        same_side = (bool(tr['left0']) == (geo._side_sign(r, 0) > 0)) == fwd
        side = (lambda x: x) if same_side else (lambda x: 1 - x)
        if tr.get('choice'):
            s0, par = tr['choice']
            tr['choice'] = (side(s0) if fwd else side(s0 ^ par), par)
        tr['segmid'] = [track_map(Vec2(*p)) for p in tr['segmid']]
        tr['seam_s'] = project(r, track_map(Vec2(*tr['seam'])))[1] if r.cycle else None
        tr['fwd'] = fwd
        tr['same_side'] = same_side
        out[rid] = tr
    # SAME TOPOLOGY: every local run matched, and no reference run left over
    # that maps INTO this region (e.g. a junction lens that has shrunk away).
    # Otherwise the match is PARTIAL: the matched runs keep their choices,
    # the caller re-plans the rest and repairs parity.
    best_i = {}
    for cost, i, rid, r, pr in cand:
        best_i[i] = min(best_i.get(i, math.inf), cost)
    same = len(out) == len(runs) and not any(i not in used_i and c <= thick for i, c in best_i.items())
    return out, same


def _line_key(P, Q):
    a, b = (round(P.x, 6), round(P.y, 6)), (round(Q.x, 6), round(Q.y, 6))
    return min(a, b) + max(a, b)


def _simplify(poly, keep=()):
    out = [poly[0]]
    for k in range(1, len(poly) - 1):
        a, b, c = out[-1], poly[k], poly[k + 1]
        if (b.x, b.y) in keep:          # a shared vertex (jamb crossing)
            out.append(b)
            continue
        d = c - a
        L = d.length()
        if L > 1e-12 and abs((b.x - a.x) * d.y - (b.y - a.y) * d.x) / L < 1e-6 and \
                (b.x - a.x) * d.x + (b.y - a.y) * d.y > 0 and b.dist(a) < L:
            continue
        out.append(b)
    out.append(poly[-1])
    return out


def _same_face(sk, p, q):
    """p and q lie close together along one ring (a corner cut)."""
    def pos(x):
        for ri, r in enumerate(sk.R.rings):
            c = sk.R.cum[ri]
            for i in range(len(r)):
                a, b = r[i], r[(i + 1) % len(r)]
                L = a.dist(b)
                if L < 1e-12:
                    continue
                t = max(0.0, min(1.0, ((x.x - a.x) * (b.x - a.x) + (x.y - a.y) * (b.y - a.y)) / (L * L)))
                if x.dist(a.lerp(b, t)) < 1e-6:
                    return ri, c[i] + t * L
        return None
    a, b = pos(p), pos(q)
    if not a or not b or a[0] != b[0]:
        return False
    L = sk.R.length(a[0])
    d = abs(a[1] - b[1])
    return min(d, L - d) < 2.0 * p.dist(q)


def _dense(a, b, n=8):
    return [a.lerp(b, k / n) for k in range(n + 1)]


def _seg_d(p, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return p.dist(a)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    return math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy)


def _extreme_face_point(sk, c, direction, radius):
    """Boundary point within `radius` of c farthest along `direction`
    (a corner: the convex vertex / outer arc apex; with the reversed
    direction the reflex vertex / inner arc apex). Ring vertices and the
    ends of the clipped segments are candidates; ties → nearer to c."""
    best = None
    for r in sk.R.rings:
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            if _seg_d(c, a, b) > radius:
                continue
            cands = []
            for q in (a, b):
                if q.dist(c) <= radius:
                    cands.append(q)
            # where the segment leaves the disc (a leg running away)
            for t in _disc_hits(c, a, b, radius):
                cands.append(a.lerp(b, t))
            for q in cands:
                key = ((q.x - c.x) * direction.x + (q.y - c.y) * direction.y, -q.dist(c))
                if best is None or key > best[0]:
                    best = (key, q)
    return best[1] if best else None


def _disc_hits(c, a, b, r):
    dx, dy = b.x - a.x, b.y - a.y
    fx, fy = a.x - c.x, a.y - c.y
    A = dx * dx + dy * dy
    if A < 1e-18:
        return []
    B = 2 * (fx * dx + fy * dy)
    C = fx * fx + fy * fy - r * r
    disc = B * B - 4 * A * C
    if disc < 0:
        return []
    sq = math.sqrt(disc)
    return [t for t in ((-B - sq) / (2 * A), (-B + sq) / (2 * A)) if 0 < t < 1]


def _nearest_on_side(sk, c, bis, radius):
    """Inner corner: the boundary point nearest to c on the inside of the
    turn (opposite the bisector) — the reflex vertex / inner arc apex."""
    best = None
    for r in sk.R.rings:
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            if _seg_d(c, a, b) > radius:
                continue
            dx, dy = b.x - a.x, b.y - a.y
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((c.x - a.x) * dx + (c.y - a.y) * dy) / L2))
            q = a if t <= 0 else (b if t >= 1 else a.lerp(b, t))
            if (q.x - c.x) * bis.x + (q.y - c.y) * bis.y >= 0:
                continue
            dd = q.dist(c)
            if best is None or dd < best[0] - 1e-12:
                best = (dd, q)
    return best[1] if best else None


def _ray_hit(sk, p, d, maxlen):
    """First boundary point along the ray p + t·d (0 < t ≤ maxlen)."""
    far = Vec2(p.x + d.x * maxlen, p.y + d.y * maxlen)
    best = None
    for r in sk.R.rings:
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            ex, ey = b.x - a.x, b.y - a.y
            den = (far.x - p.x) * ey - (far.y - p.y) * ex
            if abs(den) < 1e-12:
                continue
            t = ((a.x - p.x) * ey - (a.y - p.y) * ex) / den
            u = ((a.x - p.x) * (far.y - p.y) - (a.y - p.y) * (far.x - p.x)) / den
            if 1e-6 < t <= 1.0 and -1e-9 <= u <= 1 + 1e-9:
                if best is None or t < best[0]:
                    best = (t, a.lerp(b, max(0.0, min(1.0, u))))
    return best[1] if best else None


def _inner_bisector(sk, I, arc):
    """Unit vector from the inner corner I away from the wall's inside
    corner: minus the sum of the unit directions from I to the boundary
    points `arc` before and after I along its ring."""
    for ri, r in enumerate(sk.R.rings):
        c = sk.R.cum[ri]
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            if _seg_d(I, a, b) < 1e-6:
                L = a.dist(b)
                s0 = c[i] + (I.dist(a) if L > 1e-12 else 0.0)
                p1 = sk.R.point(ri, s0 - arc)
                p2 = sk.R.point(ri, s0 + arc)
                u1, u2 = p1 - I, p2 - I
                if u1.length() < 1e-9 or u2.length() < 1e-9:
                    return None
                v = u1 * (1.0 / u1.length()) + u2 * (1.0 / u2.length())
                if v.length() < 1e-9:
                    return None
                return v * (-1.0 / v.length())
    return None
