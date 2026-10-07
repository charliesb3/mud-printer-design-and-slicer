"""
COMPONENTS — distinct XY masses of a Layer Design. They only SUGGEST
transform groups ("Suggest from components": one group per mass, listing
the source ids found in it — groups.py); a component is NOT the identity
of a transform group (one connected mass may hold several groups, assigned
explicitly by source). Pure geometry over the
resolved printable centrelines (the LayerSource contract) and the bead
width; the Assembly still knows nothing about walls, lattices or openings.

RULE (V1):
  1. Two strands belong to one mass when their beads TOUCH: some point of
     one lies within one bead width of the other's centreline (centre
     separation ≤ bead width). Wall faces, the lattice landing on them and
     the caps joining them are therefore one mass.
  2. A mass whose bounding box lies inside another's (grown by one bead)
     joins it — so the two faces of a hollow ring (no lattice) stay one
     mass. (Consequence: a mass standing inside a courtyard joins it.)
  3. Masses joined by printed material are ONE mass (a circle and a square
     connected by a wall are one component). Splitting connected geometry
     is done by explicit source assignment (groups.py).
  Masses are ordered by bbox centre (x, then y): keys "<root>:<k>".

IDENTITY ACROSS DESIGNS: masses are detected ONCE per lineage, on its ROOT
design (the base of the derivation chain). Every design of that lineage is
ASSIGNED to the root's masses (each strand votes with its sample points by
the nearest root-mass bbox), so an opening that splits a wall — a doorway,
an arc cut free by two openings — never creates, drops or swaps a
component, and a layer-height change cannot either (nothing here depends on
Z). A design whose root is different gets its own root's masses. Geometry
far outside every root mass still goes to the nearest one (predictable,
documented fallback).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass



@dataclass(frozen=True)
class Mass:
    key: str
    bbox: tuple                   # (x0, y0, x1, y1) in design coordinates

    @property
    def centre(self):
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)

    @property
    def half(self):
        return max(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1]) / 2

    def distance(self, p):
        dx = max(self.bbox[0] - p[0], 0.0, p[0] - self.bbox[2])
        dy = max(self.bbox[1] - p[1], 0.0, p[1] - self.bbox[3])
        return math.hypot(dx, dy)


def _pts(pl):
    pts = [tuple(p) for p in pl['pts']]
    if pl.get('closed') and len(pts) > 2 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _samples(pts, step):
    out = []
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(math.dist(a, b) / step)))
        out.extend((a[0] + (b[0] - a[0]) * j / n, a[1] + (b[1] - a[1]) * j / n) for j in range(n))
    if pts:
        out.append(pts[-1])
    return out


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def detect(polylines, bead, root='') -> list:
    """The masses of one design's printable centrelines (see RULE)."""
    bead = float(bead or 3.0)
    polys = [_pts(pl) for pl in polylines]
    idx = [i for i, p in enumerate(polys) if len(p) >= 2]
    if not idx:
        return []
    parent = {i: i for i in idx}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    cell = bead * 2
    grid = defaultdict(list)                       # segments filed by every cell they touch (± bead)
    for i in idx:
        for a, b in zip(polys[i], polys[i][1:]):
            for gx in range(math.floor((min(a[0], b[0]) - bead) / cell), math.floor((max(a[0], b[0]) + bead) / cell) + 1):
                for gy in range(math.floor((min(a[1], b[1]) - bead) / cell), math.floor((max(a[1], b[1]) + bead) / cell) + 1):
                    grid[gx, gy].append((i, a, b))
    for i in idx:
        for p in _samples(polys[i], bead):
            for j, a, b in grid.get((math.floor(p[0] / cell), math.floor(p[1] / cell)), ()):
                if find(j) != find(i) and _seg_dist(p, a, b) <= bead:
                    parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in idx:
        groups[find(i)].append(i)
    boxes = [(_bbox([q for i in g for q in polys[i]]), g) for g in groups.values()]
    # rule 2: nested masses join the mass around them
    merged = True
    while merged:
        merged = False
        boxes.sort(key=lambda bg: -(bg[0][2] - bg[0][0]) * (bg[0][3] - bg[0][1]))
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                A, B = boxes[a][0], boxes[b][0]
                if B[0] >= A[0] - bead and B[1] >= A[1] - bead and B[2] <= A[2] + bead and B[3] <= A[3] + bead:
                    boxes[a] = (A, boxes[a][1] + boxes[b][1])
                    del boxes[b]
                    merged = True
                    break
            if merged:
                break
    boxes.sort(key=lambda bg: ((bg[0][0] + bg[0][2]) / 2, (bg[0][1] + bg[0][3]) / 2))
    return [Mass(f'{root}:{k}', bb) for k, (bb, _) in enumerate(boxes)]


def assign(polylines, masses) -> list:
    """The mass key of every polyline (strand) of a design: its sample
    points vote for the nearest mass bbox (inside = 0); ties → the nearer."""
    out = []
    for pl in polylines:
        pts = _pts(pl)
        if not pts or not masses:
            out.append(masses[0].key if masses else None)
            continue
        step = max(1, len(pts) // 16)
        votes = defaultdict(float)
        best = {}
        for p in pts[::step]:
            d, m = min((m.distance(p), m.key) for m in masses)
            votes[m] += 1
            best[m] = min(best.get(m, math.inf), d)
        out.append(max(votes, key=lambda k: (votes[k], -best[k], k)))
    return out


def root_of(design_id, parent_of) -> str:
    """The base of a design's derivation chain."""
    seen, d = set(), design_id
    while parent_of.get(d) and d not in seen:
        seen.add(d)
        d = parent_of[d]
    return d
