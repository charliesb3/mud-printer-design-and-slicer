/**
 * Mud Printer Interface — browser client
 *
 * Connects to the server via WebSocket.
 * Handles state rendering, jog interactions, controls, file upload, and console.
 *
 * Jog dead-man mechanism:
 *   On jog button press  → send jog_start, then send jog_heartbeat every 100 ms
 *   On jog button release → send jog_stop, clear heartbeat interval
 *   The server stops the jog loop if heartbeats stop arriving (≤ 300 ms timeout).
 *   This ensures motion stops if this page is closed or the network drops.
 *
 * Stop button:
 *   Must be held for 2.5 seconds. Releases before that cancel the action.
 *   A visual fill animation provides feedback during the hold.
 *
 * Lifts / frame reposition:
 *   A building job is an ordered list of lifts. Between lifts the server stops
 *   in job state "awaiting_reposition"; every connected client (laptop, Pi
 *   touchscreen kiosk, …) shows the reposition panel. The operator confirms by
 *   pressing and holding Confirm (2 s). The message names the lift being
 *   confirmed, so a stale screen or a second tap cannot skip a lift.
 *
 * One UI for every screen: all clients render the same server-owned state;
 * any client's action is broadcast to all. Touch, mouse and keyboard
 * (Space/Enter held on a focused hold button) all work.
 */

"use strict";

// ------------------------------------------------------------------ //
// WebSocket management                                                 //
// ------------------------------------------------------------------ //

let ws = null;
let reconnectTimer = null;
let wsAlive = false;

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    wsAlive = true;
    setWsStatus("connected");
    clearTimeout(reconnectTimer);
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") handleState(msg);
      else if (msg.type === "console") appendConsoleLine(msg.direction, msg.line);
      else if (msg.type === "notice") showNotice(msg.message);
    } catch (e) {
      console.warn("WS parse error", e);
    }
  };

  ws.onclose = () => {
    wsAlive = false;
    ws = null;
    setWsStatus("reconnecting");
    reconnectTimer = setTimeout(connectWS, 1500);
  };

  ws.onerror = () => {
    ws && ws.close();
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function setWsStatus(status) {
  const el = document.getElementById("ws-status");
  if (status === "connected") {
    el.textContent = "● browser connected";
    el.style.color = "#4ade80";
  } else {
    el.textContent = "● reconnecting…";
    el.style.color = "#f87171";
  }
}

let noticeTimer = null;

function showNotice(message) {
  const el = document.getElementById("notice");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { el.hidden = true; }, 6000);
}

// ------------------------------------------------------------------ //
// State rendering                                                      //
// ------------------------------------------------------------------ //

const JOB_ACTIVE_STATES = ["running", "held", "awaiting_reposition"];

function jobActive(s) {
  return !!(s.job && JOB_ACTIVE_STATES.includes(s.job.state));
}

let lastState = {};

function handleState(s) {
  lastState = s;

  // Connection badge
  const connBadge = document.getElementById("conn-badge");
  if (s.connected) {
    connBadge.textContent = "CONNECTED";
    connBadge.className = "badge badge-connected";
  } else {
    connBadge.textContent = "DISCONNECTED";
    connBadge.className = "badge badge-disconnected";
  }

  // GRBL state badge
  const grblState = document.getElementById("grbl-state");
  const rawState = s.grbl_state || "Disconnected";
  const baseState = rawState.split(":")[0].toLowerCase();
  grblState.textContent = rawState;
  grblState.className = "state-badge state-" + (
    ["idle", "run", "jog", "hold", "alarm", "disconnected"].includes(baseState)
      ? baseState : "other"
  );

  // Position + feed rate
  const connected = s.connected;
  document.getElementById("pos-x").textContent = connected ? s.x.toFixed(3) : "—";
  document.getElementById("pos-y").textContent = connected ? s.y.toFixed(3) : "—";
  document.getElementById("feed-rate").textContent = connected ? s.feed_rate.toFixed(0) : "—";

  // Jog buttons
  renderJogSection(s);

  // Operational controls
  renderControls(s);

  // Job section
  renderJobSection(s);

  // Lifts + frame reposition panel
  renderLifts(s);
}

