"""
M7 correction pass (2026-10-16, after manual review — WALL_ARCHITECTURE.md
"M7 correction"): THROUGH walls, layer consistency, host-search robustness.

Before: the Out-and-Back arms of one generated part were spliced ONE AFTER
ANOTHER, each on the geometry the previous ones had cut, and the host search
refused any stretch holding a vertex the strand visits twice (a guard made
for Parallel kisses). A Chained Loop's necks are such vertices, so
  * a branch whose lanes straddled a neck found no host (it depended on the
    pitch / phase: the loop pattern re-fits to every envelope change), and
  * the second arm of a wall running THROUGH the host reached the stretch
    the first arm had just cut —
and both fell back to the pass-10 crossing loop (`extend_through`): the
bracket / staple seen in review. Base, Variants and cut layers re-fit the
loops differently, so the same architecture switched between excursion and
bracket from layer to layer.

Now (`wall_systems.splice_excursions`): every arm is resolved on the SAME
unspliced geometry (order-independent), without the kiss guard, and each
host strand is cut once for all its landings (alternating stretches: every
landing keeps one host end). A through wall = two local excursions, one per
side of the host it crosses (the two arms of the architectural crossing).
An arm with no host strand is REPORTED (`status: unresolved`) and keeps its
own beads — never a crossing loop.
"""
import os

import pytest

from layer_assembly.tests.test_m7_excursions import (  # noqa: E402
    CS, DesignLibrary, _build, _excursions, _ob, _ring, _room, _route, _ws)

GENERATED = ['interleaved', 'linked', 'parallel', 'chain']


def _reports(b):
    return [j for w in b.network['wall_systems'] for j in w.get('junctions') or []]


def _no_legacy(b):
    """no pass-10 crossing loop / skin crossing bracket where M7 owns the attachment"""
    reps = _reports(b)
    assert not [j for j in reps if 'crossing_from' in j], 'no crossing loop (bracket) on an Out-and-Back'
    assert not [j for j in reps if j.get('status') in ('fallback', 'unresolved')], reps
    assert not any('~cross' in p['id'] for p in b.printable), 'no skin crossing bracket'


def _uncut(paths, typ, params, monkeypatch):
    """{strand id: its points} of the same construction WITHOUT excursions"""
    import wall_systems as WS
    with monkeypatch.context() as m:
        m.setattr(WS, 'splice_excursions',
                  lambda polys, br, *a, **k: (polys, [{'excursion': None, 'reason': 'off'}] * len(br)))
        b = _build(paths, typ, params).build('base')
    return {p['id']: {(round(q[0], 6), round(q[1], 6)) for q in p['pts']} for p in b.printable if '~ws' in p['id']}


def _assert_same_host(b, uncut):
    """Each excursion (or two arms sharing their landings, printed back to back) is
    entered from and left to pieces of ONE original host strand, contiguously."""
    ex, moves = _excursions(b)
    exc = {e['id'] for e in ex}
    for e in ex:
        assert e['connections'] == 0, 'an incidental crossing became a connection'
        assert e['block'], 'the excursion is printed in one go'
    seq = [m.strand_id for m in moves if m.kind != 'travel']
    runs = []
    for s in seq:                                    # collapse consecutive moves on one strand
        if not runs or runs[-1] != s:
            runs.append(s)
    if len(runs) > 1 and runs[0] == runs[-1]:
        runs.pop()
    pts = {p['id']: {(round(q[0], 6), round(q[1], 6)) for q in p['pts']} for p in b.printable}
    jps = {(round(q[0], 6), round(q[1], 6)) for e in ex for q in e['join_points']}
    n = len(runs)
    for k, s in enumerate(runs):
        if s not in exc or runs[k - 1] in exc:
            continue
        j = k
        while runs[j % n] in exc:                    # (a block of excursions sharing landings)
            j += 1
        before, after = runs[k - 1], runs[j % n]
        hosts = [u for u, U in uncut.items()
                 if (pts[before] - jps) <= U and (pts[after] - jps) <= U]
        assert hosts, f'{s}: leaves {before} and rejoins {after} — pieces of ONE original strand'
    return ex, moves


def _travels(moves):
    return sum(1 for m in moves if m.kind == 'travel')


def _host_travels(host, typ, params=None):
    _, moves = _route(_build([host], typ, params).build('base'))
    return _travels(moves)


def _through(host, typ, x=40.0):
    if host == 'straight':
        return [_room(8.0), _ob('O', (x, 40), (x, -30))]
    return [_ring(8.0), _ob('O', (10, x - 40), (80, x - 40))]


