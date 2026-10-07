"""
ONE SHARED LATTICE PER LAYER-DESIGN LINEAGE (2026-10-06): a lattice family
(generator + descendants inheriting the wall infill unchanged) prints ONE
scaffold — planned once on the generator with openings removed — clipped to
each member. Revised the same day after manual testing: every run crossed
by a member's end wall (JAMB) carries TWO passes that cross exactly at the
jamb line's midpoint, so each connected member closes into ONE route (cap
V at its end walls); the lattice DEFINITION (pattern, spacing, variation)
belongs to the lineage.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from app import _deserialise_layer, app as flask_app
from layer_design import DesignLibrary
from graph import route_layer, compute_metrics, build_graph, closure_report
import multi_opening_fixtures as F


def base_doc():
    return {'id': 'l', 'source_paths': [
        {'id': 'W', 'type': 'RectanglePath', 'label': 'Wall', 'x': 100, 'y': 140, 'w': 200, 'h': 120,
         'closed': True, 'wall': {'thickness': 10}}],
        'offset_treatments': [], 'lattice_instances': [],
        'infills': [{'id': 'I', 'path_id': 'W', 'pattern': 'wave', 'params': {'spacing': 20},
                     'kind': 'wall', 'variation_index': 0}],
        'material': {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}}


DOOR = {'door': {'source_path_id': 'W', 'center_s': 100, 'width': 36}}      # x 182 … 218 on y = 140
WINDOW = {'win': {'source_path_id': 'W', 'center_s': 420, 'width': 30}}     # x 185 … 215 on y = 260


def lineage(extra=()):
    return DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base_doc()},
                          {'id': 'door', 'name': 'Door', 'parent': 'base', 'patch': {'openings': DOOR}},
                          {'id': 'win', 'name': 'Window', 'parent': 'base', 'patch': {'openings': WINDOW}},
                          *extra])


def segs(pls):
    return [((a.x, a.y), (b.x, b.y)) for pl in pls for a, b in zip(pl, pl[1:])]


def dseg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def on(p, S, tol=1e-6):
    return any(dseg(p, a, b) < tol for a, b in S)


def route(lib, d):
    lay = _deserialise_layer(lib.document(d))
    lay.lattice_reference = lib.lattice_reference(d) or None
    p, m = lay._build_effective()
    rl = lay.to_routing_layer(p, m)
    return closure_report(build_graph(rl)), compute_metrics(route_layer(rl, allow_retrace=False))


JAMBS = (182, 218, 185, 215)
MIDS = ((182.0, 145.0), (218.0, 145.0), (185.0, 255.0), (215.0, 255.0))   # jamb line midpoints


def _passes_through(pls, p, tol=1e-9):
    """How many lattice passages have p as a vertex (pass-through = 1,
    a polyline end = ½)."""
    n = 0.0
    for pl in pls:
        for k, q in enumerate(pl):
            if abs(q.x - p[0]) < tol and abs(q.y - p[1]) < tol:
                n += 0.5 if k in (0, len(pl) - 1) else 1.0
    return n


def test_one_opening_does_not_re_phase_unrelated_lattice():
    """Adding the Window member changes the scaffold only next to the
    window's jambs (the two neighbouring stations re-centre): everything
    farther than ~1.5 pitches is unchanged."""
    two = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base_doc()},
                         {'id': 'door', 'name': 'Door', 'parent': 'base', 'patch': {'openings': DOOR}}])
    a, b = two.build('base').lattice['I'], lineage().build('base').lattice['I']
    S = segs(b)
    far = [(q.x, q.y) for pl in a for q in pl if min(abs(q.x - j) for j in (185, 215)) > 45 or q.y < 200]
    assert len(far) > 400 and all(on(p, S) for p in far)


def test_every_member_end_wall_is_a_jamb_crossing_in_the_shared_scaffold():
    """The local transition of every member is part of the ONE scaffold:
    its run carries two passes and both cross exactly at the midpoint of
    each end-wall line — in Base too (the relic)."""
    L = lineage()
    sc = L.scaffold('base')['I']
    assert sc['members'] == ['base', 'door', 'win'] and len(sc['stations']) == 4
    jl = L.lattice_lineage('base')['I']
    assert jl['jambs'] == {'resolved': 4, 'unresolved': [], 'bend_failed': 0} and jl['doubled_runs'] == 1
    base = L.build('base').lattice['I']
    for m in MIDS:
        assert _passes_through(base, m) == 2, m                     # both passes, one shared vertex


def test_door_prints_the_scaffold_clipped_not_a_regenerated_lattice():
    L = lineage()
    base, door = L.build('base').lattice['I'], L.build('door').lattice['I']
    Sb = segs(base)
    pts = [(q.x, q.y) for pl in door for q in pl]
    assert pts and all(on(p, Sb) for p in pts)                      # Door ⊂ Base: vertically registered
    assert not any(182 < x < 218 and 140 < y < 150 for x, y in pts)  # the opening stays clear
    ends = [(pl[0].x, pl[0].y) for pl in door] + [(pl[-1].x, pl[-1].y) for pl in door]
    # both passes end at the middle of each of its end walls: a CAP V
    assert sorted(e for e in ends if e in MIDS) == [MIDS[0], MIDS[0], MIDS[1], MIDS[1]]
    regen = L.build('door', inherit_lattice=False).lattice['I']
    assert sum(1 for pl in regen for q in pl if on((q.x, q.y), Sb)) < 0.5 * sum(len(pl) for pl in regen)


def test_window_and_door_share_the_same_scaffold_and_returning_to_base_registers():
    L = lineage([{'id': 'closed', 'name': 'Closed', 'parent': 'door', 'patch': {'openings': {'door': None}}}])
    b, d, w, c = (L.build(x) for x in ('base', 'door', 'win', 'closed'))
    assert b.lattice_source['I'] is d.lattice_source['I'] is w.lattice_source['I'] is c.lattice_source['I']
    key = lambda pls: sorted(tuple(round(v, 6) for q in pl for v in (q.x, q.y)) for pl in pls)
    assert key(c.lattice['I']) == key(b.lattice['I'])               # above the door: Base again, same stitches


def test_assembly_sees_only_the_real_opening():
    """Base → Door → Base through the Layer Assembly: the only unsupported
    span is the doorway itself (HEADER NEEDED) — the Door's end walls sit
    on Base's braces and its lattice on Base's lattice."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from layer_assembly.designer_source import DesignerSource
    from layer_assembly.model import Assembly, Section, resolve
    from layer_assembly.support import analyse_support
    src = DesignerSource(lineage())
    geo = {d: (src.geometry(d).polylines, 3.0) for d in ('base', 'door')}
    a = Assembly(1.5, [Section('base', 6), Section('door', 6), Section('base', 6)])
    fs = analyse_support(resolve(a, ['base', 'door', 'win']), geo, a).findings
    assert len(fs) == 1 and fs[0].layer == 8 and abs(fs[0].centre[0] - 200) < 1 and 30 < fs[0].span < 36


