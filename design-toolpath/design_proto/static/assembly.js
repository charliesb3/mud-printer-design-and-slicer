// ===========================================================================
// ASSEMBLY workspace — UI of the Layer Assembly subsystem
// (design-toolpath/layer_assembly). It owns WHICH Layer Design is used, HOW
// HIGH (physical height), the resolved layers and their Z, and the stack
// preview. It knows nothing about walls, trims, lattices, beads or routes:
//   * Layer Designs come from the Designer through window.Designer
//     (listDesigns / designsForBackend / createDerivedDesign / editDesign);
//   * resolution and geometry come from /api/assembly/resolve and
//     /api/assembly/geometry (the layer_assembly blueprint).
// The design intent is PHYSICAL HEIGHT; layer counts are always derived.
// A section may carry a TRANSFORM (shift / scale per layer, linear profile);
// every resolved instance comes back with its placement {scale, tx, ty}
// (p' = scale·p + t), which the preview applies — designs are never copied.
// An ASSEMBLY TRANSFORM runs through the whole stack (never restarting);
// section transforms compose on top of it (layer_assembly/model.py).
// SUPPORT: the backend reports where mud would print over nothing (support.py);
// the designer adds a HEADER (an assembly object, not printed) or ignores the
// warning (recorded as an override — never shown as supported).
// HISTORY: Assembly edits have their OWN undo / redo (asm.hist), separate
// from the Designer's; Cmd/Ctrl-Z / Shift-Z act on it while Assembly is shown.
// SCALE CENTRE: 'footprint' (one pivot) or 'multiple' — TRANSFORM GROUPS
// (stable id, pivot, EXPLICIT membership = the source walls it moves). Every
// printable vertex carries the opaque id of the source it belongs to ('src',
// from the Designer); a vertex follows the group listing its source, else the
// whole-footprint placement. Walls are assigned by a per-wall selector or by
// clicking geometry in "Assign geometry" mode — never by nearest centre.
// MINIMUM LAYER OVERLAP: the support rule's physical parameter (support.py);
// a lean / scale beyond it is INSUFFICIENT LAYER SUPPORT, a span over empty
// space HEADER NEEDED.
// PROFILES (a separate subsystem): Z-dependent source geometry of straight
// walls — self-supporting openings, wall tapers — edited in each profile's
// canonical elevation; resolved per layer by the backend BEFORE the layer's
// design is resolved (see "ASSEMBLY PROFILES" below).
// ===========================================================================
const ASM_PALETTE = ['#4a9eff', '#ff9f43', '#2ecc71', '#e056fd', '#f9ca24', '#ff6b6b', '#48dbfb'];

const asm = {
  state: { layer_height: 1.5, sections: [{ design_id: 'base', height: 36 }],
           transform: { profile: 'linear', shift: [0, 0], scale_in: 0 },     // assembly-wide
           objects: [],     // Headers (assembly objects)
           overrides: [],   // ignored support warnings
           center_mode: 'footprint',   // scale centre: 'footprint' | 'multiple'
           centers: [],     // transform groups [{id, x, y, sources: [source id], label}]
           center_counter: 0,
           max_overhang: 1.5,  // in: how far a bead may hang past the one below (min overlap = bead − this)
           profiles: [],    // ASSEMBLY PROFILES (Z-dependent source geometry of straight walls)
           profile_counter: 0 },
  hist: { undo: [], redo: [], group: null, t: 0 },
  open: {},                 // section index (or 'asm') → transform panel expanded
  headerCounter: 0,
  resolved: null,           // /api/assembly/resolve result
  geometry: {},             // design id → { name, parent, polylines }
  geomKey: null,            // what `geometry` was built from
  variants: {},             // geometry key → a grouped layer's SEMANTIC variant (Designer-resolved)
  view: { yaw: -Math.PI / 4, pitch: 0.55, zoom: 1, px: 0, py: 0 },
  drag: null,
  busy: false,
  selCenter: null,          // the transform group being edited (assign mode target)
  assigning: false,         // "Assign geometry": clicks in the preview assign walls
  profSel: null,            // the Assembly profile being edited
  profPick: null,           // {kind}: choosing the straight wall of a new profile
  profHover: null,          // the straight wall under the pointer while choosing
  profError: null,
  elevation: null,          // {id, orbited}: the preview shows / left a profile's canonical elevation
};

// Assembly controls around a CENTRED preview, no tabs, each column scrolling
// on its own (2026-10-06; recentred 2026-10-08):
//   LAYER DESIGNS | LAYERS | 3D preview | ASSEMBLY TRANSFORM + PROFILES
//   LAYERS             — what is stacked, in what order, for how much height;
//                        the physical inputs (bead width, maximum overhang →
//                        minimum overlap), a one-line support summary and the
//                        SUPPORT block (findings / headers / ignored spans —
//                        shown only while there is something in it)
//   right column       — two collapsible sections: ASSEMBLY TRANSFORM
//                        (assembly-wide transform, transform groups, geometry
//                        assignment, connectors) and PROFILES (self-supporting
//                        openings, wall tapers) — separate subsystems
function _asmSupportOpen(S) {
  return S && S.findings ? _asmSupportGroups(S).filter(g => g.status === 'needs_header' ||
                                                        g.status === 'insufficient_support').length : 0;
}

function _asmStructureHead() {
  const S = (asm.resolved && asm.resolved.support) || {};
  const n = _asmSupportOpen(S);
  const any = !!((S.findings && S.findings.length) || (S.headers && S.headers.length));
  const ws = document.getElementById('ws-assembly');
  if (ws && ws.classList) ws.classList.toggle('asm-has-support', any);
  const head = document.getElementById('asm-support-head');
  if (head) head.textContent = n ? `Support · ${n} open` : 'Support';
  const sum = document.getElementById('asm-support-summary');
  if (sum) {
    sum.innerHTML = ''; if (Array.isArray(sum.children)) sum.children.length = 0;   // (test DOM stub)
    if (asm.resolved && asm.resolved.support) {
      const nh = (S.headers || []).length;
      sum.appendChild(_el('div', n ? 'asm-diff' : 'asm-kv-row',
        n ? `Support: ${n} open finding${n === 1 ? '' : 's'} (below)`
          : `Support: every layer supported${nh ? ` · ${nh} header${nh === 1 ? '' : 's'}` : ''}`));
    }
  }
}

const ASM_UNASSIGNED = '#4a9eff';    // = the Designer's print / bead blue
const ASM_GROUP_COLORS = ['#ff7675', '#55efc4', '#fdcb6e', '#a29bfe', '#e17055', '#fd79a8', '#00cec9'];   // no blue: blue = unassigned

function _asmColor(designId) {
  const ids = window.Designer.listDesigns().map(d => d.id);
  const k = Math.max(0, ids.indexOf(designId));
  return ASM_PALETTE[k % ASM_PALETTE.length];
}

function _asmName(designId) {
  const d = window.Designer.listDesigns().find(x => x.id === designId);
  return d ? d.name : designId;
}

async function _asmPost(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(body) });
  const data = await res.json();
  if (data && data.error) throw new Error(data.error);
  return data;
}

// the Assembly of a fresh page (NEW PROJECT; never mutated)
const _ASM_PRISTINE = JSON.stringify({ state: asm.state, header_counter: 0 });

// ---- history (Assembly only; the Designer keeps its own) --------------------
const ASM_HIST_MAX = 200;
const ASM_GROUP_MS = 1500;      // repeated edits of ONE field within this merge into one step

function _asmSnap() {
  // the bead width is the Designer's (project) material, edited from here too:
  // recorded so an Assembly undo restores it there (like a rename)
  return JSON.stringify({ state: asm.state, hc: asm.headerCounter,
                          names: window.Designer.listDesigns().map(d => [d.id, d.name]),
                          bw: window.Designer.beadWidth() });
}

// record `before` as one undo step if the state changed; `group` merges
// rapid edits of the same field (spinner clicks, retyping)
function _asmRecord(before, group) {
  if (before === _asmSnap()) return false;
  const H = asm.hist, now = Date.now();
  H.redo = [];
  if (group && group === H.group && now - H.t < ASM_GROUP_MS) { H.t = now; return true; }
  H.undo.push(before);
  if (H.undo.length > ASM_HIST_MAX) H.undo.shift();
  H.group = group || null; H.t = now;
  return true;
}

function asmEdit(group, fn) {
  const before = _asmSnap();
  const r = fn();
  _asmRecord(before, group);
  return r;
}

function _asmRestore(snap) {
  const o = JSON.parse(snap);
  asm.profNote = null;
  asm.state = o.state;
  asm.headerCounter = o.hc;
  for (const [id, name] of o.names) {
    const d = window.Designer.listDesigns().find(x => x.id === id);
    if (d && d.name !== name) window.Designer.renameDesign(id, name);
  }
  if (o.bw != null && o.bw !== window.Designer.beadWidth()) window.Designer.setBeadWidth(o.bw);
}

function asmUndo() {
  const H = asm.hist;
  if (!H.undo.length) return;
  H.redo.push(_asmSnap());
  _asmRestore(H.undo.pop());
  H.group = null;
  return asmResolve();
}

function asmRedo() {
  const H = asm.hist;
  if (!H.redo.length) return;
  H.undo.push(_asmSnap());
  _asmRestore(H.redo.pop());
  H.group = null;
  return asmResolve();
}

// ---- editing (the intent: physical heights) --------------------------------
function asmSetLayerHeight(v) {
  const x = parseFloat(v);
  if (!(x > 0)) { asmRender(); return; }
  return asmEdit('lh', () => {
    asm.state.layer_height = x;
    // opening Z values stay on whole layers (so identical clones cut identically),
    // then each opening is constrained again on the new layers (its driver wins)
    const snap = z => Math.round(Math.round(z / x) * x * 1e6) / 1e6;
    for (const p of _asmProfiles()) {
      if (p.kind !== 'opening' || p.source_opening) continue;
      p.z_bottom = Math.max(0, snap(p.z_bottom));
      if (p.array && p.array.v_spacing != null) p.array.v_spacing = snap(p.array.v_spacing);
    }
    _asmConstrainAll('layer');
    return asmResolve();
  });
}

function asmAddSection() {
  return asmEdit(null, () => {
    const ds = window.Designer.listDesigns();
    const last = asm.state.sections[asm.state.sections.length - 1];
    asm.state.sections.push({ design_id: last ? last.design_id : ds[0].id, height: 12 });
    return asmResolve();
  });
}

function asmDeleteSection(i) {
  return asmEdit(null, () => { asm.state.sections.splice(i, 1); return asmResolve(); });
}

// dir +1 = one place higher in the stack (later in the list)
function asmMoveSection(i, dir) {
  const j = i + dir;
  const S = asm.state.sections;
  if (j < 0 || j >= S.length) return;
  return asmEdit(null, () => {
    const T = asm.state.sections;
    [T[i], T[j]] = [T[j], T[i]];
    return asmResolve();
  });
}

function asmSetSectionDesign(i, designId) {
  return asmEdit(null, () => { asm.state.sections[i].design_id = designId; return asmResolve(); });
}

function asmSetSectionHeight(i, v) {
  const x = parseFloat(v);
  return asmEdit('h' + i, () => {
    if (x >= 0) asm.state.sections[i].height = x;
    return asmResolve();
  });
}

const ASM_STEP = 0.0625;     // 1/16 in: the input spinner step only — typed values are kept as typed

function _asmTf(i) {
  if (i === 'asm') return asm.state.transform;
  const s = asm.state.sections[i];
  if (!s.transform) s.transform = { profile: 'linear', shift: [0, 0], scale_in: 0 };
  return s.transform;
}

function _asmTfActive(t) {
  return !!t && (t.shift[0] || t.shift[1] || t.scale_in);
}

// field: 'dx' | 'dy' | 'scale_in' | 'profile'
function asmSetTransform(i, field, v) {
  return asmEdit(field === 'profile' ? null : `tf${i}.${field}`, () => {
    const t = _asmTf(i);
    if (field === 'profile') t.profile = v;
    else {
      const x = parseFloat(v) || 0;
      if (field === 'dx') t.shift[0] = x;
      else if (field === 'dy') t.shift[1] = x;
      else t.scale_in = x;
    }
    return asmResolve();
  });
}

// ---- scale centres (explicit transform centres) ------------------------------------
function asmSetCenterMode(mode) {
  return asmEdit(null, () => { asm.state.center_mode = mode; return asmResolve(); });
}

function _asmCenter(id) { return asm.state.centers.find(c => c.id === id); }

function _asmNewCenterId() { return 'c' + (++asm.state.center_counter); }

// a manual group (no walls yet) near the middle of the stack's footprint; selected
function asmAddCenter() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const g of Object.values(asm.geometry)) {
    const b = _asmBBox(g.polylines || []);
    if (b) { x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[2]); y1 = Math.max(y1, b[3]); }
  }
  const n = asm.state.centers.length;
  const cx = isFinite(x0) ? (x0 + x1) / 2 : 0, cy = isFinite(y0) ? (y0 + y1) / 2 : 0;
  return asmEdit(null, () => {
    const id = _asmNewCenterId();
    asm.state.centers.push({ id, x: cx + 12 * n, y: cy, sources: [], label: '' });
    asm.selCenter = id;
    return asmResolve();
  });
}

// one group per detected component, listing the walls found in it that no
// group has yet (explicit, reversible: ordinary groups afterwards)
function asmSuggestCenters() {
  const sug = (asm.resolved && asm.resolved.groups && asm.resolved.groups.suggestions) || [];
  const taken = new Set(asm.state.centers.flatMap(c => c.sources || []));
  const add = sug.map(m => ({ m, srcs: m.sources.filter(x => !taken.has(x)) })).filter(e => e.srcs.length);
  if (!add.length) return;
  return asmEdit(null, () => {
    for (const { m, srcs } of add)
      asm.state.centers.push({ id: _asmNewCenterId(), x: m.auto[0], y: m.auto[1], sources: srcs, label: '' });
    return asmResolve();
  });
}

// typed pivot of one centre (design coordinates) — membership never changes
function asmSetCenter(id, x, y) {
  const c = _asmCenter(id);
  if (!c) return;
  return asmEdit('c' + id, () => {
    c.x = parseFloat(x); c.y = parseFloat(y);
    return asmResolve();
  });
}

// back to the centre of its member walls' bbox
function asmResetCenter(id) {
  const c = _asmCenter(id);
  const g = c && asm.resolved && asm.resolved.groups && asm.resolved.groups.centres[c.id];
  if (!g) return;
  return asmEdit(null, () => { c.x = g.auto[0]; c.y = g.auto[1]; return asmResolve(); });
}

function asmDeleteCenter(id) {
  return asmEdit(null, () => {
    asm.state.centers = asm.state.centers.filter(c => c.id !== id);
    if (asm.selCenter === id) { asm.selCenter = null; asm.assigning = false; }
    return asmResolve();
  });
}

// EXPLICIT assignment: source wall → group (null = none). A wall belongs to
// at most one group, so assigning it moves it from any other.
function asmAssignSource(src, centerId) {
  if (centerId && !_asmCenter(centerId)) return;
  if (_asmGroupOf(src) === (centerId || null)) return;
  return asmEdit(null, () => {
    for (const c of asm.state.centers) c.sources = (c.sources || []).filter(x => x !== src);
    if (centerId) _asmCenter(centerId).sources.push(src);
    return asmResolve();
  });
}

function asmSelectCenter(id) {
  asm.selCenter = asm.selCenter === id ? null : id;
  if (!asm.selCenter) asm.assigning = false;
  asmRender();
}

function asmToggleAssign() {
  if (!asm.selCenter) return;
  asm.assigning = !asm.assigning;
  asmRender();
}

// click in "Assign geometry" mode: the wall under the pointer joins the
// selected group (clicking one of its own walls removes it)
function asmAssignAt(sx, sy) {
  const src = asmSourceAt(sx, sy);
  if (!src || !asm.selCenter) return null;
  asmAssignSource(src, _asmGroupOf(src) === asm.selCenter ? null : asm.selCenter);
  return src;
}

// the source of the drawn vertex nearest a canvas point (within 10 px)
function asmSourceAt(sx, sy) {
  const L = asm.lastProj;
  if (!L) return null;
  let best = null, bd = 10;
  for (const Ly of asmStack()) {
    Ly.polylines.forEach((pl, j) => {
      if (!pl.src) return;
      pl.pts.forEach((q, k) => {
        const [x, y] = _asmPlace(Ly.transform, q[0], q[1]);
        const [px, py] = L.P(x, y, Ly.z_top);
        const d = Math.hypot(px - sx, py - sy);
        if (d < bd && pl.src[k]) { bd = d; best = pl.src[k]; }
      });
    });
  }
  return best;
}

// the group listing a source (the first one, as the backend decides)
function _asmGroupOf(src) {
  if (asm.state.center_mode !== 'multiple' || !src) return null;
  const c = asm.state.centers.find(c => (c.sources || []).includes(src));
  return c ? c.id : null;
}

function _asmGroupColor(id) {
  const k = asm.state.centers.findIndex(c => c.id === id);
  return k < 0 ? null : ASM_GROUP_COLORS[k % ASM_GROUP_COLORS.length];
}

// preview colour of geometry of source `src` in a layer of `designId`. The
// transform-group view (Multiple centres with groups) is DIAGNOSTIC: assigned
// geometry = its group's colour, UNASSIGNED = the normal Designer blue (never
// a Layer Design colour that could read as another group). Otherwise each
// design keeps its own colour.
function _asmStrokeColor(designId, src) {
  if (asm.state.center_mode === 'multiple' && asm.state.centers.length > 0)
    return _asmGroupColor(_asmGroupOf(src)) || ASM_UNASSIGNED;
  return _asmColor(designId);
}

function _asmSourceLabel(src) {
  const S = asm.resolved && asm.resolved.groups && asm.resolved.groups.sources;
  if (S && S[src]) return S[src].label;
  for (const g of Object.values(asm.geometry)) if (g.sources && g.sources[src]) return g.sources[src];
  return src;
}

// PHYSICAL SUPPORT inputs (2026-10-06): Bead Width (the project MATERIAL —
// the same value the Designer's Material panel edits) and Maximum Overhang;
// the minimum overlap is DERIVED (bead width − maximum overhang).
function asmSetMaxOverhang(v) {
  const x = parseFloat(v), bw = window.Designer.beadWidth();
  if (!(x >= 0 && x <= bw)) {
    asm.inputError = `Maximum overhang must be between 0 and the bead width (${_fmt(bw)} in).`;
    asmRender(); return;
  }
  asm.inputError = null;
  return asmEdit('mh', () => { asm.state.max_overhang = x; _asmConstrainAll('overhang'); return asmResolve(); });
}

// a bead-width change is a MATERIAL edit: the Designer rebuilds every design
// (physical-rule geometry: contact / return-lane separations), so the
// Assembly's geometry and semantic variants are refetched (asmLoadGeometry's
// key is the designs) and support is re-analysed. One Assembly undo step.
function asmSetBeadWidth(v) {
  const x = parseFloat(v);
  if (!(x > 0)) { asmRender(); return; }
  asm.inputError = null;
  return asmEdit('bw', () => {
    const w = window.Designer.setBeadWidth(x);
    if (asm.state.max_overhang > w) asm.state.max_overhang = w;     // keep 0 ≤ overhang ≤ bead
    _asmConstrainAll('bead');
    return asmResolve();
  });
}

function _asmMaxOverhang() {
  return asm.state.max_overhang != null ? asm.state.max_overhang : 1.5;
}

function _asmCenters() {
  return asm.state.center_mode === 'multiple' ? asm.state.centers : [];
}

function _asmPivot(id) {
  const c = _asmCenter(id);
  return c ? [c.x, c.y] : null;
}

// dragging a centre in the preview: ONE undo step, applied on release
function asmCenterDragStart(key) {
  asm.drag = { center: key, before: _asmSnap(), moved: false };
}

// screen point (canvas px) → the pivot under it on the build plate (z 0;
// layer 0 is never transformed, so this is the design coordinate)
function asmCenterDragTo(sx, sy) {
  const d = asm.drag, L = asm.lastProj;
  if (!d || !d.center || !L) return;
  const p0 = _asmPivot(d.center);
  const [ax, ay] = L.P(p0[0], p0[1], 0), [bx, by] = L.P(p0[0] + 1, p0[1], 0), [cx, cy] = L.P(p0[0], p0[1] + 1, 0);
  const jx = [bx - ax, by - ay], jy = [cx - ax, cy - ay], r = [sx - ax, sy - ay];
  const det = jx[0] * jy[1] - jx[1] * jy[0];
  let dx, dy;
  if (Math.abs(det) > 1e-6 * (Math.hypot(...jx) * Math.hypot(...jy) || 1)) {
    dx = (r[0] * jy[1] - r[1] * jy[0]) / det;
    dy = (jx[0] * r[1] - jx[1] * r[0]) / det;
  } else {          // the build plate is edge-on (Front / Side): move along the visible axis
    const [u, nu] = Math.hypot(...jx) >= Math.hypot(...jy) ? [jx, 'x'] : [jy, 'y'];
    const t = (r[0] * u[0] + r[1] * u[1]) / (u[0] * u[0] + u[1] * u[1]);
    dx = nu === 'x' ? t : 0; dy = nu === 'y' ? t : 0;
  }
  const c = _asmCenter(d.center);
  c.x = p0[0] + dx; c.y = p0[1] + dy;
  d.moved = true;
  asmDrawPreview();
}

function asmCenterDragEnd() {
  const d = asm.drag;
  asm.drag = null;
  if (!d || !d.center) return;
  if (d.moved && _asmRecord(d.before, null)) return asmResolve();
  asmDrawPreview();
}

// the centre marker under a canvas point, if any
function asmCenterAt(sx, sy) {
  const L = asm.lastProj;
  if (!L) return null;
  let best = null, bd = 9;
  for (const c of _asmCenters()) {
    const [x, y] = L.P(c.x, c.y, 0);
    const d = Math.hypot(x - sx, y - sy);
    if (d < bd) { bd = d; best = c.id; }
  }
  return best;
}

function asmToggleTransform(i) {
  asm.open[i] = !asm.open[i];
  asmRender();
}

function asmRenameDesign(id, name) {
  const ok = asmEdit('rn' + id, () => window.Designer.renameDesign(id, name));
  asmRender();                                   // sections show the new name (same id)
  return ok;
}

function asmNewDerived() {
  const name = document.getElementById('asm-new-name').value;
  const parent = document.getElementById('asm-new-parent').value || 'base';
  const id = window.Designer.createDerivedDesign(name, parent);
  document.getElementById('asm-new-name').value = '';
  asmRender();
  return id;
}

// ---- resolution ----------------------------------------------------------------
// RESOLVE, COALESCED (2026-10-08): an edit draws at once (a live preview of
// every planar cut over the uncut layers, "resolving…"), and the exact
// resolve runs for the LATEST state only — one request in flight; a result
// already superseded by a newer edit is dropped (no variant fetch, no
// render), so rapid edits never queue a resolve each. Every caller's promise
// settles when the state it saw (or a newer one) is resolved and shown.
function asmResolve() {
  asm.resolveGen = (asm.resolveGen || 0) + 1;
  asm.resolving = true;
  asmDrawPreview();
  if (asm._resolveLoop) return asm._resolveLoop;
  asm._resolveLoop = (async () => {
    try {
      while (asm._resolvedGen !== asm.resolveGen) {
        const gen = asm.resolveGen;
        await _asmResolveOnce(gen);
        asm._resolvedGen = gen;
      }
    } finally {
      asm._resolveLoop = null;
      asm.resolving = false;
    }
    asmDrawPreview();
  })();
  return asm._resolveLoop;
}

