"""
THIN OUT-AND-BACK PHYSICAL WIDTH (2026-10-16 correctness pass, issue 5):
measured on the generated geometry, not assumed.

A THIN open wall (no architectural thickness) under physical rules prints
two passes — the outbound lane and the return lane — W − R apart (bead width
W, return-lane overlap R), joined by U-turns. Its deposited envelope (each
lane a bead of width W) is therefore 2W − R wide, the beads overlapping by
exactly R: the "THIN · 5.25 in" the UI shows for W = 3, R = 0.75
(app.js _thinWidth, material.return_separation).
"""
import math
import os
import re

import pytest

from app import _deserialise_layer

HERE = os.path.dirname(os.path.abspath(__file__))


def _layer(W, R, start, end):
    doc = {'id': 'd', 'source_paths': [{'id': 'L', 'type': 'LinePath', 'closed': False, 'role': 'free',
                                        'visible': True, 'start': list(start), 'end': list(end)}],
           'material': {'bead_width': W, 'contact_overlap': 0.75, 'return_overlap': R, 'physical': True},
           'return_paths': True, 'prefer_closed': True}
    lay = _deserialise_layer(doc)
    return lay._build_effective()


def _cross_section(paths, P, n):
    """Signed offsets (along n) where the line through P with normal
    direction n crosses the printed polylines."""
    t = (-n[1], n[0])                       # the wall direction
    out = []
    for p in paths:
        q = [(v.x, v.y) for v in p.sample_points()]
        for a, b in zip(q, q[1:]):
            da = (a[0] - P[0]) * t[0] + (a[1] - P[1]) * t[1]
            db = (b[0] - P[0]) * t[0] + (b[1] - P[1]) * t[1]
            if da == db or da * db > 0:
                continue
            f = da / (da - db)
            X = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
            out.append((X[0] - P[0]) * n[0] + (X[1] - P[1]) * n[1])
    return sorted(out)


@pytest.mark.parametrize('W,R', [(3.0, 0.75), (2.5, 0.5), (3.5, 1.25), (3.0, 0.0), (3.0, 2.0)])
@pytest.mark.parametrize('angle', [0.0, 30.0, 90.0])
def test_two_lanes_W_minus_R_apart_deposit_2W_minus_R(W, R, angle):
    a = math.radians(angle)
    u = (math.cos(a), math.sin(a))
    n = (-u[1], u[0])
    S, E = (10.0, 20.0), (10.0 + 60 * u[0], 20.0 + 60 * u[1])
    paths, meta = _layer(W, R, S, E)
    assert meta['network']['return_lanes'] == ['L']
    for f in (0.1, 0.3, 0.5, 0.7, 0.9):                         # stations along the wall
        P = (S[0] + f * (E[0] - S[0]), S[1] + f * (E[1] - S[1]))
        ys = _cross_section(paths, P, n)
        assert len(ys) == 2, ys                                  # exactly two passes cross the section
        sep = ys[1] - ys[0]
        assert math.isclose(sep, W - R, abs_tol=1e-6)            # lane spacing = the return separation
        assert math.isclose(ys[0] + ys[1], 0.0, abs_tol=1e-6)    # centred on the drawn path
        deposited = (ys[1] + W / 2) - (ys[0] - W / 2)
        assert math.isclose(deposited, 2 * W - R, abs_tol=1e-6)  # the deposited envelope
        assert math.isclose(W - sep, R, abs_tol=1e-6)            # the two beads overlap by R
    # the passes are joined at both free ends: one closed out-and-back circuit
    caps = [p for p in paths if p.id in ('L_cs', 'L_ce')]
    assert len(caps) == 2
    for c in caps:
        q = c.sample_points()
        assert math.isclose(q[0].dist(q[-1]), W - R, abs_tol=1e-6)


def test_ui_thin_width_is_the_measured_width():
    """The UI's displayed THIN width (_thinWidth) is the same formula."""
    src = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()
    m = re.search(r'function _thinWidth\(path\) \{(.*?)\n\}', src, re.S)
    assert m and '2 * W - m.return_overlap' in m.group(1)
    paths, _ = _layer(3.0, 0.75, (0, 0), (40, 0))
    ys = _cross_section(paths, (20, 0), (0, 1))
    assert math.isclose((ys[1] - ys[0]) + 3.0, 5.25, abs_tol=1e-9)
