# Design + Toolpath — Project Memory

## Purpose

Develop a design and toolpath environment specifically suited to the large-scale mud printer.

This subproject covers both:

1. Creating/manipulating printable geometry (FORM), and
2. Translating that geometry into ordered machine paths (TOOLPATH).

Design and toolpath are intentionally kept together because the design representation corresponds closely to the physical paths followed by the printer.

---

## Conceptual Architecture

The agreed hierarchy:

```
FORM       — continuous design geometry defined over physical Z height
    ↓  [sample at layer height]
LAYERS     — discrete cross-sections (geometry only, no traversal order)
    ↓  ↑  [toolpath may hint to procedural generators about continuity]
TOOLPATH   — ordered traversal of layer geometry
    ↓  [group by cure time]
LIFTS      — operational scheduling (Z ranges, status, notes)
```

The feedback arrow (TOOLPATH → LAYERS) is narrow: only a "continuity hint" to procedural lattice generators about which geometrically equivalent arrangement traverses better. It does NOT silently alter explicit geometry. The designer must explicitly request geometry optimization.

**Geometry describes what exists. Toolpath describes how the nozzle traverses it. These remain conceptually separate.**

---

## Core Design Decisions

### Physical Z keyframes are foundational

FORM keyframes are defined at physical Z heights (e.g., Z = 0", Z = 24", Z = 48"), NOT layer numbers.

Layer generation samples the continuous form according to a separately chosen layer height strategy.

This decouples form design from layer height. Changing layer height does not require redesigning the form.

### Keyframe model is the primary design interaction

The designer:
- Defines 2D geometry
- Places/edits that geometry at specific physical Z heights
- Interpolates between keyframes
- Previews the resulting 3D form
- Samples that form into printable layers

Initial transformations: scale, rotation, X translation, Y translation.

Linear interpolation is sufficient for the first prototype.

Richer interpolation (easing, parameter curves, shape morphing) is an intended future capability — the architecture must leave room for it, but it should not be built prematurely.

### Tiered interpolation strategy

**A. Parametric interpolation**: If geometry is procedurally generated, interpolate its parameters and regenerate. Never directly interpolate baked point arrays for procedural objects.

**B. Shape morphing**: For explicit paths with compatible topology, resample by arc length and interpolate vertex positions. Not required in the first prototype.

**C. Topological transitions**: When topology changes (paths split, merge, appear, disappear, lattice cell count changes discretely), these must be explicit designer-placed transition events. The system must not silently attempt to interpolate through topology changes.

### Strand is the geometry primitive

A **strand** is one continuous piece of printable 2D geometry.

A layer/cross-section contains one or more strands.

A strand may be:
- Open or closed
- Explicit (designer-drawn points) or procedural (generator with retained parameters)
- Outer boundary, inner boundary, lattice/web, or other role

**STRAND describes geometry. TOOLPATH describes traversal. This separation must be preserved.**

### Toolpath is a major design concern

The external mud pump runs continuously. Extrusion is NOT controlled by this software.

The default optimization objective:
- **Maximize continuous printing**
- Minimize non-printing travel moves
- Minimize unnecessary start/stop events
- Minimize awkward reversals
- Prefer smooth, legible traversal

Continuity must not override geometry — it is a major objective when choosing among valid traversals.

### Print graph / Eulerian routing

Resolved printable geometry for a layer is modeled as a graph:
- Printable segments → edges
- Junctions, endpoints, intersections → nodes
- A fully continuous print = Eulerian traversal (visits every edge exactly once)
- Non-Eulerian geometry requires travel moves, retracing, or designer decisions

Eulerian path detection and the Chinese Postman / Route Inspection problem are the correct algorithmic foundation for continuity-first routing.

**Routing objective (lexicographic, adopted 2026-10-05):** (1) never create false printable connections; (2) print all geometry; (3) minimise print runs / travel moves; (4) minimise travel distance; (5) minimise retracing. Consequence: within a connected component the router RETRACES printed edges rather than travelling (see "Routing: retrace augmentation + T-junctions" below). Physical acceptability of double-printed mud on retraced edges is not yet validated on the machine.

**Important constraint:** Simple geometry must remain simple. One continuous wall should not become complicated merely because the software has an internal graph representation. Use graph routing only where it provides genuine value.

### Simple wall geometry

The system must handle all of these naturally:
1. One continuous wall path (one strand, trivially Eulerian)
2. Multiple independent wall paths in one layer
3. Outer + inner wall with no lattice (two disconnected components)
4. Outer + inner wall connected by lattice (one connected component)
5. Mixed combinations

Lattice-connected walls are important but NOT the assumed default case. A simple single-path wall must remain extremely simple in both model and UI.

### Lattice / web geometry

A major use case: outer wall + inner wall + designed web structure between them.

The internal geometry is a first-class architectural element, not conventional slicer infill.

Design parameters for webs include: amplitude, wavelength/frequency, phase, connections, and variation of these over Z height.

Topological changes in lattice (e.g., cell count changes) are discrete events, not smooth interpolations. This distinction between parametric change and topological change must be preserved.

### Procedural geometry

Procedural objects retain their parameters (amplitude, wavelength, frequency, phase, connection rules, etc.) rather than only storing baked points.

This enables meaningful parameter interpolation between keyframes and post-hoc parameter adjustment.

### Designer control over routing

Automatic routing proposes a default — it does not remove designer control.

Eventually controllable: start point, end point, seam location, path direction, component order, inner/outer ordering, retracing acceptability, specific connection treatment, consecutive operation requirements.

### Toolpath visualization

The toolpath must be inspectable, not hidden. Visualization should clearly distinguish:
- Print motion vs. travel motion
- Direction arrows
- Numbered path order
- Start/end markers
- Seam markers
- (Eventually) animated nozzle traversal

### Multi-layer continuity

Important future capability: where layer N ends should inform where layer N+1 begins; seam position should be optimizable across layers.

**Deferred.** Layers are treated independently for now. Architecture must not make multi-layer optimization difficult to add later.

### Lifts

LIFT is an operational/scheduling concept: a Z range of layers printed in one session before allowing mud to cure.

Structure: Z range, list of layers, status, notes.

**Deferred.** Do not build lift planning yet.

---

## What Is Explicitly Outside This Subproject

- Extrusion/pump control
- GRBL firmware
- Raspberry Pi printer interface (separate subproject, Milestone 1 already built)
- The separate small clay extrusion / multi-auger project (different machine, do not conflate)

The Design + Toolpath system will eventually produce a job/toolpath representation that the Pi Interface executes. Integration is not required yet.

---

## Development Philosophy

- Small, understandable systems
- Explicit representations
- Inspectable geometry and toolpaths
- Deterministic behavior
- Easy experimentation
- Reversible decisions
- Tests for geometry/routing logic
- Visual prototypes that let us judge ideas quickly
- No premature production architecture
- No hidden "magic" optimization

---

## Prototype Strategy

### Phase 1 — Form / Keyframe Prototype (not yet built)

Tests whether physical-Z keyframes and interpolation are a useful way to design these forms.

Scope:
- Hard-coded or imported simple 2D geometry
- 2–3 keyframes at physical Z heights
- Scale, rotation, X/Y translation
- Linear interpolation
- Adjustable layer height
- Sample form into layers
- 3D preview of stacked paths
- Scrub through physical Z to see cross-section

### Phase 2 — Toolpath / Graph Prototype (FIRST — currently building)

Tests whether graph-routing produces useful, understandable paths.

Rationale: graph-based toolpath is the more novel and uncertain hypothesis. Validates it independently using hard-coded geometry.

Test geometries:
- A: one continuous wall
- B: two independent walls
- C: outer + inner wall, no connection
- D: outer + inner wall + sinusoidal connecting web
- E: intentionally awkward/disconnected geometry

For each:
- Resolve to printable geometry
- Build print graph
- Identify connected components
- Identify odd-degree nodes
- Compute continuity-first traversal
- Visualize: print order, direction, start/end, travel moves
- Show metrics: print runs, travel moves, travel distance, retracing, % printing vs. travel

Basic overrides: pin start, pin end, reverse direction, component order.

---

## Foundational Design Decisions (Established in Design Interview)

### PATHS ARE THE DESIGN

Every printable element is a path (nozzle trace). This is a foundational decision, not a preference for the first prototype.

Design tools create, modify, relate, and generate paths. The toolpath system determines how those paths are traversed. The final physical result is produced directly from those paths.

Non-printing visual elements (guides, reference geometry, annotations) are permitted but must be visually distinguished. The path-first principle means printable geometry is always paths.

### SOURCE GEOMETRY IS NON-DESTRUCTIVE

Treatments, generators, and Z conditions derive *effective print geometry* from source geometry. Source geometry is never altered, split, or destroyed.

Effective Print Geometry pipeline:

```
SOURCE DESIGN GEOMETRY
    ↓  [treatments / generators / Z conditions]
EFFECTIVE PRINT GEOMETRY
    ↓  [routing / traversal]
TOOLPATH (ordered moves)
```

Offset inner walls, lattice webs, and other derived geometry exist only in Effective Print Geometry. The source path that generated them remains unchanged and editable.

### PRIMITIVE IDENTITY PRESERVATION

Line, Circle, Ellipse, and Rectangle are distinct parametric types. Each samples to a common path representation for rendering, offset, lattice, and routing. Converting to editable points destroys parametric identity and must be an explicit designer action.

### PATH SECTIONS

A path section is a range `[t_start, t_end]` on a path (arc-length parameterized). Different treatments can apply to different sections without splitting underlying source geometry. Sections are included in the prototype.

### PARAMETERS vs VARIATIONS

**Parameters** are deliberately specified properties. Changing a parameter changes what was requested.

**Variations** are discrete alternative solutions to the same parameter set. Selecting a different variation does not alter parameters. These are strictly separate concepts.

This applies to lattice generators: same amplitude/wavelength/frequency can have multiple valid arrangements (phase choice, which wall connects to which). Variations expose those alternatives.

### ROUTING OVERRIDES ARE CONSTRAINTS

Start point, direction, component order, and other routing overrides are stored as constraints on the traversal. The router recomputes when constraints change. These are never implemented as mutations to source geometry.

### PROGRESSIVE DISCLOSURE

The simplest valid design (draw one path → route it → done) must remain simple. Complexity is revealed only as the designer needs it. Offset panels, lattice, sections, overrides — all optional, none required for basic use.

---

## Prototype Strategy

### Phase 2 — Toolpath / Graph Prototype — COMPLETE

Location: `design-toolpath/toolpath_proto/`

Built and tested. 65 tests at completion (57 routing/geometry + 8 app integration); 77 after the 2026-10-05 retrace-routing change.

Key result: graph-based Eulerian routing works correctly for all five test geometries.

**Most important finding:** Geometry D (wall perimeter + internal zigzag web) produces exactly 2 odd-degree nodes → single Eulerian path, zero travel moves, 100% continuous printing. This validates the core hypothesis that designed internal geometry enables fully continuous printing.

| Case | Description | Components | Odd nodes | Travel moves | % Printing |
|------|-------------|-----------|-----------|--------------|------------|
| A | Single closed wall | 1 | 0 | 0 | 100% |
| B | Two independent walls | 2 | 0 each | 1 | ~99% |
| C | Outer + inner, no lattice | 2 | 0 each | 1 | ~99% |
| D | Wall + internal web | 1 | 2 | 0 | 100% |
| E | Awkward / T-junction | 3 | mixed | 2 (was 2+: the T-component now retraces instead of travelling) | ~85–95% |

### Phase 3 — Design Canvas Prototype — IN PROGRESS

Location: `design-toolpath/design_proto/`

**Goal:** Test whether the path-first design model + live parametric treatments + overlay toolpath visualization works as an interactive design environment.

**Scope** (13 implementation steps, in order):

1. Data model — effective-print-geometry pipeline (Path, Section, Offset, Lattice, PrintLayer)
2. Blank canvas + source-path drawing/editing (freehand polyline, move nodes, close path)
3. Primitives: Line, Circle, Ellipse, Rectangle (parametric identity preserved)
4. Non-destructive sections (split points, per-section properties)
5. Live offset treatment (offset distance, side; regenerates when source changes)
6. Assemble effective PrintLayer → connect existing routing engine from toolpath_proto
7. Toolpath overlay (toggle direction arrows, sequence numbers, start/end, travel moves)
8. Zigzag lattice generator (amplitude, wavelength, connection nodes, variation index)
9. Generator variations (discrete alternative arrangements for same parameters)
10. Routing overrides as constraints (start point, direction, component order)
11. Live dependency/regeneration (geometry change → effective layer recompute → toolpath update)
12. Wave lattice generator (extensibility test: confirms generator architecture is correct)
13. UX cleanup + integration testing

**Physical-Z keyframes are deliberately deferred.** This prototype works at a single Z slice. Architecture must not make keyframes hard to add later.

**Routing engine reuse:** `graph.py` and `geometry.py` from `toolpath_proto/` are reused directly. No duplication.

**Generator architecture:**
```python
class LatticeGenerator:
    def parameters(self) -> list[ParameterSpec]: ...
    def generate(self, path_a, path_b, params, variation_index=0) -> list[Path]: ...
```

**Data model (top level):**
```
Path          — source geometry (open or closed, parametric or explicit points)
  PathSection — range [t_start, t_end] on a path, with own properties
OffsetTreatment  — derives an offset path from a source path
LatticeGenerator — derives lattice paths between two source paths
PrintLayer    — assembles effective print geometry from all sources + treatments
               → fed to route_layer() for traversal
```

### Phase 1 — Form / Keyframe Prototype — NOT YET BUILT

---

## Current State

### Phase 2 — Toolpath / Graph Prototype — COMPLETE

Location: `design-toolpath/toolpath_proto/`. 77 tests passing.

### Phase 3 — Design Canvas Prototype — COMPLETE (all 13 steps + six UX passes)

Location: `design-toolpath/design_proto/`. 339 tests passing.

Files:
- `model.py` — full data model: Vec2, Path subtypes (incl. QuadBezierPath), OffsetTreatment, ZigzagGenerator, WaveGenerator, LatticeInstance, Opening, PrintLayer (with corner_radius, cap_style, cap_corner_radius, openings), TraversalConstraints
- `app.py` — Flask app; API: GET /api/generators, POST /api/route, POST /api/effective_paths
- `static/index.html` — design canvas UI (incl. WALL GEOMETRY sidebar section)
- `static/app.js` — canvas drawing, primitives, offset panel, lattice panel, toolpath overlay, routing overrides, dimensions overlay (incl. curve chord), playback transport, JS-side corner rounding
- `tests/test_model.py` — unit tests covering model layer + geometry validation
- `tests/test_app.py` — integration + workflow tests
- `tests/test_openings.py` (+ `tests/js/ui_openings_smoke.js`) — opening geometry, routing, lattice, caps, serialisation, JS parity, UI smoke

**UX pass 2 (14-point spec):** True geometric offset, Add Lattice fix, Role removed from UI, Individual delete, Arrow legibility, Numbers removed, Arrows disabled when Toolpath OFF, Metric label renames, Clear All.

**UX pass 3 (14-point spec) — geometry validity pass:**

**Architectural principle established:**
WALL GEOMETRY → VALID WALL CAVITY → VALID LATTICE GEOMETRY → TOOLPATH / CONTINUITY OPTIMIZATION.
The routing engine must never rescue geometrically invalid lattice. Validity is the generator's responsibility.

**`_resample` closed-path fix**: Added `closed` parameter. When `closed=True`, the closing segment (last→first) is included in the total arc length before resampling. This ensures closed-path resampling covers the FULL perimeter including the final edge. Root cause of "zigzag covers ~3 sides then stops" on rectangles.

**ZigzagGenerator — closed-loop coverage**: Now calls `_resample(..., closed=path.closed)` for both boundaries. For a closed rectangle with segments=8, all 4 sides are now covered. For closed circles, the full 360° is sampled.

**ZigzagGenerator — geometric validity and auto-increase**:
- For two closed boundaries, every generated lattice segment is validated against the wall cavity: midpoint must be inside outer and outside inner boundary (point-in-polygon), and the segment must not cross either boundary (_segments_intersect check).
- If requested segment count produces invalid geometry (e.g., a diagonal chord cuts through the inner circle at low segment counts), the generator auto-increases by 2 until valid or max 80 segments reached.
- Actual count stored in path label (e.g., `zigzag_n12`).

**WaveGenerator — redesigned**:
- Old behavior: sinusoidal perpendicular oscillation around midpoint, didn't touch boundaries, folded at high amplitude. Amplitude was user-controlled.
- New behavior: wave oscillates between boundary A and boundary B by direct lerp. `alpha(s) = 0.5 × (1 − cos(2π × cycles × s + phase_offset))`. alpha=0 → exactly on A, alpha=1 → exactly on B. Inherently valid by construction (alpha always ∈ [0,1]).
- Parameters removed: `amplitude`, `frequency`, `samples`.
- Parameters kept/added: `cycles` (default 3, min 0.5, max 20), `phase` (0..1 fraction of cycle).
- Samples computed internally: `max(64, int(cycles × 32))`.
- V1: starts on A. V2: half-cycle offset (starts toward B).

**Geometry validation helpers added to model.py**:
- `_polygon_area(pts)` — shoelace formula
- `_point_in_polygon(pt, poly)` — ray-casting
- `_segments_intersect(p1,p2,p3,p4)` — strict interior crossing test
- `_segment_crosses_polyline(a,b,poly,closed)` — segment vs. polyline
- `_lattice_valid_in_cavity(derived_paths,path_a,path_b)` — full cavity check

**Dimensions overlay (toolbar toggle "Dimensions")**:
- Independent of Toolpath toggle.
- When ON: draws type-specific physical dimension labels on source paths and derived wall offsets. NOT on lattice paths.
- LinePath: length. CirclePath: `R XX in`. EllipsePath: `Rx, Ry`. RectanglePath: `W × H`. ExplicitPath/other: arc length (`~XX in`).
- Derived offset paths: radius (for circle sources), W×H (for rect sources), arc length otherwise.
- Purely visual — no effect on geometry, routing graph, or toolpath export.

**UX pass 4 (6-point spec) — routing quality + playback:**

**Wave seam bridge (zero-travel for closed walls)**: `WaveGenerator.generate()` now appends a second `DerivedPath` for closed boundaries — a two-point "seam connector" from `wave_pts[-1]` to `path_b.sample_points()[0]` (V1) or `path_a.sample_points()[0]` (V2). This creates exactly 2 odd-degree nodes in the routing graph → Eulerian path → zero travel moves. Node matching is exact because `wave_pts[0]` is always exactly `path_a.sample_points()[0]` (alpha=0 at t=0 → lerp(a,b,0) = a).

**Phase parameter removed**: `phase` removed from `WaveGenerator.parameters()` and `generate()`. Phase breaks the seam bridge (a nonzero phase offset makes `wave_pts[0]` not exactly equal to a boundary node). Phase was also observed to have no useful visual effect in practice. `phase_total = 0.5 * (variation_index % 2)` is now baked in.

**Open double-wall end caps**: `PrintLayer.effective_paths()` auto-generates two `DerivedPath` cap objects for each `OffsetTreatment` whose source path is open (`not src.closed`):
- `cap_start` (id=`ot.id+'_cs'`, role='cap'): `[src_pts[0], der_pts[0]]`
- `cap_end` (id=`ot.id+'_ce'`, role='cap'): `[src_pts[-1], der_pts[-1]]`
This connects two otherwise-disconnected open strands into an Eulerian circuit. Endpoint coordinates are guaranteed exact because `LinePath.sample_points()` always returns `[start, end]` and `_offset_polyline` on a single segment returns the exact offset endpoints.

**Reverse routing override fixed**: `app.py` `api_route()` now post-processes moves when `c.reverse_direction=True` by reversing the list and swapping `start`/`end` of each `PrintMove`. Previously, `reverse_direction` was read from constraints but never actually applied (the routing engine has no such parameter).

**Unique boundary labels**: `allBoundaries()` in `app.js` now generates: `"${dirCap} offset of ${srcLabel} — ${dist.toFixed(0)} in"` (e.g. "Inside offset of Circle — 10 in" vs "Outside offset of Circle — 15 in"). Previously all offset boundaries showed the same generic label.

**Toolpath playback transport bar**: Full play/pause/scrubber/speed/direction transport added in `index.html` + `app.js`.
- Transport bar appears below canvas when a route is computed; hidden otherwise.
- Progress is distance-based (world inches), not point- or move-count-based.
- `_routeCumDists` precomputes `{ds, de, m}` per move; `_nozzleAtPos(p)` interpolates within the current move.
- Yellow nozzle circle drawn at current position; already-printed moves shown at full opacity, future moves at 18%.
- Controls: restart (⏮), play/pause (▶/⏸), scrubber (range 0–1000), speed selector (0.25×–8×), reverse toggle (⏪).
- rAF animation loop at `_PLAYBACK_WORLD_SPEED = 100.0` world in/s at 1×.
- Playback resets when toolpath is toggled off or Clear All is called.

**UX pass 6 — curve chord dimensions, global corner rounding, round end caps:**

Core architectural rule enforced: **displayed geometry = printed geometry.** Corner rounding and cap styles are applied to effective print geometry before routing, dimensions, arrows, playback — not as cosmetic canvas effects.

**Curve chord dimensions**: When Dimensions is enabled on a QuadBezierPath, a faint dotted line is drawn start→end with a `chord NNN in` label. Arc-length label remains unchanged. The chord is purely visual — never enters `effective_paths()` or routing.

**Global corner rounding (fillets)**: New `corner_radius` field on `PrintLayer` (default 0). When > 0, every eligible source path (`RectanglePath` and `ExplicitPath`) has its sharp corners replaced with true tangent circular arcs via `_apply_corner_rounding(pts, radius, closed)` which calls `_fillet_vertex(A, B, C, r)` per vertex. The fillet geometry:
- Tangent distance from the vertex `t = r / tan(θ/2)` where θ is the interior angle
- Automatic clamping: `t ≤ min(len_AB/2, len_BC/2)` so fillets never overrun adjacent segments
- Actual fillet radius after clamping: `actual_r = t * tan(θ/2)` (equals requested R when not clamped)
- Arc center on angle bisector, distance `actual_r / sin(θ/2)` from vertex
- Arc sweep `(π − θ)`, direction chosen so arc replaces the corner (short side)
- `~5°/sample` density gives smooth polyline approximation
- Open polylines preserve first/last points; interior vertices are filleted
- Closed polygons fillet every vertex

**Constant wall spacing under rounding**: Rounding is applied **before** offsetting. `OffsetTreatment.generate(source, corner_radius)` first rounds the source polyline, then calls the existing `_offset_polyline` on the rounded sample. The result is a parallel offset of the rounded shape — wall spacing stays within ~2in of the requested distance everywhere (test: `test_rounded_rect_offset_approx_constant_spacing`).

**Round end caps**: New `cap_style` field on `PrintLayer` (`'flat'` | `'round'`, default `'flat'`). For an open source + offset with `cap_style='round'`, each flat 2-point cap is replaced by `_semicircle_cap(p0, p1, outward, n=32)` — a semicircular arc from `p0` to `p1` bulging in the `outward` direction. The outward direction:
- Start cap outward = −(forward tangent at source start)
- End cap outward = +(forward tangent at source end)
Tangent is derived from the rounded source polyline (`src_pts[1] − src_pts[0]` for start, `src_pts[-1] − src_pts[-2]` for end), so curved walls (QuadBezierPath) produce caps oriented along the local curve tangent. Cap radius = half the wall perpendicular distance. Endpoints are forced to exact `p0`/`p1` so the routing graph merges nodes.

**JS/Python geometry parity**: `_computePrimitivePoints` in `app.js` applies the same corner rounding to `path.points` (used for canvas rendering, hit-testing, and `_offset_polyline` input in the backend), so the on-canvas path matches the backend's effective geometry byte-for-byte. `onWallGeometryChange()` recomputes all source path points and triggers a route refresh.

**WALL GEOMETRY sidebar section**: Corner R number input + End caps Flat/Round select. Both are layer-level (not per-path). Changing either immediately refreshes effective geometry.

**Geometry correctness pass (2026-10-05) — wall offsets, rounding, end caps** — complete; verified by manual UI testing:

- **One canonical processed source per path**: `_processed_source_pts(path, corner_radius)` (sample → fillet if eligible → dedupe). Source display, every offset, caps and lattice all derive from it. `OffsetTreatment.generate(processed_pts, closed)` is now a pure offset of that polyline (signature changed from `generate(source, corner_radius)`).
- **Always miter, never bevel** (decision): `_offset_polyline` no longer bevels at acute corners, because a bevel silently thins the wall below D. Consequence: miter spikes at sharp corners are kept and treated as real geometry.
- **Offset trimming** — `_trim_offset(raw, source, dist, closed)` replaced the earlier `_prune_offset_inversions` (direction-reversal vertex dropping), which failed on manual test (irregular W polygon, 10 in inside offset, Corner R 6 → hooks/loops at concave corners). Root cause: wherever D exceeds the local feature size (fillet R < D, or notch narrower than 2D) the raw miter offset forms swallowtail loops, including non-local ones the vertex-direction test cannot see. Fix: split the raw offset at all self-intersections; keep pieces that lie outside the *mitered* offset band of the source (per-edge rectangles + per-joint miter kites — must match miter semantics, true-distance classification breaks reassembly at miter spikes) and on the requested side; re-join at intersection nodes. Closed: largest loop kept; open: longest chain. Nothing valid → empty DerivedPath (explicit collapse signal; JS skips it).
- **Wall-system end treatment**: one cap per end per wall system (source + all its offsets), never per offset. Single cross-section profile of total thickness W: outermost wall → fillet r → straight face (r beyond wall ends) → fillet r → innermost wall, fillets tangent to the walls. `flat` r=0; `rounded_corners` r=min(End R, W/2); `full_round` r=W/2 (legacy `'round'` aliases to it). Intermediate walls get no cap: each continues straight (`cap_*_ext` DerivedPath, role `cap`) to a vertex inserted into the cap profile, so the system routes as one run with zero travel. Earlier version ignored End R (it filleted collinear points on the end face) and Full Round spiked back to / dangled the centre wall — both fixed.
- UI: End caps select = Flat / Rounded Corners / Full Round; `End R` input (`cap_corner_radius`, layer-level) shown for Rounded Corners.
- **Known ambiguous case**: sharp (Corner R = 0) deep notches with a large inside offset — the miter spike at the notch can sever the cavity into islands; only the largest island is kept. True (round-join) distance would keep it connected. Not changed because always-miter is a deliberate decision; revisit if it matters in practice (options: Corner R > 0, round joins at reflex corners, or emit all islands).

**Openings in walls (2026-10-05)** — future doors/windows. Complete; verified by manual UI testing.

- **Data model** — `model.Opening`: `id, source_path_id, center_s, width (default 12), end_treatment='inherit', z_min=None, z_max=None, label`. First-class design object stored on `PrintLayer.openings`; serialised in `to_dict` / `app._deserialise_layer` (payloads without `openings` still work).
- **Path-relative, not XY**: `center_s` / `width` are inches of arc length along the *processed* source polyline (sampled + Corner R = the printed wall) from the path's start. Editing/moving/resizing the source keeps the stored `center_s`; on open paths it is clamped to the path length (non-destructively), on closed paths it wraps mod perimeter. JS samples Circle/Ellipse/Bézier at 128 points so `path.points` equals the backend processed polyline (parity for placement, handles and drawn gaps).
- **Width = CLEAR opening** (decision): end treatments protrude past a cut face by the cap reach r (0 flat, min(End R, W/2) rounded, W/2 full round), so each cut face is placed at width/2 + r from the centre. The finished ends then stop exactly at the clear width and two caps can never cross, however narrow the opening. `PrintLayer._opening_cap_reach` / JS `_openingCapReach`.
- **Pipeline position**: processed source → FULL offsets (miter + `_trim_offset` unchanged) → `_OpeningPlan` cuts the assembly → lattice generated on FULL walls then clipped → caps → emit. Offsets are never computed on cut pieces, so all offset invariants hold. Sources without openings produce exactly the old output and ids.
- **Cutting**: `_opening_removed_intervals` merges intervals (wrapping through the seam on closed paths; ≥ perimeter removes the whole assembly); `_surviving_intervals` gives the pieces. A closed loop with k openings → k open pieces; an open path → up to k+1. Source pieces via `_sub_polyline` (arc-length, spans any corners/seam). Offset cut points: the offset point whose nearest-point projection onto the source equals the cut s (start from source point + d·normal, then local bisection). Exception inherent to polyline offsets: on a concave side a miter vertex covers a short s-range, so a cut there lands on that miter vertex (also how corner-spanning openings cut an inner wall at a sharp corner).
- **Ids**: pieces `{src}~k`, `{offset}~k`; caps `{src}~k_cs/_ce` (+ `_x` extensions); lattice `{id}~k`. Source pieces have `treatment_id='opening_cut'` (JS draws sources itself via `_survivingPieces`, so it skips those).
- **End geometry**: every wall-system piece is capped by the existing `_wall_system_end_pts` (generalised to take `walls=[(distance, polyline)]` plus `landings`), so opening faces inherit Flat / Rounded Corners (End R) / Full Round exactly like original open ends. Intermediate walls extend onto the cap as before. A single wall without offsets gets no caps (as before).
- **Lattice**: generated on the full boundaries (generator validity unchanged), then `_clip_lattice_by_openings` splits each segment where it crosses a cut face (or the face line continued; source normal line if no offsets) and drops sub-segments whose midpoint projects into a removed interval — nothing crosses, bridges or protrudes into an opening. Lattice ends on a face are registered as cut `landings` and joined to the cap exactly like intermediate walls (real contact with the end wall). Lattice between two independent sources is clipped by openings in either.
- **Routing**: router solves the real topology. Closed double wall + 1 opening → one Eulerian circuit (outer → face → inner reversed → face): 1 run, 0 travel. 2 openings → 2 components: 2 runs, 1 travel (correct, not avoided). Lattice landings add odd nodes (e.g. ring + zigzag + 1 opening → 4 odd nodes).
- **UI**: toolbar *Opening* tool (click a wall → 12 in opening centred at the nearest point, back to Edit). Selected opening: sidebar Width / Position (centre, along path) / End treatment = Inherit (disabled), Delete; drag the gap to slide (wraps through the seam), drag □ ends to resize (other end fixed; a click nearer the middle than an end slides). Openings listed under their wall in Paths. Faint dashed gap + face ticks when unselected, magenta accent when selected (distinct from retrace orange). Any number of openings per wall: each is placed, selected, slid, resized, edited and deleted independently. Dimensions: clear width label at each opening (e.g. `36 in`). Deleting a wall deletes its openings.
- **Future Z**: `z_min`/`z_max` are round-tripped but unused; this prototype is one Z slice. Intended: an opening exists only for layers whose physical Z is within [z_min, z_max] (door 0–84 in, window 36–72 in) — the per-layer plan simply includes/excludes it.
- **Multiple openings / union**: all openings of a source are unioned in arc length before cutting (`_opening_removed_intervals`): overlapping, contained or touching intervals (gap ≤ `OPENING_MERGE_TOL` = 0.001 in) become ONE removed interval, including across the closed-path seam (an interval running past L absorbs those starting near 0); open-path intervals within tol of an end snap to it. Fragments ≤ tol are dropped. Caps therefore exist only at the outer boundaries of a union; Opening objects stay separate design objects. JS `_survivingPieces` mirrors the same rules (parity tested). Each opening's cut interval is widened by the cap reach before the union.
- **Tests**: `tests/test_openings.py` (incl. JS helper parity and a node-driven UI smoke test `tests/js/ui_openings_smoke.js`; JS tests skip without node).
- **Known limitations**: (1) [resolved 2026-10-05 — see retrace routing] router used travel for all odd pairs. (2) Near sharp corners the cut face can be skewed (cross-section through a miter region). (3) Openings are measured on the processed (rounded) path, so changing Corner R shifts positions past rounded corners slightly. (4) Per-opening end treatment and Z range not implemented by design.

**Routing: retrace augmentation + T-junctions (2026-10-05, toolpath_proto/graph.py)** — complete; verified by manual UI testing.

- **Problem found** (rect + inside offset + zigzag + 1 opening, Flat): UI showed 3 runs / 2 travels. Graph: 1 connected component, 4 odd nodes (zigzag start at a wall corner (deg 5), inner corner on the zigzag end-connector (deg 3), the two lattice landings on the opening faces (deg 3)); no unmerged coincident nodes. Old `_augment` paired ALL odd nodes with straight travel edges into a circuit — one travel jumped 12 in straight across the opening. A zero-travel route exists; zero-retrace does not (Euler: > 2 odd nodes). Proven optimum (exhaustive pairing): 1 run, 0 travel, 14.14 in retrace (the zigzag end connector re-traversed once).
- **Previous approach**: travel-edge augmentation via min-weight matching on Euclidean distance, full circuit. **Changed because** it (a) wasted one pair that could be the trail's ends, (b) used non-printing jumps inside connected geometry — which can cross openings — instead of the legitimate graph.
- **New `_augment_by_retrace`**: open-trail route inspection — min-weight matching of odd nodes on SHORTEST IN-GRAPH path length, with two zero-cost dummy terminals so exactly two odd nodes remain as trail ends (a pinned odd start is forced to be one); matched paths' edges are duplicated. Travel only between components. `label_passes` marks first traversal 'print', later ones 'retrace' (also re-run after reverse in app.py). `compute_metrics`: a run is continuous extrusion (print + retrace) broken only by travel; adds `retrace_moves`.
- **T-junctions in `build_graph`**: a strand vertex lying on another segment's interior (≤ `JUNCTION_TOL` 1e-6 in) splits that segment into a shared node (edges carry `sub_idx`). Case D did this by hand; design_proto lattices (zigzag/wave vertices on walls) never did, so lattice touching walls mid-segment was represented as disconnected (e.g. zigzag between two separate walls: 2–3 components before, 1 after). Near-misses (≥ 0.001 in) are not merged.
- UI badge "Continuous, with retrace" (1 component, > 2 odd nodes) with retrace length in the tooltip.
- Tests: `toolpath_proto/tests/test_retrace_routing.py` (junctions, near-miss, minimal retrace vs brute force, pinned start, disconnected still travels, Case E); design_proto `TestReportedRoutingCase`.

**UX pass 5 — editable curved line segments (QuadBezierPath):**

`QuadBezierPath` added as a first-class parametric type. Implements B(t) = (1−t)² P0 + 2(1−t)t P1 + t² P2 where P0=start, P1=control/bend, P2=end.

**3-click Curve tool**: "Curve" button in toolbar; click 1 = start, click 2 = end, click 3 = bend/control. Live curved preview after click 2. Auto-returns to Edit mode after placement.

**Three semantic handles**: Start (circle), End (circle), Bend/control (square). Dashed control lines from start→bend and end→bend shown when selected.

**Sidebar fields**: Label, Start X/Y, End X/Y, Bend X/Y. Closed checkbox suppressed for curves (always open).

**Canvas rendering**: Uses `ctx.quadraticCurveTo` for smooth rendering; sampled polyline is stored for hit testing and offset generation only.

**Dimensions**: Shows arc length (sampled polyline arc length), label at t=0.5 midpoint.

**Offsets, end caps, lattice**: Work identically to LinePath — `OffsetTreatment.generate()` calls `source.sample_points(128)` then `_offset_polyline`; end caps and Eulerian zero-travel routing work because `sample_points()[0]` = start exactly and `sample_points()[-1]` = end exactly (B(0) = P0, B(1) = P2 by construction).

**Toolpath, playback, reverse, start/end markers**: All work without modification — QuadBezierPath integrates with existing routing engine transparently.

**Known limitations / deferred:**
- Node-drag editing for Circle/Ellipse primitives is approximate
- Path sections (split points / per-section properties) in model but no UI yet
- Multi-layer / physical-Z keyframes deferred (as planned)
- Offset direction convention assumes CCW winding for "inside" = inward

---

## Algorithms and References

- **Eulerian path / Chinese Postman (Route Inspection)**: primary toolpath routing
- **Arc-length parameterization**: path sections and shape morphing
- **Medial axis / Voronoi skeleton**: lattice positioning between walls (future)
- **Offset curves**: generating inner wall from outer wall
- **Catmull-Rom splines**: curved drawn paths (through-points, simpler than Bezier)
- **NetworkX**: graph construction, matching (`nx.min_weight_matching`)

---

## Open Questions

- How should topological transitions (cell count changes, path splits/merges) be represented?
- How should the lattice generator accept continuity hints without violating geometry/toolpath separation?
- How should seam position be managed and optimized across layers?
- What is the right output format for the Pi Interface to consume?
- Is retracing (printing a second bead over an existing one) physically acceptable for mud, and is there a length above which a travel would be preferred? Current policy always retraces within a connected component. Needs machine testing.
- Should opening positions be measured on the unrounded source so Corner R changes don't shift them?
- Does the keyframe model produce the forms the designer imagines? (Phase 1 will answer)

---

## Last Updated

2026-10-05

Geometry correctness pass committed (dd527a4). Openings in walls (multiple per wall, unioned) and retrace routing + T-junction graph merging complete and manually verified in the UI (committed together). Phase 2: 77 tests. Phase 3: 339 tests. All green.
