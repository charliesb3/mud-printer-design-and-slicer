# Design + Toolpath — Project Memory

## Purpose

Develop a design and toolpath environment specifically suited to the large-scale mud printer.

This subproject concerns both:

1. creating/manipulating printable geometry, and
2. translating that geometry into paths and machine instructions.

Design and toolpath are intentionally being kept together for now because the intended design representation may correspond closely to the phical paths followed by the printer.

This boundary may change later if experience shows that design and toolpath generation should become separate systems.

---

## Current Direction

The main design concept currently being explored is representing a print as a stack of 2D vector paths rather than beginning with a conventional 3D solid or mesh and passing it through a conventional slicer.

A possible workflow is:

2D vector/path
→ layers
→ transformations between layers
→ 3D preview
→ printable paths
→ G-code / machine instructions

Potential capabilities include:

- creating 2D vector geometry
- importing vector geometry
- stacking geometry into layers
- defining layer height
- scaling geometry between layers
- translating/offsetting geometry between layers
- rotating geometry between layers
- defining key layers/keyframes
- interpolating between keyframes
- previewing the resulting 3D form
- understanding machine-specific printability constraints
- generating machine paths
- exporting appropriatThe stacked-vector approach is promising but NOT yet a permanent architectural decision.

---

## Design Philosophy

Do not assume that this machine needs the conventional:

3D CAD
→ STL
→ slicer
→ G-code

workflow.

Because the printer is large-scale and prints adobe/mud, the relationship between the designed geometry and the actual nozzle path may be much more direct.

The software should exploit useful characteristics of the physical printing process rather than imitate desktop FDM software unnecessarily.

At the same time, do not reject conventional approaches merely because they are conventional. Evaluate approaches based on usefulness for this machine.

---

## Known Requirements

- The resulting output must ultimately be executable by the physical printer.
- The design system should be oriented toward large-scale mud/adobe printing.
- Architectural forms, walls, and building-scale structures are a primary use case.
- Do not unnecessarily restrict the system to architectural forms if the same retation can support other large-scale mud forms.
- Machine and material constraints should be incorporated as they are actually learned.
- Do not invent material or machine constraints without evidence.
- Preserve G-code as the interface to GRBL where practical.

---

## Relationship to Other Subprojects

This subproject produces the paths or machine instructions that will ultimately be executed through the printer-control system.

Approximate relationship:

Design + Toolpath
        ↓
G-code / machine instructions
        ↓
Raspberry Pi / Printer Interface
        ↓
Arduino / GRBL
        ↓
physical machine

This subproject should not take responsibility for real-time motion control.

It also does not control the external mud pumping/extrusion system.

---

## Current State

No Design + Toolpath application code has been written yet.

The project is currently at the conceptual/architectural stage.

The stacked-2D-vector concept is the leading direction but has not yet been validated through implemen
---

## Decisions Made

- Treat Design and Toolpath as one subproject for now.
- Explore stacked 2D vectors/paths as the primary initial design concept.
- Do not commit permanently to the stacked-vector representation yet.
- Design around the actual large-scale mud-printing process rather than automatically copying desktop FDM workflows.
- Keep real-time machine control outside this subproject.

---

## Open Questions

- What should the fundamental internal representation of a print be?
- Is a stack of 2D vectors/paths the correct representation?
- How should layers be represented?
- How should keyframes/key layers work?
- How should interpolation between keyframes work?
- Which transformations should be supported?
- How should geometry be created and edited?
- Which vector file formats should be supported for import?
- How should the resulting 3D form be visualized?
- How should printability be evaluated?
- Which machine-specific constraints should influence design?
- Which mud/adobe-specific constraints should influence design?
- How should toolpaths be converted into G-code?
- What should the actual application technology stack be?
- Should design and toolpath remain one system after the concept is better understood?

---

## Completed Work

- Initial subproject boundary established.
- Initial design philosophy established.
- Stacked-2D-vector approach identified as the first major concept to explore.

No application implementation has yet been completed.

---

## Next Steps

1. Define the first minimal Design + Toolpath prototype.
2. Decide what the smallest useful representation of a layered print should be.
3. Determine the minimum operations needed to manipulate that representation.
4. Build a simple prototype before committing to a large application architecture.
5. Use what is learned from the prototype to refine the permanent architecture.

---

## Last Updated

2026-10-03

Initial subproject memory created during repository setup.
