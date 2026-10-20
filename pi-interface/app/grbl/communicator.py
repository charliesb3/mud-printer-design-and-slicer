"""
GRBL communication manager.

Responsibilities:
- Runs the status polling loop (sends '?' at the configured interval)
- Runs the response reader loop (dispatches status reports, ok, error, alarm, etc.)
- Provides send_command() for queued commands that expect ok/error responses
- Provides send_realtime() for immediate real-time bytes (!, ~, 0x18, 0x85)
- Updates AppState on every received status report or connection event
- Logs all traffic to the console buffer in AppState

Design note on dead-man safety for jogging:
The jog loop in api/http.py sends small incremental commands and waits for ok
before sending the next. Combined with a heartbeat timeout, the maximum runaway
distance on browser disconnection is approximately 1 jog step length.
send_realtime() is used to send 0x85 (jog cancel) when the heartbeat stops.

GRBL version dependency:
Jog mode ($J=) requires GRBL 1.1+. The version is detected from the welcome
message and stored in AppState. Physical hardware verification is required to
confirm the installed GRBL version before relying on jog behavior.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .. import config
from ..state import AppState
from .codes import describe_error
from .parser import (
    is_end_of_program,
    is_welcome_message,
    parse_bracket,
    parse_grbl_version,
    parse_setting,
    parse_status_report,
)
from .transport import GRBLTransport

log = logging.getLogger(__name__)


class GRBLCommunicator:
    def __init__(self, state: AppState) -> None:
        self._state = state
        self._transport: Optional[GRBLTransport] = None
        self._send_lock = asyncio.Lock()
        self._pending_future: Optional[asyncio.Future[str]] = None
        self._last_command: Optional[str] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._read_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def state(self) -> AppState:
        return self._state

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    async def start(self, transport: GRBLTransport) -> None:
        """Attach a transport and start the poll + read loops."""
        self._transport = transport
        self._running = True
        self._state.mark_link_up()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="grbl-poll")
        self._read_task = asyncio.create_task(self._read_loop(), name="grbl-read")

    async def stop(self) -> None:
        """Stop loops and close the transport."""
        self._running = False
        for task in (self._poll_task, self._read_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._transport:
            await self._transport.close()
        self._transport = None
        self._state.mark_disconnected()

    # ------------------------------------------------------------------ #
    # Sending                                                              #
    # ------------------------------------------------------------------ #

    async def send_command(self, command: str, timeout: float | None = None, *, live: bool = False) -> str:
        """
        Send a normal GRBL command and wait for the ok or error response.

        Returns the raw response string: "ok" or "error:N".
        Raises RuntimeError if not connected.
        May raise asyncio.CancelledError if the calling task is cancelled.

        Two ways of bounding the wait:
        - default: a fixed `timeout` (COMMAND_TIMEOUT_S) — console, jog,
          setup commands, which GRBL acknowledges promptly;
        - `live=True` (job lines, lift drains, positioning moves): no fixed
          limit while GRBL is demonstrably alive. GRBL withholds `ok` while
          its planner buffer is full (until the oldest block finishes — on
          this machine one long, slow move can take minutes) and during a
          feed hold, so a fixed timeout would wrongly fail a healthy job. The
          wait fails only if NO status report arrives for GRBL_UNRESPONSIVE_S
          (status polls are real-time and answered even while GRBL blocks).
          A disconnect, reset or alarm still rejects the wait at once.
        A non-live timeout <= 0 waits without limit.
        """
        if self._transport is None or not self._transport.is_open():
            raise RuntimeError("Not connected to GRBL")

        effective_timeout: float | None = timeout if timeout is not None else config.COMMAND_TIMEOUT_S
        if effective_timeout <= 0:
            effective_timeout = None

        async with self._send_lock:
            loop = asyncio.get_running_loop()
            self._pending_future = loop.create_future()
            future = self._pending_future
            try:
                await self._transport.write_line(command)
                self._last_command = command.strip()
                self._state.console_add("tx", command.strip())
                if live:
                    return await self._await_live(future, command)
                result = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=effective_timeout,
                )
                return result
            except asyncio.TimeoutError:
                log.warning("GRBL command timed out: %s", command.strip())
                raise
            finally:
                self._pending_future = None

    async def _await_live(self, future: "asyncio.Future[str]", command: str) -> str:
        """Wait for `future` as long as GRBL keeps answering status polls."""
        loop = asyncio.get_running_loop()
        limit = config.GRBL_UNRESPONSIVE_S
        seq, since = self._state.status_seq, loop.time()
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=min(0.25, limit))
            except asyncio.TimeoutError:
                if self._state.status_seq != seq:
                    seq, since = self._state.status_seq, loop.time()
                elif loop.time() - since > limit:
                    raise asyncio.TimeoutError(
                        f"GRBL unresponsive: no status report for {limit:g} s "
                        f"while waiting for 'ok' to: {command.strip()}"
                    )

    async def send_realtime(self, data: bytes) -> None:
        """Send a GRBL real-time command byte immediately, bypassing the command queue."""
        if self._transport and self._transport.is_open():
            await self._transport.write_raw(data)
        else:
            log.debug("send_realtime: not connected, dropping %r", data)

    def _resolve_pending(self, result: str) -> None:
        """Resolve the pending command future with ok or error."""
        if self._pending_future and not self._pending_future.done():
            self._pending_future.set_result(result)

    def _reject_pending(self, exc: Exception) -> None:
        """Reject the pending command future (e.g., on reset or alarm)."""
        if self._pending_future and not self._pending_future.done():
            self._pending_future.set_exception(exc)
        self._pending_future = None

    # ------------------------------------------------------------------ #
    # Loops                                                                #
    # ------------------------------------------------------------------ #

    async def _poll_loop(self) -> None:
        """Send '?' status requests at the configured interval."""
        while self._running:
            await asyncio.sleep(config.STATUS_POLL_INTERVAL_S)
            if self._transport and self._transport.is_open():
                try:
                    # '?' is a GRBL real-time byte; no newline needed.
                    await self._transport.write_raw(b"?")
                except Exception as exc:
                    log.debug("Poll error: %s", exc)

    async def _read_loop(self) -> None:
        """Read lines from the transport and dispatch them."""
        assert self._transport is not None
        while self._running:
            try:
                line = await self._transport.read_line()
            except Exception as exc:
                log.debug("Read error: %s", exc)
                break

            if line is None:
                # Transport signalled disconnection
                break

            self._dispatch(line)

        self._state.mark_disconnected()
        self._reject_pending(RuntimeError("GRBL disconnected"))
        log.info("GRBL read loop exited")

    def _dispatch(self, line: str) -> None:
        """Route a received line to the appropriate handler."""
        # Status report — response to '?', never gets an 'ok'
        if line.startswith("<"):
            report = parse_status_report(line)
            if report:
                self._state.update_from_status(report)
            self._state.console_add("rx", line)
            return

        # Bracketed messages: [MSG:...], [GC:...], [G54:...], [HLP:...], etc.
        if line.startswith("["):
            kv = parse_bracket(line)
            if kv:
                key, value = kv
                if key == "GC":
                    self._state.parser_state = value
                elif key in ("VER", "OPT"):              # $I build info (read only)
                    self._state.build_info[key] = value
                elif key == "MSG":
                    self._state.add_event("message", value)
                elif key[0] in "GPT" and key not in ("GC",):   # $#: G54…G59, G28, G30, G92, TLO, PRB
                    self._state.offsets[key] = value
            self._state.console_add("rx", line)
            return

        # Welcome / reset message
        if is_welcome_message(line):
            version = parse_grbl_version(line)
            if version:
                self._state.grbl_version = version
            self._state.mark_connected_idle()
            self._state.add_event("reset", line)
            self._state.add_hw_event("grbl", f"GRBL started / reset: {line}")
            # Cancel any pending command — GRBL reset mid-command
            self._reject_pending(RuntimeError("GRBL reset"))
            self._state.console_add("rx", line)
            return

        # Normal acknowledgement
        if line == "ok":
            self._resolve_pending("ok")
            self._state.console_add("rx", line)
            return

        # Error response
        if line.startswith("error:"):
            try:
                detail = describe_error(int(line.split(":", 1)[1]))
            except ValueError:
                detail = ""
            if self._last_command:
                detail = f"{detail} (after: {self._last_command})"
            self._state.add_event("error", line, detail)
            self._resolve_pending(line)
            self._state.console_add("rx", line)
            return

        # Alarm
        if line.startswith("ALARM:"):
            self._state.note_alarm(line)  # e.g. "ALARM:1" — machine position may be lost
            self._reject_pending(RuntimeError(line))
            self._state.console_add("rx", line)
            return

        # `$N=value` lines of a `$$` listing: remembered (read only)
        setting = parse_setting(line)
        if setting:
            self._state.grbl_settings[setting[0]] = setting[1]

        # Everything else (info lines during $ commands, etc.)
        self._state.console_add("rx", line)
