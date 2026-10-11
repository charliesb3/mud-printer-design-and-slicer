"""
PROFILE CUTS — the Designer side of ASSEMBLY PROFILES (layer_assembly/
profiles.py, 2026-10-08).

The Assembly decides, per layer Z, which SOURCE geometry survives and asks
for the design with a CUT SPEC. This module turns that spec into ordinary
Designer document records — nothing else in the Designer knows profiles
exist, and no geometry is generated here:

  {'op': 'opening', 'source', 'edge', 'at', 'width', 'profile'}
      → an ordinary Opening record on the source path: clear width `width`,
        centred at the point `at` inches along the straight wall / edge (from
        its start), projected onto the processed source polyline (Corner R
        included) for `center_s`. The existing opening machinery cuts the
        WHOLE wall assembly (every face, lattice, Wall System construction)
        and closes the cut faces with the normal end caps.
  {'op': 'extent', 'source', 'edge': None, 'end', 'retained', 'profile'}
      → the straight open wall's `end` ('start' | 'end') moved so `retained`
        inches survive (its free end is an ordinary free end: normal caps,
        T-joins / networks recomputed). Openings already on it keep their XY
        place (positions re-measured; openings now off the wall are dropped).
        retained 0 → the source is ABSENT: it and the records that belong to
        it alone (its openings, trims, offsets, infills, region overrides)
        are removed; Wall System membership drops it; a Network Wall or an
        owned web anchored on it moves to another member of its network /
        system.

  {'op': 'transition', 'source', 'edge', 'at', 'width', 'alpha', 'profile'}
      → no cut: a `cut_transitions` record (a virtual opening, positioned like
        an opening): Wall Systems / the lattice pre-deform toward that cut's
        end motif by alpha — an opening's cross-Z transition.
  {'op': 'plane', 'sources', 'systems', 'exclude', 'point', 'normal' | 'remove_all', 'profile'}
      → a PLANAR TOP CUT at this layer: one half-plane in plan, kept side
        {q: (q − point)·normal ≥ 0}; every selected source loses its part on
        the removed side (closed: Openings; open: trimmed + Openings; too
        little left: removed) — _apply_plane.
  {'op': 'station', 'source', 'edge', 'at', 'profile'}
      → no document change: a LATTICE STATION of an Assembly opening
        (layer_design.DesignLibrary._station_reference uses it).
  {'op': 'move', 'source', 'opening', 'point', 'width', 'profile'}
      → the design's OWN opening `opening` re-placed for this layer: centred
        at the foot of `point` (design coordinates) on the processed source
        polyline — any wall, curved too — with clear width `width`. The
        Assembly's VERTICAL (PLUMB) opening alignment (layer_assembly/
        profiles._align_designer_openings): the same world position on every
        layer of a transformed stack. Applied first (on the authored design).

STRAIGHT WALLS (straight_walls): what a profile can target in V1 —
  LinePath (whole wall, taperable), an open ExplicitPath whose points are
  collinear (whole wall, taperable), every edge of a RectanglePath and every
  segment of other ExplicitPaths (openings only). Curves are not targets
  (curved / unwrapped elevations are not supported yet).

Order (layer_design.DesignLibrary.transformed): document (inheritance)
→ apply_cuts (extents → openings / transitions → planes → removed sources)
→ semantic transform → resolution.
"""
from __future__ import annotations

import copy
import math

_COLLINEAR = 1e-6


def _pts(d):
    t = d.get('type', 'ExplicitPath')
    if t == 'LinePath':
        return [tuple(d['start']), tuple(d['end'])]
    if t == 'RectanglePath':
        from app import _deserialise_path
        p = _deserialise_path(d)
        return [(q.x, q.y) for q in p.sample_points()]
    if t == 'ExplicitPath':
        return [tuple(q) for q in d.get('control_points', d.get('points', [])) or []]
    return None


def _closed(d) -> bool:
    if d.get('type') == 'RectanglePath':
        return True
    if d.get('type') == 'LinePath':
        return False
    return bool(d.get('closed', False))


def _straight_open(d):
    """(a, b) when the source is ONE straight open wall, else None."""
    if d is None or _closed(d):
        return None
    pts = _pts(d)
    if not pts or len(pts) < 2:
        return None
    a, b = pts[0], pts[-1]
    L = math.dist(a, b)
    if L <= 1e-9:
        return None
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    for q in pts[1:-1]:
        if abs((q[0] - a[0]) * uy - (q[1] - a[1]) * ux) > _COLLINEAR * max(1.0, L):
            return None
        if not -1e-9 <= (q[0] - a[0]) * ux + (q[1] - a[1]) * uy <= L + 1e-9:
            return None
    return a, b


def _edges(d):
    """[(edge index, a, b)] — the straight edges of a source (None index =
    the whole straight open wall)."""
    s = _straight_open(d)
    if s is not None:
        return [(None, s[0], s[1])]
    if d.get('type') not in ('RectanglePath', 'ExplicitPath'):
        return []
    pts = _pts(d) or []
    n = len(pts)
    segs = list(range(n if _closed(d) else n - 1))
    return [(k, pts[k], pts[(k + 1) % n]) for k in segs if math.dist(pts[k], pts[(k + 1) % n]) > 1e-9]


def _edge(d, edge):
    for k, a, b in _edges(d):
        if k == edge:
            return a, b
    return None


def _thicknesses(doc) -> dict:
    """Effective wall thickness per source: its Wall System's envelope, else
    the document-level wall rule (semantic_transform.effective_walls)."""
    out = {}
    try:
        import semantic_transform as ST
        out = {k: v[0] for k, v in ST.effective_walls(doc).items()}
    except Exception:                      # thickness is only shown / used for jamb margins
        out = {}
    for ws in doc.get('wall_systems') or []:
        th = ws.get('thickness')
        if th:
            for m in ws.get('members') or []:
                out[str(m)] = float(th)
    return out


