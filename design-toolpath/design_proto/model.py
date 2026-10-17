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

from material import MaterialSpec, RETURN_MIN

# PHYSICAL WALL WIDTH: the smallest skin-centreline band of a skin wall whose
# stored thickness is thinner than two beads (an old file / a material change)
INSET_MIN_SPAN = RETURN_MIN

# SKIN constructions: their envelope faces are printed beads (inset half a
# bead under physical rules); generated constructions place their own paths
SKIN_SYSTEMS = ('hollow', 'skin_web')

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
            # (per-path walls were removed 2026-10-16 — a wall's envelope lives in the
            # document / its Wall System record; the key is kept, always None, so the
            # strict golden response hashes stay byte-identical. Drop it at the next
            # deliberate golden re-baseline.)
            'wall': None,
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
                          landings: tuple = ((), ()),
                          include_src: bool = True):
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

    include_src=False: the source is NOT a line of the assembly (an inset
    skin wall's aligned reference lies outside its printed faces, PHYSICAL
    WALL WIDTH): the cross-section is the offsets alone.

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
    entries: list[tuple[float, list[Vec2]]] = [(0.0, src_pts)] if include_src else []
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
    # [a, b, point at a | None, point at b | None]: the resolved section as an
    # arc interval, frozen — set only by Assembly profile cuts (trim.resolve)
    pinned: list = None

    def to_dict(self) -> dict:
        d = {'id': self.id, 'source_path_id': self.source_path_id,
             'start': list(self.start), 'end': list(self.end),
             'inside': dict(self.inside), 'u_mid': self.u_mid}
        if self.pinned:
            d['pinned'] = list(self.pinned)
        return d


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
        return {'thickness': self.thickness, 'align': self.align,
                'print_reference': self.print_reference}

    def offsets(self, path_id: str, closed: bool,
                pts: list[Vec2], inset: float = 0.0) -> list['OffsetTreatment']:
        """The wall's face lines. inset > 0 (PHYSICAL WALL WIDTH, 2026-10-16:
        a SKIN construction — Hollow / Skin + Web — whose faces are printed
        beads): the faces are the bead CENTRELINES, `inset` (half a bead)
        inside the finished envelope, so the deposited width is the
        thickness. An aligned wall's reference (a finished face) then lies
        outside the printed band and is not printed (see inset_reference)."""
        t = max(0.0, float(self.thickness))
        if t <= 1e-9:
            return []
        align = self.resolved_align(closed)
        ccw = _polygon_area(pts) > 0 if closed and len(pts) >= 3 else True
        sign = {'left': 1.0, 'right': -1.0,
                'inside': 1.0 if ccw else -1.0,
                'outside': -1.0 if ccw else 1.0}.get(align)
        if inset > 0.0:
            # (a stale thickness thinner than two beads keeps a minimal band)
            span = max(t - 2.0 * inset, INSET_MIN_SPAN)
            near = (t - span) / 2.0
            if sign is not None:
                return [OffsetTreatment(f'{path_id}.wall', path_id, sign * (near + span), label='wall'),
                        OffsetTreatment(f'{path_id}.wall_near', path_id, sign * near, label='wall')]
            return [OffsetTreatment(f'{path_id}.wall+', path_id, span / 2, label='wall'),
                    OffsetTreatment(f'{path_id}.wall-', path_id, -span / 2, label='wall')]
        if sign is not None:
            return [OffsetTreatment(f'{path_id}.wall', path_id, sign * t,
                                    label='wall')]
        return [OffsetTreatment(f'{path_id}.wall+', path_id, t / 2, label='wall'),
                OffsetTreatment(f'{path_id}.wall-', path_id, -t / 2, label='wall')]

    def inset_reference(self, closed: bool) -> bool:
        """With inset faces, does the reference lie OUTSIDE the printed band
        (an aligned wall: the reference is a finished face)?"""
        return self.resolved_align(closed) != 'center'


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


def _x_arms(ring, closed, L, at, need, c, d):
    """Does the polyline (ring points, total length L) run on STRAIGHT for
    `need` both ways from arc position `at`, its two arms on opposite sides
    of the line c→d (the other wall's centre line there)?"""
    if not closed and (at < need or L - at < need):
        return False
    pa = _point_at(ring, (at - need) % L if closed else at - need)
    pb = _point_at(ring, (at + need) % L if closed else at + need)
    if pa.dist(pb) < 1.8 * need:
        return False
    side = lambda q: (d.x - c.x) * (q.y - c.y) - (d.y - c.y) * (q.x - c.x)
    return side(pa) * side(pb) < 0


def _point_at(ring, s):
    """The point at arc length s along an open polyline."""
    acc = 0.0
    for a, b in zip(ring, ring[1:]):
        l_ = a.dist(b)
        if acc + l_ >= s:
            return a.lerp(b, 0.0 if l_ < 1e-12 else (s - acc) / l_)
        acc += l_
    return ring[-1]


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


