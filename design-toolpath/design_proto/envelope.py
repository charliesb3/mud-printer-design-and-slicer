"""
ARCHITECTURAL ENVELOPE (deferred print paths, milestone M0 — 2026-10-11;
design-toolpath/DEFERRED_TOOLPATH_ARCHITECTURE.md).

The Designer's own build (model.PrintLayer._build_effective) stopped after
the wall NETWORKS: source paths, trims, wall faces, openings (Designer and
Assembly-cut alike), junctions / fillets, the material rings — exactly as a
full build resolves them — and nothing of Wall Systems, the wall lattice,
region infill or routing (`PrintLayer.envelope_only`). There is no second
geometry algorithm: the rings ARE the `regions` a full build hands to Wall
Systems and the lattice (`BuiltLayer.envelope_rings`; equality is tested).

What an Envelope carries (plain data, JSON-ready):
  rings        material components → their rings ([[x, y]…], closed, not
               repeated; outer + holes, even-odd)
  ring_sources aligned with `rings`: the SOURCE PATH id of each vertex — the
               edge starting there (source_attribution.attribute: a vertex on
               a face / cap bead takes that bead's source, any other one the
               Designer's junction-territory rule)
  strands      the stage's printed strands that bound NO material ring
               (single-bead walls, return lanes, legacy lattice instances):
               {id, pts, closed, source, cls}
  centrelines  source id → processed source polyline (after cuts / transforms)
  walls        source id → its effective wall {thickness, align, system, …}
               (None = a single bead)
  wall_systems the document's Wall Systems (assignments + parameters)
  membership   source id → its Wall System {id, name, type, filled}
  openings     the document's openings (Designer + Assembly `asm-…`) and
               their resolution status
  trims, junctions, report (the cut / semantic-transform report), bead_width

Cut ops that only steer the lattice (`station`, `transition`) do not change
the architecture: `architectural_cuts` drops them, so an Assembly opening
moving elsewhere in the stack leaves every other layer's envelope (and its
cache key) unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

LATTICE_ONLY_OPS = ('station', 'transition')


def architectural_cuts(cuts):
    """The cut ops that change a layer's ARCHITECTURE (openings, extents,
    planes, opening moves) — `station` / `transition` only steer the lattice
    (profile_cuts: no cut / a virtual opening). None when nothing is left."""
    out = [c for c in cuts or [] if c.get('op') not in LATTICE_ONLY_OPS]
    return out or None


def _xy(pts):
    return [[float(p.x), float(p.y)] for p in pts]


@dataclass
class Envelope:
    design_id: str
    rings: list
    ring_sources: list
    strands: list
    centrelines: dict
    walls: dict
    wall_systems: list
    membership: dict
    openings: list
    trims: dict
    junctions: list
    report: dict = field(default_factory=dict)
    bead_width: float = 0.0
    labels: dict = field(default_factory=dict)

    def polylines(self) -> list:
        """The Assembly's drawing / support geometry: every material ring as a
        closed polyline (`role` 'ring') and every bare strand (`role` 'bead'),
        each with a per-vertex `src` tag — the LayerGeometry polyline form."""
        out = []
        for c, (comp, srcs) in enumerate(zip(self.rings, self.ring_sources)):
            for k, (ring, src) in enumerate(zip(comp, srcs)):
                out.append({'id': f'env{c}.{k}', 'pts': ring, 'closed': True, 'src': src, 'role': 'ring'})
        for s in self.strands:
            # `single`: the printed lines of a SINGLE-BEAD wall (its return
            # lanes) — no envelope; the Designer's envelope view leaves them out
            out.append({'id': s['id'], 'pts': s['pts'], 'closed': s['closed'], 'src': s['src'], 'role': 'bead',
                        'cls': s.get('cls'), 'single': self.walls.get(s.get('source')) is None})
        return out

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ('design_id', 'rings', 'ring_sources', 'strands', 'centrelines',
                                              'walls', 'wall_systems', 'membership', 'openings', 'trims',
                                              'junctions', 'report', 'bead_width', 'labels')}


def _ring_test(rings, tol=0.05):
    """on(pts): every sample of a polyline within `tol` of a ring boundary
    (the membership test model._apply_wall_systems uses for faces of a
    region)."""
    import math
    cell = 4.0
    grid = {}
    for comp in rings:
        for r in comp:
            for a, b in zip(r, r[1:] + r[:1]):
                for gx in range(math.floor((min(a[0], b[0]) - tol) / cell), math.floor((max(a[0], b[0]) + tol) / cell) + 1):
                    for gy in range(math.floor((min(a[1], b[1]) - tol) / cell), math.floor((max(a[1], b[1]) + tol) / cell) + 1):
                        grid.setdefault((gx, gy), []).append((a, b))

    def near(p):
        for (ax, ay), (bx, by) in grid.get((math.floor(p[0] / cell), math.floor(p[1] / cell)), ()):
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
            if math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy) < tol:
                return True
        return False

    def on(pts):
        return bool(grid) and all(near(p) for p in pts[::max(1, len(pts) // 12)] + [pts[-1]])
    return on


def from_stage(design_id, doc, report, layer, result, meta) -> Envelope:
    """The Envelope of a layer built with `envelope_only` (result, meta =
    its _build_effective())."""
    import network as N
    from source_attribution import attribute
    cls_of = meta['cls']
    net = meta['network']
    src_ids = {sp.get('id') for sp in doc.get('source_paths') or []}

    def source_of(p):                     # (the rule of layer_design._finish's strand_sources)
        sid = getattr(p, 'source_id', None) or (p.id if p.id in src_ids else None)
        return sid if sid in src_ids else None
    faces, strands = [], []
    for p in result:
        if cls_of.get(id(p)) == N.VIRTUAL:
            continue
        pts = p.sample_points()
        if len(pts) < 2:
            continue
        rec = {'id': str(p.id), 'pts': _xy(pts), 'closed': bool(p.closed), 'source': source_of(p),
               'cls': cls_of.get(id(p))}
        (faces if rec['cls'] == N.FACE else strands).append(rec)
    rings = [[_xy(r) for r in comp] for comp in meta.get('regions') or []]
    # FACE beads that bound no material ring (a plain / Hollow wall without
    # an infill region: its faces and caps ARE what it prints) are kept as
    # bare beads; faces on a ring boundary are represented by the ring
    on_ring = _ring_test(rings)
    strands += [f for f in faces if not on_ring(f['pts'])]
    # SOURCE ATTRIBUTION — the Designer's rule (source_attribution.attribute):
    # face / cap beads name their source, every ring vertex and bare strand
    # vertex takes the face it lies on (or the junction-territory owner)
    # (a ring vertex that IS a vertex of face beads of one source takes it
    # directly — the same answer, without the nearest-face search)
    on_face = {}
    for f in faces:
        if f['source']:
            for x, y in f['pts']:
                on_face.setdefault((x, y), set()).add(f['source'])
    flat = [r for comp in rings for r in comp]
    flat_tags = [[(lambda s: next(iter(s)) if s is not None and len(s) == 1 else None)(on_face.get((x, y)))
                  for x, y in r] for r in flat]
    # a vertex shared by two faces' sources (a junction node) or none (an
    # arrangement node): the source of the EDGE starting there — the next
    # vertex that has one along the ring (the face that edge lies on)
    for t in flat_tags:
        n = len(t)
        if n and any(t) and not all(t):
            nxt = None
            for j in range(2 * n - 1, -1, -1):
                v = t[j % n]
                if v is not None:
                    nxt = v
                elif j < n:
                    t[j] = nxt
    rest = [(i, j) for i, t in enumerate(flat_tags) for j, v in enumerate(t) if v is None]
    labels = {s['id']: (s.get('label') or s['id']) for s in doc.get('source_paths') or []}
    for s in strands:
        if s['source']:
            s['src'] = [s['source']] * len(s['pts'])
    if rest or any(not s['source'] for s in strands):
        # the rest: the Designer's attribution rule (junction territory /
        # nearest face) — as for printed geometry
        printable = [{'id': f['id'], 'pts': f['pts'], 'closed': f['closed']} for f in faces + strands] + \
                    ([{'id': '__ring', 'pts': [flat[i][j] for i, j in rest], 'closed': False}] if rest else [])
        named = {f['id']: f['source'] for f in faces + strands if f['source']}
        tags, _ = attribute(doc, printable, named)
        nfs = len(faces) + len(strands)
        for s, t in zip(strands, tags[len(faces):nfs]):
            s.setdefault('src', t)
        for (i, j), t in zip(rest, tags[nfs] if rest else ()):
            flat_tags[i][j] = t
    ring_sources, k = [], 0
    for comp in rings:
        ring_sources.append(flat_tags[k:k + len(comp)])
        k += len(comp)
    processed = meta.get('processed') or {}
    by_id = {p.id: p for p in layer.source_paths}
    centrelines = {sid: {'pts': _xy(pts), 'closed': bool(getattr(by_id.get(sid), 'closed', False))}
                   for sid, pts in processed.items()}
    openings = [dict(o, status=(net.get('opening_status') or {}).get(o.get('id')))
                for o in doc.get('openings') or []]
    return Envelope(design_id, rings, ring_sources,
                    [dict(s, src=s.get('src') or [s['source']] * len(s['pts'])) for s in strands],
                    centrelines, dict(net.get('source_walls') or {}),
                    [dict(w) for w in doc.get('wall_systems') or []],
                    dict(net.get('wall_system_membership') or {}), openings,
                    net.get('trims') or {}, list(net.get('junctions') or []), report or {},
                    float(((doc.get('material') or {}).get('bead_width')) or layer.material.bead_width),
                    labels)
