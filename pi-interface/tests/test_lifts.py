"""
Tests for lift-aware building jobs.

Covers the job file format/validation, the runner's lift sequencing
(drain → awaiting_reposition → explicit confirmation → next lift), and the
WebSocket-handler rules for confirmation, resume, stop, jog and console while
a reposition is awaited. Flat G-code jobs must behave exactly as in M1.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app import config
from app.api import http as http_module
from app.api.ws import ConnectionManager
from app.grbl.communicator import GRBLCommunicator
from app.grbl.parser import program_control_reason
from app.jobs.current_job import (
    CurrentJob,
    JobFormatError,
    JobLift,
    load_job_from_file,
    parse_building_job,
)
from app.jobs.runner import DRAIN_COMMAND, JobRunner
from app.state import AppState
from .mock_transport import (
    ALARM_STATUS,
    IDLE_STATUS,
    RUN_STATUS,
    WELCOME_MSG,
    MockTransport,
    wait_for,
)

# The .mudjob contract (spec + fixtures) — shared as DATA with Design + Toolpath.
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "mudjob"
FIXTURE = CONTRACT / "fixtures" / "executable-3-lifts.mudjob"
EXPORTED = CONTRACT / "fixtures" / "designer-export-3-lifts.mudjob"


def job_doc(lifts, auto=True, units=True, **extra) -> str:
    """An executable job document; `auto` fills each lift's index and the
    reposition_before required on later lifts (unless given); `units`
    prefixes each lift's G-code with G20 + G90 (required before XY motion)."""
    if units:
        lifts = [{**l, "gcode": ["G20", "G90", *l["gcode"]]} if isinstance(l.get("gcode"), list) else l
                 for l in lifts]
    if auto:
        lifts = [{"index": i, **({"reposition_before": {"rise": 1}} if i else {}), **l}
                 for i, l in enumerate(lifts)]
    doc = {"format": "mudprinter-job", "version": 1, "executable": True, "lifts": lifts}
    doc.update(extra)
    return json.dumps(doc)


UNRESOLVED = [{"id": "machine_units", "summary": "not decided"}]


def building_job(*lift_lines: list[str]) -> CurrentJob:
    lifts = [JobLift(index=i, lines=list(lines)) for i, lines in enumerate(lift_lines)]
    return CurrentJob(filename="b.mudjob", filepath=Path("/tmp/b.mudjob"), lifts=lifts, kind="building")


@pytest.fixture
async def env(monkeypatch):
    """Communicator on a mock transport that acks every line and answers '?' with Idle."""
    monkeypatch.setattr(config, "STATUS_POLL_INTERVAL_S", 0.02)
    transport = MockTransport(auto_ok=True, status_reply=IDLE_STATUS)
    state = AppState()
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    yield comm, transport, state
    await comm.stop()


def sent_job_lines(transport) -> list[str]:
    return [l for l in transport.sent_lines if l != DRAIN_COMMAND]


# ------------------------------------------------------------------ #
# Format and validation                                                #
# ------------------------------------------------------------------ #

