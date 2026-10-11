// UI smoke test for the CHAINED LOOP PARAMETER MODEL (2026-10-09): effective
// values shown (auto resolved, so spinners step from the generated value),
// each control edited alone, LAST-EDITED-WINS for the dependent controls
// (Pitch ↔ Loop Width ↔ Neck, Loop Depth ≤ the envelope, Wall Thickness),
// edit sequences. Since 2026-10-11 the parameter rows live in the Wall
// Systems workspace (`_addWallSystemParams(panel, s, {onParam, thickness})`:
// the thickness is the Wall Group's, an edit generates nothing — see
// layer_assembly/tests/js/ui_wallsystems_smoke.js). The request part now
// drives a Wall Group THICKNESS edit in the Designer's full preview: rapid
// edits (one follow-up, the in-flight resolve aborted), stale-response
// prevention, geometry first then the route (same payload, increasing resolve
// sequence numbers).
// Run via tests/test_chain_params.py (or: node tests/js/ui_chain_params_smoke.js).
const fs = require('fs');
const path = require('path');
// Permissive stub: any property is a callable stub returning another stub.
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
  addEventListener() {}, body: {}, activeElement: null,
};
global.window = { DESIGNER_PRINT_PREVIEW: true, addEventListener() {}, devicePixelRatio: 1 };
global.requestAnimationFrame = () => 0; global.cancelAnimationFrame = () => {};
global.fetch = async () => ({ json: async () => ([]) });
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const near = (a, b, tol = 1e-3) => Math.abs(a - b) <= tol;
  const all = (el, out = []) => { for (const c of el.children || []) { out.push(c); all(c, out); } return out; };
  const row = (p, label) => all(p).find(r => r.children && r.children[0] && r.children[0].textContent === label);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  addPrimitive('RectanglePath');
  const A = layer.source_paths[0];
  // (a project Wall System; its Wall Group gives the 10 in thickness)
  const S = { id: 'S1', name: 'Loops', type: 'chain', thickness: 10, params: {}, members: [], groups: [],
              web: { pattern: 'none', params: {} } };
  layer.material = { ...(layer.material || {}), bead_width: 3 };
  const panel = document.getElementById('wsys-props');
  const show = () => { panel.children.length = 0; _addWallSystemParams(panel, S, { onParam: () => {}, thickness: S.thickness }); };
  const inp = l => row(panel, l).children[1];
  const unit = l => row(panel, l).children[2].textContent;
  const val = l => +inp(l).value;
  const edit = (l, v) => { inp(l).value = String(v); inp(l).onchange(); };
  show();
  // --- display: the EFFECTIVE values, auto marked ---------------------------------
  check(val('Pitch') === 12 && unit('Pitch') === 'in · auto', 'auto Pitch shows 1.2 × Wall Thickness (12 in), marked auto');
  check(near(val('Loop Width'), 9.6) && near(val('Loop Depth'), 3.5) && near(val('Neck'), 0.12) && val('Phase') === 0,
        'auto Loop Width 0.8 × Pitch, Loop Depth = the envelope (10 − 3) / 2, Neck 0.12');
  check(inp('Pitch').step === 0.5 && inp('Neck').step === 0.01, 'spinner steps suited to each control');
  // the spinner from auto steps from the generated value (12 → 12.5), not 0 → 1
  inp('Pitch').value = String(val('Pitch') + inp('Pitch').step); inp('Pitch').onchange();
  check(S.params.pitch === 12.5 && unit('Pitch') === 'in', 'spinner up from auto: 12.5 in (explicit), never a 1 in pitch');
  edit('Pitch', 0);
  check(S.params.pitch === 0 && val('Pitch') === 12 && unit('Pitch') === 'in · auto', '0 restores auto');
  // retyping the shown auto value changes nothing
  const n0 = _hist.undo.length; edit('Loop Width', 9.6);
  check(!(S.params.loop_width > 0) && _hist.undo.length === n0, 'retyping the shown auto value is not an edit');
  // --- each control independently ---------------------------------------------------
  edit('Pitch', 8);
  check(S.params.pitch === 8 && !(S.params.loop_width > 0) && near(val('Loop Width'), 6.4), 'Pitch alone: auto Loop Width follows (0.8 × 8)');
  edit('Loop Width', 7);
  check(S.params.loop_width === 7 && S.params.pitch === 8, 'Loop Width alone (in range): Pitch unchanged');
  edit('Loop Depth', 2);
  check(S.params.loop_depth === 2 && S.params.pitch === 8 && S.params.loop_width === 7, 'Loop Depth alone');
  edit('Neck', 0.3);
  check(S.params.neck === 0.3 && S.params.loop_width === 7, 'Neck alone (reachable): Loop Width unchanged');
  edit('Phase', 0.5);
  check(S.params.phase === 0.5 && S.params.neck === 0.3 && S.params.pitch === 8, 'Phase alone');
  // --- LAST-EDITED-WINS ------------------------------------------------------------
  edit('Loop Width', 20);                              // > 1.6 × 8
  check(S.params.loop_width === 20 && near(S.params.pitch, 12.5) && near(val('Pitch'), 12.5),
        'Loop Width wins: Pitch raised to Width / 1.6 (and shown)');
  check(/Pitch raised/.test(document.getElementById('status-bar').textContent), 'the moved Pitch is announced');
  edit('Pitch', 6);                                    // now 20 > 1.6 × 6
  check(S.params.pitch === 6 && near(S.params.loop_width, 9.6) && near(val('Loop Width'), 9.6),
        'Pitch wins: Loop Width clamped to 1.6 × Pitch (and shown)');
  edit('Loop Width', 0.5);                             // < 0.15 × 6
  check(S.params.loop_width === 0.5 && near(S.params.pitch, 0.5 / 0.15, 1e-3), 'Loop Width wins: Pitch reduced to Width / 0.15');
  edit('Pitch', 10); edit('Loop Width', 8);
  let e = _chainEffective(S.params, 10, 3);
  edit('Neck', 0.8);                                   // unreachable at W / P = 0.8 (max ≈ 0.68)
  e = _chainEffective(S.params, 10, 3);
  check(S.params.neck === 0.8 && S.params.loop_width < 8 && near(e.neck, 0.8, 2e-3) && near(val('Loop Width'), S.params.loop_width),
        'Neck wins: Loop Width narrowed until the Neck is reachable (and shown)');
  edit('Neck', 0.02);
  e = _chainEffective(S.params, 10, 3);
  check(S.params.neck === 0.05 && near(e.neck, 0.05, 2e-3), 'Neck below its global minimum: held at 0.05; the width widened to reach it');
  edit('Neck', 0.6); edit('Loop Width', 16);           // W / P = 1.6: neck ≤ 0.58
  e = _chainEffective(S.params, 10, 3);
  check(S.params.loop_width === 16 && S.params.neck <= e.neck_range[1] + 1e-3 && near(val('Neck'), S.params.neck),
        'Loop Width wins: Neck moved into the reachable range (and shown)');
  edit('Loop Depth', 50);
  check(near(S.params.loop_depth, 3.5) && near(val('Loop Depth'), 3.5), 'Loop Depth limited to the envelope (shown)');
  // Wall Thickness wins
  S.thickness = 7; const notes = _chainOnThickness(S);
  check(near(S.params.loop_depth, 2) && notes.some(n => /Loop Depth/.test(n)), 'Wall Thickness wins: Loop Depth limited to the new envelope');
  S.params = { loop_width: 9 }; S.thickness = 6; _chainOnThickness(S);      // auto pitch 7.2: 9 > 1.6 × 7.2 → kept? 9 ≤ 11.52
  check(S.params.loop_width === 9, 'Wall Thickness: an in-range Loop Width is kept');
  S.params = { loop_width: 14 }; _chainOnThickness(S);
  check(near(S.params.loop_width, 1.6 * 7.2), 'Wall Thickness: auto Pitch follows it; Loop Width clamped to 1.6 × the new Pitch');
  S.thickness = 10;
  // --- sequences: every state stays consistent ----------------------------------------
  S.params = {}; show();
  const seq = [['Pitch', 3], ['Neck', 0.85], ['Loop Width', 4.8], ['Loop Depth', 1], ['Pitch', 30], ['Neck', 0.04],
               ['Phase', 0.25], ['Loop Width', 60], ['Pitch', 0.1], ['Loop Depth', 9], ['Neck', 0.5], ['Pitch', 0]];
  for (const [l, v] of seq) {
    edit(l, v);
    const p = S.params, e2 = _chainEffective(p, 10, 3);
    const W = p.loop_width > 0 ? p.loop_width : 0.8 * e2.pitch;
    check(W <= 1.6 * e2.pitch + 1e-6 && W >= 0.15 * e2.pitch - 1e-6 &&
          (!(p.neck > 0) || (p.neck >= e2.neck_range[0] - 2e-3 && p.neck <= e2.neck_range[1] + 2e-3)) &&
          (!(p.loop_depth > 0) || p.loop_depth <= e2.envelope + 1e-9) &&
          WALL_SYSTEMS.chain.params.every(q => near(val(q.label), +(+(q.name === 'phase' ? e2.phase : e2[q.name])).toFixed(3), 2e-3)),
          'sequence ' + l + ' = ' + v + ': stored values consistent, every field shows the generated value');
  }
  // --- requests (Designer full preview): a Wall Group THICKNESS edit ------------------------
  const G = _wgNew([A.id], 10);
  project.wall_systems = [{ ...S, groups: [G.id] }];
  updateWallGroupSection();
  const gpanel = document.getElementById('wallgrp-props');
  const thick = v => { const r = row(gpanel, 'Wall Thickness').children[1]; r.value = String(v); r.onchange(); };
  const calls = [];
  let pending = [];
  global.fetch = (url, opts) => new Promise((res, rej) => {
    const c = { url, body: opts.body, seq: +opts.headers['X-Resolve-Seq'], ch: opts.headers['X-Resolve-Channel'], aborted: false };
    calls.push(c);
    if (opts.signal) opts.signal.addEventListener('abort', () => { c.aborted = true; const e = new Error('aborted'); e.name = 'AbortError'; rej(e); });
    pending.push(data => res({ json: async () => data }));
  });
  const geom = { paths: [], network: { wall_systems: [] }, printable: [] };
  const routeData = { layer: { paths: [] }, network: { wall_systems: [] }, printable: [], moves: [], graph: {},
                      metrics: { print_runs: 1, travel_moves: 0, travel_distance: 0, pct_printing: 100, print_distance: 1, retrace_distance: 0 },
                      closure: { physical: true, closed: 1, components: 1, open: [] } };
  showToolpath = true; routeResult = { moves: [] };
  thick(11);
  check(routeResult === null && G.thickness === 11, 'a thickness edit clears the old toolpath at once');
  await sleep(330);
  check(calls.length === 1 && calls[0].url === '/api/effective_paths', 'geometry first (walls, toolpath shown)');
  check(JSON.parse(calls[0].body).project_wall_systems[0].type === 'chain' && !JSON.parse(calls[0].body).envelope,
        'the full preview sends the project Wall Systems (the Designer itself shows envelopes)');
  for (const v of [12, 13, 14, 15]) { thick(v); await sleep(40); }
  await sleep(330);
  check(calls[0].aborted, 'a newer edit aborts the resolve in flight');
  check(calls.length === 2 && JSON.parse(calls[1].body).wall_groups[0].thickness === 15,
        'rapid edits: ONE follow-up resolve, with the latest value');
  check(calls[1].seq > calls[0].seq && calls[1].ch === calls[0].ch, 'resolve requests carry the page channel and increasing sequence numbers');
  pending[1](geom); await sleep(10);
  check(calls.length === 3 && calls[2].url === '/api/route' && calls[2].body === calls[1].body,
        'then the route, with the identical payload (the server reuses the geometry)');
  check(!_geomPending, 'the new geometry is drawn undimmed while the route resolves');
  thick(16); await sleep(330);
  check(calls[2].aborted && routeResult === null, 'the superseded route is aborted, never drawn');
  pending[3](geom); await sleep(10);
  pending[4]({ superseded: true }); await sleep(10);
  check(routeResult === null, 'a 409 "superseded" answer is ignored');
  calls.length = 0; pending = [];
  routeResult = { moves: [] };
  scheduleRefresh(); await sleep(330);
  const firstUrl = calls[0] && calls[0].url;
  scheduleRefresh(); await sleep(330);
  check(firstUrl === '/api/route' && !calls[0].aborted, 'other refreshes keep the in-flight request (coalesced as before)');
  pending[0](routeData); await sleep(10);
  check(calls.length === 2 && !!routeResult, 'its result lands, then the coalesced follow-up runs');
  pending[1](routeData); await sleep(10);
  console.log('UI CHAIN PARAMS SMOKE PASSED');
})().catch(e => { console.error(e.stack || e); process.exit(1); });
`);
