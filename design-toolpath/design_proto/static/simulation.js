// ===========================================================================
// PRINT SIMULATION workspace (2026-10-09) — playback of the AUTHORITATIVE
// print plan (layer_assembly/print_plan.py, /api/assembly/print_plan
// detail 'stream'). Nothing here generates geometry, routes or estimates
// time: the plan's ITEMS (in print order: a layer transition, a travel, or
// one segment of a printing run — each one kind at one constant speed) come
// with their vertices (world XY, the layer's z) and their start time /
// duration from the planner's single timing rule (print_plan.duration). The
// simulation only INTERPOLATES the nozzle inside the current item by arc
// length, which is exactly that model.
// Rendering (canvas 2D, an orbit projection like the Assembly preview):
//   * on a view / size / option change: every vertex projected once
//     (typed arrays), the GHOST (all extrusion, faint) drawn once to an
//     off-screen canvas;
//   * PRINTED geometry accumulates on another off-screen canvas: playing
//     forward draws only the items completed since the last frame; a
//     backward scrub or view change redraws it (reduced detail while the
//     pointer is down, full detail on release);
//   * each frame: the two canvases + the current item's printed part + the
//     nozzle (position, direction) + headers.
// LAYER INSPECTION: a selected layer drawn alone from above (its full
// toolpath: ordered runs, start / end points, direction arrows, travel), its
// own time range on the timeline.
// WALL SYSTEMS WORKSPACE (2026-10-11): the geometry is generated there
// ("Generate Wall Systems"); the plan is made only when the user presses
// "Plan Print", and only from the CURRENT generated geometry (else: back to
// the Wall Systems workspace). (Earlier the same day: "Generate Print Paths"
// did both — Wall Systems, lattice,
// routing and the bead-level support check of every layer: the existing
// full pipeline, /api/assembly/print_plan). Assembly editing works on
// architectural envelopes and never plans. When the Designer / Assembly state
// changes afterwards the prepared paths are OUTDATED: still shown, but
// labelled so (banner + canvas) until they are generated again. A speed /
// frame-time change only re-times the same plan ('times'); a lift-setting
// change made here re-plans when the paths were current.
// ===========================================================================
const SIM_PALETTE = ['#4a9eff', '#ff9f43', '#2ecc71', '#e056fd', '#f9ca24', '#ff6b6b', '#48dbfb'];
const SIM_RUN_COLORS = ['#4a9eff', '#2ecc71', '#f9ca24', '#e056fd', '#48dbfb', '#ff9f43', '#a29bfe', '#55efc4'];
const SIM_TRAVEL = '#ff6b6b', SIM_TRANSITION = '#e056fd', SIM_DOWN = '#ffa94d', SIM_LIFT = '#ffd43b';
const SIM_KIND = { PRINT: 0, RETRACE: 1, TRAVEL: 2, TRANSITION: 3, FRAME: 4 };
const SIM_LOD_VERTICES = 120000;      // while dragging: aim to draw at most about this many vertices
// DRAG / SCRUB LOD (2026-10-15): an ERROR-BOUNDED vertex subset, computed once
// per plan (`_simLodMask`): every vertex of every item lies within the
// tolerance (world XY, inches) of what is drawn; item end points are always
// drawn. Replaces the earlier index stride (every k-th vertex), which cut
// right-angle corners by several inches while orbiting. Stationary: full detail.
const SIM_LOD_TOLERANCE_IN = 0.05;
const SIM_LOD_MAX_TOLERANCE_IN = 0.5;     // never coarser: above the budget at this, draw more vertices

const sim = {
  data: null, key: null, speedsKey: null, loading: false, error: null, paramError: null, reqGen: 0, inflight: null,
  currentKey: null,                // the CURRENT design state's plan key (outdated = data for another key)
  N: 0, V: 0,                      // items, vertices
  t: 0, playing: false, rate: 60, lastTs: null, raf: null,   // (1× = real machine time; 60× by default)
  mode: '3d',                      // '3d' | 'top' (layer inspection)
  selLayer: 0, selRun: null, selSection: null, selLift: null, selStage: null,
  open: {},                        // tree: expanded nodes
  opts: { ghost: true, travel: true, beads: false },   // beads: SHOW BEAD (deposited width) — drawing only
  view: { yaw: -Math.PI / 4, pitch: 0.55, zoom: 1, px: 0, py: 0 },
  beadPx: { '3d': 1, top: 1 },     // SHOW BEAD: the bead width on screen (px), per view
  drag: null, scrubbing: false,
  cache: null,                     // { key, W, H, sx, sy, ghost, printed, drawn, lod }
  stats: { framesDrawn: 0, itemsDrawn: 0, fullRedraws: 0, lastFrameMs: 0 },
};

// ---- the plan → typed arrays (once per plan) --------------------------------
function simIngest(res) {
  const S = res.stream, I = S.items, N = I.kind.length;
  const D = {
    res, N, strings: S.strings,
    kind: Int8Array.from(I.kind), layer: Int32Array.from(I.layer), run: Int32Array.from(I.run),
    off: Int32Array.from(I.off), n: Int32Array.from(I.n),
    z0: Float64Array.from(I.z0), z1: Float64Array.from(I.z1),
    strand: Int32Array.from(I.strand), source: Int32Array.from(I.source), system: Int32Array.from(I.system),
    t0: Float64Array.from(I.t0), dt: Float64Array.from(I.dt), xy: Float64Array.from(S.xy),
    // LIFT-AWARE plan: each item's lift / island stage / pass, and the clear Z of a non-printing move
    lift: Int32Array.from(I.lift || new Array(N).fill(0)), stage: Int32Array.from(I.stage || new Array(N).fill(0)),
    pass: Int32Array.from(I.pass || new Array(N).fill(0)), zc: Float64Array.from(I.zc || I.z1),
  };
  D.V = D.xy.length / 2;
  // per-vertex cumulative length inside its item (arc-length interpolation)
  D.cum = new Float64Array(D.V);
  for (let i = 0; i < N; i++) {
    const o = D.off[i], n = D.n[i];
    let acc = 0;
    D.cum[o] = 0;
    for (let k = 1; k < n; k++) {
      const dx = D.xy[2 * (o + k)] - D.xy[2 * (o + k - 1)], dy = D.xy[2 * (o + k) + 1] - D.xy[2 * (o + k - 1) + 1];
      acc += Math.sqrt(dx * dx + dy * dy);
      D.cum[o + k] = acc;
    }
  }
  const nL = res.plan.layers.length;
  // a global layer's items are NOT contiguous in time (islands of a lift are
  // built one after another): its item list, first / last item
  D.layerFirst = new Int32Array(nL).fill(-1);
  D.layerLast = new Int32Array(nL).fill(-1);
  const li = Array.from({ length: nL }, () => []);
  for (let i = 0; i < N; i++) {
    if (D.kind[i] === SIM_KIND.FRAME) continue;
    const L = D.layer[i];
    if (D.layerFirst[L] < 0) D.layerFirst[L] = i;
    D.layerLast[L] = i;
    li[L].push(i);
  }
  D.layerItems = li.map(a => Int32Array.from(a));
  D.clocks = {};
  const nS = (res.plan.stages || []).length, nF = (res.plan.lifts || []).length;
  D.stageLast = new Int32Array(Math.max(1, nS)).fill(-1);
  D.liftLast = new Int32Array(Math.max(1, nF)).fill(-1);
  D.frames = [];
  for (let i = 0; i < N; i++) {
    if (D.kind[i] === SIM_KIND.FRAME) { D.frames.push(i); continue; }
    if (D.stage[i] >= 0 && D.stage[i] < D.stageLast.length) D.stageLast[D.stage[i]] = i;
    if (D.lift[i] >= 0 && D.lift[i] < D.liftLast.length) D.liftLast[D.lift[i]] = i;
  }
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let k = 0; k < D.V; k++) {
    const x = D.xy[2 * k], y = D.xy[2 * k + 1];
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  if (!isFinite(x0)) { x0 = 0; x1 = 100; y0 = 0; y1 = 100; }
  D.bounds = { x0, x1, y0, y1, z1: nL ? res.plan.layers[nL - 1].z_top : 0 };
  D.total = N ? D.t0[N - 1] + D.dt[N - 1] : 0;
  D.sectionOfLayer = Int32Array.from(res.plan.layers.map(L => L.section_index));
  return D;
}

function simApplyTimes(t0, dt) {          // new speeds: the same items, new clock
  const D = sim.data;
  if (!D || t0.length !== D.N) return false;
  const frac = D.total > 0 ? sim.t / D.total : 0;
  D.t0 = Float64Array.from(t0); D.dt = Float64Array.from(dt);
  D.total = D.N ? D.t0[D.N - 1] + D.dt[D.N - 1] : 0;
  sim.t = frac * D.total;                  // (same place in the print)
  return true;
}

