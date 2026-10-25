"""
Raspberry Pi hardware integration (2026-10-20), with fake / mock hardware only:

- the authoritative GPIO map and the gpiozero backend (gpiozero MockFactory);
- active-low remote interpretation, debounce, startup arming;
- the ONE manual Z controller: hold / release, UP + DOWN = STOP, dead-man,
  run limit, exception and shutdown → STOP, fixed duty;
- REMOTE STOP: stops Z, cancels jog, feed-holds GRBL, holds the job, latches,
  refuses new motion, needs an explicit UI clear, keeps the job resumable;
- AUX / extrusion inputs do nothing; no-GPIO development keeps working.
"""
from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config, hardware, sdnotify
from app.api import http as http_module
from app.api.ws import ConnectionManager
from app.grbl.communicator import GRBLCommunicator
from app.hardware import pins
from app.hardware.backend import GpioZeroBackend, MockBackend, NoHardwareBackend, create_backend
from app.hardware.remote import RemoteInputs
from app.hardware.service import HardwareService
from app.hardware.z import DOWN, UP, ZController
from app.jobs.current_job import CurrentJob
from app.state import AppState
from .mock_transport import MockTransport, wait_for, wait_until_sent


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_z(backend=None, clock=None, duty=0.6, interlock=lambda: None, **kw) -> tuple:
    backend = backend or MockBackend()
    clock = clock or Clock()
    events = []
    z = ZController(backend, duty=duty, timeout_s=kw.pop("timeout_s", 0.3), max_run_s=kw.pop("max_run_s", 30.0),
                    interlock=interlock, event=lambda k, t: events.append(t), clock=clock, **kw)
    return z, backend, clock, events


# ------------------------------------------------------------------ #
# GPIO map                                                             #
# ------------------------------------------------------------------ #

class TestPinMap:
    def test_exact_wired_map(self):
        got = {c.key: (c.channel, c.bcm, c.physical) for c in pins.REMOTE_CHANNELS}
        assert got == {
            "remote_stop": (1, 17, 11),
            "z_up": (2, 27, 13),
            "z_down": (3, 22, 15),
            "extrusion": (4, 5, 29),
            "aux1": (5, 6, 31),
            "aux2": (6, 13, 33),
        }
        assert (pins.Z_PWM_BCM, pins.Z_PWM_PHYSICAL) == (18, 12)
        assert (pins.Z_DIR_BCM, pins.Z_DIR_PHYSICAL) == (23, 16)
        assert pins.SIGNAL_GROUND_PHYSICAL == 9
        assert pins.REMOTE_ACTIVE_LOW is True

    def test_no_duplicates_and_never_physical_27(self):
        assert len(set(pins.all_bcm())) == len(pins.all_bcm()) == 8
        assert 27 not in [c.physical for c in pins.REMOTE_CHANNELS]      # the erroneous CH6 diagram

    def test_pin_numbers_live_only_in_pins_py(self):
        # no other module configures a GPIO by number
        app_dir = Path(__file__).resolve().parents[1] / "app"
        for f in app_dir.rglob("*.py"):
            if f.name == "pins.py":
                continue
            text = f.read_text()
            for bad in ("GPIO17", "GPIO18", "GPIO23", "DigitalInputDevice(1", "PWMOutputDevice(1"):
                assert bad not in text, (f, bad)


# ------------------------------------------------------------------ #
# gpiozero backend on gpiozero's MockFactory                           #
# ------------------------------------------------------------------ #

@pytest.fixture
def mock_factory():
    gz = pytest.importorskip("gpiozero")
    from gpiozero.pins.mock import MockFactory, MockPWMPin
    factory = MockFactory(pin_class=MockPWMPin)
    yield factory
    factory.close()
    gz.Device.pin_factory = None


