# X / Y Steps per mm — Calculation

This document records the mechanical derivation of GRBL `$100` (X steps/mm) and
`$101` (Y steps/mm) for the current drivetrain, together with the parts of the
calculation that require physical verification before the resulting value is
written to the Arduino.

The Pi interface never modifies GRBL settings. Any change to `$100` / `$101`
must be made directly on the Arduino (for example through UGS) by an operator.

---

## Current drivetrain (reported by the user)

- Arduino Uno running GRBL
- Three DM556T stepper drivers (X, Y-left, Y-right)
  - Y-left and Y-right share the Y step/direction signal
- NEMA 23 stepper motors (1.8° / 200 full steps per motor revolution)
- 20 : 1 gearboxes between motor and sprocket shaft
- 12-tooth sprockets on the output shaft
- ANSI #25 roller chain **(assumed; verify physically)**
- All DM556T DIP switches currently **DOWN** (microstep setting **unverified**)

The X-axis and the Y-axis use identical drivetrains, so `$100` and `$101`
should carry the same value.

---

## Formula

Given

    M  = microstep setting on the DM556T (microsteps per full step)
    S  = motor full steps per revolution        = 200
    G  = gearbox reduction (motor : output)     = 20
    T  = sprocket teeth                         = 12
    P  = chain pitch (mm/tooth)                 = 6.35 (ANSI #25, 0.25 in)

the steps per millimetre at the sprocket shaft is

    steps/mm = (S × G × M) / (T × P)
             = (200 × 20 × M) / (12 × 6.35)
             = (4000 × M) / 76.2
             = 52.4934 × M

---

## Table of candidate values

| DM556T microstep M | steps/mm (X and Y) |
|---:|---:|
| 1 (full step)      |    52.4934 |
| 2                  |   104.9869 |
| 4                  |   209.9738 |
| 5                  |   262.4672 |
| 8                  |   419.9475 |
| 10                 |   524.9344 |
| 16                 |   839.8950 |
| 20                 |  1049.8688 |
| 25                 |  1312.3360 |
| 32                 |  1679.7900 |

Only one of these rows is correct for the machine. The row is determined by
the DIP-switch position on the DM556T, which is **not inferred by this
document.** All DIP switches reportedly **DOWN** is not a reliable indicator
by itself — different DM556T revisions label their microstep tables
differently, and some label the "all down" row as `NC` / undefined.

---

## Parts that must be physically verified

The following items are inputs to the formula above. Each is listed with the
assumption used and the way to confirm it on the machine.

1. **DM556T microstep (M) — UNVERIFIED**
   - Required source: the label on top of the DM556T itself.
   - Record the SW5–SW8 positions, then look up the row in the microstep
     table printed on that specific driver.
   - All three drivers should match (X, Y-left, Y-right).

2. **Chain pitch — ASSUMED #25 (0.25 in / 6.35 mm)**
   - Confirm by measuring the pitch of the installed chain, or from the
     chain packaging / stamping, or from the sprocket spec sheet.

3. **Sprocket tooth count — ASSUMED 12**
   - Confirm by counting teeth on an installed sprocket.

4. **Gearbox reduction — ASSUMED 20 : 1**
   - Confirm from the gearbox label / spec sheet.

5. **Motor full-step angle — ASSUMED 1.8°**
   - Standard NEMA 23 value. Confirm from motor label if any doubt.

6. **Direction / invert — NOT COVERED HERE**
   - `$3` (direction invert mask) must match the physical build so that
     positive G-code motion is positive machine motion. This is a separate
     verification during the first jog test.

---

## Independent verification procedure (recommended)

After writing a candidate `$100` / `$101` value to the Arduino, measure the
actual axis displacement for a small commanded motion:

1. Mark the current axis position with a reference against a fixed point
   on the frame.
2. Jog a small, well-defined distance (for example 10 mm, well inside the
   usable travel).
3. Measure the actual displacement with a tape or caliper.
4. Compute the correction factor
       correction = commanded_mm / measured_mm
   and multiply the current `$100` / `$101` by that factor.

Repeat until commanded and measured agree to acceptable precision.

If the measured displacement differs from the commanded distance by a factor
near one of the ratios between rows in the table above (for example a factor
of 2× or 0.5×), that strongly suggests the microstep setting assumed in the
calculation is wrong.

---

## GRBL settings to write

Once the correct value is confirmed, write it directly to the Arduino. Example
UGS console commands (do NOT send these through the Pi interface — the Pi
interface never writes GRBL settings):

    $100 = <steps/mm from the correct row of the table>
    $101 = <same value>

Also review but do not blindly change:

- `$3` direction invert mask (so positive G-code motion matches positive
  physical motion on both axes).
- `$110` / `$111` axis maximum rate (mm/min). The first motion test uses
  a conservative value well below this.
- `$120` / `$121` axis acceleration.

Do NOT enable soft limits (`$20`) or homing (`$22`) until homing switches
are physically installed, wired, and verified. The nominal 120 × 120 inch
machine envelope is NOT a verified travel limit.

---

## Record of chosen value

Once the microstep setting and the verification measurement are complete,
record the final values here (append; do not overwrite past entries):

| Date | `$100` (X) | `$101` (Y) | Microstep M | Verified by |
|---|---|---|---|---|
| _not yet set_ |  |  |  |  |
