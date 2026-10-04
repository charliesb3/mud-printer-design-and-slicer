"""
Design canvas data model — effective-print-geometry pipeline.

Pipeline:
    SOURCE DESIGN GEOMETRY (Path, primitive subtypes)
        ↓  treatments / generators
    EFFECTIVE PRINT GEOMETRY (Strand list fed to routing engine)
        ↓  routing
    TOOLPATH (ordered PrintMove list)

Source geometry is never mutated by treatments.
"""
from __future__ import annotations
import math
import uuid
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Vec2
# ---------------------------------------------------------------------------

@dataclass
class Vec2:
    x: float
    y: float

    def __iter__(self):
        yield self.x
        yield self.y

    def dist(self, other: Vec2) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def lerp(self, other: Vec2, t: float) -> Vec2:
        return Vec2(self.x + (other.x - self.x) * t,
                    self.y + (other.y - self.y) * t)

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, s: float) -> Vec2:
        return Vec2(self.x * s, self.y * s)

    def __rmul__(self, s: float) -> Vec2:
        return Vec2(self.x * s, self.y * s)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> Vec2:
        n = self.length()
        return Vec2(self.x / n, self.y / n) if n > 1e-12 else Vec2(0.0, 0.0)

    def perpendicular(self) -> Vec2:
        return Vec2(-self.y, self.x)

    def to_tuple(self) -> tuple:
        return (self.x, self.y)


# ---------------------------------------------------------------------------
# PathSection — a range [t_start, t_end] on a parent path
# ---------------------------------------------------------------------------

@dataclass
class PathSection:
    id: str
    path_id: str
    t_start: float  # 0.0–1.0 arc-length parameterization
    t_end: float
    label: str = ''

    def __post_init__(self):
        if not (0.0 <= self.t_start < self.t_end <= 1.0):
            raise ValueError(f'Invalid section range [{self.t_start}, {self.t_end}]')


# ---------------------------------------------------------------------------
# Path — source geometry (base class + parametric subtypes)
# ---------------------------------------------------------------------------

class Path:
    """
    Base class for all source geometry.

    Subclasses represent parametric primitives (Line, Circle, etc.) or
    explicit polylines. All expose sample_points() for rendering and
    to_strand() for feeding the routing engine.
    """
    def __init__(self, id: str = None, label: str = '', closed: bool = False,
                 role: str = 'free', visible: bool = True):
        self.id: str = id or str(uuid.uuid4())[:8]
        self.label: str = label
        self.closed: bool = closed
        self.role: str = role       # 'outer' | 'inner' | 'lattice' | 'free'
        self.visible: bool = visible
        self.sections: list[PathSection] = []

    # -- Override in subclasses --

    def sample_points(self, n: int = 64) -> list[Vec2]:
        raise NotImplementedError

    def point_at(self, t: float) -> Vec2:
        """Arc-length parameterized position; t in [0, 1]."""
        pts = self.sample_points()
        if not pts:
            raise ValueError('Empty path')
        # Build cumulative lengths
        lengths = [0.0]
        for a, b in zip(pts, pts[1:]):
            lengths.append(lengths[-1] + a.dist(b))
        if self.closed:
            lengths.append(lengths[-1] + pts[-1].dist(pts[0]))
        total = lengths[-1]
        if total < 1e-12:
            return pts[0]
        target = t * total
        for i in range(len(lengths) - 1):
            if lengths[i] <= target <= lengths[i + 1]:
                seg_t = (target - lengths[i]) / (lengths[i + 1] - lengths[i])
                a = pts[i] if i < len(pts) else pts[0]
                b = pts[(i + 1) % len(pts)]
                return a.lerp(b, seg_t)
        return pts[-1]

    def arc_length(self) -> float:
        pts = self.sample_points()
        total = sum(a.dist(b) for a, b in zip(pts, pts[1:]))
        if self.closed and pts:
            total += pts[-1].dist(pts[0])
        return total

    def to_strand_points(self) -> list[Vec2]:
        return self.sample_points()

    def to_dict(self) -> dict:
        pts = self.sample_points()
        return {
            'id': self.id,
            'label': self.label,
            'closed': self.closed,
            'role': self.role,
            'visible': self.visible,
            'type': self.__class__.__name__,
            'points': [[p.x, p.y] for p in pts],
        }


