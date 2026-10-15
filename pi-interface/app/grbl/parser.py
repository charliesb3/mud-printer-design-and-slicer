"""
Parse GRBL serial responses.

Supports GRBL 1.1 status format: <State|MPos:x,y,z|FS:f,s>
Also handles GRBL 0.9 format for robustness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class StatusReport:
    state: str          # e.g. "Idle", "Run", "Hold", "Jog", "Alarm"
    sub_state: str | None   # e.g. "0" from "Hold:0", "1" from "Alarm:1"
    mpos_x: float | None
    mpos_y: float | None
    wpos_x: float | None
    wpos_y: float | None
    feed_rate: float | None
    overrides: tuple | None = None      # Ov:feed,rapid,spindle (only in some reports)
    pins: str = ""                      # Pn: triggered input pins ("" = none reported)
    buffer: tuple | None = None         # Bf:planner blocks free, RX bytes free
    wco: tuple | None = None            # WCO:x,y (only in some reports)
    line_number: int | None = None      # Ln:


# Position fields: MPos:x,y,z or WPos:x,y,z
_MPOS_RE = re.compile(r'MPos:(-?[\d.]+),(-?[\d.]+)')
_WPOS_RE = re.compile(r'WPos:(-?[\d.]+),(-?[\d.]+)')
# Feed/speed: FS:feed,spindle (or F:feed)
_FS_RE = re.compile(r'(?:^|\|)F(?:S)?:(-?[\d.]+)')
_OV_RE = re.compile(r'Ov:(\d+),(\d+),(\d+)')
_PN_RE = re.compile(r'Pn:([A-Z]+)')
_BF_RE = re.compile(r'Bf:(\d+),(\d+)')
_WCO_RE = re.compile(r'WCO:(-?[\d.]+),(-?[\d.]+)')
_LN_RE = re.compile(r'Ln:(\d+)')

# GRBL 0.9: <State,MPos:x,y,z,WPos:x,y,z> — comma-separated, no | characters
_GRBL09_RE = re.compile(r'^<(\w+),MPos:(-?[\d.]+),(-?[\d.]+)(?:[^>]*)>')


def parse_status_report(line: str) -> StatusReport | None:
    """Return a StatusReport for a GRBL status line, or None if not a status report."""
    if not (line.startswith('<') and '>' in line):
        return None

    inner = line[1:line.index('>')]

    # GRBL 1.1 uses '|' as field separator; GRBL 0.9 uses ',' throughout.
    if '|' in inner:
        # GRBL 1.1 format: <State[:sub]|field|field>
        parts = inner.split('|', 1)
        state_field = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if ':' in state_field:
            state, sub_state = state_field.split(':', 1)
        else:
            state, sub_state = state_field, None

        mpos_x = mpos_y = wpos_x = wpos_y = feed_rate = None

        pm = _MPOS_RE.search(rest)
        if pm:
            mpos_x, mpos_y = float(pm.group(1)), float(pm.group(2))

        pw = _WPOS_RE.search(rest)
        if pw:
            wpos_x, wpos_y = float(pw.group(1)), float(pw.group(2))

        fm = _FS_RE.search('|' + rest)
        if fm:
            feed_rate = float(fm.group(1))
        ov, pn, bf, wco, ln = (_OV_RE.search(rest), _PN_RE.search(rest), _BF_RE.search(rest),
                               _WCO_RE.search(rest), _LN_RE.search(rest))

        return StatusReport(
            state=state,
            sub_state=sub_state,
            mpos_x=mpos_x,
            mpos_y=mpos_y,
            wpos_x=wpos_x,
            wpos_y=wpos_y,
            feed_rate=feed_rate,
            overrides=tuple(int(v) for v in ov.groups()) if ov else None,
            pins=pn.group(1) if pn else "",
            buffer=(int(bf.group(1)), int(bf.group(2))) if bf else None,
            wco=(float(wco.group(1)), float(wco.group(2))) if wco else None,
            line_number=int(ln.group(1)) if ln else None,
        )

    # GRBL 0.9 format: <State,MPos:x,y,z,WPos:x,y,z>
    m09 = _GRBL09_RE.match(line)
    if m09:
        return StatusReport(
            state=m09.group(1),
            sub_state=None,
            mpos_x=float(m09.group(2)),
            mpos_y=float(m09.group(3)),
            wpos_x=None,
            wpos_y=None,
            feed_rate=None,
        )

    return None


def is_welcome_message(line: str) -> bool:
    """True for the GRBL startup/reset greeting line."""
    return line.startswith("Grbl ") or line.startswith("grbl ")


def parse_grbl_version(line: str) -> str | None:
    """Extract version token from welcome message, e.g. '1.1f' from 'Grbl 1.1f [...]'."""
    if is_welcome_message(line):
        parts = line.split()
        if len(parts) >= 2:
            return parts[1]
    return None


_PAREN_COMMENT_RE = re.compile(r'\([^)]*\)')


def strip_gcode_comment(line: str) -> str:
    """Remove G-code comments: (parenthetical) and ; to end of line."""
    line = _PAREN_COMMENT_RE.sub('', line)
    idx = line.find(';')
    if idx >= 0:
        line = line[:idx]
    return line.strip()


def prepare_gcode_line(raw: str) -> str | None:
    """
    Strip comments and whitespace from a raw G-code line.
    Returns None if the line is empty or comment-only (should be skipped).
    """
    cleaned = strip_gcode_comment(raw)
    return cleaned if cleaned else None


def is_end_of_program(line: str) -> bool:
    """True if the G-code line signals end of program (M2, M30, or bare %)."""
    normalized = strip_gcode_comment(line).upper().replace(' ', '')
    return normalized in ('M2', 'M02', 'M30', '%')


# Program pause / end words: M0, M1, M2, M30 (leading zeros allowed: M00, M02).
_PROGRAM_CONTROL_RE = re.compile(r'M0*(0|1|2|30)(?!\d)')


def program_control_reason(line: str) -> str | None:
    """
    Why a line may not appear inside a building-job lift, or None if it may.

    Lift G-code is ordinary motion G-code; the Pi job runner owns all pauses
    and the end of the job. Program pause/end words (M0/M1/M2/M30), '%'
    markers, and '$' system commands are therefore rejected.
    """
    normalized = strip_gcode_comment(line).upper().replace(' ', '')
    if not normalized:
        return None
    if normalized.startswith('%'):
        return "'%' program marker"
    if normalized.startswith('$'):
        return "GRBL '$' system command"
    m = _PROGRAM_CONTROL_RE.search(normalized)
    if m:
        return f"program pause/end word {m.group(0)}"
    return None


# GRBL setting line in a `$$` listing: "$13=0" (optionally followed by a comment).
_SETTING_RE = re.compile(r'^\$(\d+)=([^\s(]+)')


def parse_setting(line: str) -> tuple[int, str] | None:
    """(number, value) for a GRBL `$N=value` line, else None."""
    m = _SETTING_RE.match(line.strip())
    return (int(m.group(1)), m.group(2)) if m else None


# Building-job lift G-code rules for the CURRENT machine (XY-only, the
# controller owns the work coordinate system).
_Z_WORD_RE = re.compile(r'Z[-+.\d]')
_XY_WORD_RE = re.compile(r'[XY][-+.\d]')
_COORD_CHANGE_RE = re.compile(r'G0*(10|92)(?![\d])')
_UNITS_RE = re.compile(r'G0*2[01](?![\d.])')
_DISTANCE_RE = re.compile(r'G9[01](?![\d.])')


def lift_gcode_reason(lines: list[str]) -> str | None:
    """
    Why a building-job lift's (comment-stripped) G-code is not executable on
    the current machine, or None. Each lift must set units (G20/G21) and
    distance mode (G90/G91) itself before any XY motion (never relying on the
    controller's previous modal state — the operator may jog / re-zero
    between lifts); no Z words (Z is not a GRBL axis); no G10 / G92 (the
    controller owns the work coordinate system).
    """
    units = distance = False
    for n, line in enumerate(lines, start=1):
        u = strip_gcode_comment(line).upper().replace(' ', '')
        if _Z_WORD_RE.search(u):
            return f"line {n} ({line}): Z words are not allowed (Z is not a GRBL axis on this machine)"
        if _COORD_CHANGE_RE.search(u):
            return f"line {n} ({line}): G10 / G92 are not allowed (the controller owns the work coordinates)"
        units = units or bool(_UNITS_RE.search(u))
        distance = distance or bool(_DISTANCE_RE.search(u))
        if _XY_WORD_RE.search(u) and not (units and distance):
            return (f"line {n} ({line}): XY motion before the lift sets its units (G20/G21) "
                    f"and distance mode (G90/G91)")
    return None


# Bracketed query responses: [GC:…] ($G parser state), [G54:x,y,z] … [PRB:…] ($#)
_BRACKET_RE = re.compile(r'^\[([A-Z0-9.]+):(.*)\]$')


def parse_bracket(line: str) -> tuple[str, str] | None:
    """(key, value) for a `[KEY:value]` GRBL line, e.g. ('GC', 'G0 G54 …'), ('G54', '0.000,0.000,0.000')."""
    m = _BRACKET_RE.match(line.strip())
    return (m.group(1), m.group(2)) if m else None
