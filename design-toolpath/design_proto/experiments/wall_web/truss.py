"""
CANDIDATE A — ADAPTIVE TRUSS PHASE FIELD (experimental).

The web is a Warren-type truss between the two skins whose LOCAL geometry
comes from structural parameters instead of a wavelength:

    θ      brace angle to the wall direction (centre-line tangent)
    b      bond length: the web runs ALONG the skin for b at every contact
           (a flat contact, not a point V)
    r      minimum turning radius of the bead (every vertex filleted)
    D      maximum unsupported SKIN span (per face, along that face)
    a_min  congestion floor: smallest advance per crossing on the inner face

For a cavity of local width w (rail to rail, i.e. the faces minus the
contact separation) one crossing advances

    a(s) = w(s) / tan θ + b               (straight wall: pitch = w cot θ + b)

clamped by D (on the OUTER face of a bend) and a_min (on the INNER face).
This defines a PHASE FIELD along each wall run,

    φ(s) = ∫ ds / a(s)                     (crossings per unit length)

Integer phases are contacts, alternating faces. Between FIXED ANCHORS
(junction ends, dead ends, corners — production's segment boundaries)
the integer count n = round(Δφ) is chosen and the stations are placed at
φ⁻¹(k Δφ / n): a TWO-POINT boundary-value problem per segment, never an
initial-value propagation — a change inside one segment cannot move a
station outside it.

Reuses the production skeleton / face correspondence (common.Corridor);
only the per-run web generation differs. Single pass per run, no junction
pairing / doubling (raw structural web; routing is measured separately).
"""
from __future__ import annotations

import math

from common import Corridor, Vec2, BEAD, CONTACT, fillet, WL


DEFAULTS = dict(theta=45.0, bond=BEAD, r_turn=0.5 * BEAD, d_skin=40.0, a_min=BEAD)


def _cumulative(xs, f):
    out = [0.0]
    for a, b, fa, fb in zip(xs, xs[1:], f, f[1:]):
        out.append(out[-1] + 0.5 * (fa + fb) * (b - a))
    return out


def _invert(xs, cum, target):
    import bisect
    k = bisect.bisect_left(cum, target)
    if k <= 0:
        return xs[0]
    if k >= len(cum):
        return xs[-1]
    c0, c1 = cum[k - 1], cum[k]
    f = (target - c0) / (c1 - c0) if c1 > c0 else 0.0
    return xs[k - 1] + f * (xs[k] - xs[k - 1])


HYSTERESIS = 0.2        # cross-Z: keep a segment's count until |Δφ − n| > 1 + this (then ± a pair)
RELAX = 0.05            # cross-Z: per-layer relaxation of carried phases towards uniform


def _adapt(q, n):
    """Carried normalised phases [0, …, 1] → n crossings. Crossings come and
    go in PAIRS (a single pass alternates faces: one extra crossing would
    swap the face of every later landing — a global change): a pair is
    inserted in the widest gap (at its thirds) / the most crowded adjacent
    pair is removed; only the stations next to the change are smoothed,
    then everything relaxes slightly towards uniform."""
    q = list(q)
    changed = []
    while len(q) - 1 < n:
        i = max(range(len(q) - 1), key=lambda k: q[k + 1] - q[k])
        a, b = q[i], q[i + 1]
        add = [a + (b - a) / 3, a + 2 * (b - a) / 3] if n - (len(q) - 1) >= 2 else [0.5 * (a + b)]
        q[i + 1:i + 1] = add
        changed.append(i + 1)
    while len(q) - 1 > n and len(q) > 3:
        if (len(q) - 1) - n >= 2 and len(q) > 4:
            j = min(range(1, len(q) - 2), key=lambda k: q[k + 2] - q[k - 1])
            del q[j:j + 2]
        else:
            j = min(range(1, len(q) - 1), key=lambda k: q[k + 1] - q[k - 1])
            del q[j]
        changed.append(j)
    for c in changed:
        for _ in range(3):
            for i in range(max(1, c - 3), min(len(q) - 1, c + 4)):
                q[i] = 0.5 * (q[i - 1] + q[i + 1])
    m = len(q) - 1
    return [q[k] + RELAX * (k / m - q[k]) if 0 < k < m else q[k] for k in range(m + 1)]


