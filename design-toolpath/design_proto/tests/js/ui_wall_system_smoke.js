// UI smoke test for the WALL SYSTEM panel (its own Designer sidebar): explicit
// user-authored groups of Design paths owning thickness / alignment /
// construction / parameters / the Skin + Web web; at most one system per
// path; members need not touch; path Properties membership row; webs
// materialised as owned infill records; legacy migration; undo / payload.
// Run via tests/test_wall_systems.py (or: node tests/js/ui_wall_system_smoke.js).
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
  const all = (el, out = []) => { for (const c of el.children || []) { out.push(c); all(c, out); } return out; };
  const btn = (p, re) => all(p).find(c => typeof c.textContent === 'string' && re.test(c.textContent) && c.onclick);
  const row = (p, label) => all(p).find(r => r.children && r.children[0] && r.children[0].textContent === label);
  const text = p => all(p).map(c => (typeof c.textContent === 'string' ? c.textContent : '')).join(' | ');
  addPrimitive('RectanglePath');
  addPrimitive('RectanglePath');
  addPrimitive('LinePath');
  const [A, B, Ln] = layer.source_paths;
  const list = document.getElementById('wallsys-list');
  const sysPanel = document.getElementById('wallsys-props');
  const pathPanel = document.getElementById('path-props');
  const panels = () => { for (const el of [list, sysPanel, pathPanel]) el.children.length = 0; updatePropPanel(); };
  selectedId = A.id;
  panels();
  check(/No Wall System \\(single bead\\)/.test(text(list)), 'Wall System panel: every path starts as a single bead');
  check(/Wall System/.test(text(pathPanel)) && row(pathPanel, 'Wall System').children[1].children[0].value === 'none',
        'path Properties: Wall System = None (single bead)');
  historyCheckpoint();
  btn(list, /New Wall System with/).onclick();
  const S = layer.wall_systems[0];
  check(S && S.type === 'skin_web' && S.thickness === 10 && S.members.join() === A.id && S.web.pattern === 'zigzag',
        'New Wall System: Skin + Web, 10 in, zigzag web, the selected path a member');
  check(layer.infills.some(f => f.owner === S.id && f.path_id === A.id && f.pattern === 'zigzag'),
        'the web is materialised as an OWNED infill record');
  panels();
  check(row(sysPanel, 'Construction').children[1].children.map(o => o.textContent).join('|') ===
        'Single / Out-and-Back Wall|Hollow / Skins Only|Skin + Web|Parallel Walls|Interleaved Waves|Linked Waves|Chained Loop',
        'Construction offers the seven constructions');
  const kids = sysPanel.children.map(c => c.textContent);
  check(kids.indexOf('Members') >= 0 && kids.indexOf('Members') < kids.indexOf('Construction'),
        'Members come first in the Wall System panel');
  check(all(sysPanel).filter(c => c.className === 'prop-row wallsys-member').map(r => r.children[0].textContent).join() === A.label,
        "the member list shows ONLY this system paths");
  check(!all(sysPanel).some(c => c.children && c.children[1] && c.children[1].type === 'checkbox' &&
                                 c.children[0].textContent === B.label), 'no all-path checkbox list');
  check(!!row(sysPanel, 'Name') && !!row(sysPanel, 'Wall Thickness') && !!row(sysPanel, 'Wall Alignment'),
        'the system owns Name, Wall Thickness and Wall Alignment');
  check(row(sysPanel, 'Web').children[1].children.map(o => o.value).join() === 'zigzag,wave',
        'Skin + Web: Web = Zigzag / Wave (+ Adaptive Truss from the backend pattern list) — no "None"');
  row(sysPanel, 'Name').children[1].value = 'Outer'; row(sysPanel, 'Name').children[1].onchange();
  check(S.name === 'Outer', 'renamed');
  panels();
  row(sysPanel, 'Wall Thickness').children[1].value = '12'; row(sysPanel, 'Wall Thickness').children[1].onchange();
  check(S.thickness === 12 && _effectiveWall(A).wall.thickness === 12 && !A.wall, 'Wall Thickness lives on the system');
  const ts = row(sysPanel, 'Target Spacing');
  const before = sysPanel.children.length, firstRow = sysPanel.children[0];
  ts.children[1].value = '16'; ts.children[1].onchange();
  check(S.web.params.spacing === 16 && layer.infills.find(f => f.owner === S.id).params.spacing === 16,
        'web parameter edited on the system, synced into its owned record');
  check(sysPanel.children.length === before && sysPanel.children[0] === firstRow,
        'a parameter edit does not rebuild the panel (input keeps focus)');
  infillPatterns = { zigzag: { kind: 'wall', parameters: [{ name: 'spacing', label: 'Target Spacing', default: 20 }] },
                     wave: { kind: 'wall', parameters: [{ name: 'spacing', label: 'Target Spacing', default: 20 }] },
                     truss: { kind: 'wall', parameters: [{ name: 'angle', label: 'Brace Angle', default: 45 }] } };
  panels();
  const web = row(sysPanel, 'Web').children[1];
  web.value = 'truss'; web.onchange();
  panels();
  check(S.web.pattern === 'truss' && S.web.params.seed === 0 && !!row(sysPanel, 'Brace Angle'),
        'Adaptive Truss web with its parameters');
  btn(sysPanel, /^Regenerate$/).onclick();
  check(S.web.params.seed === 1 && layer.infills.find(f => f.owner === S.id).params.seed === 1,
        'Regenerate: next truss solution, synced');
  panels();
  const c1 = row(sysPanel, 'Construction').children[1];
  c1.value = 'hollow'; c1.onchange();
  panels();
  check(S.type === 'hollow' && /skins only/.test(text(sysPanel)) && !row(sysPanel, 'Web') &&
        layer.infills.some(f => f.owner === S.id), 'Hollow / Skins Only: no web (the owned record kept for its lineage)');
  const c2 = row(sysPanel, 'Construction').children[1];
  c2.value = 'skin_web'; c2.onchange();
  panels();
  // members: explicit, need not touch; at most one system per path
  const add = row(sysPanel, 'Add path…').children[1];
  check(add.children.map(o => o.value).join() === ['__add', B.id, Ln.id].join(), 'Add path… lists the other paths');
  add.value = B.id; add.onchange();
  check(S.members.join() === [A.id, B.id].join(), 'Add path… adds it (no contact needed)');
  panels();
  row(sysPanel, 'Web').children[1].value = 'wave'; row(sysPanel, 'Web').children[1].onchange();
  check(layer.infills.filter(f => f.owner === S.id && f.pattern === 'wave').length === 2,
        'one owned web record per connected group of members');
  btn(list, /^\\+ New Wall System$/).onclick();
  const S2 = layer.wall_systems[1];
  panels();
  const add2 = row(sysPanel, 'Add path…').children[1];
  check(/\\(from Outer\\)/.test(add2.children.find(o => o.value === A.id).textContent),
        'Add path… says when a path comes from another system');
  add2.value = A.id; add2.onchange();
  check(S2.members.join() === A.id && S.members.join() === B.id, 'moving a path between systems (one system per path)');
  panels();
  const xb = all(sysPanel).find(c => c.className === 'remove-btn');
  xb.onclick();
  check(S2.members.length === 0, 'the compact × removes a member');
  add2.value = A.id; add2.onchange();
  panels();
  const cons = row(sysPanel, 'Construction').children[1];
  cons.value = 'chain'; cons.onchange();
  panels();
  check(S2.type === 'chain' && !!row(sysPanel, 'Pitch') && !!row(sysPanel, 'Loop Depth') && !!row(sysPanel, 'Neck') &&
        !row(sysPanel, 'Web') && /Minimum effective thickness/.test(text(sysPanel)),
        'Chained Loop: its parameters + Minimum Effective Thickness, no web');
  const c3 = row(sysPanel, 'Construction').children[1];
  c3.value = 'parallel'; c3.onchange();
  panels();
  check(S2.type === 'parallel' && S2.params.walls === 4 && !!row(sysPanel, 'Number of Walls'), 'Parallel Walls: Number of Walls');
  const c4 = row(sysPanel, 'Construction').children[1];
  c4.value = 'single'; c4.onchange();
  panels();
  check(S2.type === 'single' && !row(sysPanel, 'Wall Thickness') && /outbound \\+ return lanes/.test(text(sysPanel)) &&
        _effectiveWall(layer.source_paths.find(p => p.id === A.id)).wall === null,
        'Single / Out-and-Back: no Wall Thickness needed (the bead / return-lane rules)');
  const c5 = row(sysPanel, 'Construction').children[1];
  c5.value = 'chain'; c5.onchange();
  // path Properties: membership row
  selectedId = Ln.id;
  panels();
  const wr = row(pathPanel, 'Wall System').children[1];
  wr.value = S.id; wr.onchange();
  check(_wsOf(Ln.id) === S, 'path Properties: choose its Wall System');
  updatePathList();
  check(all(document.getElementById('path-list')).some(c => c.className === 'path-type wallsys-badge' &&
                                                           /Outer/.test(c.textContent)), 'path list badge names the system');
  // payload / undo
  const pl = buildPayload();
  check(pl.wall_systems.length === 2 && pl.wall_systems[0].thickness === 12 && pl.wall_systems[0].name === 'Outer' &&
        pl.wall_systems[0].web.pattern === 'wave', 'payload carries the Wall Systems');
  check(undo() && redo(), 'undo / redo');
  // the Infill list no longer shows wall webs
  const il = document.getElementById('infill-list'); il.children.length = 0; updateInfillList();
  check(il.children.length === 0, 'owned webs are not in the Infill list');
  // deleting a system releases its paths and drops its webs
  wallSystemPick = layer.wall_systems[0].id; panels();
  btn(sysPanel, /^Delete /).onclick();
  check(layer.wall_systems.length === 1 && !layer.infills.some(f => f.owner === S.id), 'Delete: paths released, webs dropped');
  // LEGACY MIGRATION (backend groups the effective walls; not an undo step)
  layer.wall_systems = []; layer.infills = [];
  const A0 = layer.source_paths[0];                 // (undo / redo rebuilt the layer objects)
  A0.wall = { thickness: 8, align: 'inside' };
  layer.infills.push({ id: 'old-infill', path_id: A0.id, kind: 'wall', pattern: 'zigzag', params: { spacing: 20 } });
  historyCheckpoint();
  const undoDepth = _hist.undo.length;
  global.fetch = async (url) => ({ json: async () => (String(url).includes('migrate') ? {
    migrated: true, owners: { 'old-infill': 'WS1' },
    wall_systems: [{ id: 'WS1', name: 'Wall System 1', type: 'skin_web', thickness: 8, align: 'inside',
                     print_reference: false, params: {}, members: [A0.id],
                     web: { pattern: 'zigzag', params: { spacing: 20 }, variation_index: 0 } }] } : []) });
  _maybeMigrateWallSystems().then(() => {
    check(!A0.wall && layer.wall_systems[0].members.join() === A0.id && layer.infills[0].owner === 'WS1' &&
          layer.infills[0].id === 'old-infill', 'migration: path wall → Wall System, wall infill → owned web (same id)');
    check(_hist.undo.length === undoDepth, 'migration is not an undo step');
    // REQUEST COALESCING: edits during a resolve collapse into one follow-up
    let calls = 0, release;
    const slow = () => { calls++; return new Promise(r => { release = r; }); };
    _coalesced(slow); _coalesced(slow); _coalesced(slow); _coalesced(slow);
    check(calls === 1, 'one resolve in flight at a time');
    release();
    setTimeout(() => {
      check(calls === 2, 'the queued edits became ONE follow-up resolve');
      release();
      console.log('UI WALL SYSTEM SMOKE PASSED');
    }, 0);
  });
})();
`);
