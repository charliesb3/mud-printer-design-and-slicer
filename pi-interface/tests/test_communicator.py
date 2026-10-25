"""
Tests for GRBLCommunicator using MockTransport.

These tests verify application-level behavior (state updates, command routing,
disconnect handling) without any real serial port or GRBL firmware.
"""
from __future__ import annotations

import asyncio
import pytest

from app.grbl.communicator import GRBLCommunicator
from app.state import AppState
from .mock_transport import (
    MockTransport,
    wait_for,
    IDLE_STATUS,
    RUN_STATUS,
    HOLD_STATUS,
    ALARM_STATUS,
    WELCOME_MSG,
)


@pytest.fixture
def state():
    return AppState()


@pytest.fixture
def transport():
    return MockTransport()


@pytest.fixture
async def comm_started(state, transport):
    """GRBLCommunicator started with a MockTransport."""
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    yield comm, transport, state
    await comm.stop()


class TestStatusReporting:
    async def test_idle_status_updates_state(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(IDLE_STATUS)
        await wait_for(lambda: state.grbl_state == "Idle")
        assert state.connected is True
        assert state.mpos_x == pytest.approx(0.0)
        assert state.mpos_y == pytest.approx(0.0)

    async def test_run_status_updates_state(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(RUN_STATUS)
        await wait_for(lambda: state.grbl_state == "Run")
        assert state.mpos_x == pytest.approx(5.0)
        assert state.mpos_y == pytest.approx(2.0)
        assert state.feed_rate == pytest.approx(1000.0)

    async def test_hold_status_with_substate(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(HOLD_STATUS)
        await wait_for(lambda: "Hold" in state.grbl_state)
        assert state.grbl_state == "Hold:0"

    async def test_alarm_status(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(ALARM_STATUS)
        await wait_for(lambda: "Alarm" in state.grbl_state)


class TestCommandSending:
    async def test_command_gets_ok(self, comm_started):
        comm, transport, state = comm_started

        async def respond():
            await asyncio.sleep(0.02)
            await transport.inject("ok")

        asyncio.create_task(respond())
        result = await comm.send_command("G0 X0")
        assert result == "ok"

    async def test_command_gets_error(self, comm_started):
        comm, transport, state = comm_started

        async def respond():
            await asyncio.sleep(0.02)
            await transport.inject("error:20")

        asyncio.create_task(respond())
        result = await comm.send_command("bad_command")
        assert result == "error:20"

    async def test_command_appears_in_tx_log(self, comm_started):
        comm, transport, state = comm_started

        async def respond():
            await asyncio.sleep(0.02)
            await transport.inject("ok")

        asyncio.create_task(respond())
        await comm.send_command("G0 X10")
        assert any("G0 X10" in line for line in transport.sent_lines)

    async def test_command_timeout(self, comm_started):
        comm, transport, state = comm_started
        # No response injected — should time out
        with pytest.raises(asyncio.TimeoutError):
            await comm.send_command("G0 X0", timeout=0.1)

    async def test_command_rejected_when_not_connected(self, state):
        comm = GRBLCommunicator(state)
        # Transport never started
        with pytest.raises(RuntimeError, match="Not connected"):
            await comm.send_command("G0 X0")


class TestRealtimeCommands:
    async def test_realtime_sends_raw_byte(self, comm_started):
        comm, transport, state = comm_started
        await comm.send_realtime(b"!")
        assert b"!" in transport.raw_sent

    async def test_jog_cancel_byte(self, comm_started):
        comm, transport, state = comm_started
        await comm.send_realtime(b"\x85")
        assert b"\x85" in transport.raw_sent

    async def test_realtime_ignored_when_not_connected(self, state):
        comm = GRBLCommunicator(state)
        # Should not raise
        await comm.send_realtime(b"!")


class TestSerialWriteOrdering:
    """
    Byte-level serial write atomicity (SerialTransport._write_lock).

    Without the lock, a $J=… `write_line` and a 0x85 `write_raw` dispatched
    on different asyncio.to_thread worker threads could interleave their
    bytes on the wire. GRBL reads real-time bytes mid-line, so an embedded
    0x85 would cancel the current jog AND the finished $J= would start a
    new jog — the "second burst" observed on hardware. The lock serializes
    all writes to the pyserial Serial.write() call so each write appears
    atomically on the wire.
    """

    async def test_concurrent_writes_never_interleave_bytes(self):
        from app.grbl.transport import SerialTransport
        import time
        import threading

        # A fake serial whose write() has a small, nondeterministic delay —
        # a window where the OTHER thread would interleave if there were no
        # lock. We record each write as one entry (not byte-by-byte).
        class SlowSerial:
            def __init__(self):
                self.writes: list[bytes] = []
                self.inside = 0
                self.max_concurrent = 0
                self._state_lock = threading.Lock()

            def write(self, data):
                with self._state_lock:
                    self.inside += 1
                    self.max_concurrent = max(self.max_concurrent, self.inside)
                # Sleep OUTSIDE the state lock — this is the window where
                # another thread could run concurrently if the transport's
                # write lock didn't exist.
                time.sleep(0.02)
                with self._state_lock:
                    self.writes.append(bytes(data))
                    self.inside -= 1

            @property
            def is_open(self):
                return True

        slow = SlowSerial()
        tr = SerialTransport.__new__(SerialTransport)
        tr._port = "/dev/null"
        tr._baud_rate = 115200
        tr._serial = slow
        tr._read_queue = asyncio.Queue()
        tr._running = True
        tr._reader_thread = None
        tr._write_lock = threading.Lock()

        # Fire many concurrent writes mixing full lines and real-time bytes.
        async def write_line(line):
            await tr.write_line(line)

        async def write_realtime():
            await tr.write_raw(b"\x85")

        async def write_status_poll():
            await tr.write_raw(b"?")   # GRBL real-time status poll byte

        await asyncio.gather(
            write_line("$J=G21 G91 X254.000 F3048"),
            write_realtime(),
            write_line("$J=G21 G91 Y254.000 F3048"),
            write_realtime(),
            write_status_poll(),
        )

        # Each call must have landed as ONE atomic write (not split / mixed).
        assert slow.max_concurrent == 1, (
            f"writes ran concurrently (max_concurrent={slow.max_concurrent}) "
            "— the SerialTransport._write_lock is missing or ineffective")
        # Every originally submitted byte sequence must appear as one write.
        expected = {b"$J=G21 G91 X254.000 F3048\n",
                    b"\x85",
                    b"$J=G21 G91 Y254.000 F3048\n",
                    b"?"}
        got = set(slow.writes)
        for e in expected:
            assert e in got, f"missing atomic write: {e!r}; got: {got!r}"


class TestConnectionHandling:
    async def test_welcome_message_updates_version(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(WELCOME_MSG)
        await wait_for(lambda: state.grbl_version is not None)
        assert state.grbl_version == "1.1f"

    async def test_welcome_message_sets_connected_idle(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(WELCOME_MSG)
        await wait_for(lambda: state.grbl_state == "Idle")
        assert state.connected is True

    async def test_disconnect_marks_state(self, comm_started):
        comm, transport, state = comm_started
        # First confirm connected
        await transport.inject(IDLE_STATUS)
        await wait_for(lambda: state.connected is True)

        # Now disconnect
        await transport.disconnect()
        await wait_for(lambda: state.connected is False, timeout=2.0)
        assert state.grbl_state == "Disconnected"

    async def test_alarm_rejects_pending_command(self, comm_started):
        comm, transport, state = comm_started

        async def alarm_after_delay():
            await asyncio.sleep(0.05)
            await transport.inject("ALARM:1")

        asyncio.create_task(alarm_after_delay())
        # ALARM:1 causes _reject_pending to set an exception on the future,
        # which propagates through send_command as RuntimeError.
        with pytest.raises(RuntimeError):
            await comm.send_command("G0 X0")

    async def test_reset_mid_command_rejects_pending(self, comm_started):
        comm, transport, state = comm_started

        async def reset_after_delay():
            await asyncio.sleep(0.05)
            await transport.inject(WELCOME_MSG)

        asyncio.create_task(reset_after_delay())
        with pytest.raises(Exception, match="reset"):
            await comm.send_command("G0 X0")

    async def test_console_log_receives_rx(self, comm_started):
        comm, transport, state = comm_started
        await transport.inject(IDLE_STATUS)
        await asyncio.sleep(0.05)
        lines = [e["line"] for e in state.console_recent]
        assert any(IDLE_STATUS in line for line in lines)
