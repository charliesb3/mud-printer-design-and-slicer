"""
DIMENSION FEASIBILITY of SCALE CONSTRUCTION (Assembly.dimension_behavior
'scale', 2026-10-18).

Scale construction places each layer's resolved construction by its
similarity, so every centreline distance scales by the layer's scale k —
but the nozzle does not: Bead Width stays physical. A strong taper can
therefore ask the real bead for construction it cannot make. Two existing
material rules are checked (never "fixed" — Bead Width is never scaled):

  * PARALLEL WALL OVERLAP (material `return_overlap`, R): neighbouring
    beads running alongside each other (two skins, two lanes, a ring's two
    sides) may overlap by at most R, i.e. their centrelines stay ≥ W − R
    apart. The canonical layer's closest parallel spacing d (measured on
    its printed centrelines; the Assembly's editing view: its envelope rings
    less one bead) becomes k · d.
  * WEB PITCH FLOOR (Skin + Web, Target Web Angle: max(1, W / 2)): the
    web's station pitch S becomes k · S.

Pure geometry over the canonical layer geometry + each layer's placement
scale; the Assembly never learns what a wall or a lattice is (it reads the
strands and the Designer's lattice report)."""
from __future__ import annotations

import math

from .support import _seg_dist

PARALLEL_DEG = 20.0          # two strands are "alongside each other" within this angle
SAMPLE = 1.0                 # in: along-strand sampling of the spacing measurement


def _closest_parallel(polylines, skip_lattice=True) -> float | None:
    """The closest spacing of two printed beads running ALONGSIDE each other
    (distinct strands, or far-apart parts of one strand — a ring's two sides
    across a wall), ignoring the web (it meets the skins by design) and
    strands that touch (junctions)."""
    strands = []
    for pl in polylines:
        if skip_lattice and (pl.get('role') == 'lattice' or '~' in (pl.get('id') or '')):
            continue
        pts = [tuple(p) for p in pl.get('pts') or ()]
        if pl.get('closed') and len(pts) > 2 and pts[0] != pts[-1]:
            pts.append(pts[0])
        if len(pts) >= 2:
            strands.append(pts)
    if not strands:
        return None
    segs = []                                   # (a, b, strand, arclength at a, unit dir)
    total, ring = [], []
    for j, pts in enumerate(strands):
        acc = 0.0
        for a, b in zip(pts, pts[1:]):
            L = math.dist(a, b)
            if L > 1e-9:
                segs.append((a, b, j, acc, ((b[0] - a[0]) / L, (b[1] - a[1]) / L)))
            acc += L
        total.append(acc)
        ring.append(pts[0] == pts[-1])
    reach = 40.0
    grid = {}
    for k, (a, b, _, _, _) in enumerate(segs):
        for gx in range(math.floor((min(a[0], b[0]) - reach) / reach), math.floor((max(a[0], b[0]) + reach) / reach) + 1):
            for gy in range(math.floor((min(a[1], b[1]) - reach) / reach), math.floor((max(a[1], b[1]) + reach) / reach) + 1):
                grid.setdefault((gx, gy), []).append(k)
    cos_t = math.cos(math.radians(PARALLEL_DEG))
    best = None
    for k, (a, b, j, s0, d) in enumerate(segs):
        L = math.dist(a, b)
        n = max(1, int(L / SAMPLE))
        for i in range(n + 1):
            f = i / n
            p = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
            sp = s0 + f * L
            for m in grid.get((math.floor(p[0] / reach), math.floor(p[1] / reach)), ()):
                a2, b2, j2, s2, d2 = segs[m]
                if abs(d[0] * d2[0] + d[1] * d2[1]) < cos_t:
                    continue                    # not alongside
                dist = _seg_dist(p, a2, b2)
                if dist < 1e-6:
                    continue                    # touching (a junction / the same place)
                if j2 == j:
                    # the same strand: only parts far apart ALONG it (a ring's
                    # other side across a wall), never its own neighbourhood
                    L2 = math.dist(a2, b2)
                    arc = 0.0 if s2 <= sp <= s2 + L2 else min(abs(s2 - sp), abs(s2 + L2 - sp))
                    if ring[j]:
                        arc = min(arc, total[j] - arc)
                    if arc < 3.0 * dist:
                        continue
                if best is None or dist < best:
                    best = dist
    return best


def _web_pitches(diagnostics) -> list:
    out = []
    for info in ((diagnostics or {}).get('lattice') or {}).values():
        for reg in (info or {}).get('regions') or []:
            wa = reg.get('web_angle')
            if wa and wa.get('pitch'):
                out.append(float(wa['pitch']))
    return out


def scale_construction_report(resolved, assembly, geometry, diagnostics, bead, return_overlap,
                              envelope=False) -> dict:
    """{'findings': [...], 'warnings': [...]} for a stack placed by Scale
    construction. geometry: {design id: (polylines, bead)} — the CANONICAL
    layers (the editing view: envelope rings — a skin pair then sits one
    bead inside them: spacing = ring spacing − W, preliminary).
    diagnostics: {design id: the canonical build's diagnostics} (lattice
    pitch), or {}."""
    out = {'findings': [], 'warnings': []}
    if getattr(assembly, 'dimension_behavior', 'scale') != 'scale' and assembly.center_mode != 'multiple':
        return out
    W = float(bead or 3.0)
    R = float(return_overlap if return_overlap is not None else 0.75)
    e_min = max(1.0, 0.5 * W)
    d_par, pitch = {}, {}
    for inst in resolved.instances:
        d = inst.design_id
        if d in d_par or d not in geometry:
            continue
        polys = geometry[d][0]
        if envelope:
            rings = [pl for pl in polys if (pl.get('role') or 'ring') == 'ring']
            dd = _closest_parallel(rings, skip_lattice=False)
            d_par[d] = None if dd is None else dd - W
        else:
            d_par[d] = _closest_parallel(polys)
        p_ = _web_pitches(diagnostics.get(d))
        pitch[d] = min(p_) if p_ else None
    first = {}
    for inst in resolved.instances:
        k = inst.transform.scale
        if k >= 1.0 - 1e-9:
            continue
        d = inst.design_id
        dp, S = d_par.get(d), pitch.get(d)
        if dp is not None and dp > 0 and W - k * dp > R + 1e-6 and ('parallel', d) not in first:
            first[('parallel', d)] = inst
            out['findings'].append({
                'kind': 'parallel_overlap', 'layer': inst.index, 'design': d, 'scale': round(k, 4),
                'spacing': round(k * dp, 3), 'required': round(W - R, 3), 'preliminary': bool(envelope),
                'message': (f'Scale construction: from layer {inst.index + 1} (scale {k:.3f}) neighbouring beads '
                            f'run {k * dp:.2f} in apart — closer than the bead allows ({W - R:.2f} in: '
                            f'{W:g} in Bead Width, {R:g} in Parallel Wall Overlap)'
                            + (' — preliminary (architecture)' if envelope else ''))})
        if S is not None and k * S < e_min - 1e-6 and ('pitch', d) not in first:
            first[('pitch', d)] = inst
            out['findings'].append({
                'kind': 'web_pitch_floor', 'layer': inst.index, 'design': d, 'scale': round(k, 4),
                'pitch': round(k * S, 3), 'floor': round(e_min, 3),
                'message': (f'Scale construction: from layer {inst.index + 1} (scale {k:.3f}) the Skin + Web '
                            f'web\'s pitch is {k * S:.2f} in — under its physical floor ({e_min:.2f} in for a '
                            f'{W:g} in bead)')})
    out['warnings'] = [f['message'] for f in out['findings']]
    return out
