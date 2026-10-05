"""
Pass 5 — design-model features:

  1. Extra Offsets on several sources / several offsets on one source,
     reassigned / deleted / moved sources (backend side).
  4. Explicit infill REGION / VOID semantics (geometric nesting).
  5. Parametric inset / outset (InsetPath: design geometry derived from
     a parent closed path).
  6. WALL vs SOLID infill (RegionInfill.kind; solid.py area fill).
  + RectanglePath rotation (transform tools keep parametric identity).
  + UI edit smoke test (undo / redo, copy / paste / duplicate / rotate,
    offset source picker, inset, infill region) via node.
"""
import sys, os, math, json, shutil, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import pytest

import app as design_app
from model import (Vec2, PrintLayer, RectanglePath, CirclePath, EllipsePath,
                   ExplicitPath, LinePath, InsetPath, RegionInfill, OffsetTreatment,
                   WallSpec, _polygon_area, _point_in_polygon)
import route_quality as RQ
import solid as SO


def _pts(p):
    return [(round(q.x, 6), round(q.y, 6)) for q in p.sample_points()]


def _by_id(paths, pid):
    return next(p for p in paths if p.id == pid)


def _infill_info(layer, fid='S'):
    return next(i for i in layer.network_summary()['infills'] if i['id'] == fid)


@pytest.fixture
def client():
    design_app.app.config['TESTING'] = True
    with design_app.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Extra Offsets — any source, many per source
# ---------------------------------------------------------------------------

class TestExtraOffsets:

    def _layer(self, offs):
        return PrintLayer('t', source_paths=[
            RectanglePath(100, 140, 200, 120, id='R', label='Rect 1'),
            LinePath(Vec2(50, 50), Vec2(150, 50), id='L1', label='Line 1'),
            LinePath(Vec2(50, 350), Vec2(150, 350), id='L2', label='Line 2')],
            offset_treatments=offs)

    def _off_pts(self, layer, oid):
        return _pts(_by_id(layer.effective_paths(), oid))

    def test_offsets_on_two_separate_lines(self):
        L = self._layer([OffsetTreatment('o1', 'L1', 10), OffsetTreatment('o2', 'L2', -10)])
        a, b = self._off_pts(L, 'o1'), self._off_pts(L, 'o2')
        assert all(abs(y - 60) < 1e-6 for _, y in a)      # left of L1 (+y)
        assert all(abs(y - 340) < 1e-6 for _, y in b)     # right of L2 (−y)

    def test_two_offsets_on_one_source(self):
        L = self._layer([OffsetTreatment('o1', 'L1', 10), OffsetTreatment('o2', 'L1', 20)])
        assert {round(y) for _, y in self._off_pts(L, 'o1')} == {60}
        assert {round(y) for _, y in self._off_pts(L, 'o2')} == {70}

    def test_offset_on_rect_and_line_together(self):
        L = self._layer([OffsetTreatment('o1', 'R', 10), OffsetTreatment('o2', 'L2', 10)])
        r = self._off_pts(L, 'o1')
        assert min(x for x, _ in r) == pytest.approx(110) and max(x for x, _ in r) == pytest.approx(290)
        assert {round(y) for _, y in self._off_pts(L, 'o2')} == {360}

    def test_changing_source_recomputes(self):
        L = self._layer([OffsetTreatment('o1', 'L1', 10)])
        L.offset_treatments[0].source_path_id = 'L2'
        assert {round(y) for _, y in self._off_pts(L, 'o1')} == {360}

    def test_moving_source_moves_its_offset(self):
        L = self._layer([OffsetTreatment('o1', 'L1', 10)])
        src = _by_id(L.source_paths, 'L1')
        src.start, src.end = Vec2(50, 80), Vec2(150, 80)
        assert {round(y) for _, y in self._off_pts(L, 'o1')} == {90}

    def test_api_offsets_on_lines(self, client):
        payload = {'id': 't', 'source_paths': [
            {'id': 'L1', 'type': 'LinePath', 'start': [50, 50], 'end': [150, 50], 'label': 'Line 1'},
            {'id': 'L2', 'type': 'LinePath', 'start': [50, 350], 'end': [150, 350], 'label': 'Line 2'}],
            'offset_treatments': [{'id': 'o1', 'source_path_id': 'L1', 'distance': 10},
                                  {'id': 'o2', 'source_path_id': 'L1', 'distance': -10},
                                  {'id': 'o3', 'source_path_id': 'L2', 'distance': 10}]}
        d = client.post('/api/route', data=json.dumps(payload), content_type='application/json').get_json()
        ids = {p['id'] for p in d['layer']['paths']}
        assert {'o1', 'o2', 'o3'} <= ids


