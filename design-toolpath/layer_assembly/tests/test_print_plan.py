"""PRINT PLAN (print_plan.py): the whole print ordered + time estimates.

- the final Assembly layers print EXACTLY what is displayed / checked: every
  strand of each layer's resolved geometry (derived designs, several Wall
  Systems, transform groups, a self-supporting opening, a planar top cut)
  printed once, nothing else, placed by the layer's own transform;
- the per-layer route IS the Designer's route of that geometry;
- cross-layer routing: closed layers start right above the previous end,
  transitions never longer than without planning, deterministic;
- manual overrides win: Route Origins, a design start, reverse;
- timing: units, per run / layer / section / total, timeline;
- the endpoint and the Assembly's print speeds.
"""
import copy
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests')))

from layer_assembly.model import Assembly, Section, resolve, Footprint, AssemblyError   # noqa: E402
from layer_assembly.print_plan import (build_plan, estimate, timeline, PrintSpeeds, speed_from, speed_to,  # noqa: E402
                                       PlanError, Run, Travel)
from layer_assembly.source import StaticSource, layer_geometry, bbox_of               # noqa: E402

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def _doc(paths, systems=(), **kw):
    d = {'id': 'd', 'source_paths': list(paths), 'material': dict(MAT), 'wall_systems': list(systems)}
    d.update(kw)
    return d


def _ws(i, t, members):
    return {'id': i, 'name': i, 'type': t, 'members': members, 'thickness': 10, 'align': 'auto',
            'params': {'paths': 4} if t in ('interleaved', 'linked') else {}}


RING = {'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 120, 'h': 80, 'closed': True}


def _source(designs):
    from layer_assembly.designer_source import DesignerSource
    return DesignerSource.from_designs(designs)


def _stack(src, a, ids):
    fps = {d: Footprint.of_bbox(*bbox_of(src.geometry(d).polylines)) for d in ids}
    return resolve(a, list(ids), fps)


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])


# --- the final layers print exactly what is displayed -------------------------------------

def _combined():
    import reference_network as RN
    base = RN.document('wave')
    base['infills'] = []
    base['material'] = dict(MAT)
    base['wall_systems'] = [_ws('H', 'interleaved', ['R1', 'R2', 'C1']), _ws('B', 'chain', ['L1'])]
    cut = {'rect1_top': dict(zip(('source_path_id', 'center_s', 'width'), RN.OPENINGS['rect1_top']))}
    designs = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': base},
               {'id': 'gaps', 'name': 'gaps', 'parent': 'base', 'patch': {'openings': cut}, 'settings': {}}]
    asm = {'layer_height': 1.5, 'max_overhang': 1.5,
           'sections': [{'design_id': 'base', 'height': 9}, {'design_id': 'gaps', 'height': 9}],
           'transform': {'profile': 'linear', 'shift': [0.1, 0], 'scale_in': 0.2},
           'center_mode': 'multiple', 'center_counter': 2,
           'centers': [{'id': 'c1', 'x': RN.CX, 'y': RN.CY, 'sources': ['C1']},
                       {'id': 'c2', 'x': 200, 'y': 170, 'sources': ['R1']}],
           'profiles': [{'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R2', 'edge': 3}, 'position': 50,
                         'z_bottom': 3, 'height': 9, 'width': 12, 'shape': 'diamond'},
                        {'kind': 'plane', 'id': 'p2', 'sources': ['R1'], 'systems': [], 'exclude': [],
                         'direction': 0.0, 'slope': 0.25, 'anchor': [100, 170], 'z_anchor': 12.0}]}
    return designs, asm


def _stack_web(designs, asm):
    from layer_assembly.web import _resolve_stack
    return _resolve_stack({'assembly': asm, 'designs': designs})


