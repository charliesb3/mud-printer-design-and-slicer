"""Lightweight junction-geometry test harness.

Reproduce individual junctions through the SAME production geometry
functions (`model._build_effective`, `wall_systems.generate`,
`network.round_junctions`) that print preparation uses, without needing a
multi-layer building or the browser. Each fixture resolves to printable
paths + diagnostics in milliseconds; the harness runs automated checks on
the output and renders an SVG preview per fixture so a geometry pass can
be inspected visually.

Usage (shell, from `design-toolpath/`):

    design_proto/.venv/bin/python -m design_proto.junction_harness
    design_proto/.venv/bin/python -m design_proto.junction_harness --list
    design_proto/.venv/bin/python -m design_proto.junction_harness single_to_thick
    design_proto/.venv/bin/python -m design_proto.junction_harness circle_rect --style round --system interleaved

Usage (code / tests):

    from design_proto.junction_harness import fixtures, runner
    rep = runner.run(fixtures.single_to_thick(), name='single_to_thick')

See `design_proto/tests/test_junction_harness.py` for the pytest driver.
Full guide: `layer_assembly/PROJECT_MEMORY.md` → "JUNCTION HARNESS".
"""
from . import fixtures, runner, checks, svg

__all__ = ['fixtures', 'runner', 'checks', 'svg']
