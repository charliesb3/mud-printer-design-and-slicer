"""
Shared geometry for the wall-web research harness (EXPERIMENTAL — not used
by the Designer; delete freely).

Reuses the production wall-region machinery read-only:
  wall_lattice.skeleton / _Geo   chordal-axis runs, face correspondence
  infill._Region                 inside / crossing / distance tests
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = os.path.abspath(os.path.join(HERE, '..', '..'))
for p in (os.path.join(PROTO, 'tests'), PROTO):
    if p not in sys.path:
        sys.path.insert(0, p)
_TP = os.path.abspath(os.path.join(PROTO, '..', 'toolpath_proto'))
if _TP not in sys.path:
    sys.path.append(_TP)

import wall_lattice as WL          # noqa: E402
from infill import _Region         # noqa: E402
from model import Vec2             # noqa: E402

# physical defaults (material.py defaults; provisional machine values)
BEAD = 3.0                 # bead width W
CONTACT = 2.25             # contact separation W − O (O = 0.75)
SPACING = 20.0             # production Target Spacing default


def V(x, y):
    return Vec2(float(x), float(y))


def unit(v):
    L = v.length()
    return Vec2(v.x / L, v.y / L) if L > 1e-12 else Vec2(0.0, 0.0)


def rot(v, a):
    c, s = math.cos(a), math.sin(a)
    return Vec2(c * v.x - s * v.y, s * v.x + c * v.y)


def cross(a, b):
    return a.x * b.y - a.y * b.x


def dot(a, b):
    return a.x * b.x + a.y * b.y


def seg_closest(p, a, b):
    d = b - a
    L2 = dot(d, d)
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, dot(p - a, d) / L2))
    return a.lerp(b, t), t


def ring_segments(rings):
    """[(ring index, segment index, a, b, arc at a)]"""
    out = []
    for ri, r in enumerate(rings):
        s = 0.0
        for k in range(len(r)):
            a, b = r[k], r[(k + 1) % len(r)]
            out.append((ri, k, a, b, s))
            s += a.dist(b)
    return out


class Faces:
    """Nearest-face queries over the wall rings (face centrelines), with a
    grid index. project(p) → (distance, ring, arc, point, tangent)."""

    def __init__(self, rings, cell=6.0):
        self.rings = rings
        self.segs = ring_segments(rings)
        self.length = [sum(r[k].dist(r[(k + 1) % len(r)]) for k in range(len(r))) for r in rings]
        self.cell = cell
        self.grid = {}
        for i, (_, _, a, b, _) in enumerate(self.segs):
            for gx in range(math.floor(min(a.x, b.x) / cell), math.floor(max(a.x, b.x) / cell) + 1):
                for gy in range(math.floor(min(a.y, b.y) / cell), math.floor(max(a.y, b.y) / cell) + 1):
                    self.grid.setdefault((gx, gy), []).append(i)
        self.region = _Region(rings, cell)

    def _cands(self, p, r):
        c = self.cell
        out = set()
        for gx in range(math.floor((p.x - r) / c), math.floor((p.x + r) / c) + 1):
            for gy in range(math.floor((p.y - r) / c), math.floor((p.y + r) / c) + 1):
                out.update(self.grid.get((gx, gy), ()))
        return out

    def project(self, p, rmax=60.0):
        r = self.cell
        while True:
            best = None
            for i in self._cands(p, r):
                ri, k, a, b, s0 = self.segs[i]
                q, t = seg_closest(p, a, b)
                d = p.dist(q)
                if best is None or d < best[0]:
                    best = (d, ri, s0 + t * a.dist(b), q, unit(b - a))
            if best is not None and best[0] <= r:
                return best
            if r >= rmax:
                return best
            r *= 2

    def inside(self, p):
        return self.region.inside(p)


def arc_gap(L, a, b):
    """Forward arc distance a → b on a ring of length L."""
    return (b - a) % L


# ---------------------------------------------------------------------------
# Corridor: the production skeleton, read-only, with contact rails
# ---------------------------------------------------------------------------

class Corridor:
    """Wall runs of the production chordal-axis skeleton with their two
    faces and CONTACT RAILS (contact separation off each face centreline,
    across the wall) — the same correspondence the production lattice
    uses, so the candidates differ only in how the web is generated."""

    def __init__(self, rings, contact=CONTACT, spacing=SPACING):
        self.rings = rings
        self.contact = contact
        self.thick = WL._thickness(rings)
        h = max(0.5, min(self.thick / 3.0, spacing / 3.0))
        self.sk = WL.skeleton(rings, h, self.thick)
        self.geo = WL._Geo(self.sk)
        self.runs = [r for r in self.sk.runs if r.length > 1e-6 or r.cycle]
        self.faces = Faces(rings)

    def face(self, run, side, s):
        return self.geo.side_point(run, side, s)

    def rail(self, run, side, s):
        p, q = self.face(run, side, s), self.face(run, 1 - side, s)
        d = q - p
        L = d.length()
        if L < 2 * self.contact + 1e-6:
            return p.lerp(q, 0.5)
        return p + d * (self.contact / L)

    def width(self, run, s):
        """Rail-to-rail width (the cavity a brace actually crosses)."""
        return self.rail(run, 0, s).dist(self.rail(run, 1, s))

    def at(self, run, s, t):
        """Strip coordinates → plane: t = 0 rail 0, t = 1 rail 1."""
        return self.rail(run, 0, s).lerp(self.rail(run, 1, s), max(0.0, min(1.0, t)))

    def span(self, run):
        """Usable range: a dead end stops where the wall is still square
        across (production rule: ≥ half a thickness short of the cap)."""
        th = self.thick

        def back(s, step):
            for _ in range(12):
                if self.geo.perpendicular(run, s):
                    return s
                s += step
            return s
        L = run.length
        lo = 0.0 if run.cycle or run.a[0] == 'J' else back(min(0.5 * th, 0.25 * L), 0.25 * th)
        hi = L if run.cycle or run.b[0] == 'J' else back(L - min(0.5 * th, 0.25 * L), -0.25 * th)
        return lo, max(hi, lo + 1e-6)

    def corners(self, run, turn_deg=30.0):
        """Centre-line corners (production rule: turning > 30° within two
        thicknesses), as station positions only."""
        th = self.thick
        L = run.length
        W = 2.0 * th
        d = max(0.5, 0.25 * th)
        n = max(4, int(L / d))
        ss = [L * k / n for k in range(n + 1)]

        def ang(x):
            a = self.geo.centre(run, x - 0.5 * th) if (run.cycle or x - 0.5 * th > 0) else self.geo.centre(run, 0.0)
            b = self.geo.centre(run, x + 0.5 * th) if (run.cycle or x + 0.5 * th < L) else self.geo.centre(run, L)
            return math.atan2(b.y - a.y, b.x - a.x)

        def turn(x):
            t = ang(x + W / 2) - ang(x - W / 2)
            return (t + math.pi) % (2 * math.pi) - math.pi
        tv = [abs(turn(x)) for x in ss]
        lo, hi = self.span(run)
        found = []
        wn = int(W / d)
        for k, x in enumerate(ss):
            if tv[k] < math.radians(turn_deg):
                continue
            if not run.cycle and (x < lo + 0.75 * th or x > hi - 0.75 * th):
                continue
            if run.cycle and k == len(ss) - 1:
                continue
            m = len(ss) - 1
            nb = [tv[j % m] for j in range(k - wn, k + wn + 1)] if run.cycle else \
                [tv[j] for j in range(max(0, k - wn), min(len(ss), k + wn + 1))]
            if tv[k] + 1e-12 < max(nb):
                continue
            if found and abs(x - found[-1]) < W:
                continue
            found.append(x)
        if run.cycle and len(found) > 1 and found[0] + L - found[-1] < W:
            found.pop()
        return found

    def face_speed(self, run, s, ds=1.0):
        """(inner, outer) face progress per unit centre-line length at s."""
        L = run.length
        a, b = max(0.0, s - ds), min(L, s + ds) if not run.cycle else s + ds
        if run.cycle:
            a = s - ds
        dl = b - a
        if dl < 1e-9:
            return 1.0, 1.0
        f0 = self.face(run, 0, a).dist(self.face(run, 0, b)) / dl
        f1 = self.face(run, 1, a).dist(self.face(run, 1, b)) / dl
        return min(f0, f1), max(f0, f1)


# ---------------------------------------------------------------------------
# Polyline utilities
# ---------------------------------------------------------------------------

def resample(poly, step):
    out = [poly[0]]
    for a, b in zip(poly, poly[1:]):
        L = a.dist(b)
        n = max(1, int(math.ceil(L / step)))
        for k in range(1, n + 1):
            out.append(a.lerp(b, k / n))
    return out


def length(poly):
    return sum(a.dist(b) for a, b in zip(poly, poly[1:]))


def fillet(poly, r, keep_ends=True, samples=6):
    """Round every vertex with a circular arc of radius ≤ r (limited to
    half of each adjacent segment): the minimum turning radius of a mud
    bead. Exact for straight segments; vertices turning < 2° are kept."""
    if r <= 0 or len(poly) < 3:
        return list(poly)
    out = [poly[0]]
    n = len(poly)
    for i in range(1, n - 1):
        a, v, b = poly[i - 1], poly[i], poly[i + 1]
        u1, u2 = unit(v - a), unit(b - v)
        cosang = max(-1.0, min(1.0, dot(u1, u2)))
        turn = math.acos(cosang)
        if turn < math.radians(2):
            out.append(v)
            continue
        la, lb = v.dist(a), v.dist(b)
        tlen = r * math.tan(turn / 2)
        lim = 0.5 * min(la, lb)
        rr = r
        if tlen > lim:
            tlen = lim
            rr = tlen / math.tan(turn / 2)
        p1 = v - u1 * tlen
        p2 = v + u2 * tlen
        sgn = 1.0 if cross(u1, u2) > 0 else -1.0
        nrm = Vec2(-u1.y * sgn, u1.x * sgn)
        c = p1 + nrm * rr
        a0 = math.atan2(p1.y - c.y, p1.x - c.x)
        out.append(p1)
        for k in range(1, samples):
            ang = a0 + sgn * turn * k / samples
            out.append(Vec2(c.x + rr * math.cos(ang), c.y + rr * math.sin(ang)))
        out.append(p2)
    out.append(poly[-1])
    # drop zero-length steps
    clean = [out[0]]
    for p in out[1:]:
        if p.dist(clean[-1]) > 1e-6:
            clean.append(p)
    return clean


def bbox(rings, polys=()):
    xs = [p.x for r in rings for p in r] + [p.x for q in polys for p in q]
    ys = [p.y for r in rings for p in r] + [p.y for q in polys for p in q]
    return min(xs), min(ys), max(xs), max(ys)
