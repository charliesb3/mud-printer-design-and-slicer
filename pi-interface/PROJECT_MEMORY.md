# Raspberry Pi / Printer Interface — Project Memory

## Purpose

Develop a printer-specific higher-level control system that runs on a Raspberry Pi and replaces the role currently served by Universal G-code Sender (UGS).

The goal is not merely to clone UGS.

The goal is a simpler, more appropriate interface specifically designed around the actual workflow of this large-scale mud printer.

---

## Hardware

The real control-box interfaces (Pi 4, Arduino Uno / GRBL over Pi USB,
Cytron MD13S for Z on Pi GPIO, Kaleinavi RF receiver on Pi GPIO, 7" 800×480
DSI touchscreen, power, E-stop, VERIFY items, superseded parts) are
documented in `../CONTROL_BOX_HARDWARE.md`. Consult it before writing code
that touches serial, GPIO, Z, the remote, limits or power-loss behaviour.

**The Pi GPIO pin map is WIRED (2026-10-20) and authoritative in
`../CONTROL_BOX_HARDWARE.md` → "Raspberry Pi GPIO pin map (wired)"** —
together with the software consequences for hardware integration (one
hardware-mapping layer, BCM in code, active-low debounced inputs, dead-man
Z, UP + DOWN = STOP, PWM 0 on every exit path, startup = Z STOP). Do not
renumber the pins; do not copy the table here. UGS is not installed on the
Pi: this application replaces its operator functions.

X/Y on the Uno use the standard GRBL 1.1 pin map (X step D2 after the
2026-10-20 rewire; `../CONTROL_BOX_HARDWARE.md` → "Arduino Uno → DM556T
wiring"); confirm X with a slow jog before the first Pi-driven motion.

---

## Technology Stack — Decided

- **Runtime**: Python 3.9+ on Raspberry Pi (Mac for development)
- **Web framework**: FastAPI (native async WebSocket support)
- **ASGI server**: uvicorn
- **Serial**: pyserial in a background reader thread; asyncio.Queue bridges thread→async
- **Frontend**: Vanilla HTML/CSS/JS (no framework, no CDN, no build step)
- **Testing**: pytest + pytest-asyncio (asyncio_mode=auto)
- **GPIO** (2026-10-20): gpiozero (Raspberry Pi's maintained library,
  preinstalled on Raspberry Pi OS; default pin factory lgpio via
  `/dev/gpiochip*`, no daemon / root). On the Pi from apt
  (`python3-gpiozero`, `python3-lgpio`; venv `--system-site-packages`); on
  the Mac only in requirements-dev (tests use its MockFactory). Imported only
  in `app/hardware/backend.py`. Rejected: RPi.GPIO (legacy, no Pi 5),
  pigpio (daemon, no Pi 5), sysfs hardware PWM (keeps running after the
  process dies — wrong for Z default-STOP).

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
`G4 P0` drain (live ok-wait) → wait for a
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

Jog sends incremental `$J=G21 G91 {axis}{step_mm:.3f} F{speed}` commands in a loop.
(`G21` added 2026-10-14: jobs and positioning moves now leave GRBL in G20;
units in a `$J=` line are non-modal, so jog steps are always millimetres.)
`step_mm = JOG_SPEED_MM_MIN * JOG_SEND_INTERVAL_S / 60`

The jog loop terminates if:
- The browser sends `jog_stop`
- The last heartbeat is older than `JOG_TIMEOUT_S` (0.3s default)

On exit, the loop sends `b"\x85"` (GRBL jog cancel real-time byte).

**Safety invariant**: if the browser closes, crashes, or loses Wi-Fi while a
jog button is held, motion stops within one `JOG_TIMEOUT_S` interval.
This is the fail-safe requirement. It does NOT rely on the browser successfully
sending a release message.

### Hardware layer (2026-10-20; `app/hardware/`)

```
pins.py      THE BCM map (+ physical pins) — no GPIO number anywhere else
backend.py   none (Mac, default) | mock (tests / UI) | gpio (gpiozero) — only place touching pins;
             Z outputs claimed at STOP BEFORE inputs get pull-ups
remote.py    CH1–CH6 polled every 10 ms, debounced (50 ms stable), active LOW;
             first stable state only after 200 ms; a channel closed at startup is NOT ARMED
             until seen released
z.py         ZController — the ONE manual Z: per-source dead-man requests (each browser
             client, remote_up, remote_down) → STOP | UP | DOWN at the FIXED duty
service.py   HardwareService: one asyncio loop (poll + Z tick); remote → same machine ops as UI
sdnotify.py  (app/) systemd READY / WATCHDOG pings from the event loop
```
Z resolution: no live request → STOP; two directions (UP+DOWN, UI vs remote,
a source reversing) → STOP + BLOCKED until every Z input is released; a
request refused (disabled / fault / REMOTE STOP) is also BLOCKED — it never
starts later on its own; run > `Z_MAX_RUN_S` → STOP + blocked; exception →
STOP + fault (Z refused until restart). Z allowed during a job (manual Z
while printing), independent of GRBL. Hardware events (remote, Z, REMOTE
STOP, GRBL link) go to the log and `AppState.hw_events` — no polling noise.
A failing gpio backend falls back to `none` (no remote, no Z) and is reported.

---

## File Layout

```
pi-interface/
├── app/
│   ├── config.py           # All settings, loaded from .env
│   ├── state.py            # AppState (job = live CurrentJob reference)
│   ├── main.py             # FastAPI app, lifespan, background loops
│   ├── grbl/
│   │   ├── parser.py       # Pure GRBL response parsing (no I/O; status Ov/Pn/Bf/WCO/Ln, [..] lines)
│   │   ├── codes.py        # GRBL 1.1 alarm / error descriptions, setting names (read-only reference)
│   │   ├── transport.py    # GRBLTransport ABC + SerialTransport
│   │   └── communicator.py # GRBLCommunicator (lock + future + loops)
│   ├── jobs/
│   │   ├── current_job.py  # CurrentJob + JobLift, .mudjob parsing/validation, progress, load_job_from_file()
│   │   ├── layout.py       # LiftLayout: effective lift boundaries, clean cuts, adjusting a lift's end
│   │   ├── motion.py       # G-code motion analysis: time / distance estimates, 2D path points
│   │   └── runner.py       # JobRunner (asyncio task, server-owned)
│   ├── api/
│   │   ├── ws.py           # ConnectionManager (broadcast to all clients)
│   │   └── http.py         # All HTTP + WebSocket route handlers
│   └── static/
│       ├── index.html      # Single-page app
│       ├── style.css       # Dark touch-first theme
│       └── app.js          # WS client, jog, hold buttons (stop, confirm), lifts UI
├── scripts/
│   ├── Deploy to Pi.command        # Mac double-click launcher (one-click deploy)
│   ├── deploy.config.sample        # Template → copy to deploy.config (gitignored)
│   ├── deploy_pi.sh                # Runs ON the Pi: preflight + git pull + restart
│   ├── consolidation_migrate.sh    # One-time M4 atomic swap + venv rewrite + auto-rollback
│   └── preflight_state.py          # Shared pass/block decision (unit-tested)
├── tests/
│   ├── mock_transport.py   # MockTransport + wait_until_sent helper
│   ├── test_parser.py      # 34 tests
│   ├── test_communicator.py # 18 tests
│   ├── test_runner.py      # 13 tests
│   ├── test_lifts.py       # 82 tests (contract + current-machine lift rules, fixtures incl. executing the Designer export, sequencing, handler rules)
│   ├── test_setup.py       # 25 tests (live ok-wait, origin / prime, Start returns to origin, reposition re-zero)
│   ├── test_progress.py    # 60 tests (motion estimates, progress, adaptive lifts, lift parameters, feed override, outline, diagnostics, path, multi-client)
│   ├── test_ui.py          # 6 tests (dashboard structure; UI proposal rule == server rule, via Node)
│   └── js/propose_check.js # runs app.js proposeLiftEnd for test_ui.py
│   └── test_api.py         # 25 tests (building/export upload, .muddesign refusal, synced clients incl. setup)
│   ├── test_hardware.py    # GPIO map, gpiozero backend (MockFactory), remote, Z, REMOTE STOP, lifecycle
│   ├── test_grbl_capture.py # capture tool sends read-only queries only
│   └── test_preflight_state.py # Deploy preflight pass/block decision (one-click deploy)
├── tools/grbl_capture.py   # read-only $I $$ $# $G capture to a file (service stopped)
├── deploy/                 # mudprinter.service (systemd), kiosk.sh (labwc), 99-mudpr-grbl.rules.example
├── docs/
│   ├── grbl-capture-checklist.md  # read-only capture + prepared (not executed) flash procedure
│   ├── deployment.md              # Pi OS, config.txt Z STOP, venv, systemd, udev, kiosk, Z default-STOP layers
│   ├── one-click-deploy.md        # Mac → Pi one-click deploy workflow
│   └── acceptance-test.md         # 10-step control-box commissioning + M1 functional checks
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
| `GRBL_UNRESPONSIVE_S` | `5.0` | Job lines / drains / positioning moves fail only after this long WITHOUT any status report (replaced `LIFT_DRAIN_TIMEOUT_S`) |
| `HARDWARE_BACKEND` | `none` | `none` (Mac) / `mock` / `gpio` (the Pi) |
| `REMOTE_POLL_INTERVAL_S` / `REMOTE_DEBOUNCE_S` | `0.01` / `0.05` | remote sampling / debounce |
| `Z_PWM_DUTY` | `0` | **0 = manual Z disabled**; fixed duty for every manual Z move, set at commissioning |
| `Z_PWM_FREQUENCY_HZ` | `1000` | lgpio software PWM (MD13S ≤ 20 kHz) |
| `Z_DIR_INVERT` | `false` | set if Z UP moves down (actuator test) |
| `Z_TIMEOUT_S` / `Z_MAX_RUN_S` | `0.3` / `30` | Z dead-man / max continuous run (provisional) |
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
- While `awaiting_reposition` the job is ACTIVE: console, upload and start are
  refused; JOG, SET ORIGIN, SET PRIME and PRIME are allowed (changed
  2026-10-14 with the XY-origin decision — previously jog was refused too).
  Confirmation additionally requires the origin to be established and no
  motion in progress.
- GRBL disconnect/reset while awaiting → job error (position untrusted; no
  resume-at-lift yet).
- Design + Toolpath exports EXECUTABLE `.mudjob` jobs (current-machine pass):
  each lift `G20` + `G90` + absolute `G1` XY moves; the Pi validates per lift:
  units + distance mode before any XY motion, no Z words, no G10 / G92
  (`parser.lift_gcode_reason`). `layer_marks` (mapped to sendable-line
  indices) drive the "Layer n · nozzle Z … — Z is manual" cue; `xy_bounds`
  shown in lift details.

### Print origin, prime position, Start returns to origin (decided 2026-10-14)
Current machine: XY-only jobs, manual Z, manual pump. The building toolpath
is in WORK coordinates whose origin the operator establishes (contract:
`contracts/mudjob/README.md`). Pi-owned `AppState.setup` (`MachineSetup`, in
memory):
- **Set Origin** (UI hold 1.5 s; `set_origin`): `$$` (read-only, learns `$13`
  report units), `G54`, `G10 L20 P1 X0 Y0` (work XY zero = here; Z untouched;
  G10 stores the G54 offset in GRBL EEPROM). Records the machine position.
- **Set Prime Position** (`set_prime`): current work XY in inches (from MPos −
  origin MPos, or WPos if `$10` reports it; converted with `$13`). Stored
  RELATIVE to the origin, so it follows a re-zero.
- **Prime** (UI hold 1 s; `prime`): background move `G54 G20 G90 G1 X… Y…`
  at the jog speed, then `G4 P0` + Idle; `setup.moving = "prime"`; Stop
  cancels it. The operator runs the pump by hand. Never part of the job.
- **Start** of a building job requires the origin and a jog speed; the runner
  first sends `G54 G20 G90 G1 X0 Y0 F<jog speed in/min>` (`job.phase =
  "origin"`), then the lifts. Flat G-code: no origin needed, no return (M1).
- Allowed when connected, GRBL Idle, no motion / jog in progress and no job
  running or held (a job awaiting a frame reposition is fine).
- Origin INVALIDATED (prime kept, unusable until a new origin) on GRBL
  disconnect (incl. the Uno's reset on serial reopen) and on any ALARM
  (status `Alarm` or `ALARM:n`): machine position may be lost. An idle soft
  reset keeps it. Positioning speed = the validated jog speed (no separate
  setting; disabled while jogging is disabled).
- Why G10 L20 / G54 rather than G92 or G28/G30: standard, persists across
  idle resets, motion stays in G54; G28/G30 store MACHINE positions (prime
  must follow the origin) and move at the unvalidated rapid rate.

### Controller features pass (2026-10-14; manually reviewed, checkpoint 2026-10-15)
Behaviour modelled on Universal G-code Sender (checked in its source / GRBL
docs), not its code: UGS feed override = coarse ±10 / fine ±1 real-time
bytes + reset, value from `Ov:`; UGS Outline = convex hull of non-rapid
moves traced with `G90 G1` at the jog feed when Idle with a file loaded.
- **Progress / estimates** (`job.progress()`, `motion.py`): from the job's own
  G-code — per line XY length / modal F (G21 converted to inches), G0 at
  min($110,$111) if known else counted as unknown, G4 dwell, arcs as chords;
  100 % feed, no acceleration. Totals / remaining for job and EFFECTIVE lift
  from cumulative arrays at `current_line` (lines acknowledged — may lead
  real motion by up to GRBL's ~15-block planner buffer). Print distance uses
  the job's `travel_lines` (flat G-code: G0 = travel). Elapsed shown with
  frame-reposition WAITING separated (`begin_wait`/`end_wait`); waits never
  change the estimate. UI also shows remaining at the current override.
- **Feed override** (`feed_override {step}`): only −10/−1/+1/+10/"reset" →
  0x92/0x94/0x93/0x91/0x90 (GRBL 1.1; GRBL clamps 10–200 %). Value displayed
  = GRBL's reported `Ov:` (intermittent; None until reported, cleared on
  reset). Buttons, no draggable slider; the gauge is display-only.
- **2D view** (`GET /api/job/path`): points of every XY move from the G-code
  (decimated evenly above 120k, kind changes kept), canvas top view of the
  current effective lift (or all), printed / remaining / travel, nozzle =
  work position (needs the origin), fit / zoom / drag, redrawn ≤ 2.5 Hz.
- **Outline** (`outline`, UI hold 1 s): traces the job's `xy_bounds` (else the
  extent of its G-code moves) as a rectangle — `G54 G20 G90 G1` corners at
  the jog speed, then G4 P0; preview text in the job state before motion;
  same preconditions as Prime (origin set, Idle, no running job); never
  touches the job. Rectangle (not UGS's convex hull) — per the requested
  "XY bounds".
- **Diagnostics** (Advanced): connection / version / state, overrides, Pn
  pins, Bf, WCO, report units; alarm / error / reset / [MSG] history with
  GRBL 1.1 descriptions (error detail names the last command); read-only
  `read_grbl` = `$$` / `$G` / `$#` only (whitelist; Idle, no running job);
  settings table with names. Never writes settings; no macros.
- **Lift height / adaptive lifts** (`layout.py`): see Key decision below.
- Job loading (parse + motion analysis) runs in a thread executor so the
  event loop (status, jog dead-man) keeps running.

### Dashboard layout + print parameters (UI pass 2026-10-14; manually reviewed, checkpoint 2026-10-15)
- ONE responsive dashboard. ≥1100 px: LEFT Current Job → Print progress →
  Print parameters (lift + speed); MIDDLE Controls (Start, Hold / Resume /
  Stop, Set origin / Set prime / Prime / Outline + origin / prime / work
  status) → Manual Jog; RIGHT the toolpath top view (sticky, most width).
  760–1099 px: left | middle, view full width; <760 px (touch portrait):
  one column, Controls + Jog first. Current Job holds only the job (upload,
  identity, readiness, lifts); machine operations live in Controls.
  Diagnostics / console unchanged (collapsed, full width).
- PRINT PARAMETERS replace the old "end of lift" picker and the measured-
  height input: the operator edits physical variables with ± buttons —
  Target lift height (±0.5 in, ≤ Z travel; planned value shown) and Layer
  thickness (±0.1 in; Slicer nominal shown) — and sees "target ÷ thickness
  → N layers needed · Lift k: planned P → proposed M (ending after building
  layer L)" plus WHY when M ≠ N. Rule (server `propose_lift_end`, mirrored
  in app.js `proposeLiftEnd`, kept identical by a Node test): the clean-cut
  end with the most layers ≤ N, else the earliest end still available;
  reasons: interleaved islands (no cut at N), job ends, layers already under
  way, Z travel. Edits are local to the client until "Hold to apply"
  (1.5 s; `apply_lift_parameters`), which re-computes on the server, stores
  target + thickness on the job and moves the current lift's end; while
  awaiting a reposition it reads "Hold to continue lift k". Applies to the
  current lift only (later lifts keep their planned ends until applied
  there). "Restore planned lifts" also resets target and thickness.
