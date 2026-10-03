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


# Position fields: MPos:x,y,z or WPos:x,y,z
_MPOS_RE = re.compile(r'MPos:(-?[\d.]+),(-?[\d.]+)')
_WPOS_RE = re.compile(r'WPos:(-?[\d.]+),(-?[\d.]+)')
# Feed/speed: FS:feed,spindle
_FS_RE = re.compile(r'FS:(-?[\d.]+)')

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

        fm = _FS_RE.search(rest)
        if fm:
            feed_rate = float(fm.group(1))

        return StatusReport(
            state=state,
            sub_state=sub_state,
            mpos_x=mpos_x,
            mpos_y=mpos_y,
            wpos_x=wpos_x,
            wpos_y=wpos_y,
            feed_rate=feed_rate,
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
