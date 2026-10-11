"""LIFT-AWARE PRINT PLANNING (lifts.py + print_plan.build_plan, 2026-10-09).

The machine has ~18 in of Z travel; after a lift the whole XY frame is
raised and printing never returns below it. Inside a lift independent
islands are built upward as far as their geometry allows, merged geometry
after all of its lower parts.

Every scenario is checked by an INDEPENDENT validity checker (`_valid`):
passes never go back to an earlier lift, every lift is whole layers within
the lift height, a frame move sits between lifts, every piece is printed
after every lower piece of the lift within bead width + head clearance of
it (support / no descending beside finished geometry — brute force over the
printed runs), and every layer's strands are printed exactly once.
"""
import copy
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.dirname(__file__))

from layer_assembly.lifts import LiftSettings, LiftError, lift_bounds, layer_components   # noqa: E402
from layer_assembly.print_plan import build_plan, estimate, PrintSpeeds, stream, items, PlanError  # noqa: E402
from layer_assembly.source import layer_geometry                                          # noqa: E402

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def rect(i, x, y=0.0, w=40.0, h=30.0):
    return {'id': i, 'type': 'RectanglePath', 'x': x, 'y': y, 'w': w, 'h': h, 'closed': True}


def line(i, a, b):
    return {'id': i, 'type': 'LinePath', 'start': list(a), 'end': list(b)}


def doc(paths, **kw):
    d = {'id': 'd', 'source_paths': list(paths), 'material': dict(MAT), 'wall_systems': []}
    d.update(kw)
    return d


def design(i, d, parent=None, patch=None):
    if parent:
        return {'id': i, 'name': i, 'parent': parent, 'patch': patch or {}, 'settings': {}}
    return {'id': i, 'name': i, 'parent': None, 'document': d}


def plan_of(designs, sections, lh=1.5, machine=None, **asm_kw):
    from layer_assembly.web import _resolve_stack
    asm = {'layer_height': lh, 'sections': sections, 'machine': machine or {}, **asm_kw}
    st = _resolve_stack({'assembly': asm, 'designs': designs})
    plan = build_plan(st['res'], st['src'], lift_settings=LiftSettings.from_dict(asm['machine']))
    return plan, st


R1, R2, R3 = rect('R1', 0), rect('R2', 100), rect('R3', 200)
BRIDGE12 = line('L12', (40, 15), (100, 15))      # touches R1's right edge and R2's left edge
BRIDGE23 = line('L23', (140, 15), (200, 15))


def _pts(P):
    out = []
    from layer_assembly.lifts import samples
    for r in P.runs:
        for s in r.segments:
            out.extend(samples(s.pts, 1.0))
    return out


def _segs(P):
    return [(a, b) for r in P.runs for s in r.segments for a, b in zip(s.pts, s.pts[1:])]


def _dist(P, Q):
    from layer_assembly.lifts import _seg_dist2
    S = _segs(Q)
    return min(math.sqrt(min(_seg_dist2(p, a, b) for a, b in S)) for p in _pts(P)) if S else math.inf


