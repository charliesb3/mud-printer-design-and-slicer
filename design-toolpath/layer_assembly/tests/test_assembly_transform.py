"""
ASSEMBLY-WIDE transform: one progression over the whole stack (global
layer g of L), composed with section transforms (model.py doc). Pure.
"""
import math

import pytest

from layer_assembly.model import (Assembly, Section, SectionTransform, Footprint, resolve,
                                  reference_footprint, AssemblyError)
from layer_assembly.preview import build_stack
from layer_assembly.source import StaticSource, DesignInfo, LayerGeometry

FP = {'A': Footprint.of_bbox(50, 50, 150, 150), 'B': Footprint.of_bbox(50, 50, 150, 150)}


def T(profile='linear', dx=0.0, dy=0.0, s=0.0):
    return SectionTransform(profile, (dx, dy), s)


def stack(*secs, lh=1.5, tf=None):
    return Assembly(lh, list(secs), '', tf or T())


def tfs(r):
    return [i.transform for i in r.instances]


def test_zero_assembly_transform_is_exactly_the_section_behaviour():
    secs = [Section('A', 12, '', T(dx=0.25, s=0.5)), Section('B', 12), Section('A', 12, '', T('quadratic', dy=-0.5))]
    a = resolve(stack(*secs), footprints=FP)
    b = resolve(Assembly(1.5, secs), footprints=FP)
    assert tfs(a) == tfs(b)


def test_linear_assembly_shift_runs_through_sections_and_designs():
    r = resolve(stack(Section('A', 12), Section('B', 12), Section('A', 12), tf=T(dx=0.25, dy=-0.125)))
    for g, t in enumerate(tfs(r)):                      # 24 layers, never restarting
        assert t.scale == 1 and math.isclose(t.tx, 0.25 * g) and math.isclose(t.ty, -0.125 * g)


def test_quadratic_assembly_shift_is_normalised_to_the_whole_stack():
    r = resolve(stack(Section('A', 15), Section('B', 15), tf=T('quadratic', dx=0.5)))   # 20 layers
    tx = [t.tx for t in tfs(r)]
    for g in range(20):
        assert math.isclose(tx[g], 0.5 * g * (g + 1) / 40)
    steps = [b - a for a, b in zip(tx, tx[1:])]
    assert all(b > a for a, b in zip(steps, steps[1:]))                   # no restart at the design change
    assert math.isclose(steps[-1], 0.5 * 19 / 20)


def test_assembly_scale_about_the_reference_footprint():
    r = resolve(stack(Section('A', 12), Section('B', 12), tf=T(s=0.5)), footprints=FP)
    for g, t in enumerate(tfs(r)):
        assert math.isclose(t.apply(150, 100)[0], 150 - 0.5 * g, abs_tol=1e-9)
        assert math.isclose(t.apply(100, 100)[0], 100, abs_tol=1e-9)
    with pytest.raises(AssemblyError):
        resolve(stack(Section('A', 12), tf=T(s=0.5)))                       # needs footprints


def test_reference_footprint_is_the_union():
    F = reference_footprint(['A', 'C'], {'A': Footprint.of_bbox(0, 0, 10, 10), 'C': Footprint.of_bbox(20, 0, 30, 4)})
    # a Footprint keeps centre + half of its LONGER side: the union is that of squares
    assert (F.cx, F.cy, F.half) == (15, 3.5, 15)


def test_composition_shifts_stay_physical_and_scales_multiply():
    """Section shift + assembly scale: the section's shift is NOT shrunk by
    the assembly scale, and the assembly shift is not shrunk by the
    section scale."""
    a = stack(Section('A', 15, '', T(dx=1.0, s=0.5)), tf=T(dx=0.25, s=0.25))
    r = resolve(a, footprints=FP)
    for g, t in enumerate(tfs(r)):
        fa = 1 - 0.25 * g / 50
        fs = 1 - 0.5 * g / 50
        assert math.isclose(t.scale, fa * fs)
        cx, _ = t.apply(100, 100)                     # the reference centre moves by BOTH shifts, unscaled
        assert math.isclose(cx, 100 + 0.25 * g + 1.0 * g, abs_tol=1e-9)


def test_composition_is_continuous_across_section_boundaries():
    a = stack(Section('A', 12, '', T(dx=0.5, s=0.25)), Section('B', 12),
              Section('A', 12, '', T('quadratic', dy=0.5)), tf=T('quadratic', dx=-0.25, s=0.125))
    r = resolve(a, footprints=FP)
    pts = [t.apply(150, 150) for t in tfs(r)]
    jumps = [math.dist(p, q) for p, q in zip(pts, pts[1:])]
    assert max(jumps) < 1.0                            # every step small: no jump anywhere


def test_layer_height_change_renormalises_the_assembly_progression():
    a = [Section('A', 30)]
    r1 = resolve(stack(*a, tf=T('quadratic', dx=0.25)))
    r2 = resolve(stack(*a, lh=3.0, tf=T('quadratic', dx=0.25)))
    assert len(r1.instances) == 20 and len(r2.instances) == 10
    assert math.isclose(tfs(r2)[-1].tx - tfs(r2)[-2].tx, 0.25 * 9 / 10)


def test_assembly_transform_round_trip_and_preview_uses_resolved_transforms():
    a = stack(Section('A', 12), tf=T('quadratic', 0.25, 0.5, 0.0))
    assert Assembly.from_dict(a.to_dict()).to_dict() == a.to_dict()
    sq = {'id': 's', 'pts': [[50, 50], [150, 50], [150, 150], [50, 150]], 'closed': True}
    src = StaticSource({'A': (DesignInfo('A', 'A'), LayerGeometry('A', [sq]))})
    r = resolve(a)
    st = build_stack(r, src)
    for L, inst in zip(st, r.instances):
        assert L.transform is inst.transform
        assert L.placed()[0][0] == inst.transform.apply(50, 50)
