# Control Box Hardware — Current Baseline

Reference for the REAL electrical interfaces of the printer's control box.
Consult it before any software work that touches the serial link, GPIO, Z,
the wireless remote, limits, power-loss behaviour or extrusion signals.
Established 2026-10-14. Items marked **VERIFY** are current understanding,
not confirmed on the hardware — never treat them as facts in code.

System-level context: `PROJECT_OVERVIEW.md`. Software state:
`pi-interface/PROJECT_MEMORY.md`, `arduino-grbl/PROJECT_MEMORY.md`.

---

## Architectural principle

```
operator: Pi touchscreen / laptop browser / handheld RF remote
        ↓
Raspberry Pi 4 — SUPERVISORY controller (UI, jobs, Z, remote inputs)
        ↓ USB serial (G-code)                 ↓ 3.3 V GPIO (PWM + DIR)
Arduino Uno / GRBL — real-time X/Y      Cytron MD13S — Z motor driver
        ↓ STEP/DIR                            ↓
DM556T drivers (X, Y1, Y2)              12 V linear actuator (Z)
        ↓                                     ↓
                 physical machine

HARDWIRED E-STOP (120 VAC side) — separate, independent safety layer
```

- The Pi is **not** the real-time X/Y motion controller (that is GRBL) and
  **not** the safety system (that is the hardwired E-stop).
- The Pi is the **sole command source for Z**.
- Nothing in software, the Pi or the RF remote is safety-rated.

---

## Controllers

