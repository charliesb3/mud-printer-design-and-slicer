"""CHAINED LOOP parameters and editing performance (2026-10-09).

- the parameter model: each control alone, last-edited-wins (client
  resolver, checked against the generator's own effective values), larger
  values, edit sequences (tests/js/ui_chain_params_smoke.js);
- geometry equivalence: the default / existing settings generate exactly
  what they did before the performance work (pinned hashes, taken from the
  code before the change), the shared strip frame and `_simplify` are exact;
- superseded-resolve cancellation (resolve_cancel), the geometry cache
  shared by /api/effective_paths and /api/route;
- bounded complexity: pathological pitches report DENSE, stay within the
  point budget and resolve in bounded time; the junction search's dense
  grid finds exactly the same junctions.
"""
import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app as A                                   # noqa: E402
import resolve_cancel as RC                       # noqa: E402
import wall_systems as WS                         # noqa: E402

T = 10.0
MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


def doc(paths, members, openings=(), params=None, thickness=T):
    return {'id': 'l', 'source_paths': paths, 'offset_treatments': [], 'lattice_instances': [], 'infills': [],
            'openings': list(openings), 'trims': [], 'material': dict(MAT),
            'wall_systems': [{'id': 'WS1', 'name': 'WS1', 'type': 'chain', 'members': members,
                              'thickness': thickness, 'params': dict(params or {})}]}


STRAIGHT = doc([{'id': 'L', 'type': 'LinePath', 'start': [0, 0], 'end': [240, 0]}], ['L'])
CLOSED = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True}], ['R'])
OPENING = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True}], ['R'],
              openings=[{'id': 'o1', 'type': 'opening', 'source_path_id': 'R', 'center_s': 100, 'width': 30,
                         'end_treatment': 'inherit'}])
