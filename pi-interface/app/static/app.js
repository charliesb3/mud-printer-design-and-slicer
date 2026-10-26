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
 * Print origin / prime (machine setup, never part of the job): Set origin
 *   (hold 1.5 s) = work XY zero here; Set prime here; Prime (hold 1 s) moves
 *   to the prime position at jog speed. A building job needs the origin and
 *   returns to it on Start. While a job awaits a frame reposition the operator
 *   may jog / Set origin / Prime before confirming.
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
    // On reconnect, release any button that was "pressing" when the WS
    // died. The server-side cleanup already dropped any held Z / jog
    // source when the socket closed; this clears the UI's local
    // pointer state so the next deliberate press is accepted. See
    // tenth-pass "Z duty = 1.0 WS close" root-cause note.
    forceReleaseAllHeldButtons();
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
    // Release every held input and reset every held-button's local
    // pointer state. Without this, a WS-death-while-held leaves the
    // button with pointerId !== null; begin() then returns early on
    // the next press and the UI goes unresponsive even though the
    // WS reconnects cleanly. See tenth-pass memory.
    stopAllHeldInputs();
    forceReleaseAllHeldButtons();
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

  // Position + feed rate. ALWAYS display MPos in inches (converted
  // server-side if GRBL reports mm). The status bar label is "in" so
  // the operator cannot misread GRBL's raw mm as inches — the mistake
  // behind the twelfth-pass incident.
  const connected = s.connected;
  const posX = connected ? (typeof s.x_in === "number" ? s.x_in : s.x / 25.4) : null;
  const posY = connected ? (typeof s.y_in === "number" ? s.y_in : s.y / 25.4) : null;
  document.getElementById("pos-x").textContent = posX == null ? "—" : posX.toFixed(3);
  document.getElementById("pos-y").textContent = posY == null ? "—" : posY.toFixed(3);
  document.getElementById("feed-rate").textContent = connected ? s.feed_rate.toFixed(0) : "—";

  // Jog buttons
  renderJogSection(s);

  // Operational controls
  renderControls(s);

  // Job section
  renderJobSection(s);

  // Lifts + frame reposition panel
  renderLifts(s);

  // Print origin + prime position + outline
  renderSetup(s);

  // Progress / estimates, speeds + feed override, lift height, 2D view, diagnostics
  renderProgress(s);
  renderSpeed(s);
  renderParams(s);
  renderPrinter(s);
  renderPreflight(s);
  renderView(s);
  renderDiagnostics(s);
  renderRemoteStop(s);
  renderZ(s);
  renderHardware(s);
}

function latched(s) {
  return !!(s.remote_stop && s.remote_stop.latched);
}

function renderJogSection(s) {
  const warn = document.getElementById("jog-warning");
  const btns = document.querySelectorAll(".jog-btn");
  const awaiting = !!(s.job && s.job.state === "awaiting_reposition");
  const canJog = s.connected
    && s.jog_enabled
    && ["Idle", "Jog"].includes(s.grbl_state)
    && (!jobActive(s) || awaiting)           // jog / re-zero allowed during a frame reposition
    && !(s.setup && s.setup.moving)         // the server applies the same rules
    && !latched(s);

  if (s.jog_warning) {
    warn.textContent = s.jog_warning;
    warn.classList.add("visible");
  } else {
    warn.classList.remove("visible");
  }

  btns.forEach(btn => { btn.disabled = !canJog; });
  renderJogSpeedControl(s);
}

// Jog-speed slider state. The slider is 0-100 (percent of configured ceiling);
// the user never sees the percentage. jogSliderDirty tracks whether the user
// is actively dragging — while dirty, state broadcasts don't overwrite the
// slider position (so the thumb doesn't jump mid-drag).
let jogSliderDirty = false;
const JOG_NUDGE_IN_S = 0.05;

function renderJogSpeedControl(s) {
  const label = document.getElementById("jog-speed-in-s");
  const slider = document.getElementById("jog-speed-slider");
  const meta = document.getElementById("jog-speed-meta");
  if (!label || !slider || !meta) return;

  const maxInS = s.jog_speed_max_in_s || 0;
  const curInS = s.jog_speed_in_s || 0;
  const maxMm = s.jog_speed_max_mm_min || 0;
  const curMm = s.jog_speed_mm_min || 0;

  label.textContent = curInS.toFixed(3);

  // Slider shows percent-of-ceiling (0-100). Enabled only when jogging is
  // configured. Not updated while the user is actively dragging it.
  slider.disabled = !s.jog_enabled;
  if (!jogSliderDirty && s.jog_enabled && maxInS > 0) {
    const pct = Math.max(0, Math.min(100, Math.round(100 * curInS / maxInS)));
    slider.value = String(pct);
  }
  const stepButtons = document.querySelectorAll("#jog-speed-minus, #jog-speed-plus");
  stepButtons.forEach(b => { b.disabled = !s.jog_enabled; });

  if (!s.jog_enabled) {
    meta.textContent = "Jog disabled — set JOG_SPEED_MM_MIN in .env";
    meta.classList.add("warn");
  } else {
    meta.textContent =
      `${curMm.toFixed(0)} / ${maxMm.toFixed(0)} mm/min  (max ${maxInS.toFixed(3)} in/s)`;
    meta.classList.remove("warn");
  }
}

function sendJogSpeedInS(inS) {
  const s = lastState || {};
  const maxInS = s.jog_speed_max_in_s || 0;
  const clamped = Math.max(0, Math.min(maxInS, inS));
  send({ type: "jog_set_speed", speed_in_s: clamped });
}

function attachJogSpeedSlider() {
  const slider = document.getElementById("jog-speed-slider");
  if (!slider) return;
  // input: fires continuously while dragging — update the big display and
  // suppress the next broadcast overwrite. change: fires on release — final
  // commit to the server.
  slider.addEventListener("input", () => {
    jogSliderDirty = true;
    const pct = parseFloat(slider.value);
    const maxInS = (lastState || {}).jog_speed_max_in_s || 0;
    const target = (pct / 100) * maxInS;
    const label = document.getElementById("jog-speed-in-s");
    if (label) label.textContent = target.toFixed(3);
  });
  slider.addEventListener("change", () => {
    const pct = parseFloat(slider.value);
    const maxInS = (lastState || {}).jog_speed_max_in_s || 0;
    sendJogSpeedInS((pct / 100) * maxInS);
    // Release ownership so the next state broadcast refreshes the slider.
    jogSliderDirty = false;
  });
  // Make sure a flick / drag-cancel also releases ownership.
  slider.addEventListener("pointerup", () => { jogSliderDirty = false; });
  slider.addEventListener("pointercancel", () => { jogSliderDirty = false; });

  const minus = document.getElementById("jog-speed-minus");
  const plus = document.getElementById("jog-speed-plus");
  const step = (delta) => () => {
    const s = lastState || {};
    const cur = s.jog_speed_in_s || 0;
    const next = Math.round((cur + delta) * 100) / 100;
    sendJogSpeedInS(Math.max(0, next));
  };
  if (minus) minus.addEventListener("click", step(-JOG_NUDGE_IN_S));
  if (plus)  plus.addEventListener("click", step(JOG_NUDGE_IN_S));
}

function renderControls(s) {
  const inActiveState = s.connected
    && ["Run", "Jog", "Hold", "Idle"].includes((s.grbl_state || "").split(":")[0]);
  const inHold = (s.grbl_state || "").startsWith("Hold");
  const inRun = ["Run", "Jog"].includes((s.grbl_state || "").split(":")[0]);

  document.getElementById("btn-hold").disabled = !inRun;
  document.getElementById("btn-resume").disabled = !inHold || latched(s);
  document.getElementById("btn-stop").disabled = !s.connected;
}

