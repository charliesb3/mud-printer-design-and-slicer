"""
SELF-SUPPORTING OPENINGS, extended (2026-10-09) — the pure model
(layer_assembly/profiles.py, no Designer):
  * ARRAYS: one parametric profile → identical clones; seven arrangements;
    spacing = CLEAR distance between opening edges; validation of overlaps,
    material between openings, wall ends, several profiles on one wall;
  * ARCH shapes: an UPPER closure (pointed / smooth curve) whose height is
    the minimum self-supporting one on the ACTUAL layers + a LOWER profile
    (flat — ground-level doors / angular / curved (sharp vesica half, 'arc') / rounded (half
    ellipse, 'round'); older semicircle / ellipse / the former 'curve' migrate to rounded);
  * CONVERSION of a Designer opening: its rectangle stays the Designer's
    (never cut twice), the closure is added above it, insufficient wall above
    is reported with the missing height.
"""
import json
import math

import pytest

from layer_assembly.model import Assembly, Section, resolve
from layer_assembly.profiles import (OpeningProfile, OpeningArray, WallRef, WallTarget, apply_profiles,
                                     profile_from_dict, cuts_to_dicts, cuts_from_dicts,
                                     _wmin, ProfileError, PlaneProfile)
from layer_assembly import opening_shape as OS

BEAD, H = 3.0, 1.5
WALL = WallTarget('L', None, (0.0, 0.0), (480.0, 0.0), 480.0, 10.0, True, 'Wall')
TG = {'A': {'L': WALL}, 'B': {'L': WALL}}
DOOR = {'id': 'door', 'source': 'L', 'edge': None, 'at': 200.0, 'width': 36.0, 'label': 'Door'}


def _run(profiles, height=120, lh=1.5, targets=TG, h=H, sections=None, openings=None, sources=None):
    a = Assembly(lh, sections or [Section('A', height)], profiles=profiles, max_overhang=h)
    return apply_profiles(resolve(a, ['A', 'B']), profiles, targets, BEAD, h, sources, openings)


def _ops(res, kind='opening'):
    return {k: sorted((c[3], c[4]) for c in i.cuts if c[0] == kind) for k, i in enumerate(res.instances)}


def _arr(arr, **kw):
    a = dict(arrangement='single')
    a.update(arr)
    return OpeningArray(**a)


def _prof(width=18, height=36, pos=40, zb=6, shape='diamond', array=None, **kw):
    return OpeningProfile('P', WallRef('L'), pos, zb, height, width, shape, array=array, **kw)


# ---- ARRAYS ----------------------------------------------------------------------------

ARR = [('single', {}, [(40, 6)]),
       ('horizontal', dict(count=3, h_spacing=12), [(40, 6), (70, 6), (100, 6)]),
       ('vertical', dict(count=3, v_spacing=6), [(40, 6), (40, 48), (40, 90)]),
       ('diagonal_up', dict(count=3, h_spacing=6, v_spacing=-12), [(40, 6), (64, 30), (88, 54)]),
       ('diagonal_down', dict(count=3, h_spacing=6, v_spacing=-12), [(40, 54), (64, 30), (88, 6)]),
       ('grid', dict(cols=3, rows=2, h_spacing=12, v_spacing=6), [(40, 6), (70, 6), (100, 6), (40, 48), (70, 48), (100, 48)]),
       ('staggered', dict(cols=3, rows=2, h_spacing=12, v_spacing=6), [(40, 6), (70, 6), (100, 6), (55, 48), (85, 48), (115, 48)])]


