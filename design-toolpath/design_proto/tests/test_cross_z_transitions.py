"""
TRANSITIONS CONSUME / EXPOSE PERSISTENT CONSTRUCTION — one rule for corners,
openings, end caps and junctions (2026-10-18; WALL_ARCHITECTURE §Z.11).

Every stack here is a WHOLE-DESIGN scale about one centre (semantic '*': what
Assembly "Preserve physical dimensions" sends the Designer, single centre).
An architectural transition (wall end, corner, junction) moves THROUGH the
persistent Skin + Web web:
  * construction it has not reached stays where its host carried it;
  * only construction it reaches dies / is born (N → N ∓ 1, the survivors
    never move), and every change of the deposited web lies at a moving
    transition;
  * a junction is an anchor like a corner: the face a run lands there is the
    junction's (inherited from below), so a station dying / born beside it
    FOLDS — the junction's hand-offs never flip;
  * a segment whose stations an anchor has consumed continues the same comb
    (never re-planned);
  * every layer is supported within Maximum Overhang and routes closed.
Measured on deposited geometry (cross_z: support findings, route closure,
landings, `changed`).
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from cross_z import (about, changed, circle, findings, library, line, rect, route, skin_web_doc,  # noqa: E402
                     stack, web_points)

W, O = 3.0, 0.75


def pitch(T):
    return (T - W - 2 * (W - O)) / math.tan(math.radians(20.0))


def whole(ks, c):
    return [{'*': about(k, c)} if abs(k - 1.0) > 1e-12 else None for k in ks]


def _closed(L):
    for i, b in enumerate(L):
        ends, pieces = route(b)
        assert not ends and pieces == 1, (i, ends, pieces)


# -- A / B: end caps -----------------------------------------------------------

T_END = 16.0                                     # a wide cavity: a re-planned web cannot hide in it
RAIL = T_END / 2 - W / 2 - (W - O)               # the straight wall's contact rails: y = ±RAIL


def _end_stack(sign):
    """An open Skin + Web wall 0 … 120 scaled about (30, 0): its right end
    moves ¾ in per layer, its left end ¼ in (60 layers)."""
    lib = library(skin_web_doc([line('L', (0, 0), (120, 0))], T=T_END))
    ks = [1 + sign * 0.75 * i / 90 for i in range(61)]
    return stack(lib, whole(ks, (30, 0))), ks


def _landings(b):
    return sorted({round(p[0], 2) for p in web_points(b) if abs(abs(p[1]) - RAIL) < 0.05})


def _near_ends(p, k, m):
    return abs(p[0] - (30 - 30 * k)) <= m or abs(p[0] - (30 + 90 * k)) <= m


def test_end_cap_shrinking_consumes_locally():
    """TEST A. Each end moves inward through the web: a station dies only
    where an end reaches it; every landing farther in is the reference's,
    unmoved; every change of the deposited web is at an end; supported and
    closed throughout."""
    L, ks = _end_stack(-1)
    S = pitch(T_END)
    m = S + T_END
    ref = _landings(L[0])
    counts = []
    for i, (b, k) in enumerate(zip(L, ks)):
        xs = _landings(b)
        counts.append(len(xs))
        far = lambda v: [x for x in v if 30 - 30 * k + m < x < 30 + 90 * k - m]   # noqa: E731
        assert far(xs) == far(ref), i
        if i:
            assert all(_near_ends(p, k, m) for p in changed(L[i - 1], b)), i
    assert counts == sorted(counts, reverse=True) and counts[-1] < counts[0]
    assert findings(L) == []
    _closed(L)


def test_end_cap_expanding_grows_from_the_end():
    """TEST B. Each end moves outward: every reference landing stays; new
    landings appear only beyond the reference's, one pitch on from the
    survivors (the same comb — never the old ones spread apart); changes
    only at the ends."""
    L, ks = _end_stack(1)
    S = pitch(T_END)
    m = S + T_END
    ref = _landings(L[0])
    inner = ref[1:-1]                                   # (the end cells: the ends' own)
    for i, (b, k) in enumerate(zip(L, ks)):
        xs = _landings(b)
        assert all(any(abs(x - y) < 0.02 for y in xs) for x in inner), i
        new = [x for x in xs if x > inner[-1] + 0.02][:-1]
        steps = [b_ - a_ for a_, b_ in zip([inner[-1]] + new, new)]
        assert all(abs(d - S) < 0.05 for d in steps), (i, steps)
        if i:
            assert all(_near_ends(p, k, m) for p in changed(L[i - 1], b)), i
    assert len(_landings(L[-1])) > len(ref)
    assert findings(L) == []
    _closed(L)


# -- C: a moving junction ------------------------------------------------------

T_J = 10.0
C_J = (60, 70)


def _t_stack(sign):
    """A same-system T: rectangle 0 … 120 × 0 … 80 with a branch from its top
    wall at x = 90 up to y = 140, the whole design scaled about (60, 70)
    ½ in per layer at the branch's end (60 layers): the junction moves
    along the top wall THROUGH its web (left shrinking, right expanding)."""
    lib = library(skin_web_doc([rect('R', 0, 0, 120, 80), line('B', (90, 80), (90, 140))],
                               systems=[['R', 'B']], T=T_J))
    ks = [1 + sign * 0.5 * i / 70 for i in range(61)]
    return stack(lib, whole(ks, C_J)), ks


def _sc(p, k):
    return (C_J[0] + k * (p[0] - C_J[0]), C_J[1] + k * (p[1] - C_J[1]))


def _host_run(b):
    reg = b.network['lattice']['WEBR']['regions'][0]
    return next(t for t in reg['track']['runs'] if t['mult'] == 1 and t['partners'])


@pytest.mark.parametrize('sign', [-1, 1], ids=['shrinking', 'expanding'])
def test_moving_junction_consumes_and_exposes_locally(sign):
    """TEST C. The junction is an anchor like a corner: its hand-off faces
    never flip (stations dying / born beside it fold); every change of the
    deposited web is at the junction or at another moving transition (a
    corner, the branch's cap); the top wall's web away from them never
    moves; supported and closed throughout."""
    L, ks = _t_stack(sign)
    S = pitch(T_J)
    m = S + 2 * T_J
    choice0 = _host_run(L[0])['choice']
    # (the top wall's centre: half a PHYSICAL thickness inside its scaled outer face)
    top = lambda b, k: sorted({round(p[0], 2) for p in web_points(b)                      # noqa: E731
                               if abs(abs(p[1] - (_sc((0, 80), k)[1] - T_J / 2)) - 1.25) < 0.05})
    ref_top = top(L[0], 1.0)
    for i, (b, k) in enumerate(zip(L, ks)):
        assert _host_run(b)['choice'] == choice0, i                    # the junction never flips
        J = (_sc((90, 80), k)[0], _sc((90, 80), k)[1] - T_J / 2)
        marks = [J, _sc((90, 140), k)] + [_sc(q, k) for q in ((0, 0), (120, 0), (0, 80), (120, 80))]
        if i:
            for p in changed(L[i - 1], b):
                assert any(math.dist(p, q) <= m for q in marks), (i, p)
        # (inside this layer's AND the reference's corners: a corner motif's
        # landings move with their corner, they are not web stations)
        lo, hi = max(_sc((0, 0), k)[0], 0.0) + m, min(_sc((120, 0), k)[0], 120.0) - m
        far = lambda v: [x for x in v if lo < x < hi and abs(x - J[0]) > m]           # noqa: E731
        assert far(top(b, k)) == far(ref_top), i
    assert findings(L) == []
    _closed(L)


# -- F: an isolated circle (no transition: the seam) ---------------------------

def test_isolated_circle_changes_only_at_its_seam():
    """TEST F. A corner-less loop with no architectural transition: radial
    host motion keeps every station's angle (its cells compress / stretch
    with the circumference — §Z.9 Step 8; a cell under the pitch floor is
    reported); whatever is born / dies, does so only at the loop's one
    stable seam — the rest never rotates."""
    lib = library(skin_web_doc([circle('C', 0, 0, 60)], T=T_END))
    ks = [1 - 0.5 * i / 60 for i in range(61)]
    L = stack(lib, whole(ks, (0, 0)))
    ang = lambda b: sorted({round(math.degrees(math.atan2(p[1], p[0])), 1) for p in web_points(b)})  # noqa: E731
    ref = ang(L[0])
    changes = set()
    for i, b in enumerate(L):
        if i:
            changes |= {round(math.degrees(math.atan2(p[1], p[0]))) for p in changed(L[i - 1], b)}
    seam = sorted(changes)
    if seam:                       # every change in one arc of ≤ 80° (one place on the ring: the seam)
        spread = min(max((a - s0) % 360 for a in seam) for s0 in seam)
        assert spread <= 80, seam
    away = [a for a in ref if all(abs((a - s + 180) % 360 - 180) > 45 for s in seam)]
    assert away
    for i, b in enumerate(L):
        got = ang(b)
        # (≤ 1°: a cell is ~16°; a rotation / re-indexing would move them
        # all by cells). At most two landings travel further, gradually
        # (~0.03° per layer): a concave-rail landing's contact clearance
        # slides it along its stitch as the radius halves (§Z.3) — never a step.
        moved = [a for a in away if not any(abs(a - g) <= 1.0 for g in got)]
        assert len(moved) <= 2, (i, moved)
        if moved:
            gaps = sorted(moved)
            assert gaps[-1] - gaps[0] <= 2 * 360 / len(ref) + 1.0 or len(moved) == 1, (i, moved)
    assert findings(L) == []
    _closed(L)
