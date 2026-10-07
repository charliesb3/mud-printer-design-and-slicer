"""
Components (detection only — they SUGGEST groups) and TRANSFORM GROUPS:
explicit source membership (groups.py + model 'multiple' mode). Pure:
synthetic centrelines tagged with opaque source ids, no Designer.
"""
import math

from layer_assembly.source import StaticSource, DesignInfo, LayerGeometry, stack_geometry

import pytest

from layer_assembly.components import detect, assign, root_of
from layer_assembly.groups import build_groups
from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, resolve, TransformCenter
from layer_assembly.support import analyse_support
from layer_assembly.objects import Header

B = 3.0


def sq(x0, y0, x1, y1):
    return {'pts': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], 'closed': True}


def ring(x0, y0, x1, y1, t=10, lattice=True):
    """A hollow square wall: outer + inner face, optionally a zigzag lattice
    landing on both faces along the bottom side."""
    polys = [sq(x0, y0, x1, y1), sq(x0 + t, y0 + t, x1 - t, y1 - t)]
    if lattice:
        polys.append({'pts': [[x0 + t + 5 * k, y0 + (2.25 if k % 2 == 0 else t - 2.25)]
                              for k in range(int((x1 - x0 - 2 * t) / 5) + 1)], 'closed': False})
    return polys


def T(profile='linear', dx=0.0, dy=0.0, s=0.0):
    return SectionTransform(profile, (dx, dy), s)


def two_masses():
    return ring(0, 0, 100, 100) + ring(200, 20, 260, 80)       # square A, smaller square B


def test_one_wall_mass_is_one_component_even_hollow_or_multi_strand():
    assert len(detect(ring(0, 0, 100, 100), B, 'r')) == 1
    assert len(detect(ring(0, 0, 100, 100, lattice=False), B, 'r')) == 1      # nested faces join
    many = ring(0, 0, 100, 100) + [{'pts': [[12 + 5 * k, 50], [12 + 5 * k, 52]], 'closed': False} for k in range(15)]
    assert len(detect(many, B, 'r')) == 1                                      # touching bits: not 16 masses


def test_two_disconnected_masses_are_two_components_in_a_stable_order():
    ms = detect(two_masses(), B, 'base')
    assert [m.key for m in ms] == ['base:0', 'base:1']
    assert ms[0].centre == (50, 50) and ms[0].half == 50
    assert ms[1].centre == (230, 50) and ms[1].half == 30
    assert [m.key for m in detect(list(reversed(two_masses())), B, 'base')] == ['base:0', 'base:1']


def test_masses_joined_by_material_are_one():
    joined = two_masses() + [{'pts': [[100, 50], [200, 50]], 'closed': False}]
    assert len(detect(joined, B, 'r')) == 1


def test_derived_design_strands_are_assigned_to_the_root_masses():
    root = two_masses()
    gap = [{'pts': [[0, 0], [40, 0]], 'closed': False}, {'pts': [[60, 0], [100, 0], [100, 100], [0, 100], [0, 0]], 'closed': False},
           sq(10, 10, 90, 90)] + ring(200, 20, 260, 80)
    ms = detect(root, B, 'base')
    assert assign(gap, ms) == ['base:0', 'base:0', 'base:0', 'base:1', 'base:1', 'base:1']
    assert root_of('door', {'door': 'base', 'base': None}) == 'base'


def tag(polys, src):
    return [dict(p, src=[src] * len(p['pts'])) for p in polys]


def tagged():
    """Two squares tagged 'A' / 'B' (sources = walls)."""
    return tag(ring(0, 0, 100, 100), 'A') + tag(ring(200, 20, 260, 80), 'B')


def joined():
    """ONE connected mass: square A, square B and a connecting wall 'L'
    whose 'lattice' strand runs through all three walls (per-vertex tags)."""
    lat = {'pts': [[50, 5], [100, 50], [150, 50], [230, 25]], 'closed': False, 'src': ['A', 'A', 'L', 'B']}
    return tagged() + tag([{'pts': [[100, 48], [200, 48]], 'closed': False}], 'L') + [lat]


GROUPS = [TransformCenter('c1', 50, 50, ('A',)), TransformCenter('c2', 230, 50, ('B',))]


def model(centers=None, geo=None):
    return build_groups({'A': (geo or tagged(), B)}, {'A': None}, ['A'], GROUPS if centers is None else centers)


def comp(asm, gm):
    return resolve(asm, footprints={'A': Footprint.of_bbox(0, 0, 260, 100)}, groups=gm)


