"""
Tests for the deploy preflight decision (scripts/preflight_state.py).

The preflight parser decides whether the Pi controller is safe to restart.
These tests pin the exact pass/block behaviour.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


# scripts/preflight_state.py is not a package, so load it by path.
_PRE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "preflight_state.py"
_spec = importlib.util.spec_from_file_location("preflight_state", _PRE_PATH)
assert _spec is not None and _spec.loader is not None
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


# ------------------------------------------------------------------- evaluate

class TestEvaluate:
    def test_idle_no_job_is_safe(self):
        assert preflight.evaluate({"grbl_state": "Idle", "job": None}) == []

    def test_idle_ready_job_is_safe(self):
        assert preflight.evaluate(
            {"grbl_state": "Idle", "job": {"state": "ready"}}
        ) == []

    def test_disconnected_is_safe(self):
        # Pi app alive, Arduino unplugged — safe time to restart the Pi service.
        assert preflight.evaluate({"grbl_state": "Disconnected", "job": None}) == []

    def test_alarm_is_safe(self):
        # Alarm means no motion. Operator may want to restart to recover.
        assert preflight.evaluate({"grbl_state": "Alarm:1", "job": None}) == []

    def test_completed_or_stopped_job_is_safe(self):
        assert preflight.evaluate(
            {"grbl_state": "Idle", "job": {"state": "completed"}}
        ) == []
        assert preflight.evaluate(
            {"grbl_state": "Idle", "job": {"state": "stopped"}}
        ) == []

    def test_run_blocks(self):
        reasons = preflight.evaluate(
            {"grbl_state": "Run", "job": {"state": "running"}}
        )
        assert reasons
        assert any("Run" in r for r in reasons)

    def test_jog_blocks(self):
        reasons = preflight.evaluate({"grbl_state": "Jog", "job": None})
        assert reasons
        assert any("Jog" in r for r in reasons)

    def test_hold_blocks(self):
        reasons = preflight.evaluate(
            {"grbl_state": "Hold", "job": {"state": "held"}}
        )
        assert reasons
        assert any("Hold" in r for r in reasons)

    def test_hold_with_substate_blocks(self):
        reasons = preflight.evaluate(
            {"grbl_state": "Hold:0", "job": {"state": "held"}}
        )
        assert reasons
        assert any("Hold" in r for r in reasons)

    def test_held_job_blocks_even_if_grbl_idle(self):
        # Defensive — a stale 'held' job state shouldn't be ignored just because
        # GRBL has returned to Idle (e.g. after a cycle out of Hold).
        reasons = preflight.evaluate(
            {"grbl_state": "Idle", "job": {"state": "held"}}
        )
        assert reasons
        assert any("held" in r for r in reasons)

    def test_missing_grbl_state_blocks(self):
        reasons = preflight.evaluate({"job": None})
        assert reasons
        assert any("grbl_state" in r for r in reasons)

    def test_empty_state_blocks(self):
        reasons = preflight.evaluate({})
        assert reasons

    def test_running_job_blocks_even_without_grbl_run(self):
        # If job is "running" we should block regardless of what grbl_state says.
        reasons = preflight.evaluate(
            {"grbl_state": "Idle", "job": {"state": "running"}}
        )
        assert reasons
        assert any("running" in r for r in reasons)


# ---------------------------------------------------------------------- main

class TestMainExitCodes:
    def _run(self, stdin_text: str) -> int:
        """Invoke preflight.main() with the given stdin content; return exit code."""
        orig_stdin = sys.stdin
        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        try:
            sys.stdin = io.StringIO(stdin_text)
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            rc = preflight.main([])
        finally:
            sys.stdin = orig_stdin
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
        return rc

    def test_safe_state_returns_zero(self):
        rc = self._run(json.dumps({"grbl_state": "Idle", "job": None}))
        assert rc == 0

    def test_unsafe_state_returns_two(self):
        rc = self._run(json.dumps({"grbl_state": "Run", "job": None}))
        assert rc == 2

    def test_malformed_json_returns_two(self):
        rc = self._run("not-json-at-all")
        assert rc == 2

    def test_empty_input_returns_two(self):
        rc = self._run("")
        assert rc == 2

    def test_non_object_input_returns_two(self):
        rc = self._run("[1, 2, 3]")
        assert rc == 2
