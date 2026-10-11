"""
OPENING SHAPES + DIMENSIONAL CONSTRAINTS (layer_assembly/opening_shape.py,
2026-10-10, second pass): Diamond (angular | curved vesica), Arch from three
independent components (lower flat | angular | curved, optional middle,
upper angular | curved Gothic | header); the dimension edited last wins;
every result is printable on the actual layers (each narrowing step ≤ h a
side, the closure included) at any layer phase; migration of older shapes;
generated headers. Pure — no Designer.
"""
import math

import pytest

from layer_assembly import opening_shape as O
from layer_assembly.model import Assembly, Section, resolve
from layer_assembly.profiles import (OpeningArray, OpeningProfile, WallRef, WallTarget, apply_profiles,
                                     profile_from_dict, profile_headers)

LH, H, BEAD = 1.5, 1.5, 3.0
DIAMONDS = ['pointed', 'curve']
ARCH_UPPERS = ['pointed', 'curve']
ARCH_LOWERS = ['flat', 'pointed', 'arc', 'round']        # UI: Flat · Angular · Curved · Rounded
WALL = WallTarget('L', None, (0.0, 0.0), (240.0, 0.0), 240.0, 10.0, True, 'Wall')


def D(style='pointed', width=24.0, height=48.0, zb=6.0, **kw):
    return dict(shape='diamond', upper=style, lower=style, width=width, height=height, z_bottom=zb, array=None, **kw)


def A(upper='pointed', lower='flat', width=24.0, U=30.0, middle=True, mh=24.0, depth=None, zb=6.0, array=None, **kw):
    s = dict(shape='arch', upper=upper, lower=lower, width=width, upper_height=U, middle=middle, middle_height=mh,
             bottom_depth=None if lower == 'flat' else (depth if depth is not None else
                                                        (width if lower == 'arc' else 0.5 * width)),
             z_bottom=zb, array=array, height=0.0, **kw)
    O.sync(s)
    return s


def ok(s, lh=LH, h=H, bead=BEAD):
    return O.evaluate(s, lh, h, bead)['ok']


def widths(s, lh=LH, h=H, bead=BEAD):
    d, m, U, _ = O.parts(s)
    return O.layer_widths(s['z_bottom'], s['width'], d, m, U, s['shape'], s['upper'], s['lower'], lh, O.wmin(bead, h))


def closing_steps(s, lh=LH, h=H, bead=BEAD):
    """Every narrowing half-step of the cut sequence, the closure to a full layer included."""
    seq = [w for _, w in widths(s, lh, h, bead)] + [0.0]
    return [(a - b) / 2 for a, b in zip(seq, seq[1:]) if b < a]


def wd(s, t):
    d, m, U, _ = O.parts(s)
    return O.width_at(t, s['width'], d, m, U, s['shape'], s['upper'], s['lower'])


# ---- geometry -------------------------------------------------------------------------------

def test_angular_diamond_is_four_straight_sides_widest_at_mid_height():
    s = D('pointed', 24, 40)
    assert wd(s, 20) == pytest.approx(24) and wd(s, 0) == 0 and wd(s, 39.999) == pytest.approx(0, abs=1e-2)
    for t in (2, 7, 13, 19):                                  # linear: equal steps, symmetric
        assert wd(s, t) == pytest.approx(24 * t / 20)
        assert wd(s, 40 - t) == pytest.approx(wd(s, t))


def test_curved_diamond_is_a_vesica_of_two_arcs_with_sharp_points():
    W, Ht = 12.0, 40.0
    s = D('curve', W, Ht)
    a = (Ht * Ht / 4 - W * W / 4) / W                       # centre offset of each arc on the mid line
    R = a + W / 2
    for t in (1, 5, 11, 20, 27, 39):                        # every edge point lies on its circle
        x = wd(s, t) / 2
        assert math.hypot(x + a, t - Ht / 2) == pytest.approx(R, abs=1e-9)
        assert wd(s, Ht - t) == pytest.approx(wd(s, t))      # symmetric top / bottom
    # SHARP points: the edge meets the axis at a finite angle (half width
    # ∝ distance from the point), not tangentially as a circle / ellipse would
    e = 1e-4
    slope = (wd(s, Ht - e) / 2) / e
    assert slope == pytest.approx(Ht / 2 / a, rel=1e-3) and 0.1 < slope < 10
    assert wd(s, 20) == pytest.approx(W)                     # widest at mid-height
    assert wd(s, 10) > 0.5 * W                               # convex: fuller than the angular diamond


