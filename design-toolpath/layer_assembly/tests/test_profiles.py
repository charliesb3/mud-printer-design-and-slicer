"""
ASSEMBLY PROFILES — the pure model (layer_assembly/profiles.py): shapes,
per-layer resolution, printability validation, serialisation. No Designer.
"""
import json
import math

import pytest

from layer_assembly.model import Assembly, Section, SectionTransform, resolve
from layer_assembly.profiles import (OpeningProfile, ExtentProfile, WallRef, WallTarget, taper,
                                     apply_profiles, profile_from_dict, cuts_to_dicts,
                                     cuts_from_dicts, ProfileError)

BEAD, H = 3.0, 1.5
WALL = WallTarget('L', None, (0.0, 0.0), (120.0, 0.0), 120.0, 10.0, True, 'Wall')
TG = {'A': {'L': WALL}}


def _run(profiles, height=72, lh=1.5, targets=TG, h=H, sections=None):
    a = Assembly(lh, sections or [Section('A', height)], profiles=profiles, max_overhang=h)
    return apply_profiles(resolve(a, ['A', 'B']), profiles, targets, BEAD, h)


def _widths(res, pid='P'):
    return {k: w for k, w in ((L['layer'], L['width']) for r in res for L in r.layers if r.id == pid)}


# ---- shapes -------------------------------------------------------------------------

def test_diamond_width_bottom_middle_top():
    p = OpeningProfile('P', WallRef('L'), 60, 12, 48, 24, 'diamond')
    assert p.width_at(12) == 0 and p.width_at(60) == 0          # pointed bottom and top
    assert p.width_at(36) == pytest.approx(24)                   # widest at mid-height
    assert p.width_at(24) == pytest.approx(12) and p.width_at(48) == pytest.approx(12)
    res, rep = _run([p])
    w = _widths(rep)
    # layer k spans [1.5k, 1.5k + 1.5]; its width is taken at its mid-height
    assert min(w) == 8 and max(w) == 39                         # z 12–60 → layers 8 … 39
    assert w[8] == pytest.approx(0.75) and w[39] == pytest.approx(0.75)        # bottom / top layers
    assert w[23] == pytest.approx(23.25) and w[24] == pytest.approx(23.25)     # middle layers
    assert all(w[k + 1] - w[k] == pytest.approx(1.5) for k in range(8, 23))    # linear opening
    assert all(w[k] - w[k + 1] == pytest.approx(1.5) for k in range(24, 39))   # linear closing
    assert rep[0].valid and rep[0].worst_step == pytest.approx(0.75)
    cut = [i for i in res.instances if any(c[0] == 'opening' for c in i.cuts)]
    assert len(cut) == 32 and all(i.cuts[0][:3] == ('opening', 'L', None) for i in cut)
    # a LATTICE STATION (no cut) on every layer of the design: one lattice reference for the stack
    assert all(('station', 'L', None, 60.0, 0.0, 'P') in i.cuts for i in res.instances)


def test_a_legacy_lens_resolves_as_a_curved_diamond():
    """Lens retired 2026-10-10: a saved Lens is the CURVED Diamond (a vesica:
    two arcs, sharp top and bottom) — the closest current shape (both were
    curved, pointed top and bottom); the UI re-constrains it on Open."""
    p = OpeningProfile('P', WallRef('L'), 60, 6, 54, 18, 'lens')
    assert (p.upper, p.lower, p.middle, p.upper_height, p.bottom_depth) == ('curve', 'arc', False, 27, 27)
    _, rep = _run([p])
    ws = [L['width'] for L in rep[0].layers]
    k = ws.index(max(ws))
    assert ws[:k + 1] == sorted(ws[:k + 1]) and ws[k:] == sorted(ws[k:], reverse=True)
    assert rep[0].valid and ws[0] < 3.0 and ws[-1] < 3.0                         # gradual first / last cut


def test_a_curved_closure_needs_more_height_than_an_angular_one():
    d = OpeningProfile('P', WallRef('L'), 60, 6, 40, 30, 'diamond')
    l = OpeningProfile('P', WallRef('L'), 60, 6, 40, 30, 'diamond', upper='curve')
    assert _run([d])[1][0].worst_step < _run([l])[1][0].worst_step


