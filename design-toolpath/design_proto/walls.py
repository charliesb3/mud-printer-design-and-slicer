"""
WALLS (M2, 2026-10-15 — WALL_ARCHITECTURE.md): the per-wall architecture.

A WALL is a source path; its persistent identity is its source id (the same
in Base and every Variant). Its architectural envelope is its own:

    path['envelope'] = {thickness, align ('center' default for new walls;
                        'auto' kept where older data had it), print_reference}

— no envelope (or thickness 0) = a THIN wall (single bead / out-and-back:
its width is set by the material, not a free value). Wall GROUPS no longer
exist: `normalize_groups` turns an older grouped document into this form
(each member wall receives its group's envelope).

CONSTRUCTION is assigned to walls by the project's Wall Systems
(`project['wall_systems'] = [{id, name, type, params, web, members}]`) —
independent of Layer Design: the `designs` scope and group assignment are
gone (migrated by `migrate_project`).

BUILD FORMAT (unchanged pipeline): the Designer's model, the lineage lattice,
Wall System generation and routing consume the per-document legacy
`wall_systems` RECORDS; `compose` derives them per wall:
  * walls assigned to one Wall System with one envelope (thickness, align,
    print reference) form ONE record — exactly as before, so a region of
    such walls generates continuously; record id = the system id (a second
    envelope of the same system: `<system>~2` …);
  * an unassigned THICK wall is its own record `wall:<id>` (Hollow: its two
    skins — skins are never "generated", so record grouping cannot change
    their geometry, and envelope-only builds never generate);
  * an unassigned thin wall is no record (a plain single-bead path);
  * Skin + Web webs: owned infill records take the system's web definition,
    a record without one gets `<record>~web~<member>`.
Record ids are LABELS only (renaming one leaves the G-code byte-identical —
measured 2026-10-15). `arch=True` composes the ARCHITECTURE only (Designer /
Assembly envelopes): every thick wall a record of a generated type so its
material rings resolve; never generated (`PrintLayer.envelope_only`).
Documents with legacy `wall_systems` records are returned unchanged.
"""
from __future__ import annotations

import copy

ARCH_TYPE = 'parallel'           # (any generated type: every wall resolves its rings)
ENVELOPE_FIELDS = ('thickness', 'align', 'print_reference')
DEFAULT_TYPE = 'hollow'          # a thick wall in no Wall System prints its two skins
TYPES = ('single', 'hollow', 'skin_web', 'parallel', 'interleaved', 'linked', 'chain')
OWN_PREFIX = 'wall:'


def is_wall_document(doc) -> bool:
    """A Designer document in the per-wall (or older grouped) form, not yet
    composed. LEGACY documents carry their construction themselves (build
    `wall_systems` records, per-path `wall`, `network_walls`) and are never
    composed."""
    if not isinstance(doc, dict) or 'wall_systems' in doc:
        return False
    paths = [p for p in doc.get('source_paths') or [] if isinstance(p, dict)]
    if 'wall_groups' in doc or any(p.get('envelope') for p in paths):
        return True
    return not (any(p.get('wall') for p in paths) or doc.get('network_walls'))


def envelope_of(group) -> dict | None:
    th = float(group.get('thickness') or 0)
    if th <= 1e-9:
        return None
    return {'thickness': th, 'align': group.get('align') or 'auto',
            'print_reference': bool(group.get('print_reference'))}


def normalize_groups(doc) -> dict:
    """An older GROUPED document → the per-wall form (in place; returned):
    each member wall receives its group's envelope (the group wins over an
    own envelope, as it did), `wall_groups` removed."""
    groups = doc.pop('wall_groups', None) if isinstance(doc, dict) else None
    if not groups:
        return doc
    paths = {str(p.get('id')): p for p in doc.get('source_paths') or [] if isinstance(p, dict)}
    for g in groups:
        env = envelope_of(g)
        for m in g.get('members') or []:
            p = paths.get(str(m))
            if p is None:
                continue
            if env is None:
                p.pop('envelope', None)
            else:
                p['envelope'] = dict(env)
    return doc