def test_gothic_upper_is_vertical_at_the_spring_and_sharp_at_the_apex():
    s = A('curve', 'flat', width=20, U=30, mh=10)
    sp = 10
    e = 1e-4
    assert (wd(s, sp + e) - 20) / e == pytest.approx(0, abs=1e-2), 'smooth (vertical) where it meets the middle'
    a = (30 * 30 - 100) / 20
    slope = (wd(s, sp + 30 - e) / 2) / e
    assert slope == pytest.approx(30 / a, rel=1e-3), 'a sharp point at the apex'
    R = a + 10
    for u in (3, 12, 25):
        assert math.hypot(wd(s, sp + u) / 2 + a, u) == pytest.approx(R, abs=1e-9)  # an arc centred on the spring line


@pytest.mark.parametrize('lower', ARCH_LOWERS)
def test_arch_lower_profiles(lower):
    d = 18 if lower == 'arc' else 12
    s = A('pointed', lower, width=24, U=24, mh=12, depth=d)
    if lower == 'flat':
        assert O.parts(s)[0] == 0 and wd(s, 0) == 24
        return
    assert wd(s, d) == pytest.approx(24) and wd(s, 0) == 0
    if lower == 'pointed':
        assert wd(s, 3) == pytest.approx(6) and wd(s, 9) == pytest.approx(18)
    elif lower == 'round':                                   # a genuine semicircle (depth W / 2)
        for t in (0.5, 3, 6, 9, 11.5):
            assert math.hypot(wd(s, t) / 2, 12 - t) == pytest.approx(12, abs=1e-9)
        e = 1e-4                                             # horizontal at the bottom, vertical at its top
        assert wd(s, e) > 2 * math.sqrt(2 * 12 * e) * 0.99 and (24 - wd(s, 12 - e)) / e < 0.1
    else:                                                    # CURVED: the vesica's lower half
        a = (d * d - 144) / 24                               # arcs centred on its top line
        for t in (0.5, 4, 9, 15, 17.5):
            assert math.hypot(wd(s, t) / 2 + a, d - t) == pytest.approx(a + 12, abs=1e-9)
        e = 1e-4
        slope = (wd(s, e) / 2) / e                           # SHARP: a finite angle at the point,
        assert slope == pytest.approx(d / a, rel=1e-3)       # not a rounded (tangent) tip
        assert (24 - wd(s, d - e)) / e < 0.1, 'vertical where it meets the sides'


def test_curved_lower_is_the_lower_half_of_the_curved_diamond():
    W, Ht = 12.0, 36.0
    v = D('curve', W, Ht)
    s = A('pointed', 'arc', width=W, U=24, mh=6, depth=Ht / 2)
    for t in (0.2, 3, 9, 17.9):
        assert wd(s, t) == pytest.approx(wd(v, t), abs=1e-12)


def test_rounded_geometry_is_unchanged_from_the_former_curved_lower():
    # the half ellipse of the previous pass (stored 'curve'), point for point
    for W, d in ((24, 12), (24, 4.5), (30, 21)):
        s = A('pointed', 'round', width=W, U=30, mh=6, depth=d)
        for t in (0.1, d / 3, d / 2, d * 0.9):
            assert wd(s, t) == pytest.approx(W * math.sqrt(1 - (1 - t / d) ** 2), abs=1e-12)


