"""
LAYER DESIGNS — reusable 2D printable layer definitions, with inheritance.

This is the Designer side of the Designer ↔ Layer Assembly boundary
(design-toolpath/layer_assembly consumes it through a narrow interface and
knows nothing about walls, trims, lattices or beads).

A LAYER DESIGN is a Designer DOCUMENT — exactly the JSON the Design Canvas
edits and posts to /api/route (`layer` in static/app.js) — plus an id, a
user name ("Base", "Door Gap" …) and an optional PARENT. There is no door /
window object: a doorway is simply a design whose geometry has a gap.

INHERITANCE (id-keyed overlay, resolved LIVE — never copied):
    base design:    {'id', 'name', 'document': {...full document...}}
    derived design: {'id', 'name', 'parent': <id>,
                     'patch':    {collection: {record id: partial record | None}},
                     'settings': {scalar setting: value}}
  * every record of the parent not named in `patch` is inherited as is, so
    later edits of the parent propagate;
  * {id: {...fields}} on an existing record merges those FIELDS onto the
    parent's record (other fields keep following the parent);
  * {id: {...}} with a new id adds a record (complete);  {id: None} removes;
  * `settings` overrides scalar settings (dict settings — material,
    constraints — merge one level deep).
  Derivation may be multi-level; cycles are rejected. Record ids survive
  derivation, so everything keyed by id (openings on a wall, trims, Route
  Origins on printable strands, infills) stays meaningful.

ONE SHARED LATTICE PER LINEAGE (decision 2026-10-06; supersedes "the
parent's lattice, clipped"): the designs of a lineage that share a wall
infill (same path / kind / pattern / params / variation) form a LATTICE
FAMILY — its GENERATOR (the root, or the design where that infill was
introduced) and every descendant inheriting it. A family of several
designs shares ONE vertically registered SCAFFOLD:
  * planned ONCE on the generator's document with every opening removed
    (openings only subtract material, so this is the family's union);
  * every end wall (cap) that any member's openings create is a JAMB line
    of the scaffold (wall_lattice "stations", module doc §5): its run
    carries two passes crossing exactly at the line's midpoint, so the
    member's clipped lattice ends in a cap V there and its connected wall
    is ONE closed route; Base and the other members carry the doubled run
    and the crossing as a relic (vertically registered with the member's
    end wall);
  * every member — the generator included — prints the scaffold clipped to
    its own material (model.PrintLayer.lattice_reference);
  * A CLOSED ROUTE OUTRANKS REGISTRATION (stabilization 2026-10-06): each
    member's closure with the clipped scaffold is checked
    (scaffold_verdict). An opening reaching into another wall's material
    rebuilds the junction there, so the member is not "Base minus the
    opening" and may not close: it is then planned ON ITS OWN (closed),
    reported in lattice_lineage ('planned_alone').
A family of ONE design is planned normally (so a single design, or a
project without variants, is unchanged). The Designer view of a design
resolves through the same library (app.py `lineage`), so Designer and
Assembly show identical lattice.
LATTICE DEFINITION BELONGS TO THE LINEAGE (decision 2026-10-06): pattern,
params (spacing, max unsupported …) and variation of a wall infill live in
the design that INTRODUCED the infill record (its owner — Base for
inherited infills). A derived patch never overrides them (ignored if
present); an edit made while viewing any member is moved to the owner
(set_document), so Base, siblings and descendants always print the same
lattice. A descendant that introduces its OWN infill on the lineage's wall
is reported as a conflict (lattice_conflicts), never silently stacked.
PROJECT MATERIAL: the bead width is owned by the lineage ROOT (Base's
document `material.bead_width`); derived designs never store their own.
TRANSFORMED VARIANTS: build(design, transforms={source: (k, tx, ty)}) —
the Layer Assembly's semantic transform groups (semantic_transform.py):
the sources move, the scaffold is planned for the same transforms with
CROSS-Z tracking against the untransformed scaffold, and the variant is
resolved / verified like any member. Cached (_tbuilds, _SHARED).
Whether material over a gap is vertically SUPPORTED is the Layer
Assembly's concern, not the design's.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

# Content-keyed results shared by every library instance (the Designer view
# builds a fresh library per request): end-wall sets and scaffolds.
from collections import OrderedDict
_SHARED: OrderedDict = OrderedDict()
_SHARED_MAX = 64


def _shared(key, make):
    if key in _SHARED:
        _SHARED.move_to_end(key)
        return _SHARED[key]
    val = make()
    _SHARED[key] = val
    while len(_SHARED) > _SHARED_MAX:
        _SHARED.popitem(last=False)
    return val


def _transforms_active(transforms) -> bool:
    import semantic_transform as ST
    return bool(transforms) and ST.spec_key(transforms) != '[]'


def _merged_track(reports) -> dict:
    """The station tracks of a reference plan's regions, as one list."""
    return {'runs': [run for r in reports or [] for run in ((r or {}).get('track') or {}).get('runs', [])]}


