"""
VERTICAL SUPPORT + HEADERS (support.py, objects.py) on synthetic geometry:
a square wall (two faces + a zigzag lattice), and the same wall with a gap
in its y = 0 side. Pure: no Designer.
"""
import copy
import math

import pytest

from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, resolve
from layer_assembly.objects import Header, SupportOverride, object_from_dict, point_in_rect
from layer_assembly.support import analyse_support

BEAD = 3.0


def wall(gap=None, jitter=0.0):
    """Faces at y = 0 / 10 (and the other three sides), lattice zigzag along
    the y = 0 side; `gap` = (x0, x1) removes that stretch of the y = 0 side."""
    j = jitter
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    inner = [(10, 10), (90, 10), (90, 90), (10, 90)]
    zig = [(10 + 5 * k, 0.5 + 9 * (k % 2)) for k in range(17)]       # x 10 … 90 between the faces
    if gap is None:
        polys = [{'pts': [[x + j, y - j] for x, y in outer], 'closed': True},
                 {'pts': [[x - j, y + j] for x, y in inner], 'closed': True},
                 {'pts': [list(p) for p in zig], 'closed': False}]
        return polys
    g0, g1 = gap
    return [{'pts': [[g1, 0], [100, 0], [100, 100], [0, 100], [0, 0], [g0, 0]], 'closed': False},
            {'pts': [[g1, 10], [90, 10], [90, 90], [10, 90], [10, 10], [g0, 10]], 'closed': False},
            {'pts': [list(p) for p in zig if p[0] <= g0], 'closed': False},
            {'pts': [list(p) for p in zig if p[0] >= g1], 'closed': False}]


GEO = {'A': (wall(), BEAD), 'G': (wall(gap=(30, 70)), BEAD)}
FP = {'A': Footprint.of_bbox(0, 0, 100, 100), 'G': Footprint.of_bbox(0, 0, 100, 100)}


def run(asm, geo=GEO):
    return analyse_support(resolve(asm, footprints=FP), geo, asm)


def door_stack(**kw):
    return Assembly(1.5, [Section('A', 6), Section('G', 12), Section('A', 6)], **kw)


def test_ordinary_walls_are_supported():
    assert run(Assembly(1.5, [Section('A', 30)])).findings == []
    assert run(Assembly(1.5, [Section('A', 6), Section('G', 12)])).findings == []   # less material: fine


def test_bridge_over_a_gap_is_detected_once():
    rep = run(door_stack())
    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert f.status == 'needs_header' and f.layer == 12 and f.section == 2 and f.local_layer == 0
    assert math.isclose(f.z, 18.0)
    assert abs(f.span - (40 - BEAD)) <= BEAD / 2                 # clear opening between bead edges
    assert abs(f.angle) < 1e-6 and math.isclose(f.centre[0], 50, abs_tol=0.8)
    assert math.isclose(f.centre[1], 5, abs_tol=0.1) and math.isclose(f.depth, 10 + BEAD, abs_tol=0.1)
    assert f.runs and all(len(r) >= 2 for r in f.runs)


def test_gap_on_a_vertical_side_gives_a_vertical_span():
    rot = lambda polys: [{'pts': [[y, x] for x, y in p['pts']], 'closed': p['closed']} for p in polys]
    geo = {'A': (rot(wall()), BEAD), 'G': (rot(wall(gap=(30, 70))), BEAD)}
    f = run(door_stack(), geo).findings[0]
    assert math.isclose(abs(f.angle), 90, abs_tol=1e-6) and math.isclose(f.centre[1], 50, abs_tol=0.8)


def test_tolerances_ignore_numerical_noise():
    geo = {'A': (wall(), BEAD), 'J': (wall(jitter=0.4), BEAD)}       # re-tessellated / jittered
    assert run(Assembly(1.5, [Section('A', 6), Section('J', 6), Section('A', 6)]), geo).findings == []
    nick = wall(gap=(48, 52))[:2] + [wall()[2]]                     # faces nicked 4 in, lattice intact
    small = {'A': (wall(), BEAD), 'g': (nick, BEAD)}                 # 1 in clear < 2 beads: noise
    assert run(Assembly(1.5, [Section('A', 6), Section('g', 6), Section('A', 6)]), small).findings == []
    lean = Assembly(1.5, [Section('A', 30, '', SectionTransform('linear', (1.25, 0), 0))])
    assert run(lean).findings == []                                  # 1.25 in/layer < ½ bead overhang


def test_a_steep_overhang_is_reported():
    lean = Assembly(1.5, [Section('A', 6, '', SectionTransform('linear', (2.5, 0), 0))])
    assert run(lean).findings                                        # > ½ bead per layer


def add_header(asm, f, **kw):
    h = Header('h1', f.section, f.local_layer, *f.local_centre, f.angle, f.span, **kw)
    asm.objects.append(h)
    return h


