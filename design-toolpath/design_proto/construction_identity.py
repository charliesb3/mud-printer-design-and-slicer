"""
CONSTRUCTION IDENTITY — stable architectural anchors and construction
segments of ONE resolved layer (stage Z2, 2026-10-17; WALL_ARCHITECTURE.md
§Z.2 §1–2). IDENTITY ONLY: nothing here changes geometry.

Why: cross-Z coherence is an IDENTITY problem. The reverted Pass B recovered
"which cell is which" by nearest-distance matching between layers and
failed. Every feature a construction pins to already HAS a stable identity
in the architecture — walls (source ids), junctions (persistent records,
junctions.py), openings (record ids, Assembly cuts included: `asm-…`),
corners (a wall's own vertex ordinal), wall ends — so a layer names its
construction from those and never recovers it geometrically from another
layer. Within ONE layer the construction is derived from that layer's
architecture, so mapping a skeleton feature to the architectural feature it
sits on (nearest within reach) is a plain lookup, not cross-Z matching.

ANCHORS of a layer (architecture; `anchors(layer, sources, processed)`):
  J<n>            a persistent junction record present on this layer
                  (junctions._match on the layer's own contacts: members +
                  roles, nearest hints); `J?<signature>` for a junction that
                  exists only here (walls that start to intersect under a
                  transform — a valid topology event, §F1), named by its
                  member walls + roles so the same event has the same name
                  on every layer it is on
  C:<wall>#<k>    the k-th corner of a wall: a vertex of the AUTHORED path
                  (unrounded) turning ≥ CORNER_MIN — an ordinal along the
                  path, never a position
  E:<wall>:start  the free ends of an open wall
  E:<wall>:end
  O:<opening>:a   the two jambs of an opening (Designer or Assembly cut;
  O:<opening>:b   `a` = lower arc position)
A junction anchor carries its member roles, which members are THIN (no
envelope: an Out-and-Back / single bead — never part of a construction
domain, invariant 8) and `excursion` when a thin member meets thick ones.

DOMAINS and SEGMENTS (`resolve_runs`, from the wall-lattice planner's
skeleton runs): a domain is one lattice region — the merged material whose
bounding walls print one web (source boundaries irrelevant, invariant 7;
'__J' fillets excluded; THIN walls are never faces of it). Each skeleton run
is labelled by the anchors at its ends (a skeleton junction → the junction
anchor there; a dead end → the opening jamb / wall end there, else the
junction that cut it) and at its detected corners (a wall corner or a
junction kink). The construction SEGMENTS are the stretches between
consecutive anchors; segment id = the unordered anchor pair, disambiguated
only when a region has two of them: cyclic order round a loop (`a>b`), the
wall a run lies in (`~wall`, the two runs of a lens). An anchor that nothing
architectural explains within reach is reported (`unresolved`), never
invented.

EVENTS (`diff(previous, current)`): anchors / segments / domains born or
ceased between two layers — junction appearance and disappearance are
explicit events here and nowhere else.
"""
from __future__ import annotations

import math

CORNER_MIN = math.radians(30.0)       # = wall_lattice.CORNER_TURN: an authored vertex turning this much is a corner
REACH_JUNCTION = 2.0                  # × thickness: a skeleton junction node to its junction anchor
REACH_END = 1.5                       # × thickness: a dead-end node to its jamb / wall end
REACH_CORNER = 2.0                    # × thickness: a centre-line corner to its wall corner / junction kink


# ---- anchors of a layer -----------------------------------------------------------------

def _turn(a, b, c) -> float:
    d1 = (b.x - a.x, b.y - a.y)
    d2 = (c.x - b.x, c.y - b.y)
    l1, l2 = math.hypot(*d1), math.hypot(*d2)
    if l1 < 1e-9 or l2 < 1e-9:
        return 0.0
    cosang = max(-1.0, min(1.0, (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)))
    return math.acos(cosang)


