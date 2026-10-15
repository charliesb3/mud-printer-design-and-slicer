"""MACHINE JOB EXPORT (machine_job.py; 2026-10-14 Pi P3, executable since the
current-machine pass).

The `.mudjob` file is the ONLY boundary to the printer controller (contract:
`contracts/mudjob/README.md`). These tests check, from the Design + Toolpath
side alone (no Pi code is imported), that the authoritative PrintPlan is
serialized into a conforming, EXECUTABLE document for the current machine:

- lift identities, order, layer / Z boundaries and frame repositions are the
  PrintPlan's own (nothing reconstructed);
- G-code: explicit inches (G20) + absolute (G90) at the start of every lift,
  XY-only G1 moves (no Z, no M / pump, no prime), feeds from the speeds,
  and its move sequence is exactly the plan's `items()` in print order;
- layer / Z metadata (`layer_marks`) without any Z command;
- the document conforms to the contract (checker written from the README);
- the export of the reference building equals the shared contract fixture
  that the Pi interface's tests load (golden; MUDJOB_UPDATE_FIXTURE=1
  rewrites it);
- the endpoint exports only an existing, current plan;
- `.muddesign` stays authoring-only (a job is not a project file).
"""
import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.lifts import LiftSettings                                      # noqa: E402
from layer_assembly.machine_job import ExportError, export_job, job_filename        # noqa: E402
from layer_assembly.print_plan import PrintSpeeds, build_plan, items                # noqa: E402

CONTRACT = Path(__file__).resolve().parents[3] / 'contracts' / 'mudjob'
FIXTURE = CONTRACT / 'fixtures' / 'designer-export-3-lifts.mudjob'
FIXED_TIME = '2026-10-14T00:00:00Z'
NAME = 'Two-island test building'
SPEEDS = PrintSpeeds(2.0, 6.0)          # in/s → F120 / F360 in/min

MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def rect(i, x, y=0.0, w=40.0, h=30.0):
    return {'id': i, 'type': 'RectanglePath', 'x': x, 'y': y, 'w': w, 'h': h, 'closed': True}


DESIGNS = [{'id': 'two', 'name': 'two', 'parent': None,
            'document': {'id': 'two', 'source_paths': [rect('R1', 0), rect('R2', 100)],
                         'material': dict(MAT), 'wall_systems': []}}]
# 6 layers of 1.5 in, lifts of 3 in → 3 lifts of 2 layers, two islands each
ASSEMBLY = {'layer_height': 1.5, 'sections': [{'design_id': 'two', 'height': 9.0}],
            'machine': {'max_z_travel': 18, 'lift_height': 3.0, 'head_clearance': 1}}


@pytest.fixture(scope='module')
def plan():
    from layer_assembly.web import _resolve_stack
    st = _resolve_stack({'assembly': ASSEMBLY, 'designs': DESIGNS})
    return build_plan(st['res'], st['src'], lift_settings=LiftSettings.from_dict(ASSEMBLY['machine']))


# ---------------------------------------------------------------------------
# contract conformance — written from contracts/mudjob/README.md
# ---------------------------------------------------------------------------

WORD = re.compile(r'([A-Z])\s*([-+]?[0-9.]+)')


def words(line):
    return [(a, float(b)) for a, b in WORD.findall(line.upper())]


def check_contract(doc):
    assert doc['format'] == 'mudprinter-job'
    assert doc['version'] == 1
    assert isinstance(doc['executable'], bool)
    if doc['executable']:
        assert not doc.get('unresolved')
    else:
        assert doc['unresolved'] and all(isinstance(u['id'], str) and isinstance(u['summary'], str)
                                         for u in doc['unresolved'])
    lifts = doc['lifts']
    assert isinstance(lifts, list) and lifts
    for pos, L in enumerate(lifts):
        assert L['index'] == pos
        if doc['executable']:
            g = L['gcode']
            assert isinstance(g, list) and g
            units = absolute = False
            for line in g:                                   # units + distance mode before any motion
                w = dict(words(line))
                units = units or w.get('G') in (20, 21) or 'G20' in line or 'G21' in line
                absolute = absolute or 'G90' in line or 'G91' in line
                if 'X' in w or 'Y' in w:
                    assert units and absolute, f'motion before units / distance mode in lift {pos}'
                assert 'Z' not in w, 'no Z words (current machine)'
                assert not re.search(r'M0*(0|1|2|30)(?!\d)', line.upper()) and not line.startswith(('%', '$'))
        else:
            assert 'gcode' not in L
        if pos == 0:
            assert 'reposition_before' not in L
        else:
            assert isinstance(L['reposition_before'], dict)
    json.dumps(doc)                                  # plain JSON


