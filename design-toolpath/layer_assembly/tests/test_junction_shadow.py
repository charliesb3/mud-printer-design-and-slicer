"""
M4 — persistent junction records in SHADOW mode (design_proto/junctions.py,
layer_assembly/junction_shadow.py; WALL_ARCHITECTURE.md §4, §F).

Detected from the design geometry (the Trim contact primitives), persisted
in the project file with stable ids, legacy junction overrides mapped, and
compared with the PRODUCTION topology (wall-network face contacts) — which
still builds every junction: nothing here changes geometry (the golden
suite, test_golden.py, proves it byte for byte).
"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, '..', '..', 'design_proto'), os.path.join(HERE, '..', '..'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('LAYER_ASSEMBLY_WORKERS', '0')

import junctions as J                                          # noqa: E402
import project_file as PF                                      # noqa: E402
from golden import cases as GC                                 # noqa: E402
from golden.harness import load_project                        # noqa: E402
from layer_design import DesignLibrary                         # noqa: E402

GOLDEN = os.path.join(HERE, 'golden', 'projects')


def _open(proj):
    """A case project through the real save → open path (schema 7)."""
    return PF.load(PF.dumps(PF.save(proj)))['project']


def _golden(name):
    return load_project(os.path.join(GOLDEN, name + '.muddesign'))[0]


def _sig(r):
    return sorted((m['wall'], m['at']) for m in r['members'])


def _by_sig(reg):
    return {json.dumps(_sig(r)): r for r in reg['records']}


def _doc(P, did='base'):
    return DesignLibrary(P['designer']['designs'], P.get('wall_systems') or [], True).document(did)


# ---- detection: members + roles, never faces / XY --------------------------------------

def test_two_wall_tee_and_hub_junctions_by_member_walls_and_roles():
    P = _golden('syn_branch_at_junction')            # circle × rectangle + a branch FROM their junction
    recs = P['junctions']['records']
    kinds = sorted(r['kind'] for r in recs)
    assert kinds == ['cross', 'hub', 'tee'], kinds
    hub = next(r for r in recs if r['kind'] == 'hub')
    assert _sig(hub) == [('C', 'through'), ('L', 'start'), ('R', 'through')], 'a 3-wall junction: two through, one end'
    tee = next(r for r in recs if r['kind'] == 'tee')
    assert _sig(tee) == [('C', 'through'), ('L', 'end')]
    cross = next(r for r in recs if r['kind'] == 'cross')
    assert _sig(cross) == [('C', 'through'), ('R', 'through')]
    for r in recs:
        assert all(0.0 <= m['s'] < 1.0 for m in r['members'] if m['at'] == 'through'), 'through members carry an arc HINT'
        assert all('s' not in m for m in r['members'] if m['at'] != 'through')
        assert r['owner'] == 'base' and r['states'] == ['base'] and r['shape'] == 'inherit'
    assert P['junctions']['mode'] == 'trims'          # (M5: authoritative for trim boundaries)


def test_two_walls_meeting_twice_are_two_junctions_told_apart_by_their_hints():
    P = _golden('syn_circrect_hollow')
    recs = P['junctions']['records']
    assert len(recs) == 2 and all(_sig(r) == [('C', 'through'), ('R', 'through')] for r in recs)
    s = sorted(next(m['s'] for m in r['members'] if m['wall'] == 'C') for r in recs)
    assert s[1] - s[0] > 0.1, 'the same member set twice: distinct arc hints on the circle'


def test_mixed_thickness_and_mixed_systems_need_nothing_equal():
    for name in ('syn_mixed_thickness', 'syn_mixed_systems'):
        P = _golden(name)
        assert len(P['junctions']['records']) == 2 and all(r['kind'] == 'tee' for r in P['junctions']['records'])
        c = J.consistency(_doc(P), P['junctions']['records'], 'base')
        assert c['consistent'] and len(c['agree']) == 2, (name, c)


# ---- stable identity across saves --------------------------------------------------------

def test_ids_are_stable_across_saves_edits_and_reordering():
    P = _open(GC.build_cases()['syn_circrect_chain'])            # circle × rectangle: two junctions
    def ids(Q):
        return {round(next(m['s'] for m in r['members'] if m['wall'] == 'C'), 1): r['id'] for r in Q['junctions']['records']}
    ids0 = ids(P)
    assert len(ids0) == 2
    # save → open → save: the same ids
    P2 = _open(P)
    assert ids(P2) == ids0
    # MOVE + RESHAPE a member (the circle grows; the junctions slide along it): ids kept
    P3 = copy.deepcopy(P2)
    doc = P3['designer']['designs'][0]['document']
    next(p for p in doc['source_paths'] if p['id'] == 'C')['radius'] = 44
    P3 = _open(P3)
    assert sorted(ids(P3).values()) == sorted(ids0.values()) and len(P3['junctions']['records']) == 2
    # (the reordered design's paths: ids kept)
    P3b = copy.deepcopy(P3)
    P3b['designer']['designs'][0]['document']['source_paths'].reverse()
    assert sorted(r['id'] for r in _open(P3b)['junctions']['records']) == sorted(ids0.values())
    # a NEW wall: a new id after the counter; a REMOVED contact: retired, never resurrected
    P4 = copy.deepcopy(P3)
    doc = P4['designer']['designs'][0]['document']
    doc['source_paths'].append(GC.line('N', (0, 20), (-30, 20), 'New'))      # from the rectangle's left edge
    P4 = _open(P4)
    newr = next(r for r in P4['junctions']['records'] if any(m['wall'] == 'N' for m in r['members']))
    assert J._num(newr['id']) > max(J._num(i) for i in ids0.values())
    P5 = copy.deepcopy(P4)
    doc = P5['designer']['designs'][0]['document']
    doc['source_paths'] = [p for p in doc['source_paths'] if p['id'] != 'N']
    reg, rep = J.build_registry(P5['designer']['designs'], P5['wall_systems'], P5['junctions'])
    assert rep['retired'] == [newr['id']] and newr['id'] not in {r['id'] for r in reg['records']}
    assert reg['counter'] >= J._num(newr['id']), 'a retired id is never reused'


# ---- Variants: additions / removals ------------------------------------------------------

def test_variant_added_wall_owns_its_junction_and_removed_walls_deactivate_theirs():
    P = _golden('syn_variant_walls')                 # d1 adds a bench wall N; d2 removes the buttress B
    recs = {r['id']: r for r in P['junctions']['records']}
    b = next(r for r in recs.values() if any(m['wall'] == 'B' for m in r['members']))
    n = next(r for r in recs.values() if any(m['wall'] == 'N' for m in r['members']))
    assert b['owner'] == 'base' and 'd1' in b['states'] and 'd2' not in b['states'], b
    assert n['owner'] == 'd1' and n['states'] == ['d1'], 'a Variant-added wall: its junction is owned by that Variant'


def test_a_variant_removing_one_member_of_a_three_wall_junction_keeps_it_as_two():
    base = GC.circrect([GC.line('L', (120, 80), (80, 40), 'Branch')])
    P = _open(GC.project('hub', base, [('d1', {'source_paths': {'L': None}}, {})]))
    hub = next(r for r in P['junctions']['records'] if r['kind'] == 'hub')
    assert hub['states'] == ['base', 'd1'], 'the same junction record, reduced to C × R in the Variant'
    assert len(P['junctions']['records']) == 3


# ---- legacy junction overrides mapped onto the records ------------------------------------

def test_existing_junction_overrides_map_to_their_junction_and_orphans_are_reported():
    base = GC.circrect([GC.line('L', (120, 80), (80, 40), 'Branch')])
    P = _open(GC.project('ov', base))
    doc = P['designer']['designs'][0]['document']
    obs = J.observe(_doc(P), production=True)
    corners = [j for j in obs['production'] if j.get('corner')]
    lr = next(j['corner'] for j in corners if set(j['walls']) == {'L', 'R'})
    doc['junction_overrides'] = [{'key': lr, 'treatment': 'round', 'radius': 4.0},
                                 {'key': 'X.wall+|Y.wall-#0', 'treatment': 'round', 'radius': 1.0}]
    reg, rep = J.build_registry(P['designer']['designs'], P['wall_systems'], P['junctions'])
    holder = [r for r in reg['records'] if r['overrides']]
    assert len(holder) == 1 and holder[0]['kind'] == 'hub' and holder[0]['overrides'][0]['corner'] == lr, holder
    assert holder[0]['shape'] in ('per_corner', {'style': 'round', 'radius': 4.0})
    assert [o['corner'] for o in rep['orphan_overrides']] == ['X.wall+|Y.wall-#0']


# ---- consistency against production ---------------------------------------------------

@pytest.mark.parametrize('name', sorted(f[:-10] for f in os.listdir(GOLDEN) if f.endswith('.muddesign')))
def test_shadow_records_agree_with_production_topology(name):
    """Every shadow junction has production nodes of its walls. Production
    contacts WITHOUT a source-line contact are the known face-contact cases
    (bands overlapping where the circle runs inside the rectangle's wall);
    a shadow junction is missing in production only where it lies on a
    trimmed-away stretch (M5)."""
    P = _golden(name)
    lib = DesignLibrary(P['designer']['designs'], P.get('wall_systems') or [], True)
    for d in P['designer']['designs']:
        c = J.consistency(lib.document(d['id']), P['junctions']['records'], d['id'])
        # (M5) a wall CROSSING a trimmed-away stretch meets the circle / rectangle
        # SOURCE lines there — architectural junctions — but the material is
        # removed, so production has no node: valid, and the trims stay
        shadow = {'syn_branch_across_trim': [['C', 'L'], ['L', 'R']],
                  'syn_branch_at_junction': [['C', 'L']]}.get(name, [])     # (its far end lands on C's removed arc)
        assert sorted(x['walls'] for x in c['shadow_only']) == shadow, (d['id'], c['shadow_only'])
        assert all(e['id'] for e in c['agree']), 'every observed junction is a stored record'
        # (M4 found C × R band-overlap nodes in the two branch cases: they were
        # the overlap of the UNRESOLVED trims — M5 resolves them, none remain)
        assert not c['production_only'], (d['id'], c['production_only'])


# ---- through Z: moving, ceasing, appearing (valid topology) ---------------------------------

def _presence(P):
    from layer_assembly.junction_shadow import presence
    return presence(P)['layers']


def test_moving_junctions_are_followed_without_any_distance_limit():
    L = _presence(_golden('syn_transform_multi'))
    assert not L['events'] and all(r['present'] == [[0, 15]] for r in L['records'].values())
    assert max(r['max_move'] for r in L['records'].values()) > 2.0, 'the junctions move with the transformed walls'


def test_a_junction_ceases_where_transformed_walls_stop_intersecting():
    P = _golden('syn_transform_multi')
    st = P['assembly']['state']
    st['centers'][0]['x'] = 160                      # the circle shrinks away from the rectangle
    st['transform']['scale_in'] = 2.0
    L = _presence(P)
    cr = [r['id'] for r in P['junctions']['records'] if _sig(r) == [('C', 'through'), ('R', 'through')]]
    ceases = {e['junction']: e['layers'] for e in L['events'] if e['event'] == 'ceases'}
    assert set(ceases) == set(cr) and all(a > 0 and b == 15 for a, b in ceases.values()), L['events']
    assert all(L['records'][i]['present'][0][0] == 0 for i in cr), 'present below, absent above — never stretched'
    tee = next(r['id'] for r in P['junctions']['records'] if r['kind'] == 'tee')
    assert L['records'][tee]['present'] == [[0, 15]]


def test_a_junction_appears_where_transformed_walls_start_to_intersect():
    P = _golden('syn_transform_multi')
    doc = P['designer']['designs'][0]['document']
    next(p for p in doc['source_paths'] if p['id'] == 'C')['cx'] = 170   # apart in the Designer
    P = _open(P)
    st = P['assembly']['state']
    st['centers'][0].update(x=210, y=40)
    st['transform']['scale_in'] = -2.0               # it grows towards the rectangle
    L = _presence(P)
    app = [e for e in L['events'] if e['event'] == 'appears']
    assert app and app[0]['members'] == [['C', 'through'], ['R', 'through']] and app[0]['layers'][0] > 0, L['events']


def test_planar_cut_removing_a_wall_ends_its_junction_in_the_real_project():
    L = _presence(_golden('real_10xtest1'))
    assert {e['junction'] for e in L['events'] if e['event'] == 'ceases'} == {'J1', 'J2', 'J3'}
    P = _golden('real_10xtest1')
    P['assembly']['state']['profiles'] = []
    assert not _presence(P)['events'], 'without its planar top cuts every junction runs the full stack'


# ---- shadow only: nothing reads the records ------------------------------------------------

def test_the_registry_changes_no_document_and_no_geometry():
    P = _golden('syn_branch_at_junction')
    Q = copy.deepcopy(P)
    Q['junctions'] = {'mode': 'trims', 'counter': 99, 'records': []}
    a, b = _doc(P), _doc(Q)
    assert a == b and 'junctions' not in a
    from app import _deserialise_layer
    la, lb = _deserialise_layer(a), _deserialise_layer(b)
    pa, _ = la._build_effective()
    pb, _ = lb._build_effective()
    assert json.dumps([x.to_dict() for x in pa], sort_keys=True) == json.dumps([x.to_dict() for x in pb], sort_keys=True)


def test_shadow_report_endpoint():
    from app import app
    P = _golden('syn_transform_multi')
    r = app.test_client().post('/api/assembly/junctions_shadow', json={'project': P})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert [x['id'] for x in d['registry']['records']] == [x['id'] for x in P['junctions']['records']], 'stored ids kept'
    assert d['consistency']['base']['consistent'] and set(d['layers']['records']) == {x['id'] for x in P['junctions']['records']}
    assert app.test_client().post('/api/assembly/junctions_shadow', json={'project': {}}).status_code == 400