function renderJobSection(s) {
  const job = s.job;
  const fileEl = document.getElementById("job-filename");
  const stateBadge = document.getElementById("job-state-badge");
  const metaEl = document.getElementById("job-meta");
  const startBtn = document.getElementById("btn-start");

  if (!job) {
    fileEl.textContent = "No job loaded";
    fileEl.className = "job-filename empty";
    stateBadge.style.display = "none";
    metaEl.textContent = "";
    startBtn.disabled = true;
    startBtn.textContent = "START JOB";
    document.getElementById("start-note").textContent = "Upload a job first.";
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

  // Identity line: kind, lifts / layers, lines, readiness (progress is in its own panel)
  const layers = (job.lifts || []).reduce((n, l) => n + ((l.layers && l.layers.count) || 0), 0);
  let meta = job.kind === "building"
    ? `Building job · ${job.total_lifts} lifts` + (layers ? ` · ${layers} layers` : "") + ` · ${job.total_lines} lines`
    : `G-code file · ${job.total_lines} lines`;
  if (job.executable === false) meta += " · not executable";
  if (job.error) meta = `Error: ${job.error}`;
  metaEl.textContent = meta;

  // Start button (never for a non-executable job: the server refuses it too).
  // A building job needs the print origin and returns to it first.
  const setup = s.setup || {};
  const building = job.kind === "building";
  const startable = ["ready", "stopped", "completed", "error"].includes(jobState);
  let startWhy = "";
  const pfj = job.preflight;
  if (startable && pfj && pfj.blocking) {
    startWhy = `Does not fit printer "${pfj.profile_name}" — see Current Job. Fix the job or this printer's profile.`;
  } else if (startable && job.executable !== false && building) {
    if (!setup.origin_set) startWhy = "Set the print origin first (jog to it, then hold Set origin here).";
    else if (!setup.positioning_enabled) startWhy = "Start returns to the origin at jog speed — jogging is disabled.";
  }
  const canStart = s.connected
    && s.grbl_state === "Idle"
    && job.executable !== false
    && startable
    && !startWhy
    && !setup.moving;
  startBtn.disabled = !canStart;
  startBtn.textContent = building && job.executable !== false ? "START — returns to origin first" : "START JOB";
  document.getElementById("start-note").textContent = startWhy;

  renderFirstMove(s, job);
}

// ---- Twelfth-pass preflight: show the first commanded XY destination and
// the expected travel distance from the current work position. If > 6 in,
// style amber so the operator sees it clearly before pressing Start.
function renderFirstMove(s, job) {
  const box = document.getElementById("first-move");
  const destEl = document.getElementById("first-move-dest");
  const travelEl = document.getElementById("first-move-travel");
  if (!box || !destEl || !travelEl) return;
  const fm = job && job.first_motion;
  if (!fm || job.executable === false || job.kind !== "building") {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  destEl.textContent = `First ${fm.kind} move → X ${fm.x_in.toFixed(3)} in, Y ${fm.y_in.toFixed(3)} in`;
  const setup = s.setup || {};
  const wx = setup.work_x, wy = setup.work_y;
  if (wx == null || wy == null || !setup.origin_set) {
    travelEl.textContent = "Distance from current position: unavailable (origin not established).";
    travelEl.className = "first-move-row warn";
    return;
  }
  // Start of a building job sends `G54 G20 G90 G1 X0 Y0` first (return to
  // the operator's origin), THEN the first job motion. Travel distance
  // for the UI = origin -> first_motion.
  const dx = fm.x_in - 0;
  const dy = fm.y_in - 0;
  const d = Math.sqrt(dx * dx + dy * dy);
  const parts = [];
  parts.push(`Expected travel after return-to-origin: ${d.toFixed(2)} in`);
  parts.push(`(ΔX ${dx >= 0 ? "+" : ""}${dx.toFixed(2)}, ΔY ${dy >= 0 ? "+" : ""}${dy.toFixed(2)})`);
  travelEl.textContent = parts.join("  ·  ");
  travelEl.className = "first-move-row" + (d > 6 ? " warn" : "");
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
  const markEl = document.getElementById("mark-line");
  const mk = job.current_mark;
  markEl.hidden = !(mk && ["running", "held"].includes(job.state));
  if (mk) {
    const u = job.units ? " " + job.units : "";
    markEl.textContent = `Layer ${mk.layer + 1} · nozzle Z ${fmtNum(mk.z)}${u}` +
      (typeof mk.z_in_lift === "number" ? ` (${fmtNum(mk.z_in_lift)}${u} above the frame base)` : "") +
      " — Z is manual";
  }

  if (job.phase === "origin") {
    summary.textContent = "Returning to the print origin…";
  } else if (job.executable === false) {
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
  const st = s.setup || {};
  let blocked = "";
  if (!s.connected) blocked = "Not connected to GRBL.";
  else if (!idle) blocked = `GRBL is ${s.grbl_state}; it must be Idle to continue.`;
  else if (st.moving) blocked = "Wait until the machine has stopped moving.";
  else if (!st.origin_set) blocked = "The print origin is not established — Set origin before continuing.";
  btn.disabled = !!blocked;
  btn.dataset.lift = String(next);
  document.getElementById("btn-confirm-label").textContent =
    `Hold to confirm — start lift ${next + 1}`;
  blockedEl.textContent = blocked;
  if (blocked) cancelConfirmHold();
}

function renderOutline(s, ready) {
  const o = s.job && s.job.outline;
  const st = s.setup || {};
  const el = document.getElementById("outline-preview");
  if (!o) {
    el.textContent = "Outline: load an executable job to see the rectangle it would trace.";
  } else {
    const c = o.corners;
    el.textContent = `Outline traces X ${fmtNum(c[0][0])} → ${fmtNum(c[1][0])}, Y ${fmtNum(c[0][1])} → ` +
      `${fmtNum(c[2][1])} in (${fmtNum(o.width)} × ${fmtNum(o.height)} in, from ${o.source}) at jog speed, ` +
      "starting at the first corner. Z is not moved — make sure the nozzle clears everything.";
  }
  document.getElementById("btn-outline").disabled =
    !(ready && o && st.origin_set && st.positioning_enabled);
}

function renderSetup(s) {
  const st = s.setup || {};
  const u = "in";
  const originEl = document.getElementById("origin-status");
  originEl.textContent = st.origin_set ? "Set — work X0 Y0" : "Not set";
  originEl.className = st.origin_set ? "ok" : "missing";
  document.getElementById("work-pos").textContent =
    st.work_x != null ? `${fmtNum(st.work_x)} / ${fmtNum(st.work_y)} ${u}` : "—";
  const primeEl = document.getElementById("prime-status");
  primeEl.textContent = st.prime
    ? `X ${fmtNum(st.prime[0])} Y ${fmtNum(st.prime[1])} ${u} (from origin)` + (st.origin_set ? "" : " — needs the origin")
    : "Not set";
  primeEl.className = st.prime_ready ? "ok" : "missing";

  // same rules as the server: idle machine, no running job (a frame reposition is fine)
  const running = !!(s.job && ["running", "held"].includes(s.job.state));
  const ready = s.connected && s.grbl_state === "Idle" && !running && !st.moving;
  document.getElementById("btn-set-origin").disabled = !ready;
  document.getElementById("btn-set-prime").disabled = !(ready && st.origin_set);
  document.getElementById("btn-prime").disabled = !(ready && st.prime_ready && st.positioning_enabled);
  renderOutline(s, ready);

  let note = st.note || "";
  if (st.moving === "prime") note = "Moving to the prime position… (HOLD / STOP available)";
  else if (st.moving === "outline") note = "Tracing the outline… (HOLD / STOP available)";
  else if (!note && st.prime_ready && !st.positioning_enabled) note = "Prime uses the jog speed — jogging is disabled.";
  else if (!note && s.job && s.job.state === "awaiting_reposition")
    note = "Frame reposition: you may jog and Set origin again before confirming.";
  document.getElementById("setup-note").textContent = note;
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

function fmtTime(secs) {
  if (secs == null || !isFinite(secs)) return "—";
  secs = Math.max(0, Math.round(secs));
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), sec = secs % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : (m ? `${m} min ${String(sec).padStart(2, "0")} s` : `${sec} s`);
}

function kvGrid(el, rows) {
  el.innerHTML = "";
  rows.filter(r => r && r[1] != null && r[1] !== "").forEach(([k, v]) => {
    const a = document.createElement("div"); a.className = "k"; a.textContent = k;
    const b = document.createElement("div"); b.className = "v"; b.textContent = v;
    el.appendChild(a); el.appendChild(b);
  });
}

function feedOverride(s) {
  return s.grbl && s.grbl.overrides ? s.grbl.overrides.feed : null;
}

// ---- Print progress: whole job + current lift (estimates from the job's G-code)
function renderProgress(s) {
  const job = s.job;
  const sec = document.getElementById("progress-section");
  sec.hidden = !(job && job.executable !== false && job.progress);
  if (sec.hidden) return;
  const p = job.progress, lf = p.lift || {};
  const ov = feedOverride(s);
  const atOv = (t) => (ov && ov !== 100 && t != null) ? ` (≈ ${fmtTime(t * 100 / ov)} at ${ov}%)` : "";
  const unk = p.unknown_lines ? ` — ${p.unknown_lines} move(s) without a known speed are not included` : "";
  document.getElementById("prog-job-pct").textContent = Math.round(p.fraction * 100) + "%";
  document.getElementById("prog-job-bar").style.width = (p.fraction * 100) + "%";
  const inch = (v) => v == null ? null : `${fmtNum(v)} in`;
  kvGrid(document.getElementById("prog-job"), [
    ["Estimated total", fmtTime(p.total_s) + unk],
    ["Elapsed", p.elapsed_s != null ? fmtTime(p.elapsed_s) +
      (p.waiting_s ? ` (of which ${fmtTime(p.waiting_s)} waiting at frame repositions)` : "") : "—"],
    ["Estimated remaining", fmtTime(p.remaining_s) + atOv(p.remaining_s)],
    ["Print path", `${inch(p.print_done_in)} of ${inch(p.print_in)}` + (p.classified ? "" : " (feed moves)")],
    ["Lifts", `${job.current_lift + 1} of ${job.total_lifts}` + (job.lifts_adjusted ? " (adjusted)" : "")],
  ]);
  const building = job.kind === "building";
  document.getElementById("prog-lift-block").hidden = !building;
  if (!building) return;
  document.getElementById("prog-lift-title").textContent =
    `Lift ${job.current_lift + 1} of ${job.total_lifts}`;
  document.getElementById("prog-lift-pct").textContent = Math.round(p.lift_fraction * 100) + "%";
  document.getElementById("prog-lift-bar").style.width = (p.lift_fraction * 100) + "%";
  const u = " in";
  kvGrid(document.getElementById("prog-lift"), [
    ["Layer", lf.layer_in_lift != null ? `${lf.layer_in_lift} of ${lf.layers} in this lift (building layer ${lf.layer + 1})`
      : (lf.layers ? `${lf.layers} layers in this lift` : null)],
    ["Expected nozzle Z", lf.z != null ? `${fmtNum(lf.z)}${u} building · ${fmtNum(lf.z_in_lift)}${u} above the frame base` : null],
    ["Lift estimate", `${fmtTime(p.lift_total_s)} · remaining ${fmtTime(p.lift_remaining_s)}${atOv(p.lift_remaining_s)}`],
    ["Lift print path", `${inch(p.lift_print_done_in)} of ${inch(p.lift_print_in)}`],
    ["Layer thickness", lf.nominal_layer != null ? `${fmtNum(lf.nominal_layer)}${u} nominal (Slicer)` +
      (lf.observed_layer ? ` · ${fmtNum(lf.observed_layer)}${u} observed` : "") : null],
    ["Target lift height", lf.target_lift_height != null ? `${fmtNum(lf.target_lift_height)}${u}` +
      (lf.max_z_travel != null ? ` (Z travel ${fmtNum(lf.max_z_travel)}${u})` : "") : null],
  ]);
  // physical progress toward the target lift height
  const meter = document.getElementById("height-meter");
  const target = lf.target_lift_height, maxz = lf.max_z_travel || target;
  meter.hidden = !(target && lf.layers);
  if (!meter.hidden) {
    const scale = Math.max(maxz || 0, lf.physical_lift_estimate || 0, target);
    const done = lf.physical_done_estimate || 0, plan = lf.physical_lift_estimate || 0;
    document.getElementById("height-done").style.width = (100 * done / scale) + "%";
    document.getElementById("height-plan").style.left = (100 * Math.min(plan, scale) / scale) + "%";
    document.getElementById("height-labels").textContent =
      `≈ ${fmtNum(done)}${u} done (${lf.completed_layers} layers) · this lift ≈ ${fmtNum(plan)}${u} at ` +
      `${fmtNum(lf.observed_layer || lf.nominal_layer)}${u}/layer · target ${fmtNum(target)}${u}`;
  }
}

// ---- Print speed (in/s primary, feed-override % secondary)
// GRBL feed override is 10 % – 200 %. The slider spans that range; the big
// display is the effective print speed in in/s (planned × override). ± fine
// buttons = ±1 %, lower-row buttons = ±10 % and "100 %" reset. Slider commit
// on release sends the minimum number of GRBL real-time bytes to reach the
// target. Multi-feed-rate jobs: the big display is based on the PRINT feed
// (print_in/60) so the number corresponds to the single most relevant speed;
// the secondary line names it as the print feed.
let printSliderDirty = false;

function _planned_print_in_s(s) {
  const feeds = s.job && s.job.machine && s.job.machine.feeds_in_min;
  if (!feeds || !feeds.print) return null;
  return feeds.print / 60.0;
}

function renderSpeed(s) {
  const bigNum = document.getElementById("print-speed-value");
  const slider = document.getElementById("print-speed-slider");
  const meta = document.getElementById("print-speed-meta");
  const minusBtn = document.getElementById("print-speed-minus");
  const plusBtn = document.getElementById("print-speed-plus");
  if (!bigNum || !slider || !meta) return;

  const planned = _planned_print_in_s(s);
  const ov = feedOverride(s);
  const feeds = s.job && s.job.machine && s.job.machine.feeds_in_min;

  // Primary big number
  if (planned == null) {
    bigNum.textContent = "—";
    slider.disabled = true;
    if (minusBtn) minusBtn.disabled = true;
    if (plusBtn)  plusBtn.disabled  = true;
    document.querySelectorAll(".btn-ovr").forEach(b => { b.disabled = true; });
    meta.textContent = s.job ? "This job has no planned print speed in its G-code."
                             : "Load a job for speed control.";
    meta.classList.remove("ovr-changed");
    return;
  }
  slider.disabled = !s.connected;
  if (minusBtn) minusBtn.disabled = !s.connected;
  if (plusBtn)  plusBtn.disabled  = !s.connected;
  document.querySelectorAll(".btn-ovr").forEach(b => { b.disabled = !s.connected; });

  // Slider position = GRBL-reported override, unless we're mid-drag.
  if (!printSliderDirty) {
    slider.value = String(ov != null ? ov : 100);
  }
  // During a drag, show the preview-at-slider effective speed; otherwise
  // show the actual effective (GRBL-override-applied) speed.
  const sliderOv = parseFloat(slider.value);
  const liveOv = printSliderDirty ? sliderOv : (ov != null ? ov : 100);
  const effective = planned * liveOv / 100;
  bigNum.textContent = effective.toFixed(2);

  // Secondary line: planned-at-100% + current override + a hint when more
  // than one feed rate is present in the job (so the single number here is
  // clearly the print feed, not every move's feed).
  const differentTravel = feeds && Math.abs((feeds.travel || 0) - (feeds.print || 0)) > 1e-3;
  let secondary = `planned ${planned.toFixed(2)} in/s at 100 %`;
  secondary += ov == null && s.connected
    ? "  ·  GRBL has not yet reported its override"
    : `  ·  override ${Math.round(liveOv)} %`;
  if (differentTravel) {
    secondary += `  ·  travel moves use ${(feeds.travel / 60).toFixed(2)} in/s at 100 %`;
  }
  meta.textContent = secondary;
  meta.classList.toggle("ovr-changed", liveOv !== 100);

  const note = document.getElementById("ovr-note");
  if (note) {
    note.textContent = ov == null && s.connected
      ? "GRBL has not reported its override yet (it reports after a change or every few status reports)."
      : "Print speed slider = GRBL feed override (10–200 %). Applies to feed moves at once; estimates stay at 100 %.";
  }
}

// Send the sequence of GRBL feed-override real-time bytes to move the current
// override to `targetPct`. GRBL has ±10 (0x91 / 0x92) and ±1 (0x93 / 0x94)
// steps + reset (0x90). We use the minimum number of 10-steps first then
// 1-steps.
function sendFeedOverrideTo(targetPct) {
  const s = lastState || {};
  const cur = feedOverride(s);
  if (cur == null) {
    // Fast path when GRBL hasn't reported yet: reset to 100 % then step.
    send({ type: "feed_override", step: "reset" });
    if (targetPct === 100) return;
  }
  const start = cur != null ? cur : 100;
  const clamped = Math.max(10, Math.min(200, Math.round(targetPct)));
  let delta = clamped - start;
  const sendStep = (step) => send({ type: "feed_override", step });
  while (delta >= 10) { sendStep(10); delta -= 10; }
  while (delta <= -10) { sendStep(-10); delta += 10; }
  while (delta >= 1) { sendStep(1); delta -= 1; }
  while (delta <= -1) { sendStep(-1); delta += 1; }
}

function attachPrintSpeedSlider() {
  const slider = document.getElementById("print-speed-slider");
  if (!slider) return;
  slider.addEventListener("input", () => {
    printSliderDirty = true;
    renderSpeed(lastState);
  });
  slider.addEventListener("change", () => {
    const target = parseFloat(slider.value);
    sendFeedOverrideTo(target);
    // Give GRBL a tick to report back, then release ownership.
    setTimeout(() => { printSliderDirty = false; renderSpeed(lastState); }, 250);
  });
  slider.addEventListener("pointerup", () => { setTimeout(() => { printSliderDirty = false; }, 250); });
  slider.addEventListener("pointercancel", () => { printSliderDirty = false; });

  const minus = document.getElementById("print-speed-minus");
  const plus = document.getElementById("print-speed-plus");
  if (minus) minus.addEventListener("click", () => send({ type: "feed_override", step: -1 }));
  if (plus)  plus.addEventListener("click",  () => send({ type: "feed_override", step: 1 }));
}

// ---- Print parameters: target lift height + layer thickness → the lift's layer count.
// The operator edits physical VARIABLES; the Pi turns them into the safe end of
// the current lift — only whole-layer clean cuts of the exported order (never
// new layers). Edits stay local until "Hold to apply"; the server re-checks.
const params = { key: null, target: null, thickness: null };
const TARGET_STEP = 0.5, THICK_STEP = 0.1;

// Same rule as CurrentJob.propose_lift_end (server): layers needed =
// floor(target / thickness); the clean-cut end with the most layers not above
// that, else the earliest end still available.
function proposeLiftEnd(cands, target, t, maxZ) {
  const needed = Math.max(1, Math.floor(target / t + 1e-9));
  const ok = cands.filter(c => maxZ == null || c.layers * t <= maxZ + 1e-9);
  if (!ok.length) return { needed, choice: null, reason: "No safe whole-layer end is available for this lift." };
  const below = ok.filter(c => c.layers <= needed);
  const choice = below.length ? below.reduce((a, b) => (b.layers > a.layers ? b : a))
                              : ok.reduce((a, b) => (b.layers < a.layers ? b : a));
  let reason = null;
  if (choice.layers > needed) {
    reason = `This lift already has ${choice.layers - 1} or more layers under way — ` +
             `the earliest end still possible is after ${choice.layers} layers.`;
  } else if (choice.layers < needed) {
    if (choice.end_of_job) reason = `Only ${choice.layers} layers remain in the job.`;
    else if (ok.some(c => c.layers > choice.layers))
      reason = `No safe whole-layer end at ${needed} layers here (layers of different islands interleave) — ` +
               `nearest below: ${choice.layers}.`;
    else reason = `${needed} layers × ${t} in would exceed the machine's Z travel — limited to ${choice.layers}.`;
  }
  return { needed, choice, reason };
}

function approx(a, b) { return a != null && b != null && Math.abs(a - b) < 1e-6; }

function renderParams(s) {
  const job = s.job;
  const box = document.getElementById("lift-params");
  const lf = (job && job.progress && job.progress.lift) || {};
  box.hidden = !(job && job.kind === "building" && job.executable !== false && lf.layers);
  if (box.hidden) { params.key = null; return; }
  const adj = job.adjust && job.adjust.lift != null ? job.adjust : null;
  const appliedTarget = lf.target_lift_height, appliedT = lf.observed_layer || lf.nominal_layer;
  const key = `${job.job_id}:${adj ? adj.lift : "-"}`;
  if (params.key !== key || params.target == null || params.thickness == null) {
    params.key = key; params.target = appliedTarget; params.thickness = appliedT;     // start from the applied values
  }
  const maxZ = lf.max_z_travel;
  const tv = document.getElementById("p-target-value"), hv = document.getElementById("p-thick-value");
  tv.textContent = params.target != null ? `${fmtNum(params.target)} in` : "—";
  hv.textContent = params.thickness != null ? `${fmtNum(params.thickness)} in` : "—";
  tv.className = "param-value" + (approx(params.target, appliedTarget) ? "" : " changed");
  hv.className = "param-value" + (approx(params.thickness, appliedT) ? "" : " changed");
  document.getElementById("p-target-note").textContent =
    lf.planned_target_lift_height != null ? `planned ${fmtNum(lf.planned_target_lift_height)} in` +
    (maxZ != null ? ` · Z travel ${fmtNum(maxZ)} in` : "") : "";
  document.getElementById("p-thick-note").textContent =
    lf.nominal_layer != null ? `Slicer nominal ${fmtNum(lf.nominal_layer)} in` : "";
  document.querySelectorAll(".step-btn").forEach(b => { b.disabled = !adj; });

  const res = document.getElementById("p-result"), why = document.getElementById("p-reason");
  const applyBtn = document.getElementById("btn-apply-params");
  const k = adj ? adj.lift : job.current_lift;
  const planned = adj ? adj.candidates.find(c => c.planned) : null;
  const plannedLayers = planned ? planned.layers : lf.layers;
  if (!adj) {
    res.innerHTML = "";
    res.append(`Lift ${k + 1}: ${lf.layers} layers planned.`);
    why.textContent = "";
    applyBtn.disabled = true;
    document.getElementById("params-note").textContent = (lf.adjustable === false)
      ? "This job has no re-entry layer marks, so lift ends cannot be adjusted (re-export it from the Designer)."
      : "Lift parameters can be applied before or while a lift prints, or while it awaits its reposition.";
  } else {
    const prop = (params.target && params.thickness) ? proposeLiftEnd(adj.candidates, params.target, params.thickness, maxZ) : null;
    const c = prop && prop.choice;
    res.innerHTML = "";
    const b1 = document.createElement("b");
    b1.textContent = prop ? `${prop.needed} layers` : "—";
    res.append(`${fmtNum(params.target)} ÷ ${fmtNum(params.thickness)} in → `, b1, " needed. ");
    if (c) {
      const b2 = document.createElement("b");
      b2.textContent = c.planned ? `${c.layers} layers (unchanged)` : `${c.layers} layers`;
      res.append(`Lift ${k + 1}: planned ${plannedLayers} → `, b2,
        c.planned ? "" : `, ending after building layer ${c.end_layer + 1} (≈ ${fmtNum(c.layers * params.thickness)} in).`);
    }
    why.textContent = prop ? (prop.reason || "") : "";
    const changes = c && (!c.planned || !approx(params.target, appliedTarget) || !approx(params.thickness, appliedT));
    applyBtn.disabled = !changes;
    applyBtn.dataset.lift = String(k);
    document.getElementById("btn-apply-label").textContent =
      job.state === "awaiting_reposition" && c && !c.planned ? `Hold to continue lift ${k + 1}` : "Hold to apply";
    document.getElementById("params-note").textContent =
      "Applies to the current lift only. The Pi re-uses the exported layers in their exact order — it never adds layers.";
  }
  document.getElementById("btn-restore-lifts").disabled =
    !job.lifts_adjusted || ["running", "held", "awaiting_reposition"].includes(job.state);
}

// ---- Machine / Printer: THIS controller's profile (authoritative) and job preflight
function renderPrinter(s) {
  const m = s.machine || {}, p = m.profile || {};
  const chip = document.getElementById("printer-chip");
  const unset = !(p.x_extent && p.y_extent);
  chip.textContent = `Printer: ${p.name || "—"}${unset ? " (extents not set)" : ""}`;
  chip.className = "printer-chip" + (unset ? " unset" : "");
  const sec = document.getElementById("printer-section");
  if (sec.hidden) return;
  const set = (id, v) => { const f = document.getElementById(id); if (document.activeElement !== f) f.value = v == null ? "" : v; };
  set("pp-name", p.name); set("pp-x", p.x_extent); set("pp-y", p.y_extent);
  set("pp-zmax", p.max_build_z); set("pp-zframe", p.z_travel_per_frame);
  const busy = !!(s.job && ["running", "held", "awaiting_reposition"].includes(s.job.state));
  document.getElementById("printer-save").disabled = busy;
  document.getElementById("printer-note").textContent = m.problem ||
    (busy ? "The printer profile cannot change while a job is running, held or awaiting a reposition." : "");
}

function renderPreflight(s) {
  const job = s.job, box = document.getElementById("job-preflight");
  const pf = job && job.preflight;
  box.hidden = !pf || job.executable === false;
  if (box.hidden) return;
  const fails = pf.checks.filter(c => c.status === "fail"), warns = pf.checks.filter(c => c.status === "warn");
  const unchecked = pf.checks.filter(c => c.status === "unchecked");
  box.className = "job-preflight " + (pf.blocking ? "block" : (fails.length || warns.length || pf.notes.length ? "warn" : "ok"));
  document.getElementById("preflight-title").textContent = pf.blocking
    ? `Incompatible with printer "${pf.profile_name}" — cannot start`
    : fails.length ? `Exceeds printer "${pf.profile_name}" (external G-code: informational)`
    : `Fits printer "${pf.profile_name}"` + (warns.length ? " — with warnings" : "") +
      (unchecked.length ? ` · not checked: ${unchecked.map(c => c.id).join(", ")}` : "");
  const list = document.getElementById("preflight-list");
  list.innerHTML = "";
  const add = (cls, text) => { const li = document.createElement("li"); li.className = cls; li.textContent = text; list.appendChild(li); };
  fails.forEach(c => add("fail", c.message));
  warns.forEach(c => add("warn", c.message));
  pf.notes.forEach(n => add("note", n));
}

function attachPrinter() {
  const sec = document.getElementById("printer-section");
  const toggle = () => { sec.hidden = !sec.hidden; renderPrinter(lastState); };
  document.getElementById("printer-chip").addEventListener("click", toggle);
  document.getElementById("printer-close").addEventListener("click", toggle);
  document.getElementById("printer-save").addEventListener("click", () => {
    const num = (id) => { const v = document.getElementById(id).value.trim(); return v === "" ? null : parseFloat(v); };
    const cur = (lastState.machine && lastState.machine.profile) || {};
    send({ type: "set_machine_profile", profile: { ...cur, name: document.getElementById("pp-name").value,
      x_extent: num("pp-x"), y_extent: num("pp-y"), max_build_z: num("pp-zmax"), z_travel_per_frame: num("pp-zframe"),
      units: "in" } });
  });
}

function stepParam(name, delta) {
  const lf = (lastState.job && lastState.job.progress && lastState.job.progress.lift) || {};
  const maxZ = lf.max_z_travel || 1000;
  const round = (v) => Math.round(v * 1000) / 1000;
  if (name === "target") params.target = round(Math.min(maxZ, Math.max(TARGET_STEP, (params.target || 0) + delta)));
  else params.thickness = round(Math.min(24, Math.max(THICK_STEP, (params.thickness || 0) + delta)));
  renderParams(lastState);
}

// ---- Diagnostics (Advanced): GRBL state, alarms / errors, read-only queries
function renderDiagnostics(s) {
  if (document.getElementById("console-body").style.display === "none") return;
  const g = s.grbl || {}, d = s.diag || {};
  kvGrid(document.getElementById("diag-status"), [
    ["Connection", s.connected ? "connected" : "disconnected"],
    ["GRBL", `${s.grbl_version || "?"} · ${s.grbl_state}`],
    ["Feed override", g.overrides ? `${g.overrides.feed}% (rapid ${g.overrides.rapid}%)` : "not reported yet"],
    ["Input pins", g.pins_named && g.pins_named.length ? g.pins_named.join(", ") : "none triggered"],
    ["Planner / RX buffer", g.buffer ? `${g.buffer[0]} blocks / ${g.buffer[1]} bytes free` : "not reported ($10)"],
    ["Work offset (WCO)", g.wco ? `${g.wco[0]}, ${g.wco[1]} ${g.report_units || ""}` : "—"],
    ["Report units ($13)", g.report_units || "unknown (read settings)"],
  ]);
  const ev = document.getElementById("diag-events");
  ev.innerHTML = "";
  (d.events || []).slice().reverse().forEach(e => {
    const row = document.createElement("div");
    row.className = "ev-" + e.kind;
    row.textContent = `${new Date(e.t * 1000).toLocaleTimeString()} ${e.text}${e.detail ? " — " + e.detail : ""}`;
    ev.appendChild(row);
  });
  if (!(d.events || []).length) ev.textContent = "none";
  const bi = d.build_info || {};
  document.getElementById("diag-build").textContent = Object.keys(bi).length
    ? Object.entries(bi).map(([k, v]) => `${k}: ${v}`).join("\n") : "";
  document.getElementById("diag-parser").textContent = d.parser_state ? "Parser state: " + d.parser_state : "";
  document.getElementById("diag-offsets").textContent = d.offsets && Object.keys(d.offsets).length
    ? Object.entries(d.offsets).map(([k, v]) => `${k}: ${v}`).join("\n") : "";
  const tbl = document.getElementById("diag-settings");
  tbl.innerHTML = "";
  (d.settings || []).forEach(r => {
    const tr = document.createElement("tr");
    [`$${r.n}`, r.value, r.name].forEach(t => { const td = document.createElement("td"); td.textContent = t; tr.appendChild(td); });
    tbl.appendChild(tr);
  });
  const idle = s.connected && s.grbl_state === "Idle" && !(s.job && ["running", "held"].includes(s.job.state));
  document.querySelectorAll("[data-read]").forEach(b => { b.disabled = !idle; });
}

function attachFeatureControls() {
  document.querySelectorAll(".btn-ovr").forEach(btn => {
    btn.addEventListener("click", () => {
      const step = btn.dataset.step === "reset" ? "reset" : parseInt(btn.dataset.step, 10);
      send({ type: "feed_override", step });
    });
  });
  attachHoldButton(document.getElementById("btn-outline"), 1000, () => send({ type: "outline" }));
  // the values shown when the hold BEGAN are applied; the server re-checks them
  attachHoldButton(document.getElementById("btn-apply-params"), 1500,
    (v) => send({ type: "apply_lift_parameters", lift: v.lift, target: v.target, thickness: v.thickness }),
    () => ({ lift: parseInt(document.getElementById("btn-apply-params").dataset.lift, 10),
             target: params.target, thickness: params.thickness }));
  document.querySelectorAll(".step-btn").forEach(b =>
    b.addEventListener("click", () => stepParam(b.dataset.param, parseFloat(b.dataset.delta))));
  document.getElementById("btn-restore-lifts").addEventListener("click", () => send({ type: "restore_planned_lifts" }));
  document.querySelectorAll("[data-read]").forEach(b =>
    b.addEventListener("click", () => send({ type: "read_grbl", what: b.dataset.read })));
}

// ---- REMOTE STOP (Kaleinavi CH1): latched operational stop — never called an E-stop
function renderRemoteStop(s) {
  const rs = s.remote_stop || {};
  const panel = document.getElementById("rstop-panel");
  document.getElementById("rstop-badge").hidden = !rs.latched;
  panel.hidden = !rs.latched;
  if (!rs.latched) return;
  const when = rs.since ? new Date(rs.since * 1000).toLocaleTimeString() : "";
  document.getElementById("rstop-text").textContent = `Latched at ${when} — ${rs.cause || ""}.`;
  const ul = document.getElementById("rstop-actions");
  ul.innerHTML = "";
  (rs.actions || []).forEach(a => { const li = document.createElement("li"); li.textContent = a; ul.appendChild(li); });
  const ch = ((s.hardware || {}).remote || []).find(c => c.key === "remote_stop");
  const held = ch && ch.state !== "released";
  document.getElementById("btn-rstop-clear").disabled = !!held;
  document.getElementById("rstop-blocked").textContent = held
    ? "The REMOTE STOP input still reads " + (ch.state === "pressed" ? "PRESSED" : "unknown") + " — release the remote button first."
    : "";
}

// ---- Manual Z: one dead-man controller on the server (same as the remote's Z UP / Z DOWN)
let zSpeedSliderDirty = false;

function renderZ(s) {
  const z = (s.hardware || {}).z;
  const warn = document.getElementById("z-warning");
  const remoteWarn = document.getElementById("z-remote-warning");
  const status = document.getElementById("z-status");
  const reason = !z ? "No Z hardware." : (z.blocked || z.refusal);
  warn.textContent = reason || "";
  warn.classList.toggle("visible", !!reason && !(z && z.blocked));

  // Diagnostic: if the handheld's Z UP or Z DOWN channel is not armed
  // (closed-at-startup — the service refuses to act on a channel first
  // seen pressed), say so prominently near the Z controls. The operator
  // likely won't open the Hardware / Commissioning panel to diagnose a
  // dead remote button.
  if (remoteWarn) {
    const remote = (s.hardware || {}).remote || [];
    const zUp   = remote.find(c => c.key === "z_up");
    const zDown = remote.find(c => c.key === "z_down");
    const unarmed = [];
    if (zUp && !zUp.armed && zUp.held_at_startup)
      unarmed.push(`CH${zUp.channel} Z UP (handheld button looks stuck pressed — release it to arm)`);
    if (zDown && !zDown.armed && zDown.held_at_startup)
      unarmed.push(`CH${zDown.channel} Z DOWN (handheld button looks stuck pressed — release it to arm)`);
    if (unarmed.length) {
      remoteWarn.textContent = "Handheld remote: " + unarmed.join("; ");
      remoteWarn.classList.add("visible");
    } else {
      remoteWarn.textContent = "";
      remoteWarn.classList.remove("visible");
    }
  }

  const usable = !!(z && z.available && !z.refusal);
  document.querySelectorAll(".z-btn").forEach(b => { b.disabled = !usable && !zActive; });

  // Z speed slider (operator-adjustable PWM duty). The slider sends
  // percent-of-max to the server; the server clamps + persists and
  // reports back in `z.duty_config`.
  const dutyPct = z && typeof z.duty_config === "number" ? Math.round(z.duty_config * 100) : null;
  const label = document.getElementById("z-speed-value");
  const slider = document.getElementById("z-speed-slider");
  const minus = document.getElementById("z-speed-minus");
  const plus = document.getElementById("z-speed-plus");
  if (label && slider && minus && plus) {
    const canChange = usable && (!z || z.command === "STOP");
    slider.disabled = !canChange;
    minus.disabled  = !canChange;
    plus.disabled   = !canChange;
    if (dutyPct == null) {
      label.textContent = "—";
    } else {
      label.textContent = String(dutyPct);
      if (!zSpeedSliderDirty) slider.value = String(dutyPct);
    }
  }

  if (!z) { status.textContent = "—"; status.className = "z-status"; return; }
  const moving = z.command !== "STOP";
  status.className = "z-status" + (moving ? " moving" : "");
  status.textContent = moving
    ? `Z ${z.command} — ${z.source} · duty ${Math.round(z.duty_config * 100)} %`
    : (z.blocked ? z.blocked : `Z STOP · fixed duty ${z.duty_config > 0 ? Math.round(z.duty_config * 100) + " %" : "not set"}`);
}

function sendZDuty(pct) {
  const clamped = Math.max(0, Math.min(100, Math.round(pct / 5) * 5));
  send({ type: "set_z_duty", duty: clamped / 100 });
}

function attachZSpeedSlider() {
  const slider = document.getElementById("z-speed-slider");
  if (!slider) return;
  slider.addEventListener("input", () => {
    zSpeedSliderDirty = true;
    const label = document.getElementById("z-speed-value");
    if (label) label.textContent = slider.value;
  });
  slider.addEventListener("change", () => {
    sendZDuty(parseInt(slider.value, 10));
    setTimeout(() => { zSpeedSliderDirty = false; }, 300);
  });
  slider.addEventListener("pointercancel", () => { zSpeedSliderDirty = false; });

  const minus = document.getElementById("z-speed-minus");
  const plus  = document.getElementById("z-speed-plus");
  const step = (delta) => () => {
    const z = (lastState && lastState.hardware && lastState.hardware.z) || null;
    const current = z && typeof z.duty_config === "number" ? Math.round(z.duty_config * 100) : 50;
    sendZDuty(current + delta);
  };
  if (minus) minus.addEventListener("click", step(-5));
  if (plus)  plus.addEventListener("click",  step(5));
}

// ---- Hardware / commissioning panel
function renderHardware(s) {
  if (document.getElementById("hw-body").style.display === "none") return;
  const hw = s.hardware || {};
  const lastRemoteT = hw.remote_last_activity_wall_t;
  let lastRemoteText = "never since this boot — the handheld transmitter may be asleep";
  if (lastRemoteT) {
    const nowSec = Date.now() / 1000;
    const dt = Math.max(0, nowSec - lastRemoteT);
    if (dt < 60) lastRemoteText = `${Math.round(dt)} s ago`;
    else if (dt < 3600) lastRemoteText = `${Math.round(dt / 60)} min ago`;
    else lastRemoteText = `${(dt / 3600).toFixed(1)} h ago — handheld may be asleep (press any handheld button to wake)`;
  }
  kvGrid(document.getElementById("hw-status"), [
    ["GPIO backend", hw.backend + (hw.gpio ? "" : " (no GPIO: remote inputs not read)")
                     + (hw.backend_error ? " — " + hw.backend_error : "")],
    ["Poll loop", hw.loop_error ? "ERROR: " + hw.loop_error : "running"],
    ["Debounce", hw.debounce_ms != null ? hw.debounce_ms + " ms" : "—"],
    ["Handheld last activity", lastRemoteText],
    ["GRBL", s.connected ? `connected · ${s.grbl_version || "version not yet reported"} · ${s.grbl_state}`
                         : "disconnected"],
    ["GRBL build ($I)", (s.diag && s.diag.build_info && s.diag.build_info.VER) || "not read (Advanced → Build info)"],
    ["REMOTE STOP", latched(s) ? "LATCHED" : "clear"],
  ]);
  const tbl = document.getElementById("hw-remote");
  tbl.innerHTML = "";
  const head = document.createElement("tr");
  ["CH", "Function", "GPIO (BCM)", "Pin", "Level", "Software", "Armed"].forEach(t => {
    const td = document.createElement("td"); td.textContent = t; td.className = "k"; head.appendChild(td);
  });
  tbl.appendChild(head);
  (hw.remote || []).forEach(c => {
    const tr = document.createElement("tr");
    if (c.state === "pressed") tr.className = "pressed";
    [`CH${c.channel}`, c.label, `GPIO${c.bcm}`, `${c.physical}`, c.level || "—", c.state.toUpperCase(),
     c.armed ? "yes" : (c.held_at_startup ? "no — held at startup, release to arm" : "no")
    ].forEach(t => { const td = document.createElement("td"); td.textContent = t; tr.appendChild(td); });
    tbl.appendChild(tr);
  });
  const z = hw.z, p = hw.z_pins || {};
  kvGrid(document.getElementById("hw-z"), z ? [
    ["Command", z.command + (z.source ? ` (${z.source})` : "")],
    ["PWM output", `${Math.round(z.pwm_duty * 100)} % on GPIO${p.pwm_bcm} (pin ${p.pwm_physical})`],
    ["DIR output", `${z.dir_level} on GPIO${p.dir_bcm} (pin ${p.dir_physical})${z.dir_invert ? " · inverted" : ""}`],
    ["Configured duty", z.duty_config > 0 ? Math.round(z.duty_config * 100) + " % (fixed)" : "0 — manual Z disabled"],
    ["Dead-man / max run", `${z.timeout_s} s / ${z.max_run_s} s`],
    ["Blocked / refused", z.blocked || z.refusal || "—"],
    ["Fault", z.fault || "none"],
    ["Last stop", z.last_stop || "—"],
  ] : [["Z", "unavailable"]]);
  const ev = document.getElementById("hw-events");
  ev.innerHTML = "";
  (hw.events || []).slice().reverse().forEach(e => {
    const row = document.createElement("div");
    row.className = "ev-" + e.kind;
    row.textContent = `${new Date(e.t * 1000).toLocaleTimeString()} ${e.text}`;
    ev.appendChild(row);
  });
  if (!(hw.events || []).length) ev.textContent = "none";
}

// ---- Live 2D toolpath view: drawn only from the loaded job's G-code (GET /api/job/path)
const view = { jobId: null, path: null, scale: 1, cx: 0, cy: 0, fitted: false, drawnAt: 0, drag: null };

async function loadPath(jobId) {
  view.jobId = jobId; view.path = null; view.fitted = false;
  try {
    const r = await fetch("/api/job/path");
    const data = await r.json();
    if (data.job_id === jobId) view.path = data;
  } catch (e) { /* the next state broadcast retries */ view.jobId = null; }
}

function viewRange(s) {
  const job = s.job;
  if (!job || document.getElementById("view-all").checked) return [0, Infinity];
  const L = job.lifts[Math.min(job.current_lift, job.lifts.length - 1)];
  return L && L.start_line != null ? [L.start_line, L.end_line] : [0, Infinity];
}

function fitView(s) {
  const P = view.path, canvas = document.getElementById("path-canvas");
  if (!P || !P.x.length) return;
  const [a, b] = viewRange(s);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < P.x.length; i++) {
    if (P.line[i] < a || P.line[i] >= b) continue;
    x0 = Math.min(x0, P.x[i]); x1 = Math.max(x1, P.x[i]); y0 = Math.min(y0, P.y[i]); y1 = Math.max(y1, P.y[i]);
  }
  if (!isFinite(x0)) return;
  const env = s.machine && s.machine.envelope;            // the printer envelope is framed too
  if (env) { x0 = Math.min(x0, env.min_x); y0 = Math.min(y0, env.min_y); x1 = Math.max(x1, env.max_x); y1 = Math.max(y1, env.max_y); }
  const w = Math.max(x1 - x0, 1), h = Math.max(y1 - y0, 1);
  view.scale = 0.9 * Math.min(canvas.width / w, canvas.height / h);
  view.cx = (x0 + x1) / 2; view.cy = (y0 + y1) / 2; view.fitted = true;
}

function renderView(s, force) {
  const job = s.job;
  const sec = document.getElementById("view-section");
  sec.hidden = !(job && job.executable !== false);
  if (sec.hidden) return;
  if (job.job_id !== view.jobId) { loadPath(job.job_id).then(() => renderView(lastState, true)); return; }
  const now = Date.now();
  if (!force && now - view.drawnAt < 400) return;          // ~2.5 Hz: light on the Pi
  view.drawnAt = now;
  const P = view.path, canvas = document.getElementById("path-canvas"), ctx = canvas.getContext("2d");
  ctx.fillStyle = "#0b0b0b"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!P) return;
  if (!view.fitted) fitView(s);
  const [a, b] = viewRange(s);
  const done = job.current_line;
  const X = (x) => canvas.width / 2 + (x - view.cx) * view.scale;
  const Y = (y) => canvas.height / 2 - (y - view.cy) * view.scale;
  // one pass per style: remaining, travel, printed (segments end at point i)
  const styles = [
    { test: (i) => P.kind[i] === 0 && P.line[i] >= done, color: "#555", width: 2, dash: [] },
    { test: (i) => P.kind[i] === 1, color: "#a855f7", width: 1, dash: [6, 6] },
    { test: (i) => P.kind[i] === 0 && P.line[i] < done, color: "#4ade80", width: 2.5, dash: [] },
  ];
  for (const st of styles) {
    ctx.beginPath(); ctx.strokeStyle = st.color; ctx.lineWidth = st.width; ctx.setLineDash(st.dash);
    for (let i = 1; i < P.x.length; i++) {
      if (P.line[i] < a || P.line[i] >= b || P.line[i - 1] < a || !st.test(i)) continue;
      ctx.moveTo(X(P.x[i - 1]), Y(P.y[i - 1])); ctx.lineTo(X(P.x[i]), Y(P.y[i]));
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);
  // THIS printer's physical X × Y envelope (from the print origin) — a reference;
  // a job reaching outside it is outlined in red. The path itself is never changed.
  const env = s.machine && s.machine.envelope;
  if (env) {
    ctx.strokeStyle = "rgba(170,176,186,0.7)"; ctx.lineWidth = 1; ctx.setLineDash([4, 6]);
    ctx.strokeRect(X(env.min_x), Y(env.max_y), X(env.max_x) - X(env.min_x), Y(env.min_y) - Y(env.max_y));
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(170,176,186,0.85)"; ctx.font = "11px monospace";
    ctx.fillText(`printer ${fmtNum(env.max_x)}×${fmtNum(env.max_y)} in`, X(env.max_x) - 120, Y(env.max_y) - 6);
    const jb = job.preflight && job.preflight.requirements && job.preflight.requirements.xy_bounds;
    if (jb && (jb.min_x < env.min_x - 1e-4 || jb.min_y < env.min_y - 1e-4 || jb.max_x > env.max_x + 1e-4 || jb.max_y > env.max_y + 1e-4)) {
      ctx.strokeStyle = "#f87171"; ctx.lineWidth = 2; ctx.setLineDash([8, 5]);
      ctx.strokeRect(X(jb.min_x), Y(jb.max_y), X(jb.max_x) - X(jb.min_x), Y(jb.min_y) - Y(jb.max_y));
      ctx.setLineDash([]);
      ctx.fillStyle = "#f87171"; ctx.font = "bold 12px monospace";
      ctx.fillText("JOB OUTSIDE THE PRINTER ENVELOPE", X(jb.min_x) + 4, Y(jb.max_y) - 8);
    }
  }
  // work origin — blue crosshair
  ctx.strokeStyle = "#60a5fa"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(X(0) - 8, Y(0)); ctx.lineTo(X(0) + 8, Y(0)); ctx.moveTo(X(0), Y(0) - 8); ctx.lineTo(X(0), Y(0) + 8); ctx.stroke();
  ctx.fillStyle = "#60a5fa"; ctx.font = "11px monospace";
  ctx.fillText("origin (0, 0)", X(0) + 10, Y(0) + 4);

  // Start point of the toolpath (first commanded motion). Amber ringed
  // marker + label. Added after the twelfth-pass incident so the operator
  // can see where the first move will go BEFORE pressing Start.
  const fm = job.first_motion;
  if (fm && typeof fm.x_in === "number" && typeof fm.y_in === "number") {
    const sx = X(fm.x_in), sy = Y(fm.y_in);
    ctx.strokeStyle = "#fbbf24"; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(sx, sy, 10, 0, 2 * Math.PI); ctx.stroke();
    ctx.beginPath(); ctx.arc(sx, sy, 4, 0, 2 * Math.PI); ctx.fillStyle = "#fbbf24"; ctx.fill();
    ctx.fillStyle = "#fbbf24"; ctx.font = "bold 11px monospace";
    ctx.fillText(`start (${fm.x_in.toFixed(2)}, ${fm.y_in.toFixed(2)})`, sx + 14, sy + 4);
  }

  // nozzle (work coordinates, inches) — only when the origin is established
  const st = s.setup || {};
  if (st.work_x != null) {
    ctx.fillStyle = "#f87171"; ctx.beginPath(); ctx.arc(X(st.work_x), Y(st.work_y), 6, 0, 2 * Math.PI); ctx.fill();
  }
  document.getElementById("view-note").textContent =
    (st.work_x == null ? "Nozzle position appears once the print origin is set. " : "") +
    (P.decimated ? `Simplified view (${P.points} moves). ` : "") +
    "Drawn from the loaded job's G-code only (work coordinates, inches).";
}

function attachView() {
  const canvas = document.getElementById("path-canvas");
  const zoom = (f) => { view.scale *= f; renderView(lastState, true); };
  document.getElementById("view-zoom-in").addEventListener("click", () => zoom(1.4));
  document.getElementById("view-zoom-out").addEventListener("click", () => zoom(1 / 1.4));
  document.getElementById("view-fit").addEventListener("click", () => { view.fitted = false; renderView(lastState, true); });
  document.getElementById("view-all").addEventListener("change", () => { view.fitted = false; renderView(lastState, true); });
  canvas.addEventListener("pointerdown", (e) => { view.drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!view.drag) return;
    const k = canvas.width / canvas.getBoundingClientRect().width;
    view.cx -= (e.clientX - view.drag.x) * k / view.scale;
    view.cy += (e.clientY - view.drag.y) * k / view.scale;
    view.drag = { x: e.clientX, y: e.clientY };
    renderView(lastState, true);
  });
  const end = () => { view.drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
}

function fmtDuration(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ------------------------------------------------------------------ //
// Held-input (jog + Z) release safety                                 //
// --------------------------------------------------------------------
// Design (eighth-pass hardening, 2026-10-25):
//
//  * One Pointer Events implementation for BOTH jog and Z buttons.
//    Pointer events unify mouse + touch + pen; `setPointerCapture`
//    on pointerdown guarantees the SAME button receives pointerup /
//    pointercancel / lostpointercapture even if the finger / cursor
//    is dragged off, over another element, or past the window edge.
//
//  * Global `visibilitychange`, `blur`, `pagehide` handlers call
//    `stopAllHeldInputs()`. If the tab is backgrounded or the
//    browser loses focus, both held jog AND held Z are released
//    immediately — the UI never relies on the server's dead-man
//    timeout as the primary stop mechanism.
//
//  * `window.onbeforeunload` is implicit: closing the tab closes
//    the WS, the server's websocket_endpoint `finally` releases
//    every held source for that WS.
//
//  * Client-side belt-and-braces: an INPUT_MAX_HOLD_MS timer stops
//    the input even if every event path above fails. The number
//    matches the server's `Z_MAX_RUN_S` so client and server agree
//    on the ceiling.
//
// The server-side `Z_TIMEOUT_S=0.3` dead-man and `Z_MAX_RUN_S=30`
// run ceiling are the ultimate safety net below all of this; they
// remain unchanged.
// ------------------------------------------------------------------ //

// Hard client-side ceiling for any held input. Matches server
// Z_MAX_RUN_S / jog usage patterns. If this timer fires something
// has gone wrong — log and release.
const INPUT_MAX_HOLD_MS = 30 * 1000;

let jogHeartbeatInterval = null;
let jogActive = false;
let jogMaxHoldTimer = null;

function startJog(axis, dir) {
  if (jogActive) stopJog();
  jogActive = true;
  send({ type: "jog_start", axis, direction: dir });
  // Send heartbeat every 100 ms to keep jog alive on the server
  jogHeartbeatInterval = setInterval(() => {
    send({ type: "jog_heartbeat" });
  }, 100);
  clearTimeout(jogMaxHoldTimer);
  jogMaxHoldTimer = setTimeout(() => {
    if (jogActive) {
      console.warn("Jog client max-hold ceiling reached — stopping");
      stopJog();
    }
  }, INPUT_MAX_HOLD_MS);
}

function stopJog() {
  if (!jogActive) return;
  jogActive = false;
  clearInterval(jogHeartbeatInterval);
  jogHeartbeatInterval = null;
  clearTimeout(jogMaxHoldTimer);
  jogMaxHoldTimer = null;
  send({ type: "jog_stop" });
}

let zHeartbeatInterval = null;
let zActive = false;
let zMaxHoldTimer = null;

function startZ(direction) {
  if (zActive) stopZ();
  zActive = true;
  send({ type: "z_start", direction });
  zHeartbeatInterval = setInterval(() => send({ type: "z_heartbeat" }), 100);
  clearTimeout(zMaxHoldTimer);
  zMaxHoldTimer = setTimeout(() => {
    if (zActive) {
      console.warn("Z client max-hold ceiling reached — stopping");
      stopZ();
    }
  }, INPUT_MAX_HOLD_MS);
}

function stopZ() {
  if (!zActive) return;
  zActive = false;
  clearInterval(zHeartbeatInterval);
  zHeartbeatInterval = null;
  clearTimeout(zMaxHoldTimer);
  zMaxHoldTimer = null;
  send({ type: "z_stop" });
}

// Any time the browser loses visibility / focus / is being
// backgrounded, stop every held input. Belt-and-braces for the
// case where OS-level focus loss means no pointerup fires.
function stopAllHeldInputs() {
  if (jogActive) stopJog();
  if (zActive) stopZ();
}

// Attach once. Idempotent — safe to call from the DOMContentLoaded block
// AND from any future test harness.
let _globalReleaseHandlersAttached = false;
function attachGlobalReleaseHandlers() {
  if (_globalReleaseHandlersAttached) return;
  _globalReleaseHandlersAttached = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") stopAllHeldInputs();
  });
  window.addEventListener("blur", stopAllHeldInputs);
  window.addEventListener("pagehide", stopAllHeldInputs);
  window.addEventListener("beforeunload", stopAllHeldInputs);
}

// Shared Pointer Events wiring for a press-and-hold button that
// starts/stops some held action. Callers provide `onStart()` and
// `onStop()`. Guarantees release on EVERY release path: pointerup
// (same spot), pointercancel (OS cancel), lostpointercapture (DOM
// re-render / capture released), pointerleave (cursor exits while
// not captured — rare fallback). Also calls `onStop()` if the
// button becomes disabled while pressed.

// Module-level list of every held button's force-release function. On
// WS disconnect / reconnect, call `forceReleaseAllHeldButtons()` so no
// stale pointerId / "pressing" state can block the next deliberate press.
const _heldButtonResetters = [];
function forceReleaseAllHeldButtons() {
  for (const r of _heldButtonResetters) {
    try { r(); } catch (_) {}
  }
}

function attachHeldButton(btn, onStart, onStop, {skipFn} = {}) {
  let pointerId = null;

  const begin = (e) => {
    if (btn.disabled) return;
    if (skipFn && skipFn()) return;
    if (pointerId !== null) return;
    pointerId = e.pointerId;
    try { btn.setPointerCapture(pointerId); } catch (_) { /* older browsers */ }
    btn.classList.add("pressing");
    onStart();
  };

  const end = (e) => {
    if (e && e.pointerId != null && pointerId != null && e.pointerId !== pointerId) return;
    if (pointerId !== null) {
      try { btn.releasePointerCapture(pointerId); } catch (_) {}
    }
    pointerId = null;
    btn.classList.remove("pressing");
    onStop();
  };

  // Force-release path for the WS disconnect / reconnect handler.
  // Resets local pointer state WITHOUT re-sending a stop (the server-
  // side cleanup already released any held source when the WS died;
  // we just need the UI to be clean so the next press works).
  const forceReset = () => {
    if (pointerId !== null) {
      try { btn.releasePointerCapture(pointerId); } catch (_) {}
      pointerId = null;
    }
    btn.classList.remove("pressing");
  };
  _heldButtonResetters.push(forceReset);

  btn.addEventListener("pointerdown", begin);
  btn.addEventListener("pointerup", end);
  btn.addEventListener("pointercancel", end);
  btn.addEventListener("lostpointercapture", end);
  // Pointerleave is a last-resort for the case where no pointer
  // capture was granted (older browsers). It is ignored if we
  // released capture ourselves on pointerup.
  btn.addEventListener("pointerleave", (e) => {
    if (pointerId !== null) end(e);
  });
  // If the button is disabled mid-press, no further pointer events
  // reach it. Watch for disabled -> true via MutationObserver so we
  // can stop immediately.
  const mo = new MutationObserver(() => {
    if (btn.disabled && pointerId !== null) end({pointerId});
  });
  mo.observe(btn, {attributes: true, attributeFilter: ["disabled"]});
  return () => mo.disconnect();
}

function attachJogButton(btn) {
  const axis = btn.dataset.axis;
  const dir  = parseInt(btn.dataset.dir, 10);
  // Belt-and-braces: Z buttons used to share the .jog-btn class and were
  // wired here by mistake, firing a jog_start with axis=null direction=null
  // (JSON converts NaN to null) that threw TypeError server-side. The
  // Z buttons no longer carry .jog-btn, and if any new unlabelled button
  // slips in with the class, skip it rather than send a malformed message.
  if (!axis || !["X", "Y"].includes(axis) || !Number.isFinite(dir)) {
    return;
  }
  attachHeldButton(btn, () => startJog(axis, dir), () => stopJog());
}

function attachZButton(btn) {
  const dir = btn.dataset.zdir;
  if (!dir || !["up", "down"].includes(dir)) return;
  attachHeldButton(btn, () => startZ(dir), () => stopZ());
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

const SET_ORIGIN_HOLD_MS = 1500;
const PRIME_HOLD_MS = 1000;

function attachSetupButtons() {
  attachHoldButton(document.getElementById("btn-set-origin"), SET_ORIGIN_HOLD_MS,
    () => send({ type: "set_origin" }));
  attachHoldButton(document.getElementById("btn-prime"), PRIME_HOLD_MS,
    () => send({ type: "prime" }));
  document.getElementById("btn-set-prime").addEventListener("click", () => send({ type: "set_prime" }));
}

let cancelConfirmHold = () => {};

// STOP button: SINGLE-TAP, IMMEDIATE. No hold-to-confirm, no dialog.
// Fires on `pointerdown` (not `click`) so the operator doesn't have to
// wait for pointerup → click synthesis on touchscreens. The physical
// hardwired E-stop remains the primary emergency device.
function attachStopButton() {
  const btn = document.getElementById("btn-stop");
  if (!btn) return;
  const fire = (e) => {
    if (e) e.preventDefault();
    if (btn.disabled) return;
    btn.classList.add("pressing");
    setTimeout(() => btn.classList.remove("pressing"), 150);
    send({ type: "stop" });
  };
  btn.addEventListener("pointerdown", fire);
  // Keyboard accessibility (Space / Enter when focused) also fires once.
  btn.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) fire(e);
  });
}

