# Raspberry Pi / Printer Interface — Project Memory

## Purpose

Develop a printer-specific higher-level control system that runs on a Raspberry Pi and replaces the role currently served by Universal G-code Sender (UGS).

The goal is not merely to clone UGS.

The goal is a simpler, more appropriate interface specifically designed around the actual workflow of this large-scale mud printer.

---

## Hardware

The real control-box interfaces (Pi 4, Arduino Uno / GRBL over USB, planned
Cytron MD13S for Z on Pi GPIO, planned Kaleinavi RF receiver on Pi GPIO,
power, E-stop, VERIFY items, superseded parts) are documented in
`../CONTROL_BOX_HARDWARE.md`. Consult it before writing code that touches
serial, GPIO, Z, the remote, limits or power-loss behaviour.

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

Lifts (2026-10-14): the runner streams `job.lifts` in order (a flat G-code
file = one lift → identical M1 behaviour). After every non-final lift:
`G4 P0` drain (no timeout by default, `LIFT_DRAIN_TIMEOUT_S`) → wait for a
status report newer than the dwell's ok showing Idle (`AppState.status_seq`)
→ `awaiting_reposition` (`awaiting_lift` = next lift) → poll until the
confirm handler sets `running`. GRBL disconnect or reset (`AppState.reset_seq`)
while awaiting → job `error`. Hold pressed during the drain is honoured
before the boundary.

### WebSocket

- `ConnectionManager` broadcasts to all connected clients
- On connect: full state snapshot sent immediately (supports reconnection)
- Broadcast loop: every 200ms unconditionally; also immediately after any
  state-changing action (hold/resume/stop/job_start/confirm_reposition, upload)
- Client-side: auto-reconnect with 1.5s delay
- `notice {level, message}`: refusal sent only to the requesting client
- `AppState.job` is the live `CurrentJob` (shared reference; the duplicate
  `JobInfo` mirror was removed)

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
│   ├── state.py            # AppState (job = live CurrentJob reference)
│   ├── main.py             # FastAPI app, lifespan, background loops
│   ├── grbl/
│   │   ├── parser.py       # Pure GRBL response parsing (no I/O)
│   │   ├── transport.py    # GRBLTransport ABC + SerialTransport
│   │   └── communicator.py # GRBLCommunicator (lock + future + loops)
│   ├── jobs/
│   │   ├── current_job.py  # CurrentJob + JobLift, .mudjob parsing/validation, load_job_from_file()
│   │   └── runner.py       # JobRunner (asyncio task, server-owned)
│   ├── api/
│   │   ├── ws.py           # ConnectionManager (broadcast to all clients)
│   │   └── http.py         # All HTTP + WebSocket route handlers
│   └── static/
│       ├── index.html      # Single-page app
│       ├── style.css       # Dark touch-first theme
│       └── app.js          # WS client, jog, hold buttons (stop, confirm), lifts UI
├── tests/
│   ├── mock_transport.py   # MockTransport + wait_until_sent helper
│   ├── test_parser.py      # 34 tests
│   ├── test_communicator.py # 18 tests
│   ├── test_runner.py      # 13 tests
│   ├── test_lifts.py       # 66 tests (contract, fixtures, sequencing, handler rules)
│   └── test_api.py         # 23 tests (incl. building/export upload, .muddesign refusal, synced clients)
├── docs/
│   ├── grbl-capture-checklist.md  # Capture $$ baseline before first use
│   ├── deployment.md              # Pi OS, systemd, kiosk, networking
│   └── acceptance-test.md         # Physical machine acceptance criteria
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

The `.mudjob` contract lives OUTSIDE this subsystem, at the repository root:
`contracts/mudjob/README.md` (spec) + `contracts/mudjob/fixtures/` (example
files; tests read them as data). Moved there from `docs/job-format.md` /
`tests/fixtures/` in P3.

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

Implemented 2026-10-14 (P1 + P2, contract finalized in P3):
- Boundary: the versioned `.mudjob` file — `contracts/mudjob/README.md`
  (version 1). Plain G-code uploads remain valid as a one-lift job.
- P3: `executable` (required) + `unresolved` [{id, summary}]; `index`
  required = position; `reposition_before` required on every later lift
  (forbidden on the first); display-only lift metadata passed through
  (`layers`, `z_bottom`, `z_top`, stages / passes / runs / lengths); unknown
  keys ignored. The P1 draft of version 1 (gcode only) was superseded before
  any exporter existed.
- NON-EXECUTABLE job: loaded and displayed (lift list, unresolved items, amber
  "structure only" block); START disabled and `job_start` refused by the
  server with a notice. Never run.
