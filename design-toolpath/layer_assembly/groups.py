"""
TRANSFORM GROUPS — explicit geometry → transform-centre membership
('multiple' centre mode).

A group is a TransformCenter: stable id, pivot (x, y) in design
coordinates, and `sources` — the SOURCE ids whose geometry it moves. The
Designer tags every printable vertex with the source (wall) it belongs to
(LayerSource polyline 'src', design_proto/source_attribution.py); here the
tags are opaque ids. Source ids are persistent across derived Layer
Designs and are not changed by openings, so membership survives both.

RULES (V1):
  * a source is moved by the group that LISTS it; unlisted sources follow
    the layer's whole-footprint placement. No nearest-centre / Voronoi
    rule; moving a pivot never changes membership.
  * the move is SEMANTIC (decision 2026-10-06): the Assembly hands each
    group's placement relative to the layer's own to the Designer as a
    transform of the group's SOURCES (model.relative_transforms →
    LayerSource.geometry(design, transforms)); the Designer regenerates
    walls connecting differently moved hosts and resolves the rest. The
    Assembly never moves a grouped layer's resolved beads itself (the
    previous per-vertex placement stretched / tore connecting walls).
  * a source listed by two groups belongs to the FIRST (reported).
  * a group's reference size (Scale in) is half the longer side of the
    bbox of its member geometry on the lineage ROOT design (stable across
    openings; else the first design using it); `auto` = that bbox centre
    (↺ reset target).
  * vertex tags (vertex_groups) are used only to SEE groups: footprints,
    colouring, the group of a header / finding (its local frame).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Footprint
from .components import detect, assign, root_of


@dataclass
class GroupModel:
    group_of: dict                 # source id → centre id
    footprints: dict               # centre id → Footprint(pivot, member half-size) (groups WITH geometry)
    auto: dict                     # centre id → member bbox centre
    vertex_groups: dict            # design id → [[centre id | None per vertex] per polyline]
    warnings: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)  # detected masses → their sources
    sources: dict = field(default_factory=dict)      # source id → label (designs involved)

    def groups_in(self, design_id) -> set:
        return {g for pl in self.vertex_groups.get(design_id, ()) for g in pl if g}

    def to_dict(self) -> dict:
        return {'group_of': self.group_of,
                'centres': {c: {'auto': list(self.auto[c]), 'half': self.footprints[c].half}
                            for c in self.footprints},
                'warnings': self.warnings,
                'suggestions': self.suggestions,
                'sources': {s: {'label': lab, 'group': self.group_of.get(s)}
                            for s, lab in sorted(self.sources.items())}}


def _tags(pl):
    src = pl.get('src')
    return list(src) if src and len(src) == len(pl['pts']) else [None] * len(pl['pts'])


def _bbox(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def build_groups(geometry, parent_of, used, centers, labels=None) -> GroupModel:
    """geometry: {design id: (polylines, bead)} incl. the ROOTS of `used`;
    parent_of: {design id: parent | None}; centers: [TransformCenter];
    labels: {source id: label}."""
    group_of, warnings = {}, []
    for c in centers or []:
        for s in c.sources:
            if s in group_of and group_of[s] != c.id:
                warnings.append(f'source {s!r} is listed by {group_of[s]} and {c.id}: {group_of[s]} keeps it')
                continue
            group_of[s] = c.id
    vertex_groups = {}
    for d in used:
        if d not in geometry:
            continue
        vertex_groups[d] = [[group_of.get(t) for t in _tags(pl)] for pl in geometry[d][0]]
    roots = {d: root_of(d, parent_of) for d in used}
    footprints, auto = {}, {}
    for c in centers or []:
        pts = []
        for d in [r for r in dict.fromkeys(roots[x] for x in used)] + list(used):
            if d not in geometry:
                continue
            pts = [p for pl in geometry[d][0] for p, t in zip(pl['pts'], _tags(pl))
                   if t is not None and group_of.get(t) == c.id]
            if pts:
                break
        b = _bbox(pts)
        if b is None:
            continue                       # no geometry (yet): moves nothing
        auto[c.id] = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        footprints[c.id] = Footprint(c.x, c.y, max(b[2] - b[0], b[3] - b[1]) / 2)
    suggestions = []
    for r in sorted(set(roots.values())):
        if r not in geometry:
            continue
        polys, bead = geometry[r]
        masses = detect(polys, bead, r)
        keys = assign(polys, masses)
        for m in masses:
            srcs = sorted({t for pl, k in zip(polys, keys) if k == m.key for t in _tags(pl) if t})
            suggestions.append({'key': m.key, 'bbox': list(m.bbox), 'auto': list(m.centre),
                                'sources': srcs})
    srcs = {t for d in set(used) | set(roots.values()) if d in geometry
            for pl in geometry[d][0] for t in _tags(pl) if t}
    lab = {s: (labels or {}).get(s, s) for s in srcs}
    return GroupModel(group_of, footprints, auto, vertex_groups, warnings, suggestions, lab)
