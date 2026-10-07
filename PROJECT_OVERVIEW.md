# Mud Printer Software — Project Overview

## Overall Goal

Develop a custom software environment for an existing large-scale, house-sized mud 3D printer.

The physical printer is already built and operational. It has successfully moved and printed using an Arduino Mega running GRBL for motion control and Universal G-code Sender (UGS) on a computer as the operator interface and G-code sender.

The current software/hardware system works.

The purpose of this project is therefore not to create a printer-control system from scratch, but to improve and eventually replace parts of the existing software workflow with a system that is cleaner, more intuitive, more capable, and specifically designed around this printer.

The longer-term goal is a coherent software environment spanning:

1. designingrintable forms and generating appropriate toolpaths,
2. operating the printer through a dedicated Raspberry Pi-based interface,
3. communicating with the existing Arduino/GRBL real-time motion-control system.

The project should evolve incrementally, preserving working parts of the existing machine until there is a demonstrated reason to change them.

---

## Physical Machine Context

This software is for a large-scale Cartesian mud 3D printer intended for building-scale work.

The machine is approximately 10 ft × 10 ft or larger in scale.

It prints thick adobe/mud material.

Material is supplied by a large external concrete/adobe pumping system that pushes material through a hose to the print head.

The extrusion/pumping system is independently controlled.

IMPORTANT:

This software project does NOT need to control the extrusion pump.

Do not assume that extrusion start/stop, extrusion rate, pump pressure, or other extrusion functions are responsibilities of this software unless the project requirements re explicitly changed in the future.

The scale of the machine, behavior of the material, hose, pumping system, and other physical characteristics may eventually impose important constraints on software and toolpath design.

Those constraints should be incorporated as they are experimentally established.

Do not invent machine constraints based solely on assumptions from conventional FDM printing.

---

## Current Motion Architecture

The machine is Cartesian.

### X and Y

X and Y motion are currently controlled by the Arduino Mega running GRBL.

This arrangement has already been used successfully to move and print with the machine.

### Z

The Z axis is physically different from X and Y.

Z motion is provided by a linear actuator rather than the same stepper-motor arrangement used for X/Y.

Historically, Z has been controlled manually rather than through GRBL.

Future software should ideally provide automatic Z control while preserving the ability to control Z manually.

IMPORTANT:

Automatic Z control is a desired future capability.

The method of implementing automatic Z control has NOT yet been decided.

Do not assume that Z must be integrated into GRBL unless that approach is deliberately evaluated and chosen.

The eventual solution may involve GRBL, separate hardware control, the Raspberry Pi, or another architecture.

Treat this as an open architectural question.

---

## Project Philosophy

The machine should not automatically be forced into the conventional desktop 3D-printing workflow:

CAD
→ 3D solid or mesh
→ STL
→ conventional slicer
→ G-code
→ printer

Large-scale adobe/mud printing has different material behavior, physical constraints, geometry, scale, and creative possibilities from ordinary desktop FDM printing.

The software should therefore be designed around the actual machine and process rather than assuming that conventions developed for plastic FDM printers are necessarily appropriate.

Where useful, the relationship between the geometry being designed and the physical path f the printer may be much more direct.

The project should remain open to discovering the best representation and workflow through experimentation.

Do not prematurely convert promising ideas into permanent architectural requirements.

---

## High-Level Software Architecture

The current project is organized into three major subprojects:

1. Design + Toolpath
2. Raspberry Pi / Printer Interface
3. Arduino / GRBL Motion Control

The approximate current/future information flow is:

Design + Toolpath
  ├─ Designer (design_proto): reusable 2D LAYER DESIGNS
  │         ↓ narrow interface (LayerSource: design info + resolved 2D printable geometry)
  └─ Layer Assembly (layer_assembly): stacks Layer Designs by physical height → layers + Z
        ↓
machine instructions / G-code
        ↓
Raspberry Pi / Printer Interface
        ↓
USB / serial communication
        ↓
Arduino Mega / GRBL
        ↓
X/Y machine motion

Z control:
currently manual
future automation method TBD