# Generated Wall System constructions whose canonical end motifs need room:
# a stub shorter than this × thickness changes motif from layer to layer
# (measured on tapers: Parallel 3 / 4, Interleaved, Chained Loop)
GENERATED = ('parallel', 'interleaved', 'linked', 'chain')
GENERATED_MIN_THICKNESS = 2.0


def straight_walls(doc) -> list:
    """The design's STRAIGHT walls (profile targets), design coordinates.
    min_length: the shortest printable length of the wall's construction."""
    th = _thicknesses(doc)
    kind = {str(m): ws.get('type') for ws in doc.get('wall_systems') or [] for m in ws.get('members') or []}
    try:                                  # which ends of an open wall touch another wall (UI default side)
        import semantic_transform as ST
        att = ST.attachments(doc)
    except Exception:
        att = {}
    out = []
    for d in doc.get('source_paths') or []:
        sid = d.get('id')
        if not sid or d.get('type') == 'InsetPath' or d.get('visible') is False:
            continue
        base = d.get('label') or sid
        es = _edges(d)
        for k, a, b in es:
            out.append({'source': sid, 'edge': k, 'a': [a[0], a[1]], 'b': [b[0], b[1]],
                        'length': math.dist(a, b), 'thickness': float(th.get(sid, 0.0)),
                        'taper': k is None,
                        'min_length': GENERATED_MIN_THICKNESS * float(th.get(sid, 0.0))
                        if kind.get(sid) in GENERATED else 0.0,
                        'label': base if k is None else f'{base} · edge {k + 1}',
                        'attached': [e for w, e in ((0, 'start'), (1, 'end')) if (sid, w) in att]
                        if k is None else []})
    return out


def designer_openings(doc) -> list:
    """The design's own (authored) OPENINGS as an Assembly profile can refer
    to them: id, source, the straight wall edge it lies on (None index = a
    whole straight wall; `edge` / `at` absent when it is not on one straight
    edge — a curve, a rounded corner), `at` = its centre in inches along that
    edge from its start, clear width, label. Assembly openings ('asm-…') are
    not listed."""
    srcs = {d.get('id'): d for d in doc.get('source_paths') or []}
    th = _thicknesses(doc)
    out = []
    for o in doc.get('openings') or []:
        oid, sid = o.get('id'), o.get('source_path_id')
        d = srcs.get(sid)
        if not oid or str(oid).startswith('asm-') or d is None:
            continue
        rec = {'id': oid, 'source': sid, 'width': float(o.get('width', 12.0)), 'label': o.get('label') or oid,
               'center_s': float(o.get('center_s', 0.0)), 'thickness': float(th.get(sid, 0.0))}
        try:
            pts, closed = _processed(d, doc)
            q = _at_arc(pts, closed, rec['center_s'])
            ring = list(pts) + ([pts[0]] if closed else [])
            rec.update(xy=[q[0], q[1]], closed=bool(closed),
                       length=sum(a.dist(b) for a, b in zip(ring, ring[1:])))   # (any wall: its centre point)
        except Exception:
            q = None
        if q is not None:
            for k, a, b in _edges(d):
                L = math.dist(a, b)
                if L <= 1e-9:
                    continue
                t = ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / (L * L)
                foot = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                if -1e-9 <= t <= 1 + 1e-9 and math.dist(foot, q) <= 1e-3 * max(1.0, L):
                    rec.update(edge=k, at=t * L, point=[q[0], q[1]])
                    break
        out.append(rec)
    return out


def _processed(d, doc):
    """The processed source polyline (the arc-length frame of openings)."""
    from app import _deserialise_path
    from model import _processed_source_pts
    p = _deserialise_path(d)
    cr = p.corner_radius if p.corner_radius is not None else float(doc.get('corner_radius', 0.0) or 0.0)
    return _processed_source_pts(p, cr), p.closed


def _project_s(pts, closed, q):
    """(arc length of the foot of q on the polyline, distance)."""
    ring = list(pts) + ([pts[0]] if closed else [])
    best, acc = (math.inf, 0.0), 0.0
    for a, b in zip(ring, ring[1:]):
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        L = math.sqrt(L2)
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a.x) * dx + (q[1] - a.y) * dy) / L2))
        f = (a.x + t * dx, a.y + t * dy)
        dd = math.dist(f, q)
        if dd < best[0] - 1e-12:
            best = (dd, acc + t * L)
        acc += L
    return best[1], best[0]


def _set_line(d, a, b):
    if d.get('type') == 'LinePath':
        d['start'], d['end'] = [a[0], a[1]], [b[0], b[1]]
    else:
        key = 'control_points' if 'control_points' in d else 'points'
        d[key] = [[a[0], a[1]], [b[0], b[1]]]


def _networks(doc):
    """Union-find root per source over centre-line contacts (the Designer's
    source networks)."""
    import network as N
    import semantic_transform as ST
    from model import Vec2
    srcs = [d for d in doc.get('source_paths') or [] if d.get('id')]
    beads = []
    for d in srcs:
        if d.get('type') == 'InsetPath' and not d.get('points'):
            continue
        pts = ST._centreline(d)
        if len(pts) >= 2:
            beads.append(N.Bead(d['id'], [Vec2(*q) for q in pts], False, N.WIRE, f"S:{d['id']}"))
    uf = N.UnionFind()
    for b in beads:
        uf.find(b.element)
    for a, b in N.find_contacts(beads):
        uf.union(a, b)
    return lambda sid: uf.find(f'S:{sid}')


