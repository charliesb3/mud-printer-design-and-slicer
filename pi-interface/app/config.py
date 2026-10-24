from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Serial connection
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
BAUD_RATE: int = int(os.getenv("BAUD_RATE", "115200"))

# Jog configuration
#
# JOG_SPEED_MM_MIN is the DEFAULT jog speed at startup. The operator can
# adjust the live speed from the UI; the server clamps it against
# JOG_SPEED_MAX_MM_MIN (the hard ceiling) and the jog loop re-reads the
# current speed each iteration so a slider change takes effect within one
# step. A JOG_SPEED_MM_MIN of 0 disables jogging. The UI shows jog speed in
# in/s; GRBL always receives mm/min.
_jog_speed_raw = float(os.getenv("JOG_SPEED_MM_MIN", "0"))
JOG_SPEED_MM_MIN: float | None = _jog_speed_raw if _jog_speed_raw > 0 else None

# Hard ceiling for the UI jog-speed slider. Defaults to the configured
# default when unset, so the slider has no travel until the operator
# explicitly raises the ceiling in .env. See
# `arduino-grbl/steps-per-mm-calculation.md` for the drivetrain-safe speed
# derivation.
_jog_speed_max_raw = float(os.getenv("JOG_SPEED_MAX_MM_MIN", "0"))
if _jog_speed_max_raw > 0:
    JOG_SPEED_MAX_MM_MIN: float | None = _jog_speed_max_raw
elif JOG_SPEED_MM_MIN is not None:
    JOG_SPEED_MAX_MM_MIN = JOG_SPEED_MM_MIN
else:
    JOG_SPEED_MAX_MM_MIN = None

# How often (seconds) the backend sends an incremental jog command.
# Each step covers: current_jog_speed_mm_min * JOG_SEND_INTERVAL_S / 60 mm.
# Maximum axis travel after browser connection loss ≈ 1–2 step distances.
JOG_SEND_INTERVAL_S: float = float(os.getenv("JOG_SEND_INTERVAL_S", "0.15"))

# Backend stops sending jog commands if no heartbeat received within this interval.
JOG_TIMEOUT_S: float = float(os.getenv("JOG_TIMEOUT_S", "0.3"))

# GRBL status poll interval in seconds (0.2 = 5 Hz)
STATUS_POLL_INTERVAL_S: float = float(os.getenv("STATUS_POLL_INTERVAL_S", "0.2"))

# Time to wait between serial reconnect attempts
RECONNECT_INTERVAL_S: float = 5.0

# Timeout waiting for GRBL to acknowledge a normal command
COMMAND_TIMEOUT_S: float = 10.0

# Job lines, lift drains and positioning moves wait for GRBL's `ok` WITHOUT a
# fixed time limit (GRBL withholds `ok` while its planner buffer is full or a
# feed hold is active — minutes on this machine). They fail only when GRBL
# stops answering status polls for this long (unresponsive / link dead).
GRBL_UNRESPONSIVE_S: float = float(os.getenv("GRBL_UNRESPONSIVE_S", "5.0"))

# This controller's MACHINE PROFILE (the physical printer): a local JSON file,
# edited in the UI (Machine / Printer settings). Independent of any Designer
# project — .mudjob files only report what they were prepared for.
MACHINE_PROFILE_PATH: Path = Path(os.getenv("MACHINE_PROFILE_PATH", "machine_profile.json"))

# Job storage directory (relative to pi-interface/ when running locally)
JOBS_DIR: Path = Path(os.getenv("JOBS_DIR", "jobs"))

# Web server
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# ------------------------------------------------------------------ #
# Raspberry Pi hardware (GPIO pins: app/hardware/pins.py — never here)  #
# ------------------------------------------------------------------ #

# none = no GPIO (Mac development; remote absent, no Z) | mock = in-memory
# pins (tests / UI exercise) | gpio = the Raspberry Pi (gpiozero → lgpio).
HARDWARE_BACKEND: str = os.getenv("HARDWARE_BACKEND", "none")

# Remote inputs are sampled at this interval and debounced: a level must be
# stable this long before it counts (relay contacts bounce a few ms).
REMOTE_POLL_INTERVAL_S: float = float(os.getenv("REMOTE_POLL_INTERVAL_S", "0.01"))
REMOTE_DEBOUNCE_S: float = float(os.getenv("REMOTE_DEBOUNCE_S", "0.05"))

# Manual Z (MD13S). FIXED duty 0–1 for every manual Z move (no operator speed
# control). 0 = manual Z DISABLED — set it only during commissioning, after the
# default-STOP check (docs/acceptance-test.md). Never a position estimate.
Z_PWM_DUTY: float = float(os.getenv("Z_PWM_DUTY", "0"))
# Software PWM frequency (lgpio). MD13S accepts up to 20 kHz.
Z_PWM_FREQUENCY_HZ: float = float(os.getenv("Z_PWM_FREQUENCY_HZ", "1000"))
# Swap if Z UP moves the actuator down (set at commissioning).
Z_DIR_INVERT: bool = os.getenv("Z_DIR_INVERT", "false").strip().lower() in ("1", "true", "yes")
# Dead-man: a held Z request (UI heartbeat / remote contact) must be refreshed
# within this time or Z stops.
Z_TIMEOUT_S: float = float(os.getenv("Z_TIMEOUT_S", "0.3"))
# Any single continuous Z run stops after this long (release and press again).
Z_MAX_RUN_S: float = float(os.getenv("Z_MAX_RUN_S", "30"))

# Rolling console buffer size
CONSOLE_MAX_LINES: int = 100
