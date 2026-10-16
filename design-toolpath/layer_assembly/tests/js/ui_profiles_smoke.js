// UI smoke test of ASSEMBLY PROFILES (self-supporting openings, wall tapers):
// the real static/app.js + static/assembly.js against the REAL backend, DOM
// stubbed. Run via layer_assembly/tests/test_profiles_ui.py.
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
// The test body is a real function, re-evaluated INSIDE the eval scope (so it
// sees app.js / assembly.js globals) — template strings stay ordinary.
async function run() {
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const el = id => document.getElementById(id);
  const undoN = () => asm.hist.undo.length;
  const rep = id => (asm.resolved.profiles || []).find(r => r.id === id);
  // layers with a real cut (an opening / a shortened wall; 'station' ops cut nothing) — from the
  // FULL print key (`print_key`; the page draws envelopes: geometry_key = the envelope key)
  const cutLayers = () => asm.resolved.instances.filter(i => /"(opening|extent)"/.test(i.print_key || i.geometry_key || '')).length;
  const headers = () => (asm.resolved.support.findings || []).filter(f => f.status === 'needs_header').length;

  // ---- a Base design: a hollow rectangle room + a free-standing straight Parallel wall ----
  addPrimitive('RectanglePath');                       // (140,140) 120 x 120
  const R = layer.source_paths[0];
  setWallProps([R.id], { thickness: 10 });             // (no Wall System: Hollow)
  addPrimitive('LinePath');
  const Ln = layer.source_paths[1];
  Ln.start = [140, 330]; Ln.end = [260, 330]; _computePrimitivePoints(Ln);
  setWallProps([Ln.id], { thickness: 10 });
  wsysNew('parallel', { members: [Ln.id] });                // a project Wall System: 4 walls (even: closes at the ends)
  layer.material.physical = true;
  historyCheckpoint();
  const designerBefore = JSON.stringify(layer);

  setWorkspace('assembly');
  asm.state.max_overhang = 1.5;                        // (these cases were written for the former 1.5 in default)
  asm.state.sections = [{ design_id: 'base', height: 72 }];
  await asmResolve();
  check(asm.resolved.total_layers === 48 && !asm.error, 'Base 72 in → 48 layers');
  const T = _asmProfileTargets();
  check(T[Ln.id] && T[Ln.id].taper && Math.abs(T[Ln.id].length - 120) < 1e-6 && T[Ln.id].thickness === 10,
        'the straight Parallel wall is a profile target (taperable, 120 in, 10 in thick)');
  check([0, 1, 2, 3].every(k => T[R.id + '#' + k] && !T[R.id + '#' + k].taper),
        'each rectangle edge is a target for openings only');
  check(cutLayers() === 0 && !asm.resolved.profiles.length, 'no profiles: every layer prints its canonical design');

  // ---- 1. SELF-SUPPORTING OPENING: choose the operation, then the wall ----
  const u0 = undoN();
  asmProfileBegin('opening');
  check(!!asm.profPick && el('asm-profiles').children.length >= 2, 'choosing a wall: the Profiles column offers the straight walls');
  await asmProfileCreate('opening', Ln.id);
  let P = asm.state.profiles[0];
  check(asm.state.profiles.length === 1 && P.kind === 'opening' && asm.profSel === P.id && !asm.profPick,
        'opening created on the chosen wall and selected');
  check(undoN() === u0 + 1, 'creating it is ONE undo step');
  check(asm.elevation && asm.elevation.id === P.id && asm.view.pitch === 0 &&
        Math.abs(Math.cos(asm.view.yaw) - 1) < 1e-9, 'entering it snaps the preview to its canonical elevation (along the wall, pitch 0)');
  let r = rep(P.id);
  check(r && r.valid && r.layers.length > 0 && cutLayers() === r.layers.length,
        `default opening is self-supporting and cuts ${r && r.layers.length} layers`);
  check(headers() === 0, 'no HEADER NEEDED for the valid self-supporting opening');
  const vk = Object.keys(asm.variants).filter(k => k.includes('|env|'));
  check(vk.length > 0 && vk.every(k => asm.variants[k].envelope && (asm.variants[k].polylines || []).length > 0),
        'its per-layer cut variants are fetched (as ARCHITECTURAL ENVELOPES) and drawn');
  check(JSON.stringify(layer) === designerBefore, 'the Designer document is untouched by the profile');

  // precision: Z in whole layers, positions in sensible steps
  const lh = asm.state.layer_height;
  const isLayer = z => Math.abs(z / lh - Math.round(z / lh)) < 1e-9;
  check(isLayer(P.z_bottom) && isLayer(P.height) && Number.isInteger(P.position), 'the default opening sits on whole layers and a whole inch');
  _asmRenderProfiles();
  const flat = n => [n, ...(n.children || []).flatMap(flat)];
  const inputs = flat(el('asm-profiles')).filter(n => n.dataset && typeof n.dataset.field === 'string');
  const inp = f => inputs.find(n => n.dataset.field === f);
  check(inp('position').step === '1' && inp('z_bottom').step === String(lh) && inp('upper_height').step === String(lh) &&
        inp('bottom_depth').step === String(lh) && !inp('height'),
        'Position steps 1 in; Bottom Z, Upper height and Lower depth step one layer; no separate Height (it is calculated)');
  check(P.upper === 'pointed' && P.lower === 'pointed' && !P.middle && P.upper_height === P.bottom_depth && !('shape' in P),
        'a new opening is the angular diamond CONFIGURATION (no Diamond / Arch type)');
  check(flat(el('asm-profiles')).some(n => n.textContent === 'Elevation' && n.title && /elevation/.test(n.title)),
        'the card has a labelled "Elevation" button (no cryptic ⊥)');
  await asmProfileSet(P.id, 'z_bottom', 7.3);
  check(asm.state.profiles[0].z_bottom === 7.5, `Bottom Z snaps to the layer height (7.3 → ${asm.state.profiles[0].z_bottom})`);
  await asmProfileSet(P.id, 'position', 61.0312345);
  check(asm.state.profiles[0].position === 61, 'typed positions keep no micro-inch precision (1/16 in)');
  await asmProfileSet(P.id, 'upper_height', 22.6);
  check(isLayer(asm.state.profiles[0].upper_height) && isLayer(asm.state.profiles[0].height), 'Upper height snaps to whole layers');
  await asmUndo(); await asmUndo(); await asmUndo();
  P = asm.state.profiles[0];
  check(P.position === 60 && rep(P.id).valid, 'undo restores the edited fields');

  // elevation focus: only the profiled wall at full strength
  const foc = _asmElevationFocus();
  check(foc && foc([200, 330]) && !foc([140, 140]), 'in the elevation the profiled wall is in focus, the room behind it is dimmed');

  // BIDIRECTIONAL CONSTRAINTS (2026-10-10): the edited dimension wins, the
  // other follows AT ONCE (fields, outline, per-layer cuts) — never an
  // over-steep "requested" shape that prints differently
  const h0 = _asmMaxOverhang();
  await asmProfileSet(P.id, 'width', 60);
  P = asm.state.profiles[0];
  r = rep(P.id);
  check(P.width === 60 && P.upper_height > 24 && P.height > 48 && P.driver === 'width' && r.valid && r.worst_step <= h0 + 1e-9,
        `a wider opening keeps its width; its upper height rose to the first printable one (${P.upper_height} in)`);
  await asmProfileSet(P.id, 'upper_height', 15);
  P = asm.state.profiles[0];
  r = rep(P.id);
  check(P.upper_height === 15 && P.width < 60 && P.driver === 'upper_height' && r.valid && r.worst_step <= h0 + 1e-9,
        `a lower upper keeps its height; the width shrank to the widest printable one (${P.width} in)`);
  check(asm.profNote && asm.profNote.id === P.id && /Width 60 → /.test(asm.profNote.text), `the dependent change is said on the card (${asm.profNote && asm.profNote.text})`);
  check(headers() === 0, 'it never turns into a header request');
  await asmUndo(); await asmUndo();
  check(rep(P.id).valid && asm.state.profiles[0].width !== 60, 'undo restores the opening (both dimensions, one step each)');
  await asmRedo();
  check(asm.state.profiles[0].width === 60 && asm.state.profiles[0].height > 48, 'redo re-applies the constrained edit');
  await asmUndo();

  // direct manipulation: drag the TOP handle up in the elevation = one step
  asmDrawPreview();
  P = asm.state.profiles[0];
  const t = _asmProfileTarget(P);
  const top0 = P.height;
  const [hx, hy] = asm.lastProj.P(..._asmWallPoint(t, P.position, P.z_bottom + P.height));
  check(asmProfileHandleAt(hx, hy) === 'top', 'the top handle is under its screen point');
  const u1 = undoN();
  asmProfileDragStart('top', hx, hy);
  asmProfileDragTo(hx, hy - 40);
  await asmProfileDragEnd();
  P = asm.state.profiles[0];
  check(P.height > top0 + 1 && undoN() === u1 + 1, `dragging the top handle raises the height (${top0} → ${P.height}) as one step`);
  check(isLayer(P.height), 'a dragged height lands on a whole layer');
  check(P.z_bottom + P.height <= 72 || (!rep(P.id).valid && rep(P.id).errors.some(e => /above the top of the stack/.test(e))),
        'an opening dragged past the top of the stack is reported (its closure would not print), never trimmed');
  await asmUndo();
  asmProfileDragStart('top', hx, hy);
  asmProfileDragTo(hx, hy - 6);
  await asmProfileDragEnd();
  P = asm.state.profiles[0];
  check(P.height > top0 && P.z_bottom + P.height <= 72 && rep(P.id).valid, `a smaller drag stays inside the stack (${P.height} in)`);
  {
    const [bx, by] = asm.lastProj.P(..._asmWallPoint(t, P.position, P.z_bottom + P.height / 2));
    asmProfileDragStart('body', bx, by);
    asmProfileDragTo(bx + 13.7, by - 9.1);
    await asmProfileDragEnd();
    P = asm.state.profiles[0];
    check(isLayer(P.z_bottom) && Math.abs(P.position * 4 - Math.round(P.position * 4)) < 1e-9 && P.position !== 60,
          `dragging the body moves it in ¼ in / whole layers (position ${P.position}, bottom Z ${P.z_bottom})`);
    await asmUndo();
    P = asm.state.profiles[0];
  }
  check(rep(P.id).valid, 'still self-supporting after the drag');

  // orbit away and back
  asmViewPreset('iso');
  check(asm.elevation.orbited && asm.view.pitch > 0, 'orbiting away is allowed');
  check(_asmElevationFocus() === null, 'orbited away: nothing is dimmed');
  asmViewPreset('elevation');
  check(!asm.elevation.orbited && asm.view.pitch === 0, '"Elevation" returns to the canonical view');

  // ---- EDIT → RESULT: the preview shows REAL resolved cut layers ----
  {
    const r0 = rep(P.id);
    const widest = r0.layers.reduce((a, L) => (L.width > a.width ? L : a));
    const inst = asm.resolved.instances[widest.layer];
    const ux = (t.b[0] - t.a[0]) / t.length, uy = (t.b[1] - t.a[1]) / t.length;
    const along = q => (q[0] - t.a[0]) * ux + (q[1] - t.a[1]) * uy;
    const across = q => Math.abs((q[0] - t.a[0]) * uy - (q[1] - t.a[1]) * ux);
    // (points every ½ in along each polyline: an envelope's straight wall
    // face has vertices at its ends only)
    const dense = p => { const P_ = p.closed ? [...p.pts, p.pts[0]] : p.pts, out = [];
      for (let k = 0; k + 1 < P_.length; k++) { const [a, b_] = [P_[k], P_[k + 1]], n = Math.max(1, Math.ceil(Math.hypot(b_[0] - a[0], b_[1] - a[1]) / 0.5));
        for (let j = 0; j <= n; j++) out.push([a[0] + (b_[0] - a[0]) * j / n, a[1] + (b_[1] - a[1]) * j / n]); }
      return out; };
    const inHole = polys => polys.flatMap(dense).filter(q => across(q) < 6 &&
      Math.abs(along(q) - P.position) < widest.width / 2 - 2);
    const layer = asmStack()[widest.layer];
    check((inst.print_key || inst.geometry_key).includes('opening') && asm.variants[inst.geometry_key] &&
          layer.polylines === asm.variants[inst.geometry_key].polylines,
          'the preview draws the layer\'s CUT VARIANT (the geometry fetched with its cut spec)');
    check(inHole(layer.polylines).length === 0 && inHole(asm.geometry.base.polylines).length > 0,
          'that resolved layer has a real through-wall gap where the canonical design is solid');
    const nOpen = layer.polylines.filter(p => p.pts.some(q => across(q) < 6)).length;
    const nBase = asm.geometry.base.polylines.filter(p => p.pts.some(q => across(q) < 6)).length;
    check(nOpen > nBase, `the wall is split into separate capped pieces (${nBase} → ${nOpen} strands)`);
    const zone = _asmRemovedZone(P, t, widest);
    check(zone([t.a[0] + ux * P.position, t.a[1] + uy * P.position]) && !zone([t.a[0] + ux * 5, t.a[1] + uy * 5]),
          'edit mode ghosts exactly the removed source geometry (inside the cut, not elsewhere)');
    _asmRenderProfiles();
    const card = () => flat(el('asm-profiles')).find(n => n.dataset && n.dataset.profile === P.id);
    const btnOf = a => flat(card()).find(n => n.dataset && n.dataset.action === a);
    check(card().dataset.mode === 'edit' && btnOf('done'), 'while editing: the card is in EDIT mode with a Done button');
    const yaw0 = asm.viewBeforeEdit && asm.viewBeforeEdit.yaw;
    const u = undoN();
    btnOf('done').onclick();
    check(asm.profSel === null && asm.elevation === null && undoN() === u && asm.view.yaw === yaw0 && asm.view.pitch > 0,
          'Done: edit mode left (not an undo step, nothing baked), the view the user had before editing is back');
    check(card().dataset.mode === 'result' && btnOf('edit') && !inputs.some(i => flat(card()).includes(i) && i.dataset.field === 'width') &&
          !flat(card()).some(n => n.dataset && typeof n.dataset.field === 'string'), 'result mode: a summary card with Edit (no construction fields)');
    check(_asmElevationFocus() === null && _asmProfile(P.id) && rep(P.id).valid && asmStack()[widest.layer].polylines === layer.polylines,
          'result mode: nothing dimmed, the profile is still a parametric Assembly object and the stack still prints its cut layers');
    btnOf('edit').onclick();
    check(asm.profSel === P.id && asm.elevation && asm.elevation.id === P.id && card().dataset.mode === 'edit',
          'Edit re-enters edit mode on the same profile');
  }

  // ---- 2. WALL TAPER ----
  asmProfileBegin('extent');
  asmProfileCreate('extent', R.id + '#0');
  check(asm.state.profiles.length === 1 && /OPEN wall/.test(asm.profError || ''), 'a rectangle edge cannot be tapered (V1): refused with a reason');
  await asmProfileCreate('extent', Ln.id);
  let Q = asm.state.profiles[1];
  check(Q && Q.kind === 'extent' && Q.keep === 'auto' && JSON.stringify(Q.knots) === '[[0,120],[72,0]]' &&
        rep(Q.id).keep === 'start' && rep(Q.id).valid,
        'taper created as a CUT LINE (a triangle) with Keep = Auto: the supported (left / start) side is kept');
  check(asm.elevation.id === Q.id && !asm.elevation.orbited, 'creating it snaps to its elevation');
  await asmProfileSet(Q.id, 's_lower', 60);
  await asmProfileSet(Q.id, 's_upper', 20);
  r = rep(Q.id);
  check(r && r.valid && r.layers.length === 48 && Math.abs(r.layers[47].retained - (60 - 40 * 71.25 / 72)) < 1e-3 &&
        r.layers[47].cut === r.layers[47].retained,
        `cut 60 → 20 in over Z 0–72: every layer shortened (top layer keeps ${r && r.layers[47].retained} in)`);
  // a SHORT LOCAL buttress taper: only the outer 20 in, between Z 24 and Z 60
  await asmProfileSet(Q.id, 's_lower', 120);
  await asmProfileSet(Q.id, 'z_lower', 24.4);
  await asmProfileSet(Q.id, 's_upper', 100);
  await asmProfileSet(Q.id, 'z_upper', 60);
  Q = asm.state.profiles[1];
  r = rep(Q.id);
  check(JSON.stringify(Q.knots) === '[[24,120],[60,100]]', 'the cut points are authoritative (Z snapped to whole layers)');
  check(r.valid && r.layers.every(L => L.retained >= 100 - 1e-9 && L.z > 24) && r.layers.length === 32,
        'only the outer 20 in are cut, from Z 24 up (the rest of the wall is never involved)');
  await asmProfileSet(Q.id, 'z_lower', 66);
  check(JSON.stringify(asm.state.profiles[1].knots) === '[[60,100],[66,120]]' && rep(Q.id).valid && rep(Q.id).keep === 'end',
        'typing the "lower" point above the upper one REORDERS them — the other diagonal; Auto now keeps the right / end side');
  await asmUndo();
  check(JSON.stringify(asm.state.profiles[1].knots) === '[[24,120],[60,100]]', 'undo restores the first diagonal');
  // drag the UPPER point in the elevation: distance + Z, one step
  asmProfileElevation(Q.id);
  asmDrawPreview();
  const tq = _asmProfileTarget(Q);
  const [ux, uy] = asm.lastProj.P(..._asmWallPoint(tq, 100, 60));
  check(asmProfileHandleAt(ux, uy) === 'hi', 'the upper cut point is a handle');
  const u3 = undoN();
  asmProfileDragStart('hi', ux, uy);
  asmProfileDragTo(ux - 25, uy - 15);
  await asmProfileDragEnd();
  Q = asm.state.profiles[1];
  check(undoN() === u3 + 1 && Q.knots[1][1] < 100 && Q.knots[1][0] > 60 && isLayer(Q.knots[1][0]),
        `dragging the upper point moves it along the wall and up (${Q.knots[1]}) as one step`);
  await asmUndo();
  check(JSON.stringify(asm.state.profiles[1].knots) === '[[24,120],[60,100]]', 'undo restores the cut line');
  await asmRedo(); await asmUndo();
  // the OTHER diagonal by dragging: the lower point dragged above the upper one swaps roles
  {
    asmDrawPreview();
    const [lx, ly] = asm.lastProj.P(..._asmWallPoint(tq, 120, 24));
    const [hx2, hy2] = asm.lastProj.P(..._asmWallPoint(tq, 120, 72));
    const u4 = undoN();
    asmProfileDragStart('lo', lx, ly);
    asmProfileDragTo(hx2, hy2);
    await asmProfileDragEnd();
    const K2 = asm.state.profiles[1].knots;
    check(undoN() === u4 + 1 && K2[0][0] === 60 && K2[1][0] === 72 && K2[0][1] === 100 && K2[1][1] === 120 &&
          rep(Q.id).valid && rep(Q.id).keep === 'end',
          `dragging a point past the other draws the opposite diagonal (${JSON.stringify(K2)}); Auto keeps the supported side`);
    await asmUndo();
  }
  await asmProfileSet(Q.id, 'keep', 'end');
  check(rep(Q.id).layers.every(L => Math.abs(L.retained - (120 - L.cut)) < 1e-6), 'Keep: End side — the start side disappears instead');
  await asmUndo();
  // the TRIANGLE: the same line with the other side kept overhangs (rejected, physically);
  // "Mirror cut" keeps the other END with the same retreating shape
  await asmProfileSet(Q.id, 's_lower', 120); await asmProfileSet(Q.id, 'z_lower', 0);
  await asmProfileSet(Q.id, 's_upper', 0); await asmProfileSet(Q.id, 'z_upper', 72);
  await asmProfileSet(Q.id, 'keep', 'start');
  check(rep(Q.id).valid, 'triangle, keep the start side: valid (retreats)');
  await asmProfileSet(Q.id, 'keep', 'end');
  check(!rep(Q.id).valid && /overhangs the layer below/.test(rep(Q.id).errors[0]) && /mirror/.test(rep(Q.id).errors[0]),
        'same line, the other side kept: a genuinely growing wall — rejected with a physical reason and the fix');
  await asmUndo();
  await asmProfileMirror(Q.id);
  Q = asm.state.profiles[1];
  check(Q.keep === 'end' && JSON.stringify(Q.knots) === '[[0,0],[72,120]]' && rep(Q.id).valid &&
        rep(Q.id).layers.every(L => Math.abs(L.retained - (120 - L.cut)) < 1e-6),
        'Mirror cut: the other end kept, the same retreating triangle (valid)');
  await asmUndo();
  check(asm.state.profiles[1].keep === 'start', 'mirror is one undo step');
  for (const [f, v] of [['s_lower', 120], ['z_lower', 24], ['s_upper', 100], ['z_upper', 60]]) await asmProfileSet(Q.id, f, v);
  check(headers() === 0, 'a retreating wall end never produces header findings');

  // ---- Wall Taper is no longer a tool: an older taper converts to a Planar Top Cut ----
  {
    asmProfileDone();
    _asmRenderProfiles();
    const flatP = n => [n, ...(n.children || []).flatMap(flatP)];
    check(!flatP(el('asm-profiles')).some(n => n.textContent === '+ Wall taper') &&
          flatP(el('asm-profiles')).some(n => n.textContent === '+ Planar top cut'),
          'the Profiles column offers Planar top cut — no separate Wall taper tool');
    const Qt = asm.state.profiles.find(q => q.kind === 'extent');
    await asmProfileSet(Qt.id, 's_lower', 120); await asmProfileSet(Qt.id, 'z_lower', 6);
    await asmProfileSet(Qt.id, 's_upper', 40); await asmProfileSet(Qt.id, 'z_upper', 66);
    const before = rep(Qt.id).layers.filter(L => L.z > 6 && L.z < 66).map(L => ({ z: L.z, cut: L.cut }));
    _asmRenderProfiles();
    const card = flatP(el('asm-profiles')).find(n => n.dataset && n.dataset.profile === Qt.id);
    check(card && card.dataset.mode === 'legacy' && flatP(card).some(n => n.dataset && n.dataset.action === 'convert'),
          'an older Wall taper shows as a legacy card with "Convert to planar top cut"');
    const tq = _asmProfileTarget(Qt);
    const u7 = undoN();
    await asmTaperToPlane(Qt.id);
    const PLt = _asmProfile(Qt.id);
    const L_ = tq.length, at = sv => [tq.a[0] + (tq.b[0] - tq.a[0]) * sv / L_, tq.a[1] + (tq.b[1] - tq.a[1]) * sv / L_];
    check(PLt.kind === 'plane' && PLt.sources.join() === Ln.id && undoN() === u7 + 1 && rep(Qt.id).valid,
          'converted (one step): a Planar Top Cut on that one wall');
    check(before.length > 10 && before.every(b => Math.abs(_asmPlaneZ(PLt, ...at(b.cut)) - b.z) < 1e-6),
          'the plane passes through every cut point the taper had (the same cut line)');
    await asmUndo();
    check(_asmProfile(Qt.id).kind === 'extent', 'undo restores the taper');
    await asmRedo();
  }

  // ---- 3. PLANAR TOP CUT: plan view, selection, uphill arrow, linked plane fields ----
  {
    asmProfileDone();
    const nBefore = asm.state.profiles.length;
    const u5 = undoN();
    await asmPlaneCreate();
    let PL = asm.state.profiles[asm.state.profiles.length - 1];
    check(PL && PL.kind === 'plane' && asm.profSel === PL.id && undoN() === u5 + 1 && Math.abs(asm.view.pitch - Math.PI / 2) < 1e-9,
          'planar top cut created as one step, in EDIT mode, the preview in PLAN view (bird\'s-eye)');
    const srcs = asm.resolved.profile_sources.base;
    const rWs = srcs.find(r => r.source === R.id).system;
    check(srcs.length === 2 && rWs && srcs.every(r => r.points.length >= 2), 'the backend lists every selectable wall with its Wall System');
    await asmPlaneToggleSystem(PL.id, rWs);
    PL = _asmProfile(PL.id);
    check(PL.systems.includes(rWs) && _asmPlaneSel(PL, 'base').has(R.id) && !_asmPlaneSel(PL, 'base').has(Ln.id),
          'choosing a Wall System selects all its members (the line is not in it)');
    check(PL.slope > 0 && rep(PL.id).valid && rep(PL.id).layers.length > 0,
          `the first selection gets a sloped plane across it (${rep(PL.id).layers.length} layers cut)`);
    // click the line in the plan view to add it
    asmDrawPreview();
    const lp = asm.lastProj.P(200, 330, 0);
    check(asmPlaneWallAt(lp[0], lp[1]) === Ln.id, 'in plan view the line is under its screen point');
    check(asmPlaneMouseDown(lp[0], lp[1]) === true, 'a click on it toggles it');
    await new Promise(r => setTimeout(r, 300));
    for (let i = 0; i < 40 && !(_asmProfile(PL.id).sources || []).includes(Ln.id); i++) await new Promise(r => setTimeout(r, 100));
    await asmResolve();
    PL = _asmProfile(PL.id);
    check(PL.sources.includes(Ln.id) && _asmPlaneSel(PL, 'base').has(Ln.id), 'the line is added to the selection');
    // linked fields: High / Low ↔ slope ↔ rise / run are one plane
    let hl = _asmPlaneHL(PL);
    await asmPlaneSet(PL.id, 'high', 70.4);
    PL = _asmProfile(PL.id); hl = _asmPlaneHL(PL);
    check(Math.abs(hl.high - 70.5) < 1e-6 && Math.abs(PL.slope - (hl.high - hl.low) / hl.run) < 1e-6,
          `High snaps to whole layers (70.4 → ${_fmt(hl.high)}) and the slope follows (${PL.slope})`);
    const pivBefore = _asmPlaneContour(PL).pivot;
    await asmPlaneSet(PL.id, 'angle', 10);
    PL = _asmProfile(PL.id); hl = _asmPlaneHL(PL);
    check(Math.abs(Math.atan(PL.slope) * 180 / Math.PI - 10) < 1e-3 && Math.abs(_asmPlaneZ(PL, ...pivBefore) - _asmStackTop()) < 1e-6,
          'Slope 10° turns the plane about its cut-begins line (where it meets the stack top stays put)');
    await asmPlaneSet(PL.id, 'rise', 0.2);
    PL = _asmProfile(PL.id); hl = _asmPlaneHL(PL);
    check(Math.abs(PL.slope - 0.2) < 1e-6 && Math.abs(hl.high - hl.low - 0.2 * hl.run) < 1e-6, 'Rise / run 0.2 is that same plane');
    // FLIP: uphill reversed about the cut-begins line, the same slope
    const dir0 = PL.direction, sl0 = PL.slope, pv0 = _asmPlaneContour(PL).pivot;
    await asmPlaneFlip(PL.id);
    PL = _asmProfile(PL.id); hl = _asmPlaneHL(PL);
    check(Math.abs(((PL.direction - dir0 + 360) % 360) - 180) < 1e-9 && Math.abs(PL.slope - sl0) < 1e-9 &&
          Math.abs(_asmPlaneZ(PL, ...pv0) - _asmStackTop()) < 1e-6 && rep(PL.id).valid,
          'Flip slope: uphill reversed, the same slope, about the same cut-begins line');
    await asmUndo();
    PL = _asmProfile(PL.id);
    check(PL.direction === dir0, 'undo restores the direction');
    await asmRedo(); await asmUndo();
    // drag the uphill arrow (5° steps)
    asmPlaneView(PL.id, 'plan'); asmDrawPreview();
    const tip = asm.lastProj.P(..._asmPlaneArrow(PL)[1], 0);
    const u6 = undoN();
    check(asmPlaneMouseDown(tip[0], tip[1]) === true && asm.drag && asm.drag.plane === 'direction', 'the arrow tip is a handle');
    const tail = asm.lastProj.P(..._asmPlaneArrow(PL)[0], 0);
    asmPlaneDragTo(tail[0], tail[1] - 120);                // straight up on screen
    await asmPlaneDragEnd();
    PL = _asmProfile(PL.id);
    check(undoN() === u6 + 1 && PL.direction % 5 === 0 && PL.direction !== dir0, `dragging the arrow turns the uphill direction (${PL.direction}°) as one step`);
    // the cut is REAL resolved geometry: material of the selected walls only up to the plane
    const cutL = rep(PL.id).layers.find(L => !L.removed_all);
    const inst = asm.resolved.instances[cutL.layer];
    const layer = asmStack()[cutL.layer];
    check((inst.print_key || inst.geometry_key).includes('plane') && layer.polylines === asm.variants[inst.geometry_key].polylines &&
          JSON.stringify(layer.polylines) !== JSON.stringify(asm.geometry.base.polylines),
          'the preview draws that layer\'s CUT VARIANT (fetched with its plane cut)');
    const zAbove = layer.polylines.flatMap(pl => pl.pts).filter(q => _asmPlaneZ(PL, ...q) < cutL.z - 12);
    check(zAbove.length === 0, 'no printed vertex lies far above the plane (beyond a cut face)');
    // PLAN POSITION is independent: translate (field + drag) keeps the slope; rotate keeps the slope
    const slope0 = PL.slope, ct0 = _asmPlaneContour(PL);
    await asmPlaneSet(PL.id, 'shift', ct0.s + 30.3);
    PL = _asmProfile(PL.id);
    check(Math.abs(PL.slope - slope0) < 1e-9 && Math.abs(_asmPlaneContour(PL).s - (ct0.s + 30.3125)) < 1e-6,
          'Cut begins +30 in: the plane moves in plan (1/16 in), its slope unchanged');
    asmPlaneView(PL.id, 'plan'); asmDrawPreview();
    const pv = asm.lastProj.P(..._asmPlaneArrow(PL)[0], _asmStackTop());
    const s1 = _asmPlaneContour(PL).s;
    check(asmPlaneMouseDown(pv[0], pv[1]) === true && asm.drag.plane === 'move', 'the cut-begins handle moves the plane');
    asmPlaneDragTo(pv[0] + 25, pv[1] + 25);
    await asmPlaneDragEnd();
    PL = _asmProfile(PL.id);
    check(Math.abs(PL.slope - slope0) < 1e-9 && Math.abs(_asmPlaneContour(PL).s - s1) > 0.2,
          `dragging it translates the plane perpendicular to itself (${_fmt(s1)} → ${_fmt(_asmPlaneContour(PL).s)} in), slope kept`);
    {
      // RESPONSIVE EDITING (2026-10-08): a drag moves the plane at once and resolves only
      // at its end; meanwhile the stack is a live PREVIEW (uncut layers clipped by the plane
      // as it is now); quick successive edits resolve only the latest state
      const nRes = () => calls.filter(u => u === '/api/assembly/resolve').length;
      const n0 = nRes();
      const pv2 = asm.lastProj.P(..._asmPlaneArrow(PL)[0], _asmStackTop());
      const sA = _asmPlaneContour(PL).s;
      check(asmPlaneMouseDown(pv2[0], pv2[1]) === true, 'drag again');
      for (let k = 1; k <= 6; k++) asmPlaneDragTo(pv2[0] + 5 * k, pv2[1] + 5 * k);
      check(nRes() === n0 && Math.abs(_asmPlaneContour(_asmProfile(PL.id)).s - sA) > 0.2,
            'a drag moves the plane at once (6 pointer moves) and resolves nothing while moving');
      check(!!_asmPlanePreview(), 'while the plane differs from the resolved result the stack is a live PREVIEW');
      const st = asmStack(), top = st[st.length - 1];
      const plen = pls => pls.reduce((a, q) => a + q.pts.slice(1).reduce((b, v, i) => b + Math.hypot(v[0] - q.pts[i][0], v[1] - q.pts[i][1]), 0)
                                                 + (q.closed && q.pts.length > 1 ? Math.hypot(q.pts[0][0] - q.pts[q.pts.length - 1][0], q.pts[0][1] - q.pts[q.pts.length - 1][1]) : 0), 0);
      check(plen(top.polylines) < plen(asm.geometry[top.design_id].polylines) - 1,
            'the preview\'s top layer is the uncut layer with the live plane\'s removed segments left out');
      await asmPlaneDragEnd();
      check(nRes() === n0 + 1 && !_asmPlanePreview() && asm.resolvedPlanes === _asmPlanesKey(),
            'ONE resolve at the end of the drag; the exact result replaces the preview');
      const n1 = nRes(), s0 = _asmPlaneContour(_asmProfile(PL.id)).s;
      const ps = [1, 2, 3].map(k => asmPlaneSet(PL.id, 'shift', s0 + k));
      await Promise.all(ps);
      check(nRes() - n1 <= 2 && asm.resolvedPlanes === _asmPlanesKey() &&
            Math.abs(_asmPlaneContour(_asmProfile(PL.id)).s - (s0 + 3)) < 1e-6,
            `three quick edits: ${nRes() - n1} resolve(s) (superseded states skipped), the LATEST shown`);
      PL = _asmProfile(PL.id);
    }
    const piv0 = _asmPlaneContour(PL).pivot;
    await asmPlaneSet(PL.id, 'direction', (PL.direction + 40) % 360);
    PL = _asmProfile(PL.id);
    const piv1 = _asmPlaneContour(PL).pivot;
    check(Math.abs(PL.slope - slope0) < 1e-9 && Math.abs(_asmPlaneZ(PL, ...piv0) - _asmStackTop()) < 1e-6,
          'turning the plane keeps its slope and turns about the cut-begins point (not re-spanned over the footprint)');
    // a SECOND plane on the same walls composes (each its own selection / position / slope)
    await asmPlaneCreate();
    let P2 = asm.state.profiles[asm.state.profiles.length - 1];
    await asmPlaneToggleSource(P2.id, R.id);
    P2 = _asmProfile(P2.id);
    check(rep(PL.id).valid && rep(P2.id).valid && asm.state.profiles.filter(q => q.kind === 'plane').length >= 2,
          'a second planar cut on the same wall: both valid, each with its own selection');
    const both = asm.resolved.instances.find(i => ((i.print_key || i.geometry_key).match(/"plane"/g) || []).length === 2);
    check(!!both, 'some layers carry BOTH plane cuts (composed subtractively by the Designer)');
    // (2026-10-15) quick selection: All walls / None — explicit wall ids, no group
    const u7 = undoN();
    await asmPlaneSelectAll(P2.id, true);
    check([R.id, Ln.id].every(w => _asmProfile(P2.id).sources.includes(w)) && !_asmProfile(P2.id).systems.length && undoN() === u7 + 1,
          'All walls: every wall selected explicitly (one undo step)');
    await asmPlaneSelectAll(P2.id, false);
    check(!_asmProfile(P2.id).sources.length && !_asmPlaneSel(_asmProfile(P2.id), 'base').size, 'None: the selection cleared');
    await asmProfileDelete(P2.id);
    check(rep(PL.id).valid && !asm.state.profiles.some(q => q.id === P2.id), 'deleting one plane leaves the other');
    asmProfileEdit(PL.id);

    // ---- 3a. the REMOVED-MATERIAL GHOST is clipped exactly at each layer's cut
    // (regression 2026-10-15: segments were judged whole by their midpoint, so a
    // long envelope segment straddling the cut — an out-and-back wall's side, a
    // rectangle edge — was ghosted far past the cut, over material still printed)
    {
      const checkGhost = (P_, label) => {
        let zc = 0; const segs = [], cur = [];
        const rec = { moveTo(x, y) { cur.length = 0; cur.push([x, y, zc]); }, lineTo(x, y) { segs.push([cur[0], [x, y, zc]]); },
                      beginPath() {}, stroke() {}, setLineDash() {}, set strokeStyle(v) {}, set lineWidth(v) {}, set globalAlpha(v) {} };
        _asmDrawPlaneGhost(rec, (x, y, z) => { zc = z; return [x, y]; }, asmStack(), P_);
        const [dx, dy] = _asmPlaneDir(P_), h = asm.state.layer_height;
        const bad = segs.filter(([A, B]) => [A, B].some(([x, y, zt]) => {
          const tc = _asmPlaneCutT(P_, zt - h / 2);
          if (tc === 'none') return false;
          if (tc === 'all') return true;
          return (x - P_.anchor[0]) * dx + (y - P_.anchor[1]) * dy > tc + 1e-6;
        }));
        const straddle = segs.filter(([A, B]) => Math.hypot(B[0] - A[0], B[1] - A[1]) > 0).length;
        check(segs.length > 0 && bad.length === 0,
              `${label}: every ghost segment lies on the removed side of its layer's cut (${segs.length} segments, ${bad.length} over kept material)`);
        return straddle;
      };
      checkGhost(PL, 'linear plane ghost');
      // the clip itself: a segment straddling the cut is split at the cut, not taken whole
      const zMid = rep(PL.id).layers.find(L => !L.removed_all).z, tc = _asmPlaneCutT(PL, zMid), [dx, dy] = _asmPlaneDir(PL);
      const base = [PL.anchor[0] + dx * tc, PL.anchor[1] + dy * tc];
      const r = _asmPlaneRemovedF(PL)(base[0] - dx * 30, base[1] - dy * 30, base[0] + dx * 10, base[1] + dy * 10, zMid);
      check(r && Math.abs(r[0]) < 1e-12 && Math.abs(r[1] - 0.75) < 1e-9,
            'a 40 in segment with 30 in on the removed side is removed over exactly its first 75 % (the midpoint rule removed all of it)');
    }

    // ---- 3b. PROFILE TYPES (2026-10-15): Linear | Stepped | Curved ----
    {
      const offs = id => rep(id).layers.filter(L => !L.removed_all).map(L => [L.z, L.offset]);
      const linOffs = JSON.stringify(offs(PL.id));
      const hl0 = _asmPlaneHL(PL), u0 = undoN();
      check((PL.shape || 'linear') === 'linear', 'a plane is Linear by default');
      await asmPlaneSet(PL.id, 'shape', 'stepped');
      PL = _asmProfile(PL.id);
      check(PL.shape === 'stepped' && undoN() === u0 + 1 && rep(PL.id).valid, 'Stepped: one undo step, valid');
      check(Math.abs(PL.span - hl0.run) < 1e-6 && Math.abs(PL.z_anchor - hl0.low) < 1e-6,
            'its start / end are the footprint\'s downhill / uphill extremes on the same chord (Z start / Z end)');
      const st = _asmPlaneSteps(PL);
      const offS = offs(PL.id);
      check(offS.length > 0 && offS.every(([, t]) => Math.abs(t - st.tread * Math.round(t / st.tread)) < 1e-3),
            `every resolved layer cut lies on a riser line (${st.n} risers, treads of ${_fmt(st.tread)} in)`);
      check(offS.every(([z, t]) => Math.abs(_asmPlaneCutT(PL, z) - t) < 1e-3), 'the page\'s mirror gives the server\'s cut for every layer');
      const txt = () => flat(el('asm-profiles')).map(n => n.textContent || '').join(' | ');
      asmRender();
      check(/lower landing \+ \d+ risers? \+ upper landing · tread/.test(txt()) && /Stepped/.test(txt()) && flat(el('asm-profiles')).some(n => n.dataset && n.dataset.shape === 'curved'),
            'the card shows the type buttons and the stair (steps · tread · riser ≈ layers)');
      await asmPlaneSet(PL.id, 'step_run', 18);
      PL = _asmProfile(PL.id);
      check(PL.step_run === 18 && rep(PL.id).valid && _asmPlaneSteps(PL).treads === Math.max(2, Math.round(PL.span / 18)),
            'Step run 18 in: a whole number of treads over the span');
      asmPlaneView(PL.id, 'slope'); asmDrawPreview();
      // CURVED
      await asmPlaneSet(PL.id, 'shape', 'curved');
      PL = _asmProfile(PL.id);
      check(PL.shape === 'curved' && JSON.stringify(PL.control) === '[0.5,0.5]' && rep(PL.id).valid,
            'Curved starts on the chord (control 50 % / 50 %)');
      const chord = offs(PL.id);
      await asmPlaneSet(PL.id, 'cu', 80); await asmPlaneSet(PL.id, 'cv', 20);
      PL = _asmProfile(PL.id);
      const bow = offs(PL.id);
      check(JSON.stringify(PL.control) === '[0.8,0.2]' && bow.length >= chord.length &&
            bow.filter(([z, t]) => { const c = chord.find(q => q[0] === z); return c && t > c[1] + 1e-6; }).length > 0,
            'control 80 % / 20 %: the curve starts gentle — more is removed than on the chord at mid-height');
      check(bow.every(([z, t]) => Math.abs(_asmPlaneCutT(PL, z) - t) < 1e-3), 'the mirror matches the server for the curve too');
      // drag the control point in Slope view
      asmPlaneView(PL.id, 'slope'); asmDrawPreview();
      const cp = asm.lastProj.P(..._asmPlaneControlW(PL));
      const u1 = undoN();
      check(asmPlaneMouseDown(cp[0], cp[1]) === true && asm.drag.plane === 'control', 'the curve\'s control point is a handle (Slope view)');
      asmPlaneDragTo(cp[0], cp[1] - 400);                 // far up: clamped inside the start–end box
      check(_asmProfile(PL.id).control[1] === 1 && _asmProfile(PL.id).control[0] === 0.8, 'dragged up: the rise fraction is clamped at 100 % (monotonic curve)');
      await asmPlaneDragEnd();
      PL = _asmProfile(PL.id);
      check(undoN() === u1 + 1 && rep(PL.id).valid, 'the drag is ONE undo step, resolved at its end');
      asmDrawPreview();                                    // (the resolved silhouette + intended curve draw)
      // back to Linear: the plane it was
      await asmPlaneSet(PL.id, 'shape', 'linear');
      PL = _asmProfile(PL.id);
      const back = offs(PL.id);
      const lin0 = JSON.parse(linOffs);
      check(back.length === lin0.length && back.every(([z, t], k) => Math.abs(z - lin0[k][0]) < 1e-9 && Math.abs(t - lin0[k][1] - (back[0][1] - lin0[0][1])) < 1e-6),
            'Linear again: the same plane (the same cut at every layer, measured from its start)');
      const saved2 = JSON.stringify(asm.state);
      for (let k = 0; k < 7; k++) await asmUndo();                // shape · step run · shape · cu · cv · drag · shape
      check(_asmProfile(PL.id).shape === 'linear' || !_asmProfile(PL.id).shape, 'undo walks back to the linear plane');
      asm.state = JSON.parse(saved2); await asmResolve();
      PL = _asmProfile(PL.id);
    }

    // Done → result: no overlay; persistence
    asmProfileDone();
    check(asm.profSel === null && asm.elevation === null, 'Done: the resolved result, the plane stays parametric');
    const saved = JSON.stringify(asm.state);
    asm.state = JSON.parse(saved); await asmResolve();
    check(rep(PL.id) && rep(PL.id).valid && _asmProfile(PL.id).kind === 'plane', 'the plane survives a JSON round trip of the Assembly state');
    await asmProfileDelete(PL.id);
    check(asm.state.profiles.length === nBefore, 'deleted');
    await asmUndo();
    check(asm.state.profiles.length === nBefore + 1, 'undo brings it back');
    await asmProfileDelete(PL.id);
  }

  // Support column: only while there is something to show
  const cls = new Set();
  el('ws-assembly').classList = { toggle(c, on) { if (on) cls.add(c); else cls.delete(c); }, add() {}, remove() {} };
  asmRender();
  check(!cls.has('asm-has-support') && /every layer supported/.test(flat(el('asm-support-summary')).map(n => n.textContent || '').join(' ')),
        'no findings: no Support column, a one-line summary in Layers');
  const keepRes = asm.resolved.support;
  asm.resolved.support = { ...keepRes, findings: [{ id: 'x', status: 'needs_header', kind: 'bridge', layer: 3, z: 6,
                                                   section: 0, span: 30, centre: [200, 330], coverage: 0, runs: [] }], groups: null };
  asmRender();
  check(cls.has('asm-has-support') && /1 open finding/.test(flat(el('asm-support-summary')).map(n => n.textContent || '').join(' ')),
        'a finding opens the Support column and the summary points to it');
  asm.resolved.support = keepRes;
  asmRender();

  // ---- persistence: the state round-trips through JSON and resolves identically ----
  const saved = JSON.stringify(asm.state);
  const before = JSON.stringify(asm.resolved.profiles);
  asm.state = JSON.parse(saved);
  await asmResolve();
  check(JSON.stringify(asm.resolved.profiles) === before, 'profiles survive a JSON round trip of the Assembly state');

  // delete + undo
  const u2 = undoN();
  await asmProfileDelete(Q.id);
  check(asm.state.profiles.length === 1 && undoN() === u2 + 1, 'delete is one step');
  await asmUndo();
  check(asm.state.profiles.length === 2 && rep(Q.id).valid, 'undo brings the taper back');
  _asmRenderProfiles();
  check(el('asm-profiles').children.filter(c => c.dataset && typeof c.dataset.profile === 'string').length === 2, 'both profiles are listed');

  // ---- 4. ARRAYS + ARCH SHAPES (one parametric profile → identical clones) ----
  const ua = undoN();
  await asmProfileCreate('opening', R.id + '#2');
  const A = asm.state.profiles[asm.state.profiles.length - 1];
  await asmProfileSet(A.id, 'lower', 'flat'); await asmProfileSet(A.id, 'middle', true);
  check(!('shape' in A) && A.upper === 'pointed' && A.lower === 'flat' && A.middle === true && A.upper_height > 0 && !('closure' in A),
        'Lower → Flat + Middle on: an arch-like configuration of the same opening (no closure parameter)');
  await asmProfileSet(A.id, 'width', 12); await asmProfileSet(A.id, 'upper_height', 9); await asmProfileSet(A.id, 'middle_height', 12);
  await asmProfileSet(A.id, 'position', 20); await asmProfileSet(A.id, 'z_bottom', 6);
  await asmProfileSet(A.id, 'array.arrangement', 'grid');
  check(A.array && A.array.arrangement === 'grid' && A.array.cols === 3 && A.array.rows === 2, 'Array → Grid: 3 × 2 by default');
  await asmProfileSet(A.id, 'array.h_spacing', 18);
  await asmProfileSet(A.id, 'array.v_spacing', 3);          // (the upper row closes below the stack top)
  let ra = rep(A.id);
  check(ra.valid && ra.members.length === 6 && ra.geometry.upper_height > 0, `a valid 3 × 2 arch grid (upper ${ra.geometry.upper_height} in)`);
  const xs = [...new Set(ra.members.map(m => m.position))].sort((a, b) => a - b);
  check(xs.join() === '20,50,80', 'columns one width + the CLEAR spacing apart (12 + 18)');
  const k0 = ra.layers.find(L => L.width === 12).layer;
  const ops = JSON.parse((asm.resolved.instances[k0].print_key || asm.resolved.instances[k0].geometry_key).split('|cut:')[1]).filter(c => c[0] === 'opening');
  check(ops.length === 3 && ops.every(c => c[5] === A.id), 'one cut per clone in its layer, all named by the ONE profile');
  _asmRenderProfiles();
  const flat2 = n => [n, ...(n.children || []).flatMap(flat2)];
  const f2 = flat2(el('asm-profiles')).filter(n => n.dataset && typeof n.dataset.field === 'string').map(n => n.dataset.field);
  check(['array.cols', 'array.rows', 'array.h_spacing', 'array.v_spacing', 'upper_height', 'middle_height'].every(f => f2.includes(f)) &&
        !f2.includes('array.count') && !f2.includes('closure') && !f2.includes('bottom_depth') && !f2.includes('height'),
        'the card shows the grid fields and the arch components (no closure; no lower depth for a flat sill; total height calculated)');
  await asmProfileSet(A.id, 'width', 14);
  ra = rep(A.id);
  check(ra.valid && Math.max(...ra.layers.map(L => L.width)) === 14 && [...new Set(ra.members.map(m => m.position))].join() === '20,52,84',
        'editing the master updates every opening');
  await asmProfileSet(A.id, 'array.h_spacing', 1);
  check(!rep(A.id).valid && rep(A.id).errors.some(e => /of wall between/.test(e)), 'too little wall between openings: refused with the reason');
  await asmUndo();
  check(rep(A.id).valid && _asmProfile(A.id).array.h_spacing === 18, 'undo restores the spacing');
  const sv = JSON.stringify(asm.state);
  asm.state = JSON.parse(sv);
  await asmResolve();
  check(rep(A.id).valid && rep(A.id).members.length === 6, 'the array survives a JSON round trip of the Assembly state');
  await asmProfileDone();
  while (undoN() > ua) await asmUndo();
  check(!asm.state.profiles.some(p => p.id === A.id), 'undo back to before the array');

  // ---- 5. CONVERT a Designer opening that needs a header ----
  el('asm-new-name').value = 'Door Gap'; el('asm-new-parent').value = 'base';
  const d1 = asmNewDerived();
  await editDesign(d1);
  layer.openings.push({ id: 'door', source_path_id: R.id, center_s: 60, width: 30, end_treatment: 'inherit',
                        z_min: null, z_max: null, label: 'Door' });
  historyCheckpoint();
  await editDesign('base');
  asm.state.sections = [{ design_id: 'base', height: 36 }, { design_id: d1, height: 24 }, { design_id: 'base', height: 36 }];
  asm.state.profiles = [];
  await asmResolve();
  const lint = () => (asm.resolved.support.findings || []).filter(f => f.status === 'needs_header' && f.opening && f.opening.id === 'door');
  check(lint().length === 1 && lint()[0].opening.convertible && lint()[0].opening.at === 60, 'the lintel finding names the Designer opening (convertible)');
  _asmRenderSupport();
  check(flat2(el('asm-support')).some(n => n.dataset && n.dataset.action === 'convert-opening' && /Convert to Self-Supporting Opening/.test(n.textContent)),
        '"Convert to Self-Supporting Opening" beside Add Header');
  const uc = undoN();
  await asmConvertOpening(lint()[0].id);
  const C = asm.state.profiles[0];
  const rc = rep(C.id);
  check(undoN() === uc + 1 && C.source_opening.id === 'door' && rc.valid && rc.conversion.width === 30 &&
        rc.conversion.rect_bottom === 36 && rc.conversion.rect_top === 60, 'one undo step: an Assembly profile over the door (width / clear height kept)');
  check(lint().length === 0 && headers() === 0, 'the converted door needs no header: its resolved closure is supported');
  check(!asm.resolved.instances.slice(24, 40).some(i => /"opening"/.test(i.print_key || i.geometry_key || '')), 'the door\'s own layers are not cut a second time');
  check(project.deltas[d1].patch.openings.door.width === 30, 'the Designer opening itself is unchanged');
  await asmUndo();
  check(asm.state.profiles.length === 0 && lint().length === 1, 'removing the conversion restores the header finding');
  await asmRedo();
  check(rep(C.id).valid && lint().length === 0, 'redo converts again');


  // ---- 6. SHAPES · CONSTRAINTS · PREVIEW = RESULT (2026-10-10) ----
  asm.state.sections = [{ design_id: 'base', height: 120 }];
  asm.state.profiles = [];
  await asmResolve();
  const hh = () => Math.min(_asmMaxOverhang(), window.Designer.beadWidth());
  await asmProfileCreate('opening', Ln.id);
  let S = asm.state.profiles[0];
  // the edit preview's per-layer cuts (drawn at once, before any resolve) = the resolved layers
  const same = id => {
    const js = _asmOpeningCuts(_asmProfile(id)), be = rep(id).layers.filter(L => !L.designer);
    return js.length > 0 && js.length === be.length &&
      js.every(L => be.some(B => B.layer === L.layer && Math.abs(B.at - L.at) < 1e-3 && Math.abs(B.width - L.width) < 1e-3));
  };
  check(rep(S.id).valid && same(S.id), 'a new opening: printable, and the preview draws exactly the resolved per-layer cuts');
  const geomSame = id => {           // the resolved components = the ones the card / preview use
    const g = rep(id).geometry, G = _asmOpeningGeom(_asmProfile(id));
    return Math.abs(g.upper_height - G.U) < 1e-3 && Math.abs(g.lower_depth - G.d) < 1e-3 &&
           Math.abs(g.middle - G.m) < 1e-3 && Math.abs(g.height - G.H) < 1e-3;
  };
  const combos = [];
  for (const up of ['pointed', 'curve', 'header']) for (const lo of ['flat', 'pointed', 'arc', 'round']) for (const mid of [true, false])
    if (!(up === 'header' && lo === 'flat' && !mid)) combos.push([up, lo, mid]);
  for (const [up, lo, mid] of combos) {
    await asmProfileSet(S.id, 'upper', up); await asmProfileSet(S.id, 'lower', lo); await asmProfileSet(S.id, 'middle', mid);
    S = _asmProfile(S.id);
    const rr = rep(S.id);
    check(S.upper === up && S.lower === lo && !!S.middle === mid && rr.valid && rr.worst_step <= hh() + 1e-9 && same(S.id) && geomSame(S.id),
          `${up} upper · ${mid ? 'middle · ' : ''}${lo} lower: printable (${S.width} × ${S.height} in), preview = result`);
  }
  _asmRenderProfiles();
  const fl6 = n => [n, ...(n.children || []).flatMap(fl6)];
  const typeOpts = fl6(el('asm-profiles')).filter(n => ['diamond', 'arch', 'lens'].includes(n.value));
  check(typeOpts.length === 0 && !fl6(el('asm-profiles')).some(n => n.textContent === 'Type' || n.textContent === 'Profile'),
        'no Diamond / Arch selector: Upper, Middle and Lower only');
  // both diamonds are CONFIGURATIONS (named on the card)
  for (const [up, lo, name] of [['pointed', 'pointed', 'angular diamond'], ['curve', 'arc', 'curved diamond']]) {
    await asmProfileSet(S.id, 'middle', false); await asmProfileSet(S.id, 'upper', up); await asmProfileSet(S.id, 'lower', lo);
    await asmProfileSet(S.id, 'upper_height', 24); await asmProfileSet(S.id, 'bottom_depth', 24);
    S = _asmProfile(S.id);
    _asmRenderProfiles();
    const lab = fl6(el('asm-profiles')).find(n => n.className === 'asm-calc asm-config');
    check(S.upper_height === S.bottom_depth && rep(S.id).valid && same(S.id) && lab && lab.textContent.includes(name),
          `${up} upper + ${lo} lower + no middle, equal heights = ${lab && lab.textContent}`);
  }
  {
    const heads = fl6(el('asm-profiles')).filter(n => n.className === 'asm-subhead').map(n => n.textContent);
    check(['Shape', 'Position', 'Dimensions', 'Array'].every(h => heads.includes(h)), `the card is organised under SHAPE / POSITION / DIMENSIONS / ARRAY (${heads.join(', ')})`);
  }
  // the ARCH components: genuine curves, independent, the total calculated
  {
    _asmRenderProfiles();
    const lowSel = fl6(el('asm-profiles')).find(n => n.tagName === 'select' || (n.children || []).some(c => c.value === 'round'));
    const opts = fl6(el('asm-profiles')).filter(n => ['flat', 'pointed', 'arc', 'round'].includes(n.value) && n.textContent)
      .map(n => n.textContent);
    check(lowSel && ['Flat', 'Angular', 'Curved', 'Rounded'].every(o => opts.includes(o)), `Lower offers Flat / Angular / Curved / Rounded (${opts.join(', ')})`);
  }
  // CURVED lower = the vesica's lower half: a SHARP point, deeper than half the width
  await asmProfileSet(S.id, 'upper', 'curve'); await asmProfileSet(S.id, 'lower', 'arc'); await asmProfileSet(S.id, 'middle', true);
  S = _asmProfile(S.id);
  {
    const G = _asmOpeningGeom(S), O = window.OpeningShape, e = 1e-4;
    const w = t => O.widthAt(t, G.W, G.d, G.m, G.U, S.upper, S.lower);
    const a = (G.d * G.d - G.W * G.W / 4) / G.W;
    check(G.d > G.W / 2 && Math.abs((w(e) / 2) / e - G.d / a) < 1e-2 * (G.d / a) && (G.W - w(G.d - e)) / e < 0.1 &&
          rep(S.id).valid && same(S.id),
          `Curved lower: two arcs meeting at a sharp bottom point (depth ${G.d} > W/2), vertical at the sides, preview = result`);
    await asmProfileSet(S.id, 'bottom_depth', 3);
    S = _asmProfile(S.id);
    check(S.bottom_depth > S.width / 2 && asm.profNote && /Lower depth 3 → /.test(asm.profNote.text),
          `a Curved lower too shallow to stay pointed is deepened (${asm.profNote && asm.profNote.text})`);
    // ARRAY of Curved-bottom arches: identical clones
    const keep = { position: S.position, width: S.width };
    await asmProfileSet(S.id, 'position', 30); await asmProfileSet(S.id, 'width', 16);
    await asmProfileSet(S.id, 'array.arrangement', 'horizontal'); await asmProfileSet(S.id, 'array.count', 2);
    await asmProfileSet(S.id, 'array.h_spacing', 20);
    const per = {};
    for (const L of rep(S.id).layers) (per[L.at] = per[L.at] || []).push(L.width.toFixed(4));
    check(rep(S.id).valid && Object.keys(per).length === 2 && new Set(Object.values(per).map(v => v.join())).size === 1 && same(S.id),
          'an array of Curved-bottom arches: identical clones, preview = result');
    await asmProfileSet(S.id, 'lower', 'round');
    check(rep(S.id).valid && same(S.id) && _asmProfile(S.id).lower === 'round', 'the same array with a Rounded lower');
    await asmProfileSet(S.id, 'array.arrangement', 'single');
    await asmProfileSet(S.id, 'width', keep.width); await asmProfileSet(S.id, 'position', keep.position);
  }
  await asmProfileSet(S.id, 'lower', 'round'); await asmProfileSet(S.id, 'bottom_depth', Math.round(_asmProfile(S.id).width / 2 / 1.5) * 1.5);
  S = _asmProfile(S.id);
  {
    const G = _asmOpeningGeom(S), O = window.OpeningShape;
    const w = t => O.widthAt(t, G.W, G.d, G.m, G.U, S.upper, S.lower);
    check(w(0.01 * G.d) > 0.13 * G.W && Math.abs(w(G.d * 0.999) - G.W) < 0.05 * G.W && w(0.5 * G.d) > 0.86 * G.W,
          'the Rounded lower profile is a genuine smooth curve (a half ellipse)');
    const e = 1e-3;
    check((G.W - w(G.d + G.m + e)) / e < 0.05 && w(G.H - e) / e > 0.1 && w(G.H - e) / e < 10,
          'the curved upper is Gothic: vertical where it meets the middle, a sharp point at the apex');
    _asmRenderProfiles();
    const tv = fl6(el('asm-profiles')).find(n => n.dataset && n.dataset.field === 'total_height');
    check(tv && Math.abs(parseFloat(tv.textContent) - (G.d + G.m + G.U)) < 0.01 && Math.abs(S.height - (G.d + G.m + G.U)) < 1e-6,
          `Total height = lower depth + middle + upper height, calculated (${tv && tv.textContent})`);
  }
  {
    const U0 = S.upper_height, W0 = S.width;
    await asmProfileSet(S.id, 'middle_height', S.middle_height + 7.5);
    await asmProfileSet(S.id, 'bottom_depth', S.bottom_depth + 3);
    S = _asmProfile(S.id);
    check(S.upper_height === U0 && S.width === W0 && rep(S.id).valid && same(S.id),
          'changing the middle height / lower depth never distorts the upper (nor the width)');
    // UPPER HEIGHT edit narrows, WIDTH edit raises the upper — the apex itself moves
    await asmProfileSet(S.id, 'upper_height', U0 * 0.6);
    S = _asmProfile(S.id);
    check(S.upper_height === _asmSnapZ(U0 * 0.6) && S.width < W0 && rep(S.id).valid && same(S.id) && S.driver === 'upper_height' &&
          asm.profNote && /Width .* → /.test(asm.profNote.text),
          `Upper height ${S.upper_height}: the width follows (${W0} → ${S.width} in), said on the card`);
    const U1 = S.upper_height, H1 = S.height;
    await asmProfileSet(S.id, 'width', W0 + 10);
    S = _asmProfile(S.id);
    check(S.width === W0 + 10 && S.upper_height > U1 && Math.abs(S.height - H1 - (S.upper_height - U1)) < 1e-6 && rep(S.id).valid && same(S.id),
          `Width ${W0 + 10}: the upper height follows (${U1} → ${S.upper_height} in), the middle and lower unchanged`);
  }
  // the ANGULAR DIAMOND configuration: upper height narrows, width raises the upper
  await asmProfileSet(S.id, 'middle', false); await asmProfileSet(S.id, 'upper', 'pointed'); await asmProfileSet(S.id, 'lower', 'pointed');
  await asmProfileSet(S.id, 'bottom_depth', 12); await asmProfileSet(S.id, 'upper_height', 12);
  S = _asmProfile(S.id);
  check(S.upper_height === 12 && S.height === 24 && rep(S.id).valid && same(S.id) && S.driver === 'upper_height',
        `Upper height 12: the width follows (${S.width} in), preview = result`);
  const wH = S.width;
  await asmProfileSet(S.id, 'width', wH + 12);
  S = _asmProfile(S.id);
  check(S.width === wH + 12 && S.upper_height > 12 && S.bottom_depth === 12 && rep(S.id).valid && same(S.id),
        `Width ${wH + 12}: the upper height follows (${S.upper_height} in), the lower unchanged`);
  // DRAG the side handle: the upper height follows live, during the drag
  await asmProfileSet(S.id, 'width', 24); await asmProfileSet(S.id, 'upper_height', 15);
  asmProfileElevation(S.id); asmDrawPreview();
  S = _asmProfile(S.id);
  {
    const tt = _asmProfileTarget(S), hs = _asmProfileHandles(S, tt).find(x => x.key === 'side');
    const [sx, sy] = asm.lastProj.P(..._asmWallPoint(tt, hs.s, hs.z));
    const H0 = S.height, nCalls = calls.length;
    asmProfileDragStart('side', sx, sy);
    const [sx2, sy2] = asm.lastProj.P(..._asmWallPoint(tt, hs.s + 10, hs.z));
    asmProfileDragTo(sx2, sy2);
    check(S.width > 30 && S.height > H0 && calls.length === nCalls && window.OpeningShape.evaluate(S, asm.state.layer_height, hh(), window.Designer.beadWidth()).ok,
          `dragging the width: the height follows during the drag (${H0} → ${S.height} in, width ${S.width}), always printable`);
    await asmProfileDragEnd();
    check(rep(S.id).valid && same(S.id), 'released: the resolved layers are the shape shown during the drag');
  }
  // DONE commits what is visible: no change of parameters or layers, no new resolve
  {
    const snap = JSON.stringify(_asmProfile(S.id)), layers = JSON.stringify(rep(S.id).layers), nCalls = calls.length;
    const cutKeys = JSON.stringify(asm.resolved.instances.map(i => i.geometry_key));
    asmProfileDone();
    check(JSON.stringify(_asmProfile(S.id)) === snap && JSON.stringify(rep(S.id).layers) === layers &&
          JSON.stringify(asm.resolved.instances.map(i => i.geometry_key)) === cutKeys && !calls.slice(nCalls).some(u => /resolve/.test(u)),
          'Done: the opening does not jump — same parameters, same resolved layers, no re-resolve');
    asmProfileEdit(S.id);
  }
  // MAXIMUM OVERHANG / LAYER HEIGHT: dependent dimensions recalculated (the driver wins)
  {
    await asmProfileSet(S.id, 'upper_height', 18);
    const W0 = _asmProfile(S.id).width, u = undoN();
    await asmSetMaxOverhang(hh() / 2);
    S = _asmProfile(S.id);
    check(S.upper_height === 18 && S.width < W0 && rep(S.id).valid && same(S.id) && undoN() === u + 1,
          `half the overhang: height kept (last edited), width ${W0} → ${S.width} in, one undo step`);
    await asmUndo();
    S = _asmProfile(S.id);
    check(S.width === W0 && Math.abs(_asmMaxOverhang() - 2 * (hh() / 2)) < 1e-9, 'undo restores the overhang and the width together');
    await asmProfileSet(S.id, 'width', W0 + 6);
    const H1 = _asmProfile(S.id).height;
    await asmSetLayerHeight(1.75);
    S = _asmProfile(S.id);
    const on = z => Math.abs(z / 1.75 - Math.round(z / 1.75)) < 1e-6;
    check(S.width === W0 + 6 && on(S.height) && on(S.z_bottom) && rep(S.id).valid && same(S.id),
          `layer height 1.75: width kept (last edited), height ${H1} → ${S.height} in on whole layers, preview = result`);
    await asmUndo();
    check(asm.state.layer_height === 1.5 && _asmProfile(S.id).height === H1, 'undo restores the layer height and the opening');
  }
  // ARRAYS inherit the constrained master; clear spacing preserved
  {
    await asmProfileSet(S.id, 'array.arrangement', 'staggered');
    await asmProfileSet(S.id, 'array.cols', 2); await asmProfileSet(S.id, 'array.rows', 2);
    await asmProfileSet(S.id, 'width', 12); await asmProfileSet(S.id, 'position', 20); await asmProfileSet(S.id, 'z_bottom', 3);
    await asmProfileSet(S.id, 'array.h_spacing', 20); await asmProfileSet(S.id, 'array.v_spacing', 4.1);
    S = _asmProfile(S.id);
    check(S.array.v_spacing === 4.5, 'vertical spacing is whole layers (identical clones cut identically)');
    const r6 = rep(S.id);
    const per = {};
    for (const L of r6.layers) (per[`${L.at}`] = per[`${L.at}`] || []).push(L.width);
    const seqs = Object.values(per).map(v => v.map(w => w.toFixed(4)).join());
    check(r6.valid && r6.members.length === 4 && new Set(seqs).size === 1 && same(S.id),
          'every array element is the identical constrained master (same cut widths), preview = result');
    await asmProfileSet(S.id, 'width', 16);
    S = _asmProfile(S.id);
    const xs6 = [...new Set(rep(S.id).members.map(m => m.position))].sort((a, b) => a - b);
    const zs6 = [...new Set(rep(S.id).members.map(m => m.z_bottom))].sort((a, b) => a - b);
    check(rep(S.id).valid && Math.abs(xs6[1] - xs6[0] - (16 + 20) / 2) < 1e-9 && Math.abs(zs6[1] - zs6[0] - (S.height + 4.5)) < 1e-9 && same(S.id),
          `a wider master: all clones follow, clear spacing kept (columns ${xs6.join(', ')}; rows ${zs6.join(', ')})`);
  }
  // a DOOR at ground level: flat lower, middle, a self-supporting upper
  {
    await asmProfileSet(S.id, 'array.arrangement', 'single');
    await asmProfileSet(S.id, 'lower', 'flat'); await asmProfileSet(S.id, 'upper', 'pointed');
    await asmProfileSet(S.id, 'z_bottom', 0); await asmProfileSet(S.id, 'width', 30); await asmProfileSet(S.id, 'position', 60);
    await asmProfileSet(S.id, 'middle', true); await asmProfileSet(S.id, 'middle_height', 60);
    S = _asmProfile(S.id);
    const d0 = rep(S.id).layers.find(L => L.layer === 0);
    check(rep(S.id).valid && d0 && d0.width === 30 && same(S.id), 'a flat-bottomed ground-level door: cut at full width from layer 0, self-supporting upper');
  }
  // HEADER upper: a flat top on a generated physical header
  const hdrs = id => ((asm.resolved.support || {}).headers || []).filter(h => h.profile === id);
  {
    const manual = { kind: 'header', id: 'h77', section: 0, layer: 70, x: 200, y: 140, angle: 0, span: 20, bearing: 8, depth: 13,
                     thickness: 6, snap: true };
    asm.state.objects.push(manual);              // an unrelated, manually placed header
    const u = undoN();
    await asmProfileSet(S.id, 'upper', 'header');
    S = _asmProfile(S.id);
    let H = hdrs(S.id);
    const lastCut = Math.max(...rep(S.id).layers.map(L => L.layer));
    check(rep(S.id).valid && H.length === 1 && H[0].id === `${S.id}-header` && H[0].layer === lastCut + 1 && !H[0].error &&
          Math.abs(H[0].span - 30) < 1e-6 && undoN() === u + 1 && same(S.id),
          `Upper → Header: a flat top and ONE generated header above its last cut layer (span ${H[0] && H[0].span} in), one undo step`);
    const f = (asm.resolved.support.findings || []).filter(x => x.layer === lastCut + 1 && x.status === 'needs_header');
    check(f.length === 0, 'the span over the flat top is carried by the header (no HEADER NEEDED)');
    _asmRenderProfiles(); _asmRenderSupport();
    const cards = fl6(el('asm-support') || {}).filter(n => n.dataset && n.dataset.header === `${S.id}-header`);
    check(cards.length === 1 && cards[0].dataset.profile === S.id, 'its header card is listed under Support, associated with the opening');
    await asmSetHeader(`${S.id}-header`, 'bearing', 11);
    S = _asmProfile(S.id);
    check(S.header && S.header.bearing === 11 && hdrs(S.id)[0].bearing === 11 && Math.abs(hdrs(S.id)[0].length - (30 + 22)) < 1e-6,
          'editing it through the header controls changes the opening\'s header settings');
    await asmProfileSet(S.id, 'upper', 'pointed');
    check(hdrs(S.id).length === 0 && asm.state.objects.some(o => o.id === 'h77') && _asmProfile(S.id).header.bearing === 11,
          'Upper → Angular: its header is gone (the manual one kept), its settings remembered');
    await asmProfileSet(S.id, 'upper', 'header');
    check(hdrs(S.id).length === 1 && hdrs(S.id)[0].bearing === 11, 'back to Header: the header is restored with its settings, no duplicate');
    // arrays: one header per opening, never duplicated or orphaned
    await asmProfileSet(S.id, 'middle_height', 24); await asmProfileSet(S.id, 'position', 22); await asmProfileSet(S.id, 'width', 20);
    await asmProfileSet(S.id, 'array.arrangement', 'horizontal'); await asmProfileSet(S.id, 'array.count', 3);
    await asmProfileSet(S.id, 'array.h_spacing', 20);                 // (a Parallel wall: piers ≥ 2 × its thickness)
    H = hdrs(S.id);
    check(rep(S.id).valid && H.length === 3 && new Set(H.map(h => h.id)).size === 3 && H.every(h => h.bearing === 11),
          `a header array: one associated header per opening (${H.map(h => h.id).join(', ')}${rep(S.id).valid ? '' : ' — ' + rep(S.id).errors.join('; ')})`);
    await asmProfileSet(S.id, 'array.count', 2);
    check(hdrs(S.id).length === 2, 'fewer openings → fewer headers (none orphaned)');
    await asmUndo();
    check(hdrs(S.id).length === 3, 'undo → three again');
    await asmRedo();
    check(hdrs(S.id).length === 2, 'redo → two');
    check(!asm.state.objects.some(o => String(o.id).startsWith(S.id)), 'generated headers are never stored as separate objects');
    // Save / Open keeps the association
    const ex = window.AssemblyProject.exportState();
    window.AssemblyProject.importState(JSON.parse(JSON.stringify(ex)));
    await asmResolve();
    check(hdrs(S.id).length === 2 && hdrs(S.id)[0].bearing === 11 && _asmProfile(S.id).upper === 'header',
          'Save / Open: the header opening and its header settings round trip');
    // deleting the opening removes its headers, never the manual one
    const keep = JSON.stringify(window.AssemblyProject.exportState());
    await asmProfileDelete(S.id);
    check(hdrs(S.id).length === 0 && asm.state.objects.some(o => o.id === 'h77'), 'deleting the opening removes its generated headers');
    await asmUndo();
    check(hdrs(S.id).length === 2, 'undo restores the opening with its headers');
    void keep;
    asm.state.objects = asm.state.objects.filter(o => o.id !== 'h77');
    await asmProfileSet(S.id, 'array.arrangement', 'single'); await asmProfileSet(S.id, 'upper', 'pointed');
    await asmProfileSet(S.id, 'width', 30); await asmProfileSet(S.id, 'position', 60); await asmProfileSet(S.id, 'middle_height', 60);
  }
  // SAVE / OPEN, and an older LENS project migrating
  {
    const ex = window.AssemblyProject.exportState();
    const lens = { kind: 'opening', id: 'p9', wall: { source: Ln.id, edge: null }, position: 60, z_bottom: 99, height: 12,
                   width: 4, shape: 'lens', label: 'old' };       // (above the door)
    // the former Curved (half-ellipse) lower, saved as 'curve'
    const oldArch = { kind: 'opening', id: 'p8', wall: { source: R.id, edge: 0 }, position: 60, z_bottom: 6, height: 30,
                      width: 12, shape: 'arch', label: '', upper: 'pointed', lower: 'curve', bottom_depth: 6, upper_height: 12,
                      middle: true, middle_height: 12 };
    // a saved DIAMOND of an odd number of layers (17: its widest point mid-layer)
    const oldDiamond = { kind: 'opening', id: 'p10', wall: { source: R.id, edge: 1 }, position: 60, z_bottom: 6, height: 25.5,
                         width: 12, shape: 'diamond', label: '', upper: 'pointed', lower: 'pointed', driver: 'height' };
    window.AssemblyProject.importState({ state: { ...ex.state, profiles: [...ex.state.profiles, lens, oldArch, oldDiamond], profile_counter: 10 },
                                         header_counter: ex.header_counter });
    await asmResolve();
    const L9 = _asmProfile('p9'), S2 = _asmProfile(S.id);
    check(!('shape' in L9) && L9.upper === 'curve' && L9.lower === 'arc' && !L9.middle && L9.width === 4 &&
          rep('p9').valid && same('p9') && asm.profNote && /Lens → curved upper \+ curved lower/.test(asm.profNote.text),
          `an older Lens opens as the curved Diamond, made printable — said once (${asm.profNote && asm.profNote.text})`);
    const P7 = _asmProfile('p10'), O7 = window.OpeningShape;
    const g7 = t => O7.widthAt(t, 12, P7.bottom_depth, 0, P7.upper_height, P7.upper, P7.lower);
    check(!('shape' in P7) && P7.upper === 'pointed' && P7.lower === 'pointed' && !P7.middle && P7.upper_height === 12.75 &&
          P7.bottom_depth === 12.75 && P7.height === 25.5 && P7.width === 12 && P7.driver === 'upper_height' &&
          [0.75, 6.75, 12.75, 18.75, 24.75].every(t => Math.abs(g7(t) - 12 * (1 - Math.abs(2 * t / 25.5 - 1))) < 1e-9) &&
          rep('p10').valid && same('p10') && !/P\d+: Diamond/.test(asm.profNote.text),
          `a saved Diamond opens as the identical angular configuration (odd layer count kept, nothing to report)`);
    const P8 = _asmProfile('p8');
    check(P8.lower === 'round' && P8.bottom_depth === 6 && P8.height === 30 && rep('p8').valid && same('p8') &&
          /Curved bottom → Rounded/.test(asm.profNote.text),
          'an opening saved with the former Curved lower opens as Rounded, same geometry — said once');
    check(JSON.stringify(S2) === JSON.stringify(ex.state.profiles.find(p => p.id === S.id)) && rep(S.id).valid,
          'a saved opening re-opens unchanged (Save / Open round trip)');
  }

  // ---- 7. OPENING ALIGNMENT: ONE global setting under Assembly Transform (2026-10-10) ----
  {
    asm.state.sections = [{ design_id: 'base', height: 120, transform: { profile: 'linear', shift: [0.2, 0], scale_in: 0 } }];
    asm.state.profiles = asm.state.profiles.filter(p => p.id === S.id);
    setWorkspace('designer');
    layer.openings = (layer.openings || []).concat([{ id: 'dw', type: 'opening', source_path_id: R.id, center_s: 60, width: 20,
                                                      end_treatment: 'inherit', z_min: null, z_max: null, label: 'Designer window' }]);
    historyCheckpoint();
    setWorkspace('assembly');
    await asmResolve();
    await asmProfileSet(S.id, 'upper', 'pointed'); await asmProfileSet(S.id, 'z_bottom', 12);
    S = _asmProfile(S.id);
    const worldAt = L => { const T = asm.resolved.instances[L.layer].transform; return Math.round((T.scale * L.at + T.tx) * 1e3) / 1e3; };
    check(asm.state.opening_alignment === 'plumb', 'Vertical / Plumb by default (since 2026-10-14)');
    asm.state.opening_alignment = 'follow';
    await asmResolve();
    S = _asmProfile(S.id);
    check(rep(S.id).valid && new Set(rep(S.id).layers.map(worldAt)).size > 5,
          'Follow Wall (chosen): under a sideways shift the self-supporting opening leans (moves in world)');
    asmProfileEdit(S.id);
    _asmRenderProfiles(); asmRender();
    const fl7 = n => [n, ...(n.children || []).flatMap(fl7)];
    check(!fl7(el('asm-profiles')).some(n => n.dataset && n.dataset.field === 'alignment') &&
          !fl7(el('asm-profiles')).some(n => n.dataset && n.dataset.section === 'designer-openings'),
          'no per-opening alignment controls in Profiles');
    const gsel = fl7(el('asm-assembly-tf')).find(n => n.dataset && n.dataset.field === 'opening_alignment');
    check(gsel && gsel.value === 'follow', 'ONE Opening Alignment control under Assembly Transform (Follow Wall)');
    const u = undoN();
    await asmSetOpeningAlignment('plumb');
    S = _asmProfile(S.id);
    const al = (asm.resolved.alignments || []).find(a => a.opening === 'dw');
    const moves = asm.resolved.instances.filter(i => /"move"/.test(i.print_key || i.geometry_key || '')).length;
    check(asm.state.opening_alignment === 'plumb' && undoN() === u + 1 && rep(S.id).valid &&
          new Set(rep(S.id).layers.map(worldAt)).size === 1 && same(S.id),
          'Vertical / Plumb: the self-supporting opening is cut at ONE world position on every layer; preview = result; one undo step');
    check(al && al.valid && al.members.length === 1 && moves > 10,
          `…and the Designer window too (re-placed on ${moves} layers) — one setting for every opening`);
    await asmUndo();
    check(asm.state.opening_alignment === 'follow' && new Set(rep(S.id).layers.map(worldAt)).size > 5 &&
          !asm.resolved.instances.some(i => /"move"/.test(i.geometry_key || '')), 'undo → Follow Wall for all');
    await asmRedo();
    check(asm.state.opening_alignment === 'plumb' && new Set(rep(S.id).layers.map(worldAt)).size === 1, 'redo → plumb');
    const ex = window.AssemblyProject.exportState();
    window.AssemblyProject.importState(JSON.parse(JSON.stringify(ex)));
    await asmResolve();
    check(asm.state.opening_alignment === 'plumb' && new Set(rep(S.id).layers.map(worldAt)).size === 1,
          'Save / Open keeps the global alignment');
    // MIGRATION of the short-lived per-opening form
    const legacy = JSON.parse(JSON.stringify(ex));
    delete legacy.state.opening_alignment;
    legacy.state.opening_alignment = { dw: 'plumb' };
    legacy.state.profiles.forEach(p => { p.alignment = 'plumb'; });
    window.AssemblyProject.importState(legacy);
    await asmResolve();
    check(asm.state.opening_alignment === 'plumb' && !asm.state.profiles.some(p => 'alignment' in p),
          'an older per-opening project (everything plumb) opens as global Vertical / Plumb');
    legacy.state.profiles.forEach(p => { delete p.alignment; });
    window.AssemblyProject.importState(legacy);
    await asmResolve();
    check(asm.state.opening_alignment === 'follow', 'mixed (a following Assembly opening) → Follow Wall (stored explicitly)');
  }

}
eval(src + '\n;(' + run.toString() + ')().then(() => { console.log("UI PROFILES SMOKE PASSED"); process.exit(0); })' +
     '.catch(e => { console.log(String(e && e.stack || e)); process.exit(1); });');
