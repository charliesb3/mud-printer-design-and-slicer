"""
Assembly data model and the height → layer resolution.

DESIGN INTENT is physical height: a Section says "use Layer Design X for
36 in", never "for 24 layers". The Assembly has ONE global layer height;
resolve() turns the intent into whole layers. Changing the layer height
simply re-resolves the same intent.

ROUNDING POLICY (prototype, deterministic): section BOUNDARIES are rounded,
not section lengths. Boundary k sits at the cumulative desired height
H_k = h_1 + … + h_k; its layer index is round_half_up(H_k / layer_height).
So every transition (e.g. a sill at 48 in) is within half a layer of its
intended Z, the total is within half a layer, and rounding errors never
accumulate along the stack. Each section reports desired height, layer
count, actual height and the error; a section that rounds to 0 layers is
reported (it would not appear in the print).

Each physical layer uses exactly ONE Layer Design; transitions happen
between layers. Layer instances only REFERENCE their design (by id) — a
design used for 40 layers exists once.

LAYER TRANSFORMS (Assembly operations — the Layer Design is never changed or
copied). A Section may carry a SectionTransform; every LayerInstance gets a
compact InstanceTransform, a 2D similarity p' = scale · p + (tx, ty), that
places its design's resolved geometry:
  * SHIFT: (dx, dy) inches per layer — the whole layer moves, same shape and
    size (leaning / sloped forms).
  * SCALE: `scale_in` inches per layer — a UNIFORM scale about the footprint's
    current centre so the footprint's outermost edge (along its longer bbox
    axis) moves that far per layer; positive = inward, negative = outward.
    It is deliberately NOT called inset: everything scales together (walls,
    bead spacing, lattice) — a true constant-thickness inset needs the
    Designer to re-resolve the design (see PROJECT_MEMORY.md).
  * ORDER: per layer, scale about the current footprint centre, THEN
    translate; the translation is in physical inches and is never scaled.
  * PROFILE: the amount at section-local layer k of n is m(k, n) × the
    per-layer value v (PROFILES). m(n, n) is the section's END state — one
    step past its top layer, where the next section starts.
      linear:    m = k                 every step adds exactly v (constant slope)
      quadratic: m = k (k + 1) / (2n)  step j (layer j−1 → j) adds v · j / n:
                 the first step v/n (≈ 0), growing linearly, the top layer's
                 own step v(n−1)/n and the step into the next section exactly
                 v — so v is the FINAL per-layer rate at the top. Total at the
                 top layer v(n−1)/2; end state v(n+1)/2 (vs v·n linear).
  * LOCAL vs CUMULATIVE: progression restarts at each section (k = 0 … n−1;
    k = 0 adds nothing), but each section starts from the previous section's
    END state — the transform it would reach at k = n — so slopes continue
    smoothly and a section without a transform continues straight up from
    where the stack has got to (it never snaps back to the design's origin).
    POSITION is always continuous; SLOPE is not matched: a quadratic section
    restarts at a near-zero rate even after a section that ended steep (a
    visible kink at the boundary — accepted for now).
  * ASSEMBLY-WIDE TRANSFORM (Assembly.transform, same SectionTransform): one
    progression over the WHOLE stack, global layer g = 0 … L−1 with n = L
    (never restarting at sections / design changes). It scales about the
    centre of the REFERENCE footprint F = union bbox of the designs used
    (edge of F moves v per layer), then shifts. COMPOSITION, per layer:
        geometry → A_g (assembly-wide) → section part → placement
    where the section part is the section chain E_i (above) written as
    "scale s_E about c_F, then shift τ = E_i(c_F) − c_F" and applied AFTER
    A_g about the CURRENT reference centre A_g(c_F):
        p' = A_g(c_F) + s_E (A_g(p) − A_g(c_F)) + τ
    So both shifts stay physical (neither is scaled by the other's scale),
    the scales multiply, position is continuous, and with A = identity the
    placement is exactly E_i (the section-only behaviour).
  * SCALE CENTRE (Assembly.center_mode):
      'footprint' (default) — exactly the above: sections scale about their
        design's footprint, the assembly about the union F.
      'multiple' — TRANSFORM GROUPS (Assembly.centers): user objects with a
        stable id, a pivot and an EXPLICIT membership — the SOURCE ids
        (Designer source paths / walls, opaque here) whose geometry they
        move. Connectivity is irrelevant: one connected printed mass may
        hold several groups. Membership never changes when a pivot moves;
        there is no nearest-centre rule (groups.py). For each group the SAME
        pipeline runs with its pivot and its member bbox half-size, for BOTH
        the section and the assembly-wide scale and as c_F
        (LayerInstance.parts[centre id]). Shifts are identical for all.
        SEMANTIC, NOT RUBBER-SHEET (decision 2026-10-06): a layer's groups
        are not applied to its resolved beads. Each group's placement
        RELATIVE to the layer's own, R_g = T_whole⁻¹ ∘ T_g (a uniform scale
        + translation), is handed to the Designer as a semantic transform of
        the group's SOURCES (LayerInstance.semantic → LayerSource.geometry(
        design, transforms)); the Designer moves the architecture,
        regenerates walls connecting differently moved hosts and resolves
        walls / junctions / openings / lattice. The resulting geometry
        variant is then placed by T_whole like any layer. Wall thickness is
        therefore scaled only by the layer's whole placement, never
        stretched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional


class AssemblyError(ValueError):
    pass


DEFAULT_MAX_OVERHANG = 1.5    # in: the support rule's input; min overlap = bead width − max overhang


# profile: section-local layer k of n → amount multiplier
PROFILES = {
    'linear': lambda k, n: float(k),
    'quadratic': lambda k, n: k * (k + 1) / (2.0 * n),
}


@dataclass(frozen=True)
class SectionTransform:
    """How successive layers of a section move / change in XY."""
    profile: str = 'linear'
    shift: tuple = (0.0, 0.0)          # (dx, dy) in per layer
    scale_in: float = 0.0              # outermost-edge movement per layer (in); + inward

    def is_identity(self) -> bool:
        return self.shift[0] == 0 and self.shift[1] == 0 and self.scale_in == 0

    def to_dict(self) -> dict:
        return {'profile': self.profile, 'shift': list(self.shift), 'scale_in': self.scale_in}

    @staticmethod
    def from_dict(d) -> 'SectionTransform':
        d = d or {}
        sh = d.get('shift') or (0.0, 0.0)
        return SectionTransform(d.get('profile', 'linear') or 'linear',
                                (float(sh[0]), float(sh[1])), float(d.get('scale_in', 0.0) or 0.0))


IDENTITY_TRANSFORM = SectionTransform()


@dataclass(frozen=True)
class InstanceTransform:
    """Placement of one layer instance: p' = scale · p + (tx, ty)."""
    scale: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, x, y):
        return self.scale * x + self.tx, self.scale * y + self.ty

    def then(self, scale_about, sx, sy, dx, dy) -> 'InstanceTransform':
        """This transform, then: uniform scale `scale_about` about (sx, sy),
        then translate (dx, dy)."""
        k = scale_about
        return InstanceTransform(self.scale * k, sx + k * (self.tx - sx) + dx,
                                 sy + k * (self.ty - sy) + dy)

    def to_dict(self) -> dict:
        return {'scale': self.scale, 'tx': self.tx, 'ty': self.ty}