def wall_corners(path) -> list:
    """[(ordinal, Vec2)] — the corners of a wall's AUTHORED geometry: the
    vertices of its unrounded sample polyline that turn ≥ CORNER_MIN
    (a rectangle: its four; a circle / ellipse / curve: none; a drawn
    polyline: its sharp vertices). The ordinal is the vertex index along
    the path — stable under every transform and Corner R."""
    pts = path.sample_points()
    n = len(pts)
    if n < 3:
        return []
    out = []
    if path.closed:
        rng = range(n)
        if pts[0].dist(pts[-1]) < 1e-9:              # (a repeated closing vertex)
            n -= 1
            rng = range(n)
    else:
        rng = range(1, n - 1)
    for k in rng:
        a, b, c = pts[(k - 1) % n], pts[k], pts[(k + 1) % n]
        if _turn(a, b, c) >= CORNER_MIN - 1e-9:
            out.append((k, b))
    return out


def _at_s(pts, cum, s, closed):
    from model import _locate_s
    L = cum[-1]
    if L <= 1e-9:
        return pts[0]
    if closed:
        s %= L
    else:
        s = max(0.0, min(L, s))
    return _locate_s(pts, cum, s, closed)[0]


def anchors(layer, sources, processed, records=None, contact_processed=None) -> list:
    """The architectural anchors of one resolved layer: `sources` /
    `processed` = its visible sources and processed polylines as the build
    uses them (branch ends attached, M6); `contact_processed` = the polylines
    of the CONTACT stage (the design geometry every contact is found on —
    an attached branch end moved into its host would otherwise read as a
    crossing); `records` = the persistent junction records active in this
    design state (None: every junction named by its signature). Walls =
    sources with an effective wall (layer._source_walls); THIN sources
    anchor nothing of their own but are reported as junction members."""
    import junctions as J
    from model import _cum_lengths
    walls = getattr(layer, '_source_walls', None) or {}
    thick_of = {w: float((v or {}).get('thickness') or 0.0) for w, v in walls.items()}
    bead = float(getattr(getattr(layer, 'material', None), 'bead_width', 0.0) or 3.0)
    out = []
    # junctions: the layer's own contacts, named by the registry
    obs = J.observe_contacts(sources, contact_processed if contact_processed is not None else processed)
    present = {p.id for p in sources}
    unmatched: dict = {}
    for o, r in J._match(obs, list(records or []), present):
        members = o['members']
        sig = '+'.join(f"{w}.{v['at']}" for w, v in sorted(members.items()))
        thin = sorted(w for w in members if thick_of.get(w, 0.0) <= 1e-9)
        a = {'id': r['id'] if r else None, 'kind': 'junction', 'x': o['x'], 'y': o['y'],
             'walls': sorted(members), 'roles': {w: v['at'] for w, v in members.items()},
             'thin': thin, 'excursion': bool(thin) and len(thin) < len(members),
             'record': bool(r), 'signature': sig,
             'reach': sum(max(thick_of.get(w, 0.0), bead) for w in members) + 1.0}
        if r is None:
            unmatched.setdefault(sig, []).append((min((v.get('s') or 0.0) for v in members.values()), a))
        out.append(a)
    for sig, lst in unmatched.items():
        lst.sort(key=lambda t: t[0])
        for k, (_, a) in enumerate(lst):
            a['id'] = f'J?{sig}' + (f'#{k}' if len(lst) > 1 else '')
    # corners and free ends of every wall
    for p in sources:
        if thick_of.get(p.id, 0.0) <= 1e-9:
            continue
        for k, pt in wall_corners(p):
            out.append({'id': f'C:{p.id}#{k}', 'kind': 'corner', 'x': pt.x, 'y': pt.y, 'walls': [p.id]})
        pts = processed.get(p.id) or []
        if not p.closed and len(pts) >= 2:
            out.append({'id': f'E:{p.id}:start', 'kind': 'end', 'x': pts[0].x, 'y': pts[0].y, 'walls': [p.id]})
            out.append({'id': f'E:{p.id}:end', 'kind': 'end', 'x': pts[-1].x, 'y': pts[-1].y, 'walls': [p.id]})
    # opening jambs (Designer openings and Assembly cuts alike)
    closed = {p.id: bool(p.closed) for p in sources}
    for o in getattr(layer, 'openings', None) or []:
        pts = processed.get(o.source_path_id) or []
        if len(pts) < 2 or thick_of.get(o.source_path_id, 0.0) <= 1e-9:
            continue
        cum = _cum_lengths(pts, closed[o.source_path_id])
        for tag, s in (('a', o.center_s - 0.5 * o.width), ('b', o.center_s + 0.5 * o.width)):
            q = _at_s(pts, cum, s, closed[o.source_path_id])
            out.append({'id': f'O:{o.id}:{tag}', 'kind': 'jamb', 'x': q.x, 'y': q.y,
                        'walls': [o.source_path_id], 'opening': o.id})
    return out


