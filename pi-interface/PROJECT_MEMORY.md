# Raspberry Pi / Printer Interface — Project Memory

## Purpose

Develop a printer-specific higher-level control system that runs on a Raspberry Pi and replaces the role currently served by Universal G-code Sender (UGS).

The goal is not merely to clone UGS.

The goal is to build a simpler and more appropriate interface around the actual workflow of this large-scale mud printer.

The Raspberry Pi should become the persistent computer associated with the physical printer.

---

## Current Existing Workflow

The printer currently operates approximately as:

computer running UGS
        ↓
USB / serial
        ↓
Arduino Mega running GRBL
        ↓
X/Y machine motion

This workflow has already successfully operated the printer.

The new Pi interface should therefore be developed as an incremental replacement for a known working system.

---

## Intended Architecture

The likely architecture is:

laptop / tablet client
        ↓
browser
        ↓
local network / Wi-Fi
        ↓
Raspberry Pi
        ↓
printer-control application
        ↓
USB / serial
        ↓
Arduino Mega / GRBL

The Raspberry Pi should run the actual printer-control process.

The laptop/tablet should primarily act as a user interface.

The client device should not need to remain continuously connected for the printer to continue executing an active job.

---

## Intended Capabilities

Potential capabilities include:

- connect to GRBL
- load G-code files
- send/stream G-code
- jog the machine
- home the machine where appropriate
- start jobs
- pause jobs
- resume jobs
- stop jobs
- display machine position
- display GRBL state
- display alarms/errors
- display job progress
- manage print files/jobs
- expose printer-specific controls
- eventually interact with the future Z-control system
- provide a browser-based interface
- run automatically when the Raspberry Pi/printer starts
- operate without internet access
- work over an existinork when one is available
- provide or participate in a local network when no infrastructure exists

---

## Offline / Local Operation

Offline operation is an important requirement.

The physical printer may be used at remote or undeveloped sites where internet service and network infrastructure are unavailable.

Core printer operation must not depend on:

- cloud services
- external servers
- an internet connection

A user should ultimately be able to arrive with a laptop or tablet, connect locally to the printer, open the interface, and operate the machine.

---

## Job Ownership

The Raspberry Pi should own or stream the active print job.

The browser/client is a control and monitoring interface.

An active print should not inherently stop because:

- the browser is closed
- the laptop sleeps
- the tablet disconnects
- Wi-Fi temporarily drops
- the user leaves the interface

The exact behavior for reconnection, interruption, and recovery remains to be designed.

---

## Relationship to Real-Time Motion Control

The Raspberry Pi handles higher-level printer operation.

The Arduino/GRBL layer handles timing-critical motion control for the axes under its control.

Do not implement software-timed step pulses or similar real-time motion behavior on the Raspberry Pi unless the architecture is deliberately reconsidered.

Where practical:

Raspberry Pi determines WHAT commands should be sent.

GRBL determines HOW commanded motion is executed in real time.

---

## Z Control

Z is currently a manually controlled linear actuator.

Future automatic Z control is a project goal.

The Pi interface will likely need some way to command and display Z behavior once the Z architecture is established.

However, the actual control architecture has NOT been selected.

Do not assume that the Raspberry Pi will directly drive the Z actuator.

Do not assume that GRBL will control Z.

Treat the implementation of automatic Z as an open architectural question.

Manual Z control should remain available.

---

## Extrusion

The external mud/adobe pumping system is independently controlled.

This software does not need to control extrusion.

Do not add extrusion-rate, pump, or material-flow controls unless the project requirements explicitly change.

---

## Current State

No Raspberry Pi printer-interface application has been written yet.

The current working printer interface is UGS running on a conventional computer.

A Raspberry Pi is intended to become the dedicated printer-side computer.

The technology stack for the Pi application has not yet been selected.

---

## Decisions Made

- Use a Raspberry Pi as the persistent higher-level printer computer.
- Develop a printer-specific alternative to the current UGS workflow.
- Prefer a browser-based client interface.
- Keep core operation local and offline-capable.
- The Pi should own/stream active jobs rather than relying on the browser/client to do so.
- Preserve the Arduino/GRBL controller for real-time motion control unless a concrete reason emerges to change it.
- Do not assume a solution for automatic Z control yet.
- Do not control the external extrusion pump from this software.

---

## Open Questions

- What software stack should the Raspberry Pi application use?
- What should run as the backend/server?
- What should be used for the browser UI?
- How should serial communication with GRBL be implemented?
- How should G-code streaming and flow control work?
- How should job progress be represented?
- How should pause/resume work?
- How should errors and GRBL alarms be handled?
- What should happen after a power failure or interrupted print?
- How should reconnection work?
- How should job files be stored?
- How should the Pi create or join local networks?
- How should the interface discover/connect to the printer?
- How should future Z control integrate with the interface?
- Which UGS features are actually needed?
- Which UGS features are unnecessary for this machine?
- What printer-specific controls would improve operation?

---

## Completed Work

- Raspberry Pi identified as the dedicated higher-level printer computer.
- Browser-based local interface identified as the preferred general direction.
- Offline/local operation established as a requirement.
- Responsibility boundary between Pi and GRBL established.
- Active-job ownership assigned conceptually to the Raspberry Pi.

No application implementation has yet been completed.

---

## Next Steps

1. Define the smallest useful replacement for the current UGS workflow.
2. Identify the exact UGS functions currently used when operating the printer.
3. Choose an initial Raspberry Pi software stack.
4. Establish basic serial communication with GRBL.
5. Build a minimal interface capable of connecting to GRBL and reporting machine state.
6. Add functionality incrementally while preserving the ability to operate the printer using the existing UGS workflow during development.

---

## Last Updated

2026-10-03

Initial subproject memory created during repository setup.
