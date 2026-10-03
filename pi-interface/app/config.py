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
# JOG_SPEED_MM_MIN must be validated on the physical machine before use.
# A value of 0 disables jogging entirely. Start conservatively during
# first hardware testing and only increase after confirming safe behavior.
_jog_speed_raw = float(os.getenv("JOG_SPEED_MM_MIN", "0"))
JOG_SPEED_MM_MIN: float | None = _jog_speed_raw if _jog_speed_raw > 0 else None

# How often (seconds) the backend sends an incremental jog command.
# Each step covers: JOG_SPEED_MM_MIN * JOG_SEND_INTERVAL_S / 60 mm.
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

# Job storage directory (relative to pi-interface/ when running locally)
JOBS_DIR: Path = Path(os.getenv("JOBS_DIR", "jobs"))

# Web server
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# Rolling console buffer size
CONSOLE_MAX_LINES: int = 100
