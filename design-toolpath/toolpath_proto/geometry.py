from __future__ import annotations
from dataclasses import dataclass, field
from typing import NamedTuple
import math


class Vec2(NamedTuple):
    x: float
    y: float

    def dist(self, other: Vec2) -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def lerp(self, other: Vec2, t: float) -> Vec2:
        return Vec2(self.x + (other.x - self.x) * t,
                    self.y + (other.y - self.y) * t)


class Role:
    OUTER = 'outer'
    INNER = 'inner'
    LATTICE = 'lattice'
    FREE = 'free'


@dataclass
class Strand:
    id: str
    role: str
    points: list[Vec2]
    closed: bool
    # Relative cost of RETRACING (printing again) this strand when the
    # router must pair odd junctions. Visible wall faces are dearer than
    # internal geometry, so continuity transitions hide inside the wall.
    retrace_cost: float = 1.0
    # What the strand IS, for junction rules:
    #   'face'     visible / structural wall geometry (default)
    #   'internal' hidden wall geometry (centre lines, intermediate walls)
    #   'field'    hidden infill / lattice / return paths — a flexible
    #              routing field. Where a field strand CROSSES another
    #              hidden strand it is a structural crossing, not a routing
    #              junction (the nozzle passes straight over it).
    kind: str = 'face'
    # Odd ends of this strand may be paired by a short TRAVEL instead of a
    # retrace when that is shorter (area infill: a hop is better than
    # printing the perimeter twice). Default: retrace as before.
    travel_pairing: bool = False

    def segments(self) -> list[tuple[Vec2, Vec2]]:
        pts = self.points
        segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        if self.closed and len(pts) > 1:
            segs.append((pts[-1], pts[0]))
        return segs

    def length(self) -> float:
        return sum(a.dist(b) for a, b in self.segments())

    @property
    def start(self) -> Vec2:
        return self.points[0]

    @property
    def end(self) -> Vec2:
        return self.points[-1]

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'role': self.role,
            'points': [[p.x, p.y] for p in self.points],
            'closed': self.closed,
        }


@dataclass
class Layer:
    z_height: float
    strands: list[Strand] = field(default_factory=list)
    label: str = ''

    def to_dict(self) -> dict:
        return {
            'z_height': self.z_height,
            'label': self.label,
            'strands': [s.to_dict() for s in self.strands],
        }


@dataclass
class PrintMove:
    kind: str           # 'print' | 'travel' | 'retrace'
    strand_id: str | None
    seg_idx: int | None
    start: Vec2
    end: Vec2

    @property
    def length(self) -> float:
        return self.start.dist(self.end)

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'strand_id': self.strand_id,
            'seg_idx': self.seg_idx,
            'start': [self.start.x, self.start.y],
            'end': [self.end.x, self.end.y],
            'length': round(self.length, 3),
        }
