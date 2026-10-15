"""
Regression (manual, 2026-10-06): after trimming a rectangle and a circle
(10 in walls) where they overlap, SELECTING the rectangle drew a faint ghost
of the untrimmed rectangle (selection outline, hover highlight, outline
handles, hit-testing on trimmed-away sections). Selection now uses the
VISIBLE source: the parametric source minus its trimmed sections. The
sections come from the real backend; the UI is checked under node.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app as flask_app


def payload(trims=()):
    return {'id': 'l', 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect 1', 'x': 100, 'y': 100, 'w': 240, 'h': 160,
         'closed': True, 'wall': {'thickness': 10}},
        {'id': 'C', 'type': 'CirclePath', 'label': 'Circle 1', 'cx': 340, 'cy': 180, 'radius': 80,
         'closed': True, 'wall': {'thickness': 10}}],
        'offset_treatments': [], 'lattice_instances': [], 'trims': list(trims),
        'material': {'physical': True}}


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_trimmed_source_has_no_ghost_when_selected():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        secs = c.post('/api/effective_paths', json=payload()).get_json()['network']['trim_sections']
        trims = []
        for s in secs:
            other = 'C' if s['source'] == 'R' else 'R'
            if s['inside'].get(other):
                trims.append({'id': 't' + s['source'], 'source_path_id': s['source'], 'start': s['start'],
                              'end': s['end'], 'inside': s['inside'], 'u_mid': s['u_mid']})
        assert len(trims) == 2
        net = c.post('/api/effective_paths', json=payload(trims)).get_json()['network']
    assert all(v['status'] == 'ok' for v in net['trims'].values())
    data = {'trims': trims, 'trim_sections': net['trim_sections'],
            'modified_sources': net['modified_sources'], 'all_sections': secs}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(data, f)
    try:
        script = os.path.join(os.path.dirname(__file__), 'js', 'ui_trim_ghost_smoke.js')
        proc = subprocess.run(['node', script, f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# DEFERRED (2026-10-15) — documented, NOT fixed: attaching new geometry to a
# trimmed-away section brings that trimmed geometry back. A Trim is a
# topological SIGNATURE (its bounding sources + inside-which-closed-source);
# a new wall ending on the removed section adds a contact that splits it, the
# signature no longer resolves ('unresolved') and the section reappears.
# Attaching to VISIBLE geometry leaves every trim intact. Since the snap fix
# the UI no longer invites this (removed sections are not snap targets), but
# an Alt-placed / crossing wall still triggers it. Part of the deferred
# junction / trim topology architecture — see design-toolpath/PROJECT_MEMORY.md.
# ---------------------------------------------------------------------------

def _trims_after(extra):
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        secs = c.post('/api/effective_paths', json=payload()).get_json()['network']['trim_sections']
        trims = [{'id': 't' + s['source'], 'source_path_id': s['source'], 'start': s['start'], 'end': s['end'],
                  'inside': s['inside'], 'u_mid': s['u_mid']}
                 for s in secs if s['inside'].get('C' if s['source'] == 'R' else 'R')]
        P = payload(trims)
        P['source_paths'] += extra
        return c.post('/api/effective_paths', json=P).get_json()['network']['trims']


def _line(a, b):
    return [{'id': 'L', 'type': 'LinePath', 'start': list(a), 'end': list(b), 'closed': False, 'wall': {'thickness': 10}}]


def test_attaching_to_visible_geometry_keeps_trims():
    assert {k: v['status'] for k, v in _trims_after(_line((480, 300), (420, 180))).items()} == {'tC': 'ok', 'tR': 'ok'}
    assert {k: v['status'] for k, v in _trims_after(_line((200, 30), (200, 100))).items()} == {'tC': 'ok', 'tR': 'ok'}


@pytest.mark.xfail(strict=True, reason='DEFERRED: trim topology — a contact on a removed section un-resolves the Trim')
def test_attaching_to_a_removed_section_keeps_trims():
    assert {k: v['status'] for k, v in _trims_after(_line((200, 300), (260, 180))).items()} == {'tC': 'ok', 'tR': 'ok'}