def test_header_resolves_the_warning_and_removal_restores_it():
    asm = door_stack()
    f = run(asm).findings[0]
    add_header(asm, f, bearing=8, depth=13, thickness=6)
    rep = run(asm)
    assert rep.findings[0].status == 'supported' and rep.findings[0].header == 'h1'
    h = rep.headers[0]
    assert h['supports'] == [rep.findings[0].id] and h['snapped']
    assert math.isclose(h['length'], f.span + 16) and h['depth'] == 13
    assert math.isclose(h['z_top'], 18.0) and math.isclose(h['z_bottom'], 12.0)   # hangs down from the boundary
    assert h['pocket_layers'] == [8, 9, 10, 11]                  # its ends pass through wall layers below
    asm.objects.clear()
    assert run(asm).findings[0].status == 'needs_header'


def test_bearing_depth_and_orientation_define_the_rectangle():
    h = Header('h', 0, 0, 50, 5, 0.0, 37, bearing=8, depth=13, thickness=6)
    from layer_assembly.model import IDENTITY
    xs = [p[0] for p in h.support_rect(IDENTITY)]
    ys = [p[1] for p in h.support_rect(IDENTITY)]
    assert math.isclose(max(xs) - min(xs), 37 + 16) and math.isclose(max(ys) - min(ys), 13)
    v = Header('v', 0, 0, 5, 50, 90.0, 37, bearing=8, depth=13)
    ys = [p[1] for p in v.support_rect(IDENTITY)]
    assert math.isclose(max(ys) - min(ys), 53, abs_tol=1e-9)


def test_a_too_short_header_does_not_support():
    asm = door_stack()
    f = run(asm).findings[0]
    add_header(asm, f, bearing=0, depth=13).snap = False
    asm.objects[0].span = 20                                     # does not cover the opening
    rep = run(asm)
    assert rep.findings[0].status == 'needs_header' and 0 < rep.findings[0].coverage < 1


def test_header_snaps_to_the_opening_after_a_layer_height_change():
    asm = door_stack()
    f = run(asm).findings[0]
    add_header(asm, f, bearing=8, depth=13)
    asm.objects[0].x += 3                                         # drifted
    asm.layer_height = 2.0
    rep = run(asm)
    assert rep.findings[0].status == 'supported' and math.isclose(asm.objects[0].x, f.local_centre[0], abs_tol=0.8)
    assert math.isclose(rep.headers[0]['z_top'], resolve(asm).sections[2].z_bottom)


def test_ignored_warning_is_recorded_not_supported():
    asm = door_stack()
    f = run(asm).findings[0]
    asm.overrides.append(SupportOverride(f.section, f.local_layer, *f.local_centre, 'test'))
    rep = run(asm)
    assert rep.findings[0].status == 'overridden' and rep.findings[0].override == 0
    assert rep.to_dict()['needs_header'] == 0 and rep.to_dict()['overridden'] == 1 and rep.to_dict()['supported'] == 0
    # an override elsewhere does not hide this span
    asm.overrides[0] = SupportOverride(f.section, f.local_layer, 50, 95)
    assert run(asm).findings[0].status == 'needs_header'


def test_header_follows_the_assembly_transform():
    asm = door_stack(transform=SectionTransform('linear', (0.5, 0.25), 0.25))
    f = run(asm).findings[0]
    add_header(asm, f, bearing=8, depth=13)
    res = resolve(asm, footprints=FP)
    rep = analyse_support(res, GEO, asm)
    t = res.instances[f.layer].transform
    h = rep.headers[0]
    assert rep.findings[0].status == 'supported'
    assert math.isclose(h['centre'][0], t.apply(*f.local_centre)[0]) and math.isclose(h['span'], f.span, rel_tol=1e-9)
    cx = sum(p[0] for p in h['rect']) / 4
    assert math.isclose(cx, t.apply(*f.local_centre)[0])
    assert math.isclose(h['length'], h['span'] + 16)             # bearing stays physical


def test_designs_are_not_modified_and_objects_round_trip():
    geo = copy.deepcopy(GEO)
    asm = door_stack()
    f = run(asm, geo).findings[0]
    add_header(asm, f)
    asm.overrides.append(SupportOverride(1, 0, 1, 2))
    run(asm, geo)
    assert geo == GEO
    back = Assembly.from_dict(asm.to_dict())
    assert back.to_dict() == asm.to_dict() and isinstance(back.objects[0], Header)
    with pytest.raises(ValueError):
        object_from_dict({'kind': 'tie-rod'})


def test_header_on_a_vanished_boundary_reports_it():
    asm = door_stack()
    asm.objects.append(Header('hx', 7, 0, 50, 5, 0, 30))
    assert run(asm).headers[0]['error']


def test_point_in_rect():
    r = [(0, 0), (2, 0), (2, 1), (0, 1)]
    assert point_in_rect((1, 0.5), r) and not point_in_rect((3, 0.5), r)
