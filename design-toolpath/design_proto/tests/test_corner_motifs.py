"""
CANONICAL CORNER MOTIFS for the Zigzag / Wave wall lattice (2026-10-07):
significant corners are HARD anchors with a fixed treatment, the ordinary
lattice is fitted between them (integer stitches nearest Target Spacing).

  single pass: Ia → Oa → Ob → Ib — inner face before the inner corner, outer
               face either side of the outer corner (wrapping it), inner face
               after the inner corner (the wall-web experiment's Candidate B
               lower-left corner in out/08_multiple_openings.png)
  double pass: both mirrored phases land the corner station, one on the
               inner corner, one on the outer (production's upper corners in
               the same sheet)

The motif depends on the corner geometry only — never on spacing, phase,
openings elsewhere or the pattern. Adaptive Truss is unchanged.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

import app  # noqa: F401,E402  (path setup)
import wall_lattice as WL                          # noqa: E402
import wall_fixtures as WF                         # noqa: E402
import physical_fixtures as PF                     # noqa: E402
from model import (Vec2, PrintLayer, RectanglePath, RegionInfill, WallSpec, Opening,   # noqa: E402
                   CirclePath)
from graph import route_layer, compute_metrics, build_graph, closure_report   # noqa: E402


def rings_of(layer):
    out = []
    for lat in layer.network_summary()['lattice'].values():
        for reg in lat['regions']:
            if reg.get('rings'):
                out.append([[Vec2(x, y) for x, y in r] for r in reg['rings']])
    return out


def plan(rings, pattern='zigzag', S=20.0, phys=True):
    return WL.plan(rings, S, pattern, 0, True, None, contact=2.25 if phys else 0.0, closed=phys,
                   bead=3.0 if phys else 0.0)


def rect_layer(w=200, h=120, openings=(), pattern='zigzag'):
    R = RectanglePath(100, 140, w, h, id='R')
    R.wall = WallSpec(10)
    lay = PrintLayer('t', source_paths=[R], infills=[RegionInfill('I', 'R', pattern, {'spacing': 20})])
    lay.openings = [Opening(f'o{k}', 'R', c, wd) for k, (c, wd) in enumerate(openings)]
    return PF._phys(lay)


def local(m):
    """A motif's landings in its corner frame: origin at the outer corner,
    axes = back along the incoming run direction / on along the outgoing one
    (identical numbers for congruent corners, whatever their orientation)."""
    O = m['outer']
    t1, t2 = m['t1'], m['t2']
    out = {}
    for k, (x, y) in m['landings'].items():
        vx, vy = x - O[0], y - O[1]
        out[k] = (round(-(vx * t1[0] + vy * t1[1]), 3), round(vx * t2[0] + vy * t2[1], 3))
    return out


def motifs(lp):
    return lp.report['corner_motifs']['motifs']


def close(a, b, tol=0.05):
    return a.keys() == b.keys() and all(math.dist(a[k], b[k]) <= tol for k in a)


def polyline_order(lp, pts):
    """Indices of the given points along the lattice polylines (each must
    be a vertex), or None."""
    for pl in lp.polylines:
        idx = []
        for p in pts:
            j = next((i for i, q in enumerate(pl) if math.dist((q.x, q.y), p) < 1e-6), None)
            if j is None:
                break
            idx.append(j)
        if len(idx) == len(pts):
            return idx
    return None


# --- single pass ---------------------------------------------------------------------

@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_single_pass_corners_carry_the_canonical_single_motif(pattern):
    (rings,) = rings_of(rect_layer())
    lp = plan(rings, pattern)
    ms = motifs(lp)
    assert len(ms) == 4 and all(m['kind'] == 'single' for m in ms)
    assert lp.report['corner_motifs']['fallback'] == 0
    for m in ms:
        L = m['landings']
        # contacts near both sides of the OUTER and of the INNER corner …
        assert math.dist(L['Oa'], m['outer']) < 6 and math.dist(L['Ob'], m['outer']) < 6
        assert math.dist(L['Ia'], m['inner']) < 9 and math.dist(L['Ib'], m['inner']) < 9
        # … printed in the motif's order Ia → Oa → Ob → Ib (the seam corner of
        # a loop starts at Ob: its order wraps)
        idx = polyline_order(lp, [L['Ia'], L['Oa'], L['Ob'], L['Ib']])
        assert idx is not None
        assert idx == sorted(idx) or (idx[1] < idx[2] or idx[2] == 0)


def test_equivalent_corners_get_equivalent_motifs_whatever_the_spans():
    """A 200 × 120 and a 260 × 90 ring (different spans) and three target
    spacings: every 90° corner's motif is the same in its own frame."""
    ref = None
    for w, h in ((200, 120), (260, 90)):
        (rings,) = rings_of(rect_layer(w, h))
        for S in (14.0, 20.0, 27.0):
            for m in motifs(plan(rings, 'zigzag', S)):
                loc = local(m)
                ref = ref or loc
                assert close(loc, ref), (w, h, S, loc, ref)


def test_target_spacing_changes_the_fitted_lattice_not_the_motifs():
    (rings,) = rings_of(rect_layer())
    a, b = plan(rings, 'zigzag', 14.0), plan(rings, 'zigzag', 27.0)
    assert a.report['runs'][0]['stitches'] != b.report['runs'][0]['stitches']   # fitted lattice changed
    ma = sorted((m['outer'], tuple(sorted(m['landings'].items()))) for m in motifs(a))
    mb = sorted((m['outer'], tuple(sorted(m['landings'].items()))) for m in motifs(b))
    assert ma == mb                                                           # motifs exactly unchanged
    # the spacing between the motifs stays near the target
    for lp, S in ((a, 14.0), (b, 27.0)):
        assert abs(lp.report['pitch_min'] - S) < 0.35 * S and abs(lp.report['pitch_max'] - S) < 0.35 * S


