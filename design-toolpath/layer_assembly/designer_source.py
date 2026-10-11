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


def lattice_mode() -> str:
    """The generator of print preparation's generated Wall Systems (M3):
    'stack' (default — the stack-aware reference, layer_design.stack_reference)
    | 'layer' (the previous per-layer generator, kept as the reference /
    fallback: WALL_LATTICE_MODE=layer)."""
    return 'layer' if os.environ.get('WALL_LATTICE_MODE', 'stack').strip().lower() == 'layer' else 'stack'


def _geometry_of(built):
    from source_attribution import attribute
    import time
    mat = built.document.get('material') or {}
    t0 = time.perf_counter()
    tags, labels = attribute(built.document, built.printable, built.strand_sources)
    polys = [dict(pl, src=t) for pl, t in zip(built.printable, tags)]
    timing = dict(built.timing or {}, source_attribution=round(time.perf_counter() - t0, 4))
    # (the Wall System reports — route closability per generated band — for
    # the Wall Systems workspace's continuity inspection, M2)
    ws_rep = [{'system_id': w.get('system_id'), 'sources': list(w.get('sources') or []),
               'closable': w.get('closable'), 'status': w.get('status'),
               'type': w.get('system'), 'generators': w.get('generators') or {},
               'generator_reasons': w.get('generator_reasons') or []}
              for w in built.network.get('wall_systems') or []]
    return (polys, mat.get('bead_width'),
            {'lattice': built.network.get('lattice'), 'trims': built.network.get('trims'),
             'semantic': built.semantic, 'profile': (built.semantic or {}).get('profile', []),
             'routing': built.routing, 'timing': timing, 'wall_systems': ws_rep}, labels)


def _variant_worker(designs_json, design_id, transforms, cuts=None):
    """(in a worker process) one variant (semantic transforms and / or
    Assembly-profile cuts), from the designs JSON."""
    import json
    from layer_design import DesignLibrary
    lib = _WORKER_LIBS.get(designs_json)
    if lib is None:
        _WORKER_LIBS.clear()
        spec = json.loads(designs_json)
        if isinstance(spec, dict):              # designs [+ the project's Wall Systems] [+ M3 mode]
            lib = DesignLibrary(spec['designs'], spec.get('wall_systems'), spec.get('arch', False),
                                spec.get('stack_lattice', False))
        else:
            lib = DesignLibrary(spec)
        _WORKER_LIBS[designs_json] = lib
    return _geometry_of(lib.build(design_id, transforms=transforms or None, cuts=cuts or None))


def _vkey(design_id, transforms, cuts):
    import semantic_transform as ST
    import profile_cuts as PC
    return (design_id, ST.spec_key(transforms) if transforms else '[]', PC.cut_key(cuts) if cuts else '')


def grouped(designs) -> bool:
    """Do these Layer Designs use the Wall Group form (2026-10-11,
    design_proto/wall_groups.py)?"""
    from wall_groups import is_grouped
    return any(is_grouped((d.get('document') if isinstance(d, dict) else d.document) or {})
               or 'wall_groups' in ((d.get('patch') if isinstance(d, dict) else d.patch) or {}) for d in designs)


def arch_library(designs):
    """The ARCHITECTURE library of a design set (Wall Group documents
    composed without construction: envelopes), or None for legacy documents."""
    if not grouped(designs):
        return None
    from layer_design import DesignLibrary
    return DesignLibrary(designs, arch=True)