# ---------------------------------------------------------------------------
# ExplicitPath — designer-drawn polyline (open or closed)
# ---------------------------------------------------------------------------

class ExplicitPath(Path):
    def __init__(self, points: list[Vec2] = None, **kwargs):
        super().__init__(**kwargs)
        self.points: list[Vec2] = list(points) if points else []

    def sample_points(self, n: int = 64) -> list[Vec2]:
        return list(self.points)

    def insert_point(self, idx: int, pt: Vec2):
        self.points.insert(idx, pt)

    def move_point(self, idx: int, pt: Vec2):
        self.points[idx] = pt

    def remove_point(self, idx: int):
        if len(self.points) > 2:
            self.points.pop(idx)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['control_points'] = [[p.x, p.y] for p in self.points]
        return d


# ---------------------------------------------------------------------------
# Primitives — parametric subtypes (preserve identity)
# ---------------------------------------------------------------------------

class LinePath(Path):
    def __init__(self, start: Vec2, end: Vec2, **kwargs):
        kwargs.setdefault('closed', False)
        super().__init__(**kwargs)
        self.start = start
        self.end = end

    def sample_points(self, n: int = 64) -> list[Vec2]:
        return [self.start, self.end]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['start'] = [self.start.x, self.start.y]
        d['end'] = [self.end.x, self.end.y]
        return d


class CirclePath(Path):
    def __init__(self, cx: float, cy: float, radius: float, **kwargs):
        kwargs.setdefault('closed', True)
        super().__init__(**kwargs)
        self.cx = cx
        self.cy = cy
        self.radius = radius

    def sample_points(self, n: int = 64) -> list[Vec2]:
        pts = []
        for i in range(n):
            angle = 2 * math.pi * i / n
            pts.append(Vec2(self.cx + self.radius * math.cos(angle),
                            self.cy + self.radius * math.sin(angle)))
        return pts

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['cx'] = self.cx
        d['cy'] = self.cy
        d['radius'] = self.radius
        return d


class EllipsePath(Path):
    def __init__(self, cx: float, cy: float, rx: float, ry: float,
                 rotation: float = 0.0, **kwargs):
        kwargs.setdefault('closed', True)
        super().__init__(**kwargs)
        self.cx = cx
        self.cy = cy
        self.rx = rx
        self.ry = ry
        self.rotation = rotation  # radians

    def sample_points(self, n: int = 64) -> list[Vec2]:
        pts = []
        cos_r = math.cos(self.rotation)
        sin_r = math.sin(self.rotation)
        for i in range(n):
            angle = 2 * math.pi * i / n
            lx = self.rx * math.cos(angle)
            ly = self.ry * math.sin(angle)
            pts.append(Vec2(
                self.cx + lx * cos_r - ly * sin_r,
                self.cy + lx * sin_r + ly * cos_r,
            ))
        return pts

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(cx=self.cx, cy=self.cy, rx=self.rx, ry=self.ry,
                 rotation=self.rotation)
        return d


class RectanglePath(Path):
    def __init__(self, x: float, y: float, w: float, h: float, **kwargs):
        kwargs.setdefault('closed', True)
        super().__init__(**kwargs)
        self.x = x
        self.y = y
        self.w = w
        self.h = h

    def sample_points(self, n: int = 64) -> list[Vec2]:
        return [
            Vec2(self.x, self.y),
            Vec2(self.x + self.w, self.y),
            Vec2(self.x + self.w, self.y + self.h),
            Vec2(self.x, self.y + self.h),
        ]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(x=self.x, y=self.y, w=self.w, h=self.h)
        return d


# ---------------------------------------------------------------------------
# OffsetTreatment — derives an offset path from a source path
# ---------------------------------------------------------------------------

@dataclass
class OffsetTreatment:
    id: str
    source_path_id: str
    distance: float      # positive = left of travel direction
    role: str = 'inner'
    label: str = ''

    def generate(self, source: Path) -> 'DerivedPath':
        pts = source.sample_points(128)
        if not pts:
            return DerivedPath([], closed=source.closed,
                               role=self.role, label=self.label,
                               source_id=self.source_path_id,
                               treatment_id=self.id)
        offset_pts = _offset_polyline(pts, self.distance, source.closed)
        return DerivedPath(offset_pts, closed=source.closed,
                           role=self.role, label=self.label,
                           source_id=self.source_path_id,
                           treatment_id=self.id)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'type': 'offset',
            'source_path_id': self.source_path_id,
            'distance': self.distance,
            'role': self.role,
            'label': self.label,
        }