def plan(rings, theta=None, bond=None, r_turn=None, d_skin=None, a_min=None, contact=CONTACT, corridor=None,
         track=None):
    p = dict(DEFAULTS)
    for k, v in dict(theta=theta, bond=bond, r_turn=r_turn, d_skin=d_skin, a_min=a_min).items():
        if v is not None:
            p[k] = v
    C = corridor or Corridor(rings, contact)
    tan_t = math.tan(math.radians(p['theta']))
    polys, runs_rep = [], []
    track_out = {}
    for ri, run in enumerate(C.runs):
        L = run.length
        lo, hi = (0.0, L) if run.cycle else C.span(run)
        cs = [x for x in C.corners(run) if lo < x < hi]
        if run.cycle:
            anchors = cs + [cs[0] + L] if cs else [0.0, L]
        else:
            anchors = [lo] + cs + [hi]

        # phase density on a 1 in grid between the outermost anchors
        a0, a1 = anchors[0], anchors[-1]
        n_g = max(4, int(math.ceil((a1 - a0) / 1.0)))
        xs = [a0 + (a1 - a0) * k / n_g for k in range(n_g + 1)]
        rho = []
        for x in xs:
            w = max(0.25, C.width(run, x % L if run.cycle else x))
            inner, outer = C.face_speed(run, x % L if run.cycle else min(x, L))
            adv = w / tan_t + p['bond']
            adv = min(adv, p['d_skin'] / (2.0 * max(outer, 1e-3)))      # skin span bound (outer face)
            adv = max(adv, p['a_min'] / max(inner, 0.2))                 # congestion floor (inner face)
            rho.append(1.0 / adv)
        cum = _cumulative(xs, rho)

        # integer crossings per anchor segment (two-point BVP)
        seg_phi, ns = [], []
        # CANONICAL orientation (the skeleton's run direction / face labels are
        # arbitrary and may flip between neighbouring layers): direction from
        # the lexicographically smaller end, start face = the LEFT face
        rev, side_left = _canonical(C, run)
        prev = (track or {}).get(ri)
        if prev is not None and len(prev) != len(anchors) - 1:
            prev = None                          # anchor structure changed: plan afresh
        if prev is not None and rev:
            prev = [{'n': d['n'], 'q': [1.0 - x for x in reversed(d['q'])]} for d in reversed(prev)]
        for si, (x0, x1) in enumerate(zip(anchors, anchors[1:])):
            ph = _invert_phi(xs, cum, x1) - _invert_phi(xs, cum, x0)
            seg_phi.append(ph)
            n = max(1, int(round(ph)))
            if prev is not None:
                # CROSS-Z: keep the count (hysteresis); change it only by
                # PAIRS so every other landing keeps its face
                n0 = prev[si]['n']
                n = n0 if abs(ph - n0) < 1.0 + HYSTERESIS else max(1, n0 + 2 * int(round((ph - n0) / 2)))
            ns.append(n)
        if run.cycle and len(ns) == 1 and ns[0] % 2:
            # a loop with no corner must return to its start face: even count
            ns[0] += 1 if seg_phi[0] > ns[0] else -1
            if ns[0] < 2:
                ns[0] = 2
        # normalised phase of every station inside its segment: uniform, or
        # (CROSS-Z) the previous layer's, with a LOCAL insertion / removal
        # where the count changed and a slow relaxation towards uniform
        seg_q = []
        for si, n in enumerate(ns):
            if prev is None:
                q = [k / n for k in range(n + 1)]
            else:
                q = _adapt([0.0] + prev[si]['q'] + [1.0], n)
            seg_q.append(q)
        # landings segment by segment. Each segment starts on its CANONICAL
        # face (the left face at its canonical start), so a count change in
        # one segment never swaps the faces of another; where neighbouring
        # segments disagree, the anchor (a corner) gets a CORNER BRACE —
        # both faces landed at the same station, as in production.
        land = []
        for (x0, x1), q in zip(zip(anchors, anchors[1:]), seg_q):
            n = len(q) - 1
            f0, f1 = _invert_phi(xs, cum, x0), _invert_phi(xs, cum, x1)
            st = [x0] + [_invert(xs, cum, f0 + (f1 - f0) * q[k]) for k in range(1, n)] + [x1]
            first = side_left if not rev else (side_left + n) % 2
            seg_l = [(st[k], (first + k) % 2) for k in range(n + 1)]
            if land and land[-1][1] == seg_l[0][1]:
                land += seg_l[1:]
            else:
                land += seg_l                    # (a corner brace when land is not empty)
        if run.cycle and land[-1][1] != land[0][1]:
            land.append((land[-1][0], land[0][1]))   # brace at the seam corner
        stations = [x for x, _ in land]
        sides = [sd for _, sd in land]

        # key vertices in metric strip coordinates (s, y), y ∈ {0, w(s)}
        def w_at(x):
            return max(0.25, C.width(run, x % L if run.cycle else x))
        jA = (not run.cycle) and run.a[0] == 'J'
        jB = (not run.cycle) and run.b[0] == 'J'
        keys = []
        m = len(stations)
        for k, (x, sd) in enumerate(zip(stations, sides)):
            prev_gap = x - stations[k - 1] if k > 0 else None
            next_gap = stations[k + 1] - x if k + 1 < m else None
            h = 0.5 * p['bond']
            for g in (prev_gap, next_gap):
                if g is not None:
                    h = min(h, 0.3 * g)
            left = x - h if prev_gap is not None else x
            right = x + h if next_gap is not None else x
            for xx in ([left, right] if right - left > 1e-6 else [x]):
                keys.append((xx, sd * w_at(xx)))
        keys = _dedupe(keys)
        strip = fillet([Vec2(x, y) for x, y in keys], p['r_turn'])

        # map to the plane
        pts = []
        for a, b in zip(strip, strip[1:]):
            n = max(1, int(math.ceil(abs(b.x - a.x) / 1.0)))
            for j in range(0 if not pts else 1, n + 1):
                q = a.lerp(b, j / n)
                x = q.x
                w = w_at(x)
                pts.append(C.at(run, x % L if run.cycle else x, q.y / w))
        # junction ends land EXACTLY on the junction corner (as production:
        # the route joins the faces there)
        if jA:
            f = C.face(run, sides[0], 0.0)
            pts = [f] + [q for q in pts[1:] if q.dist(f) > 1.0]
        if jB:
            f = C.face(run, sides[-1], L)
            pts = [q for q in pts[:-1] if q.dist(f) > 1.0] + [f]
        # dead ends: a last leg onto the end face (cap), like production's cap V
        for at_start, node in ((True, run.a), (False, run.b)):
            if run.cycle or node[0] != 'E':
                continue
            x = stations[0] if at_start else stations[-1]
            cpt = _cap(C, run, x, toward_end=not at_start)
            if cpt is not None:
                if at_start:
                    pts = [cpt] + pts
                else:
                    pts = pts + [cpt]
        polys.append(pts)
        tr_ = [{'n': len(q) - 1, 'q': q[1:-1]} for q in seg_q]
        if rev:
            tr_ = [{'n': d['n'], 'q': [1.0 - x for x in reversed(d['q'])]} for d in reversed(tr_)]
        track_out[ri] = tr_
        runs_rep.append({'length': round(L, 1), 'cycle': run.cycle, 'anchors': len(anchors),
                         'crossings': sum(ns), 'segment_phase': [round(x, 2) for x in seg_phi],
                         'segment_counts': ns, 'stations': stations, 'sides': sides})
    return polys, {'params': p, 'runs': runs_rep, 'track': track_out}