# ---------------------------------------------------------------------------
# PrintLayer — assembles effective print geometry
# ---------------------------------------------------------------------------

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
    wall_systems: list[WallSystem] = field(default_factory=list)
    wall_relations: list[WallRelation] = field(default_factory=list)
    # return_paths ("Infill repair" in the UI): local continuity repair of
    # the wide-region field fallback (route_plan.repair); motif wall
    # lattices and solid infill do not use it. prefer_closed: prefer a
    # closed (start = end) layer route when it is cheap.
    return_paths: bool = True
    prefer_closed: bool = True
    # MATERIAL / BEAD (material.py): the physical bead deposited around each
    # printable centerline; under `physical` its width / overlaps drive the
    # lane spacing, landings, splices and the Assembly support.
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
    # LOCAL CUT JOIN (ASSEMBLY PROFILES, layer_design.py): with a
    # lattice_reference, the reference strands a NEW end wall (an Assembly
    # opening's cut face / a tapered wall's free end) cuts are paired along
    # that end wall and their tails bent to meet at one point on it
    # (_join_cut_ends) — the cut stays local, the route can close.
    lattice_join: bool = False
    # PHASE REFERENCE (ASSEMBLY PROFILES, layer_design.py): the generated Wall
    # System frames of the same layer WITHOUT the cuts (wall_systems.generate
    # report['frames']); a cut variant's Interleaved / Linked / Chained Loop
    # runs keep that pattern and only solve their new free ends. None = off.
    wall_system_reference: Optional[list] = None
    # CROSS-Z TRANSITION of an Assembly opening (layer_assembly/profiles.py):
    # virtual openings [{source_path_id, center_s, width, alpha}] — NOT cut;
    # Wall Systems / the referenced lattice are pre-deformed by alpha toward
    # the end motif such a cut would have (_virtual_faces).
    cut_transitions: list = field(default_factory=list)
    # ARCHITECTURAL ENVELOPE ONLY (deferred print paths, 2026-10-11): stop
    # after the wall networks — walls, trims, openings, junctions and the
    # material rings exactly as a full build resolves them — before Wall
    # Systems, the wall lattice, region infill and routing (envelope.py).
    envelope_only: bool = False

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

    def _normalized_cap_style(self, style=None) -> str:
        """
        Normalize cap_style aliases:
          'round' (legacy) → 'full_round'
          'rounded' → 'rounded_corners'
        """
        s = (style if style is not None else (self.cap_style or 'flat')).lower()
        if s == 'round':
            return 'full_round'
        if s == 'rounded':
            return 'rounded_corners'
        return s

    def _own_cap(self, sid):
        """A wall's OWN end-cap shape (M3, 2026-10-15: `path['cap']` =
        {style, corner_radius}), or None — the project default applies."""
        p = self._path_by_id(str(sid).split('~')[0]) if sid is not None else None
        cap = getattr(p, 'cap', None) if p is not None else None
        return cap if isinstance(cap, dict) and cap.get('style') else None

    def _cap_radius_of(self, sid) -> float:
        cap = self._own_cap(sid)
        if cap is not None and cap.get('corner_radius') is not None:
            return float(cap.get('corner_radius') or 0.0)
        return self.cap_corner_radius

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
        cap = self._own_cap(sid)
        return self._normalized_cap_style(cap['style'] if cap is not None else None)

    def _opening_cap_reach(self, offsets, sid=None) -> float:
        """How far this wall system's end treatment protrudes past a cut
        face: the effective cap radius for its total thickness W."""
        dists = ([] if sid in getattr(self, '_ref_outside', ()) else [0.0]) + \
            [ot.distance for ot, d in offsets if len(d.sample_points()) >= 2]
        if not dists:
            return 0.0
        W = max(dists) - min(dists)
        style = self._cap_style_of(sid)
        if W <= 1e-9 or style == 'flat':
            return 0.0
        if style == 'full_round':
            return W / 2.0
        return min(max(0.0, self._cap_radius_of(sid)), W / 2.0)

    def effective_paths(self) -> list[Path]:
        """
        Canonical pipeline:
          raw source → _processed_source_pts (ONE per source)
            → every offset of this source uses the same processed pts
              (offsets are always computed on the FULL, uncut wall)
            → openings cut the whole wall assembly into open pieces
            → end treatment per wall-system piece (not per offset); the
              same builder closes original open ends and opening faces
            → wall networks: systems that touch / cross are combined into
              one printable region (network.py); attached ends splice
              into their host instead of being capped
            → Wall Systems fill the material regions; the wall lattice /
              region infill fills what remains (wall_lattice, infill, solid)
        Sources without openings produce exactly the pre-opening output,
        and systems that touch nothing produce exactly the pre-network
        output.
        """
        return self._build_effective()[0]

    def _contact_stage(self):
        """(processed polylines {id: pts}, visible sources in order, snapped
        ends) — stages 1 / 1b of _build_effective: the DESIGN geometry every
        contact (trims, junctions) is found on. Also kept as
        `_contact_sources` for the shadow junction records (junctions.py)."""
        import network as N
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
        self._contact_sources = (list(source_paths_in_order), dict(processed))
        return processed, source_paths_in_order, snapped

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
        processed, source_paths_in_order, snapped = self._contact_stage()
        # (M4 shadow junctions, internal only: the production junctions,
        # recorded by _apply_networks — nothing serialised)
        self._production_junctions = []

        # 1c) TRIMS: sections of sources between their contacts with other
        # sources (design geometry, untrimmed), and the sections the layer's
        # trims name. A trimmed source keeps its parametric identity; its
        # removed intervals cut its whole wall assembly in stage 4 (as open
        # pieces with FREE ends) and it no longer bounds a closed region.
        import trim as TR
        trim_secs = TR.sections(source_paths_in_order, processed)
        trim_removed, trim_status = TR.resolve(self.trims, trim_secs, set(processed), processed,
                                               {p.id: bool(p.closed) for p in source_paths_in_order})
        self._trimmed = {pid for pid, iv in trim_removed.items() if iv}

        # 1d) M6 SOURCE-WALL ATTACHMENT (WALL_ARCHITECTURE.md §4 "E"): a branch
        # whose end lies on another wall's SOURCE line joins that WALL; its
        # PHYSICAL end is derived from the host's envelope on the branch's
        # side (`_attach_branches`) — never from whichever offset face its
        # line happens to touch. The architectural relationship (connectivity
        # `source_nets`, wall specs) is taken from the authored geometry first.
        source_nets = self._source_networks(N, source_paths_in_order, processed)
        wall_offs, ref_only = self._wall_offsets(source_paths_in_order,
                                                 processed, source_nets)
        shifts = self._attach_branches(source_paths_in_order, processed, wall_offs, trim_removed)
        for bid, d in shifts.items():          # (a moved START shifts arc positions on the branch)
            if bid in trim_removed:
                trim_removed[bid] = [(a + d, b + d) + tuple(t[2:]) for t in trim_removed[bid]
                                     for a, b in [t[:2]]]
        saved_openings = self.openings
        if shifts:
            import dataclasses
            self.openings = [dataclasses.replace(o, center_s=o.center_s + shifts[o.source_path_id])
                             if o.source_path_id in shifts else o for o in self.openings]
        try:
            return self._build_effective_rest(N, result, cls_of, elem_of, processed, source_paths_in_order,
                                              snapped, trim_secs, trim_removed, trim_status,
                                              source_nets, wall_offs, ref_only)
        finally:
            self.openings = saved_openings

    def _build_effective_rest(self, N, result, cls_of, elem_of, processed, source_paths_in_order,
                              snapped, trim_secs, trim_removed, trim_status,
                              source_nets, wall_offs, ref_only):
        import trim as TR
        attached = {a['branch'] for a in getattr(self, '_attachments', [])}

        # 2) Full (uncut) source geometry = the processed polyline. If
        # rounding (or an end snap) changed it we wrap it in a DerivedPath;
        # otherwise the parametric source path flows through unchanged
        # (identity preserved). Either way every consumer — lattice,
        # routing, junctions — uses the processed polyline (pts_of), the
        # same one the offsets and the canvas use. (Curves used to be
        # routed at their 64-point default sampling instead.)
        full_sources: dict[str, Path] = {}
        pts_of: dict[int, list[Vec2]] = {}      # id(path) → printed polyline
        for p in source_paths_in_order:
            pts = processed[p.id]
            if p.id in snapped or p.id in attached or (self._corner_r(p) > 0.0 and
                                                       _eligible_for_rounding(p)):
                full_sources[p.id] = DerivedPath(
                    pts, closed=p.closed, id=p.id, role=p.role,
                    label=p.label, source_id=p.id,
                    treatment_id='snap' if p.id in snapped else ('attach' if p.id in attached else 'round'))
            else:
                full_sources[p.id] = p
                pts_of[id(p)] = pts

        # 3) Full offset-derived paths. All offsets of the same source use
        # the SAME processed polyline — no re-sampling or re-rounding.
        offsets_by_source: dict[str, list[tuple[OffsetTreatment, DerivedPath]]] = {}
        all_offsets: list[DerivedPath] = []
        source_of: dict[str, str] = {p.id: p.id for p in source_paths_in_order}
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
        # a cut wall's face lines are only its SURVIVING pieces: a generated
        # band must never follow its own face through a trimmed-away section
        # (or an opening) — trims stay authoritative after Wall System assignment
        for sid_, plan in plans.items():
            faces = []
            for pc in plan.pieces:
                fl = [list(wp) for _, wp in pc.walls if len(wp) >= 2]
                if len(fl) == 1 and len(pc.src_pts) >= 2:
                    fl.append(list(pc.src_pts))       # (aligned wall: the path is a face)
                faces += [(f, False) for f in fl]
            self._src_faces[sid_] = faces
        # each source's surviving CENTRE lines (X crossings between generated walls)
        self._src_center = {p.id: ([(list(pc.src_pts), False) for pc in plans[p.id].pieces
                                    if len(pc.src_pts) >= 2] if p.id in plans
                                   else [(list(processed[p.id]), bool(p.closed))])
                            for p in source_paths_in_order}
        self._src_order = {p.id: i for i, p in enumerate(source_paths_in_order)}

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
                net_pieces.append(self._inset_piece(N.NetPiece(
                    p.id, p.id, p.closed, walls,
                    [None, None] if p.closed else ['free', 'free'])))
                continue
            for k, pc in enumerate(plan.pieces):
                walls = [(0.0, pc.src_pts, f'{p.id}~{k}')] + \
                        [(ot.distance, wp, f'{ot.id}~{k}') for ot, wp in pc.walls]
                walls.sort(key=lambda w: -w[0])
                # opening faces are CUT ends; path ends and trim ends are
                # FREE (a trimmed wall may join the wall it now ends on)
                net_pieces.append(self._inset_piece(N.NetPiece(
                    p.id, f'{p.id}~{k}', False, walls,
                    ['cut' if pc.start_cut and not pc.start_cut.trim else 'free',
                     'cut' if pc.end_cut and not pc.end_cut.trim else 'free'])))
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
                                            self._cap_radius_of(src.id), landings,
                                            include_src=src.id not in self._ref_outside)
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

        # 9) Wall networks.
        result, network = self._apply_networks(
            N, result, cls_of, elem_of, pts_of, net_pieces, plans, offsets_by_source, processed)
        regions = network.pop('_rings', [])
        jobs = network.pop('_infill_jobs', [])
        network['wall_systems'] = []
        self._ws_frames = []                 # generated Wall System frames (the PHASE REFERENCE of cut variants)
        network['wall_system_membership'] = dict(getattr(self, '_wall_system_of', {}) or {})
        network['source_walls'] = dict(getattr(self, '_source_walls', {}) or {})
        if jobs and not self.envelope_only:
            jobs = self._restrict_webs(N, result, cls_of, regions, jobs)
        if getattr(self, '_wall_systems', None) and not self.envelope_only:
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
        if getattr(self, '_attachments', None):          # (M6: only where a physical end was derived)
            network['attachments'] = [dict(x) for x in self._attachments]
        network['wall_relations'] = dict(getattr(self, '_relation_status', {}))
        network['derived_sources'] = {
            p.id: [[q.x, q.y] for q in p.points]
            for p in self.source_paths if isinstance(p, InsetPath)}
        network['source_networks'] = [{'id': f'N{k + 1}', 'sources': ids}
                                      for k, ids in enumerate(n for n in source_nets if len(n) > 1)]
        meta = {'cls': cls_of, 'pts': pts_of, 'network': network,
                'regions': regions}
        if self.envelope_only:
            # the ARCHITECTURAL ENVELOPE: `regions` are the very material
            # rings Wall Systems / the lattice would fill (envelope.py)
            meta['processed'] = processed
            return result, meta
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
                    joined = None
                    if self.lattice_join:
                        pieces, joined = _join_cut_ends(pieces, ref, job['rings'], self.material.bead_width)
                    if self.cut_transitions:
                        pieces = _bend_virtual(pieces, self._virtual_faces(), job['rings'], self.material.bead_width)
                    for j, poly in enumerate(pieces):
                        dp = DerivedPath(poly, closed=False, role='lattice',
                                         label=inf.pattern, source_id=inf.id,
                                         treatment_id='infill', id=f"{job['key']}.r{j}")
                        cls_of[id(dp)] = N.INTERNAL
                        result.append(dp)
                    rep_ = lattice.setdefault(inf.id, {'motif': False, 'inherited': True, 'regions': [],
                                                      'lineage': (self.lattice_lineage or {}).get(inf.id)})
                    rep_['regions'].append({'inherited': True, 'pieces': len(pieces),
                                            'reference_polylines': len(ref),
                                            **({'joined': joined} if joined is not None else {})})
                    continue
                phys = self.material.physical
                # a Skin + Web web (owned record): TARGET WEB ANGLE — the
                # pitch follows the cavity (its legacy `spacing` is ignored)
                web_angle = None if not inf.owner or pattern == 'truss' else \
                    float(inf.params.get('web_angle') or WL.WEB_ANGLE_DEFAULT)
                lp = WL.plan(job['rings'], spacing, pattern, inf.variation_index,
                             self.prefer_closed or phys,
                             float(inf.params.get('max_unsupported', 0) or 0) or None,
                             contact=self.material.contact_separation() if phys else 0.0,
                             closed=phys,
                             stations=(self.lattice_stations or {}).get(inf.id),
                             track=((self.lattice_track or {}).get(inf.id) or (None, None))[0],
                             track_map=((self.lattice_track or {}).get(inf.id) or (None, None))[1],
                             bead=self.material.bead_width if phys else 0.0,
                             truss=inf.params if pattern == 'truss' else None,
                             web_angle=web_angle)
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
                        # (a run-less plan — no cavity — reports neither)
                        'max_unsupported': max((r['max_unsupported'] for r in regs
                                                if r.get('max_unsupported') is not None), default=None),
                        'max_unsupported_limit': max((r['max_unsupported_limit'] for r in regs
                                                      if r.get('max_unsupported_limit') is not None), default=None),
                        'corners': sum(run.get('corners', 0) for r in regs for run in r['runs']),
                        # V1 / V2 only move the phase of closed loops and lone
                        # walls; in networks junction coherence fixes it
                        'variation_effective': any(run.get('phase_free', run['motif'] in ('loop', 'lone'))
                                                   for r in regs for run in r['runs']),
                        # the lineage's SHARED lattice (Layer Designs), if any
                        'lineage': lr.get('lineage'),
                        # ADAPTIVE TRUSS: effective pitch / stitch constructions
                        'truss': next((r['truss'] for r in regs if r.get('truss')), None),
                        # TARGET WEB ANGLE (Skin + Web): derived pitch per region
                        'web_angle': [r['web_angle'] for r in regs if r.get('web_angle')] or None}
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

    def _region_faces(self, N, result, cls_of, rings):
        """(members, msid): the face beads bounding one wall-material region
        and the source each belongs to — a junction fillet between two
        different sources is '__J' (followed by neither band)."""
        known = sorted((q.id for q in self.source_paths), key=len, reverse=True)
        members, msid = [], {}
        for p in result:
            if cls_of.get(id(p)) != N.FACE:
                continue
            pts = p.sample_points()
            sm = (pts[::max(1, len(pts) // 12)] + [pts[-1]]) if pts else []
            if not sm or not all(min(N.dist_to_polyline(q, r, True) for r in rings) < 0.05 for q in sm):
                continue
            members.append(p)
            msid[id(p)] = (getattr(p, 'source_id', None) or p.id).split('~')[0]
            if str(p.id).startswith('junction:'):
                faces_ = str(p.id)[len('junction:'):].split('#')[0].split('|')
                srcs_ = {next((k for k in known if f.startswith(k)), None) for f in faces_} - {None}
                if len(srcs_) > 1:
                    msid[id(p)] = '__J'
        return members, msid

    def _web_envelope(self, N, rings, members, msid):
        """The rings of a region's SKIN + WEB walls alone: every wall of the
        region that does not print a web (a thick wall of another
        construction — Hollow, its own unassigned record, a generated
        system) closed off by a chord across its mouth (the same stitching as
        _split_wall_systems). None when nothing needs closing off, or when a
        non-web wall cannot be closed off (a partition)."""
        def other(sid_):
            if sid_ == '__J' or self._web_member(sid_):
                return False
            sw = (getattr(self, '_source_walls', {}) or {}).get(sid_) or {}
            return float(sw.get('thickness') or 0.0) > 1e-9
        sids = set(msid.values())
        if not any(other(s) for s in sids) or not any(s != '__J' and self._web_member(s) for s in sids):
            return None

        def key(sid_):
            if sid_ == '__J':
                return '__J'
            return sid_ if other(sid_) else None
        keep = getattr(self, '_split_skin_part', None)
        try:
            self._split_skin_part = None
            parts = self._split_wall_systems(N, rings, members, msid, key)
            env = self._split_skin_part if parts is not None else None
        finally:
            self._split_skin_part = keep
        return env['rings'] if env and env.get('rings') else None

    def _restrict_webs(self, N, result, cls_of, regions, jobs):
        """A SKIN + WEB web fills only its OWN walls (2026-10-16): a Wall
        Infill job OWNED by a Skin + Web Wall System, in a region that also
        holds thick walls of another construction (Hollow / an unassigned
        wall's own record), is generated on the web walls' envelope alone.
        Before, the web filled the whole connected material region — a wall
        NOT in the Skin + Web system received its web as soon as it had a
        thickness, as if it were a member. The layer's own unowned Wall
        Infill still fills its whole region. Regions with a GENERATED wall
        are restricted in _apply_wall_systems."""
        gen = getattr(self, '_wall_systems', {}) or {}
        for rings in regions:
            ring0 = rings[0][0] if rings and rings[0] else None
            mine = [j for j in jobs if ring0 is not None and j['rings'] and j['rings'][0]
                    and j['rings'][0][0].dist(ring0) < 1e-6 and getattr(j['infill'], 'owner', None)]
            if not mine:
                continue
            members, msid = self._region_faces(N, result, cls_of, rings)
            if any(s in gen for s in msid.values()):
                continue
            env = self._web_envelope(N, rings, members, msid)
            if env is not None:
                for j in mine:
                    j['rings'] = env
        return jobs

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
                polys, rep = WS.generate(rings, system, bead, contact, self._seam_hints(), self.wall_system_reference,
                                         self._virtual_faces())
                self._ws_frames.extend(rep.pop('frames', []))
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
                    polys, rep = WS.generate(part['rings'], system, bead, contact, self._seam_hints(),
                                             self.wall_system_reference, self._virtual_faces(),
                                             [c['tip'] for c in part.get('chords', []) if c.get('tip') is not None])
                    self._ws_frames.extend(rep.pop('frames', []))
                    polys, spliced, joined, jinfo = self._connect_skins(polys, part, WS, rep.get('envelope') or 10.0)
                    excursions = list(self._excursions_out)
                    for ch in joined:                # the incoming beads are re-emitted, extended
                        drop |= {id(p) for p in members if keyof(msid[id(p)]) is None and
                                 all(min(N.dist_to_polyline(q, ch, False), 1.0) < 0.05 for q in samples[id(p)])}
                    rep.update({'sources': part['sources'], 'region': k, 'status': 'ok',
                                'system_id': self._wall_system_key.get(sid0),
                                'shared_with': sorted(sids - set(part['sources'])),
                                'spliced_skins': spliced, 'junctions': jinfo})
                    gen.append({'part': part, 'polys': polys, 'rep': rep, 'system': system, 'sid0': sid0,
                                'excursions': excursions})
                self._check_junction_motifs(gen, WS, bead, contact)
                self._contact_staples(gen, rings, bead, WS)
                # (pass 10: generated walls meet by PUSHED bands that cross the host's
                # paths — the earlier open-the-cap-and-cut splice is retired)
                skin_part = getattr(self, '_split_skin_part', None)
                if skin_part:
                    # a SKIN wall bridging generated walls (its two faces on two rings):
                    # at each mouth its two face ends continue straight into the host
                    # and are capped there — crossing the host's printed paths
                    # (a mouth the HOST already joined — the skin run there is one of a
                    # generated part's `skins`, carried into the host by _connect_skins —
                    # gets no second bead: its ends would land on that joined run's
                    # vertices as two odd nodes, splitting the route. Case: a line
                    # crossing a generated wall from outside to outside — its exterior
                    # stub is joined by the host, its interior piece bridges)
                    host_mouths = [tuple(sk['ends']) for g in gen for sk in g['part'].get('skins', [])]

                    def _host_joined(ends, tol=0.05):
                        return any(min(ends[0].dist(a) + ends[1].dist(b), ends[0].dist(b) + ends[1].dist(a)) < tol
                                   for a, b in host_mouths)
                    for ci, ch in enumerate(skin_part.get('chords', [])):
                        if not (ch.get('is_end') and not ch.get('same') and ch.get('other') and ch.get('exact')):
                            continue
                        if _host_joined(ch['exact']):
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
                    for xi, (exc, jp, xr) in enumerate(g.get('excursions') or []):
                        # M7: the excursion is its OWN strand, fenced — the route
                        # enters / leaves it only at p1 / p2 on its host strand
                        eid = f"{g['sid0']}~ws{k}p{pi}.x{xi}"
                        dp = DerivedPath(list(exc), closed=False, role='wall_system', label=g['system']['type'],
                                         source_id=xr['excursion_from'], treatment_id='wall_system_path', id=eid)
                        dp.join_group = f'exc:{eid}'
                        dp.join_points = [(q.x, q.y) for q in jp]
                        cls_of[id(dp)] = N.FACE
                        new.append(dp)
                        xr['excursion'] = eid
                    network['wall_systems'].append(g['rep'])
                    systems_used.add(g['system']['type'])
                drop |= {id(p) for p in members if partkey(msid[id(p)]) is not None}
            ring0 = rings[0][0] if rings and rings[0] else None
            mine = [j for j in jobs if ring0 is not None and j['rings'] and j['rings'][0]
                    and j['rings'][0][0].dist(ring0) < 1e-6]
            skin_env = getattr(self, '_split_skin_part', None) if len(keys) > 1 else None
            if skin_env and any(self._web_member(s_) for s_ in sids):
                # MIXED region with Skin + Web members: their web is generated on the
                # skin walls' own envelope (the generated walls' bands closed off) —
                # and, when a thick skin wall of another construction (Hollow) shares
                # it, on the WEB walls' own envelope (_restrict_webs)
                env_ = skin_env['rings']
                if any(getattr(j['infill'], 'owner', None) for j in mine) and \
                        any(s_ not in self._wall_systems and not self._web_member(s_) and
                            float(((getattr(self, '_source_walls', {}) or {}).get(s_) or {}).get('thickness') or 0) > 1e-9
                            for s_ in sids):
                    env_ = self._web_envelope(N, rings, members, msid) or env_
                for j in mine:
                    j['rings'] = env_
                mine = []
            jobs = [j for j in jobs if j not in mine]
            for j in mine:                          # a Wall Infill here is not printed
                for info in network.get('infills', []):
                    if info.get('id') == j['infill'].id:
                        info['status'] = 'wall system'
                        info['wall_system'] = ', '.join(sorted(systems_used))
        return [p for p in result if id(p) not in drop] + new, jobs

    def _contact_staples(self, gen, rings, bead, WS):
        """GENERATED walls that TOUCH (their bands meet, or overlap by less than
        about a bead — a circle grazing a rectangle) but print no crossing near
        the contact get one CONTACT STAPLE (wall_systems.contact_staple): a
        small closed loop crossing both walls' printed paths — physical contact
        and a routing junction, parity unchanged. Walls whose paths already
        cross there (X / T / deep overlaps, pushed branches) are untouched."""
        import network as N_

        def inside(q):                     # (on the faces counts: two walls just touching)
            if sum(_point_in_polygon(q, r) for r in rings) % 2 == 1:
                return True
            return min(N_.dist_to_polyline(q, r, True) for r in rings) <= 0.25 * bead
        for i, ga in enumerate(gen):
            for gb in gen[i + 1:]:
                T_ = max(float(ga['rep'].get('envelope') or 10.0), float(gb['rep'].get('envelope') or 10.0))
                c = WS.band_contact(ga['part']['rings'], gb['part']['rings'], 0.25 * bead)
                if c is None:
                    continue
                loop = WS.contact_staple(ga['polys'], gb['polys'], c, bead, T_, inside)
                if loop is None:
                    continue
                ga['polys'] = list(ga['polys']) + [loop]
                ga['rep'].setdefault('contact_staples', []).append(
                    {'with': gb['part']['sources'], 'at': [round(c.x, 3), round(c.y, 3)]})

    def _check_junction_motifs(self, gen, WS, bead, contact):
        """A Chained Loop JUNCTION MOTIF (the eye at a branch end pushed into a
        host, wall_systems._chain_eye) must really CROSS the host's printed
        paths (or the skin bead closing a skin host's mouth). An end whose eye
        does not is regenerated with the canonical cap there — the contact
        the pushed band always gave is never lost."""
        for g in gen:
            part = g['part']
            if g['system'].get('type') != 'chain' or \
                    not any(i.get('junction_motifs') for i in g['rep'].get('per_run', [])):
                continue
            tips = [c for c in part.get('chords', []) if c.get('tip') is not None]
            others = [pl for h in gen if h is not g for pl in h['polys']]
            others += [list(rec['ends']) for rec in part.get('host_skins', [])]
            T_ = float(g['rep'].get('envelope') or 10.0)
            bad = [c for c in tips if not WS.contact_near(g['polys'], others, c['tip'], 1.5 * T_ + bead)]
            if not bad:
                continue
            keep = [c['tip'] for c in tips if c not in bad]
            polys, rep = WS.generate(part['rings'], g['system'], bead, contact, self._seam_hints(),
                                     self.wall_system_reference, self._virtual_faces(), keep)
            rep.pop('frames', None)
            polys, spliced, joined, jinfo = self._connect_skins(polys, part, WS, rep.get('envelope') or 10.0)
            g['excursions'] = list(self._excursions_out)
            rep.update({k: v for k, v in g['rep'].items() if k not in rep})
            rep['junction_motif_fallbacks'] = len(bad)
            g['polys'], g['rep'] = polys, rep

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
                    cand_ = None                       # (an X yield: the starts across its own band)
                    follow = self._face_follow(key, R_, [(q, runs[q]['pts'][0]) for q in cand]) \
                        if key is not None else None
                    if follow is not None and key is not None:
                        # an X crossing of two Parallel Walls walls: one YIELDS (its
                        # face is not followed across the other wall; it ends there and
                        # is pushed in just past the other's near lane — see _x_yield)
                        r_, ts_ = rings[R_['ring']], tagged[R_['ring']]
                        # (the wall crossed here: the first one after the run's end on its ring)
                        crossed = next((ts_[(R_['g0'] + x) % len(r_)] for x in range(R_['glen'])
                                        if ts_[(R_['g0'] + x) % len(r_)] != '__J'), None)
                        if crossed not in (None, key) and self._x_yield(key, crossed, e):
                            own = [f for f, fc in (getattr(self, '_src_faces', {}) or {}).get(key, [])
                                   if N.dist_to_polyline(e, f, fc) < 0.05]
                            # (the start across this wall's own band, at the same mouth)
                            wT = float(((getattr(self, '_source_walls', {}) or {}).get(key) or {}).get('thickness') or 10.0)
                            across = [q for q in cand if runs[q]['pts'][0].dist(e) < 1.5 * wT + bead_w and not any(
                                N.dist_to_polyline(runs[q]['pts'][0], f, False) < 0.05 for f in own)]
                            if across:
                                follow = None
                                cand_ = across
                    if follow is not None:
                        nxt, fpath = follow
                    elif cand_:
                        nxt = min(cand_, key=lambda q: (e.dist(runs[q]['pts'][0]), q != first))
                        fpath = None
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
                        # (an INSET skin host: measured on its printed band — the
                        # push ends where it did relative to the host's centre)
                        hT = float(getattr(self, '_print_thickness', {}).get(hsid) or
                                   hw.get('thickness') or 10.0)
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
                            flen = sum(a_.dist(b_) for a_, b_ in zip(fr, fr[1:]))
                            fpos = 0.0
                            for a_, b_ in zip(fr, fr[1:]):
                                fpos += a_.dist(b_)
                                sg = b_ - a_
                                den = dv.x * sg.y - dv.y * sg.x
                                if abs(den) < 1e-12:
                                    continue
                                w_ = a_ - mid_
                                tt = (w_.x * sg.y - w_.y * sg.x) / den
                                uu = (w_.x * dv.y - w_.y * dv.x) / den
                                # (only the host face AT this mouth: a face far beyond it —
                                # the ray missing a trimmed / ending host at a corner — is
                                # another stretch of that wall, never a push target)
                                if not (-1e-9 <= uu <= 1 + 1e-9 and -0.5 < tt <= hT + 2.0 * bead_w):
                                    continue
                                # (and a face that runs ON past the mouth both ways: a host
                                # face ENDING here — an L corner — only grazes the ray)
                                at_ = fpos - (1.0 - uu) * sg.length()
                                if not fcl and min(at_, flen - at_) < 0.4 * cv.length():
                                    continue
                                hits_.append(tt)
                        if hits_:
                            t_face = max(0.0, min(hits_))
                            depth = t_face + (self._push_reach(hsid, hT, bead_w) + 0.5 * bead_w) / k_
                            push = [e + dv * depth, sp + dv * depth]
                            chords[-1]['pushed'] = True
                            chords[-1]['tip'] = e.lerp(sp, 0.5) + dv * depth     # (the Chained Loop junction motif)
                        else:
                            # no host face across this mouth: a CORNER (two walls ending
                            # on each other — e.g. trimmed into an L), not a T. The end
                            # is squared at the chord's far end, filling the corner square
                            # (both bands overlap there: contact), never pushed past the
                            # corner out of the envelope
                            se, ss_ = e.x * dv.x + e.y * dv.y, sp.x * dv.x + sp.y * dv.y
                            far = max(se, ss_)
                            sq = [e + dv * (far - se), sp + dv * (far - ss_)]

                            def _in_env(q):
                                return sum(_point_in_polygon(q, rr) for rr in rings) % 2 == 1
                            if far - min(se, ss_) > 1e-6 and \
                                    all(_in_env(q.lerp(e.lerp(sp, 0.5), 0.05)) for q in sq):
                                push = [q for q, s0 in zip(sq, (se, ss_)) if far - s0 > 1e-6]
                                chords[-1]['pushed'] = 'corner'
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

    def _parallel_spec(self, sid):
        """(thickness, N lanes) of a PARALLEL WALLS source (straight lanes), else None."""
        ws = (getattr(self, '_wall_systems', {}) or {}).get(sid)
        sw = (getattr(self, '_source_walls', {}) or {}).get(sid)
        if not ws or ws.get('type') != 'parallel' or not sw:
            return None
        return float(sw.get('thickness') or 0.0), int(ws.get('walls') or ws.get('paths') or 2)

    def _push_reach(self, hsid, hT, bead):
        """How far past the host's face an incoming generated wall's outer cap
        rail lies. A PARALLEL host has straight lanes: just past its near lane
        (half way to the next) is real contact — the lanes cross that one host
        lane, nothing piles up on the inner lanes. Other hosts (waves / chain
        strands cross the centre line, not the near lane): about the centre line."""
        ps = self._parallel_spec(hsid)
        if ps is not None and ps[1] >= 2 and ps[0] > bead:
            gap = (ps[0] - bead) / (ps[1] - 1)
            return min(0.5 * hT + 0.25 * bead, 0.5 * bead + 0.5 * gap)
        return 0.5 * hT + 0.25 * bead

    def _x_yield(self, sid, other, near):
        """At an X crossing (both centre lines continue through) of two
        PARALLEL WALLS walls near `near`: does `sid` YIELD — print as two
        branches shallowly pushed into `other` — instead of both walls running
        every lane through every lane of the other (a dense knot)? Deterministic:
        the thicker wall, then the one with more lanes, then the earlier path
        passes through. Only with an even lane count (each half closes: no new
        runs); anything else keeps the pass-through crossing."""
        from wall_systems import _cross as WS_cross
        a, b = self._parallel_spec(sid), self._parallel_spec(other)
        if a is None or b is None or a[1] % 2 or a[1] < 2:
            return False
        order = getattr(self, '_src_order', {}) or {}
        if (a[0], a[1], -order.get(sid, 0)) >= (b[0], b[1], -order.get(other, 0)):
            return False
        cen = getattr(self, '_src_center', {}) or {}
        reach = 0.5 * max(a[0], b[0])
        for pa, ca in cen.get(sid, []):
            for pb, cb in cen.get(other, []):
                ra, rb = pa + ([pa[0]] if ca else []), pb + ([pb[0]] if cb else [])
                la = sum(x.dist(y) for x, y in zip(ra, ra[1:]))
                lb = sum(x.dist(y) for x, y in zip(rb, rb[1:]))
                sa = 0.0
                for x1, x2 in zip(ra, ra[1:]):
                    sb = 0.0
                    for y1, y2 in zip(rb, rb[1:]):
                        t = WS_cross(x1, x2, y1, y2)
                        u = WS_cross(y1, y2, x1, x2)
                        if t is not None and u is not None and 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
                            q = x1.lerp(x2, t)
                            at, bt = sa + t * x1.dist(x2), sb + u * y1.dist(y2)
                            # both walls continue straight PAST each other (four arms:
                            # an X — not a T, an L, or a corner hidden in the other band)
                            need = reach + self.material.bead_width
                            if q.dist(near) < 2.0 * reach + self.material.bead_width and \
                                    _x_arms(ra, ca, la, at, need, y1, y2) and _x_arms(rb, cb, lb, bt, need, x1, x2):
                                return True
                        sb += y1.dist(y2)
                    sa += x1.dist(x2)
        return False

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

    def _virtual_faces(self) -> list:
        """[(face point at s0, face point at s1, unit normal at s0, unit
        normal at s1, alpha)] of the cut_transitions: points of the processed
        source centre line where a cut of that width would put its faces."""
        out = []
        for t in self.cut_transitions or []:
            src = next((p for p in self.source_paths if p.id == t.get('source_path_id')), None)
            if src is None:
                continue
            pts = _processed_source_pts(src, self._corner_r(src))
            ring = list(pts) + ([pts[0]] if src.closed else [])
            if len(ring) < 2:
                continue
            cum = [0.0]
            for a, b in zip(ring, ring[1:]):
                cum.append(cum[-1] + a.dist(b))
            L = cum[-1]

            def at(sv):
                sv = sv % L if src.closed else max(0.0, min(L, sv))
                k = max(0, min(len(ring) - 2, next((i for i in range(len(cum) - 1) if cum[i + 1] >= sv), len(cum) - 2)))
                a, b = ring[k], ring[k + 1]
                seg = max(1e-12, cum[k + 1] - cum[k])
                d = Vec2((b.x - a.x) / seg, (b.y - a.y) / seg)
                return a.lerp(b, (sv - cum[k]) / seg), Vec2(-d.y, d.x)
            c, w = float(t.get('center_s', 0.0)), float(t.get('width', 0.0))
            (pa, na), (pb, nb) = at(c - w / 2), at(c + w / 2)
            out.append((pa, pb, na, nb, float(t.get('alpha', 0.0))))
        return out

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
        """Does source sid print a Skin + Web web (its record's web, or — a
        source in no record — its own unowned wall infill)?"""
        ws = self._system_of(sid)
        if ws is not None:
            return ws.type == 'skin_web' and (ws.web or {}).get('pattern', 'none') != 'none'
        return sid not in (getattr(self, '_wall_systems', {}) or {})

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
        Skin + Web) to the host system's ACTUAL printed geometry (M6 source-wall
        attachment: _attach_to_host / wall_systems._resolve_host); where that
        is not possible (no straight lanes, no contact) fall back to the mouth
        splice (_splice_skins).
        → (polys, joined count, chains re-emitted, junction reports)."""
        R = float(self.junction_radius or 0.0) if self.junction_style == 'round' else 0.0
        bead = self.material.bead_width
        joined, infos, fallback = [], [], []
        self._excursions_out = []              # (excursion polyline, [p1, p2], report) — M7
        lanes = getattr(self, '_return_lanes', set())
        rest, oab = [], []
        for sk in part['skins']:
            ch = sk['chain']
            if len(ch) < 3:
                rest.append((sk, None, None))
                continue
            mid = ch[len(ch) // 2]
            d1 = self._junction_dir(sk['sids'][0], ch[0], mid)
            d2 = self._junction_dir(sk['sids'][1], ch[-1], mid)
            if d1 is not None and d2 is not None and sk['sids'][0] == sk['sids'][1] and sk['sids'][0] in lanes:
                oab.append((sk, d1, d2))
            else:
                rest.append((sk, d1, d2))
        if oab:
            # M7 — each OUT-AND-BACK arm at the host (a branch ending there: one;
            # a wall running THROUGH it: one per side) is a continuous EXCURSION
            # of ONE host strand (the one its lanes reach first on THIS layer),
            # fenced so incidental crossings never connect. All arms are resolved
            # together on the unspliced geometry (wall_systems.splice_excursions).
            # The pass-10 crossing loop (extend_through) is NOT applied to them:
            # an arm with no host strand is reported and keeps its own beads.
            polys, res = WS.splice_excursions(polys, [(sk['chain'], d1, d2) for sk, d1, d2 in oab],
                                              R, bead, 2.0 * envelope + 2.0 * bead)
            for (sk, _, _), info in zip(oab, res):
                if info.get('excursion') is None:
                    infos.append({'excursion_from': sk['sids'][0], 'status': 'unresolved',
                                  'reason': info['reason'] + ' — not joined (no crossing loop)'})
                    continue
                exc = info['excursion']
                joined.append(sk['chain'])
                rep_ = {'excursion_from': sk['sids'][0], 'host_strand': info['host_strand'],
                        'contacts': [list(c) for c in info['contacts']], 'depth': info['depth']}
                self._excursions_out.append((exc, [exc[0], exc[-1]], rep_))
                infos.append(rep_)
        for sk, d1, d2 in rest:
            ch = sk['chain']
            if len(ch) < 3:
                fallback.append(sk['ends'])
                continue
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
        """Offsets generated by the walls' envelopes: each source's Wall
        System build record (walls.compose — thickness, alignment, print
        reference); a wall in no record, or a `single` one, is a thin wall."""
        out, ref_only = [], set()
        self._return_lanes = set()
        self._print_thickness = {}            # source id → skin-centreline band (inset skin walls)
        self._arch_faces = {}                 # source id → its ENVELOPE faces (inset skin walls)
        self._ref_outside = set()             # inset aligned walls: reference outside the printed band
        self._wall_systems = {}               # source id → its (non Skin + Web) wall system
        self._wall_system_key = {}            # source id → grouping key (layer system id)
        self._wall_system_of = {}             # source id → {'id', 'name', 'type', 'filled'}
        mat = self.material
        extra = {ot.source_path_id for ot in self.offset_treatments}
        self._source_walls = {}               # source id → its effective wall (report / migration)
        import wall_systems as WS
        for p in sources:
            lsys = self._system_of(p.id)
            spec, src = None, None
            if lsys is not None:
                src = 'system'
                if lsys.type != 'single':         # (SINGLE / OUT-AND-BACK: one bead, no envelope —
                    spec = lsys.envelope()        # Physical rules → the outbound + return lanes below)
                    if spec is not None and getattr(p, 'align_override', None):
                        spec = WallSpec(spec.thickness, p.align_override, spec.print_reference)
            ws = None
            if spec is not None and spec.thickness > 1e-9:
                ws = WS.normalize(lsys.spec())
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
            # PHYSICAL WALL WIDTH (2026-10-16): Wall Thickness is the FINISHED
            # deposited width. A SKIN construction prints its faces, so in a
            # print build they are the bead centrelines half a bead inside the
            # envelope; the ARCHITECTURAL envelope (envelope_only: Designer /
            # Assembly) keeps the thickness itself. Generated constructions
            # already keep their paths half a bead inside the faces.
            inset = 0.0
            if spec is not None and p.id not in self._return_lanes and mat.physical and \
                    not self.envelope_only and lsys is not None and lsys.type in SKIN_SYSTEMS:
                inset = 0.5 * mat.bead_width
                self._print_thickness[p.id] = max(spec.thickness - mat.bead_width, INSET_MIN_SPAN)
            if spec is not None:
                offs = spec.offsets(p.id, p.closed, processed[p.id], inset)
                out.extend(offs)
                if offs and (not spec.reference_printed(p.closed) or
                             (inset > 0.0 and spec.inset_reference(p.closed))):
                    ref_only.add(p.id)
                if offs and inset > 0.0:
                    # the finished envelope's faces (attachment detection only)
                    self._arch_faces[p.id] = spec.offsets(p.id, p.closed, processed[p.id])
                    if spec.inset_reference(p.closed):
                        self._ref_outside.add(p.id)
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

    def resolved_trims(self) -> dict:
        """{trim id: status info (+ a, b, pa, pb of the section it removes)}
        — the layer's trims resolved on its AUTHORED geometry (the same steps
        1–1c as _build_effective: relations, insets, processed sources, end
        snaps, sections)."""
        import network as N
        import trim as TR
        by_id = {p.id: p for p in self.source_paths}
        for rel in self.wall_relations:
            rel.apply(by_id)
        self._resolve_insets()
        processed, order = {}, []
        for p in self.source_paths:
            if not p.visible or (isinstance(p, InsetPath) and len(p.points) < 3):
                continue
            processed[p.id] = _processed_source_pts(p, self._corner_r(p))
            order.append(p)
        self._snap_source_ends(order, processed, N.SNAP_TOL)
        return TR.resolve(self.trims, TR.sections(order, processed), set(processed), processed,
                          {p.id: bool(p.closed) for p in order})[1]

    def _attach_branches(self, sources, processed, wall_offs, trim_removed) -> dict:
        """M6 — SOURCE-WALL ATTACHMENT (WALL_ARCHITECTURE.md §4, symptom E).

        ARCHITECTURE: a branch END lying on another wall's SOURCE line, where
        that wall passes through (not at its own open end, not on a trimmed-
        away stretch), is attached to that WALL: (branch, end, host wall(s),
        station on the host, side the branch comes from) — the persistent
        junction's member roles (`start` / `end` × `through`), never an
        offset-face id.

        PHYSICS, derived here: the branch's printed end is the host's
        ENVELOPE CENTRE on that station — the middle of the host's band,
        between its two physical faces (its offset lines, and its source line
        when the wall is aligned Inside / Outside). The wall-network stage
        then meets the branch with the face on ITS side and never touches
        the far face. For a Centered host the band centre IS the source line
        (nothing moves: identical geometry); for an Inside / Outside host the
        source line is a physical face — before M6 a branch drawn to the wall
        from the other side ran through the material and split that far face.
        At a junction of several hosts the end goes to where their band
        centres meet.

        Mutates `processed` (only moved branch ends). Returns {branch id:
        arc shift of its start} for branches whose START moved; records
        self._attachments (reported in network['attachments'])."""
        self._attachments = []
        shifts: dict = {}
        by_id = {p.id: p for p in sources}
        faces_of: dict = {}
        for ot in wall_offs:
            faces_of.setdefault(ot.source_path_id, []).append(ot)
        arch = getattr(self, '_arch_faces', {}) or {}
        arch_cache: dict = {}
        import network as N
        tol = 1e-4

        def arch_pts(h):
            """an inset skin host's FINISHED face lines (PHYSICAL WALL WIDTH)"""
            if h.id not in arch_cache:
                arch_cache[h.id] = [ot.generate(processed[h.id], h.closed).sample_points()
                                    for ot in arch.get(h.id, ())]
            return arch_cache[h.id]

        def nearest(pt, pts, closed):
            cum = _cum_lengths(pts, closed)
            s_, d_, q = _project_to_polyline(pt, pts, cum, closed)
            return s_, d_, q, cum

        def seg_tangent(pts, i):
            a_, b_ = pts[i % len(pts)], pts[(i + 1) % len(pts)]
            n_ = math.hypot(b_.x - a_.x, b_.y - a_.y) or 1.0
            return Vec2((b_.x - a_.x) / n_, (b_.y - a_.y) / n_)

        def removed(h, s_, L):
            for t in trim_removed.get(h, []):
                a, b = t[0], t[1]
                if a + 1e-6 < s_ < b - 1e-6 or (by_id[h].closed and a + 1e-6 < s_ + L < b - 1e-6):
                    return True                   # (strictly inside: a trim BOUNDARY is a junction, kept)
            return False

        def tangent_at(h, s_, cum):
            """the host's direction at the station — at a vertex, the segment on
            the KEPT side (a trimmed-away neighbour is no wall)"""
            pts, closed = processed[h], by_id[h].closed
            n = len(pts)
            idx = [k for k in range(n if closed else n - 1)
                   if cum[k] - 1e-6 <= s_ <= cum[k + 1] + 1e-6]
            best = None
            for k in idx:
                mid = (cum[k] + cum[k + 1]) / 2.0
                if not removed(h, mid, cum[-1]) and best is None:
                    best = k
            return seg_tangent(pts, best if best is not None else (idx[0] if idx else 0))

        for p in sources:
            if p.closed or len(processed.get(p.id, ())) < 2:
                continue
            for end in ('start', 'end'):
                pts = processed[p.id]
                E = pts[0] if end == 'start' else pts[-1]
                inner = pts[1] if end == 'start' else pts[-2]
                dx, dy = E.x - inner.x, E.y - inner.y
                seg = math.hypot(dx, dy)
                if seg <= 1e-9:
                    continue
                dx, dy = dx / seg, dy / seg           # the branch's own direction, toward its end
                banded = []
                for h in sources:
                    if h.id == p.id or len(processed.get(h.id, ())) < 2:
                        continue
                    s_, d_, q, cum = nearest(E, processed[h.id], h.closed)
                    if d_ > tol and not any(N.dist_to_polyline(E, fp, h.closed) <= tol
                                            for fp in arch_pts(h)):
                        continue                      # (PHYSICAL WALL WIDTH: or on an inset
                        #  skin host's FINISHED face — its printed skin lies half a bead in)
                    L = cum[-1]
                    if not h.closed and (s_ <= tol or s_ >= L - tol):
                        continue                      # (a corner of two ends — not a through-host)
                    if removed(h.id, s_, L):
                        continue                      # (on a trimmed-away stretch: no wall there)
                    ds = [ot.distance for ot in faces_of.get(h.id, [])]
                    if not ds:
                        continue                      # (a single bead: no band)
                    lo_d = min([0.0] + ds) if len(ds) == 1 else min(ds)
                    hi_d = max([0.0] + ds) if len(ds) == 1 else max(ds)
                    oc = (lo_d + hi_d) / 2.0          # band centre: signed offset, left of the host's travel
                    tang = tangent_at(h.id, s_, cum)
                    nl = Vec2(-tang.y, tang.x)
                    e_off = (E.x - q.x) * nl.x + (E.y - q.y) * nl.y if d_ > tol else 0.0
                    banded.append({'host': h.id, 's': s_, 'q': q, 'n': nl, 'oc': oc, 'lo': lo_d, 'hi': hi_d,
                                   'e_off': e_off})
                for x in banded:                      # the side the branch comes from, and its NEAR face
                    v = (inner.x - x['q'].x) * x['n'].x + (inner.y - x['q'].y) * x['n'].y
                    x['near'] = x['hi'] if v > 0 else x['lo']
                    # where the end goes: the band centre (M6) — but an end drawn to an
                    # INSET skin host's FINISHED face on its own side (PHYSICAL WALL
                    # WIDTH: that face is no longer a printed bead) goes to the near
                    # printed skin, half a bead in: it meets that face bead exactly as
                    # it met the face before (same junction faces, same joins)
                    x['tgt'], x['edge'] = x['oc'], False
                    ad = [ot.distance for ot in arch.get(x['host'], ())]
                    if ad:
                        ad = ad + [0.0] if len(ad) == 1 else ad
                        a_near = max(ad) if v > 0 else min(ad)
                        if abs(x['e_off'] - a_near) <= tol:
                            x['tgt'], x['edge'] = x['near'], True
                if not banded or all((abs(x['oc']) <= 1e-9 or abs(x['near']) <= 1e-9) and
                                     abs(x['e_off']) <= 1e-9 for x in banded):
                    # Centered (the source line is the band centre) or the source line IS
                    # the near face (Inside / Outside, branch on that side): the authored
                    # end already meets the face on its own side — nothing to derive
                    continue
                def inside_band(P, x):
                    half = (x['hi'] - x['lo']) / 2.0
                    off = (P.x - x['q'].x) * x['n'].x + (P.y - x['q'].y) * x['n'].y
                    m_ = -1e-6 if x['edge'] else 0.2 * half      # (on the near skin: the band's edge)
                    return half > 1e-6 and x['lo'] + m_ <= off <= x['hi'] - m_
                if len(banded) >= 2:
                    # a JUNCTION of several walls: where their band centres meet (the
                    # junction's material centre — for Centered walls the junction point)
                    x1, x2 = banded[0], banded[1]
                    C1 = Vec2(x1['q'].x + x1['n'].x * x1['tgt'], x1['q'].y + x1['n'].y * x1['tgt'])
                    C2 = Vec2(x2['q'].x + x2['n'].x * x2['tgt'], x2['q'].y + x2['n'].y * x2['tgt'])
                    t1, t2 = Vec2(x1['n'].y, -x1['n'].x), Vec2(x2['n'].y, -x2['n'].x)
                    den = t1.x * t2.y - t1.y * t2.x
                    if abs(den) < 1e-6:
                        En = C1 if C1.dist(C2) <= 1e-6 or C1.dist(E) <= C2.dist(E) else C2
                    else:
                        u = ((C2.x - C1.x) * t2.y - (C2.y - C1.y) * t2.x) / den
                        En = Vec2(C1.x + u * t1.x, C1.y + u * t1.y)
                    b_ = x1
                    ok = all(inside_band(En, x) for x in banded[:2]) and En.dist(E) < seg - 1e-6
                    t = (En.x - E.x) * dx + (En.y - E.y) * dy
                else:
                    # one host: along the branch's own direction to its band centre
                    b_ = banded[0]
                    dn = dx * b_['n'].x + dy * b_['n'].y
                    ok = abs(dn) >= 0.25              # (grazing: the branch runs along the host — kept)
                    if ok:
                        t = (b_['tgt'] - b_['e_off']) / dn
                        En = Vec2(E.x + t * dx, E.y + t * dy)
                        ok = inside_band(En, b_) and abs(t) < seg - 1e-6
                if not ok:
                    self._attachments.append({'branch': p.id, 'end': end, 'hosts': [x['host'] for x in banded],
                                              'status': 'kept', 'reason': 'too oblique / too short to place inside the host'})
                    continue
                new = list(pts)
                if end == 'start':
                    new[0] = En
                    shifts[p.id] = shifts.get(p.id, 0.0) + t   # (the start moved t outward: positions +t)
                else:
                    new[-1] = En
                processed[p.id] = new
                side = {}
                for x in banded:
                    v = (inner.x - x['q'].x) * x['n'].x + (inner.y - x['q'].y) * x['n'].y
                    side[x['host']] = 'left' if v > 0 else 'right'
                self._attachments.append({
                    'branch': p.id, 'end': end, 'hosts': [x['host'] for x in banded], 'via': b_['host'],
                    'station': {x['host']: round(x['s'], 6) for x in banded}, 'side': side,
                    'at': [round(E.x, 6), round(E.y, 6)], 'physical_end': [round(En.x, 6), round(En.y, 6)],
                    'status': 'attached'})
        return shifts

    def _inset_piece(self, pc):
        """PHYSICAL WALL WIDTH — a network piece of an INSET skin wall (its
        printed faces half a bead inside the finished envelope). An aligned
        wall's reference (a finished face) lies outside that band: it leaves
        the piece's wall list (walls[0] / walls[-1] stay its two printed
        faces) but stays the piece's reference line (`ref`: its ends and
        direction find junctions exactly as before). The finished envelope's
        face lines (`arch`) still identify a branch drawn to the wall's face
        as attached (network.find_attachments)."""
        faces = getattr(self, '_arch_faces', {}).get(pc.sys_id)
        if not faces:
            return pc
        ref = next(w for w in pc.walls if abs(w[0]) < 1e-12)
        closed = pc.closed
        pc.arch = [(ot.distance, ot.generate(ref[1], closed).sample_points()) for ot in faces]
        if pc.sys_id in getattr(self, '_ref_outside', ()):
            pc.walls = [w for w in pc.walls if w is not ref]
            pc.ref = ref[1]
            pc.arch.append((0.0, ref[1]))
        return pc

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
                        plans, offsets_by_source, processed):
        """
        Find wall networks (wall systems whose beads touch or cross) and
        replace their beads by the resolved network geometry. Elements in
        no network are returned untouched.
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

        uf = N.UnionFind()
        foreign = []
        for a, b in N.find_contacts(beads_all):
            uf.union(a, b)
            foreign.append(a)
        # PHYSICAL WALL WIDTH: an INSET skin wall prints its faces half a bead
        # inside its envelope, so a wall ending ON it (its reference / finished
        # face — where its beads used to touch) may no longer touch a bead. The
        # architectural attachment (the same test the joins use) still makes
        # them one network; only walls involving an inset skin wall
        inset = getattr(self, '_arch_faces', None) or {}
        if inset:
            live = {b.element for b in beads_all}
            for key, att in N.find_attachments(net_pieces).items():
                pc = next(q for q in net_pieces if id(q) == key[0])
                others = [att[1]] if att[0] == 'T' else [m for m, _ in att[2]]
                for q in others:
                    a, b = f'S:{pc.sys_id}', f'S:{q.sys_id}'
                    if (pc.sys_id in inset or q.sys_id in inset) and a in live and b in live \
                            and uf.find(a) != uf.find(b):
                        uf.union(a, b)
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
            summary['components'].append({'sources': sorted(sys_ids)})
            corner_at = {(c.pt.x, c.pt.y): c for c in corners}
            prod = self.__dict__.setdefault('_production_junctions', [])
            for k, p in enumerate(res.junctions):
                c = corner_at.pop((p.x, p.y), None)
                summary['junctions'].append(self._junction_info(p, c))
                prod.append({'x': p.x, 'y': p.y, 'corner': c.key if c else None,
                             'walls': list(res.junction_walls[k]) if k < len(res.junction_walls) else []})
            for c in corner_at.values():
                summary['junctions'].append(self._junction_info(c.pt, c))
                prod.append({'x': c.pt.x, 'y': c.pt.y, 'corner': c.key,
                             'walls': sorted({rb.bead.element for rb, _, _ in c.ends})})
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
        _snap_strand_ends(strands, RVec2)
        return RoutingLayer(z_height=0.0, strands=strands, label=self.label)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'label': self.label,
            'source_paths': [p.to_dict() for p in self.source_paths],
            'offset_treatments': [ot.to_dict() for ot in self.offset_treatments],
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


def _join_cut_ends(pieces, ref, rings, bead) -> tuple:
    """LOCAL CUT JOIN (PrintLayer.lattice_join): the reference strands that
    an end wall of this design cuts (piece ends that are not ends of the
    reference polylines) are paired with their NEIGHBOUR ALONG THE BOUNDARY
    RING (the end wall between them — flat or rounded: an arc of many short
    segments) and each pair's tails are bent smoothly to meet at one point
    on it: the ring vertex nearest the middle of the stretch between them (an
    even node: the lineage-jamb meeting, done locally). Only the last
    `reach` (≈ the wall thickness, ≥ 2 beads) of each tail moves, so the
    lattice stays the reference everywhere else. Returns (pieces, report
    {'pairs', 'odd'}); ends that cannot be paired (an odd count on a ring, or
    no neighbour within an end-wall length) are left as clipped and counted
    in 'odd' (the caller's closure check decides)."""
    import wall_lattice as _WL
    ends0 = [q for poly in ref for q in (poly[0], poly[-1])]
    thick = _WL._thickness([r for r in rings if len(r) >= 3]) or 1.0

    def original(q):
        return any(q.dist(e) < 1e-6 for e in ends0)
    cums = []
    for r in rings:
        c = [0.0]
        for i in range(len(r)):
            c.append(c[-1] + r[i].dist(r[(i + 1) % len(r)]))
        cums.append(c)

    def locate(q):
        """(ring index, arc position) of a point on a ring, or None."""
        best = None
        for ri, r in enumerate(rings):
            n = len(r)
            for i in range(n):
                a, b = r[i], r[(i + 1) % n]
                d = _seg_dist_pt(q, a, b)
                if best is None or d < best[0]:
                    L = a.dist(b)
                    t = 0.0 if L < 1e-12 else ((q.x - a.x) * (b.x - a.x) + (q.y - a.y) * (b.y - a.y)) / (L * L)
                    best = (d, ri, cums[ri][i] + max(0.0, min(1.0, t)) * L)
        return None if best is None or best[0] > 1e-6 else (best[1], best[2])
    by_ring: dict = {}
    for i, poly in enumerate(pieces):
        for end in (0, -1):
            q = poly[end]
            if original(q):
                continue
            loc = locate(q)
            if loc is not None:
                by_ring.setdefault(loc[0], []).append((loc[1], i, end))
    out = [list(p) for p in pieces]
    rep = {'pairs': 0, 'odd': 0}
    reach = max(thick, 2.0 * bead, 1.0)
    for ri, ends in by_ring.items():
        Lr = cums[ri][-1]
        ends.sort()
        n = len(ends)
        if n % 2:
            rep['odd'] += 1
            continue
        gap = lambda j: (ends[(j + 1) % n][0] - ends[j][0]) % Lr     # ring distance end j → j+1
        # pair neighbours: the alternative (offset 0 or 1) with the shorter total gap
        offs = min((0, 1), key=lambda o: sum(gap(j) for j in range(o, o + n, 2)))
        for j in range(offs, offs + n, 2):
            e1, e2 = ends[j % n], ends[(j + 1) % n]
            g = gap(j % n)
            if g > 2.5 * thick:
                rep['odd'] += 1
                continue
            mid = (e1[0] + 0.5 * g) % Lr
            r = rings[ri]
            # the ring vertex nearest the middle of the stretch, within it
            cand = [k for k in range(len(r)) if ((cums[ri][k] - e1[0]) % Lr) <= g + 1e-9]
            if cand:
                k = min(cand, key=lambda k: min(abs(cums[ri][k] - mid), Lr - abs(cums[ri][k] - mid)))
                m = r[k]
            else:
                q1, q2 = pieces[e1[1]][e1[2]], pieces[e2[1]][e2[2]]
                m = Vec2((q1.x + q2.x) / 2, (q1.y + q2.y) / 2)
            for (_, i, end) in (e1, e2):
                q = pieces[i][end]
                out[i] = _bend_tail(out[i], end, Vec2(m.x - q.x, m.y - q.y), reach, m)
            rep['pairs'] += 1
    return out, rep


def _bend_virtual(pieces, faces, rings, bead) -> list:
    """CROSS-Z TRANSITION of the local cut join: the strands that WOULD be
    cut at a virtual opening's faces (the face line across the wall at each
    face point) are paired along that line as _join_cut_ends pairs them,
    and moved toward the pair's meeting point by alpha — around the crossing,
    fading over the same reach — without cutting anything."""
    import wall_lattice as _WL
    thick = _WL._thickness([r for r in rings if len(r) >= 3]) or 1.0
    reach = max(thick, 2.0 * bead, 1.0)
    out = [list(p) for p in pieces]
    for (pa, pb, na, nb, alpha) in faces:
        if alpha <= 0:
            continue
        for P, n in ((pa, na), (pb, nb)):
            A = Vec2(P.x - n.x * thick, P.y - n.y * thick)
            B = Vec2(P.x + n.x * thick, P.y + n.y * thick)
            hits = []                                   # (position along the face line, piece, segment, t)
            for i, poly in enumerate(out):
                for j in range(len(poly) - 1):
                    h_ = _seg_intersect_param(poly[j], poly[j + 1], A, B)
                    if h_ is not None:
                        hits.append((h_[1], i, j, h_[0]))
            hits.sort()
            if len(hits) % 2:
                continue
            bumps = []
            for e1, e2 in zip(hits[::2], hits[1::2]):
                q1 = out[e1[1]][e1[2]].lerp(out[e1[1]][e1[2] + 1], e1[3])
                q2 = out[e2[1]][e2[2]].lerp(out[e2[1]][e2[2] + 1], e2[3])
                m = Vec2((q1.x + q2.x) / 2, (q1.y + q2.y) / 2)
                for (_, i, j, t), q in ((e1, q1), (e2, q2)):
                    bumps.append((i, j, t, Vec2(alpha * (m.x - q.x), alpha * (m.y - q.y))))
            # (applied from the highest segment index down: an applied bump
            # inserts points, the lower indices stay valid)
            for i, j, t, d in sorted(bumps, key=lambda b_: (b_[0], b_[1], b_[2]), reverse=True):
                out[i] = _bump(out[i], j, t, d, reach)
    return out


def _bump(poly, j, t, delta, reach) -> list:
    """The polyline with the point at segment j, parameter t moved by delta,
    the move fading smoothly to nothing `reach` away along it, both ways
    (the point is inserted; only the moved stretch is resampled ≤ 0.5 in)."""
    if math.hypot(delta.x, delta.y) < 1e-9:
        return poly
    c = poly[j].lerp(poly[j + 1], t)
    pts = list(poly[:j + 1]) + [c] + list(poly[j + 1:])
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + a.dist(b))
    sc = cum[j + 1]
    dense, s_ = [pts[0]], [0.0]
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        L = cum[k + 1] - cum[k]
        if cum[k + 1] < sc - reach or cum[k] > sc + reach:
            n = 1
        else:
            n = max(1, int(math.ceil(L / 0.5)))
        for u in range(1, n + 1):
            dense.append(a.lerp(b, u / n))
            s_.append(cum[k] + L * u / n)
    res = []
    for q, sv in zip(dense, s_):
        u = max(0.0, 1.0 - abs(sv - sc) / reach)
        w = u * u * (3 - 2 * u)
        res.append(Vec2(q.x + w * delta.x, q.y + w * delta.y) if w > 0 else q)
    return res


