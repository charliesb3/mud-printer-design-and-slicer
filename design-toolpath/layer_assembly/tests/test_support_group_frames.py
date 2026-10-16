"""
HEADERS / SUPPORT FINDINGS FOLLOW A TRANSFORM GROUP (2026-10-16 correctness
pass, issue 1). Pure: synthetic geometry, no Designer.

A grouped layer's geometry is its SEMANTIC variant — already moved by its
groups — placed by the layer's own transform; a finding / header stores its
position in the LOCAL frame of the wall it belongs to (its group's
placement). `support.group_at` used to compare that local point with the
variant's (moved) vertices: two different frames. Once a group had moved its
wall farther than an unassigned wall lay from the old position, the header
was framed by the unassigned wall's placement (the layer's own transform) and
drawn where the grouped wall USED to be — the opening below stayed
unsupported and "Add Header" seemed to place it in the wrong place.
"""
import math

from layer_assembly.model import (Assembly, InstanceTransform, LayerInstance, ResolvedAssembly,
                                  SectionResult)
from layer_assembly.groups import GroupModel
from layer_assembly.objects import Header
from layer_assembly.support import analyse_support

BEAD = 3.0
LIFT = 45.0          # how far group c1 has moved wall A (design units) on the upper layer


def _line(pts, src):
    return {'pts': [list(p) for p in pts], 'closed': False, 'src': [src] * len(pts)}


def _stack(header=None):
    """Layer 0 prints wall A with a 20 in DOOR GAP (x 40…60) and the
    unassigned wall B; layer 1 closes the gap. Wall A is in transform group
    c1, which has moved it up by LIFT on BOTH layers (relative placement:
    identical, so the stack itself is vertical); B stays at y = 30 — closer
    to A's local position (y = 0) than A's variant geometry (y = 45)."""
    whole = InstanceTransform(1.0, 0.0, 0.0)
    part = InstanceTransform(1.0, 0.0, LIFT)
    sem = (('A', 1.0, 0.0, LIFT),)
    lo = [_line([(0, LIFT), (40, LIFT)], 'A'), _line([(60, LIFT), (100, LIFT)], 'A'),
          _line([(0, 30), (100, 30)], 'B')]
    up = [_line([(0, LIFT), (100, LIFT)], 'A'), _line([(0, 30), (100, 30)], 'B')]
    i0 = LayerInstance(0, 'door', 0, 0.0, 1.5, whole, {'c1': part}, sem)
    i1 = LayerInstance(1, 'top', 1, 1.5, 3.0, whole, {'c1': part}, sem)
    res = ResolvedAssembly(1.5, [SectionResult(0, 'door', '', 1.5, 1, 1.5, 0, 0.0, 1.5),
                                 SectionResult(1, 'top', '', 1.5, 1, 1.5, 1, 1.5, 3.0)], [i0, i1], [])
    canon = lambda pls: [dict(p, pts=[[x, y - LIFT if s == 'A' else y] for (x, y), s in zip(p['pts'], p['src'])])
                         for p in pls]                              # (the designs as drawn: A at y = 0)
    geo = {i0.geometry_key: (lo, BEAD), i1.geometry_key: (up, BEAD),
           'door': (canon(lo), BEAD), 'top': (canon(up), BEAD)}
    grp = GroupModel(group_of={'A': 'c1'}, footprints={}, auto={}, vertex_groups={}, by_tag=True)
    asm = Assembly(1.5, [], objects=[header] if header else [], center_mode='multiple', max_overhang=1.0)
    return res, geo, asm, grp


def _bridge(rep):
    fs = [f for f in rep.findings if f.kind == 'bridge']
    assert len(fs) == 1, [(f.kind, f.centre) for f in rep.findings]
    return fs[0]


def test_finding_is_reported_in_the_wall_frame():
    res, geo, asm, grp = _stack()
    f = _bridge(analyse_support(res, geo, asm, grp))
    assert math.dist(f.centre, (50, LIFT)) < 1.0           # world: over the moved door gap
    assert math.dist(f.local_centre, (50, 0)) < 1.0        # local: wall A's own frame


def test_header_from_the_finding_supports_the_moved_span():
    """Add Header (the UI stores the finding's local centre, snap on)."""
    res, geo, asm, grp = _stack()
    f = _bridge(analyse_support(res, geo, asm, grp))
    h = Header('h1', 1, 0, f.local_centre[0], f.local_centre[1], f.angle, f.span, 8.0, 6.0, 6.0, snap=True)
    res, geo, asm, grp = _stack(h)
    rep = analyse_support(res, geo, asm, grp)
    f = _bridge(rep)
    hd = next(x for x in rep.headers if x['id'] == 'h1')
    assert not hd['error']
    assert math.dist(hd['centre'], (50, LIFT)) < 1.0, hd['centre']    # NOT (50, 0): where A used to be
    assert f.status == 'supported' and f.header == 'h1'


def test_unsnapped_header_is_framed_by_its_own_wall():
    res, geo, asm, grp = _stack(Header('h1', 1, 0, 50.0, 0.0, 0.0, 20.0, 8.0, 6.0, 6.0, snap=False))
    rep = analyse_support(res, geo, asm, grp)
    hd = next(x for x in rep.headers if x['id'] == 'h1')
    assert math.dist(hd['centre'], (50, LIFT)) < 1e-9
    assert _bridge(rep).status == 'supported'
