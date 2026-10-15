# Arduino / GRBL Motion Control — Project Memory

## Purpose

Maintain and, only where necessary, adapt the real-time motion-control layer for the large-scale mud printer.

The existing motion controller is an Arduino running GRBL — an **Uno** per the
2026-10-14 control-box baseline (earlier versions of this file said Mega;
VERIFY the installed board and GRBL build). Hardware interfaces: see
`../CONTROL_BOX_HARDWARE.md`.

This is a working system that has already successfully moved the printer and been used during printing.

The initial strategy is therefore to preserve the existing GRBL-based motion system rather than replace it.

---

## Current Motion Architecture

The machine is Cartesian.

### X/Y

X and Y are currently controlled through GRBL.

Approximate control path:

UGS today → Raspberry Pi 4 (pi-interface) in the new architecture
        ↓
USB / serial (USB also powers the Uno)
        ↓
Arduino Uno / GRBL
        ↓
step/direction control → three DM556T drivers: X, Y1, Y2
        (Y1/Y2 believed to share the Y STEP/DIR — VERIFY)
        ↓
X/Y machine motion

Limits: the existing X/Y limit-switch hardware is to connect to the Uno/GRBL
(not the Pi); current wiring and GRBL settings — VERIFY.

This system has already operated successfully.

### Z

Z is provided by a linear actuator.

Z has historically been controlled manually rather than through GRBL.

Automatic Z control is a desired futurey.

The architecture for automatic Z control has NOT been selected.

Possible approaches should be evaluated when the project reaches that problem.

Do not assume that Z must be added to GRBL.

Manual Z control should remain available even after automatic control is introduced.

DECIDED 2026-10-14 (control-box baseline): Z is NOT a GRBL axis. The
Raspberry Pi is the sole Z command source, through a Cytron MD13S driving the
12 V actuator (`../CONTROL_BOX_HARDWARE.md`). Z needs nothing from GRBL or
this subsystem. (Previous state: Z method undecided, GRBL one option.)

---

## Role of GRBL

GRBL is responsible for timing-critical real-time motion control for the axes it controls.

Where practical, higher-level software should communicate with GRBL using standard machine-specific G-code and normal GRBL interfaces.

Do not modify GRBL merely because a behavior is easier to implement by changing firmware.

First determine whether the requirement can reasonably be handled using:

- standard G-code
- GRBL configuration
- the Raspberry Pi application
- toolpath generation

Modify or replace GRBL only when a concrete requirement demonstrates that the existing system is insufficient.

---

## Responsibility Boundary

Approximate intended responsibility split:

Design + Toolpath:
determines printable geometry and generates machine paths/instructions

Raspberry Pi:
handles user interaction, jobs, G-code streaming, and higher-level operation

Arduino / GRBL:
executes timing-critical motion reliably

The motion controller should remain relatively simple and deterministic where practical.

---

## Extrusion

Extrusion is not controlled by this subsystem.

The large external mud/adobe pumping system is independently controlled.

Do not treat an extruder as a GRBL axis or introduce extrusion-control requirements unless the project requirements explicitly change.

---

## Current State

Arduino (Uno per the 2026-10-14 baseline; earlier documented as Mega) + GRBL currently control X/Y.

The system has successfully moved and printed with the physical machine.

UGS is currently used as the higher-level sender/interface.

Z remains manually controlled.

No GRBL modifications associated with this new software project have yet been made.

---

## Decisions Made

- Retain the existing Arduino + GRBL system initially (Uno per the 2026-10-14 baseline).
- Preserve G-code as the higher-level interface where practical.
- Do not replace or substantially modify GRBL without a demonstrated requirement.
- Keep timing-critical motion control on a dedicated controller rather than moving it casually onto the Raspberry Pi.
- ~~Treat automatic Z control as an unsolved architectural question.~~
  2026-10-14: Z is Pi-commanded via a Cytron MD13S — not a GRBL axis.
- Preserve manual Z capability (now through the Pi UI / wireless remote).
- X/Y limit switches belong to GRBL (Uno), not the Pi (2026-10-14).
- Do not control extrusion through this subsystem.

---

## Open Questions

- What exact GRBL version/configuration is currently running on the machine?
- What current machine settings need to be documented?
- Will the existing GRBL implementation satisfy all future X/Y requirements?
- Are any custom GRBL behaviors actually needed?
- ~~How should automatic Z control be implemented? Should Z become a GRBL
  axis?~~ Answered 2026-10-14 for the hardware path: Pi → MD13S, not GRBL.
- What coordinates Z with X/Y within a print job? (Pi-side; the undecided
  `z_within_lift` semantic in `contracts/mudjob/README.md`.)
- Which board and GRBL build are actually installed (Uno vs earlier-documented Mega)?
- What homing/limit behavior currently exists?
- What safety/interlock behavior may eventually be needed?
- What machine-state information will the Raspberry Pi interface need from GRBL?

Do not answer these by assumption. Resolve them as the physical machine and requirements are inspected.

---

## Completed Work

- Existing Arduino + GRBL architecture identified as the working motion-control baseline (board: Uno per the 2026-10-14 baseline).
- X/Y responsibility established.
- Z identified as currently manual and requiring future architectural investigation.
- Responsibility boundary between GRBL and Raspberry Pi established.

No new firmware work has yet been completed for this project.

---

## Next Steps

1. Before modifying firmware, document the existing working GRBL installation and configuration.
2. Determine the exact GRBL version currently running.
3. Record relevant machine settings and existing X/Y behavior.
4. Preserve a known-good copy/configuration of the working controller before experimentation.
5. Defer GRBL modification until a concrete software or machine requirement requires it.
6. Investigate Z-control options when automatic Z becomes an active development milestone.

---

## Last Updated

2026-10-14

Control-box baseline: board recorded as Arduino Uno (earlier: Mega — VERIFY),
three DM556T drivers, X/Y limits to GRBL, Z decided as Pi → MD13S (not GRBL).
See `../CONTROL_BOX_HARDWARE.md`.

2026-10-03

Initial subproject memory created during repository setup.
