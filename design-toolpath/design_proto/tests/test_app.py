"""
Integration tests for the design canvas Flask app.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import re
import json
import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

class TestStaticAssets:
    def test_index_loads(self, client):
        r = client.get('/')
        assert r.status_code == 200
        assert b'DESIGN CANVAS' in r.data

    def test_all_script_srcs_return_200(self, client):
        r = client.get('/')
        html = r.data.decode()
        srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        assert srcs, 'No <script src> found in index.html'
        for src in srcs:
            resp = client.get(src)
            assert resp.status_code == 200, \
                f"Script src '{src}' returned {resp.status_code}"

    def test_all_link_hrefs_return_200(self, client):
        r = client.get('/')
        html = r.data.decode()
        hrefs = re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', html)
        for href in hrefs:
            if href.startswith('http'):
                continue
            resp = client.get(href)
            assert resp.status_code == 200, \
                f"Link href '{href}' returned {resp.status_code}"


# ---------------------------------------------------------------------------
# API — generators
# ---------------------------------------------------------------------------

class TestGeneratorsAPI:
    def test_returns_list(self, client):
        r = client.get('/api/generators')
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_zigzag_in_generators(self, client):
        data = client.get('/api/generators').get_json()
        names = [g['name'] for g in data]
        assert 'zigzag' in names

    def test_wave_in_generators(self, client):
        data = client.get('/api/generators').get_json()
        names = [g['name'] for g in data]
        assert 'wave' in names

    def test_each_generator_has_parameters(self, client):
        data = client.get('/api/generators').get_json()
        for g in data:
            assert 'parameters' in g
            assert isinstance(g['parameters'], list)


# ---------------------------------------------------------------------------
# API — route
# ---------------------------------------------------------------------------

CASE_D_PAYLOAD = {
    'id': 'test',
    'label': 'Case D',
    'source_paths': [
        {
            'id': 'perim',
            'type': 'ExplicitPath',
            'label': 'Perimeter',
            'closed': True,
            'role': 'outer',
            'visible': True,
            'control_points': [
                [0,0],[25,0],[50,0],[75,0],[100,0],
                [100,10],[75,10],[50,10],[25,10],[0,10],
            ],
        },
        {
            'id': 'web',
            'type': 'ExplicitPath',
            'label': 'Web',
            'closed': False,
            'role': 'lattice',
            'visible': True,
            'control_points': [
                [25,10],[25,0],[50,10],[50,0],[75,10],[75,0],
            ],
        },
    ],
    'offset_treatments': [],
    'lattice_instances': [],
    'constraints': {
        'start_path_id': None,
        'start_t': None,
        'reverse_direction': False,
        'component_order': None,
    },
}

SIMPLE_PAYLOAD = {
    'id': 'simple',
    'label': 'Single path',
    'source_paths': [
        {
            'id': 'p1',
            'type': 'ExplicitPath',
            'label': 'Path',
            'closed': True,
            'role': 'outer',
            'visible': True,
            'control_points': [[0,0],[100,0],[100,100],[0,100]],
        }
    ],
    'offset_treatments': [],
    'lattice_instances': [],
    'constraints': {'start_path_id': None, 'start_t': None,
                    'reverse_direction': False, 'component_order': None},
}


class TestRouteAPI:
    def test_route_returns_200(self, client):
        r = client.post('/api/route',
                        data=json.dumps(CASE_D_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200

    def test_route_response_fields(self, client):
        r = client.post('/api/route',
                        data=json.dumps(SIMPLE_PAYLOAD),
                        content_type='application/json')
        data = r.get_json()
        assert 'moves' in data
        assert 'metrics' in data
        assert 'graph' in data
        assert 'layer' in data

    def test_metrics_fields(self, client):
        r = client.post('/api/route',
                        data=json.dumps(SIMPLE_PAYLOAD),
                        content_type='application/json')
        m = r.get_json()['metrics']
        for key in ('print_distance', 'travel_distance', 'print_runs',
                    'travel_moves', 'pct_printing'):
            assert key in m

    def test_moves_nonempty(self, client):
        r = client.post('/api/route',
                        data=json.dumps(SIMPLE_PAYLOAD),
                        content_type='application/json')
        assert len(r.get_json()['moves']) > 0

    def test_case_d_zero_travel(self, client):
        r = client.post('/api/route',
                        data=json.dumps(CASE_D_PAYLOAD),
                        content_type='application/json')
        data = r.get_json()
        assert data['metrics']['travel_moves'] == 0
        assert data['metrics']['pct_printing'] == 100.0

    def test_route_with_offset(self, client):
        payload = {
            **SIMPLE_PAYLOAD,
            'id': 'offset_test',
            'offset_treatments': [{
                'id': 'ot1',
                'source_path_id': 'p1',
                'distance': -5,
                'role': 'inner',
                'label': '',
            }],
        }
        r = client.post('/api/route',
                        data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert data['metrics']['print_runs'] >= 1

    def test_route_with_zigzag_lattice(self, client):
        payload = {
            'id': 'lat_test',
            'label': '',
            'source_paths': [
                {
                    'id': 'a', 'type': 'ExplicitPath', 'label': 'A',
                    'closed': False, 'role': 'outer', 'visible': True,
                    'control_points': [[0,0],[100,0]],
                },
                {
                    'id': 'b', 'type': 'ExplicitPath', 'label': 'B',
                    'closed': False, 'role': 'inner', 'visible': True,
                    'control_points': [[0,20],[100,20]],
                },
            ],
            'offset_treatments': [],
            'lattice_instances': [{
                'id': 'li1',
                'generator': 'zigzag',
                'path_a_id': 'a',
                'path_b_id': 'b',
                'params': {'segments': 4, 'connect_ends': False},
                'variation_index': 0,
                'label': '',
            }],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }
        r = client.post('/api/route',
                        data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0

    def test_primitives_route(self, client):
        for ptype, extra in [
            ('LinePath', {'start': [0,0], 'end': [100,50]}),
            ('CirclePath', {'cx': 50, 'cy': 50, 'radius': 40}),
            ('RectanglePath', {'x': 10, 'y': 10, 'w': 80, 'h': 80}),
        ]:
            payload = {
                'id': 'prim_test',
                'label': '',
                'source_paths': [{
                    'id': 'prim', 'type': ptype, 'label': ptype,
                    'closed': ptype != 'LinePath', 'role': 'free', 'visible': True,
                    **extra,
                }],
                'offset_treatments': [],
                'lattice_instances': [],
                'constraints': {'start_path_id': None, 'start_t': None,
                                'reverse_direction': False, 'component_order': None},
            }
            r = client.post('/api/route',
                            data=json.dumps(payload),
                            content_type='application/json')
            assert r.status_code == 200, f'{ptype} routing failed: {r.get_json()}'

    def test_bad_payload_returns_400(self, client):
        r = client.post('/api/route',
                        data=json.dumps({'garbage': True}),
                        content_type='application/json')
        # Should return 400 with error field
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# API — effective_paths
# ---------------------------------------------------------------------------

class TestEffectivePathsAPI:
    def test_returns_source_paths(self, client):
        r = client.post('/api/effective_paths',
                        data=json.dumps(SIMPLE_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert 'paths' in data
        assert len(data['paths']) >= 1

    def test_offset_adds_path(self, client):
        payload = {
            **SIMPLE_PAYLOAD,
            'offset_treatments': [{
                'id': 'ot1', 'source_path_id': 'p1',
                'distance': -5, 'role': 'inner', 'label': '',
            }],
        }
        r = client.post('/api/effective_paths',
                        data=json.dumps(payload),
                        content_type='application/json')
        data = r.get_json()
        assert len(data['paths']) == 2  # source + derived

    def test_all_paths_have_renderable_points(self, client):
        """Every path in effective_paths must have ≥2 [x,y] points for canvas rendering."""
        payload = {
            **SIMPLE_PAYLOAD,
            'offset_treatments': [{
                'id': 'ot1', 'source_path_id': 'p1',
                'distance': -5, 'role': 'inner', 'label': '',
            }],
        }
        r = client.post('/api/effective_paths',
                        data=json.dumps(payload),
                        content_type='application/json')
        data = r.get_json()
        for p in data['paths']:
            assert 'points' in p, f"Path {p.get('id')} missing points"
            assert len(p['points']) >= 2, f"Path {p.get('id')} has too few points"
            for pt in p['points']:
                assert len(pt) == 2, f"Point {pt} is not [x, y]"

    def test_offset_path_differs_from_source(self, client):
        """Offset-derived path must not be identical to source."""
        payload = {
            **SIMPLE_PAYLOAD,
            'offset_treatments': [{
                'id': 'ot1', 'source_path_id': 'p1',
                'distance': -5, 'role': 'inner', 'label': '',
            }],
        }
        r = client.post('/api/effective_paths',
                        data=json.dumps(payload),
                        content_type='application/json')
        data = r.get_json()
        source = next(p for p in data['paths'] if p['id'] == 'p1')
        derived = next(p for p in data['paths'] if p['id'] != 'p1')
        # At least one point must differ
        assert any(
            abs(sp[0] - dp[0]) > 0.1 or abs(sp[1] - dp[1]) > 0.1
            for sp, dp in zip(source['points'], derived['points'])
        )