// ---- time → where the nozzle is --------------------------------------------
// the item playing at time t: the last item that STARTED before t (at an
// item boundary that is the item just finished — a layer's end is still
// that layer, its last move complete)
function simItemAt(t) {
  const D = sim.data;
  if (!D || !D.N) return -1;
  if (t <= D.t0[0]) return 0;
  let lo = 0, hi = D.N - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (D.t0[mid] < t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// {i, f (0..1 of the item), x, y, z, dx, dy (unit direction), kind, layer, run}
function simLocate(t) {
  const D = sim.data;
  const i = simItemAt(t);
  if (i < 0) return null;
  const f = D.dt[i] > 0 ? Math.max(0, Math.min(1, (t - D.t0[i]) / D.dt[i])) : 1;
  const o = D.off[i], n = D.n[i], Lt = D.cum[o + n - 1], d = f * Lt;
  let k = 1;
  while (k < n - 1 && D.cum[o + k] < d) k++;
  const a = o + k - 1, b = o + k;
  const seg = D.cum[b] - D.cum[a], u = seg > 0 ? (d - D.cum[a]) / seg : 1;
  const ax = D.xy[2 * a], ay = D.xy[2 * a + 1], bx = D.xy[2 * b], by = D.xy[2 * b + 1];
  const len = Math.hypot(bx - ax, by - ay) || 1;
  // a move over finished geometry happens at its clear height (rise, move, descend)
  const over = _simOver(i);
  const z = over && f > 0 && f < 1 ? D.zc[i] : D.z0[i] + (D.z1[i] - D.z0[i]) * f;
  return { i, f, k, x: ax + (bx - ax) * u, y: ay + (by - ay) * u, z,
           dx: (bx - ax) / len, dy: (by - ay) / len, kind: D.kind[i], layer: D.layer[i], run: D.run[i],
           lift: D.lift[i], stage: D.stage[i], pass: D.pass[i] };
}

// a transition made at its clear height — rise, move, descend: going DOWN to
// another island (never diagonally through finished geometry), or over
// geometry taller than both ends
function _simOver(i) {
  const D = sim.data;
  if (D.kind[i] !== SIM_KIND.TRANSITION) return false;
  const top = Math.max(D.z0[i], D.z1[i]);
  return D.zc[i] > top + 1e-6 || (D.z1[i] < D.z0[i] - 1e-9 && D.zc[i] >= top - 1e-9);
}

function simLayerRange(L) {                // [first start, last end] of a layer's items (global time)
  const D = sim.data;
  const a = D.layerFirst[L], b = D.layerLast[L];
  if (a < 0) return [0, 0];
  return [D.t0[a], D.t0[b] + D.dt[b]];
}

function simRunRange(L, r) {               // [t0, t1] of one printing run
  const D = sim.data;
  let t0 = null, t1 = null;
  for (const i of D.layerItems[L] || [])
    if (D.run[i] === r) { if (t0 === null) t0 = D.t0[i]; t1 = D.t0[i] + D.dt[i]; }
  return t0 === null ? null : [t0, t1];
}

// LAYER CLOCK (top view): a layer's own items back to back — its time line
// skips what other islands print in between (u = time inside the layer)
function _simClock(L) {
  const D = sim.data;
  if (!D.clocks[L]) {
    const idx = D.layerItems[L] || new Int32Array(0), cum = new Float64Array(idx.length + 1);
    for (let j = 0; j < idx.length; j++) cum[j + 1] = cum[j] + D.dt[idx[j]];
    D.clocks[L] = { idx, cum, U: cum[idx.length] };
  }
  return D.clocks[L];
}
function _simToGlobal(u) {                  // timeline position → print time
  const D = sim.data;
  if (sim.mode !== 'top') return u;
  const C = _simClock(sim.selLayer), n = C.idx.length;
  if (!n) return 0;
  if (u <= 0) return D.t0[C.idx[0]];
  let lo = 0, hi = n - 1;                    // the last item starting at or before u
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (C.cum[m] <= u) lo = m; else hi = m - 1; }
  const i = C.idx[lo];
  return D.t0[i] + Math.min(u - C.cum[lo], D.dt[i]);
}
function _simToLocal(t) {                   // print time → timeline position
  const D = sim.data;
  if (sim.mode !== 'top') return t;
  const C = _simClock(sim.selLayer), n = C.idx.length;
  if (!n || t <= D.t0[C.idx[0]]) return 0;
  let lo = 0, hi = n - 1;                    // the last item of the layer starting before t
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (D.t0[C.idx[m]] < t) lo = m; else hi = m - 1; }
  const i = C.idx[lo];
  return C.cum[lo] + Math.min(t - D.t0[i], D.dt[i]);
}

function simRange() {                      // the timeline's range in the current mode (timeline units)
  const D = sim.data;
  if (!D) return [0, 0];
  return sim.mode === 'top' ? [0, _simClock(sim.selLayer).U] : [0, D.total];
}

// ---- styles (extrusion vs travel are always distinct) -----------------------
function _simStyle(i, mode) {
  const D = sim.data, k = D.kind[i];
  if (k === SIM_KIND.TRAVEL) return { color: SIM_TRAVEL, width: 1, dash: [5, 4], kind: 'travel' };
  if (k === SIM_KIND.TRANSITION) return D.z1[i] < D.z0[i] - 1e-9
    // back DOWN to another island's lower layer — same lift (never an earlier lift)
    ? { color: SIM_DOWN, width: 1.2, dash: [6, 3], kind: 'transition', down: true }
    : { color: SIM_TRANSITION, width: 1, dash: [2, 3], kind: 'transition' };
  if (k === SIM_KIND.FRAME) return { color: SIM_LIFT, width: 0, dash: null, kind: 'frame' };
  // 3D: one colour per island / connected stage
  const color = mode === 'top' ? SIM_RUN_COLORS[Math.max(0, D.run[i]) % SIM_RUN_COLORS.length]
                               : SIM_PALETTE[Math.max(0, D.stage[i]) % SIM_PALETTE.length];
  const kind = k === SIM_KIND.RETRACE ? 'retrace' : 'print';
  if (sim.opts.beads)          // SHOW BEAD: the deposited bead at its real width (project material)
    return { color, width: Math.max(1, sim.beadPx[mode] || 1), dash: null, kind, bead: true };
  return { color, width: k === SIM_KIND.RETRACE ? 2.4 : 1.4, dash: null, kind };
}

// ---- SHOW BEAD (2026-10-16): VISUALIZATION ONLY — the extruded passes drawn
// at the project's bead width (the Designer's Material / Bead width, round
// caps / joins like the Designer's bead view) instead of thin centrelines.
// Nothing here reaches the plan, its timing, the support check or the export.
function simBeadWidth() {
  const m = typeof _material === 'function' ? _material() : null;
  return m && m.bead_width > 0 ? m.bead_width : 3.0;
}
function _simSetBeadPx(mode, pxPerInch) { sim.beadPx[mode] = simBeadWidth() * pxPerInch; }

// ---- projection ---------------------------------------------------------------
function simProjector(W, H) {
  const B = sim.data.bounds;
  const cx = (B.x0 + B.x1) / 2, cy = (B.y0 + B.y1) / 2, cz = B.z1 / 2;
  const { yaw, pitch, zoom, px, py } = sim.view;
  const cyw = Math.cos(yaw), syw = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  const span = Math.max(1, Math.hypot(B.x1 - B.x0, B.y1 - B.y0), B.z1);
  const s = 0.8 * Math.min(W, H) / span * zoom;
  _simSetBeadPx('3d', s);
  return (x, y, z) => {
    const a = (x - cx) * cyw - (y - cy) * syw, b = (x - cx) * syw + (y - cy) * cyw;
    return [W / 2 + a * s + px, H / 2 - ((z - cz) * cp + b * sp) * s + py];
  };
}

function simTopProjector(W, H, L) {        // a layer from straight above, fitted
  const D = sim.data;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const i of D.layerItems[L] || []) {
    if (D.kind[i] === SIM_KIND.TRANSITION) continue;
    for (let k = D.off[i]; k < D.off[i] + D.n[i]; k++) {
      const x = D.xy[2 * k], y = D.xy[2 * k + 1];
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
  }
  if (!isFinite(x0)) { x0 = 0; x1 = 1; y0 = 0; y1 = 1; }
  const s = 0.88 * Math.min(W / Math.max(1, x1 - x0), H / Math.max(1, y1 - y0)) * sim.view.zoom;
  _simSetBeadPx('top', s);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return (x, y) => [W / 2 + (x - cx) * s + sim.view.px, H / 2 - (y - cy) * s + sim.view.py];
}

// ---- static caches (3D) ---------------------------------------------------------
function _simCanvas(W, H) {
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  return c;
}

function _simViewKey(W, H) {
  return JSON.stringify([W, H, sim.view, sim.opts, sim.key, sim.mode, sim.opts.beads ? simBeadWidth() : 0]);
}

// project every vertex (z per vertex: a transition climbs from z0 to z1)
function _simProjectAll(W, H) {
  const D = sim.data, P = simProjector(W, H);
  const sx = new Float32Array(D.V), sy = new Float32Array(D.V), over = {};
  for (let i = 0; i < D.N; i++) {
    if (_simOver(i)) {                       // the clear-height corners of a move over finished geometry
      const o = D.off[i], e = o + D.n[i] - 1;
      over[i] = [...P(D.xy[2 * o], D.xy[2 * o + 1], D.zc[i]), ...P(D.xy[2 * e], D.xy[2 * e + 1], D.zc[i])];
    }
    const o = D.off[i], n = D.n[i];
    for (let k = 0; k < n; k++) {
      const z = n > 1 ? D.z0[i] + (D.z1[i] - D.z0[i]) * (k / (n - 1)) : D.z0[i];
      const q = P(D.xy[2 * (o + k)], D.xy[2 * (o + k) + 1], z);
      sx[o + k] = q[0]; sy[o + k] = q[1];
    }
  }
  return { sx, sy, over };
}

// Ramer–Douglas–Peucker on item [o, o + n) of the flat xy array: marks in
// `mask` the vertices needed so every vertex lies within `eps` of the kept
// polyline; returns how many it kept
function _simRdp(xy, o, n, eps, mask) {
  if (n <= 0) return 0;
  mask[o] = 1; mask[o + n - 1] = 1;
  let kept = n > 1 ? 2 : 1;
  const stack = [o, o + n - 1], e2 = eps * eps;
  while (stack.length) {
    const j = stack.pop(), i = stack.pop();
    if (j <= i + 1) continue;
    const ax = xy[2 * i], ay = xy[2 * i + 1], dx = xy[2 * j] - ax, dy = xy[2 * j + 1] - ay, l2 = dx * dx + dy * dy;
    let best = -1, bk = -1;
    for (let k = i + 1; k < j; k++) {
      const px = xy[2 * k] - ax, py = xy[2 * k + 1] - ay;
      let d;
      if (l2 === 0) d = px * px + py * py;
      else {
        let t = (px * dx + py * dy) / l2;
        t = t < 0 ? 0 : (t > 1 ? 1 : t);
        const ex = px - t * dx, ey = py - t * dy;
        d = ex * ex + ey * ey;
      }
      if (d > best) { best = d; bk = k; }
    }
    if (best > e2) { mask[bk] = 1; kept++; stack.push(i, bk, bk, j); }
  }
  return kept;
}