# 1 — a thin branch ENDING at the host: the approved behaviour --------------------------------------

@pytest.mark.parametrize('typ', GENERATED)
def test_branch_ending_at_the_host_is_one_excursion(typ, monkeypatch):
    paths = [_room(8.0), _ob('O', (40, 40), (40, 0))]
    b = _build(paths, typ).build('base')
    ex, moves = _assert_same_host(b, _uncut(paths, typ, None, monkeypatch))
    assert len(ex) == 1
    _no_legacy(b)
    assert _travels(moves) == _host_travels(_room(8.0), typ)


# 2 / 4 / 5 / 6 — a thin wall running THROUGH the host: two local excursions -----------------------

@pytest.mark.parametrize('typ', GENERATED)
@pytest.mark.parametrize('host', ['straight', 'curved'])
def test_through_wall_is_two_local_excursions(typ, host, monkeypatch):
    paths = _through(host, typ)
    b = _build(paths, typ).build('base')
    _no_legacy(b)
    ex, moves = _assert_same_host(b, _uncut(paths, typ, None, monkeypatch))
    assert len(ex) == 2, 'one excursion per side of the crossed host'
    reps = [j for j in _reports(b) if j.get('excursion')]
    assert [j['excursion_from'] for j in reps] == ['O', 'O']
    assert sorted(j['excursion'] for j in reps) == sorted(e['id'] for e in ex)
    hostpaths = [_room(8.0)] if host == 'straight' else [_ring(8.0)]
    assert _travels(moves) == _host_travels(hostpaths[0], typ), 'no travel added by the branch'


def test_interleaved_through_wall_each_side_takes_its_own_exposed_wave(monkeypatch):
    """the inner arm lands on the innermost wave, the outer arm on the outermost;
    every other wave is BYTE-IDENTICAL to the construction without excursions"""
    paths = _through('straight', 'interleaved')
    b = _build(paths, 'interleaved').build('base')
    uncut = _uncut(paths, 'interleaved', None, monkeypatch)
    ex, _ = _assert_same_host(b, uncut)
    inner = next(e for e in ex if e['join_points'][0][1] > 0)
    outer = next(e for e in ex if e['join_points'][0][1] < 0)
    assert inner['from'] != outer['from'], 'two different waves'
    assert all(q[1] > 0 for q in inner['join_points']) and all(q[1] < 0 for q in outer['join_points'])
    now = {p['id']: {(round(q[0], 6), round(q[1], 6)) for q in p['pts']} for p in b.printable if '~ws' in p['id']}
    hosts = {inner['from'], inner['to'], outer['from'], outer['to']}
    for k, U in now.items():
        if k not in hosts and '.x' not in k:
            assert U == uncut[k], f'{k} unchanged'


# 3 — Hollow / Skin + Web: the wall network joins each arm to its side's skin ---------------------

@pytest.mark.parametrize('typ', ['hollow', 'skin_web'])
def test_through_wall_with_skin_hosts(typ):
    b = _build(_through('straight', typ), typ, {}).build('base')
    _no_legacy(b)
    ids = {p['id'] for p in b.printable}
    assert {'O.wall+~n0', 'O.wall+~n1', 'O.wall-~n0', 'O.wall-~n1'} <= ids, 'both arms printed'
    _, moves = _route(b)
    assert _travels(moves) == _host_travels(_room(8.0), typ, {}), 'each arm one circuit with its skin'


# 7 — identical architecture → identical topology on every layer ----------------------------------

def _signature(b):
    return sorted((j.get('excursion_from') or j.get('crossing_from'),
                   'excursion' if j.get('excursion') else ('crossing' if 'crossing_from' in j else j.get('status')))
                  for j in _reports(b))


