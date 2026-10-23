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
import pytest
from starlette.testclient import TestClient

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
                    "feed_rate", "jog_enabled", "jog_warning", "job"]
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

    def test_state_exposes_jog_speed_fields(self, client):
        """The state dict must include adjustable jog-speed fields in both units."""
        resp = client.get("/api/state")
        data = resp.json()
        for field in (
            "jog_speed_mm_min", "jog_speed_in_s",
            "jog_speed_max_mm_min", "jog_speed_max_in_s",
        ):
            assert field in data, f"Missing jog-speed field: {field}"

    def test_state_exposes_print_speed_fields(self, client):
        """The state must include planned/actual print speed and feed override."""
        resp = client.get("/api/state")
        data = resp.json()
        for field in (
            "feed_rate", "feed_rate_in_s",
            "planned_feed_rate", "planned_feed_rate_in_s",
            "feed_override_pct",
        ):
            assert field in data, f"Missing print-speed field: {field}"
        assert data["feed_override_pct"] == 100

    def test_state_exposes_envelope_fields(self, client):
        resp = client.get("/api/state")
        data = resp.json()
        for field in (
            "envelope_x_mm", "envelope_y_mm",
            "envelope_x_in", "envelope_y_in",
            "envelope_verified", "envelope_warning",
        ):
            assert field in data, f"Missing envelope field: {field}"


class TestJogSpeedWSMessage:
    def test_jog_set_speed_clamped_to_max(self, client):
        """Over-limit speeds must be clamped on the server."""
        from app import config
        from app.api import http as http_mod
        state = http_mod._state
        assert state is not None

        # Simulate configured ceiling of 900 mm/min
        orig_max = config.JOG_SPEED_MAX_MM_MIN
        orig_min = config.JOG_SPEED_MM_MIN
        config.JOG_SPEED_MAX_MM_MIN = 900.0
        config.JOG_SPEED_MM_MIN = 300.0
        try:
            with client.websocket_connect("/ws") as ws:
                _ = ws.receive_json()
                # Try to set an impossibly large speed
                ws.send_json({"type": "jog_set_speed", "speed_mm_min": 99999})
                # Give the server a moment to process by requesting a fresh state
            resp = client.get("/api/state")
            data = resp.json()
            # Should be clamped to the configured max
            assert data["jog_speed_mm_min"] == 900.0
        finally:
            config.JOG_SPEED_MAX_MM_MIN = orig_max
            config.JOG_SPEED_MM_MIN = orig_min
            state.current_jog_speed_mm_min = orig_min

    def test_jog_set_speed_in_s_accepted(self, client):
        """speed_in_s payload should be converted to mm/min on the server."""
        from app import config
        from app.api import http as http_mod
        state = http_mod._state
        assert state is not None

        orig_max = config.JOG_SPEED_MAX_MM_MIN
        orig_min = config.JOG_SPEED_MM_MIN
        config.JOG_SPEED_MAX_MM_MIN = 9144.0  # 6 in/s
        config.JOG_SPEED_MM_MIN = 300.0
        try:
            with client.websocket_connect("/ws") as ws:
                _ = ws.receive_json()
                # 0.5 in/s → 762 mm/min
                ws.send_json({"type": "jog_set_speed", "speed_in_s": 0.5})
            resp = client.get("/api/state")
            data = resp.json()
            # Allow tiny float wobble
            assert abs(data["jog_speed_mm_min"] - 762.0) < 0.5
        finally:
            config.JOG_SPEED_MAX_MM_MIN = orig_max
            config.JOG_SPEED_MM_MIN = orig_min
            state.current_jog_speed_mm_min = orig_min

    def test_jog_set_speed_zero_disables(self, client):
        """Setting speed to 0 should disable jogging (slider all the way down)."""
        from app import config
        from app.api import http as http_mod
        state = http_mod._state
        assert state is not None

        orig_max = config.JOG_SPEED_MAX_MM_MIN
        orig_min = config.JOG_SPEED_MM_MIN
        config.JOG_SPEED_MAX_MM_MIN = 900.0
        config.JOG_SPEED_MM_MIN = 300.0
        try:
            with client.websocket_connect("/ws") as ws:
                _ = ws.receive_json()
                ws.send_json({"type": "jog_set_speed", "speed_mm_min": 0})
            assert state.current_jog_speed_mm_min is None \
                or state.current_jog_speed_mm_min == 0.0
        finally:
            config.JOG_SPEED_MAX_MM_MIN = orig_max
            config.JOG_SPEED_MM_MIN = orig_min
            state.current_jog_speed_mm_min = orig_min


