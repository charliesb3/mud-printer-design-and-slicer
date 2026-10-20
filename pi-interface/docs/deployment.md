# Pi Interface — Deployment Guide

Reproducible installation of the Mud Printer Interface (pi-interface, the
printer-specific replacement for UGS) on the control box's Raspberry Pi 4.
Development and tests run on a Mac with no GPIO (`HARDWARE_BACKEND=none`).

Physical wiring is authoritative in `../../CONTROL_BOX_HARDWARE.md`; GPIO
numbers in software live only in `app/hardware/pins.py`. This guide does not
commission the machine — `acceptance-test.md` does, step by step.

Core operation is LOCAL and OFFLINE: nothing below is a runtime dependency on
the internet (internet is only needed while installing packages). The Wi-Fi
access-point feature is not part of this pass.

---

## Hardware

- Raspberry Pi 4 Model B on regulated 5 V from the 12→5 V buck
- 7" 800×480 **DSI** touchscreen on the Pi's DSI connector (display data),
  powered separately with 5 V + GND from the regulated supply
- Pi USB-A (black USB 2.0 port) → Arduino Uno USB-B: GRBL serial + Uno power
- Pi GPIO: Kaleinavi RF receiver contacts (6 inputs) and the Cytron MD13S
  (PWM GPIO18 / DIR GPIO23) — `../../CONTROL_BOX_HARDWARE.md` → pin map

---

## 1. OS

Raspberry Pi Imager → **Raspberry Pi OS (64-bit) with desktop** (current
release; Bookworm or later — the desktop is needed for the touchscreen
kiosk). In the imager's settings: hostname `mudprinter`, a user account
(this guide assumes `pi`; otherwise substitute it everywhere, including the
systemd unit), SSH enabled, Wi-Fi only if an existing network will be used.

First boot:

    sudo apt update && sudo apt full-upgrade -y
    sudo apt install -y git python3-venv python3-gpiozero python3-lgpio curl
    which pinctrl            # Raspberry Pi GPIO tool (raspi-utils) — must print /usr/bin/pinctrl

`python3-gpiozero` / `python3-lgpio` are normally preinstalled on the desktop
image; gpiozero's default pin factory on current Raspberry Pi OS is lgpio,
which uses the kernel's `/dev/gpiochip*` interface (no root, no daemon).

Display: the 7" DSI panel should be detected automatically
(`display_auto_detect=1` in `/boot/firmware/config.txt`). If the screen stays
black, check its documentation for the required `dtoverlay=` line — **VERIFY**
(panel model / overlay not recorded yet).

---

## 2. Boot-time Z STOP (config.txt)

Make the firmware drive the MD13S lines LOW from early boot, long before
Linux or the application runs. Add to the end of
`/boot/firmware/config.txt` (under `[all]`):

    # Mud printer: MD13S PWM (GPIO18) and DIR (GPIO23) driven LOW from early boot = Z STOP
    gpio=18=op,dl
    gpio=23=op,dl

Reboot, then check: `pinctrl get 18,23` → both `op dl` (output, driving low).
These numbers must match `app/hardware/pins.py`.

See "Z default STOP" below for the full layering and what is still open.

---

## 3. Application

    cd ~
    git clone <repo-url> mudprintersoftware
    cd mudprintersoftware/pi-interface
    python3 -m venv --system-site-packages .venv     # sees apt's gpiozero + lgpio
    .venv/bin/pip install -r requirements.txt
    cp .env.example .env
    nano .env

`.env` on the Pi (first install — all motion disabled):

    SERIAL_PORT=/dev/mudpr-grbl     # after step 5; until then /dev/ttyACM0 or /dev/ttyUSB0
    BAUD_RATE=115200
    HARDWARE_BACKEND=gpio
    JOG_SPEED_MM_MIN=0              # X/Y jog disabled until validated (acceptance test)
    Z_PWM_DUTY=0                    # manual Z disabled until the default-STOP + actuator checks
    HOST=0.0.0.0
    PORT=8000

Permissions: the user must be in `dialout` (serial) and `gpio`
(`/dev/gpiochip*`). The imager's first user normally is; check with `groups`.
Otherwise: `sudo usermod -aG dialout,gpio pi` and log out / in. (The systemd
unit also adds both groups.)