_TRACK_MAPS: OrderedDict = OrderedDict()


def _track_map(doc0, transforms):
    """Reference (untransformed) point → this transformed design, carried
    WALL-RELATIVELY by the source that owns it (junction-seam ownership,
    source_attribution) — never by a transform-centre distance."""
    import semantic_transform as ST
    from source_attribution import _seam_owner
    from model import Vec2
    key = (json.dumps(doc0, sort_keys=True), ST.spec_key(transforms))
    if key in _TRACK_MAPS:
        _TRACK_MAPS.move_to_end(key)
        return _TRACK_MAPS[key]
    maps = ST.source_maps(doc0, transforms)
    owner = _seam_owner(doc0) or (lambda p: None)
    lines = {d['id']: ST._centreline(d) for d in doc0.get('source_paths') or []
             if d.get('id') in maps}

    def f(p):
        q = ST.map_wall_point(maps, lines, owner, (p.x, p.y))
        return Vec2(q[0], q[1])
    _TRACK_MAPS[key] = f
    while len(_TRACK_MAPS) > 64:
        _TRACK_MAPS.popitem(last=False)
    return f


# Collections of id-keyed records in a Designer document (key field).
KEYED = {
    'source_paths': 'id', 'offset_treatments': 'id', 'lattice_instances': 'id',
    'openings': 'id', 'trims': 'id', 'region_overrides': 'id', 'infills': 'id',
    'network_walls': 'id', 'wall_relations': 'id', 'junction_overrides': 'key',
    'route_origins': 'strand',
}
MERGED_SETTINGS = ('material', 'constraints')
_INFILL_IDENTITY = ('path_id', 'kind', 'pattern', 'params', 'variation_index')
# The LATTICE DEFINITION of a wall infill — owned by the LINEAGE (the design
# that introduced the infill record), never overridden by a descendant.
LATTICE_DEFINITION = ('pattern', 'params', 'variation_index')
# labels of the beads that are END WALLS (jamb lines of a lineage scaffold)
END_WALL_LABELS = ('cap_start', 'cap_end', 'opening_face')


class LayerDesignError(ValueError):
    pass


@dataclass
class LayerDesign:
    id: str
    name: str
    parent: Optional[str] = None
    document: Optional[dict] = None          # base designs: the full document
    patch: dict = field(default_factory=dict)       # derived designs
    settings: dict = field(default_factory=dict)    # derived designs

    @staticmethod
    def from_dict(d: dict) -> 'LayerDesign':
        return LayerDesign(d['id'], d.get('name', d['id']), d.get('parent'),
                           d.get('document'), dict(d.get('patch') or {}),
                           dict(d.get('settings') or {}))

    def to_dict(self) -> dict:
        out = {'id': self.id, 'name': self.name, 'parent': self.parent}
        if self.parent is None:
            out['document'] = self.document
        else:
            out['patch'], out['settings'] = self.patch, self.settings
        return out


