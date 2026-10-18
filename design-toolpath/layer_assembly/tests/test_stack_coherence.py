"""
CROSS-Z COHERENCE / SUPPORT BASELINES (WALL_ARCHITECTURE.md §Z.5, stage Z1,
2026-10-17): the product metrics of stack_coherence.py on the representative
stacks, frozen in layer_assembly/tests/coherence/<stack>.json. A change may
only IMPROVE them: support findings, construction drift (max / median),
topology events and NONLOCAL events, landing changes must not grow beyond
the baseline (small tolerances for float noise). COHERENCE_UPDATE=1 rewrites
the baselines — deliberately, after reviewing the numbers (the Z2–Z8 stages
are expected to improve them step by step and re-baseline each time);
COHERENCE_DUMP=<dir> writes the per-pair rows for inspection.
The harness measures deposited geometry only: it needs no change when the
construction pipeline changes.
"""
import json
import os

import pytest

from layer_assembly.tests import stack_coherence as SC

TOL_IN = 0.05            # float noise on a distance in inches
STACKS = list(SC.STACKS) + (list(SC.FULL_STACKS) if os.environ.get('COHERENCE_FULL') else [])


def _load(name):
    p = SC.baseline_path(name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _check_not_worse(name, got, base):
    g, b = got['summary'], base['summary']
    bad = []

    def le(path, tol=0):
        gv, bv = g, b
        for k in path:
            gv, bv = gv[k], bv[k]
        if gv > bv + tol:
            bad.append(f"{'.'.join(path)}: {gv} > baseline {bv}")
    le(('support', 'findings'))
    le(('support', 'bridge'))
    le(('construction', 'max'), TOL_IN)
    le(('construction', 'p95'), TOL_IN)
    le(('construction', 'median'), TOL_IN)
    le(('events', 'clusters'))
    le(('events', 'nonlocal_clusters'))
    le(('events', 'nonlocal_vertices'))
    le(('landings', 'changes'))
    assert g['layers'] == b['layers'], (g['layers'], b['layers'])
    assert not bad, f"{name} is WORSE than its baseline:\n  " + '\n  '.join(bad) + \
        f"\n  now:      {SC.one_line(name, g)}\n  baseline: {SC.one_line(name, b)}"


@pytest.mark.parametrize('name', STACKS)
def test_stack_coherence(name):
    got = SC.run(name)
    dump = os.environ.get('COHERENCE_DUMP')
    if dump:
        os.makedirs(dump, exist_ok=True)
        with open(os.path.join(dump, name + '.json'), 'w') as f:
            json.dump(got, f, indent=1)
    if os.environ.get('COHERENCE_UPDATE'):
        os.makedirs(SC.BASELINES, exist_ok=True)
        with open(SC.baseline_path(name), 'w', encoding='utf-8') as f:
            json.dump({'summary': got['summary'],
                       'pairs': [{k: v for k, v in r.items()} for r in got['pairs']]}, f, indent=1, sort_keys=True)
        return
    base = _load(name)
    assert base is not None, f'no baseline for {name}: run with COHERENCE_UPDATE=1'
    _check_not_worse(name, got, base)


def test_metrics_on_a_synthetic_pair():
    """The metric definitions on hand-made layers: a coherent pair drifts 0
    with no events; a re-phased stitch is one event, NONLOCAL when nothing
    architectural moved, LOCAL when the architecture moved next to it."""
    def layer(idx, stitch_x, env_shift=0.0):
        skin0 = [(0.0, 0.0), (100.0, 0.0)]
        skin1 = [(0.0, 10.0), (100.0, 10.0)]
        web = [(stitch_x, 2.25), (stitch_x + 10.0, 7.75), (stitch_x + 20.0, 2.25)]     # (on the contact rails, off the skins)
        # (env_shift moves the wall SIDEWAYS: every envelope vertex moves — a
        # wall sliding along its own axis would move only at its ends)
        env = [[(0.0, -1.5 + env_shift), (100.0, -1.5 + env_shift), (100.0, 11.5 + env_shift),
                (0.0, 11.5 + env_shift), (0.0, -1.5 + env_shift)]]
        return {'index': idx, 'design_id': 'd', 'key': 'd', 'z': idx * 1.5, 'thickness': 10.0, 'junctions': [],
                'placed': [{'id': 'a', 'role': 'outer', 'pts': skin0}, {'id': 'b', 'role': 'inner', 'pts': skin1},
                           {'id': 'w', 'role': 'lattice', 'pts': web}],
                'env': env}

    class Rep:
        findings = []
        max_overhang = 1.0
    same = SC.measure({'layers': [layer(0, 40.0), layer(1, 40.0)], 'support': Rep(), 'bead': 3.0, 'h': 1.0})
    assert same['summary']['construction']['max'] == 0.0 and same['summary']['events']['clusters'] == 0
    moved = SC.measure({'layers': [layer(0, 40.0), layer(1, 46.0)], 'support': Rep(), 'bead': 3.0, 'h': 1.0})
    e = moved['summary']['events']
    assert moved['summary']['construction']['max'] > 1.0 and e['clusters'] >= 1 and e['nonlocal_clusters'] == e['clusters']
    local = SC.measure({'layers': [layer(0, 40.0), layer(1, 46.0, env_shift=5.0)], 'support': Rep(), 'bead': 3.0, 'h': 1.0})
    e2 = local['summary']['events']
    assert e2['clusters'] >= 1 and e2['nonlocal_clusters'] == 0        # the wall itself moved 5 in: explained
