// Regression: a TRIMMED source shows no ghost of its untrimmed shape when
// selected (selection outline / hover highlight / handles / hit-testing use
// the visible source). Data: real backend sections, written by
// tests/test_trim_ghost.py (argv[2]).
const DATA = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
// (harness from ui_trim_smoke.js)
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
global.window = { DESIGNER_PRINT_PREVIEW: true, addEventListener() {}, devicePixelRatio: 1 };
global.requestAnimationFrame = () => 0; global.cancelAnimationFrame = () => {};
global.fetch = async () => ({ json: async () => ([]) });
global.console.warn = () => {};
global.setTimeout = () => 0; global.clearTimeout = () => {};   // no backend round trips
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const ev = (wx, wy) => { const [cx, cy] = worldToCanvas(wx, wy); return { clientX: cx, clientY: cy, button: 0 }; };
  const R = { id: 'R', type: 'RectanglePath', label: 'Rect 1', closed: true, role: 'free', visible: true,
              x: 100, y: 100, w: 240, h: 160, rotation: 0, wall: { thickness: 10, align: 'auto', print_reference: false } };
  const C = { id: 'C', type: 'CirclePath', label: 'Circle 1', closed: true, role: 'free', visible: true,
              cx: 340, cy: 180, radius: 80, wall: { thickness: 10, align: 'auto', print_reference: false } };
  _computePrimitivePoints(R); _computePrimitivePoints(C);
  layer.source_paths.push(R, C);
  layer.trims = DATA.trims;
  networkInfo = { trim_sections: DATA.trim_sections, modified_sources: DATA.modified_sources, trims: {}, junctions: [] };
  printable = [{ id: 'x', pts: [[0, 0], [1, 0]], closed: false }];
  _netState = _histState();
  const removed = DATA.trim_sections.filter(q => q.source === 'R' && q.trimmed_by)[0];
  const n_ = removed.pts.length - 1;                                 // a point of the trimmed-away edge
  const pa_ = removed.pts[Math.floor(n_ / 2)], pb_ = removed.pts[Math.ceil(n_ / 2) === Math.floor(n_ / 2) ? Math.floor(n_ / 2) + 1 : Math.ceil(n_ / 2)];
  const mid = [(pa_[0] + pb_[0]) / 2, (pa_[1] + pb_[1]) / 2];
  const far = (polys, q, d) => polys.every(v => _distToPolys(q[0], q[1], [v]) > d);

  // ---- selection / highlight geometry ----
  const vis = _visibleSourcePolys(R);
  check(vis.length >= 1 && far(vis, mid, 1), 'selected rectangle: the trimmed-away edge is not part of its outline');
  check(_visibleSourcePolys(C).every(v => v.pts.every(([x]) => x >= 340 - 1e-6)),
        'selected circle: only its arc outside the rectangle');
  check(R.points.length === 4 && R.w === 240, 'the parametric rectangle is unchanged internally');

  // ---- hit testing favours visible geometry ----
  selectedId = null;
  onMouseDown(ev(mid[0], mid[1])); onMouseUp({});
  check(selectedId === null, 'clicking where the trimmed edge was selects nothing (no invisible hit)');
  onMouseDown(ev(220, 100.5)); onMouseUp({});
  check(selectedId === 'R', 'clicking the visible rectangle selects it');

  // ---- handles on visible geometry ----
  const hs = visibleHandles(R);
  check(hs.every(h => _distToPolys(h.wx, h.wy, vis) < 0.5), 'every rectangle handle sits on visible geometry');
  const ch = visibleHandles(C);
  const rad = ch.find(h => h.key === 'radius');
  check(ch.some(h => h.key === 'center') && rad && rad.wx > 340 &&
        Math.abs(Math.hypot(rad.wx - 340, rad.wy - 180) - 80) < 1e-6, 'circle: centre + radius handle moved onto the visible arc');
  const r0 = C.radius;
  selectedId = 'C';
  onMouseDown(ev(rad.wx, rad.wy)); onMouseMove(ev(340 + (rad.wx - 340) * 1.1, 180 + (rad.wy - 180) * 1.1)); onMouseUp({});
  check(Math.abs(C.radius - r0 * 1.1) < 1e-6, 'resize still works from the relocated radius handle');
  C.radius = r0; _computePrimitivePoints(C);

  // ---- move / rotate stay usable ----
  selectedId = 'R';
  historyCheckpoint();                              // (baseline state for Undo)
  const x0 = R.x;
  onMouseDown(ev(220, 100.5)); onMouseMove(ev(230, 100.5)); onMouseUp({});
  check(Math.abs(R.x - (x0 + 10)) < 1e-9, 'move by dragging the visible rectangle');
  check(!!_rotHandle(R), 'rotation handle still available');
  undo();
  check(layer.source_paths.find(q => q.id === 'R').x === x0, 'undo the move');

  // ---- SNAPPING uses the EFFECTIVE geometry (2026-10-15 regression): a
  // trimmed-away section is no invisible snap target ----
  const onRemoved = (q, sec) => _distToPolys(q[0], q[1], [{ pts: sec.pts, closed: false }]) < 0.5;
  const geoKinds = ['end', 'junction', 'edge'];
  const removedC = DATA.trim_sections.filter(q => q.source === 'C' && q.trimmed_by)[0];
  const cm = removedC.pts[Math.floor(removedC.pts.length / 2)];   // a point of the circle's trimmed-away arc
  for (const [name, sec, q] of [['rectangle edge', removed, mid], ['circle arc', removedC, cm]]) {
    const near = [q[0] - 1.5, q[1] + 1.5];                           // ~4 px from it at this zoom
    check(!findSnap(near[0], near[1], null, false) || !onRemoved(findSnap(near[0], near[1], null, false).pt, sec),
          'findSnap: no snap onto the trimmed-away ' + name);
    setTool('line');
    onMouseMove(ev(near[0], near[1]));
    check(!snapHint || !geoKinds.includes(snapHint.kind) || !onRemoved(snapHint.pt, sec),
          'Line tool hover near the trimmed-away ' + name + ': no invisible snap (' + (snapHint ? snapHint.kind : 'none') + ')');
    onMouseDown(ev(near[0], near[1]));
    check(drawPts.length === 1 && !onRemoved(drawPts[0], sec), 'clicking there does not land on the removed ' + name);
    setTool('edit');
    setTool('draw');
    onMouseDown(ev(near[0], near[1]));
    check(drawPts.length === 1 && !onRemoved(drawPts[0], sec), 'Draw tool: no snap onto the removed ' + name);
    drawPts = []; setTool('edit');
  }
  // legitimate visible geometry still snaps: a surviving edge, a section end where the trim cut it
  const vEdge = findSnap(220, 101.5, null, false);
  check(vEdge && vEdge.kind === 'edge' && Math.abs(vEdge.pt[1] - 100) < 1e-9, 'a visible rectangle edge still snaps');
  const keep = DATA.trim_sections.filter(q => q.source === 'R' && !q.trimmed_by);
  const jEnd = keep[0].pts[keep[0].pts.length - 1];
  const js = findSnap(jEnd[0] + 1, jEnd[1] - 1, null, false);
  check(js && ['junction', 'end'].includes(js.kind) && Math.hypot(js.pt[0] - jEnd[0], js.pt[1] - jEnd[1]) < 1e-6,
        'where a trim cut the wall, the visible section end is a junction snap');
  check(findSnap(near0(removed)[0], near0(removed)[1], null, true) === null, 'Alt: nothing snaps');
  function near0(sec) { const q = sec.pts[0]; return [q[0] + 1, q[1] + 1]; }
  // without the trims the same point snaps onto the (now present) edge — the check above is not vacuous
  const savedNet = networkInfo;
  networkInfo = { trim_sections: DATA.all_sections, modified_sources: [], trims: {}, junctions: [] };
  const back = findSnap(mid[0] - 1.5, mid[1] + 1.5, null, false);
  check(back && onRemoved(back.pt, removed), 'untrimmed, the same hover DOES snap onto that edge');
  networkInfo = savedNet;

  // ---- restoring the trims restores the full representation ----
  layer.trims = [];
  networkInfo = { trim_sections: DATA.all_sections, modified_sources: [], trims: {}, junctions: [] };
  const R1 = layer.source_paths.find(q => q.id === 'R'), C1 = layer.source_paths.find(q => q.id === 'C');
  check(_visibleSourcePolys(R1)[0].pts.length === 4 && visibleHandles(R1).length === getHandles(R1).length,
        'without trims: the whole rectangle and all its handles again');
  check(visibleHandles(C1).find(h => h.key === 'radius').wx === 420, 'circle radius handle back at its normal place');
  console.log('UI TRIM GHOST SMOKE PASSED');
})();
`);
