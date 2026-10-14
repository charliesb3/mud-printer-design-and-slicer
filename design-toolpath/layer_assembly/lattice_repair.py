"""
LOCAL LATTICE SUPPORT REPAIR (M4, 2026-10-11).

After Generate Wall Systems has produced every layer (M3 stack-aware
references), print preparation walks the stack BOTTOM-UP and, where the
AUTHORITATIVE bead-level rule (support.py — the very predicate, the same
Maximum Overhang, nothing relaxed) finds an internal strand unsupported,
tries a small, deterministic, bounded set of LOCAL repairs before the
finding is reported:

  CONTACT SHIFT — the unsupported stretch of the strand (and a blend margin
  of 1, 2 or 4 beads, smallest first) moves toward the nearest bead
  centreline of the (already repaired) layer below, just far enough to sit
  within the allowed lateral offset; vertices are faded in / out smoothly
  (a local change of the motif's contact geometry). Strand ends and every
  vertex another strand also uses stay where they are, so the routing graph
  keeps its topology.

A candidate is ACCEPTED only when, within its window:
  * every sample of the moved strand is supported (support.py's rule);
  * every moved point stays inside the layer's ARCHITECTURAL envelope with
    half a bead to its faces (the architecture is never changed);
  * the strand comes no closer to the layer's other strands than before
    (no new material accumulation);
  * nothing of the layer ABOVE that was supported becomes unsupported.
Otherwise the best candidate that strictly reduces the unsupported length
and passes every other check is kept as PARTIAL (the finding stays
reported); else the strand is left exactly as generated.

Eligible strands: internal structure only — routing role 'lattice' (Skin +
Web / Adaptive Truss web) and 'wall_system' (generated Wall System paths:
Interleaved, Linked, Chained Loop, Parallel). Skins, caps and jambs
(construction detail at the wall faces) are never moved.

Bounded: per unsupported run at most 6 candidates (3 margins × smoothed /
raw, smoothed and smallest first); each evaluated on its window only; the
whole pass is O(layers × samples).
"""
from __future__ import annotations

import math
from dataclasses import replace

from .support import (_SegIndex, _Material, _sample_dirs, _clusters, _principal, covered_width, BRIDGE_FACTOR,
                      SAMPLE_FACTOR, LINK_FACTOR, MIN_SPAN_FACTOR, DEFAULT_MAX_OVERHANG, DEFAULT_BEAD)

ELIGIBLE_ROLES = ('lattice', 'wall_system')
MARGINS = (1.0, 2.0, 4.0)          # blend margins, in beads (smallest change first)
STRATEGY = 'contact_shift'
SMOOTH_PASSES = 4                  # Laplacian passes over the moved vertices (a smooth motif)
MAX_TURN = math.radians(25.0)      # a repaired window turns no sharper than this (or its original's sharpest)


