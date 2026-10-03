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

from . import config
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
    # Startup
    http_module.init(app_state, communicator, ws_manager)
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
    yield
    # Shutdown
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
