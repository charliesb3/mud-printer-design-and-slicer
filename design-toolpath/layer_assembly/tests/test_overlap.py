"""
MINIMUM LAYER OVERLAP (support.py): a general inter-layer rule on the
PLACED beads — two parallel beads of width w offset by d overlap w − d, so
required overlap o allows a centreline overhang of w − o; beyond it the
exact covered cross-section (union of lower beads) decides. Pure: no
Designer.
"""
import copy
import math

import pytest

from layer_assembly.model import Assembly, Section, SectionTransform, Footprint, TransformCenter, resolve, AssemblyError
from layer_assembly.support import analyse_support
from layer_assembly.groups import build_groups
from layer_assembly.source import StaticSource, DesignInfo, LayerGeometry, stack_geometry

W = 3.0
LINE = [{'pts': [[0, 0], [100, 0]], 'closed': False}]
SQUARE = [{'pts': [[0, 0], [100, 0], [100, 100], [0, 100]], 'closed': True}]


def lean(d, o, n=2, profile='linear', geo=LINE):
    """n layers of `geo`, each shifted d (per layer, Y) — min overlap o."""
    a = Assembly(1.5, [Section('A', 1.5 * n, '', SectionTransform(profile, (0, d), 0))], max_overhang=W - o)
    return a, analyse_support(resolve(a), {'A': (geo, W)}, a)


@pytest.mark.parametrize('o, ok, bad', [(0.0, 2.99, 3.01), (2.0, 0.99, 1.01), (1.5, 1.49, 1.51)])
def test_allowed_overhang_is_bead_minus_overlap(o, ok, bad):
    assert lean(ok, o)[1].findings == []
    f = lean(bad, o)[1].findings
    assert len(f) == 1 and f[0].status == 'insufficient_support' and f[0].kind == 'overlap'
    assert math.isclose(f[0].min_overlap, o) and math.isclose(f[0].overlap, max(0.0, W - bad), abs_tol=1e-6)


def test_zero_overlap_permits_one_full_bead_and_full_overlap_essentially_none():
    assert lean(2.999, 0.0)[1].findings == [] and lean(3.2, 0.0)[1].findings
    assert lean(0.0, W)[1].findings == []                            # an identical placement
    f = lean(0.01, W)[1].findings
    assert f and math.isclose(f[0].overlap, W - 0.01, abs_tol=1e-6)  # any lateral offset is reported


def test_a_bead_straddling_two_lower_beads_is_supported_by_their_union():
    two = [{'pts': [[0, 0], [100, 0]], 'closed': False}, {'pts': [[0, 3], [100, 3]], 'closed': False}]
    one = [{'pts': [[0, 1.5], [100, 1.5]], 'closed': False}]
    a = Assembly(1.5, [Section('L', 1.5), Section('U', 1.5)], max_overhang=W - 2.5)
    assert analyse_support(resolve(a), {'L': (two, W), 'U': (one, W)}, a).findings == []
    half = [{'pts': [[0, 0], [100, 0]], 'closed': False}]           # only one of them: 1.5 in overlap
    f = analyse_support(resolve(a), {'L': (half, W), 'U': (one, W)}, a).findings
    assert f and math.isclose(f[0].overlap, 1.5, abs_tol=1e-6)


def test_quadratic_shift_crosses_the_threshold_only_at_higher_layers():
    # 20 layers, final rate 2 in/layer: step k adds 2k/20 > allowed 1.5 only for k ≥ 16
    _, rep = lean(2.0, 1.5, n=20, profile='quadratic')
    assert sorted(f.layer for f in rep.findings) == [16, 17, 18, 19]
    assert all(f.status == 'insufficient_support' for f in rep.findings)
    assert min(f.overlap for f in rep.findings) == pytest.approx(W - 2.0 * 19 / 20, abs=1e-6)


