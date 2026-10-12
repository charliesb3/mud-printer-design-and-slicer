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
therefore look identical whatever the period or the span lengths.

CHAINED LOOP MOTIFS (2026-10-09; LOCAL — the chain outside them is unchanged,
sample for sample):
  corner    within a window of ~2 pitches (≥ 1.5 t) each side of a corner the
            loops are laid in a CORNER FRAME (straight legs + a sector where
            every lane turns round the inner corner, the outer lanes squarer
            so the outermost loops fill the outer corner) and re-timed (≤ the
            window, rate ½…2) so one whole loop crest sits on the bisector:
            the outer lobe on a ring / single lap; on a doubled run the
            nearest crest (the mirror lap holds the inner one, made shallower).
  junction  a branch end pushed into a host (pass-10 band push) joins its two
            mirrored laps by one round EYE reaching into the host — the chain's
            last link — instead of face-lane leads + a flat cap; the model keeps
            it only where it really crosses the host's paths (else the cap).
"""
from __future__ import annotations

import bisect
import math

import wall_lattice as WL
from model import Vec2
from resolve_cancel import check as _cancel_check     # (a superseded Designer resolve stops here)

SYSTEMS = ('single', 'hollow', 'skin_web', 'parallel', 'interleaved', 'linked', 'chain')
LABELS = {'single': 'Single / Out-and-Back Wall', 'hollow': 'Hollow / Skins Only', 'skin_web': 'Skin + Web',
          'parallel': 'Parallel Walls', 'interleaved': 'Interleaved Waves', 'linked': 'Linked Waves',
          'chain': 'Chained Loop'}
GENERATED = ('parallel', 'interleaved', 'linked', 'chain')     # paths generated here (else: skins / beads)
DEFAULTS = {
    'parallel': {'walls': 3},
    # (Interleaved Depth / Linked Amplitude REMOVED 2026-10-08: the Wall
    # Thickness alone owns the transverse envelope — full depth / H / N; a
    # stored legacy value is ignored by normalize)
    'interleaved': {'pattern': 'wave', 'paths': 3, 'period': 30.0},
    'linked': {'pattern': 'wave', 'paths': 3, 'period': 24.0},
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
CHAIN_SLACK = 1.0                    # × lead: a loop crest may pass the usable end by this much at a cut
CHAIN_MAX_POINTS = 60000             # sampling budget of one run's chain …
CHAIN_SAMPLES = 96                   # … samples per loop (half period) within it
CHAIN_MIN_SAMPLES = 24               # … and never fewer per loop
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

    def at(self, s, y, fr=None):
        """fr: frame(s) already evaluated by the caller (same result)."""
        m, u, H = fr or self.frame(s)
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

def _wave_paths(st, sp, H0, env, ref=None, vcuts=()):
    """Interleaved / linked paths of one run: spans between anchors (ends,
    corners) with whole periods, phase anchored at the corners, fading into
    lanes that turn each corner mitred.
    ref (PHASE REFERENCE, _ref_map): the run lies on a run of the uncut
    wall — every sample takes the REFERENCE span's period and phase (and its
    corner fades) at s_ref, the across sign of the reference; only this
    run's own free ends fade into the end lanes (a cut end is solved
    locally, the pattern is not re-fitted)."""
    t, N, pat = sp['type'], sp['paths'], sp['pattern']
    th = st.thick
    cs = _corners(st)
    if ref is not None and cs and ref[0].get('corner_s'):
        # corners at the REFERENCE's positions (this piece's own detection
        # differs by a fraction of an inch — and moves the mitres with it)
        Lr, snapped = ref[0]['L'], []
        for c in cs:
            xr = ref[1](c[0])
            best = None
            for sr in ref[0]['corner_s']:
                for k in ((-1, 0, 1) if ref[0]['cycle'] else (0,)):
                    if abs(sr + k * Lr - xr) < st.thick and (best is None or abs(sr + k * Lr - xr) < abs(best - xr)):
                        best = sr + k * Lr
            snapped.append(((ref[2](best),) + tuple(c[1:])) if best is not None else c)
        cs = snapped
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

    tau = ref[3] if ref is not None else 1.0       # across sign of the reference run

    def value(k, phase, H):
        return tau * _value(k, phase, H)

    def _value(k, phase, H):
        if t == 'parallel':                      # N evenly spread longitudinal walls
            return 0.0 if N < 2 else -H + k * (2.0 * H / (N - 1))
        if t == 'interleaved':                   # full depth: the envelope
            return H * _wave(pat, phase + 2 * math.pi * k / N)
        a = H / N                                # neighbours meet at their extrema
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
    if ref is not None and e_lo and e_hi and t != 'parallel':
        # the closing lane permutation stays where the REFERENCE has it (its
        # 'hi' end), expressed in this run's lanes — else an untouched
        # original end would change motif whenever the skeleton orients the
        # cut piece the other way: against the reference → the other end;
        # across sign flipped → the permutation mirrored (lane r ↔ N−1−r)
        pl = perm if tau > 0 else [N - 1 - perm[N - 1 - r] for r in range(N)]
        ident = {k: rank[k] for k in range(N)}
        permd = {k: pl[rank[k]] for k in range(N)}
        end_lane = {'lo': ident, 'hi': permd} if ref[1](1.0) > ref[1](0.0) else {'lo': permd, 'hi': ident}

    def end_y(r, H):
        return 0.0 if N < 2 else -H + r * (2.0 * H / (N - 1))

    # CROSS-Z TRANSITION (vcuts): toward the end lanes the cut pieces use —
    # the closing permutation on the low-s face (that piece's far end in the
    # reference orientation), the plain ranks on the high-s face; between the
    # two end stations the lanes are interpolated (no cut: strands go on)
    # (the low-s piece carries the permutation only when both its ends are
    # free: a ring cut open, or a run starting at a free wall end)
    perm_t = _end_perm(N) if t != 'parallel' and (st.run.cycle or st.run.a[0] == 'E') else list(range(N))
    dst = 0.5 * st.thick
    Fv = max(st.bead, 0.5 * st.thick)

    def adj(k, s, y, H):
        for (fa, fb, al) in vcuts:
            for sh in ((0.0, st.L) if st.run.cycle else (0.0,)):
                s_ = s + sh
                sa, sb = fa - dst, fb + dst
                if s_ < sa - Fv or s_ > sb + Fv:
                    continue
                left, right = end_y(perm_t[rank[k]], H), end_y(rank[k], H)
                if s_ <= sa:
                    wv, tgt = _smooth((sa - s_) / Fv), left
                elif s_ >= sb:
                    wv, tgt = _smooth((s_ - sb) / Fv), right
                else:
                    wv, tgt = 0.0, left + (right - left) * (s_ - sa) / max(1e-9, sb - sa)
                y = tgt + (y - tgt) * (1.0 - al * (1.0 - wv))
        return y

    polys = [[] for _ in range(N)]
    info = {'periods': [], 'corners': len(cs)}
    ref_spans = []
    ref_corners = []
    rspans = []
    if ref is not None:
        rf, fwd = ref[0], ref[1]
        rspans = rf.get('spans') or []
        if not rspans:
            ref = None

    # a NEW end (a cut) fades in half a wall thickness: the smallest local
    # change that still lands the strands on the end lanes (an ORIGINAL end
    # keeps the reference fade, identical to the uncut wall)
    Fcut = max(st.bead, 0.5 * st.thick)
    orig_lo = orig_hi = False
    if ref is not None:
        rf0 = ref[0]
        ends_r = (rf0['lo'], rf0['hi'])
        orig_lo = e_lo and min(abs(ref[1](st.lo) - e) for e in ends_r) < 1e-6
        orig_hi = e_hi and min(abs(ref[1](st.hi) - e) for e in ends_r) < 1e-6

    def ref_span(sr):
        """(a, b, P, fade-at-a, fade-at-b, sr in its frame) of the reference span at sr."""
        L_ = rf['L']
        cands = [sr] + ([sr % L_, sr % L_ + L_] if rf['cycle'] else [])
        for x in cands:
            for (ra, rb, rP, rca, rcb) in rspans:
                if ra - 1e-6 <= x <= rb + 1e-6:
                    return ra, rb, rP, rca, rcb, x
        x = min(cands, key=lambda x: min(min(abs(x - ra), abs(x - rb)) for ra, rb, *_ in rspans))
        ra, rb, rP, rca, rcb = min(rspans, key=lambda q: min(abs(x - q[0]), abs(x - q[1])))
        return ra, rb, rP, rca, rcb, x
    for a, b, ca, corner in spans:
        ln = b - a
        if ln <= 1e-6:
            continue
        n = max(1, round(ln / sp['period']))
        P = ln / n
        info['periods'].append(round(P, 3))
        ref_spans.append((a, b, P, bool(ca), corner is not None))
        F = min(0.35 * P, 0.5 * ln)          # the fade into the corner lanes
        step = max(0.25, min(1.0, P / 32))
        free_a = e_lo and not ca and abs(a - st.lo) < 1e-9
        free_b = e_hi and corner is None and abs(b - st.hi) < 1e-9
        fa = ca or free_a
        fb = corner is not None or free_b
        for s in _samples(a, b, step):
            H = st.half(s)
            if ref is not None:
                # the REFERENCE pattern at s_ref; the reference's corner fades;
                # this run's own free ends (original or cut) fade into the end lanes
                ra, rb, rP, rca, rcb, x = ref_span(fwd(s))
                ph = phi0 + 2 * math.pi * (x - ra) / rP
                Fr = min(0.35 * rP, 0.5 * (rb - ra))
                wc = min(_smooth((x - ra) / Fr) if rca else 1.0, _smooth((rb - x) / Fr) if rcb else 1.0)
                wl = _smooth((s - st.lo) / (Fr if orig_lo else Fcut)) if e_lo else 1.0
                wh = _smooth((st.hi - s) / (Fr if orig_hi else Fcut)) if e_hi else 1.0
                w = min(wc, wl, wh)
                for k in range(N):
                    if wl <= min(wc, wh):
                        lk = end_y(end_lane['lo'][k], H)
                    elif wh <= wc:
                        lk = end_y(end_lane['hi'][k], H)
                    else:
                        lk = lane(k, H)
                    q = st.at(s, adj(k, s, lk + (value(k, ph, H) - lk) * w, H))
                    if not polys[k] or polys[k][-1].dist(q) > 1e-9:
                        polys[k].append(q)
                continue
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
                q = st.at(s, adj(k, s, lk + (value(k, ph, H) - lk) * w, H))
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
            forced = None
            if ref is not None:                      # the reference's own choice at this corner
                xr = ref[1](b)
                Lr = ref[0]['L']
                for (cb, ck) in ref[0].get('corner_kinds') or []:
                    dd = abs(cb - xr)
                    if ref[0]['cycle']:
                        dd = min(dd, Lr - dd % Lr if dd % Lr else 0.0, dd % Lr)
                    if dd < st.thick:
                        forced = ck
                        break
            if forced == 'mitre' or (forced is None and all(env.clear(M, 0.5 * st.bead - 0.05) for M in mits)):
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
            ref_corners.append((b, kind))
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
    info['_ref'] = {'spans': ref_spans, 'corner_kinds': ref_corners, 'corner_s': [c[0] for c in cs]}
    if ref is not None:
        info['phase_ref'] = True
    if t == 'parallel':
        info['spacing'] = round(2.0 * H0 / (N - 1), 3) if N > 1 else 0.0
    elif t == 'linked':
        a = H0 / N
        d = 2.0 * (H0 - a) / (N - 1) if N > 1 else 0.0
        info.update({'amplitude': round(a, 3), 'spacing': round(d, 3), 'closest_approach': round(d - 2 * a, 3)})
    else:
        info['amplitude'] = round(H0, 3)
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
        square = st.is_square(s_e) or round(s_e, 9) in getattr(st, '_ref_square', ())
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


# ---------------------------------------------------------------------------
# CHAINED LOOP CORNER MOTIF (2026-10-09)
# ---------------------------------------------------------------------------
# The strip frame crosses a sharp corner on the skeleton's diagonal, so the
# loops there were squeezed onto it (an angular, crushed loop). Within a LOCAL
# WINDOW round each corner the chain is instead laid in a CORNER FRAME:
#   - legs: the straight wall before / after (identical to the strip there);
#   - sector: every lane (constant distance ρ from the turn-inside face) turns
#     round one pivot P (the inner face corner; the inner fillet's centre when
#     that face is rounded) — inner lanes as round arcs, the outer ones
#     squarer (a superellipse, exponent κ growing with ρ), so the outermost
#     loops reach into the outer corner while every lane stays ½ bead inside
#     the resolved faces (κ is lowered until it does: rounded corners).
# The loops keep their own curve; only their PROGRESS is remapped smoothly:
# the strip length of the window onto the frame's centre-lane length, plus a
# small timing shift (≤ ½ pitch, zero slope at the window ends) that puts the
# nearest loop CREST on the corner bisector — the corner always holds one
# whole, symmetric loop (an outer lobe filling the corner, or an inner
# teardrop round the inner corner), never a loop cut by it. Outside the
# window the chain is unchanged (byte-identical samples).
CHAIN_MOTIFS = True                  # corner + junction motifs on (False: the plain V1 chain — tests / comparison)
CHAIN_CORNER_WINDOW = 2.0            # × pitch: half window (≥ 1.5 t) — the loops a corner may absorb
CHAIN_CORNER_KAPPA = (6.0, 4.0, 3.0, 2.5, 2.0)    # outer-lane squareness, tried in order
CHAIN_CORNER_MAX_TURN = math.radians(135.0)       # sharper turns keep the strip frame
CHAIN_CORNER_INNER = 0.5           # inside-of-turn depth reduction at the bisector
CHAIN_CORNER_MIN_RATE = 0.5          # the timing shift keeps the loops' progress rate within [½, 2]


def _dot(a, b):
    return a.x * b.x + a.y * b.y


def _bell(f):
    """0 → 1 → 0 over f ∈ [−1, 1] (smoothstep each way: zero slope at both ends and the top)."""
    return _smooth(1.0 - abs(f))


class _CornerFrame:
    """The corner frame of one chain corner window [sa, sb] round sc (see
    CHAINED LOOP CORNER MOTIF). `ok` False: no motif (the strip is kept)."""

    def __init__(self, st, sa, sc, sb, H, bead, env, D):
        self.ok = False
        self.sa, self.sc, self.sb, self.H, self.bead = sa, sc, sb, H, bead
        L = st.L
        ma, ua, _ = st.frame(sa % L if st.run.cycle else sa)
        mb, ub, _ = st.frame(sb % L if st.run.cycle else sb)
        if ua.length() < 0.5 or ub.length() < 0.5:
            return
        nx = lambda s: st.frame(s % L if st.run.cycle else s)[0]
        e1 = Vec2(ua.y, -ua.x)
        if _dot((nx(sa + 0.5) - ma), e1) < 0:
            e1 = e1 * -1.0
        e2 = Vec2(ub.y, -ub.x)
        if _dot((mb - nx(sb - 0.5)), e2) < 0:
            e2 = e2 * -1.0
        cr = e1.x * e2.y - e1.y * e2.x
        phi = math.acos(max(-1.0, min(1.0, _dot(e1, e2))))
        if phi < math.radians(20.0) or phi > CHAIN_CORNER_MAX_TURN:
            return
        left = 1.0 if cr > 0 else -1.0                 # the turn's inside: left of travel for a left turn
        si = 1.0 if _dot(ua, Vec2(-e1.y, e1.x)) * left > 0 else -1.0     # y sign of the inside face (incoming)
        si_b = 1.0 if _dot(ub, Vec2(-e2.y, e2.x)) * left > 0 else -1.0
        if si_b != si:                                  # (the across direction flips: not one wall)
            return
        # both legs must be STRAIGHT over the window (the frame's legs are
        # lines): the strip's centre line within ¼ bead of them
        zone = 0.75 * (2.0 * H + bead)                  # (the skeleton's own corner zone)
        for s in _samples(sa, sb, 0.5):
            if abs(s - sc) < zone:
                continue
            m = nx(s)
            off = _dot(m - ma, Vec2(-e1.y, e1.x)) if s < sc else _dot(m - mb, Vec2(-e2.y, e2.x))
            if abs(off) > 0.25 * bead:
                return
        self.T = T = 2.0 * H + bead
        self.si, self.ma, self.mb, self.ua, self.ub, self.e1, self.e2 = si, ma, mb, ua, ub, e1, e2
        self.n1, self.n2 = ua * -si, ub * -si          # outward (turn-outside) normals of the legs
        c = _dot(self.n1, self.n2)
        self.o_u = (self.n1 + self.n2) * (c / (1.0 + c))
        self.a1, self.a2 = self.n1 - self.o_u, self.n2 - self.o_u
        dext = max(0.5 * bead, min(H, D))               # the deepest lane the loops use
        env_ok = (lambda q, mg: True) if env is None else env.clear
        tol = 0.5 * bead - 0.05
        # the pivot: the inner face corner (r_i = 0), or the centre of a ROUNDED
        # inner face (r_i grows until the innermost lane stays in the material)
        built = False
        for r_i in (0.0, 0.1 * T, 0.25 * T, 0.5 * T, 1.0 * T, 2.0 * T):
            if not self._place(r_i):
                continue
            if all(env_ok(self._sector(th, si * dext, 2.0), tol) for th in self._ths(16)):
                built = True
                break
        if not built:
            return
        # the outer lanes' squareness: the squarest κ whose outermost loop lane
        # stays ½ bead inside the resolved outer face (a rounded corner: lower)
        for k in CHAIN_CORNER_KAPPA:
            self.k_out = k
            if all(env_ok(self._sector(th, -si * dext), tol) for th in self._ths(24)):
                break
        else:
            return
        # the centre lane's arc length (progress ↔ angle in the sector)
        ths = self._ths(64)
        pts = [self._sector(th, 0.0) for th in ths]
        cum = [0.0]
        for p, q in zip(pts, pts[1:]):
            cum.append(cum[-1] + p.dist(q))
        self.ths, self.cum, self.arc = ths, cum, cum[-1]
        self.L1 = self.la + 0.5 * self.arc
        self.L2 = 0.5 * self.arc + self.lb
        self.E1 = self.L1 - (sc - sa)
        self.E2 = self.L2 - (sb - sc)
        self.shift = None
        self.ok = True

    def _place(self, r_i):
        """Pivot P at distance r_i + T/2 inside both centre lines; leg lengths."""
        off = r_i + 0.5 * self.T
        A = self.ma + self.ua * (self.si * off)
        B = self.mb + self.ub * (self.si * off)
        P = _miter(A, self.e1, B, self.e2)
        la, lb = _dot((P - self.ma), self.e1), _dot((self.mb - P), self.e2)
        if la < 0 or lb < 0:
            return False
        self.P, self.r_i, self.la, self.lb = P, r_i, la, lb
        return True

    @staticmethod
    def _ths(n):
        return [0.5 * math.pi * k / n for k in range(n + 1)]

    def _kappa(self, rho):
        t = max(0.0, min(1.0, (rho - 0.5 * self.bead) / max(1e-9, self.T - self.bead)))
        return 2.0 + (getattr(self, 'k_out', 2.0) - 2.0) * t * t

    def _sector(self, th, y, kappa=None):
        rho = 0.5 * self.T - self.si * y               # distance from the inside face
        k = kappa if kappa is not None else self._kappa(rho)
        e = 2.0 / k
        cs, sn = max(0.0, math.cos(th)), max(0.0, math.sin(th))
        return self.P + (self.o_u + self.a1 * (cs ** e) + self.a2 * (sn ** e)) * (self.r_i + rho)

    def _warp(self, s):
        """The TIMING shift: a monotone C1 map of [sa, sb] onto itself (slope 1
        at both ends) taking the chosen crest s* to the corner sc — a cubic
        Hermite through (sa, sa), (s*, sc'), (sb, sb)."""
        if self.shift is None:
            return s
        x1, y1, k1 = self.shift
        if s <= x1:
            x0, y0, k0, xe, ye, ke = self.sa, self.sa, 1.0, x1, y1, k1
        else:
            x0, y0, k0, xe, ye, ke = x1, y1, k1, self.sb, self.sb, 1.0
        h = xe - x0
        t = (s - x0) / h
        t2, t3 = t * t, t * t * t
        return (2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * h * k0 + (-2 * t3 + 3 * t2) * ye + (t3 - t2) * h * ke

    def progress(self, s):
        """Strip position s → centre-lane progress (window start = 0): the
        timing shift, then the window's strip length matched to the frame's
        (sc → the sector's middle; slope 1 at sa, sc, sb)."""
        s = self._warp(s)
        sa, sc, sb = self.sa, self.sc, self.sb
        if s <= sc:
            return (s - sa) + self.E1 * _smooth((s - sa) / (sc - sa))
        return self.L1 + (s - sc) + self.E2 * _smooth((s - sc) / (sb - sc))

    def set_crest(self, s_crest):
        """Shift the timing so the curve's crest at s_crest lands on the
        corner (sc → the bisector), as far as the progress rate stays within
        [CHAIN_CORNER_MIN_RATE, 1 / CHAIN_CORNER_MIN_RATE]."""
        self.shift = None
        sa, sc, sb = self.sa, self.sc, self.sb
        if not sa + 0.05 * (sc - sa) < s_crest < sb - 0.05 * (sb - sc):
            return
        zs = _samples(sa, sb, 0.25)
        for lam in (1.0, 0.8, 0.6, 0.4, 0.2):
            y1 = s_crest + lam * (sc - s_crest)
            self.shift = (s_crest, y1, 1.0)          # (slope 1 at the crest: the corner loop is moved, not distorted)
            ws = [self._warp(z) for z in zs]
            rates = [(q - p) / (z1 - z0) for p, q, z0, z1 in zip(ws, ws[1:], zs, zs[1:])]
            if min(rates) >= CHAIN_CORNER_MIN_RATE and max(rates) <= 1.0 / CHAIN_CORNER_MIN_RATE:
                return
        self.shift = None

    def point(self, s, y):
        t = self.progress(s)
        if y * self.si > 0:
            # the INSIDE of the turn is short: loops reaching into it are made
            # shallower round the bisector (no crumpled curl at the inner corner)
            y *= 1.0 - CHAIN_CORNER_INNER * _bell((t - self.L1) / (0.5 * self.arc + 0.5 * self.T))
        if t <= self.la:
            return self.ma + self.e1 * t + self.ua * y
        if t >= self.la + self.arc:
            return self.mb - self.e2 * (self.L1 + self.L2 - t) + self.ub * y
        d = t - self.la
        k = min(len(self.cum) - 2, max(0, bisect.bisect_right(self.cum, d) - 1))
        f = (d - self.cum[k]) / max(1e-12, self.cum[k + 1] - self.cum[k])
        return self._sector(self.ths[k] + (self.ths[k + 1] - self.ths[k]) * f, y)


def _chain_corners(st, T, H, bead, env, lo, hi, pitch, D, s_of_u, u_lo, u_hi, doubled=False, ysign=1.0):
    """[_CornerFrame] of the run's corners (windows inside [lo, hi], never
    overlapping); each with its timing shift set (s_of_u: the curve's strip
    position of a parameter u, monotonic in the loop's mean progress)."""
    cs = [c[0] for c in _corners(st)]
    if not cs:
        return []
    L = st.L
    if st.run.cycle:
        cs = sorted(c + L if c < lo else c for c in cs)
    out = []
    for i, sc in enumerate(cs):
        A = max(1.5 * T, CHAIN_CORNER_WINDOW * pitch)
        if st.run.cycle:
            prev = cs[i - 1] - (L if i == 0 else 0.0)
            nxt = cs[(i + 1) % len(cs)] + (L if i == len(cs) - 1 else 0.0)
            room_a, room_b = 0.5 * (sc - prev), 0.5 * (nxt - sc)
            if len(cs) == 1:
                room_a = room_b = 0.5 * L
            room_a, room_b = min(room_a, sc - lo), min(room_b, lo + L - sc)
        else:
            room_a = (sc - lo) if i == 0 else 0.5 * (sc - cs[i - 1])
            room_b = (hi - sc) if i == len(cs) - 1 else 0.5 * (cs[i + 1] - sc)
        Aa, Ab = min(A, room_a - 0.25), min(A, room_b - 0.25)
        if min(Aa, Ab) < 0.9 * T:
            continue
        fr = _CornerFrame(st, sc - Aa, sc, sc + Ab, H, bead, env, D)
        if not fr.ok:
            continue
        # the loop CREST (u = kπ + π/2) to put on the corner: on a single lap
        # (a ring, a run between junctions) the nearest OUTER crest — every
        # corner gets the outer lobe; on a doubled run (two mirrored laps)
        # the nearest crest of either side (the other lap holds the mirror)
        a_mean = (s_of_u(u_hi) - s_of_u(u_lo)) / max(1e-12, u_hi - u_lo)
        if abs(a_mean) < 1e-12:
            continue
        uc = u_lo + (sc - s_of_u(u_lo)) / a_mean
        k = round((uc - 0.5 * math.pi) / math.pi)
        best = None
        for kk in range(k - 2, k + 3):
            u_ = kk * math.pi + 0.5 * math.pi
            if not doubled and (math.sin(u_) * ysign > 0) != (fr.si < 0):
                continue
            if u_lo <= u_ <= u_hi:
                s_ = s_of_u(u_)
                if best is None or abs(s_ - sc) < abs(best - sc):
                    best = s_
        if best is not None:
            fr.set_crest(best)
        out.append(fr)
    return out


def _chain_adj(s, y, side, H, vcuts, dst, lead_v):
    """CROSS-Z TRANSITION deformation of a chain y-offset: toward the cut
    layer's end motif — the forward lap toward face 0 (side −1), the return
    lap toward face 1 (side +1), over the lead before each end station, held
    between the stations. Shared by _chain_path (own fit) and
    _chain_path_ref (M4: composed on top of the mapped stack-reference
    curve)."""
    for (fa, fb, al) in vcuts:
        sa, sb = fa - dst, fb + dst
        if s < sa - lead_v or s > sb + lead_v:
            continue
        wv = _smooth((sa - s) / lead_v) if s <= sa else (_smooth((s - sb) / lead_v) if s >= sb else 0.0)
        tgt = side * H
        y = tgt + (y - tgt) * (1.0 - al * (1.0 - wv))
    return y


def _chain_path(st, sp, T, H0, env=None, vcuts=(), jends=()):
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
    n = max(64, min(int(m * CHAIN_SAMPLES), max(int(m * CHAIN_MIN_SAMPLES), CHAIN_MAX_POINTS)))
    us = [u0 + (u1 - u0) * k / n for k in range(n + 1)]
    xs = [a * u + w * math.sin(2 * u) for u in us]
    if st.run.cycle:
        x0 = xs[0]
        ss = [lo + (x - x0) for x in xs]                 # exactly one turn of the loop: closes
        ref_c = {'lo': lo, 'span': span, 'm': m, 'u0': u0, 'xmin': x0, 'k': 1.0}
    else:
        xmin, xmax = min(xs), max(xs)
        k_ = span / (xmax - xmin) if xmax > xmin else 1.0
        ss = [lo + (x - xmin) * k_ for x in xs]          # (backtracking loops kept inside the ends)
        ref_c = {'lo': lo, 'span': span, 'm': m, 'u0': u0, 'xmin': xmin, 'k': k_}
    # CROSS-Z TRANSITION (vcuts, capped runs): see _chain_adj for the rule.
    dst, lead_v = 0.5 * T, min(CORNER_ZONE * T, 0.25 * (hi - lo))
    vc_on = bool(vcuts) and capped
    eyes = 0
    # CORNER MOTIFS: the samples inside a corner window are laid in its
    # corner frame (one loop depth there: the run's own envelope)
    D0 = min(sp['loop_depth'], H0) if sp['loop_depth'] > 0 else H0
    xr = (x0, 1.0) if st.run.cycle else (xmin, k_)
    cfs = [] if not CHAIN_MOTIFS else _chain_corners(st, T, H0, st.bead, env, lo, hi, span / m, D0,
                         lambda u_: lo + (a * u_ + w * math.sin(2 * u_) - xr[0]) * xr[1], u0, u1, capped) \
        if env is not None else []
    win = [next((f for f in cfs if f.sa < s < f.sb), None) for s in ss] if cfs else [None] * len(ss)
    # the strip frame ONCE per sample: both laps run over the same stations
    # (st.half / st.at would each evaluate it again — identical values)
    frs = [st.frame(s) if f is None else None for s, f in zip(ss, win)]
    _cancel_check()
    out = []
    for u, s, fr, cf in zip(us, ss, frs, win):
        sn = math.sin(u)
        if cf is not None:
            out.append(cf.point(s, D0 * math.copysign(abs(sn) ** q, sn)))
            continue
        H = fr[2]
        D = min(sp['loop_depth'], H) if sp['loop_depth'] > 0 else H
        y = D * math.copysign(abs(sn) ** q, sn)
        out.append(st.at(s, _chain_adj(s, y, -1.0, H, vcuts, dst, lead_v) if vc_on else y, fr))
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
        _cancel_check()
        back = []
        for u, s, fr, cf in zip(reversed(us), reversed(ss), reversed(frs), reversed(win)):
            sn = math.sin(u)
            if cf is not None:
                back.append(cf.point(s, -D0 * math.copysign(abs(sn) ** q, sn)))
                continue
            H = fr[2]
            D = min(sp['loop_depth'], H) if sp['loop_depth'] > 0 else H
            y = -D * math.copysign(abs(sn) ** q, sn)
            back.append(st.at(s, _chain_adj(s, y, 1.0, H, vcuts, dst, lead_v) if vc_on else y, fr))

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
        # a JUNCTION end (pushed into a host): the chain's last link — one eye
        # joining the two laps (_chain_eye); a free end keeps the canonical
        # end motif (face lanes + the resolved cap)
        tips = (st.centre(0.0), st.centre(st.L))
        at_j = [CHAIN_MOTIFS and any(tp.dist(j) < 0.75 * T for j in (jends or ())) for tp in tips]
        # the eye starts at the lap's last WAIST (a centre crossing, u = kπ):
        # a partial loop beyond it (Phase ≠ 0) is absorbed into the eye
        i_lo, i_hi = 0, len(out) - 1
        if at_j[1]:
            uw = math.floor((u1 + 1e-9) / math.pi) * math.pi
            i_hi = max(range(len(us)), key=lambda i: -abs(us[i] - uw)) if uw > u0 + 0.5 * math.pi else i_hi
        if at_j[0]:
            uw = math.ceil((u0 - 1e-9) / math.pi) * math.pi
            i_lo = min(range(len(us)), key=lambda i: abs(us[i] - uw)) if uw < u1 - 0.5 * math.pi else 0
        nb = len(back)
        eye_hi = _chain_eye(st, 'hi', ss[i_hi], out[i_hi], out[i_hi] - out[i_hi - 1], back[nb - 1 - i_hi], env, H0) \
            if at_j[1] and i_hi > i_lo + 1 else None
        eye_lo = _chain_eye(st, 'lo', ss[i_lo], back[nb - 1 - i_lo], back[nb - 1 - i_lo] - back[nb - 2 - i_lo],
                            out[i_lo], env, H0) if at_j[0] and i_hi > i_lo + 1 else None
        if eye_hi or eye_lo:
            j_lo, j_hi = (i_lo if eye_lo else 0), (i_hi if eye_hi else len(out) - 1)
            out, back = out[j_lo:j_hi + 1], back[nb - 1 - j_hi:nb - j_lo]
        hi_tr = eye_hi[1:-1] if eye_hi else \
            hi_out[1:] + _chain_cap(st, 'hi', hi_out[-1], hi_back[0], env)[1:-1] + hi_back[:-1]
        if eye_lo:
            out = out + hi_tr + back + eye_lo[1:-1] + out[:1]
        else:
            cap_lo = _chain_cap(st, 'lo', lo_back[-1], lo_out[0], env)
            out = lo_out[:-1] + out + hi_tr + back + lo_back[1:] + cap_lo[1:-1] + lo_out[:1]
        eyes = int(bool(eye_hi)) + int(bool(eye_lo))
        closed = True
    v = _neck_v(a, w)
    return [out], {'pitch': round(span / m, 3), 'loops': m, 'loop_width': round(_loop_width(a, w), 3),
                   'closed': closed, '_ref': {'chain': ref_c},
                   'corner_motifs': len(cfs), 'junction_motifs': eyes,
                   'neck': round((math.cos(v) ** q) if v is not None else 0.0, 3), 'q': round(q, 3),
                   'loop_depth': round(min(sp['loop_depth'], H0) if sp['loop_depth'] > 0 else H0, 3)}


def _chain_path_ref(st, sp, T, H0, env, ref, vcuts=()):
    """The chain of a run lying on a reference run (PHASE REFERENCE): the
    REFERENCE curve (same pitch, same loop stations — mapped s_ref → s),
    kept where it lies inside this run's usable length; at a cut end the
    chain is clipped where it leaves (any phase) and the canonical end
    motif — a lead from the curve to the face lanes + the resolved cap — is
    solved locally. (Tried 2026-10-08 and measured on 5 profiles: clipping
    at loop boundaries, clipping by the curve's mean progression, and
    warping the last 2–5 pitches so the end starts on a centre crossing —
    all left more support findings at the cut: 11–44 vs 10.)
    M4 (2026-10-12): a CLOSED ring layer on a ring / stack reference
    (st.run.cycle True) draws the reference curve over its parameter range
    and closes the output explicitly — the loops come from the reference's
    own even loop count, so the ring reference guarantees parity. The
    function bails with (None, None) if the mapped ring cannot close
    (parity mismatch between reference and layer, or the mapping does not
    cover one full turn), and the own fit takes over.
    M4 (2026-10-12): a CROSS-Z TRANSITION on this run (vcuts) is composed
    on top of the mapped curve through _chain_adj (same rule as _chain_path).
    The deformation is validated inside the strip — any sample whose
    adjusted offset would leave the wall (abs(y_adj) > H + eps) aborts the
    composition and the own fit takes over.
    (None, None) when nothing maps (own fit then)."""
    f, fwd, inv, tau = ref
    c = f.get('chain')
    if not c:
        return None, None
    m_r = int(c['m'])
    a, w, q = chain_shape(c['span'] / m_r, sp['loop_width'], sp['neck'])
    K = 48                                             # samples per half loop (π)
    # (a RING reference: two turns of its chain, so a piece lying across the
    # ring's seam is covered continuously)
    if f['cycle'] or f.get('stack'):
        # a RING reference: the curve over exactly the parameter range that
        # covers this piece's reference range (± a loop) — any position,
        # across the ring's seam, continuous (u on the reference's loop grid).
        # A STACK reference (M3) likewise: a run reaching past the reference's
        # ends continues its loops (same pitch, same phase) instead of
        # stretching one lead to the cap
        r0, r1 = sorted((fwd(st.lo), fwd(st.hi)))
        u_of = lambda sr: ((sr - c['lo']) / c['k'] + c['xmin']) / a
        j0 = math.floor((u_of(r0) - c['u0']) / math.pi) - 1
        j1 = math.ceil((u_of(r1) - c['u0']) / math.pi) + 1
        us = [c['u0'] + math.pi * (j0 + i / K) for i in range((j1 - j0) * K + 1)]
    else:
        us = [c['u0'] + math.pi * i / K for i in range(m_r * K + 1)]
    sref = [c['lo'] + (a * u + w * math.sin(2 * u) - c['xmin']) * c['k'] for u in us]
    L_ = f['L']

    def local(x):
        if not f['cycle']:
            return inv(x)
        cands = [inv(x + j * L_) for j in (-1, 0, 1)]
        return min(cands, key=lambda v: 0.0 if st.lo - 1 <= v <= st.hi + 1 else min(abs(v - st.lo), abs(v - st.hi)))
    if f['cycle']:
        # a ring reference: each sample maps to ±L candidates — keep the
        # mapped curve CONTINUOUS (the candidate nearest the previous one)
        sl = [inv(x) for x in sref]                  # (two turns: the plain mapping is continuous)
    else:
        sl = [local(x) for x in sref]
    e_lo = (not st.run.cycle) and st.run.a[0] == 'E'
    e_hi = (not st.run.cycle) and st.run.b[0] == 'E'
    capped = env is not None and e_lo and e_hi
    lead = min(CORNER_ZONE * T, 0.25 * (st.hi - st.lo)) if capped else 0.0
    lo_l, hi_l = st.lo + lead, st.hi - lead
    # cross-Z transition composition (M4): same rule as _chain_path's own fit
    dst, lead_v = 0.5 * T, (min(CORNER_ZONE * T, 0.25 * (hi_l - lo_l)) if capped else 0.0)
    vc_on = bool(vcuts) and capped
    # the reference curve inside this run's usable length: an original end
    # keeps the reference loops to the end; at a cut the curve is clipped
    # where it leaves (any phase). A loop's crest may reach a little past
    # the end of the usable length and come back: the run is taken with a
    # looser bound (CHAIN_SLACK), then trimmed to the last / first sample
    # within it — no whole loop is lost at a cut.
    slack = CHAIN_SLACK * lead if capped else 0.0
    loose = [lo_l - slack - 1e-6 <= v <= hi_l + slack + 1e-6 for v in sl]
    best, cur = None, None
    for i, o in enumerate(loose + [False]):
        if o and cur is None:
            cur = i
        elif not o and cur is not None:
            if best is None or i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = None
    if best is None:
        return None, None
    strict = [i for i in range(best[0], best[1]) if lo_l - 1e-6 <= sl[i] <= hi_l + 1e-6]
    if len(strict) < K:
        return None, None
    best = (strict[0], strict[-1] + 1)
    idx = list(range(best[0], best[1]))
    if sl[idx[-1]] < sl[idx[0]]:
        idx.reverse()

    # CORNER MOTIFS as on the uncut wall (the crest rule of the reference: a
    # ring's single lap puts its OUTER crest on each corner)
    D0 = min(sp['loop_depth'], H0) if sp['loop_depth'] > 0 else H0
    loc = inv if f['cycle'] else local
    ua_, ub_ = sorted((us[idx[0]], us[idx[-1]]))
    cfs = _chain_corners(st, T, H0, st.bead, env, sl[idx[0]], sl[idx[-1]], c['span'] / m_r, D0,
                         lambda u_: loc(c['lo'] + (a * u_ + w * math.sin(2 * u_) - c['xmin']) * c['k']), ua_, ub_,
                         not f['cycle'], tau) if env is not None and CHAIN_MOTIFS else []

    def pt(i, sign=1.0):
        s_ = sl[i]
        sn = math.sin(us[i])
        for cf in cfs:
            if cf.sa < s_ < cf.sb:
                return cf.point(s_, sign * tau * D0 * math.copysign(abs(sn) ** q, sn))
        H = st.half(s_)
        D = min(sp['loop_depth'], H) if sp['loop_depth'] > 0 else H
        y = sign * tau * D * math.copysign(abs(sn) ** q, sn)
        if vc_on:
            # M4: compose the cross-Z transition on top of the mapped curve.
            # The forward lap (sign +1 in ref terms) targets face at -tau in
            # strip coordinates (matching _chain_path's side = -1 forward
            # convention when tau = +1); the return lap targets the opposite
            # face. side = -sign * tau. The adjusted offset stays in [-H, H]
            # for all natural inputs (the deformation interpolates between
            # the mapped y and ±H); the guard below is a defensive
            # safety-net for weird numerical cases — if it fires,
            # _chain_path_ref bails and the own fit draws this run.
            y_adj = _chain_adj(s_, y, -sign * tau, H, vcuts, dst, lead_v)
            if abs(y_adj) > H + 1e-6:
                return None
            y = y_adj
        return st.at(s_, y)
    _out = [pt(i) for i in idx]
    if any(p is None for p in _out):
        return None, None               # cross-Z deformation would leave the wall
    out = _out
    lo, hi = sl[idx[0]], sl[idx[-1]]

    def ycur(i):                                       # the curve's offset at sample i (local)
        s_ = sl[i]
        H = st.half(s_)
        D = min(sp['loop_depth'], H) if sp['loop_depth'] > 0 else H
        sn = math.sin(us[i])
        return tau * D * math.copysign(abs(sn) ** q, sn)
    y_lo, y_hi = ycur(idx[0]), ycur(idx[-1])
    closed = False
    if capped:
        back = [pt(i, -1.0) for i in reversed(idx)]
        if any(p is None for p in back):
            return None, None           # (same reason: cross-Z deformation out of strip)

        def lead_pts(s0, s1, side, y0):
            """From (s0, y0) on the curve to the face lane `side` at s1."""
            zs = _samples(min(s0, s1), max(s0, s1), 0.25)
            if s1 < s0:
                zs = list(reversed(zs))
            return [st.at(z, y0 + (side * st.half(z) - y0) * _smooth(abs(z - s0) / abs(s1 - s0))) for z in zs]
        # lap → face lane in REFERENCE terms (the forward lap to the
        # reference's face 0), whatever way the skeleton orients this piece
        fs = -tau
        hi_out = lead_pts(hi, st.hi, fs, y_hi)
        hi_back = list(reversed(lead_pts(hi, st.hi, -fs, -y_hi)))
        lo_back = lead_pts(lo, st.lo, -fs, -y_lo)
        lo_out = list(reversed(lead_pts(lo, st.lo, fs, y_lo)))
        cap_hi = _chain_cap(st, 'hi', hi_out[-1], hi_back[0], env)
        cap_lo = _chain_cap(st, 'lo', lo_back[-1], lo_out[0], env)
        out = (lo_out[:-1] + out + hi_out[1:] + cap_hi[1:-1] + hi_back[:-1] + back + lo_back[1:]
               + cap_lo[1:-1] + lo_out[:1])
        closed = True
    elif st.run.cycle:                                  # M4: ring layer on a ring / stack reference
        # the mapped curve must cover one full turn of the layer ring: the
        # strict samples' s-range (hi − lo) must be close to the layer's
        # perimeter st.L. The ring reference's even loop count propagates
        # (its c['m'] is even — _chain_path's ring parity rule), so no
        # extra parity check is needed; the kept block of (m × K) + 1
        # samples already closes.
        if not (0.9 * st.L <= hi - lo <= 1.1 * st.L):
            return None, None           # (mapping did not cover one full turn)
        out[-1] = out[0]
        closed = True
    else:                                              # a junction end: run on to the centre line
        if lo > st.lo + 1e-6:
            zs = _samples(st.lo, lo, 0.5)[:-1]
            out = [st.at(z, y_lo * _smooth((z - st.lo) / max(1e-9, lo - st.lo))) for z in zs] + out
        if hi < st.hi - 1e-6:
            zs = _samples(hi, st.hi, 0.5)[1:]
            out = out + [st.at(z, y_hi * _smooth((st.hi - z) / max(1e-9, st.hi - hi))) for z in zs]
    v = _neck_v(a, w)
    return [out], {'pitch': round(c['span'] / m_r, 3), 'loops': round((best[1] - best[0]) / (2 * K), 2),
                   'corner_motifs': len(cfs),
                   'loop_width': round(_loop_width(a, w), 3), 'closed': closed, 'phase_ref': True,
                   'neck': round((math.cos(v) ** q) if v is not None else 0.0, 3), 'q': round(q, 3),
                   'loop_depth': round(min(sp['loop_depth'], H0) if sp['loop_depth'] > 0 else H0, 3)}


def _hermite(P0, T0, P1, T1, n):
    """n + 1 points of the cubic Hermite P0 (tangent T0) → P1 (tangent T1)."""
    out = []
    for k in range(n + 1):
        t = k / n
        t2, t3 = t * t, t * t * t
        out.append(P0 * (2 * t3 - 3 * t2 + 1) + T0 * (t3 - 2 * t2 + t) + P1 * (-2 * t3 + 3 * t2) + T1 * (t3 - t2))
    return out


def _chain_eye(st, end, s_l, A, dA, B, env, H):
    """CHAINED LOOP JUNCTION MOTIF (2026-10-09): at a branch end pushed into
    a host, the two mirrored laps are joined by ONE round EYE — the chain's
    last link — instead of leading out to the face lanes and a flat cap.
    From A (the forward lap's last waist, arriving along dA) the path swings
    to the side it is already heading for, out along that lane, round the
    apex on the resolved end cap (inset ½ bead: inside the host, so it
    crosses the host's printed paths), back along the other lane and into B
    (the mirrored return lap) — mirror symmetric, so the laps cross at the
    eye's throat like every other link. None when the eye does not fit ½
    bead inside the envelope. (The model checks the eye really crosses the
    host and otherwise regenerates that end with the canonical cap.)"""
    s_e, f0, f1, t = _end_frame(st, end)
    O, n, _ = st.frame(s_l)
    rail = env.cap_rail(f0, f1, st.at(s_e, 0.0)) if env is not None else None
    if not rail or len(rail) < 2:
        return None
    # the APEX: the middle of the end cap (by length) — the eye points along
    # the cap's own normal, so at an oblique end (a wall meeting its host at
    # an angle) it still reaches straight into the host
    cum = [0.0]
    for p_, q_ in zip(rail, rail[1:]):
        cum.append(cum[-1] + p_.dist(q_))
    half = 0.5 * cum[-1]
    k = min(len(cum) - 2, max(0, bisect.bisect_right(cum, half) - 1))
    Ap = rail[k].lerp(rail[k + 1], (half - cum[k]) / max(1e-12, cum[k + 1] - cum[k]))
    ch = rail[-1] - rail[0]
    if ch.length() < 1e-9:
        return None
    t2 = Vec2(-ch.y, ch.x) * (1.0 / ch.length())
    if _dot(t2, t) < 0:
        t2 = t2 * -1.0
    if _dot(t2, t) < 0.5:                               # (more than 60° off the wall: not an end cap)
        return None
    n2 = Vec2(-t2.y, t2.x)
    if _dot(n2, n) < 0:
        n2 = n2 * -1.0
    if _dot(Ap - O, t) < 0.25 * st.bead:
        return None
    dA = dA * (1.0 / max(1e-12, dA.length()))
    side = 1.0 if _dot(dA, n) >= 0 else -1.0
    dB = (dA - n * (2.0 * _dot(dA, n))) * -1.0          # the mirrored lap leaves this way
    for f in (1.0, 0.85, 0.7, 0.55):
        Hs = f * H
        Apf = Ap if f == 1.0 else O.lerp(Ap, 0.5 + 0.5 * f)
        # a LINK: the sides run along the lanes into the host (where they
        # cross its paths, as the canonical cap's sides do) and close with a
        # half circle at the apex
        S1 = Apf - t2 * Hs + n2 * (side * Hs)
        S2 = Apf - t2 * Hs - n2 * (side * Hs)
        if _dot(S1 - O, t) < 0.25 * st.bead or _dot(S2 - O, t) < 0.25 * st.bead:
            continue
        k1, k1b = max(A.dist(S1), 1.0), max(B.dist(S2), 1.0)
        k2 = 1.57 * Hs
        half1 = _hermite(A, dA * k1, S1, t2 * k1, 16) + _hermite(S1, t2 * k2, Apf, n2 * (-side * k2), 10)[1:]
        half2 = _hermite(Apf, n2 * (-side * k2), S2, t2 * -k2, 10) + _hermite(S2, t2 * -k1b, B, dB * k1b, 16)[1:]
        pts = half1 + half2[1:]
        if env is None or all(env.clear(q, 0.5 * st.bead - 0.05) for q in pts[1:-1]):
            return pts
    return None


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
    keys = [(round(q.x, 6), round(q.y, 6)) for q in pl]     # (once per point)
    seen = {}
    for k in keys[1:-1]:
        seen[k] = seen.get(k, 0) + 1
    out = [pl[0]]
    hypot = math.hypot
    for i in range(1, len(pl) - 1):
        a, b, c = out[-1], pl[i], pl[i + 1]
        if seen[keys[i]] > 1:                     # a vertex the path visits twice (a touch point): keep
            out.append(b)
            continue
        dx, dy = c.x - a.x, c.y - a.y
        L2 = dx * dx + dy * dy
        if L2 > 1e-18:
            t = max(0.0, min(1.0, ((b.x - a.x) * dx + (b.y - a.y) * dy) / L2))
            # (= (a + d * t).dist(b), the same float operations)
            if hypot(a.x + dx * t - b.x, a.y + dy * t - b.y) < tol:
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


def contact_near(polys, others, tip, radius):
    """Does a path of `polys` CROSS a path of `others` within `radius` of
    `tip`? (The junction motif's contact check: real printed contact.)
    Segments are bucketed in a 1 in grid round the tip: dense chains stay
    cheap."""
    h = 1.0

    def near(pls):
        for pl in pls:
            for a, b in zip(pl, pl[1:]):
                if min(a.dist(tip), b.dist(tip)) < radius + a.dist(b):
                    yield a, b
    grid = {}
    for c, d in near(others):
        for i in range(int(math.floor(min(c.x, d.x) / h)), int(math.floor(max(c.x, d.x) / h)) + 1):
            for j in range(int(math.floor(min(c.y, d.y) / h)), int(math.floor(max(c.y, d.y) / h)) + 1):
                grid.setdefault((i, j), []).append((c, d))
    for a, b in near(polys):
        seen = set()
        for i in range(int(math.floor(min(a.x, b.x) / h)), int(math.floor(max(a.x, b.x) / h)) + 1):
            for j in range(int(math.floor(min(a.y, b.y) / h)), int(math.floor(max(a.y, b.y) / h)) + 1):
                for sg in grid.get((i, j), ()):
                    if id(sg) in seen:
                        continue
                    seen.add(id(sg))
                    c, d = sg
                    if _cross(a, b, c, d) is not None and _cross(c, d, a, b) is not None:
                        return True
    return False


# ---------------------------------------------------------------------------
# CONTACT STAPLE (2026-10-09): two generated walls that touch architecturally
# (their bands meet or overlap by less than ~a bead: a circle grazing a
# rectangle) print no crossing — each pattern keeps ½ bead inside its OWN
# faces, so the walls' beads only graze or leave a gap, and the router sees two
# separate components. A STAPLE is one small closed loop across the contact:
# two strands one bead apart from inside one wall's printed paths to inside
# the other's, round caps. It crosses both walls' paths (degree-4 crossings:
# parity unchanged), so the walls are BONDED (physical contact) and the route
# can continue through the junction. Used only where the bands touch and no
# printed path of one crosses the other's near the contact.
# ---------------------------------------------------------------------------

def _segs_near(polys, c, radius):
    out = []
    for pl in polys:
        for a, b in zip(pl, pl[1:]):
            if min(a.dist(c), b.dist(c)) < radius + a.dist(b):
                out.append((a, b))
    return out


def _closest_on(segs, q):
    best = None
    for a, b in segs:
        d = b - a
        L2 = d.x * d.x + d.y * d.y
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((q.x - a.x) * d.x + (q.y - a.y) * d.y) / L2))
        p = a + d * t
        dd = p.dist(q)
        if best is None or dd < best[0]:
            best = (dd, p, d)
    return best