# ---- printability ------------------------------------------------------------------------

def test_too_steep_a_diamond_is_reported_never_resized():
    """2026-10-10: the parameters ARE the shape. A Diamond that narrows faster
    than Maximum overhang allows is NOT generated (no longer silently
    extended — that made the printed opening differ from the drawn one);
    the hint gives the printable height at this width / width at this
    height (what the UI's constraints apply while editing)."""
    p = OpeningProfile('P', WallRef('L'), 60, 6, 30, 36, 'diamond')   # 1.8 in a side per layer drawn
    res, rep = _run([p])
    r = rep[0]
    assert not r.valid and 'not self-supporting' in r.errors[0]
    assert not any(c[0] == 'opening' for i in res.instances for c in i.cuts)
    # (hints: the UPPER height at this width / the width at this upper height)
    assert r.hint['field'] == 'upper_height' and r.hint['min_height'] == 18 and r.hint['max_width'] == 30
    for H_, W_ in ((2 * r.hint['min_height'], 36), (30, r.hint['max_width'])):
        ok = _run([OpeningProfile('P', WallRef('L'), 60, 6, H_, W_, 'diamond')])[1][0]
        assert ok.valid and ok.worst_step <= H + 1e-9


def test_validity_follows_maximum_overhang_and_layer_height():
    p = OpeningProfile('P', WallRef('L'), 60, 6, 30, 30, 'diamond')    # step = W·lh/H per side
    assert _run([p], h=1.5)[1][0].valid                                 # 1.5 in a side: as drawn
    for kw in ({'h': 1.0}, {'lh': 2.0, 'h': 1.5}, {'h': 0.25}):
        r = _run([p], **kw)[1][0]
        assert not r.valid and r.hint['min_height'] > 15 and r.hint['max_width'] < 30, kw


def test_opening_must_fit_the_wall():
    p = OpeningProfile('P', WallRef('L'), 10, 6, 48, 24, 'diamond')
    r = _run([p])[1][0]
    assert not r.valid and 'does not fit' in r.errors[0]


def test_retreating_taper_is_never_an_overhang_but_a_growing_one_is():
    fast = taper('T', WallRef('L'), 'start', 120, 0, 0, 6)            # 120 in → 0 in 4 layers
    res, rep = _run([fast])
    assert rep[0].valid and rep[0].worst_step == 0
    grow = taper('T', WallRef('L'), 'start', 0, 0, 120, 6)
    res, rep = _run([grow])
    assert not rep[0].valid and 'grows' in rep[0].errors[0] and not any(i.cuts for i in res.instances)


def test_taper_to_zero():
    p = taper('T', WallRef('L'), 'start', 60, 0, 0, 72)
    res, rep = _run([p], height=90)
    r = {L['layer']: L['retained'] for L in rep[0].layers}
    assert r[0] == pytest.approx(60 - 60 * 0.75 / 72)
    assert r[24] == pytest.approx(60 - 60 * 36.75 / 72)
    zero = [k for k, v in r.items() if v == 0]
    assert zero and min(zero) == 46                     # under one bead → absent; it stays absent
    assert all(r[k] == 0 for k in range(46, 60)) and len(r) == 60
    assert res.instances[59].cuts == (('extent', 'L', None, 'end', 0.0, 'T'),)


def test_taper_to_nonzero_and_above_the_upper_z_holds():
    p = taper('T', WallRef('L'), 'end', 60, 0, 100, 72)        # keep the END: 60 → 20 in retained
    res, rep = _run([p], height=90)
    r = {L['layer']: L['retained'] for L in rep[0].layers}
    assert r[47] == pytest.approx(60 - 40 * 71.25 / 72, abs=1e-4)
    assert all(r[k] == 20 for k in range(48, 60))
    assert res.instances[10].cuts[0][3] == 'start'


def test_taper_spanning_section_boundaries_and_below_its_lower_z():
    p = taper('T', WallRef('L'), 'start', 100, 24, 40, 60)
    secs = [Section('A', 36), Section('B', 36)]
    tg = {'A': {'L': WALL}, 'B': {'L': WALL}}
    res, rep = _run([p], sections=secs, targets=tg)
    r = {L['layer']: (L['retained'], L['design']) for L in rep[0].layers}
    assert min(r) == 16                                  # below z 24: unaffected (no cut)
    assert not res.instances[15].cuts and res.instances[16].cuts
    assert r[23][1] == 'A' and r[24][1] == 'B'           # one continuous profile across the boundary
    assert r[24][0] - r[23][0] == pytest.approx(-60 * 1.5 / 36)
    assert r[47][0] == 40


