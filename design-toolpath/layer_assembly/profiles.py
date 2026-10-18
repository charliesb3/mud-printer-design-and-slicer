"""
ASSEMBLY PROFILES — Z-dependent SOURCE-geometry profiles (2026-10-08).

A separate subsystem from Assembly Transform. Core rule:

    Assembly Profiles determine what source wall geometry survives at each Z.
    They resolve into ordinary 2D layer geometry, which then goes through the
    existing Wall System / lattice / opening / routing machinery.

So a profile never touches printable geometry. Per layer it resolves to a
CUT SPEC — a few source-level operations named by source id, like the
semantic transforms of transform groups — that the Designer applies to the
layer's design document BEFORE it resolves it (design_proto/profile_cuts.py):
    ('opening', source, edge, at, width, profile id)
        an ordinary Designer Opening (clear width `width`, centred `at`
        inches along the straight wall / edge from its start) — cut through
        the whole wall assembly, cut faces capped, Wall Systems regenerated
    ('extent', source, end, retained, profile id)
        the straight open wall shortened from `end` ('start' | 'end') so
        `retained` inches of it survive (0 = the wall is absent at that Z) —
        a taper's cut position converted for the Designer
    ('transition', source, edge, at, width, profile id, α)
        NO cut: a layer of an opening's CROSS-Z TRANSITION (just below its
        first / above its last cut layer): the Designer pre-deforms the
        pattern by α toward that cut layer's local end motif (a virtual
        opening of `width` at `at`), so the stack changes by ≤ h per layer
    ('station', source, edge, at, 0, profile id)
        NO cut: on every layer of a design that has a valid opening
        profile's wall — the Designer then keeps one lattice reference for
        all those layers (a single-pass lattice run crossed by the opening
        is doubled on every layer, as a Door Gap derived design's family
        does), so the cut layers stay a local change

RESOLUTION ORDER (per layer; decision 2026-10-08):
    Layer Design document (inheritance applied)
      → PROFILE CUTS (this module decides them from the layer's Z; the
        Designer applies them in DESIGN coordinates of the untransformed
        sources: profile_cuts.apply_cuts)
      → semantic transform of transform groups (semantic_transform: opening
        positions scale with their host, widths stay physical — exactly as for
        Designer openings)
      → normal Designer resolution (walls, Wall Systems, openings / caps,
        junctions, lattice, printable centrelines)
      → the layer's InstanceTransform places it (section / assembly-wide
        transform, unchanged).
  Placements (footprints, scale centres) are computed from the CANONICAL
  designs, so a profile never changes how layers are placed.

PROFILE KINDS (V1):
  OpeningProfile — a SELF-SUPPORTING through-wall opening: centre position
    along a straight wall, bottom Z, height, maximum width and a SHAPE whose
    width w(z) closes gradually (no header). Layer k (Z range [zb, zt]) gets
    the width at its mid-height; a layer the shape crosses more narrowly
    than MIN_OPENING_BEADS beads is cut that wide (never dropped, so the
    first opening and the final closure step no more than the flanks).
  ExtentProfile — a CUT PROFILE of a straight open wall, drawn in its
    elevation (revised 2026-10-08): KNOTS [(z, s)] — the cut position s
    (inches from the wall's start) at height z — with an interpolation per
    span ('linear'; the representation also takes 'step' so stepped /
    piecewise profiles — stairs, shelves — can be added without changing the
    model; V1 UI edits exactly two points = one diagonal cut) and the
    RETAINED side (`keep` 'start' | 'end'); the other side disappears layer
    by layer. The cut points are authoritative (a short local buttress taper
    is two points near the free end). Below the first point the wall is
    unaffected; above the last point the cut holds. Retained under
    MIN_RETAINED_BEADS beads or under the Designer's printable minimum for the
    wall's construction (WallTarget.min_length: 2 × thickness for generated
    Wall Systems, whose canonical end motifs degenerate on shorter stubs) →
    the wall is absent at that layer.

PRINTABILITY (the existing support rule — maximum overhang h,
Assembly.max_overhang): validated on the ACTUAL layers of the resolved stack:
  * opening: wherever the opening narrows from layer i−1 to i, each side
    steps (w_{i−1} − w_i)/2 over the void — must be ≤ h. The layer that
    closes it steps w_{last}/2. A profile that fails is NOT generated (the
    wall stays solid) and is reported with the reason and the smallest
    height / largest width that would work — never a header request.
  * extent: evaluated on the RESULT, direction-free: each layer's KEPT
    stretch of the wall (an interval along it) against the stretch below —
    a retreating end is never an overhang; where the kept stretch reaches
    past the one below (either end, or appears over a layer without the
    wall) it overhangs by that much — must be ≤ h (else not generated,
    reported). With one cut line, the two Keep sides are complementary: the
    side that grows outward as it rises is (correctly) rejected.
The support checker (support.py) still analyses the resolved geometry —
it is never weakened; a valid profile produces no finding by construction.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Optional

from . import opening_shape as OS
from .model import InstanceTransform

MIN_OPENING_BEADS = 0.25      # the smallest cut: a layer the shape crosses more narrowly is cut this wide
                              # (never dropped — the first / closing step stays ≤ ½ of it)
MIN_RETAINED_BEADS = 1.0      # a wall shorter than one bead is absent at that layer (and shorter than the
                              # Designer's printable minimum for its construction: WallTarget.min_length)
JAMB_BEADS = 1.0              # solid wall kept beside an opening: half the wall thickness + 1 bead
# ONE OPENING (revised 2026-10-10, third pass — opening_shape.py): LOWER flat |
# angular | curved | rounded, optional MIDDLE, UPPER angular | curved Gothic |
# HEADER. Diamond / Lens / Arch openings of older projects are MIGRATED on load
# (opening_shape.migrate / fill) to the identical configuration.
UPPERS = ('pointed', 'curve', 'header')
LOWERS = ('flat', 'pointed', 'arc', 'round')
ALIGNMENTS = ('follow', 'plumb')     # OPENING ALIGNMENT — ONE global Assembly setting (Assembly.opening_alignment)
ARRANGEMENTS = ('single', 'horizontal', 'vertical', 'diagonal_up', 'diagonal_down', 'grid', 'staggered')
MAX_MEMBERS = 400             # openings of one array
MIN_PIER_BEADS = 2.0          # solid wall left between two openings of a layer (and the construction's
                              # printable minimum: WallTarget.min_length)
ENDS = ('start', 'end')
KEEPS = ('start', 'end', 'auto')   # the retained side of a taper's cut line ('auto': the supported side)
INTERPOLATIONS = ('linear', 'step')
ROUND = 4                     # cut values are rounded to 1e-4 in (stable variant keys)


class ProfileError(ValueError):
    pass


# ---- wall references ------------------------------------------------------------

@dataclass(frozen=True)
class WallRef:
    """A STRAIGHT wall of the Designer: a source path id and, for a source
    with several straight edges (a rectangle, a polyline), the edge index
    (None = the whole source is one straight open wall)."""
    source: str
    edge: Optional[int] = None

    @property
    def key(self) -> str:
        return self.source if self.edge is None else f'{self.source}#{self.edge}'

    def to_dict(self) -> dict:
        return {'source': self.source, 'edge': self.edge}

    @staticmethod
    def from_dict(d) -> 'WallRef':
        e = d.get('edge')
        return WallRef(str(d['source']), None if e is None else int(e))


@dataclass(frozen=True)
class WallTarget:
    """A straight wall as the Designer reports it (LayerSource.walls):
    design coordinates of its centre line a → b, length, effective wall
    thickness, whether it can be tapered (a whole straight OPEN wall)."""
    source: str
    edge: Optional[int]
    a: tuple
    b: tuple
    length: float
    thickness: float = 0.0
    taper: bool = False
    label: str = ''
    min_length: float = 0.0            # shortest printable length of its construction (Designer)
    attached: tuple = ()               # which ends touch other walls: subset of ('start', 'end') (UI default)

    @property
    def key(self) -> str:
        return WallRef(self.source, self.edge).key

    @staticmethod
    def from_dict(d) -> 'WallTarget':
        return WallTarget(str(d['source']), d.get('edge'), tuple(d['a']), tuple(d['b']),
                          float(d['length']), float(d.get('thickness') or 0.0),
                          bool(d.get('taper')), d.get('label', '') or '', float(d.get('min_length') or 0.0),
                          tuple(d.get('attached') or ()))

    def to_dict(self) -> dict:
        return {'source': self.source, 'edge': self.edge, 'key': self.key, 'a': list(self.a),
                'b': list(self.b), 'length': self.length, 'thickness': self.thickness,
                'taper': self.taper, 'label': self.label, 'min_length': self.min_length,
                'attached': list(self.attached)}


# ---- opening shapes: opening_shape.py (shared, line for line, with the UI) ----


@dataclass(frozen=True)
class OpeningArray:
    """ONE parametric array of IDENTICAL openings (clones of the profile's
    master shape). Spacing is CLEAR distance between opening EDGES (not
    centre to centre): horizontal = between the widest edges of neighbours,
    vertical = from one opening's top to the next one's bottom. Diagonals use
    the same two numbers as their horizontal / vertical OFFSETS between
    consecutive openings (the vertical one may be negative, down to −height:
    neighbours overlap in height, separated sideways). Staggered: every second
    row shifted by `stagger` (None = half the horizontal repeat). The profile's
    position / bottom Z are the array's LOWER-LEFT opening: the left-most
    column's centre and the lowest bottom."""
    arrangement: str = 'single'
    count: int = 1                     # horizontal / vertical / diagonal
    cols: int = 1                      # grid / staggered
    rows: int = 1
    h_spacing: float = 12.0
    v_spacing: float = 12.0
    stagger: Optional[float] = None

    def offsets(self, W: float, H: float) -> list:
        """[(Δposition, Δz_bottom)] of every opening from the lower-left one."""
        return OS.array_offsets(self.to_dict(), W, H)

    def to_dict(self) -> dict:
        return {'arrangement': self.arrangement, 'count': self.count, 'cols': self.cols, 'rows': self.rows,
                'h_spacing': self.h_spacing, 'v_spacing': self.v_spacing, 'stagger': self.stagger}

    @staticmethod
    def from_dict(d) -> 'OpeningArray':
        a = OpeningArray(d.get('arrangement') or 'single', int(d.get('count', 1)), int(d.get('cols', 1)),
                         int(d.get('rows', 1)), float(d.get('h_spacing', 12.0)), float(d.get('v_spacing', 12.0)),
                         None if d.get('stagger') is None else float(d['stagger']))
        if a.arrangement not in ARRANGEMENTS:
            raise ProfileError(f'unknown array arrangement {a.arrangement!r}')
        if min(a.count, a.cols, a.rows) < 1:
            raise ProfileError('an array needs at least one opening per row / column')
        if len(a.offsets(1.0, 1.0)) > MAX_MEMBERS:
            raise ProfileError(f'an array may have at most {MAX_MEMBERS} openings')
        return a


