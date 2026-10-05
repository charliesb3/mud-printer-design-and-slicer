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

### Phase 3 — Design Canvas Prototype — COMPLETE (all 13 steps + two UX passes)

Location: `design-toolpath/design_proto/`. 86 tests passing.

Files:
- `model.py` — full data model: Vec2, Path subtypes, OffsetTreatment, ZigzagGenerator, WaveGenerator, LatticeInstance, PrintLayer, TraversalConstraints
- `app.py` — Flask app; API: GET /api/generators, POST /api/route, POST /api/effective_paths
- `static/index.html` — design canvas UI
- `static/app.js` — canvas drawing, primitives, offset panel, lattice panel, toolpath overlay, routing overrides
- `tests/test_model.py` — 58 unit tests covering model layer + offset geometry
- `tests/test_app.py` — 30 integration + workflow tests

**UX pass 2 completed (14-point spec):**
- **True geometric offset algorithm**: `_offset_polyline` replaced with proper segment-parallel-intersection method. Each segment is shifted parallel by `dist`, adjacent offset segments are intersected (miter join), bevel fallback when miter exceeds 4×dist. Rectangle 10in inset → exact corners. Circle r=60 with 10in offset → radius within 0.2 in of 50 or 70.
- **Add Lattice fixed**: `addLattice()` now uses `allBoundaries()` (source paths + offset treatments) instead of requiring 2 source paths. Boundary selectors in lattice panel now show both source paths and "Offset of X" entries. `PrintLayer.effective_paths()` allows lattice to reference offset-derived paths by ID. `OffsetTreatment.generate()` now assigns `id=self.id` to derived paths (stable, predictable).
- **Role removed from UI**: Role field removed from source path Properties panel and from Wall Offsets panel. Kept internally; not exposed to designer.
- **Individual delete**: "× Delete path" button in path Properties panel. Delete/Backspace key continues to work. Cascade: deleting source path removes its offsets and lattice instances. Removing an offset treatment also removes lattice instances that reference it.
- **Toolpath arrows**: Redesigned for legibility — white fill with dark outline, size 7 (up from 5), drawn above all geometry in repaint order.
- **Numbers removed**: Run-sequence numbers removed from toolpath visualization and toolbar.
- **Arrows grayed when Toolpath OFF**: Arrows button disabled while toolpath is off.
- **Metric labels**: "% printing" → "Continuous"; "Retrace dist" → "Reprinted" (with tooltip: "Distance printed more than once to maintain a continuous route").
- **Clear → Clear All** in toolbar.
- 11 geometry regression tests added (offset distance accuracy, lattice-with-derived-boundary, circle radius verification).

**Known limitations / deferred:**
- Node-drag editing for Circle/Ellipse primitives is approximate (resamples rather than adjusting radius parametrically from drag)
- Path sections (split points / per-section properties) are in the model but have no UI yet
- Multi-layer / physical-Z keyframes deferred (as planned)
- Offset direction convention assumes CCW winding for "inside" = inward; CW-wound paths will have inside/outside reversed (user can flip direction selector)

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

Phase 3 major UX pass complete. Phase 2: 65 tests. Phase 3: 75 tests. All green.