def _valid(plan, st, bead=3.0, clearance=1.0):
    s = LiftSettings.from_dict(plan.settings)
    lifts = [P.lift for P in plan.passes]
    assert lifts == sorted(lifts), 'returned to an earlier lift'
    for Lf in plan.lifts:
        assert Lf.height <= s.lift_height + 1e-9 and Lf.height <= s.max_z_travel + 1e-9
        assert all(plan.layers[i].lift == Lf.index for i in range(Lf.first_layer, Lf.last_layer + 1))
        assert all(Lf.first_layer <= plan.passes[i].layer <= Lf.last_layer for i in Lf.passes)
        if Lf.index:
            fm = Lf.reposition_in
            assert fm is not None and fm.frame_z_to == Lf.z_bottom and fm.rise > 0
            prev = plan.lifts[Lf.index - 1]
            assert max(Lf.passes and [min(Lf.passes)] or [0]) > max(prev.passes)
    by_lift = {}
    for P in plan.passes:
        by_lift.setdefault(P.lift, []).append(P)
    for ps in by_lift.values():
        for X in ps:
            for Y in ps:
                if Y.layer < X.layer and Y.runs and X.runs and _dist(X, Y) < bead + clearance - 0.6:
                    assert Y.index < X.index, f'pass {X.index} (layer {X.layer}) before lower {Y.index} (layer {Y.layer})'
    # every strand of every layer printed exactly once, by exactly one pass
    for inst, L in zip(st['res'].instances, plan.layers):
        g = layer_geometry(st['src'], inst)
        want = {p['id'] for p in g.polylines if len(p['pts']) >= 2}
        got = [s.strand for P in L.passes for r in P.runs for s in r.segments]
        assert set(got) == want
        owner = {}
        for P in L.passes:
            for r in P.runs:
                for sg in r.segments:
                    assert owner.setdefault(sg.strand, P.index) == P.index
    # the transitions: dz is the Z change from the previous pass, never below the lift
    prev = None
    for P in plan.passes:
        if prev is not None and P.transition_in is not None:
            assert abs(P.transition_in.dz - (P.z - prev.z)) < 1e-9
            assert P.transition_in.clear_z >= max(P.z, min(prev.z, P.z)) - 1e-9
        if P.runs:
            prev = P
    return True


def _names(plan, lift=0):
    return [plan.stages[i].name for i in plan.lifts[lift].stages]


def _blocks(plan):
    """[(stage name, first layer, last layer)] of consecutive passes."""
    out = []
    for P in plan.passes:
        n = plan.stages[P.stage].name
        if out and out[-1][0] == n and out[-1][2] == P.layer - 1:
            out[-1] = (n, out[-1][1], P.layer)
        else:
            out.append((n, P.layer, P.layer))
    return out


# --- lift boundaries ----------------------------------------------------------------------

def test_lift_boundaries_are_whole_layers():
    lay = lambda h, n: [(k * h, (k + 1) * h) for k in range(n)]     # noqa: E731
    assert lift_bounds(lay(1.5, 30), LiftSettings()) == [(0, 11), (12, 23), (24, 29)]
    # 1.4 in does not divide 18: 12 layers = 16.8 in per lift, the rest goes on
    b = lift_bounds(lay(1.4, 30), LiftSettings())
    assert b == [(0, 11), (12, 23), (24, 29)]
    assert lift_bounds(lay(1.0, 20), LiftSettings(18, 10)) == [(0, 9), (10, 19)]
    assert lift_bounds(lay(2.5, 9), LiftSettings(18, 18)) == [(0, 6), (7, 8)]      # 17.5 in
    with pytest.raises(LiftError):
        LiftSettings(18, 20)                                     # lift above the machine's Z travel
    with pytest.raises(LiftError):
        lift_bounds(lay(20, 2), LiftSettings())                  # one layer taller than a lift
    assert LiftSettings.from_dict({'max_z_travel': 12}).lift_height == 12


def test_machine_settings_are_validated_and_saved():
    from layer_assembly.model import Assembly, AssemblyError
    a = Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'machine': {'max_z_travel': 20, 'lift_height': 16},
                            'print_speeds': {'frame': 900}})
    assert a.machine == {'max_z_travel': 20.0, 'lift_height': 16.0} and a.print_speeds == {'frame': 900.0}
    assert Assembly.from_dict(a.to_dict()).machine == a.machine
    for bad in ({'lift_height': 19}, {'max_z_travel': 0}, {'head_clearance': -1}):
        with pytest.raises(AssemblyError):
            Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'machine': bad})
    with pytest.raises(AssemblyError):
        Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'print_speeds': {'frame': -1}})


# --- scenarios ------------------------------------------------------------------------------

