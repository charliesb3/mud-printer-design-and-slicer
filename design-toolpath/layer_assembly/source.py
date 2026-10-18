"""
The narrow interface between the Designer and the Assembly.

A LayerSource provides, per Layer Design id: its info (name, parent — the
derivation relationship) and its RESOLVED 2D printable geometry (the
printable centrelines the router prints, already including walls, trims,
openings, lattice …), each vertex optionally tagged with an opaque SOURCE
id ('src': the Designer source path / wall it belongs to) and the sources'
labels — what explicit transform-group assignment refers to. The Assembly
needs nothing else from the Designer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class DesignInfo:
    id: str
    name: str
    parent: Optional[str] = None


@dataclass
class LayerGeometry:
    """Resolved 2D printable geometry of one Layer Design (XY, inches)."""
    design_id: str
    polylines: list                    # [{'id', 'pts': [[x, y]…], 'closed', 'src'?: [source id per vertex]}]
    bead_width: Optional[float] = None
    diagnostics: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)    # source id → label (opaque to the Assembly)


class LayerSource(Protocol):
    """geometry(design, transforms): with `transforms` {source id: (k, tx,
    ty)} the design resolved with those SEMANTIC transforms applied to its
    sources BEFORE resolution (the Designer moves walls, regenerates the
    walls connecting differently moved hosts and resolves junctions /
    openings / lattice — design_proto/semantic_transform.py). The Assembly
    only names source ids and similarities; it never moves resolved beads
    of a grouped layer itself.
    With `cuts` (ASSEMBLY PROFILES, profiles.py: a list of {'op': 'opening'
    | 'extent', 'source', 'edge', …}) the Designer first applies those
    source-level cuts to the design document (ordinary openings, shortened
    straight walls), then the transforms, then resolves as usual.
    walls(design): the design's STRAIGHT walls a profile can target
    ([WallTarget-like dicts]; optional — a source without it has none).
    With `state` (CONSTRUCTION STATE, stage Z4 — design_proto/
    construction_state.py: the webs' discrete choices carried from the layer
    below by identity, computed by the Assembly's chain over the resolved
    stack) the Designer builds the layer's webs on it; the Designer still
    receives no Z, only the state. `topology(design, transforms, cuts)`
    (optional) is the chain's per-layer input."""
    def design_ids(self) -> list: ...
    def info(self, design_id: str) -> DesignInfo: ...
    def geometry(self, design_id: str, transforms: Optional[dict] = None,
                 cuts: Optional[list] = None, state: Optional[dict] = None) -> LayerGeometry: ...


class StaticSource:
    """An in-memory LayerSource (tests, demos): {id: (DesignInfo, LayerGeometry)}.
    It has no architecture to resolve, so `transforms` moves each tagged
    vertex by its source's similarity — a TEST DOUBLE only (the real
    adapter resolves semantically). It cannot apply profile `cuts` (no
    architecture): it records them (`cut_calls`) and returns the design's
    geometry; `walls` are given to it explicitly."""

    def __init__(self, designs: dict, walls: Optional[dict] = None):
        self._d = designs
        self._walls = walls or {}
        self.calls = 0
        self.cut_calls = []

    def walls(self, design_id):
        return list(self._walls.get(design_id) or [])

    def sources(self, design_id):
        return list((getattr(self, '_sources', None) or {}).get(design_id) or [])

    def design_ids(self):
        return list(self._d)

    def info(self, design_id):
        return self._d[design_id][0]

    def geometry(self, design_id, transforms=None, cuts=None, state=None):
        self.calls += 1
        if cuts:
            self.cut_calls.append((design_id, cuts))
        g = self._d[design_id][1]
        if not transforms:
            return g
        polys = []
        for pl in g.polylines:
            tags = pl.get('src') or [None] * len(pl['pts'])
            pts = []
            for (x, y), t in zip(pl['pts'], tags):
                k, tx, ty = transforms.get(t, (1.0, 0.0, 0.0))
                pts.append([k * x + tx, k * y + ty])
            polys.append(dict(pl, pts=pts))
        return LayerGeometry(design_id, polys, g.bead_width, g.diagnostics, g.sources)


def bbox_of(polylines):
    """(x0, y0, x1, y1) of resolved printable geometry — what SCALE needs
    (the Assembly reads only coordinates, never their meaning)."""
    xs = [x for p in polylines for x, _ in p['pts']]
    ys = [y for p in polylines for _, y in p['pts']]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def stack_geometry(resolved, source) -> dict:
    """{geometry key: (polylines, bead width)} for every layer of a resolved
    assembly: each design once, plus each grouped layer's SEMANTIC variant
    (what support analysis and the preview consume)."""
    out = {}
    if hasattr(source, 'prefetch'):
        source.prefetch([variant_request(i) for i in resolved.instances if i.is_variant])
    for i in resolved.instances:
        if i.design_id not in out:
            g = source.geometry(i.design_id)
            out[i.design_id] = (g.polylines, g.bead_width)
        if i.is_variant and i.geometry_key not in out:
            g = layer_geometry(source, i)
            out[i.geometry_key] = (g.polylines, g.bead_width)
    return out


def variant_request(inst) -> tuple:
    """(design id, transforms, cuts[, state]) of a layer printing a variant
    — the state only when the layer has one (the chain ran)."""
    st = getattr(inst, 'state', None)
    if st:
        return (inst.design_id, inst.transforms(), inst.cut_spec(), st)
    return (inst.design_id, inst.transforms(), inst.cut_spec())


def layer_geometry(source, inst):
    """The geometry a layer prints: its design, or its VARIANT (transform
    groups → semantic transforms; profiles → cuts; the construction state)."""
    if not inst.is_variant:
        return source.geometry(inst.design_id)
    st = getattr(inst, 'state', None) or None
    if st:
        return source.geometry(inst.design_id, inst.transforms() or None, inst.cut_spec() or None, st)
    if inst.cuts:
        return source.geometry(inst.design_id, inst.transforms() or None, inst.cut_spec())
    return source.geometry(inst.design_id, inst.transforms())


def toolpath_graph():
    """(toolpath_proto.graph, toolpath_proto.geometry): the layer router the
    print plan falls back on for a LayerSource without its own `route`."""
    import os
    import sys
    tp = os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto')
    if tp not in sys.path:
        sys.path.insert(0, tp)
    import graph
    import geometry
    return graph, geometry
