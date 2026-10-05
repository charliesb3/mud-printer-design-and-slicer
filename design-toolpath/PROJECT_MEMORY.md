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

**Wall-infill objective — REVISED 2026-10-05 (wall lattice pass): the STITCH PATTERN IS PRIMARY; spacing is a target; continuity is DESIGNED into the lattice, not repaired into it.** Hierarchy for wall infill: (1) repeatedly stitch between opposite faces, (2) a smooth recognisable motif, (3) no exact interior-infill retrace where wall area allows a return, (4) no congestion, (5) coherent strategy across equivalent branches, (6) one run, (7) start ≈ end, (8) even pitch within a run, (9) near the target spacing, (10) short. Previous approach (fixed-pitch triangulated field + local parity repair, below) changed because manual testing of four (curved) arms showed bow ties, boxes, diamonds, abrupt motif switches, different strategies per arm and exact retrace — legal graphs, bad deposition. See "Wall lattice (motif-based)" below. Solid infill is unaffected (solid.py).

**Routing objective — REVISED AGAIN 2026-10-05 (physical quality) — NOW APPLIES ONLY to the wide-region field fallback; wall regions follow the wall-infill objective above:** the goal is a physically sensible continuous wall toolpath: keep the intended infill pattern and make the smallest LOCAL edits needed for continuity. BEST: continuous, no travel, no exact retrace, sensible infill, small local deviations, no congestion. ACCEPTABLE: modest local support, hidden crossings, locally irregular lattice. BAD: exact retrace, long arbitrary return beads, generated knots, generated paths converging on one point, excessive extrusion for parity. Previous version (hidden "return paths": straight / offset-copy beads paired globally) changed because manual testing showed long beads through walls, dense knots at junctions and many generated paths through nearly one point — zero retrace bought with bad deposition. See "Physical route quality" section.

**Routing objective — REVISED 2026-10-05 (route planning, superseded the same day by physical quality):** (1) never leave printable wall material except unavoidable travel between disconnected components; (2) keep visible faces clean (never moved); (3) continuous extrusion; (4) prefer a NEW hidden return path through the wall over exact retrace; (5) short / smooth; (6) keep the infill pattern where practical (local deviations allowed); (7) exact retrace only as last resort (e.g. single-bead dead ends). Previous objective (kept below for history) made retrace the normal solution; changed because a mud bead has width and depth, so printing back over the same bead is not neutral, while the inside of a thick wall is free to change. See "Route planning" section.

