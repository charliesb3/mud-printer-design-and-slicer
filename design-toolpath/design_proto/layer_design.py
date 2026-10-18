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


PROBE_WIDTH = 0.5     # in: the narrow cut that probes / places an Assembly opening's lattice STATION


def _cut_key(cuts) -> str:
    """'' without ASSEMBLY PROFILE cuts (the plain / transform-only keys
    are then exactly as before)."""
    if not cuts:
        return ''
    import profile_cuts as PC
    return '|cut:' + PC.cut_key(cuts)


def _transforms_active(transforms) -> bool:
    import semantic_transform as ST
    return bool(transforms) and ST.spec_key(transforms) != '[]'


def _merged_track(reports, pool=None) -> dict:
    """The station tracks of a reference plan's regions, as one list —
    plus the POOL of deposited web stations carried to the new layer
    (wall_lattice §11): `pool` (reports) or these same runs."""
    runs = [run for r in reports or [] for run in ((r or {}).get('track') or {}).get('runs', [])]
    return {'runs': runs, 'pool': runs if pool is None else
            [run for r in pool or [] for run in ((r or {}).get('track') or {}).get('runs', [])]}


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
    spans = {sid: ST.straight_spans(pts) for sid, pts in lines.items()}
    grids, memo = {}, {}

    def host(p):
        # DEPOSITED CONSTRUCTION follows its host's motion only (carried
        # web stations; wall_lattice module doc §11) — never the full map
        # of a wall whose length changed
        k = (round(p.x, 9), round(p.y, 9))
        if k not in memo:
            memo[k] = ST.map_host_point(maps, lines, owner, (p.x, p.y), spans, grids)
        q = memo[k]
        return Vec2(q[0], q[1])
    f.host = host
    _TRACK_MAPS[key] = f
    while len(_TRACK_MAPS) > 64:
        _TRACK_MAPS.popitem(last=False)
    return f


# Collections of id-keyed records in a Designer document (key field).
KEYED = {
    'source_paths': 'id', 'offset_treatments': 'id', 'lattice_instances': 'id',
    'openings': 'id', 'trims': 'id', 'region_overrides': 'id', 'infills': 'id',
    'network_walls': 'id', 'wall_systems': 'id', 'wall_groups': 'id', 'wall_relations': 'id',
    'junction_overrides': 'key',
    'route_origins': 'strand',
}
MERGED_SETTINGS = ('material', 'constraints')
# PERSISTENT WALL PROPERTIES (M1, 2026-10-15 — WALL_ARCHITECTURE.md): owned by
# the lineage root (document-level) / the design that introduced the record —
# never by a layout-state Variant. The Designer writes edits through to the
# owner; a Variant that STORES one (older projects) is reported (divergences).
OWNED_DOC_SETTINGS = ('junction_style', 'junction_radius', 'cap_style', 'cap_corner_radius', 'corner_radius', 'material')
OWNED_RECORD_FIELDS = {'source_paths': ('envelope', 'wall', 'corner_radius', 'cap'),
                       'wall_groups': ('name', 'thickness', 'align', 'print_reference')}


def divergences(designs) -> list:
    """Every persistent wall / material property a DERIVED design stores
    (Layer Design exchange dicts): [{design, name, kind, field, record?, value}].
    kind 'setting' (document-level), 'junction_override', 'record' (an owned
    field of an inherited record). New records and removals (layout state) are
    not divergences."""
    out = []
    for d in designs or []:
        if d.get('parent') is None:
            continue
        did, name = d.get('id'), d.get('name') or d.get('id')
        st, patch = d.get('settings') or {}, d.get('patch') or {}
        for f in OWNED_DOC_SETTINGS:
            if f not in st:
                continue
            if f == 'material' and isinstance(st[f], dict):
                for k, v in st[f].items():
                    out.append({'design': did, 'name': name, 'kind': 'setting', 'field': f'material.{k}', 'value': v})
            else:
                out.append({'design': did, 'name': name, 'kind': 'setting', 'field': f, 'value': st[f]})
        for key, v in (patch.get('junction_overrides') or {}).items():
            out.append({'design': did, 'name': name, 'kind': 'junction_override', 'field': key, 'value': v})
        for coll, fields in OWNED_RECORD_FIELDS.items():
            for rid, r in (patch.get(coll) or {}).items():
                if not isinstance(r, dict) or r.get('id') == rid:
                    continue                      # (removed / introduced here: layout state)
                for f in fields:
                    if f in r:
                        out.append({'design': did, 'name': name, 'kind': 'record', 'record': rid,
                                    'field': f, 'value': r[f]})
    return out


