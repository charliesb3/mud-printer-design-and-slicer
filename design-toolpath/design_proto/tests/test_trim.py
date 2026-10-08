"""
Trim: non-destructive suppression of source-path SECTIONS (between contacts
with other sources). Sections, signature resolution through parametric
edits, unresolved trims, and the downstream effect on walls / networks /
infill / routing (trimmed geometry is genuinely absent, not just hidden).
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from model import (Vec2, _point_in_polygon, _cum_lengths, _project_to_polyline,
                   RegionInfill, Opening, Trim)
import network as N
from trim_fixtures import (circle_rect, strip_circle, line_rect, curve_line, bars,
                           sections, trim_of, pick)

inside_R = lambda s: s['inside'].get('R') is True
inside_C = lambda s: s['inside'].get('C') is True


def build(layer):
    return layer._build_effective()


def src_pieces(paths, sid):
    """The effective geometry of source sid itself (whole, or its pieces)."""
    return [p for p in paths if p.id == sid or (p.id.startswith(sid + '~') and
            getattr(p, 'treatment_id', '') in ('opening_cut', 'network_src'))]


def poly(layer, sid):
    p = next(s for s in layer.source_paths if s.id == sid)
    return p.sample_points()


def strictly_inside(q, ring, margin=1e-3):
    if not _point_in_polygon(q, ring):
        return False
    return _project_to_polyline(q, ring, _cum_lengths(ring, True), True)[1] > margin


def seg_mids(pts):
    return [a.lerp(b, 0.5) for a, b in zip(pts, pts[1:])]


def route(layer):
    from graph import route_layer, compute_metrics
    paths, meta = build(layer)
    return compute_metrics(route_layer(layer.to_routing_layer(paths, meta)))


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

class TestSections:
    def test_circle_rect_two_sections_each(self):
        secs = sections(circle_rect())
        by = {}
        for s in secs.values():
            by.setdefault(s['source'], []).append(s)
        assert len(by['R']) == 2 and len(by['C']) == 2
        assert sorted(s['inside']['R'] for s in by['C']) == [False, True]
        assert sorted(s['inside']['C'] for s in by['R']) == [False, True]
        for s in secs.values():
            assert s['start'] and s['end']           # bounded by the other path

    def test_sections_meet_at_exact_shared_points(self):
        secs = sections(circle_rect(cy=150, r=95))   # contacts off the rect corners
        ends = lambda sid: {tuple(p) for s in secs.values() if s['source'] == sid
                            for p in (s['pts'][0], s['pts'][-1])}
        assert ends('R') == ends('C') and len(ends('R')) == 2

    def test_open_line_through_rect_three_sections(self):
        secs = [s for s in sections(line_rect()).values() if s['source'] == 'L']
        assert len(secs) == 3
        assert [bool(s['start']) for s in secs] == [False, True, True]
        assert [bool(s['end']) for s in secs] == [True, True, False]
        assert [s['inside']['R'] for s in secs] == [False, True, False]

    def test_isolated_and_singly_touched_paths_have_no_sections(self):
        lay = circle_rect(cx=700)                   # no contact at all
        assert sections(lay) == {}

    def test_strip_circle_four_contacts(self):
        secs = [s for s in sections(strip_circle()).values() if s['source'] == 'C']
        assert len(secs) == 4
        assert sum(1 for s in secs if s['inside']['S']) == 2

    def test_curve_sections(self):
        secs = [s for s in sections(curve_line()).values() if s['source'] == 'Q']
        assert len(secs) == 3                       # the line crosses the curve twice


# ---------------------------------------------------------------------------
# Trimming (thin / single-bead)
# ---------------------------------------------------------------------------

class TestTrimCircleRect:
    def test_1_trim_circle_section_inside_rect(self):
        t = pick(circle_rect(), 'C', inside_R)
        lay = circle_rect(trims=[t])
        paths, meta = build(lay)
        assert meta['network']['trims']['t1']['status'] == 'ok'
        pcs = src_pieces(paths, 'C')
        assert pcs and all(p.id != 'C' for p in pcs)
        rect = poly(lay, 'R')
        for p in pcs:
            assert not any(strictly_inside(q, rect) for q in seg_mids(p.sample_points()))
        assert [p.id for p in src_pieces(paths, 'R')] == ['R']       # untouched
        assert 'C' in meta['network']['modified_sources']

    def test_2_trim_rect_section_inside_circle(self):
        t = pick(circle_rect(), 'R', inside_C)
        lay = circle_rect(trims=[t])
        paths, _ = build(lay)
        circ = poly(lay, 'C')
        pcs = src_pieces(paths, 'R')
        assert pcs and all(p.id != 'R' for p in pcs)
        for p in pcs:
            assert not any(strictly_inside(q, circ) for q in seg_mids(p.sample_points()))

    def test_3_trim_both_leaves_one_closed_outline(self):
        base = circle_rect()
        lay = circle_rect(trims=[pick(base, 'C', inside_R, 't1'), pick(base, 'R', inside_C, 't2')])
        paths, meta = build(lay)
        assert all(v['status'] == 'ok' for v in meta['network']['trims'].values())
        rect, circ = poly(lay, 'R'), poly(lay, 'C')
        for p in src_pieces(paths, 'C'):
            assert not any(strictly_inside(q, rect) for q in seg_mids(p.sample_points()))
        for p in src_pieces(paths, 'R'):
            assert not any(strictly_inside(q, circ) for q in seg_mids(p.sample_points()))
        m = route(lay)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0

    @pytest.mark.parametrize('cx,cy,r', [(355, 180, 80), (340, 200, 80), (325, 165, 80),
                                         (340, 150, 95), (340, 180, 50), (340, 180, 100)])
    def test_4_5_trims_follow_moved_and_resized_circle(self, cx, cy, r):
        base = circle_rect()
        trims = [pick(base, 'C', inside_R, 't1'), pick(base, 'R', inside_C, 't2')]
        lay = circle_rect(cx=cx, cy=cy, r=r, trims=trims)
        paths, meta = build(lay)
        assert all(v['status'] == 'ok' for v in meta['network']['trims'].values())
        rect, circ = poly(lay, 'R'), poly(lay, 'C')
        for p in src_pieces(paths, 'C'):
            assert not any(strictly_inside(q, rect) for q in seg_mids(p.sample_points()))
        for p in src_pieces(paths, 'R'):
            assert not any(strictly_inside(q, circ) for q in seg_mids(p.sample_points()))
        m = route(lay)
        assert m['print_runs'] == 1 and m['travel_moves'] == 0       # exact shared ends

    @pytest.mark.parametrize('rect', [(120, 90, 240, 160), (80, 120, 260, 120), (100, 100, 200, 200)])
    def test_6_trims_follow_moved_and_resized_rect(self, rect):
        base = circle_rect()
        trims = [pick(base, 'C', inside_R, 't1'), pick(base, 'R', inside_C, 't2')]
        lay = circle_rect(rect=rect, trims=trims)
        paths, meta = build(lay)
        assert all(v['status'] == 'ok' for v in meta['network']['trims'].values())
        rect_p = poly(lay, 'R')
        for p in src_pieces(paths, 'C'):
            assert not any(strictly_inside(q, rect_p) for q in seg_mids(p.sample_points()))

    def test_source_is_never_modified(self):
        base = circle_rect()
        lay = circle_rect(trims=[pick(base, 'C', inside_R)])
        before = [p.to_dict() for p in lay.source_paths]
        build(lay)
        assert [p.to_dict() for p in lay.source_paths] == before
        assert type(lay.source_paths[1]).__name__ == 'CirclePath'

    def test_removing_the_trim_restores_the_exact_section(self):
        paths0, _ = build(circle_rect())
        base = circle_rect()
        lay = circle_rect(trims=[pick(base, 'C', inside_R)])
        build(lay)
        lay.trims = []
        paths1, _ = build(lay)
        assert [(p.id, [(q.x, q.y) for q in p.sample_points()]) for p in paths0] == \
               [(p.id, [(q.x, q.y) for q in p.sample_points()]) for p in paths1]

    def test_trims_are_independent_of_order(self):
        base = circle_rect()
        a, b = pick(base, 'C', inside_R, 't1'), pick(base, 'R', inside_C, 't2')
        p1, _ = build(circle_rect(trims=[a, b]))
        p2, _ = build(circle_rect(trims=[b, a]))
        key = lambda ps: sorted((p.id, len(p.sample_points())) for p in ps)
        assert key(p1) == key(p2)


# ---------------------------------------------------------------------------
# Resolution: ambiguity / disappearance
# ---------------------------------------------------------------------------

class TestResolution:
    def test_unresolved_when_the_intersections_disappear(self):
        base = circle_rect()
        t = pick(base, 'C', inside_R)
        lay = circle_rect(cx=700, trims=[t])         # moved away: no contact
        paths, meta = build(lay)
        st = meta['network']['trims']['t1']
        assert st['status'] == 'unresolved' and st['reason']
        assert [p.id for p in src_pieces(paths, 'C')] == ['C']     # shown untrimmed
        assert 'C' not in meta['network']['modified_sources']

    def test_unresolved_when_a_bounding_path_is_deleted(self):
        t = pick(circle_rect(), 'C', inside_R)
        lay = circle_rect(trims=[t])
        lay.source_paths = [p for p in lay.source_paths if p.id != 'R']
        _, meta = build(lay)
        assert meta['network']['trims']['t1']['status'] == 'unresolved'

    def test_missing_source(self):
        t = pick(circle_rect(), 'C', inside_R)
        lay = circle_rect(trims=[t])
        lay.source_paths = [p for p in lay.source_paths if p.id != 'C']
        _, meta = build(lay)
        assert meta['network']['trims']['t1']['status'] == 'missing source'

    def test_8_multiple_intersections_pick_the_clicked_section(self):
        secs = [s for s in sections(strip_circle()).values()
                if s['source'] == 'C' and s['inside']['S']]
        assert len(secs) == 2
        for k, sec in enumerate(secs):
            lay = strip_circle(trims=[trim_of(sec)])
            paths, meta = build(lay)
            assert meta['network']['trims']['t1']['section'] == sec['id']
            other = secs[1 - k]
            mid = Vec2(*other['pts'][len(other['pts']) // 2])
            gone = Vec2(*sec['pts'][len(sec['pts']) // 2])
            kept = [q for p in src_pieces(paths, 'C') for q in p.sample_points()]
            assert min(q.dist(mid) for q in kept) < 1.0        # the other one stays
            assert min(q.dist(gone) for q in kept) > 5.0       # the clicked one is gone

    def test_8b_tie_break_follows_a_small_move(self):
        sec = next(s for s in sections(strip_circle()).values()
                   if s['source'] == 'C' and s['inside']['S'])
        _, meta = build(strip_circle(trims=[trim_of(sec)], dx=12))
        assert meta['network']['trims']['t1']['status'] == 'ok'

    def test_8c_tie_without_a_unique_match_is_ambiguous_not_guessed(self):
        sec = next(s for s in sections(strip_circle()).values()
                   if s['source'] == 'C' and s['inside']['S'])
        t = trim_of(sec)
        # its position now lies in an OUTSIDE section → no inside section contains it
        outside = next(s for s in sections(strip_circle()).values()
                       if s['source'] == 'C' and not s['inside']['S'])
        t.u_mid = outside['u_mid']
        paths, meta = build(strip_circle(trims=[t]))
        assert meta['network']['trims']['t1']['status'] == 'ambiguous'
        assert [p.id for p in src_pieces(paths, 'C')] == ['C']


# ---------------------------------------------------------------------------
# Other sources
# ---------------------------------------------------------------------------

class TestOtherSources:
    def test_9_open_line_middle_and_end_sections(self):
        secs = [s for s in sections(line_rect()).values() if s['source'] == 'L']
        paths, _ = build(line_rect(trims=[trim_of(secs[1])]))        # inside the rect
        pcs = src_pieces(paths, 'L')
        assert len(pcs) == 2
        rect = poly(line_rect(), 'R')
        assert not any(strictly_inside(q, rect) for p in pcs for q in seg_mids(p.sample_points()))
        paths, _ = build(line_rect(trims=[trim_of(secs[0])]))        # the left stub
        pcs = src_pieces(paths, 'L')
        assert len(pcs) == 1 and min(q.x for q in pcs[0].sample_points()) > 99.9

    def test_10_quadratic_curve(self):
        secs = [s for s in sections(curve_line()).values() if s['source'] == 'Q']
        lay = curve_line(trims=[trim_of(secs[1])])
        paths, meta = build(lay)
        assert meta['network']['trims']['t1']['status'] == 'ok'
        assert len(src_pieces(paths, 'Q')) == 2
        assert type(lay.source_paths[0]).__name__ == 'QuadBezierPath'

    def test_12_trim_that_disconnects_the_design(self):
        secs = [s for s in sections(bars()).values() if s['source'] == 'V']
        assert len(secs) == 3
        lay = bars(trims=[trim_of(secs[1])])                  # between the bars
        m = route(lay)
        assert m['print_runs'] == 2 and m['travel_moves'] >= 1


# ---------------------------------------------------------------------------
# Wall Thickness / networks / infill / openings
# ---------------------------------------------------------------------------

class TestWalls:
    def _caps_at(self, paths, pt, tol=1e-6):
        return [p for p in paths if getattr(p, 'treatment_id', '') == 'wall_system' and
                p.label in ('cap_start', 'cap_end') and
                min(q.dist(pt) for q in p.sample_points()) < 5.0]

    @pytest.mark.parametrize('cx,cy,r', [(340, 180, 80), (340, 150, 95)])
    def test_11_trimmed_walls_join_into_one_outline(self, cx, cy, r):
        """Both walls trimmed: the ends meet at the shared contact → hub
        (mitred faces), no end caps left inside the outline."""
        base = circle_rect(wall=10, cx=cx, cy=cy, r=r)
        lay = circle_rect(wall=10, cx=cx, cy=cy, r=r,
                          trims=[pick(base, 'C', inside_R, 't1'), pick(base, 'R', inside_C, 't2')])
        paths, meta = build(lay)
        ends = {tuple(p) for s in meta['network']['trim_sections'] if s['trimmed_by']
                for p in (s['pts'][0], s['pts'][-1])}
        assert len(ends) == 2
        for e in ends:
            assert self._caps_at(paths, Vec2(*e)) == []
        m = route(lay)                              # two faces, no infill: 2 loops
        assert m['print_runs'] == 2 and m['retrace_moves'] == 0

    def test_11b_trimmed_wall_tees_into_the_intact_wall(self):
        base = circle_rect(wall=10)
        lay = circle_rect(wall=10, trims=[pick(base, 'C', inside_R)])
        paths, meta = build(lay)
        caps = [p for p in paths if getattr(p, 'treatment_id', '') == 'wall_system'
                and p.id.startswith('C~')]
        assert caps == []                           # both ends attached (T), no caps
        assert len(meta['network']['components']) == 1

    def test_11c_trimmed_wall_without_a_host_is_capped(self):
        """A thick line through a THIN rect: trimming its middle leaves two
        wall stubs; an end on a single bead is still capped (no T host)."""
        secs = [s for s in sections(line_rect()).values() if s['source'] == 'L']
        lay = line_rect(trims=[trim_of(secs[1])])
        lay.source_paths[1].wall = __import__('model').WallSpec(10, 'center')
        paths, _ = build(lay)
        assert [p for p in paths if p.id.startswith('L~') and getattr(p, 'treatment_id', '') == 'wall_system']

    def test_13_infill_is_absent_where_a_wall_was_trimmed(self):
        def lay_with(trims):
            lay = circle_rect(wall=10, trims=trims)
            lay.infills = [RegionInfill('I', 'C', 'zigzag', {'spacing': 20})]
            return lay
        sec = next(s for s in sections(circle_rect(wall=10)).values()
                   if s['source'] == 'C' and s['inside']['R'])
        arc = [Vec2(*q) for q in sec['pts']]
        core = arc[len(arc) // 4: 3 * len(arc) // 4]          # away from the rect walls

        def near(paths):
            pts = [q for p in paths if getattr(p, 'treatment_id', '') == 'infill'
                   for q in p.sample_points()]
            return min(min(q.dist(c) for q in pts) for c in core)
        p0, _ = build(lay_with([]))
        assert near(p0) < 11.0                       # control: infill ran along that wall
        p1, m1 = build(lay_with([trim_of(sec)]))
        assert near(p1) > 15.0
        assert any(i['status'] == 'ok' for i in m1['network']['infills'])

    def test_13b_trimmed_boundary_no_longer_bounds_a_solid_region(self):
        lay = circle_rect()
        lay.infills = [RegionInfill('I', 'R', 'rectilinear', {'spacing': 20}, kind='solid')]
        _, m0 = build(lay)
        assert m0['network']['infills'][0]['status'] == 'ok'
        lay.trims = [pick(circle_rect(), 'R', inside_C)]
        paths, m1 = build(lay)
        assert m1['network']['infills'][0]['status'] != 'ok'
        assert not [p for p in paths if getattr(p, 'treatment_id', '') == 'solid_infill']

    def test_14_openings_still_cut_a_trimmed_wall(self):
        base = circle_rect(wall=10)
        lay = circle_rect(wall=10, trims=[pick(base, 'R', inside_C)])
        lay.openings = [Opening('o1', 'R', center_s=120.0, width=30.0)]   # top edge
        paths, meta = build(lay)
        assert meta['network']['trims']['t1']['status'] == 'ok'
        pcs = src_pieces(paths, 'R')
        assert len(pcs) >= 2
        gap = Vec2(220, 100)                        # centre of the opening
        assert all(min(q.dist(gap) for q in p.sample_points()) > 10 for p in pcs)
        # opening clear void, but none for the trim
        lay.openings = []
        _, m2 = build(lay)
        assert m2['network']['trims']['t1']['status'] == 'ok'


# ---------------------------------------------------------------------------
# API + UI
# ---------------------------------------------------------------------------

def test_api_round_trip_trims():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    payload = {
        'id': 'l', 'source_paths': [
            {'id': 'R', 'type': 'RectanglePath', 'x': 100, 'y': 100, 'w': 240, 'h': 160, 'closed': True},
            {'id': 'C', 'type': 'CirclePath', 'cx': 340, 'cy': 180, 'radius': 80, 'closed': True}],
        'offset_treatments': [], 'lattice_instances': [],
    }
    with flask_app.test_client() as c:
        net = c.post('/api/effective_paths', json=payload).get_json()['network']
        sec = next(s for s in net['trim_sections'] if s['source'] == 'C' and s['inside']['R'])
        payload['trims'] = [{'id': 't1', 'source_path_id': 'C', 'start': sec['start'],
                             'end': sec['end'], 'inside': sec['inside'], 'u_mid': sec['u_mid']}]
        net2 = c.post('/api/effective_paths', json=payload).get_json()['network']
        assert net2['trims']['t1']['status'] == 'ok'
        assert next(s for s in net2['trim_sections'] if s['id'] == sec['id'])['trimmed_by'] == 't1'
        r = c.post('/api/route', json=payload).get_json()
        assert r['metrics']['print_runs'] == 1


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_trim_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_trim_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_trim_after_wall_system_smoke():
    """Trim after Wall System assignment: membership kept, the last unwanted
    section still trimmable, web regrouping folded into the trim's undo step,
    Undo / Redo coherent (correction pass 2026-10-07)."""
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_trim_wall_system_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