def band_contact(rings_a, rings_b, tol):
    """The point where two bands' rings meet (closest approach ≤ tol), or None."""
    best = None
    sb = [(r[k], r[(k + 1) % len(r)]) for r in rings_b for k in range(len(r))]
    for r in rings_a:
        n = len(r)
        for k in range(n):
            a = r[k]
            for c_, d_ in sb:
                if max(c_.x, d_.x) < a.x - tol - 50 or min(c_.x, d_.x) > a.x + tol + 50:
                    continue
                cl = _closest_on([(c_, d_)], a)
                if best is None or cl[0] < best[0]:
                    best = (cl[0], a.lerp(cl[1], 0.5))
    return best[1] if best is not None and best[0] <= tol else None


def contact_staple(polys_a, polys_b, c, bead, thick, inside=None):
    """A closed staple loop bonding walls a and b at their contact point c
    (see CONTACT STAPLE), or None when one is not needed (their paths cross
    near c) or not possible. `inside(q)`: q lies in the material."""
    radius = 3.0 * thick + bead
    if contact_near(polys_a, polys_b, c, radius):
        return None
    sa, sb = _segs_near(polys_a, c, radius), _segs_near(polys_b, c, radius)
    if not sa or not sb:
        return None
    _, pa, ta = _closest_on(sa, c)
    _, pb, _ = _closest_on(sb, c)
    u = pb - pa
    if u.length() < 0.25 * bead:                        # (touching paths: across the a path)
        u = Vec2(-ta.y, ta.x)
        if _dot(u, c - pa) < 0 and (c - pa).length() > 1e-9:
            u = u * -1.0
    if u.length() < 1e-9:
        return None
    u = u * (1.0 / u.length())
    n = Vec2(-u.y, u.x)
    for e, w in ((bead, bead), (1.5 * bead, bead), (0.5 * thick, bead), (bead, 0.5 * bead), (0.5 * thick, 0.5 * bead)):
        A0, B0 = pa - u * e, pb + u * e
        h = 0.5 * w
        cap = lambda C, d, k=8: [C + (n * math.cos(math.pi * i / k) + d * math.sin(math.pi * i / k)) * h
                                 for i in range(k + 1)]
        loop = [A0 + n * h, B0 + n * h] + cap(B0, u)[1:-1] + [B0 - n * h, A0 - n * h] + \
               [A0 + (n * -math.cos(math.pi * i / 8) - u * math.sin(math.pi * i / 8)) * h for i in range(1, 8)]
        loop.append(loop[0])
        if inside is not None and not all(inside(q) for q in loop):
            continue
        if contact_near([loop], polys_a, c, radius) and contact_near([loop], polys_b, c, radius):
            return loop
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