function renderJogSection(s) {
  const warn = document.getElementById("jog-warning");
  const btns = document.querySelectorAll(".jog-btn");
  const canJog = s.connected
    && s.jog_enabled
    && ["Idle", "Jog"].includes(s.grbl_state)
    && !jobActive(s);   // the server also refuses jogging while a job is active

  if (s.jog_warning) {
    warn.textContent = s.jog_warning;
    warn.classList.add("visible");
  } else {
    warn.classList.remove("visible");
  }

  btns.forEach(btn => { btn.disabled = !canJog; });
}

function renderControls(s) {
  const inActiveState = s.connected
    && ["Run", "Jog", "Hold", "Idle"].includes((s.grbl_state || "").split(":")[0]);
  const inHold = (s.grbl_state || "").startsWith("Hold");
  const inRun = ["Run", "Jog"].includes((s.grbl_state || "").split(":")[0]);

  document.getElementById("btn-hold").disabled = !inRun;
  document.getElementById("btn-resume").disabled = !inHold;
  document.getElementById("btn-stop").disabled = !s.connected;
}

function renderJobSection(s) {
  const job = s.job;
  const fileEl = document.getElementById("job-filename");
  const stateBadge = document.getElementById("job-state-badge");
  const metaEl = document.getElementById("job-meta");
  const progressWrap = document.getElementById("progress-wrap");
  const progressFill = document.getElementById("progress-fill");
  const startBtn = document.getElementById("btn-start");

  if (!job) {
    fileEl.textContent = "No job loaded";
    fileEl.className = "job-filename empty";
    stateBadge.style.display = "none";
    metaEl.textContent = "";
    progressWrap.classList.remove("visible");
    startBtn.disabled = true;
    return;
  }

  fileEl.textContent = job.filename;
  fileEl.className = "job-filename";

  // State badge
  const jobState = job.state || "ready";
  stateBadge.textContent = jobState === "awaiting_reposition"
    ? "AWAITING REPOSITION" : jobState.toUpperCase();
  stateBadge.style.display = "inline-block";
  stateBadge.className = "state-badge " + {
    ready:     "state-idle",
    running:   "state-run",
    held:      "state-hold",
    awaiting_reposition: "state-hold",
    stopped:   "state-disconnected",
    completed: "state-idle",
    error:     "state-alarm",
  }[jobState] || "state-other";

  // Meta line
  const pct = job.total_lines > 0
    ? Math.round((job.current_line / job.total_lines) * 100) : 0;
  const elapsed = job.elapsed_seconds != null
    ? fmtDuration(job.elapsed_seconds) : null;

  let meta = `${job.current_line} / ${job.total_lines} lines (${pct}%)`;
  if (elapsed) meta += `   ${elapsed} elapsed`;
  if (job.error) meta = `Error: ${job.error}`;
  metaEl.textContent = meta;

  // Progress bar
  if (["running", "held", "awaiting_reposition", "stopped", "completed"].includes(jobState)) {
    progressWrap.classList.add("visible");
    progressFill.style.width = pct + "%";
  } else {
    progressWrap.classList.remove("visible");
  }

  // Start button (never for a non-executable job: the server refuses it too)
  const canStart = s.connected
    && s.grbl_state === "Idle"
    && job.executable !== false
    && ["ready", "stopped", "completed", "error"].includes(jobState);
  startBtn.disabled = !canStart;
}

function liftLabel(i, total) {
  return `Lift ${i + 1} of ${total}`;
}