def moves(gcode):
    """[(x, y, feed)] of the G1 moves — an independent reading of the G-code."""
    out, feed = [], None
    for line in gcode:
        w = dict(words(line))
        if w.get('G') == 1:
            feed = w.get('F', feed)
            out.append((w['X'], w['Y'], feed))
    return out


def expected_moves(plan, lift):
    """The plan's own move sequence for one lift (items() in print order),
    rounded like the G-code, consecutive duplicates dropped."""
    out, last = [], None
    r = lambda v: round(v, 4)                                   # noqa: E731
    def add(p, f):
        nonlocal last
        q = (r(p[0]), r(p[1]))
        if q != last:
            out.append((q[0], q[1], f))
            last = q
    first = True
    for it in items(plan):
        kind, pts = it[0], it[3]
        if it[9] != lift or kind == 'frame':
            continue
        if kind in ('travel', 'transition'):
            add(pts[-1], SPEEDS.travel_speed * 60)
        else:
            if first:
                add(pts[0], SPEEDS.travel_speed * 60)
            for p in pts[1:]:
                add(p, SPEEDS.print_speed * 60)
        first = False
    return out


# ---------------------------------------------------------------------------

class TestExport:
    def test_lifts_are_the_plans_lifts(self, plan):
        assert len(plan.lifts) == 3
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        assert [L['index'] for L in doc['lifts']] == [L.index for L in plan.lifts] == [0, 1, 2]
        for J, L in zip(doc['lifts'], plan.lifts):
            assert (J['layers']['first'], J['layers']['last']) == (L.first_layer, L.last_layer)
            assert J['layers']['count'] == L.last_layer - L.first_layer + 1
            assert (J['z_bottom'], J['z_top']) == (pytest.approx(L.z_bottom), pytest.approx(L.z_top))
            assert J['stages'] == len(L.stages) and J['passes'] == len(L.passes)
        # boundaries: contiguous, whole layers, every plan layer exactly once
        firsts = [J['layers']['first'] for J in doc['lifts']]
        lasts = [J['layers']['last'] for J in doc['lifts']]
        assert firsts[0] == 0 and lasts[-1] == len(plan.layers) - 1
        assert all(f == l + 1 for f, l in zip(firsts[1:], lasts))

    def test_frame_repositions_are_the_plans_frame_moves(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        assert 'reposition_before' not in doc['lifts'][0]
        for J, L in zip(doc['lifts'][1:], plan.lifts[1:]):
            fm, R = L.reposition_in, J['reposition_before']
            assert R['from_lift'] == fm.from_lift == L.index - 1
            assert R['rise'] == pytest.approx(fm.rise)
            assert (R['frame_z_from'], R['frame_z_to']) == (pytest.approx(fm.frame_z_from), pytest.approx(fm.frame_z_to))
            assert R['nozzle_at'] == [pytest.approx(fm.at[0]), pytest.approx(fm.at[1])]

    def test_summary_numbers_come_from_the_plan(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        for J, L in zip(doc['lifts'], plan.lifts):
            runs = [r for i in L.passes for r in plan.passes[i].runs]
            assert J['runs'] == len(runs)
            assert J['print_length'] == pytest.approx(sum(r.print_length + r.retrace_length for r in runs), abs=1e-3)
        assert doc['units'] == plan.units == 'in'
        assert doc['source']['layers'] == len(plan.layers)
        assert doc['source']['lift_settings'] == plan.settings

    def test_executable_for_the_current_machine(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        assert doc['executable'] is True and 'unresolved' not in doc
        assert doc['units'] == 'in'
        assert doc['machine']['axes'] == 'XY' and doc['machine']['z'] == 'manual'
        assert doc['machine']['feeds_in_min'] == {'print': 120.0, 'travel': 360.0}

    def test_every_lift_sets_inches_and_absolute_first(self, plan):
        for L in export_job(plan, SPEEDS, generated_at=FIXED_TIME)['lifts']:
            assert L['gcode'][:2] == ['G20', 'G90']
            assert all(line.startswith('G1 X') for line in L['gcode'][2:])

    def test_xy_only_no_z_no_pump_no_prime(self, plan):
        for L in export_job(plan, SPEEDS, generated_at=FIXED_TIME)['lifts']:
            text = '\n'.join(L['gcode']).upper()
            assert 'Z' not in text and 'M' not in text and 'G0 ' not in text and '$' not in text
            assert 'G28' not in text and 'G30' not in text and 'G92' not in text and 'G10' not in text

    def test_moves_are_the_plans_moves_in_order(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        for L in doc['lifts']:
            got = moves(L['gcode'])
            assert got == [(x, y, pytest.approx(f)) for x, y, f in expected_moves(plan, L['index'])]
            assert len(got) > 4

    def test_each_lift_starts_with_a_travel_to_its_first_point(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        for L, PL in zip(doc['lifts'], plan.lifts):
            first_pass = plan.passes[PL.passes[0]]
            x, y, f = moves(L['gcode'])[0]
            target = first_pass.transition_in.end if first_pass.transition_in else first_pass.start
            assert (x, y) == (round(target[0], 4), round(target[1], 4)) and f == 360.0

    def test_layer_marks_carry_z_without_commanding_it(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME)
        for L, PL in zip(doc['lifts'], plan.lifts):
            marks = L['layer_marks']
            assert [m['pass'] for m in marks] == PL.passes
            lines = [m['line'] for m in marks]
            assert lines == sorted(lines) and lines[0] == 2 and lines[-1] < len(L['gcode'])
            for m in marks:
                P = plan.passes[m['pass']]
                assert m['layer'] == P.layer and m['z'] == pytest.approx(P.z)
                assert m['z_in_lift'] == pytest.approx(P.z - PL.z_bottom)

    def test_bounds_and_speed_source(self, plan):
        doc = export_job(plan, SPEEDS, generated_at=FIXED_TIME, placeholder_speeds=True)
        b = doc['xy_bounds']
        pts = [p for it in items(plan) if it[0] != 'frame' for p in it[3]]
        assert b['min_x'] == pytest.approx(min(p[0] for p in pts)) and b['max_y'] == pytest.approx(max(p[1] for p in pts))
        assert doc['source']['placeholder_speeds'] is True
        assert doc['source']['speeds_in_s'] == {'print': 2.0, 'travel': 6.0}

    def test_feeds_follow_the_speeds(self, plan):
        doc = export_job(plan, PrintSpeeds(1.5, 4.0), generated_at=FIXED_TIME)
        feeds = {f for L in doc['lifts'] for _, _, f in moves(L['gcode'])}
        assert feeds == {90.0, 240.0}

    def test_conforms_to_contract(self, plan):
        check_contract(export_job(plan, SPEEDS, name=NAME, generated_at=FIXED_TIME))

    def test_pure_and_deterministic(self, plan):
        before = json.dumps(plan.to_dict('full'), sort_keys=True)
        a = export_job(plan, SPEEDS, name=NAME, generated_at=FIXED_TIME)
        b = export_job(plan, SPEEDS, name=NAME, generated_at=FIXED_TIME)
        assert a == b
        assert json.dumps(plan.to_dict('full'), sort_keys=True) == before

    def test_plan_without_lifts_refused(self, plan):
        empty = copy.copy(plan)
        empty.lifts = []
        with pytest.raises(ExportError):
            export_job(empty, SPEEDS)

    def test_filename(self):
        assert job_filename('My House') == 'My House.mudjob'
        assert job_filename('a/b:c') == 'abc.mudjob'
        assert job_filename('') == job_filename(None) == 'print-job.mudjob'


class TestContractFixture:
    def test_export_equals_shared_fixture(self, plan):
        """The Pi interface's import tests load this very file."""
        doc = export_job(plan, SPEEDS, name=NAME, generated_at=FIXED_TIME)
        text = json.dumps(doc, indent=1, ensure_ascii=False) + '\n'
        if os.environ.get('MUDJOB_UPDATE_FIXTURE'):
            FIXTURE.write_text(text, encoding='utf-8')
        assert FIXTURE.exists(), 'run with MUDJOB_UPDATE_FIXTURE=1 to create the contract fixture'
        assert json.loads(FIXTURE.read_text(encoding='utf-8')) == doc

    def test_handwritten_executable_fixture_conforms(self):
        check_contract(json.loads((CONTRACT / 'fixtures' / 'executable-3-lifts.mudjob').read_text(encoding='utf-8')))


# ---------------------------------------------------------------------------
# endpoint: exports only the CURRENT, existing plan
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    import app as A
    from layer_assembly import web as W
    A.app.config['TESTING'] = True
    W._PLANS.clear()
    return A.app.test_client()


REQ = {'assembly': ASSEMBLY, 'designs': DESIGNS}


class TestEndpoint:
    def test_no_plan_no_export(self, client):
        r = client.post('/api/assembly/print_job', json={**REQ, 'name': NAME})
        assert r.status_code == 409 and r.get_json()['outdated'] is True

    def test_exports_the_planned_print(self, client, plan):
        assert client.post('/api/assembly/print_plan', json=REQ).status_code == 200
        r = client.post('/api/assembly/print_job', json={**REQ, 'name': NAME})
        assert r.status_code == 200
        out = r.get_json()
        assert out['filename'] == f'{NAME}.mudjob'
        assert out['executable'] is True and out['placeholder_speeds'] is True
        job = out['job']
        check_contract(job)
        assert out['lines'] == sum(len(L['gcode']) for L in job['lifts'])
        job['source']['generated_at'] = FIXED_TIME
        assert job == export_job(plan, SPEEDS, name=NAME, generated_at=FIXED_TIME, placeholder_speeds=True)

    def test_changed_design_needs_a_new_plan(self, client):
        client.post('/api/assembly/print_plan', json=REQ)
        changed = copy.deepcopy(REQ)
        changed['assembly']['layer_height'] = 1.0
        assert client.post('/api/assembly/print_job', json=changed).status_code == 409

    def test_speeds_do_not_invalidate_the_plan_and_set_the_feeds(self, client):
        client.post('/api/assembly/print_plan', json=REQ)
        timed = copy.deepcopy(REQ)
        timed['assembly']['print_speeds'] = {'print': 3.0, 'travel': 5.0}
        r = client.post('/api/assembly/print_job', json=timed)
        assert r.status_code == 200
        out = r.get_json()
        assert out['placeholder_speeds'] is False
        assert out['job']['machine']['feeds_in_min'] == {'print': 180.0, 'travel': 300.0}


# ---------------------------------------------------------------------------
# .muddesign is authoring-only; .mudjob is not a project
# ---------------------------------------------------------------------------

class TestFileRoles:
    def test_project_file_is_not_a_job(self):
        import project_file as PF
        assert PF.FORMAT != 'mudprinter-job'
        assert PF.FORMAT == 'mud-printer-project'

    def test_designer_refuses_to_open_a_job(self, client):
        import project_file as PF
        text = FIXTURE.read_text(encoding='utf-8')
        with pytest.raises(PF.ProjectFileError):
            PF.parse(text)
        r = client.post('/api/project/open', data=text.encode('utf-8'))
        assert r.status_code == 400
