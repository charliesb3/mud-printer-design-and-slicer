"""
Tests for the controller feature pass (2026-10-14):

- motion analysis of the job's own G-code (time / distance estimates, path);
- progress at job / lift / layer level; operator waits excluded;
- adaptive lift boundaries (clean cuts only, order and lines unchanged);
- feed override (GRBL real-time bytes, Ov read back);
- Outline (job XY bounds, origin required, separate from Prime);
- read-only GRBL diagnostics;
- the 2D path endpoint and multi-client progress state.
"""
from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import config
from app import main as main_module
from app.api import http as http_module
from app.api.ws import ConnectionManager
from app.grbl.communicator import GRBLCommunicator
from app.jobs.current_job import CurrentJob, JobFormatError, load_job_from_file, parse_building_job
from app.jobs.layout import LiftLayout
from app.jobs.motion import analyze, path_payload
from app.jobs.runner import DRAIN_COMMAND, JobRunner
from app.main import app
from app.state import AppState
from .mock_transport import ALARM_STATUS, IDLE_STATUS, WELCOME_MSG, MockTransport, wait_for

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "mudjob" / "fixtures"
WALL = CONTRACT / "designer-export-single-wall.mudjob"         # 12 layers, 4 lifts of 3, one pass per layer
ISLANDS = CONTRACT / "designer-export-3-lifts.mudjob"           # 3 lifts, two islands interleaved per lift
HANDWRITTEN = CONTRACT / "executable-3-lifts.mudjob"            # no layer marks


def idle_at(x_mm, y_mm, extra=""):
    return f"<Idle|MPos:{x_mm:.3f},{y_mm:.3f},0.000|FS:0,0{extra}>"


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data):
        self.sent.append(data)


async def msg(data, ws=None):
    ws = ws or FakeWS()
    await http_module._handle_message(data, ws)
    return ws


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr(config, "STATUS_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(config, "GRBL_UNRESPONSIVE_S", 0.5)
    monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", 254.0)
    transport = MockTransport(auto_ok=True, status_reply=idle_at(0, 0))
    transport.replies["$$"] = ["$10=1", "$13=0", "$110=5000.000", "$111=4000.000"]
    transport.replies["$G"] = ["[GC:G0 G54 G17 G20 G90 G94 M5 M9 T0 F120 S0]"]
    transport.replies["$#"] = ["[G54:12.000,34.000,0.000]", "[G92:0.000,0.000,0.000]", "[PRB:0.000,0.000,0.000:0]"]
    state = AppState()
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    http_module.init(state, comm, ConnectionManager())
    http_module._current_job = None
    http_module._runner_task = None
    await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)
    yield comm, transport, state
    await http_module._stop_job()
    http_module._current_job = None
    http_module._runner_task = None
    http_module._state = None
    http_module._comm = None
    await comm.stop()


def never_writes_settings(transport):
    import re
    return not any(re.match(r"^\$\d+=", l) for l in transport.sent_lines)


# ------------------------------------------------------------------ #
# Motion analysis (the job's own G-code)                               #
# ------------------------------------------------------------------ #

class TestMotion:
    def test_feed_moves_time_and_distance(self):
        p = analyze(["G20 G90", "G1 X0 Y0 F60", "G1 X10 Y0 F120", "G1 X10 Y10"])
        assert p.time_s[-1] == pytest.approx(10.0)                 # 2 × 10 in at 120 in/min
        assert p.path_in[-1] == pytest.approx(20.0) and p.print_in[-1] == pytest.approx(20.0)
        assert list(p.pt_line) == [1, 2, 3]

    def test_metric_lengths_are_reported_in_inches(self):
        p = analyze(["G21 G90", "G1 X254 F2540"])
        assert p.path_in[-1] == pytest.approx(10.0) and p.time_s[-1] == pytest.approx(6.0)

    def test_relative_moves_and_dwell(self):
        p = analyze(["G20 G91", "G1 X3 F60", "G1 Y4", "G4 P2"])
        assert p.path_in[-1] == pytest.approx(7.0) and p.time_s[-1] == pytest.approx(9.0)

    def test_rapids_need_the_grbl_rate(self):
        assert analyze(["G20 G90", "G0 X10"]).unknown_lines == 1
        p = analyze(["G20 G90", "G0 X10"], rapid_in_min=100.0)
        assert p.unknown_lines == 0 and p.time_s[-1] == pytest.approx(6.0)
        assert p.print_in[-1] == 0.0                                 # G0 counts as travel

    def test_travel_lines_split_print_and_travel(self):
        p = analyze(["G20 G90", "G1 X10 F60", "G1 X20", "G1 X30 F120"], travel={1, 3})
        assert p.print_in[-1] == pytest.approx(10.0) and p.path_in[-1] == pytest.approx(30.0)
        assert list(p.pt_kind) == [1, 0, 1]

    def test_path_payload_is_exact_when_nothing_can_be_dropped(self):
        p = analyze(["G20 G90", "G1 X0 Y0 F60", "G1 X10 Y0", "G1 X10 Y10", "G1 X0 Y10"])
        out = path_payload(p)
        assert not out["decimated"] and out["x"] == [0, 10, 10, 0] and out["y"] == [0, 0, 10, 10]