**Routing objective (lexicographic, adopted 2026-10-05; SUPERSEDED for wall infill by the objectives above — still the router's generic objective, with one exception: odd ends of solid-infill strands (`travel_pairing`) are joined by a short travel instead of retrace):** (1) never create false printable connections; (2) print all geometry; (3) minimise print runs / travel moves; (4) minimise travel distance; (5) minimise retracing — WEIGHTED (since wall networks, 2026-10-05): retracing a visible wall face / single-bead wall costs `FACE_RETRACE_COST` = 3 × its length, internal geometry (lattice, centre lines, junction connectors) 1 ×, so continuity transitions hide inside the wall. Consequence: within a connected component the router RETRACES printed edges rather than travelling (see "Routing: retrace augmentation + T-junctions" below). Physical acceptability of double-printed mud on retraced edges is not yet validated on the machine.

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

### Phase 2 — Toolpath / Graph Prototype (original plan — now COMPLETE, see below)

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

Built and tested. 65 tests at completion (57 routing/geometry + 8 app integration); 77 after the 2026-10-05 retrace-routing change; 85 after wall networks (X-crossing junctions, per-strand retrace cost).

Key result: graph-based Eulerian routing works correctly for all five test geometries.

**Most important finding:** Geometry D (wall perimeter + internal zigzag web) produces exactly 2 odd-degree nodes → single Eulerian path, zero travel moves, 100% continuous printing. This validates the core hypothesis that designed internal geometry enables fully continuous printing.

| Case | Description | Components | Odd nodes | Travel moves | % Printing |
|------|-------------|-----------|-----------|--------------|------------|
| A | Single closed wall | 1 | 0 | 0 | 100% |
| B | Two independent walls | 2 | 0 each | 1 | ~99% |
| C | Outer + inner, no lattice | 2 | 0 each | 1 | ~99% |
| D | Wall + internal web | 1 | 2 | 0 | 100% |
| E | Awkward / T-junction | 3 | mixed | 2 (was 2+: the T-component now retraces instead of travelling) | ~85–95% |

### Phase 3 — Design Canvas Prototype (original plan — current status under Current State)

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

Location: `design-toolpath/toolpath_proto/`. 90 tests passing.

### Phase 3 — Design Canvas Prototype — 13 steps + six UX passes + wall networks + passes 1–7 (checkpoint commit 414c283)

Location: `design-toolpath/design_proto/`. 750 tests passing (incl. 3 node UI smoke tests); toolpath_proto 90. Passes since wall networks are NOT yet manually verified in the browser.

How to read this file: the pass sections below are kept as HISTORY (newest decisions win). Where a later pass replaced an approach the older text is marked SUPERSEDED. Current behaviour in one paragraph: wall infill = `wall_lattice.py` motifs (corners braced, max unsupported distance, interleaved out-and-back with cap V); regions too wide to be a wall (thickness > 1.6 × target, provisional) fall back to the `infill.py` field + `route_plan.py` repair; solid infill = `solid.py` (rectilinear / serpentine, turns land on boundaries); router pairs leftover solid odd ends by short travel.

Files:
- `model.py` — data model: Vec2; Path subtypes (Line, Rect (with rotation), Circle, Ellipse, QuadBezier, Explicit, InsetPath; per-path `corner_radius`, `wall` WallSpec); OffsetTreatment (Extra Offsets); WallSpec, NetworkWall, WallRelation; RegionInfill (kind wall / solid); JunctionSetting; Opening; RegionOverride; PrintLayer (`_build_effective` pipeline, network summary, routing layer); legacy pairwise LatticeInstance + Zigzag/WaveGenerator (API-only, no UI); legacy layer-wide corner_radius fallback
- `network.py` — derived wall-network topology: planar arrangement, region classification, attached-end joins (T / hub), bead survival, material components (regions with holes), junction corners + rounding
- `wall_lattice.py` — WALL infill: chordal-axis skeleton (own Delaunay), route-aware stitching motifs (single / loop / interleaved out-and-back), corner braces, cap V, junction hand-off, max unsupported distance
- `infill.py` — wide-region FIELD fallback (Delaunay web) + shared `_Region` / `_strut_ok` helpers; wall pattern list (zigzag / wave)
- `route_plan.py` — local continuity repair of the field fallback (toggle chains, supports, phase shifts, tier 3); closed vs open
- `solid.py` — SOLID infill: rectilinear / serpentine, boundary-contact turns, chain links, extra perimeters
- `route_quality.py` — geometric quality metrics: `quality`, `wall_metrics`, `solid_metrics`, `corner_support`, congestion, geometric retrace (used by tests)
- `app.py` — Flask app; API: POST /api/route, POST /api/effective_paths, GET /api/infill_patterns, GET /api/generators (legacy, unused by the UI)
- `static/index.html`, `static/app.js` — design canvas UI: shape tools, snapping, wall / network / relationship panels, infill (wall / solid), extra offsets, insets, openings, junctions, snapshot undo / redo, copy / duplicate / rotate, toolpath overlay + playback
- `tests/test_model.py` — model layer + geometry validation (incl. legacy lattice generators)
- `tests/test_app.py` — integration + workflow tests
- `tests/test_openings.py` (+ `tests/js/ui_openings_smoke.js`) — opening geometry, routing, lattice, caps, serialisation, JS parity, UI smoke
- `tests/test_networks.py` (+ `tests/js/ui_network_smoke.js`) — wall networks A–O, region overrides, API, robustness, UI smoke
- `tests/test_route_plan.py` — repair report (fallback), crossings, closed/open routes + layer alternation, WallSpec, NetworkWall, API
- `tests/test_route_quality.py` — physical route quality A–H, user vs generated junctions, voids/doorways, unavoidable cases, diagnostics
- `tests/test_infill_junctions.py` — region infill, per-source Corner R, junction corners, material inference, API
- `tests/test_wall_lattice.py` + `tests/wall_fixtures.py` — wall-lattice regression fixtures (straight / curved / dead ends / 4 & curved arms / star / loop / openings / narrow / unequal; corner fixtures), spacing sweep, coherence, wave
- `tests/test_pass5.py` (+ `tests/js/ui_edit_smoke.js`) — extra offsets, insets, rect rotation, region / void nesting, solid infill basics, UI edit smoke (undo / redo, clipboard, rotate, picker, relationships, Angle unit)
- `tests/test_pass7.py` — corner support, max unsupported distance, out-and-back density / cap V, solid boundary contact + serpentine, wall relationships, router travel pairing

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
- **New `_augment_by_retrace`**: open-trail route inspection — min-weight matching of odd nodes on SHORTEST IN-GRAPH path length, with two zero-cost dummy terminals so exactly two odd nodes remain as trail ends (a pinned odd start is forced to be one); matched paths' edges are duplicated. Travel only between components. *(SUPERSEDED in part, pass 7: odd ends of `travel_pairing` strands — solid infill — may be joined by an in-component travel.)* `label_passes` marks first traversal 'print', later ones 'retrace' (also re-run after reverse in app.py). `compute_metrics`: a run is continuous extrusion (print + retrace) broken only by travel; adds `retrace_moves`.
- **T-junctions in `build_graph`**: a strand vertex lying on another segment's interior (≤ `JUNCTION_TOL` 1e-6 in) splits that segment into a shared node (edges carry `sub_idx`). Case D did this by hand; design_proto lattices (zigzag/wave vertices on walls) never did, so lattice touching walls mid-segment was represented as disconnected (e.g. zigzag between two separate walls: 2–3 components before, 1 after). Near-misses (≥ 0.001 in) are not merged.
- UI badge "Continuous, with retrace" (1 component, > 2 odd nodes) with retrace length in the tooltip. *(Later: "Connected, N short hops" when solid travel pairing joins odd ends instead.)*
- Tests: `toolpath_proto/tests/test_retrace_routing.py` (junctions, near-miss, minimal retrace vs brute force, pinned start, disconnected still travels, Case E); design_proto `TestReportedRoutingCase`.

**Wall networks / printable regions (2026-10-05)** — implemented and tested (automated + headless-browser screenshots); NOT yet manually verified by the designer, NOT committed.

- **Principle**: geometry that physically touches or crosses is connected. Source geometry is never altered; the network is a DERIVED per-layer topology (`network.py`, stage 9 of `PrintLayer._build_effective`), never stored — so it follows geometry through future per-layer / keyframe changes.
- **Wall system** = source + its offsets (+ caps): one wall of thickness W (W = 0 → a single-bead "wire"). Its *band* = polygon between its extreme walls closed by its caps (closed systems: annulus). Systems whose beads touch/cross (contact ≤ 1e-6 in; lattice touching its OWN boundary walls does not count) form a network. Isolated systems are emitted exactly as before (tested byte-identical).
- **Bead classes**: FACE = a thick system's two extreme walls + caps (architectural faces); INTERNAL = intermediate walls, centre lines, lattice, cap extensions, junction connectors; WIRE = single-bead wall (always kept); VIRTUAL = non-printed region boundary (attached-end root lines, opening clear-void rings, open-lattice cavity closers).
- **Material (printable region)** = ∪ bands ∪ junction fill ∪ lattice cavities between *separate* systems (closed walls lying inside a cavity are islands = void) − opening clear voids; ± region overrides. All material boundaries are arrangement edges, so classification is constant per face (one ε-sample per face).
- **Arrangement**: all beads split at every proper crossing and vertex-on-segment touch (covers collinear overlaps → merged edges with several owners); nodes merged ≤ 1e-6; half-edge face tracing; holes assigned to the smallest containing face. **Regions** = faces joined across edges only internal geometry runs along (lattice subdivides a wall, it doesn't bound a region).
- **Survival**: FACE kept iff it separates wall from void (a face buried in the combined wall — host face across a T mouth, faces inside an X overlap, cap inside a host — is dropped); INTERNAL kept iff at least one side is wall; WIRE always; VIRTUAL printed iff it separates wall from void (e.g. a wider wall cut by a doorway gets a face along the doorway).
- **Attached ends** (`find_attachments`, only original free ends of THICK pieces): **T** = end lies on another system's wall bead (anywhere but that bead's own end) or inside another thick band → cap dropped; each face extended along its end tangent to the host's NEAR face (host face nearest a probe point inside the branch; falls back to the nearest point when a near-tangent ray misses); junction fill polygon root→hits→along near face (may be a bow-tie — even-odd, only rejected if it has no extent); internal walls run on to the next host wall behind the near face (ray, else nearest point). **Hub** = ≥ 2 thick free source ends at one point (corner, Y, X-of-ends): arms in angular order, consecutive arms' left/right faces mitred (bevel beyond `MITER_LIMIT` = 10 × W), hub polygon = wall. If a join can't be built the end keeps its normal cap and the union does its best.
- **Openings in networks**: opening pieces/caps unchanged; an end landing in an opening (no host material) is free → capped; each opening's CLEAR space (clear lines × outermost/innermost full walls) is void for every wall of the network (whole ring added as a virtual boundary). A wall narrower than a door passing through it touches nothing → separate (not carved).
- **End snap pre-pass**: open source ends within `SNAP_TOL` (0.001 in) of another source are projected onto it (float noise = contact); ≥ 0.001 in stays a gap (consistent with graph near-miss rule).
- **Canonical source polyline fix (changed)**: curves (Circle/Ellipse/QuadBezier) used to be ROUTED and fed to lattice at their 64-point default sampling while offsets/openings/canvas used the 128-point processed polyline. Now every consumer uses the processed polyline (`meta['pts']`); the parametric object is still emitted (identity preserved). Needed so junctions on curves agree everywhere.
- **Routing changes (toolpath_proto)**: (1) `build_graph` splits segments at proper X crossings (any geometry), not only T vertices. (2) `Strand.retrace_cost` (default 1.0) → edge `cost`; `_augment_by_retrace` matches on cost. Previous approach: unweighted retrace length — changed because it retraced visible faces when hidden internal geometry was available (brief: hide transitions inside the wall).
- **Results**: T of a ±5 branch on a room with inside offset → faces splice into one outline, centre line continues to the inner face → 1 run, 0 travel, 0 retrace. Dead-end double-wall branch folds into the outline (no retrace); wire dead end retraces its length (only option). X / Y / skewed L / curves on circles & Béziers / rounded rectangles: connected → 0 travel, retrace only on internal geometry. Disconnected networks still travel.
- **API**: `/api/route` and `/api/effective_paths` return `network`: `components` [{sources, lattices}], `junctions` (wall–wall nodes), `modified_sources` (sources the network trimmed), `regions` [{outer, holes, material, override, region}]. Network-trimmed source pieces have `treatment_id='network_src'`; join/virtual faces `treatment_id='network'`, role `cap`.
- **RegionOverride** (data model + API round trip; NO UI yet): `id, path_id, s, offset, kind ('wall'|'void')` — path-relative anchor (point at arc length s along the processed source, `offset` along its left normal), applies to the whole region containing it; follows the path when moved. The outside is never wall.
- **UI**: snapping (draw points, curve start/end, Line/Curve end handles, drawn-path points, body drag of open paths snaps its nearest end) onto source polylines, visible offset/cap faces and open ends; green ring + "connect" / "join end" label; Alt = free placement. Junction diamonds at wall–wall junctions; path-list badge `⛓ N1` per network; status line names networks; network-trimmed sources drawn from backend pieces (live source while dragging). Legend entry added.
- **Known limitations / unresolved**: (1) attachments are geometric coincidence only — moving a host does NOT carry attached ends (they detach); interpolated keyframes could likewise break contact mid-height. (2) No explicit "separate" override for touching geometry yet (graph merges by coordinates; needs node identity beyond coordinates). (3) Regions exist only inside networks (or walls carrying an infill); no paint-bucket UI. [Region-filling lattice: done 2026-10-05 — see "Wall-region infill" below.] (4) Overlapping bands without bead contact (a wall wholly inside another's cavity) are not networked. (5) Branch attached to a host's cap/offset END uses fallbacks (may keep its cap). (6) Self-attachment of one path is ignored. (7) FACE_RETRACE_COST = 3 is a guess. (8) Router still returns one solution (stages are separable; alternatives not enumerated).
- **Z/layer debt avoided**: topology recomputed per layer (no stored junction records); overrides path-relative like openings; openings' future z ranges simply include/exclude per layer and the network re-derives.

**Physical route quality: local infill repair, congestion rules, wall authoring (2026-10-05)** — *(local repair now applies only to the wide-region fallback; wall regions use the wall lattice)* — implemented and tested (automated + headless browser); NOT yet manually verified, NOT committed.

- **Infill as a structural field (`infill.py`)**: `build_web` = sample points (wall-face samples + interior hex points, forced samples at the fixed geometry's odd junctions) and candidate struts: the pattern's own Delaunay struts (`web.base`) plus SKIP struts (other valid struts ≤ 1.8 pitch, not printed by default). `select` = the printed default: base struts with DEGREE CAPS — ≤ 2 struts at a wall-face sample (one generated path), ≤ 4 at an interior point (two paths); over-capped points (corner fans, 6-way interior points) shed struts. `emit` chains / wave-smooths. Decision: generated geometry has at most two continuous generated paths through any exact point (user-authored geometry may meet at any degree).
- **Local repair (`route_plan.repair`)** replaces the return-path planner. Defects = odd junctions of (fixed geometry + selected infill). Each pair is fixed by TOGGLING web struts (remove a printed one / add a candidate), so every correction is an edit of the field: toggle chains (state Dijkstra; same-type steps penalised and capped; ≤ LOCAL_LIMIT 3 pitch) and short supports (straight, or bent once inside the wall to turn a corner; ≤ 3 pitch) = tier 1; pure alternating phase shifts (remove/add/…, strut counts unchanged — density-neutral, ≤ PHASE_LIMIT 20 pitch) = tier 2. All candidates go into ONE joint min-weight matching (max cardinality first) so a cheap local fix never orphans a defect only a longer edit can reach (this fixed the six-arm star). Tier 3 (interleaved return zigzag landing midway between existing samples) only for what remains. Every applied edit must keep degree caps, keep every strut cluster attached to a wall face, and pass the HARD congestion gate (≤ 2 generated beads within the clearance radius around anything added, incl. crossings); otherwise it is reverted. Unfixable defects become the open route's ends (2) and then exact retrace (last resort). `PrintLayer.return_paths` now means "infill repair on".
- **Lattice crossings**: lattice/infill crossing lattice/infill is structural, not a junction, and lattice–lattice contact no longer forms a wall network.
- **Route-quality diagnostics (`route_quality.py`)**: exact retrace (collinear overlap of moves, either direction), travel, print, generated length, correction added / removed, longest added correction strut, farthest edit from its defects, congestion = bead length inside a disk of CLEARANCE_RADIUS (2 in, provisional) / its diameter at every generated vertex and generated crossing (max generated, max total, hotspots > MAX_GENERATED_BEADS = 2), max generated junction degree. Exposed in `network.route_plan.corrections` (per correction: defects, tier, added, removed, max_segment, max_distance, edits).
- **Measured, old (return-path planner) → new (local repair)** — exact retrace / generated congestion (beads) / hotspots / generated degree / longest correction strut / correction material: rect + 1 dead end 0→0 / 2.04→1.0 / 1→0 / 4→2 / 19.5→18.0 / +96→+51−42; rect + 2 dead ends 0→0 / 2.04→2.0 / 2→0 / 4→2 / 19.5→31.6 / +136→+121−71; ring + zigzag 0→0 / 2.0→1.0 / 0→0 / 4→2 / 15.4→18.0 / +61→+54−42; two voids zigzag 0→0 / 4.0→2.0 / 51→0 / 8→4 / 51.4→38.1 / +502→+285−101; two voids wave same as zigzag (54→0 hotspots); six-arm star 0→0 / 2.97→2.0 / 4→0 / 4→2 / 56.5→26.2 / +208→+334−148 (but the star now prints NO centre lines: total print 3222→2590); nearby branches 0→0 / 2.13→2.0 / 2→0 / 4→2 / 16.0→27.2; narrow wall 0→0 / 2.5→1.0 / 3→0 / 5→2 / 8.6→0 (no edits needed). All 1 run, 0 travel. Phase-shift edits (tier 2) reach up to ~45 in from their defects (whole dead-end arms) — reported as `max_distance`; corrections are mostly tier 1.
- **Centred wall reference not printed (decision)**: WallSpec / NetworkWall `print_reference` (default False): a CENTRED wall's reference path is construction geometry (drawn dashed in the UI; `network.reference_only`), because a bead down the middle crosses every infill strut (local mud build-up) and adds odd junctions. Inside / outside / left / right walls keep the reference as a printed face. Explicit Extra Offsets keep the old behaviour (source printed). "Print reference line" opt-in restores a printed centre line (its user-authored junctions stay legal at any degree).
- **Junction-arc precision fix**: trimmed faces now start exactly at the fillet arc end (a float gap left an outline open).
- **Wall authoring UI**: path Properties start with a prominent name header; Wall section: "Wall Thickness" + "Wall Alignment" (closed: Inside / Centered / Outside, open: Centered / Left / Right; default Inside / Centered) + "Print reference line" for Centered; a networked path without its own wall shows "Wall Thickness N in · … — inherited from network N1" + "Override for this path"; an overriding path offers "Use network N1 wall instead". Network panel: "Network Wall Thickness" + "Wall Alignment (Default / Centered / Inside / Outside)" + who overrides it. Extra Offsets moved into a collapsed "Advanced: Extra Offsets" section with an explanation. Path identification: a custom path picker (Extra Offset source) — hovering / arrow-keying an item highlights that path on the canvas (yellow glow + transient name label); path-list hover and the properties header highlight too; no permanent canvas labels. Routing: "Infill repair" toggle; status shows local infill edits.
- **Remaining unavoidable cases**: a single-bead (no-thickness) dead end retraces its length (no material to edit); a wall without infill whose faces are separate loops travels between them (no hidden bridge is invented — possible future option); two overlapping user-requested legacy lattices (pairwise) are not a repairable field: their own landing points stack (congestion reported, ~2.9 beads); wide regions get a sparser, irregular web than the old 6-way triangulation; wave infill becomes straight where struts meet interior points or would leave the wall; closing the loop (start = end) is chosen less often now (closing must also be a cheap local edit).

**Route planning, parametric walls, network-level walls, interactive shape tools (2026-10-05)** — the return-path planner described here was SUPERSEDED the same day by "Physical route quality" above (kept for history). — implemented and tested (automated + headless-browser); NOT yet manually verified, NOT committed.

- **Principle shift**: from "generate fixed lines, then traverse the graph" to "generate a printable region and DESIGN a continuous route through it". Visible faces are sacred; the hidden interior (infill, centre lines' surroundings) is a flexible routing field.
- **Route planning (`route_plan.plan_returns`, stage 10 of `_build_effective`)**: runs wherever wall-material regions exist (network components and walls with infill; isolated walls without infill are untouched). Per graph component: odd junctions (what the router would retrace) are paired by min-weight matching (candidates: each junction's 8 nearest by in-graph cost, widened if pairing is incomplete); each pair becomes a NEW hidden return path: a straight segment if it lies inside one material region and overlaps no existing bead, else a nearby copy of the shortest in-graph route offset 3 in (then 1.5, 5) sideways — a "tent" over a single edge. Return paths are DerivedPaths role 'lattice', treatment 'return_path', class INTERNAL. Unrealisable pairs are left to the router's exact retrace (last resort). `PrintLayer.return_paths` (default True) turns it off.
- **Closed vs open**: all junctions paired → closed loop (start = end; next layer starts right above); chosen when it costs ≤ `CLOSE_MAX_EXTRA` = 5 % of the component's print length more than leaving one pair as the open route's ends. `PrintLayer.prefer_closed` (default True). Otherwise the open route's two ends are reported for alternation.
- **Multi-layer readiness (graph.py)**: `RouteEnds(start, end, closed)`, `route_ends(moves)`, `reverse_route(moves)`, `orient_for_previous(moves, prev_end)` → (oriented moves, XY transition). `/api/route` returns `route_ends`. No Z/layer system built.
- **Structural crossings (decision)**: `Strand.kind` = 'face' (visible / single bead; default) | 'internal' (hidden walls: centre lines, intermediates, joins) | 'field' (infill, lattice, return paths). Where a FIELD strand crosses another hidden strand it is a structural crossing, NOT a graph junction (the nozzle passes over); all other crossings still split (source-wall rule unchanged); T touches always join. Trade-off: in the opt-out mode (return_paths off) the fallback retrace can no longer shortcut through lattice/centre-line crossings, so a little face retrace can appear there.
- **Measured (old = return_paths off → new)**, exact retrace / geometric overlap / added print, all 1 run 0 travel: two holes zigzag 451.6 → 0 / 0 / +502 (closed); two holes wave 451.6 → 0 / 0 / +502 (closed); six-arm star + infill 137.7 → 0 / 0 / +208 (closed); six-arm star plain 750 → 0 / 0 / +485 (open); rect + 1 dead-end branch + infill 70.7 → 0 / 0 / +96 (closed); rect + 2 dead ends + infill 111 → 0 / 0 / +136 (closed); rect + 2 dead ends plain 90 → 0 / 0 / +90.5 (open). Planning time ≤ ~250 ms on these.
- **Parametric wall thickness (decision)**: the existing OffsetTreatment already IS the parametric relationship (regenerated from its source on every edit); what was missing was the wall concept. `WallSpec(thickness, align)` on a source path (`Path.wall`) GENERATES its offsets (`{id}.wall` or `{id}.wall+` / `{id}.wall-`): align auto (closed → inside, open → centre) / inside / outside (by winding) / left / right / center. Resizing/moving/reshaping the source keeps the wall exactly that thick. Raw OffsetTreatments remain as "Extra Offsets (advanced)". Not a constraint solver; an independently drawn inner shape cannot (yet) be bound to an outer one — the derived boundary is the supported form.
- **Network-level walls (decision)**: `NetworkWall(id, path_id, thickness, align)` = wall default for every path of the SOURCE network containing path_id (paths whose source geometry touches / crosses, computed before offsets: `network.source_networks` in the summary, N1…). A path's own WallSpec wins; paths joined later inherit; anchor deleted → re-anchored to another member. UI: "Wall network N1" panel under a networked path's Properties (members, Wall (all), Align (all), + Add infill to network). Path badges / infill labels now use source networks.
- **Interactive shape tools**: Line / Rect / Circle / Ellipse are tools (two clicks or press-drag-release; points snap; Esc cancels; back to Edit with the shape selected); nothing is created until placed. Curve unchanged (3 clicks). `addPrimitive()` kept for programmatic use. Default labels auto-numbered per type (Line 1, Rect 2 …), user labels untouched.
- **Routing UI** *(SUPERSEDED: the toggle is now "Infill repair" and only affects the wide-region fallback; return paths no longer exist)*: Routing section toggles "Return paths" and "Closed loop"; status shows closed / open and the number + length of hidden return paths; return paths drawn pink (legend).
- **Limitations**: return paths are straight / offset polylines (wave infill does not curve them); planning cost grows with odd-junction count; returns may cross several infill struts (structural crossings); closing threshold (5 %) and offsets (3 / 1.5 / 5 in) are guesses awaiting bead-width data; a component whose odd junctions are only reachable through single-bead walls still retraces; separate printed components inside one material region (e.g. a ring's inner and outer face without infill) are NOT bridged automatically (possible next step).

**Wall-region infill, per-source Corner R, junction corners (2026-10-05)** — *(the Delaunay field described here is now only the wide-region fallback; wall regions use the wall lattice)* — implemented and tested (automated + headless-browser screenshots); NOT yet manually verified, NOT committed. Triggered by manual testing of wall networks: Boundary A/B lattice produced starbursts across networks and ignored all but one hole; a global Corner R mixed source corners with network junctions.

- **Lattice model changed (decision)**: lattice is no longer "between Boundary A and Boundary B". It is `RegionInfill(id, path_id, pattern, params{spacing}, variation_index)` — infill of the printable WALL REGION. Pipeline: source → offsets / faces → wall network → material region (with holes) → infill → routing. Previous approach: `LatticeInstance` pairing two boundary paths (still supported by the backend for old payloads/tests, output unchanged, but no longer created by the UI). Changed because pairing arbitrary paths is meaningless once walls splice into networks (starbursts across voids; a region with several holes can't be described by two boundaries).
- **Ownership / anchoring**: an infill is anchored to a source path (path-relative, like openings) and fills every material region bordering that path's walls (whole connected network, incl. branches; every piece if openings split it). First infill reaching a region wins; later ones report `shadowed`. Anchor deleted → infill deleted. Isolated walls with an infill go through the network machinery too (walls without infill are untouched).
- **Material regions & holes**: `network.material_components` = connected unions of material faces of the classified arrangement (joined across edges with wall on both sides); each has boundary RINGS with material on the left — one outer ring + any number of holes (rooms, islands, doorways via opening voids). Infill uses these rings directly, so 0, 1 or N holes are the same code path.
- **Material inference (unambiguous cases automatic)**: thick wall bands (+ junction fill) are wall; rooms inside a thick ring are void; opening clear width is void. A closed SINGLE-BEAD wall carrying an infill declares its inside wall, with every closed wall inside it a hole (even-odd: an island inside a hole is wall again). Open single-bead paths never bound material (infill crosses them as junctions).
- **Generator (`infill.py`)**: rings sampled at one global pitch (`spacing`); sharp turns (> 35° within half a pitch — sharp vertices and small junction fillets, not large Corner R arcs or circles) are always samples; each run between corners chooses the offset that best STAGGERS it against existing samples (facing walls alternate → zigzag, not rungs); interior points on a global hex grid where the region is wider than the pitch; Delaunay (Bowyer–Watson, deterministic jitter); a triangle edge is a strut iff not a boundary piece and entirely inside the material (spatial-indexed crossing / inside tests). Struts chained into polylines. Patterns: `zigzag` (straight struts) and `wave` (each strut a Hermite curve tangent to the walls; straight where a curve would leave the wall). V1/V2 = half-pitch phase shift. **Phase ownership**: one pitch and one phase per infill = per region; no restart per branch.
- **Routing**: unchanged. Infill is INTERNAL (retrace cost 1); struts land on boundary samples on the faces (graph T junctions); in bands boundary nodes are even-degree, junction zones add a few odd nodes → retrace on internal geometry. Connected → zero travel (tested incl. 1–5 holes).
- **Corner R per source (decision)**: `Path.corner_radius` (Rect / drawn paths in the UI, in the path's Properties next to X/Y/Width/Length); fillets only that path's original corners; offsets still generated from the processed (rounded) source. Previous: layer-wide `PrintLayer.corner_radius` (kept only as a legacy fallback when a path's value is None; removed from the UI).
- **Junction corners (decision)**: corners CREATED by the network — where the printed faces of two different wall systems end at one boundary node (T splices, X crossings, hub mitres, inner corners) — are a separate concept from source corners and never take a source's Corner R. Treatment: layer default (`junction_style` 'miter' | 'round', `junction_radius`) + per-junction `JunctionSetting(key, treatment, radius)`. Rounding (`network.round_junctions`): tangent arc of radius r (clamped to 45 % of either leg), the two face chains trimmed, a FACE bead `junction:<key>`; the same fillet is applied to the material rings so infill lands on the rounded wall. Default 'miter' = previous output exactly.
- **Junction identity (temporary, documented)**: key = the two wall FACES that meet (source / offset ids, joins mapped to the face they extend), sorted, + `#ordinal` among corners of that face pair ordered by position. Survives moving/reshaping as long as the same faces meet; if topology changes the setting no longer matches (falls back to the default; the override stays in the data and re-applies if that junction reappears). Ordinal can swap if two junctions of the same face pair cross over in position. Compatible with persistent attachments: an attachment id can later replace / prefix the face pair. Internal junctions (e.g. a centre line meeting a face) are markers but not corners (no treatment).
- **UI**: INFILL section replaces "Connecting Geometry" (+ Add Infill → the selected path's wall region; label "Fills wall network N1 (…)" + status: ok / shadowed / no wall material); Pattern, Spacing, Variation. Corner R row in Rectangle / drawn-path Properties. WALL GEOMETRY: Junctions Miter/Rounded + Junction R (default for all). Click a ◆ junction diamond (filled = corner, hollow = internal) → Properties "Junction": Treatment Default / Miter / Rounded + Radius. `/api/infill_patterns`; network summary `junctions` are now objects {x, y, key, corner, treatment, radius, override}; `infills` [{id, regions, holes, shadowed_by, status}].
- **Ambiguities (require a user decision eventually, via RegionOverride / a paint tool)**: (a) nested closed single-bead walls with an infill are read even-odd — if an inner loop is meant as a free-standing thin wall inside solid material (not a hole), the software can't know; (b) a lone closed single-bead wall with an infill becomes a solid slab (only reading available); (c) thick walls: the room inside a ring is always void — a filled room needs an override; (d) a single-bead wall lying inside wall material is crossed by infill rather than treated as a boundary.
- **Limitations**: region summary (`network.regions`) is classified before junction rounding (fillet slivers not reflected); where stagger is impossible (e.g. very short runs, uneven face lengths) the web has some rungs / extra odd nodes; spacing is one global pitch (not adapted to wall width); legacy pairwise lattice remains in the backend; infill is not automatic — each network needs one added.

**Structural infill rules — pass 7 (2026-10-05)** — implemented and tested (automated + headless browser); NOT yet manually verified; committed in checkpoint 414c283. Principle: PATTERN PARAMETERS ARE PREFERENCES; STRUCTURAL SUPPORT AND TOPOLOGY ARE CONSTRAINTS.
- **Wall-lattice priority order (decision, encoded in wall_lattice.py + tests):** (1) never enter openings / voids, (2) never exact-retrace interior lattice, (3) positively support corners, junctions, dead ends / caps, (4) never exceed the maximum unsupported distance if avoidable, (5) no congestion hotspots, (6) continuous printing, (7) a coherent repeated motif, (8) approach Target Spacing, (9) visual regularity.
- **Motif vocabulary:** ordinary stitch · CORNER BRACE · junction hand-off · CAP V (dead-end turnaround / open-end support). A run is laid out in SEGMENTS between fixed support points (junction ends, dead ends, corners); each segment gets its own whole stitch count.
- **Corners (decision):** detected along each run where the smoothed centre line turns ≥ 30° within 2 thicknesses (a 140° interior corner turns ~35° there; gentle curves ~10°). Corner points: INNER = boundary point nearest the corner on the inside of the turn (reflex vertex / inner arc apex), OUTER = boundary point farthest along the corner bisector (convex vertex / outer arc apex); bisector from the inner face either side of the inner point. Single-pass runs: the corner is a double station — arrive at one corner point, a BRACE diagonal to the other, continue (one extra stitch; parity accounted). Two-phase runs: each phase lands one corner point; the start side is chosen by measuring both options (hotspots, tiny cells, multiplicity). Corner landings do not move with Target Spacing. Closed-loop seam: corner window wraps.
- **Maximum unsupported distance (decision):** the longest distance along the wall between consecutive lattice supports (landings on either face). Default 1.375 × target (16 → 22 in); wall-infill param `max_unsupported` (UI: Advanced, 0 = auto). It wins over target spacing: segment counts are raised (and stations redistributed) until no real centre-line gap exceeds it — never a tiny extra stitch. Report: `max_unsupported`, `max_unsupported_limit`; UI "Actual: a–b in · Max unsupported: x in (limit y)".
- **Out-and-back density (decision, changed from pass 6):** Target Spacing = COMBINED density. The two phases interleave on one station grid (each at ~2 × target, offset by one station), so supports are ~target apart and wall crossings ≈ length / target. Pass 6 used two complementary passes at the target (every station landed on both faces) = twice the crossings — changed because the user's spec defines target as the combined density. Trade-off (reported): with interleaving, each face's landings come in pairs (gaps alternate ~S and ~3S); bead LENGTH is still ~1.8 × a single zigzag because an out-and-back must traverse the arm twice.
- **Turnaround / caps (decision, changed from pass 6's square rung):** CAP V — the last station is landed on the cap face (centre line carried on to the boundary from mid-wall), joined to both phases' last landings (odd count: symmetric, both phases end at the last station; even: one phase one station earlier). The cap landing is within t/2 of both cap corners. Lone walls get a cap V at both ends (stay OPEN single zigzags; not doubled).
- **Solid infill (rewritten, decision):** lines clipped EXACTLY to the region; every turn between consecutive lines is a V (rectilinear) or a smooth U (serpentine) whose apex lands ON the boundary (outer or void) — a turn adds degree 2 (even), so boundaries are braced without routing defects; chain ends land on the boundary; nearby chain ends are joined by the same V/U link; untouched voids get one turn redirected onto them. Leftover odd ends: the ROUTER pairs them by a short TRAVEL instead of retracing the perimeter — `Strand.travel_pairing` (set only for solid strands; graph.TRAVEL_W = 1: travel when shorter than the weighted retrace). Every other strand keeps the old retrace behaviour (wire X junctions etc. unchanged). Turn pull-back ≤ 30 % of the shorter line; U only where lines are ≥ one wavelength long and the U never reverses (else V).
- **Solid 'serpentine' pattern** (named distinctly from wall 'wave' — the UI keys patterns by name): parallel serpentines, amplitude 0.22 × spacing, wavelength 2.5 × spacing, one global phase (uniform gap), tapered to straight at the boundary; U turns. Rectilinear kept. Spacing remains a target.
- **Wall relationship (decision):** `WallRelation(outer_id, inner_id, thickness, driver)` — WALL/REGION semantics, not geometry: the dependent boundary is recomputed from the driver every evaluation (backend authoritative, JS mirror for live drags and every scheduleRefresh). Rect↔rect (every side t apart, same centre / rotation, sharp↔sharp, rounded R↔R ± t), circle↔circle (concentric, exact), ellipse↔ellipse (rx/ry ± t: exact on the axes; actual gap range reported). Anything else refused ('unsupported'); too thick → 'thickness too large'. Driven boundary: no handles, not draggable/rotatable. Properties: Outer / Inner (drives), Thickness, Driver, Break (keeps geometry). Deleting a boundary removes the link. Undo/redo by snapshot. Inset / Outset unchanged (general CAD operation).
- **UI:** Angle / Rotate show ° (addPropRowNum had hard-coded "in"); V1/V2 hidden where they do nothing (wall networks: `lattice.variation_effective`); wall-infill Advanced → Max unsupported.
- **Wall vs area classification:** still the PROVISIONAL heuristic local thickness ≤ 1.6 × target (wall_lattice.WIDE) — not the definition of a wall; to be replaced by a medial-axis / opposing-face classification.
- **Metrics (route_quality):** `corner_support` (sharp boundary vertices → nearest landing), `solid_metrics` (contacts / longest unsupported stretch per ring, void violations, longest connector, runs, travel), `HOTSPOT_TOL` 0.02 (curved beads crossing measure ≤ 2.007; a third bead adds ≈ 0.5), faces separated by corners ≥ 60° (FACE_CORNER) for the one-face test (a leg tying a side face to the cap face crosses faces).
- **Results:** 18 wall fixtures × 7 spacings (12–24 in): 0 interior retrace, 0 tiny loops, 0 hotspots, 100 % cross-face stitches, max unsupported ≤ limit, every sharp corner / cap within one thickness of a landing (wall corners landed exactly; before: 11.6–16.7 in away). Solid fixtures (rect, ellipse, voids, multiple voids, nested island × rectilinear / serpentine): 0 void violations, 0 retrace, 0 hotspots, every boundary contacted.

**Wall lattice (motif-based, route-aware) — 2026-10-05** — implemented and tested (automated + headless browser); NOT yet manually verified; committed in checkpoint 414c283. *(Out-and-back density, turnaround and wave profile below were SUPERSEDED by pass 7 — see above.)* `wall_lattice.py`, used for every WALL infill region whose local thickness ≤ 1.6 × target (wider regions = areas → old field generator + repair as fallback).
- **Where the old bow ties / boxes / retrace came from (investigated):** (a) generation — each face run sampled independently at the exact pitch + Delaunay + degree-capped `select` left odd nodes at arm ends, junctions and remainders; (b) `route_plan.repair` then toggled struts: removing a diagonal merged triangles → boxes; adding skip struts across existing ones → bow ties; bent supports → diamonds; tier-2 alternating chains → abrupt phase switches mid-run; each arm repaired independently → different strategies per arm; (c) whatever parity was left (dead-end arms always end odd) → router exact retrace. Measured before/after on the regression fixtures (tests/wall_fixtures.py).
- **Skeleton (chordal axis):** densely sampled wall rings (h ≈ thickness/3) → own incremental Delaunay (no scipy; walking Bowyer–Watson, super triangle 1000 × span — 50 × left hull triangles missing) with conforming refinement → inside triangles classified by internal chords: 1 = wall END, 2 = SLEEVE, 3 = JUNCTION. Sleeve chains = wall RUNS (each chord spans face to face → exact face correspondence on curves). Short side branches (< 1.2 t; convex-corner noise) pruned; junctions joined by runs < t merged into one CLUSTER. Centre line = smoothed chord midpoints. Face point of a station = nearest point of that side's face to the centre-line point (side and ±45°-of-normal constrained, so never the cap / far face); junction run ends use the exact corner vertices.
- **Motifs:** a PASS is a zigzag/wave landing alternately on the two faces at evenly distributed stations; interior landings add degree 2 (even), so layer parity depends only on pass ENDS. Through run between junctions → 1 pass; closed loop without junction → 1 circulating pass, even N; dead-end arm → OUT-AND-BACK: 2 passes in complementary phase + a square turnaround rung half a thickness before the cap (every station landed on both faces, no interior retrace, returns to its junction); lone run (no junction) → 1 pass, open route (decision; see Open Questions). *(SUPERSEDED, pass 7: out-and-back = interleaved phases at combined target density joined by a cap V; no rung.)*
- **Network coherence:** passes per run chosen for the whole network by route inspection on the skeleton (min-weight matching of odd junctions, doubling the cheapest runs; dead ends always doubled when closed routes are preferred) → equivalent arms get the same motif. Junctions: pass ends pair up at shared corners (min-weight matching per cluster; single-pass runs choose start face + stitch-count parity jointly, brute force ≤ 6 runs else coordinate descent); rounded corners: both passes meet at one point just inside the fillet (moved off the arc only as far as needed for straight stitches to stay inside); fallback connector only when no corner pairing exists. Stitches touching a junction are straight. Nothing is generated in the junction centre.
- **Target spacing:** N = nearest admissible integer to U / S (parity only when a junction or loop requires it), actual pitch = U / N exactly (91 in @ 20 → 5 × 18.2). U = station parameter: ½ centre-line length + ½ progress of the SHORTER face, so tight bends / reflex corners get fewer, wider stitches (no inner crowding). Report: `network.lattice[id].regions[].runs` (motif, passes, stitches, pitch, length, ends), `infills[i].lattice` (pitch_min/max) → UI "Target Spacing" + read-only "Actual: a–b in".
- **Wave:** same machinery, stitch weight (1 − cos πf)/2 (tangent to the faces), complementary phases; junction stitches straight. *(SUPERSEDED, pass 7: profile = 0.4 straight + 0.6 sine (leaves faces at an angle); out-and-back phases interleave.)*
- **Repair (route_plan) now only for fallback (wide) regions.** `network.route_plan` keeps its shape for motif regions (0 defects, 0 edits, closed components from the motifs, `motif_regions`). The "Infill repair" checkbox only affects fallback regions. Variation V1/V2: flips the phase of loops / lone runs; in networks the phase is fixed by junction coherence (old test updated).
- **Metrics (route_quality.wall_metrics, geometric):** interior retrace (overlap with printed field beads), stitches split at landings on printed geometry with cross-wall vs one-face (printed boundary path ≤ 2 × chord and < 120° turning), tiny LOOPS (arrangement cells < 0.1 × t × S whose perimeter is ≥ 60 % generated — a sliver between a lattice turn and a face is not a loop), start/end distance, runs, travel, congestion (hotspot tolerance +1e-3: two curved beads crossing measure 2.000006).
- **Results (fixtures, target 20 / star 16):** every fixture 0 interior retrace (before: four curved arms 90.8 in; four arms @12 in 397 in; curved arms @24 in 189 in), 100 % cross-wall stitches (before 0.83–0.99), 0 tiny loops (before up to 6), 0 hotspots, networks start = end (before 70–203 in apart), one motif type across a 12–24 in sweep (before 9/21 sweep points clean, now 21/21). Lone walls / openings: open routes (openings: 2 separate pieces, travel 35 in vs 24 before).
- **Limitations:** wide regions (thickness > 1.6 × target) still use the old field + repair; junction-adjacent wave stitches are straight; stitch count steps by 2 on closed loops (even N); lone wall runs are single-phase open routes (not doubled to close); parity-constrained single runs may shift ±1 stitch; very short runs get 1 stitch; performance ~0.1–0.3 s per network (Python Delaunay).

**Design-model pass (2026-10-05): undo, transforms, region/void, insets, WALL vs SOLID infill.** Three layers are kept distinct: DESIGN GEOMETRY (source paths incl. parametric InsetPath children) → WALL / REGION SEMANTICS (WallSpec faces, RegionInfill kind + region + voids, openings) → TOOLPATH (faces, wall infill + local repair, solid infill, perimeters, travel). Extra Offset (an extra printed bead), Wall Thickness (wall faces), Inset (new design geometry) and Solid Infill (area fill) are separate data structures.
- **Extra Offset source bug (fixed)**: "+ Add Offset" always used `source_paths[0]` whatever was selected, so every new offset landed on the first path (looked like "only one path can own an offset"). Now the SELECTED path; direction defaults left/right for open paths and is re-validated when the source changes. The path picker also reads its candidates when it OPENS (paths drawn after the panel was built are offered) and hovering no longer rebuilds the hovered node. Backend already supported any number of offsets per source / several sources.
- **Undo / redo (decision: snapshot history)**: `_hist` = stack of serialised `layer` states (+ selection). Every edit funnels through `scheduleRefresh()` → `historyCheckpoint()`, which records a step only if the serialised layer changed; no checkpoint while a pointer gesture is active (handle / body / rotation / opening drag), so a drag = one step; a new edit clears redo. Undo restores the WHOLE snapshot, so anything derived from geometry (snap connections, networks, junctions, inset shapes, infill regions / voids) is restored with it — no per-operation inverses. Cap: 2000 steps / 64 MB of snapshots (oldest dropped). Cmd/Ctrl-Z, Cmd-Shift-Z (and Cmd-Y); not while typing in a field (native field undo). Toolbar Undo / Redo disable when unavailable. Backend-evaluated inset shapes update the current state without creating a phantom step. Clear All is undoable.
- **Transforms**: `_transformPath(path, map, dAngle)` maps any source through a point map (translate / rotate now; scale / mirror later). Parametric identity kept: RectanglePath gains `rotation` (radians about its centre; quarter turns swap w/h and stay axis-aligned; rotated rect handles resize in the rect's own frame), Ellipse adds to `rotation` (handles now follow it), Circle moves its centre, lines / curves / drawn paths map points. Pivot: a primitive's centre, else bbox centre (line midpoint). UI: Properties "Rotate" (degrees, ↺ / ↻) + "Angle °" for rect / ellipse; a ◯ rotation handle above the selection (Shift = 15° steps).
- **Copy / paste / duplicate / delete**: Cmd-C / Cmd-V / Cmd-D / Delete-Backspace, toolbar Duplicate. A copy = new id + next auto label (Line 2 …; custom labels get " copy"), offset (+10, −10) per paste; wall spec and Corner R travel with it; its ends are ordinary snap targets / snapping handles. A copy of an inset is frozen ordinary geometry.
- **Region / void semantics (explicit)**: `RegionInfill.path_id` IS the region boundary; the UI shows "Region: X · Voids: Y, Z (· Islands)" and a Region picker. Default "+ Add Infill": the selected path (explicit wins — an inner shape is filled), else the OUTERMOST closed boundary. Nesting is GEOMETRIC (`PrintLayer._nesting`: smallest containing closed path, sampled point-in-polygon; not creation order); voids = closed paths whose parent is the region, islands = closed paths inside a void (even-odd → material again — now actually FILLED: declared regions also own nested material components; the pass-2 test that pinned `regions == 1` was updated).
- **Parametric inset / outset (decision)**: `InsetPath(parent_id, distance, mode inset|outset)` is a real closed SOURCE path whose shape is recomputed from the parent's processed polyline every evaluation (`_resolve_insets`: offset + trim, orientation-independent, chains of insets allowed, cycles → no shape, too large → no shape). It can carry a wall thickness, bound a region or be a void. Frontend previews it (miter offset) while the parent is dragged; backend returns exact points in `network.derived_sources`. No handles / not draggable on its own; "Detach" turns it into a plain closed path. **Parent deletion DETACHES children** (keeps their current shape) rather than cascading — never silently deletes derived geometry; undo restores the link. Backend with a missing parent keeps the frozen points.
- **WALL vs SOLID infill (decision)** *(solid algorithm SUPERSEDED by the pass-7 rewrite: boundary-contact turns, serpentine pattern)*: `RegionInfill.kind` 'wall' (default for old payloads; zigzag / wave field between faces + local repair — unchanged) | 'solid' (`solid.py`, pattern 'rectilinear', spacing / angle / perimeters). The UI defaults closed single-bead boundaries to Solid and walls with thickness to Wall (Type select to change). Solid: region + void boundaries are the first perimeter (extra perimeters inward at 6 in), parallel lines at `angle`, `spacing` apart clipped even-odd (voids empty) with a 0.3·spacing margin, boustrophedon chains (end-to-end joins along the boundary), void / inner-perimeter loops joined by bending ONE nearby turn to touch them (p1→q→p2: parity-neutral, no travel), remaining pieces linked by short straight connectors (≤ 2·spacing, inside material) else a new trail (short TRAVEL — never a long connector). Field lines are never moved. Emitted as DerivedPaths treatment `solid_infill` / `solid_link` / `solid_perimeter` (strand kind field / internal). Report in `network.infills[i].solid`.
- **Limitations**: chains split by a void need travel (closed-loop parity makes bridging cost retrace); travel ORDER between components is the generic router's nearest-neighbour (e.g. two voids: ~450 in vs ~200 in possible); only rectilinear solid (zigzag-as-pattern is the boustrophedon itself; no gyroid / concentric); single selection only (no multi-select / group transforms); copy does not copy attached offsets / infills; inset preview in the browser is a miter offset (exact shape arrives from the backend ~200 ms later); the inset-too-large case shows no shape with a warning.

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
- **Medial axis / chordal axis**: wall-lattice skeleton — implemented as a chordal-axis transform over a constrained Delaunay of the wall rings (`wall_lattice.skeleton`); a medial-axis wall / area classification is still future
- **Offset curves**: generating inner wall from outer wall
- **Catmull-Rom splines**: curved drawn paths (through-points, simpler than Bezier)
- **NetworkX**: graph construction, matching (`nx.min_weight_matching`)

---

## Open Questions

- How should topological transitions (cell count changes, path splits/merges) be represented?
- How should the lattice generator accept continuity hints without violating geometry/toolpath separation?
- How should seam position be managed and optimized across layers?
- What is the right output format for the Pi Interface to consume?
- Is retracing (printing a second bead over an existing one) physically acceptable for mud? Now a last resort (wall lattices avoid it by construction; solid infill uses short travel instead); still needs machine testing for the remaining single-bead cases.
- Bead width / depth: what CLEARANCE_RADIUS (2 in provisional), degree caps and MAX_GENERATED_BEADS make physical sense? What closing threshold?
- *(ANSWERED by the wall lattice + pass 7: dead-end arms get an interleaved out-and-back at combined target density with a cap V.)* Odd dead-end infilled arms need either a route end or an arm-length edit — is a density-preserving out-and-back pattern preferable?
- Should hidden bridges automatically connect separate printed components lying in one material region (e.g. inner + outer face of a ring without infill) to avoid travel?
- Layer planner: closed loops vs alternating open routes across Z — when is each preferable physically (seam stacking)?
- Should opening positions be measured on the unrounded source so Corner R changes don't shift them?
- Wall networks: should snapping create persistent ATTACHMENT constraints (end of P on Q at s) so moving a host carries its branches and keyframe interpolation can't silently break a junction? (Currently derived from coincidence only.)
- How should the designer explicitly SEPARATE touching geometry (and how does the routing graph represent two beads touching without joining)?
- *(First half ANSWERED: wall regions now use wall-following chordal-axis stitching motifs.)* Is a paint-bucket wall/void UI needed in practice for the ambiguous cases listed under "Wall-region infill"?
- Should every wall network get infill automatically (layer default) rather than per-network "+ Add Infill"?
- Out-and-back: interleaved phases (combined density = target, per-face landings paired S / 3S) vs complementary phases at 2 × target (even per-face support, both faces landed every 2S) — which reads better and prints stronger?
- Corner brace on single-pass rings adds one stitch per corner; is a brace diagonal preferred to a V apexed at the corner?
- Wall relationship: should ellipse pairs use true offset curves (an InsetPath-like dependent) instead of rx/ry ± t?
- Wall lattice: should a lone wall run (no junction) be doubled (out-and-back X lattice, closed route) or stay a single open zigzag (current)? *(Density half ANSWERED in pass 7: Target Spacing is the combined density — see the interleaved vs complementary question above.)* Is 1.6 × target the right wall/area threshold (provisional heuristic)?
- Solid infill: are rectilinear lines at 45° right for mud slabs, and what spacing / margin / extra-perimeter spacing match the bead? Should consecutive layers alternate the angle (layer planner)?
- Parent deletion detaches insets (current) — would the designer rather cascade-delete with a confirmation?
- Should travel order between disconnected components be optimised in the router (shortest tour) rather than nearest-neighbour?
- Junction identity: replace the face-pair key with persistent attachment ids once attachments exist.
- Is retracing a visible face 3× worse than retracing internal geometry the right trade-off physically?
- Should the router expose several valid routes (different pairings / starts) for the designer to cycle through?
- Does the keyframe model produce the forms the designer imagines? (Phase 1 will answer)

---

## Next Steps

0. Designer manual verification in the browser of everything since wall networks (all in checkpoint 414c283): pass 7 (corner braces, max unsupported, out-and-back density, cap V, solid boundary contact + serpentine, wall relationships, Angle unit), the wall lattice (Target Spacing sweeps, dead-end out-and-back, four straight / curved arms, junction congestion, openings clear) and the design-model pass (offset sources, undo / redo, duplicate-rotate-snap, region / voids, insets, wall vs solid).
0b. Deferred housekeeping from the checkpoint audit (each its own reviewed pass): make the vacuous `return_path` test helper real; SolidPlan.connectors / solid_link leftovers; request sequencing in the UI; `~` in path ids; exact-float matching; history / restore gaps; inset previews after sidebar edits; cap-style alias mismatch; float(None) payload robustness; the unused routing-graph build; geometry-helper / tolerance consolidation; splitting `wall_lattice.plan()` and `_build_effective`; feature-named test files.
0c. Known issue (deferred, do not fix without a reviewed pass): the order of the wide-region fallback's `route_plan.corrections` diagnostic list is non-deterministic. The same entries come out in a different order depending on unrelated prior process state (likely iteration over an object-identity set / dict). Geometry and toolpath are unaffected. Confirmed present in the unmodified checkpoint 414c283; not introduced by the cleanup.
1b. Multi-select + group transforms; copy attached offsets / infills with a path; travel-order optimisation.
2. Calibrate CLEARANCE_RADIUS / degree caps / limits from real bead width.
3. Persistent attachment constraints; then the Z / layer planner using RouteEnds.

---

## Last Updated

2026-10-05

Checkpoint commit 414c283 (pushed): wall networks, region infill, per-source Corner R, junctions, parametric / network walls, interactive shape tools, physical-quality routing + wall-authoring UI, the design-model pass (offset-source fix, undo / redo, transforms + clipboard, region / void semantics, parametric insets, WALL vs SOLID infill), the motif-based route-aware wall lattice (wall_lattice.py), and pass 7 (structural rules: corners, max unsupported distance, combined-density out-and-back, cap V, solid boundary contact + serpentine, wall relationships, router travel pairing for solids) — awaiting manual browser verification. Followed by a behaviour-preserving cleanup (dead code, stale comments / docs). Phase 2: 90 tests. Phase 3: 750 tests (incl. 3 node UI smoke tests). All green.
