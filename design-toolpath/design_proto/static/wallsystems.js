// ===========================================================================
// WALL SYSTEMS workspace (2026-10-11) — Designer → Assembly → WALL SYSTEMS →
// Print Simulation.
//
// The PROJECT's printable constructions (`project.wall_systems`,
// design_proto/walls.py — 2026-10-15: Wall Groups removed):
//   { id, name, type, params, web, members: [wall ids] }
// assigned to WALLS (a checklist: several at once), the same in every layout
// state. A wall's system: the one listing it, else none (Hollow: its two
// skins; a THIN wall stays a single bead). One system may span walls of
// different thickness.
//
// THICKNESS is architecture: edited here it changes the wall's OWN envelope
// (`path.envelope`, its owner design) via window.Designer.setWallThickness;
// the Designer and Assembly follow, generated geometry becomes OUTDATED. The
// same holds for the "Architecture" section (junction / end-cap / corner
// shape — the Designer's own controls, `_wsysRenderArch`): a second editing
// surface for the same canonical state, never Wall-System properties.
//
// Nothing is generated while editing: "Generate Wall Systems" runs the
// existing full-resolution pipeline for the whole assembled building
// (/api/wallsystems/generate — a cancellable background job; every layer's
// printable geometry + the bead-level support report). Status: NOT GENERATED
// · GENERATING · CURRENT · OUTDATED (any Designer / Assembly / Wall System
// change since — outdated geometry stays visible as a labelled reference).
// The Print Simulation plans only from CURRENT generated geometry.
//
// INSPECTION (like the Print Simulation's layer inspection): the Base design
// (first view), any layer (◀ ▶ / number / slider), Top View, a 3D orbit of the
// layer over the one below, Envelope | Generated Paths (each generated layer
// fetched once from the generation's cache — never regenerated), the selected
// layer's support findings. After generating, Generated Paths are shown.
// ===========================================================================
const WSYS_COLORS = ['#ff9f43', '#2ecc71', '#e056fd', '#f9ca24', '#48dbfb', '#ff6b6b', '#a29bfe', '#55efc4'];
const WSYS_STATUS_LABEL = { not_generated: 'NOT GENERATED', generating: 'GENERATING', current: 'CURRENT',
                            outdated: 'OUTDATED' };

const wsys = {
  pick: null,                       // the system shown in the editor
  gen: null,                        // the last generation {key, layers, support, stats, systems, clientKey, body}
  currentKey: null,                 // the current design state's generation key
  inflight: null,                   // { job, key, promise }
  error: null,
  layer: -1,                        // -1 = the Base design view, else a layer index
  display: 'envelope',              // 'envelope' | 'generated'
  view: { mode: 'top', zoom: 1, px: 0, py: 0, yaw: -Math.PI / 4, pitch: 0.6 },
  cache: {},                        // `${clientKey}|${index}` → generated layer geometry (index -1: Base design)
  pending: {},                      // the same keys while their request is in flight
  baseEnv: null,                    // { key, polylines } the Base design's envelope
  hist: { undo: [], redo: [] },
  drag: null, seq: 0, stats: { layerFetches: 0 },
  // PRINTABILITY INSPECTION (M2): which findings are listed (FINAL bead level |
  // PRELIMINARY architectural) and the one selected (jumped to, highlighted)
  findView: 'final', sel: null,             // sel: { which, id }
};
const WSYS_CATEGORY = {
  architectural: ['architectural', 'Architectural overhang (also flagged by the envelope check)'],
  lattice: ['lattice', 'Unsupported lattice / Wall System paths'],
  construction: ['construction', 'Skins, caps or jambs (construction detail)'],
  continuity: ['continuity', 'Routing / continuity'],
};

function _wsysList() { return window.Designer.wallSystems(); }
function _wsysCatalog() { return window.Designer.wallCatalog(); }
function _wsysSys(id) { return _wsysList().find(s => s.id === id) || null; }
function _wsysLabel(s) { return (s && (s.name || s.id)) || ''; }
function _wsysTypeLabel(t) { return (WALL_SYSTEMS[t] && WALL_SYSTEMS[t].label) || t; }

// the system a wall uses (the backend rule: walls.system_of — the first listing it)
function wsysSystemOf(wallId) {
  return _wsysList().find(s => (s.members || []).includes(wallId)) || null;
}

// ---- generation status --------------------------------------------------------------
async function _wsysKeys() {
  const designs = await window.Designer.designsForBackend();
  const st = JSON.parse(JSON.stringify(asm.state));
  delete st.print_speeds; delete st.machine;
  const ws = JSON.parse(JSON.stringify(_wsysList()));
  return { designs, assembly: JSON.parse(JSON.stringify(asm.state)), wall_systems: ws,
           key: JSON.stringify([st, designs, ws]) };
}

function wsysStatus() {
  if (wsys.inflight) return 'generating';
  if (!wsys.gen) return 'not_generated';
  return wsys.currentKey !== null && wsys.gen.clientKey === wsys.currentKey ? 'current' : 'outdated';
}

// the current design state changed (or may have): recompute its key
// (light: only the status — a parameter being typed keeps its field)
async function wsysNoteState(light = false) {
  try { wsys.currentKey = (await _wsysKeys()).key; } catch (e) { /* (no designs yet) */ }
  if (light) _wsysRenderStatus(); else wsysRender();
  return wsysStatus();
}

// ---- editing (no geometry is generated) ------------------------------------------------
// (a snapshot = the Wall Systems + the architectural thicknesses this workspace may edit upstream)
function _wsysSnap() {
  return JSON.stringify({ ws: _wsysList(), arch: window.Designer.archThickness ? window.Designer.archThickness() : null,
                          geom: window.Designer.wallGeometry ? window.Designer.wallGeometry() : null });
}
function _wsysRestore(snap) {
  const s = JSON.parse(snap), now = JSON.parse(_wsysSnap());
  window.Designer.setWallSystems(s.ws);
  if (s.arch && JSON.stringify(s.arch) !== JSON.stringify(now.arch)) window.Designer.setArchThickness(s.arch);
  // (wall geometry: restored only on the design it was recorded for)
  if (s.geom && now.geom && s.geom.design === now.geom.design && JSON.stringify(s.geom) !== JSON.stringify(now.geom))
    window.Designer.setWallGeometry(s.geom);
}
function _wsysEdit(fn, rebuild = true) {
  const before = _wsysSnap();
  fn();
  if (_wsysSnap() === before) return;
  wsys.hist.undo.push(before);
  if (wsys.hist.undo.length > 200) wsys.hist.undo.shift();
  wsys.hist.redo = [];
  wsysNoteState();
  if (rebuild) wsysRender();
}
function wsysUndo() {
  const s = wsys.hist.undo.pop();
  if (s == null) return;
  wsys.hist.redo.push(_wsysSnap());
  _wsysRestore(s);
  wsysNoteState();
}
function wsysRedo() {
  const s = wsys.hist.redo.pop();
  if (s == null) return;
  wsys.hist.undo.push(_wsysSnap());
  _wsysRestore(s);
  wsysNoteState();
}
// a wall's ARCHITECTURAL thickness, edited upstream (its Wall Group's, shared;
// else its own envelope) — never a second value; generation becomes OUTDATED
function wsysSetThickness(wallId, t) { _wsysEdit(() => window.Designer.setWallThickness(wallId, t)); }

