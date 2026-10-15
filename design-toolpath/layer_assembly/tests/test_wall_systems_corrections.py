"""
CORRECTION PASS of the four-stage workflow (2026-10-11): Designer → Assembly →
Wall Systems → Print Simulation.

  * the default Maximum Overhang is 1 in; projects saved without a value keep
    the 1.5 in they were designed with (project file schema 3); explicit
    values are untouched;
  * EVERY architectural wall is assignable — an ungrouped wall too, with its
    OWN envelope (`path.envelope`), composed as a one-wall group; its own
    thickness is the authoritative architecture (prints exactly as the same
    thickness through a Wall Group); editing it invalidates a generation;
  * the Designer's envelope response marks single-bead lanes (left out of the
    envelope view) and still carries the source pieces of trimmed / opened
    sources (the single-line shapes view draws them);
  * the Base design's GENERATED geometry is served from the generation (the
    Wall Systems workspace's first view after generating);
  * the Assembly Transform column never scrolls sideways (real browser, when
    Chrome is available).
"""
import copy
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

from wall_groups import compose

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


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


# ---- Maximum Overhang ------------------------------------------------------------------

def test_default_maximum_overhang_is_one_inch():
    from layer_assembly.model import Assembly, DEFAULT_MAX_OVERHANG
    assert DEFAULT_MAX_OVERHANG == 1.0
    a = Assembly.from_dict({'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 3}]})
    assert a.max_overhang == 1.0
    assert Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'max_overhang': 2.0}).max_overhang == 2.0
    page = open(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'static', 'assembly.js')).read()
    assert 'max_overhang: 1.0,' in page and "asm.state.max_overhang : 1.0;" in page      # new projects


def _v2_file(max_overhang=None):
    import project_file as PF
    from layer_assembly import bench_mixed as B
    from wall_groups import split_designs
    ds, systems = split_designs(B._fixture())
    st = B.assembly(12)
    st.pop('max_overhang', None)
    if max_overhang is not None:
        st['max_overhang'] = max_overhang
    return {'format': PF.FORMAT, 'schema_version': 2, 'project': {
        'name': 'v2', 'designer': {'designs': ds, 'active_design': 'base', 'id_counter': 40, 'design_counter': 3},
        'assembly': {'state': st, 'header_counter': 0}, 'wall_systems': systems}}


def test_existing_projects_keep_their_maximum_overhang():
    import project_file as PF
    from app import _deserialise_layer
    implicit = PF.load(json.dumps(_v2_file()), _deserialise_layer)
    assert implicit['migrated_from'] == 2 and implicit['schema_version'] == 6
    assert implicit['project']['assembly']['state']['max_overhang'] == 1.5          # what it was designed with
    explicit = PF.load(json.dumps(_v2_file(2.25)), _deserialise_layer)
    assert explicit['project']['assembly']['state']['max_overhang'] == 2.25         # untouched
    assert implicit['project']['wall_systems'] == _v2_file()['project']['wall_systems']


# ---- every architectural wall assignable; its own envelope -----------------------------------

def doc(**over):
    d = {'id': 'b', 'material': dict(MAT), 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect', 'x': 100, 'y': -40, 'w': 100, 'h': 80, 'closed': True},
        {'id': 'L', 'type': 'LinePath', 'label': 'Line', 'start': [250, -60], 'end': [250, 60]},
        {'id': 'U', 'type': 'LinePath', 'label': 'Free', 'start': [320, -60], 'end': [320, 60]}],
        'wall_groups': [{'id': 'G1', 'name': 'Rooms', 'members': ['R', 'L'], 'thickness': 10.0, 'align': 'auto',
                         'print_reference': False}]}
    d.update(over)
    return d


def recs(d):
    return {r['id']: r for r in d['wall_systems']}


def test_ungrouped_walls_compose_with_their_own_envelope():
    d = doc()
    d['source_paths'][2]['envelope'] = {'thickness': 8.0, 'align': 'center', 'print_reference': False}
    R = recs(compose(d, 'A', []))
    assert R['wall:U']['members'] == ['U'] and R['wall:U']['thickness'] == 8.0 and R['wall:U']['align'] == 'center'
    assert R['wall:U']['type'] == 'hollow'                              # no system: its two skins
    S = [{'id': 'S1', 'type': 'parallel', 'params': {'walls': 2}, 'members': ['U'], 'groups': []}]
    assert recs(compose(d, 'A', S))['wall:U']['type'] == 'parallel'
    assert recs(compose(d, 'A', S, arch=True))['wall:U']['thickness'] == 8.0
    # without a thickness: a single bead, unless a Wall System lists it
    plain = doc()
    assert 'wall:U' not in recs(compose(plain, 'A', []))
    assert recs(compose(plain, 'A', S))['wall:U']['thickness'] == 0.0
    assert 'wall:U' not in recs(compose(plain, 'A', S, arch=True))


