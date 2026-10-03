"""
HTTP and WebSocket routes.

WebSocket (/ws):
  Handles the real-time bidirectional control channel between the browser
  and the server. Messages are JSON objects with a 'type' field.

  Client → Server message types:
    jog_start      {axis: "X"|"Y", direction: 1|-1}
    jog_heartbeat  {}  — sent every ~100 ms while button held
    jog_stop       {}
    hold           {}
    resume         {}
    stop           {}  — terminates the current job + sends GRBL soft reset
    job_start      {}
    console_send   {command: str}

  Server → Client message types:
    state          — full machine/job state (broadcast every 200 ms)
    console        {direction, line} — individual TX/RX console lines

HTTP:
  POST /upload   — multipart file upload, sets the current job
  GET  /         — serves the static frontend

Dead-man jogging:
  The jog_start message activates jogging for a single axis/direction.
  The browser must send jog_heartbeat every ~100 ms while the button is held.
  If no heartbeat arrives within JOG_TIMEOUT_S, the backend sends a GRBL jog
  cancel (0x85) and stops the jog loop. This ensures that motion stops if the
  browser disconnects, the page is closed, or the network drops.

  Each iteration of the jog loop sends ONE small incremental jog command and
  waits for GRBL's 'ok' before sending the next. At most one jog command is
  queued in GRBL's buffer beyond the currently executing one, bounding runaway.

  GRBL 1.1+ is required for jog mode ($J=). The installed version must be
  verified during physical hardware acceptance testing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, File, UploadFile, WebSocket, WebSocketDisconnect

from .. import config
from ..grbl.communicator import GRBLCommunicator
from ..jobs.current_job import CurrentJob, load_job_from_file
from ..jobs.runner import JobRunner
from ..state import AppState
from .ws import ConnectionManager

log = logging.getLogger(__name__)

router = APIRouter()

# ------------------------------------------------------------------ #
# Module-level references — set by main.py at startup                 #
# ------------------------------------------------------------------ #

_state: Optional[AppState] = None
_comm: Optional[GRBLCommunicator] = None
_ws_manager: Optional[ConnectionManager] = None
_current_job: Optional[CurrentJob] = None
_runner_task: Optional[asyncio.Task] = None

# Jog state — last heartbeat time and active axis/direction
_jog_axis: Optional[str] = None
_jog_direction: int = 1
_jog_last_heartbeat: float = 0.0
_jog_task: Optional[asyncio.Task] = None


def init(
    state: AppState,
    comm: GRBLCommunicator,
    ws_manager: ConnectionManager,
) -> None:
    """Called from main.py after all objects are created."""
    global _state, _comm, _ws_manager
    _state = state
    _comm = comm
    _ws_manager = ws_manager


# ------------------------------------------------------------------ #
# Jog management                                                       #
# ------------------------------------------------------------------ #

def _jog_step_mm() -> float:
    """Distance per incremental jog command at the configured speed and interval."""
    speed = config.JOG_SPEED_MM_MIN
    if speed is None:
        return 0.0
    # One step covers exactly one send-interval worth of motion at configured speed.
    return speed * config.JOG_SEND_INTERVAL_S / 60.0


async def _jog_loop(axis: str, direction: int) -> None:
    """
    Send small incremental jog commands while heartbeats are fresh.

    Each iteration:
    1. Check heartbeat timeout → cancel jog if expired.
    2. Send one incremental $J= command.
    3. Wait for GRBL ok (this paces the loop to GRBL's buffer consumption).
    4. Repeat.

    Max runaway on connection loss ≈ 1–2 jog step distances.
    """
    global _jog_axis, _jog_direction, _jog_last_heartbeat

    step = _jog_step_mm()
    if step == 0:
        log.warning("Jog step is zero — jogging is disabled (JOG_SPEED_MM_MIN not set)")
        return

    speed = config.JOG_SPEED_MM_MIN
    assert speed is not None

    log.info("Jog loop started: %s%+d at %.1f mm/min, %.3f mm/step",
             axis, direction, speed, step)

    try:
        while True:
            age = time.monotonic() - _jog_last_heartbeat
            if age > config.JOG_TIMEOUT_S:
                log.debug("Jog heartbeat timeout (%.2f s) — stopping", age)
                break

            distance = direction * step
            cmd = f"$J=G91 {axis}{distance:.3f} F{speed:.0f}"
            try:
                result = await _comm.send_command(cmd, timeout=3.0)  # type: ignore[union-attr]
                if result.startswith("error:"):
                    log.warning("Jog command error: %s", result)
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("Jog send exception: %s", exc)
                break
    finally:
        # Send jog cancel as a safety net regardless of why we stopped.
        log.info("Jog loop ended — sending cancel (0x85)")
        if _comm:
            await _comm.send_realtime(b"\x85")
        _jog_axis = None


async def _start_jog(axis: str, direction: int) -> None:
    """Activate the jog loop for the given axis and direction."""
    global _jog_axis, _jog_direction, _jog_last_heartbeat, _jog_task

    if config.JOG_SPEED_MM_MIN is None:
        log.warning("Jog requested but JOG_SPEED_MM_MIN is not configured")
        return

    if _state and _state.grbl_state not in ("Idle", "Jog"):
        log.debug("Jog rejected: GRBL state is %s", _state.grbl_state)
        return

    # Cancel previous jog task if still running
    if _jog_task and not _jog_task.done():
        _jog_task.cancel()
        try:
            await _jog_task
        except (asyncio.CancelledError, Exception):
            pass

    _jog_axis = axis
    _jog_direction = direction
    _jog_last_heartbeat = time.monotonic()
    _jog_task = asyncio.create_task(_jog_loop(axis, direction), name="jog-loop")


async def _stop_jog() -> None:
    """Stop the jog loop immediately."""
    global _jog_task, _jog_axis
    _jog_axis = None
    if _jog_task and not _jog_task.done():
        _jog_task.cancel()
        try:
            await _jog_task
        except (asyncio.CancelledError, Exception):
            pass


# ------------------------------------------------------------------ #
# Job management                                                       #
# ------------------------------------------------------------------ #

async def _start_job() -> None:
    global _current_job, _runner_task

    if _current_job is None:
        log.warning("job_start: no current job")
        return
    if _current_job.is_active:
        log.warning("job_start: job already running")
        return
    if _state and not _state.connected:
        log.warning("job_start: not connected to GRBL")
        return
    if _state and _state.grbl_state not in ("Idle",):
        log.warning("job_start: GRBL state is %s — must be Idle", _state.grbl_state)
        return

    assert _comm is not None
    _current_job.state = "ready"
    _current_job.current_line = 0
    _current_job.error = None
    runner = JobRunner(_comm, _current_job)
    _runner_task = asyncio.create_task(runner.run(), name="job-runner")


async def _stop_job() -> None:
    global _runner_task

    if _state and _current_job and _current_job.is_active:
        _current_job.state = "stopped"

    if _comm:
        await _comm.send_realtime(b"\x18")  # GRBL soft reset

    if _runner_task and not _runner_task.done():
        _runner_task.cancel()
        try:
            await _runner_task
        except (asyncio.CancelledError, Exception):
            pass
    _runner_task = None


# ------------------------------------------------------------------ #
# WebSocket message dispatcher                                         #
# ------------------------------------------------------------------ #

async def _handle_message(data: dict, ws: WebSocket) -> None:
    global _jog_last_heartbeat, _current_job

    msg_type = data.get("type")

    if msg_type == "jog_start":
        axis = data.get("axis", "X").upper()
        direction = int(data.get("direction", 1))
        if axis not in ("X", "Y"):
            return
        await _start_jog(axis, direction)

    elif msg_type == "jog_heartbeat":
        _jog_last_heartbeat = time.monotonic()

    elif msg_type == "jog_stop":
        await _stop_jog()

    elif msg_type == "hold":
        if _comm:
            await _comm.send_realtime(b"!")  # GRBL feed hold
        if _current_job and _current_job.state == "running":
            _current_job.state = "held"

    elif msg_type == "resume":
        if _comm:
            await _comm.send_realtime(b"~")  # GRBL cycle start
        if _current_job and _current_job.state == "held":
            _current_job.state = "running"

    elif msg_type == "stop":
        await _stop_job()

    elif msg_type == "job_start":
        await _start_job()

    elif msg_type == "console_send":
        command = str(data.get("command", "")).strip()
        if not command:
            return
        if _current_job and _current_job.is_active:
            log.debug("Console blocked: job is active")
            return
        if _comm:
            try:
                result = await _comm.send_command(command)
                log.debug("Console: %s → %s", command, result)
            except Exception as exc:
                log.debug("Console error: %s", exc)
    else:
        log.debug("Unknown WS message type: %s", msg_type)


# ------------------------------------------------------------------ #
# WebSocket endpoint                                                   #
# ------------------------------------------------------------------ #

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    assert _ws_manager is not None
    assert _state is not None

    await _ws_manager.connect(ws)
    # Send full state immediately so a reconnecting client syncs instantly
    await ws.send_json(_state.to_dict())

    try:
        while True:
            data = await ws.receive_json()
            await _handle_message(data, ws)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("WebSocket error: %s", exc)
    finally:
        _ws_manager.disconnect(ws)


# ------------------------------------------------------------------ #
# HTTP: file upload                                                    #
# ------------------------------------------------------------------ #

@router.post("/upload")
async def upload_gcode(file: UploadFile = File(...)):
    global _current_job

    if _current_job and _current_job.is_active:
        return {
            "success": False,
            "error": "Cannot replace job while one is active. Stop the current job first.",
        }

    # Ensure jobs directory exists
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)

    destination = config.JOBS_DIR / file.filename  # type: ignore[arg-type]
    content = await file.read()
    destination.write_bytes(content)

    try:
        job = load_job_from_file(destination)
    except Exception as exc:
        return {"success": False, "error": f"Could not parse file: {exc}"}

    _current_job = job

    # Sync job info into AppState for WebSocket broadcasts
    if _state:
        from ..state import JobInfo
        _state.job = JobInfo(
            filename=job.filename,
            total_lines=job.total_lines,
        )

    log.info("Job uploaded: %s (%d sendable lines)", job.filename, job.total_lines)
    return {
        "success": True,
        "filename": job.filename,
        "total_lines": job.total_lines,
    }


# ------------------------------------------------------------------ #
# HTTP: current state (for debugging / REST clients)                  #
# ------------------------------------------------------------------ #

@router.get("/api/state")
async def get_state():
    if _state:
        return _state.to_dict()
    return {"error": "not initialised"}


# ------------------------------------------------------------------ #
# Accessors for main.py background loops                              #
# ------------------------------------------------------------------ #

def get_current_job() -> Optional[CurrentJob]:
    return _current_job


def sync_job_state_to_appstate() -> None:
    """Keep AppState.job in sync with the CurrentJob object for WS broadcasts."""
    if _state is None:
        return
    job = _current_job
    if job is None:
        _state.job = None
        return

    import time
    from ..state import JobInfo
    if _state.job is None or _state.job.filename != job.filename:
        _state.job = JobInfo(filename=job.filename, total_lines=job.total_lines)

    sj = _state.job
    sj.state = job.state
    sj.current_line = job.current_line
    sj.started_at = job.started_at
    sj.error = job.error
