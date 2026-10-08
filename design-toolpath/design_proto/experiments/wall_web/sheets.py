"""
PNG comparison sheets for manual review (macOS: rendered with qlmanage).

    cd design-toolpath/design_proto
    .venv/bin/python experiments/wall_web/sheets.py      # → out/01_straight.png … 10_cross_z_tracking.png

One sheet per geometry: Production Zigzag / Production Wave / Candidate A /
Candidate B on IDENTICAL geometry, scale and framing. The cross-Z sheet
overlays consecutive layers (mapped back to the reference frame).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import HERE, BEAD, CONTACT, Faces, Corridor, bbox, resample   # noqa: E402
import fixtures as FX          # noqa: E402
import run as R                # noqa: E402
import truss as TA             # noqa: E402
import propagate as PB         # noqa: E402

PX = 3200                      # output size (square)
OUT = os.path.join(HERE, 'out')
PANELS = [
    ('prod_zz', 'Production — Zigzag', '#2f5fb3'),
    ('prod_wave', 'Production — Wave', '#3f8fd0'),
    ('A', 'Candidate A — adaptive truss / phase field', '#1f9d55'),
    ('B', 'Candidate B — boundary propagation', '#d9822b'),
]
CAPTION = (f'W {BEAD:g} in · contact sep. {CONTACT:g} in · production S 20 · '
           f'A / B: θ {R.THETA:g}°, bond {BEAD:g} in, turn r {0.5 * BEAD:g} in, max skin span 40 in')
SHEETS = [
    ('01_straight', 'straight'), ('02_curve', 'gentle'), ('03_variable_width', 'variable'),
    ('04_u_bend', 'ubend'), ('05_corner', 'corner'), ('06_short_wall', 'short'),
    ('07_one_opening', 'opening1'), ('08_multiple_openings', 'openings'),
    ('09_reference_network', 'reference'),
]


def _path(poly, closed=False):
    return 'M' + 'L'.join(f'{p.x:.2f},{p.y:.2f}' for p in poly) + ('Z' if closed else '')


def _contact_runs(F, polys):
    """Web stretches within contact + 0.6 in of a face (contact / bond)."""
    out = []
    for poly in polys:
        S = resample(poly, 0.5)
        run = []
        for q in S + [None]:
            pr = F.project(q) if q is not None else None
            if pr is not None and pr[0] <= CONTACT + 0.6:
                run.append(q)
            elif run:
                out.append(run)
                run = []
    return out


def _panel(regions, polys, colour, anchors=(), unit=1.0):
    rings = [r for reg in regions for r in reg]
    F = Faces(rings)
    d = ''.join(_path(r, True) for r in rings)
    g = [f'<path d="{d}" fill="#f1e8d8" fill-rule="evenodd" stroke="#b9a687" stroke-width="{BEAD}" '
         f'stroke-opacity="0.55" stroke-linejoin="round"/>',
         f'<path d="{d}" fill="none" stroke="#5b4d3a" stroke-width="{0.35 * unit}"/>']
    for p in polys:
        if len(p) >= 2:
            g.append(f'<path d="{_path(p)}" fill="none" stroke="{colour}" stroke-opacity="0.35" '
                     f'stroke-width="{BEAD}" stroke-linejoin="round" stroke-linecap="round"/>')
            g.append(f'<path d="{_path(p)}" fill="none" stroke="{colour}" stroke-width="{0.45 * unit}"/>')
    for run in _contact_runs(F, polys):
        if len(run) >= 2:
            g.append(f'<path d="{_path(run)}" fill="none" stroke="#111" stroke-width="{1.1 * unit}" '
                     f'stroke-linecap="round"/>')
        else:
            g.append(f'<circle cx="{run[0].x:.2f}" cy="{run[0].y:.2f}" r="{0.75 * unit}" fill="#111"/>')
    for a in anchors:
        s = 1.4 * unit
        g.append(f'<path d="M{a.x:.2f},{a.y - s:.2f}L{a.x + s:.2f},{a.y:.2f}L{a.x:.2f},{a.y + s:.2f}'
                 f'L{a.x - s:.2f},{a.y:.2f}Z" fill="none" stroke="#c2185b" stroke-width="{0.4 * unit}"/>')
    return ''.join(g)


def _a_anchors(regions):
    """Candidate A's segment anchors (ends, corners) on the centre line."""
    out = []
    for rings in regions:
        C = Corridor(rings, CONTACT)
        for run in C.runs:
            lo, hi = (0.0, run.length) if run.cycle else C.span(run)
            for x in ([] if run.cycle else [lo, hi]) + C.corners(run):
                out.append(C.geo.centre(run, x))
    return out


