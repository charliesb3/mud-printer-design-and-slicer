"""
ASSEMBLY OBJECTS — physical, NON-PRINTED things inserted into the assembled
structure. They belong to the Assembly, never to a Layer Design. A HEADER
(lintel) is the first: a rectangular solid that SUPPORTS mud printed on it.

Every object is anchored at a LAYER BOUNDARY: (section, layer) names the
first layer that rests on it — global layer i = section.first_layer + layer —
and its plan geometry is written in that layer's LOCAL (design)
coordinates, so the layer's resolved InstanceTransform places it (it follows
section and assembly-wide transforms exactly like the mud it supports).

Support contract (support.py): an object exposes its TOP footprint at the
boundary (`support_rect`); support analysis treats it exactly like mud of
the layer below. New object kinds plug in the same way (object_from_dict).

HEADER, V1 conventions:
  * plan: centre (x, y) and direction angle (deg) of the span, `span` = the
    clear opening along that direction (local units — it scales with the
    layer). Physical LENGTH = span · scale + 2 · bearing.
  * bearing, depth, thickness are PHYSICAL inches (never scaled).
  * VERTICAL: the header's TOP is at its boundary (z_bottom of the first
    layer resting on it) and it hangs DOWN `thickness` into the top of the
    opening below. The global layer Z never changes. Where the header's ends
    (the bearings) pass through wall layers below, those layers would need a
    pocket — the Assembly reports them, it does not cut them.
  * `snap`: re-derive centre / angle / span from the detected unsupported
    span at its boundary on every resolve (so it stays on the opening when
    layer height or transforms change); the stored values are the fallback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict


@dataclass
class Header:
    id: str
    section: int                 # anchor: first layer resting on the header …
    layer: int = 0               # … = this section's local layer
    x: float = 0.0               # plan centre (local / design coordinates)
    y: float = 0.0
    angle: float = 0.0           # span direction (deg)
    span: float = 0.0            # clear opening along the span (local units)
    bearing: float = 8.0         # into supported material, EACH side (in)
    depth: float = 10.0          # across the wall (in)
    thickness: float = 6.0       # vertical (in)
    snap: bool = True
    kind: str = 'header'

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> 'Header':
        f = {k: d[k] for k in ('id', 'section', 'layer', 'x', 'y', 'angle', 'span', 'bearing',
                               'depth', 'thickness', 'snap') if k in d}
        h = Header(**f)
        h.section, h.layer = int(h.section), int(h.layer)
        for k in ('x', 'y', 'angle', 'span', 'bearing', 'depth', 'thickness'):
            setattr(h, k, float(getattr(h, k)))
        return h

    def length(self, scale: float = 1.0) -> float:
        return self.span * scale + 2 * self.bearing

    def support_rect(self, transform) -> list:
        """The four plan corners of the header (physical), placed by the
        anchor layer's transform."""
        cx, cy = transform.apply(self.x, self.y)
        a = math.radians(self.angle)
        ux, uy = math.cos(a), math.sin(a)
        hl, hd = self.length(transform.scale) / 2, self.depth / 2
        return [(cx + sx * hl * ux - sy * hd * uy, cy + sx * hl * uy + sy * hd * ux)
                for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


@dataclass
class SupportOverride:
    """An unsupported-span warning the designer chose to IGNORE. It stays
    recorded (reported as 'overridden', never as supported)."""
    section: int
    layer: int
    x: float                     # the span's centre (local coordinates)
    y: float
    note: str = ''

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> 'SupportOverride':
        return SupportOverride(int(d['section']), int(d.get('layer', 0)), float(d['x']),
                               float(d['y']), d.get('note', ''))


KINDS = {'header': Header}


def object_from_dict(d: dict):
    kind = d.get('kind', 'header')
    if kind not in KINDS:
        raise ValueError(f'unknown assembly object kind {kind!r}')
    return KINDS[kind].from_dict(d)


def point_in_rect(p, rect) -> bool:
    """p inside the convex quad `rect` (corners in order)."""
    sign = 0
    for (ax, ay), (bx, by) in zip(rect, rect[1:] + rect[:1]):
        c = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
        if c != 0:
            if sign == 0:
                sign = 1 if c > 0 else -1
            elif (c > 0) != (sign > 0):
                return False
    return True
