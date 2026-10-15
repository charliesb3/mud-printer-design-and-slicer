"""Architectural snapping in the Designer (2026-10-15): screen-distance radii,
effective (trim-aware) targets, 0 / 45 / 90° angles, Shift lock, Alt free,
the physical grid. The trim regression itself: tests/test_trim_ghost.py."""
import os
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_ui_snap_smoke():
    script = os.path.join(os.path.dirname(__file__), 'js', 'ui_snap_smoke.js')
    proc = subprocess.run(['node', script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'UI SNAP SMOKE PASSED' in proc.stdout
