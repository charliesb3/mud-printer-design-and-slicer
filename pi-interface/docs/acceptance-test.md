# Control-Box Commissioning + Acceptance Test

Physical procedure for bringing the wired control box into service with
pi-interface, followed by the Milestone 1 functional checks (Sections 1–7).
Nothing here has been performed yet — record date, results and the values
chosen for each step. Do not skip ahead: each step assumes the previous ones
passed. Stop at the first unexpected result.

Throughout:
- the hardwired mains-side mushroom **E-stop** is the only safety stop; it is
  functional and within reach before any step that can move anything;
- the RF **REMOTE STOP** is an operational software stop, never an E-stop;
- wiring reference: `../../CONTROL_BOX_HARDWARE.md`; installation:
  `deployment.md`.

---

## Step 1 — Pi + display boot

- [ ] 5 V supply measured at the Pi and the display under load (no
      under-voltage warning: `vcgencmd get_throttled` → `throttled=0x0`).
- [ ] The DSI display shows the desktop; touch works.
- [ ] `pinctrl get 18,23` → `op dl` (config.txt boot STOP, deployment §2).

## Step 2 — Service + UI

- [ ] `systemctl status mudprinter` → active (running); enabled at boot
      (power-cycle once and check it comes back on its own).
- [ ] The kiosk opens the UI full-screen on the touchscreen; a laptop on
      the same network reaches `http://mudprinter.local:8000`.
- [ ] Hardware / Commissioning shows backend `gpio`, poll loop running,
      no backend error; Manual Z shows "disabled: Z_PWM_DUTY not set".
- [ ] `sudo systemctl restart mudprinter` → the UI reconnects by itself.

## Step 3 — Arduino detected, read-only GRBL capture

- [ ] `ls -l /dev/serial/by-id/` lists the Uno; create `/dev/mudpr-grbl`
      (deployment §5) and set it in `.env`.
- [ ] Read-only capture of `$I`, `$$`, `$#`, `$G` (`grbl-capture-checklist.md`)
      BEFORE any motion; file committed under `arduino-grbl/`.
- [ ] `$I` shows a stock GRBL 1.1 Uno build (standard pin map: X step D2 —
      the rewired X). Anything else: stop and report.
- [ ] UI: CONNECTED, GRBL Idle (or Alarm if homing is enabled — do not
      unlock yet), version shown; unplug / replug USB → Disconnected /
      reconnects (event log shows both).

## Step 4 — Six RF inputs, NO MOTION

Motion stays disabled: `JOG_SPEED_MM_MIN=0`, `Z_PWM_DUTY=0`. Before this
step: meter check that every Kaleinavi COM/NO pair is potential-free (no 12 V,
no receiver ground) and that Z UP / Z DOWN are configured MOMENTARY.

Open Hardware / Commissioning. All six rows: Level HIGH, RELEASED, armed.
For each handheld button, press and hold, then release:

| Button | Row that must change (only this one) | Level held | Software |
|---|---|---|---|
| CH1 | REMOTE STOP — GPIO17 / pin 11 | LOW | PRESSED → REMOTE STOP LATCHED banner |
| CH2 | Z UP — GPIO27 / pin 13 | LOW | PRESSED; event "Z UP refused: manual Z is disabled" |
| CH3 | Z DOWN — GPIO22 / pin 15 | LOW | PRESSED; refused as above |
| CH4 | Extrusion — GPIO5 / pin 29 | LOW | PRESSED; event only, nothing else |
| CH5 | AUX 1 — GPIO6 / pin 31 | LOW | PRESSED; event only |
| CH6 | AUX 2 — GPIO13 / pin 33 | LOW | PRESSED; event only |

- [ ] Each button changes exactly its own row, and back to RELEASED on release.
- [ ] REMOTE STOP stays LATCHED after the button is released; "Hold to clear"
      is refused while CH1 is held, works after release, and nothing moves.
- [ ] Z UP + Z DOWN together: both rows PRESSED, Z output stays STOP.
- [ ] Walk the remote to the farthest operating position; repeat CH1 and
      CH2. Note any dropouts (a dropout stops Z — by design).
- [ ] Restart the service while holding CH2: the row shows "held at startup
      — release to arm" and no Z request is made.

## Step 5 — MD13S logic + default STOP, ACTUATOR DISCONNECTED

Disconnect the actuator from MD13S motor outputs A/B. Meter between MD13S
PWM and the signal-ground bus (and DIR likewise). This step also settles the
pull-down COMMISSIONING BLOCKER (deployment §7).

