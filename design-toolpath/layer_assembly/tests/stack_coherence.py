"""
CROSS-Z COHERENCE / SUPPORT HARNESS (WALL_ARCHITECTURE.md §Z.5 — stage Z1,
2026-10-17). Measures the PRODUCT requirement on a resolved, generated stack,
never an implementation: it reads only what a print would deposit (the placed
printable centrelines of every layer, as Generate Wall Systems builds them)
and the layers' architectural envelopes. It knows nothing of lattice tracks,
scaffolds, phase references or construction states, so it measures the
current pipeline and any future one alike.

Per adjacent layer pair (lower, upper), in WORLD coordinates (beads must sit
on beads physically):

  support         the authoritative bead-level support report (support.analyse_support,
                  unchanged thresholds): findings of the upper layer by kind.
  correspondence  for every CONSTRUCTION vertex of the upper layer (roles 'lattice' and
                  'wall_system' — the web / lanes / waves / chains), the distance to the
                  nearest construction strand of the layer below: max, p95, median.
                  Coherent construction moves a fraction of a bead per layer; a re-phased
                  stitch moves up to half a pitch. The same for the ARCHITECTURE strands
                  (skins, caps, single beads) as the control — they move only as the
                  Assembly moves the walls.
  events          construction vertices farther than the Maximum Overhang h from every
                  construction strand below (they rest on nothing of their own kind),
                  clustered by bead-width proximity: a cluster is one topology event —
                  a cell born or dying, a re-phased stretch, a motif that changed.
  locality        every event cluster's distance to the nearest MOVING ARCHITECTURAL
                  BOUNDARY of the pair: envelope vertices (rings / bare beads) that moved
                  more than h between the two layers, territory that appeared or vanished,
                  junction points that appeared, ceased or moved at all (> JUNCTION_MOVE:
                  a web beside a sliding junction gains / loses cells there). An event
                  within LOCAL_REACH (2 × the thickest wall) of such a boundary is
                  explained by the architecture; farther away it is NONLOCAL — the
                  "unexplained rephasing / reorganisation" number, 0 when §Z.2 is done.
  landings        construction vertices within one bead of an architecture strand of the
                  SAME layer (the web's landings on the skins, lane ends): their count per
                  layer and its change between layers — a cheap count of cells.

The numbers are deposited geometry; they do not depend on how it was made.
Baselines: test_stack_coherence.py (COHERENCE_UPDATE=1 rewrites them;
COHERENCE_DUMP=<dir> writes the per-pair rows). CLI, from design-toolpath/:

    design_proto/.venv/bin/python -m layer_assembly.tests.stack_coherence [stack …]
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
for p in (os.path.join(ROOT, 'design_proto'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

CONSTRUCTION_ROLES = ('lattice', 'wall_system')
LOCAL_REACH_FACTOR = 2.0          # × the thickest wall of the pair: how far from a moving boundary an event is local
JUNCTION_MOVE = 0.05              # in: a junction point that moved more than this between the layers is a moving boundary
DEFAULT_THICKNESS = 10.0
BASELINES = os.path.join(HERE, 'coherence')


# ---- the stack, exactly as Generate Wall Systems builds it ------------------------

def build_stack(designs, wall_systems, assembly_state, design_ids=None) -> dict:
    """Resolve the Assembly and build every layer's printed geometry (semantic
    / cut variants included) + its architectural envelope, placed in world
    coordinates by the layer's own transform (as support.analyse_support
    places them); plus the bead-level support report of the whole stack."""
    from layer_assembly import web
    from layer_assembly.source import variant_request, layer_geometry
    data = {'assembly': assembly_state, 'designs': designs,
            'design_ids': design_ids or [d['id'] for d in designs], 'wall_systems': wall_systems or []}
    st = web._resolve_stack(data)
    src, res = st['src'], st['res']
    src.prefetch([variant_request(i) for i in res.instances if i.is_variant])
    geo = dict(st['geo'])
    layers = []
    for inst in res.instances:
        g = layer_geometry(src, inst)
        if inst.is_variant and inst.geometry_key not in geo:
            geo[inst.geometry_key] = (g.polylines, g.bead_width)
        attrs = (g.diagnostics.get('routing') or {}).get('strands') or []
        T = inst.transform
        placed = []
        for j, pl in enumerate(g.polylines):
            a = attrs[j] if j < len(attrs) else {}
            pts = [T.apply(x, y) for x, y in pl['pts']]
            if pl.get('closed') and len(pts) > 2:
                pts.append(pts[0])
            placed.append({'id': pl['id'], 'role': a.get('role') or 'free', 'kind': a.get('kind'),
                           'source': a.get('source'), 'pts': pts})
        d, tr, cu = variant_request(inst)[:3]
        e = src.envelope(d, tr or None, cu or None)
        env = e.diagnostics['envelope']
        env_polys = []
        for pl in e.polylines:
            pts = [T.apply(x, y) for x, y in pl['pts']]
            if pl.get('closed') and len(pts) > 2:
                pts.append(pts[0])
            env_polys.append(pts)
        thick = max((float((w or {}).get('thickness') or 0.0) for w in (env.walls or {}).values()),
                    default=0.0) or DEFAULT_THICKNESS
        # the construction's own LOCAL boundaries (WALL_ARCHITECTURE §Z.1 15, 18): the
        # layer's corner anchors and the loops' seams — a repeat is consumed / born there
        anchors = ((g.diagnostics.get('identity') or {}).get('anchors') or [])
        corners = [T.apply(a['x'], a['y']) for a in anchors if a.get('kind') == 'corner']
        seams = []
        for inf in (g.diagnostics.get('lattice') or {}).values():
            for reg in (inf or {}).get('regions') or []:
                seams += [T.apply(q[0], q[1]) for q in (reg.get('seams') or [])]
        layers.append({'index': inst.index, 'design_id': inst.design_id, 'key': inst.geometry_key,
                       'z': inst.z_bottom, 'placed': placed, 'env': env_polys,
                       'junctions': [T.apply(j['x'], j['y']) for j in (env.junctions or [])] + corners + seams,
                       'thickness': thick})
    report = web._support_report(st, geo)
    return {'layers': layers, 'support': report, 'bead': float(st['bead'] or 3.0),
            'h': float(report.max_overhang if hasattr(report, 'max_overhang') else st['assembly'].max_overhang)}


# ---- metrics ----------------------------------------------------------------------

def _index(polys, reach):
    from layer_assembly.support import _SegIndex
    return _SegIndex([p for p in polys if len(p) >= 2], reach)


def _stats(ds):
    if not ds:
        return {'n': 0, 'max': 0.0, 'p95': 0.0, 'median': 0.0}
    s = sorted(ds)
    return {'n': len(s), 'max': s[-1], 'p95': s[min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)],
            'median': statistics.median(s)}


def _clusters(points, link):
    """Groups of points linked by distance ≤ link (single linkage, grid)."""
    if not points:
        return []
    cell = max(link, 1e-6)
    grid = {}
    for k, p in enumerate(points):
        grid.setdefault((math.floor(p[0] / cell), math.floor(p[1] / cell)), []).append(k)
    parent = list(range(len(points)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for k, p in enumerate(points):
        gx, gy = math.floor(p[0] / cell), math.floor(p[1] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in grid.get((gx + dx, gy + dy), ()):
                    if m > k and math.dist(p, points[m]) <= link:
                        a, b = find(k), find(m)
                        if a != b:
                            parent[a] = b
    groups = {}
    for k in range(len(points)):
        groups.setdefault(find(k), []).append(points[k])
    return list(groups.values())


def _densify(poly, step):
    """The polyline's vertices plus samples every `step` along its segments
    (a straight wall's envelope has vertices only at its corners; a sideways
    move must be seen along the whole edge)."""
    out = []
    for a, b in zip(poly, poly[1:]):
        out.append(a)
        L = math.dist(a, b)
        n = int(L // step)
        for k in range(1, n + 1):
            t = k * step / L
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    if poly:
        out.append(poly[-1])
    return out


def _landings(layer, bead):
    """Construction vertices within one bead of an architecture strand of the same layer."""
    arch = [p['pts'] for p in layer['placed'] if p['role'] not in CONSTRUCTION_ROLES]
    con = [p['pts'] for p in layer['placed'] if p['role'] in CONSTRUCTION_ROLES]
    if not arch or not con:
        return 0
    idx = _index(arch, bead)
    return sum(1 for pl in con for q in pl if idx.dist(q) <= bead)


def measure(stack, local_reach=None) -> dict:
    """{'summary', 'pairs'} of a built stack (build_stack)."""
    layers, rep, bead, h = stack['layers'], stack['support'], stack['bead'], stack['h']
    by_layer = {}
    for f in rep.findings:
        by_layer.setdefault(f.layer, []).append(f)
    land = {L['index']: _landings(L, bead) for L in layers}
    pairs = []
    all_con, all_arch = [], []
    for lo, up in zip(layers, layers[1:]):
        T = max(lo['thickness'], up['thickness'])
        reach = max(2.0 * T, 4.0 * bead)
        R = local_reach if local_reach is not None else LOCAL_REACH_FACTOR * T
        lo_con = [p['pts'] for p in lo['placed'] if p['role'] in CONSTRUCTION_ROLES]
        up_con = [p['pts'] for p in up['placed'] if p['role'] in CONSTRUCTION_ROLES]
        lo_arch = [p['pts'] for p in lo['placed'] if p['role'] not in CONSTRUCTION_ROLES]
        up_arch = [p['pts'] for p in up['placed'] if p['role'] not in CONSTRUCTION_ROLES]
        idx_c, idx_a = _index(lo_con, reach), _index(lo_arch, reach)
        ds_c = [min(idx_c.dist(q), reach) for pl in up_con for q in pl] if lo_con else []
        ds_a = [min(idx_a.dist(q), reach) for pl in up_arch for q in pl] if lo_arch else []
        all_con += ds_c
        all_arch += ds_a
        # an EVENT: a construction vertex with no printed bead of any role under
        # it (a lane cell rides the skin below; the physical checker reasons the
        # same way) — correspondence (above) stays construction-to-construction
        idx_all = _index(lo_con + lo_arch, reach) if (lo_con or lo_arch) else None
        far = [q for pl in up_con for q in pl if idx_all.dist(q) > h] if idx_all is not None \
            else [q for pl in up_con for q in pl]
        clusters = _clusters(far, bead)
        # the MOVING architectural boundary of this pair
        e_lo, e_up = _index(lo['env'], reach), _index(up['env'], reach)
        moving = [q for pl in up['env'] for q in _densify(pl, bead) if e_lo.dist(q) > h] + \
                 [q for pl in lo['env'] for q in _densify(pl, bead) if e_up.dist(q) > h]
        # a junction, corner or loop seam is a moving boundary as soon as it
        # moves at all (a web beside a slowly sliding one gains / loses its cells
        # there: local by definition — the territory it bounds changed)
        for js, other in ((up['junctions'], lo['junctions']), (lo['junctions'], up['junctions'])):
            for j in js:
                if not other or min(math.dist(j, o) for o in other) > JUNCTION_MOVE:
                    moving.append(j)
        m_idx = _clusters(moving, bead) if moving else []
        m_pts = [c[0] for c in m_idx]                      # one representative per boundary cluster
        events = []
        for c in clusters:
            cx = sum(q[0] for q in c) / len(c)
            cy = sum(q[1] for q in c) / len(c)
            dmin = min((math.dist((cx, cy), m) for m in moving), default=math.inf) if moving else math.inf
            events.append({'at': [round(cx, 2), round(cy, 2)], 'vertices': len(c),
                           'to_moving_boundary': None if math.isinf(dmin) else round(dmin, 2),
                           'local': dmin <= R})
        fs = by_layer.get(up['index'], [])
        pairs.append({
            'pair': [lo['index'], up['index']], 'z': round(up['z'], 3),
            'construction': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in _stats(ds_c).items()},
            'architecture': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in _stats(ds_a).items()},
            'events': {'clusters': len(events), 'vertices': len(far),
                       'nonlocal_clusters': sum(1 for e in events if not e['local']),
                       'nonlocal_vertices': sum(e['vertices'] for e in events if not e['local']),
                       'list': events[:12]},
            'moving_boundary': {'points': len(moving), 'clusters': len(m_pts), 'reach': round(R, 2)},
            'landings': {'below': land[lo['index']], 'above': land[up['index']],
                         'change': land[up['index']] - land[lo['index']]},
            'support': {'findings': len(fs), 'kinds': sorted(f.kind for f in fs),
                        'span': round(sum(f.span for f in fs), 2)},
        })
    fs_all = list(rep.findings)
    summary = {
        'layers': len(layers), 'pairs': len(pairs), 'bead': bead, 'max_overhang': h,
        'support': {'findings': len(fs_all),
                    'bridge': sum(1 for f in fs_all if f.kind == 'bridge'),
                    'overlap': sum(1 for f in fs_all if f.kind != 'bridge'),
                    'span': round(sum(f.span for f in fs_all), 2),
                    'layers': sorted({f.layer for f in fs_all})},
        'construction': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in _stats(all_con).items()},
        'architecture': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in _stats(all_arch).items()},
        'worst_pair': (max(pairs, key=lambda r: r['construction']['max'])['pair'] if pairs else None),
        'events': {'clusters': sum(r['events']['clusters'] for r in pairs),
                   'vertices': sum(r['events']['vertices'] for r in pairs),
                   'nonlocal_clusters': sum(r['events']['nonlocal_clusters'] for r in pairs),
                   'nonlocal_vertices': sum(r['events']['nonlocal_vertices'] for r in pairs),
                   'pairs_with_events': sum(1 for r in pairs if r['events']['clusters']),
                   'pairs_with_nonlocal': sum(1 for r in pairs if r['events']['nonlocal_clusters'])},
        'landings': {'min': min(land.values()) if land else 0, 'max': max(land.values()) if land else 0,
                     'changes': sum(abs(r['landings']['change']) for r in pairs),
                     'pairs_changed': sum(1 for r in pairs if r['landings']['change'])},
    }
    return {'summary': summary, 'pairs': pairs}


# ---- the representative stacks ---------------------------------------------------

def _golden_project(name):
    from layer_assembly.tests.golden import harness as H
    P, _ = H.load_project(os.path.join(H.PROJECTS, name + '.muddesign'))
    return P['designer']['designs'], P.get('wall_systems') or [], (P.get('assembly') or {}).get('state') or {}


def stack_scaled_circle_skin_web():
    """A circle × square lobe building (trimmed, rounded junctions), Skin + Web
    zigzag; the CIRCLE alone scaled gradually through 20 layers by its
    transform group — a junction sliding along the square's walls (shrinking /
    growing territory) and a curved run under uniform scale."""
    from layer_assembly.tests import test_wall_system_z_coherence as Z
    a = Z.assembly(20)
    a['sections'][0]['transform'] = {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0.15}
    return Z.designs(False), Z.SYSTEMS, a


def stack_scaled_circle_interleaved():
    """The same building and transform with Interleaved Waves (4 paths) on
    both walls: a GENERATED construction under gradual scale."""
    from layer_assembly.tests import test_wall_system_z_coherence as Z
    a = Z.assembly(20)
    a['sections'][0]['transform'] = {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0.15}
    systems = [{'id': 'S1', 'name': 'Wall System 1', 'type': 'interleaved', 'params': {'paths': 4},
                'members': ['C', 'R'], 'web': {}}]
    return Z.designs(False), systems, a


def stack_reference_network_wave():
    """The reference network (Circle 1 / Rect 1 / Rect 2 in three transform
    groups scaling independently, Line 1 unassigned, round junctions, wave
    web), 16 layers, Base then the 'gaps' design with an opening: the
    documented transformed-junction problem (the lens between Rect 1 and
    Circle 1 shrinking away) plus an opening appearing mid-stack."""
    from layer_assembly.tests import test_reference_network_stack as RS
    return RS.designs(), None, RS.assembly(layers=16)


def stack_real_test3b():
    """The operator's `test 3 (1)` project: an Interleaved ring + rectangle
    with trims, Line 1 an Out-and-Back excursion, Variant openings, two
    profiles, 73 layers (golden case real_test3b)."""
    return _golden_project('real_test3b')


def stack_real_10xtest1():
    """The operator's `10xtest1`: Skin + Web 6 in walls (no cavity since the
    physical wall width: skins only) × a thin Out-and-Back line, two planar
    top cuts + a header, 73 layers (golden case real_10xtest1) — territory
    consumed at moving cut faces."""
    return _golden_project('real_10xtest1')


def stack_mixed_benchmark():
    """The 84-layer mixed benchmark (four Wall Systems, openings, arrays, a
    planar cut, section + Assembly transforms) — opt-in (COHERENCE_FULL=1):
    minutes in one process."""
    from layer_assembly import bench_mixed as B
    return B._fixture(), None, B.assembly(84)


STACKS = {
    'scaled_circle_skin_web': stack_scaled_circle_skin_web,
    'scaled_circle_interleaved': stack_scaled_circle_interleaved,
    'reference_network_wave': stack_reference_network_wave,
    'real_test3b': stack_real_test3b,
    'real_10xtest1': stack_real_10xtest1,
}
FULL_STACKS = {'mixed_benchmark': stack_mixed_benchmark}


def run(name) -> dict:
    designs, ws, asm = (STACKS.get(name) or FULL_STACKS[name])()
    return measure(build_stack(designs, ws, asm))


def baseline_path(name) -> str:
    return os.path.join(BASELINES, name + '.json')


def one_line(name, s) -> str:
    c, e, sup, ld = s['construction'], s['events'], s['support'], s['landings']
    return (f"{name:28s} layers {s['layers']:3d}  support {sup['findings']:3d} (bridge {sup['bridge']}, overlap {sup['overlap']})  "
            f"construction drift max {c['max']:6.2f} p95 {c['p95']:5.2f} median {c['median']:5.2f} in  "
            f"events {e['clusters']:3d} ({e['vertices']} v) nonlocal {e['nonlocal_clusters']:3d} ({e['nonlocal_vertices']} v) "
            f"in {e['pairs_with_nonlocal']}/{s['pairs']} pairs  landings {ld['min']}–{ld['max']} changes {ld['changes']}")


def main(argv=None):
    import time
    names = (argv or sys.argv[1:]) or list(STACKS)
    for n in names:
        t0 = time.perf_counter()
        m = run(n)
        print(one_line(n, m['summary']), f' [{time.perf_counter() - t0:.1f} s]')
        dump = os.environ.get('COHERENCE_DUMP')
        if dump:
            os.makedirs(dump, exist_ok=True)
            with open(os.path.join(dump, n + '.json'), 'w') as f:
                json.dump(m, f, indent=1)


if __name__ == '__main__':
    main()
