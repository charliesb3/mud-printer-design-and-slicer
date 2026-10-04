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

## Current State

### Phase 2 — Toolpath / Graph Prototype — COMPLETE

Location: `design-toolpath/toolpath_proto/`

Built and tested. 57 tests passing.

Key result: the graph-based routing approach works correctly for all five test geometries.

**Most important finding:** Geometry D (closed wall perimeter + internal zigzag web) produces exactly 2 odd-degree nodes in the print graph, which means a single Eulerian path traverses the entire geometry — wall perimeter AND web — with zero travel moves and zero retracing. This confirms the core hypothesis that designed internal geometry can enable fully continuous printing.

**Summary of test case results:**

| Case | Description | Components | Odd nodes | Travel moves | % Printing |
|------|-------------|-----------|-----------|--------------|------------|
| A | Single closed wall | 1 | 0 | 0 | 100% |
| B | Two independent walls | 2 | 0 each | 1 | ~99% |
| C | Outer + inner, no lattice | 2 | 0 each | 1 | ~99% |
| D | Wall + internal web | 1 | 2 | 0 | 100% |
| E | Awkward / T-junction | 3 | mixed | 2+ | ~85–95% |

### Phase 1 — Form / Keyframe Prototype — NOT YET BUILT

---

## Algorithms and References

Most relevant existing work:
- **Eulerian path / Chinese Postman (Route Inspection)**: primary toolpath routing algorithm
- **Arc-length parameterization**: for shape morphing between compatible paths
- **Medial axis / Voronoi skeleton**: for positioning lattice geometry between walls
- **Offset curves**: for generating inner wall from outer wall at specified thickness

---

## Open Questions

- Does the graph routing model produce good results in practice for the wall+lattice geometry? (Phase 2 will answer this)
- Does the keyframe model produce the forms the designer imagines? (Phase 1 will answer this)
- How should topological transitions (cell count changes, path splits/merges) be represented and edited?
- How should the lattice generator accept continuity hints from the toolpath planner without violating the geometry/toolpath separation?
- How should the seam position be managed and optimized across layers?
- What is the right output format for the Pi Interface to consume?

---

## Last Updated

2026-10-03

Design direction consolidated. Phase 2 (Toolpath / Graph) prototype in active development.
