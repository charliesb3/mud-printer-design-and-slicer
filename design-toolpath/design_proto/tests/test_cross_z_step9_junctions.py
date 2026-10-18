"""
STEP 9 — JUNCTIONS AND TOPOLOGY CHANGES THROUGH Z (2026-10-18).

Same-system Skin + Web walls whose connectivity changes through Z
(WALL_ARCHITECTURE §Z.0.C, K, O, U; §Z.1 6, 7). The web a layer carries is
the POOL of every station deposited below, host-moved, each on the run it
now lies on (wall_lattice §11) — never a run's / segment's / infill's
identity — so:
  * a moving junction consumes / exposes the host's web only where it
    reaches it;
  * a junction appearing or disappearing changes the construction only at
    the junction; neither wall is regenerated;
  * a domain that SPLITS (circle + rectangle pulling apart) keeps its web on
    both resulting walls — the circle keeps its phase as a new loop whose
    seam sits where the junction was; a MERGE keeps both inputs' webs;
  * every layer routes closed (one route per connected piece).
KNOWN LIMITATION (strict xfail below): the junction's OWN transition web,
the layers where it appears / vanishes (the lens of two overlapping walls),
is still rebuilt from that layer's junction geometry — its support findings
are local to the junction, never elsewhere.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from cross_z import circle, findings, library, line, near, rect, route, skin_web_doc, stack, web  # noqa: E402

T = 10.0
S = (T - 3.0 - 2 * (3.0 - 0.75)) / math.tan(math.radians(20.0))
TOL = 0.1


def _host_x(b):
    """x of the web's landings on the host line's rails (y = ±1.25)."""
    return sorted({round(p[0], 3) for pl in web(b) for p in pl if abs(abs(p[1]) - 1.25) < 0.05})


def _closed(L, pieces):
    for i, b in enumerate(L):
        ends, n = route(b)
        assert not ends and n == pieces(i), (i, ends, n)


def test_moving_junction_changes_only_the_host_web_it_reaches():
    """A same-system wall crossing the host (an X junction) slides ½ in per
    layer for 40 layers: the host's web more than a cell + two thicknesses
    from the crossing never moves."""
    lib = library(skin_web_doc([line('H', (0, 0), (160, 0)), line('B', (60, -50), (60, 50))],
                               systems=[['H', 'B']], T=T))
    L = stack(lib, [{'B': (1.0, 0.5 * i, 0.0)} for i in range(41)])
    ref = _host_x(L[0])
    for i, b in enumerate(L):
        jx = 60 + 0.5 * i
        far = lambda xs: [x for x in xs if abs(x - jx) > S + 2 * T and abs(x - 60) > S + 2 * T and 6 < x < 154]
        assert far(_host_x(b)) == far(ref), i
    _closed(L, lambda i: 1)
    assert findings(L) == []


@pytest.mark.parametrize('direction', ['appearing', 'disappearing'])
def test_t_junction_changes_only_where_it_meets(direction):
    """A branch closes onto (or withdraws from) the host ½ in per layer: the
    host's web away from the contact and the branch's web away from its foot
    (host-moved) never change; nothing else is regenerated."""
    # (a FREE branch — its end more than a wall thickness off the host in
    # the design — moved by its own transform: appearing, it closes onto the
    # host; disappearing, it starts overlapping the host and withdraws)
    doc = skin_web_doc([line('H', (0, 0), (160, 0)), line('B', (80, 20), (80, 80))], systems=[['H', 'B']], T=T)
    if direction == 'appearing':
        mv = lambda i: -0.5 * i
        n = 41
    else:
        mv = lambda i: -18.0 + 0.5 * i
        n = 37
    lib = library(doc)
    L = stack(lib, [{'B': (1.0, 0.0, mv(i))} for i in range(n)])
    host_far = lambda xs: [x for x in xs if abs(x - 80) > S + 2 * T and 6 < x < 154]
    # (the branch above its foot, short of its far cap's tip — whose end-face
    # landing jitters ±⅓ in with the skeleton, as on every layer before)
    branch = lambda b, i: [[(x, y - mv(i)) for x, y in pl if abs(x - 80) < 6 and 45 < y - mv(i) < 74]
                           for pl in web(b)]
    ref_b = branch(L[0], 0)
    for i, b in enumerate(L):
        assert host_far(_host_x(b)) == host_far(_host_x(L[0])), i
        got = branch(b, i)
        assert all(near(p, ref_b, TOL) for pl in got for p in pl), i
    for i, b in enumerate(L):
        assert not route(b)[0], i
    assert findings(L) == []


def _circle_rect(direction):
    """Same-system rectangle + circle: 'split' — overlapping, the circle
    pulls away ½ in / layer (separates at ~layer 14); 'merge' — 8 in apart,
    the circle closes in (touches at ~layer 23)."""
    if direction == 'split':
        doc = skin_web_doc([rect('R', 0, 0, 100, 80), circle('C', 120, 40, 30)], systems=[['R', 'C']], T=T)
        mv, n, cx0 = (lambda i: 0.5 * i), 41, 120.0
    else:
        doc = skin_web_doc([rect('R', 0, 0, 100, 80), circle('C', 138, 40, 30)], systems=[['R', 'C']], T=T)
        mv, n, cx0 = (lambda i: -0.5 * i), 41, 138.0
    lib = library(doc)
    return stack(lib, [{'C': (1.0, mv(i), 0.0)} for i in range(n)]), mv, cx0


def _junction_zone(i, mv, cx0):
    """Between the rectangle's right wall and the circle's near side."""
    return lambda p: 85 < p[0] < cx0 + mv(i) - 30 + 15 and 5 < p[1] < 75


@pytest.mark.parametrize('direction', ['split', 'merge'])
def test_circle_and_rectangle_keep_their_webs_through_split_and_merge(direction):
    """The previously troublesome case. Through the whole stack — joined,
    separating / meeting, apart — the rectangle's web away from the junction
    never moves; the circle's web (host-moved with it) never moves away from
    the junction: its phase survives, as a loop whose seam sits where the
    junction was; neither is ever regenerated; every layer routes closed."""
    L, mv, cx0 = _circle_rect(direction)
    ref = web(L[0])
    for i, b in enumerate(L):
        rect_far = [p for pl in web(b) for p in pl if p[0] < 85]
        # (corner-motif contact points are re-derived from the region's
        # skeleton, whose sampling shifts when the region's outline changes
        # — the circle joining / leaving: ≤ 0.2 in, ≪ a bead; no station moves)
        assert all(near(p, ref, 0.2) for p in rect_far), i
        circ = [[(x - mv(i), y) for x, y in pl] for pl in web(b)]
        circ_far = [p for pl in circ for p in pl if p[0] > cx0 - 8]
        assert all(near(p, ref, TOL) for p in circ_far), i
        assert not route(b)[0], i
    pieces = [route(b)[1] for b in L]
    assert pieces[0] == (1 if direction == 'split' else 2) and pieces[-1] == (2 if direction == 'split' else 1)


@pytest.mark.parametrize('direction', ['split', 'merge'])
def test_junction_transition_findings_stay_at_the_junction(direction):
    """Every support finding of the stack lies in the junction zone (the
    junction's own transition web): nothing elsewhere is unsupported."""
    L, mv, cx0 = _circle_rect(direction)
    F = findings(L)
    for f in F:
        z = _junction_zone(f['layer'], mv, cx0)
        assert z(f['centre']), f


@pytest.mark.xfail(strict=True, reason=(
    'KNOWN Step 9 limitation (2026-10-18): the junction\'s own transition web — the layers where '
    'two same-system walls first touch / finally part (the lens) — is rebuilt from that layer\'s '
    'junction geometry (a connector across the new contact, the lens run collapsing); a junction '
    'motif that grows from / shrinks into the construction below is not implemented. Its support '
    'findings are local to the junction (test above).'))
@pytest.mark.parametrize('direction', ['split', 'merge'])
def test_junction_transition_is_supported(direction):
    L, _, _ = _circle_rect(direction)
    assert findings(L) == []
