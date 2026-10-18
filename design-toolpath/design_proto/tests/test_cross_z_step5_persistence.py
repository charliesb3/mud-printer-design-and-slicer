"""
STEP 5 — FUNDAMENTAL CROSS-Z PERSISTENCE ON SIMPLE TOPOLOGY (2026-10-18).

One straight Skin + Web wall and one plain rectangle, under simple Assembly
transforms, measured on the DEPOSITED web (WALL_ARCHITECTURE §Z.0.A–C, F, S):
  * construction follows the legitimate motion of its continuing host
    (a translated wall's web translates with it, exactly);
  * after that host motion, interior web landings do not move at all — no
    longitudinal drift, no re-spacing, no re-phasing, no redistribution,
    however the available length changes;
  * the station count stays the same (no pulsing);
  * every layer is supported by the one below within Maximum Overhang
    (the bead-level checker) and routes closed.
Each stack STOPS before a moving end / corner reaches a station (local
repeat death / birth is Step 6).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from cross_z import (about, findings, library, line, near, rect, route, skin_web_doc, stack,  # noqa: E402
                     web, web_points)

T = 10.0
RAIL = 0.5 * (T - 3.0) - (3.0 - 0.75)      # contact rails: ±1.25 off the centre line (skins inset ½ bead)


def _line_lib():
    return library(skin_web_doc([line('L', (0, 0), (120, 0))], T=T))


def _rect_lib():
    return library(skin_web_doc([rect('R', 0, 0, 120, 80)], T=T))


def _landings_x(b, y0=0.0):
    """x of the web's landings on a horizontal wall at y0 (both rails)."""
    return sorted({round(p[0], 4) for p in web_points(b) if abs(abs(p[1] - y0) - RAIL) < 0.05})


TOL = 0.05     # in: corner / end transitions are re-derived from the moved walls (sampling noise ≪ a bead)


def _same(got, ref, tol=TOL):
    """The same deposited web (polylines): every vertex of each lies within
    tol of the other's polylines (a curve may be resampled differently)."""
    return all(near(p, ref, tol) for pl in got for p in pl) and all(near(q, got, tol) for pl in ref for q in pl)


def _valid(L):
    for i, b in enumerate(L):
        ends, pieces = route(b)
        assert not ends and pieces == 1, (i, ends, pieces)
    assert findings(L) == []


def test_translated_wall_carries_its_web_exactly():
    """A rigid translation (along and across the wall, ½ in per layer) is
    host motion: every landing moves with the wall, nothing else."""
    lib = _line_lib()
    L = stack(lib, [{'L': (1.0, 0.5 * i, 0.3 * i)} for i in range(9)])
    ref = web(L[0])
    for i, b in enumerate(L):
        got = [[(x - 0.5 * i, y - 0.3 * i) for x, y in pl] for pl in web(b)]
        assert _same(got, ref), i
    _valid(L)


@pytest.mark.parametrize('pivot', [(0.0, 0.0), (60.0, 0.0)], ids=['one_end', 'both_ends'])
def test_moving_ends_leave_the_interior_where_it_was(pivot):
    """One end (scaled about the other end) or both ends (about the centre)
    move inward ½ in per layer for 8 layers — short of the end cells'
    consumption. The interior landings stay exactly where they were
    deposited: no compression, no shift, no redistribution."""
    lib = _line_lib()
    n = 8
    L = stack(lib, [{'L': about((120 - 0.5 * i * (1 if pivot[0] == 0 else 2)) / 120, pivot)}
                    for i in range(n + 1)])
    ref = _landings_x(L[0])
    interior = [x for x in ref if 12.0 < x < 108.0 - 1e-6]
    assert len(interior) >= 12
    for i, b in enumerate(L):
        xs = _landings_x(b)
        assert len(xs) == len(ref), i                           # no birth / death yet
        assert [x for x in xs if 12.0 < x < 108.0 - 1e-6] == interior, i
    # the moving end(s) carry only their own end station
    last = _landings_x(L[-1])
    assert last[-1] == pytest.approx(ref[-1] - 0.5 * n * (1 if pivot[0] == 0 else 1), abs=0.05)
    if pivot[0] == 0:
        assert last[0] == ref[0]                               # the fixed end: untouched
    else:
        assert last[0] == pytest.approx(ref[0] + 0.5 * n, abs=0.05)
    _valid(L)


def _side_landings(b, side):
    """Rectangle 120 × 80 at the origin, centre line inset ½ T: tangential
    coordinates of the web's landings along one SIDE's interior (away from
    the corner motifs)."""
    band = {'top': lambda p: p[1] > 60 and 25 < p[0] < 95, 'bottom': lambda p: p[1] < 20 and 25 < p[0] < 95,
            'left': lambda p: p[0] < 25 and 22 < p[1] < 58, 'right': lambda p: p[0] > 95 and 22 < p[1] < 58}[side]
    k = 0 if side in ('top', 'bottom') else 1
    return sorted({round(p[k], 4) for p in web_points(b) if band(p)})


def test_rectangle_scaled_about_its_centre_keeps_side_interiors():
    """Corners move inward along both sides (½ in / layer in x), the sides
    move inward across themselves (host motion): every side's interior
    landings keep their tangential position exactly."""
    lib = _rect_lib()
    L = stack(lib, [{'R': about((120 - i) / 120, (60, 40))} for i in range(7)])
    ref = {s: _side_landings(L[0], s) for s in ('top', 'bottom', 'left', 'right')}
    assert all(len(v) >= 4 for v in ref.values()), ref
    for i, b in enumerate(L):
        for s in ref:
            assert _side_landings(b, s) == ref[s], (i, s)
    counts = [b.network['lattice']['WEBR']['regions'][0]['runs'][0]['stitches'] for b in L]
    assert len(set(counts)) == 1, counts
    _valid(L)


def test_rectangle_under_rigid_motion_carries_its_web_exactly():
    lib = _rect_lib()
    L = stack(lib, [{'R': (1.0, 0.4 * i, -0.6 * i)} for i in range(9)])
    ref = web(L[0])
    for i, b in enumerate(L):
        got = [[(x - 0.4 * i, y + 0.6 * i) for x, y in pl] for pl in web(b)]
        assert _same(got, ref), i
    _valid(L)
