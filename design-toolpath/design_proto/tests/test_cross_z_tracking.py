"""
CROSS-Z LATTICE CORRESPONDENCE through the CONSTRUCTION STATE (stage Z4,
2026-10-17; WALL_ARCHITECTURE.md §Z.2 §4–6). A semantically transformed
design does not rediscover its lattice per layer: each layer's webs are
built on the STATE carried from the layer below by identity
(construction_state.advance over the layers' topologies — what the
Assembly's chain does at Generate) — passes per run, start sides / parity,
pinned stations, slack zones, a loop's seam — changing a count only in the
slack zone, by hysteresis, where the territory changed.

HISTORY: until 2026-10-17 these tests exercised the untransformed-reference
TRACKING (`wall_lattice.plan(track=…)`, geometric run matching against the
layer-0 plan), superseded by §Z — the Adaptive Truss alone still uses it.
The product checks (registration between consecutive layers, counts that
change only where they must) are unchanged; the mechanism under test is the
chain.
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


import pytest  # noqa: E402
import construction_state as CS

pytestmark = pytest.mark.skip(reason='class C (WALL_ARCHITECTURE §Z.0, milestone 1): these tests drive the deleted per-segment state model (segment counts, network["state"]) on a junction / circle fixture outside the stacking gate; re-expressed when junctions and closed curves are reintroduced')


def lib():
    return DesignLibrary([{'id': 'b', 'name': 'Base', 'document': F.document(())}])


class Chain:
    """A stack built layer by layer through the construction-state chain
    (the Assembly's construction_chain.run, for one design and a sequence of
    transforms)."""

    def __init__(self, L, design='b'):
        self.L, self.d = L, design
        self.state = self.topo = self.prev_state = self.prev_topo = None

    def build(self, transforms=None):
        T = self.L.topology(self.d, transforms)['topology']
        self.prev_state, self.state = self.state, CS.advance(self.state, self.topo, T)
        self.prev_topo, self.topo = self.topo, T
        return self.L.build(self.d, transforms=transforms, state=self.state)


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


def carried(b):
    """The segments of a build's identity report whose stations came from
    the construction state."""
    st = b.network.get('state') or {}
    return {sid for sid, seg in (st.get('segments') or {}).items() if seg.get('carried')}


def test_the_transformed_plan_is_carried_from_the_layer_below_by_identity():
    L = lib()
    alone = L.build('b')
    assert not carried(alone), 'a build without a state plans on its own …'
    assert alone.network['state']['segments'], '… and reports its decisions in state form'
    ch = Chain(L)
    b0 = ch.build()
    assert carried(b0) == set(b0.network['state']['segments']), 'the first layer of a chain is built on that state …'
    # … which lays the same number of stations out, measured on the WALL
    # (Pass A spaced them in the station parameter u, which compresses the
    # zones round a corner — the arc frame does not: up to a few inches of
    # difference beside corners, the same stations elsewhere)
    # (and the TRANSFER landing of a ring sits on a station counted from a
    # segment's pin, not mid-sequence: one landing per ring may go to the
    # skin instead of the contact rail — a contact width away)
    assert counts(b0) == counts(alone)
    assert registered(circle_lattice(alone), circle_lattice(b0), 3.0, lambda q: True)
    b = ch.build({'C': about(0.9, (480, 180))})
    segs = set((b.network['state'] or {}).get('segments') or {})
    assert carried(b) == segs and segs, 'every segment of the transformed layer is carried'
    t = b.network['lattice']['IR']['regions'][0]['track']
    assert t['tracked_runs'] == 0, 'the geometric reference tracking is not used (truss only)'


def test_smooth_uniform_scaling_keeps_lattice_correspondence():
    L = lib()
    ch = Chain(L)
    prev, seq = ch.build(), []
    circle = lambda q: math.hypot(q[0] - 480, q[1] - 180) > 50          # the circle wall, any scale
    for j in range(1, 26):
        k = 1 - 0.012 * j                                                # → 0.70, about its centre
        b = ch.build({'C': about(k, (480, 180))})
        seq.append(counts(b))
        # PERSISTENT COMB (Z.9): the circle shrinks ≈ 1 in of radius per step, its
        # circumference by ΔL ≈ 6 in; the web keeps its PHYSICAL pitch (no
        # wavelength scaling) and its stations keep their arc from the loop's seam,
        # so a station can move tangentially by up to ΔL / 2 beside the seam's
        # antipode — the physically required motion, nothing re-phased
        dL = 2 * math.pi * 80 * 0.012
        assert registered(circle_lattice(prev), circle_lattice(b), max(1.2, dL / 2 + 1.5),      # (+ the radial motion)
                          lambda q: math.hypot(q[0] - 480, q[1] - 180) > 40 and q[0] > 420), j
        prev = b
    singles = [sorted(n for p, n in c if p == 1) for c in seq]
    # the loops' stitch counts change only by consumption at a boundary / the seam: monotone, never oscillating
    for i_ in range(len(singles[0])):
        col = [s_[i_] for s_ in singles]
        assert all(b_ <= a_ for a_, b_ in zip(col, col[1:])) or all(b_ >= a_ for a_, b_ in zip(col, col[1:])), col
    # the regenerated connector (now longer) may only gain stitches, never oscillate
    dbl = [next(n for p, n in c if p == 2) for c in seq]
    assert dbl == sorted(dbl)


def test_smooth_translation_keeps_lattice_correspondence():
    L = lib()
    ch = Chain(L)
    prev, dbl = ch.build(), []
    for j in range(1, 21):
        dx = 1.0 * j                                                     # the circle moves right, 1 in / step
        b = ch.build({'C': (1.0, dx, 0.0)})
        dbl.append(next(n for p, n in counts(b) if p == 2))
        assert registered(circle_lattice(prev), circle_lattice(b), 1.2,
                          lambda q: q[0] > 440 + dx), j
        assert registered(circle_lattice(prev), circle_lattice(b), 1.2, lambda q: q[0] < 330), j   # rect untouched
        prev = b
    assert dbl == sorted(dbl)                                           # connector: only genuine changes, monotone


def test_a_count_changes_only_where_the_territory_grew():
    """The circle moved 60 in right in one step: the connector doubles its
    length and gains cells in its slack zone; the loops keep theirs."""
    L = lib()
    ch = Chain(L)
    plain = ch.build()
    b = ch.build({'C': (1.0, 60.0, 0.0)})                                # connector 60 → 120 in long
    before = CS.cell_counts(ch.prev_state, ch.prev_topo)
    after = CS.cell_counts(ch.state, ch.topo)
    grown = {sid for sid in after if sid in before and after[sid] > before[sid]}
    assert grown and all('L' in sid or 'J' in sid for sid in grown), grown     # … the connector's segments
    assert sorted(n for p, n in counts(b) if p == 1) == sorted(n for p, n in counts(plain) if p == 1)   # … nothing else