const RSTOP_CLEAR_HOLD_MS = 2000;

function attachRemoteStopClear() {
  attachHoldButton(document.getElementById("btn-rstop-clear"), RSTOP_CLEAR_HOLD_MS,
    () => send({ type: "remote_stop_clear" }));
}

// ---- Exit kiosk (hold 1.5 s) — closes Chromium kiosk; backend keeps running
const EXIT_KIOSK_HOLD_MS = 1500;

// ------------------------------------------------------------------ //
// Industrial touchscreen scrollbar — only wires up when the custom     //
// scrollbar DOM is present (it's CSS-hidden on desktop; the elements   //
// exist but their event handlers are harmless there).                  //
// ------------------------------------------------------------------ //

function attachTouchScrollbar() {
  const main  = document.getElementById("main-content");
  const bar   = document.getElementById("touch-scrollbar");
  const track = document.getElementById("ts-track");
  const thumb = document.getElementById("ts-thumb");
  const up    = document.getElementById("ts-up");
  const down  = document.getElementById("ts-down");
  if (!main || !bar || !track || !thumb || !up || !down) return;

  // The minimum thumb height so a finger can always grab it, even on a
  // page with very long content. Must match the CSS min-height.
  const MIN_THUMB_PX = 80;

  // Sync the thumb's size + position to the current scroll state. Called on
  // scroll, resize, and any DOM mutation inside #main-content.
  function sync() {
    const trackH = track.clientHeight;
    const scrollH = main.scrollHeight;
    const clientH = main.clientHeight;
    if (scrollH <= clientH || trackH <= 0) {
      // Content fits — no scrolling needed. Show a disabled full-height thumb
      // so the control still reads as "scrollbar" rather than vanishing.
      thumb.style.height = trackH + "px";
      thumb.style.top = "0";
      thumb.classList.add("disabled");
      return;
    }
    thumb.classList.remove("disabled");
    const rawThumbH = trackH * (clientH / scrollH);
    const thumbH = Math.max(MIN_THUMB_PX, Math.round(rawThumbH));
    const maxThumbTop = Math.max(0, trackH - thumbH);
    const scrollRatio = main.scrollTop / (scrollH - clientH);
    const thumbTop = Math.round(scrollRatio * maxThumbTop);
    thumb.style.height = thumbH + "px";
    thumb.style.top = thumbTop + "px";
  }

  main.addEventListener("scroll", sync, { passive: true });
  window.addEventListener("resize", sync);
  // Sections appear / disappear (printer settings, hw panel, console) —
  // observe mutations so the thumb size tracks the real content height.
  try {
    const mo = new MutationObserver(sync);
    mo.observe(main, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ["hidden", "style", "class"],
    });
  } catch (_) { /* no MutationObserver (very old browser): sync on scroll only */ }
  // Initial sync when fonts / layout settle.
  requestAnimationFrame(sync);

  // ---- Thumb drag ---------------------------------------------------
  let dragPointerId = null;
  let dragStartClientY = 0;
  let dragStartScrollTop = 0;
  let dragTrackH = 0;
  let dragThumbH = 0;
  let dragMaxScrollTop = 0;

  thumb.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragPointerId = e.pointerId;
    dragStartClientY = e.clientY;
    dragStartScrollTop = main.scrollTop;
    dragTrackH = track.clientHeight;
    dragThumbH = thumb.offsetHeight;
    dragMaxScrollTop = Math.max(1, main.scrollHeight - main.clientHeight);
    try { thumb.setPointerCapture(e.pointerId); } catch (_) {}
    thumb.classList.add("pressing");
  });
  thumb.addEventListener("pointermove", (e) => {
    if (e.pointerId !== dragPointerId) return;
    const dy = e.clientY - dragStartClientY;
    const maxThumbTop = Math.max(1, dragTrackH - dragThumbH);
    const trackRatio = dy / maxThumbTop;
    main.scrollTop = dragStartScrollTop + trackRatio * dragMaxScrollTop;
  });
  const dragEnd = (e) => {
    if (e && e.pointerId != null && dragPointerId != null && e.pointerId !== dragPointerId) return;
    if (dragPointerId !== null) {
      try { thumb.releasePointerCapture(dragPointerId); } catch (_) {}
    }
    dragPointerId = null;
    thumb.classList.remove("pressing");
  };
  thumb.addEventListener("pointerup", dragEnd);
  thumb.addEventListener("pointercancel", dragEnd);
  thumb.addEventListener("lostpointercapture", dragEnd);

  // ---- Track tap: page up / down ------------------------------------
  // Tap above the thumb → page up; below → page down. 85 % of a viewport
  // per tap, smooth-scrolled, matches native pager behaviour.
  track.addEventListener("pointerdown", (e) => {
    if (e.target === thumb || e.target.closest(".ts-thumb")) return;
    const trackRect = track.getBoundingClientRect();
    const thumbRect = thumb.getBoundingClientRect();
    const clickY = e.clientY;
    const pageAmount = Math.max(60, Math.round(main.clientHeight * 0.85));
    const dir = clickY < thumbRect.top ? -1 : 1;
    main.scrollBy({ top: dir * pageAmount, behavior: "smooth" });
  });

  // ---- Arrow buttons: press-and-hold continuous scroll --------------
  // The first step fires immediately; a setInterval then repeats every
  // 50 ms while held. Release stops scrolling at once.
  function arrowHold(button, pxPerStep) {
    let pointerId = null;
    let timer = null;
    const start = (e) => {
      if (pointerId !== null) return;
      pointerId = e.pointerId;
      try { button.setPointerCapture(e.pointerId); } catch (_) {}
      button.classList.add("pressing");
      main.scrollBy({ top: pxPerStep });
      timer = setInterval(() => {
        main.scrollBy({ top: pxPerStep });
      }, 50);
    };
    const stop = (e) => {
      if (e && e.pointerId != null && pointerId != null && e.pointerId !== pointerId) return;
      if (pointerId !== null) {
        try { button.releasePointerCapture(pointerId); } catch (_) {}
      }
      pointerId = null;
      if (timer !== null) { clearInterval(timer); timer = null; }
      button.classList.remove("pressing");
    };
    button.addEventListener("pointerdown", start);
    button.addEventListener("pointerup", stop);
    button.addEventListener("pointercancel", stop);
    button.addEventListener("lostpointercapture", stop);
  }
  arrowHold(up,   -40);
  arrowHold(down,  40);
}

