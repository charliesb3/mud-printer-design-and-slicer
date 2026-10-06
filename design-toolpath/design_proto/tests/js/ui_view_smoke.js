// UI smoke test for VIEW NAVIGATION (zoom / pan, integrated in the
// world ↔ canvas transform) and the Beads OFF / ON rendering rule.
// Run via tests/test_app.py.
// (harness from ui_origin_smoke.js)
// UI smoke test for ROUTE ORIGIN and the solid bead rendering: one origin
// marker per closed run (Start / End only for open runs), dragging it along
// its own run's printable strands (stored as design state, one undo step,
// geometry untouched), playback beginning at the origin; beads composited
// once, opaque, in the print colour; no centreline drawn through it.
// Run via tests/test_route_origin.py.
const mainRec = [];
const mainCtx = new Proxy({ globalAlpha: 1 }, {
  get(t, k) {
    if (k in t) return t[k];
    if (['stroke', 'fill', 'drawImage', 'fillText'].includes(k))
      return (...a) => mainRec.push({ op: k, args: a, lineWidth: t.lineWidth, alpha: t.globalAlpha,
                                     fillStyle: t.fillStyle, strokeStyle: t.strokeStyle });
    if (k === 'save') return () => { (t._st = t._st || []).push(t.globalAlpha); };
    if (k === 'restore') return () => { t.globalAlpha = (t._st || []).pop() ?? 1; };
    return () => {};
  },
  set(t, k, v) { t[k] = v; return true; },
});
// (harness from ui_bead_smoke.js)
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
// the main canvas: recording context
elements['canvas'] = Object.assign(stub('canvas'), {
  style: {}, classList: { toggle() {}, add() {}, remove() {} }, id: 'canvas', width: 800, height: 800,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 800 }),
  getContext: () => mainCtx, addEventListener() {},
});
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const near = (a, b, t = 1e-6) => Math.abs(a - b) < t;
  const ev = (wx, wy, extra) => { const [cx, cy] = worldToCanvas(wx, wy); return Object.assign({ clientX: cx, clientY: cy, button: 0 }, extra || {}); };
  const key = (type, k) => (listeners[type] || []).forEach(f => f({ key: k, preventDefault() {} }));

  // ---- transform ----
  check(view.z === 1 && view.px === 0 && view.py === 0, 'default view = 100 %');
  for (const [z, px, py] of [[1, 0, 0], [3.7, -120, 55], [0.4, 300, -20]]) {
    setView(z, px, py);
    const [cx, cy] = worldToCanvas(123.4, 56.7);
    const [wx, wy] = canvasToWorld(cx, cy);
    check(near(wx, 123.4) && near(wy, 56.7), 'world ↔ canvas round trip at zoom ' + z);
  }
  viewReset();
  const [w0x, w0y] = canvasToWorld(310, 450);
  zoomAt(310, 450, 2.5);
  const [w1x, w1y] = canvasToWorld(310, 450);
  check(near(view.z, 2.5) && near(w0x, w1x) && near(w0y, w1y), 'zoom keeps the world point under the cursor');
  check(near(HIT_DIST, 10 / 2.5) && near(SNAP_RADIUS, 12 / 2.5), 'hit / snap tolerances stay constant on screen');
  const before = worldToCanvas(100, 100);
  panBy(40, -25);
  const after = worldToCanvas(100, 100);
  check(near(after[0] - before[0], 40) && near(after[1] - before[1], -25), 'pan moves the view');
  zoomAt(400, 400, 1000); check(view.z === VIEW_MAX, 'zoom clamped (max)');
  zoomAt(400, 400, 1e-6); check(view.z === VIEW_MIN, 'zoom clamped (min)');
  viewReset();
  check(view.z === 1 && view.px === 0 && view.py === 0, '100% restores the default view');

  // ---- wheel / Space-drag / middle-drag ----
  const [a0x, a0y] = canvasToWorld(200, 600);
  onWheel({ clientX: 200, clientY: 600, deltaY: -300, preventDefault() {} });
  const [a1x, a1y] = canvasToWorld(200, 600);
  check(view.z > 1 && near(a0x, a1x) && near(a0y, a1y), 'wheel zooms in at the pointer');
  addPrimitive('RectanglePath');                       // (140,140) 120 x 120
  const R = layer.source_paths[0];
  const geom = () => JSON.stringify(layer.source_paths);
  const g0 = geom();
  const pz = { ...view };
  key('keydown', ' ');
  onMouseDown({ clientX: 500, clientY: 500, button: 0 }); onMouseMove({ clientX: 530, clientY: 470 }); onMouseUp({});
  key('keyup', ' ');
  check(near(view.px - pz.px, 30) && near(view.py - pz.py, -30) && geom() === g0, 'Space + drag pans, geometry untouched');
  const pm = { ...view };
  onMouseDown({ clientX: 10, clientY: 10, button: 1 });
  onMouseMove({ clientX: 25, clientY: 0 }); onMouseUp({});
  check(near(view.px - pm.px, 15) && near(view.py - pm.py, -10) && geom() === g0, 'middle-drag pans, geometry untouched');

  // ---- interactions at 3x zoom, panned ----
  setView(3, -500, -600);
  selectedId = null;
  onMouseDown(ev(200, 140)); onMouseUp({});
  check(selectedId === R.id, 'selection hit-test under zoom');
  onMouseDown(ev(200, 140)); onMouseMove(ev(210, 150)); onMouseUp({});
  check(near(R.x, 150) && near(R.y, 150), 'body drag moves by world distance under zoom');
  const hs = getHandles(R);
  const h = hs.find(q => q.key && q.key.includes('ne')) || hs[hs.length - 1];
  const hBefore = JSON.stringify([R.x, R.y, R.w, R.h]);
  onMouseDown(ev(h.wx, h.wy)); onMouseMove(ev(h.wx + 5, h.wy + 5)); onMouseUp({});
  check(JSON.stringify([R.x, R.y, R.w, R.h]) !== hBefore, 'handle drag under zoom');
  // drawing a shape: two clicks land exactly at the world points
  setTool('rect'); onMouseDown(ev(20, 20, { altKey: true })); onMouseDown(ev(60, 50, { altKey: true }));
  const R2 = layer.source_paths[1];
  check(R2 && near(R2.x, 20) && near(R2.y, 20) && near(R2.w, 40) && near(R2.h, 30), 'drawing under zoom lands on world coordinates');
  // opening: place, then drag it along the wall
  setTool('opening'); onMouseDown(ev(R.x + 60, R.y)); onMouseUp({}); setTool('edit');
  const op = layer.openings[0];
  check(!!op && near(op.center_s, 60, 1e-6), 'opening placed at the clicked wall point under zoom');
  onMouseDown(ev(R.x + 60, R.y)); onMouseMove(ev(R.x + 80, R.y)); onMouseUp({});
  check(near(op.center_s, 80, 1e-6), 'opening slides by world distance under zoom');
  // Trim (sections injected as the API returns them)
  const sec = { id: R.id + ':0', source: R.id, start: [R2.id], end: [R2.id], inside: {}, u_mid: 0.5,
                pts: [[R.x, R.y + 40], [R.x, R.y + 80]], trimmed_by: null };
  networkInfo = { trim_sections: [sec], trims: {}, modified_sources: [], junctions: [] };
  _netState = _histState();
  setTool('trim'); onMouseMove(ev(R.x + 0.3, R.y + 60));
  check(trimHover && trimHover.id === sec.id, 'Trim hover under zoom');
  onMouseDown(ev(R.x + 0.3, R.y + 60));
  check(layer.trims.length === 1, 'Trim click under zoom');
  setTool('edit');
  // junction selection
  networkInfo = { junctions: [{ x: R.x + 120, y: R.y + 60, key: 'k#0', corner: true, treatment: 'miter', radius: 2 }],
                  trim_sections: [], trims: {}, modified_sources: [] };
  selectedId = null; onMouseDown(ev(R.x + 120.5, R.y + 60.5)); onMouseUp({});
  check(selectedJunction && selectedJunction.key === 'k#0', 'junction selection under zoom');
  selectedJunction = null;
  // Route Origin drag
  const mv = (a, b) => ({ kind: 'print', strand_id: 'q', start: a, end: b });
  printable = [{ id: 'q', pts: [[300, 300], [340, 300], [340, 340], [300, 340]], closed: true }];
  routeResult = { moves: [mv([300, 300], [340, 300]), mv([340, 300], [340, 340]), mv([340, 340], [300, 340]), mv([300, 340], [300, 300])],
                  metrics: {}, closure: {} };
  showToolpath = true;
  setView(4, -1000, -150);
  const g1 = geom();
  onMouseDown(ev(300.5, 300.2)); onMouseMove(ev(341, 320)); onMouseUp({});
  check(layer.route_origins.length === 1 && near(layer.route_origins[0].u, 0.375, 1e-6) && geom() === g1,
        'Route Origin drag under zoom (projected, geometry untouched)');

  // ---- Fit ----
  printable = [{ id: 'f', pts: [[150, 150], [250, 150], [250, 200], [150, 200]], closed: true }];
  viewFit();
  const c0 = worldToCanvas(150, 150), c1 = worldToCanvas(250, 200);
  check(c0[0] >= 0 && c1[0] <= 800 && c1[1] >= 0 && c0[1] <= 800, 'Fit: the geometry is in view');
  check(Math.abs(c1[0] - c0[0]) >= 0.8 * 800, 'Fit: and fills the canvas sensibly');
  viewReset();

  // ---- rendering: Beads OFF = one thin blue line, Beads ON = one thick blue bead ----
  printable = [{ id: 'a', pts: [[100, 100], [200, 100]], closed: false },
               { id: 'b', pts: [[100, 150], [200, 150], [200, 250]], closed: false }];
  routeResult = null; selectedId = null; showBeads = false;
  layer.openings = []; networkInfo = null;          // (opening / junction indicators are not printable lines)
  mainRec.length = 0; rec.length = 0; repaint();
  const st = mainRec.filter(r => r.op === 'stroke');
  const blue = st.filter(r => r.strokeStyle === MOVE_COLORS.print);
  check(blue.length === 2 && blue.every(r => r.lineWidth <= 2.0), 'Beads OFF: one thin blue line per printable centreline');
  check(!st.some(r => r.strokeStyle !== MOVE_COLORS.print && r.strokeStyle !== '#252525'),
        'Beads OFF: nothing layered under / over it (no sandwich)');
  check(rec.filter(r => r.op === 'stroke').length === 0, 'Beads OFF: no bead layer');
  toggleBeads(true);
  mainRec.length = 0; rec.length = 0; repaint();
  const bs = rec.filter(r => r.op === 'stroke');
  check(bs.length === 2 && bs.every(r => r.strokeStyle === MOVE_COLORS.print && near(r.lineWidth, _beadWidth() * _viewScale())),
        'Beads ON: one thick blue bead (bead width at the current zoom)');
  check(!mainRec.filter(r => r.op === 'stroke').some(r => r.strokeStyle === MOVE_COLORS.print), 'Beads ON: no thin line through it');
  setView(2, 0, 0); rec.length = 0; repaint();
  check(rec.filter(r => r.op === 'stroke').every(r => near(r.lineWidth, _beadWidth() * 2 * 800 / 400)), 'bead width follows the zoom');
  console.log('UI VIEW SMOKE PASSED');
})();
`);
