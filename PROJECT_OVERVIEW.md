# Mud Printer Software — Project Overview

## Overall Goal

Develop a custom software environment for an existing large-scale, house-sized mud 3D printer.

The physical printer is already built and operational. It has successfully moved and printed using an Arduino running GRBL (an Uno per the 2026-10-14 control-box baseline; earlier documents said Mega — VERIFY) for motion control and Universal G-code Sender (UGS) on a computer as the operator interface and G-code sender.

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

UPDATE 2026-10-14: extrusion remains electrically deferred / undefined and the pump is still independently controlled today, but FUTURE control of the pump is now anticipated; remote and software concepts reserve extrusion pause / resume without any implementation (see `CONTROL_BOX_HARDWARE.md` → Extrusion).

The scale of the machine, behavior of the material, hose, pumping system, and other physical characteristics may eventually impose important constraints on software and toolpath design.

Those constraints should be incorporated as they are experimentally established.

Do not invent machine constraints based solely on assumptions from conventional FDM printing.

---

## Current Motion Architecture

The machine is Cartesian.

The control-box hardware (controllers, drivers, power, E-stop, Z driver,
wireless remote, VERIFY items, superseded parts) is documented in
`CONTROL_BOX_HARDWARE.md` — consult it before any work touching real
hardware interfaces.

### X and Y

X and Y motion are currently controlled by the Arduino (Uno — see `CONTROL_BOX_HARDWARE.md`; earlier documents said Mega) running GRBL, driving three DM556T drivers (X, Y1, Y2).

This arrangement has already been used successfully to move and print with the machine.

### Z

The Z axis is physically different from X and Y: a 12 V reversible DC linear
actuator (ECO-WORTHY, 18 in stroke), not a stepper axis.

CURRENT ARCHITECTURE (decided 2026-10-14; details and implementation
consequences: `CONTROL_BOX_HARDWARE.md` → Z axis):
- The Raspberry Pi is the SOLE Z command source: Pi 3.3 V GPIO PWM + DIR →
  Cytron MD13S (planned) → actuator. Z is NOT a GRBL axis.
- Manual Z = Pi UI and the RF remote's Z UP / Z DOWN, both as inputs to Pi
  software (the remote never drives Z directly).
- Open-loop: no Z limits, homing or position feedback yet (actuator internal
  end-of-travel only); software must not assume a known Z position and Z
  motion must be dead-man / time-bounded with a guaranteed default STOP.
- CURRENT MACHINE (decided 2026-10-14): print jobs are XY-only and Z stays
  MANUAL during printing — the Z hardware path above is for future automatic
  Z / operator Z control, not used by jobs yet. Layer / Z information travels
  with each job as metadata only.
- Still OPEN: automatic Z within prints, and whether / how Z position ever
  becomes known.

Today, before the new control box is built, Z is still operated manually.

HISTORY: until 2026-10-14 the Z control method was an open architectural
question (GRBL, separate hardware, the Pi or another architecture were all
possible; "do not assume Z must be integrated into GRBL"). The hardware path
is now chosen as above; the earlier SRDKG RF Z controller and a separate Pi
relay board / D2-ZFZ are superseded (`CONTROL_BOX_HARDWARE.md` →
"Superseded").

### Lifts and frame repositioning (stated 2026-10-09)

The machine has approximately 18 inches of usable Z travel. A building is printed in vertical LIFTS: after a lift is complete, the ENTIRE XY frame is physically raised before the next lift is printed. Frame repositioning is a major, manual operation (not assumed motorized) and must be minimized; printing never returns to a lift below the frame.

The print plan (Design + Toolpath) divides the stack into lifts of whole layers, completes each lift (independent islands built upward one after another, merged geometry after its parts) and represents each frame repositioning as an explicit event between lifts. Physical frame-lifting control is not yet designed.