def _offset_polyline(pts: list[Vec2], dist: float, closed: bool) -> list[Vec2]:
    """Offset a polyline by dist (positive = left of travel direction)."""
    n = len(pts)
    if n < 2:
        return list(pts)

    def seg_normal(a: Vec2, b: Vec2) -> Vec2:
        d = (b - a).normalized()
        return Vec2(-d.y, d.x)  # left normal

    # Compute per-segment normals
    seg_normals = [seg_normal(pts[i], pts[i + 1]) for i in range(n - 1)]
    if closed:
        seg_normals.append(seg_normal(pts[-1], pts[0]))

    result = []
    if closed:
        num = n
    else:
        num = n

    for i in range(num):
        if closed:
            n0 = seg_normals[(i - 1) % len(seg_normals)]
            n1 = seg_normals[i % len(seg_normals)]
        else:
            if i == 0:
                n0 = n1 = seg_normals[0]
            elif i == n - 1:
                n0 = n1 = seg_normals[-1]
            else:
                n0 = seg_normals[i - 1]
                n1 = seg_normals[i]
        avg = Vec2(n0.x + n1.x, n0.y + n1.y)
        mag = avg.length()
        if mag < 1e-12:
            normal = n1
        else:
            normal = avg * (1.0 / mag)
            # miter limit
            cos_half = max(n0.x * n1.x + n0.y * n1.y, -1.0)
            cos_half = min(cos_half, 1.0)
            sin_half_sq = (1.0 - cos_half) / 2.0
            if sin_half_sq > 1e-12:
                miter_len = 1.0 / math.sqrt(sin_half_sq)
                if miter_len > 4.0:
                    normal = n1
                else:
                    normal = normal * miter_len
        p = pts[i]
        result.append(Vec2(p.x + normal.x * dist, p.y + normal.y * dist))

    return result


# ---------------------------------------------------------------------------
# DerivedPath — output of a treatment; not a source path
# ---------------------------------------------------------------------------

class DerivedPath(Path):
    """Produced by treatments; not designer-editable source geometry."""
    def __init__(self, points: list[Vec2], source_id: str = '',
                 treatment_id: str = '', **kwargs):
        super().__init__(**kwargs)
        self._points = list(points)
        self.source_id = source_id
        self.treatment_id = treatment_id

    def sample_points(self, n: int = 64) -> list[Vec2]:
        return list(self._points)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['source_id'] = self.source_id
        d['treatment_id'] = self.treatment_id
        return d


# ---------------------------------------------------------------------------
# LatticeGenerator — base class
# ---------------------------------------------------------------------------

@dataclass
class ParameterSpec:
    name: str
    label: str
    default: float
    min: float
    max: float
    step: float = 1.0


class LatticeGenerator:
    name: str = 'base'

    def parameters(self) -> list[ParameterSpec]:
        return []

    def variation_count(self, path_a: Path, path_b: Path, params: dict) -> int:
        return 1

    def generate(self, path_a: Path, path_b: Path,
                 params: dict, variation_index: int = 0) -> list[DerivedPath]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ZigzagGenerator
# ---------------------------------------------------------------------------

