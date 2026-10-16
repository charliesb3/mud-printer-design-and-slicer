"""
GOLDEN REGRESSION (M0, 2026-10-15) — the printable / architectural outputs of
real and synthetic projects, frozen (layer_assembly/tests/golden/). Any change
to a component (Designer build, envelope build, Assembly resolve, PrintPlan,
`.mudjob`) fails with WHERE it changed. Snapshots are rewritten only with
GOLDEN_UPDATE=1 — deliberately, after reviewing the change (cases tagged in
golden/cases.py CHANGES_AT are expected to change at that milestone, never
before). GOLDEN_DUMP=<dir> writes the full canonical JSON for diffing.
"""
import glob
import os

import pytest

from layer_assembly.tests.golden import harness as H

CASES = sorted(glob.glob(os.path.join(H.PROJECTS, '*.muddesign')))


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setenv('LAYER_ASSEMBLY_WORKERS', '0')


def test_every_case_has_a_snapshot():
    assert len(CASES) >= 20
    missing = [c for c in CASES if not os.path.exists(H.snapshot_path(c))]
    assert not missing, missing


@pytest.mark.parametrize('case', CASES, ids=[os.path.basename(c)[:-10] for c in CASES])
def test_golden(case):
    diffs = H.compare(case)
    assert not diffs, '\n'.join(f'{k}: expected {e} — got {g}' for k, e, g in diffs)