def _layout(w, h, n, label):
    best = None
    for cols in (1, 2, 4):
        rows = (n + cols - 1) // cols
        W, H = cols * w, rows * (h + label)
        if best is None or max(W, H) < best[0]:
            best = (max(W, H), cols, rows, W, H)
    return best[1:]


def _render(svg, name):
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, name + '.svg')
    with open(p, 'w') as f:
        f.write(svg)
    subprocess.run(['qlmanage', '-t', '-s', str(PX), '-o', tmp, p], capture_output=True, check=False)
    shutil.move(p + '.png', os.path.join(OUT, name + '.png'))
    shutil.rmtree(tmp, ignore_errors=True)


def _sheet(name, title, cells, extent, caption, note=''):
    """cells: [(label, svg group)] drawn in identical frames of `extent`."""
    x0, y0, x1, y1 = extent
    pad = 0.06 * max(x1 - x0, y1 - y0) + 6
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w, h = x1 - x0, y1 - y0
    lab = 0.07 * max(w, h) + 4
    cols, rows, W, H = _layout(w, h, len(cells), lab)
    head = 0.06 * max(W, H)
    foot = 0.07 * max(W, H)
    side = max(W, H + head + foot)
    ox, oy = (side - W) / 2, head + (side - head - foot - H) / 2
    fs = lab * 0.42
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side:.1f} {side:.1f}" '
             f'width="{PX}" height="{PX}"><rect width="{side:.1f}" height="{side:.1f}" fill="white"/>',
             f'<text x="{side * 0.02:.1f}" y="{head * 0.62:.1f}" font-family="Helvetica" font-weight="bold" '
             f'font-size="{head * 0.42:.1f}" fill="#222">{title}</text>']
    for i, (label, colour, group) in enumerate(cells):
        cx = ox + (i % cols) * w
        cy = oy + (i // cols) * (h + lab)
        main, _, sub = label.partition(' | ')
        f1 = fs if not sub else fs * 0.8
        parts.append(f'<text x="{cx + 0.02 * w:.1f}" y="{cy + lab * (0.7 if not sub else 0.45):.1f}" '
                     f'font-family="Helvetica" font-weight="bold" font-size="{f1:.1f}" fill="{colour}">{main}</text>')
        if sub:
            parts.append(f'<text x="{cx + 0.02 * w:.1f}" y="{cy + lab * 0.88:.1f}" font-family="Helvetica" '
                         f'font-size="{fs * 0.55:.1f}" fill="#444">{sub}</text>')
        parts.append(f'<rect x="{cx + 0.01 * w:.1f}" y="{cy + lab:.1f}" width="{0.98 * w:.1f}" height="{h:.1f}" '
                     f'fill="none" stroke="#ddd" stroke-width="{0.002 * side:.2f}"/>')
        parts.append(f'<g transform="translate({cx - x0:.2f},{cy + lab - y0:.2f})">{group}</g>')
    ly = side - foot
    fsm = foot * 0.2
    legend = ('Beige = wall material · brown band = skin bead · coloured band = web bead · '
              'BLACK = contact / bond portion · magenta ◇ = Candidate A segment anchor (end / corner)')
    parts.append(f'<text x="{side * 0.02:.1f}" y="{ly + foot * 0.33:.1f}" font-family="Helvetica" '
                 f'font-size="{fsm:.1f}" fill="#444">{caption}</text>')
    parts.append(f'<text x="{side * 0.02:.1f}" y="{ly + foot * 0.6:.1f}" font-family="Helvetica" '
                 f'font-size="{fsm * 0.85:.1f}" fill="#666">{legend}</text>')
    if note:
        parts.append(f'<text x="{side * 0.02:.1f}" y="{ly + foot * 0.85:.1f}" font-family="Helvetica" '
                     f'font-size="{fsm * 0.85:.1f}" fill="#666">{note}</text>')
    parts.append('</svg>')
    _render(''.join(parts), name)
    return os.path.join(OUT, name + '.png')


def geometry_sheet(name, case):
    desc, fn = next((d, f) for n, d, f in FX.CASES if n == case)
    regions = fn()
    rings = [r for reg in regions for r in reg]
    extent = bbox(rings)
    unit = max(extent[2] - extent[0], extent[3] - extent[1]) / 300.0
    unit = max(1.0, unit)
    anchors = _a_anchors(regions)
    cells = []
    for key, label, colour in PANELS:
        polys, _, _ = R.run_method(key, regions)
        cells.append((label, colour, _panel(regions, polys, colour, anchors if key == 'A' else (), unit)))
    return _sheet(name, desc, cells, extent, CAPTION)


def cross_z_sheet(name='10_cross_z_tracking'):
    """Curved wall, scale 1.00 → 1.10 in 2 % steps (thickness physical), every
    layer mapped back to the reference frame and overlaid. Window: the middle
    of the wall, where re-phasing is easy to see."""
    series = FX.zseries_curve()
    layer_cols = ['#0b3d91', '#2a6fdb', '#19a974', '#f2a900', '#e8590c', '#c2185b']
    methods = [('prod_zz', 'Production — Zigzag (untracked)', '#2f5fb3'),
               ('A', 'Candidate A — plain (untracked)', '#1f9d55'),
               ('A_track', 'Candidate A — tracked (hysteresis, pairs)', '#117a3f'),
               ('B', 'Candidate B — boundary propagation', '#d9822b')]
    win = (95.0, 185.0, 265.0, 225.0)                  # x0, y0, x1, y1 (reference frame)
    base_rings = series[0][2][0]
    d = ''.join(_path(r, True) for r in base_rings)
    cells = []
    for key, label, colour in methods:
        g = [f'<path d="{d}" fill="#f1e8d8" stroke="#b9a687" stroke-width="{BEAD}" stroke-opacity="0.55"/>',
             f'<path d="{d}" fill="none" stroke="#5b4d3a" stroke-width="0.3"/>']
        carried = None
        counts = []
        for li, (k, c0, regions) in enumerate(series):
            if key == 'A_track':
                polys, tracks = [], []
                for ri, rings in enumerate(regions):
                    p_, rep = TA.plan(rings, theta=R.THETA, track=carried[ri] if carried else None)
                    polys += p_
                    tracks.append(rep['track'])
                carried = tracks
            else:
                polys, _, _ = R.run_method(key, regions)
            back = [[q.__class__(c0.x + (q.x - c0.x) / k, c0.y + (q.y - c0.y) / k) for q in p] for p in polys]
            cont = [q.__class__(c0.x + (q.x - c0.x) / k, c0.y + (q.y - c0.y) / k)
                    for q in R.contacts_only(regions, polys)]
            counts.append(len(cont))
            col = layer_cols[li]
            for p in back:
                g.append(f'<path d="{_path(p)}" fill="none" stroke="{col}" stroke-width="0.45" stroke-opacity="0.85"/>')
            for q in cont:
                g.append(f'<circle cx="{q.x:.2f}" cy="{q.y:.2f}" r="0.9" fill="{col}"/>')
        clip = f'clip{key}'
        group = (f'<defs><clipPath id="{clip}"><rect x="{win[0]}" y="{win[1]}" width="{win[2] - win[0]}" '
                 f'height="{win[3] - win[1]}"/></clipPath></defs><g clip-path="url(#{clip})">{"".join(g)}</g>')
        cells.append((f'{label} | contacts per layer: {counts}', colour, group))
    return _sheet(name, 'Cross-Z: 6 layers overlaid (curved wall, scale 1.00 → 1.10)', cells, win,
                  'Layers 0–5 (scale 1.00, 1.02 … 1.10): dark blue → blue → green → yellow → orange → magenta',
                  note='Mapped back to the reference frame; thickness physical. Coincident dots = contact kept its place; '
                       'colour spread = re-phasing. Window: middle 170 in.')


def main():
    os.makedirs(OUT, exist_ok=True)
    made = [geometry_sheet(n, c) for n, c in SHEETS]
    made.append(cross_z_sheet())
    for p in made:
        print(p)


if __name__ == '__main__':
    main()
