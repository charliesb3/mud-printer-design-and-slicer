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
    of a grouped layer itself."""
    def design_ids(self) -> list: ...
    def info(self, design_id: str) -> DesignInfo: ...
    def geometry(self, design_id: str, transforms: Optional[dict] = None) -> LayerGeometry: ...


class StaticSource:
    """An in-memory LayerSource (tests, demos): {id: (DesignInfo, LayerGeometry)}.
    It has no architecture to resolve, so `transforms` moves each tagged
    vertex by its source's similarity — a TEST DOUBLE only (the real
    adapter resolves semantically)."""


    def __init__(self, designs: dict):
        self._d = designs
        self.calls = 0

    def design_ids(self):
        return list(self._d)

    def info(self, design_id):
        return self._d[design_id][0]

    def geometry(self, design_id, transforms=None):
        self.calls += 1
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
        source.prefetch([(i.design_id, i.transforms()) for i in resolved.instances if i.semantic])
    for i in resolved.instances:
        for key, tf in ((i.design_id, None), (i.geometry_key, i.transforms() if i.semantic else None)):
            if key not in out:
                g = source.geometry(i.design_id, tf) if tf else source.geometry(i.design_id)
                out[key] = (g.polylines, g.bead_width)
    return out
