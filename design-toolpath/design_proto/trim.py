"""
Trim — non-destructive suppression of SECTIONS of source paths (CAD Trim).

A section is the stretch of a source path between two consecutive contacts
with OTHER source paths, or between a contact and an open end of the path.
Contacts are found on the DESIGN geometry — the processed source polylines
(sampled + Corner R + end snapping), before any wall offsets — with the
same segment-contact primitives the wall networks use (network.py).
Self-intersections of a path do not split it.

A trim never edits the source. It is stored as a SIGNATURE of the section
(model.Trim): the sources bounding it at each end, and whether its midpoint
lies inside each closed bounding source. On every build the signature is
matched against the current sections, so a trim follows its section
through moves / resizes / reshapes of either path. u_mid (the midpoint as a
fraction of the source's length) only breaks ties between sections with
the same signature. No match, or a tie u_mid cannot break → the trim is
UNRESOLVED: its record is kept and nothing is suppressed — a trim is never
moved onto an unrelated section.

Sections are always computed from the UNTRIMMED design geometry, so trims
are independent of each other and of their order.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

import network as N
from model import (_cum_lengths, _locate_s, _sub_polyline, _project_to_polyline,
                   _point_in_polygon)

SPLIT_MERGE = 1e-4      # contacts closer than this along a path are one split


@dataclass
class Section:
    source: str
    index: int
    a: float                 # arc interval on the source (closed: b may pass L)
    b: float
    L: float
    pa: object               # exact contact point at a / b (shared by every
    pb: object               # path meeting there; None at an open end)
    start: list              # bounding source ids at a ([] = open path end)
    end: list
    inside: dict             # closed bounding source id → midpoint inside it
    u_mid: float
    pts: list
    trimmed_by: Optional[str] = None

    def matches(self, start, end, inside) -> bool:
        return (_pair(self.start, self.end) == _pair(start, end) and
                self.inside == {str(k): bool(v) for k, v in (inside or {}).items()})

    def contains_u(self, u: float) -> bool:
        s = (u % 1.0) * self.L
        return self.a - 1e-9 <= s <= self.b + 1e-9 or \
            self.a - 1e-9 <= s + self.L <= self.b + 1e-9

    def to_dict(self) -> dict:
        return {'id': f'{self.source}:{self.index}', 'source': self.source,
                'start': self.start, 'end': self.end, 'inside': self.inside,
                'u_mid': self.u_mid, 'a': self.a, 'b': self.b,
                'pts': [[q.x, q.y] for q in self.pts],
                'trimmed_by': self.trimmed_by}


def _pair(start, end):
    """Bounding sources as an UNORDERED pair (a path's direction is not
    part of a section's identity)."""
    return tuple(sorted([tuple(sorted(start)), tuple(sorted(end))]))


def contacts(sources, processed, tol=N.TOL) -> dict:
    """{source id: [(s, {other source ids}, point)]} — every point where a
    source touches or crosses another source (crossings, T / endpoint-on-
    path, shared vertices), as arc length along it, merged within
    SPLIT_MERGE. `point` is ONE representative per physical contact, shared
    by every path meeting there, so trimmed ends meet exactly."""
    beads = [N.Bead(p.id, processed[p.id], p.closed, N.WIRE, p.id)
             for p in sources if len(processed.get(p.id, ())) >= 2]
    segs = N._seg_list(beads)
    grid = N._Grid()
    for k, (_, _, a, b) in enumerate(segs):
        grid.insert(k, (min(a.x, b.x) - tol, max(a.x, b.x) + tol,
                        min(a.y, b.y) - tol, max(a.y, b.y) + tol))
    cand = [ev[2] for ev in N._contact_events(segs, beads, tol)]
    seen: dict = {}
    for bi, bd in enumerate(beads):
        if not bd.closed:
            cand += [bd.pts[0], bd.pts[-1]]
        for q in bd.pts:                       # vertices shared by two paths
            seen.setdefault((round(q.x, 6), round(q.y, 6)), (set(), q))[0].add(bi)
    cand += [q for owners, q in seen.values() if len(owners) >= 2]
    reps: list = []                              # one point per contact
    for q in cand:
        if all(q.dist(r) > tol for r in reps):
            reps.append(q)
    cand = reps

    cums = [_cum_lengths(bd.pts, bd.closed) for bd in beads]
    raw: dict = {bd.id: [] for bd in beads}
    for pt in cand:
        near = set()
        for k in grid.query((pt.x - tol, pt.x + tol, pt.y - tol, pt.y + tol)):
            bi, _, a, b = segs[k]
            if N._pt_seg(pt, a, b)[0] <= tol:
                near.add(bi)
        if len(near) < 2:
            continue                             # self-contact only
        for bi in near:
            bd = beads[bi]
            s = _project_to_polyline(pt, bd.pts, cums[bi], bd.closed)[0]
            L = cums[bi][-1]
            if bd.closed and L > 0:
                s %= L
            raw[bd.id].append((s, {beads[j].id for j in near if j != bi}, pt))

    out = {}
    for bi, bd in enumerate(beads):
        L = cums[bi][-1]
        merged: list = []
        for s, ids, pt in sorted(raw[bd.id], key=lambda e: e[0]):
            if merged and s - merged[-1][0] <= SPLIT_MERGE:
                merged[-1][1].update(ids)
            else:
                merged.append([s, set(ids), pt])
        if bd.closed and len(merged) > 1 and merged[0][0] + L - merged[-1][0] <= SPLIT_MERGE:
            merged[0][1].update(merged.pop()[1])
        if not bd.closed:                        # contacts at an end are ends
            merged = [m for m in merged if SPLIT_MERGE < m[0] < L - SPLIT_MERGE]
        out[bd.id] = [tuple(m) for m in merged]
    return out


def sections(sources, processed) -> dict:
    """{source id: [Section]} — the trimmable sections of every source.
    A closed path needs ≥ 2 contacts, an open path ≥ 1 (a path with no
    contact is one whole object: Delete it rather than trim it)."""
    by_id = {p.id: p for p in sources}
    out = {}
    for pid, splits in contacts(sources, processed).items():
        p, pts = by_id[pid], processed[pid]
        cum = _cum_lengths(pts, p.closed)
        L = cum[-1]
        if L <= 1e-9:
            continue
        if p.closed:
            if len(splits) < 2:
                continue
            bounds = [(splits[i], splits[(i + 1) % len(splits)]) for i in range(len(splits))]
        else:
            if not splits:
                continue
            ends = [(0.0, set(), None)] + list(splits) + [(L, set(), None)]
            bounds = list(zip(ends[:-1], ends[1:]))
        secs = []
        for k, ((a, ida, pa), (b, idb, pb)) in enumerate(bounds):
            if p.closed and b <= a:
                b += L
            mid_s = (a + b) / 2.0
            mid, _ = _locate_s(pts, cum, mid_s % L if p.closed else mid_s, p.closed)
            inside = {}
            for c in sorted(ida | idb):
                q = by_id.get(c)
                if q is not None and q.closed and len(processed.get(c, ())) >= 3:
                    inside[c] = _point_in_polygon(mid, processed[c])
            sub = _sub_polyline(pts, cum, a, b, p.closed)
            for idx, q in ((0, pa), (-1, pb)):     # exact shared contact points
                if q is not None and len(sub) >= 2 and q.dist(sub[idx]) <= 1e-6:
                    sub[idx] = q
            secs.append(Section(pid, k, a, b, L, pa, pb, sorted(ida), sorted(idb), inside,
                                (mid_s % L) / L, sub))
        out[pid] = secs
    return out


def resolve(trims, secs: dict, source_ids) -> tuple[dict, dict]:
    """Match each trim to the section it names. Returns
    ({source id: [(a, b, pa, pb)] removed intervals + exact end points},
     {trim id: {'status': 'ok' | 'unresolved' | 'ambiguous' | 'missing source',
                'reason', 'source', 'section'}})."""
    removed: dict = {}
    status: dict = {}
    for t in trims:
        info = {'source': t.source_path_id, 'section': None}
        status[t.id] = info
        if t.source_path_id not in source_ids:
            info.update(status='missing source', reason='its path no longer exists')
            continue
        cands = [s for s in secs.get(t.source_path_id, [])
                 if s.matches(t.start, t.end, t.inside)]
        if len(cands) > 1:
            cands = [s for s in cands if s.contains_u(t.u_mid)]
            if len(cands) != 1:
                info.update(status='ambiguous',
                            reason='several sections between the same paths match — not trimmed')
                continue
        if not cands:
            info.update(status='unresolved',
                        reason='the intersections that bounded it no longer exist — not trimmed')
            continue
        sec = cands[0]
        info.update(status='ok', section=f'{sec.source}:{sec.index}')
        if sec.trimmed_by is None:
            sec.trimmed_by = t.id
            removed.setdefault(sec.source, []).append((sec.a, sec.b, sec.pa, sec.pb))
    return removed, status
