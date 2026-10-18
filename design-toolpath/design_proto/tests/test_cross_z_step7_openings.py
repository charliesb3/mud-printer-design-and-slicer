"""
STEP 7 — SHORT TERRITORIES AND OPENINGS THROUGH Z (2026-10-18).

Assembly openings (per-layer cut ops, with the opening's lattice STATION on
every layer as the Assembly sends it) on straight Skin + Web walls. A cut
layer prints the UNCUT layer's web — the construction below — clipped at
the jambs and joined locally there (DesignLibrary._cut_join,
model._join_cut_ends): WALL_ARCHITECTURE §Z.0.C, R, §Z.1 5.
  * a shrinking pier keeps a descendant of the web below — fewer surviving
    branches, then the last brace — never a fresh miniature pattern;
  * a growing pier / closing opening grows the SAME web back (no phase
    restart);
  * a moving jamb changes only what it reaches;
  * every pier of an opening array keeps the web that occupied it below;
  * every layer is supported by the one below (bead level, Maximum
    Overhang) and every wall piece routes closed.
No circles (Step 8) or junctions (Step 9) here.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from cross_z import findings, library, line, near, rect, route, skin_web_doc, web  # noqa: E402

T = 10.0


def _ops(src, edge, openings, stations):
    c = [{'op': 'station', 'source': src, 'edge': edge, 'at': a, 'profile': 'p'} for a in stations]
    c += [{'op': 'opening', 'source': src, 'edge': edge, 'at': a, 'width': w, 'profile': 'p'}
          for a, w in openings if w > 0]
    return c


def _line_stack(seq, stations):
    """seq: per layer [(centre, width)] on the 120 in line L."""
    lib = library(skin_web_doc([line('L', (0, 0), (120, 0))], T=T))
    base = lib.build('b', cuts=_ops('L', None, [], stations))        # the stack's uncut layer
    return base, [lib.build('b', cuts=_ops('L', None, ops, stations)) for ops in seq]


def _jambs(ops):
    return [c + s * 0.5 * w for c, w in ops if w > 0 for s in (-1, 1)]


def _check(base, L, seq, pieces, margin=T):
    """Hard validity + persistence: closed pieces, support, and every web
    point farther than `margin` from a jamb on the uncut layer's web."""
    ref = web(base)
    for i, (b, ops) in enumerate(zip(L, seq)):
        ends, n = route(b)
        assert not ends and n == pieces(ops), (i, ends, n)
        jam = _jambs(ops)
        bad = [p for pl in web(b) for p in pl if all(abs(p[0] - j) > margin for j in jam) and not near(p, ref, 0.05)]
        assert not bad, (i, bad[:4])
    assert findings([base] + L) == []


def _pier_web(b, lo, hi):
    return [p for pl in web(b) for p in pl if lo < p[0] < hi]


def test_shrinking_pier_descends_from_the_web_below():
    """Two openings (centres 35 / 85) widen 2 in per layer: the 40 in pier
    between them shrinks to 4 in. Its web stays the web below, clipped —
    fewer branches, then the last brace (the reference's central diamond)."""
    seq = [[(35, 10 + 2 * i), (85, 10 + 2 * i)] for i in range(19)]
    base, L = _line_stack(seq, [35, 85])
    _check(base, L, seq, lambda ops: 3)
    sizes = [len(_pier_web(b, 40 + i, 80 - i)) for i, b in enumerate(L)]
    assert sizes[-1] > 0                                    # a final supported brace survives
    # the final brace lies on / within one bead of the web below it (ancestry)
    last = _pier_web(L[-1], 55, 65)
    assert last and all(near(p, web(L[-2]), 1.0) for p in last)


def test_growing_pier_branches_back_out_of_the_surviving_brace():
    """The inverse: the pier grows from 4 in back to 40 in — the same web
    returns (every point away from the jambs on the uncut layer's web: no
    phase restart)."""
    seq = [[(35, 46 - 2 * i), (85, 46 - 2 * i)] for i in range(19)]
    base, L = _line_stack(seq, [35, 85])
    _check(base, L, seq, lambda ops: 3)


def test_widening_opening_changes_only_what_its_jambs_reach():
    seq = [[(60, 8 + 1.0 * i)] for i in range(25)]
    base, L = _line_stack(seq, [60])
    _check(base, L, seq, lambda ops: 2)


def test_moving_opening_consumes_at_one_jamb_and_exposes_at_the_other():
    """A 12 in opening slides ½ in per layer (centre 40 → 70): its leading
    jamb consumes, its trailing jamb exposes the same web again."""
    seq = [[(40 + 0.5 * i, 12)] for i in range(61)]
    base, L = _line_stack(seq, [40 + 0.5 * i for i in range(61)][::10])
    _check(base, L, seq, lambda ops: 2)


def test_opening_array_on_a_rectangle_edge_keeps_each_piers_web():
    """Three openings on the bottom edge of a single-pass rectangle widen
    ½ in per layer: each pier keeps the web that occupied it below; the
    rest of the ring never moves."""
    lib = library(skin_web_doc([rect('R', 0, 0, 160, 80)], T=T))
    cs = [40, 80, 120]
    seq = [[(c, 8 + 0.5 * i) for c in cs] if i >= 2 else [] for i in range(26)]
    L = [lib.build('b', cuts=_ops('R', 0, ops, cs)) for ops in seq]
    base = L[0]
    ref = web(base)
    for i, (b, ops) in enumerate(zip(L, seq)):
        ends, n = route(b)
        assert not ends and n == (3 if ops else 1), (i, ends, n)
        jam = _jambs(ops)
        bad = [p for pl in web(b) for p in pl
               if (p[1] > 20 or all(abs(p[0] - j) > T for j in jam)) and not near(p, ref, 0.05)]
        assert not bad, (i, bad[:4])
    assert findings(L) == []
