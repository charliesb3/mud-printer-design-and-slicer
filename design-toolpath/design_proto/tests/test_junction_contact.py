"""
CONTACT OVERLAP AT A T-JUNCTION (manual-review regression, 2026-10-08).

A Skin + Web wall meeting another wall's side: the junction triangle has
two CONCAVE corners (where the stem's faces meet the through wall) and one
vertex on the through wall's FAR face. Pass ends were handed off at all
three exactly ON the face centreline — at the far face that is a strand
crossing the through wall's cavity and landing on the opposite skin with
100 % overlap, unlike every other lattice contact (Contact Overlap: the
landing stops bead − overlap off the face centreline). Now a hand-off on
a straight face lands on its contact rail; concave corners stay exact (the
route joins the faces there); every route still closes.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from layer_design import DesignLibrary

BEAD, OVERLAP = 3.0, 0.75
T = 10.0
SKIN = (T - BEAD) / 2          # skin centrelines half a bead inside the 10 in envelope (PHYSICAL WALL WIDTH)


def tee(pattern):
    return {'id': 't', 'source_paths': [
        {'id': 'A', 'type': 'LinePath', 'start': [0, 0], 'end': [200, 0]},
        {'id': 'B', 'type': 'LinePath', 'start': [100, 0], 'end': [100, 100]}],
        'wall_systems': [{'id': 'WS', 'type': 'skin_web', 'members': ['A', 'B'], 'thickness': T, 'params': {},
                          'web': {'pattern': pattern, 'params': {'spacing': 20}, 'variation_index': 0}}],
        'infills': [{'id': 'WEB', 'path_id': 'A', 'pattern': pattern, 'params': {'spacing': 20}, 'kind': 'wall',
                     'variation_index': 0, 'owner': 'WS'}],
        'material': {'bead_width': BEAD, 'contact_overlap': OVERLAP, 'return_overlap': 0.75, 'physical': True}}


def _seg_d(q, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
    return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy)


@pytest.mark.parametrize('pattern', ['zigzag', 'wave'])
def test_t_junction_hand_off_respects_contact_overlap(pattern):
    lib = DesignLibrary([{'id': 'A', 'name': 'A', 'document': tee(pattern)}])
    b = lib.build('A')
    faces = [p['pts'] + ([p['pts'][0]] if p.get('closed') else []) for p in b.printable if p['id'] in b.strand_sources]
    segs = [(a, c) for f in faces for a, c in zip(f, f[1:])]
    contact = BEAD - OVERLAP
    corners = [(100.0 - SKIN, SKIN), (100.0 + SKIN, SKIN)]   # the two concave junction corners
    ends = [(q.x, q.y) for ps in b.lattice.values() for p in ps for q in (p[0], p[-1])]
    exact = [q for q in ends if min(_seg_d(q, a, c) for a, c in segs) < 0.5 * contact]
    assert exact and all(min(math.dist(q, k) for k in corners) < 1e-6 for q in exact), \
        f'exact (100 % overlap) landings away from the concave corners: {exact}'
    far = [q for q in ends if abs(q[1] + SKIN) < 0.5 * contact and 90 < q[0] < 110]
    assert far == [], 'a pass end lands on the through wall\'s far face'
    from app import _deserialise_layer
    lay = _deserialise_layer(lib.document('A'))
    paths, meta = lay._build_effective()
    assert lib._open_ends(lay, paths, meta) == []         # junction connectivity kept: one closed route
