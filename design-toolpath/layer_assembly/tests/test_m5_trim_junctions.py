"""
M5 (2026-10-15, WALL_ARCHITECTURE.md §4 / §10): PERSISTENT JUNCTIONS ARE
AUTHORITATIVE FOR TRIM BOUNDARIES.

Primary failure (before M5): circle C and rectangle R overlap, both overlap
sections trimmed → one continuous perimeter. Drawing a branch L to one of the
two C × R junctions changed the set of sources bounding the trimmed sections
({R} → {R, L}); the trims' signatures no longer matched, both became
'unresolved' and the removed sections REAPPEARED. A wall crossing a removed
section split it, with the same result.

Now a trim's boundaries are junctions (trim._by_junctions): a member joining
or leaving a junction, or a wall crossing the removed stretch, changes
nothing; the junction keeps its persistent id (junctions.compatible).
"""
import copy
import json
import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for p in (os.path.join(ROOT, 'design_proto'), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from layer_assembly.tests.golden import cases as CS  # noqa: E402

C_GONE = (80.0, 40.0)      # the circle's removed arc (inside R) — its midpoint
R_GONE = (120.0, 40.0)     # the rectangle's removed edge (inside C) — its midpoint
J_TOP = (120.0, 80.0)      # one C × R junction (R's corner, C's top)


@pytest.fixture(scope='module')
def client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


@pytest.fixture(scope='module')
def base_doc():
    """circle + rectangle, 10 in walls, both overlap sections trimmed (no extra walls)."""
    return CS.circrect()


def _with(doc, *paths, drop=()):
    d = copy.deepcopy(doc)
    d['source_paths'] = [p for p in d['source_paths'] if p['id'] not in drop] + [copy.deepcopy(p) for p in paths]
    return d


def _resolve(client, doc):
    r = client.post('/api/effective_paths', json=dict(doc, envelope=True)).get_json()
    return r, r['network']['trims']


def _near(env, src, pt, r):
    """an envelope point of wall `src` within r of pt"""
    for e in env:
        for q, s in zip(e.get('pts') or [], e.get('src') or []):
            if s == src and math.hypot(q[0] - pt[0], q[1] - pt[1]) < r:
                return True
    return False


def _removed_gone(r):
    env = r.get('envelope') or []
    return not _near(env, 'C', C_GONE, 6.0) and not _near(env, 'R', R_GONE, 6.0)


def _iv(st):
    return {k: (v['status'], round(v['a'], 6), round(v['b'], 6),
                [round(x, 6) for x in v['pa']], [round(x, 6) for x in v['pb']]) for k, v in st.items()}


THIN_BRANCH = CS.line('L', J_TOP, (120, 130), 'Branch')
THICK_BRANCH = CS.line('L', J_TOP, (120, 130), 'Branch', {'thickness': 6.0, 'align': 'center', 'print_reference': False})
CROSS = CS.line('X', (60, 40), (150, 40), 'Cross')


# 1 ------------------------------------------------------------------------------
def test_circle_rectangle_trims_unchanged(client, base_doc):
    r, st = _resolve(client, base_doc)
    assert {k: v['status'] for k, v in st.items()} == {'tR': 'ok', 'tC': 'ok'}
    assert not any(v.get('via') for v in st.values())              # exact (pre-M5) resolution
    assert _removed_gone(r)


# 2 / 3 --------------------------------------------------------------------------
@pytest.mark.parametrize('branch', [THIN_BRANCH, THICK_BRANCH], ids=['thin', 'thick'])
def test_branch_at_an_existing_junction_keeps_the_trims(client, base_doc, branch):
    _, st0 = _resolve(client, base_doc)
    r, st = _resolve(client, _with(base_doc, branch))
    assert {k: v['status'] for k, v in st.items()} == {'tR': 'ok', 'tC': 'ok'}
    assert all(v.get('via') == 'junctions' for v in st.values())   # (the old signatures no longer match)
    assert _iv(st) == _iv(st0), 'the SAME stretches removed, ending at the same junction points'
    assert _removed_gone(r), 'the trimmed-away circle / rectangle sections did not reappear'
    secs = r['network']['trim_sections']
    assert any(s['source'] == 'C' and s['trimmed_by'] == 'tC' for s in secs)
    assert any('L' in s['start'] + s['end'] for s in secs if s['source'] in ('C', 'R')), 'L is a member there now'


# 4 ------------------------------------------------------------------------------
def test_branch_crossing_a_removed_section_does_not_resurrect_it(client, base_doc):
    _, st0 = _resolve(client, base_doc)
    r, st = _resolve(client, _with(base_doc, CROSS))
    assert {k: v['status'] for k, v in st.items()} == {'tR': 'ok', 'tC': 'ok'}
    assert _iv(st) == _iv(st0)
    assert _removed_gone(r)
    # the crossing split each removed stretch into two current sections: BOTH are trimmed
    by = {}
    for s in r['network']['trim_sections']:
        by.setdefault(s['source'], []).append(s['trimmed_by'])
    assert by['C'].count('tC') == 2 and by['R'].count('tR') == 2


# 5 ------------------------------------------------------------------------------
def test_three_and_more_members_at_one_junction(client, base_doc):
    _, st0 = _resolve(client, base_doc)
    M = CS.line('M', J_TOP, (170, 120), 'Branch 2')
    r, st = _resolve(client, _with(base_doc, THIN_BRANCH, M))
    assert _iv(st) == _iv(st0) and _removed_gone(r)
    reg = _registry(_with(base_doc, THIN_BRANCH, M))
    hub = [x for x in reg['records'] if {m['wall'] for m in x['members']} == {'C', 'R', 'L', 'M'}]
    assert len(hub) == 1 and hub[0]['kind'] == 'hub'


# 6 / 9 — identity through Save / Open: join, delete, cross ---------------------------
def _project(doc, variants=()):
    return CS.project('m5', doc, list(variants))


def _roundtrip(P):
    import project_file as PF
    return PF.load(PF.dumps(PF.save(copy.deepcopy(P))))['project']


def _registry(doc):
    return _roundtrip(_load(_project(doc)))['junctions']


def _load(P6):
    """open a case project (built in the schema it was frozen in, 6) as a file"""
    import project_file as PF
    return PF.load(json.dumps({'format': PF.FORMAT, 'schema_version': 6, 'project': P6}))['project']


def _ids(P):
    return {r['id']: sorted(m['wall'] for m in r['members']) for r in P['junctions']['records']}


def _links(P, design='base'):
    d = next(x for x in P['designer']['designs'] if x['id'] == design)
    return {t['id']: t.get('junctions') for t in d['document']['trims']}


def test_junction_and_trim_identity_survive_join_delete_and_save_open(base_doc):
    P = _roundtrip(_load(_project(base_doc)))
    ids0, links0 = _ids(P), _links(P)
    assert P['junctions']['mode'] == 'trims'
    assert sorted(ids0.values()) == [['C', 'R'], ['C', 'R']]
    assert set(links0) == {'tR', 'tC'} and all(len(v) == 2 and all(v) for v in links0.values())
    # a branch JOINS the junction at (120, 80): the same id, one more member
    P['designer']['designs'][0]['document']['source_paths'].append(copy.deepcopy(THIN_BRANCH))
    P = _roundtrip(P)
    hub = [j for j, w in _ids(P).items() if 'L' in w and 'C' in w]
    assert len(hub) == 1 and hub[0] in ids0 and _ids(P)[hub[0]] == ['C', 'L', 'R'], \
        'the junction GAINED a member — not renamed'
    assert _links(P) == links0, 'the trims still reference the same junctions'
    assert set(_ids(P)) >= set(ids0), 'no junction retired'
    # Save / Open again: unchanged
    Q = _roundtrip(P)
    assert _ids(Q) == _ids(P) and _links(Q) == _links(P)
    # the branch DELETED: the junction is a 2-member junction again, same id; trims valid
    P['designer']['designs'][0]['document']['source_paths'] = [
        p for p in P['designer']['designs'][0]['document']['source_paths'] if p['id'] != 'L']
    P = _roundtrip(P)
    assert _ids(P) == ids0 and _links(P) == links0


# 7 ------------------------------------------------------------------------------
def test_variant_only_branch_joins_a_base_junction(client, base_doc):
    import layer_design as LD
    from layer_design import DesignLibrary
    v = ('d1', {'source_paths': {'L': copy.deepcopy(THIN_BRANCH)}}, {})
    P = _roundtrip(_load(CS.project('m5v', base_doc, [v])))
    designs = P['designer']['designs']
    lib = DesignLibrary(designs, P.get('wall_systems') or [], True)
    _, st_base = _resolve(client, lib.document('base'))
    r1, st_v = _resolve(client, lib.document('d1'))
    assert all(x['status'] == 'ok' and not x.get('via') for x in st_base.values()), 'Base untouched'
    assert all(x['status'] == 'ok' and x.get('via') == 'junctions' for x in st_v.values())
    assert _iv(st_v) == _iv(st_base) and _removed_gone(r1)
    rec = [r for r in P['junctions']['records'] if {m['wall'] for m in r['members']} == {'C', 'R', 'L'}]
    assert len(rec) == 1 and rec[0]['owner'] == 'base' and rec[0]['states'] == ['base', 'd1'], \
        'ONE junction (owned by Base), the Variant-only wall a member in the Variant'
    assert not LD.divergences(designs), 'no Variant-owned physical properties'
    d1 = next(d for d in designs if d['id'] == 'd1')
    assert set((d1.get('patch') or {})) <= {'source_paths'} and not d1.get('settings'), \
        'the Variant stores only its layout (the added wall)'


# 8 ------------------------------------------------------------------------------
def test_variant_removing_a_junction_member(client, base_doc):
    from layer_design import DesignLibrary
    doc = _with(base_doc, THIN_BRANCH)
    # (a) the Variant removes the branch: a 2-member junction there, the same record; trims valid
    va = ('d1', {'source_paths': {'L': None}}, {})
    # (b) the Variant removes R: the circle's trim is INACTIVE there (its junction does not exist) — reported
    vb = ('d2', {'source_paths': {'R': None}}, {})
    P = _roundtrip(_load(CS.project('m5r', doc, [va, vb])))
    lib = DesignLibrary(P['designer']['designs'], P.get('wall_systems') or [], True)
    _, sa = _resolve(client, lib.document('d1'))
    assert all(x['status'] == 'ok' for x in sa.values())
    hub = [r for r in P['junctions']['records'] if {m['wall'] for m in r['members']} == {'C', 'R', 'L'}]
    assert len(hub) == 1 and set(hub[0]['states']) == {'base', 'd1', 'd2'}, \
        'one record: C + R in d1, C + L in d2 (R removed there — L still meets the circle)'
    _, sb = _resolve(client, lib.document('d2'))
    assert sb['tR']['status'] == 'missing source'
    assert sb['tC']['status'] == 'inactive' and 'R' in sb['tC']['reason'], sb['tC']
    # Base unaffected by either Variant
    _, s0 = _resolve(client, lib.document('base'))
    assert all(x['status'] == 'ok' for x in s0.values())


# G3 — a deleted / moved-away boundary is REPORTED, never silently re-attached ----------
def test_a_boundary_junction_that_no_longer_exists_is_reported(client, base_doc):
    _, st = _resolve(client, _with(base_doc, drop=('R',)))
    assert st['tC']['status'] == 'inactive' and 'R' in st['tC']['reason']
    far = copy.deepcopy(base_doc)
    far['source_paths'][1]['cx'] = 400          # the circle moved away: no C × R junction at all
    r, st = _resolve(client, far)
    assert {k: v['status'] for k, v in st.items()} == {'tR': 'unresolved', 'tC': 'unresolved'}
    assert all('no longer' in v['reason'] for v in st.values())


def test_a_trim_moved_with_its_junctions_follows_ordinary_movement(client, base_doc):
    _, st0 = _resolve(client, base_doc)
    moved = _with(base_doc, THIN_BRANCH)
    for p in moved['source_paths']:             # everything shifted together: the same relationships
        if p['type'] == 'RectanglePath':
            p['x'] += 7
        elif p['type'] == 'CirclePath':
            p['cx'] += 7
        else:
            p['start'][0] += 7
            p['end'][0] += 7
    r, st = _resolve(client, moved)
    assert {k: v['status'] for k, v in st.items()} == {'tR': 'ok', 'tC': 'ok'}
    assert [round(st[k]['b'] - st[k]['a'], 6) for k in ('tR', 'tC')] == \
        [round(st0[k]['b'] - st0[k]['a'], 6) for k in ('tR', 'tC')]


# the diagnostics (manual review) --------------------------------------------------
def test_diagnostics_name_the_junctions_each_trim_is_bounded_by(client, base_doc):
    from app import app
    P = _load(_project(_with(base_doc, THIN_BRANCH)))
    r = app.test_client().post('/api/assembly/junctions_shadow', json={'project': P}).get_json()
    t = r['trims']['base']
    assert t['tC']['status'] == 'ok' and t['tC']['via'] == 'junctions'
    ids = {x['id']: sorted(m['wall'] for m in x['members']) for x in r['registry']['records']}
    assert sorted(ids[j] for j in t['tC']['junctions']) == [['C', 'L', 'R'], ['C', 'R']]
    stored = next(x for x in P['designer']['designs'][0]['document']['trims'] if x['id'] == 'tC')
    assert stored['junctions'] == t['tC']['junctions'], 'saved on the trim: the same junction ids'
