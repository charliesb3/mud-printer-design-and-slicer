// UI smoke test of the PRINT SIMULATION workspace: the real static/app.js +
// assembly.js + simulation.js against the REAL backend (the print plan
// endpoint), DOM stubbed. Run via layer_assembly/tests/test_simulation_ui.py.
// (harness from design_proto/tests/js/ui_trim_smoke.js)
// UI smoke test for the Trim tool: loads static/app.js against a permissive
// DOM/canvas stub and drives the real interaction functions — hover picks
// ONE section, click stores its signature as one undo step, stale sections
// are never offered, Esc / tool switch exits, selection outside Trim is
// unchanged, Restore, delete / duplicate / Clear All, payload.
// The sections normally come from the backend (trim.py); here they are
// injected as the API returns them. Run via tests/test_trim.py.
const fs = require('fs');
const path = require('path');
function stub(name) {
  const fn = function () { return stub(name + '()'); };
  return new Proxy(fn, {
    get(t, k) {
      if (k === Symbol.toPrimitive) return () => 0;
      if (k === 'then') return undefined;
      if (k in t) return t[k];
      return (t[k] = stub(name + '.' + String(k)));
    },
    set(t, k, v) { t[k] = v; return true; },
  });
}
const elements = {};
const listeners = {};
global.document = {
  getElementById: id => (elements[id] = elements[id] || Object.assign(stub(id), {
    style: {}, classList: { toggle() {}, add() {}, remove() {} }, value: '0', id,
    children: [], innerHTML: '', appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 800 }),
    getContext: () => stub('ctx'), addEventListener() {},
    parentElement: { clientWidth: 800, clientHeight: 800, getBoundingClientRect: () => ({ width: 800, height: 800 }) },
  })),
  createElement: tag => Object.assign(stub(tag), { style: {}, children: [], className: '',
    appendChild(c) { this.children.push(c); return c; }, append(...c) { this.children.push(...c); },
    querySelector: () => ({ disabled: false }), set lastChild(v) {}, get lastChild() { return this.children[this.children.length - 1]; } }),
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); }, body: {}, activeElement: null,
};
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.requestAnimationFrame = () => 0; global.cancelAnimationFrame = () => {};
const BASE = process.argv[2];                 // the real backend (started by the pytest wrapper)
const realFetch = fetch;
const calls = [];
// fetchHook(url, body): may delay a request (await) or answer it itself (return a fake response)
let fetchHook = null;
global.fetch = async (url, opts) => { let body = {}; try { body = JSON.parse(opts.body) || {}; } catch (e) {}
  calls.push({ url, detail: body.detail || null, job: body.job, rebuild: !!body.rebuild, envelope: !!body.envelope });
  if (fetchHook) { const r = await fetchHook(url, body); if (r) return r; }
  return realFetch(BASE + url, opts); };
global.console.warn = () => {};
const realSetTimeout = setTimeout;
// the Designer's background refreshes (route / effective paths) are not part
// of this test and must not race it; every other timer is real
global.setTimeout = (fn, ms, ...a) => (fn && /^(runRoute|fetchEffectivePaths)$/.test(fn.name))
  ? { unref() {}, ref() {} } : realSetTimeout(fn, ms, ...a);
