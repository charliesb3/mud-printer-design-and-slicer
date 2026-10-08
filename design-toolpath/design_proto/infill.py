"""
Region infill FIELD — the wide-region fallback, and shared region helpers.

ROLE NOW: wall regions are stitched by wall_lattice.py (route-aware
motifs). This field generator runs only for regions too wide to be a wall
(wall_lattice.plan returns None), with route_plan.repair for continuity.
Its `_Region` and `_strut_ok` helpers are shared by wall_lattice.py,
solid.py, route_plan.py and route_quality.py. PATTERNS / PARAMETERS here
are also the wall-infill pattern list served to the UI.

Algorithm (fallback field):

A wall region (network.MaterialComponent) is a polygon with any number of
holes: its rings carry the material on their LEFT (outer outline CCW,
cavities / rooms / islands / doorways CW). Infill fills that material as
ONE coherent field:

  1. Every ring is sampled at one global pitch (`spacing`). Sharp ring
     corners are always samples. Rings after the first choose the sampling
     phase that best STAGGERS them against what is already sampled, so
     facing walls alternate (→ zigzag, not ladder rungs).
  2. Where the material is wider than the pitch, interior points on a
     global hex grid (same pitch) are added, so wide regions get a web
     instead of long struts.
  3. The points are Delaunay-triangulated; a triangle edge is a strut iff
     it is not a piece of the boundary and lies entirely inside the
     material (never across a hole, a doorway or the outside).
  4. Struts are chained into polylines. 'wave' smooths each strut into a
     curve leaving and meeting the walls tangentially (same topology).

Because struts end ON the boundary samples, they land on the wall faces
as real junctions; in a band they alternate face to face, so boundary
nodes are even-degree and routing stays continuous. Spacing / phase are
global (not per wall), so branches, junctions and holes share one field.
"""
from __future__ import annotations
import math

from model import Vec2, _segments_intersect

PATTERNS = {
    'zigzag': 'Triangulated web: struts alternate face to face',
    'wave': 'Same web, each strut a smooth wave tangent to the walls',
    'truss': 'Adaptive Truss: braces at a structural angle, pitch from the local cavity width, '
             'a bond along each skin (wall lattice only; wide areas use the zigzag field)',
}

PARAMETERS = [
    # A TARGET: the wall lattice redistributes whole stitches evenly along
    # each wall run (actual pitch = run length / stitch count).
    {'name': 'spacing', 'label': 'Target Spacing', 'default': 20.0, 'min': 4.0,
     'max': 120.0, 'step': 1.0},
]

# ADAPTIVE TRUSS (wall_lattice.truss_params). Prototype defaults from the
# wall-web experiment — NOT calibrated machine values. Target Spacing is not
# a parameter: the pitch follows from the brace angle and the cavity width.
TRUSS_PARAMETERS = [
    {'name': 'brace_angle', 'label': 'Brace Angle', 'default': 45.0, 'min': 15.0, 'max': 75.0,
     'step': 1.0, 'unit': '°'},
    {'name': 'bond', 'label': 'Bond Length', 'default': 3.0, 'min': 0.0, 'max': 24.0, 'step': 0.25},
    {'name': 'max_span', 'label': 'Max Unsupported Span', 'default': 40.0, 'min': 4.0, 'max': 240.0,
     'step': 1.0},
    {'name': 'turn_radius', 'label': 'Min Turn Radius', 'default': 1.5, 'min': 0.0, 'max': 12.0,
     'step': 0.25},
]


def parameters_for(pattern):
    return TRUSS_PARAMETERS if pattern == 'truss' else PARAMETERS


CORNER_TURN = math.radians(35.0)    # sharper ring turns are always samples


def _seg_dist(p, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    if L2 < 1e-24:
        return p.dist(a)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    return math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy)


def _ring_segs(ring):
    n = len(ring)
    return [(ring[i], ring[(i + 1) % n]) for i in range(n)]


def _canonical(ring):
    """Rotate so the ring starts at its lowest-left vertex (stable start)."""
    k = min(range(len(ring)), key=lambda i: (round(ring[i].x, 6), round(ring[i].y, 6)))
    return ring[k:] + ring[:k]


