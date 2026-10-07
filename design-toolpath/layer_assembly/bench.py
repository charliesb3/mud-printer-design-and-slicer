"""
Assembly performance benchmark (development tool, not a test):

    design_proto/.venv/bin/python -m layer_assembly.bench [layers …]

from design-toolpath/. A realistic wall network (circle — line — rectangle,
rounded junctions, wave lattice) and a derived design with openings, stacked
to ~N layers (Base / Openings / Base), two transform groups scaling about
their own centres. Reports the /api/assembly/resolve stage timings (cold:
every grouped layer's semantic variant resolved; warm: a Maximum-overhang
change, nothing rebuilt) and the mean Designer build stages per variant.
"""
from __future__ import annotations

import os
import sys
import time


def designs():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'design_proto', 'tests'))
    import multi_opening_fixtures as F
    base = F.document(())
    base['junction_style'], base['junction_radius'] = 'round', 6
    cuts = {o['id']: o for o in F.document((('R', 120, 40), ('C', 450, 30)))['openings']}
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': base},
            {'id': 'cuts', 'name': 'Openings', 'parent': 'base', 'patch': {'openings': cuts}, 'settings': {}}]


def assembly(layers, max_overhang=1.5):
    h = layers * 1.5 / 3
    return {'layer_height': 1.5, 'max_overhang': max_overhang,
            'sections': [{'design_id': 'base', 'height': h}, {'design_id': 'cuts', 'height': h},
                         {'design_id': 'base', 'height': h}],
            'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 18.0 / layers},
            'center_mode': 'multiple', 'center_counter': 2,
            'centers': [{'id': 'c1', 'x': 480, 'y': 180, 'sources': ['C']},
                        {'id': 'c2', 'x': 220, 'y': 180, 'sources': ['R']}]}


def run(layers):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'design_proto'))
    from app import app
    from layer_assembly import web
    web._SOURCES.clear()
    c = app.test_client()
    ds = designs()
    t = time.perf_counter()
    cold = c.post('/api/assembly/resolve', json={'assembly': assembly(layers), 'designs': ds,
                                                 'debug_timing': True}).get_json()
    wall = time.perf_counter() - t
    warm = c.post('/api/assembly/resolve', json={'assembly': assembly(layers, 2.0), 'designs': ds,
                                                 'debug_timing': True}).get_json()
    src = next(iter(web._SOURCES.values()))
    stages = {}
    for g in src._variants.values():
        for k, v in ((g.diagnostics or {}).get('timing') or {}).items():
            stages.setdefault(k, []).append(v)
    mean = {k: round(sum(v) / len(v), 3) for k, v in stages.items()}
    return {'layers': cold['total_layers'], 'wall_s': round(wall, 2), 'cold': cold['timing'],
            'warm': warm['timing'], 'per_variant_build_s': mean,
            'findings': len(cold['support']['findings']), 'groups': len(cold['support'].get('groups') or [])}


if __name__ == '__main__':
    for n in [int(x) for x in sys.argv[1:]] or [50, 100, 300]:
        r = run(n)
        print(f"\n{r['layers']} layers — {r['wall_s']} s cold")
        for k in ('cold', 'warm', 'per_variant_build_s'):
            print(f'  {k}: {r[k]}')
        print(f"  support: {r['findings']} findings in {r['groups']} groups")