def junction_of(anchor_list, pt, walls, default_reach) -> object:
    """The junction anchor a production junction node / corner belongs to:
    the nearest junction whose members include `walls`, within the members'
    reach (the rule of junctions.map_overrides). None if there is none."""
    best = None
    walls = set(walls)
    for a in anchor_list:
        if a.get('kind') != 'junction' or not walls <= set(a['walls']):
            continue
        d = math.hypot(a['x'] - pt.x, a['y'] - pt.y)
        if d <= max(float(a.get('reach') or 0.0), default_reach) and (best is None or d < best[0]):
            best = (d, a)
    return best[1] if best else None


# ---- segments of a lattice region ---------------------------------------------------------

def _nearest(cands, pt, reach):
    best = None
    for a in cands:
        d = math.hypot(a['x'] - pt.x, a['y'] - pt.y)
        if d <= reach and (best is None or d < best[0]):
            best = (d, a)
    return best[1] if best else None


def _dist_poly(pt, pts, closed) -> float:
    best = math.inf
    n = len(pts)
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((pt.x - a.x) * dx + (pt.y - a.y) * dy) / L2))
        d = math.hypot(pt.x - a.x - t * dx, pt.y - a.y - t * dy)
        if d < best:
            best = d
    return best


def resolve_runs(identity, runs, thick) -> dict:
    """Name the skeleton runs of one lattice region by its anchors.
    identity: {'anchors': [anchor], 'walls': {wall id: (pts, closed)}}
    (the region's own walls; wall_lattice.plan's `identity` input).
    runs: [{'cycle', 'length', 'ends': [(kind 'J' | 'E', Vec2) × 2] | None,
            'corners': [(s, Vec2)], 'mid': Vec2, 'ccw': bool | None}]
    Returns {'runs': [{'a', 'b', 'corners', 'wall', 'anchors', 'segments':
    [{'id', 'a', 'b', 's': [s0, s1]}]}], 'segments': [ids], 'unresolved': n}.
    The anchors at a run's ends and corners are looked up WITHIN REACH of
    the architecture of this very layer (never another layer's plan)."""
    A = identity.get('anchors') or []
    # every junction with a wall of the region, EXCURSION junctions included:
    # where a thin Out-and-Back enters the host, the host's construction has a
    # local feature (its lanes' mouth) that is named by that junction record —
    # the thin wall itself is never a member of the domain (invariant 8)
    junctions = [a for a in A if a['kind'] == 'junction']
    ends = [a for a in A if a['kind'] in ('jamb', 'end')]
    corners = [a for a in A if a['kind'] == 'corner']
    walls = identity.get('walls') or {}
    out_runs, unresolved = [], 0

    def wall_of(pt):
        best = None
        for w, (pts, closed) in walls.items():
            if len(pts) < 2:
                continue
            d = _dist_poly(pt, pts, closed)
            if best is None or d < best[0]:
                best = (d, w)
        return best[1] if best else None

    for ri, r in enumerate(runs):
        rec = {'cycle': bool(r['cycle']), 'wall': wall_of(r['mid']), 'corners': [], 'a': None, 'b': None}
        seq = []
        if not r['cycle']:
            ids = []
            for kind, pt in r['ends']:
                if kind == 'J':
                    a = _nearest(junctions, pt, REACH_JUNCTION * thick)
                else:
                    a = _nearest(ends, pt, REACH_END * thick) or _nearest(junctions, pt, REACH_END * thick)
                if a is None:
                    unresolved += 1
                    ids.append(f'?{kind}@{rec["wall"]}#{ri}')
                else:
                    ids.append(a['id'])
            rec['a'], rec['b'] = ids
            seq.append((0.0, ids[0]))
        for k, (s, pt) in enumerate(r['corners']):
            a = _nearest(corners + junctions, pt, REACH_CORNER * thick)
            if a is None:
                unresolved += 1
                cid = f'?corner@{rec["wall"]}#{k}'
            else:
                cid = a['id']
            rec['corners'].append(cid)
            seq.append((s, cid))
        if not r['cycle']:
            seq.append((r['length'], ids[1]))
        rec['anchors'] = [i for _, i in seq]
        segs = []
        if r['cycle']:
            if not seq:
                segs.append({'id': f"loop:{rec['wall']}", 'a': None, 'b': None, 's': [0.0, r['length']]})
            else:
                order = seq if r.get('ccw', True) is not False else list(reversed(seq))
                m = len(order)
                for k in range(m):
                    (s0, a), (s1, b) = order[k], order[(k + 1) % m]
                    segs.append({'id': None, 'a': a, 'b': b, 's': [s0, s1], 'cyclic': (a, b)})
        else:
            for (s0, a), (s1, b) in zip(seq, seq[1:]):
                segs.append({'id': None, 'a': a, 'b': b, 's': [s0, s1]})
        rec['segments'] = segs
        out_runs.append(rec)
    # segment ids: the unordered anchor pair; two of them in the region →
    # cyclic order round a loop, else the wall the run lies in
    count: dict = {}
    for rec in out_runs:
        for sg in rec['segments']:
            if sg['id'] is None:
                sg['id'] = '|'.join(sorted((str(sg['a']), str(sg['b']))))
            count[sg['id']] = count.get(sg['id'], 0) + 1
    for rec in out_runs:
        for sg in rec['segments']:
            if count.get(sg['id'], 0) > 1:
                if sg.get('cyclic'):
                    sg['id'] = f"{sg['a']}>{sg['b']}"
                else:
                    sg['id'] = f"{sg['id']}~{rec['wall']}"
    seen: dict = {}
    for rec in out_runs:
        for sg in rec['segments']:
            sg.pop('cyclic', None)
            if sg['id'] in seen:
                seen[sg['id']] += 1
                sg['id'] = f"{sg['id']}#{seen[sg['id']]}"
                sg['ambiguous'] = True
            else:
                seen[sg['id']] = 0
    return {'runs': out_runs, 'segments': sorted(sg['id'] for rec in out_runs for sg in rec['segments']),
            'unresolved': unresolved}


# ---- events between two layers -----------------------------------------------------------

def _ids(identity, kind):
    if not identity:
        return set()
    if kind == 'segment':
        return {s for d in identity.get('domains') or [] for s in (d.get('segments') or [])}
    if kind == 'domain':
        return {d['id'] for d in identity.get('domains') or []}
    return {a['id'] for a in identity.get('anchors') or [] if a['kind'] == kind}


def diff(previous, current) -> dict:
    """The topology EVENTS between two layers' identities (network
    'identity' reports): per kind ('junction', 'jamb', 'end', 'corner',
    'segment', 'domain') the ids born (in current, not previous) and ceased.
    A junction that appears or ceases, an opening starting or closing, a
    segment created by a split or merged away are events HERE — construction
    elsewhere has no reason to change (invariant 6)."""
    out = {}
    for kind in ('junction', 'jamb', 'end', 'corner', 'segment', 'domain'):
        p, c = _ids(previous, kind), _ids(current, kind)
        if p != c:
            out[kind] = {'born': sorted(c - p), 'ceased': sorted(p - c)}
    return out