@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_single_motif_closes_and_stays_in_the_wall(pattern):
    lay = rect_layer(pattern=pattern)
    p, m = lay._build_effective()
    rl = lay.to_routing_layer(p, m)
    c = closure_report(build_graph(rl))
    mt = compute_metrics(route_layer(rl, allow_retrace=False))
    assert not c['open'] and mt['print_runs'] == 1 and mt['retrace_distance'] == 0
    (rings,) = rings_of(lay)
    assert plan(rings, pattern).report['contact_violations'] == 0


def test_runs_between_junctions_use_the_single_motif_too():
    (rings,) = rings_of(PF._phys(WF.rect_branches(1)))
    lp = plan(rings)
    kinds = [m['kind'] for m in motifs(lp)]
    assert kinds.count('single') == 4 and lp.report['corner_motifs']['fallback'] == 0


# --- double pass ---------------------------------------------------------------------

def test_double_pass_corners_carry_the_canonical_double_motif():
    """A ring cut by an opening is out-and-back (two mirrored passes): at
    every corner one pass lands the inner corner, the other the outer."""
    ms = []
    for rings in rings_of(rect_layer(openings=[(100.0, 30.0)])):
        lp = plan(rings)
        ms += motifs(lp)
    assert len(ms) == 4 and all(m['kind'] == 'double' for m in ms)
    ref = local(ms[0])
    for m in ms:
        assert close(local(m), ref)
        L = m['landings']
        # the contacts sit at the corner points (pulled in by the contact separation)
        assert math.dist(L['O'], m['outer']) < 4.5 and math.dist(L['I'], m['inner']) < 4.5


def test_double_motif_is_independent_of_target_spacing():
    rs = rings_of(rect_layer(openings=[(100.0, 30.0)]))
    a = [local(m) for r in rs for m in motifs(plan(r, 'zigzag', 14.0))]
    b = [local(m) for r in rs for m in motifs(plan(r, 'zigzag', 27.0))]
    assert all(close(x, y) for x, y in zip(a, b))


def test_a_corner_beside_a_dead_end_is_still_anchored():
    """The short leg of a piece cut by an opening (its cap end's turn
    readings used to out-rank the real corner)."""
    rs = rings_of(rect_layer(openings=[(560.0, 24.0), (430.0, 30.0)]))
    short = next(r for r in rs if max(p.y for p in r[0]) > 250 and min(p.x for p in r[0]) < 101
                 and max(p.x for p in r[0]) < 200)
    assert [m['kind'] for m in motifs(plan(short))] == ['double']


# --- curves, openings, patterns, truss ----------------------------------------------------

def _circle(R):
    C = CirclePath(200, 200, R, id='C')
    C.wall = WallSpec(10)
    return PF._phys(PrintLayer('t', source_paths=[C], infills=[RegionInfill('I', 'C', 'zigzag', {'spacing': 20})]))


@pytest.mark.parametrize('layer', [
    lambda: PF._phys(WF.curved()),                      # a gentle Bézier wall
    lambda: _circle(60), lambda: _circle(30),           # even arcs (also a tight one)
    lambda: PF._phys(WF.rounded_rect_loop(r=40)),       # a generous corner radius: a curve
], ids=['curve', 'circle60', 'circle30', 'rounded40'])
def test_curves_carry_the_ordinary_pattern(layer):
    for rings in rings_of(layer()):
        lp = plan(rings)
        assert motifs(lp) == []


def test_tight_rounded_corners_are_still_corners():
    for r in (10, 20):
        (rings,) = rings_of(PF._phys(WF.rounded_rect_loop(r=r)))
        assert [m['kind'] for m in motifs(plan(rings))] == ['single'] * 4


def test_an_opening_does_not_change_an_unrelated_corner():
    """Moving an opening in the top wall leaves the bottom corners' motifs
    exactly as they were."""
    def bottom(openings):
        out = []
        for rings in rings_of(rect_layer(openings=openings)):
            for m in motifs(plan(rings)):
                if m['outer'][1] > 250:
                    out.append((m['kind'], tuple(sorted(m['landings'].items()))))
        return sorted(out)
    a, b = bottom([(100.0, 30.0)]), bottom([(110.0, 30.0)])
    assert a and a == b


def test_zigzag_and_wave_share_the_corner_semantics_not_the_ordinary_stitch():
    (rings,) = rings_of(rect_layer())
    z, w = plan(rings, 'zigzag'), plan(rings, 'wave')
    mz = sorted((m['outer'], tuple(sorted(m['landings'].items()))) for m in motifs(z))
    mw = sorted((m['outer'], tuple(sorted(m['landings'].items()))) for m in motifs(w))
    assert mz == mw                                                 # identical motifs
    assert z.report['runs'][0]['stitches'] == w.report['runs'][0]['stitches']
    nz = sum(len(p) for p in z.polylines)
    nw = sum(len(p) for p in w.polylines)
    assert nw > 2 * nz                                              # wave keeps its curved ordinary stitches


def test_adaptive_truss_is_untouched():
    (rings,) = rings_of(rect_layer(pattern='truss'))
    lp = WL.plan(rings, 20, 'truss', 0, True, None, contact=2.25, closed=True, bead=3.0, truss={})
    assert lp.report['corner_motifs'] == 0
    assert sum(r['corners'] for r in lp.report['runs']) == 4      # its own corner handling (unchanged)
