"""
CANDIDATE B — BOUNDARY-DRIVEN FIXED-ANGLE PROPAGATION (experimental).

A "structural billiard": a path travels through the wall cavity as straight
rays. Where a ray meets a face it LANDS at the contact separation, runs
along the face for the bond length b, reads the local face tangent and
LEAVES at the fixed structural angle θ to it (towards the interior, keeping
its longitudinal sense) — not specular: the departure angle does not
depend on the arrival angle (a "fixed reflection law" / slap-type billiard,
see NOTE.md). It knows nothing of the skeleton: openings, caps, corners and
junctions are simply boundary.

Purely local and deterministic, but an INITIAL-VALUE process: every landing
depends on all earlier ones. Termination: a landing that comes within one
bead of an existing landing on the same face MERGES into it (the web joins
itself — a dead end turns the path round, a ring closes with whatever
mismatch it has accumulated); otherwise a bounce limit.

Seeds (deterministic): every dead end's cap; one seed per region if it has
none; then one seed in the middle of every wall run the web has not
reached (the skeleton is used ONLY for seeding and coverage).
"""
from __future__ import annotations

import math

from common import Corridor, Vec2, BEAD, CONTACT, fillet, unit, cross, dot, rot
import truss


DEFAULTS = dict(theta=45.0, bond=BEAD, r_turn=0.5 * BEAD, d_skin=40.0)


def _hit(F, p, d, Lmax):
    """First boundary crossing of the ray p + t d, 1e-6 < t ≤ Lmax."""
    q = p + d * Lmax
    c = F.cell
    cands = set()
    # cells along the ray (sampled at half-cell steps, ±1 cell)
    n = max(1, int(Lmax / (0.5 * c)))
    for k in range(n + 1):
        x = p.x + (q.x - p.x) * k / n
        y = p.y + (q.y - p.y) * k / n
        gx, gy = math.floor(x / c), math.floor(y / c)
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                cands.update(F.grid.get((gx + ox, gy + oy), ()))
    best = None
    for i in cands:
        _, _, a, b, _ = F.segs[i]
        e = b - a
        den = cross(d, e)
        if abs(den) < 1e-12:
            continue
        w = a - p
        t = cross(w, e) / den
        u = cross(w, d) / den
        if t > 1e-6 and t <= Lmax and -1e-9 <= u <= 1 + 1e-9:
            if best is None or t < best[0]:
                best = (t, i, unit(e))
    return best


def _inward(F, q, tan, probe=0.4):
    n = Vec2(-tan.y, tan.x)
    return n if F.inside(q + n * probe) else Vec2(tan.y, -tan.x)


