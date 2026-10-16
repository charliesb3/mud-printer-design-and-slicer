"""
WALL THICKNESS / THIN and WALL SYSTEM MEMBERSHIP are independent (2026-10-16
correctness pass, issue 4). Through the real Designer.

A line attached to a Skin + Web rectangle. Its THICKNESS (THIN or a number)
is the wall's architecture; its MEMBERSHIP (the Wall Systems checklist) is
its construction. A thick line in NO Wall System prints Hollow — its own
record (walls.compose 'wall:<id>'). Old behaviour: the Skin + Web web filled
the whole connected material region, so as soon as the non-member line had a
thickness the rectangle's web ran into it — the line printed as if it were a
member. Now the web is generated on its own walls' envelope (the line's mouth
closed off); ticking the line as a member gives it the web.
"""
import copy
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import pytest

LINE_X = 50.0


def project(line_thickness=None, member=False):
    line = {'id': 'L', 'type': 'LinePath', 'label': 'Line', 'closed': False, 'role': 'free', 'visible': True,
            'start': [LINE_X, 0.0], 'end': [LINE_X, 60.0]}
    if line_thickness:
        line['envelope'] = {'align': 'center', 'print_reference': False, 'thickness': line_thickness}
    doc = {'id': 'design', 'label': '',
           'source_paths': [
               {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect', 'closed': True, 'role': 'free',
                'visible': True, 'x': 0, 'y': 0, 'w': 100, 'h': 100,
                'envelope': {'align': 'center', 'print_reference': False, 'thickness': 8}}, line],
           'openings': [], 'trims': [], 'route_origins': [], 'region_overrides': [], 'infills': [],
           'constraints': {'start_path_id': None, 'start_t': None, 'reverse_direction': False,
                           'component_order': None},
           'corner_radius': 0, 'cap_style': 'flat', 'cap_corner_radius': 0, 'junction_style': 'miter',
           'junction_radius': 2, 'junction_overrides': [], 'wall_relations': [],
           'material': {'bead_width': 3, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True},
           'return_paths': True, 'prefer_closed': True}
    systems = [{'id': 'S1', 'name': 'Wall System 1', 'type': 'skin_web', 'params': {},
                'members': ['R'] + (['L'] if member else []),
                'web': {'pattern': 'zigzag', 'params': {'spacing': 20}, 'variation_index': 0}}]
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}], systems


def build(**kw):
    from layer_design import DesignLibrary
    from app import _deserialise_layer
    designs, systems = project(**kw)
    lib = DesignLibrary(designs, systems)
    lay = _deserialise_layer(lib.document('base'))
    paths, meta = lay._build_effective()
    return lib, lay, paths, meta


def _web_in_line(meta_paths):
    """Web (Skin + Web infill) vertices inside the line's band, clear of the
    rectangle's own band and of the line's mouth."""
    return [q for p in meta_paths if getattr(p, 'treatment_id', '') == 'infill'
            for q in p.sample_points() if abs(q.x - LINE_X) < 2.5 and 12 < q.y < 56]


@pytest.mark.parametrize('thickness', [5.25, 6.0, 8.0])
def test_a_thick_wall_in_no_wall_system_gets_no_web(thickness):
    from layer_design import DesignLibrary
    lib, lay, paths, meta = build(line_thickness=thickness)
    recs = {r['id']: (r['type'], r['members']) for r in lib.document('base')['wall_systems']}
    assert recs == {'S1': ('skin_web', ['R']), 'wall:L': ('hollow', ['L'])}
    assert not _web_in_line(paths)                                    # the web stays in its own walls
    assert any(getattr(p, 'treatment_id', '') == 'infill' for p in paths)  # (the rectangle keeps its web)
    assert DesignLibrary._open_ends(lay, paths, meta) == []            # still one closed route


def test_ticking_the_wall_gives_it_the_web():
    from layer_design import DesignLibrary
    lib, lay, paths, meta = build(line_thickness=6.0, member=True)
    assert _web_in_line(paths)
    assert DesignLibrary._open_ends(lay, paths, meta) == []


def test_a_thin_wall_is_unaffected_by_membership():
    """THIN (no thickness): one bead out and back whatever the checklist says."""
    a = build()[2]
    b = build(member=True)[2]
    pts = lambda ps: sorted((round(q.x, 6), round(q.y, 6)) for p in ps if p.source_id == 'L' for q in p.sample_points())
    assert pts(a) == pts(b) and pts(a)
    assert not _web_in_line(a) and not _web_in_line(b)