def test_designer_and_assembly_resolve_identical_geometry():
    L = lineage()
    designs = [d.to_dict() for d in L.designs.values()]
    with flask_app.test_client() as c:
        for did in ('base', 'door'):
            doc = L.document(did)
            r = c.post('/api/route', json={**doc, 'lineage': {'designs': designs, 'id': did, 'document': doc}}).get_json()
            assert r.get('printable') == L.build(did).printable, did
        alone = c.post('/api/route', json=L.document('door')).get_json()          # no lineage: normal planning
        assert alone['printable'] != L.build('door').printable


def test_multiple_openings_keep_the_rest_of_the_lattice_registered():
    L = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': F.document(())},
                       {'id': 'cuts', 'name': 'Cuts', 'parent': 'base',
                        'patch': {'openings': {o['id']: o for o in F.document()['openings']}}}])
    base, cuts = L.build('base'), L.build('cuts')
    Sb = segs(base.lattice['IR'])
    assert all(on((q.x, q.y), Sb) for pl in cuts.lattice['IR'] for q in pl)
    lay = _deserialise_layer(L.document('cuts'))
    lay.lattice_reference = L.lattice_reference('cuts')
    paths, _ = lay._build_effective()
    faces = [[(q.x, q.y) for q in p.sample_points()] for p in paths if getattr(p, 'treatment_id', '') != 'infill']
    lat = [[(q.x, q.y) for q in p.sample_points()] for p in paths if getattr(p, 'treatment_id', '') == 'infill']
    assert '!' not in F.circle_profile(faces, lat)                  # the cut-free arc keeps its lattice


