"""
Raspberry Pi hardware integration: GPIO backends, RF remote inputs, manual Z.

The pin map lives ONLY in `pins.py`. `build()` assembles the service from
`app.config`; application code uses the service, never GPIO directly.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from .backend import HardwareBackend, NoHardwareBackend, create_backend
from .remote import RemoteInputs
from .service import HardwareService
from .z import ZController

log = logging.getLogger(__name__)


def build(cfg, *, interlock: Callable, on_remote_stop: Callable[[str], Awaitable[None]],
          event: Callable[[str, str], None], backend: HardwareBackend | None = None,
          z_duty: float | None = None) -> HardwareService:
    """Create the backend (Z outputs at STOP first), the remote inputs and the ONE Z controller.

    `z_duty` overrides the config default (used when the operator has
    persisted a Z speed via the UI). None → use `cfg.Z_PWM_DUTY`.
    """
    error = None
    if backend is None:
        try:
            backend = create_backend(cfg.HARDWARE_BACKEND, cfg.Z_PWM_FREQUENCY_HZ)
        except Exception as exc:
            # remote + Z unavailable (the no-motion direction) — reported, never hidden
            error = f"GPIO backend '{cfg.HARDWARE_BACKEND}' failed: {type(exc).__name__}: {exc}"
            log.error("%s — running WITHOUT remote inputs or Z", error)
            event("hardware", error)
            backend = NoHardwareBackend()
    duty = cfg.Z_PWM_DUTY if z_duty is None else z_duty
    z = ZController(backend, duty=duty, timeout_s=cfg.Z_TIMEOUT_S, max_run_s=cfg.Z_MAX_RUN_S,
                    dir_invert=cfg.Z_DIR_INVERT, interlock=interlock, event=event)
    remote = RemoteInputs(backend, debounce_s=cfg.REMOTE_DEBOUNCE_S)
    svc = HardwareService(backend, remote, z, poll_interval_s=cfg.REMOTE_POLL_INTERVAL_S,
                          on_remote_stop=on_remote_stop, event=event)
    svc.backend_error = error
    return svc