function attachExitKiosk() {
  const btn = document.getElementById("btn-exit-kiosk");
  if (!btn) return;
  attachHoldButton(btn, EXIT_KIOSK_HOLD_MS, async () => {
    try {
      const resp = await fetch("/api/exit_kiosk", { method: "POST" });
      const data = await resp.json();
      if (!data.ok) {
        showNotice(data.error || "Exit kiosk failed.");
      }
      // On success Chromium is closing right now; nothing more to do.
    } catch (e) {
      showNotice("Exit kiosk request failed: " + e);
    }
  });
}

function attachHardwarePanel() {
  const toggle = document.getElementById("hw-toggle");
  const body = document.getElementById("hw-body");
  toggle.addEventListener("click", () => {
    const open = body.style.display === "none";
    body.style.display = open ? "block" : "none";
    toggle.textContent = "Hardware / Commissioning " + (open ? "▴" : "▾");
    if (open) renderHardware(lastState);
  });
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
    toggle.textContent = "Advanced / Diagnostics / Console " + (open ? "▴" : "▾");
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
  // Global release handlers (visibilitychange / blur / pagehide /
  // beforeunload) stop every held input immediately. Attach ONCE,
  // before any button handlers that depend on them.
  attachGlobalReleaseHandlers();

  // Attach jog buttons
  document.querySelectorAll(".jog-btn").forEach(attachJogButton);

  // Attach stop and reposition-confirm press-and-hold buttons
  attachStopButton();
  attachConfirmButton();
  attachSetupButtons();
  attachFeatureControls();
  document.querySelectorAll(".z-btn").forEach(attachZButton);
  attachRemoteStopClear();
  attachHardwarePanel();
  attachExitKiosk();
  attachView();
  attachPrinter();
  attachJogSpeedSlider();
  attachPrintSpeedSlider();
  attachZSpeedSlider();
  attachTouchScrollbar();

  // Attach other controls
  attachControls();

  // Attach file upload
  attachFileUpload();

  // Attach console
  attachConsole();

  // Start WebSocket
  connectWS();
});
