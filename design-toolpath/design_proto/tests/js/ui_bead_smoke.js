// UI smoke test for Material / Bead: the bead footprint is drawn from the
// backend's PRINTABLE centerlines as round-capped, round-joined strokes of
// the bead width into one offscreen layer; Show bead On / Off; Bead Width
// in 0.25 in steps, undoable, visual only. Run via tests/test_material.py.
const rec = [];
const beadCtx = new Proxy({}, {
  get(t, k) {
    if (k in t) return t[k];
    if (['beginPath', 'moveTo', 'lineTo', 'closePath', 'stroke', 'clearRect'].includes(k))
      return (...a) => rec.push({ op: k, args: a, lineWidth: t.lineWidth, lineCap: t.lineCap,
                                  lineJoin: t.lineJoin, strokeStyle: t.strokeStyle });
    return () => {};
  },
  set(t, k, v) { t[k] = v; return true; },
});
const beadCanvas = { width: 0, height: 0, getContext: () => beadCtx };
// (harness copied from ui_trim_smoke.js)
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
  createElement: tag => tag === 'canvas' ? beadCanvas : Object.assign(stub(tag), { style: {}, children: [], className: '',
    appendChild(c) { this.children.push(c); return c; }, append(...c) { this.children.push(...c); },
    querySelector: () => ({ disabled: false }), set lastChild(v) {}, get lastChild() { return this.children[this.children.length - 1]; } }),
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); }, body: {}, activeElement: null,
};
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.requestAnimationFrame = () => 0; global.cancelAnimationFrame = () => {};
global.fetch = async () => ({ json: async () => ([]) });
global.console.warn = () => {};
global.setTimeout = () => 0; global.clearTimeout = () => {};   // no backend round trips
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const strokes = () => rec.filter(r => r.op === 'stroke');
  addPrimitive('RectanglePath');
  printable = [
    { id: 'line', pts: [[100, 200], [300, 200]], closed: false },
    { id: 'loop', pts: [[50, 50], [90, 50], [90, 90], [50, 90]], closed: true },
    { id: 'dot', pts: [[10, 10]], closed: false },
  ];
  _netState = _histState();

  // ---- default: vector-only view ----
  check(showBeads === false, 'Bead display is OFF by default (vector view unchanged)');
  rec.length = 0; repaint();
  check(strokes().length === 0, 'no bead drawn while OFF');
  check(_beadWidth() === 3.0, 'default Bead Width 3.00 in');

  // ---- ON: one stroke per printable centerline ----
  toggleBeads();
  check(showBeads && document.getElementById('bead-show').checked === true, 'toggle ON (toolbar and checkbox agree)');
  rec.length = 0; repaint();
  const st = strokes();
  check(st.length === 2, 'one bead per printable centerline (a 1-point entry is skipped)');
  check(st.every(r => r.lineCap === 'round' && r.lineJoin === 'round'), 'round ends and round joins');
  check(st.every(r => Math.abs(r.lineWidth - 3.0 * 800 / 400) < 1e-9), 'stroke width = bead width in canvas scale (3 in = 6 px)');
  check(rec.filter(r => r.op === 'closePath').length === 1, 'a closed centerline is a closed loop (no ends)');
  const mv = rec.filter(r => r.op === 'moveTo')[0].args;
  const [cx, cy] = worldToCanvas(100, 200);
  check(mv[0] === cx && mv[1] === cy, 'the bead follows the centerline exactly');
  check(new Set(st.map(r => r.strokeStyle)).size === 1, 'one opaque colour: overlapping beads union (composited once)');

  // ---- width: 0.25 in steps, undoable ----
  // (first pass: Bead Width was visual only. SUPERSEDED 2026-10-06: with
  // physical rules on it shapes geometry and regenerates; with them off it
  // is still visual only.)
  check(layer.material.physical === true, 'physical rules ON by default in the app');
  const steps = _hist.undo.length;
  const sentinel = { moves: [] }; routeResult = sentinel;
  document.getElementById('bead-width').value = '4.37';
  onBeadWidthChange();
  check(layer.material.bead_width === 4.25 && document.getElementById('bead-width').value === '4.25',
        'Bead Width snaps to 0.25 in steps');
  check(_hist.undo.length === steps + 1, 'a width change is one undo step');
  check(routeResult === null, 'physical rules: a width change regenerates the geometry / toolpath');
  document.getElementById('phys-rules').checked = false; onMaterialChange('physical');
  routeResult = sentinel;
  document.getElementById('bead-width').value = '4.5'; onBeadWidthChange();
  check(routeResult === sentinel, 'physical rules OFF: Bead Width is visual only (legacy)');
  document.getElementById('phys-rules').checked = true; onMaterialChange('physical');
  document.getElementById('bead-width').value = '4.25'; onBeadWidthChange();

  // ---- Contact Overlap and Return-Lane Overlap: distinct, 0.25 in, clamped ----
  document.getElementById('contact-overlap').value = '1.1'; onMaterialChange('contact_overlap');
  document.getElementById('return-overlap').value = '0.4'; onMaterialChange('return_overlap');
  check(layer.material.contact_overlap === 1.0 && layer.material.return_overlap === 0.5,
        'both overlaps snap to 0.25 in and stay independent');
  const pm = buildPayload().material;
  check(pm.contact_overlap === 1.0 && pm.return_overlap === 0.5 && pm.physical === true,
        'payload carries both overlaps separately + the physical switch');
  document.getElementById('contact-overlap').value = '9'; onMaterialChange('contact_overlap');
  check(layer.material.contact_overlap === 4.25, 'Contact Overlap ≤ bead width (100 % overlap allowed)');
  document.getElementById('return-overlap').value = '9'; onMaterialChange('return_overlap');
  check(layer.material.return_overlap === 4.0, 'Return-Lane Overlap < bead width (a full overlap would be a retrace)');
  document.getElementById('bead-width').value = '2'; onBeadWidthChange();
  check(layer.material.contact_overlap === 2.0 && layer.material.return_overlap === 1.75 &&
        document.getElementById('contact-overlap').value === '2.00',
        'reducing the bead width clamps both overlaps visibly');
  check(document.getElementById('material-derived').textContent.includes('0.25 in apart'),
        'derived readout shows the return-lane separation');
  undo(); undo(); undo(); undo(); undo(); undo(); undo(); undo(); undo();
  document.getElementById('bead-width').value = '4.25'; onBeadWidthChange();
  rec.length = 0; repaint();
  check(strokes().every(r => Math.abs(r.lineWidth - 4.25 * 2) < 1e-9), 'beads redraw at the new width');
  check(buildPayload().material.bead_width === 4.25, 'payload carries the material (future geometry input)');
  const wBefore = _beadWidth();
  document.getElementById('bead-width').value = '0';
  onBeadWidthChange();
  check(layer.material.bead_width === 0.25, 'minimum 0.25 in');
  undo();
  check(_beadWidth() === wBefore && document.getElementById('bead-width').value === wBefore.toFixed(2),
        'undo restores the width and the control');
  redo(); check(_beadWidth() === 0.25, 'redo');
  undo();

  // ---- OFF again ----
  toggleBeads(false);
  rec.length = 0; repaint();
  check(!showBeads && strokes().length === 0 && document.getElementById('bead-show').checked === false,
        'toggle OFF returns to the vector-only view');
  // ---- only printable geometry ----
  toggleBeads(true); printable = []; rec.length = 0; repaint();
  check(strokes().length === 0, 'no printable centerlines → no bead (sources alone never get one)');
  clearAll();
  check(printable.length === 0 && _beadWidth() === wBefore, 'Clear All clears geometry, keeps the material');
  console.log('UI BEAD SMOKE PASSED');
})();
`);
