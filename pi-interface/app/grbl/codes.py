"""
GRBL 1.1 message codes and setting names (read-only reference for the
diagnostics view; from the GRBL 1.1 interface documentation). The Pi never
writes settings.
"""
from __future__ import annotations

ALARMS = {
    1: "Hard limit triggered. Machine position is likely lost — re-establish position (re-home if available).",
    2: "Motion target exceeds machine travel (soft limit). Machine position safely retained.",
    3: "Reset while in motion. Position cannot be guaranteed; steps likely lost.",
    4: "Probe fail: probe not in the expected initial state.",
    5: "Probe fail: probe did not contact the workpiece.",
    6: "Homing fail: reset during the homing cycle.",
    7: "Homing fail: safety door opened during homing.",
    8: "Homing fail: could not clear the limit switch when pulling off.",
    9: "Homing fail: could not find the limit switch within the search distance.",
    10: "Homing fail: could not find the second limit switch (dual axis).",
}

ERRORS = {
    1: "G-code words consist of a letter and a value. Letter was not found.",
    2: "Numeric value format is not valid or missing an expected value.",
    3: "'$' system command was not recognized or supported.",
    4: "Negative value received for an expected positive value.",
    5: "Homing cycle is not enabled via settings.",
    6: "Minimum step pulse time must be greater than 3 µs.",
    7: "EEPROM read failed. Reset and restored to default values.",
    8: "'$' command cannot be used unless GRBL is IDLE.",
    9: "G-code locked out during alarm or jog state.",
    10: "Soft limits cannot be enabled without homing also enabled.",
    11: "Max characters per line exceeded. Line was not processed.",
    12: "'$' setting value exceeds the maximum step rate supported.",
    13: "Safety door detected as opened and door state initiated.",
    14: "Build info or startup line exceeded EEPROM line length limit.",
    15: "Jog target exceeds machine travel. Command ignored.",
    16: "Jog command with no '=' or contains prohibited G-code.",
    17: "Laser mode requires PWM output.",
    20: "Unsupported or invalid G-code command found in block.",
    21: "More than one G-code command from the same modal group in block.",
    22: "Feed rate has not yet been set or is undefined.",
    23: "G-code command in block requires an integer value.",
    24: "Two G-code commands that both require XYZ axis words in the block.",
    25: "A G-code word was repeated in the block.",
    26: "A G-code command requires XYZ axis words in the block, but none were found.",
    27: "N line number value is not within the valid range.",
    28: "A G-code command is missing required P or L value words.",
    29: "Only work coordinate systems G54–G59 are supported.",
    30: "G53 requires G0 or G1 motion mode to be active.",
    31: "Unused axis words in the block while G80 motion mode cancel is active.",
    32: "G2/G3 arc with no XYZ axis words in the selected plane.",
    33: "The motion command has an invalid target.",
    34: "Arc radius value is invalid.",
    35: "G2/G3 arc offset words missing in the selected plane.",
    36: "Unused, leftover G-code words in the block.",
    37: "G43.1 tool length offset cannot apply to an axis other than its configured axis.",
    38: "Tool number greater than the maximum supported value.",
}

SETTINGS = {
    0: "Step pulse time (µs)", 1: "Step idle delay (ms)", 2: "Step pulse invert (mask)",
    3: "Step direction invert (mask)", 4: "Invert step enable pin", 5: "Invert limit pins",
    6: "Invert probe pin", 10: "Status report options (mask)", 11: "Junction deviation (mm)",
    12: "Arc tolerance (mm)", 13: "Report in inches", 20: "Soft limits enable", 21: "Hard limits enable",
    22: "Homing cycle enable", 23: "Homing direction invert (mask)", 24: "Homing locate feed rate (mm/min)",
    25: "Homing search seek rate (mm/min)", 26: "Homing switch debounce delay (ms)",
    27: "Homing switch pull-off distance (mm)", 30: "Maximum spindle speed (RPM)",
    31: "Minimum spindle speed (RPM)", 32: "Laser-mode enable",
    100: "X steps/mm", 101: "Y steps/mm", 102: "Z steps/mm",
    110: "X max rate (mm/min)", 111: "Y max rate (mm/min)", 112: "Z max rate (mm/min)",
    120: "X acceleration (mm/s²)", 121: "Y acceleration (mm/s²)", 122: "Z acceleration (mm/s²)",
    130: "X max travel (mm)", 131: "Y max travel (mm)", 132: "Z max travel (mm)",
}

PINS = {"X": "X limit", "Y": "Y limit", "Z": "Z limit", "P": "probe", "D": "door",
        "H": "hold button", "R": "reset button", "S": "cycle-start button"}


def describe_alarm(code: int) -> str:
    return ALARMS.get(code, "Unknown alarm.")


def describe_error(code: int) -> str:
    return ERRORS.get(code, "Unknown error.")
