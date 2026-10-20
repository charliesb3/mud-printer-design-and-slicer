# Control Box Hardware — Current Baseline

Reference for the REAL electrical interfaces of the printer's control box.
Consult it before any software work that touches the serial link, GPIO, Z,
the wireless remote, limits, power-loss behaviour or extrusion signals.
Established 2026-10-14; audited 2026-10-14 (implementation
consequences strengthened); 2026-10-20: the Pi GPIO pin map, Kaleinavi /
MD13S / Uno / display connections are PHYSICALLY WIRED and recorded below as
authoritative. Items marked **VERIFY** are current understanding, not
confirmed on the hardware — never treat them as facts in code.

**The authoritative Raspberry Pi pin map is § "Raspberry Pi GPIO pin map
(wired)" below.** Other documents refer to it; they do not copy it.

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

HARDWIRED MUSHROOM E-STOP (120 VAC mains side) — separate, independent safety layer
```

- The Pi is **not** the real-time X/Y motion controller (that is GRBL) and
  **not** the safety system (that is the hardwired E-stop).
- The Pi is the **sole command source for Z**. Every Z request — touchscreen,
  laptop browser, RF remote — is an INPUT to Pi software, which alone decides
  and drives the MD13S.
- The RF remote is an **operator-input device only**: handheld → Kaleinavi
  receiver → six dry contacts → Pi GPIO → Pi software. It never drives Z (or
  anything else) directly.
- Nothing in software, the Pi or the RF remote is safety-rated.

---

## Controllers

| Device | Role | Connection |
|---|---|---|
| Raspberry Pi 4 Model B | supervisory controller + UI host (pi-interface) | regulated 5 V from the 12→5 V buck |
| Arduino Uno running GRBL | dedicated real-time X/Y motion controller | Pi USB-A → Uno USB-B (serial + the Uno's power) |

Responsibilities (do not blur):
- **Pi** = supervisory controller, UI, remote-input interpretation, sole Z
  command source.
- **Uno / GRBL** = real-time X/Y motion (and the X/Y limits).
- **MD13S** = Z power stage (driven by the Pi only).
- **Kaleinavi** = wireless operator-input device (dry contacts to the Pi).
- **Hardwired mushroom E-stop** = the independent safety layer.

### Pi ↔ Uno connection (wired)

- Raspberry Pi **USB-A** (either black USB 2.0 port) → Arduino Uno **USB-B**.
- The one cable carries (1) GRBL serial communication and (2) the Uno's
  power. **No Pi GPIO is used** for the Uno.
- The MD13S is **not** routed through the Uno, and Z is **not** a GRBL axis.
- pi-interface is the custom replacement for the operator functions of
  Universal G-code Sender; **UGS is not installed on the Pi** (UGS remains
  only the pre-control-box workflow and the tool for the GRBL capture
  checklist on the existing setup).

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
| Y gantry | **CONFIRMED (observed 2026-10-20):** Y1 and Y2 share the same PUL and DIR signals — one logical Y axis from the controller |
| Power | the two 48 V PSUs (**VERIFY** which PSU feeds which driver) |
| Limits | existing X/Y limit-switch hardware/connectors are to connect to the **Uno/GRBL**, not the Pi (current wiring and GRBL `$21`/`$22` state: **VERIFY**, see `pi-interface/docs/grbl-capture-checklist.md`) |

### Arduino Uno → DM556T wiring (observed, authoritative)

Physically observed in the control box 2026-10-20. Terminal names are the
DM556T's own labels: **PUL+** is the step (STEP) input and **DIR+** the
direction input.

| Driver | DM556T terminal | Arduino Uno pin |
|---|---|---|
| X | PUL+ (step) | digital **D4** |
| X | DIR+ (direction) | digital **D5** |
| Y1 and Y2 (both) | PUL+ (step) | digital **D3** |
| Y1 and Y2 (both) | DIR+ (direction) | digital **D6** |
| all three | signal returns (PUL− / DIR−) | commoned → Arduino **GND** |

- Y1 and Y2 are driven in parallel from the same two Uno pins: they cannot be
  commanded independently (no separate Y2 / auto-squaring axis).
- Driver ENA terminals and limit-switch pins: not part of this observation.

**DISCREPANCY with stock GRBL 1.1 (Uno / ATmega328P default pin map) —
UNRESOLVED, do not "fix" either side by assumption.** Stock GRBL 1.1 uses:
step X **D2** / Y **D3** / Z **D4**; direction X **D5** / Y **D6** / Z **D7**.

| Signal | Observed | Stock GRBL 1.1 | Match |
|---|---|---|---|
| X step | D4 | D2 (D4 = **Z** step) | **NO** |
| X dir | D5 | D5 | yes |
| Y step | D3 | D3 | yes |
| Y dir | D6 | D6 | yes |

Under STOCK GRBL 1.1 with this wiring, X-axis moves would pulse D2 (not
connected) and the X driver would step only on **Z** moves, with its
direction taken from the X direction pin. XY-only jobs (no Z words) and X
jogs would then not move X. Because the machine has moved and printed, one
of these must be true — **VERIFY before any Pi-driven motion**: the
installed firmware is a modified build with X step remapped to D4 (or with
X/Z swapped), the wiring was changed since the machine last ran, or the
observation is mistaken. Check: the installed GRBL build / `cpu_map`
(source or `$I`), and a low-speed single-axis X jog with the operator at
the E-stop.

## Z axis

| Item | Detail |
|---|---|
| Actuator | ECO-WORTHY 12 V linear actuator, 18 in stroke, 330 lb / 1500 N, two-wire reversible DC (polarity = direction) |
| Driver | Cytron MD13S (wired), 12 V motor power from the 12 V PSU; motor outputs → the actuator |
| Control | Pi 3.3 V GPIO, wired **directly** (not via the Uno): MD13S **PWM ← Pi physical 12 / GPIO18**, MD13S **DIR ← Pi physical 16 / GPIO23**, MD13S **GND → Pi signal-ground bus**; MD13S **5 V control-header pin: UNUSED — DO NOT CONNECT** |
| Command path | UI or remote → Pi software → GPIO18 / GPIO23 → MD13S → actuator; the Pi is the only command source |
| End of travel | actuator's internal end-of-travel protection only |
| Limits / homing / position feedback | **deferred** — none in the new architecture yet |

Implementation consequences (for coding agents):
- **Open-loop.** The Pi can command direction, PWM duty and duration, but
  cannot know Z position. Do not write code that assumes a known Z height,
  homing, or closed-loop positioning. Z position / homing hardware is
  **deferred / TBD**.
- **Dead-man / time-bounded.** Every Z motion command must stop on its own:
  held-button motion ends when the input (UI heartbeat or remote contact) stops
  being refreshed, as for the X/Y jog; any commanded move has a maximum
  duration. Stopping = PWM duty 0.
- **Default = STOP (required design item, not yet solved — Z is NOT
  safely commissioned until it is finalized).** The MD13S must be
  at STOP whenever the Pi is booting, shutting down, crashed, or has lost
  control. Do NOT assume Pi GPIO boot / reset states make this safe (pins
  differ in default pull and float briefly during boot), and note that
  hardware PWM generated by the SoC can keep running after the controlling
  process dies. Needed before Z is wired:
  - hardware: a pull-down on the MD13S PWM input so an undriven line means
    STOP (value per the MD13S input spec — **TBD**);
  - software: drive PWM to 0 at service start, on exit / exception / shutdown
    (systemd stop), and on any loss of the dead-man refresh;
  - a loss-of-control mechanism that does not depend on the Z process being
    alive (e.g. PWM that stops when the process dies, or a watchdog) —
    **mechanism TBD**.
- **Remote Z UP / Z DOWN** must be momentary channels (receiver mode
  **VERIFY**). UP and DOWN active at the same time — or any contradictory /
  unexpected input combination — resolves to **STOP / no motion**, never to
  either direction. Inputs are debounced.
- Current print jobs are XY-only and Z stays MANUAL during printing (decided
  2026-10-14, `PROJECT_OVERVIEW.md` → "Current machine execution"); jobs
  carry layer / Z only as metadata. Using this Z hardware path within prints
  (automatic Z) is future work.

## Wireless remote (wired)

| Item | Detail |
|---|---|
| Hardware | Kaleinavi 6-channel 433 MHz industrial RF receiver + handheld transmitter |
| Receiver power | 12 V, from the 12 V PSU (separate from the Pi; nothing from the receiver's supply reaches the Pi) |
| Outputs | six potential-free (dry) relay contacts, used **only as dry switches**, read **directly** by Pi GPIO |
| Input wiring | each channel NO → ~1 kΩ series resistor → its Pi GPIO; CH1–CH6 COM commoned onto the Pi signal-ground bus; **NC contacts unused**; Pi internal 3.3 V pull-up; relay closure pulls the GPIO to **Pi GND** |
| Isolation | no isolated input board |

| Channel | Contacts | Pi input | Function |
|---|---|---|---|
| CH1 | NO → ~1 kΩ | physical **11** / GPIO17 | REMOTE STOP |
| CH2 | NO → ~1 kΩ | physical **13** / GPIO27 | Z UP |
| CH3 | NO → ~1 kΩ | physical **15** / GPIO22 | Z DOWN |
| CH4 | NO → ~1 kΩ | physical **29** / GPIO5 | extrusion pause / resume |
| CH5 | NO → ~1 kΩ | physical **31** / GPIO6 | AUX 1 (reserved) |
| CH6 | NO → ~1 kΩ | physical **33** / GPIO13 | AUX 2 (reserved) |
| CH1–CH6 COM | — | signal-ground bus → Pi physical **9** (GND) | |

The function per channel is the wired assignment (2026-10-20); it is no
longer "tentative". Software still takes it from the hardware mapping layer
(see "GPIO assignment").

- **Absolutely no 12 V on any Pi GPIO.** Before connecting, **VERIFY** with
  voltage and continuity measurements (meter) that every COM/NO output is
  genuinely potential-free (no 12 V or receiver ground on the contacts).
- **VERIFY** contact behaviour and modes per channel: contact rating,
  momentary vs latching / toggle — Z UP / Z DOWN **must** be configured
  momentary on the receiver.
- Input logic (all six): **ACTIVE LOW** with the Pi's 3.3 V pull-up —
  contact open = HIGH = released; contact closed to Pi GND = LOW = pressed.
- Fail-safe reading: with pull-ups, an open contact (released, receiver
  unpowered, broken wire) reads HIGH = not pressed → no motion. Consequence:
  REMOTE STOP cannot be sent when the receiver or its wiring has failed —
  acceptable only because it is not a safety function.
- Z from the remote: handheld → Kaleinavi receiver → Pi GPIO → Pi software →
  MD13S → actuator. Software must debounce inputs and handle UP+DOWN pressed
  together safely.
- **REMOTE STOP is a software / operational stop, not a safety-rated
  E-stop.** UI and code must never present it as an emergency stop. Its exact
  effect is to be defined in software. The actual safety layer is the
  hardwired mains-side mushroom E-stop.

## Power

```
120 VAC → hardwired physical E-STOP → main power switch
        → 48 V PSU ×2   (X/Y DM556T drivers)
        → 12 V PSU      → enclosure fan (always on whenever 12 V control power is on)
                        → Kaleinavi RF receiver
                        → Cytron MD13S → ECO-WORTHY Z actuator
                        → 12→5 V buck → regulated 5 V → Raspberry Pi 4 (→ USB → Arduino Uno)
                                                      → 7" DSI display (5 V + GND)
