"""
GPIO backends — the ONLY place that touches pins.

- `NoHardwareBackend` ("none", the default; Mac development): no GPIO at
  all. Remote inputs read released; Z is unavailable.
- `MockBackend` ("mock"): in-memory pins for tests and for exercising the UI
  on a computer. Records every Z output change.
- `GpioZeroBackend` ("gpio"): the Raspberry Pi, through gpiozero (its default
  pin factory on current Raspberry Pi OS is lgpio, via /dev/gpiochip*).

Application logic never checks the platform: it receives one backend.

Startup order (conservative): Z outputs are claimed FIRST and driven to STOP
(PWM 0, DIR low), THEN the inputs are configured with pull-ups. Callers must
not act on inputs until the remote service has seen them settle (see
remote.py).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from . import pins

log = logging.getLogger(__name__)


class HardwareBackend(ABC):
    name: str = "?"
    has_gpio: bool = False      # real or simulated pins present (remote inputs readable)
    has_z: bool = False         # a Z output stage can be driven

    @abstractmethod
    def read_levels(self) -> dict:
        """Raw electrical level of every remote input: {bcm: True (HIGH) | False (LOW)}."""

    @abstractmethod
    def set_z(self, duty: float, dir_level: bool) -> None:
        """Drive the MD13S: DIR level, then PWM duty (0.0 = STOP … 1.0)."""

    def z_outputs(self) -> dict:
        """Current Z output state for diagnostics: {duty, dir_level}."""
        return {"duty": 0.0, "dir_level": False}

    def close(self) -> None:
        """Release the pins, leaving Z at STOP."""


class NoHardwareBackend(HardwareBackend):
    name = "none"

    def read_levels(self) -> dict:
        return {c.bcm: True for c in pins.REMOTE_CHANNELS}        # all released

    def set_z(self, duty: float, dir_level: bool) -> None:
        if duty > 0:
            raise RuntimeError("No Z hardware (HARDWARE_BACKEND=none)")


class MockBackend(HardwareBackend):
    """In-memory pins. Tests set `levels[bcm]`; `z_log` records outputs."""
    name = "mock"
    has_gpio = True
    has_z = True

    def __init__(self) -> None:
        self.levels: dict = {c.bcm: True for c in pins.REMOTE_CHANNELS}
        self.duty: float = 0.0
        self.dir_level: bool = False
        self.z_log: list = []           # (duty, dir_level) per set_z
        self.fail_next_set: Optional[Exception] = None
        self.closed = False

    def press(self, channel: pins.RemoteChannel) -> None:
        self.levels[channel.bcm] = not pins.REMOTE_ACTIVE_LOW

    def release(self, channel: pins.RemoteChannel) -> None:
        self.levels[channel.bcm] = pins.REMOTE_ACTIVE_LOW

    def read_levels(self) -> dict:
        return dict(self.levels)

    def set_z(self, duty: float, dir_level: bool) -> None:
        if self.fail_next_set is not None:
            exc, self.fail_next_set = self.fail_next_set, None
            self.duty = 0.0             # a failed write must not leave the mock "running"
            raise exc
        self.dir_level = dir_level
        self.duty = duty
        self.z_log.append((duty, dir_level))

    def z_outputs(self) -> dict:
        return {"duty": self.duty, "dir_level": self.dir_level}

    def close(self) -> None:
        self.duty = 0.0
        self.closed = True


class GpioZeroBackend(HardwareBackend):
    """Raspberry Pi GPIO through gpiozero (imported only here)."""
    name = "gpio"
    has_gpio = True
    has_z = True

    def __init__(self, pwm_frequency_hz: float, pin_factory=None) -> None:
        from gpiozero import DigitalInputDevice, DigitalOutputDevice, PWMOutputDevice

        kw = {"pin_factory": pin_factory} if pin_factory is not None else {}
        # 1. Z outputs first, at STOP.
        self._pwm = PWMOutputDevice(pins.Z_PWM_BCM, active_high=True, initial_value=0,
                                    frequency=pwm_frequency_hz, **kw)
        self._dir = DigitalOutputDevice(pins.Z_DIR_BCM, active_high=True, initial_value=False, **kw)
        # 2. Remote inputs: internal pull-up, no library debounce (remote.py debounces).
        self._inputs = {c.bcm: DigitalInputDevice(c.bcm, pull_up=True, bounce_time=None, **kw)
                        for c in pins.REMOTE_CHANNELS}
        log.info("GPIO ready (%s): Z PWM GPIO%d @ %g Hz + DIR GPIO%d at STOP; remote inputs %s pull-up",
                 type(self._pwm.pin_factory).__name__, pins.Z_PWM_BCM, pwm_frequency_hz, pins.Z_DIR_BCM,
                 ", ".join(f"GPIO{c.bcm}" for c in pins.REMOTE_CHANNELS))

    def read_levels(self) -> dict:
        return {bcm: bool(dev.pin.state) for bcm, dev in self._inputs.items()}

    def set_z(self, duty: float, dir_level: bool) -> None:
        if duty <= 0:
            self._pwm.value = 0
            self._dir.value = dir_level
            return
        if bool(self._dir.value) != dir_level:
            self._pwm.value = 0          # never change direction while driving
            self._dir.value = dir_level
        self._pwm.value = duty

    def z_outputs(self) -> dict:
        return {"duty": float(self._pwm.value), "dir_level": bool(self._dir.value)}

    def close(self) -> None:
        try:
            self._pwm.value = 0
            self._dir.value = False
        finally:
            for dev in (self._pwm, self._dir, *self._inputs.values()):
                try:
                    dev.close()
                except Exception:          # pragma: no cover - best effort on shutdown
                    pass


def create_backend(kind: str, pwm_frequency_hz: float) -> HardwareBackend:
    kind = (kind or "none").strip().lower()
    if kind == "gpio":
        return GpioZeroBackend(pwm_frequency_hz)
    if kind == "mock":
        return MockBackend()
    if kind == "none":
        return NoHardwareBackend()
    raise ValueError(f"HARDWARE_BACKEND must be none, mock or gpio (got {kind!r})")