def test_own_thickness_prints_exactly_as_the_same_thickness_through_a_group(client):
    """The own envelope is the authoritative architecture, not a second value:
    U at 8 in on its own == U as the only wall of a Wall Group at 8 in."""
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 3}], 'max_overhang': 1.0}
    own = doc()
    own['source_paths'][2]['envelope'] = {'thickness': 8.0, 'align': 'auto', 'print_reference': False}
    grp = doc()
    grp['wall_groups'].append({'id': 'GU', 'name': 'Free', 'members': ['U'], 'thickness': 8.0, 'align': 'auto',
                               'print_reference': False})
    S_own = [{'id': 'S1', 'name': 'P', 'type': 'parallel', 'params': {'walls': 2}, 'members': ['U'], 'groups': []}]
    out = []
    for d, S in ((own, S_own), (grp, S_own)):
        B = {'assembly': asm, 'designs': [{'id': 'A', 'name': 'A', 'document': d}], 'wall_systems': S}
        code, g = post(client, '/api/wallsystems/generate', B)
        assert code == 200
        code, lay = post(client, '/api/wallsystems/layer', dict(B, index=1))
        assert code == 200
        out.append([(p['pts'], p['closed']) for p in lay['polylines']])
    assert out[0] == out[1]


def test_own_thickness_edit_invalidates_the_generation(client):
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 3}], 'max_overhang': 1.0}
    d = doc()
    d['source_paths'][2]['envelope'] = {'thickness': 8.0, 'align': 'auto', 'print_reference': False}
    S = [{'id': 'S1', 'name': 'P', 'type': 'parallel', 'params': {'walls': 2}, 'members': ['U'], 'groups': []}]
    B = {'assembly': asm, 'designs': [{'id': 'A', 'name': 'A', 'document': d}], 'wall_systems': S}
    assert post(client, '/api/wallsystems/generate', B)[0] == 200
    assert post(client, '/api/assembly/print_plan', dict(B, require_generated=True))[0] == 200
    X = copy.deepcopy(B)
    X['designs'][0]['document']['source_paths'][2]['envelope']['thickness'] = 9.0
    assert post(client, '/api/assembly/print_plan', dict(X, require_generated=True))[0] == 409
    assert post(client, '/api/wallsystems/layer', dict(X, index=0))[0] == 409
    # the Assembly envelope follows the thickness
    _, e1 = post(client, '/api/assembly/geometry', {'designs': B['designs'], 'ids': ['A'], 'envelope': True})
    _, e2 = post(client, '/api/assembly/geometry', {'designs': X['designs'], 'ids': ['A'], 'envelope': True})
    assert e1['designs']['A']['polylines'] != e2['designs']['A']['polylines']


def test_base_design_generated_geometry_is_served_from_the_generation(client):
    asm = {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 3}], 'max_overhang': 1.0}
    S = [{'id': 'S1', 'name': 'W', 'type': 'interleaved', 'params': {'paths': 4}, 'members': [], 'groups': ['G1']}]
    B = {'assembly': asm, 'designs': [{'id': 'A', 'name': 'A', 'document': doc()}], 'wall_systems': S}
    assert post(client, '/api/wallsystems/layer', dict(B, design='A'))[0] == 409          # not generated
    assert post(client, '/api/wallsystems/generate', B)[0] == 200
    code, base = post(client, '/api/wallsystems/layer', dict(B, design='A'))
    code0, lay0 = post(client, '/api/wallsystems/layer', dict(B, index=0))
    assert code == 200 and base['index'] == -1 and base['polylines'] and any(p.get('system') == 'G1' for p in base['polylines'])
    assert [p['pts'] for p in base['polylines']] == [p['pts'] for p in lay0['polylines']]     # (layer 0 = the design)


# ---- the Designer's envelope response --------------------------------------------------------

def test_designer_envelope_marks_single_bead_lanes_and_keeps_source_pieces(client):
    d = doc(openings=[{'id': 'o1', 'type': 'opening', 'source_path_id': 'R', 'center_s': 50.0, 'width': 14.0,
                       'end_treatment': 'inherit'}])
    code, r = post(client, '/api/effective_paths', dict(d, envelope=True))
    assert code == 200
    single = [p for p in r['envelope'] if p.get('single')]
    assert single and {p['src'][0] for p in single} == {'U'}            # the free single-bead line's lanes
    assert not any(p.get('single') for p in r['envelope'] if p['src'][0] in ('R', 'L'))
    pieces = [p for p in r['paths'] if p.get('treatment_id') in ('opening_cut', 'network_src')]
    assert pieces and all(p.get('source_id') == 'R' for p in pieces if p.get('treatment_id') == 'opening_cut')


# ---- Assembly Transform column (real browser) ----------------------------------------------

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


@pytest.mark.skipif(shutil.which('node') is None or not os.path.exists(CHROME), reason='node / Chrome not available')
def test_assembly_transform_column_never_scrolls_sideways(tmp_path):
    import socket
    import threading
    import time
    from werkzeug.serving import make_server
    from app import app
    from layer_assembly import bench_mixed as B
    proj = tmp_path / 'p.json'
    B.project_file(str(proj), layers=12)
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    ch = subprocess.Popen([CHROME, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={tmp_path / "c"}',
                           '--window-size=1600,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import urllib.request
        for _ in range(60):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        script = os.path.join(os.path.dirname(__file__), 'js', 'assembly_panel_layout.js')
        p = subprocess.run(['node', script, f'http://127.0.0.1:{srv.server_port}/', str(proj), str(port)],
                           capture_output=True, text=True, timeout=240)
    finally:
        ch.kill()
        srv.shutdown()
    assert p.returncode == 0, p.stdout + p.stderr
    rows = json.loads(p.stdout.strip().splitlines()[-1])
    assert len(rows) == 4
    for m in rows:
        assert m['scrollW'] <= m['clientW'] and not m['over'], m          # no horizontal scrolling
    assert len({m['canvasW'] for m in rows}) == 1                       # the preview keeps its width