def describe_divergences(divs) -> list:
    """One readable line per design."""
    by = {}
    for x in divs:
        f = x['field'] if x['kind'] != 'record' else f"{x['record']} {x['field']}"
        if x['kind'] == 'junction_override':
            f = 'junction override'
        by.setdefault(x['name'], []).append(f'{f} = {x["value"]!r}' if x['kind'] == 'setting' else f)
    return [f'{n} overrides persistent wall / material properties ({", ".join(fs)}).' for n, fs in by.items()]
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
    ws_frames: list = field(default_factory=list)        # generated Wall System frames (cut-variant phase reference)
    routing: dict = field(default_factory=dict)          # how the Designer routes `printable` (layer_route.py)
    timing: dict = field(default_factory=dict)           # seconds per build stage (instrumentation)
    envelope_rings: list = field(default_factory=list)   # the material rings its Wall Systems / lattice
                                                         # filled (= DesignLibrary.envelope's rings)


def _unopened(cuts):
    """The REFERENCE for a layer that re-places its own Designer openings
    (Assembly plumb alignment, cut op 'move'): the same design with those
    openings removed ('unopen'); None when nothing moves."""
    moves = [c for c in cuts or [] if c.get('op') == 'move']
    if not moves:
        return None
    return [{'op': 'unopen', 'opening': c.get('opening'), 'profile': c.get('profile', '')} for c in moves]