@dataclass(frozen=True)
class OpeningProfile:
    """A through-wall opening — lower profile, optional middle, upper profile
    (opening_shape.py): self-supporting (a closing upper profile) or, with
    the HEADER upper, flat-topped and carried by a physical header the
    Assembly generates for it (`header` = its settings). Its
    parameters are the PRINTABLE shape (the UI constrains them while editing
    — opening_shape.constrain); resolution validates them, never alters them."""
    id: str
    wall: WallRef
    position: float                    # centre, inches along the wall from its start
    z_bottom: float
    height: float                      # TOTAL = lower depth + middle + upper height (derived)
    width: float                       # maximum (clear) width
    shape: Optional[str] = None        # OLDER INPUT ONLY ('diamond' | 'lens' | 'arch'; None with no
                                       # components = a diamond): migrated, '' after construction
    label: str = ''
    kind: str = 'opening'
    upper: str = 'pointed'             # 'pointed' (Angular) | 'curve' (Curved, Gothic) | 'header'
    lower: str = ''                    # 'flat' | 'pointed' (Angular) | 'arc' (Curved, sharp vesica
                                       # half) | 'round' (Rounded half ellipse; stored 'curve' before)
    bottom_depth: Optional[float] = None    # the lower profile's depth
    array: Optional[OpeningArray] = None    # ONE parametric array of identical clones
    # CONVERSION of a Designer opening (2026-10-09): {'id': opening id, 'z_ref': a Z
    # inside it}. Its rectangle (position, clear width, the layers that have it) is
    # the DESIGNER's — never cut again here; this profile adds only the closure
    # above it (lower flat, its z_bottom / height / width / position are
    # display copies, re-read from the Designer at every resolve)
    source_opening: Optional[dict] = None
    driver: Optional[str] = None            # the dimension edited last ('width' | 'upper_height')
    upper_height: Optional[float] = None    # the upper profile's height (None: an older arch → the
                                            # minimum printable one, opening_shape.fill)
    middle: Optional[bool] = None           # the rectangular middle on / off (None: older input)
    middle_height: Optional[float] = None   # kept while the middle is off
    header: Optional[dict] = None           # HEADER settings {bearing, depth, thickness} (kept while
                                            # another upper is chosen, so switching back restores them)

    def __post_init__(self):
        d = self.shape_dict()
        if self.shape is not None:
            d['shape'] = self.shape         # ('' = already the one opening: never re-migrated as a diamond)
        OS.migrate(d)                       # Diamond / Lens / older arches → the one opening
        OS.defaults(d)
        if d['middle'] is not None and (d['upper_height'] is not None or d['upper'] == 'header'):
            OS.sync(d)                      # the total height IS its components
        object.__setattr__(self, 'shape', '')
        for k in ('upper', 'lower', 'bottom_depth', 'upper_height', 'middle', 'middle_height', 'height', 'driver'):
            if d.get(k) != getattr(self, k):
                object.__setattr__(self, k, d.get(k))

    def shape_dict(self) -> dict:
        """The parameters opening_shape works on."""
        return {'upper': self.upper, 'lower': self.lower, 'width': self.width,
                'height': self.height, 'z_bottom': self.z_bottom, 'bottom_depth': self.bottom_depth,
                'upper_height': self.upper_height, 'middle': self.middle, 'middle_height': self.middle_height,
                'array': self.array.to_dict() if self.array else None, 'driver': self.driver}

    def completed(self, lh, h, bead) -> 'OpeningProfile':
        """An older arch with its upper height / middle filled in (opening_shape.fill)."""
        d = self.shape_dict()
        if not OS.fill(d, lh, h, bead):
            return self
        return replace(self, upper_height=d['upper_height'], middle=d['middle'],
                       middle_height=d['middle_height'], height=d['height'])

    def members(self) -> list:
        """[(position, z_bottom)] of every opening (the master alone without an array)."""
        if self.array is None or self.source_opening:
            return [(self.position, self.z_bottom)]
        return [(self.position + dx, self.z_bottom + dz) for dx, dz in self.array.offsets(self.width, self.height)]

    def width_at(self, z: float) -> float:
        """The continuous shape's width at Z."""
        d, m, U, H = OS.parts(self.shape_dict())
        return OS.width_at(z - self.z_bottom, self.width, d, m, U, self.upper, self.lower)

    @property
    def z_top(self) -> float:
        return self.z_bottom + self.height

    def header_settings(self, thickness=None, bead=None) -> dict:
        """bearing / depth / thickness of its header (depth default: the
        wall thickness + 1 bead)."""
        hs = dict(OS.HEADER_DEFAULTS, **{k: v for k, v in (self.header or {}).items() if v is not None})
        if hs['depth'] is None:
            hs['depth'] = round((thickness or 0.0) + (bead or 0.0), 4)
        return hs

    def to_dict(self) -> dict:
        d = {'kind': 'opening', 'id': self.id, 'wall': self.wall.to_dict(), 'position': self.position,
             'z_bottom': self.z_bottom, 'height': self.height, 'width': self.width,
             'label': self.label, 'upper': self.upper, 'lower': self.lower, 'bottom_depth': self.bottom_depth,
             'upper_height': self.upper_height, 'middle': self.middle, 'middle_height': self.middle_height}
        if self.middle is None:
            d['shape'] = 'arch'             # an older closure-based arch not completed yet (opening_shape.fill)
        if self.header:
            d['header'] = dict(self.header)
        if self.array is not None:
            d['array'] = self.array.to_dict()
        if self.source_opening:
            d['source_opening'] = dict(self.source_opening)
        if self.driver:
            d['driver'] = self.driver
        return d

    @staticmethod
    def from_dict(d) -> 'OpeningProfile':
        d = dict(d)
        OS.migrate(d)
        f = lambda k: None if d.get(k) is None else float(d[k])
        src = d.get('source_opening')
        hd = d.get('header')
        p = OpeningProfile(str(d['id']), WallRef.from_dict(d['wall']), float(d['position']),
                           float(d.get('z_bottom', 0.0)), float(d['height']), float(d['width']),
                           d.pop('shape', '') if d.get('middle') is None else '', d.get('label', '') or '',
                           upper=d.get('upper') or 'pointed', lower=d.get('lower') or '',
                           bottom_depth=f('bottom_depth'),
                           array=OpeningArray.from_dict(d['array']) if d.get('array') else None,
                           source_opening={'id': str(src['id']), 'z_ref': float(src.get('z_ref', 0.0))} if src else None,
                           driver=d.get('driver') if d.get('driver') in ('width', 'upper_height') else None,
                           upper_height=f('upper_height'),
                           middle=None if d.get('middle') is None else bool(d['middle']),
                           middle_height=f('middle_height'),
                           header={k: (None if hd.get(k) is None else float(hd[k])) for k in OS.HEADER_DEFAULTS
                                   if k in hd} if isinstance(hd, dict) else None)
        if p.upper not in OS.UPPERS:
            raise ProfileError(f'profile {p.id}: unknown upper profile {p.upper!r}')
        if p.lower not in OS.LOWERS:
            raise ProfileError(f'profile {p.id}: unknown lower profile {p.lower!r}')
        if p.height < 0 or p.width < 0 or any(v is not None and v < 0 for v in
                                              (p.bottom_depth, p.upper_height, p.middle_height)) or \
                any(v is not None and v < 0 for v in (p.header or {}).values()):
            raise ProfileError(f'profile {p.id}: dimensions must be ≥ 0')
        if p.source_opening and (p.lower != 'flat' or p.upper == 'header'):
            raise ProfileError(f'profile {p.id}: a converted Designer opening has a flat lower and a closing upper')
        return p


@dataclass(frozen=True)
class ExtentProfile:
    """A CUT PROFILE of a straight open wall in its elevation (revised
    2026-10-08 after manual review): the cut position along the wall as a
    function of Z — knots ((z, s), …), s = inches from the wall's START point
    — and which side of that cut line is RETAINED (`keep`: 'start' = the
    stretch from the start point to the cut survives, the end side
    disappears; 'end' = the opposite). The cut endpoints are authoritative:
    a short local buttress taper is two points near the free end, nothing
    forces the cut to reach either architectural end of the wall.
    Per span: 'linear' (V1 UI: one span = a straight diagonal cut) or
    'step' (stairs / shelves later — more knots, no model change). Below the
    first knot the wall is UNAFFECTED; above the last knot the last cut
    position HOLDS (the cut continues vertically)."""
    id: str
    wall: WallRef
    keep: str = 'start'                # which side of the cut survives: 'start' | 'end' | 'auto'
    knots: tuple = ()                  # ((z, s), …) by increasing z; s along the wall from its start
    interp: tuple = ()                 # per span: 'linear' | 'step' (missing → linear)
    label: str = ''
    kind: str = 'extent'

    @property
    def end(self) -> str:
        """The wall end that retreats (the Designer cut spec)."""
        return 'end' if self.keep == 'start' else 'start'

    def cut_at(self, z: float) -> Optional[float]:
        """The cut position (from the wall's start) at z; None = unaffected
        (below the first knot)."""
        ks = self.knots
        if not ks or z < ks[0][0]:
            return None
        if z >= ks[-1][0]:
            return ks[-1][1]
        for j in range(len(ks) - 1):
            (z0, s0), (z1, s1) = ks[j], ks[j + 1]
            if z0 <= z < z1:
                mode = self.interp[j] if j < len(self.interp) else 'linear'
                if mode == 'step' or z1 - z0 <= 1e-12:
                    return s0
                return s0 + (s1 - s0) * (z - z0) / (z1 - z0)
        return ks[-1][1]

    def retained_at(self, z: float, length: float) -> Optional[float]:
        """Retained length of a wall of `length` at z (None = unaffected)."""
        c = self.cut_at(z)
        if c is None:
            return None
        c = max(0.0, min(float(length), c))
        return c if self.keep == 'start' else float(length) - c

    def mirrored(self, length: float) -> 'ExtentProfile':
        """The SAME physical cut described from the wall's other end (the
        wall drawn the other way, or 'keep the other end' of a buttress):
        s → length − s and the kept side swapped."""
        return replace(self, keep={'start': 'end', 'end': 'start'}.get(self.keep, self.keep),
                       knots=tuple((z, float(length) - c) for z, c in self.knots))

    def to_dict(self) -> dict:
        return {'kind': 'extent', 'id': self.id, 'wall': self.wall.to_dict(), 'keep': self.keep,
                'knots': [list(k) for k in self.knots], 'interp': list(self.interp), 'label': self.label}

    @staticmethod
    def from_dict(d) -> 'ExtentProfile':
        keep = d.get('keep')
        if keep is None and d.get('end') in (None, 'end'):
            keep = 'start'        # (pre-revision dicts with end 'end': retained length = cut position)
        if keep not in KEEPS:
            raise ProfileError(f'profile {d.get("id")}: keep must be start, end or auto')
        ks = tuple((float(z), float(c)) for z, c in (d.get('knots') or ()))
        p = ExtentProfile(str(d['id']), WallRef.from_dict(d['wall']), keep,
                          tuple(sorted(ks)), tuple(d.get('interp') or ()), d.get('label', '') or '')
        if any(m not in INTERPOLATIONS for m in p.interp):
            raise ProfileError(f'profile {p.id}: unknown interpolation')
        if len({z for z, _ in p.knots}) != len(p.knots):
            raise ProfileError(f'profile {p.id}: two cut points at the same Z')
        return p


