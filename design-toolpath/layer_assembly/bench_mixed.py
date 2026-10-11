"""
MIXED-FEATURE performance benchmark (development tool, not a test):

    design_proto/.venv/bin/python -m layer_assembly.bench_mixed [--workers N] [--layers N] [--pause S]
    design_proto/.venv/bin/python -m layer_assembly.bench_mixed --project out.json   (for bench_browser.js)
    … --envelope   (the page's requests since the deferred print paths: envelope editing;
                    the print plan rows are then the print preparation)

from design-toolpath/. A realistic connected building (the project-file
fixture: circle + two rectangles + a link + a garden wall, rounded junctions)
with FOUR Wall Systems (Skin + Web rooms, an Interleaved room, a Chained Loop
link, a Parallel garden wall), Designer openings in two derived designs,
Assembly self-supporting openings + a staggered array + a horizontal array, a
Designer-opening conversion, section transforms + an Assembly-wide transform,
a Planar Top Cut, ≥ 80 layers.

It drives the SAME HTTP requests as the browser (assembly.js asmResolve:
canonical geometry → /api/assembly/resolve → /api/assembly/geometry for the
variants the client does not hold yet; print_plan stream for the
Simulation) through the Flask test client, keeping a client-side variant cache
exactly as the page does, and reports per scenario: wall time, server stage
timings (`debug_timing`), variants returned / new / built, geometry fetch
time and payload size.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _fixture():
    sys.path.insert(0, os.path.join(HERE, '..', 'design_proto', 'tests'))
    sys.path.insert(0, os.path.join(HERE, '..', 'design_proto'))
    sys.path.insert(0, os.path.join(HERE, '..'))
    import test_project_file as TP
    ds = TP.designs()
    base = ds[0]['document']
    # an INTERLEAVED room: Rect 2 gets its own Wall System
    ws1 = next(w for w in base['wall_systems'] if w['id'] == 'WS1')
    ws1['members'] = ['R1', 'C1']
    base['wall_systems'].append({'id': 'WS4', 'name': 'Studio', 'type': 'interleaved', 'thickness': 10,
                                 'align': 'auto', 'print_reference': False,
                                 'params': {'pattern': 'wave', 'paths': 4, 'period': 30}, 'members': ['R2'], 'web': {}})
    return ds


def assembly(layers=84):
    """Footing / door level / upper; section + assembly-wide transforms
    (footprint centre mode: the common case); openings, arrays, a plane."""
    h = layers * 1.5
    return {'layer_height': 1.5, 'name': 'Bench house', 'max_overhang': 1.25,
            'sections': [{'design_id': 'base', 'height': round(h * 0.3 / 1.5) * 1.5, 'label': 'Footing',
                          'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0}},
                         {'design_id': 'd1', 'height': round(h * 0.45 / 1.5) * 1.5, 'label': 'Door level',
                          'transform': {'profile': 'quadratic', 'shift': [0.125, 0], 'scale_in': 0.03125}},
                         {'design_id': 'd2', 'height': h - round(h * 0.3 / 1.5) * 1.5 - round(h * 0.45 / 1.5) * 1.5,
                          'label': 'Upper'}],
            'transform': {'profile': 'linear', 'shift': [0, 0.0625], 'scale_in': 0},
            'objects': [], 'overrides': [], 'center_mode': 'footprint', 'centers': [], 'center_counter': 0,
            'profiles': [
                {'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R1', 'edge': 0}, 'position': 140.0,
                 'z_bottom': 9.0, 'height': 36.0, 'width': 16.0, 'upper': 'pointed', 'lower': 'pointed',
                 'bottom_depth': 18.0, 'upper_height': 18.0, 'middle': False, 'middle_height': None,
                 'label': 'Window'},
                {'kind': 'opening', 'id': 'p3', 'wall': {'source': 'R1', 'edge': 2}, 'position': 30.0,
                 'z_bottom': 6.0, 'height': 30.0, 'width': 12.0, 'upper': 'curve', 'lower': 'round',
                 'bottom_depth': 6.0, 'upper_height': 15.0, 'middle': True, 'middle_height': 9.0, 'label': '',
                 'array': {'arrangement': 'staggered', 'count': 1, 'cols': 3, 'rows': 2, 'h_spacing': 18.0,
                           'v_spacing': 6.0, 'stagger': None}},
                {'kind': 'opening', 'id': 'p5', 'wall': {'source': 'R2', 'edge': 2}, 'position': 20.0,
                 'z_bottom': 60.0, 'height': 24.0, 'width': 12.0, 'upper': 'pointed', 'lower': 'pointed',
                 'bottom_depth': 12.0, 'upper_height': 12.0, 'middle': False, 'middle_height': None, 'label': 'Row',
                 'array': {'arrangement': 'horizontal', 'count': 2, 'cols': 3, 'rows': 2, 'h_spacing': 34.0,
                           'v_spacing': 6.0, 'stagger': None}},
                {'kind': 'plane', 'id': 'p2', 'sources': ['L1'], 'systems': ['WS1'], 'exclude': ['R2'],
                 'direction': 30.0, 'slope': 0.2, 'anchor': [200.0, 200.0], 'z_anchor': layers * 1.5 - 18,
                 'label': 'Roof'}],
            'profile_counter': 5}


class Client:
    """The page's request sequence and its client-side variant cache."""

    def __init__(self, app, designs, envelope=False, wall_systems=None):
        self.c = app.test_client()
        self.designs = designs
        self.wall_systems = wall_systems      # (the Wall Group model: the project's Wall Systems)
        self.variants = {}
        self.geometry = None
        self.envelope = envelope              # the page's ENVELOPE editing requests (deferred print paths)

    def post(self, url, body):
        r = self.c.post(url, data=json.dumps(body), content_type='application/json')
        return r.get_json(), len(r.data)

    def resolve(self, asm):
        out = {}
        t0 = time.perf_counter()
        if self.geometry is None:                       # asmLoadGeometry (once per design set)
            ids = sorted({s['design_id'] for s in asm['sections']})
            g, n = self.post('/api/assembly/geometry', {'designs': self.designs, 'ids': ids,
                                                        **({'envelope': True} if self.envelope else {})})
            self.geometry = g['designs']
            out['canonical_s'] = round(time.perf_counter() - t0, 3)
        fps = {}
        for k, g in self.geometry.items():
            xs = [p[0] for pl in g['polylines'] for p in pl['pts']]
            ys = [p[1] for pl in g['polylines'] for p in pl['pts']]
            fps[k] = [min(xs), min(ys), max(xs), max(ys)]
        t1 = time.perf_counter()
        res, nbytes = self.post('/api/assembly/resolve', {'assembly': asm, 'design_ids': list(self.geometry),
                                                          'footprints': fps, 'designs': self.designs,
                                                          'debug_timing': True,
                                                          **({'envelope': True} if self.envelope else {}),
                                                          **({'wall_systems': self.wall_systems}
                                                             if self.wall_systems is not None else {})})
        if 'error' in res:
            raise RuntimeError(res['error'])
        out['resolve_s'] = round(time.perf_counter() - t1, 3)
        out['resolve_kb'] = round(nbytes / 1024)
        need = {k: {'design_id': v['design_id'], 'transforms': v['transforms'], 'cuts': v.get('cuts') or []}
                for k, v in (res.get('variants') or {}).items() if k not in self.variants}
        t2 = time.perf_counter()
        if need:
            g, gb = self.post('/api/assembly/geometry', {'designs': self.designs, 'variants': need,
                                                         **({'envelope': True} if self.envelope else {})})
            self.variants.update(g['designs'])
            out['geometry_kb'] = round(gb / 1024)
        out['geometry_s'] = round(time.perf_counter() - t2, 3)
        out['wall_s'] = round(time.perf_counter() - t0, 3)
        tm = res.get('timing') or {}
        out['server'] = {k: tm[k] for k in ('designs_and_groups', 'resolve_layers', 'profiles', 'variant_geometry',
                                            'envelope_geometry', 'support_analysis', 'serialize', 'total')
                         if k in tm}
        out['variants'] = len(res.get('variants') or {})
        out['variants_fetched'] = len(need)
        out['variants_built'] = tm.get('envelopes_new' if self.envelope else 'variants_new')
        out['findings'] = len(((res.get('support') or {}).get('findings')) or [])
        out['profiles_valid'] = {p['id']: p['valid'] for p in res.get('profiles') or []}
        return res, out

    def plan(self, asm, speeds=None):
        body = {'assembly': dict(asm, **({'print_speeds': speeds} if speeds else {})), 'designs': self.designs,
                'detail': 'stream',
                **({'wall_systems': self.wall_systems, 'require_generated': True}
                   if self.wall_systems is not None else {})}
        if self.wall_systems is not None and not getattr(self, '_generated', None) == json.dumps(asm, sort_keys=True):
            t0 = time.perf_counter()        # GENERATE WALL SYSTEMS first (the Simulation plans only from it)
            g, _ = self.post('/api/wallsystems/generate', {'assembly': asm, 'designs': self.designs,
                                                           'wall_systems': self.wall_systems})
            if g.get('error'):
                raise RuntimeError(g['error'])
            self._generated = json.dumps(asm, sort_keys=True)
            self.generate_s = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        res, nbytes = self.post('/api/assembly/print_plan', body)
        if res.get('error'):
            raise RuntimeError(res['error'])
        return res, {'wall_s': round(time.perf_counter() - t0, 3), 'kb': round(nbytes / 1024),
                     'items': len(res['stream']['items']['kind'])}