@pytest.mark.parametrize('arrangement,params,expected', ARR, ids=[a[0] for a in ARR])
def test_all_seven_arrangements_place_identical_clones_by_clear_spacing(arrangement, params, expected):
    p = _prof(array=_arr(dict(arrangement=arrangement, **params)))
    assert sorted(p.members()) == sorted(map(lambda t: (float(t[0]), float(t[1])), expected))
    res, rep = _run([p], height=160)
    assert rep[0].valid, rep[0].errors
    if arrangement == 'vertical':                   # beyond the stack top: reported, never trimmed
        r = _run([p], height=120)[1][0]
        assert not r.valid and any('above the top of the stack' in e for e in r.errors)
    # every clone is cut, each with the master's widths (shifted in Z only)
    by_member = {}
    for L in rep[0].layers:
        by_member.setdefault((L['at']), []).append(L['width'])
    seqs = {tuple(v) for v in by_member.values()}
    assert len(by_member) == len({m[0] for m in expected}) or arrangement == 'vertical'
    assert len({tuple(round(w, 6) for w in s) for s in seqs if len(s) == max(map(len, seqs))}) == 1
    # ONE cut op per opening per layer (each its own place)
    ops = _ops(res)
    for k, v in ops.items():
        assert sorted(a for a, _ in v) == sorted(L['at'] for L in rep[0].layers if L['layer'] == k)


def test_spacing_is_the_clear_distance_between_opening_edges():
    for sp in (6.0, 12.5, 30.0):
        p = _prof(width=18, height=36, array=_arr(dict(arrangement='horizontal', count=3, h_spacing=sp)))
        res, rep = _run([p])
        widest = max(L['width'] for L in rep[0].layers)
        k = next(M['layer'] for M in rep[0].layers if M['width'] == widest)
        row = sorted(L['at'] for L in rep[0].layers if L['layer'] == k)
        assert len(row) == 3
        # centres one width + the clear spacing apart: the widest EDGES are `sp` apart
        assert all(abs((b - a) - 18 - sp) < 1e-9 for a, b in zip(row, row[1:])), (row, sp)
        assert all((b - a) - widest >= sp - 1e-9 for a, b in zip(row, row[1:]))   # (a sampled layer: never closer)
    # vertical: from one opening's top tip to the next one's bottom tip
    p = _prof(width=18, height=36, array=_arr(dict(arrangement='vertical', count=2, v_spacing=9)))
    assert [z for _, z in p.members()] == [6.0, 51.0]


def test_stagger_defaults_to_half_the_horizontal_repeat_and_can_be_set():
    p = _prof(width=20, array=_arr(dict(arrangement='staggered', cols=2, rows=3, h_spacing=10)))
    rows = sorted({z for _, z in p.members()})
    xs = sorted({x for x, z in p.members() if z == rows[1]})
    assert xs == [55.0, 85.0]                                         # row 2: shifted (20 + 10) / 2
    q = _prof(width=20, array=_arr(dict(arrangement='staggered', cols=2, rows=3, h_spacing=10, stagger=4)))
    assert sorted({x for x, z in q.members() if z == rows[1]}) == [44.0, 74.0]
    assert sorted({x for x, z in q.members() if z == rows[2]}) == [40.0, 70.0]   # every SECOND row


def test_editing_the_master_updates_every_opening():
    a = _arr(dict(arrangement='grid', cols=3, rows=2, h_spacing=12, v_spacing=6))
    _, r1 = _run([_prof(width=18, array=a, shape='arch', height=40)])
    _, r2 = _run([_prof(width=12, array=a, shape='arch', height=40)])
    w1 = {round(L['width'], 6) for L in r1[0].layers}
    w2 = {round(L['width'], 6) for L in r2[0].layers}
    assert max(w1) == 18 and max(w2) == 12
    assert len(r2[0].members) == 6
    # positions follow the new width (clear spacing kept)
    assert sorted({m['position'] for m in r2[0].members}) == [40.0, 64.0, 88.0]