@pytest.mark.parametrize('s, n_find', [(1.4, 0), (1.6, 1)])
def test_scale_overhang(s, n_find):
    a = Assembly(1.5, [Section('A', 3, '', SectionTransform('linear', (0, 0), s))], max_overhang=W - 1.5)
    rep = analyse_support(resolve(a, footprints={'A': Footprint.of_bbox(0, 0, 100, 100)}), {'A': (SQUARE, W)}, a)
    assert len(rep.findings) == n_find and all(f.kind == 'overlap' for f in rep.findings)


def test_transform_group_placement_is_what_is_checked():
    tag = lambda polys, s: [dict(p, src=[s] * len(p['pts'])) for p in polys]
    sq = lambda x0, y0, x1, y1: [{'pts': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], 'closed': True}]
    geo = {'A': (tag(sq(0, 0, 100, 100), 'A') + tag(sq(200, 20, 260, 80), 'B'), W)}
    cs = [TransformCenter('c1', 50, 50, ('A',)), TransformCenter('c2', 230, 50, ('B',))]
    gm = build_groups(geo, {'A': None}, ['A'], cs)
    for s, xs in ((1.4, []), (1.6, [50, 230])):
        a = Assembly(1.5, [Section('A', 3, '', SectionTransform('linear', (0, 0), s))],
                     center_mode='multiple', centers=cs)
        res = resolve(a, footprints={'A': Footprint.of_bbox(0, 0, 260, 100)}, groups=gm)
        g = stack_geometry(res, StaticSource({'A': (DesignInfo('A', 'A'), LayerGeometry('A', geo['A'][0], W))}))
        rep = analyse_support(res, g, a, gm)
        assert sorted(round(f.centre[0] / 10) * 10 for f in rep.findings) == xs    # each group's own edge


def test_a_lean_is_insufficient_support_and_a_gap_is_a_header():
    _, rep = lean(2.5, 1.5)
    assert rep.findings[0].status == 'insufficient_support' and rep.to_dict()['needs_header'] == 0
    gap = [{'pts': [[0, 0], [30, 0]], 'closed': False}, {'pts': [[70, 0], [100, 0]], 'closed': False}]
    a = Assembly(1.5, [Section('G', 1.5), Section('A', 1.5)], max_overhang=W - 1.5)
    f = analyse_support(resolve(a), {'G': (gap, W), 'A': (LINE, W)}, a).findings
    assert len(f) == 1 and f[0].kind == 'bridge' and f[0].status == 'needs_header'


def test_changing_the_setting_recomputes_diagnostics_without_changing_geometry():
    geo = {'A': (copy.deepcopy(LINE), W)}
    a = Assembly(1.5, [Section('A', 3, '', SectionTransform('linear', (0, 1.25), 0))], max_overhang=W - 1.5)
    res = resolve(a)
    assert analyse_support(res, geo, a).findings == []
    a.max_overhang = W - 2.0
    res2 = resolve(a)
    assert analyse_support(res2, geo, a).findings                     # 1.25 > 3 − 2
    assert [i.transform for i in res.instances] == [i.transform for i in res2.instances]
    assert geo == {'A': (LINE, W)}


def test_validation_and_round_trip():
    a = Assembly(1.5, [Section('A', 3, '', SectionTransform('linear', (0, 0.5), 0))], max_overhang=0.0)
    rep = analyse_support(resolve(a), {'A': (LINE, W)}, a)
    assert rep.findings and math.isclose(rep.min_overlap, W)               # no overhang: overlap = w
    assert Assembly.from_dict({'layer_height': 1.5, 'sections': []}).max_overhang == 1.5   # default
    assert Assembly.from_dict(a.to_dict()).max_overhang == 0.0


