"""
Persistence for the manual Z PWM duty (operator-adjustable at runtime).

The ZController's `duty` field is the fixed PWM level used for every manual
Z move. Historically it was configured ONCE from the Pi `.env`
(`Z_PWM_DUTY`); from the tenth-pass (2026-10-25) the operator can adjust it
live via a slider in the Z panel. The chosen value is persisted here so a
restart (or power cycle) does not reset the operator's chosen Z speed.

File format (`Z_SETTING_PATH`, default `z_setting.json` next to the Pi
`.env`): one JSON object:
    {"version": 1, "duty": 0.60}
`duty` is a float in [0, 1]. The file is written atomically.

If the file is missing or unreadable the loader returns None and the caller
falls back to `config.Z_PWM_DUTY`.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _clamp(duty: float) -> float:
    try:
        d = float(duty)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, d))


def load(path: Path) -> Optional[float]:
    """Load the persisted duty, or None if absent / unreadable."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("Could not read %s: %s — using config default", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    duty = data.get("duty")
    if duty is None:
        return None
    return _clamp(duty)


def save(path: Path, duty: float) -> None:
    """
    Atomically write the duty value. Raises OSError on write failure so the
    caller can surface it to the UI.
    """
    duty = _clamp(duty)
    payload = json.dumps({"version": 1, "duty": duty}) + "\n"
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".z_setting_", dir=str(path.parent))
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