def system_of(source_id, systems):
    """The Wall System of one wall: the first listing it (or None)."""
    for s in systems or []:
        if str(source_id) in [str(m) for m in s.get('members') or []]:
            return s
    return None


def compose(doc, design_id=None, systems=None, arch=False) -> dict:
    """The build document (legacy `wall_systems` records) of a per-wall
    document — see the module doc. Unchanged for a legacy document.
    (`design_id` is accepted for the callers' signature: construction never
    depends on the Layer Design.)"""
    if not is_wall_document(doc):
        return doc
    out = normalize_groups(copy.deepcopy(doc))
    recs, order = {}, []
    for p in out.get('source_paths') or []:
        pid = str(p.get('id') or '')
        if not pid:
            continue
        env = p.get('envelope') if isinstance(p.get('envelope'), dict) else {}
        th = float(env.get('thickness') or 0)
        s = None if arch else system_of(pid, systems)
        if th <= 1e-9 and s is None:
            continue                                  # a thin, unassigned wall: a plain single-bead path
        ekey = (th, env.get('align') or 'auto', bool(env.get('print_reference')))
        key = ('wall', pid) if s is None else ('system', str(s.get('id')), ekey)
        r = recs.get(key)
        if r is None:
            r = recs[key] = {'system': s, 'env': ekey, 'members': [], 'label': p.get('label') or pid}
            order.append(key)
        r['members'].append(pid)
    used, legacy, n_of = set(), [], {}
    for key in order:
        r = recs[key]
        s, (th, align, ref) = r['system'], r['env']
        if s is None:
            rid = OWN_PREFIX + r['members'][0]
        else:
            sid = str(s.get('id'))
            n_of[sid] = n_of.get(sid, 0) + 1
            rid = sid if n_of[sid] == 1 else f'{sid}~{n_of[sid]}'
        base, k = rid, 1
        while rid in used:
            k += 1
            rid = f'{base}~{k}'
        used.add(rid)
        rec = {'id': rid, 'name': (s.get('name') if s is not None else '') or r['label'],
               'members': list(r['members']), 'thickness': th, 'align': align, 'print_reference': ref}
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


# ---- MIGRATION (project file schema 7): Wall Groups → walls; ownership cleanup ----------

OWNED_DOC_SETTINGS = ('junction_style', 'junction_radius', 'cap_style', 'cap_corner_radius', 'corner_radius', 'material')
OWNED_WALL_FIELDS = ('envelope', 'wall', 'corner_radius', 'cap')