// the planar cuts' parameters: what the shown result was resolved for
function _asmPlanesKey() {
  return JSON.stringify(((asm.state && asm.state.profiles) || []).filter(p => p.kind === 'plane'));
}

async function _asmResolveOnce(gen) {
  const ids = window.Designer.listDesigns().map(d => d.id);
  await asmLoadGeometry();          // footprints (bbox of each design's geometry) for SCALE
  const footprints = {};
  for (const [id, g] of Object.entries(asm.geometry)) footprints[id] = _asmBBox(g.polylines || []);
  try {
    const designs = await window.Designer.designsForBackend();
    const T = window.ASM_TIMING ? { t0: performance.now() } : null;   // opt-in instrumentation
    const sentPlanes = _asmPlanesKey();
    const res = await _asmPost('/api/assembly/resolve',
                               { assembly: asm.state, design_ids: ids, footprints, designs,
                                 ...(T ? { debug_timing: true } : {}) });
    if (gen !== asm.resolveGen) return;           // superseded: the newer state resolves next
    if (T) T.resolve_ms = performance.now() - T.t0;
    // layers with transform groups print a SEMANTIC variant of their design
    // (the Designer moved the walls and re-resolved them), layers with
    // Assembly-profile cuts a CUT variant (the Designer resolved the design
    // with the opening / shortened wall): fetch new ones BEFORE showing the
    // result — never a mix of new placements and stale geometry. A layer with
    // no variant prints the canonical design. (The cut spec MUST travel with
    // the request: without it the backend returns the uncut design.)
    const need = {};
    for (const [k, v] of Object.entries(res.variants || {}))
      if (!asm.variants[k]) need[k] = { design_id: v.design_id, transforms: v.transforms, cuts: v.cuts || [] };
    const t1 = T ? performance.now() : 0;
    if (Object.keys(need).length) {
      const r = await _asmPost('/api/assembly/geometry', { designs, variants: need });
      Object.assign(asm.variants, r.designs);
      if (gen !== asm.resolveGen) return;
    }
    asm.resolved = res;
    asm.resolvedPlanes = sentPlanes;
    asm.error = null;
    if (T) {
      T.variant_fetch_ms = performance.now() - t1;
      const t2 = performance.now();
      asmRender();
      T.render_ms = performance.now() - t2;
      asm.timing = { server: res.timing, resolve_ms: T.resolve_ms, variant_fetch_ms: T.variant_fetch_ms,
                     render_ms: T.render_ms };
      console.debug('assembly timing', asm.timing);
    }
    // a snapped header follows its opening: keep its re-derived plan as the fallback
    for (const h of ((asm.resolved.support || {}).headers || [])) {
      const o = asm.state.objects.find(x => x.id === h.id);
      if (o && h.snapped) Object.assign(o, { x: h.x, y: h.y, angle: h.angle, span: h.span / _asmScaleAt(h.layer) });
    }
  } catch (e) {
    if (gen !== asm.resolveGen) return;
    asm.error = e.message;
  }
  asmRender();
}

function _asmScaleAt(i) {
  const t = asm.resolved && asm.resolved.instances[i] && asm.resolved.instances[i].transform;
  return t ? t.scale : 1;
}

// ---- support: Headers / overrides ----------------------------------------------------
function asmFinding(id) {
  return ((asm.resolved && asm.resolved.support) || { findings: [] }).findings.find(f => f.id === id);
}

// a Header snapped to a detected unsupported span (never created automatically)
function asmAddHeader(findingId) {
  const f = asmFinding(findingId);
  if (!f) return;
  return asmEdit(null, () => {
    const id = 'h' + (++asm.headerCounter);
    asm.state.objects.push({ kind: 'header', id, section: f.section, layer: f.local_layer,
                             x: f.local_centre[0], y: f.local_centre[1], angle: f.angle,
                             span: f.span / _asmScaleAt(f.layer), bearing: 8,
                             depth: Math.round(f.depth * 16) / 16, thickness: 6, snap: true });
    return asmResolve();
  });
}

function asmRemoveHeader(id) {
  return asmEdit(null, () => {
    asm.state.objects = asm.state.objects.filter(o => o.id !== id);
    return asmResolve();
  });
}

// field: 'bearing' | 'depth' | 'thickness'
function asmSetHeader(id, field, v) {
  const h = (((asm.resolved || {}).support || {}).headers || []).find(x => x.id === id);
  const gp = h && h.profile ? _asmProfile(h.profile) : null;
  return asmEdit(gp ? `pf${gp.id}.header.${field}` : `hd${id}.${field}`, () => {
    const x = parseFloat(v);
    if (gp) {                       // a GENERATED header: its opening's settings (every clone)
      if (x >= 0) gp.header = { ...(gp.header || {}), [field]: x };
    } else {
      const o = asm.state.objects.find(x => x.id === id);
      if (o && x >= 0) o[field] = x;
    }
    return asmResolve();
  });
}

function asmIgnoreSpan(findingId) {
  const f = asmFinding(findingId);
  if (!f) return;
  return asmEdit(null, () => {
    asm.state.overrides.push({ section: f.section, layer: f.local_layer, x: f.local_centre[0],
                               y: f.local_centre[1], note: 'ignored in the Assembly' });
    return asmResolve();
  });
}

// ignore several findings as ONE step (a grouped INSUFFICIENT LAYER SUPPORT card)
function asmIgnoreSpans(ids) {
  const fs = ids.map(asmFinding).filter(Boolean);
  if (!fs.length) return;
  return asmEdit(null, () => {
    for (const f of fs)
      asm.state.overrides.push({ section: f.section, layer: f.local_layer, x: f.local_centre[0],
                                 y: f.local_centre[1], note: 'ignored in the Assembly' });
    return asmResolve();
  });
}

function asmUnignoreSpan(findingId) {
  const f = asmFinding(findingId);
  const k = f ? f.override : -1;           // the override the backend matched
  if (k < 0) return;
  return asmEdit(null, () => { asm.state.overrides.splice(k, 1); return asmResolve(); });
}

const _ASM_BBOX = new WeakMap();      // polylines array → its bbox (geometry arrays are never mutated)
function _asmBBox(polys) {
  const hit = _ASM_BBOX.get(polys);
  if (hit !== undefined) return hit;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of polys) for (const [x, y] of p.pts) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  const b = isFinite(x0) ? [x0, y0, x1, y1] : null;
  _ASM_BBOX.set(polys, b);
  return b;
}

// geometry of the designs in use (from the sections), rebuilt only when the
// designs changed
async function asmLoadGeometry() {
  const ids = window.Designer.listDesigns().map(d => d.id);
  const used = [...new Set(asm.state.sections.map(s => s.design_id))].filter(i => ids.includes(i));
  if (!used.length) return;
  const designs = await window.Designer.designsForBackend();
  // names are not geometry: a rename must not rebuild
  const key = JSON.stringify([designs.map(({ name, ...d }) => d), used]);
  if (key === asm.geomKey) return;
  try {
    asm.busy = true; asmDrawPreview();
    const r = await _asmPost('/api/assembly/geometry', { designs, ids: used });
    asm.geometry = r.designs;
    asm.geomKey = key;
    asm.variants = {};              // semantic variants belong to the old designs
    asm.error = null;
  } catch (e) {
    asm.error = 'A Layer Design could not be built: ' + e.message;
  }
  asm.busy = false;
}

// ---- panels ----------------------------------------------------------------------
function _el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function _fmt(v) { return (Math.round(v * 100) / 100).toFixed(2); }
function _fmt4(v) { return (Math.round(v * 10000) / 10000).toFixed(4); }
function _sgn(v) { return (v > 0 ? '+' : v < 0 ? '−' : '') + _fmt4(Math.abs(v)); }

// the transform controls of a section (i) or of the whole assembly ('asm')
function _asmTfPanel(i, note) {
  const tf = _asmTf(i);
  const panel = _el('div', 'asm-tf');
  const prow = _el('div', 'asm-row');
  prow.appendChild(_el('span', 'prop-label', 'Profile'));
  const ps = _el('select', 'prop-input');
  for (const [v, lab] of [['linear', 'Linear'], ['quadratic', 'Quadratic']]) {
    const o = _el('option', null, lab); o.value = v; ps.appendChild(o);
  }
  ps.value = tf.profile; ps.onchange = () => asmSetTransform(i, 'profile', ps.value);
  prow.appendChild(ps); panel.appendChild(prow);
  if (tf.profile === 'quadratic')
    panel.appendChild(_el('div', 'asm-calc', `Value = final per-layer rate at the top of the ${i === 'asm' ? 'stack' : 'section'} (starts near 0, grows steadily).`));
  for (const [label, field, val, unit, tip] of [
      ['Shift X', 'dx', tf.shift[0], 'in/layer', 'the whole layer moves this far per layer (same shape and size)'],
      ['Shift Y', 'dy', tf.shift[1], 'in/layer', 'the whole layer moves this far per layer (same shape and size)'],
      ['Scale in', 'scale_in', tf.scale_in, 'in/layer', 'UNIFORM scale about the footprint centre: its outermost edge moves this far per layer (+ inward, − outward). Walls, beads and lattice scale with it — not a constant-thickness inset']]) {
    const row = _el('div', 'asm-row'); row.title = tip;
    row.appendChild(_el('span', 'prop-label', label));
    const inp = _el('input', 'prop-input'); inp.type = 'number'; inp.step = String(ASM_STEP); inp.value = val;
    inp.onchange = () => asmSetTransform(i, field, inp.value);
    row.appendChild(inp); row.appendChild(_el('span', 'prop-unit', unit));
    panel.appendChild(row);
  }
  panel.appendChild(_el('div', 'asm-calc', note));
  return panel;
}

// one line, never wrapping (full text in its tooltip)
function _asmTfSummaryEl(t) {
  const txt = _asmTfActive(t) ? _asmTfSummary(t) : 'no transform';
  const e = _el('span', 'asm-calc asm-tf-sum', txt);
  e.title = txt;
  return e;
}

function _asmTfSummary(t) {
  if (!_asmTfActive(t)) return '';
  const parts = [];
  if (t.shift[0] || t.shift[1]) parts.push(`shift ${_sgn(t.shift[0])}, ${_sgn(t.shift[1])} in/layer`);
  if (t.scale_in) parts.push(`scale ${t.scale_in > 0 ? 'in' : 'out'} ${_fmt4(Math.abs(t.scale_in))} in/layer`);
  return parts.join(' · ') + ` (${t.profile})`;
}

function asmRender() {
  if (asm.profSel && !_asmProfile(asm.profSel)) {        // (undo / redo removed it)
    asm.profSel = null; asm.elevation = null;
  }
  const ds = window.Designer.listDesigns();
  const R = asm.resolved;
  // Layer Designs
  const list = document.getElementById('asm-designs');
  list.innerHTML = ''; if (Array.isArray(list.children)) list.children.length = 0;   // (test DOM stub)
  for (const d of ds) {
    const box = _el('div', 'asm-design');
    const head = _el('div', 'asm-row');
    const sw = _el('span', 'asm-swatch'); sw.style.background = _asmColor(d.id);
    head.appendChild(sw);
    const nm = _el('input', 'prop-input asm-name'); nm.value = d.name; nm.title = 'Rename (the design keeps its identity)';
    nm.onchange = () => asmRenameDesign(d.id, nm.value);
    head.appendChild(nm);
    box.appendChild(head);
    const uses = R ? R.sections.filter(s => s.design_id === d.id) : [];
    const layers = uses.reduce((a, s) => a + s.layers, 0);
    box.appendChild(_el('div', 'asm-meta',
      (d.parent == null ? 'base design' : `derived from ${_asmName(d.parent)}`) +
      ` · used in ${uses.length} section${uses.length === 1 ? '' : 's'}, ${layers} layer${layers === 1 ? '' : 's'}`));
    const edit = _el('button', 'mini-btn', d.active ? 'editing in Designer' : 'Edit in Designer');
    edit.disabled = !!d.active;
    edit.onclick = async () => { await window.Designer.editDesign(d.id); window.Designer.setWorkspace('designer'); };
    box.appendChild(edit);
    list.appendChild(box);
  }
  const par = document.getElementById('asm-new-parent');
  par.innerHTML = ''; if (Array.isArray(par.children)) par.children.length = 0;   // (test DOM stub)
  for (const d of ds) { const o = _el('option', null, d.name); o.value = d.id; par.appendChild(o); }

  // layer height + totals
  document.getElementById('asm-layer-height').value = asm.state.layer_height;
  const tot = document.getElementById('asm-totals');
  if (R) {
    const diff = R.total_height - R.desired_height;
    tot.innerHTML = '';
    tot.textContent = `Total: ${R.total_layers} layers · ${_fmt(R.total_height)} in actual` +
      ` · ${_fmt(R.desired_height)} in desired` +
      (Math.abs(diff) > 1e-9 ? ` (${diff > 0 ? '+' : ''}${_fmt(diff)} in from whole layers)` : ' (exact)');
  } else tot.textContent = asm.error || '';

  // assembly-wide transform (collapsed unless used / opened)
  const atf = document.getElementById('asm-assembly-tf');
  atf.innerHTML = ''; if (Array.isArray(atf.children)) atf.children.length = 0;   // (test DOM stub)
  const ahead = _el('div', 'asm-row asm-tf-head');
  const abtn = _el('button', 'mini-btn', (asm.open.asm ? '▾' : '▸') + ' Assembly Transform');
  abtn.onclick = () => asmToggleTransform('asm');
  ahead.appendChild(abtn);
  // (while open the summary line is always there: the fields below never move when it appears)
  if (_asmTfActive(asm.state.transform) || asm.open.asm) ahead.appendChild(_asmTfSummaryEl(asm.state.transform));
  atf.appendChild(ahead);
  if (asm.open.asm) atf.appendChild(_asmTfPanel('asm',
    'Runs through the WHOLE stack (layer 1 → top) without restarting at sections or design changes. ' +
    'Section transforms are applied on top of it.'));
  // scale centre (used by every Scale in: assembly-wide and sections)
  const crow = _el('div', 'asm-row');
  crow.title = 'Pivot of Scale in. Multiple centres: each transform group scales about its own (draggable) centre and moves only the walls assigned to it; Shift stays global.';
  crow.appendChild(_el('span', 'prop-label', 'Scale centre'));
  const csel = _el('select', 'prop-input');
  for (const [v, lab] of [['footprint', 'Whole footprint'], ['multiple', 'Multiple centres']]) {
    const o = _el('option', null, lab); o.value = v; csel.appendChild(o);
  }
  csel.value = asm.state.center_mode;
  csel.onchange = () => asmSetCenterMode(csel.value);
  crow.appendChild(csel);
  atf.appendChild(crow);
  if (asm.state.center_mode === 'multiple') {
    const G = (R && R.groups) || {};
    asm.state.centers.forEach((c, n) => {
      const row = _el('div', 'asm-row asm-center' + (asm.selCenter === c.id ? ' asm-selected' : ''));
      row.dataset.center = c.id;
      const sw = _el('span', 'asm-swatch'); sw.style.background = _asmGroupColor(c.id);
      row.appendChild(sw);
      const pick = _el('button', 'mini-btn' + (asm.selCenter === c.id ? ' active' : ''), `C${n + 1}`);
      pick.title = 'Select this transform group (to assign walls to it)';
      pick.onclick = () => asmSelectCenter(c.id);
      row.appendChild(pick);
      for (const ax of ['x', 'y']) {
        const inp = _el('input', 'prop-input'); inp.type = 'number'; inp.step = String(ASM_STEP);
        inp.value = Math.round(c[ax] * 100) / 100; inp.title = ax.toUpperCase();
        inp.onchange = () => asmSetCenter(c.id, ax === 'x' ? inp.value : c.x, ax === 'y' ? inp.value : c.y);
        row.appendChild(inp);
      }
      if (G.centres && G.centres[c.id]) {
        const rs = _el('button', 'mini-btn', '↺'); rs.title = 'Back to the centre of its walls';
        rs.onclick = () => asmResetCenter(c.id);
        row.appendChild(rs);
      }
      const del = _el('button', 'remove-btn', '×'); del.title = 'Delete group'; del.onclick = () => asmDeleteCenter(c.id);
      row.appendChild(del);
      atf.appendChild(row);
      const srcs = c.sources || [];
      atf.appendChild(_el('div', 'asm-calc asm-center-note',
        srcs.length ? 'moves: ' + srcs.map(_asmSourceLabel).join(', ') : 'no walls assigned yet — moves nothing'));
      if (asm.selCenter === c.id) {
        const ab = _el('button', 'mini-btn' + (asm.assigning ? ' active' : ''),
                       asm.assigning ? 'Assigning… (click walls in the preview) — done' : 'Assign geometry');
        ab.title = 'Click walls in the preview to add them to this group; click one of its walls again to remove it';
        ab.onclick = () => asmToggleAssign();
        atf.appendChild(ab);
      }
    });
    // every wall with its group: the explicit, deterministic assignment
    const S = G.sources || {};
    const walls = Object.keys(S);
    if (walls.length && asm.state.centers.length) {
      const wbox = _el('div', 'asm-walls');
      wbox.appendChild(_el('div', 'asm-calc', 'Walls → transform group'));
      for (const src of walls) {
        const row = _el('div', 'asm-row');
        row.dataset.source = src;
        row.appendChild(_el('span', 'prop-label', S[src].label));
        const sel = _el('select', 'prop-input');
        const none = _el('option', null, '— none (whole footprint)'); none.value = ''; sel.appendChild(none);
        asm.state.centers.forEach((c, n) => { const o = _el('option', null, `C${n + 1}`); o.value = c.id; sel.appendChild(o); });
        sel.value = _asmGroupOf(src) || '';
        sel.onchange = () => asmAssignSource(src, sel.value || null);
        row.appendChild(sel);
        wbox.appendChild(row);
      }
      atf.appendChild(wbox);
    }
    const brow = _el('div', 'asm-row');
    const addb = _el('button', 'mini-btn', '+ Add centre'); addb.onclick = () => asmAddCenter();
    const sug = _el('button', 'mini-btn', 'Suggest from components');
    sug.title = 'One group per separate printed mass, listing its walls (connected walls are one mass)';
    sug.onclick = () => asmSuggestCenters();
    brow.appendChild(addb); brow.appendChild(sug);
    atf.appendChild(brow);
    atf.appendChild(_el('div', 'asm-calc',
      'A group moves exactly the walls assigned to it; the Designer then re-resolves the layer (faces, junctions, lattice). ' +
      'Unassigned walls use the whole footprint. Moving a centre never changes its walls. Drag a centre (○) in the preview.'));
    // walls joining two differently moved forms are REGENERATED between them (Designer side)
    const conn = new Map();
    for (const v of Object.values((R && R.variants) || {}))
      for (const c of v.connectors || []) if (!conn.has(c.source)) conn.set(c.source, c);
    for (const c of conn.values()) {
      const hs = (c.hosts || []).filter(Boolean).map(_asmSourceLabel);
      atf.appendChild(_el('div', 'asm-calc asm-connector',
        `${_asmSourceLabel(c.source)} regenerated ` + (hs.length === 2 ? `between ${hs[0]} and ${hs[1]}` :
          hs.length === 1 ? `from ${hs[0]}` : 'with its group') + ' (an ordinary wall, not stretched)'));
    }
    // forms that merely INTERSECT: their junction is derived (recomputed from
    // the transformed forms each layer) and disappears when they separate
    const jn = new Map();
    for (const inst of (R && R.instances) || []) {
      const v = R.variants && R.variants[inst.geometry_key];
      for (const j of (v && v.junctions) || []) {
        const k = j.sources.join('|');
        const e = jn.get(k) || { sources: j.sources, apart: 0 };
        if (!j.present) e.apart++;
        jn.set(k, e);
      }
    }
    for (const e of jn.values()) {
      const [a, b] = e.sources.map(_asmSourceLabel);
      atf.appendChild(_el('div', 'asm-calc asm-junction', `${a} × ${b}: junction recomputed from the moved forms` +
        (e.apart ? ` — separate in ${e.apart} layer${e.apart > 1 ? 's' : ''} (no bridge is added)` : '')));
    }
    for (const w of G.warnings || []) atf.appendChild(_el('div', 'asm-diff', w));
  }
  const u = document.getElementById('asm-undo'), r = document.getElementById('asm-redo');
  if (u) u.disabled = !asm.hist.undo.length;
  if (r) r.disabled = !asm.hist.redo.length;
  _asmStructureHead();

  _asmRenderSupport();
  _asmRenderProfiles();
  const eb = document.getElementById('asm-elevation-btn');
  if (eb) eb.disabled = !_asmProfile(asm.profSel || (asm.elevation && asm.elevation.id));

  // sections: drawn TOP of the print first, so the stack reads bottom-up
  const box = document.getElementById('asm-sections');
  box.innerHTML = ''; if (Array.isArray(box.children)) box.children.length = 0;   // (test DOM stub)
  for (let i = asm.state.sections.length - 1; i >= 0; i--) {
    const s = asm.state.sections[i];
    const r = R && R.sections[i];
    const card = _el('div', 'asm-section');
    card.style.borderLeftColor = _asmColor(s.design_id);
    card.dataset.index = i;
    const row1 = _el('div', 'asm-row');
    row1.appendChild(_el('span', 'prop-label', i === 0 ? `${i + 1} · bottom` : `${i + 1}`));
    const sel = _el('select', 'prop-input');
    for (const d of ds) { const o = _el('option', null, d.name); o.value = d.id; sel.appendChild(o); }
    sel.value = s.design_id;
    sel.onchange = () => asmSetSectionDesign(i, sel.value);
    row1.appendChild(sel);
    const up = _el('button', 'mini-btn', '↑'); up.title = 'Move up the stack'; up.onclick = () => asmMoveSection(i, +1);
    const dn = _el('button', 'mini-btn', '↓'); dn.title = 'Move down the stack'; dn.onclick = () => asmMoveSection(i, -1);
    const del = _el('button', 'remove-btn', '×'); del.title = 'Delete section'; del.onclick = () => asmDeleteSection(i);
    up.disabled = i === asm.state.sections.length - 1; dn.disabled = i === 0;
    row1.appendChild(up); row1.appendChild(dn); row1.appendChild(del);
    card.appendChild(row1);
    const row2 = _el('div', 'asm-row');
    row2.appendChild(_el('span', 'prop-label', 'Height'));
    const h = _el('input', 'prop-input'); h.type = 'number'; h.min = '0'; h.step = '1'; h.value = s.height;
    h.onchange = () => asmSetSectionHeight(i, h.value);
    row2.appendChild(h); row2.appendChild(_el('span', 'prop-unit', 'in'));
    card.appendChild(row2);
    // TRANSFORM (collapsed unless used / opened)
    const t = s.transform;
    const tline = _el('div', 'asm-row asm-tf-head');
    const tbtn = _el('button', 'mini-btn', (asm.open[i] ? '▾' : '▸') + ' Transform');
    tbtn.onclick = () => asmToggleTransform(i);
    tline.appendChild(tbtn);
    if (_asmTfActive(t) || asm.open[i]) tline.appendChild(_asmTfSummaryEl(t));
    card.appendChild(tline);
    if (asm.open[i]) card.appendChild(_asmTfPanel(i,
      'Progress restarts in each section; it continues from where the section below ended. ' +
      'Order: scale about the current centre, then shift (applied after the assembly transform).'));
    if (r) {
      const calc = _el('div', 'asm-calc');
      calc.textContent = `${r.layers} layer${r.layers === 1 ? '' : 's'} → ${_fmt(r.actual_height)} in · z ${_fmt(r.z_bottom)}–${_fmt(r.z_top)}`;
      if (Math.abs(r.error) > 1e-9) {
        const df = _el('span', 'asm-diff', `  (${r.error > 0 ? '+' : ''}${_fmt(r.error)} in, whole layers)`);
        calc.appendChild(df);
      }
      card.appendChild(calc);
    }
    box.appendChild(card);
  }
  document.getElementById('asm-warnings').textContent = R ? R.warnings.join(' · ') : '';
  asmDrawPreview();
}