// the drag / scrub vertex subset of plan D: tolerance from
// SIM_LOD_TOLERANCE_IN, doubled while above the vertex budget (≤ max)
function _simLodMask(D) {
  let eps = SIM_LOD_TOLERANCE_IN, mask, kept;
  for (;;) {
    mask = new Uint8Array(D.V); kept = 0;
    for (let i = 0; i < D.N; i++) kept += _simRdp(D.xy, D.off[i], D.n[i], eps, mask);
    if (kept <= SIM_LOD_VERTICES || eps >= SIM_LOD_MAX_TOLERANCE_IN) break;
    eps = Math.min(SIM_LOD_MAX_TOLERANCE_IN, eps * 2);
  }
  D.lodMask = mask; D.lodTol = eps; D.lodKept = kept;
}

// draw items [a, b) with their styles; `lod` = the drag / scrub subset
// (D.lodMask, error-bounded); `only` = 'extrusion' (the ghost) or null;
// batched into one path per style run
function _simDrawItems(g, C, a, b, lod, only) {
  const D = sim.data;
  let cur = null;
  const flush = () => { if (cur) { g.stroke(); cur = null; } };
  for (let i = a; i < b; i++) {
    const kd = D.kind[i];
    if (kd === SIM_KIND.FRAME) continue;      // (an event, not a move)
    const travel = kd === SIM_KIND.TRAVEL || kd === SIM_KIND.TRANSITION;
    if (only === 'extrusion' && travel) continue;
    if (travel && !sim.opts.travel) continue;
    const st = _simStyle(i, '3d');
    const sk = st.color + st.width + (st.dash ? 'd' : '');
    if (cur !== sk) {
      flush();
      g.strokeStyle = st.color; g.lineWidth = st.width;
      g.lineCap = st.bead ? 'round' : 'butt'; g.lineJoin = st.bead ? 'round' : 'miter';
      if (g.setLineDash) g.setLineDash(st.dash || []);
      g.beginPath();
      cur = sk;
    }
    const o = D.off[i], n = D.n[i];
    g.moveTo(C.sx[o], C.sy[o]);
    const ov = C.over && C.over[i];
    if (ov) { g.lineTo(ov[0], ov[1]); g.lineTo(ov[2], ov[3]); }
    const M = lod ? D.lodMask : null;
    for (let k = 1; k < n - 1; k++) if (!M || M[o + k]) g.lineTo(C.sx[o + k], C.sy[o + k]);
    g.lineTo(C.sx[o + n - 1], C.sy[o + n - 1]);
    sim.stats.itemsDrawn++;
  }
  flush();
  if (g.setLineDash) g.setLineDash([]);
  g.lineCap = 'butt'; g.lineJoin = 'miter';
}

function _simLod(lod) {
  if (!lod || sim.data.V <= SIM_LOD_VERTICES) return false;     // small plans: always full detail
  if (!sim.data.lodMask) _simLodMask(sim.data);                 // once per plan
  return true;
}

function _simEnsureCache(W, H) {
  const lod = !!(sim.drag || sim.scrubbing);
  const key = _simViewKey(W, H);
  let C = sim.cache;
  if (C && C.key === key && C.lod === lod) return C;
  if (C && C.key === key && C.lod && !lod) {          // released: full detail
    C.lod = false;
  } else {
    const pr = _simProjectAll(W, H);
    C = sim.cache = { key, W, H, sx: pr.sx, sy: pr.sy, over: pr.over, lod, ghost: null, printed: null, drawn: 0 };
  }
  C.ghost = _simCanvas(W, H);
  const gg = C.ghost.getContext('2d');
  _simDrawItems(gg, C, 0, sim.data.N, _simLod(lod), 'extrusion');
  C.printed = _simCanvas(W, H);
  C.drawn = 0;
  sim.stats.fullRedraws++;
  return C;
}

// bring the printed canvas to item index `upTo` (exclusive): forward = only
// the new items; backward = redraw from the start
function _simCatchUp(C, upTo) {
  const g = C.printed.getContext('2d');
  if (upTo < C.drawn) {
    g.clearRect(0, 0, C.W, C.H);
    C.drawn = 0;
  }
  if (upTo > C.drawn) {
    _simDrawItems(g, C, C.drawn, upTo, _simLod(C.lod), null);
    C.drawn = upTo;
  }
}

// ---- drawing ---------------------------------------------------------------------
function simDraw() {
  const cv = document.getElementById('sim-canvas'), wrap = document.getElementById('sim-canvas-wrap');
  if (!cv || !cv.getContext || !_simVisible()) return;       // (nothing is drawn while hidden)
  const t0p = typeof performance !== 'undefined' ? performance.now() : 0;
  const W = Math.max(200, (wrap && wrap.clientWidth) || 800), H = Math.max(200, (wrap && wrap.clientHeight) || 600);
  if (cv.width !== W) cv.width = W;
  if (cv.height !== H) cv.height = H;
  const g = cv.getContext('2d');
  g.fillStyle = '#1a1a1a'; g.fillRect(0, 0, W, H);
  const D = sim.data;
  if (!D || !D.N) {
    g.fillStyle = '#777'; g.font = '12px monospace';
    g.fillText(sim.loading ? 'Planning the print… (the Assembly stays editable)' :
      (sim.error || (D ? 'Nothing to print yet — add a section in Assembly.'
                       : (sim.wsBlocked ? 'Generate the Wall Systems first (Wall Systems workspace).'
                                        : 'No print plan yet — press "Plan Print".'))), 20, 30);
    return;
  }
  const loc = simLocate(sim.t);
  if (sim.mode === 'top') _simDrawTop(g, W, H, loc);
  else _simDraw3D(g, W, H, loc);
  if (simOutdated()) {                       // never shown as if it were the current design
    g.fillStyle = 'rgba(255,159,67,0.92)'; g.font = 'bold 13px monospace';
    g.fillText('OUTDATED — the design changed after these print paths were generated', 16, H - 18);
  }
  sim.stats.framesDrawn++;
  sim.stats.lastFrameMs = typeof performance !== 'undefined' ? performance.now() - t0p : 0;
}

function _simDraw3D(g, W, H, loc) {
  const D = sim.data, C = _simEnsureCache(W, H);
  _simCatchUp(C, loc.i);
  if (sim.opts.ghost) { g.globalAlpha = 0.16; g.drawImage(C.ghost, 0, 0); g.globalAlpha = 1; }
  g.drawImage(C.printed, 0, 0);
  // the current item, printed up to the nozzle
  const st = _simStyle(loc.i, '3d');
  const o = D.off[loc.i];
  if (st.kind !== 'frame' && (!(st.kind === 'travel' || st.kind === 'transition') || sim.opts.travel)) {
    g.strokeStyle = st.color; g.lineWidth = st.width;
    g.lineCap = st.bead ? 'round' : 'butt'; g.lineJoin = st.bead ? 'round' : 'miter';
    if (g.setLineDash) g.setLineDash(st.dash || []);
    g.beginPath();
    g.moveTo(C.sx[o], C.sy[o]);
    const ov = C.over && C.over[loc.i];
    if (ov && loc.f > 0) g.lineTo(ov[0], ov[1]);
    for (let k = 1; k < loc.k; k++) g.lineTo(C.sx[o + k], C.sy[o + k]);
    const P = simProjector(W, H);
    const q = P(loc.x, loc.y, loc.z);
    g.lineTo(q[0], q[1]);
    g.stroke();
    if (g.setLineDash) g.setLineDash([]);
    g.lineCap = 'butt'; g.lineJoin = 'miter';
  }
  if (typeof machineProfile === 'function') drawMachineEnvelope(g, simProjector(W, H), machineProfile(), { z: true });
  _simDrawLifts(g, simProjector(W, H), loc);
  _simDrawHeaders(g, simProjector(W, H), loc.layer);
  _simDrawNozzle(g, simProjector(W, H), loc, true);
}

// LIFT BOUNDARIES: the frame base of every later lift, as a level outline
// around the building (bright once the frame has been raised to it)
function _simDrawLifts(g, P, loc) {
  const D = sim.data, B = D.bounds, lifts = D.res.plan.lifts || [];
  g.font = '10px monospace';
  for (const Lf of lifts) {
    if (!Lf.index) continue;
    const z = Lf.z_bottom, done = loc.lift >= Lf.index;
    const c = [[B.x0, B.y0], [B.x1, B.y0], [B.x1, B.y1], [B.x0, B.y1]].map(([x, y]) => P(x, y, z));
    g.strokeStyle = SIM_LIFT; g.globalAlpha = done ? 0.75 : 0.3; g.lineWidth = 1;
    if (g.setLineDash) g.setLineDash([8, 5]);
    g.beginPath(); c.forEach((q, k) => (k ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]))); g.closePath(); g.stroke();
    if (g.setLineDash) g.setLineDash([]);
    g.fillStyle = SIM_LIFT; g.fillText(`lift ${Lf.index + 1} · frame z ${z.toFixed(1)}`, c[1][0] + 6, c[1][1]);
    g.globalAlpha = 1;
  }
  if (loc.kind === SIM_KIND.FRAME) {
    const Lf = lifts[loc.lift];
    g.fillStyle = SIM_LIFT; g.font = '13px monospace';
    g.fillText(`FRAME REPOSITION → lift ${loc.lift + 1} (frame base z ${Lf ? Lf.z_bottom.toFixed(1) : '?'} in)`, 20, 28);
  }
}