function _wsysNewId() {
  const taken = new Set(_wsysList().map(s => s.id));
  let k = 1;
  while (taken.has(`S${k}`)) k++;
  return `S${k}`;
}
function wsysNew(type = 'skin_web', assign = {}) {
  let created = null;
  _wsysEdit(() => {
    const L = _wsysList();
    let k = L.length + 1;
    while (L.some(s => s.name === `Wall System ${k}`)) k++;
    const s = { id: _wsysNewId(), name: `Wall System ${k}`, type: 'skin_web', params: {}, members: [],
                web: { pattern: 'zigzag', params: { spacing: 20 }, variation_index: 0 } };
    _wsSetType(s, type);
    L.push(s);
    for (const w of assign.members || []) _wsysAssignWall(s, w);
    wsys.pick = s.id;
    created = s;
  });
  return created;
}
function _wsysAssignWall(s, wid) {           // a wall is listed by at most ONE system
  for (const x of _wsysList()) x.members = (x.members || []).filter(m => m !== wid);
  if (s && !s.members.includes(wid)) s.members.push(wid);
}
function wsysAssignWall(sid, wid) { _wsysEdit(() => _wsysAssignWall(_wsysSys(sid), wid)); }
// several walls at once (one undo step): the checklist's "all unassigned" etc.
function wsysAssignWalls(sid, wids) { _wsysEdit(() => { const s = _wsysSys(sid); for (const w of wids) _wsysAssignWall(s, w); }); }
function wsysUnassignWall(sid, wid) { _wsysEdit(() => { const s = _wsysSys(sid); if (s) s.members = s.members.filter(m => m !== wid); }); }
function wsysSetType(sid, type) { _wsysEdit(() => { const s = _wsysSys(sid); if (s) _wsSetType(s, type); }); }
function wsysRename(sid, name) { _wsysEdit(() => { const s = _wsysSys(sid); if (s) s.name = (name || '').trim() || s.id; }); }
function wsysSetParam(sid, name, value) { _wsysEdit(() => { const s = _wsysSys(sid); if (s) s.params[name] = value; }, false); }
function wsysDelete(sid) {
  _wsysEdit(() => {
    window.Designer.setWallSystems(_wsysList().filter(s => s.id !== sid));
    if (wsys.pick === sid) wsys.pick = null;
  });
}

// the walls effectively using a system, and their architectural thicknesses
function _wsysWallsOf(s) {
  return _wsysCatalog().walls.filter(w => wsysSystemOf(w.id) === s);
}
function _wsysThicknesses(s) {
  const th = new Set();
  for (const w of _wsysWallsOf(s)) if (w.thickness > 0) th.add(w.thickness);
  return [...th].sort((a, b) => a - b);
}

