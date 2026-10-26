"""
Server-side G-code streaming job runner.

The runner is an asyncio Task that lives on the server process, independent
of any WebSocket session. Closing or reconnecting the browser does not affect
a running job.

Streaming strategy: simple send-line → wait-for-ok → send-next-line.
This is conservative, easy to reason about, and appropriate for M1.
The architecture does not preclude upgrading to character-counting later.
Every job line waits for its ok with the communicator's LIVE wait (no fixed
time limit while GRBL keeps answering status polls — GRBL withholds ok while
its planner buffer is full or a feed hold is active; see communicator).

Building jobs (.mudjob) start by RETURNING TO THE PRINT ORIGIN: the runner
first sends `G54 G20 G90 G1 X0 Y0 F<positioning feed>` (job.phase "origin"),
then the lifts. The origin itself is established by the operator (Set
Origin); flat G-code jobs run exactly as in Milestone 1 (no return).

Lifts:
- A job is an ordered list of lifts (a flat G-code file is one lift, so it
  streams exactly as in Milestone 1).
- At the end of every lift except the last, the runner DRAINS GRBL: it sends
  `G4 P0` (GRBL replies ok only once every buffered move has finished — a
  line's ok normally means "buffered", not "executed") and then waits for a
  fresh Idle status report.
- It then enters job.state = "awaiting_reposition" (job.awaiting_lift = the
  next lift) and sends nothing until the operator confirms the frame
  reposition (api/http.py sets state back to "running"). A GRBL disconnect or
  reset while waiting ends the job in "error": machine position can no longer
  be trusted, and resuming at a lift is not implemented.

Hold/Resume:
- Hold is implemented by setting job.state = "held" externally (via the WS
  handler sending the feed-hold real-time byte to GRBL). The runner polls
  job.state and pauses before sending the next line when it sees "held".
- Resume sets job.state = "running"; the runner's poll loop exits and
  streaming continues. Resume never confirms a reposition.

Stop:
- The runner Task is cancelled externally (see api/http.py handle_stop).
  asyncio.CancelledError propagates through send_command() cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..grbl.communicator import GRBLCommunicator
from ..grbl.parser import is_end_of_program
from .current_job import CurrentJob

log = logging.getLogger(__name__)

# How often the runner polls job / machine state while waiting (seconds)
_HOLD_POLL_INTERVAL = 0.05

# GRBL acknowledges a dwell only after the planner buffer has emptied.
DRAIN_COMMAND = "G4 P0"


def origin_return_line(feed_in_min: float) -> str:
    """The move to the print origin (work XY 0, G54) before a building job."""
    return f"G54 G20 G90 G1 X0 Y0 F{feed_in_min:.1f}"


class JobRunner:
    def __init__(self, communicator: GRBLCommunicator, job: CurrentJob,
                 origin_feed_in_min: float | None = None,
                 on_layer_boundary=None) -> None:
        """
        * `on_layer_boundary(prev_layer_idx, next_layer_idx, next_layer_z_in)`
          — sixteenth-pass hook, awaited AFTER draining GRBL at every
          within-lift layer-mark crossing. Returns True to continue, False
          to abort. If None (default — "manual" Z mode), no pause is
          inserted and the runner streams through layer marks unchanged.
          Lift boundaries continue to use `_finish_lift` and are NOT
          surfaced through this hook (frame reposition is a different
          operator-step from an in-frame Z advance).
        """
        self._comm = communicator
        self._job = job
        self._origin_feed = origin_feed_in_min
        self._on_layer_boundary = on_layer_boundary

    async def run(self) -> None:
        """Stream the job. Designed to be run as an asyncio Task."""
        job = self._job
        job.state = "running"
        job.started_at = time.time()
        job.current_line = 0
        job.current_lift = 0
        job.awaiting_lift = None

        log.info("Job started: %s (%d lines, %d lifts)", job.filename, job.total_lines, job.total_lifts)

        try:
            if self._origin_feed is not None:
                if not await self._return_to_origin():
                    return
            await self._stream()

        except asyncio.CancelledError:
            # Task was cancelled externally (stop command). Let it propagate.
            raise
        finally:
            # Only mark completed if we didn't already set an error/stopped state
            if job.state == "running":
                job.state = "completed"
                log.info("Job completed: %s", job.filename)

    # ------------------------------------------------------------------ #

    async def _return_to_origin(self) -> bool:
        job = self._job
        job.phase = "origin"
        line = origin_return_line(self._origin_feed)
        log.info("Returning to the print origin: %s", line)
        try:
            result = await self._comm.send_command(line, live=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(f"Return to origin: {exc}")
            return False
        if result.startswith("error:"):
            self._fail(f"Return to origin ({line}): {result}")
            return False
        job.phase = None
        return job.state in ("running", "held")

    def _fail(self, message: str) -> None:
        self._job.state = "error"
        self._job.error = message
        self._job.awaiting_lift = None

    async def _stream(self) -> None:
        """Stream the job's global line sequence, stopping at each EFFECTIVE
        lift boundary (job.layout — the exported lifts unless the operator
        moved the end of a lift to another clean cut). Boundaries are read
        live, so an adjustment made while printing takes effect at once."""
        job = self._job
        lines = job.lines
        paused_at: set = set()
        i = 0
        while i < len(lines):
            if i > 0 and job.layout.is_boundary(i) and i not in paused_at:
                paused_at.add(i)
                ended = job.layout.lift_of(i) - 1
                if not await self._finish_lift(ended, i):
                    return
                if job.layout.is_boundary(i):
                    log.info("Frame reposition confirmed — starting lift %d", ended + 2)
                else:
                    log.info("Lift %d extended by the operator — continuing without a reposition", ended + 1)
            # Sixteenth-pass auto-Z: WITHIN-LIFT layer boundary. Fires only
            # when the layer index at `i` differs from the layer index at
            # `i - 1` AND we're not sitting on a lift boundary (lifts own
            # the reposition flow). The hook is NOT invoked at i == 0.
            if self._on_layer_boundary is not None and i > 0:
                prev_seg = job.layout.segment_at(i - 1)
                next_seg = job.layout.segment_at(i)
                if (prev_seg and next_seg
                        and prev_seg[1] != next_seg[1]
                        and not job.layout.is_boundary(i)):
                    if not await self._drain(job.layout.lift_of(i) - 1):
                        return
                    cont = await self._on_layer_boundary(
                        prev_seg[1], next_seg[1], next_seg[2])
                    if not cont:
                        log.info("Layer-boundary hook aborted the job at line %d", i)
                        return
                    if job.state == "stopped":
                        return
            job.current_lift = job.layout.lift_of(i)
            line = lines[i]

            # Wait while the operator has issued a feed hold
            while job.state == "held":
                await asyncio.sleep(_HOLD_POLL_INTERVAL)

            # Stop was issued while we were held or between lines
            if job.state == "stopped":
                log.info("Job stopped at line %d", i)
                return

            # Send the G-code line and wait for GRBL's acknowledgement
            try:
                result = await self._comm.send_command(line, live=True)
            except asyncio.CancelledError:
                log.info("Job runner task cancelled at line %d", i)
                raise
            except Exception as exc:
                log.error("GRBL error at line %d (%s): %s", i, line, exc)
                self._fail(str(exc))
                return

            if result.startswith("error:"):
                log.warning("GRBL rejected line %d (%s): %s", i, line, result)
                self._fail(f"Line {i + 1}: {result}")
                return

            job.current_line = i + 1

            # Some files include M2/M30 as an explicit end marker
            # (only flat G-code jobs: building-job lifts may not contain them)
            if is_end_of_program(line):
                log.info("Job end-of-program marker at line %d", i + 1)
                return
            i += 1

    async def _finish_lift(self, li: int, at: int) -> bool:
        """Drain GRBL, then wait for the operator to confirm the frame
        reposition (or to extend lift `li` past line `at`, removing the boundary)."""
        job = self._job
        state = self._comm.state

        if not await self._drain(li):
            return False

        # A hold pressed while draining: wait for resume (or stop) first.
        while job.state == "held":
            await asyncio.sleep(_HOLD_POLL_INTERVAL)
        if job.state != "running":
            return False

        if not job.layout.is_boundary(at):           # extended while draining
            return True
        job.awaiting_lift = li + 1
        job.state = "awaiting_reposition"
        job.begin_wait()
        reset_seq = state.reset_seq
        log.info("Lift %d complete — awaiting frame reposition before lift %d", li + 1, li + 2)

        while job.state == "awaiting_reposition":
            if not state.connected or state.reset_seq != reset_seq:
                log.error("GRBL disconnected/reset while awaiting reposition before lift %d", li + 2)
                self._fail(
                    f"GRBL disconnected or reset while awaiting the frame reposition before lift {li + 2}. "
                    "Machine position can no longer be trusted; the job cannot continue."
                )
                return False
            await asyncio.sleep(_HOLD_POLL_INTERVAL)

        job.end_wait()
        if job.state != "running":   # stopped while awaiting
            return False
        return True

    async def _drain(self, li: int) -> bool:
        """Wait until every buffered move has executed and GRBL reports Idle."""
        job = self._job
        state = self._comm.state
        try:
            result = await self._comm.send_command(DRAIN_COMMAND, live=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("GRBL error while draining lift %d: %s", li + 1, exc)
            self._fail(f"End of lift {li + 1}: {exc}")
            return False
        if result.startswith("error:"):
            self._fail(f"End of lift {li + 1}: {DRAIN_COMMAND} → {result}")
            return False

        # A status report received after the dwell's ok reflects the drained machine.
        seq = state.status_seq
        while not (state.status_seq > seq and state.grbl_state == "Idle"):
            if job.state == "stopped":
                return False
            if not state.connected:
                self._fail(f"End of lift {li + 1}: GRBL disconnected")
                return False
            await asyncio.sleep(_HOLD_POLL_INTERVAL)
        return True
