"""
PERSISTENT JUNCTION RECORDS (M4 shadow, 2026-10-15; M5: AUTHORITATIVE FOR
TRIM BOUNDARIES — design-toolpath/WALL_ARCHITECTURE.md §4, §10, §F).

M5: a junction keeps its identity while members JOIN or LEAVE it (`compatible`
/ `_diff`: the same junction when the member sets share ≥ 2 walls with equal
roles and one contains the other) — a branch drawn to the circle × rectangle
junction makes it a 3-member junction, never a new one. Trims are bounded by
junctions under the same rule (trim.py `_by_junctions`); `trim_links` names,
per layout state, the junction records each trim is bounded by (written onto
the trims on save: `trim.junctions = [J_a, J_b]`). Registry mode 'trims'.
Everything else below is still SHADOW (production junction geometry comes
from the wall-network face contacts).

A junction is a RELATIONSHIP of walls in the authored architecture: the set
of member walls (stable source ids) and each member's role there —
`through` (a closed form, or an open wall continuing past the point; `s` =
the arc fraction where the junction lies, a HINT that tells two junctions of
the same walls apart, never identity) or `start` / `end` (the wall ends at
it). Never a face pair, never an XY ordinal.

SHADOW MODE: the records are DETECTED exactly as the design geometry is
contacted today (trim.contacts on the processed source polylines — the same
segment-contact primitives as the wall networks), PERSISTED (project-level
`junctions` registry, ids kept across saves) and the existing junction
treatment overrides (corner keys) are MAPPED onto them. Only the TRIM
BOUNDARIES (M5, trim.py `_by_junctions`) read them; production junction
geometry still comes from the wall-network face contacts (network.py).
`consistency` compares the two; `layer_presence`
reports, per resolved Assembly layer, which junctions exist there — a
junction may cease / appear where transformed walls stop / start
intersecting (§F1: valid topology, never an error).

Registry (project['junctions']):
  {'mode': 'trims', 'counter': n, 'records': [
     {'id': 'J3', 'members': [{'wall': 'p1', 'at': 'through', 's': 0.25},
                              {'wall': 'p5', 'at': 'end'}],
      'kind': 'cross' | 'tee' | 'corner' | 'hub',
      'owner': design id (the state that introduces it — lineage, like walls),
      'states': [design ids where it is active (all members, or ≥ 2 of them)],
      'shape': 'inherit' | {'style', 'radius'} | 'per_corner',
      'overrides': [{'corner', 'treatment', 'radius'}]   (the mapped legacy keys)
     }]}
(Storage is project-level so no document / payload / .mudjob byte changes;
moving the records into the lineage-owned documents is still open — M5 did
not do it.)
"""
from __future__ import annotations

import copy
import math

TOL_END = 1e-4          # an open wall's contact this close (arc length) to an end: that END
HINT_ROUND = 6


# ---- detection ------------------------------------------------------------------

def _layer_of(doc):
    from app import _deserialise_layer
    layer = _deserialise_layer(doc)
    layer.envelope_only = True
    return layer


def _widths(doc) -> dict:
    """{wall id: architectural width} (envelope thickness, else a bead)."""
    bead = float(((doc.get('material') or {}).get('bead_width')) or 3.0)
    out = {p['id']: bead for p in doc.get('source_paths') or [] if isinstance(p, dict)}
    for r in doc.get('wall_systems') or []:
        t = r.get('thickness')
        for m in r.get('members') or []:
            if t:
                out[m] = max(float(t), bead)
    for p in doc.get('source_paths') or []:
        env = p.get('envelope') if isinstance(p, dict) else None
        if env and env.get('thickness'):
            out[p['id']] = max(float(env['thickness']), bead)
    return out


def observe(doc, production=False) -> dict:
    """The junctions of ONE resolved document (build format — composed, a
    state or a transformed Assembly layer): {'walls': [visible wall ids],
    'junctions': [{'x', 'y', 'members': {wall: {'at', 's'?}}}],
    'production': [network junction nodes] (with `production`, which builds
    the envelope), 'widths': {wall: width}}."""
    layer = _layer_of(doc)
    if production:
        layer._build_effective()
        srcs, proc = layer._contact_sources
    else:
        proc, srcs, _ = layer._contact_stage()
    out = observe_contacts(srcs, proc)
    res = {'walls': [p.id for p in srcs], 'junctions': out, 'widths': _widths(doc),
           'overrides': [{'corner': o.key, 'treatment': o.treatment, 'radius': o.radius}
                         for o in layer.junction_overrides]}
    if production:
        res['production'] = [dict(j, walls=[w[2:] if w.startswith('S:') else w for w in j['walls']])
                             for j in getattr(layer, '_production_junctions', [])]
    return res


