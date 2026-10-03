# Pi Interface — Deployment Guide

Instructions for deploying the Mud Printer Interface on a Raspberry Pi.

Development is done on Mac. The Pi is the runtime target.

---

## Hardware

- Raspberry Pi 4B (2 GB RAM or more)
- Official 7-inch touchscreen (800×480)
- USB connection to Arduino Mega running GRBL

---

## OS Setup

Use Raspberry Pi OS (Bookworm or later, 64-bit recommended).

Use the Raspberry Pi Imager to write the SD card.

During imaging, configure:
- hostname: `mudprinter` (or your preference)
- SSH: enabled
- user/password: set appropriately
- Wi-Fi: configure if you will use existing Wi-Fi

---

## First Boot — Install Dependencies

SSH into the Pi or use a keyboard/monitor.

    sudo apt update && sudo apt upgrade -y
    sudo apt install -y python3 python3-venv python3-pip git

---

## Clone the Repository

    cd ~
    git clone <repo-url> mudprintersoftware
    cd mudprintersoftware/pi-interface

---

## Create the Virtual Environment

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

---

## Configure the Environment

    cp .env.example .env
    nano .env

Set these values for the Pi:

    SERIAL_PORT=/dev/ttyUSB0     # or /dev/ttyACM0 — check with: ls /dev/tty*
    BAUD_RATE=115200
    JOG_SPEED_MM_MIN=0           # Leave 0 until jog speed is validated on machine
    HOST=0.0.0.0
    PORT=8000

To find the correct serial port after connecting the Arduino:

    ls /dev/tty* | grep -E "USB|ACM"

---

## Add Pi User to dialout Group

Required for serial port access without sudo:

    sudo usermod -a -G dialout $USER

Log out and back in (or reboot) for this to take effect.

---

## Test the Application

Run manually first to confirm it works:

    cd ~/mudprintersoftware/pi-interface
    source .venv/bin/activate
    uvicorn app.main:app --host 0.0.0.0 --port 8000

Open a browser on another device and navigate to http://mudprinter:8000
(or use the Pi's IP address).

Confirm the interface loads and shows "Disconnected" if the Arduino is not
connected, or the GRBL state if it is.

---

## systemd Service — Auto-Start on Boot

Create the service file:

    sudo nano /etc/systemd/system/mudprinter.service

Contents:

    [Unit]
    Description=Mud Printer Interface
    After=network.target

    [Service]
    Type=simple
    User=pi
    WorkingDirectory=/home/pi/mudprintersoftware/pi-interface
    ExecStart=/home/pi/mudprintersoftware/pi-interface/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

Adjust `User` and paths if your username differs from `pi`.

Enable and start:

    sudo systemctl daemon-reload
    sudo systemctl enable mudprinter
    sudo systemctl start mudprinter
    sudo systemctl status mudprinter

---

## Kiosk Mode (Chromium on the Touchscreen)

To automatically open the interface in a full-screen kiosk browser on the
Pi's touchscreen, add a desktop autostart entry.

Create the autostart directory if it doesn't exist:

    mkdir -p ~/.config/autostart

Create the file:

    nano ~/.config/autostart/mudprinter-kiosk.desktop

Contents:

    [Desktop Entry]
    Type=Application
    Name=Mud Printer Kiosk
    Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:8000
    X-GNOME-Autostart-enabled=true

This requires a desktop environment (Raspberry Pi OS with desktop).

If using Raspberry Pi OS Lite, a minimal X + openbox + chromium setup is an
alternative but requires more configuration.

---

## Accessing from Another Device

From a laptop or tablet on the same network:

    http://mudprinter.local:8000

Or use the Pi's IP address:

    http://192.168.x.x:8000

---

## Wi-Fi Access Point (Offline Operation)

For field use where no existing network is available, the Pi can create its
own Wi-Fi access point.

This allows a laptop or tablet to connect directly to the Pi without any
external network infrastructure.

Setup instructions for hostapd + dnsmasq are not yet documented here.

This is the intended eventual configuration for fully offline operation.

---

## Updating the Software

    cd ~/mudprintersoftware
    git pull
    cd pi-interface
    source .venv/bin/activate
    pip install -r requirements.txt
    sudo systemctl restart mudprinter

---

## Checking Logs

    sudo journalctl -u mudprinter -f

---

## Serial Port Notes

If the Arduino serial port changes between reboots (e.g., sometimes ttyUSB0,
sometimes ttyUSB1), you can use a udev rule to assign a fixed symlink.

This is not yet configured. If port instability occurs, investigate udev rules
based on the Arduino's USB vendor/product ID.
