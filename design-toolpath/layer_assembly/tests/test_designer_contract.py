"""
CONTRACT: a Designer Layer Design can be consumed by the Assembly. These few
tests exercise the real adapter (designer_source.py) end to end; the
Designer's own behaviour is covered by the Designer suites.
"""
from layer_assembly.designer_source import DesignerSource
from layer_assembly.demo import demo_designs, demo_assembly
from layer_assembly.model import resolve, Assembly, Section
from layer_assembly.preview import build_stack


def test_designs_resolve_through_the_adapter():
    s = DesignerSource.from_designs(demo_designs())
    assert s.design_ids() == ['A', 'B', 'C']
    assert s.info('B').parent == 'A' and s.info('B').name == 'Door Gap'
    g = s.geometry('A')
    assert g.polylines and all(len(p['pts']) >= 2 for p in g.polylines) and g.bead_width == 3.0


def test_assembly_over_real_designs():
    s = DesignerSource.from_designs(demo_designs())
    res = resolve(demo_assembly(1.5), s.design_ids())
    stack = build_stack(res, s)
    assert len(stack) == 72 and s.calls == 3
    a, b = s.geometry('A'), s.geometry('B')
    assert len(b.polylines) != len(a.polylines) or b.polylines != a.polylines   # the door differs


def test_base_edit_propagates_to_derived_designs_through_the_adapter():
    s = DesignerSource.from_designs(demo_designs())
    before = s.geometry('B').polylines
    base = s.lib.get('A').document
    base['source_paths'][0]['w'] = 260          # widen the Base wall
    s.lib.edit('A', document=base)
    after = s.geometry('B').polylines
    xs = lambda polys: max(x for p in polys for x, _ in p['pts'])
    assert xs(after) > xs(before) + 50            # the Door Gap design follows its Base


def test_zero_transform_assembly_matches_untransformed_geometry_and_designs_untouched():
    from layer_assembly.model import Footprint, SectionTransform
    from layer_assembly.source import bbox_of
    from layer_assembly.preview import build_stack
    src = DesignerSource.from_designs(demo_designs())
    before = {d: src.geometry(d).polylines for d in ('A', 'B')}
    a = Assembly(1.5, [Section('A', 12), Section('B', 12, '', SectionTransform('linear', (0.25, 0), 0.5))])
    fps = {d: Footprint.of_bbox(*bbox_of(before[d])) for d in before}
    st = build_stack(resolve(a, ['A', 'B'], fps), src)
    for L in st[:8]:                                          # zero-transform section: identical
        assert [list(map(tuple, p['pts'])) for p in L.polylines] == L.placed()
    assert st[12].placed() != [list(map(tuple, p['pts'])) for p in st[12].polylines]
    after = DesignerSource.from_designs(demo_designs())
    assert {d: after.geometry(d).polylines for d in before} == before     # Designer output unchanged