def observe_contacts(srcs, proc) -> list:
    """The junction observations of a layer's contact stage (the visible
    sources and their processed polylines, model.PrintLayer._contact_stage):
    [{'x', 'y', 'members': {wall: {'at', 's'?}}}] — every point where two or
    more source lines touch, with each member's role there. Used by
    `observe` (documents) and, per built layer, by construction_identity
    (anchors: the same contacts the build itself found, no re-resolve)."""
    import trim as TR
    from model import _cum_lengths, _project_to_polyline
    C = TR.contacts(srcs, proc)
    by_pt: dict = {}
    for sid, lst in C.items():
        for s, ids, pt in lst:
            key = (round(pt.x, 6), round(pt.y, 6))
            e = by_pt.setdefault(key, [pt, set()])
            e[1].add(sid)
            e[1].update(ids)
    closed = {p.id: bool(p.closed) for p in srcs}
    out = []
    for key in sorted(by_pt):
        pt, walls = by_pt[key]
        members = {}
        for w in sorted(walls):
            pts = proc.get(w) or []
            if len(pts) < 2:
                continue
            cums = _cum_lengths(pts, closed[w])
            L = cums[-1]
            s = _project_to_polyline(pt, pts, cums, closed[w])[0]
            if not closed[w] and s <= TOL_END:
                members[w] = {'at': 'start'}
            elif not closed[w] and s >= L - TOL_END:
                members[w] = {'at': 'end'}
            else:
                members[w] = {'at': 'through', 's': round(((s % L) / L) if L > 0 else 0.0, HINT_ROUND)}
        if len(members) >= 2:
            out.append({'x': pt.x, 'y': pt.y, 'members': members})
    return out


def kind_of(members) -> str:
    n = len(members)
    through = sum(1 for m in members if m['at'] == 'through')
    if n >= 3:
        return 'hub'
    return {2: 'cross', 1: 'tee', 0: 'corner'}[through]


def _sig(members, present=None) -> tuple:
    """Identity signature: the member walls + roles (restricted to the walls
    `present` in a state — a Variant removing one member of a 3-wall junction
    leaves a 2-wall junction there)."""
    return tuple(sorted((m['wall'], m['at']) for m in members
                        if present is None or m['wall'] in present))


def _hint_dist(a, b) -> float:
    """Distance of two member lists' arc hints (through members, circular)."""
    sa = {m['wall']: m.get('s') for m in a if m['at'] == 'through'}
    d = 0.0
    for m in b:
        if m['at'] == 'through' and sa.get(m['wall']) is not None and m.get('s') is not None:
            x = abs(sa[m['wall']] - m['s']) % 1.0
            d += min(x, 1.0 - x)
    return d


def _as_members(obs) -> list:
    return [{'wall': w, **v} for w, v in sorted(obs['members'].items())]


def compatible(a, b) -> bool:
    """THE JUNCTION IDENTITY RULE for wall sets (M5): two member sets name the
    same junction when they share a wall and one contains the other — a
    member JOINING (a branch drawn to the junction) or LEAVING (deleted,
    absent from a layout state) never re-keys it. Used by the registry
    (below, with roles) and by trim boundaries (trim.py)."""
    a, b = set(a), set(b)
    return bool(a & b) and (a <= b or b <= a)


def _diff(sig_a, sig_b):
    """Members differing between two (wall, role) signatures when they are the
    same junction (≥ 2 shared walls, equal roles, one set contains the other
    — `compatible`), else None."""
    A, B = dict(sig_a), dict(sig_b)
    shared = set(A) & set(B)
    if len(shared) < 2 or any(A[w] != B[w] for w in shared) or not compatible(A, B):
        return None
    return len(set(A) ^ set(B))


def _match(obs_list, records, present):
    """[(observation, record or None)] — each observation matched to the
    record naming the same junction: the same (restricted) signature first,
    else a compatible one (members joined / left, `_diff`), then the nearest
    hints; one record per observation."""
    taken, out = set(), []
    pairs = []
    for oi, o in enumerate(obs_list):
        om = _as_members(o)
        sig = _sig(om)
        for ri, r in enumerate(records):
            rs = _sig(r['members'], present)
            dn = _diff(rs, sig) if len(rs) >= 2 else None
            if dn is not None:
                pairs.append((dn, _hint_dist(r['members'], om), oi, ri))
    got = {}
    for dn, d, oi, ri in sorted(pairs):
        if oi in got or ri in taken:
            continue
        got[oi] = ri
        taken.add(ri)
    for oi, o in enumerate(obs_list):
        out.append((o, records[got[oi]] if oi in got else None))
    return out


