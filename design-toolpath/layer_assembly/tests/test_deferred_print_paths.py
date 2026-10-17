"""
DEFERRED PRINT PATHS (2026-10-11; design-toolpath/DEFERRED_TOOLPATH_ARCHITECTURE.md
M0 + M1): Assembly editing on ARCHITECTURAL ENVELOPES, print preparation
(the existing full pipeline) only on request.

  * M0 — the envelope is the Designer's own build stopped after the wall
    networks: its rings are EXACTLY the material rings a full build hands to
    Wall Systems / the lattice (reference network with transform groups, the
    mixed-feature benchmark building with openings / arrays / a plane, the
    plane fixture of every Wall System family), with source attribution,
    walls, Wall System assignments, openings, centrelines; no Wall System /
    lattice / routing code runs; lattice-only cut ops change nothing.
  * M1 — an envelope resolve places every layer exactly as the print resolve
    does (same instances, transforms, groups, profiles), builds no full
    variant, neither waits for nor pre-empts print preparation; the
    PRELIMINARY envelope-level support check (lean, closing opening →
    header, headers, single beads); print preparation (/print_plan) returns
    the AUTHORITATIVE bead-level support report and is unchanged by any
    envelope editing before it; Save / Open keeps envelope editing identical.
"""
import copy
import json
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto')))

import pytest

from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, resolve


# (written for the 1.5 in default Maximum Overhang — since 2026-10-11 the
# default is 1 in: these cases state the 1.5 in they were designed with)
_Assembly = Assembly


def Assembly(*a, **k):          # noqa: F811
    if len(a) < 9:
        k.setdefault('max_overhang', 1.5)
    return _Assembly(*a, **k)


Assembly.from_dict = _Assembly.from_dict
from layer_assembly.objects import Header
from layer_assembly.support import analyse_support

BEAD = 3.0


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture
def client():
    from app import app
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS):
        reg.clear()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def post(c, url, body):
    r = c.post(url, data=json.dumps(body), content_type='application/json')
    return r.get_json()


def rings_of(built):
    return [[[[p.x, p.y] for p in r] for r in comp] for comp in built.envelope_rings]