def test_overlapping_and_too_close_openings_are_refused():
    over = _prof(array=_arr(dict(arrangement='horizontal', count=2, h_spacing=-4)))
    _, rep = _run([over])
    assert not rep[0].valid and any('overlap' in e for e in rep[0].errors)
    thin = _prof(array=_arr(dict(arrangement='horizontal', count=2, h_spacing=4)))   # < 2 beads of wall
    res, rep = _run([thin])
    assert not rep[0].valid and any('of wall between' in e and 'needs ≥ 6.00 in' in e for e in rep[0].errors)
    assert not any(i.cuts for i in res.instances), 'an invalid profile cuts nothing'
    ok = _prof(array=_arr(dict(arrangement='horizontal', count=2, h_spacing=6)))
    assert _run([ok])[1][0].valid
    # vertically: a solid layer must separate two openings of a column
    tight = _prof(array=_arr(dict(arrangement='vertical', count=2, v_spacing=0)))
    _, rep = _run([tight])
    assert not rep[0].valid and any('no solid layer' in e for e in rep[0].errors)
    assert _run([_prof(array=_arr(dict(arrangement='vertical', count=2, v_spacing=3)))])[1][0].valid


def test_construction_minimum_applies_between_openings():
    gen = WallTarget('L', None, (0.0, 0.0), (480.0, 0.0), 480.0, 10.0, True, 'Wall', min_length=20.0)
    p = _prof(array=_arr(dict(arrangement='horizontal', count=2, h_spacing=12)))
    _, rep = _run([p], targets={'A': {'L': gen}})
    assert not rep[0].valid and any('needs ≥ 20.00 in' in e for e in rep[0].errors)


def test_array_beyond_the_wall_is_refused():
    p = _prof(pos=40, array=_arr(dict(arrangement='horizontal', count=20, h_spacing=12)))
    _, rep = _run([p])
    assert not rep[0].valid and any('does not fit the wall' in e for e in rep[0].errors)


def test_several_profiles_on_one_wall_are_checked_together():
    a = OpeningProfile('P1', WallRef('L'), 60, 6, 36, 18)
    b = OpeningProfile('P2', WallRef('L'), 70, 6, 36, 18)               # overlaps P1
    c = OpeningProfile('P3', WallRef('L'), 120, 6, 36, 18, array=_arr(dict(arrangement='horizontal', count=2)))
    res, rep = _run([a, b, c])
    by = {r.id: r for r in rep}
    assert by['P1'].valid and by['P3'].valid
    assert not by['P2'].valid and any('overlap' in e for e in by['P2'].errors)
    assert {c_[5] for i in res.instances for c_ in i.cuts if c_[0] == 'opening'} == {'P1', 'P3'}


def test_openings_crossing_a_designer_opening_are_refused_not_resized():
    """2026-10-10: the Designer's own openings of each layer take part in the
    spacing check — an array clone (or any self-supporting opening) that
    overlaps or crowds one is reported (and not generated); one clear of it
    is unaffected. Two Designer openings are never judged here."""
    ds = [Section('A', 36), Section('B', 24), Section('A', 60)]
    ops = {'B': [DOOR]}                                  # the door: layers of design B only (z 36–60)
    a = _arr(dict(arrangement='horizontal', count=3, h_spacing=12))
    bad = _prof(width=24, height=36, pos=150, zb=30, array=a)         # clones at 150, 186, 222: over the door
    _, rep = _run([bad], sections=ds, openings=ops)
    assert not rep[0].valid and any('Designer opening Door' in e and 'overlap' in e for e in rep[0].errors)
    near = _prof(width=24, height=36, pos=166, zb=30)                 # edge 178, door edge 182: a 4 in pier
    _, rep = _run([near], sections=ds, openings=ops)
    assert not rep[0].valid and any('of wall between' in e and 'Door' in e for e in rep[0].errors)
    ok = _prof(width=24, height=36, pos=100, zb=30)
    _, rep = _run([ok], sections=ds, openings=ops)
    assert rep[0].valid
    above = _prof(width=24, height=24, pos=200, zb=60)                # right on top of the door: no solid layer
    _, rep = _run([above], sections=ds, openings=ops)
    assert not rep[0].valid and any('no solid layer' in e for e in rep[0].errors)


