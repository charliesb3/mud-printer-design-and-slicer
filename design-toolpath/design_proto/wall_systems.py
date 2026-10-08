"""
WALL SYSTEMS (2026-10-07, V1) — how a thick wall's ARCHITECTURAL ENVELOPE
(Wall Thickness: its two face lines) is filled with bead paths.

  single       SINGLE / OUT-AND-BACK wall: the ordinary single-line wall (one
               bead; with Physical rules the separated outbound + return lanes)
               — the existing bead machinery, not handled here
  hollow       HOLLOW / SKINS ONLY: the two skins, no web — not handled here
  skin_web     two printed skins + a Wall Infill web (zigzag / wave /
               adaptive truss) — the existing behaviour, not handled here
  parallel     PARALLEL WALLS: N longitudinal walls evenly spread across the
               thickness (outermost half a bead inside the faces); corners and
               wall ends as Interleaved / Linked (lanes, canonical end motif)
  interleaved  INTERLEAVED WAVES: N full-depth oscillating paths, one
               waveform and period, each phase-shifted by period / N; their
               extrema together form the wall faces. No skins.
  linked       LINKED WAVES: N paths distributed across the thickness,
               neighbours in antiphase, spaced so a crest of one meets the
               trough of the next (auto amplitude: centrelines touch; more
               amplitude interlocks deeper): an interference-like chain of cells.
  chain        CHAINED LOOP (provisional): ONE continuous path progressing along
               the wall, making a loop on the + side, crossing the centre, a
               loop on the − side, … — each loop self-intersects once at its
               neck; upper and lower loops are staggered by one pitch:
                   x(u) = a·u + w·sin 2u,   y(u) = D·sgn(sin u)·|sin u|^q
               (a = pitch / π; w > a/2 makes the path double back near each
               crest, and since u and π − u share a height it crosses its own
               earlier strand exactly once — the neck; q moves the neck).

The paths follow the wall in WALL-RELATIVE (strip) coordinates on the
production chordal-axis runs (wall_lattice.skeleton / _Geo): s along the run,
y across it between the faces — so curves are followed, and an opening
(material removed) simply ends the runs at its caps. Every bead centreline
keeps half a bead off the envelope (the bead's edge stays inside).

CORNERS (interleaved / linked; same principle as the Zigzag / Wave lattice):
a corner is a HARD anchor. Each span between anchors gets a whole number of
periods (nearest the Period), its phase starts at the same value at every
corner, and the oscillation fades into fixed LANES (each path's own value at
that phase) which turn the corner as mitred offset lines. Equivalent corners
therefore look identical whatever the period or the span lengths. The chain
follows corners wall-relatively (V1). No junction motifs yet.
"""
from __future__ import annotations

import bisect
import math

import wall_lattice as WL
from model import Vec2

SYSTEMS = ('single', 'hollow', 'skin_web', 'parallel', 'interleaved', 'linked', 'chain')
LABELS = {'single': 'Single / Out-and-Back Wall', 'hollow': 'Hollow / Skins Only', 'skin_web': 'Skin + Web',
          'parallel': 'Parallel Walls', 'interleaved': 'Interleaved Waves', 'linked': 'Linked Waves',
          'chain': 'Chained Loop'}
GENERATED = ('parallel', 'interleaved', 'linked', 'chain')     # paths generated here (else: skins / beads)
DEFAULTS = {
    'parallel': {'walls': 3},
    'interleaved': {'pattern': 'wave', 'paths': 3, 'period': 30.0, 'depth': 0.0},
    'linked': {'pattern': 'wave', 'paths': 3, 'period': 24.0, 'amplitude': 0.0},
    'chain': {'pitch': 0.0, 'loop_depth': 0.0, 'loop_width': 0.0, 'neck': 0.0, 'phase': 0.0},
}
LIMITS = {'paths': (1, 12), 'period': (2.0, 600.0)}
CORNER_TURN = math.radians(30.0)     # as wall_lattice: turn within 2 t …
CORNER_LOCAL = 0.75                  # … concentrated (3 t vs 6 t): a corner, not a curve
CORNER_ZONE = 0.6                    # × thickness either side of a corner: the mitred lanes
FRAME_STEP = 0.5                     # in: the cached strip frame's resolution
CHAIN_NECK = 0.12                    # chain: default neck height / loop depth (0 → this)
# chain: smallest pitch. The loop geometry is scale-free; below ~0.1 in the
# samples per loop (≥ 24) and the router's point tolerances are the limit.
CHAIN_PITCH_MIN = 0.1
CHAIN_MAX_POINTS = 60000             # sampling budget of one run's chain
PARALLEL_SPAN = 60.0                 # parallel walls: span length unit (only sets the end fade length)


def normalize(system):
    """A wall system dict → its type and complete parameters (None for
    Skin + Web / anything unknown)."""
    if not isinstance(system, dict):
        return None
    t = system.get('type')
    if t not in DEFAULTS:
        return None
    if t == 'chain' and 'pitch' not in system and system.get('period'):
        system = dict(system, pitch=0.5 * float(system['period']))   # (the earlier ∞ period: two lobes)
    out = {'type': t}
    for k, v in DEFAULTS[t].items():
        x = system.get(k, v)
        if k == 'pattern':
            out[k] = x if x in ('wave', 'zigzag') else 'wave'
        elif k in ('paths', 'walls'):
            try:
                out[k] = int(round(float(x)))
            except (TypeError, ValueError):
                out[k] = v
            lo, hi = LIMITS['paths']
            out[k] = max(lo if t != 'linked' else 2, min(hi, out[k]))
        else:
            try:
                out[k] = max(0.0, float(x))
            except (TypeError, ValueError):
                out[k] = v
    if out.get('period'):
        out['period'] = max(LIMITS['period'][0], min(LIMITS['period'][1], out['period']))
    if t == 'parallel':
        out.update({'paths': out['walls'], 'pattern': 'wave', 'period': PARALLEL_SPAN})
    if t == 'chain':
        if out['pitch']:
            out['pitch'] = max(CHAIN_PITCH_MIN, min(LIMITS['period'][1], out['pitch']))
        out['neck'] = min(0.9, out['neck'])
        out['phase'] = out['phase'] % 1.0
    return out