Extrusion:
independently controlled external system
not controlled by this software

These subproject boundaries are useful organizational and architectural boundaries.

They do not require the components to become separate repositories or completely independent applicat boundaries may evolve if the actual architecture develops differently.

---

## Subproject 1 — Design + Toolpath

This part of the system concerns creating printable forms and translating those forms into paths or instructions that the machine can execute.

A major concept currently being explored is representing a print as a stack of 2D vectors or paths rather than beginning with a conventional 3D mesh.

Potential capabilities include:

- importing 2D vector geometry
- creating 2D vector geometry
- stacking paths into layers
- changing scale between layers
- changing position or offset between layers
- rotating geometry between layers
- defining key layers or keyframes
- interpolating transformations between keyframes
- previewing the resulting 3D form
- understanding machine-specific printability constraints
- incorporating adobe/mud-specific printing constraints as they are learned
- generating the paths the machine should follow
- converting those paths into G-code or other machine instructions

The tended application is primarily large-scale architectural/building printing, including walls and structures.

However, do not currently impose an architectural-only restriction on the design system.

The same design/toolpath concepts may also be useful for other large-scale mud forms.

### Important status of the stacked-vector concept

The stacked-2D-vector approach is currently a promising design direction.

It is NOT yet a permanent architectural requirement.

Design and toolpath are intentionally being treated as one subproject for now because the design representation may correspond closely to the physical path followed by the printer.

If experience later shows that design and toolpath generation should become separate systems, this subproject may be split.

---

## Subproject 2 — Raspberry Pi / Printer Interface

The Raspberry Pi is intended to become the higher-level computer associated with the printer.

It should eventually provide a printer-specific alternative to the current Universal G-code Sder workflow.

Rather than using a general-purpose G-code sender designed for many different CNC machines, the goal is to create an interface specifically suited to operating this mud printer.

Potential responsibilities include:

- communication with the Arduino/GRBL controller
- sending G-code or other machine instructions
- jogging X/Y
- future interaction with whatever Z-control system is developed
- homing where appropriate
- starting jobs
- pausing jobs
- resuming jobs
- stopping jobs
- displaying machine position
- displaying GRBL state
- displaying alarms and errors
- loading and managing print/job files
- printer-specific controls
- browser-based user interface
- local/offline operation
- application startup behavior
- automatically making the printer interface available when the printer powers on
- local network operation
- creating or participating in a local Wi-Fi network when appropriate

A laptop, tablet, or other device should ultimately be able to connect to the Raspberry Pi and operate the printer through a browser or similarly lightweight interface.

The printer may be operated in locations without internet access or existing network infrastructure.

Core printer operation must therefore not depend on cloud services or an internet connection.

The Raspberry Pi should own or stream the active print job so that disconnecting the laptop/tablet, closing the browser, or temporarily losing the client connection does not inherently terminate an active print.

The Raspberry Pi is responsible for higher-level operation.

It should not be assumed to perform timing-critical real-time motion control that properly belongs on a dedicated motion controller.

---

## Subproject 3 — Arduino / GRBL Motion Control

The Arduino Mega currently runs GRBL and controls X/Y machine motion.

This is an existing working system.

Its current responsibilities include:

- receiving machine instructions over serial
- real-time coordinated X/Y motion
- generating step/direction signals
- GRBL machine state
- relevant macne I/O
- alarms and other GRBL behavior

The current preference is to retain GRBL rather than immediately replacing or heavily modifying it.

Standard machine-specific G-code should remain the boundary between higher-level software and GRBL where practical.

GRBL should only be customized or replaced when a concrete machine requirement demonstrates that the existing firmware is insufficient.

This preserves a useful separation:

Higher-level software determines WHAT path or action should be executed.

The motion controller determines HOW to execute commanded motion reliably in real time.

### Z and GRBL

Z is not currently controlled through GRBL.

Do not document or treat three-axis GRBL control as an existing feature.

Whether Z should eventually be incorporated into GRBL is an unresolved architectural question.

---

## Important Existing Decisions

### Preserve the existing working system while developing replacements

