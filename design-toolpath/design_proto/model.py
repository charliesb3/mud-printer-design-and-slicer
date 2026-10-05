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


class QuadBezierPath(Path):
    """
    Quadratic Bézier curve.
    B(t) = (1−t)² P0 + 2(1−t)t P1 + t² P2
    P0=start, P1=control (bend), P2=end, t∈[0,1].
    Always open (closed=False by default).
    Sampling is an implementation detail — the parametric identity is preserved.
    """
    def __init__(self, start: Vec2, end: Vec2, control: Vec2, **kwargs):
        kwargs.setdefault('closed', False)
        super().__init__(**kwargs)
        self.start = start    # P0
        self.end = end        # P2
        self.control = control  # P1 (bend)

    def sample_points(self, n: int = 64) -> list[Vec2]:
        """Return n points along the curve.  pts[0]=start, pts[-1]=end exactly."""
        pts = []
        for i in range(n):
            t = i / (n - 1) if n > 1 else 0.0
            mt = 1.0 - t
            x = mt*mt * self.start.x + 2.0*mt*t * self.control.x + t*t * self.end.x
            y = mt*mt * self.start.y + 2.0*mt*t * self.control.y + t*t * self.end.y
            pts.append(Vec2(x, y))
        return pts

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['start']   = [self.start.x,   self.start.y]
        d['end']     = [self.end.x,     self.end.y]
        d['control'] = [self.control.x, self.control.y]
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

    def generate(self, source: Path,
                 corner_radius: float = 0.0) -> 'DerivedPath':
        pts = source.sample_points(128)
        if not pts:
            return DerivedPath([], closed=source.closed,
                               id=self.id,
                               role=self.role, label=self.label,
                               source_id=self.source_path_id,
                               treatment_id=self.id)
        if corner_radius > 0.0 and _eligible_for_rounding(source):
            pts = _apply_corner_rounding(pts, corner_radius, source.closed)
        offset_pts = _offset_polyline(pts, self.distance, source.closed)
        return DerivedPath(offset_pts, closed=source.closed,
                           id=self.id,
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


def _eligible_for_rounding(path: 'Path') -> bool:
    """True for piecewise-linear paths with discrete corners that can be filleted."""
    return isinstance(path, (RectanglePath, ExplicitPath))


def _fillet_vertex(A: Vec2, B: Vec2, C: Vec2, radius: float) -> list[Vec2]:
    """
    Circular fillet arc replacing vertex B between segments A-B and B-C.
    Returns arc points [T1, ..., T2] to substitute for B.
    Returns [] if B is collinear (no fillet needed) or geometry is degenerate.
    """
    d_BA = (A - B).normalized()
    d_BC = (C - B).normalized()
    if d_BA.length() < 1e-12 or d_BC.length() < 1e-12:
        return []
    dot = max(-1.0, min(1.0, d_BA.x * d_BC.x + d_BA.y * d_BC.y))
    theta = math.acos(dot)
    if theta < 1e-6 or (math.pi - theta) < 1e-6:
        return []
    half = theta / 2.0
    tan_half = math.tan(half)
    if abs(tan_half) < 1e-12:
        return []
    t = radius / tan_half
    t = min(t, A.dist(B) / 2.0, B.dist(C) / 2.0)
    if t < 1e-9:
        return []
    actual_r = t * tan_half
    T1 = Vec2(B.x + t * d_BA.x, B.y + t * d_BA.y)
    T2 = Vec2(B.x + t * d_BC.x, B.y + t * d_BC.y)
    bx = d_BA.x + d_BC.x
    by = d_BA.y + d_BC.y
    bis_len = math.hypot(bx, by)
    if bis_len < 1e-12:
        return [T1, T2]
    bx /= bis_len
    by /= bis_len
    dist_c = actual_r / math.sin(half)
    Cx = B.x + dist_c * bx
    Cy = B.y + dist_c * by
    t1x, t1y = T1.x - Cx, T1.y - Cy
    t2x, t2y = T2.x - Cx, T2.y - Cy
    cross_z = t1x * t2y - t1y * t2x
    start_angle = math.atan2(t1y, t1x)
    arc_sweep = math.pi - theta
    if cross_z < 0:
        arc_sweep = -arc_sweep
    n_pts = max(3, int(abs(arc_sweep) * 36.0 / math.pi) + 2)
    result = []
    for i in range(n_pts):
        frac = i / (n_pts - 1) if n_pts > 1 else 0.0
        angle = start_angle + frac * arc_sweep
        result.append(Vec2(Cx + actual_r * math.cos(angle),
                           Cy + actual_r * math.sin(angle)))
    return result


def _apply_corner_rounding(pts: list[Vec2], radius: float,
                            closed: bool) -> list[Vec2]:
    """Replace each interior corner with a circular fillet arc."""
    if radius <= 0 or len(pts) < 3:
        return list(pts)
    n = len(pts)
    result: list[Vec2] = []
    if closed:
        for i in range(n):
            arc = _fillet_vertex(pts[(i - 1) % n], pts[i], pts[(i + 1) % n], radius)
            result.extend(arc if arc else [pts[i]])
    else:
        result.append(pts[0])
        for i in range(1, n - 1):
            arc = _fillet_vertex(pts[i - 1], pts[i], pts[i + 1], radius)
            result.extend(arc if arc else [pts[i]])
        result.append(pts[-1])
    return result


def _semicircle_cap(p0: Vec2, p1: Vec2, outward: Vec2,
                    n: int = 32) -> list[Vec2]:
    """
    Semicircular arc from p0 to p1 that bulges in the outward direction.
    Endpoints p0/p1 are forced to exact values (no floating-point drift).
    """
    Cx = (p0.x + p1.x) / 2.0
    Cy = (p0.y + p1.y) / 2.0
    R = math.hypot(p0.x - Cx, p0.y - Cy)
    if R < 1e-9:
        return [p0, p1]
    start_angle = math.atan2(p0.y - Cy, p0.x - Cx)
    mid_ccw = Vec2(Cx + R * math.cos(start_angle + math.pi / 2),
                   Cy + R * math.sin(start_angle + math.pi / 2))
    mid_cw  = Vec2(Cx + R * math.cos(start_angle - math.pi / 2),
                   Cy + R * math.sin(start_angle - math.pi / 2))
    ccw_dot = (mid_ccw.x - Cx) * outward.x + (mid_ccw.y - Cy) * outward.y
    cw_dot  = (mid_cw.x  - Cx) * outward.x + (mid_cw.y  - Cy) * outward.y
    sign = 1.0 if ccw_dot >= cw_dot else -1.0
    pts = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        angle = start_angle + sign * math.pi * t
        pts.append(Vec2(Cx + R * math.cos(angle), Cy + R * math.sin(angle)))
    pts[0] = p0
    pts[-1] = p1
    return pts


def _offset_polyline(pts: list[Vec2], dist: float, closed: bool) -> list[Vec2]:
    """
    True 2D polyline offset: shift each segment parallel by dist, then
    intersect adjacent offset segments to find each vertex (miter join).
    Falls back to bevel (two points per corner) when miter extension
    exceeds MITER_LIMIT * abs(dist).
    Positive dist = left of travel direction.
    """
    n = len(pts)
    if n < 2:
        return list(pts)

    MITER_LIMIT = 4.0

    def _seg_offset(a: Vec2, b: Vec2) -> tuple:
        d = (b - a).normalized()
        nx, ny = -d.y * dist, d.x * dist
        return Vec2(a.x + nx, a.y + ny), Vec2(b.x + nx, b.y + ny)

    def _line_intersect(p1: Vec2, d1: Vec2, p2: Vec2, d2: Vec2):
        cross = d1.x * d2.y - d1.y * d2.x
        if abs(cross) < 1e-12:
            return None
        t = ((p2.x - p1.x) * d2.y - (p2.y - p1.y) * d2.x) / cross
        return Vec2(p1.x + t * d1.x, p1.y + t * d1.y)

    def _join(prev_seg, curr_seg, result_list):
        """Append miter or bevel join vertex/vertices between two offset segments."""
        ob0 = prev_seg[1]
        oa1 = curr_seg[0]
        d0 = (prev_seg[1] - prev_seg[0]).normalized()
        d1 = (curr_seg[1] - curr_seg[0]).normalized()
        pt = _line_intersect(prev_seg[0], d0, curr_seg[0], d1)
        if pt is None:
            result_list.append(ob0)
        else:
            miter_len = ob0.dist(pt)
            if abs(dist) > 1e-12 and miter_len > MITER_LIMIT * abs(dist):
                result_list.append(ob0)
                result_list.append(oa1)
            else:
                result_list.append(pt)

    result: list[Vec2] = []

    if closed:
        offset_segs = [_seg_offset(pts[i], pts[(i + 1) % n]) for i in range(n)]
        for i in range(n):
            _join(offset_segs[(i - 1) % n], offset_segs[i], result)
    else:
        offset_segs = [_seg_offset(pts[i], pts[i + 1]) for i in range(n - 1)]
        result.append(offset_segs[0][0])
        for i in range(1, n - 1):
            _join(offset_segs[i - 1], offset_segs[i], result)
        result.append(offset_segs[-1][1])

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
# Geometry validity helpers
# ---------------------------------------------------------------------------

def _polygon_area(pts: list[Vec2]) -> float:
    """Shoelace formula — signed area. Positive = CCW winding."""
    n = len(pts)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i].x * pts[j].y
        area -= pts[j].x * pts[i].y
    return area / 2.0