def taper(id, wall, keep, s_lower, z_lower, s_upper, z_upper, label='') -> ExtentProfile:
    """The V1 taper: a straight diagonal CUT between two elevation points
    (s along the wall from its start, Z); `keep` = the retained side."""
    if not z_upper > z_lower:
        raise ProfileError('the upper cut point must be above the lower one')
    return ExtentProfile(id, wall, keep, ((float(z_lower), float(s_lower)),
                                          (float(z_upper), float(s_upper))), ('linear',), label)


@dataclass(frozen=True)
class PlaneProfile:
    """PLANAR TOP CUT (2026-10-08): ONE plane in 3D cuts a SELECTION of walls;
    above it their material is removed. Mathematically

        z(x, y) = z_anchor + slope · ((x, y) − anchor) · d,   d = (cos θ, sin θ)

    in WORLD (placed, Assembly) plan coordinates: θ = `direction`, the
    horizontal UPHILL direction (degrees), slope = rise / run ≥ 0 (0 =
    horizontal). At a layer of mid-height z the plane is one LINE in plan,
    {p : d·(p − anchor) = (z − z_anchor) / slope}; the uphill side is kept —
    the same half-plane for every selected wall, so all their top ends lie
    on the one plane. High / low elevations across a footprint and the slope
    angle are VIEWS of these four numbers (plane_from_high_low).
    Selection: `sources` (walls) ∪ the members of `systems` (Wall Systems,
    resolved per design by the Designer) − `exclude`.

    PROFILE TYPES (2026-10-15) — the same stage, the same per-layer op: what
    changes is only WHERE each layer's plan line lies. t = d·(p − anchor) is
    the run along the uphill direction from the START point (anchor, at
    z_anchor); the END point lies `span` further, at z_anchor + slope·span
    (the chord: the plane above IS the chord's plane). Each layer, at its
    mid-height z, keeps t ≥ cut_t(z) — whole layers, never split.
      LINEAR  — cut_t(z) = (z − z_anchor) / slope (the original plane;
                existing projects unchanged — `span` is not used).
      STEPPED — (revised 2026-10-15 after review: the centred stair spent
                half a riser at each end) T = max(2, round(span / step_run))
                treads of span / T: the first is a real LOWER LANDING at the
                start elevation, the last a real UPPER LANDING at the end
                elevation, and n = T − 1 risers of rise / n stand between them
                at runs tread, 2·tread, … A layer whose mid-height lies in
                riser j's band (z_anchor + (j − 1)·rise/n, z_anchor + j·rise/n]
                is cut at j·tread (cut_t = tread · ⌈q·n⌉, q = fraction of the
                rise); layers at / below the start keep everything, above the
                end nothing of the run. Risers are whole layers (mid-heights
                decide — deterministic, ±1 layer between risers).
      CURVED  — a quadratic Bézier in the (t, z) elevation from the start
                (0, 0) to the end (span, rise) with its control point at
                (u·span, v·rise), `control` = (u, v) ∈ [0, 1]² — inside the
                start–end box, so the curve is monotonic in both t and z
                (a planar cut only removes material as Z rises). v < u: the
                cut starts gentle and steepens; v > u: starts steep and
                flattens; u = v: exactly the linear chord. Each layer's
                cut_t solves the curve at its mid-height (bisection); below
                the start / above the end the curve continues along its end
                tangents. Start and end are exact."""
    id: str
    sources: tuple = ()
    systems: tuple = ()
    exclude: tuple = ()
    direction: float = 0.0             # degrees: the uphill direction in plan
    slope: float = 0.0                 # rise / run (≥ 0) — of the CHORD for stepped / curved
    anchor: tuple = (0.0, 0.0)         # a point of the plane's plan (world) — the START for stepped / curved
    z_anchor: float = 0.0              # the plane's Z at the anchor
    label: str = ''
    kind: str = 'plane'
    shape: str = 'linear'              # 'linear' | 'stepped' | 'curved'
    span: float = 0.0                  # stepped / curved: the run from the start to the end point (in)
    step_run: float = 12.0             # stepped: the requested tread (horizontal run per step, in)
    control: tuple = (0.5, 0.5)        # curved: the control point, fractions of (run, rise)

    @property
    def d(self) -> tuple:
        a = math.radians(self.direction)
        return (math.cos(a), math.sin(a))

    def z_at(self, x, y) -> float:
        dx, dy = self.d
        return self.z_anchor + self.slope * ((x - self.anchor[0]) * dx + (y - self.anchor[1]) * dy)

    @property
    def steps(self) -> tuple:
        """STEPPED: (number of risers, tread run, riser height) — treads =
        risers + 1, the first and last being the lower / upper landings."""
        T = max(2, math.floor(self.span / max(self.step_run, 1e-9) + 0.5))
        return T - 1, self.span / T, self.slope * self.span / (T - 1)

    def cut_t(self, z):
        """The run t (from the anchor, along the uphill direction) of the
        layer cut at mid-height z — or 'all' (everything kept) / 'none'
        (everything removed). Non-decreasing in z for every shape."""
        if self.slope <= 1e-12 or (self.shape != 'linear' and self.span <= 1e-9):
            return 'all' if z <= self.z_anchor + 1e-9 else 'none'
        tl = (z - self.z_anchor) / self.slope
        if self.shape == 'stepped':
            n, tread, _ = self.steps
            q = tl / self.span                         # fraction of the rise
            if q <= 0:
                return tl                              # at / below the lower landing (nothing of the run)
            if q > 1:
                return self.span * q                   # above the upper landing: the whole run removed
            return tread * math.ceil(q * n - 1e-9)     # riser j's band → the cut at the j-th riser
        if self.shape == 'curved':
            u, v = self.control
            q = tl / self.span                         # fraction of the rise
            if q < 0:                                  # below the start: along the start tangent
                return 'all' if v <= 1e-12 else self.span * (u / v) * q
            if q > 1:                                  # above the end: along the end tangent
                return 'none' if v >= 1 - 1e-12 else self.span * (1 + (1 - u) / (1 - v) * (q - 1))
            if q == 0 or q == 1:
                return self.span * q                   # (the end points, exactly)
            lo, hi = 0.0, 1.0                          # z(s) = 2v·s(1 − s) + s² is monotonic for v ∈ [0, 1]
            for _ in range(64):
                m = 0.5 * (lo + hi)
                if 2 * v * m * (1 - m) + m * m < q:
                    lo = m
                else:
                    hi = m
            m = 0.5 * (lo + hi)
            return self.span * (2 * u * m * (1 - m) + m * m)
        return tl

    def line_at(self, z):
        """(point, normal to the KEPT side) of the plane's plan line at z, or
        'all' (everything kept) / 'none' (everything removed) when level."""
        t = self.cut_t(z)
        if isinstance(t, str):
            return t
        dx, dy = self.d
        return ((self.anchor[0] + dx * t, self.anchor[1] + dy * t), (dx, dy))

    def t_of(self, x, y) -> float:
        """A plan point's run from the anchor along the uphill direction."""
        dx, dy = self.d
        return (x - self.anchor[0]) * dx + (y - self.anchor[1]) * dy

    def flipped(self) -> 'PlaneProfile':
        """FLIP SLOPE: high and low swap ends, the magnitude and the plane's
        height at the anchor are kept."""
        return replace(self, direction=(self.direction + 180.0) % 360.0)

    def to_dict(self) -> dict:
        out = {'kind': 'plane', 'id': self.id, 'sources': list(self.sources), 'systems': list(self.systems),
               'exclude': list(self.exclude), 'direction': self.direction, 'slope': self.slope,
               'anchor': list(self.anchor), 'z_anchor': self.z_anchor, 'label': self.label, 'shape': self.shape}
        if self.shape != 'linear':
            out.update(span=self.span, step_run=self.step_run, control=list(self.control))
        return out

    @staticmethod
    def from_dict(d) -> 'PlaneProfile':
        """(absent `shape` = 'linear': planes saved before 2026-10-15)"""
        shape = d.get('shape') or 'linear'
        if shape not in PLANE_SHAPES:
            raise ProfileError(f"profile {d.get('id')}: unknown planar cut type {shape!r}")
        ctl = d.get('control') or (0.5, 0.5)
        p = PlaneProfile(str(d['id']), tuple(str(x) for x in d.get('sources') or ()),
                         tuple(str(x) for x in d.get('systems') or ()), tuple(str(x) for x in d.get('exclude') or ()),
                         float(d.get('direction', 0.0)) % 360.0, float(d.get('slope', 0.0)),
                         tuple(float(v) for v in (d.get('anchor') or (0.0, 0.0))), float(d.get('z_anchor', 0.0)),
                         d.get('label', '') or '', shape=shape, span=float(d.get('span', 0.0) or 0.0),
                         step_run=float(12.0 if d.get('step_run') is None else d['step_run']),
                         control=tuple(float(v) for v in ctl))
        if p.slope < 0:
            raise ProfileError(f'profile {p.id}: slope must be ≥ 0 (use the direction to point it the other way)')
        if shape != 'linear':
            if not p.span > 0:
                raise ProfileError(f'profile {p.id}: a {shape} cut needs its start and end (span > 0)')
            if shape == 'stepped' and not p.step_run > 0:
                raise ProfileError(f'profile {p.id}: the step run must be > 0')
            if shape == 'curved' and (len(p.control) != 2 or not all(0.0 <= v <= 1.0 for v in p.control)):
                raise ProfileError(f'profile {p.id}: the curve control point must lie between the start and end '
                                   f'(fractions 0–1 of run and rise)')
        return p


PLANE_SHAPES = ('linear', 'stepped', 'curved')


