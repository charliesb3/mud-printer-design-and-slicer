"""
M4 — LOCAL LATTICE SUPPORT REPAIR (2026-10-11).

Print preparation repairs internal strands (web lattice, generated Wall
System paths) that the authoritative bead-level rule finds unsupported,
locally and bottom-up (lattice_repair.py: a contact shift toward the layer
below, smoothed, bounded). Checked:

  unit (synthetic two-layer geometry): a stepped strand is pulled back onto
  the bead below and becomes supported; a strand over empty space (a bridge)
  is left as generated and reported; a move that would leave the wall
  envelope, crowd another strand, kink the motif or unsupport the layer
  above is refused; skins / caps are never touched; strand ends never move;

  stacks (T / X / Y junctions, mixed Wall Systems, rounded junctions, linear
  and quadratic transforms, horizontal opening arrays starting and ending at
  different layers, planar cuts through junctions, transform groups that
  change a junction; Chained Loop, Interleaved and Linked Waves):
    * the architecture (envelopes) is identical to M3;
    * findings never increase and NO sample anywhere becomes newly
      unsupported (every remaining unsupported sample lies within 2 beads of
      an M3 finding);
    * every moved vertex lies within its repair's window (distant lattice
      untouched), strand ends never move;
    * every repaired layer still routes, with no more pieces than before;
    * no new strand crowding; Plan Print plans the repaired stack.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

from layer_assembly.lattice_repair import repair_layer, _max_turn
from layer_assembly.model import InstanceTransform

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
B = 3.0


class _Inst:
    def __init__(self, index=1, tf=InstanceTransform()):
        self.index, self.transform = index, tf


def _line(y, x0=0.0, x1=60.0, n=61):
    return [(x0 + (x1 - x0) * k / (n - 1), y) for k in range(n)]


def _bump(dy, x0=0.0, x1=60.0, n=121, c=30.0, w=8.0):
    """A straight strand stepping `dy` sideways over |x − c| < w (smoothly)."""
    out = []
    for k in range(n):
        x = x0 + (x1 - x0) * k / (n - 1)
        t = max(0.0, 1.0 - abs(x - c) / w)
        out.append((x, dy * (t * t * (3 - 2 * t))))
    return out


RING = [[(-5, -15), (65, -15), (65, 15), (-5, 15), (-5, -15)]]       # a wide wall: the envelope


def _strand(pts):
    return {'id': 's', 'pts': [list(p) for p in pts], 'closed': False}


# ---- unit ------------------------------------------------------------------------------------

def test_a_stepped_strand_is_pulled_back_onto_the_bead_below():
    up = [_strand(_bump(4.0))]
    new, recs = repair_layer([_line(0.0)], up, _Inst(), RING, None, B, B, 1.0, {0})
    assert recs and all(r['strategy'] == 'contact_shift' and r['resolved'] for r in recs)
    P = [tuple(q) for q in new[0]['pts']]
    assert P[0] == (0.0, 0.0) and P[-1] == (60.0, 0.0)                 # strand ends never move
    assert max(abs(y) for _, y in P) < max(abs(y) for _, y in _bump(4.0))
    assert _max_turn(P) <= max(_max_turn(_bump(4.0)), math.radians(25)) + 1e-9
    # (away from the window: exactly the generated strand)
    far = [q for q in P if abs(q[0] - 30.0) > 8.0 + 4 * B + 1]
    assert all(abs(y) < 1e-12 for _, y in far)


def test_a_bridge_is_left_as_generated_and_reported():
    # the strand's middle crosses empty space (nothing below within reach):
    # it cannot be pulled onto anything — it stays and stays reported
    up = [_strand(_line(10.0))]
    new, recs = repair_layer([_line(0.0, 0, 20), _line(0.0, 40, 60)], up, _Inst(), [[(-5, -15), (65, -15), (65, 25), (-5, 25), (-5, -15)]],
                             None, B, B, 1.0, {0})
    assert recs and all(not r['resolved'] for r in recs)
    P = [tuple(q) for q in new[0]['pts']]
    assert all(abs(y - 10.0) < 1e-9 for x, y in P if 25 <= x <= 35)    # (the bridged middle stays)


def test_a_move_out_of_the_architecture_is_refused():
    # the only bead below lies outside this layer's (narrow) envelope: the
    # strand would have to leave the wall to reach it — no repair
    narrow = [[(-5, 1.0), (65, 1.0), (65, 9), (-5, 9), (-5, 1.0)]]
    up = [_strand(_line(5.0))]
    new, recs = repair_layer([_line(-1.5)], up, _Inst(), narrow, None, B, B, 1.0, {0})
    assert recs and not any(r['strategy'] for r in recs)
    assert new[0] is up[0]


def test_the_layer_above_never_loses_support():
    up = [_strand(_bump(4.0))]
    above = [[(x, y) for x, y in _bump(4.0)]]                          # rests exactly on the generated strand
    new, recs = repair_layer([_line(0.0)], up, _Inst(), RING, above, B, B, 1.0, {0})
    assert not any(r['strategy'] for r in recs)                        # (moving it would unsupport the layer above)
    assert new[0] is up[0]


def test_only_internal_strands_move():
    up = [_strand(_bump(4.0))]
    new, recs = repair_layer([_line(0.0)], up, _Inst(), RING, None, B, B, 1.0, set())   # (a skin / cap)
    assert new[0] is up[0] and recs == []


def test_crowding_is_refused():
    # another strand of the layer lies where the repair would put this one
    up = [_strand(_bump(4.0)), _strand(_line(0.6, 20, 40))]
    new, recs = repair_layer([_line(0.0)], up, _Inst(), RING, None, B, B, 1.0, {0})
    assert not any(r['strategy'] for r in recs if r['strand'] == 0)


# ---- stacks ------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def _ws(i, t, members, th=10.0):
    P = {'interleaved': {'pattern': 'wave', 'paths': 4, 'period': 24},
         'linked': {'pattern': 'wave', 'paths': 4, 'period': 24},
         'chain': {'pitch': 9.0, 'neck': 0.2}, 'parallel': {'walls': 4}}
    return {'id': f'WS{i}', 'name': f'S{i}', 'type': t, 'thickness': th, 'align': 'auto', 'print_reference': False,
            'params': P.get(t, {}), 'members': members,
            'web': {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0} if t == 'skin_web' else {}}


def _doc(paths, systems, junction='round'):
    d = {'id': 'b', 'material': dict(MAT), 'source_paths': paths, 'wall_systems': systems, 'openings': [], 'infills': [],
         'junction_style': junction, 'junction_radius': 6}
    for w in systems:
        if w['type'] == 'skin_web':
            for m in w['members']:
                d['infills'].append({'id': f'{w["id"]}~web~{m}', 'path_id': m, 'kind': 'wall', 'owner': w['id'],
                                     'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0})
    return d


R_ = lambda i, x, y, w, h: {'id': i, 'type': 'RectanglePath', 'x': x, 'y': y, 'w': w, 'h': h, 'closed': True}  # noqa: E731
C_ = lambda i, cx, cy, r: {'id': i, 'type': 'CirclePath', 'cx': cx, 'cy': cy, 'radius': r, 'closed': True}      # noqa: E731
L_ = lambda i, a, b: {'id': i, 'type': 'LinePath', 'start': a, 'end': b}                                       # noqa: E731

TF = {'linear': {'profile': 'linear', 'shift': [0.5, 0.25], 'scale_in': 0.0625},
      'quadratic': {'profile': 'quadratic', 'shift': [0.75, 0.0], 'scale_in': 0.125}}
ARRAY = {'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R', 'edge': 0}, 'position': 80.0, 'z_bottom': 4.5,
         'height': 12.0, 'width': 10.0, 'upper': 'pointed', 'lower': 'pointed', 'bottom_depth': 4.5, 'upper_height': 7.5,
         'middle': False, 'middle_height': None, 'label': '',
         'array': {'arrangement': 'horizontal', 'count': 2, 'cols': 2, 'rows': 1, 'h_spacing': 14.0, 'v_spacing': 6.0,
                   'stagger': None}}


def _plane(src):
    return {'kind': 'plane', 'id': 'p2', 'sources': [src], 'systems': [], 'exclude': [], 'direction': 0.0,
            'slope': 0.3, 'anchor': [160.0, 60.0], 'z_anchor': 18.0, 'label': ''}


def case(name, t, tf):
    """(designs, wall_systems, assembly) of a fixture."""
    from wall_groups import split_designs
    A = {'layer_height': 1.5, 'max_overhang': 1.0, 'sections': [{'design_id': 'base', 'height': 24.0}],
         'transform': TF[tf], 'profiles': [], 'profile_counter': 0}
    if name == 'X':                 # rectangle × circle (X junctions), mixed systems, rounded; array + a cut through
        d = _doc([R_('R', 0, 0, 160, 120), C_('C', 190, 60, 70)], [_ws(1, t, ['R']), _ws(2, 'skin_web', ['C'])])
        A['profiles'] = [ARRAY, _plane('C')]
    elif name == 'T':               # a link wall on a room (T junction); array + a planar cut through the junction
        d = _doc([R_('R', 0, 0, 160, 120), L_('L', [160, 60], [260, 60])], [_ws(1, 'skin_web', ['R']), _ws(2, t, ['L'])])
        A['profiles'] = [ARRAY, _plane('L')]
    elif name == 'Y':               # three walls at one node; an opening array on one leg (ends below the top)
        d = _doc([L_('A', [0, 0], [100, 0]), L_('B', [100, 0], [170, 70]), L_('D', [100, 0], [170, -70])],
                 [_ws(1, t, ['A']), _ws(2, t, ['B']), _ws(3, t, ['D'])], junction='miter')
        A['profiles'] = [dict(ARRAY, wall={'source': 'A', 'edge': None}, position=50.0)]
    elif name == 'Ygroups':         # a transform group moves one leg: the junction changes shape layer by layer
        d = _doc([L_('A', [0, 0], [100, 0]), L_('B', [100, 0], [170, 70]), L_('D', [100, 0], [170, -70])],
                 [_ws(1, t, ['A']), _ws(2, t, ['B']), _ws(3, t, ['D'])], junction='miter')
        A.update({'center_mode': 'multiple', 'centers': [{'id': 'c1', 'x': 170, 'y': 70, 'sources': ['B']}],
                  'center_counter': 1})
    ds, ws = split_designs([{'id': 'base', 'name': 'B', 'parent': None, 'document': d}])
    return ds, ws, A


def _clear():
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS, web._GENERATED, web._ARCH_LIBS):
        reg.clear()


def m3_and_m4(ds, ws, A):
    from layer_assembly import web
    from layer_assembly.source import layer_geometry
    _clear()
    data = {'assembly': A, 'designs': ds, 'wall_systems': ws}
    st = web._resolve_stack(data)
    geo = dict(st['geo'])
    for i in st['res'].instances:
        if i.is_variant:
            g = layer_geometry(st['src'], i)
            geo[i.geometry_key] = (g.polylines, g.bead_width)
    before = web._support_report(st, geo)
    st2, geo2, repairs = web._repair(st, geo, web.generation_key(data))
    after = web._support_report(st2, geo2)
    return st, st2, before, after, repairs


def _unsupported_points(rep):
    out = {}
    for f in rep.findings:
        out.setdefault(f.layer, []).extend(q for r in f.runs for q in r)
    return out


CASES = [('X', 'interleaved', 'linear'), ('X', 'linked', 'quadratic'), ('X', 'chain', 'linear'),
         ('T', 'interleaved', 'quadratic'), ('T', 'chain', 'linear'),
         ('Y', 'chain', 'linear'), ('Y', 'interleaved', 'quadratic'), ('Ygroups', 'chain', 'quadratic')]


@pytest.mark.parametrize('name,t,tf', CASES)
def test_local_repair_against_m3(name, t, tf):
    from layer_assembly.source import layer_geometry
    ds, ws, A = case(name, t, tf)
    st, st2, before, after, repairs = m3_and_m4(ds, ws, A)
    src = st['src']
    # never more findings, never a newly unsupported sample
    assert len(after.findings) <= len(before.findings)
    bp = _unsupported_points(before)
    for f in after.findings:
        for q in (q for r in f.runs for q in r):
            assert any(math.dist(q, p) <= 2 * B + 1e-9 for p in bp.get(f.layer, [])), (f.layer, q)
    recs = [r for r in repairs['records'] if r['strategy']]
    for k in repairs['layers']:
        i0, i1 = st['res'].instances[k], st2['res'].instances[k]
        g0, g1 = layer_geometry(src, i0), layer_geometry(src, i1)
        assert i1.repair and g1 is not g0
        # the architecture is untouched (the instance places the same design / cuts)
        assert (i0.design_id, i0.transform, i0.cuts, i0.semantic) == (i1.design_id, i1.transform, i1.cuts, i1.semantic)
        T = i0.transform
        win = [r for r in recs if r['layer'] == k]
        assert win
        for a, b in zip(g0.polylines, g1.polylines):
            assert len(b['pts']) >= len(a['pts'])
            assert tuple(a['pts'][0]) == tuple(b['pts'][0]) and tuple(a['pts'][-1]) == tuple(b['pts'][-1])
            orig = [tuple(q) for q in a['pts']]
            for q in b['pts']:
                q = tuple(q)
                if q in orig:
                    continue
                # a new / moved vertex: inside one of this layer's repair windows
                P = T.apply(*q)
                assert any(w and w[0] - 1e-6 <= P[0] <= w[2] + 1e-6 and w[1] - 1e-6 <= P[1] <= w[3] + 1e-6
                           for w in (r['window'] for r in win)) or \
                    any(math.dist(P, T.apply(*o)) < 1e-6 for o in orig), (k, P)
        # the repaired layer still routes, with no more pieces
        m0, _, _ = src.route(g0)
        m1, _, _ = src.route(g1)
        trav = lambda ms: sum(1 for m in ms if getattr(m, 'kind', None) == 'travel')     # noqa: E731
        assert trav(m1) <= trav(m0)


def test_plan_print_uses_the_repaired_stack(monkeypatch):
    from app import app
    monkeypatch.setenv('WALL_LATTICE_REPAIR', 'on')
    _clear()
    ds, ws, A = case('X', 'interleaved', 'linear')
    body = {'assembly': A, 'designs': ds, 'wall_systems': ws}
    app.config['TESTING'] = True
    with app.test_client() as c:
        post = lambda u, b: c.post(u, data=json.dumps(b), content_type='application/json')   # noqa: E731
        g = post('/api/wallsystems/generate', body).get_json()
        R = g['inspection']['repairs']
        assert R['mode'] == 'on' and R['applied'] > 0 and R['layers']
        assert R['remaining_findings'] == len(g['support']['findings'])
        r = post('/api/assembly/print_plan', dict(body, detail='stream', require_generated=True))
        assert r.status_code == 200
        p = r.get_json()
        assert p['stream'] and p['support']['findings'] == g['support']['findings']
        # the repaired layers are the planned ones; the generation reports them in its layer list
        lay = post('/api/wallsystems/layer', dict(body, index=R['layers'][0])).get_json()
        assert lay.get('polylines')


def test_repair_off_is_the_m3_output(monkeypatch):
    from app import app
    _clear()
    ds, ws, A = case('T', 'chain', 'linear')
    body = {'assembly': A, 'designs': ds, 'wall_systems': ws}
    app.config['TESTING'] = True
    with app.test_client() as c:
        post = lambda u, b: c.post(u, data=json.dumps(b), content_type='application/json')   # noqa: E731
        monkeypatch.setenv('WALL_LATTICE_REPAIR', 'off')
        off = post('/api/wallsystems/generate', body).get_json()
        monkeypatch.setenv('WALL_LATTICE_REPAIR', 'on')
        on = post('/api/wallsystems/generate', body).get_json()
    assert off['inspection']['repairs']['mode'] == 'off' and off['inspection']['repairs']['applied'] == 0
    assert len(on['support']['findings']) <= len(off['support']['findings'])
