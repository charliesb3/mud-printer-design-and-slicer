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

**Routing objective (lexicographic, adopted 2026-10-05; SUPERSEDED for wall infill by the objectives above — still the router's generic objective, with one exception: odd ends of solid-infill strands (`travel_pairing`) are joined by a short travel instead of retrace):** (1) never create false printable connections; (2) print all geometry; (3) minimise print runs / travel moves; (4) minimise travel distance; (5) minimise retracing — WEIGHTED (since wall networks, 2026-10-05): retracing a visible wall face / single-bead wall costs `FACE_RETRACE_COST` = 3 × its length, internal geometry (lattice, centre lines, junction connectors) 1 ×, so continuity transitions hide inside the wall. Consequence: within a connected component the router RETRACES printed edges rather than travelling (see "Routing: retrace augmentation + T-junctions" below). Physical acceptability of double-printed mud on retraced edges is not yet validated on the machine. *(Under PHYSICAL RULES 2026-10-06 the router never retraces: odd components are paired by visible travel and reported.)*

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

**Summary (2026-10-06):** the Designer is checkpointed (5ed967d). On top of it, an UNCOMMITTED Z-phase batch adds Layer Designs (inheritance, shared lineage lattice, project bead width), semantic transforms for the Layer Assembly, and the Assembly subsystem (`layer_assembly/`, own memory). Start with "Z PHASE — CURRENT ARCHITECTURE" below; known open problem: transformed-junction lattice coherence (support findings at moving junctions). Testing tiers: same section → "Testing workflow".

### Phase 2 — Toolpath / Graph Prototype — COMPLETE

Location: `design-toolpath/toolpath_proto/`. 90 tests passing.

### Phase 3 — Design Canvas Prototype — 13 steps + six UX passes + wall networks + passes 1–7 (checkpoint 414c283, cleanup 51387d3) + Pass 8 (checkpoint 0898d16)

Location: `design-toolpath/design_proto/`. 1070 tests passing (incl. 8 node UI smoke tests); toolpath_proto 90. Pass 8 + its correction + the wall-region pass were checkpointed in commit 0898d16, still awaiting browser inspection (Pass 8's wall work was manually approved).

How to read this file: the pass sections below are kept as HISTORY (newest decisions win). Where a later pass replaced an approach the older text is marked SUPERSEDED. Current behaviour in one paragraph: wall infill = `wall_lattice.py` motifs (corners braced, max unsupported distance, MIRRORED out-and-back with cap V; wave = tangent half sines, smooth); regions too wide to be a wall (thickness > 1.6 × target, provisional) fall back to the `infill.py` field + `route_plan.py` repair; solid infill = `solid.py` (perimeter and infill printed as distinct coherent phases — boundary contacts are ties, one routing hand-off per ring; rectilinear = conventional boustrophedon; serpentine = interconnected web of anti-phase waves touching at alternating apexes; boundary support measured, landings only with a user limit); short travel accepted where the field is split; openings cut through the complete wall: assemblies (Wall Thickness, linked two-path walls) are cut as assemblies; in a wall-infill MATERIAL region (outer boundary minus any number of voids) an opening is a corridor SUBTRACTED from the material, from the clicked face to the opposite face.

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
- `tests/test_wall_regions.py` — wall-material regions + doorways (the two-room screenshot fixture from outer / inner / both faces, rooms stay voids, unrelated room untouched, cut faces, no junctions, overlapping / corner / multiple doorways, circle void, no-opposite-face safety, linked rounded rect / ellipse, parametric wall next to a room) and Wall Thickness parametric under resize / move / rotate / thickness / Corner R.
- `tests/test_pass8.py` — openings through two-path walls (relation / inset / single void, either face, multiple / overlapping / corner-spanning, circle, wave, no accidental connection); solid (correction): perimeters print as whole loops exactly once, one hand-off per ring, conventional rectilinear, serpentine web (smooth, anti-phase contacts of exactly two strands), opt-in boundary support. Wave smoothness and per-face dead-end evenness tests live in `tests/test_wall_lattice.py`.

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

**Routing: retrace augmentation + T-junctions (2026-10-05, toolpath_proto/graph.py)** — complete; verified by manual UI testing. *(SUPERSEDED under PHYSICAL RULES 2026-10-06: exact print retrace is forbidden and every connected component must close — see "Physical deposition rules".)*

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
- **Remaining unavoidable cases**: a single-bead (no-thickness) dead end retraces its length (no material to edit); a wall without infill whose faces are separate loops travels between them (no hidden bridge is invented — possible future option); two overlapping user-requested legacy lattices (pairwise) are not a repairable field: their own landing points stack (congestion reported, ~2.9 beads); wide regions get a sparser, irregular web than the old 6-way triangulation; wave infill becomes straight where struts meet interior points or would leave the wall; closing the loop (start = end) is chosen less often now (closing must also be a cheap local edit). *(SUPERSEDED 2026-10-06 under physical rules: a single-bead open wall becomes a two-pass return-lane wall.)*

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
- **Junction corners (decision)**: corners CREATED by the network — where the printed faces of two different wall systems end at one boundary node (T splices, X crossings, hub mitres, inner corners) — are a separate concept from source corners and never take a source's Corner R. Treatment: layer default (`junction_style` 'miter' | 'round', `junction_radius`) + per-junction `JunctionSetting(key, treatment, radius)`. Rounding (`network.round_junctions`): tangent arc of radius r (clamped to 45 % of either leg), the two face chains trimmed, a FACE bead `junction:<key>` *(SUPERSEDED 2026-10-05: rounding is decided per wall junction — see "Rounded junctions as wall assemblies")*; the same fillet is applied to the material rings so infill lands on the rounded wall. Default 'miter' = previous output exactly.
- **Junction identity (temporary, documented)**: key = the two wall FACES that meet (source / offset ids, joins mapped to the face they extend), sorted, + `#ordinal` among corners of that face pair ordered by position. Survives moving/reshaping as long as the same faces meet; if topology changes the setting no longer matches (falls back to the default; the override stays in the data and re-applies if that junction reappears). Ordinal can swap if two junctions of the same face pair cross over in position. Compatible with persistent attachments: an attachment id can later replace / prefix the face pair. Internal junctions (e.g. a centre line meeting a face) are markers but not corners (no treatment).
- **UI**: INFILL section replaces "Connecting Geometry" (+ Add Infill → the selected path's wall region; label "Fills wall network N1 (…)" + status: ok / shadowed / no wall material); Pattern, Spacing, Variation. Corner R row in Rectangle / drawn-path Properties. WALL GEOMETRY: Junctions Miter/Rounded + Junction R (default for all). Click a ◆ junction diamond (filled = corner, hollow = internal) → Properties "Junction": Treatment Default / Miter / Rounded + Radius. `/api/infill_patterns`; network summary `junctions` are now objects {x, y, key, corner, treatment, radius, override}; `infills` [{id, regions, holes, shadowed_by, status}].
- **Ambiguities (require a user decision eventually, via RegionOverride / a paint tool)**: (a) nested closed single-bead walls with an infill are read even-odd — if an inner loop is meant as a free-standing thin wall inside solid material (not a hole), the software can't know; (b) a lone closed single-bead wall with an infill becomes a solid slab (only reading available); (c) thick walls: the room inside a ring is always void — a filled room needs an override; (d) a single-bead wall lying inside wall material is crossed by infill rather than treated as a boundary.
- **Limitations**: region summary (`network.regions`) is classified before junction rounding (fillet slivers not reflected); where stagger is impossible (e.g. very short runs, uneven face lengths) the web has some rungs / extra odd nodes; spacing is one global pitch (not adapted to wall width); legacy pairwise lattice remains in the backend; infill is not automatic — each network needs one added.

**Wall regions + openings through the complete wall (2026-10-05)** — implemented and tested; UNCOMMITTED, awaiting browser inspection. Narrow pass (no routing / infill redesign).
- **Bug (screenshot, found):** outer rectangle + two rooms + wall infill, opening placed into room A. Wall infill defined its material from CLOSED boundaries (`declared`: anchor ring minus the closed voids nested in it), but an opening cut only its own source path (+ that path's own offsets). Cutting room A's boundary OPENED it, so A was no longer a void → the room became material and was filled with lattice; cutting the outer boundary left no declared region at all ("no wall material"). Pass 8's "single wall-like void" partner guess did not cover two voids.
- **Semantic model (decision):**
  - Wall Thickness (+ Alignment) is the NORMAL wall-authoring mechanism and is already fully parametric: the derived face is regenerated from the boundary every evaluation (resize, move, rotate, Corner R, thickness — tested). No new mechanism added; the Wall section now states it ("10 in wall inside this boundary — the other face is derived and follows every edit") and the selected wall traces its derived face (dashed, with "derived face · 10 in inside").
  - Linked boundaries (wall relationship; inset + wall infill) are EXPLICIT wall faces (advanced) — cut as one assembly (Pass 8), cap styles apply.
  - Arbitrary interior boundaries are VOIDS of a wall-material region without any fixed-distance relationship; a WALL infill on a closed boundary declares the region = boundary minus all closed voids nested in it.
  - WALL INFILL AND OPENINGS USE THE SAME REGION. An opening is SUBTRACTION from wall material, never additive wall topology. Its two sides are CUT FACES — boundary geometry of the subtraction (printed, flat), not wall-source geometry.
- **Implementation:** `PrintLayer._region_openings` (stage 4) handles openings on the anchor or on any of its voids (unless explicitly linked): for each merged opening interval on the clicked face, `_region_corridor` carries both interval ends (and the middle) along the face's local inward normal (bisector at a vertex; the material side is tested off the segment middle) to the first boundary of the region; all three must reach the SAME ring within the lattice's wall depth (WIDE × spacing; ends 1.5×) — else not cut, status reported ("no opposite wall face within the wall depth — not cut", `network['opening_status']`): never across a room. The corridor = clicked-face arc + opposite-face arc. Both rings get gaps (merged per ring, `_merge_closed_intervals`); the pieces are open NetPieces with CUT ends (never joins / branches); the two sides are emitted as `opening_face` beads (FACE). In `_apply_networks` the region is still the FULL closed rings, and each corridor is a VOID (+ VIRTUAL boundary bead so the arrangement splits exactly at it) → the infill fills the material around the doorway and lands on the cut faces. Opening from either face = the same doorway (faces placed from both sides merge; duplicate / interior cut faces dropped). Cut faces of doorways are reported in `modified_sources` so the canvas draws the backend pieces (also fixes Pass 8's absorbed inner faces being drawn whole on the canvas).
- **Superseded:** Pass 8's "WALL infill region with exactly ONE void" partner inference (now the general region subtraction, any number of voids). Test `test_wall_infill_region_with_several_voids_is_not_guessed` (asserted rooms are NOT cut) → `…_is_cut_through`; `test_opening_cuts_both_faces_of_a_two_path_wall[void]` now accepts flat `opening_face` cut faces as well as `wall_system` caps.
- **Results:** two-room fixture (zigzag / wave; opening from the outer face, the inner face or both): doorway empty, room A and room B stay voids, 2 cut faces at x 58 / 82, 1 run, 0 travel, 0 retrace, no junction. Three doorways incl. an L-shaped corner doorway: all ok, 6 cut faces (an isolated wall piece between doorways needs one travel). Overlapping openings → one doorway. Circle wall with an arbitrary circular void: ok. Approved wall (36) and solid (12) metric cases unchanged.
- **Limitations:** region doorway cut faces are FLAT (the cap styles — Full Round / End R — apply to wall assemblies only); a doorway ending exactly at a void's corner gets a diagonal side; an opening whose opposite face is farther than the wall depth is not cut (reported); thick (Wall-Thickness) voids inside a wall-infill region are not region voids for doorways; the corridor sides follow the local normals (for strongly non-parallel faces the doorway widens / narrows across the wall).

**Pass 8 correction — solid infill + wall-authoring UI (2026-10-05)** — implemented and tested; UNCOMMITTED, awaiting browser inspection. Manual inspection of Pass 8 approved: dead-end out-and-back, multi-arm coherence, openings in normal wall assemblies (multiple / moving / corner-spanning), wall corners, editing (drag, resize, copy, rotate, playback, undo / redo), wall wave. Rejected: the solid hairpin strategy.
- **Design decision (user):** "Zero travel is a preference, not the objective. A short travel move is preferable to distorting otherwise good print geometry merely to make a route continuous." Start = end is desirable, not mandatory. Priority: structural geometry → smooth intentional paths → material distribution → coherent perimeter printing → coherent infill → minimal retrace → minimal travel → start / end convenience. *(CONFLICT 2026-10-06: the physical rules require zero travel inside a connected component; solid infill still uses in-component travel hops and is reported as an open component — decision pending, see Open Questions.)*
- **Perimeter and infill have distinct structural roles:** a perimeter is normally completed as one coherent loop; the infill is its own phase; no alternating perimeter → infill → perimeter to save a travel; never reprint / retrace a perimeter for continuity.
- **Cause of the hairpin playback (found):** every point where solid infill touched the printed boundary was a ROUTING JUNCTION (T-split of the perimeter), so the Euler route could leave / re-enter the perimeter at dozens of points; Pass 8's hairpin loops (all line ends paired into closed loops) made the whole layer one circuit through those junctions → perimeter printed in fragments interleaved with infill.
- **Routing fix (toolpath_proto, opt-in, generic):** `Strand.join_group` / `join_points`. Geometry of the same group (or two ungrouped strands) joins as before; contact BETWEEN groups is a junction only at declared join points — elsewhere beads touch physically and the route passes on (coincident non-join vertices get their own node, nudged 1e-9). Solid strands form a group per infill region with ONE join point per boundary ring (`SolidPlan.attach`: a trail end on the ring if possible) → each ring is a loop hanging at one node: printed whole, once. Ungrouped strands (all walls) route exactly as before (36 wall cases identical). Also: components are visited nearest-next (was arbitrary graph order).
- **Rectilinear:** back to Pass 7's conventional boustrophedon chains + short links (straight parallel passes, V turns landing on the boundary). Short travel between chains split by voids is accepted.
- **Serpentine = interconnected smooth web (decision):** each hatch line is a sine across its own line, neighbours 180° out of phase, amplitude ½ spacing (`WEB_AMP`), wavelength 3 × spacing (`WEB_LEN`): line k touches k + 1 at its crests and k − 1 at its troughs (alternating apexes). Each contact is ONE shared vertex of exactly two strands (a routing hand-off of the same join group; graph degree 4, never a knot). Swing fades over 0.9 × spacing after a 0.6 × spacing straight run into the boundary turns (smooth U); reduced where a line runs close to a boundary (no contact there); 36 samples / wavelength. Chains as rectilinear; free chain ends turn into a neighbouring free end on the same ring where a local turn exists (`_close_web_ends`, ≤ JOIN_MAX × spacing).
- **Boundary support demoted (user):** measured and reported always; landings (Pass 8 machinery) only when the user sets Advanced → Max unsupported (0 = off). The default 3 × spacing constraint is SUPERSEDED.
- **Measured (spacing 16; Pass 8 hairpins runs / travel / start–end → corrected runs / travel / start–end / perimeter↔infill switches; the Pass 8 weaving was observed in browser playback, not counted):** rectangle 1/0/0 → 1/0/374/1; ellipse 1/0/0 → 1/0/211/1; rect + void 1/0/0 → 3/161/374/2; ellipse + void 1/0/0 → 2/69/211/2; multiple voids 1/0/0 → rect 5/319/239/5, serp 4/326/239/5; nested island 2/88–155 → rect 4/139, serp 3/107. Exact retrace 0, perimeter printed exactly once, 0 void violations, 0 hotspots everywhere. Max unsupported (no limit): back to 62–126 (Pass 7 values). Build: rectilinear ≤ 0.02 s, serpentine web 0.03–0.23 s.
- **Wall-authoring UI (decision):** Wall Thickness + Wall Alignment is THE normal wall control (shown first). "Link two boundaries as one wall" (wall relationship) and "Create inset / outset path" (a geometry operation, not a wall) moved into a collapsed Advanced section with one-line explanations (it opens when a link is active). Cause of the dead "choose boundary": the relationship picker was shown for every closed rect / circle / ellipse; with no nested same-type boundary its list was EMPTY, so clicking opened an invisible zero-height list. Now: no candidates → an explanation; candidates → the same picker as the Extra Offset source (hover / open highlights the boundary on the canvas); any empty picker shows "no valid choice".
- **Tests changed:** Pass 8 solid tests (boundary limit 3 × spacing by default, one closed run / zero travel / start = end, parallel-edge landings by default, flowing parallel meander) REPLACED by: perimeters print as whole loops (contiguous, ≤ 1 hand-off per ring), perimeter printed exactly once, runs ≤ Pass 7, conventional rectilinear, serpentine web invariants, opt-in support. `test_pass7::test_solid_turns_land_on_the_boundary_not_short_of_it` restored to its original (boustrophedon) assertion; `test_pass7::test_solid_serpentine_is_smooth_and_bounded` restored, amplitude constant WAVE_AMP → WEB_AMP. `ui_edit_smoke.js` gained the wall-authoring UI checks.
- **Limitations:** travel where voids split the boustrophedon into chains (rect + void 161 in, multiple voids ~320 in — a cellular-decomposition ordering would shorten it); travel moves may cross printed infill / voids; contacts only where both strands have full swing (none near boundaries / in narrow bands); the web density is higher than rectilinear at the same spacing (curved strands).

**Pass 8 — wall + solid infill geometry (2026-10-05)** — implemented and tested (automated + rendered diagnostics); NOT yet manually verified; UNCOMMITTED (left for browser inspection). Triggered by manual testing after Pass 7. Causes were investigated first (generation vs routing):
- **Wall WAVE was angular (cause: generation):** Pass 7's profile (0.4 straight + 0.6 sine) left every face at an angle → a V at every landing; junction stitches were forced straight; 8 samples per stitch and a "tidy" step deleting the samples nearest each landing. **Decision:** wave = half sine TANGENT to both faces (consecutive stitches join smoothly at landings), 16 samples per stitch (`WAVE_SAMPLES`), ends blended onto the actual landing points with a smoothstep (keeps tangency at pinned corner / junction points); fallback near corners = cubic Hermite tangent to each face (tangents from the actual landing — a pinned corner point is not its station's nominal face point); straight only if neither fits; `_no_foldback` drops reversing samples. Zigzag unchanged (single-pass zigzag fixtures byte-identical to 51387d3). Corner braces (same station, both faces) and cap-V legs stay straight (allowed structural transitions). Measured: curvature spikes (a vertex turning > 2.2 × both neighbours and > 12°) away from sharp boundary points: 8–42 per fixture → 0 on 10 wave fixtures × 4 spacings; long straight segments in dead-end arms 42–62 % → ~0 %.
- **Dead-end out-and-back was uneven per face (cause: generation):** Pass 7 interleaved the phases a QUARTER cycle apart (one station) → each face landed at alternating ~S / ~3S (measured up to 3.3 × S per face). **Decision (changes Pass 7's density choice):** the two phases are MIRRORED (half a cycle apart) on one station grid at ≈ the target: every station is landed on both faces (one phase each), so each face's combined support is regular at ≈ S; the phases cross mid-wall (a generated crossing, not a junction). Segments between fixed points no longer need odd counts. Turnaround unchanged: cap V; without a cap landing a mid-wall apex (`MID`, centre line ½ t on), never a rung. Bead length ~8 % more than Pass 7 (an out-and-back traverses the arm twice anyway); wall crossings double (Pass 7's reason against it — the Pass 8 spec asks for even combined support at S). Measured worst per-face gap on four-arm / star / curved / unequal networks: 2.3–3.3 × S → 0.94–1.72 × S. New metric `route_quality.face_support`.
- *(The single-void inference below is SUPERSEDED by the wall-region pass: doorways are material subtraction for any number of voids.)*
- **Openings cut only one face of a two-path wall (cause: model):** an opening belongs to one source; `_OpeningPlan` only knew that source's own offsets, so with an outer + inner rectangle the inner face stayed intact, no cap faces were built and the wall infill vanished (the outer was no longer closed). **Decision:** `PrintLayer._opening_partners` — a closed source's inner face may be another SOURCE path when explicitly linked: a wall relationship; a parametric InsetPath + parent when the outer carries WALL infill; or a WALL-infill region with exactly ONE void whose gap is wall-like (≤ WIDE × spacing, the lattice's own test). Several voids (rooms) are ambiguous → not guessed; a face with its own offsets is never absorbed; nothing changes unless an opening actually cuts. The inner face joins the outer's assembly as one more wall (`_FaceWall` provides what the opening machinery reads from an OffsetTreatment); openings placed on the inner face are re-expressed on the outer's arc length; infills anchored on the absorbed face resolve to the outer (`_anchor`). Result = exactly the Wall-Thickness behaviour: both faces cut, two caps, infill clipped and landing on the caps, 1 run (before: 2 runs, 78.6 in travel, no infill).
- *(SUPERSEDED by the Pass 8 correction above: the hairpin / closed-loop topology, the default 3 × spacing support constraint and the flowing parallel serpentine were rejected after manual inspection.)*
- **Solid: weak boundary support, long travel, start / end far apart (one cause: generation topology):** boustrophedon CHAINS are open paths → their ends are odd nodes (the route starts and ends far apart, or travels), and turns were the only contacts (a boundary nearly parallel to the lines got none: 126 in at ellipse tips). **Decision (architecture):** solid generation produces the topology itself — every line END is paired with a neighbouring end on the same ring by a V / U turn whose apex lands on that ring (networkx max-weight matching; cost = turn length + skipped ends). All nodes even → CLOSED loops (often hairpins: two adjacent lines joined at both ends) tied to the printed boundary at their apexes → the layer is Eulerian (1 run, 0 travel, start = end). Hatch phase / ±5 % spacing variants are tried when a ring is left odd (`HATCH_VARIANTS`); only then an open end remains (router travel pairing still handles it). Boundary support is a first-class constraint: `max_unsupported` (solid Advanced; default `DMAX_RATIO` = 3 × spacing, PROVISIONAL) — a gap above it gets a LANDING: the nearest pass (within 1.25 × spacing) bent onto the boundary (V for rectilinear; Hermite pieces running along the boundary for serpentine, V as last resort); the landing point is the pass's closest approach for convex voids; a landing never removes an existing contact, keeps 0.3 × spacing clear of other infill, and rotates a loop's seam away (by arc length) first.
- **Solid serpentine was stiff (cause: generation):** amplitude 0.22 × spacing + long straight tapers read as straight lines + U turns. **Decision:** amplitude 0.35 × spacing, wavelength 3 × spacing, taper 0.3 λ, 24 samples / λ, one global phase (in-phase neighbours: perpendicular gap ≥ 0.81 × spacing); swing reduced LOCALLY near boundaries (clearance − ¼ spacing) instead of straightening the whole line.
- **Measured (spacing 16, both patterns; before → after) runs / travel / start–end / max unsupported outer, void:** ellipse 1/0/211/126 → 1/0/0/45–47; rect + circular void 3/161/374/57,69 → 1/0/0/48,46; ellipse + void 2/69/211/126,53 → 1/0/0/45,43; multiple voids 5/226/204/59,70 → 1/0/0/43,47; nested island 4/117/165/64,67 → 2/88–155/90–169/46,40 (the island is separate material: one travel is unavoidable); rectangle 1/0/374/62 → 1/0/0/45. 0 retrace, 0 void violations, 0 hotspots everywhere.
- **Tests:** 112 new (750 → 862). Existing tests changed (old assumption → new invariant): `test_wall_lattice::test_dead_end_return_interleaves_at_combined_target_density` (asserted the quarter-cycle interleave: no station landed on both faces, crossings ≈ length / target) → `test_dead_end_out_and_back_supports_each_face_evenly` (per-face gaps even and ≈ target, mirrored, 4 spacings); `test_pass7::test_solid_turns_land_on_the_boundary_not_short_of_it` (left and right apex heights unique together — boustrophedon alternation) → unique per side and mid-hatch; `test_pass7::test_solid_serpentine_is_smooth_and_bounded` (every interior point within the amplitude except near the left / right edges) → also excludes a one-spacing band at the top / bottom edges, where landings bend the outermost passes.
- **Performance:** solid build 0.01–0.35 s (was ~0.01 s); wave on four curved arms 0.49 s (was 0.35 s); design suite 36 s → ~85 s (more tests, heavier fixtures). Contacts / landing search are spatially indexed; wave stitches memoised.
- **Limitations:** a user `max_unsupported` below ≈ 2.5 × spacing on oblique boundaries is not always reachable (the turns set the floor; a landing never replaces a contact — reported as actual vs limit); landings in very narrow solid bands are crowded; wave stitches beside a pinned rectangle corner are Hermite curves (smooth, not the face-to-face construction); unlinked rooms (a region with several voids) are not cut through by an opening; the generic router may pick a different pair of component ends to travel between (openings fixture, wave: 36.3 → 42.2 in, same odd nodes); an island inside a void still needs one travel; solid hairpin loops make the route interleave perimeter and infill (as wall lattices already do).

**Structural infill rules — pass 7 (2026-10-05)** — implemented and tested (automated + headless browser); NOT yet manually verified; committed in checkpoint 414c283. Principle: PATTERN PARAMETERS ARE PREFERENCES; STRUCTURAL SUPPORT AND TOPOLOGY ARE CONSTRAINTS.
- **Wall-lattice priority order (decision, encoded in wall_lattice.py + tests):** (1) never enter openings / voids, (2) never exact-retrace interior lattice, (3) positively support corners, junctions, dead ends / caps, (4) never exceed the maximum unsupported distance if avoidable, (5) no congestion hotspots, (6) continuous printing, (7) a coherent repeated motif, (8) approach Target Spacing, (9) visual regularity.
- **Motif vocabulary:** ordinary stitch · CORNER BRACE · junction hand-off · CAP V (dead-end turnaround / open-end support). A run is laid out in SEGMENTS between fixed support points (junction ends, dead ends, corners); each segment gets its own whole stitch count.
  *(Future direction, 2026-10-06: a wider generator-side library of corner / junction motifs — see Open Questions, "Wall-lattice corner / junction motif vocabulary". Not implemented.)*
- **Corners (decision):** detected along each run where the smoothed centre line turns ≥ 30° within 2 thicknesses (a 140° interior corner turns ~35° there; gentle curves ~10°). Corner points: INNER = boundary point nearest the corner on the inside of the turn (reflex vertex / inner arc apex), OUTER = boundary point farthest along the corner bisector (convex vertex / outer arc apex); bisector from the inner face either side of the inner point. Single-pass runs: the corner is a double station — arrive at one corner point, a BRACE diagonal to the other, continue (one extra stitch; parity accounted). Two-phase runs: each phase lands one corner point; the start side is chosen by measuring both options (hotspots, tiny cells, multiplicity). Corner landings do not move with Target Spacing. Closed-loop seam: corner window wraps.
- **Maximum unsupported distance (decision):** the longest distance along the wall between consecutive lattice supports (landings on either face). Default 1.375 × target (16 → 22 in); wall-infill param `max_unsupported` (UI: Advanced, 0 = auto). It wins over target spacing: segment counts are raised (and stations redistributed) until no real centre-line gap exceeds it — never a tiny extra stitch. Report: `max_unsupported`, `max_unsupported_limit`; UI "Actual: a–b in · Max unsupported: x in (limit y)".
- **Out-and-back density (decision, changed from pass 6; SUPERSEDED by Pass 8: mirrored phases at the target):** Target Spacing = COMBINED density. The two phases interleave on one station grid (each at ~2 × target, offset by one station), so supports are ~target apart and wall crossings ≈ length / target. Pass 6 used two complementary passes at the target (every station landed on both faces) = twice the crossings — changed because the user's spec defines target as the combined density. Trade-off (reported): with interleaving, each face's landings come in pairs (gaps alternate ~S and ~3S); bead LENGTH is still ~1.8 × a single zigzag because an out-and-back must traverse the arm twice.
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
- **Motifs:** a PASS is a zigzag/wave landing alternately on the two faces at evenly distributed stations; interior landings add degree 2 (even), so layer parity depends only on pass ENDS. Through run between junctions → 1 pass; closed loop without junction → 1 circulating pass, even N; dead-end arm → OUT-AND-BACK: 2 passes in complementary phase + a square turnaround rung half a thickness before the cap (every station landed on both faces, no interior retrace, returns to its junction); lone run (no junction) → 1 pass, open route (decision; see Open Questions). *(SUPERSEDED under physical rules 2026-10-06: a lone run gets a CLOSED out-and-back loop — the second wave.)* *(SUPERSEDED, pass 7: out-and-back = interleaved phases at combined target density joined by a cap V; no rung.)*
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

### UI organization — Design / Print sidebars (2026-10-05, Designer checkpoint 2026-10-06)

> **UPDATED 2026-10-07 (Wall System ownership pass, see Wall Systems → pass 5):** the Designer now has a fourth column, **WALL SYSTEM** (`#sidebar-wallsys`, between Design and the canvas). Wall Thickness / Alignment / Print reference, the construction and the Skin + Web web moved there from the path Properties, the Network panel and the Infill list. The path Properties keep only a Wall System choice; the Wall Network section is read-only connectivity; the Infill list shows only non-wall (solid / region) fills. The bullets below describe the earlier layout.

**Decision:** the workspace has three columns. On the left is **DESIGN**, "what am I designing?". In the center is the canvas, which takes all the remaining width. On the right is **PRINT / TOOLPATH**, "how will it print?". The single long right sidebar was replaced because it scrolled a lot while horizontal space went unused. The split follows the project's DESIGN GEOMETRY → WALL / REGION SEMANTICS → TOOLPATH layers. It is not meant to balance the amount of content on each side. This pass is layout only: no behavior, API payload, keyboard shortcut or toolbar change.
- **Left (`#sidebar-design`):**
  - the Paths list
  - Properties of the selected path: label, closed, position and dimensions, Corner R, rotate / angle, and inset-child rows
  - Wall Thickness / Alignment / Print reference line (a property of the designed wall)
  - Advanced (linked boundaries, inset / outset)
  - Delete path
  - the Wall network panel (Network Wall Thickness / Alignment)
  - the selected opening's Width / Position / Delete
  - Selected Junction (`#junction-props-section`, rendered by `updatePropPanel` instead of `#path-props`)
  - Wall Geometry: layer junction and end-cap defaults
  - Advanced: Extra Offsets
- **Right (`#sidebar-print`):**
  - Infill: kind, pattern, target / actual spacing, Advanced
  - Routing Overrides
  - Routing metrics
  - Legend
- **Changed (same day, at the designer's request):** Wall Geometry and the selected junction's treatment first went to the RIGHT, as corners and ends generated by resolving the network. They moved to the LEFT because junction and end-cap treatment describe the physical wall geometry. The selected junction sits next to Wall Geometry.
- **Ambiguous placements:**
  - An opening's disabled "End treatment: Inherit" row stays with the opening on the left.
  - The network panel's "+ Add infill to network" shortcut stays in the network panel.
- Both sidebars are 230 px (the old width) and scroll independently, and the page itself never scrolls. The canvas hint now wraps inside the center column instead of being clipped.
- **Tests:** `test_app.py::TestWorkspaceLayout` checks the left / right / toolbar placement of control ids and the column order. `ui_network_smoke.js` checks that the junction panel renders in its own section, not in the path Properties.

### Z PHASE — CURRENT ARCHITECTURE (Layer Designs, semantic transforms, cross-Z lattice; 2026-10-06, checkpoint 8d9d439, pushed)

Read this section for any Layer Design / Assembly-facing Designer work. The Assembly side is in `layer_assembly/PROJECT_MEMORY.md`. Superseded same-day approaches are compressed at the end ("Z-phase history").

**A. Designer pipeline (as implemented; `model.PrintLayer._build_effective`)**

    source paths (+ Corner R, snapping, insets)
      → trims (sections between contacts; removed intervals cut whole wall assemblies)
      → wall specs: own WallSpec | Network Wall of the source network | single bead
        (explicit Wall Thickness 0 = single bead; physical rules: open / opened single beads → RETURN LANES)
      → offsets (wall faces) → openings (cut pieces, caps, doorway corridors) → mouth cuts (lanes on single-bead hosts)
      → wall NETWORK (joins T / hub, arrangement, material regions, junction corners, rounded / mitred fillets)
      → region infill jobs (transitive reach) → wall lattice (wall_lattice.plan: skeleton → passes → jambs → stations
        → stitches; or the lineage scaffold clipped to the material; solid / field fallback for other regions)
      → printable centrelines → routing graph (graph.route_layer, physical: closed circuits, no retrace;
        odd components paired by visible travel and reported)

**B. Layer Designs (`layer_design.py`)**
- A Layer Design = a Designer document + id, name, parent. Derived designs store a live delta (`patch` by record id, `settings` one level deep); `derive_delta` is the inverse used when a derived design is edited in the Designer. Record ids survive derivation. Not a feature tree.
- **Lineage-owned:** the LATTICE DEFINITION (pattern, params, variation) of a wall infill lives in the design that introduced it (`owner`); edits from any member are moved there (`set_document`); descendant overrides are inert; a descendant's own infill on the lineage's wall is a reported conflict. The PROJECT MATERIAL bead width is owned by the lineage root (Base); a derived design never stores one (`/api/layer_designs/delta` moves it: `material_moved`).
- **One shared lattice per lineage:** a lattice FAMILY (generator + descendants inheriting the infill) prints ONE scaffold, planned once on the generator with all openings removed; every member end wall is a JAMB line (both passes of a doubled run cross at its midpoint; corner jambs replace the corner station; jambs at a junction end end both passes there). Every member prints the scaffold clipped to its material. A family of one is planned normally.
- **A closed route outranks registration:** `scaffold_verdict` resolves each member with the clipped scaffold and checks closure; a member that cannot close (an opening reaching into another wall's material — the junction is rebuilt, so the member is not "Base minus the opening") is planned ON ITS OWN, reported as `planned_alone` in the lineage diagnostics. The verdict's resolve is reused by `build()`.
- **Designer = Assembly:** the Designer view resolves through the same library (`app._apply_lineage`, payload `lineage`).
- **Transformed variants:** `build(design, transforms={source: (k, tx, ty)})` (Assembly transform groups): `semantic_transform.transform_document` moves the sources, the scaffold is planned for the same transforms with CROSS-Z tracking, then the normal pipeline. Caches: `_cache`, `_tbuilds` (LRU 256), content-keyed `_SHARED` (scaffolds, caps, transformed docs, attachments), `_TRACK_MAPS`.

**C. Semantic transforms (`semantic_transform.py`, `source_attribution.py`)**
- Principle (durable): transform the forms → reconstruct their natural geometry → their new intersections / attachments → rebuild the junction → resolve faces / lattice / routes. A junction is derived geometry, never primary.
- Closed / free sources take their own transform (sizes scale; wall thickness, offsets, Corner R, opening widths stay physical; opening positions scale with the host).
- **Explicit connector vs derived junction** (`relations`): an authored open wall attached to forms is a CONNECTOR — regenerated between its transformed attachments (2-point similarity; straight stays straight, a curve keeps its shape); forms that merely INTERSECT have a DERIVED junction — recomputed by the network from the transformed forms, shrinking and disappearing when they separate (`semantic.junctions` [{sources, present}]; no bridge is invented). Attachments come from the untransformed design's coincidence (no persistent constraints yet).
- `effective_walls(doc)`: the Designer's wall rule at document level (own wall / network wall by source-network membership / single / lanes) for attachments and attribution. (A document-level re-statement of `_wall_offsets` — kept in step by tests.)
- `source_attribution.attribute(doc, printable, strand_sources)`: faces / caps belong to their source whole (`BuiltLayer.strand_sources`); other vertices by the JUNCTION TERRITORY rule — min over walls of distance to the material centre line / half thickness (miter bisector at a corner; a T branch trimmed at the host face). Used by the Assembly only to SEE groups (colours, finding frames).
- Identity is exact (`_is_identity`: |k−1| ≤ 1e-9, |t| ≤ 1e-7 → the plain build).

**D. The boundary** — the Designer owns source / wall semantics, junctions, openings / trims, lattice, printable geometry, route topology and the project material. It knows nothing of sections, Z, headers or support. Contract, caching and invalidation: `layer_assembly/PROJECT_MEMORY.md` → "Designer ↔ Assembly contract".

**E. What persists across Z**
- Lineage scaffold: one per family per transform spec (content-cached), registered across members at one transform.
- Lattice stations / identity: carried from the UNTRANSFORMED reference plan (not from the layer below) by `wall_lattice` track data: per matched run passes, start side / parity, segment counts, seam, stitch construction, junction PAIRINGS (`partners`, run `uid`).
- Source identity: source path ids (stable across derived designs and openings) → per-vertex tags.
- Group membership, support state, headers: Assembly state. Route information does not persist across Z (Route Origin is per design; multi-layer seam planning is future).

**Cross-Z lattice — what is solved once vs per layer**
- Once per design: untransformed scaffold (+ its track), attachments / relations, effective walls.
- Per transformed layer (new spec): source transform (~0.05 s), scaffold PLAN for the transformed walls — Delaunay skeleton, route inspection, tracked layout, stitch geometry (~0.55 s), resolve with the clipped scaffold + closure verdict (~0.17 s), attribution (~0.17 s, Assembly adapter).
- Matching is wall-relative (`_track_map`: reference point → owner source's map). Matched runs keep their choices; PARTIAL when the skeleton changed: unmatched runs planned afresh, pass counts repaired for parity through untracked runs first, carried sides re-searched only if they cannot pair, carried counts honour the requested parity.
- Correctness-critical: closure (verdict + fallback), containment (dogleg), parity. Merely inefficient: re-planning unchanged topology every layer.

**Transformed-junction lattice — THE remaining problem (precise statement for the next pass)**
- Symptom: reference stack (`tests/reference_network.py`, 32 layers, Circle 1 / Rect 1 / Rect 2 in three groups scaling, Line 1 unassigned, round junctions, Base then "gaps"): ≈ 51 (zigzag) / 71 (wave) support findings, ~85 % on lattice strands at the Rect 1 ↔ Circle 1 lens and the Rect 1 ↔ Rect 2 crossing; wall bodies stack cleanly; every route closes (4 of 31 variants plan alone at the topology change). The support checker is correct — do not weaken it.
- Verified causes (by consecutive-layer overlays and plan diffs):
  1. Junction TRANSITIONS are constructed per layer from discrete choices that flip under small geometric change even when the skeleton is unchanged: the stitch construction out of a junction landing (wave → Hermite → straight → dogleg, the first VALID one), the meeting point on a rounded fillet, the dogleg's centre-line shortcut. Layers 1–7 of the stack (identical topology, fully tracked) still show 3–6 findings each, all at the lens.
  2. Where a junction region shrinks away (lens runs 10 → 6 → 5), the changed runs are planned afresh (partial tracking) — a legitimate topology change, but the replacement motif is unrelated to the layer below.
  3. The reference is the untransformed design (layer 0), not the layer below, so drift accumulates with height.
- Tried and abandoned: a shortest two-strut dogleg (hugs and grazes the fillet → clipping splits it → 21 / 31 layers opened); bisected fillet meeting point (no effect on findings; kept, continuous).
- Next pass (attack only this): carry the reference lattice GEOMETRY of each junction territory wall-relatively (morph the stations / transition polylines with the owning walls) and re-plan only when the territory topology changes; a dedicated shrinking-junction transition motif; track against the previous layer's plan if per-layer chaining is acceptable for caching (variants would then depend on the stack below).

**Performance (benchmark `python -m layer_assembly.bench`, 8 workers, this Mac; 2026-10-06 audit)**
  | layers | cold total | variant geometry | support | warm total (overhang edit) |
  |---|---|---|---|---|
  | 49 | 8.9 s | 6.6 s | 1.2 s | 1.07 s |
  | 99 | 14.5 s | 11.5 s | 2.4 s | 2.08 s |
  | 299 | 43.5 s | 35.5 s | 7.4 s | 6.34 s |
- (Before stabilization pass 2: 8.2 / 12.7 / 38.3 s cold — the closure verdict and derived-junction detection added ~15 %.) Per variant ≈ 0.95 s CPU: scaffold plan 0.53–0.60 s, resolve with the clipped scaffold + closure verdict 0.16–0.19 s, source attribution 0.17–0.19 s, source transform + derived junctions 0.02–0.03 s (design relations cached in the audit; was 0.05). Serialisation and layer resolution < 0.02 s. Support analysis ≈ 21–25 ms per layer and is the whole warm-edit cost. The Assembly path does not route (only the closure graph); full routing happens in the Designer view. Browser render of a 32-layer stack ≈ 10 ms.
- Top bottlenecks (evidence): 1) the per-variant scaffold PLAN (≈ 60 % of variant time) — topology is re-planned although usually unchanged; 2) support analysis on warm edits; 3) attribution (could be done from the build's own face geometry instead of re-deriving material centre lines).
- Known accidental recomputation (documented, not changed): each worker process rebuilds the untransformed reference scaffold / track map once (×8 in a cold run).

**Testing workflow (decision, audit)** — tiers:
- TIER A (while editing one module; seconds): the module's own test file, e.g. `design_proto/.venv/bin/python -m pytest design_proto/tests/test_reference_network.py -k sweep`, `… layer_assembly/tests/test_overlap.py`, `… design_proto/tests/test_networks.py -k ui_network_smoke`, `… layer_assembly/tests/test_assembly_ui.py`.
- TIER B (after a coherent subsystem change): `design_proto/.venv/bin/python -m pytest design_proto/tests -q` (Designer) or `… layer_assembly/tests -q` (Assembly); for wall_lattice / layer_design / semantic changes also `layer_assembly/tests/test_semantic_groups.py test_reference_network_stack.py`.
- Measured (2026-10-06 audit, serial): design_proto 1143 tests 188 s; layer_assembly 124 tests 84 s; toolpath_proto 90 tests 0.2 s; pi-interface 78 tests 1.3 s. Slowest: `test_cross_z_tracking.py` (2 tests ≈ 23 s), `test_reference_network.py` (≈ 45 s total), Assembly `test_semantic_groups.py::…strongly_scaled…` and `test_reference_network_stack.py` (≈ 25 s each). Real-Designer Assembly tests are ≈ 90 % of the Assembly suite time.
- TIER C (checkpoint only): Designer + Assembly + `toolpath_proto` (`cd toolpath_proto && .venv/bin/python -m pytest -q`) + `pi-interface` (`cd pi-interface && .venv/bin/python -m pytest -q`). The JS smoke tests run inside the Python suites (node required). The four suites use separate venvs / processes and can run in parallel.
- All commands from `design-toolpath/` unless noted. Pure Assembly tests never import the Designer.

**Z-phase history (superseded the same day — details in git history)**
- Lattice inheritance: parent's lattice clipped (derived layer stayed open at the cut) → generated-original reference → single-pass scaffold + straight brace on jambs (rejected: 2 print runs) → doubled jammed runs crossing at jamb midpoints (current). "Unresolvable jamb → route stays open, reported" → planned-alone fallback (closed route first).
- Lattice definition per design → owned by the lineage (editing "gaps" changed only gaps and stacked different lattices).
- Transform groups as per-vertex placement of resolved beads → semantic transforms (connecting walls stretched / tore).
- Junction ownership: nearest attributed face → material-centreline / half-thickness territory; network walls were seen only on their anchor path (fixed: `effective_walls`).
- Cross-Z: per-layer independent planning (stitch counts oscillated) → full tracking against the untransformed plan (applied even to changed skeletons → 20 / 31 open variants on the reference stack) → partial tracking with parity repair + pairing tracking.
- Wall Thickness 0 used to mean "inherit the network wall again" in the UI → explicit single-bead override.
- Round-junction scaffold failures: fillet arcs taken as end walls; stitches leaving a concave fillet; jambs just short of a junction end; disconnected lattice systems in stand-alone plans — all fixed (`tests/test_reference_network.py`).
- Assembly tool: `layer_assembly/bench.py`. Fixtures: `tests/multi_opening_fixtures.py` (rect + line + circle), `tests/reference_network.py` (PRIMARY realistic integration fixture).

### Wall Systems V1 (2026-10-07; manually reviewed — Wall Systems checkpoint 2026-10-07)

- **Known remaining limitations (at the checkpoint):**
  - Wave systems (Interleaved / Linked): the closing end-lane permutation (`_end_perm`) may swap an outer lane inside the end fade (the exterior-lane rule was applied to Parallel Walls and splices only).
  - A partition of another construction prints as its own route (not spliced into the host).
  - An L corner made of two SEPARATE line sources in one generated system prints as two overlapping capped bands (one route); a single polyline gives the canonical corner.
  - (correction pass) The same holds for walls trimmed into an L: both ends are squared into the corner square and overlap there (dense corner, one route). Generated X junctions other than Parallel × Parallel (even lanes) still pass every lane through every lane (fallback). The Parallel seam may still land inside a junction when the span midpoint falls there.
  - Odd lane / path counts cannot close at free ends (parity); pieces separated by openings need travel between them.
  - Junction R / rounded junctions between two GENERATED walls are envelope-only (no pattern follows the fillet; the fillet is not printed).
  - Solid / region infills from older designs still build but have no authoring UI (the right-sidebar Infill UI was removed).
  - Derived Layer Designs are migrated to Wall Systems only when edited.
  - The retired splice helpers (`connect_branch`, `_connect_generated`, `_kiss_into_skin`, `_splice_skins` fallback) remain in code; only `_splice_skins` is still reachable (fallback).
  - Running `toolpath_proto/tests` and `design_proto/tests` in ONE pytest process fails one test (module-name clash of the two `app` modules): run the suites separately.


- **What:** a first-class Wall System. `wall_systems.py`; `model.WallSystem`, `model._apply_wall_systems`.
  - Since pass 5: the layer-level `WallSystem` OWNS the construction of an explicit group of paths (envelope, construction, parameters, Skin + Web web); see pass 5. (Pass 4 had membership-only systems with network inheritance — superseded.)
  - Originally stored as `wall.system` on a path's own wall / its Network Wall. The model still reads that legacy form (no network spreading); the client migrates it on load.
- **Systems:**

  | System | What it prints | Controls |
  |---|---|---|
  | Skin + Web | Unchanged (incl. canonical corners, Adaptive Truss) | — |
  | Interleaved Waves | N full-depth paths, one waveform / period, phases k/N | Waveform, Number of Paths, Period, Depth (0 = full envelope) |
  | Linked Waves | N antiphase paths; auto amplitude H/N, so neighbouring centrelines meet at their extrema; spacing derived so the outer paths reach the envelope; more amplitude = deeper interlock (`closest_approach`) | Waveform, Number of Paths (≥ 2), Period, Amplitude |
  | Chained Loop (provisional) | One open path of alternating, staggered self-intersecting loops (see correction pass) | Pitch, Loop Depth, Loop Width, Neck, Phase |

- **Mechanism:**
  - The normal pipeline builds the envelope (faces, openings, junctions, material regions). Wall-system sources anchor network resolution, so lone walls get regions too.
  - A region whose bounding walls all share one system has its face / cap beads replaced by the system's paths, and its Wall Infill job dropped (infill status 'wall system').
  - Paths are in the production runs' strip coordinates (wall-relative), with bead centrelines ≥ ½ bead inside the faces.
  - Mixed-system regions: split by wall since pass 4 (below). Only a removed PARTITION still falls back to Skin + Web ('mixed').
- **Minimum effective thickness** (read-only, network summary `wall_systems`):
  - Cross-sections every ½ t along each run (away from free ends) are cut by all paths; the value is (outermost-to-outermost span + bead width), clipped to the local envelope.
  - The Chained Loop gives exactly one bead at each eight's waist.
- **V1 limits:**
  - No junction motifs: paths stop at junction ends (gaps at T / X hubs). Chained Loop corners are followed wall-relatively only.
  - (End caps / closed routes: solved in correction pass 3 for even path counts and the chain; odd Interleaved / Linked counts stay open at free ends, by parity.)
  - No cross-Z / Assembly-specific handling beyond the normal pipeline.
- **Correction pass (2026-10-07, after live review):**
  - Persistence: a Wall Thickness / Alignment edit keeps `wall.system` and its parameters.
  - Responsiveness:
    - The strip frame is sampled once per run (0.5 in) and interpolated; output polylines are lightly simplified. Reference network: effective 0.6–1.2 → 0.2–0.3 s, route 1.2–2.9 → 0.7–0.8 s.
    - UI requests are coalesced (one in flight, one follow-up with the latest state).
    - Parameter edits no longer rebuild the panel; the readout updates in place.
    - A lone wall with a system now resolves (it was never fetched).
  - Corners for Interleaved / Linked are HARD anchors, as for Zigzag / Wave:
    - Each span gets whole periods and the same phase at every corner.
    - The waves fade (0.35 P) into distinct lanes, which turn the corner as mitred offset lines (zone 0.6 t).
    - Interleaved corner phase π/2 − π/2N keeps every lane distinct.
  - Chained Loop: an interim ∞∞∞ stroke, then REPLACED again (same day, designer's topology spec).
    - The current primitive is ONE continuous OPEN path progressing along the wall: upper loop → lower loop → …, staggered by one pitch, each loop self-intersecting once at its neck:
          x(u) = a·u + w·sin 2u,   y(u) = D·sgn(sin u)·|sin u|^q
    - Parameters: a = pitch / π. w > a/2 doubles back near each crest, and u and π − u share a height, so there is exactly one crossing per loop; upper / lower strands meet only at y = 0, so there are no other crossings. q places the neck.
    - Controls:
      - Pitch (0 = 1.2 t);
      - Loop Depth;
      - Loop Width (→ w by bisection; 0 = 0.8 × pitch, ≤ 1.6 × pitch);
      - Neck (share of depth; 0 = 0.12, so loops fill their half of the wall);
      - Phase.
    - Roundness is not exposed: the curve is analytic and smooth.
    - Open runs fit a whole number of loops between the ends; rings use an even count (closed), with the seam between corners.
    - Saved designs with the old 'period' map to pitch = period / 2.
    - The reference drawing was not visible to the agent; built from the written topology.
- **Correction pass 3 (2026-10-07): rounded envelope, end caps, closed routes, chain pitch.**
  - Rounded-geometry bug, root cause: the Interleaved / Linked corner lanes turned at the MITRE point of the two straight legs, which lies outside a Corner R / rounded-junction envelope. The envelope itself was already the resolved, rounded one.
    - Now a mitre is kept only where every mitre point is half a bead inside the envelope; otherwise the lanes follow the resolved wall through the corner (concentric with a round).
    - The strip frame also keeps every bead half a bead inside the resolved faces (sharp-corner frames are only approximate).
  - End caps:
    - The cap rail is the region ring's own cap / opening-face arc between the end's two face points (the very cap Skin + Web prints, flat or full round), inset by half a bead. Points that a local inset brings too close to another boundary part are pushed towards the wall end's middle.
    - Interleaved / Linked strands fade into lanes at free ends. One end gets adjacent U-turns, with the two outermost ones hugging the cap rail from each face side; the other end gets nested U-turns plus the outermost pair along the whole cap.
    - Walked as a boustrophedon, this is ONE closed circuit for an EVEN path count.
    - ODD counts cannot close at free ends (each end then holds an odd number of strand ends, and every connector graph has an even total degree). They are joined into one open path per piece; the report says `closable: False` and the UI warns.
    - Chained Loop on a wall with two free ends: the path turns through the cap rail and returns as the MIRRORED chain. One closed stroke, no retrace; loop density on such walls is therefore doubled.
  - Measured: each connected piece = one closed route, no internal travel, no retrace (one / three openings, opening near a corner, flat and full-round caps, Corner R 10 / 20 / 40, rounded and mitred junctions).
  - Chain pitch floor CHAIN_PITCH_MIN = 0.1 in (was 2 in); sampling ≥ 24 points per loop, budget 60 000 points per run.
  - Reference network (round junctions + opening): effective ≈ 0.4–0.5 s, route ≈ 1.2 s. The chain's route costs more because of the return lap.
- **Pass 4 (2026-10-07): canonical end motifs, Wall System membership, Wall Systems section.** (SUPERSEDES pass 3's cap turnarounds: adjacent U-turns hugging the cap from each side read as arbitrary strand pairings.)
  - **End motif = hard architectural boundary motif** (analogous to canonical corners), identical at source ends and opening jambs:
    - At a free end the strands fade (0.35 P) into N EVENLY spaced end lanes; the outermost two sit half a bead inside the faces.
    - Lanes r and N−1−r are joined by the resolved cap ring arc inset by ½ bead + r × lane spacing. r = 0 is the cap itself (one straight flat cap / the round arc); inner pairs return nested inside it, parallel to it (concentric rectangles / arcs). `_Envelope.cap_rail(…, inset)`, `_cap_turns`.
    - Closure: every Interleaved / Linked strand has an antipodal partner (value −y) holding the mirror lane at BOTH ends, so nested motifs alone give N/2 separate loops. `_end_perm(N)` swaps adjacent hi-end lanes of strands in different components (N//2 − 1 swaps, the minimum, inside the hi-end fade) → one circuit for even N, one open path for odd N (middle lane ends at each jamb; `closable: False`, as before).
    - Chained Loop: loops end 0.6 t short of each free end; the forward lap leads (smoothstep) out to the f0 face lane, the return lap to the f1 lane, and one cap joins them.
  - **Single vs doubled Chained Loop (reported, no mode built):** a run with two FREE ends (a lone line, a ring piece cut by openings) gets the mirrored RETURN LAP (the only way to close one stroke without retrace), so it reads as a doubled, mirror-crossing chain. Closed rings (no free ends) print the single lap. Runs with a junction end stay a single open lap (V1).
  - **Membership (decision):** Wall Network membership = physically connected; Wall System membership = shared construction + parameters. Independently editable.
    - A source uses the system listing it in `members`. Otherwise it INHERITS the first system with an explicit member in its Wall Network (convenient default: a newly attached wall picks it up), unless listed in that system's `excluded`.
    - A system fills only a thick envelope; a single-bead member keeps its bead.
    - Report: `network.wall_system_membership` {pid: {id, via: member | network | excluded, type, filled}}; each region report carries `system_id`.
  - **Mixed regions split by wall** (`_split_wall_systems`, `_splice_skins`):
    - Every ring edge is tagged with its face bead's source → system.
    - Each system fills ITS envelope: the rings with each maximal run of other walls' edges replaced by the chord across its mouth (an attached line's faces + cap → the host's face line).
    - Skin + Web walls keep their own face / cap beads (a single-bead line = its physical out-and-back lanes). Each such run is spliced into ONE system path: the short stretch between the points nearest its two mouth points is cut out and both are joined → one closed route, no travel, no retrace.
    - A removed wall with its own system gets its own envelope (closed off by the chord, which caps it): two touching closed routes (one travel between them).
    - Limits: a removed PARTITION (wall spanning between two system walls; chord ≈ run) → whole region Skin + Web, reported 'mixed'. A Skin + Web part's Wall Infill (web) is not generated in a mixed region. Walls that INHERIT the system across a junction still have the V1 junction gaps.
  - **UI:** a persistent **Wall Systems** section (Design sidebar, below Wall Network): system selector, Type, parameters, Minimum Effective Thickness (+ odd-count warning), members (explicit / via network / single bead not filled), removed paths, + Add / Remove selected path, + New system, Delete.
    - Path Properties show one membership row (+ Remove from Wall System / Use Wall System / + New) and, for an inherited network wall, "Single bead (out and back)".
    - The Network panel no longer holds system controls. New systems default to Interleaved Waves, 4 paths (even: closable).
- **Pass 5 (2026-10-07): the WALL SYSTEM OWNS CONSTRUCTION — model + UI reorganisation.** (SUPERSEDES pass 4's membership: network inheritance and `excluded` are gone; geometry work unchanged.)
  - **Conceptual model (designer decision):** DESIGN = source geometry (paths, dimensions, Corner R, junction / end-cap style, openings, trims, connectivity) · WALL SYSTEM = construction specification + explicit path membership · MATERIAL / BEAD = shared physical / process properties · PRINT / TOOLPATH = the resulting route and diagnostics.
  - **`model.WallSystem`** = {id, name, members, thickness, align, print_reference, type (skin_web | interleaved | linked | chain), params, web {pattern none | zigzag | wave | truss, params, variation_index}}.
    - Explicit, user-authored groups: members need NOT touch or share a Wall Network. At most ONE system per path (first wins in the model; the UI moves a path).
    - A member's envelope comes from its system (winning over any legacy path wall). A path in no system = a single bead (physical out-and-back when open).
    - `thickness None` = a legacy pass-4 system (envelope from the path / network wall), migrated.
  - **Skin + Web web (the former Wall Infill UI):** configured on the system. It is materialised as `RegionInfill` records OWNED by the system (`owner` field; one per connected group of members, client `_syncSystemWebs`), because Layer-Design lineage / the cross-Z lattice key the lattice on infill ids and own its definition (`LATTICE_DEFINITION`); a migrated infill keeps its id. The model uses owned records as they are (it never overrides them from `web`, respecting lineage); records of an inactive web (other construction / 'none' / member left) are kept but not built; a system with a web but no owned record (API / tests) gets one synthesised record per member (`PrintLayer._all_infills`).
  - **Migration (decision: backend-assisted, geometry-preserving):** `PrintLayer.migrate_wall_systems()` / `POST /api/migrate_wall_systems`.
    - It builds the legacy design, groups every path's EFFECTIVE wall (own WallSpec, Network Wall inheritance, legacy `wall.system`, pass-4 systems, the wall infill reaching its network) by (thickness, alignment, reference, construction, parameters, web) into systems (old system ids kept), and makes wall infills owned webs.
    - The Designer applies it once after the first response (`_maybeMigrateWallSystems`: path walls / Network Walls cleared). It is not an undo step (the current history entry is replaced).
    - Tested: printable centrelines + route identical after migration (own walls + infill, reference network, single-bead line override, line override, legacy chain system, pass-4 system, truss web); idempotent.
    - Legacy data still resolves in the model (fixtures, Python tests). Limitation: derived Layer Designs are migrated only when edited (their patch may still carry legacy path walls until then; a member's system envelope wins).
  - **Wall Network now** = geometric connectivity only: `source_networks` (junction resolution, shared envelopes, web grouping, mixed-region splitting). `NetworkWall` / `network_walls` are legacy (read by the model and the migration, never authored).
  - **UI:**
    - **Wall System sidebar:** list of systems (type · member count) + paths in no system; + New (with the selected path); editor: Name, Construction, Wall Thickness, Wall Alignment (+ Print reference when Centered), Skin + Web → Web (None / Zigzag / Wave / Adaptive Truss) with its parameters, actual-spacing / lineage readouts, Regenerate (truss), Advanced, V1 / V2; other constructions → their parameters + Minimum Effective Thickness; Members checklist (shows "(in X)", checking moves the path), + Add / Remove selected path, Delete.
    - Path Properties: Wall System select (None = single bead) + envelope note; the list badges each path with its system.
    - Design → Wall Network (connectivity): read-only. Print → Infill: unowned (solid / region) fills only; "+ Add Infill" on a member opens its system's web.
  - **Tests:** model / migration in `test_wall_systems.py` (envelope ownership, non-touching members, one system per path, owned / synthesised / inactive webs, legacy compat, migration equivalence ×7, grouping); `ui_wall_system_smoke.js` rewritten for the sidebar; `ui_network_smoke.js` / `ui_edit_smoke.js` updated (connectivity section; path Wall System row); layout test (`sidebar-wallsys` between Design and the canvas); `ui_assembly_smoke.js` seeds its wall through a Wall System.
- **Pass 6 (2026-10-07): construction catalogue, member editor, mixed junctions meet ACTUAL material.**
  - **Constructions** (`WallSystem.type`; `wall_systems.SYSTEMS / LABELS`):
    - `single` — Single / Out-and-Back Wall: the ordinary single-line wall. No envelope (Wall Thickness hidden / ignored); the existing bead + Physical return-lane rules (separated outbound + return lanes, U-turn).
    - `hollow` — Hollow / Skins Only: the two skins, no web (owned web records kept, inactive).
    - `skin_web` — skins + Zigzag / Wave / Adaptive Truss web. "None" is no longer a web choice: Skin + Web with web None (pass-5 designs / migration of infill-less walls) becomes `hollow`.
    - `parallel` — Parallel Walls: Number of Walls N (default 4), evenly spread across the thickness, outermost half a bead inside the faces (`_wave_paths` with constant lane values). Corners and wall ends reuse the Interleaved / Linked machinery (mitre-or-follow lanes, canonical nested end motif + `_end_perm` closure: one route at wall ends for even N). V1: on a closed ring the N walls are N separate concentric loops (travel between them).
    - `interleaved`, `linked`, `chain` unchanged.
  - **Member editor:** Members first (only this system's paths, click = select, compact ×), then "Add path…" (every other path; "(from X)" when it moves from another system). The all-path checkbox list and Add / Remove selected buttons are gone.
  - **Mixed junction bug, root cause:** in a mixed region the incoming skin-like wall (single / hollow / skin_web) kept its beads, which END at the host's NOMINAL face (the chord), and `_splice_skins` joined each end to the NEAREST host point by a diagonal connector. That worked by accident where host material reached the face, but gave hooks / gaps where the host's printed path lies inward (wave troughs, chain necks). With Junction R the envelope fillets were also in the host's envelope (the host pattern bulged into them) and on the incoming chain (the lanes bent along them).
  - **Fix — printed material to printed material** (`wall_systems.connect_branch`, `model._connect_skins`):
    - The architectural junction direction is the SOURCE wall's tangent at that end (`_junction_dir`).
    - Each incoming lane starts at its first long straight segment along that direction (fillets / corner bits before it are dropped) and continues STRAIGHT until its first valid contact with the host system's generated paths. The two contacts must lie on one host path, close along it; the nearest such pair is used.
    - The host stretch between the contacts is cut; branch + host are one circuit. The incoming beads are re-emitted as part of the joined path.
    - Junction fillets between systems of different constructions belong to the incoming wall, never to the host envelope (the host keeps its straight face). Junction R (layer, round style) becomes a tangent blend at each REAL contact; the contacts do not move with R (to ~0.01 in).
    - Fallback (no straight lanes / no contact): the pass-4 mouth splice.
    - Incoming GENERATED systems (parallel / waves / chain into another system) still meet at the chord as separate closed routes (V1).
  - **Fixed in passing:** the junction-fillet source parsing must resolve bead ids (e.g. `L1_ce~n0`) to real source ids.
- **Pass 7 (2026-10-07): cleanup, Parallel Walls ends, route start / travel order.**
  - **UI cleanup:** the Skin + Web web no longer shows V1 / V2 (phase variation relic; `variation_index` stays 0 in data). The right-sidebar Infill section ("+ Add Infill", the infill list) is REMOVED — the web is configured only in its Wall System; Print / Toolpath keeps Routing Overrides, metrics and the legend. (`addInfill` / `updateInfillList` remain only for the console fixtures; solid / region infills in old data still build but have no authoring UI.)
  - **Parallel Walls ends, root cause:** Parallel reused the wave machinery's hi-end LANE PERMUTATION (`_end_perm`, needed only because wave strands come in antipodal pairs): straight lanes swapped ranks across the end fade → diagonal stubs and crossings. Round caps were additionally askew: a free end's strip station sat INSIDE the round cap (face points at different depths), so lanes ended on a slanted section while the rails started square.
  - **Fix:**
    - No permutation for Parallel. The canonical nested end motif (`_cap_turns`, shared with the waves) gives concentric loops; neighbouring loops are joined by a RUNG PAIR (two parallel rungs between lanes r, r+1 mid-span of the longest straight span, `_parallel_rungs`) → one route, nothing crosses. A closed ring's N walls are joined the same way (one route instead of N loops). Odd N with free ends: one open route (parity).
    - Free-end stations are moved back (≤ 0.75 t) to a square, full-width cross-section (`_Strip._square`) — all systems' round ends now start their rails cleanly.
    - Cap rails keep only the part beyond the lane ends. A nested return that would fold over itself / the return outside it (a cap distorted by a nearby corner) becomes a nested U-turn scaled inside it. A degenerate end with no square section (an opening cutting through a corner) gets nested U-turns with one common scale. The strip's clearance guard now scales a whole side of the cross-section by one factor, so lanes keep their order.
    - Verified: 2–6 walls × one / three openings / near a corner × flat / round: no self-crossings, inside the envelope, one route per piece (closed for even N).
  - **Route start + travel order (decision; `toolpath_proto/graph.py`, bounded — routing architecture unchanged):**
    - A CLOSED component without a Route Origin starts (its seam) at the node minimising travel + SEAM_WEIGHT (12 in) × exposure: web / lattice / internal 0, wall-system path 0.4, inner skin 0.7, outer / free / cap 1, +0.5 at a corner. A seam sits on a bead vertex (splitting a segment for a mid-face seam was tried and reverted: it breaks the one-move-per-segment invariants).
    - OPEN components start at the end that leaves the other end nearest the next component.
    - Components are ordered by nearest neighbour from every possible first component (or from the manual start), then 2-opt on the open tour (≤ 150 components).
    - Constraints kept: Route Origins (manual seams), the manual start position, an explicit component order.
    - Measured: 12 hollow rects travel 1813 → 1608 in, 20 single-bead rects 1824 → 1444, 10 Skin + Web rects 1506 → 1346; the one-void fixture's seam moved from an outer corner onto the web.
    - Note: running `toolpath_proto/tests` and `design_proto/tests` in ONE pytest process fails one test (module name clash of the two `app` modules) — pre-existing; run the suites separately.
- **Tests:** `tests/test_wall_systems.py` (+ Parallel ends: 60 parametrised cases, rings, lone line) and new `tests/test_route_planning.py` (web seam, inner-skin seam, travel ≤ nearest-neighbour, closure, Route Origin kept, manual start honoured); the layout test asserts the Infill section is gone.
- **Pass 8 (2026-10-07): Move Start mode; consistent Parallel ends; exterior-lane continuity.**
  - **Move Start (toolbar, next to Arrows / Dimensions; `toggleMoveStart`, `moveStartMouseDown`):** while on, every canvas click goes to route starts first — no path / opening / handle is selected or dragged (normal priority: editable geometry outranks the overlay, so a start on an opening was unreachable). Drag a start marker along its route, or click a closed route to start it there (both write `route_origins`, one undo step). Open routes' Start / End are explained, not moved. Leaving: the button again, choosing a tool, or Toolpath OFF. Default seam / travel order (pass 7) unchanged.
  - **Inconsistent end, root cause:** the end frame and the "square end" test used the SKELETON centre line, which runs askew near a free end on a short leg (e.g. a jamb ~20 in from a corner). That end was judged degenerate and got the inset U-turn fallback while equivalent ends got the canonical motif.
  - **Fix:** the end section is judged on the FACES (perpendicular to both face tangents, full median width, every lane half a bead clear), the outward direction is perpendicular to that section, and the station is the first such section from t/2 in with a stable square stretch behind it (`_Strip.is_square`, `_first_square`, `_lanes_clear`). Rails get loop removal (`_unloop`) round concave notches. Verified: every clean jamb at 4–40 in from a corner, flat / round, ends half a bead from the cut; only genuinely degenerate ends (a cut through a corner; a round cap that does not fit before a corner) use the nested-U-turn fallback.
  - **Visible Parallel seams, root causes:** (1) the rung pair joining the outermost loop to the next CUT THE EXTERIOR LANE (a visible break, also on curved walls); (2) `_simplify` dropped the second visit of a shared vertex; (3) an incoming GENERATED wall (Parallel / waves / chain) at a mixed junction was capped at the host's NOMINAL face (chord) — a gap before the host's real material. Found in passing: `_split_wall_systems` overwrote its per-ring source tags (`srcs`) after the first system key, so with two generated systems the second lost rings (the host built on a broken envelope) — fixed.
  - **Priority rule (decision): preserve continuous exterior / outermost lanes; put any splice, handoff or seam on an interior lane, else inside host / junction material, else at the least exposed place.**
    - Parallel loops are joined by KISSES: an INTERIOR lane bulges (cos^1.5 profile, slight angle at the touch) to touch its neighbour at one shared vertex the route passes twice — no material removed, nothing crosses, face lanes never cut or moved (only a two-wall ring must bulge a face lane). Junction cuts never remove a touch vertex.
    - Incoming generated walls: the outer cap rail on the chord is opened and the route handed to `connect_branch` (`_connect_generated`): both face lanes continue straight into the host's printed material; the nested inner returns stay inside the incoming wall. Every tested host × incoming combination prints one route, no travel.
    - Not changed: the wave systems' end lane permutation (`_end_perm`) may still swap an outer lane inside the end fade (scope: Parallel + splices first).
- **Pass 9 (2026-10-07): mixed regions on the reference network; lane-by-lane Parallel Walls.**
  - **Phantom connector walls, root causes:** (1) `_split_wall_systems` closed every ring SEPARATELY: Line 1 bridges Rect 2 and the circle, so its two faces lie on two rings (the outer boundary and a hole) and each face + chord became a flat sliver that the generated system filled; (2) any skin-type run next to a generated part was treated as an INCOMING skin to splice into it, so the HOST's Skin + Web faces were spliced into the generated line and the failing splice fell back to two mouth connectors.
  - **Fix:** each system's face runs are STITCHED across rings (a run's end joins the nearest run start across the mouth; a T-branch closes on itself, a bridge becomes its true band, the host side gets its true outline). A chord that is the generated part's END means the skins are the HOST (`host_skins`): the host face is closed across the mouth by a skin bead c1 → P → c2 and the incoming generated loop restarts at P (`_kiss_into_skin`) — one route. End test relaxed (turn > ~25°: oblique incoming walls). Found in passing: a partition of another construction no longer forces the whole region to Skin + Web (the host envelope stitches across it; the partition keeps its skins, printed as its own route — not yet spliced).
  - **Mixed Skin + Web web:** the region's Wall Infill job is no longer dropped — it runs on the SKIN side's own envelope (`_split_skin_part`: generated bands closed off) whenever a Skin + Web member is present (`_web_member`).
  - **Parallel Walls route (designer decision; SUPERSEDES the pass-8 interior-lane kisses):** lane by lane. Every lane is cut once over a 2-bead window; lane k's end steps diagonally to lane k+1's start (parallel steps); one RETURN diagonal closes the route and crosses the steps (a crossing, never a retrace). All lane changes sit in that ONE seam zone (`_parallel_lanes`). Free ends keep the canonical nested caps (steps use the f0-side lanes of the nested loops); odd N cuts the middle lane too (open route, parity).
  - **Seam zone ↔ Move Start:** route origins now store their position (`pos`); the model passes them as seam hints (`_seam_hints` → `generate(…, seam_hints)`): the seam zone moves to the nearest origin on the wall, and origins resolve by position (`resolve_route_origins`), so the route starts there. Default: middle of the longest straight span.
  - (Generated systems stopping at junctions between their own members — still true after this pass — was RESOLVED in pass 10: walls meet by crossing printed paths.)
- **Pass 10 (2026-10-07): generated walls meet by CROSSING printed paths (decision).**
  - **Rule (designer):** preserve the native Wall System paths → allow real printed-path crossings / contact (a crossing is deposited-material contact, NOT a retrace) → add only the minimum transitions for one printable route → hide them where possible. Never cut or stop a valid generated path because another wall enters its nominal envelope; never deform good architectural geometry to tidy the routing graph. Several runs are acceptable where topology truly needs them; physically disconnected intersecting walls are not.
  - **Root cause of the non-contacting intersections:** a generated system filled a region along its skeleton runs; at every T / X junction the runs END at the junction node, so all walls' lanes stopped there (gaps; V1 "paths stop at junction ends"). The pass-6..9 splices then cut host stretches / opened caps locally.
  - **Representation now:** whenever a region holds more than one generated wall (or skins), EVERY generated wall gets its own band (`partkey`: per source; skin walls together; junction fillets between generated walls (`'__J'`) belong to neither band and are not printed — no pattern bends round them).
    - Bands are stitched by FOLLOWING the wall's own face lines (`_src_faces`: the wall's offsets — since the correction pass only their surviving pieces after trims / openings; `_face_follow`) — straight through an X or a branch mouth, round corners hidden inside another band; chords only at real ends.
    - X junction: both bands include the crossing square → both patterns run straight through and cross.
    - T junction: the branch's END chord is PUSHED straight into the host (measured from the host's actual face, to about its centre line + ¼ bead): its canonical end cap lies inside the host and its lanes cross the host's paths (`chords[].pushed`).
    - Skin branch (single / hollow) into a generated host: lanes run straight through the host's paths and are capped inside it (`wall_systems.extend_through`) — no host cut. Skin wall bridging generated walls (faces on two rings): both face ends at each mouth continue into the host and are capped (`~cross` beads).
    - Generated branch into a skin host: pushed across a straight host skin bead closing the mouth (replaces the pass-9 kiss).
    - The router already treats face-path crossings as junctions (degree-4 nodes: parity unchanged), so it solves one route through the contacts. `_connect_generated` / `connect_branch` (cut-and-splice) are retired from the pipeline (kept in code).
  - **Parallel seam:** window 2 beads → ½ bead (75 % less lane removed); the steps are nearly transverse and may cross.
  - **Measured:** reference network, hosts Skin + Web / Interleaved / Parallel × all 7 line constructions → 21/21 one route, no travel (Interleaved / Parallel on the whole network were 30–43 runs). All T fixtures (4 hosts × 5 branches × R 0 / 6), Parallel × Parallel X, Interleaved × Linked X, Parallel into a curved host: one route. Timings ~0.3 s effective, ≤ 1.3 s route.
  - **Legitimately several runs / open:** odd lane / path counts at free ends (parity); pieces separated by openings (travel between pieces); a partition of another construction (own route). An L of two SEPARATE line sources prints as two overlapping capped bands (one route) — a single polyline gives the canonical corner.
- **Tests (pass 10):** T junctions by crossing (4 hosts × 5 branches × R 0 / 6: crossings ≥ 2, branch reaches the host's centre, host lanes complete), X junctions (3 pairings), curved host, contact at every architectural junction of the reference network (3 hosts × 7 lines), compact seam. design_proto 1445.
- **Correction pass after checkpoint f448335 (2026-10-07; manually reviewed and accepted, committed and pushed 2026-10-08 as the correction checkpoint): compact generated junctions; Trim authoritative after Wall System assignment.**
  - **Junction rule (designer, refines pass 10):** guaranteed printed contact first; then a compact local junction with little redundant deposition; native paths kept outside the smallest junction zone; crossings allowed; DIRECT PASS-THROUGH CROSSING is the fallback whenever a cleaner solution could cost connectivity or need special casing. (Pass 10's "push to about the host's centre line" and "both walls run through an X" remain the fallback.)
  - **T into a Parallel Walls host** (`_push_reach`): the branch's outer cap rail lies just past the host's NEAR lane (half way to the next lane, ≤ the old centre-line depth) — the branch lanes cross that one host lane; nothing piles onto the inner lanes. Other hosts (waves / chain strands cross the centre line, not the near lane) keep the centre-line push. Skin branches unchanged.
  - **Parallel × Parallel X** (`_x_yield`): one wall passes through with every lane; the other YIELDS — its face is not followed across, it ends there as two branches pushed shallowly into the through wall (the stitch pairs each end with the start ACROSS its own band at the same mouth). Deterministic: thicker wall, then more lanes, then the earlier path passes through. Only for a true X (both centre lines continue straight ≥ half the thickness + a bead on both sides, arms on opposite sides — not a T, an L or a corner hidden in another band; `_x_arms`) and an EVEN lane count on the yielding wall (each half closes: no new runs). Everything else (odd lanes, waves, chain, mixed) passes through as in pass 10.
  - **Measured** (overlap square, deposited bead area ÷ covered area; a plain 4-lane wall is 1.2): Parallel × Parallel X 2.54 → 2.19 (the yielding wall prints ~31 in in the square vs > 40 in, nothing over the through wall's inner lanes); Parallel × Parallel T 1.92 → 1.80; Interleaved × Linked X unchanged 2.61 (fallback). Reference network (3 hosts × 3 line types incl. Parallel host / Parallel line): one route, no travel.
  - **Late-stage Trim corruption — root cause:** pass 10 stitches each generated wall's band by following its own face lines, and `_src_faces` held the UNTRIMMED full offsets. After a trim the band followed its face straight through the removed section (the full rectangle came back / unrelated geometry appeared), so a "trimmed" corner stayed printed and trimming again seemed to do nothing. Secondary: where two trimmed walls now meet at a mitred L corner, the push ray-cast missed the (trimmed) host and hit a FAR face of it → the band end was pushed right across the design.
  - **Fix (model level):** after the trim / opening plans, a cut wall's face lines are its SURVIVING pieces only (`_src_faces`, plus `_src_center` centre lines); the push ray only accepts a host face AT the mouth (within host thickness + 2 beads, and continuing past the mouth on both sides); with no such face the mouth is a CORNER: the end is squared at the chord's far end (fills the corner square, both bands overlap there = contact), never pushed out of the envelope. Order is now strictly design → trims / openings → resolved geometry → Wall System generation → routing; Wall System membership never makes source geometry uneditable.
  - **Client (undo / redo):** a trim that changes connectivity regroups a Skin + Web system's owned web records after the response; that regrouping was its own undo step, and Undo re-synced against the undone response → a new step that cleared Redo (Undo stuck behind the trim). Now the derived regrouping is folded into the edit's undo step (`_afterNetworkUpdate`), and `_histRestore` does not re-sync webs (`_histRestoring`).
  - **Tests:** `test_wall_systems.py` — compact Parallel × Parallel X, deterministic yield (order / thickness), pass-through fallbacks (odd lanes, Interleaved × Linked, Parallel × Chain), overlapping Parallel rects yield at both crossings, late Trim on overlapping rects (Parallel / Interleaved / Chain: only the trimmed envelope, nothing at removed sections, one route), repeated trims of overshooting lines (each stage clean, order-independent, removing trims restores exactly); the pass-10 T test now expects a Parallel host's branch just past its near lane. `tests/js/ui_trim_wall_system_smoke.js` (via `test_trim.py`): trims after assignment keep membership, the last section stays trimmable, regrouping is not an undo step, Undo / Redo coherent. The new trim / yield tests fail on f448335. design_proto 1459, layer_assembly 124, toolpath_proto 90 green.
- **Tests (pass 9):** reference network × 7 line constructions (one route, host web printed, no geometry outside the line's band, two kisses), lane-by-lane topology (N − 1 steps + 1 return in one zone, lane order), seam follows the route origin, partition, updated Parallel tests (crossings only in the seam zone, one seam window per lane); `ui_origin_smoke.js` (start position stored). design_proto 1411.
- **Tests (pass 8):** `test_wall_systems.py` (equivalent ends over corner distances, exterior-lane coverage on rings / curved wall / pieces, 16 incoming-generated junctions; 212 total), `ui_origin_smoke.js` (Move Start priority on an opening, click-to-place, open routes, leaving the mode).
- **Tests (pass 6):** `tests/test_wall_systems.py` (153: … + single branch → Interleaved / Linked / Chain / Parallel host at R 0 / 6 with straight-lane and on-material checks, crest vs trough depths, Junction R contact invariance, chain phase sweep, hollow branch, Single / Hollow / Parallel semantics) and `tests/js/ui_wall_system_smoke.js` (member list, Add path…, seven constructions, Single hides thickness, Parallel walls).

### Canonical corner motifs — Zigzag / Wave (2026-10-07; manually reviewed — Wall Systems checkpoint 2026-10-07)

- **Decision (designer):** significant corners are HARD lattice anchors with one canonical treatment, independent of spacing / phase. The ordinary lattice is fitted between them; Target Spacing is a preference between anchors. Adaptive Truss is deliberately unchanged.
- **Supersedes:** the pass-7 single-pass corner brace (one outer ↔ inner diagonal) and "V1 / V2 move a closed loop's phase" for CORNERED loops (corner-less loops keep it). Old tests rewritten to the new rule: `test_pass7` corner tests, `test_wall_lattice::test_wave_keeps_structure` (motif braces are straight), `test_infill_junctions` (motif stitches counted apart; V1/V2), `test_physical` (the wave ring no longer falls short of the contact at reflex corners).
- **Single pass** (reference: wall-web experiment Candidate B, lower-left corner of `out/08_multiple_openings.png`): Ia → Oa → Ob → Ib.
  - Inner face 0.55 t before / after the inner corner; outer face 0.30 t either side of the outer corner (Oa → Ob wraps it).
  - Contacts sit squarely off each face leg.
  - The segment-parity constraint means every corner is entered on its inner face.
- **Double pass** (reference: production's upper corners in the same sheet): the existing corner station (both phases, inner + outer corner points), now on every detected corner.
- **Corner detection:**
  - Added a localisation rule (turn over 3 t ≥ 0.75 × turn over 6 t). Sharp corners and Corner R ≤ ~20 on 10 in walls are corners; R 40, circles and gentle curves are not.
  - Cap-end turn readings are no longer rivals: the short leg beside an opening now gets its corner.
- **Measured:** reference stack support findings zigzag 51 → 55, wave 71 → 65, truss 65 (unchanged).
- **Tests:** `tests/test_corner_motifs.py` (18). Module doc §9.

### Adaptive Truss wall infill — production V1 (2026-10-07; manually reviewed — Wall Systems checkpoint 2026-10-07)

- **What:** a third Wall Infill pattern, **Adaptive Truss** (`pattern 'truss'`), next to Zigzag / Wave (both unchanged; not the default; no migration). It implements the wall-web experiment's phase-field direction INSIDE `wall_lattice.plan` (module doc §8); it is not a second lattice system. Runs, anchors (corners / ends / junctions / jambs), pass multiplicity, caps, junction pairing, lineage scaffold, tracking, Physical Rules and closure are production's.
- **Parameters** (`infill.TRUSS_PARAMETERS`, prototype defaults, NOT calibrated): Brace Angle 45° (15–75), Bond Length 3 in (= the default bead width), Max Unsupported Span 40 in (per skin), Min Turn Radius 1.5 in (½ bead), plus `seed` (Regenerate). No Target Spacing / V1–V2 for this pattern.
- **Mechanism:**
  - Station parameter u = S_eff·φ, φ = ∫ds/a, a = w·cot θ + bond. w = the cavity between the contact rails across the run (wall-relative angle). Counts are chosen with production's cost; stations are even in φ.
  - S_eff = the straight-wall pitch, standing in for Target Spacing. DMAX = max_span / 2. The wall / area test uses max(S_eff, max_span / 2).
  - Stitch: half bond → fillet → strip-coordinate brace → fillet → half bond. It is halved, then dropped where it doesn't fit (`truss_short` / `truss_plain`), then falls back to the ordinary constructions.
  - Lineage jamb margin scaled to the pitch (the zigzag-sized ½-thickness margin rejected every jamb).
- **Contact identity:** each segment's normalised station phases are in the track (`phases`). A tracked count change inserts / removes a PAIR locally (`_adapt_phases`).
- **Regenerate:** `params.seed` (UI "Solution N"; undo / redo via the snapshot history; lineage-owned like the rest of the lattice definition).
  - Seed 0 is the planner's own optimum. Seeds ≥ 1 apply hashed LOCAL choices `_hchoice(seed, feature key, kind)`, with keys quantised to 12 in: each run's preferred start face (alternatives ≤ 0.25 × thickness costlier), a nearly tied segment count (≤ 8 %), a corner-less loop's seam, and the gap a carried pair goes into.
  - Tracked runs hash their REFERENCE identity.
- **Measured:**
  - The reference network (round / miter, Base + gaps) closes as one route with the shared scaffold.
  - Assembly reference stack (32 layers) support findings: zigzag 51 / wave 71 / **truss 65**. Junction zones are similar (lens 20 vs 23; crossing 18 vs 15). The extra findings are on ordinary scaling walls: a longitudinal station drift moves a 45° brace sideways ≈ 0.7× the drift (zigzag 15°: ≈ 0.27×).
  - Assembly resolve ≈ 2.7× zigzag time.
  - Corner-anchor jitter under an opening edit is visible: ≤ 1.3 in on the far wall vs 0.3 in for zigzag.
- **Known limits / next:**
  - With Physical Rules, lone walls / dead ends / doubled runs keep production's mirrored out-and-back: two 45° passes, a dense chain of cells (review whether doubled runs should use 2a).
  - Strict alternation inside segments is the V1 default; same-skin motifs (loops / teardrops) at corners / junctions are the planned motif vocabulary (wall-web NOTE §11). The objective is both skins within the max span, not alternation.
  - Untracked edits can flip a segment count near a half-integer phase (that segment re-phases).
  - The field fallback for wide areas uses Target Spacing 20.
- **Tests:** `tests/test_adaptive_truss.py` (21, incl. `tests/js/ui_truss_smoke.js`).

### Wall-web research experiment (2026-10-07; EXPERIMENTAL — not adopted, production unchanged)

- **Question:** what mathematical rule should generate the structural web between a wall's two skins? Harness and full note: `design_proto/experiments/wall_web/` (`NOTE.md`; `run.py` regenerates `out/index.html` + `results.json` in ≈ 30 s; `sheets.py` makes PNG review sheets `out/01_straight.png` … `out/10_cross_z_tracking.png`; `out/` is gitignored). Reuses the production skeleton / `_Geo` read-only; not wired into the UI.
- **Compared:** production (zigzag / wave, S 20) vs **A, adaptive truss phase field** (brace angle θ, bond b, turn radius r, max skin span D, congestion floor; crossing advance a = w·cot θ + b; integer phases between production's anchors) vs **B, fixed-angle boundary propagation** (structural billiard), on 12 fixtures incl. openings, T / X junctions and the reference network, with perturbation and cross-Z tests.
- **Findings (measured):**
  - Production at the default S 20 on 10 in walls braces at ≈ 15–16° (cavity 5.5 in between contact rails); A at the equivalent θ reproduces production's stations (median 0.06 in).
  - Spacing also acts as the wall / area classifier (thickness ≤ 1.6·S), capping braces at ≈ 41° on 10 in walls.
  - B is rejected as a generator: initial-value propagation is neutral (never damps) and discontinuous at terminations. A 1 in bump removed 31 contacts; a 2 in opening move shifted 100 %; brace counts jumped 35 → 68 between layers; it grazes past the inner skin when cos θ ≥ R_i/R_o (whispering gallery; U bend fails below ≈ 28°).
  - Alternating ±θ diagrids are unprintable for stacked mud (overhang). A diagrid can only be a slow per-layer phase drift.
  - Any uniform two-point station layout (production and A) re-phases a whole segment when its count changes. Locality needs: crossings changed only in PAIRS; per-segment canonical start faces + corner braces; a canonical run direction / face (skeleton labels flip between layers). With these, tracked A kept contacts within ≈ 0.4 in per layer.
- **Physical objective (designer clarification, 2026-10-07; durable requirement):** the web must keep BOTH skins bonded and supported. Strict alternation between skins is NOT required: cross-wall braces are the normal mechanism, but same-skin contacts (loops / teardrops / same-skin braces, e.g. outer → inner → inner → outer round a concave corner) are allowed where useful. Evaluation rule: **neither skin may exceed its maximum unsupported longitudinal span**. Distinguish useful local same-skin contacts from pathological grazing (the opposite skin neglected beyond the span).
- **Proposal (NOT adopted; needs designer review):** a two-level hybrid. The anchored adaptive truss / phase field (A) is the global rule (density, contact identity, locality, cross-Z). A local, evaluation-chosen motif vocabulary (loops, teardrops, same-skin braces, diamonds) replaces it only in windows at corners / tight curvature / junctions / ends, entering and leaving at the phase field's boundary contacts. Not implemented.
- **Original proposal detail:** keep the skeleton / anchors / route layer / tracking; replace in-segment station placement + stitch shape with A. Parameters: θ primary, flat bond, filleted turns, explicit D, hysteresis + pair insertion. Decouple the wall classifier from spacing. Integration path and open questions in `NOTE.md` §11–12.

### Trim ghost fix (2026-10-06, Designer checkpoint 2026-10-06)

- **Bug (manual):** trimmed circle × rectangle (10 in walls); selecting the rectangle drew a ghost of the untrimmed rectangle: selection outline, hover highlight, outline handles, and hit-testing on trimmed-away sections.
- **Root cause:** all selection / editing visuals and `hitTestPath` used the parametric source polyline (`path.points`), unaware of Trim.
- **Fix (UI only; Trim semantics and geometry unchanged):** `_visibleSourcePolys(p)` returns the source minus its trimmed sections, from the Trim section data. The selection outline, hover highlight and hit-testing use it.
  - `visibleHandles(p)` drops outline handles lying only on trimmed-away geometry (e.g. a rectangle corner inside the circle). The circle's radius handle moves onto the visible arc, since its drag uses only the distance to the centre. Off-outline handles (centre, bend) and the rotation handle stay, so move / resize / rotate keep working.
  - Without trims, or after Undo, everything is the normal full representation.
- **Superseded:** the Trim smoke check "clicking a trimmed section still selects the line" — selection now favours visible geometry.
- **Test:** `tests/test_trim_ghost.py` + `tests/js/ui_trim_ghost_smoke.js`, built from real backend sections.

### View navigation, persistent Wall Network, printable-line rendering (2026-10-06, Designer checkpoint 2026-10-06)

- **Rendering rule (decision; SUPERSEDES the earlier views):** whenever the resolved printable centrelines are known, printed geometry is drawn ONLY as itself.
  - **Beads OFF:** ONE thin blue line per printable centreline: the toolpath print moves, or `drawPrintableLines` when no route is shown.
  - **Beads ON:** ONE thick blue bead.
  - Nothing is layered under or through it. No role-coloured effective faces (the old "sandwich": teal faces / caps under blue print lines), no reference line except the selected path's.
  - The design is drawn only for the SELECTED path (thin white) and a path being dragged (live; its stale strands are hidden).
  - Indicators stay on top: openings, junction diamonds, arrows, markers, travel.
- **Wall Network = a persistent Design-sidebar section** (below Wall Geometry), no selection needed. It shows the selected path's network, else the one picked in a compact selector (only with several networks), else the first.
  - It holds Network Wall Thickness / Alignment / members / "+ Add infill to network".
  - A path's Properties show only its membership (plus its inherited-wall / override rows). Semantics unchanged.
- **Zoom / pan (view only):** part of the world ↔ canvas transform (`view = {z, px, py}`), not a CSS transform, so every hit test, drag, snap and draw works at any zoom.
  - Wheel / pinch zooms at the pointer: the world point under the cursor stays put. Range 25 %–2400 %.
  - Space + drag or middle-drag pans.
  - Canvas overlay: Fit (frames the printable / design geometry), 100 % (the default view: the 400 in workspace), and a % readout.
  - `HIT_DIST` / `SNAP_RADIUS` scale with 1 / zoom (constant on screen). Bead width follows the zoom.
- **Tests:**
  - `tests/js/ui_view_smoke.js` (via test_app.py): round trips, zoom at the cursor, clamps, pan, wheel, Space / middle drag, Fit, 100 %; selection, body / handle drag, drawing, opening place + slide, Trim hover / click, junction selection and Route Origin drag, all at zoom + pan; Beads OFF / ON rendering.
  - `ui_network_smoke.js`: the section shows without a selection; a selector appears with 2 networks.
  - Layout test: `network-section` is in Design; the view controls are on the canvas.

### Attached single-bead branches close; markers never edit geometry; blue beads (2026-10-06, Designer checkpoint 2026-10-06)

**Bug (manual):** single-bead rect + open single-bead branch + an opening, physical ON → 1 component, 1 travel, Start ≠ End, Closed 0/1.
- **Root cause** (4 odd nodes):
  - (1) The branch became two lanes, but they landed on the rect's single bead, which stayed whole across the branch mouth: WIRE beads are always kept, and the network only joins thick-into-thick. That left degree-3 landings.
  - (2) The opening left the single-bead rect with two dead ends; the return-lane rule only covered open SOURCES.
- **Fix — geometry, not routing (decision):**
  - (a) A closed single-bead wall carrying an opening is a RETURN-LANE wall too (U-turns at the cut). Not done when it anchors an infill.
  - (b) **Mouth cut** (`PrintLayer._mouth_cuts`): where a return-lane branch ends on a single-bead host, the host is cut exactly between the two lanes' landings. It reuses the trim cutting with exact shared points; each lane is carried along the branch's end tangent to the host. The branch gets no U-turn there.
  - The mouth is a new **`network.SPLIT`** bead: it splits regions for classification but is NEVER printed. Without it the branch band merged with the room and its faces were dropped as void|void.
  - A declared infill region survives a mouth cut.
- **Result:** one closed run, 0 travel, 0 retrace; lanes W − R apart. This generalizes to oblique branches, inside branches, a circle host, two branches, and an open host line (which is itself two-pass). This resolves physical-rules limitation (4).

**Route markers win hit-testing (decision):** with Toolpath ON, any route marker under the pointer takes the click before paths, openings and handles.
- The origin drags and changes only `route_origins`.
- The Start / End of an OPEN route just explain themselves. Previously, clicking the green START of the then-open route fell through to the opening beneath and dragged it.

**Bead rendering (SUPERSEDES the opaque-mud + hairline rendering above):** Beads ON = every printable centreline drawn ONLY as one solid blue stroke (the print colour), Bead Width wide, round caps, composited once, opaque.
- Nothing is drawn through it: no centreline, no print / retrace toolpath line, no reference line except the selected path's.
- Travel, arrows, markers and junctions stay on top. Beads OFF = the normal vector view.

**Tests:**
- `tests/test_attached_branch.py` (18): the regression fixture with and without the opening is one closed run; lanes = W − R over a sweep of R; lanes join the host exactly (no U-turn at the host, no printed mouth); the opening stays open; generalizations; the origin around the circuit changes only the start; legacy unchanged.
- `ui_origin_smoke.js`: origin over an opening drags the origin only; an open route's Start over an opening moves nothing; one blue stroke per printable; nothing drawn through the bead.
- Fixtures: `physicalFixture('rect_branch' | 'rect_branch_closed')`.

### Route Origin + solid bead rendering (2026-10-06, Designer checkpoint 2026-10-06)

**Route Origin (decision):** the point on a CLOSED printable route (start = end) where traversal begins and returns. A closed circuit has no intrinsic start, so the designer chooses it.
- **Stored as design state** (undoable, in every payload): `layer.route_origins = [{strand, u}]`. `strand` is a printable strand id (the router's strand, stable across ordinary rebuilds); `u` is the fraction of its length. At most one origin per connected component.
- **Backend:** `model.resolve_route_origins` inserts the point as a collinear vertex, so the geometry is unchanged. `graph.route_layer(origins=…)` starts that component's circuit there; components are also entered there.
  - An OPEN component (one the rules could not close) ignores its origin and keeps its own Start / End.
  - A missing strand → `status: 'missing'` → automatic selection. The origin is never moved onto other geometry.
  - If the strand still exists after an edit (e.g. a moved wall), the origin keeps its fraction u along it.
  - `/api/route` returns `origins` (resolved / missing).
- **UI:**
  - ONE green dot per closed run (no START / END labels); START + END dots and labels only for open runs. The legend is updated, and the old yellow seam diamond is removed (the origin marks that point).
  - With Toolpath ON, the origin dot is dragged in Edit mode. It is projected onto the printable strands of ITS OWN run (not free XY), and release is one undo step.
  - Playback begins at the route start, i.e. the first component's origin.
- Groundwork for multi-layer seam / origin planning; component ordering stays future work.
- **Limitations:** the strand id of a network-modified chain (`…~nK`) can be renumbered by a topology change, and the origin then falls back to automatic. The older `constraints.start_path_id / start_t` (no UI) still pins only the first component when no origin applies.

**Bead rendering (clarification):**
- The bead layer is composited OPAQUE: one solid mud strip, all beads drawn into one layer once, so overlaps never darken.
- With Beads ON, centrelines and toolpath lines become hairlines (≤ 0.75 px, 60 % opacity) on top. The rendering itself adds no apparent thickness, so a 0.75 in contact overlap can be judged visually.
- Arrows and origin / Start / End dots stay on top. Rendering only: no geometry change.

**Tests:**
- `tests/test_route_origin.py` (7): origin moves the start, not the geometry; persists through a rebuild; follows its strand when the wall moves; missing → automatic; one origin per disconnected component; an open component keeps Start / End.
- `tests/js/ui_origin_smoke.js`: markers (origin vs Start / End); drag projected onto its own run; {strand, u} stored; one undo step; geometry untouched; per-component origins; undo / redo; playback start; opaque single composite; hairline + subtle lines over beads; normal widths with Beads off.

### Physical deposition rules — Contact Overlap, Return-Lane Overlap, no retrace, closed routes (2026-10-06, Designer checkpoint 2026-10-06)

**Decision: centrelines are physical beads.** `MaterialSpec` (material.py) holds `bead_width` W, `contact_overlap` O, `return_overlap` R and `physical`.
- **The switch:** the app sends `physical: true` by default (Material / Bead → Physical rules). The bare engine default stays the zero-width legacy, so the generic toolpath prototype and its retrace router keep their contract. All legacy tests run unchanged.
- **Four distinct concepts.** O and R share the formula "centreline separation = W − overlap", but they are separate fields, helpers and code paths: machine testing may give them very different values.
  - **BEAD WIDTH:** one pass.
  - **CONTACT OVERLAP:** a generated bead intentionally LANDS against a printed one. Separation W − O, with 0 ≤ O ≤ W; O = W means the centrelines coincide.
  - **RETURN-LANE OVERLAP:** a pass runs beside the pass it replaces a retrace of. Separation W − R, with 0 ≤ R ≤ W − 0.25; R = W would be an exact retrace.
  - **CROSSING:** centrelines meant to cross. No overlap rule applies.
- **Provisional defaults:** W 3.00, O 0.75, R 0.75 in (25 %). Contacts are 2.25 in apart; a single-line wall is ≈ 5.25 in wide.
- **Architecture (decision): the generators make physically valid, closable geometry; the router never repairs it.**
  - Router (`graph.route_layer(allow_retrace=False)`): no `_augment_by_retrace`. Every component routes as a closed circuit. Odd nodes are paired by explicit TRAVEL and reported (`closure_report`, the "Closed components" metric turns red), never printed over. API `/api/route` returns `closure`.
- **Contact Overlap (wall lattice, `wall_lattice.plan(contact=W − O, closed=True)`):**
  - Interior stitch landings stop W − O off the face centreline. The stitch is built on "contact rails", so zigzag and wave keep their shape. Rails are pushed along the across line until truly W − O clear: at a corner brace a straight offset would be only (W − O)/√2 from each leg.
  - Stitches must also stay clear mid-curve (else Hermite / straight fallback).
  - Route CONNECTIONS stay exact: pass ends at junction corners, plus ONE **transfer landing** per face ring the lattice would otherwise not touch (a closed ring wall: 2; a lone wall: 1). Converting an interior landing is parity-neutral (+2), so the route-aware motif design is unchanged.
  - Crossings between phases are untouched.
  - The report adds `contact`, `transfers`, `contact_min` and `contact_violations`. Stitches into or out of an exact connection are exempt within one target spacing.
  - Other generators (field fallback, solid) are NOT changed yet.
- **Return-Lane Overlap (dead-end / single-line walls):** an OPEN single-bead source becomes a TWO-PASS wall through the existing wall machinery, recorded in `PrintLayer._return_lanes` and `network.return_lanes`. This excludes paths that already have Wall Thickness, a network wall or extra offsets, and closed paths.
  - Its two passes are W − R apart, with semicircular U-turns (cap style `full_round` for these systems only). The drawn path becomes the unprinted reference.
  - Physical width 2W − R. Networks, trims and openings work unchanged: a thin X becomes one closed loop.
- **Lattice second wave:** a lone wall run (no junction) gets a CLOSED out-and-back loop: two phases joined by cap Vs at both ends, motif `lone_loop`.
- **Results (tests/physical_fixtures.py, physical):** every fixture's components are closed, with 0 retrace and travel only between genuinely disconnected components:
  - wave / zigzag ring 1 run; lone wall 1; X walls 1; single line 1; thin X 1; star 1; dead-end branch 1; islands 2 runs, 1 travel.
  - Legacy controls show what the rules fix: lone wall and single line open, thin X 2 retraces.
- **Superseded:** exact retrace as a last resort (router, single-bead dead ends), lone lattice runs as open routes, Bead Width "visual only" (now a geometry input when physical; still visual with the rules off). Updated tests: `ui_bead_smoke.js` and the material API test, for the superseded visual-only rule and the extended material schema.
- **Limitations / open:**
  - (1) With a large separation relative to the wall (e.g. O = 0.25 → 2.75 in in a 10 in wall) reflex corners and cap Vs cannot keep the full clearance. This is reported in `contact_violations`.
  - (2) SOLID infill still uses in-component travel hops (earlier decision) → reported as an open component. This conflicts with the closed-route constraint and is a pending decision.
  - (3) The wide-region field fallback is unchanged: its repair may still leave odd ends, which are now travel and reported.
  - (4) *(RESOLVED 2026-10-06 by the mouth cut — see "Attached single-bead branches close".)*
  - (5) The "Closed loop" routing override is moot with physical rules on.
- **Tests:** `tests/test_physical.py` (46). Covered: closed + no retrace + travel only between components + start = end per run, for 8 fixtures; legacy controls; the router reports instead of retracing; contact landings at W − O over a sweep of O (zigzag / wave, ring / lone); O moves landings; O = W lands on the face; the shortfall is reported; crossings untouched; transfers; return lanes W − R over a sweep of R with semicircular U-turns and width 2W − R; O and R independent; two-pass network X; exclusions; lone-wall second wave; trim + rounded junction + opening under the rules; API.
  - Browser fixtures: `tests/js/physical_fixtures_console.js` (`physicalFixture('wave_ring' | 'zigzag_ring' | 'x_walls' | 'thin_x' | 'dead_end_line' | 'lone_wall' | 'star' | 'islands' | 'branch')`).

### Material / Bead — first pass: physical bead visualisation (2026-10-05, Designer checkpoint 2026-10-06)

**Concept (decision): three distinct things.**
- **Wall Thickness:** the architectural thickness of a wall assembly (design).
- **Centerline:** the vector path the nozzle follows (toolpath).
- **Bead Width:** the physical width of ONE deposited mud pass (material).

MATERIAL / BEAD is a peer category to Design and Print / Toolpath. It will grow into the physical deposition rules.
- **Representation:**
  - `material.py` defines `MaterialSpec(bead_width=3.0)`, held as `PrintLayer.material` (JS: `layer.material.bead_width`). It is design state, so it is undoable and sent in every payload, ready to become a geometry input.
  - **Bead display** (on/off, default OFF) is a VIEW toggle, like Arrows and Dimensions.
  - Clear All keeps the material.
- **Bead width is ONE project-wide value (2026-10-06, decision):** owned by the lineage root's document (Base's `material.bead_width`); derived Layer Designs never store their own (`/api/layer_designs/delta` moves such an edit to the root: `material_moved`). The Material panel and the Assembly's Physical support edit the same value (`projectBeadWidth` / `setProjectBeadWidth`, `window.Designer.beadWidth / setBeadWidth`). Details and invalidation: `layer_assembly/PROJECT_MEMORY.md` → "Designer ↔ Assembly contract".
- **Footprint:** the centerline swept by a disk of the bead width (Minkowski sum). This gives half-width w/2, ROUND ends and round joins; a straight open line is a capsule, and closed paths have no ends.
  - Python: `material.in_footprint`.
  - Canvas: round-cap, round-join strokes of the bead width, all drawn into ONE offscreen layer and composited once at α 0.55, under the centerlines. Overlaps union with no seams, and the centerlines and toolpath stay visible on top.
- **Only resolved PRINTABLE geometry gets a bead.** The API returns `printable` (`PrintLayer.printable_centerlines`): exactly the strands handed to the router.
  - So the beads reflect trims, openings, derived wall faces, caps, rounded junctions, wall lattice, field and solid infill.
  - Centred-wall reference lines, construction geometry and non-printed region boundaries get none.
  - While Bead display is on, the frontend always fetches, even for a single path.
- **Visual only (decision):** Bead Width changes NO geometry — walls, infill spacing, lattice, routing, trims, openings, junctions. We want to see the physical footprint before deciding the rules. Changing it is one undo step, with no refetch and no reroute.
- **UI:**
  - The right column has two peer sections: **Material / Bead** (Show bead, Bead width in 0.25 in steps, minimum 0.25) above **Print / Toolpath**.
  - The toolbar has a **Beads** toggle next to Arrows / Dimensions, synced with the checkbox.
- **Future rule — Contact Overlap (documented, NOT implemented):** the intended physical penetration between two beads at an intentional contact.
  - Two beads of width w touch when their centerlines are w apart; for an overlap o they should be (w − o) apart.
  - Example: a wave wall-lattice stitch currently runs to the face's CENTERLINE; physically it should stop about (w − o) from it.
  - Contact Overlap will become a `MaterialSpec` field that moves generated centerlines. Later rules may include bead height, minimum bend radius and congestion.
- **Limitations:**
  - While a path is being dragged, beads show the last backend result until the refresh.
  - A self-overlapping single path unions with itself; that is correct physically, but per-pass overlap is not shown.
- **Tests:**
  - `tests/test_material.py` (15): capsule / round ends, curve, closed path, printable == routed strands, the centred reference gets no bead, faces and caps, trim, opening, rounded junction arcs, wall lattice, solid infill, width changes no geometry, API.
  - `tests/js/ui_bead_smoke.js`: OFF by default, one round-cap / round-join stroke per printable centerline at the bead width, closed loop, 0.25 snapping, undo, no reroute, OFF again, nothing without printable geometry.
  - Checked in headless Chrome: the pixels of a straight line form a capsule (round, not square, ends).

### Rounded junctions as wall assemblies (2026-10-05, Designer checkpoint 2026-10-06)

**Bug (manual Trim test):** trimmed Circle × Rect, one 10 in network wall, Junctions = Rounded, Junction R increased. The inner face froze at a few inches while the outer face kept growing, then the outer face kinked.
- **Root cause, three parts:**
  1. Each face corner was filleted INDEPENDENTLY with the same r.
  2. Each fillet was clamped to 45 % of its own resolved CHAIN. Chains are split at plain arrangement nodes, e.g. where a hub or T join-extension bead meets the wall bead, so the inner face's legs were 5–10 in stubs (tangent frozen at 2.5–4.4 in).
  3. The arc was built from the legs' first-segment directions with its ends FORCED onto the polyline at arc length t. That is wrong once t passes a leg vertex (the rect corner 15 in away) or runs along a curved face → kinks of 14–42°.
- **Decision — Junction R is a property of the physical wall junction:**
  - Faces are followed through plain face nodes up to the next real feature. Usable leg: 45 % up to another corner or a T/X/end node; 90 % up to a sharp face vertex (> 25°, e.g. a rectangle corner; a short straight remains).
  - The fillet is the exact tangent circle on the actual straight or curved faces: offset intersection, then Newton refinement so the centre is exactly r from both faces.
  - A WALL TURN is a convex face corner (material inside it) paired with the concave corner of the same two walls on its bisector. **Junction R = the radius of the OUTER (convex) face; the inner face is concentric** (radius = distance from the same centre ≈ R − wall thickness; sharp while R ≤ W). The inner corner's own setting is not used; its panel says so.
  - T / X / Y corners (concave only) keep R itself.
  - The largest R ≤ requested that fits the WHOLE assembly (both faces) is used by both. Beyond it the geometry stays fixed.
  - `junctions[]` reports `actual_radius / limited / partner / derived`. The junction panel shows "Requested · Actual (geometry limit)", and Wall Geometry notes how many junctions are limited.
  - Arcs: 36 samples per half turn, and chords ≤ 2 in.
- **Effect on existing geometry:** the T / Y / X rounded fixtures are byte-identical, except T(R=2): the old code silently built 1.45 in there (the same chain-split clamp), and it now builds 2.0 in. Miter is unchanged.
- **Tests:** `tests/test_junction_rounding.py` (41). The manual fixture is swept over R = 1…400 with invariants: no tiny pieces, no face crossings, continuous faces, arc tangent to its legs (< 3°), concentric pairs with radius difference = W, monotonic and coherent limits, stable beyond the limit, 2 closed runs. Also covered: circle / rect moved or resized after trim, W = 6 / 16, untrimmed, L corner (R ≤ W → inner sharp, huge R limited), Miter = R 0, T / Y / X, opening near a rounded corner. The committed network.py fails the manual sweep (14 / 14).

### Trim — non-destructive section suppression (2026-10-05, Designer checkpoint 2026-10-06)

**What:** a toolbar **Trim** tool, as in CAD. Hovering highlights ONLY the section under the pointer, in orange-red. Clicking suppresses that section, and Trim stays active for more clicks. Esc or a tool switch exits. One click is one undo step.
- **Section:** the stretch of a source path between two consecutive contacts with OTHER sources, or between a contact and an open end.
  - Contacts are found on the DESIGN geometry: processed source polylines after end snapping, before wall offsets.
  - They come from `trim.py`, which reuses the network contact primitives (`_seg_list`, `_contact_events`, `_Grid`). It is not a new segmentation system.
  - Self-intersections do not split a path. A closed path needs ≥ 2 contacts and an open path ≥ 1. A path that touches nothing is one object: Delete it, not Trim it.
  - Each physical contact has ONE representative point, shared by every path meeting there, so trimmed ends meet exactly. Without this the router saw near-coincident ends as disconnected (2 runs instead of 1).
- **Representation (decision):** `layer.trims` holds `Trim {id, source_path_id, start, end, inside, u_mid}`, a layer-level record like openings.
  - It stores a topological SIGNATURE, never coordinates: the bounding source ids at each end (as an unordered pair), and whether the section's midpoint lies inside each CLOSED bounding source. `u_mid` (midpoint as a fraction of length) only breaks ties between sections with the same signature.
  - The source itself is never edited: a Circle stays a CirclePath, and removing the trim restores the exact geometry.
  - Rejected alternatives: a frozen arc-length / coordinate interval, which breaks on any edit; and a constraint system, which is overkill.
- **Resolution:** on every build, every trim is matched against the current sections.
  - One match → suppressed. Several matches → the one containing `u_mid`.
  - No match (an intersection is gone, or a bounding path was deleted) → `unresolved`. Several matches with no `u_mid` containment → `ambiguous`.
  - In both cases the record is kept, NOTHING is suppressed, and the reason is shown in the path's Properties next to a "Restore trimmed sections" button. A trim is never moved onto an unrelated section.
  - Sections always come from UNTRIMMED geometry, so trims are independent of each other and of their order.
  - Verified: trims follow moves and resizes of either the circle or the rect; moving the circle off the rect makes them unresolved.
- **Pipeline stage:** 1c in `_build_effective`, after snapping. Resolved trims become removed arc intervals in the existing `_OpeningPlan`, which cuts the whole wall assembly (source + offsets, computed on the full wall). Downstream consumes the pieces normally: networks, junctions, material regions, infill and routing. Removed geometry is absent, not merely hidden.
  - **Trim ≠ Opening:** a trim interval gets no clear void and no cap-reach widening. Its ends are FREE ends, not `cut`, so the network T-joins a trimmed wall into the wall it ends on, or hub-mitres two trimmed ends meeting at a contact (circle × rect with both inside sections trimmed → one outline with mitred corners). With no host it is capped like any open end.
  - A trimmed closed source no longer bounds a region: it is excluded from `_nesting`, `_opening_partners`, `_region_openings` and declared regions. A SOLID infill on a trimmed boundary therefore reports no region. An outline made of several trimmed sources is not yet a region.
- **With Wall Systems (correction pass 2026-10-07):** trims cut generated walls exactly like any wall — the generated bands follow only the surviving face pieces (see Wall Systems → "Correction pass after checkpoint f448335"); membership / settings are untouched by trims.
- **Copy / duplicate (decision):** trims do NOT travel with a copy. A trim is relational: it is defined by intersections with OTHER specific paths, and a copy placed elsewhere has different intersections. This matches openings, which also stay with the original. Deleting a path deletes its own trims. Trims of other paths that it bounded are kept and become unresolved (Undo brings the path back).
- **Network fix needed by Trim:** in `network.find_attachments`, the "end buried inside another thick band" fallback now skips an end lying on that piece's OWN end. The first T test already did this, so such ends form a hub. A point on a band boundary counted as inside, which turned a two-wall corner into a T plus a leftover cap. No existing test changed.
- **Limitations:**
  - A contact with a section that is itself trimmed away still splits the other path. Sections come from untrimmed geometry; this was chosen for order-independence.
  - Selecting in Edit mode still hit-tests the full source path.
  - While a trimmed source is being dragged, the full source is drawn until the refresh.
  - A trimmed centered wall's dashed reference line shows only its remaining sections.
- **Files:** `trim.py` (sections + resolution); `model.py` (`Trim`, `PrintLayer.trims`, `_merge_removed` extracted from `_opening_removed_intervals` unchanged, `_OpeningPlan(trims=…)`, stage 1c, region exclusions); `app.py` (deserialise); `static/app.js` (Trim tool, hover, Restore); `index.html` (button).
- **Tests:** `tests/test_trim.py` (38 + the UI smoke) and `tests/trim_fixtures.py`. `tests/js/ui_trim_smoke.js` covers hover, stale sections, one undo step, Esc, Restore, duplicate, delete and Clear All. For browser inspection, paste `tests/js/trim_fixtures_console.js` into the console, then run `trimFixture('circle_rect', 10)` etc.

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

- Physical rules vs SOLID infill: the closed-route constraint (zero travel inside a component) conflicts with the earlier solid decision ("a short travel beats distorted print geometry"). Should solid infill close by construction (and how), or stay an allowed exception? Also: physical rules for the wide-region field fallback; per-machine values of O and R. (The single-bead loop + two-pass branch case was resolved 2026-10-06 by the mouth cut.)
- **Wall-lattice corner / junction motif vocabulary (future design direction, 2026-10-06; NOT implemented).**
  - **Direction:** the lattice generator need not converge on ONE universal algorithmic corner treatment. Instead it should eventually have a VOCABULARY / library of mathematically and physically useful local motifs for solving corners, junctions, turns and other difficult lattice transitions.
  - These are tools available to the GENERATOR, not user-selectable styles or toggles.
  - **First concrete reference:** the designer's loop / teardrop sketch from manual testing. The lattice path enters a corner, curls round in a smooth loop and exits again, a cleaner, more intentional transition than forcing the normal wave / zigzag through the corner. It is an example, not a prescribed solution.
  - **Other candidate families:** S-shaped transitions; figure-eight / interlaced transitions; woven or Celtic-knot-like constructions; tangent circular / elliptical constructions; curvature-continuous transitions; other clean motifs found by experiment.
  - **Possible selection:** generate several geometrically valid candidate motifs for a local situation, then choose by physical / toolpath criteria:
    - smoothness / curvature, structural support, continuity, avoiding exact retrace;
    - bead width, contact overlap, material buildup / congestion, even material distribution;
    - spacing, local geometry, clean integration with the neighbouring lattice.
  - The motifs are NOT primarily decorative: they are candidate solutions to physical deposition and routing problems.
  - **Sequencing (decision):** do not build motif selection yet. Bead Width has just been introduced and Contact Overlap is the next physical rule to investigate. Those physical parameters should be used to EVALUATE corner motifs before a motif system is invented.
  - Existing motifs (corner brace, junction hand-off, cap V; see the wall-lattice section) would become members of this vocabulary.
  - **Second observed reference (2026-10-06, manual testing):** the CURRENT zigzag lattice naturally forms a clean DIAMOND / brace-like transition through a corner / junction, seen where the rectangular wall meets an attached branch. The designer rates it a strong example alongside the loop / teardrop.
  - Both are examples for a future GENERATOR-side vocabulary. Neither becomes a user-selectable style, and motif selection is NOT hard-coded now.
- Material / Bead: what Contact Overlap does the material need, and where should it act first (wall lattice → face, solid infill → perimeter, junctions)? What is the real bead width of the current nozzle and mix?
- Trim: should an outline closed by SEVERAL trimmed sources (e.g. rect + circle with both inside sections trimmed) become a fillable region? Today only single closed sources declare regions.
- Trim: should a contact with an already-trimmed-away section still split other paths? Today it does, which keeps trims order-independent. Explicit Extend / Join / Fillet tools are possible future work, not planned.
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
- *(ANSWERED by the Pass 8 spec: even per-face support at ≈ the target — mirrored phases.)* Still to confirm on the machine: is doubling the wall crossings in dead-end arms acceptable for material use?
- *(ANSWERED, Pass 8 correction: hairpins rejected; boundary support is opt-in.)* Solid web: is amplitude ½ spacing (tangential contacts) the right tie for mud, or should neighbouring strands overlap slightly? Should travel around voids be reduced by a cellular-decomposition ordering?
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

Current priority order (after the Wall Systems checkpoint, 2026-10-07, and its correction checkpoint, 2026-10-08; Wall Systems, canonical corner motifs and Adaptive Truss manually reviewed and accepted):

0. **Machine testing of Wall Systems** (correction pass manually reviewed and accepted 2026-10-08) (bead overlap of closely spaced lanes, crossings at junctions, Parallel seam, Chained Loop return lap) and of Adaptive Truss; then address the known Wall Systems limitations listed in that section (wave end-lane permutation vs exterior lanes, partitions of another construction, L corners of separate lines).
1. **Adaptive Truss iteration** after machine feedback (motif vocabulary at corners / junctions; drift-aware tracking for steep braces).
2. **Transformed-junction lattice coherence** — the one open geometry problem; precise statement in "Z PHASE — CURRENT ARCHITECTURE → Transformed-junction lattice". Attack only this in its own pass.
3. **Cheaper transformed layers:** reuse stable topology (carry stations / geometry instead of a full plan per variant); share the untransformed tracking reference across worker processes.
4. Decide whether the run-scale doubling of jammed lattice runs is acceptable on the machine; persistent attachment constraints (also needed by semantic transforms); a transition motif when a tracked count must change.
5. Decide the SOLID-infill vs closed-route conflict (Open Questions); physical rules for the wide-region field fallback.
6. Deferred housekeeping (each its own reviewed pass): the vacuous `return_path` test helper; SolidPlan.connectors / solid_link leftovers; request sequencing in the UI; `~` in path ids; exact-float matching; history / restore gaps; inset previews after sidebar edits; cap-style alias mismatch; float(None) payload robustness; the unused routing-graph build; geometry-helper / tolerance consolidation; feature-named test files. Known non-deterministic order of `route_plan.corrections` (geometry unaffected; present since 414c283).
7. Multi-select + group transforms; copying attached offsets / infills with a path; travel-order optimisation; calibrate CLEARANCE_RADIUS / degree caps from the real bead width; the Z / layer planner using RouteEnds.

(Browser reviews of Pass 8, wall regions and pass 7 — items "0 / 0a" before the Designer checkpoint — remain open; the checkpoint-reviewed items were completed 2026-10-06.)

---

## Last Updated

2026-10-08 — CORRECTION CHECKPOINT (committed and pushed; the git log holds the hash): the correction pass below (compact generated-wall junctions, authoritative late-stage Trim) passed manual review. design_proto 1459, layer_assembly 124, toolpath_proto 90 green.

2026-10-07 — CORRECTION PASS after the Wall Systems checkpoint (uncommitted): compact Parallel junctions (shallow push into Parallel hosts; Parallel × Parallel X yields, pass-through fallback) and Trim authoritative after Wall System assignment (surviving face lines, corner-safe pushes, coherent undo / redo of web regrouping). Test results in the section "Correction pass after checkpoint f448335".

2026-10-07 — WALL SYSTEMS CHECKPOINT (committed and pushed; the git log holds the hash): Wall Systems V1 → pass 10, canonical corner motifs, Adaptive Truss, route start / travel order, Move Start, the wall-web research experiment (source only). All "uncommitted" entries below, back to the Z-phase checkpoint 8d9d439, are included in it. design_proto 1445, layer_assembly 124, toolpath_proto 90, pi-interface green (see the commit).

2026-10-07 — WALL SYSTEM pass 10 (uncommitted): generated walls meet by crossing printed paths (per-wall bands, face-following stitching, pushed T ends, skin branches through hosts); compact Parallel seam (½ bead). design_proto 1445, layer_assembly 124, toolpath_proto 90 green.

2026-10-07 — WALL SYSTEM pass 9 (uncommitted): cross-ring envelope stitching (no phantom connectors), generated walls entering skin hosts kiss the closed host face, mixed Skin + Web keeps its web, lane-by-lane Parallel Walls with one seam zone that follows Move Start. design_proto 1411, layer_assembly 124, toolpath_proto 90 green.

2026-10-07 — WALL SYSTEM pass 8 (uncommitted): Move Start mode; face-based end sections (consistent canonical ends); Parallel loops joined by interior-lane kisses (exterior lanes continuous); incoming generated walls run into the host's printed material; split-source shadowing bug fixed. design_proto 1400, layer_assembly 124, toolpath_proto 90 green.

2026-10-07 — WALL SYSTEM pass 7 (uncommitted): V1 / V2 and the right-sidebar Infill UI removed; Parallel Walls ends on the canonical motif with rung joins (no crossings; rings one route); square free-end stations for round caps; concealed default route seam + travel-minimising component order (`toolpath_proto/graph.py`). design_proto 1376, layer_assembly 124, toolpath_proto 90 green.

2026-10-07 — WALL SYSTEM pass 6 (uncommitted): seven constructions (Single / Out-and-Back, Hollow, Skin + Web, Parallel Walls, Interleaved, Linked, Chain); compact member editor; mixed-system junctions extend incoming lanes straight to the host's ACTUAL printed material (Junction R blends at the real contact).

2026-10-07 — WALL SYSTEM OWNERSHIP pass (uncommitted): explicit Wall Systems own thickness / alignment / construction / parameters / the Skin + Web web (owned infill records); Wall System sidebar; Wall Network = connectivity only; backend migration of legacy walls (geometry-preserving). design_proto 1321, layer_assembly 124 green.

2026-10-07 — WALL SYSTEMS pass 4 (uncommitted): canonical flat / round end motifs with nested returns and a closing lane permutation; layer-level Wall System membership independent of Wall Network (inherit / remove / own system); mixed regions split by wall with spliced skins; Wall Systems sidebar section. design_proto 1305, layer_assembly 124 green.

2026-10-07 — WALL SYSTEMS V1 (Skin + Web / Interleaved Waves / Linked Waves / Chained Loop; `wall_systems.py`), uncommitted, awaiting live review.

2026-10-07 — CANONICAL CORNER MOTIFS for Zigzag / Wave (`wall_lattice` §9), uncommitted, awaiting live review. Adaptive Truss untouched.

2026-10-07 — ADAPTIVE TRUSS wall infill V1 in production (`wall_lattice` §8, `infill.TRUSS_PARAMETERS`, Wall Infill UI + Regenerate), uncommitted, awaiting live review.

2026-10-07 — WALL-WEB RESEARCH EXPERIMENT (`design_proto/experiments/wall_web/`, uncommitted): production vs adaptive truss phase field vs fixed-angle propagation; findings and an unadopted proposal in the section of that name. Production unchanged. The Z-phase batch was committed and pushed as 8d9d439.

2026-10-06 — CHECKPOINT AUDIT of the uncommitted Z-phase batch: architecture documented ("Z PHASE — CURRENT ARCHITECTURE" replaces seven same-day sections; history compressed), dead / superseded code removed (legacy Assembly `min_overlap` input, attribution strand-prefix fallback, per-vertex placement helper, unused helpers), stale docstrings corrected, design relations cached, test tiers defined. Before it, the same day: Z phase (Layer Designs, layer_assembly subsystem), shared lineage lattice, semantic transform groups, two stabilization passes (identity, closure on the reference network, single-bead semantics, derived junctions vs connectors, cross-Z tracking). Uncommitted, awaiting manual browser approval.

DESIGNER CHECKPOINT (commit "Designer checkpoint: trim, physical beads, closed routing and canvas tools", 2026-10-06, pushed). Design / Print / Material sidebars, persistent Wall Network, Trim (+ ghost fix), rounded-junction assemblies, Material / Bead with Contact Overlap and Return-Lane Overlap, physical no-retrace closed routes, attached-branch return geometry, Route Origin, blue bead / vector rendering, zoom / pan / Fit / 100 %. All reviewed manually batch by batch. Tests: design_proto 1059 (incl. 8 JS UI smoke tests), toolpath_proto 90, pi-interface 78 — all green.

Earlier: Pass 8 (wall + solid infill geometry) + its correction (solid routing / web, wall-authoring UI) + wall regions / doorways checkpointed in 0898d16, awaiting browser inspection. Checkpoint commit 414c283 (pushed): wall networks, region infill, per-source Corner R, junctions, parametric / network walls, interactive shape tools, physical-quality routing + wall-authoring UI, the design-model pass (offset-source fix, undo / redo, transforms + clipboard, region / void semantics, parametric insets, WALL vs SOLID infill), the motif-based route-aware wall lattice (wall_lattice.py), and pass 7 (structural rules: corners, max unsupported distance, combined-density out-and-back, cap V, solid boundary contact + serpentine, wall relationships, router travel pairing for solids) — awaiting manual browser verification. Followed by a behaviour-preserving cleanup 51387d3 (dead code, stale comments / docs; pushed).