def test_every_connected_member_prints_one_closed_route():
    """The connected wall of every member — Base, Door, Window, and Closed
    (the door filled in again above it) — is ONE continuous closed route:
    no travel, no retrace."""
    L = lineage([{'id': 'closed', 'name': 'Closed', 'parent': 'door', 'patch': {'openings': {'door': None}}}])
    for d in ('base', 'door', 'win', 'closed'):
        c, m = route(L, d)
        assert c['components'] == c['closed'] == 1 and not c['open'], d
        assert m['travel_moves'] == 0 and m['retrace_moves'] == 0, d


def test_network_members_close_every_connected_piece():
    """Rect + line + circle network, three openings (one cuts a circle arc
    free): every connected piece is one closed route; the only travel is
    between the two physically separate pieces."""
    L = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': F.document(())},
                       {'id': 'cuts', 'name': 'Cuts', 'parent': 'base',
                        'patch': {'openings': {o['id']: o for o in F.document()['openings']}}}])
    cb, mb = route(L, 'base')
    assert cb['components'] == cb['closed'] == 1 and mb['travel_moves'] == 0 and mb['retrace_moves'] == 0
    cc, mc = route(L, 'cuts')
    assert cc['components'] == cc['closed'] == 2 and not cc['open']
    assert mc['travel_moves'] == 1 and mc['retrace_moves'] == 0
    assert L.lattice_lineage('base')['IR']['jambs']['unresolved'] == []


def test_an_end_wall_in_a_corner_closes_by_replacing_the_corner_station():
    """Superseded 2026-10-06 (stabilization): an opening ending in the
    rectangle's corner used to be reported unresolved (the corner station is
    fixed). The crossing now REPLACES that corner station (the passes' tails
    swap) and the member closes."""
    L = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': base_doc()},
                       {'id': 'c', 'name': 'Corner door', 'parent': 'base',
                        'patch': {'openings': {'o': {'source_path_id': 'W', 'center_s': 180, 'width': 36}}}}])
    assert L.lattice_lineage('c')['I']['jambs']['unresolved'] == []
    c, m = route(L, 'c')
    assert c['closed'] == c['components'] == 1 and m['travel_moves'] == 0 and m['retrace_moves'] == 0


NET = [[('R', 420, 20)],                                  # ends at the rect's inner corner
       [('C', 250, 20)],                                  # cuts the circle where the line attaches
       [('C', 150, 40), ('C', 350, 40), ('R', 120, 40)],  # the multi-opening fixture
       [('R', 60, 20), ('L', 30, 20), ('R', 600, 20)],
       [('C', 60, 20), ('L', 30, 20), ('C', 250, 40)],
       [('R', 600, 40), ('C', 250, 40), ('R', 200, 40)]]


@pytest.mark.parametrize('ops', NET)
def test_connected_network_members_route_closed(ops):
    """Circle + connecting line + rectangle, one / several openings: every
    connected piece of each member is ONE closed route (travel only between
    physically separate pieces, no retrace)."""
    L = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': F.document(())},
                       {'id': 'cuts', 'name': 'Cuts', 'parent': 'base',
                        'patch': {'openings': {o['id']: o for o in F.document(ops)['openings']}}}])
    c, m = route(L, 'cuts')
    assert not c['open'] and c['closed'] == c['components'], c
    assert m['travel_moves'] == c['components'] - 1 and m['retrace_moves'] == 0
    assert L.build('cuts').network['lattice']['IR']['lineage']['jambs']['unresolved'] == []


def test_an_opening_overlapping_a_junction_closes_planned_alone_and_says_so():
    """An opening on the rectangle that half-cuts the junction where the line
    attaches: no local crossing closes the SHARED scaffold there (the junction
    is rebuilt around the new end wall, so the member's material is not Base
    minus the opening). SUPERSEDES "is reported when it cannot close"
    (stabilization 2026-10-06): a closed route outranks vertical registration
    (lineage priority order), so the member is planned ON ITS OWN — one closed
    route — and the loss of registration is reported, never hidden."""
    L = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': F.document(())},
                       {'id': 'cuts', 'name': 'Cuts', 'parent': 'base',
                        'patch': {'openings': {o['id']: o for o in F.document([('R', 300, 40)])['openings']}}}])
    lin = L.build('cuts').network['lattice']['IR']['lineage']
    assert lin['planned_alone'] and 'planned on its own' in lin['jambs']['unresolved'][-1]['why']
    assert lin['jambs']['unresolved'][-1]['at']                       # where the scaffold stayed open
    assert L.lattice_reference('cuts') == {} and L.lattice_reference('base')   # Base keeps the scaffold
    c, _ = route(L, 'cuts')
    assert not c['open']