def _bend_tail(poly, end, delta, reach, target=None) -> list:
    """The polyline with its `end` (0 / -1) moved by delta, the move fading
    smoothly (smoothstep) to nothing `reach` back along it; the tail is
    resampled (≤ 0.5 in) so straight stitches bend smoothly."""
    if math.hypot(delta.x, delta.y) < 1e-9:
        return poly
    P = list(poly) if end == -1 else list(reversed(poly))
    # never past half the piece: its other end (a reference end, a seam,
    # another cut end) stays exactly where it is
    reach = min(reach, 0.5 * sum(P[j].dist(P[j + 1]) for j in range(len(P) - 1)))
    if reach <= 1e-9:
        return poly
    # densify the last `reach` of arc length
    acc, dense = 0.0, [P[-1]]
    for j in range(len(P) - 1, 0, -1):
        a, b = P[j], P[j - 1]
        L = a.dist(b)
        if acc >= reach:
            dense.append(b)
            continue
        n = max(1, int(math.ceil(L / 0.5)))
        for t in range(1, n + 1):
            dense.append(a.lerp(b, t / n))
        acc += L
    dense.reverse()                       # back in forward order, end last
    s, out = 0.0, [None] * len(dense)
    for j in range(len(dense) - 1, -1, -1):
        if j < len(dense) - 1:
            s += dense[j].dist(dense[j + 1])
        u = max(0.0, 1.0 - s / reach)
        w = u * u * (3 - 2 * u)
        out[j] = Vec2(dense[j].x + w * delta.x, dense[j].y + w * delta.y)
    out[-1] = Vec2(P[-1].x + delta.x, P[-1].y + delta.y) if target is None else target
    return out if end == -1 else list(reversed(out))