def test_a_leaning_ring_reports_its_true_overlap_at_the_corners():
    """A closed square leaning 1.6 in per layer diagonally across neither
    leg: at a convex corner the cross-section of ONE leg would see the
    other leg's corner stick out (0 in "overlap"); the better-covered leg
    is the bead's real contact — the worst overlap is W − 1.6 everywhere."""
    a = Assembly(1.5, [Section('A', 3.0, '', SectionTransform('linear', (1.6, 0), 0))], max_overhang=W - 1.5)
    f = analyse_support(resolve(a), {'A': (SQUARE, W)}, a).findings
    assert f and all(x.kind == 'overlap' for x in f)
    assert min(x.overlap for x in f) == pytest.approx(W - 1.6, abs=1e-6)


def test_consecutive_overlap_failures_aggregate_into_one_group():
    """A steep lean over 40 layers is ONE aggregated group (layers a–b,
    required vs worst overlap), not 40 viewport labels."""
    _, rep = lean(2.0, 1.5, n=40)
    groups = rep.to_dict()['groups']
    assert len(rep.findings) == 39 and len(groups) == 1
    g = groups[0]
    assert g['layers'] == [1, 39] and g['count'] == 39 and g['kind'] == 'overlap'
    assert g['min_overlap'] == 1.5 and g['worst_overlap'] == pytest.approx(W - 2.0, abs=1e-6)


def test_headers_stay_individual_groups():
    gap = [{'pts': [[0, 0], [30, 0]], 'closed': False}, {'pts': [[70, 0], [100, 0]], 'closed': False}]
    a = Assembly(1.5, [Section('G', 1.5), Section('A', 1.5)], max_overhang=W - 1.5)
    rep = analyse_support(resolve(a), {'G': (gap, W), 'A': (LINE, W)}, a)
    assert [g['kind'] for g in rep.to_dict()['groups']] == ['bridge']


# ---- MAXIMUM OVERHANG: the user-facing input (2026-10-06) -----------------------

def leanh(d, h, w=W, n=2):
    a = Assembly(1.5, [Section('A', 1.5 * n, '', SectionTransform('linear', (0, d), 0))], max_overhang=h)
    return a, analyse_support(resolve(a), {'A': (LINE, w)}, a)


@pytest.mark.parametrize('h, ok, bad', [(1.0, 0.99, 1.01), (0.0, 0.0, 0.01), (3.0, 2.99, 3.2)])
def test_maximum_overhang_derives_the_required_overlap_from_the_bead_width(h, ok, bad):
    """Bead 3, max overhang 1 → minimum overlap 2 (= bead − overhang)."""
    assert leanh(ok, h)[1].findings == []
    a, rep = leanh(bad, h)
    assert rep.findings and all(math.isclose(f.min_overlap, W - h) for f in rep.findings)
    assert math.isclose(rep.min_overlap, W - h) and rep.max_overhang == h
    assert rep.to_dict()['max_overhang'] == h


def test_the_bead_width_comes_from_the_material_not_the_assembly():
    """The same max overhang with a 4 in bead (the Designer's material)
    requires 3 in of overlap: the Assembly stores no bead width."""
    _, rep = leanh(1.2, 1.0, w=4.0)
    assert rep.findings and math.isclose(rep.min_overlap, 3.0) and math.isclose(rep.findings[0].overlap, 2.8)
    _, rep = leanh(0.9, 1.0, w=4.0)
    assert rep.findings == [] and math.isclose(rep.min_overlap, 3.0)
    assert 'bead_width' not in Assembly(1.5, [], max_overhang=1.0).to_dict()


def test_maximum_overhang_validation_and_round_trip():
    with pytest.raises(AssemblyError):
        Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'max_overhang': -0.5})
    a = Assembly.from_dict({'layer_height': 1.5, 'sections': [], 'max_overhang': 1.0})
    assert a.max_overhang == 1.0 and Assembly.from_dict(a.to_dict()).max_overhang == 1.0
    _, rep = leanh(0.5, 4.0)                                                 # overhang > bead: clamped, warned
    assert 'maximum overhang 4 in exceeds the bead width 3 in' in rep.warnings[0]
