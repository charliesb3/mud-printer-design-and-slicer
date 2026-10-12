"""Minimal SVG renderer for the junction harness.

Produces a standalone SVG per fixture showing:
  * architectural source paths (dashed grey, labelled)
  * material rings (semi-transparent pale fill)
  * printable paths coloured by role (faces, caps, wall system, lattice,
    beads, junction_step)
  * check findings as RED markers with a tooltip

The y axis is flipped so the SVG reads like the designer's canvas
(y increases up); no external library is used (SVG is one string).
"""
from __future__ import annotations
from pathlib import Path


ROLE_STYLE: dict = {
    # role → (stroke, stroke-width, dash)
    'inner':       ('#2d9bf0', 1.2, ''),       # faces (classic blue)
    'outer':       ('#2d9bf0', 1.2, ''),
    'free':        ('#2d9bf0', 1.2, ''),
    'cap':         ('#8ab52a', 1.2, ''),       # caps / junction arcs (green)
    'wall_system': ('#e07a2a', 1.1, ''),       # Wall System paths (orange)
    'lattice':     ('#aa44c4', 0.9, ''),       # wall infill (purple)
    'bead':        ('#2d9bf0', 1.2, ''),       # single bead strands
}

CAP_STEP_STYLE = ('#d02b2b', 1.6, '')          # the M4 junction_step connectors
UNKNOWN_STYLE  = ('#555555', 0.8, '2,2')


def _bbox(paths, rings):
    xs, ys = [], []
    for p in paths:
        for q in (p.sample_points() if hasattr(p, 'sample_points') else []):
            xs.append(q.x); ys.append(q.y)
    for r in rings or []:
        for q in r:
            xs.append(q.x); ys.append(q.y)
    if not xs:
        return 0.0, 0.0, 100.0, 100.0
    pad = 20.0
    return (min(xs) - pad, min(ys) - pad,
            max(xs) + pad, max(ys) + pad)


def _polyline(points, closed=False):
    pts = ' '.join(f'{q.x:.3f},{-q.y:.3f}' for q in points)
    if closed:
        return f'<polygon points="{pts}" />'
    return f'<polyline points="{pts}" />'


def _label_source(src):
    """A dashed line / rect / circle for the architectural reference."""
    kind = src.get('type')
    if kind == 'LinePath':
        a, b = src['start'], src['end']
        return (f'<line x1="{a[0]:.3f}" y1="{-a[1]:.3f}" '
                f'x2="{b[0]:.3f}" y2="{-b[1]:.3f}" />')
    if kind == 'RectanglePath':
        x, y = src['x'], src['y']
        w, h = src['w'], src['h']
        return (f'<rect x="{x:.3f}" y="{-(y + h):.3f}" '
                f'width="{w:.3f}" height="{h:.3f}" />')
    if kind == 'CirclePath':
        return (f'<circle cx="{src["cx"]:.3f}" cy="{-src["cy"]:.3f}" '
                f'r="{src["radius"]:.3f}" />')
    return ''


def render(doc, paths, meta, checks_report: dict, out_path: str | Path,
           title: str = '') -> Path:
    """Write an SVG preview; returns the resolved path."""
    from .checks import _flatten_regions
    rings = _flatten_regions((meta or {}).get('regions') or [])
    minx, miny, maxx, maxy = _bbox(paths, rings)
    # output is in SVG units (= inches); scale 4 px/in for readable viewing
    scale = 4
    width = (maxx - minx) * scale
    height = (maxy - miny) * scale
    out = []
    out.append(f'<?xml version="1.0" encoding="UTF-8"?>')
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
               f'height="{height:.0f}" '
               f'viewBox="{minx:.1f} {-maxy:.1f} {maxx - minx:.1f} {maxy - miny:.1f}">')
    out.append(f'<title>{title}</title>')
    out.append('<style>'
               ' .arch { fill: none; stroke: #888; stroke-width: 0.5; '
               '         stroke-dasharray: 4,3; opacity: 0.6; } '
               ' .ring { fill: #f0e8d8; stroke: #c8bfa4; stroke-width: 0.3; opacity: 0.5; } '
               ' .finding { fill: #d02b2b; stroke: #a00; stroke-width: 0.3; opacity: 0.9; } '
               ' .finding-ring { fill: none; stroke: #d02b2b; stroke-width: 0.4; opacity: 0.7; } '
               ' text { font: 3px sans-serif; fill: #333; } '
               ' .legend { font: 3.5px sans-serif; fill: #000; } '
               '</style>')

    # material rings
    out.append('<g class="rings">')
    for r in rings:
        out.append(f'<polygon class="ring" points="'
                   + ' '.join(f'{q.x:.3f},{-q.y:.3f}' for q in r) + '" />')
    out.append('</g>')

    # architectural reference
    out.append('<g class="arch">')
    for src in doc.get('source_paths') or []:
        out.append(_label_source(src))
    out.append('</g>')

    # printable paths grouped by role so legend / toggling is simple
    role_groups: dict = {}
    for p in paths:
        role = getattr(p, 'role', '') or 'other'
        label = getattr(p, 'label', '') or ''
        if label == 'junction_step':
            key = 'junction_step'
        else:
            key = role
        role_groups.setdefault(key, []).append(p)
    for role, group in role_groups.items():
        style = (CAP_STEP_STYLE if role == 'junction_step'
                 else ROLE_STYLE.get(role, UNKNOWN_STYLE))
        stroke, w, dash = style
        dasharray = f' stroke-dasharray="{dash}"' if dash else ''
        out.append(f'<g stroke="{stroke}" stroke-width="{w}" '
                   f'fill="none" opacity="0.9"{dasharray} '
                   f'data-role="{role}">')
        for p in group:
            pts = p.sample_points() if hasattr(p, 'sample_points') else []
            if len(pts) < 2:
                continue
            out.append(_polyline(pts, getattr(p, 'closed', False)))
        out.append('</g>')

    # check findings (red markers)
    out.append('<g class="findings">')
    for check, info in (checks_report or {}).items():
        if info.get('ok'):
            continue
        for f in info.get('findings', []):
            where = f.get('where')
            if not where:
                continue
            x, y = float(where[0]), -float(where[1])
            msg = f.get('message', check)
            out.append(f'<circle class="finding" cx="{x:.3f}" cy="{y:.3f}" r="1.2"><title>{check}: {msg}</title></circle>')
            out.append(f'<circle class="finding-ring" cx="{x:.3f}" cy="{y:.3f}" r="2.4" />')
    out.append('</g>')

    # legend at top-left of the viewbox
    legend = ['<g class="legend">',
              f'<text x="{minx + 2:.3f}" y="{-maxy + 6:.3f}">{title}</text>']
    y_off = -maxy + 12
    for role, _group in role_groups.items():
        style = (CAP_STEP_STYLE if role == 'junction_step'
                 else ROLE_STYLE.get(role, UNKNOWN_STYLE))
        legend.append(
            f'<rect x="{minx + 2:.3f}" y="{y_off:.3f}" '
            f'width="4" height="1.2" fill="{style[0]}" />')
        legend.append(f'<text x="{minx + 8:.3f}" y="{y_off + 1.2:.3f}">{role}</text>')
        y_off += 4
    legend.append('</g>')
    out.append('\n'.join(legend))

    out.append('</svg>')
    Path(out_path).write_text('\n'.join(out), encoding='utf-8')
    return Path(out_path)
