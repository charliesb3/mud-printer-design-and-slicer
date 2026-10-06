"""
Route origin: where a CLOSED printable route (start = end) begins and
returns — a routing preference [{strand, u}] stored on the layer; it never
changes geometry and falls back to automatic when it cannot be resolved.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from app import app as flask_app


def ring_payload(x=100, origins=None, infill=True, physical=True, extra=None):
    d = {'id': 'l', 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'x': x, 'y': 140, 'w': 200, 'h': 120,
         'closed': True, 'wall': {'thickness': 10}}] + (extra or []),
        'offset_treatments': [], 'lattice_instances': [],
        'infills': [{'id': 'I', 'path_id': 'R', 'pattern': 'zigzag', 'params': {'spacing': 20},
                     'kind': 'wall'}] if infill else [],
        'material': {'physical': physical}}
    if origins is not None:
        d['route_origins'] = origins
    return d


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


def route(c, payload):
    return c.post('/api/route', json=payload).get_json()


def runs(moves):
    out, cur = [], []
    for m in moves:
        if m['kind'] == 'travel':
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(m)
    return out + ([cur] if cur else [])


def test_origin_moves_the_start_but_not_the_geometry(client):
    auto = route(client, ring_payload())
    d = route(client, ring_payload(origins=[{'strand': 'R', 'u': 0.25}]))
    assert d['origins'] == [{'strand': 'R', 'u': 0.25, 'x': 260.0, 'y': 140.0, 'status': 'ok'}]
    mv = d['moves']
    assert mv[0]['start'] == [260.0, 140.0] and mv[-1]['end'] == [260.0, 140.0]   # start = end = origin
    assert auto['moves'][0]['start'] != mv[0]['start']
    # same printed geometry (the origin only splits one segment collinearly)
    assert d['metrics']['print_distance'] == auto['metrics']['print_distance']
    assert d['metrics']['retrace_moves'] == 0 and d['metrics']['travel_moves'] == 0
    # the design geometry is untouched
    assert [p['id'] for p in d['layer']['paths']] == [p['id'] for p in auto['layer']['paths']]


def test_origin_persists_through_a_rebuild(client):
    p = ring_payload(origins=[{'strand': 'R', 'u': 0.6}])
    a, b = route(client, p), route(client, p)
    assert a['moves'] == b['moves'] and a['moves'][0]['start'] == [236.0, 260.0]


def test_origin_follows_its_strand_when_geometry_moves(client):
    d = route(client, ring_payload(x=130, origins=[{'strand': 'R', 'u': 0.25}]))
    assert d['origins'][0]['status'] == 'ok' and d['moves'][0]['start'] == [290.0, 140.0]


def test_unresolvable_origin_falls_back_to_automatic(client):
    auto = route(client, ring_payload())
    for o in ({'strand': 'gone', 'u': 0.4}, {'strand': 'I~0.0', 'u': 0.5}):
        d = route(client, ring_payload(origins=[o], infill=False))
        assert d['origins'][0]['status'] == 'missing'
        assert d['closure']['open'] == [] and d['metrics']['retrace_moves'] == 0   # automatic, still closed
    # never attached to other geometry: the infill strand id is not reused
    assert route(client, ring_payload(origins=[{'strand': 'gone', 'u': 0.4}]))['moves'] == auto['moves']


def test_each_disconnected_closed_component_has_its_own_origin(client):
    island = {'id': 'S', 'type': 'RectanglePath', 'x': 20, 'y': 300, 'w': 40, 'h': 40, 'closed': True}
    d0 = route(client, ring_payload(extra=[island]))
    ids = {m['strand_id'] for m in d0['moves'] if m['strand_id']}
    assert 'S' in ids and len(runs(d0['moves'])) == 2
    d = route(client, ring_payload(extra=[island], origins=[{'strand': 'R', 'u': 0.25},
                                                           {'strand': 'S', 'u': 0.5}]))
    starts = [r[0]['start'] for r in runs(d['moves'])]
    ends = [r[-1]['end'] for r in runs(d['moves'])]
    assert sorted(map(tuple, starts)) == sorted([(260.0, 140.0), (60.0, 340.0)])
    assert starts == ends                                  # each closed run begins and ends at its origin


def test_open_component_ignores_the_origin_and_keeps_start_and_end(client):
    """A component the rules could not close (here: legacy zero-width line,
    physical off) keeps its own start / end; the origin is not applied."""
    p = {'id': 'l', 'source_paths': [{'id': 'L', 'type': 'LinePath', 'start': [100, 200], 'end': [300, 200]}],
         'offset_treatments': [], 'lattice_instances': [], 'material': {'physical': False},
         'route_origins': [{'strand': 'L', 'u': 0.5}]}
    d = route(client, p)
    mv = d['moves']
    assert mv[0]['start'] != mv[-1]['end']
    assert sorted([tuple(mv[0]['start']), tuple(mv[-1]['end'])]) == [(100.0, 200.0), (300.0, 200.0)]


@pytest.mark.skipif(__import__('shutil').which('node') is None, reason='node not installed')
def test_ui_origin_smoke():
    import subprocess
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_origin_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
