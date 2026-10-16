"""
WALL GROUPS × WALL SYSTEMS (2026-10-11; design_proto/wall_groups.py, the Wall
Systems workspace):

  * composition: Wall Group membership / thickness / alignment → the build
    format; independent Wall System membership + construction; mixed
    constructions inside one group; one construction across groups of
    different thickness; precedence (a wall's own system over its group's);
    design scope; the architecture composition ignores every Wall System;
  * migration (project file schema 1 → 2): every resolved legacy document
    reproduced exactly; Save / Open;
  * the Designer and the Assembly stay envelope-only (no Wall System is
    generated, envelopes do not depend on the Wall Systems);
  * explicit generation: NOT GENERATED → the Print Simulation refuses to
    plan; generated → CURRENT, layers inspectable; any Wall System /
    Assembly / Designer change → OUTDATED (plan and inspection refused);
  * geometry and toolpath equivalence with the previous pipeline (legacy
    documents) on a fixture with a plane, an opening and two constructions.
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests')))

import pytest

from walls import compose, system_of, ARCH_TYPE, migrate_parts
from wall_groups import split_designs


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture
def client():
    from app import app
    from layer_assembly import web
    for reg in (web._SOURCES, web._ENV_SOURCES, web._CANON, web._PLANS, web._GENERATED, web._ARCH_LIBS):
        reg.clear()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def post(c, url, body):
    r = c.post(url, data=json.dumps(body), content_type='application/json')
    return r.status_code, r.get_json()


MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


def grouped_doc():
    """A rectangle room (two walls of ONE group would need two paths: a
    rectangle R + a separate line L, both in group G1 at 10 in) and a circle
    C in group G2 at 12 in."""
    return {'id': 'b', 'material': dict(MAT), 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect', 'x': 100, 'y': -40, 'w': 100, 'h': 80, 'closed': True},
        {'id': 'L', 'type': 'LinePath', 'label': 'Line', 'start': [250, -60], 'end': [250, 60]},
        {'id': 'C', 'type': 'CirclePath', 'label': 'Circle', 'cx': 0, 'cy': 0, 'radius': 60, 'closed': True}],
        'wall_groups': [{'id': 'G1', 'name': 'Rooms', 'members': ['R', 'L'], 'thickness': 10.0, 'align': 'auto',
                         'print_reference': False},
                        {'id': 'G2', 'name': 'Tower', 'members': ['C'], 'thickness': 12.0, 'align': 'auto',
                         'print_reference': False}]}


def recs(doc):
    return {r['id']: r for r in doc['wall_systems']}


# ---- composition -----------------------------------------------------------------------

# (2026-10-15, M2: Wall Groups REMOVED — the composition is per wall
# (design_proto/walls.py); a grouped document is only an older INPUT,
# normalised: each member wall receives its group's envelope. Group-level
# assignment no longer exists — a Wall System lists walls.)

def test_grouped_input_is_normalised_to_per_wall_envelopes():
    d = compose(grouped_doc(), 'A', [])
    R = recs(d)
    assert set(R) == {'wall:R', 'wall:L', 'wall:C'} and 'wall_groups' not in d
    assert all(R[f'wall:{w}']['type'] == 'hollow' for w in 'RLC')
    assert R['wall:R']['thickness'] == R['wall:L']['thickness'] == 10.0 and R['wall:C']['thickness'] == 12.0
    assert R['wall:R']['align'] == 'auto'                                 # (the stored value, kept)


def test_wall_system_membership_and_construction():
    S = [{'id': 'S1', 'name': 'Waves', 'type': 'interleaved', 'params': {'paths': 4}, 'web': {}, 'members': ['R', 'L']}]
    R = recs(compose(grouped_doc(), 'A', S))
    assert R['S1']['type'] == 'interleaved' and R['S1']['params'] == {'paths': 4} and R['S1']['name'] == 'Waves'
    assert R['S1']['members'] == ['R', 'L'] and R['S1']['thickness'] == 10.0   # the WALLS' envelope
    assert R['wall:C']['type'] == 'hollow'
    assert system_of('L', S)['id'] == 'S1' and system_of('C', S) is None


def test_one_construction_across_walls_of_different_and_equal_thickness():
    S = [{'id': 'S1', 'name': 'Parallel', 'type': 'parallel', 'params': {'walls': 4}, 'members': ['R', 'L', 'C']}]
    R = recs(compose(grouped_doc(), 'A', S))
    assert R['S1']['members'] == ['R', 'L'] and R['S1~2']['members'] == ['C']     # one record per envelope
    assert {R['S1']['thickness'], R['S1~2']['thickness']} == {10.0, 12.0}
    doc = grouped_doc()
    doc['wall_groups'][1]['thickness'] = 10.0                             # equal envelopes: ONE record, as before
    R2 = recs(compose(doc, 'A', S))
    assert list(R2) == ['S1'] and R2['S1']['members'] == ['R', 'L', 'C']


def test_skin_web_webs_are_composed_into_owned_records():
    S = [{'id': 'S1', 'name': 'Web', 'type': 'skin_web', 'params': {},
          'web': {'pattern': 'wave', 'params': {'spacing': 18}, 'variation_index': 0}, 'members': ['C']}]
    d = compose(grouped_doc(), 'A', S)
    owned = [f for f in d['infills'] if f['owner'] == 'S1']
    assert [f['id'] for f in owned] == ['S1~web~C'] and owned[0]['pattern'] == 'wave' and owned[0]['params'] == {'spacing': 18}
    doc = grouped_doc()
    doc['infills'] = [{'id': 'old', 'path_id': 'C', 'kind': 'wall', 'pattern': 'zigzag', 'params': {'spacing': 20},
                       'owner': 'S1', 'variation_index': 0}]
    d2 = compose(doc, 'A', S)
    assert [f['id'] for f in d2['infills']] == ['old'] and d2['infills'][0]['pattern'] == 'wave'   # id kept (lineage)


def test_construction_never_depends_on_the_layout_state_and_architecture_ignores_systems():
    S = [{'id': 'S1', 'type': 'chain', 'params': {}, 'members': ['R', 'L'], 'designs': ['d1']}]   # (a stale scope)
    assert recs(compose(grouped_doc(), 'base', S))['S1']['type'] == 'chain'
    assert recs(compose(grouped_doc(), 'd1', S))['S1']['type'] == 'chain'
    a1 = compose(grouped_doc(), 'd1', [], arch=True)
    a2 = compose(grouped_doc(), 'd1', S, arch=True)
    assert a1 == a2 and all(r['type'] == ARCH_TYPE and not r['params'] for r in a1['wall_systems'])


def test_legacy_documents_are_untouched():
    from layer_assembly.tests.test_profiles_plane_designer import building
    d = building('interleaved')
    assert compose(d, 'A', [{'id': 'X', 'type': 'chain', 'members': ['C']}]) is d


# ---- migration ----------------------------------------------------------------------------

def _norm(doc):
    """A build document modulo build-record ids / names (labels — renaming a
    record never moves a bead) and the per-wall envelope (folded into the
    records' thickness / align)."""
    d = json.loads(json.dumps(doc))
    d.pop('wall_groups', None)
    for p in d.get('source_paths') or []:
        p.pop('envelope', None)
    ids = {}
    recs_ = []
    for r in d.pop('wall_systems', None) or []:
        r = dict({'align': 'auto', 'print_reference': False}, **r)     # (the reader's defaults)
        if r.get('thickness') is not None:
            r['thickness'] = float(r['thickness'])
        ids[r['id']] = 'R' + ','.join(sorted(r['members']))
        recs_.append({k: v for k, v in r.items() if k not in ('id', 'name') and v not in ({}, None, [])} |
                     {'members': sorted(r['members'])})
    if recs_:
        d['wall_systems'] = sorted(recs_, key=lambda r: json.dumps(r, sort_keys=True))
    inf = []
    for f in d.pop('infills', None) or []:
        f = dict(f)
        if f.get('owner') in ids:
            f['owner'] = ids[f['owner']]
            f['id'] = f'{f["owner"]}~web~{f["path_id"]}' if '~web~' in str(f.get('id')) else f['id']
        inf.append(f)
    if inf:
        d['infills'] = sorted(inf, key=lambda f: json.dumps(f, sort_keys=True))
    return json.dumps(d, sort_keys=True)


def _legacy_fixtures():
    from layer_assembly import bench_mixed as B
    from layer_assembly.tests import test_reference_network_stack as RS
    from layer_assembly.tests.test_profiles_plane_designer import building
    import test_project_file as TP
    out = [('bench', B._fixture()), ('refnet', RS.designs()), ('project_file', TP.designs())]
    for k in ('skin_web', 'interleaved', 'parallel', 'chain'):
        out.append((k, [{'id': 'A', 'name': 'A', 'document': building(k)}]))
    return out


@pytest.mark.parametrize('name,designs', _legacy_fixtures())
def test_migration_reproduces_every_legacy_document(name, designs):
    """legacy (schema 1) → Wall Groups (schema 2) → per wall (schema 7): every
    document builds the same records (modulo record-id labels) — except a
    design whose construction differed per Layer Design (the old `designs`
    scope): construction no longer depends on the layout state (reported)."""
    from layer_design import DesignLibrary
    old = DesignLibrary(designs)
    new, systems = split_designs(designs)
    new, systems, _, rep = migrate_parts(new, systems)
    scoped = any('applied only to' in r for r in rep)
    names = {d['id']: d.get('name') or d['id'] for d in designs}
    lib = DesignLibrary(new, systems)
    same = 0
    for d in old.designs:
        raw = lib.raw_document(d)
        assert 'wall_systems' not in raw and 'wall_groups' not in raw                   # the architecture only
        if _norm(lib.document(d)) == _norm(old.document(d)):
            same += 1
        else:                       # only a REPORTED divergence may change a design
            assert scoped or any(r.startswith(names[d] + ':') for r in rep), (name, d, rep)
    assert same >= 1
    ids = [s['id'] for s in systems]
    assert len(ids) == len(set(ids)) and all('groups' not in s and 'designs' not in s for s in systems)


def test_migration_keeps_ids_and_scopes_only_where_designs_differ():
    from layer_assembly import bench_mixed as B
    new, systems = split_designs(B._fixture())
    by = {s['id']: s for s in systems}
    assert by['WS1']['groups'] == ['WS1'] and by['WS1']['designs'] is None       # old id kept: Assembly references
    assert by['WS2']['designs'] == ['base'] and by['WS2@1']['designs'] == ['d1', 'd2']
    base = next(d for d in new if d['parent'] is None)['document']
    assert {g['id'] for g in base['wall_groups']} == {'WS1', 'WS2', 'WS3', 'WS4'}   # never merged by thickness


def test_project_file_v1_opens_as_schema_2_and_round_trips(client):
    import project_file as PF
    from layer_assembly import bench_mixed as B
    from app import _deserialise_layer
    v1 = {'format': PF.FORMAT, 'schema_version': 1, 'project': {
        'name': 'Old', 'designer': {'designs': B._fixture(), 'active_design': 'base', 'id_counter': 40, 'design_counter': 3},
        'assembly': {'state': B.assembly(12), 'header_counter': 0}}}
    res = PF.load(json.dumps(v1), _deserialise_layer)
    P = res['project']
    assert res['migrated_from'] == 1 and res['schema_version'] == 7 and P['wall_systems']
    assert all('wall_systems' not in (d.get('document') or {}) and 'wall_systems' not in (d.get('patch') or {})
               for d in P['designer']['designs'])
    assert 'wall_groups' not in res['active_document']
    assert any(p.get('envelope') for p in res['active_document']['source_paths'])     # the walls own their envelope
    code, saved = post(client, '/api/project/save', {'project': P})
    assert code == 200 and saved['file']['schema_version'] == 7 and saved['file']['project']['wall_systems'] == P['wall_systems']
    again = client.post('/api/project/open', data=json.dumps(saved['file']).encode()).get_json()
    assert again['project']['wall_systems'] == P['wall_systems'] and again['project']['designer'] == P['designer']


# ---- the Designer / Assembly stay envelope-only ----------------------------------------------

def test_designer_resolves_envelopes_only(client, monkeypatch):
    import model
    monkeypatch.setattr(model.PrintLayer, '_apply_wall_systems',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('Wall Systems generated in the Designer')))
    doc = dict(grouped_doc(), envelope=True)
    code, r = post(client, '/api/effective_paths', doc)
    assert code == 200 and r['printable'] == [] and r['envelope']
    assert {p['role'] for p in r['envelope']} <= {'ring', 'bead'}
    assert {t for p in r['envelope'] for t in p['src'] if t} == {'R', 'L', 'C'}