def distinct_requests(designs, asm):
    """The (design, transforms, cuts) of every layer, distinct, as the print resolve makes them."""
    from layer_assembly import web
    from layer_assembly.source import variant_request
    st = web._resolve_stack({'assembly': asm, 'designs': designs})
    out, seen = [], set()
    for i in st['res'].instances:
        r = variant_request(i)
        k = json.dumps(r, sort_keys=True, default=str)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _seg_d(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _boundary_d(p, rings):
    """Distance from p to the nearest ring boundary."""
    return min(_seg_d(p, r[i], r[(i + 1) % len(r)]) for comp in rings for r in comp for i in range(len(r)))


def assert_envelope_holds_the_print(env, full, bead, where):
    """PHYSICAL WALL WIDTH (2026-10-16): the ARCHITECTURAL envelope is the
    finished deposited width and never moves; a SKIN construction (Hollow,
    Skin + Web) prints its skins half a bead inside it, so the full build's
    material regions are the envelope's rings INSET by ½ bead — the same
    components, every print-ring vertex within √2 · ½ bead of the envelope
    boundary (a convex corner of an inset ring) and never the other way
    round. Generated constructions keep centrelines inside the faces and
    their regions equal the envelope exactly.
    JUNCTION ZONES (within 2.5 × the thickest wall of a network junction)
    are excluded: there the print build's regions come from the member walls'
    inset bands meeting in their own corners, fillets and mouths — up to a
    wall thickness off a pure inset (measured ≤ 1.93 T from the junction on
    the untransformed reference network, a little more on its transformed
    'gaps' variants). Not derivable from the envelope until the
    canonical wall / junction motifs (WALL_ARCHITECTURE.md Z6 / Z7). The
    region TOPOLOGY may differ there too (the lens between a circle and a
    rectangle with rounded junctions is one component in one build and two
    in the other), so components are not counted — a component missing
    outside every junction zone fails the vertex checks."""
    rings = rings_of(full)
    if env.rings == rings:
        return
    # (+0.2: the arcs of rounded corners are sampled polylines — chord sag
    # between the two rings' samples)
    tol = 0.5 * bead * math.sqrt(2) + 0.2
    T = max((float((w or {}).get('thickness') or 0.0) for w in env.walls.values()), default=10.0) or 10.0
    js = [(j['x'], j['y']) for j in env.junctions or []]

    def in_junction_zone(q):
        return any(math.dist(q, j) <= 2.5 * T for j in js)
    checked = 0
    for A, B in ((rings, env.rings), (env.rings, rings)):
        for comp in A:
            for r in comp:
                for q in r:
                    if in_junction_zone(q):
                        continue
                    checked += 1
                    d = _boundary_d(q, B)
                    assert d <= tol, (where, q, d)
    assert checked, where


def assert_envelopes_equal(designs, reqs, skin=None):
    """Every request's envelope is the full build's architectural stage:
    equal rings (generated constructions) — or, where a SKIN construction
    insets its printed skins, the rings the print build insets by half a bead
    (assert_envelope_holds_the_print)."""
    from layer_design import DesignLibrary
    lib = DesignLibrary(designs)
    for d, tr, cu in reqs:
        env = lib.envelope(d, tr or None, cu or None)
        full = lib.build(d, transforms=tr or None, cuts=cu or None)
        if skin is False:
            assert env.rings == rings_of(full), (d, tr, cu)
        else:
            assert_envelope_holds_the_print(env, full, env.bead_width or 3.0, (d, tr, cu))
        assert env.rings or env.strands


# ---- M0: the envelope IS the full build's architectural stage ------------------------

def test_envelope_rings_equal_the_full_build_reference_network_with_groups():
    from layer_assembly.tests import test_reference_network_stack as RS
    reqs = distinct_requests(RS.designs(), RS.assembly(layers=8))
    assert any(r[1] for r in reqs)                                       # transform-group variants
    assert {r[0] for r in reqs} == {'base', 'gaps'}                      # a derived design (with an opening)
    assert_envelopes_equal(RS.designs(), reqs[:4] + [r for r in reqs if r[0] == 'gaps'][:2])


def _bench():
    from layer_assembly import bench_mixed as B
    return B._fixture(), B.assembly(84)


def test_envelope_rings_equal_the_full_build_mixed_benchmark():
    """Openings (pointed / Gothic + middle), a staggered + a horizontal array,
    a planar cut, Designer opening alignment, stations — one layer of each."""
    ds, asm = _bench()
    reqs = distinct_requests(ds, asm)
    pick, kinds = [], set()
    for r in reqs:
        ops = frozenset(c.get('op') for c in r[2] or [])
        k = (r[0], ops)
        if k not in kinds:
            kinds.add(k)
            pick.append(r)
    assert {'opening', 'plane', 'station'} <= {op for _, ops in kinds for op in ops}
    assert_envelopes_equal(ds, pick[:7])


@pytest.mark.parametrize('kind', ['hollow', 'skin_web', 'interleaved', 'parallel'])
def test_envelope_rings_equal_the_full_build_plane_and_opening_fixture(kind):
    from layer_assembly.tests.test_profiles_plane_designer import building
    ds = [{'id': 'A', 'name': 'A', 'document': building(kind)}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 24}], 'max_overhang': 1.5,
           'profiles': [{'kind': 'plane', 'id': 'p1', 'sources': [], 'systems': ['WS1', 'WS2'], 'exclude': [],
                         'direction': 0.0, 'slope': 0.5, 'anchor': [50.0, 0.0], 'z_anchor': 12.0, 'label': ''},
                        {'kind': 'opening', 'id': 'p2', 'wall': {'source': 'R', 'edge': 0}, 'position': 50.0,
                         'z_bottom': 3.0, 'height': 12.0, 'width': 10.0, 'upper': 'pointed', 'lower': 'pointed',
                         'bottom_depth': 6.0, 'upper_height': 6.0, 'middle': False, 'middle_height': None,
                         'label': ''}], 'profile_counter': 2}
    reqs = distinct_requests(ds, asm)
    cut = [r for r in reqs if any(c.get('op') in ('plane', 'opening') for c in r[2] or [])]
    assert cut
    # (Skin + Web: the print build's regions are the envelope inset ½ bead —
    # physical wall width; Hollow has no rings, generated kinds equal exactly)
    assert_envelopes_equal(ds, [reqs[0]] + cut[::max(1, len(cut) // 4)][:4], skin=(kind == 'skin_web' or None))


def test_lattice_only_cut_ops_do_not_change_the_envelope():
    from layer_design import DesignLibrary
    ds, asm = _bench()
    lib = DesignLibrary(ds)
    st = [r for r in distinct_requests(ds, asm) if any(c.get('op') == 'station' for c in r[2] or [])]
    d, tr, cu = st[0]
    arch = [c for c in cu if c.get('op') not in ('station', 'transition')]
    e1 = lib.envelope(d, tr or None, cu)
    assert e1 is lib.envelope(d, tr or None, arch or None)               # the same cache entry
    fresh = DesignLibrary(ds).envelope(d, tr or None, arch or None)
    assert fresh.rings == e1.rings


def test_the_envelope_runs_no_wall_system_lattice_or_routing(monkeypatch):
    import model
    import wall_lattice
    import layer_route
    import wall_systems
    from layer_design import DesignLibrary

    def boom(*a, **k):
        raise AssertionError('print-path generation during an envelope build')
    for mod, name in ((model.PrintLayer, '_apply_wall_systems'), (wall_lattice, 'plan'),
                      (wall_systems, 'generate'), (layer_route, 'describe'), (layer_route, 'route')):
        monkeypatch.setattr(mod, name, boom)
    ds, _ = _bench()
    lib = DesignLibrary(ds)
    import profile_cuts as PC
    walls = PC.straight_walls(lib.document('base'))
    w = next(x for x in walls if x['source'] == 'R1')
    cut = [{'op': 'opening', 'source': 'R1', 'edge': w['edge'], 'at': w['length'] / 2, 'width': 14.0, 'profile': 'p'}]
    for d, c_ in (('base', None), ('d1', None), ('base', cut)):
        e = lib.envelope(d, None, c_)
        assert e.rings
    assert not lib._tbuilds and not [k for k in lib._cache if k and k[0] == 'build']


def test_envelope_contents_attribution_walls_systems_openings():
    from layer_design import DesignLibrary
    ds, _ = _bench()
    lib = DesignLibrary(ds)
    doc = lib.document('base')
    srcs = {p['id'] for p in doc['source_paths']}
    import profile_cuts as PC
    w = next(x for x in PC.straight_walls(doc) if x['source'] == 'R1')
    e = lib.envelope('base', None, [{'op': 'opening', 'source': 'R1', 'edge': w['edge'], 'at': w['length'] / 2,
                                     'width': 14.0, 'profile': 'p9'}])
    tags = [t for comp in e.ring_sources for r in comp for t in r]
    assert tags and all(t in srcs for t in tags)                         # every ring vertex attributed
    assert all(len(r) == len(t) for comp, ts in zip(e.rings, e.ring_sources) for r, t in zip(comp, ts))
    assert {w_['id'] for w_ in e.wall_systems} == {w_['id'] for w_ in doc['wall_systems']}
    assert {m['id'] for m in e.membership.values()} <= {w_['id'] for w_ in doc['wall_systems']}
    assert any(o['id'] == 'asm-p9' for o in e.openings)                  # the Assembly opening, resolved
    assert set(e.centrelines) <= srcs and 'R1' in e.centrelines
    assert e.walls['R1']['thickness'] > 0 and e.bead_width == doc['material']['bead_width']
    polys = e.polylines()
    assert {p['role'] for p in polys} <= {'ring', 'bead'} and all(len(p['src']) == len(p['pts']) for p in polys)
    json.dumps(e.to_dict())                                              # plain data


def test_plain_and_hollow_walls_without_rings_are_their_face_beads():
    from layer_design import DesignLibrary

    def doc():
        return {'id': 'd', 'source_paths': [
            {'id': 'W', 'type': 'RectanglePath', 'x': 100, 'y': 140, 'w': 200, 'h': 120, 'closed': True,
             'envelope': {'thickness': 12, 'align': 'auto', 'print_reference': False}},
            {'id': 'L', 'type': 'LinePath', 'start': [100, 330], 'end': [300, 330], 'closed': False,
             'envelope': {'thickness': 10, 'align': 'center', 'print_reference': False}}],
            'infills': [], 'openings': [],
            'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}}
    for systems in ([], [{'id': 'S', 'type': 'hollow', 'members': ['W'], 'params': {}, 'web': {}}]):
        lib = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': doc()}], systems)
        e = lib.envelope('base')
        full = lib.build('base')
        assert e.rings == rings_of(full) == []
        # the envelope's strands are the ARCHITECTURAL faces (finished width:
        # the authored reference `W` + its offset `W.wall`, the line's two
        # faces + caps); the print puts every skin half a bead inside them
        # (`W.wall_near` / `W.wall`, physical wall width 2026-10-16) — same
        # sources, every printed vertex within ½ bead of an envelope face
        assert e.strands and {s['src'][0] for s in e.strands} == {'W', 'L'}
        env_by_src = {}
        for s in e.strands:
            env_by_src.setdefault(s['src'][0], []).append(s['pts'])
        printed = [(full.strand_sources.get(p['id']), p) for p in full.printable]
        assert {src for src, _ in printed} == {'W', 'L'}
        for src, p in printed:
            segs = [(a, b) for pts in env_by_src[src] for a, b in zip(pts, pts[1:])]
            for q in p['pts']:                            # (√2 · ½ bead at a convex corner)
                assert min(_seg_d(q, a, b) for a, b in segs) <= 1.5 * math.sqrt(2) + 0.05, (src, p['id'], q)


# ---- M1: Assembly editing on envelopes ---------------------------------------------------

def test_envelope_resolve_places_every_layer_as_the_print_resolve(client):
    from layer_assembly.tests import test_reference_network_stack as RS
    ds, asm = RS.designs(), RS.assembly(layers=8)
    env = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'envelope': True})
    prn = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds})
    assert env['geometry'] == 'envelope' and env['support']['level'] == 'envelope'
    assert prn['support']['level'] == 'bead' and 'geometry' not in prn
    strip = lambda r: [{k: v for k, v in i.items() if k not in ('geometry_key', 'print_key')} for i in r['instances']]
    assert strip(env) == strip(prn)                                      # Z, transforms, group parts
    assert [i['print_key'] for i in env['instances']] == [i['geometry_key'] for i in prn['instances']]
    for k in ('sections', 'groups', 'profiles', 'profile_targets', 'total_layers', 'warnings'):
        assert env.get(k) == prn.get(k), k
    # envelope variants: fetched with their spec, = the editing adapter's envelopes
    from layer_assembly import web
    need = {k: {'design_id': v['design_id'], 'transforms': v['transforms'], 'cuts': v.get('cuts') or []}
            for k, v in env['variants'].items()}
    assert need and all(v['envelope'] for v in env['variants'].values())
    g = post(client, '/api/assembly/geometry', {'designs': ds, 'variants': need, 'envelope': True})['designs']
    src = web._source(ds, envelope=True)
    for k, v in need.items():
        assert g[k]['envelope'] and g[k]['polylines'] == src.envelope(
            v['design_id'], {s: tuple(t) for s, t in v['transforms'].items()} or None, v['cuts'] or None).polylines
    # the editing adapter built NO full variant (only the canonical designs: footprints / groups)
    assert not src._variants and not src.lib._tbuilds


