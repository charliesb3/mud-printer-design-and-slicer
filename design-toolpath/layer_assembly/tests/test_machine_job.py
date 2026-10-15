"""MACHINE JOB EXPORT (machine_job.py, 2026-10-14, Pi P3).

The `.mudjob` file is the ONLY boundary to the printer controller (contract:
`contracts/mudjob/README.md`). These tests check, from the Design + Toolpath
side alone (no Pi code is imported), that the authoritative PrintPlan is
serialized into a conforming document:

- lift identities, order, layer / Z boundaries and frame repositions are the
  PrintPlan's own (nothing reconstructed);
- unresolved machine semantics are reported, no G-code is produced;
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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.lifts import LiftSettings                                      # noqa: E402
from layer_assembly.machine_job import (ExportError, UNRESOLVED, export_job,       # noqa: E402
                                        job_filename)
from layer_assembly.print_plan import build_plan                                   # noqa: E402

CONTRACT = Path(__file__).resolve().parents[3] / 'contracts' / 'mudjob'
FIXTURE = CONTRACT / 'fixtures' / 'designer-export-3-lifts.mudjob'
FIXED_TIME = '2026-10-14T00:00:00Z'
NAME = 'Two-island test building'

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
            assert isinstance(L['gcode'], list) and L['gcode']
        else:
            assert 'gcode' not in L
        if pos == 0:
            assert 'reposition_before' not in L
        else:
            assert isinstance(L['reposition_before'], dict)
    json.dumps(doc)                                  # plain JSON


# ---------------------------------------------------------------------------

class TestExport:
    def test_lifts_are_the_plans_lifts(self, plan):
        assert len(plan.lifts) == 3
        doc = export_job(plan, generated_at=FIXED_TIME)
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
        doc = export_job(plan, generated_at=FIXED_TIME)
        assert 'reposition_before' not in doc['lifts'][0]
        for J, L in zip(doc['lifts'][1:], plan.lifts[1:]):
            fm, R = L.reposition_in, J['reposition_before']
            assert R['from_lift'] == fm.from_lift == L.index - 1
            assert R['rise'] == pytest.approx(fm.rise)
            assert (R['frame_z_from'], R['frame_z_to']) == (pytest.approx(fm.frame_z_from), pytest.approx(fm.frame_z_to))
            assert R['nozzle_at'] == [pytest.approx(fm.at[0]), pytest.approx(fm.at[1])]

    def test_summary_numbers_come_from_the_plan(self, plan):
        doc = export_job(plan, generated_at=FIXED_TIME)
        for J, L in zip(doc['lifts'], plan.lifts):
            runs = [r for i in L.passes for r in plan.passes[i].runs]
            assert J['runs'] == len(runs)
            assert J['print_length'] == pytest.approx(sum(r.print_length + r.retrace_length for r in runs), abs=1e-3)
        assert doc['units'] == plan.units == 'in'
        assert doc['source']['layers'] == len(plan.layers)
        assert doc['source']['lift_settings'] == plan.settings

    def test_unresolved_reported_and_no_gcode(self, plan):
        doc = export_job(plan, generated_at=FIXED_TIME)
        assert doc['executable'] is False
        assert [u['id'] for u in doc['unresolved']] == [i for i, _ in UNRESOLVED] == \
            ['z_within_lift', 'travel_with_pump', 'xy_origin', 'machine_units', 'frame_reposition']
        assert all('gcode' not in L for L in doc['lifts'])

    def test_conforms_to_contract(self, plan):
        check_contract(export_job(plan, name=NAME, generated_at=FIXED_TIME))

    def test_pure_and_deterministic(self, plan):
        before = json.dumps(plan.to_dict('full'), sort_keys=True)
        a = export_job(plan, name=NAME, generated_at=FIXED_TIME)
        b = export_job(plan, name=NAME, generated_at=FIXED_TIME)
        assert a == b
        assert json.dumps(plan.to_dict('full'), sort_keys=True) == before

    def test_plan_without_lifts_refused(self, plan):
        empty = copy.copy(plan)
        empty.lifts = []
        with pytest.raises(ExportError):
            export_job(empty)

    def test_filename(self):
        assert job_filename('My House') == 'My House.mudjob'
        assert job_filename('a/b:c') == 'abc.mudjob'
        assert job_filename('') == job_filename(None) == 'print-job.mudjob'


class TestContractFixture:
    def test_export_equals_shared_fixture(self, plan):
        """The Pi interface's import tests load this very file."""
        doc = export_job(plan, name=NAME, generated_at=FIXED_TIME)
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
        assert out['executable'] is False and len(out['unresolved']) == len(UNRESOLVED)
        job = out['job']
        check_contract(job)
        job['source']['generated_at'] = FIXED_TIME
        assert job == export_job(plan, name=NAME, generated_at=FIXED_TIME)

    def test_changed_design_needs_a_new_plan(self, client):
        client.post('/api/assembly/print_plan', json=REQ)
        changed = copy.deepcopy(REQ)
        changed['assembly']['layer_height'] = 1.0
        assert client.post('/api/assembly/print_job', json=changed).status_code == 409

    def test_speeds_do_not_invalidate_the_plan(self, client):
        client.post('/api/assembly/print_plan', json=REQ)
        timed = copy.deepcopy(REQ)
        timed['assembly']['print_speeds'] = {'print': 3.0}
        assert client.post('/api/assembly/print_job', json=timed).status_code == 200


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