def test_final_layers_print_exactly_the_displayed_geometry():
    st = _stack_web(*_combined())
    res, src = st['res'], st['src']
    assert any(i.cuts for i in res.instances) and any(i.transforms() for i in res.instances)
    assert {i.design_id for i in res.instances} == {'base', 'gaps'}
    plan = build_plan(res, src)
    assert [L.index for L in plan.layers] == [i.index for i in res.instances]
    for inst, L in zip(res.instances, plan.layers):
        g = layer_geometry(src, inst)
        t = inst.transform
        want = {}
        for p in g.polylines:
            pts = p['pts'] + ([p['pts'][0]] if p['closed'] else [])
            want[p['id']] = t.scale * sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        got = {}
        for r in L.runs:
            for s in r.segments:
                assert s.kind == 'print'                                  # no retrace anywhere
                got[s.strand] = got.get(s.strand, 0.0) + s.length
        assert set(got) == set(want), (L.index, set(want) ^ set(got))
        for k in want:
            assert abs(got[k] - want[k]) <= 1e-6 * max(1.0, want[k]), (L.index, k)
        # placed: every printed vertex lies on its strand placed by the layer's OWN transform
        for r in L.runs[:2]:
            for sg in r.segments[:5]:
                pts = next(p['pts'] + ([p['pts'][0]] if p['closed'] else []) for p in g.polylines if p['id'] == sg.strand)
                W = [(t.scale * q[0] + t.tx, t.scale * q[1] + t.ty) for q in pts]
                for v in sg.pts:
                    assert min(_seg_dist(v, a, b) for a, b in zip(W, W[1:])) < 1e-6
        assert L.z_top == inst.z_top and L.placement == {'scale': t.scale, 'tx': t.tx, 'ty': t.ty}


def test_layer_route_is_the_designers_route():
    """Layer 0 (nothing below: no start) of a ONE-PIECE layer = the Designer
    view's /api/route of the same design (separate islands are routed piece
    by piece: test_lifts.py)."""
    import app as A
    d = _doc([RING, {'id': 'C', 'type': 'CirclePath', 'cx': 140, 'cy': 40, 'radius': 40, 'closed': True}],
             [_ws('W', 'interleaved', ['R', 'C'])])
    src = _source([{'id': 'A', 'name': 'A', 'document': d}])
    plan = build_plan(_stack(src, Assembly(1.5, [Section('A', 3)]), ['A']), src)
    A.app.config['TESTING'] = True
    r = A.app.test_client().post('/api/route', json=d).get_json()
    designer = [(m['kind'], tuple(m['start']), tuple(m['end'])) for m in r['moves']]
    ours = [(k, a[:2], b[:2]) for (li, ri, k, a, b, *_) in plan.moves() if li == 0]
    assert len(ours) == len(designer)
    for (k1, a1, b1), (k2, a2, b2) in zip(ours, designer):
        assert k1 == k2 and math.dist(a1, a2) < 1e-6 and math.dist(b1, b2) < 1e-6


# --- cross-layer routing ------------------------------------------------------------------------

def test_closed_layers_start_right_above_the_previous_end():
    src = _source([{'id': 'A', 'name': 'A', 'document': _doc([RING], [_ws('W', 'interleaved', ['R'])])}])
    plan = build_plan(_stack(src, Assembly(1.5, [Section('A', 12)]), ['A']), src)
    assert len(plan.layers) == 8
    for prev, L in zip(plan.layers, plan.layers[1:]):
        assert len(L.runs) == 1 and L.runs[0].closed
        assert L.transition_in.length < 1e-9 and abs(L.transition_in.dz - 1.5) < 1e-9
        assert L.start == prev.end


def _islands():
    """Three separate rings (single beads) — the order and starts matter."""
    paths = [{'id': f'Q{k}', 'type': 'RectanglePath', 'x': x, 'y': 0, 'w': 40, 'h': 30, 'closed': True}
             for k, x in enumerate((0, 100, 220))]
    return _doc(paths)


def test_transitions_follow_the_previous_layer():
    """Each layer starts where the nozzle is (no longer transition than routing
    every layer on its own) and the whole print's travel is lower."""
    src = _source([{'id': 'A', 'name': 'A', 'document': _islands()}])
    res = _stack(src, Assembly(1.5, [Section('A', 9, '', __import__('layer_assembly.model', fromlist=['x']).SectionTransform('linear', (2.0, 1.0), 0.0))]), ['A'])
    plan = build_plan(res, src)
    naive_trans = 0.0
    prev = None
    for inst in res.instances:                      # every layer routed alone (its own default start)
        moves, _, _ = src.route(layer_geometry(src, inst), None)
        t = inst.transform
        s = (t.scale * moves[0].start.x + t.tx, t.scale * moves[0].start.y + t.ty)
        e = (t.scale * moves[-1].end.x + t.tx, t.scale * moves[-1].end.y + t.ty)
        if prev is not None:
            naive_trans += math.dist(prev, s)
        prev = e
    planned = sum(L.transition_in.length for L in plan.layers if L.transition_in)
    assert planned <= naive_trans + 1e-6
    for L in plan.layers[1:]:
        assert L.transition_in.length <= 2.5 + 1e-6               # the per-layer shift, nothing more