# ---- the registry -----------------------------------------------------------------

def _state_order(designs) -> list:
    by = {d['id']: d for d in designs}
    order = []

    def visit(did):
        if did in order:
            return
        p = by[did].get('parent')
        if p is not None and p in by:
            visit(p)
        order.append(did)
    for d in designs:
        visit(d['id'])
    return order


def _num(rid) -> int:
    try:
        return int(str(rid).lstrip('J'))
    except ValueError:
        return 0


def map_overrides(obs) -> tuple:
    """({junction index: [override]}, [unmapped override]) — each legacy
    corner override (key = two wall FACES + ordinal) assigned to the junction
    the production corner belongs to: the nearest junction (within the
    members' widths) whose members include the corner's walls."""
    corners = {j['corner']: j for j in obs.get('production') or [] if j.get('corner')}
    W = obs['widths']
    mapped, orphan = {}, []
    for o in obs['overrides']:
        c = corners.get(o['corner'])
        best = None
        if c is not None:
            for k, j in enumerate(obs['junctions']):
                if not set(c['walls']) <= set(j['members']):
                    continue
                d = math.hypot(c['x'] - j['x'], c['y'] - j['y'])
                reach = sum(W.get(w, 3.0) for w in j['members']) + 1.0
                if d <= reach and (best is None or d < best[0]):
                    best = (d, k)
        if best is None:
            orphan.append(o)
        else:
            mapped.setdefault(best[1], []).append(o)
    return mapped, orphan


def _shape(overs, corners_of_junction) -> object:
    if not overs:
        return 'inherit'
    shapes = {(o['treatment'], float(o['radius'] or 0)) for o in overs}
    if len(shapes) == 1 and len(overs) >= corners_of_junction:
        t, r = shapes.pop()
        return {'style': 'round' if t == 'round' else 'miter', 'radius': r}
    return 'per_corner'


def build_registry(designs, wall_systems=None, previous=None) -> tuple:
    """(registry, report) of a project's Layer Designs (LayerDesign dicts).
    `previous`: the stored registry — its ids are KEPT for the same
    junctions (same members + roles, nearest hints). A stored junction no
    longer found in any state (its contact removed by a user edit) is
    RETIRED and reported, never resurrected."""
    from layer_design import DesignLibrary
    lib = DesignLibrary(designs, wall_systems or [], True)
    order = _state_order(designs)
    fresh, report = [], {'retired': [], 'orphan_overrides': [], 'new': [], 'states': {}}
    for did in order:
        doc = lib.document(did)
        obs = observe(doc, production=bool(doc.get('junction_overrides')))
        present = set(obs['walls'])
        mapped, orphan = map_overrides(obs) if obs['overrides'] else ({}, [])
        report['orphan_overrides'] += [dict(o, design=did) for o in orphan]
        n_new = 0
        for k, (o, r) in enumerate(_match(obs['junctions'], fresh, present)):
            if r is None:
                members = _as_members(o)
                r = {'id': None, 'members': members, 'kind': kind_of(members), 'owner': did,
                     'states': [], 'shape': 'inherit', 'overrides': []}
                fresh.append(r)
                n_new += 1
            r['states'].append(did)
            have = {m['wall'] for m in r['members']}
            new = [m for m in _as_members(o) if m['wall'] not in have]
            if new:                         # a member joined (another state's wall, a branch)
                r['members'] = sorted(r['members'] + new, key=lambda m: m['wall'])
                r['kind'] = kind_of(r['members'])
            if k in mapped and did == r['owner']:
                ncorners = sum(1 for j in obs.get('production') or []
                               if j.get('corner') and set(j['walls']) <= set(o['members']))
                r['overrides'] = mapped[k]
                r['shape'] = _shape(mapped[k], max(1, ncorners))
        report['states'][did] = {'junctions': len(obs['junctions']), 'introduced': n_new}
    # stable ids: the previous registry's, matched by members + roles + hints
    prev = [r for r in ((previous or {}).get('records') or []) if isinstance(r, dict) and r.get('members')]
    counter = max([int((previous or {}).get('counter') or 0)] + [_num(r.get('id')) for r in prev])
    pairs = []
    for fi, f in enumerate(fresh):
        for pi, p in enumerate(prev):
            dn = _diff(_sig(p['members']), _sig(f['members']))
            if dn is not None:              # (members joined / left: the same junction)
                pairs.append((dn, _hint_dist(p['members'], f['members']), fi, pi))
    got, used = {}, set()
    for dn, d, fi, pi in sorted(pairs):
        if fi in got or pi in used:
            continue
        got[fi] = pi
        used.add(pi)
    for fi, f in enumerate(fresh):
        if fi in got:
            f['id'] = prev[got[fi]]['id']
        else:
            counter += 1
            f['id'] = f'J{counter}'
            if prev:
                report['new'].append(f['id'])
    report['retired'] = [p['id'] for pi, p in enumerate(prev) if pi not in used]
    reg = {'mode': 'trims', 'counter': counter, 'records': fresh}
    return reg, report