def test_minimum_printable_length_of_the_construction():
    gen = WallTarget('L', None, (0, 0), (60, 0), 60.0, 10.0, True, '', min_length=20.0)
    p = taper('T', WallRef('L'), 'start', 60, 0, 0, 72)
    _, rep = _run([p], targets={'A': {'L': gen}})
    r = [L['retained'] for L in rep[0].layers]
    assert all(v == 0 or v >= 20 for v in r)             # a generated wall never prints as a stub


def test_wall_absent_from_a_design_is_reported_not_lost():
    p = OpeningProfile('P', WallRef('L'), 60, 0, 72, 24, 'diamond')
    res, rep = _run([p], sections=[Section('A', 36), Section('B', 36)], targets=TG)
    assert rep[0].valid and any('absent from design(s) B' in w for w in rep[0].warnings)
    assert all(not i.cuts for i in res.instances if i.design_id == 'B')
    _, rep = _run([OpeningProfile('P', WallRef('X'), 60, 0, 72, 24)])
    assert not rep[0].valid and 'not a straight wall' in rep[0].errors[0]


def test_only_open_straight_walls_taper():
    edge = WallTarget('R', 0, (0, 0), (120, 0), 120.0, 10.0, False)
    _, rep = _run([taper('T', WallRef('R', 0), 'start', 120, 0, 0, 72)], targets={'A': {'R#0': edge}})
    assert not rep[0].valid and 'OPEN wall' in rep[0].errors[0]


# ---- isolation / coexistence -----------------------------------------------------------------

def test_no_profiles_resolves_exactly_as_before():
    a = Assembly(1.5, [Section('A', 36, '', SectionTransform('linear', (0.25, 0), 0.5))])
    from layer_assembly.model import Footprint
    fps = {'A': Footprint(60, 0, 60)}
    r0 = resolve(a, ['A'], fps)
    r1, rep = apply_profiles(resolve(a, ['A'], fps), [], TG, BEAD, H)
    assert rep == [] and r1.instances == r0.instances
    assert [i.geometry_key for i in r1.instances] == ['A'] * 24
    assert all(not i.is_variant for i in r1.instances)


def test_profiles_never_change_placements():
    tf = SectionTransform('linear', (0.25, -0.125), 0.5)
    from layer_assembly.model import Footprint
    fps = {'A': Footprint(60, 0, 60)}
    a0 = Assembly(1.5, [Section('A', 72, '', tf)])
    p = OpeningProfile('P', WallRef('L'), 60, 12, 48, 24)
    a1 = Assembly(1.5, [Section('A', 72, '', tf)], profiles=[p])
    r0 = resolve(a0, ['A'], fps)
    r1, _ = apply_profiles(resolve(a1, ['A'], fps), a1.profiles, TG, BEAD, H)
    assert [i.placement for i in r0.instances] == [i.placement for i in r1.instances]
    assert any(i.cuts for i in r1.instances)
    k = next(i for i in r1.instances if i.cuts)
    assert k.geometry_key.startswith('A|cut:') and k.is_variant


def test_cut_and_semantic_variant_key():
    from dataclasses import replace
    p = OpeningProfile('P', WallRef('L'), 60, 12, 48, 24)
    res, _ = apply_profiles(resolve(Assembly(1.5, [Section('A', 72)]), ['A']), [p], TG, BEAD, H)
    i = replace(res.instances[20], semantic=(('L', 0.98, 1.0, 0.0),))
    assert i.geometry_key.startswith('A|L:0.98,1,0|cut:') and i.transforms() == {'L': (0.98, 1.0, 0.0)}


# ---- persistence ----------------------------------------------------------------------------------

