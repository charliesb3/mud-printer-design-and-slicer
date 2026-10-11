"""
WALL GROUPS (architecture) × WALL SYSTEMS (printable construction) —
2026-10-11, the Wall Systems workspace.

AUTHORING MODEL
  * A Layer Design document holds WALL GROUPS (`doc['wall_groups']`, id-keyed,
    inherited by derived designs like every record):
        {id, name, members: [source ids], thickness, align, print_reference}
    — the architecture: which paths are walls, how thick, how aligned. The
    Designer and the Assembly see only this (envelopes).
  * The PROJECT holds WALL SYSTEMS (`project['wall_systems']`, edited in the
    Wall Systems workspace):
        {id, name, type, params, web, members: [source ids], groups: [group ids],
         designs: null | [design ids]}
    — the construction: Single / Hollow / Skin + Web / Parallel / Interleaved /
    Linked / Chained Loop + its parameters, assigned to individual walls
    (`members`) or whole Wall Groups (`groups`); `designs` restricts it to
    some Layer Designs (null = every design; migration uses it only where
    designs differed). A wall's system: the first applicable system listing
    the wall itself, else the first listing its group, else none (Hollow:
    its two skins). Different walls of one group may use different systems;
    one system may span groups of different thickness. A wall in NO group
    (2026-10-11 correction) is assignable too; its own envelope is
    `path['envelope']` (thickness, align — no thickness: a single bead).

BUILD FORMAT (unchanged pipeline)
  The Designer's model, the lineage lattice, Wall Systems generation and
  routing keep consuming the per-document `wall_systems` records they always
  did ({id, name, type, thickness, align, print_reference, params, members,
  web}); `compose` derives them from groups × systems:
    * one record per (system, thickness, align, print reference) — walls of
      one system and one envelope form ONE record, as before; unassigned
      walls one record per group (Hollow);
    * record id = the GROUP id when the record is exactly one whole group
      (migrated projects: the old Wall System id → identical builds, Skin +
      Web web ownership, planar-cut references), `<group>~<system>` for part
      of a group, the system id when it spans groups;
    * Skin + Web webs: the record's owned infill records take the system's
      web definition (pattern / params / variation); a record without one
      gets `<record>~web~<member>` per member (as the model would synthesise)
      — in the document, so the lineage lattice sees them.
  `arch=True` composes the ARCHITECTURE only (Designer / Assembly
  envelopes): every group one record of a generated type (so every wall
  resolves its material rings), no construction — the envelope build never
  generates it (`PrintLayer.envelope_only`). Documents without `wall_groups`
  (legacy, tests, the API) are returned unchanged.

MIGRATION (`split_designs`, project file schema 2): each design's resolved
legacy records → one Wall Group per record (members, thickness, align,
print reference verbatim; the old id kept — Assembly references stay valid)
and one project Wall System per distinct construction (old id kept; a
`designs` scope only where designs disagreed). Groups are never merged.
compose(migrated) reproduces every legacy record exactly.
"""
from __future__ import annotations

import copy
import json

ARCH_TYPE = 'parallel'          # (any generated type: every group wall resolves its rings)
GROUP_FIELDS = ('thickness', 'align', 'print_reference')
CONSTRUCTION_FIELDS = ('type', 'params', 'web')
DEFAULT_TYPE = 'hollow'          # a wall in no Wall System prints its two skins
TYPES = ('single', 'hollow', 'skin_web', 'parallel', 'interleaved', 'linked', 'chain')


def is_grouped(doc) -> bool:
    """A document in the Wall Group form that has not been composed yet."""
    return isinstance(doc, dict) and 'wall_groups' in doc and 'wall_systems' not in doc


def applies(system, design_id) -> bool:
    ds = system.get('designs')
    return not ds or design_id in ds


def system_of(source_id, group_id, systems, design_id=None):
    """The Wall System of one wall: the first applicable system listing the
    wall, else the first listing its group, else None."""
    app = [s for s in systems or [] if design_id is None or applies(s, design_id)]
    for s in app:
        if source_id in (s.get('members') or []):
            return s
    for s in app:
        if group_id is not None and group_id in (s.get('groups') or []):
            return s
    return None