def test_envelope_editing_builds_no_full_variant_on_profile_edits(client):
    from layer_assembly.tests.test_profiles_plane_designer import building
    from layer_assembly import web
    ds = [{'id': 'A', 'name': 'A', 'document': building('skin_web')}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 24}], 'max_overhang': 1.5,
           'profiles': [{'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R', 'edge': 0}, 'position': 50.0,
                         'z_bottom': 3.0, 'height': 12.0, 'width': 10.0, 'upper': 'pointed', 'lower': 'pointed',
                         'bottom_depth': 6.0, 'upper_height': 6.0, 'middle': False, 'middle_height': None,
                         'label': ''}], 'profile_counter': 1}
    r1 = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'envelope': True, 'debug_timing': True})
    asm2 = copy.deepcopy(asm)
    asm2['profiles'][0]['position'] = 60.0                              # an opening MOVE (stations change everywhere)
    r2 = post(client, '/api/assembly/resolve', {'assembly': asm2, 'designs': ds, 'envelope': True, 'debug_timing': True})
    assert r1['profiles'][0]['valid'] and r2['profiles'][0]['valid']
    # only the layers the opening cuts get new envelopes (stations change no architecture)
    cut = len(r2['profiles'][0]['layers'])
    assert 0 < r2['timing']['envelopes_new'] <= cut
    src = web._source(ds, envelope=True)
    assert not src._variants and not src.lib._tbuilds