def plane_from_high_low(pid, direction, extent, z_high, z_low, **kw) -> PlaneProfile:
    """The plane through Z `z_low` at the downhill end and `z_high` at the
    uphill end of a footprint whose projection on the uphill direction is
    extent = (s_min, s_max, reference point) — the high / low view."""
    s0, s1, ref = extent
    a = math.radians(direction)
    run = max(1e-9, s1 - s0)
    slope = max(0.0, (z_high - z_low) / run)
    anchor = (ref[0] + math.cos(a) * s0, ref[1] + math.sin(a) * s0)
    return PlaneProfile(pid, direction=direction % 360.0, slope=slope, anchor=anchor, z_anchor=z_low, **kw)


KINDS = {'opening': OpeningProfile, 'extent': ExtentProfile, 'plane': PlaneProfile}


def profile_from_dict(d):
    k = d.get('kind')
    if k not in KINDS:
        raise ProfileError(f'unknown profile kind {k!r}')
    return KINDS[k].from_dict(d)


# ---- per-layer resolution + validation ------------------------------------------------

def _mid(inst) -> float:
    return 0.5 * (inst.z_bottom + inst.z_top)


def _r(x) -> float:
    return round(float(x), ROUND)


@dataclass
class ProfileResult:
    """The resolution of one profile over the stack."""
    id: str
    kind: str
    valid: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    layers: list = field(default_factory=list)      # [{layer, z, width | retained, design}]
    worst_step: float = 0.0                         # largest overhang step (in)
    hint: dict = field(default_factory=dict)        # min height / max width that would work
    wall: Optional[dict] = None                     # the target (first design that has it)
    keep: Optional[str] = None                      # taper: the side actually kept ('auto' resolved)
    members: list = field(default_factory=list)     # array / arch: [{position, z_bottom}] of every opening
    geometry: dict = field(default_factory=dict)    # opening: lower depth, middle, upper height, spring, height
    conversion: dict = field(default_factory=dict)  # converted Designer opening: its rectangle + the closure
    headers: list = field(default_factory=list)     # HEADER upper: the headers generated for its openings

    def to_dict(self) -> dict:
        d = {'id': self.id, 'kind': self.kind, 'valid': self.valid, 'errors': self.errors,
             'warnings': self.warnings, 'layers': self.layers, 'worst_step': self.worst_step,
             'hint': self.hint, 'wall': self.wall, 'keep': self.keep}
        for k in ('members', 'geometry', 'conversion', 'headers'):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d


def _targets_for(prof, insts, targets):
    """{design id: WallTarget} for the designs of the stack that have the wall."""
    out = {}
    for d in dict.fromkeys(i.design_id for i in insts):
        t = (targets.get(d) or {}).get(prof.wall.key)
        if t is not None:
            out[d] = t
    return out


def _wmin(bead, h) -> float:
    return OS.wmin(bead, h)


def _lh(insts) -> float:
    """The stack's layer height (one global layer height: model.py)."""
    return (insts[0].z_top - insts[0].z_bottom) if insts else 1.0


def _shape_widths(p, zb, insts, tgs, wmin) -> list:
    """Per layer of the stack: the shape's cut width at the layer's
    mid-height (0 where the layer's design lacks the wall); a sliver is cut
    w_min wide — exactly opening_shape.layer_widths."""
    d, m, U, H = OS.parts(p.shape_dict())
    out = []
    for inst in insts:
        z = _mid(inst)
        w = OS.width_at(z - zb, p.width, d, m, U, p.upper, p.lower) \
            if inst.design_id in tgs and z >= zb else 0.0
        out.append(0.0 if w <= OS.EPS else max(w, wmin))
    return out


_NAMES = {'pointed': 'angular', 'curve': 'curved', 'flat': 'flat', 'header': 'header', 'arc': 'curved',
          'round': 'rounded'}


def _shape_error(p, ev, h) -> str:
    if ev['reason'] == 'empty':
        return 'the opening has no width or no height'
    if ev['reason'] == 'depth':
        return (f'its curved (sharp-pointed) bottom must be deeper than half its width: {ev["d"]:g} in ≤ '
                f'{p.width / 2:g} in (a Rounded bottom has no such limit)')
    what = f'its {_NAMES[p.upper]} upper profile'
    return (f'not self-supporting: {what} narrows a side by up to {ev["worst"]:.2f} in per layer '
            f'(maximum overhang {h:g} in)')


def _layer_at(insts, z) -> Optional[int]:
    for k, i in enumerate(insts):
        if i.z_bottom - 1e-9 <= z < i.z_top - 1e-9:
            return k
    return None


def _conversion(p, insts, tgs, openings, res):
    """A CONVERTED Designer opening: (first, last layer of the run of layers
    whose design has it, its record) or None (reported). The run is the
    contiguous layers around z_ref that print the opening — across sections
    and derived designs, as the stack is resolved NOW."""
    oid, zr = p.source_opening['id'], float(p.source_opening.get('z_ref', 0.0))

    def rec(k):
        return next((o for o in openings.get(insts[k].design_id) or () if o.get('id') == oid), None)
    k = _layer_at(insts, zr)
    if k is None or rec(k) is None:
        res.errors.append(f'the Designer opening {oid} is no longer in the layer at z {zr:g} — '
                          'remove this conversion (or put the opening back)')
        return None
    o = rec(k)
    if o.get('source') != p.wall.source or o.get('edge') != p.wall.edge or o.get('at') is None:
        res.errors.append(f'the Designer opening {oid} is not on a straight wall a self-supporting closure can follow')
        return None
    k0 = k1 = k
    while k0 > 0 and rec(k0 - 1) is not None:
        k0 -= 1
    while k1 + 1 < len(insts) and rec(k1 + 1) is not None:
        k1 += 1
    return k0, k1, o


# ---- OPENING ALIGNMENT: Vertical / Plumb (2026-10-10, global) -----------------
# ONE Assembly-wide setting (Assembly.opening_alignment 'follow' | 'plumb') for
# EVERY opening — Designer openings, Assembly openings, array clones, header
# openings, conversions. A PLUMB opening stays vertical in WORLD coordinates
# while the walls keep transforming: anchored at its world position at its
# FIRST ACTIVE LAYER (that layer's placement of its wall applied); on every
# layer the anchor is taken back into that layer's DESIGN coordinates through
# the same placement (the planar top cut does the same) and the opening is
# cut there, at its intended (physical) width: design width = width / scale.
# ONE algorithm (`plumb_track`) for both kinds; an opening whose wall moves
# completely away from its vertical line, or along which it would slide off
# the wall, FOLLOWS THE WALL instead and is reported (never broken geometry).

def wall_placement(inst, source):
    """The world placement of a source's design geometry at a layer: the
    layer's own InstanceTransform composed with the source's transform-GROUP
    placement relative to it (inst.semantic; 'multiple' centre mode) — so a
    wall moved by its group is compensated too."""
    T = inst.transform
    whole = None
    for s_, k, tx, ty in inst.semantic or ():
        if s_ == source:
            return InstanceTransform(T.scale * k, T.scale * tx + T.tx, T.scale * ty + T.ty)
        if s_ == '*':                      # the whole layer's placement as a semantic transform (plan scale)
            whole = (k, tx, ty)
    if whole is not None:
        k, tx, ty = whole
        return InstanceTransform(T.scale * k, T.scale * tx + T.tx, T.scale * ty + T.ty)
    return T


def _to_design(T, X):
    """A world point → the design coordinates of geometry placed by T."""
    k = T.scale if abs(T.scale) > 1e-12 else 1.0
    return ((X[0] - T.tx) / k, (X[1] - T.ty) / k)


def plumb_track(insts, layers, source, xy0, k0) -> dict:
    """THE plumb anchoring (every opening kind): the world anchor X0 =
    placement_k0(xy0); per layer k in `layers` → (q, width factor): q = the
    anchor in that layer's design coordinates, width factor = 1 / the layer's
    scale (widths are physical)."""
    X0 = wall_placement(insts[k0], source).apply(*xy0)
    return {k: (_to_design(wall_placement(insts[k], source), X0), 1.0 / (insts[k].transform.scale or 1.0))
            for k in layers}


def _edge_frame(t, q):
    """(position along a straight wall target, signed distance across it) of a design point."""
    L = t.length or 1.0
    ux, uy = (t.b[0] - t.a[0]) / L, (t.b[1] - t.a[1]) / L
    dx, dy = q[0] - t.a[0], q[1] - t.a[1]
    return dx * ux + dy * uy, dx * uy - dy * ux


def _plumb_misfit(at, off, width, t_len, thickness, bead, closed=False):
    """Why a plumb opening cannot be cut on this layer ('' = it can): its
    vertical line has left the wall completely (|off| ≥ thickness), or along
    the wall it would leave the jambs."""
    if abs(off) >= max(thickness, 1e-9):
        return f'the wall has moved {abs(off):.2f} in away from the opening\'s vertical line (wall {thickness:g} in)'
    jamb = 0.5 * thickness + JAMB_BEADS * bead
    if not closed and (at - width / 2 < jamb - 1e-9 or at + width / 2 > t_len - jamb + 1e-9):
        return 'the wall has slid along it so far that the opening would leave the wall (its jambs)'
    return ''


def _plumb_member(m, t, source, insts, bead, res, pre):
    """An Assembly opening (array clone, conversion closure) made PLUMB via
    plumb_track: per-layer positions + design widths. If it cannot stay
    plumb on one of its layers it follows the wall (reported)."""
    act = [k for k, w in enumerate(m['widths']) if w > 0 and k not in m['skip']]
    if not act:
        return
    k0 = m.get('anchor', act[0])
    L = t.length or 1.0
    ux, uy = (t.b[0] - t.a[0]) / L, (t.b[1] - t.a[1]) / L
    track = plumb_track(insts, range(len(insts)), source, (t.a[0] + ux * m['pos'], t.a[1] + uy * m['pos']), k0)
    at = [_edge_frame(t, track[k][0])[0] for k in range(len(insts))]
    for k in act:
        tk = t
        a_, off = _edge_frame(tk, track[k][0])
        why = _plumb_misfit(a_, off, m['widths'][k] * track[k][1], tk.length, tk.thickness, bead)
        if why:
            res.warnings.append(f'{pre}cannot stay vertical (plumb): at layer {k + 1} (z {insts[k].z_bottom:g}) {why} — '
                                'it follows the wall instead')
            return
    m['at'], m['anchor'] = at, k0
    m['widths'] = [w * track[k][1] if w > 0 and k not in m['skip'] else w for k, w in enumerate(m['widths'])]


def _poly_project(pts, q):
    """(arc length of the foot of q on an open polyline [[x, y]…], distance)."""
    best, acc = (math.inf, 0.0), 0.0
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        L = math.sqrt(L2)
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
        dd = math.dist((a[0] + t * dx, a[1] + t * dy), q)
        if dd < best[0] - 1e-12:
            best = (dd, acc + t * L)
        acc += L
    return best[1], best[0]


