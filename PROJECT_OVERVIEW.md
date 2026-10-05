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

The graph-based toolpath prototype is complete. 65 tests passing.

Location: `design-toolpath/toolpath_proto/`

Validates Eulerian/Chinese Postman routing for five test geometries.
Key result: Case D (wall perimeter + internal web) = single continuous path,
zero travel moves.

### Design + Toolpath — Phase 3 (Design Canvas) Prototype Complete + Four UX Passes

The interactive design canvas prototype is complete. 235 tests passing.

Location: `design-toolpath/design_proto/`

Implements the full path-first design model:
- Parametric primitives (Line, Circle, Ellipse, Rectangle) + drawn ExplicitPaths
- Non-destructive OffsetTreatment — true geometric parallel offset (segment-intersection algorithm)
- ZigzagGenerator + WaveGenerator with variations
- PrintLayer assembles effective geometry → feeds routing engine; lattice can use offset-derived paths as boundaries
- Auto-routing when Toolpath ON (no manual Route button)
- Sparse direction arrows (white, high-contrast), seam markers on closed loops
- Offset: Inside/Outside/Left/Right direction + positive distance
- Individual path delete with cascade (removes dependent offsets/lattices)
- Routing overrides stored as TraversalConstraints (not geometry mutations)
- Wave seam bridge for zero-travel closed-wall routing
- Open wall end caps for Eulerian open-wall routing
- Toolpath playback transport (scrub, play/pause, speed, reverse)

See `design-toolpath/PROJECT_MEMORY.md` for full details.

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

2026-10-04

Pi Interface Milestone 1 software complete on Mac. Physical testing pending.
Design + Toolpath Phase 2 (65 tests) and Phase 3 (126 tests, four UX passes) both complete.
Arduino/GRBL subproject not yet started.