def test_plan_is_deterministic():
    designs, asm = _combined()
    st1, st2 = _stack_web(designs, asm), _stack_web(copy.deepcopy(designs), copy.deepcopy(asm))
    p1 = build_plan(st1['res'], st1['src']).to_dict('full')
    st2['src']._prepared.clear()
    p2 = build_plan(st2['res'], st2['src']).to_dict('full')
    assert json.dumps(p1) == json.dumps(p2)


# --- manual overrides win ---------------------------------------------------------------------

def test_route_origin_is_kept_on_every_layer():
    d = _doc([RING], [_ws('W', 'parallel', ['R'])])
    src0 = _source([{'id': 'A', 'name': 'A', 'document': d}])
    sid = src0.geometry('A').polylines[0]['id']
    d['route_origins'] = [{'strand': sid, 'u': 0.37}]
    src = _source([{'id': 'A', 'name': 'A', 'document': d}])
    tf = __import__('layer_assembly.model', fromlist=['x']).SectionTransform('linear', (3.0, 0.0), 0.0)
    plan = build_plan(_stack(src, Assembly(1.5, [Section('A', 6, '', tf)]), ['A']), src)
    starts = []
    for L in plan.layers:
        t = L.placement
        starts.append(((L.start[0] - t['tx']) / t['scale'], (L.start[1] - t['ty']) / t['scale']))
        assert L.origins and L.origins[0]['status'] != 'missing'
    assert all(math.dist(s, starts[0]) < 1e-6 for s in starts)    # the same seam in design coordinates
    assert all(L.transition_in.length > 2.9 for L in plan.layers[1:])   # (not moved to the nozzle)


def test_design_start_and_reverse_win():
    d = _islands()
    d['constraints'] = {'start_path_id': 'Q2', 'start_t': 0.0, 'reverse_direction': False, 'component_order': None}
    src = _source([{'id': 'A', 'name': 'A', 'document': d}])
    plan = build_plan(_stack(src, Assembly(1.5, [Section('A', 6)]), ['A']), src)
    for L in plan.layers:
        assert L.manual_start and abs(L.start[0] - 220) < 1e-6 and abs(L.start[1]) < 1e-6
    d2 = _islands()
    d2['constraints'] = {'start_path_id': None, 'start_t': None, 'reverse_direction': True, 'component_order': None}
    src2 = _source([{'id': 'A', 'name': 'A', 'document': d2}])
    plan2 = build_plan(_stack(src2, Assembly(1.5, [Section('A', 3)]), ['A']), src2)
    assert all(L.reversed for L in plan2.layers)


# --- timing ------------------------------------------------------------------------------------

def test_speed_units():
    assert speed_from(2, 'in/s') == 2 and speed_from(120, 'in/min') == 2
    assert speed_from(0.5, 's/in') == 2 and abs(speed_from(10, 'ft/min') - 2) < 1e-12
    assert abs(speed_from(50.8, 'mm/s') - 2) < 1e-12
    assert speed_to(2, 's/in') == 0.5 and speed_to(2, 'in/min') == 120
    for bad in (0, -1, float('nan')):
        with pytest.raises(PlanError):
            speed_from(bad, 'in/s')
    with pytest.raises(PlanError):
        speed_from(1, 'furlongs')
    with pytest.raises(PlanError):
        PrintSpeeds(0, 5)


