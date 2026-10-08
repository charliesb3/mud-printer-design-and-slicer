// UI smoke test for pass-5 editing: undo / redo (snapshot history, drag
// coalescing, redo clearing, snapping), copy / paste / duplicate / rotate,
// the Extra Offset source picker, parametric insets and infill regions.
// Loads static/app.js against a permissive DOM stub and drives the real
// interaction functions. Run via tests/test_pass5.py (or: node tests/js/ui_edit_smoke.js).
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
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const ev = (wx, wy, extra) => { const [cx, cy] = worldToCanvas(wx, wy); return Object.assign({ clientX: cx, clientY: cy }, extra || {}); };
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  const near = (a, b, t = 1e-6) => Math.abs(a - b) < t;
  const key = (k, mods) => (listeners.keydown || []).forEach(f => f(Object.assign({ key: k, preventDefault() {} }, mods || {})));
  const byId = id => layer.source_paths.find(p => p.id === id);
  const kids = el => el.children;
  const resetEl = id => { document.getElementById(id).children.length = 0; };
  const steps = () => _hist.undo.length;

  // ===== 1. Extra Offset sources =========================================
  setTool('rect'); onMouseDown(ev(100, 140)); onMouseDown(ev(300, 260));
  setTool('line'); onMouseDown(ev(50, 50)); onMouseDown(ev(150, 50));
  setTool('line'); onMouseDown(ev(50, 350)); onMouseDown(ev(150, 350));
  const [R, L1, L2] = layer.source_paths;
  check(R.label === 'Rect 1' && L1.label === 'Line 1' && L2.label === 'Line 2', 'Rect 1, Line 1, Line 2 drawn');
  selectedId = L1.id; addOffset();
  check(layer.offset_treatments[0].source_path_id === L1.id, 'Add Offset uses the SELECTED path (Line 1), not the first path');
  check(layer.offset_treatments[0].direction === 'left', 'open source gets a left/right direction');
  selectedId = L2.id; addOffset();
  check(layer.offset_treatments[1].source_path_id === L2.id, 'second offset owned by Line 2');
  selectedId = L1.id; addOffset();
  check(layer.offset_treatments.filter(o => o.source_path_id === L1.id).length === 2, 'Line 1 owns two offsets');
  selectedId = R.id; addOffset();
  const pl = buildPayload().offset_treatments;
  check(pl.map(o => o.source_path_id).join() === [L1.id, L2.id, L1.id, R.id].join(), 'payload carries every source');

  // the picker in the real offset panel: reassign offset #4 (Rect 1) → Line 2
  resetEl('offset-list'); updateOffsetList();
  const block = kids(document.getElementById('offset-list'))[3];
  const row = kids(kids(block)[1])[0];
  const btn = kids(row)[1];
  btn.onclick();
  const list = kids(row)[2];
  const names = kids(list).map(n => n.textContent.split('  ')[0]);
  check(names.join() === 'Rect 1,Line 1,Line 2', 'picker lists every path: ' + names.join());
  kids(list)[2].onmousedown({ preventDefault() {} });
  check(layer.offset_treatments[3].source_path_id === L2.id, 'picking Line 2 reassigns the offset');
  check(layer.offset_treatments[3].direction === 'left', 'direction made valid for the open source');

  // picker built BEFORE a path existed still offers it when opened
  const panel0 = document.createElement('div');
  let picked = null;
  const pk = addPathPickerRow(panel0, 'Source', R.id, id => { picked = id; });
  setTool('line'); onMouseDown(ev(200, 20)); onMouseDown(ev(260, 20));
  const L3 = layer.source_paths[layer.source_paths.length - 1];
  pk.open();
  check(pk.items.some(p => p.id === L3.id), 'picker re-reads candidates on open (new Line 3 offered)');
  const nodeL3 = kids(pk.list)[pk.items.findIndex(p => p.id === L3.id)];
  nodeL3.onmouseenter();
  check(kids(pk.list)[pk.items.findIndex(p => p.id === L3.id)] === nodeL3, 'hover does not rebuild the hovered node');
  check(highlightPathId === L3.id, 'hover highlights the candidate');
  nodeL3.onmousedown({ preventDefault() {} });
  check(picked === L3.id, 'clicking Line 3 selects it');

  // delete source → its offsets go; undo brings source + offsets back
  const before = layer.offset_treatments.length;
  deletePath(L2.id);
  check(layer.offset_treatments.length === before - 2 && !layer.offset_treatments.some(o => o.source_path_id === L2.id),
        'deleting Line 2 removes only its offsets');
  undo();
  check(byId(L2.id) && layer.offset_treatments.filter(o => o.source_path_id === L2.id).length === 2,
        'undo restores Line 2 and its offsets');
  // move the source after the offset exists: the offset stays attached
  translatePath(L1.id, 0, 30);
  check(byId(L1.id).start[1] === 80 && buildPayload().offset_treatments[0].source_path_id === L1.id,
        'moving Line 1 keeps its offsets attached (recomputed by the backend)');

  // ===== 2. Undo / redo ===================================================
  clearAll();
  check(layer.source_paths.length === 0, 'clear all');
  undo();
  check(layer.source_paths.length === 4, 'Clear All is undoable');
  redo();
  check(layer.source_paths.length === 0, 'and redoable');
  const s0 = steps();
  setTool('line'); onMouseDown(ev(100, 100)); onMouseDown(ev(200, 100));
  const A = layer.source_paths[0];
  check(steps() === s0 + 1, 'creating a path is one step');
  check(!document.getElementById('btn-undo').disabled && document.getElementById('btn-redo').disabled,
        'Undo enabled, Redo disabled');
  // a continuous drag is ONE step
  selectedId = A.id;
  onMouseDown(ev(200, 100));
  for (let i = 1; i <= 10; i++) onMouseMove(ev(200 + i * 3, 100 + i * 2));
  onMouseUp({});
  check(steps() === s0 + 2 && near(A.end[0], 230) && near(A.end[1], 120), 'drag = exactly one step');
  key('z', { metaKey: true });
  const A1 = byId(A.id);
  check(near(A1.end[0], 200) && near(A1.end[1], 100), 'Cmd-Z undoes the whole drag');
  check(!document.getElementById('btn-redo').disabled, 'Redo enabled after undo');
  key('z', { metaKey: true, shiftKey: true });
  check(near(byId(A.id).end[0], 230), 'Cmd-Shift-Z redoes it');
  key('z', { metaKey: true });
  // a new edit after undo clears redo
  translatePath(A.id, 5, 0);
  check(_hist.redo.length === 0 && document.getElementById('btn-redo').disabled, 'new edit after undo clears redo');
  // typing in a field: Cmd-Z belongs to the field
  document.activeElement = { tagName: 'INPUT' };
  const k0 = steps(); key('z', { metaKey: true });
  check(steps() === k0 && near(byId(A.id).start[0], 105), 'Cmd-Z inside an input is not a design undo');
  document.activeElement = null;

  // wall thickness / alignment, network wall, infill, opening, junctions
  const P = byId(A.id);
  P.wall = { thickness: 8, align: 'center', print_reference: false }; scheduleRefresh();
  P.wall.align = 'left'; scheduleRefresh();
  undo(); check(byId(A.id).wall.align === 'center', 'undo wall alignment');
  undo(); check(!byId(A.id).wall || !byId(A.id).wall.thickness, 'undo wall thickness');
  redo(); redo(); check(byId(A.id).wall.align === 'left', 'redo both');
  layer.network_walls.push({ id: 'nw', path_id: A.id, thickness: 6, align: 'center' }); scheduleRefresh();
  undo(); check(layer.network_walls.length === 0, 'undo network wall');
  selectedId = A.id; addInfill();
  check(layer.infills.length === 1, 'infill created');
  layer.infills[0].params.spacing = 30; scheduleRefresh();
  undo(); check(layer.infills[0].params.spacing !== 30, 'undo infill edit');
  undo(); check(layer.infills.length === 0, 'undo infill creation');
  layer.junction_style = 'round'; scheduleRefresh();
  undo(); check(layer.junction_style === 'miter', 'undo junction style');
  layer.openings.push({ id: 'op1', source_path_id: A.id, center_s: 30, width: 12 }); scheduleRefresh();
  undo(); check(layer.openings.length === 0, 'undo opening');

  // memory safeguard
  for (let i = 0; i < HISTORY_MAX_STEPS + 50; i++) { layer.junction_radius = 1 + i; scheduleRefresh(); }
  check(_hist.undo.length <= HISTORY_MAX_STEPS, 'history capped at HISTORY_MAX_STEPS');
  layer.junction_radius = 2; scheduleRefresh();
  _hist.undo.length = 0; _hist.bytes = 0;        // (test: leave the cap behind)

  // ===== 3. Duplicate → rotate → snap endpoint → undo / redo ==============
  clearAll();
  setTool('line'); onMouseDown(ev(100, 200)); onMouseDown(ev(200, 200));
  const O = layer.source_paths[0];
  selectedId = O.id;
  key('d', { metaKey: true });
  const D = layer.source_paths[1];
  check(D.id !== O.id && D.label === 'Line 2' && near(D.start[0], 110) && near(D.start[1], 190),
        'Cmd-D: new id, Line 2, offset copy');
  rotatePath(D.id, 90);
  check(near(D.start[0], 160) && near(D.start[1], 140) && near(D.end[0], 160) && near(D.end[1], 240),
        'rotate 90° about the midpoint');
  const sDrag = steps();
  selectedId = D.id;
  onMouseDown(ev(160, 240));                 // D's end handle
  onMouseMove(ev(197, 203));                 // near O's end (200, 200)
  check(snapHint && snapHint.kind === 'end', 'duplicate endpoint shows an end snap');
  onMouseUp({});
  check(D.end[0] === 200 && D.end[1] === 200, 'duplicate end snapped EXACTLY onto the original end');
  check(steps() === sDrag + 1, 'snap drag is one step');
  undo();
  check(near(byId(D.id).end[0], 160) && near(byId(D.id).end[1], 240), 'undo: disconnected again');
  undo();
  check(near(byId(D.id).start[0], 110) && near(byId(D.id).start[1], 190), 'undo: rotation undone');
  undo();
  check(!byId(D.id) && layer.source_paths.length === 1, 'undo: duplicate gone');
  redo(); redo(); redo();
  check(byId(D.id).end[0] === 200 && byId(D.id).end[1] === 200, 'redo ×3: connected again, exactly');
  // copy / paste
  selectedId = O.id;
  key('c', { metaKey: true });
  key('v', { metaKey: true });
  const Pa = layer.source_paths[layer.source_paths.length - 1];
  key('v', { metaKey: true });
  const Pb = layer.source_paths[layer.source_paths.length - 1];
  check(Pa.label === 'Line 3' && Pb.label === 'Line 4' && Pa.id !== Pb.id, 'paste: Line 3, Line 4');
  check(near(Pb.start[0] - Pa.start[0], PASTE_OFFSET), 'successive pastes step away');
  check(tool === 'edit' && selectedId === Pb.id, 'pasted copy is selected');
  key('Backspace');
  check(!byId(Pb.id), 'Delete/Backspace deletes the selection');
  undo(); check(!!byId(Pb.id), 'undo delete');
  // rotation handle: one step, about the pivot
  selectedId = O.id;
  const rh = _rotHandle(byId(O.id));
  const sR = steps();
  onMouseDown(ev(rh.wx, rh.wy));
  check(rotateDrag !== null, 'rotation handle grabbed');
  onMouseMove(ev(rh.pivot[0] - 30, rh.pivot[1]));       // quarter turn CCW
  onMouseMove(ev(rh.pivot[0] - 40, rh.pivot[1] + 1, { shiftKey: true }));
  onMouseUp({});
  check(steps() === sR + 1, 'handle rotation = one step');
  check(near(byId(O.id).start[0], 150, 1e-6) && near(byId(O.id).start[1], 150, 1e-6), 'shift snaps to 15° (here 90°)');

  // ===== rotated rectangle stays a rectangle; its handles follow =========
  clearAll();
  addPrimitive('RectanglePath');               // (140,140) 120x120
  const RR = layer.source_paths[0];
  RR.w = 200; RR.h = 100; _computePrimitivePoints(RR); scheduleRefresh();
  rotatePath(RR.id, 30);
  check(RR.type === 'RectanglePath' && near(RR.rotation, Math.PI / 6), 'rect rotation stored as a parameter');
  const hs = getHandles(RR);
  check(hs.every((h, i) => near(h.wx, RR.points[i][0]) && near(h.wy, RR.points[i][1])), 'handles at the rotated corners');
  const tl = [hs[0].wx, hs[0].wy];
  dragging = { pathId: RR.id, handleKey: 'br', originalState: capturePathState(RR) };
  applyHandleDrag(RR, 'br', hs[2].wx + 20, hs[2].wy, dragging.originalState);
  dragging = null;
  const tl2 = getHandles(RR)[0];
  check(near(tl2.wx, tl[0]) && near(tl2.wy, tl[1]), 'resizing a rotated rect keeps the opposite corner');
  rotatePath(RR.id, 60);
  check(RR.rotation === 0, '30° + 60° = axis aligned again (w/h swapped)');

  // ===== 4. Infill region / voids =========================================
  clearAll();
  setTool('ellipse'); onMouseDown(ev(200, 200)); onMouseDown(ev(240, 220));   // drawn FIRST
  setTool('rect'); onMouseDown(ev(100, 120)); onMouseDown(ev(300, 280));
  const [EL, RB] = layer.source_paths;
  selectedId = null;
  addInfill();
  check(layer.infills[0].path_id === RB.id, 'default infill region = OUTERMOST closed boundary (not creation order)');
  check(layer.infills[0].kind === 'solid' && layer.infills[0].pattern === 'rectilinear', 'closed single-bead boundary → Solid');
  selectedId = EL.id;
  addInfill();
  check(layer.infills[1].path_id === EL.id, 'explicitly selected inner shape is filled');
  networkInfo = { infills: [{ id: layer.infills[0].id, region: RB.id, voids: [EL.id], islands: [], status: 'ok', regions: 1 }] };
  check(_infillVoidsLabel(layer.infills[0]) === 'Region: Rect 1 · Voids: Ellipse 1', 'UI: Region / Voids line');
  _setInfillKind(layer.infills[0], 'wall');
  check(layer.infills[0].pattern === 'zigzag' && layer.infills[0].params.spacing === 20, 'switching to Wall infill picks a wall pattern');
  networkInfo = null;

  // ===== 5. Parametric inset ==============================================
  clearAll();
  setTool('rect'); onMouseDown(ev(100, 100)); onMouseDown(ev(300, 200));
  const PR = layer.source_paths[0];
  const C = createInset(PR.id, 10, 'inset');
  check(C.type === 'InsetPath' && C.parent_id === PR.id && C.label === 'Inset 1', 'Create Inset 10 → InsetPath child');
  const bb = pts => [Math.min(...pts.map(p => p[0])), Math.min(...pts.map(p => p[1])),
                     Math.max(...pts.map(p => p[0])), Math.max(...pts.map(p => p[1]))];
  check(bb(C.points).every((v, i) => near(v, [110, 110, 290, 190][i])), 'preview: 10 in inside the rect');
  check(getHandles(C).length === 0 && _rotHandle(C) === null, 'an inset has no handles of its own');
  selectedId = PR.id;
  const sI = steps();
  onMouseDown(ev(300, 200));                       // br corner
  onMouseMove(ev(360, 240));
  check(bb(byId(C.id).points)[2] > 340, 'child follows the parent live during the drag');
  onMouseUp({});
  check(steps() === sI + 1 && bb(byId(C.id).points).every((v, i) => near(v, [110, 110, 350, 230][i])),
        'resize parent → inset recomputed, one step');
  byId(C.id).distance = 12; _refreshInsetChildren(PR.id); scheduleRefresh();
  check(bb(byId(C.id).points).every((v, i) => near(v, [112, 112, 348, 228][i])), 'distance 10 → 12 recomputes');
  undo();
  check(byId(C.id).distance === 10, 'undo distance change');
  undo();
  check(near(bb(byId(C.id).points)[2], 290) && near(byId(PR.id).w, 200), 'undo resize restores parent AND child');
  // backend-evaluated points are absorbed without a phantom step
  const sB = steps();
  networkInfo = { derived_sources: { [C.id]: [[110, 110], [290, 110], [290, 190], [110, 190.0000001]] } };
  _applyDerivedSources();
  check(steps() === sB && !canUndo() === (sB === 0), 'derived points do not create an undo step');
  networkInfo = null;
  // parent deletion detaches (documented choice); undo re-links
  deletePath(PR.id);
  const Cd = byId(C.id);
  check(Cd.type === 'ExplicitPath' && Cd.closed && Cd.control_points.length === 4 && !Cd.parent_id,
        'deleting the parent detaches the inset (kept as a plain closed path)');
  undo();
  check(byId(C.id).type === 'InsetPath' && byId(C.id).parent_id === PR.id && byId(PR.id), 'undo re-links parent and child');
  selectedId = C.id;
  onMouseDown(ev(110, 150));                        // on the inset's edge
  check(!bodyDragging, 'an inset cannot be dragged on its own');
  onMouseUp({});
  const cp = duplicateSelected();
  check(cp.type === 'ExplicitPath' && cp.closed, 'a copy of an inset is ordinary geometry');
  undo();
  check(detachInset(C.id) && byId(C.id).type === 'ExplicitPath', 'Detach keeps the shape');
  undo();
  check(byId(C.id).type === 'InsetPath', 'undo detach');
  const O2 = createInset(PR.id, 5, 'outset');
  check(bb(O2.points).every((v, i) => near(v, [95, 95, 305, 205][i])), 'outset preview');

  // ===== wall lattice: Target Spacing + actual pitch diagnostic ===========
  const wf = { id: 'wf', kind: 'wall', pattern: 'zigzag', params: {} };
  check(_paramsFor(wf)[0].label === 'Target Spacing', 'wall infill: "Target Spacing"');
  check(_paramsFor({ kind: 'solid', pattern: 'rectilinear' })[0].label === 'Spacing', 'solid infill keeps "Spacing"');
  networkInfo = { infills: [{ id: 'wf', lattice: { motif: true, pitch_min: 18.2, pitch_max: 21.04 } }] };
  check(_latticeActual(wf) === 'Actual: 18.2–21.0 in', 'actual pitch range shown');
  networkInfo = { infills: [{ id: 'wf', lattice: { motif: true, pitch_min: 19.5, pitch_max: 19.5 } }] };
  check(_latticeActual(wf) === 'Actual: 19.5 in', 'single actual pitch');
  networkInfo = null;
  check(_latticeActual(wf) === '', 'no diagnostic before routing');

  // ===== pass 7: Angle unit (degrees, not inches) =========================
  clearAll();
  addPrimitive('RectanglePath');
  const RA = layer.source_paths[0];
  selectedId = RA.id;
  resetEl('path-props'); updatePropPanel();
  const rowsA = kids(document.getElementById('path-props'));
  const rowByLabel = l => rowsA.find(rw => kids(rw)[0] && kids(rw)[0].textContent === l);
  const angleRow = rowByLabel('Angle');
  check(angleRow && kids(angleRow)[2].textContent === '°', 'Angle shows degrees (°), not inches');
  check(kids(rowByLabel('Rotate'))[2].textContent === '°', 'Rotate shows degrees');
  check(kids(rowByLabel('Width'))[2].textContent === 'in', 'lengths still show inches');

  // ===== pass 7: wall relationship between nested closed boundaries ======
  clearAll();
  setTool('rect'); onMouseDown(ev(100, 100)); onMouseDown(ev(300, 220));   // outer
  setTool('rect'); onMouseDown(ev(140, 130)); onMouseDown(ev(200, 170));   // inner
  const [RO, RI] = layer.source_paths;
  const s0r = steps();
  const rel = createWallRelation(RO.id, RI.id, 10);
  check(rel && rel.outer_id === RO.id && rel.inner_id === RI.id, 'relationship created (outer / inner by nesting)');
  check(near(byId(RI.id).x, 110) && near(byId(RI.id).w, 180) && near(byId(RI.id).h, 100), 'inner boundary = outer − 10 in');
  check(steps() === s0r + 1, 'creating it is one undo step');
  check(getHandles(byId(RI.id)).length === 0 && _rotHandle(byId(RI.id)) === null, 'the driven boundary has no handles');
  selectedId = RO.id;
  onMouseDown(ev(300, 220)); onMouseMove(ev(340, 260)); onMouseUp({});
  check(near(byId(RI.id).w, 220) && near(byId(RI.id).h, 140), 'dragging the outer resizes the inner live (wall stays 10 in)');
  setRelationThickness(rel.id, 12);
  check(near(byId(RI.id).x, 112) && near(byId(RI.id).w, 216), 'thickness 10 → 12');
  undo();
  check(near(byId(RI.id).w, 220), 'undo thickness');
  undo();
  check(near(byId(RI.id).w, 180) && near(byId(RO.id).w, 200), 'undo the driver edit (both boundaries)');
  undo();
  check(layer.wall_relations.length === 0, 'undo the relationship');
  redo(); redo(); redo();
  check(layer.wall_relations.length === 1 && near(byId(RI.id).w, 216), 'redo all three');
  const relId = layer.wall_relations[0].id;
  setRelationDriver(relId, 'inner');
  byId(RI.id).w = 100; scheduleRefresh();
  check(near(byId(RO.id).w, 124), 'inner can drive: outer = inner + 12');
  check(breakWallRelation(relId) && layer.wall_relations.length === 0 && near(byId(RO.id).w, 124),
        'break keeps the geometry');
  undo();
  check(layer.wall_relations.length === 1, 'undo break');
  deletePath(RO.id);
  check(layer.wall_relations.length === 0 && byId(RI.id), 'deleting a boundary removes the link, keeps the other');
  undo();
  addPrimitive('CirclePath');
  check(createWallRelation(RO.id, layer.source_paths[layer.source_paths.length - 1].id, 5) === null,
        'rect + circle refused (unsupported pair)');

  // ===== pass 7: V1 / V2 only where they do something; Max unsupported ===
  const wf2 = { id: 'wf2', kind: 'wall', pattern: 'zigzag', params: { spacing: 20 } };
  networkInfo = { infills: [{ id: 'wf2', lattice: { motif: true, variation_effective: false, pitch_min: 18, pitch_max: 21, max_unsupported: 19.1, max_unsupported_limit: 27.5 } }] };
  check(_variationEffective(wf2) === false, 'V1 / V2 hidden for a wall network');
  check(_latticeActual(wf2) === 'Actual: 18.0–21.0 in · Max unsupported: 19.1 in (limit 27.5)', 'diagnostics line');
  networkInfo.infills[0].lattice.variation_effective = true;
  check(_variationEffective(wf2) === true, 'V1 / V2 kept for a closed loop / lone wall');
  layer.infills = [wf2];
  resetEl('infill-list'); updateInfillList();
  const txt = JSON.stringify(kids(document.getElementById('infill-list')).map(b => b.children.length));
  check(txt.length > 0, 'infill panel renders with the Advanced section');
  networkInfo = null; layer.infills = [];

  // ===== pass 8 correction: wall authoring UI =============================
  const flat = el => [el].concat(...(el.children || []).map(flat));
  const panelNodes = () => { resetEl('path-props'); updatePropPanel(); return flat(document.getElementById('path-props')); };
  clearAll();
  setTool('rect'); onMouseDown(ev(100, 100)); onMouseDown(ev(300, 220));
  const [WO] = layer.source_paths;
  selectedId = WO.id;
  let nodes = panelNodes();
  const iWall = nodes.findIndex(n => n.textContent === 'Wall System');
  const iAdv = nodes.findIndex(n => n.id === 'adv-geometry');
  check(iWall >= 0 && iAdv > iWall, 'the Wall System choice is the primary wall control (before the Advanced section)');
  check(nodes.find(n => n.id === 'adv-geometry').open !== true, 'Advanced (link / inset) starts collapsed');
  check(nodes.some(n => n.id === 'relation-none') && !nodes.some(n => n.id === 'relation-picker'),
        'no candidate → an explanation, not a dead picker');
  // Wall Thickness still works
  WO.wall = { thickness: 10, align: 'inside' }; scheduleRefresh();
  check(buildPayload().source_paths[0].wall.thickness === 10, 'Wall Thickness kept in the payload');
  WO.wall = null;
  _wsNew([WO.id]).align = 'inside';
  nodes = panelNodes();
  check(nodes.some(n => typeof n.textContent === 'string' && n.textContent.startsWith('10 in wall · Inside · Skin + Web')),
        'Wall section names the Wall System envelope');
  layer.wall_systems = []; layer.infills = layer.infills.filter(f => !f.owner);
  WO.wall = { thickness: 10, align: 'inside' }; scheduleRefresh();
  derivedPaths = [{ id: WO.id + '.wall', source_id: WO.id, treatment_id: WO.id + '.wall', closed: true,
                    points: [[110, 110], [290, 110], [290, 210], [110, 210]] }];
  _drawDerivedWallFaces();                       // selection trace + label (no throw)
  check(true, 'selected wall traces its derived face');
  derivedPaths = [];
  WO.wall = { thickness: 12, align: 'inside' }; scheduleRefresh();
  undo();
  check(byId(WO.id).wall.thickness === 10, 'undo a Wall Thickness change (12 → 10)');
  redo();
  check(byId(WO.id).wall.thickness === 12, 'redo it');
  byId(WO.id).wall = { thickness: 10, align: 'inside' }; scheduleRefresh();
  WO.wall = null; scheduleRefresh();
  // a nested rectangle → link via the real controls
  setTool('rect'); onMouseDown(ev(130, 125)); onMouseDown(ev(270, 195));
  const WI = layer.source_paths[1];
  selectedId = WO.id;
  nodes = panelNodes();
  const pbtn = nodes.find(n => n.id === 'relation-picker');
  check(!!pbtn && !nodes.some(n => n.id === 'relation-none'), 'with a nested boundary the picker is offered');
  pbtn.onclick();                                         // open
  check(highlightPathId === WI.id, 'the candidate boundary is highlighted on the canvas while choosing');
  const plist = flat(document.getElementById('path-props')).find(n => n.className === 'path-picker-list');
  check(plist && plist.children.length === 1, 'exactly one candidate listed');
  plist.children[0].onmouseenter && plist.children[0].onmouseenter();
  check(highlightPathId === WI.id, 'hovering a candidate highlights it');
  plist.children[0].onmousedown({ preventDefault() {} });
  check(highlightPathId === null, 'highlight cleared after choosing');
  const s0w = steps();
  nodes.find(n => n.id === 'relation-link').onclick();
  check(layer.wall_relations.length === 1 && layer.wall_relations[0].inner_id === WI.id, 'Link creates the relationship');
  check(steps() === s0w + 1, 'linking is one undo step');
  nodes = panelNodes();
  check(nodes.find(n => n.id === 'adv-geometry').open === true, 'an active link keeps the Advanced section open');
  undo();
  check(layer.wall_relations.length === 0, 'undo link');
  redo();
  check(layer.wall_relations.length === 1, 'redo link');
  breakWallRelation(layer.wall_relations[0].id);
  check(layer.wall_relations.length === 0, 'unlink');
  undo();
  check(layer.wall_relations.length === 1, 'undo unlink');
  layer.wall_relations = []; scheduleRefresh();
  // inset / outset through the relabelled controls
  selectedId = WO.id;
  nodes = panelNodes();
  nodes.find(n => n.id === 'inset-distance').value = '8';
  const nIns = layer.source_paths.length;
  nodes.find(n => n.id === 'inset-create').onclick();
  const NI = layer.source_paths[layer.source_paths.length - 1];
  check(layer.source_paths.length === nIns + 1 && NI.type === 'InsetPath' && NI.distance === 8 && NI.mode === 'inset',
        'Create inset (8 in) makes a new parametric path');
  undo();
  check(layer.source_paths.length === nIns, 'undo inset');
  redo();
  check(layer.source_paths.length === nIns + 1, 'redo inset');
  nodes = panelNodes();
  nodes.find(n => n.id === 'outset-create').onclick();
  check(layer.source_paths[layer.source_paths.length - 1].mode === 'outset', 'Create outset');
  // an empty picker says so (also the Extra Offset picker)
  const box = document.createElement('div');
  const epk = addPathPickerRow(box, 'X', null, () => {}, null, () => false);
  epk.open();
  check(epk.list.children.length === 1 && epk.list.children[0].textContent === 'no valid choice',
        'an empty picker shows "no valid choice"');
  epk.close(false);

  console.log('UI EDIT SMOKE PASSED');
})();
`);