def _align_designer_openings(insts, openings, targets, sources, bead, per_layer, results) -> dict:
    """PLUMB DESIGNER OPENINGS (the global setting): every Designer opening
    of the stack, per run of contiguous layers whose design has it, anchored
    at the run's first layer (plumb_track); every layer of the run gets a
    ('move', source, opening id, x, y, profile, width) op and the Designer
    re-places its own opening record there (profile_cuts 'move': projected
    onto the processed source polyline — curved walls included). A run that
    cannot stay plumb (_plumb_misfit: straight → its edge; curved → the
    distance to the transformed centre line, an open wall's ends) follows
    the wall and is reported. Returns {(layer, opening id): (at, width)} of
    the straight-edge ones (the Assembly's spacing check)."""
    moves = {}
    ids = sorted({str(o['id']) for k in range(len(insts)) for o in openings.get(insts[k].design_id) or ()})
    for oid in ids:
        res = ProfileResult(f'align:{oid}', 'alignment')
        results.append(res)

        def rec(k):
            return next((o for o in openings.get(insts[k].design_id) or () if str(o.get('id')) == oid), None)
        ks = [k for k in range(len(insts)) if rec(k) is not None]
        runs, cur = [], [ks[0]]
        for k in ks[1:]:
            if k == cur[-1] + 1:
                cur.append(k)
            else:
                runs.append(cur)
                cur = [k]
        runs.append(cur)
        for run in runs:
            k0 = run[0]
            o0 = rec(k0)
            name = o0.get('label') or oid
            if o0.get('xy') is None:
                res.warnings.append(f'Designer opening {name}: its position on the wall is unknown — it follows the wall')
                continue
            track = plumb_track(insts, run, o0['source'], o0['xy'], k0)
            ops, layer_moves, why = [], {}, ''
            for k in run:
                o = rec(k)
                q, f = track[k]
                w = float(o['width']) * f
                th = float(o.get('thickness') or 0.0)
                key = o['source'] if o.get('edge') is None else f"{o['source']}#{o['edge']}"
                t = (targets.get(insts[k].design_id) or {}).get(key) if o.get('at') is not None else None
                if t is not None:
                    a_, off = _edge_frame(t, q)
                    why = _plumb_misfit(a_, off, w, t.length, th or t.thickness, bead)
                    layer_moves[k] = (a_, w)
                else:
                    rs = next((r for r in sources.get(insts[k].design_id) or () if r.get('source') == o['source']),
                              None)
                    pts = list((rs or {}).get('points') or ())
                    closed = bool((rs or {}).get('closed'))
                    if closed and pts:
                        pts = pts + [pts[0]]
                    if len(pts) >= 2:
                        sv, dist = _poly_project(pts, q)
                        Lp = sum(math.dist(a_, b_) for a_, b_ in zip(pts, pts[1:]))
                        why = _plumb_misfit(sv, dist, w, Lp, th or 2 * bead, bead, closed)
                if why:
                    why = f'at layer {k + 1} (z {insts[k].z_bottom:g}) {why}'
                    break
                ops.append((k, ('move', o['source'], oid, round(q[0], 6), round(q[1], 6), f'align:{oid}', _r(w))))
            if why:
                res.warnings.append(f'Designer opening {name} cannot stay vertical (plumb) on layers {run[0] + 1}–'
                                    f'{run[-1] + 1}: {why} — it follows the wall there')
                continue
            for k, op in ops:
                per_layer[k].append(op)
                res.layers.append({'layer': k, 'z': round(_mid(insts[k]), 4), 'design': insts[k].design_id,
                                   'point': [op[3], op[4]], 'width': op[6],
                                   **({'at': round(layer_moves[k][0], 4)} if k in layer_moves else {})})
            for k, v in layer_moves.items():
                moves[(k, oid)] = v
            X0 = wall_placement(insts[k0], o0['source']).apply(*o0['xy'])
            res.members.append({'anchor_layer': k0, 'anchor': [round(X0[0], 4), round(X0[1], 4)], 'label': name})
        res.valid = True
    return moves


def _at(m, k):
    """A member's position (along its wall, design inches) at layer k."""
    a = m.get('at')
    return m['pos'] if a is None else a[k]


def _resolve_members(p, insts, tgs, bead, h, res, openings, alignment='follow', designer_moves=None):
    """Every opening of the profile (the master, its array clones, or the
    closure of a converted Designer opening): [{'pos', 'zb', 'widths',
    'skip' (layers NOT cut here: the Designer's own opening), 'label'}];
    validation of each one into `res`. Returns (members, p) — p with the
    converted opening's position / width / Z read from the Designer, an
    older arch completed (opening_shape.fill).

    The parameters ARE the shape (2026-10-10): resolved and validated as
    they are, never extended or resized here — the UI keeps them printable
    (opening_shape.constrain); a set that is not printable is reported with
    the width / height that would be, and not generated."""
    wmin, lh = _wmin(bead, h), _lh(insts)
    members = []
    if p.source_opening:
        cv = _conversion(p, insts, tgs, openings, res)
        if cv is None:
            return [], p
        k0, k1, o = cv
        zb, spring = insts[k0].z_bottom, insts[k1].z_top
        p = replace(p, position=float(o['at']), width=float(o['width']), z_bottom=zb)
        cmin = OS.min_upper_fixed(p.width, p.upper, spring, lh, h, bead)
        if cmin is None:
            raise ProfileError(f'no self-supporting {_NAMES[p.upper]} closure for a {p.width:g} in clear width')
        c = cmin
        if p.upper_height is not None:
            if p.upper_height < cmin - 1e-6:
                res.errors.append(f'an upper height of {p.upper_height:g} in is lower than the self-supporting '
                                  f'{_NAMES[p.upper]} closure needs ({cmin:.2f} in)')
            c = max(cmin, p.upper_height)
        # the closure needs WALL above the rectangle: layers that have the wall
        # and are not the Designer opening again
        avail, k = 0.0, k1 + 1
        while k < len(insts) and insts[k].design_id in tgs and \
                not any(x.get('id') == o['id'] for x in openings.get(insts[k].design_id) or ()):
            avail = insts[k].z_top - spring
            k += 1
        need = 0.0                                  # the top of the last layer the closure cuts
        k = int(math.floor(spring / lh + 1e-9))
        while (k + 0.5) * lh < spring + c:
            need = (k + 1) * lh - spring
            k += 1
        p = replace(p, height=round(spring - zb + c, 6), lower='flat', bottom_depth=None, middle=True,
                    middle_height=round(spring - zb, 6), upper_height=c)
        res.conversion = {'opening': o['id'], 'rect_bottom': round(zb, 4), 'rect_top': round(spring, 4),
                          'layers': [k0, k1], 'upper_height': round(c, 4), 'min_upper_height': round(cmin, 4),
                          'required': round(need, 4), 'available': round(avail, 4),
                          'position': p.position, 'width': p.width}
        if need > avail + 1e-6:
            res.errors.append(f'not enough wall above the opening: its self-supporting {_NAMES[p.upper]} closure '
                              f'({p.width:g} in clear width) needs {need:.2f} in of wall above z {spring:g}, '
                              f'only {avail:.2f} in available — add {need - avail:.2f} in of wall above it '
                              '(the doorway is never shortened)')
            res.hint = {'required_height': round(need, 4), 'available': round(avail, 4),
                        'missing': round(need - avail, 4)}
        widths = _shape_widths(p, zb, insts, tgs, wmin)
        res.geometry = {'upper_height': round(c, 4), 'min_upper_height': round(cmin, 4), 'lower_depth': 0.0,
                        'middle': round(spring - zb, 4), 'spring': round(spring - zb, 4), 'height': round(p.height, 4)}
        members.append({'pos': p.position, 'zb': zb, 'widths': widths, 'skip': set(range(k0, k1 + 1)),
                        'label': 'opening', 'top_only': True})
        if alignment == 'plumb' and (k0, str(o['id'])) in (designer_moves or {}):
            # the closure above a PLUMB Designer opening is plumb with it (anchored
            # where its rectangle starts, as the rectangle is)
            members[-1]['anchor'] = k0
            _plumb_member(members[-1], tgs.get(insts[k0].design_id), p.wall.source, insts, bead, res, '')
    else:
        p = p.completed(lh, h, bead)
        sd = p.shape_dict()
        ev = OS.evaluate(sd, lh, h, bead)
        if not ev['ok']:
            res.errors.append(_shape_error(p, ev, h))
            lim = OS.limits(sd, lh, h, bead)
            res.hint = {k: v for k, v in (('min_height', lim['min_height']), ('max_width', lim['max_width']))
                        if v is not None}
            if res.hint:
                res.hint['field'] = 'upper_height'
        res.geometry = {'lower_depth': round(ev['d'], 4), 'middle': round(ev['m'], 4),
                        'upper_height': round(ev['U'], 4), 'spring': round(ev['d'] + ev['m'], 4),
                        'height': round(ev['H'], 4)}
        mems = p.members()
        for j, (pos, zb) in enumerate(mems):
            members.append({'pos': pos, 'zb': zb, 'widths': _shape_widths(p, zb, insts, tgs, wmin),
                            'skip': set(), 'label': f'#{j + 1}' if len(mems) > 1 else 'opening',
                            'header': p.upper == 'header'})
            if alignment == 'plumb':
                first = next((k for k, w in enumerate(members[-1]['widths']) if w > 0), None)
                t0 = tgs.get(insts[first].design_id) if first is not None else None
                if t0 is not None:
                    lab = members[-1]['label']
                    _plumb_member(members[-1], t0, p.wall.source, insts, bead, res,
                                  '' if lab == 'opening' else f'opening {lab}: ')
        if p.lower != 'flat':
            # the LOWER profile is not a closure (nothing hangs over its void),
            # but its jamb CAPS move outward with it: where a layer widens the
            # cut by more than 2 h (a round bottom's first layers) a cap there
            # has nothing under it if the wall has a cavity — the support check
            # reports it; said here so the reason is clear
            fast = sorted({k for m in members for k in range(len(insts))
                           if m['widths'][k] - (m['widths'][k - 1] if k else 0.0) > 2 * h + 1e-9})
            if fast:
                res.warnings.append(f'the {_NAMES[p.lower]} bottom widens by more than 2 × {h:g} in per layer at its '
                                    f'first layer{"s" if len(fast) > 1 else ""}: in a wall with a cavity (hollow, skins) '
                                    'the jamb caps there are not supported — see Support (an angular bottom, or a '
                                    'deeper one, avoids it)')
    # per member — fits the wall, every transition ≤ h, closed below the stack top
    for m in members:
        _member_checks(p, m, insts, tgs, bead, h, res, warn_fit=bool(p.source_opening))
    res.members = [dict({'position': m['pos'], 'z_bottom': m['zb']},
                        **({'anchor_layer': m['anchor']} if m.get('at') is not None else {})) for m in members]
    return members, p


