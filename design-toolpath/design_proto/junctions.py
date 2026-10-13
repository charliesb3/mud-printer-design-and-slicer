"""Shared geometric region framework (J2 / J3 / J4a).

Named architectural regions that the Wall System generators consume for
construction decisions. Five region types, from the J4a specification:

  1. CornerRegion       — a wall direction change (one source, two arms).
  2. JunctionRegion     — multiple walls connecting (existing J2/J3 record;
                          `corner`/`tee`/`wye`/`cross`/`hub` by arm count).
  3. EndCapRegion       — wall termination: free end, opening side, or
                          cut jamb. Carries end-cap style (`flat` /
                          `semicircle` / `rounded_corner`).
  4. ConstrainedRegion  — a wall section too short / narrow for its normal
                          construction (e.g. a pier between two openings
                          in an array). Detected and REPORTED; the full
                          compact-motif library is J4b scope.
  5. TransitionRegion   — explicit relationship between a normal
                          construction and an adjacent constrained
                          section. The motif for the transition itself is
                          J4b/J4c scope; the record pins the identity.

All records share a stable source-based KEY so a region can be identified
across layers, through semantic transforms and across cuts. All records
belong to one layer's resolved `NetworkResult`; the registry
`RegionSet.of(res, layer)` returns every type in one call.

Design intent: shared VOCABULARY. The Designer and Assembly produce
architectural reference geometry (unchanged); Wall System generators
consume the records to make junction / corner / endcap / fit decisions.
The existing `network.round_junctions` + `network.junction_corners`
continue to produce the DESIGNER'S actual junction geometry (fillets,
mitres, hub turns); the records REFERENCE that geometry.

J2 wired JunctionRegion extraction only; J3 wired consumption into
Hollow / Parallel generators. J4a adds the other four region types,
threads them through the pipeline, and begins their consumption with
diagnostic reporting first (geometry-affecting consumption is J4b).

Designer and Assembly architectural references are not changed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from model import Vec2
except ImportError:
    Vec2 = None                   # (circular at module load time: resolved lazily)


@dataclass
class Arm:
    """One wall leaving a junction."""
    source_id: str                # architectural source path id (stable)
    direction: tuple              # (dx, dy) outward unit vector from the junction centre
    half_thickness: float         # wall half-thickness along this arm
    is_single_bead: bool          # thickness == 0 → prints a single bead
    face0: tuple | None = None    # (x, y) connect point on face 0 at the arm root
    face1: tuple | None = None    # (x, y) connect point on face 1 at the arm root


@dataclass
class JunctionRegion:
    """A corner / T / Y / X / hub, named once for every construction."""
    key: str                      # stable id: '|'.join(sorted source ids) + '#k'
    kind: str                     # 'corner' | 'tee' | 'wye' | 'cross' | 'hub'
    source_ids: tuple             # ('C', 'L')  (sorted)
    style: str                    # 'miter' | 'round'
    center: tuple                 # (x, y) of the arrangement node
    arms: list                    # list of Arm, in CCW order around the centre
    boundary_outer: list = field(default_factory=list)
    boundary_inner: list = field(default_factory=list)
    connect_points: list = field(default_factory=list)

    # J4a: region_type is a uniform way to switch across types.
    region_type: str = 'junction'

    @property
    def is_corner(self) -> bool:
        return self.kind == 'corner'

    @property
    def arm_count(self) -> int:
        return len(self.arms)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def _arm_count_kind(n: int) -> str:
    if n <= 1:
        return 'end'
    if n == 2:
        return 'corner'           # a wall turn / corner (two arms)
    if n == 3:
        return 'tee'              # three arms — right-angle tee or wye
    if n == 4:
        return 'cross'            # four arms — X
    return 'hub'                  # 5+ arms — a hub


def extract(res, source_walls: dict | None = None,
            junction_style: str = 'miter',
            junction_radius: float = 0.0) -> list:
    """Collect JunctionRegion records from a NetworkResult.

    - `res` is `network.NetworkResult` (from `network.resolve`).
    - `source_walls` maps source id → {'thickness', 'align', …} (the
      Designer's `_source_walls`); used for the `half_thickness` and the
      `is_single_bead` flag on each arm.
    - `junction_style` / `junction_radius` reflect the Designer document's
      global junction settings. (Per-junction overrides are not consumed
      by this first-pass helper.)

    One record per arrangement NODE with ≥ 2 wall arms. The record is
    advisory: it names the junction and tells a generator the arm
    directions and single-bead flags. The actual printable boundary and
    the Designer's resolved fillets live in `res.beads` and are not
    reconstructed here — a `JunctionRegion.boundary_outer` / `_inner`
    stay empty unless a caller fills them in after routing.
    """
    arr = res.arrangement
    # degree of each node across face / virtual beads
    face_deg: dict = {}
    for (u, v), bi in res.emitter.items():
        for n in (u, v):
            face_deg[n] = face_deg.get(n, 0) + 1
    # per node, list the (source_id, outward_direction, half_thickness)
    per_node: dict = {}
    for rb in res.beads:
        src = rb.bead.element       # the source id
        for ci, ch in enumerate(rb.chains):
            if len(ch) < 2:
                continue
            for end in (0, -1):
                n = arr.find_node(ch[end])
                if n is None:
                    continue
                # outward direction: from the node toward the next chain point
                other = ch[1] if end == 0 else ch[-2]
                dx = other.x - ch[end].x
                dy = other.y - ch[end].y
                L = (dx * dx + dy * dy) ** 0.5
                if L < 1e-9:
                    continue
                per_node.setdefault(n, []).append((src, (dx / L, dy / L)))
    # keep only nodes with ≥ 2 distinct source ids OR ≥ 3 distinct arms
    # (co-source arms of a smooth wall should not count as a junction;
    # the arrangement finds them because the wall was resolved into
    # multiple chains)
    regions = []
    for n, arms_raw in per_node.items():
        srcs = sorted({a[0] for a in arms_raw})
        if len(srcs) < 2 and len(arms_raw) < 3:
            continue
        pt = arr.nodes[n]
        # deduplicate arms by (source, rounded direction)
        seen: dict = {}
        for src, d in arms_raw:
            key = (src, round(d[0], 3), round(d[1], 3))
            if key not in seen:
                seen[key] = (src, d)
        armlist = sorted(seen.values(),
                         key=lambda x: math.atan2(x[1][1], x[1][0]))
        arms = []
        for src, d in armlist:
            spec = (source_walls or {}).get(src) or {}
            th = float(spec.get('thickness') or 0.0)
            arms.append(Arm(source_id=src, direction=d,
                            half_thickness=0.5 * th,
                            is_single_bead=(th == 0.0)))
        regions.append(JunctionRegion(
            key='|'.join(srcs) + f'#{n}',
            kind=_arm_count_kind(len(arms)),
            source_ids=tuple(srcs),
            style='round' if junction_style == 'round' and junction_radius > 0 else 'miter',
            center=(pt.x, pt.y),
            arms=arms,
        ))
    # ordinal: stable position-sorted key within the same source set
    by_srcs: dict = {}
    for r in regions:
        by_srcs.setdefault(r.source_ids, []).append(r)
    for srcs, group in by_srcs.items():
        group.sort(key=lambda r: (round(r.center[0], 3), round(r.center[1], 3)))
        for k, r in enumerate(group):
            r.key = '|'.join(srcs) + f'#{k}'
    return regions


def summary(regions: list) -> dict:
    """Short diagnostic dict for logging / tests."""
    out: dict = {'count': len(regions), 'by_kind': {}}
    for r in regions:
        out['by_kind'][r.kind] = out['by_kind'].get(r.kind, 0) + 1
    out['single_bead_arms'] = sum(1 for r in regions for a in r.arms
                                   if a.is_single_bead)
    return out


# ===========================================================================
# J4a — additional region types
# ===========================================================================

@dataclass
class CornerRegion:
    """A wall DIRECTION CHANGE within ONE source path.

    A rectangle, polygon or polyline source produces one CornerRegion per
    interior vertex whose turn angle exceeds `CORNER_MIN_TURN` radians
    (default 30°). Smooth curves (closed circles) produce none.

    JunctionRegion covers corners where TWO wall sources meet; CornerRegion
    covers corners WITHIN one source. They are complementary.
    """
    key: str                      # 'corner:<source>#<idx>'
    source_id: str                # the architectural source path id
    source_vertex_index: int      # index of the corner vertex in the processed source
    turn_angle: float             # radians; signed (CCW positive for closed sources)
    center: tuple                 # (x, y) of the corner vertex (on the architectural line)
    in_direction: tuple           # unit tangent entering the corner
    out_direction: tuple          # unit tangent leaving the corner
    style: str                    # 'miter' | 'round' | 'chamfer' (document-level or local override)
    radius: float = 0.0           # the applied fillet radius if `style == 'round'`
    half_thickness: float = 0.0   # the source's wall half-thickness
    region_type: str = 'corner'


@dataclass
class EndCapRegion:
    """A wall TERMINATION — the free end of an open wall, or an opening
    jamb (both sides of the opening are two EndCapRegions). The cap_style
    reflects what the current pipeline is drawing (`flat` / `semicircle`
    / `rounded_corner`). The record is diagnostic (J4a) — J4b/J4c may
    make it drive the cap decision itself.
    """
    key: str                      # 'endcap:<source>#<idx>'
    source_id: str
    source_vertex_index: int      # 0 = start, -1 = end; or the opening-jamb index
    center: tuple                 # (x, y) at the architectural centre line
    outward: tuple                # unit vector pointing OUT of the material
    cap_style: str                # 'flat' | 'semicircle' | 'rounded_corner' | 'unknown'
    half_thickness: float         # half the wall's architectural thickness
    region_type: str = 'end_cap'


@dataclass
class ConstrainedRegion:
    """A wall section TOO SMALL for its normal construction. Detected and
    REPORTED; J4a does NOT force an invalid construction into it. The
    full compact-loop motif library is J4b.

    `reason` names the specific constraint ('short_section',
    'narrow_pier', 'short_jamb', 'short_between_openings', 'unresolved').
    `available` and `required` are the measured lengths (inches); a
    region with `available < required` is flagged. The record also
    carries the two neighbouring regions (`neighbour_a` / `neighbour_b`,
    the end-cap or junction keys that bound this section), so a
    TransitionRegion can relate it to its context.
    """
    key: str                      # 'constrained:<source>#<idx>'
    source_id: str
    reason: str
    available_length: float
    required_length: float
    center: tuple                 # mid-point of the section (x, y)
    neighbour_a: str | None = None
    neighbour_b: str | None = None
    region_type: str = 'constrained'


@dataclass
class TransitionRegion:
    """Explicit relationship between a NORMAL and a CONSTRAINED region.
    Carries both neighbours' keys and the architectural length over which
    the pattern transitions. The motif choice itself (how the chain /
    interleaved pattern morphs) is J4b/J4c scope; this record just
    pins the identity so a J4b change has a stable anchor."""
    key: str                      # 'transition:<normal>→<constrained>'
    source_id: str
    from_region: str              # the normal region's key
    to_region: str                # the constrained region's key
    center: tuple
    length: float
    region_type: str = 'transition'


# -------- extraction ------------------------------------------------------
# `extract_corners`, `extract_end_caps`, `extract_constrained`,
# `extract_transitions` each take a `PrintLayer`-like object providing
# `source_paths`, `_source_walls`, `_processed_source_pts()`,
# `openings`, `cut_transitions`, `material.bead_width`, `junction_style`,
# `junction_radius`. They walk the Designer's architectural geometry
# (unchanged) and return records. No `NetworkResult` is needed — these
# region types are architecturally observable even before Wall System
# generation runs.

CORNER_MIN_TURN = math.radians(30.0)   # as wall_systems.CORNER_TURN


def _unit(v: tuple) -> tuple:
    """Return a unit vector or (1, 0) for degenerate input."""
    L = (v[0] * v[0] + v[1] * v[1]) ** 0.5
    if L < 1e-12:
        return (1.0, 0.0)
    return (v[0] / L, v[1] / L)


def extract_corners(layer, min_turn: float = CORNER_MIN_TURN) -> list:
    """Collect CornerRegion records for every substantive direction
    change inside each source path.

    `layer` must have `source_paths`, `_source_walls`, `junction_style`,
    `junction_radius`, `_corner_r(src)` (optional, falls back to global).
    """
    try:
        from model import _processed_source_pts
    except ImportError:                                           # test contexts
        _processed_source_pts = getattr(layer, '_processed_source_pts', None)
        if _processed_source_pts is None:
            return []
    out: list = []
    walls = (getattr(layer, '_source_walls', None) or {})
    style = getattr(layer, 'junction_style', 'miter')
    radius = float(getattr(layer, 'junction_radius', 0.0) or 0.0)
    for src in getattr(layer, 'source_paths', ()) or ():
        r = 0.0
        try:
            r = float(getattr(layer, '_corner_r', lambda s: 0.0)(src))
        except Exception:
            r = 0.0
        pts = _processed_source_pts(src, r)
        if len(pts) < 3:
            continue
        n = len(pts)
        # iterate over interior vertices only (open paths: 1 .. n-2;
        # closed paths: 0 .. n-1 wrapping)
        rng = range(n) if src.closed else range(1, n - 1)
        spec = walls.get(src.id) if walls else None
        hthick = 0.5 * float((spec or {}).get('thickness') or 0.0)
        for i in rng:
            prev_i = (i - 1) % n if src.closed else i - 1
            next_i = (i + 1) % n if src.closed else i + 1
            a, b, c = pts[prev_i], pts[i], pts[next_i]
            ux, uy = _unit((b.x - a.x, b.y - a.y))
            vx, vy = _unit((c.x - b.x, c.y - b.y))
            cross = ux * vy - uy * vx
            dot = ux * vx + uy * vy
            turn = math.atan2(cross, dot)
            if abs(turn) < min_turn:
                continue
            key = f'corner:{src.id}#{i}'
            out.append(CornerRegion(
                key=key, source_id=src.id, source_vertex_index=i,
                turn_angle=turn, center=(b.x, b.y),
                in_direction=(ux, uy), out_direction=(vx, vy),
                style=('round' if style == 'round' and radius > 0 else 'miter'),
                radius=radius if style == 'round' else 0.0,
                half_thickness=hthick))
    return out


def extract_end_caps(layer) -> list:
    """Collect EndCapRegion records for free ends of open walls and for
    opening jambs. One EndCapRegion per terminated end.

    For free ends: both endpoints of an open source path with a wall.
    For openings: both jamb edges of each opening declared on the layer.
    """
    try:
        from model import _processed_source_pts
    except ImportError:
        _processed_source_pts = getattr(layer, '_processed_source_pts', None)
        if _processed_source_pts is None:
            return []
    out: list = []
    walls = (getattr(layer, '_source_walls', None) or {})
    by_id = {p.id: p for p in (getattr(layer, 'source_paths', ()) or ())}
    for sid, p in by_id.items():
        spec = walls.get(sid) if walls else None
        if spec is None:
            continue
        hthick = 0.5 * float(spec.get('thickness') or 0.0)
        if hthick <= 1e-9:
            continue                                              # single-bead → no cap
        pts = _processed_source_pts(p, 0.0)
        if not pts or p.closed:
            continue
        for ei, (a, b) in ((0, (pts[0], pts[1])),
                            (-1, (pts[-2], pts[-1]))):
            inward = _unit((b.x - a.x, b.y - a.y))                # inward along the wall
            if ei == -1:
                centre = (b.x, b.y); outward = inward
            else:
                centre = (a.x, a.y); outward = (-inward[0], -inward[1])
            out.append(EndCapRegion(
                key=f'endcap:{sid}#{ei}', source_id=sid,
                source_vertex_index=ei, center=centre, outward=outward,
                cap_style='unknown',                              # production decides at render
                half_thickness=hthick))
    # opening jambs: each opening cuts its source into two ends
    for o in (getattr(layer, 'openings', ()) or ()):
        sid = getattr(o, 'source_path_id', None) or getattr(o, 'id', None)
        if sid not in by_id:
            continue
        p = by_id[sid]
        spec = walls.get(sid) if walls else None
        if spec is None:
            continue
        hthick = 0.5 * float(spec.get('thickness') or 0.0)
        if hthick <= 1e-9:
            continue
        try:
            pts = _processed_source_pts(p, 0.0)
            cs = float(o.center_s); w = float(o.width)
        except Exception:
            continue
        if len(pts) < 2:
            continue
        # locate (approximately) the two jamb centres on the source
        # polyline. The exact resolution is in model.py's opening pass;
        # here we only need the mid-point of each jamb for region
        # identity + diagnostic.
        cum = [0.0]
        for i in range(len(pts) - 1):
            cum.append(cum[-1] + pts[i].dist(pts[i + 1]))
        total = cum[-1]
        if total < 1e-9:
            continue
        s_lo = cs - 0.5 * w
        s_hi = cs + 0.5 * w
        if p.closed:
            s_lo %= total; s_hi %= total
        else:
            s_lo = max(0.0, s_lo); s_hi = min(total, s_hi)
        def at(s):
            if s <= 0.0: return pts[0], _unit((pts[1].x - pts[0].x, pts[1].y - pts[0].y))
            if s >= total: return pts[-1], _unit((pts[-1].x - pts[-2].x, pts[-1].y - pts[-2].y))
            for k in range(len(pts) - 1):
                if cum[k + 1] >= s:
                    t = (s - cum[k]) / max(1e-9, cum[k + 1] - cum[k])
                    a, b = pts[k], pts[k + 1]
                    return (type(a)(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t),
                            _unit((b.x - a.x, b.y - a.y)))
            return pts[-1], _unit((pts[-1].x - pts[-2].x, pts[-1].y - pts[-2].y))
        pt_lo, u_lo = at(s_lo); pt_hi, u_hi = at(s_hi)
        oid = getattr(o, 'id', 'o')
        out.append(EndCapRegion(
            key=f'endcap:{sid}#opening{oid}-lo', source_id=sid,
            source_vertex_index=0,                                # jamb side marker
            center=(pt_lo.x, pt_lo.y),
            outward=(u_lo[0], u_lo[1]),                           # points INTO the opening
            cap_style='unknown', half_thickness=hthick))
        out.append(EndCapRegion(
            key=f'endcap:{sid}#opening{oid}-hi', source_id=sid,
            source_vertex_index=1,
            center=(pt_hi.x, pt_hi.y),
            outward=(-u_hi[0], -u_hi[1]),                         # points INTO the opening
            cap_style='unknown', half_thickness=hthick))
    return out


def extract_constrained_sections(layer, bead_scale: float = 2.0) -> list:
    """Detect wall sections that are TOO SHORT for their normal construction.

    Specifically, for each source path with a wall AND openings on it,
    measure the arc-length PIER between consecutive opening pairs along
    that source. If the pier is < max(wall_thickness, bead_scale × bead)
    the section is reported as a ConstrainedRegion with reason
    `short_between_openings`.

    Also flags walls whose TOTAL open length (end-to-end minus openings)
    is below the same threshold with reason `short_section`.

    Does NOT change geometry; the J4b compact-loop motif library decides
    what to print there.
    """
    try:
        from model import _processed_source_pts
    except ImportError:
        _processed_source_pts = getattr(layer, '_processed_source_pts', None)
        if _processed_source_pts is None:
            return []
    bead = float(getattr(getattr(layer, 'material', None), 'bead_width', 3.0) or 3.0)
    walls = (getattr(layer, '_source_walls', None) or {})
    by_id = {p.id: p for p in (getattr(layer, 'source_paths', ()) or ())}
    out: list = []
    # bucket openings by source
    openings_by_src: dict = {}
    for o in (getattr(layer, 'openings', ()) or ()):
        sid = getattr(o, 'source_path_id', None)
        if sid in by_id:
            openings_by_src.setdefault(sid, []).append(o)
    for sid, p in by_id.items():
        spec = walls.get(sid) if walls else None
        if spec is None:
            continue
        thick = float(spec.get('thickness') or 0.0)
        if thick <= 1e-9:
            continue
        min_pier = max(thick, bead_scale * bead)
        pts = _processed_source_pts(p, 0.0)
        if len(pts) < 2:
            continue
        cum = [0.0]
        for i in range(len(pts) - 1):
            cum.append(cum[-1] + pts[i].dist(pts[i + 1]))
        total = cum[-1]
        ops = sorted(openings_by_src.get(sid, []), key=lambda o: float(o.center_s))
        edges = []                                                # list of (s_lo, s_hi) openings
        for o in ops:
            try:
                cs = float(o.center_s); w = float(o.width)
            except Exception:
                continue
            edges.append((cs - 0.5 * w, cs + 0.5 * w))
        # neighbour piers
        if not edges:
            if not p.closed and total < min_pier:
                out.append(ConstrainedRegion(
                    key=f'constrained:{sid}#whole',
                    source_id=sid, reason='short_section',
                    available_length=total, required_length=min_pier,
                    center=(pts[0].x, pts[0].y)))
            continue
        # piers between consecutive edges (and from start/end for open paths)
        pier_bounds = []
        if p.closed:
            # wrap: last.hi → first.lo
            pier_bounds.append((edges[-1][1] % total, edges[0][0] % total, 'between_openings'))
            for i in range(len(edges) - 1):
                pier_bounds.append((edges[i][1], edges[i + 1][0], 'between_openings'))
        else:
            pier_bounds.append((0.0, edges[0][0], 'short_jamb'))
            for i in range(len(edges) - 1):
                pier_bounds.append((edges[i][1], edges[i + 1][0], 'between_openings'))
            pier_bounds.append((edges[-1][1], total, 'short_jamb'))
        for idx, (s0, s1, reason) in enumerate(pier_bounds):
            length = (s1 - s0) if s1 >= s0 else (total - s0 + s1)
            if length >= min_pier - 1e-6:
                continue
            s_mid = (s0 + 0.5 * length) if s1 >= s0 else ((s0 + 0.5 * length) % total)
            # locate mid-point on polyline
            for k in range(len(pts) - 1):
                if cum[k + 1] >= s_mid:
                    t = (s_mid - cum[k]) / max(1e-9, cum[k + 1] - cum[k])
                    a, b = pts[k], pts[k + 1]
                    mid = (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
                    break
            else:
                mid = (pts[-1].x, pts[-1].y)
            out.append(ConstrainedRegion(
                key=f'constrained:{sid}#pier{idx}',
                source_id=sid, reason=reason,
                available_length=length, required_length=min_pier,
                center=mid))
    return out


def extract_transitions(constrained: list) -> list:
    """For every ConstrainedRegion, emit a TransitionRegion pair with the
    normal regions on either side (currently uses the source's name as
    the normal-region placeholder — J4b will wire this to actual
    construction records). Pure diagnostic; no neighbour geometry yet.
    """
    out: list = []
    for c in constrained:
        out.append(TransitionRegion(
            key=f'transition:normal→{c.key}',
            source_id=c.source_id,
            from_region=f'normal:{c.source_id}',
            to_region=c.key,
            center=c.center,
            length=max(0.0, c.required_length - c.available_length)))
    return out


# -------- registry --------------------------------------------------------

@dataclass
class RegionSet:
    """One layer's shared region framework result (J4a).

    Produced ONCE per layer by `extract_all(res, layer)`:

      * `junctions`    = list[JunctionRegion]  (as J3)
      * `corners`      = list[CornerRegion]
      * `end_caps`     = list[EndCapRegion]
      * `constrained`  = list[ConstrainedRegion]
      * `transitions`  = list[TransitionRegion]

    `by_key` lets a generator look up a region in O(1).
    `serialise()` → dict ready for `meta['network']['regions']`.
    """
    junctions: list = field(default_factory=list)
    corners: list = field(default_factory=list)
    end_caps: list = field(default_factory=list)
    constrained: list = field(default_factory=list)
    transitions: list = field(default_factory=list)

    def by_key(self) -> dict:
        out: dict = {}
        for group in (self.junctions, self.corners, self.end_caps,
                      self.constrained, self.transitions):
            for r in group:
                out[r.key] = r
        return out

    def serialise(self) -> dict:
        """A JSON-ready dict (lists of dicts). Keeps numeric / string
        fields; drops dataclass identity."""
        def _d(r):
            # dataclasses -> dict without `boundary_outer` etc. (large)
            d = r.__dict__
            return {k: list(v) if isinstance(v, tuple) else v
                    for k, v in d.items()
                    if k not in ('boundary_outer', 'boundary_inner',
                                 'connect_points', 'arms')}
        def _arms(r):
            return [{k: list(v) if isinstance(v, tuple) else v
                     for k, v in a.__dict__.items()} for a in getattr(r, 'arms', [])]
        return {
            'junctions': [dict(_d(r), arms=_arms(r)) for r in self.junctions],
            'corners': [_d(r) for r in self.corners],
            'end_caps': [_d(r) for r in self.end_caps],
            'constrained': [_d(r) for r in self.constrained],
            'transitions': [_d(r) for r in self.transitions],
            'counts': {'junctions': len(self.junctions),
                       'corners': len(self.corners),
                       'end_caps': len(self.end_caps),
                       'constrained': len(self.constrained),
                       'transitions': len(self.transitions)},
        }


def extract_all(res, layer) -> RegionSet:
    """One-call extractor for every region type (J4a).

    * `res` is a `network.NetworkResult` for the current component (used
      by `extract()` for the junction records).
    * `layer` is the Designer `PrintLayer` (used by the architectural
      extractors for corners / end-caps / constrained sections).

    Returns a `RegionSet`.
    """
    junctions = extract(
        res, source_walls=getattr(layer, '_source_walls', None),
        junction_style=getattr(layer, 'junction_style', 'miter'),
        junction_radius=float(getattr(layer, 'junction_radius', 0.0) or 0.0))
    corners = extract_corners(layer)
    end_caps = extract_end_caps(layer)
    constrained = extract_constrained_sections(layer)
    transitions = extract_transitions(constrained)
    return RegionSet(junctions=junctions, corners=corners,
                      end_caps=end_caps, constrained=constrained,
                      transitions=transitions)