def test_curved_lower_stays_sharp_pointed():
    s, info = O.constrain(A('pointed', 'arc', width=24, U=30, depth=6), 'bottom_depth', LH, H, BEAD)
    assert s['bottom_depth'] == O.arc_min_depth(24, LH) == 13.5 and s['bottom_depth'] > 12
    assert info['changed']['bottom_depth'] == [6, 13.5]
    t, _ = O.constrain(dict(s, width=40), 'width', LH, H, BEAD)          # a wider opening deepens it
    assert t['bottom_depth'] > 20 and ok(t)
    assert O.evaluate(dict(s, bottom_depth=12), LH, H, BEAD)['reason'] == 'depth'
    r, _ = O.constrain(A('pointed', 'round', width=24, U=30, depth=6), 'bottom_depth', LH, H, BEAD)
    assert r['bottom_depth'] == 6, 'a Rounded bottom keeps any depth'


@pytest.mark.parametrize('frm,to', [(a, b) for a in ARCH_LOWERS for b in ARCH_LOWERS if a != b])
def test_switching_lower_profiles(frm, to):
    s, _ = O.constrain(A('curve', frm, width=24, U=1), 'width', LH, H, BEAD)
    t, info = O.constrain(dict(s, lower=to, bottom_depth=None if to == 'flat' else (s.get('bottom_depth') or 12)),
                          'lower', LH, H, BEAD)
    assert t['lower'] == to and ok(t) and info['conflict'] is None
    assert (t['width'], t['upper_height'], t['middle_height']) == (s['width'], s['upper_height'], s['middle_height'])
    d = O.parts(t)[0]
    assert t['height'] == pytest.approx(d + t['middle_height'] + t['upper_height'])
    if to == 'arc':
        assert d > 12


def test_arch_total_height_is_its_components():
    s = A('pointed', 'round', width=24, U=20, mh=15, depth=9)
    assert O.parts(s) == (9, 15, 20, 44) and s['height'] == 44
    s2 = dict(s, middle=False)
    O.sync(s2)
    assert s2['height'] == 29 and O.parts(s2)[1] == 0, 'a disabled middle adds nothing (its height is kept)'
    s3 = A('header', 'flat', width=36, U=99, mh=80)
    assert O.parts(s3) == (0, 80, 0, 80) and wd(s3, 79.99) == 36, 'header: a flat top'


# ---- component independence ------------------------------------------------------------------

@pytest.mark.parametrize('upper', ARCH_UPPERS)
def test_upper_height_resizes_the_actual_closure_not_the_sides(upper):
    s1, s2 = A(upper, 'flat', width=20, U=30, mh=20), A(upper, 'flat', width=20, U=45, mh=20)
    assert wd(s2, 20 + 15) > wd(s1, 20 + 15), 'a taller upper is fuller at the same height above the spring'
    assert wd(s1, 19.9) == wd(s2, 19.9) == 20, 'the sides (middle) are unchanged'
    assert wd(s1, 49.99) < 1 and wd(s2, 64.99) < 1, 'each closes at its own apex'
    assert wd(s1, 50.01) == 0 and wd(s2, 50.01) > 5, 'the pointed geometry itself is taller'
    if upper == 'pointed':                                   # straight flanks over the WHOLE upper height
        assert wd(s2, 20 + 22.5) == pytest.approx(10)


@pytest.mark.parametrize('field,value', [('middle_height', 37.5), ('middle', False), ('lower', 'round'), ('lower', 'arc'),
                                         ('bottom_depth', 4.5), ('z_bottom', 7.0), ('z_bottom', 0.4)])
def test_middle_lower_and_position_never_distort_the_upper(field, value):
    s = A('curve', 'pointed', width=24, U=0, mh=12, depth=6)
    s, _ = O.constrain(s, 'width', LH, H, BEAD)               # printable: U from the width
    if value == 'arc':
        s['bottom_depth'] = 18.0
    t, info = O.constrain(dict(s, **{field: value}), field, LH, H, BEAD)
    assert (t['width'], t['upper_height']) == (s['width'], s['upper_height']) and not info['changed']
    up = lambda q, u: wd(q, sum(O.parts(q)[:2]) + u)
    assert all(up(t, u) == pytest.approx(up(s, u)) for u in (1, 5, 10, 20))