def plan(rings, theta=None, bond=None, r_turn=None, d_skin=None, contact=CONTACT, max_bounces=400,
         corridor=None):
    p = dict(DEFAULTS)
    for k, v in dict(theta=theta, bond=bond, r_turn=r_turn, d_skin=d_skin).items():
        if v is not None:
            p[k] = v
    C = corridor or Corridor(rings, contact)
    F = C.faces
    th = math.radians(p['theta'])
    Lmax = 6.0 * C.thick / max(math.sin(th), 0.1) + 4 * p['bond'] + 20
    landings = []                  # (point, inward normal, path index)
    paths, events = [], {'merge': 0, 'limit': 0, 'escape': 0, 'same_skin': 0, 'stuck': 0}

    def near_landing(q, nrm, own, own_recent):
        for k, (pt, nn, pi) in enumerate(landings):
            if pi == own and k in own_recent:
                continue
            if pt.dist(q) < BEAD and dot(nn, nrm) > 0.5:
                return pt
        return None

    def run_path(start, d, heading):
        pi = len(paths)
        path = [start]
        own = []
        prev_n = None
        pos = start
        for _ in range(max_bounces):
            h = _hit(F, pos, d, Lmax)
            if h is None:
                events['escape'] += 1
                break
            t, si, tan = h
            hp = pos + d * t
            sin_a = abs(cross(d, tan))
            back = contact / max(sin_a, 0.25)
            if back >= t - 0.25:
                events['stuck'] += 1
                break
            Lp = hp - d * back
            nrm = _inward(F, hp, tan)
            if prev_n is not None and dot(nrm, prev_n) > 0.5:
                events['same_skin'] += 1
            m = near_landing(Lp, nrm, pi, set(own[-3:]))
            if m is not None:
                path.append(m)
                events['merge'] += 1
                break
            path.append(Lp)
            own.append(len(landings))
            landings.append((Lp, nrm, pi))
            # longitudinal sense: keep the travel direction along the face
            along = dot(d, tan)
            sg = 1.0 if along > 0.05 else -1.0 if along < -0.05 else (1.0 if dot(heading, tan) >= 0 else -1.0)
            tau = tan * sg
            # bond: run along the face (re-projected onto the contact rail)
            L2 = Lp
            if p['bond'] > 0:
                q = Lp + tau * p['bond']
                pr = F.project(q)
                if pr is not None:
                    _, _, _, fq, ftan = pr
                    fn = _inward(F, fq, ftan)
                    cand = fq + fn * contact
                    if F.inside(cand) and not F.region.crosses(Lp, cand):
                        L2 = cand
                        tan2 = ftan if dot(ftan, tau) >= 0 else ftan * -1.0
                        tau, nrm = tan2, fn
            if L2.dist(Lp) > 1e-6:
                path.append(L2)
                own.append(len(landings))
                landings.append((L2, nrm, pi))
            heading = tau
            d = unit(tau * math.cos(th) + nrm * math.sin(th))
            pos = L2
            prev_n = nrm
        else:
            events['limit'] += 1
        paths.append(path)

    seeds = 0
    for run in C.runs:
        for at_start, node in ((True, run.a), (False, run.b)):
            if run.cycle or node[0] != 'E':
                continue
            lo, hi = C.span(run)
            x = lo if at_start else hi
            cpt = truss._cap(C, run, x, toward_end=not at_start)
            if cpt is None:
                continue
            inward = unit(C.geo.centre(run, min(run.length, x + 1.0) if at_start else max(0.0, x - 1.0)) - C.geo.centre(run, x))
            if near_landing(cpt, inward, -1, set()):
                continue
            landings.append((cpt, inward, len(paths)))
            run_path(cpt, unit(rot(inward, th)), inward)
            seeds += 1
    if not paths and C.runs:
        run = max(C.runs, key=lambda r: r.length)
        s0 = 0.0 if run.cycle else 0.5 * run.length
        start = C.rail(run, 0, s0)
        tng = unit(C.geo.centre(run, s0 + 1.0) - C.geo.centre(run, s0))
        nrm = unit(C.rail(run, 1, s0) - start)
        run_path(start, unit(tng * math.cos(th) + nrm * math.sin(th)), tng)
        seeds += 1
    # coverage seeds: wall runs the web never reached
    for run in C.runs:
        lo, hi = (0.0, run.length) if run.cycle else C.span(run)
        xs = [lo + (hi - lo) * (k + 0.5) / 6 for k in range(6)]
        reached = sum(1 for x in xs if any(pt.dist(C.geo.centre(run, x)) < 0.5 * p['d_skin'] for pt, _, _ in landings))
        if reached >= 3:
            continue
        s0 = 0.5 * (lo + hi)
        start = C.rail(run, 0, s0)
        tng = unit(C.geo.centre(run, s0 + 1.0) - C.geo.centre(run, s0 - 1.0))
        nrm = unit(C.rail(run, 1, s0) - start)
        if nrm.length() < 0.5:
            continue
        landings.append((start, unit(start - C.face(run, 0, s0)), len(paths)))
        run_path(start, unit(tng * math.cos(th) + nrm * math.sin(th)), tng)
        seeds += 1
    polys = [fillet(pth, p['r_turn']) for pth in paths if len(pth) >= 2]
    return polys, {'params': p, 'seeds': seeds, 'events': events,
                   'landings': len(landings)}
