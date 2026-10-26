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
    key: str              # stable software name
    channel: int          # Kaleinavi channel number
    bcm: int              # BCM GPIO number (used in code)
    physical: int         # 40-pin header pin (documentation / diagnostics)
    label: str            # operator-facing name
    enabled: bool = True  # sixteenth-pass: disabled channels are
                          # polled only to keep the pull-up configured;
                          # they emit NO transitions, require NO arming,
                          # and never trigger motion / warnings / faults.


# Kaleinavi CH1–CH6, in channel order (2026-10-26 remap).
#
# Previous (pre-sixteenth-pass):
#   CH1 REMOTE STOP, CH2 Z UP, CH3 Z DOWN, CH4 Extrusion, CH5/CH6 AUX.
#
# New assignment:
#   CH1 REMOTE STOP   (unchanged)
#   CH2 Z UP          (unchanged)
#   CH3 DISABLED      (ignored in software — pin stays pulled up but
#                      presses produce no event)
#   CH4 Z DOWN        (moved from CH3 — same handheld button, new GPIO)
#   CH5 Extrusion     (pause / resume — reserved; no pump output yet)
#   CH6 SPARE         (unchanged — no function, reserved)
#
# Physical GPIO assignments are NOT changed — only the software mapping.
REMOTE_STOP = RemoteChannel("remote_stop", 1, 17, 11, "REMOTE STOP")
Z_UP        = RemoteChannel("z_up",        2, 27, 13, "Z UP")
DISABLED_CH3 = RemoteChannel("disabled_ch3", 3, 22, 15,
                              "DISABLED (ignored)", enabled=False)
Z_DOWN      = RemoteChannel("z_down",      4,  5, 29, "Z DOWN")
EXTRUSION   = RemoteChannel("extrusion",   5,  6, 31,
                             "Extrusion pause / resume (reserved — no output)")
SPARE       = RemoteChannel("spare",       6, 13, 33, "Spare (reserved)")

REMOTE_CHANNELS: tuple = (REMOTE_STOP, Z_UP, DISABLED_CH3, Z_DOWN, EXTRUSION, SPARE)

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