The printer already works using GRBL + UGS.

Development should proceed incrementally.

Do not unnecessarily replace working components simply because a more sophisticated architecture is possible.

---

### Raspberry Pi and Arduino have different responsibilities

The Raspberry Pi is intended to handle:

- higher-level printer operation
- user interaction
- job management
- communication
- non-real-time coordination

The Arduino/GRBL system handles timing-critical real-time motion control for the axes under its control.

Do not casually move real-time motion-control responsibilities onto the Raspberry Pi.

---

### Preserve G-code as an interface where practical

The current preferred architecture uses machine-specific G-code as the boundary between higher-level software and GRBL where practical.

Do not modify GRBL merely to avoid generating appropriate G-code in the higher-level software.

---

### Do not replace GRBL without demonstrated need

GRBL already operates the machine successfully.

Initially prefer building around it.

Consider modification or replacement only when an actual requirement cannot reasonably be met with the existing firmware.

---

### Automatic Z control is a goal, not a solved architecture

Future software should automate Z motion while retaining manual control.

How that automation will work has not yet been decided.

Do not silently convert this goal into an assumption that Z will be controlled through GRBL.

---

### Extrusion is outside the software-control scope

The large external mud/concrete/adobe pumping system is independently controlled.

The Mud Printer Software does not need to command extrusion.

Do not introduce extrusion-control requirements unless the user explicitly changes this project requirement.

---

### Offline operation matters

The machine may be used in locations without internet access or existing network infrastructure.

Core printer operation must work locally and offline.

---

### The client device should not own the active print

The intended architecture places the active printer-control process on the Raspberry Pi.

A laptop, tablet, or similar device acts primarily as the user interface.

Closing the browser, losing Wi-Fi temporarily, or disconnecting the client should not inherently terminate an active print.

---

## Current Project State

The physical printer already exists and has successfully printed.

The existing software/control workflow is approximately:

computer running UGS
        ↓
USB / serial
        ↓
Arduino Mega running GRBL
        ↓
X/Y motion

Z:
manual control of linear actuator

Extrusion:
independently controlled external pumping system

### Pi Interface — Milestone 1 Software Complete (Mac)

The Raspberry Pi Printer Interface Milestone 1 has been fully implemented
in software and is passing all tests on Mac.

The software is ready for Pi deployment and physical machine testing.

See `pi-interface/PROJECT_MEMORY.md` for full details.

### Design + Toolpath — Phase 2 (Toolpath) Prototype Complete

Graph-based toolpath prototype (`design-toolpath/toolpath_proto/`, 90 tests).
Printable geometry is a multigraph; a continuous print is an Eulerian
traversal. Objective: no false connections → print everything → fewest
runs / travels → least travel → least retrace (retracing visible faces costs
more than internal geometry). Connected non-Eulerian geometry is printed in
one run by retracing printed edges; travel otherwise only joins
disconnected sections — except that leftover odd ends of solid infill are
joined by a short travel rather than printing a perimeter twice. Touching
(T) and crossing (X) geometry share graph nodes. Key result: a wall
perimeter + internal web prints as one continuous path with zero travel.

### Design + Toolpath — Phase 3 (Design Canvas) Prototype

Interactive path-first design canvas (`design-toolpath/design_proto/`,
1059 tests incl. 8 JS UI smoke tests). Checkpoints: commit 414c283 (+ cleanup
51387d3); Pass 8 + correction + wall regions in commit 0898d16; the
2026-10-06 "Designer checkpoint" commit (trim, physical beads, closed
routing, canvas tools), reviewed manually batch by batch.

Major capabilities:
- Path-first, non-destructive design: parametric primitives, curves, drawn
  paths, interactive shape tools, snapping, undo / redo, copy / duplicate /
  rotate, parametric inset / outset.
- Walls: a source path is reference geometry; Wall Thickness + Alignment
  make it a wall. Touching / crossing walls form wall networks with
  junctions (miter / rounded); openings cut clear gaps through the whole wall
  (also when its two faces are two linked paths); nested closed
  boundaries can be linked as a parametric wall relationship.
