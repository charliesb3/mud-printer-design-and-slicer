"""
VERTICAL SUPPORT — mud requires physical support below.

Pure geometry over the RESOLVED stack: each layer's printable centrelines
(from the LayerSource — the Assembly never learns what a wall or a lattice
is) placed by its layer's InstanceTransform — a grouped layer's geometry is
already its SEMANTIC variant (the Designer moved the groups' sources); a
vertex's transform group only gives its local FRAME (finding / header
coordinates) — compared with what is directly below.

MINIMUM LAYER OVERLAP (o, physical inches). The user-facing input is the
MAXIMUM OVERHANG h (Assembly.max_overhang, 2026-10-06): per transition
o = w_u − h (= bead width − overhang for equal widths), h clamped to
[0, w_u] (warned); the bead width is the Designer's material. A
general rule — it does not care WHY layers moved: shift, scale, profiles,
section / assembly / group transforms alike). Two parallel beads of widths
w_u (upper) and w_l (lower) whose centrelines are laterally offset by d
overlap by  min(w_u, w_l, (w_u + w_l)/2 − d)  (≥ 0). So a point of layer i
is SUPPORTED when
  * its nearest lower centreline is within  h = (w_u + w_l)/2 − o  (the
    allowed overhang; = w − o for equal widths: o = 0 allows a full bead
    of offset, o = w essentially none) — the quick test; else
  * the UNION of lower beads covers at least o of its own cross-section
    (the segment of length w_u through it, perpendicular to its strand;
    lower beads are capsules: centreline ⊕ disk w_l/2) — e.g. a bead
    straddling two adjacent lower beads; this exact covered length is the
    point's ACTUAL OVERLAP; or
  * it lies over the TOP of an assembly object anchored at that boundary
    (a Header).
o is clamped to [0, w_u] per transition (warned).
Unsupported points are grouped into REGIONS (within 4 bead widths of each
other); a region whose extent is under 2 bead widths is noise. Each region
is one finding of one of two KINDS:
    'bridge'   the region spans EMPTY SPACE (a closing doorway / window)
               → status 'needs_header' → HEADER NEEDED. Rule (decision
               2026-10-06, stabilization): along some unsupported run of an
               upper strand there is a contiguous stretch ≥ 1 bead (w_u)
               whose points are over an EMPTY CORRIDOR — the line across
               the strand through the point, of length w_u + 2·w_l (the
               bead plus one full lower bead of clearance on each side),
               touches no lower bead (capsule centreline ⊕ w_l/2). Lower
               material merely NEAR the gap (the jamb end caps, lattice
               landing beside it, ends of the stretch) does not count: it
               lies along the strand, not beside it. Also a bridge: some
               point with no lower material within 2·w_u at all.
    'overlap'  material below, but too little of it (a wall leaning or
               scaling too fast between layers):
               status 'insufficient_support'  → INSUFFICIENT LAYER SUPPORT
               (required o vs actual worst overlap) — no header proposed
and either may be 'supported' (every point over a Header) or 'overridden'
(the designer chose to ignore it — recorded, NOT supported).
    span   — extent along its direction (that of its minimum-width strip):
             for a bridge, the clear opening between the jambs' bead edges
    depth  — extent across that direction + one bead (the wall depth)
Layer 0 sits on the build plate. A transition whose two layers use the
same design and whose placements move no point by more than h is
supported by construction and skipped (with o = w_u only an identical
placement is).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .objects import Header, point_in_rect
from .model import DEFAULT_MAX_OVERHANG

SAMPLE_FACTOR = 0.5      # sample every ½ bead (independent of the rule)
BRIDGE_FACTOR = 2.0      # no lower material within 2 beads → empty space (a bridge)
GAP_FACTOR = 1.0         # an empty-corridor stretch of ≥ 1 bead along a strand → a bridge
MIN_SPAN_FACTOR = 2.0    # regions shorter than 2 beads are noise
LINK_FACTOR = 4.0        # unsupported points within 4 beads form one region
DEFAULT_BEAD = 3.0


@dataclass
class SpanFinding:
    id: str
    layer: int                    # global index of the first unsupported layer
    section: int
    local_layer: int
    z: float                      # the boundary (z_bottom of that layer)
    centre: tuple                 # physical
    local_centre: tuple           # in the layer's design coordinates
    angle: float                  # span direction (deg, physical = local)
    span: float                   # physical
    depth: float                  # physical
    runs: list                    # unsupported centreline pieces (physical) — for the preview
    samples: list = field(default_factory=list, repr=False)
    status: str = 'needs_header'
    kind: str = 'bridge'          # 'bridge' (HEADER NEEDED) | 'overlap' (INSUFFICIENT LAYER SUPPORT)
    min_overlap: float = 0.0      # required (in, after clamping)
    overlap: float = 0.0          # actual: the worst covered cross-section in the region (in)
    header: str = ''
    coverage: float = 0.0         # fraction over a header
    override: int = -1            # index of the matching SupportOverride

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ('id', 'layer', 'section', 'local_layer', 'z', 'centre',
                                              'local_centre', 'angle', 'span', 'depth', 'runs',
                                              'status', 'kind', 'min_overlap', 'overlap',
                                              'header', 'coverage', 'override')}


@dataclass
class SupportReport:
    findings: list
    headers: list                 # placed header dicts
    warnings: list = field(default_factory=list)
    min_overlap: float = 0.0                      # required: DERIVED (bead width − max overhang)
    max_overhang: float = DEFAULT_MAX_OVERHANG

    @property
    def needs_header(self):
        return [f for f in self.findings if f.status == 'needs_header']

    def groups(self) -> list:
        """Findings AGGREGATED for display (building-scale diagnostics):
        same kind + status + section and the same place (centres within half
        their spans + 12 in of a member) form one group, whether its layers
        are contiguous or scattered (`count` vs the `layers` range) — a steep
        lean over 40 layers is ONE card, not 40, and recurring local findings
        (e.g. one corner) are one card. Header findings are sparse and stay
        one group each."""
        out, open_ = [], []
        for f in sorted(self.findings, key=lambda f: (f.layer, f.centre)):
            g = None
            if f.kind == 'overlap':
                for c in open_:
                    if (c['kind'], c['status'], c['section']) == (f.kind, f.status, f.section) and \
                            any(math.dist(m.centre, f.centre) <= (m.span + f.span) / 2 + 12.0
                                for m in c['_members']):
                        g = c
                        break
            if g is None:
                g = {'id': f'g{len(out)}', 'kind': f.kind, 'status': f.status, 'section': f.section,
                     'layers': [f.layer, f.layer], 'min_overlap': f.min_overlap,
                     'worst_overlap': f.overlap, 'worst_layer': f.layer, 'worst': f.id,
                     'centre': f.centre, 'z': f.z, 'finding_ids': [], '_last': f, '_members': []}
                out.append(g)
                if f.kind == 'overlap':
                    open_.append(g)
            g['finding_ids'].append(f.id)
            g['_members'].append(f)
            g['layers'][1] = max(g['layers'][1], f.layer)
            g['_last'] = f
            if f.overlap < g['worst_overlap'] or f.id == g['worst']:
                g.update(worst_overlap=f.overlap, worst_layer=f.layer, worst=f.id, centre=f.centre, z=f.z)
        for g in out:
            del g['_last']
            g['count'] = len({m.layer for m in g.pop('_members')})
            g['contiguous'] = g['count'] == g['layers'][1] - g['layers'][0] + 1
        return out

    def to_dict(self) -> dict:
        return {'findings': [f.to_dict() for f in self.findings], 'headers': self.headers,
                'groups': self.groups(),
                'needs_header': len(self.needs_header),
                'insufficient_support': sum(f.status == 'insufficient_support' for f in self.findings),
                'supported': sum(f.status == 'supported' for f in self.findings),
                'overridden': sum(f.status == 'overridden' for f in self.findings),
                'min_overlap': self.min_overlap, 'max_overhang': self.max_overhang,
                'warnings': self.warnings}


# ---------------------------------------------------------------------------

def _bbox(polys):
    xs = [x for pl in polys for x, _ in pl['pts']]
    ys = [y for pl in polys for _, y in pl['pts']]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _seg_dist(p, a, b):
    ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


class _SegIndex:
    """Lower-layer segments in a uniform grid (cell = reach); each segment is
    filed in every cell its bbox grown by `reach` touches, so the segments
    within `reach` of a point are all in that point's cell."""

    def __init__(self, polylines, reach):
        self.cell = max(reach, 1e-6)
        self.grid = defaultdict(list)
        self.segs = [(a, b) for pts in polylines for a, b in zip(pts, pts[1:])]
        for pts in polylines:
            for a, b in zip(pts, pts[1:]):
                x0 = math.floor((min(a[0], b[0]) - reach) / self.cell)
                x1 = math.floor((max(a[0], b[0]) + reach) / self.cell)
                y0 = math.floor((min(a[1], b[1]) - reach) / self.cell)
                y1 = math.floor((max(a[1], b[1]) + reach) / self.cell)
                for gx in range(x0, x1 + 1):
                    for gy in range(y0, y1 + 1):
                        self.grid[gx, gy].append((a, b))

    def near(self, p):
        return self.grid.get((math.floor(p[0] / self.cell), math.floor(p[1] / self.cell)), ())

    def dist(self, p) -> float:
        """Distance to the nearest lower centreline (inf beyond the reach)."""
        return min((_seg_dist(p, a, b) for a, b in self.near(p)), default=math.inf)

    def within(self, p, r) -> bool:
        """Some lower centreline within r of p (the quick test): a finer grid,
        built on first use for this r — the same answer as dist(p) <= r."""
        fine = getattr(self, '_fine', None)
        if fine is None or fine[0] != r:
            cell = max(r, 0.25, 1e-6)
            g = defaultdict(list)
            for a, b in self.segs:
                for gx in range(math.floor((min(a[0], b[0]) - r) / cell), math.floor((max(a[0], b[0]) + r) / cell) + 1):
                    for gy in range(math.floor((min(a[1], b[1]) - r) / cell), math.floor((max(a[1], b[1]) + r) / cell) + 1):
                        g[gx, gy].append((a, b))
            fine = self._fine = (r, cell, g)
        _, cell, g = fine
        return any(_seg_dist(p, a, b) <= r for a, b in g.get((math.floor(p[0] / cell), math.floor(p[1] / cell)), ()))


