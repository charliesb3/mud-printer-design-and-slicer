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
             'semantic': built.semantic, 'profile': (built.semantic or {}).get('profile', []),
             'routing': built.routing, 'timing': timing}, labels)


def _variant_worker(designs_json, design_id, transforms, cuts=None):
    """(in a worker process) one variant (semantic transforms and / or
    Assembly-profile cuts), from the designs JSON."""
    import json
    from layer_design import DesignLibrary
    lib = _WORKER_LIBS.get(designs_json)
    if lib is None:
        _WORKER_LIBS.clear()
        lib = _WORKER_LIBS[designs_json] = DesignLibrary(json.loads(designs_json))
    return _geometry_of(lib.build(design_id, transforms=transforms or None, cuts=cuts or None))


def _vkey(design_id, transforms, cuts):
    import semantic_transform as ST
    import profile_cuts as PC
    return (design_id, ST.spec_key(transforms) if transforms else '[]', PC.cut_key(cuts) if cuts else '')


class DesignerSource:
    def __init__(self, library):
        self.lib = library
        self.calls = 0
        from collections import OrderedDict
        self._variants = OrderedDict()           # (design, spec key) → LayerGeometry
        self._plain = {}                         # design → its canonical LayerGeometry
        self._walls = {}                         # design → its straight walls (profile targets)
        self._srcs = {}                          # design → every selectable wall (planar cut)
        self._opens = {}                         # design → its authored openings (conversions)
        # support analysis per layer boundary (support.analyse_support `cache`):
        # valid exactly while this library's geometry is (cleared with it)
        self.support_cache = {}
        self._prepared = OrderedDict()           # id(LayerGeometry) → (it, layer_route.Prepared)

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
            self._walls.clear()
            self._srcs.clear()
            self._opens.clear()
            self.support_cache.clear()
            self._prepared.clear()

    def route(self, geometry, start=None, members=None, manual_start=True):
        """The Designer's ROUTE of a layer's resolved geometry (its design or
        a variant: design_proto/layer_route.py) — exactly as the Designer view
        routes that geometry, starting nearest `start` (x, y) where the design
        sets no start of its own. → (toolpath_proto PrintMoves, info,
        {strand id: (source path, Wall System)}). The routing graph is built
        once per geometry (and piece).
        `members`: route only these polylines (indices into
        geometry.polylines) — ONE physical piece of the layer, scheduled on
        its own by the lift planner; the routing description is narrowed to
        them (their Route Origins; the design's start point only if
        `manual_start` — it belongs to the piece nearest to it; the
        component order, which numbers the WHOLE layer's components, is
        dropped)."""
        import layer_route
        key = id(geometry) if members is None else (id(geometry), tuple(members), bool(manual_start))
        e = self._prepared.get(key)
        if e is None or e[0] is not geometry:
            R = geometry.diagnostics.get('routing') or {}
            if members is None:
                prep = layer_route.Prepared(geometry.polylines, R)
            else:
                polys = [geometry.polylines[i] for i in members]
                ids = {p['id'] for p in polys}
                attrs = R.get('strands') or []
                R = dict(R, strands=[attrs[i] for i in members] if attrs else [],
                         origins=[o for o in R.get('origins') or [] if o.get('strand') in ids],
                         start=R.get('start') if manual_start else None, component_order=None)
                prep = layer_route.Prepared(polys, R)
            e = (geometry, prep)
            self._prepared[key] = e
            while len(self._prepared) > 512:
                self._prepared.popitem(last=False)
        self._prepared.move_to_end(key)
        # the same geometry routed from the same start is the same route (a
        # plain section: every closed layer starts where the last one ended)
        memo = e[1].__dict__.setdefault('_routes', {})
        rk = None if start is None else (round(start[0], 6), round(start[1], 6))
        if rk not in memo:
            memo[rk] = layer_route.route(e[1], start)
            while len(memo) > 64:
                memo.pop(next(iter(memo)))
        moves, info = memo[rk]
        return moves, info, e[1].identity

    def walls(self, design_id):
        """The design's STRAIGHT walls — what an Assembly profile can target
        (design_proto/profile_cuts.straight_walls)."""
        self._fresh()
        if design_id not in self._walls:
            from profile_cuts import straight_walls
            self._walls[design_id] = straight_walls(self.lib.document(design_id))
        return self._walls[design_id]

    def openings(self, design_id):
        """The design's authored OPENINGS on straight walls (id, source, edge,
        at, width) — what a 'Convert to self-supporting opening' refers to
        (design_proto/profile_cuts.designer_openings)."""
        self._fresh()
        if design_id not in self._opens:
            from profile_cuts import designer_openings
            self._opens[design_id] = designer_openings(self.lib.document(design_id))
        return self._opens[design_id]

    def sources(self, design_id):
        """Every wall of the design a PLANAR TOP CUT can select: source id,
        label, closed, Wall System (id + name), a coarse centre line
        (design_proto/profile_cuts.source_list)."""
        self._fresh()
        if design_id not in self._srcs:
            from profile_cuts import source_list
            self._srcs[design_id] = source_list(self.lib.document(design_id))
        return self._srcs[design_id]

    def geometry(self, design_id, transforms=None, cuts=None):
        """The design's resolved geometry — with `transforms`, its SEMANTIC
        variant (the Designer moves the sources, regenerates connecting
        walls and resolves walls / junctions / openings / lineage lattice:
        layer_design.DesignLibrary.build(…, transforms)). With `cuts`
        (Assembly profiles) the cut variant — cuts applied first."""
        self.calls += 1
        self._fresh()
        if not transforms and not cuts:          # the canonical design: built and attributed once
            if design_id not in self._plain:
                self._plain[design_id] = LayerGeometry(design_id, *_geometry_of(self.lib.build(design_id)))
            return self._plain[design_id]
        key = _vkey(design_id, transforms, cuts)
        if key not in self._variants:
            self._store(key, _geometry_of(self.lib.build(design_id, transforms=transforms or None,
                                                         cuts=cuts or None)))
        self._variants.move_to_end(key)
        return self._variants[key]

    def _store(self, key, g):
        self._variants[key] = LayerGeometry(key[0], *g)
        while len(self._variants) > 512:
            self._variants.popitem(last=False)

    def prefetch(self, requests):
        """Resolve many variants [(design id, transforms[, cuts])] — in
        parallel worker processes when several are missing (each is a full
        Designer resolve: ~0.1–0.6 s)."""
        import json
        self._fresh()
        miss, seen = [], set()
        for r in requests:
            d, t = r[0], r[1]
            c = r[2] if len(r) > 2 else None
            if not t and not c:
                continue
            key = _vkey(d, t, c)
            if key not in self._variants and key not in seen:
                seen.add(key)
                miss.append((key, d, t, c))
        if len(miss) < 3 or _workers() < 2:
            for key, d, t, c in miss:
                self._store(key, _geometry_of(self.lib.build(d, transforms=t or None, cuts=c or None)))
            return
        dj = json.dumps([x.to_dict() for x in self.lib.designs.values()], sort_keys=True)
        global _POOL
        try:
            futs = [(key, _pool().submit(_variant_worker, dj, d, t, c)) for key, d, t, c in miss]
            for key, f in futs:
                self._store(key, f.result())
        except Exception:                        # no usable worker processes: build here
            _POOL = None
            for key, d, t, c in miss:
                if key not in self._variants:
                    self._store(key, _geometry_of(self.lib.build(d, transforms=t or None, cuts=c or None)))