// ---- generation -----------------------------------------------------------------------
async function wsysGenerate() {
  const I = wsys.inflight;
  const R = { job: 'wsys-' + (++wsys.seq) + '-' + Date.now(), key: null };
  wsys.inflight = R; wsys.error = null;             // GENERATING at once
  wsysRender();
  let K;
  try { K = await _wsysKeys(); } catch (e) {
    if (wsys.inflight === R) { wsys.inflight = null; wsys.error = e.message; wsysRender(); }
    return;
  }
  if (wsys.inflight !== R) return;                  // (cancelled meanwhile)
  wsys.currentKey = K.key;
  if (I && I.key === K.key && I.promise) { wsys.inflight = I; return I.promise; }   // (already generating this)
  R.key = K.key;
  const body = { assembly: K.assembly, designs: K.designs, wall_systems: K.wall_systems };
  const t0 = typeof performance !== 'undefined' ? performance.now() : 0;
  R.promise = (async () => {
    try {
      const res = await fetch('/api/wallsystems/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                            body: JSON.stringify({ ...body, job: R.job }) });
      const data = await res.json();
      if (wsys.inflight !== R) return;          // superseded / cancelled: never overwrite a newer state
      if (data.cancelled) { wsys.error = 'Generation cancelled.'; return; }
      if (data.error) { wsys.error = 'Generation failed: ' + data.error; return; }
      wsys.gen = { ...data, clientKey: K.key, body, ms: (typeof performance !== 'undefined' ? performance.now() : 0) - t0 };
      if (wsys.layer >= data.layers.length) wsys.layer = data.layers.length - 1;
      // (2026-10-11 correction) what was generated is SHOWN: Generated Paths
      // of the selected layer — or of the Base design when that is the view
      wsys.display = 'generated';
      _wsysFetchLayer(wsys.layer);
    } catch (e) {
      if (wsys.inflight === R) wsys.error = 'Generation failed: ' + e.message;
    } finally {
      if (wsys.inflight === R) wsys.inflight = null;
      wsysRender();
      if (typeof simRenderPanels === 'function') simRenderPanels();
      if (typeof _asmStructureHead === 'function') _asmStructureHead();
    }
  })();
  return R.promise;
}

function wsysCancel() {
  const R = wsys.inflight;
  wsys.inflight = null;
  if (R) fetch('/api/assembly/print_plan/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                    body: JSON.stringify({ job: R.job }) }).catch(() => {});
  wsys.error = R ? 'Generation cancelled.' : wsys.error;
  wsysRender();
}

// one GENERATED layer of the shown generation (read-only on the server; i = -1:
// the Base design's generated geometry); each fetched once per generation
async function _wsysFetchLayer(i) {
  const G = wsys.gen;
  if (!G) return null;
  const k = `${G.clientKey}|${i}`;
  if (wsys.cache[k]) return wsys.cache[k];
  if (wsys.pending[k]) return wsys.pending[k];    // (in flight: the same request)
  const p = _wsysFetchLayerNow(G, i, k);
  wsys.pending[k] = p;
  try { return await p; } finally { delete wsys.pending[k]; }
}

async function _wsysFetchLayerNow(G, i, k) {
  try {
    wsys.stats.layerFetches++;
    const root = (window.Designer.listDesigns().find(d => d.parent == null) || {}).id;
    const res = await fetch('/api/wallsystems/layer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                       body: JSON.stringify({ ...G.body, ...(i < 0 ? { design: root } : { index: i }) }) });
    const data = await res.json();
    if (data.error) {
      if (data.outdated && wsys.gen === G) wsys.error = 'The generated geometry of that layer is no longer available — generate again.';
      if (wsys.gen === G) wsysRender();
      return null;
    }
    wsys.cache[k] = data;
    const keys = Object.keys(wsys.cache);
    if (keys.length > 400) delete wsys.cache[keys[0]];
    if (wsys.gen === G && wsys.layer === i) { _wsysRenderLayer(); wsysDraw(); }
    return data;
  } catch (e) { return null; }
}

// ---- inspection -----------------------------------------------------------------------
function _wsysLayers() { return (asm.resolved && asm.resolved.instances) || []; }
function _wsysCount() {
  return wsys.gen && wsysStatus() !== 'outdated' && wsys.display === 'generated' ? wsys.gen.layers.length
    : (_wsysLayers().length || (wsys.gen ? wsys.gen.layers.length : 0));
}

function wsysShowBase() {
  wsys.layer = -1; wsys.view.mode = 'top'; _wsysFit(); wsys.view.focus = null;
  if (wsys.display === 'generated') _wsysFetchLayer(-1);
  wsysRender();
}
function wsysSelectLayer(i, keepFocus = false) {
  const n = _wsysCount();
  if (!n) return;
  if (!keepFocus) { wsys.view.focus = null; }
  wsys.layer = Math.max(0, Math.min(n - 1, Math.round(+i) || 0));
  if (wsys.display === 'generated') _wsysFetchLayer(wsys.layer);     // (cached per generation × layer)
  wsysRender();
}
function wsysStep(d) { wsysSelectLayer((wsys.layer < 0 ? (d > 0 ? -1 : 0) : wsys.layer) + d); }
function wsysTopView() { wsys.view.mode = 'top'; _wsysFit(); wsys.view.focus = null; wsysRender(); }

// ---- printability inspection (M2) -------------------------------------------------------
function wsysSetFindings(v) { wsys.findView = v; wsysRender(); }

// the classified FINAL findings of the shown generation (bead level)
function _wsysFinal() {
  const G = wsys.gen;
  if (!G) return [];
  const cls = Object.fromEntries(((G.inspection || {}).findings || []).map(x => [x.id, x]));
  return (G.support.findings || []).map(f => ({ ...f, which: 'final', category: (cls[f.id] || {}).category || 'construction',
                                                role: (cls[f.id] || {}).role, architectural: (cls[f.id] || {}).architectural }));
}
// the PRELIMINARY architectural findings (the Assembly's envelope check)
function _wsysPreliminary() {
  const S = asm.resolved && asm.resolved.support;
  return S && S.level === 'envelope' ? (S.findings || []).map(f => ({ ...f, which: 'preliminary' })) : [];
}

// a finding clicked: its layer, its kind of geometry, centred + highlighted
function wsysSelectFinding(which, id) {
  const f = (which === 'final' ? _wsysFinal() : _wsysPreliminary()).find(x => x.id === id);
  if (!f) return;
  wsys.sel = { which, id };
  wsys.display = which === 'final' && wsys.gen ? 'generated' : 'envelope';
  wsys.view.mode = 'top';
  Object.assign(wsys.view, { zoom: 1, px: 0, py: 0 });
  wsys.view.focus = { x: f.centre[0], y: f.centre[1], span: Math.max(f.span || 0, f.depth || 0) };
  wsysSelectLayer(f.layer, true);
}
function wsysSelectContinuity(k) {
  const c = ((wsys.gen && wsys.gen.inspection && wsys.gen.inspection.continuity) || [])[k];
  if (!c) return;
  wsys.sel = { which: 'continuity', id: k };
  wsys.display = 'generated';
  wsysSelectLayer((c.layers || [0])[0]);
}
function wsysSelectOpening(k) {
  const o = ((wsys.gen && wsys.gen.inspection && wsys.gen.inspection.openings) || [])[k];
  if (!o) return;
  wsys.sel = { which: 'opening', id: k };
  wsys.display = 'generated';
  wsysSelectLayer(o.layer != null ? o.layer : 0);
}
function _wsysSelected() {
  const s = wsys.sel;
  if (!s || s.which === 'continuity' || s.which === 'opening') return null;
  return (s.which === 'final' ? _wsysFinal() : _wsysPreliminary()).find(x => x.id === s.id) || null;
}
function wsysOrbit() { wsys.view.mode = 'orbit'; Object.assign(wsys.view, { zoom: 1, px: 0, py: 0 }); wsysRender(); }
function wsysSetDisplay(d) {
  wsys.display = d;
  if (d === 'generated') _wsysFetchLayer(wsys.layer);
  wsysRender();
}

async function _wsysBaseEnvelope() {
  const root = window.Designer.listDesigns().find(d => d.parent == null);
  if (!root) return null;
  if (asm.geometry && asm.geometry[root.id] && asm.geometry[root.id].envelope)
    return asm.geometry[root.id].polylines || [];
  const designs = await window.Designer.designsForBackend();
  const key = JSON.stringify(designs);
  if (wsys.baseEnv && wsys.baseEnv.key === key) return wsys.baseEnv.polylines;
  try {
    const r = await fetch('/api/assembly/geometry', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                     body: JSON.stringify({ designs, ids: [root.id], envelope: true }) });
    const data = await r.json();
    wsys.baseEnv = { key, polylines: ((data.designs || {})[root.id] || {}).polylines || [] };
    return wsys.baseEnv.polylines;
  } catch (e) { return null; }
}

// what is drawn for a layer: [{ pts (placed), closed, role, system }]
function _wsysPlace(t, pts) { return pts.map(([x, y]) => [t.scale * x + t.tx, t.scale * y + t.ty]); }
function _wsysEnvelopeOf(i) {
  const L = typeof asmStack === 'function' ? asmStack()[i] : null;
  if (!L) return [];
  const t = L.transform || { scale: 1, tx: 0, ty: 0 };
  return (L.polylines || []).map(p => ({ pts: _wsysPlace(t, p.pts), closed: !!p.closed, role: p.role || 'ring' }));
}
function _wsysSystemColor(id) {
  const ids = Object.keys((wsys.gen && wsys.gen.systems) || {});
  const k = ids.indexOf(id);
  return k < 0 ? '#4a9eff' : WSYS_COLORS[k % WSYS_COLORS.length];
}
function _wsysGeneratedOf(i) {
  const G = wsys.gen;
  const d = G && wsys.cache[`${G.clientKey}|${i}`];
  if (!d) return null;
  const t = d.transform || { scale: 1, tx: 0, ty: 0 };
  return d.polylines.map(p => ({ pts: _wsysPlace(t, p.pts), closed: !!p.closed, role: 'path', system: p.system }));
}
function _wsysShown(i) {
  if (wsys.display === 'generated' && wsys.gen) return _wsysGeneratedOf(i);
  if (i < 0) return (wsys._base || []).map(p => ({ pts: p.pts, closed: !!p.closed, role: p.role || 'ring' }));
  return _wsysEnvelopeOf(i);
}

// WHAT the canvas shows (also the test hook): the view, the geometry kind and
// the status of the generated geometry
function wsysScene() {
  const i = wsys.layer, gen = wsys.display === 'generated' && !!wsys.gen;
  const items = _wsysShown(i);
  return { layer: i, view: wsys.view.mode, kind: gen ? 'generated' : 'envelope',
           status: wsys.display === 'generated' ? (wsys.gen ? wsysStatus() : 'not_generated') : null,
           items: items ? items.length : null, paths: items ? items.filter(it => it.role === 'path').length : 0,
           loading: gen && !items, focus: wsys.view.focus || null,
           highlight: (() => { const f = _wsysSelected(); return f && f.layer === i ? f.id : null; })() };
}
function _wsysZ(i) {
  const L = _wsysLayers()[i] || (wsys.gen && wsys.gen.layers[i]);
  return L ? L.z_top : 0;
}

function _wsysBounds(items) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const it of items || []) for (const [x, y] of it.pts) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  return isFinite(x0) ? [x0, y0, x1, y1] : [0, 0, 100, 100];
}
function _wsysFit() { Object.assign(wsys.view, { zoom: 1, px: 0, py: 0 }); }

