"""
Deploy preflight — decide whether the Pi controller is safe to restart.

Reads a GRBL /api/state JSON document from stdin and either:
  - exits 0 after printing a one-line "OK" message, or
  - exits 2 after printing one or more human-readable reasons to stderr.

Called by scripts/deploy_pi.sh. Also unit-tested from tests/test_preflight_state.py
so the pass/block decision has an authoritative record. Keep it dependency-free
(stdlib only) so the Pi can run it with the system Python.
"""
from __future__ import annotations

import json
import sys
from typing import Iterable


def evaluate(state: dict) -> list[str]:
    """
    Return the list of reasons the controller is NOT safe to deploy against.
    An empty list means "safe to proceed".
    """
    grbl_raw = state.get("grbl_state") or ""
    grbl = grbl_raw.split(":", 1)[0]
    job = state.get("job") or {}
    job_state = (job.get("state") or "")

    reasons: list[str] = []
    if grbl in ("Run", "Jog"):
        reasons.append(f"GRBL is {grbl!r} — motion in progress")
    if grbl == "Hold":
        reasons.append("GRBL is 'Hold' — a job is paused, not safe to restart")
    if job_state in ("running", "held"):
        reasons.append(f"job state is {job_state!r}")
    if grbl == "":
        reasons.append("no grbl_state field in /api/state — state unverifiable")
    return reasons


def describe(state: dict) -> str:
    grbl = (state.get("grbl_state") or "").split(":", 1)[0] or "Unknown"
    job = state.get("job") or {}
    job_state = job.get("state") or "none"
    return f"GRBL={grbl}  job={job_state}"


def main(argv: Iterable[str]) -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("Preflight blocked: empty /api/state input.", file=sys.stderr)
        return 2
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Preflight blocked: cannot parse /api/state as JSON ({exc}).",
              file=sys.stderr)
        return 2
    if not isinstance(state, dict):
        print("Preflight blocked: /api/state is not a JSON object.", file=sys.stderr)
        return 2

    reasons = evaluate(state)
    if reasons:
        print("Controller not in a deployable state:", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 2

    print(f"Preflight OK: {describe(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
