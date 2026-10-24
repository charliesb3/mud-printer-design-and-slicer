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
Pi USB-A → Uno USB-B: serial + the Uno's power (wired 2026-10-20; no Pi GPIO used)
        ↓
Arduino Uno / GRBL
        ↓
step/direction control → three DM556T drivers: X, Y1, Y2
        (standard GRBL 1.1 Uno pin map: X PUL+ D2 / DIR+ D5; Y1 + Y2 share
         PUL+ D3 / DIR+ D6 = one logical Y axis; returns commoned to Uno
         GND; D4 / D7 (Z step / dir) unused —
         table: ../CONTROL_BOX_HARDWARE.md → "Arduino Uno → DM556T wiring")

HISTORY (2026-10-20): X step was found on D4 (stock Z step); the operator is
rewiring it D4 → D2 so the machine uses the STANDARD GRBL 1.1 pin map — no
custom pin map. Superseded; confirm the installed build is stock-pin-map
GRBL 1.1 ($I) and check X with a slow jog.
        ↓
X/Y machine motion

Limits: the existing X/Y limit-switch hardware is to connect to the Uno/GRBL
(not the Pi); current wiring and GRBL settings — VERIFY.

This system has already operated successfully.

### Z

CURRENT (decided 2026-10-14): Z is NOT a GRBL axis and needs nothing from
this subsystem. The Raspberry Pi is the sole Z command source, through a
Cytron MD13S driving the 12 V reversible linear actuator
(`../CONTROL_BOX_HARDWARE.md` → Z axis). Do not add Z to GRBL.

HISTORY: Z has historically been controlled manually (still the case until
the new control box is built). Until 2026-10-14 the automatic Z architecture
was unselected and GRBL was one option ("do not assume Z must be added to
GRBL"); that question is now answered for the hardware path.

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

Z remains manually controlled today; in the new control box it is commanded by the Pi via the MD13S, wired directly to Pi GPIO — not through the Uno and not GRBL (2026-10-20).

In the new control box the Uno is connected to the Pi by USB (2026-10-20); pi-interface replaces UGS's operator functions there (UGS is not installed on the Pi).

No GRBL modifications associated with this new software project have yet been made.

2026-10-20: capture tooling prepared, nothing run on the controller yet:
`pi-interface/tools/grbl_capture.py` (read-only `$I` `$$` `$#` `$G`, hard
whitelist) and `pi-interface/docs/grbl-capture-checklist.md` (capture
procedure + a PREPARED, not executed, avrdude backup / flash procedure from
the Pi). The Pi interface reads `$I` build info into its diagnostics.
Firmware has NOT been flashed or changed.

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
- Stock GRBL 1.1 + standard Uno pin map, configured only through normal `$`
  settings (size, steps/mm, rates, acceleration, direction invert, travel,
  limits, homing — captured, never guessed). Do not reflash merely to strip
  unused Z / spindle / coolant parsing; the Pi keeps speaking standard GRBL
  (2026-10-20). Firmware flashing is never done from the normal printer UI.

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
- ~~Does the installed build drive X step on D4?~~ Superseded 2026-10-20: X rewired to stock D2; intended baseline = standard GRBL 1.1 Uno pin map (installed build still to confirm).
- What homing/limit behavior currently exists?
- What safety/interlock behavior may eventually be needed?
- What machine-state information will the Raspberry Pi interface need from GRBL?

Do not answer these by assumption. Resolve them as the physical machine and requirements are inspected.

---

## Completed Work

- Existing Arduino + GRBL architecture identified as the working motion-control baseline (board: Uno per the 2026-10-14 baseline).
- X/Y responsibility established.
- Z identified as currently manual; 2026-10-14 decided as Pi-commanded (MD13S), outside GRBL.
- Responsibility boundary between GRBL and Raspberry Pi established.

No new firmware work has yet been completed for this project.

---

## Next Steps

1. Before modifying firmware, document the existing working GRBL installation and configuration — run the read-only capture (`pi-interface/docs/grbl-capture-checklist.md`; commissioning step 3) and commit it here as `grbl-settings-YYYY-MM-DD.txt`.
2. Determine the exact GRBL version currently running.
3. Record relevant machine settings and existing X/Y behavior.
4. Preserve a known-good copy/configuration of the working controller before experimentation.
5. Defer GRBL modification until a concrete software or machine requirement requires it.
6. ~~Investigate Z-control options~~ — decided 2026-10-14 (Pi → MD13S, not GRBL); nothing to do here for Z.
7. When wiring the control box: connect the existing X/Y limit switches to the Uno and document GRBL limit / homing settings (`../CONTROL_BOX_HARDWARE.md` VERIFY list).

---

## Last Updated

2026-10-23 — first-motion GRBL configuration applied

Pre-change capture committed at
`arduino-grbl/captures/grbl-pre-first-motion-2026-10-23_235310.txt`.

Operator-stated drivetrain for the first X/Y motion test (2026-10-23):

- Arduino **Uno** running GRBL **1.1h** (confirmed via `$I`: `[VER:1.1h.20190825]`)
- Three STEPPERONLINE DM556T drivers (X, Y1, Y2 — Y1/Y2 share Y step/dir)
- All 8 DIP switches ON on each driver — operator asserts 200 ppr full step
  (**UNVERIFIED against DM556T V4.0 label**: typical DM556T SW5-SW8 convention
  reads SW5-SW8 all ON as a high microstep, NOT full step. Operator must
  physically read the driver label and confirm, or verify empirically on
  the first measured jog.)
- NEMA 23 steppers, 1.8° / 200 full steps per rev
- 20:1 planetary gearboxes
- 12-tooth drive sprockets
- ANSI #25 chain (**assumed**; measure to confirm)
- Nominal working envelope: 120 × 120 in = 3048 × 3048 mm (**unverified**)

Settings applied (via Pi WebSocket `console_send`, verified by `$$` re-read):

| Setting | Pre-value | Post-value | Rationale |
|---|---|---|---|
| `$100` X steps/mm | 23.805 | **52.493** | theoretical from drivetrain at M=1 |
| `$101` Y steps/mm | 23.805 | **52.493** | same |
| `$110` X max rate | 1500.000 mm/min | **381.000** mm/min | 15 in/min = 0.25 in/s |
| `$111` Y max rate | 1500.000 mm/min | **381.000** mm/min | same |
| `$130` X max travel | 3352.800 mm | **3048.000** mm | 120 in nominal |
| `$131` Y max travel | 3200.400 mm | **3048.000** mm | 120 in nominal |

Settings preserved (verified unchanged):

- `$3=0` direction invert mask — **must be observed during first X+ / Y+ jog**
- `$20=0` soft limits DISABLED
- `$21=0` hard limits DISABLED
- `$22=0` homing DISABLED
- `$120=5.000`, `$121=5.000` acceleration (mm/s²) — already conservative
- `$0=10`, `$1=25`, `$2=0`, `$10=1`, `$13=0`, step pulse timing and reporting

**Known discrepancy**: `$100` pre-value `23.805` corresponds to a drivetrain
ratio that doesn't match the stated geometry (ratio 2.206, not a standard
microstep divisor). Either the pre-value was from a prior empirical
calibration on different mechanics, or the stated geometry needs
re-verification. The planned initial jog speed (46 mm/min ≈ 0.03 in/s)
bounds the per-step motion to ~0.1 mm, so a wrong `$100` is observable but
not dangerous on the first short jog. Verification procedure:
`arduino-grbl/steps-per-mm-calculation.md` → "Independent verification
procedure".