def compose(doc, design_id=None, systems=None, arch=False) -> dict:
    """The build document (legacy `wall_systems` records) of a Wall Group
    document — see the module doc. Unchanged for any other document."""
    if not is_grouped(doc):
        return doc
    out = copy.deepcopy(doc)
    groups = list(out.get('wall_groups') or []) + _own_walls(out, design_id, systems, arch)
    recs, order = {}, []                         # key → record draft (first-occurrence order)
    for g in groups:
        gid = g.get('id')
        members = [str(m) for m in g.get('members') or []]
        for m in members:
            s = None if arch else system_of(m, gid, systems, design_id)
            if s is None:
                key = ('group', gid)
            else:
                key = ('system', s.get('id'), json.dumps([g.get(f) for f in GROUP_FIELDS]))
            r = recs.get(key)
            if r is None:
                r = recs[key] = {'group': g, 'system': s, 'members': [], 'groups': []}
                order.append(key)
            r['members'].append(m)
            if gid not in r['groups']:
                r['groups'].append(gid)
    used, legacy = set(), []
    for key in order:
        r = recs[key]
        g, s = r['group'], r['system']
        whole = len(r['groups']) == 1 and len(r['members']) == len(g.get('members') or [])
        if whole:
            rid = g.get('id')
        elif len(r['groups']) == 1:
            rid = f"{g.get('id')}~{s.get('id') if s else 'base'}"
        else:
            rid = s.get('id')
        base, k = rid, 1
        while rid in used:
            k += 1
            rid = f'{base}~{k}'
        used.add(rid)
        rec = {'id': rid, 'name': (s.get('name') if s is not None else '') or g.get('name') or '',
               'members': list(r['members'])}
        for f in GROUP_FIELDS:
            if f in g:
                rec[f] = copy.deepcopy(g[f])
        if arch:
            rec.update(type=ARCH_TYPE, params={}, web={})
        elif s is None:
            rec.update(type=DEFAULT_TYPE, params={}, web={})
        else:
            rec.update(type=s.get('type') or DEFAULT_TYPE, params=copy.deepcopy(s.get('params') or {}),
                       web=copy.deepcopy(s.get('web') or {}))
        legacy.append(rec)
    out['wall_systems'] = legacy
    if not arch:
        _sync_webs(out, legacy)
    return out


OWN_PREFIX = 'wall:'


def _own_walls(doc, design_id, systems, arch):
    """UNGROUPED walls (2026-10-11 correction): a source path in no Wall Group
    is still an architectural wall — with its OWN envelope when it stores one
    (`path['envelope']` = {thickness, align, print_reference}; the Wall
    Systems workspace edits it) — and assignable to a Wall System directly.
    Each takes part as a one-wall group `wall:<id>`: with a thickness (an
    envelope), or — printing only — when a Wall System lists it."""
    grouped = {str(m) for g in doc.get('wall_groups') or [] for m in g.get('members') or []}
    out = []
    for p in doc.get('source_paths') or []:
        pid = str(p.get('id'))
        if not p.get('id') or pid in grouped:
            continue
        env = p.get('envelope') if isinstance(p.get('envelope'), dict) else {}
        th = float(env.get('thickness') or 0)
        listed = not arch and system_of(pid, None, systems, design_id) is not None
        if th <= 1e-9 and not listed:
            continue                              # (a plain single-bead path, as before)
        out.append({'id': OWN_PREFIX + pid, 'name': p.get('label') or pid, 'members': [pid], 'thickness': th,
                    'align': env.get('align') or 'auto', 'print_reference': bool(env.get('print_reference'))})
    return out


