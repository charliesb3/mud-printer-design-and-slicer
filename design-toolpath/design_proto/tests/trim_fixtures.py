"""
Trim fixtures — deterministic layers for tests/test_trim.py and for manual
inspection in the browser (tests/js/trim_fixtures_console.js builds the
same geometry in the Design Canvas).

  circle_rect   Rect 1 (100,100 240×160) + Circle 1 centred on its right
                edge (340,180 r 80): two contacts, two sections each
  strip_circle  a long thin rect crossed by a larger circle: 4 contacts,
                TWO circle sections inside the strip (the u_mid tie-break)
  line_rect     an open line passing through a rect: 3 line sections
  curve_line    a quadratic curve crossed by a straight line
  bars          two parallel lines joined by one vertical connector
                (trimming its middle disconnects the design)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from model import (Vec2, PrintLayer, RectanglePath, CirclePath, LinePath,
                   QuadBezierPath, WallSpec, Trim, RegionInfill, Opening)


def circle_rect(wall=0.0, cx=340.0, cy=180.0, r=80.0, rect=(100, 100, 240, 160), trims=()):
    R = RectanglePath(*rect, id='R', label='Rect 1', closed=True)
    C = CirclePath(cx, cy, r, id='C', label='Circle 1', closed=True)
    if wall:
        R.wall = WallSpec(wall)
        C.wall = WallSpec(wall)
    return PrintLayer('t', source_paths=[R, C], trims=list(trims))


def strip_circle(trims=(), dx=0.0):
    S = RectanglePath(40, 160, 320, 40, id='S', label='Strip', closed=True)
    C = CirclePath(200 + dx, 180, 60, id='C', label='Circle 1', closed=True)
    return PrintLayer('t', source_paths=[S, C], trims=list(trims))


def line_rect(trims=(), wall=0.0):
    R = RectanglePath(100, 100, 200, 120, id='R', label='Rect 1', closed=True)
    L = LinePath(Vec2(40, 160), Vec2(360, 160), id='L', label='Line 1')
    if wall:
        R.wall = WallSpec(wall)
        L.wall = WallSpec(wall)
    return PrintLayer('t', source_paths=[R, L], trims=list(trims))


def curve_line(trims=()):
    Q = QuadBezierPath(Vec2(60, 100), Vec2(340, 100), Vec2(200, 320), id='Q', label='Curve 1')
    L = LinePath(Vec2(40, 160), Vec2(360, 160), id='L', label='Line 1')
    return PrintLayer('t', source_paths=[Q, L], trims=list(trims))


def bars(trims=()):
    A = LinePath(Vec2(100, 100), Vec2(300, 100), id='A', label='Line A')
    B = LinePath(Vec2(100, 200), Vec2(300, 200), id='B', label='Line B')
    V = LinePath(Vec2(200, 60), Vec2(200, 240), id='V', label='Connector')
    return PrintLayer('t', source_paths=[A, B, V], trims=list(trims))


def sections(layer):
    """{section id: section dict} as the API returns them."""
    _, meta = layer._build_effective()
    return {s['id']: s for s in meta['network']['trim_sections']}


def trim_of(sec, tid='t1'):
    """The Trim the UI creates when this section is clicked."""
    return Trim(tid, sec['source'], list(sec['start']), list(sec['end']),
                dict(sec['inside']), sec['u_mid'])


def pick(layer, source, pred=lambda s: True, tid='t1'):
    """Trim of the one section of `source` satisfying pred."""
    cands = [s for s in sections(layer).values() if s['source'] == source and pred(s)]
    assert len(cands) == 1, f'{len(cands)} sections of {source} match'
    return trim_of(cands[0], tid)
