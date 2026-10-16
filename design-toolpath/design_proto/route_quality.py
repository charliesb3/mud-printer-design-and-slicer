"""
Physical route-quality diagnostics — bead deposition, not just graph
correctness.

A route can be graph-perfect (one run, no travel, no exact retrace) and
still be a bad wall: long arbitrary beads through the wall, or many
generated beads converging in one small area (a mud "knot"). These
metrics make that visible and testable.

Generated geometry = infill / lattice / continuity corrections (strand
kind 'field'). User-authored geometry (sources, offsets, caps, joins) may
legitimately meet at high degree (e.g. six walls drawn into one point);
the congestion rules apply to what the software GENERATES.

CLEARANCE_RADIUS is provisional (the bead width is not modelled yet).
"""
from __future__ import annotations
import math

CLEARANCE_RADIUS = 2.0      # inches: neighbourhood for congestion
MAX_GENERATED_BEADS = 2.0   # generated beads through any point (≈ 2 paths)
HOTSPOT_TOL = 0.02          # measurement tolerance: two CURVED beads crossing at
                            # a shallow angle measure up to ~2.007 (each arc is a
                            # little longer than the disc's diameter); a real
                            # third bead adds ≈ 0.5


def _seg_disk_len(a, b, c, r):
    """Length of segment a-b inside the disk (c, r)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    fx, fy = a[0] - c[0], a[1] - c[1]
    A = dx * dx + dy * dy
    if A < 1e-18:
        return 0.0
    B = 2 * (fx * dx + fy * dy)
    C = fx * fx + fy * fy - r * r
    disc = B * B - 4 * A * C
    if disc <= 0:
        return 0.0
    s = math.sqrt(disc)
    t0, t1 = (-B - s) / (2 * A), (-B + s) / (2 * A)
    t0, t1 = max(0.0, t0), min(1.0, t1)
    return max(0.0, t1 - t0) * math.sqrt(A)


class _SegGrid:
    def __init__(self, segs, cell):
        self.segs, self.cell, self.g = segs, cell, {}
        for k, (a, b) in enumerate(segs):
            for gx in range(math.floor(min(a[0], b[0]) / cell), math.floor(max(a[0], b[0]) / cell) + 1):
                for gy in range(math.floor(min(a[1], b[1]) / cell), math.floor(max(a[1], b[1]) / cell) + 1):
                    self.g.setdefault((gx, gy), []).append(k)

    def near(self, p, r):
        out = set()
        c = self.cell
        for gx in range(math.floor((p[0] - r) / c), math.floor((p[0] + r) / c) + 1):
            for gy in range(math.floor((p[1] - r) / c), math.floor((p[1] + r) / c) + 1):
                out.update(self.g.get((gx, gy), ()))
        return out


def congestion(generated, user, rho=CLEARANCE_RADIUS):
    """
    Local bead multiplicity: at probe points (every generated vertex and
    every point where generated segments meet or cross), the bead length
    inside a disk of radius rho divided by its diameter ≈ number of beads
    passing through. Returns {'max_generated', 'max_total', 'hotspots'}:
    the worst generated multiplicity, the worst total multiplicity at those
    probes, and how many probes exceed MAX_GENERATED_BEADS.
    """
    if not generated:
        return {'max_generated': 0.0, 'max_total': 0.0, 'hotspots': 0}
    gg = _SegGrid(generated, max(rho * 2, 4.0))
    ug = _SegGrid(user, max(rho * 2, 4.0)) if user else None
    probes = {p for s in generated for p in s}
    # crossings between generated segments
    for k, (a, b) in enumerate(generated):
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        half = math.hypot(b[0] - a[0], b[1] - a[1]) / 2
        for j in gg.near(mid, half):
            if j <= k:
                continue
            x = _cross(a, b, *generated[j])
            if x is not None:
                probes.add(x)
    worst_g = worst_t = 0.0
    hot = 0
    for p in probes:
        g = sum(_seg_disk_len(*generated[k], p, rho) for k in gg.near(p, rho)) / (2 * rho)
        u = (sum(_seg_disk_len(*user[k], p, rho) for k in ug.near(p, rho)) / (2 * rho)
             if ug else 0.0)
        worst_g = max(worst_g, g)
        worst_t = max(worst_t, g + u)
        if g > MAX_GENERATED_BEADS + HOTSPOT_TOL:
            hot += 1
    return {'max_generated': round(worst_g, 3), 'max_total': round(worst_t, 3),
            'hotspots': hot}


def _cross(a, b, c, d):
    rx, ry = b[0] - a[0], b[1] - a[1]
    qx, qy = d[0] - c[0], d[1] - c[1]
    cr = rx * qy - ry * qx
    if abs(cr) < 1e-12:
        return None
    wx, wy = c[0] - a[0], c[1] - a[1]
    t = (wx * qy - wy * qx) / cr
    u = (wx * ry - wy * rx) / cr
    if 1e-9 < t < 1 - 1e-9 and 1e-9 < u < 1 - 1e-9:
        return (a[0] + t * rx, a[1] + t * ry)
    return None


def geometric_retrace(moves):
    """Length printed over an already-printed segment (either direction)."""
    segs = [((m.start.x, m.start.y), (m.end.x, m.end.y))
            for m in moves if m.kind != 'travel' and m.length > 1e-9]
    grid = _SegGrid(segs, 8.0)
    tot = 0.0
    for i, (a, b) in enumerate(segs):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for j in grid.near(mid, L / 2 + 1e-6):
            if j <= i:
                continue
            c, d = segs[j]
            if abs((c[0] - a[0]) * uy - (c[1] - a[1]) * ux) > 1e-6 or \
                    abs((d[0] - a[0]) * uy - (d[1] - a[1]) * ux) > 1e-6:
                continue
            t0 = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
            t1 = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
            tot += max(0.0, min(L, max(t0, t1)) - max(0.0, min(t0, t1)))
    return tot


def quality(layer, rho=CLEARANCE_RADIUS):
    """Route + physical-quality diagnostics for a PrintLayer."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto'))
    from graph import route_layer, compute_metrics, route_ends, build_graph
    paths, meta = layer._build_effective()
    rl = layer.to_routing_layer(paths, meta)
    moves = route_layer(rl)
    m = compute_metrics(moves)
    kind = {s.id: s.kind for s in rl.strands}
    gen, user = [], []
    for s in rl.strands:
        segs = [((a.x, a.y), (b.x, b.y)) for a, b in s.segments()]
        (gen if s.kind == 'field' else user).extend(segs)
    G = build_graph(rl)
    gdeg = 0
    for n in G.nodes:
        d = sum(1 for _, _, k, a in G.edges(n, keys=True, data=True)
                if kind.get(a['strand_id']) == 'field')
        gdeg = max(gdeg, d)
    plan = (meta['network'].get('route_plan') or {})
    corr = plan.get('corrections', [])
    ends = route_ends(moves)
    out = {
        'runs': m['print_runs'], 'travel_moves': m['travel_moves'],
        'travel_length': m['travel_distance'],
        'print_length': m['print_distance'],
        'exact_retrace': round(geometric_retrace(moves), 3),
        'router_retrace': m['retrace_distance'],
        'generated_length': round(sum(math.dist(a, b) for a, b in gen), 2),
        'correction_added': round(sum(c['added'] for c in corr), 2),
        'correction_removed': round(sum(c['removed'] for c in corr), 2),
        'max_correction_segment': round(max((c['max_segment'] for c in corr), default=0.0), 2),
        'max_correction_distance': round(max((c['max_distance'] for c in corr), default=0.0), 2),
        'max_generated_degree': gdeg,
        'closed': bool(ends and ends.closed),
    }
    out.update({f'congestion_{k}': v for k, v in congestion(gen, user, rho).items()})
    return out