function wsysDraw() {
  const cv = document.getElementById('wsys-canvas'), wrap = document.getElementById('wsys-canvas-wrap');
  if (!cv || !cv.getContext || (typeof currentWorkspace !== 'undefined' && currentWorkspace !== 'wallsystems')) return;
  const W = Math.max(200, (wrap && wrap.clientWidth) || 800), H = Math.max(200, (wrap && wrap.clientHeight) || 600);
  if (cv.width !== W) cv.width = W;
  if (cv.height !== H) cv.height = H;
  const g = cv.getContext('2d');
  g.fillStyle = '#1a1a1a'; g.fillRect(0, 0, W, H);
  const i = wsys.layer;
  const items = _wsysShown(i);
  g.font = '12px monospace';
  if (!items) {
    g.fillStyle = '#888';
    g.fillText(wsys.gen ? 'Loading the generated layer…' : 'NOT GENERATED — press "Generate Wall Systems".', 20, 36);
    return;
  }
  const below = i > 0 && wsys.view.mode === 'orbit' ? (_wsysShown(i - 1) || _wsysEnvelopeOf(i - 1)) : [];
  const B = _wsysBounds(items.length ? items : below);
  const F = wsys.view.focus;                     // (a selected finding: centred, close up)
  const cx = F ? F.x : (B[0] + B[2]) / 2, cy = F ? F.y : (B[1] + B[3]) / 2;
  const span = F ? Math.max(4 * (F.span || 0), 60) : Math.max(B[2] - B[0], B[3] - B[1], 1);
  const v = wsys.view, k = 0.86 * Math.min(W, H) / span * v.zoom;
  let P;
  if (v.mode === 'orbit') {
    const z0 = i >= 0 ? _wsysZ(i) : 0, cyw = Math.cos(v.yaw), syw = Math.sin(v.yaw), cp = Math.cos(v.pitch), sp = Math.sin(v.pitch);
    P = (x, y, z) => {
      const X = x - cx, Y = y - cy, Z = (z - z0) * 6;
      const rx = X * cyw - Y * syw, ry = X * syw + Y * cyw;
      return [W / 2 + v.px + k * 0.8 * rx, H / 2 + v.py + k * 0.8 * (ry * sp - Z * cp)];
    };
  } else {
    P = (x, y) => [W / 2 + v.px + k * (x - cx), H / 2 + v.py - k * (y - cy)];
    if (typeof drawPhysicalGrid === 'function') drawPhysicalGrid(g, P, W, H);   // 1 ft squares from the print origin
  }
  const stroke = (list, z, alpha) => {
    for (const it of list) {
      if (it.pts.length < 2) continue;
      g.globalAlpha = alpha;
      g.strokeStyle = it.role === 'path' ? _wsysSystemColor(it.system) : '#6fb1ff';
      g.lineWidth = it.role === 'path' ? 1.4 : 1.2;
      g.beginPath();
      const a = P(it.pts[0][0], it.pts[0][1], z);
      g.moveTo(a[0], a[1]);
      for (let j = 1; j < it.pts.length; j++) { const q = P(it.pts[j][0], it.pts[j][1], z); g.lineTo(q[0], q[1]); }
      if (it.closed) g.closePath();
      g.stroke();
    }
    g.globalAlpha = 1;
  };
  if (below.length) stroke(below, _wsysZ(i - 1), 0.35);
  if (v.mode === 'top' && items.some(it => it.role === 'ring')) {     // the envelope's material, faint
    g.beginPath();
    for (const it of items) {
      if (it.role !== 'ring' || it.pts.length < 3) continue;
      const a = P(it.pts[0][0], it.pts[0][1]);
      g.moveTo(a[0], a[1]);
      for (let j = 1; j < it.pts.length; j++) { const q = P(it.pts[j][0], it.pts[j][1]); g.lineTo(q[0], q[1]); }
      g.closePath();
    }
    g.fillStyle = 'rgba(74,158,255,0.12)';
    g.fill('evenodd');
  }
  stroke(items, i >= 0 ? _wsysZ(i) : 0, 1);
  // MACHINE: the printer's X × Y envelope at this layer (a reference only)
  const mp = typeof machineProfile === 'function' ? machineProfile() : null;
  if (mp) {
    drawMachineEnvelope(g, P, mp, { z: false, zBase: i >= 0 ? _wsysZ(i) : 0 });
    const bb = items.length ? _wsysBounds(items) : null;
    const mw = bb && isFinite(bb[0]) ? machineWarning({ xy_bounds: { min_x: bb[0], min_y: bb[1], max_x: bb[2], max_y: bb[3] } },
                                                      { ...mp, max_build_z: null, z_travel_per_frame: null }) : '';
    if (mw) { g.fillStyle = '#ffb366'; g.font = '11px monospace'; g.fillText('⚠ ' + mw, 14, 34); g.font = '12px monospace'; }
  }
  const selF = _wsysSelected();
  for (const f of _wsysFindings()) {                // support findings of the layer (the span runs)
    const isSel = selF && selF.id === f.id && selF.layer === f.layer;
    g.strokeStyle = isSel ? '#ffffff' : (f.kind === 'bridge' ? '#ff6b6b' : '#ffa94d');
    g.lineWidth = isSel ? 6 : 3;
    for (const run of f.runs || []) {
      if (run.length < 2) continue;
      g.beginPath();
      const a = P(run[0][0], run[0][1], _wsysZ(i));
      g.moveTo(a[0], a[1]);
      for (const q0 of run.slice(1)) { const q = P(q0[0], q0[1], _wsysZ(i)); g.lineTo(q[0], q[1]); }
      g.stroke();
    }
  }
  if (selF && selF.layer === i) {                  // the selected finding: a ring at its centre
    const c = P(selF.centre[0], selF.centre[1], _wsysZ(i));
    g.strokeStyle = '#ffffff'; g.lineWidth = 2;
    g.beginPath(); g.arc(c[0], c[1], 14, 0, 2 * Math.PI); g.stroke();
  }
  const sc = wsysScene();
  const what = (i < 0 ? 'Base design' : `Layer ${i + 1}`) + ' — ' +
    (sc.kind === 'generated' ? `generated wall paths · ${WSYS_STATUS_LABEL[sc.status]}` : 'architectural envelope');
  g.fillStyle = sc.kind !== 'generated' ? '#aaa' : (sc.status === 'current' ? '#6fdc8c' : '#ffb366');
  g.fillText(what, 14, 18);
  if (sc.kind === 'generated' && sc.status === 'outdated') {
    g.fillStyle = 'rgba(255,159,67,0.95)'; g.font = 'bold 13px monospace';
    g.fillText('OUTDATED — generated before the latest Designer / Assembly / Wall System change', 14, H - 16);
  }
}

