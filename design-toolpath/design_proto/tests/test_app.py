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


# ---------------------------------------------------------------------------
# Workflow tests — end-to-end user scenarios
# ---------------------------------------------------------------------------

# Workflow A: ExplicitPath sent with only control_points key (drawn path from UI)
_WF_A_PAYLOAD = {
    'id': 'wf_a',
    'label': '',
    'source_paths': [{
        'id': 'drawn',
        'type': 'ExplicitPath',
        'label': 'Drawn',
        'closed': True,
        'role': 'outer',
        'visible': True,
        'control_points': [[0, 0], [100, 0], [100, 100], [0, 100]],
        # deliberately no 'points' key — simulates a freshly drawn UI path
    }],
    'offset_treatments': [],
    'lattice_instances': [],
    'constraints': {'start_path_id': None, 'start_t': None,
                    'reverse_direction': False, 'component_order': None},
}

# Workflow B: Rectangle primitive → route produces moves
_WF_B_PAYLOAD = {
    'id': 'wf_b',
    'label': '',
    'source_paths': [{
        'id': 'rect',
        'type': 'RectanglePath',
        'label': 'Rect',
        'closed': True,
        'role': 'outer',
        'visible': True,
        'x': 50, 'y': 50, 'w': 100, 'h': 80,
    }],
    'offset_treatments': [],
    'lattice_instances': [],
    'constraints': {'start_path_id': None, 'start_t': None,
                    'reverse_direction': False, 'component_order': None},
}

# Workflow C: Offset direction conversion — inside (positive distance) vs outside (negative)
_WF_C_SOURCE = {
    'id': 'wf_c',
    'label': '',
    'source_paths': [{
        'id': 'sq',
        'type': 'ExplicitPath',
        'label': 'Square',
        'closed': True,
        'role': 'outer',
        'visible': True,
        'control_points': [[0, 0], [100, 0], [100, 100], [0, 100]],
    }],
    'offset_treatments': [],
    'lattice_instances': [],
    'constraints': {'start_path_id': None, 'start_t': None,
                    'reverse_direction': False, 'component_order': None},
}

# Workflow D: Closed drawn path routes as a single print run (0 travel)
_WF_D_PAYLOAD = {
    'id': 'wf_d',
    'label': '',
    'source_paths': [{
        'id': 'loop',
        'type': 'ExplicitPath',
        'label': 'Loop',
        'closed': True,
        'role': 'outer',
        'visible': True,
        'control_points': [[10, 10], [90, 10], [90, 90], [10, 90]],
    }],
    'offset_treatments': [],
    'lattice_instances': [],
    'constraints': {'start_path_id': None, 'start_t': None,
                    'reverse_direction': False, 'component_order': None},
}

# Workflow E: Two lines + zigzag lattice → effective paths includes lattice geometry
_WF_E_PAYLOAD = {
    'id': 'wf_e',
    'label': '',
    'source_paths': [
        {
            'id': 'top',
            'type': 'ExplicitPath',
            'label': 'Top',
            'closed': False,
            'role': 'outer',
            'visible': True,
            'control_points': [[0, 40], [100, 40]],
        },
        {
            'id': 'bot',
            'type': 'ExplicitPath',
            'label': 'Bottom',
            'closed': False,
            'role': 'inner',
            'visible': True,
            'control_points': [[0, 10], [100, 10]],
        },
    ],
    'offset_treatments': [],
    'lattice_instances': [{
        'id': 'li1',
        'generator': 'zigzag',
        'path_a_id': 'top',
        'path_b_id': 'bot',
        'params': {'segments': 4, 'connect_ends': 0},
        'variation_index': 0,
        'label': '',
    }],
    'constraints': {'start_path_id': None, 'start_t': None,
                    'reverse_direction': False, 'component_order': None},
}