Decided 2026-10-14 (Design + Toolpath ↔ Pi interface): a whole building is sent to the Pi as ONE print job made of an ordered sequence of first-class LIFTS taken from the PrintPlan; the Pi never recalculates lift boundaries. After each lift, progression stops until the operator repositions the frame and explicitly confirms. GRBL still receives ordinary G-code underneath. Implemented 2026-10-14: the Pi runs `.mudjob` jobs lift by lift (P1/P2); the Designer exports the PrintPlan as a `.mudjob` (P3), EXECUTABLE since the current-machine pass (XY-only G-code in inches, see "Current machine execution"). During the frame reposition the operator may jog and re-establish the XY origin before confirming; all lifts share one work coordinate system.

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
  │         (WALL GROUPS since 2026-10-11: members, thickness, alignment — the architecture;
  │          the Designer shows wall envelopes, no construction / lattice / route)
  ├─ Layer Assembly (layer_assembly): stacks Layer Designs by physical height → layers + Z
  │         (edited on ARCHITECTURAL ENVELOPES: walls / junctions / openings / cuts, no Wall
  │          System lattice or routing; preliminary envelope-level support)
  └─ WALL SYSTEMS workspace (2026-10-11): the project's printable constructions (Single /
            Hollow / Skin + Web / Parallel / waves / Chained Loop) assigned to walls or Wall
            Groups; "Generate Wall Systems" (explicit) = the existing full pipeline for the whole
            building + bead-level support; status NOT GENERATED / GENERATING / CURRENT / OUTDATED
        ↓ (only CURRENT generated geometry)
  PRINT PLAN (layer_assembly/print_plan.py, 2026-10-09): every layer routed and
  ordered (runs, travels, layer transitions, source identities) + time estimates —
  the ONE representation simulation, G-code export and machine execution consume
  (Print Simulation workspace: playback of it, 2026-10-09)
        ↓ "Export Print Job" (layer_assembly/machine_job.py)
.mudjob FILE — the ONLY boundary between the two applications
  (contract: contracts/mudjob/README.md; copied by USB / email / network)
        ↓ upload (parsed + validated independently)
Raspberry Pi 4 / Printer Interface (supervisory: UI, jobs, Z, RF remote inputs)
        ↓ USB / serial                        ↓ GPIO PWM + DIR
Arduino Uno / GRBL                      Cytron MD13S (planned)
        ↓ DM556T ×3 (X, Y1, Y2)               ↓
X/Y machine motion                      Z: 12 V linear actuator (open-loop)

Handheld RF remote → Kaleinavi receiver (dry contacts) → Pi GPIO (planned)
Hardwired E-stop (120 VAC side): separate, independent safety layer
Hardware details: CONTROL_BOX_HARDWARE.md

Z control (2026-10-14): commanded by the Pi via the MD13S; manual Z through
the Pi UI / remote; automatic use within prints still undecided

Extrusion:
independently controlled external system
not controlled by this software

Project persistence (Design + Toolpath, 2026-10-09):
the whole editable project (every Layer Design + the Assembly) is saved / opened
as ONE versioned JSON project file (`.muddesign`, format "mud-printer-project",
schema_version) holding AUTHORING state only — resolved geometry, routes and
support results are regenerated. It is NOT machine output and is never uploaded
to the Pi: the machine job is the separate `.mudjob` export (2026-10-14).

These subproject boundaries are useful organizational and architectural boundaries.

EXCEPTION (decided 2026-10-14): Design + Toolpath and the Pi interface ARE
independent applications whose only integration is the `.mudjob` file — see
"Design + Toolpath and the Pi interface are independent applications" below.

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
- Z control: the Pi is the sole Z command source via the Cytron MD13S
  (`CONTROL_BOX_HARDWARE.md`; not yet implemented)
- reading the wireless remote (Kaleinavi dry contacts on Pi GPIO; planned)
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

The Arduino (Uno per the 2026-10-14 baseline; earlier documents said Mega — VERIFY) currently runs GRBL and controls X/Y machine motion.

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

Superseded 2026-10-14: Z is Pi-commanded through a Cytron MD13S, not a GRBL axis (see "Current Motion Architecture" → Z and `CONTROL_BOX_HARDWARE.md`). (Previously: whether Z should be incorporated into GRBL was unresolved.)

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