def generate(rings, system, bead, contact, seam_hints=None, phase_ref=None, transitions=None, junction_ends=None):
    """(polylines, report) of a wall system filling one wall-material region
    (rings: the envelope, material on the left). seam_hints: points where the
    user placed route starts (Parallel Walls put their lane-change seam zone
    at the nearest one on the wall).
    report['frames']: per run, the REFERENCE FRAME of what was generated (its
    centre line, the spans / periods / phase anchors of Interleaved / Linked,
    the loop parameters of Chained Loop) — see PHASE REFERENCE.
    phase_ref: frames of the same wall system generated WITHOUT an Assembly
    profile cut (layer_design). A run lying on a reference run keeps that
    run's period, phase, lane identity and loop stations; only its NEW free
    ends (the cut) get the canonical end motif (_ref_map).
    transitions: virtual openings [(face point a, face point b, normal a,
    normal b, alpha)] — an Assembly opening's CROSS-Z TRANSITION: no cut;
    Interleaved / Linked strands are pre-deformed by alpha toward the end
    lanes that cut would give them (_wave_paths vcuts).
    junction_ends: tips of this band's ends PUSHED into a host wall (model
    _split_wall_systems): a Chained Loop ends there in its junction motif."""
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
    polys, infos, Ts, frames = [], [], [], []
    refs = [f for f in (phase_ref or []) if f.get('type') == sp['type'] and f.get('key') == _sp_key(sp)]
    self_ref = any(f.get('stack_self') for f in phase_ref or [])   # (M3: this layer IS the stack reference)
    for st in strips:
        _cancel_check()
        if st.hi - st.lo < 0.5 * bead:
            continue
        ws_ = sorted(2 * st.half(st.lo + (st.hi - st.lo) * k / 8) + bead for k in range(1, 8))
        T = ws_[len(ws_) // 2]                       # the run's true (median) thickness
        Ts.append(T)
        H0 = max(0.0, 0.5 * T - 0.5 * bead)
        phased = sp['type'] in ('interleaved', 'linked', 'chain')
        ref = _ref_map(st, refs, thick) if refs and phased else None
        p = None
        chain_vc = _virtual_cuts(st, transitions, thick) if transitions and sp['type'] == 'chain' else []
        held = None
        # M4 (2026-10-12): a cross-Z transition on a Chained Loop run is
        # composed on top of the mapped stack reference inside
        # _chain_path_ref (_chain_adj), with silent fall-back to the own
        # fit when the composed deformation would leave the wall. A closed
        # ring layer is also tried on the stack reference (M4) and falls
        # back when the mapping does not cover one full turn.
        tried_ref_vc = bool(chain_vc) and ref is not None and ref[0].get('stack')
        tried_ref_ring = (sp['type'] == 'chain' and st.run.cycle and ref is not None
                          and ref[0].get('stack'))
        if sp['type'] == 'chain':
            if ref is not None:
                p, info = _chain_path_ref(st, sp, T, H0, env, ref, chain_vc)
            if p is None:
                if tried_ref_vc:
                    held = ('a cross-Z transition next to an Assembly opening pushed a chain loop '
                            'past the wall face on the stack reference; the chain is pre-deformed '
                            'by its own fit instead')
                elif tried_ref_ring:
                    held = ('a closed Chained Loop ring could not close on the stack reference '
                            '(parity / coverage mismatch); fitted whole on its own loop count')
                p, info = _chain_path(st, sp, T, H0, env, chain_vc, junction_ends)
                info['phase_ref'] = False        # (no reference could be used: own fit)
        else:
            vc = _virtual_cuts(st, transitions, thick) if transitions and sp['type'] in ('interleaved', 'linked') else []
            p, info = _wave_paths(st, sp, H0, env, ref, vc)
        info['phase_ref'] = ref is not None and info.get('phase_ref', True)
        # WHICH GENERATOR drew this run (M3, never silent): 'stack' — the
        # family's STACK reference (layer_design.stack_reference: one pattern
        # for every layer of the stack); 'layer-ref' — the same layer's uncut
        # pattern (a cut variant, the previous pipeline); 'layer' — fitted on
        # its own (the previous per-layer generator), with the reason
        if phased:
            if info['phase_ref']:
                info['generator'] = 'stack' if ref[0].get('stack') else 'layer-ref'
            elif self_ref and not held:
                info['generator'] = 'stack'               # (the reference's own fit)
            else:
                info['generator'] = 'layer'
                stack_refs = [f for f in refs if f.get('stack')]
                info['generator_reason'] = held or (
                    'a closed Chained Loop ring could not close on the layer reference; '
                    'fitted whole on its own loop count'
                    if ref is not None and sp['type'] == 'chain' and st.run.cycle else
                    'the stack reference could not be applied to this run' if ref is not None else
                    'no run of the stack reference lies on this run (the architecture differs from the '
                    'family reference here)' if stack_refs else
                    'no stack reference for this Wall System (parameters differ from the family root, '
                    'or previous generator selected)')
        polys += [_simplify(pl) for pl in p if len(pl) >= 2]
        infos.append(info)
        fr = _frame(st, sp, info)
        if info['phase_ref']:
            fr['derived'] = True                 # (drawn from a reference: its own fit is not what printed)
        frames.append(fr)
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
                            any(not st.run.cycle and 'E' in (st.run.a[0], st.run.b[0]) for st in strips)),
           'phase_ref_runs': sum(1 for i in infos if i.get('phase_ref')),
           'generators': {g: sum(1 for i in infos if i.get('generator') == g)
                          for g in ('stack', 'layer-ref', 'layer') if any(i.get('generator') == g for i in infos)},
           'generator_reasons': sorted({i['generator_reason'] for i in infos if i.get('generator_reason')}),
           'frames': frames}
    if sp['type'] == 'chain':
        # COMPLEXITY of the chain (the Designer warns before it gets slow): the
        # loops / points generated, the fitted pitch range, and DENSE when
        # loops are closer than one bead (they overlap: the route gets large)
        ps = [i['pitch'] for i in infos if 'pitch' in i]
        rep['chain'] = {'loops': round(sum(i.get('loops', 0) for i in infos), 2),
                        'points': sum(len(p) for p in polys),
                        'pitch_min': min(ps) if ps else None, 'pitch_max': max(ps) if ps else None,
                        'dense': bool(ps) and min(ps) < bead,
                        'corner_motifs': sum(i.get('corner_motifs', 0) for i in infos),
                        'junction_motifs': sum(i.get('junction_motifs', 0) for i in infos)}
    for i in infos:
        i.pop('_ref', None)
    # J2 (2026-10-12): PARALLEL lane joining across strips. For a region
    # with multiple strips meeting at junction NODES (circle grazing a
    # rectangle: one strip for the R loop, one for the C arc, short
    # bridges across the overlap), each strip's own lanes are OPEN at
    # the junction nodes and the lane endpoints of adjacent strips sit
    # about half a bead apart (lane width differs by strip orientation).
    # Pair every open endpoint with the NEAREST open endpoint from
    # ANOTHER polyline within one bead and splice both into one chain —
    # the junction stitching closes the lanes into continuous closed
    # loops across runs. Only applied to Parallel; Interleaved / Linked
    # / Chain are untouched (their junction crossings are intentional).
    if sp['type'] == 'parallel' and polys:
        polys = _join_parallel_at_junctions(polys, bead)
    return polys, rep


