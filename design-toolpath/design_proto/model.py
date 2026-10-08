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
import json
import math
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Optional

from material import MaterialSpec

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
                 role: str = 'free', visible: bool = True,
                 corner_radius: Optional[float] = None):
        self.id: str = id or str(uuid.uuid4())[:8]
        self.label: str = label
        self.closed: bool = closed
        self.role: str = role       # 'outer' | 'inner' | 'lattice' | 'free'
        self.visible: bool = visible
        self.sections: list[PathSection] = []
        # This path's OWN corner rounding (fillets its original corners —
        # never the corners the wall network creates at junctions).
        # None = fall back to the legacy layer-wide PrintLayer.corner_radius.
        self.corner_radius: Optional[float] = corner_radius
        # The wall this path stands for (thickness + alignment): generates
        # its offsets parametrically. None = no own wall (a network-level
        # NetworkWall may supply one).
        self.wall: Optional['WallSpec'] = None

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
            'corner_radius': self.corner_radius,
            'wall': self.wall.to_dict() if self.wall else None,
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
    """Axis-aligned x, y, w, h, then rotated by `rotation` (radians) about
    its centre — still a parametric rectangle (identity preserved)."""
    def __init__(self, x: float, y: float, w: float, h: float,
                 rotation: float = 0.0, **kwargs):
        kwargs.setdefault('closed', True)
        super().__init__(**kwargs)
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.rotation = rotation

    def sample_points(self, n: int = 64) -> list[Vec2]:
        pts = [
            Vec2(self.x, self.y),
            Vec2(self.x + self.w, self.y),
            Vec2(self.x + self.w, self.y + self.h),
            Vec2(self.x, self.y + self.h),
        ]
        if not self.rotation:
            return pts
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        c, s = math.cos(self.rotation), math.sin(self.rotation)
        return [Vec2(cx + (p.x - cx) * c - (p.y - cy) * s,
                     cy + (p.x - cx) * s + (p.y - cy) * c) for p in pts]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(x=self.x, y=self.y, w=self.w, h=self.h, rotation=self.rotation)
        return d


@dataclass
class WallRelation:
    """
    WALL / REGION SEMANTICS: "these two nested closed boundaries define a
    wall `thickness` thick". One boundary DRIVES (default the outer), the
    other is DEPENDENT and is recomputed from it on every evaluation, so
    resizing / moving / rotating the driver keeps the wall thickness.

    Supported pairs (same type), with what "thickness" means:
      rectangle ↔ rectangle   every side `thickness` apart; same centre and
                              rotation; corners keep their style (sharp ↔
                              sharp, rounded R ↔ R ± thickness)
      circle ↔ circle         concentric, radius ± thickness (exact)
      ellipse ↔ ellipse       concentric, same rotation, rx / ry ± thickness
                              — exact on the axes; between them the gap of
                              two ellipses varies (reported as `spread`)
    Anything else is refused (status 'unsupported') — no silent distortion.
    General shapes: use Inset / Outset (design geometry operation).
    """
    id: str
    outer_id: str
    inner_id: str
    thickness: float = 10.0
    driver: str = 'outer'            # 'outer' | 'inner'

    SUPPORTED = ('RectanglePath', 'CirclePath', 'EllipsePath')

    def to_dict(self) -> dict:
        return {'id': self.id, 'outer_id': self.outer_id, 'inner_id': self.inner_id,
                'thickness': self.thickness, 'driver': self.driver}

    def apply(self, by_id) -> dict:
        """Update the dependent boundary in place. Returns a status dict."""
        o, i = by_id.get(self.outer_id), by_id.get(self.inner_id)
        if o is None or i is None:
            return {'status': 'missing boundary'}
        kind = type(o).__name__
        if kind != type(i).__name__ or kind not in self.SUPPORTED:
            return {'status': 'unsupported'}
        t = max(0.0, float(self.thickness))
        drv, dep = (o, i) if self.driver == 'outer' else (i, o)
        sgn = -1.0 if self.driver == 'outer' else 1.0      # dependent = driver ∓ t
        if kind == 'RectanglePath':
            w, h = drv.w + 2 * sgn * t, drv.h + 2 * sgn * t
            if w <= 0 or h <= 0:
                return {'status': 'thickness too large'}
            cx, cy = drv.x + drv.w / 2, drv.y + drv.h / 2
            dep.w, dep.h = w, h
            dep.x, dep.y = cx - w / 2, cy - h / 2
            dep.rotation = getattr(drv, 'rotation', 0.0)
            r = getattr(drv, 'corner_radius', None) or 0.0
            dep.corner_radius = max(0.0, r + sgn * t) if r > 0 else 0.0
            return {'status': 'ok', 'spread': 0.0}
        if kind == 'CirclePath':
            rad = drv.radius + sgn * t
            if rad <= 0:
                return {'status': 'thickness too large'}
            dep.cx, dep.cy = drv.cx, drv.cy
            dep.radius = rad
            return {'status': 'ok', 'spread': 0.0}
        rx, ry = drv.rx + sgn * t, drv.ry + sgn * t
        if rx <= 0 or ry <= 0:
            return {'status': 'thickness too large'}
        dep.cx, dep.cy = drv.cx, drv.cy
        dep.rotation = getattr(drv, 'rotation', 0.0)
        dep.rx, dep.ry = rx, ry
        # actual gap between the two ellipses (normal distance), for honesty
        out_pts = o.sample_points(96)
        in_pts = i.sample_points(96)
        gaps = [min(p.dist(q) for q in in_pts) for p in out_pts]
        return {'status': 'ok', 'spread': round(max(gaps) - min(gaps), 3),
                'min_gap': round(min(gaps), 3), 'max_gap': round(max(gaps), 3)}


