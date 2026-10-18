"""
CONSTRUCTION STATE — DEPOSITED LANDINGS (WALL_ARCHITECTURE.md §Z.0; Construction
through Z, milestone 1: the simple Skin + Web stacking gate). Pure functions on
plain dicts; no geometry is built here.

THE STATE of a layer is the web's LANDINGS as deposited: {'landings': [{'x',
'y', 'f', 'w', 'e', 't'}]} in that layer's design coordinates — a point on a
face rail, its face `f` (+1 / −1: left / right of the host wall's own
direction), its host wall `w`, `e` = an edge landing (where a segment meets a
corner motif or a cap: architecture, not lattice), `t` = the ring transfer
rides it. Layer N's deposited landings are the physical starting state of
layer N + 1. Nothing else is carried: no pitch, phase, segment, run or frame.

ONE STEP (advance) for every supported run of the new layer (the planner's
topology: its two face rails, its segments between corner motifs / caps, the
forced face at each motif edge, Pass A's stations):

1. RIGID HOST MOTION. The host wall's samples on the two layers (world
   coordinates) are fitted by a similarity. Scale 1 and residual ≈ 0 → a
   genuine rigid motion (a translation, a rotation): the landings move with
   it. Otherwise (a scale, any non-rigid change) the landings keep their
   world position — the change of territory is never distributed through
   them.
2. SURVIVE. Each landing is projected onto ITS face's rail of the new layer
   (perpendicular accommodation to the moved face). A landing never slides
   along the wall. A projection farther than Maximum Overhang is NOT hidden by
   re-planning: the landing is still carried and the bead-level checker
   reports the failure (the architecture moved farther than printable).
3. CONSUME / EXPOSE at each segment edge (a cap end, a corner motif), from
   the edge landing below to the edge now:
     - edge moved INWARD: the landing nearest the edge is consumed when its
       cell to the edge falls under MERGE pitches — one at a time, the rest
       unchanged;
     - edge moved OUTWARD: the edge landing it left behind is PROMOTED to an
       ordinary landing (it is deposited below, so the stitch to it stacks)
       once the cell beyond the last survivor reaches PROMOTE pitches;
     - a gap wider than FILL pitches (territory with nothing below) is filled
       at the pitch from the nearest survivor.
   The pitch is the surviving construction's own spacing (Pass A's only for
   territory with no construction).
4. PARITY is never forced: faces are carried; where a face meets a motif's
   forced face the planner draws a LANE cell (the pass follows the face).
5. TRANSFER: the transfer flag stays on its landing; a consumed one passes to
   the survivor nearest the middle of the run's longest segment.
"""
from __future__ import annotations

import math

MERGE = 0.5           # × pitch: a landing whose cell to an inward-moving edge is under this is consumed
PROMOTE = 0.9         # × pitch: an outward-moving edge promotes its old edge landing once the cell reaches this
FILL = 1.6            # × pitch: a gap wider than this (nothing below) is filled at the pitch
DUP = 0.05            # in: landings this close along a rail are one station (both faces of a two-pass station)
REACH = 4.0           # × Maximum Overhang: a landing below this close to a rail belongs to that run


# ---- coordinates -------------------------------------------------------------------

def _world(q, place):
    if not place:
        return (float(q[0]), float(q[1]))
    k, tx, ty = float(place.get('scale', 1.0)), float(place.get('tx', 0.0)), float(place.get('ty', 0.0))
    return (k * float(q[0]) + tx, k * float(q[1]) + ty)


def _design(q, place):
    if not place:
        return q
    k, tx, ty = float(place.get('scale', 1.0)), float(place.get('tx', 0.0)), float(place.get('ty', 0.0))
    return ((q[0] - tx) / k, (q[1] - ty) / k)


def rigid_fit(P, Q, tol=1e-4):
    """(cos, sin, tx, ty) of the RIGID motion taking samples P to Q, or None
    when the motion is not rigid (a scale, a change of shape)."""
    n = len(P)
    if n < 2 or n != len(Q):
        return None
    cpx, cpy = sum(p[0] for p in P) / n, sum(p[1] for p in P) / n
    cqx, cqy = sum(q[0] for q in Q) / n, sum(q[1] for q in Q) / n
    sxx = sxy = var = 0.0
    for (px, py), (qx, qy) in zip(P, Q):
        ax, ay, bx, by = px - cpx, py - cpy, qx - cqx, qy - cqy
        sxx += ax * bx + ay * by
        sxy += ax * by - ay * bx
        var += ax * ax + ay * ay
    if var <= 1e-12:
        return None
    if abs(math.hypot(sxx, sxy) / var - 1.0) > 1e-6:
        return None                                   # scaled: not rigid
    th = math.atan2(sxy, sxx)
    c, s = math.cos(th), math.sin(th)
    tx, ty = cqx - (c * cpx - s * cpy), cqy - (s * cpx + c * cpy)
    if max(math.hypot(c * px - s * py + tx - qx, s * px + c * py + ty - qy) for (px, py), (qx, qy) in zip(P, Q)) > tol:
        return None                                   # the shape changed
    return (c, s, tx, ty)