def test_profiles_round_trip_through_json():
    ps = [OpeningProfile('P1', WallRef('R', 2), 40.5, 6, 54, 20, 'lens', 'window'),
          taper('T1', WallRef('L'), 'end', 40, 12, 100, 60, 'buttress'),
          ExtentProfile('S1', WallRef('L'), 'start', ((0, 60), (24, 40), (48, 20)), ('step', 'step'))]
    a = Assembly(1.5, [Section('A', 72)], profiles=ps)
    b = Assembly.from_dict(json.loads(json.dumps(a.to_dict())))
    assert b.profiles == ps and b.to_dict() == a.to_dict()
    assert Assembly.from_dict({'layer_height': 1.5, 'sections': []}).profiles == []   # older states


def test_stepped_extent_representation():
    s = ExtentProfile('S', WallRef('L'), 'start', ((0, 60), (24, 40), (48, 20)), ('step', 'linear'))
    assert s.cut_at(-1) is None and s.cut_at(10) == 60 and s.cut_at(30) == 40 - 20 * 6 / 24
    assert s.cut_at(100) == 20 and s.retained_at(100, 120) == 20
    e = ExtentProfile('S', WallRef('L'), 'end', s.knots, s.interp)
    assert e.retained_at(100, 120) == 100 and e.end == 'start' and s.end == 'end'


def test_cut_spec_round_trip_and_bad_input():
    p = OpeningProfile('P', WallRef('R', 1), 60, 12, 48, 24)
    res, _ = apply_profiles(resolve(Assembly(1.5, [Section('A', 72)]), ['A']), [p],
                            {'A': {'R#1': WallTarget('R', 1, (0, 0), (0, 120), 120.0, 10.0)}}, BEAD, H)
    c = res.instances[20].cuts
    assert cuts_from_dicts(cuts_to_dicts(c)) == c and cuts_to_dicts(c)[0]['edge'] == 1
    with pytest.raises(ProfileError):
        profile_from_dict({'kind': 'opening', 'id': 'x', 'wall': {'source': 'L'}, 'position': 1,
                           'height': 10, 'width': 5, 'upper': 'circle', 'lower': 'flat', 'middle': True,
                           'middle_height': 10})
    with pytest.raises(ProfileError):
        profile_from_dict({'kind': 'boolean', 'id': 'x'})


# ---- the cut-profile taper (revised after manual review) ----------------------------------

def test_taper_is_a_cut_between_two_arbitrary_elevation_points():
    """The cut endpoints are authoritative: a short LOCAL buttress taper —
    only the outer 20 in of a 120 in wall, between z 24 and z 60 — never
    involves the rest of the wall."""
    p = taper('T', WallRef('L'), 'start', 120, 24, 100, 60)
    res, rep = _run([p], height=72)
    assert rep[0].valid
    r = {L['layer']: L for L in rep[0].layers}
    assert min(r) == 16 and not res.instances[15].cuts           # below the lower point: unaffected
    for k, L in r.items():
        z = 1.5 * k + 0.75
        cut = 120 - 20 * min(1.0, (z - 24) / 36)
        assert L['cut'] == pytest.approx(cut, abs=1e-4) and L['retained'] == pytest.approx(cut, abs=1e-4)
        assert L['retained'] >= 100 - 1e-9                       # never more than the outer 20 in
    assert all(r[k]['retained'] == 100 for k in range(40, 48))    # above the upper point: holds
    assert res.instances[30].cuts[0][:4] == ('extent', 'L', None, 'end')


def test_taper_keeps_the_chosen_side():
    """keep 'end': the START side of the cut line disappears (a buttress
    whose free end is the wall's start point)."""
    p = taper('T', WallRef('L'), 'end', 0, 0, 30, 72)              # cut moves 0 → 30 from the start
    res, rep = _run([p], height=72)
    assert rep[0].valid
    L = rep[0].layers[23]
    assert L['cut'] == pytest.approx(30 * 35.25 / 72, abs=1e-4) and L['retained'] == pytest.approx(120 - L['cut'], abs=1e-4)
    assert res.instances[23].cuts[0][3] == 'start'                # the start retreats


def test_triangular_buttress_is_two_points():
    p = taper('T', WallRef('L'), 'start', 120, 0, 0, 72)           # free end at the bottom → nothing at the top
    _, rep = _run([p], height=72)
    rs = [L['retained'] for L in rep[0].layers]
    assert rep[0].valid and rs == sorted(rs, reverse=True) and rs[0] > 118 and rs[-1] == 0


