"""
Physical-bead diagnostic fixtures (Material / Bead physical rules: Contact
Overlap, Return-Lane Overlap, no exact retrace, closed routes). Used by
tests/test_physical.py; the same geometry is loadable in the browser with
tests/js/physical_fixtures_console.js.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from model import (Vec2, PrintLayer, LinePath, RectanglePath, RegionInfill, WallSpec,
                   NetworkWall, CirclePath)
from material import MaterialSpec
import wall_fixtures as WF


def mat(W=3.0, O=0.75, R=0.75, physical=True):
    return MaterialSpec(W, O, R, physical)


def _phys(lay, **m):
    lay.material = mat(**m)
    return lay


def wave_ring(pat='wave', **m):
    """1/2. A closed 10 in rect wall with wall infill (a circulating pass)."""
    return _phys(WF.rect_loop(pat=pat), **m)


def lone_wall(pat='zigzag', **m):
    """6. A lone straight 10 in wall with infill: one pass cannot close —
    the lattice needs a second (return) wave."""
    return _phys(WF.straight(pat=pat), **m)


def x_walls(**m):
    """3. Two 10 in walls crossing (X) with infill."""
    return _phys(WF.arms(4, pat='zigzag'), **m)


def dead_end_line(**m):
    """4/5. One freestanding single-bead line."""
    return _phys(PrintLayer('t', source_paths=[LinePath(Vec2(100, 200), Vec2(300, 200), id='L')]), **m)


def thin_x(**m):
    """3b. Two single-bead lines crossing."""
    return _phys(PrintLayer('t', source_paths=[LinePath(Vec2(100, 200), Vec2(300, 200), id='A'),
                                               LinePath(Vec2(200, 100), Vec2(200, 300), id='B')]), **m)


def star(**m):
    """7. Multi-arm wall network with infill."""
    return _phys(WF.star(), **m)


def islands(**m):
    """8. Disconnected components: a walled rect with infill + a separate line."""
    lay = WF.rect_loop()
    lay.source_paths.append(LinePath(Vec2(20, 380), Vec2(120, 380), id='island'))
    return _phys(lay, **m)


def dead_end_branch(**m):
    """Rect wall with a dead-end branch, infill (out-and-back + cap V)."""
    return _phys(WF.rect_branches(1), **m)


def rect_branch(opening=True, branch_end=(380, 200), host='rect', **m):
    """The manual regression fixture: a closed single-bead rectangle, one
    open single-bead branch from its right side, an opening on its bottom
    edge (Physical rules ON)."""
    from model import Opening
    if host == 'rect':
        H = RectanglePath(100, 140, 200, 120, id='R', label='Rect 1', closed=True)
    else:
        H = CirclePath(200, 200, 100, id='R', label='Circle 1', closed=True)
    start = Vec2(300, 200)
    B = LinePath(start, Vec2(*branch_end), id='B', label='Line 1')
    lay = PrintLayer('t', source_paths=[H, B])
    if opening:
        lay.openings = [Opening('o', 'R', 100.0, 30.0)]
    return _phys(lay, **m)
