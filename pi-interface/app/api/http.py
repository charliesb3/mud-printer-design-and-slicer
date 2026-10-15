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
    resume         {}  — resumes a feed hold; never confirms a reposition
    stop           {}  — terminates the current job + sends GRBL soft reset
    job_start      {}
    confirm_reposition {lift: int}  — 0-based index of the lift about to start
    set_origin     {}  — print origin = current XY (work XY zero, G54)
    set_prime      {}  — prime position = current XY (work coordinates)
    prime          {}  — move to the prime position (operator then runs the pump)
    outline        {}  — trace the loaded job's XY bounds at jog speed (preview in state)
    feed_override  {step: -10|-1|1|10|"reset"}  — GRBL real-time feed override
    adjust_lift_end {lift: int, at: int}  — move the end of the adjustable lift to a clean cut
    set_observed_layer {thickness: float}  — operator-observed physical layer thickness (in)
    measure_lift_height {height: float}  — measured height of the completed layers of this lift (in)
    apply_lift_parameters {lift: int, target: float, thickness: float}  — operator target lift
                   height + layer thickness → the safe lift end (Hold to Apply)
    restore_planned_lifts {}
    read_grbl      {what: "settings"|"parser"|"offsets"}  — read-only $$ / $G / $#
    console_send   {command: str}

  Server → Client message types:
    state          — full machine/job state (broadcast every 200 ms, and
                     immediately after any job/control action)
    console        {direction, line} — individual TX/RX console lines
    notice         {level, message} — sent only to the client whose request
                     was refused (e.g. a stale reposition confirmation)

Synchronized clients:
  Every client (laptop browser, Pi touchscreen kiosk, …) is an equal view of
  the one server-owned machine/job state. Any client's action changes that
  state and the change is broadcast to all. Connecting or disconnecting a
  client never affects the job.

HTTP:
  POST /upload   — multipart file upload, sets the current job
  GET  /api/job/path — the loaded job's XY path (2D live view), from its G-code
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
from ..jobs.current_job import CurrentJob, JobFormatError, load_job_from_file
from ..jobs.motion import path_payload
from ..jobs.runner import DRAIN_COMMAND, JobRunner
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

# Positioning move (Prime) in progress
_position_task: Optional[asyncio.Task] = None


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
            # G21 in the jog line (non-modal in GRBL jogging): steps and speed
            # are millimetres whatever units mode jobs / positioning left active.
            cmd = f"$J=G21 G91 {axis}{distance:.3f} F{speed:.0f}"
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

    # A job owns the machine while running or held. While it AWAITS a frame
    # reposition the operator may jog (and re-zero) before confirming.
    if _current_job and _current_job.state in ("running", "held"):
        log.debug("Jog rejected: job is %s", _current_job.state)
        return
    if _state and _state.setup.moving:
        log.debug("Jog rejected: positioning move in progress")
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

async def _start_job() -> Optional[str]:
    """Start the current job. Returns a refusal reason, or None when started."""
    global _current_job, _runner_task

    if _current_job is None:
        log.warning("job_start: no current job")
        return "No job loaded."
    if not _current_job.executable:
        log.warning("job_start: job is not executable")
        return ("This job is not executable — it describes the building and its lifts only "
                "(unresolved: " + ", ".join(u["id"] for u in _current_job.unresolved) + ").")
    if _current_job.is_active:
        log.warning("job_start: job already running")
        return "The job is already running."
    if _state and not _state.connected:
        log.warning("job_start: not connected to GRBL")
        return "Not connected to GRBL."
    if _state and _state.grbl_state not in ("Idle",):
        log.warning("job_start: GRBL state is %s — must be Idle", _state.grbl_state)
        return f"GRBL must be Idle to start (it is {_state.grbl_state})."
    if _state and _state.setup.moving:
        return "A positioning move is in progress."
    origin_feed = None
    if _current_job.kind == "building":
        # A building job is in work coordinates from the print origin and
        # starts by returning there.
        if _state is None or not _state.setup.origin_set:
            return "Set the print origin first (jog to it, then Set Origin)."
        origin_feed = _positioning_feed_in_min()
        if origin_feed is None:
            return ("Moves to the origin use the jog speed, and jogging is disabled "
                    "(JOG_SPEED_MM_MIN not set).")

    assert _comm is not None
    _current_job.reset_progress()
    runner = JobRunner(_comm, _current_job, origin_feed_in_min=origin_feed)
    _runner_task = asyncio.create_task(runner.run(), name="job-runner")
    return None


