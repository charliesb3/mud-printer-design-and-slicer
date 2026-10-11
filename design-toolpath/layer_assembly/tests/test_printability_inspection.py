"""
PRINTABILITY INSPECTION — deferred print paths M2 (2026-10-11) + the Designer's
wall display and the Wall Systems layer navigation.

  * the Designer's envelope shows each wall at its ARCHITECTURAL thickness
    (a 10 in wall: inner and outer boundaries 10 in apart); a wall without
    thickness is its single centreline — its printed out-and-back lanes are
    flagged `single` and never part of the envelope view;
  * Generate Wall Systems returns the FINAL bead-level report (unchanged
    thresholds, nothing suppressed) with an inspection: every finding
    classified ('architectural' = the PRELIMINARY envelope check of the same
    stack flags the same place; else by the printed strand under it:
    'lattice' — Wall Infill / Wall System paths — or 'construction' — skins,
    caps, jambs), the preliminary report itself, and routing / CONTINUITY
    items (Wall System bands whose route cannot close);
  * the Print Simulation's support report is the generation's, unchanged;
  * the layer navigation is in view, clickable and working at 1600×1000,
    1280×720 and 1024×640 (real browser, when Chrome is available).
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

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


def room(**over):
    d = {'id': 'b', 'material': dict(MAT), 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Room', 'x': 100, 'y': 100, 'w': 200, 'h': 120, 'closed': True},
        {'id': 'U', 'type': 'LinePath', 'label': 'Free line', 'start': [400, 100], 'end': [400, 220]}],
        'wall_groups': [{'id': 'G1', 'name': 'Rooms', 'members': ['R'], 'thickness': 10.0, 'align': 'auto',
                         'print_reference': False}]}
    d.update(over)
    return d


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# ---- the Designer's wall display ----------------------------------------------------------

def test_designer_envelope_shows_the_architectural_thickness(client):
    code, r = post(client, '/api/effective_paths', dict(room(), envelope=True))
    assert code == 200 and r['printable'] == []
    rings = [p for p in r['envelope'] if p['role'] == 'ring' and set(p['src']) == {'R'}]
    assert len(rings) == 2                                        # the outer and the inner boundary
    (a, b) = sorted((bbox(p['pts']) for p in rings), key=lambda q: q[2] - q[0], reverse=True)
    assert a == pytest.approx((100, 100, 300, 220))               # (auto = inside a closed shape)
    for k, s in enumerate((1, 1, -1, -1)):
        assert (b[k] - a[k]) * s == pytest.approx(10.0)           # 10 in apart on every side


def test_a_wall_without_thickness_is_its_single_centreline(client):
    code, r = post(client, '/api/effective_paths', dict(room(), envelope=True))
    mine = [p for p in r['envelope'] if 'U' in p['src']]
    assert mine and all(p['role'] == 'bead' and p['single'] for p in mine)   # printed lanes: never the envelope view
    assert not any(p['role'] == 'ring' and 'U' in p['src'] for p in r['envelope'])
    page = open(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'static', 'app.js')).read()
    assert 'let showEnvelope = true;' in page                     # the envelope is the Designer's default view
    assert "p.cls === 'face' && !p.single" in page                 # (single lanes left out; the shape line drawn)


# ---- M2: final bead-level findings, classified ---------------------------------------------

def _bench(layers=16):
    from layer_assembly import bench_mixed as B
    from wall_groups import split_designs
    ds, ws = split_designs(B._fixture())
    return {'assembly': B.assembly(layers), 'designs': ds, 'wall_systems': ws}


def test_generation_classifies_every_final_finding(client):
    B = _bench()
    code, g = post(client, '/api/wallsystems/generate', B)
    assert code == 200
    S, I = g['support'], g['inspection']
    assert S['level'] == 'bead' and I['architectural']['level'] == 'envelope'
    assert S['min_overlap'] == I['architectural']['min_overlap'] and S['max_overhang'] == I['architectural']['max_overhang']
    ids = [f['id'] for f in S['findings']]
    assert [x['id'] for x in I['findings']] == ids and ids       # every final finding, none dropped
    assert {x['category'] for x in I['findings']} <= {'architectural', 'lattice', 'construction'}
    assert sum(I['counts'].values()) == len(ids)
    arch_ids = {a['id'] for a in I['architectural']['findings']}
    for x in I['findings']:
        assert (x['category'] == 'architectural') == (x['architectural'] is not None)
        assert x['architectural'] is None or x['architectural'] in arch_ids
        assert x['role'] in ('lattice', 'wall_system', 'cap', 'inner', 'outer', 'free', 'face', None)
        if x['category'] == 'lattice':
            assert x['role'] in ('lattice', 'wall_system')
    # the preliminary report IS the Assembly's (same stack, same rule)
    _, e = post(client, '/api/assembly/resolve', dict(B, envelope=True))
    strip = lambda R: [(f['layer'], f['kind'], round(f['span'], 6), [round(c, 6) for c in f['centre']]) for f in R['findings']]
    assert strip(e['support']) == strip(I['architectural'])


def test_routing_continuity_items_come_from_the_wall_system_reports(client):
    doc = {'id': 'b', 'material': dict(MAT), 'source_paths': [
        {'id': 'L', 'type': 'LinePath', 'label': 'Wave wall', 'start': [0, 0], 'end': [200, 0]}],
        'wall_groups': [{'id': 'G', 'name': 'Wall', 'members': ['L'], 'thickness': 12.0, 'align': 'center',
                         'print_reference': False}]}
    ws = [{'id': 'S1', 'name': 'Odd waves', 'type': 'interleaved', 'params': {'paths': 3}, 'members': [], 'groups': ['G']}]
    B = {'assembly': {'layer_height': 1.5, 'sections': [{'design_id': 'A', 'height': 4.5}], 'max_overhang': 1.0},
         'designs': [{'id': 'A', 'name': 'A', 'document': doc}], 'wall_systems': ws}
    code, g = post(client, '/api/wallsystems/generate', B)
    assert code == 200
    cont = g['inspection']['continuity']
    assert len(cont) == 1 and cont[0]['system'] == 'G' and cont[0]['layers'] == [0, 1, 2]
    assert 'cannot close' in cont[0]['reason'] and cont[0]['sources'] == ['L']
    ws[0]['params']['paths'] = 4                                   # even: one closed route — no item
    code, g2 = post(client, '/api/wallsystems/generate', B)
    assert g2['inspection']['continuity'] == []


def test_the_print_simulation_reports_the_same_final_support(client):
    B = _bench(8)
    _, g = post(client, '/api/wallsystems/generate', B)
    code, p = post(client, '/api/assembly/print_plan', dict(B, detail='summary', require_generated=True))
    assert code == 200
    # (the plan's report: the same bead-level findings — classification is inspection only)
    code, s = post(client, '/api/assembly/print_plan', dict(B, detail='stream', require_generated=True))
    assert s['support']['findings'] == g['support']['findings']


# ---- the layer navigation (real browser) --------------------------------------------------

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


@pytest.mark.skipif(shutil.which('node') is None or not os.path.exists(CHROME), reason='node / Chrome not available')
def test_layer_navigation_is_in_view_clickable_and_working(tmp_path):
    import socket
    import threading
    import time
    import urllib.request
    from werkzeug.serving import make_server
    from app import app
    from layer_assembly import bench_mixed as Bm
    proj = tmp_path / 'p.json'
    Bm.project_file(str(proj), layers=12)
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    ch = subprocess.Popen([CHROME, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={tmp_path / "c"}',
                           '--window-size=1600,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        script = os.path.join(os.path.dirname(__file__), 'js', 'wallsystems_nav_layout.js')
        p = subprocess.run(['node', script, f'http://127.0.0.1:{srv.server_port}/', str(proj), str(port)],
                           capture_output=True, text=True, timeout=300)
    finally:
        ch.kill()
        srv.shutdown()
    assert p.returncode == 0, p.stdout + p.stderr
    rows = json.loads(p.stdout.strip().splitlines()[-1])
    assert [r['size'] for r in rows] == ['1600x1000', '1280x720', '1024x640']
    for r in rows:
        assert r['bad'] == [] and not r['docScroll'], r            # every control in view + clickable, no page scroll
        assert r['layerAfterSlider'] == 5                           # the slider selects a layer
        sc = r['scene']
        assert sc['kind'] == 'generated' and sc['status'] == 'current' and sc['paths'] > 0   # its generated geometry