def scenarios(layers):
    """[(name, assembly dict)] — each an EDIT of the previous state, as a user makes them."""
    a0 = assembly(layers)
    out = [('cold', a0), ('warm (unchanged)', a0)]
    a = copy.deepcopy(a0)
    a['profiles'][0]['width'] = 14.0                               # opening dimension (p1, constrained-valid)
    out.append(('opening width', a))
    a = copy.deepcopy(a)
    a['profiles'][2]['array']['count'] = 3                         # array count (p5)
    out.append(('array count', a))
    a = copy.deepcopy(a)
    a['profiles'][2]['array']['h_spacing'] = 36.0                  # array spacing
    out.append(('array spacing', a))
    a = copy.deepcopy(a)
    a['transform'] = {'profile': 'linear', 'shift': [0.0625, 0.0625], 'scale_in': 0}   # Assembly Transform
    out.append(('assembly transform', a))
    a = copy.deepcopy(a)
    a['profiles'][3]['slope'] = 0.25                                # Planar Top Cut
    out.append(('planar cut slope', a))
    a = copy.deepcopy(a)
    a['max_overhang'] = 1.5                                         # support only
    out.append(('max overhang (support only)', a))
    return out


def run(layers=84, workers=None, only=None, pause=0.0, envelope=False, groups=False):
    """envelope: the page's editing requests since the deferred print paths
    (2026-10-11) — envelope resolves; the print plan rows are then the
    PRINT PREPARATION (full variants + routing + bead-level support)."""
    if workers is not None:
        os.environ['LAYER_ASSEMBLY_WORKERS'] = str(workers)
    ds = _fixture()
    from app import app
    from layer_assembly import web
    web._SOURCES.clear()
    web._ENV_SOURCES.clear()
    web._CANON.clear()
    web._PLANS.clear()
    web._GENERATED.clear()
    web._ARCH_LIBS.clear()
    systems = None
    if groups:                            # (the current model: Wall Groups + project Wall Systems)
        from wall_groups import split_designs
        ds, systems = split_designs(ds)
    cl = Client(app, ds, envelope, systems)
    rows = []
    last = None
    for name, a in scenarios(layers):
        if only and name not in only:
            continue
        if pause and rows:
            time.sleep(pause)                    # (the user's think time between edits)
        res, m = cl.resolve(a)
        m['scenario'] = name
        m['layers'] = res['total_layers']
        rows.append(m)
        last = a
        print(json.dumps(m), flush=True)
    for name, sp in (('print plan (cold)', None), ('print plan (warm)', None),
                     ('print speeds only', {'print': 3.0, 'travel': 6.0})):
        _, m = cl.plan(last, sp)
        m['scenario'] = name
        if getattr(cl, 'generate_s', None) is not None:
            m['generate_s'] = cl.generate_s
            cl.generate_s = None
        rows.append(m)
        print(json.dumps(m), flush=True)
    return rows


