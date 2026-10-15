"""MACHINE PROFILE — the Design + Toolpath side (2026-10-15).

The project's printer: authoring state (.muddesign schema 5), a reference
envelope and a check — never a constraint. Machine profiles are independent
configuration on each side; the `.mudjob` carries what this one assumed
(`prepared_for`), the job's `requirements` and an informational `preflight`.

- the profile model, defaults (provisional) and validation;
- requirements of the AUTHORITATIVE PrintPlan and the preflight rules;
- the `.mudjob` records them; the job's G-code is identical whatever the
  profile (designs are never changed to fit);
- Save / Open and the schema 4 → 5 migration; the Z-per-frame value is the
  Assembly's planning setting;
- the browser's envelope / preflight helpers (app.js, under node) agree with
  the server's rules.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

import machine_profile as MP                                                   # noqa: E402
from layer_assembly.lifts import LiftSettings                                  # noqa: E402
from layer_assembly.machine_job import export_job                              # noqa: E402
from layer_assembly.print_plan import PrintSpeeds, build_plan                  # noqa: E402

HERE = Path(__file__).parent
CONTRACT = HERE.parents[2] / 'contracts' / 'mudjob' / 'fixtures'
PREPARED_FIXTURE = CONTRACT / 'designer-export-prepared-for.mudjob'
STATIC = HERE.parents[1] / 'design_proto' / 'static'
MAT = {'bead_width': 3.0, 'contact_overlap': 0.75, 'return_overlap': 0.75, 'physical': True}
WALL = [{'id': 'wall', 'name': 'wall', 'parent': None,
         'document': {'id': 'wall', 'source_paths': [{'id': 'W', 'type': 'RectanglePath', 'x': 0, 'y': 0, 'w': 40,
                                                       'h': 30, 'closed': True}],
                      'material': dict(MAT), 'wall_systems': []}}]
ASM = {'layer_height': 1.5, 'sections': [{'design_id': 'wall', 'height': 18.0}],
       'machine': {'max_z_travel': 18, 'lift_height': 4.5, 'head_clearance': 1}}
SPEEDS = PrintSpeeds(2.0, 6.0)


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture(scope='module')
def plan():
    from layer_assembly.web import _resolve_stack
    st = _resolve_stack({'assembly': ASM, 'designs': WALL})
    return build_plan(st['res'], st['src'], lift_settings=LiftSettings.from_dict(ASM['machine']))


def prof(**kw):
    p = MP.default_profile()
    p.update(provisional=[], **kw)
    return p


# ---------------------------------------------------------------------------

class TestProfile:
    def test_defaults_are_provisional_not_facts(self):
        p = MP.default_profile()
        assert p['units'] == 'in' and p['z_travel_per_frame'] == 18.0
        assert p['max_build_z'] is None                       # not documented: not set
        assert set(p['provisional']) == {'x_extent', 'y_extent'} and 'provisional' in p['name']

    @pytest.mark.parametrize('bad, msg', [({'units': 'mm'}, 'inches'), ({'x_extent': -1}, 'positive'),
                                          ({'y_extent': 'wide'}, 'positive'), ({'name': 3}, 'text'),
                                          ({'version': 99}, 'newer')])
    def test_validation(self, bad, msg):
        with pytest.raises(MP.MachineProfileError, match=msg):
            MP.validate({**MP.default_profile(), **bad})

    def test_unknown_keys_kept(self):
        assert MP.validate({**MP.default_profile(), 'nozzle': {'d': 2}})['nozzle'] == {'d': 2}


class TestRequirementsAndPreflight:
    def test_requirements_come_from_the_plan(self, plan):
        r = MP.requirements(plan)
        assert r['xy_bounds'] == {'min_x': 0.0, 'min_y': 0.0, 'max_x': 40.0, 'max_y': 30.0}
        assert r['building_height'] == 18.0 and r['max_lift_height'] == 4.5 and r['lifts'] == 4

    def test_fits(self, plan):
        pf = MP.preflight(MP.requirements(plan), prof(x_extent=120, y_extent=120, max_build_z=100))
        assert pf['fits'] and not pf['warnings'] and all(c['status'] == 'ok' for c in pf['checks'])

    @pytest.mark.parametrize('kw, failing', [({'x_extent': 30}, {'x'}), ({'y_extent': 20}, {'y'}),
                                             ({'max_build_z': 12}, {'z'}), ({'z_travel_per_frame': 4}, {'lift'})])
    def test_each_limit(self, plan, kw, failing):
        p = prof(**{'x_extent': 120, 'y_extent': 120, 'max_build_z': 100, **kw})
        pf = MP.preflight(MP.requirements(plan), p)
        assert not pf['fits'] and {c['id'] for c in pf['checks'] if c['status'] == 'fail'} == failing

    def test_placement_only_warns(self):
        req = {'xy_bounds': {'min_x': -5, 'min_y': 0, 'max_x': 35, 'max_y': 30}, 'building_height': 3, 'max_lift_height': 3}
        pf = MP.preflight(req, prof(x_extent=50, y_extent=50))
        x = next(c for c in pf['checks'] if c['id'] == 'x')
        assert pf['fits'] and x['status'] == 'warn' and 'print origin' in x['message']

    def test_unset_values_are_unchecked_and_provisional_is_said(self, plan):
        pf = MP.preflight(MP.requirements(plan), MP.default_profile())
        assert 'z' in pf['unchecked']
        assert all('provisional' in c['message'] for c in pf['checks'] if c['id'] in ('x', 'y'))


class TestMudjob:
    def test_requirements_always_prepared_for_with_a_profile(self, plan):
        doc = export_job(plan, SPEEDS)
        assert doc['requirements']['building_height'] == 18.0 and 'prepared_for' not in doc
        doc = export_job(plan, SPEEDS, machine_profile=prof(name='Shop printer', x_extent=120, y_extent=100))
        assert doc['prepared_for']['name'] == 'Shop printer' and doc['prepared_for']['y_extent'] == 100
        assert doc['preflight']['fits'] and doc['preflight']['informational'] is True

    def test_never_mutated_to_fit(self, plan):
        """A job far outside the profile: warned, its toolpath identical."""
        before = json.dumps(plan.to_dict('full'), sort_keys=True)
        small = export_job(plan, SPEEDS, machine_profile=prof(x_extent=10, y_extent=10, max_build_z=5,
                                                              z_travel_per_frame=2))
        free = export_job(plan, SPEEDS)
        assert not small['preflight']['fits'] and len([c for c in small['preflight']['checks'] if c['status'] == 'fail']) == 4
        assert [L['gcode'] for L in small['lifts']] == [L['gcode'] for L in free['lifts']]
        assert small['requirements'] == free['requirements']
        assert json.dumps(plan.to_dict('full'), sort_keys=True) == before

    def test_prepared_for_contract_fixture(self, plan):
        """Shared with the Pi interface's tests: the Designer says it FITS (its
        profile is large) — the Pi's own profile decides."""
        doc = export_job(plan, SPEEDS, name='Prepared-for test', generated_at='2026-10-15T00:00:00Z',
                         machine_profile=prof(name='Designer assumption', x_extent=120, y_extent=120,
                                              max_build_z=100))
        text = json.dumps(doc, indent=1, ensure_ascii=False) + '\n'
        if os.environ.get('MUDJOB_UPDATE_FIXTURE'):
            PREPARED_FIXTURE.write_text(text, encoding='utf-8')
        assert json.loads(PREPARED_FIXTURE.read_text(encoding='utf-8')) == doc