class TestGpioZeroBackend:
    def test_startup_outputs_stop_inputs_pulled_up(self, mock_factory):
        b = GpioZeroBackend(1000, pin_factory=mock_factory)
        pwm, d = mock_factory.pin(pins.Z_PWM_BCM), mock_factory.pin(pins.Z_DIR_BCM)
        assert pwm.function == "output" and pwm.state == 0 and pwm.frequency == 1000
        assert d.function == "output" and d.state == 0
        for c in pins.REMOTE_CHANNELS:
            p = mock_factory.pin(c.bcm)
            assert p.function == "input" and p.pull == "up", c
        assert all(b.read_levels().values())                     # released = HIGH
        b.close()

    def test_active_low_and_z_outputs(self, mock_factory):
        b = GpioZeroBackend(1000, pin_factory=mock_factory)
        mock_factory.pin(pins.Z_UP.bcm).drive_low()               # contact closed to GND
        levels = b.read_levels()
        assert levels[pins.Z_UP.bcm] is False and RemoteInputs.interpret(levels[pins.Z_UP.bcm]) is True
        assert RemoteInputs.interpret(levels[pins.Z_DOWN.bcm]) is False
        b.set_z(0.6, True)
        assert mock_factory.pin(pins.Z_PWM_BCM).state == pytest.approx(0.6)
        assert mock_factory.pin(pins.Z_DIR_BCM).state == 1
        b.set_z(0.6, False)                                       # reverse: DIR changes, PWM back on
        assert mock_factory.pin(pins.Z_DIR_BCM).state == 0
        b.set_z(0.0, False)
        assert mock_factory.pin(pins.Z_PWM_BCM).state == 0
        b.set_z(0.6, True)
        b.close()                                                  # shutdown → PWM 0 before release
        assert mock_factory.pin(pins.Z_PWM_BCM).state == 0

    def test_backend_factory(self):
        assert isinstance(create_backend("none", 1000), NoHardwareBackend)
        assert isinstance(create_backend("MOCK", 1000), MockBackend)
        with pytest.raises(ValueError):
            create_backend("relay", 1000)


# ------------------------------------------------------------------ #
# Remote inputs: debounce, arming                                      #
# ------------------------------------------------------------------ #

def run_remote(remote, clock, seconds, step=0.01):
    out = []
    for _ in range(round(seconds / step)):
        out += remote.poll()
        clock.advance(step)
    return out


class TestRemoteInputs:
    def test_nothing_before_settle_then_released_and_armed(self):
        b, clk = MockBackend(), Clock()
        r = RemoteInputs(b, debounce_s=0.05, clock=clk)
        r.poll()
        assert all(c["state"] == "unknown" for c in r.to_list())
        assert run_remote(r, clk, 0.3) == []
        assert all(c["state"] == "released" and c["armed"] for c in r.to_list())

    def test_bounce_shorter_than_debounce_is_ignored(self):
        b, clk = MockBackend(), Clock()
        r = RemoteInputs(b, debounce_s=0.05, clock=clk)
        run_remote(r, clk, 0.3)
        b.press(pins.Z_UP)
        assert run_remote(r, clk, 0.03) == []
        b.release(pins.Z_UP)
        assert run_remote(r, clk, 0.2) == []
        b.press(pins.Z_UP)
        t = run_remote(r, clk, 0.1)
        assert [(x.channel.key, x.pressed) for x in t] == [("z_up", True)]
        assert r.is_pressed("z_up")
        b.release(pins.Z_UP)
        t = run_remote(r, clk, 0.1)
        assert [(x.channel.key, x.pressed) for x in t] == [("z_up", False)]

    def test_held_at_startup_is_not_armed(self):
        b, clk = MockBackend(), Clock()
        b.press(pins.Z_UP)
        r = RemoteInputs(b, debounce_s=0.05, clock=clk)
        t = run_remote(r, clk, 0.4)
        assert [(x.channel.key, x.startup) for x in t] == [("z_up", True)]
        assert not r.is_pressed("z_up")                         # never acted on
        b.release(pins.Z_UP)
        assert run_remote(r, clk, 0.1) == []                    # the arming release is not an action
        b.press(pins.Z_UP)
        assert [x.pressed for x in run_remote(r, clk, 0.1)] == [True]

    def test_no_gpio_backend_reads_nothing(self):
        r = RemoteInputs(NoHardwareBackend(), debounce_s=0.05)
        assert r.poll() == []


# ------------------------------------------------------------------ #
# Manual Z controller                                                  #
# ------------------------------------------------------------------ #

