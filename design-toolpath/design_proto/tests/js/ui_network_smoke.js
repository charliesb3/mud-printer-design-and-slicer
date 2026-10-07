// UI smoke test for wall networks: loads static/app.js against a permissive
// DOM/canvas stub and drives the real interaction functions: snapping while
// drawing, dragging ends and moving paths; network drawing; payload.
// Run via tests/test_networks.py (or: node tests/js/ui_network_smoke.js).
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
global.console.warn = () => {};
let src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
src = src.replace(/\ninit\(\);\s*$/, '\n') + '\ncanvas.width = 800; canvas.height = 800;\n';
eval(src + `
;(function run() {
  const ev = (wx, wy) => { const [cx, cy] = worldToCanvas(wx, wy); return { clientX: cx, clientY: cy }; };
  const check = (c, m) => { if (!c) throw new Error('FAIL: ' + m); console.log('ok  ' + m); };
  addPrimitive('RectanglePath');               // (140,140) 120x120
  const R = layer.source_paths[0];
  // --- draw tool: first click near the bottom edge snaps exactly onto it
  setTool('draw');
  onMouseMove(Object.assign(ev(203.3, 143), {}));
  check(snapHint && snapHint.kind === 'edge', 'hover shows an edge snap');
  check(Math.abs(snapHint.pt[1] - 140) < 1e-12 && Math.abs(snapHint.pt[0] - 203.3) < 1e-9,
        'snap point is the foot on the wall');
  onMouseDown(ev(203.3, 143));
  check(drawPts[0][1] === 140, 'placed point lies exactly on the wall');
  onMouseDown(ev(203.3, 60));
  finishDraw(false);
  const P = layer.source_paths[1];
  check(P.points[0][1] === 140, 'drawn path starts on the rectangle');
  // --- Alt disables snapping
  setTool('draw');
  onMouseDown(Object.assign(ev(100, 143), { altKey: true }));
  check(Math.abs(drawPts[0][1] - 143) < 1e-6, 'alt: free placement (no snap)');
  drawPts = []; setTool('edit');
  // --- line endpoint drag snaps (edge), and onto another path's end (end)
  addPrimitive('LinePath');                    // (140,200) → (260,200)
  const L = layer.source_paths[layer.source_paths.length - 1];
  selectedId = L.id;
  onMouseDown(ev(140, 200));                   // grab start handle
  onMouseMove(ev(137, 205));                   // near the left wall x=140
  check(snapHint && snapHint.kind === 'edge', 'dragging end shows snap');
  onMouseUp();
  check(L.start[0] === 140 && Math.abs(L.start[1] - 205) < 1e-9, 'line start snapped onto left wall');
  check(snapHint === null, 'hint cleared on mouse up');
  selectedId = L.id;
  onMouseDown(ev(260, 200));                   // end handle
  onMouseMove(ev(205, 62));                    // near the drawn path's free end (203.3, 60)
  onMouseUp();
  check(L.end[0] === 203.3 && L.end[1] === 60, 'line end snapped onto the other path end');
  // --- body drag of an open path snaps its nearest end
  addPrimitive('LinePath');
  const M = layer.source_paths[layer.source_paths.length - 1];
  M.start = [300, 300]; M.end = [380, 300]; _computePrimitivePoints(M);
  selectedId = M.id;
  onMouseDown(ev(340, 300));
  onMouseMove(ev(340 - 37, 300 - 52));         // start → (263, 248): near top-right corner region
  onMouseUp();
  const onWall = Math.abs(M.start[0] - 260) < 1e-9 || Math.abs(M.start[1] - 260) < 1e-9 ||
                 Math.abs(M.end[0] - 260) < 1e-9 || Math.abs(M.end[1] - 260) < 1e-9;
  check(onWall, 'body drag snapped an end onto the rectangle');
  // --- network drawing: trimmed sources drawn from backend pieces
  networkInfo = { components: [{ sources: [R.id, P.id], lattices: [] }],
                  junctions: [{ x: 198, y: 140, key: 'p1|p2#0', corner: true, treatment: 'miter', radius: 2 },
                              { x: 203.3, y: 150, key: null, corner: false }],
                  modified_sources: [R.id], regions: [], infills: [],
                  source_networks: [{ id: 'N1', sources: [R.id, P.id], wall: null }] };
  derivedPaths = [{ id: R.id + '~n0', treatment_id: 'network_src', source_id: R.id,
                    role: 'free', closed: false, points: [[208, 140], [260, 140], [260, 260], [140, 260], [140, 140], [198, 140]] }];
  check(_networkTrimmed().has(R.id), 'trimmed set from network info');
  repaint();                                   // must not throw
  updatePathList();
  const nets = _networkOf();
  check(nets[R.id].label === 'N1' && nets[P.id].label === 'N1', 'network badge N1 for both');
  // --- junctions: click a diamond → junction panel → treatment override
  selectedId = null; selectedOpeningId = null;
  onMouseDown(ev(198.5, 140.5)); onMouseUp();
  check(selectedJunction && selectedJunction.key === 'p1|p2#0', 'clicking a diamond selects the junction');
  const pathPanelEl = document.getElementById('path-props');
  const junctionPanelEl = document.getElementById('junction-props');
  const rebuild = () => { pathPanelEl.children.length = 0; junctionPanelEl.children.length = 0; updatePropPanel(); };  // stub: innerHTML='' doesn't clear
  // a selected junction shows its own section of the DESIGN sidebar (next to Wall
  // Geometry), separate from the path Properties
  let panelEl = junctionPanelEl;
  rebuild();
  const findRow = label => panelEl.children.find(r => r.children && r.children[0] &&
                                                 r.children[0].textContent === label);
  check(document.getElementById('junction-props-section').style.display === '' &&
        document.getElementById('path-props-section').style.display === 'none' &&
        pathPanelEl.children.length === 0, 'junction panel renders in its own section, not in the path Properties');
  const tRow = findRow('Treatment');
  check(!!tRow, 'junction panel has a Treatment row');
  tRow.children[1].value = 'Rounded'; tRow.children[1].onchange();
  const ov = layer.junction_overrides.find(o => o.key === 'p1|p2#0');
  check(ov && ov.treatment === 'round' && ov.radius === 2, 'Rounded override stored for that junction only');
  check(buildPayload().junction_overrides.length === 1, 'payload carries junction overrides');
  rebuild();
  const rRow = findRow('Radius');
  check(!!rRow, 'radius row shown for a rounded junction');
  rRow.children[1].value = '0.75'; rRow.children[1].onchange();
  check(ov.radius === 0.75, 'junction radius edited independently');
  selectJunction(networkInfo.junctions[1]); rebuild();
  check(!findRow('Treatment'), 'internal (non-corner) junction offers no corner treatment');
  // --- per-path Corner R (rect) is the path's own, not the layer's
  selectedJunction = null; selectedId = R.id; panelEl = pathPanelEl; rebuild();
  check(document.getElementById('junction-props-section').style.display === 'none' &&
        document.getElementById('path-props-section').style.display === '',
        'selecting a path hides the junction panel and shows path Properties');
  const cRow = findRow('Corner R');
  check(!!cRow, 'rectangle properties show Corner R');
  cRow.children[1].value = '12'; cRow.children[1].onchange();
  check(R.corner_radius === 12 && R.points.length > 4, 'rect rounded by its own Corner R');
  check(!layer.corner_radius, 'layer-wide corner radius untouched');
  check(buildPayload().source_paths.find(x => x.id === R.id).corner_radius === 12, 'payload carries path corner R');
  // --- infill: added to the selected path's wall region
  addInfill();
  check(layer.infills.length === 1 && layer.infills[0].path_id === R.id, 'infill anchored to the selected wall');
  check(buildPayload().infills[0].params.spacing === 20, 'infill default spacing 20');
  document.getElementById('infill-list').children.length = 0; updateInfillList();
  check(document.getElementById('infill-list').children.length === 1, 'infill panel lists it');
  // --- property header names the selected path
  selectedId = P.id; rebuild();
  const head = panelEl.children.find(c => c.className === 'prop-header');
  check(head && head.textContent === P.label, 'properties header names the selected path');
  head.onmouseenter();
  check(highlightPathId === P.id, 'hovering the header highlights the path');
  head.onmouseleave();
  // --- network wall thickness, inherited by members. The Wall Network is a
  // persistent Design-sidebar section (2026-10-06; it used to live in the
  // selected path's Properties): no selection needed.
  const netEl = document.getElementById('network-props');
  const netRow = label => netEl.children.find(r => r.children && r.children[0] && r.children[0].textContent === label);
  const keepSel = selectedId;
  selectedId = null; netEl.children.length = 0; updatePropPanel();
  check(document.getElementById('network-section').style.display === '' && !!netRow('Network Wall Thickness'),
        'Wall Network section is accessible with NO path selected');
  selectedId = keepSel; rebuild();
  check(!findRow('Network Wall Thickness') &&
        panelEl.children.some(c => (c.textContent || '').startsWith('Member of wall network N1')),
        'the path Properties show membership, the settings live in Wall Network');
  netEl.children.length = 0; updateNetworkSection();
  const nRow = netRow('Network Wall Thickness');
  check(!!nRow, 'network panel offers Network Wall Thickness');
  nRow.children[1].value = '10'; nRow.children[1].onchange();
  check(layer.network_walls.length === 1 && layer.network_walls[0].thickness === 10,
        'one network wall applies to every member');
  check(buildPayload().network_walls.length === 1, 'payload carries the network wall');
  rebuild();
  // two networks: a compact selector picks which one is edited
  const savedNI = networkInfo;
  networkInfo = { ...networkInfo, source_networks: [{ id: 'N1', sources: [R.id, P.id], wall: null },
                                                    { id: 'N2', sources: ['x1', 'x2'], wall: null }] };
  const keep2 = selectedId; selectedId = null;
  netEl.children.length = 0; updateNetworkSection();
  const pick = netRow('Network');
  check(!!pick && pick.children[1].children.length === 2, 'several networks: a compact selector');
  pick.children[1].value = pick.children[1].children[1].value; pick.children[1].onchange();
  check(networkPick === 'N2', 'the selector switches the edited network');
  selectedId = keep2; netEl.children.length = 0; updateNetworkSection();
  check(networkPick === 'N1', 'selecting a member path shows its own network');
  networkInfo = savedNI;
  rebuild();
  check(!findRow('Wall Thickness'), 'inheriting path shows the inherited wall, not an editor');
  const ovBtn = panelEl.children.find(c => c.textContent === 'Override for this path');
  check(!!ovBtn, 'inheriting path offers an override');
  ovBtn.onclick(); rebuild();
  const wRow = findRow('Wall Thickness');
  check(P.wall && P.wall.thickness === 10 && !!wRow, 'override copies the network wall onto the path');
  wRow.children[1].value = '8'; wRow.children[1].onchange(); rebuild();
  check(P.wall.thickness === 8, 'own wall thickness edited');
  const aRow = findRow('Wall Alignment');
  check(aRow && aRow.children[1].children.map(o => o.value).join() === 'Centered,Left,Right',
        'open path: Centered / Left / Right alignment');
  check(!!findRow('Print reference line'), 'centered wall offers Print reference line');
  aRow.children[1].value = 'Left'; aRow.children[1].onchange(); rebuild();
  check(P.wall.align === 'left' && !findRow('Print reference line'), 'Left alignment stored');
  const useNet = panelEl.children.find(c => typeof c.textContent === 'string' &&
                                           c.textContent.startsWith('Use network'));
  useNet.onclick();
  check(P.wall === null, 'back to inheriting the network wall');
  // --- Wall Thickness 0 inside a thick-walled network = SINGLE BEAD (stabilization
  // 2026-10-06): an explicit override, not "inherit 10 in again", never deleted
  panelEl.children.find(c => c.textContent === 'Override for this path').onclick(); rebuild();
  const zRow = findRow('Wall Thickness');
  zRow.children[1].value = '0'; zRow.children[1].onchange(); rebuild();
  check(P.wall && P.wall.thickness === 0, '0 stores the explicit single-bead override');
  check(findRow('Wall Thickness').children[2].textContent.includes('Single bead'), '0 reads "Single bead"');
  check(panelEl.children.some(c => (c.textContent || '').startsWith('Single bead — overrides network N1')),
        'the note explains the single-bead override and its return lanes / no lattice');
  check(buildPayload().source_paths.find(s => s.id === P.id).wall.thickness === 0,
        'the payload carries the single-bead override');
  panelEl.children.find(c => typeof c.textContent === 'string' && c.textContent.startsWith('Use network')).onclick();
  check(P.wall === null, 'single bead → back to the network wall');
  // --- path picker: hover / arrow keys highlight the candidate path
  const pk = addPathPickerRow({ appendChild() {} }, 'Source', P.id, id => { picked = id; });
  let picked = null;
  pk.open();
  check(pk.isOpen() && highlightPathId === P.id, 'opening the picker highlights the current path');
  pk.btn.onkeydown({ key: 'ArrowDown', preventDefault() {} });
  const second = pk.items[pk.active].id;
  check(highlightPathId === second, 'arrow key moves the canvas highlight');
  pk.btn.onkeydown({ key: 'Enter', preventDefault() {} });
  check(picked === second && highlightPathId === null && !pk.isOpen(), 'Enter selects; highlight cleared');
  // --- routing options
  document.getElementById('route-returns').checked = false;
  document.getElementById('route-closed').checked = true;
  onOverrideChange();
  check(buildPayload().return_paths === false && buildPayload().prefer_closed === true,
        'routing toggles reach the payload');
  // --- payload carries region overrides; deleting a path cascades them
  layer.region_overrides.push({ id: 'r1', path_id: P.id, s: 10, offset: 2, kind: 'wall' });
  check(buildPayload().region_overrides.length === 1, 'payload has region overrides');
  deletePath(P.id);
  check(layer.region_overrides.length === 0, 'deleting the path removes its overrides');
  deletePath(R.id);
  check(layer.infills.length === 0, 'deleting the anchor path removes its infill');
  clearAll();
  check(networkInfo === null && layer.region_overrides.length === 0, 'clear all resets network state');
  check(layer.network_walls.length === 0 && layer.return_paths === true, 'clear all resets walls / routing');
  // --- interactive shape tools: clicking a tool creates nothing
  for (const t of ['line', 'rect', 'circle', 'ellipse']) {
    setTool(t);
    check(layer.source_paths.length === 0, t + ' tool: nothing created until placed');
  }
  setTool('rect');
  onMouseDown(ev(100, 100)); onMouseUp();
  check(layer.source_paths.length === 0 && drawPts.length === 1, 'rect: first corner placed, no shape yet');
  onMouseMove(ev(220, 180));
  onMouseDown(ev(220, 180)); onMouseUp();
  const RR = layer.source_paths[0];
  check(RR && RR.type === 'RectanglePath' && Math.abs(RR.w - 120) < 1e-6 && Math.abs(RR.h - 80) < 1e-6,
        'rect: two clicks → 120 × 80');
  check(tool === 'edit' && selectedId === RR.id && RR.label === 'Rect 1', 'back to edit, selected, labelled Rect 1');
  setTool('circle');
  onMouseDown(ev(300, 300)); onMouseUp(); onMouseDown(ev(340, 300)); onMouseUp();
  const CC = layer.source_paths[1];
  check(CC.type === 'CirclePath' && Math.abs(CC.radius - 40) < 1e-6 && CC.cx === 300, 'circle: centre then radius');
  setTool('ellipse');
  onMouseDown(ev(50, 300)); onMouseUp(); onMouseDown(ev(90, 320)); onMouseUp();
  const EE = layer.source_paths[2];
  check(EE.type === 'EllipsePath' && Math.abs(EE.rx - 40) < 1e-6 && Math.abs(EE.ry - 20) < 1e-6, 'ellipse: centre then corner');
  setTool('line');                                      // press-drag-release
  onMouseDown(ev(160, 60)); onMouseMove(ev(160, 120)); onMouseUp({});
  const LL = layer.source_paths[3];
  check(LL && LL.type === 'LinePath' && LL.label === 'Line 1', 'line: drag creates Line 1');
  setTool('line');
  onMouseDown(ev(10, 10)); onMouseDown(ev(30, 10));
  check(layer.source_paths[4].label === 'Line 2', 'second line auto-numbered Line 2');
  setTool('line');
  onMouseDown(ev(203, 104));                           // near the rect's bottom edge y=100
  check(Math.abs(drawPts[0][1] - 100) < 1e-9, 'shape creation snaps onto walls');
  drawPts = []; setTool('edit');
  setTool('rect'); onMouseDown(ev(5, 5)); setTool('edit');
  check(layer.source_paths.length === 5, 'leaving the tool mid-shape creates nothing');
  console.log('UI NETWORK SMOKE PASSED');
})();
`);