- [ ] External pull-down fitted on MD13S PWM (proposed 10 kΩ to signal GND —
      record the value actually fitted).
- [ ] Pi unpowered, MD13S powered: PWM < 0.5 V; MA / MB LEDs off.
- [ ] Power-on and through the whole boot (watch continuously): PWM stays
      < 0.5 V; LEDs off.
- [ ] Service stopped (`systemctl stop`): PWM < 0.5 V.
- [ ] Set `Z_PWM_DUTY=0.3` (provisional, for this test only) and restart.
      Hold UI Z UP: MA (or MB) LED on, PWM average ≈ duty × 3.3 V, DIR level
      as shown in the Z diagnostics; release → LED off within ~0.3 s.
      Z DOWN: the other LED. Remote CH2 / CH3: the same.
- [ ] Unplug the laptop's network while holding UI Z UP: LED off within
      ~0.3 s (dead-man).
- [ ] Press REMOTE STOP while holding Z UP: LED off at once; latched.
- [ ] Hold Z UP longer than `Z_MAX_RUN_S`: stops by itself.
- [ ] Hold remote Z UP and Z DOWN together: LEDs off; event "contradictory
      Z requests"; Z stays stopped until both are released.
- [ ] While holding Z UP (LED on): `sudo systemctl kill -s KILL mudprinter`
      → LED off within ~1 s (ExecStopPost), service restarts with Z STOP.
- [ ] Do NOT use the MD13S on-board MA / MB test buttons with the actuator
      connected: they drive the motor directly, outside all software control.

Only when every item passes: default STOP is commissioned; record the
pull-down value in `CONTROL_BOX_HARDWARE.md`.

## Step 6 — Z actuator test

Reconnect the actuator. Clear the Z travel; E-stop in reach.

- [ ] Short holds (≤ 1 s) of UI Z UP: the actuator moves UP. If it moves
      down: set `Z_DIR_INVERT=true`, restart, re-check.
- [ ] Z DOWN, remote CH2 / CH3: correct directions; release stops.
- [ ] Choose the fixed manual duty (`Z_PWM_DUTY`) — record it; the operator
      has no speed control.
- [ ] Watch the 12 V and 5 V rails during actuator start / reversal (no Pi
      brown-out).
- [ ] The actuator's internal end-of-travel stops it at both ends; the
      software has no Z position and never shows one.

## Step 7 — Very low speed single-axis XY jog

- [ ] Machine clear; operator at the E-stop. `JOG_SPEED_MM_MIN` very low
      (e.g. 100) and restart.
- [ ] X+ short hold: X moves in + (the rewired D2 step); X−; Y+ / Y− (both
      Y drivers move together).
- [ ] Release stops within one jog interval; closing the browser while
      holding stops within 0.3 s (Section 2).
- [ ] REMOTE STOP while jogging: motion stops at once, latched, jog refused
      until cleared.

## Step 8 — Limits (when ready)

Only once the X/Y limit switches are wired to the Uno and their GRBL settings
are decided (`$21`, `$22`, `$5` from the capture — never guessed).

- [ ] With jog at low speed, trigger each switch by hand: `Pn:` shows the
      pin (Advanced → Input pins); with hard limits on, GRBL alarms.
- [ ] Alarm handling understood: the print origin is cleared after an alarm.

## Step 9 — Air print (no material)

- [ ] Sections 3–6 below with a small exported `.mudjob`, pump off: Set
      origin → Prime → Start (returns to origin) → HOLD / RESUME → one lift
      boundary → confirm → complete.
- [ ] REMOTE STOP mid-job: job HELD at the shown line, GRBL feed hold,
      latched; clear (nothing moves) → RESUME continues from the same line
      (no line repeated or skipped).
- [ ] Manual Z during the job works from the touchscreen and the remote.

## Step 10 — Material print

- [ ] Only after steps 1–9 pass. Small job first; operator at the E-stop,
      pump operated by hand as today.

---

## Milestone 1 functional checks

Run with the Pi connected to the Arduino Uno running GRBL. The physical
machine should be in a safe, idle state. The physical hardwired E-stop must
be functional and within reach before any motion tests are attempted.

### Prerequisites

- Pi interface deployed and running (see deployment.md)
- Arduino connected to Pi via USB
- GRBL settings captured (see grbl-capture-checklist.md)
- JOG_SPEED_MM_MIN set to a known safe value in .env (start conservatively)
- Physical E-stop confirmed functional
- Machine axes confirmed free to move safely for the planned test distances

