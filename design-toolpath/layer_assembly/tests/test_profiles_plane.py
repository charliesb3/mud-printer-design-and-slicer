"""
PLANAR TOP CUT — the pure model (layer_assembly/profiles.py PlaneProfile):
one plane z = z_anchor + slope · d·(p − anchor) in WORLD plan coordinates;
per layer one half-plane (the line at the layer's mid-height), mapped into
the layer's design coordinates through its own placement; selection by
sources / Wall Systems − exclude. No Designer.
"""
import json
import math

import pytest

from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, resolve
from layer_assembly.profiles import (PlaneProfile, plane_from_high_low, apply_profiles, profile_from_dict,
                                     cuts_to_dicts, cuts_from_dicts, plane_selection, ProfileError)

BEAD, H = 3.0, 1.5
SRC = {'A': [{'source': 'C', 'label': 'Circle', 'closed': True, 'system': 'WS1', 'system_label': 'Outer',
              'points': [[60 * math.cos(a / 16 * math.pi), 60 * math.sin(a / 16 * math.pi)] for a in range(32)]},
             {'source': 'L', 'label': 'Line', 'closed': False, 'system': None, 'system_label': None,
              'points': [[100, -50], [100, 50]]},
             {'source': 'U', 'label': 'Unselected', 'closed': False, 'system': None, 'system_label': None,
              'points': [[-100, -50], [-100, 50]]}]}


def _run(p, height=96, sections=None, fps=None):
    a = Assembly(1.5, sections or [Section('A', height)], profiles=[p])
    return apply_profiles(resolve(a, ['A'], fps), [p], {'A': {}}, BEAD, H, SRC)


def test_plane_math_and_its_plan_line():
    p = PlaneProfile('P', sources=('C',), direction=90.0, slope=0.5, anchor=(0.0, 0.0), z_anchor=30.0)
    assert p.z_at(0, 0) == 30 and p.z_at(0, 10) == pytest.approx(35) and p.z_at(10, 0) == pytest.approx(30)
    (pt, n) = p.line_at(40.0)
    assert pt == pytest.approx((0.0, 20.0)) and n == pytest.approx((0.0, 1.0))   # kept side = uphill (+y)
    level = PlaneProfile('P', sources=('C',), slope=0.0, z_anchor=30.0)
    assert level.line_at(29.0) == 'all' and level.line_at(31.0) == 'none'


def test_high_low_and_slope_are_one_plane():
    p = plane_from_high_low('P', 0.0, (-60.0, 100.0, (0.0, 0.0)), z_high=80.0, z_low=40.0, sources=('C', 'L'))
    assert p.z_at(-60, 0) == pytest.approx(40) and p.z_at(100, 0) == pytest.approx(80)
    assert p.slope == pytest.approx(40 / 160) and math.degrees(math.atan(p.slope)) == pytest.approx(14.036, abs=1e-3)
    f = p.flipped()                                  # FLIP SLOPE: same magnitude, uphill reversed
    assert f.slope == p.slope and f.direction == 180.0 and f.z_at(*p.anchor) == pytest.approx(p.z_anchor)
    assert f.z_at(100, 0) < f.z_at(-60, 0)


def test_selection_systems_sources_exclude():
    p = PlaneProfile('P', sources=('L',), systems=('WS1',), exclude=())
    assert [r['source'] for r in plane_selection(p, SRC['A'])] == ['C', 'L']
    q = PlaneProfile('P', sources=('L',), systems=('WS1',), exclude=('C',))
    assert [r['source'] for r in plane_selection(q, SRC['A'])] == ['L']


def test_per_layer_half_planes_layers_below_untouched_above_removed():
    p = PlaneProfile('P', sources=('C', 'L'), direction=0.0, slope=0.25, anchor=(-60.0, 0.0), z_anchor=30.0)
    res, rep = _run(p)
    r = rep[0]
    assert r.valid
    cut = {L['layer']: L for L in r.layers}
    # z(min over the footprint) = 30 at x = −60: layers with mid ≤ 30 untouched
    assert min(cut) == 20 and not any(c[0] == 'plane' for c in res.instances[19].cuts)   # (stations only)
    for k, L in cut.items():
        z = 1.5 * k + 0.75
        ops = [c for c in res.instances[k].cuts if c[0] == 'plane']
        assert len(ops) == 1                            # ONE op for the whole selection
        op = json.loads(ops[0][1])
        if z > p.z_at(100, 0):                          # above the plane everywhere: all removed
            assert op['remove_all']
        else:
            assert op['normal'] == [1.0, 0.0] and op['point'][0] == pytest.approx(-60 + (z - 30) / 0.25, abs=1e-4)
    assert all(not any(c[0] == 'plane' for c in i.cuts) for i in res.instances[:20])


def test_a_steep_cut_is_never_rejected():
    p = PlaneProfile('P', sources=('C', 'L'), direction=0.0, slope=20.0, anchor=(-60.0, 0.0), z_anchor=10.0)
    _, rep = _run(p)
    assert rep[0].valid and not rep[0].errors


def test_level_plane_is_valid():
    p = PlaneProfile('P', sources=('C',), slope=0.0, z_anchor=30.0)
    res, rep = _run(p)
    assert rep[0].valid and all(L['removed_all'] for L in rep[0].layers) and min(L['layer'] for L in rep[0].layers) == 20


def test_world_plane_follows_the_layer_placement():
    """The plane is in WORLD coordinates: a section shift moves the layers,
    the cut line in DESIGN coordinates moves the other way."""
    p = PlaneProfile('P', sources=('C', 'L'), direction=0.0, slope=0.25, anchor=(-60.0, 0.0), z_anchor=30.0)
    fps = {'A': Footprint(20, 0, 160)}
    shifted = [Section('A', 96, '', SectionTransform('linear', (0.5, 0.0), 0.0))]
    res, rep = _run(p, sections=shifted, fps=fps)
    for L in rep[0].layers[:5]:
        inst = res.instances[L['layer']]
        op = json.loads(next(c for c in inst.cuts if c[0] == 'plane')[1])
        x_world = -60 + (L['z'] - 30) / 0.25
        assert op['point'][0] == pytest.approx((x_world - inst.transform.tx) / inst.transform.scale, abs=1e-4)


def test_plane_round_trips_and_cut_spec_round_trips():
    p = PlaneProfile('P', sources=('L',), systems=('WS1',), exclude=('X',), direction=33.0, slope=0.3,
                     anchor=(1.5, -2.0), z_anchor=44.0, label='roof')
    a = Assembly(1.5, [Section('A', 96)], profiles=[p])
    b = Assembly.from_dict(json.loads(json.dumps(a.to_dict())))
    assert b.profiles == [p]
    res, _ = apply_profiles(resolve(a, ['A']), [p], {'A': {}}, BEAD, H, SRC)
    c = next(i.cuts for i in res.instances if any(x[0] == 'plane' for x in i.cuts))
    assert cuts_from_dicts(cuts_to_dicts(c)) == c
    with pytest.raises(ProfileError):
        profile_from_dict(dict(p.to_dict(), slope=-1))


def test_no_selection_is_reported():
    _, rep = _run(PlaneProfile('P', slope=0.2))
    assert not rep[0].valid and 'no walls selected' in rep[0].errors[0]
