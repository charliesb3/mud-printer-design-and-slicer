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
# JOG_SPEED_MM_MIN is the default jog speed used at startup. A value of 0
# disables jogging entirely until a non-zero speed is configured.
# JOG_SPEED_MAX_MM_MIN is a hard ceiling the operator cannot exceed from the UI.
# Both must be validated on the physical machine. Start conservatively.
_jog_speed_raw = float(os.getenv("JOG_SPEED_MM_MIN", "0"))
JOG_SPEED_MM_MIN: float | None = _jog_speed_raw if _jog_speed_raw > 0 else None

# Hard ceiling for the UI jog-speed slider. Defaults to the configured default
# when unset, so the slider has no travel until the operator explicitly raises
# the ceiling in .env.
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

# ---------------------------------------------------------------------------
# Printer envelope (nominal, display-only)
#
# These values describe the manufacturer / designer's nominal XY envelope.
# They are NOT verified travel limits and NOT used for soft-limit enforcement.
# The GRBL firmware is still responsible for actual axis limits. Set via .env
# in inches and converted to mm here.
# ---------------------------------------------------------------------------
_envelope_x_in = float(os.getenv("PRINTER_ENVELOPE_X_IN", "0"))
_envelope_y_in = float(os.getenv("PRINTER_ENVELOPE_Y_IN", "0"))
PRINTER_ENVELOPE_X_MM: float | None = _envelope_x_in * 25.4 if _envelope_x_in > 0 else None
PRINTER_ENVELOPE_Y_MM: float | None = _envelope_y_in * 25.4 if _envelope_y_in > 0 else None
# Whether the operator has physically verified the usable travel. Even when
# True, the envelope here is still only a display hint; GRBL enforces limits.
PRINTER_EXTENTS_VERIFIED: bool = os.getenv("PRINTER_EXTENTS_VERIFIED", "0") in ("1", "true", "True")

# GRBL status poll interval in seconds (0.2 = 5 Hz)
STATUS_POLL_INTERVAL_S: float = float(os.getenv("STATUS_POLL_INTERVAL_S", "0.2"))

# Time to wait between serial reconnect attempts
RECONNECT_INTERVAL_S: float = 5.0

# Timeout waiting for GRBL to acknowledge a normal command
COMMAND_TIMEOUT_S: float = 10.0

# Job storage directory (relative to pi-interface/ when running locally)
JOBS_DIR: Path = Path(os.getenv("JOBS_DIR", "jobs"))

# Web server
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# Rolling console buffer size
CONSOLE_MAX_LINES: int = 100
