"""
Representative wall geometry for the wall-web experiment.

Simple walls are built directly as rings (a centre line offset by a
per-vertex half width — easy to perturb and to give a variable width).
Openings, junctions and the reference network go through the REAL Designer
pipeline (PrintLayer → wall network → lattice region rings), so their caps,
junction corners and fillets are exactly what wall_lattice.plan() sees.
Every factory returns a list of REGIONS (connected wall materials), each a
list of rings — an opening can split one wall into several regions.
"""
from __future__ import annotations

import math

from common import V, unit, Vec2, CONTACT, BEAD
import app                                   # noqa: F401  (path setup / deserialiser)
import wall_fixtures as WF
import physical_fixtures as PF
import reference_network as RN
from model import Opening, RectanglePath, WallSpec, PrintLayer, LinePath, NetworkWall, RegionInfill


def _area(r):
    return sum(r[i].x * r[(i + 1) % len(r)].y - r[(i + 1) % len(r)].x * r[i].y for i in range(len(r))) / 2


def band(centre, half):
    """Open wall around a centre polyline; half = per-vertex half widths
    (mitred). One ring, counter-clockwise (material on the left)."""
    n = len(centre)
    left, right = [], []
    for i in range(n):
        if i == 0:
            t = unit(centre[1] - centre[0])
            m = 1.0
        elif i == n - 1:
            t = unit(centre[-1] - centre[-2])
            m = 1.0
        else:
            t1, t2 = unit(centre[i] - centre[i - 1]), unit(centre[i + 1] - centre[i])
            t = unit(t1 + t2)
            m = 1.0 / max(0.3, t.x * t1.x + t.y * t1.y)       # miter length factor
        nrm = Vec2(-t.y, t.x)
        left.append(centre[i] + nrm * (half[i] * m))
        right.append(centre[i] - nrm * (half[i] * m))
    ring = right + list(reversed(left))
    if _area(ring) < 0:
        ring.reverse()
    return [ring]


def arc_pts(cx, cy, R, a0, a1, step=3.0):
    n = max(2, int(abs(a1 - a0) * R / step))
    return [V(cx + R * math.cos(a0 + (a1 - a0) * k / n), cy + R * math.sin(a0 + (a1 - a0) * k / n))
            for k in range(n + 1)]


def line_pts(a, b, step=4.0):
    n = max(1, int(a.dist(b) / step))
    return [a.lerp(b, k / n) for k in range(n + 1)]


def rings_of(layer):
    """Lattice region rings of a PrintLayer (physical rules on)."""
    ns = layer.network_summary()
    out = []
    for lat in ns['lattice'].values():
        for reg in lat['regions']:
            if reg.get('rings'):
                out.append([[V(x, y) for x, y in ring] for ring in reg['rings']])
    return out


# --- direct (ring) walls ----------------------------------------------------

def straight(t=10.0, L=280.0, bump=0.0, bump_at=150.0, bump_len=30.0):
    """Lone straight wall. bump: one face pushed outwards over bump_len
    (a smooth local perturbation of ONE boundary)."""
    c = line_pts(V(40, 200), V(40 + L, 200), 2.0)
    half_l = [t / 2] * len(c)
    ring = band(c, half_l)[0]
    if bump:
        out = []
        for p in ring:
            x = p.x - (40 + bump_at)
            if p.y > 200 and 0 <= x <= bump_len:
                p = V(p.x, p.y + bump * math.sin(math.pi * x / bump_len) ** 2)
            out.append(p)
        ring = out
    return [[ring]]


def gentle_curve(t=10.0, scale=1.0):
    R = 250.0 * scale
    c = arc_pts(180, 200 + R, R, math.radians(270 - 32), math.radians(270 + 32))
    return [band(c, [t / 2] * len(c))]


def strong_curve(t=10.0):
    """Tight U bend: centre-line radius 45 (inner face 40, outer 50) with
    short straight legs."""
    R = 45.0
    arc = arc_pts(200, 200, R, math.radians(180), math.radians(360), 2.0)
    legA = line_pts(V(155, 100), V(155, 200), 3.0)[:-1]
    legB = line_pts(V(245, 200), V(245, 100), 3.0)[1:]
    # (arc mirrored so the bend is at the bottom — y grows downwards in SVG)
    c = legA + [V(p.x, 200 - (p.y - 200)) for p in arc] + legB
    return [band(c, [t / 2] * len(c))]


def variable_width(t0=6.0, t1=18.0, L=280.0, dt=0.0):
    c = line_pts(V(40, 200), V(40 + L, 200), 2.0)
    half = [(t0 + (t1 - t0) * k / (len(c) - 1) + dt) / 2 for k in range(len(c))]
    return [band(c, half)]


