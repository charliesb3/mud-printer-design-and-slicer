"""PRINT PATH FIDELITY + VIEW ACCURACY (2026-10-15).

Found 2026-10-15: corners / openings / junctions looked cut while orbiting
the Print Simulation (and in the Pi's top view) — the viewers skipped
vertices by index stride. The PrintPlan and the `.mudjob` were faithful.

Realistic fixture: `fixtures/realistic_stack_request.json` — the plan request
of the operator's "test 3" project as the page sends it (interleaved Wall
System over a circle + rectangle + line, two Assembly profiles, a header,
73 layers in 7 lifts).

- PrintPlan → `.mudjob`: every PrintPlan vertex appears in the G-code, in
  order (≤ the 0.0001 in output rounding); print length per lift equal;
- stationary Print Simulation: the stream IS the plan (≤ its 0.001 in
  playback rounding), vertex for vertex;
- drag / scrub LOD (static/simulation.js, under node): every vertex lies
  within the LOD tolerance of the drawn path; sharp turns are drawn.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'design_proto')))

from layer_assembly.machine_job import export_job                          # noqa: E402
from layer_assembly.print_plan import PrintSpeeds, items, stream            # noqa: E402

HERE = Path(__file__).parent
REQUEST = HERE / 'fixtures' / 'realistic_stack_request.json'
SIM_JS = HERE.parents[1] / 'design_proto' / 'static' / 'simulation.js'
LOD_CHECK = HERE / 'js' / 'sim_lod_check.js'
WORD = re.compile(r'([XY])(-?[\d.]+)')


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


@pytest.fixture(scope='module')
def plan():
    os.environ['LAYER_ASSEMBLY_WORKERS'] = '0'
    import app  # noqa: F401  (registers the Designer adapter)
    from layer_assembly import web as W
    req = json.loads(REQUEST.read_text())
    with W._STACK_LOCK:
        p, _ = W._planned(req)
    assert len(p.layers) == 73 and len(p.lifts) == 7
    return p


@pytest.fixture(scope='module')
def doc(plan):
    return export_job(plan, PrintSpeeds.from_dict({}))


def _gcode_points(lift):
    tl, pos, out, printed = set(lift['travel_lines']), None, [], 0.0
    for i, g in enumerate(lift['gcode']):
        w = dict((k, float(v)) for k, v in WORD.findall(g))
        if 'X' not in w:
            continue
        if pos is not None and i not in tl:
            printed += math.hypot(w['X'] - pos[0], w['Y'] - pos[1])
        pos = (w['X'], w['Y'])
        out.append(pos)
    return out, printed


def _plan_points(plan, lift_index):
    """The plan's own XY sequence for a lift, as the exporter must reproduce it
    (consecutive points equal after 0.0001 in rounding counted once; each pass
    is a re-entry segment starting with a travel to its first point)."""
    out, last, cur, entering = [], None, None, False
    for it in items(plan):
        if it[9] != lift_index or it[0] == 'frame':
            continue
        if it[11] != cur:
            cur, last, entering = it[11], None, True
        pts = [it[3][-1]] if it[0] in ('travel', 'transition') else \
            ((list(it[3][:1]) if entering else []) + list(it[3][1:]))
        for p in pts:
            q = (round(p[0], 4), round(p[1], 4))
            if q != last:
                out.append(p)
                last = q
        entering = False
    return out


class TestPlanToMudjob:
    def test_every_plan_vertex_is_in_the_gcode_in_order(self, plan, doc):
        total = 0
        for lift in doc['lifts']:
            got, _ = _gcode_points(lift)
            exp = _plan_points(plan, lift['index'])
            assert len(got) == len(exp), lift['index']
            worst = max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(exp, got))
            assert worst <= 0.00005 + 1e-12, (lift['index'], worst)
            total += len(got)
        assert total > 15000

    def test_print_length_per_lift(self, doc):
        for lift in doc['lifts']:
            _, printed = _gcode_points(lift)
            assert printed == pytest.approx(lift['print_length'], abs=0.01)


class TestSimulationStream:
    def test_stationary_stream_is_the_plan(self, plan):
        S = stream(plan, PrintSpeeds.from_dict({}))
        I, xy = S['items'], S['xy']
        its = list(items(plan))
        assert len(its) == len(I['kind'])
        worst = 0.0
        for k, it in enumerate(its):
            o, n = I['off'][k], I['n'][k]
            assert n == len(it[3])
            for j, (x, y) in enumerate(it[3]):
                worst = max(worst, abs(xy[2 * (o + j)] - x), abs(xy[2 * (o + j) + 1] - y))
        assert worst <= 0.0005 + 1e-12          # the playback copy's 0.001 in rounding


def _sharp_turns(xy, off, n, deg=60):
    """Vertex indices (flat) inside items where the path turns by more than `deg`."""
    out = []
    for o, m in zip(off, n):
        for k in range(o + 1, o + m - 1):
            ax, ay = xy[2 * k] - xy[2 * (k - 1)], xy[2 * k + 1] - xy[2 * (k - 1) + 1]
            bx, by = xy[2 * (k + 1)] - xy[2 * k], xy[2 * (k + 1) + 1] - xy[2 * k + 1]
            na, nb = math.hypot(ax, ay), math.hypot(bx, by)
            if na < 1e-6 or nb < 1e-6:
                continue
            c = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
            if math.degrees(math.acos(c)) > deg and min(na, nb) > 0.2:
                out.append(k)
    return out


def _run_lod(streams):
    r = subprocess.run(['node', str(LOD_CHECK), str(SIM_JS)], input=json.dumps(streams),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
class TestSimulationDragLod:
    def test_real_plan_stream_error_bounded_and_corners_drawn(self, plan):
        S = stream(plan, PrintSpeeds.from_dict({}))
        I, xy = S['items'], S['xy']
        corners = _sharp_turns(xy, I['off'], I['n'])
        assert len(corners) > 100                                   # walls, junctions, cuts
        res = _run_lod([{'xy': xy, 'off': I['off'], 'n': I['n'], 'corners': corners}])[0]
        assert res['tol'] <= res['max_tol']
        assert res['worst'] <= res['tol'] + 1e-9
        assert res['corners_kept'] == len(corners)

    def test_large_stream_above_the_budget(self):
        """V > SIM_LOD_VERTICES with right-angle corners at indices no stride would keep."""
        xy, corners, step = [], [], 0.01
        for k in range(0, 60001):
            xy += [k * step, 0.0]
        corners.append(len(xy) // 2 - 1)
        for k in range(1, 60003):
            xy += [600.0, k * step]
        corners.append(len(xy) // 2 - 1)
        for k in range(1, 30001):
            xy += [600.0 - k * step, 600.02 + 0.5 * math.sin(k * 0.05)]
        V = len(xy) // 2
        res = _run_lod([{'xy': xy, 'off': [0], 'n': [V], 'corners': corners}])[0]
        assert V > res['budget'] and res['kept'] < V / 10
        assert res['worst'] <= res['tol'] + 1e-9 and res['tol'] <= res['max_tol']
        assert res['corners_kept'] == 2


# ---------------------------------------------------------------------------
# The exact project behind the operator's `test 4.mudjob` (saved 2026-10-15 as
# "test 3 (1).muddesign"; its plan request in fixtures/test4_stack_request.json):
# the traced upper-right corner of the rectangular island, layer 1. (~50 s to plan)
# ---------------------------------------------------------------------------

TEST4_REQUEST = HERE / 'fixtures' / 'test4_stack_request.json'
TEST4_JOB = Path('/Users/charliebritton/Downloads/test4.mudjob')       # the operator's export (dev machine)
CORNER = [(363.6334, 315.5516), (368.2064, 320.1246), (365.5276, 317.4458), (370.1006, 322.0187)]


@pytest.fixture(scope='module')
def plan4():
    os.environ['LAYER_ASSEMBLY_WORKERS'] = '0'
    import app  # noqa: F401
    from layer_assembly import web as W
    with W._STACK_LOCK:
        p, _ = W._planned(json.loads(TEST4_REQUEST.read_text()))
    assert len(p.layers) == 73 and len(p.passes) == 286 and len(p.lifts) == 7
    return p


class TestOperatorProject:
    def test_plan_to_mudjob_vertex_for_vertex(self, plan4):
        doc = export_job(plan4, PrintSpeeds.from_dict({}))
        for lift in doc['lifts']:
            got, printed = _gcode_points(lift)
            exp = _plan_points(plan4, lift['index'])
            assert len(got) == len(exp)
            assert max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(exp, got)) <= 0.00005 + 1e-12
            assert printed == pytest.approx(lift['print_length'], abs=0.01)

    @pytest.mark.skipif(not TEST4_JOB.exists(), reason="the operator's export is only on the development machine")
    def test_reexport_equals_the_operators_job(self, plan4):
        doc = export_job(plan4, PrintSpeeds.from_dict({}), name='test 3', placeholder_speeds=True)
        job = json.loads(TEST4_JOB.read_text())
        doc['source'].pop('generated_at'); job['source'].pop('generated_at')
        assert doc == job

    @pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
    def test_traced_corner_in_every_view(self, plan4):
        S = stream(plan4, PrintSpeeds.from_dict({}))
        I, xy = S['items'], S['xy']
        idx = []
        for c in CORNER:                                         # in the stationary stream (0.001 in)
            hits = [v for v in range(len(xy) // 2) if abs(xy[2 * v] - c[0]) < 6e-4 and abs(xy[2 * v + 1] - c[1]) < 6e-4]
            assert hits, c
            idx.append(hits[0])
        res = _run_lod([{'xy': xy, 'off': I['off'], 'n': I['n'], 'corners': idx}])[0]
        assert len(xy) // 2 > res['budget']                      # the drag LOD really applies here
        assert res['corners_kept'] == 4 and res['worst'] <= res['tol'] + 1e-9
