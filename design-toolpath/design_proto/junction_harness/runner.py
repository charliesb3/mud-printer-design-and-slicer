"""Harness runner: resolve a fixture through the PRODUCTION geometry
functions, run checks, write SVG, return a dict report.

No separate geometry engine. Everything goes through
`app._deserialise_layer` + `PrintLayer._build_effective` + the Designer's
own `to_routing_layer`, same as print preparation. Multi-layer fixtures
use `layer_design.DesignLibrary.build` (which also drives the Designer's
cross-Z coherence machinery).
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

# make the design_proto / toolpath_proto siblings importable even when the
# harness is invoked from an arbitrary cwd. design_proto FIRST so its
# `app.py` wins over toolpath_proto's.
_HERE = Path(__file__).resolve().parent
_DP   = _HERE.parent
_TP   = _DP.parent / 'toolpath_proto'
for p in (_TP, _DP):
    s = str(p)
    if s in sys.path:
        sys.path.remove(s)
    sys.path.insert(0, s)
# design_proto was inserted last so it is first in sys.path


def _default_out_dir() -> Path:
    return _HERE / '_out'


def run(doc: dict, name: str, *, out_dir: Path | None = None,
        write_svg: bool = True) -> dict:
    """Resolve a single-layer fixture document; return the harness report.

    The report shape:
      {'name', 'kind' = 'single', 'timing': {...}, 'checks': {...},
       'open_ends': int, 'paths': int, 'svg': Path | None, 'params': {...}}
    """
    out_dir = Path(out_dir) if out_dir is not None else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    from app import _deserialise_layer
    from . import checks as C
    from . import svg as S
    t0 = time.perf_counter()
    layer = _deserialise_layer(doc)
    t1 = time.perf_counter()
    paths, meta = layer._build_effective()
    t2 = time.perf_counter()
    bead = float(doc.get('material', {}).get('bead_width') or 3.0)
    contact = float(doc.get('material', {}).get('contact_overlap') or 0.75)
    style = doc.get('junction_style', 'miter')
    cks = C.run_all(paths, meta, layer, style, bead, contact)
    t3 = time.perf_counter()
    opens = (cks.get('continuity') or {}).get('count', 0)
    svg_path = None
    if write_svg:
        svg_path = S.render(doc, paths, meta, cks,
                            out_dir / f'{name}.svg', title=name)
    t4 = time.perf_counter()
    return {
        'name': name, 'kind': 'single',
        'params': {'junction_style': style, 'bead': bead,
                   'contact_overlap': contact},
        'timing': {
            'deserialise': round(t1 - t0, 4),
            'build_effective': round(t2 - t1, 4),
            'checks': round(t3 - t2, 4),
            'svg': round(t4 - t3, 4) if write_svg else 0.0,
            'total': round(t4 - t0, 4),
        },
        'paths': len(paths), 'open_ends': opens,
        'checks': cks,
        'svg': str(svg_path) if svg_path else None,
    }


def run_multi(designs: list, section_plan: list, name: str, *,
              out_dir: Path | None = None, write_svg: bool = True) -> dict:
    """Resolve a multi-layer fixture through `DesignLibrary.build`.

    `designs` is the list the harness fixture returned (ready for
    `DesignLibrary`); `section_plan` is `[(design_id, layer_count)]`.
    Writes one SVG per layer and ONE comparison SVG stacking the per-layer
    strokes with a small x offset (for a quick visual coherence check).
    """
    out_dir = Path(out_dir) if out_dir is not None else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    from layer_design import DesignLibrary
    from . import checks as C
    from . import svg as S
    lib = DesignLibrary(designs)
    layers: list = []
    t0 = time.perf_counter()
    for (design_id, count) in section_plan:
        for _ in range(count):
            layers.append(design_id)
    builds = []
    build_times = []
    for i, did in enumerate(layers):
        tb0 = time.perf_counter()
        bl = lib.build(did)
        tb1 = time.perf_counter()
        build_times.append(round(tb1 - tb0, 4))
        builds.append((i, did, bl))
    t1 = time.perf_counter()
    # the Designer's own result is `BuiltLayer.printable` + `network` meta.
    # For checks we need `paths` + `meta` as `_build_effective` returns them;
    # the cheapest way is to re-build each layer through _deserialise_layer
    # (same geometry; same documents go through the same functions).
    from app import _deserialise_layer
    layer_reports = []
    for (i, did, bl) in builds:
        doc = lib.transformed(did)[0]
        lay = _deserialise_layer(doc)
        paths, meta = lay._build_effective()
        bead = float(doc.get('material', {}).get('bead_width') or 3.0)
        contact = float(doc.get('material', {}).get('contact_overlap') or 0.75)
        style = doc.get('junction_style', 'miter')
        cks = C.run_all(paths, meta, lay, style, bead, contact)
        svg_path = None
        if write_svg:
            svg_path = S.render(doc, paths, meta, cks,
                                out_dir / f'{name}.L{i:02d}.{did}.svg',
                                title=f'{name} / layer {i} ({did})')
        layer_reports.append({
            'layer': i, 'design': did,
            'paths': len(paths),
            'open_ends': (cks.get('continuity') or {}).get('count', 0),
            'checks': cks,
            'svg': str(svg_path) if svg_path else None,
        })
    t2 = time.perf_counter()
    # cross-layer coherence summary: a layer and the one two above / below
    # should produce identical geometry away from the opening (the M3 claim).
    # Here we just report pairwise polyline-count differences — the SVGs
    # carry the visual check.
    coherence = []
    for i in range(len(layer_reports) - 1):
        a, b = layer_reports[i], layer_reports[i + 1]
        coherence.append({'between': [a['layer'], b['layer']],
                          'paths_delta': b['paths'] - a['paths']})
    return {
        'name': name, 'kind': 'multi',
        'section_plan': list(section_plan),
        'timing': {
            'builds': round(t1 - t0, 4),
            'checks_and_svg': round(t2 - t1, 4),
            'total': round(t2 - t0, 4),
            'per_build_mean': round(sum(build_times) / max(1, len(build_times)), 4),
            'per_build_max': max(build_times) if build_times else 0.0,
        },
        'layers': layer_reports,
        'coherence': coherence,
    }


def run_catalogue(single_filter: list | None = None,
                  multi_filter: list | None = None, **params) -> list:
    """Run every registered fixture (or a filtered subset). Returns the
    list of reports; writes SVGs to `_out/`."""
    from . import fixtures as F
    results = []
    for name, (builder, defaults) in F.SINGLE_LAYER.items():
        if single_filter and name not in single_filter:
            continue
        kwargs = dict(defaults, **params)
        valid = {k: v for k, v in kwargs.items()
                 if k in builder.__code__.co_varnames}
        doc = builder(**valid)
        results.append(run(doc, name))
    for name, (builder, defaults) in F.MULTI_LAYER.items():
        if multi_filter and name not in multi_filter:
            continue
        kwargs = dict(defaults, **params)
        valid = {k: v for k, v in kwargs.items()
                 if k in builder.__code__.co_varnames}
        designs, plan = builder(**valid)
        results.append(run_multi(designs, plan, name))
    return results


def summarise(reports: list) -> str:
    """One-line table + aggregate timing. Returned as text."""
    lines = []
    lines.append(f'{"fixture":32} {"kind":6} {"paths":>6} {"open":>4} '
                 f'{"fail":>5} {"time_ms":>8}')
    total_ms = 0.0
    for r in reports:
        if r['kind'] == 'single':
            fails = sum(0 if v['ok'] else 1 for v in r['checks'].values())
            ms = int(r['timing']['total'] * 1000)
            lines.append(f'{r["name"]:32} {r["kind"]:6} {r["paths"]:6d} '
                         f'{r["open_ends"]:4d} {fails:5d} {ms:8d}')
            total_ms += r['timing']['total'] * 1000
        else:
            n_layers = len(r['layers'])
            total_paths = sum(x['paths'] for x in r['layers'])
            open_sum = sum(x['open_ends'] for x in r['layers'])
            fails = sum(sum(0 if v['ok'] else 1 for v in L['checks'].values())
                        for L in r['layers'])
            ms = int(r['timing']['total'] * 1000)
            lines.append(f'{r["name"]:32} {r["kind"]:6} {total_paths:6d} '
                         f'{open_sum:4d} {fails:5d} {ms:8d}  '
                         f'(n={n_layers})')
            total_ms += r['timing']['total'] * 1000
    lines.append(f'{"total":32} {"":6} {"":6} {"":4} {"":5} {int(total_ms):8d}')
    return '\n'.join(lines)