def _corners(ring, window):
    """
    Ring vertices where the wall turns sharply: more than CORNER_TURN
    within an arc-length `window` (half the pitch). A sharp vertex and a
    small junction fillet both count; a large Corner R arc or a circle
    does not. One corner per sharp turn (strongest vertex wins).
    """
    n = len(ring)
    cum = [0.0]
    for i in range(n):
        cum.append(cum[-1] + ring[i].dist(ring[(i + 1) % n]))
    L = cum[-1]
    if L < 1e-9:
        return []
    h = window / 2.0

    def point_at(s):
        s %= L
        j = max(0, min(n - 1, _bisect(cum, s) - 1))
        seg = cum[j + 1] - cum[j]
        f = (s - cum[j]) / seg if seg > 1e-12 else 0.0
        return ring[j].lerp(ring[(j + 1) % n], f)

    turn = []
    for i in range(n):
        p = ring[i]
        a, b = point_at(cum[i] - h), point_at(cum[i] + h)
        d1, d2 = p - a, b - p
        if d1.length() < 1e-9 or d2.length() < 1e-9:
            turn.append(0.0)
            continue
        c = (d1.x * d2.x + d1.y * d2.y) / (d1.length() * d2.length())
        turn.append(math.acos(max(-1.0, min(1.0, c))))
    out = []
    for i in range(n):
        if turn[i] <= CORNER_TURN:
            continue
        # strongest vertex within the window (ties: lowest index)
        best = True
        for j in range(n):
            if j == i:
                continue
            d = abs(cum[j] - cum[i])
            d = min(d, L - d)
            if d <= h and (turn[j] > turn[i] + 1e-12 or
                           (abs(turn[j] - turn[i]) <= 1e-12 and j < i)):
                best = False
                break
        if best:
            out.append(i)
    return out


def _bisect(a, x):
    import bisect
    return bisect.bisect_right(a, x)


class _PointGrid:
    def __init__(self, cell):
        self.cell = cell
        self.g = {}

    def add(self, p):
        self.g.setdefault((math.floor(p.x / self.cell), math.floor(p.y / self.cell)), []).append(p)

    def nearest(self, p, cap):
        cx, cy = math.floor(p.x / self.cell), math.floor(p.y / self.cell)
        best = cap
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in self.g.get((cx + dx, cy + dy), ()):
                    best = min(best, p.dist(q))
        return best


def _runs(ring, corners):
    """The ring split at its sharp corners into runs [(points, closed)].
    A ring without corners (circle, fully rounded outline) is one closed
    run."""
    n = len(ring)
    if not corners:
        return [(ring, True)]
    return [([ring[k % n] for k in range(a, b + 1)], False)
            for a, b in zip(corners, corners[1:] + [corners[0] + n])]


def _run_samples(pts, closed, pitch, offset):
    """Samples strictly inside an open run (its corner ends are samples
    already), or all round a closed run, at a step close to pitch."""
    seq = pts + [pts[0]] if closed else pts
    cum = [0.0]
    for a, b in zip(seq, seq[1:]):
        cum.append(cum[-1] + a.dist(b))
    L = cum[-1]
    if L < 1e-9:
        return []
    if closed:
        n = max(3, int(round(L / pitch)))
        step = L / n
        ss = sorted((offset * step + k * step) % L for k in range(n))
    else:
        n = max(1, int(round(L / pitch)))
        step = L / n
        ss = [offset * step + k * step for k in range(n + 1)]
        ss = [x for x in ss if 0.35 * step < x < L - 0.35 * step]
    out = []
    j = 0
    for x in ss:
        while j < len(seq) - 2 and cum[j + 1] < x:
            j += 1
        seg = cum[j + 1] - cum[j]
        f = (x - cum[j]) / seg if seg > 1e-12 else 0.0
        out.append(seq[j].lerp(seq[j + 1], max(0.0, min(1.0, f))))
    return out


