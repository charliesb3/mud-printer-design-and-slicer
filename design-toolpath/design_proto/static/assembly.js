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
           max_overhang: 1.5 }, // in: how far a bead may hang past the one below (min overlap = bead − this)
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
};

// Assembly controls: five columns side by side, no tabs, each control column
// scrolling on its own (2026-10-06):
//   LAYER DESIGNS | LAYERS | ASSEMBLY TRANSFORM | STRUCTURE | 3D preview
//   LAYERS             — what is stacked, in what order, for how much height
//   ASSEMBLY TRANSFORM — the assembly-wide transform, transform groups,
//                        geometry assignment, regenerated connectors
//   STRUCTURE          — physical support (bead width, maximum overhang →
//                        minimum overlap), findings, headers
// The Structure column head shows how many support findings are open.
function _asmStructureHead() {
  const S = (asm.resolved && asm.resolved.support) || {};
  const n = S.findings ? _asmSupportGroups(S).filter(g => g.status === 'needs_header' ||
                                                        g.status === 'insufficient_support').length : 0;
  const col = document.getElementById('asm-col-structure');
  const head = col && col.querySelector && col.querySelector('.sidebar-head');
  if (head) head.textContent = n ? `Structure · ${n} open` : 'Structure';
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
  return asmEdit('lh', () => { asm.state.layer_height = x; return asmResolve(); });
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
  return asmEdit('mh', () => { asm.state.max_overhang = x; return asmResolve(); });
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
async function asmResolve() {
  const ids = window.Designer.listDesigns().map(d => d.id);
  await asmLoadGeometry();          // footprints (bbox of each design's geometry) for SCALE
  const footprints = {};
  for (const [id, g] of Object.entries(asm.geometry)) footprints[id] = _asmBBox(g.polylines || []);
  try {
    const designs = await window.Designer.designsForBackend();
    const T = window.ASM_TIMING ? { t0: performance.now() } : null;   // opt-in instrumentation
    const res = await _asmPost('/api/assembly/resolve',
                               { assembly: asm.state, design_ids: ids, footprints, designs,
                                 ...(T ? { debug_timing: true } : {}) });
    if (T) T.resolve_ms = performance.now() - T.t0;
    // layers with transform groups print a SEMANTIC variant of their design
    // (the Designer moved the walls and re-resolved them): fetch new ones
    // BEFORE showing the result — never a mix of new placements and stale
    // geometry. A layer with no variant prints the canonical design.
    const need = {};
    for (const [k, v] of Object.entries(res.variants || {}))
      if (!asm.variants[k]) need[k] = { design_id: v.design_id, transforms: v.transforms };
    const t1 = T ? performance.now() : 0;
    if (Object.keys(need).length) {
      const r = await _asmPost('/api/assembly/geometry', { designs, variants: need });
      Object.assign(asm.variants, r.designs);
    }
    asm.resolved = res;
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
  return asmEdit(`hd${id}.${field}`, () => {
    const o = asm.state.objects.find(x => x.id === id);
    const x = parseFloat(v);
    if (o && x >= 0) o[field] = x;
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

function _asmBBox(polys) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of polys) for (const [x, y] of p.pts) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  return isFinite(x0) ? [x0, y0, x1, y1] : null;
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

function _asmTfSummary(t) {
  if (!_asmTfActive(t)) return '';
  const parts = [];
  if (t.shift[0] || t.shift[1]) parts.push(`shift ${_sgn(t.shift[0])}, ${_sgn(t.shift[1])} in/layer`);
  if (t.scale_in) parts.push(`scale ${t.scale_in > 0 ? 'in' : 'out'} ${_fmt4(Math.abs(t.scale_in))} in/layer`);
  return parts.join(' · ') + ` (${t.profile})`;
}

function asmRender() {
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
  if (_asmTfActive(asm.state.transform)) ahead.appendChild(_el('span', 'asm-calc', _asmTfSummary(asm.state.transform)));
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
    if (_asmTfActive(t)) tline.appendChild(_el('span', 'asm-calc', _asmTfSummary(t)));
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
    // the threshold, made obvious: what the purple warnings are measured against
    note.innerHTML = ''; if (Array.isArray(note.children)) note.children.length = 0;
    const kv = (k, v) => { const r = _el('div', 'asm-kv-row'); r.appendChild(_el('span', null, k));
                           r.appendChild(_el('b', null, v)); note.appendChild(r); };
    kv('Minimum overlap', `${_fmt(Math.max(0, bw - mh))} in (derived)`);
    note.appendChild(_el('div', 'asm-kv-hint', 'Minimum overlap = bead width − maximum overhang. Less overhang = ' +
                         'stricter. Bead width is the project material (the same value as the Designer\'s ' +
                         'Material / Bead). Controls INSUFFICIENT LAYER SUPPORT (a printability rule, not a transform).'));
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
    if (f.status === 'needs_header') {
      card.appendChild(_el('div', 'asm-support-title', `HEADER NEEDED · unsupported span ${_fmt(f.span)} in`));
      card.appendChild(_el('div', 'asm-calc', where(f) + (f.coverage > 0 ? ` · a header covers only ${Math.round(f.coverage * 100)} %` : '')));
      const row = _el('div', 'asm-row');
      const add = _el('button', 'mini-btn', 'Add Header'); add.onclick = () => asmAddHeader(f.id);
      const ign = _el('button', 'mini-btn', 'Ignore'); ign.title = 'Keep this span unsupported on purpose (recorded as an override)';
      ign.onclick = () => asmIgnoreSpan(f.id);
      row.appendChild(add); row.appendChild(ign); card.appendChild(row);
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
    head.appendChild(_el('span', 'asm-support-title', `Header ${h.id}`));
    const rm = _el('button', 'remove-btn', '×'); rm.title = 'Remove header'; rm.onclick = () => asmRemoveHeader(h.id);
    head.appendChild(rm); card.appendChild(head);
    if (h.error) { card.appendChild(_el('div', 'asm-diff', h.error)); box.appendChild(card); continue; }
    card.appendChild(_el('div', 'asm-calc',
      `span ${_fmt(h.span)} + 2 × ${_fmt(h.bearing)} bearing = length ${_fmt(h.length)} in · ` +
      `top z ${_fmt(h.z_top)} (under layer ${h.layer + 1}), bottom z ${_fmt(h.z_bottom)}`));
    const o = asm.state.objects.find(x => x.id === h.id) || {};
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
    if (!h.snapped) card.appendChild(_el('div', 'asm-diff', 'no unsupported span at this boundary now — kept at its last position'));
    if (h.supports.length === 0 && h.snapped) card.appendChild(_el('div', 'asm-diff', 'does not fully cover the span'));
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

// ---- 3D stack preview (orthographic, canvas 2D) ----------------------------------
const ASM_VIEWS = {
  iso: { yaw: -Math.PI / 4, pitch: 0.55 }, front: { yaw: 0, pitch: 0.0 },
  side: { yaw: -Math.PI / 2, pitch: 0.0 }, top: { yaw: 0, pitch: Math.PI / 2 },
};

function asmViewPreset(name) {
  if (name === 'fit') Object.assign(asm.view, { zoom: 1, px: 0, py: 0 });
  else Object.assign(asm.view, ASM_VIEWS[name], { zoom: 1, px: 0, py: 0 });
  asmDrawPreview();
}

// Every layer instance with its Z and its design's printable centrelines
// (the build_stack() data): what is drawn.
function asmStack() {
  if (!asm.resolved) return [];
  return asm.resolved.instances.map(i => ({
    ...i, transform: i.transform || { scale: 1, tx: 0, ty: 0 }, parts: i.parts || null,
    polylines: ((i.geometry_key && asm.variants[i.geometry_key]) || asm.geometry[i.design_id] || {}).polylines || [] }));
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
    if (!(gk in bb)) bb[gk] = _asmBBox(L.polylines);
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
  // level of detail while orbiting a large stack
  const segs = stack.reduce((a, L) => a + L.polylines.reduce((b, p) => b + p.pts.length, 0), 0);
  const step = asm.drag && segs > 120000 ? Math.ceil(segs / 120000) : 1;
  const starts = new Set((asm.resolved.sections || []).filter(s => s.layers > 0).map(s => s.first_layer));
  const byGroup = asm.state.center_mode === 'multiple' && asm.state.centers.length > 0;
  // build plate outline
  const z0 = 0;
  g.lineWidth = 1;
  for (const L of stack) {
    if (L.index % step && !starts.has(L.index)) continue;
    const col = _asmStrokeColor(L.design_id, null);
    g.strokeStyle = col;
    g.globalAlpha = starts.has(L.index) ? 1.0 : 0.62;   // a section's first layer: subtly marked
    g.lineWidth = starts.has(L.index) ? 1.4 : 0.8;
    for (let j = 0; j < L.polylines.length; j++) {
      const pl = L.polylines[j];
      if (pl.pts.length < 2) continue;
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
  _asmDrawSupport(g, P);
  _asmDrawCenters(g, P, stack);
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
             (asm.busy ? ' · building…' : ''), 14, 18);
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
    const key = e.button === 0 && !e.shiftKey ? asmCenterAt(...local(e)) : null;
    if (key) { asmCenterDragStart(key); return; }
    if (asm.assigning && e.button === 0 && !e.shiftKey && asmAssignAt(...local(e))) return;
    asm.drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button !== 0 };
  });
  window.addEventListener('mousemove', e => {
    if (!asm.drag) return;
    if (asm.drag.center) { asmCenterDragTo(...local(e)); return; }
    const dx = e.clientX - asm.drag.x, dy = e.clientY - asm.drag.y;
    asm.drag.x = e.clientX; asm.drag.y = e.clientY;
    if (asm.drag.pan) { asm.view.px += dx; asm.view.py += dy; }
    else {
      asm.view.yaw += dx * 0.01;
      asm.view.pitch = Math.max(-0.1, Math.min(Math.PI / 2, asm.view.pitch + dy * 0.01));
    }
    asmDrawPreview();
  });
  window.addEventListener('mouseup', () => {
    if (!asm.drag) return;
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
  if (!(e.metaKey || e.ctrlKey) || (typeof _isTyping === 'function' && _isTyping())) return;
  const k = (e.key || '').toLowerCase();
  if (k === 'z') { if (e.preventDefault) e.preventDefault(); if (e.shiftKey) asmRedo(); else asmUndo(); }
  else if (k === 'y') { if (e.preventDefault) e.preventDefault(); asmRedo(); }
});

if (window.Designer) {
  _asmInitPreview();
  window.Designer.onWorkspaceShow('assembly', () => asmResolve());
}