function _simDrawHeaders(g, P, curLayer) {
  for (const h of (sim.data.res.headers || [])) {
    const done = curLayer >= h.layer;
    g.strokeStyle = done ? '#bbbbbb' : '#555'; g.lineWidth = 1;
    g.globalAlpha = done ? 0.9 : 0.35;
    for (const z of [h.z_bottom, h.z_top]) {
      g.beginPath();
      h.corners.forEach((c, k) => { const q = P(c[0], c[1], z); if (k) g.lineTo(q[0], q[1]); else g.moveTo(q[0], q[1]); });
      g.closePath(); g.stroke();
    }
    g.globalAlpha = 1;
  }
}

// the nozzle: a vertical needle down to the bead + a direction arrow
function _simDrawNozzle(g, P, loc, threeD) {
  const p = threeD ? P(loc.x, loc.y, loc.z) : P(loc.x, loc.y);
  const top = threeD ? P(loc.x, loc.y, loc.z + 14) : [p[0], p[1] - 22];
  const extruding = loc.kind === SIM_KIND.PRINT || loc.kind === SIM_KIND.RETRACE;
  g.strokeStyle = extruding ? '#ffffff' : SIM_TRAVEL; g.lineWidth = 2;
  g.beginPath(); g.moveTo(top[0], top[1]); g.lineTo(p[0], p[1]); g.stroke();
  g.fillStyle = extruding ? '#ffffff' : SIM_TRAVEL;
  g.beginPath(); g.arc(p[0], p[1], 4, 0, Math.PI * 2); g.fill();
  const ahead = threeD ? P(loc.x + loc.dx * 6, loc.y + loc.dy * 6, loc.z) : P(loc.x + loc.dx * 6, loc.y + loc.dy * 6);
  const ax = ahead[0] - p[0], ay = ahead[1] - p[1], al = Math.hypot(ax, ay) || 1;
  const ux = ax / al, uy = ay / al, L = 18;
  const tip = [p[0] + ux * L, p[1] + uy * L];
  g.strokeStyle = '#ffd43b'; g.lineWidth = 2;
  g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(tip[0], tip[1]); g.stroke();
  g.beginPath(); g.moveTo(tip[0], tip[1]);
  g.lineTo(tip[0] - ux * 7 - uy * 4, tip[1] - uy * 7 + ux * 4);
  g.lineTo(tip[0] - ux * 7 + uy * 4, tip[1] - uy * 7 - ux * 4);
  g.closePath(); g.fillStyle = '#ffd43b'; g.fill();
}

