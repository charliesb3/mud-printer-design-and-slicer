"""PERFORMANCE PASS (2026-10-10): every optimisation must leave results
EXACTLY unchanged — each is checked here against the implementation it
replaced (kept below as the reference), on random and on real geometry —
plus a deterministic work-count regression (no wall-clock thresholds).

- lifts._near / SegIndex.within: only the wanted owners, only the samples
  near their bboxes, stop once decided (was: every owner, every sample);
- lifts.layer_components: candidate rings by bbox, settled by the first hit;
- toolpath_proto.graph.route_layer: a one-component graph is routed as is
  (was: an identical copy);
- a background plan gives way during its variant prefetch (no further
  build started) and the layer components pass (cancellation polled there).

The end-to-end benchmark (timings, not a test): layer_assembly/bench_mixed.py.
"""
import json
import math
import os
import random
import sys
from collections import defaultdict

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.dirname(__file__))
import layer_route  # noqa: E402,F401  (puts toolpath_proto on the path: `graph`)

from layer_assembly import lifts as LF                                     # noqa: E402
from layer_assembly.lifts import LiftSettings, SegIndex, make_piece, samples, _ring   # noqa: E402
from layer_assembly.print_plan import build_plan, stream, PrintSpeeds, PlanCancelled   # noqa: E402


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


# ---- the replaced implementations (reference) ------------------------------------------

def _near_ref(X, idx, r, owners=None, settle=None):
    """lifts._near before the pass: every owner, every sample, exact minimum."""
    out = {}
    for p in X.pts:
        for o, d2 in idx.near_owners(p, r).items():
            if d2 < out.get(o, math.inf):
                out[o] = d2
    return {o: math.sqrt(d2) for o, d2 in out.items()}


def _components_ref(polylines, bead):
    """lifts.layer_components before the pass."""
    n = len(polylines)
    if n <= 1:
        return [list(range(n))]
    rings = [_ring(p['pts'], p.get('closed')) for p in polylines]
    uf = LF._UF(n)
    groups = n
    seen = {}
    for i, R in enumerate(rings):
        for q in (R[0], R[-1]) if R else ():
            key = (round(q[0], 4), round(q[1], 4))
            if key in seen:
                groups -= uf.union(seen[key], i)
            else:
                seen[key] = i
    if groups > 1:
        r = bead * 0.999
        idx = SegIndex(max(r, 1e-6))
        for i, R in enumerate(rings):
            idx.add(i, R)
        step = max(min(bead / 4.0, 0.75), 1e-3)
        for i, R in enumerate(rings):
            if groups == 1:
                break
            for p in samples(R, step):
                hit = idx.near_owners(p, r, skip=lambda o, i=i: uf.find(o) == uf.find(i))
                for o in sorted(hit):
                    groups -= uf.union(i, o)
                if groups == 1:
                    break
    by = defaultdict(list)
    for i in range(n):
        by[uf.find(i)].append(i)
    return sorted((sorted(v) for v in by.values()), key=lambda v: v[0])


# ---- random geometry ---------------------------------------------------------------------

def _poly(rng, cx, cy, size):
    k = rng.randint(2, 7)
    pts = [(cx + rng.uniform(-size, size), cy + rng.uniform(-size, size)) for _ in range(k)]
    return {'pts': pts, 'closed': k > 2 and rng.random() < 0.5}