class TestWorkflows:
    def test_wf_a_drawn_path_via_control_points_routes(self, client):
        """Drawn path sent as control_points (no points key) must reach routing engine."""
        r = client.post('/api/route',
                        data=json.dumps(_WF_A_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0

    def test_wf_a_drawn_path_effective_paths(self, client):
        """Drawn path via control_points must appear in effective paths."""
        r = client.post('/api/effective_paths',
                        data=json.dumps(_WF_A_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert any(p['id'] == 'drawn' for p in data['paths'])

    def test_wf_b_rectangle_routes(self, client):
        """Rectangle primitive produces routable moves."""
        r = client.post('/api/route',
                        data=json.dumps(_WF_B_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0
        assert data['metrics']['print_runs'] >= 1

    def test_wf_c_inside_offset_positive_distance(self, client):
        """Inside offset (positive distance) produces a different path than outside offset."""
        payload_in = dict(_WF_C_SOURCE)
        payload_in = {**_WF_C_SOURCE, 'offset_treatments': [{
            'id': 'ot_in', 'source_path_id': 'sq',
            'distance': 5, 'role': 'inner', 'label': '',
        }]}
        payload_out = {**_WF_C_SOURCE, 'id': 'wf_c2', 'offset_treatments': [{
            'id': 'ot_out', 'source_path_id': 'sq',
            'distance': -5, 'role': 'outer', 'label': '',
        }]}
        r_in  = client.post('/api/effective_paths', data=json.dumps(payload_in),
                            content_type='application/json')
        r_out = client.post('/api/effective_paths', data=json.dumps(payload_out),
                            content_type='application/json')
        assert r_in.status_code == 200
        assert r_out.status_code == 200
        # Both offsets must differ from source AND from each other
        paths_in  = r_in.get_json()['paths']
        paths_out = r_out.get_json()['paths']
        derived_in  = next(p for p in paths_in  if p['id'] != 'sq')
        derived_out = next(p for p in paths_out if p['id'] != 'sq')
        source_in   = next(p for p in paths_in  if p['id'] == 'sq')
        # Derived paths from +5 and -5 must not be identical
        assert any(
            abs(pi[0] - po[0]) > 0.1 or abs(pi[1] - po[1]) > 0.1
            for pi, po in zip(derived_in['points'], derived_out['points'])
        ), "inside and outside offsets produced identical geometry"
        # Both must differ from source
        assert any(
            abs(sp[0] - dp[0]) > 0.1 or abs(sp[1] - dp[1]) > 0.1
            for sp, dp in zip(source_in['points'], derived_in['points'])
        ), "inside offset did not change geometry"

    def test_wf_d_closed_loop_zero_travel(self, client):
        """Single closed drawn path should route with zero travel moves."""
        r = client.post('/api/route',
                        data=json.dumps(_WF_D_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert data['metrics']['travel_moves'] == 0
        assert data['metrics']['pct_printing'] == 100.0

    def test_wf_e_lattice_adds_connecting_geometry(self, client):
        """Zigzag lattice between two boundary paths adds geometry beyond the two source paths."""
        r = client.post('/api/effective_paths',
                        data=json.dumps(_WF_E_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        source_ids = {'top', 'bot'}
        all_ids = {p['id'] for p in data['paths']}
        assert len(all_ids - source_ids) >= 1, "lattice produced no additional geometry"

    def test_wf_e_lattice_routes(self, client):
        """Two boundary paths + zigzag lattice route to at least one move."""
        r = client.post('/api/route',
                        data=json.dumps(_WF_E_PAYLOAD),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0

    def test_wf_f_open_path_routes_as_path(self, client):
        """Single open ExplicitPath routes as a continuous path (not a circuit)."""
        payload = {
            'id': 'wf_f',
            'label': '',
            'source_paths': [{
                'id': 'line',
                'type': 'ExplicitPath',
                'label': 'Line',
                'closed': False,
                'role': 'free',
                'visible': True,
                'control_points': [[0, 50], [50, 50], [100, 50]],
            }],
            'offset_treatments': [],
            'lattice_instances': [],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }
        r = client.post('/api/route',
                        data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert data['metrics']['travel_moves'] == 0
        # An open path is an Eulerian path (2 odd-degree nodes) — still zero travel

    def test_wf_g_lattice_with_offset_boundary_routes(self, client):
        """1 source + 1 offset + lattice referencing offset id as boundary → routes."""
        payload = {
            'id': 'wf_g',
            'label': '',
            'source_paths': [{
                'id': 'wall',
                'type': 'RectanglePath',
                'label': 'Wall',
                'closed': True,
                'role': 'outer',
                'visible': True,
                'x': 0, 'y': 0, 'w': 100, 'h': 100,
            }],
            'offset_treatments': [{
                'id': 'inner',
                'source_path_id': 'wall',
                'distance': 10,
                'role': 'inner',
                'label': '',
            }],
            'lattice_instances': [{
                'id': 'lat1',
                'generator': 'zigzag',
                'path_a_id': 'wall',
                'path_b_id': 'inner',
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
        assert data['metrics']['print_runs'] >= 1
        # Effective paths must include both source, derived offset, and lattice
        paths = data['layer']['paths']
        ids = {p['id'] for p in paths}
        assert 'wall' in ids
        assert 'inner' in ids
        lattice_paths = [p for p in paths if p.get('role') == 'lattice']
        assert len(lattice_paths) >= 1

    def test_wf_g_offset_distance_accuracy(self, client):
        """Rectangle inside offset by 10 in: derived path corners must be exactly 10 in inward."""
        payload = {
            'id': 'wf_g2',
            'label': '',
            'source_paths': [{
                'id': 'sq',
                'type': 'RectanglePath',
                'label': 'Square',
                'closed': True,
                'role': 'outer',
                'visible': True,
                'x': 0, 'y': 0, 'w': 100, 'h': 100,
            }],
            'offset_treatments': [{
                'id': 'inner',
                'source_path_id': 'sq',
                'distance': 10,
                'role': 'inner',
                'label': '',
            }],
            'lattice_instances': [],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }
        r = client.post('/api/effective_paths',
                        data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        derived = next(p for p in r.get_json()['paths'] if p['id'] == 'inner')
        pts = derived['points']
        xs = sorted(set(round(p[0], 6) for p in pts))
        ys = sorted(set(round(p[1], 6) for p in pts))
        assert xs == pytest.approx([10.0, 90.0], abs=1e-6)
        assert ys == pytest.approx([10.0, 90.0], abs=1e-6)