def test_cut_points_beyond_the_wall_are_clamped_and_reported():
    p = taper('T', WallRef('L'), 'start', 150, 0, 60, 72)
    _, rep = _run([p], height=72)
    assert rep[0].valid and any('beyond the wall' in w for w in rep[0].warnings)
    assert max(L['retained'] for L in rep[0].layers) == 120


def test_taper_coexists_with_an_assembly_transform():
    """Profiles resolve in DESIGN coordinates before the placement: a
    section scale / shift changes placements only, never the cut."""
    from layer_assembly.model import Footprint
    tf = SectionTransform('linear', (0.25, 0), 0.5)
    p = taper('T', WallRef('L'), 'start', 120, 0, 60, 72)
    fps = {'A': Footprint(60, 0, 60)}
    plain = apply_profiles(resolve(Assembly(1.5, [Section('A', 72)], profiles=[p]), ['A'], fps), [p], TG, BEAD, H)
    moved = apply_profiles(resolve(Assembly(1.5, [Section('A', 72, '', tf)], profiles=[p]), ['A'], fps), [p], TG, BEAD, H)
    assert [i.cuts for i in plain[0].instances] == [i.cuts for i in moved[0].instances]
    assert [i.placement for i in plain[0].instances] != [i.placement for i in moved[0].instances]


def test_revised_taper_round_trips_and_reads_older_dicts():
    p = taper('T', WallRef('L'), 'end', 10, 6, 40, 66, 'buttress')
    d = json.loads(json.dumps(p.to_dict()))
    assert d['keep'] == 'end' and d['knots'] == [[6.0, 10.0], [66.0, 40.0]] and profile_from_dict(d) == p
    old = {'kind': 'extent', 'id': 'O', 'wall': {'source': 'L'}, 'end': 'end', 'knots': [[0, 60], [72, 0]], 'interp': ['linear']}
    assert profile_from_dict(old) == taper('O', WallRef('L'), 'start', 60, 0, 0, 72)
    with pytest.raises(ProfileError):
        profile_from_dict(dict(old, end='start'))                  # (its numbers were retained lengths)
    with pytest.raises(ProfileError):
        profile_from_dict({'kind': 'extent', 'id': 'X', 'wall': {'source': 'L'}, 'keep': 'start',
                           'knots': [[6, 10], [6, 20]]})


# ---- subtractive cut: both wall directions, both Keep sides (2026-10-08 review 2) ----------

REV = WallTarget('L', None, (120.0, 0.0), (0.0, 0.0), 120.0, 10.0, True, 'Wall (drawn the other way)')


def _kept_world(rep, t, keep):
    """Per layer: the kept stretch of the wall in WORLD x (sorted ends)."""
    out = {}
    for L in rep[0].layers:
        if L['retained'] == 0:
            out[L['layer']] = None
            continue
        s0, s1 = (0.0, L['cut']) if keep == 'start' else (L['cut'], t.length)
        xs = sorted(t.a[0] + (t.b[0] - t.a[0]) * s / t.length for s in (s0, s1))
        out[L['layer']] = (round(xs[0], 6), round(xs[1], 6))
    return out


@pytest.mark.parametrize('target', [WALL, REV], ids=['drawn_0_to_120', 'drawn_120_to_0'])
@pytest.mark.parametrize('keep', ['start', 'end'])
def test_triangular_buttress_works_for_either_kept_end(target, keep):
    """The kept end at the bottom-wide side, the cut rising toward it: a
    retreating result — valid for both Keep sides on both wall directions."""
    free = target.length if keep == 'start' else 0.0
    p = taper('T', WallRef('L'), keep, free, 0, target.length - free, 72)
    _, rep = _run([p], targets={'A': {'L': target}})
    assert rep[0].valid, rep[0].errors
    rs = [L['retained'] for L in rep[0].layers]
    assert rs == sorted(rs, reverse=True) and rs[-1] == 0