def test_array_round_trips_and_older_dicts_are_unchanged():
    p = _prof(shape='arch', upper='curve', lower='ellipse', bottom_depth=4,
              array=_arr(dict(arrangement='staggered', cols=4, rows=3, h_spacing=8, v_spacing=6, stagger=5)))
    d = json.loads(json.dumps(p.to_dict()))
    assert profile_from_dict(d) == p
    a = Assembly(1.5, [Section('A', 120)], profiles=[p])
    assert Assembly.from_dict(json.loads(json.dumps(a.to_dict()))).profiles == [p]
    old = {'kind': 'opening', 'id': 'P', 'wall': {'source': 'L', 'edge': None}, 'position': 60, 'z_bottom': 6,
           'height': 48, 'width': 24, 'shape': 'lens', 'label': ''}
    q = profile_from_dict(old)                       # a saved LENS → the Curved Diamond (2026-10-10)
    assert q.array is None and q.source_opening is None
    exp = {k: v for k, v in old.items() if k != 'shape'}
    assert q.to_dict() == {**exp, 'position': 60.0, 'z_bottom': 6.0, 'height': 48.0, 'width': 24.0,
                           'upper': 'curve', 'lower': 'arc', 'bottom_depth': 24.0, 'upper_height': 24.0,
                           'middle': False, 'middle_height': None}
    assert profile_from_dict(q.to_dict()) == q
    with pytest.raises(ProfileError):
        profile_from_dict({**old, 'array': {'arrangement': 'spiral'}})
    with pytest.raises(ProfileError):
        profile_from_dict({**old, 'array': {'arrangement': 'grid', 'cols': 0}})
    with pytest.raises(ProfileError):
        profile_from_dict({**old, 'shape': 'arch', 'lower': 'gothic'})
    with pytest.raises(ProfileError):
        profile_from_dict({**exp, 'upper': 'pointed', 'lower': 'flat', 'middle': True, 'middle_height': 10,
                           'upper_height': 10, 'source_opening': {'id': 'o', 'z_ref': 1}, 'upper_x': 0,
                           'header': None} | {'upper': 'header'})


def test_cut_spec_of_an_array_round_trips_with_one_station_per_column():
    p = _prof(array=_arr(dict(arrangement='grid', cols=3, rows=2, h_spacing=12, v_spacing=6)))
    res, rep = _run([p])
    for i in res.instances:
        assert cuts_from_dicts(cuts_to_dicts(i.cuts)) == i.cuts
        st = sorted(c[3] for c in i.cuts if c[0] == 'station')
        assert st == [40.0, 70.0, 100.0]


# ---- ARCH SHAPES -------------------------------------------------------------------------

def test_shape_functions():
    assert OS.upper_half('pointed', 10, 20, 0) == 5 and OS.upper_half('pointed', 10, 20, 10) == 2.5
    assert OS.upper_half('curve', 10, 20, 0) == pytest.approx(5) and OS.upper_half('curve', 10, 20, 20) == 0
    assert OS.lower_half('flat', 24, 0, 0.01) == 12
    for t in (1.2, 6, 10.8):                                          # a TRUE circle (depth = R)
        assert abs(OS.lower_half('round', 24, 12, t) - math.sqrt(144 - (12 - t) ** 2)) < 1e-12


def _arch_widths(rep):
    return {L['layer']: L['width'] for L in rep[0].layers}


def _arch(width, U=None, middle=None, depth=None, **kw):
    """An arch from its components (U None: an older arch — the minimum)."""
    kw.setdefault('shape', 'arch')
    return _prof(width=width, upper_height=U, middle=middle if middle is not None else (None if U is None else True),
                 bottom_depth=depth, **kw)