// LAYER INSPECTION: the selected layer's whole toolpath from above
function _simDrawTop(g, W, H, loc) {
  const D = sim.data, L = sim.selLayer, P = simTopProjector(W, H, L);
  if (D.layerFirst[L] < 0) return;
  if (typeof drawPhysicalGrid === 'function') drawPhysicalGrid(g, (x, y) => P(x, y), W, H);   // 1 ft squares from the print origin
  if (typeof machineProfile === 'function') drawMachineEnvelope(g, (x, y) => P(x, y), machineProfile(), { z: false });
  const cur = loc.i;                          // items before it are printed (time order)
  const runStart = {}, runEnd = {};
  for (const i of D.layerItems[L]) {
    const st = _simStyle(i, 'top');
    const isTravel = st.kind === 'travel' || st.kind === 'transition';
    if (isTravel && !sim.opts.travel) continue;
    const sel = sim.selRun === null || D.run[i] === sim.selRun || isTravel;
    g.globalAlpha = (i < cur ? 1 : (sim.opts.ghost ? 0.28 : 0)) * (sel ? 1 : 0.25) * (st.bead ? 0.55 : 1);
    if (g.globalAlpha <= 0) continue;
    // (SHOW BEAD: the bead's real width, translucent — where two passes overlap
    // the band is denser: a THIN Out-and-Back shows both beads and their overlap)
    g.strokeStyle = st.color; g.lineWidth = st.bead ? st.width : st.width + 0.6;
    g.lineCap = st.bead ? 'round' : 'butt'; g.lineJoin = st.bead ? 'round' : 'miter';
    if (g.setLineDash) g.setLineDash(st.dash || []);
    g.beginPath();
    const o = D.off[i], n = D.n[i];
    const upto = i === cur ? loc.k - 1 : n - 1;      // (the current item: up to the nozzle)
    let q = P(D.xy[2 * o], D.xy[2 * o + 1]);
    g.moveTo(q[0], q[1]);
    for (let k = 1; k <= upto; k++) { q = P(D.xy[2 * (o + k)], D.xy[2 * (o + k) + 1]); g.lineTo(q[0], q[1]); }
    if (i === cur) { q = P(loc.x, loc.y); g.lineTo(q[0], q[1]); }
    g.stroke();
    if (!isTravel) {
      _simArrows(g, P, i, upto);
      const r = D.run[i];
      if (!(r in runStart)) runStart[r] = [D.xy[2 * o], D.xy[2 * o + 1]];
      runEnd[r] = [D.xy[2 * (o + n - 1)], D.xy[2 * (o + n - 1) + 1]];
    }
  }
  if (g.setLineDash) g.setLineDash([]);
  g.globalAlpha = 1;
  g.lineCap = 'butt'; g.lineJoin = 'miter';
  // start (green, numbered) / end (red) of every run
  g.font = '11px monospace';
  for (const r of Object.keys(runStart)) {
    const s = P(...runStart[r]), e = P(...runEnd[r]);
    g.fillStyle = '#ff6b6b'; g.fillRect(e[0] - 4, e[1] - 4, 8, 8);
    g.fillStyle = '#2ecc71'; g.beginPath(); g.arc(s[0], s[1], 5, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#fff'; g.fillText(String(+r + 1), s[0] + 7, s[1] - 7);
  }
  if (loc.layer === L) _simDrawNozzle(g, P, loc, false);
}

function _simArrows(g, P, i, upto) {        // direction arrows every ~70 px along a printed item
  const D = sim.data, o = D.off[i];
  let acc = 0;
  let prev = P(D.xy[2 * o], D.xy[2 * o + 1]);
  g.fillStyle = g.strokeStyle;
  for (let k = 1; k <= upto; k++) {
    const q = P(D.xy[2 * (o + k)], D.xy[2 * (o + k) + 1]);
    const dx = q[0] - prev[0], dy = q[1] - prev[1], l = Math.hypot(dx, dy);
    acc += l;
    if (acc > 70 && l > 0.5) {
      acc = 0;
      const ux = dx / l, uy = dy / l, mx = (q[0] + prev[0]) / 2, my = (q[1] + prev[1]) / 2;
      g.beginPath();
      g.moveTo(mx + ux * 5, my + uy * 5);
      g.lineTo(mx - ux * 4 - uy * 4, my - uy * 4 + ux * 4);
      g.lineTo(mx - ux * 4 + uy * 4, my - uy * 4 - ux * 4);
      g.closePath(); g.fill();
    }
    prev = q;
  }
}

// ---- playback ---------------------------------------------------------------------
function simSetTime(t) {                     // t: PRINT time
  sim.t = Math.max(0, Math.min(sim.data ? sim.data.total : 0, t));
  const loc = sim.data && sim.data.N ? simLocate(sim.t) : null;
  if (loc && sim.mode === '3d' && loc.layer !== sim.selLayer) sim.selLayer = loc.layer;
  simRenderPanels();
  simDraw();
}

// a TIMELINE position (the whole print, or the selected layer's own clock in top view)
function simSetPos(u) {
  const [a, b] = simRange();
  simSetTime(_simToGlobal(Math.max(a, Math.min(b, u))));
}

function simTogglePlay() {
  if (!sim.data || !sim.data.N) return;
  if (sim.playing) { sim.playing = false; simRenderPanels(); return; }
  const [a, b] = simRange();
  if (_simToLocal(sim.t) >= b - 1e-9) sim.t = _simToGlobal(a);   // at the end: play again from the start
  sim.playing = true; sim.lastTs = null;
  simRenderPanels();
  _simLoop();
}

function _simLoop() {
  if (!sim.playing) return;
  if (!_simVisible()) { sim.playing = false; return; }
  sim.raf = requestAnimationFrame(ts => {
    if (!sim.playing) return;
    if (sim.lastTs !== null) {
      const [, b] = simRange();
      const u = _simToLocal(sim.t) + (ts - sim.lastTs) / 1000 * sim.rate;    // 1× = the estimated machine time
      if (u >= b) { sim.playing = false; simSetPos(b); return; }
      simSetPos(u);
    }
    sim.lastTs = ts;
    _simLoop();
  });
}

function simRestart() { sim.playing = false; simSetPos(simRange()[0]); }

function simSetRate(v) {
  const x = parseFloat(v);
  if (x > 0) sim.rate = x;
  sim.lastTs = null;
  simRenderPanels();
}

function simScrub(v, live) {                  // the range slider (0..10000 over the current range)
  const [a, b] = simRange();
  sim.scrubbing = !!live;
  sim.playing = false;
  simSetPos(a + (b - a) * (+v / 10000));
  if (!live) { sim.scrubbing = false; simDraw(); }
}

// ---- layers / navigation ---------------------------------------------------------------
function simSelectLayer(L, keepTime) {
  const D = sim.data;
  if (!D || !D.res.plan.layers.length) return;
  L = Math.max(0, Math.min(D.res.plan.layers.length - 1, Math.round(+L || 0)));
  sim.selLayer = L; sim.selRun = null;
  if (!keepTime) {
    const [, b] = simLayerRange(L);
    sim.t = sim.mode === 'top' ? _simToGlobal(0) : b;   // 3D: the layer just finished; top view: its start
  }
  simRenderPanels();
  simDraw();
}

function simStepLayer(d) { sim.playing = false; simSelectLayer(sim.selLayer + d); }

function simTopView() {
  if (!sim.data) return;
  sim.mode = 'top'; sim.playing = false;
  sim.view.zoom = 1; sim.view.px = 0; sim.view.py = 0;
  const [a, b] = simLayerRange(sim.selLayer);
  if (sim.t < a || sim.t > b) sim.t = _simToGlobal(0);
  simRenderPanels(); simDraw();
}

function sim3D() {
  sim.mode = '3d'; sim.playing = false;
  simViewPreset('iso');
}

function simViewPreset(name) {
  if (name === 'top' && sim.mode === '3d') { sim.view.yaw = 0; sim.view.pitch = Math.PI / 2; }
  else if (name === 'front') { sim.view.yaw = 0; sim.view.pitch = 0; }
  else if (name === 'iso') { sim.view.yaw = -Math.PI / 4; sim.view.pitch = 0.55; }
  sim.view.zoom = 1; sim.view.px = 0; sim.view.py = 0;
  simRenderPanels(); simDraw();
}

function simSetOption(k, v) { sim.opts[k] = !!v; simDraw(); }

// select a node of the hierarchy: focus its geometry + its timeline range
function simSelectSection(si) {
  const D = sim.data, sec = D.res.sections.find(s => s.index === si);
  if (!sec || !sec.layers) return;
  sim.selSection = si; sim.mode = '3d'; sim.playing = false;
  sim.open['s' + si] = true;
  simSelectLayer(sec.first_layer + sec.layers - 1);
}

// a LIFT / an island STAGE: 3D, the print up to its last move
function simSelectLift(f) {
  const D = sim.data, i = D.liftLast[f];
  sim.selLift = f; sim.selStage = null; sim.mode = '3d'; sim.playing = false;
  sim.open['f' + f] = true;
  if (i >= 0) simSetTime(D.t0[i] + D.dt[i]);
  else { simRenderPanels(); simDraw(); }
}

function simSelectStage(si) {
  const D = sim.data, i = D.stageLast[si], st = (D.res.plan.stages || [])[si];
  sim.selStage = si; sim.mode = '3d'; sim.playing = false;
  sim.open['g' + si] = true;
  if (st) sim.open['f' + st.lift] = true;
  if (i >= 0) simSetTime(D.t0[i] + D.dt[i]);
  else { simRenderPanels(); simDraw(); }
}

function simSelectRun(L, r) {
  sim.selLayer = L; sim.selRun = r; sim.mode = 'top'; sim.playing = false;
  sim.open['l' + L] = true;
  const rr = simRunRange(L, r);
  if (rr) sim.t = rr[0];
  simRenderPanels(); simDraw();
}

// ---- print-plan settings (the project's: Assembly state, undoable, saved) -----------
function simSetSpeedUnit(u) { asmSetSpeedUnit(u); simRenderPanels(); }

// a setting edited here: the Assembly setter (validates, undoable); its
// refusal is shown in THIS column (sim.paramError), never left in the Assembly's
function _simParam(fn) {
  const prev = asm.inputError;
  asm.inputError = null;
  fn();
  sim.paramError = asm.inputError;
  asm.inputError = prev;
}

async function simSetSpeed(kind, v) {
  _simParam(() => asmSetSpeed(kind, v));     // validates, converts the unit, undoable
  simRenderPanels();                         // (the field / its error / the lift boundaries at once)
  return simRefresh();
}

async function simSetFrameTime(min) {        // estimated minutes per frame reposition (timing only)
  _simParam(() => asmSetFrameTime(min));
  simRenderPanels();                         // (the field / its error / the lift boundaries at once)
  return simRefresh();
}

// LIFTS (asm.state.machine — the Assembly state the plan is built from): a
// change is a new plan key → the whole plan is planned again (the lift / island
// schedule depends on it); speeds and frame time only re-time the same plan
async function simSetMachine(field, v) {
  const wasCurrent = !!sim.data && !simOutdated();
  _simParam(() => asmSetMachine(field, v)); // validates (lift height ≤ max Z travel), undoable
  simRenderPanels();                         // (the field / its error / the lift boundaries at once)
  return simRefresh({ generate: wasCurrent });   // (print preparation settings: re-plan current paths)
}

// ---- the plan: fetched when the project changed -------------------------------------
// LIFECYCLE (reliability pass 2026-10-09): ONE current request (`sim.inflight`
// = {key, speedsKey, job, kind}); only that request's answer is applied, and
// "Planning…" is exactly "a current request exists" (never left stuck by a
// superseded one). A refresh for the keys already in flight joins it; an
// up-to-date refresh drops it. The server runs it as a BACKGROUND job: a
// newer job, an Assembly request (it preempts) or /print_plan/cancel (the
// page leaves the Simulation / the project is replaced) abandons it → 409
// cancelled; while the Simulation is still shown it simply asks again.
let _simJobSeq = 0;

async function _simKeys() {
  const designs = await window.Designer.designsForBackend();
  const st = JSON.parse(JSON.stringify(asm.state));
  const assembly = JSON.parse(JSON.stringify(asm.state));
  const wall_systems = JSON.parse(JSON.stringify(window.Designer.wallSystems ? window.Designer.wallSystems() : []));
  const speeds = st.print_speeds || {};
  delete st.print_speeds;
  return { designs, assembly, wall_systems, key: JSON.stringify([st, designs, wall_systems]),
           speedsKey: JSON.stringify(speeds) };
}

// WALL SYSTEMS GENERATION (2026-10-11): the plan consumes the CURRENT generated
// Wall System geometry only (the Wall Systems workspace) — never generated here
function _simWallSystemsStatus() {
  return window.WallSystemsProject ? window.WallSystemsProject.status() : 'current';
}

async function _simPost(body) {
  const res = await fetch('/api/assembly/print_plan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                       body: JSON.stringify(body) });
  const data = await res.json();
  if (data && data.cancelled) { const e = new Error('cancelled'); e.cancelled = true; throw e; }
  if (data && data.outdated) { const e = new Error(data.error); e.outdated = true; throw e; }
  if (data && data.error) throw new Error(data.error);
  return data;
}

function _simCancelInflight() {
  const R = sim.inflight;
  sim.inflight = null; sim.loading = false;
  if (R) fetch('/api/assembly/print_plan/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                   body: JSON.stringify({ job: R.job }) }).catch(() => {});
}

function _simVisible() { return typeof currentWorkspace === 'undefined' || currentWorkspace === 'simulation'; }

// where the playhead is, in terms that survive a new plan: a layer + the
// fraction of that layer's time
function _simPlace() {
  const D = sim.data;
  if (!D || !D.N) return null;
  const loc = simLocate(sim.t), [a, b] = simLayerRange(loc.layer);
  return { layer: loc.layer, f: b > a ? (sim.t - a) / (b - a) : 1, atEnd: sim.t >= D.total - 1e-9 };
}

function _simRestorePlace(P) {
  const D = sim.data, nL = D.res.plan.layers.length;
  sim.selLayer = Math.min(sim.selLayer, Math.max(0, nL - 1));
  sim.selRun = null;
  if (sim.mode === 'top' && !nL) sim.mode = '3d';
  if (!P || P.atEnd || !nL) { sim.t = D.total; return; }          // first view / was at the end: the finished building
  const L = Math.min(P.layer, nL - 1), [a, b] = simLayerRange(L);
  sim.t = Math.max(0, Math.min(D.total, a + (b - a) * P.f));
}

// is the shown plan for ANOTHER design state than the current one?
function simOutdated() {
  return !!sim.data && sim.currentKey !== null && sim.key !== sim.currentKey;
}

// the page's current design state (Assembly edits / Open / the Designer):
// the prepared paths may now be outdated — nothing is planned
function simNoteState(assembly, designs) {
  const st = JSON.parse(JSON.stringify(assembly));
  delete st.print_speeds;
  const ws = JSON.parse(JSON.stringify(window.Designer.wallSystems ? window.Designer.wallSystems() : []));
  sim.currentKey = JSON.stringify([st, designs, ws]);
  if (window.WallSystemsProject) window.WallSystemsProject.noteState();
}

// PLANNING: the user's explicit "Plan Print" (from the CURRENT generated Wall Systems)
function simGenerate() {
  sim.playing = false;
  return simRefresh({ generate: true });
}

// opts.generate: PLAN (print preparation) when the paths are missing or
// outdated; without it only re-TIME a current plan (speeds) — never plan
async function simRefresh(opts) {
  const rebuild = !!(opts && opts.rebuild);
  const generate = rebuild || !!(opts && opts.generate);
  const gen = ++sim.reqGen;
  let K;
  try { K = await _simKeys(); }
  catch (e) { if (gen === sim.reqGen) { sim.error = e.message; simRenderPanels(); simDraw(); } return; }
  if (gen !== sim.reqGen) return;                                    // a newer refresh took over
  sim.currentKey = K.key;
  if (!rebuild && sim.data && K.key === sim.key && K.speedsKey === sim.speedsKey) {   // up to date
    if (sim.inflight && sim.inflight.key !== K.key) { _simCancelInflight(); }
    simRenderPanels(); simDraw();
    return;
  }
  const I = sim.inflight;
  if (!rebuild && I && I.key === K.key && I.speedsKey === K.speedsKey) return I.promise;   // already asked
  if (!generate && !(sim.data && K.key === sim.key)) {               // (missing / outdated: wait for the user)
    simRenderPanels(); simDraw();
    return;
  }
  const kindNow = !rebuild && sim.data && K.key === sim.key ? 'times' : 'stream';
  if (kindNow === 'stream' && window.WallSystemsProject) {
    await window.WallSystemsProject.noteState();
    if (gen !== sim.reqGen) return;
    sim.wsBlocked = _simWallSystemsStatus() === 'current' ? null : _simWallSystemsStatus();
    if (sim.wsBlocked) { simRenderPanels(); simDraw(); return; }   // → generate in the Wall Systems workspace
  }
  if (I) sim.inflight = null;                    // superseded (the server drops it: a newer job)
  const kind = !rebuild && sim.data && K.key === sim.key ? 'times' : 'stream';
  const R = { key: K.key, speedsKey: K.speedsKey, job: 'sim-' + (++_simJobSeq) + '-' + Date.now(), kind };
  sim.inflight = R; sim.loading = true; sim.error = null;
  simRenderPanels(); simDraw();
  R.promise = (async () => {
    let retry = false;
    try {
      const body = { assembly: K.assembly, designs: K.designs, wall_systems: K.wall_systems, require_generated: true,
                     job: R.job, ...(rebuild ? { rebuild: true } : {}) };
      let r = await _simPost({ ...body, detail: kind });
      if (sim.inflight !== R) return;            // obsolete: never overwrite a newer state
      if (kind === 'times' && !simApplyTimes(r.t0, r.dt)) {      // (not the plan we hold: fetch it whole)
        r = await _simPost({ ...body, detail: 'stream' });
        if (sim.inflight !== R) return;
        R.kind = 'stream';
      }
      if (R.kind === 'times') sim.data.res.estimate = r.estimate;
      else {
        const P = _simPlace();
        sim.data = simIngest(r);
        sim.cache = null;
        sim.exportNote = null;                   // (a note about the previous plan's export)
        _simRestorePlace(P);                     // camera untouched; layer / playhead kept where practical
      }
      sim.key = K.key; sim.speedsKey = K.speedsKey;
      sim.error = null;
    } catch (e) {
      if (sim.inflight !== R) return;
      if (e.cancelled) retry = true;              // preempted (a full-geometry request): ask again
      else if (e.outdated) sim.wsBlocked = 'outdated';
      else sim.error = 'Print planning failed: ' + e.message;
    } finally {
      if (sim.inflight === R) {
        sim.inflight = null; sim.loading = false; simRenderPanels(); simDraw();
        if (typeof _asmStructureHead === 'function') _asmStructureHead();   // (the Assembly's final-check line)
      }
    }
    if (retry) await new Promise(ok => setTimeout(ok, 150)).then(() => simRefresh(opts));
  })();
  return R.promise;
}

// EXPORT PRINT JOB (2026-10-14): the CURRENT plan as an executable .mudjob
// (XY-only G-code in inches; Z and pump manual) — the only thing handed to
// the printer controller (contracts/mudjob). The server serializes the plan
// it already made for exactly this request; it never plans. Save Project
// (.muddesign) stays the authoring file.
async function simExportJob() {
  if (!sim.data || simOutdated() || sim.loading) return;
  let K;
  try { K = await _simKeys(); } catch (e) { sim.exportNote = 'Export failed: ' + e.message; simRenderPanels(); return; }
  if (K.key !== sim.key) {
    sim.exportNote = 'The design changed after this plan was made — plan the print again, then export.';
    simRenderPanels(); return;
  }
  const name = (typeof project !== 'undefined' && project.name) || '';
  let r;
  try {
    const res = await fetch('/api/assembly/print_job', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assembly: K.assembly, designs: K.designs, wall_systems: K.wall_systems, name,
                             machine_profile: typeof machineProfile === 'function' ? machineProfile() : undefined }) });
    r = await res.json();
    if (r.error) throw new Error(r.error);
  } catch (e) { sim.exportNote = 'Export failed: ' + e.message; simRenderPanels(); return; }
  _downloadText(r.filename, JSON.stringify(r.job, null, 1) + '\n');
  const n = r.job.lifts.length;
  sim.exportNote = `Exported ${r.filename} — ${n} lift${n === 1 ? '' : 's'}, ${r.lines} lines of XY-only G-code ` +
    `(inches, work coordinates from the print origin; Z and pump stay manual).` +
    (r.placeholder_speeds ? ' Uses PLACEHOLDER speeds — set measured print / travel speeds before printing.' : '') +
    (r.preflight ? (r.preflight.fits ? ` Prepared for printer "${r.job.prepared_for.name}": fits.`
      : ` Prepared for printer "${r.job.prepared_for.name}": does NOT fit (` +
        r.preflight.checks.filter(c => c.status === 'fail').map(c => c.id.toUpperCase()).join(', ') +
        ' exceeded) — exported anyway; the printer controller will check it against the real machine.') : '');
  if (typeof setStatus === 'function') setStatus(sim.exportNote);
  simRenderPanels();
  return r;
}

