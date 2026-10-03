"""
Tests for the server-side job runner.

Verifies streaming behavior, hold/resume, stop, error handling,
and the key property that the runner is independent of any WebSocket session.
"""
from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
import tempfile

from app.grbl.communicator import GRBLCommunicator
from app.jobs.current_job import CurrentJob, load_job_from_file
from app.jobs.runner import JobRunner
from app.state import AppState
from .mock_transport import MockTransport, wait_until_sent, WELCOME_MSG


def make_job(lines: list[str]) -> CurrentJob:
    """Build a CurrentJob from a list of G-code line strings (already cleaned)."""
    return CurrentJob(
        filename="test.nc",
        filepath=Path("/tmp/test.nc"),
        lines=lines,
    )


@pytest.fixture
async def env():
    """Shared communicator + mock transport in auto-ok mode, job runner ready."""
    transport = MockTransport(auto_ok=True)
    state = AppState()
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    yield comm, transport, state
    await comm.stop()


class TestBasicStreaming:
    async def test_simple_job_completes(self, env):
        comm, transport, state = env
        job = make_job(["G0 X0 Y0", "G1 X10 F500", "G1 X20"])

        runner = JobRunner(comm, job)
        task = asyncio.create_task(runner.run())
        await asyncio.wait_for(task, timeout=3.0)

        assert job.state == "completed"
        assert job.current_line == 3

    async def test_all_lines_sent(self, env):
        comm, transport, state = env
        lines = ["G0 X0", "G1 X10 F500", "G1 X20 Y5"]
        job = make_job(lines)

        await asyncio.wait_for(JobRunner(comm, job).run(), timeout=3.0)

        for line in lines:
            assert any(line in sent for sent in transport.sent_lines), \
                f"Expected '{line}' to be sent"

    async def test_m2_ends_job_early(self, env):
        comm, transport, state = env
        job = make_job(["G0 X0", "M2", "G0 X100"])  # G0 X100 should NOT be sent

        await asyncio.wait_for(JobRunner(comm, job).run(), timeout=3.0)

        assert job.state == "completed"
        sent = transport.sent_lines
        assert not any("X100" in s for s in sent), \
            "Lines after M2 should not be sent"

    async def test_progress_increments(self, env):
        comm, transport, state = env
        job = make_job(["G0 X0", "G1 X10", "G1 X20"])

        task = asyncio.create_task(JobRunner(comm, job).run())
        await asyncio.wait_for(task, timeout=3.0)

        assert job.current_line == 3

    async def test_grbl_error_stops_job(self, env):
        comm, transport, state = env
        transport._auto_ok = False  # We'll control responses manually

        job = make_job(["G0 X0", "INVALID", "G0 X10"])

        async def respond():
            await asyncio.sleep(0.02)
            await transport.inject("ok")     # for G0 X0
            await asyncio.sleep(0.02)
            await transport.inject("error:20")  # for INVALID

        asyncio.create_task(respond())
        await asyncio.wait_for(JobRunner(comm, job).run(), timeout=3.0)

        assert job.state == "error"
        assert "error:20" in job.error
        # Third line should not have been sent
        sent = transport.sent_lines
        assert not any("X10" in s for s in sent)


