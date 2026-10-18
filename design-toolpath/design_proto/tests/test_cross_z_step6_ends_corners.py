"""
STEP 6 — ENDS AND ORDINARY CORNERS THROUGH Z (2026-10-18).

An architectural boundary now REACHES the persistent web (WALL_ARCHITECTURE
§Z.0.C, G–J, O): a moving wall end / corner moves THROUGH the construction.
  * a comb station is deposited iff it lies at least e_min (the pitch floor)
    inside its segment's anchors: the end CONSUMES the station it reaches
    (N → N − 1) and EXPOSES the next comb station of the same pitch / phase
    once there is room (N → N + 1) — locally, each end on its own;
  * the survivors never move (no redistribution, no re-phasing);
  * a single pass (the rectangle's corner-motif loop) changes by PAIRS: an
    end in the odd state folds the pair it gains / loses out of / into the
    deposited stitch — never a pair across the cavity in one layer;
  * end caps and corner motifs move with their end / corner, supported;
  * every layer is supported by the one below within Maximum Overhang and
    routes closed (one piece).
No openings / short territories (Step 7), circles (Step 8) or junctions
(Step 9) here.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from cross_z import (about, findings, library, line, near, rect, route, skin_web_doc, stack, web,  # noqa: E402
                     web_points)

T = 10.0
S = (T - 3.0 - 2 * (3.0 - 0.75)) / math.tan(math.radians(20.0))     # derived pitch (2.5 in cavity)


def _line_lib():
    return library(skin_web_doc([line('L', (0, 0), (120, 0))], T=T))


def _rect_lib():
    return library(skin_web_doc([rect('R', 0, 0, 120, 80)], T=T))


def _xs(b):
    """x of the web's landings on the straight wall's rails (y = ±1.25)."""
    return sorted({round(p[0], 3) for p in web_points(b) if abs(abs(p[1]) - 1.25) < 0.05})


def _valid(L):
    for i, b in enumerate(L):
        ends, pieces = route(b)
        assert not ends and pieces == 1, (i, ends, pieces)
    assert findings(L) == []


def _no_giant_diagonal(L):
    """No web segment longer than the longest legitimate end-cell stitch."""
    lim = math.hypot(S + 2 * 1.5, 2.5) + 0.5
    for i, b in enumerate(L):
        for pl in web(b):
            for a, c in zip(pl, pl[1:]):
                if abs(a[1]) < 1.3 and abs(c[1]) < 1.3:          # (a straight wall's web band)
                    assert math.dist(a, c) <= lim, (i, a, c)


def test_one_end_consumes_locally_n_to_n_minus_1():
    """The right end moves in ½ in per layer for 30 layers (15 in, two
    deaths). Each death removes exactly the station the end reached; the
    N − 1 survivors are the same stations, unmoved; the far end is
    untouched."""
    lib = _line_lib()
    L = stack(lib, [{'L': about((120 - 0.5 * i) / 120, (0, 0))} for i in range(31)])
    ref = _xs(L[0])
    counts = []
    for i, b in enumerate(L):
        xs = _xs(b)
        counts.append(len(xs))
        end = 120 - 0.5 * i
        # every landing more than one cell + a thickness from the moving end
        # is a reference landing, unmoved
        keep = [x for x in ref[1:] if x < end - S - 2 * T]
        assert [x for x in xs[1:] if x < end - S - 2 * T] == keep, i
        assert xs[0] == pytest.approx(ref[0], abs=0.05)             # the fixed end's own station
    assert counts == sorted(counts, reverse=True)                   # no pulsing
    assert counts[0] - counts[-1] == 2                              # two local deaths
    assert all(a - b in (0, 1) for a, b in zip(counts, counts[1:]))
    _no_giant_diagonal(L)
    _valid(L)


def test_both_ends_consume_independently():
    """Scaled about the centre (each end 1 in / layer in): the left end
    consumes from the left, the right from the right; the middle never
    moves, and the two ends' deaths mirror each other."""
    lib = _line_lib()
    L = stack(lib, [{'L': about((120 - 1.0 * i) / 120, (60, 0))} for i in range(31)])
    ref = _xs(L[0])
    for i, b in enumerate(L):
        xs = _xs(b)
        lo, hi = 0.5 * i, 120 - 0.5 * i
        mid = [x for x in ref if lo + S + 2 * T < x < hi - S - 2 * T]
        assert [x for x in xs if lo + S + 2 * T < x < hi - S - 2 * T] == mid, i
        # the stations both ends still carry are reference stations (no shift)
        inner = [x for x in xs[1:-1]]
        assert all(any(abs(x - y) < 0.05 for y in ref) for x in inner[1:-1]), i
        left = sum(1 for x in xs if x < 60)
        right = sum(1 for x in xs if x > 60)
        assert abs(left - right) <= 1, (i, xs)                        # symmetric, independent
    _no_giant_diagonal(L)
    _valid(L)


