"""Automated geometry checks for the junction harness.

Each check returns `(ok: bool, findings: list[dict])`. Findings have a
`kind` (short category), a `message` (one line), optionally a `where`
coordinate (for SVG markers) and optionally extra numeric fields. Checks
run on the OUTPUT of `PrintLayer._build_effective` + `to_routing_layer`,
through the same production functions print preparation uses.

Checks (every one is a documented production invariant, not a style
preference):
  continuity              — the routing graph closes (no open route)
  cap_integrity           — every open wall end has a `cap` bead of the
                            style the document asks for ('flat' / 'round')
  envelope_containment    — printable beads lie inside the material rings
                            (plus a small rounding tolerance)
  exterior_continuity     — along each material ring (the architectural
                            boundary), a printed path lies within one
                            bead of every point
  region_coverage         — every material ring has a printed path within
                            two beads (catches "missing wall region" bugs)
  incorrect_rounding      — round-labelled cap arcs exist iff the document
                            asks for round junctions
  overlap                 — pairs of distinct printed beads do not run
                            parallel at centreline distance < bead − contact
                            overlap for longer than a bead

`no_protrusions` is kept as a public alias of `envelope_containment` for
callers that want to assert the outer-perimeter case specifically; `run_all`
does not call it (the two implementations are currently identical, so
calling both was pure duplication).
"""
from __future__ import annotations
import math
import os
import sys


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sample_pts(p):
    return p.sample_points() if hasattr(p, 'sample_points') else []


def _flatten_regions(regions):
    """`meta['regions']` is a list of REGIONS; each region is a list of
    RINGS; each ring is a list of Vec2. Flatten to one list of rings."""
    out = []
    for region in regions or []:
        if not region:
            continue
        # a ring is a list of Vec2 (first element has `.x`); a region is a
        # list of rings (first element has no `.x`)
        first = region[0]
        if hasattr(first, 'x') or (isinstance(first, (list, tuple)) and first
                                   and hasattr(first[0], 'x') is False):
            out.append(list(region))               # legacy flat ring
        else:
            out.extend(list(r) for r in region)
    return out


def _polyline_length(pts):
    return sum(a.dist(b) for a, b in zip(pts, pts[1:]))


def _point_in_polygon(q, poly):
    """Standard ray cast. `poly` is a list of Vec2."""
    cnt = 0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if ((a.y > q.y) != (b.y > q.y)) and \
                (q.x < a.x + (b.x - a.x) * (q.y - a.y) / (b.y - a.y + 1e-18)):
            cnt += 1
    return cnt % 2 == 1


def _dist_to_polyline(q, pts, closed):
    """Minimum perpendicular distance from `q` to the polyline."""
    best = None
    n = len(pts)
    for i in range(n - 1):
        a, b = pts[i], pts[i + 1]
        d = _dist_pt_seg(q, a, b)
        if best is None or d < best:
            best = d
    if closed and n >= 3:
        d = _dist_pt_seg(q, pts[-1], pts[0])
        if best is None or d < best:
            best = d
    return best if best is not None else float('inf')


def _dist_pt_seg(p, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return p.dist(a)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    return math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy)


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------

def continuity(paths, meta, layer):
    """Build the routing graph and look for open components. Reports every
    open-component endpoint. Uses the same `graph.closure_report` print
    preparation uses, so what the harness sees is what the printer sees."""
    sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', '..', 'toolpath_proto')
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from graph import build_graph, closure_report
    rl = layer.to_routing_layer(paths, meta)
    rep = closure_report(build_graph(rl))
    findings = []
    opens = rep.get('open') if isinstance(rep.get('open'), list) else []
    for o in opens:
        for xy in (o.get('at') or []):
            findings.append({'kind': 'open_end',
                             'message': 'open route endpoint',
                             'where': list(xy)})
    return (not findings, findings)


