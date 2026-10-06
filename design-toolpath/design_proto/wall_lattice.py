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
         max_unsupported=None, contact=0.0, closed=False):
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

    # -- 1. how many passes per run (route inspection on the skeleton) -----
    K = nx.MultiGraph()
    for ri, r in enumerate(runs):
        if not r.cycle:
            K.add_edge(r.a, r.b, key=ri, weight=r.length)
    mult = {ri: 1 for ri in range(len(runs))}
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
                if not last and round(b, 9) in cmap:            # corner brace
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
        for (a, b, U), n in zip(segs_o, ns_o):
            st = seg_stations(r, a, b, n) if not rev else \
                list(reversed(seg_stations(r, b, a, n)))
            for k in range(1, n + 1):
                sa = 1 - sa
                A.append((st[k], sa))
                B.append((st[k], 1 - sa))
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
            if nd[0] == 'J':
                junction_ends.setdefault(nd, []).append((ri, end))

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
    if len(single) <= 6:
        best = None
        for combo in itertools.product(options, repeat=len(single)):
            choice = dict(zip(single, combo))
            tot, pairs = solve(choice)
            if best is None or tot < best[0] - 1e-9:
                best = (tot, choice, pairs)
    else:                                         # coordinate descent
        choice = {ri: (flip, (sum(counts(runs[ri], 'single')[0]) + len(corners(runs[ri]))) % 2)
                  for ri in single}
        tot0, pairs0 = solve(choice)
        best = (tot0, dict(choice), pairs0)
        for _ in range(4):
            improved = False
            for ri in single:
                for opt in options:
                    trial = dict(best[1]); trial[ri] = opt
                    tot, pairs = solve(trial)
                    if tot < best[0] - 1e-9:
                        best = (tot, trial, pairs); improved = True
            if not improved:
                break
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
        if abs(s_a - s_b) < 1e-9 or CAP in (side_a, side_b) or MID in (side_a, side_b):
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
            if all(_strut_ok(u, v, region) or u.dist(v) < 1e-9 for u, v in zip(out, out[1:])) \
                    and _clear_poly(out, ex_a, ex_b):
                return _no_foldback(out)
            # beside a corner the face-to-face construction can leave the
            # wall (or, with contact rails, come closer to a face than the
            # contact): a Hermite curve leaving / meeting each face along it
            # (still tangent, still a wave) before falling back to straight
            h = hermite(r, s_a, side_a, pa, s_b, side_b, pb)
            if h is not None and _clear_poly(h, ex_a, ex_b):
                return h
            if straight_ok and (contact <= 0 or _clear_poly(_dense(pa, pb), ex_a, ex_b)):
                return [pa, pb]
            if h is not None:
                return h
            return [pa, pb] if straight_ok else out
        out = [pa]
        for k in range(1, 8):
            f = k / 8
            s_ = s_a + f * (s_b - s_a)
            out.append(rail(r, side_a, s_).lerp(rail(r, side_b, s_), f))
        out.append(pb)
        if straight_ok and not all(_strut_ok(u, v, region) or u.dist(v) < 1e-9
                                   for u, v in zip(out, out[1:])):
            return [pa, pb]             # the curve would leave the wall
        # tidy the ends: no curve point hugging a landing (a curve running
        # into a tight inner corner would overshoot it and double back)
        tidy = 0.35 * thick
        while len(out) > 2 and out[-2].dist(out[-1]) < tidy and _strut_ok(out[-3], out[-1], region):
            del out[-2]
        while len(out) > 2 and out[1].dist(out[0]) < tidy and _strut_ok(out[0], out[2], region):
            del out[1]
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

    def emit(r, seq):
        poly = [lp(r, seq[0][1], seq[0][0])]
        for (xa, sa), (xb, sb) in zip(seq, seq[1:]):
            # memoised by the actual landing points (junction hand-off
            # points may be moved after a first emission)
            pa, pb = lp(r, sa, xa), lp(r, sb, xb)
            key = (id(r), round(xa, 9), sa, round(xb, 9), sb, pa.x, pa.y, pb.x, pb.y)
            if key not in stitch_memo:
                stitch_memo[key] = stitch(r, xa, sa, xb, sb)
            poly.extend(stitch_memo[key][1:])
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
            seqs[ri] = ('single', layout(r, 'single', flip, 0))
        elif mult[ri] == 2:
            # Which phase lands which corner point follows from the start
            # side: pick the one keeping other beads clear of the inner
            # corner (the phase arriving two stations back passes wide).
            opts = [layout(r, 'two', s0_) for s0_ in (0, 1)]
            if corners(r):
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
                apex = min((sk.R.point(ri, a0 + d * k / 16) for k in range(17)),
                           key=lambda q: q.dist(centre))
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
            for k in (0.0, 0.2, 0.3, 0.4, 0.5, 0.65):
                q = apex + dirv * (k * thick / dl)
                if k > 0 and not region.inside(q):
                    break
                if all(_strut_ok(q, nx_, region) for nx_ in nexts):
                    point = q
                    break
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
            for one in seqs[ri][1]:
                for x, sd in one:
                    if sd not in (0, 1):
                        continue
                    p = geo.landing(r, sd, x)
                    d, k = ring_of(p)
                    if d > 1e-6:
                        continue                # moved inside (merged hand-off)
                    if not is_contact(r, sd, x):
                        touched.add(k)
                    else:
                        cands.setdefault(k, []).append((ri, sd, x))
        for k, cs in sorted(cands.items()):
            if k in touched or not cs:
                continue
            ri, sd, x = cs[len(cs) // 2]       # mid-sequence: away from ends / corners
            r = runs[ri]
            transfers.add((id(r), sd, round(x % r.length if r.cycle else x, 9)))

    polys = []
    run_rep = []
    pitches = []
    unsupported = []

    for ri, r in enumerate(runs):
        mode, sq = seqs[ri]
        for one in sq:
            polys.append(emit(r, one))
        if r.cycle:
            polys[-1][-1] = polys[-1][0]
        # supports along the wall: every landing station (either face)
        stn = sorted({round(x % r.length if r.cycle else x, 6) for one in sq for x, _ in one})
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
            motif = 'loop'
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
    polys = [_simplify(p) for p in polys if len(p) >= 2]
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
    report = {
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


def _simplify(poly):
    out = [poly[0]]
    for k in range(1, len(poly) - 1):
        a, b, c = out[-1], poly[k], poly[k + 1]
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
