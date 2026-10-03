# GRBL Configuration Capture Checklist

Before connecting the Pi interface to the physical machine for the first time,
complete this checklist to preserve the existing GRBL configuration.

The current GRBL configuration ($$) is the known-working baseline for this
machine. It must be preserved and never silently overwritten.

---

## Step 1 — Connect UGS (existing working setup)

Connect your computer to the Arduino Mega via USB using Universal G-code Sender,
exactly as you do now for normal printing. Confirm GRBL connects and the machine
responds.

---

## Step 2 — Capture $$ (all settings)

In the UGS console, send:

    $$

Copy the entire output to a file. Save it as:

    arduino-grbl/grbl-settings-YYYY-MM-DD.txt

This is the authoritative baseline. Every GRBL setting is recorded here.

---

## Step 3 — Capture $# (coordinate offsets)

In the UGS console, send:

    $#

Copy the output to the same file or a companion file. This captures work
coordinate offsets (G54–G59, G28, G30, TLO, PRB).

---

## Step 4 — Capture $I (build info)

In the UGS console, send:

    $I

Record the GRBL version string. Confirm which GRBL version and variant is
running on the Arduino.

---

## Step 5 — Record machine-specific observations

In the same file, note:

- Which USB port the Arduino appears on (e.g., /dev/cu.usbmodem1101 on Mac,
  /dev/ttyUSB0 on Pi)
- Baud rate confirmed working (almost certainly 115200)
- Any observed GRBL alarm or error codes from normal use
- The approximate step/mm values for X and Y ($100, $101)
- The maximum travel limits if set ($130, $131)
- Whether homing ($22) is enabled or disabled
- Whether soft limits ($20) are enabled

---

## Step 6 — Commit the captured settings

Add the captured file to the arduino-grbl/ directory and commit it to Git.

    git add arduino-grbl/grbl-settings-YYYY-MM-DD.txt
    git commit -m "Capture GRBL $$ baseline settings"

---

## Step 7 — Verify before first Pi connection

Before connecting the Pi interface to the Arduino:

- Confirm the Pi interface .env file uses the correct SERIAL_PORT and BAUD_RATE
- Confirm JOG_SPEED_MM_MIN is set to 0 (jogging disabled) for first connection
- Confirm the physical E-stop is functional and within reach
- Confirm the machine is in a safe, idle state (not mid-job)

---

## Important Reminders

The Pi interface communicates with GRBL using the standard serial protocol.
It does not modify GRBL settings.

The Pi interface never sends $$= or $N= commands unless the user explicitly
asks for that capability in the future.

If GRBL settings appear to have changed unexpectedly, compare against the
captured baseline file.

The physical hardwired E-stop remains the real emergency stop.
Software controls are not a substitute.
