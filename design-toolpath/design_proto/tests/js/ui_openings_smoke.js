// UI smoke test for openings: loads static/app.js against a permissive
// DOM/canvas stub and drives the real interaction functions (place, slide,
// resize both ends, wrap through the seam, move source, delete, cascade).
// Run via tests/test_openings.py (or: node tests/js/ui_openings_smoke.js).
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
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.requestAnimationFrame = () => 0; global.cancelAnimationFrame = () => {};
global.fetch = async () => ({ json: async () => ([]) });
global.console.warn = () => {};
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const ev = (wx, wy) => { const [cx, cy] = worldToCanvas(wx, wy); return { clientX: cx, clientY: cy }; };
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  addPrimitive('RectanglePath');               // (140,140) 120x120, perimeter 480
  const R = layer.source_paths[0];
  check(R.points.length === 4, 'rect points');
  setTool('opening');
  onMouseDown(ev(200, 140));                    // bottom edge midpoint, s = 60
  check(layer.openings.length === 1, 'opening placed');
  const op = layer.openings[0];
  check(Math.abs(op.center_s - 60) < 1e-6 && op.width === 12, 'centre 60, width 12');
  check(tool === 'edit' && selectedOpeningId === op.id, 'back to edit, opening selected');
  updatePropPanel(); repaint();
  const pcs = _survivingPieces(R);
  check(pcs.length === 1 && Math.abs(pcs[0][0][0] - 206) < 1e-9, 'gap drawn: piece starts at x=206');
  // slide: grab gap at x=200, drag to x=220
  onMouseDown(ev(200, 140)); onMouseMove(ev(220, 141)); onMouseUp();
  check(Math.abs(op.center_s - 80) < 1e-6, 'slide → centre 80');
  // resize end handle: end at s=86 (x=226) drag to x=236
  onMouseDown(ev(226, 140)); onMouseMove(ev(236, 140)); onMouseUp();
  check(Math.abs(op.width - 22) < 1e-6 && Math.abs(op.center_s - 85) < 1e-6, 'resize end → width 22, centre 85');
  // resize start handle: start at s=74 (x=214) drag to x=204
  onMouseDown(ev(214, 140)); onMouseMove(ev(204, 140)); onMouseUp();
  check(Math.abs(op.width - 32) < 1e-6 && Math.abs(op.center_s - 80) < 1e-6, 'resize start → width 32, centre 80');
  // slide across the seam (start corner at s=0): drag far left past (140,140)
  onMouseDown(ev(220, 140)); onMouseMove(ev(140, 160)); onMouseUp();   // to left side, s ≈ 460
  check(op.center_s > 400 && op.center_s < 480, 'slide wraps through seam: centre ' + op.center_s.toFixed(1));
  // dims + props
  showDimensions = true; repaint(); updatePathList();
  // move the source: opening keeps its s
  const s0 = op.center_s;
  selectedOpeningId = null; selectedId = R.id;
  onMouseDown(ev(200, 260)); onMouseMove(ev(230, 290)); onMouseUp();
  check(R.x === 170 && op.center_s === s0, 'source moved, opening s unchanged');
  // payload carries openings; with offsets + full round the cut widens
  layer.offset_treatments.push({ id: 'p9', source_path_id: R.id, direction: 'inside', distance: 10, role: 'inner' });
  layer.cap_style = 'full_round';
  const g = _openingGeom(op);
  check(Math.abs(g.width - 32) < 1e-6 && Math.abs((g.b - g.a) - 42) < 1e-6, 'clear width 32, cut 42 with full round');
  check(buildPayload().openings[0].id === op.id, 'payload has openings');
  // INTERACTION PRIORITY: an open route's END marker exactly on the opening's
  // end handle must not steal the pointer (toolpath overlay = diagnostics)
  selectOpening(op.id); setTool('edit');
  const gh = _openingGeom(op), hp = gh.end.pt;
  const prevTP = showToolpath, prevRR = routeResult, w0 = op.width;
  showToolpath = true;
  routeResult = { moves: [{ kind: 'print', start: [hp[0] - 30, hp[1] - 30], end: [hp[0], hp[1]], strand_id: 'x' }] };
  const mk = routeMarkers(routeResult.moves).find(m => m.kind === 'end');
  check(mk && Math.hypot(mk.pos[0] - hp[0], mk.pos[1] - hp[1]) < 1e-9 && !!hitTestRouteMarker(hp[0], hp[1]),
        'an END marker sits exactly on the opening end handle');
  const [hx, hy] = worldToCanvas(hp[0], hp[1]);
  const r0 = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : { left: 0, top: 0 };
  onMouseDown({ clientX: hx + (r0.left || 0), clientY: hy + (r0.top || 0), button: 0, preventDefault() {} });
  check(openingDrag && openingDrag.mode === 'end', 'the opening end handle wins over the END marker');
  onMouseUp();
  showToolpath = prevTP; routeResult = prevRR; op.width = w0;
  // keyboard delete of selected opening
  selectOpening(op.id);
  document.activeElement = document.body;
  deleteOpening(op.id);
  check(layer.openings.length === 0 && selectedOpeningId === null, 'opening deleted');
  // delete path cascades
  setTool('opening'); onMouseDown(ev(230, 170)); 
  check(layer.openings.length === 1, 'placed again');
  deletePath(R.id);
  check(layer.openings.length === 0, 'deleting the wall deletes its openings');
  clearAll();

  // ---- Multiple openings on one wall ----
  addPrimitive('RectanglePath');               // (140,140) 120x120 again
  const W = layer.source_paths[0];
  for (const [x, y] of [[170, 140], [230, 140], [260, 200]]) {
    setTool('opening'); onMouseDown(ev(x, y)); onMouseUp();
  }
  check(layer.openings.length === 3 && new Set(layer.openings.map(o => o.id)).size === 3,
        'three openings on one wall, distinct ids');
  check(layer.openings.every(o => o.source_path_id === W.id), 'all attached to the same wall');
  check(_survivingPieces(W).length === 3, 'wall drawn as three pieces');
  const [A, B, C] = layer.openings;
  const snapAC = () => JSON.stringify([A, C].map(o => [o.center_s, o.width]));
  const before = snapAC();
  // select and slide B only (centre at x=230 → s=90), drag +10
  onMouseDown(ev(230, 140)); onMouseMove(ev(240, 140)); onMouseUp();
  check(selectedOpeningId === B.id && Math.abs(B.center_s - 100) < 1e-6, 'B slid to 100');
  check(snapAC() === before, 'A and C untouched by sliding B');
  // resize B's end handle (s=106 → x=246) to x=250
  onMouseDown(ev(246, 140)); onMouseMove(ev(250, 140)); onMouseUp();
  check(Math.abs(B.width - 16) < 1e-6, 'B resized to 16');
  check(snapAC() === before, 'A and C untouched by resizing B');
  // numeric edit through the panel callback path
  B.width = 20; _syncOpeningPanel(B);
  // delete A only
  selectOpening(A.id); deleteOpening(A.id);
  check(layer.openings.length === 2 && layer.openings[0] === B && layer.openings[1] === C,
        'deleting A keeps B and C');
  // overlapping openings: union drawn as one gap
  B.center_s = 100; B.width = 20; C.center_s = 112; C.width = 20;
  check(_survivingPieces(W).length === 1, 'overlapping openings draw as one gap');
  // clicking inside the overlap selects one opening (last placed wins)
  onMouseDown(ev(250, 140)); onMouseUp();
  check(selectedOpeningId === C.id || selectedOpeningId === B.id, 'overlap click selects an opening');
  check(buildPayload().openings.length === 2, 'payload carries both');
  clearAll();
  console.log('UI SMOKE PASSED');
})();
`);
