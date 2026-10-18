"""
PERSISTENT CONSTRUCTION THROUGH TOPOLOGY CHANGES (WALL_ARCHITECTURE.md §Z.1
invariants 6, 14–16; Z.10 — the Stage 4 topology review, 2026-10-17).

Surviving physical construction keeps its ancestry when the layer's
SEGMENTATION changes: a junction appearing or ceasing, a lens opening or
closing, a segment split, merged or renamed, a circle becoming a plain loop.
Those are events for the construction NEXT to them; a wall far from the
event keeps every station where it was. The tests measure the DEPOSITED
web: its landings on the walls' contact rails (both ends of every stitch
that crosses a wall's cavity), placed in world coordinates.

Two stacks reproduce the review's failures:
  - the golden circle + rectangle (`circrect`) with two transform centres
    and a Skin + Web zigzag: around layer 19 two junctions appear on the
    circle and the one segment that ran bottom wall → circle → top wall
    splits; later lens stubs and unresolved anchors come and go; the circle
    separates near the end;
  - the lobe building (a circle on the square's corner, the circle scaled
    about its own centre): a lens opens and closes on the lobe, the junction
    anchors vanish and the lobe becomes a plain loop.
"""
import copy
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, '..', '..', 'design_proto'), os.path.join(HERE, '..', '..'),
          os.path.join(HERE, '..', '..', 'design_proto', 'tests'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('LAYER_ASSEMBLY_WORKERS', '0')

pytestmark = [pytest.mark.integration, pytest.mark.xfail(strict=False, reason='Construction through Z: future stage beyond milestone 1 (WALL_ARCHITECTURE §Z.0 / Z.4) — junction / lens / loop topology events are out of scope of the simple Skin + Web stacking gate; the deposited-landing chain supports only lone straight walls and plain corner-motif loops')]

TOL = 0.6            # in: a landing is "where it was" within this (a fifth of a bead)


def _stack(designs, systems, asm):
    """[(instance, web polylines in world, anchors in world, semantic scale per source)]."""
    from layer_assembly import web
    from layer_assembly.source import layer_geometry, variant_request
    st = web._resolve_stack({'assembly': asm, 'designs': designs, 'design_ids': ['base'], 'wall_systems': systems})
    src, res = st['src'], st['res']
    out = []
    for inst in res.instances:
        g = layer_geometry(src, inst)
        T = inst.transform
        attrs = (g.diagnostics.get('routing') or {}).get('strands') or []
        pls = [[T.apply(x, y) for x, y in pl['pts']] for j, pl in enumerate(g.polylines)
               if (attrs[j] if j < len(attrs) else {}).get('role') == 'lattice']
        anchors = {a['id']: T.apply(a['x'], a['y']) for a in (g.diagnostics.get('identity') or {}).get('anchors') or []}
        tr = variant_request(inst)[1] or {}
        scales = {k: float(v[0]) for k, v in tr.items()}
        out.append((inst, pls, anchors, scales))
    return out


def _straight_landings(pls, axis, c, lo, hi, dmin=1.0, dmax=6.0):
    """Positions along a straight wall (its centreline at `c` across `axis`)
    of the landings of every stitch that crosses its cavity between lo and hi."""
    out = set()
    for pl in pls:
        for p, q in zip(pl, pl[1:]):
            dp, dq = (p[1] - c, q[1] - c) if axis == 'x' else (p[0] - c, q[0] - c)
            up, uq = (p[0], q[0]) if axis == 'x' else (p[1], q[1])
            if dmin < abs(dp) < dmax and dmin < abs(dq) < dmax and dp * dq < 0 and abs(up - uq) > 0.5 \
                    and lo <= up <= hi and lo <= uq <= hi:
                out.add(round(up, 3))
                out.add(round(uq, 3))
    return sorted(out)


def _circle_landings(pls, centre, r, dmin=1.0, dmax=6.0, accept=None):
    """Angles (radians) of the landings of every stitch crossing a circular
    wall's cavity (centreline radius r)."""
    out = set()
    for pl in pls:
        for p, q in zip(pl, pl[1:]):
            dp = math.hypot(p[0] - centre[0], p[1] - centre[1]) - r
            dq = math.hypot(q[0] - centre[0], q[1] - centre[1]) - r
            if dmin < abs(dp) < dmax and dmin < abs(dq) < dmax and dp * dq < 0 \
                    and (accept is None or (accept(p) and accept(q))):
                ap = math.atan2(p[1] - centre[1], p[0] - centre[0])
                aq = math.atan2(q[1] - centre[1], q[0] - centre[0])
                if abs(math.remainder(ap - aq, 2 * math.pi)) * r > 0.5:
                    out.add(ap)
                    out.add(aq)
    # (one landing per station: the two rails' landings of one station share its angle)
    merged = []
    for t in sorted(out):
        if not merged or (t - merged[-1]) * r > 0.3:
            merged.append(t)
    return merged


def _median_gap(xs):
    gaps = sorted(y - x for x, y in zip(xs, xs[1:]))
    return gaps[len(gaps) // 2] if gaps else 0.0


def _retained(a, b, interior):
    """Every landing of `a` that `interior` accepts has a landing of `b`
    within TOL, and vice versa. Returns the offenders."""
    gone = [x for x in a if interior(x) and not any(abs(x - y) <= TOL for y in b)]
    new = [x for x in b if interior(x) and not any(abs(x - y) <= TOL for y in a)]
    return gone, new


# ---- circle + rectangle, two centres --------------------------------------------------

def circrect_stack(layers=46, scale_in=0.6):
    from layer_assembly.tests.golden import cases
    P = cases.build_cases()['syn_transform_multi']
    designs = P['designer']['designs']
    systems = [{'id': 'S1', 'name': 'S1', 'type': 'skin_web', 'params': {}, 'members': ['R', 'C'],
                'web': {'pattern': 'zigzag', 'params': {}, 'variation_index': 0}}]
    asm = copy.deepcopy(P['assembly']['state'])
    asm['sections'] = [{'design_id': 'base', 'height': 1.5 * layers}]
    asm['transform'] = {'profile': 'linear', 'shift': [0, 0], 'scale_in': scale_in}
    return _stack(designs, systems, asm)


@pytest.fixture(scope='module')
def circrect():
    return circrect_stack()


def _wall_landings(layer, wall):
    """The bottom ('C:R#0' → 'C:R#1') or top ('C:R#3' → 'C:R#2') wall of the
    rectangle: (positions along the wall, the anchors on it)."""
    inst, pls, anchors, _ = layer
    a_id, b_id = ('C:R#0', 'C:R#1') if wall == 'bottom' else ('C:R#3', 'C:R#2')
    (xa, ya), (xb, yb) = anchors[a_id], anchors[b_id]
    c = 0.5 * (ya + yb)
    on_wall = [x for aid, (x, y) in anchors.items() if abs(y - c) < 2.0 and xa - 2.0 <= x <= xb + 2.0]
    return _straight_landings(pls, 'x', c, xa - 1.0, xb + 1.0), on_wall


@pytest.mark.parametrize('wall', ['bottom', 'top'])
def test_rectangle_walls_keep_their_stations_through_the_circle_junction_events(circrect, wall):
    """Two junctions appear on the circle (layer ≈ 19) and split the segment
    that ran bottom wall → circle → top wall; lens stubs and unresolved
    anchors follow. Nothing happened ON the bottom and top walls: every
    landing farther than 2.5 pitches from any anchor on the wall is still
    there on the next layer, and no landing appears there."""
    seq = [_wall_landings(L, wall) for L in circrect]
    assert len(seq[0][0]) >= 8, seq[0][0]
    band = 20.0                                       # ≈ 2.5 pitches: the transition cells at a wall's ends
    bad = []
    for k, ((a, an_a), (b, an_b)) in enumerate(zip(seq, seq[1:])):
        edges = an_a + an_b

        def interior(x, edges=edges):
            return all(abs(x - e) > band for e in edges)
        gone, new = _retained(a, b, interior)
        if gone or new:
            bad.append((k + 1, gone[:4], new[:4]))
    assert not bad, bad


def test_circle_station_count_never_exceeds_its_arc_over_pitch(circrect):
    """The circle's web keeps ONE comb at its own pitch: its landing count
    never exceeds the full circumference over the pitch plus the transition
    cells, whatever segments (lens stubs, unresolved-anchor loops) the layer's
    topology names on it."""
    centre = (120.0, 40.0)
    counts = []
    first = None
    for inst, pls, anchors, scales in circrect:
        r = 40.0 * scales.get('C', 1.0)
        xb = anchors['C:R#1'][0]                                   # the rectangle's right side
        land = _circle_landings(pls, centre, r, accept=lambda q, xb=xb: q[0] > xb + 1.0)
        if first is None and len(land) >= 4:
            first = r * _median_gap(land)
        counts.append((inst.index, len(land), r))
    p = first                                                     # the landing spacing of the first layer
    assert p and 2.0 < p < 12.0, p
    # (+ 6: a transition cell at each window edge of up to two windows, and the residue)
    over = [(k, n, round(2 * math.pi * r / p + 6, 1)) for k, n, r in counts if n > 2 * math.pi * r / p + 6]
    assert not over, over


# ---- the lobe building ----------------------------------------------------------------

def lobe_stack(layers=25, scale_in=0.75):
    from layer_assembly.tests import test_wall_system_z_coherence as Z
    a = Z.assembly(layers)
    a['sections'][0]['transform'] = {'profile': 'linear', 'shift': [0, 0], 'scale_in': scale_in}
    return _stack(Z.designs(False), Z.SYSTEMS, a)


def test_the_lobe_keeps_its_stations_while_its_segments_are_renamed():
    """The circle lobe's segment is renamed three layers running (a lens
    opens on it, closes, the junction anchors vanish) and then becomes a
    plain loop: the lobe's landings keep their arc position about the lobe's
    axis; only landings within 2.5 pitches of the lobe's ends change."""
    from layer_assembly.tests import test_wall_system_z_coherence as Z
    centre = (Z.CX, Z.CY)
    axis = math.radians(135.0)                       # the lobe points away from the square's corner
    seq = []
    for inst, pls, anchors, scales in lobe_stack():
        r = Z.CR * scales.get('C', 1.0)
        if r < 4.4:
            break                                    # (the circle is nearly a solid disc from here)
        angs = _circle_landings(pls, centre, r, dmin=0.1)          # (8 in wall: a 2 in cavity, landings ± 0.25)
        arcs = sorted(r * math.remainder(t - axis, 2 * math.pi) for t in angs)
        seq.append((inst.index, arcs))
    assert len(seq) >= 23, len(seq)                  # (the loop event at layer 23 is in)
    p = _median_gap(seq[0][1])
    assert 0.5 < p < 4.0, p
    band = 2.5 * p
    bad = []
    for (k0, a), (k1, b) in zip(seq, seq[1:]):
        if len(a) < 4 or len(b) < 4:
            continue
        lo, hi = max(a[0], b[0]) + band, min(a[-1], b[-1]) - band

        def interior(x, lo=lo, hi=hi):
            return lo < x < hi
        gone, new = _retained(a, b, interior)
        if gone or new:
            bad.append((k1, [round(x, 2) for x in gone[:4]], [round(x, 2) for x in new[:4]]))
    assert not bad, bad