// "layer 5" / "layers 24–63" / "9 layers in 2–30" (a scattered group)
function _asmLayerRange(gr) {
  const [a, b] = gr.layers;
  if (a === b) return `layer ${a + 1}`;
  if (gr.contiguous === false) return `${gr.count} layers in ${a + 1}–${b + 1}`;
  return `layers ${a + 1}–${b + 1}`;
}

function _asmRenderSupport() {
  const bw = window.Designer.beadWidth(), mh = _asmMaxOverhang();
  const bwIn = document.getElementById('asm-bead-width');
  if (bwIn) bwIn.value = bw;
  const mhIn = document.getElementById('asm-max-overhang');
  if (mhIn) mhIn.value = mh;
  const note = document.getElementById('asm-overlap-note');
  const box = document.getElementById('asm-support');
  box.innerHTML = ''; if (Array.isArray(box.children)) box.children.length = 0;   // (test DOM stub)
  const S = asm.resolved && asm.resolved.support;
  if (note) {
    note.innerHTML = ''; if (Array.isArray(note.children)) note.children.length = 0;
    const r = _el('div', 'asm-kv-row');
    r.title = 'Minimum overlap = bead width − maximum overhang (less overhang = stricter). ' +
              'Controls INSUFFICIENT LAYER SUPPORT — a printability rule, not a transform.';
    r.appendChild(_el('span', null, 'Minimum overlap'));
    r.appendChild(_el('b', null, `${_fmt(Math.max(0, bw - mh))} in`));
    note.appendChild(r);
    if (asm.inputError) note.appendChild(_el('div', 'asm-diff', asm.inputError));
    if (S && S.warnings && S.warnings.length) note.appendChild(_el('div', 'asm-diff', S.warnings.join(' · ')));
  }
  if (!S) return;
  const R = asm.resolved;
  const where = f => {
    const lo = R.instances[f.layer - 1], up = R.instances[f.layer];
    return `layer ${f.layer + 1} (z ${_fmt(f.z)}) · ${_asmName(lo.design_id)} → ${_asmName(up.design_id)}`;
  };
  // INSUFFICIENT LAYER SUPPORT: the backend's aggregated groups (contiguous
  // layers, one section, one place) → ONE card each, per-layer detail folded
  const byId = Object.fromEntries(S.findings.map(f => [f.id, f]));
  for (const gr of _asmSupportGroups(S).filter(g => g.status === 'insufficient_support')) {
    const items = gr.finding_ids.map(i => byId[i]).filter(Boolean);
    const card = _el('div', 'asm-support-card asm-insufficient_support');
    card.dataset.finding = gr.finding_ids.join(' ');
    card.dataset.group = gr.id;
    card.appendChild(_el('div', 'asm-support-title', 'INSUFFICIENT LAYER SUPPORT'));
    const rng = _asmLayerRange(gr);
    card.appendChild(_el('div', 'asm-calc', `${rng} · section ${gr.section + 1}`));
    card.appendChild(_el('div', 'asm-calc',
      `required overlap: ${_fmt(gr.min_overlap)} in · worst overlap: ${_fmt(gr.worst_overlap)} in (layer ${gr.worst_layer + 1}, z ${_fmt(gr.z)})`));
    card.appendChild(_el('div', 'asm-calc', 'the layers move too far relative to the one below — not a header case'));
    const atp = [...new Set(items.map(f => f.profile).filter(Boolean))];
    if (atp.length) card.appendChild(_el('div', 'asm-calc', 'at Assembly profile ' +
      atp.map(id => 'P' + (_asmProfiles().findIndex(p => p.id === id) + 1)).join(', ')));
    if (items.length > 1) {
      const det = _el('details');
      const nl = new Set(items.map(f => f.layer)).size;
      det.appendChild(_el('summary', null, nl === items.length ? `${nl} layers` : `${items.length} regions over ${nl} layers`));
      for (const f of items) det.appendChild(_el('div', null, `layer ${f.layer + 1} (z ${_fmt(f.z)}): overlap ${_fmt(f.overlap)} in`));
      card.appendChild(det);
    }
    const ign = _el('button', 'mini-btn', 'Ignore'); ign.title = 'Accept on purpose (recorded as overrides; NOT supported)';
    ign.onclick = () => asmIgnoreSpans(gr.finding_ids);
    card.appendChild(ign);
    box.appendChild(card);
  }
  for (const f of S.findings) {
    if (f.status === 'insufficient_support') continue;
    const card = _el('div', 'asm-support-card asm-' + f.status);
    card.dataset.finding = f.id;
    const what = f.kind === 'overlap' ? `insufficient overlap ${_fmt(f.overlap)} in` : `span ${_fmt(f.span)} in`;
    const fp = f.profile ? _asmProfile(f.profile) : null;
    if (f.status === 'needs_header' && fp && fp.upper === 'header' && !fp.source_opening) {
      // at a HEADER-upper Assembly opening: its generated header does not carry all of it
      const n = _asmProfiles().indexOf(fp);
      card.appendChild(_el('div', 'asm-support-title', `HEADER SHORT · unsupported span ${_fmt(f.span)} in at opening P${n + 1}`));
      card.appendChild(_el('div', 'asm-calc', where(f) + (f.coverage > 0 ? ` · its header covers ${Math.round(f.coverage * 100)} %` : '') +
                                            ' — increase the header bearing / depth (Support › its header)'));
      const ign = _el('button', 'mini-btn', 'Ignore'); ign.title = 'Keep this span unsupported on purpose (recorded as an override)';
      ign.onclick = () => asmIgnoreSpan(f.id);
      card.appendChild(ign);
    } else if (f.status === 'needs_header' && f.profile) {
      // at a SELF-SUPPORTING Assembly opening: never a header request — the
      // geometry of that layer is the problem (reported, not hidden)
      const n = _asmProfiles().findIndex(p => p.id === f.profile);
      card.appendChild(_el('div', 'asm-support-title', `UNSUPPORTED SPAN ${_fmt(f.span)} in at self-supporting opening P${n + 1}`));
      card.appendChild(_el('div', 'asm-calc', where(f) + ' · headerless by design: adjust the profile (no header is proposed)'));
      const ign = _el('button', 'mini-btn', 'Ignore'); ign.title = 'Keep this span unsupported on purpose (recorded as an override)';
      ign.onclick = () => asmIgnoreSpan(f.id);
      card.appendChild(ign);
    } else if (f.status === 'needs_header') {
      card.appendChild(_el('div', 'asm-support-title', `HEADER NEEDED · unsupported span ${_fmt(f.span)} in`));
      card.appendChild(_el('div', 'asm-calc', where(f) + (f.coverage > 0 ? ` · a header covers only ${Math.round(f.coverage * 100)} %` : '')));
      const o = f.opening;
      if (o) card.appendChild(_el('div', 'asm-calc', `over Designer opening ${o.label || o.id}` +
        (o.converted ? ` — its conversion P${_asmProfiles().findIndex(p => p.id === o.converted) + 1} is not generated (see Profiles)` : '')));
      const row = _el('div', 'asm-row');
      const add = _el('button', 'mini-btn', 'Add Header'); add.onclick = () => asmAddHeader(f.id);
      const ign = _el('button', 'mini-btn', 'Ignore'); ign.title = 'Keep this span unsupported on purpose (recorded as an override)';
      ign.onclick = () => asmIgnoreSpan(f.id);
      row.appendChild(add);
      if (o && o.convertible && !o.converted) {
        const cv = _el('button', 'mini-btn', 'Convert to Self-Supporting Opening'); cv.dataset.action = 'convert-opening';
        cv.title = 'Keep this opening\'s width and clear height and add a self-supporting closure ABOVE it ' +
                   '(an editable Assembly profile; the Designer opening is not changed — delete the profile to undo)';
        cv.onclick = () => asmConvertOpening(f.id);
        row.appendChild(cv);
      }
      row.appendChild(ign); card.appendChild(row);
    } else if (f.status === 'overridden') {
      card.appendChild(_el('div', 'asm-support-title', `IGNORED · ${what} is NOT supported (override)`));
      card.appendChild(_el('div', 'asm-calc', where(f)));
      const un = _el('button', 'mini-btn', 'Restore warning'); un.onclick = () => asmUnignoreSpan(f.id);
      card.appendChild(un);
    } else {
      card.appendChild(_el('div', 'asm-support-title', `Supported by header ${f.header} · ${what}`));
      card.appendChild(_el('div', 'asm-calc', where(f)));
    }
    box.appendChild(card);
  }
  for (const h of S.headers) {
    const card = _el('div', 'asm-support-card asm-header');
    card.dataset.header = h.id;
    const head = _el('div', 'asm-row');
    const gp = h.profile ? _asmProfile(h.profile) : null;         // GENERATED for a Header-upper opening
    if (gp) {
      card.dataset.profile = gp.id;
      const n = _asmProfiles().indexOf(gp), many = (_asmProfileReport(gp.id) || {}).headers;
      head.appendChild(_el('span', 'asm-support-title',
        `Header of opening P${n + 1}${many && many.length > 1 ? ` #${h.member + 1}` : ''}`));
      card.appendChild(head);
      card.appendChild(_el('div', 'asm-calc', 'generated with the opening (Upper: Header) — choose another Upper to remove it' +
                                            (many && many.length > 1 ? '; settings shared by every opening of the array' : '')));
    } else {
      head.appendChild(_el('span', 'asm-support-title', `Header ${h.id}`));
      const rm = _el('button', 'remove-btn', '×'); rm.title = 'Remove header'; rm.onclick = () => asmRemoveHeader(h.id);
      head.appendChild(rm); card.appendChild(head);
    }
    if (h.error) { card.appendChild(_el('div', 'asm-diff', h.error)); box.appendChild(card); continue; }
    card.appendChild(_el('div', 'asm-calc',
      `span ${_fmt(h.span)} + 2 × ${_fmt(h.bearing)} bearing = length ${_fmt(h.length)} in · ` +
      `top z ${_fmt(h.z_top)} (under layer ${h.layer + 1}), bottom z ${_fmt(h.z_bottom)}`));
    const o = gp ? _asmHeaderSettings(gp) : (asm.state.objects.find(x => x.id === h.id) || {});
    for (const [label, field, tip] of [['Bearing', 'bearing', 'how far it extends into supported wall on EACH side of the opening'],
                                       ['Depth', 'depth', 'across the wall'],
                                       ['Thickness', 'thickness', 'vertical; the header hangs down from its top at the layer boundary']]) {
      const row = _el('div', 'asm-row'); row.title = tip;
      row.appendChild(_el('span', 'prop-label', label));
      const inp = _el('input', 'prop-input'); inp.type = 'number'; inp.min = '0'; inp.step = String(ASM_STEP);
      inp.value = o[field] != null ? o[field] : h[field];
      inp.onchange = () => asmSetHeader(h.id, field, inp.value);
      row.appendChild(inp); row.appendChild(_el('span', 'prop-unit', 'in'));
      card.appendChild(row);
    }
    if (!h.snapped && !gp) card.appendChild(_el('div', 'asm-diff', 'no unsupported span at this boundary now — kept at its last position'));
    if (h.supports.length === 0 && h.snapped && !gp) card.appendChild(_el('div', 'asm-diff', 'does not fully cover the span'));
    if (h.pocket_layers.length) {
      const a = h.pocket_layers[0], b = h.pocket_layers[h.pocket_layers.length - 1];
      card.appendChild(_el('div', 'asm-calc',
        `its ends pass through wall layers ${a + 1}–${b + 1}: those layers need a pocket (not cut by the Assembly)`));
    }
    box.appendChild(card);
  }
}

// The viewport labels of the support findings: one per aggregated group,
// placed at its worst layer; a label that would overlap one already placed
// is nudged up (twice) and otherwise dropped (its card still lists it).
function asmSupportLabels(P) {
  const S = asm.resolved && asm.resolved.support;
  if (!S) return [];
  const out = [];
  const W = t => 6.2 * t.length;
  for (const gr of _asmSupportGroups(S)) {
    if (gr.status === 'supported') continue;
    const inst = asm.resolved.instances[gr.worst_layer];
    if (!inst || !gr.centre) continue;
    const c = P(gr.centre[0], gr.centre[1], inst.z_top);
    const text = gr.status === 'overridden' ? 'IGNORED'
      : gr.status === 'insufficient_support'
        ? `INSUFFICIENT SUPPORT · ${_asmLayerRange(gr)}`
        : `HEADER NEEDED · layer ${gr.worst_layer + 1}`;
    const color = gr.status === 'overridden' ? '#f0a030' : gr.status === 'insufficient_support' ? '#d68df5' : '#ff6b6b';
    let y = c[1] - 10, placed = false;
    for (let k = 0; k < 3 && !placed; k++, y -= 13) {
      if (!out.some(o => Math.abs(o.y - y) < 12 && Math.abs(o.x - c[0]) < (W(o.text) + W(text)) / 2)) {
        out.push({ text, x: c[0], y, color, group: gr.id }); placed = true;
      }
    }
  }
  return out;
}

// Aggregated findings (support.py SupportReport.groups); a fallback groups
// each finding alone (older responses).
function _asmSupportGroups(S) {
  if (S.groups) return S.groups;
  return S.findings.map((f, k) => ({ id: 'g' + k, kind: f.kind, status: f.status, section: f.section,
    layers: [f.layer, f.layer], min_overlap: f.min_overlap, worst_overlap: f.overlap, worst_layer: f.layer,
    worst: f.id, centre: f.centre, z: f.z, finding_ids: [f.id], count: 1 }));
}

// ---- ASSEMBLY PROFILES (layer_assembly/profiles.py, 2026-10-08) -----------------
// Z-dependent SOURCE-geometry profiles of one straight wall — a subsystem of
// its own, separate from Assembly Transform. The backend resolves each
// profile per layer into ordinary Designer geometry (an opening on the wall /
// the wall shortened) BEFORE the layer is resolved; Wall Systems, lattice,
// caps and routing then work as usual. Profiles live in asm.state (Assembly
// undo / redo); every edit is one step (a handle drag too).
//   Self-supporting opening: position along the wall, bottom Z, height,
//     maximum width, shape (Diamond / Lens); headerless — the backend
//     validates its layer-to-layer closure against Maximum overhang and cuts
//     nothing when it is too steep (reported with a fix).
//   Wall taper: a CUT LINE drawn in the wall's elevation between two points
//     (distance along the wall, Z) and which side of it is kept; below the
//     lower point the wall is untouched, above the upper point the cut holds.
// Precision: Z values snap to whole layers (the Assembly layer height);
// distances along the wall step 1 in (typed) / ¼ in (dragged).
// Editing happens in the profile's CANONICAL ELEVATION (horizontal = distance
// along the wall, vertical = Z): entering a profile snaps the preview to it;
// orbiting away is allowed; "Elevation" returns. Straight walls only (V1).
const ASM_PROFILE_COLOR = '#00d2d3';

function _asmProfiles() {
  if (!asm.state.profiles) asm.state.profiles = [];
  return asm.state.profiles;
}

function _asmProfile(id) { return _asmProfiles().find(p => p.id === id); }

function _asmProfileReport(id) {
  return ((asm.resolved && asm.resolved.profiles) || []).find(r => r.id === id) || null;
}

// straight walls of the designs in the stack: wall key → target (first design
// in stack order that has it — design coordinates)
function _asmProfileTargets() {
  const T = (asm.resolved && asm.resolved.profile_targets) || {};
  const order = [...new Set(((asm.resolved && asm.resolved.instances) || []).map(i => i.design_id))];
  const out = {};
  for (const d of order) for (const t of T[d] || []) {
    const k = t.edge == null ? t.source : `${t.source}#${t.edge}`;
    if (!out[k]) out[k] = { ...t, key: k, design: d };
  }
  return out;
}

function _asmWallKey(w) { return w.edge == null ? w.source : `${w.source}#${w.edge}`; }

function _asmProfileTarget(p) { return p && p.wall ? _asmProfileTargets()[_asmWallKey(p.wall)] || null : null; }

function _asmProfileLabel(p, n) {
  const t = _asmProfileTarget(p);
  const wall = t ? t.label : _asmWallKey(p.wall);
  if (p.kind === 'plane') return `P${n + 1} · Planar top cut`;
  return `P${n + 1} · ${p.kind === 'opening' ? 'Self-supporting opening' : 'Wall taper'} · ${wall}`;
}

function _asmStackTop() {
  return (asm.resolved && asm.resolved.total_height) || 0;
}

const _asmClean = v => Math.round(v * 1e6) / 1e6;
// Z of a profile: whole layers of the current Assembly layer height
function _asmSnapZ(z) {
  const lh = asm.state.layer_height || 1;
  return _asmClean(Math.max(0, Math.round(z / lh) * lh));
}
// a distance along the wall: typed values keep 1/16 in, drags ¼ in
const _asmQ16 = v => Math.round(v * 16) / 16;
const _asmQ4 = v => Math.round(v * 4) / 4;

// ---- creating: choose the operation, then a straight wall ----
function asmProfileBegin(kind) {
  asm.profPick = { kind };
  asm.assigning = false;
  asmRender();
}

function asmProfileCancelPick() {
  asm.profPick = null;
  asmRender();
}

function asmProfileCreate(kind, key) {
  const t = _asmProfileTargets()[key];
  if (!t) return;
  if (kind === 'extent' && !t.taper) {
    asm.profError = `${t.label}: only a whole straight OPEN wall can be tapered (V1).`;
    asm.profPick = null; asmRender(); return;
  }
  asm.profPick = null; asm.profError = null;
  const top = _asmStackTop() || 72;
  const L = t.length;
  const id = 'p' + (asm.state.profile_counter = (asm.state.profile_counter || 0) + 1);
  const wall = { source: t.source, edge: t.edge == null ? null : t.edge };
  let p;
  if (kind === 'opening') {
    const jamb = (t.thickness || 0) / 2 + window.Designer.beadWidth();
    const w = Math.max(0, Math.floor(Math.min(24, L - 2 * jamb)));
    const zb = _asmSnapZ(Math.min(12, top / 4));
    const lh = asm.state.layer_height || 1;
    p = { kind: 'opening', id, wall, position: Math.round(L / 2), z_bottom: zb,
          height: Math.max(lh, _asmSnapZ(Math.min(2 * w, top - zb))), width: w, shape: 'diamond', label: '',
          upper: 'pointed', lower: 'pointed', driver: 'height' };
    _asmConstrain(p, 'height');              // printable from the start: the width fits the height
  } else {
    // a triangular buttress: keep the ATTACHED end (when the Designer reports
    // exactly one), the cut from the free end at the bottom to the kept end
    // at the top — then drag either point
    // Keep = AUTO: whichever side of the drawn cut is supported is kept
    const att = t.attached || [];
    const free = (att.length === 1 ? att[0] : 'start') === 'start' ? L : 0;
    p = { kind: 'extent', id, wall, keep: 'auto', knots: [[0, _asmQ16(free)], [_asmSnapZ(top), _asmQ16(L - free)]],
          interp: ['linear'], label: '' };
  }
  return asmEdit(null, () => {
    _asmProfiles().push(p);
    return asmResolve().then(() => asmProfileEdit(id));
  });
}

// EDIT vs RESULT. Editing a profile (asm.profSel) shows its construction —
// cut outline / line, handles, the resolved per-layer cuts, the removed
// source geometry GHOSTED — in its canonical elevation. "Done" leaves edit
// mode: nothing is baked into the Designer (the profile stays a parametric
// Assembly object, editable again), the construction overlay disappears and
// the preview shows only the RESOLVED layers (cut variants: real openings /
// shortened walls), in the view the user had before editing.
function asmProfileEdit(id) {
  const p = _asmProfile(id);
  if (!p) return;
  if (!asm.profSel) asm.viewBeforeEdit = { ...asm.view };
  asm.profSel = id;
  if (p.kind === 'plane') return asmPlaneView(id, 'plan');
  asmProfileElevation(id);
}

function asmProfileDone() {
  asm.profSel = null;
  asm.elevation = null;
  asm.profPick = null;
  Object.assign(asm.view, asm.viewBeforeEdit || ASM_VIEWS.iso, asm.viewBeforeEdit ? {} : { zoom: 1, px: 0, py: 0 });
  asm.viewBeforeEdit = null;
  asmRender();
}

function asmProfileSelect(id) {
  if (asm.profSel === id) asmProfileDone();
  else asmProfileEdit(id);
}

function asmProfileDelete(id) {
  return asmEdit(null, () => {
    asm.state.profiles = _asmProfiles().filter(p => p.id !== id);
    if (asm.profSel === id) asmProfileDone();
    return asmResolve();
  });
}

// the taper's SAME cut seen from the wall's other end: s → L − s, Keep swapped
// (= profiles.ExtentProfile.mirrored) — e.g. to keep the other end of a buttress
function asmProfileMirror(id) {
  const p = _asmProfile(id), t = _asmProfileTarget(p);
  if (!p || !t || p.kind !== 'extent') return;
  return asmEdit(null, () => {
    if (p.keep !== 'auto') p.keep = p.keep === 'end' ? 'start' : 'end';
    p.knots = p.knots.map(([z, c]) => [z, _asmQ16(t.length - c)]);
    return asmResolve();
  });
}

// numeric fields. opening: position | z_bottom | width | height (diamond) |
// upper_height | middle_height | bottom_depth, choices shape | upper | lower |
// middle (on / off) — every change of a dimension or a
// profile is CONSTRAINED at once (the edited parameter wins: _asmConstrain);
// taper: keep | s_lower | z_lower | s_upper | z_upper (the two cut points)
function asmProfileSet(id, field, v) {
  const p = _asmProfile(id);
  if (!p) return;
  asm.profError = null;
  if (field === 'keep') return asmEdit(null, () => { p.keep = v; return asmResolve(); });
  if (field === 'shape' || field === 'upper' || field === 'lower' || field === 'middle') {
    return asmEdit(null, () => {
      const Q = _OS().q16;
      if (field === 'shape' && v !== p.shape) {
        if (v === 'arch') {                     // Diamond → Arch of the same width and total height:
          const H = +p.height || 0;             // its style above a flat sill, the rest straight sides
          Object.assign(p, { shape: 'arch', lower: 'flat', bottom_depth: null, middle: true,
                             upper_height: Q(H / 2), middle_height: Q(H - Q(H / 2)),
                             driver: p.driver === 'height' ? 'upper_height' : p.driver });
        } else {                                // Arch → Diamond of its total height, its style
          Object.assign(p, { shape: 'diamond', upper: p.upper === 'curve' ? 'curve' : 'pointed',
                             driver: p.driver === 'upper_height' ? 'height' : p.driver });
          p.lower = p.upper;
        }
      } else if (field === 'upper') {
        p.upper = v;
        if (p.shape === 'diamond') p.lower = v;
        if (p.shape === 'arch' && v !== 'header' && !(p.upper_height > 0) && !p.source_opening) p.upper_height = Q(p.width / 2);
      } else if (field === 'lower') {
        p.lower = v;
        // default depths: Rounded a semicircle, Angular a 45° point, Curved (vesica) as deep as wide
        if (v !== 'flat' && !(p.bottom_depth > 0)) p.bottom_depth = Q(v === 'arc' ? p.width : p.width / 2);
      } else if (field === 'middle') {
        p.middle = !!v;
        if (p.middle && !(p.middle_height > 0)) p.middle_height = Q(Math.max(asm.state.layer_height || 1, p.width));
      }
      if (!p.source_opening) _OS().sync(p);    // (a conversion's dimensions are the Designer's)
      _asmConstrain(p, field);
      return asmResolve();
    });
  }
  if (p.kind === 'opening' && p.source_opening && field === 'upper_height') {   // a conversion: blank = the minimum
    const x = String(v).trim() === '' ? null : parseFloat(v);
    if (x !== null && !(x > 0)) { asm.profError = 'Values must be > 0 (blank = the minimum).'; asmRender(); return; }
    return asmEdit(`pf${id}.${field}`, () => {
      p.upper_height = x === null ? null : _asmSnapZ(x);
      const G = _asmOpeningGeom({ ...p, upper_height: null });
      if (p.upper_height != null && G && G.Umin != null && p.upper_height <= G.Umin + 1e-6) p.upper_height = null;
      return asmResolve();
    });
  }
  if (field.startsWith('array.')) return asmProfileSetArray(id, field.slice(6), v);
  const x = parseFloat(v);
  if (!isFinite(x)) { asmRender(); return; }
  const lh = asm.state.layer_height || 1;
  if (p.kind === 'opening') {
    if (x < 0 && field !== 'position') { asm.profError = 'Values must be ≥ 0.'; asmRender(); return; }
    if (['width', 'height', 'upper_height', 'bottom_depth'].includes(field) && !(x > 0)) {
      asm.profError = 'Width, heights and depths must be > 0.'; asmRender(); return;
    }
    // Z dimensions (bottom, heights, depths) whole layers; widths / positions 1/16 in
    const val = ['z_bottom', 'height', 'upper_height', 'middle_height', 'bottom_depth'].includes(field) ? _asmSnapZ(x) : _asmQ16(x);
    return asmEdit(`pf${id}.${field}`, () => {
      p[field] = val;
      _OS().sync(p);
      if (field !== 'position') _asmConstrain(p, field);
      return asmResolve();
    });
  }
  const k = { z_lower: [0, 0], z_upper: [1, 0], s_lower: [0, 1], s_upper: [1, 1] }[field];
  if (!k) return;
  const kn = p.knots.map(q => q.slice());
  kn[k[0]][k[1]] = k[1] === 0 ? _asmSnapZ(x) : _asmQ16(x);
  if (kn[1][0] === kn[0][0]) { asm.profError = 'The two cut points need different Z.'; asmRender(); return; }
  const ordered = kn[0][0] < kn[1][0] ? kn : [kn[1], kn[0]];   // either diagonal: the points are reordered by Z
  return asmEdit(`pf${id}.${field}`, () => { p.knots = ordered; return asmResolve(); });
}

// ARRAY of identical openings (profiles.OpeningArray): ONE parametric object.
// Spacing = CLEAR distance between opening edges; diagonals: the two spacings
// are the horizontal / vertical offsets between neighbours
const ASM_ARRANGEMENTS = [['single', 'Single'], ['horizontal', 'Horizontal'], ['vertical', 'Vertical'],
                          ['diagonal_up', 'Diagonal ↗'], ['diagonal_down', 'Diagonal ↘'],
                          ['grid', 'Grid'], ['staggered', 'Staggered grid']];
function asmProfileSetArray(id, field, v) {
  const p = _asmProfile(id);
  if (!p) return;
  const A = { arrangement: 'single', count: 3, cols: 3, rows: 2, h_spacing: 12, v_spacing: 12, stagger: null, ...(p.array || {}) };
  if (field === 'arrangement') {
    return asmEdit(null, () => {
      p.array = v === 'single' ? null : { ...A, arrangement: v };
      _asmConstrain(p, 'array');
      return asmResolve();
    });
  }
  const blank = String(v).trim() === '';
  const x = blank ? null : parseFloat(v);
  if (field === 'stagger' && blank) A.stagger = null;
  else if (!isFinite(x)) { asmRender(); return; }
  else if (['count', 'cols', 'rows'].includes(field)) {
    if (!(x >= 1)) { asm.profError = 'At least one opening per row / column.'; asmRender(); return; }
    A[field] = Math.min(50, Math.round(x));
  } else if (field === 'v_spacing') {        // a Z distance: whole layers (identical clones cut identically)
    const lh = asm.state.layer_height || 1;
    A[field] = _asmClean(Math.round(x / lh) * lh);
  } else A[field] = _asmQ16(x);
  return asmEdit(`pf${id}.array.${field}`, () => { p.array = A; _asmConstrain(p, 'array'); return asmResolve(); });
}

// CONVERT a Designer opening that needs a header (its finding names it —
// web._tag_designer_openings) into a self-supporting opening: an Assembly
// profile (the Designer opening itself is untouched) that keeps its
// rectangle — position, clear width, the layers that print it — and adds the
// minimum self-supporting closure ABOVE it. Delete the profile to undo.
function asmConvertOpening(findingId) {
  const f = asmFinding(findingId), o = f && f.opening;
  if (!o || !o.convertible) return;
  const id = 'p' + (asm.state.profile_counter = (asm.state.profile_counter || 0) + 1);
  const p = { kind: 'opening', id, wall: { source: o.source, edge: o.edge == null ? null : o.edge },
              position: o.at, z_bottom: 0, height: 0, width: o.width, shape: 'arch', label: `${o.label}`,
              upper: 'pointed', lower: 'flat', bottom_depth: null, upper_height: null, middle: true, middle_height: 0,
              source_opening: { id: o.id, z_ref: o.z_ref } };
  return asmEdit(null, () => {
    _asmProfiles().push(p);
    return asmResolve().then(() => asmProfileEdit(id));
  });
}

// ---- the canonical elevation of a profile ----
// horizontal = distance along the wall, vertical = Z. Snaps the preview
// (yaw along the wall, pitch 0) and frames the wall over the stack height.
function asmProfileElevation(id) {
  const p = _asmProfile(id || asm.profSel);
  const t = _asmProfileTarget(p);
  asm.profSel = p ? p.id : null;
  if (!p || !t) { asmRender(); return; }
  const stack = asmStack();
  const T0 = (stack[0] && stack[0].transform) || { scale: 1, tx: 0, ty: 0 };
  const ux = t.b[0] - t.a[0], uy = t.b[1] - t.a[1];
  Object.assign(asm.view, { yaw: -Math.atan2(uy, ux), pitch: 0, zoom: 1, px: 0, py: 0 });
  asm.elevation = { id: p.id, orbited: false };
  const cv = document.getElementById('asm-canvas'), wrap = document.getElementById('asm-preview-wrap');
  const W = Math.max(200, (wrap && wrap.clientWidth) || 600), H = Math.max(200, (wrap && wrap.clientHeight) || 500);
  if (stack.length) {
    const P = asmProjector(stack, W, H);
    const top = _asmStackTop();
    const A = _asmPlace(T0, t.a[0], t.a[1]), B = _asmPlace(T0, t.b[0], t.b[1]);
    const a = P(A[0], A[1], top / 2), b = P(B[0], B[1], top / 2);
    const len = Math.max(1, Math.hypot(b[0] - a[0], b[1] - a[1]));
    const zp = Math.max(1, Math.abs(P(A[0], A[1], top)[1] - P(A[0], A[1], 0)[1]));
    const k = Math.max(0.2, Math.min(30, Math.min(0.8 * W / len, 0.8 * H / zp)));
    const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    asm.view.zoom = k;
    asm.view.px = -(m[0] - W / 2) * k;
    asm.view.py = -(m[1] - H / 2) * k;
  }
  asmRender();
}

// a point of the profile's wall at (distance s along it, Z) → placed XYZ:
// the wall as the layer at that Z places it
function _asmWallPoint(t, s, z) {
  const R = asm.resolved;
  const insts = (R && R.instances) || [];
  let T = { scale: 1, tx: 0, ty: 0 };
  if (insts.length) {
    const lh = asm.state.layer_height || 1;
    const i = Math.max(0, Math.min(insts.length - 1, Math.floor(z / lh - 1e-9)));
    T = insts[i].transform || T;
  }
  const L = t.length || 1;
  const x = t.a[0] + (t.b[0] - t.a[0]) * s / L, y = t.a[1] + (t.b[1] - t.a[1]) * s / L;
  const q = _asmPlace(T, x, y);
  return [q[0], q[1], z];
}

// OPENING SHAPES (2026-10-10, static/opening_shape.js = layer_assembly/opening_shape.py):
// Diamond (angular | curved vesica) | Arch (lower + optional middle + upper:
// angular | curved Gothic | header). The stored
// parameters ARE the printable shape: every edit goes through
// OpeningShape.constrain (the parameter edited last wins), so the outline,
// the per-layer cuts drawn here, the fields and the resolved layers agree.
const _OS = () => window.OpeningShape;
function _asmShapeCtx() {
  const bead = window.Designer.beadWidth();
  return { lh: asm.state.layer_height || 1, h: Math.max(0, Math.min(_asmMaxOverhang(), bead)), bead };
}
const ASM_FIELD_NAMES = { width: 'Width', height: 'Height', upper_height: 'Upper height', bottom_depth: 'Lower depth' };
// Arch lower profiles (UI label ↔ stored value: Curved = 'arc', the vesica's
// sharp-pointed lower half; Rounded = 'round', the half ellipse — stored as
// 'curve' before 2026-10-10 and migrated)
const ASM_LOWERS = [['flat', 'Flat'], ['pointed', 'Angular'], ['arc', 'Curved'], ['round', 'Rounded']];
const ASM_SHAPE_FIELDS = ['shape', 'upper', 'lower', 'width', 'height', 'upper_height', 'middle', 'middle_height',
                          'bottom_depth', 'driver'];
// constrain one opening in place after an edit of `edited` → a short note of
// what else changed (shown on its card), or the conflict
function _asmConstrain(p, edited) {
  if (!p || p.kind !== 'opening' || p.source_opening) return null;
  const { lh, h, bead } = _asmShapeCtx();
  _OS().migrate(p);
  const [q, info] = _OS().constrain(p, edited, lh, h, bead);
  for (const k of ASM_SHAPE_FIELDS) if (q[k] !== undefined) p[k] = q[k];
  delete p.closure;
  const n = x => String(+(+x).toFixed(3));
  const parts = Object.entries(info.changed).map(([k, [a, b]]) => `${ASM_FIELD_NAMES[k] || k} ${n(a)} → ${n(b)} in`);
  const note = info.conflict ? `Cannot be made printable: ${info.conflict}.`
    : (parts.length ? `${parts.join(', ')} — to stay self-supporting (max overhang ${n(h)} in, ${n(lh)} in layers)` : null);
  asm.profNote = note ? { id: p.id, text: note, conflict: !!info.conflict } : (asm.profNote && asm.profNote.id === p.id ? null : asm.profNote);
  return note;
}
// every opening after a change of Maximum overhang / layer height / bead
// width (each keeps its driver: the dimension edited last)
function _asmConstrainAll(edited) {
  const notes = [];
  for (const p of _asmProfiles()) {
    if (p.kind !== 'opening' || p.source_opening) continue;
    const n = _asmConstrain(p, edited);
    if (n) notes.push(`P${_asmProfiles().indexOf(p) + 1}: ${n}`);
  }
  asm.profNote = notes.length ? { id: null, text: notes.join(' · ') } : null;
}
// the shape of an opening as resolved: {W, d, m, U, H, ok, shape, upper,
// lower} (d / m / U: lower depth, middle, upper height). A conversion: the
// Designer rectangle (its middle) + the closure above it (Umin: its minimum)
function _asmOpeningGeom(p) {
  const { lh, h, bead } = _asmShapeCtx(), O = _OS();
  if (p.source_opening) {
    const r = _asmProfileReport(p.id), cv = r && r.conversion;
    if (!cv) return null;
    const W = cv.width, m = cv.rect_top - cv.rect_bottom;
    const Umin = O.minUpperFixed(W, p.upper, cv.rect_top, lh, h, bead);
    const U = Umin == null ? cv.upper_height : Math.max(Umin, p.upper_height || 0);
    return { W, d: 0, m, U, H: m + U, Umin, ok: Umin != null, shape: 'arch', upper: p.upper, lower: 'flat' };
  }
  const q = { ...p };
  O.migrate(q); O.defaults(q); O.fill(q, lh, h, bead);
  const ev = O.evaluate(q, lh, h, bead);
  return { W: +q.width, d: ev.d, m: ev.m, U: ev.U, H: ev.H, ok: ev.ok, shape: q.shape, upper: q.upper, lower: q.lower };
}
// [[Δs, z]] of one opening's outline, left side bottom → top (mirror for the
// right): lower profile → straight sides → upper profile (header: flat top)
function _asmOpeningOutline(p) {
  const G = _asmOpeningGeom(p);
  if (!G) return [];
  const N = 48, out = [], O = _OS(), { W, d, m, U, H } = G;
  const at = t => O.widthAt(Math.min(t, H - 1e-9), W, d, m, U, G.shape, G.upper, G.lower) / 2;
  if (d > 0) for (let k = 0; k <= N; k++) out.push([at(d * k / N), d * k / N]);
  else out.push([0, 0], [W / 2, 0]);
  if (m > 0) out.push([W / 2, d + m]);
  if (U > 0) for (let k = 1; k <= N; k++) out.push([k === N ? 0 : at(d + m + U * k / N), d + m + U * k / N]);
  else out.push([0, H]);
  return out;
}
// every opening of a profile: [{s, z}] = centre position + bottom Z (the
// lower-left one first) — = profiles.OpeningArray.offsets
function _asmMembers(p) {
  const r = _asmProfileReport(p.id);
  if (p.source_opening) return r && r.conversion ? [{ s: r.conversion.position, z: r.conversion.rect_bottom }] : [];
  const A = p.source_opening ? null : p.array;
  return _OS().arrayOffsets(A, p.width, p.height).map(([dx, dz]) => ({ s: p.position + dx, z: p.z_bottom + dz }));
}
// the per-layer cuts of an opening as resolved (the same numbers as the
// backend: OpeningShape.layerWidths), on the layers whose design has the
// wall — drawn at once while editing, before the resolve returns
function _asmOpeningCuts(p) {
  if (p.source_opening) return null;
  const R = asm.resolved, insts = (R && R.instances) || [];
  const T = (R && R.profile_targets) || {}, key = _asmWallKey(p.wall);
  const has = d => (T[d] || []).some(t => (t.edge == null ? t.source : `${t.source}#${t.edge}`) === key);
  const { lh, h, bead } = _asmShapeCtx(), O = _OS(), G = _asmOpeningGeom(p);
  const out = [];
  for (const m of _asmMembers(p))
    for (const [k, w] of O.layerWidths(m.z, G.W, G.d, G.m, G.U, G.shape, G.upper, G.lower, lh, O.wmin(bead, h)))
      if (insts[k] && has(insts[k].design_id)) out.push({ layer: k, at: m.s, width: w });
  return out;
}

// the side a taper actually keeps ('auto' resolved by the backend)
function _asmKept(p) {
  if (p.keep !== 'auto') return p.keep || 'start';
  const r = _asmProfileReport(p.id);
  return (r && r.keep) || 'start';
}

function _asmKeepLabel(p, short) {
  const k = _asmKept(p);
  const side = k === 'start' ? 'left / start' : 'right / end';
  return short ? side : `the ${side} side${p.keep === 'auto' ? ' (auto)' : ''}`;
}

// handles of the selected profile: [{key, s, z}]
function _asmProfileHandles(p, t) {
  if (!p || !t) return [];
  if (p.kind === 'opening') {
    if (p.source_opening) return [];        // (a converted Designer opening: placed by the Designer)
    const G = _asmOpeningGeom(p);            // the widest point: the top of the lower profile / mid-sides
    const zw = p.z_bottom + (G ? G.d + G.m / 2 : p.height / 2);
    const top = p.z_bottom + (G ? G.H : p.height);
    const hs = [{ key: 'body', s: p.position, z: zw, tip: 'drag: position along the wall / bottom Z' },
                { key: 'side', s: p.position + p.width / 2, z: zw, tip: 'drag: width' }];
    if (p.shape === 'diamond') hs.push({ key: 'top', s: p.position, z: top, tip: 'drag: height' });
    else if (p.upper !== 'header') hs.push({ key: 'top', s: p.position, z: top, tip: 'drag: upper height (the apex)' });
    else if (p.middle) hs.push({ key: 'top', s: p.position, z: top, tip: 'drag: middle height (the flat top)' });
    return hs;
  }
  return p.knots.map((k, j) => ({ key: j ? 'hi' : 'lo', s: k[1], z: k[0],
                                  tip: 'drag: cut point (distance along the wall / Z)' }));
}

function _asmDrawProfiles(g, P) {
  const R = asm.resolved;
  if (!R) return;
  const targets = _asmProfileTargets();
  const top = _asmStackTop();
  const line = (pts, col, w, dash) => {
    if (pts.length < 2) return;
    g.strokeStyle = col; g.lineWidth = w;
    if (g.setLineDash) g.setLineDash(dash || []);
    g.beginPath();
    pts.forEach((q, k) => { const [x, y] = P(...q); if (k) g.lineTo(x, y); else g.moveTo(x, y); });
    g.stroke();
    if (g.setLineDash) g.setLineDash([]);
  };
  // choosing a wall: every eligible straight wall, bottom and top, ends joined
  if (asm.profPick) {
    for (const t of Object.values(targets)) {
      if (asm.profPick.kind === 'extent' && !t.taper) continue;
      const hot = asm.profHover === t.key;
      const col = hot ? '#ffffff' : ASM_PROFILE_COLOR;
      for (const z of [0, top]) line([_asmWallPoint(t, 0, z), _asmWallPoint(t, t.length, z)], col, hot ? 2.6 : 1.4);
      for (const s of [0, t.length]) line([_asmWallPoint(t, s, 0), _asmWallPoint(t, s, top)], col, 0.8, [3, 3]);
    }
    g.fillStyle = '#ffffff'; g.font = '11px monospace'; g.textAlign = 'left';
    g.fillText(`NEW ${asm.profPick.kind === 'opening' ? 'SELF-SUPPORTING OPENING' : 'WALL TAPER'}: click a straight wall ` +
               '(highlighted) — or choose it in the Profiles column · Esc cancels', 14, 34);
  }
  _asmProfiles().forEach((p, n) => {
    // RESULT mode draws no construction: only the profile being EDITED
    if (asm.profSel !== p.id) return;
    if (p.kind === 'plane') { _asmDrawPlane(g, P, p); return; }
    const t = targets[_asmWallKey(p.wall)];
    if (!t) return;
    const rep = _asmProfileReport(p.id);
    const sel = true;
    const bad = rep && !rep.valid;
    const col = bad ? '#ff6b6b' : ASM_PROFILE_COLOR;
    if (sel) {                          // the wall's elevation frame: length × stack height
      const fr = [[0, 0], [t.length, 0], [t.length, top], [0, top], [0, 0]].map(([s, z]) => _asmWallPoint(t, s, z));
      line(fr, '#888', 0.8, [2, 4]);
    }
    if (p.kind === 'opening') {
      // every opening of the profile (an array: all its clones), the master's outline at each
      const ol = _asmOpeningOutline(p);
      for (const m of _asmMembers(p)) {
        const left = ol.map(([ds, z]) => _asmWallPoint(t, m.s - ds, m.z + z));
        const right = ol.map(([ds, z]) => _asmWallPoint(t, m.s + ds, m.z + z));
        line(left.concat(right.reverse()), col, sel ? 2 : 1.2, bad ? [5, 4] : []);
      }
      // the cut of each layer (at its top): the same numbers the backend
      // resolves (OpeningShape.layerWidths) — drawn at once while editing;
      // a conversion: the resolved layers (its Designer rectangle grey)
      const cuts = _asmOpeningCuts(p) || (rep ? rep.layers : []);
      for (const L of cuts) {
        const inst = R.instances[L.layer];
        if (!inst) continue;
        const at = L.at != null ? L.at : p.position;
        line([_asmWallPoint(t, at - L.width / 2, inst.z_top), _asmWallPoint(t, at + L.width / 2, inst.z_top)],
             L.designer ? '#888' : col, sel ? 1.2 : 0.7);
      }
      // HEADER upper: each opening's header — its top at the boundary above
      // the last cut layer, hanging down its thickness, bearing each side
      if (p.shape === 'arch' && p.upper === 'header' && !p.source_opening) {
        const hs = _asmHeaderSettings(p), top = {};
        for (const L of cuts) if (R.instances[L.layer]) top[L.at] = Math.max(top[L.at] || 0, R.instances[L.layer].z_top);
        g.fillStyle = '#c8c8c8';
        for (const [at, zt] of Object.entries(top)) {
          const s0 = +at - p.width / 2 - hs.bearing, s1 = +at + p.width / 2 + hs.bearing, z0 = zt - hs.thickness;
          const quad = [[s0, z0], [s1, z0], [s1, zt], [s0, zt], [s0, z0]].map(([sv, z]) => _asmWallPoint(t, sv, z));
          g.globalAlpha = 0.35; g.beginPath();
          quad.forEach((q, k) => { const [x, y] = P(...q); if (k) g.lineTo(x, y); else g.moveTo(x, y); });
          g.fill(); g.globalAlpha = 1;
          line(quad, '#e0e0e0', 1.2);
        }
      }
    } else {
      // the CUT LINE between its points; the removed side shaded: from the
      // lower point's Z up, beyond the line (holding above the upper point)
      const [k0, k1] = p.knots;
      const E = _asmKept(p) === 'end' ? 0 : t.length;       // the removed side's wall end
      const zTop = Math.max(top, k1[0]);
      const region = [[E, k0[0]], [k0[1], k0[0]], [k1[1], k1[0]], [k1[1], zTop], [E, zTop]]
        .map(([sv, z]) => P(..._asmWallPoint(t, Math.max(0, Math.min(t.length, sv)), z)));
      g.fillStyle = col; g.globalAlpha = sel ? 0.16 : 0.08;
      g.beginPath(); region.forEach(([x, y], k) => (k ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath(); g.fill();
      g.globalAlpha = 1;
      line([_asmWallPoint(t, k0[1], k0[0]), _asmWallPoint(t, k1[1], k1[0])], col, sel ? 2.4 : 1.4, bad ? [5, 4] : []);
      line([_asmWallPoint(t, k1[1], k1[0]), _asmWallPoint(t, k1[1], zTop)], col, 1, [4, 3]);
      line([_asmWallPoint(t, E, k0[0]), _asmWallPoint(t, k0[1], k0[0])], col, 1, [4, 3]);
      if (rep) for (const L of rep.layers) {     // the resolved cut of each layer
        const inst = R.instances[L.layer];
        if (!inst || L.cut == null) continue;
        line([_asmWallPoint(t, L.cut, inst.z_bottom), _asmWallPoint(t, L.cut, inst.z_top)], col, sel ? 1.4 : 0.7);
      }
    }
    const m0 = p.kind === 'opening' ? _asmMembers(p)[0] : null, ol0 = m0 ? _asmOpeningOutline(p) : null;
    const lab = P(..._asmWallPoint(t, m0 ? m0.s : (p.knots ? p.knots[1][1] : p.position),
                                   m0 ? m0.z + (ol0.length ? ol0[ol0.length - 1][1] : 0)
                                      : (p.knots ? Math.min(top, p.knots[1][0]) : p.z_bottom)));
    g.fillStyle = col; g.font = '10px monospace'; g.textAlign = 'center';
    g.fillText(`P${n + 1}` + (bad ? ' · not printable' : ''), lab[0], lab[1] - 8);
    if (sel) for (const h of _asmProfileHandles(p, t)) {
      const [x, y] = P(..._asmWallPoint(t, h.s, h.z));
      const hot = asm.drag && asm.drag.handle === h.key;
      g.fillStyle = hot ? '#ffffff' : col; g.strokeStyle = '#111'; g.lineWidth = 1;
      g.fillRect(x - 4, y - 4, 8, 8); g.strokeRect(x - 4, y - 4, 8, 8);
    }
  });
  if (asm.elevation && _asmProfile(asm.elevation.id)) {
    const n = _asmProfiles().findIndex(p => p.id === asm.elevation.id);
    g.fillStyle = '#cccccc'; g.font = '11px monospace'; g.textAlign = 'right';
    const W = (asm.lastProj && asm.lastProj.W) || 600;
    g.fillText(asm.elevation.orbited ? `P${n + 1}: orbited — "Elevation" returns to its canonical view`
                                     : `P${n + 1} ELEVATION · horizontal = distance along the wall · vertical = Z`, W - 14, 18);
  }
  g.textAlign = 'left';
}

// the straight wall nearest a canvas point (choosing a wall)
function asmProfileWallAt(sx, sy) {
  const L = asm.lastProj;
  if (!L || !asm.profPick) return null;
  const top = _asmStackTop();
  let best = null, bd = 12;
  for (const t of Object.values(_asmProfileTargets())) {
    if (asm.profPick.kind === 'extent' && !t.taper) continue;
    for (let k = 0; k <= 8; k++) {
      const z = top * k / 8;
      const a = L.P(..._asmWallPoint(t, 0, z)), b = L.P(..._asmWallPoint(t, t.length, z));
      const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy || 1;
      const u = Math.max(0, Math.min(1, ((sx - a[0]) * dx + (sy - a[1]) * dy) / l2));
      const d = Math.hypot(a[0] + u * dx - sx, a[1] + u * dy - sy);
      if (d < bd) { bd = d; best = t.key; }
    }
  }
  return best;
}

function asmProfileHandleAt(sx, sy) {
  const L = asm.lastProj, p = _asmProfile(asm.profSel), t = p && p.kind !== 'plane' ? _asmProfileTarget(p) : null;
  if (!L || !p || !t) return null;
  for (const h of _asmProfileHandles(p, t)) {
    const [x, y] = L.P(..._asmWallPoint(t, h.s, h.z));
    if (Math.abs(x - sx) <= 7 && Math.abs(y - sy) <= 7) return h.key;
  }
  return null;
}

// dragging a handle: screen motion → (Δs along the wall, ΔZ) through the
// projected wall / Z axes at the handle (works in any orbit, exact in the
// elevation). ONE undo step, resolved on release.
function asmProfileDragStart(key, sx, sy) {
  const p = _asmProfile(asm.profSel), t = _asmProfileTarget(p), L = asm.lastProj;
  const h = _asmProfileHandles(p, t).find(x => x.key === key);
  const P = L.P, o = P(..._asmWallPoint(t, h.s, h.z));
  const es = P(..._asmWallPoint(t, h.s + 1, h.z)), ez = P(..._asmWallPoint(t, h.s, h.z + 1));
  asm.drag = { handle: key, before: _asmSnap(), moved: false, x0: sx, y0: sy,
               js: [es[0] - o[0], es[1] - o[1]], jz: [ez[0] - o[0], ez[1] - o[1]],
               orig: JSON.parse(JSON.stringify(p)) };
}

function asmProfileDragTo(sx, sy) {
  const d = asm.drag, p = _asmProfile(asm.profSel), t = _asmProfileTarget(p);
  if (!d || !d.handle || !p || !t) return;
  const r = [sx - d.x0, sy - d.y0], det = d.js[0] * d.jz[1] - d.js[1] * d.jz[0];
  let ds, dz;
  if (Math.abs(det) > 1e-9) {
    ds = (r[0] * d.jz[1] - r[1] * d.jz[0]) / det;
    dz = (d.js[0] * r[1] - d.js[1] * r[0]) / det;
  } else {                                   // the wall is seen end-on: Z only
    ds = 0; dz = (r[0] * d.jz[0] + r[1] * d.jz[1]) / (d.jz[0] ** 2 + d.jz[1] ** 2 || 1);
  }
  const o = d.orig;
  const lh = asm.state.layer_height || 1;
  if (p.kind === 'opening') {
    // from the ORIGINAL each move (no drift), constrained at once: the
    // outline, the per-layer cuts and the fields follow the printable shape
    for (const k of ASM_SHAPE_FIELDS) p[k] = o[k];
    if (d.handle === 'body') { p.position = _asmQ4(o.position + ds); p.z_bottom = _asmSnapZ(o.z_bottom + dz); _asmConstrain(p, 'z_bottom'); }
    else if (d.handle === 'top') {
      const f = p.shape === 'diamond' ? 'height' : (p.upper === 'header' ? 'middle_height' : 'upper_height');
      p[f] = Math.max(f === 'middle_height' ? 0 : lh, _asmSnapZ((+o[f] || 0) + dz));
      _OS().sync(p); _asmConstrain(p, f);
    } else if (d.handle === 'side') { p.width = Math.max(0.25, _asmQ4(o.width + 2 * ds)); _asmConstrain(p, 'width'); }
  } else {
    const j = d.handle === 'hi' ? 1 : 0;
    const kn = o.knots.map(k => k.slice());
    kn[j][1] = _asmQ4(Math.max(0, Math.min(t.length, kn[j][1] + ds)));
    kn[j][0] = _asmSnapZ(kn[j][0] + dz);
    // either diagonal can be drawn: a point dragged past the other swaps roles
    if (kn[0][0] !== kn[1][0]) p.knots = kn[0][0] < kn[1][0] ? kn : [kn[1], kn[0]];
  }
  d.moved = true;
  asmDrawPreview();
  if (p.kind === 'opening') _asmRenderProfiles();       // the dependent fields follow during the drag
}

function asmProfileDragEnd() {
  const d = asm.drag;
  asm.drag = null;
  if (!d || !d.handle) return;
  if (d.moved && _asmRecord(d.before, null)) return asmResolve();
  asmDrawPreview();
}

// ---- PLANAR TOP CUT (layer_assembly/profiles.py PlaneProfile, 2026-10-08) ----
// One plane z = z_anchor + slope · d·(p − anchor) (d = the UPHILL direction in
// plan, world coordinates) cuts a SELECTION of walls (sources ∪ Wall System
// members − exclude). Editing: PLAN view (pick walls / Wall Systems, drag the
// uphill arrow), then the plane: High / Low over the selected footprint ↔
// slope angle ↔ rise / run — views of the same four numbers; Flip Slope.
function _asmPlaneSources(d) {
  return ((asm.resolved && asm.resolved.profile_sources) || {})[d] || [];
}

function _asmPlaneDesigns() {
  return [...new Set(((asm.resolved && asm.resolved.instances) || []).map(i => i.design_id))];
}

// selected source ids of design d
function _asmPlaneSel(p, d) {
  const recs = _asmPlaneSources(d);
  const ids = new Set(p.sources || []);
  for (const r of recs) if (r.system && (p.systems || []).includes(r.system)) ids.add(r.source);
  for (const x of p.exclude || []) ids.delete(x);
  return new Set(recs.filter(r => ids.has(r.source)).map(r => r.source));
}

function _asmPlaneDir(p) { const a = (p.direction || 0) * Math.PI / 180; return [Math.cos(a), Math.sin(a)]; }

function _asmPlaneZ(p, x, y) {
  const [dx, dy] = _asmPlaneDir(p);
  return p.z_anchor + p.slope * ((x - p.anchor[0]) * dx + (y - p.anchor[1]) * dy);
}

// the same as a function of (x, y), direction precomputed (per-segment loops)
function _asmPlaneZf(p) {
  const [dx, dy] = _asmPlaneDir(p), ax = p.anchor[0], ay = p.anchor[1], z0 = p.z_anchor, k = p.slope;
  return (x, y) => z0 + k * ((x - ax) * dx + (y - ay) * dy);
}

// the selected footprint in WORLD coordinates (each design placed by its first layer)
function _asmPlaneFootprint(p) {
  const R = asm.resolved, out = [];
  if (!R) return out;
  for (const d of _asmPlaneDesigns()) {
    const inst = R.instances.find(i => i.design_id === d);
    const T = (inst && inst.transform) || { scale: 1, tx: 0, ty: 0 };
    const sel = _asmPlaneSel(p, d);
    for (const r of _asmPlaneSources(d)) if (sel.has(r.source))
      for (const [x, y] of r.points || []) out.push(_asmPlace(T, x, y));
  }
  return out;
}

// {s0, s1, low, high, run}: the footprint's extent along the uphill direction and the plane's Z there
function _asmPlaneHL(p) {
  const [dx, dy] = _asmPlaneDir(p);
  const pts = _asmPlaneFootprint(p);
  if (!pts.length) return null;
  const ss = pts.map(([x, y]) => x * dx + y * dy);
  const s0 = Math.min(...ss), s1 = Math.max(...ss);
  const zAt = sv => p.z_anchor + p.slope * (sv - (p.anchor[0] * dx + p.anchor[1] * dy));
  return { s0, s1, run: s1 - s0, low: zAt(s0), high: zAt(s1) };
}

// the same plane re-expressed through (direction, low at the footprint's downhill end, high at its uphill end)
function _asmPlaneFromHL(p, direction, high, low) {
  const q = { ...p, direction: ((direction % 360) + 360) % 360 };
  const [dx, dy] = _asmPlaneDir(q);
  const pts = _asmPlaneFootprint(q);
  const ss = pts.length ? pts.map(([x, y]) => x * dx + y * dy) : [0, 1];
  const s0 = Math.min(...ss), s1 = Math.max(...ss);
  q.slope = Math.max(0, (high - low) / Math.max(1e-9, s1 - s0));
  q.anchor = [_asmClean(dx * s0), _asmClean(dy * s0)];
  q.z_anchor = _asmClean(low);
  q.slope = Math.round(q.slope * 1e6) / 1e6;
  return q;
}

function asmPlaneCreate() {
  const id = 'p' + (asm.state.profile_counter = (asm.state.profile_counter || 0) + 1);
  const top = _asmStackTop() || 72;
  const p = { kind: 'plane', id, sources: [], systems: [], exclude: [], direction: 0, slope: 0,
              anchor: [0, 0], z_anchor: _asmSnapZ(0.75 * top), label: '' };
  asm.profPick = null; asm.profError = null;
  return asmEdit(null, () => {
    _asmProfiles().push(p);
    return asmResolve().then(() => asmProfileEdit(id));
  });
}

// selection edits (one undo step each)
function asmPlaneToggleSource(id, sid) {
  const p = _asmProfile(id); if (!p) return;
  return asmEdit(null, () => {
    const d = _asmPlaneDesigns().find(dd => _asmPlaneSources(dd).some(r => r.source === sid));
    const on = d ? _asmPlaneSel(p, d).has(sid) : (p.sources || []).includes(sid);
    const viaSystem = d && _asmPlaneSources(d).some(r => r.source === sid && r.system && (p.systems || []).includes(r.system));
    if (on) {
      p.sources = (p.sources || []).filter(x => x !== sid);
      if (viaSystem && !(p.exclude || []).includes(sid)) p.exclude = [...(p.exclude || []), sid];
    } else {
      p.exclude = (p.exclude || []).filter(x => x !== sid);
      if (!viaSystem && !(p.sources || []).includes(sid)) p.sources = [...(p.sources || []), sid];
    }
    _asmPlaneKeepHL(p);
    return asmResolve();
  });
}

function asmPlaneToggleSystem(id, sys) {
  const p = _asmProfile(id); if (!p) return;
  return asmEdit(null, () => {
    if ((p.systems || []).includes(sys)) p.systems = p.systems.filter(x => x !== sys);
    else {
      p.systems = [...(p.systems || []), sys];
      const members = new Set(_asmPlaneDesigns().flatMap(d => _asmPlaneSources(d).filter(r => r.system === sys).map(r => r.source)));
      p.exclude = (p.exclude || []).filter(x => !members.has(x));
    }
    _asmPlaneKeepHL(p);
    return asmResolve();
  });
}

// a new selection keeps a sensible plane: the first walls get a plane
// across them (high = the stack top, low = ¾ of it); later edits keep it
function _asmPlaneKeepHL(p) {
  if (p.slope === 0 && (p.anchor[0] === 0 && p.anchor[1] === 0) && _asmPlaneFootprint(p).length) {
    const top = _asmStackTop() || 72;
    Object.assign(p, _asmPlaneFromHL(p, p.direction || 0, _asmSnapZ(top), _asmSnapZ(0.6 * top)));
  }
}

// numeric fields: direction | high | low | angle | rise
// The plane's PLAN POSITION is its own degree of freedom (2026-10-08): the
// TOP CONTOUR — the line where the plane meets the stack top, i.e. where it
// begins to cut — is the handle. Rotating and changing the slope pivot about
// that line's point nearest the footprint centre; translating moves it along
// the uphill direction; nothing re-spans the plane over the footprint.
function _asmPlaneFootCentre(p) {
  const pts = _asmPlaneFootprint(p);
  if (!pts.length) return [p.anchor[0], p.anchor[1]];
  const xs = pts.map(q => q[0]), ys = pts.map(q => q[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

// {pivot, s}: the top contour's point nearest the footprint centre and its signed
// distance from the centre along the uphill direction (null for a level plane)
function _asmPlaneContour(p) {
  if (!(p.slope > 1e-12)) return null;
  const top = _asmStackTop() || 72;
  const [dx, dy] = _asmPlaneDir(p);
  const fc = _asmPlaneFootCentre(p);
  const sLine = p.anchor[0] * dx + p.anchor[1] * dy + (top - p.z_anchor) / p.slope;
  const sFc = fc[0] * dx + fc[1] * dy;
  return { pivot: [fc[0] + dx * (sLine - sFc), fc[1] + dy * (sLine - sFc)], s: sLine - sFc, z: top };
}

// the plane through the pivot (at the stack top) with a new direction / slope
function _asmPlaneAbout(p, direction, slope) {
  const c = _asmPlaneContour(p);
  const piv = c ? c.pivot : _asmPlaneFootCentre(p);
  const z = c ? c.z : p.z_anchor;
  return { direction: ((Math.round(direction * 1e6) / 1e6) % 360 + 360) % 360, slope: Math.max(0, Math.round(slope * 1e6) / 1e6),
           anchor: [_asmClean(piv[0]), _asmClean(piv[1])], z_anchor: _asmClean(z) };
}

// numeric fields: direction | shift | angle | rise | high | low
function asmPlaneSet(id, field, v) {
  const p = _asmProfile(id); if (!p) return;
  const x = parseFloat(v);
  if (!isFinite(x)) { asmRender(); return; }
  const hl = _asmPlaneHL(p) || { high: p.z_anchor, low: p.z_anchor, run: 1 };
  let q;
  if (field === 'direction') q = _asmPlaneAbout(p, Math.round(x), p.slope);
  else if (field === 'angle') q = _asmPlaneAbout(p, p.direction, Math.tan(Math.max(0, Math.min(89.9, x)) * Math.PI / 180));
  else if (field === 'rise') q = _asmPlaneAbout(p, p.direction, Math.max(0, x));
  else if (field === 'shift') {                        // TRANSLATE: the top contour to x in from the centre
    const c = _asmPlaneContour(p);
    if (!c) { q = { z_anchor: _asmSnapZ(x) }; }        // (a level plane: its Z)
    else {
      const [dx, dy] = _asmPlaneDir(p), dd = _asmQ16(x) - c.s;
      q = { anchor: [_asmClean(p.anchor[0] + dx * dd), _asmClean(p.anchor[1] + dy * dd)] };
    }
  }
  else if (field === 'high') q = _asmPlaneFromHL(p, p.direction, Math.max(_asmSnapZ(x), hl.low), hl.low);
  else if (field === 'low') q = _asmPlaneFromHL(p, p.direction, hl.high, Math.min(_asmSnapZ(x), hl.high));
  else return;
  return asmEdit(`pp${id}.${field}`, () => { Object.assign(p, q); return asmResolve(); });
}

// FLIP SLOPE: uphill reversed about the top contour — same slope, same place
function asmPlaneFlip(id) {
  const p = _asmProfile(id); if (!p) return;
  return asmEdit(null, () => { Object.assign(p, _asmPlaneAbout(p, (p.direction || 0) + 180, p.slope)); return asmResolve(); });
}

function asmPlaneView(id, which) {
  const p = _asmProfile(id); if (!p) return;
  if (which === 'plan') Object.assign(asm.view, ASM_VIEWS.top, { zoom: 1, px: 0, py: 0 });
  else {                                   // the slope seen side-on: screen x = the uphill direction
    Object.assign(asm.view, { yaw: -(p.direction || 0) * Math.PI / 180, pitch: 0.12, zoom: 1, px: 0, py: 0 });
  }
  asm.elevation = { id: p.id, orbited: false, plane: which };
  asmRender();
}

// the plane handle in plan: [tail = the top contour's pivot, tip = uphill]
function _asmPlaneArrow(p) {
  const pts = _asmPlaneFootprint(p);
  let r = 30;
  if (pts.length) {
    const xs = pts.map(q => q[0]), ys = pts.map(q => q[1]);
    r = Math.max(20, 0.3 * Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)));
  }
  const c = _asmPlaneContour(p);
  const t = c ? c.pivot : _asmPlaneFootCentre(p);
  const [dx, dy] = _asmPlaneDir(p);
  return [[t[0], t[1]], [t[0] + dx * r, t[1] + dy * r]];
}

// the top contour as a plan segment spanning the footprint (for drawing)
function _asmPlaneContourSeg(p) {
  const c = _asmPlaneContour(p);
  if (!c) return null;
  const pts = _asmPlaneFootprint(p);
  const [dx, dy] = _asmPlaneDir(p);
  const vs = pts.length ? pts.map(([x, y]) => -x * dy + y * dx) : [0];
  const v0 = Math.min(...vs) - 12, v1 = Math.max(...vs) + 12, vp = -c.pivot[0] * dy + c.pivot[1] * dx;
  const at = v => [c.pivot[0] - dy * (v - vp), c.pivot[1] + dx * (v - vp)];   // along e = (−dy, dx)
  return [at(v0), at(v1)];
}

function _asmDrawPlane(g, P, p) {
  const R = asm.resolved, rep = _asmProfileReport(p.id);
  const top = _asmStackTop();
  // the selected walls, highlighted (bottom and top of the stack)
  g.strokeStyle = ASM_PROFILE_COLOR; g.lineWidth = 2.2;
  for (const d of _asmPlaneDesigns()) {
    const inst = R.instances.find(i => i.design_id === d);
    const T = (inst && inst.transform) || { scale: 1, tx: 0, ty: 0 };
    const sel = _asmPlaneSel(p, d);
    for (const r of _asmPlaneSources(d)) {
      if (!sel.has(r.source)) continue;
      const pts = (r.points || []).map(([x, y]) => _asmPlace(T, x, y));
      if (r.closed && pts.length) pts.push(pts[0]);
      g.beginPath();
      pts.forEach(([x, y], k) => { const [a, b] = P(x, y, 0); if (k) g.lineTo(a, b); else g.moveTo(a, b); });
      g.stroke();
    }
  }
  // the PLANE over the footprint (its corners at the plane's Z), translucent
  const pts = _asmPlaneFootprint(p);
  if (pts.length) {
    const [dx, dy] = _asmPlaneDir(p);
    const us = pts.map(([x, y]) => x * dx + y * dy), vs = pts.map(([x, y]) => -x * dy + y * dx);
    const u0 = Math.min(...us) - 6, u1 = Math.max(...us) + 6, v0 = Math.min(...vs) - 6, v1 = Math.max(...vs) + 6;
    const corner = (u, v) => { const x = u * dx - v * dy, y = u * dy + v * dx; return P(x, y, Math.max(0, Math.min(top * 1.5, _asmPlaneZ(p, x, y)))); };
    const quad = [corner(u0, v0), corner(u1, v0), corner(u1, v1), corner(u0, v1)];
    g.fillStyle = ASM_PROFILE_COLOR; g.globalAlpha = 0.14;
    g.beginPath(); quad.forEach(([x, y], k) => (k ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath(); g.fill();
    g.globalAlpha = 0.9; g.strokeStyle = ASM_PROFILE_COLOR; g.lineWidth = 1; g.stroke(); g.globalAlpha = 1;
  }
  // the TOP CONTOUR (where the plane begins to cut; drag it to move the plane)
  // and the uphill arrow from it (drag its tip to turn the plane)
  const seg = _asmPlaneContourSeg(p);
  if (seg) {
    const [c0, c1] = seg.map(([x, y]) => P(x, y, top));
    g.strokeStyle = '#ffd166'; g.lineWidth = 2.2; if (g.setLineDash) g.setLineDash([6, 4]);
    g.beginPath(); g.moveTo(c0[0], c0[1]); g.lineTo(c1[0], c1[1]); g.stroke();
    if (g.setLineDash) g.setLineDash([]);
  }
  const [a, b] = _asmPlaneArrow(p).map(([x, y]) => P(x, y, top));
  g.strokeStyle = '#ffffff'; g.lineWidth = 2;
  g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
  const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
  g.beginPath(); g.moveTo(b[0], b[1]);
  g.lineTo(b[0] - 10 * Math.cos(ang - 0.4), b[1] - 10 * Math.sin(ang - 0.4));
  g.lineTo(b[0] - 10 * Math.cos(ang + 0.4), b[1] - 10 * Math.sin(ang + 0.4)); g.closePath();
  g.fillStyle = '#ffffff'; g.fill();
  g.fillRect(b[0] - 4, b[1] - 4, 8, 8);
  g.fillStyle = '#ffd166'; g.fillRect(a[0] - 5, a[1] - 5, 10, 10);
  g.fillStyle = '#cccccc'; g.font = '10px monospace'; g.textAlign = 'center';
  g.fillText('uphill', b[0], b[1] - 10);
  g.fillText('cut begins', a[0], a[1] + 16);
  g.textAlign = 'left';
  if (rep && !rep.valid) { g.fillStyle = '#ff6b6b'; g.fillText('P: ' + rep.errors.join(' · '), 14, 34); }
}

// EDIT mode: the material the plane removes, ghosted (selected walls of each cut layer above the plane)
function _asmDrawPlaneGhost(g, P, stack, p) {
  // (from the plane AS IT IS NOW — not the last resolved report — so the
  // removed zone follows a drag / an edit immediately)
  if (!(p.sources || []).length && !(p.systems || []).length) return;
  g.strokeStyle = '#ff9f43'; g.lineWidth = 1; g.globalAlpha = 0.5;
  if (g.setLineDash) g.setLineDash([3, 3]);
  g.beginPath();                                   // one path: thousands of segments, one stroke
  const zf = _asmPlaneZf(p), step = asm.drag ? _asmDragStep(stack) : 1;
  for (const inst of stack) {
    if (inst.index % step) continue;
    const geo = asm.geometry[inst.design_id];
    if (!geo) continue;
    const sel = _asmPlaneSel(p, inst.design_id);
    if (!sel.size) continue;
    const z = (inst.z_bottom + inst.z_top) / 2, T = inst.transform, sc = T.scale, tx = T.tx, ty = T.ty;
    for (const pl of geo.polylines || []) {
      const n = pl.pts.length, segN = pl.closed ? n : n - 1;
      for (let k = 0; k < segN; k++) {
        const s0 = pl.src && pl.src[k];
        if (s0 && !sel.has(s0)) continue;
        const a = pl.pts[k], b = pl.pts[(k + 1) % n];
        const ax = sc * a[0] + tx, ay = sc * a[1] + ty, bx = sc * b[0] + tx, by = sc * b[1] + ty;
        if (zf((ax + bx) / 2, (ay + by) / 2) >= z) continue;
        const A = P(ax, ay, inst.z_top), B = P(bx, by, inst.z_top);
        g.moveTo(A[0], A[1]); g.lineTo(B[0], B[1]);
      }
    }
  }
  g.stroke();
  if (g.setLineDash) g.setLineDash([]);
  g.globalAlpha = 1;
}

// plan-view interaction: the arrow tip turns the uphill direction (5° steps);
// a click on a wall toggles it in the selection
function asmPlaneMouseDown(sx, sy) {
  const p = _asmProfile(asm.profSel), L = asm.lastProj;
  if (!p || p.kind !== 'plane' || !L) return false;
  const top = _asmStackTop();
  const [tail, tipW] = _asmPlaneArrow(p);
  const tip = L.P(...tipW, top), piv = L.P(...tail, top);
  if (Math.abs(tip[0] - sx) <= 8 && Math.abs(tip[1] - sy) <= 8) {
    asm.drag = { plane: 'direction', before: _asmSnap(), moved: false };
    return true;
  }
  const seg = _asmPlaneContourSeg(p);
  const onLine = seg && (() => {
    const [a0, a1] = seg.map(([x, y]) => L.P(x, y, top));
    const dx = a1[0] - a0[0], dy = a1[1] - a0[1], l2 = dx * dx + dy * dy || 1;
    const u = Math.max(0, Math.min(1, ((sx - a0[0]) * dx + (sy - a0[1]) * dy) / l2));
    return Math.hypot(a0[0] + u * dx - sx, a0[1] + u * dy - sy) <= 6;
  })();
  if ((Math.abs(piv[0] - sx) <= 8 && Math.abs(piv[1] - sy) <= 8) || onLine) {
    asm.drag = { plane: 'move', before: _asmSnap(), moved: false, x0: sx, y0: sy,
                 anchor0: p.anchor.slice() };
    return true;
  }
  const sid = asmPlaneWallAt(sx, sy);
  if (sid) { asmPlaneToggleSource(p.id, sid); return true; }
  return false;
}

function asmPlaneWallAt(sx, sy) {
  const L = asm.lastProj, R = asm.resolved;
  if (!L || !R) return null;
  let best = null, bd = 8;
  for (const d of _asmPlaneDesigns()) {
    const inst = R.instances.find(i => i.design_id === d);
    const T = (inst && inst.transform) || { scale: 1, tx: 0, ty: 0 };
    for (const r of _asmPlaneSources(d)) {
      const pts = (r.points || []).map(([x, y]) => L.P(..._asmPlace(T, x, y), 0));
      if (r.closed && pts.length) pts.push(pts[0]);
      for (let k = 0; k + 1 < pts.length; k++) {
        const [ax, ay] = pts[k], [bx, by] = pts[k + 1];
        const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1;
        const u = Math.max(0, Math.min(1, ((sx - ax) * dx + (sy - ay) * dy) / l2));
        const dd = Math.hypot(ax + u * dx - sx, ay + u * dy - sy);
        if (dd < bd) { bd = dd; best = r.source; }
      }
    }
  }
  return best;
}

// screen motion → plan vector through the projected X / Y axes at a point
function _asmPlanVector(L, at, z, sx, sy, s0x, s0y) {
  const c = L.P(at[0], at[1], z);
  const ex = L.P(at[0] + 1, at[1], z), ey = L.P(at[0], at[1] + 1, z);
  const jx = [ex[0] - c[0], ex[1] - c[1]], jy = [ey[0] - c[0], ey[1] - c[1]];
  const r = [sx - s0x, sy - s0y], det = jx[0] * jy[1] - jx[1] * jy[0];
  if (Math.abs(det) < 1e-9) return null;
  return [(r[0] * jy[1] - r[1] * jy[0]) / det, (jx[0] * r[1] - jx[1] * r[0]) / det];
}

function asmPlaneDragTo(sx, sy) {
  const p = _asmProfile(asm.profSel), L = asm.lastProj, d = asm.drag;
  if (!p || !L || !d) return;
  const top = _asmStackTop();
  if (d.plane === 'move') {
    // TRANSLATE perpendicular to the plane's line (along the uphill direction), ¼ in
    const w = _asmPlanVector(L, d.anchor0, top, sx, sy, d.x0, d.y0);
    if (!w) return;
    const [dx, dy] = _asmPlaneDir(p);
    const t = _asmQ4(w[0] * dx + w[1] * dy);
    p.anchor = [_asmClean(d.anchor0[0] + dx * t), _asmClean(d.anchor0[1] + dy * t)];
  } else {
    // ROTATE about the top contour's pivot, 5° steps; the slope is kept
    const tail = _asmPlaneArrow(p)[0], c = L.P(tail[0], tail[1], top);
    const w = _asmPlanVector(L, tail, top, sx, sy, c[0], c[1]);
    if (!w) return;
    const deg = Math.round(Math.atan2(w[1], w[0]) * 180 / Math.PI / 5) * 5;
    Object.assign(p, _asmPlaneAbout(p, deg, p.slope));
  }
  d.moved = true;
  _asmDrawSoon();
}

// pointer moves can outpace frames: draw at most once per animation frame
function _asmDrawSoon() {
  if (asm._drawQueued) return;
  asm._drawQueued = true;
  const run = () => { asm._drawQueued = false; asmDrawPreview(); };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run); else run();
}

function asmPlaneDragEnd() {
  const d = asm.drag;
  asm.drag = null;
  if (d && d.moved && _asmRecord(d.before, null)) return asmResolve();
  asmDrawPreview();
}

// ---- the Profiles column ----
function _asmRenderProfiles() {
  const box = document.getElementById('asm-profiles');
  if (!box) return;
  box.innerHTML = ''; if (Array.isArray(box.children)) box.children.length = 0;   // (test DOM stub)
  const targets = _asmProfileTargets();
  const row = (...kids) => { const r = _el('div', 'asm-row'); kids.forEach(k => r.appendChild(k)); return r; };
  const lh = asm.state.layer_height || 1;
  const input = (value, onchange, step, field) => {
    const inp = _el('input', 'prop-input'); inp.type = 'number'; inp.step = String(step);
    inp.value = Math.round(value * 10000) / 10000; inp.onchange = () => onchange(inp.value);
    if (field) inp.dataset.field = field;
    return inp;
  };
  const num = (label, value, onchange, tip, step, field) => {
    const r = _el('div', 'asm-row'); if (tip) r.title = tip;
    r.appendChild(_el('span', 'prop-label', label));
    r.appendChild(input(value, onchange, step, field)); r.appendChild(_el('span', 'prop-unit', 'in'));
    return r;
  };
  // a cut point: distance along the wall + Z on one row
  const point = (id, label, k, fs, fz) => {
    const r = _el('div', 'asm-row');
    r.title = 'cut point: distance along the wall from its start · Z (whole layers)';
    r.appendChild(_el('span', 'prop-label', label));
    r.appendChild(input(k[1], v => asmProfileSet(id, fs, v), 1, fs));
    r.appendChild(_el('span', 'prop-unit', '@ z'));
    r.appendChild(input(k[0], v => asmProfileSet(id, fz, v), lh, fz));
    return r;
  };

  const sel = (label, value, opts, onchange) => {
    const r = _el('div', 'asm-row');
    r.appendChild(_el('span', 'prop-label', label));
    const s = _el('select', 'prop-input');
    for (const [v, lab] of opts) { const o = _el('option', null, lab); o.value = v; s.appendChild(o); }
    s.value = value; s.onchange = () => onchange(s.value);
    r.appendChild(s);
    return r;
  };
  const add1 = _el('button', 'mini-btn' + (asm.profPick && asm.profPick.kind === 'opening' ? ' active' : ''), '+ Self-supporting opening');
  add1.title = 'A headerless through-wall opening whose width closes gradually with Z';
  add1.onclick = () => asmProfileBegin('opening');
  // (Wall Taper is no longer a tool of its own: a Planar Top Cut on one wall does it — 2026-10-08)
  const add3 = _el('button', 'mini-btn', '+ Planar top cut');
  add3.title = 'One flat plane cuts the selected walls / Wall Systems (one wall, a shape, several systems, the whole building): their material above it is removed';
  add3.onclick = () => asmPlaneCreate();
  box.appendChild(row(add1, add3));
  if (asm.profPick) {
    const k = asm.profPick.kind;
    box.appendChild(_el('div', 'asm-calc', 'Click a straight wall in the preview, or pick it here (curved walls cannot be profiled yet).'));
    const s = _el('select', 'prop-input');
    const none = _el('option', null, '— straight walls —'); none.value = ''; s.appendChild(none);
    for (const t of Object.values(targets)) {
      if (k === 'extent' && !t.taper) continue;
      const o = _el('option', null, `${t.label} (${_fmt(t.length)} in)`); o.value = t.key; s.appendChild(o);
    }
    s.onchange = () => { if (s.value) asmProfileCreate(k, s.value); };
    const cancel = _el('button', 'mini-btn', 'Cancel'); cancel.onclick = () => asmProfileCancelPick();
    box.appendChild(row(s, cancel));
    if (!Object.keys(targets).length) box.appendChild(_el('div', 'asm-diff', 'No straight walls in the designs of this stack.'));
  }
  if (asm.profError) box.appendChild(_el('div', 'asm-diff', asm.profError));
  _asmProfiles().forEach((p, n) => {
    if (p.kind === 'plane') { box.appendChild(_asmPlaneCard(p, n, row, input, sel)); return; }
    if (p.kind === 'extent') { box.appendChild(_asmLegacyTaperCard(p, n)); return; }
    const t = targets[_asmWallKey(p.wall)];
    const rep = _asmProfileReport(p.id);
    const card = _el('div', 'asm-support-card asm-profile' + (asm.profSel === p.id ? ' asm-selected' : '') +
                      (rep && !rep.valid ? ' asm-profile-bad' : ''));
    card.dataset.profile = p.id;
    const editing = asm.profSel === p.id;
    card.dataset.mode = editing ? 'edit' : 'result';
    const head = _el('div', 'asm-row');
    head.appendChild(_el('span', 'asm-support-title', `P${n + 1} · ${p.source_opening ? `Converted opening ${p.label || p.source_opening.id}`
      : (p.kind === 'opening' ? ((p.shape === 'arch' && p.upper === 'header' ? 'Header opening' : 'Self-supporting opening') + (p.array ? ' array' : ''))
                              : 'Wall taper')}`));
    const act = _el('button', editing ? 'mini-btn active' : 'mini-btn', editing ? 'Done' : 'Edit');
    act.dataset.action = editing ? 'done' : 'edit';
    act.title = editing ? 'Leave edit mode: the preview shows the resolved result (the profile stays editable)'
                        : 'Edit this profile in its elevation (handles, cut line, removed geometry ghosted)';
    act.onclick = () => (editing ? asmProfileDone() : asmProfileEdit(p.id));
    const del = _el('button', 'remove-btn', '×'); del.title = 'Delete profile'; del.onclick = () => asmProfileDelete(p.id);
    head.appendChild(act); head.appendChild(del);
    card.appendChild(head);
    card.appendChild(_el('div', 'asm-calc', `wall: ${t ? `${t.label} · ${_fmt(t.length)} in · ${_fmt(t.thickness)} in thick` : _asmWallKey(p.wall) + ' (not in this stack)'}`));
    if (!editing) {                     // RESULT: a one-line summary + status
      card.appendChild(_el('div', 'asm-calc', p.kind === 'opening' ? _asmOpeningSummary(p, rep)
        : `cut ${_fmt(p.knots[0][1])} in @ z ${_fmt(p.knots[0][0])} → ${_fmt(p.knots[1][1])} in @ z ${_fmt(p.knots[1][0])} · keep ${_asmKeepLabel(p)}`));
    } else if (p.kind === 'opening') {
      const row = _el('div', 'asm-row');
      const el = _el('button', 'mini-btn', 'Elevation'); el.title = 'Back to its canonical elevation (distance along the wall × Z)';
      el.onclick = () => asmProfileElevation(p.id);
      row.appendChild(el); card.appendChild(row);
      const opt = (label, value, onchange, tip, step, field, unit, ph) => {   // a number field; blank = automatic
        const r = num(label, value == null ? '' : value, onchange, tip, step, field);
        const inp = r.querySelector ? r.querySelector('input') : r.children[1];
        if (inp) { inp.placeholder = ph || '—'; if (value == null) inp.value = ''; }
        if (unit && r.children[2]) r.children[2].textContent = unit;
        return r;
      };
      const head2 = t => { const e = _el('div', 'asm-subhead', t); return e; };
      const G = _asmOpeningGeom(p);
      const STYLE = [['pointed', 'Angular'], ['curve', 'Curved']];
      const r2 = v => Math.round((v || 0) * 10000) / 10000;
      if (!p.source_opening) {
        card.appendChild(head2('Shape'));
        card.appendChild(sel('Type', p.shape, [['diamond', 'Diamond'], ['arch', 'Arch']],
                             v => asmProfileSet(p.id, 'shape', v)));
        if (p.shape === 'diamond') {
          card.appendChild(sel('Profile', p.upper || 'pointed', STYLE, v => asmProfileSet(p.id, 'upper', v)));
        } else {
          card.appendChild(sel('Upper', p.upper || 'pointed', [...STYLE, ['header', 'Header']],
                               v => asmProfileSet(p.id, 'upper', v)));
          const mr = _el('div', 'asm-row'); mr.title = 'an optional rectangular section between the lower and upper profiles';
          mr.appendChild(_el('span', 'prop-label', 'Middle'));
          const cb = _el('input'); cb.type = 'checkbox'; cb.checked = !!p.middle; cb.dataset.field = 'middle';
          cb.onchange = () => asmProfileSet(p.id, 'middle', cb.checked);
          mr.appendChild(cb);
          card.appendChild(mr);
          card.appendChild(sel('Lower', p.lower || 'flat', ASM_LOWERS, v => asmProfileSet(p.id, 'lower', v)));
        }
        card.appendChild(head2('Position'));
        card.appendChild(num('Horizontal', p.position, v => asmProfileSet(p.id, 'position', v),
                             p.array ? 'centre of the LEFT-most opening, along the wall from its start' : 'centre, measured along the wall from its start', 1, 'position'));
        card.appendChild(num('Bottom Z', p.z_bottom, v => asmProfileSet(p.id, 'z_bottom', v),
                             p.array ? 'bottom of the LOWEST opening — whole layers (0 = a door at ground level)' : 'whole layers (0 = a door at ground level)', lh, 'z_bottom'));
        card.appendChild(head2('Dimensions'));
        card.appendChild(num('Width', p.width, v => asmProfileSet(p.id, 'width', v),
                             p.upper === 'header' && p.shape === 'arch' ? 'clear width (the header spans it)'
                               : 'clear width at the widest point — a wider opening makes the ' + (p.shape === 'diamond' ? 'height' : 'upper height') + ' grow if it must, to stay self-supporting', 0.5, 'width'));
        if (p.shape === 'diamond') {
          card.appendChild(num('Height', p.height, v => asmProfileSet(p.id, 'height', v),
                               'total height, whole layers — a lower diamond makes the width shrink if it must, to stay self-supporting', lh, 'height'));
        } else {
          if (p.upper !== 'header')
            card.appendChild(num('Upper height', r2(p.upper_height), v => asmProfileSet(p.id, 'upper_height', v),
                                 'height of the pointed upper profile, spring to apex, whole layers — a lower one makes the width shrink if it must', lh, 'upper_height'));
          if (p.middle)
            card.appendChild(num('Middle height', r2(p.middle_height), v => asmProfileSet(p.id, 'middle_height', v),
                                 'the straight-sided rectangular section, whole layers', lh, 'middle_height'));
          if (p.lower !== 'flat')
            card.appendChild(num('Lower depth', r2(p.bottom_depth), v => asmProfileSet(p.id, 'bottom_depth', v),
                                 `depth of the ${{ round: 'rounded (half the width = a semicircle)', arc: 'curved, sharp-pointed (deeper than half the width)', pointed: 'angular' }[p.lower] || 'lower'} lower profile, whole layers`, lh, 'bottom_depth'));
          const tot = _el('div', 'asm-row asm-total'); tot.title = 'lower depth + middle + upper height (calculated)';
          tot.appendChild(_el('span', 'prop-label', 'Total height'));
          const tv = _el('span', 'asm-total-value', _fmt(G ? G.H : p.height)); tv.dataset.field = 'total_height';
          tot.appendChild(tv); tot.appendChild(_el('span', 'prop-unit', 'in'));
          card.appendChild(tot);
          if (p.upper === 'header') {
            const hs = _asmHeaderSettings(p);
            card.appendChild(_el('div', 'asm-calc asm-header-line',
              `Header: ${_fmt(p.width)} + 2 × ${_fmt(hs.bearing)} in bearing, ${_fmt(hs.thickness)} in thick, hangs into the top of the opening` +
              `${p.array ? ' — one per opening' : ''} · edit it under Support`));
          }
        }
        card.appendChild(head2('Array'));
        card.appendChild(_asmArrayFields(p, sel, num, opt));
      } else {
        const cv = rep && rep.conversion;
        card.appendChild(head2('Shape'));
        card.appendChild(sel('Upper', p.upper || 'pointed', STYLE, v => asmProfileSet(p.id, 'upper', v)));
        if (G) {
          const r = num('Upper height', p.upper_height != null ? p.upper_height : r2(G.U),
                        v => asmProfileSet(p.id, 'upper_height', v),
                        'height of the closure above the doorway, whole layers — blank = the lowest self-supporting one; taller = gentler', lh, 'upper_height');
          card.appendChild(r);
        }
        card.appendChild(_el('div', 'asm-calc', cv
          ? `Designer opening ${cv.opening}: ${_fmt(cv.width)} in clear × ${_fmt(cv.rect_top - cv.rect_bottom)} in (z ${_fmt(cv.rect_bottom)}–${_fmt(cv.rect_top)}) — kept as authored`
          : `Designer opening ${p.source_opening.id}`));
      }
      if (asm.profNote && (asm.profNote.id === p.id))
        card.appendChild(_el('div', asm.profNote.conflict ? 'asm-diff' : 'asm-calc asm-shape-note', asm.profNote.text));
    } else {
      const row = _el('div', 'asm-row');
      const el = _el('button', 'mini-btn', 'Elevation'); el.title = 'Back to its canonical elevation (distance along the wall × Z)';
      el.onclick = () => asmProfileElevation(p.id);
      const mir = _el('button', 'mini-btn', 'Mirror cut'); mir.dataset.action = 'mirror';
      mir.title = 'The same cut from the other end of the wall (s → length − s) and the other side kept — e.g. keep the other end of a buttress';
      mir.onclick = () => asmProfileMirror(p.id);
      row.appendChild(el); row.appendChild(mir); card.appendChild(row);
      const end = (t && t.attached || []);
      // the elevation always shows the wall's START on the LEFT
      const lab = w => `${w === 'start' ? 'Left (start)' : 'Right (end)'} side` + (end.includes(w) ? ' — attached' : '');
      card.appendChild(sel('Keep', p.keep || 'start',
                           [['auto', `Auto — the supported side (${_asmKeepLabel(p, true)})`],
                            ['start', lab('start')], ['end', lab('end')]],
                           v => asmProfileSet(p.id, 'keep', v)));
      card.appendChild(point(p.id, 'Lower point', p.knots[0], 's_lower', 'z_lower'));
      card.appendChild(point(p.id, 'Upper point', p.knots[1], 's_upper', 'z_upper'));
    }
    if (rep) {
      if (rep.valid) {
        const nl = new Set(rep.layers.filter(L => !L.designer).map(L => L.layer)).size;
        const nm = (rep.members || []).length;
        card.appendChild(_el('div', 'asm-calc', p.kind === 'opening'
          ? (p.upper === 'header' && !p.source_opening
              ? `${nm > 1 ? nm + ' openings · ' : ''}${nl} layer${nl === 1 ? '' : 's'} cut · flat top on ${(rep.headers || []).length} header${(rep.headers || []).length === 1 ? '' : 's'}`
              : `self-supporting: ${nm > 1 ? nm + ' openings · ' : ''}${nl} layer${nl === 1 ? '' : 's'} cut · largest side step ${_fmt(rep.worst_step)} in (max overhang ${_fmt(_asmMaxOverhang())} in) · no header`)
          : `${nl} layer${nl === 1 ? '' : 's'} cut · the kept wall retreats (largest outward step ${_fmt(rep.worst_step)} in)`));
      } else {
        card.appendChild(_el('div', 'asm-diff', 'NOT GENERATED — ' + rep.errors.join(' · ')));
        if (rep.hint && rep.hint.min_height != null) {
          const hn = rep.hint.field === 'upper_height' ? 'upper height' : 'height';
          card.appendChild(_el('div', 'asm-diff', `Self-supporting needs ${hn} ≥ ${_fmt(rep.hint.min_height)} in` +
            (rep.hint.max_width != null ? ` at this width, or width ≤ ${_fmt(rep.hint.max_width)} in at this ${hn}.` : '.')));
        }
        if (p.source_opening)
          card.appendChild(_el('div', 'asm-calc', 'Nothing is cut: the Designer opening prints as before (and needs its header). Delete this conversion (×) to remove it.'));
      }
      for (const w of rep.warnings || []) card.appendChild(_el('div', 'asm-calc', '⚠ ' + w));
      const fs = (((asm.resolved || {}).support || {}).findings || []).filter(f => f.profile === p.id && f.status !== 'supported');
      if (fs.length) card.appendChild(_el('div', 'asm-diff',
        `${fs.length} support finding${fs.length === 1 ? '' : 's'} at this opening — see Support` +
        (p.upper === 'header' ? '' : '; no header is proposed')));
    }
    box.appendChild(card);
  });
}

function _asmOpeningSummary(p, rep) {
  const nm = { pointed: 'angular', curve: 'curved', flat: 'flat', header: 'header', arc: 'curved', round: 'rounded' };
  const shape = p.shape === 'arch'
    ? `Arch (${nm[p.upper] || 'angular'} upper${p.middle ? ', middle' : ''}, ${nm[p.lower] || 'flat'} lower)`
    : `Diamond (${nm[p.upper] || 'angular'})`;
  const cv = rep && rep.conversion;
  if (p.source_opening)
    return cv ? `${shape}: ${_fmt(cv.width)} in clear, rectangle z ${_fmt(cv.rect_bottom)}–${_fmt(cv.rect_top)} kept + closure ${_fmt(cv.upper_height)} in`
              : `${shape} over Designer opening ${p.source_opening.id}`;
  const A = p.array, lab = Object.fromEntries(ASM_ARRANGEMENTS);
  const arr = !A ? '' : ` · ${lab[A.arrangement] || A.arrangement}: ` +
    (['grid', 'staggered'].includes(A.arrangement) ? `${A.cols} × ${A.rows}` : `${A.count}`) + ` (clear ${_fmt(A.h_spacing)} / ${_fmt(A.v_spacing)} in)`;
  return `${shape} ${_fmt(p.width)} × ${_fmt(p.height)} in at ${_fmt(p.position)} in, from Z ${_fmt(p.z_bottom)}${arr}`;
}

// the HEADER settings of a Header-upper opening (profiles.OpeningProfile.header_settings)
function _asmHeaderSettings(p) {
  const t = _asmProfileTarget(p), D = _OS().HEADER_DEFAULTS, hs = { ...D };
  for (const [k, v] of Object.entries(p.header || {})) if (v != null) hs[k] = v;
  if (hs.depth == null) hs.depth = Math.round((((t && t.thickness) || 0) + window.Designer.beadWidth()) * 1e4) / 1e4;
  return hs;
}

// the ARRAY block of an opening card: arrangement + only the fields it uses
function _asmArrayFields(p, sel, num, opt) {
  const box = _el('div', 'asm-array');
  const A = p.array || { arrangement: 'single' };
  box.appendChild(sel('Array', A.arrangement, ASM_ARRANGEMENTS, v => asmProfileSet(p.id, 'array.arrangement', v)));
  if (!p.array) return box;
  const ar = A.arrangement, diag = ar.startsWith('diagonal');
  const cnt = (label, field, tip) => {
    const r = num(label, A[field], v => asmProfileSet(p.id, 'array.' + field, v), tip, 1, 'array.' + field);
    if (r.children[2]) r.children[2].textContent = '×';
    return r;
  };
  if (['grid', 'staggered'].includes(ar)) {
    box.appendChild(cnt('Columns', 'cols', 'openings per row'));
    box.appendChild(cnt('Rows', 'rows', 'rows of openings'));
  } else box.appendChild(cnt('Count', 'count', 'openings in the array'));
  if (ar !== 'vertical')
    box.appendChild(num(diag ? 'H offset' : 'H spacing', A.h_spacing, v => asmProfileSet(p.id, 'array.h_spacing', v),
      diag ? 'horizontal CLEAR distance between neighbouring openings (edge to edge)' : 'CLEAR distance between neighbouring openings (edge to edge, at their widest)', 0.5, 'array.h_spacing'));
  if (ar !== 'horizontal')
    box.appendChild(num(diag ? 'V offset' : 'V spacing', A.v_spacing, v => asmProfileSet(p.id, 'array.v_spacing', v),
      diag ? 'vertical CLEAR distance from one opening\'s top to the next one\'s bottom (negative: they overlap in height, apart sideways)'
           : 'CLEAR distance from one opening\'s top to the next one\'s bottom', asm.state.layer_height || 1, 'array.v_spacing'));
  if (ar === 'staggered')
    box.appendChild(opt('Stagger', A.stagger, v => asmProfileSet(p.id, 'array.stagger', v),
      'sideways shift of every second row — blank = half the horizontal repeat', 0.5, 'array.stagger', null, '½'));
  return box;
}

// a WALL TAPER from an older state (no longer a tool): still resolved as it was;
// "Convert" replaces it by the equivalent Planar Top Cut on its wall
function _asmLegacyTaperCard(p, n) {
  const rep = _asmProfileReport(p.id), t = _asmProfileTarget(p);
  const card = _el('div', 'asm-support-card asm-profile' + (rep && !rep.valid ? ' asm-profile-bad' : ''));
  card.dataset.profile = p.id; card.dataset.mode = 'legacy';
  const head = _el('div', 'asm-row');
  head.appendChild(_el('span', 'asm-support-title', `P${n + 1} · Wall taper (older profile)`));
  const cv = _el('button', 'mini-btn', 'Convert to planar top cut'); cv.dataset.action = 'convert';
  cv.title = 'The same cut line as one plane over this wall (the plane extends the line below its lower point too)';
  cv.onclick = () => asmTaperToPlane(p.id);
  const del = _el('button', 'remove-btn', '×'); del.onclick = () => asmProfileDelete(p.id);
  head.appendChild(cv); head.appendChild(del); card.appendChild(head);
  card.appendChild(_el('div', 'asm-calc', `${t ? t.label : _asmWallKey(p.wall)} · cut ${_fmt(p.knots[0][1])} in @ z ${_fmt(p.knots[0][0])} → ` +
                                          `${_fmt(p.knots[p.knots.length - 1][1])} in @ z ${_fmt(p.knots[p.knots.length - 1][0])}`));
  if (rep && !rep.valid) card.appendChild(_el('div', 'asm-diff', 'NOT GENERATED — ' + rep.errors.join(' · ')));
  return card;
}

// WALL TAPER → PLANAR TOP CUT: the plane through the taper's cut line (its two
// points on the wall) rising toward the kept side; one undo step
function asmTaperToPlane(id) {
  const p = _asmProfile(id), t = _asmProfileTarget(p);
  if (!p || !t || p.kind !== 'extent') return;
  const [k0, k1] = [p.knots[0], p.knots[p.knots.length - 1]];
  const L = t.length || 1, ux = (t.b[0] - t.a[0]) / L, uy = (t.b[1] - t.a[1]) / L;
  const keep = _asmKept(p);
  const toward = keep === 'start' ? -1 : 1;              // uphill = toward the kept side along the wall
  const run = toward * (k1[1] - k0[1]);                   // + when the cut retreats toward the kept side as Z rises
  const slope = run > 1e-9 ? (k1[0] - k0[0]) / run : 1000;   // (a vertical / growing cut: a near-vertical plane)
  const q = { kind: 'plane', id: p.id, sources: [p.wall.source], systems: [], exclude: [],
              direction: ((Math.atan2(toward * uy, toward * ux) * 180 / Math.PI) % 360 + 360) % 360,
              slope: Math.round(slope * 1e6) / 1e6,
              anchor: [_asmClean(t.a[0] + ux * k0[1]), _asmClean(t.a[1] + uy * k0[1])], z_anchor: k0[0], label: p.label || '' };
  return asmEdit(null, () => {
    const i = _asmProfiles().indexOf(p);
    _asmProfiles()[i] = q;
    return asmResolve();
  });
}

// the PLANAR TOP CUT card: result = a summary; edit = selection + plane fields
function _asmPlaneCard(p, n, row, input, sel) {
  const rep = _asmProfileReport(p.id);
  const editing = asm.profSel === p.id;
  const card = _el('div', 'asm-support-card asm-profile' + (editing ? ' asm-selected' : '') + (rep && !rep.valid ? ' asm-profile-bad' : ''));
  card.dataset.profile = p.id;
  card.dataset.mode = editing ? 'edit' : 'result';
  const head = _el('div', 'asm-row');
  head.appendChild(_el('span', 'asm-support-title', `P${n + 1} · Planar top cut`));
  const act = _el('button', editing ? 'mini-btn active' : 'mini-btn', editing ? 'Done' : 'Edit');
  act.dataset.action = editing ? 'done' : 'edit';
  act.onclick = () => (editing ? asmProfileDone() : asmProfileEdit(p.id));
  const del = _el('button', 'remove-btn', '×'); del.title = 'Delete profile'; del.onclick = () => asmProfileDelete(p.id);
  head.appendChild(act); head.appendChild(del); card.appendChild(head);
  const designs = _asmPlaneDesigns();
  const nsel = new Set(designs.flatMap(d => [..._asmPlaneSel(p, d)])).size;
  const hl = _asmPlaneHL(p);
  const ang = Math.atan(p.slope || 0) * 180 / Math.PI;
  if (!editing) {
    card.appendChild(_el('div', 'asm-calc', `${nsel} wall${nsel === 1 ? '' : 's'} · ` +
      (hl ? `high ${_fmt(hl.high)} → low ${_fmt(hl.low)} in · ${_fmt(ang)}° · uphill ${Math.round(p.direction)}°` : 'no walls selected')));
  } else {
    const views = row();
    const v1 = _el('button', 'mini-btn', 'Plan'); v1.title = 'Bird\'s-eye view: pick walls, turn the uphill arrow';
    v1.onclick = () => asmPlaneView(p.id, 'plan');
    const v2 = _el('button', 'mini-btn', 'Slope view'); v2.title = 'Side-on along the uphill direction: see the plane';
    v2.onclick = () => asmPlaneView(p.id, 'slope');
    const fl = _el('button', 'mini-btn', 'Flip slope'); fl.dataset.action = 'flip';
    fl.title = 'Swap the high and low ends (same magnitude)'; fl.onclick = () => asmPlaneFlip(p.id);
    views.appendChild(v1); views.appendChild(v2); views.appendChild(fl); card.appendChild(views);
    // WALL SYSTEMS (all members) + walls (click in plan, or here)
    const systems = new Map();
    for (const d of designs) for (const r of _asmPlaneSources(d)) if (r.system) systems.set(r.system, r.system_label || r.system);
    if (systems.size) {
      card.appendChild(_el('div', 'asm-calc', 'Wall Systems (all members):'));
      for (const [sid, lab] of systems) {
        const b = _el('button', 'mini-btn' + ((p.systems || []).includes(sid) ? ' active' : ''), lab);
        b.dataset.system = sid; b.onclick = () => asmPlaneToggleSystem(p.id, sid);
        card.appendChild(b);
      }
    }
    card.appendChild(_el('div', 'asm-calc', `Walls (${nsel}) — click a wall in the plan view to add / remove it:`));
    const seen = new Set();
    for (const d of designs) for (const r of _asmPlaneSources(d)) {
      if (seen.has(r.source)) continue; seen.add(r.source);
      const on = _asmPlaneSel(p, d).has(r.source);
      const b = _el('button', 'mini-btn' + (on ? ' active' : ''), r.label + (r.system_label ? ` (${r.system_label})` : ''));
      b.dataset.source = r.source; b.onclick = () => asmPlaneToggleSource(p.id, r.source);
      card.appendChild(b);
    }
    const lh = asm.state.layer_height || 1;
    const num = (label, value, field, step, unit, tip) => {
      const r = _el('div', 'asm-row'); if (tip) r.title = tip;
      r.appendChild(_el('span', 'prop-label', label));
      const inp = input(value, v => asmPlaneSet(p.id, field, v), step, field);
      r.appendChild(inp); r.appendChild(_el('span', 'prop-unit', unit));
      return r;
    };
    card.appendChild(num('Uphill dir.', p.direction || 0, 'direction', 5, '°', 'horizontal direction in which the plane rises (plan, 0° = +X) — turns about the cut-begins line'));
    const ct = _asmPlaneContour(p);
    card.appendChild(ct ? num('Cut begins', ct.s, 'shift', 1, 'in', 'where the plane meets the stack top: its distance from the selection centre along the uphill direction (moves the plane, slope kept)')
                        : num('Level at Z', p.z_anchor, 'shift', lh, 'in', 'a level plane: everything selected above this Z is removed'));
    card.appendChild(num('Slope', ang, 'angle', 1, '°', 'angle above horizontal (turns about the cut-begins line)'));
    card.appendChild(num('Rise / run', p.slope, 'rise', 0.01, '', 'the same slope: rise per inch of run'));
    if (hl) {
      card.appendChild(num('Z uphill edge', hl.high, 'high', lh, 'in', 'the plane at the uphill extreme of the selected footprint (editing it changes the slope about the downhill edge)'));
      card.appendChild(num('Z downhill edge', hl.low, 'low', lh, 'in', 'the plane at the downhill extreme of the selected footprint'));
    }
  }
  if (rep) {
    if (!rep.valid) card.appendChild(_el('div', 'asm-diff', 'NOT GENERATED — ' + rep.errors.join(' · ')));
    else card.appendChild(_el('div', 'asm-calc', `${rep.layers.length} layer${rep.layers.length === 1 ? '' : 's'} cut · a planar top only removes material as it rises (always supported)`));
    for (const w of rep.warnings || []) card.appendChild(_el('div', 'asm-calc', '⚠ ' + w));
  }
  return card;
}

// EDIT mode: the source geometry the edited profile REMOVES, ghosted — each
// cut layer's uncut design (the canonical geometry) where it lies in that
// layer's removed zone (opening: within its resolved width; taper: beyond
// its resolved cut, on the removed side), dashed orange under the result.
function _asmRemovedZone(p, t, L) {
  const ux = (t.b[0] - t.a[0]) / t.length, uy = (t.b[1] - t.a[1]) / t.length;
  const half = (t.thickness || 0) / 2 + window.Designer.beadWidth();
  return q => {
    const dx = q[0] - t.a[0], dy = q[1] - t.a[1];
    const sv = dx * ux + dy * uy;
    if (Math.abs(dx * uy - dy * ux) > half || sv < -half || sv > t.length + half) return false;
    if (p.kind === 'opening') return Math.abs(sv - (L.at != null ? L.at : p.position)) < L.width / 2;
    if (L.retained === 0) return true;
    return _asmKept(p) === 'end' ? sv < L.cut : sv > L.cut;
  };
}

function _asmDrawRemovedGhost(g, P, stack) {
  const pp = _asmProfile(asm.profSel);
  if (pp && pp.kind === 'plane') return _asmDrawPlaneGhost(g, P, stack, pp);
  const p = pp, t = _asmProfileTarget(p), rep = p && _asmProfileReport(p.id);
  if (!p || !t || !rep || !rep.valid) return;
  g.strokeStyle = '#ff9f43'; g.lineWidth = 1; g.globalAlpha = 0.55;
  if (g.setLineDash) g.setLineDash([3, 3]);
  for (const L of rep.layers) {
    const inst = stack[L.layer], geo = inst && asm.geometry[inst.design_id];
    if (!geo) continue;
    const zone = _asmRemovedZone(p, t, L);
    for (const pl of geo.polylines || []) {
      const n = pl.pts.length, segN = pl.closed ? n : n - 1;
      for (let k = 0; k < segN; k++) {
        const a = pl.pts[k], b = pl.pts[(k + 1) % n];
        if (!zone([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])) continue;
        const A = P(..._asmPlace(inst.transform, a[0], a[1]), inst.z_top), B = P(..._asmPlace(inst.transform, b[0], b[1]), inst.z_top);
        g.beginPath(); g.moveTo(A[0], A[1]); g.lineTo(B[0], B[1]); g.stroke();
      }
    }
  }
  if (g.setLineDash) g.setLineDash([]);
  g.globalAlpha = 1;
}

// While a profile's canonical elevation is shown (not orbited away), only
// geometry near its wall is drawn at full strength: in an elevation every
// parallel wall projects onto the same place and would hide the cut.
// → (design point) → near the profiled wall? | null
function _asmElevationFocus() {
  const e = asm.elevation;
  if (!e || e.orbited) return null;
  const t = _asmProfileTarget(_asmProfile(e.id));
  if (!t) return null;
  const r = (t.thickness || 0) / 2 + 2 * window.Designer.beadWidth();
  const [ax, ay] = t.a, [bx, by] = t.b;
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy || 1;
  return q => {
    const u = Math.max(0, Math.min(1, ((q[0] - ax) * dx + (q[1] - ay) * dy) / l2));
    return Math.hypot(ax + u * dx - q[0], ay + u * dy - q[1]) <= r;
  };
}

// ---- 3D stack preview (orthographic, canvas 2D) ----------------------------------
const ASM_VIEWS = {
  iso: { yaw: -Math.PI / 4, pitch: 0.55 }, front: { yaw: 0, pitch: 0.0 },
  side: { yaw: -Math.PI / 2, pitch: 0.0 }, top: { yaw: 0, pitch: Math.PI / 2 },
};

function asmViewPreset(name) {
  if (name === 'elevation') {
    const ep = _asmProfile(asm.profSel || (asm.elevation && asm.elevation.id));
    if (ep && ep.kind === 'plane') return asmPlaneView(ep.id, 'plan');
    return asmProfileElevation(asm.profSel || (asm.elevation && asm.elevation.id));
  }
  if (asm.elevation) asm.elevation.orbited = true;
  if (name === 'fit') Object.assign(asm.view, { zoom: 1, px: 0, py: 0 });
  else Object.assign(asm.view, ASM_VIEWS[name], { zoom: 1, px: 0, py: 0 });
  asmDrawPreview();
}

// Every layer instance with its Z and its design's printable centrelines
// (the build_stack() data): what is drawn.
function asmStack() {
  if (!asm.resolved) return [];
  const prev = _asmPlanePreview();
  return asm.resolved.instances.map(i => {
    const L = { ...i, transform: i.transform || { scale: 1, tx: 0, ty: 0 }, parts: i.parts || null };
    if (!prev) {
      L.polylines = ((i.geometry_key && asm.variants[i.geometry_key]) || asm.geometry[i.design_id] || {}).polylines || [];
      return L;
    }
    // (preview: clipped only when a layer is actually drawn; bounds / level of
    // detail from its uncut design — `basePolylines`)
    L.basePolylines = (asm.geometry[i.design_id] || {}).polylines || [];
    let memo = null;
    Object.defineProperty(L, 'polylines', { get: () => memo || (memo = prev(L)), enumerable: true });
    return L;
  });
}

// LIVE PLANE PREVIEW: while the planar cuts differ from those the shown
// result was resolved for (a drag, a field edit, a member toggle awaiting its
// resolve), every layer is drawn as its UNCUT design with the selected walls'
// centre-line segments above the planes (as they are NOW) left out — instant,
// approximate (no caps / re-closed ends; other profile cuts not shown) and
// replaced by the exact resolved geometry as soon as it arrives.
function _asmPlanePreview() {
  const key = _asmPlanesKey();
  if (asm.resolvedPlanes == null || key === asm.resolvedPlanes || key === '[]') return null;
  const planes = _asmProfiles().filter(p => p.kind === 'plane');
  if (asm._prev && asm._prev.key === key) return asm._prev.fn;
  const memo = {};
  const fn = inst => {
    const geo = asm.geometry[inst.design_id];
    if (!geo) return [];
    const z = (inst.z_bottom + inst.z_top) / 2, T = inst.transform || { scale: 1, tx: 0, ty: 0 };
    const act = planes.map(p => [_asmPlaneZf(p), _asmPlaneSel(p, inst.design_id)]).filter(([, sel]) => sel.size);
    const mk = inst.design_id + '|' + z + '|' + T.scale + ',' + T.tx + ',' + T.ty;
    if (memo[mk]) return memo[mk];
    const out = [], sc = T.scale, tx = T.tx, ty = T.ty;
    for (const pl of geo.polylines || []) {
      const n = pl.pts.length, segN = pl.closed ? n : n - 1;
      const gone = k => {
        const s0 = pl.src && pl.src[k];
        if (!s0) return false;
        const a = pl.pts[k], b = pl.pts[(k + 1) % n];
        const mx = sc * (a[0] + b[0]) / 2 + tx, my = sc * (a[1] + b[1]) / 2 + ty;
        for (const [zf, sel] of act) if (sel.has(s0) && zf(mx, my) < z) return true;
        return false;
      };
      if (!act.length || !pl.src) { out.push(pl); continue; }
      let run = null;
      for (let k = 0; k < segN; k++) {
        if (gone(k)) { run = null; continue; }
        if (!run) { run = { pts: [pl.pts[k]], closed: false }; out.push(run); }
        run.pts.push(pl.pts[(k + 1) % n]);
      }
    }
    return (memo[mk] = out);
  };
  asm._prev = { key, fn };
  return fn;
}

// Every vertex of a layer is placed by the layer's OWN transform. Transform
// groups are never applied to resolved beads here: a grouped layer's
// polylines are already its SEMANTIC variant (walls moved and re-resolved by
// the Designer, connecting walls regenerated).
function _asmPlace(t, x, y) { return [t.scale * x + t.tx, t.scale * y + t.ty]; }

function asmProjector(stack, W, H) {
  // bounds of the PLACED stack (each design's bbox under each instance transform)
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  const bb = {};
  for (const L of stack) {
    const gk = L.geometry_key || L.design_id;
    if (!(gk in bb)) bb[gk] = _asmBBox(L.basePolylines || L.polylines);
    const b = bb[gk];
    if (!b) continue;
    for (const T of [L.transform, ...Object.values(L.parts || {})])
      for (const [x, y] of [[b[0], b[1]], [b[2], b[3]]]) {
        const [px, py] = _asmPlace(T, x, y);
        x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py);
      }
  }
  if (!isFinite(x0)) { x0 = 0; x1 = 100; y0 = 0; y1 = 100; }
  const zt = stack.length ? stack[stack.length - 1].z_top : 0;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, cz = zt / 2;
  const { yaw, pitch, zoom, px, py } = asm.view;
  const cyw = Math.cos(yaw), syw = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  const rot = (x, y, z) => {
    const a = (x - cx) * cyw - (y - cy) * syw;
    const b = (x - cx) * syw + (y - cy) * cyw;
    return [a, (z - cz) * cp + b * sp];
  };
  // fit the bounding box in any orientation
  const span = Math.max(1, Math.hypot(x1 - x0, y1 - y0), zt);
  const s = 0.8 * Math.min(W, H) / span * zoom;
  return (x, y, z) => { const [a, b] = rot(x, y, z); return [W / 2 + a * s + px, H / 2 - b * s + py]; };
}

function asmDrawPreview() {
  const cv = document.getElementById('asm-canvas');
  const wrap = document.getElementById('asm-preview-wrap');
  if (!cv || !cv.getContext) return;
  const W = Math.max(200, wrap.clientWidth || 600), H = Math.max(200, wrap.clientHeight || 500);
  if (cv.width !== W) cv.width = W;
  if (cv.height !== H) cv.height = H;
  const g = cv.getContext('2d');
  g.fillStyle = '#1a1a1a'; g.fillRect(0, 0, W, H);
  const stack = asmStack();
  if (!stack.length) {
    g.fillStyle = '#666'; g.font = '12px monospace';
    g.fillText(asm.error || 'Add a section to see the stack.', 20, 30);
    return;
  }
  const P = asmProjector(stack, W, H);
  asm.lastProj = { P, W, H };
  // level of detail while dragging (orbit, a plane handle) a large stack:
  // every step-th layer (+ each section's first) — the full stack once released
  const step = asm.drag ? _asmDragStep(stack) : 1;
  const starts = new Set((asm.resolved.sections || []).filter(s => s.layers > 0).map(s => s.first_layer));
  const byGroup = asm.state.center_mode === 'multiple' && asm.state.centers.length > 0;
  // build plate outline
  const z0 = 0;
  g.lineWidth = 1;
  const foc = _asmElevationFocus();
  for (const L of stack) {
    if (L.index % step && !starts.has(L.index)) continue;
    const col = _asmStrokeColor(L.design_id, null);
    g.strokeStyle = col;
    const alpha = starts.has(L.index) ? 1.0 : 0.62;     // a section's first layer: subtly marked
    g.globalAlpha = alpha;
    g.lineWidth = starts.has(L.index) ? 1.4 : 0.8;
    for (let j = 0; j < L.polylines.length; j++) {
      const pl = L.polylines[j];
      if (pl.pts.length < 2) continue;
      if (foc) {                                   // profile elevation: other walls dimmed
        const n = pl.pts.length, segN = pl.closed ? n : n - 1;
        const pt = k => P(..._asmPlace(L.transform, pl.pts[k % n][0], pl.pts[k % n][1]), L.z_top);
        const near = k => foc(pl.pts[k % n]) || foc(pl.pts[(k + 1) % n]);
        let k = 0;
        while (k < segN) {
          const on = near(k);
          g.globalAlpha = on ? alpha : 0.08;
          g.beginPath();
          let q = pt(k); g.moveTo(q[0], q[1]);
          while (k < segN && near(k) === on) { k++; q = pt(k); g.lineTo(q[0], q[1]); }
          g.stroke();
        }
        g.globalAlpha = alpha;
        continue;
      }
      if (byGroup && pl.src) {                     // membership: each vertex run in its group's colour
        const n = pl.pts.length, segN = pl.closed ? n : n - 1;
        const pt = k => P(..._asmPlace(L.transform, pl.pts[k % n][0], pl.pts[k % n][1]), L.z_top);
        let k = 0;
        while (k < segN) {
          const gid = _asmGroupOf(pl.src[k]);
          g.strokeStyle = _asmStrokeColor(L.design_id, pl.src[k]);
          g.beginPath();
          let q = pt(k); g.moveTo(q[0], q[1]);
          while (k < segN && _asmGroupOf(pl.src[k]) === gid) { k++; q = pt(k); g.lineTo(q[0], q[1]); }
          g.stroke();
        }
        continue;
      }
      g.beginPath();
      const [sx, sy] = P(..._asmPlace(L.transform, pl.pts[0][0], pl.pts[0][1]), L.z_top);
      g.moveTo(sx, sy);
      for (let k = 1; k < pl.pts.length; k++) {
        const [qx, qy] = P(..._asmPlace(L.transform, pl.pts[k][0], pl.pts[k][1]), L.z_top);
        g.lineTo(qx, qy);
      }
      if (pl.closed) g.closePath();
      g.stroke();
    }
  }
  g.globalAlpha = 1;
  _asmDrawRemovedGhost(g, P, stack);
  _asmDrawSupport(g, P);
  _asmDrawCenters(g, P, stack);
  _asmDrawProfiles(g, P);
  // Z ruler: section boundaries with their Z and design
  g.font = '10px monospace';
  g.textAlign = 'left';
  const secs = asm.resolved.sections;
  const lx = 14;
  const zTop = stack[stack.length - 1].z_top;
  const bar = z => H - 30 - (H - 60) * (z / Math.max(zTop, 1e-9));
  g.strokeStyle = '#444'; g.beginPath(); g.moveTo(lx + 6, bar(0)); g.lineTo(lx + 6, bar(zTop)); g.stroke();
  for (const s of secs) {
    if (!s.layers) continue;
    g.fillStyle = _asmColor(s.design_id);
    g.fillRect(lx + 3, bar(s.z_top), 6, bar(s.z_bottom) - bar(s.z_top));
    g.fillText(`${_asmName(s.design_id)}  ${s.layers}×`, lx + 14, (bar(s.z_top) + bar(s.z_bottom)) / 2 + 3);
    g.fillStyle = '#888';
    g.fillText(`z ${_fmt(s.z_top)}`, lx + 14, bar(s.z_top) + 3);
  }
  g.fillStyle = '#888';
  g.fillText('z 0', lx + 14, bar(0) + 3);
  g.fillText(`${asm.resolved.total_layers} layers · ${_fmt(zTop)} in · layer ${asm.state.layer_height} in` +
             (asm.busy ? ' · building…' : '') +
             (_asmPlanePreview() ? ' · PREVIEW — exact cut resolving…' : asm.resolving ? ' · resolving…' : ''), 14, 18);
}

// layer stride while dragging: ≤ ~40k drawn vertices per frame
const ASM_DRAG_BUDGET = 40000;
function _asmDragStep(stack) {
  let segs = 0;
  for (const L of stack) for (const p of (L.basePolylines || L.polylines)) segs += p.pts.length;
  return segs > ASM_DRAG_BUDGET ? Math.ceil(segs / ASM_DRAG_BUDGET) : 1;
}

// Headers as rectangular solids (not mud: light grey, translucent faces);
// unsupported spans in red (ignored: dashed amber) on the layer that bridges
function _asmDrawSupport(g, P) {
  const S = asm.resolved && asm.resolved.support;
  if (!S) return;
  for (const h of S.headers) {
    if (h.error || !h.rect) continue;
    const bot = h.rect.map(([x, y]) => P(x, y, h.z_bottom)), top = h.rect.map(([x, y]) => P(x, y, h.z_top));
    const faces = [bot, top, ...[0, 1, 2, 3].map(k => [bot[k], bot[(k + 1) % 4], top[(k + 1) % 4], top[k]])];
    g.fillStyle = 'rgba(200, 200, 205, 0.22)';
    g.strokeStyle = '#d8d8dc'; g.lineWidth = 1.2;
    for (const f of faces) {
      g.beginPath(); g.moveTo(f[0][0], f[0][1]);
      for (const q of f.slice(1)) g.lineTo(q[0], q[1]);
      g.closePath(); g.fill(); g.stroke();
    }
    const c = P(h.centre[0], h.centre[1], h.z_top);
    g.fillStyle = '#d8d8dc'; g.font = '10px monospace'; g.textAlign = 'center';
    g.fillText(`header ${h.id} · ${_fmt(h.length)} in`, c[0], c[1] - 8);
  }
  // the affected geometry: restrained highlights (overlap thin + translucent,
  // header spans bold red, ignored dashed amber) — no text per finding
  for (const f of S.findings) {
    if (f.status === 'supported') continue;
    const z = asm.resolved.instances[f.layer].z_top;
    const overlap = f.status === 'insufficient_support';
    g.strokeStyle = f.status === 'overridden' ? '#f0a030' : overlap ? '#c56cf0' : '#ff4d4d';
    g.lineWidth = overlap ? 1.4 : 2.6;
    g.globalAlpha = overlap ? 0.7 : 1.0;
    if (g.setLineDash) g.setLineDash(f.status === 'overridden' ? [5, 4] : []);
    for (const r of f.runs) {
      if (r.length < 2) continue;
      g.beginPath();
      const a = P(r[0][0], r[0][1], z); g.moveTo(a[0], a[1]);
      for (const q of r.slice(1)) { const b = P(q[0], q[1], z); g.lineTo(b[0], b[1]); }
      g.stroke();
    }
    if (g.setLineDash) g.setLineDash([]);
    g.globalAlpha = 1.0;
  }
  // labels: ONE per aggregated group (at its worst layer), de-cluttered
  const labels = asmSupportLabels(P);
  g.font = '10px monospace'; g.textAlign = 'center';
  for (const L of labels) { g.fillStyle = L.color; g.fillText(L.text, L.x, L.y); }
  asm.lastLabels = labels;
  g.textAlign = 'left';
}

// transform groups: a ring + cross on the build plate (in the group's colour;
// dashed while it has no walls) and the pivot's path up the stack
function _asmDrawCenters(g, P, stack) {
  _asmCenters().forEach((c, n) => {
    const p = [c.x, c.y];
    const has = (c.sources || []).length > 0;
    const col = _asmGroupColor(c.id) || '#d0d0d8';
    if (has) {
      g.strokeStyle = col; g.globalAlpha = 0.45; g.lineWidth = 1;
      if (g.setLineDash) g.setLineDash([3, 3]);
      g.beginPath();
      let started = false;
      for (const L of stack) {
        const T = L.parts && L.parts[c.id];
        if (!T) continue;
        const [x, y] = P(T.scale * p[0] + T.tx, T.scale * p[1] + T.ty, L.z_top);
        if (started) g.lineTo(x, y); else { g.moveTo(x, y); started = true; }
      }
      g.stroke();
      g.globalAlpha = 1;
      if (g.setLineDash) g.setLineDash([]);
    }
    const [cx, cy] = P(p[0], p[1], 0);
    const hot = (asm.drag && asm.drag.center === c.id) || asm.selCenter === c.id;
    g.strokeStyle = hot ? '#ffffff' : col; g.lineWidth = hot ? 2.2 : 1.4;
    if (!has && g.setLineDash) g.setLineDash([2, 2]);
    g.beginPath(); g.arc(cx, cy, 6, 0, 2 * Math.PI); g.stroke();
    if (g.setLineDash) g.setLineDash([]);
    g.beginPath(); g.moveTo(cx - 9, cy); g.lineTo(cx + 9, cy); g.moveTo(cx, cy - 9); g.lineTo(cx, cy + 9); g.stroke();
    g.fillStyle = col; g.font = '10px monospace'; g.textAlign = 'left';
    g.fillText(`C${n + 1}` + (has ? '' : ' (no walls)'), cx + 9, cy - 7);
  });
  if (asm.assigning) {
    g.fillStyle = '#ffffff'; g.font = '11px monospace'; g.textAlign = 'left';
    const n = asm.state.centers.findIndex(c => c.id === asm.selCenter);
    g.fillText(`ASSIGN GEOMETRY → C${n + 1}: click a wall (click one of its walls again to remove it)`, 14, 34);
  }
}

// ---- preview interaction: orbit / pan / zoom ---------------------------------------
function _asmInitPreview() {
  const cv = document.getElementById('asm-canvas');
  if (!cv || !cv.addEventListener) return;
  cv.addEventListener('contextmenu', e => e.preventDefault());
  const local = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  cv.addEventListener('mousedown', e => {
    if (e.button === 0 && !e.shiftKey) {
      if (asmPlaneMouseDown(...local(e))) return;
      const ph = asmProfileHandleAt(...local(e));
      if (ph) { asmProfileDragStart(ph, ...local(e)); return; }
      if (asm.profPick) {
        const w = asmProfileWallAt(...local(e));
        if (w) { asmProfileCreate(asm.profPick.kind, w); return; }
      }
    }
    const key = e.button === 0 && !e.shiftKey ? asmCenterAt(...local(e)) : null;
    if (key) { asmCenterDragStart(key); return; }
    if (asm.assigning && e.button === 0 && !e.shiftKey && asmAssignAt(...local(e))) return;
    asm.drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button !== 0 };
  });
  window.addEventListener('mousemove', e => {
    if (!asm.drag) {
      if (asm.profPick && e.target === cv) {          // choosing a wall: highlight the one under the pointer
        const w = asmProfileWallAt(...local(e));
        if (w !== asm.profHover) { asm.profHover = w; asmDrawPreview(); }
      }
      return;
    }
    if (asm.drag.handle) { asmProfileDragTo(...local(e)); return; }
    if (asm.drag.plane) { asmPlaneDragTo(...local(e)); return; }
    if (asm.drag.center) { asmCenterDragTo(...local(e)); return; }
    const dx = e.clientX - asm.drag.x, dy = e.clientY - asm.drag.y;
    asm.drag.x = e.clientX; asm.drag.y = e.clientY;
    if (asm.drag.pan) { asm.view.px += dx; asm.view.py += dy; }
    else {
      if (asm.elevation && (dx || dy)) asm.elevation.orbited = true;
      asm.view.yaw += dx * 0.01;
      asm.view.pitch = Math.max(-0.1, Math.min(Math.PI / 2, asm.view.pitch + dy * 0.01));
    }
    asmDrawPreview();
  });
  window.addEventListener('mouseup', () => {
    if (!asm.drag) return;
    if (asm.drag.handle) { asmProfileDragEnd(); return; }
    if (asm.drag.plane) { asmPlaneDragEnd(); return; }
    if (asm.drag.center) { asmCenterDragEnd(); return; }
    asm.drag = null; asmDrawPreview();
  });
  cv.addEventListener('wheel', e => {
    e.preventDefault();
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left - cv.width / 2 - asm.view.px, my = e.clientY - r.top - cv.height / 2 - asm.view.py;
    const f = Math.exp(-(e.deltaY || 0) * 0.0015);
    const z = Math.max(0.2, Math.min(30, asm.view.zoom * f));
    const k = z / asm.view.zoom;
    asm.view.px -= mx * (k - 1); asm.view.py -= my * (k - 1);   // zoom at the pointer
    asm.view.zoom = z;
    asmDrawPreview();
  }, { passive: false });
  window.addEventListener('resize', () => { if (currentWorkspace === 'assembly') asmDrawPreview(); });
}

// Assembly undo / redo keys (the Designer's handler is inactive here)
document.addEventListener('keydown', e => {
  if (typeof currentWorkspace === 'undefined' || currentWorkspace !== 'assembly') return;
  if (e.key === 'Escape' && asm.profPick) { asmProfileCancelPick(); return; }
  if (e.key === 'Escape' && asm.profSel && !asm.drag) { asmProfileDone(); return; }
  if (!(e.metaKey || e.ctrlKey) || (typeof _isTyping === 'function' && _isTyping())) return;
  const k = (e.key || '').toLowerCase();
  if (k === 'z') { if (e.preventDefault) e.preventDefault(); if (e.shiftKey) asmRedo(); else asmUndo(); }
  else if (k === 'y') { if (e.preventDefault) e.preventDefault(); asmRedo(); }
});

// ---- PRINT PLAN SETTINGS (layer_assembly/print_plan.py, lifts.py) -----------
// Project settings of the print plan, part of the Assembly state (undoable,
// saved with the project): speeds (asm.state.print_speeds, in/s; the unit they
// are shown in is a view choice), lifts (asm.state.machine) and the estimated
// frame reposition time. EDITED and shown in the Print Simulation's Print plan
// column (simulation.js) — the plan and its estimates live there.
const ASM_SPEED_DEFAULTS = { print: 2.0, travel: 6.0 };        // placeholders (in/s) until measured
const ASM_SPEED_UNITS = {            // unit → [to in/s, from in/s]; s/in = time per inch
  'in/s': [v => v, v => v], 'in/min': [v => v / 60, v => v * 60], 'ft/min': [v => v / 5, v => v * 5],
  'mm/s': [v => v / 25.4, v => v * 25.4], 's/in': [v => 1 / v, v => 1 / v],
};
function _asmSpeeds() {
  const s = asm.state.print_speeds || {};
  return { print: s.print || ASM_SPEED_DEFAULTS.print, travel: s.travel || ASM_SPEED_DEFAULTS.travel };
}
function asmSetSpeedUnit(u) {
  if (ASM_SPEED_UNITS[u]) asm.speedUnit = u;
  asmRender();
}
function asmSetSpeed(kind, v) {
  const u = asm.speedUnit || 'in/s', x = parseFloat(v);
  if (!(x > 0) || !isFinite(x)) {
    asm.inputError = `${kind === 'print' ? 'Print' : 'Travel'} speed must be greater than 0.`;
    asmRender(); return;
  }
  asm.inputError = null;
  const ips = ASM_SPEED_UNITS[u][0](x);
  return asmEdit('speed-' + kind, () => {
    asm.state.print_speeds = { ...(asm.state.print_speeds || {}), ..._asmSpeeds(), [kind]: ips };
    asmRender();
  });
}
// MACHINE / LIFTS (layer_assembly/lifts.py): the stack is printed in LIFTS of
// whole layers; after each the XY frame is raised (an explicit, manual event)
const ASM_MACHINE_DEFAULTS = { max_z_travel: 18, lift_height: 18, head_clearance: 1 };
function _asmMachine() { return { ...ASM_MACHINE_DEFAULTS, ...((asm.state && asm.state.machine) || {}) }; }
function asmSetMachine(field, v) {
  const x = parseFloat(v), m = { ..._asmMachine(), [field]: x };
  const label = { max_z_travel: 'Maximum Z travel', lift_height: 'Lift height', head_clearance: 'Head clearance' }[field];
  let err = null;
  if (!isFinite(x) || (field === 'head_clearance' ? x < 0 : x <= 0)) err = `${label} must be ${field === 'head_clearance' ? '≥ 0' : 'greater than 0'}.`;
  else if (field === 'lift_height' && m.lift_height > m.max_z_travel + 1e-9)
    err = `The lift height (${m.lift_height} in) cannot exceed the maximum Z travel (${m.max_z_travel} in).`;
  if (err) { asm.inputError = err; asmRender(); return; }
  asm.inputError = null;
  return asmEdit('machine-' + field, () => {
    const mm = { ...((asm.state.machine) || {}), [field]: x };
    if (field === 'max_z_travel' && _asmMachine().lift_height > x)
      mm.lift_height = x;                         // a smaller machine lowers the lift height with it
    asm.state.machine = mm;
    asmRender();
  });
}
// the ESTIMATED time of one frame reposition (minutes in the UI, seconds stored; 0 = not estimated)
function asmSetFrameTime(min) {
  const x = parseFloat(min);
  if (!(x >= 0) || !isFinite(x)) { asm.inputError = 'Frame reposition time must be ≥ 0.'; asmRender(); return; }
  asm.inputError = null;
  return asmEdit('frame-time', () => {
    asm.state.print_speeds = { ...(asm.state.print_speeds || {}), frame: x * 60 };
    asmRender();
  });
}
function _asmFrameMinutes() { return Math.round((((asm.state.print_speeds || {}).frame) || 0) / 60 * 100) / 100; }
// lift boundaries of the resolved stack, the planner's rule (lifts.lift_bounds): whole layers, greedy
function _asmLiftBounds(layers, H) {
  const out = [];
  let i = 0;
  while (i < layers.length) {
    const base = layers[i].z_bottom;
    let j = i;
    while (j + 1 < layers.length && layers[j + 1].z_top - base <= H + 1e-9) j++;
    out.push([i, j]);
    i = j + 1;
  }
  return out;
}
function _asmFmtTime(sec) {
  const s = Math.round(sec), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : (m ? `${m} min ${String(r).padStart(2, '0')} s` : `${r} s`);
}
// ---- PROJECT FILE (whole-project Save / Open; app.js + project_file.py) -----
// The Assembly's part of the project: its authoring state (asm.state — what
// /api/assembly/* and Assembly.from_dict take, with the UI's id counters) and
// the header id counter. Everything else here is derived (resolved stack,
// geometry, variants, support) or view / interaction state, and is reset.
window.AssemblyProject = {
  exportState: () => ({ state: JSON.parse(JSON.stringify(asm.state)), header_counter: asm.headerCounter }),
  pristine: () => JSON.parse(_ASM_PRISTINE),        // a new project's Assembly
  isPristine: () => JSON.stringify({ state: asm.state, header_counter: asm.headerCounter }) === _ASM_PRISTINE,
  // REPLACE the Assembly with a loaded project's (validated server-side)
  importState(a) {
    asm.state = JSON.parse(JSON.stringify(a.state));
    asm.headerCounter = a.header_counter || 0;
    // older openings: Lens / semicircle / ellipse → the current shapes, then
    // made printable on this project's layers (said on the card, never silent)
    const migrated = [];
    for (const p of asm.state.profiles || []) {
      if (p.kind !== 'opening') continue;
      const before = JSON.stringify(p);
      const was = p.shape === 'lens' ? 'Lens → Diamond (curved)'
        : (p.shape === 'diamond' && p.lower && p.upper && p.lower !== p.upper) ? `Diamond with a ${p.lower === 'curve' ? 'curved' : 'angular'} lower → Diamond (${p.upper === 'curve' ? 'curved' : 'angular'})`
        : (p.shape === 'arch' && !p.source_opening && p.upper_height == null) ? 'Arch → upper height + middle (its total height kept)'
        : (p.shape === 'arch' && ['curve', 'semicircle', 'ellipse'].includes(p.lower)) ? 'Curved bottom → Rounded (same geometry)'
        : null;
      if (window.OpeningShape) window.OpeningShape.migrate(p);
      const n = p.source_opening ? null : _asmConstrain(p, 'load');
      if (p.source_opening && 'closure' in p) {               // a conversion's older explicit closure
        if (p.upper_height == null && p.closure != null) p.upper_height = p.closure;
        delete p.closure;
      }
      if (JSON.stringify(p) !== before && (was || n))
        migrated.push(`P${asm.state.profiles.indexOf(p) + 1}: ${was || ''}${was && n ? '; ' : ''}${n || ''}`);
    }
    asm.hist = { undo: [], redo: [], group: null, t: 0 };       // a new project: no undo into the old one
    asm.open = {};
    asm.resolved = null; asm.geometry = {}; asm.geomKey = null; asm.variants = {};
    asm.resolvedPlanes = null; asm.error = null; asm.inputError = null;
    asm.drag = null; asm.selCenter = null; asm.assigning = false;
    asm.profSel = null; asm.profPick = null; asm.profHover = null; asm.profError = null;
    asm.elevation = null; asm.viewBeforeEdit = null;
    asm.profNote = migrated.length ? { id: null, text: 'Opened an older project: ' + migrated.join(' · ') } : null;
    asm.resolveGen = (asm.resolveGen || 0) + 1;      // a resolve of the previous project is dropped
    asmRender();
    if (typeof currentWorkspace !== 'undefined' && currentWorkspace === 'assembly') return asmResolve();
  },
};

if (window.Designer) {
  _asmInitPreview();
  window.Designer.onWorkspaceShow('assembly', () => asmResolve());
}
