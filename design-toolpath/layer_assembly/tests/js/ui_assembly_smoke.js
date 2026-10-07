// UI smoke test of the ASSEMBLY workspace: the real static/app.js +
// static/assembly.js against the REAL backend (layer_assembly blueprint +
// Layer Design endpoints), DOM stubbed. Run via layer_assembly/tests/test_assembly_ui.py.
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
src += '\n' + fs.readFileSync(path.join(STATIC, 'assembly.js'), 'utf8');
eval(src + `
;(async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const el = id => document.getElementById(id);
  const geoCalls = () => calls.filter(u => u === '/api/assembly/geometry').length;

  // ---- a Base design in the Designer ----
  addPrimitive('RectanglePath');                       // (140,140) 120 x 120
  const R = layer.source_paths[0];
  R.wall = { thickness: 10, align: 'auto', print_reference: false };
  layer.material.physical = true;
  historyCheckpoint();
  const designerBefore = JSON.stringify(layer);
  renderDesignTabs();
  check(project.designs.length === 1 && el('design-tabs').style.display === 'none',
        'one design: the Designer looks exactly as before (no design tabs)');

  // ---- switch to Assembly ----
  setWorkspace('assembly');
  check(el('ws-designer').style.display === 'none' && el('ws-assembly').style.display === '' &&
        currentWorkspace === 'assembly', 'Assembly workspace shown, Designer hidden');
  await asmResolve();
  check(JSON.stringify(layer) === designerBefore, 'switching workspaces leaves the Designer state untouched');
  let r = asm.resolved;
  check(r.sections[0].layers === 24 && r.total_height === 36, 'default section Base 36 in → 24 layers at 1.5 in');

  // keyboard: Designer shortcuts are off while Assembly is shown
  selectedId = R.id;
  (listeners.keydown || []).forEach(f => f({ key: 'Delete', preventDefault() {} }));
  check(layer.source_paths.length === 1, 'Delete in the Assembly does not delete Designer geometry');
  selectedId = null;

  // ---- layer height / sections ----
  await asmSetLayerHeight(2);
  check(asm.resolved.sections[0].layers === 18 && asm.resolved.total_layers === 18, 'layer height 2 → 18 layers (intent kept: 36 in)');
  await asmSetLayerHeight(1.5);
  await asmAddSection();
  await asmSetSectionHeight(1, 24);
  await asmAddSection();
  await asmSetSectionHeight(2, 12);
  r = asm.resolved;
  check(r.sections.map(s => s.layers).join() === '24,16,8' && r.total_layers === 48 && r.total_height === 72,
        'add sections + edit heights: 24 / 16 / 8 layers, 72 in');
  check(r.sections.map(s => [s.z_bottom, s.z_top].join('-')).join() === '0-36,36-60,60-72', 'Z ranges stack bottom-up');
  await asmMoveSection(0, +1);
  check(asm.state.sections.map(s => s.height).join() === '24,36,12', 'reorder: the bottom section moved up one');
  await asmMoveSection(1, -1);
  await asmDeleteSection(2);
  check(asm.state.sections.length === 2 && asm.resolved.total_height === 60, 'delete a section');
  await asmAddSection(); await asmSetSectionHeight(2, 12);

  // ---- rounding is shown, as information ----
  await asmSetLayerHeight(1.75);
  r = asm.resolved;
  check(r.sections.map(s => s.layers).join() === '21,13,7' && r.total_layers === 41, '1.75 in: 21 / 13 / 7 layers');
  check(el('asm-totals').textContent.includes('71.75 in actual') && el('asm-totals').textContent.includes('72.00 in desired'),
        'totals: actual vs desired height');
  const cards = el('asm-sections').children;
  const flat = n => [n, ...(n.children || []).flatMap(flat)];
  const texts = cards.flatMap(flat).map(n => n.textContent || '');
  check(texts.some(t => t.includes('-1.25 in, whole layers')), 'a section shows its rounding difference');
  check(cards[0].dataset.index === 2 && cards[cards.length - 1].dataset.index === 0,
        'the stack is listed top of the print first, bottom section last');
  await asmSetLayerHeight(1.5);

  // ---- a derived design, edited in the Designer (delta only) ----
  el('asm-new-name').value = 'Door Gap'; el('asm-new-parent').value = 'base';
  const d1 = asmNewDerived();
  check(project.designs.length === 2 && project.designs[1].parent === 'base', '+ New Derived Design (name, parent)');
  await editDesign(d1);
  check(project.active === d1 && layer.source_paths[0].id === R.id, 'the derived design opens with the inherited geometry');
  layer.openings.push({ id: 'door', source_path_id: R.id, center_s: 60, width: 30, end_treatment: 'inherit',
                        z_min: null, z_max: null, label: '' });
  historyCheckpoint();
  await editDesign('base');
  check(Object.keys(project.deltas[d1].patch).join() === 'openings' && !!project.deltas[d1].patch.openings.door,
        'leaving it stores ONLY its difference (the opening)');
  check((layer.openings || []).length === 0 && layer.source_paths.length === 1, 'Base itself is unchanged');

  // ---- reuse the same design; preview data ----
  asm.state.sections = [{ design_id: 'base', height: 36 }, { design_id: d1, height: 24 }, { design_id: 'base', height: 12 }];
  const g0 = geoCalls();
  await asmResolve();
  check(geoCalls() === g0 + 1 && Object.keys(asm.geometry).sort().join() === ['base', d1].sort().join(),
        'geometry fetched once for the designs in use');
  const st = asmStack();
  check(st.length === 48 && st[0].z_top === 1.5 && st[47].z_top === 72, 'preview receives every layer instance with its Z');
  check(st[0].polylines === st[45].polylines && st[0].polylines !== st[30].polylines,
        'Base reused: the SAME design geometry (not a copy); Door Gap differs');
  const used = st.filter(L => L.design_id === 'base').length;
  check(used === 32, 'Base referenced by 32 layers in two sections');
  await asmSetLayerHeight(2);
  check(geoCalls() === g0 + 1 && asmStack().length === 36, 'a layer-height change re-resolves without rebuilding designs');
  asmDrawPreview();
  asmViewPreset('front'); asmViewPreset('fit');

  // ---- rename: name only, identity kept ----
  const sectionIdsBefore = asm.state.sections.map(x => x.design_id).join();
  check(asmRenameDesign(d1, '  Variant 1 ') && project.designs[1].name === 'Variant 1' && project.designs[1].id === d1,
        'rename changes the name (trimmed), not the id');
  check(asm.state.sections.map(x => x.design_id).join() === sectionIdsBefore && _asmName(d1) === 'Variant 1',
        'sections still reference the same id and now show the new name');
  check(!asmRenameDesign(d1, '   ') && project.designs[1].name === 'Variant 1', 'a blank name is refused (name kept)');
  asmRenameDesign('base', 'Variant 1');
  check(project.designs[0].name === 'Variant 1' && project.designs[1].parent === 'base',
        'duplicate display names allowed; the parent link (by id) survives');
  asmRenameDesign('base', 'Base');
  await asmResolve();
  check(geoCalls() === g0 + 1, 'renaming does not rebuild geometry');

  // ---- transforms ----
  await asmSetLayerHeight(1.5);
  const T = i => asm.resolved.instances[i].transform;
  check(asm.resolved.instances.every(i => i.transform.scale === 1 && i.transform.tx === 0 && i.transform.ty === 0),
        'no transform: every instance is the identity');
  await asmSetTransform(0, 'dx', '0.3');                 // typed: kept as typed (no snapping)
  check(asm.state.sections[0].transform.shift[0] === 0.3, 'a typed 0.3 in/layer is stored as 0.3');
  await asmSetTransform(0, 'dx', 0.25);
  check(T(1).tx === 0.25 && T(23).tx === 5.75, 'shift X 0.25 in/layer');
  check(T(24).tx === 6 && T(47).tx === 6, 'later sections continue from where section 1 ended (no snap back)');
  await asmSetTransform(1, 'scale_in', 0.5);
  const st2 = asmStack();
  const b = _asmBBox(st2[0].polylines), place = (L, x, y) => _asmPlace(L.transform, x, y);
  const w = L => place(L, b[2], 0)[0] - place(L, b[0], 0)[0];
  check(Math.abs(w(st2[24]) - (b[2] - b[0])) < 1e-9 && Math.abs(w(st2[27]) - (w(st2[24]) - 2 * 3 * 0.5)) < 1e-6,
        'scale 0.5 in/layer: the outer edges move in 0.5 in per layer');
  check(Math.abs(w(st2[40]) - w(st2[47])) < 1e-9 && Math.abs(w(st2[40]) - (w(st2[24]) - 16 * 1.0)) < 1e-6,
        'the next section keeps the scaled size');
  asm.open[1] = true; asmRender();
  const txt = el('asm-sections').children.flatMap(flat).map(n => n.textContent || '');
  check(txt.some(t => t.includes('scale in 0.5000 in/layer')) && txt.some(t => t.includes('shift +0.2500, 0.0000 in/layer')),
        'section cards summarise their transform');
  await asmSetLayerHeight(2);
  check(asm.resolved.instances.length === 36 && T(17).tx === 17 * 0.25, 'layer height 2: transforms re-derived per layer');
  asmDrawPreview();
  const allInputs = el('asm-sections').children.flatMap(flat).filter(n => n.className === 'asm-tf')
    .flatMap(flat).filter(n => n.type === 'number');
  check(allInputs.length >= 3 && allInputs.every(n => n.step === '0.0625'), 'transform inputs step 1/16 in');

  // ---- quadratic ----
  await asmSetLayerHeight(1.5);
  await asmSetTransform(1, 'scale_in', 0);
  await asmSetTransform(1, 'dx', 0.25);
  await asmSetTransform(1, 'profile', 'quadratic');                 // section 2: 16 layers
  const q = [...Array(17).keys()].map(k => T(24 + Math.min(k, 15)).tx);
  const e0 = T(24).tx;
  check(Math.abs(T(25).tx - e0 - 0.25 / 16) < 1e-12 && Math.abs(T(39).tx - T(38).tx - 0.25 * 15 / 16) < 1e-12,
        'quadratic: first step 0.25/16, top step 0.25·15/16');
  check(Math.abs(T(40).tx - T(39).tx - 0.25) < 1e-12, 'quadratic: the step into the next section is the entered rate');
  const optsP = el('asm-sections').children.flatMap(flat).filter(n => n.value === 'quadratic' && n.textContent === 'Quadratic');
  const noteP = el('asm-sections').children.flatMap(flat).some(n => (n.textContent || '').includes('final per-layer rate'));
  check(optsP.length === 1 && noteP, 'Profile offers Quadratic, with its one-line explanation');
  // the preview draws the resolved transform 1:1 — XY and Z share one scale
  asmViewPreset('front');
  const P = asmProjector(asmStack(), 800, 600);
  const [ax, ay] = P(0, 0, 0), [bx] = P(10, 0, 0), [, cy] = P(0, 0, 10);
  check(Math.abs((bx - ax) - (ay - cy)) < 1e-9 && bx - ax > 0, 'preview: 10 in of X and 10 in of Z are the same length');
  const stq = asmStack();
  check(stq[30].transform === asm.resolved.instances[30].transform, 'preview uses the resolved transform object itself');

  // ---- support: the door gap is closed again above it (Base over Door Gap) ----
  asm.state.sections.forEach(x => { x.transform = { profile: 'linear', shift: [0, 0], scale_in: 0 }; });
  await asmSetLayerHeight(1.5);
  let SP = () => asm.resolved.support;
  const txtOf = id => el(id).children.flatMap(flat).map(n => n.textContent || '').join(' | ');
  check(SP() && SP().needs_header === 1 && SP().findings[0].layer === 40, 'HEADER NEEDED where Base closes the door gap (layer 41)');
  check(txtOf('asm-support').includes('HEADER NEEDED · unsupported span'), 'the support panel shows the warning');
  const fid = SP().findings[0].id;
  check(asm.state.objects.length === 0, 'no header is created automatically');
  await asmAddHeader(fid);
  let H = SP().headers[0];
  check(SP().supported === 1 && SP().needs_header === 0 && H.snapped && H.supports[0] === fid, 'Add Header: snapped to the gap, the span is supported');
  check(Math.abs(H.length - (H.span + 16)) < 1e-9 && H.z_top === 60 && H.z_bottom === 54,
        'length = span + 2 × 8 in bearing; top at the boundary (z 60), 6 in thick');
  await asmSetHeader(H.id, 'bearing', 4);
  await asmSetHeader(H.id, 'thickness', 3);
  H = SP().headers[0];
  check(Math.abs(H.length - (H.span + 8)) < 1e-9 && H.z_bottom === 57 && SP().supported === 1, 'bearing / thickness edits');
  check(txtOf('asm-support').includes('Supported by header') && txtOf('asm-support').includes('need a pocket'),
        'header card: supported span and the bearing pockets');
  asmDrawPreview();
  await asmRemoveHeader(H.id);
  check(SP().needs_header === 1 && asm.state.objects.length === 0, 'removing the header restores the warning');
  await asmIgnoreSpan(fid);
  check(SP().overridden === 1 && SP().needs_header === 0 && SP().supported === 0 && asm.state.overrides.length === 1 &&
        txtOf('asm-support').includes('IGNORED'), 'Ignore: recorded as an override, NOT as supported');
  asmDrawPreview();
  await asmUnignoreSpan(fid);
  check(asm.state.overrides.length === 0 && SP().needs_header === 1, 'Restore warning');

  // ---- assembly-wide transform: runs through every section and design ----
  await asmSetTransform('asm', 'dx', 0.25);
  check(asm.resolved.instances.every((L, g) => Math.abs(L.transform.tx - 0.25 * g) < 1e-12),
        'assembly transform: +0.25 in/layer across all 48 layers, no restart at sections');
  asm.open.asm = true; asmRender();
  check(txtOf('asm-assembly-tf').includes('shift +0.2500, 0.0000 in/layer (linear)') &&
        txtOf('asm-assembly-tf').includes('WHOLE stack'), 'assembly transform panel + summary');
  await asmAddHeader(SP().findings[0].id);
  H = SP().headers[0];
  const Ti = asm.resolved.instances[H.layer].transform;
  check(SP().supported === 1 && Math.abs(H.centre[0] - (Ti.scale * H.x + Ti.tx)) < 1e-9 && Ti.tx === 10,
        'the header follows the assembly transform (placed by its layer)');
  asmDrawPreview();
  await asmSetTransform('asm', 'dx', 0);
  await asmRemoveHeader(H.id);

  // ---- Assembly undo / redo (separate from the Designer's) ----
  asm.hist = { undo: [], redo: [], group: null, t: 0 };
  const dUndo = _hist.undo.length, dRedo = _hist.redo.length;
  const S0 = JSON.stringify(asm.state);
  await asmSetLayerHeight(2);
  await asmAddSection();
  await asmSetSectionHeight(asm.state.sections.length - 1, 6);
  const S3 = JSON.stringify(asm.state);
  check(asm.hist.undo.length === 3 && el('asm-undo').disabled === false, 'three Assembly edits = three undo steps');
  await asmUndo(); await asmUndo();
  check(asm.state.sections.length === 3 && asm.state.layer_height === 2, 'undo: height edit, then the added section');
  await asmUndo();
  check(JSON.stringify(asm.state) === S0 && asm.resolved.total_layers === 48, 'undo restores the layer height and re-resolves');
  await asmRedo(); await asmRedo(); await asmRedo();
  check(JSON.stringify(asm.state) === S3, 'redo replays all three');
  await asmUndo();
  await asmSetSectionDesign(0, d1);
  check(asm.hist.redo.length === 0, 'a divergent edit clears redo');
  asm.hist.group = null;
  const n0 = asm.hist.undo.length;
  await asmSetSectionHeight(1, 20); await asmSetSectionHeight(1, 21); await asmSetSectionHeight(1, 22);
  check(asm.hist.undo.length === n0 + 1, 'retyping one field quickly = ONE step');
  check(_hist.undo.length === dUndo && _hist.redo.length === dRedo, 'Designer history untouched by Assembly edits');
  const Sa = JSON.stringify(asm.state);
  (listeners.keydown || []).forEach(f => f({ key: 'z', metaKey: true, preventDefault() {} }));
  await new Promise(r => realSetTimeout(r, 400));
  check(asm.state.sections[1].height !== 22 && _hist.undo.length === dUndo, 'Cmd-Z in Assembly = Assembly undo (Designer untouched)');
  (listeners.keydown || []).forEach(f => f({ key: 'z', metaKey: true, shiftKey: true, preventDefault() {} }));
  await new Promise(r => realSetTimeout(r, 400));
  check(JSON.stringify(asm.state) === Sa, 'Cmd-Shift-Z = Assembly redo');
  for (const [label, act, ok] of [
      ['transform', () => asmSetTransform('asm', 'dx', 0.125), () => asm.state.transform.shift[0] === 0.125],
      ['rename', () => asmRenameDesign(d1, 'Variant X'), () => _asmName(d1) === 'Variant X'],
      ['ignore', () => asmIgnoreSpan(SP().findings[0].id), () => asm.state.overrides.length === 1],
      ['add header', () => asmAddHeader(SP().findings.find(f => f.status === 'needs_header').id), () => asm.state.objects.length === 1]]) {
    const before = JSON.stringify(asm.state), name0 = _asmName(d1);
    await act();
    const done = ok();
    await asmUndo();
    check(done && JSON.stringify(asm.state) === before && _asmName(d1) === name0, 'undo: ' + label);
  }

  // ---- physical support: Bead Width + MAXIMUM OVERHANG (min overlap derived) ----
  await asmSetSectionDesign(0, 'base');
  const gBefore = geoCalls(), nMo = asm.hist.undo.length;
  check(asm.state.max_overhang === 1.5 && asm.state.min_overlap === undefined && el('asm-max-overhang').value === 1.5 &&
        el('asm-bead-width').value === 3 && txtOf('asm-overlap-note').includes('Minimum overlap') &&
        txtOf('asm-overlap-note').includes('1.50 in (derived)') && txtOf('asm-overlap-note').includes('bead width − maximum overhang'),
        'Physical support: Bead width 3, Maximum overhang 1.5 (editable), Minimum overlap 1.50 in (derived)');
  await asmSetTransform(2, 'dx', 1.25);                 // the top section leans 1.25 in / layer
  check(SP().insufficient_support === 0, '1.25 in / layer lean with 1.5 in max overhang: supported');
  await asmSetMaxOverhang(1);
  check(txtOf('asm-overlap-note').includes('2.00 in (derived)') && SP().min_overlap === 2 && SP().max_overhang === 1,
        'Bead 3 − Maximum overhang 1 = Minimum overlap 2.00 in (derived, also in the report)');
  const ins = SP().findings.filter(f => f.status === 'insufficient_support');
  check(ins.length > 3 && ins.every(f => f.kind === 'overlap' && f.min_overlap === 2 && Math.abs(f.overlap - 1.75) < 0.01),
        'max overhang 1 in: INSUFFICIENT LAYER SUPPORT on every leaning layer (actual overlap 1.75 in)');
  const nBad = asm.hist.undo.length;
  await asmSetMaxOverhang(3.5); await asmSetMaxOverhang(-1);
  check(asm.state.max_overhang === 1 && asm.hist.undo.length === nBad &&
        txtOf('asm-overlap-note').includes('between 0 and the bead width'),
        'validation: 0 ≤ maximum overhang ≤ bead width (rejected, explained, no undo step)');
  const insCards = el('asm-support').children.filter(c => (c.className || '').includes('insufficient'));
  check(txtOf('asm-support').includes('INSUFFICIENT LAYER SUPPORT') && txtOf('asm-support').includes('worst overlap: 1.75') &&
        txtOf('asm-support').includes('required overlap: 2.00') && insCards.length === SP().groups.filter(g => g.status === 'insufficient_support').length &&
        insCards.length <= 4 && ins.length >= 10,
        'consecutive leaning layers are AGGREGATED: a few cards (layers a–b, required vs worst overlap), no "Add Header"');
  asmDrawPreview();
  check(Array.isArray(asm.lastLabels) && asm.lastLabels.length <= SP().groups.length && asm.lastLabels.length < ins.length,
        'the viewport draws at most one label per aggregated group, never one per layer (' + (asm.lastLabels || []).length + ' labels, ' + ins.length + ' findings)');
  check(geoCalls() === gBefore && asm.hist.undo.length === nMo + 2, 'changing the setting re-resolves only (no geometry rebuild); one undo step');
  await asmUndo();
  check(asm.state.max_overhang === 1.5 && SP().insufficient_support === 0, 'undo the overhang setting');
  await asmSetTransform(2, 'dx', 0);

  // ---- transform groups: explicit wall membership ----
  await asmSetCenterMode('multiple');
  let G = asm.resolved.groups;
  check(asm.state.centers.length === 0 && G.suggestions.length === 1 && G.suggestions[0].sources.join() === R.id &&
        asm.resolved.instances.every(L => !L.parts),
        'Multiple centres: the component is only a SUGGESTION — nothing moves until a group lists a wall');
  check(asm.geometry.base.polylines.every(pl => pl.src && pl.src.length === pl.pts.length && pl.src.every(x => x === R.id)),
        'every printable vertex carries its source wall id (lattice included)');
  await asmSuggestCenters();
  const g1 = asm.state.centers[0];
  check(asm.state.centers.length === 1 && g1.sources.join() === R.id && g1.id.startsWith('c') && Number(g1.id.slice(1)) > 0 &&
        asm.resolved.groups.sources[R.id].group === g1.id, 'Suggest from components: one group listing the wall, stable id');
  await asmSuggestCenters();
  check(asm.state.centers.length === 1, 'suggesting again adds nothing (the wall is taken)');
  await asmSetTransform('asm', 'scale_in', 0.25);
  check(asm.resolved.instances.every(L => L.parts && L.parts[g1.id]), 'the group places its wall');
  await asmAddCenter(); await asmAddCenter();
  const [m1, m2] = asm.state.centers.slice(1);
  check(asm.state.centers.length === 3 && !m1.sources.length && !m2.sources.length && m1.id !== m2.id && m1.id !== g1.id &&
        asm.selCenter === m2.id, 'two manual groups added (stable, distinct ids; the new one selected)');
  check(asm.resolved.instances.every(L => Object.keys(L.parts).join() === g1.id) &&
        txtOf('asm-assembly-tf').includes('no walls assigned yet'), 'groups without walls move nothing (no nearest-centre rule)');
  check(txtOf('asm-assembly-tf').includes('Walls → transform group') && txtOf('asm-assembly-tf').includes('Rect'),
        'the per-wall assignment table lists the walls');
  asmViewPreset('top'); asmDrawPreview();
  const [mx, my] = asm.lastProj.P(m1.x, m1.y, 0);
  check(asmCenterAt(mx + 2, my - 2) === m1.id, 'a centre is picked in the preview');
  const nH = asm.hist.undo.length, q0 = [m1.x, m1.y];
  asmCenterDragStart(m1.id);
  for (const f of [0.3, 0.7, 1]) asmCenterDragTo(...asm.lastProj.P(q0[0] + 12 * f, q0[1] - 6 * f, 0));
  await asmCenterDragEnd();
  let cm = asm.state.centers.find(c => c.id === m1.id);
  check(Math.abs(cm.x - (q0[0] + 12)) < 1e-6 && Math.abs(cm.y - (q0[1] - 6)) < 1e-6 && asm.hist.undo.length === nH + 1 &&
        asm.state.centers[0].sources.join() === R.id && !cm.sources.length,
        'drag a centre: follows the pointer; ONE undo step; membership unchanged');
  // assign by clicking geometry: select m1, Assign geometry, click a wall vertex
  asmSelectCenter(m1.id); asmToggleAssign();
  check(asm.assigning && asm.selCenter === m1.id, 'Assign geometry mode on the selected group');
  const top = asmStack()[asmStack().length - 1], pl0 = top.polylines[0];
  const v = _asmPlace(top.transform, pl0.pts[0][0], pl0.pts[0][1]);
  const [vx, vy] = asm.lastProj.P(v[0], v[1], top.z_top);
  check(asmSourceAt(vx + 1, vy) === R.id, 'clicking geometry picks its source wall');
  const nA = asm.hist.undo.length;
  asmAssignAt(vx + 1, vy); await new Promise(r => realSetTimeout(r, 300)); await asmResolve();
  check(asm.state.centers.find(c => c.id === m1.id).sources.join() === R.id && !asm.state.centers[0].sources.length &&
        asm.hist.undo.length === nA + 1 && asm.resolved.instances.every(L => Object.keys(L.parts).join() === m1.id),
        'click assigns the wall to the selected group (explicitly moved from C1); one undo step');
  const T1 = asm.resolved.instances[40].parts[m1.id], c1 = asm.state.centers.find(c => c.id === m1.id);
  check(Math.abs(T1.scale * c1.x + T1.tx - c1.x) < 1e-6, 'the wall now scales about the pivot of its new group');
  asmAssignAt(vx + 1, vy); await new Promise(r => realSetTimeout(r, 300)); await asmResolve();
  check(!asm.state.centers.some(c => c.sources.length) && asm.resolved.instances.every(L => !L.parts),
        'clicking one of its own walls again removes it');
  asmToggleAssign();
  await asmAssignSource(R.id, m2.id);
  check(asm.state.centers.find(c => c.id === m2.id).sources.join() === R.id, 'the per-wall selector assigns too');
  // transform-group colours are diagnostic: group colour vs Designer BLUE
  check(_asmStrokeColor('base', R.id) === _asmGroupColor(m2.id) && _asmStrokeColor('base', 'nope') === '#4a9eff' &&
        _asmStrokeColor('base', null) === '#4a9eff' && _asmStrokeColor(d1, 'nope') === '#4a9eff' &&
        !ASM_GROUP_COLORS.includes('#4a9eff') && _asmColor(d1) !== '#4a9eff',
        'Multiple centres: assigned geometry in its group colour, UNASSIGNED geometry in the normal Designer blue (not its design colour)');
  await asmSetCenter(m2.id, 100, 120);
  cm = asm.state.centers.find(c => c.id === m2.id);
  check(cm.x === 100 && cm.y === 120 && cm.sources.join() === R.id, 'numeric X / Y edit (membership kept)');
  await asmResetCenter(m2.id);
  const auto = asm.resolved.groups.centres[m2.id].auto;
  check(Math.abs(asm.state.centers.find(c => c.id === m2.id).x - auto[0]) < 1e-9, '↺ back to the centre of its walls');
  await asmUndo(); await asmUndo(); await asmUndo();
  check(asm.state.centers.every(c => !c.sources.length), 'undo: reset, numeric edit, assignment');
  await asmRedo(); await asmRedo(); await asmRedo();
  check(asm.state.centers.find(c => c.id === m2.id).sources.join() === R.id &&
        Math.abs(asm.state.centers.find(c => c.id === m2.id).x - auto[0]) < 1e-9, 'redo them');
  await asmDeleteCenter(m1.id);
  check(asm.state.centers.length === 2 && !asm.state.centers.some(c => c.id === m1.id), 'delete a group');
  await asmUndo();
  check(asm.state.centers.some(c => c.id === m1.id), 'undo the delete');
  const keep = JSON.stringify(asm.state.centers);
  await asmSetCenterMode('footprint');
  check(asm.resolved.instances.every(L => !L.parts) && !asm.resolved.groups && JSON.stringify(asm.state.centers) === keep,
        'Whole footprint: nothing per group, and the groups are kept');
  await asmSetCenterMode('multiple');
  check(JSON.stringify(asm.state.centers) === keep && asm.resolved.instances.every(L => L.parts[m2.id]),
        'back to Multiple centres: the same groups and walls, uncorrupted');
  check(JSON.parse(JSON.stringify(asm.state)).centers.find(c => c.id === m2.id).sources.join() === R.id,
        'membership serialises with the Assembly state');
  await asmAddCenter();
  check(new Set(asm.state.centers.map(c => c.id)).size === 4, 'ids stay unique after undo / redo');
  asmDrawPreview();
  await asmSetCenterMode('footprint');
  await asmSetTransform('asm', 'scale_in', 0);
  asmViewPreset('iso');

  // ---- Layer Designs | Layers | Assembly Transform | Structure | 3D preview: five columns ----
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const iD = html.indexOf('asm-designs-col'), iL = html.indexOf('id="asm-col-layers"'),
        iT = html.indexOf('id="asm-col-transform"'), iS = html.indexOf('id="asm-col-structure"'),
        iEnd = html.indexOf('id="asm-preview-wrap"');
  const paneOf = id => { const i = html.indexOf('id="' + id + '"');
    return i < 0 ? 'missing' : (i > iS && i < iEnd) ? 'structure' : (i > iT && i < iS) ? 'transform'
         : (i > iL && i < iT) ? 'layers' : 'none'; };
  check(iD > 0 && iL > iD && iT > iL && iS > iT && iEnd > iS,
        'Layer Designs | Layers | Assembly Transform | Structure | 3D preview, side by side');
  check(!html.includes('asm-tab') && typeof asmSetTab === 'undefined', 'no tabs');
  check(['asm-layer-height', 'asm-totals', 'asm-sections', 'asm-undo', 'asm-redo'].every(id => paneOf(id) === 'layers'),
        'Layer height, totals, the section cards (with their own transforms) and Undo / Redo are in the Layers column');
  check(paneOf('asm-assembly-tf') === 'transform',
        'the Assembly Transform (profile / shift / scale, transform groups, assignment, connectors) has its own column');
  check(['asm-physical', 'asm-bead-width', 'asm-max-overhang', 'asm-overlap-note', 'asm-support'].every(id => paneOf(id) === 'structure') &&
        paneOf('asm-min-overlap') === 'missing',
        'Structure: Bead width, Maximum overhang, derived Minimum overlap, findings / headers (no editable min overlap)');
  check(html.indexOf('asmAddSection()') > iL && html.indexOf('asmAddSection()') < iT, 'Add Section is in the Layers column');
  check(['asm-pane-layers', 'asm-pane-transform', 'asm-pane-structure'].every(id => paneOf(id) !== 'missing' && el(id).style.display !== 'none') &&
        /\.asm-stack-col \.sidebar-scroll \{ overflow-y: auto; \}/.test(html),
        'all three control columns are visible at once and scroll independently');
  await asmSetCenterMode('multiple');
  const tfBox = el('asm-assembly-tf');
  const flatT = n => [n, ...(n.children || []).flatMap(flatT)];
  check(flatT(tfBox).some(n => (n.textContent || '').includes('Assembly Transform')) &&
        flatT(tfBox).some(n => (n.textContent || '').includes('Suggest from components')),
        'the Assembly Transform column renders the transform and the transform groups');
  const secTexts = flatT(el('asm-sections')).map(n => n.textContent || '');
  check(secTexts.some(t => t.includes('Transform')), 'section transform controls render in the section cards (Layers)');
  await asmSetCenterMode('footprint');
  const s0 = JSON.stringify(asm.state), d0 = JSON.stringify([layer, project.active, project.deltas, _hist.undo.length]);
  const mo0 = asm.state.max_overhang;
  await asmSetMaxOverhang(mo0 - 0.25);
  asm.hist.group = null;
  await asmUndo();
  check(asm.state.max_overhang === mo0, 'Assembly undo (a Structure edit)');
  await asmRedo();
  check(asm.state.max_overhang === mo0 - 0.25, 'Assembly redo (a Structure edit)');
  await asmUndo();
  const lh0 = asm.state.layer_height;
  await asmSetLayerHeight(lh0 + 0.5);
  asm.hist.group = null;
  await asmUndo();
  check(asm.state.layer_height === lh0, 'Assembly undo (a Layers edit)');
  check(JSON.stringify(asm.state) === s0, 'after the undos the Assembly is as before');
  check(JSON.stringify([layer, project.active, project.deltas, _hist.undo.length]) === d0, 'the Designer state is untouched');

  // ---- BEAD WIDTH: ONE project material value, edited from either side ----
  const gB = geoCalls();
  await asmSetBeadWidth(3.5);
  const rootMat = () => (project.active === 'base' ? layer : project.baseDoc).material.bead_width;
  check(window.Designer.beadWidth() === 3.5 && rootMat() === 3.5 && el('bead-width').value === '3.50' &&
        Object.values(asm.geometry).every(g => g.bead_width === 3.5) && geoCalls() > gB,
        'Assembly → Bead width 3.5 edits the Designer material itself (Material panel shows it; geometry rebuilt)');
  check(SP().min_overlap === 2 && txtOf('asm-overlap-note').includes('2.00 in (derived)'),
        'support re-analysed: minimum overlap = 3.5 − 1.5 = 2.00 (derived)');
  check((await window.Designer.designsForBackend()).every(d => d.parent == null ? d.document.material.bead_width === 3.5
                                                            : !(d.settings.material || {}).bead_width),
        'one authoritative value: the Base material; derived designs store none');
  await asmUndo();
  check(window.Designer.beadWidth() === 3 && rootMat() === 3, 'Assembly undo restores the material bead width');
  el('bead-width').value = '3.25'; onBeadWidthChange();
  asmRender();
  check(window.Designer.beadWidth() === 3.25 && el('asm-bead-width').value === 3.25,
        'Designer Material → Bead width 3.25 is what the Assembly shows (same value, no copy)');
  setWorkspace('designer');
  await editDesign(d1);
  el('bead-width').value = '2.75'; onBeadWidthChange();
  const pl = await window.Designer.designsForBackend();
  check(project.baseDoc.material.bead_width === 2.75 && !(project.deltas[d1].settings.material || {}).bead_width &&
        pl.find(d => d.id === 'base').document.material.bead_width === 2.75 && window.Designer.beadWidth() === 2.75,
        'a bead width edited while viewing a derived design goes to the project (Base), never into the variant');
  layer.material.bead_width = 3.0; historyCheckpoint();               // (as an undo in the variant would)
  await designsForBackend();
  check(project.baseDoc.material.bead_width === 3 && !(project.deltas[d1].settings.material || {}).bead_width,
        'a stale bead width in the variant is moved to the project too (backend material_moved)');
  await editDesign('base');
  check(layer.material.bead_width === 3, 'Base shows the project bead width');
  setWorkspace('assembly');

  // ---- the lattice DEFINITION belongs to the lineage (edited from a variant) ----
  setWorkspace('designer');
  await editDesign('base');
  layer.infills.push({ id: 'IW', path_id: R.id, kind: 'wall', pattern: 'wave', params: { spacing: 20 },
                       variation_index: 0 });
  historyCheckpoint();
  await editDesign(d1);
  check(layer.infills.length === 1 && layer.infills[0].pattern === 'wave', 'the variant inherits the wall infill of Base');
  layer.infills[0].pattern = 'zigzag'; layer.infills[0].params.spacing = 30;
  historyCheckpoint();
  await designsForBackend();
  check(!project.deltas[d1].patch.infills && project.baseDoc.infills[0].pattern === 'zigzag' &&
        project.baseDoc.infills[0].params.spacing === 30,
        'a lattice edit made while viewing the variant goes to Base (the lineage), not into the variant');
  await editDesign('base');
  check(layer.infills[0].pattern === 'zigzag' && layer.infills[0].params.spacing === 30,
        'Base shows the lineage-wide lattice definition');

  // ---- back to the Designer ----
  setWorkspace('designer');
  check(currentWorkspace === 'designer' && el('ws-designer').style.display === '' &&
        el('design-tabs').style.display === '' && layer.source_paths.length === 1,
        'back in the Designer: state intact, design tabs shown (2 designs)');
  console.log('UI ASSEMBLY SMOKE PASSED');
})().catch(e => { console.error(e); process.exit(1); });
`);