def test_assembly_envelopes_do_not_depend_on_the_wall_systems(client):
    from layer_assembly import web
    ds = [{'id': 'A', 'name': 'A', 'document': grouped_doc()}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 6}], 'max_overhang': 1.5,
           'transform': {'profile': 'linear', 'shift': [0.25, 0], 'scale_in': 0}}
    S1 = [{'id': 'S1', 'type': 'interleaved', 'params': {'paths': 4}, 'members': ['R', 'L']}]
    S2 = [{'id': 'S1', 'type': 'chain', 'params': {}, 'members': ['R', 'L']}]
    _, a = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'wall_systems': S1, 'envelope': True})
    _, b = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': ds, 'wall_systems': S2, 'envelope': True})
    _, ga = post(client, '/api/assembly/geometry', {'designs': ds, 'ids': ['A'], 'envelope': True, 'wall_systems': S1})
    _, gb = post(client, '/api/assembly/geometry', {'designs': ds, 'ids': ['A'], 'envelope': True, 'wall_systems': S2})
    assert ga['designs']['A']['polylines'] == gb['designs']['A']['polylines']
    assert [i['geometry_key'] for i in a['instances']] == [i['geometry_key'] for i in b['instances']]
    for src in web._ENV_SOURCES.values():
        assert not src._variants and not src.lib._tbuilds                # no full layer was built