def _point_in_polygon(pt: Vec2, poly: list[Vec2]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i].x, poly[i].y
        xj, yj = poly[j].x, poly[j].y
        if ((yi > pt.y) != (yj > pt.y)) and \
                (pt.x < (xj - xi) * (pt.y - yi) / (yj - yi + 1e-18) + xi):
            inside = not inside
        j = i
    return inside


def _segments_intersect(p1: Vec2, p2: Vec2, p3: Vec2, p4: Vec2) -> bool:
    """True if segment p1-p2 strictly crosses segment p3-p4 (excludes shared endpoints)."""
    d1 = p2 - p1
    d2 = p4 - p3
    cross = d1.x * d2.y - d1.y * d2.x
    if abs(cross) < 1e-12:
        return False
    t = ((p3.x - p1.x) * d2.y - (p3.y - p1.y) * d2.x) / cross
    u = ((p3.x - p1.x) * d1.y - (p3.y - p1.y) * d1.x) / cross
    # Strictly interior to both segments (excludes endpoints)
    return 1e-6 < t < 1.0 - 1e-6 and 1e-6 < u < 1.0 - 1e-6


def _segment_crosses_polyline(a: Vec2, b: Vec2,
                               poly: list[Vec2], closed: bool) -> bool:
    """True if segment a-b strictly crosses any edge of poly."""
    n = len(poly)
    for i in range(n - 1):
        if _segments_intersect(a, b, poly[i], poly[i + 1]):
            return True
    if closed and n >= 2:
        if _segments_intersect(a, b, poly[-1], poly[0]):
            return True
    return False


