"""The CURRENT production lattice, called exactly as the Designer calls it
for a wall region with physical rules on (model.py → wall_lattice.plan)."""
from __future__ import annotations

from common import WL, BEAD, CONTACT, SPACING


def plan(rings, pattern='zigzag', spacing=SPACING, contact=CONTACT, bead=BEAD, closed=True):
    lp = WL.plan(rings, spacing, pattern, 0, True, None, contact=contact, closed=closed, bead=bead)
    if lp is None:
        return [], {'fallback': 'field (not wall-like at this spacing)'}
    return [list(p) for p in lp.polylines], lp.report
