// UI smoke test (Designer correction pass): TRIM stays authoritative after
// Wall System assignment. A rect + a line crossing it are members of one
// Skin + Web system with a web; trimming the line's outer stubs and the
// section inside the rect keeps the system's membership / settings, each
// click is ONE undo step, and when a trim changes connectivity (the backend
// reports split member groups) the owned web records follow WITHOUT a step
// of their own — Undo / Redo stay coherent (undo used to re-sync against the
// undone response and clear Redo). Run via tests/test_trim.py.
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
global.fetch = async () => ({ json: async () => ([]) });
global.console.warn = () => {};
global.setTimeout = () => 0; global.clearTimeout = () => {};   // no backend round trips
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const ev = (wx, wy) => { const [cx, cy] = worldToCanvas(wx, wy); return { clientX: cx, clientY: cy }; };
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };

  addPrimitive('RectanglePath');                       // (140,140) 120 x 120
  const R = layer.source_paths[0];
  setTool('line'); onMouseDown(ev(100, 200)); onMouseDown(ev(300, 200)); setTool('edit');
  const L = layer.source_paths[1];
  layer.wall_systems = [{ id: 'WS1', name: 'Walls', type: 'skin_web', thickness: 10, align: 'auto', params: {},
                          print_reference: false, members: [R.id, L.id],
                          web: { pattern: 'zigzag', params: { spacing: 20 }, variation_index: 0 } }];
  const sysJSON = () => JSON.stringify(layer.wall_systems);
  const sys0 = sysJSON();

  const sec = (id, source, start, end, inside, u, pts) =>
    ({ id, source, start, end, inside, u_mid: u, pts, trimmed_by: null });
  const sections = () => [
    sec(L.id + ':0', L.id, [], [R.id], { [R.id]: false }, 0.1, [[100, 200], [140, 200]]),
    sec(L.id + ':1', L.id, [R.id], [R.id], { [R.id]: true }, 0.5, [[140, 200], [260, 200]]),
    sec(L.id + ':2', L.id, [R.id], [], { [R.id]: false }, 0.9, [[260, 200], [300, 200]]),
    sec(R.id + ':0', R.id, [L.id], [L.id], { }, 0.375, [[140, 200], [140, 260], [260, 260], [260, 200]]),
    sec(R.id + ':1', R.id, [L.id], [L.id], { }, 0.875, [[260, 200], [260, 140], [140, 140], [140, 200]]),
  ];
  // a backend response for the CURRENT state: trimming the line's inside
  // section leaves its stubs attached; trimming all three detaches the line
  const respond = () => {
    const secs = sections();
    for (const t of layer.trims) {
      const s = secs.find(x => x.source === t.source_path_id && Math.abs(x.u_mid - t.u_mid) < 1e-9);
      if (s) s.trimmed_by = t.id;
    }
    const lineGone = [0.1, 0.5, 0.9].every(u => layer.trims.some(t => t.source_path_id === L.id && t.u_mid === u));
    networkInfo = { trim_sections: secs, trims: Object.fromEntries(layer.trims.map(t => [t.id, { status: 'ok' }])),
                    modified_sources: [], junctions: [], wall_systems: [],
                    source_networks: lineGone ? [{ id: 'n1', sources: [R.id] }, { id: 'n2', sources: [L.id] }]
                                              : [{ id: 'n1', sources: [R.id, L.id] }] };
    _netState = _histState();
    _afterNetworkUpdate();
  };
  scheduleRefresh(); respond(); respond();
  const webs = () => (layer.infills || []).filter(f => f.owner === 'WS1');
  check(webs().length === 1, 'one connected member group: one owned web record');

  setTool('trim');
  const steps = _hist.undo.length;
  for (const [x, y, u] of [[120, 202, 0.1], [280, 202, 0.9]]) {
    onMouseMove(ev(x, y));
    check(trimHover && trimHover.u_mid === u, 'hover offers the stub section ' + u);
    onMouseDown(ev(x, y)); respond();
  }
  check(layer.trims.length === 2 && _hist.undo.length === steps + 2, 'two trims after assignment: two undo steps');
  check(sysJSON() === sys0, 'Wall System membership and settings unchanged by Trim');

  // the final remaining unwanted section can still be trimmed
  onMouseMove(ev(200, 203));
  check(trimHover && trimHover.u_mid === 0.5, 'the last section is still offered');
  onMouseDown(ev(200, 203));
  check(layer.trims.length === 3 && _hist.undo.length === steps + 3, 'third trim: ONE more undo step');
  respond();                                           // the line is now detached: groups split
  check(webs().length === 2, 'web records follow the split member groups');
  check(_hist.undo.length === steps + 3, 'the web regrouping is not an undo step of its own');
  check(sysJSON() === sys0, 'membership still unchanged');

  // ---- undo / redo stay coherent ----
  undo(); respond();
  check(layer.trims.length === 2 && webs().length === 1, 'undo: the last trim and the old web grouping return');
  check(canRedo(), 'the undone response does not clear Redo');
  undo(); respond(); undo(); respond();
  check(layer.trims.length === 0 && webs().length === 1 && sysJSON() === sys0, 'undo to before the trims');
  redo(); respond(); redo(); respond(); redo(); respond();
  check(layer.trims.length === 3 && webs().length === 2, 'redo all three trims');
  check(!canRedo() && _hist.undo.length === steps + 3, 'history intact: no extra steps');
  check(L.type === 'LinePath' && R.type === 'RectanglePath', 'sources unchanged (non-destructive)');
  console.log('UI TRIM WALL SYSTEM SMOKE PASSED');
})();
`);