def test_semicircle_bottom_is_a_true_semicircle():
    W, zb = 24.0, 6.0
    res, rep = _run([_prof(width=W, height=60, zb=zb, shape='arch', lower='curve')])
    assert rep[0].valid and rep[0].geometry['lower_depth'] == 12.0       # curved, default depth = W / 2
    R = W / 2
    for k, w in _arch_widths(rep).items():
        z = 0.5 * (res.instances[k].z_bottom + res.instances[k].z_top) - zb
        if z < R:
            assert abs(w - 2 * math.sqrt(R * R - (R - z) ** 2)) < 1e-3 or w == pytest.approx(_wmin(BEAD, H))


def test_elliptical_bottom_depth_is_exposed():
    res, rep = _run([_prof(width=24, height=60, zb=6, shape='arch', lower='ellipse', bottom_depth=4.5)])
    w = _arch_widths(rep)
    first = min(w)
    # 3 layers of 1.5 in: the half-ellipse, then the full width
    assert [w[k] for k in range(first, first + 4)] == pytest.approx(
        [24 * math.sqrt(1 - (1 - t) ** 2) for t in (0.75 / 4.5, 2.25 / 4.5, 3.75 / 4.5)] + [24.0], abs=1e-3)
    _, deep = _run([_prof(width=24, height=60, zb=6, shape='arch', lower='curve', bottom_depth=12)])
    assert deep[0].geometry['lower_depth'] == 12


def test_flat_bottom_windows_and_ground_level_doors():
    res, rep = _run([_prof(width=30, height=84, zb=0, pos=200, shape='arch', lower='flat')])
    w = _arch_widths(rep)
    assert w[0] == 30.0 and rep[0].valid, 'a door is cut from the ground layer at full width'
    res, rep = _run([_prof(width=30, height=48, zb=36, pos=200, shape='arch', lower='flat')])
    w = _arch_widths(rep)
    assert min(w) == 24 and w[24] == 30.0                              # the sill: full width at once


@pytest.mark.parametrize('upper', ['pointed', 'curve'])
@pytest.mark.parametrize('W,lh,h', [(36, 1.5, 1.5), (24, 1.25, 1.0), (30, 1.75, 1.5), (12, 1.5, 0.75)])
def test_older_arch_gets_the_minimum_upper_on_every_layer(upper, W, lh, h):
    """An older (closure-based) arch: its upper height = the minimum
    printable one (opening_shape.fill), the rest of its height the middle."""
    Ht = lh * round(200 / lh)
    p = _prof(width=W, height=Ht, zb=lh * 4, pos=200, shape='arch', upper=upper, lower='flat')
    res, rep = _run([p], height=240, lh=lh, h=h)
    assert rep[0].valid, rep[0].errors
    w = _arch_widths(rep)
    seq = [w[k] for k in sorted(w)] + [0.0]                             # + the closed layer above
    for a, b in zip(seq, seq[1:]):
        assert b >= a - 1e-9 or (a - b) / 2 <= h + 1e-9, (a, b)          # every narrowing step, the closure too
    g = rep[0].geometry
    assert g['height'] == pytest.approx(Ht) and g['middle'] + g['upper_height'] == pytest.approx(Ht)
    s = dict(p.completed(lh, h, BEAD).shape_dict())
    assert not OS.evaluate(dict(s, upper_height=g['upper_height'] - lh, height=0), lh, h, BEAD)['ok'], 'minimal'
    assert rep[0].worst_step <= h + 1e-9


def test_smooth_curve_needs_a_taller_upper_than_a_pointed_one():
    cp = OS.min_height(dict(_arch(36, 1, False).shape_dict(), upper='pointed'), 1.5, H, BEAD, 1.5)
    cc = OS.min_height(dict(_arch(36, 1, False).shape_dict(), upper='curve'), 1.5, H, BEAD, 1.5)
    assert 17.9 < cp < 20 and cc > cp * 1.4                           # pointed ≈ W·lh / 2h
    assert OS.min_height(dict(_arch(36, 1, False).shape_dict()), 1.5, 0.75, BEAD, 1.5) > 1.9 * cp