// support findings of the selected layer: bead-level of the shown generation
// (Generated Paths) or the Assembly's preliminary architectural check
function _wsysFindings() {
  const i = wsys.layer;
  if (i < 0) return [];
  if (wsys.display === 'generated' && wsys.gen) return _wsysFinal().filter(f => f.layer === i);
  return _wsysPreliminary().filter(f => f.layer === i);
}

// ---- panels ---------------------------------------------------------------------------
function _wsysClear(el) { if (!el) return; el.innerHTML = ''; if (Array.isArray(el.children)) el.children.length = 0; }

function wsysRender() {
  _wsysRenderStatus();
  _wsysRenderList();
  _wsysRenderGeometry();
  _wsysRenderArch();
  _wsysRenderMaterial();
  _wsysRenderLayer();
  _wsysRenderFindings();
  wsysDraw();
}

function _wsysFindRow(box, f, label) {
  const r = _el('div', 'wsys-find' + (wsys.sel && wsys.sel.which === f.which && wsys.sel.id === f.id ? ' sel' : ''));
  if (f.category) { const c = _el('span', 'wsys-cat ' + f.category, WSYS_CATEGORY[f.category][0]); r.appendChild(c); }
  r.appendChild(_el('span', null, label));
  r.title = 'Show it: its layer, centred and highlighted';
  r.onclick = () => wsysSelectFinding(f.which, f.id);
  box.appendChild(r);
}
function _wsysFindLabel(f) {
  const what = f.kind === 'bridge' ? 'HEADER NEEDED' : 'INSUFFICIENT SUPPORT';
  const st = f.status === 'supported' ? ' · supported by a header' : f.status === 'overridden' ? ' · ignored (override)' : '';
  return `L${f.layer + 1} · ${what} · ${(+f.span).toFixed(1)} in at (${f.centre[0].toFixed(0)}, ${f.centre[1].toFixed(0)})` +
         (f.kind === 'overlap' ? ` · overlap ${(+f.overlap).toFixed(2)} / ${(+f.min_overlap).toFixed(2)} in` : '') + st;
}