def test_one_continuous_wall():
    plan, st = plan_of([design('A', doc([rect('R', 0, 0, 120, 80)]))], [{'design_id': 'A', 'height': 36}])
    assert _valid(plan, st)
    assert [(Lf.first_layer, Lf.last_layer) for Lf in plan.lifts] == [(0, 11), (12, 23)]
    assert [P.layer for P in plan.passes] == list(range(24))                 # plain bottom-up
    assert _names(plan, 0) == ['A'] and _names(plan, 1) == ['A']
    fm = plan.lifts[1].reposition_in
    assert fm.frame_z_from == 0 and fm.frame_z_to == 18 and fm.at == plan.passes[11].end
    for a, b in zip(plan.passes, plan.passes[1:]):
        assert b.transition_in.length < 1e-9                                  # straight above the last end


def test_two_disconnected_islands_are_built_one_after_the_other():
    plan, st = plan_of([design('A', doc([R1, R2]))], [{'design_id': 'A', 'height': 18}])
    assert _valid(plan, st)
    assert _blocks(plan) == [('A', 0, 11), ('B', 0, 11)]                       # never alternating
    downs = [P for P in plan.passes if P.transition_in and P.transition_in.dz < 0]
    assert len(downs) == 1 and downs[0].layer == 0
    assert downs[0].transition_in.clear_z >= 18 - 1e-9                       # over island A's top
    assert all(L.components == 2 and len(L.passes) == 2 for L in plan.layers)


def test_two_islands_merging_halfway():
    """Two rings to z 9 (6 layers), then joined by a bridge (derived design)."""
    designs = [design('two', doc([R1, R2])),
               design('joined', None, parent='two', patch={'source_paths': {'L12': BRIDGE12}})]
    plan, st = plan_of(designs, [{'design_id': 'two', 'height': 9}, {'design_id': 'joined', 'height': 9}])
    assert _valid(plan, st)
    assert [L.components for L in plan.layers] == [2] * 6 + [1] * 6
    assert _blocks(plan) == [('A', 0, 5), ('B', 0, 5), ('A+B', 6, 11)]
    merged = plan.stages[plan.passes[-1].stage]
    assert merged.kind == 'merge' and sorted(plan.stages[p].name for p in merged.parents) == ['A', 'B']
    first_merged = min(merged.passes)
    assert all(i < first_merged for s in merged.parents for i in plan.stages[s].passes)


def test_three_islands_merging_in_stages():
    designs = [design('three', doc([R1, R2, R3])),
               design('ab', None, parent='three', patch={'source_paths': {'L12': BRIDGE12}}),
               design('abc', None, parent='ab', patch={'source_paths': {'L23': BRIDGE23}})]
    plan, st = plan_of(designs, [{'design_id': 'three', 'height': 6}, {'design_id': 'ab', 'height': 6},
                                 {'design_id': 'abc', 'height': 6}])
    assert _valid(plan, st)
    assert [L.components for L in plan.layers] == [3] * 4 + [2] * 4 + [1] * 4
    names = [n for n, _, _ in _blocks(plan)]
    kinds = {plan.stages[i].name: plan.stages[i].kind for i in plan.lifts[0].stages}
    assert sorted(kinds.values()) == ['island', 'island', 'island', 'merge', 'merge']
    assert names[-1].count('+') == 2 and len(names) == 5                    # 5 blocks: nothing interleaved
    top = plan.stages[plan.passes[-1].stage]
    assert top.layers == list(range(8, 12))


def test_island_ending_below_the_lift_boundary():
    designs = [design('two', doc([R1, R2])), design('one', doc([R1]))]
    plan, st = plan_of(designs, [{'design_id': 'two', 'height': 6}, {'design_id': 'one', 'height': 12}])
    assert _valid(plan, st)
    assert _blocks(plan) == [('A', 0, 11), ('B', 0, 3)]
    assert plan.stages[plan.passes[-1].stage].layers == [0, 1, 2, 3]