class ZigzagGenerator(LatticeGenerator):
    name = 'zigzag'

    def parameters(self) -> list[ParameterSpec]:
        return [
            ParameterSpec('segments', 'Segments', 6, 2, 40, 1),
            ParameterSpec('connect_ends', 'Connect ends', 1, 0, 1, 1),
        ]

    def variation_count(self, path_a: Path, path_b: Path, params: dict) -> int:
        # Two variations: A→B start vs B→A start
        return 2

    def generate(self, path_a: Path, path_b: Path,
                 params: dict, variation_index: int = 0) -> list[DerivedPath]:
        segments = max(2, int(params.get('segments', 6)))
        connect_ends = bool(params.get('connect_ends', True))

        pts_a = _resample(path_a.sample_points(), segments + 1)
        pts_b = _resample(path_b.sample_points(), segments + 1)

        # variation_index 0: start A0→B0→A1→B1…
        # variation_index 1: start B0→A0→B1→A1…
        if variation_index % 2 == 1:
            pts_a, pts_b = pts_b, pts_a

        zigzag_pts = []
        for i in range(segments + 1):
            if i % 2 == 0:
                zigzag_pts.append(pts_a[i])
            else:
                zigzag_pts.append(pts_b[i])

        path = DerivedPath(zigzag_pts, closed=False,
                           role='lattice', label='zigzag',
                           source_id='', treatment_id='')

        result = [path]

        if connect_ends and len(pts_a) > 0 and len(pts_b) > 0:
            # End connector between last zigzag point and the other path endpoint
            if segments % 2 == 0:
                connector = DerivedPath([pts_a[-1], pts_b[-1]],
                                        closed=False, role='lattice',
                                        label='zigzag_end',
                                        source_id='', treatment_id='')
            else:
                connector = DerivedPath([pts_b[-1], pts_a[-1]],
                                        closed=False, role='lattice',
                                        label='zigzag_end',
                                        source_id='', treatment_id='')
            result.append(connector)

        return result


# ---------------------------------------------------------------------------
# WaveGenerator
# ---------------------------------------------------------------------------

class WaveGenerator(LatticeGenerator):
    name = 'wave'

    def parameters(self) -> list[ParameterSpec]:
        return [
            ParameterSpec('amplitude', 'Amplitude', 5.0, 0.5, 50.0, 0.5),
            ParameterSpec('frequency', 'Frequency', 3.0, 0.5, 20.0, 0.5),
            ParameterSpec('phase', 'Phase', 0.0, 0.0, 1.0, 0.05),
            ParameterSpec('samples', 'Samples', 64, 16, 256, 8),
        ]

    def variation_count(self, path_a: Path, path_b: Path, params: dict) -> int:
        return 2  # phase 0 vs phase 0.5

    def generate(self, path_a: Path, path_b: Path,
                 params: dict, variation_index: int = 0) -> list[DerivedPath]:
        amplitude = float(params.get('amplitude', 5.0))
        frequency = float(params.get('frequency', 3.0))
        phase_base = float(params.get('phase', 0.0))
        samples = max(16, int(params.get('samples', 64)))

        phase_offset = 0.5 * (variation_index % 2)
        phase = phase_base + phase_offset

        pts_a = _resample(path_a.sample_points(), samples)
        pts_b = _resample(path_b.sample_points(), samples)

        wave_pts = []
        for i in range(samples):
            t = i / (samples - 1)
            t_wave = math.sin(2 * math.pi * (frequency * t + phase))
            alpha = (t_wave + 1.0) / 2.0  # 0→1
            a = pts_a[i]
            b = pts_b[i]
            wave_pts.append(a.lerp(b, alpha * amplitude / (a.dist(b) + 1e-9)
                                   if a.dist(b) > 1e-9 else 0.5))

        # Proper interpolation: midpoint + perpendicular offset
        wave_pts2 = []
        for i in range(samples):
            t = i / (samples - 1)
            mid = pts_a[i].lerp(pts_b[i], 0.5)
            a_to_b = pts_b[i] - pts_a[i]
            span = a_to_b.length()
            if span < 1e-9:
                wave_pts2.append(mid)
                continue
            perp = a_to_b.normalized().perpendicular()
            t_wave = math.sin(2 * math.pi * (frequency * t + phase))
            offset = t_wave * amplitude
            wave_pts2.append(Vec2(mid.x + perp.x * offset,
                                  mid.y + perp.y * offset))

        return [DerivedPath(wave_pts2, closed=False, role='lattice',
                            label='wave', source_id='', treatment_id='')]


# ---------------------------------------------------------------------------
# LatticeInstance — one generator applied between two source paths
# ---------------------------------------------------------------------------

@dataclass
class LatticeInstance:
    id: str
    generator_name: str
    path_a_id: str
    path_b_id: str
    params: dict
    variation_index: int = 0
    label: str = ''

    def generate(self, path_a: Path, path_b: Path,
                 generator: LatticeGenerator) -> list[DerivedPath]:
        paths = generator.generate(path_a, path_b, self.params,
                                   self.variation_index)
        for p in paths:
            p.source_id = self.id
            p.treatment_id = self.generator_name
        return paths

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'type': 'lattice',
            'generator': self.generator_name,
            'path_a_id': self.path_a_id,
            'path_b_id': self.path_b_id,
            'params': self.params,
            'variation_index': self.variation_index,
            'label': self.label,
        }


