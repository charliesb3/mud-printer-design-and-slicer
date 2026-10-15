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

FIXTURE = Path(__file__).parent / "fixtures" / "sample_building.mudjob"


def job_doc(lifts, **extra) -> str:
    doc = {"format": "mudprinter-job", "version": 1, "lifts": lifts}
    doc.update(extra)
    return json.dumps(doc)


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
        assert [len(l.lines) for l in job.lifts] == [6, 4, 3]   # "; lift 1" comment stripped
        assert job.total_lines == 13
        assert job.lines == [line for l in job.lifts for line in l.lines]
        assert job.lifts[1].reposition_before["rise"] == 18
        assert job.units == "in"

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
        (job_doc([{"gcode": ["; only a comment"]}]), "no sendable"),
        (job_doc([{"index": 1, "gcode": ["G1 X1"]}]), "in order"),
        (job_doc([{"gcode": ["G1 X1"], "reposition_before": {"rise": 1}}]), "later lifts"),
        (job_doc([{"gcode": ["G1 X1"]}, {"gcode": ["G1 X2"], "reposition_before": 5}]), "must be an object"),
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

    async def test_jog_blocked_while_awaiting(self, handler_env, monkeypatch):
        comm, transport, state, job = handler_env
        monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", 100.0)
        await http_module._handle_message({"type": "jog_start", "axis": "X", "direction": 1}, FakeWS())
        await asyncio.sleep(0.1)
        assert not any(l.startswith("$J=") for l in transport.sent_lines)
        await http_module._stop_jog()

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
        assert b"\x18" in transport.raw_sent
        assert "G1 X2" not in transport.sent_lines