# ---------------------------------------------------------------------------
# Wall-lattice geometric quality (wall infill: stitching between faces)
# ---------------------------------------------------------------------------

TINY_CELL = 0.1      # × thickness × target spacing: a lattice cell smaller
                     # than this is a corrective loop (bow tie, diamond, box)
TINY_LOOP_GEN = 0.6  # share of a tiny cell's perimeter that is generated


def _ring_pos(rings, p, tol=1e-5):
    """(ring index, arc position) of a point lying on a ring, else None."""
    best = None
    for ri, r in enumerate(rings):
        acc = 0.0
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            L = math.dist(a, b)
            if L > 1e-12:
                t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) +
                                       (p[1] - a[1]) * (b[1] - a[1])) / (L * L)))
                d = math.dist(p, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
                if d < tol and (best is None or d < best[0]):
                    best = (d, ri, acc + t * L)
            acc += L
    return None if best is None else (best[1], best[2])


# ---------------------------------------------------------------------------
# Solid-infill geometric quality (area fill attached to its boundaries)
# ---------------------------------------------------------------------------

def solid_metrics(layer, rho=CLEARANCE_RADIUS):
    """
    Geometric quality of SOLID infill, per boundary ring of each solid
    region (outer boundary and voids), measured on the produced geometry:
      contacts[i]          infill points lying ON ring i
      max_unsupported[i]   longest stretch of ring i between contacts
      void_violations      infill length inside a void (should be 0)
      longest_connector    longest infill segment that is not a field
                           line (turn legs / links: segments not parallel
                           to the field direction)
      exact_retrace, runs, travel, congestion.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto'))
    from graph import route_layer, compute_metrics
    from infill import _Region
    paths, meta = layer._build_effective()
    rl = layer.to_routing_layer(paths, meta)
    moves = route_layer(rl)
    m = compute_metrics(moves)
    regions = [r for r in meta['network'].get('solid_regions', [])]
    solid = [p for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill']
    polys = [[(q.x, q.y) for q in (meta['pts'].get(id(p)) or p.sample_points())] for p in solid]
    segs = [(a, b) for poly in polys for a, b in zip(poly, poly[1:])]
    out_rings = []
    void_len = 0.0
    from model import Vec2
    Rs = [_Region([[Vec2(*q) for q in ring] for ring in reg['rings']], 4.0) for reg in regions]
    # a point of solid infill must lie in SOME solid region (or on its
    # boundary): anything else is infill inside a void / outside the part
    for a_, b_ in segs:
        for f in (0.25, 0.5, 0.75):
            q = Vec2(a_[0] + f * (b_[0] - a_[0]), a_[1] + f * (b_[1] - a_[1]))
            if not any(R.inside(q) or R.dist(q, 1e-3) < 1e-3 for R in Rs):
                void_len += math.dist(a_, b_) / 3
    for reg in regions:
        rings = [[tuple(q) for q in ring] for ring in reg['rings']]
        for ring in rings:
            n = len(ring)
            cum = [0.0]
            for i in range(n):
                cum.append(cum[-1] + math.dist(ring[i], ring[(i + 1) % n]))
            L = cum[-1]
            pos = []
            for poly in polys:
                for q in poly:
                    rp = _ring_pos([ring], q)
                    if rp is not None:
                        pos.append(rp[1])
            pos = sorted(set(round(x, 6) for x in pos))
            gaps = [b - a for a, b in zip(pos, pos[1:])] + ([pos[0] + L - pos[-1]] if pos else [L])
            out_rings.append({'length': round(L, 2), 'contacts': len(pos),
                              'max_unsupported': round(max(gaps), 2), 'void': ring is not rings[0]})
    # connectors: segments of infill polylines that are not field lines
    ang = [r.get('angle', 45.0) for r in regions] or [45.0]
    th = math.radians(ang[0])
    ux, uy = math.cos(th), math.sin(th)
    longest = 0.0
    for a, b in segs:
        L = math.dist(a, b)
        if L < 1e-9:
            continue
        par = abs((b[0] - a[0]) * ux + (b[1] - a[1]) * uy) / L
        if par < 0.98:
            longest = max(longest, L)
    gen = [((a.x, a.y), (b.x, b.y)) for s in rl.strands if s.kind == 'field' for a, b in s.segments()]
    user = [((a.x, a.y), (b.x, b.y)) for s in rl.strands if s.kind != 'field' for a, b in s.segments()]
    cg = congestion(gen, user, rho)
    return {
        'runs': m['print_runs'], 'travel_moves': m['travel_moves'],
        'travel_length': round(m['travel_distance'], 2),
        'exact_retrace': round(geometric_retrace(moves), 3),
        'rings': out_rings,
        'void_violations': round(void_len, 3),
        'longest_connector': round(longest, 2),
        'congestion_max_generated': cg['max_generated'],
        'congestion_hotspots': cg['hotspots'],
    }


def face_support(layer):
    """Support of each WALL boundary ring by the lattice: arc positions
    of the lattice landings ON the ring and the gaps between consecutive
    landings. Returns [{'length', 'landings', 'gaps'}] per ring. (Junction
    hand-offs sit just inside the wall, so a gap may span a junction
    corner.)"""
    paths, meta = layer._build_effective()
    lat = meta['network'].get('lattice') or {}
    rings = [[tuple(q) for q in ring] for v in lat.values() for reg in v['regions']
             for ring in reg.get('rings', [])]
    gen = [[(q.x, q.y) for q in (meta['pts'].get(id(p)) or p.sample_points())]
           for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    out = []
    for ri, ring in enumerate(rings):
        n = len(ring)
        L = sum(math.dist(ring[i], ring[(i + 1) % n]) for i in range(n))
        pos = sorted({round(rp[1], 4) for poly in gen for q in poly
                      for rp in [_ring_pos([ring], q)] if rp is not None})
        gaps = [b - a for a, b in zip(pos, pos[1:])] + ([pos[0] + L - pos[-1]] if pos else [L])
        out.append({'length': round(L, 2), 'landings': len(pos),
                    'gaps': [round(g, 3) for g in gaps]})
    return out


def corner_support(layer, turn_deg=35.0):
    """For every sharp vertex of the wall boundary (rings of the lattice
    regions; turning ≥ turn_deg within a short window), the distance to the
    nearest lattice landing on the boundary. Returns [(x, y, turn, dist)]."""
    paths, meta = layer._build_effective()
    lat = meta['network'].get('lattice') or {}
    rings = [[tuple(q) for q in ring] for v in lat.values() for reg in v['regions']
             for ring in reg.get('rings', [])]
    gen = [[(q.x, q.y) for q in (meta['pts'].get(id(p)) or p.sample_points())]
           for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    lands = [q for poly in gen for q in poly if _ring_pos(rings, q) is not None]
    out = []
    for ring in rings:
        n = len(ring)
        for i in range(n):
            a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
            # turning over a short window (fillet arcs are many small turns)
            tot = 0.0
            for j in range(-3, 4):
                p0, p1, p2 = ring[(i + j - 1) % n], ring[(i + j) % n], ring[(i + j + 1) % n]
                if math.dist(p1, b) > 3.0:
                    continue
                v1 = (p1[0] - p0[0], p1[1] - p0[1]); v2 = (p2[0] - p1[0], p2[1] - p1[1])
                l1, l2 = math.hypot(*v1), math.hypot(*v2)
                if l1 > 1e-9 and l2 > 1e-9:
                    tot += math.degrees(math.acos(max(-1, min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))))
            if tot >= turn_deg:
                d = min((math.dist(b, q) for q in lands), default=math.inf)
                out.append((round(b[0], 2), round(b[1], 2), round(tot, 1), round(d, 2)))
    return out
