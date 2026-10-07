"""
CROSS-Z LATTICE CORRESPONDENCE (2026-10-06, stabilization): a semantically
transformed design does not rediscover its lattice topology per layer. The
planner gets the untransformed plan's TRACK (wall_lattice.plan(track=…)) and
keeps its discrete choices — passes per run, start sides / parity, stitch
count per segment, a loop's seam, each stitch's construction — changing a
count only where the old one is physically impossible (a gap beyond the
maximum unsupported distance, or a pitch under half the target).
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from layer_design import DesignLibrary
import multi_opening_fixtures as F


def about(k, c):
    return (k, c[0] * (1 - k), c[1] * (1 - k))


def lib():
    return DesignLibrary([{'id': 'b', 'name': 'Base', 'document': F.document(())}])


def runs(b):
    return [(round(r['length']), r['stitches'], r['passes'])
            for reg in b.network['lattice']['IR']['regions'] for r in reg.get('runs') or []]


def counts(b):
    return sorted((r['passes'], r['stitches']) for reg in b.network['lattice']['IR']['regions']
                  for r in reg.get('runs') or [])


def circle_lattice(b):
    return [[(q.x, q.y) for q in pl] for pl in b.lattice['IR']]


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def registered(lo, up, tol, box):
    """Every lattice vertex of `up` inside `box` lies within tol of `lo`'s lattice."""
    S = [(a, b) for pl in lo for a, b in zip(pl, pl[1:])]
    pts = [q for pl in up for q in pl if box(q)]
    return pts and max(min(_segd(q, a, b) for a, b in S) for q in pts) <= tol


def tracks(b):
    return [reg.get('track') for reg in b.network['lattice']['IR']['regions']]


def test_the_transformed_plan_is_tracked_against_the_untransformed_one():
    L = lib()
    b = L.build('b', transforms={'C': about(0.9, (480, 180))})
    (t,) = tracks(b)
    assert t['tracked_runs'] == 3 and t['replanned'] is None


def test_smooth_uniform_scaling_keeps_lattice_correspondence():
    L = lib()
    prev, seq = L.build('b'), []
    circle = lambda q: math.hypot(q[0] - 480, q[1] - 180) > 50          # the circle wall, any scale
    for j in range(1, 26):
        k = 1 - 0.012 * j                                                # → 0.70, about its centre
        b = L.build('b', transforms={'C': about(k, (480, 180))})
        seq.append(counts(b))
        # the circle moved ≤ 80·0.012 ≈ 1 in per step: its lattice follows it
        assert registered(circle_lattice(prev), circle_lattice(b), 1.2,
                          lambda q: math.hypot(q[0] - 480, q[1] - 180) > 40 and q[0] > 420), j
        prev = b
    singles = [sorted(n for p, n in c if p == 1) for c in seq]
    assert all(s == singles[0] for s in singles)                        # no stitch-count change on the loops
    # the regenerated connector (now longer) may only gain stitches, never oscillate
    dbl = [next(n for p, n in c if p == 2) for c in seq]
    assert dbl == sorted(dbl)


def test_smooth_translation_keeps_lattice_correspondence():
    L = lib()
    prev, dbl = L.build('b'), []
    for j in range(1, 21):
        dx = 1.0 * j                                                     # the circle moves right, 1 in / step
        b = L.build('b', transforms={'C': (1.0, dx, 0.0)})
        dbl.append(next(n for p, n in counts(b) if p == 2))
        assert registered(circle_lattice(prev), circle_lattice(b), 1.2,
                          lambda q: q[0] > 440 + dx), j
        assert registered(circle_lattice(prev), circle_lattice(b), 1.2, lambda q: q[0] < 330), j   # rect untouched
        prev = b
    assert dbl == sorted(dbl)                                           # connector: only genuine changes, monotone


def test_a_count_changes_only_where_the_old_one_is_impossible():
    L = lib()
    b = L.build('b', transforms={'C': (1.0, 60.0, 0.0)})                 # connector 60 → 120 in long
    (t,) = tracks(b)
    assert t['changed_segments'] >= 1                                   # the stretched connector re-counted …
    plain = counts(L.build('b'))
    assert sorted(n for p, n in counts(b) if p == 1) == sorted(n for p, n in plain if p == 1)   # … nothing else