class TestZController:
    def test_startup_is_stop(self):
        z, b, _, _ = make_z()
        assert b.z_log == [(0.0, False)] and z.command == 0

    def test_up_hold_release_with_fixed_duty(self):
        z, b, clk, _ = make_z(duty=0.45)
        assert z.request("ui:1", UP) is None
        assert z.command == UP and b.duty == 0.45 and b.dir_level is True
        clk.advance(0.2); z.refresh("ui:1"); clk.advance(0.2); z.tick()
        assert z.command == UP                                    # held + refreshed = still moving
        z.release("ui:1")
        assert z.command == 0 and b.duty == 0.0

    def test_down_hold_release(self):
        z, b, _, _ = make_z()
        z.request("remote_down", DOWN)
        assert z.command == DOWN and b.duty == 0.6 and b.dir_level is False
        z.release("remote_down")
        assert b.duty == 0.0

    def test_dir_invert(self):
        z, b, _, _ = make_z(dir_invert=True)
        z.request("ui:1", UP)
        assert b.dir_level is False

    def test_up_plus_down_is_stop_until_all_released(self):
        z, b, _, events = make_z()
        z.request("remote_up", UP)
        assert z.command == UP
        assert z.request("remote_down", DOWN) is not None
        assert z.command == 0 and b.duty == 0.0 and z.blocked
        z.release("remote_down")                                   # UP still held: stays stopped
        assert z.command == 0 and b.duty == 0.0
        z.release("remote_up")
        assert z.blocked is None
        z.request("remote_up", UP)
        assert z.command == UP
        assert any("contradictory" in e for e in events)

    def test_ui_and_remote_contradiction(self):
        z, b, _, _ = make_z()
        z.request("ui:1", DOWN)
        z.request("remote_up", UP)
        assert b.duty == 0.0 and z.blocked

    def test_source_reversing_is_contradictory(self):
        z, b, _, _ = make_z()
        z.request("ui:1", UP)
        z.request("ui:1", DOWN)
        assert b.duty == 0.0 and z.blocked

    def test_dead_man_timeout(self):
        z, b, clk, _ = make_z()
        z.request("ui:1", UP)
        clk.advance(0.31); z.tick()
        assert z.command == 0 and b.duty == 0.0 and "dead-man" in z.last_stop

    def test_max_run(self):
        z, b, clk, _ = make_z(max_run_s=2.0)
        z.request("remote_up", UP)
        for _ in range(25):
            clk.advance(0.1); z.refresh("remote_up"); z.tick()
        assert b.duty == 0.0 and "maximum continuous run" in z.blocked

    def test_exception_forces_stop_and_fault(self):
        z, b, _, _ = make_z()
        b.fail_next_set = OSError("gpiochip gone")
        z.request("ui:1", UP)
        assert z.command == 0 and b.duty == 0.0 and "gpiochip gone" in z.fault
        assert z.request("ui:1", UP) is not None and b.duty == 0.0  # refused until restart

    def test_shutdown_stops(self):
        z, b, _, _ = make_z()
        z.request("ui:1", UP)
        z.close()
        assert b.duty == 0.0 and z.command == 0

    def test_disabled_without_duty_and_without_hardware(self):
        z, b, _, _ = make_z(duty=0.0)
        assert "Z_PWM_DUTY" in z.request("ui:1", UP) and b.duty == 0.0
        z2, _, _, _ = make_z(backend=NoHardwareBackend())
        assert "No Z hardware" in z2.request("ui:1", UP)

    def test_refused_request_never_starts_later(self):
        latch = {"on": True}
        z, b, clk, _ = make_z(interlock=lambda: "REMOTE STOP is latched" if latch["on"] else None)
        assert z.request("remote_up", UP) is not None
        latch["on"] = False                                        # cleared while Z UP still held
        z.refresh("remote_up"); z.tick()
        assert b.duty == 0.0 and z.command == 0
        z.release("remote_up")
        z.request("remote_up", UP)
        assert z.command == UP


# ------------------------------------------------------------------ #
# Service + machine integration: REMOTE STOP, remote Z, AUX            #
# ------------------------------------------------------------------ #

class FakeWS:
    def __init__(self):
        self.sent: list = []

    async def send_json(self, data):
        self.sent.append(data)


def notices(ws):
    return [m["message"] for m in ws.sent if m.get("type") == "notice"]