def _sample_rings(rings, pitch, variation, forced=frozenset()):
    """
    Sample every ring at one global pitch. Sharp corners are samples. Each
    run between corners (longest first) picks the offset that best
    STAGGERS its samples against everything already sampled, so facing
    walls alternate and the triangulation is a zigzag rather than rungs —
    also where both faces of an arm belong to the same outline ring.
    `forced`: exact ring vertices that must be samples (e.g. junctions
    where routing defects sit), treated like corners.
    Returns {ring index: [points in ring order]}.
    """
    grid = _PointGrid(pitch)
    corners = {}
    for i, r in enumerate(rings):
        cs = set(_corners(r, pitch / 2.0))
        cs.update(k for k, q in enumerate(r) if (q.x, q.y) in forced)
        corners[i] = sorted(cs)
    runs = []
    for i, ring in enumerate(rings):
        for k in corners[i]:
            grid.add(ring[k])
        for r, (pts, closed) in enumerate(_runs(ring, corners[i])):
            runs.append((i, r, pts, closed))
    runs.sort(key=lambda x: -sum(a.dist(b) for a, b in zip(x[2], x[2][1:])))
    chosen = {}
    for rank, (i, r, pts, closed) in enumerate(runs):
        if rank == 0:
            cand = [0.5 * (variation % 2) + (0.0 if closed else 0.5)]
        else:
            cand = [k / 8.0 for k in range(8)]
        best, best_score = [], -1.0
        for off in cand:
            samp = _run_samples(pts, closed, pitch, off)
            score = (sum(grid.nearest(p, pitch) for p in samp) / len(samp)
                     if samp else 0.0)
            if score > best_score + 1e-9:
                best, best_score = samp, score
        chosen[(i, r)] = best
        for p in best:
            grid.add(p)
    out = {}
    for i, ring in enumerate(rings):
        pts = []
        for r, _ in enumerate(_runs(ring, corners[i])):
            if corners[i]:
                pts.append(ring[corners[i][r]])
            pts.extend(chosen[(i, r)])
        out[i] = pts
    return out


class _Region:
    """Material region (even-odd rings) with spatial indexes for the
    inside test, distance to the walls and crossing tests."""

    def __init__(self, rings, cell):
        self.rings = rings
        self.segs = [s for r in rings for s in _ring_segs(r)]
        self.cell = cell
        self.grid = {}
        self.rows = {}
        for k, (a, b) in enumerate(self.segs):
            for key in self._cells(min(a.x, b.x), max(a.x, b.x),
                                   min(a.y, b.y), max(a.y, b.y)):
                self.grid.setdefault(key, []).append(k)
            for r in range(math.floor(min(a.y, b.y) / cell),
                           math.floor(max(a.y, b.y) / cell) + 1):
                self.rows.setdefault(r, []).append(k)

    def _cells(self, x0, x1, y0, y1):
        c = self.cell
        for gx in range(math.floor(x0 / c), math.floor(x1 / c) + 1):
            for gy in range(math.floor(y0 / c), math.floor(y1 / c) + 1):
                yield (gx, gy)

    def near(self, x0, x1, y0, y1):
        out = set()
        for key in self._cells(x0, x1, y0, y1):
            out.update(self.grid.get(key, ()))
        return [self.segs[k] for k in out]

    def inside(self, p):
        """Even-odd ray cast (+x) over the segments of p's row."""
        inside = False
        for k in self.rows.get(math.floor(p.y / self.cell), ()):
            a, b = self.segs[k]
            if (a.y > p.y) != (b.y > p.y):
                x = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
                if p.x < x:
                    inside = not inside
        return inside

    def dist(self, p, r):
        """Distance to the walls if < r (else r)."""
        best = r
        for a, b in self.near(p.x - r, p.x + r, p.y - r, p.y + r):
            best = min(best, _seg_dist(p, a, b))
        return best

    def crosses(self, a, b):
        for c, d in self.near(min(a.x, b.x), max(a.x, b.x),
                              min(a.y, b.y), max(a.y, b.y)):
            if _segments_intersect(a, b, c, d):
                return True
        return False


def _interior_points(region, pitch, variation):
    """Global hex grid points well inside the material."""
    xs = [p.x for r in region.rings for p in r]
    ys = [p.y for r in region.rings for p in r]
    dy = pitch * math.sqrt(3) / 2.0
    off = (variation % 2) * pitch / 2.0
    out = []
    j0, j1 = math.floor(min(ys) / dy), math.ceil(max(ys) / dy)
    for j in range(j0, j1 + 1):
        y = j * dy
        shift = (pitch / 2.0 if j % 2 else 0.0) + off
        i0 = math.floor((min(xs) - shift) / pitch)
        i1 = math.ceil((max(xs) - shift) / pitch)
        for i in range(i0, i1 + 1):
            p = Vec2(i * pitch + shift, y)
            if region.inside(p) and region.dist(p, 0.6 * pitch) >= 0.6 * pitch:
                out.append(p)
    return out


