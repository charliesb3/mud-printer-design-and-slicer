"""
Wall-lattice regression fixtures (wall infill = stitching between faces).

Each fixture returns a PrintLayer whose walls carry one zigzag wall infill
at target spacing `sp`. Used by tests/test_wall_lattice.py and for the
before / after metrics of the motif generator.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from model import (Vec2, PrintLayer, RectanglePath, LinePath, QuadBezierPath,
                   RegionInfill, WallSpec, NetworkWall, Opening, ExplicitPath)


def _inf(pid, sp, pat='zigzag'):
    return [RegionInfill('I', pid, pat, {'spacing': sp})]


def straight(sp=20, t=10, pat='zigzag'):
    L = LinePath(Vec2(40, 200), Vec2(320, 200), id='W')
    L.wall = WallSpec(t)
    return PrintLayer('t', source_paths=[L], infills=_inf('W', sp, pat))


def curved(sp=20, t=10, pat='zigzag'):
    C = QuadBezierPath(Vec2(40, 120), Vec2(200, 380), Vec2(360, 120), id='W')
    C.wall = WallSpec(t)
    return PrintLayer('t', source_paths=[C], infills=_inf('W', sp, pat))


def rect_branches(n=1, sp=20, t=10, pat='zigzag'):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(t)
    srcs = [R, LinePath(Vec2(200, 260), Vec2(200, 340), id='B1')]
    if n >= 2:
        srcs.append(LinePath(Vec2(300, 200), Vec2(380, 200), id='B2'))
    return PrintLayer('t', source_paths=srcs, network_walls=[NetworkWall('W', 'B1', t)],
                      infills=_inf('R', sp, pat))


def arms(n=4, length=110, sp=20, t=10, lengths=None, rot=0.0, pat='zigzag'):
    c = Vec2(200, 200)
    srcs = []
    for k in range(n):
        a = rot + 2 * math.pi * k / n
        Lk = lengths[k] if lengths else length
        srcs.append(LinePath(c, Vec2(c.x + Lk * math.cos(a), c.y + Lk * math.sin(a)), id=f'A{k + 1}'))
    return PrintLayer('t', source_paths=srcs, network_walls=[NetworkWall('W', 'A1', t)],
                      infills=_inf('A1', sp, pat))


def curved_arms(n=4, length=120, bend=0.45, sp=20, t=10, pat='zigzag'):
    """Pinwheel of curved dead-end arms meeting at one centre point."""
    c = Vec2(200, 200)
    srcs = []
    for k in range(n):
        a = 2 * math.pi * k / n
        end = Vec2(c.x + length * math.cos(a), c.y + length * math.sin(a))
        mid = Vec2(c.x + 0.55 * length * math.cos(a + bend), c.y + 0.55 * length * math.sin(a + bend))
        srcs.append(QuadBezierPath(c, mid, end, id=f'A{k + 1}'))
    return PrintLayer('t', source_paths=srcs, network_walls=[NetworkWall('W', 'A1', t)],
                      infills=_inf('A1', sp, pat))


def star(sp=16, t=10, pat='zigzag'):
    return arms(6, 120, sp, t, rot=0.2, pat=pat)


def rect_loop(sp=20, t=10, pat='zigzag'):
    R = RectanglePath(100, 140, 200, 120, id='R')
    R.wall = WallSpec(t)
    return PrintLayer('t', source_paths=[R], infills=_inf('R', sp, pat))


def openings(sp=20, t=10, pat='zigzag'):
    L = rect_loop(sp, t, pat)
    L.openings = [Opening('o1', 'R', 100.0, 24.0), Opening('o2', 'R', 400.0, 30.0)]
    return L


def narrow(sp=20, pat='zigzag'):
    return rect_branches(1, sp, 4, pat)


def unequal(sp=20, t=10, pat='zigzag'):
    return arms(4, sp=sp, t=t, lengths=[60, 90, 125, 160], rot=0.3, pat=pat)


def rounded_rect_loop(sp=20, t=10, r=20, pat='zigzag'):
    R = RectanglePath(100, 140, 200, 120, id='R', corner_radius=r)
    R.wall = WallSpec(t)
    return PrintLayer('t', source_paths=[R], infills=_inf('R', sp, pat))


def one_void(sp=16, pat='zigzag'):
    # a 10 in wall region between an outer and an inner single-bead boundary
    return PrintLayer('t', source_paths=[RectanglePath(0, 0, 200, 120, id='O'),
                                         RectanglePath(10, 10, 180, 100, id='A')],
                      infills=_inf('O', sp, pat))


def two_voids(sp=16, pat='zigzag'):
    return PrintLayer('t', source_paths=[RectanglePath(0, 0, 230, 110, id='O'),
                                         RectanglePath(10, 10, 100, 90, id='A'),
                                         RectanglePath(120, 10, 100, 90, id='B')],
                      infills=_inf('O', sp, pat))


def polygon_wall(n=3, radius=110, sp=20, t=10, pat='zigzag'):
    # triangle: acute 60° corners; hexagon: obtuse 120° corners
    pts = [Vec2(200 + radius * math.cos(2 * math.pi * k / n + 0.3),
                200 + radius * math.sin(2 * math.pi * k / n + 0.3)) for k in range(n)]
    P = ExplicitPath(pts, closed=True, id='P')
    P.wall = WallSpec(t)
    return PrintLayer('t', source_paths=[P], infills=_inf('P', sp, pat))


def curve_into_corner(sp=20, t=10, pat='zigzag'):
    # a curved wall that turns sharply into a straight wall (a corner where
    # two sources meet end to end) and continues to a dead end
    C = QuadBezierPath(Vec2(60, 120), Vec2(260, 200), Vec2(120, 260), id='C')
    L = LinePath(Vec2(260, 200), Vec2(320, 80), id='L')
    return PrintLayer('t', source_paths=[C, L], network_walls=[NetworkWall('W', 'C', t)],
                      infills=_inf('C', sp, pat))


CORNER_FIXTURES = {
    'rect wall': lambda sp=20: rect_loop(sp),
    'rounded rect wall': lambda sp=20: rounded_rect_loop(sp),
    'rect with one void': lambda sp=16: one_void(sp),
    'rect with two voids': lambda sp=16: two_voids(sp),
    'acute (triangle) wall': lambda sp=20: polygon_wall(3, sp=sp),
    'obtuse (hexagon) wall': lambda sp=20: polygon_wall(6, sp=sp),
    'curve into corner': lambda sp=20: curve_into_corner(sp),
}


FIXTURES = {
    '01 straight wall': straight,
    '02 curved wall': curved,
    '03 one dead-end arm': lambda sp=20: rect_branches(1, sp),
    '04 two dead-end arms': lambda sp=20: rect_branches(2, sp),
    '05 four-arm junction': lambda sp=20: arms(4, sp=sp),
    '06 four curved arms': lambda sp=20: curved_arms(4, sp=sp),
    '07 six-arm star': lambda sp=16: star(sp),
    '08 closed rect loop': rect_loop,
    '09 wall with openings': openings,
    '10 narrow wall': narrow,
    '11 unequal branches': unequal,
}