def test_older_arch_too_short_for_its_closure_grows_to_its_minimum():
    _, rep = _run([_prof(width=36, height=16, zb=6, pos=200, shape='arch', lower='flat')])
    g = rep[0].geometry
    assert rep[0].valid and g['middle'] == 0 and g['height'] == g['upper_height'] > 16


def test_upper_height_below_the_minimum_is_reported_with_hints():
    _, rep = _run([_arch(36, 10, True, middle_height=40, zb=6, pos=200)])
    assert not rep[0].valid and any('not self-supporting' in e for e in rep[0].errors)
    assert rep[0].hint['field'] == 'upper_height' and rep[0].hint['min_height'] > 10 and rep[0].hint['max_width'] < 36
    _, rep = _run([_arch(36, 30, True, middle_height=40, zb=6, pos=200)])
    assert rep[0].valid and rep[0].geometry['upper_height'] == 30 and rep[0].worst_step < H


def test_rounded_bottom_widening_faster_than_the_overhang_is_explained():
    _, rep = _run([_prof(width=24, height=60, zb=6, shape='arch', lower='curve')])
    assert rep[0].valid and any('jamb caps' in w for w in rep[0].warnings)
    _, rep = _run([_prof(width=24, height=60, zb=6, shape='arch', lower='flat')])
    assert not any('jamb caps' in w for w in rep[0].warnings)


def test_diamond_and_legacy_lens_resolve_as_single_openings():
    """A classic Diamond resolves as before (pointed both ends, widest at
    mid-height); a saved Lens is migrated (the curved diamond)."""
    for shape, height in (('diamond', 48), ('lens', 66)):
        p = OpeningProfile('P', WallRef('L'), 60, 6, height, 24, shape)
        res, rep = _run([p])
        assert rep[0].valid and len(rep[0].members) == 1
        assert all(set(L) == {'layer', 'z', 'design', 'width', 'at'} for L in rep[0].layers)
        assert {c[3] for i in res.instances for c in i.cuts if c[0] in ('opening', 'station')} == {60.0}


# ---- CONVERSION of a Designer opening ------------------------------------------------------

def _door_stack(above=60, below=36, door=24):
    """Base (A) below, the Door Gap (B, a Designer opening) for `door` in,
    Base above."""
    return [Section('A', below), Section('B', door), Section('A', above)]


def _conv(upper='pointed', upper_height=None, z_ref=40):
    return OpeningProfile('C', WallRef('L'), 0, 0, 0, 0, 'arch', upper=upper, lower='flat', upper_height=upper_height,
                          source_opening={'id': 'door', 'z_ref': z_ref})


OPS = {'A': [], 'B': [DOOR]}


def test_conversion_keeps_the_designer_rectangle_and_adds_the_minimum_closure_above():
    res, rep = _run([_conv()], sections=_door_stack(), openings=OPS)
    r = rep[0]
    assert r.valid, r.errors
    cv = r.conversion
    assert (cv['rect_bottom'], cv['rect_top'], cv['width'], cv['position']) == (36.0, 60.0, 36.0, 200.0)
    assert 18 <= cv['upper_height'] < 19 and cv['required'] <= cv['available']
    door_layers = range(24, 40)
    ops = _ops(res)
    # NO second cut where the Designer opening prints (never two active cuts for one opening)
    assert all(ops[k] == [] for k in door_layers)
    assert all(L.get('designer') for L in r.layers if L['layer'] in door_layers)
    # the closure: layers above the rectangle, each ≤ h narrower a side, starting at the clear width
    cl = [ops[k] for k in range(40, 60) if ops[k]]
    assert cl and all(c[0][0] == 200.0 for c in cl)
    seq = [36.0] + [c[0][1] for c in cl] + [0.0]
    assert all((a - b) / 2 <= H + 1e-9 for a, b in zip(seq, seq[1:]))
    # the cross-Z transition only ABOVE (the rectangle and the layers below stay as authored)
    tr = {k for k, i in enumerate(res.instances) for c in i.cuts if c[0] == 'transition'}
    assert tr and min(tr) > max(k for k in range(60) if ops.get(k))