def test_layer_and_lift_heights():
    for lh, machine, lifts in ((1.4, {}, [(0, 11), (12, 23), (24, 25)]),
                               (1.0, {'lift_height': 10}, [(0, 9), (10, 19), (20, 25), ]),
                               (2.0, {'max_z_travel': 12, 'lift_height': 12}, [(0, 5), (6, 11), (12, 17)])):
        height = {1.4: 36.4, 1.0: 26, 2.0: 36}[lh]
        plan, st = plan_of([design('A', doc([R1, R2]))], [{'design_id': 'A', 'height': height}], lh=lh, machine=machine)
        assert _valid(plan, st)
        assert [(Lf.first_layer, Lf.last_layer) for Lf in plan.lifts] == lifts, (lh, machine)
        # per lift: island A up, then island B
        for Lf in plan.lifts:
            ps = [plan.passes[i] for i in Lf.passes]
            assert [P.layer for P in ps] == list(range(Lf.first_layer, Lf.last_layer + 1)) * 2
        assert sum(1 for Lf in plan.lifts if Lf.reposition_in) == len(lifts) - 1


def test_openings_change_connectivity():
    """The bridge has a Designer opening below z 9 (derived design): two
    islands below, one above."""
    op = {'id': 'o1', 'source_path_id': 'L12', 'center_s': 30.0, 'width': 20.0}
    designs = [design('joined', doc([R1, R2, BRIDGE12])),
               design('gap', None, parent='joined', patch={'openings': {'o1': op}})]
    plan, st = plan_of(designs, [{'design_id': 'gap', 'height': 9}, {'design_id': 'joined', 'height': 9}])
    assert _valid(plan, st)
    assert [L.components for L in plan.layers] == [2] * 6 + [1] * 6
    assert [n for n, _, _ in _blocks(plan)] == ['A', 'B', 'A+B']


def test_planar_cut_changes_connectivity():
    """A level planar top cut removes the bridge above z 9: one piece below,
    two islands (a split) above."""
    plane = {'kind': 'plane', 'id': 'p1', 'sources': ['L12'], 'systems': [], 'exclude': [],
             'direction': 0.0, 'slope': 0.0, 'anchor': [70, 15], 'z_anchor': 9.0}
    plan, st = plan_of([design('joined', doc([R1, R2, BRIDGE12]))], [{'design_id': 'joined', 'height': 18}],
                       profiles=[plane])
    assert _valid(plan, st)
    comps = [L.components for L in plan.layers]
    assert comps[0] == 1 and comps[-1] == 2, comps
    k = comps.index(2)
    kinds = [plan.stages[i].kind for i in plan.lifts[0].stages]
    assert kinds == ['island', 'split', 'split']
    blocks = _blocks(plan)
    assert blocks[0] == ('A', 0, k - 1) and {b[0] for b in blocks[1:]} == {'A.1', 'A.2'} and len(blocks) == 3


def test_connected_wall_systems_are_one_island():
    a = rect('Ra', 0, 0, 60, 40)
    b = rect('Rb', 50, 0, 60, 40)                                  # overlaps Ra
    ws = [{'id': 'W1', 'name': 'W1', 'type': 'interleaved', 'members': ['Ra'], 'thickness': 10, 'align': 'auto',
           'params': {'paths': 4}, 'web': {}},
          {'id': 'W2', 'name': 'W2', 'type': 'chain', 'members': ['Rb'], 'thickness': 10, 'align': 'auto',
           'params': {}, 'web': {}}]
    plan, st = plan_of([design('A', doc([a, b], wall_systems=ws))], [{'design_id': 'A', 'height': 6}])
    assert _valid(plan, st)
    assert all(L.components == 1 for L in plan.layers)
    systems = {s for L in plan.layers for r in L.runs for s in r.systems}
    assert systems == {'W1', 'W2'}
    assert _names(plan) == ['A'] and [P.layer for P in plan.passes] == [0, 1, 2, 3]


