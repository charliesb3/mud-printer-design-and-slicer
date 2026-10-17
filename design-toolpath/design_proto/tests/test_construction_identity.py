"""
CONSTRUCTION IDENTITY — stage Z2 (WALL_ARCHITECTURE.md §Z.2 §1–2,
2026-10-17; design_proto/construction_identity.py). IDENTITY ONLY: these
tests pin that every piece of construction is named from the architecture
of its own layer (persistent junction records, wall corner ordinals, opening
ids, wall ends) and that those names are STABLE through gradual transforms
— so cross-Z correspondence never has to be recovered by geometric
proximity. Geometry is unchanged (the golden suite proves it byte for byte).
"""
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, '..'), os.path.join(HERE, '..', '..'), os.path.join(HERE, '..', '..', 'layer_assembly', 'tests')):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('LAYER_ASSEMBLY_WORKERS', '0')

import construction_identity as CI                              # noqa: E402
from layer_design import DesignLibrary                          # noqa: E402
from model import Vec2, RectanglePath, CirclePath, ExplicitPath, LinePath   # noqa: E402
import test_wall_system_z_coherence as Z                        # noqa: E402  (the lobe building)


def _ids(identity, kind=None):
    return sorted(a['id'] for a in identity['anchors'] if kind is None or a['kind'] == kind)


def _segments(identity):
    return sorted(s for d in identity['domains'] for s in d['segments'])


def _scaled_circle(k):
    """The lobe building's circle scaled about its centre (the Assembly's
    transform-group variant of the same design)."""
    return None if k == 1.0 else {'C': (k, Z.CX * (1 - k), Z.CY * (1 - k))}


@pytest.fixture(scope='module')
def lobe():
    lib = DesignLibrary(Z.designs(False), Z.SYSTEMS)
    return lib, lib.build('base')


# ---- anchors: names from the architecture -------------------------------------------

def test_wall_corners_are_authored_vertex_ordinals():
    rect = RectanglePath(10, 20, 30, 40, id='R')
    assert [k for k, _ in CI.wall_corners(rect)] == [0, 1, 2, 3]
    rot = RectanglePath(10, 20, 30, 40, rotation=0.7, id='R')
    assert [k for k, _ in CI.wall_corners(rot)] == [0, 1, 2, 3], 'the same ordinals under rotation'
    assert CI.wall_corners(CirclePath(0, 0, 8, id='C')) == [], 'a circle has no corner at any radius'
    assert CI.wall_corners(CirclePath(0, 0, 400, id='C')) == []
    poly = ExplicitPath([Vec2(0, 0), Vec2(50, 0), Vec2(100, 5), Vec2(100, 60)], id='P', closed=False)
    assert [k for k, _ in CI.wall_corners(poly)] == [2], 'only the sharp vertex (a 5.7° bend is not a corner)'
    assert CI.wall_corners(LinePath(Vec2(0, 0), Vec2(10, 0), id='L')) == []


def test_lobe_building_anchors_are_its_corners_and_junction_records(lobe):
    lib, b = lobe
    I = b.network['identity']
    recs = {r['id'] for r in lib.registry()['records']}
    assert recs == {'J1', 'J2'}
    assert _ids(I, 'junction') == ['J1', 'J2'], 'the two circle × square junctions, by their persistent ids'
    assert _ids(I, 'corner') == ['C:R#0', 'C:R#1', 'C:R#2', 'C:R#3'], 'the square\'s corners by ordinal'
    assert _ids(I, 'end') == [] and _ids(I, 'jamb') == []
    for a in I['anchors']:
        if a['kind'] == 'junction':
            assert a['record'] and a['walls'] == ['C', 'R'] and a['roles'] == {'C': 'through', 'R': 'through'}
            assert a['thin'] == [] and not a['excursion']
    json.dumps(I)                      # plain data: travels in the geometry diagnostics


def test_same_system_walls_form_one_domain_across_source_boundaries(lobe):
    """Invariant 7: the circle and the square, both in Wall System S1, are
    ONE construction domain — one web over the merged territory."""
    _, b = lobe
    doms = b.network['identity']['domains']
    assert len(doms) == 1
    d = doms[0]
    assert d['system'] == 'S1' and d['walls'] == ['C', 'R'] and d['unresolved'] == 0
    assert len(d['runs']) == 1 and d['runs'][0]['cycle']
    # the merged loop's anchors: three square corners (the fourth is inside
    # the circle — trimmed away) and the two junction KINKS by junction id
    run = d['runs'][0]
    assert sorted(run['anchors']) == ['C:R#0', 'C:R#1', 'C:R#2', 'J1', 'J2']
    assert len(d['segments']) == 5
    assert all('|' in s for s in d['segments']), 'segment id = its two anchors'


def test_production_junction_corners_carry_their_junction_record(lobe):
    _, b = lobe
    infos = [j for j in b.network['junctions'] if j.get('corner')]
    assert len(infos) == 4, 'two rounded corners per junction (inner / outer face)'
    assert {j['junction'] for j in infos} == {'J1', 'J2'}
    assert all(j['key'] and '#' in j['key'] for j in infos), 'the legacy face-pair key is kept (Z6 / Z8)'
    by = {}
    for j in infos:
        by.setdefault(j['junction'], []).append(j['key'])
    assert all(len(v) == 2 for v in by.values())