def _remove_source(out, doc, sid):
    """Remove a source and the records that belong to it alone; re-anchor
    shared records (Network Wall, owned web) on another member."""
    out['source_paths'] = [d for d in out.get('source_paths') or [] if d.get('id') != sid]
    alive = {d.get('id') for d in out['source_paths']}
    for coll, f in (('openings', 'source_path_id'), ('trims', 'source_path_id'),
                    ('offset_treatments', 'source_path_id'), ('region_overrides', 'path_id')):
        if out.get(coll):
            out[coll] = [r for r in out[coll] if r.get(f) != sid]
    if out.get('lattice_instances'):
        out['lattice_instances'] = [r for r in out['lattice_instances']
                                    if sid not in (r.get('path_a_id'), r.get('path_b_id'))]
    root = None
    systems = {}
    for ws in out.get('wall_systems') or []:
        mem = [str(m) for m in ws.get('members') or []]
        if sid in mem:
            ws['members'] = [m for m in mem if m != sid]
        systems[ws.get('id')] = [m for m in ws.get('members') or [] if m in alive]
    for nw in list(out.get('network_walls') or []):
        if nw.get('path_id') == sid:
            root = root or _networks(doc)
            alt = next((d for d in sorted(alive) if root(d) == root(sid)), None)
            if alt is None:
                out['network_walls'].remove(nw)
            else:
                nw['path_id'] = alt
    keep = []
    for f in out.get('infills') or []:
        if f.get('path_id') != sid:
            keep.append(f)
            continue
        mem = systems.get(f.get('owner')) or []
        if mem:                                    # an owned web: same connected group first
            root = root or _networks(doc)
            f['path_id'] = next((m for m in mem if root(m) == root(sid)), mem[0])
            keep.append(f)
    out['infills'] = keep


def apply_cuts(doc, cuts) -> tuple:
    """(document with the cuts applied, report [{profile, op, applied, why?}])."""
    out = copy.deepcopy(doc)
    srcs = {d.get('id'): d for d in out.get('source_paths') or []}
    orig = {d.get('id'): d for d in doc.get('source_paths') or []}
    report, removed = [], set()
    cuts = list(cuts or [])
    if cuts:
        # AUTHORED geometry first: every Trim is resolved on the design as
        # drawn and PINNED to the section it removes, before any cut changes
        # the contacts / closedness its signature names (a cut must never
        # bring back what Trim removed)
        _pin_trims(out)
    gone = {str(c.get('opening')) for c in cuts if c.get('op') == 'unopen'}
    if gone:                                     # (a plumb layer's lattice REFERENCE: those openings absent)
        out['openings'] = [o for o in out.get('openings') or [] if str(o.get('id')) not in gone]
        report += [{'profile': c.get('profile', ''), 'op': 'unopen', 'applied': True} for c in cuts
                   if c.get('op') == 'unopen']
    for c in [c for c in cuts if c.get('op') == 'move']:
        # a PLUMB Designer opening: its own record re-placed for this layer
        r = {'profile': c.get('profile', ''), 'op': 'move', 'source': c.get('source'), 'applied': False}
        report.append(r)
        o = next((o for o in out.get('openings') or [] if str(o.get('id')) == str(c.get('opening'))), None)
        d = srcs.get(o.get('source_path_id')) if o is not None else None
        if d is None:
            r['why'] = 'the opening is not in this design'
            continue
        pts, closed = _processed(d, out)
        sv, dist = _project_s(pts, closed, c['point'])
        o['center_s'], o['width'] = sv, float(c['width'])
        r.update(applied=True, center_s=sv, off=dist)
    for c in [c for c in cuts if c.get('op') == 'extent']:
        sid = c['source']
        r = {'profile': c.get('profile', ''), 'op': 'extent', 'source': sid, 'applied': False}
        report.append(r)
        line = _straight_open(srcs.get(sid))
        if line is None:
            r['why'] = 'not a straight open wall of this design'
            continue
        a, b = line
        L = math.dist(a, b)
        keep = max(0.0, min(L, float(c['retained'])))
        r['applied'] = True
        if keep <= 1e-9:
            removed.add(sid)
            r['removed'] = True
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        if (c.get('end') or 'end') == 'end':
            na, nb, shift = a, (a[0] + ux * keep, a[1] + uy * keep), 0.0
        else:
            na, nb, shift = (b[0] - ux * keep, b[1] - uy * keep), b, L - keep
        _set_line(srcs[sid], na, nb)
        ops = []
        for o in out.get('openings') or []:
            if o.get('source_path_id') == sid:
                o = dict(o, center_s=float(o.get('center_s', 0.0)) - shift)
                if not -1e-9 <= o['center_s'] <= keep + 1e-9:
                    continue                       # its wall stretch is gone at this Z
            ops.append(o)
        if 'openings' in out:
            out['openings'] = ops
    for c in [c for c in cuts if c.get('op') in ('opening', 'transition')]:
        sid = c['source']
        r = {'profile': c.get('profile', ''), 'op': c.get('op'), 'source': sid, 'applied': False}
        report.append(r)
        if sid in removed:
            r['why'] = 'the wall is absent at this Z (tapered away)'
            continue
        d0, d = orig.get(sid), srcs.get(sid)
        if c.get('center_s') is not None and d is not None:
            # (a probe / cut placed directly by arc length on the processed
            # source polyline — any wall, curved too)
            s = float(c['center_s'])
        else:
            e = _edge(d0, c.get('edge')) if d0 is not None else None
            if e is None or d is None:
                r['why'] = 'no such straight wall in this design'
                continue
            (ax, ay), (bx, by) = e
            L = math.dist((ax, ay), (bx, by))
            at = float(c['at'])
            q = (ax + (bx - ax) * at / L, ay + (by - ay) * at / L)
            pts, closed = _processed(d, out)
            s, dist = _project_s(pts, closed, q)
            if dist > 1e-3 * max(1.0, L):
                r['why'] = 'the opening is no longer on the wall at this Z'
                continue
        if c.get('op') == 'transition':
            # NO cut: a virtual opening — the generators pre-deform toward its
            # cut-end motif by alpha (cross-Z transition of the opening)
            out.setdefault('cut_transitions', []).append({
                'source_path_id': sid, 'center_s': s, 'width': float(c['width']),
                'alpha': float(c['alpha']), 'profile': c.get('profile', '')})
            r['applied'] = True
            r['transition'] = float(c['alpha'])
            continue
        oid = f"asm-{c.get('profile', '')}"
        taken = {o.get('id') for o in out.get('openings') or ()}
        if oid in taken:                        # (an array: one record per opening)
            oid = next(f'{oid}.{j}' for j in range(1, len(taken) + 2) if f'{oid}.{j}' not in taken)
        out.setdefault('openings', []).append({
            'id': oid, 'type': 'opening', 'source_path_id': sid,
            'center_s': s, 'width': float(c['width']), 'end_treatment': 'inherit',
            'z_min': None, 'z_max': None, 'label': 'Assembly opening'})
        r['applied'] = True
        r['center_s'] = s
    # planes AFTER the openings of this layer (a stub left between a plane cut
    # and an opening is removed with the cut), then every removed source
    planes = [c for c in cuts if c.get('op') == 'plane']
    if planes:
        # every planar cut of this layer TOGETHER: a source keeps only what
        # survives every plane that selects it (one union of removed stretches)
        report += _apply_planes(out, srcs, planes, removed)
    for sid in sorted(removed):
        _remove_source(out, doc, sid)
        srcs.pop(sid, None)
    return out, report


