"""
STEP 8 — CLOSED CURVES THROUGH Z (2026-10-18).

Isolated Skin + Web circles (no junctions — Step 9). WALL_ARCHITECTURE
§Z.0.P, Q, S; §Z.1 18:
  * the circle's construction follows its HOST: a rigid translation carries
    it; a changing radius moves it RADIALLY — every landing keeps its angle
    (no circumferential drift, no rotation, no re-indexing);
  * the loop has ONE stable seam (the reference plan's, carried): repeats
    die there only when a seam cell falls under the pitch floor, and are
    born there once a new cell of the Target Web Angle's pitch fits —
    the changed landings always form ONE contiguous run at the seam;
  * no corner event at any radius; no repeat-count explosion;
  * cells the radial host motion compresses below the pitch floor away from
    the seam are REPORTED (track 'pitch_floor'), never redistributed;
  * every layer is supported by the one below and routes closed.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from cross_z import about, circle, findings, library, near, route, skin_web_doc, stack, web  # noqa: E402

T = 10.0
TOL = 0.1          # in: a landing re-found on this layer's faces (face-ring sampling ≪ a bead)


def _lib(R):
    return library(skin_web_doc([circle('C', 0, 0, R)], T=T))


def _region(b):
    return b.network['lattice']['WEBC']['regions'][0]


def _seam(b):
    x, y = _region(b)['track']['runs'][0]['seam']
    return math.atan2(y, x)


def _landings(b, cx=0.0, cy=0.0):
    """[(angle, rail radius)] of the web's landings on its two contact rails
    (W − O inside each printed skin)."""
    pts = [p for pl in web(b) for p in pl]
    skins = sorted(sum(math.hypot(q[0] - cx, q[1] - cy) for q in s_['pts']) / len(s_['pts'])
                   for s_ in b.printable if s_['id'].startswith('C.'))
    ri, ro = skins[0] + (3.0 - 0.75), skins[-1] - (3.0 - 0.75)
    out = []
    for p in pts:
        r_ = math.hypot(*p)
        for rail in (ri, ro):
            if abs(r_ - rail) < 0.05:
                out.append((math.atan2(p[1], p[0]), rail))
    return out


def _changes(ref, cur, scale):
    """Landings of `cur` not at a reference landing's angle on the same rail
    (born / moved) and reference landings `cur` no longer has (died)."""
    def same(a, b, r_):
        return abs((a - b + math.pi) % (2 * math.pi) - math.pi) * r_ <= TOL
    ri0, ro0 = min(r for _, r in ref), max(r for _, r in ref)
    ri1, ro1 = min(r for _, r in cur), max(r for _, r in cur)
    side0 = lambda r_: 0 if abs(r_ - ri0) < abs(r_ - ro0) else 1
    side1 = lambda r_: 0 if abs(r_ - ri1) < abs(r_ - ro1) else 1
    new = [(a, side1(r_)) for a, r_ in cur if not any(side0(r0) == side1(r_) and same(a, a0, r_) for a0, r0 in ref)]
    lost = [(a, side0(r0)) for a0, r0 in ref for a in (a0,)
            if not any(side0(r0) == side1(r_) and same(a, a1, r_) for a1, r_ in cur)]
    kept = [(a, side1(r_)) for a, r_ in cur if (a, side1(r_)) not in new]
    return new, lost, kept


def _contiguous_at_seam(changed, kept, seam):
    """All changed angles lie in ONE arc through the seam that holds no kept
    landing."""
    if not changed:
        return True
    rel = lambda a: (a - seam + math.pi) % (2 * math.pi) - math.pi      # (−π, π] from the seam
    lo, hi = min(rel(a) for a, _ in changed), max(rel(a) for a, _ in changed)
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    # (the seam's own station is the loop's anchor — its landings sit within
    # a fraction of a cell of the seam point: changes on both sides of it
    # are one run)
    anchor = 0.25 * 2 * math.pi / max(1, len(kept) + len(changed)) * 2
    return not any(lo + 1e-6 < rel(a) < hi - 1e-6 and abs(rel(a)) > anchor for a, _ in kept)


def _stitches(b):
    return sum(r_['stitches'] for r_ in _region(b)['runs'])


def _valid(L):
    for i, b in enumerate(L):
        ends, pieces = route(b)
        assert not ends and pieces == 1, (i, ends, pieces)
        assert all(r_['corners'] == 0 for r_ in _region(b)['runs']), i          # a circle never has corners
    assert findings(L) == []


@pytest.mark.parametrize('path', ['straight', 'curving'])
def test_rigidly_moving_circle_carries_its_web(path):
    """Rigid motion (a straight drift, or a curving one) is host motion: the
    whole web moves with the circle, nothing else."""
    lib = _lib(40)
    mv = (lambda i: (0.6 * i, 0.3 * i)) if path == 'straight' else \
        (lambda i: (4 * math.sin(i / 6.0), 0.5 * i))      # (≤ 0.85 in per layer: within Maximum Overhang)
    L = stack(lib, [{'C': (1.0, *mv(i))} for i in range(15)])
    ref = web(L[0])
    for i, b in enumerate(L):
        dx, dy = mv(i)
        got = [[(x - dx, y - dy) for x, y in pl] for pl in web(b)]
        assert all(near(p, ref, 0.05) for pl in got for p in pl), i
        assert all(near(q, got, 0.05) for pl in ref for q in pl), i
    _valid(L)


@pytest.mark.parametrize('R,step,n', [(60, -0.75, 62), (40, -0.5, 30), (24, -0.4, 20)])
def test_shrinking_circle_keeps_every_landing_at_its_angle(R, step, n):
    """Radial host motion: the surviving landings keep their angles; repeats
    die only at the seam (one contiguous run there), and only once a seam
    cell is under the pitch floor; the count only falls."""
    lib = _lib(R)
    L = stack(lib, [{'C': about((R + step * i) / R, (0, 0))} for i in range(n + 1)])
    ref = _landings(L[0])
    seam = _seam(L[0])
    for i, b in enumerate(L):
        assert abs((_seam(b) - seam + math.pi) % (2 * math.pi) - math.pi) < 1e-3, i       # one stable seam
        new, lost, kept = _changes(ref, _landings(b), (R + step * i) / R)
        # dying repeats (and a single pass's folding pair) — one run at the seam
        assert _contiguous_at_seam(lost + new, kept, seam), (i, new[:3], lost[:3])
    counts = [_stitches(b) for b in L]
    assert counts == sorted(counts, reverse=True), counts
    _valid(L)


def test_a_strongly_shrunk_circle_reports_its_compressed_cells():
    """60 → 13.5 in: radial host motion compresses every cell; those under
    the pitch floor away from the seam are reported, not redistributed."""
    lib = _lib(60)
    b = lib.build('b', transforms={'C': about(13.5 / 60, (0, 0))})
    assert (_region(b)['track'].get('pitch_floor') or 0) > 10
    assert not (_region(lib.build('b', transforms={'C': about(0.8, (0, 0))}))['track'].get('pitch_floor'))


@pytest.mark.parametrize('R,step,n', [(60, 0.75, 40), (40, 0.5, 40), (24, 0.4, 30)])
def test_expanding_circle_grows_new_repeats_only_at_its_seam(R, step, n):
    """The inverse: the surviving landings keep their angles; new landings
    appear only in one contiguous run at the seam (none moved elsewhere);
    the count only grows, by a few — never an explosion."""
    lib = _lib(R)
    L = stack(lib, [{'C': about((R + step * i) / R, (0, 0))} for i in range(n + 1)])
    ref = _landings(L[0])
    seam = _seam(L[0])
    for i, b in enumerate(L):
        new, lost, kept = _changes(ref, _landings(b), (R + step * i) / R)
        # new repeats (and a single pass's folding pair) — one run at the seam
        assert _contiguous_at_seam(new + lost, kept, seam), (i, new[:3], lost[:3])
    counts = [_stitches(b) for b in L]
    assert counts == sorted(counts), counts
    assert counts[-1] - counts[0] <= 0.25 * counts[0] + 4, counts
    _valid(L)
