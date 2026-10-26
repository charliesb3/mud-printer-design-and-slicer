"""
Hardware service: owns the GPIO backend, the remote inputs and the Z
controller, and runs ONE polling loop (remote sampling + Z dead-man checks).

Remote functions call the same machine operations as the UI:
- CH1 REMOTE STOP press → `on_remote_stop` (api/http.py: the same stop the
  application uses everywhere — latches, stops Z, cancels jog, feed hold);
- CH2 Z UP / CH4 Z DOWN → the common ZController (sources remote_up /
  remote_down; both held = contradictory = STOP);
- CH3 is DISABLED (sixteenth-pass remap): no transitions, no arming,
  pin stays pulled up. See pins.py for the full remap rationale.
- CH5 extrusion pause / resume (reserved — no pump output), CH6 SPARE:
  logged only.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from . import pins
from .backend import HardwareBackend
from .remote import RemoteInputs, Transition
from .z import DOWN, UP, ZController

log = logging.getLogger(__name__)

_Z_SOURCES = {pins.Z_UP.key: ("remote_up", UP), pins.Z_DOWN.key: ("remote_down", DOWN)}


class HardwareService:
    def __init__(self, backend: HardwareBackend, remote: RemoteInputs, z: ZController, *,
                 poll_interval_s: float,
                 on_remote_stop: Callable[[str], Awaitable[None]],
                 event: Callable[[str, str], None] = lambda kind, text: None) -> None:
        self.backend = backend
        self.remote = remote
        self.z = z
        self.poll_interval_s = poll_interval_s
        self._on_remote_stop = on_remote_stop
        self._event = event
        self._task: Optional[asyncio.Task] = None
        self.loop_error: Optional[str] = None
        self.backend_error: Optional[str] = None      # configured backend failed → running without GPIO

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hardware-poll")

    async def stop(self) -> None:
        """Shutdown: Z STOP first, then stop polling and release the pins."""
        self.z.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            self.backend.close()
        except Exception as exc:          # pragma: no cover - best effort
            log.error("GPIO release failed: %s", exc)

    async def _loop(self) -> None:
        while True:
            try:
                await self.step()
                self.loop_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:       # never let the loop die silently: STOP Z, report, retry
                self.loop_error = f"{type(exc).__name__}: {exc}"
                log.exception("Hardware poll failed — Z STOP")
                self.z.stop_all(f"hardware poll error: {exc}")
            await asyncio.sleep(self.poll_interval_s)

    async def step(self) -> None:
        """One sample: debounced transitions, held remote Z refresh, Z dead-man."""
        for t in self.remote.poll():
            await self._transition(t)
        for key, (source, direction) in _Z_SOURCES.items():
            if self.remote.is_pressed(key):
                self.z.refresh(source)
        self.z.tick()

    async def _transition(self, t: Transition) -> None:
        ch = t.channel
        if not ch.enabled:
            # Belt-and-braces: RemoteInputs.poll already filters disabled
            # channels, so no Transition for them is ever emitted. This
            # guard is here so a future bug in `poll` can't accidentally
            # wake a disabled channel into motion.
            return
        what = "pressed" if t.pressed else "released"
        if t.startup:
            msg = f"Remote CH{ch.channel} {ch.label}: closed at startup — not armed until released"
            self._event("remote", msg)
            log.warning("%s", msg)
            if ch is pins.REMOTE_STOP:
                # a closed STOP contact can only mean "stop": the no-motion direction
                await self._on_remote_stop(f"CH{ch.channel} closed at startup")
            return
        self._event("remote", f"Remote CH{ch.channel} {ch.label} {what}")
        log.info("Remote CH%d %s %s (GPIO%d)", ch.channel, ch.label, what, ch.bcm)
        if ch is pins.REMOTE_STOP:
            if t.pressed:
                await self._on_remote_stop(f"remote CH{ch.channel}")
        elif ch.key in _Z_SOURCES:
            source, direction = _Z_SOURCES[ch.key]
            if t.pressed:
                refusal = self.z.request(source, direction)
                if refusal:
                    self._event("remote", f"Remote {ch.label} refused: {refusal}")
                    log.info("Remote %s refused: %s", ch.label, refusal)
            else:
                self.z.release(source)
        # EXTRUSION, AUX1, AUX2: no machine action (no output defined / reserved)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend.name,
            "gpio": self.backend.has_gpio,
            "remote": self.remote.to_list(),
            "debounce_ms": round(self.remote.debounce_s * 1000),
            # Wall-clock seconds since the last ARMED remote-channel
            # transition; None if nothing has been seen since boot. UI
            # uses this to warn "handheld may be asleep" after a long
            # silence. The Kaleinavi receiver has no "RF link lost"
            # output, so this is the best proxy the Pi has.
            "remote_last_activity_wall_t": self.remote.last_activity_wall_t,
            "z": self.z.to_dict(),
            "z_pins": {"pwm_bcm": pins.Z_PWM_BCM, "pwm_physical": pins.Z_PWM_PHYSICAL,
                       "dir_bcm": pins.Z_DIR_BCM, "dir_physical": pins.Z_DIR_PHYSICAL},
            "loop_error": self.loop_error,
            "backend_error": self.backend_error,
        }