The Pi is the SUPERVISORY controller — not the real-time X/Y controller and
not the safety system: operator / UI / RF remote → Pi → Uno/GRBL (X/Y) and
MD13S (Z) → machine; the hardwired E-stop is a separate, independent safety
layer. Z through the Pi (2026-10-14) is a deliberate decision: a DC actuator
commanded open-loop, not timing-critical coordinated motion.

### The hardwired E-stop is the only safety stop (2026-10-14)

The physical E-stop on the 120 VAC side is independent of the Pi, software
and the RF remote. The wireless "REMOTE STOP" is a software / operational
stop, never safety-rated, and must never be presented as an emergency stop.
Details: `CONTROL_BOX_HARDWARE.md`.

---

### Preserve G-code as an interface where practical

The current preferred architecture uses machine-specific G-code as the boundary between higher-level software and GRBL where practical.

Do not modify GRBL merely to avoid generating appropriate G-code in the higher-level software.

---

### Project files store authoring state, versioned, separate from machine output

Design + Toolpath projects are saved as a JSON project file (`.muddesign`) with an explicit format identifier and schema version from the first release (schema 2 since 2026-10-11: Wall Groups in the designs + the project's Wall Systems; schema 3 the same day: the default Maximum Overhang became 1 in — older projects without a value keep 1.5; schema 4 on 2026-10-14: the default Opening Alignment became Vertical / Plumb — older projects get their effective alignment written explicitly; schema 5: the machine profile; schema 6: planar cut profile types; schema 7 on 2026-10-15: Wall Groups removed — each wall owns its thickness, Wall Systems are assigned per wall, Variant-specific physical properties are dropped with a report; plus a shadow `junctions` registry). Files store the editable inputs (Layer Designs, Assembly), not derived geometry or toolpaths. Older files are upgraded on load by a central chain of schema migrations (`design-toolpath/design_proto/project_file.py`); files from a newer schema are refused rather than partially loaded. G-code / machine instructions will be a separate export derived from a project, never the project format itself. Details: `design-toolpath/PROJECT_MEMORY.md` → "PROJECT FILE".

### Current machine execution — XY-only jobs, manual Z and pump (2026-10-14)

- Executable `.mudjob` jobs are XY-only: no Z commands (Z manual; layer / Z
  kept as metadata for the operator and future automatic Z), no pump /
  extrusion commands (the pump is manual and independent; travel is never
  paused or altered for it).
- Machine-job G-code states its units explicitly: inches (`G20`) and
  absolute (`G90`) at the start of every lift; it never relies on the
  controller's previous modal state.
- XY ORIGIN: the operator jogs to the intended print origin and establishes
  work XY zero there on the controller; the exported building toolpath is in
  those work coordinates (the Designer's world XY, no invented machine
  offset). All lifts use the same work coordinates; if a frame raise disturbs
  registration the operator re-zeroes before confirming the next lift.
- PRIME position is a CONTROLLER feature (machine operation, never part of
  the PrintPlan or `.mudjob`): Set Origin → jog to a nearby safe spot → Set
  Prime Position (stored relative to the origin) → Prime moves there, the
  operator runs the pump by hand → Start returns to the origin before the
  building toolpath. No priming lap, no computed prime location, no pump
  control.
- Travel is `G1` at the planned travel speed (not `G0`).
- Automatic Z and pump control remain future controller capabilities.

### Adaptive lift boundaries — operator, clean cuts only (2026-10-14)

The TARGET physical lift height (e.g. 18 in, a planning setting) is distinct
from the Slicer's NOMINAL layer thickness. With manual Z the real layers may
be thinner or thicker than nominal, so the operator may move the END of the
current lift on the controller ("end this lift early" / "continue into more
layers", also while awaiting its frame reposition) using an observed layer
thickness. The controller never invents layers or geometry and never
reorders: a boundary may only move to a CLEAN CUT of the exported pass order
(every earlier pass in a lower layer than every later one) that starts a
re-entry segment (`reentry_marks`). Same lines, same order — only the pause
point moves; lifts estimated taller than the machine's Z travel are refused.
Interleaved islands inside a lift offer no clean cuts there; changing that
would need replanning upstream. Contract: `contracts/mudjob/README.md`.

### Controller features use only the job and GRBL (2026-10-14)

Progress / time estimates (from the job's own G-code feeds and lengths,
labelled estimates, waits excluded), the live 2D toolpath view, Outline
(trace the job's XY bounds at jog speed, separate from Prime), GRBL feed
override (real-time bytes, deliberate ±1 / ±10 % steps + reset, value read
back from GRBL), and read-only GRBL diagnostics are Pi controller features —
modelled on Universal G-code Sender's behaviour, not its code — derived only
from the loaded `.mudjob` / G-code and GRBL's own reports. No macros,
probing, 3D view, G-code editing or GRBL setting writes.

### Design + Toolpath and the Pi interface are independent applications (2026-10-14)

Their ONLY integration boundary is the `.mudjob` file: a small, documented, versioned interchange contract (`contracts/mudjob/README.md` + example fixtures, version 1). Neither imports code from the other, shares runtime modules with it, calls its API, requires it to be installed or running, or couples to its internal data model. Design + Toolpath serializes its authoritative PrintPlan into the contract (`layer_assembly/machine_job.py`); the Pi parses and validates it independently (`pi-interface/app/jobs/current_job.py`). A `.mudjob` exported on one computer must open on an independent Raspberry Pi with no knowledge of the Designer. Interoperability is tested with contract fixture FILES read by each subsystem's own tests — never by cross-importing code.

File roles: `.muddesign` = editable AUTHORING project (Designer Save / Open), never a print job (the Pi refuses it); `.mudjob` = MACHINE JOB, one whole-building print as ordered first-class lifts from the PrintPlan (the Pi never recalculates lift boundaries); plain G-code = a less-rich external input the Pi still accepts (third-party G-code preflight: later). Undecided machine semantics are never chosen silently: an export that cannot be executable says so (`executable: false` + `unresolved`).

### Machine profiles — independent on each side (2026-10-15; manually reviewed, checkpoint 2026-10-15)

Machine profiles are independent configuration on each side. `.mudjob` communicates job assumptions and requirements; it does not synchronize applications.

Each application owns and persists its own profile (name, inches, printable X / Y extents, maximum total building Z, Z travel per frame position — distinct constraints; forward-compatible, unknown keys kept). Design + Toolpath: the profile the PROJECT is designed for (`.muddesign` schema 5), a reference envelope and an informational check — designs are never clipped, snapped or resized. Pi interface: the profile of the PHYSICAL machine (local `machine_profile.json`), AUTHORITATIVE — it recomputes every job's needs from the job itself and never trusts the Designer's `preflight`; a native `.mudjob` exceeding a configured limit cannot Start. Envelope convention (provisional): [0, X] × [0, Y] from the print origin; a job larger than an extent fails, one that fits but lies outside it only warns. Real dimensions are not yet recorded: the Designer default (120 × 120 in, from "approximately 10 ft × 10 ft or larger") is marked provisional; maximum building Z is unknown (unset = unchecked); Z travel per frame defaults to the documented ~18 in.

### One authoritative print plan feeds simulation, G-code and machine execution

The whole-print plan (ordered layers → continuous runs, travels and layer transitions in world coordinates, with source identities) is produced once by the Design + Toolpath subsystem from the exact geometry the designer sees. Print Simulation, G-code export and any machine-controller execution consume that plan (and its timeline) instead of re-deriving order, starts or timing. Time figures from it are estimates (constant speeds), not machine timing. Details: `design-toolpath/layer_assembly/PROJECT_MEMORY.md` → "Print plan".

### Active Design + Toolpath baseline (2026-10-14)

**ACTIVE DESIGN + TOOLPATH BASELINE (2026-10-14): `dd70148` + one cleanup pass, branch `restart/post-print-simulation`.** After manual review, development was ROLLED BACK to commit `dd70148` (the four-workspace architecture Designer → Assembly → Wall Systems → Print Simulation, deferred print paths M0–M2, architectural openings) — this is the current, stable Designer state. The later work on `main` / `origin/main` — M3 stack-aware lattice (`040d8de`), M4 sub-pass 1 (`fdaa7d7`), the junction harness / shared junction / region framework J1–J4a (`481554e`, `48845cc`) — and the home machine's unfinished M4 local lattice repair (branch `archive/home-m4-local-repair-wip-2026-10-14`, `487f796`) are ARCHIVED HISTORY, kept on GitHub for reference, NOT the current development direction. Explicitly deferred (not to be attempted without a new decision): corner glitches, junction geometry, standardized endings / end caps, motifs and other Wall Systems behaviour; the deferred-toolpath milestones M3+ are no longer the plan of record. Focus moved to the Raspberry Pi interface (2026-10-14). Details: `design-toolpath/layer_assembly/PROJECT_MEMORY.md` → "ACTIVE BASELINE".

### Architectural editing is separate from print-path generation (2026-10-11)

The workflow is Designer → Assembly → Wall Systems → Print Simulation. The Designer and the Assembly edit ARCHITECTURE only: the walls themselves (each path is a wall owning its thickness, alignment, Corner R and end caps — Wall Groups were removed 2026-10-15; junction / end-cap shape is edited in the Designer) and lightweight wall ENVELOPES (the Designer's build stopped after walls, junctions, trims and openings). The printable CONSTRUCTION is a separate project-level data structure, WALL SYSTEMS (construction type + parameters, assigned to individual walls, the same in every layout state; one construction may span thicknesses), edited in its own workspace. Wall Systems, lattice and printable wall geometry are generated only by the explicit "Generate Wall Systems" (the existing full-resolution pipeline for the whole assembled building); the Print Simulation plans (routing, lifts, timing) only from CURRENT generated geometry and never generates it. Generated geometry that no longer matches the design is labelled OUTDATED, never treated as current. Envelope-level support is preliminary; bead-level support of the generated geometry is authoritative. The Assembly places layers and validates openings from the architecture only (envelopes, architectural pier / overhang rules), so changing a Wall System never changes the Assembly; construction-specific minimums are reported after generation in the Wall Systems inspection. Project files (schema 7) store each wall's envelope in the designs and the Wall Systems in the project; older files are migrated one way (geometry-preserving except reported Variant-specific physical properties, which are dropped). Details since 2026-10-15: `design-toolpath/WALL_ARCHITECTURE.md`. Details: `design-toolpath/DEFERRED_TOOLPATH_ARCHITECTURE.md`, `design-toolpath/layer_assembly/PROJECT_MEMORY.md` → "WALL GROUPS × WALL SYSTEMS WORKSPACE" and "DEFERRED PRINT PATHS".

### Do not replace GRBL without demonstrated need

GRBL already operates the machine successfully.

Initially prefer building around it.

Consider modification or replacement only when an actual requirement cannot reasonably be met with the existing firmware.

---

### Z is commanded by the Pi; automatic Z within prints is still a goal (2026-10-14)

Z hardware path DECIDED: Pi → Cytron MD13S → 12 V actuator; not a GRBL axis
(`CONTROL_BOX_HARDWARE.md`). Manual Z remains available through the Pi UI and
the remote. Current jobs keep Z manual (XY-only, 2026-10-14). Automating Z within
prints remains a goal whose method is NOT decided; Z is open-loop, so do
not assume known Z positions.

(Previously, until 2026-10-14: "how automation will work has not been
decided; do not assume Z will be controlled through GRBL" — the GRBL caution
still stands; the hardware question is now answered.)

---

### Extrusion is outside the software-control scope

The large external mud/concrete/adobe pumping system is independently controlled.

The Mud Printer Software does not need to command extrusion.

Do not introduce extrusion-control requirements unless the user explicitly changes this project requirement.

2026-10-14: still true today, but future pump control is anticipated
(mechanism undefined). Extrusion pause / resume is reserved as a remote /
software concept only — no implementation, signal or wiring is defined.

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

The existing (pre-control-box, still in use today) software/control workflow is approximately:

computer running UGS
        ↓
USB / serial
        ↓
Arduino (documented as Mega at the time; Uno per the 2026-10-14 baseline) running GRBL
        ↓
X/Y motion

Z:
manual control of linear actuator (to be replaced by Pi → MD13S; see CONTROL_BOX_HARDWARE.md)

Extrusion:
independently controlled external pumping system

### Pi Interface — Milestone 1 Software Complete (Mac)

The Raspberry Pi Printer Interface Milestone 1 has been fully implemented
in software and is passing all tests on Mac.

The software is ready for Pi deployment and physical machine testing.

2026-10-14: lift-aware building jobs (P1/P2: drain + operator-confirmed frame
reposition between lifts, one responsive UI with synchronized clients) and
`.mudjob` import of the Designer's export (P3), then the current-machine
execution pass: executable XY-only jobs, Set Origin / Set Prime / Prime /
Start-returns-to-origin, jog + re-zero during a frame reposition, and
"live" ok-waits (no fixed per-line timeout while GRBL answers status polls).
Not yet tested on hardware.

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
  + prototype, checkpoint 8d9d439): the Designer defines one reusable 2D Layer
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
- Workspace: DESIGN sidebar (source geometry: paths, properties, wall
  geometry, read-only Wall Network connectivity) | WALL SYSTEM sidebar
  (construction: explicit groups of paths owning wall thickness /
  alignment / construction type / parameters / the Skin + Web web) | canvas (zoom at the pointer, Space / middle-drag pan,
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
- What constraints arise from the physical scale of the machine? (Machine profiles exist from 2026-10-15; the real printable X / Y and maximum building Z are still unmeasured, and where the print origin sits relative to the frame's printable area — the envelope convention — is provisional.)
- What constraints arise from material behavior and the hose-fed printing process?
- What should the boundary between design and toolpath generation ultimately be?
- Wall / junction / corner / cap ownership: `design-toolpath/WALL_ARCHITECTURE.md` approved (revision 4, §F final decisions); milestones M0–M4 implemented 2026-10-15, manually reviewed and checkpointed — walls own their properties, no Wall Groups, persistent junction records in shadow mode. M5+ (junction geometry changes) not started; open points in "Before M5 — to decide".

### Raspberry Pi / Printer Interface

- What is the correct jog speed for this machine's scale? (Must be validated physically)
- Does the Pi need to act as a Wi-Fi access point for offline field use?
- Should the interface support job recovery after power failure? (The hardwired E-stop is believed to cut power to the Pi and Uno too — VERIFY; see `CONTROL_BOX_HARDWARE.md`.)
- How should Pi-side Z control guarantee default STOP on boot / crash / loss of control (hardware pull-down + loss-of-control mechanism TBD)?
- How should local/offline networking be implemented?

### Arduino / GRBL

- Will stock/current GRBL satisfy all required future machine behavior?
- Are any GRBL customizations actually necessary?
- Which board and GRBL build are actually installed (Uno per the 2026-10-14 baseline; earlier docs said Mega)?
- ~~How should automatic Z control be implemented? Through GRBL or a separate mechanism? How should manual and automatic Z coexist?~~ Hardware answered 2026-10-14: Pi → MD13S, not GRBL; manual and automatic Z both go through Pi software. Open: Z use within prints, Z position knowledge.

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

2026-10-15 (checkpoint)

Committed after manual review: the Pi controller features pass, the dashboard UI pass, and the viewer corner-cutting fix (Pi top view + Designer Print Simulation drag LOD: index-stride vertex skipping replaced by error-bounded simplification; PrintPlan → .mudjob verified faithful vertex for vertex on the exact operator project). Machine Profile work not started.

2026-10-14 (controller features)

Pi controller: progress / estimates (job + lift + layer), planned speeds + GRBL feed override, live 2D toolpath view, Outline, Advanced diagnostics (alarms / errors with descriptions, read-only $$ / $G / $#), manual-Z lift-height display with observed layer thickness, and operator-adjustable lift ends at clean cuts. `.mudjob` gains optional `lift_plan`, `reentry_marks`, `travel_lines` (exporter emits a re-entry segment per pass). New decisions "Adaptive lift boundaries" and "Controller features use only the job and GRBL".

2026-10-14 (current-machine execution)

Executable `.mudjob`: XY-only G-code in inches from the PrintPlan (Z and pump manual, layer / Z metadata kept). Pi: print origin + prime position workflow (controller feature, not in the job), Start returns to the origin, jog / re-zero allowed while awaiting a frame reposition, per-line `ok` waits bounded by GRBL liveness instead of a fixed 10 s timeout. New decision "Current machine execution".

2026-10-14 (control box)

Control-box hardware baseline documented in `CONTROL_BOX_HARDWARE.md`: Pi 4 supervisory controller, Arduino Uno / GRBL for X/Y (earlier docs said Mega — VERIFY), Z decided as Pi → Cytron MD13S → 12 V actuator (not GRBL; old SRDKG RF Z controller superseded), planned Kaleinavi RF remote read by Pi GPIO (REMOTE STOP is not an E-stop), hardwired E-stop the only safety stop, future pump control anticipated but undefined.

2026-10-14 (P3)

`.mudjob` interchange established (Pi P3): contract `contracts/mudjob/`; Designer "Export Print Job" (Print Simulation) serializes the current PrintPlan — non-executable until the machine semantics are decided; the Pi validates / displays it and refuses to run it; `.muddesign` refused by the Pi. New decision "Design + Toolpath and the Pi interface are independent applications". Pi P1/P2 (lift-aware jobs, reposition UI) committed earlier the same day.

2026-10-14

Design + Toolpath ROLLED BACK to the stable four-workspace baseline (`dd70148` + cleanup; branch `restart/post-print-simulation`): Wall Geometry (junction / end-cap treatment) moved from the Designer to the Wall Systems workspace, the Designer's Extra Offsets control removed (older designs can still remove theirs), Assembly Opening Alignment default Vertical / Plumb (project file schema 4 keeps older projects' alignment). Later M3 / M4 / junction work archived, not current. Development focus moved to the Raspberry Pi interface.

2026-10-11

Design + Toolpath: DEFERRED PRINT PATHS (M0 + M1; uncommitted, pending manual review) — Assembly editing on architectural envelopes, explicit "Generate Print Paths" print preparation (unchanged full pipeline), preliminary vs bead-level support; new decision "Architectural editing is separate from print-path generation". Exact Assembly edits 1.2–3.4 s on the 84-layer benchmark (were 12–44 s); print outputs byte-identical. layer_assembly 619, design_proto 1724, toolpath_proto 92 tests green.

2026-10-10

Design + Toolpath: CHECKPOINT of the work since 951c42b (Designer, Assembly, Print Simulation, lift-aware print plan, Save / Open, Assembly openings) committed as one checkpoint. Assembly openings: Diamond (angular / curved vesica) and Arch (lower flat / angular / curved / rounded + optional middle + upper angular / curved Gothic / header), constrained live so the stored dimensions are always printable (the edited dimension wins); the Header upper generates associated Assembly headers (support, preview, print plan). Details: `design-toolpath/layer_assembly/PROJECT_MEMORY.md` → "OPENING SHAPES, SECOND PASS".

2026-10-09

Design + Toolpath: LIFT-AWARE PRINT PLANNING — lifts of whole layers (≤ 18 in Z travel), explicit frame-reposition events, island-by-island scheduling inside a lift, followed by the Print Simulation (see "Lifts and frame repositioning"). Before it, a reliability pass: Simulation sync / cancellation / Rebuild, stable numeric fields, New Project. Uncommitted, pending manual review.

Design + Toolpath: PRINT SIMULATION — a third application section (Designer | Assembly | Print Simulation) that plays back the authoritative print plan (no separate slicer: geometry, order and timing all come from the planner); layer inspection, time estimates, plan navigation. Uncommitted, pending manual review. design_proto 1723, layer_assembly 315, toolpath_proto 92 tests green.

Design + Toolpath: whole-print PLAN (every Assembly layer routed by the Designer's router, ordered with automatic starts / layer transitions, manual Route Origins kept) + time estimates; connected-wall routing fixes (a line through a generated wall, grazing walls bonded). New decision: "One authoritative print plan feeds simulation, G-code and machine execution". Uncommitted, pending manual review. design_proto 1723, layer_assembly 312, toolpath_proto 92 tests green.

Design + Toolpath: whole-project Save / Open — one versioned JSON project file (`.muddesign`, authoring state of every Layer Design + the Assembly, central schema migrations, newer versions refused); see the new decision "Project files store authoring state, versioned, separate from machine output". Uncommitted, pending manual review. design_proto 1678, layer_assembly 300, toolpath_proto 92 tests green.

2026-10-06

Pi Interface Milestone 1 software complete on Mac. Physical testing pending.
Design + Toolpath: Phase 2 complete (90 tests). Phase 3 design canvas: checkpoints 414c283 / 51387d3 / 0898d16, then the 2026-10-06 Designer checkpoint (Design / Print sidebars, Trim, rounded-junction assemblies, Material / Bead, Contact + Return-Lane Overlap, no-retrace closed routes, attached-branch return geometry, Route Origin, blue bead / vector rendering, zoom / pan, persistent Wall Network, trim ghost fix): design_proto 1059 + toolpath_proto 90 + pi-interface 78 tests green. Then the Z-phase checkpoint 8d9d439 (Layer Designs, Assembly, semantic transforms) and the 2026-10-07 WALL SYSTEMS checkpoint (explicit Wall Systems owning construction — Single / Hollow / Skin + Web / Parallel / Interleaved / Linked / Chained Loop; canonical end caps; mixed-system junctions by printed-path crossings; Wall System sidebar; canonical lattice corner motifs; Adaptive Truss; concealed route seam, travel order and Move Start): design_proto 1445 + layer_assembly 124 + toolpath_proto 90 tests green.
Z phase (2026-10-06; checkpoint 8d9d439, pushed): Layer Designs with live inheritance, a shared lineage lattice and one project bead width (Designer); the Layer Assembly subsystem and workspace (stack by physical height, section / assembly-wide transforms, semantic transform groups, vertical support with HEADER NEEDED / INSUFFICIENT LAYER SUPPORT, headers, undo / redo, 3D preview). Stable boundary: the Designer owns source / wall semantics, junctions, openings, lattice, printable geometry, routes and the material; the Assembly owns layer order, Z, transforms, group choices, vertical support and assembly objects; the Assembly asks the Designer for each grouped layer's design with its SOURCES moved and receives resolved, source-tagged geometry (never rubber-sheets beads). Assembly Profiles (2026-10-08, uncommitted) use the same boundary: per layer Z the Assembly names which part of a straight source wall survives (a self-supporting opening, a taper) and the Designer resolves that layer with those source cuts — profiles never generate toolpaths. Principles: a closed print route outranks vertical lattice registration; lattice identity persists through Z where topology is unchanged; routine edits must not re-solve unchanged layers; mud requires physical support below — checked in the Assembly, never weakened to hide findings. Main open issue: lattice coherence at independently transformed junctions (legitimate support findings on the reference network). Tests (2026-10-06 audit): design_proto 1143, layer_assembly 124, toolpath_proto 90, pi-interface 78 — all green.
Repository permanently relocated to `/Users/charliebritton/Projects/mudprintersoftware` (previously `/Users/charliebritton/Documents/mude printer/mudprintersoftware`). Post-move environment restored 2026-10-05: the three `.venv`s were recreated with the same package versions, and all suites pass from the new path (Pi 78, toolpath_proto 90, design_proto 889 incl. 3 JS UI smoke tests).
Arduino/GRBL subproject not yet started.