// the FINAL (bead level, authoritative) or PRELIMINARY (architectural) findings
// of the whole stack — grouped, each one click away from its layer
function _wsysRenderFindings() {
  const box = document.getElementById('wsys-findings');
  if (!box) return;
  _wsysClear(box);
  for (const [id, on] of [['wsys-find-final', wsys.findView === 'final'], ['wsys-find-pre', wsys.findView === 'preliminary']]) {
    const b = document.getElementById(id);
    if (b && b.classList) b.classList.toggle('active', on);
  }
  if (wsys.findView === 'preliminary') {
    box.appendChild(_el('div', 'asm-calc', 'PRELIMINARY — the Assembly’s architectural check of the wall envelopes ' +
                                           '(Maximum overhang, openings / headers). Lattice and bead detail are checked only after generation.'));
    const L = _wsysPreliminary();
    if (!asm.resolved) box.appendChild(_el('div', 'asm-calc', 'Open the Assembly once to resolve the stack.'));
    else if (!L.length) box.appendChild(_el('div', 'asm-kv-row', 'No architectural findings.'));
    for (const f of L) _wsysFindRow(box, f, _wsysFindLabel(f));
    return;
  }
  const st = wsys.gen ? wsysStatus() : 'not_generated';
  if (!wsys.gen) {
    box.appendChild(_el('div', 'asm-calc', 'FINAL bead-level findings appear after “Generate Wall Systems”.'));
    return;
  }
  const head = _el('div', 'asm-kv-row');
  head.appendChild(_el('span', null, 'FINAL · bead level of the generated geometry '));
  head.appendChild(_el('span', 'wsys-badge ' + st, WSYS_STATUS_LABEL[st]));
  box.appendChild(head);
  const all = _wsysFinal();
  const open = all.filter(f => f.status === 'needs_header' || f.status === 'insufficient_support');
  if (!all.length) box.appendChild(_el('div', 'asm-kv-row', 'Every layer supported (bead level).'));
  else box.appendChild(_el('div', 'asm-calc', `${open.length} open of ${all.length} findings.`));
  for (const cat of ['architectural', 'lattice', 'construction']) {
    const fs = all.filter(f => f.category === cat);
    if (!fs.length) continue;
    box.appendChild(_el('div', 'asm-calc wsys-cat-head', `${WSYS_CATEGORY[cat][1]} · ${fs.length}`));
    for (const f of fs) _wsysFindRow(box, f, _wsysFindLabel(f));
  }
  const cont = (wsys.gen.inspection && wsys.gen.inspection.continuity) || [];
  if (cont.length) {
    box.appendChild(_el('div', 'asm-calc wsys-cat-head', `${WSYS_CATEGORY.continuity[1]} · ${cont.length}`));
    cont.forEach((c, k) => {
      const sy = (wsys.gen.systems || {})[c.system];
      const r = _el('div', 'wsys-find' + (wsys.sel && wsys.sel.which === 'continuity' && wsys.sel.id === k ? ' sel' : ''));
      r.appendChild(_el('span', 'wsys-cat continuity', 'continuity'));
      r.appendChild(_el('span', null, `${sy ? sy.name : c.system} · ${c.reason} · layers ${c.layers.length > 3 ?
        `${c.layers[0] + 1}–${c.layers[c.layers.length - 1] + 1} (${c.layers.length})` : c.layers.map(x => x + 1).join(', ')}`));
      r.onclick = () => wsysSelectContinuity(k);
      box.appendChild(r);
    });
  }
  // the Assembly's openings / tapers judged with the CONSTRUCTION's own
  // minimum (the Assembly validated them on the architecture only)
  const ops = (wsys.gen.inspection && wsys.gen.inspection.openings) || [];
  if (ops.length) {
    box.appendChild(_el('div', 'asm-calc wsys-cat-head', `Openings · construction minimum · ${ops.length}`));
    ops.forEach((o, k) => {
      const r = _el('div', 'wsys-find' + (wsys.sel && wsys.sel.which === 'opening' && wsys.sel.id === k ? ' sel' : ''));
      r.appendChild(_el('span', 'wsys-cat construction', 'opening'));
      const who = o.members.length > 1 ? ` · openings ${o.members.map(j => '#' + (j + 1)).join(', ')}` : '';
      r.appendChild(_el('span', null, `P${(o.index ?? 0) + 1}${who} · ${o.errors.join(' · ')}`));
      r.title = 'Valid on the architecture (Assembly); this Wall System construction needs more wall';
      r.onclick = () => wsysSelectOpening(k);
      box.appendChild(r);
    });
  }
  if (st === 'outdated') box.appendChild(_el('div', 'asm-diff', 'These findings belong to the OUTDATED generation — generate again.'));
}

function _wsysRenderStatus() {
  const el = id => document.getElementById(id);
  const st = wsysStatus();
  const status = el('wsys-status');
  if (status) {
    _wsysClear(status);
    const row = _el('div', 'asm-kv-row');
    row.appendChild(_el('span', null, 'Generated geometry '));
    row.appendChild(_el('span', 'wsys-badge ' + st, WSYS_STATUS_LABEL[st]));
    status.appendChild(row);
    if (st === 'generating') status.appendChild(_el('div', 'asm-calc', 'Generating every layer (Wall Systems, lattice, caps, junctions, bead-level support)… editing stays available.'));
    if (st === 'outdated') status.appendChild(_el('div', 'asm-diff', 'OUTDATED — the Designer, Assembly or a Wall System changed since. The shown geometry is a reference only; generate again before planning the print.'));
    if (st === 'not_generated') status.appendChild(_el('div', 'asm-calc', 'Edit the Wall Systems below, then generate. Nothing is generated while you edit.'));
    if (wsys.gen && st !== 'generating') {
      const S = wsys.gen.stats || {};
      status.appendChild(_el('div', 'asm-calc', `${wsys.gen.layers.length} layers · ${S.built || 0} built · ` +
        `${(wsys.gen.support.findings || []).length} bead-level findings` +
        (wsys.gen.ms ? ` · ${(wsys.gen.ms / 1000).toFixed(1)} s` : '')));
    }
    if (wsys.error) status.appendChild(_el('div', 'asm-diff', wsys.error));
  }
  const gb = el('wsys-generate');
  if (gb) { gb.textContent = st === 'outdated' ? 'Regenerate Wall Systems' : 'Generate Wall Systems'; gb.disabled = st === 'generating'; }
  const cb = el('wsys-cancel');
  if (cb) cb.style.display = st === 'generating' ? '' : 'none';
  const u = el('wsys-undo'), r = el('wsys-redo');
  if (u) u.disabled = !wsys.hist.undo.length;
  if (r) r.disabled = !wsys.hist.redo.length;
}

// WALL GEOMETRY (2026-10-14: moved here from the Designer): the junction
// and end-cap treatment of the design being edited — defaults, then each
// wall-face junction (Default / Miter / Rounded). The same document fields
// and algorithms as before; edits are part of this workspace's undo.
// (2026-10-15: the Wall Geometry panel — junction / end-cap SHAPE — moved to the
// Designer's "Junctions & ends": it is architecture, the same in every layout
// state; this workspace chooses construction only)
function _wsysRenderGeometry() {}

// ARCHITECTURE, convenience editing (2026-10-15 correction): the Designer's
// "Junctions, Ends & Corners" controls, built by the Designer itself on the
// SAME canonical state (project junction default + per-junction treatment,
// each wall's own end cap and Corner R) — not Wall-System properties, no
// Variant copies. An edit is a step of this workspace's undo and makes the
// generation OUTDATED (it is in the generation key); regenerate to see it.
// MATERIAL, convenience editing (2026-10-16): the project's Bead Width,
// Contact Overlap and Parallel Wall Overlap (`return_overlap`) — the Designer's
// own values, edited through its one setter (window.Designer.setMaterial):
// no Wall-System copy, not in this workspace's undo (the Designer's, as when
// edited there). With physical rules they shape geometry → OUTDATED.
function _wsysRenderMaterial() {
  const m = window.Designer.material ? window.Designer.material() : null;
  if (!m) return;
  for (const [id, v] of [['wsys-bead-width', m.bead_width], ['wsys-contact-overlap', m.contact_overlap],
                         ['wsys-parallel-overlap', m.return_overlap]]) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = (+v).toFixed(2);
  }
}
function wsysSetMaterial(field, el) {
  if (!window.Designer.setMaterial) return;
  const v = window.Designer.setMaterial(field, el.value);
  el.value = (+v).toFixed(2);
  _wsysRenderMaterial();
  wsysNoteState(true);
}