# ------------------------------------------------------------------ #
# 2D view accuracy (2026-10-15: no shortcuts absent from the machine path)
# ------------------------------------------------------------------ #

def _dense_corner_job(n_side=40000, step=0.01):
    """> 120k moves: a dense square with right-angle corners at indices that
    are not multiples of any stride, a wave edge, a travel and a second pass."""
    import math as m
    lines, travel = ["G20 G90", "G1 X0 Y0 F360"], {1}
    pts = []
    for k in range(1, n_side + 1):  pts.append((k * step, 0.0))                          # bottom edge
    for k in range(1, n_side + 7):  pts.append((n_side * step, k * step))                 # right edge (odd length)
    for k in range(1, n_side + 3):  pts.append((n_side * step - k * step,                 # wavy top edge
                                                (n_side + 6) * step + 0.5 * m.sin(k * step)))
    for x, y in pts:
        lines.append(f"G1 X{x:.4f} Y{y:.4f} F120")
    travel.add(len(lines)); lines.append("G1 X-5 Y-5 F360")                               # a travel
    lines.append("G20 G90"); brk = len(lines)                                             # a pass start
    lines.append("G1 X-5 Y5 F120"); lines.append("G1 X5 Y5")
    return lines, travel, brk


def _max_view_error(p, out):
    """Largest distance from any ORIGINAL vertex to the drawn polyline (the
    segment between the kept points around it)."""
    import bisect
    pos = {}
    for k, l in enumerate(out["line"]):
        pos[l] = k
    kept_lines = out["line"]
    worst = 0.0
    for i in range(len(p.pt_line)):
        l = p.pt_line[i]
        if l in pos:
            k = pos[l]
            worst = max(worst, abs(out["x"][k] - p.pt_x[i]), abs(out["y"][k] - p.pt_y[i]))
            continue
        k = bisect.bisect_left(kept_lines, l)
        ax, ay, bx, by = out["x"][k - 1], out["y"][k - 1], out["x"][k], out["y"][k]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p.pt_x[i] - ax) * dx + (p.pt_y[i] - ay) * dy) / l2))
        worst = max(worst, ((p.pt_x[i] - ax - t * dx) ** 2 + (p.pt_y[i] - ay - t * dy) ** 2) ** 0.5)
    return worst


class TestViewAccuracy:
    def test_every_vertex_within_tolerance_and_corners_exact(self):
        lines, travel, brk = _dense_corner_job()
        p = analyze(lines, travel=travel)
        assert len(p.pt_line) > 120_000
        out = path_payload(p, breaks={brk})
        assert out["decimated"] and out["tolerance_in"] <= 0.25
        assert _max_view_error(p, out) <= out["tolerance_in"] + 1e-4      # (+ 4-decimal output rounding)
        pts = set(zip(out["x"], out["y"]))
        n = 40000
        for corner in ((400.0, 0.0), (400.0, 400.06)):                     # the right-angle corners, exactly
            assert corner in pts
        line_of = dict(zip(out["line"], out["kind"]))
        assert line_of[max(travel)] == 1                                    # the travel move is drawn as travel
        assert brk + 1 in line_of                                           # the pass start (first move after it)
        assert len(out["line"]) < len(p.pt_line) / 4                        # and it is genuinely lighter

    def test_old_stride_shortcut_cannot_happen(self):
        """The 2026-10-15 defect: a right-angle corner at an index the stride skipped."""
        lines = ["G20 G90", "G1 X0 Y0 F120"] + [f"G1 X{k * 0.01:.4f} Y0" for k in range(1, 130_003)] + \
                [f"G1 X{130_002 * 0.01:.4f} Y{k * 0.01:.4f}" for k in range(1, 50)]
        p = analyze(lines)
        out = path_payload(p)
        assert _max_view_error(p, out) <= out["tolerance_in"] + 1e-4
        assert (round(1300.02, 4), 0.0) in set(zip(out["x"], out["y"]))

    @pytest.mark.skipif(not Path("/Users/charliebritton/Downloads/test4.mudjob").exists(),
                        reason="the operator's test 4 job is only on the development machine")
    def test_the_operators_test4_job(self):
        """Regression on the job where the shortcut was found (374 749 moves)."""
        job = load_job_from_file(Path("/Users/charliebritton/Downloads/test4.mudjob"))
        breaks = {g for g, _, _ in job.layout.segments} | set(job.layout.offsets)
        out = path_payload(job.profile, breaks)
        assert _max_view_error(job.profile, out) <= out["tolerance_in"] + 1e-4
        corner = {(363.6334, 315.5516), (368.2064, 320.1246), (365.5276, 317.4458), (370.1006, 322.0187)}
        assert corner <= set(zip(out["x"], out["y"]))                      # the traced corner, exactly


