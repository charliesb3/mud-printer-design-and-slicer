// UI smoke test for ARCHITECTURAL SNAPPING (2026-10-15): screen-distance
// snap radii (no long-distance magnetism), priority (ends / junctions >
// wall lines, where a near-45° angle meets them > gentle 0 / 45 / 90° …
// angles > grid points on the physical 1 ft grid), Alt = no snapping,
// Shift = the angle locked, the cue (snapHint kind / label / guide).
// Run via tests/test_snapping.py.
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
global.console.warn = () => {};
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const ev = (wx, wy, extra) => { const [cx, cy] = worldToCanvas(wx, wy); return Object.assign({ clientX: cx, clientY: cy }, extra || {}); };
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const near = (a, b, t = 1e-9) => Math.abs(a - b) <= t;
  const at = (q, x, y, t = 1e-9) => q && near(q[0], x, t) && near(q[1], y, t);
  // zoom 1: 2 px per inch (0, 0, 0 view); a rectangle 140..260 × 140..260
  addPrimitive('RectanglePath');
  setView(1, 0, 0);
  check(near(_viewScale(), 2), '2 px / in at this view');

  // ---- radii are on SCREEN ----
  check(findSnap(200, 143, null, false).kind === 'edge', 'edge 3 in = 6 px away: snaps');
  check(findSnap(200, 146, null, false) === null, 'edge 6 in = 12 px away: no snap (was 8 world in = always)');
  setView(4, -400, -400);                            // 8 px / in
  check(findSnap(200, 141, null, false) && findSnap(200, 142.5, null, false) === null,
        'zoomed in: 1 in (8 px) snaps, 2.5 in (20 px) does not — constant on screen');
  setView(1, 0, 0);

  // ---- angle snapping (gentle, never a constraint) ----
  const a0 = [20, 40];
  let h = snapDrawPoint(120, 45, a0, {});            // 2.9° from horizontal
  check(h && h.kind === 'angle' && h.label === '0°' && at(h.pt, 120, 40), 'within 4° of 0°: the point lies on the horizontal');
  h = snapDrawPoint(90, 109, a0, {});                // 44.6°
  check(h && h.kind === 'angle' && h.label === '45°' && near(h.pt[0] - 20, h.pt[1] - 40, 1e-9), '45° snaps');
  h = snapDrawPoint(23, 115, a0, {});                // 87.7°
  check(h && h.label === '90°' && near(h.pt[0], 20), '90° snaps');
  h = snapDrawPoint(-60, 44, a0, {});                // 177°
  check(h && h.label === '180°' && near(h.pt[1], 40), '180° snaps');
  h = snapDrawPoint(114, 66, a0, {});                // 15.5°, mid-square: a free point
  check(h === null, 'an arbitrary angle stays free (no snap of any kind)');
  check(snapDrawPoint(25, 41, a0, {}) === null || snapDrawPoint(25, 41, a0, {}).kind !== 'angle',
        'no angle snap on a very short segment (meaningless there)');

  // ---- Shift locks, Alt frees ----
  h = snapDrawPoint(110, 70, a0, { shiftKey: true });
  check(h && h.kind === 'lock' && h.label === '0° lock' && near(h.pt[1], 40), 'Shift: locked to the nearest architectural angle');
  h = snapDrawPoint(80, 110, a0, { shiftKey: true });
  check(h && h.label === '45° lock' && near(h.pt[0] - 20, h.pt[1] - 40, 1e-9), 'Shift near 45°: 45° lock');
  check(snapDrawPoint(200, 141, a0, { altKey: true }) === null && snapDrawPoint(120, 45, a0, { altKey: true }) === null,
        'Alt / Option: no snapping at all (edges, angles, grid)');

  // ---- priority: visible geometry first; an angle meeting a wall meets it exactly ----
  h = snapDrawPoint(138, 172, [60, 170.5], {});      // 1.1° and 2 in from the wall x = 140
  check(h && h.kind === 'edge' && at(h.pt, 140, 170.5) && /0°/.test(h.label),
        'near a wall at a near-0° angle: the horizontal meets the wall exactly (' + (h && h.label) + ')');
  h = snapDrawPoint(138, 186, [60, 170.5], {});      // 11° : no angle — plain nearest point on the wall
  check(h && h.kind === 'edge' && at(h.pt, 140, 186), 'off-angle near a wall: the nearest wall point');
  h = snapDrawPoint(110, 66, [60, 170.5], { shiftKey: true });   // Shift: 0°... no, nearest is −45°
  check(h && /lock/.test(h.label), 'Shift always locks');
  h = snapDrawPoint(120, 175, [60, 170.5], { shiftKey: true });  // locked 0°, the ray reaches the wall 20 in on — not near
  check(h && h.kind === 'lock' && near(h.pt[1], 170.5), 'Shift: a wall far along the ray does not pull the point');
  h = snapDrawPoint(138.5, 175, [60, 170.5], { shiftKey: true });
  check(h && h.kind === 'edge' && at(h.pt, 140, 170.5) && h.label === 'connect · 0° lock', 'Shift: the locked ray meets the wall near the cursor');
  addPrimitive('LinePath');                          // (140,200) → (260,200): its ends
  const L = layer.source_paths[1];
  L.start = [300, 300]; L.end = [380, 300]; _computePrimitivePoints(L);
  h = snapDrawPoint(302, 303, [200, 302], {});       // an end 3.6 in away beats the 0° angle
  check(h && h.kind === 'end' && at(h.pt, 300, 300), 'a path end outranks the angle and the grid');

  // ---- grid (physical: the finest grid drawn) ----
  check(_gridSnapStep() === 12, 'at 2 px / in the grid drawn is 2 ft with 1 ft lines: snapping uses 1 ft');
  h = snapDrawPoint(49, 71, null, {});
  check(h && h.kind === 'grid' && at(h.pt, 48, 72), 'a free point near a grid point snaps onto it');
  check(snapDrawPoint(54, 66, null, {}) === null, 'farther from the grid point: free');
  viewReset();                                       // home: the 120 in envelope, ~5.6 px / in
  check(_gridSnapStep() === 6, 'at the home view the grid has 6 in subdivisions: snapping uses them');
  h = snapDrawPoint(36.6, 41.4, null, {});
  check(h && h.kind === 'grid' && at(h.pt, 36, 42), '6 in grid point');
  h = snapDrawPoint(60.5, 13, [12, 12], {});         // 0° then the free coordinate onto the grid
  check(h && h.kind === 'angle' && at(h.pt, 60, 12), 'along a horizontal the length lands on the grid');
  check(findSnap(200, 144, null, false) === null, 'home view: 4 in (≈ 22 px) from a wall no longer snaps');
  setView(1, 0, 0);

  // ---- the Line tool end to end: click, hover (cue), click ----
  setTool('line');
  onMouseDown(ev(63, 165));                          // (8.5 px from the nearest grid point: free)
  onMouseMove(ev(138, 166.5));
  check(snapHint && snapHint.kind === 'edge' && /0°/.test(snapHint.label) && snapHint.anchor, 'hover cue: the snap type + the angle guide');
  onMouseDown(ev(138, 166.5));
  const N = layer.source_paths[layer.source_paths.length - 1];
  check(N.type === 'LinePath' && at(N.start, 63, 165) && at(N.end, 140, 165), 'the line ends exactly on the wall, exactly horizontal');
  setTool('line');
  onMouseDown(ev(60, 230));
  onMouseMove(ev(130, 241, { shiftKey: true }));
  check(snapHint && snapHint.kind === 'lock', 'Shift held while hovering: the angle lock cue');
  onMouseMove(ev(130, 252));
  check(!snapHint || snapHint.kind !== 'lock', 'released: free again (17°)');
  onMouseDown(ev(130, 252, { altKey: true }));
  const M = layer.source_paths[layer.source_paths.length - 1];
  check(at(M.end, 130, 252, 1e-6), 'Alt click: placed exactly where clicked');
  // rectangle tool: geometry + grid, never angles
  setTool('rect');
  onMouseDown(ev(49, 71));
  check(at(drawPts[0], 48, 72), 'rectangle corner snaps to the grid');
  drawPts = []; setTool('edit');
  console.log('UI SNAP SMOKE PASSED');
})();
`);