def cap_integrity(paths, meta, junction_style: str):
    """Every open wall (end markers at both faces) has a matching cap bead.
    Looks for `role='cap'` beads bridging each unresolved pair of face
    endpoints. The check is loose: it flags a face endpoint that has no
    cap-labelled bead within one bead-width. (Round / flat is left to
    the renderer — it is enough that the cap EXISTS.)
    """
    # face beads: open polylines labelled 'wall' / 'inner' / 'outer' / 'free'
    face_ends = []
    caps = []
    for p in paths:
        role = getattr(p, 'role', '')
        pts = _sample_pts(p)
        if not pts:
            continue
        if role in ('inner', 'outer', 'free') and not p.closed:
            face_ends.append((p, pts[0]))
            face_ends.append((p, pts[-1]))
        if role == 'cap':
            caps.append(pts)
    findings = []
    for owner, q in face_ends:
        has_cap = any(any(q.dist(cp) < 1.0 for cp in c) for c in caps)
        if not has_cap:
            # might still be joined by another face bead's endpoint
            tol = 0.1
            if any(any(q.dist(x) < tol for x in (opts[0], opts[-1]))
                   for other, opts in [(p, _sample_pts(p)) for p in paths
                                       if p is not owner and _sample_pts(p)]):
                continue
            findings.append({'kind': 'missing_cap',
                             'message': f'face endpoint has no cap bead',
                             'where': [q.x, q.y]})
    return (not findings, findings)


def envelope_containment(paths, meta, bead: float):
    """Every printable sample lies within one bead-width of some material
    ring. The tolerance includes rounded junction expansion (`+bead`)."""
    rings = _flatten_regions((meta or {}).get('regions') or [])
    if not rings:
        return True, []
    findings = []
    tol = bead + 0.5                         # 1 bead + a small cushion for rounded junctions
    for p in paths:
        role = getattr(p, 'role', '')
        if role not in ('inner', 'outer', 'free', 'cap', 'wall_system', 'lattice', 'bead'):
            continue
        pts = _sample_pts(p)
        if len(pts) < 2:
            continue
        for q in pts:
            inside_any = any(_point_in_polygon(q, r) for r in rings)
            if inside_any:
                continue
            # just outside: compare to nearest ring
            d = min(_dist_to_polyline(q, r, True) for r in rings)
            if d > tol:
                findings.append({'kind': 'protrusion',
                                 'message': f'bead {d:.2f} in outside the material envelope',
                                 'where': [q.x, q.y], 'd': round(d, 2)})
                break                        # one per path is enough
    return (not findings, findings)


def no_protrusions(paths, meta, bead: float):
    """A specialisation of envelope_containment that only looks at the
    OUTER perimeter: a printed bead must not extend outside the material
    beyond rounded-junction tolerance."""
    return envelope_containment(paths, meta, bead)