# ------------------------------------------------------------------ #
# Progress / estimates                                                  #
# ------------------------------------------------------------------ #

class TestProgress:
    def test_job_and_lift_totals_from_the_gcode(self):
        job = load_job_from_file(WALL)
        doc = json.loads(WALL.read_text())
        p = job.progress()
        assert p["estimate"] is True and p["unknown_lines"] == 0 and p["classified"] is True
        # the export's print length per lift = the controller's own reading of the G-code
        assert p["print_in"] == pytest.approx(sum(L["print_length"] for L in doc["lifts"]), abs=0.2)
        lift_times = [job.profile.between(*job.layout.range_of(k))["time_s"] for k in range(job.total_lifts)]
        assert p["total_s"] == pytest.approx(sum(lift_times), abs=0.2)
        assert p["lift_total_s"] == pytest.approx(lift_times[0], abs=0.1)
        assert p["remaining_s"] == p["total_s"] and p["fraction"] == 0

    def test_progress_mid_lift_and_layer(self):
        job = load_job_from_file(WALL)
        start, end = job.layout.range_of(1)
        mark_layer4 = job.layout.segments[4][0]                     # layer 4 = 2nd layer of lift 2
        job.current_lift, job.current_line = 1, mark_layer4 + 3
        p = job.progress()
        lift = p["lift"]
        assert lift["layer"] == 4 and lift["layer_in_lift"] == 2 and lift["layers"] == 3
        assert lift["completed_layers"] == 1
        assert lift["z"] == pytest.approx(7.5) and lift["z_in_lift"] == pytest.approx(3.0)
        assert lift["nominal_layer"] == 1.5 and lift["target_lift_height"] == 4.5 and lift["max_z_travel"] == 18
        assert lift["planned_height_nominal"] == pytest.approx(4.5)
        assert 0 < p["lift_fraction"] < 1 and 0 < p["fraction"] < 1
        assert p["lift_remaining_s"] < p["lift_total_s"] and p["remaining_s"] > p["lift_remaining_s"]
        done = job.profile.between(0, job.current_line)["time_s"]
        assert p["remaining_s"] == pytest.approx(p["total_s"] - done, abs=0.1)

    def test_observed_thickness_drives_physical_estimates(self):
        job = load_job_from_file(WALL)
        job.observed_layer = 1.0
        lift = job.progress()["lift"]
        assert lift["physical_lift_estimate"] == pytest.approx(3.0) and lift["observed_layer"] == 1.0

    def test_waiting_is_not_motion_time(self, monkeypatch):
        job = load_job_from_file(WALL)
        t0 = 1000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        job.started_at = t0
        job.begin_wait()
        monkeypatch.setattr(time, "time", lambda: t0 + 600)        # ten minutes raising the frame
        job.end_wait()
        monkeypatch.setattr(time, "time", lambda: t0 + 700)
        p = job.progress()
        assert p["elapsed_s"] == 700 and p["waiting_s"] == 600 and p["motion_elapsed_s"] == 100
        assert p["remaining_s"] == p["total_s"]                      # the estimate is untouched by waiting

    def test_flat_gcode_progress(self, tmp_path):
        f = tmp_path / "flat.nc"
        f.write_text("G21 G90\nG0 X10\nG1 X20 F600\nM2\n")
        job = load_job_from_file(f, rapid_in_min=None)
        p = job.progress()
        assert p["unknown_lines"] == 1 and p["total_s"] == pytest.approx(1.0) and p["lift"]["layers"] is None
        assert job.to_dict()["adjust"] is None