JUNCTIONED = doc([{'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 140, 'closed': True},
                  {'id': 'L', 'type': 'LinePath', 'start': [100, 0], 'end': [100, 140]}], ['R', 'L'])
CURVED = doc([{'id': 'C', 'type': 'QuadBezierPath', 'start': [0, 0], 'control': [120, 140], 'end': [240, 0]}], ['C'])


@pytest.fixture
def client():
    A.app.config['TESTING'] = True
    return A.app.test_client()


def with_params(d, **p):
    d = copy.deepcopy(d)
    d['wall_systems'][0]['params'] = p
    return d


def post(client, url, d, headers=None):
    r = client.post(url, json=d, headers=headers or {})
    return r.status_code, r.get_json()


def printable_hash(r):
    return hashlib.sha1(json.dumps([[round(x, 6) for q in s['pts'] for x in q]
                                    for s in r['printable']]).encode()).hexdigest()[:16]


def chain_rep(r):
    return r['network']['wall_systems'][0]


# --- geometry equivalence --------------------------------------------------------------

# taken with the wall_systems.py of before this pass (default settings). The
# Chained Loop CORNER / JUNCTION MOTIFS (2026-10-09, test_chain_motifs.py)
# change corners and junction ends on purpose: the hashes are checked with the
# motifs switched off — everything else is still exactly as before.
DEFAULT_HASHES = {'straight': (STRAIGHT, '85fa31e7568106ca'), 'closed': (CLOSED, 'a6512051624c1d73'),
                  'opening': (OPENING, 'ed20b23ba23ca33b')}


@pytest.mark.parametrize('name', sorted(DEFAULT_HASHES))
def test_default_geometry_unchanged(client, name, monkeypatch):
    monkeypatch.setattr(WS, 'CHAIN_MOTIFS', False)
    A._GEOM_CACHE.clear()
    d, h = DEFAULT_HASHES[name]
    code, r = post(client, '/api/effective_paths', d)
    A._GEOM_CACHE.clear()
    assert code == 200 and printable_hash(r) == h


def test_shared_frame_is_exact():
    """`at(s, y, frame(s))` (the frame evaluated once, shared by both laps)
    is the very same point as `at(s, y)`."""
    import wall_lattice as WL
    from model import Vec2
    rings = [[Vec2(0, 0), Vec2(240, 0), Vec2(240, 10), Vec2(120, 10), Vec2(120, 80), Vec2(110, 80),
              Vec2(110, 10), Vec2(0, 10)]]
    thick = WL._thickness(rings)
    sk = WL.skeleton(rings, max(0.5, min(thick / 3.0, 20.0 / 3.0)), thick)
    geo = WL._Geo(sk)
    env = WS._Envelope(rings, 3.0)
    n = 0
    for run in sk.runs:
        st = WS._Strip(geo, run, 3.0, thick, env)
        for k in range(200):
            s = st.lo + (st.hi - st.lo) * k / 199
            for y in (-5.0, -3.5, -1.0, 0.0, 2.2, 3.5, 4.9):
                a, b = st.at(s, y), st.at(s, y, st.frame(s))
                assert (a.x, a.y) == (b.x, b.y)
                n += 1
    assert n > 0


def _simplify_reference(pl, tol=0.01):
    """`_simplify` as it was before the 2026-10-09 speed-up."""
    if len(pl) < 3:
        return pl
    key = lambda q: (round(q.x, 6), round(q.y, 6))  # noqa: E731
    seen = {}
    for q in pl[1:-1]:
        seen[key(q)] = seen.get(key(q), 0) + 1
    out = [pl[0]]
    for i in range(1, len(pl) - 1):
        a, b, c = out[-1], pl[i], pl[i + 1]
        if seen[key(b)] > 1:
            out.append(b)
            continue
        d = c - a
        L2 = d.x * d.x + d.y * d.y
        if L2 > 1e-18:
            t = max(0.0, min(1.0, ((b.x - a.x) * d.x + (b.y - a.y) * d.y) / L2))
            if (a + d * t).dist(b) < tol:
                continue
        out.append(b)
    out.append(pl[-1])
    return out


def test_simplify_is_exact():
    from model import Vec2
    a, w, q = WS.chain_shape(5.0, 0.0, 0.0)
    for n in (50, 400, 3000):
        us = [3 * math.pi * k / n for k in range(n + 1)]
        pl = [Vec2(a * u + w * math.sin(2 * u), 3.5 * math.copysign(abs(math.sin(u)) ** q, math.sin(u))) for u in us]
        pl += list(reversed(pl))[1:]                      # (a doubled path: repeated vertices kept)
        assert [(p.x, p.y) for p in WS._simplify(pl)] == [(p.x, p.y) for p in _simplify_reference(pl)]


# --- each control alone, larger values ------------------------------------------------

@pytest.mark.parametrize('param,value', [('pitch', 6.0), ('loop_depth', 2.0), ('loop_width', 5.0),
                                         ('neck', 0.4), ('phase', 0.3)])
def test_each_control_changes_the_geometry_alone(client, param, value):
    _, r0 = post(client, '/api/effective_paths', STRAIGHT)
    _, r1 = post(client, '/api/effective_paths', with_params(STRAIGHT, **{param: value}))
    assert printable_hash(r0) != printable_hash(r1)
    run = chain_rep(r1)['per_run'][0]
    base = chain_rep(r0)['per_run'][0]
    if param == 'pitch':
        assert abs(run['pitch'] - 6.0) < 0.2 and run['loops'] > base['loops']
    if param == 'loop_depth':
        assert run['loop_depth'] == 2.0 and run['pitch'] == base['pitch']
    if param == 'loop_width':
        assert abs(run['loop_width'] - 5.0) < 0.05 and run['pitch'] == base['pitch']
    if param == 'neck':
        assert abs(run['neck'] - 0.4) < 0.01 and run['loop_width'] == base['loop_width']
    if param == 'phase':
        assert run['loops'] == base['loops'] and run['loop_width'] == base['loop_width']


@pytest.mark.parametrize('d', [STRAIGHT, CLOSED, OPENING, JUNCTIONED, CURVED],
                         ids=['straight', 'closed', 'opening', 'junctioned', 'curved'])
def test_larger_values_resolve_and_close(client, d):
    """Large pitch, the widest loop, a high neck, the full envelope: valid and
    each piece one closed route (as at the defaults)."""
    code, r = post(client, '/api/route', with_params(d, pitch=60.0, loop_width=96.0, neck=0.55, loop_depth=3.5))
    assert code == 200 and not r.get('error')
    rep = chain_rep(r)
    assert all(abs(i['loop_width'] - 1.6 * i['pitch']) < 0.05 * i['pitch'] or i['loop_width'] <= 1.6 * i['pitch'] + 1e-6
               for i in rep['per_run'])
    assert r['closure']['closed'] == r['closure']['components']
    assert not rep['chain']['dense']


# --- last-edited-wins: the client resolver against the generator ------------------------

_JS = r"""
const fs = require('fs'), path = require('path');
let src = fs.readFileSync(path.join(%r, 'static', 'app.js'), 'utf8');
const a = src.indexOf('const CHAIN = {'), b = src.indexOf('function _chainOnThickness');
eval(src.slice(a, b).replace(/^const /mg, 'var ') + `;
const out = [];
for (const [p, k, v] of JSON.parse(process.argv[1])) {
  const r = _chainResolve({ ...p, [k]: v }, k, 10, 3);
  out.push({ params: r.params, eff: _chainEffective(r.params, 10, 3) });
}
console.log(JSON.stringify(out));`);
"""

CASES = [
    ({'pitch': 8.0}, 'loop_width', 20.0),     # width wins → pitch 12.5
    ({'pitch': 12.0, 'loop_width': 20.0}, 'pitch', 6.0),   # pitch wins → width 9.6
    ({'pitch': 10.0, 'loop_width': 8.0}, 'neck', 0.8),     # neck wins → width narrowed
    ({'pitch': 10.0, 'loop_width': 2.0}, 'neck', 0.06),    # neck wins → width widened
    ({'pitch': 10.0, 'neck': 0.6}, 'loop_width', 16.0),    # width wins → neck into range
    ({}, 'loop_depth', 50.0),                              # depth ≤ the envelope
]


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_last_edited_wins_matches_the_generator(client):
    """The values the panel shows after a last-edited-wins edit are the ones
    the generator produces (on a long straight wall, whose fitted pitch is
    the requested one within a fraction of a percent)."""
    root = os.path.join(os.path.dirname(__file__), '..')
    proc = subprocess.run(['node', '-e', _JS % root, json.dumps(CASES)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    long_wall = doc([{'id': 'L', 'type': 'LinePath', 'start': [0, 0], 'end': [1200, 0]}], ['L'])
    for (p0, k, v), r in zip(CASES, res):
        assert r['params'][k] == pytest.approx(v if k != 'loop_depth' else 3.5)
        _, out = post(client, '/api/effective_paths', with_params(long_wall, **r['params']))
        run = chain_rep(out)['per_run'][0]
        e = r['eff']
        assert run['pitch'] == pytest.approx(e['pitch'], rel=0.01)
        assert run['loop_width'] == pytest.approx(e['loop_width'], rel=0.02, abs=0.02)
        assert run['neck'] == pytest.approx(e['neck'], abs=0.01)
        assert run['loop_depth'] == pytest.approx(e['loop_depth'], abs=1e-3)
        if k == 'neck':
            assert run['neck'] == pytest.approx(v, abs=0.01)     # the edited value is honoured


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_chain_params_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_chain_params_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI CHAIN PARAMS SMOKE PASSED' in proc.stdout


# --- superseded resolves, the geometry cache ---------------------------------------------

def test_resolve_cancel_tokens():
    RC.begin('ch-a', 1)
    RC.check()                                     # nothing newer
    RC._latest['ch-a'] = 2                         # a newer request of the page began
    with pytest.raises(RC.Superseded):
        RC.check()
    RC.end()
    RC.check()                                     # no token: never cancelled
    with pytest.raises(RC.Superseded):
        RC.begin('ch-a', 1)                        # a late older request is refused
    RC.begin('ch-b', 5)
    RC.check()                                     # other pages are independent
    RC.end()
    RC.begin(None, None)
    RC.check()


def test_late_request_answers_superseded(client):
    RC._latest['page-late'] = 10
    code, r = post(client, '/api/route', STRAIGHT, {'X-Resolve-Channel': 'page-late', 'X-Resolve-Seq': '3'})
    assert code == 409 and r == {'superseded': True}


def test_request_stops_when_superseded_mid_resolve(client, monkeypatch):
    """A newer request beginning while the generator runs stops the older one
    at its next checkpoint."""
    orig = WS._chain_path

    def newer_begins(*a, **k):
        RC._latest['page-mid'] = 99
        return orig(*a, **k)
    monkeypatch.setattr(WS, '_chain_path', newer_begins)
    A._GEOM_CACHE.clear()
    code, r = post(client, '/api/route', with_params(OPENING, pitch=7.0),
                   {'X-Resolve-Channel': 'page-mid', 'X-Resolve-Seq': '1'})
    assert code == 409 and r['superseded']
    monkeypatch.setattr(WS, '_chain_path', orig)
    code, r = post(client, '/api/route', with_params(OPENING, pitch=7.0))        # (no headers: never cancelled)
    assert code == 200 and r['moves']


def test_route_reuses_the_geometry_of_the_same_payload(client, monkeypatch):
    from model import PrintLayer
    A._GEOM_CACHE.clear()
    calls = []
    orig = PrintLayer._build_effective

    def counted(self):
        calls.append(1)
        return orig(self)
    monkeypatch.setattr(PrintLayer, '_build_effective', counted)
    d = with_params(JUNCTIONED, pitch=9.0)
    _, g = post(client, '/api/effective_paths', d)
    _, r = post(client, '/api/route', d)
    assert len(calls) == 1                         # the route reused the geometry
    A._GEOM_CACHE.clear()
    _, r2 = post(client, '/api/route', d)          # resolved afresh
    assert len(calls) == 2 and r == r2 and g['printable'] == r['printable']
    _, r3 = post(client, '/api/route', with_params(JUNCTIONED, pitch=9.5))     # any edit misses
    assert len(calls) == 3 and r3['printable'] != r['printable']


# --- bounded complexity ------------------------------------------------------------------

def test_pathological_pitch_is_bounded_and_reported(client):
    t0 = time.perf_counter()
    code, r = post(client, '/api/effective_paths', with_params(STRAIGHT, pitch=0.1))
    dt = time.perf_counter() - t0
    assert code == 200
    ch = chain_rep(r)['chain']
    assert ch['dense'] and ch['loops'] > 1000
    assert ch['points'] <= 2 * (WS.CHAIN_MIN_SAMPLES * ch['loops'] + WS.CHAIN_MAX_POINTS) + 2000   # sampling budget
    assert dt < 20.0
    _, r0 = post(client, '/api/effective_paths', STRAIGHT)
    assert not chain_rep(r0)['chain']['dense']


def test_dense_junction_grid_is_exact_and_finer():
    """Many long strokes close together (a tiny pitch): the junction grid
    takes a finer cell than the median rule, and finds exactly the same
    junctions as the median-rule cell."""
    import graph as G
    layer = A._deserialise_layer(with_params(STRAIGHT, pitch=0.25))
    paths, meta = layer._build_effective()
    rl = layer.to_routing_layer(paths, meta)
    ls = sorted(a.dist(b) for st in rl.strands for a, b in st.segments())
    median_cell = max(G.GRID_MIN, min(G.GRID_MAX, 4.0 * ls[len(ls) // 2]))
    cell = G._grid_cell(rl)
    assert cell < median_cell
    fine = G._junction_splits_uncached(rl)
    orig = G._grid_cell
    try:
        G._grid_cell = lambda layer_: median_cell
        coarse = G._junction_splits_uncached(rl)
    finally:
        G._grid_cell = orig
    assert fine == coarse and len(fine) > 100


def test_default_layers_keep_the_median_cell():
    import graph as G
    layer = A._deserialise_layer(OPENING)
    paths, meta = layer._build_effective()
    rl = layer.to_routing_layer(paths, meta)
    ls = sorted(a.dist(b) for st in rl.strands for a, b in st.segments())
    assert G._grid_cell(rl) == max(G.GRID_MIN, min(G.GRID_MAX, 4.0 * ls[len(ls) // 2]))