def test_footprint_mode_is_exactly_the_previous_behaviour():
    a = Assembly(1.5, [Section('A', 15, '', T('quadratic', dx=0.25, s=0.5))], '', T(s=0.25))
    fp = {'A': Footprint.of_bbox(0, 0, 260, 100)}
    r0 = resolve(a, footprints=fp)
    r1 = resolve(a, footprints=fp, groups=model())               # mode 'footprint': groups unused
    assert [i.transform for i in r0.instances] == [i.transform for i in r1.instances]
    assert all(not i.parts for i in r1.instances)


@pytest.mark.parametrize('profile', ['linear', 'quadratic'])
def test_each_group_scales_about_its_own_centre(profile):
    a = Assembly(1.5, [Section('A', 15, '', T(profile, s=0.5))], center_mode='multiple')
    r = comp(a, model())
    m = {'linear': lambda k: k, 'quadratic': lambda k: k * (k + 1) / 20}[profile]
    for g, inst in enumerate(r.instances):
        ta, tb = inst.parts['c1'], inst.parts['c2']
        assert math.isclose(ta.apply(50, 50)[0], 50, abs_tol=1e-9) and math.isclose(tb.apply(230, 50)[0], 230, abs_tol=1e-9)
        assert math.isclose(ta.apply(100, 50)[0], 100 - 0.5 * m(g), abs_tol=1e-9)    # A's own edge
        assert math.isclose(tb.apply(260, 50)[0], 260 - 0.5 * m(g), abs_tol=1e-9)    # B's own edge


def test_two_groups_inside_one_connected_mass_follow_explicit_source_membership():
    geo = joined()
    assert len(detect(geo, B, 'r')) == 1                         # one printed mass …
    gm = model(geo=geo)                                          # … two groups (A → c1, B → c2)
    assert gm.group_of == {'A': 'c1', 'B': 'c2'}
    lat = gm.vertex_groups['A'][-1]
    assert lat == ['c1', 'c1', None, 'c2']                       # the lattice follows ITS walls; L unassigned
    a = Assembly(1.5, [Section('A', 15, '', T(s=0.5))], center_mode='multiple')
    inst = comp(a, gm).instances[-1]
    # the layer asks for its geometry with SEMANTIC transforms of its groups'
    # sources, relative to its own placement (unassigned L: none)
    tf = inst.transforms()
    assert set(tf) == {'A', 'B'} and inst.geometry_key != 'A'
    for s_, c in (('A', 'c1'), ('B', 'c2')):
        k, tx, ty = tf[s_]
        w, p = inst.transform, inst.parts[c]
        assert math.isclose(w.scale * k, p.scale) and math.isclose(w.scale * tx + w.tx, p.tx)
    gm2 = model([GROUPS[0], TransformCenter('c2', 230, 50, ('B', 'L'))], geo)   # assign L explicitly
    assert gm2.vertex_groups['A'][-1] == ['c1', 'c1', 'c2', 'c2']
    assert set(comp(a, gm2).instances[-1].transforms()) == {'A', 'B', 'L'}


def test_moving_a_pivot_never_reassigns_geometry():
    geo = joined()
    far = [TransformCenter('c1', 240, 50, ('A',)), TransformCenter('c2', 0, 0, ('B',))]   # pivots swapped over
    gm = model(far, geo)
    assert gm.group_of == {'A': 'c1', 'B': 'c2'} and gm.vertex_groups == model(geo=geo).vertex_groups
    assert gm.footprints['c1'].cx == 240 and gm.auto['c1'] == (50, 50)    # pivot moved; ↺ target unchanged


def test_shift_stays_global_and_assembly_plus_section_scale_compose():
    a = Assembly(1.5, [Section('A', 12, '', T(dx=0.5, s=0.25)), Section('A', 12)], '',
                 T('quadratic', dy=0.25, s=0.5), center_mode='multiple')
    r = comp(a, model())
    for inst in r.instances:
        ta, tb = inst.parts['c1'], inst.parts['c2']
        da = (ta.apply(50, 50)[0] - 50, ta.apply(50, 50)[1] - 50)
        db = (tb.apply(230, 50)[0] - 230, tb.apply(230, 50)[1] - 50)
        assert math.isclose(da[0], db[0], abs_tol=1e-9) and math.isclose(da[1], db[1], abs_tol=1e-9)
    pts = [i.parts['c2'].apply(260, 80) for i in r.instances]
    assert max(math.dist(p, q) for p, q in zip(pts, pts[1:])) < 1.5      # continuous