const STATIC = path.join(__dirname, '..', '..', '..', 'design_proto', 'static');
let src = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
src += '\n' + fs.readFileSync(path.join(STATIC, 'opening_shape.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'assembly.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'wallsystems.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'simulation.js'), 'utf8');
eval(src + `
function firstTransI() { const D = sim.data; return Array.from(D.kind).findIndex((k, i) => k === SIM_KIND.TRANSITION && D.z1[i] > D.z0[i]); }
;(async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const el = id => document.getElementById(id);
  const flat = n => [n, ...(n.children || []).flatMap(flat)];
  const txt = id => flat(el(id)).map(n => n.textContent || '').join(' | ');
  const plans = d => calls.filter(c => c.url === '/api/assembly/print_plan' && (!d || c.detail === d)).length;
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  check(html.includes('id="ws-tab-simulation"') && html.includes('id="ws-simulation"') && html.includes('src="/static/simulation.js"') &&
        html.indexOf('ws-tab-simulation') > html.indexOf('ws-tab-assembly'), 'third workspace: Designer | Assembly | Print Simulation');
  check(['sim-play', 'sim-restart', 'sim-scrub', 'sim-rate', 'sim-top-btn', 'sim-prev-layer', 'sim-next-layer', 'sim-tree',
         'sim-print-speed', 'sim-travel-speed', 'sim-times', 'sim-sections', 'sim-runs', 'sim-ghost', 'sim-travel'].every(id => html.includes('id="' + id + '"')),
        'transport, layer inspection, navigation tree and time sidebar controls');
  {
    const at = id => html.indexOf('id="' + id + '"'), ws = html.slice(html.indexOf('id="ws-simulation"'));
    const left = ws.indexOf('sim-nav-col'), mid = ws.indexOf('class="sim-center"'), right = ws.indexOf('sim-time-col');
    const col = id => { const k = ws.indexOf('id="' + id + '"'); return k < 0 ? null : (k < mid ? 'left' : (k > right ? 'right' : 'centre')); };
    check(left < mid && mid < right && ['sim-print-speed', 'sim-speed-unit', 'sim-travel-speed', 'sim-max-z', 'sim-lift-height',
          'sim-head-clearance', 'sim-lift-info', 'sim-frame-time', 'sim-rebuild'].every(id => col(id) === 'left'),
          'LEFT column (Print plan): every editable print-planning parameter');
    check(['sim-times', 'sim-now', 'sim-tree', 'sim-layer-info', 'sim-runs', 'sim-sections', 'sim-layer-input'].every(id => col(id) === 'right') &&
          col('sim-canvas') === 'centre' && col('sim-scrub') === 'centre' && !html.includes('id="sim-lifts"'),
          'RIGHT column (Time estimates): totals, playhead, the Building → Lift → Island → Layer → Run tree, selection; preview in the centre');
    check(at('sim-max-z') < at('sim-lift-height') && at('sim-lift-height') < at('sim-frame-time') &&
          html.includes('>Frame reposition time<') && !html.includes('>Frame move<'),
          'Max Z travel, then lift height; "Frame reposition time" (minutes) last');
    check(!['asm-print-speed', 'asm-travel-speed', 'asm-max-z', 'asm-lift-height', 'asm-frame-time', 'asm-plan'].some(id => html.includes('id="' + id + '"')),
          'no second copy of the settings / estimate in Assembly');
  }
  check(html.includes('<option value="1">1× real speed</option>') && html.includes('value="3600"'), '1× real speed and faster playback options');

  // ---- a project: a Hollow rectangle (Wall System) + a separate circle (another run: travel); Base + a derived design ----
  addPrimitive('RectanglePath');
  const R = layer.source_paths[0];
  _wgNew([R.id]);                                      // a 10 in Wall Group (Hollow)
  addPrimitive('CirclePath');
  const Cp = layer.source_paths[1]; Cp.cx = 420; Cp.cy = 200; _computePrimitivePoints(Cp);
  layer.material.physical = true;
  historyCheckpoint();
  const d1 = createDerivedDesign('Upper', 'base');
  asm.state.sections = [{ design_id: 'base', height: 6 }, { design_id: d1, height: 4.5 }];
  asm.state.objects = [{ kind: 'header', id: 'h1', section: 1, layer: 0, x: 200, y: 140, angle: 0, span: 30,
                         bearing: 8, depth: 10, thickness: 6, snap: true }];

  // ---- the simulation consumes the print plan ----
  setWorkspace('simulation');
  check(currentWorkspace === 'simulation' && el('ws-simulation').style.display === '' && el('ws-assembly').style.display === 'none',
        'Print Simulation shown, the other workspaces hidden');
  await simShow();
  check(!sim.data && plans() === 0 && /No print plan yet/.test(txt('sim-status')) &&
        el('sim-generate').textContent === 'Plan Print' && html.includes('id="sim-generate"'),
        'showing the Simulation plans nothing — "Plan Print" is offered');
  await simGenerate();
  check(!sim.data && plans() === 0 && sim.wsBlocked === 'not_generated' && /NOT GENERATED/.test(txt('sim-status')) &&
        /Go to Wall Systems/.test(txt('sim-status')),
        'WALL SYSTEMS: the print is planned only from generated Wall Systems — NOT GENERATED sends the user to that workspace');
  check(html.indexOf('ws-tab-wallsystems') > html.indexOf('ws-tab-assembly') &&
        html.indexOf('ws-tab-wallsystems') < html.indexOf('ws-tab-simulation'),
        'workspace order: Designer → Assembly → Wall Systems → Print Simulation');
  await wsysGenerate();
  check(wsysStatus() === 'current' && wsys.gen.layers.length === 7, 'Generate Wall Systems: every layer generated (CURRENT)');
  await simGenerate();
  const D = sim.data;
  check(D && plans('stream') === 1 && !sim.wsBlocked,
        'Plan Print: the plan of the CURRENT generated geometry, fetched once as the compact stream (/api/assembly/print_plan)');
  check(D.res.support && D.res.support.level === 'bead' && /Bead-level support [(]final[)]/.test(txt('sim-status')) && !simOutdated(),
        'the FINAL bead-level support check comes with the prepared print paths (current)');
  check(D.res.plan.layers.length === 7 && D.res.sections.length === 2 && D.res.plan.layers[6].design_id === d1,
        '7 layers in 2 sections (Base, the derived design) — the resolved stack');
  check(Math.abs(D.total - D.res.estimate.total_time) < 1e-3, 'the playback clock ends at the planner estimate (same timing rule)');
  check(sim.t === D.total, 'opened on the finished building');

  // ---- EXPORT PRINT JOB (.mudjob): the current plan, a file separate from the project ----
  check(!el('sim-export-job').disabled, 'Export Print Job is offered for the current, successfully planned print');
  const _dl = _downloadText, nPlans = plans();
  let job = null;
  _downloadText = (name, text) => { job = { name, text }; };
  project.name = 'Smoke job';
  await simExportJob();
  const J = JSON.parse(job.text);
  check(job.name === 'Smoke job.mudjob' && J.format === 'mudprinter-job' && J.version === 1 && J.name === 'Smoke job' &&
        J.lifts.length === D.res.plan.lifts.length && J.lifts.every((L, i) => L.index === i) &&
        J.lifts[J.lifts.length - 1].layers.last === 6 && plans() === nPlans,
        'Export Print Job downloads <project>.mudjob: the lifts of the plan in order, nothing re-planned');
  check(J.executable === false && J.unresolved.length === 5 && J.lifts.every(L => !('gcode' in L)) &&
        /NOT EXECUTABLE/.test(txt('sim-export-note')) && /machine_units/.test(txt('sim-export-note')),
        'unresolved machine decisions are reported (file + note); no G-code is produced');
  _downloadText = _dl; project.name = '';

  // ---- playback ordering ----
  let ordered = true, schedule = true, liftsUp = true;
  for (let i = 1; i < D.N; i++) {
    if (!(D.t0[i] >= D.t0[i - 1] - 1e-9) || Math.abs(D.t0[i - 1] + D.dt[i - 1] - D.t0[i]) > 1e-4) ordered = false;
    if (D.pass[i] < D.pass[i - 1]) schedule = false;
    if (D.lift[i] < D.lift[i - 1]) liftsUp = false;
  }
  const passSeq = []; for (let i = 0; i < D.N; i++) if (!passSeq.length || passSeq[passSeq.length - 1] !== D.pass[i]) passSeq.push(D.pass[i]);
  check(ordered && schedule && liftsUp && JSON.stringify(passSeq) === JSON.stringify(D.res.plan.passes.map(p => p.index)) &&
        Array.from(D.layer).every((L, i) => D.res.plan.passes[D.pass[i]].layer === L),
        'items play in the EXACT print schedule (pass order, contiguous times, never back to an earlier lift)');
  // three separate pieces — the Hollow wall's two skins (7 in apart, no web) and the circle: each built
  // up in turn, then back DOWN to the next (same lift)
  const PS = D.res.plan.passes, ST = D.res.plan.stages;
  check(ST.length === 3 && ST.map(x => x.name).join() === 'A,B,C' && [0, 7, 14].every(k => PS.slice(k, k + 7).every(p => p.stage === PS[k].stage) &&
        PS.slice(k, k + 7).map(p => p.layer).join() === '0,1,2,3,4,5,6') && D.res.plan.layers.every(L => L.components === 3),
        'islands A, B, C each built layers 1–7 in turn (not alternating)');
  const downI = Array.from(D.kind).findIndex((k, i) => k === SIM_KIND.TRANSITION && D.z1[i] < D.z0[i]);
  check(downI > 0 && D.layer[downI] === 0 && D.lift[downI] === 0 && _simStyle(downI, '3d').down &&
        _simStyle(downI, '3d').color !== _simStyle(firstTransI(), '3d').color,
        'returning DOWN to the other island (same lift) is drawn distinctly from a layer climb');
  simSetTime(D.t0[downI] + D.dt[downI] / 2);
  check(/Moving DOWN to island B/.test(txt('sim-now')) && /Lift 1 of 1/.test(txt('sim-now')) && simLocate(sim.t).z >= D.z0[downI] - 1e-9,
        'the status names the island and the lift; the nozzle moves at the clear height');
  let prevI = -1, mono = true;
  for (let k = 0; k <= 200; k++) { const l = simLocate(D.total * k / 200); if (l.i < prevI) mono = false; prevI = l.i; }
  check(mono, 'scrubbing forward never moves backwards in the plan');
  const firstTrans = Array.from(D.kind).indexOf(SIM_KIND.TRANSITION);
  check(firstTrans > 0 && D.layer[firstTrans] === 1 && D.z0[firstTrans] < D.z1[firstTrans],
        'layer transitions are moves of the plan (climbing to the next layer)');
  // (each piece here is one closed run: the non-printing XY moves are the transitions between islands)
  const travelI = Array.from(D.kind).findIndex((k, i) => (k === SIM_KIND.TRAVEL || k === SIM_KIND.TRANSITION) &&
    Math.hypot(D.xy[2 * D.off[i] + 2] - D.xy[2 * D.off[i]], D.xy[2 * D.off[i] + 3] - D.xy[2 * D.off[i] + 1]) > 1);
  check(travelI >= 0, 'travel between the disconnected runs is in the playback');
  const ts = _simStyle(travelI, '3d'), ps = _simStyle(Array.from(D.kind).indexOf(SIM_KIND.PRINT), '3d');
  check(ts.dash && !ps.dash && ts.color !== ps.color && _simStyle(firstTrans, '3d').dash, 'travel / transitions drawn dashed in their own colours, extrusion solid');
  const mid = D.t0[travelI] + D.dt[travelI] / 2, lt = simLocate(mid);
  const o = D.off[travelI];
  check(lt.i === travelI && Math.abs(lt.x - (D.xy[2 * o] + D.xy[2 * o + 2]) / 2) < 1e-6,
        'the nozzle at mid-travel is half way along it (arc-length interpolation of the planned move)');

  // ---- play / pause / restart / speed ----
  const frames = []; global.requestAnimationFrame = f => { frames.push(f); return frames.length; };
  simRestart();
  check(sim.t === 0 && !sim.playing, 'Restart: back to the start, paused');
  simSetRate(60);
  simTogglePlay();
  check(sim.playing && el('sim-play').textContent === '⏸', 'Play');
  frames.shift()(1000); frames.shift()(2000);                   // 1 s of wall time
  check(Math.abs(sim.t - 60) < 1e-6, '60×: one second of playback = one minute of estimated machine time');
  simSetRate(1); frames.shift()(3000); frames.shift()(4000);    // (a rate change restarts the frame clock)
  check(Math.abs(sim.t - 61) < 1e-6, '1×: real time');
  simTogglePlay();
  check(!sim.playing, 'Pause');
  const fr0 = sim.stats.fullRedraws, it0 = sim.stats.itemsDrawn;
  simSetTime(sim.t + 1); simSetTime(sim.t + 1);
  check(sim.stats.fullRedraws === fr0 && sim.stats.itemsDrawn - it0 <= 4,
        'playing forward draws only the newly finished moves (cached static geometry)');
  simScrub(5000, false);
  check(Math.abs(sim.t - D.total / 2) < 1e-6 && el('sim-time').textContent.includes('left'), 'timeline scrubbing (whole print)');

  // ---- time sidebar ----
  const tt = txt('sim-times');
  check(/Total:/.test(tt) && /printing/.test(tt) && /travel/.test(tt) && /frame repositioning/.test(tt) && /not machine timing/.test(tt) &&
        !/remaining/.test(tt), 'totals: printing vs travel vs frame repositioning, estimate caveat');
  check(/remaining/.test(txt('sim-now')) && /done/.test(txt('sim-now')), 'done / remaining at the playhead (Playhead block, once)');
  check(D.res.estimate.sections.length === 2 && /Section 1:/.test(txt('sim-sections')) && /Section 2:/.test(txt('sim-sections')),
        'per-section durations');
  const secSum = D.res.estimate.sections.reduce((a, s) => a + s.total_time, 0);
  check(Math.abs(secSum - D.total) < 1e-3, 'section durations add up to the total');

  // ---- layer inspection ----
  simSelectLayer(2);
  simTopView();
  const [la, lb] = simRange();
  check(sim.mode === 'top' && la === 0 && Math.abs(_simToLocal(sim.t) - lb) < 1e-9 && D.layerItems[2].length > 0 &&
        new Set(Array.from(D.layerItems[2]).map(i => D.pass[i])).size === 3,
        'one-click Top view of layer 3 (printed in three passes, one per island): its own clock, its complete toolpath shown');
  simScrub(0, false);
  check(Math.abs(sim.t - D.t0[D.layerItems[2][0]]) < 1e-9, 'scrub to the layer start');
  simScrub(5000, false);
  check(simLocate(sim.t).layer === 2, 'mid-layer: the nozzle is on that layer (the other island time in between is skipped)');
  simScrub(10000, false);
  check(Math.abs(_simToLocal(sim.t) - lb) < 1e-9 && simLocate(sim.t).layer === 2, 'scrubbing stays inside the selected layer');
  const El = D.res.estimate.layers[2];
  const lay = txt('sim-layer-info');
  check(/Layer 3/.test(lay) && /printing/.test(lay) && /travel/.test(lay) && /Estimated/.test(lay) &&
        El.runs.length === D.res.plan.layers[2].runs && El.runs.length >= 2 && Math.abs(El.total_time - (lb - la)) < 1e-3 && /printed in 3 passes: A, B, C/.test(lay), 'layer info: distances, passes per island, duration = the layer\\'s clock');
  const runsTxt = txt('sim-runs');
  check(/Run 1/.test(runsTxt) && /Run 2/.test(runsTxt) && /Rect 1/.test(runsTxt) && /Circle 1/.test(runsTxt) && /Wall Group 1/.test(runsTxt),
        'per-run durations with their SOURCE paths and wall construction (a group with no Wall System: its own, Hollow)');
  simStepLayer(1);
  check(sim.selLayer === 3 && sim.mode === 'top', 'next layer');
  simStepLayer(-1); simStepLayer(-1);
  check(sim.selLayer === 1, 'previous layer');
  const rr = simRunRange(1, 1);
  simSelectRun(1, 1);
  check(sim.selRun === 1 && Math.abs(sim.t - rr[0]) < 1e-9 && simLocate(sim.t + 1e-6).run === 1, 'a run: focused, playhead at its start');
  const ids = []; for (const i of D.layerItems[1]) if (D.run[i] === 1) ids.push(D.strings[D.source[i]]);
  check(ids.length && ids.every(s => s === Cp.id || s === R.id), 'every move keeps its source path identity');

  // ---- navigation tree ----
  sim3D();
  simSelectLift(0);
  check(/Lift 1 · z 0.0–10.5/.test(txt('sim-tree')) && /Island A/.test(txt('sim-tree')) && /Island C/.test(txt('sim-tree')) &&
        /Building · 1 lift/.test(txt('sim-tree')) && Math.abs(sim.t - D.total) < 1e-6, 'Building → Lift → Island: the lift and its islands');
  const sB = ST.findIndex(x => x.name === 'B');
  simSelectStage(sB);
  check(sim.selStage === sB && sim.mode === '3d' && Math.abs(sim.t - (D.t0[D.stageLast[sB]] + D.dt[D.stageLast[sB]])) < 1e-9 &&
        /Layer 7/.test(txt('sim-tree')), 'an island focuses its last move; its layers listed (→ Layer → Run)');
  simSelectSection(1);
  check(sim.selSection === 1 && sim.selLayer === 6, 'sections still focus their layers');
  check(D.res.headers.length === 1 && D.res.headers[0].layer === 4, 'the header is in the plan view (above the layer it carries)');

  // ---- LIFTS: a 6 in lift → 2 lifts, an explicit frame reposition, never back below it ----
  const nsL = plans('stream');
  const pending = simSetMachine('lift_height', 6);
  check(asm.state.machine.lift_height === 6 && el('sim-lift-info').textContent.includes('2 lifts (frame raised 1×): 0.00–6.00, 6.00–10.50 in'),
        'Print plan column: lift height → lift boundaries shown at once (before the new plan arrives)');
  await pending;
  check(plans('stream') === nsL + 1, 'a lift-height change plans again (the schedule depends on it), not just re-timing');
  await simSetMachine('lift_height', 30);
  check(/cannot exceed the maximum Z travel/.test(sim.paramError || '') && !asm.inputError && asm.state.machine.lift_height === 6 &&
        /cannot exceed/.test(txt('sim-param-error')) && el('sim-lift-height').max === 18 && plans('stream') === nsL + 1,
        'a lift above the max Z travel is refused, said in the Print plan column (not left in the Assembly); the field is capped at it');
  await simSetMachine('max_z_travel', 5);
  check(asm.state.machine.max_z_travel === 5 && asm.state.machine.lift_height === 5 && el('sim-lift-height').max === 5 && !sim.paramError,
        'a smaller max Z travel lowers the lift height with it');
  asm.hist.group = null;
  await asmUndo();
  check(asm.state.machine.max_z_travel !== 5 && asm.state.machine.lift_height === 6, 'lift settings are Assembly undo steps');
  await simShow();
  check(simOutdated() && /OUTDATED/.test(txt('sim-status')) && el('sim-generate').textContent === 'Plan Print Again',
        'an Assembly undo leaves the prepared paths OUTDATED — labelled, never re-planned behind the user');
  await wsysGenerate(); await simGenerate();
  const D2 = sim.data, F = D2.frames;
  check(D2.res.plan.lifts.length === 2 && F.length === 1 && D2.res.plan.lifts[1].first_layer === 4 &&
        Array.from(D2.lift).slice(F[0] + 1).every(l => l === 1) && Array.from(D2.layer).slice(F[0] + 1).every(L => L >= 4),
        'two lifts (layers 1–4, 5–7): one frame reposition, nothing below it afterwards');
  check(D2.res.plan.stages.map(x => x.name).join() === 'A,B,C,A,B,C', 'each lift: island A up, then B, then C');
  check(el('sim-marks').children.length === 1, 'the lift boundary is marked on the timeline');
  const nt = plans('times');
  await simSetFrameTime(10);
  const E2 = sim.data.res.estimate;
  check(plans('times') === nt + 1 && E2.frame_time === 600 && E2.frame_moves === 1 &&
        Math.abs(E2.total_time - E2.print_time - E2.travel_time - 600) < 1e-6 && Math.abs(sim.data.total - E2.total_time) < 1e-3 &&
        sim.data.dt[F[0]] === 600, 'frame reposition time (10 min): re-timed only, separate in the total, the frame event lasts it');
  check(/frame repositioning 1× · 10 min/.test(txt('sim-times')) && /Lift 2 · z 6.0–10.5 · 3 layers/.test(txt('sim-tree')),
        'time sidebar: extrusion / travel / frame separately; per lift and island');
  simSetTime(sim.data.t0[F[0]] + 300);
  check(simLocate(sim.t).kind === SIM_KIND.FRAME && /Frame reposition: raise the XY frame to z 6.00/.test(txt('sim-now')),
        'during the frame reposition the status says so');
  await simSetFrameTime(0);
  await simSetMachine('lift_height', 18);
  check(sim.data.res.plan.lifts.length === 1, '(back to one lift)');

  // ---- synchronisation with Assembly / Designer changes ----
  const n0 = plans('stream');
  await simShow();
  check(plans('stream') === n0, 'nothing changed: no new plan');
  el('sim-speed-unit').value = 'in/s';
  const nt0 = plans('times');
  await simSetSpeed('print', 4);
  check(plans('times') === nt0 + 1 && plans('stream') === n0 && Math.abs(sim.data.total - sim.data.res.estimate.total_time) < 1e-3 &&
        asm.state.print_speeds.print === 4, 'a speed edit re-times the same plan (no re-routing)');
  setWorkspace('assembly');
  const nr = calls.filter(c => c.url === '/api/assembly/resolve').length;
  await asmSetLayerHeight(1.0);
  check(calls.filter(c => c.url === '/api/assembly/resolve').slice(nr).every(c => c.envelope) && plans('stream') === n0 &&
        asm.resolved.geometry === 'envelope' && asm.resolved.support.level === 'envelope',
        'Assembly editing resolves ARCHITECTURAL ENVELOPES (preliminary support) — no print plan');
  check(/Architectural support [(]preliminary[)]/.test(txt('asm-support-summary')) && /Bead-level support [(]outdated print paths[)]/.test(txt('asm-support-summary')),
        'the Assembly says its support is preliminary and that the prepared paths are outdated');
  setWorkspace('simulation');
  await simShow();
  check(plans('stream') === n0 && simOutdated() && sim.data.res.plan.layers.length === 7 && /OUTDATED/.test(txt('sim-status')),
        'an Assembly change (layer height) → the shown paths are OUTDATED (labelled), nothing planned');
  check(el('sim-export-job').disabled, 'an OUTDATED plan cannot be exported');
  await wsysGenerate(); await simGenerate();
  check(plans('stream') === n0 + 1 && sim.data.res.plan.layers.length === 11 && !simOutdated(),
        'Generate Wall Systems + Plan Print → the new plan (11 layers), current');
  setWorkspace('designer');
  await editDesign('base');
  addPrimitive('LinePath'); historyCheckpoint();
  setWorkspace('simulation');
  await simShow();
  check(plans('stream') === n0 + 1 && simOutdated(), 'a Designer change → outdated, nothing planned');
  await wsysGenerate(); await simGenerate();
  check(plans('stream') === n0 + 2 && Array.from(sim.data.source).some(i => i >= 0 && sim.data.strings[i] === layer.source_paths[2].id),
        'regenerated: a new plan that prints the new wall');

  // ---- RELIABILITY (2026-10-09): the plan lifecycle never sticks, never shows a stale plan ----
  const sleep = ms => new Promise(r => realSetTimeout(r, ms));
  const gateNext = () => { let open, sent; const g = { wait: new Promise(r => { open = r; }), sent: new Promise(r => { sent = r; }) };
    g.open = open;
    fetchHook = (url, body) => { if (url === '/api/assembly/print_plan' && body.detail === 'stream') { fetchHook = null; g.job = body.job; sent(); return g.wait.then(() => null); } return null; };
    return g; };
  const cancels = job => calls.filter(c => c.url === '/api/assembly/print_plan/cancel').length;
  check(sim.data.res.plan.layers.length === 11 && !sim.loading, 'start: the 11-layer plan, nothing in flight');
  // a superseded answer never overwrites the newer one
  let g = gateNext();
  await asmSetLayerHeight(2.0);
  await wsysGenerate();
  const pA = simGenerate();
  await g.sent;
  check(sim.loading && sim.data.res.plan.layers.length === 11, 'while a new plan is built the last one stays shown ("Generating…")');
  await asmSetLayerHeight(1.5);
  await wsysGenerate(); await simGenerate();
  check(sim.data.res.plan.layers.length === 7 && !sim.loading, 'a newer change plans at once (7 layers)');
  g.open(); await pA;
  check(sim.data.res.plan.layers.length === 7 && !sim.loading && sim.key === (await _simKeys()).key,
        'the superseded (5-layer) answer arriving late is dropped');
  // back to the state already shown while a request is in flight: dropped, not left "Planning…"
  g = gateNext();
  await asmSetLayerHeight(2.0);
  await wsysGenerate();
  const pB = simGenerate();
  await g.sent;
  const c0 = cancels();
  await asmSetLayerHeight(1.5);
  await simRefresh();
  check(!sim.loading && !sim.inflight && cancels() === c0 + 1 && calls[calls.length - 1].job === g.job,
        'returning to the shown state: the in-flight plan is cancelled, the status clears');
  g.open(); await pB;
  check(sim.data.res.plan.layers.length === 7 && !sim.loading, '… and its late answer changes nothing');
  // leaving the Simulation: playback stops; a PRINT PREPARATION in progress
  // continues in the background while the Assembly is edited
  simTogglePlay();
  check(sim.playing, 'playing');
  setWorkspace('assembly');
  check(!sim.playing, 'leaving the Simulation stops playback (no frames while hidden)');
  await asmSetLayerHeight(2.0);
  const ns0 = plans('stream');
  setWorkspace('simulation');
  await simShow();
  check(plans('stream') === ns0 && simOutdated(), 'showing the Simulation after an edit plans nothing (outdated)');
  await wsysGenerate();
  g = gateNext();
  const pC = simGenerate();
  await g.sent;
  const c1 = cancels();
  setWorkspace('assembly');
  check(sim.loading && cancels() === c1, 'leaving while generating does NOT cancel the print preparation');
  const t0r = Date.now();
  await asmSetLayerHeight(1.0);
  check(asm.resolved && asm.resolved.instances.length === 11 && Date.now() - t0r < 20000 && sim.loading,
        'Assembly edits resolve (envelopes) while the preparation runs');
  g.open(); await pC;
  check(!sim.loading && sim.data.res.plan.layers.length === 5 && simOutdated(),
        'the preparation finishes in the background; made for the earlier state, it is shown OUTDATED');
  setWorkspace('simulation');
  await simShow();
  check(simOutdated() && sim.data.res.plan.layers.length === 5, 'reopened: still the outdated paths, labelled');
  await wsysGenerate(); await simGenerate();
  check(sim.data.res.plan.layers.length === 11 && !sim.loading && !simOutdated(), 'regenerated for the current Assembly');
  // a plan cancelled by the server (a full-geometry request preempted it): asked again
  await asmSetLayerHeight(1.5);
  let fakeCancel = true;
  fetchHook = (url, body) => (url === '/api/assembly/print_plan' && body.detail === 'stream' && fakeCancel)
    ? (fakeCancel = false, { json: async () => ({ error: 'print plan cancelled', cancelled: true }) }) : null;
  const ns = plans('stream');
  await wsysGenerate(); await simGenerate();
  fetchHook = null;
  check(plans('stream') === ns + 2 && sim.data.res.plan.layers.length === 7 && !sim.loading && !sim.error,
        'a server-cancelled print preparation is requested again');
  // REBUILD: plans again from scratch, keeps camera / mode / layer / playhead
  simSelectLayer(3); simTopView();
  const [ra3, rb3] = simLayerRange(3);
  simSetTime(ra3 + (rb3 - ra3) * 0.5);
  sim.view.zoom = 2.5; sim.view.px = 30;
  const before = sim.data, nsR = plans('stream');
  check(html.includes('id="sim-rebuild"') && html.includes('Rebuild Simulation'), 'a visible "Rebuild Simulation" control');
  await simRebuild();
  const last = calls.filter(c => c.url === '/api/assembly/print_plan').pop();
  check(plans('stream') === nsR + 1 && last.rebuild && last.job && sim.data !== before && !sim.loading,
        'Rebuild: a fresh plan (server cache dropped) without reloading');
  const [ra3b, rb3b] = simLayerRange(3);
  check(sim.mode === 'top' && sim.selLayer === 3 && sim.view.zoom === 2.5 && sim.view.px === 30 &&
        Math.abs(sim.t - (ra3b + (rb3b - ra3b) * 0.5)) < 1e-6, 'camera, top view, selected layer and playhead kept');
  check(asm.state.layer_height === 1.5 && project.designs.length === 2, 'project state untouched by a rebuild');

  // ---- NEW PROJECT (separate from Clear All = "Clear Design") ----
  check(html.includes('id="btn-project-new"') && html.indexOf('btn-project-new') < html.indexOf('btn-project-open') &&
        html.includes('id="btn-clear-all"') && html.includes('New Project.">Clear Design</button>'),
        'New Project in the shared header; the Designer toolbar button reads "Clear Design"');
  await editDesign('base');
  const secs = JSON.stringify(asm.state.sections);
  clearAll();
  check(layer.source_paths.length === 0 && project.designs.length === 2 && JSON.stringify(asm.state.sections) === secs &&
        /Other designs and the Assembly are unchanged/.test(el('status-bar').textContent),
        'Clear Design empties only the design being edited (and says so)');
  undo();
  check(layer.source_paths.length === 3, '(undone)');
  asm.state.objects.push({ kind: 'header', id: 'h9', section: 0, layer: 1, x: 0, y: 0, angle: 0, span: 10, bearing: 8, depth: 10, thickness: 6 });
  asm.state.transform.shift = [0.5, 0];
  let asked = 0;
  global.confirm = () => { asked++; return false; };
  check(newProject() === false && asked === 1 && layer.source_paths.length === 3 && project.designs.length === 2 &&
        sim.data, 'New Project asks first; declined → nothing changes');
  global.confirm = () => { asked++; return true; };
  const saves = calls.filter(c => c.url.startsWith('/api/project/')).length;
  setWorkspace('simulation');
  check(newProject() === true && asked === 2, 'confirmed');
  check(layer.source_paths.length === 0 && !(layer.wall_groups || []).length && !project.wall_systems.length && layer.openings.length === 0 &&
        project.designs.length === 1 && project.active === 'base' && !project.name &&
        JSON.stringify(layer.material) === JSON.stringify(JSON.parse(_PRISTINE_LAYER).material),
        'a clean empty Base design (no derived designs, Wall Systems, sources; default material)');
  check(JSON.stringify(window.AssemblyProject.exportState()) === JSON.stringify(window.AssemblyProject.pristine()) &&
        asm.state.objects.length === 0 && asm.state.profiles.length === 0 && !asm.state.print_speeds &&
        asm.state.transform.shift[0] === 0 && asm.state.sections.length === 1 && asm.hist.undo.length === 0,
        'the default Assembly (sections, transforms, profiles, headers, overrides, speeds), no Assembly undo');
  check(_hist.undo.length === 0 && sim.data === null && sim.key === null && !sim.inflight && sim.selLayer === 0 &&
        sim.view.zoom === 1 && asm.resolved === null && Object.keys(asm.variants).length === 0,
        'no Designer undo, print plan, simulation, caches or selections of the old project');
  check(currentWorkspace === 'designer' && calls.filter(c => c.url.startsWith('/api/project/')).length === saves,
        'back in the Designer; nothing saved or written (.muddesign files untouched)');
  global.confirm = () => { throw new Error('should not ask'); };
  check(newProject() === true, 'an untouched project is replaced without asking');
  setWorkspace('simulation');
  await simShow();
  check(!sim.loading && sim.inflight === null, 'the Simulation of the new project settles (nothing stuck)');
  console.log('UI SIMULATION SMOKE PASSED');
})().catch(e => { console.error(e); process.exit(1); });
`);