# ---- rails ---------------------------------------------------------------------------

def _rail_at(rail, s):
    """The point of a rail ([[s, x, y], …], s increasing) at s."""
    if s <= rail[0][0]:
        return rail[0][1], rail[0][2]
    for (s0, x0, y0), (s1, x1, y1) in zip(rail, rail[1:]):
        if s <= s1:
            t = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0)
    return rail[-1][1], rail[-1][2]


def _project(rail, q):
    """(s, distance) of the rail point nearest q."""
    best = (math.inf, 0.0)
    for (s0, x0, y0), (s1, x1, y1) in zip(rail, rail[1:]):
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((q[0] - x0) * dx + (q[1] - y0) * dy) / L2))
        d = math.hypot(q[0] - x0 - t * dx, q[1] - y0 - t * dy)
        if d < best[0]:
            best = (d, s0 + t * (s1 - s0))
    return best[1], best[0]


def _stations(lst):
    """Merge landings within DUP along the rail into one station (kept sorted)."""
    out = []
    for item in sorted(lst):
        if out and item[0] - out[-1][0] <= DUP:
            out[-1] = (out[-1][0], out[-1][1], out[-1][2] or item[2])
        else:
            out.append(item)
    return out


# ---- one run -------------------------------------------------------------------------

def _run(run, below, h):
    L, cyc, f0, w = float(run['L']), bool(run['cycle']), int(run['face0']), run.get('wall')
    lone = run['kind'] == 'lone'
    rails = run['rails']
    items = []                                        # (s, side, edge, transfer) on THIS layer's rails
    for ld in below:
        sd = 0 if int(ld['f']) == f0 else 1
        s, d = _project(rails[sd], (ld['x'], ld['y']))
        if d <= REACH * max(h, 1.0):                  # (this run's landing; beyond h the checker reports it)
            items.append((s, sd, bool(ld.get('e')), bool(ld.get('t'))))
    out, seg_int = [], []
    for seg in run['segs']:
        a, b = float(seg['a']), float(seg['b'])

        def rep(s):
            return [s - L, s, s + L] if cyc else [s]
        inter = [(x, sd, t) for s, sd, e, t in items if not e for x in rep(s) if a + 1e-6 < x < b - 1e-6]
        pa = [(float(x), int(sd), False) for x, sd in seg['pa']]
        if lone:
            inter = [(x, 0, t) for x, _, t in _stations(inter)]
            pa = [(x, 0, False) for x, _, _ in _stations(pa)]
        else:
            inter.sort()
        if not inter:
            inter = list(pa)                          # territory with no construction below: new construction
        else:
            gaps = sorted(y[0] - x[0] for x, y in zip(inter, inter[1:]))
            p = gaps[len(gaps) // 2] if len(gaps) >= 2 else (b - a) / (len(pa) + 1)

            def edge_below(xe, forced):
                best = None
                for s, sd, e, t in items:
                    if not e or (forced is not None and not lone and sd != forced):
                        continue
                    for x in rep(s):
                        d = abs(x - xe)
                        if d <= 0.5 * p + h and (best is None or d < best[0]):
                            best = (d, x, sd)
                return best
            # the low edge
            ea = edge_below(a, seg.get('fa'))
            ma = None if ea is None else a - ea[1]                 # > 0: the edge moved inward
            if ma is None or ma > 1e-6:
                while inter and inter[0][0] - a < MERGE * p:
                    inter.pop(0)
            elif ma < -1e-6 and inter and a < ea[1] < inter[0][0] and inter[0][0] - ea[1] >= PROMOTE * p \
                    and (lone or ea[2] != inter[0][1]):
                inter.insert(0, (ea[1], ea[2] if not lone else 0, False))
            while inter and inter[0][0] - a > FILL * p:
                inter.insert(0, (inter[0][0] - p, 0 if lone else 1 - inter[0][1], False))
            # the high edge
            eb = edge_below(b, seg.get('fb'))
            mb = None if eb is None else eb[1] - b                 # > 0: the edge moved inward
            if mb is None or mb > 1e-6:
                while inter and b - inter[-1][0] < MERGE * p:
                    inter.pop()
            elif mb < -1e-6 and inter and inter[-1][0] < eb[1] < b and eb[1] - inter[-1][0] >= PROMOTE * p \
                    and (lone or eb[2] != inter[-1][1]):
                inter.append((eb[1], eb[2] if not lone else 0, False))
            while inter and b - inter[-1][0] > FILL * p:
                inter.append((inter[-1][0] + p, 0 if lone else 1 - inter[-1][1], False))
            # a hole inside (a landing lost to overhang): new landings at the pitch
            k = 0
            while k < len(inter) - 1:
                if inter[k + 1][0] - inter[k][0] > FILL * p:
                    inter.insert(k + 1, (inter[k][0] + p, 0 if lone else 1 - inter[k][1], False))
                k += 1
        seg_int.append((a, b, inter))

        def emit(x, sd, e=False, t=False):
            px, py = _rail_at(rails[sd], x % L if cyc else x)
            out.append({'x': round(px, 6), 'y': round(py, 6), 'f': f0 if sd == 0 else -f0, 'w': w,
                        'e': e, 't': t})
        for x, sd in ((a, seg.get('fa')), (b, seg.get('fb'))):
            for sd_ in ((0, 1) if sd is None else (int(sd),)):
                emit(x, sd_, e=True)
        for x, sd, t in inter:
            for sd_ in ((0, 1) if lone else (sd,)):
                emit(x, sd_, t=t and (not lone or (f0 if sd_ == 0 else -f0) > 0))
    # the transfer: on its own landing while it survives; else (per face) the
    # landing nearest the middle of the run's longest segment
    for side in ((0,) if lone and f0 > 0 else (1,) if lone else (0, 1)):
        f = f0 if side == 0 else -f0                  # (a two-pass station lands both faces: one flag, face +1)
        ts = [ld for ld in out if ld['t'] and ld['f'] == f]
        if ts:
            for ld in ts[1:]:
                ld['t'] = False
            continue
        if not seg_int:
            continue
        a, b, inter = max(seg_int, key=lambda q: q[1] - q[0])
        mid = 0.5 * (a + b)
        cands = [x for x, sd, _ in inter if lone or sd == side]
        if not cands:
            continue
        x = min(cands, key=lambda q: (abs(q - mid), q))
        px, py = _rail_at(rails[side], x % L if cyc else x)
        for ld in out:
            if not ld['e'] and ld['f'] == f and abs(ld['x'] - round(px, 6)) < 1e-9 and abs(ld['y'] - round(py, 6)) < 1e-9:
                ld['t'] = True
                break
    return out


# ---- the chain step ---------------------------------------------------------------------

def advance(prev_state, prev_topo, topo, prev_place=None, place=None, h=1.5):
    """The state of a layer from the layer below's deposited landings and this
    layer's topology (`wall_lattice.plan(topology_only)` merged by the model).
    prev_place / place: the two layers' InstanceTransform dicts (None =
    identity). h: Maximum Overhang. None when the layer has no supported run."""
    runs = (topo or {}).get('runs') or []
    if not runs:
        return None
    walls_now = topo.get('walls') or {}
    walls_prev = (prev_topo or {}).get('walls') or {}
    below, rig = [], {}
    for ld in (prev_state or {}).get('landings') or []:
        w = ld.get('w')
        if w not in rig:
            A, B = walls_prev.get(w), walls_now.get(w)
            rig[w] = rigid_fit([_world(q, prev_place) for q in A['pts']], [_world(q, place) for q in B['pts']]) \
                if A and B else None
        q = _world((ld['x'], ld['y']), prev_place)
        if rig[w] is not None:                        # a genuine rigid motion carries the construction
            c, s, tx, ty = rig[w]
            q = (c * q[0] - s * q[1] + tx, s * q[0] + c * q[1] + ty)
        q = _design(q, place)
        below.append(dict(ld, x=q[0], y=q[1]))
    out = []
    for run in runs:
        out += _run(run, [ld for ld in below if ld.get('w') == run.get('wall')], h)
    return {'landings': out} if out else None


def initial(topo) -> dict:
    """The state of the first layer: Pass A's own landings."""
    return advance(None, None, topo)