def test_z_dimensions_are_whole_layers_so_every_boundary_is_a_layer_boundary():
    s, _ = O.constrain(A('curve', 'round', width=24, U=20.2, mh=13, depth=7.1, zb=6), 'width', LH, H, BEAD)
    for k in ('upper_height', 'middle_height', 'bottom_depth', 'height'):
        assert abs(s[k] / LH - round(s[k] / LH)) < 1e-9, k
    t, _ = O.constrain(D('pointed', 24, 31.3), 'height', LH, H, BEAD)
    assert t['height'] == 31.5
    # an older off-grid bottom (an old array spacing): judged on its ACTUAL layers
    u, _ = O.constrain(D('pointed', 30, 20, zb=6.4), 'width', LH, H, BEAD)
    assert ok(u) and max(closing_steps(u)) <= H + 1e-9


# ---- precedence --------------------------------------------------------------------------------

@pytest.mark.parametrize('style', DIAMONDS)
def test_diamond_height_edit_narrows_the_width(style):
    s, info = O.constrain(D(style, 48, 30), 'height', LH, H, BEAD)
    assert s['height'] == 30 and s['width'] < 48 and s['driver'] == 'height'
    assert ok(s) and max(closing_steps(s)) <= H + 1e-9
    assert not ok(dict(s, width=s['width'] + 1 / 16)), 'the widest printable width (1/16 in)'
    assert info['changed'] == {'width': [48, s['width']]} and info['conflict'] is None


@pytest.mark.parametrize('style', DIAMONDS)
def test_diamond_width_edit_raises_the_height(style):
    s, info = O.constrain(D(style, 30, 18), 'width', LH, H, BEAD)
    assert s['width'] == 30 and s['height'] > 18 and s['driver'] == 'width'
    assert ok(s) and max(closing_steps(s)) <= H + 1e-9
    assert not ok(dict(s, height=s['height'] - LH)), 'the lowest printable height (whole layers)'
    assert list(info['changed']) == ['height']


@pytest.mark.parametrize('upper', ARCH_UPPERS)
@pytest.mark.parametrize('lower', ARCH_LOWERS)
def test_arch_upper_height_edit_narrows_the_width(upper, lower):
    s, info = O.constrain(A(upper, lower, width=48, U=21), 'upper_height', LH, H, BEAD)
    assert s['upper_height'] == 21 and s['width'] < 48 and s['driver'] == 'upper_height'
    assert ok(s) and not ok(dict(s, width=s['width'] + 1 / 16))
    assert info['changed'] == {'width': [48, s['width']]}
    assert s['height'] == sum(O.parts(s)[:3]), 'the total follows its components'


@pytest.mark.parametrize('upper', ARCH_UPPERS)
@pytest.mark.parametrize('lower', ARCH_LOWERS)
def test_arch_width_edit_raises_the_upper_height(upper, lower):
    s0 = A(upper, lower, width=48, U=9, depth=30 if lower == 'arc' else 6)
    s, info = O.constrain(s0, 'width', LH, H, BEAD)
    assert s['width'] == 48 and s['upper_height'] > 9 and s['driver'] == 'width'
    assert ok(s) and not ok(dict(s, upper_height=s['upper_height'] - LH))
    assert list(info['changed']) == ['upper_height']
    assert (s['middle_height'], s['bottom_depth']) == (s0['middle_height'], s0['bottom_depth'])
    assert s['height'] == pytest.approx(s0['height'] - 9 + s['upper_height'])


def test_curved_needs_more_height_than_angular():
    a, _ = O.constrain(A('pointed', width=30, U=1), 'width', LH, H, BEAD)
    c, _ = O.constrain(A('curve', width=30, U=1), 'width', LH, H, BEAD)
    assert c['upper_height'] > a['upper_height'] > 30 * LH / (2 * H) - 1e-9
    da, _ = O.constrain(D('pointed', 30, 1), 'width', LH, H, BEAD)
    dc, _ = O.constrain(D('curve', 30, 1), 'width', LH, H, BEAD)
    assert dc['height'] > da['height'] and dc['height'] > 30, 'a vesica must be taller than wide'