IDENTITY = InstanceTransform()


@dataclass(frozen=True)
class Footprint:
    """A design's XY extent: centre + reference half-size (longer bbox axis)."""
    cx: float
    cy: float
    half: float

    @staticmethod
    def of_bbox(x0, y0, x1, y1) -> 'Footprint':
        return Footprint((x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) / 2)


@dataclass(frozen=True)
class TransformCenter:
    """A TRANSFORM GROUP ('multiple' mode): stable id + pivot (design
    coordinates) + EXPLICIT membership — the source ids whose geometry it
    moves (empty = a centre with no geometry yet)."""
    id: str
    x: float
    y: float
    sources: tuple = ()               # explicit membership: source (wall) ids
    label: str = ''

    def to_dict(self) -> dict:
        return {'id': self.id, 'x': self.x, 'y': self.y, 'sources': list(self.sources),
                'label': self.label}

    @staticmethod
    def from_dict(d) -> 'TransformCenter':
        return TransformCenter(str(d['id']), float(d['x']), float(d['y']),
                               tuple(str(s) for s in d.get('sources') or ()), d.get('label', '') or '')


@dataclass(frozen=True)
class Section:
    """One vertical range: a Layer Design for a desired physical height."""
    design_id: str
    height: float                      # desired physical height (in)
    label: str = ''
    transform: SectionTransform = IDENTITY_TRANSFORM


