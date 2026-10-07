"""
DesignerSource — the ONLY Layer Assembly module that imports the Designer.
It adapts design_proto.layer_design.DesignLibrary to the LayerSource
protocol: design info + resolved printable 2D geometry, each vertex tagged
with its source path (design_proto/source_attribution.py).
"""
from __future__ import annotations

import os
import sys

from .source import DesignInfo, LayerGeometry

_DESIGNER = os.path.join(os.path.dirname(__file__), '..', 'design_proto')
if _DESIGNER not in sys.path:
    sys.path.insert(0, _DESIGNER)


_POOL = None
_WORKER_LIBS: dict = {}


def _workers() -> int:
    n = os.environ.get('LAYER_ASSEMBLY_WORKERS')
    if n is not None:
        return max(0, int(n))
    return min(8, os.cpu_count() or 1)


def _pool():
    global _POOL
    if _POOL is None:
        import concurrent.futures as cf
        import multiprocessing as mp
        _POOL = cf.ProcessPoolExecutor(max_workers=_workers(), mp_context=mp.get_context('spawn'))
    return _POOL


def _geometry_of(built):
    from source_attribution import attribute
    import time
    mat = built.document.get('material') or {}
    t0 = time.perf_counter()
    tags, labels = attribute(built.document, built.printable, built.strand_sources)
    polys = [dict(pl, src=t) for pl, t in zip(built.printable, tags)]
    timing = dict(built.timing or {}, source_attribution=round(time.perf_counter() - t0, 4))
    return (polys, mat.get('bead_width'),
            {'lattice': built.network.get('lattice'), 'trims': built.network.get('trims'),
             'semantic': built.semantic, 'timing': timing}, labels)


def _variant_worker(designs_json, design_id, transforms):
    """(in a worker process) one semantic variant, from the designs JSON."""
    import json
    from layer_design import DesignLibrary
    lib = _WORKER_LIBS.get(designs_json)
    if lib is None:
        _WORKER_LIBS.clear()
        lib = _WORKER_LIBS[designs_json] = DesignLibrary(json.loads(designs_json))
    return _geometry_of(lib.build(design_id, transforms=transforms))


class DesignerSource:
    def __init__(self, library):
        self.lib = library
        self.calls = 0
        from collections import OrderedDict
        self._variants = OrderedDict()           # (design, spec key) → LayerGeometry
        self._plain = {}                         # design → its canonical LayerGeometry

    @staticmethod
    def from_designs(designs) -> 'DesignerSource':
        from layer_design import DesignLibrary
        return DesignerSource(DesignLibrary(designs))

    def design_ids(self):
        return list(self.lib.designs)

    def info(self, design_id):
        d = self.lib.get(design_id)
        return DesignInfo(d.id, d.name, d.parent)

    def _fresh(self):
        """Drop cached geometry when the library was edited (its build cache
        is replaced on every edit)."""
        if getattr(self, '_for', None) is not self.lib._cache:
            self._for = self.lib._cache
            self._plain.clear()
            self._variants.clear()

    def geometry(self, design_id, transforms=None):
        """The design's resolved geometry — with `transforms`, its SEMANTIC
        variant (the Designer moves the sources, regenerates connecting
        walls and resolves walls / junctions / openings / lineage lattice:
        layer_design.DesignLibrary.build(…, transforms))."""
        self.calls += 1
        self._fresh()
        if not transforms:                       # the canonical design: built and attributed once
            if design_id not in self._plain:
                self._plain[design_id] = LayerGeometry(design_id, *_geometry_of(self.lib.build(design_id)))
            return self._plain[design_id]
        import semantic_transform as ST
        key = (design_id, ST.spec_key(transforms))
        if key not in self._variants:
            self._store(key, _geometry_of(self.lib.build(design_id, transforms=transforms)))
        self._variants.move_to_end(key)
        return self._variants[key]

    def _store(self, key, g):
        self._variants[key] = LayerGeometry(key[0], *g)
        while len(self._variants) > 512:
            self._variants.popitem(last=False)

    def prefetch(self, requests):
        """Resolve many semantic variants [(design id, transforms)] — in
        parallel worker processes when several are missing (each is a full
        Designer resolve: ~0.1–0.6 s)."""
        import json
        import semantic_transform as ST
        self._fresh()
        miss, seen = [], set()
        for d, t in requests:
            if not t:
                continue
            key = (d, ST.spec_key(t))
            if key not in self._variants and key not in seen:
                seen.add(key)
                miss.append((key, d, t))
        if len(miss) < 3 or _workers() < 2:
            for key, d, t in miss:
                self._store(key, _geometry_of(self.lib.build(d, transforms=t)))
            return
        dj = json.dumps([x.to_dict() for x in self.lib.designs.values()], sort_keys=True)
        global _POOL
        try:
            futs = [(key, _pool().submit(_variant_worker, dj, d, t)) for key, d, t in miss]
            for key, f in futs:
                self._store(key, f.result())
        except Exception:                        # no usable worker processes: build here
            _POOL = None
            for key, d, t in miss:
                if key not in self._variants:
                    self._store(key, _geometry_of(self.lib.build(d, transforms=t)))