def test_lattice_edit_on_a_descendant_changes_the_whole_lineage():
    """Pattern / spacing edited while viewing Door: the definition is the
    LINEAGE's — it lands in Base (the owner), Door stores only its opening,
    and Base, Door and Window print one vertically registered scaffold."""
    L = lineage()
    doc = L.document('door')
    doc['infills'][0].update(pattern='zigzag', params={'spacing': 30})
    assert L.set_document('door', doc) == {'base': ['I']}
    assert set(L.get('door').patch) == {'openings'}                 # no copy of the lattice in Door
    for d in ('base', 'door', 'win'):
        f = L.document(d)['infills'][0]
        assert f['pattern'] == 'zigzag' and f['params'] == {'spacing': 30}, d
    sb = L.scaffold('base')['I']['polylines']
    assert L.scaffold('door')['I']['polylines'] is sb and L.scaffold('win')['I']['polylines'] is sb
    Sb = segs(L.build('base').lattice['I'])
    for d in ('door', 'win'):
        assert all(on((q.x, q.y), Sb) for pl in L.build(d).lattice['I'] for q in pl), d
        c, m = route(L, d)
        assert c['closed'] == 1 and m['travel_moves'] == 0, d
    regular = DesignLibrary([{'id': 'base', 'name': 'Base', 'document': L.document('base')}])
    assert regular.document('base')['infills'][0]['pattern'] == 'zigzag'


def test_a_descendant_cannot_stack_a_different_lattice():
    """A lattice override stored in a derived patch (older data) is inert —
    one authoritative definition; a descendant that introduces its OWN
    infill on the lineage's wall is reported as a conflict."""
    L = lineage([{'id': 'zig', 'name': 'Zigzag', 'parent': 'door',
                  'patch': {'infills': {'I': {'pattern': 'zigzag'}}}}])
    assert L.document('zig')['infills'][0]['pattern'] == 'wave'
    assert L.family('zig', 'I') == ['base', 'door', 'win', 'zig'] and L.generator('zig', 'I') == 'base'
    own = lineage([{'id': 'own', 'name': 'Own', 'parent': 'door',
                    'patch': {'infills': {'I': None, 'J': {'path_id': 'W', 'pattern': 'zigzag', 'kind': 'wall',
                                                           'params': {'spacing': 20}, 'variation_index': 0}}}}])
    conf = own.lattice_lineage('own').get('_conflicts')
    assert conf == [{'path_id': 'W', 'infill': 'J', 'owner': 'own', 'lineage_infill': 'I', 'lineage_owner': 'base'}]


def test_designer_lattice_edit_through_the_endpoints():
    """The Designer stores a derived design through /api/layer_designs/delta
    (with its id): the lattice edit comes back as Base's; and the live view
    (/api/route with `lineage`) already shows the lineage-wide lattice."""
    L = lineage()
    designs = [d.to_dict() for d in L.designs.values()]
    doc = L.document('door')
    doc['infills'][0]['pattern'] = 'zigzag'
    with flask_app.test_client() as c:
        r = c.post('/api/layer_designs/delta', json={'designs': designs, 'parent': 'base', 'id': 'door',
                                                     'document': doc}).get_json()
        assert r['lattice_moved'] == {'base': ['I']} and 'infills' not in r['patch']
        base = next(d for d in r['designs'] if d['id'] == 'base')
        assert base['document']['infills'][0]['pattern'] == 'zigzag'
        live = c.post('/api/route', json={**doc, 'lineage': {'designs': designs, 'id': 'door',
                                                             'document': doc}}).get_json()
    L2 = DesignLibrary(r['designs'])
    assert live['printable'] == L2.build('door').printable