def test_dragged_centre_changes_only_its_group():
    a = Assembly(1.5, [Section('A', 15, '', T(s=0.5))], center_mode='multiple',
                 centers=[GROUPS[0], TransformCenter('c2', 250, 50, ('B',))])
    r0 = comp(Assembly(1.5, a.sections, center_mode='multiple'), model())
    r1 = comp(a, model(a.centers))
    assert [i.parts['c1'] for i in r0.instances] == [i.parts['c1'] for i in r1.instances]
    for i in r1.instances:
        assert math.isclose(i.parts['c2'].apply(250, 50)[0], 250, abs_tol=1e-9)    # the new pivot is fixed
    assert Assembly.from_dict(a.to_dict()).centers == a.centers


def test_layer_height_change_keeps_groups():
    a = Assembly(1.5, [Section('A', 15, '', T(s=0.5))], center_mode='multiple')
    k1 = {k for i in comp(a, model()).instances for k in i.parts}
    a.layer_height = 2.5
    k2 = {k for i in comp(a, model()).instances for k in i.parts}
    assert k1 == k2 == {'c1', 'c2'}


def test_support_and_headers_use_the_group_placement():
    gapB = tag(ring(0, 0, 100, 100), 'A') + tag([{'pts': [[200, 20], [220, 20]], 'closed': False},
                                                 {'pts': [[240, 20], [260, 20], [260, 80], [200, 80], [200, 20]], 'closed': False},
                                                 sq(210, 30, 250, 70)], 'B')
    geo = {'A': (tagged(), B), 'G': (gapB, B)}
    gm = build_groups(geo, {'A': None, 'G': 'A'}, ['A', 'G'], GROUPS)
    a = Assembly(1.5, [Section('A', 3), Section('G', 6), Section('A', 6)], '', T(s=0.25), center_mode='multiple')
    fps = {d: Footprint.of_bbox(0, 0, 260, 100) for d in 'AG'}
    res = resolve(a, footprints=fps, groups=gm)
    geo = stack_geometry(res, StaticSource({d: (DesignInfo(d, d), LayerGeometry(d, g[0], B)) for d, g in geo.items()}))
    rep = analyse_support(res, geo, a, gm)
    f = next(f for f in rep.findings if f.centre[0] > 150 and f.kind == 'bridge')     # the gap in B
    tb = res.instances[f.layer].parts['c2']
    assert math.isclose(f.local_centre[0], 230, abs_tol=1.0)
    a.objects.append(Header('h1', f.section, f.local_layer, *f.local_centre, f.angle, f.span, 4, 13, 3))
    rep = analyse_support(res, geo, a, gm)
    h = rep.headers[0]
    assert next(x for x in rep.findings if x.id == f.id).status == 'supported'
    assert math.isclose(h['centre'][0], tb.apply(*f.local_centre)[0])        # placed by B's transform


def test_centres_without_members_move_nothing_and_a_source_has_one_group():
    a = Assembly(1.5, [Section('A', 15, '', T(s=0.5))], center_mode='multiple')
    r = comp(a, model([TransformCenter('m1', 120, 50)]))
    assert all(not i.parts for i in r.instances)                         # nothing assigned: whole footprint
    gm = model([TransformCenter('c1', 50, 50, ('A',)), TransformCenter('c9', 0, 0, ('A', 'B')),
                TransformCenter('m1', 120, 50)])
    assert gm.group_of == {'A': 'c1', 'B': 'c9'} and set(gm.footprints) == {'c1', 'c9'}
    assert len(gm.warnings) == 1 and 'c1 keeps it' in gm.warnings[0]


def test_suggestions_list_the_sources_of_each_mass_and_resolution_is_deterministic():
    gm = model([])
    assert [(s['key'], s['sources']) for s in gm.suggestions] == [('A:0', ['A']), ('A:1', ['B'])]
    assert model([], joined()).suggestions[0]['sources'] == ['A', 'B', 'L']    # one mass, three walls
    a = Assembly(1.5, [Section('A', 15, '', T(s=0.5))], center_mode='multiple')
    assert [i.parts for i in comp(a, model()).instances] == [i.parts for i in comp(a, model()).instances]


def test_centres_round_trip():
    a = Assembly(1.5, [Section('A', 6)], center_mode='multiple', max_overhang=1.0,
                 centers=[TransformCenter('c1', 1.5, 2.5, ('C', 'L'), 'circle'), TransformCenter('m2', 3, 4)])
    assert Assembly.from_dict(a.to_dict()).to_dict() == a.to_dict()
    assert Assembly.from_dict(a.to_dict()).centers[0].sources == ('C', 'L')
