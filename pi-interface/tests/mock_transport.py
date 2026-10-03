"""
Minimal GRBL serial test double.

Simulates only the serial protocol surface needed to test application
behavior: responding with ok, returning status reports, simulating errors,
and simulating disconnection.

This is NOT a GRBL motion emulator. It has no knowledge of G-code, axis
limits, physics, or real machine behavior.
"""
from __future__ import annotations

import asyncio
from typing import Optional


class MockTransport:
    """
    In-memory transport for testing.

    Test code injects what GRBL 'sends' via inject(). The application's
    writes are captured in sent_lines / raw_sent for assertion.

    The asyncio.Queue is created lazily on first access so that it is always
    created inside a running event loop — avoiding Python 3.9 event-loop
    binding issues when the object is constructed in a synchronous fixture.
    """

    def __init__(self, auto_ok: bool = False) -> None:
        """
        Args:
            auto_ok: if True, every write_line call automatically triggers
                     an 'ok' response, useful for tests that only care about
                     streaming logic rather than response handling.
        """
        self._rx: Optional[asyncio.Queue] = None  # lazy init
        self._tx_lines: list[str] = []
        self._raw_writes: list[bytes] = []
        self._open: bool = True
        self._auto_ok = auto_ok

    def _queue(self) -> asyncio.Queue:
        """Return the receive queue, creating it in the current event loop if needed."""
        if self._rx is None:
            self._rx = asyncio.Queue()
        return self._rx

    # ---------------------------------------------------------------- #
    # GRBLTransport interface                                           #
    # ---------------------------------------------------------------- #

    async def read_line(self) -> Optional[str]:
        return await self._queue().get()

    async def write_line(self, line: str) -> None:
        stripped = line.strip()
        self._tx_lines.append(stripped)
        if self._auto_ok and stripped and not stripped.startswith("?"):
            # Status polls (?) are not commands — no ok response
            await self._queue().put("ok")

    async def write_raw(self, data: bytes) -> None:
        self._raw_writes.append(data)

    def is_open(self) -> bool:
        return self._open

    async def close(self) -> None:
        if self._open:
            self._open = False
            await self._queue().put(None)  # sentinel: signals disconnection

    # ---------------------------------------------------------------- #
    # Test helpers                                                      #
    # ---------------------------------------------------------------- #

    async def inject(self, line: Optional[str]) -> None:
        """Put a line into the receive queue as if GRBL sent it."""
        await self._queue().put(line)

    def inject_nowait(self, line: Optional[str]) -> None:
        """Non-async variant for use in synchronous test helpers."""
        self._queue().put_nowait(line)

    async def disconnect(self) -> None:
        """Simulate the serial port dropping."""
        self._open = False
        await self._queue().put(None)

    @property
    def sent_lines(self) -> list[str]:
        """All lines the application wrote (in order)."""
        return list(self._tx_lines)

    @property
    def raw_sent(self) -> list[bytes]:
        """All raw byte writes the application made."""
        return list(self._raw_writes)

    def last_sent(self) -> Optional[str]:
        return self._tx_lines[-1] if self._tx_lines else None

    def clear_sent(self) -> None:
        self._tx_lines.clear()
        self._raw_writes.clear()


# -------------------------------------------------------------------- #
# Shared test helpers                                                   #
# -------------------------------------------------------------------- #

async def wait_for(predicate, timeout: float = 1.0, interval: float = 0.01) -> None:
    """Poll predicate until it returns True, or raise TimeoutError."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        await asyncio.sleep(interval)
        if loop.time() > deadline:
            raise asyncio.TimeoutError("Condition not met within timeout")


async def wait_until_sent(transport: "MockTransport", count: int, timeout: float = 2.0) -> None:
    """
    Wait until transport.sent_lines has at least `count` entries.

    In GRBLCommunicator.send_command(), the pending_future is created BEFORE
    write_line() is called.  So by the time a command appears in sent_lines,
    the future already exists and it is safe to inject an 'ok' response.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while len(transport.sent_lines) < count:
        await asyncio.sleep(0.01)
        if loop.time() > deadline:
            raise asyncio.TimeoutError(
                f"Expected {count} sent lines, got {len(transport.sent_lines)}"
            )


IDLE_STATUS = "<Idle|MPos:0.000,0.000,0.000|FS:0,0>"
RUN_STATUS  = "<Run|MPos:5.000,2.000,0.000|FS:1000,0>"
HOLD_STATUS = "<Hold:0|MPos:5.000,2.000,0.000|FS:0,0>"
ALARM_STATUS = "<Alarm:1|MPos:0.000,0.000,0.000>"
WELCOME_MSG = "Grbl 1.1f ['$' for help]"