@pytest.mark.parametrize('upper', ARCH_UPPERS)
def test_style_change_follows_the_driver(upper):
    other = 'curve' if upper == 'pointed' else 'pointed'
    s, _ = O.constrain(A(upper, width=24, U=42), 'upper_height', LH, H, BEAD)       # driver: upper height
    t, _ = O.constrain(dict(s, upper=other), 'upper', LH, H, BEAD)
    assert t['upper_height'] == 42 and ok(t)
    s, _ = O.constrain(A(upper, width=24, U=42), 'width', LH, H, BEAD)              # driver: width
    t, _ = O.constrain(dict(s, upper=other), 'upper', LH, H, BEAD)
    assert t['width'] == 24 and ok(t)


@pytest.mark.parametrize('style', DIAMONDS)
def test_maximum_overhang_and_layer_height_recalculate(style):
    s, _ = O.constrain(D(style, 48, 30), 'height', LH, H, BEAD)
    t, info = O.constrain(s, 'overhang', LH, 0.75, BEAD)
    assert t['height'] == 30 and t['width'] < s['width'] and ok(t, h=0.75)
    t, info = O.constrain(s, 'layer_height', 2.0, H, BEAD)
    assert t['height'] == 30 and t['width'] < s['width'] and ok(t, lh=2.0)
    s, _ = O.constrain(D(style, 24, 10), 'width', LH, H, BEAD)
    t, _ = O.constrain(s, 'overhang', LH, 0.75, BEAD)
    assert t['width'] == 24 and t['height'] > s['height'] and ok(t, h=0.75)


def test_less_overhang_needs_a_proportionally_taller_upper():
    U1 = O.constrain(A('pointed', width=36, U=1), 'width', LH, 1.5, BEAD)[0]['upper_height']
    U2 = O.constrain(A('pointed', width=36, U=1), 'width', LH, 0.75, BEAD)[0]['upper_height']
    assert 1.6 < U2 / U1 < 2.4


@pytest.mark.parametrize('lh,h', [(1.5, 1.5), (0.5, 1.5), (1.75, 0.25), (1.0, 0.5)])
@pytest.mark.parametrize('upper', ARCH_UPPERS)
def test_discrete_layer_overhang_compliance(lh, h, upper):
    s, _ = O.constrain(A(upper, 'round', width=33, U=1, depth=7, zb=3 * lh), 'width', lh, h, BEAD)
    assert max(closing_steps(s, lh, h)) <= h + 1e-9
    assert not ok(dict(s, upper_height=s['upper_height'] - lh), lh, h), 'minimal (whole layers)'


def test_a_sliver_is_cut_at_the_smallest_cut_never_dropped():
    s, _ = O.constrain(D('pointed', 24, 30), 'height', LH, H, BEAD)
    ws = [w for _, w in widths(s)]
    assert min(ws) >= O.wmin(BEAD, H) - 1e-12 and all(w > 0 for w in ws)


def test_header_upper_has_no_closure_constraint():
    s = A('header', 'flat', width=96, U=0, mh=84, zb=0)
    t, info = O.constrain(s, 'width', LH, H, BEAD)
    assert ok(s) and t['width'] == 96 and not info['changed'] and info['conflict'] is None
    assert O.limits(s, LH, H, BEAD) == {'max_width': None, 'min_height': None}


def test_flat_bottom_windows_and_ground_level_doors():
    door, _ = O.constrain(A('pointed', 'flat', width=36, U=1, mh=72, zb=0), 'width', LH, H, BEAD)
    assert door['z_bottom'] == 0 and wd(door, 0) == 36 and ok(door)
    win, _ = O.constrain(A('curve', 'flat', width=24, U=1, mh=6, zb=36), 'width', LH, H, BEAD)
    assert wd(win, 0) == 24 and ok(win), 'a flat sill is never a reason to reject an opening'
    assert widths(win)[0][1] == 24


