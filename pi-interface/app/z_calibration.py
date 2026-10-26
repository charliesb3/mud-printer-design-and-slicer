"""
Persistence for Z-axis time-based calibration (sixteenth pass, 2026-10-25).

The Z actuator is OPEN-LOOP (no feedback) — see arduino-grbl design notes.
Automatic Z advancement between .mudjob layers uses TIME-based moves at the
configured PWM duty, so we need a measured speed (inches per second) for
each direction, associated with the PWM duty at which it was measured.

File format (`Z_CALIBRATION_PATH`, default `z_calibration.json` next to the
Pi `.env`):

    {"version": 1,
     "up_in_per_s":  <float > 0 | null>,
     "down_in_per_s":<float > 0 | null>,
     "pwm_duty":     <float in [0, 1] | null>,
     "calibrated_at":<unix seconds | null>}

Nulls mean "not calibrated." Any auto-Z action refuses while any field is
null OR the current ZController PWM duty differs from `pwm_duty`.

NOT the same thing as `z_setting.json` (which stores the operator's chosen
PWM duty). Calibration is a measured speed FOR that duty.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def default_calibration() -> dict:
    return {"version": 1, "up_in_per_s": None, "down_in_per_s": None,
            "pwm_duty": None, "calibrated_at": None}


def _norm_speed(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not (0 < f <= 10.0):                   # sanity: 10 in/s is faster than any clay-printer Z
        return None
    return f


def _norm_duty(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not (0 < f <= 1.0):
        return None
    return f


def validate(p) -> dict:
    """Return a normalized copy; silently drops out-of-range or malformed values."""
    out = default_calibration()
    if isinstance(p, dict):
        out["up_in_per_s"]   = _norm_speed(p.get("up_in_per_s"))
        out["down_in_per_s"] = _norm_speed(p.get("down_in_per_s"))
        out["pwm_duty"]      = _norm_duty(p.get("pwm_duty"))
        ca = p.get("calibrated_at")
        if isinstance(ca, (int, float)) and ca > 0:
            out["calibrated_at"] = float(ca)
    return out


def is_complete(p: dict) -> bool:
    """All three measurements present and sane."""
    return (p.get("up_in_per_s") is not None
            and p.get("down_in_per_s") is not None
            and p.get("pwm_duty") is not None)


def matches_duty(p: dict, current_duty: float, tol: float = 1e-3) -> bool:
    """True if the stored PWM duty matches the ZController's current duty.
    Auto-Z refuses if the duty was changed since calibration — speed does NOT
    scale linearly with PWM, so prior measurements are invalid."""
    pd = p.get("pwm_duty")
    if pd is None:
        return False
    try:
        return abs(float(current_duty) - float(pd)) <= tol
    except (TypeError, ValueError):
        return False


def load(path: Path) -> dict:
    """Load the persisted calibration, or defaults if missing / unreadable."""
    try:
        with open(path) as f:
            return validate(json.load(f))
    except FileNotFoundError:
        return default_calibration()
    except Exception as exc:
        log.warning("Could not read %s: %s — using defaults", path, exc)
        return default_calibration()


def save(path: Path, cal: dict) -> dict:
    """Atomically write the calibration (stamps `calibrated_at` to now)."""
    out = validate(cal)
    if is_complete(out):
        out["calibrated_at"] = time.time()
    payload = json.dumps(out, indent=2) + "\n"
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".z_cal_", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return out
