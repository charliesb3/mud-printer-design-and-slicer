"""tools/grbl_capture.py: the pre-change controller capture sends read-only queries only."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "grbl_capture", Path(__file__).resolve().parents[1] / "tools" / "grbl_capture.py")
grbl_capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grbl_capture)


class FakeSerial:
    """Answers like a GRBL 1.1 Uno after its open-reset."""

    def __init__(self):
        self.written: list = []
        self.rx = [b"\r\n", b"Grbl 1.1h ['$' for help]\r\n"]

    def write(self, data: bytes):
        self.written.append(data)
        cmd = data.decode().strip()
        replies = {"?": ["<Idle|MPos:0.000,0.000,0.000|FS:0,0>"],
                   "$I": ["[VER:1.1h.20190825:]", "[OPT:V,15,128]", "ok"],
                   "$$": ["$0=10", "$100=80.000", "ok"],
                   "$#": ["[G54:0.000,0.000,0.000]", "ok"],
                   "$G": ["[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]", "ok"]}[cmd]
        self.rx += [(r + "\r\n").encode() for r in replies]

    def readline(self) -> bytes:
        return self.rx.pop(0) if self.rx else b""


def test_capture_sends_only_whitelisted_queries():
    ser = FakeSerial()
    lines = grbl_capture.capture(ser, wait_welcome_s=1.0)
    assert [w.decode().strip() for w in ser.written] == ["?", "$I", "$$", "$#", "$G"]
    text = "\n".join(lines)
    assert "Grbl 1.1h" in text and "[VER:1.1h.20190825:]" in text and "$100=80.000" in text
    assert "[GC:" in text and "[G54:" in text


@pytest.mark.parametrize("cmd", ["$100=80", "$X", "$H", "$RST=*", "G0 X0", "\x18", "$J=G91 X1 F10"])
def test_refuses_anything_else(cmd):
    with pytest.raises(ValueError):
        grbl_capture.run_query(FakeSerial(), cmd)