@pytest.fixture
def client():
    import app as A
    from layer_assembly import web as W
    A.app.config['TESTING'] = True
    W._PLANS.clear()
    return A.app.test_client()


class TestEndpoints:
    def test_export_with_the_project_profile(self, client):
        req = {'assembly': ASM, 'designs': WALL}
        assert client.post('/api/assembly/print_plan', json=req).status_code == 200
        r = client.post('/api/assembly/print_job', json={**req, 'machine_profile': prof(x_extent=30, y_extent=120)})
        out = r.get_json()
        assert r.status_code == 200 and not out['preflight']['fits'] and out['job']['prepared_for']['x_extent'] == 30
        r = client.post('/api/assembly/print_job', json={**req, 'machine_profile': {'units': 'mm'}})
        assert r.status_code == 400 and 'inches' in r.get_json()['error']

    def test_stream_reports_requirements(self, client):
        r = client.post('/api/assembly/print_plan', json={'assembly': ASM, 'designs': WALL, 'detail': 'stream',
                                                          'machine_profile': prof(max_build_z=10)})
        out = r.get_json()
        assert out['requirements']['building_height'] == 18.0 and not out['preflight']['fits']


class TestProjectFile:
    def _project(self, machine=None, profile='absent'):
        import copy as _c
        P = {'name': 'm', 'designer': {'designs': _c.deepcopy(WALL), 'active_design': 'wall', 'id_counter': 2,
                                        'design_counter': 1},
             'assembly': {'state': {'layer_height': 1.5, 'sections': [{'design_id': 'wall', 'height': 6}],
                                    **({'machine': machine} if machine else {})}, 'header_counter': 1},
             'wall_systems': []}
        if profile != 'absent':
            P['machine_profile'] = profile
        return P

    def test_v4_file_migrates_with_the_assembly_z(self):
        import project_file as PF
        f = {'format': PF.FORMAT, 'schema_version': 4, 'project': self._project(machine={'max_z_travel': 12, 'lift_height': 12})}
        out = PF.load(json.dumps(f))
        mp = out['project']['machine_profile']
        assert out['migrated_from'] == 4 and mp['z_travel_per_frame'] == 12.0 and 'x_extent' in mp['provisional']

    def test_save_open_round_trip(self):
        import project_file as PF
        p = prof(name='Shop', x_extent=144, y_extent=96, max_build_z=200, z_travel_per_frame=18)
        saved = PF.save(self._project(profile=p))
        out = PF.load(json.dumps(saved))
        assert out['project']['machine_profile'] == MP.validate(p) and not out['warnings']

    def test_one_z_per_frame_value(self):
        import project_file as PF
        saved = PF.save(self._project(machine={'max_z_travel': 15, 'lift_height': 15}, profile=prof(z_travel_per_frame=20)))
        assert saved['project']['machine_profile']['z_travel_per_frame'] == 15.0          # the Assembly wins
        saved = PF.save(self._project(profile=prof(z_travel_per_frame=12)))                # no Assembly value
        assert saved['project']['assembly']['state']['machine']['max_z_travel'] == 12.0

    def test_bad_profile_refused(self):
        import project_file as PF
        with pytest.raises(PF.ProjectFileError, match='machine profile'):
            PF.save(self._project(profile={'units': 'mm'}))


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_browser_helpers_agree_with_the_server(plan):
    """app.js machinePreflight / machineEnvelopeSegments under node vs the server's rules."""
    req = MP.requirements(plan)
    cases = [prof(x_extent=120, y_extent=120, max_build_z=100), prof(x_extent=30), prof(y_extent=20, max_build_z=12),
             prof(z_travel_per_frame=4), MP.default_profile(), prof(x_extent=50, y_extent=50)]
    reqs = [req, req, req, req, req, {'xy_bounds': {'min_x': -5, 'min_y': 0, 'max_x': 35, 'max_y': 30},
                                      'building_height': 3, 'max_lift_height': 3}]
    script = r"""
const fs = require('fs');
const stub = new Proxy(function () {}, { get: () => stub, apply: () => stub });
global.window = stub; global.document = stub; global.localStorage = undefined;
let src = fs.readFileSync(process.argv[process.argv.length - 1], 'utf8').replace(/\ninit\(\);\s*$/, '\n');
const api = new Function('window', 'document', src + '\n;return { machinePreflight, machineEnvelopeSegments };')(stub, stub);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => ({ pf: api.machinePreflight(c.req, c.p),
  segs: api.machineEnvelopeSegments(c.p, true, 0) }))));
"""
    r = subprocess.run(['node', '-e', script, '--', str(STATIC / 'app.js')],
                       input=json.dumps([{'req': q, 'p': p} for q, p in zip(reqs, cases)]),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout)
    for g, q, p in zip(got, reqs, cases):
        srv = MP.preflight(q, p)
        assert g['pf']['fits'] == srv['fits']
        assert [(c['id'], c['status']) for c in g['pf']['checks']] == [(c['id'], c['status']) for c in srv['checks']]
        segs = g['segs']
        if p['x_extent'] and p['y_extent']:
            pts = {tuple(pt) for s in segs for pt in s}
            assert (0, 0, 0) in pts and (p['x_extent'], p['y_extent'], 0) in pts
            assert len(segs) == (12 if p['max_build_z'] else 4)
            if p['max_build_z']:
                assert (p['x_extent'], p['y_extent'], p['max_build_z']) in pts
        else:
            assert segs == []