def _seg_dist_pt(q, a, b) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q.x - a.x) * dx + (q.y - a.y) * dy) / L2))
    return math.hypot(q.x - a.x - t * dx, q.y - a.y - t * dy)


# Strand END POINTS that coincide within this tolerance are made identical:
# the router keys graph nodes by exact coordinates, so float noise (a ring
# closing at 2e-5 in from its start after a cut, two pieces meeting at a
# rounded point) made a closed loop an "open" path whose two ends were paired
# by a 0.00002 in TRAVEL — an extrusion break for nothing (2026-10-09).
END_SNAP = 1e-3


def _snap_strand_ends(strands, V):
    reps = {}                                  # grid cell → representative points

    def snap(p):
        cx, cy = int(p.x // END_SNAP), int(p.y // END_SNAP)
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for q in reps.get((i, j), ()):
                    if abs(q.x - p.x) <= END_SNAP and abs(q.y - p.y) <= END_SNAP:
                        return q
        reps.setdefault((cx, cy), []).append(p)
        return p
    for st in strands:
        if st.closed or len(st.points) < 2:
            continue
        a = snap(st.points[0])
        b = snap(st.points[-1])
        if a is not st.points[0] or b is not st.points[-1]:
            st.points = [a] + list(st.points[1:-1]) + [b]
        if st.points[0] != st.points[-1] and st.points[0].dist(st.points[-1]) <= END_SNAP and len(st.points) > 2:
            st.points[-1] = st.points[0]             # (its own ends: a closed loop)


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