def _join_parallel_at_junctions(polys, bead):
    """Stitch open Parallel polyline endpoints that sit within one bead of
    each other at a junction node into continuous closed loops. Pure
    O(n²) over endpoints; a region typically has at most a few dozen
    open-ended pieces so the cost is negligible compared to the
    generator itself."""
    pieces = [list(p) for p in polys if len(p) >= 2]
    # exact _join first (shares exact vertices along a strip's own corners)
    pieces = _join(pieces)
    tol = max(10.0 * bead, 1.0)            # pair endpoints within ~10 beads
    # the lane-end offsets at a junction node differ by at most half a
    # wall thickness; a generous tol lets the stitcher reach them (the
    # closest-pair ranking inside the loop prevents cross-junction
    # pairings when the real neighbour sits closer)
    changed = True
    while changed:
        changed = False
        # collect open endpoints
        opens = []                         # (piece index, end 'start'/'end', Vec2)
        for i, pl in enumerate(pieces):
            if len(pl) < 2:
                continue
            if pl[0].dist(pl[-1]) < 1e-6:
                continue                   # already closed loop
            opens.append((i, 'start', pl[0]))
            opens.append((i, 'end',   pl[-1]))
        # find the closest pair across DIFFERENT pieces
        best = None
        for i_a in range(len(opens)):
            ia, ea, qa = opens[i_a]
            for i_b in range(i_a + 1, len(opens)):
                ib, eb, qb = opens[i_b]
                if ia == ib:
                    continue
                d = qa.dist(qb)
                if d >= tol:
                    continue
                if best is None or d < best[0]:
                    best = (d, ia, ea, ib, eb)
        if best is None:
            break
        _, ia, ea, ib, eb = best
        a = pieces[ia]
        b = pieces[ib]
        # orient so that we concatenate a → b
        if ea == 'start':
            a = list(reversed(a))
        if eb == 'end':
            b = list(reversed(b))
        merged = a[:-1] + b                # drop the duplicate seam vertex pair
        # replace piece a with the merge, drop piece b
        new_pieces = []
        for k, pl in enumerate(pieces):
            if k == ia:
                new_pieces.append(merged)
            elif k == ib:
                continue
            else:
                new_pieces.append(pl)
        pieces = new_pieces
        changed = True
    # final pass: close any piece whose two ends sit within one bead
    out = []
    for pl in pieces:
        if len(pl) < 2:
            continue
        if pl[0].dist(pl[-1]) < 1e-6:
            out.append(pl)
            continue
        if pl[0].dist(pl[-1]) < tol:
            pl.append(pl[0])
        out.append(pl)
    return out


