// UI smoke (M5, 2026-10-15): TRIMS BOUNDED BY PERSISTENT JUNCTIONS, driven
// through the real static/app.js (DOM stubbed) against the REAL backend:
// circle + rectangle trimmed with the Trim tool → a branch drawn from their
// junction → the trims stay, the removed geometry stays removed; undo / redo;
// a wall crossing the removed stretches; deleting the branches.
// Run via layer_assembly/tests/test_trim_junction_ui.py.
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
eval(src + `
;(async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const refresh = async () => { historyCheckpoint(); await fetchEffectivePaths(); };
  const st = () => Object.fromEntries(Object.entries(networkInfo.trims || {}).map(([k, v]) => [k, v.status + (v.via ? '/' + v.via : '')]));
  const trimmed = src => (networkInfo.trim_sections || []).filter(s => s.source === src && s.trimmed_by).length;
  // circle + rectangle, 10 in walls
  addPrimitive('RectanglePath');
  const R = layer.source_paths[0];
  Object.assign(R, { x: 0, y: 0, w: 120, h: 80 }); _computePrimitivePoints(R);
  addPrimitive('CirclePath');
  const C = layer.source_paths[1];
  Object.assign(C, { cx: 120, cy: 40, radius: 40 }); _computePrimitivePoints(C);
  setWallProps([R.id, C.id], { thickness: 10 });
  await refresh();
  // trim both overlap sections with the Trim tool's own function
  const overlap = () => networkInfo.trim_sections.filter(s => !s.trimmed_by && s.inside[s.source === R.id ? C.id : R.id]);
  for (const s of overlap()) trimSection(s);
  await refresh();
  const T = layer.trims.map(t => t.id);
  check(T.length === 2 && T.every(id => st()[id] === 'ok'), 'circle + rectangle: both overlap sections trimmed (one perimeter)');
  const visC0 = JSON.stringify(_visibleSourcePolys(C)), visR0 = JSON.stringify(_visibleSourcePolys(R));
  // JUNCTION IDENTITY inspector (manual review): ids of the two C × R junctions
  const I0 = await inspectJunctions();
  const top = I0.list.find(j => Math.hypot(j.x - 120, j.y - 80) < 1e-6);
  check(I0.list.length === 2 && top && top.walls.join() === [C.id, R.id].sort().join() &&
        T.every(id => I0.trims[id].junctions.includes(top.id)),
        'inspector: two junctions; the one at (120, 80) is ' + (top && top.id) + ', both trims bounded by it');
  // a branch drawn FROM the junction at (120, 80)
  addPrimitive('LinePath');
  const L = layer.source_paths[2];
  L.start = [120, 80]; L.end = [120, 130]; _computePrimitivePoints(L);
  await refresh();
  check(T.every(id => st()[id] === 'ok/junctions'), 'a branch joins the junction: both trims stay (resolved by their junctions)');
  check(networkInfo.trim_sections.some(s => s.source === C.id && s.start.concat(s.end).includes(L.id)),
        'the branch is a member of that junction now');
  const I1 = await inspectJunctions();
  const top1 = I1.list.find(j => Math.hypot(j.x - 120, j.y - 80) < 1e-6);
  check(top1 && top1.id === top.id && top1.walls.length === 3 && top1.walls.includes(L.id) &&
        T.every(id => JSON.stringify(I1.trims[id].junctions) === JSON.stringify(I0.trims[id].junctions)),
        'inspector: the SAME junction id (' + top.id + ') with the branch as a third member; the trims reference the same junctions');
  check(JSON.stringify(_visibleSourcePolys(C)) === visC0 && JSON.stringify(_visibleSourcePolys(R)) === visR0,
        'the circle / rectangle drawn exactly as before — no trimmed geometry back');
  // UNDO: the branch removed — the trims resolve exactly again
  check(undo() && undo(), 'undo (placing the branch, adding it)');
  await fetchEffectivePaths();
  check(layer.source_paths.length === 2 && T.every(id => st()[id] === 'ok'), 'undo: no branch, trims resolve as before');
  check(redo() && redo(), 'redo');
  await fetchEffectivePaths();
  check(layer.source_paths.length === 3 && T.every(id => st()[id] === 'ok/junctions'), 'redo: the branch back, trims still there');
  // a wall CROSSING both removed stretches (placed directly, e.g. Alt-drawn)
  addPrimitive('LinePath');
  const X = layer.source_paths[3];
  X.start = [60, 40]; X.end = [150, 40]; _computePrimitivePoints(X);
  await refresh();
  check(T.every(id => st()[id] === 'ok/junctions') && trimmed(C.id) === 2 && trimmed(R.id) === 2,
        'a wall crossing the removed stretches: they stay removed (each split in two, both trimmed)');
  check(JSON.stringify(_visibleSourcePolys(C)) === visC0 && JSON.stringify(_visibleSourcePolys(R)) === visR0,
        '… the circle and rectangle still drawn as before');
  // deleting the branches: everything as at first
  deletePath(X.id); deletePath(L.id);
  await refresh();
  check(layer.source_paths.length === 2 && T.every(id => st()[id] === 'ok'), 'branches deleted: trims as at first');
  console.log('UI TRIM JUNCTION SMOKE PASSED');
})().catch(e => { console.error(e); process.exit(1); });
`);