def test_envelope_requests_neither_wait_for_nor_preempt_print_preparation(client):
    from layer_assembly import web
    from layer_assembly.tests import test_reference_network_stack as RS
    ds, asm = RS.designs(), RS.assembly(layers=4)
    post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'envelope': True})     # (warm)
    held, release = threading.Event(), threading.Event()

    def hold():                                  # a print preparation holding the stack lock
        with web._STACK_LOCK:
            held.set()
            release.wait(30)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    held.wait(5)
    try:
        e0 = web._JOBS['epoch']
        t0 = time.perf_counter()
        r = post(client, '/api/assembly/resolve', {'assembly': dict(asm, max_overhang=1.25), 'designs': ds,
                                                   'envelope': True})
        assert r['support']['level'] == 'envelope' and time.perf_counter() - t0 < 20
        post(client, '/api/assembly/geometry', {'designs': ds, 'ids': ['base'], 'envelope': True})
        assert web._JOBS['epoch'] == e0                                  # no background plan is cancelled
    finally:
        release.set()
        t.join(5)
    post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds})
    assert web._JOBS['epoch'] == e0 + 1                                  # (a full-geometry request still pre-empts)


def test_print_preparation_returns_the_bead_level_support_and_is_unchanged_by_editing(client):
    from layer_assembly.tests import test_reference_network_stack as RS
    ds, asm = RS.designs(), RS.assembly(layers=6)
    body = {'assembly': asm, 'designs': ds, 'detail': 'stream'}
    p1 = post(client, '/api/assembly/print_plan', body)
    prn = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds})
    assert p1['support']['level'] == 'bead' and p1['support'] == prn['support']
    # a fresh server: envelope EDITING first (the design set's shared canonical store
    # registered; the envelopes themselves come from the architecture library, so no
    # canonical build is needed for editing), then print preparation
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS):
        reg.clear()
    post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'envelope': True})
    assert web._CANON
    p2 = post(client, '/api/assembly/print_plan', body)
    assert json.dumps(p2, sort_keys=True) == json.dumps(p1, sort_keys=True)   # byte-identical plan + support


