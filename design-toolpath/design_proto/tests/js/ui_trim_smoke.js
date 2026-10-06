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
global.fetch = async () => ({ json: async () => ([]) });
global.console.warn = () => {};
global.setTimeout = () => 0; global.clearTimeout = () => {};   // no backend round trips
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const ev = (wx, wy) => { const [cx, cy] = worldToCanvas(wx, wy); return { clientX: cx, clientY: cy }; };
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const key = k => (listeners.keydown || []).forEach(f => f({ key: k, preventDefault() {} }));
  const flat = el => [el, ...(el.children || []).flatMap(flat)];

  addPrimitive('RectanglePath');                       // (140,140) 120 x 120
  const R = layer.source_paths[0];
  setTool('line'); onMouseDown(ev(100, 200)); onMouseDown(ev(300, 200)); setTool('edit');
  const L = layer.source_paths[1];
  check(L.type === 'LinePath', 'a line crossing the rectangle');

  // The backend's sections of this design (as /api/effective_paths returns them)
  const sec = (id, source, start, end, inside, u, pts) =>
    ({ id, source, start, end, inside, u_mid: u, pts, trimmed_by: null });
  const sections = () => [
    sec(L.id + ':0', L.id, [], [R.id], { [R.id]: false }, 0.1, [[100, 200], [140, 200]]),
    sec(L.id + ':1', L.id, [R.id], [R.id], { [R.id]: true }, 0.5, [[140, 200], [260, 200]]),
    sec(L.id + ':2', L.id, [R.id], [], { [R.id]: false }, 0.9, [[260, 200], [300, 200]]),
    sec(R.id + ':0', R.id, [L.id], [L.id], { }, 0.375, [[140, 200], [140, 260], [260, 260], [260, 200]]),
    sec(R.id + ':1', R.id, [L.id], [L.id], { }, 0.875, [[260, 200], [260, 140], [140, 140], [140, 200]]),
  ];
  const refresh = () => {                              // a backend response for the current state
    const secs = sections();
    for (const t of layer.trims) {
      const s = secs.find(x => x.source === t.source_path_id && JSON.stringify(x.inside) === JSON.stringify(t.inside)
                          && Math.abs(x.u_mid - t.u_mid) < 1e-9);
      if (s) s.trimmed_by = t.id;
    }
    networkInfo = { trim_sections: secs, trims: Object.fromEntries(layer.trims.map(t => [t.id, { status: 'ok' }])),
                    modified_sources: [], junctions: [] };
    _netState = _histState();
  };
  scheduleRefresh(); refresh();

  // ---- hover: only the section under the pointer ----
  setTool('trim');
  check(tool === 'trim' && document.getElementById('tool-trim').classList, 'Trim tool active');
  onMouseMove(ev(200, 203));
  check(trimHover && trimHover.id === L.id + ':1', 'hover picks the line section INSIDE the rect');
  check(trimHover.pts.length === 2 && trimHover.pts[0][0] === 140 && trimHover.pts[1][0] === 260,
        'highlight is that section only, not the whole line');
  onMouseMove(ev(120, 202));
  check(trimHover.id === L.id + ':0', 'the nearest of several sections is chosen');
  onMouseMove(ev(258, 230));
  check(trimHover.id === R.id + ':0', 'a section of the other path');
  onMouseMove(ev(600, 600));
  check(trimHover === null, 'nothing hovered away from geometry');

  // ---- stale sections are never offered ----
  layer.return_paths = false;                         // any edit not yet refreshed
  onMouseMove(ev(200, 203));
  check(trimHover === null && document.getElementById('hint').textContent.includes('updating'),
        'stale sections: no hover until the backend answers');
  layer.return_paths = true;
  onMouseMove(ev(200, 203));
  check(trimHover && trimHover.id === L.id + ':1', 'current again: hover works');

  // ---- click: one trim = one undo step, signature stored ----
  const steps = _hist.undo.length;
  onMouseDown(ev(200, 203));
  check(layer.trims.length === 1, 'click trims the section');
  const t = layer.trims[0];
  check(t.source_path_id === L.id && t.start[0] === R.id && t.end[0] === R.id &&
        t.inside[R.id] === true && t.u_mid === 0.5, 'the trim stores the section signature');
  check(_hist.undo.length === steps + 1, 'ONE undo step');
  check(tool === 'trim', 'Trim stays active for further clicks');
  onMouseDown(ev(200, 203));
  check(layer.trims.length === 1, 'no second trim of the same section before the refresh');
  refresh();
  onMouseMove(ev(200, 203));
  check(trimHover === null && document.getElementById('hint').textContent.includes('already trimmed'),
        'a trimmed section is not offered again');
  onMouseDown(ev(258, 230));
  check(layer.trims.length === 2 && layer.trims[1].source_path_id === R.id, 'continue trimming another section');
  check(_hist.undo.length === steps + 2, 'second click = second undo step');
  check(L.type === 'LinePath' && R.type === 'RectanglePath' && R.w === 120, 'sources unchanged (non-destructive)');

  // ---- undo / redo ----
  undo(); check(layer.trims.length === 1, 'undo restores the last section');
  undo(); check(layer.trims.length === 0, 'undo restores the first section');
  redo(); redo(); check(layer.trims.length === 2, 'redo suppresses both again');

  // ---- payload ----
  const pl = buildPayload().trims;
  check(pl.length === 2 && pl[0].start !== layer.trims[0].start, 'payload carries (copied) trims');

  // ---- Esc / tool switch exits Trim; selection outside Trim unchanged ----
  key('Escape');
  check(tool === 'edit' && trimHover === null, 'Esc exits Trim');
  setTool('trim'); setTool('rect'); check(trimHover === null, 'switching tools exits Trim'); setTool('edit');
  // (2026-10-06: selection hit-tests the VISIBLE source — a trimmed-away
  // section no longer selects its path; the visible part does)
  refresh();
  selectedId = null;
  onMouseDown(ev(200, 200)); onMouseUp();
  check(selectedId !== L.id && layer.trims.length === 2, 'Edit: clicking a trimmed-away section does not select the line');
  onMouseDown(ev(120, 200)); onMouseUp();
  check(selectedId === L.id && layer.trims.length === 2, 'Edit: clicking the visible part of the line selects it (no trim)');

  // ---- Restore (Properties) ----
  refresh();
  const panel = document.getElementById('path-props');
  panel.children.length = 0; updatePropPanel();
  const btn = flat(panel).find(n => n.textContent === 'Restore trimmed sections');
  check(!!btn && flat(panel).some(n => n.id === 'trim-note'), 'Properties list the trimmed sections');
  btn.onclick();
  check(layer.trims.length === 1 && layer.trims[0].source_path_id === R.id, 'Restore removes only this path\\'s trims');
  undo(); check(layer.trims.length === 2, 'Restore is undoable');

  // ---- duplicate: trims do not travel with a copy ----
  selectedId = L.id;
  const copy = duplicateSelected();
  check(copy && layer.trims.length === 2 && !layer.trims.some(x => x.source_path_id === copy.id),
        'a duplicate is the untrimmed source');
  deletePath(copy.id);

  // ---- delete ----
  deletePath(L.id);
  check(layer.trims.length === 1 && layer.trims[0].source_path_id === R.id,
        'deleting a path deletes its own trims');
  undo(); check(layer.trims.length === 2, 'undo brings them back');
  deletePath(R.id);
  check(layer.trims.length === 1 && layer.trims[0].source_path_id === L.id,
        'deleting a bounding path keeps the other path\\'s trim (it becomes unresolved)');
  undo();

  // ---- no section under the pointer on a path ----
  networkInfo.trim_sections = networkInfo.trim_sections.filter(s => s.source !== R.id);
  _netState = _histState();
  setTool('trim'); onMouseMove(ev(200, 140));
  check(trimHover === null && document.getElementById('hint').textContent.includes('nothing to trim'),
        'a path without a section there says so');

  // ---- Clear All ----
  clearAll();
  check(layer.trims.length === 0, 'Clear All removes trims');
  undo(); check(layer.trims.length === 2, 'Clear All is undoable');
  console.log('UI TRIM SMOKE PASSED');
})();
`);