def _layer(rng, n, spread=60.0, size=8.0):
    out = [_poly(rng, rng.uniform(0, spread), rng.uniform(0, spread), size) for _ in range(n)]
    for _ in range(n // 4):                       # some shared end vertices (junction nodes)
        a, b = rng.sample(range(n), 2)
        out[b]['pts'][0] = out[a]['pts'][-1]
    return out


def test_layer_components_identical_to_the_reference():
    rng = random.Random(7)
    for trial in range(60):
        polys = _layer(rng, rng.randint(2, 30), spread=rng.choice([30.0, 80.0, 200.0]))
        bead = rng.choice([1.5, 3.0, 6.0])
        assert LF.layer_components(polys, bead) == _components_ref(polys, bead), trial


def _stack(rng, layers, per_layer, spread):
    """{layer: [Piece]} — each layer's pieces made of a few random rings."""
    out = {}
    for L in range(layers):
        out[L] = []
        for c in range(per_layer):
            cx, cy = rng.uniform(0, spread), rng.uniform(0, spread)
            rings = [_ring(p['pts'], p['closed']) for p in (_poly(rng, cx, cy, 10.0) for _ in range(rng.randint(1, 3)))]
            out[L].append(make_piece(L, c, rings, [c], 0.75))
    return out


def _graph_tuple(G):
    return (G.preds, G.down, G.up, G.group, G.by_layer)


def test_near_decides_every_test_like_the_reference():
    rng = random.Random(11)
    for trial in range(40):
        st = _stack(rng, 2, 6, 60.0)
        r1, r2 = 3.0 * 0.999, 3.0 + 1.0
        idx = SegIndex(r2)
        for Q in st[0]:
            for R in Q.rings:
                idx.add(Q.comp, R)
        for P in st[1]:
            ref = _near_ref(P, idx, r2)
            owners = [Q for Q in st[0] if LF._bbox_near(P.bbox, Q.bbox, r2)]
            got = LF._near(P, idx, r2, owners, settle=r1)
            for Q in st[0]:
                a, b = ref.get(Q.comp, math.inf), got.get(Q.comp, math.inf)
                assert (a < r2) == (b < r2) and (a < r1) == (b < r1), (trial, Q.comp, a, b)
                if b >= r1:                         # not settled early: the exact minimum
                    assert (a == b) or (a >= r2 and b == math.inf)
            got2 = LF._near(P, idx, r2, owners)
            for Q in st[0]:
                assert (ref.get(Q.comp, math.inf) < r2) == (got2.get(Q.comp, math.inf) < r2)


def test_lift_graph_identical_to_the_reference(monkeypatch):
    rng = random.Random(3)
    for trial in range(12):
        st = _stack(rng, rng.randint(3, 8), rng.randint(2, 6), rng.choice([40.0, 90.0]))
        new = _graph_tuple(LF.lift_graph(st, 3.0, 1.0))
        monkeypatch.setattr(LF, '_near', _near_ref)
        ref = _graph_tuple(LF.lift_graph(st, 3.0, 1.0))
        monkeypatch.undo()
        assert new == ref, trial


def test_work_is_a_fraction_of_the_reference(monkeypatch):
    """REGRESSION (deterministic, not timed): the segment-distance
    evaluations of a many-island stack — the cost that was ~80 % of a cold
    print plan — stay well below the replaced implementation's."""
    rng = random.Random(5)
    st = _stack(rng, 24, 10, 120.0)
    n = [0]
    real = LF._seg_dist2

    def counted(p, a, b):
        n[0] += 1
        return real(p, a, b)
    monkeypatch.setattr(LF, '_seg_dist2', counted)
    LF.lift_graph(st, 3.0, 1.0)
    new = n[0]
    n[0] = 0
    monkeypatch.setattr(LF, '_near', _near_ref)
    LF.lift_graph(st, 3.0, 1.0)
    ref = n[0]
    assert new * 4 < ref, (new, ref)


# ---- real geometry: whole plans --------------------------------------------------------

def _plan_bytes(plan):
    return json.dumps({'plan': plan.to_dict('full'), 'stream': stream(plan, PrintSpeeds())}, sort_keys=True)


def test_plans_identical_to_the_reference(monkeypatch):
    """Islands merging in stages + a transform-grouped stack: the whole plan
    (passes, runs, travels, stages, times) is byte-identical with the
    replaced proximity code and with the copying router."""
    import graph as GR
    from test_lifts import design, doc, plan_of, R1, R2, R3, BRIDGE12, BRIDGE23
    cases = [([design('three', doc([R1, R2, R3])),
               design('ab', None, parent='three', patch={'source_paths': {'L12': BRIDGE12}}),
               design('abc', None, parent='ab', patch={'source_paths': {'L23': BRIDGE23}})],
              [{'design_id': 'three', 'height': 6}, {'design_id': 'ab', 'height': 6},
               {'design_id': 'abc', 'height': 6}], {}),
             ([design('A', doc([R1, R2]))],
              [{'design_id': 'A', 'height': 18, 'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': -3.0}}],
              dict(center_mode='multiple', center_counter=2,
                   centers=[{'id': 'c1', 'x': 20, 'y': 15, 'sources': ['R1']},
                            {'id': 'c2', 'x': 120, 'y': 15, 'sources': ['R2']}]))]
    real_route = GR.route_layer

    def copying_route(layer, *a, G=None, **kw):
        import networkx as nx
        if G is None:
            G = GR.build_graph(layer)
        if '_route_comps' not in G.graph:
            G.graph['_route_comps'] = [G.subgraph(c).copy() for c in nx.connected_components(G)]
        return real_route(layer, *a, G=G, **kw)
    for designs, sections, kw in cases:
        new = _plan_bytes(plan_of(designs, sections, **kw)[0])
        monkeypatch.setattr(LF, '_near', _near_ref)
        monkeypatch.setattr(LF, 'layer_components', _components_ref)
        monkeypatch.setattr(GR, 'route_layer', copying_route)
        ref = _plan_bytes(plan_of(designs, sections, **kw)[0])
        monkeypatch.undo()
        monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')
        assert new == ref