def migrate_project(P) -> list:
    """Schema 6 → 7, in place on a project dict; returns the REPORT (lines).
    * Base's Wall Groups → each member wall's `envelope` (Base);
    * a Variant's group changes: a wall the Variant ADDED that its groups put
      in a group receives that envelope (it is the Variant's own wall); any
      change to an INHERITED wall's envelope (group fields, membership, a
      removed group) is a divergence → dropped (Base wins) and reported;
    * every Variant-stored persistent property (document settings incl.
      material, junction overrides, owned fields of inherited walls) →
      dropped, reported;
    * Wall Systems: group assignment → explicit wall members; `designs`
      scope removed (reported where it was set);
    * Planar Top Cuts: selected groups → explicit `sources`."""
    report = []
    D = (P.get('designer') or {}).get('designs') or []
    base = next((d for d in D if d.get('parent') is None), None)
    bdoc = (base or {}).get('document') or {}
    bgroups = {str(g.get('id')): g for g in bdoc.get('wall_groups') or []}
    members_of = {gid: [str(m) for m in g.get('members') or []] for gid, g in bgroups.items()}
    for d in D:
        if d.get('parent') is None:
            continue
        name = d.get('name') or d.get('id')
        patch, settings = d.setdefault('patch', {}), d.setdefault('settings', {})
        sp = patch.get('source_paths') or {}
        own = {rid for rid, r in sp.items() if isinstance(r, dict) and r.get('id') == rid}   # walls it added
        for gid, g in (patch.get('wall_groups') or {}).items():
            gid = str(gid)
            if g is None:
                report.append(f'{name}: removed Wall Group {gid} for its walls — dropped (wall thickness belongs to the wall).')
                continue
            full = g.get('id') == gid                           # a group the Variant introduced
            fields = g if full else bgroups.get(gid, {})        # (an inherited group: Base's envelope wins)
            if not full and any(f in g for f in ('thickness', 'align', 'print_reference')):
                report.append(f'{name}: changed Wall Group {gid} ({", ".join(f for f in ("thickness", "align", "print_reference") if f in g)}) '
                              '— dropped (Base wins).')
            env = envelope_of(fields)
            for m in [str(x) for x in (g.get('members') if 'members' in g else members_of.get(gid, []))]:
                if m in own:
                    if env is None:
                        sp[m].pop('envelope', None)
                    else:
                        sp[m]['envelope'] = dict(env)
                elif m not in members_of.get(gid, []) or full:
                    report.append(f'{name}: put inherited wall {m} in Wall Group {gid} — dropped (Base wins).')
            if not full and 'members' in g:
                for m in members_of.get(gid, []):
                    if m not in [str(x) for x in g['members']]:
                        report.append(f'{name}: took inherited wall {m} out of Wall Group {gid} — dropped (Base wins).')
            if full:
                members_of.setdefault(gid, [])
        patch.pop('wall_groups', None)
        for f in OWNED_DOC_SETTINGS:
            if f in settings:
                report.append(f'{name}: stored {f} = {settings[f]!r} — dropped (owned by the project / Base).')
                settings.pop(f)
        for coll, what in (('wall_systems', 'Wall System construction'), ('network_walls', 'network wall')):
            if patch.get(coll):                    # (LEGACY documents: per-state construction / thickness)
                report.append(f'{name}: changed {what} of {", ".join(sorted(map(str, patch[coll])))} '
                              '— dropped (construction and wall properties belong to the walls in every state).')
                patch.pop(coll)
        if patch.get('junction_overrides'):
            report.append(f'{name}: stored {len(patch["junction_overrides"])} junction override(s) — dropped (Base owns junctions).')
            patch.pop('junction_overrides')
        for rid, r in list(sp.items()):
            if isinstance(r, dict) and r.get('id') != rid:
                for f in OWNED_WALL_FIELDS:
                    if f in r:
                        report.append(f'{name}: changed {f} of inherited wall {rid} — dropped (Base wins).')
                        r.pop(f)
                if not r:
                    sp.pop(rid)
    if base is not None:
        normalize_groups(bdoc)
    for s in P.get('wall_systems') or []:
        mem = [str(m) for m in s.get('members') or []]
        for gid in s.pop('groups', None) or []:
            for m in members_of.get(str(gid), []):
                if m not in mem:
                    mem.append(m)
        s['members'] = mem
        if s.get('designs'):
            report.append(f'Wall System {s.get("name") or s.get("id")}: applied only to {", ".join(s["designs"])} '
                          '— now applies to its walls in every layout state.')
        s.pop('designs', None)
    st = (P.get('assembly') or {}).get('state') or {}
    for p in st.get('profiles') or []:
        if isinstance(p, dict) and p.get('kind') == 'plane' and p.get('systems'):
            src, keep = [str(x) for x in p.get('sources') or []], []
            for gid in p['systems']:
                if str(gid) not in members_of:
                    keep.append(gid)              # (not a Wall Group: a legacy document's own record id)
                    continue
                for m in members_of[str(gid)]:
                    if m not in src:
                        src.append(m)
            p['sources'], p['systems'] = src, keep
    return report


def migrate_parts(designs, systems=(), assembly_state=None):
    """(designs, wall systems, assembly state, report) — `migrate_project` on
    the parts of a project (API callers / tests holding older grouped inputs:
    the same conversion an opened project file gets)."""
    P = {'designer': {'designs': copy.deepcopy(list(designs))}, 'wall_systems': copy.deepcopy(list(systems or [])),
         'assembly': {'state': copy.deepcopy(assembly_state or {})}}
    rep = migrate_project(P)
    return P['designer']['designs'], P['wall_systems'], P['assembly']['state'], rep
