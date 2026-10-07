"""
SOURCE ATTRIBUTION — which SOURCE PATH (wall) every vertex of a design's
printable geometry belongs to. Designer semantics, exported to the Layer
Assembly as opaque per-vertex tags: the Assembly uses them to SEE which
transform group a vertex belongs to (preview colours, a support finding's
local frame) — never to move geometry (groups move SOURCES, Designer-side:
semantic_transform.py). layer_assembly/designer_source.py is the only
caller.

RULE (V1, deterministic):
  * A printable strand that is a face / cap of a source path (the
    Designer's own path identity, BuiltLayer.strand_sources) belongs to
    that source as a whole.
  * Every other strand (wall lattice, junction fill, solid infill …) is
    attributed PER VERTEX by the JUNCTION SEAM rule (decision 2026-10-06):
    the owner is the wall minimising  distance to its MATERIAL centre line
    / its half thickness  (the material centre line = the source offset by
    half the thickness towards its wall side; the source itself for a
    centred wall). At an ordinary corner that equal-ratio locus is exactly
    the MITER line through the inner and outer corners, so ownership splits
    cleanly along the natural division of the two walls' material. At a T
    (an end attached to a host, semantic_transform.attachments) the
    branch's centre line is trimmed where it enters the host's material,
    so the host keeps its full thickness and the branch owns from the
    host's face outward. Ties → the smaller source id. Never transform-
    centre distance. (Without wall data: the nearest attributed face.)
This is attribution of geometry to the wall it is part of — not an
assignment of geometry to transform centres (that is explicit, Assembly
side).
"""
from __future__ import annotations

import math
from collections import defaultdict


def _seg_d(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)



def _centrelines(doc):
    from app import _deserialise_layer
    lay = _deserialise_layer(dict(doc, infills=[], openings=[], trims=[]))
    out = []
    for p in lay.source_paths:
        pts = [(q.x, q.y) for q in p.sample_points()]
        if p.closed and len(pts) > 2:
            pts.append(pts[0])
        out.append((p.id, pts))
    return out


def attribute(document, printable, strand_sources) -> tuple:
    """(tags, labels): tags = [[source id per vertex] per printable
    polyline] (aligned with its 'pts'); labels = {source id: label}.
    strand_sources: {strand id: source path id} from the build
    (BuiltLayer.strand_sources)."""
    srcs = document.get('source_paths') or []
    labels = {s['id']: (s.get('label') or s['id']) for s in srcs}
    named = [strand_sources.get(pl.get('id')) if strand_sources.get(pl.get('id')) in labels else None
             for pl in printable]
    ref = []                                        # (source, a, b) reference segments
    for pl, sid in zip(printable, named):
        if sid is None:
            continue
        pts = [tuple(p) for p in pl['pts']]
        if pl.get('closed') and len(pts) > 2:
            pts.append(pts[0])
        ref += [(sid, a, b) for a, b in zip(pts, pts[1:])]
    if not ref and any(n is None for n in named):
        for sid, pts in _centrelines(document):
            ref += [(sid, a, b) for a, b in zip(pts, pts[1:])]
    cell = 12.0
    grid = defaultdict(list)
    for k, (_, a, b) in enumerate(ref):
        for gx in range(math.floor(min(a[0], b[0]) / cell), math.floor(max(a[0], b[0]) / cell) + 1):
            for gy in range(math.floor(min(a[1], b[1]) / cell), math.floor(max(a[1], b[1]) / cell) + 1):
                grid[gx, gy].append(k)

    def nearest(p):
        gx, gy = math.floor(p[0] / cell), math.floor(p[1] / cell)
        for r in range(0, 64):
            ks = {k for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                  if max(abs(dx), abs(dy)) == r for k in grid.get((gx + dx, gy + dy), ())}
            if ks or r == 63:
                # ring r found something: anything nearer lies within one more ring
                ks |= {k for dx in range(-r - 1, r + 2) for dy in range(-r - 1, r + 2)
                       for k in grid.get((gx + dx, gy + dy), ())}
                if not ks:
                    return None
                return min((_seg_d(p, ref[k][1], ref[k][2]), ref[k][0]) for k in ks)[1]
        return None

    seam = _seam_owner(document)
    tags = []
    for pl, sid in zip(printable, named):
        if sid is not None:
            tags.append([sid] * len(pl['pts']))
        else:
            tags.append([(seam(tuple(p)) if seam else None) or (nearest(tuple(p)) if ref else None)
                         for p in pl['pts']])
    return tags, labels


