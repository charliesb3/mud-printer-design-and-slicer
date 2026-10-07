"""
Preview data for the (future) 3D view: every layer instance with its Z and
the resolved 2D printable geometry of its Layer Design. Each design's
geometry is fetched ONCE however many layers use it.

isometric_svg() is a deliberately simple prototype view (an isometric
projection of the stacked centrelines, coloured by design) to validate the
Z structure — not the eventual 3D preview.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PreviewLayer:
    index: int
    design_id: str
    z_bottom: float
    z_top: float
    polylines: list          # shared with every layer printing the same geometry (design or
                             # its SEMANTIC variant for this layer's transform groups)
    transform: object = None  # InstanceTransform placing them

    def placed(self):
        """The layer's polylines placed by its transform: [[(x, y)…]]."""
        t = self.transform
        return [[t.apply(x, y) if t else (x, y) for x, y in pl['pts']] for pl in self.polylines]


def build_stack(resolved, source, groups=None) -> list:
    """Every layer with its geometry: a grouped layer gets its design's
    SEMANTIC variant (source.geometry(design, transforms)), fetched once per
    distinct variant; any other layer its design's geometry, once per design."""
    geom = {}
    out = []
    for inst in resolved.instances:
        k = inst.geometry_key
        if k not in geom:
            geom[k] = (source.geometry(inst.design_id, inst.transforms()) if inst.semantic
                       else source.geometry(inst.design_id)).polylines
        out.append(PreviewLayer(inst.index, inst.design_id, inst.z_bottom, inst.z_top,
                                geom[k], inst.transform))
    return out


PALETTE = ['#4a9eff', '#ff9f43', '#2ecc71', '#e056fd', '#f9ca24', '#ff6b6b']


def isometric_svg(stack, names=None, scale=1.4, every=1) -> str:
    """Isometric stack of the layers' printable centrelines (z up)."""
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))

    def proj(x, y, z):
        return ((x - y) * c * scale, -((x + y) * s + z) * scale)   # z up on screen

    designs = list(dict.fromkeys(L.design_id for L in stack))
    colour = {d: PALETTE[i % len(PALETTE)] for i, d in enumerate(designs)}
    paths, xs, ys = [], [], []
    for L in stack:
        if L.index % every:
            continue
        for pl, placed in zip(L.polylines, L.placed()):
            pts = [proj(x, y, L.z_top) for x, y in placed]
            if pl.get('closed') and pts:
                pts.append(pts[0])
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
            d = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
            paths.append(f'<polyline points="{d}" fill="none" stroke="{colour[L.design_id]}" '
                         f'stroke-width="0.6" stroke-opacity="0.8"/>')
    if not xs:
        return '<svg xmlns="http://www.w3.org/2000/svg"/>'
    pad = 20
    x0, y0, x1, y1 = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    legend = ''.join(
        f'<text x="{x0 + 8}" y="{y0 + 16 + 14 * i}" fill="{colour[d]}" font-size="11" '
        f'font-family="monospace">■ {(names or {}).get(d, d)}</text>' for i, d in enumerate(designs))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.0f} {y0:.0f} {x1 - x0:.0f} {y1 - y0:.0f}" '
            f'style="background:#1a1a1a">{"".join(paths)}{legend}</svg>')