class TestEnvelopeConfiguration:
    def test_envelope_warning_without_configuration(self, client):
        """When extents aren't set, the state must flag an unconfigured printer."""
        from app import config
        orig_x = config.PRINTER_ENVELOPE_X_MM
        orig_y = config.PRINTER_ENVELOPE_Y_MM
        config.PRINTER_ENVELOPE_X_MM = None
        config.PRINTER_ENVELOPE_Y_MM = None
        try:
            resp = client.get("/api/state")
            data = resp.json()
            assert data["envelope_x_in"] is None
            assert data["envelope_y_in"] is None
            assert data["envelope_warning"] is not None
            assert "nconfigured" in data["envelope_warning"] \
                or "extents" in data["envelope_warning"].lower()
        finally:
            config.PRINTER_ENVELOPE_X_MM = orig_x
            config.PRINTER_ENVELOPE_Y_MM = orig_y

    def test_envelope_warning_when_configured_but_unverified(self, client):
        from app import config
        orig_x = config.PRINTER_ENVELOPE_X_MM
        orig_y = config.PRINTER_ENVELOPE_Y_MM
        orig_v = config.PRINTER_EXTENTS_VERIFIED
        config.PRINTER_ENVELOPE_X_MM = 3048.0  # 120 in
        config.PRINTER_ENVELOPE_Y_MM = 3048.0
        config.PRINTER_EXTENTS_VERIFIED = False
        try:
            resp = client.get("/api/state")
            data = resp.json()
            assert data["envelope_x_in"] == 120.0
            assert data["envelope_y_in"] == 120.0
            assert data["envelope_verified"] is False
            assert data["envelope_warning"] is not None
            assert "verified" in data["envelope_warning"].lower() \
                or "nominal" in data["envelope_warning"].lower()
        finally:
            config.PRINTER_ENVELOPE_X_MM = orig_x
            config.PRINTER_ENVELOPE_Y_MM = orig_y
            config.PRINTER_EXTENTS_VERIFIED = orig_v

    def test_envelope_no_warning_when_verified(self, client):
        from app import config
        orig_x = config.PRINTER_ENVELOPE_X_MM
        orig_y = config.PRINTER_ENVELOPE_Y_MM
        orig_v = config.PRINTER_EXTENTS_VERIFIED
        config.PRINTER_ENVELOPE_X_MM = 3048.0
        config.PRINTER_ENVELOPE_Y_MM = 3048.0
        config.PRINTER_EXTENTS_VERIFIED = True
        try:
            resp = client.get("/api/state")
            data = resp.json()
            assert data["envelope_verified"] is True
            assert data["envelope_warning"] is None
        finally:
            config.PRINTER_ENVELOPE_X_MM = orig_x
            config.PRINTER_ENVELOPE_Y_MM = orig_y
            config.PRINTER_EXTENTS_VERIFIED = orig_v


class TestInchConversion:
    def test_mm_min_to_in_s_known_values(self):
        from app.state import mm_min_to_in_s, in_s_to_mm_min
        # 25.4 mm = 1 in, so 25.4 mm/min = 1 in/min = 1/60 in/s
        assert abs(mm_min_to_in_s(25.4) - (1 / 60.0)) < 1e-9
        # 3048 mm/min = 120 in/min = 2.0 in/s
        assert abs(mm_min_to_in_s(3048.0) - 2.0) < 1e-9

    def test_round_trip(self):
        from app.state import mm_min_to_in_s, in_s_to_mm_min
        for mm_min in (100.0, 300.0, 900.0, 3048.0, 9144.0):
            assert abs(in_s_to_mm_min(mm_min_to_in_s(mm_min)) - mm_min) < 1e-9