def test_assembly_transforms_islands_grow_into_one():
    """Each ring grows about its own centre (transform groups, scale out) —
    their 60 in gap closes: the plan sees the TRANSFORMED geometry merge."""
    sec = {'design_id': 'A', 'height': 18, 'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': -3.0}}
    plan, st = plan_of([design('A', doc([R1, R2]))], [sec], center_mode='multiple', center_counter=2,
                       centers=[{'id': 'c1', 'x': 20, 'y': 15, 'sources': ['R1']},
                                {'id': 'c2', 'x': 120, 'y': 15, 'sources': ['R2']}])
    assert _valid(plan, st)
    comps = [L.components for L in plan.layers]
    assert comps[0] == 2 and comps[-1] == 1, comps
    names = [n for n, _, _ in _blocks(plan)]
    assert names == ['A', 'B', 'A+B']
    # a shift moves everything together: still two islands, placed in the world
    sec2 = {'design_id': 'A', 'height': 9, 'transform': {'profile': 'linear', 'shift': [2, 1], 'scale_in': 0}}
    plan2, st2 = plan_of([design('A', doc([R1, R2]))], [sec2])
    assert _valid(plan2, st2) and [n for n, _, _ in _blocks(plan2)] == ['A', 'B']
    top = plan2.passes[5]
    assert min(q[0] for r in top.runs for s in r.segments for q in s.pts) > 9.9      # (5 layers × 2 in)


def test_never_back_to_a_previous_lift_and_frame_events():
    designs = [design('two', doc([R1, R2])),
               design('joined', None, parent='two', patch={'source_paths': {'L12': BRIDGE12}})]
    plan, st = plan_of(designs, [{'design_id': 'two', 'height': 24}, {'design_id': 'joined', 'height': 12}])
    assert _valid(plan, st)
    assert len(plan.lifts) == 2 and plan.lifts[1].first_layer == 12
    # lift 1: still two islands up to z 24 then merged — again A, B, A+B in lift 1 (new letters)
    assert _names(plan, 0) == ['A', 'B'] and _names(plan, 1) == ['A', 'B', 'A+B']
    kinds = [it[0] for it in items(plan)]
    assert kinds.count('frame') == 1
    i = kinds.index('frame')
    after = [it for it in list(items(plan))[i:] if it[0] != 'frame']
    assert all(it[1] >= 12 for it in after)                                   # nothing below the frame again


def test_merge_is_never_scheduled_before_its_parts():
    """However the islands are numbered, the merged piece waits for both
    columns (validity checker) — also with the bridge starting at layer 1."""
    designs = [design('two', doc([R1, R2])),
               design('joined', None, parent='two', patch={'source_paths': {'L12': BRIDGE12}})]
    for h in (1.5, 3, 15):
        plan, st = plan_of(designs, [{'design_id': 'two', 'height': h}, {'design_id': 'joined', 'height': 18 - h}])
        assert _valid(plan, st)
        n = int(round(h / 1.5))
        assert _blocks(plan) == [('A', 0, n - 1), ('B', 0, n - 1), ('A+B', n, 11)]


def test_deterministic():
    designs = [design('three', doc([R1, R2, R3])),
               design('ab', None, parent='three', patch={'source_paths': {'L12': BRIDGE12}})]
    secs = [{'design_id': 'three', 'height': 9}, {'design_id': 'ab', 'height': 15}]
    p1, _ = plan_of(designs, secs)
    p2, st2 = plan_of(copy.deepcopy(designs), copy.deepcopy(secs))
    st2['src']._prepared.clear()
    p3 = build_plan(st2['res'], st2['src'])
    assert json.dumps(p1.to_dict('full')) == json.dumps(p2.to_dict('full')) == json.dumps(p3.to_dict('full'))