def test_base_variant_and_assembly_layers_share_one_topology():
    """Base, a Variant whose opening re-fits the loops, and every Assembly layer (cut
    by an Assembly opening): the same through wall + ending branch resolve the SAME
    way — never bracket on one layer and excursion on the next"""
    os.environ['LAYER_ASSEMBLY_WORKERS'] = '0'
    from layer_assembly import web
    from layer_assembly.source import variant_request
    R = _room(8.0)
    d = CS.doc([R, _ob('X', (66, 40), (66, 110)), _ob('O', (0, 50), (40, 50))])
    d.pop('wall_groups', None)
    var = ('d1', {'openings': {'o1': {'id': 'o1', 'source_path_id': 'R', 'center_s': 30.0, 'width': 16.0,
                                      'end_treatment': 'inherit'}}}, {})
    op = {'kind': 'opening', 'id': 'pO', 'wall': {'source': 'R', 'edge': 0}, 'position': 90, 'z_bottom': 3,
          'height': 6, 'width': 9, 'label': '', 'upper': 'pointed', 'lower': 'flat', 'bottom_depth': 0,
          'middle': False, 'middle_height': None, 'upper_height': 4.5, 'driver': 'width'}
    want = [('O', 'excursion'), ('X', 'excursion'), ('X', 'excursion')]
    for pitch in (6.0, 7.0, 9.0):
        ws = _ws('chain', {'pitch': pitch})
        P = CS.project('through', d, [var], [{'design_id': 'd1', 'height': 6}, {'design_id': 'base', 'height': 6}],
                       ws, {'profiles': [op], 'profile_counter': 1})
        designs = P['designer']['designs']
        lib = DesignLibrary(designs, ws)
        sigs = {did: _signature(lib.build(did)) for did in ('base', 'd1')}
        st = web._resolve_stack({'assembly': P['assembly']['state'], 'designs': designs, 'wall_systems': ws})
        seen = {}
        for inst in st['res'].instances:
            did, tr, cu = variant_request(inst) if inst.is_variant else (inst.design_id, None, None)
            key = (did, repr(tr), repr(cu))
            if key not in seen:
                seen[key] = _signature(st['src'].lib.build(did, transforms=tr or None, cuts=cu or None))
            sigs[f'layer {inst.index}'] = seen[key]
        assert len(seen) >= 3, 'the Assembly opening cuts some layers (re-fitted loops)'
        bad = {k: v for k, v in sigs.items() if v != want}
        assert not bad, f'pitch {pitch}: {bad}'


# 8 / 9 — Chained Loop across pitch (loop count / phase at the branch change) ----------------------

def _circrect_chain(extra, pitch):
    """the circle × rectangle union (trimmed), both Chained Loop, 8 in — its chain
    strand visits each NECK vertex twice (as in the user's `test6`)"""
    base = CS.circrect(thickness=8.0)
    base.pop('wall_groups', None)
    for p in base['source_paths']:
        p['envelope'] = {'thickness': 8.0, 'align': 'center', 'print_reference': False}
    base['source_paths'] += extra
    return DesignLibrary([{'id': 'base', 'name': 'Base', 'parent': None, 'document': base}],
                         _ws('chain', {'pitch': pitch}, members=('R', 'C'))).build('base')


def test_chain_resolves_across_a_pitch_sweep(monkeypatch):
    """the same attachment over a sweep of pitches: the loop count and phase at the
    branch change, and in some the lanes straddle a NECK (a vertex the strand visits
    twice — the old search refused every stretch through it) — every one resolves
    to the same strategy: excursion(s), never a crossing loop"""
    import wall_systems as WS
    real = WS._resolve_host
    guarded_fail = []

    def spy(polys, chain, d1, d2, bead, reach, touch_guard=True):
        r = real(polys, chain, d1, d2, bead, reach, touch_guard)
        if real(polys, chain, d1, d2, bead, reach, True) is None and r is not None:
            guarded_fail.append(1)                   # (the old guarded search found nothing)
        return r
    monkeypatch.setattr(WS, '_resolve_host', spy)
    _, moves0 = _route(_circrect_chain([], 7.0))
    loops = set()
    for pitch in (0.0, 5.0, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0, 10.0, 12.0, 14.0):
        for mode in ('end', 'through'):
            for x in (34.5, 36.0, 40.5):
                b = _circrect_chain([_ob('O', (x, 40), (x, 0 if mode == 'end' else -30))], pitch)
                _no_legacy(b)
                ex, moves = _excursions(b)
                assert len(ex) == (1 if mode == 'end' else 2), (pitch, mode, x)
                assert all(e['connections'] == 0 and e['block'] for e in ex), (pitch, mode, x)
                assert _travels(moves) == _travels(moves0), (pitch, mode, x)
                loops |= {i.get('loops') for w in b.network['wall_systems'] for i in w.get('per_run') or []}
    assert len(loops) >= 5, f'the sweep changes the loop count ({sorted(map(str, loops))})'
    assert guarded_fail, 'the sweep covers branches straddling a neck (the old search failed there)'


# 10 — Interleaved / Linked parameters near the branch ---------------------------------------------

