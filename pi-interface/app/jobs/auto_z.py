"""
Automatic Z advancement — pure planning layer (sixteenth pass, 2026-10-25).

This module is PURE: no I/O, no side effects. All functions take explicit
inputs and return plans. The caller (api/http.py) is responsible for
actually driving the ZController and the job runner.

Core rule (operator-specified):
    Each layer has an ABSOLUTE reference height measured from the operator-
    established Z origin:   layer_k target = k * layer_thickness.

    At every layer transition:
      required_move = next_absolute_target − estimated_current_z

    Manual Z adjustments during a layer must NOT change the layer target
    heights, only the estimated current position. Negative required_move
    (Z above target) is NEVER auto-corrected downward — the job pauses and
    requires operator intervention.

Modes:
  * "manual"        — current behavior; no auto-Z. Default.
  * "auto_confirm"  — pause before each transition; operator confirms.
  * "auto"          — move automatically.

Auto-Z preconditions (checked by `can_enable_auto`):
  * Z origin established.
  * Calibration complete (up_in_per_s, down_in_per_s, pwm_duty).
  * ZController's current PWM duty matches the calibration's.
  * Z estimate is known.

See `pi-interface/PROJECT_MEMORY.md` for the operator's full requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import z_calibration


Z_MODES = ("manual", "auto_confirm", "auto")
_TOL_IN = 1e-4


@dataclass
class AutoZPlan:
    """Result of planning the Z move for a layer transition.

    * `valid`     — safe to execute (no operator action required).
    * `direction` — "up" | "down" | None (None iff `valid` and already there).
    * `delta_in`  — target − estimate (signed; sign may differ from direction
                    only when valid=False, e.g. negative delta the planner
                    refuses to execute).
    * `duration_s`— seconds to run the ZController in `direction`; 0.0 if
                    already there.
    * `requires_operator` — True if `valid` is False AND the condition is
                    something the operator can resolve (above target, missing
                    calibration, Z fault). The job must pause.
    * `reason`    — human-readable explanation.
    """
    valid: bool
    direction: Optional[str]
    delta_in: float
    duration_s: float
    requires_operator: bool
    reason: str

    def to_dict(self) -> dict:
        return {"valid": self.valid, "direction": self.direction,
                "delta_in": round(self.delta_in, 4),
                "duration_s": round(self.duration_s, 3),
                "requires_operator": self.requires_operator,
                "reason": self.reason}


def layer_target_in(layer_index: int, layer_thickness_in: float) -> float:
    """Absolute Z (inches above origin) for the layer at `layer_index`
    (zero-based). Operator rule:
        layer_0 = 0, layer_1 = thickness, layer_2 = 2*thickness, ...
    `layer_index` may be negative — caller's responsibility to filter."""
    return float(layer_index) * float(layer_thickness_in)


def plan_z_move(estimate_in: Optional[float],
                target_in: Optional[float],
                calibration: dict,
                current_duty: Optional[float]) -> AutoZPlan:
    """
    Plan the Z move from estimate_in to target_in, given calibration.

    Preconditions and failure modes (all return valid=False):
    * Missing estimate / target: invalid, reason given.
    * Incomplete calibration: invalid, operator must calibrate.
    * Current PWM duty != calibration duty: invalid, operator must
      recalibrate (speed does not scale linearly with duty).
    * Required move is NEGATIVE (Z above target) by more than TOL: invalid,
      requires_operator=True. We never command Z down automatically.

    Valid outcomes:
    * delta ~ 0 (|delta| <= TOL): valid, direction=None, duration=0.
    * delta > TOL: valid, direction="up", duration = delta / up_speed.
    """
    if estimate_in is None:
        return AutoZPlan(False, None, 0.0, 0.0, True,
                         "Z position is unknown — set the Z origin first.")
    if target_in is None:
        return AutoZPlan(False, None, 0.0, 0.0, True,
                         "No next layer target available for this job.")
    if not z_calibration.is_complete(calibration):
        return AutoZPlan(False, None, float(target_in - estimate_in), 0.0, True,
                         "Z calibration is incomplete — measure UP and DOWN speeds "
                         "at the current PWM duty.")
    if current_duty is None or not z_calibration.matches_duty(calibration, current_duty):
        return AutoZPlan(False, None, float(target_in - estimate_in), 0.0, True,
                         f"Z PWM duty ({current_duty}) does not match the "
                         f"calibration duty ({calibration.get('pwm_duty')}) — "
                         f"recalibrate at the current duty.")
    delta = float(target_in) - float(estimate_in)
    if abs(delta) <= _TOL_IN:
        return AutoZPlan(True, None, delta, 0.0, False,
                         "Already at the next layer height (within tolerance).")
    if delta < -_TOL_IN:
        return AutoZPlan(False, None, delta, 0.0, True,
                         f"Estimated Z ({estimate_in:.4f} in) is above the next layer "
                         f"target ({target_in:.4f} in) by {-delta:.4f} in — Z is NEVER "
                         f"lowered automatically. Pause for operator intervention.")
    # Positive delta: move UP.
    up = calibration.get("up_in_per_s")
    duration = delta / up                             # up is guaranteed non-null + > 0 by is_complete
    return AutoZPlan(True, "up", delta, duration, False,
                     f"Advance Z by {delta:.4f} in (≈ {duration:.2f} s at {up:.3f} in/s).")


def apply_manual_move_to_estimate(estimate_in: Optional[float],
                                  direction: str,
                                  elapsed_s: float,
                                  calibration: dict) -> Optional[float]:
    """
    Fold an observed manual Z move into the estimate.

    * `direction` — "up" | "down".
    * `elapsed_s` — how long the move actually ran (dead-man release / stop /
                    max-run timeout — whatever ended it).

    Returns the new estimate (inches above origin). If calibration is
    missing / direction is unknown / estimate is unknown, returns the
    original estimate unchanged (we never fabricate a position from the
    nominal duty — the operator must set the origin and calibrate).
    """
    if estimate_in is None:
        return None
    if direction not in ("up", "down"):
        return estimate_in
    speed = calibration.get("up_in_per_s" if direction == "up" else "down_in_per_s")
    if speed is None or elapsed_s <= 0:
        return estimate_in
    delta = float(elapsed_s) * float(speed)
    return float(estimate_in) + (delta if direction == "up" else -delta)


def can_enable_auto(mode: str,
                    z_origin_set: bool,
                    calibration: dict,
                    current_duty: Optional[float],
                    estimate_in: Optional[float]) -> Optional[str]:
    """Return a refusal string if the given auto mode CANNOT be enabled
    with the current state. None means ok."""
    if mode == "manual":
        return None
    if mode not in Z_MODES:
        return f"Unknown Z mode: {mode}."
    if not z_origin_set:
        return "Set the Z origin before enabling automatic Z."
    if estimate_in is None:
        return "Z position is unknown — set the Z origin first."
    if not z_calibration.is_complete(calibration):
        return "Calibrate Z (UP and DOWN speeds at the current PWM duty) before enabling automatic Z."
    if current_duty is None or not z_calibration.matches_duty(calibration, current_duty):
        return (f"Z PWM duty ({current_duty}) differs from the calibration duty "
                f"({calibration.get('pwm_duty')}) — recalibrate at the current duty.")
    return None