| Device | Role | Connection |
|---|---|---|
| Raspberry Pi 4 Model B | supervisory controller + UI host (pi-interface) | powered from the 12→5 V buck |
| Arduino Uno running GRBL | dedicated real-time X/Y motion controller | USB to the Pi (serial + the Uno's power) |

- The Uno is reached only through the GRBL serial protocol (see
  pi-interface: never `$N=` settings writes). **VERIFY** the serial device
  name on the Pi: a genuine Uno usually enumerates as `/dev/ttyACM0`, a
  CH340 clone as `/dev/ttyUSB0`.
- Earlier project documents said **Arduino Mega**; the 2026-10-14 baseline is
  an **Uno** (Mega references updated). **VERIFY** the board actually
  installed and its GRBL build (stock GRBL 1.1 targets the Uno/328P; the
  Mega uses a separate fork with a different pin map).

## X/Y motion

| Item | Detail |
|---|---|
| Drivers | three DM556T: X, Y1, Y2 |
| Y gantry | **VERIFY**: Y1 and Y2 believed to share the GRBL Y STEP/DIR signals (one logical Y axis) |
| Power | the two 48 V PSUs (**VERIFY** which PSU feeds which driver) |
| Limits | existing X/Y limit-switch hardware/connectors are to connect to the **Uno/GRBL**, not the Pi (current wiring and GRBL `$21`/`$22` state: **VERIFY**, see `pi-interface/docs/grbl-capture-checklist.md`) |

## Z axis

| Item | Detail |
|---|---|
| Actuator | ECO-WORTHY 12 V linear actuator, 18 in stroke, 330 lb / 1500 N, two-wire reversible DC (polarity = direction) |
| Driver (planned) | Cytron MD13S, powered from 12 V |
| Control | Pi 3.3 V GPIO: PWM (speed) + DIR — the Pi is the only command source |
| End of travel | actuator's internal end-of-travel protection only |
| Limits / homing / position feedback | **deferred** — none in the new architecture yet |

Software consequences (for coding agents):
- Z is **open-loop**: the Pi can command direction, PWM duty and duration,
  but cannot know Z position. Do not write code that assumes a known Z
  height, homing, or closed-loop positioning.
- If Pi software hangs while Z is commanded, hardware PWM keeps running until
  the actuator's internal end stop. Z motion control in software should be
  dead-man / time-bounded (like the X/Y jog), and the MD13S PWM input should
  be held low when the Pi is booting, crashed or unpowered — **VERIFY / design
  item** (e.g. a pull-down on the PWM line; Pi pins' default boot states).
- What Z does *within a print* (layer steps etc.) is still the undecided
  `z_within_lift` machine semantic (`contracts/mudjob/README.md`).

## Wireless remote (planned)

| Item | Detail |
|---|---|
| Hardware | Kaleinavi 6-channel 433 MHz industrial RF receiver + handheld transmitter |
| Receiver power | 12 V |
| Outputs | six potential-free (dry) relay contacts, read **directly** by Pi GPIO |
| Input wiring | Pi internal 3.3 V pull-up; relay closure pulls the GPIO to **Pi GND**; ~1 kΩ series resistor on each GPIO signal |
| Isolation | no isolated input board planned |

- **Absolutely no 12 V on any Pi GPIO.** Before connecting, **VERIFY** with
  voltage and continuity measurements that every COM/NO output is genuinely
  potential-free (no 12 V or receiver ground on the contacts).
- **VERIFY** the receiver's channel mode (momentary vs latching/toggle) per
  channel; Z UP / Z DOWN need momentary.
- Tentative functions (mapping is software-configurable, not fixed):
  REMOTE STOP, Z UP, Z DOWN, extrusion pause/resume, two spare/future.
- Z from the remote: handheld → Kaleinavi receiver → Pi GPIO → Pi software →
  MD13S → actuator. Software must debounce inputs and handle UP+DOWN pressed
  together safely.
- **REMOTE STOP is a software / operational stop, not a safety-rated
  E-stop.** UI and code must never present it as an emergency stop. Its exact
  effect is to be defined in software.

## Power

```
120 VAC → hardwired physical E-STOP → main power switch
        → 48 V PSU ×2   (X/Y DM556T drivers)
        → 12 V PSU      → enclosure fan, Kaleinavi RF receiver, Z (MD13S → actuator),
                          12→5 V buck → Raspberry Pi 4 (→ USB → Arduino Uno)
```

- Existing AC protection / fuses and terminal / distribution hardware stay.
- The hardwired E-stop is independent of the Pi, software and the RF remote
  and is the actual safety stop.
- As described, the E-stop and main switch are upstream of **every** PSU, so
  pressing the E-stop also powers down the Pi and (via USB) the Uno: after an
  E-stop the whole control system cold-boots and in-memory job state is lost
  (relevant to job recovery / "start at lift N"). **VERIFY**.
- **VERIFY** protective earth / bonding during final wiring.
- **VERIFY** the 12→5 V buck's continuous current covers the Pi 4 plus the
  touchscreen (and USB-powered Uno) once the touchscreen is known.
- **VERIFY** the 12 V PSU rating covers actuator peak/stall current + fan +
  RF receiver + buck simultaneously.

## Touchscreen

Already owned; exact model, interface (DSI / HDMI / USB touch) and power
requirements are **unknown — VERIFY**. (`pi-interface/docs/deployment.md`
previously assumed the official 7" 800×480 display; not confirmed.) Do **not**
finalize Pi GPIO pin assignments until the touchscreen's connections are
known. The UI is the same responsive web UI on touchscreen and laptop
(pi-interface decision).

## Extrusion

Electrically **deferred / undefined**. The pump is still controlled
independently today. Future control of the pump is anticipated (exact
mechanism not defined — **VERIFY with the user**), so remote and software
concepts reserve **extrusion pause / resume**, but no signal, wiring or
implementation is defined. Do not invent one.

## GPIO assignment

Not finalized (pending the touchscreen). Required Pi signals:
- MD13S: 1 × PWM (prefer a hardware-PWM-capable pin), 1 × DIR (outputs).
- Kaleinavi: 6 × digital inputs (pull-up, active-low).

---

## Superseded — do NOT reintroduce

| Not in the architecture | Replaced by |
|---|---|
| D2-ZFZ (previously considered) | Cytron MD13S driven by the Pi |
| a separate Pi relay board for Z | MD13S PWM/DIR from Pi GPIO |
| the existing SRDKG RF Z controller | Kaleinavi receiver → Pi → MD13S (Pi is the sole Z command source) |
| isolated input board for the RF contacts | direct GPIO with pull-ups + ~1 kΩ series (none planned) |
| Z through GRBL | not the current architecture (Z is Pi-commanded) |

---

## BOM / status

**1. Owned**
- Raspberry Pi 4 Model B
- Arduino Uno with GRBL (X/Y)
- 3 × DM556T stepper drivers (X, Y1, Y2) + X/Y steppers
- 2 × 48 V PSU, 1 × 12 V PSU, 12→5 V buck converter
- hardwired E-stop, main power switch, AC protection / fuses, terminal / distribution hardware
- enclosure fan
- ECO-WORTHY 12 V 18 in actuator (Z)
- existing X/Y limit-switch hardware / connectors
- touchscreen (model TBD)

**2. Planned purchases**
- Kaleinavi 6-channel 433 MHz RF receiver + handheld transmitter
- Cytron MD13S motor driver
- ~1 kΩ resistors for the GPIO inputs
- incidental wiring / connectors

**3. Deferred / undefined**
- Z limit switches, homing, position feedback
- extrusion / pump control electrics
- touchscreen connection and GPIO pin map

**4. VERIFY before final wiring**
- [ ] Arduino board (Uno vs earlier-documented Mega) and GRBL build / settings
- [ ] Uno serial device name on the Pi (ttyACM0 / ttyUSB0)
- [ ] Y1 / Y2 share the Y STEP/DIR command
- [ ] which 48 V PSU feeds which driver
- [ ] X/Y limit switches wired to the Uno; GRBL limit / homing settings
- [ ] protective earth / bonding
- [ ] 12 V PSU capacity (actuator stall + fan + RF + buck)
- [ ] 12→5 V buck continuous current (Pi + touchscreen + Uno)
- [ ] every Kaleinavi COM/NO output potential-free (voltage + continuity)
- [ ] Kaleinavi channel modes (momentary for Z UP / Z DOWN)
- [ ] MD13S PWM held low while the Pi boots / is unpowered
- [ ] E-stop removes power from the Pi as well (cold boot after E-stop)
- [ ] touchscreen model, interface, power
