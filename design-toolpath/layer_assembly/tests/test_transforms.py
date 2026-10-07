"""
Per-section layer transforms (Assembly operations; the Layer Design is never
changed): SHIFT (dx, dy per layer), SCALE (outermost edge moves scale_in per
layer, uniform about the footprint centre), linear profile, section-local
progression with cumulative continuity. Pure: no Designer.
"""
import math

import pytest

from layer_assembly.model import (Assembly, Section, SectionTransform, Footprint, PROFILES,
                                  resolve, AssemblyError)
from layer_assembly.preview import build_stack
from layer_assembly.source import StaticSource, DesignInfo, LayerGeometry, bbox_of

FP = {'A': Footprint.of_bbox(50, 50, 150, 150)}        # centre (100, 100), half-size 50


def sec(h, dx=0.0, dy=0.0, s=0.0, d='A', profile='linear'):
    return Section(d, h, '', SectionTransform(profile, (dx, dy), s))


def q(h, dx=0.0, dy=0.0, s=0.0, d='A'):
    return sec(h, dx, dy, s, d, 'quadratic')


def tfs(res):
    return [i.transform for i in res.instances]


def test_zero_transform_is_identity_everywhere():
    r = resolve(Assembly(1.5, [sec(36), sec(12, d='B')]))
    assert all(t.scale == 1 and t.tx == 0 and t.ty == 0 for t in tfs(r))


@pytest.mark.parametrize('dx,dy', [(0.25, 0), (0, 0.25), (-0.5, 0), (0.25, -0.75)])
def test_shift_exact_per_layer_progression(dx, dy):
    r = resolve(Assembly(1.5, [sec(12, dx, dy)]))           # 8 layers
    for k, t in enumerate(tfs(r)):
        assert t.scale == 1                                  # same shape and size
        assert math.isclose(t.tx, k * dx, abs_tol=1e-12) and math.isclose(t.ty, k * dy, abs_tol=1e-12)


def test_sections_with_different_slopes_stay_continuous():
    r = resolve(Assembly(1.5, [sec(12, 0.25), sec(12, 0.5), sec(12, 0.75), sec(12)]))
    tx = [t.tx for t in tfs(r)]
    steps = [round(b - a, 9) for a, b in zip(tx, tx[1:])]
    assert steps == [0.25] * 8 + [0.5] * 8 + [0.75] * 8 + [0.0] * 7   # no jump, no snap back
    assert tx[8] == 2.0 and tx[16] == 6.0 and tx[24] == 12.0 and tx[-1] == 12.0
    # a section without a transform continues straight up from where the stack got to


def test_scale_moves_the_outer_edge_exactly_per_layer_about_the_centre():
    r = resolve(Assembly(1.5, [sec(12, s=0.5)]), footprints=FP)
    for k, t in enumerate(tfs(r)):
        assert math.isclose(t.apply(150, 100)[0], 150 - 0.5 * k, abs_tol=1e-9)    # edge
        assert math.isclose(t.apply(50, 100)[0], 50 + 0.5 * k, abs_tol=1e-9)
        cx, cy = t.apply(100, 100)
        assert math.isclose(cx, 100, abs_tol=1e-9) and math.isclose(cy, 100, abs_tol=1e-9)
    # every relationship preserved: distances all scale by the same factor
    t = tfs(r)[5]
    p, q = (60, 70), (140, 120)
    d0 = math.dist(p, q)
    assert math.isclose(math.dist(t.apply(*p), t.apply(*q)), t.scale * d0, rel_tol=1e-12)


def test_negative_scale_grows():
    r = resolve(Assembly(1.5, [sec(12, s=-0.5)]), footprints=FP)
    assert math.isclose(tfs(r)[7].apply(150, 100)[0], 150 + 3.5, abs_tol=1e-9)


def test_scale_is_cumulative_across_sections():
    r = resolve(Assembly(1.5, [sec(12, s=0.5), sec(12), sec(12, s=-0.25)]), footprints=FP)
    edge = [t.apply(150, 100)[0] for t in tfs(r)]
    assert math.isclose(edge[7], 146.5) and math.isclose(edge[8], 146.0)       # end state carried
    assert all(math.isclose(e, 146.0) for e in edge[8:16])                      # holds, no snap back
    assert math.isclose(edge[16], 146.0) and math.isclose(edge[23], 146.0 + 7 * 0.25)