def test_unprintable_is_a_reported_conflict():
    s, info = O.constrain(A('pointed', 'flat', width=24, U=10), 'upper_height', LH, 0.0, BEAD)
    assert info['conflict']
    s, info = O.constrain(A('header', 'flat', width=24, U=0, middle=False), 'upper', LH, H, BEAD)
    assert info['conflict'] and 'no height' in info['conflict']


# ---- arrays --------------------------------------------------------------------------------------

@pytest.mark.parametrize('lower', ['arc', 'round'])
def test_arrays_with_either_curved_lower(lower):
    arr = {'arrangement': 'grid', 'cols': 3, 'rows': 2, 'h_spacing': 8, 'v_spacing': 3}
    s, _ = O.constrain(A('pointed', lower, width=16, U=1, mh=6, depth=None, array=arr), 'width', LH, H, BEAD)
    _, r = _resolve(_profile(s, position=20, array=OpeningArray('grid', cols=3, rows=2, h_spacing=8, v_spacing=3)))
    assert r.valid, r.errors
    seqs = {}
    for L in r.layers:
        seqs.setdefault(L['member'], []).append(round(L['width'], 4))
    assert len(seqs) == 6 and len({tuple(v) for v in seqs.values()}) == 1, 'identical clones'
    first = min(v[0] for v in seqs.values())
    assert first < 16 and [round(w, 4) for _, w in widths(dict(s, z_bottom=6))] == seqs[0]


def test_array_members_share_the_master_and_its_constraints():
    arr = {'arrangement': 'staggered', 'cols': 3, 'rows': 3, 'h_spacing': 6, 'v_spacing': 4.6, 'stagger': None}
    s, _ = O.constrain(A('curve', 'pointed', width=20, U=1, depth=5, array=arr), 'width', LH, H, BEAD)
    assert ok(s)
    offs = O.array_offsets(s['array'], s['width'], s['height'])
    for dx, dz in offs:                                      # every clone printable, the same shape
        c = dict(s, z_bottom=s['z_bottom'] + dz, array=None)
        assert ok(c) and max(closing_steps(c)) <= H + 1e-9
    pitch = offs[1][0] - offs[0][0]
    assert pitch == pytest.approx(s['width'] + 6), 'clear spacing kept'


# ---- migration ------------------------------------------------------------------------------------

def test_older_shapes_migrate():
    lens = {'shape': 'lens', 'width': 12, 'height': 36, 'z_bottom': 6}
    assert O.migrate(lens) and (lens['shape'], lens['upper'], lens['lower']) == ('diamond', 'curve', 'curve')
    mixed = {'shape': 'diamond', 'upper': 'pointed', 'lower': 'curve', 'bottom_depth': 9, 'width': 12, 'height': 30}
    O.migrate(mixed)
    assert (mixed['upper'], mixed['lower'], mixed['bottom_depth']) == ('pointed', 'pointed', None)
    semi = {'shape': 'arch', 'upper': 'curve', 'lower': 'semicircle', 'width': 20, 'height': 50}
    O.migrate(semi)
    assert (semi['lower'], semi['bottom_depth']) == ('round', 10)
    ell = {'shape': 'arch', 'upper': 'curve', 'lower': 'ellipse', 'width': 20, 'height': 50}
    O.migrate(ell)
    assert (ell['lower'], ell['bottom_depth']) == ('round', 5)
    prev = {'shape': 'arch', 'upper': 'curve', 'lower': 'curve', 'bottom_depth': 7.5, 'width': 20, 'height': 50,
            'upper_height': 30, 'middle': True, 'middle_height': 12.5}       # the former Curved lower
    O.migrate(prev)
    assert (prev['lower'], prev['bottom_depth']) == ('round', 7.5), 'loads as Rounded, its geometry kept'
    old = {'shape': 'arch', 'upper': 'pointed', 'lower': 'flat', 'width': 24, 'height': 42, 'closure': 21.0,
           'z_bottom': 0}
    O.migrate(old)
    O.defaults(old)
    assert old['upper_height'] == 21 and 'closure' not in old
    O.fill(old, LH, H, BEAD)
    assert (old['middle'], old['middle_height'], old['height']) == (True, 21, 42), 'the old total height kept'