class TestJobFormat:
    def test_fixture_loads_as_building_job(self):
        job = load_job_from_file(FIXTURE)
        assert job.kind == "building"
        assert job.total_lifts == 3
        assert [len(l.lines) for l in job.lifts] == [7, 6, 5]   # "; lift 1" comment stripped
        assert job.total_lines == 18
        assert job.lines == [line for l in job.lifts for line in l.lines]
        assert job.lifts[1].reposition_before["rise"] == 18
        assert job.units == "in"
        assert job.executable is True and job.unresolved == []
        assert job.lifts[2].meta["layers"] == {"first": 4, "last": 5, "count": 2}

    def test_flat_gcode_is_one_lift(self, tmp_path):
        f = tmp_path / "flat.nc"
        f.write_text("G0 X0\n; c\nG1 X10\nM2\n")
        job = load_job_from_file(f)
        assert job.kind == "gcode"
        assert job.total_lifts == 1
        assert job.lifts[0].lines == ["G0 X0", "G1 X10", "M2"]   # M2 still allowed in flat files

    def test_json_content_detected_regardless_of_extension(self, tmp_path):
        f = tmp_path / "job.txt"
        f.write_text(job_doc([{"gcode": ["G1 X1"]}]))
        assert load_job_from_file(f).kind == "building"

    @pytest.mark.parametrize("doc, message", [
        ("not json", "not valid JSON"),
        (json.dumps([1]), "JSON object"),
        (json.dumps({"format": "other", "version": 1, "lifts": []}), "format"),
        (json.dumps({"format": "mudprinter-job", "version": 2, "lifts": []}), "version"),
        (job_doc([]), "non-empty"),
        (job_doc([{"gcode": "G1 X1"}]), "list of strings"),
        (job_doc([{"gcode": ["; only a comment"]}], units=False), "no sendable"),
        (job_doc([{"index": 1, "gcode": ["G1 X1"]}]), "in order"),
        (job_doc([{"gcode": ["G1 X1"]}], auto=False), "in order"),             # index is required
        (job_doc([{"gcode": ["G1 X1"], "reposition_before": {"rise": 1}}]), "later lifts"),
        (job_doc([{"gcode": ["G1 X1"]}, {"gcode": ["G1 X2"], "reposition_before": 5}]), "must be an object"),
        (job_doc([{"index": 0, "gcode": ["G1 X1"]}, {"index": 1, "gcode": ["G1 X2"]}], auto=False),
         "frame reposition"),                                                     # later lift without one
        (job_doc([{"gcode": ["G1 X1"]}], executable="yes"), "'executable'"),
        (job_doc([{"gcode": ["G1 X1"]}], unresolved=UNRESOLVED), "cannot list unresolved"),
        (job_doc([{}], executable=False), "must say what is unresolved"),
        (job_doc([{"gcode": ["G1 X1"]}], executable=False, unresolved=UNRESOLVED), "carries no 'gcode'"),
        (job_doc([{}], executable=False, unresolved=[{"id": "x"}]), "'unresolved'"),
        (json.dumps({"format": "mud-printer-project", "schema_version": 4, "project": {}}), ".muddesign"),
    ])
    def test_invalid_documents_rejected(self, doc, message):
        with pytest.raises(JobFormatError, match=message):
            parse_building_job(doc)

    @pytest.mark.parametrize("line", ["M0", "M00", "M1", "M2", "M02", "M30", "G1 X5 M0", "%", "$H", "$X", "$$"])
    def test_program_control_rejected_inside_lift(self, line):
        with pytest.raises(JobFormatError, match="not allowed inside a lift"):
            parse_building_job(job_doc([{"gcode": ["G1 X1", line]}]))

    @pytest.mark.parametrize("line", ["G1 X10 F100", "M3", "M5", "M10", "G4 P1", "G1 X1 ; M0 in comment"])
    def test_ordinary_lines_allowed(self, line):
        assert program_control_reason(line) is None

    @pytest.mark.parametrize("gcode, message", [
        (["G20", "G90", "G1 X1 Z2"], "Z words"),
        (["G20", "G90", "G1 Z1"], "Z words"),
        (["G20", "G90", "G10 L20 P1 X0 Y0"], "G10 / G92"),
        (["G20", "G90", "G92 X0 Y0"], "G10 / G92"),
        (["G90", "G1 X1 Y1"], "units"),
        (["G20", "G1 X1 Y1"], "distance mode"),
        (["G1 X1 Y1", "G20", "G90"], "before the lift sets"),
    ])
    def test_current_machine_lift_rules(self, gcode, message):
        with pytest.raises(JobFormatError, match=message):
            parse_building_job(job_doc([{"gcode": gcode}], units=False))

    @pytest.mark.parametrize("gcode", [["G21", "G90", "G1 X1 Y1"], ["G20 G90", "G1 X1 Y1 F120"],
                                       ["G20", "G91", "G1 X1"], ["G20", "G90", "G4 P1", "G1 X0.5 Y2"]])
    def test_current_machine_lifts_accepted(self, gcode):
        assert parse_building_job(job_doc([{"gcode": gcode}], units=False))["executable"] is True

    def test_layer_marks_map_to_sendable_lines(self):
        doc = job_doc([{"gcode": ["; c", "G1 X1 Y1", "; c", "G1 X2 Y2", "G1 X3 Y3"],
                        "layer_marks": [{"line": 0, "layer": 0, "z": 1.5, "z_in_lift": 1.5},
                                        {"line": 5, "layer": 1, "z": 3.0, "z_in_lift": 3.0}]}], units=False)
        doc = json.loads(doc)
        doc["lifts"][0]["gcode"] = ["G20", "G90"] + doc["lifts"][0]["gcode"]   # raw indices shift by 2
        doc["lifts"][0]["layer_marks"] = [{"line": 2, "layer": 0, "z": 1.5, "z_in_lift": 1.5},
                                          {"line": 5, "layer": 1, "z": 3.0, "z_in_lift": 3.0}]
        lift = parse_building_job(json.dumps(doc))["lifts"][0]
        assert lift.lines == ["G20", "G90", "G1 X1 Y1", "G1 X2 Y2", "G1 X3 Y3"]
        assert [m["line"] for m in lift.marks] == [2, 3]          # comments are not sendable lines
        assert lift.mark_at(0)["layer"] == 0 and lift.mark_at(3)["layer"] == 1 and lift.mark_at(5)["z"] == 3.0

    def test_bad_layer_marks_rejected(self):
        with pytest.raises(JobFormatError, match="layer_marks"):
            parse_building_job(job_doc([{"gcode": ["G1 X1"], "layer_marks": [{"line": 99}]}]))

    def test_muddesign_file_refused_by_extension(self, tmp_path):
        f = tmp_path / "house.muddesign"
        f.write_text(json.dumps({"format": "mud-printer-project", "project": {}}))
        with pytest.raises(JobFormatError, match="Export Print Job"):
            load_job_from_file(f)

    def test_unknown_keys_ignored(self):
        parsed = parse_building_job(job_doc([{"gcode": ["G1 X1"], "future_field": 1}], future_top_level={"a": 1}))
        assert parsed["executable"] is True and len(parsed["lifts"]) == 1