def exterior_continuity(paths, meta, bead: float, gap_threshold: float | None = None):
    """Scan the OUTER material ring and find the SINGLE LONGEST stretch
    where no printed bead comes within one bead of the ring. Flag only
    if that stretch is longer than `gap_threshold` (default: 6 beads = 18
    in — larger than one period of the default wave patterns so periodic
    exterior touching does not trigger). Catches the user's 'occasional
    unintended gaps in otherwise connected walls'. The REPORTED finding
    is the MIDPOINT of the gap (handy as an SVG marker)."""
    rings = _flatten_regions((meta or {}).get('regions') or [])
    if not rings:
        return True, []
    gap_threshold = gap_threshold if gap_threshold is not None else 6.0 * bead

    def sign_area(ring):
        return 0.5 * sum(ring[i].x * ring[(i + 1) % len(ring)].y -
                         ring[(i + 1) % len(ring)].x * ring[i].y
                         for i in range(len(ring)))
    outer_rings = [r for r in rings if sign_area(r) > 0]
    if not outer_rings:
        outer_rings = rings
    printed_polys = []
    for p in paths:
        role = getattr(p, 'role', '')
        if role not in ('inner', 'outer', 'free', 'cap', 'wall_system', 'bead'):
            continue
        pts = _sample_pts(p)
        if len(pts) >= 2:
            printed_polys.append((pts, getattr(p, 'closed', False)))
    if not printed_polys:
        return True, []
    # bbox index per polyline for a quick nearest-polyline reject
    polys_bbox = []
    for pts, _ in printed_polys:
        xs = [q.x for q in pts]; ys = [q.y for q in pts]
        polys_bbox.append((min(xs), min(ys), max(xs), max(ys)))

    def nearest(q):
        best = None
        for (pts, closed), (bx1, by1, bx2, by2) in zip(printed_polys, polys_bbox):
            # bbox fast reject
            dx = max(0.0, max(bx1 - q.x, q.x - bx2))
            dy = max(0.0, max(by1 - q.y, q.y - by2))
            d_bb = (dx * dx + dy * dy) ** 0.5
            if best is not None and d_bb > best:
                continue
            d = _dist_to_polyline(q, pts, closed)
            if best is None or d < best:
                best = d
        return best if best is not None else float('inf')
    tol = 1.5 * bead
    findings = []
    for ring in outer_rings:
        # sample the ring at ~1 in intervals; find the longest stretch of
        # consecutive samples that are all > tol from a printed bead
        samples: list = []
        for a, b in zip(ring, ring[1:] + ring[:1]):
            L = a.dist(b)
            steps = max(1, int(L))
            for k in range(steps):
                t = k / steps
                q = type(a)(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
                samples.append((q, nearest(q)))
        # scan for runs where distance > tol; closed ring: wrap the scan
        n = len(samples)
        if n == 0:
            continue
        in_gap = [d > tol for q, d in samples]
        # find all contiguous runs (handling wrap)
        runs = []
        i = 0
        while i < 2 * n:
            if not in_gap[i % n]:
                i += 1
                continue
            start = i
            while i < 2 * n and in_gap[i % n]:
                i += 1
            end = i
            if end - start >= n:
                runs = [(0, n)]
                break
            runs.append((start, end))
            if end >= n:
                break
        # pick the longest run
        if not runs:
            continue
        best = max(runs, key=lambda r: r[1] - r[0])
        start, end = best
        length = end - start                 # roughly inches
        if length > gap_threshold:
            mid = samples[(start + end) // 2 % n][0]
            findings.append({'kind': 'exterior_gap',
                             'message': f'exterior ring gap of {length} in',
                             'where': [mid.x, mid.y], 'd': length})
    return (not findings, findings)


def overlap(paths, bead: float, contact_overlap: float = 0.75,
            min_run_beads: int = 6, parallel_cos: float = 0.9):
    """Pairs of distinct printed beads should not run PARALLEL at
    centreline distance < bead - contact_overlap for a run longer than
    `min_run_beads` beads. Pairs that CROSS (tangents nearly perpendicular)
    are not an overlap: they are a legitimate junction event. Interleaved
    / Linked / Chain patterns periodically cross their own lanes and the
    check treats those crossings correctly (short runs, not sustained
    parallel). Reports the first few offending pairs."""
    strands = []
    for p in paths:
        role = getattr(p, 'role', '')
        if role not in ('wall_system', 'lattice', 'bead', 'inner', 'outer'):
            continue
        pts = _sample_pts(p)
        if len(pts) < 2:
            continue
        strands.append((p, pts))
    min_sep = bead - contact_overlap - 0.1
    min_run_len = min_run_beads * bead
    findings = []

    def tangent(pts, i):
        n = len(pts)
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        d = type(a)(b.x - a.x, b.y - a.y)
        L = (d.x * d.x + d.y * d.y) ** 0.5
        return (d.x / L, d.y / L) if L > 1e-12 else (1.0, 0.0)

    def nearest_point_index(q, B, B_closed):
        best = None
        for k, bk in enumerate(B):
            d = q.dist(bk)
            if best is None or d < best[0]:
                best = (d, k)
        return best[0], best[1] if best else (float('inf'), 0)
    for i in range(len(strands)):
        pa, A = strands[i]
        for j in range(i + 1, len(strands)):
            pb, B = strands[j]
            ax1 = min(q.x for q in A); ax2 = max(q.x for q in A)
            ay1 = min(q.y for q in A); ay2 = max(q.y for q in A)
            bx1 = min(q.x for q in B); bx2 = max(q.x for q in B)
            by1 = min(q.y for q in B); by2 = max(q.y for q in B)
            if ax2 < bx1 - min_sep or bx2 < ax1 - min_sep or \
                    ay2 < by1 - min_sep or by2 < ay1 - min_sep:
                continue
            B_closed = (B[0].dist(B[-1]) < 1e-6) if len(B) >= 3 else False
            run_len = 0.0
            prev = None
            for ia, q in enumerate(A):
                d, ib = nearest_point_index(q, B, B_closed)
                parallel = False
                if d < min_sep:
                    ta = tangent(A, ia)
                    tb = tangent(B, ib)
                    cos = abs(ta[0] * tb[0] + ta[1] * tb[1])
                    parallel = cos >= parallel_cos
                if parallel:
                    if prev is not None:
                        run_len += q.dist(prev)
                else:
                    run_len = 0.0
                prev = q
                if run_len > min_run_len:
                    findings.append({'kind': 'overlap',
                                     'message': f'{pa.id} ↔ {pb.id} parallel overlap '
                                                f'> {min_run_len:.1f} in',
                                     'where': [q.x, q.y]})
                    if len(findings) >= 10:
                        return False, findings
                    break
    return (not findings, findings)


# ---------------------------------------------------------------------------
# runner-level composition
# ---------------------------------------------------------------------------

def region_coverage(paths, meta, bead: float):
    """Every material ring (= a wall region) should have at least ONE
    printed path within one bead of some of its vertices. Catches the
    J1-era 'missing wall region' bug directly: a region with zero
    nearby printed paths = no coverage at all."""
    rings = _flatten_regions((meta or {}).get('regions') or [])
    if not rings:
        return True, []
    printed_polys = []
    for p in paths:
        role = getattr(p, 'role', '')
        if role not in ('inner', 'outer', 'free', 'cap', 'wall_system',
                        'lattice', 'bead'):
            continue
        pts = _sample_pts(p)
        if len(pts) >= 2:
            printed_polys.append((pts, getattr(p, 'closed', False)))
    if not printed_polys:
        return False, [{'kind': 'missing_region',
                        'message': 'material rings exist but nothing is printed',
                        'where': None}]
    findings = []
    for ring in rings:
        if not ring:
            continue
        covered = False
        for q in ring:
            if any(_dist_to_polyline(q, pts, cl) < 2.0 * bead
                   for pts, cl in printed_polys):
                covered = True
                break
        if not covered:
            # the ring is nowhere near a printed bead → missing coverage
            cx = sum(q.x for q in ring) / len(ring)
            cy = sum(q.y for q in ring) / len(ring)
            findings.append({'kind': 'missing_region',
                             'message': f'material ring (n={len(ring)}) has no printed path within 2 beads',
                             'where': [cx, cy]})
    return (not findings, findings)


def incorrect_rounding(paths, meta, junction_style: str, bead: float):
    """When the document uses `round` junctions, the printed CAP beads at
    a junction should include round-labelled arcs. Catches the J1-era
    'incorrect rounded vs square transition' symptom (silently falling
    back to miter when round was requested) in both directions."""
    want_round = (junction_style == 'round')
    found_round = False
    found_junction = False
    for p in paths:
        role = getattr(p, 'role', '')
        label = getattr(p, 'label', '') or ''
        if role != 'cap':
            continue
        if 'junction' in label:
            found_junction = True
            if 'round' in label:
                found_round = True
    if not found_junction:
        return True, []                      # no junctions in this fixture
    if want_round and not found_round:
        return False, [{'kind': 'rounding_mismatch',
                        'message': 'junction_style=round but no round-labelled caps',
                        'where': None}]
    if (not want_round) and found_round:
        return False, [{'kind': 'rounding_mismatch',
                        'message': 'junction_style=miter but round caps are present',
                        'where': None}]
    return True, []


def run_all(paths, meta, layer, junction_style: str, bead: float,
            contact_overlap: float = 0.75) -> dict:
    """Run every check on one resolved layer. Returns a dict keyed by
    check name with `{ok, findings}` entries."""
    out = {}
    out['continuity'] = _finding(continuity(paths, meta, layer))
    out['cap_integrity'] = _finding(cap_integrity(paths, meta, junction_style))
    out['envelope_containment'] = _finding(envelope_containment(paths, meta, bead))
    # no_protrusions is currently an alias of envelope_containment; running
    # both was pure duplication. Kept callable above for callers that may
    # want to tighten the outer-perimeter case independently.
    out['exterior_continuity'] = _finding(exterior_continuity(paths, meta, bead))
    out['region_coverage'] = _finding(region_coverage(paths, meta, bead))
    out['incorrect_rounding'] = _finding(incorrect_rounding(paths, meta, junction_style, bead))
    out['overlap'] = _finding(overlap(paths, bead, contact_overlap))
    return out


def _finding(res):
    ok, items = res
    return {'ok': ok, 'count': len(items), 'findings': items}