def test_older_arch_with_the_minimum_closure_keeps_its_total_height():
    old = {'shape': 'arch', 'upper': 'pointed', 'lower': 'flat', 'width': 24, 'height': 60, 'z_bottom': 0}
    s, info = O.constrain(old, 'load', LH, H, BEAD)
    assert s['height'] == 60 and s['middle'] and ok(s)
    assert s['upper_height'] == O.min_height(dict(s, middle=False), LH, H, BEAD, LH)


def test_profile_dicts_round_trip_and_migrate():
    p = profile_from_dict({'kind': 'opening', 'id': 'p1', 'wall': {'source': 'L', 'edge': None}, 'position': 60,
                           'z_bottom': 6, 'height': 24, 'width': 12, 'shape': 'lens'})
    assert (p.shape, p.upper, p.lower) == ('diamond', 'curve', 'curve')
    q = OpeningProfile('p2', WallRef('L'), 60, 0, 0, 36, 'arch', upper='header', lower='flat', middle=True,
                       middle_height=84, upper_height=20, header={'bearing': 10, 'thickness': 8})
    d = q.to_dict()
    assert d['header'] == {'bearing': 10, 'thickness': 8} and d['upper_height'] == 20
    assert profile_from_dict(d) == q
    with pytest.raises(ValueError):
        profile_from_dict(dict(d, shape='diamond'))           # a diamond has no header upper


# ---- resolution: the backend cuts exactly the constrained shape ---------------------------------

def _resolve(p, top=150):
    a = Assembly(LH, [Section('A', top)], profiles=[p], max_overhang=H)
    res, rep = apply_profiles(resolve(a, ['A']), [p], {'A': {'L': WALL}}, BEAD, H)
    return res, rep[0]


def _profile(s, **kw):
    return OpeningProfile('P', WallRef('L'), kw.pop('position', 120), s['z_bottom'], s['height'], s['width'],
                          s['shape'], upper=s['upper'], lower=s['lower'], bottom_depth=s.get('bottom_depth'),
                          upper_height=s.get('upper_height'), middle=s.get('middle'),
                          middle_height=s.get('middle_height'), driver=s.get('driver'), **kw)


@pytest.mark.parametrize('s0,edit', [(D('pointed'), 'height'), (D('curve'), 'width'),
                                     (A('pointed', 'flat'), 'upper_height'), (A('curve', 'round'), 'width'), (A('pointed', 'arc'), 'width'),
                                     (A('pointed', 'pointed', middle=False), 'width'),
                                     (A('header', 'round', mh=30), 'width'), (A('header', 'arc', mh=30), 'width')])
def test_constrained_shapes_resolve_valid_with_identical_layers(s0, edit):
    s, _ = O.constrain(s0, edit, LH, H, BEAD)
    _, r = _resolve(_profile(s))
    assert r.valid, r.errors
    assert [(L['layer'], L['width']) for L in r.layers] == [(k, round(w, 4)) for k, w in widths(s)]
    assert r.worst_step <= H + 1e-9
    assert r.geometry['height'] == pytest.approx(s['height'])


def test_unconstrained_too_steep_is_reported_with_hints():
    s = A('pointed', 'flat', width=48, U=6)
    _, r = _resolve(_profile(s))
    assert not r.valid and 'not self-supporting' in r.errors[0]
    assert r.hint['field'] == 'upper_height' and r.hint['max_width'] < 48 and r.hint['min_height'] > 6


# ---- HEADER upper: generated headers --------------------------------------------------------------

def test_header_opening_generates_one_associated_header():
    s = A('header', 'flat', width=36, U=0, mh=72, zb=0)
    res, r = _resolve(_profile(s))
    assert r.valid, r.errors
    assert len(r.headers) == 1
    h = r.headers[0]
    last = max(L['layer'] for L in r.layers)
    assert (h['id'], h['profile'], h['member']) == ('P-header', 'P', 0)
    assert h['section'] == 0 and h['layer'] == last + 1 and res.instances[last + 1].z_bottom >= 72 - 1e-9
    assert (h['x'], h['y'], h['angle'], h['span']) == (120, 0, 0, 36)
    assert (h['bearing'], h['thickness'], h['depth']) == (8, 6, 13), 'defaults; depth = wall + 1 bead'
    assert h['snap'], 'snapped to the detected span like a placed header'
    assert r.worst_step == 0, 'the flat top is carried by the header, not an overhang'