# ---------------------------------------------------------------------------
# 5. Parametric inset / outset
# ---------------------------------------------------------------------------

def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


class TestInset:

    def _layer(self, parent, **kw):
        return PrintLayer('t', source_paths=[parent, InsetPath(parent.id, id='C', **kw)])

    def _child(self, L):
        return _pts(_by_id(L.effective_paths(), 'C'))

    def test_rect_inset_10(self):
        L = self._layer(RectanglePath(0, 0, 200, 100, id='R'), distance=10)
        assert _bbox(self._child(L)) == pytest.approx((10, 10, 190, 90))

    def test_resize_parent_recomputes(self):
        R = RectanglePath(0, 0, 200, 100, id='R')
        L = self._layer(R, distance=10)
        self._child(L)
        R.w, R.h = 300, 160
        assert _bbox(self._child(L)) == pytest.approx((10, 10, 290, 150))

    def test_changing_distance_recomputes(self):
        L = self._layer(RectanglePath(0, 0, 200, 100, id='R'), distance=10)
        _by_id(L.source_paths, 'C').distance = 12
        assert _bbox(self._child(L)) == pytest.approx((12, 12, 188, 88))

    def test_outset(self):
        L = self._layer(RectanglePath(0, 0, 200, 100, id='R'), distance=10, mode='outset')
        assert _bbox(self._child(L)) == pytest.approx((-10, -10, 210, 110))

    def test_orientation_independent(self):
        # a clockwise drawn closed path insets INWARD too
        cw = ExplicitPath([Vec2(0, 0), Vec2(0, 100), Vec2(200, 100), Vec2(200, 0)],
                          closed=True, id='R')
        assert _polygon_area(cw.sample_points()) < 0
        L = self._layer(cw, distance=10)
        assert _bbox(self._child(L)) == pytest.approx((10, 10, 190, 90))

    def test_circle_and_ellipse(self):
        L = self._layer(CirclePath(0, 0, 50, id='R'), distance=10)
        for x, y in self._child(L):
            assert math.hypot(x, y) == pytest.approx(40, abs=0.2)
        L = self._layer(EllipsePath(0, 0, 80, 50, id='R'), distance=10)
        b = _bbox(self._child(L))
        assert b == pytest.approx((-70, -40, 70, 40), abs=0.3)

    def test_concave_explicit_parent(self):
        U = ExplicitPath([Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(70, 100),
                          Vec2(70, 30), Vec2(30, 30), Vec2(30, 100), Vec2(0, 100)],
                         closed=True, id='R')
        L = self._layer(U, distance=5)
        pts = self._child(L)
        poly = U.sample_points()
        assert len(pts) >= 8
        assert all(_point_in_polygon(Vec2(x, y), poly) for x, y in pts)

    def test_rotated_parent(self):
        R = RectanglePath(0, 0, 200, 100, rotation=math.pi / 2, id='R')
        L = self._layer(R, distance=10)
        # rotated about (100, 50): spans x 50..150, y −50..150
        assert _bbox(self._child(L)) == pytest.approx((60, -40, 140, 140))

    def test_too_large_inset_has_no_shape(self):
        L = self._layer(RectanglePath(0, 0, 20, 20, id='R'), distance=15)
        assert 'C' not in {p.id for p in L.effective_paths()}

    def test_inset_of_inset(self):
        R = RectanglePath(0, 0, 200, 100, id='R')
        L = PrintLayer('t', source_paths=[InsetPath('C', 5, id='D'),   # child listed first
                                          R, InsetPath('R', 10, id='C')])
        assert _bbox(_pts(_by_id(L.effective_paths(), 'D'))) == pytest.approx((15, 15, 185, 85))

    def test_parent_deleted_keeps_frozen_shape(self):
        R = RectanglePath(0, 0, 200, 100, id='R')
        L = self._layer(R, distance=10)
        self._child(L)
        L.source_paths = [p for p in L.source_paths if p.id != 'R']
        assert _bbox(self._child(L)) == pytest.approx((10, 10, 190, 90))

    def test_inset_as_void_of_solid_infill(self):
        R = RectanglePath(0, 0, 200, 100, id='R')
        L = PrintLayer('t', source_paths=[R, InsetPath('R', 30, id='C')],
                       infills=[RegionInfill('S', 'R', 'rectilinear', {'spacing': 8}, kind='solid')])
        info = _infill_info(L)
        assert info['voids'] == ['C'] and info['holes'] == [1]
        for p in L.effective_paths():
            if getattr(p, 'treatment_id', '') == 'solid_infill':
                for q in p.sample_points():
                    assert not (30 < q.x < 170 and 30 < q.y < 70)

    def test_inset_as_wall_geometry(self):
        R = RectanglePath(0, 0, 200, 100, id='R')
        C = InsetPath('R', 20, id='C')
        C.wall = WallSpec(thickness=6, align='inside')
        L = PrintLayer('t', source_paths=[R, C])
        ids = {p.id for p in L.effective_paths()}
        assert 'C.wall' in ids

    def test_api_round_trip_and_derived_points(self, client):
        payload = {'id': 't', 'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 200, 'h': 100, 'closed': True},
            {'id': 'C', 'type': 'InsetPath', 'parent_id': 'R', 'distance': 10, 'mode': 'inset',
             'points': [], 'closed': True}]}
        d = client.post('/api/effective_paths', data=json.dumps(payload),
                        content_type='application/json').get_json()
        ds = d['network']['derived_sources']['C']
        assert _bbox(ds) == pytest.approx((10, 10, 190, 90))
        L = design_app._deserialise_layer(payload)
        c = _by_id(L.source_paths, 'C')
        assert isinstance(c, InsetPath) and c.parent_id == 'R' and c.distance == 10
        assert c.to_dict()['type'] == 'InsetPath'