def test_shift_and_scale_compose_deterministically():
    """Order: scale about the CURRENT footprint centre, then shift (physical
    inches, never scaled)."""
    r = resolve(Assembly(1.5, [sec(12, dx=1.0), sec(12, dx=0.25, s=0.5)]), footprints=FP)
    t = tfs(r)[8 + 3]                                    # section 2, local layer 3
    # entering section 2 the design sits 8 in to the right; scale about THAT centre
    cx, cy = t.apply(100, 100)
    assert math.isclose(cx, 100 + 8 + 3 * 0.25) and math.isclose(cy, 100)
    assert math.isclose(t.apply(150, 100)[0] - t.apply(100, 100)[0], 50 - 3 * 0.5)
    again = resolve(Assembly(1.5, [sec(12, dx=1.0), sec(12, dx=0.25, s=0.5)]), footprints=FP)
    assert tfs(again) == tfs(r)


def test_layer_height_change_recalculates_the_transforms():
    a = Assembly(1.5, [sec(12, 0.25)])
    r1, r2 = resolve(a), resolve(Assembly(2.0, a.sections))
    assert len(r1.instances) == 8 and len(r2.instances) == 6
    assert [t.tx for t in tfs(r2)] == [k * 0.25 for k in range(6)]       # per layer, re-derived


def test_scaling_needs_a_footprint_and_warns_on_collapse():
    with pytest.raises(AssemblyError):
        resolve(Assembly(1.5, [sec(12, s=0.5)]))
    r = resolve(Assembly(1.5, [sec(120, s=1.0)]), footprints=FP)       # 80 layers × 1 in > 50 in
    assert r.warnings and all(t.scale > 0 for t in tfs(r))


def test_profiles_are_a_registry():
    with pytest.raises(AssemblyError):
        resolve(Assembly(1.5, [Section('A', 12, '', SectionTransform('cubic', (1, 0), 0))]))
    PROFILES['test_quadratic'] = lambda k, n: k * k / n        # any profile plugs in the same way
    try:
        r = resolve(Assembly(1.5, [Section('A', 12, '', SectionTransform('test_quadratic', (1, 0), 0))]))
        assert [round(t.tx, 6) for t in tfs(r)][:4] == [0, round(1 / 8, 6), round(4 / 8, 6), round(9 / 8, 6)]
    finally:
        del PROFILES['test_quadratic']


def test_transform_round_trips_in_the_assembly_dict():
    a = Assembly(1.5, [sec(12, 0.25, -0.5, 0.75)])
    b = Assembly.from_dict(a.to_dict())
    assert b.to_dict() == a.to_dict() and b.sections[0].transform.shift == (0.25, -0.5)


def test_preview_places_each_layer_by_its_transform():
    sq = {'id': 's', 'pts': [[50, 50], [150, 50], [150, 150], [50, 150]], 'closed': True}
    src = StaticSource({'A': (DesignInfo('A', 'A'), LayerGeometry('A', [sq]))})
    fp = {'A': Footprint.of_bbox(*bbox_of([sq]))}
    r = resolve(Assembly(1.5, [sec(12, dx=0.5, s=0.25)]), footprints=fp)
    st = build_stack(r, src)
    assert st[0].placed()[0] == [(50, 50), (150, 50), (150, 150), (50, 150)]
    x0 = [x for x, _ in st[4].placed()[0]]
    assert math.isclose(min(x0), 50 + 4 * 0.25 + 4 * 0.5) and math.isclose(max(x0), 150 - 4 * 0.25 + 4 * 0.5)
    assert st[4].polylines is st[0].polylines                  # geometry never copied



# ---------------------------------------------------------------------------
# QUADRATIC: m(k, n) = k (k + 1) / (2n) — step j adds v·j/n; v = the final
# per-layer rate at the top of the section
# ---------------------------------------------------------------------------

def steps(vals):
    return [b - a for a, b in zip(vals, vals[1:])]


@pytest.mark.parametrize('v', [0.25, -0.25, 0.0625])
def test_quadratic_shift_accelerates_to_the_entered_final_rate(v):
    r = resolve(Assembly(1.5, [q(30, dx=v)]))                 # 20 layers
    tx = [t.tx for t in tfs(r)]
    n = 20
    assert tx[0] == 0                                         # section start: nothing added
    st = steps(tx)
    assert math.isclose(st[0], v / n) and abs(st[0]) < abs(v) / 10       # starts near zero
    assert all(abs(b) > abs(a) for a, b in zip(st, st[1:]))               # monotonically growing
    assert math.isclose(st[-1], v * (n - 1) / n)              # top layer's own step ≈ v
    for k in range(n):
        assert math.isclose(tx[k], v * k * (k + 1) / (2 * n), abs_tol=1e-12)
    assert math.isclose(tx[-1], v * (n - 1) / 2)              # total at the top layer
    assert all(t.scale == 1 and t.ty == 0 for t in tfs(r))


