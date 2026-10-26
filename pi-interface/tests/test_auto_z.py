"""
Sixteenth-pass automatic Z between .mudjob layers.

These tests cover:
  * absolute layer reference heights (`layer_target_in`),
  * planning the Z move at a layer transition (`plan_z_move`),
  * folding manual moves into the estimate (`apply_manual_move_to_estimate`),
  * mode preconditions (`can_enable_auto`),
  * calibration persistence,
  * mode persistence,
  * state exposure through AppState.to_dict().

The operator rule is: EVERY layer has an absolute reference height. Manual
Z adjustments shift the ESTIMATE only, never the layer targets.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import z_calibration, z_mode
from app.jobs import auto_z
from app.state import AppState


class TestLayerTarget:
    def test_absolute_targets(self):
        # 1.5-inch layers: layer_0 = 0.0, layer_1 = 1.5, layer_2 = 3.0, ...
        assert auto_z.layer_target_in(0, 1.5) == 0.0
        assert auto_z.layer_target_in(1, 1.5) == 1.5
        assert auto_z.layer_target_in(2, 1.5) == 3.0
        assert auto_z.layer_target_in(3, 1.5) == 4.5

    def test_manual_adjustments_do_not_shift_targets(self):
        """The next layer's target does NOT depend on the estimate; it
        is purely a function of the layer index and the thickness. A
        manual nudge in-between layers can't change where the next
        layer boundary should be."""
        th = 1.5
        assert auto_z.layer_target_in(1, th) == 1.5
        # No "estimate" parameter — targets are intrinsic.
        assert auto_z.layer_target_in(2, th) == 3.0


FULL_CAL = {"up_in_per_s": 0.5, "down_in_per_s": 0.6, "pwm_duty": 0.5,
            "calibrated_at": 1700000000.0}


class TestPlanZMove:
    def test_plans_up_move_at_boundary(self):
        plan = auto_z.plan_z_move(0.0, 1.5, FULL_CAL, 0.5)
        assert plan.valid and plan.direction == "up"
        assert abs(plan.delta_in - 1.5) < 1e-6
        assert abs(plan.duration_s - 3.0) < 1e-6                # 1.5 / 0.5
        assert not plan.requires_operator

    def test_refuses_negative_delta_requires_operator(self):
        """CORE RULE: if manual adjustments left Z above the next
        target, DO NOT automatically lower it. Pause for operator."""
        plan = auto_z.plan_z_move(2.0, 1.5, FULL_CAL, 0.5)
        assert not plan.valid
        assert plan.direction is None
        assert plan.requires_operator
        assert "above" in plan.reason.lower()

    def test_at_target_within_tolerance(self):
        plan = auto_z.plan_z_move(1.5, 1.5, FULL_CAL, 0.5)
        assert plan.valid and plan.direction is None and plan.duration_s == 0.0

    def test_refuses_missing_calibration(self):
        plan = auto_z.plan_z_move(0.0, 1.5, {"version": 1}, 0.5)
        assert not plan.valid and plan.requires_operator
        assert "calibration" in plan.reason.lower()

    def test_refuses_duty_mismatch(self):
        plan = auto_z.plan_z_move(0.0, 1.5, FULL_CAL, 0.75)
        assert not plan.valid and plan.requires_operator
        assert "duty" in plan.reason.lower()

    def test_refuses_missing_estimate(self):
        plan = auto_z.plan_z_move(None, 1.5, FULL_CAL, 0.5)
        assert not plan.valid and plan.requires_operator
        assert "unknown" in plan.reason.lower() or "origin" in plan.reason.lower()


class TestApplyManualMoveToEstimate:
    def test_up_add(self):
        new = auto_z.apply_manual_move_to_estimate(1.0, "up", 2.0, FULL_CAL)
        assert abs(new - (1.0 + 2.0 * 0.5)) < 1e-6             # 2.0 s × 0.5 in/s

    def test_down_subtract(self):
        new = auto_z.apply_manual_move_to_estimate(1.0, "down", 2.0, FULL_CAL)
        assert abs(new - (1.0 - 2.0 * 0.6)) < 1e-6             # 2.0 s × 0.6 in/s

    def test_missing_estimate_returns_none(self):
        assert auto_z.apply_manual_move_to_estimate(None, "up", 1.0, FULL_CAL) is None

    def test_missing_speed_leaves_estimate_unchanged(self):
        assert auto_z.apply_manual_move_to_estimate(1.0, "up", 2.0, {}) == 1.0


class TestCanEnableAuto:
    def test_manual_always_allowed(self):
        assert auto_z.can_enable_auto("manual", False, {}, None, None) is None

    def test_auto_blocked_without_origin(self):
        r = auto_z.can_enable_auto("auto", False, FULL_CAL, 0.5, 0.0)
        assert r and "origin" in r.lower()

    def test_auto_blocked_without_calibration(self):
        r = auto_z.can_enable_auto("auto", True, {"version": 1}, 0.5, 0.0)
        assert r and "calibrat" in r.lower()

    def test_auto_blocked_on_duty_mismatch(self):
        r = auto_z.can_enable_auto("auto", True, FULL_CAL, 0.75, 0.0)
        assert r and "duty" in r.lower()

    def test_auto_blocked_without_estimate(self):
        r = auto_z.can_enable_auto("auto", True, FULL_CAL, 0.5, None)
        assert r

    def test_auto_ok_when_everything_set(self):
        assert auto_z.can_enable_auto("auto", True, FULL_CAL, 0.5, 0.0) is None


class TestCalibrationPersistence:
    def test_round_trip(self, tmp_path):
        p = tmp_path / "cal.json"
        z_calibration.save(p, {"up_in_per_s": 0.5, "down_in_per_s": 0.6, "pwm_duty": 0.5})
        loaded = z_calibration.load(p)
        assert loaded["up_in_per_s"] == 0.5 and loaded["down_in_per_s"] == 0.6
        assert loaded["pwm_duty"] == 0.5
        assert loaded["calibrated_at"] is not None             # save stamps it

    def test_defaults_when_missing(self, tmp_path):
        assert z_calibration.load(tmp_path / "nope.json") == z_calibration.default_calibration()

    def test_rejects_insane_values(self, tmp_path):
        p = tmp_path / "cal.json"
        z_calibration.save(p, {"up_in_per_s": 999, "down_in_per_s": -1, "pwm_duty": 2})
        loaded = z_calibration.load(p)
        assert loaded["up_in_per_s"] is None       # 999 in/s is nonsense
        assert loaded["down_in_per_s"] is None     # negative speed is nonsense
        assert loaded["pwm_duty"] is None          # duty > 1 is nonsense


class TestModePersistence:
    def test_round_trip(self, tmp_path):
        p = tmp_path / "mode.json"
        z_mode.save(p, "auto_confirm")
        assert z_mode.load(p) == "auto_confirm"

    def test_default_when_missing(self, tmp_path):
        assert z_mode.load(tmp_path / "nope.json") == "manual"

    def test_unknown_mode_falls_back(self, tmp_path):
        p = tmp_path / "mode.json"
        p.write_text('{"mode": "weird"}')
        assert z_mode.load(p) == "manual"


class TestAppStateExposure:
    def test_initial_state_is_manual_no_origin(self):
        s = AppState()
        d = s.to_dict()["auto_z"]
        assert d["mode"] == "manual"
        assert d["origin_set"] is False
        assert d["estimate_in"] is None
        assert d["calibration"]["up_in_per_s"] is None
        assert d["fault"]["latched"] is False

    def test_origin_set_clears_estimate_to_zero(self):
        """When the operator declares Z origin, estimate = 0.0 by rule.
        The next Z move folds elapsed × speed into the estimate."""
        s = AppState()
        s.z_origin_set = True
        s.z_estimate_in = 0.0
        d = s.to_dict()["auto_z"]
        assert d["origin_set"] is True
        assert d["estimate_in"] == 0.0


class TestRestartBehavior:
    def test_origin_is_not_restored_across_restart(self, tmp_path):
        """
        Operator requirement: 'Preserve calibration across restarts.
        Integrate the current Z reference after power loss or restart.'

        Our interpretation: calibration IS persisted (per
        TestCalibrationPersistence) but the Z origin / estimate is NOT —
        a drifting estimate must not survive a reboot. The operator
        re-establishes the origin after every start. Covered in main.py
        startup; this test pins the contract on AppState.
        """
        s = AppState()
        s.z_origin_set = True
        s.z_estimate_in = 7.5
        # Simulate restart: new AppState.
        s2 = AppState()
        d = s2.to_dict()["auto_z"]
        assert d["origin_set"] is False
        assert d["estimate_in"] is None