def corner(t=10.0):
    k = V(220, 100)
    c = line_pts(V(60, 100), k, 3.0) + line_pts(k, V(220, 260), 3.0)[1:]
    c = [p for p in c if p.dist(k) < 1e-9 or p.dist(k) > t]       # no fold-back of the inner offset
    return [band(c, [t / 2] * len(c))]


def short(t=10.0):
    c = line_pts(V(180, 200), V(216, 200), 2.0)
    return [band(c, [t / 2] * len(c))]


# --- real Designer walls (openings, junctions) -------------------------------

def _phys(lay):
    return PF._phys(lay)


def rect_openings(openings, scale=1.0, t=10.0):
    w, h = 200.0 * scale, 120.0 * scale
    R = RectanglePath(200 - w / 2, 200 - h / 2, w, h, id='R')
    R.wall = WallSpec(t)
    lay = PrintLayer('t', source_paths=[R], infills=[RegionInfill('I', 'R', 'zigzag', {'spacing': 20})])
    lay.openings = [Opening(f'o{k}', 'R', pos * scale, width) for k, (pos, width) in enumerate(openings)]
    return rings_of(_phys(lay))


def one_opening(shift=0.0, scale=1.0):
    return rect_openings([(100.0 + shift, 30.0)], scale)


def multi_openings():
    return rect_openings([(100.0, 24.0), (300.0, 30.0), (430.0, 30.0), (560.0, 24.0)])


def opening_near_bend():
    # the rectangle's first edge is 200 long: centre 22 in past the corner
    return rect_openings([(222.0, 24.0)])


def t_junction():
    return rings_of(_phys(WF.rect_branches(1)))


def x_junction():
    return rings_of(_phys(WF.arms(4)))


def reference_network():
    lay = app._deserialise_layer(RN.document())
    return rings_of(lay)


CASES = [
    ('straight', 'Straight constant-width wall (10 in, 280 in, lone run)', straight),
    ('gentle', 'Gently curved wall (centre-line R 250 in)', gentle_curve),
    ('variable', 'Variable width (6 → 18 in, tapered)', variable_width),
    ('ubend', 'Tight U bend (centre-line R 45 in; inner face R 40)', strong_curve),
    ('corner', 'Sharp 90° corner (L wall)', corner),
    ('short', 'Short wall segment (36 in)', short),
    ('opening1', 'Ring wall, one opening (30 in)', lambda: one_opening()),
    ('openings', 'Ring wall, four openings', multi_openings),
    ('opening_bend', 'Opening 10 in from a corner', opening_near_bend),
    ('tjunction', 'Ring + attached branch (T junctions)', t_junction),
    ('xjunction', 'Four-arm crossing (X junction, dead ends)', x_junction),
    ('reference', 'Reference network (Circle 1 ∩ Rect 1 ∩ Rect 2 + Line 1, round junctions)', reference_network),
]

# perturbations: (name, description, base factory, perturbed factory, site or None = global)
PERTURBATIONS = [
    ('bump', 'Straight wall: one face pushed out 1 in over 30 in (local)',
     lambda: straight(), lambda: straight(bump=1.0), V(205, 205)),
    ('bump_curve', 'Straight wall: one face pushed out 0.5 in over 30 in (local, smaller)',
     lambda: straight(), lambda: straight(bump=0.5), V(205, 205)),
    ('opening_move', 'Ring wall: opening moved 2 in along the wall (local)',
     lambda: one_opening(), lambda: one_opening(2.0), V(200, 140)),
    ('width_global', 'Variable-width wall: +0.5 in everywhere (global, small)',
     lambda: variable_width(), lambda: variable_width(dt=0.5), None),
    ('thick_curve', 'Curved wall: 10 → 10.5 in thick (global, small)',
     lambda: gentle_curve(), lambda: gentle_curve(10.5), None),
]


def zseries_curve(ks=(1.00, 1.02, 1.04, 1.06, 1.08, 1.10)):
    """Neighbouring 'layers': the curved wall's centre line scaled about
    the arc apex, thickness physical (semantic-transform rule)."""
    out = []
    for k in ks:
        R = 250.0 * k
        c0 = arc_pts(180, 200 + 250.0, 250.0, math.radians(270 - 32), math.radians(270 + 32))
        c = [V(180 + (p.x - 180) * k, 200 + (p.y - 200) * k) for p in c0]
        out.append((k, V(180, 200), [band(c, [5.0] * len(c))]))
    return out


def zseries_opening(ks=(1.00, 1.02, 1.04, 1.06, 1.08, 1.10)):
    return [(k, V(200, 200), one_opening(scale=k)) for k in ks]
