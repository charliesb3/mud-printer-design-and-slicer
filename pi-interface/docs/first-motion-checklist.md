# First X/Y Motion — Operator Checklist

The software is configured for the first short X/Y jog test. This
checklist is the physical commissioning procedure to run before and
during that test. Nothing here enables Z or starts a job.

Pre-commissioning state (confirmed 2026-10-23, software side):

- Controller: `consolidate/trunk` at `a7ff06f`, service `mudprinter`
  active, PID / timestamp recorded in `~/.mudprinter-last-backup`.
- `hardware.backend='gpio'`, LGPIOFactory in use. Z controller
  `available=True`, `command=STOP`, `duty_config=0.0`, `dir_level=LOW`.
- `.env`: `JOG_SPEED_MM_MIN=46` (≈ 0.030 in/s), `JOG_SPEED_MAX_MM_MIN=381`
  (≈ 0.250 in/s), `Z_PWM_DUTY=0`, `HARDWARE_BACKEND=gpio`.
- GRBL `$100=$101=52.493`, `$110=$111=381`, `$130=$131=3048`,
  `$3=0`, `$20=0`, `$22=0`.
- Machine profile: "Mud Printer (nominal, unverified)", 120 × 120 in.

---

## Before power-on

- [ ] Mains to the stepper driver supply is **isolated**.
- [ ] Physical E-STOP tested (not through this software — hardwired).
  Confirmed to cut mains to the drivers.
- [ ] At least 500 mm (20 in) of unobstructed travel on X+, X−, Y+, Y−
  from the current head position.
- [ ] Tape measure or ruler within reach for a measured-jog check.
- [ ] Operator can reach the physical E-STOP without taking eyes off
  the machine.
- [ ] Handheld remote (Kaleinavi, CH1 = REMOTE STOP) within reach.
  CH3 (Z DOWN) button confirmed physically released, not stuck.

## Driver microstep verification (one-time)

- [ ] Read the microstep table printed on top of one DM556T driver.
- [ ] Confirm the SW5–SW8 column labelled "all ON" shows **200 pulses/rev**.
- [ ] If it shows anything else (e.g. 25,000 ppr @ M=125), STOP:
  recompute `$100`/`$101` using
  `arduino-grbl/steps-per-mm-calculation.md` and update via console.
  Correct value = 52.4934 × actual microstep.

## Power-on sequence

- [ ] Pi + Arduino + remote are already powered (same session as this
  checklist unless rebooted — if rebooted, re-verify controller state
  at `http://192.168.1.144:8000`).
- [ ] Energize stepper driver supply.
- [ ] Confirm no driver fault LEDs are lit; motors are powered and
  holding (slight hiss/holding torque).
- [ ] Open the controller UI in a laptop/tablet browser:
  `http://192.168.1.144:8000`.
- [ ] Status bar: badge CONNECTED, state IDLE, X=0 Y=0, feed 0.000 in/s.

## REMOTE STOP physical test (safety-critical)

Do this BEFORE the first jog.

- [ ] On the UI, confirm REMOTE STOP banner is NOT showing and
  `remote_stop.latched=False`.
- [ ] Press and release CH1 on the handheld remote.
- [ ] UI must latch within ~100 ms: red REMOTE STOP banner appears,
  "LATCHED" badge, jog buttons disabled, motion commands refused.
- [ ] Verify the latch cannot be cleared while CH1 is held — hold CH1,
  tap "Clear REMOTE STOP" in UI, expect a refusal notice.
- [ ] Release CH1. Click "Clear REMOTE STOP" in UI. Banner clears.
- [ ] If any step above fails, STOP. REMOTE STOP is a safety mechanism;
  do not proceed to jog testing without it working.

## First X+ jog (tiny, slow, direction check)

- [ ] In the Jog section, confirm "Jog speed" shows **0.030 in/s** and
  the "¼" preset button is highlighted (default on startup).
- [ ] Place a mark on the X-axis frame against a reference feature
  (zip-tie, piece of tape, pencil mark).
- [ ] Press and HOLD X+ for approximately **1 second**, then release.
- [ ] Expected commanded motion: 46 mm/min × 1 s = **0.77 mm** (0.030 in).
- [ ] Observe: did the X axis move in the physical +X direction (per
  your machine convention)?
  - If **yes**: continue.
  - If **no** (motion was in the −X direction): STOP. From the
    controller console (expand "Advanced / Console"), send `$3=1`.
    Repeat the test.
