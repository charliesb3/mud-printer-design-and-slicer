"""
M6 (2026-10-16, WALL_ARCHITECTURE.md §4 "E", §10): SOURCE-WALL ATTACHMENT.

Old failure (first divergence): a branch drawn to an architectural wall — its
end on the host's SOURCE line — was physically joined to whichever offset
FACE its own line touched. For an Inside / Outside (incl. legacy Auto) host
the source line IS a physical face: a branch from the other side ran through
the wall material and split that FAR face (the user's `test 3` projects: the
rectangle's exterior face was cut by an interior line).

Now (model._attach_branches): the attachment is architectural — (branch, end,
host wall(s), station, side) — and the branch's PHYSICAL end is derived from
the host's envelope: the middle of its band (where several hosts meet: where
their band centres meet). The wall network then meets the face on the
branch's own side. Centered hosts: the source line is the band centre —
nothing moves, identical geometry.
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

T = 10.0
FACES = {'center': (-5.0, 5.0), 'inside': (0.0, 10.0), 'outside': (-10.0, 0.0)}   # room's bottom edge: (exterior y, interior y)
THICK6 = {'thickness': 6.0, 'align': 'center', 'print_reference': False}


@pytest.fixture(scope='module')
def client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _env(align, thickness=T):
    return {'thickness': thickness, 'align': align, 'print_reference': False}


def _room(align, *extra, room=(0, 0, 120, 80)):
    R = CS.rect('R', *room, 'Room')
    R['envelope'] = _env(align)
    d = CS.doc([R] + [copy.deepcopy(e) for e in extra])
    d.pop('wall_groups', None)
    return d


def _post(client, d, envelope=True, systems=()):
    body = dict(d, envelope=True) if envelope else dict(d, project_wall_systems=list(systems))
    return client.post('/api/effective_paths', json=body).get_json()


def _rings(r):
    def area(q):
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(q, q[1:] + q[:1]))) / 2
    return sorted([e for e in r['envelope'] if e['role'] == 'ring'], key=lambda e: -area(e['pts']))


def _run_along_y(polys, y, x0=50, x1=70, tol=0.05):
    """a straight run of a polyline along y = y covering x0 … x1 (the face intact there)"""
    for p in polys:
        q = p['pts']
        closed = p.get('closed', True)
        for a, b in zip(q, q[1:] + ([q[0]] if closed else [])):
            if abs(a[1] - y) < tol and abs(b[1] - y) < tol and min(a[0], b[0]) <= x0 and max(a[0], b[0]) >= x1:
                return True
    return False


def _geo(r):
    return sorted(json.dumps([[round(a, 4), round(b, 4)] for a, b in p['pts']]) for p in r['printable'])


# ---- the core: a branch drawn to the WALL meets the face on its own side --------------------

@pytest.mark.parametrize('align', ['center', 'inside', 'outside'])
@pytest.mark.parametrize('benv', [None, THICK6], ids=['thin', 'thick'])
@pytest.mark.parametrize('inward', [True, False], ids=['from-interior', 'from-exterior'])
def test_branch_drawn_to_the_source_line_never_touches_the_far_face(client, align, benv, inward):
    ext, inn = FACES[align]
    end = (60, 50) if inward else (60, -50)
    r = _post(client, _room(align, CS.line('B', (60, 0), end, 'Branch', benv)))
    rings = [e for e in r['envelope'] if e['role'] == 'ring']
    far, near = (ext, inn) if inward else (inn, ext)
    assert _run_along_y(rings, far), 'the opposite face is untouched'
    assert not _run_along_y(rings, near), 'the branch joins the face on its own side'
    att = r['network'].get('attachments') or []
    if align == 'center' or (inward and align == 'outside') or (not inward and align == 'inside'):
        assert not att, 'Centered, or the source line IS the near face: the authored end already meets it — nothing derived'
    else:
        assert len(att) == 1 and att[0]['status'] == 'attached' and att[0]['hosts'] == ['R']
        assert att[0]['branch'] == 'B' and att[0]['end'] == 'start' and 'face' not in json.dumps(att[0]), \
            'the attachment names walls (branch / host), never an offset face'


@pytest.mark.parametrize('align', ['inside', 'outside'])
@pytest.mark.parametrize('system', [None, ('skin_web', {}), ('interleaved', {'paths': 4}), ('parallel', {'walls': 3}),
                                    ('chain', {}), ('linked', {'paths': 4})],
                         ids=['hollow', 'skin_web', 'interleaved', 'parallel', 'chain', 'linked'])
def test_drawn_to_the_wall_prints_exactly_like_drawn_to_its_near_face(client, align, system):
    """the derived physical end gives exactly the geometry of the same branch drawn
    to the visible face on its side — for every construction (thin + thick branch)"""
    ext, inn = FACES[align]
    ws = [] if system is None else [{'id': 'S1', 'name': 'S1', 'type': system[0], 'params': dict(system[1]),
                                     'web': {}, 'members': ['R']}]
    inward = align == 'inside'
    near = inn if inward else ext
    end = (60, 50) if inward else (60, -50)
    for benv in (None, THICK6):
        a = _post(client, _room(align, CS.line('B', (60, 0), end, 'Branch', benv)), envelope=False, systems=ws)
        b = _post(client, _room(align, CS.line('B', (60, near), end, 'Branch', benv)), envelope=False, systems=ws)
        assert _geo(a) == _geo(b)


def test_centered_hosts_are_byte_identical_to_before(client):
    """Centered: the source line is the band centre — no attachment is derived and
    nothing moves (golden: every Centered case unchanged)"""
    for benv in (None, THICK6):
        r = _post(client, _room('center', CS.line('B', (60, 0), (60, 50), 'Branch', benv)), envelope=False)
        assert 'attachments' not in r['network']


def test_curved_host(client):
    C = CS.circle('C', 0, 0, 60, 'Tower')
    C['envelope'] = _env('inside')
    d0 = CS.doc([C]); d0.pop('wall_groups', None)
    d1 = CS.doc([C, CS.line('B', (60, 0), (-20, 10), 'Spoke')]); d1.pop('wall_groups', None)   # (60, 0): on the sampled circle
    r0, r1 = _post(client, d0), _post(client, d1)
    assert json.dumps(_rings(r1)[0]['pts']) == json.dumps(_rings(r0)[0]['pts']), 'the exterior circle is untouched'
    att = r1['network']['attachments']
    assert att[0]['hosts'] == ['C'] and math.hypot(*att[0]['physical_end']) == pytest.approx(55.0, abs=0.05), \
        'the physical end lies in the middle of the ring (r 55)'


# ---- junctions (circle + rectangle), M5 trims ------------------------------------------

@pytest.mark.parametrize('align', ['center', 'inside', 'outside'])
def test_branch_at_an_existing_trimmed_junction(client, align):
    base = CS.circrect()
    base.pop('wall_groups', None)
    for p in base['source_paths']:
        p['envelope'] = _env(align)
    r0 = _post(client, base)
    for end, keep, far in (((90, 50), 0, 'inside'), ((150, 120), -1, 'outside')):   # inward: exterior stays; outward: interior
        d = copy.deepcopy(base)
        d['source_paths'].append(CS.line('L', (120, 80), end, 'Branch'))
        r = _post(client, d)
        assert {k: v['status'] for k, v in r['network']['trims'].items()} == {'tC': 'ok', 'tR': 'ok'}, 'M5 trims kept'
        assert json.dumps(_rings(r)[keep]['pts']) == json.dumps(_rings(r0)[keep]['pts'])
        att = r['network'].get('attachments') or []
        if align != far:
            assert not att, 'Centered, or the source lines are the near faces: nothing derived'
        else:
            assert len(att) == 1 and sorted(att[0]['hosts']) == ['C', 'R'], 'joins BOTH walls of the junction'


def test_branch_on_a_trimmed_away_stretch_is_not_attached(client):
    """a wall ending on a removed stretch meets no wall there: no attachment, nothing moved"""
    base = CS.circrect()
    base.pop('wall_groups', None)
    for p in base['source_paths']:
        p['envelope'] = _env('inside')
    d = copy.deepcopy(base)
    d['source_paths'].append(CS.line('L', (80, 40), (40, 40), 'Into the removed arc'))
    r = _post(client, d)
    assert not (r['network'].get('attachments') or [])
    assert {k: v['status'] for k, v in r['network']['trims'].items()} == {'tC': 'ok', 'tR': 'ok'}


def test_an_incidental_crossing_is_never_an_attachment(client):
    r = _post(client, _room('inside', CS.line('X', (60, -30), (60, 50), 'Crossing')))
    assert not r['network'].get('attachments'), 'a line crossing the wall is not attached to it'


# ---- mixed thickness / systems / openings ----------------------------------------------

def test_mixed_thickness_and_systems(client):
    ws = [{'id': 'S1', 'name': 'S1', 'type': 'interleaved', 'params': {'paths': 4}, 'web': {}, 'members': ['R']},
          {'id': 'S2', 'name': 'S2', 'type': 'parallel', 'params': {'walls': 2}, 'web': {}, 'members': ['T']}]
    T6 = CS.line('T', (60, 0), (60, 50), 'Thick branch', THICK6)
    O = CS.line('O', (0, 40), (45, 40), 'Out-and-back')
    a = _post(client, _room('inside', T6, O), envelope=False, systems=ws)
    b = _post(client, _room('inside', CS.line('T', (60, 10), (60, 50), 'Thick branch', THICK6),
                            CS.line('O', (10, 40), (45, 40), 'Out-and-back')), envelope=False, systems=ws)
    assert _geo(a) == _geo(b), 'both branches meet the interior faces (one Interleaved host, a Parallel + a thin branch)'
    assert sorted(x['branch'] for x in a['network']['attachments']) == ['O', 'T']


def test_an_opening_on_the_branch_stays_where_it_was_drawn(client):
    """the start moved (arc positions on the branch shift): the opening keeps its XY place"""
    B = CS.line('B', (60, 0), (60, 60), 'Branch', THICK6)
    d = _room('inside', B)
    d['openings'] = [{'id': 'o1', 'source_path_id': 'B', 'center_s': 30.0, 'width': 8.0, 'end_treatment': 'inherit'}]
    r = _post(client, d, envelope=False)
    ys = [q[1] for p in r['printable'] if p['id'].startswith('B') for q in p['pts']]
    assert ys and not any(26.5 < y < 33.5 for y in ys), 'the doorway is still at y 26 … 34 (drawn at s 30 from y 0)'
    # an opening on the HOST next to the attachment is unaffected by it
    d2 = _room('inside', B)
    d2['openings'] = [{'id': 'o2', 'source_path_id': 'R', 'center_s': 80.0, 'width': 12.0, 'end_treatment': 'inherit'}]
    d3 = _room('inside', CS.line('B', (60, 10), (60, 60), 'Branch', THICK6))
    d3['openings'] = d2['openings']
    assert _geo(_post(client, d2, envelope=False)) == _geo(_post(client, d3, envelope=False))


# ---- Variants, Save / Open ---------------------------------------------------------------

def _load(P6):
    import project_file as PF
    return PF.load(json.dumps({'format': PF.FORMAT, 'schema_version': 6, 'project': P6}))['project']


def test_variant_only_branch(client):
    import layer_design as LD
    from layer_design import DesignLibrary
    base = _room('inside')
    v = ('d1', {'source_paths': {'B': CS.line('B', (60, 0), (60, 50), 'Branch')}}, {})
    P = _load(CS.project('m6v', base, [v]))
    lib = DesignLibrary(P['designer']['designs'], P.get('wall_systems') or [], True)
    rb, rv = _post(client, lib.document('base')), _post(client, lib.document('d1'))
    assert not (rb['network'].get('attachments') or []), 'Base: no branch, no attachment'
    assert rv['network']['attachments'][0]['hosts'] == ['R'], 'the attachment exists only in the Variant'
    assert json.dumps(_rings(rv)[0]['pts']) == json.dumps(_rings(rb)[0]['pts']), 'the exterior face is the same in both'
    assert not LD.divergences(P['designer']['designs']), 'the host keeps its own physical properties (no Variant copy)'
    d1 = next(d for d in P['designer']['designs'] if d['id'] == 'd1')
    assert set(d1.get('patch') or {}) == {'source_paths'} and set(d1['patch']['source_paths']) == {'B'}


def test_save_open_round_trip_reproduces_the_attachment(client):
    import project_file as PF
    P = _load(CS.project('m6s', _room('inside', CS.line('B', (60, 0), (60, 50), 'Branch'))))
    Q = PF.load(PF.dumps(PF.save(copy.deepcopy(P))))['project']
    doc_p, doc_q = P['designer']['designs'][0]['document'], Q['designer']['designs'][0]['document']
    assert 'attachments' not in json.dumps(doc_q), 'derived, never stored'
    a, b = _post(client, doc_p, envelope=False), _post(client, doc_q, envelope=False)
    assert _geo(a) == _geo(b) and a['network']['attachments'] == b['network']['attachments']
    rec = [r for r in Q['junctions']['records'] if {m['wall'] for m in r['members']} == {'R', 'B'}]
    assert len(rec) == 1 and {(m['wall'], m['at']) for m in rec[0]['members']} == {('R', 'through'), ('B', 'start')}, \
        'the persistent junction: the branch (start) on the wall R (through)'


# ---- the user's real projects (symptom E) -------------------------------------------------

@pytest.mark.parametrize('name', ['real_test3', 'real_test3b'])
def test_real_projects_exterior_face_no_longer_cut(client, name):
    from layer_assembly.tests.golden import harness as H
    from layer_design import DesignLibrary
    P, _ = H.load_project(os.path.join(H.PROJECTS, name + '.muddesign'))
    doc = DesignLibrary(P['designer']['designs'], P.get('wall_systems') or [], True).raw_document('base')
    r = _post(client, doc)
    x, y = 78.26087, 150.895141           # Line 1 starts on the rectangle's left edge (Auto = Inside, 10 in)
    rings = [e for e in r['envelope'] if e['role'] == 'ring']

    def run_at(X):
        for e in rings:
            q = e['pts']
            for a, b in zip(q, q[1:] + [q[0]]):
                if abs(a[0] - X) < 0.05 and abs(b[0] - X) < 0.05 and min(a[1], b[1]) < y - 3 and max(a[1], b[1]) > y + 3:
                    return True
        return False
    assert run_at(x), 'the exterior face runs straight past the line (it used to be cut there)'
    assert not run_at(x + 10), 'the line joins the interior face'