# ---- PLANAR TOP CUT (2026-10-08) ------------------------------------------------
# One plane cuts a SELECTION of walls; at a layer it is one half-plane in plan
# (op 'plane': `point`, `normal` pointing to the KEPT side, or `remove_all`).
# Every selected source loses the part of its processed centre line on the
# removed side — a closed source by ordinary Openings over those arc-length
# intervals, an open one trimmed to what is kept (+ Openings for interior
# removed stretches), a source with too little left removed — so each
# surviving piece ends exactly where its centre line meets the plane's line.

def selection(doc, c) -> list:
    """The source ids a plane cut selects in this design: `sources` ∪ the
    members of `systems` − `exclude`."""
    ids = {d.get('id') for d in doc.get('source_paths') or [] if d.get('id')}
    out = set(c.get('sources') or []) & ids
    # `systems`: the selected WALL GROUPS (2026-10-11; a legacy document:
    # its Wall Systems — migrated groups keep those ids)
    for ws in (doc.get('wall_groups') or []) + (doc.get('wall_systems') or []):
        if ws.get('id') in set(c.get('systems') or []):
            out |= {str(m) for m in ws.get('members') or []} & ids
    return sorted(out - set(c.get('exclude') or []))


def removed_intervals(pts, closed, P, n) -> tuple:
    """(arc-length intervals [(s0, s1)] of the polyline on the REMOVED side
    of the half-plane {q: (q − P)·n ≥ 0}, total length). Closed: an interval
    may run through the seam (s1 > L)."""
    ring = list(pts) + ([pts[0]] if closed else [])
    f = [(q.x - P[0]) * n[0] + (q.y - P[1]) * n[1] for q in ring]
    cum = [0.0]
    for a, b in zip(ring, ring[1:]):
        cum.append(cum[-1] + a.dist(b))
    L = cum[-1]
    out, cur = [], None
    for k in range(len(ring) - 1):
        a_in, b_in = f[k] < 0, f[k + 1] < 0
        if a_in and cur is None:
            cur = cum[k]
        if a_in != b_in:
            t = f[k] / (f[k] - f[k + 1])
            x = cum[k] + t * (cum[k + 1] - cum[k])
            if a_in:
                out.append((cur, x))
                cur = None
            else:
                cur = x
    if cur is not None:
        out.append((cur, L))
    if closed and len(out) > 1 and out[0][0] <= 1e-9 and out[-1][1] >= L - 1e-9:
        out = [(out[-1][0], out[0][1] + L)] + out[1:-1]          # through the seam
    return [(a, b) for a, b in out if b - a > 1e-9], L


def _min_kept(doc, sid) -> float:
    bead = float((doc.get('material') or {}).get('bead_width', 3.0) or 3.0)
    th = _thicknesses(doc).get(sid, 0.0)
    kind = {str(m): ws.get('type') for ws in doc.get('wall_systems') or [] for m in ws.get('members') or []}
    return max(bead, GENERATED_MIN_THICKNESS * th if kind.get(sid) in GENERATED else 0.0)


def _sub_polyline(pts, a, b):
    """The processed polyline between arc lengths a < b (open)."""
    out, acc = [], 0.0
    for p, q in zip(pts, pts[1:]):
        L = p.dist(q)
        if L <= 0:
            continue
        lo, hi = max(a, acc), min(b, acc + L)
        if lo < hi + 1e-12 and acc <= b and acc + L >= a:
            for t in ((lo - acc) / L, (hi - acc) / L):
                v = p.lerp(q, t)
                if not out or math.dist((out[-1].x, out[-1].y), (v.x, v.y)) > 1e-9:
                    out.append(v)
        acc += L
    return [[v.x, v.y] for v in out]


