"""
FIRST-CLASS REGRESSION FIXTURE (2026-10-06, manual browser failures): the
designer's real wall network

    Circle 1  ∩  Rect 1  ∩  Rect 2      (overlapping closed forms, 10 in)
    Line 1    joins Rect 2 to Circle 1  (an authored connector)

with ONE network wall (Network Wall Thickness 10 in on the network, so Line 1
INHERITS 10 in unless it overrides) and a wall lattice (zigzag or wave). The
derived design "gaps" adds openings. Simpler fixtures kept passing while this
topology still failed; tests/test_reference_network.py and the Layer
Assembly semantic tests use it.
"""
import math

CX, CY, R = 380.0, 170.0, 90.0                    # Circle 1 (overlaps Rect 1's right wall)
RECT1 = (100.0, 100.0, 200.0, 140.0)              # x, y, w, h
RECT2 = (20.0, 200.0, 140.0, 120.0)               # overlaps Rect 1's lower-left corner
A_LINE = math.radians(110)                        # Line 1 ends on Circle 1 here …
LINE = ((160.0, 300.0), (CX + R * math.cos(A_LINE), CY + R * math.sin(A_LINE)))   # … from Rect 2's right side

# named openings: (source, center_s, width)
OPENINGS = {
    'rect1_top': ('R1', 100.0, 30.0),             # Rect 1's top wall, mid-span
    'circle_far': ('C1', 120.0, 30.0),            # Circle 1, the curved wall away from the junction
    'rect2_left': ('R2', 300.0, 30.0),            # Rect 2's left wall
    'near_junction': ('R1', 160.0, 24.0),         # Rect 1's top wall near the Circle 1 junction
}


def document(pattern='zigzag', line_thickness=None, openings=(), spacing=20):
    """line_thickness: None = Line 1 inherits the network wall; a number =
    its own override (0 = SINGLE BEAD)."""
    x1, y1, w1, h1 = RECT1
    x2, y2, w2, h2 = RECT2
    line = {'id': 'L1', 'type': 'LinePath', 'label': 'Line 1', 'start': list(LINE[0]), 'end': list(LINE[1])}
    if line_thickness is not None:
        line['wall'] = {'thickness': line_thickness, 'align': 'auto'}
    return {'id': 'ref', 'source_paths': [
        {'id': 'C1', 'type': 'CirclePath', 'label': 'Circle 1', 'cx': CX, 'cy': CY, 'radius': R, 'closed': True},
        {'id': 'R1', 'type': 'RectanglePath', 'label': 'Rect 1', 'x': x1, 'y': y1, 'w': w1, 'h': h1, 'closed': True},
        {'id': 'R2', 'type': 'RectanglePath', 'label': 'Rect 2', 'x': x2, 'y': y2, 'w': w2, 'h': h2, 'closed': True},
        line],
        'offset_treatments': [], 'lattice_instances': [],
        'network_walls': [{'id': 'NW1', 'path_id': 'R1', 'thickness': 10, 'align': 'auto'}],
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
