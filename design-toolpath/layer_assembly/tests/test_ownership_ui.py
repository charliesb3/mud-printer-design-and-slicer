"""M1 OWNERSHIP (2026-10-15): persistent wall / material / Wall Geometry edits
made while a Variant is open go to their owner; layout state stays the
Variant's; stored divergences are reported (WALL_ARCHITECTURE.md)."""
import os
import shutil
import subprocess
import sys
import threading

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..', 'design_proto')))


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ownership_ui_smoke():
    from werkzeug.serving import make_server
    from app import app
    srv = make_server('127.0.0.1', 0, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        proc = subprocess.run(['node', os.path.join(HERE, 'js', 'ui_ownership_smoke.js'),
                               f'http://127.0.0.1:{srv.server_port}',
                               os.path.join(HERE, 'golden', 'projects', 'real_10xtest1.muddesign')],
                              capture_output=True, text=True, timeout=240)
    finally:
        srv.shutdown()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI OWNERSHIP SMOKE PASSED' in proc.stdout


def test_divergences_report():
    from layer_design import divergences, describe_divergences
    designs = [{'id': 'base', 'parent': None, 'document': {}},
               {'id': 'd1', 'name': 'Lower', 'parent': 'base',
                'settings': {'junction_style': 'round', 'material': {'contact_overlap': 0.5}, 'constraints': {}},
                'patch': {'junction_overrides': {'k#0': {'treatment': 'round'}},
                          'source_paths': {'p1': {'corner_radius': 2, 'x': 3},      # owned field + geometry
                                           'p9': {'id': 'p9', 'type': 'LinePath'},  # introduced: not a divergence
                                           'p2': None},                             # removal: layout state
                          'wall_groups': {'WG1': {'thickness': 8}},
                          'openings': {'o1': {'id': 'o1'}}}}]
    got = {(x['kind'], x.get('record'), x['field']) for x in divergences(designs)}
    assert got == {('setting', None, 'junction_style'), ('setting', None, 'material.contact_overlap'),
                   ('junction_override', None, 'k#0'), ('record', 'p1', 'corner_radius'),
                   ('record', 'WG1', 'thickness')}
    assert describe_divergences(divergences(designs))[0].startswith('Lower overrides persistent')