def _closest(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return (ax + t * dx, ay + t * dy)


def _nearest_point(idx, p):
    best, q = math.inf, None
    for a, b in idx.near(p):
        c = _closest(p, a, b)
        d = math.dist(p, c)
        if d < best:
            best, q = d, c
    return q, best


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _cum(pts):
    out = [0.0]
    for a, b in zip(pts, pts[1:]):
        out.append(out[-1] + math.dist(a, b))
    return out


class _Rule:
    """support.py's bead-level predicate for one layer pair."""

    def __init__(self, lower_pts, b, bl, h_max):
        h = min(max(h_max, 0.0), b)
        self.b, self.bl, self.o = b, bl, b - h
        self.allow = max(0.0, (b + bl) / 2 - self.o)
        self.idx = _SegIndex(lower_pts, BRIDGE_FACTOR * b + bl)
        # (covered_width only ever counts lower beads within (b + bl) / 2 of
        # p: a grid of that size gives the identical width, far faster)
        self.cidx = _SegIndex(lower_pts, (b + bl) / 2 + 1e-6)

    def ok(self, p, dirn, alt):
        idx = self.idx
        if idx.within(p, self.allow + 1e-9):
            return True
        d = idx.dist(p)
        if d >= (self.b + self.bl) / 2:
            return False
        ov = covered_width(p, dirn, self.b, self.cidx, self.bl / 2)
        if alt is not None:
            ov = max(ov, covered_width(p, alt, self.b, self.cidx, self.bl / 2))
        return ov > 1e-9 and ov >= self.o - 1e-9

    def unsupported(self, pts, lo=None, hi=None):
        """[(sample point, arc position)] of the unsupported samples (only
        within [lo, hi] along the strand when given)."""
        out = []
        cum = _cum(pts)
        for p, dirn, k, alt in _sample_dirs(pts, SAMPLE_FACTOR * self.b):
            s = cum[k] + math.dist(pts[k], p)
            if lo is not None and not (lo - 1e-9 <= s <= hi + 1e-9):
                continue
            if not self.ok(p, dirn, alt):
                out.append((p, s))
        return out


def _count_clusters(per_strand, b):
    """support.py's clustering over {strand: [(point, arc)]} unsupported
    samples: how many findings (≥ 2 beads of span) they make."""
    pts, runs, run_of = [], [], []
    for j in sorted(per_strand):
        prev = None
        for p, s in per_strand[j]:
            if prev is None or s - prev > 1.5 * SAMPLE_FACTOR * b + 1e-9:
                runs.append([])
            runs[-1].append(p)
            pts.append(p)
            run_of.append(len(runs) - 1)
            prev = s
    n = 0
    for members in _clusters(pts, LINK_FACTOR * b):
        cp = [pts[j] for j in members]
        _, _, span, _ = _principal(cp, [runs[r] for r in sorted({run_of[j] for j in members})])
        n += span >= MIN_SPAN_FACTOR * b
    return n


def _finding_count(rule, placed):
    """How many findings support.py's clustering makes of the layer."""
    pts, runs, run_of = [], [], []
    for P in placed:
        cur = None
        for p, dirn, k, alt in _sample_dirs(P, SAMPLE_FACTOR * rule.b):
            if rule.ok(p, dirn, alt):
                cur = None
                continue
            if cur is None:
                cur = []
                runs.append(cur)
            cur.append(p)
            pts.append(p)
            run_of.append(len(runs) - 1)
    n = 0
    for members in _clusters(pts, LINK_FACTOR * rule.b):
        cp = [pts[j] for j in members]
        _, _, span, _ = _principal(cp, [runs[r] for r in sorted({run_of[j] for j in members})])
        n += span >= MIN_SPAN_FACTOR * rule.b
    return n


def _finding_points(rule, placed):
    """The unsupported samples of the whole layer that FORM FINDINGS under
    support.py's own clustering (≥ 2 beads of span within 4-bead links) —
    shorter unsupported stretches are not findings and are left alone."""
    pts, runs, run_of = [], [], []
    for P in placed:
        cur = None
        for p, dirn, k, alt in _sample_dirs(P, SAMPLE_FACTOR * rule.b):
            if rule.ok(p, dirn, alt):
                cur = None
                continue
            if cur is None:
                cur = []
                runs.append(cur)
            cur.append(p)
            pts.append(p)
            run_of.append(len(runs) - 1)
    keep = set()
    for members in _clusters(pts, LINK_FACTOR * rule.b):
        cp = [pts[j] for j in members]
        _, _, span, _ = _principal(cp, [runs[r] for r in sorted({run_of[j] for j in members})])
        if span >= MIN_SPAN_FACTOR * rule.b:
            keep.update((round(p[0], 6), round(p[1], 6)) for p in cp)
    return keep


def _runs(samples, gap):
    """Contiguous unsupported stretches [(s0, s1, points)] (samples closer than `gap` along the strand)."""
    out = []
    for p, s in samples:
        if out and s - out[-1][1] <= gap:
            out[-1][1] = s
            out[-1][2].append(p)
        else:
            out.append([s, s, [p]])
    return [tuple(r) for r in out]


def _subdivide(pts, tags, s_lo, s_hi, step):
    """Insert vertices (≤ step apart) on the segments overlapping [s_lo, s_hi]."""
    cum = _cum(pts)
    P, T = [pts[0]], [tags[0] if tags else None]
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        if cum[k + 1] >= s_lo and cum[k] <= s_hi:
            n = max(1, int(math.ceil(math.dist(a, b) / step)))
            for j in range(1, n):
                t = j / n
                P.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
                T.append(tags[k] if tags else None)
        P.append(b)
        T.append(tags[k + 1] if tags else None)
    return P, (T if tags else None)


class _Faces:
    """Distance to the architectural faces + point-in-material of an envelope layer."""

    def __init__(self, rings, bead):
        self.mat = _Material(rings, max(bead, 1.0))
        self.idx = _SegIndex(rings, 4 * bead)

    def clear(self, p, r):
        return self.mat.inside(p) and not self.idx.within(p, r - 1e-9)


def _max_turn(pts):
    """The sharpest turn (radians) between consecutive segments."""
    best = 0.0
    segs = [(a, b) for a, b in zip(pts, pts[1:]) if math.dist(a, b) > 1e-9]
    for (a, b), (_, c) in zip(segs, segs[1:]):
        u = (b[0] - a[0], b[1] - a[1])
        v = (c[0] - b[0], c[1] - b[1])
        cr, dt = u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]
        best = max(best, abs(math.atan2(cr, dt)))
    return best


