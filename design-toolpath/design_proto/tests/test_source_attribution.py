"""
JUNCTION SEAM (2026-10-06): which wall owns each printed vertex of the
lattice / junction geometry (source_attribution.py) — the wall minimising
distance to its material centre line / half thickness: the MITER division
at a corner; the host keeps its full thickness at a T. Never transform-
centre distance. Used by Assembly transform groups (opaque 'src' tags).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from layer_design import DesignLibrary
from source_attribution import attribute

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


def doc(paths, infill_on):
    return {'id': 'l', 'source_paths': paths, 'offset_treatments': [], 'lattice_instances': [],
            'infills': [{'id': 'I', 'path_id': infill_on, 'pattern': 'zigzag', 'params': {'spacing': 20},
                         'kind': 'wall', 'variation_index': 0}], 'material': MAT}


def line(i, a, b):
    return {'id': i, 'type': 'LinePath', 'label': i, 'start': list(a), 'end': list(b), 'wall': {'thickness': 10}}


def lattice_tags(d):
    b = DesignLibrary([{'id': 'd', 'name': 'd', 'document': d}]).build('d')
    tags, _ = attribute(b.document, b.printable, b.strand_sources)
    out = [((x, y), t) for pl, tg in zip(b.printable, tags) if pl['id'] not in b.strand_sources
           for (x, y), t in zip(pl['pts'], tg)]
    assert len(out) > 20
    return out


def test_a_two_wall_corner_splits_along_the_miter():
    # A: horizontal arm to the left of the corner (100,100); B: vertical arm up.
    # Centred 10 in walls: inner corner (95,105), outer (105,95) → miter x + y = 200
    vs = lattice_tags(doc([line('A', (0, 100), (100, 100)), line('B', (100, 100), (100, 200))], 'A'))
    for (x, y), t in vs:
        s = x + y - 200
        if abs(s) > 1e-6:
            assert t == ('A' if s < 0 else 'B'), ((x, y), t)


def test_at_a_T_the_host_keeps_its_full_thickness():
    # host H along y = 100 (faces 95 / 105), branch B up from (100,100)
    vs = lattice_tags(doc([line('H', (0, 100), (200, 100)), line('B', (100, 100), (100, 200))], 'H'))
    assert all(t == 'H' for (x, y), t in vs if y < 105 - 1e-6)
    assert all(t == 'B' for (x, y), t in vs if y > 105 + 1e-6)


def test_attribution_is_deterministic():
    d = doc([line('A', (0, 100), (100, 100)), line('B', (100, 100), (100, 200))], 'A')
    assert lattice_tags(d) == lattice_tags(d)
