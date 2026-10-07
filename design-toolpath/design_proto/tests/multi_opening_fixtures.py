"""
REGRESSION fixture (2026-10-06): a connected rectangle + circle + line wall
network (10 in walls, ONE wave Wall Infill — on the rectangle, as the UI adds
it) with several large openings. Two openings on the circle cut its far arc
free of the rest of the network; that arc keeps both faces and must keep
its lattice. Used by tests/test_multi_opening_lattice.py and by the Layer
Assembly support contract test.
"""
import math

CX, CY, R = 480.0, 180.0, 80.0          # circle, joined to the rect by the line


def document(openings=(('C', 150, 40), ('C', 350, 40), ('R', 120, 40)), infill_on=('R',)):
    return {'id': 'l', 'source_paths': [
        {'id': 'R', 'type': 'RectanglePath', 'label': 'Rect 1', 'x': 100, 'y': 100, 'w': 240, 'h': 160,
         'closed': True, 'wall': {'thickness': 10}},
        {'id': 'C', 'type': 'CirclePath', 'label': 'Circle 1', 'cx': CX, 'cy': CY, 'radius': R,
         'closed': True, 'wall': {'thickness': 10}},
        {'id': 'L', 'type': 'LinePath', 'label': 'Line 1', 'start': [340, 180], 'end': [400, 180],
         'wall': {'thickness': 10}}],
        'offset_treatments': [], 'lattice_instances': [],
        'infills': [{'id': 'I' + p, 'path_id': p, 'pattern': 'wave', 'params': {'spacing': 20},
                     'kind': 'wall', 'variation_index': 0} for p in infill_on],
        'openings': [{'id': f'o{k}', 'source_path_id': s, 'center_s': c, 'width': w}
                     for k, (s, c, w) in enumerate(openings)],
        'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}}


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def near(p, polys, tol):
    return any(_segd(p, a, b) <= tol for pl in polys for a, b in zip(pl, pl[1:]))


def circle_profile(faces, lattice, step_deg=2):
    """Around the circle wall (faces at r and r − 10): 'L' wall with lattice,
    '!' wall WITHOUT lattice, '.' no wall (an opening / the junction)."""
    out = []
    for k in range(0, 360, step_deg):
        a = math.radians(k)
        P = lambda rr: (CX + rr * math.cos(a), CY + rr * math.sin(a))
        wall = near(P(R), faces, 2.0) and near(P(R - 10), faces, 2.0)
        out.append('L' if wall and near(P(R - 5), lattice, 12.0) else ('!' if wall else '.'))
    return ''.join(out)
