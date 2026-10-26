"""
Persistence for the Z mode (sixteenth pass, 2026-10-25).

File format (`Z_MODE_PATH`, default `z_mode.json`):

    {"version": 1, "mode": "manual" | "auto_confirm" | "auto"}

Default is "manual". Missing / unreadable file → defaults to "manual" (safe).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .jobs import auto_z

log = logging.getLogger(__name__)

DEFAULT_MODE = "manual"


def _norm(m) -> str:
    if isinstance(m, str) and m in auto_z.Z_MODES:
        return m
    return DEFAULT_MODE


def load(path: Path) -> str:
    try:
        with open(path) as f:
            return _norm(json.load(f).get("mode"))
    except FileNotFoundError:
        return DEFAULT_MODE
    except Exception as exc:
        log.warning("Could not read %s: %s — defaulting to manual", path, exc)
        return DEFAULT_MODE


def save(path: Path, mode: str) -> str:
    out = _norm(mode)
    payload = json.dumps({"version": 1, "mode": out}) + "\n"
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".z_mode_", dir=str(path.parent))
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