def _sync_webs(doc, legacy):
    """Skin + Web records: their owned web infills carry the system's web."""
    infills = doc.setdefault('infills', [])
    for rec in legacy:
        web = rec.get('web') or {}
        if rec.get('type') != 'skin_web' or web.get('pattern', 'none') == 'none':
            continue
        owned = [f for f in infills if f.get('owner') == rec['id']]
        fields = {'pattern': web['pattern'], 'params': copy.deepcopy(web.get('params') or {})}
        if 'variation_index' in web:
            fields['variation_index'] = int(web.get('variation_index') or 0)
        if owned:
            for f in owned:
                f.update(copy.deepcopy(fields))
            continue
        for pid in rec['members']:
            infills.append({'id': f"{rec['id']}~web~{pid}", 'path_id': pid, 'kind': 'wall',
                            'variation_index': 0, **copy.deepcopy(fields), 'owner': rec['id']})


# ---- MIGRATION: legacy Wall Systems → Wall Groups + project Wall Systems --------------

def split_document(doc):
    """(Wall Group document, [(record id, construction dict)]) of one resolved
    legacy document. The construction's web definition is the one the build
    actually used: the owned infill record's (pattern, params, variation)."""
    out = copy.deepcopy(doc)
    legacy = out.pop('wall_systems', None) or []
    groups, cons = [], []
    for r in legacy:
        g = {'id': r.get('id'), 'name': r.get('name') or '', 'members': list(r.get('members') or [])}
        for f in GROUP_FIELDS:
            if f in r:
                g[f] = copy.deepcopy(r[f])
        groups.append(g)
        web = copy.deepcopy(r.get('web') or {})
        if r.get('type') == 'skin_web' and web.get('pattern', 'none') != 'none':
            owned = [f for f in out.get('infills') or [] if f.get('owner') == r.get('id')]
            if owned:
                web['pattern'] = owned[0].get('pattern', web.get('pattern'))
                web['params'] = copy.deepcopy(owned[0].get('params') or {})
                if 'variation_index' in owned[0] or 'variation_index' in web:
                    web['variation_index'] = int(owned[0].get('variation_index') or 0)
        cons.append((r.get('id'), {'name': r.get('name') or '', 'type': r.get('type') or 'skin_web',
                                   'params': copy.deepcopy(r.get('params') or {}), 'web': web}))
    out['wall_groups'] = groups
    return out, cons


def split_designs(designs) -> tuple:
    """(new design dicts, project wall_systems) of a list of LEGACY Layer
    Design dicts (LayerDesign.to_dict form): every design resolved, split,
    and derived designs re-expressed as deltas of their (new) parents."""
    from layer_design import DesignLibrary, LayerDesign, derive_delta
    lib = DesignLibrary(designs)
    order = []

    def visit(did):
        if did in order:
            return
        p = lib.get(did).parent
        if p is not None:
            visit(p)
        order.append(did)
    for d in lib.designs:
        visit(d)
    new_docs, by_id = {}, {}
    for did in order:
        doc, cons = split_document(lib.document(did))
        new_docs[did] = doc
        for rid, c in cons:
            by_id.setdefault(rid, []).append((did, c))
    systems = []
    for rid, entries in by_id.items():
        variants = []                                  # distinct constructions of one id
        for did, c in entries:
            sig = json.dumps(c, sort_keys=True)
            v = next((x for x in variants if x[0] == sig), None)
            if v is None:
                variants.append((sig, c, [did]))
            else:
                v[2].append(did)
        for k, (_, c, dids) in enumerate(variants):
            systems.append({'id': rid if k == 0 else f'{rid}@{k}', **c, 'members': [], 'groups': [rid],
                            'designs': None if len(variants) == 1 else dids})
    out = []
    for raw in designs:                                # (the file's design order)
        did = raw['id']
        d = lib.get(did)
        if d.parent is None:
            out.append(LayerDesign(did, d.name, None, new_docs[did]).to_dict())
        else:
            patch, settings = derive_delta(new_docs[d.parent], new_docs[did])
            out.append(LayerDesign(did, d.name, d.parent, None, patch, settings).to_dict())
    return out, systems