def _member_checks(p, m, insts, tgs, bead, h, res, warn_fit=False):
    widths, lab = m['widths'], m['label']
    pre = '' if lab == 'opening' else f'opening {lab}: '
    for d, t in tgs.items():                        # it must fit the wall, jambs kept
        jamb = 0.5 * t.thickness + JAMB_BEADS * bead
        if m['pos'] - p.width / 2 < jamb - 1e-9 or m['pos'] + p.width / 2 > t.length - jamb + 1e-9:
            msg = (f'{pre}does not fit the wall ({t.length:.2f} in): keep {jamb:.2f} in of wall beside it '
                   f'(centre {jamb + p.width / 2:.2f}–{t.length - jamb - p.width / 2:.2f} in)')
            if msg not in res.errors and msg not in res.warnings:
                (res.warnings if warn_fit else res.errors).append(msg)
            break
    if m.get('at') is not None:
        # PLUMB: on every layer it cuts, the (moved) opening must still lie on
        # the transformed wall with its jambs — else it no longer intersects
        # the wall properly: reported, never cut
        for k, w in enumerate(widths):
            t = tgs.get(insts[k].design_id)
            if w <= 0 or k in m['skip'] or t is None:
                continue
            jamb = 0.5 * t.thickness + JAMB_BEADS * bead
            a = m['at'][k]
            if a - w / 2 < jamb - 1e-9 or a + w / 2 > t.length - jamb + 1e-9:
                msg = (f'{pre}vertical (plumb): at layer {k + 1} (z {insts[k].z_bottom:g}) it no longer lies on the wall '
                       f'with {jamb:.2f} in of jamb each side')
                if msg not in res.errors:
                    res.errors.append(msg)
                break
    if m.get('at') is not None:                     # PLUMB: design widths × scale = the world (intended) widths
        widths = [w * (insts[k].transform.scale or 1.0) for k, w in enumerate(widths)]
    worst, where = 0.0, None
    for k in range(1, len(insts)):
        if insts[k - 1].design_id not in tgs or insts[k].design_id not in tgs:
            continue
        lo, up = widths[k - 1], widths[k]
        if m.get('header') and up <= 0.0:
            continue                                # the flat top: the layer above rests on the header
        if up < lo and 0.5 * (lo - up) > worst + 1e-12:
            worst, where = 0.5 * (lo - up), k
    res.worst_step = max(res.worst_step, worst)
    if worst > h + 1e-9:
        msg = (f'{pre}not self-supporting: a side steps {worst:.2f} in over the opening at layer '
               f'{where + 1} (maximum overhang {h:g} in)')
        if not any(e.startswith(pre + 'not self-supporting') or e.startswith('not self-supporting') for e in res.errors):
            res.errors.append(msg)
    top = insts[-1].z_top if insts else 0.0
    if not p.source_opening and m['zb'] + p.height > top + 1e-6:
        msg = (f'{pre}reaches z {m["zb"] + p.height:g}, above the top of the stack (z {top:g}): '
               + ('nothing would rest on its header' if m.get('header') else 'its closure would not be printed')
               + ' — lower it or reduce its height')
        if msg not in res.errors:
            res.errors.append(msg)
    elif m.get('header'):
        last = max((k for k, w in enumerate(widths) if w > 0), default=None)
        if last is not None and (last + 1 >= len(insts) or insts[last + 1].design_id not in tgs):
            msg = (f'{pre}no wall above its flat top (layer {last + 2}) to rest on its header — '
                   'lower it or reduce its height')
            if msg not in res.errors:
                res.errors.append(msg)


def _opening_headers(p, res, members, insts, tgs, bead):
    """The HEADERS of a valid Header-upper opening profile: one per opening
    (array clones included), anchored at the boundary above its last cut
    layer — the layer resting on it — in that layer's LOCAL coordinates
    (centred on the opening, along the wall, span = the clear width), with
    the profile's header settings. Derived at every resolve: never stored,
    so they cannot duplicate or outlive their opening."""
    from .objects import Header
    out = []
    first = {}
    for i in insts:
        first.setdefault(i.section_index, i.index)
    for j, m in enumerate(members):
        last = max((k for k, w in enumerate(m['widths']) if w > 0), default=None)
        if last is None or last + 1 >= len(insts):
            continue
        inst = insts[last + 1]
        t = tgs.get(inst.design_id)
        if t is None or t.length <= 0:
            continue
        ux, uy = (t.b[0] - t.a[0]) / t.length, (t.b[1] - t.a[1]) / t.length
        hs = p.header_settings(t.thickness, bead)
        # (local units of the WALL's frame: its transform group's placement in
        # 'multiple' mode — the frame the header's x / y are in)
        sc = wall_placement(inst, p.wall.source).scale or 1.0
        hid = f'{p.id}-header' + (f'-{j + 1}' if len(members) > 1 else '')
        out.append(Header(hid, inst.section_index, inst.index - first[inst.section_index],
                          t.a[0] + ux * _at(m, last + 1), t.a[1] + uy * _at(m, last + 1), math.degrees(math.atan2(uy, ux)),
                          p.width / sc, hs['bearing'], hs['depth'], hs['thickness'], snap=True,
                          profile=p.id, member=j))
    res.headers = [h.to_dict() for h in out]
    return out


def profile_headers(results) -> list:
    """Every header generated by the opening profiles (Header objects)."""
    from .objects import Header
    return [Header.from_dict(d) for r in results for d in (r.headers or [])]


def _check_spacing(entries, insts, tgs_of, bead, name):
    """Openings sharing a wall (an array's clones, other profiles): on every
    layer no two overlap and at least the pier minimum of wall stays between
    them; and two openings that never share a layer have at least ONE solid
    layer between them vertically (else they print as one void and neither
    closes). entries: [(k, lo, hi, owner)]. Returns [((owner a, owner b),
    message)] — one message per pair, at its worst layer (the profile of the
    later owner reports it; BOTH openings are marked)."""
    by = {}
    for k, lo, hi, ow in entries:
        by.setdefault(k, []).append((lo, hi, ow))
    worst = {}                                          # pair → (gap, layer, pier)
    shared, vert = set(), {}
    for k in sorted(by):
        row = sorted(by[k])
        t = tgs_of(insts[k].design_id)
        pier = max(MIN_PIER_BEADS * bead, t.min_length if t else 0.0)
        for j, (lo0, hi0, a) in enumerate(row):
            for lo1, hi1, b in row[j + 1:]:
                if a == b or (a[0] == -1 and b[0] == -1):      # (two Designer openings: the Designer's own business)
                    continue
                pr = (min(a, b), max(a, b))
                shared.add(pr)
                gap = lo1 - hi0
                if pr not in worst or gap < worst[pr][0]:
                    worst[pr] = (gap, k, pier)
        for lo0, hi0, a in by.get(k + 1, ()):
            for lo1, hi1, b in row:
                pr = (min(a, b), max(a, b))
                if a != b and not (a[0] == -1 and b[0] == -1) and lo0 < hi1 - 1e-9 and lo1 < hi0 - 1e-9 and pr not in vert:
                    vert[pr] = k
    out = []
    for pr, (gap, k, pier) in sorted(worst.items()):
        if gap < -1e-9:
            out.append((pr, f'{name(pr[0])} and {name(pr[1])} overlap (layer {k + 1}, z {insts[k].z_bottom:g})'))
        elif gap < pier - 1e-9:
            out.append((pr, f'only {gap:.2f} in of wall between {name(pr[0])} and {name(pr[1])} at layer {k + 1} '
                               f'— needs ≥ {pier:.2f} in (increase the horizontal spacing)'))
    for pr, k in sorted(vert.items()):
        if pr not in shared:
            out.append((pr, f'no solid layer between {name(pr[0])} and {name(pr[1])} (layers {k + 1}–{k + 2}) '
                               '— increase the vertical spacing'))
    return out


def _resolve_extent(p: ExtentProfile, insts, tgs, bead, h, res: ProfileResult):
    if not p.knots:
        res.errors.append('no cut points defined')
        return [None] * len(insts)
    for d, t in tgs.items():
        if not t.taper:
            res.errors.append('only a whole straight OPEN wall can be tapered (V1)')
            return [None] * len(insts)
    out, clamped = [], False
    for inst in insts:
        t = tgs.get(inst.design_id)
        c = p.cut_at(_mid(inst)) if t is not None else None
        r = None
        if c is not None:
            if c < -1e-9 or c > t.length + 1e-9:
                clamped = True
            r = p.retained_at(_mid(inst), t.length)
            rmin = max(MIN_RETAINED_BEADS * bead, t.min_length)
            if r < rmin - 1e-9:
                r = 0.0
        out.append(r)
    if clamped:
        res.warnings.append('the cut line passes beyond the wall there (the whole wall is kept / gone)')
    # PRINTABILITY OF THE RESULT (direction-free): each layer's KEPT stretch
    # of the wall, as an interval along it, against the layer below — an end
    # may retreat freely; where the kept stretch reaches past the stretch
    # below (at EITHER end, or appears where the layer below has none of the
    # wall) it overhangs by that much.
    def kept(t, r):
        if r is None:
            return (0.0, t.length)                  # unaffected: the whole wall
        if r <= 1e-9:
            return None                             # absent
        return (0.0, r) if p.keep == 'start' else (t.length - r, t.length)
    worst, where = 0.0, None
    for k in range(1, len(insts)):
        t_lo, t_up = tgs.get(insts[k - 1].design_id), tgs.get(insts[k].design_id)
        if t_lo is None or t_up is None:
            continue
        lo, up = kept(t_lo, out[k - 1]), kept(t_up, out[k])
        if up is None:
            continue
        step = (up[1] - up[0]) if lo is None else max(0.0, lo[0] - up[0], up[1] - lo[1])
        if step > worst + 1e-12:
            worst, where = step, k
    res.worst_step = worst
    if worst > h + 1e-9:
        res.errors.append(f'the kept wall overhangs the layer below by {worst:.2f} in at layer {where + 1} '
                          f'(maximum overhang {h:g} in): the kept side grows outward as it rises — '
                          'keep the other side, or mirror the cut')
    return out


