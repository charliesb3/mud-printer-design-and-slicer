"""
Solid infill — conventional AREA fill of a solid region.

Wall infill (wall_lattice.py) stitches a wall BETWEEN ITS FACES. A solid is
different: a closed area (with voids) filled like a slicer fills it. This
module lays a regular field and makes it STRUCTURALLY ATTACHED to its
boundaries — the outer boundary and every void boundary take part in the
infill graph instead of being clipping masks:

  1. PERIMETERS — the region boundary and every void boundary are the
     first perimeter (they are the printed source paths). `perimeters` > 1
     adds inward copies spaced `perimeter_spacing` apart (the field then
     attaches to the innermost one).
  2. FIELD — parallel lines at `angle`, `spacing` apart (a target),
     clipped EXACTLY to the region (even-odd: voids stay empty).
     Pattern 'rectilinear': straight lines. Pattern 'serpentine': each line is a
     smooth sine (amplitude / wavelength scale with the spacing, one global
     phase so neighbouring lines stay parallel — uniform material), tapered
     to its straight ends at the boundary.
  3. BOUNDARY CONTACT — consecutive lines are joined at the boundary by a
     TURN whose apex lands ON the boundary between them: a V for
     rectilinear, a smooth U for wave. A turn adds degree 2 to the
     perimeter (even), so every turn braces the boundary without creating
     routing defects. Chain ends land on the boundary too. Void loops that
     no line reaches get one turn redirected onto them.
  4. CHAINS → TRAILS — a void splits the field into chains; nearby chain
     ends are joined by the same V / U link (short, at the boundary, never
     a long connector through the middle). What is left is routed with a
     short TRAVEL (the strands are marked `travel_pairing`), never by
     printing the perimeter twice.

Spacing is a target: nothing here is corrected by a tiny extra stitch.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from model import Vec2, _offset_polyline, _trim_offset, _polygon_area, \
    _dedupe_polyline
from infill import _Region, _strut_ok

PATTERNS = {
    'rectilinear': 'Parallel lines at an angle; V turns brace the boundary',
    'serpentine': 'Smooth parallel waves (serpentine); U turns brace the boundary',
}
PARAMETERS = [
    {'name': 'spacing', 'label': 'Spacing', 'default': 20.0, 'min': 2.0, 'max': 120.0, 'step': 1.0},
    {'name': 'angle', 'label': 'Angle', 'default': 45.0, 'min': -90.0, 'max': 180.0, 'step': 5.0},
    {'name': 'perimeters', 'label': 'Perimeters', 'default': 1, 'min': 1, 'max': 4, 'step': 1},
]
TURN_BACK = 0.5         # × spacing: a turn starts this far before the boundary
LINK_MAX = 2.0          # × spacing: longest V / U link between chain ends
JOIN_MAX = 4.0          # × spacing: longest boundary stretch a turn may bridge
PERIMETER_SPACING = 6.0  # default spacing of extra perimeters (in)
WAVE_AMP = 0.22         # × spacing: wave amplitude (gap stays ≥ 0.56 × spacing)
WAVE_LEN = 2.5          # × spacing: wavelength


@dataclass
class SolidPlan:
    lines: list = field(default_factory=list)        # trail polylines [[Vec2]]
    connectors: list = field(default_factory=list)   # (kept for the API; links are in lines)
    perimeters: list = field(default_factory=list)   # extra perimeter rings
    trails: int = 0
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


def _hatch(rings, spacing, angle):
    """Parallel segments clipped EXACTLY to the region (end points lie on
    the ring segments they hit). [_Line] with a → b along the direction."""
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
        t = k * spacing + spacing / 2 + 1e-7          # never exactly on a vertex
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

    # -- line bodies ---------------------------------------------------------
    def body(self, p, q):
        """Points strictly between p and q along a field line."""
        if self.pattern != 'serpentine':
            return []
        L = p.dist(q)
        lam = WAVE_LEN * self.sp
        A = WAVE_AMP * self.sp
        if L < 0.5 * lam:
            return []
        d = (q - p) * (1.0 / L)
        nrm = Vec2(-self.u[1], self.u[0])
        n = max(4, int(L / (lam / 12)))
        taper = 0.5 * lam
        out = []
        for j in range(1, n):
            t = L * j / n
            c = p + d * t
            # one global phase (absolute position along the field direction)
            ph = 2 * math.pi * (c.x * self.u[0] + c.y * self.u[1]) / lam
            w = min(1.0, t / taper, (L - t) / taper)
            w = w * w * (3 - 2 * w)                    # smoothstep taper
            out.append(c + nrm * (A * w * math.sin(ph)))
        pts = [p] + out + [q]
        if all(_strut_ok(a, b, self.region) for a, b in zip(pts, pts[1:]) if a.dist(b) > 1e-9):
            return out
        return []                                      # too tight here: straight

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
                # wave: a smooth U where the lines are long enough to carry
                # it; short lines (narrow places) turn with a plain V
                smooth = self.pattern == 'serpentine' and short >= WAVE_LEN * self.sp
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
            pts += B.body(p, q)
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

    sp.lines = [_simplify(t) for t in trails if len(t) >= 2]
    sp.trails = len(sp.lines)
    sp.report = {'lines': len(lines), 'chains': len(chains), 'links': len(links) // 2,
                 'trails': sp.trails, 'connectors': len(links) // 2,
                 'max_connector': round(B.max_leg, 3),
                 'untouched_boundaries': len(hatch_rings) - len(touched),
                 'pattern': pattern}
    return sp


def _simplify(poly):
    out = [poly[0]]
    for k in range(1, len(poly) - 1):
        if poly[k].dist(out[-1]) < 1e-9:
            continue
        out.append(poly[k])
    if poly[-1].dist(out[-1]) > 1e-9 or len(out) == 1:
        out.append(poly[-1])
    return out