def _interval(p, n, a, b, r):
    """{u : p + n·u within r of segment ab} — an interval (capsule ∩ line), or None."""
    out = []
    for c in (a, b):                                   # end disks: |p + n u − c|² ≤ r²
        B = n[0] * (p[0] - c[0]) + n[1] * (p[1] - c[1])
        C = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 - r * r
        D = B * B - C
        if D >= 0:
            out.append((-B - math.sqrt(D), -B + math.sqrt(D)))
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L > 0:                                          # the rectangle: 0 ≤ s ≤ L, |h| ≤ r
        tx, ty = dx / L, dy / L
        lo, hi = -math.inf, math.inf
        for v0, dv, vmin, vmax in (((p[0] - a[0]) * tx + (p[1] - a[1]) * ty, n[0] * tx + n[1] * ty, 0.0, L),
                                   (-(p[0] - a[0]) * ty + (p[1] - a[1]) * tx, -n[0] * ty + n[1] * tx, -r, r)):
            if abs(dv) < 1e-12:
                if not vmin <= v0 <= vmax:
                    lo, hi = 1.0, 0.0
                continue
            u1, u2 = (vmin - v0) / dv, (vmax - v0) / dv
            lo, hi = max(lo, min(u1, u2)), min(hi, max(u1, u2))
        if lo <= hi:
            out.append((lo, hi))
    if not out:
        return None
    return (min(x for x, _ in out), max(y for _, y in out))