@pytest.fixture
async def rig(monkeypatch):
    """GRBL on a MockTransport + mock GPIO hardware, with a fake clock for the inputs / Z."""
    monkeypatch.setattr(config, "STATUS_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(config, "GRBL_UNRESPONSIVE_S", 0.5)
    monkeypatch.setattr(config, "JOG_SPEED_MM_MIN", 254.0)
    transport = MockTransport(auto_ok=False, status_reply="<Idle|MPos:0.000,0.000,0.000|FS:0,0>")
    state = AppState()
    comm = GRBLCommunicator(state)
    await comm.start(transport)
    http_module.init(state, comm, ConnectionManager())
    http_module._current_job = None
    http_module._runner_task = None
    clk = Clock()
    backend = MockBackend()
    z = ZController(backend, duty=0.5, timeout_s=0.3, max_run_s=30, interlock=state.remote_stop.refusal,
                    event=state.add_hw_event, clock=clk)
    remote = RemoteInputs(backend, debounce_s=0.05, clock=clk)
    svc = HardwareService(backend, remote, z, poll_interval_s=0.01, on_remote_stop=http_module.remote_stop,
                          event=state.add_hw_event)
    state.hardware = svc
    await wait_for(lambda: state.grbl_state == "Idle", timeout=2.0)

    async def run(seconds, step=0.01):
        for _ in range(round(seconds / step)):
            await svc.step()
            clk.advance(step)

    async def ok_pump():
        """GRBL acknowledging each line ~10 ms after it arrives (a slow, observable stream)."""
        acked = 0
        while True:
            if len(transport.sent_lines) > acked:
                acked += 1
                await transport.inject("ok")
            await asyncio.sleep(0.01)

    await run(0.3)                                                # inputs settle, all released + armed
    pump = asyncio.create_task(ok_pump())
    rig = SimpleNamespace(transport=transport, state=state, comm=comm, backend=backend, z=z, svc=svc,
                          clock=clk, run=run)
    yield rig
    pump.cancel()
    await http_module._stop_jog()
    if http_module._runner_task and not http_module._runner_task.done():
        http_module._runner_task.cancel()
        try:
            await http_module._runner_task
        except (asyncio.CancelledError, Exception):
            pass
    http_module._current_job = None
    http_module._runner_task = None
    http_module._state = None
    http_module._comm = None
    await comm.stop()


async def msg(data, ws=None):
    ws = ws or FakeWS()
    await http_module._handle_message(data, ws)
    return ws


class TestRemoteZ:
    async def test_remote_up_hold_release(self, rig):
        rig.backend.press(pins.Z_UP)
        await rig.run(0.1)
        assert rig.z.command == UP and rig.backend.duty == 0.5
        await rig.run(1.0)                                        # held: the poll refreshes the dead-man
        assert rig.z.command == UP
        rig.backend.release(pins.Z_UP)
        await rig.run(0.1)
        assert rig.z.command == 0 and rig.backend.duty == 0.0

    async def test_remote_up_and_down_together_stop(self, rig):
        rig.backend.press(pins.Z_UP)
        await rig.run(0.1)
        rig.backend.press(pins.Z_DOWN)
        await rig.run(0.1)
        assert rig.backend.duty == 0.0 and rig.z.blocked

    async def test_ui_and_remote_use_the_same_controller(self, rig):
        ws = FakeWS()
        await msg({"type": "z_start", "direction": "down"}, ws)
        assert rig.z.command == DOWN
        rig.backend.press(pins.Z_UP)
        await rig.run(0.1)
        assert rig.backend.duty == 0.0                            # contradiction across sources

    async def test_ui_z_dead_man(self, rig):
        ws = FakeWS()
        await msg({"type": "z_start", "direction": "up"}, ws)
        assert rig.z.command == UP
        await rig.run(0.2)
        await msg({"type": "z_heartbeat"}, ws)
        await rig.run(0.2)
        assert rig.z.command == UP
        await rig.run(0.2)                                       # heartbeats stopped (client gone)
        assert rig.z.command == 0 and rig.backend.duty == 0.0

    async def test_z_stop_message_releases_the_source(self, rig):
        """A clean z_stop clears the per-WS source request so no heartbeat
        can keep Z alive after a release."""
        ws = FakeWS()
        await msg({"type": "z_start", "direction": "up"}, ws)
        assert rig.z.command == UP
        source = http_module._z_source(ws)
        assert source in rig.z._requests
        await msg({"type": "z_stop"}, ws)
        await rig.run(0.01)
        assert rig.z.command == 0 and rig.backend.duty == 0.0
        assert source not in rig.z._requests, "z_stop did not release the source"

    async def test_z_max_run_ceiling_still_fires(self, rig, monkeypatch):
        """Server-side 30 s ceiling (`Z_MAX_RUN_S`) is unchanged — if a
        client somehow keeps heartbeats flowing past the ceiling, the
        controller still forces STOP + blocks until all inputs are released."""
        # Shrink the ceiling so the test is fast; this is a knob on the
        # ZController, so no code change is required.
        rig.z.max_run_s = 0.5
        ws = FakeWS()
        await msg({"type": "z_start", "direction": "up"}, ws)
        assert rig.z.command == UP
        for _ in range(10):
            await msg({"type": "z_heartbeat"}, ws)
            await rig.run(0.1)
        # By now > 0.5 s of continuous run — ceiling should have fired.
        assert rig.z.command == 0 and rig.z.blocked and "maximum continuous run" in rig.z.blocked
        # Blocked until every input is released, even if heartbeats continue.
        await msg({"type": "z_heartbeat"}, ws)
        await rig.run(0.1)
        assert rig.z.command == 0 and rig.z.blocked
        # Explicit release clears the block for the next deliberate press.
        await msg({"type": "z_stop"}, ws)
        await rig.run(0.1)
        assert rig.z.blocked is None

    async def test_ws_cleanup_releases_held_z(self, rig):
        """Simulate the WebSocket endpoint's `finally` cleanup: when a WS
        disappears while Z is held, the source MUST be released. This is
        the primary safety net for a closed browser tab mid-press.
        """
        ws = FakeWS()
        await msg({"type": "z_start", "direction": "down"}, ws)
        assert rig.z.command == DOWN
        source = http_module._z_source(ws)
        assert source in rig.z._requests
        # Simulate the endpoint's finally block — this is literally the
        # cleanup line in websocket_endpoint.
        rig.state.hardware.z.release(source)
        await rig.run(0.01)
        assert rig.z.command == 0 and rig.backend.duty == 0.0
        assert source not in rig.z._requests

    async def test_aux_and_extrusion_do_nothing(self, rig):
        writes = (len(rig.transport.sent_lines),)
        z_log = list(rig.backend.z_log)
        for ch in (pins.EXTRUSION, pins.AUX1, pins.AUX2):
            rig.backend.press(ch)
            await rig.run(0.1)
            rig.backend.release(ch)
            await rig.run(0.1)
        assert rig.backend.z_log == z_log
        assert len(rig.transport.sent_lines) == writes[0]
        assert [b for b in rig.transport.raw_sent if b != b"?"] == []
        assert not rig.state.remote_stop.latched
        texts = [e["text"] for e in rig.state.hw_events]
        assert any("CH4" in t and "pressed" in t for t in texts)   # logged, nothing else


def running_job(n=40) -> CurrentJob:
    return CurrentJob(filename="t.gcode", filepath=Path("/tmp/t.gcode"),
                      lines=[f"G1 X{i} F100" for i in range(n)])


class TestJogRelease:
    """
    Single-shot jog model (2026-10-25). Each press-and-hold session emits
    EXACTLY ONE `$J=…` line (at start) and EXACTLY ONE `0x85` real-time byte
    (at release / heartbeat timeout / cancel). GRBL never has more than one
    jog in its planner, and no further `$J=` can ever be written after a
    release.

    These tests substitute for physical verification of the "second burst"
    fix. Byte-level serial atomicity between a jog `$J=` and the stop `0x85`
    is covered separately in TestSerialWriteOrdering (byte interleave is the
    other half of the second-burst root cause).
    """

    async def test_jog_start_writes_exactly_one_long_distance_line(self, rig, monkeypatch):
        monkeypatch.setattr(config, "JOG_SINGLE_DISTANCE_IN", 10.0)
        ws = FakeWS()
        await msg({"type": "jog_start", "axis": "X", "direction": 1}, ws)
        await wait_until_sent(rig.transport, 1)
        # Give the heartbeat loop time to settle in its wait — it must NOT
        # write a second $J=.
        for _ in range(5):
            await asyncio.sleep(0.05)
            await msg({"type": "jog_heartbeat"}, ws)
        jogs = [l for l in rig.transport.sent_lines if l.startswith("$J=")]
        assert len(jogs) == 1, f"expected exactly one $J=, got {jogs!r}"
        line = jogs[0]
        # 10 in × 25.4 = 254 mm; direction = +.
        assert "X254.000" in line or "X254.0" in line, line
        assert "G21" in line and "G91" in line, line
        # Clean up.
        await msg({"type": "jog_stop"}, ws)

    async def test_jog_stop_sends_0x85_and_no_more_jogs_after(self, rig, monkeypatch):
        monkeypatch.setattr(config, "JOG_SINGLE_DISTANCE_IN", 10.0)
        ws = FakeWS()
        await msg({"type": "jog_start", "axis": "X", "direction": 1}, ws)
        await wait_until_sent(rig.transport, 1)
        raws_before = len(rig.transport.raw_sent)

        await msg({"type": "jog_stop"}, ws)
        # 0x85 written by the task's finally — the one place it is sent.
        new_raws = rig.transport.raw_sent[raws_before:]
        assert b"\x85" in new_raws, new_raws
        # The task has exited.
        assert http_module._jog_task is None or http_module._jog_task.done()
        # No $J= appears after the single startup one, even if a late ok
        # drains in afterwards.
        await asyncio.sleep(0.15)
        jogs = [l for l in rig.transport.sent_lines if l.startswith("$J=")]
        assert len(jogs) == 1, f"$J= written after release: {jogs!r}"

    async def test_heartbeat_timeout_cancels_without_a_second_jog(self, rig, monkeypatch):
        monkeypatch.setattr(config, "JOG_SINGLE_DISTANCE_IN", 10.0)
        monkeypatch.setattr(config, "JOG_TIMEOUT_S", 0.1)
        ws = FakeWS()
        await msg({"type": "jog_start", "axis": "Y", "direction": -1}, ws)
        await wait_until_sent(rig.transport, 1)
        raws_before = len(rig.transport.raw_sent)
        # Stop sending heartbeats; the loop should time out and send 0x85.
        await asyncio.sleep(0.3)
        new_raws = rig.transport.raw_sent[raws_before:]
        assert b"\x85" in new_raws, new_raws
        jogs = [l for l in rig.transport.sent_lines if l.startswith("$J=")]
        assert len(jogs) == 1, f"$J= written on heartbeat timeout: {jogs!r}"

    async def test_repeat_press_release_press(self, rig, monkeypatch):
        monkeypatch.setattr(config, "JOG_SINGLE_DISTANCE_IN", 10.0)
        wsA = FakeWS()
        await msg({"type": "jog_start", "axis": "X", "direction": 1}, wsA)
        await wait_until_sent(rig.transport, 1)
        await msg({"type": "jog_stop"}, wsA)
        assert http_module._jog_task is None or http_module._jog_task.done()
        # Fresh press on a different axis.
        wsB = FakeWS()
        await msg({"type": "jog_start", "axis": "Y", "direction": -1}, wsB)
        await wait_until_sent(rig.transport, 2)
        jogs = [l for l in rig.transport.sent_lines if l.startswith("$J=")]
        assert len(jogs) == 2, f"expected 2 jog lines, got {jogs!r}"
        assert "X254" in jogs[0]
        assert "Y-254" in jogs[1]
        # No internal retention of the stop flag.
        assert not http_module._jog_stopping
        await msg({"type": "jog_stop"}, wsB)

    async def test_rapid_press_release_no_dangling_task(self, rig, monkeypatch):
        monkeypatch.setattr(config, "JOG_SINGLE_DISTANCE_IN", 10.0)
        # 10 fast cycles.
        ws = FakeWS()
        for _ in range(10):
            await msg({"type": "jog_start", "axis": "X", "direction": 1}, ws)
            await asyncio.sleep(0.01)
            await msg({"type": "jog_stop"}, ws)
        assert http_module._jog_task is None or http_module._jog_task.done()
        # Every session wrote exactly 1 $J= and ≥1 0x85.
        jogs = [l for l in rig.transport.sent_lines if l.startswith("$J=")]
        cancels = sum(1 for b in rig.transport.raw_sent if b == b"\x85")
        assert 1 <= len(jogs) <= 10
        assert cancels >= len(jogs), f"cancels={cancels} jogs={len(jogs)}"


class TestRemoteStop:
    async def press_stop(self, rig):
        rig.backend.press(pins.REMOTE_STOP)
        await rig.run(0.1)

    async def test_stops_z_and_latches_after_release(self, rig):
        rig.backend.press(pins.Z_UP)
        await rig.run(0.1)
        assert rig.backend.duty == 0.5
        await self.press_stop(rig)
        assert rig.backend.duty == 0.0 and rig.state.remote_stop.latched
        rig.backend.release(pins.REMOTE_STOP)
        await rig.run(0.3)
        assert rig.state.remote_stop.latched                      # releasing the RF button changes nothing
        assert rig.backend.duty == 0.0                             # Z UP still held: refused
        assert b"!" in rig.transport.raw_sent

    async def test_cancels_jog_with_realtime_bytes(self, rig):
        ws = FakeWS()
        await msg({"type": "jog_start", "axis": "X", "direction": 1}, ws)
        for _ in range(3):
            await msg({"type": "jog_heartbeat"}, ws)
            await asyncio.sleep(0.02)
        assert http_module._jog_task and not http_module._jog_task.done()
        await self.press_stop(rig)
        assert http_module._jog_task is None or http_module._jog_task.done()
        raw = rig.transport.raw_sent
        assert b"\x85" in raw and b"!" in raw
        assert not any(l.startswith("!") or l == "~" for l in rig.transport.sent_lines)   # never in the line stream

    async def test_holds_job_preserves_progress_and_requires_explicit_recovery(self, rig):
        job = running_job()
        http_module._current_job = job
        assert await http_module._start_job() is None
        await wait_for(lambda: job.current_line >= 5, timeout=2.0)
        await self.press_stop(rig)
        assert job.state == "held"
        await asyncio.sleep(0.05)                                  # a line already in flight may complete
        held_at = job.current_line
        await asyncio.sleep(0.2)
        assert job.current_line == held_at < job.total_lines      # nothing more streamed
        rig.backend.release(pins.REMOTE_STOP)
        await rig.run(0.3)

        # new motion refused while latched
        ws = FakeWS()
        await msg({"type": "resume"}, ws)
        await msg({"type": "jog_start", "axis": "X", "direction": 1}, ws)
        await msg({"type": "z_start", "direction": "up"}, ws)
        await msg({"type": "prime"}, ws)
        await msg({"type": "outline"}, ws)
        assert len(notices(ws)) == 5 and all("REMOTE STOP" in n for n in notices(ws))
        assert b"~" not in rig.transport.raw_sent and job.state == "held"
        assert rig.backend.duty == 0.0
        assert http_module._jog_task is None or http_module._jog_task.done()

        # explicit clear: moves nothing
        sent_before = (len(rig.transport.sent_lines), list(rig.transport.raw_sent))
        ws = FakeWS()
        await msg({"type": "remote_stop_clear"}, ws)
        assert not rig.state.remote_stop.latched
        assert "RESUME" in notices(ws)[0]
        await asyncio.sleep(0.1)
        assert job.state == "held" and job.current_line == held_at
        assert len(rig.transport.sent_lines) == sent_before[0]
        assert b"~" not in rig.transport.raw_sent

        # deliberate RESUME continues the SAME job from where it was held
        await msg({"type": "resume"})
        await wait_for(lambda: job.state == "completed", timeout=3.0)
        streamed = [l for l in rig.transport.sent_lines if l.startswith("G1 X")]
        assert streamed == job.lines                               # no line repeated or skipped

    async def test_clear_refused_while_button_still_pressed(self, rig):
        await self.press_stop(rig)
        ws = await msg({"type": "remote_stop_clear"})
        assert "still reads PRESSED" in notices(ws)[0] and rig.state.remote_stop.latched
        rig.backend.release(pins.REMOTE_STOP)
        await rig.run(0.1)
        await msg({"type": "remote_stop_clear"})
        assert not rig.state.remote_stop.latched

    async def test_start_and_confirm_refused_while_latched(self, rig):
        http_module._current_job = running_job()
        await self.press_stop(rig)
        assert "REMOTE STOP" in await http_module._start_job()
        assert "REMOTE STOP" in http_module._confirm_reposition(1)
        n = len(rig.transport.sent_lines)
        ws = await msg({"type": "console_send", "command": "G1 X5"})       # no job active: console refused too
        assert "REMOTE STOP" in notices(ws)[0] and len(rig.transport.sent_lines) == n

    async def test_awaiting_reposition_cannot_be_continued_by_lift_change(self, rig):
        http_module._current_job = job = running_job()
        job.kind = "building"
        job.state = "awaiting_reposition"
        await self.press_stop(rig)
        ws = await msg({"type": "adjust_lift_end", "lift": 0, "at": 10})
        assert "REMOTE STOP" in notices(ws)[0] and job.state == "awaiting_reposition"

    async def test_closed_at_startup_latches_no_motion(self, rig):
        # a fresh service whose CH1 is closed when the inputs are first read
        b, clk = MockBackend(), Clock()
        b.press(pins.REMOTE_STOP)
        b.press(pins.Z_DOWN)
        z = ZController(b, duty=0.5, timeout_s=0.3, max_run_s=30, interlock=rig.state.remote_stop.refusal,
                        clock=clk)
        svc = HardwareService(b, RemoteInputs(b, debounce_s=0.05, clock=clk), z, poll_interval_s=0.01,
                              on_remote_stop=http_module.remote_stop)
        for _ in range(40):
            await svc.step()
            clk.advance(0.01)
        assert rig.state.remote_stop.latched and "startup" in rig.state.remote_stop.cause
        assert b.duty == 0.0 and z.command == 0                   # Z DOWN held at startup: never acted on

    async def test_ui_and_logs_never_say_estop(self, rig):
        await self.press_stop(rig)
        texts = " ".join(e["text"] for e in rig.state.hw_events).lower()
        assert "remote stop" in texts and "e-stop" not in texts and "emergency" not in texts
        html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text()
        panel = html.split('id="rstop-panel"')[1].split("</div>\n")[0]
        assert "Remote stop latched" in panel


# ------------------------------------------------------------------ #
# Lifecycle, no-GPIO development, systemd notify                       #
# ------------------------------------------------------------------ #

class TestLifecycle:
    async def test_build_without_gpio_works(self):
        cfg = SimpleNamespace(HARDWARE_BACKEND="none", Z_PWM_FREQUENCY_HZ=1000, Z_PWM_DUTY=0.5, Z_TIMEOUT_S=0.3,
                              Z_MAX_RUN_S=30, Z_DIR_INVERT=False, REMOTE_DEBOUNCE_S=0.05,
                              REMOTE_POLL_INTERVAL_S=0.01)
        svc = hardware.build(cfg, interlock=lambda: None, on_remote_stop=None, event=lambda k, t: None)
        svc.start()
        await asyncio.sleep(0.05)
        d = svc.to_dict()
        assert d["backend"] == "none" and not d["gpio"] and d["z"]["available"] is False
        assert "No Z hardware" in svc.z.request("ui:1", UP)
        await svc.stop()

    async def test_failed_gpio_backend_falls_back_to_no_motion(self, monkeypatch):
        cfg = SimpleNamespace(HARDWARE_BACKEND="gpio", Z_PWM_FREQUENCY_HZ=1000, Z_PWM_DUTY=0.5, Z_TIMEOUT_S=0.3,
                              Z_MAX_RUN_S=30, Z_DIR_INVERT=False, REMOTE_DEBOUNCE_S=0.05,
                              REMOTE_POLL_INTERVAL_S=0.01)

        def boom(*a, **k):
            raise RuntimeError("no /dev/gpiochip0")
        monkeypatch.setattr(hardware, "create_backend", boom)
        events = []
        svc = hardware.build(cfg, interlock=lambda: None, on_remote_stop=None, event=lambda k, t: events.append(t))
        assert svc.backend.name == "none" and "gpiochip0" in svc.backend_error and events

    async def test_service_stop_leaves_z_stopped_and_releases_pins(self):
        b, clk = MockBackend(), Clock()
        z = ZController(b, duty=0.5, timeout_s=0.3, max_run_s=30, clock=clk)
        svc = HardwareService(b, RemoteInputs(b, debounce_s=0.05, clock=clk), z, poll_interval_s=0.01,
                              on_remote_stop=None)
        svc.start()
        z.request("ui:1", UP)
        assert b.duty == 0.5
        await svc.stop()
        assert b.duty == 0.0 and b.closed

    async def test_poll_exception_stops_z(self):
        b, clk = MockBackend(), Clock()
        z = ZController(b, duty=0.5, timeout_s=10, max_run_s=30, clock=clk)
        svc = HardwareService(b, RemoteInputs(b, debounce_s=0.05, clock=clk), z, poll_interval_s=0.01,
                              on_remote_stop=None)
        z.request("ui:1", UP)

        def broken():
            raise OSError("read failed")
        b.read_levels = broken
        svc.start()
        await asyncio.sleep(0.05)
        assert b.duty == 0.0 and "read failed" in svc.loop_error
        await svc.stop()

    def test_app_state_reports_hardware_without_service(self):
        d = AppState().to_dict()
        assert d["hardware"]["backend"] == "none" and d["remote_stop"]["latched"] is False

    def test_sdnotify(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        assert sdnotify.notify("READY=1") is False and sdnotify.watchdog_interval_s() is None
        path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "n.sock")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(path)
        try:
            monkeypatch.setenv("NOTIFY_SOCKET", path)
            monkeypatch.setenv("WATCHDOG_USEC", "4000000")
            monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
            assert sdnotify.notify("READY=1") and sock.recv(64) == b"READY=1"
            assert sdnotify.watchdog_interval_s() == 2.0
        finally:
            sock.close()
            os.unlink(path)


class TestGrblIdentity:
    async def test_build_info_and_link_events(self, rig):
        rig.transport.replies["$I"] = ["[VER:1.1h.20190825:]", "[OPT:V,15,128]"]
        assert await http_module._read_grbl("info") is None
        assert rig.state.build_info == {"VER": "1.1h.20190825:", "OPT": "V,15,128"}
        assert any(e["text"] == "GRBL serial link connected" for e in rig.state.hw_events)


def test_systemd_unit_forces_the_z_pins_low_on_every_exit():
    unit = (Path(__file__).resolve().parents[1] / "deploy" / "mudprinter.service").read_text()
    for pin in (pins.Z_PWM_BCM, pins.Z_DIR_BCM):
        assert f"ExecStartPre=+/usr/bin/pinctrl set {pin} op dl" in unit
        assert f"ExecStopPost=+/usr/bin/pinctrl set {pin} op dl" in unit
    assert "Type=notify" in unit and "WatchdogSec=" in unit
    assert "After=network.target" in unit and "Wants=network-online" not in unit     # offline-capable