class TestContractFixtures:
    """The shared contract fixtures (data only — no Design + Toolpath code)."""

    def test_designer_export_loads_as_executable_building(self):
        job = load_job_from_file(EXPORTED)
        doc = json.loads(EXPORTED.read_text(encoding="utf-8"))
        assert job.kind == "building" and job.executable is True and job.unresolved == []
        assert job.total_lifts == len(doc["lifts"]) == 3
        # lift identities, order, boundaries and G-code exactly as exported (never recalculated)
        for lift, raw in zip(job.lifts, doc["lifts"]):
            assert lift.index == raw["index"]
            assert lift.lines == raw["gcode"]                     # the exporter writes no comments
            assert lift.lines[0] == "G20 G90"                    # each pass: a re-entry segment
            assert lift.travel == sorted(raw["travel_lines"])
            assert not any("Z" in l for l in lift.lines)
            assert lift.meta["layers"] == raw["layers"]
            assert (lift.meta["z_bottom"], lift.meta["z_top"]) == (raw["z_bottom"], raw["z_top"])
            assert lift.reposition_before == raw.get("reposition_before")
            assert [m["line"] for m in lift.marks] == [m["line"] for m in raw["layer_marks"]]
        d = job.to_dict()
        assert d["xy_bounds"] == doc["xy_bounds"]
        assert [l["layers"]["first"] for l in d["lifts"]] == [0, 2, 4]
        assert d["current_mark"]["layer"] == 0 and d["current_mark"]["z"] == 1.5

    def test_executable_fixture_loads(self):
        job = load_job_from_file(FIXTURE)
        assert job.executable and job.total_lifts == 3

    async def test_designer_export_executes_lift_by_lift(self, env):
        comm, transport, state = env
        job = load_job_from_file(EXPORTED)
        task = asyncio.create_task(JobRunner(comm, job, origin_feed_in_min=40.0).run())
        doc = json.loads(EXPORTED.read_text(encoding="utf-8"))
        for i in (1, 2):
            await wait_for(lambda: job.awaiting_lift == i, timeout=3.0)
            assert job.current_mark if hasattr(job, "current_mark") else True
            assert job.confirm_reposition(i) is None
        await asyncio.wait_for(task, timeout=3.0)
        assert job.state == "completed"
        expected = ["G54 G20 G90 G1 X0 Y0 F40.0"]
        for i, L in enumerate(doc["lifts"]):
            if i:
                expected.append(DRAIN_COMMAND)
            expected += L["gcode"]
        assert transport.sent_lines == expected
        assert not any("Z" in l for l in transport.sent_lines)


