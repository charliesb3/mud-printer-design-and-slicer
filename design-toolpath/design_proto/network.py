"""
Wall networks — a DERIVED planar topology over wall systems.

Source geometry stays exactly as the designer drew it (lines, curves,
rectangles, circles, drawn paths, their offsets, openings, lattice).
Nothing here is stored: it is recomputed for every layer from the
effective geometry of that layer, so it will follow the geometry through
future physical-Z keyframes / per-layer openings without stale state.

Principle: geometry that physically touches or crosses is connected.

A WALL SYSTEM is a source path + its offsets (+ caps): one physical wall
of thickness W (W = 0 for a single-bead wall, a "wire"). Systems whose
beads touch or cross form a WALL NETWORK. For each network:

  * Junctions — every crossing / touching point becomes a shared node
    (T, X, endpoint-on-path, curves alike: they are all polylines).
  * Attached ends — an open end of a thick system that lands on another
    system (T) or meets other ends (corner / Y hub) is not capped; its
    walls are extended into the host so its faces splice into the host's
    faces instead of floating beside them.
  * Printable wall REGION — the network's material is the union of every
    system's band (between its extreme walls, closed by its end caps),
    junction fill (attached-end extensions, hub corners), lattice
    cavities between separate systems, minus opening clear-voids,
    adjusted by explicit region overrides (paint-bucket wall/void).
  * Planar arrangement — all beads plus the region's non-printed
    boundaries are split at every contact; the arrangement's faces are
    classified wall / void (one sample per face).
  * Bead survival, by bead class:
        FACE      a system's extreme walls and caps — the architectural
                  wall faces. Kept where they separate wall from void; a
                  face buried inside the combined wall (e.g. the host's
                  face across a T mouth, faces inside an X overlap) is no
                  longer a face and is dropped.
        INTERNAL  intermediate walls, centre lines, lattice, cap
                  extensions — internal print geometry. Kept inside the
                  wall, dropped in voids (never extrudes across a void).
        WIRE      the single bead of a zero-thickness system. Always kept.
        VIRTUAL   non-printed region boundaries (attached-end root lines,
                  opening clear lines, cavity closers). Printed only
                  where they end up separating wall from void.

The arrangement keeps every face (region) with its classification, so a
future paint-bucket UI can hover / toggle regions, and region-filling
lattice can target them, without changing this representation.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

from model import (Vec2, _point_in_polygon, _polygon_area,
                   _project_to_polyline, _cum_lengths, _sub_polyline)

TOL = 1e-6          # contact / node-merge tolerance (= graph JUNCTION_TOL)
SNAP_TOL = 1e-3     # open source ends this close to another source snap onto it

FACE, INTERNAL, WIRE, VIRTUAL = 'face', 'internal', 'wire', 'virtual'
SPLIT = 'split'     # region boundary for classification only — NEVER printed
                    # (e.g. the mouth where a two-pass branch joins a
                    # single-bead host: material on one side, but the
                    # route must pass through it, not along it)
_PRIORITY = {WIRE: 0, FACE: 1, INTERNAL: 2, VIRTUAL: 3, SPLIT: 4}

FACE_RETRACE_COST = 3.0     # retracing a visible face costs 3× internal geometry
MITER_LIMIT = 10.0          # hub corner further than this × W → bevel


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------

def _bbox(pts, pad=0.0):
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return (min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)


def _bbox_overlap(a, b):
    return not (a[0] > b[1] or a[1] < b[0] or a[2] > b[3] or a[3] < b[2])


def _in_bbox(p, bb):
    return bb[0] <= p.x <= bb[1] and bb[2] <= p.y <= bb[3]


def _segs(pts, closed):
    n = len(pts)
    m = n if closed and n > 2 else n - 1
    return [(pts[i], pts[(i + 1) % n]) for i in range(m)]


def _pt_seg(p, a, b):
    """(distance, t) of p to segment a-b."""
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    if L2 < 1e-24:
        return p.dist(a), 0.0
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2
    tc = max(0.0, min(1.0, t))
    return math.hypot(p.x - (a.x + tc * dx), p.y - (a.y + tc * dy)), t


def dist_to_polyline(p, pts, closed):
    best = float('inf')
    for a, b in _segs(pts, closed):
        best = min(best, _pt_seg(p, a, b)[0])
    return best


def _ray_hits(origin, direction, pts, closed, t_min, t_max):
    """Parameters t (origin + t·direction) where the LINE crosses the
    polyline, restricted to [t_min, t_max]. direction is unit length."""
    hits = []
    for a, b in _segs(pts, closed):
        ex, ey = b.x - a.x, b.y - a.y
        cross = direction.x * ey - direction.y * ex
        if abs(cross) < 1e-12:
            continue
        wx, wy = a.x - origin.x, a.y - origin.y
        t = (wx * ey - wy * ex) / cross
        u = (wx * direction.y - wy * direction.x) / cross
        if -1e-9 <= u <= 1 + 1e-9 and t_min <= t <= t_max:
            hits.append((t, Vec2(origin.x + t * direction.x,
                                 origin.y + t * direction.y)))
    return hits


def _end_dir(pts, which):
    """Unit tangent at an end of a polyline pointing INTO its body."""
    if which == 0:
        for q in pts[1:]:
            if q.dist(pts[0]) > 1e-9:
                return (q - pts[0]).normalized()
    else:
        for q in reversed(pts[:-1]):
            if q.dist(pts[-1]) > 1e-9:
                return (q - pts[-1]).normalized()
    return Vec2(0.0, 0.0)


# ---------------------------------------------------------------------------
# Shapes for region classification (even-odd rings)
# ---------------------------------------------------------------------------

@dataclass
class Shape:
    rings: list            # list of closed point rings; inside = odd count
    bbox: tuple = None

    def __post_init__(self):
        self.rings = [r for r in self.rings if len(r) >= 3]
        if self.rings:
            self.bbox = _bbox([p for r in self.rings for p in r])

    def contains(self, p) -> bool:
        if not self.rings or not _in_bbox(p, self.bbox):
            return False
        k = sum(1 for r in self.rings if _point_in_polygon(p, r))
        return k % 2 == 1


# ---------------------------------------------------------------------------
# Beads
# ---------------------------------------------------------------------------

@dataclass
class Bead:
    id: str
    pts: list
    closed: bool
    cls: str               # FACE / INTERNAL / WIRE / VIRTUAL
    element: str           # 'S:<source id>' or 'L:<lattice id>'
    path: object = None    # the effective Path it came from (None = new)
    face_id: Optional[str] = None   # wall face it belongs to (joins: the
                                    # face they extend); default: base id

    @property
    def face(self) -> str:
        return self.face_id or self.id.split('~')[0]


# ---------------------------------------------------------------------------
# Segment index
# ---------------------------------------------------------------------------

class _Grid:
    def __init__(self, cell=8.0):
        self.cell = cell
        self.cells: dict = {}

    def _keys(self, bb):
        c = self.cell
        for gx in range(math.floor(bb[0] / c), math.floor(bb[1] / c) + 1):
            for gy in range(math.floor(bb[2] / c), math.floor(bb[3] / c) + 1):
                yield (gx, gy)

    def insert(self, idx, bb):
        for k in self._keys(bb):
            self.cells.setdefault(k, []).append(idx)

    def query(self, bb):
        out = set()
        for k in self._keys(bb):
            out.update(self.cells.get(k, ()))
        return out


def _seg_list(beads):
    """[(bead_idx, seg_idx, a, b)] for every non-degenerate segment."""
    segs = []
    for bi, bd in enumerate(beads):
        for si, (a, b) in enumerate(_segs(bd.pts, bd.closed)):
            if a.dist(b) > 1e-12:
                segs.append((bi, si, a, b))
    return segs


def _adjacent(beads, s1, s2):
    """Consecutive segments of the same bead share a vertex by construction."""
    if s1[0] != s2[0]:
        return False
    bd = beads[s1[0]]
    m = len(bd.pts) if bd.closed and len(bd.pts) > 2 else len(bd.pts) - 1
    d = abs(s1[1] - s2[1])
    return d == 1 or (bd.closed and d == m - 1)


def _contact_events(segs, beads, tol=TOL):
    """
    All contacts between segments: proper crossings and vertex-on-segment
    touches (which also covers collinear overlaps). Yields
    (i, t_i, point, j, t_j); t_j is None when only segment i is split.
    """
    grid = _Grid()
    boxes = []
    for k, (_, _, a, b) in enumerate(segs):
        bb = (min(a.x, b.x) - tol, max(a.x, b.x) + tol,
              min(a.y, b.y) - tol, max(a.y, b.y) + tol)
        boxes.append(bb)
        grid.insert(k, bb)
    for i, (bi, si, a, b) in enumerate(segs):
        for j in grid.query(boxes[i]):
            if j <= i:
                continue
            bj, sj, c, d = segs[j]
            if bi == bj and _adjacent(beads, segs[i], segs[j]):
                continue
            if not _bbox_overlap(boxes[i], boxes[j]):
                continue
            L1, L2 = a.dist(b), c.dist(d)
            # vertex-on-segment touches (T junctions, collinear overlaps)
            for p in (c, d):
                dist, t = _pt_seg(p, a, b)
                if dist <= tol and tol < t * L1 < L1 - tol:
                    yield (i, t, p, None, None)
            for p in (a, b):
                dist, t = _pt_seg(p, c, d)
                if dist <= tol and tol < t * L2 < L2 - tol:
                    yield (j, t, p, None, None)
            # proper crossing
            rx, ry = b.x - a.x, b.y - a.y
            qx, qy = d.x - c.x, d.y - c.y
            cross = rx * qy - ry * qx
            if abs(cross) <= 1e-9 * L1 * L2:
                continue
            wx, wy = c.x - a.x, c.y - a.y
            t = (wx * qy - wy * qx) / cross
            u = (wx * ry - wy * rx) / cross
            if tol < t * L1 < L1 - tol and tol < u * L2 < L2 - tol:
                x = Vec2(a.x + t * rx, a.y + t * ry)
                yield (i, t, x, j, u)


def find_contacts(beads, tol=TOL) -> set:
    """Pairs of elements whose beads touch or cross (incl. shared ends)."""
    segs = _seg_list(beads)
    pairs = set()
    for i, j in _touching_segment_pairs(segs, tol):
        ei, ej = beads[segs[i][0]].element, beads[segs[j][0]].element
        if ei != ej:
            pairs.add(tuple(sorted((ei, ej))))
    return pairs


def _touching_segment_pairs(segs, tol):
    grid = _Grid()
    boxes = []
    for k, (_, _, a, b) in enumerate(segs):
        bb = (min(a.x, b.x) - tol, max(a.x, b.x) + tol,
              min(a.y, b.y) - tol, max(a.y, b.y) + tol)
        boxes.append(bb)
        grid.insert(k, bb)
    out = []
    for i, (bi, _, a, b) in enumerate(segs):
        for j in grid.query(boxes[i]):
            if j <= i or segs[j][0] == bi:
                continue
            if not _bbox_overlap(boxes[i], boxes[j]):
                continue
            c, d = segs[j][2], segs[j][3]
            if _segments_touch(a, b, c, d, tol):
                out.append((i, j))
    return out


def _segments_touch(a, b, c, d, tol):
    if min(_pt_seg(c, a, b)[0], _pt_seg(d, a, b)[0],
           _pt_seg(a, c, d)[0], _pt_seg(b, c, d)[0]) <= tol:
        return True
    rx, ry = b.x - a.x, b.y - a.y
    qx, qy = d.x - c.x, d.y - c.y
    cross = rx * qy - ry * qx
    if abs(cross) < 1e-15:
        return False
    wx, wy = c.x - a.x, c.y - a.y
    t = (wx * qy - wy * qx) / cross
    u = (wx * ry - wy * rx) / cross
    return 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0


# ---------------------------------------------------------------------------
# Planar arrangement
# ---------------------------------------------------------------------------

@dataclass
class Face:
    """A region of the arrangement: outer boundary + holes, wall or void."""
    id: int
    outer: list                    # CCW ring (empty for the unbounded face)
    holes: list = field(default_factory=list)
    area: float = 0.0
    sample: Optional[Vec2] = None
    material: bool = False
    override: Optional[str] = None  # 'wall' | 'void' if painted
    region: int = 0                 # faces with the same region form one
                                    # paintable region (split only by lattice)


class Arrangement:
    """
    Planar subdivision of the plane by a set of beads.

    Nodes are merged within TOL; edges shared by several beads (collinear
    overlaps) are one edge with several owners. Faces are traced with the
    usual half-edge rule (face on the left); bounded faces are CCW rings,
    each component's outline is a CW ring that becomes a hole of the face
    containing it.
    """

    def __init__(self, beads: list[Bead], tol: float = TOL):
        self.beads = beads
        self.tol = tol
        segs = _seg_list(beads)
        splits = [[] for _ in segs]
        for i, t, p, j, u in _contact_events(segs, beads, tol):
            splits[i].append((t, p))
            if j is not None:
                splits[j].append((u, p))

        # Nodes: bead vertices first (they keep exact coordinates), then
        # computed split points; anything within tol merges.
        self.nodes: list[Vec2] = []
        self._grid: dict = {}
        for bd in beads:
            for p in bd.pts:
                self._node(p)

        self.edges: dict = {}          # (u, v) u<v → list of bead idx
        self.bead_edges: list = [[] for _ in beads]   # [(u, v)] in order
        for k, (bi, si, a, b) in enumerate(segs):
            chain = [a] + [p for _, p in sorted(splits[k], key=lambda e: e[0])] + [b]
            ids = [self._node(p) for p in chain]
            for u, v in zip(ids, ids[1:]):
                if u == v:
                    continue
                key = (min(u, v), max(u, v))
                owners = self.edges.setdefault(key, [])
                if bi not in owners:
                    owners.append(bi)
                self.bead_edges[bi].append((u, v))
        self._trace_faces()

    def find_node(self, p: Vec2) -> Optional[int]:
        """Existing node within tol of p, or None."""
        c = self.tol * 4
        key = (math.floor(p.x / c), math.floor(p.y / c))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in self._grid.get((key[0] + dx, key[1] + dy), ()):
                    if self.nodes[idx].dist(p) <= self.tol:
                        return idx
        return None

    def _node(self, p: Vec2) -> int:
        c = self.tol * 4
        key = (math.floor(p.x / c), math.floor(p.y / c))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in self._grid.get((key[0] + dx, key[1] + dy), ()):
                    if self.nodes[idx].dist(p) <= self.tol:
                        return idx
        idx = len(self.nodes)
        self.nodes.append(Vec2(p.x, p.y))
        self._grid.setdefault(key, []).append(idx)
        return idx

    # -- faces ---------------------------------------------------------------

    def _trace_faces(self):
        nbrs: dict = {}
        for u, v in self.edges:
            nbrs.setdefault(u, []).append(v)
            nbrs.setdefault(v, []).append(u)
        order = {}
        for u, vs in nbrs.items():
            pu = self.nodes[u]
            vs.sort(key=lambda v: math.atan2(self.nodes[v].y - pu.y,
                                             self.nodes[v].x - pu.x))
            order[u] = {v: i for i, v in enumerate(vs)}
        self._nbrs = nbrs

        def nxt(u, v):
            vs = nbrs[v]
            return v, vs[(order[v][u] - 1) % len(vs)]

        self.cycle_of: dict = {}       # half-edge (u, v) → cycle index
        cycles = []
        for (a, b) in self.edges:
            for he in ((a, b), (b, a)):
                if he in self.cycle_of:
                    continue
                cyc = []
                cur = he
                while cur not in self.cycle_of:
                    self.cycle_of[cur] = len(cycles)
                    cyc.append(cur)
                    cur = nxt(*cur)
                cycles.append(cyc)
        self.cycles = cycles
        self.cycle_pts = [[self.nodes[u] for u, _ in c] for c in cycles]
        self.cycle_area = [_polygon_area(pts) for pts in self.cycle_pts]
        self.cycle_sample = [self._sample(i) for i in range(len(cycles))]

        # Faces: one per positive (CCW) cycle + the unbounded face (id 0).
        self.faces: list[Face] = [Face(0, [], area=float('inf'))]
        self.face_of_cycle: dict = {}
        positive = [i for i, a in enumerate(self.cycle_area) if a > 1e-12]
        for i in positive:
            f = Face(len(self.faces), self.cycle_pts[i],
                     area=self.cycle_area[i], sample=self.cycle_sample[i])
            self.faces.append(f)
            self.face_of_cycle[i] = f.id
        pos_faces = sorted(self.faces[1:], key=lambda f: f.area)
        for i, a in enumerate(self.cycle_area):
            if i in self.face_of_cycle:
                continue
            s = self.cycle_sample[i]
            host = next((f for f in pos_faces
                         if s is not None and _point_in_polygon(s, f.outer)), None)
            fid = host.id if host else 0
            self.face_of_cycle[i] = fid
            if host is not None and len(self.cycle_pts[i]) >= 3:
                host.holes.append(self.cycle_pts[i])

    def _sample(self, ci):
        """A point just to the LEFT of the cycle (inside its face)."""
        cyc = self.cycles[ci]
        pts = self.cycle_pts[ci]
        area = _polygon_area(pts)
        cand = sorted(cyc, key=lambda he: -self.nodes[he[0]].dist(self.nodes[he[1]]))
        first = None
        for u, v in cand[:6]:
            a, b = self.nodes[u], self.nodes[v]
            L = a.dist(b)
            if L < 1e-12:
                continue
            eps = min(1e-3, 0.05 * L)
            m = a.lerp(b, 0.5)
            p = Vec2(m.x - (b.y - a.y) / L * eps, m.y + (b.x - a.x) / L * eps)
            if first is None:
                first = p
            if len(pts) < 3:
                return p
            inside = _point_in_polygon(p, pts)
            if inside == (area > 0):
                return p
        return first

    def face_at(self, p: Vec2) -> int:
        """Face containing p (smallest bounded face whose outer ring holds it)."""
        best = None
        for f in self.faces[1:]:
            if _point_in_polygon(p, f.outer) and (best is None or f.area < best.area):
                best = f
        # A point inside a hole of `best` lies in a face of the hole's own
        # component, which is smaller and would have been chosen instead.
        return best.id if best is not None else 0

    def edge_faces(self, u, v):
        """(left face id, right face id) of the edge traversed u → v."""
        return (self.face_of_cycle[self.cycle_of[(u, v)]],
                self.face_of_cycle[self.cycle_of[(v, u)]])


# ---------------------------------------------------------------------------
# Resolution: which beads (pieces) survive
# ---------------------------------------------------------------------------

def _keep(cls, left, right):
    if cls == WIRE:
        return True
    if cls == FACE or cls == VIRTUAL:
        return left != right
    if cls == INTERNAL:
        return left or right
    return False


@dataclass
class ResolvedBead:
    bead: Bead
    whole: bool                 # survives unchanged (same id, same shape)
    chains: list                # surviving pieces as open point lists
                                # ([bead.pts] if whole)


@dataclass
class NetworkResult:
    beads: list                 # [ResolvedBead]
    junctions: list             # [Vec2]
    faces: list                 # [Face]
    arrangement: Arrangement
    emitter: dict = field(default_factory=dict)   # edge → emitting bead idx
    material: dict = field(default_factory=dict)  # face id → is wall


def resolve(beads: list[Bead], material: list[Shape], voids: list[Shape],
            overrides: list[tuple] = ()) -> NetworkResult:
    """
    Build the arrangement, classify faces, decide survival per edge and
    regroup surviving edges per bead.

    overrides: [(point, 'wall' | 'void')] — the face containing point is
    forced to that class (paint-bucket region override).
    """
    arr = Arrangement(beads)

    def in_material(p):
        if p is None:
            return False
        if any(v.contains(p) for v in voids):
            return False
        return any(s.contains(p) for s in material)

    for f in arr.faces[1:]:
        f.material = in_material(f.sample)

    # Regions: faces joined across edges that only internal geometry
    # (lattice, centre lines) runs along — a lattice subdivides a wall, it
    # does not bound a region. A paint override applies to the whole region.
    uf = UnionFind()
    for (u, v), owners in arr.edges.items():
        if all(beads[b].cls == INTERNAL for b in owners):
            lf, rf = arr.edge_faces(u, v)
            uf.union(lf, rf)
    for f in arr.faces:
        f.region = uf.find(f.id)
    for pt, kind in overrides:
        fid = arr.face_at(pt)
        reg = arr.faces[fid].region
        if reg == arr.faces[0].region:
            continue                       # the outside is never wall
        for f in arr.faces[1:]:
            if f.region == reg:
                f.material = (kind == 'wall')
                f.override = kind

    mat = {f.id: f.material for f in arr.faces}

    # Edge survival + emitter (the highest-priority owner that keeps it).
    emitter: dict = {}
    for (u, v), owners in arr.edges.items():
        lf, rf = arr.edge_faces(u, v)
        L, R = mat[lf], mat[rf]
        for bi in sorted(owners, key=lambda b: (_PRIORITY[beads[b].cls], b)):
            if _keep(beads[bi].cls, L, R):
                emitter[(u, v)] = bi
                break

    resolved = []
    for bi, bd in enumerate(beads):
        seq = arr.bead_edges[bi]
        mine = [emitter.get((min(u, v), max(u, v))) == bi for u, v in seq]
        if not seq:
            resolved.append(ResolvedBead(bd, False, []))
            continue
        if all(mine) and bd.cls != VIRTUAL:
            resolved.append(ResolvedBead(bd, True, [bd.pts]))
            continue
        if bd.closed and not all(mine) and any(mine):
            k = mine.index(False)
            seq = seq[k + 1:] + seq[:k + 1]
            mine = mine[k + 1:] + mine[:k + 1]
        chains, cur = [], []
        for (u, v), ok in zip(seq, mine):
            if ok:
                if not cur:
                    cur = [u]
                elif cur[-1] != u:            # discontinuity (degenerate)
                    chains.append(cur)
                    cur = [u]
                cur.append(v)
            elif cur:
                chains.append(cur)
                cur = []
        if cur:
            chains.append(cur)
        # (a closed VIRTUAL ring printed all round comes out as one chain
        # that returns to its start)
        resolved.append(ResolvedBead(
            bd, False, [[arr.nodes[n] for n in c] for c in chains if len(c) >= 2]))

    # Junction markers: surviving nodes where walls of ≥ 2 different
    # systems meet (T, X, spliced faces, hub corners). Lattice landing on
    # its walls is ordinary internal structure, not a network junction.
    walls_at: dict = {}
    for (u, v), bi in emitter.items():
        el = beads[bi].element
        if el.startswith('S:'):
            for n in (u, v):
                walls_at.setdefault(n, set()).add(el)
    junctions = [arr.nodes[n] for n, els in walls_at.items() if len(els) >= 2]
    return NetworkResult(resolved, junctions, arr.faces, arr, emitter, mat)


# ---------------------------------------------------------------------------
# Wall-system pieces and attached ends
# ---------------------------------------------------------------------------

@dataclass
class NetPiece:
    """One stretch of a wall system (whole system, or a piece between
    opening cuts). walls: [(distance, pts, bead_id)] sorted by distance,
    descending — walls[0] / walls[-1] are its two faces."""
    sys_id: str
    key: str
    closed: bool
    walls: list
    end_kinds: list                    # [start, end]: 'free' | 'cut' | None
    caps: list = field(default_factory=lambda: [None, None])
    cap_ids: list = field(default_factory=lambda: [[], []])
    attached: list = field(default_factory=lambda: [False, False])
    ref_only: bool = False      # the source is an unprinted reference line

    def printed_internal(self, wi) -> bool:
        return not (self.ref_only and abs(self.walls[wi][0]) < 1e-12)

    @property
    def thick(self) -> bool:
        return len(self.walls) >= 2 and \
            self.walls[0][0] - self.walls[-1][0] > 1e-9

    @property
    def width(self) -> float:
        return self.walls[0][0] - self.walls[-1][0] if self.walls else 0.0

    def src(self):
        return next(w for w in self.walls if abs(w[0]) < 1e-12)[1]

    def wall_class(self, idx) -> str:
        if not self.thick:
            return WIRE
        d = self.walls[idx][0]
        if abs(d - self.walls[0][0]) < 1e-9 or abs(d - self.walls[-1][0]) < 1e-9:
            return FACE
        return INTERNAL

    def end_pt(self, which, wall_idx=None):
        pts = self.src() if wall_idx is None else self.walls[wall_idx][1]
        return pts[0] if which == 0 else pts[-1]

    def band(self) -> Optional[Shape]:
        if not self.thick:
            return None
        out, inn = self.walls[0][1], self.walls[-1][1]
        if self.closed:
            return Shape([out, inn])
        ring = list(out)
        if self.caps[1] and not self.attached[1]:
            ring += self.caps[1][1:-1]
        ring += list(reversed(inn))
        if self.caps[0] and not self.attached[0]:
            ring += list(reversed(self.caps[0]))[1:-1]
        return Shape([ring])


@dataclass
class EndJoin:
    """Geometry added where attached ends meet a host / each other."""
    beads: list = field(default_factory=list)      # [Bead]
    material: list = field(default_factory=list)   # [Shape]


def _face_walls(piece):
    return [i for i in range(len(piece.walls))
            if piece.wall_class(i) == FACE] if piece.thick else [0]


def find_attachments(pieces: list[NetPiece], tol=TOL):
    """
    Classify every free original end of a THICK piece:
      ('T', host_piece, contact)  — lands on another system's wall bead
                                   (anywhere except that piece's own end),
                                   or lies inside another thick wall
      ('hub', point)              — meets other thick free ends at a point
    Unlisted ends are free (keep their caps). Wires never need joins.
    """
    result = {}
    ends = []
    for pc in pieces:
        if pc.closed:
            continue
        for which in (0, 1):
            if pc.end_kinds[which] == 'free':
                ends.append((pc, which))
    for pc, which in ends:
        if not pc.thick:
            continue
        e = pc.end_pt(which)
        host = None
        for q in pieces:
            if q.sys_id == pc.sys_id:
                continue
            for wi, (d, pts, _) in enumerate(q.walls):
                if dist_to_polyline(e, pts, q.closed) > tol:
                    continue
                at_end = (not q.closed and
                          (e.dist(pts[0]) <= tol or e.dist(pts[-1]) <= tol))
                if at_end and abs(d) < 1e-12:
                    continue                    # another source's end → hub
                host = q
                break
            if host is not None:
                break
        if host is None:
            # An end buried inside another thick wall is also attached:
            # its faces already overlap the host (the union trims them),
            # but its internal walls must run on to a host wall instead of
            # dangling in the cavity.
            for q in pieces:
                if q.sys_id != pc.sys_id and q.thick:
                    if not q.closed and any(e.dist(q.end_pt(w)) <= tol for w in (0, 1)):
                        continue        # on that piece's own end → hub (as above)
                    band = q.band()
                    if band is not None and band.contains(e):
                        host = q
                        break
        if host is not None:
            result[(id(pc), which)] = ('T', host, e)
    # Hubs: thick free ends (not already T) sharing a point.
    rest = [(pc, w) for pc, w in ends
            if pc.thick and (id(pc), w) not in result]
    groups: list = []
    for pc, w in rest:
        e = pc.end_pt(w)
        for g in groups:
            if g[0].dist(e) <= tol:
                g[1].append((pc, w))
                break
        else:
            groups.append((e, [(pc, w)]))
    for pt, members in groups:
        if len({pc.sys_id for pc, _ in members}) >= 2:
            for pc, w in members:
                result[(id(pc), w)] = ('hub', pt, members)
    return result


def _limit(*widths):
    return 3.0 * sum(widths) + 1.0


def join_T(pc: NetPiece, which: int, host: NetPiece, tag: str) -> Optional[EndJoin]:
    """
    A thick end landing on a host system (T junction). The end's faces
    are extended along their own end tangent to the host's NEAR face
    (the host face on the branch's side), the gap between the branch's
    root line and that face becomes wall, and internal walls run on to
    the next host wall behind the near face. Returns None if the faces
    cannot reach the host (then the end keeps its normal cap).
    """
    e = pc.end_pt(which)
    d_body = _end_dir(pc.src(), which)
    probe = Vec2(e.x + d_body.x * (pc.width / 2 + 1.0),
                 e.y + d_body.y * (pc.width / 2 + 1.0))
    host_faces = _face_walls(host)
    near_i = min(host_faces, key=lambda i: dist_to_polyline(
        probe, host.walls[i][1], host.closed))
    near = host.walls[near_i][1]
    lim = _limit(pc.width, host.width)
    join = EndJoin()
    hits = {}
    near_cum = _cum_lengths(near, host.closed)
    for wi in (0, len(pc.walls) - 1):
        pts = pc.walls[wi][1]
        ew = pts[0] if which == 0 else pts[-1]
        g = _end_dir(pts, which) * -1.0             # toward the host
        hs = _ray_hits(ew, g, near, host.closed, -lim, lim)
        if hs:
            t, h = min(hs, key=lambda th: abs(th[0]))
        else:
            # A face arriving almost tangentially to a curved host can
            # miss it altogether: close the gap to the nearest point.
            _, d, h = _project_to_polyline(ew, near, near_cum, host.closed)
            if d > lim:
                return None
            t = d
        hits[wi] = (ew, t, h)
    for wi, (ew, t, h) in hits.items():
        if t > TOL:
            join.beads.append(Bead(f'{tag}_f{wi}', [ew, h], False, FACE,
                                   f'S:{pc.sys_id}',
                                   face_id=pc.walls[wi][2].split('~')[0]))
    # Internal walls (intermediates, a centre-line source) continue to the
    # next host wall behind the near face (the near face is gone across
    # the mouth); a host with one wall is its own target.
    targets = [i for i in range(len(host.walls)) if i != near_i] or [near_i]
    for wi in range(1, len(pc.walls) - 1):
        if not pc.printed_internal(wi):
            continue
        pts = pc.walls[wi][1]
        ew = pts[0] if which == 0 else pts[-1]
        if any(dist_to_polyline(ew, host.walls[i][1], host.closed) <= TOL
               for i in targets):
            continue                                # already ends on one
        g = _end_dir(pts, which) * -1.0
        best = None
        for i in targets:
            for t, h in _ray_hits(ew, g, host.walls[i][1], host.closed,
                                  TOL, lim):
                if best is None or t < best[0]:
                    best = (t, h)
        if best is None:
            # Nearly tangential arrival: the straight continuation misses.
            # Internal geometry may turn — connect to the nearest point of
            # the next host wall (the arrangement drops it if it ever
            # leaves the wall material).
            for i in targets:
                pts_i = host.walls[i][1]
                _, d, foot = _project_to_polyline(
                    ew, pts_i, _cum_lengths(pts_i, host.closed), host.closed)
                if d <= lim and (best is None or d < best[0]):
                    best = (d, foot)
        if best is not None:
            join.beads.append(Bead(f'{tag}_i{wi}', [ew, best[1]], False,
                                   INTERNAL, f'S:{pc.sys_id}'))
    # Junction fill: root line → faces' hits → along the near face.
    (e_out, _, h_out), (e_in, _, h_in) = hits[0], hits[len(pc.walls) - 1]
    cum = _cum_lengths(near, host.closed)
    s_out = _project_to_polyline(h_out, near, cum, host.closed)[0]
    s_in = _project_to_polyline(h_in, near, cum, host.closed)[0]
    if host.closed:
        L = cum[-1]
        fwd = (s_in - s_out) % L
        if fwd <= L / 2:
            along = _sub_polyline(near, cum, s_out, s_out + fwd, True)
        else:
            along = list(reversed(_sub_polyline(near, cum, s_in,
                                                s_in + (L - fwd), True)))
    elif s_in >= s_out:
        along = _sub_polyline(near, cum, s_out, s_in, False)
    else:
        along = list(reversed(_sub_polyline(near, cum, s_in, s_out, False)))
    # May be a bow-tie (one face overshoots the host face, the other falls
    # short): even-odd keeps both lobes, and its signed area can cancel to
    # zero, so only reject a ring with no extent.
    ring = [e_out, h_out] + along[1:-1] + [h_in, e_in]
    if _has_extent(ring):
        join.material.append(Shape([ring]))
    join.beads.append(_root_line(pc, which, f'{tag}_root'))
    return join


def _has_extent(ring) -> bool:
    """Encloses some area (any lobe), unlike a ring folded onto a line."""
    if len(ring) < 3:
        return False
    a = ring[0]
    return any(abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) > 1e-9
               for b, c in zip(ring[1:], ring[2:]))


def _root_line(pc, which, bead_id):
    ends = [w[1][0] if which == 0 else w[1][-1] for w in pc.walls]
    return Bead(bead_id, ends, False, VIRTUAL, f'S:{pc.sys_id}')


def join_hub(members, point, tag: str) -> Optional[EndJoin]:
    """
    Two or more thick ends meeting at one point (corner, Y, ...). Arms are
    taken in angular order; between consecutive arms the left face of one
    and the right face of the next are mitred (extended to meet), or
    bevelled when the corner would be a spike. The polygon through the
    arms' face ends and the corners is wall.
    """
    arms = []
    for pc, which in members:
        d = _end_dir(pc.src(), which)
        if d.length() < 1e-12:
            return None
        n = Vec2(-d.y, d.x)
        ends = []
        for wi, (_, pts, _) in enumerate(pc.walls):
            ew = pts[0] if which == 0 else pts[-1]
            ends.append((wi, ew, _end_dir(pts, which)))
        side = lambda item: (item[1].x - point.x) * n.x + (item[1].y - point.y) * n.y
        left = max(ends, key=side)
        right = min(ends, key=side)
        arms.append((math.atan2(d.y, d.x), pc, which, left, right))
    arms.sort(key=lambda a: a[0])
    join = EndJoin()
    max_w = max(a[1].width for a in arms)
    ring = []
    k = len(arms)
    for i in range(k):
        _, pc, which, left, right = arms[i]
        _, pc2, which2, left2, right2 = arms[(i + 1) % k]
        ring += [right[1], left[1]]
        A, dA = left[1], left[2]
        B, dB = right2[1], right2[2]
        cross = dA.x * dB.y - dA.y * dB.x
        corner = None
        if abs(cross) > 1e-9:
            wx, wy = B.x - A.x, B.y - A.y
            t = (wx * dB.y - wy * dB.x) / cross
            u = (wx * dA.y - wy * dA.x) / cross
            if max(-t, -u) <= MITER_LIMIT * max_w:
                corner = (Vec2(A.x + t * dA.x, A.y + t * dA.y), t, u)
        fa = pc.walls[left[0]][2].split('~')[0]
        fb = pc2.walls[right2[0]][2].split('~')[0]
        if corner is None:
            if A.dist(B) > TOL:
                join.beads.append(Bead(f'{tag}_b{i}', [A, B], False, FACE,
                                       f'S:{pc.sys_id}', face_id=fa))
            continue
        P, t, u = corner
        if t < -TOL:
            join.beads.append(Bead(f'{tag}_m{i}a', [A, P], False, FACE,
                                   f'S:{pc.sys_id}', face_id=fa))
        if u < -TOL:
            join.beads.append(Bead(f'{tag}_m{i}b', [B, P], False, FACE,
                                   f'S:{pc2.sys_id}', face_id=fb))
        ring.append(P)
    if _has_extent(ring):
        join.material.append(Shape([ring]))
    for idx, (_, pc, which, _, _) in enumerate(arms):
        join.beads.append(_root_line(pc, which, f'{tag}_root{idx}'))
        # intermediate walls run on until they meet another arm's wall
        others = [w[1] for a in arms if a[1] is not pc for w in a[1].walls]
        for wi in range(1, len(pc.walls) - 1):
            if not pc.printed_internal(wi):
                continue
            pts = pc.walls[wi][1]
            ew = pts[0] if which == 0 else pts[-1]
            if any(dist_to_polyline(ew, o, False) <= TOL for o in others):
                continue
            g = _end_dir(pts, which) * -1.0
            best = None
            for o in others:
                for t, h in _ray_hits(ew, g, o, False, TOL, _limit(max_w)):
                    if best is None or t < best[0]:
                        best = (t, h)
            if best is not None:
                join.beads.append(Bead(f'{tag}_a{idx}i{wi}', [ew, best[1]],
                                       False, INTERNAL, f'S:{pc.sys_id}'))
    return join


# ---------------------------------------------------------------------------
# Union-find
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


# ---------------------------------------------------------------------------
# Material components (printable wall regions) and their boundary rings
# ---------------------------------------------------------------------------

@dataclass
class MaterialComponent:
    """
    One connected piece of printable wall material: the union of material
    faces joined across edges with wall on both sides. Its boundary is a
    set of rings with the material on their LEFT — the outer outline
    (CCW) plus any number of holes (CW): cavities, rooms, islands,
    doorways. This is what region infill fills.
    """
    id: int
    faces: set
    rings: list                  # [[Vec2]] closed, material on the left

    def contains(self, p) -> bool:
        return sum(1 for r in self.rings if _point_in_polygon(p, r)) % 2 == 1


def material_components(res: NetworkResult) -> tuple:
    """(components, face id → component id) of a resolved network."""
    arr, mat = res.arrangement, res.material
    uf = UnionFind()
    for (u, v) in arr.edges:
        lf, rf = arr.edge_faces(u, v)
        if mat.get(lf) and mat.get(rf):
            uf.union(lf, rf)
    groups: dict = {}
    for f in arr.faces[1:]:
        if f.material:
            groups.setdefault(uf.find(f.id), set()).add(f.id)
    comp_of = {}
    comps = []
    for k, (_, faces) in enumerate(sorted(groups.items())):
        for fid in faces:
            comp_of[fid] = k
        comps.append(MaterialComponent(k, faces, []))
    # Boundary half-edges: wall on the left, not on the right. Traced with
    # the same turning rule as faces, restricted to boundary edges.
    bnd = set()
    for (u, v) in arr.edges:
        for a, b in ((u, v), (v, u)):
            lf, rf = arr.edge_faces(a, b)
            if mat.get(lf) and not mat.get(rf):
                bnd.add((a, b))
    seen = set()
    nbrs = arr._nbrs
    for he in sorted(bnd):
        if he in seen:
            continue
        ring_nodes = []
        cur = he
        while cur not in seen:
            seen.add(cur)
            ring_nodes.append(cur[0])
            u, v = cur
            vs = nbrs[v]
            i = vs.index(u)
            nxt = None
            for k in range(1, len(vs) + 1):
                w = vs[(i - k) % len(vs)]
                if (v, w) in bnd:
                    nxt = (v, w)
                    break
            if nxt is None:
                break
            cur = nxt
        if len(ring_nodes) >= 3:
            lf, _ = arr.edge_faces(*he)
            comps[comp_of[lf]].rings.append(
                [arr.nodes[n] for n in ring_nodes])
    return comps, comp_of


# ---------------------------------------------------------------------------
# Junction corners — corners CREATED by the network (not source corners)
# ---------------------------------------------------------------------------

@dataclass
class JunctionCorner:
    """
    A corner of the resolved wall boundary where faces of two different
    wall systems meet (T splice, X crossing, hub mitre, inner corner). Its
    treatment is independent of the sources' own Corner R.

    key: the two wall faces that meet ('|'-joined, sorted) + '#ordinal'
    among corners of the same face pair. Derived from topology, not XY:
    it survives moving / reshaping walls as long as the same faces meet.
    """
    node: int
    pt: Vec2
    key: str
    ends: list                   # [(resolved bead, chain index, end 0|-1)] × 2


def junction_corners(res: NetworkResult) -> list:
    arr = res.arrangement
    face_deg: dict = {}
    for (u, v), bi in res.emitter.items():
        if res.beads[bi].bead.cls in (FACE, VIRTUAL):
            for n in (u, v):
                face_deg[n] = face_deg.get(n, 0) + 1
    ends: dict = {}
    for rb in res.beads:
        if rb.bead.cls not in (FACE, VIRTUAL):
            continue
        for ci, ch in enumerate(rb.chains):
            if len(ch) < 2 or (rb.bead.closed and rb.whole):
                continue
            for end in (0, -1):
                n = arr.find_node(ch[end])
                if n is not None:
                    ends.setdefault(n, []).append((rb, ci, end))
    corners = []
    for n, es in ends.items():
        if len(es) != 2 or face_deg.get(n) != 2:
            continue
        (r1, c1, e1), (r2, c2, e2) = es
        if r1.bead.element == r2.bead.element:
            continue                    # a wall's own end / continuation
        l1, l2 = _leg(r1.chains[c1], e1), _leg(r2.chains[c2], e2)
        d1, d2 = _leg_dir(l1), _leg_dir(l2)
        if d1 is None or d2 is None:
            continue
        cosang = d1.x * d2.x + d1.y * d2.y
        if cosang < -math.cos(math.radians(2.0)):
            continue                    # straight continuation: no corner
        pair = '|'.join(sorted((r1.bead.face, r2.bead.face)))
        corners.append(JunctionCorner(n, arr.nodes[n], pair, es))
    # ordinal among corners sharing a face pair (stable: by position)
    by_pair: dict = {}
    for c in corners:
        by_pair.setdefault(c.key, []).append(c)
    for pair, cs in by_pair.items():
        cs.sort(key=lambda c: (round(c.pt.x, 3), round(c.pt.y, 3)))
        for k, c in enumerate(cs):
            c.key = f'{pair}#{k}'
    return corners


def _leg(chain, end):
    return list(chain) if end == 0 else list(reversed(chain))


def _leg_dir(leg):
    for q in leg[1:]:
        d = q - leg[0]
        if d.length() > 1e-9:
            return d.normalized()
    return None


def _point_along(leg, t):
    """Point at arc length t along a polyline and the index after it."""
    acc = 0.0
    for i in range(len(leg) - 1):
        L = leg[i].dist(leg[i + 1])
        if acc + L >= t:
            f = (t - acc) / L if L > 1e-12 else 0.0
            return leg[i].lerp(leg[i + 1], f), i + 1
        acc += L
    return leg[-1], len(leg) - 1


def _poly_len(pts):
    return sum(a.dist(b) for a, b in zip(pts, pts[1:]))


@dataclass
class Fillet:
    pt: Vec2                     # the corner that was rounded
    arc: list                    # cut on leg 1 → … → cut on leg 2


SHARP_TURN = math.radians(25.0)   # a face vertex turning more than this ends a leg
LEG_SHARE = 0.45                  # share of a leg ending at another feature
VERTEX_SHARE = 0.9                # share of a leg ending at a sharp face vertex
                                  # (a short straight always remains before it)
PAIR_CONE = math.radians(20.0)    # inner corner within this of the outer bisector
ARC_CHORD = 2.0                   # max chord of a sampled junction arc (in)


@dataclass
class _Leg:
    """A wall face leaving a junction corner, continued through plain face
    nodes (where the resolved face is only split into separate chains),
    up to the next real feature. pts start at the corner; usable = how
    much of it a fillet may consume."""
    pts: list
    refs: list                   # [(resolved bead, chain index, end)] in order
    usable: float


def _walk_leg(res, start, corner_nodes, ends_at, deg):
    arr = res.arrangement
    rb, ci, e = start
    pts, refs, seen = [], [], set()
    share = LEG_SHARE
    while True:
        ch = rb.chains[ci]
        if len(ch) < 2 or (id(rb), ci) in seen:
            break
        seen.add((id(rb), ci))
        refs.append((rb, ci, e))
        seg = _leg(ch, e)
        pts = seg if not pts else pts + seg[1:]
        # a sharp vertex of the face (e.g. a rectangle corner) ends the
        # leg: a fillet may run up to it, never round it
        acc = 0.0
        for k in range(1, len(pts) - 1):
            acc += pts[k - 1].dist(pts[k])
            d0, d1 = pts[k] - pts[k - 1], pts[k + 1] - pts[k]
            if d0.length() < 1e-12 or d1.length() < 1e-12:
                continue
            c = (d0.x * d1.x + d0.y * d1.y) / (d0.length() * d1.length())
            if math.acos(max(-1.0, min(1.0, c))) > SHARP_TURN:
                return _Leg(pts[:k + 1], refs, VERTEX_SHARE * acc)
        m = arr.find_node(seg[-1])
        nxt = [x for x in ends_at.get(m, []) if x[0] is not rb or x[1] != ci]
        if m is None or m in corner_nodes or deg.get(m) != 2 or len(nxt) != 1:
            break                  # another corner / a T, X, end … : shared
        rb, ci, e = nxt[0]
        share = LEG_SHARE
    return _Leg(pts, refs, share * _poly_len(pts)) if len(pts) >= 2 else None


def _truncate(pts, t):
    if t >= _poly_len(pts) - 1e-12:
        return list(pts)
    p, i = _point_along(pts, t)
    return list(pts[:i]) + [p]


def _offset_leg(pts, r, side):
    out = []
    for a, b in zip(pts, pts[1:]):
        d = b - a
        L = d.length()
        if L < 1e-12:
            continue
        nx, ny = -d.y / L * side * r, d.x / L * side * r
        out += [Vec2(a.x + nx, a.y + ny), Vec2(b.x + nx, b.y + ny)]
    return out


def _project(pts, p):
    """(distance, arc position, foot) of p on the polyline."""
    acc, best = 0.0, (float('inf'), 0.0, None)
    for a, b in zip(pts, pts[1:]):
        d, t = _pt_seg(p, a, b)
        L = a.dist(b)
        tc = max(0.0, min(1.0, t))
        if d < best[0]:
            best = (d, acc + tc * L, a.lerp(b, tc))
        acc += L
    return best


@dataclass
class _Tangent:
    centre: Vec2
    r: float
    foot1: Vec2
    t1: float
    foot2: Vec2
    t2: float


def tangent_fillet(leg1, leg2, r) -> Optional[_Tangent]:
    """The circle of radius r tangent to both legs (polylines starting at
    the corner) inside the corner — the CAD fillet, exact on straight and
    curved legs: both legs offset by r towards the inside of the corner,
    the centre where the offsets cross, the tangent points the feet of the
    centre on each leg. None if the legs are too short for r."""
    d1, d2 = _leg_dir(leg1), _leg_dir(leg2)
    if d1 is None or d2 is None or r <= 0:
        return None
    cr = d1.x * d2.y - d1.y * d2.x
    if abs(cr) < 1e-9:
        return None                       # straight on / folded back
    side = 1.0 if cr > 0 else -1.0
    o1, o2 = _offset_leg(leg1, r, side), _offset_leg(leg2, r, -side)
    hits = []
    for i in range(len(o1) - 1):
        a, b = o1[i], o1[i + 1]
        for j in range(len(o2) - 1):
            c, d = o2[j], o2[j + 1]
            rx, ry, qx, qy = b.x - a.x, b.y - a.y, d.x - c.x, d.y - c.y
            den = rx * qy - ry * qx
            if abs(den) < 1e-15:
                continue
            wx, wy = c.x - a.x, c.y - a.y
            t = (wx * qy - wy * qx) / den
            u = (wx * ry - wy * rx) / den
            if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                hits.append((i + t, Vec2(a.x + t * rx, a.y + t * ry)))
    tol = 1e-6 * max(1.0, r)
    for _, c in sorted(hits, key=lambda h: h[0]):
        # refine: the offset joins at polygon vertices of a sampled curve
        # are chords, so the crossing is only approximately r from both
        # faces — a few Newton steps put the centre exactly r from each
        for _ in range(8):
            dist1, s1, f1 = _project(leg1, c)
            dist2, s2, f2 = _project(leg2, c)
            e1, e2 = r - dist1, r - dist2
            if abs(e1) < tol * 1e-3 and abs(e2) < tol * 1e-3:
                break
            if dist1 < 1e-12 or dist2 < 1e-12:
                break
            n1, n2 = (c - f1) * (1.0 / dist1), (c - f2) * (1.0 / dist2)
            det = n1.x * n2.y - n1.y * n2.x
            if abs(det) < 1e-12:
                break
            c = Vec2(c.x + (e1 * n2.y - e2 * n1.y) / det, c.y + (n1.x * e2 - n2.x * e1) / det)
        dist1, s1, f1 = _project(leg1, c)
        dist2, s2, f2 = _project(leg2, c)
        if abs(dist1 - r) > tol or abs(dist2 - r) > tol or s1 <= 1e-9 or s2 <= 1e-9:
            continue
        if s1 >= _poly_len(leg1) - 1e-9 or s2 >= _poly_len(leg2) - 1e-9:
            continue                      # tangent beyond the usable leg
        return _Tangent(c, r, f1, s1, f2, s2)
    return None


def _arc(tg: _Tangent, corner: Vec2) -> list:
    c, r = tg.centre, tg.r
    a1 = math.atan2(tg.foot1.y - c.y, tg.foot1.x - c.x)
    a2 = math.atan2(tg.foot2.y - c.y, tg.foot2.x - c.x)
    sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi      # the short way
    # 36 samples per half turn, and chords ≤ ARC_CHORD on large radii
    n = max(3, int(abs(sweep) * 36.0 / math.pi) + 2, int(abs(sweep) * r / ARC_CHORD) + 2)
    arc = [Vec2(c.x + r * math.cos(a1 + sweep * k / (n - 1)),
                c.y + r * math.sin(a1 + sweep * k / (n - 1))) for k in range(n)]
    arc[0], arc[-1] = tg.foot1, tg.foot2
    return arc


def _max_feasible(ok, r_req, iters=40):
    """Largest r ≤ r_req with ok(r) (bisection; 0 if none)."""
    if ok(r_req):
        return r_req
    lo, hi = 0.0, r_req
    for _ in range(iters):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def round_junctions(res: NetworkResult, corners: list, radius_of,
                    wall_width: float = 0.0) -> list:
    """
    Apply junction rounding in place on the resolved chains. radius_of(key)
    → requested radius (0 = mitre / sharp). Returns the fillets made (for
    the material rings) and appends one FACE bead per arc to res.beads.

    Rounding is decided per WALL JUNCTION, not per face:
      * The faces leaving a corner are followed through plain face nodes
        (chain splits) up to the next real feature — another corner, a
        T / X node, a sharp vertex of the face — so a face is never
        limited by an arbitrary split of its chain.
      * A wall TURN has a convex face corner (material inside it) and,
        on its bisector one wall thickness in, the concave corner of the
        other face of the same two walls. They are one assembly: Junction
        R is the radius of the OUTER (convex) face; the inner face is
        concentric (radius = R − wall thickness; sharp when R ≤ that), so
        the wall keeps its thickness round the turn. The inner corner's
        own setting is not used.
      * Every other corner (T, X, Y sides) is rounded with R itself.
      * The fillet is the exact tangent circle on the actual (straight or
        curved) faces; the largest radius ≤ R that fits the WHOLE assembly
        (both faces, within their usable legs) is used by both faces.
    Each corner gets .requested / .actual (radius used) / .limited /
    .partner (the other corner of its assembly).
    """
    arr = res.arrangement
    deg: dict = {}
    for (u, v), bi in res.emitter.items():
        for n in (u, v):
            deg[n] = deg.get(n, 0) + 1
    ends_at: dict = {}
    for rb in res.beads:
        if rb.bead.cls not in (FACE, VIRTUAL):
            continue
        for ci, ch in enumerate(rb.chains):
            if len(ch) < 2 or (rb.bead.closed and rb.whole):
                continue
            for end in (0, -1):
                n = arr.find_node(ch[end])
                if n is not None:
                    ends_at.setdefault(n, []).append((rb, ci, end))
    corner_nodes = {c.node for c in corners}

    info = []
    for c in corners:
        c.requested, c.actual, c.limited, c.partner = radius_of(c.key), 0.0, False, None
        legs = [_walk_leg(res, e, corner_nodes, ends_at, deg) for e in c.ends]
        if any(lg is None for lg in legs):
            info.append(None)
            continue
        legs = [(lg, _truncate(lg.pts, lg.usable)) for lg in legs]
        d1, d2 = _leg_dir(legs[0][1]), _leg_dir(legs[1][1])
        if d1 is None or d2 is None:
            info.append(None)
            continue
        th = math.acos(max(-1.0, min(1.0, d1.x * d2.x + d1.y * d2.y)))
        u = d1 + d2
        u = u.normalized() if u.length() > 1e-12 else Vec2(0.0, 0.0)
        probe = c.pt + u * 1e-3
        convex = bool(res.material.get(arr.face_at(probe), False))
        elems = tuple(sorted(r.bead.element for r, _, _ in c.ends))
        info.append({'legs': legs, 'u': u, 'half': th / 2.0, 'convex': convex,
                     'elems': elems})

    # wall turns: a convex corner + the concave corner on its bisector
    partner = {}
    for i, a in enumerate(corners):
        ia = info[i]
        if ia is None or not ia['convex'] or wall_width <= 0:
            continue
        best = None
        for j, b in enumerate(corners):
            ib = info[j]
            if j == i or ib is None or ib['convex'] or j in partner or \
                    ib['elems'] != ia['elems']:
                continue
            v = b.pt - a.pt
            dist = v.length()
            if dist < 1e-9 or dist * math.sin(ia['half']) > 1.5 * wall_width:
                continue
            cosv = (v.x * ia['u'].x + v.y * ia['u'].y) / dist
            if cosv < math.cos(PAIR_CONE) or \
                    ia['u'].x * ib['u'].x + ia['u'].y * ib['u'].y < math.cos(2 * PAIR_CONE):
                continue
            if best is None or dist < best[0]:
                best = (dist, j)
        if best is not None:
            partner[i], partner[best[1]] = best[1], i

    plans = {}                                   # corner index → _Tangent
    for i, c in enumerate(corners):
        ia = info[i]
        if ia is None or i in plans or c.requested <= 0:
            continue
        (_, l1), (_, l2) = ia['legs']
        j = partner.get(i)
        if j is not None and ia['convex']:
            ib = info[j]
            (f1, m1), (f2, m2) = ib['legs']
            inner_pt = corners[j].pt

            def inner_r(tg):
                # concentric: the other face's radius is its distance from
                # the outer arc's centre (0 = sharp when the centre lies
                # inside the wall, i.e. R ≤ the wall thickness)
                k = tg.centre - inner_pt
                if k.x * ib['u'].x + k.y * ib['u'].y <= 0:
                    return 0.0
                return min(_project(f1.pts, tg.centre)[0], _project(f2.pts, tg.centre)[0])

            def ok(r):
                tg = tangent_fillet(l1, l2, r)
                if tg is None:
                    return False
                ri = inner_r(tg)
                return ri <= 1e-6 or tangent_fillet(m1, m2, ri) is not None
            R = _max_feasible(ok, c.requested)
            tg = tangent_fillet(l1, l2, R) if R > 1e-6 else None
            ri = inner_r(tg) if tg else 0.0
            tgi = tangent_fillet(m1, m2, ri) if ri > 1e-6 else None
            c.actual, c.limited, c.partner = (R if tg else 0.0), R < c.requested - 1e-6, corners[j].key
            cj = corners[j]
            cj.requested, cj.actual, cj.partner = None, (ri if tgi else 0.0), c.key
            cj.limited = c.limited
            plans[i], plans[j] = tg, tgi
        elif j is not None:
            continue                          # the inner face: done with its outer corner
        else:
            R = _max_feasible(lambda r: tangent_fillet(l1, l2, r) is not None, c.requested)
            tg = tangent_fillet(l1, l2, R) if R > 1e-6 else None
            c.actual, c.limited = (R if tg else 0.0), R < c.requested - 1e-6
            plans[i] = tg

    fillets = []
    for i, c in enumerate(corners):
        tg = plans.get(i)
        if tg is None:
            continue
        arc = _arc(tg, c.pt)
        for (lg, _), t, tip in ((info[i]['legs'][0], tg.t1, tg.foot1),
                                (info[i]['legs'][1], tg.t2, tg.foot2)):
            _consume(lg, t, tip)
        bead = Bead(f'junction:{c.key}', arc, False, FACE,
                    c.ends[0][0].bead.element, face_id=f'junction:{c.key}')
        res.beads.append(ResolvedBead(bead, True, [arc]))
        fillets.append(Fillet(c.pt, arc))
    for rb in res.beads:
        if any(len(ch) < 2 for ch in rb.chains):
            rb.chains = [ch for ch in rb.chains if len(ch) >= 2]
            rb.whole = False
    return fillets


def _consume(lg: _Leg, t: float, tip: Vec2):
    """Remove the first t of arc length of a leg from its chains; the face
    then starts EXACTLY at the arc's end (no float gap)."""
    for rb, ci, e in lg.refs:
        ch = rb.chains[ci]
        if len(ch) < 2:
            continue
        leg = _leg(ch, e)
        L = _poly_len(leg)
        rb.whole = False
        if t >= L - 1e-9:
            rb.chains[ci] = []                   # wholly inside the fillet
            t -= L
            continue
        p, k = _point_along(leg, t)
        rest = leg[k:]
        while rest and rest[0].dist(tip) < 1e-9:   # tangent point ON a vertex
            rest = rest[1:]
        new = [tip] + rest
        rb.chains[ci] = new if e == 0 else list(reversed(new))
        return


def trim_leg(leg, t):
    """The leg without its first t of arc length (starts at the cut)."""
    p, i = _point_along(leg, t)
    return [p] + leg[i:]


def _arc_pos(leg, p):
    """Arc length along leg of a point lying on it."""
    acc = 0.0
    best = (float('inf'), 0.0)
    for a, b in zip(leg, leg[1:]):
        d, t = _pt_seg(p, a, b)
        L = a.dist(b)
        if d < best[0]:
            best = (d, acc + max(0.0, min(1.0, t)) * L)
        acc += L
    return best[1]


def fillet_ring(ring, fillets):
    """Apply junction fillets to a closed material ring (same geometry as
    the printed faces, so infill lands exactly on the rounded wall)."""
    for f in fillets:
        n = len(ring)
        idx = next((i for i, q in enumerate(ring)
                    if q.x == f.pt.x and q.y == f.pt.y), None)
        if idx is None:
            continue
        fwd = [ring[(idx + k) % n] for k in range(n + 1)]
        back = [ring[(idx - k) % n] for k in range(n + 1)]
        # Orient the arc to run from the backward leg to the forward leg.
        a = f.arc[0]
        # the arc may end beyond the first ring vertex: compare positions
        arc = list(reversed(f.arc)) if _arc_pos(fwd, a) < _arc_pos(back, a) else list(f.arc)
        _, i_f = _point_along(fwd, _arc_pos(fwd, arc[-1]))
        _, i_b = _point_along(back, _arc_pos(back, arc[0]))
        j = (idx + i_f) % n
        m = (idx - i_b) % n
        count = (m - j) % n + 1
        ring = arc + [ring[(j + k) % n] for k in range(count)]
    return ring