def _crowding(pts, others, r):
    """Samples of pts closer than r to another strand of the layer."""
    if others is None:
        return 0
    return sum(1 for p, _, _, _ in _sample_dirs(pts, r) if others.within(p, r))


def repair_layer(lower_pts, upper, inst, env_rings, above_pts, b, bl, h_max, eligible):
    """Repair one placed layer. upper: [{'pts' (design coords), 'src'…}];
    lower_pts / above_pts: placed polylines of the layers below / above
    (above: as generated). → (new polylines, [repair records])."""
    T = inst.transform
    fwd = lambda q: T.apply(*q)                                    # noqa: E731
    inv = lambda q: ((q[0] - T.tx) / T.scale, (q[1] - T.ty) / T.scale)   # noqa: E731
    rule = _Rule(lower_pts, b, bl, h_max)
    placed = [[fwd(q) for q in pl['pts']] for pl in upper]
    faces = _Faces([[fwd(q) for q in r] for r in env_rings], b) if env_rings else None
    # vertices shared with another strand (and every strand end): fixed
    fixed = {}
    for j, P in enumerate(placed):
        for q in (P[0], P[-1]):
            fixed.setdefault((round(q[0], 6), round(q[1], 6)), set()).add(j)
    out, records = list(upper), []
    above_rule_lower = None
    flagged = _finding_points(rule, placed)
    if not flagged:
        return out, records
    unsup = {i: rule.unsupported(Q) for i, Q in enumerate(placed)}     # (per strand; updated on each repair)
    n_findings = _count_clusters(unsup, b)
    for j, pl in enumerate(upper):
        if j not in eligible:
            continue
        P = placed[j]
        bad = rule.unsupported(P)
        if not bad:
            continue
        runs_ = [r for r in _runs(bad, 2 * SAMPLE_FACTOR * b)
                 if any((round(p[0], 6), round(p[1], 6)) in flagged for p in r[2])]
        if not runs_:
            continue
        others = _SegIndex([Q for i, Q in enumerate(placed) if i != j], b)
        tags = pl.get('src')
        for s0, s1, pts_bad in reversed(runs_):   # (from the end: arc positions before it stay valid)
            n_before = len(pts_bad)
            centre = pts_bad[len(pts_bad) // 2]
            best = None
            for m, smooth in [(m_, sm) for m_ in MARGINS for sm in (True, False)]:
                cand = _contact_shift(P, tags, s0, s1, m * b, rule, faces, fixed, b, smooth)
                if cand is None:
                    continue
                NP, NT, lo, hi = cand
                after = rule.unsupported(NP, lo, hi)
                win = [q for q, s in zip(NP, _cum(NP)) if lo <= s <= hi]
                win0 = [q for q, s in zip(P, _cum(P)) if lo <= s <= hi]
                if _crowding(win, others, 0.5 * b) > _crowding(win0, others, 0.5 * b):
                    continue
                if _max_turn(win) > max(_max_turn(win0), MAX_TURN) + 1e-9:
                    continue                                   # (a kink: not a clean motif)
                if above_pts:
                    if above_rule_lower is None:
                        above_rule_lower = [Q for i, Q in enumerate(placed)]
                    if not _above_ok(above_pts, above_rule_lower, j, NP, win + win0, b, bl, h_max):
                        continue
                score = (len(after), m, not smooth)
                if best is None or score < best[0]:
                    best = (score, NP, NT, m, len(after), smooth, (lo, hi))
                if not after:
                    break
            if best is None or best[4] >= n_before:
                records.append({'layer': inst.index, 'centre': [round(c, 3) for c in centre], 'strand': j,
                                'strategy': None, 'resolved': False, 'before': n_before, 'after': n_before})
                continue
            _, NP, NT, m, n_after, smooth, _win = best
            # a repair inside a finding that several strands form may SPLIT it
            # (one strand's stretch linked its two halves): kept only when the
            # layer's findings do not increase
            lo_, hi_ = best[6]
            dl = _cum(NP)[-1] - _cum(P)[-1]                 # (the window's length change: arcs after it shift)
            new_j = [x for x in unsup[j] if x[1] < lo_ - 1e-9] + rule.unsupported(NP, lo_, hi_ + dl) + \
                [(p_, s_ + dl) for p_, s_ in unsup[j] if s_ > hi_ + 1e-9]
            n_new = _count_clusters({**unsup, j: new_j}, b)
            if n_new > n_findings:
                records.append({'layer': inst.index, 'centre': [round(c, 3) for c in centre], 'strand': j,
                                'strategy': None, 'resolved': False, 'before': n_before, 'after': n_before,
                                'note': 'the repair would split a finding in two'})
                continue
            n_findings = n_new
            unsup[j] = new_j
            old = {(round(q[0], 9), round(q[1], 9)) for q in P}
            mv = [q for q in NP if (round(q[0], 9), round(q[1], 9)) not in old]
            fl, ce = (lambda v: math.floor(v * 1000) / 1000), (lambda v: math.ceil(v * 1000) / 1000)
            box = [fl(min(q[0] for q in mv)), fl(min(q[1] for q in mv)),        # (rounded OUTWARD)
                   ce(max(q[0] for q in mv)), ce(max(q[1] for q in mv))] if mv else None
            P, tags = NP, NT
            placed[j] = P
            if above_rule_lower is not None:
                above_rule_lower[j] = P
            records.append({'layer': inst.index, 'centre': [round(c, 3) for c in centre], 'strand': j,
                            'strategy': STRATEGY, 'margin_beads': m, 'smoothed': smooth, 'resolved': n_after == 0,
                            'window': box,
                            'before': n_before, 'after': n_after})
        if P is not placed[j] or P != [fwd(q) for q in pl['pts']]:
            # (vertices that did not move keep their EXACT design coordinates —
            # no round trip through the placement)
            keep = {fq: q for fq, q in zip(([fwd(q) for q in pl['pts']]), pl['pts'])}
            npl = dict(pl, pts=[tuple(keep[q]) if q in keep else tuple(round(c, 9) for c in inv(q)) for q in P])
            if tags is not None:
                npl['src'] = tags
            out[j] = npl
    return out, records


def _contact_shift(P, tags, s0, s1, margin, rule, faces, fixed, b, smooth=True):
    """The strand with [s0 − margin, s1 + margin] faded toward the lower
    beads (vertices with nothing below within reach stay — a bridge cannot
    be pulled onto anything); smoothed (bounded Laplacian passes over the
    moved vertices only) so the motif stays a clean curve. None when nothing
    moves or a move would leave the architecture."""
    lo, hi = s0 - margin, s1 + margin
    NP, NT = _subdivide(P, tags, lo, hi, 0.25 * b)
    cum = _cum(NP)
    out = list(NP)
    movable = []
    for k, (q, s) in enumerate(zip(NP, cum)):
        if not (lo < s < hi) or k == 0 or k == len(NP) - 1:
            continue
        if (round(q[0], 6), round(q[1], 6)) in fixed:
            continue
        movable.append(k)
        w = 1.0 if s0 <= s <= s1 else _smooth((s - lo) / margin if s < s0 else (hi - s) / margin)
        if w <= 0:
            continue
        t, d = _nearest_point(rule.idx, q)
        if t is None or d <= rule.allow:
            continue                                           # (a bridge vertex / already supported)
        lim = 0.9 * rule.allow
        tgt = (t[0] + (q[0] - t[0]) * lim / d, t[1] + (q[1] - t[1]) * lim / d)
        out[k] = (q[0] + (tgt[0] - q[0]) * w, q[1] + (tgt[1] - q[1]) * w)
    if all(out[k] == NP[k] for k in movable):
        return None
    if smooth and len(movable) > 2:
        ms = set(movable)
        for _ in range(SMOOTH_PASSES):
            nxt = list(out)
            for k in movable:
                if k - 1 >= 0 and k + 1 < len(out) and (k - 1 in ms or k + 1 in ms):
                    nxt[k] = (0.5 * out[k][0] + 0.25 * (out[k - 1][0] + out[k + 1][0]),
                              0.5 * out[k][1] + 0.25 * (out[k - 1][1] + out[k + 1][1]))
            out = nxt
    if faces is not None:
        for k in movable:
            if out[k] != NP[k] and not faces.clear(out[k], 0.5 * b) and faces.clear(NP[k], 0.5 * b):
                return None                                    # (would leave the architecture)
    return out, NT, lo, hi


def _above_ok(above_pts, layer_now, j, NP, window_pts, b, bl, h_max):
    """No sample of the layer above near the window loses its support when
    strand j becomes NP."""
    if not window_pts:
        return True
    xs = [p[0] for p in window_pts]
    ys = [p[1] for p in window_pts]
    r = BRIDGE_FACTOR * b + bl
    box = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
    R = 2 * r                                       # (only segments that can reach a checked sample matter)

    def local(polys):
        out = []
        for Q in polys:
            cur = []
            for a, c in zip(Q, Q[1:]):
                if max(a[0], c[0]) >= box[0] - R and min(a[0], c[0]) <= box[2] + R and \
                        max(a[1], c[1]) >= box[1] - R and min(a[1], c[1]) <= box[3] + R:
                    if not cur:
                        cur = [a]
                    cur.append(c)
                elif cur:
                    out.append(cur)
                    cur = []
            if cur:
                out.append(cur)
        return out
    before = _Rule(local(layer_now), b, bl, h_max)
    after = _Rule(local([NP if i == j else Q for i, Q in enumerate(layer_now)]), b, bl, h_max)
    for Q in above_pts:
        for p, dirn, _, alt in _sample_dirs(Q, SAMPLE_FACTOR * b):
            if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]:
                if before.ok(p, dirn, alt) and not after.ok(p, dirn, alt):
                    return False
    return True


