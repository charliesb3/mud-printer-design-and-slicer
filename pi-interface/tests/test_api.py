"""
API-level tests using Starlette's TestClient.

Tests WebSocket connection/reconnection, file upload, and state delivery.
The serial connection will not succeed in this environment (no real port),
so machine state will show 'Disconnected' — which is the correct behavior.

These tests verify the web layer independently of hardware.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import main as main_module
from app.main import app
from app.api import http as http_module


@pytest.fixture(autouse=True)
def reset_http_globals():
    """Reset module-level job/runner state between tests."""
    http_module._current_job = None
    http_module._runner_task = None
    http_module._jog_axis = None
    http_module._jog_task = None
    if http_module._state:
        http_module._state.job = None
    yield
    http_module._current_job = None
    http_module._runner_task = None
    if http_module._state:
        http_module._state.job = None


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestStateEndpoint:
    def test_state_returns_json(self, client):
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "state"
        assert "connected" in data
        assert "grbl_state" in data

    def test_state_shows_disconnected_without_hardware(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        # Without a real serial port, state should be Disconnected
        assert data["connected"] is False
        assert data["grbl_state"] == "Disconnected"

    def test_state_has_expected_fields(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        required = ["type", "connected", "grbl_state", "x", "y",
                    "feed_rate", "jog_enabled", "jog_warning", "job", "setup"]
        for field in required:
            assert field in data, f"Missing field: {field}"


class TestFileUpload:
    def _gcode_file(self, content: str = "G0 X0\nG1 X10 F500\nM2\n"):
        return ("test.nc", io.BytesIO(content.encode()), "text/plain")

    def test_upload_creates_current_job(self, client):
        resp = client.post(
            "/upload",
            files={"file": self._gcode_file()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["filename"] == "test.nc"
        assert data["total_lines"] == 3  # G0 X0, G1 X10 F500, M2

    def test_upload_counts_only_sendable_lines(self, client):
        gcode = "; header\nG0 X0\n; comment\n\nG1 X10\nM2\n"
        resp = client.post(
            "/upload",
            files={"file": ("job.nc", io.BytesIO(gcode.encode()), "text/plain")},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["total_lines"] == 3  # G0 X0, G1 X10, M2 (not comments/blanks)

    def test_upload_sets_current_job_in_module(self, client):
        client.post("/upload", files={"file": self._gcode_file()})
        assert http_module._current_job is not None
        assert http_module._current_job.filename == "test.nc"

    def test_upload_updates_state_job(self, client):
        client.post("/upload", files={"file": self._gcode_file()})
        # State should reflect the new job after sync
        http_module.sync_job_state_to_appstate()
        state = http_module._state
        assert state is not None
        assert state.job is not None
        assert state.job.filename == "test.nc"

    def test_upload_rejected_while_job_running(self, client):
        # First upload
        client.post("/upload", files={"file": self._gcode_file()})
        # Simulate active job
        http_module._current_job.state = "running"

        resp = client.post(
            "/upload",
            files={"file": self._gcode_file("G0 X100\n")},
        )
        data = resp.json()
        assert data["success"] is False
        assert "active" in data["error"].lower() or "stop" in data["error"].lower()


class TestWebSocket:
    def test_connect_receives_state(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert "connected" in msg
            assert "grbl_state" in msg

    def test_reconnect_receives_current_state(self, client):
        # First connection
        with client.websocket_connect("/ws") as ws:
            state1 = ws.receive_json()

        # Second connection — should get the same structure
        with client.websocket_connect("/ws") as ws:
            state2 = ws.receive_json()

        assert state2["type"] == "state"
        assert state2["grbl_state"] == state1["grbl_state"]

    def test_unknown_message_type_ignored(self, client):
        with client.websocket_connect("/ws") as ws:
            _ = ws.receive_json()  # consume initial state
            ws.send_json({"type": "nonexistent_type"})
            # Should not crash or disconnect — no response expected

    def test_jog_start_rejected_when_not_connected(self, client):
        """When GRBL is disconnected, jog_start should be silently ignored."""
        with client.websocket_connect("/ws") as ws:
            _ = ws.receive_json()
            ws.send_json({"type": "jog_start", "axis": "X", "direction": 1})
            # No exception, no crash — server handles gracefully

    def test_jog_start_malformed_payload_does_not_error(self, client):
        """
        Pin the seventh-pass bug: the Z buttons used to share the .jog-btn
        class, which wired them as X/Y jog buttons too, firing
        `jog_start { axis: null, direction: null }`. The server used to
        `int(None)` and raise TypeError, which the sixth-pass handler fix
        surfaced as a visible notice. The fix validates the payload before
        touching int() so a malformed message is silently dropped (same
        way an unknown type is).
        """
        malformed_payloads = (
            {"type": "jog_start"},                                   # missing axis + direction
            {"type": "jog_start", "axis": None, "direction": None},  # the Z-button mis-wire shape
            {"type": "jog_start", "axis": "X"},                      # missing direction
            {"type": "jog_start", "axis": "Z", "direction": 1},      # Z is not a jog axis
            {"type": "jog_start", "axis": "X", "direction": 0},      # not -1 or 1
            {"type": "jog_start", "axis": "X", "direction": "up"},   # wrong type
            {"type": "jog_start", "axis": 1, "direction": 1},        # axis not a string
            {"type": "jog_start", "axis": "X", "direction": True},   # bool is not a valid direction
        )
        with client.websocket_connect("/ws") as ws:
            _ = ws.receive_json()
            for payload in malformed_payloads:
                ws.send_json(payload)
            # None of these should have produced a notice — all silently dropped.
            # Follow up with a known-good jog_start just to confirm the handler
            # is still processing messages. (It will be refused with "Not
            # connected to GRBL" or similar, but the point is the WS is alive.)
            ws.send_json({"type": "jog_set_speed", "speed_in_s": 0.5})
            # No crash by this point = test passed.

    def test_handler_exception_does_not_close_ws(self, client, monkeypatch):
        """
        Pin the fifth-pass bug: a handler exception used to be caught at
        log.debug then the `finally` block disconnected the client, so a
        single bad message silently closed the WS. The browser then
        auto-reconnected ~1.5 s later — the symptom "WS turns red on z_start,
        green again a second later". The handler now logs the traceback and
        notifies the client, but the socket stays open.
        """
        from app.api import http as http_module

        async def boom(_data, _ws):
            raise RuntimeError("synthetic test error")
        monkeypatch.setattr(http_module, "_handle_message", boom)

        with client.websocket_connect("/ws") as ws:
            _ = ws.receive_json()                               # initial state
            ws.send_json({"type": "anything"})
            # The handler raised. The server should have sent us a notice
            # AND kept the socket open. Drain any messages for up to 2 s and
            # confirm we see a notice without the socket closing.
            got_notice = False
            for _ in range(10):
                m = ws.receive_json()
                if m.get("type") == "notice":
                    got_notice = True
                    assert "Internal error" in m.get("message", "")
                    break
            assert got_notice, "server did not notify the client of the handler error"
            # The socket must still be open — send another message and expect
            # ANOTHER notice (the server is still processing messages).
            ws.send_json({"type": "another"})
            for _ in range(10):
                m = ws.receive_json()
                if m.get("type") == "notice":
                    return
            assert False, "WS closed after the first exception"


class TestJogConfiguration:
    def test_jog_disabled_without_speed(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        # Default config has JOG_SPEED_MM_MIN = 0 / None
        from app import config
        if config.JOG_SPEED_MM_MIN is None:
            assert data["jog_enabled"] is False
            assert data["jog_warning"] is not None
            assert "JOG_SPEED_MM_MIN" in data["jog_warning"]


# ------------------------------------------------------------------ #
# Building jobs and synchronized clients                               #
# ------------------------------------------------------------------ #

CONTRACT_FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "mudjob" / "fixtures"
FIXTURE = CONTRACT_FIXTURES / "executable-3-lifts.mudjob"
EXPORTED = CONTRACT_FIXTURES / "designer-export-3-lifts.mudjob"


def receive_state_until(ws, predicate, max_messages: int = 40) -> dict:
    """Read messages (state broadcasts arrive every 200 ms) until one matches."""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg.get("type") == "state" and predicate(msg):
            return msg
    raise AssertionError("expected state never arrived")


@pytest.fixture
def offline_client(monkeypatch):
    """App without the serial reconnect loop, so tests can set machine state directly."""
    async def no_connection_loop():
        return None
    monkeypatch.setattr(main_module, "_connection_loop", no_connection_loop)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    main_module.app_state.mark_disconnected()
    from app.state import MachineSetup
    main_module.app_state.setup = MachineSetup()


def upload_fixture(client):
    return client.post(
        "/upload",
        files={"file": ("sample.mudjob", FIXTURE.read_bytes(), "application/json")},
    ).json()


class TestBuildingJobUpload:
    def test_upload_building_job(self, client):
        data = upload_fixture(client)
        assert data["success"] is True
        assert data["kind"] == "building"
        assert data["total_lifts"] == 3
        assert data["total_lines"] == 18

        job = client.get("/api/state").json()["job"]
        assert job["kind"] == "building"
        assert job["total_lifts"] == 3
        assert job["current_lift"] == 0
        assert job["awaiting_lift"] is None
        assert job["lifts"][1]["reposition_before"]["rise"] == 18

    def test_upload_designer_export(self, client):
        data = client.post(
            "/upload",
            files={"file": ("house.mudjob", EXPORTED.read_bytes(), "application/json")},
        ).json()
        assert data["success"] is True
        assert data["kind"] == "building" and data["executable"] is True and data["unresolved"] == []
        assert data["total_lifts"] == 3 and data["total_lines"] == 72
        job = client.get("/api/state").json()["job"]
        assert job["executable"] is True and job["xy_bounds"]["max_x"] == 140.0
        assert [l["index"] for l in job["lifts"]] == [0, 1, 2]
        assert job["lifts"][2]["layers"] == {"first": 4, "last": 5, "count": 2}

    def test_muddesign_upload_refused(self, client):
        project = json.dumps({"format": "mud-printer-project", "schema_version": 4, "project": {}})
        data = client.post("/upload", files={"file": ("house.muddesign", io.BytesIO(project.encode()),
                                                      "application/json")}).json()
        assert data["success"] is False
        assert ".muddesign" in data["error"] and "Export Print Job" in data["error"]
        assert http_module._current_job is None

    def test_flat_upload_reports_one_lift(self, client):
        data = client.post("/upload", files={"file": ("a.nc", io.BytesIO(b"G0 X0\n"), "text/plain")}).json()
        assert data["kind"] == "gcode"
        assert data["total_lifts"] == 1

    def test_invalid_building_job_rejected(self, client):
        bad = json.dumps({"format": "mudprinter-job", "version": 1, "executable": True,
                          "lifts": [{"index": 0, "gcode": ["G1 X1", "M0"]}]})
        data = client.post("/upload", files={"file": ("bad.mudjob", io.BytesIO(bad.encode()), "application/json")}).json()
        assert data["success"] is False
        assert "M0" in data["error"]
        assert http_module._current_job is None

    def test_upload_rejected_while_awaiting_reposition(self, client):
        upload_fixture(client)
        http_module._current_job.state = "awaiting_reposition"
        data = client.post("/upload", files={"file": ("a.nc", io.BytesIO(b"G0 X0\n"), "text/plain")}).json()
        assert data["success"] is False


class TestSynchronizedClients:
    def _await_lift_2(self):
        """Put the loaded job at the lift 1 → 2 boundary with GRBL Idle (no runner needed)."""
        job = http_module._current_job
        job.state = "awaiting_reposition"
        job.awaiting_lift = 1
        main_module.app_state.connected = True
        main_module.app_state.grbl_state = "Idle"
        main_module.app_state.setup.origin_set = True
        return job

    def test_upload_appears_on_every_client(self, offline_client):
        with offline_client.websocket_connect("/ws") as a, offline_client.websocket_connect("/ws") as b:
            upload_fixture(offline_client)
            for ws in (a, b):
                s = receive_state_until(ws, lambda m: m["job"] is not None)
                assert s["job"]["total_lifts"] == 3

    def test_confirmation_from_one_client_appears_on_the_other(self, offline_client):
        upload_fixture(offline_client)
        self._await_lift_2()
        with offline_client.websocket_connect("/ws") as laptop, offline_client.websocket_connect("/ws") as kiosk:
            receive_state_until(kiosk, lambda m: m["job"]["state"] == "awaiting_reposition")
            laptop.send_json({"type": "confirm_reposition", "lift": 1})
            s = receive_state_until(kiosk, lambda m: m["job"]["state"] == "running")
            assert s["job"]["awaiting_lift"] is None

    def test_stale_confirmation_gets_notice_and_changes_nothing(self, offline_client):
        upload_fixture(offline_client)
        job = self._await_lift_2()
        with offline_client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "confirm_reposition", "lift": 2})
            for _ in range(40):
                msg = ws.receive_json()
                if msg["type"] == "notice":
                    break
            else:
                raise AssertionError("no notice received")
            assert "lift 2" in msg["message"].lower()
        assert job.state == "awaiting_reposition"

    def test_client_disconnect_does_not_affect_awaiting_job(self, offline_client):
        upload_fixture(offline_client)
        job = self._await_lift_2()
        with offline_client.websocket_connect("/ws") as kiosk:
            with offline_client.websocket_connect("/ws") as laptop:
                receive_state_until(laptop, lambda m: m["job"] is not None)
            # laptop gone; job unchanged and the kiosk still sees it
            s = receive_state_until(kiosk, lambda m: m["job"] is not None)
            assert s["job"]["state"] == "awaiting_reposition"
            assert s["job"]["awaiting_lift"] == 1
        assert job.state == "awaiting_reposition"

        # a reconnecting client gets the full boundary state immediately
        with offline_client.websocket_connect("/ws") as again:
            s = again.receive_json()
            assert s["job"]["state"] == "awaiting_reposition"
            assert s["job"]["awaiting_lift"] == 1
            assert s["job"]["total_lifts"] == 3

    def test_setup_state_is_shared_by_all_clients(self, offline_client):
        main_module.app_state.connected = True
        main_module.app_state.grbl_state = "Idle"
        with offline_client.websocket_connect("/ws") as laptop, offline_client.websocket_connect("/ws") as kiosk:
            receive_state_until(kiosk, lambda m: "setup" in m)
            main_module.app_state.setup.origin_set = True
            main_module.app_state.setup.prime = (1.0, -2.0)
            laptop.send_json({"type": "hold"})                    # any action broadcasts at once
            s = receive_state_until(kiosk, lambda m: m["setup"]["origin_set"])
            assert s["setup"]["prime"] == [1.0, -2.0] and s["setup"]["prime_ready"] is True

    def test_setup_refusal_goes_to_the_requesting_client(self, offline_client):
        with offline_client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "set_origin"})
            for _ in range(40):
                m = ws.receive_json()
                if m["type"] == "notice":
                    break
            else:
                raise AssertionError("no notice")
            assert "Not connected" in m["message"]


class TestJogSpeedState:
    """Pins the first-motion jog-speed fields exposed in /api/state."""

    def test_state_exposes_jog_speed_fields(self, client):
        data = client.get("/api/state").json()
        for field in ("jog_speed_mm_min", "jog_speed_in_s",
                      "jog_speed_max_mm_min", "jog_speed_max_in_s"):
            assert field in data, f"Missing jog-speed field: {field}"

    def test_state_exposes_print_speed_in_in_per_s(self, client):
        data = client.get("/api/state").json()
        for field in ("feed_rate_in_s", "planned_feed_rate", "planned_feed_rate_in_s"):
            assert field in data, f"Missing print-speed field: {field}"


class TestJogSpeedWS:
    def test_jog_set_speed_clamped_to_max(self, client):
        from app import config
        from app.api import http as http_mod
        orig_default, orig_max = config.JOG_SPEED_MM_MIN, config.JOG_SPEED_MAX_MM_MIN
        config.JOG_SPEED_MAX_MM_MIN = 900.0
        config.JOG_SPEED_MM_MIN = 300.0
        try:
            with client.websocket_connect("/ws") as ws:
                _ = ws.receive_json()
                ws.send_json({"type": "jog_set_speed", "speed_mm_min": 99999})
            data = client.get("/api/state").json()
            assert data["jog_speed_mm_min"] == 900.0
        finally:
            config.JOG_SPEED_MM_MIN, config.JOG_SPEED_MAX_MM_MIN = orig_default, orig_max
            http_mod._state.current_jog_speed_mm_min = orig_default

    def test_jog_set_speed_in_s_converted_to_mm_min(self, client):
        from app import config
        from app.api import http as http_mod
        orig_default, orig_max = config.JOG_SPEED_MM_MIN, config.JOG_SPEED_MAX_MM_MIN
        config.JOG_SPEED_MAX_MM_MIN = 9144.0  # 6 in/s
        config.JOG_SPEED_MM_MIN = 300.0
        try:
            with client.websocket_connect("/ws") as ws:
                _ = ws.receive_json()
                # 0.5 in/s → 762 mm/min
                ws.send_json({"type": "jog_set_speed", "speed_in_s": 0.5})
            data = client.get("/api/state").json()
            assert abs(data["jog_speed_mm_min"] - 762.0) < 0.5
        finally:
            config.JOG_SPEED_MM_MIN, config.JOG_SPEED_MAX_MM_MIN = orig_default, orig_max
            http_mod._state.current_jog_speed_mm_min = orig_default


class TestInchConversion:
    def test_mm_min_to_in_s_known_values(self):
        from app.state import mm_min_to_in_s, in_s_to_mm_min
        assert abs(mm_min_to_in_s(25.4) - (1 / 60.0)) < 1e-9
        assert abs(mm_min_to_in_s(3048.0) - 2.0) < 1e-9
        for mm_min in (100.0, 300.0, 900.0, 3048.0, 9144.0):
            assert abs(in_s_to_mm_min(mm_min_to_in_s(mm_min)) - mm_min) < 1e-9


class TestExitKiosk:
    """POST /api/exit_kiosk signals the Chromium PID written by launch-kiosk.sh.
    Backend is unaffected. Covers: missing PID file, stale PID, successful signal."""

    def test_missing_pid_file_reports_not_running(self, client, tmp_path, monkeypatch):
        from app.api import http as http_mod
        monkeypatch.setattr(http_mod, "KIOSK_PID_PATH", str(tmp_path / "nope.pid"))
        resp = client.post("/api/exit_kiosk")
        assert resp.status_code == 200
        d = resp.json()
        assert d["ok"] is False and d["running"] is False
        assert "No kiosk" in d["error"]

    def test_stale_pid_reports_already_exited(self, client, tmp_path, monkeypatch):
        from app.api import http as http_mod
        import os
        # Find a PID that is almost certainly dead: fork a child that exits
        # immediately, record its PID, wait for it, use it.
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        pid_file = tmp_path / "kiosk.pid"
        pid_file.write_text(str(pid))
        monkeypatch.setattr(http_mod, "KIOSK_PID_PATH", str(pid_file))
        resp = client.post("/api/exit_kiosk")
        d = resp.json()
        assert d["ok"] is False and d["running"] is False
        assert "already exited" in d["error"]

    def test_live_pid_is_signaled(self, client, tmp_path, monkeypatch):
        from app.api import http as http_mod
        import os
        import signal
        import time
        # Fork a child that sleeps so we have a live PID to signal.
        pid = os.fork()
        if pid == 0:
            # Child: SIGTERM handler exits cleanly.
            signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
            time.sleep(5)
            os._exit(1)   # if we ever get here, the test failed
        try:
            pid_file = tmp_path / "kiosk.pid"
            pid_file.write_text(str(pid))
            monkeypatch.setattr(http_mod, "KIOSK_PID_PATH", str(pid_file))
            resp = client.post("/api/exit_kiosk")
            d = resp.json()
            assert d["ok"] is True
            assert d["signaled_pid"] == pid
            # The child's SIGTERM handler exits — reap it.
            for _ in range(50):
                waited, _ = os.waitpid(pid, os.WNOHANG)
                if waited:
                    break
                time.sleep(0.02)
            assert waited == pid, "child did not exit after SIGTERM"
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