# ------------------------------------------------------------------ #
# Adaptive lift boundaries                                             #
# ------------------------------------------------------------------ #

class TestLayout:
    def test_clean_cuts_never_split_interleaved_islands(self):
        job = load_job_from_file(ISLANDS)
        L = job.layout
        assert L.adjustable
        assert L.cuts == set(L.offsets[1:])          # only the exported boundaries (layer-monotone there)

    def test_every_layer_of_a_single_wall_is_a_clean_cut(self):
        L = load_job_from_file(WALL).layout
        assert L.adjustable and L.cuts == {g for g, _, _ in L.segments[1:]}

    def test_synthetic_island_order(self):
        class Lift:
            def __init__(self, marks, n):
                self.lines, self.marks, self.meta, self.reposition_before = ["x"] * n, marks, {}, None
        # A0 A1 B0 B1 AB2 AB3: cuts only above the merge (before AB2, AB3)
        marks = [{"line": i * 2, "layer": l, "z": 1.0 * (l + 1)} for i, l in enumerate([0, 1, 0, 1, 2, 3])]
        L = LiftLayout([Lift(marks, 12)], {}, reentry=True)
        assert L.cuts == {8, 10}

    def test_not_adjustable_without_reentry_marks(self):
        job = load_job_from_file(HANDWRITTEN)
        assert not job.layout.adjustable
        refusal, _ = job.adjust_lift_end(0, 5)
        assert "re-export" in refusal

    def test_reentry_marks_are_validated(self):
        doc = json.loads(WALL.read_text())
        doc["lifts"][0]["travel_lines"] = []                        # entry moves no longer travel
        with pytest.raises(JobFormatError, match="re-entry"):
            parse_building_job(json.dumps(doc))

    def test_end_lift_early_and_extend(self):
        job = load_job_from_file(WALL)
        L = job.layout
        cands = {c["end_layer"]: c for c in job.end_candidates()}
        assert cands[2]["planned"] and cands[1]["layers"] == 2 and cands[3]["layers"] == 4
        assert job.adjust_lift_end(0, cands[1]["at"]) == (None, None)      # end lift 1 after layer 2
        assert L.layers_in(*L.range_of(0)) == [0, 1] and L.layers_in(*L.range_of(1)) == [2, 3, 4, 5]
        assert job.total_lifts == 4
        d = job.to_dict()
        assert d["lifts"][0]["adjusted"] and d["lifts"][1]["description"] == "Layers 3–6 (adjusted)"
        assert d["lifts"][2]["adjusted"] is False                         # untouched lifts keep their export
        assert job.restore_planned_lifts() is None and L.boundaries == L.offsets
        cands = {c["end_layer"]: c for c in job.end_candidates()}
        assert job.adjust_lift_end(0, cands[4]["at"]) == (None, None)      # 5 layers in lift 1
        assert L.layers_in(*L.range_of(0)) == [0, 1, 2, 3, 4] and L.layers_in(*L.range_of(1)) == [5]

    def test_refusals(self):
        job = load_job_from_file(WALL)
        assert "Only the end of lift 1" in job.adjust_lift_end(1, 50)[0]
        assert "not an allowed" in job.adjust_lift_end(0, 3)[0]           # mid-pass line
        job.observed_layer = 5.0                                          # 4 layers × 5 in = 20 > 18
        c4 = {c["end_layer"]: c for c in job.end_candidates()}[3]
        assert c4["exceeds_max"]
        assert "Z travel" in job.adjust_lift_end(0, c4["at"])[0]

    def test_cannot_move_a_boundary_into_sent_lines(self):
        job = load_job_from_file(WALL)
        job.state, job.current_line = "running", job.layout.segments[2][0] + 1      # inside layer 3
        ats = [c["at"] for c in job.end_candidates()]
        assert ats and min(ats) > job.current_line

    def test_warning_when_the_next_lift_gets_too_tall(self):
        job = load_job_from_file(WALL)
        job.observed_layer = 2.0
        L = job.layout
        # end lift 1 after 1 layer: lift 2 then holds layers 2..6 (5 × 2 = 10 in ≤ 18) — no warning
        assert job.adjust_lift_end(0, L.segments[1][0])[1] is None
        job.restore_planned_lifts()
        job.observed_layer = 4.0
        # 1 layer × 4 in; lift 2 = layers 2..6 → 5 × 4 = 20 in > 18
        refusal, warning = job.adjust_lift_end(0, L.segments[1][0])
        assert refusal is None and "Lift 2" in warning


