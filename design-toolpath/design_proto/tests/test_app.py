"""
Integration tests for the design canvas Flask app.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import re
import math
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

    # ------------------------------------------------------------------
    # Geometry validation test cases (spec items A–G)
    # ------------------------------------------------------------------

    def _concentric_circles_payload(self, r_outer=60, r_inner=None, offset_dist=10,
                                    generator='zigzag', gen_params=None):
        """Payload: single circle + inside offset + lattice."""
        if r_inner is not None:
            # Two explicit circles, no offset treatment
            return {
                'id': 'cc',
                'label': '',
                'source_paths': [
                    {'id': 'outer', 'type': 'CirclePath', 'label': 'Outer',
                     'closed': True, 'role': 'outer', 'visible': True,
                     'cx': 200, 'cy': 200, 'radius': r_outer},
                    {'id': 'inner', 'type': 'CirclePath', 'label': 'Inner',
                     'closed': True, 'role': 'inner', 'visible': True,
                     'cx': 200, 'cy': 200, 'radius': r_inner},
                ],
                'offset_treatments': [],
                'lattice_instances': [{
                    'id': 'lat', 'generator': generator,
                    'path_a_id': 'outer', 'path_b_id': 'inner',
                    'params': gen_params or {'segments': 16, 'connect_ends': False},
                    'variation_index': 0, 'label': '',
                }],
                'constraints': {'start_path_id': None, 'start_t': None,
                                'reverse_direction': False, 'component_order': None},
            }
        return {
            'id': 'cc',
            'label': '',
            'source_paths': [
                {'id': 'outer', 'type': 'CirclePath', 'label': 'Outer',
                 'closed': True, 'role': 'outer', 'visible': True,
                 'cx': 200, 'cy': 200, 'radius': r_outer},
            ],
            'offset_treatments': [{
                'id': 'inner', 'source_path_id': 'outer',
                'distance': offset_dist, 'role': 'inner', 'label': '',
            }],
            'lattice_instances': [{
                'id': 'lat', 'generator': generator,
                'path_a_id': 'outer', 'path_b_id': 'inner',
                'params': gen_params or {'segments': 16, 'connect_ends': False},
                'variation_index': 0, 'label': '',
            }],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }

    def _nested_rects_payload(self, generator='zigzag', gen_params=None):
        """Payload: outer 100×100 rect + inner 60×60 rect + lattice."""
        return {
            'id': 'nr',
            'label': '',
            'source_paths': [
                {'id': 'outer', 'type': 'RectanglePath', 'label': 'Outer',
                 'closed': True, 'role': 'outer', 'visible': True,
                 'x': 0, 'y': 0, 'w': 100, 'h': 100},
                {'id': 'inner', 'type': 'RectanglePath', 'label': 'Inner',
                 'closed': True, 'role': 'inner', 'visible': True,
                 'x': 20, 'y': 20, 'w': 60, 'h': 60},
            ],
            'offset_treatments': [],
            'lattice_instances': [{
                'id': 'lat', 'generator': generator,
                'path_a_id': 'outer', 'path_b_id': 'inner',
                'params': gen_params or {'segments': 8, 'connect_ends': False},
                'variation_index': 0, 'label': '',
            }],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }

    def test_wf_geo_a_concentric_circles_zigzag_routes(self, client):
        """Case A: concentric circles + zigzag routes without error."""
        payload = self._concentric_circles_payload(r_outer=60, r_inner=40,
                                                   generator='zigzag',
                                                   gen_params={'segments': 16, 'connect_ends': False})
        r = client.post('/api/route', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0
        lattice = [p for p in data['layer']['paths'] if p.get('role') == 'lattice']
        assert len(lattice) >= 1

    def test_wf_geo_b_concentric_circles_wave_routes(self, client):
        """Case B: concentric circles + wave routes without error."""
        payload = self._concentric_circles_payload(r_outer=60, r_inner=40,
                                                   generator='wave',
                                                   gen_params={'cycles': 4.0, 'phase': 0.0})
        r = client.post('/api/route', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0

    def test_wf_geo_c_nested_rects_zigzag_routes(self, client):
        """Case C: nested rectangles + zigzag routes and covers all 4 sides."""
        payload = self._nested_rects_payload(generator='zigzag',
                                             gen_params={'segments': 8, 'connect_ends': False})
        r = client.post('/api/effective_paths', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        paths = r.get_json()['paths']
        lattice = [p for p in paths if p.get('role') == 'lattice']
        assert len(lattice) >= 1
        # Collect all lattice points and check coverage of all 4 sides
        all_pts = [pt for lp in lattice for pt in lp['points']]
        near_bottom = any(y < 5 for _, y in all_pts)
        near_top    = any(y > 95 for _, y in all_pts)
        near_left   = any(x < 5 for x, _ in all_pts)
        near_right  = any(x > 95 for x, _ in all_pts)
        assert near_bottom and near_top and near_left and near_right, \
            "Zigzag does not cover all 4 sides of the rectangle"

    def test_wf_geo_d_nested_rects_wave_routes(self, client):
        """Case D: nested rectangles + wave routes without error."""
        payload = self._nested_rects_payload(generator='wave',
                                             gen_params={'cycles': 3.0})
        r = client.post('/api/route', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['moves']) > 0

    def test_wf_geo_e_low_segment_count_auto_increases(self, client):
        """Case E: very low segment count on tight circles → auto-increase, no error."""
        payload = self._concentric_circles_payload(r_outer=60, r_inner=55,
                                                   generator='zigzag',
                                                   gen_params={'segments': 2, 'connect_ends': False})
        r = client.post('/api/effective_paths', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        paths = r.get_json()['paths']
        lattice = [p for p in paths if p.get('role') == 'lattice']
        assert len(lattice) >= 1
        # All lattice points must be in the annular cavity (r ∈ [55, 60])
        for lp in lattice:
            for x, y in lp['points']:
                r_pt = math.hypot(x - 200, y - 200)
                assert 54.5 <= r_pt <= 60.5, \
                    f"Auto-increased lattice point r={r_pt:.2f} outside cavity"

    def test_wf_geo_f_irregular_closed_source_offset_lattice(self, client):
        """Case F: irregular closed source + generated offset + zigzag lattice inside cavity."""
        payload = {
            'id': 'irr',
            'label': '',
            'source_paths': [{
                'id': 'wall',
                'type': 'ExplicitPath',
                'label': 'Wall',
                'closed': True,
                'role': 'outer',
                'visible': True,
                'control_points': [[50,200],[100,150],[150,200],[100,250]],
            }],
            'offset_treatments': [{
                'id': 'inner', 'source_path_id': 'wall',
                'distance': 8, 'role': 'inner', 'label': '',
            }],
            'lattice_instances': [{
                'id': 'lat', 'generator': 'zigzag',
                'path_a_id': 'wall', 'path_b_id': 'inner',
                'params': {'segments': 6, 'connect_ends': False},
                'variation_index': 0, 'label': '',
            }],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }
        r = client.post('/api/effective_paths', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        lattice = [p for p in data['paths'] if p.get('role') == 'lattice']
        assert len(lattice) >= 1

    def test_wf_geo_g_dimensions_flag_does_not_alter_geometry(self, client):
        """Case G: adding a 'show_dimensions' flag must not change effective paths."""
        base_payload = {**SIMPLE_PAYLOAD, 'offset_treatments': [{
            'id': 'ot1', 'source_path_id': 'p1',
            'distance': -5, 'role': 'inner', 'label': '',
        }]}
        r1 = client.post('/api/effective_paths', data=json.dumps(base_payload),
                         content_type='application/json')
        payload_with_flag = {**base_payload, 'show_dimensions': True}
        r2 = client.post('/api/effective_paths', data=json.dumps(payload_with_flag),
                         content_type='application/json')
        assert r1.status_code == 200
        assert r2.status_code == 200
        pts1 = {p['id']: p['points'] for p in r1.get_json()['paths']}
        pts2 = {p['id']: p['points'] for p in r2.get_json()['paths']}
        assert pts1 == pts2, "Dimensions flag altered effective geometry"

    def test_wf_geo_wave_params_no_amplitude(self, client):
        """Wave generator API must not expose amplitude, samples, or phase parameters."""
        data = client.get('/api/generators').get_json()
        wave = next(g for g in data if g['name'] == 'wave')
        param_names = [p['name'] for p in wave['parameters']]
        assert 'amplitude' not in param_names
        assert 'samples' not in param_names
        assert 'phase' not in param_names
        assert 'cycles' in param_names

    def test_wave_closed_circles_zero_travel(self, client):
        """Wave lattice on concentric circles routes with zero travel moves."""
        payload = self._concentric_circles_payload(
            r_outer=60, r_inner=40,
            generator='wave',
            gen_params={'cycles': 3.0},
        )
        r = client.post('/api/route', data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        data = r.get_json()
        assert data['metrics']['travel_moves'] == 0, \
            f"Wave on closed circles should have 0 travel, got {data['metrics']['travel_moves']}"

    def test_open_line_offset_generates_caps(self, client):
        """Open source + offset produces two cap paths in effective_paths."""
        payload = {
            'id': 'caps_test',
            'label': '',
            'source_paths': [{
                'id': 'line',
                'type': 'LinePath',
                'label': 'Line',
                'closed': False,
                'role': 'free',
                'visible': True,
                'start': [0, 50],
                'end': [200, 50],
            }],
            'offset_treatments': [{
                'id': 'ot1', 'source_path_id': 'line',
                'distance': 20, 'role': 'inner', 'label': '',
            }],
            'lattice_instances': [],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }
        r = client.post('/api/effective_paths',
                        data=json.dumps(payload),
                        content_type='application/json')
        assert r.status_code == 200
        paths = r.get_json()['paths']
        cap_paths = [p for p in paths if p.get('role') == 'cap']
        assert len(cap_paths) == 2, f"Expected 2 cap paths, got {len(cap_paths)}"

    def test_open_line_offset_routes_single_run(self, client):
        """Open source + offset with auto caps routes as 1 run, 0 travel."""
        payload = {
            'id': 'openwall',
            'label': '',
            'source_paths': [{
                'id': 'line',
                'type': 'LinePath',
                'label': 'Line',
                'closed': False,
                'role': 'free',
                'visible': True,
                'start': [0, 50],
                'end': [200, 50],
            }],
            'offset_treatments': [{
                'id': 'ot1', 'source_path_id': 'line',
                'distance': 20, 'role': 'inner', 'label': '',
            }],
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
        assert data['metrics']['print_runs'] == 1

    def test_reverse_direction_reverses_route_order(self, client):
        """Reverse direction produces a route traversed in the opposite order."""
        base = {
            'id': 'rev_test',
            'label': '',
            'source_paths': [{
                'id': 'p1', 'type': 'ExplicitPath', 'label': 'P',
                'closed': False, 'role': 'free', 'visible': True,
                'control_points': [[0, 0], [50, 0], [100, 0]],
            }],
            'offset_treatments': [],
            'lattice_instances': [],
            'constraints': {'start_path_id': None, 'start_t': None,
                            'reverse_direction': False, 'component_order': None},
        }
        fwd = client.post('/api/route', data=json.dumps(base),
                          content_type='application/json').get_json()
        rev_payload = {**base, 'constraints': {**base['constraints'],
                                               'reverse_direction': True}}
        rev = client.post('/api/route', data=json.dumps(rev_payload),
                          content_type='application/json').get_json()
        fwd_start = fwd['moves'][0]['start']
        fwd_last_end = fwd['moves'][-1]['end']
        rev_start = rev['moves'][0]['start']
        # Reversed route starts where the forward route ended
        assert rev_start[0] == pytest.approx(fwd_last_end[0], abs=0.01)
        assert rev_start[1] == pytest.approx(fwd_last_end[1], abs=0.01)
        # Reversed route first start ≠ forward first start
        assert fwd_start != rev_start

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
