"""
CONSTRUCTION IDENTITY through the Assembly — stage Z2 (WALL_ARCHITECTURE.md
§Z.2 §1–2, 2026-10-17). Whole stacks built exactly as Generate Wall Systems
builds them (every layer a transformed / cut / derived variant); each
layer's `identity` diagnostics must name the same architecture by the same
ids on every layer it exists on, and topology changes must appear as
explicit EVENTS (construction_identity.diff) — never as a re-matching of
geometry. Identity only: geometry is the golden suite's business.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, '..', '..', 'design_proto'), os.path.join(HERE, '..', '..'),
          os.path.join(HERE, '..', '..', 'design_proto', 'tests'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('LAYER_ASSEMBLY_WORKERS', '0')

import construction_identity as CI                              # noqa: E402


def _layers(designs, wall_systems, assembly_state):
    """[(instance, identity report)] of every resolved layer, in order."""
    from layer_assembly import web
    from layer_assembly.source import variant_request, layer_geometry
    data = {'assembly': assembly_state, 'designs': designs, 'design_ids': [d['id'] for d in designs],
            'wall_systems': wall_systems or []}
    st = web._resolve_stack(data)
    src, res = st['src'], st['res']
    src.prefetch([variant_request(i) for i in res.instances if i.is_variant])
    out = []
    for inst in res.instances:
        g = layer_geometry(src, inst)
        out.append((inst, g.diagnostics.get('identity')))
    return out, src


def _ids(identity, kind):
    return sorted(a['id'] for a in identity['anchors'] if a['kind'] == kind)


def _segments(identity):
    return sorted(s for d in identity['domains'] for s in d['segments'])


def test_scaled_circle_stack_keeps_every_name_on_all_twenty_layers():
    """test_stack_coherence 'scaled_circle_skin_web': the circle scaled alone
    through 20 layers — anchors move, names do not; no event anywhere."""
    from layer_assembly.tests.stack_coherence import stack_scaled_circle_skin_web
    designs, ws, asm = stack_scaled_circle_skin_web()
    layers, src = _layers(designs, ws, asm)
    assert len(layers) == 20 and all(I for _, I in layers)
    first = layers[0][1]
    assert _ids(first, 'junction') == ['J1', 'J2'] and _ids(first, 'corner') == ['C:R#0', 'C:R#1', 'C:R#2', 'C:R#3']
    assert len(_segments(first)) == 5
    prev = None
    for inst, I in layers:
        assert _ids(I, 'junction') == ['J1', 'J2'], inst.index
        assert _segments(I) == _segments(first), inst.index
        assert [d['unresolved'] for d in I['domains']] == [0]
        assert [d['walls'] for d in I['domains']] == [['C', 'R']]
        if prev is not None:
            assert CI.diff(prev, I) == {}, (inst.index, CI.diff(prev, I))
        prev = I
    # the ids are the registry's (the same the Designer's junction inspector shows)
    assert {r['id'] for r in src.lib.registry()['records']} == {'J1', 'J2'}


def test_reference_network_stack_reports_its_topology_events_and_nothing_else():
    """The reference network (three transform groups, Line 1 a connector, the
    shrinking lens, the 'gaps' design above the middle): every junction is
    named by its persistent record on every layer it exists on; the lens
    junctions CEASE once and stay ceased; the opening's jambs are BORN at the
    design switch; no other anchor changes anywhere in the stack."""
    from layer_assembly.tests.stack_coherence import stack_reference_network_wave
    designs, ws, asm = stack_reference_network_wave()
    layers, src = _layers(designs, ws, asm)
    assert len(layers) == 16
    recs = {r['id']: r for r in src.lib.registry()['records']}
    assert set(recs) == {'J1', 'J2', 'J3', 'J4', 'J5', 'J6'}
    lens = {j for j, r in recs.items() if {m['wall'] for m in r['members']} == {'C1', 'R1'}}
    assert lens == {'J4', 'J5'}
    events, prev = [], None
    for inst, I in layers:
        assert all(a['record'] for a in I['anchors'] if a['kind'] == 'junction'), \
            (inst.index, 'every junction of the stack is a persistent record — none named by signature')
        assert all(d['unresolved'] == 0 for d in I['domains']), inst.index
        assert all(d['segments'] for d in I['domains']), (inst.index, 'segments on every layer (the scaffold\'s too)')
        if prev is not None:
            ev = CI.diff(prev, I)
            if ev:
                events.append((inst.index, inst.design_id, ev))
        prev = I
    kinds = {k for _, _, ev in events for k in ev}
    assert 'corner' not in kinds and 'end' not in kinds, events
    jamb = [(i, ev['jamb']) for i, _, ev in events if 'jamb' in ev]
    assert jamb == [(8, {'born': ['O:rect1_top:a', 'O:rect1_top:b'], 'ceased': []})], jamb
    junc = [(i, ev['junction']) for i, _, ev in events if 'junction' in ev]
    assert len(junc) == 1 and junc[0][1] == {'born': [], 'ceased': ['J4', 'J5']}, junc
    ceased_at = junc[0][0]
    for inst, I in layers:
        present = set(_ids(I, 'junction'))
        assert (lens <= present) == (inst.index < ceased_at), (inst.index, present)
        assert {'J1', 'J2', 'J3', 'J6'} <= present
    # segment events only where the architecture changes: the design switch
    # (the opening; the lens runs of the material close there, before the
    # source lines stop intersecting) and the junctions' end — never on an
    # unchanged layer pair
    seg = [i for i, _, ev in events if 'segment' in ev]
    assert seg and set(seg) <= {8, ceased_at}, seg


def test_real_project_names_its_three_junctions_and_the_thin_line_stays_out():
    """`10xtest1`: Skin + Web walls (no cavity: skins only) × a thin
    Out-and-Back line, planar top cuts removing walls above layer 48."""
    from layer_assembly.tests.stack_coherence import stack_real_10xtest1
    designs, ws, asm = stack_real_10xtest1()
    layers, src = _layers(designs, ws, asm)
    recs = src.lib.registry()['records']
    I0 = layers[0][1]
    assert _ids(I0, 'junction') == sorted(r['id'] for r in recs)
    thin = {w for a in I0['anchors'] if a['kind'] == 'junction' for w in a['thin']}
    assert thin, 'the project\'s thin Out-and-Back line meets the walls'
    assert all(a['excursion'] for a in I0['anchors'] if a['kind'] == 'junction' and a['thin'])
    for d in I0['domains']:
        assert not (set(d['walls']) & thin), 'a thin line is never a member of a construction domain'
    # the cuts remove walls near the top: their junctions cease — an event, not a re-match
    ceased = [(inst.index, ev['junction']['ceased']) for (inst, I), (_, P) in zip(layers[1:], layers)
              for ev in [CI.diff(P, I)] if 'junction' in ev and ev['junction']['ceased']]
    assert ceased and all(i >= 40 for i, _ in ceased), ceased
    assert all(not ev.get('corner') for (inst, I), (_, P) in zip(layers[1:], layers) for ev in [CI.diff(P, I)]
               if inst.index < 40)