@pytest.mark.parametrize('target', [WALL, REV], ids=['drawn_0_to_120', 'drawn_120_to_0'])
def test_complementary_side_of_the_same_cut_is_a_real_overhang(target):
    """One cut line, two sides: keeping the side that grows outward as it
    rises is genuinely unsupported — rejected, with a physical message."""
    p = taper('T', WallRef('L'), 'end', 120, 0, 0, 72)              # line (120,0)→(0,72), keep the END side
    res, rep = _run([p], targets={'A': {'L': target}})
    assert not rep[0].valid and 'overhangs the layer below' in rep[0].errors[0] and 'mirror' in rep[0].errors[0]
    assert not any(c[0] == 'extent' for i in res.instances for c in i.cuts)


def test_mirrored_cut_on_the_reversed_wall_is_the_same_physical_result():
    p = taper('T', WallRef('L'), 'start', 120, 6, 40, 66)
    q = p.mirrored(120.0)
    assert q.keep == 'end' and q.knots == ((6.0, 0.0), (66.0, 80.0)) and q.mirrored(120.0) == p
    _, rp = _run([p], targets={'A': {'L': WALL}})
    _, rq = _run([q], targets={'A': {'L': REV}})
    assert rp[0].valid and rq[0].valid
    assert _kept_world(rp, WALL, 'start') == _kept_world(rq, REV, 'end')


def test_true_retreat_vs_unsupported_growth():
    # an outward LEAN within the maximum overhang per layer (a corbel) is printable …
    lean = taper('T', WallRef('L'), 'start', 60, 0, 120, 96)        # +0.94 in per 1.5 in layer
    _, rep = _run([lean], height=96)
    assert rep[0].valid and 0 < rep[0].worst_step <= H
    # … a steeper one is not, on either wall direction / Keep side
    for t, p in ((WALL, taper('T', WallRef('L'), 'start', 30, 0, 120, 36)),
                 (REV, taper('T', WallRef('L'), 'start', 30, 0, 120, 36)),
                 (WALL, taper('T', WallRef('L'), 'start', 30, 0, 120, 36).mirrored(120.0))):
        _, rep = _run([p], targets={'A': {'L': t}})
        assert not rep[0].valid and rep[0].worst_step > H
    # retreat is never an overhang, however fast
    _, rep = _run([taper('T', WallRef('L'), 'end', 0, 0, 120, 3)])
    assert rep[0].valid and rep[0].worst_step == 0


# ---- discrete Diamond closure (2026-10-08 review 3) -----------------------------------------

def _transitions(rep, n_layers):
    """Per-side steps of every consecutive layer pair through the whole
    opening — from the closed layer below it to the closed layer above."""
    w = {L['layer']: L['width'] for L in rep[0].layers}
    seq = [w.get(k, 0.0) for k in range(n_layers)]
    return [abs(b - a) / 2 for a, b in zip(seq, seq[1:])], seq


@pytest.mark.parametrize('zb,height,width', [(12, 48, 24), (12, 60, 24), (12.4, 47.3, 18), (6, 72, 12),
                                             (9, 40, 30), (3.7, 25.1, 10)])
def test_every_diamond_transition_obeys_the_overhang_including_tip_and_closure(zb, height, width):
    p = OpeningProfile('P', WallRef('L'), 60, zb, height, width, 'diamond')
    res, rep = _run([p], height=96)
    assert rep[0].valid, rep[0].errors
    steps, seq = _transitions(rep, len(res.instances))
    opened = [k for k, v in enumerate(seq) if v > 0]
    first, last = opened[0], opened[-1]
    flank = width / 2 * 1.5 / (height / 2)                # per-side step of the flanks per layer
    first_step, closing_step = seq[first] / 2, seq[last] / 2
    assert max(steps) <= H + 1e-9                          # every transition, tip and closure included
    assert first_step <= max(flank, 0.375) + 1e-9 and closing_step <= max(flank, 0.375) + 1e-9, \
        (first_step, closing_step, flank)                  # no larger jump at the pointed ends
    assert max(steps) <= max(flank, 0.375) + 1e-3          # the flanks themselves are even (widths rounded to 1e-4)
    # as pointed as printable: the tip layers are the narrowest the shape gives (≥ the smallest cut)
    assert seq[first] == pytest.approx(max(0.75, p.width_at(1.5 * first + 0.75)), abs=1e-4)
    assert seq[last] == pytest.approx(max(0.75, p.width_at(1.5 * last + 0.75)), abs=1e-4)