# ---- explicit generation + invalidation ---------------------------------------------------

def _gen_body(**kw):
    ds = [{'id': 'A', 'name': 'A', 'document': grouped_doc()}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 6}], 'max_overhang': 1.5,
           'transform': {'profile': 'linear', 'shift': [0.25, 0], 'scale_in': 0}}
    S = [{'id': 'S1', 'name': 'Waves', 'type': 'interleaved', 'params': {'paths': 4}, 'web': {},
          'members': ['R', 'L']}]
    return dict({'assembly': asm, 'designs': ds, 'wall_systems': S}, **kw)


def test_generation_status_and_invalidation(client):
    B = _gen_body()
    code, r = post(client, '/api/assembly/print_plan', dict(B, require_generated=True))
    assert code == 409 and r['outdated']                                  # NOT GENERATED: never planned here
    code, L = post(client, '/api/wallsystems/layer', dict(B, index=0))
    assert code == 409
    code, g = post(client, '/api/wallsystems/generate', dict(B, job='j1'))
    assert code == 200 and len(g['layers']) == 4 and g['support']['level'] == 'bead'
    assert {s['type'] for s in g['systems'].values()} == {'interleaved', 'hollow'}
    code, L = post(client, '/api/wallsystems/layer', dict(B, index=2))
    assert code == 200 and L['polylines'] and {p.get('system') for p in L['polylines']} >= {'S1'}
    code, p = post(client, '/api/assembly/print_plan', dict(B, require_generated=True, detail='summary'))
    assert code == 200 and p['plan']['layers']
    # speeds / lift settings are not geometry: still current
    B2 = copy.deepcopy(B)
    B2['assembly']['print_speeds'] = {'print': 3.0}
    B2['assembly']['machine'] = {'max_z_travel': 18, 'lift_height': 12, 'head_clearance': 1}
    assert post(client, '/api/assembly/print_plan', dict(B2, require_generated=True))[0] == 200
    # a Wall System, an Assembly and a Designer change: OUTDATED
    for change in ('system', 'assembly', 'designer'):
        X = copy.deepcopy(B)
        if change == 'system':
            X['wall_systems'][0]['params']['paths'] = 6
        elif change == 'assembly':
            X['assembly']['layer_height'] = 1.0
        else:
            X['designs'][0]['document']['wall_groups'][0]['thickness'] = 11.0
        assert post(client, '/api/assembly/print_plan', dict(X, require_generated=True))[0] == 409, change
        assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409, change


