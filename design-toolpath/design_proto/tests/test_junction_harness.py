"""Pytest driver for the junction_harness.

Runs every registered fixture, asserts baseline invariants, and pins the
two bug-reproducer fixtures (`single_to_thick`, `circle_rect`) so the
harness continues to flag the manually reported junction symptoms. If a
production change silences one of those reproducers, update the fixture
to a stronger case — do NOT loosen the pin.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))

from design_proto.junction_harness import fixtures as F
from design_proto.junction_harness import runner as R
from design_proto.junction_harness import checks as C


# ---- baseline: every registered fixture resolves and routes closed -------

@pytest.mark.parametrize('name', sorted(F.SINGLE_LAYER))
def test_single_layer_fixtures_resolve_and_close(name, tmp_path):
    """Every single-layer fixture (defaults) resolves through the production
    functions, produces at least one printable path, and has no open
    routing component. Also verifies no exceptions in the checks."""
    builder, defaults = F.SINGLE_LAYER[name]
    doc = builder(**defaults)
    rep = R.run(doc, name, out_dir=tmp_path, write_svg=True)
    assert rep['paths'] >= 1, (name, rep)
    assert rep['open_ends'] == 0, (name, rep['open_ends'])
    assert rep['checks']['continuity']['ok'], rep['checks']['continuity']
    # the SVG actually exists on disk
    assert rep['svg'] and os.path.exists(rep['svg'])


@pytest.mark.parametrize('name', sorted(F.MULTI_LAYER))
def test_multi_layer_fixtures_route_closed_per_layer(name, tmp_path):
    """Every multi-layer fixture resolves each layer independently; every
    layer routes closed."""
    builder, defaults = F.MULTI_LAYER[name]
    designs, plan = builder(**defaults)
    rep = R.run_multi(designs, plan, name, out_dir=tmp_path, write_svg=True)
    assert len(rep['layers']) == sum(c for _, c in plan)
    for L in rep['layers']:
        assert L['open_ends'] == 0, (name, L['layer'], L['open_ends'])


# ---- bug-reproducer pins: these fixtures identify the two symptoms the ---
# ---- user reported during recent manual testing. Loosening the assertions
# ---- defeats the purpose of the harness — update the fixture instead.

def test_bug_circle_rect_distorted_parallel(tmp_path):
    """J2 FIX pin: circle/rectangle junction with Parallel Walls now gets
    continuous lanes across both the rectangle and the circle (previously
    only the rectangle was covered; the exterior gap at the circle side
    exceeded 160 in). After J2 the united-region generation covers both
    walls and the junction-stitching joins open lane endpoints at the
    shared nodes — every route closes, no exterior gap. The behavioural
    assertions below pin BOTH the surface symptom (continuity + exterior)
    AND the J2 production path (`j2_united_region: True`, a per-run entry
    naming both the R and C sources), so a future change that silently
    reverts to split-per-part geometry fails this test."""
    doc = F.circle_rect()                              # defaults: parallel + round
    rep = R.run(doc, 'bug_circle_rect', out_dir=tmp_path, write_svg=True)
    # continuity: no open routes
    assert rep['open_ends'] == 0, (rep['open_ends'], rep['checks']['continuity'])
    # exterior continuity: no long exterior gaps
    assert rep['checks']['exterior_continuity']['ok'], \
        rep['checks']['exterior_continuity']
    # region_coverage directly asserts the J1-era symptom is gone (every
    # material ring has a bead within 2 beads of some vertex)
    assert rep['checks']['region_coverage']['ok'], \
        rep['checks']['region_coverage']
    # coverage: paths are present (lanes stitched into one or more closed
    # loops rather than one straggle covering only part of the material)
    assert rep['paths'] >= 2, rep['paths']

    # pin the J2 PRODUCTION PATH: the fallback must have fired, not the
    # per-wall split. The report must carry j2_united_region: True and
    # per_run data for the united ring set.
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'toolpath_proto'))
    from app import _deserialise_layer
    layer = _deserialise_layer(doc)
    _, meta = layer._build_effective()
    recs = (meta.get('network') or {}).get('wall_systems') or []
    assert len(recs) == 1, [r.get('sources') for r in recs]
    rec = recs[0]
    assert rec.get('j2_united_region') is True, rec
    assert rec.get('per_run'), rec
    # both source paths covered by the united record
    srcs = set(rec.get('sources') or [])
    assert {'C', 'R'}.issubset(srcs), srcs


def test_bug_single_to_thick_variants(tmp_path):
    """REPRODUCES surface: a single-bead wall attaching to a thick host
    across every junction style, every host system. The pin is loose
    (continuity closes on every variant with M4) — the harness is the
    visual check (SVG per variant). Confirms the fixture itself runs
    cleanly for every combination so a future production regression can
    be caught by comparing reports."""
    for style in ('miter', 'round'):
        for host in ('parallel', 'interleaved', 'linked', 'chain', 'skin_web'):
            doc = F.single_to_thick(style=style, host_system=host)
            rep = R.run(doc, f'single_to_thick_{host}_{style}',
                        out_dir=tmp_path, write_svg=False)
            # routes must close (M4 regression guard)
            assert rep['open_ends'] == 0, (host, style, rep['open_ends'])
            # continuity check is the hard assertion; exterior continuity
            # is informational for this fixture (periodic patterns)
            assert rep['checks']['continuity']['ok'], (host, style)


def test_bug_single_bead_stub_variants(tmp_path):
    """The second specific reproducer: a single-bead line attaching NEAR
    a corner, where the host's junction mitre meets the stub. Pins the
    continuity invariant across host systems + both junction styles."""
    for style in ('miter', 'round'):
        for host in ('parallel', 'interleaved', 'chain', 'skin_web', None):
            doc = F.single_bead_stub(style=style, host_system=host)
            rep = R.run(doc, f'single_bead_stub_{host}_{style}',
                        out_dir=tmp_path, write_svg=False)
            assert rep['open_ends'] == 0, (host, style, rep['open_ends'])
            assert rep['checks']['continuity']['ok'], (host, style)


# ---- timing watchdog ----------------------------------------------------

def test_single_layer_fixtures_are_fast(tmp_path):
    """The harness is a FAST feedback loop. Budget is set per fixture so
    hardware variance does not false-alarm on this watchdog while still
    catching a true regression. Observed on this laptop (2026-10-12):
    circle_rect ~1.75 s cold (build 1.15 s: probe x2 + united), corner_90
    ~1.17 s (checks 1.0 s on dense interleaved output); other fixtures
    comfortably under 900 ms. Documented reference budget on a faster
    machine is ~600 ms (per `layer_assembly/PROJECT_MEMORY.md` →
    "JUNCTION HARNESS"). If a fixture exceeds its budget here, document
    and tighten — do NOT just raise the number."""
    budgets = {
        'circle_rect': 3.0,   # J2 probe + united Parallel generation
        'corner_90':   2.0,   # dense interleaved output → O(n^2) overlap check
        'cross':       1.5,   # similar dense output at an X
    }
    default_budget = 0.9
    slow = []
    for name, (builder, defaults) in F.SINGLE_LAYER.items():
        doc = builder(**defaults)
        rep = R.run(doc, name, out_dir=tmp_path, write_svg=False)
        budget = budgets.get(name, default_budget)
        if rep['timing']['total'] > budget:
            slow.append((name, rep['timing']['total'], budget))
    assert not slow, f'slow single-layer fixtures: {slow}'