def covered_width(p, direction, w_u, idx, r_l) -> float:
    """Length of the upper bead's cross-section at p (width w_u, across
    `direction`) covered by the union of lower beads (radius r_l)."""
    n = (-direction[1], direction[0])
    h = w_u / 2
    ivs = []
    for a, b in idx.near(p):
        iv = _interval(p, n, a, b, r_l)
        if iv and iv[1] > -h and iv[0] < h:
            ivs.append((max(iv[0], -h), min(iv[1], h)))
    ivs.sort()
    tot, cur = 0.0, None
    for lo, hi in ivs:
        if cur is None or lo > cur[1]:
            if cur:
                tot += cur[1] - cur[0]
            cur = [lo, hi]
        else:
            cur[1] = max(cur[1], hi)
    if cur:
        tot += cur[1] - cur[0]
    return tot


def _sample(pts, step):
    """[(point, unit direction, segment index)] along a polyline, every
    `step` and at vertices."""
    return [(p, d, k) for p, d, k, _ in _sample_dirs(pts, step)]


def _sample_dirs(pts, step):
    """_sample, plus the OTHER direction at a vertex (the previous segment's;
    a closed polyline's first / last vertex: the closing one) — at a corner
    the cross-section of either leg is the bead's, and the better covered
    one is its actual overlap (one leg alone would see the other leg's
    corner stick out)."""
    out = []
    dirs = []
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L == 0:
            continue
        d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        n = max(1, int(math.ceil(L / step)))
        for j in range(n):
            t = j / n
            out.append([(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), d, k,
                        dirs[-1] if j == 0 and dirs else None])
        dirs.append(d)
    if out:
        out.append([pts[-1], out[-1][1], out[-1][2], None])
        if len(pts) > 2 and pts[0] == pts[-1] and len(dirs) > 1:      # closed: the seam is a vertex too
            out[0][3], out[-1][3] = dirs[-1], dirs[0]
    return [tuple(x) for x in out]


