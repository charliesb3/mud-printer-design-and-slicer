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
}

function renderJogSection(s) {
  const warn = document.getElementById("jog-warning");
  const btns = document.querySelectorAll(".jog-btn");
  const awaiting = !!(s.job && s.job.state === "awaiting_reposition");
  const canJog = s.connected
    && s.jog_enabled
    && ["Idle", "Jog"].includes(s.grbl_state)
    && (!jobActive(s) || awaiting)           // jog / re-zero allowed during a frame reposition
    && !(s.setup && s.setup.moving);        // the server applies the same rules

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

// ---- Speeds + live feed override (GRBL real-time bytes; value read back from GRBL)
function renderSpeed(s) {
  const job = s.job;
  const feeds = job && job.machine && job.machine.feeds_in_min;
  const ov = feedOverride(s);
  const eff = (f) => (ov && ov !== 100) ? ` → ${fmtNum(f * ov / 100)} in/min at ${ov}%` : "";
  kvGrid(document.getElementById("planned-speeds"), feeds ? [
    ["Print speed (planned)", `${fmtNum(feeds.print)} in/min (${fmtNum(feeds.print / 60)} in/s)${eff(feeds.print)}`],
    ["Travel speed (planned)", `${fmtNum(feeds.travel)} in/min (${fmtNum(feeds.travel / 60)} in/s)${eff(feeds.travel)}`],
  ] : [["Planned speeds", job ? "from the G-code F words" : "load a job"]]);
  const val = document.getElementById("ovr-value");
  val.textContent = ov != null ? ov + "%" : (s.connected ? "100%?" : "—");
  val.className = "override-value" + (ov != null && ov !== 100 ? " changed" : "");
  const pos = ov != null ? (ov - 10) / 190 : (100 - 10) / 190;
  document.getElementById("ovr-fill").style.width = (100 * Math.max(0, Math.min(1, pos))) + "%";
  document.querySelectorAll(".btn-ovr").forEach(b => { b.disabled = !s.connected; });
  document.getElementById("ovr-note").textContent = ov == null && s.connected
    ? "GRBL has not reported its override yet (it reports after a change or every few status reports)."
    : "GRBL feed override: applies to feed moves at once (10–200 %). Estimates stay at 100 %.";
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
  // work origin
  ctx.strokeStyle = "#60a5fa"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(X(0) - 8, Y(0)); ctx.lineTo(X(0) + 8, Y(0)); ctx.moveTo(X(0), Y(0) - 8); ctx.lineTo(X(0), Y(0) + 8); ctx.stroke();
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
  // Attach jog buttons
  document.querySelectorAll(".jog-btn").forEach(attachJogButton);

  // Attach stop and reposition-confirm press-and-hold buttons
  attachStopButton();
  attachConfirmButton();
  attachSetupButtons();
  attachFeatureControls();
  attachView();
  attachPrinter();

  // Attach other controls
  attachControls();

  // Attach file upload
  attachFileUpload();

  // Attach console
  attachConsole();

  // Start WebSocket
  connectWS();
});