function renderLifts(s) {
  const job = s.job;
  const isBuilding = !!(job && job.kind === "building");
  const badge = document.getElementById("lift-badge");
  const summary = document.getElementById("lift-summary");
  const strip = document.getElementById("lift-strip");
  const panel = document.getElementById("reposition-panel");

  badge.hidden = summary.hidden = strip.hidden = !isBuilding;
  document.getElementById("lift-details").hidden = !isBuilding;
  renderNotExecutable(isBuilding && job.executable === false ? job : null);
  if (isBuilding) renderLiftList(job);
  const awaiting = isBuilding && job.state === "awaiting_reposition" && job.awaiting_lift != null;
  panel.hidden = !awaiting;
  if (!awaiting) cancelConfirmHold();
  if (!isBuilding) return;

  const total = job.total_lifts;
  const cur = job.current_lift;
  const started = job.state !== "ready";

  // Status-bar badge: visible from anywhere on the screen
  badge.textContent = awaiting
    ? `LIFT ${cur + 1}/${total} DONE`
    : `LIFT ${started ? cur + 1 : 1}/${total}`;
  badge.classList.toggle("awaiting", awaiting);

  // Summary line
  if (job.executable === false) {
    summary.textContent = `${total} lifts — building structure only (not executable)`;
  } else if (!started) {
    summary.textContent = `${total} lifts — starts with lift 1`;
  } else if (awaiting) {
    summary.textContent = `${liftLabel(cur, total)} complete — waiting for frame reposition before lift ${job.awaiting_lift + 1}`;
  } else if (job.state === "completed") {
    summary.textContent = `All ${total} lifts complete`;
  } else {
    summary.textContent = `${liftLabel(cur, total)}: ${job.lift_line} / ${job.lift_total_lines} lines`;
  }

  // One chip per lift: done / current / next-awaiting / pending
  strip.innerHTML = "";
  job.lifts.forEach((lift, i) => {
    const chip = document.createElement("div");
    let cls = "";
    if (job.state === "completed" || (started && i < cur) || (awaiting && i === cur)) cls = "done";
    else if (awaiting && i === job.awaiting_lift) cls = "next";
    else if (started && i === cur) cls = "current";
    chip.className = "lift-chip " + cls;
    chip.textContent = String(i + 1);
    chip.title = lift.description || liftLabel(i, total);
    strip.appendChild(chip);
  });

  if (!awaiting) return;

  // Reposition panel
  const next = job.awaiting_lift;
  document.getElementById("reposition-text").textContent =
    `${liftLabel(cur, total)} is complete. Reposition the frame for lift ${next + 1}.`;

  const detailEl = document.getElementById("reposition-detail");
  const info = (job.lifts[next] || {}).reposition_before;
  const parts = [];
  if (info && typeof info.rise === "number") {
    parts.push(`Frame rise from job: ${info.rise}${job.units ? " " + job.units : ""}`);
  }
  if (info && info.note) parts.push(String(info.note));
  if ((job.lifts[next] || {}).description) parts.push(`Next: ${job.lifts[next].description}`);
  detailEl.textContent = parts.join(" · ");
  detailEl.hidden = parts.length === 0;

  const btn = document.getElementById("btn-confirm");
  const blockedEl = document.getElementById("reposition-blocked");
  const idle = s.connected && s.grbl_state === "Idle";
  btn.disabled = !idle;
  btn.dataset.lift = String(next);
  document.getElementById("btn-confirm-label").textContent =
    `Hold to confirm — start lift ${next + 1}`;
  blockedEl.textContent = idle ? ""
    : (s.connected ? `GRBL is ${s.grbl_state}; it must be Idle to continue.` : "Not connected to GRBL.");
  if (!idle) cancelConfirmHold();
}

function renderNotExecutable(job) {
  const box = document.getElementById("job-blocked");
  box.hidden = !job;
  if (!job) return;
  const list = document.getElementById("job-unresolved");
  list.innerHTML = "";
  (job.unresolved || []).forEach(u => {
    const li = document.createElement("li");
    li.textContent = u.summary;
    li.title = u.id;
    list.appendChild(li);
  });
}

function fmtNum(v) {
  return typeof v === "number" ? String(Math.round(v * 1000) / 1000) : "";
}

// One row per lift, exactly as the job file defines it (display only)
function renderLiftList(job) {
  const list = document.getElementById("lift-list");
  const unit = job.units ? " " + job.units : "";
  list.innerHTML = "";
  job.lifts.forEach((lift, i) => {
    const parts = [`Lift ${i + 1}`];
    if (lift.layers) parts.push(`layers ${lift.layers.first + 1}–${lift.layers.last + 1}`);
    if (typeof lift.z_bottom === "number" && typeof lift.z_top === "number")
      parts.push(`Z ${fmtNum(lift.z_bottom)}–${fmtNum(lift.z_top)}${unit}`);
    const r = lift.reposition_before;
    if (r) parts.push(typeof r.rise === "number" ? `frame +${fmtNum(r.rise)}${unit} before` : "frame reposition before");
    if (job.executable !== false) parts.push(`${lift.total_lines} lines`);
    const row = document.createElement("div");
    row.className = "lift-row";
    row.textContent = parts.join(" · ");
    if (lift.description) row.title = lift.description;
    list.appendChild(row);
  });
}

