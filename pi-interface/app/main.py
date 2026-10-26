"""
Mud Printer — Raspberry Pi Printer Interface
FastAPI application entry point.

Run for development on Mac:
    uvicorn app.main:app --reload --port 8000

Run on the Pi (via systemd or directly):
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, hardware, sdnotify, z_setting
from .api import http as http_module
from .api.ws import ConnectionManager
from .grbl.communicator import GRBLCommunicator
from .grbl.transport import SerialTransport
from .state import AppState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

# Shared application objects
app_state = AppState()
ws_manager = ConnectionManager()
communicator = GRBLCommunicator(app_state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — hardware FIRST: Z outputs driven to STOP and remote inputs
    # configured (pull-ups) before any machine action is possible.
    http_module.init(app_state, communicator, ws_manager)
    # Load the operator-adjusted Z duty, if persisted; fall back to .env.
    persisted_duty = z_setting.load(config.Z_SETTING_PATH)
    if persisted_duty is not None:
        log.info("Z duty loaded from %s: %.2f (overriding Z_PWM_DUTY=%.2f)",
                 config.Z_SETTING_PATH, persisted_duty, config.Z_PWM_DUTY)
    effective_z_duty = persisted_duty if persisted_duty is not None else config.Z_PWM_DUTY
    app_state.hardware = hardware.build(
        config,
        interlock=app_state.remote_stop.refusal,
        on_remote_stop=http_module.remote_stop,
        event=app_state.add_hw_event,
        z_duty=effective_z_duty,
    )
    app_state.hardware.start()
    log.info("Hardware backend: %s (manual Z %s)", app_state.hardware.backend.name,
             f"duty {config.Z_PWM_DUTY:.2f}" if config.Z_PWM_DUTY > 0 else "DISABLED: Z_PWM_DUTY not set")
    from . import machine_profile
    app_state.machine_profile, app_state.machine_profile_problem = machine_profile.load(config.MACHINE_PROFILE_PATH)
    log.info("Machine profile: %s", app_state.machine_profile.get("name"))
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(_connection_loop(), name="grbl-connection")
    asyncio.create_task(_broadcast_loop(), name="ws-broadcast")
    log.info("Mud Printer Interface started on port %d", config.PORT)
    log.info("Serial port: %s @ %d baud", config.SERIAL_PORT, config.BAUD_RATE)
    if config.JOG_SPEED_MM_MIN is None:
        log.warning("Jogging disabled — JOG_SPEED_MM_MIN not set in .env")
    else:
        log.warning(
            "Jog speed %.1f mm/min — NOT YET VALIDATED on physical machine",
            config.JOG_SPEED_MM_MIN,
        )
    watchdog = sdnotify.watchdog_interval_s()
    if watchdog:
        asyncio.create_task(sdnotify.watchdog_loop(watchdog), name="systemd-watchdog")
    sdnotify.notify("READY=1")
    try:
        yield
    finally:
        # Shutdown — Z STOP first, whatever else fails
        sdnotify.notify("STOPPING=1")
        await app_state.hardware.stop()
        await communicator.stop()
    log.info("Mud Printer Interface stopped")


app = FastAPI(title="Mud Printer Interface", version="0.1.0", lifespan=lifespan)


# ------------------------------------------------------------------ #
# Background loops                                                     #
# ------------------------------------------------------------------ #

async def _connection_loop() -> None:
    """
    Attempt to connect to the GRBL controller. On disconnection or failure,
    wait and retry. This runs for the lifetime of the application.
    """
    while True:
        if app_state.connected:
            # Already connected; the communicator's read loop will exit
            # and set connected=False when the port drops. Check periodically.
            await asyncio.sleep(1.0)
            continue

        log.info("Attempting GRBL connection on %s ...", config.SERIAL_PORT)
        transport = SerialTransport(config.SERIAL_PORT, config.BAUD_RATE)
        try:
            await transport.connect()
            log.info("GRBL serial port opened")
            await communicator.start(transport)
            # Wait for the communicator's tasks to exit (they exit on disconnect)
            if communicator._read_task:
                try:
                    await communicator._read_task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception as exc:
            log.info("Cannot connect to GRBL (%s): %s — retrying in %ds",
                     config.SERIAL_PORT, exc, config.RECONNECT_INTERVAL_S)
            app_state.mark_disconnected()
            try:
                await transport.close()
            except Exception:
                pass

        await asyncio.sleep(config.RECONNECT_INTERVAL_S)


async def _broadcast_loop() -> None:
    """Broadcast current application state to all WebSocket clients every 200 ms."""
    while True:
        await asyncio.sleep(0.2)
        try:
            http_module.sync_job_state_to_appstate()
            if ws_manager.client_count > 0:
                await ws_manager.broadcast(app_state.to_dict())
        except Exception as exc:
            log.debug("Broadcast error: %s", exc)


# ------------------------------------------------------------------ #
# Routes                                                               #
# ------------------------------------------------------------------ #

app.include_router(http_module.router)

# Serve the static frontend
_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
