"""
Integration tests for the Flask app.

Verifies that all static assets referenced in the HTML are actually served,
and that the API endpoints return well-formed responses.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import re
import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


class TestStaticAssets:
    def test_index_loads(self, client):
        r = client.get('/')
        assert r.status_code == 200
        assert b'TOOLPATH PROTOTYPE' in r.data

    def test_all_script_srcs_return_200(self, client):
        """Every <script src="..."> in index.html must be reachable."""
        r = client.get('/')
        html = r.data.decode()
        srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        assert srcs, "No <script src> found in index.html"
        for src in srcs:
            resp = client.get(src)
            assert resp.status_code == 200, \
                f"Script src '{src}' returned {resp.status_code} — likely wrong path"

    def test_all_link_hrefs_return_200(self, client):
        """Every <link href="..."> stylesheet in index.html must be reachable."""
        r = client.get('/')
        html = r.data.decode()
        hrefs = re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', html)
        for href in hrefs:
            if href.startswith('http'):
                continue  # skip external CDN links
            resp = client.get(href)
            assert resp.status_code == 200, \
                f"Link href '{href}' returned {resp.status_code}"


class TestAPI:
    def test_list_cases(self, client):
        r = client.get('/api/cases')
        assert r.status_code == 200
        data = r.get_json()
        ids = [c['id'] for c in data]
        assert ids == ['A', 'B', 'C', 'D', 'E']

    def test_route_all_cases(self, client):
        for case_id in ['A', 'B', 'C', 'D', 'E']:
            r = client.post(f'/api/route/{case_id}')
            assert r.status_code == 200
            data = r.get_json()
            assert 'moves' in data
            assert 'metrics' in data
            assert 'graph' in data
            assert len(data['moves']) > 0

    def test_route_unknown_case_returns_404(self, client):
        r = client.post('/api/route/Z')
        assert r.status_code == 404

    def test_case_d_zero_travel(self, client):
        r = client.post('/api/route/D')
        data = r.get_json()
        assert data['metrics']['travel_moves'] == 0
        assert data['metrics']['pct_printing'] == 100.0

    def test_route_response_has_required_fields(self, client):
        r = client.post('/api/route/A')
        data = r.get_json()
        assert 'case_id' in data
        assert 'layer' in data
        assert 'moves' in data
        assert 'graph' in data
        assert 'metrics' in data
        for key in ('print_distance', 'travel_distance', 'print_runs',
                    'travel_moves', 'pct_printing'):
            assert key in data['metrics']