- Speed lives in the same panel: planned print / travel feeds + the feed
  override (±1 / ±10 / 100 % buttons, read-back value, display-only gauge).

### Adaptive lift boundaries — operator-moved lift ends at clean cuts (decided 2026-10-14)
The job's lines are one ordered sequence; `LiftLayout.boundaries` (global
line indices) are where the runner pauses — initially the exported lifts.
The operator may move the END of the current effective lift (`adjust_lift_end
{lift, at}`, UI hold 1.5 s) — before/while printing it ("end early" /
"continue"), or while awaiting its reposition ("continue into more layers":
the pause point moves later and the runner resumes WITHOUT a reposition) —
but only to a CLEAN CUT: a layer mark with every earlier pass in a lower
layer than every later pass (global order), which with `reentry_marks` is a
validated re-entry segment. Lines are never added, removed, reordered or
altered; only pause points move. Rules: cut after the lines already sent;
early end → the remainder joins the next lift; extension may absorb whole
later lifts; a lift estimated (layers × observed thickness) taller than
`lift_plan.max_z_travel` is refused, above `target_lift_height` warned; a
warning when the NEXT lift becomes taller than the Z travel ("end it early
when you reach it"). Observed thickness: `set_observed_layer` or
`measure_lift_height` (height ÷ completed layers of the lift). `Restore
planned lifts` when not active. Not adjustable: jobs without re-entry marks
(older exports, hand-written, flat G-code) and inside interleaved islands —
that would need replanning upstream (not faked). Effective lifts are what
`total_lifts` / `current_lift` / `awaiting_lift` / `lifts` report (identical
to the export when unchanged).

### Live ok-wait instead of a fixed per-line timeout (decided 2026-10-14)
GRBL withholds `ok` while its 15-block planner buffer is full (until the
oldest block finishes — one long slow move can take minutes here) and during
a feed hold, so the M1 10 s per-line timeout would have failed healthy jobs.
Job lines, lift drains, the origin return and positioning moves use
`send_command(..., live=True)`: no time limit while status polls keep being
answered (they are real-time, answered even while GRBL blocks); fail after
`GRBL_UNRESPONSIVE_S` (5 s) with no status report. Disconnect / reset / alarm
still reject at once. Console / jog / setup keep the fixed timeout.

### Independent of Design + Toolpath — the `.mudjob` file is the only boundary (decided 2026-10-14)
The Pi interface never imports Design + Toolpath code, shares no runtime
modules with it, never calls its API and must run without it installed. It
parses and validates `.mudjob` files itself; a job exported elsewhere is
copied (USB / email / network) to an independent Pi. Interoperability is
tested with the contract fixture FILES (`contracts/mudjob/fixtures/`, incl.
the Designer's real export `designer-export-3-lifts.mudjob`), never by
cross-importing. (The tests read `../contracts`; on a Pi with only
`pi-interface/` deployed those tests need the contract folder copied too.)

### Machine profile + job preflight (2026-10-15; manually reviewed, checkpoint 2026-10-15)
Machine profiles are independent configuration on each side. `.mudjob` communicates job assumptions and requirements; it does not synchronize applications.

- THIS controller's profile of the physical printer: `app/machine_profile.py`,
  saved to `MACHINE_PROFILE_PATH` (default `machine_profile.json`,
  gitignored; atomic write; a damaged file → defaults + a reported problem).
  Defaults: name "Unconfigured printer", inches, X / Y / max building Z
  unset (not checked), Z travel per frame 18 in. Edited in the Machine /
  Printer settings section (opened from the status-bar printer chip, not in
  the operating columns) via WS `set_machine_profile`; refused while a job
  runs / holds / awaits a reposition.
- Preflight (`app/jobs/preflight.py`, per job, recomputed on change): XY
  from ALL G-code motion, building Z from lift / layer metadata, lift need
  from the effective lifts (layers × layer thickness); size > extent =
  fail, placement outside [0, X] × [0, Y] from the origin = warn, unset =
  unchecked. The job's `prepared_for` / `preflight` / `requirements` are
  parsed (must be objects) and only REPORTED: notes for a Designer "fits"
  that this check contradicts, prepared-for dimensions differing > 0.5 in,
  declared bounds differing from the G-code.
- Blocking: a native executable `.mudjob` with any failing configured check
  cannot Start (Start disabled with the reason; server refuses too) until
  the job or this profile changes. External G-code: informational only.
- Adaptive lift ends use this profile's Z travel per frame when set
  (`CurrentJob.max_z_travel()`), else the job's planned value.
- View: the envelope dashed (Fit frames it); a job reaching outside it gets
  a red dashed bounds box + "JOB OUTSIDE THE PRINTER ENVELOPE"; the path is
  never altered. Z stays XY-only / manual. Older `.mudjob` files load as
  before. Tests: `tests/test_machine.py` (contract fixture files only).

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

### Deliberate jog release: 0x85 BEFORE cancel, with a stopping flag (2026-10-25)
*(SUPERSEDED 2026-10-25 by the single-shot jog model + byte-level serial
write lock, below. The flag + the 0x85-before-cancel ordering stayed; the
underlying model changed.)* First-pass software fix for the operator-observed
"second burst": the jog cancel byte (0x85) was written from the jog task's
`finally` block, so a release could race one more `$J=` through the serial
write while the task was mid-`send_command`. The flag + ordering fix passed
automated tests on Mac but FAILED physical testing — the second burst was
still observed on hardware. Root cause was actually TWO issues (both now
addressed below): a byte-level serial write race, AND the planner-queue
depth from incremental streaming.

### Single-shot jog + serial write lock (2026-10-25, replaces the above)

Operator-observed on hardware: "the previous software fix FAILED physical
testing" — jog release still produced a short burst after the apparent stop.

Investigation of the full chain
`UI pointer → WS jog_stop → _stop_jog → send_realtime(0x85)`
and
`jog_loop → send_command($J=…)` revealed TWO co-contributing bugs:

**Bug 1 — Serial byte interleave race (`app/grbl/transport.py`).**
`SerialTransport.write_line` and `write_raw` both used
`asyncio.to_thread(self._serial.write, data)` which dispatches to the default
`ThreadPoolExecutor` (multiple workers). pyserial's `Serial.write()` is not
thread-safe. A concurrent `write_line("$J=G21 G91 X7.620 F3048")` and
`write_raw(b"\x85")` could land on different workers and interleave at the
kernel-serial-byte level. GRBL processes real-time bytes (`?`, `!`, `~`,
`0x85`, `0x18`) immediately as they are read, so an embedded `0x85` inside
the parsed `$J=` would cancel the current jog, and THEN the finished `$J=`
line would be parsed as a NEW jog — the "second burst" after release.

Fix: add a `threading.Lock` in `SerialTransport` (`_write_lock`) and route
both writes through `_locked_write`. Every serial write is now atomic on
the wire relative to every other write. Test:
`tests/test_communicator.py::TestSerialWriteOrdering::`
`test_concurrent_writes_never_interleave_bytes` (uses a `SlowSerial` fake
with a deliberate concurrency window).

**Bug 2 — Planner-queue buildup from incremental streaming
(`app/api/http.py`).**
The incremental jog loop sent small `$J=…` steps and awaited `ok`. GRBL
ok's a `$J=` as soon as it is parsed and added to the planner, NOT when the
motion completes. So the loop filled GRBL's 15-block planner with queued
jog steps; on release, 0x85 had to flush that whole queue AND then
decelerate from the current speed. Even with Bug 1 fixed, a release had
multiple queued jogs still in-flight AND a long controlled decel to go.

Fix: switch the jog model to SINGLE-SHOT. On `jog_start` the server sends
exactly ONE `$J=G21 G91 <axis>±<JOG_SINGLE_DISTANCE_IN × 25.4> F<speed>`;
GRBL executes that one move and stops naturally at its end. The task does
NOT write anything more during the hold — it only polls the heartbeat timer
and the stop flag. On release / heartbeat-timeout / cancel, the task's
`finally` writes 0x85, which decelerates the single in-flight jog at $120 /
$121. There is NEVER more than one jog in GRBL's planner.

Defaults (`.env.example`): `JOG_SINGLE_DISTANCE_IN=10.0` (10 inches). The
operator releases and re-presses to continue beyond that. Deliberately NOT
the machine envelope — soft/hard limits are off ($20/$21=0) so a single
jog line must not reach a mechanical end-stop.

Browser-disconnect safety is unchanged: the heartbeat loop sends 0x85 on
timeout (default 0.3 s) regardless of why the heartbeat stopped.

Tests (`tests/test_hardware.py::TestJogRelease`): exactly one `$J=` per
session, one `0x85` on release, heartbeat timeout cancels, rapid press/
release has no dangling task, repeat press writes a fresh jog line.

Note on the long-decel observation (separate from the second burst): with
`$120=$121=5 mm/s²` the controlled decel from 2.0 in/s = 50.8 mm/s takes
~10 s. That is pure GRBL physics, independent of this software fix. The
staged acceleration procedure below is the way to shorten it.

### Staged X/Y acceleration procedure (operator, not auto-applied)
`$120 / $121 = 5 mm/s²` is extremely conservative. Target decel at 2.0 in/s:

| $120 / $121 (mm/s²) | Decel from 2.0 in/s | When to use |
|---:|---:|---|
| 5 (current) | 10.2 s | status quo — too slow for jogging |
| 25 (5×)   | 2.0 s | first increment: confirm no chain slap / lost steps |
| 50 (10×)  | 1.0 s | after 25 passes a measured 10 mm commanded = 10 mm actual |
| 100 (20×) | 0.5 s | after 50 passes same check at 2.0 in/s |
| 170 (34×) | 0.3 s | user target; aggressive for this drivetrain |

Procedure for each step:
1. In the UI's Advanced → Diagnostics → Settings ($$), capture current values.
2. Advanced → Console: send `$120=<new>` and `$121=<new>`.
3. $$ to verify.
4. Jog X+ and X− at 2.0 in/s for ≥ 6 in; measure actual travel against a
   tape; confirm it matches commanded distance (no step loss).
5. Repeat at Y.
6. If any step shows chain slap, backlash growth, missed steps, or stall,
   STOP and roll back to the previous step with `$120=<prev>` and
   `$121=<prev>`.

The controller does NOT apply these automatically. The operator decides when
to progress.

### Stepper heating when stationary (observation 2026-10-25)
Reported hot stationary motors with `$1=25 ms` (step idle delay). GRBL $1
schedules the enable-pin to deactivate after 25 ms of no steps, but the
hardware doc (`CONTROL_BOX_HARDWARE.md`) explicitly records that "Driver
ENA terminals and limit-switch pins: not part of this record" — so whether
GRBL's D8 (stepper enable) is actually wired to the DM556T ENA pins is
UNKNOWN. If D8 is not wired, GRBL's enable has no effect and the drivers
stay energized continuously; a thermal increase while stationary is then a
driver-current issue, not a GRBL firmware issue.

Operator actions required (none of these are software changes):
1. Visually inspect: is Uno D8 wired to all three DM556T ENA− pins?
2. Read the DM556T label for SW1–SW4 current configuration and compare
   against the NEMA 23 motor nameplate current (motors should run below
   nameplate for sustained duty).
3. If D8 is wired: `$1=25` should already disable drivers 25 ms after the
   last step. If motors stay hot, verify `$4` (invert step enable mask)
   matches the DM556T ENA polarity.
4. If D8 is not wired: lower DIP-switch current, or wire D8 → ENA− on all
   three drivers.

The repository does NOT recommend disabling GRBL's enable mechanism
(`$1=255`) as a workaround — doing so sacrifices position retention
between jogs and compromises the holding torque the drivers currently
provide against external loads.

### Z activation (operator-controlled first motion, 2026-10-25)
`Z_PWM_DUTY` was 0 (disabled) until this pass. `.env.firstmotion` now sets
`Z_PWM_DUTY=0.5` — a moderate first-motion value for the 12 V linear
actuator. The duty is applied ONLY while a Z UP / Z DOWN button is held;
PWM returns to 0 on release, heartbeat timeout, STOP, REMOTE STOP, service
shutdown, and (via `config.txt gpio=18=op,dl`) Pi boot. The existing
Z controller behaviour is unchanged: dead-man heartbeat, UP + DOWN = STOP +
blocked, run-time limit, exception → STOP + fault, release blocks until
every input is released.

Still open (hardware verification before Z use): the MD13S PWM external
pull-down (proposed 10 kΩ) must be fitted and measured so PWM reads LOW
during Pi power-up / boot (`docs/deployment.md` §7). Software alone cannot
guarantee this. The deployment report flags this as an operator check item.

### Server-owned job runner
The job runner is an asyncio.Task on the server. Browser connect/disconnect has
no effect on it. This is a hard requirement from the user.

### Conservative streaming (line-at-a-time)
Send one line, wait for ok, send next. No character-counting buffer. Simpler,
safe, appropriate for this machine scale. Can revisit if throughput becomes a
bottleneck (unlikely for large-scale printing with slow moves). The wait for
each ok is LIVE (see "Live ok-wait"), not a fixed timeout.

### GRBL configuration must not be modified
The Pi interface communicates through the GRBL serial protocol only.
It never sends `$$=` or `$N=` commands. Existing machine settings are preserved.

### Z — the Pi is the sole Z command source (decided 2026-10-14; implemented 2026-10-20, not yet on hardware)
CURRENT: the Pi will command Z through GPIO PWM + DIR → Cytron MD13S → the
12 V actuator; manual Z comes from the UI and the remote's Z UP / Z DOWN as
inputs to Pi software. Requirements for the implementation are in
`../CONTROL_BOX_HARDWARE.md` → Z axis: open-loop (never assume a known Z
height), dead-man / time-bounded motion, guaranteed default STOP on boot /
shutdown / crash / loss of control (hardware pull-down + loss-of-control
mechanism still TBD), contradictory inputs → STOP. GPIO pins are now
WIRED (2026-10-20: PWM GPIO18 / physical 12, DIR GPIO23 / physical 16 —
authoritative table in `../CONTROL_BOX_HARDWARE.md`) and must live in one
hardware-mapping / configuration layer. (Previously, 2026-10-14: pins
intentionally unassigned pending the touchscreen.) The old SRDKG RF Z
controller is not part of the architecture.
What Z does within a print remains the undecided `z_within_lift`.
IMPLEMENTED 2026-10-20 (`app/hardware/z.py`, see "Hardware layer"): one
controller for UI + remote, fixed configurable duty (no operator speed
control; disabled at 0), open-loop (no position anywhere). Jobs stay XY-only.
Default STOP layers and the open pull-down blocker: `docs/deployment.md` §7.

HISTORY: in Milestone 1 Z was out of scope (manual actuator; no Z commands,
no Z UI, method TBD). M1 code still contains no Z — that is current fact,
not a design rule.

### Wireless remote = Pi GPIO inputs (decided 2026-10-14; wired 2026-10-20)
Operator-input device only: handheld → Kaleinavi 6-channel RF receiver → dry
contacts → Pi GPIO (internal pull-ups, active-low, ~1 kΩ series) → Pi
software; it never drives Z directly. Wired functions (no longer tentative):
CH1 REMOTE STOP, CH2 Z UP, CH3 Z DOWN, CH4 extrusion pause / resume, CH5 /
CH6 AUX 1 / AUX 2 (reserved) — pins in `../CONTROL_BOX_HARDWARE.md`; code
still reads them from the hardware-mapping layer. REMOTE STOP is an
operational software stop, NOT an E-stop; the UI must never present it as
one. Extrusion pause / resume has an input but no defined pump output.
IMPLEMENTED 2026-10-20 (`app/hardware/`): all six inputs read and shown
(Hardware / Commissioning); CH4 / CH5 / CH6 are logged only — no action.

### REMOTE STOP semantics (decided + implemented 2026-10-20)
Kaleinavi CH1 → `http.remote_stop()` — an OPERATIONAL stop, never an
E-stop (UI / logs say REMOTE STOP; the panel states it is not the E-stop).
On press: Z `stop_all` (PWM 0); GRBL real-time jog cancel `0x85` + feed hold
`!` (bypass the line stream, GRBL 1.1 semantics: decelerate, keep position
and planner); the jog loop cancelled; a running job → `held` exactly as HOLD
(runner sends nothing more; awaiting-reposition stays awaiting). Then
LATCHED (`AppState.remote_stop`): releasing the RF button does nothing.
While latched these are refused with a notice: jog, Z (UI + remote), job
start, RESUME, reposition confirm, prime, outline, console, lift-end
changes (they can release a paused job). Allowed: software STOP, hold,
upload, feed override, read-only queries. Recovery: "Hold to clear REMOTE
STOP" (2 s) on any screen, refused while CH1 reads pressed / unknown;
clearing moves NOTHING — a held job continues only with RESUME (same line,
nothing repeated / skipped; tested), or STOP ends it. CH1 already closed when
the inputs are first read latches too (the no-motion direction). `!` sent
while GRBL is Idle puts it in Hold:0 (GRBL 1.1): RESUME returns it to Idle
(empty buffer, no motion) — accepted for simplicity / no races.

### Deployment (2026-10-20, prepared; not yet performed on the Pi)
Raspberry Pi OS 64-bit desktop; venv `--system-site-packages`; `.env`
`HARDWARE_BACKEND=gpio`; systemd `deploy/mudprinter.service` (Type=notify,
WatchdogSec, pinctrl Z-LOW ExecStartPre / ExecStopPost, no network-online
dependency); config.txt `gpio=18=op,dl` / `gpio=23=op,dl`; udev symlink
`/dev/mudpr-grbl` from the Uno's discovered USB identity
(`deploy/99-mudpr-grbl.rules.example`); Chromium kiosk via
`~/.config/labwc/autostart` → `deploy/kiosk.sh`. Wi-Fi AP not done.

### Extrusion is out of scope
The external pumping system is independently controlled. Not addressed here.
(2026-10-14: electrically deferred; future pump control anticipated, so an
extrusion pause / resume concept is reserved — e.g. on the remote — with no
implementation, signal or wiring defined. `../CONTROL_BOX_HARDWARE.md`.)

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

### Jog speed presets: discrete absolute values (2026-10-25)
Product target: X/Y maximum selectable manual jog speed = 2.0 in/s
(= 120 in/min = 3048 mm/min). The UI exposes DISCRETE absolute-value preset
buttons (0.05 / 0.1 / 0.25 / 0.5 / 1.0 / 2.0 in/s) plus ±0.05 in/s nudge
buttons for fine positioning speeds — no slider. A preset above the
configured ceiling (`JOG_SPEED_MAX_MM_MIN`) is disabled in the UI; the
server clamps the WS `jog_set_speed` message defensively.
GRBL must independently permit the ceiling: `$110` / `$111` ≥ the ceiling
in mm/min or GRBL silently clamps the feed. The first-motion env template
(`.env.firstmotion`) documents the console commands and the required
pre-change capture. Do not change `$120` / `$121` (acceleration) solely to
raise the ceiling.

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

### One-click Mac → Pi deploy (2026-10-23)
Three-file workflow in `pi-interface/scripts/`:
- `Deploy to Pi.command` — double-clickable from Finder on the Mac.
- `deploy_pi.sh` — runs over SSH on the Pi.
- `preflight_state.py` — shared pass/block decision, imported by both
  `deploy_pi.sh` (over stdin) and `tests/test_preflight_state.py`.

Order of operations on the Pi: fetch `/api/state` → preflight (abort if
`Run`/`Jog`/`Hold*` or `job.state ∈ {running, held}` or state
unverifiable) → refuse pull if working tree is dirty / detached-HEAD →
`git pull --ff-only` → `pip install` only if `requirements.txt` sha256
changed since the last successful deploy (sentinel in `.venv/`) →
`sudo systemctl restart mudprinter` → wait up to 15 s for both
`is-active` AND `/api/state` to respond; abort with a specific exit code
and a journalctl command if either fails. `.env`, `jobs/`, and GRBL
settings are not touched.

Exit codes: 0 success, 2 refused (controller state), 3 refused (repo
state), 4 verification failed after restart, 255 SSH problem. The Mac
launcher maps each to a plain-English message in the Terminal window.

No automatic deploy on git push — the operator is physically beside a
moving machine; the deploy must be a deliberate, acknowledged action.

---

## Test Strategy

Tests use `MockTransport` — an in-memory transport that captures all writes
and lets the test inject GRBL responses via `inject()`.

Key invariant used in hold/resume tests: in `send_command()`, `_pending_future`
is created **before** `write_line()` appends to `sent_lines`. Therefore,
`wait_until_sent(transport, n)` guarantees the future exists before injecting ok.

346 tests total before the one-click deploy consolidation, all passing on
Python 3.9 (Mac, 2026-10-20; 291 before the hardware integration pass —
`test_hardware.py` uses MockBackend + a fake clock and gpiozero's
MockFactory for the real backend). Earlier: 262 tests (196 before the
controller features pass, 246 before the UI pass; `test_ui.py` checks the
dashboard structure from index.html and runs the UI's proposal rule under
Node against the server's): the 78 M1 tests unchanged + lift / building-job
/ contract / setup tests. `MockTransport` can answer `?` polls with a
settable `status_reply` (used to simulate Run→Idle around a lift drain,
positions, alarms) and print scripted `replies` before an ok (the `$$`
listing). API tests that need machine state set directly patch out
`main._connection_loop` (`offline_client` fixture).

Deploy preflight: `tests/test_preflight_state.py` (18 tests) covers the
pass/block decision used by the Mac → Pi one-click launcher
(`scripts/preflight_state.py`).

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

**Current-machine execution pass, 2026-10-14:** executable Designer exports
validated and run; origin / prime / Start-returns-to-origin; jog + re-zero +
prime during a frame reposition; live ok-waits; jog in G21; layer / Z cue for
manual Z. 196 tests pass. Verified end to end over HTTP / WebSocket only
against a throwaway fake GRBL (Designer :5051 export → Pi :8765: Start
refused without origin → Set Origin → jog → Set Prime → Prime → Start (origin
return) → lift 1 → jog + re-zero during the reposition → confirm → lift 2 →
completed). Not yet visually reviewed in a browser; not run on hardware.

**Controller features pass, 2026-10-14 (manually reviewed, checkpoint 2026-10-15):** progress / estimates, speeds + feed override, 2D view, Outline,
diagnostics, lift height + adaptive lift ends. 246 tests pass. Exercised end
to end against a throwaway fake GRBL (override 119 % read back, $$/$G/$#,
outline, lift end moved before start, continued while awaiting, completion
with waits separated). UI checked only headlessly (render code under Node).

**Hardware integration pass, 2026-10-20 (Mac only; NOT on the Pi, NOT
commissioned):** `app/hardware/` (pin map, none / mock / gpiozero backends,
debounced active-low remote, the one Z controller), REMOTE STOP latch +
recovery, Manual Z panel, REMOTE STOP panel, Hardware / Commissioning panel,
`$I` build info, GRBL link events, systemd notify / watchdog, read-only
capture tool, deploy/ files, rewritten deployment / capture / acceptance
docs. 346 tests pass. Exercised in-process with the mock backend (UI Z
dead-man stop over a real WebSocket); UI not yet visually reviewed.

**Not yet complete:**
- Physical commissioning (docs/acceptance-test.md steps 1–10) — nothing
  has run on the Pi or touched the hardware
- MD13S PWM pull-down: value not derivable from the spec — COMMISSIONING
  BLOCKER for Z (deployment.md §7)
- Jog speed validation on the physical machine
- Wi-Fi access point setup for offline field use

---

## Open Questions

- Envelope convention: [0, X] × [0, Y] from the print origin is provisional
  (where the operator sets the origin on the frame is undecided); should
  placement outside it block, or only size? (Currently: size blocks,
  placement warns.) Real printable X / Y and max building Z: unmeasured.

- What is the correct JOG_SPEED_MM_MIN for this machine's scale?
  (Must be established by physical testing before jogging is enabled)
- What USB identity does the Uno have (genuine 2341:xxxx with serial, or a
  CH340 clone)? Decides udev variant A / B for `/dev/mudpr-grbl` (decided
  2026-10-20: use a symlink; rule template in deploy/, values discovered on
  the Pi).
- MD13S PWM pull-down value (proposed 10 kΩ, measure — blocker for Z);
  the fixed manual Z duty; `Z_MAX_RUN_S` (30 s provisional); whether
  `Z_DIR_INVERT` is needed; Kaleinavi momentary mode + RF range / dropouts.
- Does the Raspberry Pi kernel keep released outputs
  (`persist_gpio_outputs`)? Irrelevant to safety (ExecStopPost drives LOW)
  but worth recording.
- Does the Pi need to act as a Wi-Fi access point for field use?
  If so, hostapd + dnsmasq setup is needed (not yet done).
- Will the current GRBL firmware support all required jog and job operations?
  (Expected yes, but unverified on hardware)
- After a power failure mid-print, should the interface offer job recovery?
  (Not implemented in Milestone 1 — open question for future milestones.
  Lift boundaries are natural safe restart points: persist progress there
  and offer "start at lift N"?)

Lift-job questions — RESOLVED 2026-10-14 (current-machine decisions): Z
manual (XY-only jobs); travel = plain moves regardless of the pump; XY origin
set by the operator on the controller, re-zero allowed between lifts; G20
inches. Still open:
- Confirmation strength: single hold-to-confirm (implemented, provisional)
  or a checklist?
- Is `G4 P0` accepted and does it synchronise as expected on the installed
  GRBL? Does `G10 L20 P1` behave as expected? (GRBL 1.1; verify on hardware.)
- Origin / prime are in memory only: lost on a Pi restart (and the E-stop is
  believed to power the Pi down — `../CONTROL_BOX_HARDWARE.md`).
- GRBL ALARM during a frame reposition: no Unlock (`$X`) control while a job
  is active (console blocked) — the operator must Stop the job.

KNOWN ISSUE — corner-cutting in path views (diagnosed 2026-10-15; FIXED the same day, see below).
Symptom: corners / openings / junctions look rough or diagonally cut in the
Pi top view, and in the Designer's Print Simulation only while dragging /
scrubbing. Traced on the operator's `test 4.mudjob` (project "test 3", 73
layers, 7 lifts, 374 749 moves; exported from an UNSAVED Designer state, so
the PrintPlan was not rebuilt for that exact geometry — the saved
`test 3.muddesign` is older). Upper-right corner of the rectangular island,
layer 1, G-code lines 28926 / 29068 / 30298 / 30437: clean right-angle bead
corners in the G-code, e.g. (363.6334, 315.5516), (368.2064, 320.1246).
- PrintPlan → .mudjob: faithful. The job's G-code print length = the
  PrintPlan run lengths it records, every lift (≤ 0.003 in over ~65 000 in);
  the saved project's real-scale plan re-exported: 17 028 vertices, max
  |PrintPlan − G-code| 0.00005 in (rounding only).
- The machine follows the CLEAN path: the runner sends every job line; the
  view payload is never used for motion.
- FIRST DIVERGENCE = the renderers' vertex decimation by INDEX STRIDE:
  * Pi: `jobs/motion.path_payload` keeps every k-th point above 120 000
    (k = 4 here, computed over the WHOLE job, so even the current-lift view
    is thinned, also when stationary). 3 of the 4 corner vertices dropped;
    the drawn chords miss them by 3.84 / 7.02 / 5.13 in. Job-wide: 5 943
    dropped vertices > 1 in off (max 9.2 in), 68 % at turns > 45°.
  * Designer Print Simulation: `SIM_LOD_VERTICES = 120000`; while
    `sim.drag || sim.scrubbing` `_simDrawItems` draws every
    ceil(V/120000)-th vertex inside each item (+ first / last) → the same
    corner cuts (emulated; exact item splits are per strand). The stationary
    Simulation draws every vertex and is faithful (stream coordinates
    rounded to 0.001 in).
- Planned fix (not implemented): error-bounded simplification instead of
  stride — Ramer–Douglas–Peucker per same-kind run, keeping kind changes
  (Pi ε ≈ 0.01 in; Simulation keep-mask computed once at ingest in world XY,
  ε ≈ 0.05 in, sharp turns always kept). Never smooth geometry.
- Tests to add: realistic PrintPlan → .mudjob vertex-for-vertex fidelity
  (wave Wall System + corners + opening); Pi path payload: every original
  vertex within ε of the served path, corners present, > 120 000 points;
  Simulation LOD (node): corner kept, every skipped vertex within ε.

FIX IMPLEMENTED 2026-10-15 (manually reviewed in the Pi top view and the
Designer Print Simulation, checkpoint 2026-10-15): stride
decimation replaced by ERROR-BOUNDED simplification (Ramer–Douglas–Peucker);
geometry, routing, PrintPlan and `.mudjob` untouched.
- Pi (`jobs/motion.path_payload`): per run, split at print/travel changes and
  at every pass / lift start (`breaks`); tolerance 0.10 in (sub-pixel at the
  fitted view), relaxed ×1.6 only above a 150 000-point budget, never past
  0.25 in; kept points are exact G-code coordinates (4 decimals); payload
  reports `tolerance_in`; computed once per job in an executor and cached.
  test 4: 137 199 of 374 749 points, 0.10 in, 1.1 s on the Mac, 4.1 MB JSON
  (stride version: ~94 k points, corners cut by up to 9 in).
- Designer Simulation (`simulation.js`): `_simLodMask` once per plan (first
  drag / scrub) — per item, world XY, tolerance 0.05 in, doubled while above
  SIM_LOD_VERTICES (120 000), never past 0.5 in; `_simDrawItems` draws the
  masked vertices while dragging / scrubbing; small plans (V ≤ budget) and
  the stationary view always draw every vertex. test 4-scale stream (375 k
  vertices): 33 ms, 0.2 in, 106 k vertices.
- Tests: layer_assembly `test_view_fidelity.py` (realistic fixture
  `fixtures/realistic_stack_request.json` = the "test 3" plan request: every
  PrintPlan vertex in the G-code in order ≤ 0.00005 in, print length per
  lift; stationary Simulation stream = plan ≤ 0.0005 in; drag LOD under node
  (`tests/js/sim_lod_check.js`) error-bounded with every sharp turn drawn,
  real stream + a > 120 k synthetic stream with corners at non-stride
  indices); pi-interface `TestViewAccuracy` (every original vertex within the
  reported tolerance, exact corners, travel + pass-start points kept, the
  old stride shortcut case, and the operator's test 4 job when present).

EXACT-PROJECT VERIFICATION (2026-10-15): the operator saved the exported
state as "test 3 (1).muddesign" (plan request now in layer_assembly
tests/fixtures/test4_stack_request.json). Rebuilt with the page's own
request logic: 73 layers, 286 passes, 7 lifts; re-export == test 4.mudjob
(identical apart from generated_at). The traced corner in the PrintPlan,
strand p2~ws1.0: (363.633429, 315.551588), (368.206400, 320.124558),
(365.527616, 317.445774), (370.100586, 322.018745) — clean; stationary
Simulation stream = those to 0.001 in; the OLD drag stride (4, V = 376 617)
skipped vertices 649 / 791 / 2021 of that 2 819-vertex item (confirmed, not
emulated); the NEW drag LOD (0.20 in, 107 557 vertices, 27 ms) draws all 4;
the Pi view (0.10 in) draws all 4 exactly. Regression:
test_view_fidelity.py::TestOperatorProject (~50 s planning).

~~Risk found 2026-10-14: fixed 10 s per-line ok timeout~~ — RESOLVED the same
day by the live ok-wait (see Key Decisions).

---

## Next Steps

1. Deploy on the Pi (docs/deployment.md) and run commissioning steps 1–4
   (boot, service, read-only GRBL capture — before any motion — RF inputs
   with no motion).
2. Fit + measure the MD13S PWM pull-down (step 5); only then set
   Z_PWM_DUTY (step 6).
3. Continue the commissioning / acceptance test (steps 7–10).
4. Validate and set JOG_SPEED_MM_MIN in .env.
5. If field offline use is needed, configure Wi-Fi access point.
6. After Milestone 1 is verified on hardware, define Milestone 2.

Lift-aware job work:
- ~~P1~~ / ~~P2~~ done 2026-10-14 (see Current State).
- Manual visual review of the lift UI in a browser (laptop) and later on the
  Pi touchscreen kiosk; adjust layout if needed.
- ~~Decide the per-line timeout risk~~ (live ok-wait, 2026-10-14).
- ~~P3~~ done 2026-10-14: `.mudjob` interchange; ~~executable export~~ done
  (current-machine pass, XY-only).
- Manual browser review of the origin / prime / reposition workflow.
- Hardware: validate jog speed (also the origin-return / prime / outline
  speed), check G4 P0, G10 L20, `$13` units, feed-override bytes + `Ov:`
  reports (override support compiled in), `Pn:` limit pins and alarm codes
  on the real GRBL, then a first air-print (no material) of an exported job.
- Manual browser review of the controller features + UI passes, then commit.
- Estimates vs reality: compare the G-code estimate with real lift times;
  consider using `Bf:` ($10 buffer reporting) to correct progress for the
  planner lead.
- Third-party G-code compatibility / preflight (later, not started).
- Enter the real machine dimensions in Machine / Printer settings once
  measured (max building Z unknown); machine-profile pass reviewed 2026-10-15.

Hardware-facing work: software DONE 2026-10-20 (Z via MD13S, RF remote,
REMOTE STOP). Next = the physical commissioning sequence in
`docs/acceptance-test.md` (Pi boot → service → read-only GRBL capture →
six RF inputs with no motion → MD13S default STOP with the actuator
disconnected (fit + measure the pull-down) → actuator → slow XY jog →
limits → air print → material), plus a manual browser / touchscreen review
of the new panels.
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
- 2026-10-14: current-machine execution (executable XY-only jobs, origin /
  prime, Start returns to origin, reposition re-zero, live ok-wait);
  196 tests passing
- 2026-10-20: hardware integration (GPIO layer, RF remote, manual Z,
  REMOTE STOP, diagnostics, deployment files, capture tool, commissioning
  procedure); 346 tests passing on Mac — not yet run on the Pi
- Deployment documentation written
- GRBL capture checklist written
- Acceptance test procedure written

---

## Last Updated

2026-10-25 (eighth pass) — Held-input release handling hardened.

**Why:** Operator reported hitting `Z_MAX_RUN_S=30` (server-side ceiling)
without recalling having held the touchscreen button for 30 seconds.
Audit of the Z button code found gaps where a released finger / cursor
could leave the client's heartbeat `setInterval` running indefinitely:
no `visibilitychange` / `blur` / `pagehide` handlers, no Pointer Events
with `setPointerCapture`, no client-side max-hold timer, no handling
for `lostpointercapture`. The journal cannot distinguish "continuous
30 s hold" from "missed release + heartbeat kept flowing" after the
fact — heartbeats aren't logged and the ZController only records
`Z UP start`, `Z STOP (...)`, and the ceiling warning. See the report
on this task for the inconclusive log evidence.

**Fix (`app/static/app.js`):**

- `startZ` / `startJog` now set a client-side `INPUT_MAX_HOLD_MS = 30 s`
  timer (matching the server's `Z_MAX_RUN_S`); if it fires it calls the
  corresponding `stopZ` / `stopJog` as a backstop. The server-side
  dead-man (`Z_TIMEOUT_S=0.3`) and run ceiling remain unchanged — they
  are now the LAST line of defence, not the only one.
- New global `attachGlobalReleaseHandlers()` wires
  `visibilitychange`, `window.blur`, `window.pagehide`, and
  `window.beforeunload` to `stopAllHeldInputs()`. Any OS-level focus
  loss, tab background, screen lock, or navigation now actively
  releases every held input from the client side — the UI no longer
  waits for the server to notice.
- New shared `attachHeldButton(btn, onStart, onStop)` replaces the
  per-handler touch + mouse wiring on both Z and jog buttons. It uses
  Pointer Events exclusively (`pointerdown`, `pointerup`,
  `pointercancel`, `lostpointercapture`, `pointerleave`) and calls
  `setPointerCapture` on `pointerdown`, so the ORIGINAL button
  receives the release event even when the finger / cursor is dragged
  off the button, over another element, or past the window edge.
- `attachHeldButton` also installs a `MutationObserver` on each
  button's `disabled` attribute; if the server disables the button
  mid-press (e.g. REMOTE STOP latches, Z fault, GRBL alarm), the UI
  calls `stopZ` / `stopJog` immediately rather than waiting for an
  event that may never arrive.

**Server-side invariants unchanged** (verified by regression tests):
- `Z_TIMEOUT_S = 0.3` dead-man heartbeat (test_ui_z_dead_man).
- `Z_MAX_RUN_S = 30` continuous-run ceiling (new
  test_z_max_run_ceiling_still_fires).
- `z_stop` clears the per-WS source request (new
  test_z_stop_message_releases_the_source).
- WS endpoint `finally` releases every held Z source for a dropped
  socket (new test_ws_cleanup_releases_held_z).

**UI structure test:**
`tests/test_ui.py::TestHardwareUI::test_held_input_release_safety_
handlers_present` pins the presence of every element of the hardened
release chain. If any disappears the test fails.

Tests: 392 passed, 1 skipped on Mac (+4 new).

2026-10-25 (seventh pass) — Z button double-wiring bug fixed.

**Symptom:** Operator reported that pressing Z UP / Z DOWN sometimes
produced actual actuator motion AND sometimes a UI notice "Internal
error handling 'jog_start' — see Pi logs." After a few presses the
actuator stopped responding entirely.

**Root cause** (confirmed from the Pi journal, verbatim):
```
TypeError: int() argument must be a string, a bytes-like object or
            a real number, not 'NoneType'
  File ".../api/http.py", line 849, in _handle_message
    direction = int(data.get("direction", 1))
```
The Z buttons in `index.html` carried `class="jog-btn z-btn"`. The JS
wires `.jog-btn` → `attachJogButton` (reads `dataset.axis` /
`dataset.dir`, both undefined for Z buttons; `parseInt(undefined, 10)`
is `NaN`; `JSON.stringify({direction: NaN})` becomes
`{"direction":null}`) AND `.z-btn` → `attachZButton` (correctly sends
`z_start`). Every Z press therefore fired BOTH a bogus
`jog_start { axis: null, direction: null }` and a valid
`z_start { direction: "up" | "down" }`.

Server-side `_handle_message`'s jog_start path then did `int(None)`
and threw TypeError. The sixth-pass defensive handler caught it, logged
the full traceback, and sent the client a visible notice — the exact
notice the operator saw. The actuator genuinely moved on every press
(z_start processed cleanly in parallel), confirmed by the interleaved
`Z UP start (ui:…, duty 0.50)` lines in the same journal.

The "actuator stopped responding" phase was a different and correct
behaviour: the Z controller hit its documented 30-second run limit
(`Z_MAX_RUN_S=30`) and blocked further motion until all Z inputs were
released. The journal records:
```
WARNING app.hardware.z — Z stopped: maximum continuous run 30 s reached.
```
That is not a fault — it is the dead-man ceiling behaving as designed.

**Fix:**
- `app/static/index.html`: removed `jog-btn` class from the Z buttons;
  they now carry `class="z-btn"` only. Shared visual styling moved
  to `.jog-btn, .z-btn { … }` in `app/static/style.css` (including
  the responsive `@media` block).
- `app/static/app.js` `attachJogButton`: belt-and-braces early return
  if `axis` or `dir` is not valid X/Y jog data, so any future
  unlabelled `.jog-btn` element is ignored rather than firing a
  malformed `jog_start`.
- `app/api/http.py` `_handle_message` jog_start path: defensive
  payload validation before `int()`. Malformed payloads are silently
  dropped (same way an unknown `type` is).

**Regression test:**
`tests/test_api.py::TestWebSocket::test_jog_start_malformed_payload_
does_not_error` covers 8 malformed shapes including the exact
`{axis: null, direction: null}` the operator was hitting. 388 passed.

**End-to-end verification on live Pi (post-deploy):**
A WS probe that fires the OLD payload shape (`jog_start { axis: null,
direction: null }` immediately followed by `z_start up`) produces no
notice and no close; the Z controller shows `cmd=UP duty=0.5
dir=HIGH` for the whole hold and `Z STOP` on release. Journal for the
new PID has `Z UP start` + `Z STOP` with no TypeError.

**Hardware status on the actuator itself:**
It works. During the operator's session the Pi drove GPIO18 at PWM
50 % + GPIO23 HIGH as commanded, and the actuator produced visible
downward motion. The intermittent initial no-move was almost
certainly a reseated terminal contact that improved during multimeter
probing. The 30 s run limit is a safety ceiling, not a fault — press
Z again after release and motion resumes.

2026-10-25 (sixth pass) — WebSocket handler exceptions no longer close
the WebSocket.

**Observed symptom (operator):** pressing Z UP / Z DOWN on the touchscreen
immediately turned the client's "browser connected" indicator red and
briefly showed "Reconnecting," then recovered after ~1 s. X/Y jog did
not produce this. The service journal showed NO `z_start` log line
from the browser presses — the handler never got a chance to log
"Jog start"-style breadcrumbs before the socket died.

**Root cause (code inspection):** `websocket_endpoint` wrapped the whole
receive loop in `try: ... except Exception as exc: log.debug(...)` and
then `finally: _ws_manager.disconnect(ws)`. Any exception from
`_handle_message` (serialization slip, GPIO read error inside the
`broadcast_state` that fires after a `z_start`, or anywhere else in
the chain) was caught at DEBUG level — invisible by default — and the
`finally` closed the socket. The browser's `onclose` fired, status
flipped to "Reconnecting", 1500 ms later `connectWS` reopened the
socket and status went green. No visible trace. The fact that
automated probes couldn't reproduce it (they sampled state updates
but never actually caused handler exceptions) left the fault in place.

**Fix (`app/api/http.py`):**
- `websocket_endpoint` now catches handler exceptions PER MESSAGE,
  logs them at WARNING with `log.exception` (full traceback), sends
  the client a `notice`, and keeps the socket open. Only an actual
  WebSocket framing / transport error now closes the connection.
- `broadcast_state` wraps its `self._state.to_dict()` + broadcast in
  a `try: ... except: log.exception` so a bad broadcast cannot
  propagate out through the handler.
- The initial-state send on connect is also protected.

**Regression test:**
`tests/test_api.py::TestWebSocket::test_handler_exception_does_not_
close_ws` monkey-patches `_handle_message` to raise, then verifies
the server emits a `notice` to the client AND accepts a follow-up
message on the same socket. 387 passed on Mac.

**Automated verification of the Z path (post-fix):** A WS probe from
the Mac to the Pi over the LAN runs `z_start up` + 100 ms heartbeats
for 3.7 s, observes `cmd=UP duty=0.5 dir=HIGH` the entire time, no
close, no notice. The server-side GPIO backend IS producing real
PWM on GPIO18 during this probe — i.e., any EMI / power-coupling
root cause would reproduce here too. It does not.

**Remaining physical check** (if the operator's browser STILL shows
the red-flash after this deploy): press Z UP once from the browser,
then `ssh dirtworks@192.168.1.144 'journalctl -u mudprinter -n 30
--no-pager | grep -A20 "WebSocket handler error"'` — the fix now
logs the exception type, the message type (`'z_start'`), and the
full Python traceback.

**Still open on the Z actuator itself** (unchanged from the fifth
pass): the actuator does not move despite correct GPIO18 PWM 50 %
and GPIO23 DIR HIGH signals; measured output across the actuator
is reported as ~1.5 V. The next physical check sequence is in the
report, not here, since it is not durable project knowledge.

2026-10-25 (fifth pass) — Speed sliders, touchscreen scrollbar, Z software
readiness confirmed.

**Print speed control (replaces the percentage-dominated panel):**
- `app/static/index.html`: the Speed sub-panel now shows a large in/s
  number as the PRIMARY display, a horizontal `<input type="range"
  min="10" max="200" value="100">` as the override control, ±1 % step
  buttons flanking the slider, and the ±10 % / 100 % shortcut row below.
- `app/static/app.js` `renderSpeed()`: computes the planned print speed
  from `s.job.machine.feeds_in_min.print / 60`, applies the current
  feed override from GRBL's `Ov:` report, shows the effective in/s;
  when the user is dragging the slider, uses the slider position as the
  override preview; the secondary line shows planned-at-100 % and the
  override percentage. Jobs with a different travel feed get a hint so
  the single big number is clearly the print feed, not every move.
- `attachPrintSpeedSlider()`: on slider release, sends the minimum GRBL
  real-time byte sequence (±10 first, then ±1) to move the override to
  the chosen value. ± step buttons each send one 0x93 / 0x94 (1 %).
- No job loaded: the big display reads `—`, the slider / step buttons
  disable, the secondary line reads "Load a job for speed control."

**Jog speed control (replaces the 6 preset + 2 nudge buttons):**
- `app/static/index.html`: large in/s number + one horizontal slider +
  ± 0.05 in/s step buttons. The slider is percentage-of-ceiling (0 –
  100 %); the operator only ever sees in/s.
- `app/static/app.js` `renderJogSpeedControl()`: broadcast updates do
  not overwrite the slider while the user is dragging
  (`jogSliderDirty`). On release, `jog_set_speed` with the computed
  `speed_in_s`.
- Max jog ceiling (`JOG_SPEED_MAX_MM_MIN`) preserved; the slider ends
  at the configured maximum.
- Test coverage: `tests/test_ui.py::TestLayout::test_speed_sliders_
  are_present` and `test_low_level_controls_removed` pin that both
  sliders exist and that the old preset / nudge button classes are
  gone.

**Touchscreen scrollbar:**
- `app/static/style.css` has a `@media (pointer: coarse)` block that
  widens the webkit scrollbar to 22 px with a 48 px minimum thumb
  height and 11 px radius (comfortable for a 7" touchscreen finger).
  Firefox uses `scrollbar-width: thick`. Desktop browsers (`pointer:
  fine`) keep the default narrow bar.

**Z-axis control — software path confirmed healthy:**
- A WS probe (sending `z_start` → heartbeats → `z_stop`) on the
  deployed service showed the backend driving `pwm_duty 0 → 0.5` and
  `dir_level LOW → HIGH` within one state broadcast, and back to
  `pwm_duty 0` on release. The GPIO backend (`GpioZeroBackend`), the
  `ZController` resolver, the WS handler, the UI touch events, and the
  dead-man loop are all wired and functional.
- "Z UP / Z DOWN do not appear to work" is NOT a software bug. It is a
  physical / wiring symptom. The operator must physically verify:
    1. GPIO18 (pin 12, PWM) is wired to the MD13S PWM input and the
       external pull-down resistor is fitted (see `docs/deployment.md`
       §7 — proposed 10 kΩ, measured on the installed MD13S).
    2. GPIO23 (pin 16, DIR) is wired to the MD13S DIR input.
    3. 12 V actuator supply is live.
    4. If the actuator moves in the WRONG direction on Z UP, flip
       `Z_DIR_INVERT=true` in the Pi `.env` and redeploy; do NOT change
       `pins.Z_DIR_LEVEL_UP` in code.
- No software change was needed for Task 4.

Tests: 385 passed + 1 skipped on Mac (unchanged, +1 new UI test to
verify both sliders exist, -1 removed assertion that the old percent
display dominates the Speed panel).

2026-10-25 (fourth pass) — Pi desktop launcher + autonomous deploy workflow.

**Desktop integration (new):**
- `pi-interface/deploy/launch-kiosk.sh` — Pi-side script invoked by the
  `~/Desktop/Mud Printer Controller.desktop` icon. Starts
  `mudprinter.service` if inactive (via narrow passwordless sudo), waits
  for `/api/state`, launches Chromium `--kiosk`, writes Chromium's PID
  to `/tmp/mudprinter-kiosk.pid` so the UI's new "Exit" button can send
  SIGTERM. If a kiosk is already running, raises it instead of starting
  a second one.
- `pi-interface/deploy/Mud Printer Controller.desktop` — the double-tap
  launcher entry, installed at `~/Desktop/` by
  `pi-interface/deploy/setup-controller.sh`.
- `/api/exit_kiosk` POST endpoint — reads the kiosk PID file, sends
  SIGTERM, returns `{ok, signaled_pid}`. The service is untouched.
- Status-bar "Exit" button (hold 1.5 s) calls `/api/exit_kiosk`. The
  existing boot-time `~/.config/labwc/autostart` → `kiosk.sh` path is
  unchanged — automatic startup on power-on still works. Reopening the
  controller after exit uses the desktop icon.

**Autonomous deployment (new):**
- `pi-interface/deploy/mudprinter-controller.sudoers` — narrow NOPASSWD
  rule for `/bin/systemctl {restart,start,stop,status,is-active,show}
  mudprinter` and the one-shot `/usr/bin/install` used by setup. Install
  via `sudo install -m 0440 ... /etc/sudoers.d/mudprinter-controller`.
- `pi-interface/deploy/setup-controller.sh` — idempotent one-time Pi
  install that validates and installs the sudoers rule, drops the
  `.desktop` icon, and verifies passwordless sudo works. Runs via
  `ssh -t ... sudo bash ...`.
- `.claude/settings.json` (project-level, TRACKED) — narrow allow /
  deny lists so Claude Code can push to `consolidate/trunk`, SSH to
  the Pi, run the deploy launcher, and run pytest without approval
  prompts. Force-pushes, pushes to main / master, and shell-command
  SSH payloads for destructive ops stay denied.
- `.gitignore` adjusted: `.claude/*` ignored EXCEPT
  `.claude/settings.json`.

**File relocations:**
- `Deploy to Pi.command` and `deploy.config.sample` moved to repo root
  (where the operator put the launcher by hand). `deploy.config` moved
  along with them (gitignored). The launcher's `SCRIPT_DIR` resolves
  the config next to it; `tests/test_launcher.py` updated to the new
  path. One authoritative deployment underneath: `pi-interface/scripts/
  deploy_pi.sh` on the Pi; the Finder launcher and Claude Code both
  drive it over SSH.

Tests: 385 passed + 1 skipped (3 new `TestExitKiosk` cases; 8 launcher
tests updated to the new path).

2026-10-25 (third pass) — hardware trace confirms single-shot jog + serial
write lock work correctly on real GRBL; "no motion" report traced to speed
+ acceleration physics, not code.

The operator reported after the second-pass deploy that X/Y jog produced
no visible physical motion. A diagnostic logging commit (`1fc0f4c`, now
reverted) elevated every exit path in `_jog_loop`, `send_command` and
`_dispatch` to INFO/WARNING. One operator-triggered jog captured the full
chain:
```
17:19:28.083  Jog start: X 254.0 mm @ 762.0 mm/min (single-shot)
17:19:28.083  TX: $J=G21 G91 X254.000 F762
17:19:28.094  RX ok (11 ms round trip)
17:19:28.095  entering heartbeat loop
17:19:29.580  heartbeat sleep cancelled  (operator released at t=1.5 s)
17:19:29.580  sending 0x85
```
The $J= line DID reach GRBL and was executed. The perceived "no motion"
was caused by:
- UI's startup default `JOG_SPEED_MM_MIN=46` (= 0.030 in/s): at
  `$120=5 mm/s²` and 1-s hold the total travel is ~0.77 mm = 0.030 in,
  which looks like "no motion" on a 10×10 ft machine.
- Even at the operator's chosen 762 mm/min = 0.5 in/s, `$120=5` caps the
  velocity reached in 1 s to 5 mm/s (0.2 in/s) and total travel to
  ~5 mm = 0.2 in. Also easily missed visually.
The staged X/Y acceleration procedure in this file (Key Decisions →
"Staged X/Y acceleration procedure") is the next concrete step: raise
`$120 / $121` from 5 → 25 → 50 → 100 → 170 mm/s² with physical checks at
each stage. No further code change is needed for X/Y jogging.

Diagnostic logging reverted in the follow-on commit; the single-shot jog
+ serial write lock + Z duty 0.5 remain as deployed.

2026-10-25 (second pass) — single-shot jog + serial write lock + Z duty.
Replaces the first-pass "0x85 before cancel" fix which passed automated
tests but FAILED physical testing. Changes:
- `app/grbl/transport.py`: threading.Lock around pyserial.Serial.write().
  Serializes bytes on the wire; eliminates the $J=/0x85 byte-interleave
  race that was producing the second burst.
- `app/api/http.py`: `_jog_loop` rewritten SINGLE-SHOT — ONE $J= at start,
  heartbeat-only monitoring during hold, ONE 0x85 at release. No
  incremental streaming; GRBL planner never holds > 1 jog.
- `app/config.py`: `JOG_SINGLE_DISTANCE_IN=10.0` added; `JOG_SEND_INTERVAL_S`
  is now the heartbeat-poll interval, default lowered to 0.05 s.
- `.env.firstmotion`: `Z_PWM_DUTY=0.5` (was 0); added
  `JOG_SINGLE_DISTANCE_IN=10.0`.
- `.env.example`: updated commentary to describe single-shot model + Z duty.
- Tests: 4 new (3 jog-release scenarios + 1 byte-ordering race). 382 passed
  + 1 skipped.
- See the two new "Key Decisions" entries: "Single-shot jog + serial write
  lock" and "Staged X/Y acceleration procedure".

**Not yet exercised on hardware.** Previous first-pass fix stopped physical
testing after a brief trial — the second burst persisted. The staged
acceleration procedure is operator-controlled.

2026-10-25 — jog UI reworked for 2.0 in/s + post-release burst fixed.
Changes in `app/api/http.py`, `app/static/index.html`, `app/static/app.js`,
`app/static/style.css`, `.env.firstmotion`, `.env.example`:
- Jog preset buttons rebuilt as DISCRETE absolute in/s targets (0.05, 0.1,
  0.25, 0.5, 1.0, 2.0 in/s) plus ±0.05 nudge buttons.
- `.env.firstmotion` sets `JOG_SPEED_MM_MIN=76` (0.05 in/s startup) and
  `JOG_SPEED_MAX_MM_MIN=3048` (2.0 in/s ceiling). Operator must also raise
  GRBL `$110` / `$111` to 3048 mm/min on the Arduino console (`$110=3048`,
  `$111=3048`) BEFORE the UI's 2.0 in/s preset will actually deliver 2.0 in/s
  — GRBL silently clamps feeds to its own axis-max rate.
- Post-release burst fix (see "Deliberate jog release: 0x85 BEFORE cancel"):
  `_stop_jog` now writes GRBL's `0x85` jog-cancel real-time byte BEFORE
  cancelling the loop task; a module-level `_jog_stopping` flag short-
  circuits the loop so no further `$J=` step is written after a release.
  Verified by `tests/test_hardware.py::TestJogRelease` (2 new tests).
- Not yet exercised on hardware.

Test suite: 378 passed + 1 skipped (the 2 new `TestJogRelease` tests are
included; the skip is the view-fidelity test gated on a user-local fixture).

Nothing committed — awaiting operator physical verification.

2026-10-23 — first-motion software configuration applied on the Pi
(no physical motion yet).

Current controller state at session end:

- Consolidated branch `consolidate/trunk` running from
  `/home/dirtworks/mudprinter-controller/pi-interface`. GPIO fix applied
  (`pyvenv.cfg` → `include-system-site-packages=true`), `hardware.backend='gpio'`,
  gpiozero → LGPIOFactory, all 6 remote channels armed except CH3 Z DOWN
  (reports pressed at startup — physical button may be stuck, Z stays off).
- `.env` edited for first-motion jog test, picked up by service restart via
  the one-click launcher (`Deploy to Pi.command`):
  `JOG_SPEED_MM_MIN=46` (≈ 0.030 in/s), `JOG_SPEED_MAX_MM_MIN=381`
  (matches GRBL `$110`), `Z_PWM_DUTY=0` (Z disabled),
  `HARDWARE_BACKEND=gpio`, `SERIAL_PORT=/dev/ttyACM0`.
  Backup: `pi-interface/.env.pre-first-motion-2026-10-23_235618`.
- Machine profile set via WS to `"Mud Printer (nominal, unverified)"`,
  120 × 120 in envelope. Persisted to `machine_profile.json`. UI shows the
  "nominal, unverified" wording so the operator knows this is not an
  enforced travel limit.
- GRBL `$$` captured pre-change, committed at
  `arduino-grbl/captures/grbl-pre-first-motion-2026-10-23_235310.txt`.
  New values `$100=52.493`, `$101=52.493`, `$110=381`, `$111=381`,
  `$130=3048`, `$131=3048`. Safety settings preserved
  (`$20=0`, `$22=0`, `$21=0`, direction mask `$3=0`).

Known discrepancy: pre-change `$100` was `23.805`, which doesn't match the
stated drivetrain (gives ratio 2.206 vs. new 52.493 — not a standard
microstep divisor). First jog at 0.03 in/s will reveal whether the new
value or the old one matches physical reality; bounded error per step at
that speed is ~0.1 mm. See `arduino-grbl/PROJECT_MEMORY.md` for the
commissioning items that gate first motion.

One-click deploy workflow validated end-to-end against the live
controller: `/tmp/apply_first_motion_env.sh` left in place as a reusable
.env-change restart script with auto-rollback. `scripts/deploy_pi.sh`
tested with the same restart cycle (git pull no-op, pip install already
satisfied, systemctl restart + verify).

2026-10-23 — consolidation migration M1-M3 complete on the Pi; M4 failed
once on venv shebang paths and auto-rolled back; fix landed. The Python
venv's wrapper scripts (`uvicorn`, `fastapi`, `pytest`, …) bake an
absolute path to the interpreter in their shebang. When M3 creates the
venv at `.NEW`, every wrapper's shebang reads
`/mudprinter-controller.NEW/.venv/bin/python3` — a path that **ceases
to exist the moment M4 renames `.NEW` to the live path**. systemd then
exec-fails with `203/EXEC` and the misleading "No such file or
directory" (referring to the shebang's interpreter, not the wrapper).
Fix added to `scripts/consolidation_migrate.sh`: an idempotent
`grep + sed` rewrite between the rename and `systemctl start`, plus a
pre-start `uvicorn --version` sanity check that rolls back BEFORE
systemd gets to attempt a restart loop. Documented in
`docs/consolidation-migration.md` → Phase M3 warning + Phase M4
required-step list + risks table.

2026-10-23 — consolidation on top of oct16-base: brought in the one-click
Mac → Pi deploy workflow (`scripts/Deploy to Pi.command`, `deploy_pi.sh`,
`preflight_state.py`, `docs/one-click-deploy.md`, 18 preflight tests). The
hardware-integrated controller is authoritative; the launcher restarts the
existing systemd `mudprinter` service unchanged.

2026-10-20 — hardware integration pass (Mac; not on hardware): GPIO layer
(gpiozero), debounced remote, one Z controller, REMOTE STOP latch +
recovery, diagnostics, deployment (systemd watchdog + pinctrl Z LOW,
config.txt, udev, labwc kiosk), read-only capture tool, commissioning
procedure. 346 tests.

2026-10-20 — control-box wiring recorded: Pi GPIO pin map wired and
authoritative in `../CONTROL_BOX_HARDWARE.md`; touchscreen = 7" 800×480 DSI;
remote channel functions fixed. No code changed.

2026-10-15 — machine profile + job preflight (manually reviewed,
checkpoint 2026-10-15): this printer's own persisted profile, authoritative preflight,
incompatible native jobs cannot Start, envelope in the view. 289 tests.

2026-10-15 — diagnosed: the Pi top view (and the Designer Simulation's drag
LOD) cut corners by stride-decimating vertices; PrintPlan → .mudjob and the
machine path are faithful. See Open Questions → "KNOWN ISSUE — corner-cutting".
Fixed the same day with error-bounded simplification, verified on the exact
project, manually reviewed in both viewers and committed (checkpoint). 265 tests.

2026-10-14 (UI pass — manually reviewed, checkpoint 2026-10-15)

Three-area dashboard (job / progress / parameters · controls / jog · toolpath
view); lift adjustment through target lift height + layer thickness with
Hold to apply; speed + feed override in the parameters panel. 262 tests.

2026-10-14 (controller features — manually reviewed, checkpoint 2026-10-15)

Progress / estimates, feed override, 2D view, Outline, diagnostics, manual-Z
lift height with operator-adjustable lift ends at clean cuts. 246 tests.

2026-10-14 (current-machine execution)

Executable `.mudjob` jobs run: origin / prime workflow, Start returns to the
origin, jog / re-zero / prime while awaiting a reposition, live ok-waits
(fixed 10 s per-line timeout risk resolved), jog in G21. 196 tests passing.

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