# ---- trims bounded by junctions (M5) ---------------------------------------------------

def _pkey(p):
    return None if p is None else (round(float(p[0]), 6), round(float(p[1]), 6))


def trim_links(designs, wall_systems, records) -> dict:
    """{design id: {trim id: {'source', 'status', 'via'?, 'reason'?,
    'junctions': [J at the section's start | None (open end), J at its end],
    'members': [[walls of each boundary junction]]}}} — every trim of every
    layout state resolved as the Designer resolves it, its boundary contacts
    named by the registry's junction records (`records`)."""
    from layer_design import DesignLibrary
    lib = DesignLibrary(designs, wall_systems or [], True)
    out = {}
    for did in _state_order(designs):
        doc = lib.document(did)
        if not doc.get('trims'):
            out[did] = {}
            continue
        obs = observe(doc)
        present = set(obs['walls'])
        cand = [r for r in records if did in r.get('states', [])]
        at = {}
        for o, r in _match(obs['junctions'], cand, present):
            at[_pkey((o['x'], o['y']))] = (r['id'] if r else None, sorted(o['members']))
        st = _layer_of(doc).resolved_trims()
        mine = {}
        for t in doc['trims']:
            i = st.get(t['id']) or {}
            e = {'source': t['source_path_id'], 'status': i.get('status')}
            for k in ('via', 'reason'):
                if i.get(k):
                    e[k] = i[k]
            if i.get('status') == 'ok':
                ends = [at.get(_pkey(i.get('pa')), (None, [])), at.get(_pkey(i.get('pb')), (None, []))]
                e['junctions'] = [j for j, _ in ends]
                e['members'] = [m for _, m in ends]
            mine[t['id']] = e
        out[did] = mine
    return out


def annotate_trims(designs, links) -> int:
    """Write each trim's boundary junction ids (`junctions`, from its OWNER
    state's resolution — `trim_links`) onto the trim record where it is
    stored (Base document, or the Variant patch introducing it). Returns the
    number of trims annotated. A trim not resolved in its owner state keeps
    the ids it had (the reference it was made with)."""
    n = 0
    for d in designs:
        own = links.get(d['id']) or {}
        if d.get('parent') is None:
            recs = (d.get('document') or {}).get('trims') or []
        else:
            recs = [r for r in (((d.get('patch') or {}).get('trims')) or {}).values()
                    if isinstance(r, dict) and r.get('id')]
        for t in recs:
            e = own.get(t.get('id'))
            if e and e.get('junctions') and any(e['junctions']):
                t['junctions'] = list(e['junctions'])
                n += 1
    return n


# ---- consistency against the production topology --------------------------------------

