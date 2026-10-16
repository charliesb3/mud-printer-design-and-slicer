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
A document already holding `wall_systems` records is returned unchanged.

OLDER REPRESENTATIONS live only in the migration (`migrate_project`, run on
every open / save): Wall Groups, per-path `wall` (WallSpec), `network_walls`
(a thickness inherited over a source network), in-document build records,
the pre-wall-lattice `lattice_instances`. The geometry pipeline sees only
the per-wall form above.
"""
from __future__ import annotations

import copy

ARCH_TYPE = 'parallel'           # (any generated type: every wall resolves its rings)
ENVELOPE_FIELDS = ('thickness', 'align', 'print_reference')
DEFAULT_TYPE = 'hollow'          # a thick wall in no Wall System prints its two skins
TYPES = ('single', 'hollow', 'skin_web', 'parallel', 'interleaved', 'linked', 'chain')
OWN_PREFIX = 'wall:'


def is_wall_document(doc) -> bool:
    """A Designer document as authored (per wall), not yet composed: one
    holding `wall_systems` is already a BUILD document (compose is a no-op
    on it)."""
    return isinstance(doc, dict) and 'wall_systems' not in doc


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
    """The build document (`wall_systems` records) of a per-wall document —
    see the module doc. A document that already holds records is returned
    unchanged. (`design_id` is accepted for the callers' signature:
    construction never depends on the Layer Design.)"""
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


LEGACY_EMPTY = ('offset_treatments',)        # written only when non-empty (an older feature)
LEGACY_REMOVED = ('lattice_instances', 'network_walls', 'wall_groups')   # never written any more


def _construction_system(systems, sysd, member, report, name):
    """A legacy per-path construction dict (`wall.system`) → the project
    Wall System of that construction (one per distinct construction)."""
    import json
    typ = sysd.get('type') if isinstance(sysd, dict) else None
    if typ not in TYPES or typ in ('skin_web', 'hollow', 'single'):
        return
    params = {k: v for k, v in sysd.items() if k != 'type'}
    key = json.dumps([typ, params], sort_keys=True)
    for s_ in systems:
        if s_.get('_legacy_key') == key:
            if member not in s_['members']:
                s_['members'].append(member)
            return
    taken = {str(s_.get('id')) for s_ in systems}
    sid = next(f'WS{n}' for n in range(1, 10000) if f'WS{n}' not in taken)
    systems.append({'id': sid, 'name': f'Wall System {len(systems) + 1}', 'type': typ, 'params': params,
                    'web': {}, 'members': [member], '_legacy_key': key})
    report.append(f'{name}: wall {member} carried its own {typ} construction — now project Wall System {sid}.')


def _legacy_walls(doc, systems, report, name, inherit=True):
    """A pre-Wall-System document's `wall` (per path) and `network_walls`
    (a thickness every wall of a source network inherits) → per-wall
    envelopes; `lattice_instances` (the pre-wall-lattice pairwise lattice)
    dropped. In place; only where such fields exist."""
    paths = [p for p in doc.get('source_paths') or [] if isinstance(p, dict)]
    nws = [w for w in (doc.pop('network_walls', None) or []) if isinstance(w, dict)]
    if doc.pop('lattice_instances', None):
        report.append(f'{name}: its legacy pairwise lattice (Boundary A / B lattice instances) is no longer '
                      'supported — dropped (walls carry a Wall System / wall infill instead).')
    if not paths or not (nws or any(isinstance(p.get('wall'), dict) for p in paths)):
        return
    root = None
    if nws and inherit:
        try:
            from profile_cuts import _networks
            root = _networks(doc)
        except Exception:                           # (unresolvable geometry: anchors only)
            root = None
    for p in paths:
        pid = str(p.get('id') or '')
        w = p.pop('wall', None)
        env, sysd = None, None
        if isinstance(w, dict) and 'thickness' in w:   # its OWN wall (an explicit 0: a single bead)
            t = float(w.get('thickness') or 0)
            if t > 1e-9:
                env = {'thickness': t, 'align': w.get('align') or 'auto',
                       'print_reference': bool(w.get('print_reference'))}
                sysd = w.get('system')
        else:
            nw = next((x for x in nws if str(x.get('path_id')) == pid or
                       (root is not None and root(str(x.get('path_id'))) == root(pid))), None)
            if nw is not None and float(nw.get('thickness') or 0) > 1e-9:
                env = {'thickness': float(nw['thickness']), 'align': nw.get('align') or 'auto',
                       'print_reference': bool(nw.get('print_reference'))}
                sysd = nw.get('system')
        if env is not None and not p.get('envelope'):
            p['envelope'] = env
            report.append(f'{name}: wall {pid} — its {"own" if w else "inherited network"} wall '
                          f'{env["thickness"]:g} in is now the wall\'s envelope.')
        if env is not None and sysd:
            _construction_system(systems, sysd, pid, report, name)


def _drop_state_construction(d, report):
    """A Variant's LEGACY per-state construction / thickness patches
    (`wall_systems`, `network_walls`) — dropped, reported (Base wins)."""
    patch, name = d.get('patch') or {}, d.get('name') or d.get('id')
    for coll, what in (('wall_systems', 'Wall System construction'), ('network_walls', 'network wall')):
        if patch.get(coll):
            report.append(f'{name}: changed {what} of {", ".join(sorted(map(str, patch[coll])))} '
                          '— dropped (construction and wall properties belong to the walls in every state).')
            patch.pop(coll)


def migrate_project(P) -> list:
    """Schema 6 → 7, in place on a project dict; returns the REPORT (lines).
    * pre-Wall-System documents: per-path `wall` / `network_walls` → each
      wall's `envelope` (+ a project Wall System for a per-path construction);
      in-document build records → Wall Groups (wall_groups.split_designs);
      `lattice_instances` dropped;
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
    systems = P.setdefault('wall_systems', []) if isinstance(P.get('wall_systems', []), list) else []
    if any(d.get('parent') is None and isinstance((d.get('document') or {}).get('wall_systems'), list) for d in D):
        # an in-document BUILD record set (the pre-schema-2 form): split into
        # Wall Groups + project Wall Systems first (then converted below). A
        # Variant's per-state construction is dropped BEFORE the split (Base
        # wins — schema 7), so no per-state system variants are created.
        from wall_groups import split_designs
        for d in D:
            if d.get('parent') is not None:
                _drop_state_construction(d, report)
        new, split_sys = split_designs(D)
        D[:] = new
        have = {str(s_.get('id')) for s_ in systems}
        for s_ in split_sys:
            if str(s_.get('id')) in have:
                report.append(f'Wall System {s_.get("id")} of an older document duplicates a project '
                              'Wall System — the project one is kept.')
                continue
            systems.append(s_)
        report.append('In-document Wall System records of an older document — converted to per-wall '
                      'envelopes and project Wall Systems.')
    base = next((d for d in D if d.get('parent') is None), None)
    bdoc = (base or {}).get('document') or {}
    _legacy_walls(bdoc, systems, report, (base or {}).get('name') or 'Base')
    bgroups = {str(g.get('id')): g for g in bdoc.get('wall_groups') or []}
    members_of = {gid: [str(m) for m in g.get('members') or []] for gid, g in bgroups.items()}
    for d in D:
        if d.get('parent') is None:
            continue
        name = d.get('name') or d.get('id')
        patch, settings = d.setdefault('patch', {}), d.setdefault('settings', {})
        sp = patch.get('source_paths') or {}
        own = {rid for rid, r in sp.items() if isinstance(r, dict) and r.get('id') == rid}   # walls it added
        _legacy_walls({'source_paths': [r for rid, r in sp.items() if rid in own],
                       'lattice_instances': patch.pop('lattice_instances', None)}, systems, report, name,
                      inherit=False)
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
        _drop_state_construction(d, report)
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
        for k in LEGACY_REMOVED:
            bdoc.pop(k, None)
        for k in LEGACY_EMPTY:
            if k in bdoc and not bdoc[k]:
                bdoc.pop(k)
    for s in P.get('wall_systems') or []:
        s.pop('_legacy_key', None)
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
