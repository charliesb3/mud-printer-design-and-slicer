"""
Method-agnostic diagnostics for a wall web (production or candidate),
measured on the produced geometry only — never on generator labels.

CONTACT: a maximal stretch of web within (contact separation + 0.6 in) of a
face centreline; its middle is the contact point, its length the bond.
BRACE: the web between two consecutive contacts of one polyline that
reaches into the middle of the cavity (≥ 0.3 × wall thickness off the faces).
SKIN SPAN: the gap ALONG ONE FACE between consecutive contacts (per ring,
by arc length) — the unsupported length of that skin bead.
"""
from __future__ import annotations

import math

import networkx as nx

from common import Faces, Vec2, BEAD, CONTACT, resample, length, unit, cross, dot, WL

CONTACT_TOL = 0.6


def _turns(poly):
    """[(vertex, turn rad, kind, radius)]: a KINK is a real corner (turn ≥
    20° between segments ≥ 0.75 in); other vertices discretise a curve,
    radius = mean adjacent segment / turn."""
    out = []
    for i in range(1, len(poly) - 1):
        a, v, b = poly[i - 1], poly[i], poly[i + 1]
        u1, u2 = v - a, b - v
        l1, l2 = u1.length(), u2.length()
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        c = max(-1.0, min(1.0, dot(u1, u2) / (l1 * l2)))
        t = math.acos(c)
        if t < math.radians(1):
            continue
        if t >= math.radians(20) and min(l1, l2) >= 0.75:
            out.append((v, t, 'kink', 0.0))
        else:
            out.append((v, t, 'curve', 0.5 * (l1 + l2) / t))
    return out


class _Grid:
    def __init__(self, pts, cell):
        self.cell = cell
        self.g = {}
        for i, p in enumerate(pts):
            self.g.setdefault((math.floor(p.x / cell), math.floor(p.y / cell)), []).append(i)

    def near(self, p):
        gx, gy = math.floor(p.x / self.cell), math.floor(p.y / self.cell)
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                yield from self.g.get((gx + ox, gy + oy), ())