function _wsysRenderArch() {
  const box = document.getElementById('wsys-arch');
  if (!box || !window.Designer.buildArchControls) return;
  window.Designer.buildArchControls(box, { prefix: 'wsys-arch', wrap: fn => _wsysEdit(fn) });
}

function _wsysRenderList() {
  const list = document.getElementById('wsys-list'), panel = document.getElementById('wsys-props');
  const edit = document.getElementById('wsys-edit-section');
  if (!list || !panel) return;
  _wsysClear(list); _wsysClear(panel);
  const L = _wsysList(), C = _wsysCatalog();
  const cur = L.find(s => s.id === wsys.pick) || L[0] || null;
  wsys.pick = cur ? cur.id : null;
  for (const s of L) {
    const item = _el('div', 'path-item wsys-item' + (s === cur ? ' selected' : ''));
    item.onclick = () => { wsys.pick = s.id; wsysRender(); };
    const sw = _el('span', 'wsys-swatch', '■ ');
    sw.style.color = _wsysSystemColor(s.id);
    const lab = _el('div', 'path-label');
    lab.append(sw, _el('span', null, _wsysLabel(s)));
    item.append(lab, _el('div', 'path-type', `${_wsysTypeLabel(s.type)} · ${_wsysWallsOf(s).length} walls`));
    list.appendChild(item);
  }
  const loose = C.walls.filter(w => !wsysSystemOf(w.id));
  const lh = loose.filter(w => w.thickness > 0), ls = loose.filter(w => !(w.thickness > 0));
  if (lh.length) _addNote(list, `No Wall System (prints Hollow — two skins): ${lh.map(w => w.label).join(', ')}`);
  if (ls.length) _addNote(list, `No Wall System, no thickness (a single bead): ${ls.map(w => w.label).join(', ')}`);
  if (!C.walls.length) _addNote(list, 'No walls yet: draw shapes in the Designer.');
  _addButton(list, '+ New Wall System', () => wsysNew('skin_web'));
  if (!cur) { if (edit) edit.style.display = 'none'; return; }
  if (edit) edit.style.display = '';
  const title = document.getElementById('wsys-edit-title');
  if (title) title.textContent = _wsysLabel(cur);
  // ASSIGNMENT (2026-10-15): a CHECKLIST of every wall — tick to give it this
  // construction (a wall has at most one; ticking moves it here); its
  // THICKNESS editable alongside (the wall's own architecture)
  _addSubTitle(panel, 'Walls');
  const unassigned = C.walls.filter(w => !wsysSystemOf(w.id));
  if (unassigned.length > 1)
    _addButton(panel, `+ All ${unassigned.length} walls without a Wall System`, () => wsysAssignWalls(cur.id, unassigned.map(w => w.id)));
  if (!C.walls.length) _addNote(panel, 'No walls yet: draw shapes in the Designer.');
  for (const w of C.walls) {
    const mine = (cur.members || []).includes(w.id), other = mine ? null : wsysSystemOf(w.id);
    const r = _el('div', 'prop-row wsys-wall' + (mine ? ' wsys-member' : ''));
    r.dataset.wall = w.id;
    const cb = _el('input');
    cb.type = 'checkbox'; cb.checked = mine; cb.className = 'wsys-check';
    cb.title = mine ? `Unassign ${w.label}` : (other ? `Move ${w.label} here from ${_wsysLabel(other)}` : `Assign ${w.label}`);
    cb.onchange = () => (cb.checked ? wsysAssignWall(cur.id, w.id) : wsysUnassignWall(cur.id, w.id));
    const nm = _el('span', 'prop-label', w.label + (other ? ` (${_wsysLabel(other)})` : ''));
    nm.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    nm.title = nm.textContent;
    // (the Designer's own thickness control: THIN ↔ first step at / above the THIN width)
    const [ctl, unit] = window.Designer.thicknessControl({
      value: w.thickness, thin: w.thin_width, compact: true, onSet: t => wsysSetThickness(w.id, t),
      title: 'This wall’s architectural thickness (shared with the Designer; THIN = one bead, its width set by the material)' });
    ctl.className += ' wsys-thick';
    r.append(cb, nm, ctl, unit);
    panel.appendChild(r);
  }
  const thin = _wsysWallsOf(cur).filter(w => !(w.thickness > 0));
  if (thin.length && cur.type !== 'single')
    _addNote(panel, `Thin (single bead) — give a thickness to fill it with ${_wsysTypeLabel(cur.type)}: ${thin.map(w => w.label).join(', ')}`);
  const th = _wsysThicknesses(cur);
  _addNote(panel, (th.length ? `Wall Thickness: ${th.map(t => t + ' in').join(', ')}. ` : '') +
                  'Thickness is each wall’s architecture (shared with the Designer and Assembly).');
  // CONSTRUCTION
  _addSubTitle(panel, 'Construction');
  addPropRowText(panel, 'Name', cur.name || '', v => wsysRename(cur.id, v));
  const types = Object.keys(WALL_SYSTEMS);
  addPropRowSelectTo(panel, 'Construction', cur.type, types, v => wsysSetType(cur.id, v),
                     Object.fromEntries(types.map(k => [k, WALL_SYSTEMS[k].label])));
  // a parameter edit is recorded (one undo step per field burst) — no geometry
  const before = { snap: null };
  const onParam = () => {
    const now = _wsysSnap();
    if (before.snap !== null && before.snap !== now) {
      wsys.hist.undo.push(before.snap); wsys.hist.redo = [];
    }
    before.snap = now;
    wsysNoteState(true);
  };
  before.snap = _wsysSnap();
  if (cur.type === 'single') {
    _addNote(panel, 'The ordinary single-line wall: one bead along each wall’s path (out and back when open, ' +
                    'Physical rules). The wall’s thickness stays its architectural envelope.');
  } else if (cur.type === 'hollow') {
    _addNote(panel, 'The inner and outer skins only — no structural web between them.');
  } else if (cur.type === 'skin_web') {
    _addSubTitle(panel, 'Skin + Web');
    _addWebRows(panel, cur, () => { onParam(); wsysRender(); }, { onParam });
  } else {
    _addSubTitle(panel, WALL_SYSTEMS[cur.type].label);
    _addWallSystemParams(panel, cur, { onParam, thickness: th.length ? th[0] : 10 });
    _addNote(panel, 'No separate skins: the paths fill each wall’s Wall Thickness envelope.');
  }
  _addNote(panel, 'Changes are not generated until “Generate Wall Systems”.');
  _addButton(panel, `Delete ${_wsysLabel(cur)}`, () => wsysDelete(cur.id));
}