def _union(ivs, L, closed) -> list:
    """Union of arc-length intervals (closed: an interval may run through
    the seam, s1 > L)."""
    flat = []
    for a, b in ivs:
        if closed and b > L + 1e-12:
            flat += [(a, L), (0.0, b - L)]
        else:
            flat.append((a, b))
    flat.sort()
    out = []
    for a, b in flat:
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    if closed and len(out) > 1 and out[0][0] <= 1e-9 and out[-1][1] >= L - 1e-9:
        out = [(out[-1][0], out[0][1] + L)] + out[1:-1]
    return out


def _apply_planes(out, srcs, cs, removed) -> list:
    reps = [{'profile': c.get('profile', ''), 'op': 'plane', 'applied': True, 'sources': {}} for c in cs]
    sel = [set(selection(out, c)) for c in cs]
    todo = []
    for sid in sorted(set().union(*sel)):
        mine = [k for k in range(len(cs)) if sid in sel[k]]
        d = srcs.get(sid)
        if d is None or sid in removed:
            continue
        if any(cs[k].get('remove_all') for k in mine):
            ivs, L, pts, closed = None, 0.0, None, None
        else:
            pts, closed = _processed(d, out)
            ivs, L = [], 0.0
            for k in mine:
                iv, L = removed_intervals(pts, closed, cs[k]['point'], cs[k]['normal'])
                ivs += iv
            ivs = _union(ivs, L, closed)
        todo.append((sid, mine, d, pts, closed, ivs, L))
    # sources the planes remove WHOLE go first: a wall attached to a ring is
    # no reason to open the ring when that wall is gone at this Z too
    for sid, mine, d, pts, closed, ivs, L in todo:
        if ivs is None or L - sum(b - a for a, b in ivs) < _min_kept(out, sid):
            removed.add(sid)
            for k in mine:
                reps[k]['sources'][sid] = 'removed'
    for sid, mine, d, pts, closed, ivs, L in todo:
        if sid in removed:
            continue

        def mark(st):
            for k in mine:
                reps[k]['sources'][sid] = st
        c = cs[mine[0]] if len(mine) == 1 else dict(cs[mine[0]], profile='+'.join(cs[k].get('profile', '') for k in mine))
        r = {'sources': {}}
        st = _cut_one(out, srcs, d, sid, pts, closed, ivs, L, c, removed, r)
        mark(st)
        for k in mine:                                 # where this source now ENDS (its source line)
            if r.get('ends'):
                reps[k].setdefault('ends', {})[sid] = r['ends']
            if r.get('corner_min'):
                reps[k].setdefault('corner_min', []).append(sid)
    return reps


def _pin_trims(out):
    """Freeze each Trim to the arc interval it resolves to in the authored
    design (`pinned`); a trim that resolves to nothing there removes nothing
    and is dropped."""
    if not out.get('trims'):
        return
    from app import _deserialise_layer
    st = _deserialise_layer(out).resolved_trims()
    keep = []
    for t in out['trims']:
        i = st.get(t.get('id')) or {}
        if i.get('status') == 'ok' and i.get('a') is not None:
            keep.append(dict(t, pinned=[i['a'], i['b'], i.get('pa'), i.get('pb')]))
    out['trims'] = keep


def _trim_ivs(out, sid, L, closed) -> list:
    """[(a, b, pa, pb, record)] — the source's pinned trims (closed: b may
    pass L)."""
    out_ = []
    for t in out.get('trims') or []:
        if t.get('source_path_id') == sid and t.get('pinned'):
            a, b, pa, pb = (list(t['pinned']) + [None, None])[:4]
            out_.append((float(a), float(b), pa, pb, t))
    return out_


def _uncovered(lo, hi, covers) -> list:
    """Sub-intervals of [lo, hi] not covered by `covers` [(a, b)]."""
    out, cur = [], lo
    for a, b in sorted(covers):
        if b <= cur or a >= hi:
            continue
        if a > cur + 1e-9:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < hi - 1e-9:
        out.append((cur, hi))
    return out


