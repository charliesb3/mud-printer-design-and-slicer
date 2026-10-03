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

// ------------------------------------------------------------------ //
// State rendering                                                      //
// ------------------------------------------------------------------ //

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
}

function renderJogSection(s) {
  const warn = document.getElementById("jog-warning");
  const btns = document.querySelectorAll(".jog-btn");
  const canJog = s.connected
    && s.jog_enabled
    && ["Idle", "Jog"].includes(s.grbl_state);

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
  stateBadge.textContent = jobState.toUpperCase();
  stateBadge.style.display = "inline-block";
  stateBadge.className = "state-badge " + {
    ready:     "state-idle",
    running:   "state-run",
    held:      "state-hold",
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
  if (["running", "held", "stopped", "completed"].includes(jobState)) {
    progressWrap.classList.add("visible");
    progressFill.style.width = pct + "%";
  } else {
    progressWrap.classList.remove("visible");
  }

  // Start button
  const canStart = s.connected
    && s.grbl_state === "Idle"
    && ["ready", "stopped", "completed", "error"].includes(jobState);
  startBtn.disabled = !canStart;
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
// Stop button (press-and-hold, 2.5 s)                                 //
// ------------------------------------------------------------------ //

const STOP_HOLD_MS = 2500;
let stopTimer = null;
let stopTouchId = null;

function attachStopButton() {
  const btn = document.getElementById("btn-stop");
  const fill = document.getElementById("stop-fill");

  function beginHold() {
    btn.classList.add("holding");
    stopTimer = setTimeout(() => {
      send({ type: "stop" });
      endHold();
    }, STOP_HOLD_MS);
  }

  function endHold() {
    btn.classList.remove("holding");
    clearTimeout(stopTimer);
    stopTimer = null;
  }

  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    if (stopTouchId !== null) return;
    stopTouchId = e.changedTouches[0].identifier;
    beginHold();
  }, { passive: false });

  const touchEnd = (e) => {
    const t = Array.from(e.changedTouches).find(t => t.identifier === stopTouchId);
    if (!t) return;
    stopTouchId = null;
    endHold();
  };
  btn.addEventListener("touchend", touchEnd);
  btn.addEventListener("touchcancel", touchEnd);

  btn.addEventListener("mousedown", () => beginHold());
  btn.addEventListener("mouseup",   () => endHold());
  btn.addEventListener("mouseleave",() => endHold());
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
        statusEl.textContent = `Uploaded: ${data.filename} (${data.total_lines} lines)`;
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

  // Attach stop hold-to-confirm
  attachStopButton();

  // Attach other controls
  attachControls();

  // Attach file upload
  attachFileUpload();

  // Attach console
  attachConsole();

  // Start WebSocket
  connectWS();
});
