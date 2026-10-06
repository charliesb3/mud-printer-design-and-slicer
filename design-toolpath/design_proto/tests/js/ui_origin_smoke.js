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
  const ev = (wx, wy) => { const [cx, cy] = worldToCanvas(wx, wy); return { clientX: cx, clientY: cy }; };
  const mv = (a, b, sid) => ({ kind: 'print', strand_id: sid, start: a, end: b });
  // two closed square components (strands 'a' and 'b') + an open line ('c')
  const sq = (x, y, s, sid) => [mv([x, y], [x + s, y], sid), mv([x + s, y], [x + s, y + s], sid),
                                mv([x + s, y + s], [x, y + s], sid), mv([x, y + s], [x, y], sid)];
  const moves = [...sq(100, 100, 100, 'a'), { kind: 'travel', start: [100, 100], end: [250, 250] },
                 ...sq(250, 250, 60, 'b'), { kind: 'travel', start: [250, 250], end: [40, 380] },
                 mv([40, 380], [140, 380], 'c')];
  printable = [{ id: 'a', pts: [[100, 100], [200, 100], [200, 200], [100, 200]], closed: true },
               { id: 'b', pts: [[250, 250], [310, 250], [310, 310], [250, 310]], closed: true },
               { id: 'c', pts: [[40, 380], [140, 380]], closed: false }];
  addPrimitive('RectanglePath');
  const geom = JSON.stringify(layer.source_paths);
  routeResult = { moves, metrics: {}, closure: {} };
  showToolpath = true;

  // ---- markers ----
  const mk = routeMarkers(moves);
  check(mk.filter(m => m.kind === 'origin').length === 2, 'one origin marker per closed run');
  check(!mk.some(m => (m.kind === 'start' || m.kind === 'end') && m.run < 2), 'closed runs: no Start / End pair');
  check(mk.filter(m => m.run === 2).map(m => m.kind).join() === 'start,end', 'open run keeps Start and End');
  mainRec.length = 0; drawToolpath(moves);
  const texts = mainRec.filter(r => r.op === 'fillText').map(r => r.args[0]);
  check(texts.join() === 'START,END', 'START / END labels only for the open run');

  // ---- drag the origin of component a along its own strands ----
  const steps = _hist.undo.length;
  setTool('edit');
  onMouseDown(ev(100, 100));
  check(originDrag && originDrag.strands.has('a') && !originDrag.strands.has('b'), 'grabbing the origin of run a');
  onMouseMove(ev(205, 160));                       // near a's right edge
  check(Math.abs(originDrag.pos[0] - 200) < 1e-9 && Math.abs(originDrag.pos[1] - 160) < 1e-9,
        'the origin is projected onto the printable route (not free XY)');
  onMouseMove(ev(248, 260));                       // nearer to b — still constrained to a
  check(originDrag.strand === 'a', 'constrained to its own component');
  onMouseMove(ev(150, 205));
  onMouseUp({});
  check(layer.route_origins.length === 1 && layer.route_origins[0].strand === 'a' &&
        Math.abs(layer.route_origins[0].u - 0.625) < 1e-9, 'stored as {strand, u} (u = fraction of the strand)');
  check(_hist.undo.length === steps + 1, 'one origin drag = one undo step');
  check(JSON.stringify(layer.source_paths) === geom, 'moving the origin does not change geometry');
  check(buildPayload().route_origins[0].strand === 'a', 'payload carries the route origins');

  // ---- second component: its own origin; re-dragging a replaces a's only ----
  routeResult = { moves, metrics: {}, closure: {} };
  onMouseDown(ev(250, 250)); onMouseMove(ev(311, 280)); onMouseUp({});
  check(layer.route_origins.length === 2 && layer.route_origins[1].strand === 'b', 'each closed component its own origin');
  routeResult = { moves, metrics: {}, closure: {} };
  onMouseDown(ev(100, 100)); onMouseMove(ev(100, 150)); onMouseUp({});
  check(layer.route_origins.length === 2 && layer.route_origins.filter(o => o.strand === 'a').length === 1,
        'a new origin for a replaces the old one (one per component)');

  // ---- undo / redo ----
  undo(); check(layer.route_origins.find(o => o.strand === 'a').u === 0.625, 'undo restores the previous origin');
  undo(); check(layer.route_origins.length === 1, 'undo removes the second origin');
  redo(); redo(); check(layer.route_origins.length === 2, 'redo');

  // ---- open route: no origin to drag ----
  routeResult = { moves, metrics: {}, closure: {} };
  originDrag = null;
  check(hitTestOrigin(40, 380) === null, 'the Start of an open run is not a route origin');

  // ---- playback begins at the (routed) origin ----
  _setupPlayback(moves);
  const n0 = _nozzleAtPos(0);
  check(n0[0] === moves[0].start[0] && n0[1] === moves[0].start[1], 'playback starts where the route starts');

  // ---- markers win over design geometry: an opening under a marker ----
  clearAll();
  addPrimitive('RectanglePath');                   // (140,140) 120 x 120
  const R = layer.source_paths[0];
  setTool('opening'); onMouseDown(ev(200, 140)); onMouseUp({}); setTool('edit');
  const op = layer.openings[0];
  const opBefore = JSON.stringify(layer.openings), geomBefore = JSON.stringify(layer.source_paths);
  const ring = sq(140, 140, 120, R.id);
  printable = [{ id: R.id, pts: [[140, 140], [260, 140], [260, 260], [140, 260]], closed: true }];
  // a CLOSED route whose origin sits right on the opening
  const rot = [mv([200, 140], [260, 140], R.id), mv([260, 140], [260, 260], R.id), mv([260, 260], [140, 260], R.id),
               mv([140, 260], [140, 140], R.id), mv([140, 140], [200, 140], R.id)];
  routeResult = { moves: rot, metrics: {}, closure: {} };
  showToolpath = true; selectedId = null;
  onMouseDown(ev(201, 141));
  check(originDrag && !openingDrag && !dragging && !bodyDragging, 'origin on an opening: the ORIGIN is grabbed, not the opening');
  onMouseMove(ev(230, 150)); onMouseMove(ev(258, 200)); onMouseUp({});
  check(JSON.stringify(layer.openings) === opBefore && JSON.stringify(layer.source_paths) === geomBefore,
        'dragging the origin never moves the opening or the path');
  check(layer.route_origins.length === 1 && layer.route_origins[0].strand === R.id, 'only the route origin changed');
  // an OPEN route whose START sits on the opening: the click is swallowed
  routeResult = { moves: [mv([200, 140], [260, 140], R.id), mv([260, 140], [260, 260], R.id)], metrics: {}, closure: {} };
  onMouseDown(ev(200, 141)); onMouseMove(ev(230, 160)); onMouseUp({});
  check(!originDrag && JSON.stringify(layer.openings) === opBefore, 'Start of an open route over an opening: nothing moves');
  check(routeMarkers(routeResult.moves).map(m => m.kind).join() === 'start,end', 'an open route still shows Start / End');

  // ---- solid bead rendering: the printed line IS one blue bead ----
  check(BEAD_ALPHA === 1.0 && BEAD_COLOR === MOVE_COLORS.print, 'bead = the print colour, opaque');
  routeResult = { moves, metrics: {}, closure: {} };
  printable = [{ id: 'a', pts: [[100, 100], [200, 100], [200, 200], [100, 200]], closed: true },
               { id: 'b', pts: [[250, 250], [310, 250], [310, 310], [250, 310]], closed: true },
               { id: 'c', pts: [[40, 380], [140, 380]], closed: false }];
  toggleBeads(true);
  showArrows = false;
  selectedId = null;                               // (edit handles are not centrelines)
  mainRec.length = 0; rec.length = 0; repaint();
  const draws = mainRec.filter(r => r.op === 'drawImage');
  check(draws.length === 1 && draws[0].alpha === 1, 'all beads composited once, opaque (no overlap build-up)');
  const bs = rec.filter(r => r.op === 'stroke');
  check(bs.length === 3 && bs.every(r => r.strokeStyle === MOVE_COLORS.print && r.lineCap === 'round'),
        'one solid blue round-capped stroke per printable centreline');
  const after = mainRec.slice(mainRec.indexOf(draws[0])).filter(r => r.op === 'stroke');
  check(!after.some(r => r.strokeStyle === MOVE_COLORS.print || r.strokeStyle === MOVE_COLORS.retrace),
        'no centreline / print line drawn through the bead (no two-tone)');
  check(after.some(r => r.strokeStyle === MOVE_COLORS.travel), 'travel lines still drawn above');
  check(mainRec.filter(r => r.op === 'fill').length >= 3, 'origin / start / end dots still drawn on top');
  toggleBeads(false);
  mainRec.length = 0; repaint();
  check(mainRec.filter(r => r.op === 'stroke').some(r => r.strokeStyle === MOVE_COLORS.print && r.lineWidth >= 1.5),
        'Beads OFF: the normal vector / toolpath view');
  console.log('UI ORIGIN SMOKE PASSED');
})();
`);