def _cut_one(out, srcs, d, sid, pts, closed, ivs, L, c, removed, r) -> str:
    """Cut one source by its (unioned) removed stretches: returns its status."""
    side = None                      # (a ring opened into a polyline: its wall side, left / right)
    if True:
        if not ivs:
            return 'kept'
        ivs = _drop_stubs(out, sid, ivs, L, closed)
        trims = _trim_ivs(out, sid, L, closed)
        tcov = [(a, b) for a, b, *_ in trims]
        ivs, grown = _corner_minimum(pts, closed, ivs, L, _thicknesses(out).get(sid, 0.0))
        if grown:
            r.setdefault('corner_min', []).append(sid)
        both = _union(list(ivs) + tcov, L, closed)
        intrim = lambda v: any(a - 1e-9 <= v <= b + 1e-9 or a - 1e-9 <= v + L <= b + 1e-9 for a, b in tcov)
        if L - sum(b - a for a, b in both) >= _min_kept(out, sid):
            r['ends'] = [_at_arc(pts, closed, v % L if closed else v) for a, b in ivs for v in (a, b)
                         if (closed or 1e-9 < v < L - 1e-9) and not intrim(v % L if closed else v)]
        if L - sum(b - a for a, b in both) < _min_kept(out, sid):
            removed.add(sid)
            return 'removed'
        if closed and not _attached_in(out, sid, pts, ivs, L, removed) and not _corner_in(pts, ivs, L):
            # (nothing attaches where it is removed: ordinary Openings — the
            # kept ring is exactly the Designer's own geometry)
            for k, (a, b) in enumerate(ivs):
                out.setdefault('openings', []).append({
                    'id': f"asm-{c.get('profile', '')}-{sid}-{k}", 'type': 'opening', 'source_path_id': sid,
                    'center_s': (0.5 * (a + b)) % L, 'width': b - a, 'end_treatment': 'inherit',
                    'z_min': None, 'z_max': None, 'label': 'Assembly planar cut'})
            return 'opened'
        if closed:
            # a wall ATTACHES where the ring is removed: the ring becomes the
            # OPEN polyline of what it keeps (an Opening would leave junction
            # detection seeing the removed wall — the attached end would stay
            # joined, uncapped, the route open). Re-parametrised to start in the
            # middle of its largest removed stretch; the other removed
            # stretches become interior openings.
            big = max(ivs, key=lambda iv: iv[1] - iv[0])
            s0 = 0.5 * (big[0] + big[1]) % L
            ring = list(pts) + [pts[0]]
            rot = _sub_polyline(ring + ring[1:], s0, s0 + L)
            from model import Vec2
            # the ring's wall was aligned INSIDE / OUTSIDE (auto on a closed
            # path = inside) — meaningless on an open path (auto = centred):
            # the pieces keep the ring's EXACT line and its wall side as an
            # explicit left / right (same direction), so their faces, contact
            # points and trims are exactly the ring's
            side = _ring_wall_side(out, sid, list(pts))
            pts = [Vec2(x, y) for x, y in rot]
            ivs = [((a - s0) % L, (a - s0) % L + (b - a)) for a, b in ivs]
            ivs = sorted([(a, min(b, L)) for a, b in ivs] + [(0.0, b - L) for a, b in ivs if b > L + 1e-9])
            tr2 = []
            for a, b, pa, pb, t in trims:                  # the pinned trims follow the new frame
                a2 = (a - s0) % L
                b2 = a2 + (b - a)
                if b2 > L + 1e-9:
                    tr2 += [(a2, L, pa, None, t), (0.0, b2 - L, None, pb, t)]
                else:
                    tr2.append((a2, min(b2, L), pa, pb, t))
            trims = tr2
            for o in out.get('openings') or []:           # the ring's own openings follow
                if o.get('source_path_id') == sid:
                    o['center_s'] = (float(o.get('center_s', 0.0)) - s0) % L
            closed = False
            d['closed'] = False
        # EVERY KEPT STRETCH becomes a piece: the first keeps the source id,
        # the others are sibling sources `<id>~p<k>` (same wall, same Wall
        # System, an owned web cloned with them) — no Opening is left across a
        # corner, whatever the planes leave
        kept_iv, cur = [], 0.0
        for a_, b_ in ivs:
            if a_ > cur + 1e-9:
                kept_iv.append((cur, a_))
            cur = max(cur, b_)
        if cur < L - 1e-9:
            kept_iv.append((cur, L))
        mk = _min_kept(out, sid)
        # a KEPT piece around a corner needs a wall thickness on each leg too
        # (a shorter leg's faces mitre past the cut): that leg goes, with a
        # thickness beyond the corner (a little more removed — below the plane)
        th = _thicknesses(out).get(sid, 0.0)
        if th > 0:
            ccum, acc_ = [], 0.0
            for i in range(len(pts)):
                ccum.append(acc_)
                if i + 1 < len(pts):
                    acc_ += pts[i].dist(pts[i + 1])
            cpos = []
            for i in range(1, len(pts) - 1):
                u_, v_ = pts[i] - pts[i - 1], pts[i + 1] - pts[i]
                lu, lv = u_.length(), v_.length()
                if lu > 1e-9 and lv > 1e-9 and math.acos(max(-1.0, min(1.0, (u_.x * v_.x + u_.y * v_.y) / (lu * lv)))) >= math.radians(30.0):
                    cpos.append(ccum[i])
            fixed = []
            for a_, b_ in kept_iv:
                for cp in cpos:
                    if a_ < cp < b_ and cp - a_ < th:
                        a_ = cp + th
                        r.setdefault('corner_min', []).append(sid)
                    if a_ < cp < b_ and b_ - cp < th:
                        b_ = cp - th
                        r.setdefault('corner_min', []).append(sid)
                fixed.append((a_, b_))
            kept_iv = fixed
        kept_iv = [(a_, b_) for a_, b_ in kept_iv if b_ - a_ >= mk]
        # TRIMS stay authoritative inside every piece: a piece that only Trim
        # had left (nothing ≥ the minimum outside its trims) goes; a short
        # stretch between a plane cut and a trim goes too — the piece then
        # starts / ends inside the trim, so that end stays the trim's free end
        tcov = [(a, b) for a, b, *_ in trims]
        fixed = []
        for a_, b_ in kept_iv:
            un = [u for u in _uncovered(a_, b_, tcov) if u[1] - u[0] >= mk]
            if not un:
                continue
            if un[0][0] > a_ + 1e-9:                    # start at the trim before the first real stretch
                a_ = next((max(a, a_) for a, b in sorted(tcov, key=lambda iv: -iv[1])
                           if a < un[0][0] - 1e-9 and b >= un[0][0] - 1e-9), a_)
            if un[-1][1] < b_ - 1e-9:
                b_ = next((min(b, b_) for a, b in sorted(tcov)
                           if b > un[-1][1] + 1e-9 and a <= un[-1][1] + 1e-9), b_)
            fixed.append((a_, b_))
        kept_iv = fixed
        if not kept_iv:
            removed.add(sid)
            return 'removed'
        out['trims'] = [t for t in out.get('trims') or [] if t.get('source_path_id') != sid]
        own = [o for o in out.get('openings') or [] if o.get('source_path_id') == sid]
        out['openings'] = [o for o in out.get('openings') or [] if o.get('source_path_id') != sid]
        base = copy.deepcopy(d)
        for k, (k0, k1) in enumerate(kept_iv):
            kept = _sub_polyline(pts, k0, k1)
            piece = d if k == 0 else copy.deepcopy(base)
            if side:
                piece['align_override'] = side
            pid = sid if k == 0 else f'{sid}~p{k}'
            if piece.get('type') == 'LinePath':
                piece['start'], piece['end'] = kept[0], kept[-1]
            else:
                for key in ('cx', 'cy', 'radius', 'rx', 'ry', 'start', 'end', 'control', 'x', 'y', 'w', 'h'):
                    piece.pop(key, None)
                piece.update({'type': 'ExplicitPath', 'control_points': kept, 'points': kept, 'closed': False,
                              'corner_radius': 0.0})
            if k:
                piece['id'] = pid
                out['source_paths'].append(piece)
                srcs[pid] = piece
                for ws in out.get('wall_systems') or []:
                    if sid in [str(m) for m in ws.get('members') or []]:
                        ws['members'] = list(ws['members']) + [pid]
                for f in list(out.get('infills') or []):
                    if f.get('path_id') == sid and f.get('owner'):
                        out['infills'].append(dict(f, id=f"{f['id']}~p{k}", path_id=pid))
            for o in own:                                   # the source's own openings follow their piece
                cs = float(o.get('center_s', 0.0))
                if k0 - 1e-9 <= cs <= k1 + 1e-9:
                    out['openings'].append(dict(o, source_path_id=pid, center_s=cs - k0))
            for j, (a, b, pa, pb, t) in enumerate(trims):      # … and its pinned trims, in its own frame
                lo, hi = max(a, k0), min(b, k1)
                if hi - lo <= 1e-9:
                    continue
                out.setdefault('trims', []).append(dict(
                    t, id=f"{t['id']}~{k}.{j}", source_path_id=pid,
                    pinned=[lo - k0, hi - k0, pa if abs(lo - a) <= 1e-9 else None, pb if abs(hi - b) <= 1e-9 else None]))
        r.setdefault('pieces', {})[sid] = len(kept_iv)
        return 'trimmed'


