// UI smoke test of the WALL SYSTEMS workspace (2026-10-11): the real
// static/app.js + assembly.js + wallsystems.js + simulation.js against the
// REAL backend, DOM stubbed. Run via layer_assembly/tests/test_wallsystems_ui.py.
// walls own thickness (Designer) vs Wall Systems (workspace); create / rename /
// assign (per-wall checklist) / delete; one construction per wall; one construction across
// thicknesses; edits generate NOTHING; undo / redo; explicit generation +
// status NOT GENERATED / GENERATING / CURRENT / OUTDATED; layer-by-layer
// inspection (Base view, ◀ ▶, number, Top / orbit, Envelope / Generated,
// support findings); invalidation by Wall System / Assembly / Designer edits;
// the Print Simulation consumes only current geometry; cancel; Save / Open.
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
src += '\n' + fs.readFileSync(path.join(STATIC, 'opening_shape.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'assembly.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'wallsystems.js'), 'utf8');
src += '\n' + fs.readFileSync(path.join(STATIC, 'simulation.js'), 'utf8');
eval(src + `
;(async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const el = id => document.getElementById(id);
  const flat = n => [n, ...(n.children || []).flatMap(flat)];
  const txt = id => flat(el(id)).map(n => n.textContent || '').join(' | ');
  const all = p => flat(p).slice(1);
  const row = (p, label) => all(p).find(r => r.children && r.children[0] && r.children[0].textContent === label);
  const reqs = u => calls.filter(c => c.url === u).length;
  const sleep = ms => new Promise(r => realSetTimeout(r, ms));
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  // ---- markup: the fourth workspace, between Assembly and the Print Simulation ----
  check(html.indexOf('ws-tab-assembly') < html.indexOf('ws-tab-wallsystems') &&
        html.indexOf('ws-tab-wallsystems') < html.indexOf('ws-tab-simulation') && html.includes('id="ws-wallsystems"') &&
        html.includes('src="/static/wallsystems.js"'), 'Designer → Assembly → Wall Systems → Print Simulation');
  check(['wsys-generate', 'wsys-status', 'wsys-list', 'wsys-props', 'wsys-canvas', 'wsys-base-btn', 'wsys-top-btn', 'wsys-orbit-btn',
         'wsys-show-env', 'wsys-show-gen', 'wsys-prev', 'wsys-next', 'wsys-layer-input', 'wsys-support', 'wsys-undo', 'wsys-redo']
          .every(id => html.includes('id="' + id + '"')) && />Generate Wall Systems</.test(html),
        'the workspace controls: a prominent Generate Wall Systems, the systems, the inspection view');
  check(html.includes('<div class="sidebar-head">Junctions, Ends &amp; Corners</div>') && !html.includes('Wall Groups</div>') &&
        !html.includes('id="wallsys-props"') && !html.includes('id="wsys-geom"') &&
        html.includes('id="wsys-arch-section"') && html.includes('Architecture · shared with Designer'),
        'the Designer sidebar: Junctions, Ends & Corners — no Wall Groups; Wall Systems: an Architecture section shared with the Designer (no Wall-System geometry panel)');
  // ---- Designer: ARCHITECTURE (each wall its own thickness; 2026-10-15) ----
  addPrimitive('RectanglePath');
  const R = layer.source_paths[0];
  addPrimitive('LinePath');
  const Ln = layer.source_paths[1];
  Ln.start = [140, 330]; Ln.end = [260, 330]; _computePrimitivePoints(Ln);
  addPrimitive('CirclePath');
  const Cc = layer.source_paths[2];
  Cc.cx = 420; Cc.cy = 200; _computePrimitivePoints(Cc);
  addPrimitive('LinePath');
  const U = layer.source_paths[3];                     // a THIN wall (no thickness yet)
  U.start = [400, 340]; U.end = [520, 340]; _computePrimitivePoints(U);
  layer.material.physical = true;
  setWallProps([R.id, Ln.id], { thickness: 10 });
  setWallProps([Cc.id], { thickness: 12 });
  historyCheckpoint();
  check(!layer.wall_systems && project.wall_systems.length === 0 && !('wall_groups' in layer),
        'walls own their thickness; Wall Systems (construction) are separate');
  asm.state.sections = [{ design_id: 'base', height: 6 }];
  setWorkspace('assembly');
  await asmResolve();
  check(asm.resolved.geometry === 'envelope' && !asm.error && asm.resolved.total_layers === 4, 'the Assembly resolves envelopes (4 layers)');
  // ---- the Wall Systems workspace ----
  setWorkspace('wallsystems');
  await wsysShow();
  check(wsysStatus() === 'not_generated' && /NOT GENERATED/.test(txt('wsys-status')), 'status: NOT GENERATED');
  check(wsys.layer === -1 && wsys._base && wsys._base.length > 0 && /Base design/.test(txt('wsys-layer-info')),
        'opens on the envelope of the Base design, from above');
  check(/No Wall System \\(prints Hollow/.test(txt('wsys-list')) && /no thickness \\(a single bead\\): [^|]*Line/.test(txt('wsys-list')),
        'walls without a Wall System print Hollow; a thin wall is a single bead (said)');
  check(_wsysCatalog().walls.length === 4 && !('groups' in _wsysCatalog()), 'EVERY wall is listed — no groups');
  const n0 = { gen: reqs('/api/wallsystems/generate'), res: reqs('/api/assembly/resolve'), eff: reqs('/api/effective_paths'),
               plan: reqs('/api/assembly/print_plan'), geo: reqs('/api/assembly/geometry') };
  const S1 = wsysNew('interleaved', { members: [R.id, Ln.id] });
  check(S1.type === 'interleaved' && S1.params.paths === 4 && S1.members.join() === [R.id, Ln.id].join() &&
        !('thickness' in S1) && !('groups' in S1) && !('designs' in S1),
        'a new Wall System assigned to WALLS (no thickness of its own, no groups, no design scope)');
  const panel = el('wsys-props');
  const wallRow = id => all(el('wsys-props')).find(c => c.dataset && c.dataset.wall === id);
  check(!!row(panel, 'Construction') && !!row(panel, 'Name') && !row(panel, 'Wall Alignment') && !row(panel, 'Add Wall Group…') &&
        /Wall Thickness: 10 in/.test(txt('wsys-props')) && [R, Ln, Cc, U].every(w => wallRow(w.id)) &&
        all(panel).filter(c => c.className === 'wsys-check').length === 4,
        'assignment = a CHECKLIST of every wall (with its thickness), then construction and parameters');
  check(row(panel, 'Construction').children[1].children.map(o => o.textContent).join('|') ===
        'Single / Out-and-Back Wall|Hollow / Skins Only|Skin + Web|Parallel Walls|Interleaved Waves|Linked Waves|Chained Loop',
        'the seven constructions');
  const S2 = wsysNew('chain', { members: [Ln.id] });
  check(wsysSystemOf(Ln.id) === S2 && wsysSystemOf(R.id) === S1 && !S1.members.includes(Ln.id),
        'a wall has ONE construction: assigning it elsewhere moves it');
  wsys.pick = S1.id; wsysRender();
  const cbC = wallRow(Cc.id).children[0];
  cbC.checked = true; cbC.onchange();
  check(_wsysSys(S1.id).members.includes(Cc.id) && /10 in, 12 in/.test(txt('wsys-props')),
        'one construction across walls of different thickness (ticked in the checklist)');
  const p6 = row(el('wsys-props'), 'Number of Paths').children[1];
  p6.value = '6'; p6.onchange();
  check(_wsysSys(S1.id).params.paths === 6, 'a construction parameter edited');
  // a THIN wall assigned directly; its thickness set here
  const S4 = wsysNew('parallel', { members: [U.id] });
  check(wsysSystemOf(U.id) === S4 && /Thin \\(single bead\\)/.test(txt('wsys-props')),
        'a thin wall is assigned directly; without a thickness it is said to stay a single bead');
  const tU = wallRow(U.id).children[2].children[1];       // (the shared thickness control: − value +)
  check(tU.value === 'THIN' && wallRow(U.id).children[3].textContent === '5.25 in',
        'the checklist shows a THIN wall as THIN + its physical width');
  wallRow(U.id).children[2].children[2].onclick();
  check(U.envelope && U.envelope.thickness === 5.5, '+ from THIN here: the first 0.5 in step at or above the THIN width (5.25 → 5.5)');
  wallRow(U.id).children[2].children[0].onclick();
  check(!U.envelope, '− from the minimum: THIN again');
  wallRow(U.id).children[2].children[1].value = '8'; wallRow(U.id).children[2].children[1].onchange();
  check(U.envelope && U.envelope.thickness === 8 && _effectiveWall(U).wall.thickness === 8 && !U.wall,
        'its thickness is set upstream (the wall’s own envelope), not a second value');
  const pl0 = await window.Designer.designsForBackend();
  check(JSON.stringify(pl0).includes('"envelope":{"align":"center","print_reference":false,"thickness":8}'),
        'the Designer document carries it, Centered by default (Designer + Assembly geometry follow)');
  wsysUndo();
  check(!U.envelope && wsysSystemOf(U.id) && wsysSystemOf(U.id).id === S4.id, 'undo: the thickness reverts, the assignment stays');
  wsysRedo();
  check(U.envelope && U.envelope.thickness === 8, 'redo');
  // a wall's thickness is ITS OWN: the other walls of its construction keep theirs
  wsys.pick = S2.id; wsysRender();
  const tL = wallRow(Ln.id).children[2].children[1];
  tL.value = '10.5'; tL.onchange();
  check(Ln.envelope.thickness === 10.5 && R.envelope.thickness === 10 &&
        wsysSystemOf(Ln.id) === _wsysSys(S2.id) && wsysSystemOf(R.id).id === S1.id,
        'one wall’s thickness changed (the others keep theirs); construction assignments preserved');
  wsysUndo();
  check(Ln.envelope.thickness === 10, 'undo restores it');
  wsys.pick = S1.id; wsysRender();
  const nm = row(el('wsys-props'), 'Name').children[1];
  nm.value = 'Waves'; nm.onchange();
  check(_wsysSys(S1.id).name === 'Waves' && /Waves/.test(txt('wsys-list')), 'renamed');
  const S3 = wsysNew('hollow');
  wsysDelete(S3.id);
  check(!_wsysSys(S3.id) && project.wall_systems.length === 3, 'created and deleted');
  await sleep(50);
  check(reqs('/api/wallsystems/generate') === n0.gen && reqs('/api/assembly/resolve') === n0.res &&
        reqs('/api/assembly/print_plan') === n0.plan && reqs('/api/wallsystems/layer') === 0 &&
        reqs('/api/assembly/geometry') === n0.geo,
        'changing several Wall Systems and parameters generates NOTHING (no generation, resolve, geometry or plan request)');
  // undo / redo (workspace history)
  wsysUndo();
  check(project.wall_systems.length === 4, 'undo: the deleted system returns');
  wsysRedo();
  check(project.wall_systems.length === 3 && _wsysSys(S1.id).params.paths === 6, 'redo');
  // ---- GENERATE ----
  const t0 = Date.now();
  const pg = wsysGenerate();
  check(wsysStatus() === 'generating' && /GENERATING/.test(txt('wsys-status')), 'status: GENERATING');
  await pg;
  check(wsysStatus() === 'current' && /CURRENT/.test(txt('wsys-status')) && wsys.gen.layers.length === 4 &&
        reqs('/api/wallsystems/generate') === n0.gen + 1, 'Generate Wall Systems: every layer, status CURRENT');
  const types = Object.values(wsys.gen.systems).map(s => s.type).sort().join();
  check(types.includes('chain') && types.includes('interleaved') && types.includes('parallel'),
        'the generated constructions: interleaved (two thicknesses) + chain + parallel (the ungrouped wall)');
  // GENERATED GEOMETRY IS SHOWN (2026-10-11 correction): the view switches to it
  await sleep(30); await _wsysFetchLayer(-1);
  let sc = wsysScene();
  check(wsys.display === 'generated' && sc.layer === -1 && sc.kind === 'generated' && sc.status === 'current' && sc.paths > 0,
        'after generating, the Base design shows its GENERATED printable geometry (CURRENT)');
  console.log('    (generation ' + (Date.now() - t0) + ' ms)');
  // ---- INSPECTION ----
  wsysSelectLayer(0);
  await _wsysFetchLayer(0);
  sc = wsysScene();
  check(wsys.layer === 0 && +el('wsys-layer-input').value === 1 && el('wsys-layer-count').textContent === '/ 4' &&
        sc.kind === 'generated' && sc.paths > 0 && +el('wsys-layer-slider').max === 3,
        'layer 1 of 4: its generated paths drawn; the slider spans the stack');
  el('wsys-layer-slider').value = '2'; wsysSelectLayer(el('wsys-layer-slider').value);
  await _wsysFetchLayer(2);
  check(wsys.layer === 2 && wsysScene().paths > 0 && +el('wsys-layer-input').value === 3, 'the layer slider selects a layer');
  wsysSelectLayer(0);
  wsysStep(1);
  check(wsys.layer === 1, 'next layer');
  wsysStep(-1);
  check(wsys.layer === 0, 'previous layer');
  el('wsys-layer-input').value = '4'; wsysSelectLayer(el('wsys-layer-input').value - 1);
  wsysStep(1);
  check(wsys.layer === 3, 'direct layer number; stepping past the top stays on the last layer');
  wsysSetDisplay('envelope');
  check(_wsysShown(3).every(it => it.role !== 'path') && wsysScene().kind === 'envelope',
        'Envelope display: the architectural envelope of the layer');
  wsysSetDisplay('generated');
  const d3 = await _wsysFetchLayer(3);
  check(d3 && d3.polylines.length > 0 && _wsysShown(3).every(it => it.role === 'path') &&
        new Set(d3.polylines.map(p => p.system)).size >= 2, 'Generated Paths: the printable wall geometry of the layer (two constructions)');
  const lf = wsys.stats.layerFetches, ng = reqs('/api/wallsystems/generate');
  wsysSelectLayer(1); await _wsysFetchLayer(1); wsysSelectLayer(3); wsysSelectLayer(2); wsysSelectLayer(1); wsysSetDisplay('envelope'); wsysSetDisplay('generated');
  await sleep(20);
  check(wsys.stats.layerFetches === lf && reqs('/api/wallsystems/generate') === ng && Object.keys(wsys.cache).length === 5,
        'switching between visited layers / display modes: served from the cache (no fetch), nothing regenerated');
  check(/bead level/.test(txt('wsys-support-head')), 'support findings of the selected layer: bead level for generated paths');
  wsysSetDisplay('envelope');
  check(/architectural/.test(txt('wsys-support-head')), '… and architectural (preliminary) for the envelope');
  wsysSetDisplay('generated');
  wsysOrbit();
  check(wsys.view.mode === 'orbit', 'optional 3D orbit');
  wsysTopView();
  check(wsys.view.mode === 'top', 'one-click Top View');
  wsysShowBase();
  check(wsys.layer === -1, 'back to the Base design view');
  wsysSelectLayer(3);
  // ---- INVALIDATION ----
  wsysSetType(S2.id, 'parallel');
  await wsysNoteState();
  check(wsysStatus() === 'outdated' && /OUTDATED/.test(txt('wsys-status')) && _wsysShown(3) !== null &&
        /Regenerate Wall Systems/.test(el('wsys-generate').textContent),
        'a Wall System change: OUTDATED — the old geometry stays visible as a labelled reference');
  setWorkspace('simulation');
  await simShow();
  await simGenerate();
  check(!sim.data && sim.wsBlocked === 'outdated' && /OUTDATED/.test(txt('sim-status')) && /Go to Wall Systems/.test(txt('sim-status')),
        'the Print Simulation refuses OUTDATED geometry and sends the user back to Wall Systems');
  setWorkspace('wallsystems');
  await wsysGenerate();
  check(wsysStatus() === 'current', 'regenerated: CURRENT');
  setWorkspace('simulation');
  await simGenerate();
  check(sim.data && !sim.wsBlocked && sim.data.res.plan.layers.length === 4, 'the Print Simulation plans the CURRENT generated geometry');
  setWorkspace('assembly');
  await asmSetLayerHeight(1.0);
  await wsysNoteState();                               // (the resolve notes it; the key is computed asynchronously)
  check(wsysStatus() === 'outdated', 'an Assembly change: OUTDATED');
  setWorkspace('wallsystems');
  await wsysGenerate();
  check(wsysStatus() === 'current' && wsys.gen.layers.length === 6, 'regenerated for the new stack (6 layers)');
  // ---- ARCHITECTURE edited from this workspace (2026-10-15 correction): the
  // Designer's own Junctions, Ends & Corners controls on the SAME canonical
  // state — never Wall-System properties; every edit OUTDATES the generation ----
  {
    wsysRender();
    const arch = () => all(el('wsys-arch'));
    const ctl = id => arch().find(c => c.id === 'wsys-arch-' + id);
    check(!!ctl('junction-style') && !!ctl('cap-style') && !!ctl('cap-' + Ln.id) && !!ctl('corner-' + R.id) &&
          !ctl('corner-' + Cc.id) && !ctl('cap-' + R.id),
          'Wall Systems: junction treatment, end caps (open walls), corners (walls with corners) — the Designer\u2019s controls');
    const ws0 = JSON.stringify(project.wall_systems), depth = wsys.hist.undo.length;
    ctl('junction-style').value = 'round'; ctl('junction-style').onchange();
    await wsysNoteState();
    check(layer.junction_style === 'round' && wsysStatus() === 'outdated' && wsys.hist.undo.length === depth + 1,
          'junction treatment edited here: the canonical document value; the generation is OUTDATED; one undo step');
    ctl('junction-radius').value = '4'; ctl('junction-radius').onchange();
    check(layer.junction_radius === 4, 'junction radius');
    ctl('corner-' + R.id).value = 'round'; ctl('corner-' + R.id).onchange();
    check(R.corner_radius === 2, 'corner treatment Rounded: the wall\u2019s OWN Corner R (2 in)');
    ctl('corner-r-' + R.id).value = '3'; ctl('corner-r-' + R.id).onchange();
    ctl('cap-' + Ln.id).value = 'full_round'; ctl('cap-' + Ln.id).onchange();
    check(R.corner_radius === 3 && Ln.cap && Ln.cap.style === 'full_round', 'Corner R and a wall\u2019s own end cap');
    check(JSON.stringify(project.wall_systems) === ws0, 'no Wall-System copy of any architectural value');
    updatePropPanel();
    const dg = all(el('walls-geom'));
    check(dg.find(c => c.id === 'walls-junction-style').value === 'round' && dg.find(c => c.id === 'walls-junction-radius').value === 4 &&
          dg.find(c => c.id === 'walls-corner-' + R.id).value === 'round' && dg.find(c => c.id === 'walls-cap-' + Ln.id).value === 'full_round',
          'the Designer\u2019s Junctions, Ends & Corners shows the same values (one state, two editing surfaces)');
    await wsysNoteState();
    check(wsysStatus() === 'outdated', 'still OUTDATED until regenerated');
    for (let k = 0; k < 5; k++) wsysUndo();
    await wsysNoteState();
    check(layer.junction_style !== 'round' && !R.corner_radius && !Ln.cap && wsysStatus() === 'current',
          'undo here restores the architecture — CURRENT again (the same design as generated)');
  }
  wsysSetThickness(U.id, 9);
  await wsysNoteState();
  check(wsysStatus() === 'outdated' && U.envelope.thickness === 9, 'a thickness edit from this workspace makes the generation OUTDATED');
  wsysSetThickness(U.id, 8);
  await wsysNoteState();
  setWorkspace('designer');
  R.envelope.thickness = 11; historyCheckpoint();
  setWorkspace('wallsystems');
  await wsysNoteState();
  check(wsysStatus() === 'outdated', 'a Designer change (a wall thickness): OUTDATED');
  // ---- background + cancel ----
  const pc = wsysGenerate();
  check(wsysStatus() === 'generating', 'generating in the background');
  wsysCancel();
  await pc;
  check(wsysStatus() === 'outdated' && /cancelled/.test(txt('wsys-status')), 'Cancel: the old (outdated) geometry stays; nothing half-applied');
  // ---- PRINTABILITY INSPECTION (M2): final vs preliminary, jump + highlight ----
  setWorkspace('assembly');
  const tf0 = asm.state.sections[0].transform;
  asm.state.sections[0].transform = { profile: 'linear', shift: [3.0, 0], scale_in: 0 };   // leans 3 in / layer (> the 1 in default)
  await asmResolve();
  setWorkspace('wallsystems'); await wsysShow(); await wsysGenerate();
  const fin = _wsysFinal(), pre = _wsysPreliminary();
  check(fin.length > 0 && pre.length > 0 && fin.some(f => f.category === 'architectural') &&
        fin.every(f => ['architectural', 'lattice', 'construction'].includes(f.category)) &&
        asm.resolved.support.level === 'envelope' && wsys.gen.support.level === 'bead',
        'FINAL bead-level findings (classified: architectural / lattice / construction) and PRELIMINARY architectural ones, separately');
  check(/FINAL · bead level/.test(txt('wsys-findings')) && /Architectural overhang/.test(txt('wsys-findings')) && /CURRENT/.test(txt('wsys-findings')),
        'the Final list: grouped by kind, its status shown');
  const f0 = fin.find(f => f.layer > 0) || fin[0];
  wsysSelectFinding('final', f0.id);
  await _wsysFetchLayer(f0.layer);
  const s0 = wsysScene();
  check(wsys.layer === f0.layer && s0.kind === 'generated' && s0.highlight === f0.id && s0.focus &&
        s0.focus.x === f0.centre[0] && s0.focus.y === f0.centre[1] && +el('wsys-layer-input').value === f0.layer + 1 &&
        all(el('wsys-findings')).some(c => /wsys-find sel/.test(c.className || '')),
        'clicking a final finding: its layer, the generated paths, centred on it and highlighted');
  check(/This layer · FINAL/.test(txt('wsys-support-head')) && all(el('wsys-support')).some(c => /wsys-find/.test(c.className || '')),
        'the selected layer lists its final findings');
  wsysSetFindings('preliminary');
  check(/PRELIMINARY/.test(txt('wsys-findings')) && !/FINAL · bead level/.test(txt('wsys-findings')),
        'the Preliminary list is distinct (the Assembly\u2019s envelope check)');
  wsysSelectFinding('preliminary', pre[0].id);
  check(wsys.layer === pre[0].layer && wsysScene().kind === 'envelope' && wsysScene().highlight === pre[0].id &&
        /This layer · PRELIMINARY/.test(txt('wsys-support-head')),
        'a preliminary finding: its layer on the envelope, highlighted');
  wsysSetFindings('final');
  wsysTopView();
  check(!wsysScene().focus, 'Top View fits the whole layer again');
  setWorkspace('assembly');
  asm.state.sections[0].transform = tf0;
  await asmResolve();
  setWorkspace('wallsystems'); await wsysShow();
  // ---- SAVE / OPEN ----
  const P = await projectState();
  check(P.wall_systems.length === 3 && P.wall_systems.some(s => s.name === 'Waves' && s.members.includes(Cc.id)),
        'the project state carries the Wall Systems');
  const saved = await _post('/api/project/save', { project: P });
  check(saved.file.schema_version === 7 && JSON.stringify(saved.file.project.wall_systems) === JSON.stringify(P.wall_systems),
        'saved (schema 7) with the Wall Systems');
  global.confirm = () => true;
  await openProjectText(JSON.stringify(saved.file), 'ws.muddesign');
  check(JSON.stringify(project.wall_systems) === JSON.stringify(P.wall_systems) && !('wall_groups' in layer) && layer.source_paths.find(p => p.id === R.id).envelope.thickness === 11 &&
        layer.source_paths.find(p => p.id === U.id).envelope.thickness === 8 &&
        wsysStatus() === 'not_generated' && !wsys.hist.undo.length,
        "Open: Wall Systems and every wall's own thickness restored; nothing generated yet");
  // (M4) the persistent junction records (shadow) travel opaquely: their ids survive Save / Open / Save
  const J0 = saved.file.project.junctions;
  check(J0 && J0.mode === 'shadow' && JSON.stringify(project.junctions) === JSON.stringify(J0),
        'Open: the junction records (shadow) are kept as saved');
  const saved2 = await _post('/api/project/save', { project: await projectState() });
  check(JSON.stringify(saved2.file.project.junctions.records.map(r => r.id)) === JSON.stringify(J0.records.map(r => r.id)),
        'Save again: the same junction ids');
  newProject();
  check(project.wall_systems.length === 0 && layer.source_paths.length === 0 && !project.junctions, 'New Project: no Wall Systems, no walls, no junction records');
  // ---- a VARIANT open: architecture edited from Wall Systems goes to its owner — no Variant copy ----
  {
    addPrimitive('RectanglePath'); addPrimitive('LinePath');
    const [VR, VL] = layer.source_paths;
    VL.start = [140, 330]; VL.end = [260, 330]; _computePrimitivePoints(VL);
    setWallProps([VR.id, VL.id], { thickness: 10 });
    historyCheckpoint();
    const v1 = createDerivedDesign('V1', 'base');
    await editDesign(v1);
    setWorkspace('wallsystems'); await wsysShow();
    const ctl = id => all(el('wsys-arch')).find(c => c.id === 'wsys-arch-' + id);
    ctl('cap-' + VL.id).value = 'full_round'; ctl('cap-' + VL.id).onchange();
    ctl('corner-' + VR.id).value = 'round'; ctl('corner-' + VR.id).onchange();
    ctl('junction-style').value = 'round'; ctl('junction-style').onchange();
    await window.Designer.designsForBackend();             // (syncs the open Variant)
    const B = project.baseDoc, dl = project.deltas[v1] || {};
    const bp = id => B.source_paths.find(p => p.id === id);
    check(bp(VL.id).cap && bp(VL.id).cap.style === 'full_round' && bp(VR.id).corner_radius === 2 && B.junction_style === 'round',
          'Variant open: the cap, Corner R and junction treatment are written to their owner (Base)');
    check(!JSON.stringify(dl.patch || {}).includes('full_round') && !JSON.stringify(dl.patch || {}).includes('corner_radius') &&
          !('junction_style' in (dl.settings || {})),
          '… and the Variant stores no copy of them');
    await editDesign('base');
  }
  console.log('UI WALL SYSTEMS SMOKE PASSED');
})().catch(e => { console.error(e); process.exit(1); });
`);