def _longest_empty(members, run_of, points, empty_of) -> float:
    """Longest contiguous stretch (length along its strand) of points over
    an empty corridor, within one unsupported run."""
    best = 0.0
    cur, prev, prun = 0.0, None, None
    for j in sorted(members):
        if not empty_of[j]:
            cur, prev, prun = 0.0, None, None
            continue
        if prev is not None and run_of[j] == prun:
            cur += math.dist(points[prev], points[j])
        else:
            cur = 0.0
        prev, prun = j, run_of[j]
        best = max(best, cur)
    return best


def _clusters(points, link):
    parent = list(range(len(points)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    grid = defaultdict(list)
    for i, (x, y) in enumerate(points):
        grid[math.floor(x / link), math.floor(y / link)].append(i)
    for (gx, gy), members in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    for i in members:
                        if i < j and math.dist(points[i], points[j]) <= link:
                            parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(len(points)):
        groups[find(i)].append(i)
    return list(groups.values())


def _hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h[:-1]
    return half(pts) + half(reversed(pts))


def _direction(points, runs=()):
    """Span direction (rad): the direction of the region's MINIMUM-WIDTH
    strip (it lies along a convex-hull edge). A bridge over a gap is long
    along the wall and narrow across it; its wall faces are hull edges and
    any tilt only widens the strip, so lattice zigzags cannot skew it."""
    hull = _hull(points)
    if len(hull) < 2:
        return 0.0
    best, best_a = math.inf, 0.0
    for p, q in zip(hull, hull[1:] + hull[:1]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy)
        if L == 0:
            continue
        nx, ny = -dy / L, dx / L
        proj = [x * nx + y * ny for x, y in hull]
        w = max(proj) - min(proj)
        if w < best - 1e-9:
            best, best_a = w, math.atan2(dy, dx)
    a = best_a
    while a <= -math.pi / 2:
        a += math.pi
    while a > math.pi / 2:
        a -= math.pi
    return a


def _principal(points, runs=()):
    a = _direction(points, runs)
    ux, uy = math.cos(a), math.sin(a)
    su = [p[0] * ux + p[1] * uy for p in points]
    sv = [-p[0] * uy + p[1] * ux for p in points]
    u0, u1, v0, v1 = min(su), max(su), min(sv), max(sv)
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    centre = (cu * ux - cv * uy, cu * uy + cv * ux)
    deg = math.degrees(a)
    if deg <= -90 + 1e-9:
        deg += 180
    return centre, deg, u1 - u0, v1 - v0


def _bound(t0, t1, bbox):
    """Max displacement of any design point between two placements (affine:
    attained at a bbox corner)."""
    x0, y0, x1, y1 = bbox
    return max(math.dist(t0.apply(x, y), t1.apply(x, y)) for x, y in
               ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


# ---------------------------------------------------------------------------

def analyse_support(resolved, geometry, assembly=None, groups=None) -> SupportReport:
    """geometry: {design id: (polylines, bead_width)} — the designs' resolved
    printable centrelines (untransformed; optional per-vertex 'src').
    assembly: its objects (Headers), overrides and max_overhang. groups: the
    groups.GroupModel when layers carry per-group placements."""
    vgs = groups.vertex_groups if groups is not None else {}

    def group_at(inst, p):
        """The transform group of the vertex of `inst` nearest to local point p."""
        v = vgs.get(inst.design_id)
        if not v or not inst.parts:
            return None
        best = (math.inf, None)
        for j, pl in enumerate(geometry[inst.design_id][0]):
            for q, g in zip(pl['pts'], v[j] if j < len(v) else ()):
                d = math.dist(p, q)
                if d < best[0]:
                    best = (d, g)
        return best[1]
    insts = resolved.instances
    objects = list(getattr(assembly, 'objects', None) or [])
    overrides = list(getattr(assembly, 'overrides', None) or [])
    # MAXIMUM OVERHANG (the user-facing input) wins when set: the required
    # overlap is derived per transition from the upper bead width (material)
    h_max = float(getattr(assembly, 'max_overhang', DEFAULT_MAX_OVERHANG) if assembly is not None
                  else DEFAULT_MAX_OVERHANG)
    o_report = None
    warnings = []
    bbox = {d: _bbox(g[0]) for d, g in geometry.items()}
    placed_cache, index_cache = {}, {}

    group_of = groups.group_of if groups is not None else {}

    def gk(inst):
        return inst.geometry_key if inst.geometry_key in geometry else inst.design_id

    def placed(inst):
        """[(placed points, per-vertex FRAME transforms)] per polyline. Every
        vertex is placed by the layer's own transform: a grouped layer's
        geometry is its SEMANTIC variant (the Designer already moved the
        groups' architecture); the frame of a vertex (for local
        coordinates: findings, headers) is its group's placement."""
        key = (gk(inst), inst.transform, tuple(sorted(inst.parts.items())))
        if key not in placed_cache:
            out = []
            for j, pl in enumerate(geometry[gk(inst)][0]):
                pts = [tuple(p) for p in pl['pts']]
                tags = pl.get('src') if pl.get('src') and len(pl['src']) == len(pts) else [None] * len(pts)
                frames = [inst.transform_of(group_of.get(t)) if t else inst.transform for t in tags]
                if pl.get('closed') and len(pts) > 2 and pts[0] != pts[-1]:
                    pts.append(pts[0])
                    frames = frames + frames[:1]
                out.append(([inst.transform.apply(x, y) for x, y in pts], frames))
            placed_cache[key] = out
        return placed_cache[key]

    def moved(lo, up):
        ts = [(lo.transform, up.transform)] + [(lo.transform_of(k), up.transform_of(k))
                                              for k in set(lo.parts) | set(up.parts)]
        return max(_bound(a, b, bbox[gk(up)]) for a, b in ts)

    def bead(inst):
        return float(geometry[gk(inst)][1] or DEFAULT_BEAD)

    findings = []
    for i in range(1, len(insts)):
        lo, up = insts[i - 1], insts[i]
        if lo.design_id not in geometry or up.design_id not in geometry or not bbox.get(gk(up)):
            continue
        b, bl = bead(up), bead(lo)
        h = min(max(h_max, 0.0), b)
        if h != h_max:
            msg = f'maximum overhang {h_max:g} in exceeds the bead width {b:g} in: {b:g} in used'
            if msg not in warnings:
                warnings.append(msg)
        o = b - h
        if o_report is None:
            o_report = o
        allow = max(0.0, (b + bl) / 2 - o)              # allowed lateral overhang of the centreline
        if gk(lo) == gk(up) and moved(lo, up) <= allow:
            continue
        reach = BRIDGE_FACTOR * b + bl
        lkey = (gk(lo), lo.transform, tuple(sorted(lo.parts.items())), reach)
        if lkey not in index_cache:
            index_cache[lkey] = _SegIndex([p for p, _ in placed(lo)], reach)
        idx = index_cache[lkey]
        points, runs, run_of, tf_of, ov_of, d_of = [], [], [], [], [], []
        empty_of = []                                  # per point: over an empty corridor
        corridor = b + 2 * bl
        for pts, tfs in placed(up):
            cur = None
            for p, dirn, k, alt in _sample_dirs(pts, SAMPLE_FACTOR * b):
                ok = idx.within(p, allow + 1e-9)
                ov = None
                d = None if ok else idx.dist(p)
                if not ok:
                    ov = covered_width(p, dirn, b, idx, bl / 2) if d < (b + bl) / 2 else 0.0
                    if alt is not None and d < (b + bl) / 2:
                        ov = max(ov, covered_width(p, alt, b, idx, bl / 2))
                    ok = ov > 1e-9 and ov >= o - 1e-9      # some contact is always needed
                if ok:
                    cur = None
                    continue
                if cur is None:
                    cur = []
                    runs.append(cur)
                cur.append(p)
                points.append(p)
                run_of.append(len(runs) - 1)
                tf_of.append(tfs[k])
                ov_of.append(ov)
                d_of.append(d)
                emp = ov <= 1e-9 and covered_width(p, dirn, corridor, idx, bl / 2) <= 1e-9
                if emp and alt is not None:
                    emp = covered_width(p, alt, corridor, idx, bl / 2) <= 1e-9
                empty_of.append(emp)
        if not points:
            continue
        sec = resolved.sections[up.section_index]
        n = 0
        for members in _clusters(points, LINK_FACTOR * b):
            pts = [points[j] for j in members]
            rset = sorted({run_of[j] for j in members})
            centre, ang, span, across = _principal(pts, [runs[r] for r in rset])
            if span < MIN_SPAN_FACTOR * b:
                continue
            votes = defaultdict(int)                   # the placement most of it uses
            for j in members:
                votes[tf_of[j]] += 1
            t = max(votes, key=votes.get)
            local = ((centre[0] - t.tx) / t.scale, (centre[1] - t.ty) / t.scale)
            bridge = max(d_of[j] for j in members) > BRIDGE_FACTOR * b or \
                _longest_empty(members, run_of, points, empty_of) >= GAP_FACTOR * b - 1e-9
            f = SpanFinding(f'{i}:{n}', i, up.section_index, i - sec.first_layer, up.z_bottom,
                            centre, local, ang, span, across + b, [runs[r] for r in rset], pts,
                            'needs_header' if bridge else 'insufficient_support',
                            'bridge' if bridge else 'overlap', o, min(ov_of[j] for j in members))
            findings.append(f)
            n += 1

    headers = []
    for h in objects:
        if not isinstance(h, Header):
            continue
        out = {'id': h.id, 'kind': h.kind, 'error': '', 'snapped': False, 'supports': [],
               'pocket_layers': []}
        if not (0 <= h.section < len(resolved.sections)) or resolved.sections[h.section].first_layer is None \
                or not (0 <= h.layer < resolved.sections[h.section].layers):
            out['error'] = 'its layer boundary no longer exists'
            headers.append(out)
            continue
        i = resolved.sections[h.section].first_layer + h.layer
        if i == 0:
            out['error'] = 'nothing can rest on a header at the build plate'
        t = insts[i].transform_of(group_at(insts[i], (h.x, h.y)))
        if h.snap:
            cands = [f for f in findings if f.layer == i and f.kind == 'bridge']
            best = min(cands, key=lambda f: math.dist(f.local_centre, (h.x, h.y)), default=None)
            if best is not None and math.dist(best.local_centre, (h.x, h.y)) <= \
                    max(best.span / t.scale / 2, 2 * bead(insts[i])):
                h.x, h.y = best.local_centre
                t = insts[i].transform_of(group_at(insts[i], (h.x, h.y)))
                h.angle, h.span = best.angle, best.span / t.scale
                out['snapped'] = True
        rect = h.support_rect(t)
        z_top = insts[i].z_bottom
        z_bot = z_top - h.thickness
        pockets = []
        for j in range(i - 1, -1, -1):
            L = insts[j]
            if L.z_top <= z_bot + 1e-9:
                break
            if any(point_in_rect(p, rect) for pts, _ in placed(L) for p, _, _ in _sample(pts, bead(L))):
                pockets.append(j)
        out.update({'layer': i, 'rect': rect, 'z_top': z_top, 'z_bottom': z_bot,
                    'length': h.length(t.scale), 'span': h.span * t.scale, 'angle': h.angle,
                    'centre': t.apply(h.x, h.y), 'x': h.x, 'y': h.y, 'bearing': h.bearing,
                    'depth': h.depth, 'thickness': h.thickness, 'pocket_layers': sorted(pockets)})
        headers.append(out)

    for f in findings:
        rects = [(h['id'], h['rect']) for h in headers if not h['error'] and h['layer'] == f.layer]
        covered, by = 0, set()
        for p in f.samples:
            for hid, r in rects:
                if point_in_rect(p, r):
                    covered += 1
                    by.add(hid)
                    break
        f.coverage = covered / len(f.samples)
        if covered == len(f.samples):
            f.status, f.header = 'supported', ','.join(sorted(by))
            for h in headers:
                if h['id'] in by:
                    h['supports'].append(f.id)
            continue
        b = float(geometry[gk(insts[f.layer])][1] or DEFAULT_BEAD)
        sc = insts[f.layer].transform_of(group_at(insts[f.layer], f.local_centre)).scale
        for k, o in enumerate(overrides):
            if o.section == f.section and o.layer == f.local_layer and \
                    math.dist((o.x, o.y), f.local_centre) <= max(f.span / sc / 2, 2 * b):
                f.status, f.override = 'overridden', k
                break
    if o_report is None:
        o_report = max(0.0, DEFAULT_BEAD - h_max)
    return SupportReport(findings, headers, warnings, o_report, h_max)