def _wave(pattern, phase):
    """Unit waveform of a phase (radians): sine, or the triangle wave with
    the same extrema (zigzag)."""
    if pattern == 'zigzag':
        return (2.0 / math.pi) * math.asin(max(-1.0, min(1.0, math.sin(phase))))
    return math.sin(phase)


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class _Strip:
    """One wall run in strip coordinates; the face correspondence is
    sampled once (FRAME_STEP) and interpolated."""

    def __init__(self, geo, run, bead, thick, env=None):
        self.geo, self.run, self.bead, self.thick, self.env = geo, run, bead, thick, env
        L = run.length
        self.L = L
        n = max(2, int(math.ceil(L / FRAME_STEP)))
        self.ss = [L * k / n for k in range(n + 1)]
        self.tab = [(geo.side_point(run, 0, s), geo.side_point(run, 1, s)) for s in self.ss]
        if run.cycle:
            self.lo, self.hi = 0.0, L
        else:
            self.lo = 0.0 if run.a[0] == 'J' else self._back(min(0.5 * thick, 0.25 * L), 0.25 * thick)
            self.hi = L if run.b[0] == 'J' else self._back(L - min(0.5 * thick, 0.25 * L), -0.25 * thick)
            # a FREE END's station must be a square, full-width cross-section
            # (a round cap's arc starts there): inside the cap the two face
            # points lie at different depths and the lanes would end askew
            # — searched FROM THE TIP inward, so the station never lands past a
            # corner that lies close behind the cut (that end would otherwise
            # be judged degenerate and lose its canonical motif)
            if run.a[0] == 'E':
                self.lo = self._first_square(0.0, 1.0, self.lo)
            if run.b[0] == 'E':
                self.hi = self._first_square(L, -1.0, self.hi)

    def full_width(self):
        """The run's full wall thickness (median section: corners, whose
        sections are wider, and caps, narrower, do not bias it)."""
        widths = sorted(a.dist(b) for a, b in self.tab)
        return widths[len(widths) // 2]

    def is_square(self, s):
        """A square, full-width cross-section at s (a clean wall section):
        judged on the FACES themselves — the section is perpendicular to both
        face tangents. (The skeleton centre line can run askew near a free
        end on a short leg, so it is not used.)"""
        f0, f1 = self.faces(s)
        d = f1 - f0
        if d.length() < 1e-9 or abs(d.length() - self.full_width()) > 0.03 * self.thick:
            return False
        for side in (0, 1):
            a = self.faces(min(self.L, s + 0.25))[side] - self.faces(max(0.0, s - 0.25))[side]
            if a.length() < 1e-9 or abs(d.x * a.x + d.y * a.y) / (d.length() * a.length()) > 0.03:
                return False
        return True

    def _first_square(self, tip, sign, fallback):
        """The first square section walking from the run's free end (tip)
        inward (≤ 1.25 t, ≤ half the run); else the fallback station."""
        step = 0.05 * self.thick
        k_max = int(min(1.25 * self.thick, 0.5 * self.L) / step)
        # from half a thickness in: the nested returns (≤ t/2 behind the cap)
        # must lie beyond the lane ends
        k_min = max(1, int(min(0.5 * self.thick, 0.25 * self.L) / step))
        for k in range(k_min, k_max + 1):
            s = tip + sign * k * step
            if self.is_square(s) and self._lanes_clear(s) and \
                    all(self.is_square(s + sign * f * self.thick) for f in (0.1, 0.25)):
                return s                     # (a stable straight stretch follows)
        return fallback

    def _lanes_clear(self, s):
        """Every lane of the section keeps half a bead off the RESOLVED
        envelope there (the cap included) — no lane would be pulled in."""
        if self.env is None:
            return True
        m, u, H = self.frame(s)
        return all(self.env.clear(m + u * y, 0.5 * self.bead - 0.02) for y in (-H, 0.0, H))

    def _back(self, s, step):
        for _ in range(12):
            if self.geo.perpendicular(self.run, s):
                return s
            s += step
        return s

    def faces(self, s):
        if self.run.cycle:
            s %= self.L
        s = max(0.0, min(self.L, s))
        k = min(len(self.ss) - 2, max(0, bisect.bisect_right(self.ss, s) - 1))
        f = (s - self.ss[k]) / (self.ss[k + 1] - self.ss[k])
        (a0, a1), (b0, b1) = self.tab[k], self.tab[k + 1]
        return a0.lerp(b0, f), a1.lerp(b1, f)

    def frame(self, s):
        """(mid point, unit across face0 → face1, half band H): H is how far
        a bead CENTRELINE may go off the middle (half a bead short of each
        face)."""
        f0, f1 = self.faces(s)
        d = f1 - f0
        T = d.length()
        u = d * (1.0 / T) if T > 1e-9 else Vec2(0.0, 0.0)
        return f0.lerp(f1, 0.5), u, max(0.0, 0.5 * T - 0.5 * self.bead)

    def at(self, s, y):
        m, u, H = self.frame(s)
        y = max(-H, min(H, y))
        q = m + u * y
        if self.env is not None and abs(y) > 1e-9 and not self.env.clear(q, 0.5 * self.bead - 0.02):
            # the strip frame is only approximate at a sharp corner: keep the
            # bead half a bead inside the RESOLVED faces. The whole side of the
            # cross-section is scaled by ONE factor (from its extreme point), so
            # neighbouring lanes keep their order — they never cross
            q = m + u * (y * self._side_scale(s, m, u, H, 1.0 if y > 0 else -1.0))
        return q

    def _side_scale(self, s, m, u, H, sign):
        key = (round(s, 4), sign)
        cache = self.__dict__.setdefault('_scales', {})
        if key in cache:
            return cache[key]
        lo_, hi_ = 0.0, 1.0
        if self.env.clear(m + u * (sign * H), 0.5 * self.bead - 0.02):
            lo_ = 1.0
        else:
            for _ in range(12):
                mid = 0.5 * (lo_ + hi_)
                if self.env.clear(m + u * (sign * H * mid), 0.5 * self.bead - 0.02):
                    lo_ = mid
                else:
                    hi_ = mid
        cache[key] = lo_
        return lo_

    def half(self, s):
        return self.frame(s)[2]

    def centre(self, s):
        return self.geo.centre(self.run, s % self.L if self.run.cycle else s)


def _samples(lo, hi, step):
    n = max(2, int(math.ceil((hi - lo) / step)))
    return [lo + (hi - lo) * k / n for k in range(n + 1)]


# ---------------------------------------------------------------------------
# corners (same rule as the Zigzag / Wave lattice)
# ---------------------------------------------------------------------------

def _corners(st):
    """[(s, t_in, t_out)] corners of a run: the centre line turns ≥ 30°
    within 2 t, concentrated (≥ CORNER_LOCAL of its 6 t turn within 3 t),
    with room for the corner zone and its fade on both sides."""
    run, L, th = st.run, st.L, st.thick
    d = max(0.5, 0.25 * th)
    n = max(4, int(L / d))
    ss = [L * k / n for k in range(n + 1)]

    def ang(x):
        if not run.cycle:
            x = max(0.0, min(L, x))
        a = st.centre(x - 0.5 * th) if (run.cycle or x - 0.5 * th > 0) else st.centre(0.0)
        b = st.centre(x + 0.5 * th) if (run.cycle or x + 0.5 * th < L) else st.centre(L)
        return math.atan2(b.y - a.y, b.x - a.x)

    def turn(x, w):
        t = ang(x + w / 2) - ang(x - w / 2)
        return abs((t + math.pi) % (2 * math.pi) - math.pi)
    W = 2.0 * th
    tv = [turn(x, W) for x in ss]
    lo, hi = st.lo, st.hi
    out = []
    wn = int(W / d)
    for k, x in enumerate(ss):
        if tv[k] < CORNER_TURN:
            continue
        if not run.cycle and (x < lo + 1.5 * th or x > hi - 1.5 * th):
            continue
        if run.cycle and k == len(ss) - 1:
            continue
        m = len(ss) - 1
        nb = [tv[j % m] for j in range(k - wn, k + wn + 1)] if run.cycle else \
            [tv[j] for j in range(max(0, k - wn), min(len(ss), k + wn + 1)) if lo <= ss[j] <= hi]
        if tv[k] + 1e-12 < max(nb):
            continue
        if (run.cycle or (x - 3 * th > 0 and x + 3 * th < L)) and turn(x, 3 * th) < CORNER_LOCAL * turn(x, 6 * th):
            continue
        if out and abs(x - out[-1]) < 2.5 * th:
            continue
        out.append(x)
    if run.cycle and len(out) > 1 and out[0] + L - out[-1] < 2.5 * th:
        out.pop()
    res = []
    for x in out:
        a1, a2 = ang(x - 1.5 * th), ang(x + 1.5 * th)
        res.append((x, Vec2(math.cos(a1), math.sin(a1)), Vec2(math.cos(a2), math.sin(a2))))
    return res


def _miter(A, ta, B, tb):
    """Intersection of the line through A along ta with the line through B
    along tb (their midpoint if nearly parallel)."""
    den = ta.x * tb.y - ta.y * tb.x
    if abs(den) < 1e-6:
        return A.lerp(B, 0.5)
    w = B - A
    u = (w.x * tb.y - w.y * tb.x) / den
    return A + ta * u


# ---------------------------------------------------------------------------
# the resolved envelope: clearance tests and END CAP rails
# ---------------------------------------------------------------------------

class _Envelope:
    """The region's rings (faces, caps, opening faces, junction fillets —
    the fully resolved wall geometry) for clearance tests and cap rails."""

    def __init__(self, rings, bead):
        from infill import _Region
        self.rings, self.bead = rings, bead
        self.region = _Region(rings, 4.0)

    def clear(self, q, margin):
        """q inside the material, at least `margin` from every face line."""
        return self.region.inside(q) and self.region.dist(q, margin) >= margin - 1e-6

    def _nearest(self, q):
        best = None
        for ri, r in enumerate(self.rings):
            n = len(r)
            for k in range(n):
                a, b = r[k], r[(k + 1) % n]
                d = b - a
                L2 = d.x * d.x + d.y * d.y
                t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((q.x - a.x) * d.x + (q.y - a.y) * d.y) / L2))
                dd = (a + d * t).dist(q)
                if best is None or dd < best[0]:
                    best = (dd, ri, k, t)
        return best

    def cap_rail(self, f0, f1, toward=None, inset=None):
        """The END CAP between the two face points of a wall end — the
        shorter ring arc from f0 to f1, i.e. the very cap / opening face the
        Skin + Web prints there (flat or round, as resolved) — inset by half
        a bead into the material (or by `inset`: the nested returns inside
        it run parallel to it). Ordered from f0's side to f1's side."""
        n0, n1 = self._nearest(f0), self._nearest(f1)
        if n0 is None or n1 is None or n0[1] != n1[1]:
            return None
        r = self.rings[n0[1]]
        n = len(r)
        P = lambda k, t: r[k].lerp(r[(k + 1) % n], t)

        def forward(a, b):
            """Arc from ring position a = (k, t) to b, increasing index."""
            pts = [P(*a)]
            k = a[0]
            if not (b[0] == a[0] and b[1] >= a[1]):
                for _ in range(n):
                    k = (k + 1) % n
                    pts.append(r[k])
                    if k == b[0]:
                        break
            pts.append(P(*b))
            return pts
        a, b = (n0[2], n0[3]), (n1[2], n1[3])
        fwd = forward(a, b)
        bwd = list(reversed(forward(b, a)))
        length = lambda pl: sum(u.dist(v) for u, v in zip(pl, pl[1:]))
        arc = fwd if length(fwd) <= length(bwd) else bwd
        arc = [q for i, q in enumerate(arc) if i == 0 or q.dist(arc[i - 1]) > 1e-6]
        if len(arc) < 2:
            return None
        h = 0.5 * self.bead if inset is None else inset

        def seg_normal(u, v):
            d = v - u
            L = d.length()
            nrm = Vec2(-d.y / L, d.x / L)
            m = u.lerp(v, 0.5)
            return nrm if self.region.inside(m + nrm * 0.3) else nrm * -1.0
        norms = [seg_normal(u, v) for u, v in zip(arc, arc[1:])]
        out = []
        for i, q in enumerate(arc):
            if i == 0:
                nb, k = norms[0], 1.0
            elif i == len(arc) - 1:
                nb, k = norms[-1], 1.0
            else:
                n1_, n2_ = norms[i - 1], norms[i]
                nb = n1_ + n2_
                nb = nb * (1.0 / nb.length()) if nb.length() > 1e-9 else n1_
                k = 1.0 / max(0.5, nb.x * n1_.x + nb.y * n1_.y)       # true offset at a cap corner
            p_ = q + nb * (h * k)
            # a local inset can come too close to ANOTHER part of the boundary
            # (a cap meeting a corner in a concave notch): push it further in
            c_ = toward if toward is not None else f0.lerp(f1, 0.5)
            steps = 0
            while not self.clear(p_, h - 0.02) and steps < 60:
                d_ = c_ - p_
                if d_.length() < 0.1:
                    break
                p_ = p_ + d_ * (0.1 / d_.length())         # towards the middle of the wall end
                steps += 1
            out.append(p_)
        out = _unloop(out)
        return out if len(out) >= 2 else None


