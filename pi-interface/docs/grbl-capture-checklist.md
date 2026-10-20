# GRBL Capture Checklist (read-only) + prepared flash procedure

Purpose: record the existing controller's identity and configuration BEFORE
anything is changed. The captured `$$` is the machine's known-working
baseline; it must never be silently overwritten. Nothing in this checklist
writes to the controller.

The control box uses the standard GRBL 1.1 Uno pin map for X/Y (X step D2,
Y step D3, X dir D5, Y dir D6; Z D4 / D7 unused; Z is not a GRBL axis) —
`../../CONTROL_BOX_HARDWARE.md` → "Arduino Uno → DM556T wiring". Machine
size, steps/mm, rates, acceleration, direction inversion, travel, limits and
homing are normal GRBL `$` settings: record them, never guess them.

---

## Step 1 — Connect read-only

Either route is fine; both only READ.

**A. From the Pi (preferred once the control box is wired):**

    sudo systemctl stop mudprinter                       # frees the serial port
    cd ~/mudprintersoftware/pi-interface
    .venv/bin/python tools/grbl_capture.py /dev/mudpr-grbl   # or /dev/ttyACM0 / /dev/ttyUSB0
    sudo systemctl start mudprinter

`tools/grbl_capture.py` sends only `?`, `$I`, `$$`, `$#`, `$G` (a hard
whitelist; tested) and writes `grbl-capture-<timestamp>.txt`. Opening the
port resets the Uno (normal for Arduino USB serial): GRBL restarts from its
EEPROM settings; nothing changes.

**B. From UGS on the existing computer (pre-control-box workflow):** connect
as for normal printing and type the four queries below in the console,
copying all output.

**C. From the running pi-interface:** Advanced → Read from GRBL: Build info
($I), Settings ($$), Parser state ($G), Offsets ($#) — same read-only
queries; copy them from the console / tables.

## Step 2 — The four queries

| Query | Records |
|---|---|
| `$I` | build info: `[VER:1.1x.yyyymmdd:…]` and `[OPT:…]` (compile options) — confirms GRBL 1.1 and the variant |
| `$$` | every setting: `$100/$101` steps/mm, `$110/$111` max rates, `$120/$121` acceleration, `$3` direction invert, `$130/$131` max travel, `$20` soft limits, `$21` hard limits, `$22` homing, `$13` report units, `$10` status mask … |
| `$#` | coordinate offsets G54–G59, G28, G30, G92, TLO, PRB |
| `$G` | parser / modal state |

Also note the startup line (`Grbl 1.1x ['$' for help]`).

## Step 3 — Record observations in the same file

- Board actually installed (Uno vs the earlier-documented Mega) and whether
  `$I` shows a stock GRBL 1.1 build — the stock Uno build matches the wired
  pin map; a Mega fork or custom pin map does NOT and must be reported
- Serial device seen (`/dev/ttyACM0` genuine Uno / `/dev/ttyUSB0` CH340) and
  the USB identity used for the udev rule (`deployment.md` step 5)
- Baud rate (115200 expected)
- Any alarms / errors seen in normal use
- Limit switches: wired to the Uno? `$21` / `$22` / `$5` values

## Step 4 — Commit the capture

    cp grbl-capture-*.txt ~/mudprintersoftware/arduino-grbl/grbl-settings-YYYY-MM-DD.txt
    git add arduino-grbl/grbl-settings-YYYY-MM-DD.txt
    git commit -m "Capture GRBL baseline (\$I \$\$ \$# \$G)"

Then record the version and key settings in `arduino-grbl/PROJECT_MEMORY.md`.

## Step 5 — Before the first Pi-driven motion

- `.env`: correct `SERIAL_PORT`, `BAUD_RATE`; `JOG_SPEED_MM_MIN=0` and
  `Z_PWM_DUTY=0` for the first connection
- Physical E-stop functional and within reach
- Machine idle, clear of people and obstructions

## Reminders

The Pi interface speaks the standard GRBL serial protocol only. It never
sends `$$=` / `$N=` setting writes, and the normal printer UI never flashes
firmware. If settings ever look different, compare with the committed
baseline.

---

## Prepared (NOT executed): future GRBL build / flash from the Pi

Only if a concrete requirement ever needs a firmware change — the current
plan is stock GRBL 1.1 with the standard Uno pin map, configured through `$`
settings. Do not reflash merely to strip unused Z / spindle / coolant code.

1. Capture first (Steps 1–4) — the `$$` file is the settings backup.
2. Install tools: `sudo apt install -y avrdude` (and `arduino-cli` if
   building from source).
3. Back up the current firmware image (Uno / optiboot can read flash):

       sudo systemctl stop mudprinter
       avrdude -p atmega328p -c arduino -P /dev/mudpr-grbl -b 115200 -U flash:r:grbl-backup-YYYY-MM-DD.hex:i

   (EEPROM read through the bootloader may not be supported — `$$` is the
   authoritative settings record.)
4. Build the hex from an exact upstream tag (`gnea/grbl` v1.1h) with any
   documented change, or use the release hex. Record the source and hash.
5. Flash (operator decision, machine powered for motion OFF / E-stop pressed
   as appropriate for the Uno's USB power):

       avrdude -p atmega328p -c arduino -P /dev/mudpr-grbl -b 115200 -U flash:w:grbl.hex:i

6. After a GRBL version change EEPROM settings may be reset: restore from the
   captured `$$` deliberately (operator, console, setting by setting), verify
   with a new capture, then re-run the acceptance test from X/Y step 7.
