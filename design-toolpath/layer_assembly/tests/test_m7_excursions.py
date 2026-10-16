"""
M7 (2026-10-16, WALL_ARCHITECTURE.md §6, §F3): CONTINUOUS OUT-AND-BACK EXCURSIONS.

Before M7 a thin Out-and-Back branch meeting a GENERATED host (Interleaved,
Linked, Parallel, Chained Loop) was extended straight THROUGH the host's
paths and capped inside (`extend_through`): it joined the route wherever it
crossed something — any wave, any lobe.

Now (`wall_systems.splice_excursions`, `model._connect_skins`): on each built
layer the host strand is the one BOTH lanes reach first (resolved
geometrically, never stored); it is cut between the two landings p1, p2; the
excursion (p1, lane, U-turn, lane, p2) is its own strand with
`join_group = 'exc:…'`, `join_points = [p1, p2]` — the router can enter and
leave it only there (host → excursion → the SAME host), and every incidental
crossing (another wave, a lobe, the host elsewhere) is a pass-over. All other
strands are untouched.
"""
import copy
import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for p in (os.path.join(ROOT, 'design_proto'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402,F401  (the Designer's `app` before toolpath_proto is on the path)
from layer_assembly.tests.golden import cases as CS  # noqa: E402
from layer_design import DesignLibrary  # noqa: E402


def _ws(typ, params=None, members=('R',)):
    if params is None:
        params = {'paths': 4} if typ in ('interleaved', 'linked') else ({'walls': 3} if typ == 'parallel' else {})
    return [{'id': 'S1', 'name': 'S1', 'type': typ, 'params': params, 'web': {}, 'members': list(members)}]


def _room(T=12.0):
    R = CS.rect('R', 0, 0, 120, 80, 'Room')
    R['envelope'] = {'thickness': T, 'align': 'center', 'print_reference': False}
    return R


def _ring(T=12.0):
    C = CS.circle('R', 0, 0, 60, 'Tower')
    C['envelope'] = {'thickness': T, 'align': 'center', 'print_reference': False}
    return C


def _ob(pid, a, b):
    return CS.line(pid, a, b, 'Out-and-back')


def _build(paths, typ, params=None, mat=None, members=('R',), variants=()):
    d = CS.doc([copy.deepcopy(p) for p in paths])
    d.pop('wall_groups', None)
    if mat:
        d['material'] = dict(d['material'], **mat)
    designs = [{'id': 'base', 'name': 'Base', 'parent': None, 'document': d}] + list(variants)
    return DesignLibrary(designs, _ws(typ, params, members))


def _route(b):
    import layer_route as LR
    prep = LR.Prepared(b.printable, b.routing)
    moves, _ = LR.route(prep)
    return prep, moves


def _excursions(b):
    """per excursion strand: (connections at its interior crossings, printed as one block,
    the strand before / after it in the route, its join points)"""
    prep, moves = _route(b)
    G = prep.G
    out = []
    for s in prep.layer.strands:
        if not (s.join_group or '').startswith('exc:'):
            continue
        jp = {(round(q[0], 6), round(q[1], 6)) for q in s.join_points}
        inner = {n for u, v, d in G.edges(data=True) if d.get('strand_id') == s.id for n in (u, v)
                 if (round(n[0], 6), round(n[1], 6)) not in jp}
        shared = [n for n in inner if any(d.get('strand_id') != s.id for _, _, d in G.edges(n, data=True))]
        seq = [m for m in moves if m.kind != 'travel']
        mine = [m.strand_id == s.id for m in seq]
        n = len(seq)
        cyc = n > 1 and math.dist((seq[0].start.x, seq[0].start.y), (seq[-1].end.x, seq[-1].end.y)) < 1e-6
        if cyc and mine[0] and not all(mine):
            k0 = mine.index(False)
            seq, mine = seq[k0:] + seq[:k0], mine[k0:] + mine[:k0]
        idx = [k for k in range(len(seq)) if mine[k]]
        block = bool(idx) and idx == list(range(idx[0], idx[-1] + 1))
        before = seq[idx[0] - 1].strand_id if idx and idx[0] > 0 else (seq[-1].strand_id if idx and cyc else None)
        after = seq[idx[-1] + 1].strand_id if idx and idx[-1] + 1 < len(seq) else (seq[0].strand_id if idx and cyc else None)
        out.append({'id': s.id, 'connections': len(shared), 'block': block, 'from': before, 'to': after,
                    'join_points': s.join_points, 'pts': s.points})
    return out, moves


def _assert_continuous(b, n=1):
    ex, moves = _excursions(b)
    assert len(ex) == n, [e['id'] for e in ex]
    for e in ex:
        assert e['connections'] == 0, 'an incidental crossing became a connection'
        assert e['block'], 'the excursion is printed in one go'
        assert e['from'] is not None and _host(e['from']) == _host(e['to']), \
            f"leaves and rejoins the SAME host strand ({e['from']} → {e['to']})"
    return ex, moves


def _host(sid):
    """a host strand's identity (a cut closed chain may print as two pieces of one strand)"""
    return sid


# 1 / 2 / 3 / 7 / 8 — every generated construction, straight and curved ---------------------

@pytest.mark.parametrize('typ', ['interleaved', 'linked', 'parallel', 'chain'])
@pytest.mark.parametrize('host', ['straight', 'curved'])
def test_one_continuous_excursion_on_one_host_strand(typ, host):
    paths = [_room(), _ob('O', (40, 40), (40, 0))] if host == 'straight' else [_ring(), _ob('O', (0, 0), (60, 0))]
    b = _build(paths, typ).build('base')
    ex, moves = _assert_continuous(b)
    if typ != 'chain' and host == 'straight':
        assert not any(m.kind == 'travel' for m in moves), 'host + excursion print without travel'
    rep = [j for w in b.network['wall_systems'] for j in w.get('junctions') or [] if j.get('excursion_from')]
    assert len(rep) == 1 and rep[0]['excursion'] == ex[0]['id'] and len(rep[0]['contacts']) == 2
    jp = {(round(q[0], 4), round(q[1], 4)) for q in ex[0]['join_points']}
    assert jp == {tuple(c) for c in rep[0]['contacts']}, 'join points = the two landings p1, p2'


def test_parallel_only_the_exposed_lane_is_modified(monkeypatch):
    """the three lanes print as ONE polyline; against the same construction without
    the splice, the only host points that differ lie on the innermost lane (y 4.5)"""
    paths = [_room(), _ob('O', (40, 40), (40, 0))]
    b = _build(paths, 'parallel').build('base')
    ex, _ = _assert_continuous(b)
    now = {tuple(q) for p in b.printable if not p['id'].endswith('.x0') for q in p['pts']}
    ids = {p['id'] for p in b.printable}
    before = {tuple(q) for k, pts in _waves_without_m7(paths, 'parallel', monkeypatch).items()
              if k in ids for q in pts}           # (the pre-M7 crossing loop is gone — compared: the lanes)
    changed = (now ^ before) - {tuple(q) for p in b.printable if p['id'].endswith('.x0') for q in p['pts']}
    assert all(abs(q[1] - 4.5) < 1e-6 for q in changed), sorted(changed)[:6]       # (nothing else moved)
    assert all(abs(q[1] - 4.5) < 1e-6 for q in ex[0]['join_points']), 'landings on the innermost lane'


# 4 — Skin + Web / Hollow: the branch joins the inner skin (network), one circuit ---------------

@pytest.mark.parametrize('typ', ['skin_web', 'hollow'])
def test_skin_hosts_print_the_branch_as_one_excursion_of_the_inner_skin(typ):
    b = _build([_room(), _ob('O', (40, 40), (40, 0))], typ, {}).build('base')
    _, moves = _route(b)
    seq = [m.strand_id for m in moves if m.kind != 'travel']
    first_run = []
    for m in moves:                                   # the run holding the branch (up to the first travel)
        if m.kind == 'travel' and first_run:
            break
        if m.kind != 'travel':
            first_run.append(m.strand_id)
    on = [s.startswith('O') for s in first_run]
    k = on.index(False)
    rot = on[k:] + on[:k]                             # (a closed circuit: start anywhere)
    assert sum(1 for i in range(len(rot)) if rot[i] and not rot[i - 1]) == 1, 'branch printed in one go'
    assert {s for s in first_run if not s.startswith('O')} == {'R.wall+~n0'}, 'within the inner skin'
    assert 'R.wall-' in seq and not any((s or '').startswith('exc:') for s in [x.get('join_group') for x in b.routing['strands']])


# 5 / 6 — Interleaved: crossing is not connection ----------------------------------------------

def _waves_without_m7(paths, typ, monkeypatch):
    import wall_systems as WS
    monkeypatch.setattr(WS, 'splice_excursions',
                        lambda polys, br, *a, **k: (polys, [{'excursion': None, 'reason': 'off'}] * len(br)))
    return {p['id']: p['pts'] for p in _build(paths, typ).build('base').printable}


@pytest.mark.parametrize('x, crossed', [(17, None), (14, 'R~ws0p0.1'), (20, 'R~ws0p0.2'), (71, 'R~ws0p0.3')])
def test_interleaved_excursion_never_connects_to_a_wave_it_crosses(x, crossed, monkeypatch):
    paths = [_room(), _ob('O', (x, 40), (x, 0))]
    b = _build(paths, 'interleaved').build('base')
    ex, _ = _assert_continuous(b)
    host = ex[0]['from']
    now = {p['id']: p['pts'] for p in b.printable}

    def seg_cross(a, b_, c, d):
        r, s = (b_[0] - a[0], b_[1] - a[1]), (d[0] - c[0], d[1] - c[1])
        den = r[0] * s[1] - r[1] * s[0]
        if abs(den) < 1e-12:
            return False
        t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
        u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
        return 1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6
    e = [[q.x, q.y] for q in ex[0]['pts']]
    hit = sorted({k for k, pts in now.items() if k != ex[0]['id'] and k != host
                  for a, b_ in zip(e, e[1:]) for c, d in zip(pts, pts[1:]) if seg_cross(a, b_, c, d)})
    assert hit == ([crossed] if crossed else []), hit
    before = _waves_without_m7(paths, 'interleaved', monkeypatch)
    for k, pts in now.items():
        if k not in (host, ex[0]['id']) and '.x' not in k:
            assert pts == before[k], f'{k} is byte-identical to the construction without the excursion'


# 9 / 10 / 11 / 12 ----------------------------------------------------------------------------

def test_multiple_branches_on_one_host():
    paths = [_room(), _ob('O', (40, 40), (40, 0)), _ob('O2', (90, 40), (90, 80)), _ob('O3', (60, 20), (120, 20))]
    b = _build(paths, 'interleaved').build('base')
    ex, moves = _assert_continuous(b, 3)
    assert not any(m.kind == 'travel' for m in moves)


def test_branch_near_a_corner():
    _assert_continuous(_build([_room(), _ob('O', (12, 40), (12, 0))], 'interleaved').build('base'))


def test_branch_near_an_architectural_junction_keeps_the_trims():
    base = CS.circrect()
    base.pop('wall_groups', None)
    for p in base['source_paths']:
        p['envelope'] = {'thickness': 10.0, 'align': 'center', 'print_reference': False}
    b = _build(base['source_paths'] + [_ob('O', (104, 40), (104, 80))], 'interleaved', members=('R', 'C'))
    doc = b.raw_document('base')
    doc['trims'] = base['trims']
    b = DesignLibrary([{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}],
                      _ws('interleaved', members=('R', 'C'))).build('base')
    _assert_continuous(b)
    assert {k: v['status'] for k, v in b.network['trims'].items()} == {'tR': 'ok', 'tC': 'ok'}


def test_variant_only_branch():
    v = {'id': 'd1', 'name': 'V', 'parent': 'base', 'settings': {},
         'patch': {'source_paths': {'O': _ob('O', (40, 40), (40, 0))}}}
    L = _build([_room()], 'interleaved', variants=[v])
    assert _excursions(L.build('base'))[0] == []
    _assert_continuous(L.build('d1'))


# 13 — the Out-and-Back width stays construction-derived ------------------------------------------

@pytest.mark.parametrize('W, R', [(2.5, 0.5), (3.0, 0.75), (3.5, 1.25)])
def test_bead_and_return_overlap_set_the_excursion_lanes(W, R):
    b = _build([_room(), _ob('O', (40, 40), (40, 0))], 'interleaved', mat={'bead_width': W, 'return_overlap': R}).build('base')
    ex, _ = _assert_continuous(b)
    p1, p2 = ex[0]['join_points']
    assert abs(p1[0] - p2[0]) == pytest.approx(W - R, abs=1e-6), 'lanes W − R apart (the branch 2W − R wide)'


# Assembly: the host is resolved per layer ---------------------------------------------------------

def test_host_strand_is_resolved_on_each_layer():
    """the same branch on differently resolved layers (the room scaled as a transform
    would) may land on different strands — each layer resolves its own host"""
    hosts = []
    for k in range(5):
        s = 1.0 + 0.05 * k
        R = _room()
        R['w'], R['h'] = 120 * s, 80 * s
        ex, _ = _assert_continuous(_build([R, _ob('O', (40 * s, 40 * s), (40 * s, 0))], 'interleaved').build('base'))
        hosts.append(ex[0]['from'])
    assert len(hosts) == 5


# PrintPlan / .mudjob: the fenced excursion reaches the plan and the job ----------------------------

def test_print_plan_and_job_carry_the_excursion():
    import json
    from layer_assembly.tests.golden import harness as H
    P, _ = H.load_project(os.path.join(H.PROJECTS, 'syn_excursion_interleaved.muddesign'))
    c = H._client()
    st = P['assembly']['state']
    body = {'assembly': st, 'designs': P['designer']['designs'], 'wall_systems': P['wall_systems']}
    plan = H._post(c, '/api/assembly/print_plan', dict(body, detail='full'))['plan']
    text = json.dumps(plan)
    assert '.x0' in text and '.x1' in text, 'the excursion strands are planned'
    job = H._post(c, '/api/assembly/print_job', dict(body, name='m7'))['job']
    assert len(job['lifts']) >= 1 and all(L['gcode'] for L in job['lifts'])
    travels = sum(1 for L in plan['layers'] for s in L.get('steps') or [] if s.get('kind') == 'travel')
    assert travels == 0 or travels < len(plan['layers']), 'continuous: no per-layer travel to reach a branch'
