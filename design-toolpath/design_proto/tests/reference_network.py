"""
FIRST-CLASS REGRESSION FIXTURE (2026-10-06, manual browser failures): the
designer's real wall network

    Circle 1  ∩  Rect 1  ∩  Rect 2      (overlapping closed forms, 10 in)
    Line 1    joins Rect 2 to Circle 1  (an authored connector)

every wall 10 in (its own envelope — Line 1 too, unless `line_thickness`
gives it another or 0 = a thin single-bead line) and a wall lattice (zigzag
or wave). The derived design "gaps" adds openings. Simpler fixtures kept
passing while this topology still failed; tests/test_reference_network.py and
the Layer Assembly semantic tests use it.

(2026-10-16 cleanup: the fixture is written in the per-wall form — the
Network Wall it used to inherit from no longer exists in production. Line 1's
end on Circle 1 lies EXACTLY on the circle's sampled polyline (vertex 39 of
128 — also a vertex of every finer sampling), so it is a real contact for
trims, junction records, attachment and the semantic transform, as the
editor's snapping makes it; the old 110° end lay 0.01 in off the sampled
circle and was joined only by the wall band.)
"""
import math

CX, CY, R = 380.0, 170.0, 90.0                    # Circle 1 (overlaps Rect 1's right wall)
RECT1 = (100.0, 100.0, 200.0, 140.0)              # x, y, w, h
RECT2 = (20.0, 200.0, 140.0, 120.0)               # overlaps Rect 1's lower-left corner
A_LINE = 2 * math.pi * 39 / 128                   # Line 1 ends on Circle 1 here (≈ 109.7°, a sample vertex) …
LINE = ((160.0, 300.0), (CX + R * math.cos(A_LINE), CY + R * math.sin(A_LINE)))   # … from Rect 2's right side

# named openings: (source, center_s, width)
OPENINGS = {
    'rect1_top': ('R1', 100.0, 30.0),             # Rect 1's top wall, mid-span
    'circle_far': ('C1', 120.0, 30.0),            # Circle 1, the curved wall away from the junction
    'rect2_left': ('R2', 300.0, 30.0),            # Rect 2's left wall
    'near_junction': ('R1', 160.0, 24.0),         # Rect 1's top wall near the Circle 1 junction
}


def _env(t):
    return {'thickness': t, 'align': 'auto', 'print_reference': False}


def document(pattern='zigzag', line_thickness=None, openings=(), spacing=20):
    """line_thickness: None = Line 1 is a 10 in wall like the others; a
    number = its own thickness (0 = a THIN single-bead line)."""
    x1, y1, w1, h1 = RECT1
    x2, y2, w2, h2 = RECT2
    t_line = 10 if line_thickness is None else line_thickness
    line = {'id': 'L1', 'type': 'LinePath', 'label': 'Line 1', 'start': list(LINE[0]), 'end': list(LINE[1])}
    if t_line > 0:
        line['envelope'] = _env(t_line)
    return {'id': 'ref', 'source_paths': [
        {'id': 'C1', 'type': 'CirclePath', 'label': 'Circle 1', 'cx': CX, 'cy': CY, 'radius': R, 'closed': True,
         'envelope': _env(10)},
        {'id': 'R1', 'type': 'RectanglePath', 'label': 'Rect 1', 'x': x1, 'y': y1, 'w': w1, 'h': h1, 'closed': True,
         'envelope': _env(10)},
        {'id': 'R2', 'type': 'RectanglePath', 'label': 'Rect 2', 'x': x2, 'y': y2, 'w': w2, 'h': h2, 'closed': True,
         'envelope': _env(10)},
        line],
        'infills': [{'id': 'IN', 'path_id': 'R1', 'pattern': pattern, 'params': {'spacing': spacing},
                     'kind': 'wall', 'variation_index': 0}],
        'openings': [{'id': k, 'source_path_id': OPENINGS[k][0], 'center_s': OPENINGS[k][1],
                      'width': OPENINGS[k][2]} for k in openings],
        'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}}


def library(pattern='zigzag', line_thickness=None, openings=('rect1_top',)):
    """Base + the derived design "gaps" adding `openings`."""
    from layer_design import DesignLibrary
    base = document(pattern, line_thickness)
    cuts = {k: {'source_path_id': OPENINGS[k][0], 'center_s': OPENINGS[k][1], 'width': OPENINGS[k][2]}
            for k in openings}
    return DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base},
                          {'id': 'gaps', 'name': 'gaps', 'parent': 'base', 'patch': {'openings': cuts}}])