@pytest.mark.parametrize('typ, key, values', [('interleaved', 'paths', (2, 3, 4, 5)), ('linked', 'paths', (2, 3, 4))])
@pytest.mark.parametrize('period', [12.0, 20.0, 30.0])
def test_wave_parameters_never_switch_the_strategy(typ, key, values, period):
    for v in values:
        for mode in ('end', 'through'):
            paths = [_room(8.0), _ob('O', (43.3, 40), (43.3, 0 if mode == 'end' else -30))]
            b = _build(paths, typ, {key: v, 'period': period}).build('base')
            _no_legacy(b)
            ex, _ = _excursions(b)
            assert len(ex) == (1 if mode == 'end' else 2) and all(e['connections'] == 0 and e['block'] for e in ex)


# 12 — an arm crossing another wave on its way still returns to its own host -----------------------

@pytest.mark.parametrize('x', [14, 20, 71])
def test_through_arm_crossing_another_wave_rejoins_its_host(x, monkeypatch):
    paths = [_room(), _ob('O', (x, 40), (x, -30))]
    b = _build(paths, 'interleaved').build('base')
    _no_legacy(b)
    ex, _ = _assert_same_host(b, _uncut(paths, 'interleaved', None, monkeypatch))
    assert len(ex) == 2


# 13 — M5 trims / M6 attachment are kept ------------------------------------------------------------

def test_through_wall_near_a_trimmed_junction_keeps_the_trims():
    base = CS.circrect()
    base.pop('wall_groups', None)
    for p in base['source_paths']:
        p['envelope'] = {'thickness': 10.0, 'align': 'center', 'print_reference': False}
    paths = base['source_paths'] + [_ob('O', (104, 30), (104, 120))]
    doc = _build(paths, 'interleaved', members=('R', 'C')).raw_document('base')
    doc['trims'] = base['trims']
    b = DesignLibrary([{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}],
                      _ws('interleaved', members=('R', 'C'))).build('base')
    assert {k: v['status'] for k, v in b.network['trims'].items()} == {'tR': 'ok', 'tC': 'ok'}
    _no_legacy(b)
    ex, _ = _excursions(b)
    assert len(ex) == 2 and all(e['connections'] == 0 and e['block'] for e in ex)


def test_inside_aligned_host_keeps_m6_attachment_and_its_excursion():
    """M6: the branch drawn to an Inside host's source line ends at the band centre
    (far face intact); M7 then makes it one excursion"""
    R = _room(8.0)
    R['envelope']['align'] = 'inside'
    b = _build([R, _ob('O', (40, 40), (40, 0))], 'chain').build('base')
    _no_legacy(b)
    assert [a['branch'] for a in b.network.get('attachments') or []] == ['O']
    ex, _ = _excursions(b)
    assert len(ex) == 1 and ex[0]['connections'] == 0 and ex[0]['block']


# 14 — a Variant-only through wall ------------------------------------------------------------------

def test_variant_only_through_wall():
    v = {'id': 'd1', 'name': 'V', 'parent': 'base', 'settings': {},
         'patch': {'source_paths': {'O': _ob('O', (40, 40), (40, -30))}}}
    L = _build([_room(8.0)], 'chain', variants=[v])
    assert _excursions(L.build('base'))[0] == []
    b = L.build('d1')
    _no_legacy(b)
    assert len(_excursions(b)[0]) == 2


# 15 — PrintPlan / .mudjob carry the same excursions -----------------------------------------------

def test_print_plan_and_job_carry_the_through_excursions():
    import json
    from layer_assembly.tests.golden import harness as H
    d = CS.doc([_room(8.0), _ob('O', (40, 40), (40, -30))])
    d.pop('wall_groups', None)
    ws = _ws('chain', {'pitch': 7.0})
    P = CS.project('through', d, systems=ws)
    c = H._client()
    body = {'assembly': P['assembly']['state'], 'designs': P['designer']['designs'], 'wall_systems': ws}
    plan = H._post(c, '/api/assembly/print_plan', dict(body, detail='full'))['plan']
    built = DesignLibrary(P['designer']['designs'], ws).build('base')
    ids = {p['id'] for p in built.printable}
    assert {'R~ws0p0.x0', 'R~ws0p0.x1'} <= ids
    text = json.dumps(plan)
    assert all(f'"{i}"' in text for i in ids if '~ws' in i), 'every generated strand (both excursions) is planned'
    travels = sum(1 for L in plan['layers'] for s in L.get('steps') or [] if s.get('kind') == 'travel')
    assert travels < len(plan['layers']), 'no per-layer travel to reach the arms'
    job = H._post(c, '/api/assembly/print_job', dict(body, name='m7'))['job']
    assert len(job['lifts']) >= 1 and all(L['gcode'] for L in job['lifts'])