@dataclass
class Assembly:
    layer_height: float                # ONE global layer height (in)
    sections: list = field(default_factory=list)
    name: str = ''
    transform: SectionTransform = IDENTITY_TRANSFORM    # assembly-wide (whole stack)
    objects: list = field(default_factory=list)         # non-printed assembly objects (Header …)
    overrides: list = field(default_factory=list)       # support warnings deliberately ignored
    center_mode: str = 'footprint'                      # 'footprint' | 'multiple'
    centers: list = field(default_factory=list)         # [TransformCenter]
    # MAXIMUM OVERHANG (in, the user-facing physical input, 2026-10-06): how
    # far an upper bead may hang past the bead below. support.py derives the
    # required overlap per transition: o = w_upper − max_overhang (= bead
    # width − overhang for equal widths). The bead width is NOT stored here:
    # it is the Designer's MATERIAL (one project-wide value).
    max_overhang: float = DEFAULT_MAX_OVERHANG

    def to_dict(self) -> dict:
        return {'name': self.name, 'layer_height': self.layer_height,
                'sections': [{'design_id': s.design_id, 'height': s.height, 'label': s.label,
                              'transform': s.transform.to_dict()}
                             for s in self.sections],
                'transform': self.transform.to_dict(),
                'objects': [o.to_dict() for o in self.objects],
                'overrides': [o.to_dict() for o in self.overrides],
                'center_mode': self.center_mode,
                'centers': [c.to_dict() for c in self.centers],
                'max_overhang': self.max_overhang}

    @staticmethod
    def from_dict(d: dict) -> 'Assembly':
        from .objects import object_from_dict, SupportOverride
        return Assembly(float(d['layer_height']),
                        [Section(s['design_id'], float(s['height']), s.get('label', ''),
                                 SectionTransform.from_dict(s.get('transform')))
                         for s in d.get('sections', [])], d.get('name', ''),
                        SectionTransform.from_dict(d.get('transform')),
                        [object_from_dict(o) for o in d.get('objects') or []],
                        [SupportOverride.from_dict(o) for o in d.get('overrides') or []],
                        d.get('center_mode') or 'footprint',
                        [TransformCenter.from_dict(c) for c in d.get('centers') or []],
                        _max_overhang(d.get('max_overhang')))


def _max_overhang(v) -> float:
    x = DEFAULT_MAX_OVERHANG if v is None else float(v)
    if not x >= 0:
        raise AssemblyError('maximum overhang must be ≥ 0 in')
    return x




@dataclass(frozen=True)
class LayerInstance:
    """One physical layer: which design, where."""
    index: int                         # 0 = first (bottom) layer
    design_id: str
    section_index: int
    z_bottom: float
    z_top: float
    transform: InstanceTransform = IDENTITY   # places the design's geometry
    parts: dict = field(default_factory=dict, compare=False)   # transform group (centre id) →
                                                               # placement ('multiple' mode)
    semantic: tuple = field(default=(), compare=False)         # ((source, k, tx, ty), …): the
                                                               # SEMANTIC transforms of its geometry

    def transform_of(self, key) -> 'InstanceTransform':
        return self.parts.get(key, self.transform)

    @property
    def geometry_key(self) -> str:
        """Which geometry this layer prints: its design, or the design's
        semantically transformed VARIANT."""
        if not self.semantic:
            return self.design_id
        return self.design_id + '|' + ';'.join(f'{s}:{k:.12g},{x:.9g},{y:.9g}'
                                               for s, k, x, y in self.semantic)

    def transforms(self) -> dict:
        return {s: (k, x, y) for s, k, x, y in self.semantic}

    @property
    def height(self) -> float:
        return self.z_top - self.z_bottom


@dataclass(frozen=True)
class SectionResult:
    index: int
    design_id: str
    label: str
    desired_height: float
    layers: int
    actual_height: float
    first_layer: Optional[int]         # None when the section rounds to 0 layers
    z_bottom: float
    z_top: float

    @property
    def error(self) -> float:          # actual − desired
        return self.actual_height - self.desired_height