def _lattice_valid_in_cavity(derived_paths: list['DerivedPath'],
                              path_a: Path, path_b: Path) -> bool:
    """
    Check every lattice segment lies inside the wall cavity (between path_a and
    path_b). Returns True if all segments are geometrically valid.

    Only applies when both boundaries are closed. For open boundaries, returns
    True unconditionally (no cavity to validate against).
    """
    if not (path_a.closed and path_b.closed):
        return True

    pts_a = path_a.sample_points(128)
    pts_b = path_b.sample_points(128)

    # Determine which is outer (larger area)
    area_a = abs(_polygon_area(pts_a))
    area_b = abs(_polygon_area(pts_b))
    if area_a >= area_b:
        outer_pts, inner_pts = pts_a, pts_b
    else:
        outer_pts, inner_pts = pts_b, pts_a

    for dp in derived_paths:
        seg_pts = dp.sample_points()
        n = len(seg_pts)
        for i in range(n - 1):
            a, b = seg_pts[i], seg_pts[i + 1]
            # Midpoint must be inside outer AND outside inner
            mid = a.lerp(b, 0.5)
            if not _point_in_polygon(mid, outer_pts):
                return False
            if _point_in_polygon(mid, inner_pts):
                return False
            # Segment must not cross either boundary
            if _segment_crosses_polyline(a, b, outer_pts, True):
                return False
            if _segment_crosses_polyline(a, b, inner_pts, True):
                return False
    return True


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
        return 2

    def _generate_at_count(self, path_a: Path, path_b: Path,
                           segments: int, connect_ends: bool,
                           variation_index: int) -> list[DerivedPath]:
        """Generate zigzag at a specific segment count."""
        # For closed paths include the closing segment so all sides are covered
        pts_a = _resample(path_a.sample_points(), segments + 1,
                          closed=path_a.closed)
        pts_b = _resample(path_b.sample_points(), segments + 1,
                          closed=path_b.closed)

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

        if connect_ends and pts_a and pts_b:
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

    def generate(self, path_a: Path, path_b: Path,
                 params: dict, variation_index: int = 0) -> list[DerivedPath]:
        segments_req = max(2, int(params.get('segments', 6)))
        connect_ends = bool(params.get('connect_ends', True))

        do_validate = path_a.closed and path_b.closed

        result = self._generate_at_count(
            path_a, path_b, segments_req, connect_ends, variation_index)

        if not do_validate:
            return result

        # Auto-increase until all segments are geometrically valid in the cavity
        actual = segments_req
        MAX_SEGMENTS = 80
        while actual <= MAX_SEGMENTS:
            if _lattice_valid_in_cavity(result, path_a, path_b):
                break
            actual += 2
            result = self._generate_at_count(
                path_a, path_b, actual, connect_ends, variation_index)

        # Record the actual count in the label
        for dp in result:
            if dp.label in ('zigzag', 'zigzag_end'):
                dp.label = f'zigzag_n{actual}'

        return result


