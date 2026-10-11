// UI smoke test for the Designer's WALL GROUPS panel (2026-10-11; formerly the
// Wall System panel): explicit user-authored groups of paths owning Wall
// Thickness / Wall Alignment / the print reference — the ARCHITECTURE; no
// construction, web or parameter controls (those are the project's Wall
// Systems, edited in the Wall Systems workspace: layer_assembly/tests/js/
// ui_wallsystems_smoke.js); at most one group per path; members need not
// touch; path Properties identity row; the Designer resolves ENVELOPES only
// (payload `envelope`, no route); an older document's Wall Systems are
// split into groups + project Wall Systems; legacy migration; undo / payload.
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
global.window = { addEventListener() {}, devicePixelRatio: 1 };     // (the Designer's default: envelopes)
global.requestAnimationFrame = () => 0; global.cancelAnimationFrame = () => {};
global.fetch = async () => ({ json: async () => ([]) });
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const all = (el, out = []) => { for (const c of el.children || []) { out.push(c); all(c, out); } return out; };
  const btn = (p, re) => all(p).find(c => typeof c.textContent === 'string' && re.test(c.textContent) && c.onclick);
  const row = (p, label) => all(p).find(r => r.children && r.children[0] && r.children[0].textContent === label);
  const text = p => all(p).map(c => (typeof c.textContent === 'string' ? c.textContent : '')).join(' | ');
  check(_designerEnvelope() && showToolpath === false, 'the Designer resolves envelopes only (no toolpath)');
  // DISPLAY (2026-10-11 correction): the SHAPES as single lines by default; Wall
  // Envelope optional — rings + faces of thick walls, never single-bead lanes
  _syncDesignerMode();                                  // (what init() does on the page)
  check(designerDisplay().mode === 'envelope' && document.getElementById('btn-envelope').textContent === 'Wall Envelope ON',
        'default display: the WALL ENVELOPE (each wall at its architectural thickness)');
  toggleEnvelope(false);
  envelopeLines = [{ role: 'ring', pts: [[0, 0], [10, 0], [10, 10]], closed: true, src: ['x', 'x', 'x'] },
                   { role: 'bead', cls: 'face', single: false, pts: [[0, 0], [1, 1]], src: ['y', 'y'] },
                   { role: 'bead', cls: 'face', single: true, pts: [[0, 2], [1, 3]], src: ['z', 'z'] },
                   { role: 'bead', cls: 'wire', single: true, pts: [[0, 4], [1, 5]], src: ['w', 'w'] }];
  let D0 = designerDisplay();
  check(D0.rings === 0 && D0.faces === 0 && D0.printable === 0 && !D0.toolpath,
        'shapes display: no envelope, no printable lines, no toolpath');
  toggleEnvelope();
  D0 = designerDisplay();
  check(D0.mode === 'envelope' && D0.rings === 1 && D0.faces === 1 && document.getElementById('btn-envelope').textContent === 'Wall Envelope ON',
        'Wall Envelope: inner / outer boundaries (rings, faces of thick walls) — single-bead lanes left out');
  toggleEnvelope(false);
  check(designerDisplay().mode === 'shapes', 'Wall Envelope off: every shape as one line');
  toggleEnvelope(true);
  envelopeLines = null;
  addPrimitive('RectanglePath');
  addPrimitive('RectanglePath');
  addPrimitive('LinePath');
  const [A, B, Ln] = layer.source_paths;
  const list = document.getElementById('wallgrp-list');
  const gPanel = document.getElementById('wallgrp-props');
  const pathPanel = document.getElementById('path-props');
  const panels = () => { for (const el of [list, gPanel, pathPanel]) el.children.length = 0; updatePropPanel(); };
  selectedId = A.id;
  panels();
  check(/No Wall Group \\(single bead/.test(text(list)), 'Wall Groups panel: every path starts as a single bead');
  check(/None \\(single bead\\)/.test(text(row(pathPanel, 'Wall Group'))), 'path Properties: Wall Group = None (single bead), identity only');
  historyCheckpoint();
  btn(list, /New Wall Group with/).onclick();
  const G = layer.wall_groups[0];
  check(G && G.thickness === 10 && G.align === 'auto' && G.members.join() === A.id && !('type' in G) && !('web' in G),
        'New Wall Group: 10 in, default alignment, the selected path a member — no construction in it');
  check(!(layer.infills || []).some(f => f.owner), 'no construction records in the document (no owned webs)');
  panels();
  check(!row(gPanel, 'Construction') && !row(gPanel, 'Web') && !row(gPanel, 'Pitch') && !row(gPanel, 'Number of Walls'),
        'no construction-type or lattice controls in the Designer');
  const kids = gPanel.children.map(c => c.textContent);
  check(kids.indexOf('Members') >= 0 && kids.indexOf('Members') < kids.indexOf('Envelope'), 'Members come first');
  check(all(gPanel).filter(c => c.className === 'prop-row wallgrp-member').map(r => r.children[0].textContent).join() === A.label,
        'the member list shows ONLY the paths of this group (compact)');
  check(!!row(gPanel, 'Name') && !!row(gPanel, 'Wall Thickness') && !!row(gPanel, 'Wall Alignment'),
        'the group owns Name, Wall Thickness and Wall Alignment');
  row(gPanel, 'Name').children[1].value = 'Outer'; row(gPanel, 'Name').children[1].onchange();
  check(G.name === 'Outer', 'renamed');
  panels();
  row(gPanel, 'Wall Thickness').children[1].value = '12'; row(gPanel, 'Wall Thickness').children[1].onchange();
  check(G.thickness === 12 && _effectiveWall(A).wall.thickness === 12 && !A.wall, 'Wall Thickness lives on the group');
  panels();
  const al = row(gPanel, 'Wall Alignment').children[1];
  al.value = 'Centered'; al.onchange();
  panels();
  check(G.align === 'center' && !!row(gPanel, 'Print reference line'), 'Wall Alignment; Centered offers the print reference line');
  // members: explicit, need not touch; at most one group per path
  const add = row(gPanel, 'Add path…').children[1];
  check(add.children.map(o => o.value).join() === ['__add', B.id, Ln.id].join(), 'Add path… lists the other paths');
  add.value = B.id; add.onchange();
  check(G.members.join() === [A.id, B.id].join(), 'Add path… adds it (no contact needed)');
  btn(list, /^\\+ New Wall Group$/).onclick();
  const G2 = layer.wall_groups[1];
  panels();
  const add2 = row(gPanel, 'Add path…').children[1];
  check(/\\(from Outer\\)/.test(add2.children.find(o => o.value === A.id).textContent),
        'Add path… says when a path comes from another group');
  add2.value = A.id; add2.onchange();
  check(G2.members.join() === A.id && G.members.join() === B.id, 'moving a path between groups (one group per path)');
  panels();
  all(gPanel).find(c => c.className === 'remove-btn').onclick();
  check(G2.members.length === 0, 'the compact × removes a member');
  add2.value = A.id; add2.onchange();
  // path Properties + list badge
  selectedId = B.id;
  panels();
  check(text(row(pathPanel, 'Wall Group')).includes('Outer · 12 in'), 'path Properties name the group and its thickness');
  updatePathList();
  check(all(document.getElementById('path-list')).some(c => c.className === 'path-type wallgrp-badge' &&
                                                           /Outer/.test(c.textContent)), 'path list badge names the group');
  // no ROUTE (and no lattice) is requested by ordinary Designer editing: every
  // resolve is an envelope request
  {
    const urls = [], bodies = [];
    const f0 = global.fetch;
    global.fetch = async (url, opts) => { urls.push(String(url)); bodies.push(opts && opts.body); return { json: async () => ({ superseded: true }) }; };
    for (const t of [11, 12]) { G.thickness = t; scheduleRefresh({ supersede: true }); await new Promise(r => setTimeout(r, 200)); }
    global.fetch = f0;
    check(urls.length > 0 && !urls.some(u => u.includes('/api/route')) && bodies.every(b => JSON.parse(b).envelope === true),
          'ordinary Designer editing requests envelopes only — never a route or lattice');
    G.thickness = 12;
  }
  // payload: the ARCHITECTURE only
  const pl = buildPayload();
  check(pl.wall_groups.length === 2 && pl.wall_groups[0].thickness === 12 && pl.wall_groups[0].name === 'Outer' &&
        pl.envelope === true && !('wall_systems' in pl) && !('project_wall_systems' in pl),
        'payload: the Wall Groups + envelope (no Wall Systems, no construction)');
  check(undo() && redo(), 'undo / redo');
  // deleting a group releases its paths
  wallGroupPick = layer.wall_groups[1].id; panels();
  btn(gPanel, /^Delete /).onclick();
  check(layer.wall_groups.length === 1 && !_wgOf(layer.source_paths[0].id), 'Delete: paths released');
  // AN OLDER DOCUMENT's Wall Systems: split into groups + project Wall Systems (ids kept)
  layer.wall_groups = [];
  const A0 = layer.source_paths[0];
  layer.wall_systems = [{ id: 'WS7', name: 'Rooms', type: 'interleaved', thickness: 9, align: 'inside', print_reference: false,
                          params: { paths: 4 }, members: [A0.id], web: {} }];
  project.wall_systems = [];
  historyCheckpoint();
  const depth0 = _hist.undo.length;
  scheduleRefresh();
  check(!layer.wall_systems && layer.wall_groups.length === 1 && layer.wall_groups[0].id === 'WS7' &&
        layer.wall_groups[0].thickness === 9 && layer.wall_groups[0].align === 'inside' &&
        project.wall_systems.length === 1 && project.wall_systems[0].type === 'interleaved' &&
        project.wall_systems[0].groups.join() === 'WS7' && project.wall_systems[0].params.paths === 4,
        'an older document: its Wall System → a Wall Group (envelope) + a project Wall System (construction), ids kept');
  check(_hist.undo.length <= depth0 + 1, 'the split is no edit of its own');
  // LEGACY MIGRATION (path walls → backend Wall Systems → groups + project systems; not an undo step)
  layer.wall_groups = []; project.wall_systems = []; layer.infills = [];
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
    scheduleRefresh();
    check(!A0.wall && layer.wall_groups[0].members.join() === A0.id && layer.wall_groups[0].thickness === 8 &&
          layer.infills[0].owner === 'WS1' && layer.infills[0].id === 'old-infill' &&
          project.wall_systems.some(s => s.id === 'WS1' && s.type === 'skin_web' && s.web.pattern === 'zigzag'),
          'migration: path wall → Wall Group + project Wall System (skin + web), wall infill → owned web (same id)');
    check(_hist.undo.length <= undoDepth + 1, 'migration is not an edit of its own');
    // REQUEST COALESCING: edits during a resolve collapse into one follow-up
    let calls = 0, release;
    const slow = () => { calls++; return new Promise(r => { release = r; }); };
    _coalesced(slow); _coalesced(slow); _coalesced(slow); _coalesced(slow);
    check(calls === 1, 'one resolve in flight at a time');
    release();
    setTimeout(() => {
      check(calls === 2, 'the queued edits became ONE follow-up resolve');
      release();
      // LIVE numeric input: nothing while typing, ONE apply once typing pauses
      const box = document.createElement('div'), got = [];
      addPropRowNumTo(box, 'X', 1, v => got.push(v), 'in', { live: true });
      const inp = box.children[0].children[1];
      inp.value = '1'; inp.oninput(); inp.value = '14'; inp.oninput();
      check(got.length === 0, 'typing: nothing applied while still typing');
      setTimeout(() => {
        check(got.join() === '14', 'a typed value applies ONCE when typing pauses (no Enter / blur needed)');
        console.log('UI WALL GROUP SMOKE PASSED');
      }, LIVE_INPUT_MS + 60);
    }, 0);
  });
})();
`);