class TestAdaptiveExecution:
    async def test_lines_and_order_unchanged_only_pauses_move(self, env):
        comm, transport, state = env
        job = load_job_from_file(WALL)
        L = job.layout
        new_end = L.segments[4][0]                                         # lift 1 = layers 1–4
        assert job.adjust_lift_end(0, new_end) == (None, None)
        task = asyncio.create_task(JobRunner(comm, job).run())
        expected_pauses = []
        while True:
            await wait_for(lambda: job.state in ("awaiting_reposition", "completed"), timeout=3.0)
            if job.state == "completed":
                break
            expected_pauses.append(job.current_line)
            assert job.confirm_reposition(job.awaiting_lift) is None
        await asyncio.wait_for(task, 2.0)
        sent = [l for l in transport.sent_lines if l != DRAIN_COMMAND]
        assert sent == job.lines                                           # every line once, same order
        assert expected_pauses == L.boundaries[1:] and expected_pauses[0] == new_end
        drains = [i for i, l in enumerate(transport.sent_lines) if l == DRAIN_COMMAND]
        assert len(drains) == len(L.boundaries) - 1

    async def test_extend_while_awaiting_continues_without_reposition(self, env):
        comm, transport, state = env
        job = load_job_from_file(WALL)
        L = job.layout
        task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=3.0)
        first_pause = job.current_line
        assert first_pause == L.offsets[1]
        cands = {c["end_layer"]: c for c in job.end_candidates()}
        assert all(c["at"] > first_pause for c in cands.values())         # extend only
        assert job.adjust_lift_end(0, cands[4]["at"]) == (None, None)     # continue through layer 5
        assert job.state == "running" and job.awaiting_lift is None
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=3.0)
        assert job.current_line == cands[4]["at"] and job.awaiting_lift == 1
        assert L.layers_in(*L.range_of(0)) == [0, 1, 2, 3, 4]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_adjust_via_message_and_measurement(self, env):
        comm, transport, state = env
        job = http_module._current_job = load_job_from_file(WALL)
        ws = await msg({"type": "measure_lift_height", "height": 3.0})
        assert "No layer" in ws.sent[0]["message"]
        job.current_line = job.layout.segments[2][0]                       # 2 layers done
        ws = await msg({"type": "measure_lift_height", "height": 2.0})
        assert job.observed_layer == 1.0 and ws.sent[0]["level"] == "info"
        ws = await msg({"type": "adjust_lift_end", "lift": 0, "at": 3})
        assert "not an allowed" in ws.sent[0]["message"]
        at = {c["end_layer"]: c for c in job.end_candidates()}[5]["at"]
        assert (await msg({"type": "adjust_lift_end", "lift": 0, "at": at})).sent == []
        assert job.layout.layers_in(*job.layout.range_of(0)) == list(range(6))
        ws = await msg({"type": "set_observed_layer", "thickness": -1})
        assert "between" in ws.sent[0]["message"]


# ------------------------------------------------------------------ #
# Feed override                                                        #
# ------------------------------------------------------------------ #

class TestFeedOverride:
    @pytest.mark.parametrize("step, byte", [(10, b"\x91"), (-10, b"\x92"), (1, b"\x93"), (-1, b"\x94"),
                                            ("reset", b"\x90")])
    async def test_steps_send_grbl_realtime_bytes(self, env, step, byte):
        comm, transport, state = env
        transport.clear_sent()
        assert (await msg({"type": "feed_override", "step": step})).sent == []
        assert transport.raw_sent[-1:] == [byte] or byte in transport.raw_sent
        assert transport.sent_lines == []                                 # real-time only, no G-code

    @pytest.mark.parametrize("step", [5, 50, 100, -100, "+10", None, True, 0.5])
    async def test_anything_else_is_refused(self, env, step):
        comm, transport, state = env
        transport.clear_sent()
        ws = await msg({"type": "feed_override", "step": step})
        assert ws.sent and not [b for b in transport.raw_sent if b != b"?"]

    async def test_value_is_read_back_from_grbl(self, env):
        comm, transport, state = env
        transport.status_reply = idle_at(0, 0, "|Ov:120,100,100")
        await wait_for(lambda: state.overrides == (120, 100, 100), timeout=2.0)
        assert state.to_dict()["grbl"]["overrides"]["feed"] == 120
        transport.status_reply = idle_at(0, 0)                            # Ov is intermittent: kept
        await asyncio.sleep(0.1)
        assert state.overrides == (120, 100, 100)
        await transport.inject(WELCOME_MSG)                               # reset: 100 % again, unknown until reported
        await wait_for(lambda: state.overrides is None, timeout=2.0)


