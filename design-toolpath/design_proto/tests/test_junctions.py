"""Smoke test for `design_proto/junctions.py` (J2 scaffold).

The `JunctionRegion` + `extract()` module was added as the agreed vocabulary
for the next junction architecture pass. The current J2 fix in
`_apply_wall_systems` does NOT consume it; the data structure is intentionally
dormant until J3+. This smoke test keeps the module from silently rotting
between now and then: it duck-types a minimal `NetworkResult` with the exact
attributes `extract()` reads (arrangement.find_node, arrangement.nodes,
emitter dict, beads list with `.bead.element` and `.chains`), builds a tiny
3-arm tee by hand, and asserts the extracted record has the right shape.

If a future refactor renames an attribute `extract()` touches, THIS test fails
loudly instead of a J3 pass starting from a silently-broken scaffold.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import junctions                                            # the module under test


class _Vec2:
    __slots__ = ('x', 'y')
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
    def dist(self, other):
        import math
        return math.hypot(self.x - other.x, self.y - other.y)


def _tee_result():
    """Build a minimal `NetworkResult`-shaped object for a 3-arm tee:

        A ──── J ──── B
                │
                │
                C
    Arms leave J towards A (-x), B (+x), C (-y). Two source ids: 'H' (A↔B)
    and 'V' (C).
    """
    J  = _Vec2(0, 0)
    PA = _Vec2(-10, 0)
    PB = _Vec2(10, 0)
    PC = _Vec2(0, -10)
    # arrangement: node 0 at J; find_node returns 0 iff the point IS J.
    arr = types.SimpleNamespace()
    arr.nodes = [J]

    def find_node(p):
        return 0 if (abs(p.x - J.x) < 1e-9 and abs(p.y - J.y) < 1e-9) else None
    arr.find_node = find_node

    # emitter: edge keys irrelevant to extract() beyond containing the node.
    emitter = {(0, 1): 'ab', (0, 2): 'bc', (0, 3): 'cd'}

    # three beads, each a single chain of two points.
    def bead(sid, chain):
        return types.SimpleNamespace(
            bead=types.SimpleNamespace(element=sid),
            chains=[chain],
        )
    beads = [
        bead('H', [J, PA]),
        bead('H', [J, PB]),
        bead('V', [J, PC]),
    ]
    return types.SimpleNamespace(arrangement=arr, emitter=emitter, beads=beads)


def test_extract_tee_returns_one_region_with_three_arms():
    res = _tee_result()
    walls = {'H': {'thickness': 10, 'align': 'auto'},
             'V': {'thickness': 10, 'align': 'auto'}}
    regions = junctions.extract(res, source_walls=walls, junction_style='round',
                                junction_radius=6)
    assert len(regions) == 1, regions
    r = regions[0]
    # Three arms were resolved: H→A, H→B, V→C
    assert r.arm_count == 3, r.arms
    assert r.kind == 'tee', r.kind
    # Source ids on the record (deduplicated, sorted)
    assert r.source_ids == ('H', 'V'), r.source_ids
    # Style resolved to 'round' because radius > 0
    assert r.style == 'round', r.style
    # Centre is the tee node (0, 0)
    assert abs(r.center[0]) < 1e-9 and abs(r.center[1]) < 1e-9, r.center
    # Key is stable and includes source ids
    assert 'H' in r.key and 'V' in r.key, r.key


def test_extract_classifies_arm_single_bead_flag():
    """`is_single_bead` reflects the source wall's thickness == 0 case."""
    res = _tee_result()
    walls = {'H': {'thickness': 10, 'align': 'auto'},
             'V': {'thickness': 0, 'align': 'auto'}}
    regions = junctions.extract(res, source_walls=walls, junction_style='miter',
                                junction_radius=0)
    assert regions, regions
    r = regions[0]
    arms_by_src = {a.source_id: a for a in r.arms}
    assert arms_by_src['H'].is_single_bead is False
    assert arms_by_src['V'].is_single_bead is True
    # miter style with radius 0 → 'miter'
    assert r.style == 'miter', r.style


def test_summary_returns_counts():
    res = _tee_result()
    walls = {'H': {'thickness': 10}, 'V': {'thickness': 0}}
    regions = junctions.extract(res, source_walls=walls, junction_style='round',
                                junction_radius=6)
    out = junctions.summary(regions)
    assert out['count'] == 1
    assert out['by_kind'] == {'tee': 1}
    # one single-bead arm (V)
    assert out['single_bead_arms'] == 1