def test_manual_start_and_route_origin_and_reverse():
    d = doc([R1, R2, R3])
    d['constraints'] = {'start_path_id': 'R3', 'start_t': 0.0, 'reverse_direction': False, 'component_order': None}
    plan, st = plan_of([design('A', d)], [{'design_id': 'A', 'height': 6}])
    assert _valid(plan, st)
    first = plan.passes[0]
    assert first.manual_start and abs(first.start[0] - 200) < 1e-6 and abs(first.start[1]) < 1e-6
    # every pass of R3's island starts at the manual point; the other islands are automatic
    r3 = [P for P in plan.passes if P.stage == first.stage]
    assert len(r3) == 4 and all(P.manual_start and math.dist(P.start, (200, 0)) < 1e-6 for P in r3)
    assert not any(P.manual_start for P in plan.passes if P.stage != first.stage)
    # reverse: every pass
    d2 = doc([R1, R2])
    d2['constraints'] = {'start_path_id': None, 'start_t': None, 'reverse_direction': True, 'component_order': None}
    plan2, st2 = plan_of([design('A', d2)], [{'design_id': 'A', 'height': 3}])
    assert all(P.reversed for P in plan2.passes)
    # a Route Origin on R2's strand: R2's passes keep that seam; R1 unaffected (and not reported missing)
    sid = layer_geometry(st2['src'], st2['res'].instances[0]).polylines[1]['id']
    d3 = doc([R1, R2], route_origins=[{'strand': sid, 'u': 0.37}])
    plan3, st3 = plan_of([design('A', d3)], [{'design_id': 'A', 'height': 6}])
    seam = [P.start for P in plan3.passes if any(s.strand == sid for r in P.runs for s in r.segments)]
    assert len(seam) == 4 and all(math.dist(s, seam[0]) < 1e-6 for s in seam)
    assert all(o['status'] != 'missing' for L in plan3.layers for o in L.origins)


def test_timing_and_frame_reposition_events():
    designs = [design('two', doc([R1, R2])),
               design('joined', None, parent='two', patch={'source_paths': {'L12': BRIDGE12}})]
    plan, st = plan_of(designs, [{'design_id': 'two', 'height': 24}, {'design_id': 'joined', 'height': 12}])
    sp = PrintSpeeds(2.0, 6.0, 1200.0)
    e = estimate(plan, sp)
    assert e['frame_moves'] == 1 and e['frame_time'] == 1200.0
    assert abs(e['total_time'] - (e['print_time'] + e['travel_time'] + e['frame_time'])) < 1e-9
    assert abs(sum(L['total_time'] for L in e['lifts']) - e['total_time']) < 1e-6
    assert e['lifts'][0]['frame_time'] == 0 and e['lifts'][1]['frame_time'] == 1200.0
    assert abs(sum(s['total_time'] for s in e['stages']) - (e['print_time'] + e['travel_time'])) < 1e-6
    assert abs(sum(L['total_time'] for L in e['layers']) - (e['print_time'] + e['travel_time'])) < 1e-6
    S = stream(plan, sp)
    I = S['items']
    f = [k for k, kd in enumerate(I['kind']) if kd == 4]
    assert len(f) == 1 and I['dt'][f[0]] == 1200.0 and I['lift'][f[0] + 1] == 1
    assert abs(I['t0'][-1] + I['dt'][-1] - e['total_time']) < 1e-4
    # without an estimate the frame move is still an event, of zero duration
    e0 = estimate(plan, PrintSpeeds(2.0, 6.0))
    assert e0['frame_moves'] == 1 and e0['frame_time'] == 0
    assert abs(e0['total_time'] - (e['total_time'] - 1200)) < 1e-6


def test_components_from_printed_geometry():
    """Touching beads are one piece (no shared vertex needed); a gap of more
    than one bead width separates; Wall System membership plays no role."""
    sq = lambda x: {'pts': [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]], 'closed': True}   # noqa: E731
    assert layer_components([sq(0), sq(12)], 3.0) == [[0, 1]]          # 2 in apart < 3 in bead
    assert layer_components([sq(0), sq(14)], 3.0) == [[0], [1]]        # 4 in apart
    assert layer_components([sq(0), sq(14), sq(5)], 3.0) == [[0, 1, 2]]
