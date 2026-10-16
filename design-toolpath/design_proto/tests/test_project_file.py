"""PROJECT FILE — whole-project Save / Open (project_file.py, /api/project/*).

- a NONTRIVIAL project (Base with walls of several Wall Systems, an inset
  whose parent is gone, openings, a trim, a route origin, junction / cap
  settings; two derived designs — a chain of derivation —; an Assembly with
  three sections, section + assembly-wide transforms, transform groups,
  headers, an ignored finding and opening / planar profiles) survives
  save → open → save exactly (authoring state, documents, Assembly);
- only authoring state is in the file (no resolved geometry, routes,
  stacks, support results, histories);
- id counters never fall below what the content uses;
- the MIGRATION mechanism (step registry, version stamping, chained steps,
  a missing step), NEWER files refused, malformed files refused with a clear
  message — and nothing loaded.
"""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import app as A                                   # noqa: E402
import project_file as PF                         # noqa: E402
import reference_network as RN                    # noqa: E402
from layer_design import DesignLibrary            # noqa: E402
from layer_assembly.model import Assembly         # noqa: E402


def base_document():
    d = RN.document('zigzag', openings=('rect1_top',))
    d['infills'] = []
    d['source_paths'] += [
        # an inset whose parent was deleted: its frozen points ARE its shape
        {'id': 'p7', 'type': 'InsetPath', 'label': 'Inset 1', 'parent_id': 'gone', 'distance': 6.0,
         'mode': 'inset', 'points': [[400, 400], [460, 400], [460, 440], [400, 440]]},
        {'id': 'p8', 'type': 'ExplicitPath', 'label': 'Path 1', 'closed': False,
         'points': [[20, 420], [120, 420], [120, 480]]},
    ]
    d['wall_systems'] = [
        {'id': 'WS1', 'name': 'Rooms', 'type': 'skin_web', 'thickness': 10, 'align': 'auto',
         'print_reference': False, 'params': {}, 'members': ['R1', 'R2', 'C1'],
         'web': {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}},
        {'id': 'WS2', 'name': 'Link', 'type': 'chain', 'thickness': 10, 'align': 'auto',
         'print_reference': False, 'params': {'pitch': 9.0, 'neck': 0.2}, 'members': ['L1'], 'web': {}},
        {'id': 'WS3', 'name': 'Garden', 'type': 'parallel', 'thickness': 12, 'align': 'auto',
         'print_reference': False, 'params': {'walls': 4}, 'members': ['p8'], 'web': {}}]
    d['infills'] = [{'id': 'WS1~web~R1', 'path_id': 'R1', 'kind': 'wall', 'owner': 'WS1', 'pattern': 'zigzag',
                     'params': {'spacing': 20}, 'variation_index': 0}]
    d['trims'] = [{'id': 'p9', 'source_path_id': 'R2', 'start': [160.0, 260.0], 'end': [160.0, 300.0],
                   'inside': {'C1': False}}]
    d['route_origins'] = [{'strand': 'R1~ws0.0', 'u': 0.25, 'pos': [180.0, 100.0]}]
    d['junction_style'], d['junction_radius'] = 'round', 6
    d['cap_style'], d['cap_corner_radius'] = 'rounded', 2
    d['material'] = {'bead_width': 3.5, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
    d['constraints'] = {'start_path_id': None, 'start_t': None, 'reverse_direction': True, 'component_order': None}
    return d


def designs():
    return [
        {'id': 'base', 'name': 'Base', 'parent': None, 'document': base_document()},
        {'id': 'd1', 'name': 'Door level', 'parent': 'base',
         'patch': {'openings': {'o12': {'source_path_id': 'R2', 'center_s': 60.0, 'width': 30.0}},
                   'wall_systems': {'WS2': {'params': {'pitch': 6.0}}}},
         'settings': {'junction_style': 'miter'}},
        {'id': 'd2', 'name': 'Upper', 'parent': 'd1',
         'patch': {'source_paths': {'p8': None}, 'openings': {'o12': None}},
         'settings': {'cap_style': 'flat'}},
    ]


def assembly_state():
    return {'layer_height': 1.5, 'name': 'House',
            'sections': [{'design_id': 'base', 'height': 36, 'label': 'Footing',
                          'transform': {'profile': 'linear', 'shift': [0, 0], 'scale_in': 0}},
                         {'design_id': 'd1', 'height': 48, 'label': '',
                          'transform': {'profile': 'quadratic', 'shift': [0.25, 0], 'scale_in': 0.0625}},
                         {'design_id': 'd2', 'height': 30, 'label': 'Top'}],
            'transform': {'profile': 'linear', 'shift': [0, 0.125], 'scale_in': 0},
            'objects': [{'kind': 'header', 'id': 'h2', 'section': 1, 'layer': 4, 'x': 120.0, 'y': 240.0,
                         'angle': 90.0, 'span': 30.0, 'bearing': 8.0, 'depth': 10.0, 'thickness': 6.0, 'snap': True}],
            'overrides': [{'section': 2, 'layer': 3, 'x': 300.0, 'y': 120.0, 'note': 'checked on site'}],
            'center_mode': 'multiple',
            'centers': [{'id': 'c1', 'x': 200.0, 'y': 170.0, 'sources': ['R1'], 'label': 'Hall'},
                        {'id': 'c3', 'x': 380.0, 'y': 170.0, 'sources': ['C1'], 'label': ''}],
            'center_counter': 3, 'max_overhang': 1.25,
            'profiles': [{'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R1', 'edge': 0}, 'position': 100.0,
                          'z_bottom': 6.0, 'height': 40.0, 'width': 18.0, 'shape': 'lens', 'label': 'Window'},
                         {'kind': 'plane', 'id': 'p2', 'sources': ['L1'], 'systems': ['WS1'], 'exclude': ['R2'],
                          'direction': 30.0, 'slope': 0.2, 'anchor': [200.0, 200.0], 'z_anchor': 90.0,
                          'label': 'Roof'},
                         # an ARRAY of arches + a CONVERSION of a Designer opening (2026-10-09)
                         {'kind': 'opening', 'id': 'p3', 'wall': {'source': 'R1', 'edge': 2}, 'position': 20.0,
                          'z_bottom': 6.0, 'height': 30.0, 'width': 12.0, 'shape': 'arch', 'label': '',
                          'upper': 'curve', 'lower': 'ellipse', 'bottom_depth': 4.0, 'closure': None,
                          'array': {'arrangement': 'staggered', 'count': 1, 'cols': 3, 'rows': 2, 'h_spacing': 18.0,
                                    'v_spacing': 6.0, 'stagger': None}},
                         {'kind': 'opening', 'id': 'p4', 'wall': {'source': 'R1', 'edge': 0}, 'position': 60.0,
                          'z_bottom': 0.0, 'height': 0.0, 'width': 30.0, 'shape': 'arch', 'label': 'Door',
                          'upper': 'pointed', 'lower': 'flat', 'bottom_depth': None, 'closure': 24.0,
                          'source_opening': {'id': 'o1', 'z_ref': 40.0}}],
            'profile_counter': 4}