def apply_profiles(resolved, profiles, targets, bead, max_overhang, sources=None, openings=None, alignment=None):
    """Attach each layer's CUT SPEC (LayerInstance.cuts) and report every
    profile. targets: {design id: {wall key: WallTarget}} (LayerSource.walls);
    sources: {design id: [source record]} (LayerSource.sources — every wall a
    planar cut can select); openings: {design id: [Designer opening record]}
    (LayerSource.openings — what a conversion refers to). Invalid profiles
    cut nothing. Returns (resolved, [ProfileResult])."""
    insts = resolved.instances
    alignment = 'plumb' if alignment == 'plumb' else 'follow'
    if (not profiles and alignment == 'follow') or not insts:
        return resolved, []
    h = max(0.0, min(float(max_overhang), float(bead)))
    per_layer = [[] for _ in insts]
    results = []
    # OPENING ALIGNMENT (global, 2026-10-10): plumb Designer openings moved per layer
    designer_moves = _align_designer_openings(insts, openings or {}, targets, sources or {}, bead, per_layer,
                                              results) if alignment == 'plumb' else {}
    seen = set()
    pending = []                                       # openings: validated together, then emitted
    for p in profiles:
        res = ProfileResult(p.id, p.kind)
        results.append(res)
        if p.id in seen:
            res.valid = False
            res.errors.append('duplicate profile id')
            continue
        seen.add(p.id)
        if p.kind == 'plane':
            _resolve_plane(p, insts, sources or {}, per_layer, res)
            continue
        tgs = _targets_for(p, insts, targets)
        if not tgs:
            res.valid = False
            res.errors.append(f'wall {p.wall.key} is not a straight wall of any design in the stack '
                              '(V1: straight walls only)')
            continue
        res.wall = next(iter(tgs.values())).to_dict()
        missing = [d for d in dict.fromkeys(i.design_id for i in insts) if d not in tgs]
        if p.kind == 'opening':
            try:
                members, p = _resolve_members(p, insts, tgs, bead, h, res, openings or {}, alignment, designer_moves)
            except ProfileError as e:
                res.errors.append(str(e))
                members = []
            lo_z = min((m['zb'] for m in members), default=p.z_bottom)
            hi_z = max((m['zb'] for m in members), default=p.z_bottom) + p.height
            missing = [d for d in missing if any(i.design_id == d and lo_z < i.z_top and i.z_bottom < hi_z
                                                 for i in insts)]
            if missing:
                res.warnings.append('the wall is absent from design(s) ' + ', '.join(missing) +
                                    ' — those layers are not affected')
            res.valid = not res.errors and bool(members)
            pending.append((p, res, tgs, members))
            continue
        else:
            if p.keep == 'auto':
                # AUTO: the cut line is the boundary, the kept side is the one
                # whose result is SUPPORTED (it retreats as it rises) —
                # judged on the result, never on a preferred diagonal or the
                # wall's drawing direction; both supported → the attached end
                tries = []
                for k_ in ('start', 'end'):
                    r_ = ProfileResult(p.id, p.kind)
                    v_ = _resolve_extent(replace(p, keep=k_), insts, tgs, bead, h, r_)
                    att = k_ in (next(iter(tgs.values())).attached or ())
                    tries.append((bool(r_.errors), r_.worst_step, not att, k_ != 'start', k_, r_, v_))
                tries.sort(key=lambda x: x[:4])
                _, _, _, _, k_, r_, vals = tries[0]
                if r_.errors and all(t_[0] for t_ in tries) and all('overhangs' in e for t_ in tries for e in t_[5].errors):
                    r_.errors = [f'neither side of this cut line is supported: each grows outward as it rises '
                                 f'(least overhang {r_.worst_step:.2f} in, maximum {h:g} in) — make the cut less steep']
                res.errors += r_.errors
                res.warnings += r_.warnings
                res.worst_step = r_.worst_step
                p = replace(p, keep=k_)
            else:
                vals = _resolve_extent(p, insts, tgs, bead, h, res)
            res.keep = p.keep
            missing = [d for d in missing if any(i.design_id == d and p.cut_at(_mid(i)) is not None
                                                 for i in insts)]
        if missing:
            res.warnings.append('the wall is absent from design(s) ' + ', '.join(missing) +
                                ' — those layers are not affected')
        res.valid = not res.errors
        for k, (inst, v) in enumerate(zip(insts, vals)):
            if v is None:
                continue
            L_ = tgs[inst.design_id].length            # the cut position, along the wall from its start
            res.layers.append({'layer': k, 'z': round(_mid(inst), 4), 'design': inst.design_id,
                               'retained': round(v, 4), 'cut': round(v if p.keep == 'start' else L_ - v, 4)})
            if res.valid:
                per_layer[k].append(('extent', p.wall.source, p.wall.edge, p.end, _r(v), p.id))
    _emit_openings(pending, insts, per_layer, bead, h, profiles, sources or {}, openings or {}, designer_moves)
    new = [replace(i, cuts=tuple(sorted(c, key=_cut_sort))) if c else i for i, c in zip(insts, per_layer)]
    return replace(resolved, instances=new), results


def _emit_openings(pending, insts, per_layer, bead, h, profiles, sources, openings=None, designer_moves=None):
    """The OPENING profiles of the stack, together: spacing between every
    two openings on a wall (an array's clones, other profiles and the
    Designer's own openings of each layer alike — a profile that overlaps or
    crowds one is reported, never resized), then per valid profile its cut
    layers, lattice STATIONS and cross-Z TRANSITIONS."""
    order = {res.id: j for j, (_, res, _, _) in enumerate(pending)}
    by_wall = {}
    for p, res, tgs, members in pending:
        if not members:
            continue
        for j, m in enumerate(members):
            for k, w in enumerate(m['widths']):
                if w > 0:
                    by_wall.setdefault(p.wall.key, []).append(
                        (k, _at(m, k) - w / 2, _at(m, k) + w / 2, (order[res.id], j)))
    # the Designer's own openings on those walls (fixed: owner (-1, id)); a
    # converted one is its conversion's own rectangle — not a neighbour
    converted = {(p.wall.key, p.source_opening['id']) for p, _, _, _ in pending if p.source_opening}
    walls = {p.wall.key: p.wall for p, _, _, members in pending if members}
    dlabels = {}
    for k, inst in enumerate(insts):
        for o in (openings or {}).get(inst.design_id) or ():
            if o.get('at') is None:
                continue
            for wk, w in walls.items():
                if o.get('source') == w.source and o.get('edge') == w.edge and (wk, o['id']) not in converted:
                    a, ww = float(o['at']), float(o['width'])
                    mv = (designer_moves or {}).get((k, str(o['id'])))
                    if mv is not None:                  # a PLUMB Designer opening: where it is cut at this layer
                        a, ww = mv
                    by_wall.setdefault(wk, []).append((k, a - ww / 2, a + ww / 2, (-1, str(o['id']))))
                    dlabels[(-1, str(o['id']))] = f'Designer opening {o.get("label") or o["id"]}'
    labels = dict(dlabels)
    for p, res, tgs, members in pending:
        for j, m in enumerate(members):
            n = len(members)
            labels[(order[res.id], j)] = (f'opening {m["label"]} of {p.id}' if n > 1 else f'opening {p.id}')
    tg_any = {}
    for p, res, tgs, members in pending:
        for d, t in tgs.items():
            tg_any.setdefault(p.wall.key, {})[d] = t
    marks = {}                                          # owner → its own spacing messages
    for wk, entries in by_wall.items():
        for pr, msg in _check_spacing(entries, insts, lambda d, wk=wk: tg_any[wk].get(d), bead,
                                      lambda o: labels.get(o, str(o))):
            res = pending[pr[1][0]][1]
            if msg not in res.errors:
                res.errors.append(msg)
                res.valid = False
            for ow in pr:
                if ow[0] == pr[1][0]:                   # (the reporting profile's openings)
                    marks.setdefault(ow, []).append(msg)
    # EVERY opening of a profile judged alike (an array: the master and each
    # clone): its own errors — its member checks, its spacing pairs — and the
    # profile's shared ones (the shape, the wall); reported per member so the
    # page marks exactly the openings concerned, never only the master
    for p, res, tgs, members in pending:
        if not members or not res.members:
            continue
        own = {j: (f'opening {m["label"]}:', f'opening {m["label"]} ') for j, m in enumerate(members)
               if m['label'] != 'opening'}
        shared = [e for e in res.errors if not any(e.startswith(a) or a2 in e for a, a2 in own.values())
                  and not any(e in v for v in marks.values())]
        for j, m in enumerate(members):
            a = own.get(j)
            mine = [e for e in res.errors if a and e.startswith(a[0])] + marks.get((order[res.id], j), [])
            if j < len(res.members):
                res.members[j]['errors'] = list(dict.fromkeys(shared + mine))
    planes = [q for q in profiles if q.kind == 'plane']
    cut_iv = {}                                         # (layer, wall) → [(lo, hi)] of VALID openings
    for p, res, tgs, members in pending:
        if res.valid:
            for m in members:
                for k, w in enumerate(m['widths']):
                    if w > 0:
                        cut_iv.setdefault((k, p.wall.key), []).append((_at(m, k) - w / 2, _at(m, k) + w / 2))
    for p, res, tgs, members in pending:
        w = p.wall
        for j, m in enumerate(members):
            for k, v in enumerate(m['widths']):
                if v > 0:
                    e = {'layer': k, 'z': round(_mid(insts[k]), 4), 'design': insts[k].design_id,
                         'width': round(v, 4), 'at': round(_at(m, k), 4)}
                    if len(members) > 1:
                        e['member'] = j
                    if k in m['skip']:
                        e['designer'] = True                # the Designer's own opening: not cut here
                    res.layers.append(e)
        res.layers.sort(key=lambda e: (e['layer'], e['at']))
        if not res.valid:
            continue
        if p.upper == 'header' and not p.source_opening:
            _opening_headers(p, res, members, insts, tgs, bead)
        # LATTICE STATIONS on EVERY layer of the designs that have the wall
        # (cut or not), one per distinct opening position: the Designer keeps
        # ONE lattice reference for them all — doubling a single-pass run the
        # openings cross, as a Door Gap derived design does for its Base — so
        # the cut layers stay a LOCAL change (layer_design._station_reference)
        # (a converted Designer opening's own layers stay the authored design)
        authored = set().union(*(m['skip'] for m in members))
        for pos in _station_positions(members, insts, tgs, bead):
            for k, inst in enumerate(insts):
                if inst.design_id in tgs and k not in authored:
                    per_layer[k].append(('station', w.source, w.edge, pos, 0.0, p.id))
        for m in members:
            for k, v in enumerate(m['widths']):
                if v > 0 and k not in m['skip']:
                    per_layer[k].append(('opening', w.source, w.edge, _r(_at(m, k)), _r(v), p.id))
            wts = [0.0 if k in m['skip'] else v for k, v in enumerate(m['widths'])] if m.get('top_only') else m['widths']
            last = max((k for k, v in enumerate(m['widths']) if v > 0), default=-1)
            for k, w_, al in _transition_layers(insts, wts, tgs, bead, h):
                if m.get('top_only') and k < last:
                    continue                            # (below a converted rectangle: authored, untouched)
                th = (tgs.get(insts[k].design_id).thickness if tgs.get(insts[k].design_id) else 0.0)
                if any(lo - th < _at(m, k) + w_ / 2 and _at(m, k) - w_ / 2 < hi + th
                       for lo, hi in cut_iv.get((k, w.key), ())):
                    continue                            # another opening is cut there: no pre-deformation
                per_layer[k].append(('transition', w.source, w.edge, _r(_at(m, k)), _r(w_), p.id, _r(al)))
        for q in planes:                                # a planar top cut on the same wall
            if any(w.source in {r['source'] for r in plane_selection(q, recs)} for recs in sources.values()):
                res.warnings.append(f'planar top cut {q.id} also cuts this wall: where it removes the wall above '
                                    'an opening, that opening is open at the top (no closure)')