```

- The Pi 4 runs on **regulated 5 V**. Its physical power termination may be
  USB-C or direct 5 V / GND on the header, depending on the final
  installation (**TBD**) — power wiring is separate from, and must not be
  confused with, the GPIO signal wiring above.
- Prefer the Pi and the display powered **in parallel** from the regulated
  5 V supply (where the display's power input supports it), rather than
  treating the display as a GPIO-header peripheral.
- 12 V PSU sizing (discussion, **not a confirmed specification**): roughly
  10 A minimum, ~15 A / 180 W preferred if a new PSU is selected — mainly
  margin for actuator start-up / stall transients so the Pi's supply does
  not sag. Confirm against the actual PSU, the actuator's peak / stall
  current and the buck rating before treating any figure as the spec.

- Existing AC protection / fuses and terminal / distribution hardware stay.
- Final DC branch protection (fusing of the 12 V branches: MD13S / actuator,
  RF receiver, fan, buck) is **VERIFY / TBD**.
- The hardwired mains-side mushroom E-stop is independent of the Pi, software
  and the RF remote and is the actual safety stop.
- As described, the E-stop and main switch are upstream of **every** PSU, so
  pressing the E-stop also powers down the Pi and (via USB) the Uno: after an
  E-stop the whole control system cold-boots and in-memory job state is lost
  (relevant to job recovery / "start at lift N"). **VERIFY**.
- **VERIFY** protective earth / bonding during final wiring.
- **VERIFY** the 12→5 V buck's continuous current covers the Pi 4 + the
  7" DSI display + the USB-powered Uno + any small Pi peripherals, against
  the actual converter rating.
- **VERIFY** the 12 V PSU rating covers actuator peak/stall current + fan +
  RF receiver + buck simultaneously.

## Touchscreen

Identified 2026-10-20: a **7-inch 800×480 DSI** display (earlier: model /
interface unknown).

- Video: Pi's dedicated **DSI connector** → display DSI connector, with the
  appropriate compatible FFC / FPC ribbon. **Do not splice or glue the
  supplied DSI ribbons together** — use a compatible DSI cable / connector
  arrangement.
- Power: the display needs 5 V + GND, preferably in parallel with the Pi from
  the regulated 5 V supply (see Power).
- DSI does **not** use any of the GPIO signal pins in the pin map.
- The UI is the same responsive web UI on touchscreen and laptop
  (pi-interface decision); the display is not a GPIO-controlled peripheral.

## Extrusion

Electrically **deferred / undefined**. The pump is still controlled
independently today. Future control of the pump is anticipated (exact
mechanism not defined — **VERIFY with the user**), so remote and software
concepts reserve **extrusion pause / resume**, but no signal, wiring or
implementation is defined. Do not invent one.

## Raspberry Pi GPIO pin map (wired)

**AUTHORITATIVE — physically wired by the operator (2026-10-20). Do not
renumber, "improve" or re-derive these assignments.** Software uses BCM
numbers; documentation always shows BOTH the BCM number and the physical
40-pin-header number.

| Function | BCM | Physical Pi pin | Direction | External connection |
|---|---:|---:|---|---|
| REMOTE STOP | GPIO17 | 11 | input, pull-up, active-low | Kaleinavi CH1 NO, through ~1 kΩ series resistor |
| Remote Z UP | GPIO27 | 13 | input, pull-up, active-low | Kaleinavi CH2 NO, through ~1 kΩ series resistor |
| Remote Z DOWN | GPIO22 | 15 | input, pull-up, active-low | Kaleinavi CH3 NO, through ~1 kΩ series resistor |
| Z PWM | GPIO18 | 12 | output (hardware-PWM-capable) | Cytron MD13S PWM |
| Z direction | GPIO23 | 16 | output | Cytron MD13S DIR |
| Extrusion pause / resume | GPIO5 | 29 | input, pull-up, active-low | Kaleinavi CH4 NO, through ~1 kΩ series resistor |
| Remote AUX 1 | GPIO6 | 31 | input, pull-up, active-low | Kaleinavi CH5 NO, through ~1 kΩ series resistor |
| Remote AUX 2 | GPIO13 | 33 | input, pull-up, active-low | Kaleinavi CH6 NO, through ~1 kΩ series resistor |
| Signal ground | GND | 9 | — | common signal-ground bus (Kaleinavi CH1–CH6 COM, MD13S control GND) |

- **Z DOWN = physical pin 15 / GPIO22 — CONFIRMED by the operator
  (2026-10-20).** No open question remains on that line.
- The Pi **signal-ground bus** (Kaleinavi CH1–CH6 COM + MD13S control GND →
  Pi physical 9) is a signal reference only. It is **not** protective earth
  and **not** the Kaleinavi's 12 V supply terminals — do not conflate them.

- **Kaleinavi CH6 / AUX 2 is PHYSICAL PIN 33 / GPIO13.** An earlier
  conversational ASCII diagram showed physical pin 27 — that was an ERROR.
  **Nothing in this control scheme uses physical pin 27** (an ID-EEPROM pin
  on the Pi 4 header).
- **Absolutely no 12 V on any Pi GPIO.**
- The Uno uses USB (no GPIO); the display uses DSI (no GPIO); X/Y limits are
  on the Uno (no GPIO).

### Software consequences (for pi-interface, when hardware integration is implemented)

- GPIO numbers live in **one hardware-mapping / configuration layer** — never
  scattered as magic numbers. BCM numbering in code.
- The six remote inputs: Pi pull-ups, **active low**, **debounced**. Before
  software has configured the pull-ups, the SoC's default pulls apply (on a
  Pi 4 GPIO13 / 17 / 22 / 27 default to pull-DOWN, which would read as
  "pressed"): never act on an input until it has been configured and has
  first been seen released (HIGH).
- Z remote control is **dead-man / momentary**: motion exists only while a
  valid held command is being received.
- **UP + DOWN together (or any contradictory combination) = STOP**, never
  either direction.
- Z PWM goes to **0** on release, timeout, exception, application shutdown
  and loss of the dead-man refresh. **Startup state = Z STOP.** (The
  hardware / loss-of-control default-STOP mechanism is still to be
  finalized — see Z axis.)
- Z is open-loop: never infer an absolute Z position.
- **REMOTE STOP** stops software-controlled machine activity (GRBL / X/Y,
  Z, and later extrusion as defined) — but is **never** represented as
  equivalent to the physical E-stop.
- **AUX 1 / AUX 2: reserved** (no function).
- **Extrusion pause / resume** has a physical INPUT, but the pump-control
  OUTPUT / interface remains electrically undefined — do not invent it.
- X/Y limits remain Arduino / GRBL responsibilities, not Pi GPIO.

---

## Superseded — do NOT reintroduce

| Not in the architecture | Replaced by |
|---|---|
| D2-ZFZ (previously considered) | Cytron MD13S driven by the Pi |
| a separate Pi relay board for Z | MD13S PWM/DIR from Pi GPIO |
| MD13S driven through the Uno | MD13S wired directly to Pi GPIO18 / GPIO23 |
| UGS on the Pi | pi-interface (UGS is not installed on the Pi) |
| Kaleinavi CH6 on physical pin 27 (erroneous diagram) | CH6 / AUX 2 = physical 33 / GPIO13 |
| the existing SRDKG RF Z controller | Kaleinavi receiver → Pi → MD13S (Pi is the sole Z command source) |
| isolated input board for the RF contacts | direct GPIO with pull-ups + ~1 kΩ series (none planned) |
| Z through GRBL | not the current architecture (Z is Pi-commanded) |

---

## BOM / status

**1. Owned / installed** (signal wiring per the pin map, 2026-10-20)
- Raspberry Pi 4 Model B
- Arduino Uno with GRBL (X/Y), on Pi USB
- 3 × DM556T stepper drivers (X, Y1, Y2) + X/Y steppers
- 2 × 48 V PSU, 1 × 12 V PSU, 12→5 V buck converter
- hardwired E-stop, main power switch, AC protection / fuses, terminal / distribution hardware
- enclosure fan
- ECO-WORTHY 12 V 18 in actuator (Z)
- existing X/Y limit-switch hardware / connectors
- 7" 800×480 DSI touchscreen
- Kaleinavi 6-channel 433 MHz RF receiver + handheld transmitter
- Cytron MD13S motor driver
- ~1 kΩ series resistors on the six remote inputs

**2. Still needed**
- a compatible DSI cable / connector arrangement (do not splice ribbons)
- possibly a new 12 V PSU (see Power sizing — not confirmed)
- incidental wiring / connectors, DC branch protection

**3. Deferred / undefined**
- Z limit switches, homing, position-feedback hardware
- extrusion / pump control electrics (output / interface undefined)
- MD13S default-STOP hardware (pull-down value) and loss-of-control mechanism

**4. VERIFY before final wiring**
- [ ] Arduino board (Uno vs earlier-documented Mega) and GRBL build / settings
- [ ] **X step on Uno D4 vs stock GRBL 1.1 D2** (D4 = stock Z step): firmware pin map or wiring — resolve before Pi-driven motion
- [ ] Uno serial device name on the Pi (ttyACM0 / ttyUSB0)
- [x] ~~Y1 / Y2 share the Y STEP/DIR command~~ — observed 2026-10-20: both on Uno D3 (PUL+) / D6 (DIR+)
- [ ] which 48 V PSU feeds which driver
- [ ] X/Y limit switches wired to the Uno; GRBL limit / homing settings
- [ ] protective earth / bonding
- [ ] 12 V PSU capacity (actuator peak / stall + fan + RF + buck); ~10 A min / ~15 A preferred is an estimate only
- [ ] actuator peak / stall current
- [ ] 12→5 V buck continuous current (Pi 4 + 7" DSI display + Uno + small peripherals)
- [ ] Pi 5 V power termination (USB-C vs header 5 V / GND)
- [ ] final DC branch protection (12 V branches)
- [ ] every Kaleinavi COM/NO output potential-free (voltage + continuity, by meter)
- [ ] Kaleinavi contact rating + modes (momentary for Z UP / Z DOWN)
- [ ] MD13S defaults to STOP while the Pi boots / shuts down / crashes / is unpowered
- [ ] E-stop removes power from the Pi as well (cold boot after E-stop; matters for job recovery)
- [ ] display power input accepts parallel 5 V from the regulated supply
- [x] ~~touchscreen model, interface~~ — 7" 800×480 DSI (2026-10-20)
- [x] ~~Pi GPIO pin numbers~~ — wired, see the pin map (2026-10-20)
- [ ] extrusion interface (when extrusion control is taken up)
- [ ] Z position / homing hardware (when taken up)