# ---------------------------------------------------------------------------
# WaveGenerator — oscillates between boundary A and boundary B
# ---------------------------------------------------------------------------

class WaveGenerator(LatticeGenerator):
    """
    Wave lattice: smoothly oscillates from boundary A → B → A → repeat.
    Wall spacing = transverse amplitude (not user-controlled).
    alpha(s) = 0.5 * (1 - cos(2π * cycles * s + phase_offset))
    alpha=0 → on A, alpha=1 → on B.

    For closed boundaries a seam bridge is added from the wave end to the
    other boundary's first node. This creates exactly two odd-degree nodes
    in the routing graph → Eulerian path → zero travel moves.
    """
    name = 'wave'

    def parameters(self) -> list[ParameterSpec]:
        return [
            ParameterSpec('cycles', 'Cycles', 3.0, 0.5, 20.0, 0.5),
        ]

    def variation_count(self, path_a: Path, path_b: Path, params: dict) -> int:
        return 2  # V1: start on A  |  V2: start on B (half-cycle offset)

    def generate(self, path_a: Path, path_b: Path,
                 params: dict, variation_index: int = 0) -> list[DerivedPath]:
        cycles = max(0.5, float(params.get('cycles', 3.0)))

        # V2 shifts by half a cycle so the wave starts on B instead of A
        phase_total = 0.5 * (variation_index % 2)

        # Choose sample count from cycles (~32 pts/cycle, min 64)
        samples = max(64, int(cycles * 32))

        pts_a = _resample(path_a.sample_points(), samples,
                          closed=path_a.closed)
        pts_b = _resample(path_b.sample_points(), samples,
                          closed=path_b.closed)

        wave_pts = []
        for i in range(samples):
            # Normalized arc-length position along the perimeter
            if path_a.closed:
                t = i / samples
            else:
                t = i / (samples - 1) if samples > 1 else 0.0
            # alpha ∈ [0,1]: 0 = on A, 1 = on B
            alpha = 0.5 * (1.0 - math.cos(
                2.0 * math.pi * (cycles * t + phase_total)))
            wave_pts.append(pts_a[i].lerp(pts_b[i], alpha))

        result = [DerivedPath(wave_pts, closed=False, role='lattice',
                              label='wave', source_id='', treatment_id='')]

        # Seam bridge for closed boundaries: connects wave end to the other
        # boundary's first node. This creates exactly two odd-degree nodes
        # (the touched node on each boundary) → Eulerian path → zero travel.
        if path_a.closed and path_b.closed:
            if variation_index % 2 == 0:
                # V1 starts on A (wave_pts[0] = pts_a[0]); bridge to B[0]
                seam_end = path_b.sample_points()[0]
            else:
                # V2 starts on B (wave_pts[0] = pts_b[0]); bridge to A[0]
                seam_end = path_a.sample_points()[0]
            result.append(DerivedPath(
                [wave_pts[-1], seam_end],
                closed=False, role='lattice', label='wave_seam',
                source_id='', treatment_id='',
            ))

        return result


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
    corner_radius: float = 0.0    # global fillet radius; 0 = sharp corners
    cap_style: str = 'flat'       # 'flat' | 'round' — open double-wall end caps

    def _path_by_id(self, pid: str) -> Optional[Path]:
        return next((p for p in self.source_paths if p.id == pid), None)

    def _rounded_pts(self, path: Path) -> list[Vec2]:
        """Sample path and apply corner rounding if eligible and radius > 0."""
        pts = path.sample_points()
        if self.corner_radius > 0.0 and _eligible_for_rounding(path):
            pts = _apply_corner_rounding(pts, self.corner_radius, path.closed)
        return pts

    def effective_paths(self) -> list[Path]:
        """All printable paths: source + derived. Preserves source order."""
        result: list[Path] = []

        # Source paths — eligible ones wrapped in a DerivedPath with rounded geometry
        # (same ID so lattice lookups and routing work; filtered from derivedPaths in JS)
        for p in self.source_paths:
            if not p.visible:
                continue
            if self.corner_radius > 0.0 and _eligible_for_rounding(p):
                raw = p.sample_points()
                rounded = _apply_corner_rounding(raw, self.corner_radius, p.closed)
                result.append(DerivedPath(rounded, closed=p.closed,
                                          id=p.id, role=p.role, label=p.label,
                                          source_id=p.id, treatment_id='round'))
            else:
                result.append(p)

        # Offset-derived paths + end caps for open sources
        for ot in self.offset_treatments:
            src = self._path_by_id(ot.source_path_id)
            if src is not None:
                derived = ot.generate(src, self.corner_radius)
                result.append(derived)
                if not src.closed:
                    # Effective (possibly rounded) source pts for exact cap endpoints
                    src_pts = self._rounded_pts(src)
                    der_pts = derived.sample_points()
                    if src_pts and der_pts:
                        if self.cap_style == 'round':
                            st = ((src_pts[1] - src_pts[0]).normalized()
                                  if len(src_pts) > 1 else Vec2(1, 0))
                            et = ((src_pts[-1] - src_pts[-2]).normalized()
                                  if len(src_pts) > 1 else Vec2(1, 0))
                            cs_pts = _semicircle_cap(
                                src_pts[0], der_pts[0], Vec2(-st.x, -st.y))
                            ce_pts = _semicircle_cap(
                                src_pts[-1], der_pts[-1], et)
                        else:
                            cs_pts = [src_pts[0], der_pts[0]]
                            ce_pts = [src_pts[-1], der_pts[-1]]
                        result.append(DerivedPath(
                            cs_pts, closed=False, role='cap', label='cap_start',
                            source_id=ot.id, treatment_id=ot.id,
                            id=ot.id + '_cs',
                        ))
                        result.append(DerivedPath(
                            ce_pts, closed=False, role='cap', label='cap_end',
                            source_id=ot.id, treatment_id=ot.id,
                            id=ot.id + '_ce',
                        ))

        # Lattice-derived paths — can reference source paths OR offset-derived paths
        all_by_id = {p.id: p for p in result}
        for li in self.lattice_instances:
            pa = all_by_id.get(li.path_a_id)
            pb = all_by_id.get(li.path_b_id)
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
            'corner_radius': self.corner_radius,
            'cap_style': self.cap_style,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resample(pts: list[Vec2], n: int, closed: bool = False) -> list[Vec2]:
    """
    Resample a polyline to exactly n evenly-spaced points by arc length.

    If closed=True, the closing segment (last → first) is included in the
    total arc length. This ensures closed-path resampling covers the full
    perimeter including the final edge back to the start point.
    The n-th sample (at t=1.0) equals the first sample (t=0), so for closed
    paths use n=segments+1 to get segments+1 points including the wrap-back.
    """
    if not pts or n < 2:
        return list(pts)

    # For closed paths, append the closing point so the final segment is included
    working = list(pts)
    if closed and len(pts) >= 2:
        working = working + [pts[0]]

    lengths = [0.0]
    for a, b in zip(working, working[1:]):
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
                result.append(working[j].lerp(working[j + 1], t))
                break
        else:
            result.append(working[-1])

    return result