def test_single_component_route_identical_to_the_copy():
    import graph as GR
    import networkx as nx
    from test_lifts import design, doc, R1
    from layer_assembly.web import _resolve_stack
    import layer_route
    st = _resolve_stack({'assembly': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 3}]},
                         'designs': [design('A', doc([R1]))]})
    g = st['src'].geometry('A')
    a = layer_route.Prepared(g.polylines, g.diagnostics.get('routing') or {})
    b = layer_route.Prepared(g.polylines, g.diagnostics.get('routing') or {})
    G = b.__dict__.get('G') or next((v for v in b.__dict__.values() if isinstance(v, nx.MultiGraph)), None)
    if G is not None:
        assert nx.number_connected_components(G) == 1
        G.graph['_route_comps'] = [G.subgraph(c).copy() for c in nx.connected_components(G)]
    for start in (None, (0.0, 0.0), (40.0, 30.0)):
        ma, _ = layer_route.route(a, start)[:2]
        mb, _ = layer_route.route(b, start)[:2]
        assert repr(ma) == repr(mb)


# ---- worker pool: cancellation ---------------------------------------------------------

def _plane_stack():
    from layer_assembly.model import Assembly, Section, resolve
    from layer_assembly.profiles import PlaneProfile, WallTarget, apply_profiles
    from layer_assembly.designer_source import DesignerSource
    from layer_assembly.tests.test_profiles_plane_designer import building
    src = DesignerSource.from_designs([{'id': 'A', 'name': 'A', 'document': building('skin_web')}])
    recs = src.sources('A')
    plane = PlaneProfile('P', systems=('WS1', 'WS2'), direction=180.0, slope=0.3, anchor=(230.0, 0.0), z_anchor=40.0)
    a = Assembly(1.5, [Section('A', 60)], profiles=[plane])
    tg = {'A': {t.key: t for t in map(WallTarget.from_dict, src.walls('A'))}}
    res, _ = apply_profiles(resolve(a, ['A'], None), a.profiles, tg, 3.0, a.max_overhang, {'A': recs})
    reqs = [('A', None, i.cut_spec()) for i in res.instances if i.cuts]
    return src, reqs[len(reqs) - 6:]                     # (the top layers: cut by the plane)


def test_a_cancelled_pool_prefetch_drops_what_has_not_started(monkeypatch):
    from layer_assembly import designer_source as DS
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '2')
    monkeypatch.setattr(DS, '_POOL', None)
    src, reqs = _plane_stack()
    assert len(reqs) >= 3
    try:
        src.prefetch(reqs, cancelled=lambda: True)
        assert len(src._variants) <= 2                  # (at most the builds already running)
        # … and what was kept is exactly what an uncancelled build gives
        ref, _ = _plane_stack()
        for d, t, c in reqs:
            k = DS._vkey(d, t, c)
            if k in src._variants:
                assert json.dumps(src._variants[k].polylines) == json.dumps(ref.geometry(d, t, c).polylines)
    finally:
        if DS._POOL is not None:
            DS._POOL.shutdown(cancel_futures=True)
        monkeypatch.setattr(DS, '_POOL', None)