async def _stop_job() -> None:
    global _runner_task

    if _state and _current_job and _current_job.is_active:
        _current_job.state = "stopped"

    if _comm:
        await _comm.send_realtime(b"\x18")  # GRBL soft reset

    await _cancel_positioning()

    if _runner_task and not _runner_task.done():
        _runner_task.cancel()
        try:
            await _runner_task
        except (asyncio.CancelledError, Exception):
            pass
    _runner_task = None


def _confirm_reposition(lift) -> Optional[str]:
    """Accept the operator's frame-reposition confirmation, or say why not."""
    if _current_job is None:
        return "No job loaded."
    if not isinstance(lift, int) or isinstance(lift, bool):
        return "Confirmation must name the lift it confirms."
    if _state is None or not _state.connected:
        return "Not connected to GRBL."
    if _current_job.state == "awaiting_reposition":
        if _state.grbl_state != "Idle":
            return f"GRBL must be Idle to continue (it is {_state.grbl_state})."
        if _state.setup.moving or (_jog_task and not _jog_task.done()):
            return "Wait until the machine has stopped moving."
        if _current_job.kind == "building" and not _state.setup.origin_set:
            return "The print origin is not established — Set Origin before continuing."
    return _current_job.confirm_reposition(lift)


# ------------------------------------------------------------------ #
# Print origin / prime position (operator machine setup, 2026-10-14)   #
# ------------------------------------------------------------------ #
# Priming is MACHINE OPERATION, never part of the .mudjob toolpath. The
# operator jogs to the print origin → Set Origin (work XY zero), jogs to a
# nearby safe spot → Set Prime Position, Prime moves there (the pump is run
# by hand), Start returns to the origin before the building toolpath. Allowed
# while no job is running — including while a job AWAITS a frame reposition
# (re-zero after the frame raise, prime again). Positioning moves use the
# validated jog speed.

def _positioning_feed_in_min() -> Optional[float]:
    """Feed (in/min) of Pi positioning moves = the jog speed; None = disabled."""
    if config.JOG_SPEED_MM_MIN is None:
        return None
    return config.JOG_SPEED_MM_MIN / 25.4


def _setup_refusal() -> Optional[str]:
    """Why origin / prime actions are not possible right now (None: they are)."""
    if _state is None or _comm is None or not _state.connected:
        return "Not connected to GRBL."
    if _current_job and _current_job.state in ("running", "held"):
        return "A job is running — wait for a frame reposition or stop the job."
    if _state.setup.moving:
        return "A positioning move is in progress."
    if _jog_task and not _jog_task.done():
        return "Release the jog button first."
    if _state.grbl_state != "Idle":
        return f"GRBL must be Idle (it is {_state.grbl_state})."
    return None


async def _fresh_status(timeout: float = 2.0) -> bool:
    """Wait for a status report newer than now."""
    assert _state is not None
    seq, deadline = _state.status_seq, time.monotonic() + timeout
    while _state.status_seq == seq:
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


async def _set_origin() -> Optional[str]:
    refusal = _setup_refusal()
    if refusal:
        return refusal
    assert _comm is not None and _state is not None
    try:
        await _comm.send_command("$$")          # read-only: learn report units ($13)
        for cmd in ("G54", "G10 L20 P1 X0 Y0"):  # work XY zero here (Z untouched)
            result = await _comm.send_command(cmd)
            if result.startswith("error:"):
                return f"GRBL refused {cmd}: {result}"
    except Exception as exc:
        return f"Set Origin failed: {exc}"
    if not await _fresh_status():
        return "No status report from GRBL after setting the origin."
    setup = _state.setup
    setup.origin_mpos = (_state.mpos_x, _state.mpos_y) if _state.pos_kind == "mpos" else None
    setup.origin_set = True
    setup.note = None
    log.info("Print origin set at machine XY %s", setup.origin_mpos)
    return None


