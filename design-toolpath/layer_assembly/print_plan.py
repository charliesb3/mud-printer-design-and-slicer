"""PRINT PLAN — the ONE authoritative ordered representation of a whole print
(2026-10-09): every resolved Assembly layer, bottom to top, as ordered
continuous-extrusion RUNS and the non-printing TRAVELS between them, in WORLD
coordinates (each layer's own InstanceTransform applied), with source
identities kept (layer, section, design, geometry variant, strand, source
path, Wall System). Consumers: Assembly toolpath inspection, the (future)
Print Simulation, (future) G-code export / machine-controller execution — none of
them re-derives order or timing.

WHAT IS ROUTED is exactly what the Assembly displays and checks for support:
each layer's resolved geometry (`source.layer_geometry`: its design, or its
transform-group / profile-cut VARIANT), routed by the DESIGNER's own layer
router (`source.route` → design_proto/layer_route.py: the same strands,
route origins, junction rules and no-retrace closure as the Designer view).
The planner never regenerates or edits geometry — it only orders it.

LIFT-AWARE SCHEDULE (lifts.py, 2026-10-09): Building → Lift → Stage (island /
  connected stage) → Pass (one global layer's piece) → Run. The layers are
  divided into LIFTS of whole layers (≤ the preferred lift height ≤ the
  maximum Z travel); a lift is completed before the XY frame is raised (an
  explicit FrameMove) and never returned to. Inside a lift each layer's
  PHYSICAL pieces (beads that touch) are ordered by a deterministic greedy
  over the "lower and nearby first" DAG: an island is built upward as far as
  its geometry allows; a merged piece waits for every piece below it. A
  global layer may therefore be printed in several passes at different
  times; the nozzle may go back DOWN to a lower layer of another island in
  the same lift (a transition with dz < 0, at a clear height), never to an
  earlier lift.

AUTOMATIC ROUTING (practical, deterministic heuristic): each pass is routed
  by the Designer's router from where the nozzle is (mapped into the
  layer's DESIGN coordinates): component order = nearest neighbour + 2-opt
  from there, each closed component's seam = least travel + exposure
  (concealed seams preferred), each open component starts at its better
  end. So a closed piece starts right above where the piece below ended.
  A layer that is ONE piece is routed exactly as before (whole layer).
MANUAL CHOICES WIN where they apply: Route Origins (a closed component's
  seam) and the reverse flag on every pass of the design; the design's
  start point on the piece nearest to it (and that piece is preferred
  among equally valid next pieces); the design's component order inside a
  single-piece layer. The lift / island schedule itself is never overridden
  (it is what keeps the print physically valid). Starts are only ever
  graph nodes of the routed geometry (never a cut of a continuous path, no
  retrace added).

TIMING (`PrintSpeeds`, `estimate`, `timeline`): ESTIMATES from the ordered
path lengths, two configurable speeds (printing / extrusion, non-printing
travel) and an optional estimated time per frame reposition — reported
separately. Not machine timing: no acceleration, cornering, pump response,
Z motion (manual today) or pauses. The timeline (start / end time of every
move) is what the Print Simulation plays.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

PLAN_VERSION = 1

# ---------------------------------------------------------------------------
# speeds and units
# ---------------------------------------------------------------------------

# Placeholders until measured on the machine (editable per project):
DEFAULT_PRINT_SPEED = 2.0       # in/s while extruding
DEFAULT_TRAVEL_SPEED = 6.0      # in/s while not extruding

# unit → (to in/s, from in/s). 's/in' = TIME PER INCH (a pace).
SPEED_UNITS = {
    'in/s': (lambda v: v, lambda v: v),
    'in/min': (lambda v: v / 60.0, lambda v: v * 60.0),
    'ft/min': (lambda v: v * 12.0 / 60.0, lambda v: v * 60.0 / 12.0),
    's/in': (lambda v: 1.0 / v, lambda v: 1.0 / v),
    'mm/s': (lambda v: v / 25.4, lambda v: v * 25.4),
}


class PlanError(ValueError):
    pass


class PlanCancelled(Exception):
    """A plan abandoned while it was being built (superseded by a newer
    request or cancelled by the page): nothing is returned or cached."""


def speed_from(value, unit='in/s') -> float:
    """A speed entered in `unit` → in/s (> 0)."""
    if unit not in SPEED_UNITS:
        raise PlanError(f'unknown speed unit {unit!r}')
    v = float(value)
    if not v > 0 or not math.isfinite(v):
        raise PlanError('a speed (or time per inch) must be greater than 0')
    return SPEED_UNITS[unit][0](v)


def speed_to(v_in_s, unit='in/s') -> float:
    return SPEED_UNITS[unit][1](float(v_in_s))


@dataclass(frozen=True)
class PrintSpeeds:
    """The timing model's settings: print / travel speeds (in/s) and the
    optional ESTIMATED time of one frame reposition (s; 0 = not estimated —
    the frame is not assumed to be motorized)."""
    print_speed: float = DEFAULT_PRINT_SPEED      # in/s
    travel_speed: float = DEFAULT_TRAVEL_SPEED    # in/s
    frame_time: float = 0.0                       # s per frame reposition

    def __post_init__(self):
        for v in (self.print_speed, self.travel_speed):
            if not (isinstance(v, (int, float)) and v > 0 and math.isfinite(v)):
                raise PlanError('print and travel speeds must be greater than 0')
        if not (isinstance(self.frame_time, (int, float)) and self.frame_time >= 0 and math.isfinite(self.frame_time)):
            raise PlanError('the frame reposition time must be ≥ 0')

    @staticmethod
    def from_dict(d) -> 'PrintSpeeds':
        d = d or {}
        return PrintSpeeds(float(d.get('print', DEFAULT_PRINT_SPEED)), float(d.get('travel', DEFAULT_TRAVEL_SPEED)),
                           float(d.get('frame', 0.0) or 0.0))

    def to_dict(self) -> dict:
        return {'print': self.print_speed, 'travel': self.travel_speed, 'frame': self.frame_time}


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

def _len(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


@dataclass
class Segment:
    """Consecutive moves of ONE kind on ONE strand, inside a run (world XY)."""
    kind: str                         # 'print' | 'retrace'
    pts: list                         # [(x, y)…]
    strand: Optional[str] = None
    source: Optional[str] = None      # the Designer source path it belongs to
    system: Optional[str] = None      # its Wall System id (None: none / single bead)

    @property
    def length(self) -> float:
        return _len(self.pts)

    def to_dict(self) -> dict:
        return {'kind': self.kind, 'pts': [[round(x, 5), round(y, 5)] for x, y in self.pts],
                'strand': self.strand, 'source': self.source, 'system': self.system}


@dataclass
class Run:
    """One CONTINUOUS EXTRUSION (no travel inside)."""
    index: int
    segments: list = field(default_factory=list)

    @property
    def start(self):
        return self.segments[0].pts[0]

    @property
    def end(self):
        return self.segments[-1].pts[-1]

    @property
    def closed(self) -> bool:
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1]) < 1e-6

    @property
    def print_length(self) -> float:
        return sum(s.length for s in self.segments if s.kind == 'print')

    @property
    def retrace_length(self) -> float:
        return sum(s.length for s in self.segments if s.kind == 'retrace')

    @property
    def sources(self) -> list:
        return sorted({s.source for s in self.segments if s.source})

    @property
    def systems(self) -> list:
        return sorted({s.system for s in self.segments if s.system})

    def to_dict(self, detail=True) -> dict:
        out = {'index': self.index, 'start': list(self.start), 'end': list(self.end), 'closed': self.closed,
               'print_length': round(self.print_length, 3), 'retrace_length': round(self.retrace_length, 3),
               'sources': self.sources, 'systems': self.systems}
        if detail:
            out['segments'] = [s.to_dict() for s in self.segments]
        return out


@dataclass
class Travel:
    """A non-printing move: between runs of a piece, or the TRANSITION into a
    pass from where the nozzle was (dz = the Z change: + up to the next
    layer, − back DOWN to a lower layer of ANOTHER island in the same lift,
    0 another island of the same layer). `clear_z`: the building Z the XY
    move must happen at — above every piece of the current lift already
    printed beside its path (for export: rise to it, move, descend)."""
    start: tuple
    end: tuple
    dz: float = 0.0
    transition: bool = False
    clear_z: Optional[float] = None

    @property
    def length(self) -> float:          # XY distance
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])

    def to_dict(self) -> dict:
        out = {'start': [round(v, 5) for v in self.start], 'end': [round(v, 5) for v in self.end],
               'length': round(self.length, 3), 'dz': round(self.dz, 5), 'transition': self.transition}
        if self.clear_z is not None:
            out['clear_z'] = round(self.clear_z, 5)
        return out


@dataclass
class Pass:
    """ONE SCHEDULED PIECE: the runs of one physical component (island piece)
    of one global layer, printed in one go. A global layer may be printed in
    several passes at different times (independent islands, lifts.py)."""
    index: int                         # print order (plan.passes)
    layer: int                         # global layer index
    lift: int
    component: int                     # the layer's component index (lifts.layer_components)
    z: float                           # nozzle height (the layer's top)
    stage: int = -1                    # plan.stages index
    steps: list = field(default_factory=list)        # Run | Travel (inside the piece)
    transition_in: Optional[Travel] = None           # from where the nozzle was
    manual_start: bool = False
    reversed: bool = False
    origins: list = field(default_factory=list)

    @property
    def runs(self) -> list:
        return [s for s in self.steps if isinstance(s, Run)]

    @property
    def travels(self) -> list:
        return [s for s in self.steps if isinstance(s, Travel)]

    @property
    def start(self):
        return self.runs[0].start if self.runs else None

    @property
    def end(self):
        return self.runs[-1].end if self.runs else None

    def to_dict(self, detail=False) -> dict:
        out = {'index': self.index, 'layer': self.layer, 'lift': self.lift, 'stage': self.stage,
               'component': self.component, 'z': self.z, 'runs': [r.index for r in self.runs],
               'manual_start': self.manual_start, 'reversed': self.reversed,
               'start': list(self.start) if self.start else None, 'end': list(self.end) if self.end else None,
               'transition_in': self.transition_in.to_dict() if self.transition_in else None}
        if detail:
            out['steps'] = [dict(s.to_dict(), type='run') if isinstance(s, Run) else dict(s.to_dict(), type='travel')
                            for s in self.steps]
        return out


@dataclass
class PlanLayer:
    """A GLOBAL layer (its identity, Z, placement) and the passes printing it."""
    index: int
    section_index: int
    design_id: str
    geometry_key: str
    z_bottom: float
    z_top: float
    placement: dict                   # {'scale', 'tx', 'ty'}: design → world
    lift: int = 0
    components: int = 1
    passes: list = field(default_factory=list)       # its Passes, in print order

    @property
    def steps(self) -> list:
        return [s for p in self.passes for s in p.steps]

    @property
    def runs(self) -> list:
        return [s for s in self.steps if isinstance(s, Run)]

    @property
    def travels(self) -> list:
        return [s for s in self.steps if isinstance(s, Travel)]

    @property
    def transitions(self) -> list:
        return [p.transition_in for p in self.passes if p.transition_in is not None]

    @property
    def transition_in(self) -> Optional[Travel]:     # into its first pass
        return self.passes[0].transition_in if self.passes else None

    @property
    def manual_start(self) -> bool:
        return any(p.manual_start for p in self.passes)

    @property
    def reversed(self) -> bool:
        return any(p.reversed for p in self.passes)

    @property
    def origins(self) -> list:
        out = []
        for p in self.passes:
            out.extend(o for o in p.origins if o not in out)
        return out

    @property
    def start(self):
        return self.runs[0].start if self.runs else None

    @property
    def end(self):
        return self.runs[-1].end if self.runs else None

    @property
    def z(self) -> float:               # nozzle height while printing this layer
        return self.z_top

    def to_dict(self, detail=True) -> dict:
        out = {'index': self.index, 'section_index': self.section_index, 'design_id': self.design_id,
               'geometry_key': self.geometry_key, 'z_bottom': self.z_bottom, 'z_top': self.z_top,
               'placement': dict(self.placement), 'manual_start': self.manual_start, 'reversed': self.reversed,
               'lift': self.lift, 'components': self.components,
               'passes': [{'index': p.index, 'stage': p.stage, 'runs': [r.index for r in p.runs]} for p in self.passes],
               'start': list(self.start) if self.start else None, 'end': list(self.end) if self.end else None,
               'runs': len(self.runs), 'travel_length': round(sum(t.length for t in self.travels), 3),
               'transition_in': self.transition_in.to_dict() if self.transition_in else None,
               'print_length': round(sum(r.print_length for r in self.runs), 3)}
        if detail:
            out['steps'] = [dict(s.to_dict(), type='run') if isinstance(s, Run) else dict(s.to_dict(), type='travel')
                            for s in self.steps]
        return out


@dataclass
class Stage:
    """An island / connected printing stage of a lift: a chain of pieces
    (one per layer) — an independent island ('island'), the piece where
    islands joined and everything above it ('merge'), or a branch after a
    split ('split'). Named A, B, A+B, A.1 … per lift."""
    index: int
    lift: int
    name: str
    kind: str
    parents: list = field(default_factory=list)      # stage indices it continues from
    passes: list = field(default_factory=list)       # pass indices (print order)
    layers: list = field(default_factory=list)       # global layer indices (ascending)

    def to_dict(self, plan=None) -> dict:
        out = {'index': self.index, 'lift': self.lift, 'name': self.name, 'kind': self.kind,
               'parents': list(self.parents), 'passes': list(self.passes),
               'first_layer': self.layers[0] if self.layers else None,
               'last_layer': self.layers[-1] if self.layers else None, 'layers': len(self.layers)}
        if plan is not None and self.layers:
            out['z_bottom'] = plan.layers[self.layers[0]].z_bottom
            out['z_top'] = plan.layers[self.layers[-1]].z_top
        return out


@dataclass
class FrameMove:
    """FRAME REPOSITIONING between lifts: the whole XY frame is raised (a
    major, manual operation — not machine-controlled here). The nozzle
    stays where the last lift ended; afterwards nozzle Z is measured from
    the new frame base. Never followed by printing below `frame_z_to`."""
    from_lift: int
    to_lift: int
    frame_z_from: float               # building Z of the frame base before …
    frame_z_to: float                 # … and after (= the new lift's bottom)
    at: tuple                         # nozzle XY meanwhile
    z: float                          # building Z of the nozzle meanwhile

    @property
    def rise(self) -> float:
        return self.frame_z_to - self.frame_z_from

    def to_dict(self) -> dict:
        return {'from_lift': self.from_lift, 'to_lift': self.to_lift, 'frame_z_from': self.frame_z_from,
                'frame_z_to': self.frame_z_to, 'rise': round(self.rise, 6),
                'at': [round(v, 5) for v in self.at], 'z': self.z}


@dataclass
class Lift:
    """A vertical LIFT: whole layers printable with the frame in one place
    (height ≤ the preferred lift height ≤ the maximum Z travel)."""
    index: int
    first_layer: int
    last_layer: int
    z_bottom: float
    z_top: float
    stages: list = field(default_factory=list)       # stage indices
    passes: list = field(default_factory=list)       # pass indices
    reposition_in: Optional[FrameMove] = None        # the frame move before it (lifts > 0)

    @property
    def frame_z(self) -> float:        # building Z of the frame base (nozzle Z 0)
        return self.z_bottom

    @property
    def height(self) -> float:
        return self.z_top - self.z_bottom

    def to_dict(self) -> dict:
        return {'index': self.index, 'first_layer': self.first_layer, 'last_layer': self.last_layer,
                'layers': self.last_layer - self.first_layer + 1, 'z_bottom': self.z_bottom, 'z_top': self.z_top,
                'height': round(self.height, 6), 'frame_z': self.frame_z, 'stages': list(self.stages),
                'passes': len(self.passes),
                'reposition_in': self.reposition_in.to_dict() if self.reposition_in else None}


@dataclass
class PrintPlan:
    """Building → Lift → Stage (island / connected stage) → Pass (one global
    layer's piece) → Run. `layers`: the GLOBAL layers (identity, Z, their
    passes); `passes`: the PRINT ORDER."""
    layers: list = field(default_factory=list)
    passes: list = field(default_factory=list)
    stages: list = field(default_factory=list)
    lifts: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)     # lifts.LiftSettings.to_dict()
    units: str = 'in'
    version: int = PLAN_VERSION
    warnings: list = field(default_factory=list)

    def moves(self):
        """Every move in print order: (layer index, run index | None, kind,
        start (x, y, z), end (x, y, z), strand, source, system). kind:
        'frame' (a frame reposition: no nozzle motion) | 'transition' |
        'travel' | 'print' | 'retrace'. The flat stream G-code export /
        machine execution / simulation consume."""
        for it in items(self):
            kind, li, ri, pts, z0, z1, st, so, sy = it[:9]
            r = None if ri < 0 else ri
            if kind in ('frame', 'transition'):
                yield (li, None, kind, (*pts[0], z0), (*pts[-1], z1), None, None, None)
                continue
            for a, b in zip(pts, pts[1:]):
                yield (li, r, kind, (*a, z0), (*b, z1), st, so, sy)

    def to_dict(self, detail='summary') -> dict:
        """detail: 'summary' (per layer / run numbers), 'full' (every
        segment), or a list of layer indices given in full."""
        full = detail == 'full'
        sel = set(detail) if isinstance(detail, (list, tuple, set)) else set()
        return {'version': self.version, 'units': self.units, 'warnings': list(self.warnings),
                'settings': dict(self.settings),
                'lifts': [L.to_dict() for L in self.lifts],
                'stages': [s.to_dict(self) for s in self.stages],
                'passes': [p.to_dict() for p in self.passes],
                'layers': [L.to_dict(detail=full or L.index in sel) for L in self.layers]}


# ---------------------------------------------------------------------------
# building the plan
# ---------------------------------------------------------------------------

def _to_world(p, pl):
    return (pl.scale * p[0] + pl.tx, pl.scale * p[1] + pl.ty)


def _to_design(p, pl):
    return ((p[0] - pl.tx) / pl.scale, (p[1] - pl.ty) / pl.scale)


def _fallback_route(geometry, start, members=None):
    """Routing for a LayerSource without its own router (the StaticSource
    test double): plain strands, physical (no retrace)."""
    from .source import toolpath_graph
    graph, geom = toolpath_graph()
    polys = list(enumerate(geometry.polylines))
    if members is not None:
        polys = [polys[i] for i in members]
    strands = [geom.Strand(id=p.get('id') or f's{k}', role='free', points=[geom.Vec2(*q) for q in p['pts']],
                           closed=bool(p.get('closed'))) for k, p in polys if len(p['pts']) >= 2]
    st = geom.Vec2(*start) if start is not None else None
    moves = graph.route_layer(geom.Layer(0.0, strands), start=st, allow_retrace=False)
    return moves, {'manual_start': False, 'reversed': False, 'origins': []}, {}


def _layer_steps(moves, placement, identity, drop_leading_travel, run_base=0):
    """PrintMoves (design coordinates) → (runs + travels in WORLD coordinates,
    the leading travel dropped: it is the transition into the pass). Runs
    are numbered from `run_base` (unique within the global layer)."""
    steps, run, seg = [], None, None
    rk = run_base
    ms = list(moves)
    if drop_leading_travel and ms and ms[0].kind == 'travel':
        ms = ms[1:]
    for m in ms:
        a, b = _to_world((m.start.x, m.start.y), placement), _to_world((m.end.x, m.end.y), placement)
        if m.kind == 'travel':
            run, seg = None, None
            steps.append(Travel(a, b))
            continue
        if run is None:
            run = Run(rk)
            rk += 1
            steps.append(run)
            seg = None
        if seg is None or seg.kind != m.kind or seg.strand != m.strand_id or \
                math.hypot(seg.pts[-1][0] - a[0], seg.pts[-1][1] - a[1]) > 1e-9:
            src, sysd = identity.get(m.strand_id, (None, None))
            seg = Segment(m.kind, [a], m.strand_id, src, (sysd or {}).get('id') if isinstance(sysd, dict) else sysd)
            run.segments.append(seg)
        seg.pts.append(b)
    return [s for s in steps if not (isinstance(s, Travel) and s.length < 1e-9)]


def _clear_z(a, b, z, printed, margin):
    """The Z an XY move a → b at height z must be made at: above every
    already printed piece of the lift (bbox, top z) beside its path."""
    x0, x1 = min(a[0], b[0]) - margin, max(a[0], b[0]) + margin
    y0, y1 = min(a[1], b[1]) - margin, max(a[1], b[1]) + margin
    top = z
    for (bx0, by0, bx1, by1), zt in printed:
        if zt > top and not (bx1 < x0 or x1 < bx0 or by1 < y0 or y1 < by0):
            top = zt
    return top


def _manual_point(g):
    st = ((g.diagnostics or {}).get('routing') or {}).get('start') if hasattr(g, 'diagnostics') else None
    return tuple(st) if st else None


def build_plan(resolved, source, start=None, cancelled=None, lift_settings=None) -> PrintPlan:
    """The whole-print plan of a resolved Assembly (`resolved.instances` in
    print order, profile cut specs attached). `source`: the LayerSource the
    Assembly resolved with (its `route` = the Designer's layer router).
    `start`: the nozzle's position (world XY) before the first layer, if
    known. `cancelled()`: polled before the variants are fetched and before
    every lift / piece — True aborts with PlanCancelled. `lift_settings`:
    lifts.LiftSettings (default 18 in / 18 in).
    LIFT-AWARE (lifts.py): the layers are divided into lifts; each lift is
    complete before the frame is raised (an explicit FrameMove), never
    returned to; inside a lift every layer's physical pieces are scheduled
    (independent islands upward first, merged geometry after all its lower
    parts) and each piece is routed by the Designer's router from where the
    nozzle is. Deterministic: same stack, same plan."""
    from .source import layer_geometry
    from .lifts import LiftSettings, lift_bounds, layer_components, make_piece, lift_graph, schedule, stages
    settings = lift_settings or LiftSettings()
    plan = PrintPlan(settings=settings.to_dict())
    has_router = hasattr(source, 'route')
    insts = list(resolved.instances)

    def check():
        if cancelled is not None and cancelled():
            raise PlanCancelled('print plan cancelled')
    check()
    if hasattr(source, 'prefetch'):
        from .source import variant_request
        source.prefetch([variant_request(i) for i in insts if i.is_variant])
    try:
        bounds = lift_bounds([(i.z_bottom, i.z_top) for i in insts], settings)
    except ValueError as e:
        raise PlanError(str(e))
    comp_cache = {}
    geo = {}
    for inst in insts:
        g = layer_geometry(source, inst)
        pl = inst.transform
        bead = float(getattr(g, 'bead_width', None) or 3.0)
        ck = (inst.geometry_key, round(pl.scale, 9))
        if ck not in comp_cache:
            polys = [p for p in g.polylines]
            comp_cache[ck] = [c for c in layer_components(polys, bead / pl.scale) if c] if polys else []
        geo[inst.index] = (g, pl, bead, comp_cache[ck])
        plan.layers.append(PlanLayer(inst.index, inst.section_index, inst.design_id, inst.geometry_key,
                                     inst.z_bottom, inst.z_top, {'scale': pl.scale, 'tx': pl.tx, 'ty': pl.ty},
                                     components=len(comp_cache[ck])))
    pos = tuple(start) if start is not None else None           # nozzle (world XY)
    prev_z = None
    for li, (a, b) in enumerate(bounds):
        check()
        lift = Lift(li, a, b, insts[a].z_bottom, insts[b].z_top)
        plan.lifts.append(lift)
        if li > 0 and pos is not None:
            lift.reposition_in = FrameMove(li - 1, li, plan.lifts[li - 1].frame_z, lift.frame_z, pos, prev_z)
        pieces, manual = {}, set()
        bead_l = 3.0
        for L in range(a, b + 1):
            g, pl, bead, comps = geo[L]
            bead_l = bead
            plan.layers[L].lift = li
            mp = _manual_point(g) if len(comps) > 1 else None
            best = None
            pieces[L] = []
            for c, members in enumerate(comps):
                rings = []
                for i in members:
                    P = g.polylines[i]
                    pts = [_to_world(q, pl) for q in P['pts']]
                    if P.get('closed') and len(pts) > 1:
                        pts = pts + [pts[0]]
                    rings.append(pts)
                pieces[L].append(make_piece(L, c, rings, members, max(min(bead / 4.0, 0.75), 1e-3)))
                if mp is not None:
                    d = min((math.hypot(q[0] - mp[0], q[1] - mp[1]) for i in members for q in g.polylines[i]['pts']),
                            default=math.inf)
                    if best is None or d < best[0]:
                        best = (d, (L, c))
            if best is not None:
                manual.add(best[1])
        G = lift_graph(pieces, bead_l, settings.head_clearance)
        printed = []                                   # (bbox, z) of this lift's printed pieces

        def visit(key):
            nonlocal pos, prev_z
            check()
            L, c = key
            g, pl, bead, comps = geo[L]
            layer = plan.layers[L]
            q = _to_design(pos, pl) if pos is not None else None
            whole = len(comps) == 1
            if has_router:
                moves, info, identity = (source.route(g, q) if whole else
                                         source.route(g, q, members=comps[c], manual_start=key in manual))
            else:
                moves, info, identity = _fallback_route(g, q, None if whole else comps[c])
            P = Pass(len(plan.passes), L, li, c, layer.z, manual_start=info.get('manual_start', False),
                     reversed=info.get('reversed', False), origins=info.get('origins') or [])
            P.steps = _layer_steps(moves, pl, identity, drop_leading_travel=q is not None,
                                   run_base=len(layer.runs))
            if P.runs:
                margin = bead / 2.0 + settings.head_clearance
                for t in P.travels:
                    t.clear_z = _clear_z(t.start, t.end, P.z, printed, margin)
                if pos is not None:
                    P.transition_in = Travel(pos, P.start, dz=(P.z - prev_z) if prev_z is not None else 0.0,
                                             transition=True,
                                             clear_z=max(P.z, _clear_z(pos, P.start, prev_z if prev_z is not None
                                                                       else P.z, printed, margin)))
                pos, prev_z = P.end, P.z
                printed.append((G.pieces[key].bbox, P.z))
            plan.passes.append(P)
            layer.passes.append(P)
            lift.passes.append(P.index)
            return pos
        order = schedule(G, pos, manual, visit=visit)
        sid, sinfo = stages(G, order)
        base = len(plan.stages)
        for k, si in enumerate(sinfo):
            plan.stages.append(Stage(base + k, li, si['name'], si['kind'], [base + p for p in si['parents']]))
            lift.stages.append(base + k)
        for P in (plan.passes[i] for i in lift.passes):
            st = plan.stages[base + sid[(P.layer, P.component)]]
            P.stage = st.index
            st.passes.append(P.index)
            if P.layer not in st.layers:
                st.layers.append(P.layer)
        for st in plan.stages[base:]:
            st.layers.sort()
    for L in plan.layers:
        if not L.runs:
            plan.warnings.append(f'layer {L.index + 1} ({L.design_id}) has nothing to print')
    return plan


# ---------------------------------------------------------------------------
# timing (estimates)
# ---------------------------------------------------------------------------

ASSUMPTIONS = ('Estimate from ordered path lengths at constant speeds: printing (incl. any retrace) at the '
               'print speed, every non-printing move (travel inside a piece and the transition to the next '
               'piece / layer, XY distance) at the travel speed, plus the estimated time of each frame '
               'reposition between lifts (if given). Not machine timing: acceleration, cornering, pump '
               'response, Z motion (manual today) and pauses are not included.')


def duration(kind: str, length: float, speeds: PrintSpeeds) -> float:
    """THE timing rule (every estimate, timeline and stream uses it): a
    move of `kind` and XY `length` takes length / speed — extrusion ('print',
    'retrace') at the print speed, every non-printing move ('travel',
    'transition') at the travel speed; a 'frame' reposition takes the
    estimated frame time."""
    if kind == 'frame':
        return speeds.frame_time
    return length / (speeds.print_speed if kind in ('print', 'retrace') else speeds.travel_speed)


def _pass_times(P: Pass, speeds):
    pl = sum(r.print_length + r.retrace_length for r in P.runs)
    tl = sum(t.length for t in P.travels) + (P.transition_in.length if P.transition_in else 0.0)
    return duration('print', pl, speeds), duration('travel', tl, speeds)


def estimate(plan: PrintPlan, speeds: PrintSpeeds) -> dict:
    """Estimated durations (seconds) per run, layer, section, lift, stage and
    the whole print — extrusion, travel and frame repositioning separately."""
    layers, sections = [], {}
    tot = {'print_length': 0.0, 'retrace_length': 0.0, 'travel_length': 0.0, 'transition_length': 0.0}
    for L in plan.layers:
        runs = []
        for r in L.runs:
            ln = r.print_length + r.retrace_length
            runs.append({'index': r.index, 'length': round(ln, 3), 'time': duration('print', ln, speeds),
                         'sources': r.sources, 'systems': r.systems})
        pl = sum(r.print_length for r in L.runs)
        rl = sum(r.retrace_length for r in L.runs)
        tl = sum(t.length for t in L.travels)
        trl = sum(t.length for t in L.transitions)
        e = {'index': L.index, 'section_index': L.section_index, 'design_id': L.design_id, 'lift': L.lift,
             'runs': runs, 'print_length': pl, 'retrace_length': rl, 'travel_length': tl,
             'transition_length': trl, 'print_time': duration('print', pl + rl, speeds),
             'travel_time': duration('travel', tl + trl, speeds),
             'passes': [{'index': P.index, 'stage': P.stage, 'runs': [r.index for r in P.runs]} for P in L.passes]}
        e['total_time'] = e['print_time'] + e['travel_time']
        layers.append(e)
        s = sections.setdefault(L.section_index, {'index': L.section_index, 'layers': 0, 'print_time': 0.0,
                                                  'travel_time': 0.0, 'print_length': 0.0, 'travel_length': 0.0})
        s['layers'] += 1
        s['print_time'] += e['print_time']
        s['travel_time'] += e['travel_time']
        s['print_length'] += pl + rl
        s['travel_length'] += tl + trl
        tot['print_length'] += pl
        tot['retrace_length'] += rl
        tot['travel_length'] += tl
        tot['transition_length'] += trl
    for s in sections.values():
        s['total_time'] = s['print_time'] + s['travel_time']
    stages = []
    for st in plan.stages:
        p = t = 0.0
        for i in st.passes:
            a, b = _pass_times(plan.passes[i], speeds)
            p += a
            t += b
        stages.append(dict(st.to_dict(plan), print_time=p, travel_time=t, total_time=p + t))
    lifts = []
    for Lf in plan.lifts:
        p = t = 0.0
        for i in Lf.passes:
            a, b = _pass_times(plan.passes[i], speeds)
            p += a
            t += b
        f = duration('frame', 0.0, speeds) if Lf.reposition_in is not None else 0.0
        lifts.append(dict(Lf.to_dict(), print_time=p, travel_time=t, frame_time=f, total_time=p + t + f))
    pt = duration('print', tot['print_length'] + tot['retrace_length'], speeds)
    tt = duration('travel', tot['travel_length'] + tot['transition_length'], speeds)
    frames = sum(1 for Lf in plan.lifts if Lf.reposition_in is not None)
    ft = frames * duration('frame', 0.0, speeds)
    return {'estimate': True, 'assumptions': ASSUMPTIONS, 'speeds': speeds.to_dict(),
            'print_time': pt, 'travel_time': tt, 'frame_time': ft, 'frame_moves': frames, 'total_time': pt + tt + ft,
            'runs': sum(len(L.runs) for L in plan.layers), **{k: v for k, v in tot.items()},
            'layers': layers, 'sections': [sections[k] for k in sorted(sections)], 'lifts': lifts, 'stages': stages}


def timeline(plan: PrintPlan, speeds: PrintSpeeds):
    """Every move of `plan.moves()` with its estimated start / end time (s):
    (t0, t1, move). The clock the Print Simulation plays."""
    t = 0.0
    for mv in plan.moves():
        kind, a, b = mv[2], mv[3], mv[4]
        t1 = t + duration(kind, math.hypot(b[0] - a[0], b[1] - a[1]), speeds)
        yield t, t1, mv
        t = t1


# ---------------------------------------------------------------------------
# STREAM: the plan as compact arrays (Print Simulation, inspection)
# ---------------------------------------------------------------------------
# One ITEM per polyline in print order — a FRAME reposition, a TRANSITION
# into a pass, a TRAVEL, or one SEGMENT of a run (one kind, one strand) —
# with its vertices in a flat coordinate array, its lift / stage / pass and
# its start time / duration from `duration` (the client interpolates
# position inside an item linearly in arc length: one item = one kind = one
# constant speed, exactly the estimate's model).
KIND_CODE = {'print': 0, 'retrace': 1, 'travel': 2, 'transition': 3, 'frame': 4}


def items(plan: PrintPlan):
    """(kind, layer, run | -1, pts [(x, y)…], z0, z1, strand, source, system,
    lift, stage, pass, clear_z) in print order (z0 ≠ z1 only for a
    transition; clear_z: the Z of a non-printing XY move, see Travel)."""
    prev_z = None
    for P in plan.passes:
        Lf = plan.lifts[P.lift]
        if Lf.reposition_in is not None and Lf.passes and Lf.passes[0] == P.index:
            fm = Lf.reposition_in
            yield ('frame', P.layer, -1, [fm.at, fm.at], fm.z, fm.z, None, None, None, P.lift, P.stage, P.index, fm.z)
        if P.transition_in is not None:
            t = P.transition_in
            z0 = prev_z if prev_z is not None else P.z
            yield ('transition', P.layer, -1, [t.start, t.end], z0, P.z, None, None, None, P.lift, P.stage, P.index,
                   t.clear_z if t.clear_z is not None else max(z0, P.z))
        for st in P.steps:
            if isinstance(st, Travel):
                yield ('travel', P.layer, -1, [st.start, st.end], P.z, P.z, None, None, None, P.lift, P.stage, P.index,
                       st.clear_z if st.clear_z is not None else P.z)
            else:
                for seg in st.segments:
                    yield (seg.kind, P.layer, st.index, seg.pts, P.z, P.z, seg.strand, seg.source, seg.system,
                           P.lift, P.stage, P.index, P.z)
        if P.runs:
            prev_z = P.z


def item_times(plan: PrintPlan, speeds: PrintSpeeds) -> tuple:
    """(t0 list, dt list) per item — the same clock as `timeline`."""
    t, t0s, dts = 0.0, [], []
    for it in items(plan):
        d = duration(it[0], _len(it[3]), speeds)
        t0s.append(t)
        dts.append(d)
        t += d
    return t0s, dts


def stream(plan: PrintPlan, speeds: PrintSpeeds, digits=3) -> dict:
    """The plan as compact arrays + its times (see STREAM). Coordinates to
    `digits` decimals (0.001 in: the PLAYBACK copy — the plan itself, which
    export / execution use, keeps full precision)."""
    strings, sidx = [], {}

    def S(v):
        if v is None:
            return -1
        if v not in sidx:
            sidx[v] = len(strings)
            strings.append(v)
        return sidx[v]
    cols = {k: [] for k in ('kind', 'layer', 'run', 'off', 'n', 'z0', 'z1', 'strand', 'source', 'system',
                            'lift', 'stage', 'pass', 'zc')}
    xy = []
    for (k, li, ri, pts, za, zb, st, so, sy, lf, sg, ps, zc) in items(plan):
        cols['kind'].append(KIND_CODE[k]); cols['layer'].append(li); cols['run'].append(ri)
        cols['off'].append(len(xy) // 2); cols['n'].append(len(pts))
        cols['z0'].append(round(za, 6)); cols['z1'].append(round(zb, 6)); cols['zc'].append(round(zc, 6))
        cols['strand'].append(S(st)); cols['source'].append(S(so)); cols['system'].append(S(sy))
        cols['lift'].append(lf); cols['stage'].append(sg); cols['pass'].append(ps)
        for (x, y) in pts:
            xy.append(round(x, digits)); xy.append(round(y, digits))
    t0, dt = item_times(plan, speeds)
    cols['t0'] = [round(v, 6) for v in t0]
    cols['dt'] = [round(v, 6) for v in dt]
    return {'items': cols, 'xy': xy, 'strings': strings, 'kinds': list(KIND_CODE)}


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h} h {m:02d} min' if h else (f'{m} min {s:02d} s' if m else f'{s} s')