def _drop_stubs(doc, sid, ivs, L, closed) -> list:
    """A plane cut and the source's other openings: an opening overlapping a
    removed stretch merges into it, and no piece shorter than the
    construction's printable minimum may be left between them (a stub's end
    motifs bridge) — such a stub and the opening beyond it go with the cut."""
    mk = _min_kept(doc, sid)
    ops = [(float(o.get('center_s', 0.0)) - 0.5 * float(o.get('width', 0.0)),
            float(o.get('center_s', 0.0)) + 0.5 * float(o.get('width', 0.0)))
           for o in doc.get('openings') or [] if o.get('source_path_id') == sid]
    if not ops:
        return ivs
    plane = list(ivs)
    allv = _union(plane + ops, L, closed)

    def touches_plane(iv):
        return any(min(iv[1], b) - max(iv[0], a) > -1e-9 for a, b in plane) or \
            (closed and any(min(iv[1], b + L) - max(iv[0], a + L) > -1e-9 or
                            min(iv[1] + L, b) - max(iv[0] + L, a) > -1e-9 for a, b in plane))
    # an opening OVERLAPPING a removed stretch merges into it (else it would
    # reach past the new end, and reappear whole the layer the cut passes
    # its centre)
    add = [iv for iv in ops if touches_plane(iv)]
    n = len(allv)
    pairs = [(allv[k], allv[(k + 1) % n]) for k in range(n if closed else n - 1)] if n > 1 else []
    for (a0, b0), (a1, b1) in pairs:
        gap = (a1 - b0) % L if closed else a1 - b0
        if 1e-9 < gap < mk and (touches_plane((a0, b0)) or touches_plane((a1, b1))):
            add += [(b0, b0 + gap), (a0, b0), (a1, b1)]
    return _union(plane + add, L, closed) if add else ivs


def _at_arc(pts, closed, sv):
    """[x, y] at arc length sv of the polyline."""
    ring = list(pts) + ([pts[0]] if closed else [])
    acc = 0.0
    for a, b in zip(ring, ring[1:]):
        Ls = a.dist(b)
        if acc + Ls >= sv - 1e-12:
            q = a.lerp(b, 0.0 if Ls < 1e-12 else max(0.0, min(1.0, (sv - acc) / Ls)))
            return [q.x, q.y]
        acc += Ls
    return [ring[-1].x, ring[-1].y]


def _corner_minimum(pts, closed, ivs, L, th) -> tuple:
    """A removed stretch that contains a CORNER reaches at least one wall
    thickness along each leg from it: square cut faces closer to a corner
    would cross inside the wall (a sliver the end walls cannot close). Such
    a layer removes a little more than the plane needs — its terminations
    there lie below the plane (reported). (intervals, grown?)"""
    if th <= 0:
        return ivs, False
    n = len(pts)
    ring = list(pts) + ([pts[0]] if closed else [])
    cum = [0.0]
    for a, b in zip(ring, ring[1:]):
        cum.append(cum[-1] + a.dist(b))
    corners = []
    for i in (range(n) if closed else range(1, n - 1)):
        a, m, b = pts[i - 1], pts[i], pts[(i + 1) % n]
        u, v = m - a, b - m
        lu, lv = u.length(), v.length()
        if lu > 1e-9 and lv > 1e-9 and \
                math.acos(max(-1.0, min(1.0, (u.x * v.x + u.y * v.y) / (lu * lv)))) >= math.radians(30.0):
            corners.append(cum[i])
    out, grown = [], False
    for a, b in ivs:
        for c in corners:
            for k in ((-1, 0, 1) if closed else (0,)):
                c_ = c + k * L
                if a - 1e-9 <= c_ <= b + 1e-9 and (c_ - a < th or b - c_ < th):
                    a, b, grown = min(a, c_ - th), max(b, c_ + th), True
        out.append((a if closed else max(0.0, a), b if closed else min(L, b)))
    return (_union(out, L, closed), True) if grown else (ivs, False)