class InsetPath(Path):
    """
    PARAMETRIC DESIGN GEOMETRY: a closed path kept `distance` inside
    (mode 'inset') or outside ('outset') its closed parent path. It is a
    real source path — it can be a wall, an infill boundary, a void, … —
    but its shape is derived: recomputed from the parent's processed
    polyline (offset + trim, the same machinery as wall offsets) every
    time the layer is evaluated, so moving / resizing the parent or
    changing the distance updates it. Not to be confused with an Extra
    Offset (an additional printed bead) or a Wall Thickness (wall faces).

    `points` holds the last evaluated shape (set by PrintLayer); with the
    parent gone the child is evaluated from these frozen points.
    """
    def __init__(self, parent_id: str, distance: float = 10.0,
                 mode: str = 'inset', points: list = None, **kwargs):
        kwargs['closed'] = True
        super().__init__(**kwargs)
        self.parent_id = parent_id
        self.distance = distance
        self.mode = mode
        self.points: list[Vec2] = list(points or [])

    def sample_points(self, n: int = 64) -> list[Vec2]:
        return list(self.points)

    def derive(self, parent_pts: list[Vec2]) -> list[Vec2]:
        pts = _dedupe_polyline(parent_pts, True)
        if len(pts) < 3 or self.distance <= 0:
            return []
        ccw = _polygon_area(pts) > 0
        inward = 1.0 if ccw else -1.0              # + = left of travel
        sign = inward if self.mode == 'inset' else -inward
        raw = _offset_polyline(pts, sign * self.distance, True)
        return _trim_offset(raw, pts, sign * self.distance, True)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(parent_id=self.parent_id, distance=self.distance, mode=self.mode)
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

    def generate(self, processed_pts: list[Vec2],
                 closed: bool) -> 'DerivedPath':
        """
        Pure offset: callers pass the already-processed source polyline
        (sampled + rounded if applicable) and we produce the parallel offset.
        Layer-level concerns (rounding, cap style) stay in PrintLayer.
        """
        if not processed_pts:
            return DerivedPath([], closed=closed,
                               id=self.id,
                               role=self.role, label=self.label,
                               source_id=self.source_path_id,
                               treatment_id=self.id)
        processed_pts = _dedupe_polyline(processed_pts, closed)
        offset_pts = _offset_polyline(processed_pts, self.distance, closed)
        offset_pts = _trim_offset(offset_pts, processed_pts,
                                  self.distance, closed)
        return DerivedPath(offset_pts, closed=closed,
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

    Always miters — no bevel fallback. A bevel at an acute corner
    silently reduces the perpendicular wall spacing below the requested
    |dist|, which this project treats as a hard geometric invariant.
    If miters produce long spikes at acute corners that is a signal to
    the designer, not a reason to thin the wall.

    This is the RAW offset: where |dist| exceeds the local feature size
    it contains swallowtail loops. _trim_offset, which the
    OffsetTreatment pipeline calls on this result, removes them.

    Positive dist = left of travel direction.
    """
    n = len(pts)
    if n < 2:
        return list(pts)

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
        """Append the mitered join vertex between two offset segments."""
        ob0 = prev_seg[1]
        d0 = (prev_seg[1] - prev_seg[0]).normalized()
        d1 = (curr_seg[1] - curr_seg[0]).normalized()
        pt = _line_intersect(prev_seg[0], d0, curr_seg[0], d1)
        if pt is None:
            result_list.append(ob0)   # parallel segments — degenerate join
        else:
            result_list.append(pt)    # always miter

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


def _dedupe_polyline(pts: list[Vec2], closed: bool,
                     eps: float = 1e-9) -> list[Vec2]:
    """Drop consecutive coincident points (and a duplicated closing point)."""
    out: list[Vec2] = []
    for p in pts:
        if not out or p.dist(out[-1]) > eps:
            out.append(p)
    if closed and len(out) > 1 and out[0].dist(out[-1]) <= eps:
        out.pop()
    return out


def _miter_band_test(source: list[Vec2], dist: float, closed: bool,
                     tol: float):
    """
    Return a predicate: is p STRICTLY inside the mitered offset band of
    `source` at |dist|?

    The band is the union of, per source edge, the rectangle of points
    whose foot lies on the edge and whose perpendicular distance is
    < |dist| (either side), plus, per joint, the miter kite
    [vertex, offset of edge-in end, miter point, offset of edge-out start]
    on the offset side. A point on a correct miter offset lies exactly on
    the band boundary; any part of the raw offset strictly inside the
    band is closer to the source than requested (under the same miter
    semantics used to build it) and must be trimmed.
    """
    n = len(source)
    D = abs(dist)
    m = n if closed else n - 1
    rects = []
    dirs = []
    for i in range(m):
        a, b = source[i], source[(i + 1) % n]
        L = a.dist(b)
        t = (b - a).normalized()
        dirs.append(t)
        if L > 1e-12:
            rects.append((a, t, L))
    kites = []
    joints = range(n) if closed else range(1, n - 1)
    for i in joints:
        t0, t1 = dirs[(i - 1) % m], dirs[i % m]
        v = source[i]
        p0 = Vec2(v.x - t0.y * dist, v.y + t0.x * dist)
        p1 = Vec2(v.x - t1.y * dist, v.y + t1.x * dist)
        cross = t0.x * t1.y - t0.y * t1.x
        if abs(cross) < 1e-12:
            continue
        s = ((p1.x - p0.x) * t1.y - (p1.y - p0.y) * t1.x) / cross
        M = Vec2(p0.x + s * t0.x, p0.y + s * t0.y)
        kites.append((v, p0, M, p1))

    def _in_convex(p: Vec2, poly) -> bool:
        sign = 0
        k = len(poly)
        for j in range(k):
            a, b = poly[j], poly[(j + 1) % k]
            c = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
            L = a.dist(b)
            if L < 1e-12:
                continue
            c /= L
            if abs(c) <= tol:
                return False                  # on boundary → not strictly in
            sgn = 1 if c > 0 else -1
            if sign == 0:
                sign = sgn
            elif sgn != sign:
                return False
        return sign != 0

    def in_band(p: Vec2) -> bool:
        for a, t, L in rects:
            dx, dy = p.x - a.x, p.y - a.y
            f = dx * t.x + dy * t.y
            if tol < f < L - tol and abs(dx * t.y - dy * t.x) < D - tol:
                return True
        return any(_in_convex(p, k) for k in kites)

    return in_band


def _trim_offset(raw: list[Vec2], source: list[Vec2],
                 dist: float, closed: bool) -> list[Vec2]:
    """
    Resolve a raw miter offset into the valid parallel offset.

    Wherever |dist| exceeds the local feature size of the source — a
    fillet radius smaller than the offset, or a notch narrower than
    2·|dist| — the raw miter offset folds over itself into swallowtail
    loops. Those loops are not part of the true offset: every point on
    them is CLOSER than |dist| to some part of the source.

    Trimming (the classic raw-offset + clip approach):
      1. Split the raw offset at all of its self-intersections.
      2. Keep a piece only if it lies outside the mitered offset band of
         the whole source (i.e. at ≥ |dist| under the same miter join
         semantics _offset_polyline uses) and, for closed sources, on the
         requested side.
      3. Re-join the kept pieces at the shared intersection points.

    Closed sources: the largest resulting loop is returned. If the
    offset genuinely splits into several islands (e.g. an inward offset
    through a narrow neck) only the largest survives — the remainder is
    a topology change the designer must resolve. If nothing survives
    the offset has collapsed and [] is returned.

    Open sources: the longest resulting chain is returned.
    """
    raw = _dedupe_polyline(raw, closed)
    n = len(raw)
    if abs(dist) < 1e-12 or n < 2 or len(source) < 2:
        return raw

    segs = list(range(n if closed else n - 1))   # seg i: raw[i] → raw[i+1]
    m = len(segs)

    # 1) Self-intersections. Half-open parameter ranges [0, 1) so a hit
    # exactly on a shared vertex is recorded once.
    eps = 1e-9
    events: list[list[tuple[float, int]]] = [[] for _ in range(m)]
    nodes: list[Vec2] = []
    boxes = []
    for i in range(m):
        a, b = raw[i], raw[(i + 1) % n]
        boxes.append((min(a.x, b.x), max(a.x, b.x),
                      min(a.y, b.y), max(a.y, b.y)))
    for i in range(m):
        a, b = raw[i], raw[(i + 1) % n]
        bi = boxes[i]
        d1x, d1y = b.x - a.x, b.y - a.y
        for j in range(i + 2, m):
            if closed and i == 0 and j == m - 1:
                continue                              # adjacent via wrap
            bj = boxes[j]
            if (bj[0] > bi[1] + eps or bj[1] < bi[0] - eps or
                    bj[2] > bi[3] + eps or bj[3] < bi[2] - eps):
                continue
            c, d = raw[j], raw[(j + 1) % n]
            d2x, d2y = d.x - c.x, d.y - c.y
            cross = d1x * d2y - d1y * d2x
            if abs(cross) < 1e-12:
                continue
            t = ((c.x - a.x) * d2y - (c.y - a.y) * d2x) / cross
            u = ((c.x - a.x) * d1y - (c.y - a.y) * d1x) / cross
            if -eps <= t < 1.0 - eps and -eps <= u < 1.0 - eps:
                t = max(0.0, t)
                u = max(0.0, u)
                k = len(nodes)
                nodes.append(Vec2(a.x + t * d1x, a.y + t * d1y))
                events[i].append((t, k))
                events[j].append((u, k))

    tol = 1e-6 * max(1.0, abs(dist))
    in_band = _miter_band_test(_dedupe_polyline(source, closed),
                               dist, closed, tol)
    want_inside = None
    if closed and len(source) >= 3:
        want_inside = (dist > 0) == (_polygon_area(source) > 0)

    def _valid(piece: list[Vec2]) -> bool:
        best_len, sample = -1.0, None
        for p, q in zip(piece, piece[1:]):
            L = p.dist(q)
            if L > best_len:
                best_len, sample = L, p.lerp(q, 0.5)
        if sample is None or best_len < 1e-12:
            return False
        if in_band(sample):
            return False
        if want_inside is not None:
            return _point_in_polygon(sample, source) == want_inside
        return True

    if not nodes:
        piece = raw + [raw[0]] if closed else raw
        return raw if _valid(piece) else []

    # 2) Walk the raw offset, cutting it into pieces between nodes.
    seq: list[tuple[Vec2, object]] = []
    for i in range(m):
        seq.append((raw[i], 'S' if (not closed and i == 0) else None))
        for t, k in sorted(events[i]):
            seq.append((nodes[k], k))
    if not closed:
        seq.append((raw[-1], 'E'))
    else:
        first = next(i for i, (_, k) in enumerate(seq) if k is not None)
        seq = seq[first:] + seq[:first]
        seq.append(seq[0])

    pieces: list[tuple[object, object, list[Vec2]]] = []
    cur_node, cur_pts = seq[0][1], [seq[0][0]]
    for p, k in seq[1:]:
        cur_pts.append(p)
        if k is not None:
            pieces.append((cur_node, k, cur_pts))
            cur_node, cur_pts = k, [p]

    valid = [pc for pc in pieces if _valid(pc[2])]
    if not valid:
        return []

    # 3) Re-join kept pieces at their shared intersection nodes.
    by_start: dict = {}
    for idx, (s, _, _) in enumerate(valid):
        by_start.setdefault(s, []).append(idx)
    ends = {e for _, e, _ in valid}
    order = ([i for i, pc in enumerate(valid) if pc[0] not in ends] +
             list(range(len(valid))))
    used: set[int] = set()
    chains: list[tuple[bool, list[Vec2]]] = []
    for head in order:
        if head in used:
            continue
        start_node = valid[head][0]
        pts: list[Vec2] = []
        cur, is_loop = head, False
        while cur is not None:
            used.add(cur)
            s, e, ppts = valid[cur]
            pts.extend(ppts if not pts else ppts[1:])
            if e == start_node:
                is_loop = True
                break
            cur = next((c for c in by_start.get(e, []) if c not in used),
                       None)
        chains.append((is_loop, pts))

    if closed:
        loops = [_dedupe_polyline(pts, True)
                 for is_loop, pts in chains if is_loop]
        loops = [lp for lp in loops if len(lp) >= 3]
        if not loops:
            return []
        return max(loops, key=lambda lp: abs(_polygon_area(lp)))

    def _arc_len(pts):
        return sum(p.dist(q) for p, q in zip(pts, pts[1:]))
    opens = [pts for is_loop, pts in chains if not is_loop]
    if not opens:
        return []
    return _dedupe_polyline(max(opens, key=_arc_len), False)


def _wall_system_end_pts(src_pts: list[Vec2],
                          walls: list[tuple[float, list[Vec2]]],
                          cap_style: str,
                          cap_corner_radius: float,
                          landings: tuple = ((), ())):
    """
    One end treatment per wall SYSTEM (source + N parallel offsets).

    The whole wall assembly is treated as ONE cross-section of total
    thickness W (outermost → innermost wall endpoint). At each end the
    cap follows a single profile across that cross-section:

        outermost wall → fillet r → straight end face → fillet r → innermost wall

    The fillets are tangent to the longitudinal walls at the wall
    endpoints (centres at r and W−r along the cross-section); the end
    face sits r beyond the wall ends. The effective radius r is:

        'flat'            → 0          (straight face through the endpoints)
        'rounded_corners' → min(cap_corner_radius, W/2)
        'full_round'      → W/2        (the two fillets meet: a semicircle)

    So End R = 0 reduces exactly to Flat and End R ≥ W/2 to Full Round.

    Intermediate walls never get caps of their own. Each one continues
    straight (along the end tangent) until it meets the cap profile; that
    meeting point is inserted into the cap polyline so the routing graph
    joins the wall system into one component. When the profile is at
    height 0 there (flat face) the wall endpoint itself lies on the cap
    and no extension is needed.

    `walls` are the OTHER walls of the system as (signed offset distance,
    polyline); the source itself is the distance-0 wall. The same builder
    closes original open-wall ends and the cut faces left by openings.

    `landings` = (start_points, end_points): extra points lying on the
    flat end line (e.g. lattice strands clipped at an opening) that must
    join the cap. They land on the profile exactly like intermediate walls.

    Returns (start, end), each (cap_pts, [extension_pts, ...]).
    Cap endpoints and extension endpoints are exact wall-endpoint / cap
    coordinates so the routing graph merges the nodes.
    """
    if len(src_pts) < 2 or not walls:
        return ([], []), ([], [])

    # Walls ordered outermost (most positive distance = furthest LEFT of
    # travel) → innermost; the source is the distance-0 wall.
    entries: list[tuple[float, list[Vec2]]] = [(0.0, src_pts)]
    entries.extend(walls)
    entries.sort(key=lambda e: -e[0])

    if cap_style == 'flat':
        req_r = 0.0
    elif cap_style == 'rounded_corners':
        req_r = max(0.0, cap_corner_radius)
    else:                                   # 'full_round'
        req_r = float('inf')

    def _build(end_index: int):
        endpoints = [poly[end_index] for _, poly in entries if poly]
        if len(endpoints) < 2:
            return [], []
        if end_index == 0:
            tangent = (src_pts[1] - src_pts[0]).normalized()
            o = Vec2(-tangent.x, -tangent.y)        # outward = backwards
        else:
            o = (src_pts[-1] - src_pts[-2]).normalized()
        e_out, e_in = endpoints[0], endpoints[-1]
        W = e_out.dist(e_in)
        if W < 1e-9:
            return list(endpoints), []
        u = (e_in - e_out) * (1.0 / W)
        r = min(req_r, W / 2.0)
        if r < 1e-9:
            r = 0.0

        def _at(s: float, h: float) -> Vec2:
            return Vec2(e_out.x + s * u.x + h * o.x,
                        e_out.y + s * u.y + h * o.y)

        def _height(s: float) -> float:
            if r == 0.0:
                return 0.0
            if s < r:
                c = r
            elif s > W - r:
                c = W - r
            else:
                return r
            return math.sqrt(max(0.0, r * r - (s - c) ** 2))

        # Profile samples as (s, point); s is monotonic along the profile.
        prof: list[tuple[float, Vec2]] = [(0.0, e_out)]
        if r > 0.0:
            n_arc = 19                                   # 5° per sample
            for i in range(1, n_arc):                    # outer fillet
                a = (math.pi / 2) * i / (n_arc - 1)
                s = r - r * math.cos(a)
                prof.append((s, _at(s, r * math.sin(a))))
            # Inner fillet; skip its first sample when the fillets meet
            # (full round) and its last sample, which is e_in itself.
            first = 0 if W - 2 * r > 1e-9 else 1
            for i in range(first, n_arc - 1):
                a = (math.pi / 2) * i / (n_arc - 1)
                s = (W - r) + r * math.sin(a)
                prof.append((s, _at(s, r * math.cos(a))))
        prof.append((W, e_in))

        # Intermediate walls (and any extra landing points) land on the
        # profile.
        extensions: list[list[Vec2]] = []
        landing: list[tuple[float, Vec2]] = []
        extra = landings[0] if end_index == 0 else landings[1]
        joiners: list[Vec2] = list(endpoints[1:-1])
        for x in extra:
            if all(x.dist(q) > 1e-9 for q in endpoints + joiners):
                joiners.append(x)
        for ep in joiners:
            s = (ep - e_out).x * u.x + (ep - e_out).y * u.y
            s = max(0.0, min(W, s))
            h = _height(s)
            if h < 1e-9:
                landing.append((s, ep))          # already on the flat face
            else:
                q = _at(s, h)
                landing.append((s, q))
                extensions.append([ep, q])

        # Merge landing points into the profile by s, replacing any
        # profile sample that coincides with them.
        merged = list(prof)
        for s, q in landing:
            merged = [(ps, pp) for ps, pp in merged
                      if pp is e_out or pp is e_in or pp.dist(q) > 1e-9]
            idx = next((i for i, (ps, _) in enumerate(merged) if ps > s),
                       len(merged) - 1)
            merged.insert(idx, (s, q))
        cap = [p for _, p in merged]
        cap[0], cap[-1] = e_out, e_in
        return cap, extensions

    return _build(0), _build(-1)


def _processed_source_pts(path: 'Path', corner_radius: float) -> list[Vec2]:
    """
    THE canonical processed-source polyline for a path.
    - Samples the path parametrically.
    - If the path type has discrete corners AND corner_radius > 0:
        applies corner_radius fillets.
    Downstream geometry (offsets, caps, lattice, routing, display) all
    derive from this single result — not from re-sampling the source.
    """
    pts = path.sample_points(128)
    if corner_radius > 0.0 and _eligible_for_rounding(path):
        pts = _apply_corner_rounding(pts, corner_radius, path.closed)
    return _dedupe_polyline(pts, path.closed)


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
# Openings — path-relative gaps cut through a whole wall assembly
# ---------------------------------------------------------------------------

@dataclass
class Opening:
    """
    A gap in a wall (future door / window), attached to a SOURCE path.

    It is an interval of ARC LENGTH along that path — not an XY region:
    centre `center_s` and `width`, both in inches, measured along the
    processed source polyline (the printed wall: sampled + Corner R) from
    the path's start. On closed paths positions wrap through the seam, so
    an opening may span corners and the seam freely.

    The opening cuts the complete wall assembly derived from the source
    (source + every offset of it + lattice built on those walls); the cut
    faces are closed by the same wall-end builder as open wall ends.

    end_treatment: 'inherit' = the layer's cap style. The only value for
        now; the field exists so a per-opening override can be added.
    z_min / z_max: reserved for physical-Z doors and windows (e.g. a door
        from Z 0 to 84 in). None = full height. Not used by the geometry
        yet — this prototype works on a single Z slice.
    """
    id: str
    source_path_id: str
    center_s: float
    width: float = 12.0
    end_treatment: str = 'inherit'
    z_min: Optional[float] = None
    z_max: Optional[float] = None
    label: str = ''

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'type': 'opening',
            'source_path_id': self.source_path_id,
            'center_s': self.center_s,
            'width': self.width,
            'end_treatment': self.end_treatment,
            'z_min': self.z_min,
            'z_max': self.z_max,
            'label': self.label,
        }


@dataclass
class Trim:
    """
    A suppressed SECTION of a source path: the stretch between two
    consecutive contacts with other source paths (or a contact and an open
    end). Non-destructive — the source keeps its parametric identity; the
    trim only removes that section from the effective design geometry.

    It is stored as a topological SIGNATURE, not as coordinates, so it
    follows parametric edits (trim.py resolves it on every build):
      start / end: ids of the source(s) bounding the section at each
                   contact ([] = the path's own open end)
      inside:      {closed bounding source id: section midpoint inside it?}
      u_mid:       midpoint as a fraction of the source's length — only a
                   tie-break between sections with the same signature.
    A trim that matches no current section (an intersection is gone) or
    several (a tie u_mid cannot break) is UNRESOLVED: kept, but nothing is
    suppressed.
    """
    id: str
    source_path_id: str
    start: list = field(default_factory=list)
    end: list = field(default_factory=list)
    inside: dict = field(default_factory=dict)
    u_mid: float = 0.5

    def to_dict(self) -> dict:
        return {'id': self.id, 'source_path_id': self.source_path_id,
                'start': list(self.start), 'end': list(self.end),
                'inside': dict(self.inside), 'u_mid': self.u_mid}


@dataclass
class RegionOverride:
    """
    Explicit wall / void classification of one region of a wall network
    (the future paint-bucket tool). Ordinary walls never need one — the
    network infers material from wall bands, junctions and lattice.

    The region is identified PATH-RELATIVELY, like openings: the point at
    arc length `s` along the processed source `path_id`, displaced
    `offset` inches along its left normal. The override applies to the
    network region containing that point, so it follows the path when the
    path is moved or reshaped (and, later, across physical-Z layers).
    """
    id: str
    path_id: str
    s: float
    offset: float
    kind: str = 'wall'          # 'wall' | 'void'

    def to_dict(self) -> dict:
        return {'id': self.id, 'type': 'region_override',
                'path_id': self.path_id, 's': self.s,
                'offset': self.offset, 'kind': self.kind}


@dataclass
class WallSpec:
    """
    Parametric wall thickness of a source path. The wall's other face(s)
    are OFFSETS generated from the processed source on every evaluation,
    so they follow any edit of the source (resize, move, reshape) and the
    wall stays exactly `thickness` thick.

    align: 'auto'    closed → 'inside', open → 'center'
           'inside'  / 'outside'  (closed paths; by winding)
           'left' / 'right'       (of the path's direction)
           'center'               ±thickness/2 (source = centre line)
    """
    thickness: float
    align: str = 'auto'
    # A CENTRED wall's reference line is construction geometry: by default
    # it is not printed (a bead down the middle would cross every infill
    # strut — local mud build-up). Inside / outside / left / right walls
    # keep the reference as one of their printed faces.
    print_reference: bool = False
    # WALL SYSTEM (wall_systems.py): None / 'skin_web' = two skins + Wall
    # Infill (the default); otherwise how the envelope is filled.
    system: dict = None

    def resolved_align(self, closed: bool) -> str:
        a = self.align
        if a == 'auto':
            a = 'inside' if closed else 'center'
        if a in ('inside', 'outside') and not closed:
            a = 'left' if a == 'inside' else 'right'
        return a

    def reference_printed(self, closed: bool) -> bool:
        return self.print_reference or self.resolved_align(closed) != 'center'

    def to_dict(self) -> dict:
        d = {'thickness': self.thickness, 'align': self.align,
             'print_reference': self.print_reference}
        if self.system:
            d['system'] = dict(self.system)
        return d

    def offsets(self, path_id: str, closed: bool,
                pts: list[Vec2]) -> list['OffsetTreatment']:
        t = max(0.0, float(self.thickness))
        if t <= 1e-9:
            return []
        align = self.resolved_align(closed)
        ccw = _polygon_area(pts) > 0 if closed and len(pts) >= 3 else True
        sign = {'left': 1.0, 'right': -1.0,
                'inside': 1.0 if ccw else -1.0,
                'outside': -1.0 if ccw else 1.0}.get(align)
        if sign is not None:
            return [OffsetTreatment(f'{path_id}.wall', path_id, sign * t,
                                    label='wall')]
        return [OffsetTreatment(f'{path_id}.wall+', path_id, t / 2, label='wall'),
                OffsetTreatment(f'{path_id}.wall-', path_id, -t / 2, label='wall')]


@dataclass
class NetworkWall:
    """
    Wall-NETWORK property: the default wall (thickness + alignment) for
    every source path of the network that `path_id` belongs to (source
    paths that touch / cross, transitively). A path's own WallSpec wins.
    Membership is derived per layer, so a path joined to the network later
    inherits it too. First NetworkWall per network wins.
    """
    id: str
    path_id: str
    thickness: float
    align: str = 'auto'
    print_reference: bool = False
    system: dict = None                  # WALL SYSTEM (see WallSpec)

    def to_dict(self) -> dict:
        d = {'id': self.id, 'type': 'network_wall', 'path_id': self.path_id,
             'thickness': self.thickness, 'align': self.align,
             'print_reference': self.print_reference}
        if self.system:
            d['system'] = dict(self.system)
        return d


@dataclass
class WallSystem:
    """
    WALL SYSTEM (layer-level; wall_systems.py) — the CONSTRUCTION
    specification of an explicit, user-authored group of Design paths:

      name, members                  which paths (at most ONE system per path;
                                     members need not touch or share a Wall
                                     Network — connectivity stays geometric)
      thickness, align,              the wall ENVELOPE of every member
      print_reference
      type, params                   the CONSTRUCTION: 'single' (the ordinary
                                     single-line / out-and-back wall: no
                                     envelope, the bead + return-lane rules) |
                                     'hollow' (two skins) | 'skin_web' (two
                                     skins + a web) | 'parallel' (N walls) |
                                     'interleaved' | 'linked' | 'chain'
      web = {pattern, params,        Skin + Web only: the web lattice (zigzag /
             variation_index}        wave / truss; pattern 'none' = skins
                                     only). Materialised as RegionInfill
                                     records OWNED by the system (owner = id;
                                     one per connected group of members), so
                                     Layer-Design lineage keeps keying the
                                     lattice on infill ids.

    A path in no Wall System is a single bead (a physical out-and-back pair
    when open). Legacy designs (a path's own WallSpec, Network Walls,
    unowned wall infills) still resolve as before; the Designer migrates
    them into Wall Systems. thickness None = a legacy system from the
    membership-only pass (envelope from the path / network wall).
    """
    id: str
    type: str = 'skin_web'
    params: dict = field(default_factory=dict)
    members: list = field(default_factory=list)
    name: str = ''
    thickness: Optional[float] = None
    align: str = 'auto'
    print_reference: bool = False
    web: dict = field(default_factory=dict)

    def spec(self) -> dict:
        return {**(self.params or {}), 'type': self.type}

    def envelope(self) -> Optional['WallSpec']:
        if self.thickness is None or self.thickness <= 1e-9:
            return None
        return WallSpec(float(self.thickness), self.align or 'auto', bool(self.print_reference))

    def to_dict(self) -> dict:
        d = {'id': self.id, 'name': self.name, 'type': self.type, 'params': dict(self.params or {}),
             'members': list(self.members), 'align': self.align,
             'print_reference': self.print_reference, 'web': dict(self.web or {})}
        if self.thickness is not None:
            d['thickness'] = self.thickness
        return d


@dataclass
class RegionInfill:
    """
    Lattice / infill that fills a printable WALL REGION — the connected
    wall material of a wall network (or of a single wall), with all its
    holes, branches, junctions and openings — as ONE coherent field
    (one spacing, one phase). Replaces choosing two boundaries.

    REGION: `path_id` is the boundary that owns the region (explicit). It
    fills every material region bordering that path's walls. If the path
    is a closed single-bead boundary (no wall thickness), the infill itself
    declares its inside to be material and every closed path lying inside
    it is a VOID (determined geometrically — nesting, not creation order;
    even-odd, so an island inside a void is material again). The UI picks
    the outermost closed boundary of a nested group unless the designer
    explicitly chose an inner one. Two infills reaching the same region:
    the first one wins.

    KIND — what the infill MEANS (and so how it is generated / routed):
      'wall'   wall infill: stitches reinforcing a wall BETWEEN ITS FACES
               (zigzag / wave), generated route-aware by wall_lattice.py;
               regions too wide to be a wall fall back to the field
               generator + local repair (infill.py + route_plan.py).
      'solid'  solid infill: conventional AREA fill of a solid region
               (rectilinear or serpentine lines whose turns land on the
               outer and void boundaries; short travel rather than long
               connectors). solid.py.
    Defaults to 'wall' for compatibility with older payloads; the UI sets
    it explicitly ('solid' for closed single-bead boundaries).
    """
    id: str
    path_id: str
    pattern: str = 'zigzag'          # infill.PATTERNS (wall) | solid.PATTERNS
    params: dict = field(default_factory=dict)
    variation_index: int = 0
    kind: str = 'wall'               # 'wall' | 'solid'
    owner: Optional[str] = None      # the Skin + Web WALL SYSTEM whose web this is

    def to_dict(self) -> dict:
        d = {'id': self.id, 'type': 'region_infill', 'path_id': self.path_id,
             'kind': self.kind,
             'pattern': self.pattern, 'params': dict(self.params),
             'variation_index': self.variation_index}
        if self.owner:
            d['owner'] = self.owner
        return d


@dataclass
class JunctionSetting:
    """
    Treatment of ONE network junction corner, keyed by the wall faces that
    meet there (see network.JunctionCorner) — not by position. Junction
    corners are separate from any source path's own Corner R.
    """
    key: str
    treatment: str = 'miter'         # 'miter' | 'round'
    radius: float = 0.0

    def to_dict(self) -> dict:
        return {'key': self.key, 'treatment': self.treatment,
                'radius': self.radius}


def _cum_lengths(pts: list[Vec2], closed: bool) -> list[float]:
    """Cumulative arc length at each vertex; closed adds the closing edge."""
    n = len(pts)
    cum = [0.0]
    for i in range(n if closed else n - 1):
        cum.append(cum[-1] + pts[i].dist(pts[(i + 1) % n]))
    return cum


def _locate_s(pts: list[Vec2], cum: list[float], s: float, closed: bool,
              forward: bool = True) -> tuple[Vec2, int]:
    """
    Point at arc length s and the index of the segment it lies on.
    At a vertex, `forward` picks the outgoing segment (else the incoming).
    Closed paths wrap s modulo the perimeter.
    """
    import bisect
    L = cum[-1]
    m = len(cum) - 1
    n = len(pts)
    if closed and L > 0:
        s = s % L
        if not forward and s <= 1e-12:
            s = L
    s = max(0.0, min(L, s))
    if forward:
        k = bisect.bisect_right(cum, s) - 1
    else:
        k = bisect.bisect_left(cum, s) - 1
    k = max(0, min(m - 1, k))
    a, b = pts[k], pts[(k + 1) % n]
    seg = cum[k + 1] - cum[k]
    t = (s - cum[k]) / seg if seg > 1e-12 else 0.0
    t = max(0.0, min(1.0, t))
    if t <= 0.0:
        return a, k
    if t >= 1.0:
        return b, k
    return a.lerp(b, t), k


def _sub_polyline(pts: list[Vec2], cum: list[float], s0: float, s1: float,
                  closed: bool) -> list[Vec2]:
    """
    The polyline from arc length s0 forward to s1 (s1 ≥ s0). On closed
    paths s1 may exceed the perimeter: the piece runs through the seam.
    """
    L = cum[-1]
    n = len(pts)
    if closed and L > 0:
        shift = math.floor(s0 / L) * L
        s0, s1 = s0 - shift, s1 - shift
    start, _ = _locate_s(pts, cum, s0, closed, forward=True)
    end, _ = _locate_s(pts, cum, s1, closed, forward=False)
    verts = [(cum[j], pts[j]) for j in range(n)]
    if closed:
        verts += [(cum[j] + L, pts[j]) for j in range(n)] + [(2 * L, pts[0])]
    eps = 1e-9
    out = [start] + [p for pos, p in verts if s0 + eps < pos < s1 - eps]
    out.append(end)
    return _dedupe_polyline(out, False)


def _project_to_polyline(p: Vec2, pts: list[Vec2], cum: list[float],
                         closed: bool) -> tuple[float, float, Vec2]:
    """Nearest point on the polyline: (arc length s, distance, foot)."""
    n = len(pts)
    best = (0.0, float('inf'), pts[0])
    for i in range(n if closed else n - 1):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b.x - a.x, b.y - a.y
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, (
            (p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
        f = Vec2(a.x + t * dx, a.y + t * dy)
        d = p.dist(f)
        if d < best[1]:
            best = (cum[i] + t * (cum[i + 1] - cum[i]), d, f)
    return best


# Openings closer than this along the path are treated as touching: their
# intervals merge, so no sliver of wall (with two caps) is left between them.
OPENING_MERGE_TOL = 1e-3


def _opening_removed_intervals(openings: list[Opening], L: float,
                               closed: bool, cap_reach: float = 0.0
                               ) -> list[tuple[float, float]]:
    """
    Merged arc-length intervals removed by openings on one path.

    An opening's `width` is the CLEAR opening. End treatments protrude
    past the cut face by `cap_reach` (0 flat, r rounded, W/2 full round),
    so each cut face sits cap_reach further out: the finished ends then
    stop exactly at the clear width and two caps can never collide.
    Open paths: clipped to [0, L]. Closed paths: each interval starts in
    [0, L) and may end past L (wrapping through the seam); a result of
    [(0, L)] means the whole loop is removed.
    """
    raw: list[tuple[float, float]] = []
    for op in openings:
        w = max(0.0, float(op.width))
        if w <= 1e-9 or L <= 1e-9:
            continue
        w += 2.0 * max(0.0, cap_reach)
        if closed:
            if w >= L - 1e-9:
                return [(0.0, L)]
            a = (op.center_s - w / 2.0) % L
            raw.append((a, a + w))
        else:
            c = max(0.0, min(L, op.center_s))
            a, b = max(0.0, c - w / 2.0), min(L, c + w / 2.0)
            if b - a > 1e-9:
                raw.append((a, b))
    return _merge_removed(raw, L, closed)


def _merge_removed(raw: list, L: float, closed: bool) -> list[tuple[float, float]]:
    """Union of removed arc intervals on one path (openings, trims)."""
    # Union of all openings on the path: overlapping or touching intervals
    # become ONE removed interval (caps only at its outer boundaries).
    tol = OPENING_MERGE_TOL
    raw.sort()
    merged: list[list[float]] = []
    for a, b in raw:
        if merged and a <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if not closed:
        # Snap to the path ends so no sliver survives there either.
        if merged and merged[0][0] <= tol:
            merged[0][0] = 0.0
        if merged and merged[-1][1] >= L - tol:
            merged[-1][1] = L
    # Closed: the seam is not a boundary — an interval reaching past L
    # continues into those starting near 0.
    while closed and len(merged) > 1 and \
            merged[-1][1] - L >= merged[0][0] - tol:
        merged[-1][1] = max(merged[-1][1], merged[0][1] + L)
        merged.pop(0)
    if closed and len(merged) == 1 and \
            merged[0][1] - merged[0][0] >= L - tol:
        return [(0.0, L)]
    return [(a, b) for a, b in merged]


def _merge_closed_intervals(raw: list, L: float) -> list:
    """Union of arc intervals (a, b) on a closed ring (a in [0, L), b may
    pass L through the seam)."""
    raw = sorted(((a % L, (a % L) + (b - a)) for a, b in raw if b - a > 1e-9))
    merged: list = []
    tol = OPENING_MERGE_TOL
    for a, b in raw:
        if merged and a <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    while len(merged) > 1 and merged[-1][1] - L >= merged[0][0] - tol:
        merged[-1][1] = max(merged[-1][1], merged[0][1] + L)
        merged.pop(0)
    if len(merged) == 1 and merged[0][1] - merged[0][0] >= L - tol:
        return [(0.0, L)]
    return [(a, b) for a, b in merged]


def _ray_ring_hit(o: Vec2, d: Vec2, rings: dict, skip_t: float = 1e-6):
    """Nearest hit of the ray o + t·d (t > skip_t) with any ring:
    (t, ring id, point) or None."""
    best = None
    for rid, ring in rings.items():
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            ex, ey = b.x - a.x, b.y - a.y
            den = d.x * ey - d.y * ex
            if abs(den) < 1e-12:
                continue
            wx, wy = a.x - o.x, a.y - o.y
            t = (wx * ey - wy * ex) / den
            u = (wx * d.y - wy * d.x) / den
            if t > skip_t and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best[0]):
                best = (t, rid, Vec2(o.x + t * d.x, o.y + t * d.y))
    return best


def _region_corridor(face_id: str, a: float, b: float, rings: dict,
                     in_material, depth_max: float):
    """An opening interval [a, b] on face `face_id` of a wall MATERIAL
    region (rings: outer + voids, closed) → the corridor it cuts THROUGH
    the material: each end of the interval is carried along the face's
    local normal INTO the material to the first boundary it meets (the
    opposite face). Both ends (and the middle) must reach the SAME ring
    within depth_max, else None (no defensible opposite face — never cut
    across a room). Returns (opposite ring id, (ga, gb) interval on it,
    corridor polygon, (cut face a, cut face b))."""
    F = rings[face_id]
    cumF = _cum_lengths(F, True)
    L = cumF[-1]
    hits = []
    n_F = len(F)

    def seg_normal(k, pt):
        e = F[(k + 1) % n_F] - F[k]
        if e.length() < 1e-12:
            return None
        e = e * (1.0 / e.length())
        # which side is material: tested off the segment's MIDDLE (at a
        # vertex the test point could fall on another edge's extension)
        mid = F[k].lerp(F[(k + 1) % n_F], 0.5)
        for cand in (Vec2(-e.y, e.x), Vec2(e.y, -e.x)):
            if in_material(Vec2(mid.x + 0.25 * cand.x, mid.y + 0.25 * cand.y)):
                return cand
        return None
    for s_, lim in ((a, 1.5), ((a + b) / 2.0, 1.0), (b, 1.5)):
        pt, k = _locate_s(F, cumF, s_ % L, True, forward=(s_ == a))
        nrm = seg_normal(k, pt)
        # exactly at a vertex (a corner): the bisector of both edges'
        # inward normals (else a ray could run along the next edge)
        for j in (k, (k + 1) % n_F):
            if F[j].dist(pt) < 1e-6:
                n0, n1 = seg_normal((j - 1) % n_F, pt), seg_normal(j, pt)
                if n0 is not None and n1 is not None:
                    m = n0 + n1
                    if m.length() > 1e-9:
                        nrm = m * (1.0 / m.length())
        if nrm is None:
            return None
        h = _ray_ring_hit(pt, nrm, rings)
        if h is None or h[0] > lim * depth_max:
            return None
        hits.append((pt, h))
    gid = hits[1][1][1]
    if any(h[1] != gid for _, h in hits):
        return None
    G = rings[gid]
    cumG = _cum_lengths(G, True)
    LG = cumG[-1]
    ga = _project_to_polyline(hits[0][1][2], G, cumG, True)[0]
    gb = _project_to_polyline(hits[2][1][2], G, cumG, True)[0]
    # the arc between the two hits that passes the middle hit
    gm = _project_to_polyline(hits[1][1][2], G, cumG, True)[0]
    lo, hi = (ga, gb) if (gb - ga) % LG <= (ga - gb) % LG else (gb, ga)
    if (gm - lo) % LG > (hi - lo) % LG + 1e-6:
        lo, hi = hi, lo
    span = (hi - lo) % LG
    if gid == face_id:
        return None
    side_f = _sub_polyline(F, cumF, a, b, True)
    side_g = _sub_polyline(G, cumG, lo, lo + span, True)
    pa, pb = side_f[0], side_f[-1]
    # orient the opposite side to run from b's hit back to a's hit
    if side_g[0].dist(hits[2][1][2]) > side_g[-1].dist(hits[2][1][2]):
        side_g = list(reversed(side_g))
    poly = side_f + side_g
    return gid, (lo, lo + span), poly, ([pa, side_g[-1]], [pb, side_g[0]])


def _surviving_intervals(removed: list[tuple[float, float]], L: float,
                         closed: bool) -> list[tuple[float, float]]:
    """Complement of the removed intervals: the wall pieces that remain."""
    if closed:
        if removed == [(0.0, L)]:
            return []
        k = len(removed)
        pieces = [(removed[i][1], removed[(i + 1) % k][0] +
                   (L if i == k - 1 else 0.0)) for i in range(k)]
    else:
        pieces, prev = [], 0.0
        for a, b in removed:
            pieces.append((prev, a))
            prev = b
        pieces.append((prev, L))
    return [(a, b) for a, b in pieces if b - a > OPENING_MERGE_TOL]


@dataclass
class _FaceWall:
    """Another SOURCE path acting as a wall of a source's assembly for
    openings (the inner face of a two-path wall): quacks like the
    OffsetTreatment parts the opening machinery reads."""
    id: str
    distance: float
    source_path_id: str


@dataclass
class _Cut:
    """One cut face across a wall assembly (one side of an opening)."""
    point: Vec2                       # cut point on the source
    normal: Vec2                      # left normal of the source there
    face: Optional[tuple]             # (outermost, innermost) wall endpoints
    ends: list = field(default_factory=list)      # every wall endpoint here
    landings: list = field(default_factory=list)  # lattice ends on the face
    trim: bool = False                # a trim boundary (free end), not an opening


@dataclass
class _WallPiece:
    """A surviving stretch of a wall assembly between cuts / path ends."""
    src_pts: list
    walls: list                       # [(OffsetTreatment, polyline)]
    start_cut: Optional[_Cut]         # None = original open path end
    end_cut: Optional[_Cut]


class _OpeningPlan:
    """
    How a source's openings cut its wall assembly. Built from the FULL
    processed source and FULL offsets, so offsets/trim/caps are computed
    exactly as without openings and only then segmented.
    """
    def __init__(self, src_pts: list[Vec2], closed: bool,
                 offsets: list, openings: list[Opening],
                 cap_reach: float = 0.0, trims: list = ()):
        self.src_pts = src_pts
        self.closed = closed
        self.cum = _cum_lengths(src_pts, closed)
        self.L = self.cum[-1]
        # TRIMS (resolved sections, trim.py) remove arc intervals too, but
        # they are not openings: no clear void, no cap-reach widening, and
        # their boundaries are FREE ends (they may join the wall they meet).
        self.opening_removed = _opening_removed_intervals(openings, self.L, closed,
                                                          cap_reach)
        # trims: (a, b[, exact point at a, at b]) — a trimmed end is placed
        # exactly on the contact point it shares with the path it meets
        self.trim_bounds = [(t[0], t[2] if len(t) > 2 else None) for t in trims] + \
                           [(t[1], t[3] if len(t) > 3 else None) for t in trims]
        self.removed = (_merge_removed(list(self.opening_removed) + [(t[0], t[1]) for t in trims],
                                       self.L, closed)
                        if trims else self.opening_removed)
        self.pieces: list[_WallPiece] = []
        self.cuts: list[_Cut] = []
        off_info = []
        for ot, derived in offsets:
            pts = derived.sample_points()
            if len(pts) >= 2:
                off_info.append((ot, pts, _cum_lengths(pts, derived.closed),
                                 derived.closed))
        for a, b in _surviving_intervals(self.removed, self.L, closed):
            sub = _sub_polyline(src_pts, self.cum, a, b, closed)
            if len(sub) < 2:
                continue
            for idx, s_ in ((0, a), (-1, b)):
                q = self._trim_point(s_)
                if q is not None and q.dist(sub[idx]) <= 1e-6:
                    sub[idx] = q
            is_start_cut = closed or a > 1e-9
            is_end_cut = closed or b < self.L - 1e-9
            walls = []
            for ot, pts, cum, oclosed in off_info:
                wp = self._offset_piece(ot, pts, cum, oclosed, a, b,
                                        is_start_cut, is_end_cut)
                if wp and len(wp) >= 2:
                    walls.append((ot, wp))
            start_cut = self._make_cut(a, True, sub, walls) \
                if is_start_cut else None
            end_cut = self._make_cut(b, False, sub, walls) \
                if is_end_cut else None
            for c, s in ((start_cut, a), (end_cut, b)):
                if c is not None:
                    c.trim = self.is_trim_bound(s)
            self.cuts += [c for c in (start_cut, end_cut) if c]
            self.pieces.append(_WallPiece(sub, walls, start_cut, end_cut))

    def _trim_bound(self, s: float):
        for t, q in self.trim_bounds:
            d = abs(s - t)
            if self.closed and self.L > 0:
                d = min(d % self.L, self.L - d % self.L)
            if d <= OPENING_MERGE_TOL:
                return (t, q)
        return None

    def is_trim_bound(self, s: float) -> bool:
        """Is arc position s a trim boundary (a free end, not an opening cut)?"""
        return self._trim_bound(s) is not None

    def _trim_point(self, s: float):
        """The exact shared contact point of a trim boundary at s (or None)."""
        tb = self._trim_bound(s)
        return tb[1] if tb else None

    def _cut_frame(self, s: float, forward: bool) -> tuple[Vec2, Vec2]:
        p, k = _locate_s(self.src_pts, self.cum, s, self.closed, forward)
        n = len(self.src_pts)
        d = (self.src_pts[(k + 1) % n] - self.src_pts[k]).normalized()
        return p, Vec2(-d.y, d.x)

    def _offset_pos(self, ot, pts, cum, oclosed, s, forward) -> float:
        """
        Arc position on an offset corresponding to source position s: the
        offset point whose nearest-point projection back onto the source
        is s (the same correspondence `in_opening` uses, so the cut face
        IS the opening boundary).

        Start from the offset point nearest the source point pushed out
        along its normal by the offset distance (exact on straight runs
        and concentric rounded-corner arcs), then refine locally: on
        polygon-sampled curves the concave-side miters shift that guess
        slightly. Where projection jumps (a miter vertex equidistant from
        two source edges) the bisection settles on the jump, i.e. the
        miter vertex — the physically right cut.
        """
        p, nrm = self._cut_frame(s, forward)
        target = Vec2(p.x + nrm.x * ot.distance, p.y + nrm.y * ot.distance)
        pos0 = _project_to_polyline(target, pts, cum, oclosed)[0]
        Lo = cum[-1]

        def delta(pos):
            q, _ = _locate_s(pts, cum, pos, oclosed)
            d = _project_to_polyline(q, self.src_pts, self.cum,
                                     self.closed)[0] - s
            if self.closed and self.L > 0:
                d = (d + self.L / 2) % self.L - self.L / 2
            return d

        d0 = delta(pos0)
        if abs(d0) < 1e-9:
            return pos0
        span = max(1.0, abs(ot.distance))
        steps = 32
        best = None
        prev_pos, prev_d = pos0, d0
        for direction in (1.0, -1.0):
            prev_pos, prev_d = pos0, d0
            for i in range(1, steps + 1):
                pos = pos0 + direction * span * i / steps
                if not oclosed:
                    pos = max(0.0, min(Lo, pos))
                dv = delta(pos)
                if (dv > 0) != (prev_d > 0) and abs(dv - prev_d) < self.L / 4:
                    cand = (abs(pos - pos0), prev_pos, prev_d, pos, dv)
                    if best is None or cand[0] < best[0]:
                        best = cand
                    break
                prev_pos, prev_d = pos, dv
        if best is None:
            return pos0
        _, lo, dlo, hi, _ = best
        for _ in range(48):
            mid = (lo + hi) / 2
            dm = delta(mid)
            if (dm > 0) == (dlo > 0):
                lo, dlo = mid, dm
            else:
                hi = mid
        pos = (lo + hi) / 2
        return pos % Lo if oclosed and Lo > 0 else pos

    def _offset_piece(self, ot, pts, cum, oclosed, a, b,
                      is_start_cut, is_end_cut) -> list[Vec2]:
        Lo = cum[-1]
        pa = (self._offset_pos(ot, pts, cum, oclosed, a, True)
              if is_start_cut else 0.0)
        pb = (self._offset_pos(ot, pts, cum, oclosed, b, False)
              if is_end_cut else Lo)
        if oclosed:
            span = (pb - pa) % Lo if Lo > 0 else 0.0
            # The offset's own seam is arbitrary (trimming may rotate it):
            # walk forward from pa. If the forward span disagrees wildly
            # with the source piece's share of the perimeter, the two cuts
            # collapsed onto (nearly) the same offset point — e.g. both
            # inside one miter — and this wall has no piece here.
            if abs(span / Lo - (b - a) / self.L) > 0.5:
                return []
            return _sub_polyline(pts, cum, pa, pa + span, True)
        if pb - pa <= 1e-9:
            return []
        return _sub_polyline(pts, cum, pa, pb, False)

    def _make_cut(self, s, is_piece_start, sub, walls) -> _Cut:
        p, nrm = self._cut_frame(s, is_piece_start)
        idx = 0 if is_piece_start else -1
        ends = [(0.0, sub[idx])] + [(ot.distance, wp[idx]) for ot, wp in walls]
        ends.sort(key=lambda e: -e[0])
        face = (ends[0][1], ends[-1][1]) if walls else None
        return _Cut(sub[idx], nrm, face, [e for _, e in ends])

    def clear_voids(self, offsets, cap_reach: float):
        """
        The CLEAR space of each opening across the full wall assembly:
        between the clear-width lines (cut ± cap reach), from the
        outermost to the innermost full wall. Returns
        [(ring, line_a, line_b)]. Used by wall networks so that other
        walls' material cannot fill a doorway. Thick walls only.
        """
        walls = [(0.0, None)] + [(ot.distance, (ot, d)) for ot, d in offsets
                                 if len(d.sample_points()) >= 2]
        if len(walls) < 2:
            return []
        hi = max(walls, key=lambda w: w[0])
        lo = min(walls, key=lambda w: w[0])
        if hi[0] - lo[0] <= 1e-9:
            return []
        out = []
        for a, b in self.opening_removed:     # trims leave no clear void
            ca = a + cap_reach if (self.closed or a > 1e-9) else a
            cb = b - cap_reach if (self.closed or b < self.L - 1e-9) else b
            if cb - ca <= 1e-6:
                continue
            subs = []
            for _, w in (hi, lo):
                if w is None:
                    subs.append(_sub_polyline(self.src_pts, self.cum, ca, cb,
                                              self.closed))
                else:
                    ot, d = w
                    pts = d.sample_points()
                    subs.append(self._offset_piece(
                        ot, pts, _cum_lengths(pts, d.closed), d.closed,
                        ca, cb, True, True))
            if any(len(s) < 2 for s in subs):
                continue
            o, i = subs
            out.append((o + list(reversed(i)), [o[0], i[0]], [o[-1], i[-1]]))
        return out

    def in_opening(self, q: Vec2) -> bool:
        """Does q fall inside an opening's cross-section of the wall?
        (Its nearest-point projection onto the source lies in a removed
        arc-length interval.)"""
        s = _project_to_polyline(q, self.src_pts, self.cum, self.closed)[0]
        for a, b in self.removed:
            if a - 1e-9 <= s <= b + 1e-9:
                return True
            if self.closed and a - 1e-9 <= s + self.L <= b + 1e-9:
                return True
        return False


def _seg_intersect_param(a: Vec2, b: Vec2, c: Vec2, d: Vec2,
                         tol: float = 1e-9):
    """Intersection of segments a-b and c-d → (t on a-b, u on c-d) or None."""
    r = Vec2(b.x - a.x, b.y - a.y)
    q = Vec2(d.x - c.x, d.y - c.y)
    cross = r.x * q.y - r.y * q.x
    if abs(cross) < 1e-12:
        return None
    t = ((c.x - a.x) * q.y - (c.y - a.y) * q.x) / cross
    u = ((c.x - a.x) * r.y - (c.y - a.y) * r.x) / cross
    if -tol <= t <= 1 + tol and -tol <= u <= 1 + tol:
        return max(0.0, min(1.0, t)), max(0.0, min(1.0, u))
    return None


def _clip_lattice_by_openings(pts: list[Vec2], plans: list[_OpeningPlan]
                              ) -> list[list[Vec2]]:
    """
    Remove every part of a lattice polyline that lies inside an opening.

    Each segment is split where it crosses an opening cut — the cut FACE
    (outermost → innermost wall endpoint, i.e. the flat end line), or that
    line continued past the assembly for lattice reaching beyond it (the
    source normal line when the wall has no offsets). Sub-segments whose
    midpoint lies inside an opening are dropped, so no lattice crosses,
    bridges or protrudes into an opening.

    A surviving piece that ends on a cut face is registered as a landing
    on that cut; the cap builder then joins it to the end geometry (real
    contact with the end wall, not invented connectivity).
    """
    if len(pts) < 2:
        return [list(pts)]
    band = 1.0 + max(_project_to_polyline(p, pl.src_pts, pl.cum, pl.closed)[1]
                     for pl in plans for p in pts)
    lines = []          # (c, d, cut or None)
    for pl in plans:
        for cut in pl.cuts:
            if cut.face is not None:
                f0, f1 = cut.face
                lines.append((f0, f1, cut))
                # The face line continued beyond the wall assembly.
                dn = (f1 - f0).normalized()
                lines.append((f0 - dn * band, f0, None))
                lines.append((f1, f1 + dn * band, None))
            else:
                p, n = cut.point, cut.normal
                lines.append((Vec2(p.x - n.x * band, p.y - n.y * band),
                              Vec2(p.x + n.x * band, p.y + n.y * band), None))

    def inside(q: Vec2) -> bool:
        return any(pl.in_opening(q) for pl in plans)

    pieces: list[tuple[list[Vec2], object, object]] = []
    cur: list[Vec2] = []
    cur_cut = None

    def flush(end_cut):
        nonlocal cur, cur_cut
        if len(cur) >= 2:
            pieces.append((cur, cur_cut, end_cut))
        cur, cur_cut = [], None

    for a, b in zip(pts, pts[1:]):
        brk = [[0.0, a, None], [1.0, b, None]]
        for c, d, cut in lines:
            r = _seg_intersect_param(a, b, c, d)
            if r is None:
                continue
            t, u = r
            x = c.lerp(d, u)
            if cut is not None:
                # Snap onto an exact wall endpoint so graph nodes merge.
                for e in cut.ends:
                    if x.dist(e) < 1e-7:
                        x = e
            for bp in brk:
                if bp[1].dist(x) < 1e-9:
                    if cut is not None:
                        bp[2] = cut
                    break
            else:
                brk.append([t, x, cut])
        brk.sort(key=lambda bp: bp[0])
        for (t0, p0, c0), (t1, p1, c1) in zip(brk, brk[1:]):
            if p0.dist(p1) < 1e-12:
                continue
            if inside(p0.lerp(p1, 0.5)):
                flush(c0)
                continue
            if not cur:
                cur, cur_cut = [p0], c0
            cur.append(p1)
    flush(None)

    out = []
    for piece, sc, ec in pieces:
        for end_pt, cut in ((piece[0], sc), (piece[-1], ec)):
            if cut is not None and cut.face is not None and \
                    all(end_pt.dist(e) > 1e-9 for e in cut.ends):
                cut.landings.append(end_pt)
        out.append(piece)
    return out


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
    corner_radius: float = 0.0        # global fillet radius; 0 = sharp corners
    cap_style: str = 'flat'           # 'flat' | 'rounded_corners' | 'full_round'
    cap_corner_radius: float = 0.0    # fillet radius for 'rounded_corners' cap
    openings: list[Opening] = field(default_factory=list)
    trims: list[Trim] = field(default_factory=list)
    region_overrides: list[RegionOverride] = field(default_factory=list)
    infills: list[RegionInfill] = field(default_factory=list)
    junction_style: str = 'miter'     # default for every junction corner
    junction_radius: float = 0.0      # radius when junction_style == 'round'
    junction_overrides: list[JunctionSetting] = field(default_factory=list)
    network_walls: list[NetworkWall] = field(default_factory=list)
    wall_systems: list[WallSystem] = field(default_factory=list)
    wall_relations: list[WallRelation] = field(default_factory=list)
    # return_paths ("Infill repair" in the UI): local continuity repair of
    # the wide-region field fallback (route_plan.repair); motif wall
    # lattices and solid infill do not use it. prefer_closed: prefer a
    # closed (start = end) layer route when it is cheap.
    return_paths: bool = True
    prefer_closed: bool = True
    # MATERIAL / BEAD (material.py): the physical bead deposited around each
    # printable centerline. First pass: carried, not yet used by geometry.
    material: MaterialSpec = field(default_factory=MaterialSpec)
    # ROUTE ORIGINS: where a CLOSED printable component's circuit begins and
    # returns, chosen by the designer — [{'strand': printable strand id,
    # 'u': fraction of its length}] (resolve_route_origins). A routing
    # preference only: never changes geometry.
    route_origins: list = field(default_factory=list)
    # LATTICE REFERENCE (layer_design.py; None = normal generation): for a
    # DERIVED layer design, {infill id: [polyline]} — the parent design's
    # resolved wall lattice. A wall infill found here is NOT regenerated: the
    # parent's stitches are kept exactly and only clipped to this design's
    # material (unchanged XY geometry keeps unchanged XY print structure).
    lattice_reference: Optional[dict] = None
    lattice_stations: Optional[dict] = None   # infill id → [(Vec2, Vec2)] lineage jamb lines
    lattice_lineage: Optional[dict] = None    # infill id → shared-lattice diagnostics (layer_design)
    lattice_track: Optional[dict] = None      # infill id → (reference track, map): cross-Z station tracking

    def _corner_r(self, p: Path) -> float:
        """A source's own Corner R (legacy fallback: layer-wide value)."""
        r = getattr(p, 'corner_radius', None)
        return max(0.0, float(r if r is not None else self.corner_radius))

    def _junction_radius(self, key: str) -> float:
        js = next((j for j in self.junction_overrides if j.key == key), None)
        treatment = js.treatment if js else self.junction_style
        radius = js.radius if js else self.junction_radius
        return max(0.0, float(radius)) if treatment == 'round' else 0.0

    def _path_by_id(self, pid: str) -> Optional[Path]:
        return next((p for p in self.source_paths if p.id == pid), None)

    def _normalized_cap_style(self) -> str:
        """
        Normalize cap_style aliases:
          'round' (legacy) → 'full_round'
          'rounded' → 'rounded_corners'
        """
        s = (self.cap_style or 'flat').lower()
        if s == 'round':
            return 'full_round'
        if s == 'rounded':
            return 'rounded_corners'
        return s

    def _anchor(self, inf) -> str:
        """The source an infill is anchored to (an inner face absorbed into
        an opened two-face wall anchors to that wall's outer face)."""
        return getattr(self, '_absorbed', {}).get(inf.path_id, inf.path_id)

    def _opening_partners(self, sources, processed, offsets_by_source) -> dict:
        """Two-face wall assemblies whose faces are two closed SOURCE paths
        → {outer id: inner id}. Only explicit links count: a wall
        relationship, or a parametric InsetPath and its parent when the
        outer one carries WALL infill (the material between them is
        declared a wall). (Openings in a wall-infill region with arbitrary
        voids are handled as material subtraction: _region_openings.) A
        face that has its own offsets (its own assembly) is never
        absorbed."""
        by_id = {p.id: p for p in sources}
        cand = []
        for rel in self.wall_relations:
            if (getattr(self, '_relation_status', {}).get(rel.id) or {}).get('status') == 'ok':
                cand.append((rel.outer_id, rel.inner_id))
        wall_regions = {inf.path_id for inf in self._all_infills() if inf.kind == 'wall'}
        for p in sources:
            if isinstance(p, InsetPath) and p.parent_id in by_id:
                o, i = (p.parent_id, p.id) if p.mode == 'inset' else (p.id, p.parent_id)
                if o in wall_regions:
                    cand.append((o, i))
        out, used = {}, set()
        trimmed = getattr(self, '_trimmed', set())
        for o, i in cand:
            if o in used or i in used or o not in by_id or i not in by_id:
                continue
            if o in trimmed or i in trimmed:
                continue        # a trimmed boundary is no longer a closed face
            if not (by_id[o].closed and by_id[i].closed) or offsets_by_source.get(i):
                continue
            if len(processed.get(o, ())) < 3 or len(processed.get(i, ())) < 3:
                continue
            out[o] = i
            used |= {o, i}
        return out

    def _region_openings(self, sources, processed, offsets_by_source, partners) -> dict:
        """OPENINGS IN A WALL MATERIAL REGION. A WALL infill on a closed
        single-bead boundary declares its inside, minus the closed voids
        nested in it, to be wall material (Region: Rect 1 · Voids: Rect 2,
        Rect 3). An opening placed on that boundary or on one of the voids
        is a SUBTRACTION from that same material: a corridor from the
        clicked face through the material to the opposite face (local
        inward normal, nearest boundary, within the lattice's wall depth
        WIDE × spacing). Both faces get a gap; the corridor's two sides
        are CUT FACES (printed, flat). The region itself stays defined by
        the FULL closed rings — the corridor is a void subtracted from it,
        never new wall topology. Explicitly linked two-path walls
        (relationship / inset) keep their assembly treatment."""
        import wall_lattice as WL
        by_id = {p.id: p for p in sources}
        linked = set(partners) | set(partners.values())
        nest = self._nesting(processed)
        out = {'cuts': {}, 'faces': {}, 'voids': {}, 'rings': {}, 'status': {}, 'handled': set()}
        trimmed = getattr(self, '_trimmed', set())
        for inf in self._all_infills():
            O = inf.path_id
            if inf.kind != 'wall' or O not in by_id or not by_id[O].closed or O in linked \
                    or O in trimmed \
                    or offsets_by_source.get(O) or len(processed.get(O, ())) < 3:
                continue
            voids = [k for k, par in nest.items() if par == O and k in by_id and
                     not offsets_by_source.get(k) and k not in linked and len(processed.get(k, ())) >= 3]
            members = [O] + voids
            ops = [o for o in self.openings if o.source_path_id in members and o.id not in out['handled']]
            if not ops:
                continue
            rings = {pid: processed[pid] for pid in members}
            outer = processed[O]

            def in_material(q, outer=outer, voids=voids):
                return _point_in_polygon(q, outer) and not any(_point_in_polygon(q, processed[v]) for v in voids)
            depth = WL.WIDE * float(inf.params.get('spacing', 20.0))
            cuts: dict = {}
            faces, polys = [], []
            for pid in members:
                mine = [o for o in ops if o.source_path_id == pid]
                if not mine:
                    continue
                L = _cum_lengths(processed[pid], True)[-1]
                for (a, b) in _opening_removed_intervals(mine, L, True):
                    if (a, b) == (0.0, L):
                        for o in mine:
                            out['status'][o.id] = 'opening covers the whole boundary'
                        continue
                    cor = _region_corridor(pid, a, b, rings, in_material, depth)
                    if cor is None:
                        for o in mine:
                            out['status'].setdefault(o.id, 'no opposite wall face within the wall depth — not cut')
                        continue
                    gid, gint, poly, fcs = cor
                    cuts.setdefault(pid, []).append((a, b))
                    cuts.setdefault(gid, []).append(gint)
                    polys.append(poly)
                    faces.extend(fcs)
                    for o in mine:
                        out['status'][o.id] = 'ok'
            for o in ops:
                out['handled'].add(o.id)
            if not polys:
                continue
            # cut faces lying inside ANOTHER corridor are interior to a
            # merged doorway (an opening placed from both faces): dropped
            keep = []
            for j, f in enumerate(faces):
                m = f[0].lerp(f[1], 0.5)
                if any(min(f[0].dist(g[0]) + f[1].dist(g[1]), f[0].dist(g[1]) + f[1].dist(g[0])) < 1e-6
                       for g in keep):
                    continue                      # the same face from both sides
                inside_other = False
                for k, pl in enumerate(polys):
                    if k == j // 2 or not _point_in_polygon(m, pl):
                        continue
                    cum_pl = _cum_lengths(pl, True)
                    if _project_to_polyline(m, pl, cum_pl, True)[1] > 1e-6:
                        inside_other = True       # interior of a merged doorway
                if not inside_other:
                    keep.append(f)
            for pid, raw in cuts.items():
                L = _cum_lengths(processed[pid], True)[-1]
                out['cuts'][pid] = _merge_closed_intervals(raw, L)
            out['faces'][O] = keep
            out['voids'][O] = polys
            out['rings'][O] = [processed[O]] + [processed[v] for v in voids]
        return out

    def _partner_openings(self, part_id, prim, processed) -> list:
        """Openings placed on the inner face, re-expressed on the outer
        face's arc length (the nearest point of its centre)."""
        out = []
        pts_i, pts_o = processed[part_id], processed[prim.id]
        cum_i, cum_o = _cum_lengths(pts_i, True), _cum_lengths(pts_o, prim.closed)
        for o in self.openings:
            if o.source_path_id != part_id:
                continue
            c, _ = _locate_s(pts_i, cum_i, o.center_s % cum_i[-1], True)
            s_o = _project_to_polyline(c, pts_o, cum_o, prim.closed)[0]
            out.append(Opening(o.id, prim.id, s_o, o.width, o.end_treatment,
                               o.z_min, o.z_max, o.label))
        return out

    def _partner_face(self, prim, part_id, processed):
        """The inner face as one more wall of the outer face's assembly:
        (wall, DerivedPath) with the wall's signed distance (left of the
        outer face's direction = +), measured as the mean gap."""
        pts_o, pts_i = processed[prim.id], processed[part_id]
        cum_o = _cum_lengths(pts_o, prim.closed)
        n = len(pts_o)
        tot, sgn = 0.0, 0.0
        for q in pts_i:
            s_, d, foot = _project_to_polyline(q, pts_o, cum_o, prim.closed)
            _, k = _locate_s(pts_o, cum_o, s_, prim.closed)
            a, b = pts_o[k], pts_o[(k + 1) % n]
            sgn += (b.x - a.x) * (q.y - a.y) - (b.y - a.y) * (q.x - a.x)
            tot += d
        dist = (tot / len(pts_i)) * (1.0 if sgn >= 0 else -1.0)
        part = self._path_by_id(part_id)
        derived = DerivedPath(list(pts_i), closed=True, id=part_id, role=part.role,
                              label=part.label, source_id=part_id,
                              treatment_id='opening_cut')
        return _FaceWall(part_id, dist, prim.id), derived

    def _cap_style_of(self, sid) -> str:
        """End treatment of a wall system: the layer's cap style, except a
        physical RETURN-LANE (two-pass) wall, which turns round in a
        semicircle (its "cap" is the nozzle's U-turn, not architecture)."""
        if sid in getattr(self, '_return_lanes', ()):
            return 'full_round'
        return self._normalized_cap_style()

    def _opening_cap_reach(self, offsets, sid=None) -> float:
        """How far this wall system's end treatment protrudes past a cut
        face: the effective cap radius for its total thickness W."""
        dists = [0.0] + [ot.distance for ot, d in offsets
                         if len(d.sample_points()) >= 2]
        W = max(dists) - min(dists)
        style = self._cap_style_of(sid)
        if W <= 1e-9 or style == 'flat':
            return 0.0
        if style == 'full_round':
            return W / 2.0
        return min(max(0.0, self.cap_corner_radius), W / 2.0)

    def effective_paths(self) -> list[Path]:
        """
        Canonical pipeline:
          raw source → _processed_source_pts (ONE per source)
            → every offset of this source uses the same processed pts
              (offsets are always computed on the FULL, uncut wall)
            → openings cut the whole wall assembly into open pieces
            → end treatment per wall-system piece (not per offset); the
              same builder closes original open ends and opening faces
            → lattice built on the full walls, clipped out of openings,
              its cut ends landing on the opening caps
            → wall networks: systems that touch / cross are combined into
              one printable region (network.py); attached ends splice
              into their host instead of being capped
        Sources without openings produce exactly the pre-opening output,
        and systems that touch nothing produce exactly the pre-network
        output.
        """
        return self._build_effective()[0]

    def network_summary(self) -> dict:
        """Derived wall-network topology of this layer (for the UI/API)."""
        return self._build_effective()[1]['network']

    def _build_effective(self):
        """(effective paths, meta). meta['cls'] maps id(path) → bead class
        (network.FACE / INTERNAL / WIRE) for retrace costs; meta['network']
        summarises the derived wall networks."""
        import network as N

        result: list[Path] = []
        cls_of: dict[int, str] = {}           # id(path object) → bead class
        elem_of: dict[int, str] = {}          # id(path object) → element

        # 1) Canonical processed-source polylines (ONE per visible source path)
        # Parametric InsetPaths are design geometry derived from their
        # parent's processed polyline: evaluated after it (chains allowed).
        self._relation_status = {}
        by_id = {p.id: p for p in self.source_paths}
        for rel in self.wall_relations:
            self._relation_status[rel.id] = rel.apply(by_id)
        self._resolve_insets()
        processed: dict[str, list[Vec2]] = {}
        source_paths_in_order: list[Path] = []
        for p in self.source_paths:
            if not p.visible or (isinstance(p, InsetPath) and len(p.points) < 3):
                continue
            processed[p.id] = _processed_source_pts(p, self._corner_r(p))
            source_paths_in_order.append(p)

        # 1b) Open ends within SNAP_TOL of another source are snapped onto
        # it (a near-miss from float noise is a junction, not a gap).
        snapped = self._snap_source_ends(source_paths_in_order, processed,
                                         N.SNAP_TOL)

        # 1c) TRIMS: sections of sources between their contacts with other
        # sources (design geometry, untrimmed), and the sections the layer's
        # trims name. A trimmed source keeps its parametric identity; its
        # removed intervals cut its whole wall assembly in stage 4 (as open
        # pieces with FREE ends) and it no longer bounds a closed region.
        import trim as TR
        trim_secs = TR.sections(source_paths_in_order, processed)
        trim_removed, trim_status = TR.resolve(self.trims, trim_secs, set(processed))
        self._trimmed = {pid for pid, iv in trim_removed.items() if iv}

        # 2) Full (uncut) source geometry = the processed polyline. If
        # rounding (or an end snap) changed it we wrap it in a DerivedPath;
        # otherwise the parametric source path flows through unchanged
        # (identity preserved). Either way every consumer — lattice,
        # routing, junctions — uses the processed polyline (pts_of), the
        # same one the offsets and the canvas use. (Curves used to be
        # routed at their 64-point default sampling instead.)
        full_sources: dict[str, Path] = {}
        geom_sources: dict[str, Path] = {}      # what lattice generators see
        pts_of: dict[int, list[Vec2]] = {}      # id(path) → printed polyline
        for p in source_paths_in_order:
            pts = processed[p.id]
            if p.id in snapped or (self._corner_r(p) > 0.0 and
                                   _eligible_for_rounding(p)):
                full_sources[p.id] = DerivedPath(
                    pts, closed=p.closed, id=p.id, role=p.role,
                    label=p.label, source_id=p.id,
                    treatment_id='snap' if p.id in snapped else 'round')
                geom_sources[p.id] = full_sources[p.id]
            else:
                full_sources[p.id] = p
                pts_of[id(p)] = pts
                geom_sources[p.id] = p if _same_polyline(
                    _dedupe_polyline(p.sample_points(), p.closed), pts) \
                    else DerivedPath(pts, closed=p.closed, id=p.id,
                                     role=p.role, label=p.label,
                                     source_id=p.id, treatment_id='sampled')

        # 3) Full offset-derived paths. All offsets of the same source use
        # the SAME processed polyline — no re-sampling or re-rounding.
        offsets_by_source: dict[str, list[tuple[OffsetTreatment, DerivedPath]]] = {}
        all_offsets: list[DerivedPath] = []
        source_of: dict[str, str] = {p.id: p.id for p in source_paths_in_order}
        source_nets = self._source_networks(N, source_paths_in_order, processed)
        wall_offs, ref_only = self._wall_offsets(source_paths_in_order,
                                                 processed, source_nets)
        for ot in list(self.offset_treatments) + wall_offs:
            src = self._path_by_id(ot.source_path_id)
            if src is None or src.id not in processed:
                continue
            derived = ot.generate(processed[src.id], src.closed)
            all_offsets.append(derived)
            offsets_by_source.setdefault(src.id, []).append((ot, derived))
            source_of[ot.id] = src.id
        # each wall's OWN face lines (untrimmed by the network): a generated wall's
        # band in a mixed / junction region follows them round corners that lie
        # hidden inside another wall's band (see _split_wall_systems)
        self._src_faces = {}
        for sid_, lst in offsets_by_source.items():
            src = self._path_by_id(sid_)
            faces = [(list(d_.sample_points()), bool(src.closed)) for _, d_ in lst if len(d_.sample_points()) >= 2]
            if len(faces) == 1 and sid_ in processed:
                faces.append((list(processed[sid_]), bool(src.closed)))     # (aligned wall: the path is a face)
            self._src_faces[sid_] = faces

        # 3b) PHYSICAL: a return-lane (two-pass) branch ending on a SINGLE-BEAD
        # host joins it: the host is cut exactly across the branch mouth
        # (between the two lanes' landings — the trim machinery, exact shared
        # points) and the branch has no U-turn there, so host → lane → U-turn
        # → lane → host is one closed circuit (no degree-3 landings).
        self._wire_attached = []               # (source id, end point) without a cap
        self._mouths = []                      # (branch id, landing, landing)
        self._mouth_cut = set()
        if self.material.physical:
            for host_id, iv in self._mouth_cuts(N, source_paths_in_order, processed,
                                                offsets_by_source).items():
                trim_removed.setdefault(host_id, []).extend(iv)
                self._mouth_cut.add(host_id)

        # 4) Openings → how each affected wall assembly is cut. A wall whose
        # two faces are two SOURCE paths (an explicit wall relationship, or
        # a parametric inset bounding a wall-infill region with its parent)
        # is ONE assembly: an opening on either face cuts both. The inner
        # face joins the outer's assembly as one more wall (like an offset),
        # so the cut, both cap faces and the infill clipping are exactly
        # those of a Wall-Thickness wall. Only done where an opening
        # actually cuts; otherwise the two faces stay separate sources.
        plans: dict[str, _OpeningPlan] = {}
        self._absorbed = {}
        partners = self._opening_partners(source_paths_in_order, processed,
                                          offsets_by_source)
        region_ops = self._region_openings(source_paths_in_order, processed,
                                           offsets_by_source, partners)
        self._region_ops = region_ops
        order = sorted(source_paths_in_order, key=lambda q: q.id not in partners)
        for p in order:
            if p.id in self._absorbed:
                continue
            ops = [o for o in self.openings if o.source_path_id == p.id
                   and o.id not in region_ops['handled']]
            part = partners.get(p.id)
            extra = []
            if part is not None:
                ops = ops + self._partner_openings(part, p, processed)
                if ops:
                    extra = [self._partner_face(p, part, processed)]
            trims = trim_removed.get(p.id, [])
            if (ops or trims) and len(processed[p.id]) >= 2:
                offs = offsets_by_source.get(p.id, []) + extra
                plan = _OpeningPlan(processed[p.id], p.closed, offs, ops,
                                    self._opening_cap_reach(offs, p.id), trims)
                if plan.removed:
                    plans[p.id] = plan
                    for ot, derived in extra:
                        offsets_by_source.setdefault(p.id, []).append((ot, derived))
                        all_offsets.append(derived)
                        source_of[part] = p.id
                        self._absorbed[part] = p.id

        # 5) Lattice — generated on the FULL walls (so generator validity is
        # unchanged), then clipped out of any opening of the walls it uses.
        full_by_id = dict(geom_sources)
        full_by_id.update({d.id: d for d in all_offsets})
        lattice_paths: list[Path] = []
        for li in self.lattice_instances:
            pa = full_by_id.get(li.path_a_id)
            pb = full_by_id.get(li.path_b_id)
            gen = GENERATORS.get(li.generator_name)
            if pa is None or pb is None or not gen:
                continue
            generated = li.generate(pa, pb, gen)
            li_plans = [plans[sid] for sid in
                        dict.fromkeys((source_of.get(li.path_a_id),
                                       source_of.get(li.path_b_id)))
                        if sid in plans]
            if not li_plans:
                out = generated
            else:
                out = []
                for dp in generated:
                    for k, piece in enumerate(_clip_lattice_by_openings(
                            dp.sample_points(), li_plans)):
                        out.append(DerivedPath(
                            piece, closed=False, role=dp.role, label=dp.label,
                            source_id=dp.source_id,
                            treatment_id=dp.treatment_id,
                            id=f'{dp.id}~{k}'))
            for dp in out:
                cls_of[id(dp)] = N.INTERNAL
                elem_of[id(dp)] = f'L:{li.id}'
            lattice_paths.extend(out)

        # 6) Wall-system pieces (the unit of end treatment and of networks).
        net_pieces: list = []
        for p in source_paths_in_order:
            if p.id in self._absorbed:
                continue        # a face of another source's assembly now
            plan = plans.get(p.id)
            offs = [(ot, d) for ot, d in offsets_by_source.get(p.id, [])
                    if len(d.sample_points()) >= 2]
            rcut = region_ops['cuts'].get(p.id)
            if rcut is not None:
                # a face of a wall-material region with a doorway: open
                # pieces whose ends are CUT ends (never joins / branches)
                pts_r = processed[p.id]
                cum_r = _cum_lengths(pts_r, True)
                for k, (a, b) in enumerate(_surviving_intervals(rcut, cum_r[-1], True)):
                    sub = _sub_polyline(pts_r, cum_r, a, b, True)
                    if len(sub) >= 2:
                        net_pieces.append(N.NetPiece(
                            p.id, f'{p.id}~{k}', False, [(0.0, sub, f'{p.id}~{k}')], ['cut', 'cut']))
                continue
            if plan is None:
                walls = [(0.0, processed[p.id], p.id)] + \
                        [(ot.distance, d.sample_points(), d.id) for ot, d in offs]
                walls.sort(key=lambda w: -w[0])
                net_pieces.append(N.NetPiece(
                    p.id, p.id, p.closed, walls,
                    [None, None] if p.closed else ['free', 'free']))
                continue
            for k, pc in enumerate(plan.pieces):
                walls = [(0.0, pc.src_pts, f'{p.id}~{k}')] + \
                        [(ot.distance, wp, f'{ot.id}~{k}') for ot, wp in pc.walls]
                walls.sort(key=lambda w: -w[0])
                # opening faces are CUT ends; path ends and trim ends are
                # FREE (a trimmed wall may join the wall it now ends on)
                net_pieces.append(N.NetPiece(
                    p.id, f'{p.id}~{k}', False, walls,
                    ['cut' if pc.start_cut and not pc.start_cut.trim else 'free',
                     'cut' if pc.end_cut and not pc.end_cut.trim else 'free']))
        for pc in net_pieces:
            pc.ref_only = pc.sys_id in ref_only
        piece_by_key = {pc.key: pc for pc in net_pieces}
        wall_class: dict[str, str] = {}
        for pc in net_pieces:
            for i, (_, _, bid) in enumerate(pc.walls):
                wall_class[bid] = pc.wall_class(i)

        def _emit(path, sid):
            result.append(path)
            cls_of[id(path)] = wall_class.get(path.id, N.WIRE)
            elem_of[id(path)] = f'S:{sid}'

        # 7) Emit walls. Order: sources, offsets, end treatments, lattice.
        for p in source_paths_in_order:
            if p.id in ref_only or p.id in self._absorbed:
                continue        # centred wall reference / absorbed face
            rcut = region_ops['cuts'].get(p.id)
            if rcut is not None:
                pts_r = processed[p.id]
                cum_r = _cum_lengths(pts_r, True)
                for k, (a, b) in enumerate(_surviving_intervals(rcut, cum_r[-1], True)):
                    sub = _sub_polyline(pts_r, cum_r, a, b, True)
                    if len(sub) >= 2:
                        _emit(DerivedPath(sub, closed=False, id=f'{p.id}~{k}', role=p.role,
                                          label=p.label, source_id=p.id,
                                          treatment_id='opening_cut'), p.id)
                continue
            plan = plans.get(p.id)
            if plan is None:
                _emit(full_sources[p.id], p.id)
                continue
            for k, piece in enumerate(plan.pieces):
                _emit(DerivedPath(
                    piece.src_pts, closed=False, id=f'{p.id}~{k}',
                    role=p.role, label=p.label, source_id=p.id,
                    treatment_id='opening_cut'), p.id)
        # cut faces of doorways through wall-material regions: boundary
        # geometry of the SUBTRACTION (printed), not wall-source geometry
        for anchor, fcs in region_ops['faces'].items():
            for j, f in enumerate(fcs):
                if f[0].dist(f[1]) < 1e-6:
                    continue
                cf = DerivedPath(list(f), closed=False, role='cap', label='opening_face',
                                 source_id=anchor, treatment_id='opening_face',
                                 id=f'{anchor}_cut{j}')
                _emit(cf, anchor)
                cls_of[id(cf)] = N.FACE
        for derived in all_offsets:
            sid = source_of[derived.id]
            plan = plans.get(sid)
            if plan is None:
                _emit(derived, sid)
                continue
            for k, piece in enumerate(plan.pieces):
                for ot, wp in piece.walls:
                    if ot.id == derived.id:
                        _emit(DerivedPath(
                            wp, closed=False, id=f'{ot.id}~{k}',
                            role=derived.role, label=derived.label,
                            source_id=derived.source_id,
                            treatment_id=derived.treatment_id), sid)

        # 8) Wall-system end treatment — ONE cap per end of each wall-system
        # piece (= source + its offsets): original open ends and opening
        # faces alike.
        cap_style = self._normalized_cap_style()
        for src in source_paths_in_order:
            if src.id in self._absorbed:
                continue
            plan = plans.get(src.id)
            if plan is None:
                if src.closed:
                    continue
                ots = offsets_by_source.get(src.id, [])
                if not ots:
                    continue
                pieces = [(src.id, processed[src.id],
                           [(ot.distance, d.sample_points()) for ot, d in ots],
                           ((), ()))]
            else:
                pieces = [
                    (f'{src.id}~{k}', pc.src_pts,
                     [(ot.distance, wp) for ot, wp in pc.walls],
                     (pc.start_cut.landings if pc.start_cut else (),
                      pc.end_cut.landings if pc.end_cut else ()))
                    for k, pc in enumerate(plan.pieces) if pc.walls]
            for pid, src_pts, walls, landings in pieces:
                npc = piece_by_key.get(pid)
                ends = _wall_system_end_pts(src_pts, walls, self._cap_style_of(src.id),
                                            self.cap_corner_radius, landings)
                for which, ((cap_pts, extensions), tag, label) in enumerate(zip(
                        ends, ('_cs', '_ce'), ('cap_start', 'cap_end'))):
                    e_pt = src_pts[0] if which == 0 else src_pts[-1]
                    if any(sid == src.id and e_pt.dist(q) < 1e-9 for sid, q in self._wire_attached):
                        continue        # joins a single-bead host: no U-turn here
                    if cap_pts:
                        cap = DerivedPath(
                            cap_pts, closed=False, role='cap', label=label,
                            source_id=src.id, treatment_id='wall_system',
                            id=pid + tag,
                        )
                        result.append(cap)
                        cls_of[id(cap)] = N.FACE
                        elem_of[id(cap)] = f'S:{src.id}'
                        if npc is not None:
                            npc.caps[which] = cap_pts
                            npc.cap_ids[which].append(cap.id)
                    # Intermediate walls / lattice continuing onto the cap.
                    if src.id in ref_only:      # (not the unprinted reference)
                        ends_src = (src_pts[0], src_pts[-1])
                        extensions = [x for x in extensions
                                      if all(x[0].dist(q) > 1e-9 for q in ends_src)]
                    for k, ext in enumerate(extensions):
                        xp = DerivedPath(
                            ext, closed=False, role='cap', label=label + '_ext',
                            source_id=src.id, treatment_id='wall_system',
                            id=f'{pid}{tag}_x{k}',
                        )
                        result.append(xp)
                        cls_of[id(xp)] = N.INTERNAL
                        elem_of[id(xp)] = f'S:{src.id}'
                        if npc is not None:
                            npc.cap_ids[which].append(xp.id)

        result.extend(lattice_paths)

        # 9) Wall networks.
        lat_bounds = {li.id: {source_of.get(li.path_a_id),
                              source_of.get(li.path_b_id)} - {None}
                      for li in self.lattice_instances}
        result, network = self._apply_networks(
            N, result, cls_of, elem_of, pts_of, net_pieces, lat_bounds,
            full_by_id, plans, offsets_by_source, processed)
        regions = network.pop('_rings', [])
        jobs = network.pop('_infill_jobs', [])
        network['wall_systems'] = []
        network['wall_system_membership'] = dict(getattr(self, '_wall_system_of', {}) or {})
        network['source_walls'] = dict(getattr(self, '_source_walls', {}) or {})
        if getattr(self, '_wall_systems', None):
            result, jobs = self._apply_wall_systems(N, result, cls_of, regions, jobs, network)
        network['reference_only'] = sorted(ref_only)
        network['return_lanes'] = sorted(getattr(self, '_return_lanes', ()))
        network['opening_status'] = dict(region_ops['status'])
        # faces cut on behalf of a doorway (room faces, absorbed inner faces)
        # are drawn from the backend pieces, like network-trimmed sources
        network['modified_sources'] = sorted(set(network.get('modified_sources', [])) |
                                             set(region_ops['cuts']) | set(self._absorbed) |
                                             self._trimmed)
        # trimmable sections (for the Trim tool's hover) and each trim's state
        network['trim_sections'] = [s.to_dict() for secs in trim_secs.values() for s in secs]
        network['trims'] = trim_status
        network['wall_relations'] = dict(getattr(self, '_relation_status', {}))
        network['derived_sources'] = {
            p.id: [[q.x, q.y] for q in p.points]
            for p in self.source_paths if isinstance(p, InsetPath)}
        network['source_networks'] = [
            {'id': f'N{k + 1}', 'sources': ids,
             'wall': (lambda w: w.to_dict() if w else None)(
                 self._network_wall_for(ids))}
            for k, ids in enumerate(n for n in source_nets if len(n) > 1)]
        meta = {'cls': cls_of, 'pts': pts_of, 'network': network,
                'regions': regions}
        # 10) Region infill. WALL regions: route-aware stitching motifs
        # (wall_lattice.plan), continuous by construction. Regions too wide
        # to be a wall fall back to the structural FIELD (infill.py web,
        # degree-capped selection) whose continuity is repaired LOCALLY
        # (route_plan.repair). SOLID regions: solid.py area fill.
        network['route_plan'] = None
        if jobs:
            import infill as IF
            import route_plan as RP
            sys_path = os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto')
            if sys_path not in sys.path:
                sys.path.insert(0, sys_path)
            from graph import build_graph
            rl = self.to_routing_layer(result, meta)
            Gf = build_graph(rl)
            segs = [((a.x, a.y), (b.x, b.y)) for s in rl.strands
                    for a, b in s.segments()]
            odd = [Vec2(*n) for n in Gf.nodes if Gf.degree(n) % 2 == 1]
            webs = []
            import wall_lattice as WL
            lattice = {}
            for job in jobs:
                inf = job['infill']
                if inf.kind == 'solid':
                    continue                     # area fill: solid.py below
                pattern = inf.pattern if inf.pattern in IF.PATTERNS else 'zigzag'
                spacing = float(inf.params.get('spacing', 20.0))
                # WALL regions: route-aware stitching motifs (wall_lattice);
                # wide regions (areas, not walls) keep the field generator +
                # local repair below.
                ref = (self.lattice_reference or {}).get(inf.id)
                if ref is not None:
                    # inherited lattice: the parent's stitches clipped to
                    # this design's material — local change only at the cut
                    pieces = _clip_to_rings(ref, job['rings'])
                    for j, poly in enumerate(pieces):
                        dp = DerivedPath(poly, closed=False, role='lattice',
                                         label=inf.pattern, source_id=inf.id,
                                         treatment_id='infill', id=f"{job['key']}.r{j}")
                        cls_of[id(dp)] = N.INTERNAL
                        result.append(dp)
                    rep_ = lattice.setdefault(inf.id, {'motif': False, 'inherited': True, 'regions': [],
                                                      'lineage': (self.lattice_lineage or {}).get(inf.id)})
                    rep_['regions'].append({'inherited': True, 'pieces': len(pieces),
                                            'reference_polylines': len(ref)})
                    continue
                phys = self.material.physical
                lp = WL.plan(job['rings'], spacing, pattern, inf.variation_index,
                             self.prefer_closed or phys,
                             float(inf.params.get('max_unsupported', 0) or 0) or None,
                             contact=self.material.contact_separation() if phys else 0.0,
                             closed=phys,
                             stations=(self.lattice_stations or {}).get(inf.id),
                             track=((self.lattice_track or {}).get(inf.id) or (None, None))[0],
                             track_map=((self.lattice_track or {}).get(inf.id) or (None, None))[1],
                             bead=self.material.bead_width if phys else 0.0,
                             truss=inf.params if pattern == 'truss' else None)
                if lp is not None:
                    for j, poly in enumerate(lp.polylines):
                        dp = DerivedPath(poly, closed=False, role='lattice',
                                         label=inf.pattern, source_id=inf.id,
                                         treatment_id='infill', id=f"{job['key']}.{j}")
                        cls_of[id(dp)] = N.INTERNAL
                        result.append(dp)
                    rep_ = lattice.setdefault(inf.id, {'motif': True, 'regions': [],
                                                      'lineage': (self.lattice_lineage or {}).get(inf.id)})
                    lp.report['rings'] = [[[q.x, q.y] for q in ring] for ring in job['rings']]
                    rep_['regions'].append(lp.report)
                    continue
                lattice.setdefault(inf.id, {'motif': False, 'regions': []})['regions'].append(
                    {'fallback': 'field', 'target': spacing})
                web = IF.build_web(job['rings'], spacing, inf.variation_index, forced=odd)
                if web is not None:
                    webs.append((job, web, IF.select(web)))
            network['lattice'] = lattice
            for info in network['infills']:
                lr = lattice.get(info['id'])
                if lr:
                    ps = [(r['pitch_min'], r['pitch_max']) for r in lr['regions']
                          if r.get('pitch_min') is not None]
                    regs = [r for r in lr['regions'] if 'runs' in r]
                    info['lattice'] = {
                        'motif': lr['motif'],
                        'pitch_min': min((a for a, _ in ps), default=None),
                        'pitch_max': max((b for _, b in ps), default=None),
                        'target': float(next((x.params.get('spacing', 20.0) for x in self._all_infills()
                                              if x.id == info['id']), 20.0)),
                        'max_unsupported': max((r['max_unsupported'] for r in regs), default=None),
                        'max_unsupported_limit': max((r['max_unsupported_limit'] for r in regs), default=None),
                        'corners': sum(run.get('corners', 0) for r in regs for run in r['runs']),
                        # V1 / V2 only move the phase of closed loops and lone
                        # walls; in networks junction coherence fixes it
                        'variation_effective': any(run.get('phase_free', run['motif'] in ('loop', 'lone'))
                                                   for r in regs for run in r['runs']),
                        # the lineage's SHARED lattice (Layer Designs), if any
                        'lineage': lr.get('lineage'),
                        # ADAPTIVE TRUSS: effective pitch / stitch constructions
                        'truss': next((r['truss'] for r in regs if r.get('truss')), None)}
            # Motif lattices are continuous by construction: no repair.
            # Report them in the same shape (0 defects / edits) so route
            # consumers see one plan; open ends = lone runs / open arms.
            motif_regions = [r for v in lattice.values() if v['motif'] for r in v['regions']]
            if motif_regions:
                opens = [sum(2 if r_['motif'] == 'lone' else 1 if r_['motif'] == 'open_end' else 0
                             for r_ in reg['runs']) for reg in motif_regions]
                network['route_plan'] = {
                    'defects': 0, 'repaired_pairs': 0,
                    'route_end_defects': min(2, sum(opens)),
                    'left_to_retrace': 0, 'corrections': [],
                    'closed_components': [o == 0 for o in opens],
                    'motif_regions': len(motif_regions)}
            if self.return_paths and webs:
                rep = RP.repair([(w, S) for _, w, S in webs], Gf, segs,
                                self.prefer_closed)
                prev = network['route_plan'] or {
                    'defects': 0, 'repaired_pairs': 0, 'route_end_defects': 0,
                    'left_to_retrace': 0, 'corrections': [], 'closed_components': []}
                network['route_plan'] = dict(prev, **{
                    'defects': prev['defects'] + rep.defects,
                    'repaired_pairs': prev['repaired_pairs'] + rep.repaired,
                    'route_end_defects': prev['route_end_defects'] + rep.route_end_defects,
                    'left_to_retrace': prev['left_to_retrace'] + rep.left_to_retrace,
                    'corrections': prev['corrections'] + rep.corrections,
                    'closed_components': prev['closed_components'] + rep.closed})
                for k, poly in enumerate(rep.returns):
                    dp = DerivedPath(poly, closed=False, role='lattice',
                                     label='infill_return', source_id='route',
                                     treatment_id='infill_return', id=f'return~{k}')
                    cls_of[id(dp)] = N.INTERNAL
                    result.append(dp)
            for job, web, S in webs:
                inf = job['infill']
                pattern = inf.pattern if inf.pattern in IF.PATTERNS else 'zigzag'
                for j, poly in enumerate(IF.emit(web, S, pattern)):
                    dp = DerivedPath(poly, closed=False, role='lattice',
                                     label=inf.pattern, source_id=inf.id,
                                     treatment_id='infill', id=f"{job['key']}.{j}")
                    cls_of[id(dp)] = N.INTERNAL
                    result.append(dp)
            # SOLID regions: conventional area fill (never the wall repair).
            import solid as SO
            solid_rep = {}
            for job in jobs:
                inf = job['infill']
                if inf.kind != 'solid':
                    continue
                sp = SO.plan(job['rings'], inf.params, inf.pattern)
                for j, poly in enumerate(sp.lines):
                    dp = DerivedPath(poly, closed=False, role='lattice',
                                     label='solid', source_id=inf.id,
                                     treatment_id='solid_infill', id=f"{job['key']}.s{j}")
                    # routing: ONE hand-off per boundary ring (sp.attach);
                    # every other boundary contact is a physical tie the
                    # route passes over (perimeters print as whole loops)
                    dp.join_group = f"solid:{job['key']}"
                    dp.join_points = list(sp.attach)
                    cls_of[id(dp)] = N.INTERNAL
                    result.append(dp)
                for j, c in enumerate(sp.connectors):
                    dp = DerivedPath(c, closed=False, role='lattice',
                                     label='solid_link', source_id=inf.id,
                                     treatment_id='solid_link', id=f"{job['key']}.c{j}")
                    cls_of[id(dp)] = N.INTERNAL
                    result.append(dp)
                for j, ring in enumerate(sp.perimeters):
                    dp = DerivedPath(ring, closed=True, role='cap',
                                     label='solid_perimeter', source_id=inf.id,
                                     treatment_id='solid_perimeter', id=f"{job['key']}.p{j}")
                    cls_of[id(dp)] = N.INTERNAL
                    result.append(dp)
                network.setdefault('solid_regions', []).append({
                    'infill': inf.id, 'angle': float(inf.params.get('angle', 45.0)),
                    'spacing': float(inf.params.get('spacing', 20.0)),
                    'rings': [[[q.x, q.y] for q in ring] for ring in job['rings']]})
                r = solid_rep.setdefault(inf.id, {})
                for k, v in sp.report.items():
                    if not isinstance(v, (int, float)):
                        r[k] = v
                    else:
                        r[k] = max(r.get(k, 0), v) if k in ('max_connector', 'max_unsupported', 'max_unsupported_limit') \
                            else r.get(k, 0) + v
            for info in network['infills']:
                if info['id'] in solid_rep:
                    info['solid'] = solid_rep[info['id']]
        # What each infill's region IS: the boundary that owns it and the
        # closed paths nested in it (by geometry, not creation order).
        nest = self._nesting(processed)
        for info in network.get('infills', []):
            inf = next((x for x in self._all_infills() if x.id == info['id']), None)
            if inf is None:
                continue
            kids = [k for k, par in nest.items() if par == inf.path_id]
            info.update(region=inf.path_id, kind=inf.kind, voids=sorted(kids),
                        islands=sorted(k for k, par in nest.items() if par in kids))
        return result, meta

    def _nesting(self, processed) -> dict:
        """Closed paths → the smallest closed path that contains them (or
        None): the geometric region / void tree."""
        trimmed = getattr(self, '_trimmed', set())     # trimmed: no longer closed
        closed = [p for p in self.source_paths if p.closed and p.id in processed
                  and p.id not in trimmed and len(processed[p.id]) >= 3]
        area = {p.id: abs(_polygon_area(processed[p.id])) for p in closed}
        parent = {}
        for p in closed:
            pts = processed[p.id]
            best = None
            for q in closed:
                if q.id == p.id or area[q.id] <= area[p.id]:
                    continue
                if all(_point_in_polygon(v, processed[q.id]) for v in pts[::max(1, len(pts) // 8)]):
                    if best is None or area[q.id] < area[best]:
                        best = q.id
            parent[p.id] = best
        return parent

    def _resolve_insets(self):
        """Evaluate every InsetPath from its parent (parents first; an inset
        of an inset works). Without a parent the frozen points remain."""
        by_id = {p.id: p for p in self.source_paths}
        done: set = set()
        pending = [p for p in self.source_paths if isinstance(p, InsetPath)]
        for _ in range(len(pending) + 1):
            progress = False
            for p in pending:
                if p.id in done:
                    continue
                parent = by_id.get(p.parent_id)
                if parent is None:
                    done.add(p.id)               # detached: keep frozen shape
                    continue
                if isinstance(parent, InsetPath) and parent.id not in done:
                    continue
                if not parent.closed:
                    p.points = []
                else:
                    ppts = _processed_source_pts(parent, self._corner_r(parent))
                    p.points = p.derive(ppts)
                done.add(p.id)
                progress = True
            if not progress:
                break
        for p in pending:                        # dependency cycle: no shape
            if p.id not in done:
                p.points = []

    def _source_networks(self, N, sources, processed) -> list[list[str]]:
        """Source paths that touch / cross (transitively), before any
        offsets: the networks the designer builds and edits. Ordered by
        the first member's position in the path list."""
        beads = [N.Bead(p.id, processed[p.id], p.closed, N.WIRE, f'S:{p.id}')
                 for p in sources if len(processed[p.id]) >= 2]
        uf = N.UnionFind()
        for b in beads:
            uf.find(b.element)
        for a, b in N.find_contacts(beads):
            uf.union(a, b)
        groups: dict = {}
        for p in sources:
            groups.setdefault(uf.find(f'S:{p.id}'), []).append(p.id)
        return sorted(groups.values(),
                      key=lambda g: [s.id for s in sources].index(g[0]))

    def _network_wall_for(self, ids) -> Optional[NetworkWall]:
        return next((w for w in self.network_walls if w.path_id in ids), None)

    def _apply_wall_systems(self, N, result, cls_of, regions, jobs, network):
        """WALL SYSTEMS (wall_systems.py): a wall-material region whose
        bounding walls choose a non-default system prints that system's paths
        INSTEAD of its skins / caps / Wall Infill. The envelope (faces,
        opening cuts, junctions, caps) is built as usual; its face beads are
        only removed / replaced here.

        A region whose walls use DIFFERENT systems (e.g. a Chained Loop
        ring with an attached line removed from that system) is split by
        wall: each system fills the envelope of its own walls (the region
        with the other walls' faces closed off by a chord across their mouth),
        the Skin + Web walls keep their own face / cap beads, spliced into the
        system's route at their mouth so the piece still prints as one route."""
        import json
        import wall_systems as WS
        faces = [p for p in result if cls_of.get(id(p)) == N.FACE]
        samples = {}
        for p in faces:
            pts = p.sample_points()
            samples[id(p)] = (pts[::max(1, len(pts) // 12)] + [pts[-1]]) if pts else []

        def keyof(sid):
            ws = self._wall_systems.get(sid)
            if ws is None:
                return None
            return self._wall_system_key.get(sid) or json.dumps(ws, sort_keys=True)
        drop, new = set(), []
        bead, contact = self.material.bead_width, self.material.contact_separation()

        def emit(polys, system, k, first, tag=''):
            for j, poly in enumerate(polys):
                closed = len(poly) > 3 and poly[0].dist(poly[-1]) < 1e-6
                dp = DerivedPath(poly[:-1] if closed else poly, closed=closed, role='wall_system',
                                 label=system['type'], source_id=first,
                                 treatment_id='wall_system_path', id=f'{first}~ws{k}{tag}.{j}')
                cls_of[id(dp)] = N.FACE
                new.append(dp)
        for k, rings in enumerate(regions):
            members, msid = [], {}
            for p in faces:
                sm = samples[id(p)]
                if sm and id(p) not in drop and all(
                        min(N.dist_to_polyline(q, r, True) for r in rings) < 0.05 for q in sm):
                    members.append(p)
                    msid[id(p)] = (getattr(p, 'source_id', None) or p.id).split('~')[0]
                    if str(p.id).startswith('junction:'):
                        # a junction FILLET between two walls: in a mixed junction it
                        # belongs to the incoming skin-like wall, not to the host
                        # system's envelope (that system meets the incoming wall at
                        # its REAL material; Junction R is applied there)
                        faces_ = str(p.id)[len('junction:'):].split('#')[0].split('|')
                        known_ = sorted((q.id for q in self.source_paths), key=len, reverse=True)
                        srcs_ = [next((k for k in known_ if f.startswith(k)), None) for f in faces_]
                        srcs_ = [x for x in srcs_ if x is not None]
                        skin_ = [x for x in srcs_ if keyof(x) is None]
                        if skin_ and len({keyof(x) for x in srcs_}) > 1:
                            msid[id(p)] = skin_[0]
                        elif srcs_ and not skin_ and len(set(srcs_)) > 1:
                            # between two GENERATED walls: neither band follows it (the
                            # walls meet by crossing printed paths; no pattern bends round it)
                            msid[id(p)] = '__J'
            sids = set(msid.values()) - {'__J'}
            if not members or all(keyof(s) is None for s in sids):
                continue
            keys = {keyof(s) for s in sids}
            systems_used = set()
            self._split_skin_part = None
            gen_sids = [s_ for s_ in sids if keyof(s_) is not None]

            def partkey(sid_, keyof=keyof):
                """Split key: every GENERATED wall its own band (they meet by
                crossing printed paths), the skin walls together, fillets apart."""
                if sid_ == '__J':
                    return '__J'
                return sid_ if keyof(sid_) is not None else None
            if len(gen_sids) == 1 and len(keys) == 1 and '__J' not in set(msid.values()):
                system = self._wall_systems[next(iter(sids))]
                polys, rep = WS.generate(rings, system, bead, contact, self._seam_hints())
                drop |= {id(p) for p in members}
                emit(polys, system, k, sorted(sids)[0])
                rep.update({'sources': sorted(sids), 'region': k, 'status': 'ok',
                            'system_id': self._wall_system_key.get(next(iter(sids)))})
                network['wall_systems'].append(rep)
                systems_used.add(system['type'])
            else:
                parts = self._split_wall_systems(N, rings, members, msid, partkey)
                if parts is None:
                    # (an excluded wall spanning between two system walls — a
                    # partition — cannot be closed off by a chord: V1 keeps
                    # the whole region Skin + Web)
                    network['wall_systems'].append({'sources': sorted(sids), 'region': k, 'status': 'mixed',
                                                    'system': None})
                    continue
                skin_sids = sorted(s for s in sids if keyof(s) is None)
                gen = []
                for pi, part in enumerate(parts):
                    sid0 = part['sources'][0]
                    system = self._wall_systems[sid0]
                    polys, rep = WS.generate(part['rings'], system, bead, contact, self._seam_hints())
                    polys, spliced, joined, jinfo = self._connect_skins(polys, part, WS, rep.get('envelope') or 10.0)
                    for ch in joined:                # the incoming beads are re-emitted, extended
                        drop |= {id(p) for p in members if keyof(msid[id(p)]) is None and
                                 all(min(N.dist_to_polyline(q, ch, False), 1.0) < 0.05 for q in samples[id(p)])}
                    rep.update({'sources': part['sources'], 'region': k, 'status': 'ok',
                                'system_id': self._wall_system_key.get(sid0),
                                'shared_with': sorted(sids - set(part['sources'])),
                                'spliced_skins': spliced, 'junctions': jinfo})
                    gen.append({'part': part, 'polys': polys, 'rep': rep, 'system': system, 'sid0': sid0})
                # (pass 10: generated walls meet by PUSHED bands that cross the host's
                # paths — the earlier open-the-cap-and-cut splice is retired)
                skin_part = getattr(self, '_split_skin_part', None)
                if skin_part:
                    # a SKIN wall bridging generated walls (its two faces on two rings):
                    # at each mouth its two face ends continue straight into the host
                    # and are capped there — crossing the host's printed paths
                    for ci, ch in enumerate(skin_part.get('chords', [])):
                        if not (ch.get('is_end') and not ch.get('same') and ch.get('other') and ch.get('exact')):
                            continue
                        e_, s_ = ch['exact']
                        cv = s_ - e_
                        if cv.length() < 1e-9:
                            continue
                        nrm = Vec2(cv.y, -cv.x) * (1.0 / cv.length())
                        hw = (getattr(self, '_source_walls', {}) or {}).get(ch['other'][0]) or {}
                        depth = 0.5 * float(hw.get('thickness') or 10.0) + 0.25 * bead
                        ext = [e_, e_ + nrm * depth, s_ + nrm * depth, s_]
                        sid_ = ch.get('sid') or ''
                        dp = DerivedPath(ext, closed=False, role='inner', source_id=sid_,
                                         treatment_id='wall_system_crossing', id=f'{sid_}~cross{k}.{ci}')
                        cls_of[id(dp)] = N.FACE
                        new.append(dp)
                for gi, g in enumerate(gen):
                    # a generated wall ENTERING skin walls (Single / Hollow / Skin + Web
                    # host): the host face is closed across the mouth by a skin bead and
                    # the incoming cap touches it at one point — one route, no phantom
                    # connectors (the host skins are never spliced into the incoming wall)
                    for hj, rec in enumerate(g['part'].get('host_skins', [])):
                        # the host face closed across the mouth; the incoming wall,
                        # pushed into the host, CROSSES it (real contact)
                        halves = [list(rec['ends'])]
                        host_sid = rec['sids'][0]
                        for hk, hp in enumerate(halves):
                            dp = DerivedPath(hp, closed=False, role='inner', source_id=host_sid,
                                             treatment_id='wall_system_mouth',
                                             id=f'{host_sid}~mouth{k}.{gi}.{hj}.{hk}')
                            cls_of[id(dp)] = N.FACE
                            new.append(dp)
                        g['rep'].setdefault('junctions', []).append({'crossing_into': host_sid})
                for pi, g in enumerate(gen):
                    emit(g['polys'], g['system'], k, g['sid0'], tag=f'p{pi}')
                    network['wall_systems'].append(g['rep'])
                    systems_used.add(g['system']['type'])
                drop |= {id(p) for p in members if partkey(msid[id(p)]) is not None}
            ring0 = rings[0][0] if rings and rings[0] else None
            mine = [j for j in jobs if ring0 is not None and j['rings'] and j['rings'][0]
                    and j['rings'][0][0].dist(ring0) < 1e-6]
            skin_env = getattr(self, '_split_skin_part', None) if len(keys) > 1 else None
            if skin_env and any(self._web_member(s_) for s_ in sids):
                # MIXED region with Skin + Web members: their web is generated on the
                # skin walls' own envelope (the generated walls' bands closed off)
                for j in mine:
                    j['rings'] = skin_env['rings']
                mine = []
            jobs = [j for j in jobs if j not in mine]
            for j in mine:                          # a Wall Infill here is not printed
                for info in network.get('infills', []):
                    if info.get('id') == j['infill'].id:
                        info['status'] = 'wall system'
                        info['wall_system'] = ', '.join(sorted(systems_used))
        return [p for p in result if id(p) not in drop] + new, jobs

    def _split_wall_systems(self, N, rings, members, msid, keyof):
        """Split a mixed-system region by wall. Every ring edge belongs to the
        face bead (→ source → system key) it lies on. For each system: its
        envelope = the rings holding its edges, every maximal run of OTHER
        edges replaced by the chord across its mouth (an attached line's two
        faces and cap → the host's face line). Skin + Web runs (key None)
        whose both neighbours are that system are returned as `skins`: their
        two mouth points, to be spliced into its route. None if a run cannot
        be closed off (its chord would not remove it: a partition)."""
        pts_of = {id(p): p.sample_points() for p in members}
        bead_w = self.material.bead_width

        def tag(a, b):
            m = a.lerp(b, 0.5)
            best = min(members, key=lambda p: N.dist_to_polyline(m, pts_of[id(p)], p.closed))
            return keyof(msid[id(best)]), msid[id(best)]
        tagged, srcs = [], []
        for r in rings:
            n = len(r)
            tk = [tag(r[i], r[(i + 1) % n]) for i in range(n)]
            tagged.append([t for t, _ in tk])
            srcs.append([sid for _, sid in tk])
        keys = sorted({t for ts in tagged for t in ts if t is not None and t != '__J'})
        ends = []                                   # exact end points of the kept skin beads
        for p in members:
            if keyof(msid[id(p)]) is None and not p.closed:
                q = pts_of[id(p)]
                if q:
                    ends += [q[0], q[-1]]

        def exact(v):
            c = min(ends, key=lambda e: e.dist(v), default=None)
            return c if c is not None and c.dist(v) < 0.05 else v
        parts = []
        skin_part = None
        has_skin = any(t is None for ts in tagged for t in ts)
        for key in keys + ([None] if has_skin else []):
            env, skins, chords, host_skins = [], [], [], []
            partition = False
            # this system's face RUNS on every ring (maximal stretches of its own
            # edges) with the gap of other walls that follows each on its ring
            runs = []
            for ri, (r, ts, ss) in enumerate(zip(rings, tagged, srcs)):
                n = len(r)
                if key not in ts:
                    continue
                if all(t == key for t in ts):
                    env.append(list(r))
                    continue
                i0 = next(i for i in range(n) if ts[i] == key and ts[i - 1] != key)
                j = 0
                while j < n:
                    i = (i0 + j) % n
                    if ts[i] != key:
                        j += 1
                        continue
                    pts = [r[i]]
                    while j < n and ts[(i0 + j) % n] == key:
                        pts.append(r[(i0 + j + 1) % n])
                        j += 1
                    g0, glen = (i0 + j) % n, 0
                    while j < n and ts[(i0 + j) % n] != key:
                        glen += 1
                        j += 1
                    runs.append({'ring': ri, 'pts': pts, 'g0': g0, 'glen': glen, 'next': (i0 + j) % n})
            # STITCH the runs into closed envelope rings: a run's end joins the
            # NEAREST run start across the mouth (a chord). A T-branch closes on
            # itself; a wall BRIDGING two others has its two faces on two rings
            # (the outer boundary and a hole) and is stitched into its true band —
            # closing each ring separately made two flat slivers (phantom walls)
            left = list(range(len(runs)))
            while left:
                first = left.pop(0)
                ring_pts, cur = list(runs[first]['pts']), first
                while True:
                    R_ = runs[cur]
                    e = R_['pts'][-1]
                    cand = left + [first]
                    follow = self._face_follow(key, R_, [(q, runs[q]['pts'][0]) for q in cand]) \
                        if key is not None else None
                    if follow is not None:
                        nxt, fpath = follow
                    else:
                        nxt = min(cand, key=lambda q: (e.dist(runs[q]['pts'][0]), q != first))
                        fpath = None
                    S_ = runs[nxt]
                    sp = S_['pts'][0]
                    if fpath is not None:
                        # the wall's own face continues (round a corner hidden in
                        # another band, or straight across an X / a branch mouth):
                        # no chord, no end. A SKIN branch in that mouth still meets
                        # this wall: it is recorded to run into it (_connect_skins)
                        r_, ts_, ss_ = rings[R_['ring']], tagged[R_['ring']], srcs[R_['ring']]
                        n_ = len(r_)
                        if S_['ring'] == R_['ring'] and r_[R_['next']] is sp and R_['glen'] > 0 and \
                                {ts_[(R_['g0'] + x) % n_] for x in range(R_['glen'])} <= {None, '__J'} and \
                                any(ts_[(R_['g0'] + x) % n_] is None for x in range(R_['glen'])):
                            chain = [r_[(R_['g0'] + x) % n_] for x in range(R_['glen'] + 1)]
                            skins.append({'ends': (exact(e), exact(sp)), 'chain': chain,
                                          'sids': (ss_[R_['g0'] % n_], ss_[(R_['g0'] + R_['glen'] - 1) % n_])})
                        ring_pts += fpath
                        if nxt == first:
                            break
                        left.remove(nxt)
                        ring_pts += S_['pts']
                        cur = nxt
                        continue
                    r, ts, ss = rings[R_['ring']], tagged[R_['ring']], srcs[R_['ring']]
                    n = len(r)
                    same = S_['ring'] == R_['ring'] and r[R_['next']] is sp
                    gap_tags = {ts[(R_['g0'] + x) % n] for x in range(max(1, R_['glen']) if same else 1)}
                    if same:
                        length = sum(r[x % n].dist(r[(x + 1) % n]) for x in range(R_['g0'], R_['g0'] + R_['glen']))
                        if length < 1.05 * e.dist(sp) + 1e-6:
                            if key is not None:
                                return None
                            partition = True       # (skin side: just no web envelope)
                    # is the chord this part's END (its faces turn into it) or
                    # just a stretch of its face (the host side)?
                    pv = R_['pts'][-2] if len(R_['pts']) >= 2 else e
                    u1, u2 = e - pv, sp - e
                    # (a host face continues across a mouth nearly straight, even on a
                    # curve; an incoming wall's faces TURN into its chord — also when it
                    # meets the host obliquely)
                    is_end = u1.length() > 1e-9 and u2.length() > 1e-9 and \
                        abs(u1.x * u2.x + u1.y * u2.y) / (u1.length() * u2.length()) < 0.9
                    hosts_ = [t_ for t_ in gap_tags if t_ != '__J']
                    chords.append({'ends': (e, sp), 'other': sorted(t_ for t_ in hosts_ if t_ is not None),
                                   'is_end': is_end, 'same': same, 'exact': (exact(e), exact(sp)),
                                   'sid': ss[(R_['g0'] - 1) % n]})
                    push = None
                    if key is not None and is_end and hosts_:
                        # a generated wall's END meeting another wall: its band is
                        # PUSHED straight into the host (to about the host's centre
                        # line) — its canonical end cap lies inside the host and its
                        # lanes CROSS the host's printed paths: real contact, nothing cut
                        hsid = next((ss[(R_['g0'] + x) % n] for x in range(max(1, R_['glen']) if same else 1)
                                     if ss[(R_['g0'] + x) % n] != '__J'), None)
                        hw = (getattr(self, '_source_walls', {}) or {}).get(hsid) or {}
                        hT = float(hw.get('thickness') or 10.0)
                        cv = sp - e
                        nrm = Vec2(cv.y, -cv.x) * (1.0 / cv.length())          # (material on the left)
                        a_in = e - (R_['pts'][-2] if len(R_['pts']) >= 2 else e)
                        b_in = (S_['pts'][0] - S_['pts'][1]) if len(S_['pts']) >= 2 else a_in
                        dv = (a_in * (1.0 / max(1e-9, a_in.length()))) + (b_in * (1.0 / max(1e-9, b_in.length())))
                        dv = dv * (1.0 / dv.length()) if dv.length() > 1e-9 else nrm
                        k_ = max(0.3, dv.x * nrm.x + dv.y * nrm.y)
                        # measured from the host's ACTUAL face (a junction fillet
                        # puts the chord short of it): ray from the chord's middle
                        mid_ = e.lerp(sp, 0.5)
                        t_face = 0.0
                        hits_ = []
                        for fpts, fcl in (getattr(self, '_src_faces', {}) or {}).get(hsid, []):
                            fr = fpts + ([fpts[0]] if fcl else [])
                            for a_, b_ in zip(fr, fr[1:]):
                                sg = b_ - a_
                                den = dv.x * sg.y - dv.y * sg.x
                                if abs(den) < 1e-12:
                                    continue
                                w_ = a_ - mid_
                                tt = (w_.x * sg.y - w_.y * sg.x) / den
                                uu = (w_.x * dv.y - w_.y * dv.x) / den
                                if -1e-9 <= uu <= 1 + 1e-9 and tt > -0.5:
                                    hits_.append(tt)
                        if hits_:
                            t_face = max(0.0, min(hits_))
                        depth = t_face + (0.5 * hT + 0.25 * bead_w + 0.5 * bead_w) / k_
                        push = [e + dv * depth, sp + dv * depth]
                        chords[-1]['pushed'] = True
                    if key is not None and gap_tags == {None} and (same or is_end):
                        chain = [r[(R_['g0'] + x) % n] for x in range(R_['glen'] + 1)] if same else [e, sp]
                        rec = {'ends': (exact(e), exact(sp)), 'chain': chain,
                               'sids': (ss[R_['g0'] % n], ss[(R_['g0'] + max(0, R_['glen'] - 1)) % n])}
                        # the chord is THIS system's end: the skins are the HOST
                        # (this wall is the incoming one) — never spliced into it
                        if is_end:
                            host_skins.append(rec)
                        elif same:
                            skins.append(rec)
                    if push:
                        ring_pts += push
                    if nxt == first:
                        break
                    left.remove(nxt)
                    ring_pts += S_['pts']
                    cur = nxt
                if len(ring_pts) >= 3:
                    env.append(ring_pts)
            if key is None:
                # the SKIN side's own envelope (generated bands closed off): where a
                # Skin + Web member's web is generated in a mixed region
                if env and not partition:
                    skin_part = {'key': None, 'rings': env, 'chords': chords}
                continue
            if env:
                # (not `srcs`: that is the per-ring source tags, needed for the next key)
                part_srcs = sorted({msid[id(p)] for p in members if keyof(msid[id(p)]) == key})
                parts.append({'key': key, 'rings': env, 'skins': skins, 'sources': part_srcs, 'chords': chords,
                              'host_skins': host_skins})
        self._split_skin_part = skin_part
        return parts

    def _face_follow(self, sid, run, starts, tol=0.05):
        """If the run's end lies on one of wall `sid`'s own face lines and,
        walking FORWARD along that line (the run's direction), a candidate run
        start lies on it too: (that candidate, the face points in between).
        The band then continues along its own face — round a corner hidden in
        another wall's band, or straight through an X crossing."""
        faces = (getattr(self, '_src_faces', {}) or {}).get(sid) or []
        e = run['pts'][-1]
        prev = run['pts'][-2] if len(run['pts']) >= 2 else e
        best = None
        for pts, closed in faces:
            ring = pts + ([pts[0]] if closed else [])
            cum = [0.0]
            for a_, b_ in zip(ring, ring[1:]):
                cum.append(cum[-1] + a_.dist(b_))
            Ltot = cum[-1]

            def proj(q):
                bb = None
                for j, (a_, b_) in enumerate(zip(ring, ring[1:])):
                    d_ = b_ - a_
                    L2 = d_.x * d_.x + d_.y * d_.y
                    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((q.x - a_.x) * d_.x + (q.y - a_.y) * d_.y) / L2))
                    dd = (a_ + d_ * t).dist(q)
                    if bb is None or dd < bb[0]:
                        bb = (dd, cum[j] + t * (cum[j + 1] - cum[j]), j)
                return bb
            pe = proj(e)
            if pe is None or pe[0] > tol:
                continue
            # the run's direction along this face
            pp = proj(prev)
            delta = 0.0 if pp is None else pe[1] - pp[1]
            if closed and Ltot > 0:
                delta = (delta + 0.5 * Ltot) % Ltot - 0.5 * Ltot      # (the short way round)
            if abs(delta) < 1e-9:
                continue                                    # (no direction along this face)
            fwd = 1.0 if delta > 0 else -1.0
            for q, sp in starts:
                ps = proj(sp)
                if ps is None or ps[0] > tol:
                    continue
                gap = (ps[1] - pe[1]) * fwd
                if closed:
                    gap %= Ltot
                if gap <= 1e-6:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, q, pe[1], fwd, ring, cum, closed, Ltot)
        if best is None:
            return None
        gap, q, a0, fwd, ring, cum, closed, Ltot = best
        # the face points strictly between e and the start (in walking order)
        pts_between = []
        for j, v in enumerate(ring[:-1] if closed else ring):
            x = cum[j]
            g_ = (x - a0) * fwd
            if closed:
                g_ %= Ltot
            if 1e-6 < g_ < gap - 1e-6:
                pts_between.append((g_, v))
        return q, [v for _, v in sorted(pts_between, key=lambda t: t[0])]

    def _connect_generated(self, gen, WS, bead):
        """An incoming GENERATED wall system (Parallel / Interleaved / Linked /
        Chained Loop) meeting another system part: its envelope was closed off
        by the chord across the host's NOMINAL face, so its outer end cap sat
        there — a visible gap before the host's real material. The outer cap
        rail along that chord is opened and the remaining route (its two face
        lanes leading into it) is connected with wall_systems.connect_branch:
        both face lanes continue STRAIGHT until they reach the host's printed
        material, the handoff lies inside the host; the nested inner returns
        stay inside the incoming wall. Fallback: unchanged (two routes)."""
        R = float(self.junction_radius or 0.0) if self.junction_style == 'round' else 0.0
        h = 0.5 * bead
        for g in gen:
            for ch in g['part'].get('chords', []):
                if not ch['is_end'] or len(ch['other']) != 1:
                    continue
                host = next((x for x in gen if x is not g and x['part']['key'] == ch['other'][0]), None)
                if host is None:
                    continue
                c1, c2 = ch['ends']
                d = c2 - c1
                L2 = d.x * d.x + d.y * d.y
                if L2 < 1e-12:
                    continue

                ud = d * (1.0 / math.sqrt(L2))

                def on_rail(q):
                    """q lies on the cap rail: half a bead inside the chord, within it."""
                    t = ((q.x - c1.x) * d.x + (q.y - c1.y) * d.y) / L2
                    off = abs((q.x - c1.x) * ud.y - (q.y - c1.y) * ud.x)
                    return -0.05 <= t <= 1.05 and abs(off - h) <= 0.25
                for pi, pl in enumerate(g['polys']):
                    closed = len(pl) > 3 and pl[0].dist(pl[-1]) < 1e-6
                    if not closed:
                        continue
                    core = pl[:-1]
                    n = len(core)
                    # the cap rail along this chord: the longest segment parallel
                    # to it, half a bead inside (the end motif's outer cap)
                    best = None
                    for i in range(n):
                        a_, b_ = core[i], core[(i + 1) % n]
                        v = b_ - a_
                        if v.length() < 1e-6 or not (on_rail(a_) and on_rail(b_)):
                            continue
                        if abs(v.x * ud.x + v.y * ud.y) / v.length() < 0.95:
                            continue
                        if best is None or v.length() > best[1]:
                            best = (i, v.length())
                    if best is None:
                        continue
                    i = best[0]
                    chain = [core[(i + 1 + x) % n] for x in range(n)]      # opened at that segment
                    if len(chain) < 4:
                        continue
                    mid = chain[len(chain) // 2]
                    sid = g['part']['sources'][0]
                    d1 = self._junction_dir(sid, chain[0], mid)
                    d2 = self._junction_dir(sid, chain[-1], mid)
                    if d1 is None or d2 is None:
                        continue
                    res = WS.connect_branch(host['polys'], chain, d1, d2, R, bead,
                                            2.0 * (host['rep'].get('envelope') or 10.0) + 2.0 * bead)
                    if res is None:
                        continue
                    host['polys'], info = res
                    g['polys'] = g['polys'][:pi] + g['polys'][pi + 1:]
                    host['rep'].setdefault('junctions', []).append(dict(info, incoming=sid))
                    g['rep']['joined_into'] = host['rep'].get('system_id')
                    break

    def _seam_hints(self) -> list:
        """Positions of the user's route origins (Move Start): wall systems that
        concentrate their lane changes (Parallel Walls) put that seam there."""
        out = []
        for o in self.route_origins or []:
            pos = o.get('pos') if isinstance(o, dict) else None
            if pos and len(pos) == 2:
                out.append(Vec2(float(pos[0]), float(pos[1])))
        return out

    def _web_member(self, sid) -> bool:
        """Does source sid print a Skin + Web web (layer system or legacy)?"""
        ws = self._system_of(sid)
        if ws is not None and ws.thickness is not None:
            return ws.type == 'skin_web' and (ws.web or {}).get('pattern', 'none') != 'none'
        return sid not in (getattr(self, '_wall_systems', {}) or {})

    @staticmethod
    def _kiss_into_skin(polys, ends, bead):
        """A generated wall whose end meets SKIN walls: its outer cap rail
        (straight, half a bead inside the chord across the host's mouth) is
        bent to touch the chord's middle P, and the host face is closed across
        the mouth by the bead c1 → P → c2 (two halves ending at P). The
        incoming loop restarts at P, so host skins + incoming wall form one
        route through P. (polys, [half1, half2]) or None."""
        c1, c2 = ends
        d = c2 - c1
        L2 = d.x * d.x + d.y * d.y
        if L2 < 1e-12:
            return None
        ud = d * (1.0 / math.sqrt(L2))
        h = 0.5 * bead

        def on_rail(q):
            t = ((q.x - c1.x) * d.x + (q.y - c1.y) * d.y) / L2
            off = abs((q.x - c1.x) * ud.y - (q.y - c1.y) * ud.x)
            return -0.05 <= t <= 1.05 and abs(off - h) <= 0.25
        P = c1.lerp(c2, 0.5)
        for pi, pl in enumerate(polys):
            if not (len(pl) > 3 and pl[0].dist(pl[-1]) < 1e-6):
                continue
            core = pl[:-1]
            n = len(core)
            best = None
            for i in range(n):
                a_, b_ = core[i], core[(i + 1) % n]
                v = b_ - a_
                if v.length() < 1e-6 or not (on_rail(a_) and on_rail(b_)):
                    continue
                if abs(v.x * ud.x + v.y * ud.y) / v.length() < 0.95:
                    continue
                if best is None or v.length() > best[1]:
                    best = (i, v.length())
            if best is None:
                continue
            i = best[0]
            a_, b_ = core[i], core[(i + 1) % n]
            # rail a → b bent to touch P (a shallow V half a bead deep)
            loop = [P] + [core[(i + 1 + x) % n] for x in range(n)] + [P]
            # (loop: P → b … a → P)
            return polys[:pi] + polys[pi + 1:] + [loop], [[c1, P], [P, c2]]
        return None

    def _junction_dir(self, sid, e, inner):
        """The ARCHITECTURAL junction direction of source `sid` at its wall end
        e: the source's tangent nearest e, pointing from the wall's inside
        (`inner`, a point further along it) towards the host."""
        src = next((p for p in self.source_paths if p.id == sid), None)
        if src is None:
            return None
        pts = src.sample_points()
        if src.closed and pts:
            pts = pts + [pts[0]]
        best = None
        for a, b in zip(pts, pts[1:]):
            v = b - a
            L2 = v.x * v.x + v.y * v.y
            if L2 < 1e-18:
                continue
            t = max(0.0, min(1.0, ((e.x - a.x) * v.x + (e.y - a.y) * v.y) / L2))
            dd = (a + v * t).dist(e)
            if best is None or dd < best[0]:
                best = (dd, v * (1.0 / math.sqrt(L2)))
        if best is None:
            return None
        d = best[1]
        return d if (d.x * (e.x - inner.x) + d.y * (e.y - inner.y)) > 0 else d * -1.0

    def _connect_skins(self, polys, part, WS, envelope):
        """Join each incoming skin-like wall (single / out-and-back, hollow,
        Skin + Web) to the host system's ACTUAL printed geometry
        (wall_systems.connect_branch); where that is not possible (no straight
        lanes, no contact) fall back to the mouth splice (_splice_skins).
        → (polys, joined count, chains re-emitted, junction reports)."""
        R = float(self.junction_radius or 0.0) if self.junction_style == 'round' else 0.0
        bead = self.material.bead_width
        joined, infos, fallback = [], [], []
        for sk in part['skins']:
            ch = sk['chain']
            if len(ch) < 3:
                fallback.append(sk['ends'])
                continue
            mid = ch[len(ch) // 2]
            d1 = self._junction_dir(sk['sids'][0], ch[0], mid)
            d2 = self._junction_dir(sk['sids'][1], ch[-1], mid)
            # the lanes run straight THROUGH the host's paths (crossings = contact)
            # and are capped inside the host: nothing of the host is cut
            loop = WS.extend_through(ch, d1, d2, sk['ends'][0], sk['ends'][1],
                                     0.5 * envelope + 0.25 * bead, bead) \
                if d1 is not None and d2 is not None else None
            if loop is None:
                fallback.append(sk['ends'])
                continue
            polys = polys + [loop]
            joined.append(ch)
            infos.append({'crossing_from': sk['sids'][0],
                          'cap': [[round(loop[0].x, 4), round(loop[0].y, 4)], [round(loop[-2].x, 4), round(loop[-2].y, 4)]]})
        n = len(joined)
        if fallback:
            polys, k = self._splice_skins(polys, fallback, WS)
            n += k
        return polys, n, joined, infos

    @staticmethod
    def _splice_skins(polys, skins, WS):
        """Splice each Skin + Web run (its two mouth points e1, e2) into ONE
        system path: the short stretch of that path between the points
        nearest e1 and e2 is cut out and both mouth points are joined to the
        cut ends — the skin run and the system route become one circuit."""
        spliced = 0
        polys = [list(pl) for pl in polys]
        for e1, e2 in skins:
            best = None
            for i, pl in enumerate(polys):
                if len(pl) < 2:
                    continue
                cum = [0.0]
                for a, b in zip(pl, pl[1:]):
                    cum.append(cum[-1] + a.dist(b))

                def near(e):
                    bb = None
                    for j, (a, b) in enumerate(zip(pl, pl[1:])):
                        d = b - a
                        L2 = d.x * d.x + d.y * d.y
                        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((e.x - a.x) * d.x + (e.y - a.y) * d.y) / L2))
                        q = a + d * t
                        dd = q.dist(e)
                        if bb is None or dd < bb[0]:
                            bb = (dd, cum[j] + t * (cum[j + 1] - cum[j]), q)
                    return bb
                n1, n2 = near(e1), near(e2)
                L = cum[-1]
                closed = pl[0].dist(pl[-1]) < 1e-6
                gap = abs(n1[1] - n2[1])
                short = min(gap, L - gap) if closed else gap
                if short > max(4.0 * e1.dist(e2), 12.0):
                    continue
                score = n1[0] + n2[0]
                if best is None or score < best[0]:
                    best = (score, i, n1, n2, cum, closed)
            if best is None:
                continue
            _, i, n1, n2, cum, closed = best
            pl = polys[i]
            L = cum[-1]

            def sub(a, b):
                """Points of pl from arc length a to b (a ≤ b)."""
                def at(x):
                    j = max(0, min(len(pl) - 2, next((k for k in range(len(cum) - 1) if cum[k + 1] >= x), len(pl) - 2)))
                    seg = cum[j + 1] - cum[j]
                    return pl[j].lerp(pl[j + 1], 0.0 if seg < 1e-12 else (x - cum[j]) / seg)
                inner = [pl[k] for k in range(len(pl)) if a + 1e-9 < cum[k] < b - 1e-9]
                return [at(a)] + inner + [at(b)]
            P1, P2 = n1[2], n2[2]
            (a1, A), (a2, B) = sorted([(n1[1], P1), (n2[1], P2)], key=lambda x: x[0])
            if closed and (a2 - a1) <= L - (a2 - a1):
                keep = [sub(a2, L) + sub(0.0, a1)[1:]]
                keep[0][0], keep[0][-1] = B, A
            elif closed:
                keep = [sub(a1, a2)]
                keep[0][0], keep[0][-1] = A, B
            else:
                keep = [x for x in (sub(0.0, a1), sub(a2, L)) if len(x) >= 2 and
                        sum(u.dist(v) for u, v in zip(x, x[1:])) > 1e-9]
                for x in keep:
                    if x[-1].dist(A) < 1e-9:
                        x[-1] = A
                    if x[0].dist(B) < 1e-9:
                        x[0] = B
            polys[i:i + 1] = keep
            polys += [[e1, P1], [e2, P2]]
            spliced += 1
        return WS._join(polys), spliced

    def migrate_wall_systems(self) -> dict:
        """MIGRATION of a legacy design to explicit Wall Systems (pure: the
        layer is not changed). Every path's EFFECTIVE wall under the old rules
        (own WallSpec, Network Wall inheritance, the membership-only systems
        of the previous pass, wall infills reaching its region) is grouped by
        (thickness, alignment, reference, construction, parameters, web) into
        one Wall System; the wall infills become webs OWNED by the system of
        their anchor path (same record and id: Layer-Design lineage is kept).
        Returns {'wall_systems': [...], 'owners': {infill id: system id},
        'migrated': bool}; the caller clears path walls / Network Walls."""
        _, meta = self._build_effective()
        net = meta['network']
        sw = net.get('source_walls') or {}
        nets = {pid: n['sources'] for n in net.get('source_networks') or [] for pid in n['sources']}
        legacy = any(p.wall is not None for p in self.source_paths) or bool(self.network_walls) or \
            any(ws.thickness is None for ws in self.wall_systems) or \
            any(not f.owner and f.kind == 'wall' and sw.get(f.path_id) for f in self.infills)
        if not legacy:
            return {'wall_systems': [w.to_dict() for w in self.wall_systems], 'owners': {}, 'migrated': False}
        groups, order = {}, []
        sys_of = {}
        for p in self.source_paths:
            w = sw.get(p.id)
            if not w:
                continue
            spec = dict(w['system'] or {'type': 'skin_web'})
            typ = spec.pop('type')
            ids = nets.get(p.id, [p.id])
            f = next((f for f in self.infills if not f.owner and f.kind == 'wall' and f.path_id in ids), None)
            web = {'pattern': f.pattern, 'params': dict(f.params), 'variation_index': f.variation_index} \
                if (f is not None and typ == 'skin_web') else {'pattern': 'zigzag', 'params': {'spacing': 20.0},
                                                               'variation_index': 0}
            if typ == 'skin_web' and f is None:
                typ = 'hollow'                   # skins, no web: Hollow / Skins Only
            key = json.dumps([w['thickness'], w['align'], bool(w['print_reference']), typ, spec, web], sort_keys=True)
            if key not in groups:
                groups[key] = {'type': typ, 'params': spec, 'web': web, 'thickness': w['thickness'],
                               'align': w['align'], 'print_reference': bool(w['print_reference']), 'members': []}
                order.append(key)
            groups[key]['members'].append(p.id)
        used, out = set(), []
        for k, key in enumerate(order):
            g = groups[key]
            old = next((ws for ws in self.wall_systems if ws.id not in used and ws.type == g['type']
                        and set(ws.members) & set(g['members'])), None)
            taken = used | {ws.id for ws in self.wall_systems}
            sid = old.id if old is not None else next(f'WS{n}' for n in range(1, 10000) if f'WS{n}' not in taken)
            used.add(sid)
            name = (old.name if old is not None and old.name else f'Wall System {k + 1}')
            ws = WallSystem(sid, g['type'], g['params'], g['members'], name=name, thickness=g['thickness'],
                            align=g['align'], print_reference=g['print_reference'], web=g['web'])
            out.append(ws)
            for pid in g['members']:
                sys_of[pid] = sid
        owners = {f.id: sys_of[f.path_id] for f in self.infills
                  if not f.owner and f.kind == 'wall' and f.path_id in sys_of}
        return {'wall_systems': [w.to_dict() for w in out], 'owners': owners, 'migrated': True}

    def _system_of(self, pid) -> Optional[WallSystem]:
        """The Wall System a path explicitly belongs to (first wins; the
        Designer keeps one system per path)."""
        return next((ws for ws in self.wall_systems if pid in ws.members), None)

    def _all_infills(self) -> list:
        """The infills the build uses: the layer's own (unowned) infills,
        each Skin + Web system's OWNED web infills (anchored on a current
        member), and — for a system whose web has no owned record yet (an
        API / test layer) — one synthesised record per member (several on
        one connected region: the first wins, the rest are shadowed)."""
        owners = {ws.id: ws for ws in self.wall_systems}
        out = []
        for inf in self.infills:
            if not inf.owner:
                out.append(inf)
                continue
            ws = owners.get(inf.owner)
            if ws is None or ws.type != 'skin_web' or (ws.web or {}).get('pattern', 'none') == 'none' \
                    or inf.path_id not in ws.members:
                continue                       # an inactive web (system changed / member left)
            out.append(inf)
        have = {inf.owner for inf in out if inf.owner}
        for ws in self.wall_systems:
            web = ws.web or {}
            if ws.type != 'skin_web' or web.get('pattern', 'none') == 'none' or ws.id in have:
                continue
            for pid in ws.members:
                out.append(RegionInfill(f'{ws.id}~web~{pid}', pid, web['pattern'], dict(web.get('params') or {}),
                                        int(web.get('variation_index', 0) or 0), 'wall', ws.id))
        return out

    def _wall_offsets(self, sources, processed, source_nets):
        """Offsets generated by wall specs: a path's own WallSpec, else its
        network's NetworkWall."""
        net_of = {pid: ids for ids in source_nets for pid in ids}
        out, ref_only = [], set()
        self._return_lanes = set()
        self._wall_systems = {}               # source id → its (non Skin + Web) wall system
        self._wall_system_key = {}            # source id → grouping key (layer system id)
        self._wall_system_of = {}             # source id → {'id', 'name', 'type', 'filled'}
        mat = self.material
        extra = {ot.source_path_id for ot in self.offset_treatments}
        self._source_walls = {}               # source id → its effective wall (report / migration)
        import wall_systems as WS
        for p in sources:
            lsys = self._system_of(p.id)
            if lsys is not None and lsys.type == 'single':
                # SINGLE / OUT-AND-BACK: the ordinary single-line wall (one bead;
                # Physical rules → the separated outbound + return lanes below)
                spec, src = None, 'system'
            elif lsys is not None and lsys.thickness is not None:
                spec = lsys.envelope()            # the WALL SYSTEM owns the envelope
                src = 'system'
            else:
                spec, src = p.wall, ('path' if p.wall is not None else None)
                if spec is not None and spec.thickness <= 1e-9:
                    spec, src = None, 'single'   # explicit SINGLE BEAD: no inherited network wall
                elif spec is None:
                    nw = self._network_wall_for(net_of.get(p.id, [p.id]))
                    if nw is not None:
                        spec = WallSpec(nw.thickness, nw.align, nw.print_reference, nw.system)
                        src = 'network'
            ws = None
            if spec is not None and spec.thickness > 1e-9:
                ws = WS.normalize(lsys.spec()) if lsys is not None else WS.normalize(spec.system)
                if ws is not None:
                    self._wall_systems[p.id] = ws
                    self._wall_system_key[p.id] = lsys.id if lsys is not None else None
            if lsys is not None:
                self._wall_system_of[p.id] = {'id': lsys.id, 'name': lsys.name or lsys.id, 'type': lsys.type,
                                              'filled': spec is not None and spec.thickness > 1e-9}
            self._source_walls[p.id] = None if spec is None or spec.thickness <= 1e-9 else {
                'thickness': spec.thickness, 'align': spec.align, 'print_reference': spec.print_reference,
                'system': ws, 'from': src}
            opened = p.id in {o.source_path_id for o in self.openings} and \
                p.id not in {inf.path_id for inf in self._all_infills()}
            if spec is None and mat.physical and (not p.closed or opened) and p.id not in extra:
                # PHYSICAL: a single-bead OPEN wall (or a closed one that an
                # opening opens) cannot print out and back over itself — it
                # becomes a TWO-PASS wall: the passes are a RETURN LANE apart
                # (W − R), joined by a U-turn; the drawn path is their
                # (unprinted) centre reference.
                spec = WallSpec(mat.return_separation(), 'center', False)
                self._return_lanes.add(p.id)
            if spec is not None:
                offs = spec.offsets(p.id, p.closed, processed[p.id])
                out.extend(offs)
                if offs and not spec.reference_printed(p.closed):
                    ref_only.add(p.id)
        return out, ref_only

    def _mouth_cuts(self, N, sources, processed, offsets_by_source) -> dict:
        """{host id: [(a, b, pa, pb)]} — for each end of a RETURN-LANE wall
        lying on a single-bead host: the host interval between the two lane
        landings (each lane's end carried along the branch's end tangent to
        the host). The lanes' end points are moved exactly onto the
        landings; that end gets no U-turn (self._wire_attached)."""
        lanes = getattr(self, '_return_lanes', set())
        by_id = {p.id: p for p in sources}
        hosts = [p for p in sources if p.id not in lanes and not offsets_by_source.get(p.id)
                 and len(processed.get(p.id, ())) >= 2]
        out: dict = {}
        for bid in sorted(lanes):
            b = by_id.get(bid)
            faces = [d for _, d in offsets_by_source.get(bid, [])]
            if b is None or b.closed or len(faces) != 2:
                continue
            bpts = processed[bid]
            for which in (0, -1):
                E = bpts[which]
                host = None
                for h in hosts:
                    hp = processed[h.id]
                    if N.dist_to_polyline(E, hp, h.closed) > 1e-6:
                        continue
                    if not h.closed and min(E.dist(hp[0]), E.dist(hp[-1])) < 1e-6:
                        continue                 # end to end: not a branch mouth
                    host = h
                    break
                if host is None:
                    continue
                hp = processed[host.id]
                out_dir = N._end_dir(bpts, which) * -1.0      # leaving the branch
                hits = []
                for d in faces:
                    F = d._points[which]
                    hs = N._ray_hits(F, out_dir, hp, host.closed, -3.0 * F.dist(E) - 1.0,
                                     3.0 * F.dist(E) + 1.0)
                    if not hs:
                        break
                    hits.append((d, min(hs, key=lambda h_: abs(h_[0]))[1]))
                if len(hits) != 2:
                    continue
                cum = _cum_lengths(hp, host.closed)
                L = cum[-1]
                (d1, H1), (d2, H2) = hits
                s1 = _project_to_polyline(H1, hp, cum, host.closed)[0]
                s2 = _project_to_polyline(H2, hp, cum, host.closed)[0]
                if s1 > s2:
                    (s1, H1), (s2, H2) = (s2, H2), (s1, H1)
                if host.closed and s2 - s1 > L / 2:          # the short way round
                    s1, s2, H1, H2 = s2, s1 + L, H2, H1
                if s2 - s1 <= 1e-6:
                    continue
                for d, H in hits:                            # lanes land exactly
                    if d._points[which].dist(H) > 1e-9:
                        if which == 0:
                            d._points.insert(0, H)
                        else:
                            d._points.append(H)
                    d._points[which] = H
                out.setdefault(host.id, []).append((s1, s2, H1, H2))
                self._wire_attached.append((bid, E))
                self._mouths.append((bid, H1, H2))
        return out

    def _snap_source_ends(self, sources, processed, tol) -> set:
        """Move open source ends lying within tol (but not exactly on)
        another visible source's processed polyline onto it."""
        snapped = set()
        for p in sources:
            pts = processed[p.id]
            if p.closed or len(pts) < 2:
                continue
            for idx in (0, -1):
                e = pts[idx]
                best = None
                for q in sources:
                    if q.id == p.id:
                        continue
                    qp = processed[q.id]
                    if len(qp) < 2:
                        continue
                    s, d, foot = _project_to_polyline(
                        e, qp, _cum_lengths(qp, q.closed), q.closed)
                    if 1e-12 < d <= tol and (best is None or d < best[0]):
                        best = (d, foot)
                if best is not None:
                    pts[idx] = best[1]
                    snapped.add(p.id)
        return snapped

    def _apply_networks(self, N, result, cls_of, elem_of, pts_of, net_pieces,
                        lat_bounds, full_by_id, plans, offsets_by_source,
                        processed):
        """
        Find wall networks (systems / lattices whose beads touch or cross)
        and replace their beads by the resolved network geometry. Elements
        in no network are returned untouched.
        """
        summary = {'components': [], 'junctions': [], 'modified_sources': [],
                   'regions': [], 'infills': []}
        def _pts(p):
            return pts_of.get(id(p)) or p.sample_points()
        beads_all = [N.Bead(p.id, _pts(p), p.closed,
                            cls_of.get(id(p), N.WIRE), elem_of[id(p)], p)
                     for p in result if id(p) in elem_of
                     and len(_pts(p)) >= 2]
        all_infills = self._all_infills()
        infills = [inf for inf in all_infills if inf.path_id in processed]
        for inf in all_infills:
            if inf not in infills:
                summary['infills'].append({'id': inf.id, 'regions': 0,
                                           'shadowed_by': None,
                                           'status': 'missing path'})
        # sources with a WALL SYSTEM need their material regions (like infills)
        ws_sources = sorted(set(getattr(self, '_wall_systems', {}) or {}) & set(processed))
        if len({b.element for b in beads_all}) < 2 and not infills and not ws_sources:
            return result, summary

        def _expected(a, b):
            """Lattice touching its own boundary walls is not a junction, and
            lattices crossing each other are structural crossings (generated
            field geometry), not a wall network."""
            if a.startswith('L:') and b.startswith('L:'):
                return True
            for x, y in ((a, b), (b, a)):
                if x.startswith('L:') and y.startswith('S:') and \
                        y[2:] in lat_bounds.get(x[2:], ()):
                    return True
            return False

        uf = N.UnionFind()
        for lid, sids in lat_bounds.items():
            for sid in sids:
                uf.union(f'L:{lid}', f'S:{sid}')
        foreign = []
        for a, b in N.find_contacts(beads_all):
            uf.union(a, b)
            if not _expected(a, b):
                foreign.append(a)
        # Infill on a closed single-bead wall declares its inside to be
        # wall; closed walls lying inside it are its holes (even-odd).
        declared: dict[str, list] = {}           # anchor id → [ring, holes…]
        by_sys = {}
        for pc in net_pieces:
            by_sys.setdefault(pc.sys_id, []).append(pc)
        reg_ops = getattr(self, '_region_ops', None) or {'rings': {}, 'voids': {}}
        for inf in infills:
            if inf.path_id in getattr(self, '_absorbed', {}):
                continue        # its face belongs to another assembly now
            if inf.path_id in reg_ops['rings']:
                # a wall-material region with doorways: still defined by
                # its FULL closed rings (the doorways are subtracted below)
                rings = reg_ops['rings'][inf.path_id]
                declared[inf.path_id] = rings
                for sid, others in by_sys.items():
                    if sid != inf.path_id and any(_point_in_polygon(w[1][0], rings[0])
                                                  for pc in others for w in pc.walls):
                        uf.union(f'S:{inf.path_id}', f'S:{sid}')
                continue
            pcs = by_sys.get(inf.path_id, [])
            if inf.path_id not in getattr(self, '_mouth_cut', ()) and \
                    (len(pcs) != 1 or not pcs[0].closed or pcs[0].thick):
                continue        # (a branch mouth does not open a declared region)
            ring = processed[inf.path_id]
            rings = [ring]
            for sid, others in by_sys.items():
                if sid == inf.path_id or len(others) != 1 or not others[0].closed:
                    continue
                outer = max((w[1] for w in others[0].walls),
                            key=lambda r: abs(_polygon_area(r)))
                if _point_in_polygon(outer[0], ring):
                    rings.append(outer)
                    uf.union(f'S:{inf.path_id}', f'S:{sid}')
            declared[inf.path_id] = rings
        net_roots = {uf.find(a) for a in foreign} | \
            {uf.find(f'S:{self._anchor(inf)}') for inf in infills} | \
            {uf.find(f'S:{sid}') for sid in ws_sources}
        if not net_roots:
            return result, summary
        filled: dict = {}                        # (root, comp id) → infill id

        replaced: dict[int, list] = {}         # id(path) → replacement list
        appended: list = []
        for root in sorted(net_roots):
            elems = {b.element for b in beads_all if uf.find(b.element) == root}
            sys_ids = {e[2:] for e in elems if e.startswith('S:')}
            comp_pieces = [pc for pc in net_pieces if pc.sys_id in sys_ids]
            joins, dropped = self._network_joins(N, comp_pieces)
            beads = [b for b in beads_all
                     if b.element in elems and b.id not in dropped]
            material: list = []
            voids: list = []
            for pc in comp_pieces:
                band = pc.band()
                if band is not None:
                    material.append(band)
            for sid in sys_ids:
                if sid in declared:
                    material.append(N.Shape(declared[sid]))
            for jn in joins:
                beads.extend(jn.beads)
                material.extend(jn.material)
            # a two-pass branch's mouth on a single-bead host closes its band
            # for classification, but is never printed
            for k, (bid, a, b) in enumerate(getattr(self, '_mouths', [])):
                if f'S:{bid}' in elems:
                    beads.append(N.Bead(f'{bid}_mouth{k}', [a, b], False, N.SPLIT, f'S:{bid}'))
            # Lattice between two separate systems: its cavity is wall,
            # except the inside of closed walls lying within it (islands).
            for e in sorted(elems):
                if not e.startswith('L:'):
                    continue
                li = next((x for x in self.lattice_instances if x.id == e[2:]),
                          None)
                if li is None or len(lat_bounds.get(li.id, ())) < 2:
                    continue
                cav = self._lattice_cavity(N, li, full_by_id, comp_pieces,
                                           lat_bounds)
                if cav is None:
                    for b in beads:            # no cavity: keep it as drawn
                        if b.element == e:
                            b.cls = N.WIRE
                    continue
                shape, closers = cav
                material.append(shape)
                beads.extend(N.Bead(f'{li.id}_cav{k}', c, False, N.VIRTUAL, e)
                             for k, c in enumerate(closers))
            # Openings keep their clear width free of every wall's material.
            for sid in sorted(sys_ids):
                plan = plans.get(sid)
                if plan is None:
                    continue
                offs = offsets_by_source.get(sid, [])
                for k, (ring, _, _) in enumerate(plan.clear_voids(
                        offs, self._opening_cap_reach(offs, sid))):
                    # The whole clear-void boundary must be in the
                    # arrangement (its sides follow walls the opening has
                    # cut away), so anything crossing the doorway is split
                    # exactly at it.
                    voids.append(N.Shape([ring]))
                    beads.append(N.Bead(f'{sid}_clear{k}', ring, True,
                                        N.VIRTUAL, f'S:{sid}'))
            # Doorways through wall-material regions: corridors subtracted
            # from the material (their boundary is in the arrangement so the
            # cut is exact) — subtraction, never new wall topology.
            for sid in sorted(sys_ids):
                for k, poly in enumerate(reg_ops['voids'].get(sid, [])):
                    voids.append(N.Shape([poly]))
                    beads.append(N.Bead(f'{sid}_door{k}', poly, True, N.VIRTUAL, f'S:{sid}'))
            overrides = []
            for ro in self.region_overrides:
                if ro.path_id in sys_ids and ro.path_id in processed:
                    pt = _point_beside(processed[ro.path_id],
                                       self._path_by_id(ro.path_id).closed,
                                       ro.s, ro.offset)
                    if pt is not None:
                        overrides.append((pt, ro.kind))

            res = N.resolve(beads, material, voids, overrides)
            # Junction corners: treated by the layer's junction settings,
            # independent of every source's own Corner R.
            corners = N.junction_corners(res)
            fillets = N.round_junctions(res, corners, self._junction_radius,
                                        max((pc.width for pc in comp_pieces), default=0.0))
            self._emit_network(N, res, replaced, appended, cls_of, summary)
            ci = len(summary['components'])
            summary['components'].append({
                'sources': sorted(sys_ids),
                'lattices': sorted(e[2:] for e in elems if e.startswith('L:')),
            })
            corner_at = {(c.pt.x, c.pt.y): c for c in corners}
            for p in res.junctions:
                c = corner_at.pop((p.x, p.y), None)
                summary['junctions'].append(self._junction_info(p, c))
            for c in corner_at.values():
                summary['junctions'].append(self._junction_info(c.pt, c))
            # Region infill: one coherent field per wall-material region.
            comps, comp_of = N.material_components(res)
            summary.setdefault('_rings', []).extend(
                [N.fillet_ring(r, fillets) for r in c.rings] for c in comps)
            self._region_infill(N, res, comps, comp_of, fillets, infills,
                                sys_ids, root, filled, appended, cls_of,
                                summary, declared)
            for f in res.faces[1:]:
                summary['regions'].append({
                    'outer': [[p.x, p.y] for p in f.outer],
                    'holes': [[[p.x, p.y] for p in h] for h in f.holes],
                    'material': f.material, 'override': f.override,
                    'region': f'{ci}:{f.region}',
                })

        out: list[Path] = []
        for p in result:
            if id(p) in replaced:
                out.extend(replaced[id(p)])
            elif not (id(p) in elem_of and
                      uf.find(elem_of[id(p)]) in net_roots):
                out.append(p)
            # else: dropped by the network (e.g. a cap of an attached end)
        out.extend(appended)
        return out, summary

    def _junction_info(self, p, corner) -> dict:
        info = {'x': p.x, 'y': p.y, 'key': None, 'corner': False,
                'treatment': None, 'radius': None}
        if corner is not None:
            js = next((j for j in self.junction_overrides
                       if j.key == corner.key), None)
            info.update(key=corner.key, corner=True,
                        treatment=js.treatment if js else self.junction_style,
                        radius=js.radius if js else self.junction_radius,
                        override=js is not None)
            # radius actually built: limited by the geometry, or derived for
            # the inner face of a wall turn (concentric with its outer face)
            info.update(actual_radius=getattr(corner, 'actual', None),
                        limited=bool(getattr(corner, 'limited', False)),
                        partner=getattr(corner, 'partner', None),
                        derived=getattr(corner, 'requested', 0) is None)
        return info

    def _region_infill(self, N, res, comps, comp_of, fillets, infills,
                       sys_ids, root, filled, appended, cls_of, summary,
                       declared=None):
        """Fill each wall-material region reached by an infill anchored in
        this network (first infill per region wins)."""
        import infill as IF
        arr = res.arrangement
        for inf in infills:
            anchor = self._anchor(inf)
            if anchor not in sys_ids:
                continue
            # REACH (2026-10-06 fix): the regions bounded by the anchor's
            # beads, then — transitively — every region bounded by a source
            # that bounds a region already reached. Openings / Trim REMOVE
            # material; they never change which infill fills it: a circle arc
            # cut free of the rectangle by two openings stays filled by the
            # rectangle's infill exactly as the uncut wall was. (Uncut: the
            # same single region as before.)
            elems, targets = {f'S:{anchor}'}, set()
            edge_info = [({res.beads[b].bead.element for b in owners},
                          {comp_of[fid] for fid in arr.edge_faces(u, v) if fid in comp_of})
                         for (u, v), owners in arr.edges.items()]
            while True:
                new = set()
                for es, ks in edge_info:
                    if es & elems:
                        new |= ks
                if new <= targets:
                    break
                targets |= new
                for es, ks in edge_info:
                    if ks & targets:
                        elems |= {e for e in es if e.startswith('S:')}
            # A declared (closed single-bead) region also owns the ISLANDS
            # nested in its voids — material components not touching the
            # anchor but lying inside it (even-odd nesting).
            ring = (declared or {}).get(anchor, [None])[0]
            if ring is not None:
                for k in set(comp_of.values()):
                    outer = comps[k].rings[0] if comps[k].rings else None
                    if outer and all(_point_in_polygon(q, ring)
                                     for q in outer[::max(1, len(outer) // 8)]):
                        targets.add(k)
            mine, shadow, holes = 0, None, []
            for k in sorted(targets):
                if (root, k) in filled:
                    shadow = filled[(root, k)]
                    continue
                filled[(root, k)] = inf.id
                rings = [N.fillet_ring(r, fillets) for r in comps[k].rings]
                holes.append(len(rings) - 1)
                # Generated after the network (stage 10): wall lattice,
                # field fallback (locally repairable) or solid fill.
                summary.setdefault('_infill_jobs', []).append(
                    {'infill': inf, 'rings': rings, 'key': f'{inf.id}~{k}'})
                mine += 1
            summary['infills'].append({
                'id': inf.id, 'regions': mine, 'holes': holes,
                'shadowed_by': shadow,
                'status': 'ok' if mine else
                ('shadowed' if shadow else 'no wall material'),
            })

    def _network_joins(self, N, pieces):
        """Attached ends of the network's thick pieces → joins, and the ids
        of the caps those ends no longer have."""
        joins, dropped = [], set()
        atts = N.find_attachments(pieces)
        done_hubs = set()
        for pc in pieces:
            for which in (0, 1):
                att = atts.get((id(pc), which))
                if att is None:
                    continue
                tag = f'{pc.key}_{"s" if which == 0 else "e"}'
                if att[0] == 'T':
                    jn = N.join_T(pc, which, att[1], tag + 'j')
                    if jn is None:
                        continue
                    pc.attached[which] = True
                    dropped.update(pc.cap_ids[which])
                    joins.append(jn)
                else:
                    _, pt, members = att
                    key = (round(pt.x, 6), round(pt.y, 6))
                    if key in done_hubs:
                        continue
                    done_hubs.add(key)
                    jn = N.join_hub(members, pt, f'hub{len(done_hubs)}')
                    if jn is None:
                        continue
                    for m, w in members:
                        m.attached[w] = True
                        dropped.update(m.cap_ids[w])
                    joins.append(jn)
        return joins, dropped

    def _lattice_cavity(self, N, li, full_by_id, comp_pieces, lat_bounds):
        """Wall region implied by a lattice spanning two separate systems."""
        pa, pb = full_by_id.get(li.path_a_id), full_by_id.get(li.path_b_id)
        if pa is None or pb is None:
            return None
        a, b = pa.sample_points(), pb.sample_points()
        if pa.closed and pb.closed:
            ann = N.Shape([a, b])
            islands = []
            for pc in comp_pieces:
                if not pc.closed or pc.sys_id in lat_bounds[li.id]:
                    continue
                ring = max((w[1] for w in pc.walls),
                           key=lambda r: abs(_polygon_area(r)))
                if ann.contains(ring[0]):
                    islands.append(ring)
            return N.Shape([a, b] + islands), []
        if not pa.closed and not pb.closed:
            return (N.Shape([a + list(reversed(b))]),
                    [[a[-1], b[-1]], [b[0], a[0]]])
        return None

    def _emit_network(self, N, res, replaced, appended, cls_of, summary):
        """Turn resolved beads back into effective paths."""
        for rb in res.beads:
            bd = rb.bead
            p = bd.path
            if bd.cls == N.VIRTUAL:
                for k, ch in enumerate(rb.chains):
                    dp = DerivedPath(ch, closed=False, role='cap',
                                     label='network_face',
                                     source_id=bd.element[2:],
                                     treatment_id='network',
                                     id=f'{bd.id}~n{k}')
                    cls_of[id(dp)] = N.FACE
                    appended.append(dp)
                continue
            if p is None:                      # new join geometry
                if rb.whole:
                    dp = DerivedPath(bd.pts, closed=False, role='cap',
                                     label='junction_round'
                                     if bd.id.startswith('junction:')
                                     else 'network_join',
                                     source_id=bd.element[2:],
                                     treatment_id='network', id=bd.id)
                    cls_of[id(dp)] = bd.cls
                    appended.append(dp)
                else:
                    for k, ch in enumerate(rb.chains):
                        dp = DerivedPath(ch, closed=False, role='cap',
                                         label='network_join',
                                         source_id=bd.element[2:],
                                         treatment_id='network',
                                         id=f'{bd.id}~n{k}')
                        cls_of[id(dp)] = bd.cls
                        appended.append(dp)
                continue
            if rb.whole:
                replaced[id(p)] = [p]
                cls_of[id(p)] = bd.cls
                continue
            sid = bd.element[2:]
            is_source = bd.element.startswith('S:') and (
                p.id == sid or (p.id.startswith(sid + '~') and
                                getattr(p, 'treatment_id', '') == 'opening_cut'))
            if is_source:
                summary['modified_sources'].append(sid)
            pieces = []
            for k, ch in enumerate(rb.chains):
                dp = DerivedPath(
                    ch, closed=False, id=f'{p.id}~n{k}', role=p.role,
                    label=p.label,
                    source_id=getattr(p, 'source_id', '') or p.id,
                    treatment_id='network_src' if is_source else
                    getattr(p, 'treatment_id', ''))
                cls_of[id(dp)] = bd.cls
                pieces.append(dp)
            replaced[id(p)] = pieces
        summary['modified_sources'] = sorted(set(summary['modified_sources']))

    def printable_centerlines(self, paths=None, meta=None) -> list:
        """The resolved PRINTABLE centerlines — exactly the strands the
        router prints (so reference lines, construction geometry and
        non-printed region boundaries never get a bead):
        [{'id', 'pts': [[x, y]…], 'closed'}]."""
        rl = self.to_routing_layer(paths, meta)
        return [{'id': s.id, 'pts': [[q.x, q.y] for q in s.points], 'closed': bool(s.closed)}
                for s in rl.strands]

    def to_routing_layer(self, paths=None, meta=None):
        """
        Convert effective paths to the Strand/Layer format expected by the
        routing engine in toolpath_proto/graph.py. Visible wall faces
        (and single-bead walls) carry a higher retrace cost than internal
        geometry, so continuity transitions hide inside the wall.
        """
        import sys, os
        sys_path = os.path.join(os.path.dirname(__file__), '..', 'toolpath_proto')
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from geometry import Strand, Layer as RoutingLayer, Vec2 as RVec2
        import network as N

        if paths is None:
            paths, meta = self._build_effective()
        meta = meta or {}
        cls = meta.get('cls', {})
        pts_of = meta.get('pts', {})
        strands = []
        for path in paths:
            pts = pts_of.get(id(path)) or path.sample_points()
            if len(pts) < 2:
                continue
            rvecs = [RVec2(p.x, p.y) for p in pts]
            strands.append(Strand(
                id=path.id,
                role=path.role,
                points=rvecs,
                closed=path.closed,
                retrace_cost=(1.0 if cls.get(id(path)) == N.INTERNAL
                              else N.FACE_RETRACE_COST),
                kind=('field' if path.role == 'lattice' else
                      'internal' if cls.get(id(path)) == N.INTERNAL else 'face'),
                # area infill: leftover odd ends hop by a short travel rather
                # than printing the perimeter twice
                travel_pairing=getattr(path, 'treatment_id', '') in (
                    'solid_infill', 'solid_link', 'solid_perimeter'),
                join_group=getattr(path, 'join_group', None),
                join_points=getattr(path, 'join_points', None),
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
            'cap_corner_radius': self.cap_corner_radius,
            'openings': [o.to_dict() for o in self.openings],
            'region_overrides': [r.to_dict() for r in self.region_overrides],
            'infills': [i.to_dict() for i in self.infills],
            'junction_style': self.junction_style,
            'junction_radius': self.junction_radius,
            'junction_overrides': [j.to_dict() for j in self.junction_overrides],
            'network_walls': [w.to_dict() for w in self.network_walls],
            'wall_systems': [w.to_dict() for w in self.wall_systems],
            'wall_relations': [w.to_dict() for w in self.wall_relations],
            'return_paths': self.return_paths,
            'prefer_closed': self.prefer_closed,
        }


def _clip_to_rings(polylines, rings) -> list:
    """Parts of the polylines inside the region (even-odd rings), split
    exactly where they cross a ring (a crossing within 1e-6 of a vertex is
    that vertex); vertices inside are kept unchanged.
    A part lying ON a ring (exactly where this design prints a boundary)
    is dropped: that boundary prints it. (Ring segments are gridded — the
    result is exactly that of testing every segment.)"""
    segs = [(r[i], r[(i + 1) % len(r)]) for r in rings for i in range(len(r))]
    xs = [p.x for c, d in segs for p in (c, d)] or [0.0]
    ys = [p.y for c, d in segs for p in (c, d)] or [0.0]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    cell = max(span / max(8.0, math.sqrt(len(segs))), 1e-3)
    grid: dict = {}
    for k, (c, d) in enumerate(segs):
        for gx in range(int(math.floor(min(c.x, d.x) / cell)), int(math.floor(max(c.x, d.x) / cell)) + 1):
            for gy in range(int(math.floor(min(c.y, d.y) / cell)), int(math.floor(max(c.y, d.y) / cell)) + 1):
                grid.setdefault((gx, gy), []).append(k)

    def near(x0, y0, x1, y1, pad=0.0):
        out = set()
        for gx in range(int(math.floor((x0 - pad) / cell)), int(math.floor((x1 + pad) / cell)) + 1):
            for gy in range(int(math.floor((y0 - pad) / cell)), int(math.floor((y1 + pad) / cell)) + 1):
                out.update(grid.get((gx, gy), ()))
        return out

    def on_ring(q):
        return any(_seg_dist_pt(q, *segs[k]) < 1e-6 for k in near(q.x, q.y, q.x, q.y, 2e-6))

    def inside(q):
        return not on_ring(q) and sum(1 for r in rings if _point_in_polygon(q, r)) % 2 == 1

    out = []
    for poly in polylines:
        cur = []
        for a, b in zip(poly, poly[1:]):
            ts = [0.0, 1.0]
            L_ab = a.dist(b)
            for k in sorted(near(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y), 1e-6)):
                c, d = segs[k]
                hit = _seg_intersect_param(a, b, c, d)
                # a crossing within 1e-6 of a vertex IS that vertex (a shared
                # jamb crossing must end every piece at the same point)
                if hit is not None and 1e-6 < hit[0] * L_ab < L_ab - 1e-6:
                    ts.append(hit[0])
            ts = sorted(set(ts))
            for t0, t1 in zip(ts, ts[1:]):
                p0, p1 = a.lerp(b, t0) if t0 > 0 else a, b if t1 >= 1 else a.lerp(b, t1)
                if inside(a.lerp(b, (t0 + t1) / 2)):
                    if not cur:
                        cur = [p0]
                    elif cur[-1].dist(p0) > 1e-9:
                        out.append(cur)
                        cur = [p0]
                    cur.append(p1)
                elif cur:
                    if len(cur) >= 2:
                        out.append(cur)
                    cur = []
        if len(cur) >= 2:
            out.append(cur)
    return [p for p in out if len(p) >= 2]


def _seg_dist_pt(q, a, b) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q.x - a.x) * dx + (q.y - a.y) * dy) / L2))
    return math.hypot(q.x - a.x - t * dx, q.y - a.y - t * dy)


def resolve_route_origins(strands, origins) -> tuple[list, list]:
    """ROUTE ORIGIN → a routing node. Each origin names a printable strand
    (stable id of the router's strand) and a fraction u of its length; the
    point there is inserted into that strand as a vertex (a collinear
    split — the geometry is unchanged) so the route can begin there.
    Returns (points, report). An origin whose strand no longer exists is
    'missing' and the route falls back to automatic selection — it is never
    moved onto other geometry. (Whether the strand's component is closed is
    decided by the router: an open component ignores its origin.)"""
    by_id = {st.id: st for st in strands}
    pts, report = [], []
    for o in origins or []:
        sid, u = o.get('strand'), float(o.get('u', 0.0) or 0.0)
        st = by_id.get(sid)
        if st is None or len(st.points) < 2:
            report.append({'strand': sid, 'u': u, 'status': 'missing'})
            continue
        P = st.points
        ring = list(P) + ([P[0]] if st.closed else [])
        lens = [ring[i].dist(ring[i + 1]) for i in range(len(ring) - 1)]
        L = sum(lens)
        if L <= 1e-9:
            report.append({'strand': sid, 'u': u, 'status': 'missing'})
            continue
        target = (u % 1.0 if st.closed else max(0.0, min(1.0, u))) * L
        pos = o.get('pos')
        if pos and len(pos) == 2:
            # the start's POSITION wins over its fraction (the strand's shape can
            # change — e.g. Parallel Walls move their lane-change seam there)
            best, acc_ = None, 0.0
            for i in range(len(lens)):
                a_, b_ = ring[i], ring[i + 1]
                dx, dy = b_.x - a_.x, b_.y - a_.y
                l2 = dx * dx + dy * dy
                t = 0.0 if l2 < 1e-18 else max(0.0, min(1.0, ((pos[0] - a_.x) * dx + (pos[1] - a_.y) * dy) / l2))
                dd = math.hypot(a_.x + t * dx - pos[0], a_.y + t * dy - pos[1])
                if best is None or dd < best[0]:
                    best = (dd, acc_ + t * lens[i])
                acc_ += lens[i]
            target = best[1]
        acc, k = 0.0, 0
        while k < len(lens) - 1 and acc + lens[k] < target:
            acc += lens[k]
            k += 1
        f = 0.0 if lens[k] < 1e-12 else (target - acc) / lens[k]
        a, b = ring[k], ring[k + 1]
        q = type(a)(a.x + f * (b.x - a.x), a.y + f * (b.y - a.y))
        if q.dist(a) < 1e-9:
            q = a
        elif q.dist(b) < 1e-9:
            q = b
        else:
            st.points = list(P[:k + 1]) + [q] + list(P[k + 1:])
        pts.append(q)
        report.append({'strand': sid, 'u': u, 'x': q.x, 'y': q.y, 'status': 'ok'})
    return pts, report


def _same_polyline(a: list[Vec2], b: list[Vec2]) -> bool:
    return len(a) == len(b) and all(p.dist(q) <= 1e-12 for p, q in zip(a, b))


def _point_beside(pts: list[Vec2], closed: bool, s: float,
                  offset: float) -> Optional[Vec2]:
    """Point at arc length s along pts, `offset` along the left normal."""
    if len(pts) < 2:
        return None
    cum = _cum_lengths(pts, closed)
    p, k = _locate_s(pts, cum, s, closed)
    a, b = pts[k], pts[(k + 1) % len(pts)]
    d = (b - a).normalized()
    return Vec2(p.x - d.y * offset, p.y + d.x * offset)


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