def test_generation_is_cancellable(client):
    from layer_assembly import web
    B = _gen_body()
    web._JOBS['latest'] = None
    # a job that is no longer the latest gives way before building
    import threading
    orig = web._resolve_stack

    def resolve_then_replace(data, *a, **k):
        st = orig(data, *a, **k)
        web._JOBS['latest'] = 'newer'
        return st
    web._resolve_stack = resolve_then_replace
    try:
        code, r = post(client, '/api/wallsystems/generate', dict(B, job='old'))
    finally:
        web._resolve_stack = orig
    assert code == 409 and r['cancelled'] and not web._GENERATED


# ---- equivalence with the previous pipeline -------------------------------------------------

def test_generated_geometry_and_toolpaths_equal_the_previous_pipeline(client):
    from layer_assembly.tests.test_profiles_plane_designer import building
    legacy = [{'id': 'A', 'name': 'A', 'document': building('interleaved')}]
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 15}], 'max_overhang': 1.5,
           'transform': {'profile': 'linear', 'shift': [0.125, 0], 'scale_in': 0},
           'profiles': [{'kind': 'plane', 'id': 'p1', 'sources': [], 'systems': ['WS1', 'WS2'], 'exclude': [],
                         'direction': 0.0, 'slope': 0.5, 'anchor': [50.0, 0.0], 'z_anchor': 9.0, 'label': ''},
                        {'kind': 'opening', 'id': 'p2', 'wall': {'source': 'R', 'edge': 0}, 'position': 50.0,
                         'z_bottom': 3.0, 'height': 6.0, 'width': 10.0, 'upper': 'pointed', 'lower': 'pointed',
                         'bottom_depth': 3.0, 'upper_height': 3.0, 'middle': False, 'middle_height': None,
                         'label': ''}], 'profile_counter': 2}
    new, systems = split_designs(legacy)
    new, systems, asm_new, _ = migrate_parts(new, systems, asm)       # (schema 7: per wall, planes on walls)
    # the previous pipeline: full resolve + variants + plan of the LEGACY documents
    _, r0 = post(client, '/api/assembly/resolve', {'assembly': asm, 'designs': legacy})
    need = {k: {'design_id': v['design_id'], 'transforms': v['transforms'], 'cuts': v.get('cuts') or []}
            for k, v in r0['variants'].items()}
    _, g0 = post(client, '/api/assembly/geometry', {'designs': legacy, 'variants': need})
    _, p0 = post(client, '/api/assembly/print_plan', {'assembly': asm, 'designs': legacy, 'detail': 'stream'})
    # the new model: Wall Groups + project Wall Systems, generated, then planned from it
    B = {'assembly': asm_new, 'designs': new, 'wall_systems': systems}
    code, g = post(client, '/api/wallsystems/generate', B)
    assert code == 200
    _, r1 = post(client, '/api/assembly/resolve', B)
    # (one intended difference, 2026-10-11: the Assembly validates on the
    # ARCHITECTURE — its wall targets carry no construction minimum; the
    # legacy document's construction still does)
    def arch(P):
        return [dict(p, wall=dict(p['wall'], min_length=None)) if p.get('wall') else p for p in P]
    assert all(p['wall']['min_length'] == 0.0 for p in r1['profiles'] if p.get('wall'))
    assert arch(r1['profiles']) == arch(r0['profiles']) and r1['support'] == r0['support']
    for L in g['layers']:
        code, lay = post(client, '/api/wallsystems/layer', dict(B, index=L['index']))
        # (the legacy variant of the SAME layer: matched by index — a migrated
        # planar cut selects its walls as explicit `sources`, so its key spells
        # the same selection differently)
        k0 = next(i['geometry_key'] for i in r0['instances'] if i['index'] == L['index'])
        ref = g0['designs'][k0]['polylines'] if k0 in g0['designs'] else None
        if ref is None:
            _, c0 = post(client, '/api/assembly/geometry', {'designs': legacy, 'ids': [L['design_id']]})
            ref = c0['designs'][L['design_id']]['polylines']
        assert [(p['pts'], p['closed']) for p in lay['polylines']] == [(p['pts'], p.get('closed', False)) for p in ref]
    _, p1 = post(client, '/api/assembly/print_plan', dict(B, detail='stream', require_generated=True))
    assert json.dumps(p1['stream'], sort_keys=True) == json.dumps(p0['stream'], sort_keys=True)
    assert p1['support'] == p0['support'] and p1['estimate'] == p0['estimate']