@dataclass
class ResolvedAssembly:
    layer_height: float
    sections: list                     # [SectionResult]
    instances: list                    # [LayerInstance], bottom to top
    warnings: list

    @property
    def total_layers(self) -> int:
        return len(self.instances)

    @property
    def total_height(self) -> float:
        return self.instances[-1].z_top if self.instances else 0.0

    @property
    def desired_height(self) -> float:
        return sum(s.desired_height for s in self.sections)

    def designs_used(self) -> list:
        """Distinct design ids, in order of first use."""
        return list(dict.fromkeys(i.design_id for i in self.instances))

    def report(self) -> str:
        lines = [f'layer height {self.layer_height:g} in']
        for s in self.sections:
            lines.append(f'  {s.index + 1}. {s.label or s.design_id:<14} desired {s.desired_height:7.2f} in'
                         f' → {s.layers:3d} layers = {s.actual_height:7.2f} in'
                         f' ({s.error:+.2f})  z {s.z_bottom:.2f}–{s.z_top:.2f}')
        lines.append(f'  total {self.total_layers} layers, {self.total_height:.2f} in'
                     f' (desired {self.desired_height:.2f} in)')
        lines += [f'  ! {w}' for w in self.warnings]
        return '\n'.join(lines)


def _section_transforms(entry, tf, n, fp, k, warnings) -> list:
    """Instance transforms of a section's local layers 0 … n (n = its end
    state): scale about the footprint's current centre, then shift."""
    if tf.is_identity():
        return [entry] * (n + 1)
    if tf.scale_in and fp is None:
        raise AssemblyError(f'section {k + 1}: scaling needs the design footprint')
    m = PROFILES[tf.profile]
    out = []
    for j in range(n + 1):
        a = m(j, n)
        if tf.scale_in:
            half_w = entry.scale * fp.half                       # current physical size
            cx, cy = entry.apply(fp.cx, fp.cy)                   # current physical centre
            f = 1.0 - a * tf.scale_in / half_w if half_w > 0 else 1.0
            if f <= 0.02:
                warnings.append(f'section {k + 1}: scaling collapses the footprint at layer {j + 1}')
                f = 0.02
        else:
            f, cx, cy = 1.0, 0.0, 0.0
        out.append(entry.then(f, cx, cy, a * tf.shift[0], a * tf.shift[1]))
    return out


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5 + 1e-9))


def resolve(assembly: Assembly, known_designs=None, footprints=None, groups=None) -> ResolvedAssembly:
    """Physical-height intent → whole layers (see the module doc).
    footprints: {design id: Footprint} — needed only by sections that SCALE
    (the centre / reference size of the design's resolved geometry) and by
    an assembly-wide scale (the union of the designs used).
    groups: a groups.GroupModel — used in 'multiple' centre mode (one
    placement per transform group in LayerInstance.parts)."""
    lh = float(assembly.layer_height)
    if not lh > 0:
        raise AssemblyError('layer height must be > 0')
    known = set(known_designs) if known_designs is not None else None
    sections, warnings, counts = [], [], []
    cum, prev_n = 0.0, 0
    for k, s in enumerate(assembly.sections):
        tf = s.transform
        if tf.profile not in PROFILES:
            raise AssemblyError(f'section {k + 1}: unknown transform profile {tf.profile!r}')
        if s.height < 0:
            raise AssemblyError(f'section {k + 1}: negative height')
        if known is not None and s.design_id not in known:
            raise AssemblyError(f'section {k + 1}: unknown layer design {s.design_id!r}')
        cum += s.height
        n_top = _round_half_up(cum / lh)
        n = max(0, n_top - prev_n)
        if n == 0 and s.height > 0:
            warnings.append(f'section {k + 1} ({s.label or s.design_id}, {s.height:g} in) '
                            f'rounds to 0 layers at {lh:g} in')
        counts.append(n)
        sections.append(SectionResult(k, s.design_id, s.label, s.height, n, n * lh,
                                      prev_n if n else None, prev_n * lh, (prev_n + n) * lh))
        prev_n += n
    if assembly.transform.profile not in PROFILES:
        raise AssemblyError(f'assembly transform: unknown profile {assembly.transform.profile!r}')
    if assembly.center_mode not in ('footprint', 'multiple'):
        raise AssemblyError(f'unknown scale centre mode {assembly.center_mode!r}')
    fps = footprints or {}
    design_of = [s.design_id for s, n in zip(assembly.sections, counts) for _ in range(n)]
    whole = _placements(assembly, counts, fps.get,
                        lambda: reference_footprint(design_of, fps), warnings)
    parts = [dict() for _ in design_of]
    if assembly.center_mode == 'multiple' and groups is not None:
        seen = set(warnings)
        for cid, fp in groups.footprints.items():
            w = []
            pl = _placements(assembly, counts, lambda d, fp=fp: fp, lambda fp=fp: fp, w)
            for msg in w:
                if msg not in seen:
                    seen.add(msg)
                    warnings.append(f'transform group {cid}: {msg}')
            for g, d in enumerate(design_of):
                if cid in groups.groups_in(d):
                    parts[g][cid] = pl[g]
    semantic = [()] * len(design_of)
    if assembly.center_mode == 'multiple' and groups is not None:
        for g in range(len(design_of)):
            semantic[g] = relative_transforms(whole[g], parts[g], groups.group_of)
    instances = [LayerInstance(g, design_of[g], _section_of(counts, g), g * lh, (g + 1) * lh, whole[g], parts[g],
                               semantic[g])
                 for g in range(len(design_of))]
    return ResolvedAssembly(lh, sections, instances, warnings)