def test_header_settings_are_the_profiles_and_persist():
    s = A('header', 'flat', width=30, U=0, mh=60, zb=0)
    _, r = _resolve(_profile(s, header={'bearing': 12, 'depth': 16, 'thickness': 9}))
    h = r.headers[0]
    assert (h['bearing'], h['depth'], h['thickness']) == (12, 16, 9)
    # switching to another upper keeps the settings (no header generated); back → restored
    p = _profile(dict(s, upper='pointed', upper_height=24), header={'bearing': 12, 'depth': 16, 'thickness': 9})
    _, r2 = _resolve(p)
    assert r2.headers == [] and p.to_dict()['header']['bearing'] == 12
    _, r3 = _resolve(profile_from_dict(dict(p.to_dict(), upper='header')))
    assert len(r3.headers) == 1 and r3.headers[0]['bearing'] == 12


def test_header_arrays_one_header_per_opening_no_duplicates():
    s = A('header', 'flat', width=20, U=0, mh=24, zb=6)
    for n in (4, 2, 3):
        arr = OpeningArray('horizontal', count=n, h_spacing=10)
        _, r = _resolve(_profile(s, position=30, array=arr))
        assert r.valid, r.errors
        ids = [h['id'] for h in r.headers]
        assert ids == [f'P-header-{j + 1}' for j in range(n)] and len(set(ids)) == n
        assert [h['x'] for h in r.headers] == [30 + 30 * j for j in range(n)]
    arr = OpeningArray('grid', cols=2, rows=2, h_spacing=10, v_spacing=6)
    _, r = _resolve(_profile(s, position=30, array=arr))
    assert len(r.headers) == 4 and len({(h['layer'], h['x']) for h in r.headers}) == 4
    assert len(profile_headers([r])) == 4


def test_invalid_or_top_reaching_header_opening_generates_no_header():
    s = A('header', 'flat', width=20, U=0, mh=150, zb=0)              # its flat top = the stack top
    _, r = _resolve(_profile(s))
    assert not r.valid and r.headers == []
    assert any('nothing would rest on its header' in e or 'no wall above' in e for e in r.errors)


def test_saved_former_curved_lower_opens_as_rounded_with_its_geometry():
    """Save / Open compatibility: an Assembly saved with the former Curved
    (half-ellipse) lower — stored 'curve' — opens as Rounded and cuts the
    same layers; saved again it is 'round'."""
    old = {'kind': 'opening', 'id': 'P', 'wall': {'source': 'L', 'edge': None}, 'position': 120, 'z_bottom': 6,
           'height': 51, 'width': 24, 'shape': 'arch', 'label': '', 'upper': 'pointed', 'lower': 'curve',
           'bottom_depth': 7.5, 'upper_height': 30, 'middle': True, 'middle_height': 13.5}
    a = Assembly.from_dict({'layer_height': LH, 'sections': [{'design_id': 'A', 'height': 150}], 'max_overhang': H,
                            'profiles': [old]})
    p = a.profiles[0]
    assert (p.lower, p.bottom_depth, p.height) == ('round', 7.5, 51)
    _, r = _resolve(p)
    s = dict(p.shape_dict())
    assert r.valid and [(L['layer'], L['width']) for L in r.layers] == [(k, round(w, 4)) for k, w in widths(s)]
    for t in (0.75, 3.75, 6.75):                             # the half ellipse of depth 7.5, unchanged
        assert wd(s, t) == pytest.approx(24 * math.sqrt(1 - (1 - t / 7.5) ** 2))
    again = Assembly.from_dict(a.to_dict())
    assert again.profiles[0].to_dict()['lower'] == 'round' and again.profiles == a.profiles