def _delaunay(pts):
    """Bowyer–Watson. Returns the set of edges (i, j), i < j. A tiny
    deterministic jitter breaks the cocircular ties of rectilinear walls."""
    n = len(pts)
    if n < 3:
        return set()
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    jit = []
    for i, p in enumerate(pts):
        h = math.sin(i * 12.9898 + 78.233) * 43758.5453
        g = math.sin(i * 39.3467 + 11.135) * 24634.6345
        jit.append(((p.x + (h - math.floor(h) - 0.5) * 1e-7 * span),
                    (p.y + (g - math.floor(g) - 0.5) * 1e-7 * span)))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    big = span * 20
    P = jit + [(cx - big, cy - big), (cx + big, cy - big), (cx, cy + big)]

    def circ(a, b, c):
        ax, ay = P[a]; bx, by = P[b]; cx_, cy_ = P[c]
        d = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
        if abs(d) < 1e-18:
            return (0.0, 0.0, float('inf'))
        ux = ((ax * ax + ay * ay) * (by - cy_) + (bx * bx + by * by) * (cy_ - ay)
              + (cx_ * cx_ + cy_ * cy_) * (ay - by)) / d
        uy = ((ax * ax + ay * ay) * (cx_ - bx) + (bx * bx + by * by) * (ax - cx_)
              + (cx_ * cx_ + cy_ * cy_) * (bx - ax)) / d
        return (ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2)

    tris = {(n, n + 1, n + 2): circ(n, n + 1, n + 2)}
    for i in range(n):
        x, y = P[i]
        bad = [t for t, (ux, uy, r2) in tris.items()
               if (x - ux) ** 2 + (y - uy) ** 2 < r2]
        edges = {}
        for t in bad:
            for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                k = (min(e), max(e))
                edges[k] = edges.get(k, 0) + 1
            del tris[t]
        for (a, b), c in edges.items():
            if c == 1:
                tris[(a, b, i)] = circ(a, b, i)
    out = set()
    for t in tris:
        if max(t) >= n:
            continue
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            out.add((min(a, b), max(a, b)))
    return out


def _strut_ok(a, b, region):
    """Entirely inside the material: no proper crossing of a ring, and a
    few interior points (incl. the midpoint) strictly inside."""
    L = a.dist(b)
    if L < 1e-6:
        return False
    if region.crosses(a, b):
        return False
    for f in (0.25, 0.5, 0.75):
        q = a.lerp(b, f)
        if not region.inside(q) or region.dist(q, 1e-6) < 1e-6:
            return False
    return True


def _tangent_at(p, region):
    best = (float('inf'), None)
    for a, b in region.near(p.x - 1e-5, p.x + 1e-5, p.y - 1e-5, p.y + 1e-5):
        d = _seg_dist(p, a, b)
        if d < best[0]:
            best = (d, (b - a).normalized())
    return best[1] if best[0] < 1e-6 else None


def _wave(a, b, ta, tb, region, n=10):
    """Cubic Hermite from a to b leaving/meeting the walls tangentially."""
    if ta is None or tb is None:
        return [a, b]
    v = b - a
    if ta.x * v.x + ta.y * v.y < 0:
        ta = ta * -1.0
    if tb.x * v.x + tb.y * v.y < 0:
        tb = tb * -1.0
    ma = ta * abs(ta.x * v.x + ta.y * v.y)
    mb = tb * abs(tb.x * v.x + tb.y * v.y)
    pts = []
    for k in range(n + 1):
        t = k / n
        h00 = 2 * t ** 3 - 3 * t ** 2 + 1
        h10 = t ** 3 - 2 * t ** 2 + t
        h01 = -2 * t ** 3 + 3 * t ** 2
        h11 = t ** 3 - t ** 2
        pts.append(Vec2(h00 * a.x + h10 * ma.x + h01 * b.x + h11 * mb.x,
                        h00 * a.y + h10 * ma.y + h01 * b.y + h11 * mb.y))
    pts[0], pts[-1] = a, b
    for p, q in zip(pts, pts[1:]):
        if p.dist(q) > 1e-6 and (region.crosses(p, q) or
                                 not region.inside(p.lerp(q, 0.5))):
            return [a, b]                 # would leave the wall: stay straight
    return pts