def test_a_cancelled_prefetch_starts_nothing_and_the_plan_gives_way(monkeypatch):
    src, reqs = _plane_stack()
    src.prefetch(reqs, cancelled=lambda: True)
    assert not src._variants
    from layer_assembly.web import _resolve_stack
    from test_lifts import design, doc, R1, R2
    st = _resolve_stack({'assembly': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 9}]},
                         'designs': [design('A', doc([R1, R2]))]})
    routed = []
    real = type(st['src']).route
    monkeypatch.setattr(type(st['src']), 'route', lambda self, *a, **k: routed.append(1) or real(self, *a, **k))
    polls = []
    with pytest.raises(PlanCancelled):
        build_plan(st['res'], st['src'], cancelled=lambda: polls.append(1) or len(polls) > 1)
    assert not routed                                   # stopped right after the prefetch


# ---- support: the quick within-reach test ------------------------------------------------

def test_support_within_is_the_reference_test_bit_for_bit():
    """support._SegIndex.within (squared distance away from the threshold,
    the identical hypot near it) = any(_seg_dist(p, a, b) <= r) — also for
    points placed EXACTLY at distance r (the rounding boundary)."""
    from layer_assembly.support import _SegIndex, _seg_dist
    rng = random.Random(9)
    for trial in range(30):
        polys = [[(rng.uniform(0, 50), rng.uniform(0, 50)) for _ in range(rng.randint(2, 6))] for _ in range(6)]
        polys.append([(10.0, 10.0), (10.0, 10.0), (20.0, 10.0)])            # a zero-length segment
        r = rng.choice([0.5, 1.25, 1.5, 3.0])
        idx = _SegIndex(polys, 6.0)
        pts = [(rng.uniform(-5, 55), rng.uniform(-5, 55)) for _ in range(300)]
        for pl in polys:                                                    # exactly r from a segment
            for a, b in zip(pl, pl[1:]):
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                if L > 0:
                    nx_, ny_ = -(b[1] - a[1]) / L, (b[0] - a[0]) / L
                    m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    pts += [(m[0] + nx_ * r, m[1] + ny_ * r), (a[0] - nx_ * r, a[1] - ny_ * r)]
        segs = [(a, b) for pl in polys for a, b in zip(pl, pl[1:])]
        for p in pts:
            assert idx.within(p, r) == any(_seg_dist(p, a, b) <= r for a, b in segs), (trial, p, r)


# ---- the page's variant request hits the variants the resolve built ----------------------

def _js(o):
    """A JSON round trip through JavaScript: 12.0 → 12."""
    if isinstance(o, bool):
        return o
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, dict):
        return {k: _js(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_js(v) for v in o]
    return o


def test_the_pages_variant_request_reuses_the_resolved_variants(monkeypatch):
    """/api/assembly/resolve builds every variant; the page then asks
    /api/assembly/geometry for them with the spec it received — after
    JavaScript wrote whole numbers without '.0'. That request must be served
    from the variants just built (it rebuilt every one: ~2× every exact
    edit), and they are the geometry support and the plan used."""
    import app as A
    from layer_assembly import designer_source as DS
    from layer_assembly.tests.test_profiles_plane_designer import building
    designs = [{'id': 'A', 'name': 'A', 'parent': None, 'document': building('skin_web')}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 30}],
           'profiles': [{'kind': 'plane', 'id': 'P', 'systems': ['WS1', 'WS2'], 'exclude': [], 'sources': [],
                         'direction': 180.0, 'slope': 0.3, 'anchor': [230.0, 0.0], 'z_anchor': 20.0}],
           'profile_counter': 1}
    c = A.app.test_client()
    res = c.post('/api/assembly/resolve', json={'assembly': asm, 'designs': designs}).get_json()
    assert 'error' not in res and res['variants']
    need = {k: _js({'design_id': v['design_id'], 'transforms': v['transforms'], 'cuts': v.get('cuts') or []})
            for k, v in res['variants'].items()}
    assert json.dumps(need) != json.dumps({k: {'design_id': v['design_id'], 'transforms': v['transforms'],
                                               'cuts': v.get('cuts') or []} for k, v in res['variants'].items()})
    builds = []
    real = DS._geometry_of
    monkeypatch.setattr(DS, '_geometry_of', lambda b: builds.append(1) or real(b))
    g = c.post('/api/assembly/geometry', json={'designs': designs, 'variants': need}).get_json()
    assert builds == []                                  # nothing rebuilt
    from layer_assembly.web import _source
    src = _source(designs)
    for k, v in res['variants'].items():
        assert g['designs'][k]['polylines'] == src.geometry(v['design_id'], None, v.get('cuts')).polylines