function fmtDuration(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ------------------------------------------------------------------ //
// Jog controls (dead-man / fail-safe)                                 //
// ------------------------------------------------------------------ //

let jogHeartbeatInterval = null;
let jogActive = false;

function startJog(axis, dir) {
  if (jogActive) stopJog();
  jogActive = true;
  send({ type: "jog_start", axis, direction: dir });
  // Send heartbeat every 100 ms to keep jog alive on the server
  jogHeartbeatInterval = setInterval(() => {
    send({ type: "jog_heartbeat" });
  }, 100);
}

function stopJog() {
  if (!jogActive) return;
  jogActive = false;
  clearInterval(jogHeartbeatInterval);
  jogHeartbeatInterval = null;
  send({ type: "jog_stop" });
}

function attachJogButton(btn) {
  const axis = btn.dataset.axis;
  const dir  = parseInt(btn.dataset.dir, 10);
  let touchId = null;

  // Touch events (primary for Pi touchscreen)
  btn.addEventListener("touchstart", (e) => {
    e.preventDefault(); // suppress mouse events on mobile
    if (touchId !== null) return;
    touchId = e.changedTouches[0].identifier;
    btn.classList.add("pressing");
    startJog(axis, dir);
  }, { passive: false });

  const touchEnd = (e) => {
    const t = Array.from(e.changedTouches).find(t => t.identifier === touchId);
    if (!t) return;
    touchId = null;
    btn.classList.remove("pressing");
    stopJog();
  };
  btn.addEventListener("touchend", touchEnd);
  btn.addEventListener("touchcancel", touchEnd);

  // Mouse events (Mac browser)
  btn.addEventListener("mousedown", (e) => {
    if (touchId !== null) return; // already handled by touch
    btn.classList.add("pressing");
    startJog(axis, dir);
  });
  btn.addEventListener("mouseup", () => {
    btn.classList.remove("pressing");
    stopJog();
  });
  btn.addEventListener("mouseleave", () => {
    if (btn.classList.contains("pressing")) {
      btn.classList.remove("pressing");
      stopJog();
    }
  });
}

// ------------------------------------------------------------------ //
// Press-and-hold buttons (Stop 2.5 s, Confirm reposition 2 s)          //
// ------------------------------------------------------------------ //

const STOP_HOLD_MS = 2500;
const CONFIRM_HOLD_MS = 2000;

/**
 * Run onComplete only after the button has been held for `ms`. Releasing,
 * leaving the button, or the button becoming disabled cancels it.
 * Works with touch, mouse, and keyboard (Space / Enter held).
 * onBegin (optional) captures a value at the start of the hold, passed to
 * onComplete. Returns a cancel function.
 */
function attachHoldButton(btn, ms, onComplete, onBegin = () => undefined) {
  let timer = null;
  let touchId = null;
  btn.style.setProperty("--hold-ms", ms + "ms");

  function beginHold() {
    if (btn.disabled || timer !== null) return;
    btn.classList.add("holding");
    const captured = onBegin();
    timer = setTimeout(() => {
      endHold();
      if (!btn.disabled) onComplete(captured);
    }, ms);
  }

  function endHold() {
    btn.classList.remove("holding");
    clearTimeout(timer);
    timer = null;
  }

  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    if (touchId !== null) return;
    touchId = e.changedTouches[0].identifier;
    beginHold();
  }, { passive: false });

  const touchEnd = (e) => {
    const t = Array.from(e.changedTouches).find(t => t.identifier === touchId);
    if (!t) return;
    touchId = null;
    endHold();
  };
  btn.addEventListener("touchend", touchEnd);
  btn.addEventListener("touchcancel", touchEnd);

  btn.addEventListener("mousedown", () => beginHold());
  btn.addEventListener("mouseup",   () => endHold());
  btn.addEventListener("mouseleave",() => endHold());

  btn.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); beginHold(); }
  });
  btn.addEventListener("keyup", (e) => {
    if (e.key === " " || e.key === "Enter") endHold();
  });
  btn.addEventListener("blur", () => endHold());

  return endHold;
}