---

## Section 1 — Connection and State Display

**1.1** Open the interface in a browser. Confirm the page loads without errors.

**1.2** Confirm the interface shows machine state: Idle, Run, Hold, Alarm, or
Disconnected as appropriate.

**1.3** Confirm X and Y machine position displays update when the machine moves.

**1.4** Confirm feed rate display updates during motion.

**1.5** Disconnect the USB cable. Confirm the interface shows "Disconnected"
within a few seconds.

**1.6** Reconnect the USB cable. Confirm the interface reconnects and shows
machine state again without requiring a restart.

---

## Section 2 — Jogging

Before jogging: confirm that the machine is clear to move, the E-stop is
within reach, and a safe travel distance has been identified.

**2.1** Press and hold an X+ jog button. Confirm the machine moves in the X+
direction while the button is held.

**2.2** Release the X+ button. Confirm motion stops promptly.

**2.3** Confirm that motion stops when you release the button, NOT when you
release it and then the server receives a separate stop message.
(The machine must stop within one jog interval after button release,
not after an arbitrary delay.)

**2.4** Test X−, Y+, Y− directions the same way.

**2.5** On a touch device: press and hold a jog button. Confirm motion.
Lift your finger. Confirm motion stops.

**2.6** Simulate browser disconnection while jogging (close the browser tab
or kill Wi-Fi while holding a jog button). Confirm the machine stops
moving within 0.3 seconds (one jog timeout interval). This is the
dead-man / fail-safe jog requirement.

**2.7** Record the jog speed used for testing. Confirm it feels appropriate
for the machine scale. Note whether JOG_SPEED_MM_MIN should be adjusted
before regular use.

---

## Section 3 — Hold, Resume, Stop

**3.1** Start a jog. Press HOLD. Confirm the machine enters hold state.

**3.2** Press RESUME. Confirm motion resumes.

**3.3** Press and hold the STOP button for 2.5 seconds. Confirm the
confirmation animation plays. Confirm STOP triggers after the hold
period completes.

**3.4** Confirm that a quick tap of the STOP button does NOT trigger a stop.

---

## Section 4 — Job Streaming

Prepare a short safe test G-code file (e.g., a small back-and-forth move that
will not take the machine to unsafe positions).

**4.1** Upload the test file using the Upload button. Confirm the file name
and line count appear in the UI.

**4.2** Press START JOB. Confirm the job begins streaming.

**4.3** Confirm the progress bar and current line counter update as the job runs.

**4.4** While the job is running, close the browser tab. Confirm the job
continues running on the machine. (The server owns the job.)

**4.5** Reopen the browser. Confirm the interface reconnects and shows the
current job state and progress.

**4.6** Let the job complete. Confirm the state shows "completed."

**4.7** Test HOLD during a job. Confirm streaming pauses.

**4.8** Test RESUME after HOLD. Confirm streaming resumes from the correct
line (no lines repeated, no lines skipped).

**4.9** Test STOP during a job. Confirm streaming stops and the job state
shows "stopped."

---

## Section 5 — Console

**5.1** Expand the console section. Confirm received GRBL messages appear
in the log.

**5.2** Type `?` in the console input and send. Confirm a status report
appears in the log.

**5.3** Type `$$` and send. Confirm GRBL settings appear in the log.

**5.4** Confirm that diagnostic commands can be sent and responses read
without affecting machine state.

---

## Section 6 — Browser Independence

**6.1** Start a job. While the job is running, close and reopen the browser
multiple times. Confirm the job continues without interruption.

**6.2** Start a job on a phone or tablet browser. Close that browser.
Open the interface on a laptop. Confirm the job state is visible and
the job is still running.

---

## Section 7 — E-Stop Reminder

**7.1** Confirm the interface shows the disclaimer:
"Software controls only — use the physical E-STOP for emergencies."

This is not a functional test. It is a reminder that the software Stop
button is NOT the machine's real emergency stop.

---

## Pass Criteria

All items in Sections 1–7 must pass before Milestone 1 is considered
complete on hardware.

Particular attention should be given to:

- Dead-man jog behavior (2.6): this is a safety-critical requirement
- Server-owned job (4.4): this is a core architectural requirement
- GRBL settings unchanged: run $$ after all tests and confirm it matches
  the baseline captured before testing

---

## Notes

Record the date, Pi firmware version, GRBL version, and jog speed used
during testing.

Record any failures or unexpected behaviors with enough detail to reproduce.

Do not mark Milestone 1 as complete until physical machine tests pass.