def _unloop(pl):
    """Remove self-crossing loops from a polyline (an inset round a concave
    notch can fold over itself): where segment i crosses a later segment j,
    the vertices between are replaced by the crossing point."""
    pl = list(pl)
    changed = True
    while changed and len(pl) > 3:
        changed = False
        for i in range(len(pl) - 1):
            for j in range(len(pl) - 2, i + 1, -1):
                t = _cross(pl[i], pl[i + 1], pl[j], pl[j + 1])
                if t is None or not (1e-9 < t < 1 - 1e-9):
                    continue
                u = _cross(pl[j], pl[j + 1], pl[i], pl[i + 1])
                if u is None or not (1e-9 < u < 1 - 1e-9):
                    continue
                X = pl[i].lerp(pl[i + 1], t)
                pl = pl[:i + 1] + [X] + pl[j + 1:]
                changed = True
                break
            if changed:
                break
    return pl


def _uturn(A, B, out, depth, n=10):
    """A U-turn from A to B bulging `depth` along `out` (half ellipse)."""
    m = A.lerp(B, 0.5)
    half = B - m
    return [m - half * math.cos(math.pi * k / n) + out * (depth * math.sin(math.pi * k / n)) for k in range(n + 1)]


def _join(pieces):
    """Join polylines that share exact end points into maximal chains
    (closed where they close)."""
    key = lambda p: (round(p.x, 6), round(p.y, 6))
    pieces = [list(p) for p in pieces if len(p) >= 2]
    ends = {}
    for i, p in enumerate(pieces):
        ends.setdefault(key(p[0]), []).append((i, 0))
        ends.setdefault(key(p[-1]), []).append((i, 1))
    used, out = set(), []
    for i in range(len(pieces)):
        if i in used:
            continue
        # start from a free end if this chain has one
        chain_start = i
        used.add(i)
        line = list(pieces[i])
        for direction in (1, 0):
            while True:
                tip = line[-1] if direction == 1 else line[0]
                nxt = [(j, e) for j, e in ends.get(key(tip), []) if j not in used]
                if not nxt:
                    break
                j, e = nxt[0]
                used.add(j)
                seg = pieces[j] if e == 0 else list(reversed(pieces[j]))
                if direction == 1:
                    line += seg[1:]
                else:
                    line = list(reversed(seg))[:-1] + line
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def _wave_paths(st, sp, H0, env):
    """Interleaved / linked paths of one run: spans between anchors (ends,
    corners) with whole periods, phase anchored at the corners, fading into
    lanes that turn each corner mitred."""
    t, N, pat = sp['type'], sp['paths'], sp['pattern']
    th = st.thick
    cs = _corners(st)
    g = CORNER_ZONE * th
    L = st.L
    if st.run.cycle:
        if cs:
            pts = [c[0] for c in cs]
            spans = [(pts[i] + g, (pts[i + 1] if i + 1 < len(pts) else pts[0] + L) - g, True,
                      cs[(i + 1) % len(cs)]) for i in range(len(pts))]
        else:
            spans = [(0.0, L, False, None)]
    else:
        pts = [c[0] for c in cs]
        b_ = [st.lo] + pts + [st.hi]
        spans = [((b_[i] + g) if i > 0 else b_[i], (b_[i + 1] - g) if i + 1 < len(b_) - 1 else b_[i + 1],
                  i > 0, cs[i] if i + 1 < len(b_) - 1 else None) for i in range(len(b_) - 1)]
    # a FREE wall end (cap / opening jamb) is an anchor too: the strands fade
    # into their lanes there, so the cap turnarounds join them cleanly
    e_lo = (not st.run.cycle) and st.run.a[0] == 'E'
    e_hi = (not st.run.cycle) and st.run.b[0] == 'E'
    # the corner phase: lanes must be DISTINCT — sin repeats for angles
    # symmetric about 90°, i.e. phases π/2 − mπ/N; take the one half way
    phi0 = (math.pi / 2 - math.pi / (2 * N)) if t == 'interleaved' else 0.0

    def value(k, phase, H):
        if t == 'parallel':                      # N evenly spread longitudinal walls
            return 0.0 if N < 2 else -H + k * (2.0 * H / (N - 1))
        if t == 'interleaved':
            A = min(sp['depth'], H) if sp['depth'] > 0 else H
            return A * _wave(pat, phase + 2 * math.pi * k / N)
        a = min(sp['amplitude'], H) if sp['amplitude'] > 0 else H / N
        d = 2.0 * (H - a) / (N - 1) if N > 1 else 0.0
        return (k - (N - 1) / 2.0) * d + a * _wave(pat, phase + k * math.pi)

    def lane(k, H):
        return value(k, phi0, H)

    # END LANES (canonical end motif): at a free end the strands settle into
    # N evenly spaced lanes, the outermost two half a bead inside the faces;
    # the strand of rank r (across order at the anchor phase) takes lane r at
    # the lo end and lane perm[r] at the hi end (see _end_perm)
    rank = {k: r for r, k in enumerate(sorted(range(N), key=lambda k: value(k, phi0, max(H0, 1.0))))}
    # (Parallel Walls: NO permutation — straight lanes must never cross; their
    # nested loops are joined by rungs instead, see _parallel_rungs)
    perm = _end_perm(N) if (e_lo and e_hi and t != 'parallel') else list(range(N))
    end_lane = {'lo': {k: rank[k] for k in range(N)}, 'hi': {k: perm[rank[k]] for k in range(N)}}

    def end_y(r, H):
        return 0.0 if N < 2 else -H + r * (2.0 * H / (N - 1))

    polys = [[] for _ in range(N)]
    info = {'periods': [], 'corners': len(cs)}
    for a, b, ca, corner in spans:
        ln = b - a
        if ln <= 1e-6:
            continue
        n = max(1, round(ln / sp['period']))
        P = ln / n
        info['periods'].append(round(P, 3))
        F = min(0.35 * P, 0.5 * ln)          # the fade into the corner lanes
        step = max(0.25, min(1.0, P / 32))
        free_a = e_lo and not ca and abs(a - st.lo) < 1e-9
        free_b = e_hi and corner is None and abs(b - st.hi) < 1e-9
        fa = ca or free_a
        fb = corner is not None or free_b
        for s in _samples(a, b, step):
            H = st.half(s)
            ph = phi0 + 2 * math.pi * (s - a) / P
            wa = _smooth((s - a) / F) if fa else 1.0
            wb = _smooth((b - s) / F) if fb else 1.0
            w = min(wa, wb)
            near_a = wa <= wb
            for k in range(N):
                if near_a:
                    lk = end_y(end_lane['lo'][k], H) if free_a else lane(k, H)
                else:
                    lk = end_y(end_lane['hi'][k], H) if free_b else lane(k, H)
                q = st.at(s, lk + (value(k, ph, H) - lk) * w)
                if not polys[k] or polys[k][-1].dist(q) > 1e-9:
                    polys[k].append(q)
        if corner is not None:
            # the corner after this span. A SHARP corner: every lane straight
            # to its mitre point. Where a mitre would leave the resolved
            # envelope (Corner R, a rounded junction fillet) or crowd a face,
            # the lanes follow the resolved wall through the corner instead
            # (concentric with a rounded corner).
            nxt = b + 2 * g
            mits = []
            for k in range(N):
                A = st.at(b, lane(k, st.half(b)))
                B = st.at(nxt, lane(k, st.half(nxt)))
                mits.append(_miter(A, corner[1], B, corner[2]))
            if all(env.clear(M, 0.5 * st.bead - 0.05) for M in mits):
                for k in range(N):
                    polys[k].append(mits[k])
                kind = 'mitre'
                pts_ = mits
            else:
                kind = 'follow'
                pts_ = []
                zs = _samples(b, nxt, 0.5)[1:-1]
                for k in range(N):
                    lane_pts = [st.at(z, lane(k, st.half(z))) for z in zs]
                    polys[k] += lane_pts
                    pts_.append(lane_pts[len(lane_pts) // 2])
            info.setdefault('corner_kinds', []).append(kind)
            info.setdefault('corner_points', []).append([(round(M.x, 4), round(M.y, 4)) for M in pts_])
    if st.run.cycle and cs:
        for pl in polys:
            if pl:
                pl.append(pl[0])
    # END CAPS (canonical end motif, the same at a source end and at an
    # opening jamb): the outermost lanes are joined by the resolved cap
    # itself (flat / round), every inner pair returns nested inside it,
    # parallel to it. With the hi end's lane permutation the run prints as
    # ONE closed circuit for an even number of paths (one open path for odd).
    if not st.run.cycle and (e_lo or e_hi):
        conns = _cap_turns(st, polys, e_lo, e_hi, env, N, end_lane)
        polys = _join(polys + conns)
        info['cap_turns'] = len(conns)
    if t == 'parallel' and N > 1 and (st.run.cycle or (e_lo and e_hi)):
        polys, info['transitions'] = _parallel_lanes(st, polys, N, spans, end_y, sp.get('seam_hint'))
    if not st.run.cycle and (e_lo or e_hi):
        info['closed'] = all(len(pl) > 2 and pl[0].dist(pl[-1]) < 1e-6 for pl in polys)
    info['period'] = info['periods'][0] if info['periods'] else None
    if t == 'parallel':
        info['spacing'] = round(2.0 * H0 / (N - 1), 3) if N > 1 else 0.0
    elif t == 'linked':
        a = min(sp['amplitude'], H0) if sp['amplitude'] > 0 else H0 / N
        d = 2.0 * (H0 - a) / (N - 1) if N > 1 else 0.0
        info.update({'amplitude': round(a, 3), 'spacing': round(d, 3), 'closest_approach': round(d - 2 * a, 3)})
    else:
        info['amplitude'] = round(min(sp['depth'], H0) if sp['depth'] > 0 else H0, 3)
    return polys, info


def _cut(pl, P1, P2):
    """Polyline pl with the stretch between its points nearest P1 and P2
    removed (the shorter way round a closed one): [pieces], each ending
    exactly at the projected cut points, and those points (Q1, Q2)."""
    cum = [0.0]
    for u, v in zip(pl, pl[1:]):
        cum.append(cum[-1] + u.dist(v))

    def near(P):
        bb = None
        for j, (a, b) in enumerate(zip(pl, pl[1:])):
            d = b - a
            L2 = d.x * d.x + d.y * d.y
            t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((P.x - a.x) * d.x + (P.y - a.y) * d.y) / L2))
            q = a + d * t
            if bb is None or q.dist(P) < bb[0]:
                bb = (q.dist(P), cum[j] + t * (cum[j + 1] - cum[j]), q)
        return bb
    n1, n2 = near(P1), near(P2)
    L = cum[-1]
    closed = pl[0].dist(pl[-1]) < 1e-6
    (lo, Qlo), (hi, Qhi) = sorted([(n1[1], n1[2]), (n2[1], n2[2])], key=lambda x: x[0])
    if closed and hi - lo <= L - (hi - lo):
        pieces = [_sub(pl, cum, hi, L) + _sub(pl, cum, 0.0, lo)[1:]]
        pieces[0][0], pieces[0][-1] = Qhi, Qlo
    elif closed:
        pieces = [_sub(pl, cum, lo, hi)]
        pieces[0][0], pieces[0][-1] = Qlo, Qhi
    else:
        pieces = [x for x in (_sub(pl, cum, 0.0, lo), _sub(pl, cum, hi, L))
                  if sum(u.dist(v) for u, v in zip(x, x[1:])) > 1e-9]
        for x in pieces:
            if x[-1].dist(Qlo) < 1e-9:
                x[-1] = Qlo
            if x[0].dist(Qhi) < 1e-9:
                x[0] = Qhi
    return pieces, (n1[2], n2[2]), max(n1[0], n2[0])