def repair_stack(res, layers, envelopes, assembly, cancelled=None):
    """{instance index: LayerGeometry} → ({index: repaired LayerGeometry (only
    the changed ones)}, [records]). Bottom-up: each layer is repaired against
    the REPAIRED layer below and never worsens the (generated) layer above."""
    h_max = float(getattr(assembly, 'max_overhang', DEFAULT_MAX_OVERHANG))
    insts = res.instances
    cur = {}                                                 # index → polylines in force (design coords)

    def polys(i):
        return cur.get(i, layers[i].polylines)

    def placed(i):
        T = insts[i].transform
        return [[T.apply(*q) for q in pl['pts']] for pl in polys(i)]
    changed, records = {}, []
    for k in range(1, len(insts)):
        if cancelled is not None and cancelled():
            break
        g = layers[k]
        strands = ((g.diagnostics or {}).get('routing') or {}).get('strands') or []
        eligible = {i for i, s in enumerate(strands) if (s or {}).get('role') in ELIGIBLE_ROLES}
        if not eligible:
            continue
        b = float(g.bead_width or DEFAULT_BEAD)
        bl = float(layers[k - 1].bead_width or DEFAULT_BEAD)
        rings = [pl['pts'] for pl in envelopes.get(k) or [] if pl.get('closed') or (pl.get('role') == 'ring')]
        above = placed(k + 1) if k + 1 < len(insts) else None
        new, recs = repair_layer(placed(k - 1), polys(k), insts[k], rings, above, b, bl, h_max, eligible)
        for r in recs:
            r['role'] = (strands[r['strand']] or {}).get('role') if r['strand'] < len(strands) else None
        records += recs
        if any(r['strategy'] for r in recs):
            cur[k] = new
            changed[k] = replace(g, polylines=new) if hasattr(g, '__dataclass_fields__') else _with_polys(g, new)
    return changed, records


def _with_polys(g, polys):
    import copy
    h = copy.copy(g)
    h.polylines = polys
    return h
