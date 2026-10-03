"""
Serial transport abstraction for GRBL communication.

GRBLTransport defines the interface. SerialTransport is the production
implementation using pyserial. Tests use a MockTransport defined in
tests/mock_transport.py.
"""
from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod

try:
    import serial
    import serial.serialutil
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False


class GRBLTransport(ABC):
    """Interface for all GRBL serial transports."""

    @abstractmethod
    async def read_line(self) -> str | None:
        """
        Read one complete line from GRBL.
        Blocks until a line is available.
        Returns None to signal that the connection has been lost.
        """

    @abstractmethod
    async def write_line(self, line: str) -> None:
        """Write a G-code or $ command. A newline is appended automatically."""

    @abstractmethod
    async def write_raw(self, data: bytes) -> None:
        """Write raw bytes immediately (used for GRBL real-time commands)."""

    @abstractmethod
    def is_open(self) -> bool:
        """True if the transport is currently connected and open."""

    @abstractmethod
    async def close(self) -> None:
        """Close the transport cleanly."""


class SerialTransport(GRBLTransport):
    """
    Production serial transport using pyserial.

    Serial reads run in a dedicated background thread and are forwarded into
    an asyncio Queue via call_soon_threadsafe so the application's event loop
    never blocks. Writes use asyncio.to_thread for the same reason.
    """

    def __init__(self, port: str, baud_rate: int) -> None:
        if not _SERIAL_AVAILABLE:
            raise RuntimeError("pyserial is not installed")
        self._port = port
        self._baud_rate = baud_rate
        self._serial: serial.Serial | None = None
        self._read_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._running = False
        self._reader_thread: threading.Thread | None = None

    async def connect(self) -> None:
        """Open the serial port and start the background reader thread."""
        loop = asyncio.get_running_loop()
        self._serial = serial.Serial(
            self._port,
            self._baud_rate,
            timeout=1,
            write_timeout=2,
        )
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(loop,),
            daemon=True,
            name="grbl-serial-reader",
        )
        self._reader_thread.start()

    def _reader_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Background thread: read lines from serial and put them on the asyncio queue."""
        assert self._serial is not None
        while self._running:
            try:
                raw = self._serial.readline()
            except Exception:
                # Serial port lost — signal disconnection with sentinel
                self._running = False
                loop.call_soon_threadsafe(self._read_queue.put_nowait, None)
                return

            if raw:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    loop.call_soon_threadsafe(self._read_queue.put_nowait, line)

        loop.call_soon_threadsafe(self._read_queue.put_nowait, None)

    async def read_line(self) -> str | None:
        return await self._read_queue.get()

    async def write_line(self, line: str) -> None:
        assert self._serial is not None
        data = (line.strip() + "\n").encode("ascii", errors="replace")
        await asyncio.to_thread(self._serial.write, data)

    async def write_raw(self, data: bytes) -> None:
        assert self._serial is not None
        await asyncio.to_thread(self._serial.write, data)

    def is_open(self) -> bool:
        return self._running and self._serial is not None and self._serial.is_open

    async def close(self) -> None:
        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