async def _set_prime() -> Optional[str]:
    refusal = _setup_refusal()
    if refusal:
        return refusal
    assert _state is not None
    if not _state.setup.origin_set:
        return "Set the print origin first — the prime position is stored relative to it."
    if _state.report_units is None:
        return "GRBL report units ($13) unknown — Set Origin again."
    await _fresh_status()
    work = _state.work_position_in()
    if work is None:
        return "The current work position is not known."
    _state.setup.prime = work
    _state.setup.note = None
    log.info("Prime position set at work XY %.4f, %.4f in", *work)
    return None


async def _prime() -> Optional[str]:
    """Start the move to the prime position (a background task)."""
    global _position_task
    refusal = _setup_refusal()
    if refusal:
        return refusal
    assert _state is not None and _comm is not None
    setup = _state.setup
    if not setup.origin_set:
        return "Set the print origin first."
    if not setup.prime:
        return "Set a prime position first (jog there, then Set Prime Position)."
    feed = _positioning_feed_in_min()
    if feed is None:
        return "Moves use the jog speed, and jogging is disabled (JOG_SPEED_MM_MIN not set)."
    line = f"G54 G20 G90 G1 X{setup.prime[0]:.4f} Y{setup.prime[1]:.4f} F{feed:.1f}"
    setup.moving = "prime"
    setup.note = None
    _position_task = asyncio.create_task(_run_positioning(line), name="positioning")
    return None


async def _run_positioning(line: str) -> None:
    assert _state is not None and _comm is not None
    setup = _state.setup
    try:
        for cmd in (line, DRAIN_COMMAND):        # the move, then wait until it has finished
            result = await _comm.send_command(cmd, live=True)
            if result.startswith("error:"):
                setup.note = f"Move refused by GRBL ({cmd}): {result}"
                return
        await _fresh_status()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        setup.note = f"Move failed: {exc}"
    finally:
        setup.moving = None


# ------------------------------------------------------------------ #
# Outline: trace the job's XY bounds (controller feature, like UGS's   #
# "Outline" — separate from Prime; never changes the job)              #
# ------------------------------------------------------------------ #

async def _outline() -> Optional[str]:
    global _position_task
    refusal = _setup_refusal()
    if refusal:
        return refusal
    assert _state is not None
    if not _state.setup.origin_set:
        return "Set the print origin first — the outline is in work coordinates."
    preview = _current_job.outline() if _current_job else None
    if preview is None:
        return "Load an executable job first — Outline traces its XY bounds."
    feed = _positioning_feed_in_min()
    if feed is None:
        return "Outline moves at the jog speed, and jogging is disabled (JOG_SPEED_MM_MIN not set)."
    lines = [f"G54 G20 G90 G1 X{x:.4f} Y{y:.4f} F{feed:.1f}" for x, y in preview["corners"]]
    _state.setup.moving = "outline"
    _state.setup.note = None
    _position_task = asyncio.create_task(_run_moves(lines), name="outline")
    return None


async def _run_moves(lines: list) -> None:
    """Positioning moves (Outline) then wait until finished."""
    assert _state is not None and _comm is not None
    setup = _state.setup
    try:
        for cmd in [*lines, DRAIN_COMMAND]:
            result = await _comm.send_command(cmd, live=True)
            if result.startswith("error:"):
                setup.note = f"Move refused by GRBL ({cmd}): {result}"
                return
        await _fresh_status()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        setup.note = f"Move failed: {exc}"
    finally:
        setup.moving = None


# ------------------------------------------------------------------ #
# Feed override (GRBL 1.1 real-time bytes; value read back from Ov:)  #
# ------------------------------------------------------------------ #