def test_quadratic_y_and_combined_xy_share_one_multiplier():
    r = resolve(Assembly(1.5, [q(15, dx=0.5, dy=-0.25)]))
    for t in tfs(r):
        assert math.isclose(t.ty, -0.5 * t.tx, abs_tol=1e-12)


def test_quadratic_handoff_and_positional_continuity():
    """The step into the next section is exactly v; position never jumps;
    the next section restarts its own profile (slope kink accepted)."""
    r = resolve(Assembly(1.5, [q(30, dx=0.25), sec(12), q(15, dx=-0.5)]))
    tx = [t.tx for t in tfs(r)]
    assert math.isclose(tx[20] - tx[19], 0.25)                # into section 2: the final rate
    assert math.isclose(tx[20], 0.25 * 21 / 2)                # end state v (n+1)/2
    assert all(math.isclose(x, tx[20]) for x in tx[20:28])    # vertical section continues from there
    assert math.isclose(tx[28], tx[27]) and math.isclose(tx[29] - tx[28], -0.5 / 10)   # restarts near 0


def test_quadratic_scale_in_accelerating_taper():
    r = resolve(Assembly(1.5, [q(15, s=0.5)]), footprints=FP)          # 10 layers
    edge = [t.apply(150, 100)[0] for t in tfs(r)]
    st = steps(edge)
    for j, d in enumerate(st, start=1):
        assert math.isclose(d, -0.5 * j / 10, abs_tol=1e-9)
    assert all(math.isclose(t.apply(100, 100)[0], 100, abs_tol=1e-9) for t in tfs(r))   # about the centre
    grow = resolve(Assembly(1.5, [q(15, s=-0.5)]), footprints=FP)
    assert math.isclose(tfs(grow)[-1].apply(150, 100)[0], 150 + 0.5 * 9 / 2)


def test_quadratic_combined_keeps_the_composition_order():
    r = resolve(Assembly(1.5, [q(15, dx=0.25, s=0.5)]), footprints=FP)
    t, m = tfs(r)[6], 6 * 7 / 20
    assert math.isclose(t.apply(100, 100)[0], 100 + 0.25 * m)                 # centre: shift only
    assert math.isclose(t.apply(150, 100)[0] - t.apply(100, 100)[0], 50 - 0.5 * m)


def test_quadratic_layer_height_change_renormalises():
    a = [q(30, dx=0.25)]
    r1, r2 = resolve(Assembly(1.5, a)), resolve(Assembly(3.0, a))           # 20 vs 10 layers
    assert len(r2.instances) == 10
    assert math.isclose(steps([t.tx for t in tfs(r2)])[-1], 0.25 * 9 / 10)  # final rate still ≈ v
    assert math.isclose(tfs(r2)[-1].tx, 0.25 * 9 / 2) and math.isclose(tfs(r1)[-1].tx, 0.25 * 19 / 2)


def test_zero_quadratic_equals_no_transform():
    a = resolve(Assembly(1.5, [q(15), q(12, d='B')]))
    b = resolve(Assembly(1.5, [sec(15), sec(12, d='B')]))
    assert tfs(a) == tfs(b) and all(t.scale == 1 and t.tx == 0 == t.ty for t in tfs(a))


def test_linear_profile_formula_is_unchanged():
    r = resolve(Assembly(1.75, [sec(12, 0.25, -0.3, 0.5), sec(7), sec(20, -0.4, 0.1, -0.2)]),
                footprints={'A': Footprint.of_bbox(50, 40, 170, 150)})
    t = tfs(r)
    assert t[0].scale == 1 and t[0].tx == 0 and t[0].ty == 0
    # edge moves exactly 0.5 per layer, centre shifted 0.25 per layer (linear: m = k)
    for k in range(7):
        cx = t[k].apply(110, 95)[0]
        assert math.isclose(cx, 110 + 0.25 * k, abs_tol=1e-9)
        assert math.isclose(t[k].apply(170, 95)[0] - cx, 60 - 0.5 * k, abs_tol=1e-9)