# ---------------------------------------------------------------------------
# TraversalConstraints — routing overrides stored as constraints
# ---------------------------------------------------------------------------

@dataclass
class TraversalConstraints:
    start_path_id: Optional[str] = None
    start_t: Optional[float] = None       # position along path, 0–1
    reverse_direction: bool = False
    component_order: Optional[list[int]] = None   # explicit component ordering

    def to_dict(self) -> dict:
        return {
            'start_path_id': self.start_path_id,
            'start_t': self.start_t,
            'reverse_direction': self.reverse_direction,
            'component_order': self.component_order,
        }


# ---------------------------------------------------------------------------
# PrintLayer — assembles effective print geometry
# ---------------------------------------------------------------------------

GENERATORS: dict[str, LatticeGenerator] = {
    'zigzag': ZigzagGenerator(),
    'wave': WaveGenerator(),
}


@dataclass
class PrintLayer:
    """
    Assembles source geometry + treatments into effective print geometry,
    then feeds to the routing engine.
    """
    id: str
    label: str = ''
    source_paths: list[Path] = field(default_factory=list)
    offset_treatments: list[OffsetTreatment] = field(default_factory=list)
    lattice_instances: list[LatticeInstance] = field(default_factory=list)
    constraints: TraversalConstraints = field(default_factory=TraversalConstraints)

    def _path_by_id(self, pid: str) -> Optional[Path]:
        return next((p for p in self.source_paths if p.id == pid), None)

    def effective_paths(self) -> list[Path]:
        """All printable paths: source + derived. Preserves source order."""
        result: list[Path] = []

        # Source paths
        for p in self.source_paths:
            if p.visible:
                result.append(p)

        # Offset-derived paths
        for ot in self.offset_treatments:
            src = self._path_by_id(ot.source_path_id)
            if src is not None:
                result.append(ot.generate(src))

        # Lattice-derived paths
        for li in self.lattice_instances:
            pa = self._path_by_id(li.path_a_id)
            pb = self._path_by_id(li.path_b_id)
            if pa is not None and pb is not None:
                gen = GENERATORS.get(li.generator_name)
                if gen:
                    result.extend(li.generate(pa, pb, gen))

        return result

    def to_routing_layer(self):
        """
        Convert effective paths to the Strand/Layer format expected by the
        routing engine in toolpath_proto/graph.py.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        '..', 'toolpath_proto'))
        from geometry import Strand, Layer as RoutingLayer, Vec2 as RVec2

        strands = []
        for path in self.effective_paths():
            pts = path.sample_points()
            if len(pts) < 2:
                continue
            rvecs = [RVec2(p.x, p.y) for p in pts]
            strands.append(Strand(
                id=path.id,
                role=path.role,
                points=rvecs,
                closed=path.closed,
            ))
        return RoutingLayer(z_height=0.0, strands=strands, label=self.label)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'label': self.label,
            'source_paths': [p.to_dict() for p in self.source_paths],
            'offset_treatments': [ot.to_dict() for ot in self.offset_treatments],
            'lattice_instances': [li.to_dict() for li in self.lattice_instances],
            'constraints': self.constraints.to_dict(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resample(pts: list[Vec2], n: int) -> list[Vec2]:
    """Resample a polyline to exactly n evenly-spaced points by arc length."""
    if not pts or n < 2:
        return list(pts)

    lengths = [0.0]
    for a, b in zip(pts, pts[1:]):
        lengths.append(lengths[-1] + a.dist(b))
    total = lengths[-1]

    if total < 1e-12:
        return [pts[0]] * n

    result = []
    for i in range(n):
        target = total * i / (n - 1)
        for j in range(len(lengths) - 1):
            if lengths[j] <= target <= lengths[j + 1]:
                seg_len = lengths[j + 1] - lengths[j]
                t = (target - lengths[j]) / seg_len if seg_len > 1e-12 else 0.0
                result.append(pts[j].lerp(pts[j + 1], t))
                break
    return result