def _offset(pts, d, closed):
    """pts offset by d to the LEFT of their direction (miter joins)."""
    n = len(pts)
    if n < 2 or abs(d) < 1e-12:
        return list(pts)
    ring = pts[:-1] if closed and pts[0] == pts[-1] else pts
    m = len(ring)
    out = []
    for i in range(m):
        prv = ring[i - 1] if (closed or i > 0) else None
        nxt = ring[(i + 1) % m] if (closed or i < m - 1) else None
        ns = []
        for a, b in ((prv, ring[i]), (ring[i], nxt)):
            if a is None or b is None:
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L > 1e-12:
                ns.append((-dy / L, dx / L))
        if not ns:
            out.append(ring[i])
            continue
        nx, ny = sum(v[0] for v in ns), sum(v[1] for v in ns)
        L = math.hypot(nx, ny)
        if L < 1e-9:
            nx, ny = ns[0]
            L = 1.0
        nx, ny = nx / L, ny / L
        c = max(0.2, nx * ns[0][0] + ny * ns[0][1])           # miter scale 1/cos(half turn)
        out.append((ring[i][0] + nx * d / c, ring[i][1] + ny * d / c))
    if closed:
        out.append(out[0])
    return out


def _seam_owner(doc):
    """The junction-seam owner function (see RULE), or None."""
    import semantic_transform as ST
    srcs = [d for d in doc.get('source_paths') or [] if d.get('id')]
    if not srcs:
        return None
    mat = doc.get('material') or {}
    bead = float(mat.get('bead_width') or 3.0)
    ret = float(mat.get('return_overlap') if mat.get('return_overlap') is not None else 0.75)
    walls = ST.effective_walls(doc)
    lines = {}
    for d in srcs:
        if d.get('type') == 'InsetPath' and not d.get('points'):
            continue
        pts = ST._centreline(d)
        if len(pts) < 2:
            continue
        t, align, kind = walls.get(d['id'], (0.0, 'auto', 'single'))
        if kind != 'wall':
            # a single bead (half a bead wide) or a return-lane pair
            # (two beads W − R apart: half width W − R/2)
            lines[d['id']] = (pts, bead - ret / 2 if kind == 'lanes' else bead / 2)
            continue
        closed = bool(d.get('closed')) and d.get('type') not in ST.OPEN_TYPES or d.get('type') in (
            'RectanglePath', 'CirclePath', 'EllipsePath', 'InsetPath')
        if align == 'auto':
            align = 'inside' if closed else 'center'
        if align in ('inside', 'outside') and not closed:
            align = 'left' if align == 'inside' else 'right'
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:])) / 2 if closed else 0.0
        ccw = area > 0
        sign = {'left': 1.0, 'right': -1.0, 'inside': 1.0 if ccw else -1.0,
                'outside': -1.0 if ccw else 1.0}.get(align, 0.0)
        lines[d['id']] = (_offset(pts, sign * t / 2, closed), t / 2)
    # T junctions: an attached end's centre line stops where it enters its host
    try:
        atts = ST.attachments(doc)
    except Exception:
        atts = {}
    for (sid, w), a in atts.items():
        if a[0] != 'T' or sid not in lines or a[1] not in lines:
            continue
        pts, h = lines[sid]
        hpts, hh = lines[a[1]]
        dense = []
        for p, q in zip(pts, pts[1:]):
            n = max(1, int(math.ceil(math.dist(p, q) / max(0.25, hh / 8))))
            dense += [(p[0] + (q[0] - p[0]) * j / n, p[1] + (q[1] - p[1]) * j / n) for j in range(n)]
        dense.append(pts[-1])
        if w == 1:
            dense.reverse()
        k = 0
        while k < len(dense) - 2 and min(_seg_d(dense[k], u, v) for u, v in zip(hpts, hpts[1:])) < hh:
            k += 1
        dense = dense[k:]
        if w == 1:
            dense.reverse()
        lines[sid] = (dense, h)
    segs = [(sid, a, b, h) for sid, (pts, h) in lines.items() for a, b in zip(pts, pts[1:])]
    if not segs:
        return None
    hmax = max(h for *_, h in segs)
    cell = max(hmax, 1.0)
    grid = defaultdict(list)
    for k, (_, a, b, _h) in enumerate(segs):
        for gx in range(math.floor(min(a[0], b[0]) / cell), math.floor(max(a[0], b[0]) / cell) + 1):
            for gy in range(math.floor(min(a[1], b[1]) / cell), math.floor(max(a[1], b[1]) / cell) + 1):
                grid[gx, gy].append(k)

    def owner(p):
        """min over walls of distance / half thickness: rings of cells
        outward until no unseen segment can beat the best ratio (exact)."""
        gx, gy = math.floor(p[0] / cell), math.floor(p[1] / cell)
        best, seen = None, set()
        for rr in range(0, 64):
            ks = []
            for dx in range(-rr, rr + 1):
                for dy in (range(-rr, rr + 1) if abs(dx) == rr else (-rr, rr)):
                    ks += grid.get((gx + dx, gy + dy), ())
            for k in ks:
                if k in seen:
                    continue
                seen.add(k)
                sid, a, b, h = segs[k]
                r = _seg_d(p, a, b) / h
                if best is None or r < best[0] - 1e-9 or (abs(r - best[0]) <= 1e-9 and sid < best[1]):
                    best = (r, sid)
            # anything still unseen is at least rr·cell away
            if best is not None and rr * cell >= best[0] * hmax + cell:
                break
        return best[1] if best else None
    return owner