# ---- stability through gradual transforms (the Z2 gate) -----------------------------

@pytest.mark.parametrize('k', [0.95, 0.9, 0.85, 0.8, 0.75, 0.7])
def test_identity_is_stable_while_the_circle_scales(lobe, k):
    """The Assembly's gradually scaled circle (test_stack_coherence
    'scaled_circle_skin_web'): the anchors move, their NAMES do not — the
    same five segments on every layer, no event, nothing unresolved."""
    lib, b0 = lobe
    I0 = b0.network['identity']
    b = lib.build('base', transforms=_scaled_circle(k))
    I = b.network['identity']
    assert _ids(I) == _ids(I0)
    assert _segments(I) == _segments(I0)
    assert [d['unresolved'] for d in I['domains']] == [0]
    assert CI.diff(I0, I) == {}
    assert {j['junction'] for j in b.network['junctions'] if j.get('corner')} == {'J1', 'J2'}


def test_a_junction_ceasing_is_an_explicit_event_and_nothing_else_changes(lobe):
    """Scaled far enough the circle lies inside the square and no longer
    meets it: J1 / J2 CEASE (invariant 6: a local topology event, reported
    here) — the square's corners keep their names."""
    lib, b0 = lobe
    b = lib.build('base', transforms=_scaled_circle(0.25))
    I = b.network['identity']
    ev = CI.diff(b0.network['identity'], I)
    assert ev['junction'] == {'born': [], 'ceased': ['J1', 'J2']}
    assert _ids(I, 'corner') == ['C:R#0', 'C:R#1', 'C:R#2', 'C:R#3']
    assert 'corner' not in ev


def test_an_opening_adds_its_jambs_as_anchors_and_an_event(lobe):
    lib0, b0 = lobe
    lib = DesignLibrary(Z.designs(True), Z.SYSTEMS)        # 'd1' = Base + a door on the square
    b = lib.build('d1')
    I = b.network['identity']
    assert _ids(I, 'jamb') == ['O:o1:a', 'O:o1:b']
    ev = CI.diff(b0.network['identity'], I)
    assert ev['jamb'] == {'born': ['O:o1:a', 'O:o1:b'], 'ceased': []}
    assert 'junction' not in ev and 'corner' not in ev
    jambs = {a['id']: a for a in I['anchors'] if a['kind'] == 'jamb'}
    assert all(a['opening'] == 'o1' and a['walls'] == ['R'] for a in jambs.values())
    assert math.isclose(math.dist((jambs['O:o1:a']['x'], jambs['O:o1:a']['y']),
                                  (jambs['O:o1:b']['x'], jambs['O:o1:b']['y'])), 24.0, abs_tol=0.5)


# ---- THIN Out-and-Back stays an excursion (invariant 8) ---------------------------------

def _host_and_thin_line():
    doc = Z.document()
    doc['source_paths'] = [
        {'id': 'W', 'type': 'RectanglePath', 'label': 'Room', 'closed': True, 'role': 'free', 'visible': True,
         'x': 20, 'y': 20, 'w': 160, 'h': 100,
         'envelope': {'align': 'center', 'print_reference': False, 'thickness': 10}},
        # a THIN line (no envelope) from the room's bottom wall out into the open
        {'id': 'L', 'type': 'LinePath', 'label': 'Thin', 'closed': False, 'role': 'free', 'visible': True,
         'start': [100, 20], 'end': [100, -60]}]
    doc['trims'] = []
    doc['junction_style'], doc['junction_radius'] = 'miter', 0
    systems = [{'id': 'S1', 'name': 'Wall System 1', 'type': 'skin_web', 'params': {}, 'members': ['W'],
                'web': {'pattern': 'zigzag', 'params': {}, 'variation_index': 0}}]
    return [{'id': 'base', 'name': 'Base', 'parent': None, 'document': doc}], systems


def test_thin_out_and_back_is_never_part_of_the_domain():
    designs, systems = _host_and_thin_line()
    lib = DesignLibrary(designs, systems)
    b = lib.build('base')
    I = b.network['identity']
    js = [a for a in I['anchors'] if a['kind'] == 'junction']
    assert len(js) == 1 and js[0]['walls'] == ['L', 'W'] and js[0]['roles'] == {'L': 'start', 'W': 'through'}
    assert js[0]['thin'] == ['L'] and js[0]['excursion'], 'a thin member meeting a thick wall: an excursion'
    assert js[0]['record'] and js[0]['id'] == 'J1'
    assert _ids(I, 'corner') == ['C:W#0', 'C:W#1', 'C:W#2', 'C:W#3']
    assert _ids(I, 'end') == [], 'a thin wall anchors no ends of its own'
    doms = I['domains']
    assert len(doms) == 1 and doms[0]['walls'] == ['W'], 'the web\'s domain is the room alone'
    d = doms[0]
    assert d['thin'] == ['L'], 'the thin line touches the domain — reported apart, never a member'
    assert d['unresolved'] == 0
    # where the lanes enter the host the web has a LOCAL feature (today: the
    # skeleton splits at the lanes' mouth); it is named by the junction record,
    # so a later stage can treat it locally — the rest are the room's corners
    anchors = sorted(a for r in d['runs'] for a in r['anchors'])
    assert anchors.count('J1') >= 1 and [a for a in anchors if a != 'J1'] == ['C:W#0', 'C:W#1', 'C:W#2', 'C:W#3']
    assert all(('J1' in s) or (s.split('|')[0].startswith('C:W') and s.split('|')[1].startswith('C:W'))
               for s in d['segments'])