class DesignLibrary:
    """The set of Layer Designs of one project, resolved on demand.
    Resolution and builds are cached; any edit invalidates the cache."""

    def __init__(self, designs=(), wall_systems=None, arch=False):
        """wall_systems: the PROJECT's Wall Systems (wall_groups.py) — given
        (a list), Wall Group documents are composed into the build format
        with them; `arch`: composed as ARCHITECTURE only (envelopes). Neither:
        documents are served as authored (the Designer's editing endpoints,
        legacy documents)."""
        self.designs: dict[str, LayerDesign] = {}
        self._cache, self._tbuilds = {}, OrderedDict()
        self.wall_systems = list(wall_systems or [])
        self.arch = bool(arch)
        self._compose = wall_systems is not None or self.arch
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
        patch, settings = derive_delta(self.raw_document(d.parent), document)
        moved: dict = {}
        inherited = {r.get('id') for r in self.raw_document(d.parent).get('infills') or []}
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
                r.get('id') == inf_id for r in self.raw_document(self.get(g).parent).get('infills') or []):
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

    def raw_document(self, design_id: str) -> dict:
        """The resolved Designer document of a design (inheritance applied),
        as authored (Wall Groups not composed)."""
        key = ('raw', design_id)
        if key not in self._cache:
            d = self.get(design_id)
            if d.parent is None:
                doc = copy.deepcopy(d.document)
            else:
                parent = self.raw_document(d.parent)
                doc = apply_derivation(parent, _without_lattice_overrides(parent, d.patch), d.settings)
            self._cache[key] = doc
        return copy.deepcopy(self._cache[key])

    def document(self, design_id: str) -> dict:
        """The resolved document a build uses: the authored one, or — in a
        composing library — its Wall Groups composed with the project's
        Wall Systems (or as architecture only) into the build format
        (wall_groups.compose)."""
        if not self._compose:
            return self.raw_document(design_id)
        key = ('doc', design_id)
        if key not in self._cache:
            from walls import compose
            self._cache[key] = compose(self.raw_document(design_id), design_id, self.wall_systems, self.arch)
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

    def transformed(self, design_id, transforms=None, cuts=None) -> tuple:
        """(document, report) of a design with SEMANTIC transforms applied
        to its source geometry (semantic_transform.py); the identity /
        None gives the plain document. cuts: ASSEMBLY PROFILE cuts
        (profile_cuts.py), applied FIRST — to the untransformed sources —
        so the transform treats the resulting openings / walls like any
        Designer geometry; the report then carries `profile`."""
        doc = self.document(design_id)
        if not cuts:
            return self._transformed_doc(doc, transforms)
        import profile_cuts as PC
        cdoc, crep = _shared(('cuts', json.dumps(doc, sort_keys=True), PC.cut_key(cuts)),
                             lambda: PC.apply_cuts(doc, cuts))
        tdoc, trep = self._transformed_doc(cdoc, transforms)
        return tdoc, dict(trep, profile=crep)

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

    def lattice_lineage(self, design_id, transforms=None, cuts=None) -> dict:
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
                        'variation_effective': any(run.get('phase_free', run.get('motif') in ('loop', 'lone'))
                                                   for r in sc.get('report') or []
                                                   for run in r.get('runs') or [])}
        v = self.scaffold_verdict(design_id, transforms, cuts) if out else {'closes': True}
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

    def scaffold_verdict(self, design_id, transforms=None, cuts=None) -> dict:
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
        key = ('verdict', design_id, ST.spec_key(transforms) if transforms else '[]', _cut_key(cuts))
        if key in self._cache:
            return self._cache[key]
        out = {'closes': True, 'at': []}
        sc = self.scaffold(design_id, transforms)
        if sc:
            from app import _deserialise_layer
            layer = _deserialise_layer(self.transformed(design_id, transforms, cuts)[0])
            layer.lattice_reference = {k: v['polylines'] for k, v in sc.items()}
            paths, meta = layer._build_effective()
            ends = self._open_ends(layer, paths, meta)
            out = {'closes': not ends, 'at': ends}
            if not ends:
                out['_built'] = (layer, paths, meta)     # build() reuses it (one resolve)
        self._cache[key] = out
        return out

    def lattice_reference(self, design_id, transforms=None, cuts=None) -> dict:
        """What model.PrintLayer.lattice_reference gets for this design: the
        shared scaffold — or nothing when the member cannot close with it
        (it is then planned on its own; see scaffold_verdict)."""
        sc = self.scaffold(design_id, transforms)
        if not sc or not self.scaffold_verdict(design_id, transforms, cuts)['closes']:
            return {}
        return {k: v['polylines'] for k, v in sc.items()}

    # -- building (the Designer) ---------------------------------------------------
    def build(self, design_id: str, inherit_lattice: bool = True, transforms=None,
              cuts=None) -> BuiltLayer:
        """Resolve and build a design with the Designer. Wall infills shared
        by a lattice family print the family scaffold (see module doc).
        transforms: SEMANTIC transforms {source id: (k, tx, ty)} applied to
        the design's source geometry BEFORE resolution (Layer Assembly
        transform groups; semantic_transform.py) — walls, junctions,
        openings and the lineage scaffold are all resolved for the
        transformed architecture (the scaffold with the same transforms).
        cuts: ASSEMBLY PROFILE cuts (profile_cuts.py) applied to the
        document before the transforms — a profiled layer is a VARIANT like
        a transformed one (cached in _tbuilds). The lineage scaffold stays
        the family's (planned from the real designs); a profiled member that
        cannot close with it is planned on its own (scaffold_verdict)."""
        import semantic_transform as ST
        ckey = _cut_key(cuts)
        tkey = (ST.spec_key(transforms) if transforms else '[]') + ckey
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
        doc, trep = self.transformed(design_id, transforms, cuts)
        tm['semantic_transform'] = _t.perf_counter() - t0
        layer = _deserialise_layer(doc)
        if any(c.get('op') in ('opening', 'extent', 'plane', 'move') for c in cuts or []):
            # generated Wall Systems keep the UNCUT layer's pattern (period,
            # phase, lanes, loop stations) and solve only their new ends
            layer.wall_system_reference = self.build(design_id, inherit_lattice, transforms,
                                                     _unopened(cuts)).ws_frames or None
        t0 = _t.perf_counter()
        joined = self._cut_join(design_id, transforms, cuts, doc) if cuts and inherit_lattice else None
        if joined is not None:
            # ASSEMBLY PROFILE cut: the UNCUT layer's lattice, clipped at the
            # new end walls and joined there — local (see _cut_join)
            layer, paths, meta = joined
            tm['cut_join'] = _t.perf_counter() - t0
            return self._finish(design_id, doc, trep, layer, paths, meta, tm, key, tkey)
        t0 = _t.perf_counter()
        if inherit_lattice:
            self.scaffold(design_id, transforms)
        tm['lineage_scaffold'] = _t.perf_counter() - t0
        t0 = _t.perf_counter()
        if inherit_lattice:
            # (scaffold_verdict resolves the member with the clipped scaffold
            # and checks closure; that resolve is reused below)
            layer.lattice_reference = self.lattice_reference(design_id, transforms, cuts) or None
            layer.lattice_lineage = self.lattice_lineage(design_id, transforms, cuts) or None
        tm['scaffold_resolve_closure_check'] = _t.perf_counter() - t0
        t0 = _t.perf_counter()
        if tkey != '[]' and not layer.lattice_reference:
            # a design planned on its own: its transformed lattice keeps the
            # untransformed plan's stations (cross-Z correspondence)
            ref = self.build(design_id, inherit_lattice)
            tmap = _track_map(self.document(design_id), transforms) if _transforms_active(transforms) \
                else (lambda p: p)
            lat = ref.network.get('lattice') or {}
            # every web of this layer carries the WHOLE deposited construction
            # below (one pool): a wall whose web was another infill's below (a
            # domain that split) keeps it — physical ancestry, not infill ids
            pool = [r_ for info in lat.values() for r_ in info.get('regions') or []]
            ids = set(lat) | {f.get('id') for f in doc.get('infills') or [] if (f.get('kind') or 'wall') == 'wall'}
            layer.lattice_track = {inf: (_merged_track((lat.get(inf) or {}).get('regions') or [], pool), tmap)
                                   for inf in ids
                                   if any(r_.get('track') for r_ in pool)}
        tm['tracking_reference'] = _t.perf_counter() - t0
        t0 = _t.perf_counter()
        pre = self.scaffold_verdict(design_id, transforms, cuts).pop('_built', None) \
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
        return self._finish(design_id, doc, trep, layer, paths, meta, tm, key, tkey)

    def envelope(self, design_id: str, transforms=None, cuts=None):
        """The ARCHITECTURAL ENVELOPE of a design or of its variant (the same
        `transforms` / `cuts` as build): the document resolved exactly as
        build() resolves it (cuts, then the semantic transform), built up to
        the wall networks only — no Wall Systems, lattice, scaffold, station
        reference, closure verdict or routing (envelope.py). Lattice-only cut
        ops (stations, transitions) are ignored: they change no architecture.
        Cached per (design, transform spec, architectural cut key) until the
        library is edited."""
        import semantic_transform as ST
        from envelope import architectural_cuts, from_stage
        cuts = architectural_cuts(cuts)
        key = (design_id, ST.spec_key(transforms) if transforms else '[]', _cut_key(cuts))
        env = getattr(self, '_env', None)
        if env is None or env[0] is not self._cache:          # (a library edit replaces _cache)
            env = self._env = (self._cache, OrderedDict())
        hit = env[1].get(key)
        if hit is not None:
            env[1].move_to_end(key)
            return hit
        from app import _deserialise_layer
        doc, trep = self.transformed(design_id, transforms, cuts)
        layer = _deserialise_layer(doc)
        layer.envelope_only = True
        result, meta = layer._build_effective()
        out = from_stage(design_id, doc, trep, layer, result, meta)
        env[1][key] = out
        while len(env[1]) > 1024:
            env[1].popitem(last=False)
        return out

    def _cut_join(self, design_id, transforms, cuts, doc):
        """(layer, paths, meta) of an ASSEMBLY PROFILE cut variant printing
        the REFERENCE lattice of the same layer without the cuts, clipped to
        the cut material, the strands each new end wall cuts joined pairwise
        on it (model._join_cut_ends) — None when there is no wall lattice or
        that route cannot close (the caller then plans the cut layer as
        before: lineage scaffold / tracked re-plan).
        Why: a cut changes the length of the wall runs, and a re-plan
        re-fits the stitches of the WHOLE run (they moved by up to half a
        pitch far from the opening and the layer no longer stacked on its
        neighbours). The reference clipped stays identical everywhere except
        within one end-wall length of each cut.
        The reference: the uncut layer's own lattice (its plan, or the
        lineage scaffold it prints) — or, when the cut spec carries STATION
        ops (an Assembly opening on a wall of this design somewhere in the
        stack), the station reference (_station_reference): every layer of
        the stack prints the same one, so the layers stack."""
        walls = {f.get('id') for f in doc.get('infills') or [] if (f.get('kind') or 'wall') == 'wall'}
        if not walls or all(c.get('op') == 'unopen' for c in cuts or []):
            return None                     # (a design with Designer openings REMOVED: planned as a layer)
        # a layer whose OWN Designer openings are re-placed (plumb alignment:
        # 'move') clips the reference of the same design WITHOUT those
        # openings — the authored hole is not left behind in the lattice
        base_cuts = _unopened(cuts)
        stations = [c for c in cuts or [] if c.get('op') == 'station']
        if stations:
            src = self._station_reference(design_id, transforms, stations, base_cuts)
        else:
            src = self.build(design_id, True, transforms, base_cuts).lattice_source or {}
        # (a web cloned onto a sibling piece of a cut ring — `<id>~p<k>` —
        # clips the same reference lattice as the web it was cloned from)
        src = {w: src.get(w, src.get(w.split('~p')[0])) for w in walls}
        src = {k: v for k, v in src.items() if v}
        if not src:
            return None
        from app import _deserialise_layer
        layer = _deserialise_layer(doc)
        layer.lattice_reference = src
        layer.lattice_join = True
        layer.wall_system_reference = self.build(design_id, True, transforms, base_cuts).ws_frames or None
        paths, meta = layer._build_effective()
        if self._open_ends(layer, paths, meta):
            return None
        return layer, paths, meta

    def _station_reference(self, design_id, transforms, stations, base_cuts=None) -> dict:
        """{infill id: polylines} — the reference lattice of a design for a
        stack in which Assembly openings cut its walls (STATION ops: source,
        edge, at). A run printed by ONE pass (a ring with no junction, a run
        between junctions) cannot be clipped and closed locally: an opening
        cuts that single strand once per cut face (an odd end each — parity).
        As for a Door Gap derived design (lineage scaffold), the run must
        carry TWO mirrored passes on EVERY layer of the stack, cut layers and
        uncut layers alike, so they stack. So: probe each station with a
        narrow cut; if the uncut reference already clips and closes there
        (doubled / lone-loop runs) it is kept unchanged; otherwise the
        reference is re-planned with lineage JAMB STATIONS at the probes'
        cut faces (+ the family's own lines), tracked against the uncut
        plan (counts / sides kept where possible)."""
        import profile_cuts as PC
        import semantic_transform as ST
        key = ('stations', design_id, ST.spec_key(transforms) if transforms else '[]', PC.cut_key(stations),
               PC.cut_key(base_cuts or []))
        if key in self._cache:
            return self._cache[key]
        base = self.build(design_id, True, transforms, base_cuts)
        ref0 = dict(base.lattice_source or {})
        out = ref0
        doc0 = self.transformed(design_id, transforms, base_cuts)[0]
        probes = []
        for c in stations:
            if c.get('plane') is not None:            # a PLANAR cut's first cut: probes at its removed stretches
                probes += self._plane_probes_reaching(design_id, transforms, base_cuts, doc0, c['plane'], ref0)
            else:
                probes.append(dict(op='opening', source=c['source'], edge=c.get('edge'), at=c['at'],
                                   width=PROBE_WIDTH, profile='probe'))
        bad = False
        for pr in probes:
            pdoc = self.transformed(design_id, transforms, list(base_cuts or []) + [pr])[0]
            from app import _deserialise_layer
            lay = _deserialise_layer(pdoc)
            lay.lattice_reference = ref0
            lay.lattice_join = True
            paths, meta = lay._build_effective()
            if self._open_ends(lay, paths, meta):
                bad = True
                break
        if bad and ref0:
            pdoc = self.transformed(design_id, transforms, list(base_cuts or []) + probes)[0]
            extra = self._caps(pdoc) - self._caps(doc0)
            sc = self.scaffold(design_id, transforms)
            out = {}
            for inf in ref0:
                fam = sc.get(inf)
                if fam:
                    g = fam['generator']
                    sdoc = self._transformed_doc(dict(self.document(g), openings=[]), transforms)[0]
                    lines = sorted(set(map(tuple, fam['stations'])) | extra)
                    rep = fam.get('report')
                else:
                    sdoc = doc0
                    lines = sorted(extra)
                    rep = ((base.network.get('lattice') or {}).get(inf) or {}).get('regions')
                tracking = (_merged_track(rep), lambda p_: p_) if rep else None

                def make(sdoc=sdoc, lines=lines, inf=inf, tracking=tracking):
                    from app import _deserialise_layer
                    from model import Vec2
                    lay = _deserialise_layer(sdoc)
                    lay.lattice_stations = {inf: [(Vec2(*a), Vec2(*b)) for a, b in lines]}
                    if tracking:
                        lay.lattice_track = {inf: tracking}
                    paths, meta = lay._build_effective()
                    return [p.sample_points() for p in paths
                            if getattr(p, 'treatment_id', '') == 'infill' and p.source_id == inf]
                out[inf] = _shared(('station_scaffold', json.dumps(sdoc, sort_keys=True), inf, tuple(lines),
                                    bool(tracking)), make)
        self._cache[key] = out
        return out

    PROBE_DEPTHS = (0.0, 1.0, 2.0, 4.0, 8.0, 16.0)   # in: how far a plane station's probe may advance

    def _plane_probes_reaching(self, design_id, transforms, base_cuts, doc0, plane, ref0) -> list:
        """The probes of a PLANAR station that actually cut the reference
        lattice. The station is the plane's FIRST cut of the wall — on a
        corner, often only the envelope's tip. Under physical wall width the
        printed band (skins, web) lies half a bead and more inside the
        envelope, so a probe there removes no printed construction and
        trivially 'closes', which says nothing about the cuts that follow
        (a single-pass web cut once per face cannot close). So: probe the same
        plane advanced along its normal (deeper into the wall) until the probe
        clips the reference lattice; that probe answers the parity question.
        (The plane removes {q: (q − P)·n < 0}: moving P by +d·n cuts deeper.)
        The deeper probes sit at the QUARTER points of each removed stretch:
        a plane across a corner removes a stretch centred on the corner
        vertex at every depth, and a probe AT the vertex cuts nothing printed.
        None reaches it within PROBE_DEPTHS → the first-cut probes, as before."""
        import profile_cuts as PC
        from app import _deserialise_layer
        first = PC.plane_probes(doc0, plane, PROBE_WIDTH)
        if not ref0:
            return first
        (px, py), (nx, ny) = plane['point'], plane['normal']
        srcs = {d_.get('id'): d_ for d_ in doc0.get('source_paths') or []}

        def quarters(pl):
            out = []
            for sid in PC.selection(doc0, pl):
                if sid not in srcs:
                    continue
                pts, closed = PC._processed(srcs[sid], doc0)
                ivs, L = PC.removed_intervals(pts, closed, pl['point'], pl['normal'])
                for a, b in ivs:
                    for f in (0.25, 0.75):
                        s = a + f * (b - a)
                        out.append({'op': 'opening', 'source': sid, 'edge': None,
                                    'center_s': s % L if closed else s, 'width': PROBE_WIDTH, 'profile': 'probe'})
            return out
        for d in self.PROBE_DEPTHS:
            ps = first if d == 0.0 else quarters(dict(plane, point=[px + d * nx, py + d * ny]))
            if not ps:
                continue
            pdoc = self.transformed(design_id, transforms, list(base_cuts or []) + ps)[0]
            lay = _deserialise_layer(pdoc)
            lay.lattice_reference = ref0
            lay.lattice_join = True
            _, meta = lay._build_effective()
            cut = any((r.get('joined') or {}).get('pairs', 0) + (r.get('joined') or {}).get('odd', 0)
                      for v in (meta['network'].get('lattice') or {}).values() for r in v.get('regions') or [])
            if cut:
                return ps
        return first

    def _finish(self, design_id, doc, trep, layer, paths, meta, tm, key, tkey) -> 'BuiltLayer':
        import time as _t
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
        import layer_route
        printable, routing = layer_route.describe(layer, paths, meta, doc, strand_sources)
        built = BuiltLayer(design_id, doc, printable,
                           lattice, meta['network'], source, strand_sources, trep,
                           ws_frames=list(getattr(layer, '_ws_frames', None) or []), routing=routing,
                           envelope_rings=meta.get('regions') or [])
        tm['printable'] = _t.perf_counter() - t0
        built.timing = {k: round(v, 4) for k, v in tm.items()}
        if tkey != '[]':
            self._tbuilds[key] = built
            while len(self._tbuilds) > 256:
                self._tbuilds.popitem(last=False)
        else:
            self._cache[key] = built
        return built
