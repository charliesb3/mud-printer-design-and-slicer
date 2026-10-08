// UI smoke test for the Adaptive Truss wall infill: loads static/app.js
// against a permissive DOM/canvas stub and drives the Wall Infill panel:
// pattern choice, truss controls, Regenerate (solution seed), undo / redo.
// Run via tests/test_adaptive_truss.py (or: node tests/js/ui_truss_smoke.js).
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
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  // the server's pattern list (/api/infill_patterns): truss has its own parameters
  const SP = [{ name: 'spacing', label: 'Target Spacing', default: 20, min: 4 }];
  infillPatterns = {
    zigzag: { kind: 'wall', parameters: SP }, wave: { kind: 'wall', parameters: SP },
    truss: { kind: 'wall', parameters: [
      { name: 'brace_angle', label: 'Brace Angle', default: 45, min: 15, max: 75, unit: '°' },
      { name: 'bond', label: 'Bond Length', default: 3, min: 0 },
      { name: 'max_span', label: 'Max Unsupported Span', default: 40, min: 4 },
      { name: 'turn_radius', label: 'Min Turn Radius', default: 1.5, min: 0 }] } };
  addPrimitive('RectanglePath');
  const R = layer.source_paths[0];
  R.wall = { thickness: 10, align: 'auto' };
  selectedId = R.id;
  addInfill();
  const f = layer.infills[0];
  check(f.kind === 'wall' && f.pattern === 'zigzag', 'new wall infill defaults to Zigzag (no migration)');
  const list = document.getElementById('infill-list');
  const panel = () => { list.children.length = 0; updateInfillList(); return list.children[0].children[1]; };
  const row = (p, label) => p.children.find(r => r.children && r.children[0] && r.children[0].textContent === label);
  let p = panel();
  const sel = row(p, 'Pattern').children[1];
  check(sel.children.some(o => o.value === 'truss' && o.textContent === 'Adaptive Truss'), 'Pattern offers "Adaptive Truss"');
  sel.value = 'truss'; sel.onchange();
  check(f.pattern === 'truss' && f.params.brace_angle === 45 && f.params.bond === 3 &&
        f.params.max_span === 40 && f.params.turn_radius === 1.5 && f.params.seed === 0,
        'Adaptive Truss parameters get their defaults; seed 0');
  p = panel();
  check(row(p, 'Brace Angle') && row(p, 'Bond Length') && row(p, 'Max Unsupported Span') && row(p, 'Min Turn Radius'),
        'truss controls shown');
  check(!row(p, 'Target Spacing'), 'Target Spacing is not a truss control');
  const regen = p.children.find(r => (r.className || '').includes('truss-regenerate'));
  check(regen && regen.children[0].textContent === 'Solution 1', 'Regenerate row shows Solution 1');
  regen.children[1].onclick();
  check(f.params.seed === 1 && buildPayload().infills[0].params.seed === 1, 'Regenerate → seed 1 in the payload');
  p = panel();
  check(p.children.find(r => (r.className || '').includes('truss-regenerate')).children[0].textContent === 'Solution 2',
        'label follows the seed');
  check(undo() && layer.infills[0].params.seed === 0, 'undo restores the previous solution');
  check(redo() && layer.infills[0].params.seed === 1, 'redo re-applies the regenerated solution');
  // switching back keeps the zigzag's own control
  const f2 = layer.infills[0];
  p = panel();
  const sel2 = row(p, 'Pattern').children[1];
  sel2.value = 'zigzag'; sel2.onchange();
  check(f2.pattern === 'zigzag' && f2.params.spacing === 20, 'back to Zigzag: Target Spacing kept');
  console.log('UI TRUSS SMOKE PASSED');
})();
`);