def project_file(path, layers=84):
    """Write the benchmark building as a PROJECT FILE (Open in the page:
    layer_assembly/bench_browser.js) — in the current model (2026-10-11):
    Wall Groups in the designs + the project's Wall Systems (the legacy
    fixture split exactly as a schema-1 file is migrated)."""
    ds = _fixture()
    from app import app
    from wall_groups import split_designs
    ds, systems = split_designs(ds)
    proj = {'name': 'Bench house',
            'designer': {'designs': ds, 'active_design': 'base', 'id_counter': 40, 'design_counter': 3},
            'assembly': {'state': assembly(layers), 'header_counter': 0}, 'wall_systems': systems}
    r = app.test_client().post('/api/project/save', data=json.dumps({'project': proj}),
                               content_type='application/json').get_json()
    if 'error' in r:
        raise RuntimeError(r['error'])
    with open(path, 'w') as f:
        json.dump(r['file'], f)
    return path


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--project' in args:
        print(project_file(args[args.index('--project') + 1]))
        sys.exit(0)
    w = int(args[args.index('--workers') + 1]) if '--workers' in args else None
    n = int(args[args.index('--layers') + 1]) if '--layers' in args else 84
    pz = float(args[args.index('--pause') + 1]) if '--pause' in args else 0.0
    run(n, w, pause=pz, envelope='--envelope' in args, groups='--groups' in args)
