"""
ASSEMBLY OPENINGS ARE ARCHITECTURAL (2026-10-11 regression).

Manual finding: a transformed wall with a self-supporting 3 × 2 opening
array was valid with Hollow Skin and NOT PRINTABLE with Interleaved Waves —
the Assembly read the wall's CONSTRUCTION: its profile targets carried the
construction minimum (`min_length` 2 × thickness → a 20 in pier for a 10 in
Interleaved wall), and every layer's placement pivot / scale came from the
construction's PRINTED footprint. Now:

  * changing the Wall System changes NOTHING in the Assembly: opening
    geometry, validity, placement, preliminary support, preview geometry
    (the whole envelope resolve response is identical) — for horizontal,
    vertical and grid arrays on a transformed wall, across every type;
  * every opening of an array is judged alike and carries its own errors
    (the page marks exactly the openings concerned);
  * legitimate architectural errors stay (spacing below the 2-bead pier,
    overlapping clones, a shape steeper than the Maximum overhang);
  * the construction's own minimum is still checked — after generation, in
    the Wall Systems inspection (`inspection.openings`), never applied to
    the cuts; lattice findings are still reported there.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

from walls import TYPES

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
PARAMS = {'parallel': {'walls': 4}, 'interleaved': {'paths': 4}, 'linked': {'paths': 4}}
WEB = {'skin_web': {'pattern': 'zigzag', 'params': {'spacing': 20}}}
ARRAYS = {'horizontal': {'arrangement': 'horizontal', 'count': 3, 'cols': 3, 'rows': 1},
          'vertical': {'arrangement': 'vertical', 'count': 2, 'cols': 1, 'rows': 2},
          'grid': {'arrangement': 'grid', 'count': 6, 'cols': 3, 'rows': 2}}


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture(scope='module')
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


def designs():
    doc = {'id': 'b', 'material': dict(MAT), 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 0, 'y': 0, 'w': 240, 'h': 160, 'closed': True}],
        'wall_groups': [{'id': 'G1', 'name': 'Room', 'members': ['R'], 'thickness': 10.0, 'align': 'auto',
                         'print_reference': False}]}
    return [{'id': 'A', 'name': 'A', 'document': doc}]


def systems(t):
    return [{'id': 'S1', 'name': 'Room walls', 'type': t, 'params': PARAMS.get(t, {}), 'web': WEB.get(t, {}),
             'members': ['R']}]            # (2026-10-15: a Wall System lists walls)


def assembly(arr, h_spacing=14.0, v_spacing=6.0, position=120.0, upper_height=9.0, height=60.0):
    """A TRANSFORMED stack (shift + taper) with a self-supporting pointed opening array on one wall."""
    return {'layer_height': 1.5, 'max_overhang': 1.0, 'sections': [{'design_id': 'A', 'height': height}],
            'opening_alignment': 'follow',        # (written for Follow Wall geometry; the default is Plumb since 2026-10-14)
            'transform': {'profile': 'linear', 'shift': [0.1, 0.05], 'scale_in': 0.0625},
            'profiles': [{'kind': 'opening', 'id': 'p1', 'wall': {'source': 'R', 'edge': 0}, 'position': position,
                          'z_bottom': 6.0, 'height': 24.0, 'width': 10.0, 'upper': 'pointed', 'lower': 'pointed',
                          'bottom_depth': 9.0, 'upper_height': upper_height, 'middle': False,
                          'middle_height': None, 'label': '',
                          'array': dict(ARRAYS[arr], h_spacing=h_spacing, v_spacing=v_spacing, stagger=None)}],
            'profile_counter': 1}


def architectural(r):
    """The resolve response minus the one PRINT hint it carries: whether a
    design prints a Wall Infill lattice (profile_sources[].lattice — which
    layers of a planar cut read lattice stations; dropped from envelopes)."""
    r = json.loads(json.dumps(r))
    for recs in (r.get('profile_sources') or {}).values():
        for x in recs:
            x.pop('lattice', None)
    return json.dumps(r, sort_keys=True)


def resolve(c, asm, t, envelope=True):
    code, r = post(c, '/api/assembly/resolve', {'assembly': asm, 'designs': designs(), 'wall_systems': systems(t),
                                                 'envelope': envelope})
    assert code == 200, r
    return r


@pytest.mark.parametrize('arr', sorted(ARRAYS))
def test_the_wall_system_never_changes_the_assembly(client, arr):
    asm = assembly(arr)
    ref = resolve(client, asm, 'hollow')
    p = ref['profiles'][0]
    assert p['valid'] and not p['errors'] and len(p['members']) == ARRAYS[arr]['count']
    assert all(m['errors'] == [] for m in p['members'])
    assert ref['instances'][-1]['transform']['tx'] != 0          # (a transformed stack)
    want = architectural(ref)
    for t in TYPES:
        r = resolve(client, asm, t)
        # the WHOLE envelope response: opening geometry + validity, placements,
        # preliminary support, preview geometry, profile targets
        assert architectural(r) == want, t


def test_print_placements_do_not_depend_on_the_construction(client):
    asm = assembly('grid')
    tfs = {t: [i['transform'] for i in resolve(client, asm, t, envelope=False)['instances']]
           for t in ('hollow', 'interleaved', 'parallel', 'single')}
    assert all(v == tfs['hollow'] for v in tfs.values())


def test_every_opening_of_an_array_is_judged_alike(client):
    # too close (4 in of wall < the 2-bead 6 in pier): EVERY clone concerned,
    # not just the master — in every construction
    for t in ('hollow', 'interleaved'):
        p = resolve(client, assembly('grid', h_spacing=4.0), t)['profiles'][0]
        assert not p['valid']
        assert all(m['errors'] for m in p['members'])
        assert all('wall between' in e for m in p['members'] for e in m['errors'])
    # near the wall's end: exactly the clones beyond it, the master is fine
    p = resolve(client, assembly('grid', h_spacing=28.0, position=215.0), 'interleaved')['profiles'][0]
    assert not p['valid']
    assert [bool(m['errors']) for m in p['members']] == [False, True, True, False, True, True]


def test_architectural_errors_are_kept(client):
    for t in ('hollow', 'interleaved', 'single'):
        # clones closer than the 2-bead pier
        p = resolve(client, assembly('horizontal', h_spacing=4.0), t)['profiles'][0]
        assert not p['valid'] and any('needs ≥ 6.00 in' in e for e in p['errors'])
        # vertical rows sharing no solid layer
        p = resolve(client, assembly('vertical', v_spacing=0.0), t)['profiles'][0]
        assert not p['valid']
        # an upper profile steeper than the 1 in Maximum overhang
        p = resolve(client, assembly('horizontal', upper_height=6.0), t)['profiles'][0]
        assert not p['valid'] and any('not self-supporting' in e for e in p['errors'])
        assert all(m['errors'] for m in p['members'])             # (the shape: every opening)


def test_the_construction_minimum_is_checked_after_generation(client):
    asm = assembly('horizontal', height=45.0)
    B = {'assembly': asm, 'designs': designs()}
    code, h = post(client, '/api/wallsystems/generate', dict(B, wall_systems=systems('hollow')))
    assert code == 200 and h['inspection']['openings'] == []
    code, w = post(client, '/api/wallsystems/generate', dict(B, wall_systems=systems('interleaved')))
    assert code == 200
    ops = w['inspection']['openings']
    assert len(ops) == 1 and ops[0]['profile'] == 'p1' and ops[0]['index'] == 0
    assert ops[0]['members'] == [0, 1, 2] and ops[0]['layer'] is not None
    assert all('needs ≥ 20.00 in' in e for e in ops[0]['errors'])
    # genuine bead-level findings are still reported (Interleaved lattice over the openings)
    assert w['inspection']['counts']['lattice'] > 0
    assert len(w['support']['findings']) == sum(w['inspection']['counts'].values())
    # the Assembly (architectural) result is the same for both
    assert resolve(client, asm, 'hollow')['profiles'] == resolve(client, asm, 'interleaved')['profiles']