- `.muddesign` (Designer project file, by extension or its format
  "mud-printer-project") is refused at upload with a message pointing to
  Export Print Job.
- `reposition_before` / `units` / `name` are display-only: the Pi shows them
  and interprets nothing (no unit conversion, no Z, no origin handling).
- Lift end: GRBL `ok` means buffered, not executed — the runner drains with
  `G4 P0` and waits for a fresh Idle report before the boundary.
- Confirmation names the lift it confirms (stale/double confirm refused), is
  accepted only while GRBL is Idle, and `resume` never confirms.
- M0/M1/M2/M30/%/`$` are rejected inside lift G-code; the Pi owns pauses.
- While `awaiting_reposition` the job is ACTIVE: jog, console, upload and
  start are refused (conservative until decision on frame raise / re-zeroing;
  revisit if the operator must jog or re-zero between lifts).
- GRBL disconnect/reset while awaiting → job error (position untrusted; no
  resume-at-lift yet).
- Design + Toolpath exports `.mudjob` (P3) but no G-code: every export is
  non-executable until the machine semantics are decided.

### Independent of Design + Toolpath — the `.mudjob` file is the only boundary (decided 2026-10-14)
The Pi interface never imports Design + Toolpath code, shares no runtime
modules with it, never calls its API and must run without it installed. It
parses and validates `.mudjob` files itself; a job exported elsewhere is
copied (USB / email / network) to an independent Pi. Interoperability is
tested with the contract fixture FILES (`contracts/mudjob/fixtures/`, incl.
the Designer's real export `designer-export-3-lifts.mudjob`), never by
cross-importing. (The tests read `../contracts`; on a Pi with only
`pi-interface/` deployed those tests need the contract folder copied too.)

### One responsive UI, synchronized clients (decided 2026-10-14)
There is ONE web UI — not separate desktop and touchscreen interfaces.
Developed/tested on the computer first; later the Pi touchscreen shows the
same UI locally in kiosk/fullscreen mode. Laptop and touchscreen may be
connected at once as equal, synchronized views of the one Pi-owned
machine/job state: any client's action is broadcast to all; connecting or
disconnecting a client never affects an active print. Controls stay
touch-sized but work with mouse and keyboard (hold buttons accept a held
Space/Enter). Deliberate actions use press-and-hold: Stop 2.5 s, Confirm
frame reposition 2 s (the confirm sends the lift shown when the hold began).

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

2026-10-14 (control-box baseline): the Pi WILL be the sole Z command source —
GPIO PWM + DIR to a Cytron MD13S driving the 12 V actuator; manual Z from the
UI and the remote's Z UP / Z DOWN. Open-loop (no limits / homing / position
feedback), so Z software must be dead-man / time-bounded and never assume a
known Z height. Not implemented; GPIO pins not assigned (pending the
touchscreen). The old SRDKG RF Z controller is not part of the architecture.

### Wireless remote = Pi GPIO inputs (planned, 2026-10-14)
Kaleinavi 6-channel RF receiver dry contacts → Pi GPIO (internal pull-ups,
active-low, ~1 kΩ series). Tentative functions: REMOTE STOP, Z UP, Z DOWN,
extrusion pause / resume, two spare — mapping configurable in software.
REMOTE STOP is an operational software stop, NOT an E-stop; the UI must
never present it as one. Not implemented.

### Extrusion is out of scope
The external pumping system is independently controlled. Not addressed here.

### Homing / limit switches not required for Milestone 1
No homing commands in the UI. No assumption about limit switch configuration.

### Physical E-stop is the real emergency stop
(2026-10-14: the E-stop is upstream of every PSU, so it also powers down the
Pi and the USB-powered Uno — after an E-stop the controller cold-boots and
in-memory job state is lost; VERIFY. Relevant to job recovery / P4.)
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

154 tests total, all passing on Python 3.9 (Mac development environment):
the 78 M1 tests unchanged + 76 lift / building-job / contract tests. `MockTransport` can
answer `?` polls with a settable `status_reply` (used to simulate Run→Idle
around a lift drain). API tests that need machine state set directly patch
out `main._connection_loop` (`offline_client` fixture).

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

**Lift-aware building jobs (P1 + P2) implemented on Mac, 2026-10-14:**
`.mudjob` loading/validation, per-lift runner with drain +
`awaiting_reposition` + lift-named confirmation, synchronized-client
broadcasts, UI (status-bar lift badge, lift chips, lift progress, amber
reposition panel with 2 s hold-to-confirm, refusal notices). 139 tests pass.
Also exercised end to end against a throwaway fake-GRBL server with two
websocket clients (3-lift fixture: boundaries, cross-client confirm, stale
confirm refused, reconnect mid-job). NOT yet visually reviewed in a browser
or on the touchscreen, and not run on hardware.