def _corner_in(pts, ivs, L) -> bool:
    """Does a removed stretch contain a CORNER of the ring (an Opening across
    a corner is degenerate — the Designer cannot rebuild its faces there)?"""
    n = len(pts)
    cum, acc = [], 0.0
    for i in range(n):
        cum.append(acc)
        acc += pts[i].dist(pts[(i + 1) % n])
    for i in range(n):
        a, m, b = pts[i - 1], pts[i], pts[(i + 1) % n]
        u, v = m - a, b - m
        lu, lv = u.length(), v.length()
        if lu < 1e-9 or lv < 1e-9:
            continue
        if math.acos(max(-1.0, min(1.0, (u.x * v.x + u.y * v.y) / (lu * lv)))) < math.radians(30.0):
            continue
        if any(lo - 1e-9 <= cum[i] <= hi + 1e-9 or lo - 1e-9 <= cum[i] + L <= hi + 1e-9 for lo, hi in ivs):
            return True
    return False


def _ring_wall_side(doc, sid, pts):
    """'left' / 'right' (of the ring's own direction): the side its inside /
    outside wall lies on — or None (a centred / single-bead wall)."""
    th = _thicknesses(doc).get(sid, 0.0)
    if th <= 0:
        return None
    align = None
    for ws in doc.get('wall_systems') or []:
        if sid in [str(m) for m in ws.get('members') or []] and ws.get('thickness'):
            align = ws.get('align') or 'auto'
    if align is None:
        d = next((x for x in doc.get('source_paths') or [] if x.get('id') == sid), {}) or {}
        align = ((d.get('wall') or {}).get('align')) or 'auto'
    if align == 'auto':
        align = 'inside'
    n = len(pts)
    ccw = sum(pts[i].x * pts[(i + 1) % n].y - pts[(i + 1) % n].x * pts[i].y for i in range(n)) > 0
    return {'left': 'left', 'right': 'right', 'inside': 'left' if ccw else 'right',
            'outside': 'right' if ccw else 'left'}.get(align)


def _attached_in(doc, sid, pts, ivs, L, gone=()) -> bool:
    """Does another (surviving) source's END attach to `sid` inside a
    removed stretch?"""
    try:
        import semantic_transform as ST
        att = ST.attachments(doc)
    except Exception:
        return False
    for (other, _end), a in att.items():
        if other == sid or other in gone or a[0] != 'T' or a[1] != sid:
            continue
        sv, dist = _project_s(pts, True, a[2])
        if any(lo <= sv <= hi or lo <= sv + L <= hi for lo, hi in ivs):
            return True
    return False


def source_list(doc) -> list:
    """Every wall a PLANAR TOP CUT can select: {source, label, closed,
    system, system_label, points (a coarse centre line, design coords),
    lattice: the design prints a Wall Infill lattice (only then do the cut's
    lattice STATIONS change anything — layer_design._cut_join)}."""
    sys_of = {}
    # (a Wall Group document: the WALL GROUP — what a planar cut selects)
    for ws in doc.get('wall_groups') if 'wall_groups' in doc else (doc.get('wall_systems') or []):
        for m in ws.get('members') or []:
            sys_of.setdefault(str(m), (ws.get('id'), ws.get('name') or ws.get('id')))
    lattice = any((f.get('kind') or 'wall') == 'wall' for f in doc.get('infills') or [])
    out = []
    for d in doc.get('source_paths') or []:
        sid = d.get('id')
        if not sid or d.get('visible') is False or (d.get('type') == 'InsetPath' and not d.get('points')):
            continue
        try:
            pts, closed = _processed(d, doc)
        except Exception:
            continue
        step = max(1, len(pts) // 64)
        sm = [[round(q.x, 4), round(q.y, 4)] for q in pts[::step]]
        if not closed and pts:
            sm.append([round(pts[-1].x, 4), round(pts[-1].y, 4)])
        sy = sys_of.get(sid)
        out.append({'source': sid, 'label': d.get('label') or sid, 'closed': bool(closed),
                    'system': sy[0] if sy else None, 'system_label': sy[1] if sy else None, 'points': sm,
                    'lattice': lattice})
    return out


def plane_probes(doc, c, width) -> list:
    """Narrow probe cuts (center_s) at the middle of every stretch the plane
    cut `c` removes — the lattice STATION of a planar cut."""
    out = []
    srcs = {d.get('id'): d for d in doc.get('source_paths') or []}
    for sid in selection(doc, c):
        d = srcs.get(sid)
        if d is None:
            continue
        pts, closed = _processed(d, doc)
        ivs, L = removed_intervals(pts, closed, c['point'], c['normal'])
        for a, b in ivs:
            out.append({'op': 'opening', 'source': sid, 'edge': None, 'center_s': (0.5 * (a + b)) % L if closed else 0.5 * (a + b),
                        'width': width, 'profile': 'probe'})
    return out


def _num(o):
    """Numbers as floats (booleans kept): a spec sent back by the page has
    been through JavaScript, which writes 12.0 as 12."""
    if isinstance(o, bool):
        return o
    if isinstance(o, int):
        return float(o)
    if isinstance(o, dict):
        return {k: _num(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_num(v) for v in o]
    return o


def cut_key(cuts) -> str:
    """A cache key of a cut spec — the same for the same numbers whether
    they arrive as ints or floats (2026-10-10 performance pass: the page's
    variant request, a JSON round trip of the resolve's spec, never matched
    the variants the resolve had just built and rebuilt every one)."""
    import json
    return json.dumps(sorted(json.dumps(_num(c), sort_keys=True) for c in cuts or []))
