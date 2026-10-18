"""
The construction chain's rules in isolation (construction_state.py;
WALL_ARCHITECTURE.md §Z.0, milestone 1): DEPOSITED LANDINGS. Plain dicts
stand for the planner's topology — a straight wall along y = 0 whose two face
rails are y = ±1.25, one segment between its two cap stations — and the
landings are the state. No geometry is built.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import construction_state as CS  # noqa: E402

pytestmark = pytest.mark.fast

RAIL = 1.25
P = 7.0


def topo(x0, x1, kind='lone', margin=3.5):
    """A straight wall from x0 to x1 (its run starts at x0); one segment
    between the cap stations; Pass A stations at the even pitch nearest P."""
    L = x1 - x0
    rails = [[[s, x0 + s, side * RAIL] for s in [L * k / 200 for k in range(201)]] for side in (1, -1)]
    a, b = margin, L - margin
    n = max(1, round((b - a) / P))
    pa = [[a + (b - a) * j / n, 0] for j in range(1, n)]
    return {'runs': [{'kind': kind, 'cycle': False, 'L': L, 'face0': 1, 'wall': 'W', 'rails': rails,
                      'segs': [{'a': a, 'b': b, 'fa': None, 'fb': None, 'pa': pa}]}],
            'walls': {'W': {'pts': [[x0 + L * k / 32, 0.0] for k in range(33)], 'closed': False, 'L': L}}}


def stations(state):
    return sorted({round(ld['x'], 6) for ld in state['landings'] if not ld['e']})


def chain(steps, place=None):
    """[(x0, x1)] per layer → [state]."""
    out, prev, prev_t = [], None, None
    for k, (x0, x1) in enumerate(steps):
        t = topo(x0, x1)
        st = CS.advance(prev, prev_t, t, place(k - 1) if (place and k) else None, place(k) if place else None, 1.5)
        out.append(st)
        prev, prev_t = st, t
    return out


def test_the_first_layer_is_pass_a():
    st = chain([(0.0, 240.0)])[0]
    t = topo(0.0, 240.0)
    assert stations(st) == pytest.approx(sorted(round(x, 6) for x, _ in t['runs'][0]['segs'][0]['pa']))
    assert all(abs(abs(ld['y']) - RAIL) < 1e-9 for ld in st['landings'])


def test_one_moving_end_consumes_locally_and_nothing_else_moves():
    seq = chain([(0.0, 240.0 - 1.0 * k) for k in range(30)])
    for a, b in zip(seq, seq[1:]):
        sa, sb = stations(a), stations(b)
        assert all(x in sa for x in sb), 'every station is one of the stations below, unmoved'
        assert len(sa) - len(sb) in (0, 1)
        if len(sb) < len(sa):
            assert sa[:-1] == sb, 'the consumed station is the one at the moving (right) end'
    assert len(stations(seq[-1])) < len(stations(seq[0]))


def test_both_ends_consume_independently():
    seq = chain([(1.0 * k, 240.0 - 1.0 * k) for k in range(30)])
    s0, sn = stations(seq[0]), stations(seq[-1])
    assert sn[0] > s0[0] and sn[-1] < s0[-1], 'both ends consumed'
    assert all(x in s0 for x in sn), 'no station moved: no flow across the wall'


def test_n_to_n_minus_one_keeps_the_survivors():
    seq = chain([(0.0, 240.0 - 0.25 * k) for k in range(80)])
    deaths = 0
    for a, b in zip(seq, seq[1:]):
        sa, sb = stations(a), stations(b)
        if len(sb) < len(sa):
            deaths += 1
            assert sb == sa[:-1]
        else:
            assert sb == sa
    assert deaths >= 2


def test_expansion_exposes_new_stations_only_at_the_moving_end():
    seq = chain([(0.0, 200.0 + 1.0 * k) for k in range(40)])
    for a, b in zip(seq, seq[1:]):
        sa, sb = stations(a), stations(b)
        assert sb[:len(sa)] == sa, 'the existing stations stay; a new one appears beyond them'
        assert len(sb) - len(sa) in (0, 1)
    assert len(stations(seq[-1])) > len(stations(seq[0]))


def test_a_rigid_translation_carries_the_landings():
    """The same design translated 1 in per layer (an InstanceTransform): the
    landings, in design coordinates, are the same on every layer."""
    seq = chain([(0.0, 240.0)] * 6, place=lambda k: {'scale': 1.0, 'tx': 1.0 * k, 'ty': 0.0})
    assert all(stations(s) == stations(seq[0]) for s in seq)


def test_a_scale_keeps_world_positions_and_only_rigid_motion_is_carried():
    P0 = [(x, 0.0) for x in range(0, 100, 10)]
    assert CS.rigid_fit(P0, [(x + 3.0, 1.0) for x, y in P0]) is not None
    assert CS.rigid_fit(P0, [(0.9 * x, 0.0) for x, y in P0]) is None


def test_the_transfer_rides_one_landing():
    seq = chain([(0.0, 240.0 - 1.0 * k) for k in range(20)])
    ts = [[(round(ld['x'], 6), ld['f']) for ld in s['landings'] if ld['t']] for s in seq]
    assert all(len(t) == 1 for t in ts)
    assert all(t == ts[0] for t in ts), 'the transfer stays on its landing while it survives'
