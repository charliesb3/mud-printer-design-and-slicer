# Raspberry Pi / Printer Interface — Project Memory

## Purpose

Develop a printer-specific higher-level control system that runs on a Raspberry Pi and replaces the role currently served by Universal G-code Sender (UGS).

The goal is not merely to clone UGS.

The goal is a simpler, more appropriate interface specifically designed around the actual workflow of this large-scale mud printer.

---

## Technology Stack — Decided

- **Runtime**: Python 3.9+ on Raspberry Pi (Mac for development)
- **Web framework**: FastAPI (native async WebSocket support)
- **ASGI server**: uvicorn
- **Serial**: pyserial in a background reader thread; asyncio.Queue bridges thread→async
- **Frontend**: Vanilla HTML/CSS/JS (no framework, no CDN, no build step)
- **Testing**: pytest + pytest-asyncio (asyncio_mode=auto)

Rationale: simple, offline-capable, no Node.js toolchain, minimal surface area,
Pi-deployable, easy to maintain.

---

## Architecture — Implemented

### Communication Layer

```
Serial port (pyserial thread)
    → asyncio.Queue (via call_soon_threadsafe)
    → GRBLCommunicator._read_loop (asyncio task)
    → AppState updates + Future resolution
```

**GRBLCommunicator** owns:
- `_send_lock`: ensures one command/response pair at a time
- `_pending_future`: resolved by ok/error; rejected by alarm/reset
- `send_command(cmd, timeout)`: acquires lock, creates future, writes,
  awaits `asyncio.wait_for(asyncio.shield(future), timeout)`
- `send_realtime(data: bytes)`: bypasses lock entirely (for !, ~, 0x85, 0x18, ?)

### Job Runner

`JobRunner` is an `asyncio.Task` created by the server on job start.
It is **independent of any WebSocket session** — it survives browser
disconnect/reconnect.

Streaming: send line → await ok → send next (conservative, no char counting).
Hold: busy-poll on job.state every 50ms while state=="held".
Stop: task.cancel() from the HTTP handler; CancelledError propagates out.
End: job.state="completed" when all lines sent or M2/M30/% encountered.

### WebSocket

- `ConnectionManager` broadcasts to all connected clients
- On connect: full state snapshot sent immediately (supports reconnection)
- Broadcast loop: every 200ms unconditionally
- Client-side: auto-reconnect with 1.5s delay

### Dead-Man Jog

Jog sends incremental `$J=G91 {axis}{step_mm:.3f} F{speed}` commands in a loop.
`step_mm = JOG_SPEED_MM_MIN * JOG_SEND_INTERVAL_S / 60`

The jog loop terminates if:
- The browser sends `jog_stop`
- The last heartbeat is older than `JOG_TIMEOUT_S` (0.3s default)

On exit, the loop sends `b"\x85"` (GRBL jog cancel real-time byte).

**Safety invariant**: if the browser closes, crashes, or loses Wi-Fi while a
jog button is held, motion stops within one `JOG_TIMEOUT_S` interval.
This is the fail-safe requirement. It does NOT rely on the browser successfully
sending a release message.

---

## File Layout

