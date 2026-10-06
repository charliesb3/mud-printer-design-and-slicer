"""
Material / Bead (first pass): the physical bead around every PRINTABLE
centerline. Bead Width is carried with the layer but changes no geometry;
only resolved printable centerlines (the router's strands) get a bead.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from model import (Vec2, PrintLayer, LinePath, QuadBezierPath, CirclePath, RectanglePath,
                   WallSpec, Opening, RegionInfill)
from material import MaterialSpec, in_footprint, DEFAULT_BEAD_WIDTH
from trim_fixtures import circle_rect, pick
from test_junction_rounding import manual_fixture, L_corner


def printable(lay):
    paths, meta = lay._build_effective()
    return [(c['id'], [Vec2(*q) for q in c['pts']], c['closed'])
            for c in lay.printable_centerlines(paths, meta)]


def covered(p, cls, w=DEFAULT_BEAD_WIDTH):
    return any(in_footprint(p, pts, closed, w) for _, pts, closed in cls)


# ---------------------------------------------------------------------------
# The footprint itself
# ---------------------------------------------------------------------------

class TestFootprint:
    line = [Vec2(0, 0), Vec2(100, 0)]

    def test_straight_open_line_is_a_capsule(self):
        w = 3.0
        assert in_footprint(Vec2(50, 1.49), self.line, False, w)
        assert not in_footprint(Vec2(50, 1.51), self.line, False, w)          # width 3
        assert in_footprint(Vec2(101.49, 0), self.line, False, w)             # round end
        assert not in_footprint(Vec2(101.51, 0), self.line, False, w)
        assert not in_footprint(Vec2(101.2, 1.2), self.line, False, w)        # not square
        assert in_footprint(Vec2(101.0, 1.0), self.line, False, w)            # on the arc side

    def test_curved_path_sweeps_continuously(self):
        q = QuadBezierPath(Vec2(0, 0), Vec2(100, 0), Vec2(50, 80)).sample_points()
        for a, b in zip(q, q[1:]):
            m = a.lerp(b, 0.5)
            d = (b - a).normalized()
            n = Vec2(-d.y, d.x)
            assert in_footprint(m + n * 1.4, q, False, 3.0)
            assert in_footprint(m - n * 1.4, q, False, 3.0)

    def test_closed_path_has_no_ends(self):
        c = CirclePath(0, 0, 50).sample_points()
        assert in_footprint(Vec2(50 + 1.4, 0), c, True, 3.0)
        assert not in_footprint(Vec2(0, 0), c, True, 3.0)

    def test_material_spec(self):
        assert MaterialSpec().bead_width == 3.0
        assert MaterialSpec.from_dict({'bead_width': 4.25}).bead_width == 4.25
        assert MaterialSpec.from_dict(None).bead_width == 3.0
        assert MaterialSpec.from_dict({'bead_width': -1}).bead_width == 0.25


# ---------------------------------------------------------------------------
# What prints gets a bead; nothing else does
# ---------------------------------------------------------------------------

class TestPrintable:
    def test_printable_is_exactly_the_routed_strands(self):
        lay = manual_fixture(20.0)
        paths, meta = lay._build_effective()
        rl = lay.to_routing_layer(paths, meta)
        assert [c['id'] for c in lay.printable_centerlines(paths, meta)] == [s.id for s in rl.strands]

    def test_centred_wall_reference_line_gets_no_bead(self):
        L = LinePath(Vec2(100, 200), Vec2(300, 200), id='L')
        L.wall = WallSpec(10, 'center')                   # reference not printed
        cls = printable(PrintLayer('t', source_paths=[L]))
        assert 'L' not in [i for i, _, _ in cls]
        assert not covered(Vec2(200, 200), cls)           # centre of a 10 in wall: empty
        assert covered(Vec2(200, 205), cls) and covered(Vec2(200, 195), cls)   # both faces

    def test_thick_wall_faces_and_caps(self):
        L = LinePath(Vec2(100, 200), Vec2(300, 200), id='L')
        L.wall = WallSpec(10, 'center')
        cls = printable(PrintLayer('t', source_paths=[L], cap_style='full_round'))
        assert covered(Vec2(95, 200), cls)                # full-round cap printed

    def test_trimmed_section_gets_no_bead(self):
        base = circle_rect()
        t = pick(base, 'C', lambda s: s['inside'].get('R') is True)
        lay = circle_rect(trims=[t])
        cls = printable(lay)
        assert not covered(Vec2(260, 180), cls)           # the removed arc (leftmost point)
        assert covered(Vec2(420, 180), cls)               # the kept arc
        assert covered(Vec2(260, 180), printable(circle_rect()))   # control

    def test_opening_gap_gets_no_bead(self):
        R = RectanglePath(100, 100, 200, 120, id='R', closed=True)
        R.wall = WallSpec(10)
        lay = PrintLayer('t', source_paths=[R], openings=[Opening('o', 'R', 100.0, 30.0)])
        cls = printable(lay)
        assert not covered(Vec2(200, 100), cls) and not covered(Vec2(200, 110), cls)
        assert covered(Vec2(130, 100), cls)

    def test_rounded_junction_arcs_get_beads(self):
        lay = manual_fixture(40.0)
        paths, meta = lay._build_effective()
        arcs = [p for p in paths if p.id.startswith('junction:')]
        assert arcs
        ids = {c['id'] for c in lay.printable_centerlines(paths, meta)}
        assert all(a.id in ids for a in arcs)

    def test_wall_lattice_infill_gets_beads(self):
        lay = circle_rect(wall=10)
        lay.infills = [RegionInfill('I', 'C', 'wave', {'spacing': 20})]
        paths, meta = lay._build_effective()
        lat = {p.id for p in paths if getattr(p, 'treatment_id', '') == 'infill'}
        assert lat and lat <= {c['id'] for c in lay.printable_centerlines(paths, meta)}

    def test_solid_infill_gets_beads(self):
        R = RectanglePath(100, 100, 200, 120, id='R', closed=True)
        lay = PrintLayer('t', source_paths=[R],
                         infills=[RegionInfill('I', 'R', 'rectilinear', {'spacing': 20}, kind='solid')])
        paths, meta = lay._build_effective()
        solid = {p.id for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill'}
        assert solid and solid <= {c['id'] for c in lay.printable_centerlines(paths, meta)}
        line = next(p for p in paths if p.id in solid).sample_points()
        mid = line[len(line) // 2]
        assert covered(mid, printable(lay))                # the deposited infill lines
        # (at 20 in spacing a 3 in bead leaves gaps — visible, not hidden)

    def test_bead_width_changes_no_geometry(self):
        a = manual_fixture(20.0)
        b = manual_fixture(20.0)
        b.material = MaterialSpec(6.5)
        pa, pb = printable(a), printable(b)
        assert [(i, [(q.x, q.y) for q in p], c) for i, p, c in pa] == \
               [(i, [(q.x, q.y) for q in p], c) for i, p, c in pb]


def test_api_returns_printable_and_material():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    payload = {'id': 'l', 'source_paths': [
        {'id': 'L', 'type': 'LinePath', 'start': [100, 200], 'end': [300, 200],
         'wall': {'thickness': 10, 'align': 'center'}}],
        'offset_treatments': [], 'lattice_instances': [], 'material': {'bead_width': 4.5}}
    with flask_app.test_client() as c:
        for url in ('/api/effective_paths', '/api/route'):
            d = c.post(url, json=payload).get_json()
            assert d['material']['bead_width'] == 4.5      # (+ the physical-rule fields, 2026-10-06)
            ids = [p['id'] for p in d['printable']]
            assert ids and 'L' not in ids
            assert all(len(p['pts']) >= 2 for p in d['printable'])


@pytest.mark.skipif(__import__('shutil').which('node') is None, reason='node not installed')
def test_ui_bead_smoke():
    import subprocess
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_bead_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
