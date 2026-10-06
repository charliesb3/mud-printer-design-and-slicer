"""
Server-side G-code streaming job runner.

The runner is an asyncio Task that lives on the server process, independent
of any WebSocket session. Closing or reconnecting the browser does not affect
a running job.

Streaming strategy: simple send-line → wait-for-ok → send-next-line.
This is conservative, easy to reason about, and appropriate for M1.
The architecture does not preclude upgrading to character-counting later.

Hold/Resume:
- Hold is implemented by setting job.state = "held" externally (via the WS
  handler sending the feed-hold real-time byte to GRBL). The runner polls
  job.state and pauses before sending the next line when it sees "held".
- Resume sets job.state = "running"; the runner's poll loop exits and
  streaming continues.

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

# How often the runner polls job.state while held (seconds)
_HOLD_POLL_INTERVAL = 0.05


class JobRunner:
    def __init__(self, communicator: GRBLCommunicator, job: CurrentJob) -> None:
        self._comm = communicator
        self._job = job

    async def run(self) -> None:
        """Stream the job. Designed to be run as an asyncio Task."""
        job = self._job
        job.state = "running"
        job.started_at = time.time()
        job.current_line = 0

        log.info("Job started: %s (%d lines)", job.filename, job.total_lines)

        try:
            for i, line in enumerate(job.lines):
                # Wait while the operator has issued a feed hold
                while job.state == "held":
                    await asyncio.sleep(_HOLD_POLL_INTERVAL)

                # Stop was issued while we were held or between lines
                if job.state == "stopped":
                    log.info("Job stopped at line %d", i)
                    return

                # Send the G-code line and wait for GRBL's acknowledgement
                try:
                    result = await self._comm.send_command(line)
                except asyncio.CancelledError:
                    log.info("Job runner task cancelled at line %d", i)
                    raise
                except Exception as exc:
                    log.error("GRBL error at line %d (%s): %s", i, line, exc)
                    job.state = "error"
                    job.error = str(exc)
                    return

                if result.startswith("error:"):
                    log.warning("GRBL rejected line %d (%s): %s", i, line, result)
                    job.state = "error"
                    job.error = f"Line {i + 1}: {result}"
                    return

                job.current_line = i + 1

                # Some files include M2/M30 as an explicit end marker
                if is_end_of_program(line):
                    log.info("Job end-of-program marker at line %d", i + 1)
                    break

        except asyncio.CancelledError:
            # Task was cancelled externally (stop command). Let it propagate.
            raise
        finally:
            # Only mark completed if we didn't already set an error/stopped state
            if job.state == "running":
                job.state = "completed"
                log.info("Job completed: %s", job.filename)