class TestHoldResume:
    async def test_hold_pauses_streaming(self, env):
        """
        Coordinate on sent_lines to avoid pre-injecting oks into the queue.

        Key invariant: in GRBLCommunicator.send_command(), pending_future is
        created BEFORE write_line() appends to sent_lines.  So once a command
        appears in sent_lines, it is safe to inject the corresponding ok.
        """
        comm, transport, state = env
        transport._auto_ok = False

        job = make_job(["G0 X0", "G1 X10", "G1 X20"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        # Wait until G0 X0 is sent (pending_future now exists), then hold
        await wait_until_sent(transport, 1)
        job.state = "held"
        await transport.inject("ok")      # ok for G0 X0

        # Give runner time to process ok and enter the hold loop
        await asyncio.sleep(0.12)
        assert job.current_line == 1
        assert not any("X10" in s for s in transport.sent_lines), \
            "G1 X10 should not be sent while held"

        # Resume: wait until G1 X10 appears in sent_lines, then inject ok
        job.state = "running"
        await wait_until_sent(transport, 2)     # G1 X10 sent
        await transport.inject("ok")

        # Wait until G1 X20 appears, then inject ok
        await wait_until_sent(transport, 3)     # G1 X20 sent
        await transport.inject("ok")

        await asyncio.wait_for(task, timeout=3.0)
        assert job.state == "completed"

    async def test_resume_continues_from_correct_line(self, env):
        comm, transport, state = env
        transport._auto_ok = False

        job = make_job(["G0 X0", "G1 X10", "G1 X20"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_until_sent(transport, 1)   # G0 X0 sent
        job.state = "held"
        await transport.inject("ok")           # ok for G0 X0
        await asyncio.sleep(0.1)               # runner enters hold loop

        assert not any("X10" in s for s in transport.sent_lines)

        job.state = "running"
        await wait_until_sent(transport, 2)    # G1 X10 sent
        await transport.inject("ok")
        await wait_until_sent(transport, 3)    # G1 X20 sent
        await transport.inject("ok")

        await asyncio.wait_for(task, timeout=3.0)

        sent = transport.sent_lines
        assert any("X0" in s for s in sent)
        assert any("X10" in s for s in sent)
        assert any("X20" in s for s in sent)
        assert job.state == "completed"


class TestStop:
    async def test_stop_cancels_runner(self, env):
        comm, transport, state = env
        transport._auto_ok = False

        job = make_job(["G0 X0", "G1 X10", "G1 X20", "G1 X30"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await asyncio.sleep(0.01)
        await transport.inject("ok")   # ok for G0 X0
        await asyncio.sleep(0.02)

        job.state = "stopped"
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert task.done()

    async def test_stop_while_held_exits_cleanly(self, env):
        """
        Coordinate on sent_lines so we know the pending_future exists before
        injecting ok.  Set held, inject ok, wait for hold loop, then set stopped.
        The while-held loop sees state != 'held', exits, and the if-stopped
        guard causes the runner to return cleanly.
        """
        comm, transport, state = env
        transport._auto_ok = False

        job = make_job(["G0 X0", "G1 X10"])
        task = asyncio.create_task(JobRunner(comm, job).run())

        await wait_until_sent(transport, 1)   # G0 X0 sent
        job.state = "held"
        await transport.inject("ok")           # ok for G0 X0
        await asyncio.sleep(0.1)               # runner enters hold loop

        assert job.current_line == 1
        assert not any("X10" in s for s in transport.sent_lines)

        job.state = "stopped"
        await asyncio.wait_for(task, timeout=2.0)
        assert job.state == "stopped"


class TestBrowserIndependence:
    async def test_runner_runs_without_websocket_connection(self, env):
        """
        Verify the runner is an asyncio Task on the server — it has no WebSocket
        dependency and runs to completion with no clients connected.
        """
        comm, transport, state = env
        job = make_job(["G0 X0", "G1 X10 F500"])

        # No WebSocket connected — just run the task
        task = asyncio.create_task(JobRunner(comm, job).run())
        await asyncio.wait_for(task, timeout=3.0)

        assert job.state == "completed"

    async def test_job_continues_after_state_read(self, env):
        """
        Reading AppState (as a reconnecting browser would) doesn't interrupt streaming.
        """
        comm, transport, state = env
        job = make_job(["G0 X0", "G1 X10", "G1 X20"])

        task = asyncio.create_task(JobRunner(comm, job).run())

        # Simulate a browser reconnect reading state mid-stream
        await asyncio.sleep(0.05)
        _ = state.to_dict()  # should not affect runner

        await asyncio.wait_for(task, timeout=3.0)
        assert job.state == "completed"


class TestLoadJobFromFile:
    def test_loads_and_strips_comments(self, tmp_path):
        f = tmp_path / "test.nc"
        f.write_text(
            "; header comment\n"
            "G21 ; metric\n"
            "G90\n"
            "\n"
            "(another comment)\n"
            "G0 X0 Y0\n"
            "M2\n"
        )
        job = load_job_from_file(f)
        assert job.filename == "test.nc"
        assert "G21" in job.lines
        assert "G90" in job.lines
        assert "G0 X0 Y0" in job.lines
        assert "M2" in job.lines
        # Blank lines and comment-only lines should be excluded
        for line in job.lines:
            assert line.strip() != ""
            assert not line.strip().startswith(";")

    def test_total_lines_matches_sendable(self, tmp_path):
        f = tmp_path / "test.nc"
        f.write_text("G0 X0\n; comment\n\nG1 X10\n")
        job = load_job_from_file(f)
        assert job.total_lines == 2  # only G0 X0 and G1 X10