```
pi-interface/
├── app/
│   ├── config.py           # All settings, loaded from .env
│   ├── state.py            # AppState + JobInfo dataclasses
│   ├── main.py             # FastAPI app, lifespan, background loops
│   ├── grbl/
│   │   ├── parser.py       # Pure GRBL response parsing (no I/O)
│   │   ├── transport.py    # GRBLTransport ABC + SerialTransport
│   │   └── communicator.py # GRBLCommunicator (lock + future + loops)
│   ├── jobs/
│   │   ├── current_job.py  # CurrentJob dataclass + load_job_from_file()
│   │   └── runner.py       # JobRunner (asyncio task, server-owned)
│   ├── api/
│   │   ├── ws.py           # ConnectionManager (broadcast to all clients)
│   │   └── http.py         # All HTTP + WebSocket route handlers
│   └── static/
│       ├── index.html      # Single-page app
│       ├── style.css       # Dark touch-first theme
│       └── app.js          # WS client, jog events, stop hold-to-confirm
├── tests/
│   ├── mock_transport.py   # MockTransport + wait_until_sent helper
│   ├── test_parser.py      # 34 tests
│   ├── test_communicator.py # 18 tests
│   ├── test_runner.py      # 13 tests
│   └── test_api.py         # 13 tests
├── docs/
│   ├── grbl-capture-checklist.md  # Capture $$ baseline before first use
│   ├── deployment.md              # Pi OS, systemd, kiosk, networking
│   └── acceptance-test.md         # Physical machine acceptance criteria
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

---

## Configuration (.env)

All settings in `.env`. Key values:

| Variable | Default | Notes |
|---|---|---|
| `SERIAL_PORT` | `/dev/ttyUSB0` | Set per machine |
| `BAUD_RATE` | `115200` | Standard GRBL baud |
| `JOG_SPEED_MM_MIN` | `0` | **0 = jogging disabled**. Must be validated on machine before enabling |
| `JOG_SEND_INTERVAL_S` | `0.15` | Interval between incremental jog commands |
| `JOG_TIMEOUT_S` | `0.3` | Dead-man timeout — motion stops if no heartbeat |
| `STATUS_POLL_INTERVAL_S` | `0.2` | How often `?` is sent to GRBL |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | HTTP port |

---

## Key Decisions

### Whole-building job with first-class lifts (decided 2026-10-14)
- The Pi receives a WHOLE BUILDING as ONE print job.
- The job is an ordered sequence of first-class printable LIFTS, determined
  upstream by the authoritative PrintPlan (Design + Toolpath,
  `layer_assembly/print_plan.py`). The Pi never recalculates lift boundaries.
- Lifts are job structure the Pi understands — not separate G-code files and
  not merely opaque comments.
- Completing a lift pauses progression: the next lift cannot run until the
  operator performs the physical frame reposition and explicitly confirms it.
- Underneath, GRBL still receives ordinary machine G-code.

Implementation notes from the 2026-10-14 inspection (proposed, not yet built):
- Boundary: a versioned JSON job container defined by the Pi
  (`format`, `version`, `units`, `source`, `lifts[]` each with index, z range,
  layer range, `reposition_before` {rise, frame z from/to, nozzle xy} and
  plain `gcode` lines), produced by a Design + Toolpath exporter from
  `PrintPlan.items()` (which already carries lift/stage/pass per move and a
  `FrameMove` per later lift). Format spec + one shared fixture; the Pi does
  not import design-toolpath code. Plain `.gcode` uploads stay valid as a
  one-lift job (M1 behaviour preserved).
- Lift end: GRBL `ok` means buffered, not executed — the runner must drain
  (`G4 P0`) and see status Idle before declaring the lift complete.
- Confirmation must name the lift it confirms (prevents stale/double confirm).
- M0/M1/M2/M30/% are not allowed inside a lift's G-code; the Pi owns pauses.
- No G-code export exists yet in Design + Toolpath. PrintPlan units are
  inches, world coordinates, Z = building Z of the layer top.

### Dead-man jog — incremental commands + heartbeat timeout
Jogging uses short incremental `$J=` moves sent in a loop. The loop dies if
the browser stops sending heartbeats. A separate jog cancel byte (0x85) is sent
on loop exit. This guarantees machine stop on browser disconnect without relying
on a release message arriving.

Previous approach considered and rejected: one large jog move that requires a
separate stop message. Rejected because browser disconnect during jog would
leave the machine running.

### Server-owned job runner
The job runner is an asyncio.Task on the server. Browser connect/disconnect has
no effect on it. This is a hard requirement from the user.

### Conservative streaming (line-at-a-time)
Send one line, wait for ok, send next. No character-counting buffer. Simpler,
safe, appropriate for this machine scale. Can revisit if throughput becomes a
bottleneck (unlikely for large-scale printing with slow moves).

### GRBL configuration must not be modified
The Pi interface communicates through the GRBL serial protocol only.
It never sends `$$=` or `$N=` commands. Existing machine settings are preserved.

### Z is out of scope for Milestone 1
Z is a manually controlled linear actuator. No Z commands, no Z jog UI,
no Z architecture decisions made. Future automatic Z control method is TBD.

### Extrusion is out of scope
The external pumping system is independently controlled. Not addressed here.

### Homing / limit switches not required for Milestone 1
No homing commands in the UI. No assumption about limit switch configuration.

### Physical E-stop is the real emergency stop
Software Stop is a convenience. The physical hardwired E-stop is the machine's
actual safety device. This is explicitly displayed in the UI.

### JOG_SPEED_MM_MIN=0 default
Jogging is disabled by default until the speed is validated on the actual
machine. The UI shows a warning if jogging is disabled.

### asyncio.shield() in send_command
The pending future is shielded from task cancellation. This prevents a cancelled
runner task from corrupting the communicator's internal state, which must remain
usable after the task ends.

### Lazy asyncio.Queue in MockTransport
Queue is created on first access within a running event loop. This avoids
Python 3.9 event-loop binding issues when the object is constructed in a
synchronous fixture context.

### FastAPI lifespan context manager (not on_event)
`@app.on_event("startup")` is deprecated in current FastAPI. Using the
`@asynccontextmanager lifespan` pattern instead.

---

## Test Strategy

Tests use `MockTransport` — an in-memory transport that captures all writes
and lets the test inject GRBL responses via `inject()`.

Key invariant used in hold/resume tests: in `send_command()`, `_pending_future`
is created **before** `write_line()` appends to `sent_lines`. Therefore,
`wait_until_sent(transport, n)` guarantees the future exists before injecting ok.

78 tests total, all passing on Python 3.9 (Mac development environment).

---

## Current State

**Milestone 1 software implementation is complete on Mac.**

All application code has been written:
- GRBL parsing, transport, communicator
- Job loading, runner
- WebSocket connection manager
- HTTP + WebSocket route handlers
- Touch-first single-page frontend
- Full test suite (78 tests, all passing)
- Deployment docs, GRBL capture checklist, acceptance test procedure

**Not yet complete:**
- Physical machine testing on the Pi (hardware not yet available)
- Pi deployment and systemd configuration (depends on hardware)
- Jog speed validation on the physical machine
- Wi-Fi access point setup for offline field use

---

## Open Questions

- What is the correct JOG_SPEED_MM_MIN for this machine's scale?
  (Must be established by physical testing before jogging is enabled)
- What serial port does the Arduino appear as on the Pi (ttyUSB0 or ttyACM0)?
- Is a udev rule needed to assign a stable symlink to the Arduino port?
- Does the Pi need to act as a Wi-Fi access point for field use?
  If so, hostapd + dnsmasq setup is needed (not yet done).
- Will the current GRBL firmware support all required jog and job operations?
  (Expected yes, but unverified on hardware)
- After a power failure mid-print, should the interface offer job recovery?
  (Not implemented in Milestone 1 — open question for future milestones.
  Lift boundaries are natural safe restart points: persist progress there
  and offer "start at lift N"?)

Lift-job questions (2026-10-14, need user decisions before the exporter):
- Z inside a lift: Z is manual, but layers, back-down moves to another
  island (dz < 0) and clear-height travels all change Z. XY-only G-code with
  manual Z, per-layer operator pauses, or other?
- Travels/transitions with the pump independently running: plain moves or
  operator pause?
- Does a frame raise preserve XY registration / GRBL position, or involve
  power-down / reset / re-zeroing? (Determines whether jog/console must be
  allowed while awaiting reposition — both are blocked during an active job.)
- XY work zero: operator-set before start, or an offset in the export?
  G20 (inches, matching the plan) or convert to G21 mm?
- Confirmation strength: single hold-to-confirm or a checklist?

---

## Next Steps

1. Complete GRBL configuration capture (grbl-capture-checklist.md) on the
   existing working machine before any Pi connection.
2. Deploy the Pi interface on the Raspberry Pi (docs/deployment.md).
3. Run the physical machine acceptance test (docs/acceptance-test.md).
4. Validate and set JOG_SPEED_MM_MIN in .env.
5. If field offline use is needed, configure Wi-Fi access point.
6. After Milestone 1 is verified on hardware, define Milestone 2.

Lift-aware job work (software, can proceed before hardware; proposed stages):
- P1: lift-aware `CurrentJob` (keeps flat `lines` view so M1 tests stand),
  JSON job loader + validation, runner streams per lift, drains + waits Idle
  at lift end, state `awaiting_reposition`, WS `confirm_reposition {lift}`.
  Mock-transport tests. Independent of the Z decision.
- P2: UI — lift list, boundary panel with frame rise, hold-to-confirm,
  lift/line progress.
- P3: Design + Toolpath exporter PrintPlan → job (blocked on Z / travel /
  origin / units decisions above); round-trip test against the Pi loader.
- P4 (optional): persist progress at lift boundaries; start at lift N.

---

## Completed Work

- Milestone 1 software architecture designed and approved
- Technology stack selected: Python, FastAPI, pyserial, vanilla JS
- All application modules implemented
- Touch-first browser UI implemented
- Dead-man jog implemented and tested
- Server-owned job runner implemented and tested
- Full test suite: 78 tests passing
- Deployment documentation written
- GRBL capture checklist written
- Acceptance test procedure written

---

## Last Updated

2026-10-14

Decision: whole building = one job of first-class PrintPlan lifts with an
operator-confirmed frame reposition between lifts. Inspection done; staged
plan P1–P4 recorded; nothing implemented yet. M1 tests: 78 passing.

2026-10-03

Milestone 1 software implementation complete on Mac.
Physical machine testing pending.
