"""LIFTS + ISLANDS (2026-10-09) — the lift-aware schedule of a whole print.

The machine has ~18 in of usable Z travel; after a LIFT the whole XY frame
is raised (a major, manual operation) and printing never returns below it.
Inside a lift, separate ISLANDS are printed upward as far as their geometry
allows instead of alternating between them on every layer.

LIFT BOUNDARIES (`lift_bounds`): whole layers, greedy from the bottom — a
lift takes every next layer while (top of that layer − bottom of the lift's
first layer) ≤ the preferred lift height (≤ the maximum Z travel). Layer
heights that do not divide the lift height leave the remainder to the next
lift (18 in / 1.4 in → 12 layers = 16.8 in). A single layer taller than the
lift height is an error.

COMPONENTS (`layer_components`): per layer, the printed beads that are
PHYSICALLY one piece — centrelines closer than one bead width (beads touch /
fuse), from the ACTUAL resolved printable geometry (variants, cuts,
transforms, derived designs), never from Wall System membership.

ORDER (per lift, `schedule`): piece X (layer k) must come after piece Y
(layer j < k, same lift) when they are closer in plan than one bead width
+ the head clearance — Y is under / beside where the nozzle prints X (its
support, or geometry the nozzle would otherwise have to descend through).
A piece with nothing below it in layer k−1 (unsupported / starting on a
header) waits for the whole layer k−1. Edges always point up, so this is a
DAG; any topological order is physically valid. Among valid orders the
GREEDY picks, deterministically:
  1. a ready piece directly ABOVE the one just printed (keep building the
     current island upward; the nearest if several);
  2. else the lowest ready piece of the same CONNECTED GROUP (pieces that
     join somewhere in this lift: the other side of a later merge);
  3. else the lowest ready piece anywhere;
  ties: the piece holding the design's manual start, then the nearest to
  the nozzle, then a stable key. So two islands merging at 12 in print as
  A 0–12, B 0–12, A+B 12–18 — and a merged piece is never ready before
  every piece below it is printed.

STAGES (`stages`): maximal chains of pieces with exactly one link to the
layer below and above (links = bead overlap between adjacent layers), named
for the UI / plan: independent islands A, B, … (in print order per lift), a
MERGE "A+B", a split branch "A.1", "A.2".
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

DEFAULT_MAX_Z_TRAVEL = 18.0     # in: usable Z travel of the machine
DEFAULT_LIFT_HEIGHT = 18.0      # in: preferred lift height (≤ max Z travel)
DEFAULT_HEAD_CLEARANCE = 1.0    # in: beyond one bead width, geometry this close must be printed first


class LiftError(ValueError):
    pass


@dataclass(frozen=True)
class LiftSettings:
    max_z_travel: float = DEFAULT_MAX_Z_TRAVEL
    lift_height: float = DEFAULT_LIFT_HEIGHT
    head_clearance: float = DEFAULT_HEAD_CLEARANCE

    def __post_init__(self):
        for k in ('max_z_travel', 'lift_height'):
            v = getattr(self, k)
            if not (isinstance(v, (int, float)) and v > 0 and math.isfinite(v)):
                raise LiftError(f'{k.replace("_", " ")} must be greater than 0')
        if not (self.head_clearance >= 0 and math.isfinite(self.head_clearance)):
            raise LiftError('head clearance must be ≥ 0')
        if self.lift_height > self.max_z_travel + 1e-9:
            raise LiftError(f'the lift height ({self.lift_height:g} in) cannot exceed the maximum Z travel '
                            f'({self.max_z_travel:g} in)')

    @staticmethod
    def from_dict(d) -> 'LiftSettings':
        d = d or {}
        mz = float(d.get('max_z_travel', DEFAULT_MAX_Z_TRAVEL))
        return LiftSettings(mz, float(d.get('lift_height', min(DEFAULT_LIFT_HEIGHT, mz))),
                            float(d.get('head_clearance', DEFAULT_HEAD_CLEARANCE)))

    def to_dict(self) -> dict:
        return {'max_z_travel': self.max_z_travel, 'lift_height': self.lift_height,
                'head_clearance': self.head_clearance}


def lift_bounds(layers, settings: LiftSettings) -> list:
    """[(first, last)] layer index ranges (inclusive) of the lifts.
    `layers`: [(z_bottom, z_top)] bottom-up."""
    out, i, n = [], 0, len(layers)
    H = settings.lift_height
    while i < n:
        base = layers[i][0]
        if layers[i][1] - base > H + 1e-9:
            raise LiftError(f'layer {i + 1} ({layers[i][1] - base:g} in) is taller than the lift height ({H:g} in)')
        j = i
        while j + 1 < n and layers[j + 1][1] - base <= H + 1e-9:
            j += 1
        out.append((i, j))
        i = j + 1
    return out


# ---------------------------------------------------------------------------
# geometry: proximity of polylines (pure Python, grid accelerated)
# ---------------------------------------------------------------------------

def _seg_dist2(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        ex, ey = p[0] - a[0], p[1] - a[1]
        return ex * ex + ey * ey
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    ex, ey = a[0] + t * dx - p[0], a[1] + t * dy - p[1]
    return ex * ex + ey * ey


def _ring(pts, closed):
    pts = [tuple(q) for q in pts]
    return pts + [pts[0]] if closed and len(pts) > 1 else pts


def samples(pts, step):
    """Points along a polyline (its vertices + every `step`)."""
    out = []
    for a, b in zip(pts, pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / step)))
        for i in range(n):
            t = i / n
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    if pts:
        out.append(pts[-1])
    return out


class SegIndex:
    """Segments in a uniform grid (long segments split to the cell size)."""

    def __init__(self, cell):
        self.cell = cell
        self.grid = defaultdict(list)
        self.segs = []

    def add(self, owner, pts):
        c = self.cell
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(math.ceil(L / c)))
            for i in range(n):
                p = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                q = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
                k = len(self.segs)
                self.segs.append((owner, p, q))
                cells = {(math.floor(p[0] / c), math.floor(p[1] / c)), (math.floor(q[0] / c), math.floor(q[1] / c))}
                for key in cells:
                    self.grid[key].append(k)

    def near_owners(self, p, r, skip=None):
        """{owner: d²} of segments within r of p (owners failing `skip`)."""
        c, r2 = self.cell, r * r
        cx, cy = math.floor(p[0] / c), math.floor(p[1] / c)
        span = int(math.ceil(r / c))
        out = {}
        for i in range(cx - span, cx + span + 1):
            for j in range(cy - span, cy + span + 1):
                for k in self.grid.get((i, j), ()):
                    o, a, b = self.segs[k]
                    if skip is not None and skip(o):
                        continue
                    d2 = _seg_dist2(p, a, b)
                    if d2 < r2 and d2 < out.get(o, math.inf):
                        out[o] = d2
        return out


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return False
        if b < a:
            a, b = b, a
        self.p[b] = a
        return True


def layer_components(polylines, bead) -> list:
    """Groups (lists of polyline indices, sorted, in order of their first
    member) of polylines that are PHYSICALLY one piece: centrelines closer
    than one bead width somewhere. `polylines`: [{'pts', 'closed'}]."""
    n = len(polylines)
    if n <= 1:
        return [list(range(n))]
    rings = [_ring(p['pts'], p.get('closed')) for p in polylines]
    uf = _UF(n)
    groups = n
    # shared vertices (junction nodes): one piece at once
    seen = {}
    for i, R in enumerate(rings):
        for q in (R[0], R[-1]) if R else ():
            key = (round(q[0], 4), round(q[1], 4))
            if key in seen:
                groups -= uf.union(seen[key], i)
            else:
                seen[key] = i
    if groups > 1:
        r = bead * 0.999
        idx = SegIndex(max(r, 1e-6))
        for i, R in enumerate(rings):
            idx.add(i, R)
        step = max(min(bead / 4.0, 0.75), 1e-3)
        for i, R in enumerate(rings):
            if groups == 1:
                break
            for p in samples(R, step):
                hit = idx.near_owners(p, r, skip=lambda o, i=i: uf.find(o) == uf.find(i))
                for o in sorted(hit):
                    groups -= uf.union(i, o)
                if groups == 1:
                    break
    by = defaultdict(list)
    for i in range(n):
        by[uf.find(i)].append(i)
    return sorted((sorted(v) for v in by.values()), key=lambda v: v[0])


# ---------------------------------------------------------------------------
# scheduling one lift
# ---------------------------------------------------------------------------

class Piece:
    """One physical component of one layer (world coordinates)."""

    def __init__(self, layer, comp, rings, members, step):
        self.layer, self.comp, self.rings, self.members, self.step = layer, comp, rings, members, step
        xs = [q[0] for R in rings for q in R]
        ys = [q[1] for R in rings for q in R]
        self.bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
        self._pts = None

    @property
    def key(self):
        return (self.layer, self.comp)

    @property
    def pts(self):                     # samples (proximity, nozzle distance) — only when needed
        if self._pts is None:
            self._pts = [p for R in self.rings for p in samples(R, self.step)]
        return self._pts


def make_piece(layer, comp, rings, members, step) -> Piece:
    return Piece(layer, comp, rings, members, step)


def _bbox_near(a, b, r):
    return not (a[2] + r < b[0] or b[2] + r < a[0] or a[3] + r < b[1] or b[3] + r < a[1])


def _near(X: Piece, idx: SegIndex, r) -> dict:
    """{owner: min distance} of the segments in `idx` within r of X."""
    out = {}
    for p in X.pts:
        for o, d2 in idx.near_owners(p, r).items():
            if d2 < out.get(o, math.inf):
                out[o] = d2
    return {o: math.sqrt(d2) for o, d2 in out.items()}


@dataclass
class LiftGraph:
    pieces: dict               # key → Piece
    by_layer: dict             # layer → [keys]
    preds: dict                # key → set(keys) that must be printed first
    down: dict                 # key → set(keys) in layer−1 it overlaps (bead contact)
    up: dict                   # key → set(keys) in layer+1 overlapping it
    group: dict                # key → connected group id (pieces joining anywhere in the lift)


def lift_graph(layer_pieces: dict, bead, clearance) -> LiftGraph:
    """`layer_pieces`: {global layer: [Piece]} of ONE lift (consecutive
    layers). Links (bead contact between adjacent layers) and the ordering
    edges (closer than bead + clearance, any lower layer of the lift)."""
    layers = sorted(layer_pieces)
    pieces = {P.key: P for L in layers for P in layer_pieces[L]}
    by_layer = {L: [P.key for P in layer_pieces[L]] for L in layers}
    preds = {k: set() for k in pieces}
    down = {k: set() for k in pieces}
    up = {k: set() for k in pieces}
    r1, r2 = bead * 0.999, bead + clearance
    single = all(len(by_layer[L]) == 1 for L in layers)
    idx = {}

    def index(L):
        if L not in idx:
            ix = SegIndex(max(r2, 1e-6))
            for P in layer_pieces[L]:
                for R in P.rings:
                    ix.add(P.comp, R)
            idx[L] = ix
        return idx[L]
    anc = {k: set() for k in pieces}
    for n, L in enumerate(layers):
        if n == 0:
            continue
        below = layers[n - 1]
        for P in layer_pieces[L]:
            k = P.key
            if single:                                    # one piece per layer: the column
                Q = layer_pieces[below][0]
                preds[k].add(Q.key)
                if _bbox_near(P.bbox, Q.bbox, r1):         # (the order is forced: no exact test needed)
                    down[k].add(Q.key)
                    up[Q.key].add(k)
            else:
                cand = [Q for Q in layer_pieces[below] if _bbox_near(P.bbox, Q.bbox, r2)]
                d = _near(P, index(below), r2) if cand else {}
                for Q in layer_pieces[below]:
                    dist = d.get(Q.comp, math.inf)
                    if dist < r2:
                        preds[k].add(Q.key)
                    if dist < r1:
                        down[k].add(Q.key)
                        up[Q.key].add(k)
                if not preds[k]:                          # nothing under it: after the whole layer below
                    preds[k] |= set(by_layer[below])
                # geometry further below that is not already under it (another island the
                # nozzle would have to descend beside / through)
                for m in range(n - 2, -1, -1):
                    Lm = layers[m]
                    a = set().union(*(anc[q] | {q} for q in preds[k]))
                    others = [Q for Q in layer_pieces[Lm] if Q.key not in a and _bbox_near(P.bbox, Q.bbox, r2)]
                    if not others:
                        continue
                    d = _near(P, index(Lm), r2)
                    for Q in others:
                        if d.get(Q.comp, math.inf) < r2:
                            preds[k].add(Q.key)
            anc[k] = set().union(*(anc[q] | {q} for q in preds[k])) if preds[k] else set()
    uf = _UF(len(pieces))
    keys = sorted(pieces)
    pos = {k: i for i, k in enumerate(keys)}
    for k in keys:
        for q in down[k]:
            uf.union(pos[k], pos[q])
    roots = {}
    group = {}
    for k in keys:
        group[k] = roots.setdefault(uf.find(pos[k]), len(roots))
    return LiftGraph(pieces, by_layer, preds, down, up, group)


def schedule(G: LiftGraph, pos=None, manual=None, visit=None) -> list:
    """Deterministic print order (keys) of one lift (see the module doc).
    `pos`: nozzle (x, y) before it. `manual`: set of keys holding a manual
    design start (preferred among ties). `visit(key)` is called as each
    piece is chosen (the planner routes it there) and returns where the
    nozzle ended — the next choice is made from there."""
    manual = manual or set()
    left = {k: len(v) for k, v in G.preds.items()}
    succ = defaultdict(list)
    for k, ps in G.preds.items():
        for q in ps:
            succ[q].append(k)
    ready = {k for k, c in left.items() if c == 0}
    order, cur = [], None

    def dist(k):
        if pos is None:
            return 0.0
        P = G.pieces[k]
        return min(math.hypot(p[0] - pos[0], p[1] - pos[1]) for p in P.pts[::4] or P.pts) if P.pts else 0.0

    def rank(k):
        return (0 if k in manual else 1, round(dist(k), 6), k)
    while ready:
        ups = [k for k in ready if cur is not None and k in G.up[cur]]
        if len(ready) == 1:
            nxt = next(iter(ready))
        elif ups:
            nxt = min(ups, key=rank)
        else:
            same = [k for k in ready if cur is not None and G.group[k] == G.group[cur]]
            pool = same or list(ready)
            low = min(k[0] for k in pool)
            nxt = min((k for k in pool if k[0] == low), key=rank)
        ready.discard(nxt)
        order.append(nxt)
        cur = nxt
        P = G.pieces[nxt]
        if visit is not None:
            pos = visit(nxt) or pos
        elif P.rings and P.rings[-1]:
            pos = P.rings[-1][-1]
        for s in succ[nxt]:
            left[s] -= 1
            if left[s] == 0:
                ready.add(s)
    if len(order) != len(G.pieces):
        raise LiftError('cyclic print order (internal error)')
    return order


def stages(G: LiftGraph, order) -> tuple:
    """({key: stage number} (0-based, per lift, in print order), [stage
    info {kind 'island' | 'merge' | 'split', parents, name}]): stages are
    chains of pieces with exactly one link down and up."""
    sid, info = {}, []
    for k in order:
        d = G.down[k]
        if len(d) == 1:
            (q,) = d
            if len(G.up[q]) == 1 and q in sid:
                sid[k] = sid[q]
                continue
        kind = 'island' if not d else ('merge' if len(d) > 1 else 'split')
        parents = sorted({sid[q] for q in d if q in sid})
        sid[k] = len(info)
        info.append({'kind': kind, 'parents': parents})
    # names: independent islands A, B, …; merges join their parents; split branches .1, .2
    names, letters, branch = [], 0, defaultdict(int)
    for s in info:
        if s['kind'] == 'island':
            n, x = '', letters
            while True:
                n = chr(65 + x % 26) + n
                x = x // 26 - 1
                if x < 0:
                    break
            letters += 1
        elif s['kind'] == 'merge':
            n = '+'.join(names[p] for p in s['parents'])
        else:
            p = s['parents'][0]
            branch[p] += 1
            n = f'{names[p]}.{branch[p]}'
        names.append(n)
    for s, n in zip(info, names):
        s['name'] = n
    return sid, info