# ------------------------------------------------------------------ #
# Outline                                                              #
# ------------------------------------------------------------------ #

class TestOutline:
    async def test_preview_from_job_bounds(self, env):
        job = load_job_from_file(WALL)
        o = job.outline()
        assert o["source"] == "job xy_bounds" and o["corners"][0] == o["corners"][-1]
        assert o["corners"][:4] == [[0, 0], [40, 0], [40, 30], [0, 30]]

    async def test_needs_origin_and_job(self, env):
        comm, transport, state = env
        ws = await msg({"type": "outline"})
        assert "origin" in ws.sent[0]["message"]
        await msg({"type": "set_origin"})
        ws = await msg({"type": "outline"})
        assert "job" in ws.sent[0]["message"]

    async def test_traces_the_bounds_and_leaves_the_job_alone(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        job = http_module._current_job = load_job_from_file(WALL)
        before = list(job.lines)
        transport.clear_sent()
        assert (await msg({"type": "outline"})).sent == []
        assert state.setup.moving == "outline"
        await wait_for(lambda: state.setup.moving is None, timeout=3.0)
        assert transport.sent_lines == [
            "G54 G20 G90 G1 X0.0000 Y0.0000 F10.0", "G54 G20 G90 G1 X40.0000 Y0.0000 F10.0",
            "G54 G20 G90 G1 X40.0000 Y30.0000 F10.0", "G54 G20 G90 G1 X0.0000 Y30.0000 F10.0",
            "G54 G20 G90 G1 X0.0000 Y0.0000 F10.0", DRAIN_COMMAND]
        assert job.lines == before and job.state == "ready"
        assert state.setup.prime is None                                  # Outline is not Prime

    async def test_refused_while_printing(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        job = http_module._current_job = load_job_from_file(WALL)
        job.state = "running"
        ws = await msg({"type": "outline"})
        assert "job is running" in ws.sent[0]["message"]
        job.state = "ready"

    async def test_flat_file_outline_from_its_moves(self, env, tmp_path):
        f = tmp_path / "flat.nc"
        f.write_text("G20 G90\nG1 X2 Y3 F60\nG1 X12 Y-4\n")
        o = load_job_from_file(f).outline()
        assert o["corners"][:4] == [[2, -4], [12, -4], [12, 3], [2, 3]] and "flat file" in o["source"]


# ------------------------------------------------------------------ #
# Diagnostics (read-only)                                              #
# ------------------------------------------------------------------ #

class TestDiagnostics:
    async def test_read_queries(self, env):
        comm, transport, state = env
        for what, cmd in (("settings", "$$"), ("parser", "$G"), ("offsets", "$#")):
            transport.clear_sent()
            assert (await msg({"type": "read_grbl", "what": what})).sent == []
            assert transport.sent_lines == [cmd]
        d = state.to_dict()["diag"]
        assert {"n": 13, "value": "0", "name": "Report in inches"} in d["settings"]
        assert d["parser_state"].startswith("G0 G54") and d["offsets"]["G54"] == "12.000,34.000,0.000"
        assert never_writes_settings(transport)

    async def test_only_whitelisted_queries(self, env):
        comm, transport, state = env
        transport.clear_sent()
        for what in ("$X", "$H", "$13=1", "write", None):
            ws = await msg({"type": "read_grbl", "what": what})
            assert ws.sent and "Unknown" in ws.sent[0]["message"]
        assert transport.sent_lines == []

    async def test_refused_while_a_job_runs(self, env):
        comm, transport, state = env
        job = http_module._current_job = load_job_from_file(WALL)
        job.state = "running"
        ws = await msg({"type": "read_grbl", "what": "settings"})
        assert "job is running" in ws.sent[0]["message"]
        job.state = "ready"

    async def test_alarm_and_error_history_with_descriptions(self, env):
        comm, transport, state = env
        transport._auto_ok = False
        task = asyncio.create_task(comm.send_command("G1 X1"))
        await wait_for(lambda: "G1 X1" in transport.sent_lines, timeout=1.0)
        await transport.inject("error:22")
        assert await task == "error:22"
        await transport.inject("ALARM:1")
        await transport.inject("[MSG:Reset to continue]")
        await wait_for(lambda: len(state.events) >= 3, timeout=1.0)
        ev = state.to_dict()["diag"]["events"]
        err = next(e for e in ev if e["kind"] == "error")
        assert "Feed rate has not yet been set" in err["detail"] and "G1 X1" in err["detail"]
        assert any(e["kind"] == "alarm" and "Hard limit" in e["detail"] for e in ev)
        assert any(e["kind"] == "message" and e["text"] == "Reset to continue" for e in ev)

    async def test_status_extras(self, env):
        comm, transport, state = env
        transport.status_reply = "<Idle|MPos:0.000,0.000,0.000|Bf:15,128|FS:0,0|Pn:XY|WCO:1.000,2.000,0.000>"
        await wait_for(lambda: state.pins == "XY", timeout=2.0)
        g = state.to_dict()["grbl"]
        assert g["pins_named"] == ["X limit", "Y limit"] and g["buffer"] == [15, 128] and g["wco"] == [1.0, 2.0]


# ------------------------------------------------------------------ #
# Path endpoint, multi-client progress                                 #
# ------------------------------------------------------------------ #

@pytest.fixture
def offline_client(monkeypatch):
    async def no_connection_loop():
        return None
    monkeypatch.setattr(main_module, "_connection_loop", no_connection_loop)
    http_module._current_job = None
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    http_module._current_job = None
    main_module.app_state.mark_disconnected()
    from app.state import MachineSetup
    main_module.app_state.setup = MachineSetup()
    main_module.app_state.job = None


class TestApi:
    def test_path_comes_from_the_job_gcode(self, offline_client):
        offline_client.post("/upload", files={"file": ("wall.mudjob", WALL.read_bytes(), "application/json")})
        job = http_module._current_job
        path = offline_client.get("/api/job/path").json()
        assert path["job_id"] == job.job_id and not path["decimated"]
        assert path["line"] == list(job.profile.pt_line)
        assert path["x"][:3] == [0.0, 0.0, 40.0] and set(path["kind"]) == {0, 1}

    def test_progress_shared_and_on_reconnect(self, offline_client):
        offline_client.post("/upload", files={"file": ("wall.mudjob", WALL.read_bytes(), "application/json")})
        with offline_client.websocket_connect("/ws") as a, offline_client.websocket_connect("/ws") as b:
            sa, sb = a.receive_json(), b.receive_json()
            for s in (sa, sb):
                p = s["job"]["progress"]
                assert p["estimate"] and p["total_s"] > 0 and p["lift"]["target_lift_height"] == 4.5
                assert s["job"]["adjust"]["lift"] == 0 and s["job"]["outline"]["corners"]
            http_module._current_job.observed_layer = 1.25
            a.send_json({"type": "hold"})                                 # any action broadcasts
            for _ in range(40):
                m = b.receive_json()
                if m["type"] == "state" and m["job"]["progress"]["lift"]["observed_layer"] == 1.25:
                    break
            else:
                raise AssertionError("progress not shared")
        with offline_client.websocket_connect("/ws") as again:
            s = again.receive_json()
            assert s["job"]["progress"]["lift"]["observed_layer"] == 1.25

    def test_flat_gcode_upload_still_works(self, offline_client):
        r = offline_client.post("/upload", files={"file": ("a.nc", io.BytesIO(b"G21\nG0 X1\nG1 X2 F100\n"),
                                                           "text/plain")}).json()
        assert r["success"] and r["kind"] == "gcode" and r["total_lines"] == 3
        s = offline_client.get("/api/state").json()["job"]
        assert s["adjust"] is None and s["progress"]["lift"]["layers"] is None


# ------------------------------------------------------------------ #
# Print parameters: target lift height + layer thickness (UI pass)     #
# ------------------------------------------------------------------ #

class TestLiftParameters:
    def test_planned_values_give_the_planned_end(self):
        job = load_job_from_file(WALL)                    # target 4.5, nominal 1.5 → 3 layers per lift
        p = job.propose_lift_end(4.5, 1.5)
        assert p["needed"] == 3 and p["exact"] and p["choice"]["planned"] and p["reason"] is None

    def test_thinner_layers_need_more_layers(self):
        job = load_job_from_file(WALL)
        p = job.propose_lift_end(4.5, 1.0)                # 4.5 / 1.0 → 4 layers
        assert p["needed"] == 4 and p["exact"] and p["choice"]["layers"] == 4 and p["choice"]["end_layer"] == 3
        p = job.propose_lift_end(9.0, 1.5)
        assert p["needed"] == 6 and p["choice"]["layers"] == 6

    def test_apply_moves_the_lift_end_and_stores_the_values(self):
        job = load_job_from_file(WALL)
        refusal, info = job.apply_lift_parameters(0, 4.5, 1.0)
        assert refusal is None and "building layer 4" in info
        assert job.layout.layers_in(*job.layout.range_of(0)) == [0, 1, 2, 3]
        assert job.observed_layer == 1.0 and job.target_lift == 4.5
        assert job.restore_planned_lifts() is None
        assert job.observed_layer is None and job.target_lift is None and job.layout.boundaries == job.layout.offsets
        job.apply_lift_parameters(0, 4.5, 1.0)
        lift = job.progress()["lift"]
        assert lift["target_lift_height"] == 4.5 and lift["observed_layer"] == 1.0
        assert lift["physical_lift_estimate"] == pytest.approx(4.0)

    def test_unchanged_end_still_stores_the_values(self):
        job = load_job_from_file(WALL)
        refusal, info = job.apply_lift_parameters(0, 5.0, 1.5)          # 3 layers either way
        assert refusal is None and "keeps its end" in info and job.target_lift == 5.0
        assert not job.adjustments

    def test_interleaved_islands_say_why(self):
        job = load_job_from_file(ISLANDS)                 # only the exported boundaries are clean cuts
        p = job.propose_lift_end(3.0, 1.0)                # 3 layers wanted; lifts hold 2 (then 4, 6)
        assert p["choice"]["layers"] == 2 and not p["exact"] and "interleave" in p["reason"]

    def test_end_of_job_says_why(self):
        job = load_job_from_file(WALL)
        job.current_lift = 3                              # the last lift: layers 10–12
        job.state = "running"
        job.current_line = job.layout.range_of(3)[0]
        p = job.propose_lift_end(18.0, 1.0)
        assert p["choice"]["layers"] == 3 and p["choice"]["end_of_job"] and "remain" in p["reason"]

    def test_already_beyond_says_why(self):
        job = load_job_from_file(WALL)
        job.state, job.current_line = "running", job.layout.segments[2][0] + 1    # printing layer 3
        p = job.propose_lift_end(1.5, 1.5)                # 1 layer wanted, 3 already under way
        assert p["choice"]["layers"] == 3 and "earliest end" in p["reason"]

    def test_refusals(self):
        job = load_job_from_file(WALL)
        assert "Z travel" in job.apply_lift_parameters(0, 20.0, 1.5)[0]          # target above 18 in
        assert "lift 1" in job.apply_lift_parameters(1, 4.5, 1.0)[0]
        old = load_job_from_file(HANDWRITTEN)
        assert "re-export" in old.apply_lift_parameters(0, 4.5, 1.0)[0]

    def test_candidates_are_not_cut_by_the_current_thickness(self):
        job = load_job_from_file(WALL)
        job.observed_layer = 5.0                          # 4+ layers exceed the 18 in Z travel at 5 in
        assert len(job.end_candidates()) == 12            # still listed (flagged) for other thicknesses
        assert job.propose_lift_end(18.0, 1.5)["choice"]["layers"] == 12

    async def test_apply_while_awaiting_continues_the_lift(self, env):
        comm, transport, state = env
        job = http_module._current_job = load_job_from_file(WALL)
        http_module._runner_task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=3.0)
        ws = await msg({"type": "apply_lift_parameters", "lift": 0, "target": 9.0, "thickness": 1.5})
        assert ws.sent and ws.sent[0]["level"] == "info" and "building layer 6" in ws.sent[0]["message"]
        assert job.state == "running"
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=3.0)
        assert job.layout.layers_in(*job.layout.range_of(0)) == list(range(6))
        ws = await msg({"type": "apply_lift_parameters", "lift": 0, "target": "x", "thickness": 1.5})
        assert "need" in ws.sent[0]["message"]