def test_tip_sliver_is_widened_not_dropped():
    """W 24 over H 60 at 1.5 in layers: the tip layer's mid-height width is
    0.6 in (< the smallest cut, 0.75). Dropping it made the next layer
    (1.8 in) the first: a 0.9 in first / closing step against 0.6 in flanks.
    Widened to 0.75: 0.375 then 0.525."""
    p = OpeningProfile('P', WallRef('L'), 60, 12, 60, 24, 'diamond')
    res, rep = _run([p], height=96)
    steps, seq = _transitions(rep, len(res.instances))
    opened = [v for v in seq if v > 0]
    assert opened[0] == 0.75 and opened[-1] == 0.75 and opened[1] == pytest.approx(1.8)
    assert max(steps) == pytest.approx(0.6)               # = the flank step; nothing larger at the tips


# ---- AUTO keep: the supported side of any diagonal (2026-10-08 review 3) ----------------------

@pytest.mark.parametrize('target', [WALL, REV], ids=['drawn_0_to_120', 'drawn_120_to_0'])
@pytest.mark.parametrize('diagonal', ['TL_BR', 'TR_BL'])
@pytest.mark.parametrize('keep', ['auto', 'start', 'end'])
def test_both_diagonals_both_wall_directions_both_sides(target, diagonal, keep):
    """Either diagonal, on a wall drawn either way: 'auto' keeps the side
    that retreats; an explicit side is accepted exactly when IT retreats.
    Judged on the kept stretch in WORLD coordinates, never on the wall's
    drawing direction or a preferred diagonal."""
    # the diagonal in WORLD x (elevation left → right = world x increasing)
    xl, xr = 0.0, 120.0
    lo_x, hi_x = (xr, xl) if diagonal == 'TL_BR' else (xl, xr)     # (lower point x, upper point x)
    to_s = lambda x: (x - target.a[0]) / (target.b[0] - target.a[0]) * target.length
    p = taper('T', WallRef('L'), keep, to_s(lo_x), 0, to_s(hi_x), 72)
    _, rep = _run([p], targets={'A': {'L': target}})
    kept_world = _kept_world(rep, target, rep[0].keep) if rep[0].valid else None
    # the supported side keeps the world end the cut moves AWAY from as it rises
    supported_side = 'left' if diagonal == 'TL_BR' else 'right'
    if keep == 'auto':
        assert rep[0].valid and rep[0].worst_step == 0
    else:
        side_of_keep = 'left' if (target.a[0] < target.b[0]) == (keep == 'start') else 'right'
        assert rep[0].valid == (side_of_keep == supported_side), (side_of_keep, supported_side, rep[0].errors)
    if rep[0].valid:
        spans = [v for v in kept_world.values() if v]
        assert all((v[0] == 0.0) if supported_side == 'left' else (v[1] == 120.0) for v in spans)
        lengths = [v[1] - v[0] for v in spans]
        assert lengths == sorted(lengths, reverse=True)                # retreats as it rises


def test_auto_rejects_a_cut_with_no_supported_side():
    """A cut that (being a straight line through the wall) — kept either side
    — overhangs is reported clearly; a vertical cut (constant) is fine."""
    steep_corbel = ExtentProfile('T', WallRef('L'), 'auto', ((0, 60), (6, 60)), ('linear',))
    _, rep = _run([steep_corbel])
    assert rep[0].valid and rep[0].keep in ('start', 'end')         # vertical: both sides stand
    # a cut that zig-zags (30 in back, then 60 in out): whichever side is kept, it grows outward somewhere
    zig = ExtentProfile('T', WallRef('L'), 'auto', ((0, 60), (12, 30), (24, 90)), ('linear', 'linear'))
    res, rep = _run([zig])
    assert not rep[0].valid and 'neither side of this cut line is supported' in rep[0].errors[0]
    assert not any(c[0] == 'extent' for i in res.instances for c in i.cuts)


def test_auto_prefers_the_attached_end_when_both_sides_stand():
    att_end = WallTarget('L', None, (0.0, 0.0), (120.0, 0.0), 120.0, 10.0, True, 'Wall', attached=('end',))
    p = ExtentProfile('T', WallRef('L'), 'auto', ((0, 60), (72, 60)), ('linear',))
    _, rep = _run([p], targets={'A': {'L': att_end}})
    assert rep[0].valid and rep[0].keep == 'end'