def _canonical(C, run):
    """(reversed?, index of the left face) of a run in its canonical
    direction. Cycles keep the skeleton's direction."""
    L = run.length
    if run.cycle:
        rev = False
    else:
        p0, p1 = C.geo.centre(run, 0.0), C.geo.centre(run, L)
        rev = (round(p1.x, 3), round(p1.y, 3)) < (round(p0.x, 3), round(p0.y, 3))
    sm = 0.5 * L
    t = C.geo.centre(run, min(L, sm + 1.0)) - C.geo.centre(run, max(0.0, sm - 1.0))
    if rev:
        t = t * -1.0
    f0, f1 = C.face(run, 0, sm), C.face(run, 1, sm)
    d = f0 - f1
    return rev, (0 if t.x * d.y - t.y * d.x > 0 else 1)


def _invert_phi(xs, cum, x):
    """φ at centre-line position x (linear interpolation of the cumulative)."""
    import bisect
    k = bisect.bisect_left(xs, x)
    if k <= 0:
        return cum[0]
    if k >= len(xs):
        return cum[-1]
    f = (x - xs[k - 1]) / (xs[k] - xs[k - 1]) if xs[k] > xs[k - 1] else 0.0
    return cum[k - 1] + f * (cum[k] - cum[k - 1])


def _dedupe(keys):
    out = []
    for k in keys:
        if not out or abs(k[0] - out[-1][0]) > 1e-6 or abs(k[1] - out[-1][1]) > 1e-6:
            out.append(k)
    return out


def _cap(C, run, x, toward_end):
    """The end face point straight on along the centre line, pulled back by
    the contact separation (None if there is no cap within 3 thicknesses)."""
    L = run.length
    th = C.thick
    c = C.geo.centre(run, x)
    back = C.geo.centre(run, max(0.0, x - th) if toward_end else min(L, x + th))
    d = c - back
    if d.length() < 1e-6:
        return None
    d = d * (1.0 / d.length())
    hit = WL._ray_hit(C.sk, c, d, 3.0 * th)
    if hit is None:
        return None
    q = hit - d * C.contact
    return q if q.dist(c) > 0.5 else None