def relative_transforms(whole, parts, group_of) -> tuple:
    """((source, k, tx, ty), …): each grouped source's placement relative
    to the layer's own, R = whole⁻¹ ∘ part (identity entries omitted)."""
    out = []
    for s, cid in sorted(group_of.items()):
        t = parts.get(cid)
        if t is None:
            continue
        k = t.scale / whole.scale
        tx, ty = (t.tx - whole.tx) / whole.scale, (t.ty - whole.ty) / whole.scale
        # IDENTITY IS EXACT: a group whose placement equals the layer's own
        # (within numeric noise) asks for nothing — the layer prints the
        # canonical design, never a re-resolved look-alike
        if abs(k - 1) > 1e-9 or abs(tx) > 1e-7 or abs(ty) > 1e-7:
            out.append((s, k, tx, ty))
    return tuple(out)


def _section_of(counts, g):
    for k, n in enumerate(counts):
        if g < n:
            return k
        g -= n
    return len(counts) - 1


def _placements(assembly, counts, fp_of, F_of, warnings) -> list:
    """The placement of every layer for one choice of footprints: section
    chains (cumulative, section-local progression), then the assembly-wide
    progression composed on top (module doc)."""
    out = []
    entry = IDENTITY                   # cumulative transform where this section starts
    for k, (s, n) in enumerate(zip(assembly.sections, counts)):
        local = _section_transforms(entry, s.transform, n, fp_of(s.design_id), k, warnings)
        out.extend(local[:n])
        entry = local[n]               # the section's END state: the next one starts here
    if not assembly.transform.is_identity() and out:
        out = _compose_assembly(out, assembly.transform, F_of(), warnings)
    return out


def reference_footprint(design_ids, footprints) -> Optional['Footprint']:
    """F: the union of the designs' footprints (None if unknown). A
    Footprint is centre + half its LONGER side, so this is the union of
    those squares — exact for variants sharing one outline."""
    fps = [footprints[d] for d in dict.fromkeys(design_ids) if d in footprints]
    if not fps:
        return None
    x0 = min(f.cx - f.half for f in fps); x1 = max(f.cx + f.half for f in fps)
    y0 = min(f.cy - f.half for f in fps); y1 = max(f.cy + f.half for f in fps)
    return Footprint.of_bbox(x0, y0, x1, y1)


def _compose_assembly(placements, tf, F, warnings):
    """Compose the assembly-wide progression A_g with each layer's section
    placement E_g (see the module doc)."""
    L = len(placements)
    if tf.scale_in and F is None:
        raise AssemblyError('assembly transform: scaling needs the design footprints')
    c = (F.cx, F.cy) if F else (0.0, 0.0)
    m = PROFILES[tf.profile]
    out = []
    warned = False
    for g, E in enumerate(placements):
        a = m(g, L)
        f = 1.0
        if tf.scale_in:
            f = 1.0 - a * tf.scale_in / F.half if F.half > 0 else 1.0
            if f <= 0.02:
                if not warned:
                    warnings.append(f'assembly transform: scaling collapses the footprint at layer {g + 1}')
                    warned = True
                f = 0.02
        A = IDENTITY.then(f, c[0], c[1], a * tf.shift[0], a * tf.shift[1])
        Ac, Ec = A.apply(*c), E.apply(*c)
        s_e = E.scale
        tx = s_e * A.tx + (1 - s_e) * Ac[0] + Ec[0] - c[0]
        ty = s_e * A.ty + (1 - s_e) * Ac[1] + Ec[1] - c[1]
        out.append(InstanceTransform(A.scale * s_e, tx, ty))
    return out
