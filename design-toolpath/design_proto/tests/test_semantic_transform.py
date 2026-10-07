"""
SEMANTIC TRANSFORMS (2026-10-06): Layer Assembly transform groups move a
design's ARCHITECTURE — its source paths — and the Designer then resolves
walls, junctions, openings and lattice (semantic_transform.py,
DesignLibrary.build(…, transforms)). A wall attached to two differently
moved hosts is REGENERATED between its transformed attachments, never
stretched.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer
from layer_design import DesignLibrary
import semantic_transform as ST
from graph import route_layer, compute_metrics, build_graph, closure_report
import multi_opening_fixtures as F


def about(k, c):
    return (k, c[0] * (1 - k), c[1] * (1 - k))


# circle C (centre 480,180 r 80) — line L (340,180 → 400,180) — rectangle R (100..340 × 100..260)
TF = {'C': about(0.6, (480, 180)), 'R': about(0.8, (220, 180))}


def lib(doc=None, cuts=False):
    ds = [{'id': 'b', 'name': 'Base', 'document': doc or F.document(())}]
    if cuts:
        ds.append({'id': 'c', 'name': 'Cuts', 'parent': 'b',
                   'patch': {'openings': {o['id']: o for o in F.document()['openings']}}})
    return DesignLibrary(ds)


def strands(b, prefix):
    return [p for p in b.printable if p['id'] == prefix or p['id'].startswith(prefix)]


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def route(L, d, tf=None):
    doc, _ = L.transformed(d, tf)
    lay = _deserialise_layer(doc)
    lay.lattice_reference = L.lattice_reference(d, tf) or None
    p, m = lay._build_effective()
    rl = lay.to_routing_layer(p, m)
    return closure_report(build_graph(rl)), compute_metrics(route_layer(rl, allow_retrace=False))


def test_attachments_come_from_the_untransformed_design():
    a = ST.attachments(F.document(()))
    assert a == {('L', 0): ('T', 'R', (340.0, 180.0), (0.0, 0.0)),
                 ('L', 1): ('T', 'C', (400.0, 180.0), (0.0, 0.0))}


def test_disconnected_shapes_transform_independently():
    doc = F.document(())
    doc['source_paths'] = [p for p in doc['source_paths'] if p['id'] != 'L']   # no connector
    b = lib(doc).build('b', transforms=TF)
    assert b.semantic['connectors'] == []
    C = next(p for p in b.document['source_paths'] if p['id'] == 'C')
    R = next(p for p in b.document['source_paths'] if p['id'] == 'R')
    assert (C['cx'], C['cy'], C['radius']) == (480, 180, 48) and math.isclose(R['w'], 192)
    xs = [x for p in strands(b, 'C') for x, _ in p['pts']]
    assert math.isclose(max(xs), 528, abs_tol=0.01)                  # outer face of the scaled circle
    assert C['wall'] == F.document(())['source_paths'][1]['wall']    # thickness is physical


def test_the_connector_is_regenerated_as_a_constant_thickness_wall_on_its_hosts():
    b = lib().build('b', transforms=TF)
    (con,) = b.semantic['connectors']
    assert con['source'] == 'L' and con['hosts'] == ['R', 'C']
    L = next(p for p in b.document['source_paths'] if p['id'] == 'L')
    assert L['start'] == [316.0, 180.0] and L['end'] == [432.0, 180.0]          # T_R(340,180), T_C(400,180)
    assert L['wall'] == {'thickness': 10}                                        # same wall
    R = next(p for p in b.document['source_paths'] if p['id'] == 'R')
    assert math.isclose(R['x'] + R['w'], 316.0)                                  # ON the rectangle's right edge
    C = next(p for p in b.document['source_paths'] if p['id'] == 'C')
    assert math.isclose(C['cx'] - C['radius'], 432.0)                            # ON the circle
    faces = strands(b, 'L.wall')
    assert len(faces) == 2
    ys = sorted({round(y, 9) for p in faces for _, y in p['pts']})
    assert len(ys) == 2 and math.isclose(ys[1] - ys[0], 10.0)                    # two straight, parallel faces …
    plain = lib().build('b')
    py = sorted({round(y, 9) for p in strands(plain, 'L.wall') for _, y in p['pts']})
    assert math.isclose(py[1] - py[0], ys[1] - ys[0])                            # … exactly as far apart as before


def test_rounded_and_miter_junctions_are_regenerated_as_ordinary_junctions():
    for style, radius in (('round', 6.0), ('miter', 0.0)):
        doc = F.document(())
        doc['junction_style'], doc['junction_radius'] = style, radius
        plain, moved = lib(doc).build('b'), lib(doc).build('b', transforms=TF)
        j0 = sorted((j['key'], j['treatment'], j['actual_radius']) for j in plain.network['junctions'])
        j1 = sorted((j['key'], j['treatment'], j['actual_radius']) for j in moved.network['junctions'])
        assert j1 == j0 and all(t == style for _, t, _ in j1), style
        xs = sorted(round(j['x'], 3) for j in moved.network['junctions'])
        assert xs[:2] == [316.0, 316.0] and all(431 < x < 433 for x in xs[2:]), style   # at the new attachments


def test_the_connector_lattice_is_resolved_inside_the_new_wall():
    b = lib().build('b', transforms=TF)
    faces = strands(b, 'L.wall')
    y0, y1 = sorted({round(y, 9) for p in faces for _, y in p['pts']})
    inside = [(x, y) for pl in b.lattice['IR'] for x, y in ((q.x, q.y) for q in pl) if 325 < x < 423]
    assert len(inside) > 20 and all(y0 - 1e-6 <= y <= y1 + 1e-6 for _, y in inside)
    # the connector is ~116 in long now (60 before): more stitches at the target
    # pitch, not the old ones stretched
    plain = lib().build('b')
    n0 = sum(1 for pl in plain.lattice['IR'] for q in pl if 345 < q.x < 395)
    n1 = sum(1 for pl in b.lattice['IR'] for q in pl if 321 < q.x < 427)
    assert n1 > 1.5 * n0


def test_openings_keep_working_and_every_piece_closes():
    L = lib(cuts=True)
    b = L.build('c', transforms=TF)
    assert all(o['status'] == 'ok' for o in b.network['opening_status'])
    c, m = route(L, 'c', TF)
    assert c['closed'] == c['components'] and not c['open'] and m['retrace_moves'] == 0


def test_the_lineage_scaffold_is_resolved_for_the_same_transform_and_stays_registered():
    L = lib(cuts=True)
    base, cuts = L.build('b', transforms=TF), L.build('c', transforms=TF)
    assert base.lattice_source['IR'] is cuts.lattice_source['IR']                 # ONE scaffold for this transform
    S = [((a.x, a.y), (b.x, b.y)) for pl in base.lattice['IR'] for a, b in zip(pl, pl[1:])]
    assert all(any(_segd((q.x, q.y), *s) < 1e-6 for s in S) for pl in cuts.lattice['IR'] for q in pl)
    assert L.lattice_lineage('c', TF)['IR']['jambs']['unresolved'] == []
    assert L.build('b').lattice_source['IR'] is not base.lattice_source['IR']    # untransformed: its own


def test_the_identity_is_the_plain_design():
    L = lib()
    assert L.build('b', transforms={'C': (1.0, 0.0, 0.0)}) is L.build('b')
    assert ST.transform_document(F.document(()), {})[0] == F.document(())
