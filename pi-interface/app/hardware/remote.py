"""
Kaleinavi RF remote inputs (CH1–CH6): polling, debounce, arming.

Inputs are sampled at a fixed interval (no interrupt threads) and debounced
in software: a channel's state changes only after its level has been stable
for `debounce_s`. Interpretation is ACTIVE LOW (pins.REMOTE_ACTIVE_LOW).

Never act on an uninitialised / transient state:
- nothing is reported before the inputs have been configured (pull-ups) and
  each channel has been stable for at least `settle_s` after start;
- a channel whose first stable state is PRESSED is NOT ARMED: it produces no
  press event until it has been seen released. (The service treats REMOTE
  STOP closed at startup as a stop — the no-motion direction; see service.py.)
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import Callable, Optional

from . import pins
from .backend import HardwareBackend

# Shortcut so tests can monkeypatch
time = _time


@dataclass
class ChannelState:
    channel: pins.RemoteChannel
    level: Optional[bool] = None         # last raw level (True = HIGH)
    candidate: Optional[bool] = None     # raw "pressed" being timed
    since: float = 0.0
    pressed: Optional[bool] = None       # debounced; None = not yet known
    armed: bool = False                  # seen released since start
    held_at_startup: bool = False
    changed_at: Optional[float] = None


@dataclass
class Transition:
    channel: pins.RemoteChannel
    pressed: bool
    startup: bool = False                # first stable state (only reported when pressed)


class RemoteInputs:
    def __init__(self, backend: HardwareBackend, *, debounce_s: float, settle_s: float = 0.2,
                 clock: Callable[[], float] = _time.monotonic,
                 wall_clock: Callable[[], float] = _time.time) -> None:
        self._backend = backend
        self.debounce_s = debounce_s
        self.settle_s = max(settle_s, debounce_s)
        self._clock = clock
        self._wall_clock = wall_clock
        self._started = clock()
        # Wall-clock timestamp of the last ARMED remote-channel transition.
        # Used by the UI to show "handheld last activity Xm ago", a hint
        # that the handheld may have gone to sleep. The Kaleinavi receiver
        # has no "RF link lost" output, so this is the best signal we can
        # offer the operator; a long silence may just mean the operator
        # hasn't touched the remote. See PROJECT_MEMORY twelfth-pass.
        self.last_activity_wall_t: Optional[float] = None
        self.channels = {c.key: ChannelState(c) for c in pins.REMOTE_CHANNELS}

    @staticmethod
    def interpret(level: bool) -> bool:
        """Raw level → pressed (active LOW: LOW = pressed)."""
        return (not level) if pins.REMOTE_ACTIVE_LOW else bool(level)

    def poll(self) -> list:
        """Sample every input once; return the debounced transitions.

        Disabled channels (sixteenth-pass): the pin is still read (so the
        pull-up stays configured and the level is visible for diagnostics)
        but no transitions are ever emitted and no arming is required. A
        disabled channel permanently reports `armed=False`, `pressed=None`
        so `is_pressed()` always returns False — safely ignoring any held
        contact / wiring glitch on that channel.
        """
        if not self._backend.has_gpio:
            return []
        now = self._clock()
        levels = self._backend.read_levels()
        out = []
        for st in self.channels.values():
            level = levels.get(st.channel.bcm)
            if level is None:
                continue
            st.level = level
            if not st.channel.enabled:
                # Read the raw level for diagnostics but do nothing else:
                # no debounce, no armed state, no transitions. A held
                # contact on a disabled channel is NOT an event.
                continue
            raw = self.interpret(level)
            if raw != st.candidate:
                st.candidate, st.since = raw, now
                continue
            if st.pressed is None:
                # first stable state: only after the settle time
                if now - self._started >= self.settle_s and now - st.since >= self.debounce_s:
                    st.pressed, st.changed_at = raw, now
                    st.armed = not raw
                    st.held_at_startup = raw
                    if raw:
                        out.append(Transition(st.channel, True, startup=True))
                continue
            if raw != st.pressed and now - st.since >= self.debounce_s:
                st.pressed, st.changed_at = raw, now
                if not raw:
                    was_armed = st.armed
                    st.armed, st.held_at_startup = True, False
                    if not was_armed:
                        continue           # arming release: not an operator action
                if st.armed:
                    out.append(Transition(st.channel, raw))
                    self.last_activity_wall_t = self._wall_clock()
        return out

    def is_pressed(self, key: str) -> bool:
        """Debounced AND armed pressed state (what software acts on).

        Disabled channels always return False: the poll loop never
        advances their state beyond the initial unknown.
        """
        st = self.channels[key]
        if not st.channel.enabled:
            return False
        return bool(st.pressed and st.armed)

    def to_list(self) -> list:
        return [{
            "key": st.channel.key,
            "channel": st.channel.channel,
            "label": st.channel.label,
            "bcm": st.channel.bcm,
            "physical": st.channel.physical,
            "enabled": st.channel.enabled,
            "level": None if st.level is None else ("HIGH" if st.level else "LOW"),
            "state": ("disabled" if not st.channel.enabled
                      else "unknown" if st.pressed is None
                      else "pressed" if st.pressed else "released"),
            "armed": st.armed,
            "held_at_startup": st.held_at_startup,
        } for st in self.channels.values()]