let cancelConfirmHold = () => {};

function attachStopButton() {
  attachHoldButton(document.getElementById("btn-stop"), STOP_HOLD_MS,
    () => send({ type: "stop" }));
}

function attachConfirmButton() {
  const btn = document.getElementById("btn-confirm");
  cancelConfirmHold = attachHoldButton(btn, CONFIRM_HOLD_MS,
    // Send the lift that was shown when the hold began; the server refuses it if stale.
    (lift) => send({ type: "confirm_reposition", lift }),
    () => parseInt(btn.dataset.lift, 10));
}

// ------------------------------------------------------------------ //
// Other controls                                                       //
// ------------------------------------------------------------------ //

function attachControls() {
  document.getElementById("btn-hold").addEventListener("click", () => {
    send({ type: "hold" });
  });
  document.getElementById("btn-resume").addEventListener("click", () => {
    send({ type: "resume" });
  });
  document.getElementById("btn-start").addEventListener("click", () => {
    send({ type: "job_start" });
  });
}

// ------------------------------------------------------------------ //
// File upload                                                          //
// ------------------------------------------------------------------ //

function attachFileUpload() {
  const input = document.getElementById("file-input");
  const statusEl = document.getElementById("upload-status");

  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;

    statusEl.textContent = `Uploading ${file.name}…`;
    statusEl.className = "";
    input.value = "";

    const form = new FormData();
    form.append("file", file);

    try {
      const resp = await fetch("/upload", { method: "POST", body: form });
      const data = await resp.json();
      if (data.success) {
        statusEl.textContent = data.kind !== "building"
          ? `Uploaded: ${data.filename} (${data.total_lines} lines)`
          : data.executable === false
            ? `Uploaded: ${data.filename} (${data.total_lifts} lifts) — not executable: structure only`
            : `Uploaded: ${data.filename} (${data.total_lifts} lifts, ${data.total_lines} lines)`;
        statusEl.className = "upload-ok";
      } else {
        statusEl.textContent = `Upload failed: ${data.error}`;
        statusEl.className = "upload-err";
      }
    } catch (e) {
      statusEl.textContent = `Upload error: ${e}`;
      statusEl.className = "upload-err";
    }
  });
}

// ------------------------------------------------------------------ //
// Console                                                              //
// ------------------------------------------------------------------ //

const MAX_CONSOLE_LINES = 200;
let consoleLineCount = 0;

function appendConsoleLine(direction, line) {
  const log = document.getElementById("console-log");
  const div = document.createElement("div");
  const isOk = line === "ok";
  const isErr = line.startsWith("error:") || line.startsWith("ALARM:");
  div.className = "console-line " + direction
    + (isOk ? " ok" : "")
    + (isErr ? " err" : "");
  div.textContent = (direction === "tx" ? "> " : "< ") + line;
  log.appendChild(div);

  consoleLineCount++;
  while (consoleLineCount > MAX_CONSOLE_LINES) {
    log.removeChild(log.firstChild);
    consoleLineCount--;
  }

  log.scrollTop = log.scrollHeight;
}

function attachConsole() {
  const toggle = document.getElementById("console-toggle");
  const body   = document.getElementById("console-body");
  let open = false;

  toggle.addEventListener("click", () => {
    open = !open;
    body.style.display = open ? "block" : "none";
    toggle.textContent = "Advanced / Console " + (open ? "▴" : "▾");
  });

  const input = document.getElementById("console-cmd");
  const sendBtn = document.getElementById("console-send-btn");

  const doSend = () => {
    const cmd = input.value.trim();
    if (!cmd) return;
    send({ type: "console_send", command: cmd });
    input.value = "";
  };

  sendBtn.addEventListener("click", doSend);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSend();
  });
}

// ------------------------------------------------------------------ //
// Init                                                                 //
// ------------------------------------------------------------------ //

document.addEventListener("DOMContentLoaded", () => {
  // Attach jog buttons
  document.querySelectorAll(".jog-btn").forEach(attachJogButton);

  // Attach stop and reposition-confirm press-and-hold buttons
  attachStopButton();
  attachConfirmButton();

  // Attach other controls
  attachControls();

  // Attach file upload
  attachFileUpload();

  // Attach console
  attachConsole();

  // Start WebSocket
  connectWS();
});
