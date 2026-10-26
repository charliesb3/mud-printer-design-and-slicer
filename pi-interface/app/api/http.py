"""
HTTP and WebSocket routes.

WebSocket (/ws):
  Handles the real-time bidirectional control channel between the browser
  and the server. Messages are JSON objects with a 'type' field.

  Client → Server message types:
    jog_start      {axis: "X"|"Y", direction: 1|-1}
    jog_heartbeat  {}  — sent every ~100 ms while button held
    jog_set_speed  {speed_in_s: float} OR {speed_mm_min: float} — adjust live
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
    read_grbl      {what: "settings"|"parser"|"offsets"|"info"}  — read-only $$ / $G / $# / $I
    set_machine_profile {profile}  — THIS printer's machine profile (saved locally;
                   not while a job runs / holds / awaits a reposition)
    console_send   {command: str}
    z_start        {direction: "up"|"down"}  — manual Z, held (dead-man, like jog)
    z_heartbeat    {}  — sent every ~100 ms while a Z button is held
    z_stop         {}
    remote_stop_clear {}  — deliberate operator recovery from a latched REMOTE STOP

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

Manual Z (app/hardware/z.py): z_start / z_heartbeat / z_stop drive the ONE
  Z controller — the RF remote's Z UP / Z DOWN use the same controller. Each
  browser client is its own dead-man source; Z is allowed while a job runs
  (Z is manual during printing) but never while REMOTE STOP is latched.

REMOTE STOP (Kaleinavi CH1, `remote_stop()` below): an OPERATIONAL stop,
  never an E-stop. Stops Z, cancels any jog (0x85), feed-holds GRBL (`!`)
  and holds the job (resumable), then LATCHES: new motion is refused until an
  operator clears it on a screen (`remote_stop_clear`, CH1 released). Clearing
  never moves anything — a held job continues only with RESUME.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, File, UploadFile, WebSocket, WebSocketDisconnect

# Starlette raises TWO distinct exception classes on disconnect:
#   - WebSocketDisconnect: client sent close message mid-receive. Routine.
#   - WebSocketDisconnected: receive_json / receive_text / receive_bytes
#     was called when application_state wasn't CONNECTED. ALSO routine
#     after a client-initiated close — but the message is "Need to call
#     accept first", which is misleading. Catch both quietly.
try:
    from starlette.websockets import WebSocketDisconnected as _StarletteWSDisconnected
except ImportError:          # older starlette versions don't expose it
    class _StarletteWSDisconnected(Exception):   # pragma: no cover - stub
        pass

from .. import config
from ..grbl.communicator import GRBLCommunicator
from ..jobs.current_job import CurrentJob, JobFormatError, load_job_from_file
from ..jobs.motion import path_payload
from .. import machine_profile as machine_profile_mod
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
# True from the moment a release is requested until the task has exited.
# The loop checks this before every write so no further $J= steps are queued
# into GRBL after a release.
_jog_stopping: bool = False

# Positioning move (Prime) in progress
_position_task: Optional[asyncio.Task] = None


def _latched() -> Optional[str]:
    """The REMOTE STOP motion interlock (None: motion may be commanded)."""
    return _state.remote_stop.refusal() if _state else None


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

def _current_jog_speed_mm_min() -> float:
    """
    Operator-selected jog speed (read fresh each iteration from AppState).

    Falls back to `config.JOG_SPEED_MM_MIN` when the operator hasn't set a
    live value (startup, or a test that only configured the default).
    Returns 0 when jogging is disabled. The ceiling is enforced defensively.
    """
    speed: Optional[float] = None
    if _state is not None:
        speed = _state.current_jog_speed_mm_min
    if speed is None or speed <= 0:
        speed = config.JOG_SPEED_MM_MIN
    if speed is None or speed <= 0:
        return 0.0
    max_speed = config.JOG_SPEED_MAX_MM_MIN
    if max_speed is not None and speed > max_speed:
        return max_speed
    return speed


async def _jog_loop(axis: str, direction: int) -> None:
    """
    SINGLE-SHOT JOG MODEL (2026-10-25, replaces incremental streaming).

    On jog_start the loop sends ONE `$J=G21 G91 <axis><distance> F<speed>`
    line to GRBL with a conservative long distance (`JOG_SINGLE_DISTANCE_IN`,
    default 10 in). GRBL executes that single move and stops naturally at
    its end. While the operator holds the button, this task does NOT send
    anything more — it only polls the heartbeat timer and the stop flag.
    On release / heartbeat-timeout / cancel, the finally clause sends GRBL's
    `0x85` jog-cancel real-time byte, which decelerates the single in-flight
    jog at $120 / $121 and clears GRBL's planner.

    Why single-shot:
    - Incremental streaming filled GRBL's 15-block planner with queued $J=
      lines, so on release a 0x85 had to flush several buffered steps.
    - Concurrent `write_line($J=…)` and `write_raw(0x85)` could interleave
      at the serial byte level (asyncio.to_thread uses multiple workers,
      pyserial's write is not thread-safe). GRBL processes real-time bytes
      mid-line, so an interleaved 0x85 would cancel the current jog AND then
      the rest of the parsed $J= would start a NEW jog — the "second burst"
      observed on hardware. SerialTransport now holds a threading.Lock, and
      this loop no longer writes multiple $J= lines, so the interleave race
      cannot occur.

    Safety:
    - Dead-man heartbeat: if browser disconnects or heartbeats stop, finally
      runs and 0x85 cancels within one JOG_SEND_INTERVAL_S (default 50 ms)
      + decel at $120.
    - Stop flag (`_jog_stopping`) short-circuits BEFORE the single $J= write
      if a release arrived during startup.
    - On GRBL error / connection loss, finally still sends 0x85.
    """
    global _jog_axis, _jog_direction, _jog_last_heartbeat

    speed = _current_jog_speed_mm_min()
    if speed <= 0:
        log.warning("Jog not started — current jog speed is zero")
        return

    # One long jog line — distance in mm, feed in mm/min.
    distance_mm = direction * (config.JOG_SINGLE_DISTANCE_IN * 25.4)
    cmd = f"$J=G21 G91 {axis}{distance_mm:.3f} F{speed:.0f}"
    # DIAGNOSTIC (thirteenth pass): capture MPos BEFORE the $J= write so a
    # follow-up task can log reported travel after the jog settles. These
    # two logs per session are the whole of the "lets you calibrate from
    # the journal" feature; cheap to leave in.
    start_mpos = (_state.mpos_x, _state.mpos_y) if _state else (None, None)
    start_units = _state.report_units if _state else None
    log.info("Jog start: %s %.1f mm @ %.1f mm/min (single-shot)  "
             "MPos=(%.3f, %.3f) %s",
             axis, distance_mm, speed,
             start_mpos[0] if start_mpos[0] is not None else float("nan"),
             start_mpos[1] if start_mpos[1] is not None else float("nan"),
             start_units or "mm")

    try:
        # Guard: a stop that arrived before the task scheduled must prevent
        # even the single $J= from going out.
        if _jog_stopping:
            log.debug("Jog aborted before $J= — _jog_stopping already set")
            return
        try:
            result = await _comm.send_command(cmd, timeout=3.0)  # type: ignore[union-attr]
        except asyncio.CancelledError:
            # Cancellation during the $J= write — finally still sends 0x85.
            raise
        except Exception as exc:
            log.debug("Jog $J= send exception: %s", exc)
            return
        if result.startswith("error:"):
            log.warning("Jog $J= rejected by GRBL: %s", result)
            return

        # Jog is running in GRBL. Monitor heartbeat only — no more writes.
        while True:
            if _jog_stopping:
                log.debug("Jog loop sees _jog_stopping — exiting")
                break
            age = time.monotonic() - _jog_last_heartbeat
            if age > config.JOG_TIMEOUT_S:
                log.debug("Jog heartbeat timeout (%.2f s) — stopping", age)
                break
            try:
                await asyncio.sleep(config.JOG_SEND_INTERVAL_S)
            except asyncio.CancelledError:
                raise
    finally:
        # The ONLY place 0x85 is sent for a jog session — every exit path
        # passes through here (release, heartbeat timeout, cancel, error).
        # SerialTransport._write_lock guarantees the byte stream is atomic
        # relative to any other serial write (e.g. a late ok from the $J=).
        log.info("Jog end — sending GRBL jog-cancel 0x85")
        if _comm:
            try:
                await _comm.send_realtime(b"\x85")
            except Exception as exc:
                log.debug("send_realtime(0x85) on jog end failed: %s", exc)
        # DIAGNOSTIC (thirteenth pass): fire-and-forget task that waits for
        # GRBL to report Idle then logs reported travel. Non-blocking; one
        # extra INFO line per jog session; perfect for calibration passes
        # (`journalctl -u mudprinter --since ... | grep "JOG DIAG"`).
        try:
            asyncio.create_task(
                _log_jog_motion(axis, direction, start_mpos, start_units),
                name="jog-diag",
            )
        except RuntimeError:
            # No running loop (test teardown); skip silently.
            pass
        _jog_axis = None


async def _log_jog_motion(axis: str, direction: int,
                          start_mpos: tuple, start_units) -> None:
    """
    Thirteenth-pass calibration diagnostic.

    Spawned from `_jog_loop`'s finally. Waits up to 5 s for GRBL to settle
    (status report shows Idle), then logs the reported travel in inches
    alongside the start and end machine positions. Non-blocking and bounded;
    does not interfere with subsequent jogs or state broadcasts.
    """
    if _state is None or start_mpos[0] is None:
        return
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 5.0
    # Wait for a FRESH status report AFTER an Idle state. GRBL's decel
    # continues for several status-poll intervals after 0x85; this gives
    # the planner time to finish before we snapshot the end position.
    seen_idle_at: float = 0.0
    while loop.time() < deadline:
        if _state.grbl_state == "Idle":
            if not seen_idle_at:
                seen_idle_at = loop.time()
            elif loop.time() - seen_idle_at >= 0.3:
                break
        else:
            seen_idle_at = 0.0
        await asyncio.sleep(0.1)

    end_mpos = (_state.mpos_x, _state.mpos_y)
    units = _state.report_units or start_units or "mm"
    dx = end_mpos[0] - start_mpos[0]
    dy = end_mpos[1] - start_mpos[1]
    if units == "mm":
        dx_in, dy_in = dx / 25.4, dy / 25.4
    else:                                                # already inches
        dx_in, dy_in = dx, dy
    sign = "+" if direction > 0 else "-"
    log.info(
        "JOG DIAG: axis=%s dir=%s  MPos (%s): start=(%.3f, %.3f) -> end=(%.3f, %.3f)  "
        "reported travel: ΔX=%+.4f in, ΔY=%+.4f in",
        axis, sign, units,
        start_mpos[0], start_mpos[1],
        end_mpos[0], end_mpos[1],
        dx_in, dy_in,
    )


async def _start_jog(axis: str, direction: int) -> Optional[str]:
    """Activate the jog loop for the given axis and direction (returns a REMOTE STOP refusal)."""
    global _jog_axis, _jog_direction, _jog_last_heartbeat, _jog_task, _jog_stopping

    if _latched():
        log.info("Jog refused: REMOTE STOP latched")
        return _latched()
    if config.JOG_SPEED_MM_MIN is None:
        log.warning("Jog requested but JOG_SPEED_MM_MIN is not configured")
        return
    if _current_jog_speed_mm_min() == 0:
        log.warning("Jog requested but current jog speed is zero")
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

    # Cancel any previous jog task — and make sure the previous button hold
    # has been fully released before this new deliberate press starts anything.
    if _jog_task and not _jog_task.done():
        await _stop_jog()

    _jog_stopping = False
    _jog_axis = axis
    _jog_direction = direction
    _jog_last_heartbeat = time.monotonic()
    _jog_task = asyncio.create_task(_jog_loop(axis, direction), name="jog-loop")
    return None


async def _stop_jog() -> None:
    """
    Stop the current jog session.

    With the single-shot jog model + SerialTransport._write_lock, this is
    simple: set the stop flag so the task's heartbeat loop exits on its next
    poll, then cancel the task. The task's finally clause is the ONE place
    0x85 is written — guaranteed atomic on the wire relative to any pending
    $J= ok reader and any status-poll `?`.

    Order:
    1. `_jog_stopping = True` — the heartbeat loop exits on next tick
       (≤ JOG_SEND_INTERVAL_S), and if the task is still in the one $J=
       send_command it will skip the write-after-send phase.
    2. `_jog_task.cancel()` — ends the heartbeat sleep immediately.
    3. `await _jog_task` — the task's finally sends 0x85 to GRBL.
    """
    global _jog_task, _jog_axis, _jog_stopping
    _jog_stopping = True
    _jog_axis = None
    if _jog_task and not _jog_task.done():
        _jog_task.cancel()
        try:
            await _jog_task
        except (asyncio.CancelledError, Exception):
            pass
    _jog_task = None


# ------------------------------------------------------------------ #
# Job management                                                       #
# ------------------------------------------------------------------ #

async def _start_job() -> Optional[str]:
    """Start the current job. Returns a refusal reason, or None when started."""
    global _current_job, _runner_task

    if _current_job is None:
        log.warning("job_start: no current job")
        return "No job loaded."
    if _latched():
        return _latched()
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
    pf = _current_job.to_dict().get("preflight") if _current_job.printer else None
    if pf and pf.get("blocking"):
        # this printer's own check (authoritative) — whatever the job claims
        why = "; ".join(c["message"] for c in pf["checks"] if c["status"] == "fail")
        return f"This job does not fit printer \"{pf.get('profile_name')}\": {why} Fix the job or this printer's profile."
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
    if _latched():
        return _latched()
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
    refusal = _latched() or _setup_refusal()
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
    refusal = _latched() or _setup_refusal()
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

READ_QUERIES = {"settings": "$$", "parser": "$G", "offsets": "$#", "info": "$I"}


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
    if msg_type in ("adjust_lift_end", "apply_lift_parameters", "restore_planned_lifts") and _latched():
        # moving a lift end can release a paused job into streaming again
        return (_latched(), None)
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


# ------------------------------------------------------------------ #
# REMOTE STOP (Kaleinavi CH1) — operational stop + latch, NOT an E-stop #
# ------------------------------------------------------------------ #

async def remote_stop(cause: str) -> None:
    """
    Command a no-motion state and latch it. Uses the normal mechanisms only:
    Z → PWM 0 through the Z controller; X/Y → GRBL real-time jog cancel
    (0x85) and feed hold (`!`), which bypass the command queue and leave the
    streamed job intact; the job runner is held exactly as by HOLD, so the
    job stays resumable. Idempotent (a repeated press re-sends the stop).
    """
    if _state is None:
        return
    rs = _state.remote_stop
    first = rs.latch(cause)
    actions = []
    hw = _state.hardware
    if hw is not None:
        hw.z.stop_all("REMOTE STOP")
        actions.append("Z stopped (PWM 0)")
    jogging = bool(_jog_task and not _jog_task.done())
    if _comm and _state.connected:
        await _comm.send_realtime(b"\x85")       # jog cancel (ignored unless jogging)
        await _comm.send_realtime(b"!")          # feed hold: decelerate, keep the planner / position
        actions.append("GRBL feed hold sent (X/Y decelerate to a stop; position kept)")
    else:
        actions.append("GRBL not connected — no X/Y command sent")
    if jogging:
        await _stop_jog()
        actions.append("manual jog cancelled")
    job = _current_job
    if job is not None:
        if job.state == "running":
            job.state = "held"
            actions.append(f"job HELD at line {job.current_line} of {job.total_lines} (resumable)")
        elif job.state == "held":
            actions.append("job already held (resumable)")
        elif job.state == "awaiting_reposition":
            actions.append("job awaiting a frame reposition — confirmation blocked while latched")
    if _state.setup.moving:
        actions.append(f"{_state.setup.moving} move held (RESUME continues it, STOP abandons it)")
    if first:
        rs.actions = actions
    _state.add_hw_event("remote_stop", f"REMOTE STOP {'LATCHED' if first else 'pressed again (still latched)'}"
                                       f" — {cause}: " + "; ".join(actions))
    log.warning("REMOTE STOP %s (%s): %s", "latched" if first else "re-sent", cause, "; ".join(actions))
    # runs inside the hardware poll loop: never wait on browser sockets here
    asyncio.get_running_loop().create_task(broadcast_state())


def _clear_remote_stop() -> Optional[str]:
    """Deliberate operator recovery. Never starts motion."""
    if _state is None:
        return "Not ready."
    rs = _state.remote_stop
    if not rs.latched:
        return "REMOTE STOP is not latched."
    hw = _state.hardware
    if hw is not None and hw.backend.has_gpio:
        ch = hw.remote.channels["remote_stop"]
        if ch.pressed is None:
            return "The REMOTE STOP input has not been read yet — try again in a moment."
        if ch.pressed:
            return "The REMOTE STOP input still reads PRESSED (CH1 contact closed) — release it first."
    rs.clear()
    _state.add_hw_event("remote_stop", "REMOTE STOP cleared by the operator (nothing moved)")
    log.warning("REMOTE STOP cleared by the operator")
    return None


def _remote_stop_cleared_note() -> str:
    job = _current_job
    note = "REMOTE STOP cleared. Nothing has moved."
    if job is not None and job.state == "held":
        note += " The job is still HELD — press RESUME to continue it, or STOP to end it."
    elif _state is not None and _state.grbl_state.startswith("Hold"):
        note += " GRBL is in feed hold — RESUME continues any held motion, STOP abandons it."
    return note


def _z_source(ws) -> str:
    return f"ui:{id(ws):x}"


def _set_z_duty(raw) -> Optional[str]:
    """Operator-adjustable Z duty via the UI slider.

    Validates, applies to the shared `ZController`, and persists to
    `config.Z_SETTING_PATH` so a restart keeps the operator's choice.
    Shared across all WS clients + the RF remote because the ZController
    is one instance.
    """
    if _state is None or _state.hardware is None:
        return "No Z hardware."
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return "Z duty must be a number between 0 and 1."
    duty = float(raw)
    if duty < 0.0 or duty > 1.0:
        return "Z duty must be between 0 (STOP) and 1 (100 %)."
    refusal = _state.hardware.z.set_duty(duty)
    if refusal:
        return refusal
    # Only persist on a successful set — never save a value we refused.
    try:
        from .. import z_setting
        z_setting.save(config.Z_SETTING_PATH, duty)
    except OSError as exc:
        return f"Z duty applied but could not be saved to {config.Z_SETTING_PATH}: {exc}"
    log.info("Z duty set and persisted: %.2f", duty)
    return None


def _z_start(direction, ws) -> Optional[str]:
    hw = _state.hardware if _state else None
    if hw is None:
        return "No Z hardware."
    d = {"up": 1, "down": -1}.get(direction)
    if d is None:
        return "Z direction must be up or down."
    return hw.z.request(_z_source(ws), d)


async def _notify(ws: WebSocket, message: str, level: str = "warn") -> None:
    try:
        await ws.send_json({"type": "notice", "level": level, "message": message})
    except Exception:
        pass


async def broadcast_state() -> None:
    """Push the current state to every client now (not waiting for the 200 ms loop).

    Errors here are LOCAL to this broadcast — never propagate out, because this
    is called from the WebSocket message handler and an unhandled exception
    would otherwise close the WebSocket (see commentary on
    `websocket_endpoint`).
    """
    try:
        sync_job_state_to_appstate()
        if _ws_manager and _state and _ws_manager.client_count > 0:
            await _ws_manager.broadcast(_state.to_dict())
    except Exception:
        log.exception("broadcast_state failed")


# ------------------------------------------------------------------ #
# WebSocket message dispatcher                                         #
# ------------------------------------------------------------------ #

# Messages that change machine/job state: broadcast to all clients right after.
def _set_machine_profile(profile) -> Optional[str]:
    """Save THIS printer's profile (authoritative for job preflight)."""
    if _state is None:
        return "Not ready."
    if _current_job and _current_job.is_active:
        return "The printer profile cannot change while a job is running, held or awaiting a reposition."
    try:
        p = machine_profile_mod.save(config.MACHINE_PROFILE_PATH, profile)
    except machine_profile_mod.MachineProfileError as exc:
        return str(exc)
    except OSError as exc:
        return f"The profile could not be saved: {exc}"
    _state.machine_profile, _state.machine_profile_problem = p, None
    if _current_job:
        _current_job.printer = p
    log.info("Machine profile saved: %s", p)
    return None


_STATE_CHANGING = {"hold", "resume", "stop", "job_start", "z_start", "z_stop", "set_z_duty",
                   "remote_stop_clear", "confirm_reposition", "set_machine_profile",
                   "set_origin", "set_prime", "prime", "outline", "feed_override", "read_grbl",
                   "adjust_lift_end", "set_observed_layer", "measure_lift_height", "restore_planned_lifts",
                   "apply_lift_parameters"}


async def _handle_message(data: dict, ws: WebSocket) -> None:
    global _jog_last_heartbeat, _current_job

    msg_type = data.get("type")

    if msg_type == "jog_start":
        # Validate payload defensively — a stray message with
        # `{"direction": null}` would throw TypeError here (`int(None)`),
        # which under the sixth-pass handler fix surfaces as a user-
        # visible "Internal error handling 'jog_start'" notice. That is
        # unnecessary and alarming for a routine payload-shape issue.
        raw_axis = data.get("axis")
        raw_dir = data.get("direction")
        if not isinstance(raw_axis, str):
            return
        axis = raw_axis.upper()
        if axis not in ("X", "Y"):
            return
        if not isinstance(raw_dir, (int, float)) or isinstance(raw_dir, bool):
            return
        if raw_dir not in (-1, 1):
            return
        direction = int(raw_dir)
        refusal = await _start_jog(axis, direction)
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "jog_heartbeat":
        _jog_last_heartbeat = time.monotonic()

    elif msg_type == "jog_set_speed":
        # Operator-adjusted jog speed. Accepts in/s or mm/min; the server
        # clamps against the ceiling defined by JOG_SPEED_MAX_MM_MIN.
        if _state is None:
            return
        if "speed_in_s" in data:
            from ..state import in_s_to_mm_min
            speed_mm_min = in_s_to_mm_min(float(data["speed_in_s"]))
        elif "speed_mm_min" in data:
            speed_mm_min = float(data["speed_mm_min"])
        else:
            return
        stored = _state.set_jog_speed_mm_min(speed_mm_min)
        log.info("Jog speed set to %.1f mm/min (requested %.1f)", stored, speed_mm_min)

    elif msg_type == "jog_stop":
        await _stop_jog()

    elif msg_type == "hold":
        if _comm:
            await _comm.send_realtime(b"!")  # GRBL feed hold
        if _current_job and _current_job.state == "running":
            _current_job.state = "held"

    elif msg_type == "resume":
        if _latched():
            await _notify(ws, _latched())
            return
        if _comm:
            await _comm.send_realtime(b"~")  # GRBL cycle start
        if _current_job and _current_job.state == "held":
            _current_job.state = "running"

    elif msg_type == "stop":
        await _stop_job()

    elif msg_type == "z_start":
        refusal = _z_start(data.get("direction"), ws)
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "z_heartbeat":
        if _state and _state.hardware:
            _state.hardware.z.refresh(_z_source(ws))

    elif msg_type == "z_stop":
        if _state and _state.hardware:
            _state.hardware.z.release(_z_source(ws))

    elif msg_type == "set_z_duty":
        refusal = _set_z_duty(data.get("duty"))
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "remote_stop_clear":
        refusal = _clear_remote_stop()
        await _notify(ws, refusal or _remote_stop_cleared_note(), level="warn" if refusal else "info")

    elif msg_type == "job_start":
        refusal = await _start_job()
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "feed_override":
        refusal = await _feed_override(data.get("step"))
        if refusal:
            await _notify(ws, refusal)

    elif msg_type == "set_machine_profile":
        refusal = _set_machine_profile(data.get("profile"))
        if refusal:
            await _notify(ws, refusal)
        else:
            await _notify(ws, f"Printer profile saved: {_state.machine_profile['name']}.", level="info")

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
        if _latched():
            await _notify(ws, _latched())
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
    """
    One WebSocket client. The outer loop is for the SOCKET lifecycle only:
    - WebSocketDisconnect = client went away — expected, silent cleanup.
    - Any other exception in `receive_json` (framing, I/O) = also closes.
    - Exceptions from `_handle_message` are CAUGHT PER-MESSAGE and logged
      with a traceback so one bad message cannot silently drop the socket.
      Before 2026-10-25 (fourth-pass bug) this handler caught every
      exception at `log.debug` and let the `finally` disconnect the client
      — a single serialization slip, GPIO read-time error, or state-
      build error during a `z_start` broadcast would kick the browser
      offline for ~1 s with no visible log. Operator saw this on z_start.
    """
    assert _ws_manager is not None
    assert _state is not None

    await _ws_manager.connect(ws)
    # Send full state immediately so a reconnecting client syncs instantly
    try:
        await ws.send_json(_state.to_dict())
    except Exception:
        log.exception("WebSocket: initial state send failed; closing")
        _ws_manager.disconnect(ws)
        return

    try:
        while True:
            data = await ws.receive_json()
            try:
                await _handle_message(data, ws)
            except Exception:
                msg_type = data.get("type") if isinstance(data, dict) else None
                log.exception("WebSocket handler error (type=%r)", msg_type)
                # Tell the client there was an error but KEEP THE SOCKET OPEN.
                try:
                    await _notify(ws, f"Internal error handling {msg_type!r} — see Pi logs.")
                except Exception:
                    log.exception("Could not notify client of handler error")
    except (WebSocketDisconnect, _StarletteWSDisconnected):
        # Routine client-initiated close. The `receive_json` version with
        # the misleading "Need to call accept first" message also lands
        # here; both just mean "client is gone, clean up and move on."
        pass
    except Exception:
        log.exception("WebSocket receive/transport error")
    finally:
        _ws_manager.disconnect(ws)
        if _state and _state.hardware:          # a vanished client holds no Z (dead-man would also stop it)
            try:
                _state.hardware.z.release(_z_source(ws))
            except Exception:
                log.exception("Error releasing Z on WS cleanup")


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

    job.printer = _state.machine_profile if _state else None      # this controller's profile (authoritative)
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
# Exit kiosk — close the Chromium fullscreen window WITHOUT stopping   #
# the backend. Reads the PID written by pi-interface/deploy/           #
# launch-kiosk.sh and sends SIGTERM. The backend keeps running so a    #
# subsequent desktop launch reconnects to it instantly.                #
# ------------------------------------------------------------------ #

KIOSK_PID_PATH: str = "/tmp/mudprinter-kiosk.pid"


@router.post("/api/exit_kiosk")
async def exit_kiosk():
    """Signal the kiosk launcher to close Chromium. Backend is untouched."""
    import os
    import signal
    try:
        with open(KIOSK_PID_PATH) as f:
            pid = int(f.read().strip())
    except FileNotFoundError:
        return {"ok": False, "error": "No kiosk is running (no PID file).",
                "running": False}
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": f"Could not read kiosk PID file: {exc}"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": False, "error": "Kiosk process already exited.",
                "running": False}
    except PermissionError as exc:
        return {"ok": False, "error": f"Not permitted to signal pid {pid}: {exc}"}
    log.info("Exit kiosk: SIGTERM sent to pid %d (backend keeps running)", pid)
    return {"ok": True, "signaled_pid": pid}


# ------------------------------------------------------------------ #
# Accessors for main.py background loops                              #
# ------------------------------------------------------------------ #

def get_current_job() -> Optional[CurrentJob]:
    return _current_job


def sync_job_state_to_appstate() -> None:
    """Point AppState.job at the current job (shared by reference) for WS broadcasts."""
    if _state is not None:
        _state.job = _current_job
