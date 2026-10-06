"""
Solid infill — conventional AREA fill of a solid region.

Wall infill (wall_lattice.py) stitches a wall BETWEEN ITS FACES. A solid is
different: a closed area (with voids) filled like a slicer fills it. The
PERIMETERS (the region boundary, every void boundary) and the INFILL have
distinct structural roles and are printed as distinct, coherent phases:

  1. PERIMETERS — the region boundary and every void boundary are the
     first perimeter (they are the printed source paths). `perimeters` > 1
     adds inward copies spaced `perimeter_spacing` apart (the field then
     attaches to the innermost one).
  2. FIELD — parallel lines at `angle`, `spacing` apart (a target),
     clipped EXACTLY to the region (even-odd: voids stay empty), joined
     into boustrophedon CHAINS by short turns whose apex lands on the
     boundary (V: rectilinear, smooth U: serpentine); nearby chain ends are
     linked the same way. 'rectilinear' is the conventional straight fill.
  3. SERPENTINE WEB — every line is a sine across its own line, neighbours
     180° out of phase, amplitude ½ spacing, so neighbouring strands TOUCH
     at alternating apexes: one interconnected web of smooth waves. Each
     contact is a shared vertex of exactly two strands — a routing
     hand-off, never a knot. Free chain ends turn into a neighbouring free
     end where a local turn exists.
  4. ROUTING — boundary contacts are physical ties, not routing junctions,
     except ONE hand-off point per boundary ring (`attach`): each
     perimeter is printed as one complete loop, the infill as its own
     phase. Zero travel is a preference, not the objective: where the
     field is split (voids), a short travel is accepted rather than
     distorting the fill or reprinting a perimeter.
  5. BOUNDARY SUPPORT — measured and reported; landings (the nearest pass
     bent onto the boundary) are added only when the user sets
     `max_unsupported`.

Spacing is a target: nothing here is corrected by a tiny extra stitch.
(Pass 8's closed "hairpin" topology — every line end paired into closed
loops so the whole layer was one Eulerian circuit — is SUPERSEDED: it made
the route weave between perimeter and infill.)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from model import Vec2, _offset_polyline, _trim_offset, _dedupe_polyline
from infill import _Region, _strut_ok

PATTERNS = {
    'rectilinear': 'Parallel lines at an angle; V turns brace the boundary',
    'serpentine': 'Flowing meander (parallel waves); smooth U turns brace the boundary',
}
PARAMETERS = [
    {'name': 'spacing', 'label': 'Spacing', 'default': 20.0, 'min': 2.0, 'max': 120.0, 'step': 1.0},
    {'name': 'angle', 'label': 'Angle', 'default': 45.0, 'min': -90.0, 'max': 180.0, 'step': 5.0},
    {'name': 'perimeters', 'label': 'Perimeters', 'default': 1, 'min': 1, 'max': 4, 'step': 1},
]
TURN_BACK = 0.5         # × spacing: a turn starts this far before the boundary
BUMP_REACH = 1.25       # × spacing: farthest pass that may be bent onto the boundary
LAND_CLEAR = 0.3        # × spacing: a landing keeps this clear of other infill

LINK_MAX = 2.0          # × spacing: bound on a turn leg (reported as max_connector)
JOIN_MAX = 4.0          # × spacing: longest boundary stretch a turn may bridge
PERIMETER_SPACING = 6.0  # default spacing of extra perimeters (in)
WEB_AMP = 0.5           # × spacing: web amplitude — neighbours touch at apexes
WEB_LEN = 3.0           # × spacing: serpentine web wavelength (amplitude = ½ spacing:
                        # neighbours, 180° apart, touch at alternating apexes)
WEB_STRAIGHT = 0.6      # × spacing: straight run into a boundary turn
WEB_TAPER = 0.9         # × spacing: the swing fades in over this
WAVE_SAMPLES = 36       # points per wavelength (smooth printable curve, ≲ 11° per vertex)


@dataclass
class SolidPlan:
    lines: list = field(default_factory=list)        # trail polylines [[Vec2]]
    connectors: list = field(default_factory=list)   # (kept for the API; links are in lines)
    perimeters: list = field(default_factory=list)   # extra perimeter rings
    trails: int = 0
    attach: list = field(default_factory=list)       # one (x, y) routing junction per ring
    report: dict = field(default_factory=dict)


def _inner_rings(rings, depth):
    """Rings offset `depth` into the material (outer inward, holes out)."""
    out = []
    for r in rings:
        pts = _dedupe_polyline(r, True)
        if len(pts) < 3:
            continue
        # material is on the LEFT of every ring → + offset goes inside
        raw = _offset_polyline(pts, depth, True)
        trimmed = _trim_offset(raw, pts, depth, True)
        if len(trimmed) >= 3:
            out.append(trimmed)
    return out


class _Line:
    __slots__ = ('k', 'a', 'b', 'ra', 'rb', 'used')

    def __init__(self, k, a, b, ra, rb):
        self.k, self.a, self.b, self.ra, self.rb = k, a, b, ra, rb
        self.used = False


def _hatch(rings, spacing, angle, offset=0.5):
    """Parallel segments clipped EXACTLY to the region (end points lie on
    the ring segments they hit). [_Line] with a → b along the direction.
    `offset`: phase of the lines in units of the spacing."""
    th = math.radians(angle)
    ux, uy = math.cos(th), math.sin(th)
    vx, vy = -uy, ux
    segs = [(ri, r[i], r[(i + 1) % len(r)]) for ri, r in enumerate(rings) for i in range(len(r))]
    vs = [p.x * vx + p.y * vy for r in rings for p in r]
    if not vs:
        return [], (ux, uy)
    lo, hi = min(vs), max(vs)
    out = []
    for k in range(math.floor(lo / spacing), math.ceil(hi / spacing) + 1):
        t = k * spacing + offset * spacing + 1e-7     # never exactly on a vertex
        xs = []
        for ri, a, b in segs:
            va, vb = a.x * vx + a.y * vy, b.x * vx + b.y * vy
            if (va > t) != (vb > t):
                f = (t - va) / (vb - va)
                p = a.lerp(b, f)                       # exactly on the ring segment
                xs.append((p.x * ux + p.y * uy, p, ri))
        xs.sort(key=lambda x: x[0])
        for i in range(0, len(xs) - 1, 2):
            (u0, p0, r0), (u1, p1, r1) = xs[i], xs[i + 1]
            if u1 - u0 < 0.25 * spacing:
                continue                                # a sliver at a tip
            out.append(_Line(k, p0, p1, r0, r1))
    return out, (ux, uy)


def _nearest_on_ring(p, ring):
    best = (math.inf, None)
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
        q = a if t <= 0 else (b if t >= 1 else a.lerp(b, t))
        d = p.dist(q)
        if d < best[0]:
            best = (d, q)
    return best


class _Builder:
    def __init__(self, rings, region, spacing, pattern, u):
        self.rings, self.region, self.sp, self.pattern = rings, region, spacing, pattern
        self.u = u
        self.max_leg = 0.0
        self.n_contacts = 0
        self.web_ok = {}

    # -- line bodies ---------------------------------------------------------
    def prepare_web(self, lines):
        """SERPENTINE WEB. Every hatch line becomes a sine across its own
        line, neighbours 180° out of phase, amplitude = half the spacing:
        neighbouring strands TOUCH at alternating apexes (line k meets k + 1
        at its crests, k − 1 at its troughs) — one interconnected web of
        smooth waves, not parallel noodles. Each contact is ONE shared
        vertex of both strands (a routing hand-off of exactly two strands).
        The swing fades into straight ends before the boundary turns and
        shrinks where a line runs close to a boundary (no contact there)."""
        sp = self.sp
        self.lam = WEB_LEN * sp
        self.A = WEB_AMP * sp
        self.v = Vec2(-self.u[1], self.u[0])
        self.contacts = {}
        ok = {}
        for ln in lines:
            ok[id(ln)] = self._web_ok(ln)
        by_k = {}
        for ln in lines:
            by_k.setdefault(ln.k, []).append(ln)
        for ln in lines:
            if not ok[id(ln)]:
                continue
            for nb in by_k.get(ln.k + 1, ()):
                if not ok[id(nb)]:
                    continue
                x0 = max(self._x_range(ln)[0], self._x_range(nb)[0])
                x1 = min(self._x_range(ln)[1], self._x_range(nb)[1])
                if x1 <= x0:
                    continue
                # crests of line k: 2πx/λ + kπ = π/2 + 2πm
                m0 = math.ceil((x0 / self.lam) - 0.25 + 0.5 * ln.k)
                m1 = math.floor((x1 / self.lam) - 0.25 + 0.5 * ln.k)
                for m in range(m0, m1 + 1):
                    x = self.lam * (0.25 - 0.5 * ln.k + m)
                    if self._env(ln, x) < 1.0 - 1e-12 or self._env(nb, x) < 1.0 - 1e-12:
                        continue
                    c = self._base(ln, x) + self.v * self.A
                    if c.dist(self._base(nb, x) - self.v * self.A) > 1e-6:
                        continue
                    self.contacts.setdefault(id(ln), []).append((x, c))
                    self.contacts.setdefault(id(nb), []).append((x, c))
        self.web_ok = ok
        self.n_contacts = sum(len(v) for v in self.contacts.values()) // 2

    def _x_range(self, ln):
        xa = ln.a.x * self.u[0] + ln.a.y * self.u[1]
        xb = ln.b.x * self.u[0] + ln.b.y * self.u[1]
        return min(xa, xb), max(xa, xb)

    def _base(self, ln, x):
        xa = ln.a.x * self.u[0] + ln.a.y * self.u[1]
        xb = ln.b.x * self.u[0] + ln.b.y * self.u[1]
        f = (x - xa) / (xb - xa) if abs(xb - xa) > 1e-12 else 0.0
        return ln.a.lerp(ln.b, f)

    def _env(self, ln, x):
        """Swing envelope 0…1: straight near the line's ends (the turns
        start there), reduced where the line is close to a boundary."""
        lo, hi = self._x_range(ln)
        d = min(x - lo, hi - x)
        w = max(0.0, min(1.0, (d - WEB_STRAIGHT * self.sp) / (WEB_TAPER * self.sp)))
        w = w * w * (3 - 2 * w)
        margin = 0.25 * self.sp
        c = self._base(ln, x)
        clr = self.region.dist(c, self.A + margin)
        return min(w, max(0.0, (clr - margin) / self.A))

    def _wave(self, ln, x):
        return self._base(ln, x) + self.v * (self.A * self._env(ln, x) *
                                             math.sin(2 * math.pi * x / self.lam + ln.k * math.pi))

    def _web_ok(self, ln):
        lo, hi = self._x_range(ln)
        n = max(2, int(math.ceil((hi - lo) / (self.lam / WAVE_SAMPLES))))
        pts = [self._wave(ln, lo + (hi - lo) * j / n) for j in range(n + 1)]
        return all(_strut_ok(a, b, self.region) for a, b in zip(pts, pts[1:]) if a.dist(b) > 1e-9)

    def body(self, p, q, ln=None):
        """Points strictly between p and q along field line ln."""
        self.n_contacts = getattr(self, 'n_contacts', 0)
        if self.pattern != 'serpentine' or ln is None or not self.web_ok.get(id(ln)):
            return []
        xp = p.x * self.u[0] + p.y * self.u[1]
        xq = q.x * self.u[0] + q.y * self.u[1]
        L = abs(xq - xp)
        if L < 1e-9:
            return []
        n = max(2, int(math.ceil(L / (self.lam / WAVE_SAMPLES))))
        xs = [xp + (xq - xp) * j / n for j in range(1, n)]
        lo, hi = min(xp, xq), max(xp, xq)
        cs = {x: c for x, c in self.contacts.get(id(ln), ()) if lo + 1e-9 < x < hi - 1e-9}
        # contacts replace the nearest uniform samples (no near-duplicate)
        step = L / n
        xs = [x for x in xs if all(abs(x - xc) > 0.25 * step for xc in cs)] + list(cs)
        xs.sort(reverse=xq < xp)
        return [cs[x] if x in cs else self._wave(ln, x) for x in xs]

    # -- turns / links at the boundary ---------------------------------------
    def turn(self, p_end, dir_in, q_start, dir_out, ring_i, len_in=1e9, len_out=1e9):
        """A turn from a line ending at p_end (moving dir_in) to a line
        starting at q_start (leaving along dir_out), touching ring ring_i
        between them. Returns (P, [turn points incl. apex], C) — P / C are
        the pulled-back line ends — or None if no legal turn exists."""
        short = min(len_in, len_out)
        for back in (TURN_BACK, 0.35, 0.2):
            # never pull back more than 30 % of a (short) line
            m = min(back * self.sp, 0.3 * short)
            P = p_end - dir_in * m
            C = q_start + dir_out * m
            mid = P.lerp(C, 0.5)
            _, T = _nearest_on_ring(mid, self.rings[ring_i])
            if T is None:
                return None
            if T.dist(P) < 1e-6 or T.dist(C) < 1e-6:
                continue
            if _strut_ok(P, T, self.region) and _strut_ok(T, C, self.region):
                # serpentine: a smooth U where the lines are long enough to carry
                # it; short lines (narrow places) turn with a plain V
                smooth = self.pattern == 'serpentine' and short >= WEB_LEN * self.sp
                pts = [T] if not smooth else self._u_turn(P, T, C, dir_in, dir_out)
                if pts is None:
                    pts = [T]
                self.max_leg = max(self.max_leg, P.dist(T), T.dist(C))
                return P, pts, C
        return None

    def _u_turn(self, P, T, C, din, dout):
        """Smooth U through the apex T (Catmull–Rom), inside the region."""
        ctrl = [P - din * (0.5 * self.sp), P, T, C, C + dout * (0.5 * self.sp)]
        out = []
        for seg in (1, 2):
            p0, p1, p2, p3 = ctrl[seg - 1], ctrl[seg], ctrl[seg + 1], ctrl[seg + 2]
            for j in range(1, 6):
                t = j / 6
                t2, t3 = t * t, t * t * t
                out.append(Vec2(
                    0.5 * ((2 * p1.x) + (-p0.x + p2.x) * t + (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2
                           + (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3),
                    0.5 * ((2 * p1.y) + (-p0.y + p2.y) * t + (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2
                           + (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3)))
            if seg == 1:
                out.append(T)
        pts = [P - din * 1.0] + [P] + out + [C] + [C + dout * 1.0]
        # a U must not reverse on itself (a cusp beside a convex void folds
        # three bead pieces into one spot): every turn < 90°, incl. against
        # the lines it joins
        for a, b, c in zip(pts, pts[1:], pts[2:]):
            v1, v2 = b - a, c - b
            l1, l2 = v1.length(), v2.length()
            if l1 > 1e-9 and l2 > 1e-9 and (v1.x * v2.x + v1.y * v2.y) / (l1 * l2) < 0.0:
                return None
        core = pts[1:-1]
        if all(_strut_ok(a, b, self.region) for a, b in zip(core, core[1:]) if a.dist(b) > 1e-9):
            return out
        return None


def plan(rings, params, pattern='rectilinear'):
    """
    Solid infill for one region (rings: material on the left — outer
    boundary + voids). Returns a SolidPlan.
    """
    spacing = max(1.0, float(params.get('spacing', 20.0)))
    angle = float(params.get('angle', 45.0))
    n_per = max(1, int(params.get('perimeters', 1)))
    pw = float(params.get('perimeter_spacing', PERIMETER_SPACING))
    # boundary-support limit: OPT-IN (0 = diagnostic only, no landings)
    dmax = float(params.get('max_unsupported', 0) or 0)
    if dmax:
        dmax = max(dmax, spacing)
    pattern = pattern if pattern in PATTERNS else 'rectilinear'
    rings = [_dedupe_polyline(r, True) for r in rings if len(r) >= 3]
    sp = SolidPlan()
    if not rings:
        return sp
    hatch_rings = rings
    for k in range(1, n_per):
        inner = _inner_rings(rings, k * pw)
        if not inner:
            break
        sp.perimeters.extend(inner)
        hatch_rings = inner
    region = _Region(hatch_rings, max(spacing / 2, 2.0))
    lines, u = _hatch(hatch_rings, spacing, angle)
    B = _Builder(hatch_rings, region, spacing, pattern, u)
    du = Vec2(*u)
    if pattern == 'serpentine':
        B.prepare_web(lines)

    # -- chains: consecutive lines joined by a turn on the boundary ---------
    by_k = {}
    for ln in lines:
        by_k.setdefault(ln.k, []).append(ln)
    for k in by_k:
        by_k[k].sort(key=lambda l: l.a.x * u[0] + l.a.y * u[1])
    chains = []          # each: list of (line, forward: bool)
    joins = {}           # (id(line_i), id(line_j)) → turn
    for k in sorted(by_k):
        for ln in by_k[k]:
            if ln.used:
                continue
            ln.used = True
            chain = [(ln, True)]
            fwd = True
            cur = ln
            while True:
                end, end_ring = (cur.b, cur.rb) if fwd else (cur.a, cur.ra)
                din = du if fwd else du * -1.0
                best = None
                for nxt in by_k.get(cur.k + 1, []):
                    if nxt.used:
                        continue
                    # the next line starts on the same side (boustrophedon)
                    start, s_ring = (nxt.b, nxt.rb) if fwd else (nxt.a, nxt.ra)
                    if s_ring != end_ring or start.dist(end) > JOIN_MAX * spacing:
                        continue
                    dout = du * -1.0 if fwd else du
                    t = B.turn(end, din, start, dout, end_ring,
                               cur.a.dist(cur.b), nxt.a.dist(nxt.b))
                    if t and (best is None or start.dist(end) < best[0]):
                        best = (start.dist(end), nxt, t)
                if best is None:
                    break
                _, nxt, t = best
                nxt.used = True
                joins[(id(cur), id(nxt))] = t
                fwd = not fwd
                chain.append((nxt, fwd))
                cur = nxt
            chains.append(chain)

    def chain_ends(ch):
        (l0, f0), (l1, f1) = ch[0], ch[-1]
        s = ((l0.a, l0.ra, du) if f0 else (l0.b, l0.rb, du * -1.0))
        e = ((l1.b, l1.rb, du) if f1 else (l1.a, l1.ra, du * -1.0))
        return s, e                      # (point, ring, direction of travel there)

    # -- trails: link chain ends with short V / U links at the boundary -----
    cand = []
    for i, ci in enumerate(chains):
        for j, cj in enumerate(chains):
            if j <= i:
                continue
            si, ei = chain_ends(ci)
            sj, ej = chain_ends(cj)
            # end of one → start of the other, in either orientation
            for (pa, ra, da), (pb, rb, db), ki, kj in (
                    (ei, sj, 'e', 's'), (ei, ej, 'e', 'e'), (si, sj, 's', 's'), (si, ej, 's', 'e')):
                if ra != rb or pa.dist(pb) > LINK_MAX * spacing:
                    continue
                din = da if ki == 'e' else da * -1.0
                dout = db if kj == 's' else db * -1.0
                t = B.turn(pa, din, pb, dout, ra)
                if t:
                    cand.append((pa.dist(pb), i, ki, j, kj, t))
    cand.sort(key=lambda c: c[0])
    parent = list(range(len(chains)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    used_end = set()
    links = {}
    for d, i, ki, j, kj, t in cand:
        if (i, ki) in used_end or (j, kj) in used_end or find(i) == find(j):
            continue
        used_end.update({(i, ki), (j, kj)})
        parent[find(i)] = find(j)
        links[(i, ki)] = (j, kj, t, 'out')
        links[(j, kj)] = (i, ki, t, 'in')

    # -- assemble polylines --------------------------------------------------
    def chain_poly(ch):
        """Line bodies joined by their boundary turns:
        start → body → P | apex | C → body → P | … → end."""
        pts = []
        for idx, (ln, f) in enumerate(ch):
            p, q = (ln.a, ln.b) if f else (ln.b, ln.a)
            if idx > 0:
                _, apex, C = joins[(id(ch[idx - 1][0]), id(ln))]
                pts += apex
                p = C
            if idx + 1 < len(ch):
                q = joins[(id(ln), id(ch[idx + 1][0]))][0]
            pts.append(p)
            pts += B.body(p, q, ln)
            pts.append(q)
        return pts

    polys = [chain_poly(ch) for ch in chains]
    # follow the links: trails of chains
    done = set()
    trails = []
    for start in range(len(chains)):
        if start in done:
            continue
        # walk to an end of this trail (a chain end without a link)
        cur, side = start, 's'
        seen = set()
        while (cur, side) in links and cur not in seen:
            seen.add(cur)
            j, kj, _, _ = links[(cur, side)]
            cur, side = j, ('e' if kj == 's' else 's')
        # cur's `side` end is free: walk from there through the trail
        trail = []
        entry = side
        while True:
            done.add(cur)
            poly = polys[cur] if entry == 's' else polys[cur][::-1]
            out_side = 'e' if entry == 's' else 's'
            trail += poly
            if (cur, out_side) not in links:
                break
            j, kj, t, direction = links[(cur, out_side)]
            P, tp, C = t
            # the link's turn: trim the chain end back to P, apex, then C
            # (orientation of t follows the candidate order)
            if direction == 'out':
                seq = [P] + tp + [C]
            else:
                seq = [C] + tp[::-1] + [P]
            # the chain end is pulled back from the boundary to P: drop the
            # body points lying between P and the old end (no fold-back)
            end_pt = trail[-1]
            reach = end_pt.dist(seq[0])
            while len(trail) > 1 and trail[-2].dist(end_pt) < reach - 1e-9:
                trail.pop(-2)
            trail[-1] = seq[0]
            nxt = polys[j] if kj == 's' else polys[j][::-1]
            trail += seq[1:-1]
            cur, entry = j, kj
            nxt = list(nxt)
            start_pt = nxt[0]
            reach = start_pt.dist(seq[-1])
            while len(nxt) > 1 and nxt[1].dist(start_pt) < reach - 1e-9:
                nxt.pop(1)
            nxt[0] = seq[-1]
            polys[j] = nxt if kj == 's' else nxt[::-1]
        trails.append(trail)

    # -- voids no line reached: redirect one nearby turn onto them -----------
    touched = set()
    for tr in trails:
        for q in tr:
            for ri, ring in enumerate(hatch_rings):
                if ri not in touched and _nearest_on_ring(q, ring)[0] < 1e-6:
                    touched.add(ri)
    for ri, ring in enumerate(hatch_rings):
        if ri in touched:
            continue
        best = None
        for ti, tr in enumerate(trails):
            for k in range(1, len(tr) - 1):
                d, q = _nearest_on_ring(tr[k], ring)
                if q is not None and d <= LINK_MAX * spacing and \
                        _strut_ok(tr[k - 1], q, region) and _strut_ok(q, tr[k + 1], region) and \
                        (best is None or d < best[0]):
                    # replace a turn apex (a point lying on another ring)
                    if any(_nearest_on_ring(tr[k], r2)[0] < 1e-6 for r2 in hatch_rings):
                        best = (d, ti, k, q)
        if best:
            _, ti, k, q = best
            trails[ti][k] = q
            touched.add(ri)

    # -- web: free chain ends turn into a neighbouring free end ------------
    if pattern == 'serpentine':
        trails, extra = _close_web_ends(trails, B, hatch_rings, du, spacing)
        trails += extra

    # -- optional boundary support (only with a user limit) -----------------
    bumps = 0
    if dmax:
        trails, bumps = _support(trails, hatch_rings, region, B, dmax)
    gaps = _ring_gaps(trails, hatch_rings)

    sp.lines = [_simplify(t) for t in trails if len(t) >= 2]
    sp.trails = len(sp.lines)
    sp.attach = _attach_points(sp.lines, hatch_rings)
    sp.report = {'lines': len(lines), 'chains': len(chains), 'links': len(links) // 2,
                 'trails': sp.trails, 'connectors': len(links) // 2,
                 'max_connector': round(B.max_leg, 3),
                 'untouched_boundaries': sum(1 for g in gaps if g is None),
                 'pattern': pattern,
                 'web_contacts': B.n_contacts,
                 'bumps': bumps,
                 'max_unsupported': round(max((g for g in gaps if g is not None), default=0.0), 2),
                 'max_unsupported_limit': round(dmax, 2)}
    return sp


def _close_web_ends(trails, B, rings, du, spacing):
    """SERPENTINE WEB: the strands are already tied together by their
    contacts, so a chain end the boustrophedon left FREE (a stop / restart
    of the extrusion) is turned into a neighbouring free end on the same
    boundary with the same V / U turn used everywhere else — a local
    connection, never a long connector (≤ JOIN_MAX × spacing apart).
    Free ends without a legal neighbour stay free (short travel)."""
    import networkx as nx
    rrs = [_Region([r], 4.0) for r in rings]
    ends = []                                  # (trail, 'e'|'s', point, ring, dir)
    for ti, t in enumerate(trails):
        if len(t) < 2 or t[0].dist(t[-1]) < 1e-9:
            continue
        for side, E, nb in (('e', t[-1], t[-2]), ('s', t[0], t[1])):
            ri = next((i for i, rr in enumerate(rrs) if rr.dist(E, 1e-5) < 1e-6), None)
            if ri is None:
                continue
            d = E - nb if side == 'e' else nb - E     # travel direction at the end
            sgn = 1.0 if d.x * du.x + d.y * du.y >= 0 else -1.0
            ends.append((ti, side, E, ri, du * sgn))
    G = nx.Graph()
    turns = {}
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            ti, si, Ei, ri, di = ends[i]
            tj, sj, Ej, rj, dj = ends[j]
            if ri != rj or Ei.dist(Ej) > JOIN_MAX * spacing:
                continue
            # arrive at Ei along the trail (end) or against it (start) …
            din = di if si == 'e' else di * -1.0
            # … and leave Ej into its trail
            dout = dj if sj == 's' else dj * -1.0
            t = B.turn(Ei, din, Ej, dout, ri)
            if t is None:
                continue
            P, apex, C = t
            G.add_edge(i, j, weight=1e6 - (P.dist(apex[0]) + apex[-1].dist(C)))
            turns[(i, j)] = t
    extra = []
    trails = [list(t) for t in trails]
    for i, j in nx.max_weight_matching(G, maxcardinality=False):
        if (i, j) not in turns:
            i, j = j, i
        P, apex, C = turns[(i, j)]
        for (ti, side, E, _, _), X in ((ends[i], P), (ends[j], C)):
            t = trails[ti]
            if side == 'e':
                while len(t) > 2 and t[-2].dist(E) < X.dist(E) - 1e-9:
                    t.pop(-2)
                t[-1] = X
            else:
                while len(t) > 2 and t[1].dist(E) < X.dist(E) - 1e-9:
                    t.pop(1)
                t[0] = X
        extra.append([P] + list(apex) + [C])
    return trails, extra


def _attach_points(trails, rings):
    """ONE routing junction per boundary ring: where the infill hands over
    to / from that perimeter. Every other contact is a physical tie the
    route passes straight over, so each perimeter is printed as one
    coherent loop. Preference: a trail END on the ring (the perimeter is
    completed, then the infill starts right there), else any contact."""
    out = []
    for ring in rings:
        rr = _Region([ring], 4.0)
        ends = [q for t in sorted(trails, key=len, reverse=True) for q in (t[0], t[-1])
                if rr.dist(q, 1e-5) < 1e-6]
        if ends:
            out.append((ends[0].x, ends[0].y))
            continue
        for t in trails:
            hit = next((q for q in t if rr.dist(q, 1e-5) < 1e-6), None)
            if hit is not None:
                out.append((hit.x, hit.y))
                break
    return out


def _arc_on(ring, cum, p):
    """Arc position on ring of (a point near) p."""
    best = (math.inf, 0.0)
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
        d = p.dist(a.lerp(b, t))
        if d < best[0]:
            best = (d, cum[i] + t * math.sqrt(L2))
    return best[1]


def _ring_gaps(trails, rings):
    """Longest stretch of each ring between infill contacts (None: no
    contact at all)."""
    out = []
    for ring in rings:
        n = len(ring)
        cum = [0.0]
        for i in range(n):
            cum.append(cum[-1] + ring[i].dist(ring[(i + 1) % n]))
        L = cum[-1]
        pos = sorted({round(x, 6) for x in _contacts(trails, ring, cum)})
        if not pos:
            out.append(None)
            continue
        gaps = [b - a for a, b in zip(pos, pos[1:])] + [pos[0] + L - pos[-1]]
        out.append(max(gaps))
    return out


def _support(trails, rings, region, B, dmax):
    """BOUNDARY SUPPORT: wherever a ring runs farther than dmax between
    infill contacts (lines nearly parallel to it, a void no line reaches),
    the nearest pass is bent onto the boundary — a smooth landing for
    serpentine, a V for rectilinear — so the support is part of the
    continuous infill, never a separate stub. A landing adds degree 2 on
    the boundary: continuity is unchanged."""
    bumps = 0
    sp = B.sp
    for ri, ring in enumerate(rings):
        n = len(ring)
        cum = [0.0]
        for i in range(n):
            cum.append(cum[-1] + ring[i].dist(ring[(i + 1) % n]))
        L = cum[-1]
        failed = set()
        pos = sorted(_contacts(trails, ring, cum))
        for _ in range(96):                       # one landing per round
            if pos:
                gaps = [(b - a, a) for a, b in zip(pos, pos[1:])] + [(pos[0] + L - pos[-1], pos[-1])]
            else:
                gaps = [(L, 0.0)]
            gaps = [g for g in gaps if g[0] > dmax + 1e-6 and round(g[1], 3) not in failed]
            if not gaps:
                break
            g, a0 = max(gaps)
            k = int(math.ceil(g / dmax)) - 1 if pos else max(1, int(math.ceil(L / dmax)))
            # targets evenly inside the gap, the middle one first
            cands = [a0 + g * (j + 1) / (k + 1) for j in range(k)]
            cands.sort(key=lambda x: abs(x - (a0 + g / 2)))
            tries = []
            for x in cands:
                for f in (0.0, -0.15, 0.15, -0.3, 0.3):
                    y = x + f * min(dmax, g / (k + 1))
                    if a0 < y < a0 + g:
                        tries.append(y)
            win = (a0 + 0.15 * g, a0 + 0.85 * g)
            T = None
            for x in tries:
                T = _land(trails, _ring_point(ring, cum, x % L), region, B, ring, cum, win)
                if T is not None:
                    break
            if T is None:
                failed.add(round(a0, 3))
                continue
            pos = sorted(pos + [_arc_on(ring, cum, T)])
            bumps += 1
    return trails, bumps


def _contacts(trails, ring, cum):
    """Arc positions on `ring` of the trail points lying ON it (spatially
    indexed)."""
    rr = _Region([ring], 4.0)
    out = []
    for t in trails:
        for q in t:
            if rr.dist(q, 1e-5) < 1e-6:
                out.append(_arc_on(ring, cum, q))
    return out


def _ring_point(ring, cum, s):
    n = len(ring)
    for i in range(n):
        if cum[i + 1] >= s:
            seg = cum[i + 1] - cum[i]
            f = (s - cum[i]) / seg if seg > 1e-12 else 0.0
            return ring[i].lerp(ring[(i + 1) % n], f)
    return ring[0]


def _land(trails, T0, region, B, ring, cum, win):
    """Bend a trail portion near boundary target T0 onto the boundary (in
    place). The landing point is the boundary point nearest that portion
    (a pass running past a convex void lands where it is closest to it, so
    its legs never cut the void); it must fall inside the arc window `win`
    (the middle of the gap it splits). The few nearest portions are tried."""
    reach = BUMP_REACH * B.sp
    L = cum[-1]
    lo, hi = win
    rr = _Region([ring], max(4.0, B.sp))
    cand = []
    lim = 2.0 * reach
    for ti, tr in enumerate(trails):
        for k in range(len(tr) - 1):
            a, b = tr[k], tr[k + 1]
            if (min(a.x, b.x) - lim > T0.x or max(a.x, b.x) + lim < T0.x or
                    min(a.y, b.y) - lim > T0.y or max(a.y, b.y) + lim < T0.y):
                continue
            dx, dy = b.x - a.x, b.y - a.y
            L2 = dx * dx + dy * dy
            f = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((T0.x - a.x) * dx + (T0.y - a.y) * dy) / L2))
            Q = a.lerp(b, f)
            d0 = Q.dist(T0)
            if d0 < 2.0 * reach:
                cand.append((d0, ti, k, Q))
    cand.sort(key=lambda c: c[0])
    per_trail = {}
    for c in cand:                      # each nearby pass once (its nearest part)
        per_trail.setdefault(c[1], c)
    seen = set()
    for d0, ti, k0, Q0 in sorted(per_trail.values(), key=lambda c: c[0])[:8]:
        # two landing points per pass: straight across from the target
        # (outer boundaries curving round the pass), and the pass's
        # closest approach to the ring (a convex void: landing where the
        # pass is tangent-closest keeps both legs outside the void)
        for k, Q in ((k0, Q0), _closest_approach(trails[ti], k0, ring, rr=rr)):
            d, T = _nearest_on_ring(Q, ring)
            if T is None or d > reach or d < 1e-6:
                continue
            x = _arc_on(ring, cum, T)
            if not any(lo <= y <= hi for y in (x, x + L, x - L)):
                continue
            key = (ti, round(T.x, 3), round(T.y, 3))
            if key in seen:
                continue
            seen.add(key)
            tr = trails[ti]
            w = max(1.2 * d, 0.75 * B.sp)
            if len(tr) > 3 and tr[0].dist(tr[-1]) < 1e-9:
                # a closed loop: rotate its seam to the far side (half the
                # loop's LENGTH away) so the landing window never straddles it
                m = len(tr) - 1
                cl = [0.0]
                for u, v in zip(tr, tr[1:]):
                    cl.append(cl[-1] + u.dist(v))
                target = (cl[k] - cl[-1] / 2) % cl[-1]
                # (never on a boundary contact: a seam there would make the
                # contact look doubled)
                r0 = min(range(m), key=lambda i: (region.dist(tr[i], 1e-5) < 1e-6,
                                                   abs(cl[i] - target)))
                tr = tr[r0:m] + tr[:r0] + [tr[r0]]
            if _land_at(trails, ti, tr, T, region, B, w):
                return T
    return None


def _land_at(trails, ti, tr, T, region, B, w0):
    """Replace the stretch of trail tr around its nearest point to T by a
    landing ON T: a V (rectilinear) or a smooth curve (serpentine: Hermite
    pieces keeping the trail's own direction where it leaves / rejoins it
    and running along the boundary at T). Wider windows are tried if the
    first does not fit inside the material."""
    cum = [0.0]
    for a, b in zip(tr, tr[1:]):
        cum.append(cum[-1] + a.dist(b))
    best = None
    for k in range(len(tr) - 1):
        a, b = tr[k], tr[k + 1]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        f = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((T.x - a.x) * dx + (T.y - a.y) * dy) / L2))
        d = a.lerp(b, f).dist(T)
        if best is None or d < best[0]:
            best = (d, cum[k] + f * math.sqrt(L2))
    _, sq = best

    def at(s):
        for k in range(len(tr) - 1):
            if cum[k + 1] >= s:
                seg = cum[k + 1] - cum[k]
                return tr[k].lerp(tr[k + 1], (s - cum[k]) / seg if seg > 1e-12 else 0.0), k
        return tr[-1], len(tr) - 2

    def unit(v):
        L = v.length()
        return v * (1.0 / L) if L > 1e-12 else None

    for wf in (1.0, 1.5, 2.0, 0.6):
        w = w0 * wf
        lo, hi = sq - w, sq + w
        if lo < 0 or hi > cum[-1]:
            continue
        A, ka = at(lo)
        C, kc = at(hi)
        # never remove an existing boundary contact (a turn apex or an
        # earlier landing) by replacing the stretch that carries it
        if any(region.dist(tr[j], 1e-5) < 1e-6 for j in range(ka + 1, kc + 1)):
            continue
        shapes = []
        if B.pattern == 'serpentine':
            tA = unit(at(lo + 0.5)[0] - at(lo - 0.5)[0]) if lo > 0.5 else unit(at(lo + 0.5)[0] - A)
            tC = unit(at(hi + 0.5)[0] - at(hi - 0.5)[0]) if hi + 0.5 < cum[-1] else unit(C - at(hi - 0.5)[0])
            tT = _ring_tangent(region, T)
            if tT is not None and tT.x * (C - A).x + tT.y * (C - A).y < 0:
                tT = tT * -1.0
            if tT is None:
                tT = unit(C - A)
            if tA is not None and tC is not None and tT is not None:
                for mag in (1.0, 0.6, 0.35):
                    shapes.append([A] + _hermite(A, tA, T, tT, mag * A.dist(T)) + [T] +
                                  _hermite(T, tT, C, tC, mag * T.dist(C)) + [C])
        shapes.append([A, T, C])            # V (serpentine: last resort)
        others = None
        for new in shapes:
            if not all(_strut_ok(p, q, region) or p.dist(q) < 1e-9 for p, q in zip(new, new[1:])):
                continue
            # no knot: the landing keeps clear of every other bead of infill
            # (its own trail outside the replaced stretch included)
            clear = LAND_CLEAR * B.sp
            if others is None:
                # segments of other infill near this landing (bbox filter)
                xs = [q.x for q in new]
                ys = [q.y for q in new]
                bx0, bx1, by0, by1 = min(xs) - clear, max(xs) + clear, min(ys) - clear, max(ys) + clear
                others = [(u, v) for t in [t for j, t in enumerate(trails) if j != ti] + [tr[:ka + 1], tr[kc + 1:]]
                          for u, v in zip(t, t[1:])
                          if not (max(u.x, v.x) < bx0 or min(u.x, v.x) > bx1 or
                                  max(u.y, v.y) < by0 or min(u.y, v.y) > by1)]
            inner = new[1:-1]
            if any(_seg_d(q, u, v) < clear for q in inner for u, v in others
                   if q.dist(A) > clear and q.dist(C) > clear):
                continue
            trails[ti] = tr[:ka + 1] + new + tr[kc + 1:]
            return True
    return False


def _ring_tangent(region, T):
    """Unit direction of the boundary segment T lies on (None if T is not
    on the boundary)."""
    best = None
    for a, b in region.near(T.x - 1e-4, T.x + 1e-4, T.y - 1e-4, T.y + 1e-4):
        d = _seg_d(T, a, b)
        if d < 1e-5 and (best is None or d < best[0]) and a.dist(b) > 1e-12:
            best = (d, (b - a) * (1.0 / a.dist(b)))
    return best[1] if best else None


def _closest_approach(tr, k, ring, span=2, rr=None):
    """(segment index, point) of trail tr near segment k that is closest
    to the ring (fine sampling + local refinement)."""
    if rr is None:
        rr = _Region([ring], 16.0)

    def dist(q):
        return rr.dist(q, 2.0 * rr.cell)
    best = None
    for j in range(max(0, k - span), min(len(tr) - 1, k + span + 1)):
        a, b = tr[j], tr[j + 1]
        for i in range(33):
            q = a.lerp(b, i / 32)
            d = dist(q)
            if best is None or d < best[0]:
                best = (d, j, i / 32)
    d, j, f = best
    lo, hi = max(0.0, f - 1 / 32), min(1.0, f + 1 / 32)
    a, b = tr[j], tr[j + 1]
    for _ in range(30):
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if dist(a.lerp(b, m1)) < dist(a.lerp(b, m2)):
            hi = m2
        else:
            lo = m1
    return j, a.lerp(b, (lo + hi) / 2)


def _nearest_on_trail(tr, p):
    best = None
    for k in range(len(tr) - 1):
        a, b = tr[k], tr[k + 1]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        f = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
        q = a.lerp(b, f)
        d = q.dist(p)
        if best is None or d < best[0]:
            best = (d, k, q)
    return best[1], best[2]


def _seg_d(p, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    return p.dist(a.lerp(b, t))


def _hermite(p, tp, q, tq, L, n=8):
    """Interior points of a cubic Hermite from p (direction tp) to q
    (direction tq), tangent magnitudes = L."""
    out = []
    for j in range(1, n):
        t = j / n
        t2, t3 = t * t, t * t * t
        h00, h10, h01, h11 = 2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t, -2 * t3 + 3 * t2, t3 - t2
        out.append(Vec2(h00 * p.x + h10 * L * tp.x + h01 * q.x + h11 * L * tq.x,
                        h00 * p.y + h10 * L * tp.y + h01 * q.y + h11 * L * tq.y))
    return out


def _simplify(poly):
    out = [poly[0]]
    for k in range(1, len(poly) - 1):
        if poly[k].dist(out[-1]) < 1e-9:
            continue
        out.append(poly[k])
    if poly[-1].dist(out[-1]) > 1e-9 or len(out) == 1:
        out.append(poly[-1])
    return out
