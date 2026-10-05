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

Built and tested. 65 tests passing (57 routing/geometry + 8 app integration).

Key result: graph-based Eulerian routing works correctly for all five test geometries.

**Most important finding:** Geometry D (wall perimeter + internal zigzag web) produces exactly 2 odd-degree nodes → single Eulerian path, zero travel moves, 100% continuous printing. This validates the core hypothesis that designed internal geometry enables fully continuous printing.

| Case | Description | Components | Odd nodes | Travel moves | % Printing |
|------|-------------|-----------|-----------|--------------|------------|
| A | Single closed wall | 1 | 0 | 0 | 100% |
| B | Two independent walls | 2 | 0 each | 1 | ~99% |
| C | Outer + inner, no lattice | 2 | 0 each | 1 | ~99% |
| D | Wall + internal web | 1 | 2 | 0 | 100% |
| E | Awkward / T-junction | 3 | mixed | 2+ | ~85–95% |

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

Location: `design-toolpath/toolpath_proto/`. 65 tests passing.

### Phase 3 — Design Canvas Prototype — COMPLETE (all 13 steps + five UX passes)

Location: `design-toolpath/design_proto/`. 147 tests passing.

Files:
- `model.py` — full data model: Vec2, Path subtypes (incl. QuadBezierPath), OffsetTreatment, ZigzagGenerator, WaveGenerator, LatticeInstance, PrintLayer, TraversalConstraints
- `app.py` — Flask app; API: GET /api/generators, POST /api/route, POST /api/effective_paths
- `static/index.html` — design canvas UI
- `static/app.js` — canvas drawing, primitives, offset panel, lattice panel, toolpath overlay, routing overrides, dimensions overlay, playback transport
- `tests/test_model.py` — 97 unit tests covering model layer + geometry validation
- `tests/test_app.py` — 50 integration + workflow tests

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
- Does the keyframe model produce the forms the designer imagines? (Phase 1 will answer)

---

## Last Updated

2026-10-04

Phase 3 UX pass 5 (editable curved segments — QuadBezierPath) complete. Phase 2: 65 tests. Phase 3: 147 tests. All green.