- [ ] Measure actual X displacement from the reference mark. Record it:
  commanded ≈ 0.77 mm, measured = ______ mm.

## Steps/mm correction (if needed)

If measured ≠ commanded within ~10%:

- [ ] Compute correction = commanded / measured.
- [ ] New `$100` = current × correction. Current is 52.493.
- [ ] From the console, send `$100=<new value>` and `$101=<same>`.
- [ ] Repeat the X+ jog test. Measurement should now match command.

Specific ratios worth watching for:

- Measured ≈ 2.206 × commanded → actual steps/mm is 23.805 (old value).
  New `$100` should be 23.805. Likely means the drivetrain parameters
  described in `arduino-grbl/steps-per-mm-calculation.md` are wrong —
  physical measurement wins.
- Measured ≈ 0.008 × commanded → the DM556T microstep is 125, not 1.
  New `$100` = 52.493 × 125 = 6561.6. OR flip DIP switches to full
  step.

## First X−, Y+, Y− jog

Only after the X+ test passes direction + rough measurement:

- [ ] X− short bump, same way. Return head close to original position.
- [ ] Y+ short bump. Observe direction, measure.
- [ ] Y− short bump. Return.

If both Y1 and Y2 drivers are wired correctly, both motors should
rotate together with no twist in the frame. Listen for stall, binding,
or one motor doing the work.

## Dead-man test

- [ ] Press and hold X+ for a few seconds. While holding, physically
  disconnect the Wi-Fi or close the browser tab.
- [ ] Within ~0.3 s, machine should stop. (`JOG_TIMEOUT_S=0.3` in `.env`.)
- [ ] If motion continues past ~0.5 s, STOP everything. There is a
  bug in the dead-man path and we need to fix it before any further
  jogging.

## Slider-change-during-jog test

- [ ] Press and hold X+ with the ¼ preset (~0.030 in/s).
- [ ] While holding, press the ½ preset. Jog speed should increase
  within one jog interval (~0.15 s) without releasing the button.
- [ ] Release.

Preset values at `JOG_SPEED_MAX_MM_MIN=381`:
- ¼ = 0.0625 in/s (95 mm/min)
- ½ = 0.125 in/s (191 mm/min)
- ¾ = 0.1875 in/s (286 mm/min)
- Max = 0.250 in/s (381 mm/min)

(The service startup value is still 46 mm/min / 0.030 in/s per
`JOG_SPEED_MM_MIN`; presets scale from `JOG_SPEED_MAX_MM_MIN`.)

## Software STOP test

- [ ] Start a long X+ jog by press-and-hold.
- [ ] While still jogging, press and HOLD the UI STOP button for the
  full 2.5-second confirmation period.
- [ ] Machine stops; GRBL goes to Alarm or Reset; UI shows the stop
  state. Clear the alarm if any (`$X` in console).
- [ ] A quick tap of STOP (<2.5 s) must NOT trigger a stop.

## After the session

- [ ] De-energize the stepper driver supply.
- [ ] Record the final verified `$100` / `$101` values (if changed) in
  `arduino-grbl/PROJECT_MEMORY.md` → "Record of chosen value" table in
  `arduino-grbl/steps-per-mm-calculation.md`.
- [ ] If anything behaved unexpectedly, note it in `pi-interface/PROJECT_MEMORY.md`
  under Open Questions.

## Rollback — if the session goes wrong

Everything is reversible:

**GRBL settings** — the pre-change values are at
`arduino-grbl/captures/grbl-pre-first-motion-2026-10-23_235310.txt`. To
restore, send through the controller console:

    $100=23.805  $101=23.805
    $110=1500    $111=1500
    $130=3352.8  $131=3200.4

**Pi `.env`** — on the Pi:

    cp ~/mudprinter-controller/pi-interface/.env.pre-first-motion-2026-10-23_235618 \
       ~/mudprinter-controller/pi-interface/.env
    sudo systemctl restart mudprinter

**Machine profile** — in the UI's Machine panel, clear name and extents
and save. Or via WS send `set_machine_profile` with the "Unconfigured
printer" defaults.

**Service rollback (deeper)** — the pre-M4 controller is still available
at `~/mudprinter-controller.backup-2026-10-23_203914`. See
`pi-interface/docs/consolidation-migration.md` → "Reusing a FAILED
tree" and the M4 rollback section.