# ---------------------------------------------------------------------------
# PHASE REFERENCE (Assembly profiles, 2026-10-08): a profile CUTS the pattern
# of the uncut wall; it never re-fits it. The uncut generation reports one
# frame per run; a run of a cut variant is mapped onto the reference run it
# lies on (its centre line projected onto the reference centre line: s →
# s_ref, and the across direction compared: y → ±y) and is drawn with the
# REFERENCE period / phase / lanes / loop stations. Only the run's NEW free
# ends (cut faces, a tapered end) are solved locally: the strands fade into
# the canonical end lanes there and the normal cap turns join them
# (Interleaved / Linked), or the chain stops at the last whole loop before
# the cut and leads out to the cap (Chained Loop).

def _virtual_cuts(st, transitions, thick):
    """[(s of face a, s of face b, alpha)] of the virtual openings on this run."""
    out = []
    zs = _samples(0.0, st.L, 0.5)
    mids = [st.frame(z)[0] for z in zs]

    def s_of(P):
        k = min(range(len(zs)), key=lambda i: mids[i].dist(P))
        return zs[k] if mids[k].dist(P) < 0.5 * thick else None
    for (pa, pb, _na, _nb, al) in transitions or []:
        sa, sb = s_of(pa), s_of(pb)
        if sa is None or sb is None or al <= 0:
            continue
        if st.run.cycle and abs(sb - sa) > 0.5 * st.L:     # the opening spans the seam
            sa, sb = (sa, sb + st.L) if sa > sb else (sb, sa + st.L)
        out.append((min(sa, sb), max(sa, sb), al))
    return out