def test_estimate_from_ordered_lengths():
    src = _source([{'id': 'A', 'name': 'A', 'document': _islands()},
                   {'id': 'B', 'name': 'B', 'document': _doc([RING])}])
    res = _stack(src, Assembly(1.5, [Section('A', 4.5), Section('B', 3)]), ['A', 'B'])
    plan = build_plan(res, src)
    sp = PrintSpeeds(2.0, 8.0)
    e = estimate(plan, sp)
    plen = sum(r.print_length for L in plan.layers for r in L.runs)
    tlen = sum(t.length for L in plan.layers for t in L.travels) + \
        sum(t.length for L in plan.layers for t in L.transitions)
    assert e['estimate'] is True and 'not machine timing' in e['assumptions'].lower()
    assert abs(e['print_time'] - plen / 2.0) < 1e-9 and abs(e['travel_time'] - tlen / 8.0) < 1e-9
    assert abs(e['total_time'] - e['print_time'] - e['travel_time']) < 1e-9
    assert [s['layers'] for s in e['sections']] == [3, 2]
    assert abs(sum(s['total_time'] for s in e['sections']) - e['total_time']) < 1e-6
    assert abs(sum(L['total_time'] for L in e['layers']) - e['total_time']) < 1e-6
    for L, el in zip(plan.layers, e['layers']):
        assert [round(r['time'], 9) for r in el['runs']] == [round((r.print_length + r.retrace_length) / 2.0, 9)
                                                              for r in L.runs]
    # the timeline the simulation plays: monotone, ends at the total
    tl = list(timeline(plan, sp))
    assert all(b[0] >= a[1] - 1e-12 for a, b in zip(tl, tl[1:]))
    assert abs(tl[-1][1] - e['total_time']) < 1e-6
    # twice as fast printing halves only the printing time
    e2 = estimate(plan, PrintSpeeds(4.0, 8.0))
    assert abs(e2['print_time'] - e['print_time'] / 2) < 1e-9 and e2['travel_time'] == e['travel_time']


def test_static_source_fallback_routes_too():
    from layer_assembly.source import DesignInfo, LayerGeometry
    polys = [{'id': 's1', 'pts': [[0, 0], [10, 0], [10, 10], [0, 10]], 'closed': True},
             {'id': 's2', 'pts': [[30, 0], [40, 0]], 'closed': False}]
    src = StaticSource({'A': (DesignInfo('A', 'A', None), LayerGeometry('A', polys, 3.0))})
    res = resolve(Assembly(1.0, [Section('A', 3)]), ['A'], {'A': Footprint.of_bbox(0, 0, 40, 10)})
    plan = build_plan(res, src)
    assert len(plan.layers) == 3 and all(len(L.runs) == 2 for L in plan.layers)
    moves = list(plan.moves())
    assert moves[0][2] == 'print' and any(m[2] == 'transition' for m in moves)


# --- the Assembly's speeds + the endpoint ------------------------------------------------------

def test_print_speeds_round_trip_and_validation():
    a = Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'print_speeds': {'print': 1.5, 'travel': 5}})
    assert a.print_speeds == {'print': 1.5, 'travel': 5.0}
    assert Assembly.from_dict(a.to_dict()).print_speeds == a.print_speeds
    assert Assembly.from_dict({'layer_height': 1.5, 'sections': []}).print_speeds == {}
    with pytest.raises(AssemblyError):
        Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'print_speeds': {'print': 0}})


def test_print_plan_endpoint():
    import app as A
    A.app.config['TESTING'] = True
    c = A.app.test_client()
    designs = [{'id': 'A', 'name': 'A', 'parent': None, 'document': _islands()}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 6}], 'print_speeds': {'print': 2, 'travel': 4}}
    r = c.post('/api/assembly/print_plan', json={'assembly': asm, 'designs': designs}).get_json()
    assert not r.get('error')
    assert r['estimate']['speeds'] == {'print': 2.0, 'travel': 4.0, 'frame': 0.0}
    assert len(r['plan']['layers']) == 4 and 'steps' not in r['plan']['layers'][0]
    r2 = c.post('/api/assembly/print_plan', json={'assembly': asm, 'designs': designs, 'detail': [1],
                                                  'speeds': {'print': 1, 'travel': 1}}).get_json()
    assert 'steps' in r2['plan']['layers'][1] and 'steps' not in r2['plan']['layers'][0]
    seg = next(s for s in r2['plan']['layers'][1]['steps'] if s['type'] == 'run')['segments'][0]
    assert seg['source'] in ('Q0', 'Q1', 'Q2') and seg['kind'] == 'print'
    assert r2['estimate']['total_time'] > r['estimate']['total_time']
    bad = c.post('/api/assembly/print_plan', json={'assembly': asm, 'designs': designs, 'speeds': {'print': -1}})
    assert bad.status_code == 400
    assert c.post('/api/assembly/print_plan', json={'assembly': asm}).status_code == 400


# --- the STREAM (Print Simulation) ---------------------------------------------------------

