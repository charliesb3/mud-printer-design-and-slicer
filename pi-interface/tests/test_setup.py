"""
Tests for the current-machine execution pass (2026-10-14):

- LIVE waits for GRBL's ok (no fixed per-line timeout while GRBL answers
  status polls; fail only when it stops answering) — the M1 10 s risk;
- print origin / prime position (Pi-owned machine setup) and their motion;
- Start of a building job returns to the origin first; flat G-code unchanged;
- jog / re-zero / prime while a job awaits a frame reposition, then explicit
  confirmation;
- invalidation when machine position can no longer be trusted.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import config
from app.api import http as http_module
from app.api.ws import ConnectionManager
from app.grbl.communicator import GRBLCommunicator
from app.jobs.current_job import CurrentJob, JobLift
from app.jobs.runner import DRAIN_COMMAND, JobRunner
from app.state import AppState
from .mock_transport import ALARM_STATUS, IDLE_STATUS, WELCOME_MSG, MockTransport, wait_for, wait_until_sent

ORIGIN_LINE = "G54 G20 G90 G1 X0 Y0 F10.0"          # jog speed 254 mm/min = 10 in/min


def idle_at(x_mm: float, y_mm: float) -> str:
    return f"<Idle|MPos:{x_mm:.3f},{y_mm:.3f},0.000|FS:0,0>"


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data):
        self.sent.append(data)


async def msg(data: dict, ws: FakeWS | None = None) -> FakeWS:
    ws = ws or FakeWS()
    await http_module._handle_message(data, ws)
    return ws


def building(*lift_lines) -> CurrentJob:
    lifts = [JobLift(index=i, lines=list(l)) for i, l in enumerate(lift_lines)]
    return CurrentJob(filename="b.mudjob", filepath=Path("/tmp/b.mudjob"), lifts=lifts, kind="building")


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr(config, "STATUS_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(config, "GRBL_UNRESPONSIVE_S", 0.4)
    monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", 254.0)
    transport = MockTransport(auto_ok=True, status_reply=idle_at(100, 200))
    transport.replies["$$"] = ["$10=1", "$13=0", "$110=5000.000"]
    state = AppState()
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    http_module.init(state, comm, ConnectionManager())
    http_module._current_job = None
    http_module._runner_task = None
    await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)
    yield comm, transport, state
    await http_module._stop_job()
    await http_module._stop_jog()
    http_module._current_job = None
    http_module._runner_task = None
    http_module._state = None
    http_module._comm = None
    await comm.stop()


# ------------------------------------------------------------------ #
# Live ok-wait (the M1 per-line timeout risk)                          #
# ------------------------------------------------------------------ #

class TestLiveWait:
    async def test_slow_ok_is_fine_while_grbl_answers_status(self, env):
        """Planner buffer full on a long move: ok comes much later than the window."""
        comm, transport, state = env
        transport._auto_ok = False
        task = asyncio.create_task(comm.send_command("G1 X100", live=True))
        await wait_until_sent(transport, 1)
        await asyncio.sleep(1.0)                       # 2.5 × GRBL_UNRESPONSIVE_S, status flowing
        assert not task.done()
        await transport.inject("ok")
        assert await asyncio.wait_for(task, 1.0) == "ok"

    async def test_feed_hold_does_not_fail_the_wait(self, env):
        comm, transport, state = env
        transport._auto_ok = False
        transport.status_reply = "<Hold:0|MPos:5.000,2.000,0.000|FS:0,0>"
        task = asyncio.create_task(comm.send_command("G1 X100", live=True))
        await asyncio.sleep(1.0)
        assert not task.done()
        await transport.inject("ok")
        assert await asyncio.wait_for(task, 1.0) == "ok"

    async def test_unresponsive_grbl_fails_the_wait(self, env):
        comm, transport, state = env
        transport._auto_ok = False
        transport.status_reply = None                  # no status reports any more
        with pytest.raises(asyncio.TimeoutError, match="unresponsive"):
            await asyncio.wait_for(comm.send_command("G1 X100", live=True), 3.0)

    async def test_non_live_commands_keep_their_fixed_timeout(self, env):
        comm, transport, state = env
        transport._auto_ok = False
        with pytest.raises(asyncio.TimeoutError):
            await comm.send_command("$X", timeout=0.3)

    async def test_job_survives_a_long_move(self, env):
        comm, transport, state = env
        transport._auto_ok = False
        job = CurrentJob(filename="f.nc", filepath=Path("/tmp/f.nc"), lines=["G1 X1", "G1 X500", "G1 X2"])
        task = asyncio.create_task(JobRunner(comm, job).run())
        await wait_until_sent(transport, 1)
        await transport.inject("ok")
        await wait_until_sent(transport, 2)
        await asyncio.sleep(1.0)                       # the long move: ok withheld
        assert job.state == "running"
        await transport.inject("ok")
        await wait_until_sent(transport, 3)
        await transport.inject("ok")
        await asyncio.wait_for(task, 2.0)
        assert job.state == "completed"

    async def test_job_fails_when_grbl_goes_silent(self, env):
        comm, transport, state = env
        transport._auto_ok = False
        transport.status_reply = None
        job = CurrentJob(filename="f.nc", filepath=Path("/tmp/f.nc"), lines=["G1 X1"])
        await asyncio.wait_for(JobRunner(comm, job).run(), 3.0)
        assert job.state == "error" and "unresponsive" in job.error


# ------------------------------------------------------------------ #
# Origin and prime position                                            #
# ------------------------------------------------------------------ #

class TestOriginAndPrime:
    async def test_set_origin(self, env):
        comm, transport, state = env
        ws = await msg({"type": "set_origin"})
        assert ws.sent == []
        assert transport.sent_lines == ["$$", "G54", "G10 L20 P1 X0 Y0"]
        assert state.setup.origin_set and state.setup.origin_mpos == (100.0, 200.0)
        assert state.report_units == "mm"
        s = state.to_dict()["setup"]
        assert s["origin_set"] and s["work_x"] == 0 and s["work_y"] == 0 and not s["prime_ready"]

    async def test_prime_needs_origin_and_position(self, env):
        comm, transport, state = env
        ws = await msg({"type": "set_prime"})
        assert "origin" in ws.sent[0]["message"]
        ws = await msg({"type": "prime"})
        assert "origin" in ws.sent[0]["message"]
        await msg({"type": "set_origin"})
        ws = await msg({"type": "prime"})
        assert "prime position" in ws.sent[0]["message"].lower()

    async def test_set_prime_in_work_inches_and_move_there(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        transport.status_reply = idle_at(125.4, 149.2)          # +1 in, −2 in (mm reports)
        await wait_for(lambda: state.mpos_x == 125.4, timeout=2.0)
        assert (await msg({"type": "set_prime"})).sent == []
        assert state.setup.prime == pytest.approx((1.0, -2.0))
        transport.status_reply = idle_at(100, 200)              # back at the origin
        transport.clear_sent()
        assert (await msg({"type": "prime"})).sent == []
        assert state.setup.moving == "prime"
        await wait_for(lambda: state.setup.moving is None, timeout=2.0)
        assert transport.sent_lines == ["G54 G20 G90 G1 X1.0000 Y-2.0000 F10.0", DRAIN_COMMAND]

    async def test_inch_reports_need_no_conversion(self, env):
        comm, transport, state = env
        transport.replies["$$"] = ["$13=1"]
        transport.status_reply = idle_at(10, 20)
        await wait_for(lambda: state.mpos_x == 10, timeout=2.0)
        await msg({"type": "set_origin"})
        transport.status_reply = idle_at(13, 20)
        await wait_for(lambda: state.mpos_x == 13, timeout=2.0)
        await msg({"type": "set_prime"})
        assert state.report_units == "in" and state.setup.prime == pytest.approx((3.0, 0.0))

    async def test_prime_follows_a_re_zeroed_origin(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        transport.status_reply = idle_at(125.4, 200)
        await wait_for(lambda: state.mpos_x == 125.4, timeout=2.0)
        await msg({"type": "set_prime"})
        transport.status_reply = idle_at(150, 200)                # frame moved: re-zero here
        await wait_for(lambda: state.mpos_x == 150, timeout=2.0)
        await msg({"type": "set_origin"})
        assert state.setup.origin_mpos == (150.0, 200.0)
        assert state.setup.prime == pytest.approx((1.0, 0.0))     # relative to the (new) origin
        assert state.to_dict()["setup"]["work_x"] == 0

    async def test_positioning_needs_the_jog_speed(self, env, monkeypatch):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        await msg({"type": "set_prime"})
        monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", None)
        ws = await msg({"type": "prime"})
        assert "jog" in ws.sent[0]["message"].lower() and state.setup.moving is None

    async def test_stop_cancels_a_prime_move(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        await msg({"type": "set_prime"})
        transport._auto_ok = False
        await msg({"type": "prime"})
        await wait_for(lambda: any(l.startswith("G54 G20 G90 G1") for l in transport.sent_lines), timeout=2.0)
        await msg({"type": "stop"})
        # Fourteenth-pass: controlled-decel STOP sends 0x85 + ! for the
        # feed hold synchronously; 0x18 (soft reset) is deferred to a
        # background task that waits for Idle (<= 5 s). The mock's
        # status_reply still shows Idle here, so 0x18 lands quickly.
        assert state.setup.moving is None
        assert b"\x85" in transport.raw_sent and b"!" in transport.raw_sent
        await wait_for(lambda: b"\x18" in transport.raw_sent, timeout=5.0)

    async def test_alarm_clears_the_origin_keeps_prime(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        await msg({"type": "set_prime"})
        transport.status_reply = ALARM_STATUS
        await wait_for(lambda: not state.setup.origin_set, timeout=2.0)
        s = state.to_dict()["setup"]
        assert s["prime"] is not None and not s["prime_ready"] and "Origin cleared" in s["note"]

    async def test_alarm_message_and_disconnect_clear_the_origin(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        transport.status_reply = ALARM_STATUS                      # GRBL stays in alarm …
        await transport.inject("ALARM:1")
        await wait_for(lambda: not state.setup.origin_set, timeout=2.0)
        ws = await msg({"type": "set_origin"})                    # … so the origin is refused
        assert ws.sent and "Idle" in ws.sent[0]["message"]
        transport.status_reply = IDLE_STATUS                       # unlocked
        await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)
        await msg({"type": "set_origin"})
        assert state.setup.origin_set
        await transport.disconnect()
        await wait_for(lambda: not state.setup.origin_set, timeout=2.0)

    async def test_idle_soft_reset_keeps_the_origin(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        await transport.inject(WELCOME_MSG)
        await asyncio.sleep(0.1)
        assert state.setup.origin_set

    async def test_setup_refused_while_a_job_runs(self, env):
        comm, transport, state = env
        http_module._current_job = building(["G20", "G90", "G1 X1 Y1"])
        http_module._current_job.state = "running"
        for kind in ("set_origin", "set_prime", "prime"):
            ws = await msg({"type": kind})
            assert "job is running" in ws.sent[0]["message"]
        http_module._current_job.state = "ready"


# ------------------------------------------------------------------ #
# Start: return to origin                                              #
# ------------------------------------------------------------------ #

class TestStart:
    async def test_building_job_needs_the_origin(self, env):
        comm, transport, state = env
        http_module._current_job = building(["G20", "G90", "G1 X1 Y1"])
        ws = await msg({"type": "job_start"})
        assert "origin" in ws.sent[0]["message"] and http_module._runner_task is None

    async def test_building_job_returns_to_origin_first(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        transport.clear_sent()
        job = http_module._current_job = building(["G20", "G90", "G1 X5 Y5", "G1 X6 Y5"])
        assert (await msg({"type": "job_start"})).sent == []
        await asyncio.wait_for(http_module._runner_task, 2.0)
        assert job.state == "completed"
        assert transport.sent_lines == [ORIGIN_LINE, "G20", "G90", "G1 X5 Y5", "G1 X6 Y5"]

    async def test_return_to_origin_reported_as_phase(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        transport._auto_ok = False
        job = http_module._current_job = building(["G20", "G90", "G1 X5 Y5"])
        await msg({"type": "job_start"})
        await wait_for(lambda: ORIGIN_LINE in transport.sent_lines, timeout=2.0)
        assert job.to_dict()["phase"] == "origin" and job.current_line == 0
        await transport.inject("ok")
        await wait_for(lambda: job.phase is None, timeout=2.0)

    async def test_building_start_needs_the_jog_speed(self, env, monkeypatch):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", None)
        http_module._current_job = building(["G20", "G90", "G1 X1 Y1"])
        ws = await msg({"type": "job_start"})
        assert "jog" in ws.sent[0]["message"].lower()

    async def test_flat_gcode_needs_no_origin_and_never_returns(self, env):
        comm, transport, state = env
        job = http_module._current_job = CurrentJob(filename="f.nc", filepath=Path("/tmp/f.nc"),
                                                    lines=["G21", "G0 X1", "G1 X2 F100"])
        assert (await msg({"type": "job_start"})).sent == []
        await asyncio.wait_for(http_module._runner_task, 2.0)
        assert job.state == "completed" and transport.sent_lines == ["G21", "G0 X1", "G1 X2 F100"]


# ------------------------------------------------------------------ #
# Frame reposition: jog / re-zero / prime, then explicit confirmation #
# ------------------------------------------------------------------ #

class TestRepositionWorkflow:
    async def test_re_zero_prime_and_confirm(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        await msg({"type": "set_prime"})
        job = http_module._current_job = building(["G20", "G90", "G1 X5 Y5"], ["G20", "G90", "G1 X7 Y7"])
        await msg({"type": "job_start"})
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)

        # the frame raise disturbed registration: re-zero at a new spot, prime again
        transport.status_reply = idle_at(300, 400)
        await wait_for(lambda: state.mpos_x == 300, timeout=2.0)
        assert (await msg({"type": "set_origin"})).sent == []
        assert state.setup.origin_mpos == (300.0, 400.0)
        assert (await msg({"type": "prime"})).sent == []
        await wait_for(lambda: state.setup.moving is None, timeout=2.0)
        assert "G1 X7 Y7" not in transport.sent_lines              # nothing of the next lift yet
        assert job.state == "awaiting_reposition"

        # console stays blocked; resume never confirms; the explicit confirmation continues
        await msg({"type": "console_send", "command": "G0 X99"})
        await msg({"type": "resume"})
        await asyncio.sleep(0.1)
        assert job.state == "awaiting_reposition" and "G0 X99" not in transport.sent_lines
        assert (await msg({"type": "confirm_reposition", "lift": 1})).sent == []
        await asyncio.wait_for(http_module._runner_task, 2.0)
        assert job.state == "completed" and transport.sent_lines[-1] == "G1 X7 Y7"

    async def test_confirm_refused_while_positioning(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        job = http_module._current_job = building(["G20", "G90", "G1 X5 Y5"], ["G20", "G90", "G1 X7 Y7"])
        await msg({"type": "job_start"})
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
        state.setup.moving = "prime"
        ws = await msg({"type": "confirm_reposition", "lift": 1})
        assert "moving" in ws.sent[0]["message"] and job.state == "awaiting_reposition"
        state.setup.moving = None

    async def test_alarm_while_awaiting_requires_a_new_origin(self, env):
        comm, transport, state = env
        await msg({"type": "set_origin"})
        job = http_module._current_job = building(["G20", "G90", "G1 X5 Y5"], ["G20", "G90", "G1 X7 Y7"])
        await msg({"type": "job_start"})
        await wait_for(lambda: job.state == "awaiting_reposition", timeout=2.0)
        transport.status_reply = ALARM_STATUS
        await wait_for(lambda: not state.setup.origin_set, timeout=2.0)
        transport.status_reply = IDLE_STATUS
        await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)
        ws = await msg({"type": "confirm_reposition", "lift": 1})
        assert "origin" in ws.sent[0]["message"]