def _sp_key(sp):
    return repr(sorted((k, v) for k, v in sp.items() if k != 'seam_hint'))


def _frame(st, sp, info):
    ss = _samples(0.0, st.L, 1.0)
    cen, acr = [], []
    for z in ss:
        m, u, _ = st.frame(z)
        cen.append((m.x, m.y))
        acr.append((u.x, u.y))
    out = {'type': sp['type'], 'key': _sp_key(sp), 'L': st.L, 'cycle': st.run.cycle, 'ss': ss,
           'centre': cen, 'across': acr, 'lo': st.lo, 'hi': st.hi}
    out.update(info.get('_ref') or {})
    return out


def _ref_map(st, refs, thick):
    """(frame, s → s_ref, s_ref → s, across sign) of the reference run this
    run lies on, or None."""
    n = max(4, int(math.ceil((st.hi - st.lo) / 2.0)))
    zs = [st.lo + (st.hi - st.lo) * k / n for k in range(n + 1)]
    pts = [st.frame(z) for z in zs]
    # samples within one thickness of a FREE end (an opening's cut face) are
    # not judged: the run's frames there are askew — at a CORNER close to the
    # cut they jump to the corner bisector, off the reference centre line —
    # (2026-10-10 regression: an array's cut face near a corner rejected the
    # whole remaining ring, which was then re-fitted on its own: phase lost
    # all around the wall, remote support findings). The interior decides.
    free_lo = not st.run.cycle and st.run.a[0] == 'E'
    free_hi = not st.run.cycle and st.run.b[0] == 'E'
    judged = [k for k, z in enumerate(zs) if not ((free_lo and z < st.lo + thick) or (free_hi and z > st.hi - thick))]
    if len(judged) < 2:
        judged = list(range(len(zs)))
    judged0 = judged
    for f in refs:
        cen, fss, L = f['centre'], f['ss'], f['L']
        segs = list(range(len(cen) - 1)) + ([len(cen) - 1] if f['cycle'] else [])
        # a STACK reference (M3) may cover only PART of the run: a band that
        # reaches further on this layer (a junction moved, a wall extended)
        # draws the reference where it lies on it and continues it beyond —
        # a contiguous block of at least half the judged samples, the
        # samples off the reference only at the run's ends
        partial = bool(f.get('stack'))
        judged = judged0
        sr, ok, sign_votes = [], True, 0
        for (m, u, _) in pts:
            best = None
            for i in segs:
                a, b = cen[i], cen[(i + 1) % len(cen)]
                dx, dy = b[0] - a[0], b[1] - a[1]
                l2 = dx * dx + dy * dy
                t = 0.0 if l2 < 1e-18 else max(0.0, min(1.0, ((m.x - a[0]) * dx + (m.y - a[1]) * dy) / l2))
                d = math.hypot(a[0] + t * dx - m.x, a[1] + t * dy - m.y)
                if best is None or d < best[0]:
                    s1 = fss[i + 1] if i + 1 < len(fss) else L
                    best = (d, fss[i] + t * (s1 - fss[i]), i)
            if best is None or (best[0] > 0.25 * thick and (len(sr) in judged or partial)):
                if not partial:
                    ok = False
                    break
                sr.append(None)
                continue
            sr.append(best[1])
            ua = f['across'][best[2]]
            sign_votes += 1 if u.x * ua[0] + u.y * ua[1] >= 0 else -1
        if not ok:
            continue
        if partial:
            on = [k for k in judged if sr[k] is not None]
            if len(on) < 2 or len(on) < 0.5 * len(judged) or on != list(range(on[0], on[-1] + 1)):
                continue
            if len(on) < len(judged):
                judged = on
                lo_k, hi_k = on[0], on[-1]
                sr = [x if lo_k <= k <= hi_k else None for k, x in enumerate(sr)]
        if any(x is None for x in sr):
            # (beyond the covered block: extrapolated later from its β)
            keep = [k for k, x in enumerate(sr) if x is not None]
        else:
            keep = None
        if f['cycle']:                               # unwrap across the reference seam
            prev = None
            for k in range(len(sr)):
                if sr[k] is None:
                    continue
                if prev is not None:
                    while sr[k] - sr[prev] > 0.5 * L:
                        sr[k] -= L
                    while sr[k] - sr[prev] < -0.5 * L:
                        sr[k] += L
                prev = k
        jr = [sr[k] for k in judged]
        d = [b - a for a, b in zip(jr, jr[1:])]
        if not d or not (all(x > 0 for x in d) or all(x < 0 for x in d)):
            continue
        z0, z1 = zs[judged[0]], zs[judged[-1]]
        if abs(abs(jr[-1] - jr[0]) - (z1 - z0)) > 0.05 * (z1 - z0) + 0.5:
            continue                                 # not the same length along the wall
        # the SAME wall axis: s_ref = σ·s + β exactly (arc length is
        # preserved); β from the interior samples (the frames near a cap are
        # askew), median for robustness
        sg = 1.0 if d[0] > 0 else -1.0
        # s_ref = σ·s + β(s): σ = ±1 (arc length is preserved), β measured
        # along the run — constant on a straight wall, stepping by a fraction
        # of an inch at each corner (the cut piece's and the ring's chordal
        # axes round a corner slightly differently). Samples within a
        # thickness of a free end (frames askew near a cap) take the nearest
        # interior β; then a running median (3).
        bs = [None if r is None else r - sg * z for z, r in zip(zs, sr)]
        inner = [k for k, z in enumerate(zs) if st.lo + thick <= z <= st.hi - thick]
        if keep is not None:
            # (partial cover: β only from the covered block; held flat beyond)
            inner = [k for k in inner if bs[k] is not None] or keep
        if inner:
            for k in range(len(bs)):
                if k not in inner:
                    bs[k] = bs[min(inner, key=lambda j: abs(j - k))]
        bs = [sorted(bs[max(0, k - 1):k + 2])[len(bs[max(0, k - 1):k + 2]) // 2] for k in range(len(bs))]

        def fwd(z, sg=sg, zs=zs, bs=bs):
            return sg * z + _interp_ext_flat(zs, bs, z)

        def inv(r, sg=sg, zs=zs, bs=bs):
            z = (r - bs[0]) * sg
            for _ in range(3):                      # β varies slowly: a fixed point in 2–3 steps
                z = (r - _interp_ext_flat(zs, bs, z)) * sg
            return z
        # an ORIGINAL free end of the reference run keeps the reference's end
        # station (the same end motif); a NEW end (a cut) keeps its own
        # (identified by the run's TIP — its end face — which maps onto the
        # reference run's tip only for an original end; the end STATIONS may
        # differ by a search step, a cut close to the end must not match)
        for side in (() if st.run.cycle else ('lo', 'hi')):
            if (st.run.a if side == 'lo' else st.run.b)[0] != 'E':
                continue
            tip = fwd(0.0 if side == 'lo' else st.L)
            for r_tip, rs in ((0.0, f['lo']), (L, f['hi'])):
                if abs(tip - r_tip) < 0.25 * st.bead:
                    z = inv(rs)
                    if 0.0 <= z <= st.L:
                        if side == 'lo':
                            st.lo = z
                        else:
                            st.hi = z
                        # the reference judged that station square; this
                        # run's own frame samples (another grid) may not
                        st.__dict__.setdefault('_ref_square', set()).add(round(z, 9))
                    break
        return f, fwd, inv, (1.0 if sign_votes >= 0 else -1.0)
    return None


def _interp_ext_flat(xs, ys, x):
    """Piecewise-linear interpolation (xs increasing), held flat past the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    return _interp_ext(xs, ys, x)


def _interp_ext(xs, ys, x):
    """Piecewise-linear interpolation (xs increasing), linearly extended past the ends."""
    if x <= xs[0]:
        k = 0
    elif x >= xs[-1]:
        k = len(xs) - 2
    else:
        k = max(0, min(len(xs) - 2, bisect.bisect_right(xs, x) - 1))
    x0, x1, y0, y1 = xs[k], xs[k + 1], ys[k], ys[k + 1]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