def _insert_vertex(pl, P):
    """pl with P inserted as a vertex on its nearest segment (index of P)."""
    best = None
    for j, (a, b) in enumerate(zip(pl, pl[1:])):
        d = b - a
        L2 = d.x * d.x + d.y * d.y
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((P.x - a.x) * d.x + (P.y - a.y) * d.y) / L2))
        dd = (a + d * t).dist(P)
        if best is None or dd < best[0]:
            best = (dd, j, t)
    _, j, t = best
    if t <= 1e-9:
        pl = pl[:j] + [P] + pl[j + 1:]
        return pl, j
    if t >= 1 - 1e-9:
        pl = pl[:j + 1] + [P] + pl[j + 2:]
        return pl, j + 1
    return pl[:j + 1] + [P] + pl[j + 1:], j + 1


def _rotate_closed(pl, i):
    """A closed polyline (pl[0] == pl[-1]) restarted at vertex i."""
    core = pl[:-1]
    r = core[i:] + core[:i]
    return r + [r[0]]


def _parallel_lanes(st, polys, N, spans, end_y, hint=None):
    """PARALLEL WALLS route LANE BY LANE (designer decision, 2026-10-07): each
    lane stays one clean, continuous architectural path; all lane changes are
    concentrated in ONE seam zone (a short window [a − d, a] along the wall):

        lane 1 (complete) → step → lane 2 → step → … → lane K → return → lane 1

    Every lane is cut once over the window; lane k's end steps diagonally to
    lane k+1's start (the steps are parallel, they never cross each other);
    one RETURN diagonal from the last lane back to lane 1's start closes the
    route and crosses the steps — a crossing, never a retrace. Lanes: on a
    closed ring every lane (all closed loops); with two free ends the lanes on
    the f0 side (each is part of a nested end loop — the canonical caps stay;
    an odd middle lane is cut too and the route stays open, by parity).
    The seam zone sits at `hint` (the nearest route origin / start the user
    placed) when one lies on this wall, else in the middle of the longest
    straight span. Replaces the distributed interior-lane kisses."""
    if not spans:
        return polys, 0
    K = (N - 1) if st.run.cycle else (N + 1) // 2 - 1
    if K <= 0:
        return polys, 0
    # the window: as compact as practical (lanes run almost to their
    # continuation; the steps are nearly transverse and may cross)
    d = 0.5 * st.bead
    a, b = max(((x[0], x[1]) for x in spans), key=lambda x: x[1] - x[0])
    if b - a < 2.0 * d:
        return polys, 0
    s_seam = 0.5 * (a + b) + 0.5 * d
    if hint:
        # the user's start: the seam zone moves there (when it lies on this wall)
        best = None
        for x0, x1 in ((x[0], x[1]) for x in spans):
            for z in _samples(x0 + d, x1, 1.0):
                c = st.centre(z)
                dd = min(c.dist(hq) for hq in hint)
                if best is None or dd < best[0]:
                    best = (dd, z)
        if best is not None and best[0] < 1.5 * st.thick:
            s_seam = best[1]
    lanes = list(range(K + 1))
    ends = []
    for j in lanes:
        A = st.at(s_seam - d, end_y(j, st.half(s_seam - d)))
        B = st.at(s_seam, end_y(j, st.half(s_seam)))
        k = min(range(len(polys)), key=lambda i: min(q.dist(A) for q in polys[i]))
        pieces, (QA, QB), err = _cut(polys[k], A, B)
        if err > 0.05:
            return polys, 0
        ends.append((QA, QB))
        polys = polys[:k] + polys[k + 1:] + pieces
    links = [[ends[j][0], ends[j + 1][1]] for j in range(K)]         # steps: lane j → lane j+1
    links.append([ends[K][0], ends[0][1]])                          # the return (crosses the steps)
    return _join(polys + links), K