// RECOVERY: plan again from scratch (the server drops its cached plan +
// routes) — no page reload; project, camera, layer and playhead kept
function simRebuild() {
  sim.playing = false;
  return simRefresh({ rebuild: true });
}

// the project was REPLACED (Open / New Project): nothing of the old plan,
// selection or caches survives; replanned on the next show
function simReset() {
  sim.playing = false;
  if (sim.raf !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(sim.raf);
  sim.raf = null;
  _simCancelInflight();
  sim.reqGen++;
  Object.assign(sim, { data: null, key: null, speedsKey: null, error: null, paramError: null, wsBlocked: null, exportNote: null, N: 0, V: 0, t: 0, mode: '3d',
                       selLayer: 0, selRun: null, selSection: null, selLift: null, selStage: null, open: {}, cache: null,
                       drag: null, scrubbing: false });
  sim.view = { yaw: -Math.PI / 4, pitch: 0.55, zoom: 1, px: 0, py: 0 };
  sim.currentKey = null;
  simRenderPanels(); simDraw();
  if (typeof currentWorkspace !== 'undefined' && currentWorkspace === 'simulation') return simRefresh();
}

// showing the Simulation: the prepared paths as they are (current / outdated
// / none) — a current plan is re-timed for new speeds; nothing is planned
function simShow() { simRenderPanels(); simDraw(); return simRefresh(); }

// leaving the Simulation: playback stops (no frames while hidden). A print
// preparation in progress CONTINUES (it never holds up Assembly editing:
// envelope requests neither wait for it nor cancel it)
function simHide() {
  sim.playing = false; sim.drag = null; sim.scrubbing = false;
  if (sim.raf !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(sim.raf);
  sim.raf = null;
}

// ---- panels ------------------------------------------------------------------------
function _simT(sec) { return typeof _asmFmtTime === 'function' ? _asmFmtTime(sec) : Math.round(sec) + ' s'; }
function _simLabel(id) {
  const D = sim.data;
  if (id === null || id === undefined || id < 0) return '';
  const s = D.strings[id];
  return (D.res.labels && D.res.labels[s]) ? `${D.res.labels[s]} (${s})` : s;
}
function _simSystemName(id) {
  if (id === null || id === undefined || id < 0) return '';
  const s = sim.data.strings[id], sy = sim.data.res.systems && sim.data.res.systems[s];
  return sy ? sy.name : s;
}
function _simClear(el) { el.innerHTML = ''; if (Array.isArray(el.children)) el.children.length = 0; }

// a field's value from the state — never while the user is typing in it
// (the panels refresh every playback frame)
function _simSetField(inp, v) {
  if (!inp || document.activeElement === inp) return;
  if (inp.value !== String(v)) inp.value = v;
}

function simRenderPanels() {
  const D = sim.data, el = id => document.getElementById(id);
  const status = el('sim-status');
  if (status) {
    _simClear(status);
    if (sim.wsBlocked && !sim.loading) {
      const w = _el('div', 'asm-diff sim-ws-blocked',
        sim.wsBlocked === 'generating' ? 'The Wall Systems are being generated — plan the print when they are CURRENT.'
          : `The Wall Systems are ${sim.wsBlocked === 'outdated' ? 'OUTDATED' : 'NOT GENERATED'} for the current design. ` +
            'Generate them in the Wall Systems workspace — the print is planned from that geometry.');
      status.appendChild(w);
      const go = _el('button', 'tool-btn', 'Go to Wall Systems →');
      go.onclick = () => setWorkspace('wallsystems');
      status.appendChild(go);
    }
    if (sim.loading) status.appendChild(_el('div', 'asm-kv-row',
      'Planning the print from the generated Wall Systems (routing, lifts, islands, times)… the Assembly stays editable'));
    if (sim.error) status.appendChild(_el('div', 'asm-diff', sim.error));
    if (!D && !sim.loading && !sim.error && !sim.wsBlocked)
      status.appendChild(_el('div', 'asm-kv-row', 'No print plan yet. Plan it from the current generated Wall Systems.'));
    if (D && simOutdated())
      status.appendChild(_el('div', 'asm-diff sim-outdated',
        'OUTDATED — the Designer, Assembly or Wall Systems changed after this plan was made; it does not show the current design. Generate the Wall Systems again (Wall Systems workspace), then plan.'));
    if (D) status.appendChild(_el('div', 'asm-calc',
      `${D.res.plan.layers.length} layers · ${D.res.estimate.runs} runs · the authoritative print plan` +
      (simOutdated() ? ' (outdated)' : '')));
    // MACHINE PREFLIGHT: the plan's own requirements against the project's
    // printer (recomputed as the profile changes; the plan is never altered)
    if (D && D.res.requirements && typeof machinePreflight === 'function') {
      const mp = machineProfile(), pf = machinePreflight(D.res.requirements, mp);
      const bad = pf.checks.filter(c => c.status === 'fail'), warn = pf.checks.filter(c => c.status === 'warn');
      status.appendChild(_el('div', bad.length ? 'asm-diff' : 'asm-kv-row',
        `Printer "${mp.name}": ` + (bad.length ? `does NOT fit — ${bad.length} limit${bad.length === 1 ? '' : 's'} exceeded`
          : 'fits' + (warn.length ? ` (${warn.length} warning${warn.length === 1 ? '' : 's'})` : '')) +
        (pf.unchecked.length ? ` · not checked: ${pf.unchecked.join(', ')}` : '') + (simOutdated() ? ' (outdated)' : '')));
      for (const c of [...bad, ...warn]) status.appendChild(_el('div', 'asm-calc', c.message));
    }
    const S = D && D.res.support;
    if (S) {                                  // the FINAL bead-level check of these print paths
      const open = S.findings.filter(f => f.status === 'needs_header' || f.status === 'insufficient_support');
      status.appendChild(_el('div', open.length ? 'asm-diff' : 'asm-kv-row',
        `Bead-level support (final): ${open.length ? `${open.length} open finding${open.length === 1 ? '' : 's'} — ` +
          `${S.needs_header} header needed · ${S.insufficient_support} insufficient` : 'every layer supported'}` +
        (simOutdated() ? ' (outdated)' : '')));
      for (const f of open.slice(0, 12))
        status.appendChild(_el('div', 'asm-calc',
          `layer ${f.layer + 1} · ${f.kind === 'bridge' ? 'HEADER NEEDED' : 'INSUFFICIENT SUPPORT'} · ${Math.round(f.span * 10) / 10} in`));
      if (open.length > 12) status.appendChild(_el('div', 'asm-calc', `… ${open.length - 12} more`));
    }
  }
  const gb = el('sim-generate');
  if (gb) {
    gb.textContent = sim.loading ? 'Planning…' : (D && simOutdated() ? 'Plan Print Again' : 'Plan Print');
    gb.disabled = !!sim.loading;
  }
  const xb = el('sim-export-job');
  if (xb) xb.disabled = !(D && !simOutdated() && !sim.loading && !sim.error);
  const xn = el('sim-export-note');
  if (xn) xn.textContent = sim.exportNote || (D && !simOutdated() ? '' : 'Export Print Job needs a current print plan.');
  const play = el('sim-play');
  if (play) play.textContent = sim.playing ? '⏸' : '▶';
  const rate = el('sim-rate');
  if (rate) rate.value = String(sim.rate);
  const u = asm.speedUnit || 'in/s', sp = _asmSpeeds(), conv = ASM_SPEED_UNITS[u][1];
  const r3 = v => Math.round(v * 1000) / 1000;
  _simSetField(el('sim-print-speed'), r3(conv(sp.print)));
  _simSetField(el('sim-travel-speed'), r3(conv(sp.travel)));
  _simSetField(el('sim-frame-time'), _asmFrameMinutes());
  if (el('sim-speed-unit')) el('sim-speed-unit').value = u;
  if (el('sim-travel-unit')) el('sim-travel-unit').textContent = u;
  _simRenderLiftSettings();
  if (!D) return;
  const E = D.res.estimate, [ra, rb] = simRange(), uu = _simToLocal(sim.t);
  const scrub = el('sim-scrub');
  if (scrub) scrub.value = rb > ra ? Math.round((uu - ra) / (rb - ra) * 10000) : 0;
  const loc = D.N ? simLocate(sim.t) : null;
  const remaining = Math.max(0, D.total - sim.t);
  if (el('sim-time')) el('sim-time').textContent = sim.mode === 'top'
    ? `Layer ${sim.selLayer + 1}: ${_simT(uu - ra)} / ${_simT(rb - ra)}`
    : `${_simT(sim.t)} / ${_simT(D.total)} · ${_simT(remaining)} left`;
  _simRenderMarks();
  const PL = D.res.plan, lifts = PL.lifts || [], stages = PL.stages || [];
  // totals
  const times = el('sim-times');
  if (times) {
    _simClear(times);
    times.appendChild(_el('div', 'asm-kv-row', `Total: ${_simT(E.total_time)}`));
    times.appendChild(_el('div', 'asm-calc', `printing ${_simT(E.print_time)} · travel ${_simT(E.travel_time)}`));
    times.appendChild(_el('div', 'asm-calc', `frame repositioning ${E.frame_moves || 0}× · ` +
      (E.frame_moves ? (E.frame_time ? _simT(E.frame_time) : 'time not estimated') : 'none (one lift)')));
    const c = _el('div', 'asm-calc', 'Estimate at constant speeds — not machine timing.');
    c.title = 'Path lengths at the print / travel speeds plus the estimated frame repositions; no acceleration, pump response or Z moves. 1× playback takes this long.';
    times.appendChild(c);
  }
  // the playhead: where the nozzle is in the schedule
  const now = el('sim-now');
  if (now) {
    _simClear(now);
    now.appendChild(_el('div', 'asm-kv-row', `${_simT(sim.t)} done · ${_simT(remaining)} remaining`));
    if (loc && lifts[loc.lift]) {
      const Lf = lifts[loc.lift], st = stages[loc.stage];
      now.appendChild(_el('div', 'asm-kv-row', `Lift ${loc.lift + 1} of ${lifts.length} · z ${Lf.z_bottom.toFixed(2)}–${Lf.z_top.toFixed(2)} in`));
      now.appendChild(_el('div', 'asm-calc', `frame base z ${Lf.frame_z.toFixed(2)} in · ${Lf.layers} layers (${Lf.first_layer + 1}–${Lf.last_layer + 1})`));
      if (st) now.appendChild(_el('div', 'asm-kv-row', `${_simStageLabel(st)} · layer ${loc.layer + 1}`));
      if (loc.kind === SIM_KIND.FRAME)
        now.appendChild(_el('div', 'asm-diff', `Frame reposition: raise the XY frame to z ${Lf.z_bottom.toFixed(2)} in — lift ${loc.lift} is complete`));
      else if (loc.kind === SIM_KIND.TRANSITION && D.z1[loc.i] < D.z0[loc.i] - 1e-9)
        now.appendChild(_el('div', 'asm-calc', `Moving DOWN to ${st ? 'island ' + st.name : 'another island'} · layer ${loc.layer + 1} — same lift` +
          (D.zc[loc.i] > D.z0[loc.i] + 1e-6 ? ` (over finished geometry at z ${D.zc[loc.i].toFixed(1)})` : '')));
      else now.appendChild(_el('div', 'asm-calc', `${loc.kind <= 1 ? 'Extruding' : (loc.kind === 2 ? 'Travelling' : 'Moving to the next piece')}` +
        `${loc.run >= 0 ? ' · run ' + (loc.run + 1) : ''}` + (D.source[loc.i] >= 0 ? ' · ' + _simLabel(D.source[loc.i]) : '')));
    }
  }
  const nL = D.res.plan.layers.length;
  _simSetField(el('sim-layer-input'), sim.selLayer + 1);
  if (el('sim-layer-count')) el('sim-layer-count').textContent = `/ ${nL}`;
  // the selected layer
  const Ls = D.res.plan.layers[sim.selLayer], Le = E.layers[sim.selLayer];
  const info = el('sim-layer-info');
  if (info && Ls && Le) {
    _simClear(info);
    const sec = D.res.sections.find(s => s.index === Ls.section_index);
    info.appendChild(_el('div', 'asm-kv-row', `Layer ${sim.selLayer + 1} · z ${Ls.z_bottom.toFixed(2)}–${Ls.z_top.toFixed(2)} in`));
    info.appendChild(_el('div', 'asm-calc', `Section ${Ls.section_index + 1} · ${(D.res.designs || {})[Ls.design_id] || Ls.design_id}` +
      (sec && sec.label ? ` (${sec.label})` : '')));
    if (lifts[Ls.lift]) info.appendChild(_el('div', 'asm-calc', `Lift ${Ls.lift + 1} · ${Ls.components} piece${Ls.components === 1 ? '' : 's'} · ` +
      `printed in ${Ls.passes.length} pass${Ls.passes.length === 1 ? '' : 'es'}: ` +
      Ls.passes.map(p => (stages[p.stage] ? stages[p.stage].name : '?')).join(', ')));
    info.appendChild(_el('div', 'asm-calc', `${Ls.runs} run${Ls.runs === 1 ? '' : 's'} · printing ${Le.print_length.toFixed(1)} in · ` +
      `travel ${(Le.travel_length + Le.transition_length).toFixed(1)} in`));
    info.appendChild(_el('div', 'asm-calc', `Estimated ${_simT(Le.total_time)} (printing ${_simT(Le.print_time)} · travel ${_simT(Le.travel_time)})`));
    if (Ls.manual_start || Ls.reversed || (Ls.origins || []).length)
      info.appendChild(_el('div', 'asm-calc', 'Manual routing: ' + [Ls.manual_start ? 'start point' : '', Ls.reversed ? 'reversed' : '',
        (Ls.origins || []).length ? 'Route Origin' : ''].filter(Boolean).join(', ')));
  }
  const secs = el('sim-sections');
  if (secs) {
    _simClear(secs);
    for (const s of E.sections)
      secs.appendChild(_el('div', 'asm-calc', `Section ${s.index + 1}: ${s.layers} layers · ${_simT(s.total_time)}` +
        ` (extrusion ${_simT(s.print_time)} · travel ${_simT(s.travel_time)})`));
  }
  if (el('sim-sel-head')) el('sim-sel-head').textContent = sim.selRun !== null ? `Selected: layer ${sim.selLayer + 1}, run ${sim.selRun + 1}` : `Selected layer ${sim.selLayer + 1}`;
  const runs = el('sim-runs');
  if (runs && Le) {
    _simClear(runs);
    const stageOfRun = {};
    for (const p of (Le.passes || [])) for (const ri of p.runs) stageOfRun[ri] = stages[p.stage];
    Le.runs.forEach(r => {
      const row = _el('div', 'asm-calc sim-run-row' + (sim.selRun === r.index ? ' sel' : ''),
        `Run ${r.index + 1}: ${r.length.toFixed(1)} in · ${_simT(r.time)}` +
        (stageOfRun[r.index] && (Le.passes || []).length > 1 ? ` · island ${stageOfRun[r.index].name}` : '') +
        ((r.sources || []).length ? ` · ${r.sources.map(x => (D.res.labels || {})[x] || x).join(', ')}` : '') +
        ((r.systems || []).length ? ` · ${r.systems.map(x => ((D.res.systems || {})[x] || {}).name || x).join(', ')}` : ''));
      row.onclick = () => simSelectRun(sim.selLayer, r.index);
      runs.appendChild(row);
    });
    if (Le.transition_length > 0 || Le.travel_length > 0)
      runs.appendChild(_el('div', 'asm-calc', `Travel in the layer ${Le.travel_length.toFixed(1)} in · into its passes ${Le.transition_length.toFixed(1)} in`));
  }
  _simRenderTree();
}

// the Lifts block of the Print plan column: values, the lift height capped at
// the max Z travel, and the lift boundaries they give (whole layers — the
// planner's rule; from the resolved stack, or the plan's layers)
function _simRenderLiftSettings() {
  const el = id => document.getElementById(id), M = _asmMachine();
  _simSetField(el('sim-max-z'), M.max_z_travel);
  _simSetField(el('sim-lift-height'), M.lift_height);
  _simSetField(el('sim-head-clearance'), M.head_clearance);
  if (el('sim-lift-height')) el('sim-lift-height').max = M.max_z_travel;
  const li = el('sim-lift-info');
  if (li) {
    const layers = (asm.resolved && asm.resolved.instances && asm.resolved.instances.length) ? asm.resolved.instances
      : ((sim.data && sim.data.res.plan.layers) || []);
    const b = layers.length ? _asmLiftBounds(layers, M.lift_height) : [];
    li.textContent = b.length
      ? `${b.length} lift${b.length === 1 ? '' : 's'} (frame raised ${b.length - 1}×): ` +
        b.map(([i, j]) => `${layers[i].z_bottom.toFixed(2)}–${layers[j].z_top.toFixed(2)}`).join(', ') + ' in'
      : '';
    li.title = 'Actual lifts: whole layers up to the preferred lift height';
  }
  const er = el('sim-param-error');
  if (er) {
    _simClear(er);
    if (sim.paramError) er.appendChild(_el('div', 'asm-diff', sim.paramError));
  }
}

function _simStageLabel(st) {
  return st.kind === 'merge' ? `Islands ${st.name} (merged)` : (st.kind === 'split' ? `Branch ${st.name}` : `Island ${st.name}`);
}

// LIFT BOUNDARIES on the timeline (3D: the whole print): a tick per frame reposition
function _simRenderMarks() {
  const D = sim.data, box = document.getElementById('sim-marks');
  if (!box) return;
  const key = sim.mode === 'top' ? 'top' : JSON.stringify([sim.key, sim.speedsKey, D.frames.length, D.total]);
  if (box._key === key) return;
  box._key = key;
  _simClear(box);
  if (sim.mode === 'top' || !D.total) return;
  for (const i of D.frames) {
    const m = _el('div', 'sim-mark', '');
    m.style.left = (100 * D.t0[i] / D.total) + '%';
    m.title = `frame reposition → lift ${D.lift[i] + 1}`;
    box.appendChild(m);
  }
}

// Building → Lift → Island / connected stage → Layer → Run (the right column; durations per node)
function _simRenderTree() {
  const D = sim.data, box = document.getElementById('sim-tree');
  if (!box || !D) return;
  _simClear(box);
  const E = D.res.estimate, PL = D.res.plan;
  const node = (text, sel, onclick, time) => {        // label (ellipsis) + its duration (always visible)
    const n = _el('div', 'sim-node' + (sel ? ' sel' : ''), '');
    n.appendChild(_el('span', 'sim-node-label', text));
    if (time !== undefined) n.appendChild(_el('span', 'sim-node-time', _simT(time)));
    n.onclick = onclick;
    return n;
  };
  box.appendChild(node(`Building · ${(E.lifts || []).length} lift${(E.lifts || []).length === 1 ? '' : 's'}`, false, () => { sim.selSection = null; sim3D(); simSetTime(D.total); }, E.total_time));
  for (const Lf of (E.lifts || [])) {
    const open = !!sim.open['f' + Lf.index];
    const ln = node(`${open ? '▾' : '▸'} Lift ${Lf.index + 1} · z ${Lf.z_bottom.toFixed(1)}–${Lf.z_top.toFixed(1)} · ${Lf.layers} layers`,
      sim.selLift === Lf.index && sim.selStage === null, () => { sim.open['f' + Lf.index] = !open; simSelectLift(Lf.index); }, Lf.total_time);
    ln.title = `Lift ${Lf.index + 1}: layers ${Lf.first_layer + 1}–${Lf.last_layer + 1} · printing ${_simT(Lf.print_time)} · travel ${_simT(Lf.travel_time)}` +
      (Lf.reposition_in ? ` · frame reposition before it ${Lf.frame_time ? _simT(Lf.frame_time) : '(not estimated)'}` : '');
    box.appendChild(ln);
    if (!open) continue;
    const kids = _el('div', 'sim-kids', '');
    if (Lf.reposition_in) kids.appendChild(_el('div', 'asm-calc sim-frame-node',
      `⇡ frame raised to z ${Lf.z_bottom.toFixed(1)} in${Lf.frame_time ? ' · ' + _simT(Lf.frame_time) : ''}`));
    for (const si of Lf.stages) {
      const st = E.stages[si];
      if (!st) continue;
      const so = !!sim.open['g' + si];
      const sn = node(`${so ? '▾' : '▸'} ${_simStageLabel(st)} · layers ${st.first_layer + 1}–${st.last_layer + 1}`,
        sim.selStage === si, () => { sim.open['g' + si] = !so; simSelectStage(si); }, st.total_time);
      if (st.print_time !== undefined) sn.title = `printing ${_simT(st.print_time)} · travel ${_simT(st.travel_time)}`;
      kids.appendChild(sn);
      if (!so) continue;
      const lk = _el('div', 'sim-kids', '');
      for (let L = st.first_layer; L <= st.last_layer; L++) {
        const El = E.layers[L];
        if (!El) continue;
        const pass = (El.passes || []).find(p => p.stage === si);
        if (!pass) continue;
        const runs = El.runs.filter(r => pass.runs.includes(r.index));
        const t = runs.reduce((a, r) => a + r.time, 0);
        const lo = !!sim.open['l' + si + '_' + L];
        lk.appendChild(node(`${lo ? '▾' : '▸'} Layer ${L + 1} · ${runs.length} run${runs.length === 1 ? '' : 's'}`,
          sim.selLayer === L && sim.selRun === null && sim.mode === 'top', () => {
            sim.open['l' + si + '_' + L] = !lo; sim.mode = 'top'; simSelectLayer(L); }, t));
        if (lo) {
          const rk = _el('div', 'sim-kids', '');
          for (const r of runs)
            rk.appendChild(node(`Run ${r.index + 1} · ${r.length.toFixed(0)} in`,
              sim.selLayer === L && sim.selRun === r.index, () => simSelectRun(L, r.index), r.time));
          lk.appendChild(rk);
        }
      }
      kids.appendChild(lk);
    }
    box.appendChild(kids);
  }
}

// ---- pointer / keys -----------------------------------------------------------------
function _simInit() {
  const cv = document.getElementById('sim-canvas');
  if (!cv || !cv.addEventListener) return;
  cv.addEventListener('mousedown', e => { sim.drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey }; });
  window.addEventListener('mousemove', e => {
    if (!sim.drag) return;
    const dx = e.clientX - sim.drag.x, dy = e.clientY - sim.drag.y;
    sim.drag.x = e.clientX; sim.drag.y = e.clientY;
    if (sim.drag.pan || sim.mode === 'top') { sim.view.px += dx; sim.view.py += dy; }
    else {
      sim.view.yaw += dx * 0.01;
      sim.view.pitch = Math.max(-0.1, Math.min(Math.PI / 2, sim.view.pitch + dy * 0.01));
    }
    simDraw();
  });
  window.addEventListener('mouseup', () => { if (sim.drag) { sim.drag = null; simDraw(); } });
  cv.addEventListener('wheel', e => {
    if (e.preventDefault) e.preventDefault();
    sim.view.zoom = Math.max(0.2, Math.min(40, sim.view.zoom * Math.exp(-(e.deltaY || 0) * 0.0015)));
    simDraw();
  }, { passive: false });
  window.addEventListener('resize', () => { if (typeof currentWorkspace !== 'undefined' && currentWorkspace === 'simulation') simDraw(); });
}

document.addEventListener('keydown', e => {
  if (typeof currentWorkspace === 'undefined' || currentWorkspace !== 'simulation') return;
  if (typeof _isTyping === 'function' && _isTyping()) return;
  if (e.key === ' ') { if (e.preventDefault) e.preventDefault(); simTogglePlay(); }
  else if (e.key === 'ArrowRight') simStepLayer(1);
  else if (e.key === 'ArrowLeft') simStepLayer(-1);
});

if (window.Designer) {
  _simInit();
  window.Designer.onWorkspaceShow('simulation', () => simShow());
  if (window.Designer.onWorkspaceHide) window.Designer.onWorkspaceHide('simulation', () => simHide());
}
window.SimulationProject = { reset: simReset, noteState: simNoteState, exportJob: simExportJob };