FEED_OVERRIDE_BYTES = {"reset": b"\x90", 10: b"\x91", -10: b"\x92", 1: b"\x93", -1: b"\x94"}


async def _feed_override(step) -> Optional[str]:
    if isinstance(step, bool) or step not in FEED_OVERRIDE_BYTES:
        return "Feed override steps are -10, -1, +1, +10 or reset."
    if _state is None or _comm is None or not _state.connected:
        return "Not connected to GRBL."
    await _comm.send_realtime(FEED_OVERRIDE_BYTES[step])
    log.info("Feed override %s", step)
    return None


# ------------------------------------------------------------------ #
# Read-only GRBL queries (diagnostics)                                 #
# ------------------------------------------------------------------ #

READ_QUERIES = {"settings": "$$", "parser": "$G", "offsets": "$#"}


async def _read_grbl(what) -> Optional[str]:
    if what not in READ_QUERIES:
        return "Unknown query."
    refusal = _setup_refusal()
    if refusal:
        return refusal
    assert _comm is not None
    try:
        result = await _comm.send_command(READ_QUERIES[what])
    except Exception as exc:
        return f"Query failed: {exc}"
    return None if result == "ok" else f"GRBL answered {result}"


# ------------------------------------------------------------------ #
# Adaptive lift boundaries (operator; see jobs/layout.py)              #
# ------------------------------------------------------------------ #