Run once by hand:

    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

Expected log lines: `GPIO ready (LGPIOFactory): Z PWM GPIO18 … at STOP`,
`Hardware backend: gpio (manual Z DISABLED: Z_PWM_DUTY not set)`. Open
`http://mudprinter.local:8000` from a laptop; the interface loads and shows
the GRBL state (or Disconnected). Stop with Ctrl-C (Z is driven to STOP on
exit).

If the log says `GPIO backend 'gpio' failed`, the application runs WITHOUT
remote inputs or Z (the no-motion direction) and shows the reason under
Hardware / Commissioning — fix permissions / packages and restart.

---

## 4. systemd service (auto-start, safe shutdown)

The unit is in the repository: `deploy/mudprinter.service`.

    sudo cp deploy/mudprinter.service /etc/systemd/system/
    # edit User= and the /home/pi/... paths if the account is not "pi"
    sudo systemctl daemon-reload
    sudo systemctl enable --now mudprinter
    systemctl status mudprinter

What it does:
- starts at boot without waiting for any network (offline operation);
- `ExecStartPre` / `ExecStopPost`: `pinctrl set 18 op dl` and
  `pinctrl set 23 op dl` — Z STOP before the app starts and after it exits
  for ANY reason (stop, crash, kill, watchdog);
- `Type=notify` + `WatchdogSec=10`: the app reports ready and then pings from
  its event loop; if the loop hangs, systemd kills it (→ ExecStopPost Z STOP)
  and restarts it;
- `systemctl stop` sends SIGTERM: the app stops Z first, then closes GRBL;
- `Restart=on-failure`.

After a restart the controller comes up with Z STOP, no job loaded and
REMOTE STOP clear; GRBL resets when the port is reopened, so the print
origin must be set again.

---

## 5. Stable serial device for the Uno (`/dev/mudpr-grbl`)

`ttyACM0` / `ttyUSB0` numbering is not guaranteed. Create a udev symlink from
the Uno's USB identity — **discover the real attributes first**:

    ls -l /dev/serial/by-id/          # what is plugged in (also a usable stable path)
    udevadm info -q property -n /dev/ttyACM0 | grep -E 'ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL_SHORT|ID_PATH='

- Genuine Uno: vendor `2341`, a model id, and a unique `ID_SERIAL_SHORT` →
  use variant A of `deploy/99-mudpr-grbl.rules.example`.
- Clone (e.g. CH340 `1a86:7523`, appears as `/dev/ttyUSB0`) usually has no
  unique serial → variant B pins it to the physical USB port (`ID_PATH`);
  keep the cable in that port.

    sudo cp deploy/99-mudpr-grbl.rules.example /etc/udev/rules.d/99-mudpr-grbl.rules
    sudo nano /etc/udev/rules.d/99-mudpr-grbl.rules      # uncomment ONE variant, fill in the values
    sudo udevadm control --reload-rules && sudo udevadm trigger
    ls -l /dev/mudpr-grbl                                 # → ttyACM0 / ttyUSB0

Set `SERIAL_PORT=/dev/mudpr-grbl` in `.env` and restart the service.
(`/dev/serial/by-id/…` is an acceptable alternative if a rule is not wanted.)

---

## 6. Kiosk on the DSI touchscreen

Desktop autologin and no screen blanking:

    sudo raspi-config nonint do_boot_behaviour B4      # desktop, auto-login
    sudo raspi-config nonint do_blanking 1             # disable screen blanking

Current Raspberry Pi OS uses the labwc Wayland desktop; programs to start
with the desktop go in `~/.config/labwc/autostart`:

    mkdir -p ~/.config/labwc
    echo '/home/pi/mudprintersoftware/pi-interface/deploy/kiosk.sh &' >> ~/.config/labwc/autostart

`deploy/kiosk.sh` waits until `http://localhost:8000` answers, then starts
Chromium (`chromium` or `chromium-browser`) in kiosk mode with pinch-zoom and
swipe navigation disabled. The trailing `&` is required. To leave the kiosk
for maintenance: connect a keyboard (Alt+F4) or use SSH.

