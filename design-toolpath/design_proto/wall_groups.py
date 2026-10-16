"""
WALL GROUPS — LEGACY READER ONLY (M2, 2026-10-15).

Wall Groups no longer exist (the per-wall architecture: walls.py). What
remains here is the ONE-WAY migration of project schema 1 (legacy documents
holding their own Wall System build records) into the schema-2 form
(Wall Groups + project Wall Systems) — `split_document` / `split_designs`,
used by `project_file._v1_to_v2`; the schema-7 step (`walls.migrate_project`)
then turns those groups into per-wall envelopes and explicit Wall System
members. Nothing composes, edits or stores Wall Groups any more.

(2026-10-11 history: Wall Groups were the architecture — members, thickness,
alignment — and project Wall Systems the construction, assigned to walls or
whole groups; `compose` built the legacy records from them. Superseded by
walls.compose, which builds the same records per wall.)
"""
from __future__ import annotations

import copy
import json

GROUP_FIELDS = ('thickness', 'align', 'print_reference')


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