# ---------------------------------------------------------------------------
# Rotated rectangle stays a parametric rectangle
# ---------------------------------------------------------------------------

class TestRectRotation:

    def test_sample_points_rotate_about_centre(self):
        R = RectanglePath(0, 0, 200, 100, rotation=math.pi / 2)
        assert _bbox(_pts(R)) == pytest.approx((50, -50, 150, 150))

    def test_api_round_trip(self):
        L = design_app._deserialise_layer({'id': 't', 'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 10, 'h': 10, 'rotation': 0.5}]})
        R = L.source_paths[0]
        assert isinstance(R, RectanglePath) and R.rotation == 0.5
        assert R.to_dict()['rotation'] == 0.5


# ---------------------------------------------------------------------------
# 4. Region / void semantics — geometric nesting
# ---------------------------------------------------------------------------

class TestRegionVoids:

    def _solid(self, srcs, region='R', fid='S'):
        return PrintLayer('t', source_paths=srcs, infills=[
            RegionInfill(fid, region, 'rectilinear', {'spacing': 10}, kind='solid')])

    def _lines(self, L):
        return [p for p in L.effective_paths() if getattr(p, 'treatment_id', '') in ('solid_infill', 'solid_link')]

    def _hits(self, L, poly):
        # strictly inside (a touch point ON the void boundary is allowed)
        from network import dist_to_polyline
        return sum(1 for p in self._lines(L) for q in p.sample_points()
                   if _point_in_polygon(q, poly) and dist_to_polyline(q, poly, True) > 1e-6)

    def test_rect_containing_ellipse(self):
        E = EllipsePath(100, 60, 30, 20, id='E')
        L = self._solid([E, RectanglePath(0, 0, 200, 120, id='R')])   # void drawn FIRST
        info = _infill_info(L)
        assert info['region'] == 'R' and info['voids'] == ['E'] and info['holes'] == [1]
        assert self._hits(L, E.sample_points()) == 0
        assert len(self._lines(L)) > 0

    def test_rect_containing_rect(self):
        I = RectanglePath(50, 30, 60, 40, id='I')
        L = self._solid([RectanglePath(0, 0, 200, 120, id='R'), I])
        assert _infill_info(L)['voids'] == ['I']
        assert self._hits(L, I.sample_points()) == 0

    def test_multiple_voids(self):
        A, B = CirclePath(50, 60, 20, id='A'), CirclePath(150, 60, 20, id='B')
        L = self._solid([RectanglePath(0, 0, 200, 120, id='R'), A, B])
        info = _infill_info(L)
        assert info['voids'] == ['A', 'B'] and info['holes'] == [2]
        assert self._hits(L, A.sample_points()) == 0 and self._hits(L, B.sample_points()) == 0

    def test_nested_island(self):
        V = RectanglePath(40, 20, 120, 80, id='V')
        I = RectanglePath(80, 45, 40, 30, id='I')
        L = self._solid([RectanglePath(0, 0, 200, 120, id='R'), V, I])
        info = _infill_info(L)
        assert info['voids'] == ['V'] and info['islands'] == ['I']

    def test_void_moved_outside_is_no_longer_a_void(self):
        E = EllipsePath(100, 60, 30, 20, id='E')
        L = self._solid([RectanglePath(0, 0, 200, 120, id='R'), E])
        assert _infill_info(L)['voids'] == ['E']
        E.cx = 400
        info = _infill_info(L)
        assert info['voids'] == [] and info['holes'] == [0]

    def test_resizing_parent_changes_nesting(self):
        R = RectanglePath(0, 0, 200, 120, id='R')
        E = EllipsePath(260, 60, 30, 20, id='E')
        L = self._solid([R, E])
        assert _infill_info(L)['voids'] == []
        R.w = 400
        assert _infill_info(L)['voids'] == ['E']

    def test_explicitly_filling_the_inner_shape(self):
        E = EllipsePath(100, 60, 40, 30, id='E')
        L = self._solid([RectanglePath(0, 0, 200, 120, id='R'), E], region='E')
        info = _infill_info(L)
        assert info['region'] == 'E' and info['voids'] == [] and info['status'] == 'ok'
        lines = self._lines(L)
        # inside the ellipse (connectors may land ON its boundary)
        assert lines and all(((q.x - 100) / 40) ** 2 + ((q.y - 60) / 30) ** 2 <= 1.0 + 1e-3
                             for p in lines for q in p.sample_points())


# ---------------------------------------------------------------------------
# 6. WALL vs SOLID infill
# ---------------------------------------------------------------------------

def _solid_layer(outer, voids=(), **params):
    prm = {'spacing': 16, 'angle': 45}
    prm.update(params)
    return PrintLayer('t', source_paths=[outer, *voids],
                      infills=[RegionInfill('S', outer.id, 'rectilinear', prm, kind='solid')])


class TestSolidInfill:

    def test_model_distinguishes_kinds(self):
        f = RegionInfill('S', 'R', 'rectilinear', {}, kind='solid')
        assert f.to_dict()['kind'] == 'solid'
        assert RegionInfill('W', 'R').kind == 'wall'          # old payloads → wall
        L = design_app._deserialise_layer({'id': 't', 'source_paths': [], 'infills': [
            {'id': 'S', 'path_id': 'R', 'kind': 'solid', 'pattern': 'rectilinear'}]})
        assert L.infills[0].kind == 'solid'

    def test_solid_never_uses_wall_repair(self):
        L = _solid_layer(RectanglePath(40, 40, 320, 220, id='R'))
        paths = L.effective_paths()
        assert not [p for p in paths if getattr(p, 'treatment_id', '') in ('infill', 'infill_return')]
        assert [p for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill']
        assert (L.network_summary().get('route_plan') or {}).get('corrections', []) == []

    def test_rect_is_one_clean_continuous_field(self):
        q = RQ.quality(_solid_layer(RectanglePath(40, 40, 320, 220, id='R')))
        assert q['runs'] == 1 and q['travel_moves'] == 0
        assert q['exact_retrace'] == 0
        assert q['congestion_hotspots'] == 0 and q['congestion_max_generated'] <= 2

    def test_ellipse(self):
        q = RQ.quality(_solid_layer(EllipsePath(200, 150, 160, 100, id='R')))
        assert q['runs'] == 1 and q['exact_retrace'] == 0 and q['congestion_hotspots'] == 0

    @pytest.mark.parametrize('outer', ['rect', 'ellipse'])
    def test_void_stays_empty_and_no_long_connectors(self, outer):
        O = (RectanglePath(40, 40, 320, 220, id='R') if outer == 'rect'
             else EllipsePath(200, 150, 160, 100, id='R'))
        V = EllipsePath(200, 150, 50, 35, id='V')
        L = _solid_layer(O, [V], spacing=16, angle=0 if outer == 'ellipse' else 45)
        paths = L.effective_paths()
        for p in paths:
            if getattr(p, 'treatment_id', '') in ('solid_infill', 'solid_link'):
                for a, b in zip(p.sample_points(), p.sample_points()[1:]):
                    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                        m = Vec2(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y))
                        assert not _point_in_polygon(m, [Vec2(200 + 49 * math.cos(k / 20 * math.pi),
                                                              150 + 34 * math.sin(k / 20 * math.pi))
                                                         for k in range(40)])
        rep = _infill_info(L)['solid']
        assert rep['max_connector'] <= SO.LINK_MAX * 16 + 1e-6
        q = RQ.quality(L)
        assert q['exact_retrace'] == 0 and q['congestion_hotspots'] == 0
        # disconnected field pieces are joined by SHORT TRAVEL, not long beads
        assert q['runs'] <= 4

    def test_void_perimeter_joined_by_a_turn_touch(self):
        # Pass 7: every turn lands ON the boundary it turns at (outer or
        # void), so the void loop is part of the field's graph at several
        # turns (was: one bent turn). No retrace, no knot.
        L = _solid_layer(RectanglePath(40, 40, 320, 220, id='R'),
                         [EllipsePath(200, 150, 50, 35, id='V')])
        rep = _infill_info(L)['solid']
        assert rep['untouched_boundaries'] == 0
        q = RQ.quality(L)
        assert q['exact_retrace'] == 0 and q['max_generated_degree'] <= 2
        assert q['congestion_max_generated'] <= 2

    def test_island_in_a_void_is_filled(self):
        L = PrintLayer('t', source_paths=[
            EllipsePath(200, 200, 50, 30, id='E'), RectanglePath(80, 110, 240, 180, id='R'),
            InsetPath('R', 10, id='C')],
            infills=[RegionInfill('S', 'R', 'rectilinear', {'spacing': 20}, kind='solid')])
        info = _infill_info(L)
        assert info['voids'] == ['C'] and info['islands'] == ['E'] and info['regions'] == 2
        pts = [q for p in L.effective_paths() if getattr(p, 'treatment_id', '') == 'solid_infill'
               for q in p.sample_points()]
        island = lambda q: ((q.x - 200) / 50) ** 2 + ((q.y - 200) / 30) ** 2 <= 1 + 1e-3
        in_void = lambda q: 90 + 1e-6 < q.x < 310 - 1e-6 and 120 + 1e-6 < q.y < 280 - 1e-6
        # the island is filled; the 10 in band (R − C) is material too and
        # — now that lines are clipped exactly to the boundary — also filled
        # (pass 5 kept a margin, so the band stayed empty); the void C
        # outside the island stays empty
        assert any(island(q) for q in pts)
        assert all(island(q) or not in_void(q) for q in pts)

    def test_lines_follow_spacing_and_angle(self):
        L = _solid_layer(RectanglePath(0, 0, 200, 200, id='R'), spacing=20, angle=0)
        # the field lines (long horizontal runs; turn apexes now land on the
        # boundary between lines, so not every point is on a line)
        ys = sorted({round(a.y, 3) for p in L.effective_paths()
                     if getattr(p, 'treatment_id', '') == 'solid_infill'
                     for a, b in zip(p.sample_points(), p.sample_points()[1:])
                     if abs(a.y - b.y) < 1e-6 and a.dist(b) > 50})
        gaps = {round(b - a, 3) for a, b in zip(ys, ys[1:])}
        assert gaps == {20.0}
        L = _solid_layer(RectanglePath(0, 0, 200, 200, id='R'), spacing=20, angle=90)
        segs = [(a, b) for p in L.effective_paths() if getattr(p, 'treatment_id', '') == 'solid_infill'
                for a, b in zip(p.sample_points(), p.sample_points()[1:])]
        long = [(a, b) for a, b in segs if a.dist(b) > 50]
        assert long and all(abs(a.x - b.x) < 1e-6 for a, b in long)

    def test_extra_perimeters(self):
        L = _solid_layer(RectanglePath(0, 0, 200, 200, id='R'), perimeters=2, perimeter_spacing=6)
        per = [p for p in L.effective_paths() if getattr(p, 'treatment_id', '') == 'solid_perimeter']
        assert len(per) == 1 and _bbox(_pts(per[0])) == pytest.approx((6, 6, 194, 194))
        lines = [q for p in L.effective_paths() if getattr(p, 'treatment_id', '') == 'solid_infill'
                 for q in p.sample_points()]
        assert all(6 - 1e-9 <= q.x <= 194 + 1e-9 and 6 - 1e-9 <= q.y <= 194 + 1e-9 for q in lines)
        assert any(6 + 1e-6 < q.x < 194 - 1e-6 for q in lines)

    def test_wall_infill_unchanged_by_kind_default(self):
        # identical wall-infill output with kind omitted vs kind='wall'
        def lay(**kw):
            R = RectanglePath(0, 0, 200, 120, id='R')
            R.wall = WallSpec(thickness=12, align='inside')
            return PrintLayer('t', source_paths=[R],
                              infills=[RegionInfill('W', 'R', 'zigzag', {'spacing': 20}, **kw)])
        sig = lambda L: sorted((p.id, tuple(_pts(p))) for p in L.effective_paths())
        assert sig(lay()) == sig(lay(kind='wall'))
        assert any(getattr(p, 'treatment_id', '') == 'infill' for p in lay().effective_paths())

    def test_wall_and_solid_together(self):
        R = RectanglePath(0, 0, 200, 120, id='R')
        R.wall = WallSpec(thickness=12, align='inside')
        C = CirclePath(100, 60, 30, id='C')
        L = PrintLayer('t', source_paths=[R, C], infills=[
            RegionInfill('W', 'R', 'zigzag', {'spacing': 20}),
            RegionInfill('S', 'C', 'rectilinear', {'spacing': 10}, kind='solid')])
        tids = {getattr(p, 'treatment_id', '') for p in L.effective_paths()}
        assert {'infill', 'solid_infill'} <= tids
        q = RQ.quality(L)
        assert q['exact_retrace'] == 0 and q['congestion_hotspots'] == 0

    def test_solid_on_open_path_reports_no_material(self):
        L = PrintLayer('t', source_paths=[LinePath(Vec2(0, 0), Vec2(100, 0), id='R')],
                       infills=[RegionInfill('S', 'R', 'rectilinear', {}, kind='solid')])
        assert _infill_info(L)['status'] != 'ok'
        assert not [p for p in L.effective_paths() if getattr(p, 'treatment_id', '') == 'solid_infill']

    def test_api_solid(self, client):
        payload = {'id': 't', 'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'x': 40, 'y': 40, 'w': 320, 'h': 220, 'closed': True},
            {'id': 'V', 'type': 'EllipsePath', 'cx': 200, 'cy': 150, 'rx': 50, 'ry': 35, 'closed': True}],
            'infills': [{'id': 'S', 'path_id': 'R', 'kind': 'solid', 'pattern': 'rectilinear',
                         'params': {'spacing': 20, 'angle': 45, 'perimeters': 1}}]}
        d = client.post('/api/route', data=json.dumps(payload), content_type='application/json').get_json()
        info = d['network']['infills'][0]
        assert info['kind'] == 'solid' and info['voids'] == ['V'] and info['solid']['lines'] > 0
        assert d['metrics']['retrace_distance'] == 0


# ---------------------------------------------------------------------------
# UI edit smoke test (node)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_edit_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_edit_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert 'UI EDIT SMOKE PASSED' in proc.stdout