(If the image still runs an older X11 / wayfire desktop, start the same
script from that desktop's autostart instead.)

---

## 7. Z default STOP — layers and open items

The MD13S (sign-magnitude) stops when its PWM input is LOW: PWM LOW → both
motor outputs LOW, whatever DIR is (MD13S User's Manual V1.1, control truth
table). Logic input LOW must be ≤ 0.5 V; HIGH 3–5.5 V; PWM ≤ 20 kHz.

| Situation | What holds PWM LOW |
|---|---|
| Pi unpowered / MD13S powered | **external pull-down on the PWM line only** (nothing else drives it) |
| power-on → firmware reads config.txt | SoC reset state (GPIO18 input, default pull-DOWN ~50 kΩ) **+ external pull-down** |
| firmware → Linux → service start | `gpio=18=op,dl` (driven LOW) |
| service start | `ExecStartPre` pinctrl LOW; the app claims the pins at STOP before anything else |
| running | the app: dead-man / timeout / exception / REMOTE STOP → PWM 0 |
| app crash / kill / hang | systemd watchdog → `ExecStopPost` pinctrl LOW |
| service stop / shutdown | app stops Z first; `ExecStopPost` pinctrl LOW |
| kernel freeze | NOT covered by software; PWM keeps its last state (software PWM thread stops at its last level). Optional: enable the hardware watchdog (`RuntimeWatchdogSec=` in `/etc/systemd/system.conf`) so a frozen Pi reboots → the boot layers apply. The hardwired E-stop remains the safety layer. |

Kernel note: upstream Linux returns a released GPIO to INPUT unless
`pinctrl-bcm2835.persist_gpio_outputs=1`; the Raspberry Pi kernel may keep
outputs at their level. Check on the Pi:
`cat /sys/module/pinctrl_bcm2835/parameters/persist_gpio_outputs` — the
pinctrl `ExecStopPost` makes the result LOW either way.

**COMMISSIONING BLOCKER — external pull-down value.** The MD13S manual does
not state the PWM input's impedance or whether the board has its own
pull-down, so the resistor value cannot be derived from the specification.
Engineering proposal (to be confirmed by measurement, not a specification):
**10 kΩ from MD13S PWM to the signal-ground bus** — it holds the line at
≈ 0 V against up to ~50 µA of input current while drawing only 0.33 mA from
a GPIO driving HIGH (3.3 V stays above the MD13S 3 V HIGH threshold). It is
NOT accepted until acceptance-test step 5 measures, with the actuator
disconnected: PWM-to-GND voltage < 0.5 V with the Pi unpowered, during boot
and with the service stopped; ≥ 3.0 V when commanded. Ask Cytron for the
PWM input circuit if the measurement is marginal. Manual Z stays disabled
(`Z_PWM_DUTY=0`) until this is done.

---

## Updating the software

    cd ~/mudprintersoftware && git pull
    cd pi-interface && .venv/bin/pip install -r requirements.txt
    sudo systemctl restart mudprinter

Never update while a job is running.

## Logs and troubleshooting

    journalctl -u mudprinter -f              # live log
    journalctl -u mudprinter -b              # this boot
    systemctl status mudprinter

Logged hardware events (no polling noise): GPIO backend start, remote
CHn press / release, REMOTE STOP latched / cleared, Z start / stop with the
reason (release, dead-man timeout, contradiction, run limit, fault), GRBL
connect / disconnect / reset. The same events are listed in the UI under
Hardware / Commissioning.

- Remote input stuck "unknown": the service cannot read GPIO — see the
  backend error in Hardware / Commissioning.
- `GRBL disconnected` repeatedly: check `ls -l /dev/mudpr-grbl` and that no
  other program (capture tool, screen, UGS) holds the port.
- `pinctrl get 18,23`: shows what the Z lines are doing right now.

## GRBL capture / future firmware work from the Pi

Read-only capture before any change: `grbl-capture-checklist.md`
(`tools/grbl_capture.py`). A future firmware flash procedure is prepared
there too; the printer UI never flashes firmware.
