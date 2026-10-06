"""
Wall REGIONS and OPENINGS through them (geometric tests).

Semantic model under test:
  * Wall Thickness (+ Alignment) is the normal, parametric wall: the
    derived face follows every edit of its boundary.
  * A WALL infill on a closed boundary declares a wall-MATERIAL region:
    the boundary minus the closed voids nested in it (any number, any
    distance). Linked boundaries (relationship / inset) are explicit faces.
  * Wall infill and openings use the SAME region: an opening is a
    SUBTRACTION from that material — a corridor from the clicked face to
    the opposite face of the material. Its two sides are cut faces
    (printed), never new wall topology; the target void stays a void.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app  # noqa: F401  (path setup)
import route_quality as RQ
from graph import route_layer, compute_metrics
from model import (Vec2, PrintLayer, RectanglePath, CirclePath, EllipsePath, RegionInfill,
                   WallRelation, Opening, WallSpec, _point_in_polygon, _cum_lengths,
                   _project_to_polyline)
from tests.test_networks import _assert_internal_inside


def _pts(p, meta):
    return meta['pts'].get(id(p)) or p.sample_points()


def _any_inside(paths, meta, poly, kinds=None, exclude_boundary=True):
    """Does any path (of the given treatment ids) pass strictly inside poly?"""
    for p in paths:
        if kinds is not None and getattr(p, 'treatment_id', '') not in kinds:
            continue
        sp = _pts(p, meta)
        for a, b in zip(sp, sp[1:]):
            for k in range(1, 10):
                q = a.lerp(b, k / 10)
                if _point_in_polygon(q, poly):
                    cum = _cum_lengths(poly, True)
                    if not exclude_boundary or _project_to_polyline(q, poly, cum, True)[1] > 0.2:
                        return True
    return False


def _box(x0, y0, x1, y1):
    return [Vec2(x0, y0), Vec2(x1, y0), Vec2(x1, y1), Vec2(x0, y1)]


INFILL = ('infill', 'infill_return')


# ---------------------------------------------------------------------------
# the screenshot fixture: exterior + two rooms, wall infill, a doorway
# ---------------------------------------------------------------------------

def two_rooms(ops=(), pattern='zigzag'):
    O = RectanglePath(0, 0, 300, 160, id='O')
    A = RectanglePath(10, 10, 130, 140, id='A')      # left room
    B = RectanglePath(160, 10, 130, 140, id='B')     # right room
    return PrintLayer('t', source_paths=[O, A, B],
                      infills=[RegionInfill('F', 'O', pattern, {'spacing': 20})],
                      openings=[Opening(f'o{k}', pid, s, w) for k, (pid, s, w) in enumerate(ops)])


DOOR_A = _box(58, 0, 82, 10)          # doorway x 58…82 through the bottom wall of room A
ROOM_A = _box(10, 10, 140, 150)
ROOM_B = _box(160, 10, 290, 150)


@pytest.mark.parametrize('ops', [(('O', 70, 24),), (('A', 60, 24),), (('O', 70, 24), ('A', 60, 24))],
                         ids=['from outer face', 'from inner face', 'from both faces'])
@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_screenshot_doorway_is_empty_and_rooms_stay_voids(ops, pattern):
    L = two_rooms(ops, pattern)
    paths, meta = L._build_effective()
    # region / voids reported exactly as without the opening
    info = next(i for i in meta['network']['infills'] if i['id'] == 'F')
    assert info['status'] == 'ok' and info['voids'] == ['A', 'B']
    # the doorway is EMPTY: nothing printed inside it (cut faces lie on
    # its sides, not inside)
    assert not _any_inside(paths, meta, DOOR_A)
    # the target room stays a void: no infill inside it
    assert not _any_inside(paths, meta, ROOM_A, kinds=INFILL)
    # the unrelated room is unchanged: closed, untouched, empty
    assert any(p.id == 'B' and p.closed for p in paths)
    assert not _any_inside(paths, meta, ROOM_B, kinds=INFILL)
    # both faces cut, two cut faces printed across the wall
    assert not any(p.id == 'A' and p.closed for p in paths)
    assert not any(p.id == 'O' and p.closed for p in paths)
    faces = [_pts(p, meta) for p in paths if getattr(p, 'treatment_id', '') == 'opening_face']
    assert len(faces) == 2
    for f in faces:
        assert sorted((f[0].y, f[-1].y)) == pytest.approx([0, 10]) and abs(f[0].x - f[-1].x) < 1e-6
    assert sorted(round(f[0].x, 6) for f in faces) == [58, 82]
    # infill is still generated around the doorway, inside the material
    assert [p for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    _assert_internal_inside(L)
    # no accidental wall connection / branch: no junction is created
    assert all(not j.get('corner') for j in meta['network']['junctions'])
    assert meta['network']['opening_status'] and set(meta['network']['opening_status'].values()) == {'ok'}
    # the canvas draws both cut faces from the backend pieces
    assert {'O', 'A'} <= set(meta['network']['modified_sources']) and 'B' not in meta['network']['modified_sources']
    # routing stays sensible
    mv = route_layer(L.to_routing_layer(paths, meta))
    m = compute_metrics(mv)
    assert m['print_runs'] == 1 and m['travel_moves'] == 0
    assert RQ.geometric_retrace(mv) < 1e-6


def test_opening_from_either_face_is_the_same_doorway():
    def geo(L):
        paths, meta = L._build_effective()
        # (a cut face's direction is irrelevant: compared as an unordered pair)
        return sorted((p.id.split('~')[0], tuple(sorted((round(q.x, 6), round(q.y, 6)) for q in _pts(p, meta)))
                       if getattr(p, 'treatment_id', '') == 'opening_face' else
                       tuple((round(q.x, 6), round(q.y, 6)) for q in _pts(p, meta)))
                      for p in paths if getattr(p, 'treatment_id', '') in ('opening_cut', 'opening_face'))
    assert geo(two_rooms((('O', 70, 24),))) == geo(two_rooms((('A', 60, 24),)))


def test_two_rooms_each_with_a_doorway_and_a_corner_doorway():
    # doorway into A (bottom), into B (top), and one spanning O's
    # bottom-right corner into B's corner (an L-shaped corridor)
    L = two_rooms((('O', 70, 24), ('B', 260, 20), ('O', 300, 30)))
    paths, meta = L._build_effective()
    assert set(meta['network']['opening_status'].values()) == {'ok'}
    assert not _any_inside(paths, meta, DOOR_A)
    assert not _any_inside(paths, meta, ROOM_A, kinds=INFILL)
    assert not _any_inside(paths, meta, ROOM_B, kinds=INFILL)
    assert not any(p.closed and p.id in ('O', 'A', 'B') for p in paths)
    # corner doorway: the corner of the outer wall and of room B are gone
    for corner in (Vec2(300, 0), Vec2(290, 10)):
        assert all(min(q.dist(corner) for q in _pts(p, meta)) > 1.0
                   for p in paths if getattr(p, 'treatment_id', '') in ('opening_cut', 'infill'))
    faces = [p for p in paths if getattr(p, 'treatment_id', '') == 'opening_face']
    assert len(faces) == 6
    _assert_internal_inside(L)
    assert RQ.wall_metrics(L)['interior_retrace'] < 1e-6


def test_overlapping_openings_union_into_one_doorway():
    L = two_rooms((('O', 64, 20), ('O', 78, 20)))       # x 54…74 ∪ 68…88
    paths, meta = L._build_effective()
    assert not _any_inside(paths, meta, _box(55, 0, 87, 10))
    faces = [_pts(p, meta) for p in paths if getattr(p, 'treatment_id', '') == 'opening_face']
    assert sorted(round(f[0].x, 6) for f in faces) == [54, 88]      # one doorway, two cut faces


def test_arbitrary_circular_void_doorway():
    O = CirclePath(200, 200, 80, id='O')
    V = CirclePath(200, 200, 66, id='V')                 # 14 in wall, not linked
    L = PrintLayer('t', source_paths=[O, V], infills=[RegionInfill('F', 'O', 'wave', {'spacing': 20})],
                   openings=[Opening('o', 'O', 0.0, 20)])
    paths, meta = L._build_effective()
    assert meta['network']['opening_status'] == {'o': 'ok'}
    assert not any(p.closed and p.id in ('O', 'V') for p in paths)
    assert len([p for p in paths if getattr(p, 'treatment_id', '') == 'opening_face']) == 2
    room = [Vec2(200 + 60 * math.cos(k / 16 * math.pi), 200 + 60 * math.sin(k / 16 * math.pi)) for k in range(32)]
    assert not _any_inside(paths, meta, room, kinds=INFILL)
    _assert_internal_inside(L)


def test_no_defensible_opposite_face_is_not_cut():
    # an opening on the outer boundary where no void lies behind it within
    # the wall depth: never cut across the room — reported, left intact
    O = RectanglePath(0, 0, 300, 160, id='O')
    A = RectanglePath(10, 10, 130, 140, id='A')
    L = PrintLayer('t', source_paths=[O, A], infills=[RegionInfill('F', 'O', 'zigzag', {'spacing': 20})],
                   openings=[Opening('o', 'O', 230, 20)])             # bottom wall right of room A: 290 in deep
    paths, meta = L._build_effective()
    assert meta['network']['opening_status']['o'].startswith('no opposite wall face')
    assert any(p.id == 'O' and p.closed for p in paths)


# ---------------------------------------------------------------------------
# linked walls (assembly) and a parametric wall next to an unrelated room
# ---------------------------------------------------------------------------

def test_linked_rounded_rectangle_wall_doorway():
    O = RectanglePath(0, 0, 200, 120, id='O', corner_radius=20)
    I = RectanglePath(10, 10, 180, 100, id='I')
    L = PrintLayer('t', source_paths=[O, I], wall_relations=[WallRelation('w', 'O', 'I', 10)],
                   infills=[RegionInfill('F', 'O', 'wave', {'spacing': 20})],
                   openings=[Opening('o', 'I', 50, 24)])
    paths, meta = L._build_effective()
    assert not any(p.closed and p.id in ('O', 'I') for p in paths)
    caps = [p for p in paths if getattr(p, 'treatment_id', '') == 'wall_system']
    assert len(caps) == 2
    _assert_internal_inside(L)
    assert RQ.wall_metrics(L)['congestion_hotspots'] == 0


def test_linked_ellipse_wall_doorway():
    O = EllipsePath(200, 150, 120, 80, id='O')
    I = EllipsePath(200, 150, 110, 70, id='I')
    L = PrintLayer('t', source_paths=[O, I], wall_relations=[WallRelation('w', 'O', 'I', 10)],
                   infills=[RegionInfill('F', 'O', 'zigzag', {'spacing': 20})],
                   openings=[Opening('o', 'O', 100, 20)])
    paths, meta = L._build_effective()
    assert not any(p.closed and p.id in ('O', 'I') for p in paths)
    _assert_internal_inside(L)


def test_parametric_wall_with_an_unrelated_room_inside():
    # Wall Thickness 10 inside on O (the normal parametric wall) and an
    # arbitrary room B drawn inside: the doorway cuts O and its derived
    # face only — never across to B
    O = RectanglePath(0, 0, 300, 160, id='O')
    O.wall = WallSpec(10, 'inside')
    B = RectanglePath(160, 30, 100, 100, id='B')
    L = PrintLayer('t', source_paths=[O, B], infills=[RegionInfill('F', 'O', 'zigzag', {'spacing': 20})],
                   openings=[Opening('o', 'O', 70, 24)])
    paths, meta = L._build_effective()
    assert any(p.id == 'B' and p.closed for p in paths)
    assert not _any_inside(paths, meta, _box(58, 0, 82, 10))
    assert len([p for p in paths if getattr(p, 'treatment_id', '') == 'wall_system']) == 2


# ---------------------------------------------------------------------------
# parametric wall distance after parent edits (Wall Thickness = the normal
# parametric wall; derived face recomputed every evaluation)
# ---------------------------------------------------------------------------

def _wall_gap(L, pid='O'):
    paths, meta = L._build_effective()
    src = next(p for p in paths if p.id == pid)
    face = next(p for p in paths if p.id == f'{pid}.wall')
    s_pts = _pts(src, meta)
    cum = _cum_lengths(s_pts, True)
    ds = [_project_to_polyline(q, s_pts, cum, True)[1] for q in _pts(face, meta)]
    inside = all(_point_in_polygon(q, s_pts) for q in _pts(face, meta)[::4])
    return min(ds), max(ds), inside


@pytest.mark.parametrize('edit', ['none', 'resize', 'move', 'rotate', 'thickness', 'corner R'])
def test_wall_thickness_is_parametric(edit):
    O = RectanglePath(0, 0, 200, 120, id='O')
    O.wall = WallSpec(10, 'inside')
    t = 10
    if edit == 'resize':
        O.w, O.h = 260, 90
    elif edit == 'move':
        O.x, O.y = 37, -12
    elif edit == 'rotate':
        O.rotation = math.radians(27)
    elif edit == 'thickness':
        O.wall = WallSpec(12, 'inside')
        t = 12
    elif edit == 'corner R':
        O.corner_radius = 25
    L = PrintLayer('t', source_paths=[O])
    lo, hi, inside = _wall_gap(L)
    assert inside
    assert lo == pytest.approx(t, abs=1e-6) and hi == pytest.approx(t, abs=0.05)