def architectural_cuts(cuts):
    """The cut ops that change the architecture (envelope.architectural_cuts)."""
    from envelope import architectural_cuts as ac
    return ac(cuts)


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
        self._envelopes = OrderedDict()          # (design, spec key, ARCHITECTURAL cut key) → envelope geometry
        # the canonical builds of the SAME design set, shared with the other
        # adapter of these designs (web.py: Assembly editing and print
        # preparation keep separate adapters; each canonical design is built once)
        self.shared_plain = None
        # the ARCHITECTURE library (Wall Group documents composed without
        # construction: wall_groups.compose arch) that envelopes come from —
        # shared by every adapter of one design set, whatever its Wall
        # Systems (web.py); None: envelopes from `lib` (legacy documents)
        self.env_lib = None

    @staticmethod
    def from_designs(designs, wall_systems=None) -> 'DesignerSource':
        """wall_systems: the project's Wall Systems (Wall Group documents are
        composed with them; with Wall Group documents and none given: [],
        every wall Hollow)."""
        from layer_design import DesignLibrary
        if wall_systems is None and grouped(designs):
            wall_systems = []
        return DesignerSource(DesignLibrary(designs, wall_systems, stack_lattice=lattice_mode() == 'stack'))

    def design_ids(self):
        return list(self.lib.designs)

    def info(self, design_id):
        d = self.lib.get(design_id)
        return DesignInfo(d.id, d.name, d.parent)

    def _fresh(self):
        """Drop cached geometry when the library was edited (its build cache
        is replaced on every edit)."""
        if getattr(self, '_for', None) is not self.lib._cache:
            if getattr(self, '_for', None) is not None:
                self.shared_plain = None         # (edited: no longer that design set)
            self._for = self.lib._cache
            self._envelopes.clear()
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
        (design_proto/profile_cuts.straight_walls). Wall Group designs: the
        ARCHITECTURE only (2026-10-11 — Assembly opening validation never
        depends on the Wall Systems): from the architecture composition,
        without a construction minimum (`min_length` 0: the architectural pier
        / retained-length rules in beads apply); the construction's own
        minimum is checked after generation (construction_walls)."""
        self._fresh()
        if design_id not in self._walls:
            from profile_cuts import straight_walls
            if self.architectural(design_id):
                self._walls[design_id] = [dict(w, min_length=0.0)
                                          for w in straight_walls(self.env_lib.document(design_id))]
            else:
                self._walls[design_id] = straight_walls(self.lib.document(design_id))
        return self._walls[design_id]

    def architectural(self, design_id) -> bool:
        """A Wall Group design (its constructions are the project's Wall
        Systems) with an architecture library: the Assembly reads its
        ARCHITECTURE only — placement footprints, transform groups, profile
        targets and openings — so changing a Wall System never moves or
        re-validates the Assembly (2026-10-11). Legacy documents (their
        construction authored in the document): as before."""
        if self.env_lib is None:
            return False
        try:
            return 'wall_groups' in self.lib.raw_document(design_id)
        except (AttributeError, KeyError):
            return False

    def construction_walls(self, design_id):
        """The straight walls with their CONSTRUCTION's printable minimum
        (`min_length`: 2 × thickness for generated constructions) — the Wall
        Systems workspace's final construction check."""
        from profile_cuts import straight_walls
        return straight_walls(self.lib.document(design_id))

    def openings(self, design_id):
        """The design's authored OPENINGS on straight walls (id, source, edge,
        at, width) — what a 'Convert to self-supporting opening' refers to
        (design_proto/profile_cuts.designer_openings)."""
        self._fresh()
        if design_id not in self._opens:
            from profile_cuts import designer_openings
            lib = self.env_lib if self.architectural(design_id) else self.lib
            self._opens[design_id] = designer_openings(lib.document(design_id))
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
                sp = self.shared_plain
                g = sp.get(design_id) if sp is not None else None
                if g is None:
                    g = LayerGeometry(design_id, *_geometry_of(self.lib.build(design_id)))
                    if sp is not None:
                        sp[design_id] = g
                self._plain[design_id] = g
            return self._plain[design_id]
        key = _vkey(design_id, transforms, cuts)
        if key not in self._variants:
            self._store(key, _geometry_of(self.lib.build(design_id, transforms=transforms or None,
                                                         cuts=cuts or None)))
        self._variants.move_to_end(key)
        return self._variants[key]

    def prefetch_plain(self, design_ids):
        """The CANONICAL builds of several designs in parallel worker
        processes (Assembly editing after a Designer edit: the footprints /
        groups need each used design's printed geometry once) — the same
        build as geometry(design) (shared with the other adapter)."""
        import json
        self._fresh()
        sp = self.shared_plain if self.shared_plain is not None else {}
        miss = [d for d in dict.fromkeys(design_ids) if d not in self._plain and d not in sp]
        if len(miss) < 2 or _workers() < 2:
            return
        dj = self._lib_json()
        global _POOL
        try:
            futs = [(d, _pool().submit(_variant_worker, dj, d, None, None)) for d in miss]
            for d, f in futs:
                g = LayerGeometry(d, *f.result())
                self._plain[d] = g
                if self.shared_plain is not None:
                    self.shared_plain.setdefault(d, g)
        except Exception:                        # no usable worker processes: geometry() builds here
            _POOL = None

    def _lib_json(self):
        """The library as worker processes rebuild it (designs [+ the
        project's Wall Systems when it composes Wall Groups])."""
        import json
        designs = [x.to_dict() for x in self.lib.designs.values()]
        stack = bool(getattr(self.lib, 'stack_lattice', False))
        if not getattr(self.lib, '_compose', False):
            if not stack:
                return json.dumps(designs, sort_keys=True)
            return json.dumps({'designs': designs, 'wall_systems': None, 'stack_lattice': True}, sort_keys=True)
        return json.dumps({'designs': designs, 'wall_systems': self.lib.wall_systems, 'arch': self.lib.arch,
                           **({'stack_lattice': True} if stack else {})}, sort_keys=True)

    def envelope(self, design_id, transforms=None, cuts=None):
        """The ARCHITECTURAL ENVELOPE of a layer (deferred print paths,
        2026-10-11): the design / variant resolved up to its wall networks
        only — no Wall Systems, lattice or routing
        (layer_design.DesignLibrary.envelope, design_proto/envelope.py) — as
        a LayerGeometry whose polylines are the material rings (role 'ring')
        and bare single beads (role 'bead'), each vertex tagged with its
        source path; `diagnostics['envelope']` holds the whole Envelope.
        Lattice-only cut ops (stations, transitions) are ignored."""
        self._fresh()
        from envelope import architectural_cuts
        cuts = architectural_cuts(cuts)
        key = _vkey(design_id, transforms, cuts)
        g = self._envelopes.get(key)
        if g is None:
            env = (self.env_lib or self.lib).envelope(design_id, transforms or None, cuts)
            rep = env.report or {}
            g = LayerGeometry(design_id, env.polylines(), env.bead_width,
                              {'envelope': env, 'semantic': rep, 'profile': rep.get('profile', [])}, env.labels)
            self._envelopes[key] = g
            while len(self._envelopes) > 1024:
                self._envelopes.popitem(last=False)
        self._envelopes.move_to_end(key)
        return g

    def envelope_key(self, design_id, transforms=None, cuts=None) -> str:
        """The geometry key of a layer's envelope: its design id when it is
        the canonical envelope (no transforms, no architectural cut), else a
        short stable key of (design, transform spec, architectural cuts) —
        layers differing only in lattice stations share it."""
        import hashlib
        from envelope import architectural_cuts
        cuts = architectural_cuts(cuts)
        if not transforms and not cuts:
            return design_id
        k = _vkey(design_id, transforms, cuts)
        return f'{design_id}|env|' + hashlib.sha1('\n'.join(k).encode()).hexdigest()[:20]

    def _store(self, key, g):
        self._variants[key] = LayerGeometry(key[0], *g)
        while len(self._variants) > 512:
            self._variants.popitem(last=False)

    def prefetch(self, requests, cancelled=None):
        """Resolve many variants [(design id, transforms[, cuts])] — in
        parallel worker processes when several are missing (each is a full
        Designer resolve: ~0.1–0.6 s). `cancelled()` (a background print
        plan, 2026-10-10 performance pass): once it is True no further build
        is started — builds not yet started are dropped, those finished are
        kept — and the plan, polling it right after, gives way to the
        Assembly request (it waited for the whole prefetch before)."""
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
                if cancelled is not None and cancelled():
                    return
                self._store(key, _geometry_of(self.lib.build(d, transforms=t or None, cuts=c or None)))
            return
        dj = self._lib_json()
        global _POOL
        try:
            futs = [(key, _pool().submit(_variant_worker, dj, d, t, c)) for key, d, t, c in miss]
            for key, f in futs:
                if cancelled is not None and cancelled():
                    for _, g in futs:
                        g.cancel()                   # (not started: dropped; running: finish, kept)
                    for k2, g in futs:
                        if k2 not in self._variants and g.done() and not g.cancelled() and g.exception() is None:
                            self._store(k2, g.result())
                    return
                self._store(key, f.result())
        except Exception:                        # no usable worker processes: build here
            _POOL = None
            for key, d, t, c in miss:
                if key not in self._variants:
                    self._store(key, _geometry_of(self.lib.build(d, transforms=t or None, cuts=c or None)))