def apply_derivation(parent_doc: dict, patch: dict, settings: dict) -> dict:
    """The derived document: the parent's records + this design's delta."""
    doc = copy.deepcopy(parent_doc)
    for coll, changes in (patch or {}).items():
        if coll not in KEYED:
            raise LayerDesignError(f'unknown collection {coll!r} in a patch')
        key = KEYED[coll]
        recs = doc.setdefault(coll, [])
        index = {r.get(key): i for i, r in enumerate(recs)}
        removed = set()
        for rid, change in changes.items():
            if change is None:
                removed.add(rid)
            elif rid in index:
                recs[index[rid]] = {**recs[index[rid]], **copy.deepcopy(change), key: rid}
            else:
                recs.append({**copy.deepcopy(change), key: rid})
                index[rid] = len(recs) - 1
        doc[coll] = [r for r in recs if r.get(key) not in removed]
    for k, v in (settings or {}).items():
        if k in KEYED:
            raise LayerDesignError(f'{k!r} is a record collection: use the patch')
        if k in MERGED_SETTINGS and isinstance(v, dict):
            doc[k] = {**(doc.get(k) or {}), **copy.deepcopy(v)}
        else:
            doc[k] = copy.deepcopy(v)
    return doc


def derive_delta(parent_doc: dict, doc: dict) -> tuple[dict, dict]:
    """The inverse of apply_derivation: the (patch, settings) that turn the
    parent's document into `doc` — what a derived design edited in the
    Designer stores. Records are compared by id; only CHANGED FIELDS of an
    inherited record are stored (the rest keeps following the parent), new
    records completely, removed records as None; scalar settings that
    differ (material / constraints one level deep). Collection order is not
    part of a design's identity."""
    patch: dict = {}
    for coll, key in KEYED.items():
        prs = {r.get(key): r for r in parent_doc.get(coll) or []}
        drs = {r.get(key): r for r in doc.get(coll) or []}
        ch = {}
        for rid, r in drs.items():
            pr = prs.get(rid)
            if pr is None:
                ch[rid] = copy.deepcopy(r)
                continue
            fields = {f: copy.deepcopy(v) for f, v in r.items() if f != key and pr.get(f) != v}
            fields.update({f: None for f in pr if f not in r})
            if fields:
                ch[rid] = fields
        for rid in prs:
            if rid not in drs:
                ch[rid] = None
        if ch:
            patch[coll] = ch
    settings: dict = {}
    for k in set(parent_doc) | set(doc):
        if k in KEYED:
            continue
        pv, v = parent_doc.get(k), doc.get(k)
        if pv == v:
            continue
        if k in MERGED_SETTINGS and isinstance(pv, dict) and isinstance(v, dict):
            settings[k] = {f: copy.deepcopy(x) for f, x in v.items() if pv.get(f) != x}
        else:
            settings[k] = copy.deepcopy(v)
    return patch, settings


def _without_lattice_overrides(parent_doc: dict, patch: dict) -> dict:
    """The patch without changes to the LATTICE DEFINITION of infills the
    design inherits: that definition belongs to the lineage (see
    DesignLibrary.set_document), so a descendant can never stack a
    different lattice on its parent's."""
    inf = (patch or {}).get('infills')
    if not inf:
        return patch
    inherited = {r.get('id') for r in parent_doc.get('infills') or []}
    out = {}
    for rid, ch in inf.items():
        if rid in inherited and isinstance(ch, dict):
            ch = {k: v for k, v in ch.items() if k not in LATTICE_DEFINITION}
            if not ch:
                continue
        out[rid] = ch
    return {**patch, 'infills': out}


@dataclass
class BuiltLayer:
    """A design's resolved result, as the Designer computes it."""
    design_id: str
    document: dict
    printable: list           # [{'id', 'pts': [[x, y]…], 'closed'}]
    lattice: dict             # infill id → [polyline (Vec2)] (wall lattice)
    network: dict             # the Designer's network / diagnostics summary
    lattice_source: dict = field(default_factory=dict)   # infill id → the family SCAFFOLD
                                                         # it is clipped from (or its own lattice)
    strand_sources: dict = field(default_factory=dict)   # printable strand id → the SOURCE PATH it
                                                         # is a face / cap of (lattice: absent)
    semantic: dict = field(default_factory=dict)         # semantic-transform report (connectors …)
    timing: dict = field(default_factory=dict)           # seconds per build stage (instrumentation)