def _job_number(value, low: float, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if low <= v <= high else None


async def _lift_message(msg_type: str, data: dict) -> tuple:
    """(refusal, info) for the lift-adjustment messages."""
    job = _current_job
    if job is None or job.kind != "building":
        return ("Load a building job first.", None)
    if msg_type == "set_observed_layer":
        v = _job_number(data.get("thickness"), 0.05, 24.0)
        if v is None:
            return ("Layer thickness must be a number between 0.05 and 24 in.", None)
        job.observed_layer = v
        return (None, None)
    if msg_type == "measure_lift_height":
        h = _job_number(data.get("height"), 0.05, 1000.0)
        if h is None:
            return ("Measured height must be a positive number (in).", None)
        L = job.layout
        k = min(job.current_lift, L.count - 1)
        a, b = L.range_of(k)
        done = L.completed_layers(a, b, job.current_line)
        if done < 1:
            return ("No layer of this lift is complete yet.", None)
        job.observed_layer = round(h / done, 4)
        return (None, f"Observed layer thickness {job.observed_layer:g} in ({h:g} in over {done} layers).")
    if msg_type == "restore_planned_lifts":
        return (job.restore_planned_lifts(), None)
    if msg_type == "apply_lift_parameters":
        lift = data.get("lift")
        target = _job_number(data.get("target"), 0.1, 1000.0)
        thickness = _job_number(data.get("thickness"), 0.05, 24.0)
        if not isinstance(lift, int) or isinstance(lift, bool) or target is None or thickness is None:
            return ("Lift parameters need the lift, a target height and a layer thickness (in).", None)
        return job.apply_lift_parameters(lift, target, thickness)
    lift, at = data.get("lift"), data.get("at")
    if not isinstance(lift, int) or isinstance(lift, bool) or not isinstance(at, int) or isinstance(at, bool):
        return ("An adjustment must name the lift and the new end.", None)
    return job.adjust_lift_end(lift, at)


async def _cancel_positioning() -> None:
    global _position_task
    if _position_task and not _position_task.done():
        _position_task.cancel()
        try:
            await _position_task
        except (asyncio.CancelledError, Exception):
            pass
    _position_task = None
    if _state:
        _state.setup.moving = None


async def _notify(ws: WebSocket, message: str, level: str = "warn") -> None:
    try:
        await ws.send_json({"type": "notice", "level": level, "message": message})
    except Exception:
        pass


async def broadcast_state() -> None:
    """Push the current state to every client now (not waiting for the 200 ms loop)."""
    sync_job_state_to_appstate()
    if _ws_manager and _state and _ws_manager.client_count > 0:
        await _ws_manager.broadcast(_state.to_dict())


# ------------------------------------------------------------------ #
# WebSocket message dispatcher                                         #
# ------------------------------------------------------------------ #

# Messages that change machine/job state: broadcast to all clients right after.
_STATE_CHANGING = {"hold", "resume", "stop", "job_start", "confirm_reposition",
                   "set_origin", "set_prime", "prime", "outline", "feed_override", "read_grbl",
                   "adjust_lift_end", "set_observed_layer", "measure_lift_height", "restore_planned_lifts",
                   "apply_lift_parameters"}


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
        refusal = await _start_job()
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "feed_override":
        refusal = await _feed_override(data.get("step"))
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "read_grbl":
        refusal = await _read_grbl(data.get("what"))
        if refusal:
            await _notify(ws, refusal)

    elif msg_type in ("adjust_lift_end", "set_observed_layer", "measure_lift_height", "restore_planned_lifts",
                      "apply_lift_parameters"):
        refusal, info = await _lift_message(msg_type, data)
        if refusal:
            await _notify(ws, refusal)
        elif info:
            await _notify(ws, info, level="info")
        if msg_type == "adjust_lift_end" and not refusal:
            log.info("Lift %s end moved by the operator to line %s", data.get("lift"), data.get("at"))

    elif msg_type in ("set_origin", "set_prime", "prime", "outline"):
        action = {"set_origin": _set_origin, "set_prime": _set_prime, "prime": _prime,
                  "outline": _outline}[msg_type]
        refusal = await action()
        if refusal:
            log.info("%s refused: %s", msg_type, refusal)
            await _notify(ws, refusal)

    elif msg_type == "confirm_reposition":
        refusal = _confirm_reposition(data.get("lift"))
        if refusal:
            log.info("Reposition confirmation refused: %s", refusal)
            await _notify(ws, refusal)
        else:
            log.info("Frame reposition confirmed for lift %d", data["lift"] + 1)

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

    if msg_type in _STATE_CHANGING:
        await broadcast_state()


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

    rapid = None
    if _state and _state.grbl_settings:
        try:   # GRBL max rates are mm/min — for G0 time estimates of flat G-code
            rapid = min(float(_state.grbl_settings[110]), float(_state.grbl_settings[111])) / 25.4
        except (KeyError, ValueError):
            rapid = None
    try:
        # parsing + motion analysis can take seconds on a Pi for a whole
        # building: off the event loop, so status / jog dead-man keep running
        job = await asyncio.get_running_loop().run_in_executor(None, load_job_from_file, destination, rapid)
    except JobFormatError as exc:
        return {"success": False, "error": f"Not a valid print job: {exc}"}
    except Exception as exc:
        return {"success": False, "error": f"Could not parse file: {exc}"}

    _current_job = job
    await broadcast_state()

    log.info("Job uploaded: %s (%s, %d sendable lines, %d lifts)",
             job.filename, job.kind, job.total_lines, job.total_lifts)
    return {
        "success": True,
        "filename": job.filename,
        "kind": job.kind,
        "executable": job.executable,
        "unresolved": list(job.unresolved),
        "total_lines": job.total_lines,
        "total_lifts": job.total_lifts,
    }


# ------------------------------------------------------------------ #
# HTTP: current state (for debugging / REST clients)                  #
# ------------------------------------------------------------------ #

@router.get("/api/job/path")
async def get_job_path():
    """The loaded job's XY path for the live 2D view — derived only from its G-code."""
    job = _current_job
    if job is None:
        return {"job_id": None}
    if getattr(job, "_path_payload", None) is None:
        # bounded-error simplification: seconds on a Pi for a whole building —
        # computed once per job, off the event loop
        breaks = {g for g, _, _ in job.layout.segments} | set(job.layout.offsets)
        job._path_payload = await asyncio.get_running_loop().run_in_executor(
            None, lambda: path_payload(job.profile, breaks))
    out = dict(job._path_payload)
    out["job_id"] = job.job_id
    return out


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
    """Point AppState.job at the current job (shared by reference) for WS broadcasts."""
    if _state is not None:
        _state.job = _current_job