def _end_frame(st, end):
    """(s at the end, face0 point, face1 point, outward unit) of a run end.
    The outward direction is perpendicular to the end SECTION (from the
    faces), pointing towards the free end — not the skeleton tangent, which
    can run askew near a free end."""
    s_e = st.lo if end == 'lo' else st.hi
    f0, f1 = st.faces(s_e)
    inner = st.centre(min(st.L, s_e + 1.0)) if end == 'lo' else st.centre(max(0.0, s_e - 1.0))
    t = st.centre(s_e) - inner
    d = f1 - f0
    if d.length() > 1e-9:
        n = Vec2(-d.y, d.x) * (1.0 / d.length())
        toward = (f0 - st.faces(min(st.L, s_e + 0.5))[0]) if end == 'lo' else \
            (f0 - st.faces(max(0.0, s_e - 0.5))[0])
        ref = toward if toward.length() > 1e-9 else t
        t = n if n.x * ref.x + n.y * ref.y >= 0 else n * -1.0
    t = t * (1.0 / t.length()) if t.length() > 1e-9 else Vec2(1.0, 0.0)
    return s_e, f0, f1, t


def _end_perm(N):
    """Hi-end lane of the strand that holds lane r at the lo end, chosen so
    the nested end motifs (lane r ↔ lane N−1−r at both ends) join all N
    strands into ONE circuit (even N) / one path (odd N).

    With the identity the strands pair off: every Interleaved / Linked strand
    has an antipodal partner (value −y) holding the mirror lane at BOTH ends,
    so each pair closes on its own (N/2 separate loops). Swapping the hi
    lanes of two strands in different components merges them; adjacent lanes
    are swapped (one extra crossing each, inside the fade), the fewest
    possible (components − 1)."""
    perm = list(range(N))

    def comps():
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)
        for r in range(N):
            union(('lo', r), ('hi', perm[r]))
        for r in range(N // 2):
            union(('lo', r), ('lo', N - 1 - r))
            union(('hi', r), ('hi', N - 1 - r))
        return find
    for _ in range(N):
        find = comps()
        if len({find(('lo', r)) for r in range(N)}) <= 1:
            break
        for lane in range(N - 1):
            x, y = perm.index(lane), perm.index(lane + 1)
            if find(('lo', x)) != find(('lo', y)):
                perm[x], perm[y] = perm[y], perm[x]
                break
    return perm


def _crosses(P, Q):
    """Do polylines P and Q properly cross (P == Q: does P cross itself)?
    Touching at shared end points / neighbouring segments does not count."""
    same = P is Q
    for i in range(len(P) - 1):
        for j in range(len(Q) - 1):
            if same and abs(i - j) <= 1:
                continue
            a, b, c, d = P[i], P[i + 1], Q[j], Q[j + 1]
            t = _cross(a, b, c, d)
            if t is None or not (1e-6 < t < 1 - 1e-6):
                continue
            u = _cross(c, d, a, b)
            if u is not None and 1e-6 < u < 1 - 1e-6:
                return True
    return False


def _cap_turns(st, polys, e_lo, e_hi, env, N, end_lane):
    """Connector polylines at the run's free ends — the canonical END MOTIF:
    lanes r and N−1−r are joined by the resolved end cap inset by half a
    bead plus r lane spacings: r = 0 is the cap itself (the outermost lanes
    = the wall faces), the inner pairs return nested inside it, parallel to
    it (straight for a flat cap, concentric arcs for a round one). An odd
    middle lane ends there."""
    out = []
    h = 0.5 * st.bead
    for end, on in (('lo', e_lo), ('hi', e_hi)):
        if not on:
            continue
        s_e, f0, f1, t = _end_frame(st, end)
        mid = st.at(s_e, 0.0)
        H = st.half(s_e)
        g = 2.0 * H / (N - 1) if N > 1 else 0.0
        at_lane = {end_lane[end][k]: k for k in range(N) if polys[k]}
        pt = (lambda k: polys[k][0]) if end == 'lo' else (lambda k: polys[k][-1])
        square = st.is_square(s_e)
        prev = []
        for r in range(N // 2):
            if r not in at_lane or N - 1 - r not in at_lane:
                continue
            A, B = pt(at_lane[r]), pt(at_lane[N - 1 - r])
            if not square and env is not None:
                # a DEGENERATE end (an opening cutting through a corner: a
                # slanted, tapering wedge — no square section to cap): nested
                # U-turns, ONE scale for the whole nest (fitted on the outer
                # pair) so they stay nested and inside the envelope
                if r == 0:
                    scale = 0.5
                    for _ in range(10):
                        if all(env.clear(q, h - 0.05) for q in _uturn(A, B, t, scale * A.dist(B))[1:-1]):
                            break
                        scale *= 0.7
                out.append(_uturn(A, B, t, scale * A.dist(B)))
                continue
            rail = env.cap_rail(f0, f1, mid, inset=h + r * g) if env is not None else None
            if rail:
                # only the part of the rail BEYOND the lane ends (towards the
                # cap): near a corner its start can lie behind them and the
                # path would double back over itself
                rail = [q for q in rail if (q - A).x * t.x + (q - A).y * t.y > 1e-6 and
                        (q - B).x * t.x + (q - B).y * t.y > 1e-6]
            conn = [A] + rail + [B] if rail else _uturn(A, B, t, 0.5 * A.dist(B))
            if r > 0 and (_crosses(conn, conn) or _crosses(conn, prev)):
                # a cap distorted by a nearby corner: the nested return would
                # fold over itself / the return outside it — a nested U-turn
                # scaled inside the outer one instead
                conn = _uturn(A, B, t, 0.0)
                for depth in (0.5 * A.dist(B) * f for f in (1.0, 0.7, 0.5, 0.35, 0.2, 0.1)):
                    u = _uturn(A, B, t, depth)
                    if not _crosses(u, prev) and (env is None or all(env.clear(q, h - 0.05) for q in u[1:-1])):
                        conn = u
                        break
            prev = conn
            out.append(conn)
    return out


def _neck_v(a, w):
    """Half-angle v* of a loop's self-intersection: w·sin 2v = a·v (0 < v < π/2)."""
    lo, hi = 1e-6, 0.5 * math.pi
    if 2 * w <= a:
        return None
    for _ in range(60):
        m = 0.5 * (lo + hi)
        if w * math.sin(2 * m) - a * m > 0:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def _loop_width(a, w):
    """Longitudinal extent of one loop (the part beyond its neck)."""
    v = _neck_v(a, w)
    if v is None:
        return 0.0
    xs = [a * u + w * math.sin(2 * u) for u in (0.5 * math.pi - v + 2 * v * k / 48 for k in range(49))]
    return max(xs) - min(xs)


def chain_shape(pitch, width, neck):
    """(a, w, q) of the loop chain: pitch → a; the requested loop width (0 =
    0.8 × pitch, at most 1.6 × pitch so same-side loops never touch) → w;
    the neck as a fraction of the loop depth (0 = CHAIN_NECK) → profile q."""
    a = pitch / math.pi
    target = (width if width > 0 else 0.8 * pitch)
    target = max(0.15 * pitch, min(1.6 * pitch, target))
    lo, hi = 0.51 * a, 12.0 * a
    for _ in range(50):
        m = 0.5 * (lo + hi)
        if _loop_width(a, m) < target:
            lo = m
        else:
            hi = m
    w = 0.5 * (lo + hi)
    q = 1.0
    v = _neck_v(a, w)
    neck = neck if neck > 0 else CHAIN_NECK         # default: a low neck — the loop fills its half
    if v is not None:
        n0 = math.cos(v)                        # the natural neck height / depth
        if 1e-6 < n0 < 1 - 1e-6:
            q = max(0.35, min(3.0, math.log(max(0.05, min(0.9, neck))) / math.log(n0)))
    return a, w, q


def _chain_path(st, sp, T, H0, env=None):
    """The loop chain along one run, through the strip coordinates (so it
    follows the wall and stays in the envelope). Open run: a whole number of
    loops fitted between the ends, starting and ending on the centre line (at
    phase 0). Loop: an EVEN number of loops so it closes; the seam lies half
    way between two corners."""
    lo, hi = st.lo, st.hi
    e_lo = (not st.run.cycle) and st.run.a[0] == 'E'
    e_hi = (not st.run.cycle) and st.run.b[0] == 'E'
    capped = env is not None and e_lo and e_hi
    lead = 0.0
    if capped:
        # the END MOTIF zone: the loops end LEAD short of each wall end, and
        # each lap leads from the centre out to its face lane
        lead = min(CORNER_ZONE * T, 0.25 * (hi - lo))
        lo, hi = lo + lead, hi - lead
    span = hi - lo
    if st.run.cycle:
        cs = _corners(st)
        if cs:
            a_ = cs[0][0]
            b_ = cs[1][0] if len(cs) > 1 else a_ + st.L
            lo = 0.5 * (a_ + b_)
    pitch = sp['pitch'] if sp['pitch'] > 0 else 1.2 * T
    m = max(1, round(span / pitch))
    if st.run.cycle and m % 2:
        m = m + 1 if span / pitch >= m else max(2, m - 1)
    a, w, q = chain_shape(span / m, sp['loop_width'], sp['neck'])
    u0 = 2 * math.pi * sp['phase']
    u1 = u0 + m * math.pi
    n = max(64, min(int(m * 96), max(int(m * 24), CHAIN_MAX_POINTS)))
    us = [u0 + (u1 - u0) * k / n for k in range(n + 1)]
    xs = [a * u + w * math.sin(2 * u) for u in us]
    if st.run.cycle:
        x0 = xs[0]
        ss = [lo + (x - x0) for x in xs]                 # exactly one turn of the loop: closes
    else:
        xmin, xmax = min(xs), max(xs)
        k_ = span / (xmax - xmin) if xmax > xmin else 1.0
        ss = [lo + (x - xmin) * k_ for x in xs]          # (backtracking loops kept inside the ends)
    out = []
    for u, s in zip(us, ss):
        H = st.half(s)
        D = min(sp['loop_depth'], H) if sp['loop_depth'] > 0 else H
        sn = math.sin(u)
        out.append(st.at(s, D * math.copysign(abs(sn) ** q, sn)))
    if st.run.cycle and out:
        out[-1] = out[0]
    closed = st.run.cycle
    if capped:
        # a wall with two free ends (a lone wall, a piece cut by openings):
        # the ONE path returns as the MIRRORED chain, so it closes without
        # retrace. Canonical end motif: the forward lap leads out to the f0
        # face lane, the return lap to the f1 face lane, and the resolved end
        # cap (flat / round) joins the two lanes — the outermost lanes are
        # the wall faces, the cap the deliberate wall end
        back = []
        for u, s in zip(reversed(us), reversed(ss)):
            H = st.half(s)
            D = min(sp['loop_depth'], H) if sp['loop_depth'] > 0 else H
            sn = math.sin(u)
            back.append(st.at(s, -D * math.copysign(abs(sn) ** q, sn)))

        def lead_pts(s0, s1, side):
            """Centre at s0 → face lane (side −1: f0, +1: f1) at s1."""
            zs = _samples(min(s0, s1), max(s0, s1), 0.25)
            if s1 < s0:
                zs = list(reversed(zs))
            return [st.at(z, side * st.half(z) * _smooth(abs(z - s0) / abs(s1 - s0))) for z in zs]
        hi_out = lead_pts(hi, st.hi, -1.0)                       # forward lap → f0 lane
        hi_back = list(reversed(lead_pts(hi, st.hi, 1.0)))       # f1 lane → return lap
        lo_back = lead_pts(lo, st.lo, 1.0)                       # return lap → f1 lane
        lo_out = list(reversed(lead_pts(lo, st.lo, -1.0)))       # f0 lane → forward lap
        cap_hi = _chain_cap(st, 'hi', hi_out[-1], hi_back[0], env)
        cap_lo = _chain_cap(st, 'lo', lo_back[-1], lo_out[0], env)
        out = (lo_out[:-1] + out + hi_out[1:] + cap_hi[1:-1] + hi_back[:-1] + back + lo_back[1:]
               + cap_lo[1:-1] + lo_out[:1])
        closed = True
    v = _neck_v(a, w)
    return [out], {'pitch': round(span / m, 3), 'loops': m, 'loop_width': round(_loop_width(a, w), 3),
                   'closed': closed,
                   'neck': round((math.cos(v) ** q) if v is not None else 0.0, 3), 'q': round(q, 3),
                   'loop_depth': round(min(sp['loop_depth'], H0) if sp['loop_depth'] > 0 else H0, 3)}


def _chain_cap(st, end, A, B, env):
    """The chain's end cap: from A (one lap's face lane at the end) along
    the resolved cap, inset by half a bead, to B (the other lap's lane)."""
    s_e, f0, f1, t = _end_frame(st, end)
    rail = env.cap_rail(f0, f1, st.at(s_e, 0.0))
    if not rail:
        return [A, B]
    # rail runs from f0's side to f1's side: start on A's side
    if A.dist(rail[0]) > A.dist(rail[-1]):
        rail = list(reversed(rail))
    return [A] + [q for q in rail if q.dist(A) > 1e-6 and q.dist(B) > 1e-6] + [B]


# ---------------------------------------------------------------------------
# MIXED-SYSTEM JUNCTIONS: an incoming skin-like wall meets ACTUAL host material
# ---------------------------------------------------------------------------

def _lane_start(pts, d, bead):
    """Where an incoming wall's lane begins, walking from its end at the host
    (pts[0]) inward: the first long segment running along the wall direction
    d (back, away from the host). Envelope junction fillets / corner bits
    before it are dropped. (index, point) or None."""
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        v = b - a
        L = v.length()
        if L >= 0.5 * bead and (v.x * d.x + v.y * d.y) / L < -0.995:
            return k, a
        if (pts[0] - b).length() > 6.0 * bead + 40.0:
            break
    return None


def _ray_hits(polys, T, d, reach):
    """[(distance along d from T, poly index, arc position, point)] of the
    ray T + s·d (0 ≤ s ≤ reach) with every host polyline, nearest first."""
    out = []
    for i, pl in enumerate(polys):
        cum = 0.0
        for a, b in zip(pl, pl[1:]):
            seg = b - a
            Ls = seg.length()
            den = d.x * seg.y - d.y * seg.x
            if abs(den) > 1e-12 and Ls > 1e-12:
                w = a - T
                s_ = (w.x * seg.y - w.y * seg.x) / den
                u = (w.x * d.y - w.y * d.x) / den
                if -1e-9 <= u <= 1 + 1e-9 and -1e-6 <= s_ <= reach:
                    out.append((max(0.0, s_), i, cum + max(0.0, min(1.0, u)) * Ls, a + seg * max(0.0, min(1.0, u))))
            cum += Ls
    return sorted(out, key=lambda h: h[0])


def _sub(pl, cum, a, b):
    """Points of polyline pl from arc length a to b (a ≤ b)."""
    def at(x):
        j = next((k for k in range(len(cum) - 1) if cum[k + 1] >= x), len(pl) - 2)
        seg = cum[j + 1] - cum[j]
        return pl[j].lerp(pl[j + 1], 0.0 if seg < 1e-12 else (x - cum[j]) / seg)
    return [at(a)] + [pl[k] for k in range(len(pl)) if a + 1e-9 < cum[k] < b - 1e-9] + [at(b)]


def _trim_from(piece, X, dist):
    """(piece shortened by `dist` from its end at X, the new end point B)."""
    if piece[-1].dist(X) < 1e-9:
        r, back = list(reversed(piece)), True
    else:
        r, back = list(piece), False
    acc, k = 0.0, 0
    while k < len(r) - 1 and acc + r[k].dist(r[k + 1]) < dist:
        acc += r[k].dist(r[k + 1])
        k += 1
    if k >= len(r) - 1:
        return piece, X
    f = (dist - acc) / max(1e-12, r[k].dist(r[k + 1]))
    B = r[k].lerp(r[k + 1], f)
    rest = [B] + r[k + 1:]
    return (list(reversed(rest)) if back else rest), B


def extend_through(chain, d1, d2, c1, c2, depth_in, bead):
    """An incoming SKIN-type wall (single / hollow) meeting a generated host:
    its lanes keep straight along the source direction PAST the host's face
    (c1–c2: the mouth) to `depth_in` inside the host, CROSSING the host's
    printed paths on the way (real contact — nothing of the host is cut),
    and are joined there by a short cap: one closed loop that the router
    links to the host through those crossings. Returns the loop or None."""
    s1 = _lane_start(chain, d1, bead)
    s2 = _lane_start(list(reversed(chain)), d2, bead)
    if s1 is None or s2 is None:
        return None
    i1, T1 = s1
    i2 = len(chain) - 1 - s2[0]
    T2 = s2[1]
    if i2 <= i1:
        return None
    m = c2 - c1
    if m.length() < 1e-9:
        return None
    n = Vec2(-m.y, m.x) * (1.0 / m.length())

    def reach(T, d):
        den = d.x * n.x + d.y * n.y
        if abs(den) < 0.3:
            return None
        t_face = ((c1.x - T.x) * n.x + (c1.y - T.y) * n.y) / den      # to the face line
        return T + d * (t_face + depth_in / abs(den))
    X1, X2 = reach(T1, d1), reach(T2, d2)
    if X1 is None or X2 is None:
        return None
    return [X1] + chain[i1:i2 + 1] + [X2, X1]


def connect_branch(polys, chain, d1, d2, radius, bead, reach):
    """MIXED-SYSTEM JUNCTION — printed material to printed material.

    `chain`: the incoming wall's printed beads as one polyline from its end at
    the host (chain[0]) round to its other end at the host (chain[-1]) — e.g.
    a single / out-and-back branch: lane, U-turn, lane. d1 / d2: the
    ARCHITECTURAL junction direction at each end (the source wall's direction,
    pointing into the host). The architectural envelope only says where the
    wall may exist; the lanes therefore do NOT stop at the host's nominal face:
    each continues STRAIGHT along its direction until it reaches the host
    system's ACTUAL generated geometry (a wave crest early, a trough deeper).
    The two contacts are chosen on one host path, close along it (the first
    such pair); the short host stretch between them is cut out, so branch and
    host become one circuit. radius > 0 rounds each contact corner (Junction R
    applied at the real contact — it never pulls the walls apart).
    Returns (polys, info) or None (no straight lanes / no contact)."""
    s1 = _lane_start(chain, d1, bead)
    s2 = _lane_start(list(reversed(chain)), d2, bead)
    if s1 is None or s2 is None:
        return None
    i1, T1 = s1
    i2 = len(chain) - 1 - s2[0]
    T2 = s2[1]
    if i2 <= i1:
        return None
    h1, h2 = _ray_hits(polys, T1, d1, reach), _ray_hits(polys, T2, d2, reach)
    bound = max(4.0 * T1.dist(T2), 12.0)
    best = None
    key = lambda q: (round(q.x, 6), round(q.y, 6))

    def keeps_touch_points(pl, a_, b_, closed, L):
        """The host stretch to be cut must not hold a vertex the path visits
        twice (a Parallel Walls kiss): cutting it would break the route."""
        cnt = {}
        for q in pl[:-1] if closed else pl:
            cnt[key(q)] = cnt.get(key(q), 0) + 1
        lo_, hi_ = sorted((a_, b_))
        direct = (hi_ - lo_) <= L - (hi_ - lo_) or not closed
        acc = 0.0
        for k_, q in enumerate(pl):
            if k_:
                acc += pl[k_ - 1].dist(q)
            inside = (lo_ < acc < hi_) if direct else (acc < lo_ or acc > hi_)
            if inside and cnt.get(key(q), 0) > 1:
                return False
        return True
    for a in h1:
        for b in h2:
            if a[1] != b[1]:
                continue
            pl = polys[a[1]]
            L = sum(u.dist(v) for u, v in zip(pl, pl[1:]))
            closed = pl[0].dist(pl[-1]) < 1e-6
            gap = abs(a[2] - b[2])
            if (min(gap, L - gap) if closed else gap) > bound or gap < 1e-9:
                continue
            if not keeps_touch_points(pl, a[2], b[2], closed, L):
                continue
            if best is None or a[0] + b[0] < best[0][0] + best[1][0]:
                best = (a, b)
    if best is None:
        return None
    (sA, i, aA, X1), (sB, _, aB, X2) = best
    pl = polys[i]
    cum = [0.0]
    for u, v in zip(pl, pl[1:]):
        cum.append(cum[-1] + u.dist(v))
    L = cum[-1]
    closed = pl[0].dist(pl[-1]) < 1e-6
    (lo, Plo), (hi, Phi) = sorted([(aA, X1), (aB, X2)], key=lambda x: x[0])
    if closed and hi - lo <= L - (hi - lo):
        keep = [_sub(pl, cum, hi, L) + _sub(pl, cum, 0.0, lo)[1:]]
        keep[0][0], keep[0][-1] = Phi, Plo
    elif closed:
        keep = [_sub(pl, cum, lo, hi)]
        keep[0][0], keep[0][-1] = Plo, Phi
    else:
        keep = [x for x in (_sub(pl, cum, 0.0, lo), _sub(pl, cum, hi, L))
                if sum(u.dist(v) for u, v in zip(x, x[1:])) > 1e-9]
        for x in keep:
            if x[-1].dist(Plo) < 1e-9:
                x[-1] = Plo
            if x[0].dist(Phi) < 1e-9:
                x[0] = Phi
    inc = [X1] + chain[i1:i2 + 1] + [X2]
    if radius > 1e-9:
        # Junction R at the REAL contact: a tangent blend from the incoming
        # lane into the host path on each side of the cut
        for end in (0, -1):
            X = inc[end]
            nxt = inc[1] if end == 0 else inc[-2]
            pk = next((k for k, x in enumerate(keep) if x[0].dist(X) < 1e-9 or x[-1].dist(X) < 1e-9), None)
            if pk is None:
                continue
            plen = sum(u.dist(v) for u, v in zip(keep[pk], keep[pk][1:]))
            dl = min(radius, 0.45 * X.dist(nxt))
            dh = min(radius, 0.45 * plen)
            if dl < 1e-6 or dh < 1e-6:
                continue
            keep[pk], B = _trim_from(keep[pk], X, dh)
            A = X + (nxt - X) * (dl / X.dist(nxt))
            n = 8
            bez = [B * ((1 - t) ** 2) + X * (2 * t * (1 - t)) + A * (t * t)
                   for t in (k / n for k in range(n + 1))]
            if end == 0:
                inc = bez + inc[1:]
            else:
                inc = inc[:-1] + list(reversed(bez))
    out = polys[:i] + polys[i + 1:] + keep + [inc]
    return _join(out), {'contacts': [(round(X1.x, 4), round(X1.y, 4)), (round(X2.x, 4), round(X2.y, 4))],
                        'depth': [round(sA, 3), round(sB, 3)]}


def _simplify(pl, tol=0.01):
    """Drop interior points closer than tol to the chord of their neighbours
    (no visible change; fewer vertices for the router)."""
    if len(pl) < 3:
        return pl
    key = lambda q: (round(q.x, 6), round(q.y, 6))
    seen = {}
    for q in pl[1:-1]:
        seen[key(q)] = seen.get(key(q), 0) + 1
    out = [pl[0]]
    for i in range(1, len(pl) - 1):
        a, b, c = out[-1], pl[i], pl[i + 1]
        if seen[key(b)] > 1:                      # a vertex the path visits twice (a touch point): keep
            out.append(b)
            continue
        d = c - a
        L2 = d.x * d.x + d.y * d.y
        if L2 > 1e-18:
            t = max(0.0, min(1.0, ((b.x - a.x) * d.x + (b.y - a.y) * d.y) / L2))
            if (a + d * t).dist(b) < tol:
                continue
        out.append(b)
    out.append(pl[-1])
    return out


def _cross(a, b, c, d):
    """Parameter t on a→b of its intersection with c→d, or None."""
    r = b - a
    q = d - c
    den = r.x * q.y - r.y * q.x
    if abs(den) < 1e-12:
        return None
    w = c - a
    t = (w.x * q.y - w.y * q.x) / den
    u = (w.x * r.y - w.y * r.x) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return t
    return None


def min_effective_thickness(strips, polys, bead, thick):
    """The thinnest wall cross-section the bead paths imply: along every run
    (stations ~ half a thickness apart, away from free ends) the cross
    section between the faces is cut by every generated path; the deposited
    wall there spans from the outermost bead edge on one side to the
    outermost on the other (bead width included, never beyond the faces)."""
    segs = [(a, b, min(a.x, b.x), max(a.x, b.x), min(a.y, b.y), max(a.y, b.y))
            for pl in polys for a, b in zip(pl, pl[1:])]
    cell = max(2.0, thick)
    grid = {}
    for i, (_, _, x0, x1, y0, y1) in enumerate(segs):
        for gx in range(math.floor(x0 / cell), math.floor(x1 / cell) + 1):
            for gy in range(math.floor(y0 / cell), math.floor(y1 / cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    worst = None
    for st in strips:
        lo, hi = st.lo, st.hi
        if not st.run.cycle:
            lo, hi = lo + 0.5 * thick, hi - 0.5 * thick
        if hi <= lo:
            continue
        for s in _samples(lo, hi, max(1.0, 0.5 * thick)):
            f0, f1 = st.faces(s)
            T = f0.dist(f1)
            if T < 1e-6:
                continue
            cand = set()
            for gx in range(math.floor(min(f0.x, f1.x) / cell), math.floor(max(f0.x, f1.x) / cell) + 1):
                for gy in range(math.floor(min(f0.y, f1.y) / cell), math.floor(max(f0.y, f1.y) / cell) + 1):
                    cand.update(grid.get((gx, gy), ()))
            ts = [t for i in cand for t in [_cross(f0, f1, segs[i][0], segs[i][1])] if t is not None]
            eff = min(T, (max(ts) - min(ts)) * T + bead) if ts else 0.0
            if worst is None or eff < worst[0]:
                m = f0.lerp(f1, 0.5)
                worst = (eff, (round(m.x, 3), round(m.y, 3)))
    return worst


def generate(rings, system, bead, contact, seam_hints=None):
    """(polylines, report) of a wall system filling one wall-material region
    (rings: the envelope, material on the left). seam_hints: points where the
    user placed route starts (Parallel Walls put their lane-change seam zone
    at the nearest one on the wall)."""
    sp = normalize(system)
    if sp is not None and seam_hints:
        sp = dict(sp, seam_hint=list(seam_hints))
    rings = [r for r in rings if len(r) >= 3]
    if sp is None or not rings:
        return [], {'system': None}
    thick = WL._thickness(rings)
    h = max(0.5, min(thick / 3.0, 20.0 / 3.0))
    sk = WL.skeleton(rings, h, thick)
    geo = WL._Geo(sk)
    runs = [r for r in sk.runs if r.length > 1e-6 or r.cycle]
    env = _Envelope(rings, bead)
    strips = [_Strip(geo, r, bead, thick, env) for r in runs]
    polys, infos, Ts = [], [], []
    for st in strips:
        if st.hi - st.lo < 0.5 * bead:
            continue
        ws_ = sorted(2 * st.half(st.lo + (st.hi - st.lo) * k / 8) + bead for k in range(1, 8))
        T = ws_[len(ws_) // 2]                       # the run's true (median) thickness
        Ts.append(T)
        H0 = max(0.0, 0.5 * T - 0.5 * bead)
        if sp['type'] == 'chain':
            p, info = _chain_path(st, sp, T, H0, env)
        else:
            p, info = _wave_paths(st, sp, H0, env)
        polys += [_simplify(pl) for pl in p if len(pl) >= 2]
        infos.append(info)
    worst = min_effective_thickness(strips, polys, bead, thick)
    env = sorted(Ts) or [thick]
    rep = {'system': sp['type'], 'label': LABELS[sp['type']], 'params': sp,
           'envelope': round(env[len(env) // 2], 3), 'bead': bead, 'runs': len(strips), 'paths': len(polys),
           'corners': sum(i.get('corners', 0) for i in infos),
           'min_effective_thickness': None if worst is None else round(worst[0], 3),
           'min_effective_at': None if worst is None else worst[1],
           'per_run': infos,
           'closest_approach': min((i['closest_approach'] for i in infos if 'closest_approach' in i),
                                   default=None),
           # a closed route needs an even number of strand ends at every free
           # wall end: odd Interleaved / Linked path counts cannot close there
           'free_ends': sum(1 for st in strips if not st.run.cycle for nd in (st.run.a, st.run.b) if nd[0] == 'E'),
           'closable': not (sp['type'] in ('interleaved', 'linked', 'parallel') and sp['paths'] % 2 and
                            any(not st.run.cycle and 'E' in (st.run.a[0], st.run.b[0]) for st in strips))}
    return polys, rep
