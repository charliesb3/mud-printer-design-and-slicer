# Milestone 1 Acceptance Test

Physical machine test procedure for verifying the Pi interface against the
Milestone 1 acceptance criteria.

Run this test with the Pi connected to the Arduino Mega running GRBL.
The physical machine should be in a safe, idle state.
The physical hardwired E-stop must be functional and within reach before any
motion tests are attempted.

---

## Prerequisites

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
