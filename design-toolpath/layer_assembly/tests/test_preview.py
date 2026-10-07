"""Preview data (future 3D view): Z + resolved 2D geometry per layer, each
design fetched once. Fake source only."""
from layer_assembly.model import Assembly, Section, resolve
from layer_assembly.preview import build_stack, isometric_svg
from layer_assembly.source import StaticSource, DesignInfo, LayerGeometry


def src():
    sq = lambda o: {'id': 's', 'pts': [[o, 0], [o + 10, 0], [o + 10, 10], [o, 10]], 'closed': True}
    return StaticSource({k: (DesignInfo(k, k.upper(), None if k == 'a' else 'a'),
                             LayerGeometry(k, [sq(i * 20)])) for i, k in enumerate('abc')})


def test_stack_consumes_instances_and_geometry_once_per_design():
    s = src()
    res = resolve(Assembly(1.5, [Section('a', 36), Section('b', 24), Section('a', 12), Section('c', 36)]))
    stack = build_stack(res, s)
    assert len(stack) == 72 and s.calls == 3
    assert stack[0].polylines is stack[45].polylines          # Base again (layers 40–47): shared
    assert [L.z_top for L in stack[:3]] == [1.5, 3.0, 4.5]
    svg = isometric_svg(stack, {k: s.info(k).name for k in s.design_ids()})
    assert svg.startswith('<svg') and svg.count('<polyline') == 72 and 'B' in svg
