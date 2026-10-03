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