# ------------------------------------------------------------------ #
# Runner lift sequencing                                               #
# ------------------------------------------------------------------ #

class TestLiftSequencing:
    async def test_flat_job_never_drains_or_pauses(self, env):
        comm, transport, state = env
        job = CurrentJob(filename="f.nc", filepath=Path("/tmp/f.nc"), lines=["G0 X0", "G1 X10", "G1 X20"])
        await asyncio.wait_for(JobRunner(comm, job).run(), timeout=3.0)
        assert job.state == "completed"
        assert DRAIN_COMMAND not in transport.sent_lines
        assert job.awaiting_lift is None

    async def test_single_lift_building_job_completes_without_pause(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1", "G1 X2"])
        await asyncio.wait_for(JobRunner(comm, job).run(), timeout=3.0)
        assert job.state == "completed"
        assert DRAIN_COMMAND not in transport.sent_lines

    async def test_stops_at_lift_boundary_until_confirmed(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1", "G1 X2"], ["G1 X3"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
        assert job.awaiting_lift == 1
        assert job.current_lift == 0
        assert job.current_line == 2
        assert transport.sent_lines == ["G1 X1", "G1 X2", DRAIN_COMMAND]

        # Nothing of the next lift is sent while waiting, however long.
        await asyncio.sleep(0.3)
        assert job.state == "awaiting_reposition"
        assert "G1 X3" not in transport.sent_lines

        assert job.confirm_reposition(1) is None
        await asyncio.wait_for(task, timeout=2.0)
        assert job.state == "completed"
        assert job.current_lift == 1
        assert transport.sent_lines == ["G1 X1", "G1 X2", DRAIN_COMMAND, "G1 X3"]

    async def test_each_boundary_needs_its_own_confirmation(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1"], ["G1 X2"], ["G1 X3"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_for(lambda: job.awaiting_lift == 1, timeout=2.0)
        assert job.confirm_reposition(2) is not None      # wrong lift: refused
        assert job.state == "awaiting_reposition"
        assert job.confirm_reposition(1) is None

        await wait_for(lambda: job.awaiting_lift == 2, timeout=2.0)
        assert job.current_lift == 1
        assert job.confirm_reposition(1) is not None      # stale repeat: refused
        await asyncio.sleep(0.15)
        assert "G1 X3" not in transport.sent_lines
        assert job.confirm_reposition(2) is None

        await asyncio.wait_for(task, timeout=2.0)
        assert job.state == "completed"
        assert sent_job_lines(transport) == ["G1 X1", "G1 X2", "G1 X3"]
        assert transport.sent_lines.count(DRAIN_COMMAND) == 2

    async def test_boundary_waits_for_fresh_idle_after_drain(self, env):
        comm, transport, state = env
        transport.status_reply = RUN_STATUS
        job = building_job(["G1 X1"], ["G1 X2"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_for(lambda: DRAIN_COMMAND in transport.sent_lines, timeout=2.0)
        await asyncio.sleep(0.15)
        assert job.state == "running"          # still moving: not yet at the boundary

        transport.status_reply = IDLE_STATUS
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_drain_waits_for_dwell_ok(self, env):
        """The boundary is not reached until GRBL acks the dwell (buffer empty)."""
        comm, transport, state = env
        job = building_job(["G1 X1"], ["G1 X2"])
        transport._auto_ok = False

        async def ack_job_lines_only():
            await wait_for(lambda: len(transport.sent_lines) >= 1)
            await transport.inject("ok")                   # G1 X1
        asyncio.create_task(ack_job_lines_only())
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_for(lambda: transport.sent_lines[-1:] == [DRAIN_COMMAND], timeout=2.0)
        await asyncio.sleep(0.15)
        assert job.state == "running"

        await transport.inject("ok")                       # dwell finished
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_hold_during_drain_then_resume_reaches_boundary(self, env):
        comm, transport, state = env
        transport.status_reply = RUN_STATUS
        job = building_job(["G1 X1"], ["G1 X2"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_for(lambda: DRAIN_COMMAND in transport.sent_lines, timeout=2.0)
        job.state = "held"
        transport.status_reply = IDLE_STATUS
        await asyncio.sleep(0.15)
        assert job.state == "held"                         # hold is respected

        job.state = "running"
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
        assert "G1 X2" not in transport.sent_lines
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_stop_while_awaiting(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1"], ["G1 X2"])
        task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)

        job.state = "stopped"
        await asyncio.wait_for(task, timeout=2.0)
        assert job.state == "stopped"
        assert "G1 X2" not in transport.sent_lines

    async def test_grbl_reset_while_awaiting_is_an_error(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1"], ["G1 X2"])
        task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)

        await transport.inject(WELCOME_MSG)
        await asyncio.wait_for(task, timeout=2.0)
        assert job.state == "error"
        assert "reset" in job.error
        assert job.awaiting_lift is None
        assert "G1 X2" not in transport.sent_lines

    async def test_disconnect_while_awaiting_is_an_error(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1"], ["G1 X2"])
        task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)

        await transport.disconnect()
        await asyncio.wait_for(task, timeout=2.0)
        assert job.state == "error"
        assert "G1 X2" not in transport.sent_lines

    async def test_progress_reported_per_lift(self, env):
        comm, transport, state = env
        job = building_job(["G1 X1", "G1 X2"], ["G1 X3", "G1 X4", "G1 X5"])
        task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)

        d = job.to_dict()
        assert (d["total_lifts"], d["current_lift"], d["awaiting_lift"]) == (2, 0, 1)
        assert (d["lift_line"], d["lift_total_lines"]) == (2, 2)
        assert (d["current_line"], d["total_lines"]) == (2, 5)
        assert [l["total_lines"] for l in d["lifts"]] == [2, 3]
        assert "gcode" not in json.dumps(d["lifts"])        # lift G-code is not broadcast

        job.confirm_reposition(1)
        await asyncio.wait_for(task, timeout=2.0)
        d = job.to_dict()
        assert (d["current_lift"], d["lift_line"], d["lift_total_lines"]) == (1, 3, 3)


# ------------------------------------------------------------------ #
# WebSocket handler rules while awaiting a reposition                  #
# ------------------------------------------------------------------ #

class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data):
        self.sent.append(data)


@pytest.fixture
async def handler_env(env, monkeypatch):
    """http module wired to the mock communicator; a building job awaiting lift 2."""
    comm, transport, state = env
    http_module.init(state, comm, ConnectionManager())
    job = building_job(["G1 X1"], ["G1 X2"])
    http_module._current_job = job
    http_module._runner_task = asyncio.create_task(JobRunner(comm, job).run())
    await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
    yield comm, transport, state, job
    await http_module._stop_job()
    http_module._current_job = None
    http_module._runner_task = None
    http_module._state = None
    http_module._comm = None


class TestHandlerRules:
    async def test_confirm_via_message_runs_next_lift(self, handler_env):
        comm, transport, state, job = handler_env
        state.setup.origin_set = True
        ws = FakeWS()
        await http_module._handle_message({"type": "confirm_reposition", "lift": 1}, ws)
        await asyncio.wait_for(http_module._runner_task, timeout=2.0)
        assert job.state == "completed"
        assert ws.sent == []

    async def test_resume_does_not_confirm(self, handler_env):
        comm, transport, state, job = handler_env
        await http_module._handle_message({"type": "resume"}, FakeWS())
        await asyncio.sleep(0.15)
        assert job.state == "awaiting_reposition"
        assert "G1 X2" not in transport.sent_lines

    @pytest.mark.parametrize("lift", [0, 2, None, "1", True])
    async def test_wrong_or_missing_lift_refused_with_notice(self, handler_env, lift):
        comm, transport, state, job = handler_env
        ws = FakeWS()
        await http_module._handle_message({"type": "confirm_reposition", "lift": lift}, ws)
        assert job.state == "awaiting_reposition"
        assert ws.sent and ws.sent[0]["type"] == "notice"

    async def test_confirm_refused_unless_grbl_idle(self, handler_env):
        comm, transport, state, job = handler_env
        transport.status_reply = ALARM_STATUS
        await wait_for(lambda: state.grbl_state.startswith("Alarm"), timeout=2.0)
        ws = FakeWS()
        await http_module._handle_message({"type": "confirm_reposition", "lift": 1}, ws)
        assert job.state == "awaiting_reposition"
        assert "Idle" in ws.sent[0]["message"]

    async def test_jog_allowed_while_awaiting_in_mm(self, handler_env, monkeypatch):
        """The operator may jog (to re-zero) during a frame reposition; jog lines carry G21."""
        comm, transport, state, job = handler_env
        monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", 100.0)
        await http_module._handle_message({"type": "jog_start", "axis": "X", "direction": 1}, FakeWS())
        await wait_for(lambda: any(l.startswith("$J=") for l in transport.sent_lines), timeout=2.0)
        await http_module._stop_jog()
        jogs = [l for l in transport.sent_lines if l.startswith("$J=")]
        assert jogs[0].startswith("$J=G21 G91 X")
        assert job.state == "awaiting_reposition" and "G1 X2" not in transport.sent_lines

    async def test_confirm_needs_origin_for_building_job(self, handler_env):
        comm, transport, state, job = handler_env
        ws = FakeWS()
        await http_module._handle_message({"type": "confirm_reposition", "lift": 1}, ws)
        assert job.state == "awaiting_reposition" and "origin" in ws.sent[0]["message"]

    async def test_console_blocked_while_awaiting(self, handler_env):
        comm, transport, state, job = handler_env
        await http_module._handle_message({"type": "console_send", "command": "G0 X99"}, FakeWS())
        assert "G0 X99" not in transport.sent_lines

    async def test_start_refused_while_awaiting(self, handler_env):
        comm, transport, state, job = handler_env
        task = http_module._runner_task
        await http_module._handle_message({"type": "job_start"}, FakeWS())
        assert http_module._runner_task is task
        assert job.state == "awaiting_reposition"

    async def test_stop_while_awaiting_via_message(self, handler_env):
        comm, transport, state, job = handler_env
        await http_module._handle_message({"type": "stop"}, FakeWS())
        assert job.state == "stopped"
        # Fourteenth-pass: STOP sends 0x85 + ! synchronously (feed hold
        # at $120 / $121); 0x18 lands after GRBL settles.
        assert b"\x85" in transport.raw_sent and b"!" in transport.raw_sent
        await wait_for(lambda: b"\x18" in transport.raw_sent, timeout=5.0)
        assert "G1 X2" not in transport.sent_lines


class TestNonExecutableJob:
    async def test_start_refused_with_notice_nothing_sent(self, env, tmp_path):
        comm, transport, state = env
        await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)
        http_module.init(state, comm, ConnectionManager())
        f = tmp_path / "structure.mudjob"
        f.write_text(json.dumps({"format": "mudprinter-job", "version": 1, "executable": False,
                                 "unresolved": UNRESOLVED,
                                 "lifts": [{"index": 0}, {"index": 1, "reposition_before": {"rise": 3}}]}))
        http_module._current_job = load_job_from_file(f)
        http_module._runner_task = None
        try:
            ws = FakeWS()
            await http_module._handle_message({"type": "job_start"}, ws)
            assert http_module._runner_task is None
            assert http_module._current_job.state == "ready"
            assert ws.sent and "not executable" in ws.sent[0]["message"]
            assert transport.sent_lines == []
        finally:
            http_module._current_job = None
            http_module._state = None
            http_module._comm = None