**Rollback**: send through the controller console
`$100=23.805 $101=23.805 $110=1500 $111=1500 $130=3352.8 $131=3200.4`.
Full pre-change capture at
`arduino-grbl/captures/grbl-pre-first-motion-2026-10-23_235310.txt`.

Remaining commissioning items (gating first-print safety):

- **DM556T microstep label** — read the top-of-driver microstep table and
  confirm "all 8 ON → 200 ppr" matches, or recompute `$100`.
- **Chain pitch** — confirm ANSI #25 (6.35 mm) by measurement.
- **Direction convention** — observe X+ / Y+ as positive machine motion on
  first jog; flip `$3` bit if reversed.
- **Steps/mm by measurement** — jog a defined small distance, measure actual
  travel, apply correction factor to `$100` / `$101` if needed.
- **Physical travel limits** — the nominal 120 × 120 in envelope is NOT
  verified. First jogs stay well inside expected travel.
- **Z axis** — Z stays off via `Z_PWM_DUTY=0` in Pi `.env`. Z PWM / dir
  commissioning (`docs/acceptance-test.md` steps 1-4) is a separate
  session, not yet performed.
- **REMOTE STOP physical test** — software reports CH1 armed but the
  button has not been physically pressed to confirm latch + GRBL feed
  hold + job hold work end-to-end.

2026-10-20 (pi-interface hardware pass)

Read-only capture tool + checklist and a prepared (unexecuted) flash
procedure; decision recorded: stock GRBL 1.1, settings only. No firmware
change.

2026-10-20 (Uno → DM556T wiring)

Driver wiring recorded; Y1 / Y2 shared PUL / DIR confirmed (no longer
VERIFY). X step observed on D4 → being rewired to D2: baseline = standard
GRBL 1.1 Uno pin map (X step D2, Y step D3, X dir D5, Y dir D6).

2026-10-20

Control-box wiring: Uno on Pi USB (serial + power, no GPIO); MD13S wired to
the Pi, not the Uno; X/Y limits stay on GRBL. See `../CONTROL_BOX_HARDWARE.md`.

2026-10-14

Control-box baseline: board recorded as Arduino Uno (earlier: Mega — VERIFY),
three DM556T drivers, X/Y limits to GRBL, Z decided as Pi → MD13S (not GRBL).
See `../CONTROL_BOX_HARDWARE.md`.

2026-10-03

Initial subproject memory created during repository setup.
