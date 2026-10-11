"""
PARITY: the UI's opening constraints (design_proto/static/opening_shape.js)
and the backend's (layer_assembly/opening_shape.py) give the SAME shape for
the same edit — constrained parameters, component heights, per-layer cut
widths, the limits used as hints. (The UI constrains while editing; the
backend validates exactly those numbers: a mismatch would make the preview
and the resolved opening differ.)
"""
import json
import os
import random
import shutil
import subprocess

import pytest

from layer_assembly import opening_shape as O

SCRIPT = os.path.join(os.path.dirname(__file__), 'js', 'opening_shape_cases.js')


def _cases():
    rnd = random.Random(20261010)
    shapes = [('diamond', u, u) for u in O.UPPERS['diamond']] + \
             [('arch', u, l) for u in O.UPPERS['arch'] for l in O.LOWERS['arch']]
    arrays = [None, {'arrangement': 'grid', 'cols': 2, 'rows': 2, 'h_spacing': 9, 'v_spacing': 3},
              {'arrangement': 'diagonal_up', 'count': 3, 'h_spacing': 4, 'v_spacing': -6},
              {'arrangement': 'staggered', 'cols': 3, 'rows': 2, 'h_spacing': 7.5, 'v_spacing': 1.3, 'stagger': None}]
    out = []
    for i in range(160):
        shape, up, lo = shapes[i % len(shapes)]
        lh = rnd.choice([0.5, 1.0, 1.25, 1.5, 1.75])
        h = rnd.choice([0.25, 0.5, 1.0, 1.5])
        bead = rnd.choice([1.0, 2.0, 3.0])
        s = {'shape': shape, 'upper': up, 'lower': lo, 'width': rnd.choice([6, 12, 18.5, 24, 36, 48]),
             'height': lh * rnd.randint(4, 40), 'z_bottom': lh * rnd.randint(0, 6) + (0.3 if i % 17 == 0 else 0.0),
             'array': arrays[i % len(arrays)], 'driver': rnd.choice([None, 'width', 'height', 'upper_height'])}
        if shape == 'arch':
            s.update({'bottom_depth': rnd.choice([3.0, 8.5, 12.0, 30.0]) if lo != 'flat' else None,
                      'upper_height': rnd.choice([4.0, 10.0, 30.0, 52.5]),
                      'middle': rnd.choice([True, False]), 'middle_height': rnd.choice([0.0, 12.0, 37.5])})
            O.sync(s)
        edited = rnd.choice(['width', 'height' if shape == 'diamond' else 'upper_height', 'overhang', 'upper',
                             'middle_height', 'lower'])
        out.append([s, edited, lh, h, bead])
    # older projects
    out.append([{'shape': 'lens', 'width': 24, 'height': 56, 'z_bottom': 6, 'array': None}, 'load', 1.5, 1.5, 3.0])
    out.append([{'shape': 'arch', 'lower': 'ellipse', 'upper': 'curve', 'width': 20, 'height': 50, 'z_bottom': 0,
                 'array': None}, 'load', 1.5, 1.5, 3.0])
    out.append([{'shape': 'arch', 'lower': 'flat', 'upper': 'pointed', 'width': 24, 'height': 40, 'z_bottom': 0,
                 'closure': 20.0, 'array': None}, 'load', 1.5, 1.5, 3.0])
    out.append([{'shape': 'arch', 'lower': 'curve', 'upper': 'pointed', 'width': 20, 'height': 50, 'z_bottom': 0,
                 'bottom_depth': 7.5, 'upper_height': 30, 'middle': True, 'middle_height': 12.5, 'array': None},
                'load', 1.5, 1.5, 3.0])                      # the former Curved (half-ellipse) lower
    out.append([{'shape': 'diamond', 'upper': 'pointed', 'lower': 'curve', 'bottom_depth': 7.0, 'width': 24,
                 'height': 40, 'z_bottom': 3, 'array': None}, 'load', 1.25, 1.0, 3.0])
    return out


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_and_backend_constrain_identically():
    cases = _cases()
    proc = subprocess.run(['node', SCRIPT], input=json.dumps(cases), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)
    close = lambda a, b: (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-6)
    for (s, edited, lh, h, bead), j in zip(cases, js):
        q, info = O.constrain(s, edited, lh, h, bead)
        tag = (s, edited, lh, h, bead)
        for k in ('shape', 'upper', 'lower', 'driver', 'middle'):
            assert q.get(k) == j['q'].get(k), (k, tag)
        for k in ('width', 'height', 'upper_height', 'middle_height', 'bottom_depth'):
            assert close(q.get(k), j['q'].get(k)), (k, q.get(k), j['q'].get(k), tag)
        assert (info['conflict'] is None) == (j['info']['conflict'] is None), tag
        assert sorted(info['changed']) == sorted(j['info']['changed']), tag
        ev = O.evaluate(q, lh, h, bead)
        assert ev['ok'] == j['ev']['ok'] and all(close(ev[k], j['ev'][k]) for k in ('d', 'm', 'U', 'H', 'worst')), tag
        if ev['ok']:
            ws = O.layer_widths(q['z_bottom'], q['width'], ev['d'], ev['m'], ev['U'], q['shape'], q['upper'],
                                q['lower'], lh, O.wmin(bead, h))
            assert [k for k, _ in ws] == [k for k, _ in j['ws']], tag
            assert all(abs(a - b) < 1e-9 for (_, a), (_, b) in zip(ws, j['ws'])), tag
        lim = O.limits(s, lh, h, bead)
        assert close(lim['max_width'], j['lim']['max_width']) and close(lim['min_height'], j['lim']['min_height']), tag
    # the cases exercise both outcomes of every rule
    assert sum(1 for j in js if j['info']['changed']) > 40 and sum(1 for j in js if not j['info']['changed']) > 10