- Three layers are kept distinct: DESIGN GEOMETRY → WALL / REGION SEMANTICS
  (thickness, regions + voids by geometric nesting, wall vs solid, openings)
  → TOOLPATH.
- Wall infill is a STITCHING pattern between a wall's faces, designed
  route-aware (motifs chosen for the whole network), so it is continuous by
  construction: no interior retrace, coherent branches, braced corners and
  caps, a maximum unsupported distance. Spacing is a target. Regions too
  wide to be a wall use a field generator with local repair (provisional
  wall / area threshold).
- Solid infill: perimeters and infill are distinct structural roles,
  printed as coherent phases (each perimeter one complete loop). Fills:
  conventional rectilinear, or a serpentine WEB of smooth anti-phase waves
  touching at alternating apexes. Zero travel is a preference, not the
  objective: a short travel beats distorted print geometry.
- Guiding rule: pattern parameters are preferences; structural support and
  topology are constraints; physically sensible paths beat continuity metrics.
- Walls are normally authored with Wall Thickness + Alignment (parametric);
  linking two drawn boundaries and inset / outset are advanced operations.
- Wall infill and openings share one wall-MATERIAL region (a boundary minus
  its voids): an opening is a subtraction through the complete wall, never
  new wall topology.
- Toolpath overlay with playback; routing diagnostics.
- LAYER DESIGNS + LAYER ASSEMBLY (Z phase, started 2026-10-06; architecture
  + prototype, uncommitted): the Designer defines one reusable 2D Layer
  Design (a Designer document); designs derive from a Base by an id-keyed
  overlay resolved live (Base edits propagate; no copies; no door / window
  objects — a doorway is a design with a gap). A separate, deliberately
  "dumb" `design-toolpath/layer_assembly` package stacks designs by
  PHYSICAL HEIGHT with one global layer height (whole layers, boundaries
  rounded, errors reported) into layer instances that only reference their
  design; its 3D preview consumes instance Z + resolved 2D geometry. A
  lineage prints ONE vertically registered wall-lattice scaffold, and its
  lattice definition (pattern, spacing, variation) belongs to the lineage.
  Runs crossed by a variant's end wall carry two passes that cross on the
  jamb line, so a connected opening variant still prints as one closed
  route. Assembly transform groups move the ARCHITECTURE (source paths,
  via the LayerSource `geometry(design, transforms)`), then the Designer
  resolves walls / junctions / lattice — never rubber-sheeted beads. The
  application has two workspaces, [Designer] [Assembly] (stack
  editor + 3D stack preview). Own memory:
  `design-toolpath/layer_assembly/PROJECT_MEMORY.md`; the Designer side and
  the test tiers: `design-toolpath/PROJECT_MEMORY.md` → "Z PHASE — CURRENT
  ARCHITECTURE".
- Workspace: DESIGN sidebar (paths, properties, wall geometry, persistent
  Wall Network) | canvas (zoom at the pointer, Space / middle-drag pan,
  Fit / 100 %) | MATERIAL / BEAD + PRINT / TOOLPATH sidebar.
- Non-destructive TRIM of source sections between intersections (stored as
  a topological signature, follows parametric edits, unresolved → nothing
  suppressed); rounded thick-wall junctions are one concentric assembly.
- PHYSICAL BEADS (system-level concept): Wall Thickness (architecture) ≠
  centreline (toolpath) ≠ Bead Width (one deposited pass). With physical
  rules on: Contact Overlap (lattice landings stop W − O off a face),
  Return-Lane Overlap (single-bead open walls print out and back W − R
  apart), NO exact retrace, and every connected component one closed route
  (start = end, no internal travel); the router reports, never hides, what
  cannot close. Route Origin: the designer chooses where each closed route
  begins / returns (groundwork for multi-layer seams). Beads view: one solid
  blue bead per printable centreline. Open: solid infill still uses
  in-component travel (conflicts with the closed-route rule — pending).

See `design-toolpath/PROJECT_MEMORY.md` for decisions, history and details.

---

## Major Open Questions