def test_project_save_open_keeps_envelope_editing_identical(client):
    from layer_assembly import bench_mixed as B
    ds = B._fixture()
    asm = B.assembly(16)
    proj = {'name': 'Deferred', 'designer': {'designs': ds, 'active_design': 'base', 'id_counter': 40,
                                             'design_counter': 3},
            'assembly': {'state': asm, 'header_counter': 0}}
    f = post(client, '/api/project/save', {'project': proj})['file']
    text = json.dumps(f)
    assert '"ring_sources"' not in text and '"rings"' not in text and '"strands"' not in text   # authoring state only (per-wall `envelope` fields are authored)
    opened = client.post('/api/project/open', data=text.encode()).get_json()
    p2 = opened['project'] if 'project' in opened else opened
    ds2, asm2 = p2['designer']['designs'], p2['assembly']['state']
    # (2026-10-15, schema 7: the fixture's Variants stored persistent wall
    # properties — dropped on save and REPORTED; the reference is that same
    # project as the migration leaves it)
    from walls import migrate_parts
    ds, _, asm, rep = migrate_parts(ds, [], asm)
    assert rep and any('dropped' in r for r in rep)      # (+ the in-document records converted)
    r1 = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'envelope': True})
    r2 = post(client, '/api/assembly/resolve', {'assembly': asm2, 'designs': ds2, 'envelope': True})
    assert r1['instances'] == r2['instances'] and r1['support'] == r2['support'] and r1['profiles'] == r2['profiles']


# ---- the PRELIMINARY architectural support check (pure) ---------------------------------

def ring_wall(x0, y0, x1, y1, t=10.0, gap=None):
    """A rectangular wall of thickness t as its material rings (outer + inner);
    `gap` (a0, a1): an opening through the y = y0 side between those x."""
    if gap is None:
        return [{'pts': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], 'closed': True, 'role': 'ring'},
                {'pts': [[x0 + t, y0 + t], [x1 - t, y0 + t], [x1 - t, y1 - t], [x0 + t, y1 - t]], 'closed': True,
                 'role': 'ring'}]
    a0, a1 = gap                                   # one ring: the wall cut open by the opening
    return [{'pts': [[a1, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0], [a0, y0], [a0, y0 + t],
                     [x0 + t, y0 + t], [x0 + t, y1 - t], [x1 - t, y1 - t], [x1 - t, y0 + t], [a1, y0 + t]],
             'closed': True, 'role': 'ring'}]


