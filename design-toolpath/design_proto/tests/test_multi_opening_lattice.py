"""
REGRESSION (2026-10-06): wall lattice vanished from a surviving wall region
after several openings. Cause: an infill only reached material regions
bounded by beads of its OWN anchor source, so a circle arc cut free of the
rectangle (whose infill filled the whole uncut network) by two openings got
no infill at all — silently ('ok'). Fix (model._region_infill): the reach
is transitive through the sources bounding already-reached regions, so
openings / Trim remove material but never change which infill fills it.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer
from graph import route_layer, compute_metrics, build_graph, closure_report
import multi_opening_fixtures as F


def build(doc):
    lay = _deserialise_layer(doc)
    paths, meta = lay._build_effective()
    faces = [[(q.x, q.y) for q in p.sample_points()] for p in paths if getattr(p, 'treatment_id', '') != 'infill']
    lat = [[(q.x, q.y) for q in p.sample_points()] for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    return lay, paths, meta, faces, lat


def test_surviving_circle_arc_keeps_its_lattice():
    _, _, meta, faces, lat = build(F.document())
    prof = F.circle_profile(faces, lat)
    assert '!' not in prof, prof                         # every wall stretch has lattice
    assert prof.count('.') >= 2 * 13                     # the two circle openings are really there
    inf = next(i for i in meta['network']['infills'] if i['id'] == 'IR')
    assert inf['regions'] == 2 and inf['status'] == 'ok'   # the cut-free arc is a second region


def test_openings_stay_clear():
    _, _, _, faces, lat = build(F.document())
    for pl in lat:
        for p in pl:
            r = math.hypot(p[0] - F.CX, p[1] - F.CY)
            if F.R - 10.5 <= r <= F.R + 0.5:              # a lattice point on the circle wall …
                a = math.atan2(p[1] - F.CY, p[0] - F.CX)
                on = lambda rr: (F.CX + rr * math.cos(a), F.CY + rr * math.sin(a))
                assert F.near(on(F.R), faces, 6.0) and F.near(on(F.R - 10), faces, 6.0)  # … is in wall


def test_uncut_and_single_opening_unchanged():
    for ops in ((), (('C', 150, 40),)):
        _, _, meta, faces, lat = build(F.document(ops))
        assert '!' not in F.circle_profile(faces, lat)
        assert next(i for i in meta['network']['infills'] if i['id'] == 'IR')['regions'] == 1


def test_ownership_does_not_depend_on_an_extra_infill():
    """The cut-free arc is filled by the infill that filled the uncut wall
    (first infill per region wins, as for the uncut network): declaring an
    infill on the circle as well changes nothing."""
    a = build(F.document())[1]
    b = build(F.document(infill_on=('R', 'C')))[1]
    key = lambda paths: sorted((p.id, [(round(q.x, 6), round(q.y, 6)) for q in p.sample_points()])
                               for p in paths if getattr(p, 'treatment_id', '') == 'infill')
    assert key(a) == key(b)


def test_route_stays_physically_valid():
    lay, paths, meta, _, _ = build(F.document())
    rl = lay.to_routing_layer(paths, meta)
    cl = closure_report(build_graph(rl))
    met = compute_metrics(route_layer(rl, allow_retrace=False))
    assert cl['components'] == 2 and cl['closed'] == 2 and not cl['open']
    assert met['retrace_moves'] == 0 and met['travel_moves'] == 1     # only between the two pieces