class Web:
    """
    The candidate structural field of one wall region: sample points
    (boundary samples on the wall faces, interior hex points) and every
    valid strut between Delaunay neighbours (inside the material, never a
    piece of the wall itself). The PRINTED infill is a selection of these
    struts; continuity repair toggles struts within this web, so any
    correction is a local edit of the field, never invented geometry.
    """

    def __init__(self, pts, boundary, edges, region, pitch, ring_next=None,
                 rings=None, ring_of=None, base=None):
        self.pts = pts                  # [Vec2]
        self.boundary = boundary        # [bool] on a wall face (ring)
        self.edges = edges              # all candidate struts (i, j), i < j
        # the pattern's own struts (Delaunay neighbours): the default
        # selection comes only from these; the rest of `edges` are SKIP
        # struts (to a farther sample) that local repair may swap in.
        self.base = base if base is not None else set(edges)
        self.region = region
        self.pitch = pitch
        self.index = {(p.x, p.y): k for k, p in enumerate(pts)}
        self.ring_next = ring_next or {}    # boundary point → next on its ring
        self.rings = rings or []
        self.ring_of = ring_of or {}        # boundary point → ring index

    def face_midpoint(self, i, j):
        """The wall-face point halfway (along the face) between consecutive
        ring samples i and j — a new landing point between existing ones."""
        ring = self.rings[self.ring_of[i]]
        n = len(ring)
        cum = [0.0]
        for k in range(n):
            cum.append(cum[-1] + ring[k].dist(ring[(k + 1) % n]))
        L = cum[-1]

        def pos(p):
            best = (math.inf, 0.0)
            for k in range(n):
                a, b = ring[k], ring[(k + 1) % n]
                d = _seg_dist(p, a, b)
                if d < best[0]:
                    seg = cum[k + 1] - cum[k]
                    t = 0.0 if seg < 1e-12 else max(0.0, min(1.0, (
                        (p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / (seg * seg)))
                    best = (d, cum[k] + t * seg)
            return best[1]
        s0, s1 = pos(self.pts[i]), pos(self.pts[j])
        span = (s1 - s0) % L
        s = (s0 + span / 2) % L
        for k in range(n):
            if cum[k] <= s <= cum[k + 1]:
                seg = cum[k + 1] - cum[k]
                f = (s - cum[k]) / seg if seg > 1e-12 else 0.0
                return ring[k].lerp(ring[(k + 1) % n], f)
        return self.pts[i].lerp(self.pts[j], 0.5)

    def add_point(self, p, boundary=False):
        """A new web point (e.g. the bend of a local support strut)."""
        self.pts.append(p)
        self.boundary.append(boundary)
        self.index[(p.x, p.y)] = len(self.pts) - 1
        return len(self.pts) - 1

    def cap(self, k):
        """Struts allowed at a point: on a wall face one generated path
        (2), inside the wall two crossing paths (4)."""
        return CAP_BOUNDARY if self.boundary[k] else CAP_INTERIOR

    def length(self, e):
        return self.pts[e[0]].dist(self.pts[e[1]])


SKIP_REACH = 1.8          # × pitch: longest candidate (skip) strut
CAP_BOUNDARY = 2
CAP_INTERIOR = 4


def build_web(rings, spacing=20.0, variation=0, forced=()):
    rings = [_canonical(r) for r in rings if len(r) >= 3]
    if not rings:
        return None
    pitch = max(1.0, float(spacing))
    region = _Region(rings, max(pitch / 2.0, 2.0))
    forced_set = frozenset((p.x, p.y) for p in forced)
    samples = _sample_rings(rings, pitch, variation, forced_set)
    pts, boundary = [], []
    boundary_next = {}                    # index → next index on its ring
    ring_of = {}
    for i in range(len(rings)):
        ring_pts = samples[i]
        base = len(pts)
        pts.extend(ring_pts)
        boundary.extend([True] * len(ring_pts))
        for k in range(len(ring_pts)):
            boundary_next[base + k] = base + (k + 1) % len(ring_pts)
            ring_of[base + k] = i
    interior = _interior_points(region, pitch, variation)
    pts.extend(interior)
    boundary.extend([False] * len(interior))
    base = set()
    for i, j in sorted(_delaunay(pts)):
        if boundary_next.get(i) == j or boundary_next.get(j) == i:
            continue                      # a piece of the wall itself
        if _strut_ok(pts[i], pts[j], region):
            base.add((min(i, j), max(i, j)))
    # Skip struts: any other valid strut up to SKIP_REACH pitches long.
    # Not printed by default; they give local repair an alternative strut
    # to swap in, so a defect can be moved by a phase shift (zigzag strut ↔
    # skip strut) instead of by adding material.
    edges = set(base)
    reach = SKIP_REACH * pitch
    grid = {}
    for k, p in enumerate(pts):
        grid.setdefault((math.floor(p.x / reach), math.floor(p.y / reach)), []).append(k)
    for k, p in enumerate(pts):
        gx, gy = math.floor(p.x / reach), math.floor(p.y / reach)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in grid.get((gx + dx, gy + dy), ()):
                    if m <= k or (k, m) in edges or p.dist(pts[m]) > reach:
                        continue
                    if boundary_next.get(k) == m or boundary_next.get(m) == k:
                        continue
                    if _strut_ok(p, pts[m], region):
                        edges.add((k, m))
    return Web(pts, boundary, edges, region, pitch, boundary_next, rings,
               ring_of, base)


def select(web):
    """
    The default printed field: the web with its degree capped — at most one
    generated path through a wall-face sample (2 struts) and two crossing
    paths through an interior point (4 struts). Over-capped points (fans at
    corners, 6-way interior points) shed struts, preferring struts whose
    other end is over its cap too, then the longest. Mud-knot prevention
    by construction; parity defects this leaves are repaired locally.
    """
    S = set(web.base)
    deg = {}
    for i, j in S:
        deg[i] = deg.get(i, 0) + 1
        deg[j] = deg.get(j, 0) + 1
    over = sorted((k for k in deg if deg[k] > web.cap(k)),
                  key=lambda k: (-(deg[k] - web.cap(k)), k))
    while over:
        k = over[0]
        cand = [e for e in S if k in e]
        def excess(e):
            o = e[1] if e[0] == k else e[0]
            return (deg[o] > web.cap(o), web.length(e))
        e = max(cand, key=excess)
        S.discard(e)
        for n in e:
            deg[n] -= 1
        over = sorted((n for n in deg if deg[n] > web.cap(n)),
                      key=lambda n: (-(deg[n] - web.cap(n)), n))
    return S


def emit(web, struts, pattern='zigzag'):
    """Selected struts as polylines, chained through 2-strut points; 'wave'
    smooths each strut into a curve tangent to the walls."""
    pts, region = web.pts, web.region
    adj = {}
    for i, j in sorted(struts):
        adj.setdefault(i, []).append(j)
        adj.setdefault(j, []).append(i)
    used = set()
    chains = []

    def walk(start, nxt):
        chain = [start, nxt]
        used.add((min(start, nxt), max(start, nxt)))
        prev, cur = start, nxt
        while len(adj.get(cur, ())) == 2:
            a, b = adj[cur]
            n2 = b if a == prev else a
            e = (min(cur, n2), max(cur, n2))
            if e in used:
                break
            used.add(e)
            chain.append(n2)
            prev, cur = cur, n2
        return chain

    for node in sorted(adj):
        if len(adj[node]) != 2:
            for nb in adj[node]:
                if (min(node, nb), max(node, nb)) not in used:
                    chains.append(walk(node, nb))
    for i, j in sorted(struts):           # remaining pure cycles
        if (min(i, j), max(i, j)) not in used:
            chains.append(walk(i, j))
    out = []
    for ch in chains:
        if pattern == 'wave':
            poly = [pts[ch[0]]]
            for a, b in zip(ch, ch[1:]):
                seg = _wave(pts[a], pts[b], _tangent_at(pts[a], region),
                            _tangent_at(pts[b], region), region)
                poly.extend(seg[1:])
            out.append(poly)
        else:
            out.append([pts[k] for k in ch])
    return out