def project(active='d1'):
    return {'name': 'Courtyard house',
            'designer': {'designs': designs(), 'active_design': active, 'id_counter': 13, 'design_counter': 3},
            'assembly': {'state': assembly_state(), 'header_counter': 2}}


@pytest.fixture
def client():
    A.app.config['TESTING'] = True
    return A.app.test_client()


def save(client, proj):
    r = client.post('/api/project/save', json={'project': proj})
    return r.status_code, r.get_json()


def open_(client, text):
    r = client.post('/api/project/open', data=text if isinstance(text, (bytes, str)) else json.dumps(text))
    return r.status_code, r.get_json()


def _keys(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(o, list):
        for v in o:
            _keys(v, out)
    return out


# --- round trip ---------------------------------------------------------------------------

def test_save_writes_a_versioned_file(client):
    code, r = save(client, project())
    assert code == 200, r
    f = r['file']
    assert f['format'] == PF.FORMAT == 'mud-printer-project' and f['schema_version'] == PF.SCHEMA_VERSION
    assert r['filename'] == 'Courtyard house.muddesign'
    assert set(f) == {'format', 'schema_version', 'saved_at', 'generator', 'project'}
    assert [d['id'] for d in f['project']['designer']['designs']] == ['base', 'd1', 'd2']


def _migrated():
    """The fixture as an opened file converts it: its in-document Wall System
    records → per-wall envelopes + project Wall Systems (the plane cut's
    `systems: ['WS1']` → that system's walls as `sources`); its Variant-stored
    construction / wall properties dropped (reported)."""
    from walls import migrate_parts
    ds, systems, state, rep = migrate_parts(designs(), assembly_state=assembly_state())
    assert rep and any('dropped' in r for r in rep) and any('In-document Wall System records' in r for r in rep)
    assert [s['id'] for s in systems] == ['WS1', 'WS2', 'WS3']
    return ds, systems, state


def migrated_parts():
    return _migrated()[:2]


def migrated_state():
    return _migrated()[2]


def migrated_designs():
    return migrated_parts()[0]


def test_save_open_save_preserves_the_authoring_state(client):
    p0 = project()
    _, r1 = save(client, p0)
    text = PF.dumps(r1['file'])
    code, o = open_(client, text)
    # (M1, 2026-10-15: the fixture's Variants override persistent wall properties — reported, kept)
    assert code == 200 and all('overrides persistent wall' in w for w in o.get('warnings') or []), o
    _, r2 = save(client, o['project'])
    assert r2['file']['project'] == r1['file']['project']               # save → open → save: identical
    # … and identical to what was saved, field by field
    P = o['project']
    assert P['name'] == 'Courtyard house'
    # (schema 7: the fixture's Variant-stored wall properties / construction are
    # dropped on save — reported; the reference is the fixture as migrated)
    assert (P['designer']['designs'], P['wall_systems']) == migrated_parts()
    assert P['designer']['active_design'] == 'd1'
    assert P['assembly']['state'] == migrated_state() and P['assembly']['header_counter'] == 2
    # the open design's document = what the Layer Design library resolves
    assert o['active_document'] == DesignLibrary(migrated_designs()).raw_document('d1')
    # the Assembly reads back to the same model
    assert Assembly.from_dict(P['assembly']['state']).to_dict() == Assembly.from_dict(migrated_state()).to_dict()


def test_every_design_resolves_to_the_same_document(client):
    _, r = save(client, project())
    _, o = open_(client, PF.dumps(r['file']))
    a = DesignLibrary(*migrated_parts())
    b = DesignLibrary(o['project']['designer']['designs'], o['project']['wall_systems'])
    for did in ('base', 'd1', 'd2'):
        assert a.document(did) == b.document(did) and a.document(did)['wall_systems']
    doc = b.document('base')
    inset = next(p for p in doc['source_paths'] if p['id'] == 'p7')
    assert inset['points'] == [[400, 400], [460, 400], [460, 440], [400, 440]]     # frozen shape kept


def test_opened_project_resolves_to_the_same_geometry(client):
    """The Base document from the file prints exactly what the original did."""
    _, r = save(client, project())
    _, o = open_(client, PF.dumps(r['file']))
    doc0 = base_document()                                       # (the build format, as authored by the fixture)
    doc1 = next(d for d in o['project']['designer']['designs'] if d['id'] == 'base')['document']
    assert 'wall_systems' not in doc1 and all('envelope' in p for p in doc1['source_paths'] if p['id'] in ('R1', 'L1', 'p8'))
    g0 = client.post('/api/effective_paths', json=doc0).get_json()
    g1 = client.post('/api/effective_paths', json=dict(doc1, project_wall_systems=o['project']['wall_systems'])).get_json()
    assert not g0.get('error') and g0['printable'] == g1['printable']


def test_only_authoring_state_is_saved(client):
    _, r = save(client, project())
    keys = _keys(r['file'], set())
    for derived in ('printable', 'moves', 'network', 'resolved', 'instances', 'findings', 'polylines',
                    'graph', 'metrics', 'support', 'variants', 'undo', 'redo', 'hist'):
        assert derived not in keys, derived


def test_derived_designs_store_only_their_differences(client):
    _, r = save(client, project())
    ds = {d['id']: d for d in r['file']['project']['designer']['designs']}
    assert 'document' in ds['base'] and 'patch' not in ds['base']
    assert 'document' not in ds['d1'] and ds['d1']['patch'] and ds['d2']['parent'] == 'd1'


def test_opening_base_as_active(client):
    _, r = save(client, project(active='base'))
    _, o = open_(client, PF.dumps(r['file']))
    assert o['project']['designer']['active_design'] == 'base'
    assert o['active_document'] == DesignLibrary(migrated_designs()).raw_document('base')


# --- counters / references ----------------------------------------------------------------

def test_counters_never_collide_with_existing_ids(client):
    p = project()
    p['designer']['id_counter'] = 1                   # (a hand-edited / older file)
    p['designer']['design_counter'] = 0
    p['assembly']['state']['center_counter'] = 0
    p['assembly']['state']['profile_counter'] = 0
    p['assembly']['header_counter'] = 0
    _, r = save(client, p)
    P = r['file']['project']
    assert P['designer']['id_counter'] == 13          # o12 is in d1's patch
    assert P['designer']['design_counter'] == 3       # d1, d2 exist
    assert P['assembly']['state']['center_counter'] == 3 and P['assembly']['state']['profile_counter'] == 4
    assert P['assembly']['header_counter'] == 2


def test_missing_active_design_falls_back_to_base_with_a_warning(client):
    _, r = save(client, project())
    f = copy.deepcopy(r['file'])
    f['project']['designer']['active_design'] = 'gone'
    code, o = open_(client, json.dumps(f))
    assert code == 200 and o['project']['designer']['active_design'] == 'base'
    assert any('missing' in w for w in o['warnings'])


# --- migration ----------------------------------------------------------------------------

def _file(client):
    _, r = save(client, project())
    return r['file']


def test_migration_steps_upgrade_older_files(client, monkeypatch):
    """The mechanism: a file of schema 1 opened by a (simulated) schema-3
    build runs 1 → 2 → 3 in order, each step on the previous result; the
    result is stamped with the current version and validated."""
    f = _file(client)
    f['schema_version'] = 1            # (a layout of today stamped 1: the steps below are simulated)
    calls = []

    def v1_to_v2(d):
        calls.append(1)
        d['project']['name'] = d['project']['name'] + ' (v2)'
        return d

    def v2_to_v3(d):
        calls.append(2)
        assert d['schema_version'] == 2 and d['project']['name'].endswith('(v2)')
        d['project']['assembly']['state']['max_overhang'] = 1.0
        return d
    monkeypatch.setattr(PF, 'SCHEMA_VERSION', 3)
    monkeypatch.setattr(PF, 'MIGRATIONS', {1: v1_to_v2, 2: v2_to_v3})   # (simulated steps)
    code, o = open_(client, json.dumps(f))
    assert code == 200, o
    assert calls == [1, 2] and o['migrated_from'] == 1 and o['schema_version'] == 3
    assert o['project']['name'] == 'Courtyard house (v2)'
    assert o['project']['assembly']['state']['max_overhang'] == 1.0
    assert any('schema 1' in w for w in o['warnings'])
    # the original dict is never modified in place
    assert f['schema_version'] == 1 and f['project']['name'] == 'Courtyard house'


def test_missing_migration_step_is_refused(client, monkeypatch):
    f = _file(client)
    f['schema_version'] = 1
    monkeypatch.setattr(PF, 'SCHEMA_VERSION', 2)
    monkeypatch.setattr(PF, 'MIGRATIONS', {})
    code, o = open_(client, json.dumps(f))
    assert code == 400 and 'No upgrade from project schema 1' in o['error']


def test_newer_schema_is_refused_clearly(client):
    f = _file(client)
    f['schema_version'] = PF.SCHEMA_VERSION + 1
    code, o = open_(client, json.dumps(f))
    assert code == 400
    assert 'newer version' in o['error'] and str(PF.SCHEMA_VERSION + 1) in o['error']


# --- malformed files ----------------------------------------------------------------------

def _broken(client, mutate):
    f = _file(client)
    mutate(f)
    return open_(client, json.dumps(f))


@pytest.mark.parametrize('text,needle', [
    (b'', 'not a valid project file'),
    (b'{"format": "mud-printer-project", ', 'not a valid project file'),
    (b'[1, 2]', 'not a project file'),
    (b'\xff\xfe\x00junk', 'not UTF-8'),
    (json.dumps({'format': 'something-else', 'schema_version': 1}).encode(), 'not a Mud Printer project'),
    (json.dumps({'format': 'mud-printer-project'}).encode(), 'no valid schema version'),
    (json.dumps({'format': 'mud-printer-project', 'schema_version': '1'}).encode(), 'no valid schema version'),
    (json.dumps({'format': 'mud-printer-project', 'schema_version': 1}).encode(), 'no project'),
])
def test_not_a_project_file(client, text, needle):
    code, o = open_(client, text)
    assert code == 400 and needle in o['error'], o


@pytest.mark.parametrize('mutate,needle', [
    (lambda f: f['project'].pop('designer'), 'no Designer section'),
    (lambda f: f['project']['designer'].update(designs=[]), 'no Layer Designs'),
    (lambda f: f['project']['designer']['designs'][1].update(parent=None, document={'source_paths': []}),
     'exactly one base'),
    (lambda f: f['project']['designer']['designs'][2].update(id='d1'), 'share an id'),
    (lambda f: f['project']['designer']['designs'][2].update(parent='nowhere'), 'cannot be resolved'),
    (lambda f: f['project']['designer']['designs'][0].pop('document'), 'has no document'),
    (lambda f: f['project']['designer']['designs'][1].update(patch={'bogus': {}}), 'cannot be resolved'),
    (lambda f: f['project']['designer']['designs'][0]['document']['source_paths'].append(
        {'id': 'bad', 'type': 'CirclePath', 'cx': 1}), "Layer Design 'Base' cannot be read"),
    (lambda f: f['project']['designer'].update(id_counter=-3), 'id_counter'),
    (lambda f: f['project'].pop('assembly'), 'no Assembly section'),
    (lambda f: f['project']['assembly']['state'].update(layer_height=0), 'layer height'),
    (lambda f: f['project']['assembly']['state'].pop('layer_height'), 'The Assembly cannot be read'),
    (lambda f: f['project']['assembly']['state']['sections'].append({'design_id': 'zz', 'height': 3}),
     "'zz', which is not in the project"),
    (lambda f: f['project']['assembly']['state']['profiles'].append({'kind': 'spiral', 'id': 'p9'}),
     'The Assembly cannot be read'),
])
def test_malformed_projects_are_refused(client, mutate, needle):
    code, o = _broken(client, mutate)
    assert code == 400 and needle in o['error'], o


def test_save_refuses_an_invalid_project(client):
    p = project()
    p['assembly']['state']['sections'][0]['design_id'] = 'missing'
    code, r = save(client, p)
    assert code == 400 and 'not in the project' in r['error']


def test_file_text_is_human_readable_json(client):
    _, r = save(client, project())
    text = PF.dumps(r['file'])
    assert text.startswith('{\n "format": "mud-printer-project",\n "schema_version": 7,')
    assert json.loads(text) == r['file']


def test_cached_sample_points_are_not_stored(client):
    """The Designer's sampled points of PARAMETRIC paths (and of drawn paths
    with control points) are derived → not in the file; a drawn path's own
    points and an inset's frozen points are its shape → kept."""
    p = project()
    base = p['designer']['designs'][0]['document']
    rect = next(x for x in base['source_paths'] if x['id'] == 'R1')
    rect['points'] = [[0, 0], [1, 0], [1, 1]]                            # (a cache)
    base['source_paths'].append({'id': 'p10', 'type': 'ExplicitPath', 'closed': False,
                                 'control_points': [[0, 600], [50, 600]], 'points': [[0, 600], [50, 600]]})
    d1 = p['designer']['designs'][1]
    d1['patch']['source_paths'] = {
        'R1': {'x': 105.0, 'points': [[5, 5]]},                         # moved: x kept, cache dropped
        'C1': {'points': [[9, 9]]},                                      # only the cache differed
        'p11': {'id': 'p11', 'type': 'CirclePath', 'cx': 10, 'cy': 10, 'radius': 5, 'closed': True,
                'points': [[15, 10]]}}
    _, r = save(client, p)
    P = r['file']['project']
    b = {x['id']: x for x in P['designer']['designs'][0]['document']['source_paths']}
    assert 'points' not in b['R1'] and 'points' not in b['C1'] and 'points' not in b['p10']
    assert b['p8']['points'] == [[20, 420], [120, 420], [120, 480]]       # drawn path: its shape
    assert b['p7']['points'] == [[400, 400], [460, 400], [460, 440], [400, 440]]   # inset: frozen shape
    ch = P['designer']['designs'][1]['patch']['source_paths']
    assert ch['R1'] == {'x': 105.0} and 'C1' not in ch and 'points' not in ch['p11'] and ch['p11']['radius'] == 5
    # stable: open → save gives the same file content
    _, o = open_(client, PF.dumps(r['file']))
    _, r2 = save(client, o['project'])
    assert r2['file']['project'] == P
