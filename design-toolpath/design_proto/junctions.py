"""Shared junction / corner representation (J2).

A JunctionRegion names an architectural corner or junction once so Hollow
Skin, Parallel Walls and (eventually) other constructions can share the
decision of HOW TO PRINT AT THAT JUNCTION. This module is intentionally
small: it collects what the generators need to know about a junction,
without re-doing the production geometry. The existing
`network.round_junctions` + `network.junction_corners` continue to produce
the Designer's actual junction geometry (fillets, mitres, hub turns); a
`JunctionRegion` references THAT geometry and adds:

  * a stable KEY (sorted source ids + ordinal) so a junction is identified
    across layers, through semantic transforms and across cuts;
  * the KIND (`corner` / `tee` / `wye` / `cross` / `hub`) by arm count;
  * the ARMS: one record per wall leaving the junction, carrying source
    id, outward direction, half-thickness, single-bead flag;
  * the JUNCTION STYLE actually applied ('miter' or 'round');
  * the FINAL PRINTABLE boundary polyline(s) local to the junction;
  * CONNECT POINTS: ordered points on each arm's final boundary where
    incoming printable lanes should land. For Parallel: the outermost
    face + the inner lane positions as the arm meets the junction.

J2 scope note:
  the first pass (Hollow / Parallel) consumes `arms` + `connect_points`
  only. Interleaved / Linked / Chain are untouched this pass.

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
