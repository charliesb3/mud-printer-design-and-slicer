"""
THE authoritative Raspberry Pi GPIO map of this printer's control box, in
software. Wired 2026-10-20; source of truth for the physical wiring:
`CONTROL_BOX_HARDWARE.md` → "Raspberry Pi GPIO pin map (wired)".

BCM numbering everywhere in code; the physical 40-pin header number is kept
alongside for diagnostics and documentation only. No other module may contain
a GPIO number — they all take it from here.

Remote inputs (Kaleinavi CH1–CH6): dry relay contacts, NO → ~1 kΩ → GPIO,
COM → Pi GND, Pi internal pull-up. ACTIVE LOW: contact open = HIGH =
released; contact closed to GND = LOW = pressed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteChannel:
    key: str          # stable software name
    channel: int      # Kaleinavi channel number
    bcm: int          # BCM GPIO number (used in code)
    physical: int     # 40-pin header pin (documentation / diagnostics)
    label: str        # operator-facing name


# Kaleinavi CH1–CH6, in channel order. Functions are the wired assignment.
REMOTE_STOP = RemoteChannel("remote_stop", 1, 17, 11, "REMOTE STOP")
Z_UP = RemoteChannel("z_up", 2, 27, 13, "Z UP")
Z_DOWN = RemoteChannel("z_down", 3, 22, 15, "Z DOWN")
EXTRUSION = RemoteChannel("extrusion", 4, 5, 29, "Extrusion pause / resume (no output defined)")
AUX1 = RemoteChannel("aux1", 5, 6, 31, "AUX 1 (reserved)")
AUX2 = RemoteChannel("aux2", 6, 13, 33, "AUX 2 (reserved)")

REMOTE_CHANNELS: tuple = (REMOTE_STOP, Z_UP, Z_DOWN, EXTRUSION, AUX1, AUX2)

# Remote inputs are active LOW (pull-up; contact closes to GND).
REMOTE_ACTIVE_LOW = True

# Cytron MD13S (sign-magnitude): PWM LOW = both motor outputs LOW = STOP.
Z_PWM_BCM, Z_PWM_PHYSICAL = 18, 12
Z_DIR_BCM, Z_DIR_PHYSICAL = 23, 16

# DIR level for each Z direction. Which actuator direction a level produces
# depends on the motor wiring and is VERIFIED AT COMMISSIONING (actuator
# test); swap with Z_DIR_INVERT in .env, never by renumbering pins.
Z_DIR_LEVEL_UP = True

SIGNAL_GROUND_PHYSICAL = 9


def all_bcm() -> list:
    """Every BCM number this application uses (no duplicates allowed)."""
    return [c.bcm for c in REMOTE_CHANNELS] + [Z_PWM_BCM, Z_DIR_BCM]