FP = {k: Footprint.of_bbox(0, 0, 100, 100) for k in ('A', 'G', 'S')}


def env_run(asm, geo):
    return analyse_support(resolve(asm, footprints=FP), geo, asm, level='envelope')


def test_architectural_check_walls_that_stack_are_supported():
    geo = {'A': (ring_wall(0, 0, 100, 100), BEAD)}
    rep = env_run(Assembly(1.5, [Section('A', 30)]), geo)
    assert rep.findings == [] and rep.level == 'envelope' and rep.to_dict()['level'] == 'envelope'


def test_architectural_check_lean_beyond_the_maximum_overhang():
    geo = {'A': (ring_wall(0, 0, 100, 100), BEAD)}
    ok = Assembly(1.5, [Section('A', 6), Section('A', 6, transform=SectionTransform('linear', (1.25, 0), 0))],
                  max_overhang=1.5)
    assert env_run(ok, geo).findings == []                                # 1.25 in / layer ≤ 1.5
    bad = Assembly(1.5, [Section('A', 6), Section('A', 6, transform=SectionTransform('linear', (2.0, 0), 0))],
                   max_overhang=1.5)
    f = env_run(bad, geo).findings
    assert f and all(x.kind == 'overlap' and x.status == 'insufficient_support' for x in f)
    assert all(abs(x.overlap - (BEAD - 2.0)) < 1e-6 for x in f)          # one bead at that edge: b − δ


def test_architectural_check_opening_closing_above_needs_a_header_and_a_header_supports_it():
    geo = {'A': (ring_wall(0, 0, 100, 100), BEAD), 'G': (ring_wall(0, 0, 100, 100, gap=(30, 70)), BEAD)}
    asm = Assembly(1.5, [Section('A', 6), Section('G', 12), Section('A', 6)])
    rep = env_run(asm, geo)
    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert f.kind == 'bridge' and f.status == 'needs_header' and f.layer == 12
    # the opening's clear width less what each jamb may overhang (h = 1.5 in)
    assert 40 - 2 * 1.5 - BEAD <= f.span <= 40
    asm.objects = [Header('h1', 2, 0, f.local_centre[0], f.local_centre[1], f.angle, 40, 8, 10, 6, snap=True)]
    rep2 = env_run(asm, geo)
    assert [x.status for x in rep2.findings] == ['supported']
    # a self-supporting narrowing (≤ h per layer) is NOT a finding
    steps = {f'S{k}': (ring_wall(0, 0, 100, 100, gap=(30 + 1.25 * k, 70 - 1.25 * k)), BEAD) for k in range(12)}
    fps = {k: Footprint.of_bbox(0, 0, 100, 100) for k in steps}
    sasm = Assembly(1.5, [Section(f'S{k}', 1.5) for k in range(12)])
    assert analyse_support(resolve(sasm, footprints=fps), steps, sasm, level='envelope').findings == []


def test_architectural_check_single_beads_follow_the_bead_rule():
    line = lambda dx: [{'pts': [[10 + dx, 50], [90 + dx, 50]], 'closed': False, 'role': 'bead'}]
    for d, bad in ((1.0, False), (1.5, False), (2.0, True)):
        geo = {'A': (line(0), BEAD)}
        asm = Assembly(1.5, [Section('A', 1.5), Section('A', 3.0, transform=SectionTransform('linear', (0, d), 0))],
                       max_overhang=1.5)
        fs = env_run(asm, geo).findings
        assert bool(fs) == bad, d                                        # centre offset ≤ h: supported


def test_the_bead_level_check_is_unchanged_by_the_envelope_option():
    """Default level: the authoritative bead-level report, exactly as before."""
    from layer_assembly.tests.test_support import GEO, FP as FP2, door_stack
    asm = door_stack()
    a = analyse_support(resolve(asm, footprints=FP2), GEO, asm)
    b = analyse_support(resolve(asm, footprints=FP2), GEO, asm, level='bead')
    assert a.level == 'bead' and a.to_dict() == b.to_dict() and len(a.findings) == 1