Important unresolved questions currently include:

### Design + Toolpath

- What should the Design + Toolpath environment ultimately look like?
- Is stacked 2D vector geometry the correct fundamental representation?
- How should transformations and keyframes be represented?
- What mud/adobe-specific printability rules should the software understand?
- What constraints arise from the physical scale of the machine?
- What constraints arise from material behavior and the hose-fed printing process?
- What should the boundary between design and toolpath generation ultimately be?

### Raspberry Pi / Printer Interface

- What is the correct jog speed for this machine's scale? (Must be validated physically)
- Does the Pi need to act as a Wi-Fi access point for offline field use?
- Should the interface support job recovery after power failure?
- How should local/offline networking be implemented?

### Arduino / GRBL

- Will stock/current GRBL satisfy all required future machine behavior?
- Are any GRBL customizations actually necessary?
- How should automatic Z control be implemented?
- Should Z eventually be controlled through GRBL or through a separate mechanism?
- How should manual Z control coexist with automatic Z control?

These are open questions.

Do not treat them as decisions that must be resolved immediately.

---

## Development Principles

Prefer incremental development over replacing the entire working system at once.

Preserve working components until there is a concrete reason to replace them.

Maintain clear interfaces between major components.

Favor inspectable and understandable systems over unnecessary complexity.

Design software around the actual physical printer rather than generic assumptions about 3D printers.

Distinguish experimentally established machine constraints from assumptions.

Do not introduce complexity, automation, or agentic architecture merely because it is available.

Use it when it solves a concrete problem.

Keep core printer operation local and offline-capable.

Treat the physical printer and observed machine behavior as authoritative when software assumptions conflict with reality.

Document consequential decisions and why they were made.

Allow the architecture to evolve as the machine and software become better understood.

---

## Last Updated

2026-10-06

Pi Interface Milestone 1 software complete on Mac. Physical testing pending.
Design + Toolpath: Phase 2 complete (90 tests). Phase 3 design canvas: checkpoints 414c283 / 51387d3 / 0898d16, then the 2026-10-06 Designer checkpoint (Design / Print sidebars, Trim, rounded-junction assemblies, Material / Bead, Contact + Return-Lane Overlap, no-retrace closed routes, attached-branch return geometry, Route Origin, blue bead / vector rendering, zoom / pan, persistent Wall Network, trim ghost fix): design_proto 1059 + toolpath_proto 90 + pi-interface 78 tests green.
Z phase (2026-10-06, UNCOMMITTED, audited and awaiting manual browser approval before a checkpoint commit): Layer Designs with live inheritance, a shared lineage lattice and one project bead width (Designer); the Layer Assembly subsystem and workspace (stack by physical height, section / assembly-wide transforms, semantic transform groups, vertical support with HEADER NEEDED / INSUFFICIENT LAYER SUPPORT, headers, undo / redo, 3D preview). Stable boundary: the Designer owns source / wall semantics, junctions, openings, lattice, printable geometry, routes and the material; the Assembly owns layer order, Z, transforms, group choices, vertical support and assembly objects; the Assembly asks the Designer for each grouped layer's design with its SOURCES moved and receives resolved, source-tagged geometry (never rubber-sheets beads). Principles: a closed print route outranks vertical lattice registration; lattice identity persists through Z where topology is unchanged; routine edits must not re-solve unchanged layers; mud requires physical support below — checked in the Assembly, never weakened to hide findings. Main open issue: lattice coherence at independently transformed junctions (legitimate support findings on the reference network). Tests (2026-10-06 audit): design_proto 1143, layer_assembly 124, toolpath_proto 90, pi-interface 78 — all green.
Repository permanently relocated to `/Users/charliebritton/Projects/mudprintersoftware` (previously `/Users/charliebritton/Documents/mude printer/mudprintersoftware`). Post-move environment restored 2026-10-05: the three `.venv`s were recreated with the same package versions, and all suites pass from the new path (Pi 78, toolpath_proto 90, design_proto 889 incl. 3 JS UI smoke tests).
Arduino/GRBL subproject not yet started.