def test_expanding_end_grows_new_stations_only_in_new_territory():
    """The right end moves out ½ in per layer for 40 layers (20 in). Every
    reference station stays exactly where it was; each new station appears
    beyond the old end, one pitch on from the last (the same comb — never
    the old ones spread apart)."""
    lib = _line_lib()
    L = stack(lib, [{'L': about((120 + 0.5 * i) / 120, (0, 0))} for i in range(41)])
    ref = _xs(L[0])
    interior = ref[1:-1]
    counts = []
    for i, b in enumerate(L):
        xs = _xs(b)
        counts.append(len(xs))
        assert [x for x in xs if x <= interior[-1] + 1e-6][:len(interior) + 1][1:] == interior, i
        new = [x for x in xs[1:-1] if x > interior[-1] + 1e-6]
        steps = [b_ - a_ for a_, b_ in zip([interior[-1]] + new, new)]
        assert all(abs(d - S) < 0.01 for d in steps), (i, steps)     # the comb's pitch, in new territory
    assert counts == sorted(counts) and counts[-1] - counts[0] >= 2
    _no_giant_diagonal(L)
    _valid(L)


def _side(b, side, lo, hi):
    band = {'top': lambda p: p[1] > 55, 'bottom': lambda p: p[1] < 25,
            'left': lambda p: p[0] < 25, 'right': lambda p: p[0] > 95}[side]
    k = 0 if side in ('top', 'bottom') else 1
    return sorted({round(p[k], 3) for p in web_points(b) if band(p) and lo < p[k] < hi})


def _stitches(b):
    return b.network['lattice']['WEBR']['regions'][0]['runs'][0]['stitches']


@pytest.mark.parametrize('k_step', [-1.0, 1.0], ids=['shrinking', 'expanding'])
def test_rectangle_corners_consume_and_expose_locally(k_step):
    """Scaled about its centre 1 in / layer for 30 layers: the corners move
    in (out) along both sides through the web. Each side's interior — away
    from both corners by a cell and two thicknesses — never moves; the
    count changes monotonically (no pulsing); the corner motifs travel with
    their corners, supported."""
    lib = _rect_lib()
    L = stack(lib, [{'R': about((120 + k_step * i) / 120, (60, 40))} for i in range(31)])
    ref = {s: _side(L[0], s, -1e9, 1e9) for s in ('top', 'bottom', 'left', 'right')}
    for i, b in enumerate(L):
        dx, dy = 0.5 * i * -k_step, (i / 120) * 40 * -k_step          # corner travel per axis
        m = S + 2 * T
        dx, dy = max(dx, 0.0), max(dy, 0.0)             # (expanding: the reference's own interior)
        for s, (lo, hi) in (('top', (dx + m, 120 - dx - m)), ('bottom', (dx + m, 120 - dx - m)),
                            ('left', (dy + m, 80 - dy - m)), ('right', (dy + m, 80 - dy - m))):
            want = [x for x in ref[s] if lo < x < hi]
            assert _side(b, s, lo, hi) == want, (i, s)
    counts = [_stitches(b) for b in L]
    assert counts == sorted(counts, reverse=k_step < 0), counts
    assert counts[0] != counts[-1]
    _valid(L)


def test_moving_corner_leaves_the_fixed_corner_and_side_interiors():
    """Scaled about its lower-left corner (1 in / layer, 30 layers): the
    upper-right corner moves in along both its sides; the fixed corner's
    construction and every side interior are untouched."""
    lib = _rect_lib()
    L = stack(lib, [{'R': about((120 - 1.0 * i) / 120, (0, 0))} for i in range(31)])
    near0 = lambda b: [[p for p in pl if p[0] < 30 and p[1] < 30] for pl in web(b)]
    ref0 = near0(L[0])
    same = lambda A, B: all(near(p, B, 0.05) for pl in A for p in pl) and all(near(q, A, 0.05) for pl in B for q in pl)
    refb = _side(L[0], 'bottom', 30, 60)
    refl = _side(L[0], 'left', 30, 45)
    for i, b in enumerate(L):
        assert same(near0(b), ref0), i                         # (the fixed corner: resampling only)
        assert _side(b, 'bottom', 30, 60) == refb, i
        assert _side(b, 'left', 30, 45) == refl, i
    counts = [_stitches(b) for b in L]
    assert counts == sorted(counts, reverse=True), counts
    _valid(L)
