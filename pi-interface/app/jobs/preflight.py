"""
Job preflight against THIS controller's machine profile (2026-10-15).

Computed by the controller itself from the loaded job — its G-code XY moves
(all motion), its lift / layer Z metadata and the EFFECTIVE lifts — and
compared with the controller's own machine profile, which is authoritative.
What the job says about itself (`requirements`, the Designer's `preflight`,
the `prepared_for` profile) is only REPORTED: a Designer "fits" never
overrides a failing check here.

A native building job (.mudjob) that fails a configured limit is BLOCKING:
Start is refused until the job or the profile is changed. External flat
G-code is checked from its parsed moves for information only (its
coordinate semantics are unknown).
"""
from __future__ import annotations

from typing import Optional

TOL = 1e-4
MISMATCH_IN = 0.5          # prepared-for vs actual dimensions: "materially different"


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _size_check(axis: str, lo: float, hi: float, limit: Optional[float]) -> dict:
    A, size = axis.upper(), round(hi - lo, 4)
    c = {"id": axis, "required": size, "limit": limit, "bounds": [round(lo, 4), round(hi, 4)]}
    if limit is None:
        c.update(status="unchecked", message=f"{A}: printable {A} is not set in this printer's profile — not checked.")
    elif size > limit + TOL:
        c.update(status="fail", message=f"{A}: the job spans {_f(size)} in; this printer's {A} is {_f(limit)} in.")
    elif lo < -TOL or hi > limit + TOL:
        c.update(status="warn", message=f"{A}: the job fits ({_f(size)} of {_f(limit)} in) but lies at {_f(lo)} – {_f(hi)} in "
                                        f"from the print origin, outside the envelope placed there — set the origin so it fits.")
    else:
        c.update(status="ok", message=f"{A}: {_f(size)} of {_f(limit)} in.")
    return c


def _limit_check(cid: str, label: str, required: Optional[float], limit: Optional[float], fail: str, ok: str) -> dict:
    c = {"id": cid, "required": required, "limit": limit}
    if required is None:
        c.update(status="unchecked", message=f"{label}: the job does not say (no Z metadata).")
    elif limit is None:
        c.update(status="unchecked", message=f"{label}: not set in this printer's profile — not checked.")
    elif required > limit + TOL:
        c.update(status="fail", message=fail.format(r=_f(required), l=_f(limit)))
    else:
        c.update(status="ok", message=ok.format(r=_f(required), l=_f(limit)))
    return c


def job_requirements(job) -> dict:
    """What the controller itself reads from the job (inches)."""
    b = job.motion_bounds()
    L = job.layout
    building, lift_need = None, None
    tops = [lf.meta.get("z_top") for lf in job.lifts if isinstance(lf.meta.get("z_top"), (int, float))]
    tops += list(L.layer_top.values())
    if tops:
        building = max(tops)
    if job.kind == "building":
        t = job.layer_thickness()
        need = []
        for k in range(L.count):
            d = L.describe(k)
            layers = (d.get("layers") or {}).get("count")
            if layers and t:
                need.append(layers * t)                         # physical estimate (observed or nominal)
            elif isinstance(d.get("z_top"), (int, float)) and isinstance(d.get("z_bottom"), (int, float)):
                need.append(d["z_top"] - d["z_bottom"])
        lift_need = max(need) if need else None
    return {"xy_bounds": b, "building_height": building, "max_lift_height": lift_need}


def job_preflight(job, profile: dict) -> dict:
    req = job_requirements(job)
    checks = []
    b = req["xy_bounds"]
    if b:
        checks.append(_size_check("x", b["min_x"], b["max_x"], profile.get("x_extent")))
        checks.append(_size_check("y", b["min_y"], b["max_y"], profile.get("y_extent")))
    checks.append(_limit_check("z", "Building Z", req["building_height"], profile.get("max_build_z"),
                               "Building Z: the building reaches {r} in; this printer allows {l} in.",
                               "Building Z: {r} of {l} in."))
    if job.kind == "building":
        checks.append(_limit_check("lift", "Z travel per frame", req["max_lift_height"], profile.get("z_travel_per_frame"),
                                   "Lift: a lift needs ≈ {r} in of Z travel; one frame position gives {l} in.",
                                   "Lift: tallest lift ≈ {r} of {l} in Z travel per frame."))
    native = job.kind == "building" and job.executable
    failing = [c for c in checks if c["status"] == "fail"]
    notes = []
    # what the job claims — reported, never trusted over the checks above
    dp = job.designer_preflight
    if isinstance(dp, dict) and dp.get("fits") is True and failing:
        notes.append("The Designer reported that this job fits — this printer's own check does NOT agree (this printer is authoritative).")
    pf = job.prepared_for
    prepared_for_mismatch = None
    if isinstance(pf, dict):
        diffs, structured = [], []
        for k, label in (("x_extent", "X"), ("y_extent", "Y"), ("max_build_z", "max building Z"),
                         ("z_travel_per_frame", "Z per frame")):
            a, mine = pf.get(k), profile.get(k)
            if isinstance(a, (int, float)) and isinstance(mine, (int, float)) and abs(a - mine) > MISMATCH_IN:
                diffs.append(f"{label} {_f(a)} vs {_f(mine)} in")
                structured.append({"key": k, "label": label, "designer": a, "controller": mine, "units": "in"})
        if diffs:
            notes.append(f"Prepared for a different machine (\"{pf.get('name') or '?'}\"): " + ", ".join(diffs) + ".")
            prepared_for_mismatch = {
                "designer_name": pf.get("name") or None,
                "controller_name": profile.get("name") or None,
                "diffs": structured,
            }
    decl = job.declared_requirements
    if isinstance(decl, dict) and isinstance(decl.get("xy_bounds"), dict) and b:
        if any(abs(decl["xy_bounds"].get(k, 0) - b[k]) > 0.01 for k in ("min_x", "min_y", "max_x", "max_y")):
            notes.append("The job's declared XY bounds differ from its G-code — the G-code is what is checked.")
    return {
        "native": native,
        "fits": not failing,
        "blocking": bool(native and failing),
        "checks": checks,
        "notes": notes,
        "profile_name": profile.get("name"),
        "requirements": req,
        "designer_fits": dp.get("fits") if isinstance(dp, dict) else None,
        "prepared_for_mismatch": prepared_for_mismatch,
    }
