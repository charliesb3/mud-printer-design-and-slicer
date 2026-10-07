"""
BRIDGE vs INSUFFICIENT OVERLAP (support.py, stabilization 2026-10-06): a
region is a BRIDGE (HEADER NEEDED) when, along an unsupported run of an
upper strand, a stretch ≥ 1 bead lies over an EMPTY CORRIDOR (the line
across the strand, bead + one lower bead each side, touches no lower bead);
lower material merely near the gap (jamb caps, lattice landings) does not
make a closing opening an "overlap" problem. Pure, synthetic geometry.
"""
import pytest

from layer_assembly.model import Assembly, Section, SectionTransform, resolve
from layer_assembly.support import analyse_support

W = 3.0


def wall(x0=0.0, x1=200.0, gap=None, lattice=False):
    """Two faces (y 0 / 10) from x0 to x1; optionally a gap (x a…b) closed by
    end caps, and a zigzag lattice landing right beside the jambs."""
    if gap is None:
        polys = [{'pts': [[x0, 0], [x1, 0]], 'closed': False}, {'pts': [[x0, 10], [x1, 10]], 'closed': False}]
    else:
        a, b = gap
        polys = [{'pts': [[x0, 0], [a, 0], [a, 10], [x0, 10]], 'closed': False},
                 {'pts': [[x1, 0], [b, 0], [b, 10], [x1, 10]], 'closed': False}]
        if lattice:
            polys += [{'pts': [[a - 20, 1.5], [a - 2, 8.5]], 'closed': False},
                      {'pts': [[b + 2, 1.5], [b + 20, 8.5]], 'closed': False}]
    return polys


def kinds(lower, upper, o=1.5, shift=None):
    if shift is None:
        a = Assembly(1.5, [Section('L', 1.5), Section('U', 1.5)], max_overhang=W - o)
        geo = {'L': (lower, W), 'U': (upper, W)}
    else:
        a = Assembly(1.5, [Section('U', 3.0, '', SectionTransform('linear', (0, shift), 0))], max_overhang=W - o)
        geo = {'U': (upper, W)}
    return sorted({(f.kind, f.status) for f in analyse_support(resolve(a), geo, a).findings})


@pytest.mark.parametrize('width', [10, 12, 20])
def test_a_small_opening_closed_by_base_needs_a_header(width):
    c = 100
    assert kinds(wall(gap=(c - width / 2, c + width / 2), lattice=True), wall()) == [('bridge', 'needs_header')]


def test_a_wide_opening_closed_by_base_needs_a_header():
    assert kinds(wall(gap=(82, 118), lattice=True), wall()) == [('bridge', 'needs_header')]


@pytest.mark.parametrize('shift', [2.0, 2.9, 3.6])
def test_a_lean_with_material_below_is_insufficient_overlap(shift):
    # 2.0: partial overlap 1.0 · 2.9: 0.1 · 3.6: no contact, but the lower bead runs right beside it
    assert kinds(None, wall(), shift=shift) == [('overlap', 'insufficient_support')]


def test_an_actually_disconnected_bridge_needs_a_header():
    far = [{'pts': [[0, 300], [200, 300]], 'closed': False}]
    island = [{'pts': [[0, 300], [200, 300]], 'closed': False}, {'pts': [[50, 0], [150, 0]], 'closed': False}]
    assert kinds(far, island) == [('bridge', 'needs_header')]