def test_conversion_follows_the_designer_opening_and_the_stack():
    moved = {'A': [], 'B': [dict(DOOR, at=150.0, width=30.0)]}
    _, rep = _run([_conv()], sections=_door_stack(door=30), openings=moved)
    cv = rep[0].conversion
    assert (cv['position'], cv['width'], cv['rect_top']) == (150.0, 30.0, 66.0)
    # across a section boundary: two sections of designs that both print the opening = one rectangle
    both = {'A': [], 'B': [DOOR], 'C': [DOOR]}
    tg = dict(TG, C={'L': WALL})
    a = Assembly(1.5, [Section('A', 36), Section('B', 12), Section('C', 12), Section('A', 60)], profiles=[_conv()])
    res, rep = apply_profiles(resolve(a, ['A', 'B', 'C']), [_conv()], tg, BEAD, H, None, both)
    assert rep[0].valid and (rep[0].conversion['rect_bottom'], rep[0].conversion['rect_top']) == (36.0, 60.0)


def test_conversion_with_insufficient_wall_above_is_reported_never_shortened():
    res, rep = _run([_conv()], sections=_door_stack(above=12), openings=OPS)
    r = rep[0]
    assert not r.valid
    assert any('not enough wall above the opening' in e and 'only 12.00 in available' in e for e in r.errors)
    assert r.hint['available'] == 12.0 and r.hint['missing'] > 5
    assert not any(i.cuts for i in res.instances), 'nothing is cut: the Designer opening prints as before'
    _, rep = _run([_conv(upper='curve')], sections=_door_stack(above=24), openings=OPS)
    assert not rep[0].valid                                            # a smooth closure needs ~28 in
    _, rep = _run([_conv(upper='pointed')], sections=_door_stack(above=24), openings=OPS)
    assert rep[0].valid


def test_conversion_of_a_removed_or_misplaced_opening_is_reported():
    _, rep = _run([_conv(z_ref=10)], sections=_door_stack(), openings=OPS)
    assert not rep[0].valid and any('no longer in the layer' in e for e in rep[0].errors)
    _, rep = _run([_conv()], sections=_door_stack(), openings={'A': [], 'B': []})
    assert not rep[0].valid
    curved = {'A': [], 'B': [{'id': 'door', 'source': 'L', 'width': 36.0}]}
    _, rep = _run([_conv()], sections=_door_stack(), openings=curved)
    assert not rep[0].valid and any('straight wall' in e for e in rep[0].errors)


def test_conversion_coexists_with_an_array_on_the_same_wall_and_a_planar_cut_is_named():
    arr = _prof(pos=40, zb=60, array=_arr(dict(arrangement='horizontal', count=2, h_spacing=12)))
    res, rep = _run([_conv(), arr], sections=_door_stack(), openings=OPS)
    assert all(r.valid for r in rep), [r.errors for r in rep]
    clash = _prof(pos=200, zb=66, width=18)                            # in the closure: refused
    _, rep = _run([_conv(), clash], sections=_door_stack(), openings=OPS)
    assert rep[0].valid and not rep[1].valid
    pl = PlaneProfile('Q', sources=('L',), slope=0.1, z_anchor=80)
    src = {'A': [{'source': 'L', 'points': [[0, 0], [480, 0]]}], 'B': [{'source': 'L', 'points': [[0, 0], [480, 0]]}]}
    _, rep = _run([_conv(), pl], sections=_door_stack(), openings=OPS, sources=src)
    assert any('planar top cut Q' in w for w in rep[0].warnings)
