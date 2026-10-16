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
  // (2026-10-15: Wall Groups REMOVED — every path is a WALL owning its thickness)
  const list = document.getElementById('path-list');
  const pathPanel = document.getElementById('path-props');
  const geomBox = document.getElementById('walls-geom');
  const panels = () => { for (const el of [list, pathPanel, geomBox]) el.children.length = 0; updatePropPanel(); updatePathList(); };
  const rowOf = id => all(list).find(c => c.className === 'path-wall-row' && c.dataset && c.dataset.wall === id);
  const item = id => all(list).find(c => /^path-item/.test(c.className || '') && c.dataset && c.dataset.path === id);
  selectedId = A.id;
  panels();
  // the compact control: [label | − value + | unit] — value = THIN or inches
  const ctlOf = id => rowOf(id).children[1];
  const valOf = id => ctlOf(id).children[1];
  const unitOf = id => rowOf(id).children[2].textContent;
  check(layer.source_paths.every(p => rowOf(p.id) && rowOf(p.id).children.length === 3 && rowOf(p.id).children[0].textContent === 'Thickness' &&
                                     ctlOf(p.id).className === 'thick-ctl compact' && ctlOf(p.id).children.length === 3 &&
                                     ctlOf(p.id).children[0].textContent === '−' && ctlOf(p.id).children[2].textContent === '+' &&
                                     valOf(p.id).value === 'THIN' && /is-thin/.test(valOf(p.id).className)),
        'Paths list: every collapsed path row = label | − THIN + | width (separate elements, nothing shares a box)');
  check(unitOf(Ln.id) === '5.25 in' && unitOf(A.id) === '3 in',
        'a THIN wall shows its PHYSICAL width (open: out and back, 2W − R; closed: one bead) — not a free thickness');
  check(ctlOf(Ln.id).children[0].disabled === true, '− is disabled at THIN (nothing thinner)');
  check(!!row(pathPanel, 'Thickness') && /THIN wall/.test(text(pathPanel)),
        "path Properties: the wall's own thickness (thin explained)");
  historyCheckpoint();
  const depth = _hist.undo.length;
  const fA = valOf(A.id); fA.value = '12'; fA.onchange();
  check(A.envelope.thickness === 12 && A.envelope.align === 'center' && _effectiveWall(A).wall.thickness === 12 && !A.wall,
        "thickness typed on the collapsed row: the wall's own envelope, Centered by default");
  check(_hist.undo.length === depth + 1, 'one undo step');
  check(!(layer.infills || []).some(f => f.owner) && !('wall_groups' in layer), 'no construction records, no Wall Groups in the document');
  panels();
  check(!row(pathPanel, 'Construction') && !row(pathPanel, 'Web') && !row(pathPanel, 'Pitch') && !row(pathPanel, 'Number of Walls'),
        'no construction-type or lattice controls in the Designer');
  let al = row(pathPanel, 'Wall Alignment').children[1]; al.value = 'Inside'; al.onchange();
  check(A.envelope.align === 'inside', 'Wall Alignment on the wall');
  panels();
  al = row(pathPanel, 'Wall Alignment').children[1]; al.value = 'Centered'; al.onchange();
  panels();
  check(A.envelope.align === 'center' && !!row(pathPanel, 'Print reference line'), 'Centered offers the print reference line');
  // MULTI-SELECTION: several walls edited together (temporary — not a group)
  multiSel.clear(); selectedId = A.id; panels();
  item(B.id).onclick({ metaKey: true });
  item(Ln.id).onclick({ shiftKey: true });
  let bar = all(list).find(c => c.className === 'batch-bar');
  check(multiSel.size === 3 && bar && /3 walls selected/.test(text(bar)), 'Cmd / Shift-click: three walls selected — the batch bar');
  const bt = all(bar).find(c => c.id === 'batch-thickness'); bt.value = '9'; bt.onchange();
  check([A, B, Ln].every(p => p.envelope && p.envelope.thickness === 9), 'one thickness for every selected wall');
  bar = all(list).find(c => c.className === 'batch-bar');
  const ba = all(bar).find(c => c.id === 'batch-align'); ba.value = 'outside'; ba.onchange();
  check(A.envelope.align === 'outside' && B.envelope.align === 'outside' && Ln.envelope.align === 'right',
        'one alignment for all (closed walls: Outside, open: Right)');
  selectedId = B.id; panels();
  const pf = row(pathPanel, 'Thickness').children[1].children[1]; pf.value = '7'; pf.onchange();
  check([A, B, Ln].every(p => p.envelope.thickness === 7), 'a thickness typed in path Properties applies to every selected wall');
  item(B.id).onclick({});
  check(multiSel.size === 0 && selectedId === B.id, 'a plain click ends the multi-selection');
  setWallProps([B.id], { thickness: 0 });
  check(!B.envelope && A.envelope.thickness === 7, 'one wall made thin again — the others keep their thickness');
  // a wall's OWN end cap (open walls)
  selectedId = Ln.id; panels();
  let cap = row(pathPanel, 'End caps').children[1]; cap.value = 'Full Round'; cap.onchange();
  check(Ln.cap && Ln.cap.style === 'full_round', "a wall's own end-cap shape");
  panels();
  cap = row(pathPanel, 'End caps').children[1]; cap.value = 'Project default'; cap.onchange();
  check(!Ln.cap, '… or the project default');
  // JUNCTIONS, ENDS & CORNERS: the architectural shape controls in the Designer,
  // each written to its OWN owner (project default / the wall / the junction)
  panels();
  const gid = id => all(geomBox).find(c => c.id === 'walls-' + id);
  check(!!gid('junction-style') && !!gid('cap-style') && !!row(geomBox, 'Treatment') && !!row(geomBox, 'Default') &&
        ['Junctions', 'End caps', 'Corners'].every(h => all(geomBox).some(c => c.className === 'section-title' && c.textContent === h)),
        'Designer sidebar: Junctions, Ends & Corners — Junctions / End caps / Corners');
  const jsel = gid('junction-style'); jsel.value = 'round'; jsel.onchange();
  check(layer.junction_style === 'round' && !!gid('junction-radius'), 'junction treatment Rounded → its radius');
  gid('junction-radius').value = '3.5'; gid('junction-radius').onchange();
  check(layer.junction_radius === 3.5, 'junction radius (the project default)');
  gid('cap-style').value = 'rounded_corners'; gid('cap-style').onchange();
  gid('cap-radius').value = '2'; gid('cap-radius').onchange();
  check(layer.cap_style === 'rounded_corners' && layer.cap_corner_radius === 2, 'default end cap + End R (project)');
  // a wall's OWN cap (open thick walls) and Corner R (walls with corners) — per wall, not global
  check(!!gid('cap-' + Ln.id) && !gid('cap-' + A.id) && !!gid('corner-' + A.id) && !!gid('corner-' + B.id) && !gid('corner-' + Ln.id),
        'end caps listed for open walls, corners for walls with corners');
  gid('cap-' + Ln.id).value = 'full_round'; gid('cap-' + Ln.id).onchange();
  check(Ln.cap && Ln.cap.style === 'full_round' && layer.cap_style === 'rounded_corners', "one wall's own cap — the default unchanged");
  gid('cap-' + Ln.id).value = 'default'; gid('cap-' + Ln.id).onchange();
  check(!Ln.cap, '… back to the project default');
  const pts0 = JSON.stringify(A.points);
  gid('corner-' + A.id).value = 'round'; gid('corner-' + A.id).onchange();
  check(A.corner_radius === 2 && !B.corner_radius && JSON.stringify(A.points) !== pts0 && !!gid('corner-r-' + A.id),
        "Corner treatment Rounded: that wall's OWN Corner R (2 in), its corners re-sampled; other walls unchanged");
  gid('corner-r-' + A.id).value = '4'; gid('corner-r-' + A.id).onchange();
  multiSel.clear(); selectedId = A.id; panels();
  check(A.corner_radius === 4 && +all(pathPanel).find(c => c.id === 'prop-corner-r').value === 4,
        'Corner R — the same value path Properties shows');
  gid('corner-' + A.id).value = 'sharp'; gid('corner-' + A.id).onchange();
  check(A.corner_radius === 0, 'Sharp = Corner R 0');
  setWallProps([Ln.id], { thickness: 0 }); panels();
  check(!gid('cap-' + Ln.id) && /U-turn/.test(text(geomBox)), 'a THIN open wall: U-turn ends, set by construction (no cap choice)');
  setWallGeometry({ junction_style: 'round', cap_style: 'flat', cap_corner_radius: 0 });
  // THIN → EXPLICIT THICKNESS (2026-10-15 correction)
  check(_stepThick(0, 5.25, 1) === 5.5 && _stepThick(0, 5, 1) === 5 && _stepThick(0, 3, 1) === 3 &&
        _stepThick(5.5, 5.25, -1) === 0 && _stepThick(6, 5.25, -1) === 5.5 && _stepThick(5.5, 5.25, 1) === 6 &&
        _stepThick(7.3, 5.25, 1) === 7.5 && _stepThick(7.3, 5.25, -1) === 7 && _stepThick(4, 5.25, 1) === 5.5 &&
        _stepThick(0, 5.25, -1) === 0,
        'steps: THIN + → first 0.5 in step AT OR ABOVE the THIN width; − from that minimum → THIN; else the 0.5 in grid');
  check(_clampThick(2, 5.25) === 5.5 && _clampThick(5.3, 5.25) === 5.3 && _clampThick(0, 5.25) === 0 && _clampThick(-1, 5.25) === 0,
        'a typed thickness below the THIN width becomes the minimum explicit one; 0 = THIN');
  panels();
  ctlOf(Ln.id).children[2].onclick();
  check(Ln.envelope && Ln.envelope.thickness === 5.5, 'Paths list +: THIN (5.25 in wide) → 5.5 in — never an impossible 0.5 in');
  panels();
  check(valOf(Ln.id).value === '5.5' && unitOf(Ln.id) === 'in' && !/is-thin/.test(valOf(Ln.id).className), 'shown as 5.5 in');
  ctlOf(Ln.id).children[2].onclick(); panels();
  check(Ln.envelope.thickness === 6, '+ again: 6 in');
  ctlOf(Ln.id).children[0].onclick(); panels();
  ctlOf(Ln.id).children[0].onclick(); panels();
  check(!Ln.envelope && valOf(Ln.id).value === 'THIN' && unitOf(Ln.id) === '5.25 in', '− down to the minimum, then − → THIN');
  setWallProps([A.id], { thickness: 0 }); panels();
  ctlOf(A.id).children[2].onclick();
  check(A.envelope.thickness === 3, 'a closed THIN wall (one bead, 3 in): + → 3 in');
  setWallProps([A.id], { thickness: 7 }); panels();
  valOf(Ln.id).value = '2'; valOf(Ln.id).onchange();
  check(Ln.envelope.thickness === 5.5, 'typed 2 in on a 5.25 in THIN wall → 5.5 in (never below the THIN width)');
  setWallProps([Ln.id], { thickness: 1 });
  check(Ln.envelope.thickness === 5.5, 'the same floor for every editing surface (setWallProps)');
  setWallProps([Ln.id], { thickness: 0 }); panels();
  // the THIN width follows the material (return-lane overlap, bead width)
  document.getElementById('return-overlap').value = '1.25'; onMaterialChange('return_overlap');
  check(unitOf(Ln.id) === '4.75 in', 'return-lane overlap 1.25: THIN width 2 × 3 − 1.25 = 4.75 in, shown at once');
  ctlOf(Ln.id).children[2].onclick(); panels();
  check(Ln.envelope.thickness === 5, '… and + now gives 5 in');
  setWallProps([Ln.id], { thickness: 0 });
  setProjectBeadWidth(3.5); panels();
  check(unitOf(Ln.id) === '5.75 in', 'bead width 3.5: 2 × 3.5 − 1.25 = 5.75 in');
  setProjectBeadWidth(3); document.getElementById('return-overlap').value = '0.75'; onMaterialChange('return_overlap'); panels();
  check(unitOf(Ln.id) === '5.25 in', 'material restored: 5.25 in');
  setWallProps([B.id, Ln.id], { thickness: 7 });
  // no ROUTE (and no lattice) is requested by ordinary Designer editing: every
  // resolve is an envelope request
  {
    const urls = [], bodies = [];
    const f0 = global.fetch;
    global.fetch = async (url, opts) => { urls.push(String(url)); bodies.push(opts && opts.body); return { json: async () => ({ superseded: true }) }; };
    for (const t of [11, 12]) { A.envelope.thickness = t; scheduleRefresh({ supersede: true }); await new Promise(r => setTimeout(r, 200)); }
    global.fetch = f0;
    check(urls.length > 0 && !urls.some(u => u.includes('/api/route')) && bodies.every(b => JSON.parse(b).envelope === true),
          'ordinary Designer editing requests envelopes only — never a route or lattice');
  }
  // payload: the ARCHITECTURE only
  const pl = buildPayload();
  check(pl.source_paths[0].envelope.thickness === 12 && !('wall_groups' in pl) &&
        pl.envelope === true && !('wall_systems' in pl) && !('project_wall_systems' in pl),
        "payload: the walls' envelopes (no Wall Groups, no Wall Systems, no construction)");
  check(undo() && redo(), 'undo / redo');
  Promise.resolve().then(() => {
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