def consistency(doc, records=None, design_id=None) -> dict:
    """Shadow junctions vs the PRODUCTION topology of one resolved document
    (network junction nodes — wall FACE contacts): every production node is
    assigned to the shadow junction it belongs to (members include its walls,
    within the members' widths). Findings:
      agree            — a shadow junction with production nodes of its walls
      production_only  — walls meeting in production whose SOURCE lines do not
                         touch there (a branch ending on a FACE, envelope bands
                         overlapping): today's face-contact attachment (root
                         cause E) — the M5 trims change exactly these
      shadow_only      — source lines touching with no production node (e.g.
                         a thin wall whose bead merges, a trimmed contact)
    `records` (a registry's): names the shadow junctions by id."""
    obs = observe(doc, production=True)
    W = obs['widths']
    present = set(obs['walls'])
    names = {}
    if records is not None:
        cand = [r for r in records if design_id is None or design_id in r.get('states', [])]
        for k, (o, r) in enumerate(_match(obs['junctions'], cand, present)):
            names[k] = r['id'] if r else None
    hits = {k: [] for k in range(len(obs['junctions']))}
    stray = []
    for n in obs['production']:
        best = None
        for k, j in enumerate(obs['junctions']):
            if not set(n['walls']) <= set(j['members']):
                continue
            d = math.hypot(n['x'] - j['x'], n['y'] - j['y'])
            reach = sum(W.get(w, 3.0) for w in j['members']) + 1.0
            if d <= reach and (best is None or d < best[0]):
                best = (d, k)
        if best is None:
            stray.append(n)
        else:
            hits[best[1]].append(n)
    agree, shadow_only = [], []
    for k, j in enumerate(obs['junctions']):
        e = {'id': names.get(k), 'walls': sorted(j['members']), 'x': round(j['x'], 3), 'y': round(j['y'], 3),
             'kind': kind_of(_as_members(j)), 'nodes': len(hits[k])}
        (agree if hits[k] else shadow_only).append(e)
    groups: dict = {}
    for n in stray:
        groups.setdefault(tuple(sorted(n['walls'])), []).append(n)
    production_only = [{'walls': list(w), 'nodes': len(ns),
                        'x': round(sum(n['x'] for n in ns) / len(ns), 3),
                        'y': round(sum(n['y'] for n in ns) / len(ns), 3)}
                       for w, ns in sorted(groups.items())]
    return {'agree': agree, 'production_only': production_only, 'shadow_only': shadow_only,
            'consistent': not production_only and not shadow_only}


def consistency_all(designs, wall_systems, records) -> dict:
    """{design id: consistency(…)} of every Layer Design (its resolved
    architecture, as the envelope builds it)."""
    from layer_design import DesignLibrary
    lib = DesignLibrary(designs, wall_systems or [], True)
    return {d['id']: consistency(lib.document(d['id']), records, d['id']) for d in designs}


# ---- per resolved Assembly layer (transforms: topology may change through Z) -------------

def layer_presence(records, layers) -> dict:
    """Which junctions exist on each resolved layer. `layers`: [(layer index,
    design id, resolved build-format document)] (transformed / cut layers
    included). Per record: the layers it is present on, absent ranges (its
    walls stopped intersecting there — VALID topology, §F1), its largest
    displacement from its first position; plus junctions that APPEAR on
    transformed layers (walls starting to intersect) — reported, valid."""
    cache: dict = {}
    per = {r['id']: {'present': [], 'absent': [], 'max_move': 0.0, 'first': None} for r in records}
    appeared: dict = {}
    import json
    for li, did, doc in layers:
        key = json.dumps(doc, sort_keys=True)
        if key not in cache:
            cache[key] = observe(doc)
        obs = cache[key]
        present = set(obs['walls'])
        cand = [r for r in records if did in r.get('states', [])]
        seen = set()
        for o, r in _match(obs['junctions'], cand, present):
            if r is None:
                sig = tuple(sorted(o['members'].items(), key=lambda x: x[0]))
                appeared.setdefault(json.dumps([[w, v['at']] for w, v in sig]), []).append(li)
                continue
            seen.add(r['id'])
            e = per[r['id']]
            e['present'].append(li)
            if e['first'] is None:
                e['first'] = (o['x'], o['y'])
            else:
                e['max_move'] = max(e['max_move'], math.hypot(o['x'] - e['first'][0], o['y'] - e['first'][1]))
        for r in cand:
            if r['id'] not in seen:
                per[r['id']]['absent'].append(li)
    def ranges(xs):
        out = []
        for x in sorted(xs):
            if out and x == out[-1][1] + 1:
                out[-1][1] = x
            else:
                out.append([x, x])
        return out
    recs = {rid: {'present': ranges(e['present']), 'absent': ranges(e['absent']),
                  'max_move': round(e['max_move'], 3)} for rid, e in per.items()}
    events = []
    for rid, e in recs.items():
        for a, b in e['absent']:
            events.append({'junction': rid, 'event': 'ceases', 'layers': [a, b]})
    for sig, lis in sorted(appeared.items()):
        for a, b in ranges(lis):
            events.append({'members': json.loads(sig), 'event': 'appears', 'layers': [a, b]})
    return {'records': recs, 'events': events}


def copy_registry(reg):
    return copy.deepcopy(reg) if isinstance(reg, dict) else None