def _station_positions(members, insts, tgs, bead) -> list:
    """The lattice STATIONS of a profile's openings: one per distinct
    position — a PLUMB opening moves along its wall from layer to layer, so
    each of its cut positions counts, merged when closer than one bead."""
    ps = set()
    for m in members:
        if m.get('at') is None:
            ps.add(_r(m['pos']))
        else:
            ps |= {_r(m['at'][k]) for k, w in enumerate(m['widths']) if w > 0 and k not in m['skip']}
    out = []
    for x in sorted(ps):
        if not out or x - out[-1] >= bead - 1e-9:
            out.append(x)
    return out


def plane_selection(p, recs) -> list:
    """The selected source records of one design (Wall System members
    resolved from the design's own membership)."""
    ids = set(p.sources) | {r['source'] for r in recs if r.get('system') in set(p.systems)}
    return [r for r in recs if r['source'] in ids - set(p.exclude)]


def _resolve_plane(p, insts, sources, per_layer, res):
    """Per layer: the plane's plan line at the layer's mid-height, mapped
    into the layer's DESIGN coordinates through its own placement (the cut
    is applied to the design before resolution), as one op for the whole
    selection — or nothing where the plane is above every selected wall of
    that layer. Printability: a plane cut only REMOVES material as Z rises
    (each layer keeps a subset of the stretch below it, the kept half-plane
    shrinks monotonically), so the result is always supported — a steep cut
    just retreats faster. A level plane removes the selection above it.
    STATIONS: every layer of a design with selected walls carries the
    plane's first cut (in that layer's design coordinates of the first cut
    layer) so a single-pass lattice run keeps one doubled reference."""
    if not (p.sources or p.systems):
        res.errors.append('no walls selected')
        res.valid = False
        return
    sel = {d: plane_selection(p, recs) for d, recs in sources.items()}
    if not any(sel.values()):
        res.warnings.append('none of the selected walls is in a design of this stack')
    spec = {'sources': list(p.sources), 'systems': list(p.systems), 'exclude': list(p.exclude), 'profile': p.id}
    firsts = {}                                        # source → its first cut (a station per selected wall)
    for k, inst in enumerate(insts):
        recs = sel.get(inst.design_id) or []
        if not recs:
            continue
        z = _mid(inst)
        T = inst.transform
        pts = [T.apply(x, y) for r in recs for x, y in r.get('points') or ()]
        if p.shape != 'linear':                        # STEPPED / CURVED: compared along the run t
            tc = p.cut_t(z)
            ts = [p.t_of(x, y) for x, y in pts] or [math.inf]
            if tc == 'all' or (tc != 'none' and tc <= min(ts) + 1e-9):
                continue                               # nothing of the selection below the cut
            above = tc == 'none' or tc > max(ts) + 1e-9
            zs = None
        else:
            zs = [p.z_at(x, y) for x, y in pts] or [math.inf]
            if z <= min(zs) + 1e-9:
                continue                               # below the plane everywhere: untouched
            above = None
        ln = p.line_at(z)
        if ln == 'all':
            continue
        if ln == 'none' or (above if above is not None else z > max(zs) + 1e-9):
            op = dict(spec, remove_all=True)
        else:
            (px, py), n = ln
            k_ = T.scale if T.scale > 1e-12 else 1.0
            # (kept to 1e-9 — the terminations must lie ON the plane, not 1e-4 off it)
            op = dict(spec, point=[round((px - T.tx) / k_, 9), round((py - T.ty) / k_, 9)],
                      normal=[round(n[0], 12), round(n[1], 12)])
        per_layer[k].append(('plane', json.dumps(op, sort_keys=True), None, None, None, p.id))
        res.layers.append({'layer': k, 'z': round(z, 4), 'design': inst.design_id,
                           'removed_all': bool(op.get('remove_all')),
                           'offset': None if ln in ('all', 'none') else (
                               round((z - p.z_anchor) / max(p.slope, 1e-12), 4) if p.shape == 'linear'
                               else round(p.cut_t(z), 4))})
        if not op.get('remove_all'):
            for r in recs:                             # the first layer whose line crosses this wall
                if r['source'] in firsts:
                    continue
                if p.shape != 'linear':
                    ts_r = [p.t_of(*T.apply(x, y)) for x, y in r.get('points') or ()]
                    crosses = bool(ts_r) and min(ts_r) < p.cut_t(z) - 1e-9 < max(ts_r)
                else:
                    zs_r = [p.z_at(*T.apply(x, y)) for x, y in r.get('points') or ()]
                    crosses = bool(zs_r) and min(zs_r) < z - 1e-9 < max(zs_r)
                if crosses:
                    firsts[r['source']] = dict(op, sources=[r['source']], systems=[], exclude=[])
    res.valid = not res.errors
    if res.valid and firsts:
        sts = [json.dumps(dict(firsts[sid], plane_station=True), sort_keys=True) for sid in sorted(firsts)]
        for k, inst in enumerate(insts):
            # (only a design that prints a Wall Infill lattice reads stations:
            # elsewhere they would only make every layer a distinct variant —
            # rebuilt whenever the plane moves — with identical geometry)
            recs = sel.get(inst.design_id) or []
            if recs and any(r.get('lattice', True) for r in recs):
                for st in sts:
                    per_layer[k].append(('station', st, None, None, 0.0, p.id))


TRANSITION_DISPLACEMENT = 1.0   # × (wall thickness − bead): the largest sideways move a generated
                                # pattern makes between the uncut layer and its cut-end motif


def _transition_layers(insts, widths, tgs, bead, h):
    """[(layer, width of the nearest cut layer, α)] — the CROSS-Z TRANSITION
    of an opening (review 4): the layers just below its first cut layer and
    just above its last one print the uncut pattern pre-deformed by α toward
    the cut layer's local end motif (no cut: Designer `cut_transitions`), so
    no layer of the stack moves by more than h from the one below. Length:
    the smallest n with D / (n + 1) ≤ h, D = the largest sideways move of the
    pattern (≈ thickness − bead); α = (n + 1 − j) / (n + 1) at j layers away.
    Farther layers are exactly canonical."""
    cut = [k for k, w in enumerate(widths) if w and w > 0]
    if not cut or h <= 0:
        return []
    out = []
    for k0, step, w0 in ((cut[0], -1, widths[cut[0]]), (cut[-1], 1, widths[cut[-1]])):
        t = tgs.get(insts[k0].design_id)
        D = TRANSITION_DISPLACEMENT * max(0.0, (t.thickness if t else 0.0) - bead)
        n = max(0, math.ceil(D / h - 1e-9) - 1)
        for j in range(1, n + 1):
            k = k0 + step * j
            if not 0 <= k < len(insts) or insts[k].design_id not in tgs or (widths[k] or 0) > 0:
                break
            out.append((k, w0, (n + 1 - j) / (n + 1)))
    return out


def _cut_sort(c):
    return json.dumps(c, default=str)


def cut_key(cuts) -> str:
    return json.dumps([list(c) for c in cuts or ()], separators=(',', ':'))


def cuts_to_dicts(cuts) -> list:
    """The cut spec as the Designer takes it (JSON)."""
    out = []
    for c in cuts or ():
        if c[0] == 'opening':
            out.append({'op': 'opening', 'source': c[1], 'edge': c[2], 'at': c[3], 'width': c[4],
                        'profile': c[5]})
        elif c[0] == 'plane':
            out.append(dict(json.loads(c[1]), op='plane'))
        elif c[0] == 'station' and isinstance(c[1], str) and c[1].startswith('{'):
            out.append({'op': 'station', 'plane': json.loads(c[1]), 'profile': c[5]})
        elif c[0] == 'station':
            out.append({'op': 'station', 'source': c[1], 'edge': c[2], 'at': c[3], 'profile': c[5]})
        elif c[0] == 'transition':
            out.append({'op': 'transition', 'source': c[1], 'edge': c[2], 'at': c[3], 'width': c[4],
                        'profile': c[5], 'alpha': c[6]})
        elif c[0] == 'move':
            out.append({'op': 'move', 'source': c[1], 'opening': c[2], 'point': [c[3], c[4]], 'profile': c[5],
                        'width': c[6]})
        else:
            out.append({'op': 'extent', 'source': c[1], 'edge': c[2], 'end': c[3], 'retained': c[4],
                        'profile': c[5]})
    return out


def cuts_from_dicts(ds) -> tuple:
    out = []
    for d in ds or ():
        if d['op'] == 'opening':
            out.append(('opening', d['source'], d.get('edge'), _r(d['at']), _r(d['width']), d.get('profile', '')))
        elif d['op'] == 'plane':
            op = {k: v for k, v in d.items() if k != 'op'}
            out.append(('plane', json.dumps(op, sort_keys=True), None, None, None, d.get('profile', '')))
        elif d['op'] == 'station' and d.get('plane') is not None:
            out.append(('station', json.dumps(d['plane'], sort_keys=True), None, None, 0.0, d.get('profile', '')))
        elif d['op'] == 'station':
            out.append(('station', d['source'], d.get('edge'), _r(d['at']), 0.0, d.get('profile', '')))
        elif d['op'] == 'transition':
            out.append(('transition', d['source'], d.get('edge'), _r(d['at']), _r(d['width']), d.get('profile', ''),
                        _r(d['alpha'])))
        elif d['op'] == 'move':
            out.append(('move', d['source'], d['opening'], round(float(d['point'][0]), 6), round(float(d['point'][1]), 6),
                        d.get('profile', ''), _r(d['width'])))
        else:
            out.append(('extent', d['source'], d.get('edge'), d.get('end', 'end'), _r(d['retained']),
                        d.get('profile', '')))
    return tuple(sorted(out, key=_cut_sort))