def contacts_of(F, polys, contact=CONTACT):
    """[(point, ring, arc, bond length, poly index, sample index)]"""
    out = []
    samples = []
    for pi, poly in enumerate(polys):
        S = resample(poly, 0.5)
        proj = [F.project(q) for q in S]
        samples.append((S, proj))
        run = []
        for k, pr in enumerate(proj + [None]):
            on = pr is not None and pr[0] <= contact + CONTACT_TOL
            if on:
                run.append(k)
            elif run:
                mid = run[len(run) // 2]
                d, ri, arc, q, tan = proj[mid]
                out.append((S[mid], ri, arc, 0.5 * (len(run) - 1), pi, mid))
                run = []
    # merge contacts closer than 1 in (two polylines meeting at one point)
    merged = []
    for c in out:
        if any(m[0].dist(c[0]) < 1.0 and m[1] == c[1] for m in merged):
            continue
        merged.append(c)
    return merged, out, samples


def measure(regions, polys, contact=CONTACT, d_skin=40.0, bead=BEAD):
    rings = [r for reg in regions for r in reg]
    F = Faces(rings)
    thick = sum(WL._thickness(reg) for reg in regions) / max(1, len(regions))
    m = {}
    m['web_length'] = length_total = sum(length(p) for p in polys)
    contacts, raw, samples = contacts_of(F, polys, contact)
    m['contacts'] = len(contacts)

    # --- out of the wall / skin intrusion -------------------------------
    out_pts, intr_pts = [], []
    for (S, proj) in samples:
        n = len(S)
        for k, (q, pr) in enumerate(zip(S, proj)):
            if not F.inside(q) and (pr is None or pr[0] > 0.05):
                out_pts.append(q)
            elif pr is not None and pr[0] < contact - 0.5 and 4 < k < n - 5:
                intr_pts.append(q)
    m['out_of_wall_len'] = round(0.5 * len(out_pts), 1)
    m['skin_intrusion_len'] = round(0.5 * len(intr_pts), 1)

    # --- skin spans (per ring, along the face) ---------------------------
    by_ring = {}
    for c in contacts:
        by_ring.setdefault(c[1], []).append(c[2])
    gaps = []
    beyond = 0.0
    for ri, L in enumerate(F.length):
        arcs = sorted(by_ring.get(ri, []))
        if not arcs:
            g = [L]
        else:
            g = [(arcs[(i + 1) % len(arcs)] - arcs[i]) % L or (L if len(arcs) == 1 else 0.0)
                 for i in range(len(arcs))]
        gaps += g
        beyond += sum(max(0.0, x - d_skin) for x in g)
    gaps.sort()
    m['skin_span_max'] = round(gaps[-1], 1) if gaps else None
    m['skin_span_median'] = round(gaps[len(gaps) // 2], 1) if gaps else None
    m['skin_beyond_D_pct'] = round(100.0 * beyond / max(1e-9, sum(F.length)), 1)

    # --- braces and their angles ------------------------------------------
    angles, braces = [], 0
    by_poly = {}
    for c in raw:
        by_poly.setdefault(c[4], []).append(c)
    for pi, cs in by_poly.items():
        S, proj = samples[pi]
        cs.sort(key=lambda c: c[5])
        for c1, c2 in zip(cs, cs[1:]):
            mid = [proj[k][0] for k in range(c1[5], c2[5] + 1) if proj[k] is not None]
            if not mid or max(mid) < 0.3 * thick:
                continue
            braces += 1
            # the brace's own direction where it crosses the cavity (max
            # distance from the faces), against the local wall direction
            ks = [k for k in range(c1[5], c2[5] + 1) if proj[k] is not None]
            km = max(ks, key=lambda k: proj[k][0])
            d = unit(S[min(len(S) - 1, km + 3)] - S[max(0, km - 3)])
            angles.append(math.degrees(math.asin(min(1.0, abs(cross(d, proj[km][4]))))))
    angles.sort()
    m['braces'] = braces
    m['angle_min'] = round(angles[0], 1) if angles else None
    m['angle_median'] = round(angles[len(angles) // 2], 1) if angles else None
    m['angle_max'] = round(angles[-1], 1) if angles else None
    bonds = sorted(c[3] for c in contacts)
    m['bond_median'] = round(bonds[len(bonds) // 2], 1) if bonds else None

    # --- turns ---------------------------------------------------------------
    sharp_pts, radii, maxkink = [], [], 0.0
    for poly in polys:
        for v, t, kind, r in _turns(poly):
            if kind == 'kink':
                sharp_pts.append(v)
                maxkink = max(maxkink, t)
            else:
                radii.append(r)
    m['min_curve_radius'] = round(min(radii), 2) if radii else None
    m['kinks'] = len(sharp_pts)
    m['max_kink_deg'] = round(math.degrees(maxkink), 1)

    # --- congestion ------------------------------------------------------------
    pts, own = [], []
    for pi, poly in enumerate(polys):
        S = resample(poly, 0.75)
        acc = 0.0
        for k, q in enumerate(S):
            if k:
                acc += S[k - 1].dist(q)
            d = unit(S[min(k + 1, len(S) - 1)] - S[max(k - 1, 0)])
            pts.append(q)
            own.append((pi, acc, d))
    G = _Grid(pts, bead)
    cong_pts, dup = [], 0
    for i, q in enumerate(pts):
        pi, ai, di = own[i]
        hit = par = False
        for j in G.near(q):
            if j == i:
                continue
            pj, aj, dj = own[j]
            if pts[j].dist(q) >= 0.9 * bead:
                continue
            if pj == pi and abs(ai - aj) < 3 * bead:
                continue
            hit = True
            if abs(dot(di, dj)) > 0.95:
                par = True
        if hit:
            cong_pts.append(q)
        if par:
            dup += 1
    m['congested_len'] = round(0.75 * len(cong_pts), 1)
    m['near_duplicate_len'] = round(0.75 * dup, 1)

    # --- routing graph (web + skins) ------------------------------------------
    m.update(graph_metrics(rings, polys))
    vis = {'contacts': [c[0] for c in contacts], 'out': out_pts, 'intrusion': intr_pts,
           'sharp': sharp_pts, 'congested': cong_pts}
    return m, vis


# ---------------------------------------------------------------------------
# Graph: web strands + skin rings, split at every intersection / touch
# ---------------------------------------------------------------------------

def _key(p):
    return (round(p.x, 2), round(p.y, 2))


def graph_metrics(rings, polys):
    strands = [list(p) for p in polys if len(p) >= 2] + [list(r) + [r[0]] for r in rings]
    segs = []
    for si, st in enumerate(strands):
        for k in range(len(st) - 1):
            if st[k].dist(st[k + 1]) > 1e-9:
                segs.append((si, k, st[k], st[k + 1]))
    cell = 4.0
    grid = {}
    for i, (_, _, a, b) in enumerate(segs):
        for gx in range(math.floor(min(a.x, b.x) / cell), math.floor(max(a.x, b.x) / cell) + 1):
            for gy in range(math.floor(min(a.y, b.y) / cell), math.floor(max(a.y, b.y) / cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    cuts = {i: {0.0, 1.0} for i in range(len(segs))}
    seen = set()
    TOUCH = 0.05
    for cellsegs in grid.values():
        for x in range(len(cellsegs)):
            for y in range(x + 1, len(cellsegs)):
                i, j = cellsegs[x], cellsegs[y]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                si, ki, a, b = segs[i]
                sj, kj, c, d = segs[j]
                if si == sj and abs(ki - kj) <= 1:
                    continue
                e, f = b - a, d - c
                den = cross(e, f)
                if abs(den) > 1e-12:
                    w = c - a
                    t = cross(w, f) / den
                    u = cross(w, e) / den
                    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                        cuts[i].add(min(1.0, max(0.0, t)))
                        cuts[j].add(min(1.0, max(0.0, u)))
                        continue
                # endpoint touching the other segment (T contact)
                for (p, s_own, t_own), (q0, q1, s_oth) in (((a, i, 0.0), (c, d, j)), ((b, i, 1.0), (c, d, j)),
                                                          ((c, j, 0.0), (a, b, i)), ((d, j, 1.0), (a, b, i))):
                    g = q1 - q0
                    L2 = dot(g, g)
                    if L2 < 1e-18:
                        continue
                    tt = max(0.0, min(1.0, dot(p - q0, g) / L2))
                    if (q0 + g * tt).dist(p) < TOUCH:
                        cuts[s_oth].add(tt)
                        cuts[s_own].add(t_own)
    Gm = nx.MultiGraph()
    nodes = {}
    for i, (_, _, a, b) in enumerate(segs):
        ts = sorted(cuts[i])
        prev = None
        for t in ts:
            q = a.lerp(b, t)
            k = _key(q)
            nodes.setdefault(k, q)
            if prev is not None and prev != k:
                Gm.add_edge(prev, k, w=nodes[prev].dist(q))
            prev = k
    # merge nodes closer than TOUCH (numerical splits)
    keys = list(Gm.nodes)
    grid2 = {}
    for k in keys:
        grid2.setdefault((math.floor(k[0] / 0.5), math.floor(k[1] / 0.5)), []).append(k)
    rep = {}
    for k in keys:
        if k in rep:
            continue
        gx, gy = math.floor(k[0] / 0.5), math.floor(k[1] / 0.5)
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                for k2 in grid2.get((gx + ox, gy + oy), ()):
                    if k2 not in rep and math.hypot(k2[0] - k[0], k2[1] - k[1]) < TOUCH:
                        rep[k2] = k
    G = nx.MultiGraph()
    for u, v, dd in Gm.edges(data=True):
        a, b = rep.get(u, u), rep.get(v, v)
        if a != b:
            G.add_edge(a, b, w=dd['w'])
    comps = list(nx.connected_components(G))
    odd = [n for n in G.nodes if G.degree(n) % 2]
    res = {'components': len(comps), 'odd_nodes': len(odd),
           'eulerian_circuit': len(comps) == 1 and not odd,
           'eulerian_path': len(comps) == 1 and len(odd) in (0, 2)}
    # closure estimate: join components (MST of nearest distances), then pair odd nodes
    extra = 0.0
    if len(comps) > 1:
        reps = [list(c)[:: max(1, len(c) // 150)] for c in comps]
        H = nx.Graph()
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                best = min(((math.hypot(a[0] - b[0], a[1] - b[1]), a, b) for a in reps[i] for b in reps[j]))
                H.add_edge(i, j, w=best[0], ab=(best[1], best[2]))
        for i, j, dd in nx.minimum_spanning_tree(H, weight='w').edges(data=True):
            a, b = dd['ab']
            G.add_edge(a, b, w=dd['w'])
            extra += dd['w']
        odd = [n for n in G.nodes if G.degree(n) % 2]
    if odd:
        if len(odd) <= 160:
            K = nx.Graph()
            for i in range(len(odd)):
                for j in range(i + 1, len(odd)):
                    a, b = odd[i], odd[j]
                    K.add_edge(i, j, weight=math.hypot(a[0] - b[0], a[1] - b[1]))
            M = nx.min_weight_matching(K)
            extra += sum(K[i][j]['weight'] for i, j in M)
        else:
            left = list(odd)
            while left:
                a = left.pop()
                j = min(range(len(left)), key=lambda k: math.hypot(left[k][0] - a[0], left[k][1] - a[1]))
                b = left.pop(j)
                extra += math.hypot(a[0] - b[0], a[1] - b[1])
    res['closure_extra_len'] = round(extra, 1)
    return res


# ---------------------------------------------------------------------------
# Stability / cross-Z
# ---------------------------------------------------------------------------

def _nearest_d(G, pts, q, r=6.0):
    best = r
    for j in G.near(q):
        best = min(best, pts[j].dist(q))
    return best


def compare(polys_a, cont_a, polys_b, cont_b, site=None, far=40.0, tol=1.0):
    """How much of web A moved in web B (B = A after a small edit).
    Far field = farther than `far` from the edit site (all if None)."""
    Sa = [q for p in polys_a for q in resample(p, 1.0)]
    Sb = [q for p in polys_b for q in resample(p, 1.0)]
    Gb = _Grid(Sb, 3.0)
    far_pts = [q for q in Sa if site is None or q.dist(site) > far]
    moved = sum(1 for q in far_pts if _nearest_d(Gb, Sb, q) > tol)
    cb = [c for c in cont_b]
    Gc = _Grid(cb, 3.0)
    disp = sorted(_nearest_d(Gc, cb, c, 30.0) for c in cont_a if site is None or c.dist(site) > far)
    return {'d_contacts': len(cont_b) - len(cont_a),
            'far_web_moved_pct': round(100.0 * moved / max(1, len(far_pts)), 1),
            'far_contact_moved_pct': round(100.0 * sum(1 for d in disp if d > tol) / max(1, len(disp)), 1),
            'contact_disp_median': round(disp[len(disp) // 2], 2) if disp else None,
            'contact_disp_max': round(disp[-1], 2) if disp else None}
