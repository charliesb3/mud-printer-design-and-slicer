// M1 OWNERSHIP (2026-10-15, WALL_ARCHITECTURE.md): editing persistent wall /
// material / Wall Geometry properties while a Variant is open writes them to
// their OWNER (Base / the introducing design) — the Variant's delta never gains
// them; layout state (geometry, new / removed walls, openings) stays the
// Variant's; existing Variant divergences are kept and reported. The real
// app.js against the REAL backend. Run via layer_assembly/tests/test_ownership_ui.py.
// (harness from design_proto/tests/js/ui_trim_smoke.js)
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
const BASE = process.argv[2];                 // the real backend (started by the pytest wrapper)
const realFetch = fetch;
const calls = [];
global.fetch = (url, opts) => { calls.push(url); return realFetch(BASE + url, opts); };
global.console.warn = () => {};
const realSetTimeout = setTimeout;
// the Designer's background refreshes (route / effective paths) are not part
// of this test and must not race it; every other timer is real
global.setTimeout = (fn, ms, ...a) => (fn && /^(runRoute|fetchEffectivePaths)$/.test(fn.name))
  ? { unref() {}, ref() {} } : realSetTimeout(fn, ms, ...a);
const STATIC = path.join(__dirname, '..', '..', '..', 'design_proto', 'static');
let src = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
src += '\n' + fs.readFileSync(path.join(STATIC, 'opening_shape.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'assembly.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'wallsystems.js'), 'utf8');
async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const TEXT = fs.readFileSync(process.argv[3], 'utf8');
  check(await openProjectText(TEXT, '10x.muddesign') === true && project.active === 'd1', '10xtest1 opens with Variant 1 active');
  // (M2: opening migrates — the stored divergence was dropped and reported; to
  // test that an EXISTING divergence is never touched by write-through, one is
  // re-created in memory, as an unmigrated page would hold it)
  check(_variantDivergences('d1').length === 0, 'schema 7: the old Variant divergence was dropped on open');
  await editDesign('base');
  project.deltas.d1.settings.junction_style = 'round'; project.deltas.d1.settings.junction_radius = 7;
  await editDesign('d1');
  check(_variantDivergences('d1').join() === 'junction style,junction radius', 'a stored divergence is reported (junction style, radius)');
  const D = () => project.deltas.d1, B = () => project.baseDoc;
  // Wall Geometry edited while the Variant is open → Base
  window.Designer.setWallGeometry({ cap_style: 'full_round' });
  await syncActiveDesign();
  check(B().cap_style === 'full_round' && !('cap_style' in (D().settings || {})), 'cap style edited in Variant 1 is written to Base, not the Variant');
  check(D().settings.junction_style === 'round' && D().settings.junction_radius === 7, 'the EXISTING divergence is left untouched (rendered as before)');
  // junction override → Base
  window.Designer.setWallGeometry({ junction_overrides: [{ key: 'p1.wall+|p2.wall+#0', treatment: 'round', radius: 3 }] });
  await syncActiveDesign();
  check((B().junction_overrides || []).some(o => o.key === 'p1.wall+|p2.wall+#0') && !(D().patch || {}).junction_overrides,
        'a junction override edited in the Variant belongs to Base');
  // a wall's thickness (its own envelope) → Base
  layer.source_paths.find(p => p.id === 'p1').envelope.thickness = 8;
  await syncActiveDesign();
  check(B().source_paths.find(p => p.id === 'p1').envelope.thickness === 8 &&
        !(((D().patch || {}).source_paths || {}).p1 || {}).envelope, 'wall thickness edited in the Variant → Base');
  // a wall's Corner R → its owner
  layer.source_paths.find(p => p.id === 'p2').corner_radius = 3;
  await syncActiveDesign();
  check(B().source_paths.find(p => p.id === 'p2').corner_radius === 3 &&
        !(((D().patch || {}).source_paths || {}).p2 || {}).corner_radius, 'Corner R of an inherited wall → Base');
  // material → Base (the project's)
  layer.material.contact_overlap = 0.5;
  await syncActiveDesign();
  check(B().material.contact_overlap === 0.5 && !('material' in (D().settings || {})), 'material edited in the Variant → Base');
  // LAYOUT STATE stays the Variant's: an inherited wall's geometry, a new wall, a removal, an opening
  const p5 = layer.source_paths.find(p => p.id === 'p5');
  const baseEnd = B().source_paths.find(p => p.id === 'p5').end.slice();
  p5.end = [70, 38.26]; _computePrimitivePoints(p5);
  addPrimitive('LinePath');
  const N = layer.source_paths[layer.source_paths.length - 1];
  N.envelope = { thickness: 4, align: 'center', print_reference: false };
  await syncActiveDesign();
  const sp = (D().patch || {}).source_paths || {};
  check(sp.p5 && JSON.stringify(sp.p5.end) === '[70,38.26]' && JSON.stringify(B().source_paths.find(p => p.id === 'p5').end) === JSON.stringify(baseEnd),
        'moving an inherited wall in the Variant is layout state: the Variant stores it, Base unchanged');
  check(sp[N.id] && sp[N.id].id === N.id && sp[N.id].envelope.thickness === 4 && !B().source_paths.some(p => p.id === N.id),
        'a wall ADDED by the Variant is the Variant\'s own (with its own properties)');
  N.envelope = { thickness: 5, align: 'center', print_reference: false };
  await syncActiveDesign();
  check(D().patch.source_paths[N.id].envelope.thickness === 5, 'editing the Variant\'s own wall stays in the Variant (it owns it)');
  deletePath('p5');
  await syncActiveDesign();
  check(D().patch.source_paths.p5 === null && B().source_paths.some(p => p.id === 'p5'), 'removing an inherited wall in the Variant is layout state (Base keeps it)');
  // Base shows the written-through values
  await editDesign('base');
  check(layer.cap_style === 'full_round' && layer.source_paths.find(p => p.id === 'p1').envelope.thickness === 8 && layer.material.contact_overlap === 0.5,
        'Base now holds the edits made from the Variant');
  await editDesign('d1');
  check(layer.cap_style === 'full_round' && layer.source_paths.find(p => p.id === 'p1').envelope.thickness === 8 && layer.junction_style === 'round',
        'reopening the Variant: inherited values from Base; its old divergence still rendered');
}
eval(src + '\n;(' + run.toString() + ')().then(() => { console.log("UI OWNERSHIP SMOKE PASSED"); process.exit(0); })' +
     '.catch(e => { console.log(String(e && e.stack || e)); process.exit(1); });');