function _wsysRenderLayer() {
  const el = id => document.getElementById(id);
  const n = _wsysCount(), i = wsys.layer;
  const inp = el('wsys-layer-input');
  if (inp && document.activeElement !== inp) inp.value = i >= 0 ? i + 1 : '';
  const sl = el('wsys-layer-slider');
  if (sl) { sl.max = String(Math.max(0, n - 1)); if (document.activeElement !== sl) sl.value = String(Math.max(0, i)); }
  if (el('wsys-layer-count')) el('wsys-layer-count').textContent = `/ ${n}`;
  for (const [id, on] of [['wsys-show-env', wsys.display === 'envelope'], ['wsys-show-gen', wsys.display === 'generated'],
                          ['wsys-top-btn', wsys.view.mode === 'top'], ['wsys-orbit-btn', wsys.view.mode === 'orbit'],
                          ['wsys-base-btn', i < 0]]) {
    const b = el(id);
    if (b && b.classList) b.classList.toggle('active', on);
  }
  const L = i >= 0 ? (_wsysLayers()[i] || (wsys.gen && wsys.gen.layers[i])) : null;
  const info = el('wsys-layer-info');
  if (info) info.textContent = L ? `z ${(+L.z_bottom).toFixed(2)}–${(+L.z_top).toFixed(2)} in · ${L.design_id}` : 'Base design';
  const det = el('wsys-layer-detail');
  if (det) {
    _wsysClear(det);
    det.appendChild(_el('div', 'asm-kv-row', L ? `Layer ${i + 1} of ${n} · design ${L.design_id}`
                                               : 'The Base design, from above.'));
    if (wsys.display === 'generated') {
      const st = wsys.gen ? wsysStatus() : 'not_generated';
      const d = wsys.gen && wsys.cache[`${wsys.gen.clientKey}|${i}`];
      const r = _el('div', 'asm-kv-row');
      r.appendChild(_el('span', null, 'Generated paths '));
      r.appendChild(_el('span', 'wsys-badge ' + st, WSYS_STATUS_LABEL[st]));
      det.appendChild(r);
      det.appendChild(_el('div', 'asm-calc', d ? `${d.polylines.length} printed paths (Wall Systems, junctions, caps, openings)`
                                               : (wsys.gen ? 'loading…' : 'Generate Wall Systems to see the printable geometry.')));
    } else det.appendChild(_el('div', 'asm-calc', 'Architectural envelope (the wall material).'));
  }
  const sup = el('wsys-support'), head = el('wsys-support-head');
  if (sup) {
    _wsysClear(sup);
    const gen = wsys.display === 'generated' && wsys.gen;
    if (head) head.textContent = gen ? 'This layer · FINAL (bead level)' : 'This layer · PRELIMINARY (architectural)';
    const fs = _wsysFindings().filter(f => f.status === 'needs_header' || f.status === 'insufficient_support');
    if (i < 0) sup.appendChild(_el('div', 'asm-calc', 'Select a layer.'));
    else if (!fs.length) sup.appendChild(_el('div', 'asm-kv-row', 'No open findings on this layer.'));
    for (const f of fs) _wsysFindRow(sup, f, _wsysFindLabel(f));
    if (gen && wsysStatus() === 'outdated') sup.appendChild(_el('div', 'asm-calc', '(of the OUTDATED generation)'));
  }
  const leg = el('wsys-legend');
  if (leg) {
    _wsysClear(leg);
    for (const [id, sy] of Object.entries((wsys.gen && wsys.gen.systems) || {})) {
      const r = _el('div', 'asm-kv-row');
      const sw = _el('span', null, '■ ');
      sw.style.color = _wsysSystemColor(id);
      r.append(sw, _el('span', null, `${sy.name} · ${_wsysTypeLabel(sy.type)}`));
      leg.appendChild(r);
    }
    if (!leg.children.length) leg.appendChild(_el('div', 'asm-calc', 'Envelope: the architectural wall material (blue).'));
  }
}

// ---- lifecycle ------------------------------------------------------------------------
async function wsysShow() {
  wsysRender();
  if (!asm.resolved || asm.resolving) { try { await asmResolve(); } catch (e) { /* shown in Assembly */ } }
  wsys._base = await _wsysBaseEnvelope();
  await wsysNoteState();
  if (wsys.display === 'generated') _wsysFetchLayer(wsys.layer);
}

function wsysReset() {
  if (wsys.inflight) wsysCancel();
  Object.assign(wsys, { pick: null, gen: null, currentKey: null, inflight: null, error: null, layer: -1,
                        display: 'envelope', cache: {}, pending: {}, baseEnv: null, _base: null,
                        hist: { undo: [], redo: [] }, findView: 'final', sel: null });
  wsys.view = { mode: 'top', zoom: 1, px: 0, py: 0, yaw: -Math.PI / 4, pitch: 0.6 };
  wsysRender();
}

function _wsysInit() {
  const cv = document.getElementById('wsys-canvas');
  if (cv && cv.addEventListener) {
    cv.addEventListener('mousedown', e => { wsys.drag = { x: e.clientX, y: e.clientY }; });
    window.addEventListener('mouseup', () => { wsys.drag = null; });
    window.addEventListener('mousemove', e => {
      const d = wsys.drag;
      if (!d) return;
      const dx = e.clientX - d.x, dy = e.clientY - d.y;
      d.x = e.clientX; d.y = e.clientY;
      if (wsys.view.mode === 'orbit' && !e.shiftKey) {
        wsys.view.yaw += dx * 0.01;
        wsys.view.pitch = Math.max(0.05, Math.min(1.5, wsys.view.pitch + dy * 0.01));
      } else { wsys.view.px += dx; wsys.view.py += dy; }
      wsysDraw();
    });
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      wsys.view.zoom = Math.max(0.1, Math.min(40, wsys.view.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      wsysDraw();
    }, { passive: false });
  }
  window.Designer.onWorkspaceShow('wallsystems', () => wsysShow());
  if (document.addEventListener) document.addEventListener('keydown', e => {
    if (typeof currentWorkspace === 'undefined' || currentWorkspace !== 'wallsystems') return;
    const a = document.activeElement;
    if (a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName || '') && a.type !== 'range') return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      wsysStep(e.key === 'ArrowRight' ? 1 : -1);
      if (e.preventDefault) e.preventDefault();
    }
  });
}

if (typeof window !== 'undefined' && window.Designer) {
  _wsysInit();
  window.WallSystemsProject = {
    reset: wsysReset, status: wsysStatus, noteState: wsysNoteState,
    current: () => (wsysStatus() === 'current' ? wsys.gen : null),
  };
}
