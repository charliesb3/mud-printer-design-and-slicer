"""
CONTRACT — the primary Header fixture through the REAL Designer: Base, Door
Gap (derived, a 36 in opening), and "Closed", derived from Door Gap, which
closes the gap again. The stack prints the gap for many layers, then the
closing design. Its wall AND inherited lattice exist across the opening in
the design; whether they are SUPPORTED is the Assembly's call.
"""
import copy
import math
import os

from layer_assembly.demo import demo_designs
from layer_assembly.designer_source import DesignerSource
from layer_assembly.model import Assembly, Section, resolve
from layer_assembly.objects import Header, SupportOverride
from layer_assembly.support import analyse_support


def designs():
    return demo_designs() + [{'id': 'D', 'name': 'Closed', 'parent': 'B',
                              'patch': {'openings': {'door': None}}}]


def in_gap(p):
    return 185 < p[0] < 215 and 141 < p[1] < 149


def setup():
    src = DesignerSource.from_designs(designs())
    geo = {d: (src.geometry(d).polylines, src.geometry(d).bead_width) for d in 'ABD'}
    asm = Assembly(1.5, [Section('A', 12), Section('B', 24), Section('D', 12)])
    return src, geo, asm


def test_the_closing_design_carries_its_inherited_lattice_across_the_gap():
    src, geo, _ = setup()
    gap_pts = [p for pl in geo['D'][0] for p in pl['pts'] if in_gap(p)]
    base_pts = [p for pl in geo['A'][0] for p in pl['pts'] if in_gap(p)]
    assert gap_pts and sorted(map(tuple, gap_pts)) == sorted(map(tuple, base_pts))   # Base's stitches, in phase
    assert not [p for pl in geo['B'][0] for p in pl['pts'] if in_gap(p)]


def test_without_a_header_the_bridge_is_reported_with_its_lattice():
    src, geo, asm = setup()
    rep = analyse_support(resolve(asm, 'ABD'), geo, asm)
    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert f.status == 'needs_header' and f.layer == 24 and f.section == 2
    assert abs(f.angle) < 1e-6 and math.isclose(f.centre[0], 200, abs_tol=1) and math.isclose(f.centre[1], 145, abs_tol=0.5)
    assert 30 < f.span < 36 and math.isclose(f.depth, 13, abs_tol=0.5)
    assert any(in_gap(p) for r in f.runs for p in r)            # the lattice over the void is flagged, not accepted


def test_header_supports_it_removal_and_override():
    src, geo, asm = setup()
    res = resolve(asm, 'ABD')
    f = analyse_support(res, geo, asm).findings[0]
    asm.objects.append(Header('h1', f.section, f.local_layer, *f.local_centre, f.angle, f.span,
                              bearing=8, depth=13, thickness=4.5))
    rep = analyse_support(res, geo, asm)
    assert rep.findings[0].status == 'supported'
    h = rep.headers[0]
    assert math.isclose(h['length'], f.span + 16) and h['z_top'] == 36 and h['z_bottom'] == 31.5
    assert h['pocket_layers'] == [21, 22, 23]                   # 4.5 in = 3 wall layers at its ends
    asm.objects.clear()
    assert analyse_support(res, geo, asm).findings[0].status == 'needs_header'
    asm.overrides.append(SupportOverride(f.section, f.local_layer, *f.local_centre))
    assert analyse_support(res, geo, asm).findings[0].status == 'overridden'


def test_designer_geometry_unchanged_and_no_lattice_logic_in_the_assembly():
    src, geo, asm = setup()
    before = copy.deepcopy(geo)
    asm.objects.append(Header('h1', 2, 0, 200, 145, 0, 33, 8, 13, 6))
    analyse_support(resolve(asm, 'ABD'), geo, asm)
    assert geo == before
    again = DesignerSource.from_designs(designs())
    assert {d: again.geometry(d).polylines for d in 'ABD'} == {d: geo[d][0] for d in 'ABD'}
    here = os.path.join(os.path.dirname(__file__), '..')
    for fn in os.listdir(here):
        if fn.endswith('.py') and fn != 'designer_source.py':
            text = open(os.path.join(here, fn)).read()
            assert 'wall_lattice' not in text and 'import layer_design' not in text and 'from layer_design' not in text, fn


def test_multi_opening_lattice_fix_removes_the_false_giant_header_warning():
    """Designer regression (multi-opening lattice, 2026-10-06) seen from the
    Assembly: the same openings design below, and above it the same geometry
    with an extra infill declared on the circle. Before the Designer fix the
    lower layer's cut-free circle arc had NO lattice while the upper one had
    it → one false 156 in HEADER NEEDED. Fixed in the Designer (the Assembly
    thresholds are unchanged): no warning."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto', 'tests'))
    import multi_opening_fixtures as F
    src = DesignerSource.from_designs([
        {'id': 'G', 'name': 'Openings', 'document': F.document()},
        {'id': 'H', 'name': 'Openings + circle infill', 'document': F.document(infill_on=('R', 'C'))}])
    geo = {d: (src.geometry(d).polylines, src.geometry(d).bead_width) for d in 'GH'}
    asm = Assembly(1.5, [Section('G', 6), Section('H', 6)])
    assert analyse_support(resolve(asm, 'GH'), geo, asm).findings == []