def _stream_req(extra=None):
    designs = [{'id': 'A', 'name': 'A', 'parent': None, 'document': _islands()}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 4.5}, {'design_id': 'A', 'height': 3}],
           'print_speeds': {'print': 2, 'travel': 5},
           'objects': [{'kind': 'header', 'id': 'h1', 'section': 1, 'layer': 0, 'x': 70, 'y': 15, 'angle': 0,
                        'span': 20, 'bearing': 8, 'depth': 10, 'thickness': 6, 'snap': True}]}
    return dict({'assembly': asm, 'designs': designs}, **(extra or {}))


def test_stream_is_the_plan_in_print_order_with_the_planners_clock():
    import app as A
    from layer_assembly.print_plan import items, duration, KIND_CODE
    from layer_assembly import web as W
    A.app.config['TESTING'] = True
    c = A.app.test_client()
    r = c.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream'})).get_json()
    assert not r.get('error')
    S, I = r['stream'], r['stream']['items']
    plan, _ = W._planned(_stream_req())                             # (the cached plan of that request)
    its = list(items(plan))
    assert len(I['kind']) == len(its)
    sp = PrintSpeeds(2, 5)
    t = 0.0
    for k, it in enumerate(its):
        kind, li, ri, pts = it[0], it[1], it[2], it[3]
        assert I['kind'][k] == KIND_CODE[kind] and I['layer'][k] == li and I['run'][k] == ri
        o, n = I['off'][k], I['n'][k]
        assert n == len(pts) and all(abs(S['xy'][2 * (o + j)] - pts[j][0]) < 1e-4 for j in range(n))
        assert abs(I['t0'][k] - t) < 1e-5
        L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        assert abs(I['dt'][k] - duration(kind, L, sp)) < 1e-5
        t += duration(kind, L, sp)
    order = [(P.index, P.layer) for P in plan.passes]               # the SCHEDULE (islands, lifts.py)
    assert [I['pass'][k] for k in range(len(its))] == sorted(I['pass']) and set(I['pass']) == {i for i, _ in order}
    assert all(plan.passes[I['pass'][k]].layer == I['layer'][k] for k in range(len(its)))
    assert I['lift'] == sorted(I['lift'])                           # never back to an earlier lift
    assert abs(t - r['estimate']['total_time']) < 1e-4               # the same clock as the estimate
    assert {KIND_CODE['transition'], KIND_CODE['print']} <= set(I['kind'])
    # separate islands: built upward one after another — back DOWN to the next island (same lift)
    downs = [k for k in range(len(its)) if I['kind'][k] == KIND_CODE['transition'] and I['z1'][k] < I['z0'][k]]
    assert downs and all(I['zc'][k] >= I['z0'][k] for k in downs)
    # identities, sections, labels, headers
    srcs = {S['strings'][i] for i in I['source'] if i >= 0}
    assert srcs == {'Q0', 'Q1', 'Q2'} and r['labels'].get('Q0')
    assert [s['layers'] for s in r['sections']] == [3, 2]
    (h,) = r['headers']
    assert h['layer'] == 3 and len(h['corners']) == 4 and h['z_top'] == r['plan']['layers'][3]['z_bottom']


def test_times_only_retimes_the_cached_plan(monkeypatch):
    import app as A
    from layer_assembly import web as W
    from layer_assembly import print_plan as PP
    A.app.config['TESTING'] = True
    c = A.app.test_client()
    W._PLANS.clear()
    calls = []
    real = PP.build_plan
    monkeypatch.setattr(PP, 'build_plan', lambda *a, **k: calls.append(1) or real(*a, **k))
    s1 = c.post('/api/assembly/print_plan', json=_stream_req({'detail': 'stream'})).get_json()
    req = _stream_req({'detail': 'times', 'speeds': {'print': 4, 'travel': 10}})
    t2 = c.post('/api/assembly/print_plan', json=req).get_json()
    assert len(calls) == 1                                            # re-timed, not re-planned
    assert len(t2['t0']) == len(s1['stream']['items']['t0'])
    assert abs(t2['estimate']['total_time'] - s1['estimate']['total_time'] / 2) < 1e-6
    assert all(abs(a - b / 2) < 1e-5 for a, b in zip(t2['dt'], s1['stream']['items']['dt']))
    # a geometry change (layer height) plans again
    req2 = _stream_req({'detail': 'stream'})
    req2['assembly']['layer_height'] = 1.0
    c.post('/api/assembly/print_plan', json=req2)
    assert len(calls) == 2