# ---- segment naming rules ------------------------------------------------------------

def _desc(cycle, ends, corners, mid, length=100.0, ccw=True):
    return {'cycle': cycle, 'length': length, 'ends': ends, 'corners': corners, 'mid': mid, 'ccw': ccw}


def test_segment_ids_are_anchor_pairs_disambiguated_only_when_a_region_repeats_one():
    A = [{'id': 'J1', 'kind': 'junction', 'x': 0, 'y': 0, 'walls': ['A', 'B'], 'thin': []},
         {'id': 'J2', 'kind': 'junction', 'x': 100, 'y': 0, 'walls': ['A', 'B'], 'thin': []},
         {'id': 'C:A#0', 'kind': 'corner', 'x': 0, 'y': 50, 'walls': ['A']},
         {'id': 'C:A#1', 'kind': 'corner', 'x': 100, 'y': 50, 'walls': ['A']}]
    walls = {'A': ([Vec2(0, 10), Vec2(100, 10)], False), 'B': ([Vec2(0, -10), Vec2(100, -10)], False)}
    # a LENS: two runs between the same two junctions, one in each wall
    lens = CI.resolve_runs({'anchors': A, 'walls': walls},
                           [_desc(False, [('J', Vec2(1, 9)), ('J', Vec2(99, 9))], [], Vec2(50, 9)),
                            _desc(False, [('J', Vec2(1, -9)), ('J', Vec2(99, -9))], [], Vec2(50, -9))], 10.0)
    assert lens['segments'] == ['J1|J2~A', 'J1|J2~B'] and lens['unresolved'] == 0
    # a two-corner loop: cyclic order tells the two stretches apart
    loop = CI.resolve_runs({'anchors': A, 'walls': walls},
                           [_desc(True, None, [(0.0, Vec2(0, 49)), (50.0, Vec2(100, 49))], Vec2(50, 49))], 10.0)
    assert loop['segments'] == ['C:A#0>C:A#1', 'C:A#1>C:A#0']
    # the same loop traversed the other way round by the skeleton: the same ids
    loop2 = CI.resolve_runs({'anchors': A, 'walls': walls},
                            [_desc(True, None, [(0.0, Vec2(100, 49)), (50.0, Vec2(0, 49))], Vec2(50, 49), ccw=False)], 10.0)
    assert loop2['segments'] == loop['segments']
    # an ordinary run: the unordered pair; a corner-less loop: the wall it lies in
    one = CI.resolve_runs({'anchors': A, 'walls': walls},
                          [_desc(False, [('J', Vec2(1, 9)), ('E', Vec2(99, 40))], [(50.0, Vec2(4, 46))], Vec2(50, 30))], 10.0)
    assert one['segments'] == ['?E@A#0|C:A#0', 'C:A#0|J1'] and one['unresolved'] == 1, \
        'a dead end nothing architectural explains is reported, never invented'
    far = CI.resolve_runs({'anchors': A, 'walls': walls},
                          [_desc(False, [('J', Vec2(1, 9)), ('J', Vec2(99, 9))], [(50.0, Vec2(50, 49))], Vec2(50, 30))], 10.0)
    assert far['unresolved'] == 1 and far['runs'][0]['corners'] == ['?corner@A#0'], \
        'a corner with no wall corner / junction within reach is reported'
    ring = CI.resolve_runs({'anchors': [], 'walls': walls}, [_desc(True, None, [], Vec2(50, 9))], 10.0)
    assert ring['segments'] == ['loop:A']


def test_diff_reports_births_and_deaths_per_kind():
    a = {'anchors': [{'id': 'J1', 'kind': 'junction'}, {'id': 'C:R#0', 'kind': 'corner'}],
         'domains': [{'id': 'D', 'segments': ['C:R#0|J1']}]}
    b = {'anchors': [{'id': 'C:R#0', 'kind': 'corner'}, {'id': 'O:o1:a', 'kind': 'jamb'}],
         'domains': [{'id': 'D', 'segments': ['C:R#0|O:o1:a']}]}
    assert CI.diff(a, b) == {'junction': {'born': [], 'ceased': ['J1']},
                             'jamb': {'born': ['O:o1:a'], 'ceased': []},
                             'segment': {'born': ['C:R#0|O:o1:a'], 'ceased': ['C:R#0|J1']}}
    assert CI.diff(a, a) == {} and CI.diff(None, a)['junction'] == {'born': ['J1'], 'ceased': []}
