"""
Machine profile of the PHYSICAL printer this controller drives (2026-10-15).

Machine profiles are independent configuration on each side. `.mudjob`
communicates job assumptions and requirements; it does not synchronize
applications. This profile is the controller's own, persisted locally
(MACHINE_PROFILE_PATH), and it is AUTHORITATIVE for this machine: a job's
`prepared_for` profile and its Designer `preflight` are only reported, never
trusted over the controller's own check (jobs/preflight.py).

Inches only (the current machine). The envelope is taken as [0, X] × [0, Y]
from the print origin (work X0 Y0) — a provisional convention, since where
the operator sets the origin on the frame is not decided: a job LARGER than
an extent fails; one that fits but lies outside the envelope placed at the
origin only warns. Unset values (None) are not checked — the defaults set
only the documented ~18 in Z travel per frame position; the operator enters
the real extents in Machine / Printer settings.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROFILE_VERSION = 1
DIMENSIONS = ("x_extent", "y_extent", "max_build_z", "z_travel_per_frame")


class MachineProfileError(ValueError):
    pass


def default_profile() -> dict:
    return {
        "version": PROFILE_VERSION,
        "name": "Unconfigured printer",
        "units": "in",
        "x_extent": None,
        "y_extent": None,
        "max_build_z": None,
        "z_travel_per_frame": 18.0,
    }


def validate(p) -> dict:
    """A normalized copy (raises MachineProfileError). Unknown keys are kept."""
    if not isinstance(p, dict):
        raise MachineProfileError("The machine profile must be an object.")
    out = copy.deepcopy(p)
    out.setdefault("version", PROFILE_VERSION)
    if not isinstance(out["version"], int) or out["version"] > PROFILE_VERSION:
        raise MachineProfileError("The machine profile is from a newer version of this software.")
    name = out.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise MachineProfileError("The printer needs a name.")
    out["name"] = name.strip()[:80]
    if out.get("units", "in") != "in":
        raise MachineProfileError("Only inches are supported (units \"in\").")
    out["units"] = "in"
    for k in DIMENSIONS:
        v = out.get(k)
        if v is None or v == "":
            out[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 < v < 100000):
            raise MachineProfileError(f"{k.replace('_', ' ').capitalize()} must be a positive number (inches) or empty.")
        out[k] = float(v)
    return out


def load(path: Path) -> tuple[dict, Optional[str]]:
    """(profile, problem). A missing file gives the defaults; a damaged one too,
    with the problem reported (never silently trusted)."""
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8"))), None
    except FileNotFoundError:
        return default_profile(), None
    except (OSError, ValueError) as exc:
        log.error("Machine profile %s unreadable: %s", path, exc)
        return default_profile(), f"The saved machine profile could not be read ({exc}); defaults are in use."


def save(path: Path, profile: dict) -> dict:
    p = validate(profile)
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)                      # atomic on the same filesystem
    return p


def envelope(p: dict) -> Optional[dict]:
    """The printable XY area in work coordinates, if both extents are set."""
    if p.get("x_extent") and p.get("y_extent"):
        return {"min_x": 0.0, "min_y": 0.0, "max_x": p["x_extent"], "max_y": p["y_extent"]}
    return None
