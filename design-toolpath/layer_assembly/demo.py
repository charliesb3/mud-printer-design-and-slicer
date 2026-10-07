"""
Minimal prototype: three Layer Designs (Base + two derived variations) and
an assembly given by physical height.  Run (from design-toolpath/):
    design_proto/.venv/bin/python -m layer_assembly.demo [out.svg]
"""
from __future__ import annotations

import sys

from .designer_source import DesignerSource
from .model import Assembly, Section, resolve
from .preview import build_stack, isometric_svg


def demo_designs() -> list:
    """Base: a 10 in rectangular wall (physical rules, wave wall lattice).
    Door Gap / Window Gap derive from Base and only ADD an opening."""
    base_doc = {
        'id': 'l', 'source_paths': [
            {'id': 'W', 'type': 'RectanglePath', 'label': 'Wall', 'x': 100, 'y': 140,
             'w': 200, 'h': 120, 'closed': True, 'wall': {'thickness': 10}}],
        'offset_treatments': [], 'lattice_instances': [],
        'infills': [{'id': 'I', 'path_id': 'W', 'pattern': 'wave', 'params': {'spacing': 20},
                     'kind': 'wall', 'variation_index': 0}],
        'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75,
                     'physical': True}}
    return [
        {'id': 'A', 'name': 'Base', 'document': base_doc},
        {'id': 'B', 'name': 'Door Gap', 'parent': 'A',
         'patch': {'openings': {'door': {'source_path_id': 'W', 'center_s': 100, 'width': 36}}}},
        {'id': 'C', 'name': 'Window Gap', 'parent': 'A',
         'patch': {'openings': {'window': {'source_path_id': 'W', 'center_s': 420, 'width': 30}}}},
    ]


def demo_assembly(layer_height=1.5) -> Assembly:
    return Assembly(layer_height, [Section('A', 36, 'Base'), Section('B', 24, 'Door Gap'),
                                   Section('A', 12, 'Base'), Section('C', 36, 'Window Gap')],
                    'Prototype wall')


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    src = DesignerSource.from_designs(demo_designs())
    for lh in (1.5, 2.0, 1.75):
        print(resolve(demo_assembly(lh), src.design_ids()).report())
    res = resolve(demo_assembly(1.5), src.design_ids())
    stack = build_stack(res, src)
    print(f'designs resolved once each: {src.calls} geometry builds for {len(stack)} layers')
    if argv:
        names = {i: src.info(i).name for i in src.design_ids()}
        with open(argv[0], 'w') as f:
            f.write(isometric_svg(stack, names, every=2))
        print('wrote', argv[0])


if __name__ == '__main__':
    main()