**`.mudjob` import of the Designer's export (P3), 2026-10-14:** finalized
contract validation, non-executable jobs displayed but never run, `.muddesign`
refused, lift-details list in the UI. Verified end to end over HTTP only
(Designer on :5051 plans + exports → uploaded to the Pi on :8765 → lifts
0–2 / 3–5 / 6–7 shown as exported, start refused). 154 tests pass.

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
  (Board recorded as an Uno 2026-10-14: genuine Uno → usually ttyACM0, CH340
  clone → ttyUSB0. The `.env` default is ttyUSB0.)
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
- Confirmation strength: single hold-to-confirm (implemented, provisional)
  or a checklist?
- Is `G4 P0` accepted and does it synchronise as expected on the installed
  GRBL? (Expected per GRBL 1.1; verify on hardware.)

Risk found 2026-10-14 (M1, not yet addressed): line-at-a-time streaming uses
`COMMAND_TIMEOUT_S` = 10 s per line. GRBL withholds `ok` while its planner
buffer is full, so on this machine's long, slow moves a single line's ok can
plausibly exceed 10 s → TimeoutError → job error. Needs a decision/fix before
real prints (e.g. longer/no per-line timeout while a job runs).

---

## Next Steps

1. Complete GRBL configuration capture (grbl-capture-checklist.md) on the
   existing working machine before any Pi connection.
2. Deploy the Pi interface on the Raspberry Pi (docs/deployment.md).
3. Run the physical machine acceptance test (docs/acceptance-test.md).
4. Validate and set JOG_SPEED_MM_MIN in .env.
5. If field offline use is needed, configure Wi-Fi access point.
6. After Milestone 1 is verified on hardware, define Milestone 2.

Lift-aware job work:
- ~~P1~~ / ~~P2~~ done 2026-10-14 (see Current State).
- Manual visual review of the lift UI in a browser (laptop) and later on the
  Pi touchscreen kiosk; adjust layout if needed.
- Decide the per-line timeout risk above before the first real print.
- ~~P3~~ done 2026-10-14: `.mudjob` interchange (non-executable exports).
- Executable export: blocked on the machine decisions (Z within a lift,
  travel with the pump, XY origin, machine units, frame-reposition
  behaviour); then the Designer adds `gcode` per lift + `executable: true`.
- Third-party G-code compatibility / preflight (later, not started).

Hardware-facing work (not started; needs `../CONTROL_BOX_HARDWARE.md` VERIFY
items and the touchscreen pin usage first): Z control via MD13S (dead-man,
time-bounded), RF remote inputs (debounced, UP+DOWN interlock), REMOTE STOP
behaviour definition.
- P4 (optional): persist progress at lift boundaries; start at lift N.
- Add a lift-boundary step to docs/acceptance-test.md once hardware testing
  of lifts is planned.

---

## Completed Work

- Milestone 1 software architecture designed and approved
- Technology stack selected: Python, FastAPI, pyserial, vanilla JS
- All application modules implemented
- Touch-first browser UI implemented
- Dead-man jog implemented and tested
- Server-owned job runner implemented and tested
- Full test suite: 78 tests passing
- 2026-10-14: lift-aware building jobs (P1 model/runner/format, P2 UI);
  139 tests passing (commit 08cc8d0, pushed)
- 2026-10-14: P3 `.mudjob` contract finalized + Designer export import;
  154 tests passing
- Deployment documentation written
- GRBL capture checklist written
- Acceptance test procedure written

---

## Last Updated

2026-10-14 (control box)

Hardware baseline cross-referenced (`../CONTROL_BOX_HARDWARE.md`): Pi will
command Z (MD13S) and read the RF remote; REMOTE STOP ≠ E-stop; E-stop also
powers down the Pi. No code changed.

2026-10-14 (P3)

`.mudjob` interchange with Design + Toolpath established: contract moved to
`contracts/mudjob/`, finalized (executable / unresolved), non-executable
Designer exports displayed and refused at start, `.muddesign` refused.
Independence decision recorded. 154 tests passing.

2026-10-14

P1 + P2 implemented: whole-building `.mudjob` jobs with first-class lifts,
drain + `awaiting_reposition` + lift-named hold-to-confirm, one responsive UI
with synchronized clients. 139 tests passing. Exporter (P3) still blocked on
Z / travel / origin / units decisions.

Earlier 2026-10-14: decision recorded — whole building = one job of
PrintPlan lifts with an operator-confirmed frame reposition between lifts.

2026-10-03

Milestone 1 software implementation complete on Mac.
Physical machine testing pending.