class DesignLibrary:
    """The set of Layer Designs of one project, resolved on demand.
    Resolution and builds are cached; any edit invalidates the cache."""

    def __init__(self, designs=()):
        self.designs: dict[str, LayerDesign] = {}
        self._cache, self._tbuilds = {}, OrderedDict()
        for d in designs:
            self.add(d if isinstance(d, LayerDesign) else LayerDesign.from_dict(d))

    # -- editing ------------------------------------------------------------
    def add(self, design: LayerDesign):
        if design.id in self.designs:
            raise LayerDesignError(f'duplicate design id {design.id!r}')
        if design.parent is None and design.document is None:
            raise LayerDesignError(f'base design {design.id!r} needs a document')
        self.designs[design.id] = design
        self._check_cycles()
        self._cache = {}
        self._tbuilds = OrderedDict()

    def edit(self, design_id: str, document=None, patch=None, settings=None):
        d = self.get(design_id)
        if document is not None:
            d.document = document
        if patch is not None:
            d.patch = patch
        if settings is not None:
            d.settings = settings
        self._cache = {}
        self._tbuilds = OrderedDict()

    def set_document(self, design_id: str, document: dict) -> dict:
        """Store a design edited in the Designer. A base design takes the
        document; a derived design stores its delta against the LIVE
        parent — except LATTICE DEFINITION edits (pattern, params,
        variation) of an inherited wall infill, which go to the infill's
        OWNER (the design that introduced it), so every design of the
        lineage gets the same lattice (ONE authoritative definition — no
        copies, no circular mutation). Returns {owner id: [infill ids]}
        of the redirected edits."""
        d = self.get(design_id)
        if d.parent is None:
            self.edit(design_id, document=document)
            return {}
        patch, settings = derive_delta(self.document(d.parent), document)
        moved: dict = {}
        inherited = {r.get('id') for r in self.document(d.parent).get('infills') or []}
        for rid, ch in list((patch.get('infills') or {}).items()):
            if rid not in inherited or not isinstance(ch, dict):
                continue
            fields = {k: ch.pop(k) for k in LATTICE_DEFINITION if k in ch}
            if not ch:
                del patch['infills'][rid]
            if fields:
                owner = self.owner(d.parent, rid)
                self._define_lattice(owner, rid, fields)
                moved.setdefault(owner, []).append(rid)
        if 'infills' in patch and not patch['infills']:
            del patch['infills']
        self.edit(design_id, patch=patch, settings=settings)
        return moved

    def owner(self, design_id: str, inf_id: str) -> str:
        """The design that introduced an infill record (its lattice
        definition lives there): walk up while the parent has it."""
        g = design_id
        while self.get(g).parent is not None and any(
                r.get('id') == inf_id for r in self.document(self.get(g).parent).get('infills') or []):
            g = self.get(g).parent
        return g

    def _define_lattice(self, owner: str, inf_id: str, fields: dict):
        o = self.get(owner)
        if o.parent is None:
            for r in o.document.get('infills') or []:
                if r.get('id') == inf_id:
                    r.update(copy.deepcopy(fields))
        else:
            rec = (o.patch.get('infills') or {}).get(inf_id)
            if isinstance(rec, dict):
                rec.update(copy.deepcopy(fields))
        self._cache = {}
        self._tbuilds = OrderedDict()

    def lattice_conflicts(self, design_id: str) -> list:
        """Wall infills of this design whose lattice is NOT the one its
        lineage root prints on the same wall (a descendant introduced its
        own infill record there): their lattices are not vertically
        registered — reported, never silently stacked."""
        root = self.lineage(design_id)[-1]
        if root == design_id:
            return []
        rdoc = self.document(root)
        mine = {r.get('path_id'): r for r in self.document(design_id).get('infills') or []
                if (r.get('kind') or 'wall') == 'wall'}
        out = []
        for r in rdoc.get('infills') or []:
            if (r.get('kind') or 'wall') != 'wall':
                continue
            m = mine.get(r.get('path_id'))
            if m is not None and m.get('id') != r.get('id'):
                out.append({'path_id': r.get('path_id'), 'infill': m.get('id'),
                            'owner': self.owner(design_id, m.get('id')),
                            'lineage_infill': r.get('id'), 'lineage_owner': root})
        return out

    # -- queries --------------------------------------------------------------
    def get(self, design_id: str) -> LayerDesign:
        if design_id not in self.designs:
            raise LayerDesignError(f'unknown layer design {design_id!r}')
        return self.designs[design_id]

    def lineage(self, design_id: str) -> list[str]:
        """[design, parent, grandparent, …, base]"""
        out = []
        cur = design_id
        while cur is not None:
            out.append(cur)
            cur = self.get(cur).parent
        return out

    def _check_cycles(self):
        for did in self.designs:
            seen = set()
            cur = did
            while cur is not None:
                if cur in seen:
                    raise LayerDesignError(f'derivation cycle through {cur!r}')
                seen.add(cur)
                d = self.designs.get(cur)
                if d is None:
                    raise LayerDesignError(f'{did!r} derives from unknown {cur!r}')
                cur = d.parent

    def document(self, design_id: str) -> dict:
        """The resolved Designer document of a design (inheritance applied)."""
        key = ('doc', design_id)
        if key not in self._cache:
            d = self.get(design_id)
            if d.parent is None:
                doc = copy.deepcopy(d.document)
            else:
                parent = self.document(d.parent)
                doc = apply_derivation(parent, _without_lattice_overrides(parent, d.patch), d.settings)
            self._cache[key] = doc
        return copy.deepcopy(self._cache[key])

    # -- lattice families (one shared scaffold per family) ----------------------
    @staticmethod
    def _identity(f):
        return json.dumps([f.get(k) for k in _INFILL_IDENTITY], sort_keys=True)

    def _wall_infill(self, design_id, inf_id):
        return next((f for f in self.document(design_id).get('infills', [])
                     if f.get('id') == inf_id and (f.get('kind') or 'wall') == 'wall'), None)

    def generator(self, design_id, inf_id):
        """The design where this infill was last defined: walk up while the
        parent has the same infill unchanged."""
        g = design_id
        f = self._wall_infill(g, inf_id)
        while f is not None and self.get(g).parent is not None:
            pf = self._wall_infill(self.get(g).parent, inf_id)
            if pf is None or self._identity(pf) != self._identity(f):
                break
            g = self.get(g).parent
        return g

    def family(self, design_id, inf_id) -> list:
        """The lattice family of a design's infill: its generator and every
        design inheriting that infill from it unchanged."""
        g = self.generator(design_id, inf_id)
        return [d for d in self.designs
                if self._wall_infill(d, inf_id) is not None and self.generator(d, inf_id) == g]

    def _caps(self, doc) -> set:
        """End walls (caps) of a document's walls: {((x, y), (x, y))}."""
        def make():
            from app import _deserialise_layer
            d = dict(doc, infills=[])
            paths, _ = _deserialise_layer(d)._build_effective()
            out = set()
            for p in paths:
                # END walls only: a wall system's caps and doorway cut faces.
                # Rounded-junction arcs / network joins are 'cap' beads too,
                # but they are junction geometry, never jamb lines.
                if getattr(p, 'role', '') == 'cap' and getattr(p, 'label', '') in END_WALL_LABELS:
                    pts = p.sample_points()
                    a = (round(pts[0].x, 6), round(pts[0].y, 6))
                    b = (round(pts[-1].x, 6), round(pts[-1].y, 6))
                    out.add((min(a, b), max(a, b)))
            return frozenset(out)
        return _shared(('caps', json.dumps(doc, sort_keys=True)), make)

    def transformed(self, design_id, transforms=None) -> tuple:
        """(document, report) of a design with SEMANTIC transforms applied
        to its source geometry (semantic_transform.py); the identity /
        None gives the plain document."""
        return self._transformed_doc(self.document(design_id), transforms)

    @staticmethod
    def _transformed_doc(doc, transforms):
        import semantic_transform as ST
        if not transforms or ST.spec_key(transforms) == '[]':
            return doc, {'connectors': [], 'moved': []}
        dj = json.dumps(doc, sort_keys=True)
        atts = _shared(('atts', dj), lambda: ST.attachments(doc))
        return _shared(('tdoc', dj, ST.spec_key(transforms)),
                       lambda: ST.transform_document(doc, transforms, atts))

    def scaffold(self, design_id, transforms=None) -> dict:
        """{infill id: {'polylines', 'stations', 'members', 'generator'}} for
        every wall infill of the design whose family has several members."""
        out = {}
        for f in self.document(design_id).get('infills', []):
            if (f.get('kind') or 'wall') != 'wall':
                continue
            fam = self.family(design_id, f['id'])
            if len(fam) < 2:
                continue
            g = self.generator(design_id, f['id'])
            sdoc = self._transformed_doc(dict(self.document(g), openings=[]), transforms)[0]
            base = self._caps(sdoc)
            lines = sorted(set().union(*(self._caps(self.transformed(m, transforms)[0]) - base
                                         for m in fam)))

            doc0 = dict(self.document(g), openings=[])
            tracking = None
            if sdoc is not doc0 and _transforms_active(transforms):
                # CROSS-Z: the transformed scaffold keeps the reference's
                # (untransformed scaffold's) stations — counts, sides, passes,
                # seam — changing them only where physically impossible
                ref = self.scaffold(design_id, None).get(f['id'])
                if ref:
                    tracking = (_merged_track(ref['report']), _track_map(doc0, transforms))

            def make(sdoc=sdoc, lines=lines, inf=f['id'], tracking=tracking):
                from app import _deserialise_layer
                from model import Vec2
                lay = _deserialise_layer(sdoc)
                lay.lattice_stations = {inf: [(Vec2(*a), Vec2(*b)) for a, b in lines]}
                if tracking:
                    lay.lattice_track = {inf: tracking}
                paths, meta = lay._build_effective()
                polys = [p.sample_points() for p in paths
                         if getattr(p, 'treatment_id', '') == 'infill' and p.source_id == inf]
                regs = ((meta.get('network') or {}).get('lattice') or {}).get(inf, {}).get('regions', [])
                return polys, [{k: r.get(k) for k in ('runs', 'jambs', 'pitch_min', 'pitch_max',
                                                      'max_unsupported', 'max_unsupported_limit', 'track')}
                               for r in regs]
            polys, reports = _shared(('scaffold', json.dumps(sdoc, sort_keys=True), f['id'], tuple(lines),
                                      json.dumps(doc0, sort_keys=True) if tracking else ''), make)
            out[f['id']] = {'polylines': polys, 'stations': lines, 'members': sorted(fam), 'generator': g,
                            'report': reports}
        return out

    def lattice_lineage(self, design_id, transforms=None) -> dict:
        """Diagnostics of the shared lattice, per wall infill: its owner,
        family, jamb crossings (resolved / unresolved — an unresolved jamb
        means that member's route cannot close there) and conflicts."""
        out = {}
        for inf, sc in self.scaffold(design_id, transforms).items():
            jambs = {'resolved': 0, 'unresolved': [], 'bend_failed': 0}
            matched, done = set(), set()
            for r in sc.get('report') or []:
                j = r.get('jambs') or {}
                done |= {tuple(x) for x in j.get('lines') or []}
                jambs['resolved'] = len(done)
                jambs['unresolved'] += j.get('unresolved', [])
                jambs['bend_failed'] += j.get('bend_failed', 0)
                matched |= {tuple(m) for m in j.get('matched') or []}
            for (a, b) in sc['stations']:            # crossing no wall run (junction, wall end …)
                if (a[0], a[1], b[0], b[1]) not in matched:
                    jambs['unresolved'].append({'line': [list(a), list(b)],
                                                'why': 'not across a wall run (junction / wall end)'})
            out[inf] = {'owner': self.owner(design_id, inf), 'generator': sc['generator'],
                        'members': sc['members'], 'jamb_lines': len(sc['stations']),
                        'jambs': jambs, 'doubled_runs': sum(1 for r in sc.get('report') or []
                                                             for run in r.get('runs') or []
                                                             if run.get('passes') == 2),
                        'variation_effective': any(run.get('motif') in ('loop', 'lone')
                                                   for r in sc.get('report') or []
                                                   for run in r.get('runs') or [])}
        v = self.scaffold_verdict(design_id, transforms) if out else {'closes': True}
        if not v['closes']:
            for inf in out:
                out[inf]['planned_alone'] = True
                out[inf]['jambs']['unresolved'].append(
                    {'why': 'the shared lattice cannot close this design here — its lattice is planned '
                            'on its own (closed route kept; vertical registration with the lineage lost)',
                     'at': v['at']})
        conf = self.lattice_conflicts(design_id)
        if conf:
            out['_conflicts'] = conf
        return out

    @staticmethod
    def _open_ends(layer, paths, meta) -> list:
        """Odd (open) route ends of a resolved layer under physical rules:
        [[x, y] …] — [] when every connected piece routes closed."""
        import sys as _s
        tp = os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto')
        if tp not in _s.path:
            _s.path.insert(0, tp)
        from graph import build_graph, closure_report
        if not layer.material.physical:
            return []
        rep = closure_report(build_graph(layer.to_routing_layer(paths, meta)))
        return [[round(v, 3) for v in q] for o in rep.get('open') or [] for q in o.get('at', [])]

    def scaffold_verdict(self, design_id, transforms=None) -> dict:
        """Does this member CLOSE printing the shared scaffold clipped to its
        material? {'closes': bool, 'at': open ends}. The catch-all for what
        the local jamb motifs do not cover: an opening reaching into another
        wall's material or a junction's rounding (a member's material there
        is not Base minus the opening — the junction is rebuilt), or one
        wrapping a corner. A CLOSED ROUTE outranks vertical registration
        (lineage priority order), so a member that cannot close is planned
        ON ITS OWN instead (lattice_reference → none; reported in
        lattice_lineage as 'planned_alone')."""
        import semantic_transform as ST
        key = ('verdict', design_id, ST.spec_key(transforms) if transforms else '[]')
        if key in self._cache:
            return self._cache[key]
        out = {'closes': True, 'at': []}
        sc = self.scaffold(design_id, transforms)
        if sc:
            from app import _deserialise_layer
            layer = _deserialise_layer(self.transformed(design_id, transforms)[0])
            layer.lattice_reference = {k: v['polylines'] for k, v in sc.items()}
            paths, meta = layer._build_effective()
            ends = self._open_ends(layer, paths, meta)
            out = {'closes': not ends, 'at': ends}
            if not ends:
                out['_built'] = (layer, paths, meta)     # build() reuses it (one resolve)
        self._cache[key] = out
        return out

    def lattice_reference(self, design_id, transforms=None) -> dict:
        """What model.PrintLayer.lattice_reference gets for this design: the
        shared scaffold — or nothing when the member cannot close with it
        (it is then planned on its own; see scaffold_verdict)."""
        sc = self.scaffold(design_id, transforms)
        if not sc or not self.scaffold_verdict(design_id, transforms)['closes']:
            return {}
        return {k: v['polylines'] for k, v in sc.items()}

    # -- building (the Designer) ---------------------------------------------------
    def build(self, design_id: str, inherit_lattice: bool = True, transforms=None) -> BuiltLayer:
        """Resolve and build a design with the Designer. Wall infills shared
        by a lattice family print the family scaffold (see module doc).
        transforms: SEMANTIC transforms {source id: (k, tx, ty)} applied to
        the design's source geometry BEFORE resolution (Layer Assembly
        transform groups; semantic_transform.py) — walls, junctions,
        openings and the lineage scaffold are all resolved for the
        transformed architecture (the scaffold with the same transforms)."""
        import semantic_transform as ST
        tkey = ST.spec_key(transforms) if transforms else '[]'
        key = ('build', design_id, inherit_lattice, tkey)
        if tkey != '[]':
            hit = self._tbuilds.get(key)
            if hit is not None:
                self._tbuilds.move_to_end(key)
                return hit
        elif key in self._cache:
            return self._cache[key]
        from app import _deserialise_layer
        import time as _t
        tm = {}
        t0 = _t.perf_counter()
        doc, trep = self.transformed(design_id, transforms)
        tm['semantic_transform'] = _t.perf_counter() - t0
        layer = _deserialise_layer(doc)
        t0 = _t.perf_counter()
        if inherit_lattice:
            self.scaffold(design_id, transforms)
        tm['lineage_scaffold'] = _t.perf_counter() - t0
        t0 = _t.perf_counter()
        if inherit_lattice:
            # (scaffold_verdict resolves the member with the clipped scaffold
            # and checks closure; that resolve is reused below)
            layer.lattice_reference = self.lattice_reference(design_id, transforms) or None
            layer.lattice_lineage = self.lattice_lineage(design_id, transforms) or None
        tm['scaffold_resolve_closure_check'] = _t.perf_counter() - t0
        t0 = _t.perf_counter()
        if tkey != '[]' and not layer.lattice_reference:
            # a design planned on its own: its transformed lattice keeps the
            # untransformed plan's stations (cross-Z correspondence)
            ref = self.build(design_id, inherit_lattice)
            tmap = _track_map(self.document(design_id), transforms)
            layer.lattice_track = {inf: (_merged_track(info.get('regions') or []), tmap)
                                   for inf, info in (ref.network.get('lattice') or {}).items()
                                   if any(r_.get('track') for r_ in info.get('regions') or [])}
        tm['tracking_reference'] = _t.perf_counter() - t0
        t0 = _t.perf_counter()
        pre = self.scaffold_verdict(design_id, transforms).pop('_built', None) \
            if inherit_lattice and layer.lattice_reference else None
        if pre is not None:                  # resolved by the closure check already
            lineage = layer.lattice_lineage
            layer, paths, meta = pre
            layer.lattice_lineage = lineage
            for inf, info in (meta['network'].get('lattice') or {}).items():
                info['lineage'] = (lineage or {}).get(inf)
            for info in meta['network'].get('infills') or []:
                if isinstance(info.get('lattice'), dict) and 'lineage' in info['lattice']:
                    info['lattice']['lineage'] = (lineage or {}).get(info['id'])
        else:
            paths, meta = layer._build_effective()
        tm['resolve_walls_lattice'] = _t.perf_counter() - t0
        lattice: dict = {}
        for p in paths:
            if getattr(p, 'treatment_id', '') == 'infill':
                lattice.setdefault(p.source_id, []).append(p.sample_points())
        source = dict(lattice)
        source.update(layer.lattice_reference or {})          # shared: the scaffold, unclipped
        src_ids = {sp.get('id') for sp in doc.get('source_paths') or []}
        strand_sources = {}
        for p in paths:                      # faces / caps → their source; a bare source path → itself
            sid = getattr(p, 'source_id', None) or (p.id if p.id in src_ids else None)
            if getattr(p, 'treatment_id', '') != 'infill' and sid in src_ids:
                strand_sources[p.id] = sid
        t0 = _t.perf_counter()
        built = BuiltLayer(design_id, doc, layer.printable_centerlines(paths, meta),
                           lattice, meta['network'], source, strand_sources, trep)
        tm['printable'] = _t.perf_counter() - t0
        built.timing = {k: round(v, 4) for k, v in tm.items()}
        if tkey != '[]':
            self._tbuilds[key] = built
            while len(self._tbuilds) > 256:
                self._tbuilds.popitem(last=False)
        else:
            self._cache[key] = built
        return built
