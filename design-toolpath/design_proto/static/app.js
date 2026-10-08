'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let tool = 'edit';
let drawPts = [];
let selectedId = null;
let _mousePosW = null;       // current mouse world-coords (for snap-to-first preview)

let showToolpath = true;
let showArrows = true;
let showDimensions = false;
let showBeads = false;       // view: physical bead footprint (Material / Bead)
let printable = [];          // resolved PRINTABLE centerlines from the backend

// Playback state
let playbackPos = 0.0;         // 0.0–1.0 fraction of total route distance
let playbackPlaying = false;
let playbackSpeed = 1.0;
let playbackForward = true;
let _playbackAF = null;
let _playbackLastTime = null;
let _routeCumDists = null;     // [{ds, de, m}] cumulative distance per move
let _routeTotalDist = 0.0;
const _PLAYBACK_WORLD_SPEED = 100.0; // world inches per second at 1×

let routeResult = null;
let derivedPaths = [];       // populated by effective_paths or route on model change
let networkInfo = null;      // derived wall-network topology from the backend
let _refreshTimer = null;

// Drag state
let dragging = null;         // { pathId, handleKey, originalState }
let bodyDragging = null;     // { pathId, startWX, startWY, originalState }
let rotateDrag = null;       // { pathId, pivot, a0, orig } — rotation handle

const layer = {
  id: 'design',
  label: '',
  source_paths: [],
  offset_treatments: [],
  lattice_instances: [],   // legacy pairwise lattice (backend only; no UI)
  constraints: {
    start_path_id: null,
    start_t: null,
    reverse_direction: false,
    component_order: null,
  },
  corner_radius: 0,
  cap_style: 'flat',
  cap_corner_radius: 0,
  openings: [],         // path-relative wall openings (see Openings section)
  trims: [],            // suppressed source sections (see Trim section)
  route_origins: [],    // [{ strand, u }] where closed routes begin (see Route origin)
  region_overrides: [], // wall-network region paint (wall/void); no UI yet
  infills: [],          // wall-region infill (lattice of a whole wall region)
  junction_style: 'miter',   // default treatment of network junction corners
  junction_radius: 2,        // radius when junction_style === 'round'
  junction_overrides: [],    // [{ key, treatment, radius }] per junction corner
  network_walls: [],         // [{ id, path_id, thickness, align }] network-level wall
  wall_systems: [],          // [{ id, name, type, thickness, align, params, members, web }] (WALL_SYSTEMS)
  wall_relations: [],        // [{ id, outer_id, inner_id, thickness, driver }] nested-wall links
  // MATERIAL / BEAD (physical deposit; see Bead section)
  material: { bead_width: 3.0, contact_overlap: 0.75, return_overlap: 0.75, physical: true },
  return_paths: true,        // "Infill repair": repair of the wide-region field fallback
  prefer_closed: true,       // prefer a closed (start = end) layer route
};

let infillPatterns = {};     // name → { parameters } from /api/infill_patterns
let selectedJunction = null; // { key, x, y } of the selected network junction

// ---------------------------------------------------------------------------
// Canvas setup
// ---------------------------------------------------------------------------

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');

function resizeCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  const size = Math.min(wrap.clientWidth - 32, wrap.clientHeight - 32, 800);
  canvas.width = size;
  canvas.height = size;
  repaint();
}
window.addEventListener('resize', resizeCanvas);

// ---------------------------------------------------------------------------
// Coordinate transform: world ↔ canvas
// 1 world unit = 1 inch. Canvas represents a 400 in × 400 in workspace.
// ---------------------------------------------------------------------------

const WORLD = 400;

// VIEW (zoom / pan) — part of the world ↔ canvas transform, so every hit
// test, drag and snap works at any zoom. View only: never geometry.
// zoom 1, offset 0 = "100 %" (the whole 400 in workspace fits the canvas).
const view = { z: 1, px: 0, py: 0 };
const VIEW_MIN = 0.25, VIEW_MAX = 24;

function _viewScale() { return canvas.width / WORLD * view.z; }     // px per inch

function worldToCanvas(x, y) {
  const s = _viewScale();
  return [x * s + view.px, (WORLD - y) * s + view.py];
}

function canvasToWorld(cx, cy) {
  const s = _viewScale();
  return [(cx - view.px) / s, WORLD - (cy - view.py) / s];
}

// world-unit tolerances follow the zoom (constant on screen)
function _applyViewTolerances() {
  HIT_DIST = 10 / view.z;
  SNAP_RADIUS = 12 / view.z;
}

function setView(z, px, py) {
  view.z = Math.max(VIEW_MIN, Math.min(VIEW_MAX, z));
  view.px = px; view.py = py;
  _applyViewTolerances();
  const el = document.getElementById('zoom-readout');
  if (el) el.textContent = Math.round(view.z * 100) + '%';
  repaint();
}

// zoom by `factor` keeping the world point under canvas point (cx, cy) fixed
function zoomAt(cx, cy, factor) {
  const [wx, wy] = canvasToWorld(cx, cy);
  const z = Math.max(VIEW_MIN, Math.min(VIEW_MAX, view.z * factor));
  const s = canvas.width / WORLD * z;
  setView(z, cx - wx * s, cy - (WORLD - wy) * s);
}

function panBy(dx, dy) { setView(view.z, view.px + dx, view.py + dy); }

function viewReset() { setView(1, 0, 0); }        // "100 %"

// Fit: frame the current print / design geometry
function viewFit() {
  const pts = [];
  for (const c of printable || []) for (const q of c.pts || []) pts.push(q);
  if (!pts.length) for (const p of layer.source_paths) for (const q of p.points || []) pts.push(q);
  for (const d of derivedPaths || []) for (const q of d.points || []) pts.push(q);
  if (!pts.length) { viewReset(); return; }
  const xs = pts.map(q => q[0]), ys = pts.map(q => q[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const s0 = canvas.width / WORLD;
  const w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
  const z = Math.max(VIEW_MIN, Math.min(VIEW_MAX, 0.85 * Math.min(canvas.width / (w * s0), canvas.height / (h * s0))));
  const s = s0 * z, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  setView(z, canvas.width / 2 - cx * s, canvas.height / 2 - (WORLD - cy) * s);
}

// ---------------------------------------------------------------------------
// Semantic handle system
// ---------------------------------------------------------------------------

function getHandles(path) {
  if (_relationOf(path.id) && _relationOf(path.id).dep === path.id) return [];   // driven boundary
  switch (path.type) {
    case 'CirclePath':
      return [
        { key: 'center', wx: path.cx, wy: path.cy, shape: 'cross' },
        { key: 'radius', wx: path.cx + path.radius, wy: path.cy, shape: 'square' },
      ];
    case 'EllipsePath': {
      const c = Math.cos(path.rotation || 0), s = Math.sin(path.rotation || 0);
      return [
        { key: 'center', wx: path.cx, wy: path.cy, shape: 'cross' },
        { key: 'rx', wx: path.cx + path.rx * c, wy: path.cy + path.rx * s, shape: 'square' },
        { key: 'ry', wx: path.cx - path.ry * s, wy: path.cy + path.ry * c, shape: 'square' },
      ];
    }
    case 'LinePath':
      return [
        { key: 'start', wx: path.start[0], wy: path.start[1], shape: 'point' },
        { key: 'end',   wx: path.end[0],   wy: path.end[1],   shape: 'point' },
      ];
    case 'QuadBezierPath':
      return [
        { key: 'start',   wx: path.start[0],   wy: path.start[1],   shape: 'point' },
        { key: 'end',     wx: path.end[0],     wy: path.end[1],     shape: 'point' },
        { key: 'control', wx: path.control[0], wy: path.control[1], shape: 'square' },
      ];
    case 'RectanglePath':
      return _rectCorners(path).map((q, i) => (
        { key: ['tl', 'tr', 'br', 'bl'][i], wx: q[0], wy: q[1], shape: 'square' }));
    case 'InsetPath':
      return [];              // derived from its parent: no handles of its own
    default: {
      // ExplicitPath — only user-placed control points
      const pts = path.control_points || path.points || [];
      return pts.map((pt, i) => ({ key: `pt${i}`, wx: pt[0], wy: pt[1], shape: 'point' }));
    }
  }
}

function applyHandleDrag(path, handleKey, wx, wy, orig) {
  switch (path.type) {
    case 'CirclePath':
      if (handleKey === 'center') { path.cx = wx; path.cy = wy; }
      if (handleKey === 'radius') {
        path.radius = Math.max(1, Math.hypot(wx - path.cx, wy - path.cy));
      }
      break;
    case 'EllipsePath':
      if (handleKey === 'center') { path.cx = wx; path.cy = wy; }
      {
        const c = Math.cos(path.rotation || 0), s = Math.sin(path.rotation || 0);
        const dx = wx - path.cx, dy = wy - path.cy;
        if (handleKey === 'rx') { path.rx = Math.max(1, Math.abs(dx * c + dy * s)); }
        if (handleKey === 'ry') { path.ry = Math.max(1, Math.abs(-dx * s + dy * c)); }
      }
      break;
    case 'LinePath':
      if (handleKey === 'start') { path.start = [wx, wy]; }
      if (handleKey === 'end')   { path.end   = [wx, wy]; }
      break;
    case 'QuadBezierPath':
      if (handleKey === 'start')   { path.start   = [wx, wy]; }
      if (handleKey === 'end')     { path.end     = [wx, wy]; }
      if (handleKey === 'control') { path.control = [wx, wy]; }
      break;
    case 'RectanglePath': {
      // A rotated rectangle is resized in its own frame; the opposite
      // corner stays put in the world.
      const th = orig.rotation || 0;
      if (th) {
        const oc = [orig.x + orig.w / 2, orig.y + orig.h / 2];
        const [lx, ly] = _rotMap(oc, -th)([wx, wy]);
        _rectAxisDrag(path, handleKey, lx, ly, orig);
        const nc = _rotMap(oc, th)([path.x + path.w / 2, path.y + path.h / 2]);
        path.x = nc[0] - path.w / 2; path.y = nc[1] - path.h / 2;
      } else {
        _rectAxisDrag(path, handleKey, wx, wy, orig);
      }
      break;
    }
    default: {
      const idx = parseInt(handleKey.replace('pt', ''), 10);
      if (!isNaN(idx)) {
        const pts = path.control_points || path.points;
        pts[idx] = [wx, wy];
        path.control_points = pts;
        path.points = pts;
      }
      break;
    }
  }
  _computePrimitivePoints(path);
  _refreshInsetChildren(path.id);
}

function _rectAxisDrag(path, handleKey, wx, wy, orig) {
  {
      const fixBRX = orig.x + orig.w, fixBRY = orig.y + orig.h;
      if (handleKey === 'tl') {
        path.x = Math.min(wx, fixBRX - 1);
        path.y = Math.min(wy, fixBRY - 1);
        path.w = fixBRX - path.x;
        path.h = fixBRY - path.y;
      } else if (handleKey === 'tr') {
        path.y = Math.min(wy, orig.y + orig.h - 1);
        path.w = Math.max(1, wx - orig.x);
        path.h = (orig.y + orig.h) - path.y;
      } else if (handleKey === 'br') {
        path.w = Math.max(1, wx - orig.x);
        path.h = Math.max(1, wy - orig.y);
      } else if (handleKey === 'bl') {
        const fixTRX = orig.x + orig.w;
        path.x = Math.min(wx, fixTRX - 1);
        path.w = fixTRX - path.x;
        path.h = Math.max(1, wy - orig.y);
      }
  }
}

function capturePathState(path) {
  const s = {
    type: path.type,
    cx: path.cx, cy: path.cy, radius: path.radius,
    rx: path.rx, ry: path.ry, rotation: path.rotation,
    x: path.x, y: path.y, w: path.w, h: path.h,
  };
  if (path.start)   s.start   = [...path.start];
  if (path.end)     s.end     = [...path.end];
  if (path.control) s.control = [...path.control];
  if (path.control_points) s.control_points = path.control_points.map(p => [...p]);
  if (path.points) s.points = path.points.map(p => [...p]);
  return s;
}

function applyBodyDrag(path, dx, dy, orig) {
  switch (path.type) {
    case 'CirclePath':
      path.cx = orig.cx + dx; path.cy = orig.cy + dy; break;
    case 'EllipsePath':
      path.cx = orig.cx + dx; path.cy = orig.cy + dy; break;
    case 'LinePath':
      path.start = [orig.start[0] + dx, orig.start[1] + dy];
      path.end   = [orig.end[0]   + dx, orig.end[1]   + dy];
      break;
    case 'QuadBezierPath':
      path.start   = [orig.start[0]   + dx, orig.start[1]   + dy];
      path.end     = [orig.end[0]     + dx, orig.end[1]     + dy];
      path.control = [orig.control[0] + dx, orig.control[1] + dy];
      break;
    case 'RectanglePath':
      path.x = orig.x + dx; path.y = orig.y + dy; break;
    default: {
      const pts = orig.control_points || orig.points || [];
      path.control_points = pts.map(([px, py]) => [px + dx, py + dy]);
      path.points = path.control_points;
      break;
    }
  }
  _computePrimitivePoints(path);
  _refreshInsetChildren(path.id);
}

function syncPropPanel(path) {
  if (!path || path.id !== selectedId) return;
  const fields = {};
  switch (path.type) {
    case 'CirclePath':
      Object.assign(fields, { cx: path.cx, cy: path.cy, radius: path.radius }); break;
    case 'EllipsePath':
      Object.assign(fields, { cx: path.cx, cy: path.cy, rx: path.rx, ry: path.ry }); break;
    case 'LinePath':
      Object.assign(fields, { x0: path.start[0], y0: path.start[1],
                               x1: path.end[0],   y1: path.end[1] }); break;
    case 'QuadBezierPath':
      Object.assign(fields, { x0: path.start[0],   y0: path.start[1],
                               x1: path.end[0],     y1: path.end[1],
                               bx: path.control[0], by: path.control[1] }); break;
    case 'RectanglePath':
      Object.assign(fields, { x: path.x, y: path.y, w: path.w, h: path.h }); break;
  }
  for (const [key, val] of Object.entries(fields)) {
    const el = document.getElementById('prop-' + key);
    if (el && document.activeElement !== el) {
      el.value = typeof val === 'number' ? +val.toFixed(2) : val;
    }
  }
}

// ---------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------

const ROLE_COLORS = {
  outer:   '#4a9eff',
  inner:   '#44ccaa',
  lattice: '#aa88ff',
  cap:     '#44ccaa',
  free:    '#aaaaaa',
};

const MOVE_COLORS = {
  print:   '#4a9eff',
  travel:  '#ff6644',
  retrace: '#ffaa44',
};

// ---------------------------------------------------------------------------
// Drawing helpers
// ---------------------------------------------------------------------------

function drawLine(x0, y0, x1, y1, color, width, dashed) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dashed ? [6, 4] : []);
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawPolyline(pts, color, width, dashed, closed) {
  if (pts.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dashed ? [5, 4] : []);
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  if (closed) ctx.closePath();
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawArrowhead(cx, cy, angle, size, fillColor, strokeColor) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(size, 0);
  ctx.lineTo(-size * 0.6, size * 0.5);
  ctx.lineTo(-size * 0.6, -size * 0.5);
  ctx.closePath();
  if (strokeColor) {
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  ctx.fillStyle = fillColor;
  ctx.fill();
  ctx.restore();
}

function drawDot(x, y, r, color) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function drawHandle(wx, wy, shape) {
  const [cx, cy] = worldToCanvas(wx, wy);
  ctx.save();
  if (shape === 'cross') {
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(cx - 7, cy); ctx.lineTo(cx + 7, cy);
    ctx.moveTo(cx, cy - 7); ctx.lineTo(cx, cy + 7);
    ctx.stroke();
    ctx.strokeStyle = '#4a9eff';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.stroke();
  } else if (shape === 'square') {
    ctx.strokeStyle = '#4a9eff';
    ctx.fillStyle = '#1c2a3a';
    ctx.lineWidth = 1.5;
    ctx.fillRect(cx - 4, cy - 4, 8, 8);
    ctx.strokeRect(cx - 4, cy - 4, 8, 8);
  } else {
    // 'point'
    ctx.fillStyle = '#4a9eff';
    ctx.beginPath();
    ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// Repaint
// ---------------------------------------------------------------------------

function repaint() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawGrid();
  drawBeads();
  if (!showBeads && !(showToolpath && routeResult)) drawPrintableLines();
  drawEffectivePaths();
  drawOpenings();
  drawJunctions();
  drawTrimHover();
  if (showToolpath && routeResult) {
    if (playbackPos > 0.0 || playbackPlaying) {
      drawToolpathWithPlayback(routeResult.moves);
    } else {
      drawToolpath(routeResult.moves);
    }
  }
  if (showDimensions) drawDimensions();
  if (tool === 'draw' && drawPts.length > 0) drawInProgress();
  if (tool === 'curve') drawCurveInProgress();
  if (SHAPE_TOOLS[tool]) drawShapeInProgress();
  if (selectedId) drawHandles(selectedId);
  drawHighlight();
  drawSnapHint();
}

function drawGrid() {
  const step = 40;
  ctx.save();
  ctx.strokeStyle = '#252525';
  ctx.lineWidth = 1;
  for (let wx = 0; wx <= WORLD; wx += step) {
    const [cx] = worldToCanvas(wx, 0);
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, canvas.height);
    ctx.stroke();
  }
  for (let wy = 0; wy <= WORLD; wy += step) {
    const [, cy] = worldToCanvas(0, wy);
    ctx.beginPath();
    ctx.moveTo(0, cy);
    ctx.lineTo(canvas.width, cy);
    ctx.stroke();
  }
  ctx.restore();
}

function _pathCanvasPts(path) {
  const pts = path.points || [];
  return pts.map(([wx, wy]) => worldToCanvas(wx, wy));
}

function _draggedPathId() {
  return dragging ? dragging.pathId : (bodyDragging ? bodyDragging.pathId : null);
}

// Sources the wall network has trimmed (e.g. the host face across a T
// mouth). They are drawn from the backend's resolved pieces, except while
// being dragged (the pieces are stale until the next refresh).
function _networkTrimmed() {
  return new Set((networkInfo && networkInfo.modified_sources) || []);
}

// Selected wall: its DERIVED face(s) (Wall Thickness offsets) are traced
// in a dashed accent with the thickness — the relationship is visible only
// while the driving boundary is selected.
function _drawDerivedWallFaces() {
  const sel = selectedId && layer.source_paths.find(p => p.id === selectedId);
  const w = sel && sel.wall && sel.wall.thickness > 0 ? sel.wall : null;
  if (!w) return;
  const faces = derivedPaths.filter(d => d.source_id === sel.id && String(d.id).startsWith(sel.id + '.wall'));
  for (const f of faces) {
    const cp = _pathCanvasPts(f);
    if (cp.length < 2) continue;
    drawPolyline(cp, '#7fd4ff', 1.2, true, f.closed);
  }
  const f0 = faces.find(f => (f.points || []).length >= 2);
  if (f0) {
    const [x, y] = worldToCanvas(f0.points[0][0], f0.points[0][1]);
    ctx.save();
    ctx.fillStyle = '#7fd4ff'; ctx.font = '11px sans-serif';
    ctx.fillText(`derived face · ${w.thickness} in ${_alignLabel(w.align, sel.closed).toLowerCase()}`, x + 6, y - 6);
    ctx.restore();
  }
}

function drawEffectivePaths() {
  const trimmed = _networkTrimmed();
  const dragged = _draggedPathId();
  const refOnly = new Set((networkInfo && networkInfo.reference_only) || []);
  // PRINTABLE VIEW (whenever the resolved printable centrelines are known):
  // printed geometry is drawn ONLY as itself — Beads OFF: one thin blue
  // line (drawPrintableLines / the toolpath print moves); Beads ON: one
  // thick blue bead (drawBeads). Nothing else is layered on it — only the
  // SELECTED path (thin highlight / its reference line) and a path being
  // dragged (live) are drawn from the design here.
  const beadView = printable.length > 0;
  for (const p of layer.source_paths) {
    if (!p.visible) continue;
    if (beadView && p.id !== dragged && p.id !== selectedId) continue;
    if (beadView && p.id === selectedId && p.id !== dragged && !refOnly.has(p.id)) {
      for (const v of _visibleSourcePolys(p))                              // selection
        drawPolyline(v.pts.map(([x, y]) => worldToCanvas(x, y)), '#ffffff', 1.2, false, v.closed);
      continue;
    }
    if (refOnly.has(p.id) && p.id !== dragged) {
      // a centred wall's reference line: construction geometry, not printed
      // (a trimmed one: only its remaining sections)
      const isSel = p.id === selectedId;
      const secs = ((networkInfo && networkInfo.trim_sections) || []).filter(q => q.source === p.id);
      if (secs.some(q => q.trimmed_by)) {
        for (const q of secs.filter(q => !q.trimmed_by))
          drawPolyline(q.pts.map(([x, y]) => worldToCanvas(x, y)), isSel ? '#ffffff' : '#777', isSel ? 1.6 : 1, true, false);
        continue;
      }
      drawPolyline(_pathCanvasPts(p), isSel ? '#ffffff' : '#777', isSel ? 1.6 : 1, true, p.closed);
      continue;
    }
    if (trimmed.has(p.id) && p.id !== dragged) continue;   // pieces below
    const isSel = p.id === selectedId;
    const color = isSel ? '#ffffff' : (ROLE_COLORS[p.role] || '#aaa');
    const width = isSel ? 2.5 : 1.8;

    // A wall with openings is drawn as its surviving pieces (same arc-length
    // cut as the backend); the full path still drives selection/hit-testing.
    const pieces = _survivingPieces(p);
    if (pieces) {
      for (const piece of pieces) {
        const cp = piece.map(([x, y]) => worldToCanvas(x, y));
        drawPolyline(cp, color, width, false, false);
      }
      continue;
    }

    if (p.type === 'QuadBezierPath') {
      // Render as a true bezier curve for smooth appearance
      const [x0, y0] = worldToCanvas(p.start[0], p.start[1]);
      const [xc, yc] = worldToCanvas(p.control[0], p.control[1]);
      const [x2, y2] = worldToCanvas(p.end[0], p.end[1]);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.quadraticCurveTo(xc, yc, x2, y2);
      ctx.stroke();
      ctx.restore();
      drawDot(x0, y0, 3, '#666');
      drawDot(x2, y2, 3, '#666');
    } else {
      const pts = _pathCanvasPts(p);
      drawPolyline(pts, color, width, false, p.closed);
      if (!p.closed && pts.length >= 2) {
        drawDot(pts[0][0], pts[0][1], 3, '#666');
        drawDot(pts[pts.length - 1][0], pts[pts.length - 1][1], 3, '#666');
      }
    }
  }

  _drawDerivedWallFaces();
  if (beadView) return;
  for (const p of derivedPaths) {
    const srcPiece = p.treatment_id === 'opening_cut' || p.treatment_id === 'network_src';
    if (srcPiece) {
      // Source pieces: openings are drawn above from the live JS cut unless
      // the network trimmed this source; then the backend pieces are used.
      if (!trimmed.has(p.source_id) || p.source_id === dragged) continue;
      const src = layer.source_paths.find(s => s.id === p.source_id);
      if (!src || !src.visible) continue;
      const isSel = src.id === selectedId;
      drawPolyline(_pathCanvasPts(p), isSel ? '#ffffff' : (ROLE_COLORS[src.role] || '#aaa'),
                   isSel ? 2.5 : 1.8, false, p.closed);
      continue;
    }
    const pts = _pathCanvasPts(p);
    if (pts.length < 2) continue;
    if (p.treatment_id === 'infill_return') {  // corrective strand (wide-region repair)
      drawPolyline(pts, RETURN_COLOR, 1.5, false, false);
      continue;
    }
    const color = ROLE_COLORS[p.role] || '#aa88ff';
    drawPolyline(pts, color, 1.5, p.role === 'lattice', p.closed);
  }
}

const RETURN_COLOR = '#ff6fa8';

// Junctions of the derived wall network: where walls of different systems
// meet (T, X, end-to-end, spliced faces) — the visual proof that geometry
// has become one connected wall network.
const JUNCTION_COLOR = '#33dd88';

function drawJunctions() {
  if (!networkInfo || !(networkInfo.junctions || []).length) return;
  if (_draggedPathId()) return;                       // stale while dragging
  ctx.save();
  for (const jn of networkInfo.junctions) {
    const [cx, cy] = worldToCanvas(jn.x, jn.y);
    const sel = _isSelectedJunction(jn);
    const r = sel ? 6 : (jn.corner ? 4 : 3);
    ctx.beginPath();
    ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy);
    ctx.closePath();
    // filled = a wall-face corner (treatable); hollow = internal junction
    ctx.fillStyle = sel ? '#ffffff' : (jn.corner ? 'rgba(20,40,30,0.9)' : 'rgba(0,0,0,0)');
    ctx.fill();
    ctx.strokeStyle = jn.treatment === 'round' ? '#88ddff' : JUNCTION_COLOR;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// Junction selection — corners CREATED by the wall network. Their
// treatment (Miter / Rounded + radius) is independent of every source
// path's own Corner R. Identity = the wall faces that meet (key), so a
// setting follows the junction while walls are moved or reshaped.
// ---------------------------------------------------------------------------

function _isSelectedJunction(jn) {
  if (!selectedJunction) return false;
  if (selectedJunction.key) return jn.key === selectedJunction.key;
  return Math.hypot(jn.x - selectedJunction.x, jn.y - selectedJunction.y) < 1e-6;
}

function hitTestJunction(wx, wy) {
  if (!networkInfo) return null;
  let best = null;
  for (const jn of networkInfo.junctions || []) {
    const d = Math.hypot(wx - jn.x, wy - jn.y);
    if (d < HIT_DIST * 0.8 && (!best || d < best.d)) best = { jn, d };
  }
  return best ? best.jn : null;
}

function selectJunction(jn) {
  selectedJunction = jn ? { key: jn.key, x: jn.x, y: jn.y } : null;
  selectedId = null;
  selectedOpeningId = null;
  updatePathList();
  updatePropPanel();
  updateHint();
  repaint();
}

function _currentJunction() {
  if (!selectedJunction || !networkInfo) return null;
  return (networkInfo.junctions || []).find(_isSelectedJunction) || null;
}

function _faceName(fid) {
  if (fid.startsWith('junction:')) return 'junction arc';
  const wm = fid.match(/^(.*)\.wall([+-]?)$/);
  if (wm) {
    const s = layer.source_paths.find(x => x.id === wm[1]);
    const side = wm[2] === '+' ? 'left ' : wm[2] === '-' ? 'right ' : '';
    return `${side}wall face of ${s ? (s.label || s.id) : '?'}`;
  }
  const p = layer.source_paths.find(s => s.id === fid);
  if (p) return p.label || p.id;
  const ot = layer.offset_treatments.find(o => o.id === fid);
  if (ot) {
    const s = layer.source_paths.find(x => x.id === ot.source_path_id);
    return `${ot.direction || 'inside'} offset of ${s ? (s.label || s.id) : '?'}`;
  }
  const m = fid.match(/^(.*)_c[se]$/);
  if (m) {
    const s = layer.source_paths.find(x => x.id === m[1].split('~')[0]);
    return `end cap of ${s ? (s.label || s.id) : '?'}`;
  }
  return fid;
}

function updateJunctionPropPanel(panel) {
  const jn = _currentJunction();
  const title = document.createElement('div');
  title.className = 'path-type';
  title.style.cssText = 'color:' + JUNCTION_COLOR + ';margin-bottom:2px';
  title.textContent = 'Junction';
  panel.appendChild(title);
  const info = document.createElement('div');
  info.className = 'path-type';
  if (!jn) {
    info.textContent = 'This junction no longer exists in the current geometry.';
    panel.appendChild(info);
    return;
  }
  if (!jn.corner) {
    info.textContent = 'Internal junction (walls meet inside the wall) — no wall-face corner to treat.';
    panel.appendChild(info);
    return;
  }
  const faces = jn.key.split('#')[0].split('|').map(_faceName);
  info.textContent = `Corner where ${faces.join(' meets ')}`;
  panel.appendChild(info);
  const fmt = v => (+v).toFixed(1).replace(/\.0$/, '');
  if (jn.derived) {
    // the inner face of a wall turn: concentric with its outer corner
    const n = document.createElement('div');
    n.className = 'path-type'; n.id = 'jn-actual';
    n.textContent = `Inner face of a wall turn — follows its outer corner (concentric: outer R − wall thickness). ` +
      `Radius here: ${jn.actual_radius > 0 ? fmt(jn.actual_radius) + ' in' : 'sharp'}` +
      (jn.limited ? ' (geometry limit).' : '.') + ' Set the radius on the outer corner.';
    if (jn.limited) n.style.color = 'var(--warn)';
    panel.appendChild(n);
    return;
  }
  const ov = (layer.junction_overrides || []).find(o => o.key === jn.key);
  const dflt = `Default (${layer.junction_style === 'round' ? 'Rounded ' + (layer.junction_radius || 0) + ' in' : 'Miter'})`;
  const cur = ov ? (ov.treatment === 'round' ? 'Rounded' : 'Miter') : dflt;
  addPropRowSelect(panel, 'Treatment', cur, [dflt, 'Miter', 'Rounded'], v => {
    layer.junction_overrides = (layer.junction_overrides || []).filter(o => o.key !== jn.key);
    if (v !== dflt) {
      layer.junction_overrides.push({ key: jn.key, treatment: v === 'Rounded' ? 'round' : 'miter',
                                      radius: ov ? ov.radius : (layer.junction_radius || 2) });
    }
    routeResult = null; scheduleRefresh(); updatePropPanel(); repaint();
  });
  if (ov && ov.treatment === 'round') {
    addPropRowNum(panel, 'Radius', 'jn-radius', ov.radius, v => {
      ov.radius = Math.max(0, v); routeResult = null; scheduleRefresh(); repaint();
    });
  }
  if (jn.limited && jn.actual_radius != null) {
    const req = ov ? ov.radius : (layer.junction_radius || 0);
    const n = document.createElement('div');
    n.className = 'path-type'; n.id = 'jn-actual';
    n.style.color = 'var(--warn)';
    n.textContent = `Requested ${fmt(req)} in · Actual ${fmt(jn.actual_radius)} in (geometry limit)`;
    panel.appendChild(n);
  }
  const hint = document.createElement('div');
  hint.className = 'path-type';
  hint.textContent = 'Independent of the walls\' own Corner R.';
  panel.appendChild(hint);
}

// ---------------------------------------------------------------------------
// Toolpath drawing — runs / sparse arrows / seam markers
// ---------------------------------------------------------------------------

function buildPrintRuns(moves) {
  const runs = [];
  let current = null;
  for (const m of moves) {
    if (m.kind === 'print' || m.kind === 'retrace') {
      if (!current) current = { moves: [], startPos: m.start };
      current.moves.push(m);
    } else {
      if (current) {
        current.endPos = current.moves[current.moves.length - 1].end;
        runs.push(current);
        current = null;
      }
    }
  }
  if (current) {
    current.endPos = current.moves[current.moves.length - 1].end;
    runs.push(current);
  }
  return runs;
}

function drawToolpath(moves) {
  if (!moves || moves.length === 0) return;

  // Pass 1: draw all move lines. With Beads ON the printed lines ARE the
  // bead (drawn by drawBeads); only travel is drawn here.
  for (const m of moves) {
    if (showBeads && m.kind !== 'travel') continue;
    const [x0, y0] = worldToCanvas(m.start[0], m.start[1]);
    const [x1, y1] = worldToCanvas(m.end[0], m.end[1]);
    const color = MOVE_COLORS[m.kind] || MOVE_COLORS.print;
    const dashed = m.kind === 'travel';
    const width = m.kind === 'travel' ? 1.2 : (m.kind === 'retrace' ? 1.5 : 2.0);
    drawLine(x0, y0, x1, y1, color, width, dashed);
  }

  // Pass 2: print run overlays (numbers, sparse arrows, seam markers)
  const ARROW_SPACING = 50; // world inches between arrows
  const runs = buildPrintRuns(moves);

  runs.forEach((run) => {
    // Sparse direction arrows along print run
    if (showArrows) {
      let distSinceArrow = ARROW_SPACING; // start eager to place first arrow early
      for (const m of run.moves) {
        if (m.kind !== 'print') continue;
        const segLen = Math.hypot(m.end[0] - m.start[0], m.end[1] - m.start[1]);
        distSinceArrow += segLen;
        if (distSinceArrow >= ARROW_SPACING) {
          const [x0, y0] = worldToCanvas(m.start[0], m.start[1]);
          const [x1, y1] = worldToCanvas(m.end[0], m.end[1]);
          drawArrowhead((x0 + x1) / 2, (y0 + y1) / 2,
                        Math.atan2(y1 - y0, x1 - x0), 7, '#ffffff', 'rgba(0,0,0,0.65)');
          distSinceArrow = 0;
        }
      }
    }

  });
  _drawRouteMarkers(moves, true);
}

// ---------------------------------------------------------------------------
// Playback — distance-based interpolation and toolpath draw with nozzle
// ---------------------------------------------------------------------------

function _drawMoveLine(m) {
  const [x0, y0] = worldToCanvas(m.start[0], m.start[1]);
  const [x1, y1] = worldToCanvas(m.end[0], m.end[1]);
  const color = MOVE_COLORS[m.kind] || MOVE_COLORS.print;
  const dashed = m.kind === 'travel';
  const width = m.kind === 'travel' ? 1.2 : (m.kind === 'retrace' ? 1.5 : 2.0);
  drawLine(x0, y0, x1, y1, color, width, dashed);
}

function drawToolpathWithPlayback(moves) {
  if (!_routeCumDists || _routeTotalDist < 1e-6) { drawToolpath(moves); return; }
  const targetDist = playbackPos * _routeTotalDist;

  // Pass 1: all moves dim (future / unprinted); with Beads ON the beads
  // show the printed geometry, the nozzle shows the progress
  ctx.save();
  ctx.globalAlpha = 0.18;
  for (const m of moves) if (!showBeads || m.kind === 'travel') _drawMoveLine(m);
  ctx.restore();

  // Pass 2: printed portion at full opacity
  for (const { ds, de, m } of _routeCumDists) {
    if (ds >= targetDist) break;
    if (showBeads && m.kind !== 'travel') continue;
    if (de <= targetDist) {
      _drawMoveLine(m);
    } else {
      const t = (targetDist - ds) / Math.max(1e-12, de - ds);
      const ex = m.start[0] + t * (m.end[0] - m.start[0]);
      const ey = m.start[1] + t * (m.end[1] - m.start[1]);
      const [x0, y0] = worldToCanvas(m.start[0], m.start[1]);
      const [x1, y1] = worldToCanvas(ex, ey);
      const color = MOVE_COLORS[m.kind] || MOVE_COLORS.print;
      drawLine(x0, y0, x1, y1, color,
               m.kind === 'travel' ? 1.2 : (m.kind === 'retrace' ? 1.5 : 2.0),
               m.kind === 'travel');
    }
  }

  // Arrows + seam markers when route fully played
  if (playbackPos >= 1.0) {
    const ARROW_SPACING = 50;
    buildPrintRuns(moves).forEach(run => {
      if (showArrows) {
        let distSinceArrow = ARROW_SPACING;
        for (const m of run.moves) {
          if (m.kind !== 'print') continue;
          distSinceArrow += Math.hypot(m.end[0]-m.start[0], m.end[1]-m.start[1]);
          if (distSinceArrow >= ARROW_SPACING) {
            const [x0,y0] = worldToCanvas(m.start[0], m.start[1]);
            const [x1,y1] = worldToCanvas(m.end[0], m.end[1]);
            drawArrowhead((x0+x1)/2, (y0+y1)/2, Math.atan2(y1-y0, x1-x0),
                          7, '#ffffff', 'rgba(0,0,0,0.65)');
            distSinceArrow = 0;
          }
        }
      }
    });
  }
  _drawRouteMarkers(moves, false);

  // Nozzle drawn last — on top of everything
  const npos = _nozzleAtPos(playbackPos);
  if (npos) {
    const [ncx, ncy] = worldToCanvas(npos[0], npos[1]);
    _drawNozzle(ncx, ncy);
  }
}

function _nozzleAtPos(p) {
  if (!_routeCumDists || _routeTotalDist < 1e-6) return null;
  const target = p * _routeTotalDist;
  for (const { ds, de, m } of _routeCumDists) {
    if (de >= target - 1e-6) {
      const segLen = de - ds;
      const t = segLen > 1e-9 ? Math.max(0, Math.min(1, (target - ds) / segLen)) : 0;
      return [m.start[0] + t * (m.end[0] - m.start[0]),
              m.start[1] + t * (m.end[1] - m.start[1])];
    }
  }
  const last = _routeCumDists[_routeCumDists.length - 1];
  return last ? [last.m.end[0], last.m.end[1]] : null;
}

function _drawNozzle(cx, cy) {
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, 8, 0, Math.PI * 2);
  ctx.strokeStyle = '#ffff00';
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(cx, cy, 3, 0, Math.PI * 2);
  ctx.fillStyle = '#ffff00';
  ctx.fill();
  ctx.restore();
}

function _setupPlayback(moves) {
  if (_playbackAF) { cancelAnimationFrame(_playbackAF); _playbackAF = null; }
  playbackPlaying = false;
  playbackPos = 0.0;
  let cum = 0;
  _routeCumDists = (moves || []).map(m => {
    const len = Math.hypot(m.end[0] - m.start[0], m.end[1] - m.start[1]);
    const entry = { ds: cum, de: cum + len, m };
    cum += len;
    return entry;
  });
  _routeTotalDist = cum;
  _syncScrubber();
  _updatePlayBtn();
}

function playbackToggle() {
  if (!routeResult || !routeResult.moves || routeResult.moves.length === 0) return;
  playbackPlaying = !playbackPlaying;
  if (playbackPlaying) {
    if (playbackForward && playbackPos >= 1.0) playbackPos = 0.0;
    if (!playbackForward && playbackPos <= 0.0) playbackPos = 1.0;
    _playbackLastTime = null;
    _playbackAF = requestAnimationFrame(_playbackStep);
  } else {
    if (_playbackAF) { cancelAnimationFrame(_playbackAF); _playbackAF = null; }
  }
  _updatePlayBtn();
}

function playbackRestart() {
  if (_playbackAF) { cancelAnimationFrame(_playbackAF); _playbackAF = null; }
  playbackPlaying = false;
  playbackPos = 0.0;
  playbackForward = true;
  const revBtn = document.getElementById('btn-playrev');
  if (revBtn) revBtn.classList.remove('active');
  _syncScrubber();
  _updatePlayBtn();
  repaint();
}

function playbackScrub(v) {
  playbackPos = parseInt(v, 10) / 1000;
  repaint();
}

function playbackSetSpeed(v) {
  playbackSpeed = parseFloat(v);
}

function playbackToggleDir() {
  playbackForward = !playbackForward;
  const btn = document.getElementById('btn-playrev');
  if (btn) btn.classList.toggle('active', !playbackForward);
}

function _playbackStep(timestamp) {
  if (!playbackPlaying) return;
  const elapsed = _playbackLastTime ? (timestamp - _playbackLastTime) : 16;
  _playbackLastTime = timestamp;
  const fracAdvance = _routeTotalDist > 0
    ? (_PLAYBACK_WORLD_SPEED * playbackSpeed * elapsed / 1000) / _routeTotalDist
    : 0;
  if (playbackForward) {
    playbackPos = Math.min(1.0, playbackPos + fracAdvance);
    if (playbackPos >= 1.0) { playbackPlaying = false; _updatePlayBtn(); }
  } else {
    playbackPos = Math.max(0.0, playbackPos - fracAdvance);
    if (playbackPos <= 0.0) { playbackPlaying = false; _updatePlayBtn(); }
  }
  _syncScrubber();
  repaint();
  if (playbackPlaying) _playbackAF = requestAnimationFrame(_playbackStep);
}

function _syncScrubber() {
  const scrubber = document.getElementById('scrubber');
  if (scrubber) scrubber.value = Math.round(playbackPos * 1000);
}

function _updatePlayBtn() {
  const btn = document.getElementById('btn-play');
  if (btn) {
    btn.textContent = playbackPlaying ? '⏸' : '▶';
    btn.classList.toggle('active', playbackPlaying);
  }
}

function _showTransport(visible) {
  const bar = document.getElementById('transport-bar');
  if (bar) bar.classList.toggle('hidden', !visible);
}

// ---------------------------------------------------------------------------
// Draw-in-progress (with snap-to-first-point highlight)
// ---------------------------------------------------------------------------

let SNAP_RADIUS = 12; // world inches at 100 % (follows the zoom: _applyViewTolerances)

function drawInProgress() {
  const cpts = drawPts.map(([wx, wy]) => worldToCanvas(wx, wy));
  drawPolyline(cpts, '#4a9eff', 1.5, false, false);
  for (const [cx, cy] of cpts) drawDot(cx, cy, 3, '#4a9eff');

  // Snap-to-first highlight when mouse is within snap radius and ≥3 pts placed
  if (drawPts.length >= 3 && _mousePosW) {
    const [fx, fy] = drawPts[0];
    if (Math.hypot(_mousePosW[0] - fx, _mousePosW[1] - fy) < SNAP_RADIUS) {
      const [cx0, cy0] = worldToCanvas(fx, fy);
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx0, cy0, 9, 0, Math.PI * 2);
      ctx.strokeStyle = '#33cc66';
      ctx.lineWidth = 2;
      ctx.stroke();
      // Preview closing segment from last placed point to first
      if (drawPts.length >= 2) {
        const last = drawPts[drawPts.length - 1];
        const [lx, ly] = worldToCanvas(last[0], last[1]);
        ctx.beginPath();
        ctx.moveTo(lx, ly);
        ctx.lineTo(cx0, cy0);
        ctx.strokeStyle = 'rgba(51,204,102,0.5)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.restore();
    }
  }
}

function drawHandles(pid) {
  const path = layer.source_paths.find(p => p.id === pid);
  if (!path) return;
  const rh = _rotHandle(path);
  if (rh) {
    const [px, py] = worldToCanvas(rh.pivot[0], rh.pivot[1]);
    const [hx, hy] = worldToCanvas(rh.wx, rh.wy);
    ctx.save();
    ctx.strokeStyle = 'rgba(74,158,255,0.45)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(hx, hy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#1b2a3a';
    ctx.strokeStyle = '#4a9eff';
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(hx, hy, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.arc(px, py, 2, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }
  const vh = visibleHandles(path);
  for (const h of vh) {
    drawHandle(h.wx, h.wy, h.shape || 'point');
  }
  const rad = path.type === 'CirclePath' && vh.find(h => h.key === 'radius');
  if (rad) {
    const [cx, cy] = worldToCanvas(path.cx, path.cy);
    const [rx, ry] = worldToCanvas(rad.wx, rad.wy);
    ctx.save();
    ctx.strokeStyle = 'rgba(74,158,255,0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(rx, ry); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }
  if (path.type === 'QuadBezierPath') {
    const [sx, sy] = worldToCanvas(path.start[0],   path.start[1]);
    const [ex, ey] = worldToCanvas(path.end[0],     path.end[1]);
    const [bx, by] = worldToCanvas(path.control[0], path.control[1]);
    ctx.save();
    ctx.strokeStyle = 'rgba(74,158,255,0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(bx, by); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Snapping — make intentional connections easy
//
// While drawing, placing curve ends, dragging endpoints or moving an open
// path, a point near another wall snaps EXACTLY onto it: onto an open
// path's end (→ end-to-end joint), or onto the nearest point of a wall
// line — sources and their visible offset / cap faces — (→ T junction).
// The backend recognises any real contact, snapped or not; snapping just
// makes the contact exact. Hold Alt to place freely.
// ---------------------------------------------------------------------------

const SNAP_DIST = 8;            // world inches
let snapHint = null;            // { pt: [x, y], kind: 'end' | 'edge' } shown on canvas

function _snapTargets(excludeId) {
  const out = [];
  for (const p of layer.source_paths) {
    if (!p.visible || p.id === excludeId) continue;
    const pts = p.points || [];
    if (pts.length < 2) continue;
    out.push({ pts, closed: !!p.closed,
               ends: p.closed ? [] : [pts[0], pts[pts.length - 1]] });
  }
  // Visible wall faces of other systems (offsets, caps, network pieces).
  for (const d of derivedPaths) {
    if (d.role === 'lattice' || d.source_id === excludeId) continue;
    const pts = d.points || [];
    if (pts.length >= 2) out.push({ pts, closed: !!d.closed, ends: [] });
  }
  return out;
}

// Snap target for a world point, or null. Ends win over edges.
function findSnap(wx, wy, excludeId, altKey) {
  if (altKey) return null;
  const targets = _snapTargets(excludeId);
  let best = null;
  for (const t of targets) {
    for (const e of t.ends) {
      const d = Math.hypot(wx - e[0], wy - e[1]);
      if (d < SNAP_DIST && (!best || d < best.d)) best = { pt: [e[0], e[1]], kind: 'end', d };
    }
  }
  if (best) return best;
  for (const t of targets) {
    const n = t.pts.length;
    for (let i = 0; i < (t.closed ? n : n - 1); i++) {
      const a = t.pts[i], b = t.pts[(i + 1) % n];
      const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
      const u = L2 < 1e-18 ? 0 : Math.max(0, Math.min(1, ((wx - a[0]) * dx + (wy - a[1]) * dy) / L2));
      const pt = [a[0] + u * dx, a[1] + u * dy];
      const d = Math.hypot(wx - pt[0], wy - pt[1]);
      if (d < SNAP_DIST && (!best || d < best.d)) best = { pt, kind: 'edge', d };
    }
  }
  return best;
}

function drawSnapHint() {
  if (!snapHint) return;
  const [cx, cy] = worldToCanvas(snapHint.pt[0], snapHint.pt[1]);
  ctx.save();
  ctx.strokeStyle = JUNCTION_COLOR;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 8, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = JUNCTION_COLOR;
  ctx.beginPath();
  ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = '10px monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'bottom';
  const label = snapHint.kind === 'end' ? 'join end' : 'connect';
  const w = label.length * 6 + 6;
  ctx.fillStyle = 'rgba(10,30,20,0.85)';
  ctx.fillRect(cx + 10, cy - 22, w, 14);
  ctx.fillStyle = JUNCTION_COLOR;
  ctx.fillText(label, cx + 13, cy - 9);
  ctx.restore();
}

// Handles that are path ENDS (or any drawn-path point) may snap.
function _snappableHandle(path, key) {
  if (path.type === 'LinePath' || path.type === 'QuadBezierPath') return key === 'start' || key === 'end';
  if (path.type === 'ExplicitPath' || !path.type) return key.startsWith('pt');
  return false;
}

// Body drag of an open path: shift (dx, dy) so the end nearest to a snap
// target lands exactly on it.
function _snapBodyDrag(path, dx, dy, orig, altKey) {
  if (path.closed || altKey) return [dx, dy, null];
  let ends;
  if (path.type === 'LinePath' || path.type === 'QuadBezierPath') ends = [orig.start, orig.end];
  else {
    const pts = orig.control_points || orig.points || [];
    if (pts.length < 2) return [dx, dy, null];
    ends = [pts[0], pts[pts.length - 1]];
  }
  let best = null;
  for (const e of ends) {
    const s = findSnap(e[0] + dx, e[1] + dy, path.id, false);
    if (s && (!best || s.d < best.s.d)) best = { s, e };
  }
  if (!best) return [dx, dy, null];
  return [best.s.pt[0] - best.e[0], best.s.pt[1] - best.e[1], best.s];
}

// ---------------------------------------------------------------------------
// Openings — path-relative gaps (arc length along the source wall)
//
// Geometry is authoritative on the backend (model.Opening / _OpeningPlan).
// These helpers mirror its arc-length conventions on path.points — which
// equal the backend's processed source polyline — for live drawing,
// placement, dragging and dimensions.
// ---------------------------------------------------------------------------

let selectedOpeningId = null;
let openingDrag = null;   // { id, mode: 'slide'|'start'|'end', s0, orig: {center_s, width} }

const OPENING_COLOR = '#e070d0';   // distinct from retrace orange

function _cumLen(pts, closed) {
  const cum = [0];
  const n = pts.length, m = closed ? n : n - 1;
  for (let i = 0; i < m; i++) {
    const a = pts[i], b = pts[(i + 1) % n];
    cum.push(cum[cum.length - 1] + Math.hypot(b[0] - a[0], b[1] - a[1]));
  }
  return cum;
}

// Point at arc length s ([x, y]) plus the left normal of its segment.
function _pointAtS(pts, cum, s, closed, forward = true) {
  const L = cum[cum.length - 1], n = pts.length, m = cum.length - 1;
  if (closed && L > 0) { s = ((s % L) + L) % L; if (!forward && s <= 1e-12) s = L; }
  s = Math.max(0, Math.min(L, s));
  let k = 0;
  if (forward) { while (k < m - 1 && cum[k + 1] <= s) k++; }
  else         { while (k < m - 1 && cum[k + 1] < s) k++; }
  const a = pts[k], b = pts[(k + 1) % n];
  const seg = cum[k + 1] - cum[k];
  const t = seg > 1e-12 ? Math.max(0, Math.min(1, (s - cum[k]) / seg)) : 0;
  const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
  return { pt: [a[0] + t * dx, a[1] + t * dy], normal: [-dy / len, dx / len] };
}

function _subPolylineS(pts, cum, s0, s1, closed) {
  const L = cum[cum.length - 1], n = pts.length;
  if (closed && L > 0) { const sh = Math.floor(s0 / L) * L; s0 -= sh; s1 -= sh; }
  const out = [_pointAtS(pts, cum, s0, closed, true).pt];
  const verts = pts.map((p, j) => [cum[j], p]);
  if (closed) {
    pts.forEach((p, j) => verts.push([cum[j] + L, p]));
    verts.push([2 * L, pts[0]]);
  }
  for (const [pos, p] of verts) if (pos > s0 + 1e-9 && pos < s1 - 1e-9) out.push(p);
  out.push(_pointAtS(pts, cum, s1, closed, false).pt);
  return out;
}

function _projectS(pts, cum, closed, wx, wy) {
  const n = pts.length;
  let best = { s: 0, dist: Infinity };
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const a = pts[i], b = pts[(i + 1) % n];
    const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    const t = L2 < 1e-18 ? 0 : Math.max(0, Math.min(1, ((wx - a[0]) * dx + (wy - a[1]) * dy) / L2));
    const d = Math.hypot(wx - (a[0] + t * dx), wy - (a[1] + t * dy));
    if (d < best.dist) best = { s: cum[i] + t * (cum[i + 1] - cum[i]), dist: d };
  }
  return best;
}

// How far the wall system's end treatment protrudes past a cut face
// (mirrors PrintLayer._opening_cap_reach). Width is the CLEAR opening, so
// each cut face sits this much further out.
function _openingCapReach(pathId) {
  const d = [0];
  for (const ot of layer.offset_treatments) {
    if (ot.source_path_id !== pathId) continue;
    const dir = ot.direction || 'inside';
    const sign = (dir === 'inside' || dir === 'left') ? 1 : -1;
    d.push(sign * Math.abs(ot.distance != null ? ot.distance : 10));
  }
  const W = Math.max(...d) - Math.min(...d);
  const style = layer.cap_style === 'round' ? 'full_round' : (layer.cap_style || 'flat');
  if (W <= 1e-9 || style === 'flat') return 0;
  if (style === 'full_round') return W / 2;
  return Math.min(Math.max(0, layer.cap_corner_radius || 0), W / 2);
}

// Effective [a, b] arc-length CUT interval of one opening (b may exceed L
// on closed paths — it wraps through the seam). null if it removes nothing.
function _openingInterval(op, L, closed) {
  let w = Math.max(0, op.width || 0);
  if (w <= 1e-9 || L <= 1e-9) return null;
  w += 2 * _openingCapReach(op.source_path_id);
  if (closed) {
    if (w >= L) return [0, L];
    const a = (((op.center_s - w / 2) % L) + L) % L;
    return [a, a + w];
  }
  const c = Math.max(0, Math.min(L, op.center_s));
  const a = Math.max(0, c - w / 2), b = Math.min(L, c + w / 2);
  return b - a > 1e-9 ? [a, b] : null;
}

function _openingsOf(pathId) {
  return (layer.openings || []).filter(o => o.source_path_id === pathId);
}

// Surviving stretches of a source path after its openings (same merge
// rules as model._opening_removed_intervals / _surviving_intervals).
function _survivingPieces(path) {
  const pts = path.points || [];
  if (pts.length < 2) return [pts];
  const closed = !!path.closed, cum = _cumLen(pts, closed), L = cum[cum.length - 1];
  const raw = _openingsOf(path.id).map(o => _openingInterval(o, L, closed)).filter(Boolean);
  if (!raw.length) return null;
  if (closed && raw.some(([a, b]) => b - a >= L)) return [];
  // Union (mirrors model._opening_removed_intervals, OPENING_MERGE_TOL).
  const tol = 1e-3;
  raw.sort((x, y) => x[0] - y[0]);
  const merged = [];
  for (const [a, b] of raw) {
    if (merged.length && a <= merged[merged.length - 1][1] + tol)
      merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], b);
    else merged.push([a, b]);
  }
  if (!closed && merged.length) {
    if (merged[0][0] <= tol) merged[0][0] = 0;
    if (merged[merged.length - 1][1] >= L - tol) merged[merged.length - 1][1] = L;
  }
  while (closed && merged.length > 1 && merged[merged.length - 1][1] - L >= merged[0][0] - tol) {
    merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], merged[0][1] + L);
    merged.shift();
  }
  const pieces = [];
  if (closed) {
    if (merged.length === 1 && merged[0][1] - merged[0][0] >= L - tol) return [];
    merged.forEach(([, b], i) => {
      const next = merged[(i + 1) % merged.length][0] + (i === merged.length - 1 ? L : 0);
      pieces.push([b, next]);
    });
  } else {
    let prev = 0;
    for (const [a, b] of merged) { pieces.push([prev, a]); prev = b; }
    pieces.push([prev, L]);
  }
  return pieces.filter(([a, b]) => b - a > tol).map(([a, b]) => _subPolylineS(pts, cum, a, b, closed));
}

function _openingGeom(op) {
  const path = layer.source_paths.find(p => p.id === op.source_path_id);
  if (!path || !(path.points || []).length) return null;
  const pts = path.points, closed = !!path.closed, cum = _cumLen(pts, closed);
  const L = cum[cum.length - 1];
  const iv = _openingInterval(op, L, closed);
  if (!iv) return null;
  const [a, b] = iv;
  // Clear opening = cut interval minus the end-treatment reach each side
  // (open paths may clip the interval at a path end).
  const reach = Math.min(_openingCapReach(op.source_path_id), (b - a) / 2);
  const ca = a + reach, cb = b - reach;
  return {
    path, pts, cum, closed, L, a, b,
    width: cb - ca,
    gap: _subPolylineS(pts, cum, a, b, closed),
    start: _pointAtS(pts, cum, ca, closed, true),
    end: _pointAtS(pts, cum, cb, closed, false),
    mid: _pointAtS(pts, cum, (a + b) / 2, closed, true),
  };
}

function drawOpenings() {
  for (const op of layer.openings || []) {
    const g = _openingGeom(op);
    if (!g || !g.path.visible) continue;
    const sel = op.id === selectedOpeningId;
    const cpts = g.gap.map(([x, y]) => worldToCanvas(x, y));
    ctx.save();
    ctx.globalAlpha = sel ? 0.9 : 0.35;
    ctx.strokeStyle = OPENING_COLOR;
    ctx.lineWidth = sel ? 1.5 : 1;
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    cpts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.stroke();
    ctx.setLineDash([]);
    // Short ticks across the wall at both cut faces
    for (const e of [g.start, g.end]) {
      const [x0, y0] = worldToCanvas(e.pt[0] - e.normal[0] * 6, e.pt[1] - e.normal[1] * 6);
      const [x1, y1] = worldToCanvas(e.pt[0] + e.normal[0] * 6, e.pt[1] + e.normal[1] * 6);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    }
    ctx.restore();
    if (sel) {
      drawHandle(g.start.pt[0], g.start.pt[1], 'square');
      drawHandle(g.end.pt[0], g.end.pt[1], 'square');
    }
  }
}

function _fmtIn(v) {
  return Math.abs(v - Math.round(v)) < 0.05 ? `${Math.round(v)} in` : `${v.toFixed(1)} in`;
}

function drawOpeningDims() {
  for (const op of layer.openings || []) {
    const g = _openingGeom(op);
    if (!g || !g.path.visible) continue;
    // Label sits just off the wall, on the left-normal side at the gap centre
    const [cx, cy] = worldToCanvas(g.mid.pt[0] + g.mid.normal[0] * 14,
                                   g.mid.pt[1] + g.mid.normal[1] * 14);
    _dimLabel(cx, cy, _fmtIn(g.width));
  }
}

// Opening under the cursor: within HIT_DIST of its gap polyline.
function hitTestOpening(wx, wy) {
  for (const op of [...(layer.openings || [])].reverse()) {
    const g = _openingGeom(op);
    if (!g || !g.path.visible) continue;
    for (let j = 0; j < g.gap.length - 1; j++)
      if (distToSeg(wx, wy, g.gap[j], g.gap[j + 1]) < HIT_DIST) return op.id;
  }
  return null;
}

// Resize handle under the cursor. A click nearer the opening's middle than
// to an end slides instead, so narrow openings stay draggable.
function findOpeningHandle(op, wx, wy) {
  const g = _openingGeom(op);
  if (!g) return null;
  const mid = _pointAtS(g.pts, g.cum, (g.a + g.b) / 2, g.closed).pt;
  const dMid = Math.hypot(wx - mid[0], wy - mid[1]);
  const dS = Math.hypot(wx - g.start.pt[0], wy - g.start.pt[1]);
  const dE = Math.hypot(wx - g.end.pt[0], wy - g.end.pt[1]);
  if (dS < HIT_DIST && dS < dMid && dS <= dE) return 'start';
  if (dE < HIT_DIST && dE < dMid) return 'end';
  return null;
}

// Nearest visible source path to a point: { path, s, dist }.
function _nearestSourcePoint(wx, wy) {
  let best = null;
  for (const p of layer.source_paths) {
    if (!p.visible || (p.points || []).length < 2) continue;
    const cum = _cumLen(p.points, !!p.closed);
    const r = _projectS(p.points, cum, !!p.closed, wx, wy);
    if (!best || r.dist < best.dist) best = { path: p, s: r.s, dist: r.dist };
  }
  return best;
}

function placeOpening(wx, wy) {
  const hit = _nearestSourcePoint(wx, wy);
  if (!hit || hit.dist > HIT_DIST * 1.5) {
    setStatus('Opening: click on a wall (source path) to place it.');
    return;
  }
  const op = {
    id: 'o' + (_idCounter++),
    source_path_id: hit.path.id,
    center_s: hit.s,
    width: 12,
    end_treatment: 'inherit',
  };
  layer.openings.push(op);
  selectOpening(op.id);
  routeResult = null;
  scheduleRefresh();
  setTool('edit');
}

function selectOpening(id) {
  selectedOpeningId = id;
  selectedId = null;
  selectedJunction = null;
  updatePathList();
  updatePropPanel();
  updateHint();
  repaint();
}

// Signed shortest difference between two positions on a loop of length L.
function _wrapDelta(d, L) {
  if (L <= 0) return d;
  d = ((d % L) + L) % L;
  return d > L / 2 ? d - L : d;
}

function _normalizeCenter(op, L, closed) {
  op.center_s = closed && L > 0 ? ((op.center_s % L) + L) % L
                                : Math.max(0, Math.min(L, op.center_s));
}

function applyOpeningDrag(wx, wy) {
  const op = (layer.openings || []).find(o => o.id === openingDrag.id);
  const path = op && layer.source_paths.find(p => p.id === op.source_path_id);
  if (!path) return;
  const pts = path.points, closed = !!path.closed, cum = _cumLen(pts, closed);
  const L = cum[cum.length - 1];
  const s = _projectS(pts, cum, closed, wx, wy).s;
  const o = openingDrag.orig;
  if (openingDrag.mode === 'slide') {
    const d = closed ? _wrapDelta(s - openingDrag.s0, L) : s - openingDrag.s0;
    op.center_s = o.center_s + d;
  } else {
    // Keep the opposite end fixed; the dragged end follows the cursor.
    const a = o.center_s - o.width / 2, b = o.center_s + o.width / 2;
    let w;
    if (openingDrag.mode === 'end') {
      w = closed ? ((s - a) % L + L) % L : s - a;
      if (closed && w > L - 1) w = 1;         // dragged backwards past start
      w = Math.max(1, w);
      op.width = w; op.center_s = a + w / 2;
    } else {
      w = closed ? ((b - s) % L + L) % L : b - s;
      if (closed && w > L - 1) w = 1;
      w = Math.max(1, w);
      op.width = w; op.center_s = b - w / 2;
    }
  }
  _normalizeCenter(op, L, closed);
  _syncOpeningPanel(op);
  routeResult = null;
  repaint();
}

function updateOpeningPropPanel(panel, op) {
  const path = layer.source_paths.find(p => p.id === op.source_path_id);
  const title = document.createElement('div');
  title.className = 'path-type';
  title.style.cssText = 'color:' + OPENING_COLOR + ';margin-bottom:2px';
  title.textContent = `Opening in ${path ? (path.label || path.id) : '?'}`;
  panel.appendChild(title);
  const L = path ? _cumLen(path.points || [], !!path.closed).slice(-1)[0] : 0;
  addPropRowNum(panel, 'Width', 'op-width', op.width, v => {
    op.width = Math.max(1, v); routeResult = null; scheduleRefresh(); repaint();
  });
  addPropRowNum(panel, 'Position', 'op-pos', op.center_s, v => {
    op.center_s = v;
    if (path) _normalizeCenter(op, L, !!path.closed);
    _syncOpeningPanel(op); routeResult = null; scheduleRefresh(); repaint();
  });
  const hint = document.createElement('div');
  hint.className = 'path-type';
  hint.textContent = `centre, along path from its start` +
    (path && path.closed ? ` (wraps; perimeter ${L.toFixed(1)} in)` : ` (length ${L.toFixed(1)} in)`);
  panel.appendChild(hint);
  addPropRowSelect(panel, 'End treatment', 'Inherit', ['Inherit'], () => {});
  const sel = panel.lastChild.querySelector('select');
  if (sel) { sel.disabled = true; sel.title = 'Uses the layer End caps setting (Wall Geometry)'; }
  const delBtn = document.createElement('button');
  delBtn.className = 'add-btn';
  delBtn.style.cssText = 'margin-top:10px; color:var(--bad); border-color:#553333;';
  delBtn.textContent = '× Delete opening';
  delBtn.onclick = () => deleteOpening(op.id);
  panel.appendChild(delBtn);
}

function _syncOpeningPanel(op) {
  for (const [key, val] of [['op-width', op.width], ['op-pos', op.center_s]]) {
    const el = document.getElementById('prop-' + key);
    if (el && document.activeElement !== el) el.value = +val.toFixed(2);
  }
}

function deleteOpening(id) {
  layer.openings = (layer.openings || []).filter(o => o.id !== id);
  if (selectedOpeningId === id) selectedOpeningId = null;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateHint();
  repaint();
}

// ---------------------------------------------------------------------------
// Route origin — where a CLOSED printable route (start = end) begins and
// returns. Stored as design state: layer.route_origins = [{ strand, u }] —
// a printable strand id (the router's strand) + fraction u of its length,
// at most one per connected component. The backend inserts that point as a
// vertex (no geometry change) and starts the component's circuit there;
// an origin whose strand is gone falls back to automatic selection. Open
// components (that the physical rules could not close) keep Start / End.
// The marker is dragged along the printable strands of its own run.
// ---------------------------------------------------------------------------
const ORIGIN_COLOR = '#33cc66';
let originDrag = null;       // { strands: Set, pos, strand, u } while dragging

function _runIsClosed(run) {
  return Math.hypot(run.endPos[0] - run.startPos[0], run.endPos[1] - run.startPos[1]) < 1e-6;
}

// Markers of a route: ONE origin per closed run; Start + End per open run.
function routeMarkers(moves) {
  const out = [];
  buildPrintRuns(moves || []).forEach((run, i) => {
    if (_runIsClosed(run)) out.push({ kind: 'origin', pos: run.startPos, run: i });
    else {
      out.push({ kind: 'start', pos: run.startPos, run: i });
      out.push({ kind: 'end', pos: run.endPos, run: i });
    }
  });
  return out;
}

function _drawRouteMarkers(moves, labels) {
  const runs = buildPrintRuns(moves || []);
  for (const mk of routeMarkers(moves)) {
    let pos = mk.pos;
    if (mk.kind === 'origin' && originDrag && originDrag.run === mk.run && originDrag.pos) pos = originDrag.pos;
    const [x, y] = worldToCanvas(pos[0], pos[1]);
    if (mk.kind === 'origin') {
      drawDot(x, y, moveStartMode ? 9 : 6, ORIGIN_COLOR);
      if (moveStartMode) {
        ctx.font = '9px monospace'; ctx.textAlign = 'left'; ctx.fillStyle = ORIGIN_COLOR;
        ctx.fillText('START', x + 11, y - 8);
      }
      continue;
    }
    drawDot(x, y, 6, mk.kind === 'start' ? '#33cc66' : '#cc3333');
    if (labels) {
      ctx.font = '9px monospace';
      ctx.textAlign = 'left';
      ctx.fillStyle = mk.kind === 'start' ? '#33cc66' : '#cc3333';
      ctx.fillText(mk.kind === 'start' ? 'START' : 'END', x + 8, mk.kind === 'start' ? y - 6 : y + 12);
    }
  }
}

// Any route marker under the pointer (Toolpath ON). Only a closed route's
// ORIGIN is interactive, and editable design handles (opening ends / gaps,
// the selected path's handles) are tested BEFORE it (onMouseDown);
// START / END are diagnostics and never take the pointer.
function hitTestRouteMarker(wx, wy) {
  if (!showToolpath || !routeResult || !routeResult.moves) return null;
  let best = null;
  for (const mk of routeMarkers(routeResult.moves)) {
    const d = Math.hypot(mk.pos[0] - wx, mk.pos[1] - wy);
    if (d < HIT_DIST * 0.8 && (!best || d < best.d)) best = { ...mk, d };
  }
  return best;
}

function hitTestOrigin(wx, wy) {
  const mk = hitTestRouteMarker(wx, wy);
  return mk && mk.kind === 'origin' ? mk : null;
}

// Nearest point of the given printable strands: { strand, u, pos }.
function _projectOnStrands(wx, wy, ids) {
  let best = null;
  for (const c of printable || []) {
    if (!ids.has(c.id) || (c.pts || []).length < 2) continue;
    const ring = c.closed ? [...c.pts, c.pts[0]] : c.pts;
    const lens = ring.slice(1).map((q, i) => Math.hypot(q[0] - ring[i][0], q[1] - ring[i][1]));
    const L = lens.reduce((a, b) => a + b, 0);
    if (L < 1e-9) continue;
    let acc = 0;
    for (let i = 0; i < lens.length; i++) {
      const [ax, ay] = ring[i], [bx, by] = ring[i + 1];
      const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
      const t = l2 < 1e-18 ? 0 : Math.max(0, Math.min(1, ((wx - ax) * dx + (wy - ay) * dy) / l2));
      const px = ax + t * dx, py = ay + t * dy;
      const d = Math.hypot(wx - px, wy - py);
      if (!best || d < best.d) best = { d, strand: c.id, u: (acc + t * lens[i]) / L, pos: [px, py] };
      acc += lens[i];
    }
  }
  return best;
}

// MOVE START mode (toolbar): route starts / seams take the pointer FIRST —
// no path, opening or handle is selected or dragged — so a start can be moved
// even where it sits on or next to an opening or other editable geometry.
// Dragging a start marker moves it along its route; clicking a closed route
// places its start there. (Open routes: their Start / End follow from the
// geometry and are not movable.) Leaving the mode restores normal editing.
let moveStartMode = false;
function toggleMoveStart(on) {
  moveStartMode = on === undefined ? !moveStartMode : !!on;
  if (moveStartMode && !showToolpath) toggleToolpath();      // the starts come from the route
  const b = document.getElementById('btn-move-start');
  if (b) b.classList.toggle('toggle-on', moveStartMode);
  originDrag = null;
  setStatus(moveStartMode ? 'Move Start: drag a green start marker, or click a closed route to start it there.'
                          : 'Move Start off — normal editing.');
  updateHint();
  repaint();
}

// Move Start mode's pointer handling (before any design hit test)
function moveStartMouseDown(wx, wy) {
  if (!routeResult || !routeResult.moves) return;
  const runs = buildPrintRuns(routeResult.moves);
  let best = null;
  for (const mk of routeMarkers(routeResult.moves)) {
    const d = Math.hypot(mk.pos[0] - wx, mk.pos[1] - wy);
    if (d < HIT_DIST * 1.6 && (!best || d < best.d)) best = { ...mk, d };
  }
  if (best && best.kind === 'origin') { startOriginDrag(best); return; }
  if (best && best.kind !== 'origin') {
    setStatus('This route is open: its Start / End follow from the geometry (they are not movable).');
    return;
  }
  // a click on a closed route: its start goes there
  let pick = null;
  runs.forEach((run, i) => {
    if (!_runIsClosed(run)) return;
    const strands = new Set(run.moves.map(m => m.strand_id).filter(Boolean));
    const p = _projectOnStrands(wx, wy, strands);
    if (p && p.d < HIT_DIST * 1.6 && (!pick || p.d < pick.p.d)) pick = { i, strands, p };
  });
  if (!pick) return;
  originDrag = { run: pick.i, strands: pick.strands, pos: pick.p.pos, strand: pick.p.strand, u: pick.p.u };
  commitOriginDrag();
}

function startOriginDrag(mk) {
  const run = buildPrintRuns(routeResult.moves)[mk.run];
  const strands = new Set(run.moves.map(m => m.strand_id).filter(Boolean));
  originDrag = { run: mk.run, strands, pos: mk.pos, strand: null, u: null };
}

function moveOriginDrag(wx, wy) {
  const p = _projectOnStrands(wx, wy, originDrag.strands);
  if (p) Object.assign(originDrag, { pos: p.pos, strand: p.strand, u: p.u });
  repaint();
}

function commitOriginDrag() {
  const d = originDrag;
  originDrag = null;
  if (!d || !d.strand) { repaint(); return false; }
  // one origin per component: replace any origin on this run's strands
  // (pos: where the start is — Parallel Walls put their lane-change seam there)
  layer.route_origins = (layer.route_origins || []).filter(o => !d.strands.has(o.strand))
    .concat([{ strand: d.strand, u: d.u, pos: [d.pos[0], d.pos[1]] }]);
  scheduleRefresh();                 // ONE undo step; reroute from the new origin
  setStatus('Route origin moved — the geometry is unchanged; the closed route now begins and ends here.');
  repaint();
  return true;
}

// ---------------------------------------------------------------------------
// Trim — non-destructive suppression of a SECTION of a source path: the
// stretch between two contacts with other paths (or a contact and an open
// end), as in CAD Trim. The backend finds the sections on the design
// geometry (trim.py) and returns them with the network info; a trim stores
// the section's SIGNATURE (the paths bounding it, inside / outside of closed
// bounding paths, its position as a tie-break), never its coordinates, so it
// follows the section when either path is edited. A trim that no longer
// matches is kept but suppresses nothing (shown in the path's Properties).
// ---------------------------------------------------------------------------
const TRIM_COLOR = '#ff5a36';
const TRIM_HINT = 'Trim: hover a section between intersections, click to remove it (non-destructive — Undo restores). Esc exits.';
let trimHover = null;        // the section under the pointer in Trim mode
let _netState = null;        // layer state networkInfo was computed from

function _trimsOf(pathId) { return (layer.trims || []).filter(t => t.source_path_id === pathId); }

// Sections are only offered when they describe the CURRENT design (not a
// response still pending after an edit).
function _sectionsCurrent() { return !!networkInfo && _netState === _histState(); }

// Nearest section (trimmed or not) within the hit distance.
function hitTestTrimSection(wx, wy) {
  if (!_sectionsCurrent()) return null;
  let best = null, bd = HIT_DIST;
  for (const sec of networkInfo.trim_sections || []) {
    const src = layer.source_paths.find(p => p.id === sec.source);
    if (!src || !src.visible) continue;
    const pts = sec.pts || [];
    for (let j = 0; j < pts.length - 1; j++) {
      const d = distToSeg(wx, wy, pts[j], pts[j + 1]);
      if (d < bd) { bd = d; best = sec; }
    }
  }
  return best;
}

function _boundName(ids) { return ids.length ? ids.map(_pathName).join(' + ') : 'its end'; }

function _updateTrimHover(wx, wy) {
  const prev = trimHover;
  const sec = hitTestTrimSection(wx, wy);
  trimHover = sec && !sec.trimmed_by ? sec : null;
  const hint = document.getElementById('hint');
  if (!_sectionsCurrent()) hint.textContent = 'Trim: updating sections…';
  else if (trimHover) hint.textContent = `Click to trim this section of ${_pathName(sec.source)} ` +
    `(between ${_boundName(sec.start)} and ${_boundName(sec.end)}). Esc exits Trim.`;
  else if (sec) hint.textContent = 'This section is already trimmed — Undo, or Restore in the path\'s Properties.';
  else {
    const pid = hitTestPath(wx, wy);
    hint.textContent = pid ? `${_pathName(pid)}: no section here between intersections — it touches no other path, so there is nothing to trim (use Delete).`
                           : TRIM_HINT;
  }
  if (prev !== trimHover) repaint();
}

function trimSection(sec) {
  if (!sec || sec.trimmed_by) return null;
  const t = { id: newId(), source_path_id: sec.source, start: [...sec.start], end: [...sec.end],
              inside: { ...sec.inside }, u_mid: sec.u_mid };
  layer.trims = layer.trims || [];
  layer.trims.push(t);
  sec.trimmed_by = t.id;          // no second trim of it before the refresh
  trimHover = null;
  routeResult = null;
  scheduleRefresh();              // ONE undo step per clicked trim
  updatePathList();
  if (selectedId === sec.source) updatePropPanel();
  setStatus(`Trimmed a section of ${_pathName(sec.source)} — Undo restores it.`);
  repaint();
  return t;
}

function drawTrimHover() {
  if (tool !== 'trim' || !trimHover) return;
  const cp = (trimHover.pts || []).map(([x, y]) => worldToCanvas(x, y));
  if (cp.length < 2) return;
  ctx.save();
  ctx.globalAlpha = 0.9;
  drawPolyline(cp, TRIM_COLOR, 5, false, false);
  ctx.restore();
  for (const q of [cp[0], cp[cp.length - 1]]) drawDot(q[0], q[1], 4, TRIM_COLOR);
}

// After every backend response: refresh what depends on it.
function _afterNetworkUpdate() {
  for (const r of _wsReportEls) _fillWallSystemReport(r.el, r.sys ? { sys: r.sys } : r.id);
  if (_syncSystemWebs()) scheduleRefresh();     // connectivity changed the web groups
  if (!_isTyping()) { updateNetworkSection(); updateWallSystemSection(); }
  // Wall Geometry: say when rounded junctions are limited by the geometry
  const lim = ((networkInfo && networkInfo.junctions) || []).filter(j => j.limited && !j.derived);
  const wn = document.getElementById('wg-junction-note');
  if (wn) wn.textContent = lim.length
    ? `${lim.length} junction${lim.length > 1 ? 's' : ''} limited by the geometry — select its ◆ for the actual radius.` : '';
  if (selectedJunction || (selectedId && _trimsOf(selectedId).length && !_isTyping())) updatePropPanel();
  if (tool === 'trim' && _mousePosW) _updateTrimHover(_mousePosW[0], _mousePosW[1]);
}

function _addTrimRows(panel, path) {
  const mine = _trimsOf(path.id);
  if (!mine.length) return;
  _addSubTitle(panel, 'Trimmed sections');
  const st = (networkInfo && networkInfo.trims) || {};
  const bad = mine.filter(t => st[t.id] && st[t.id].status !== 'ok');
  const n = document.createElement('div');
  n.className = 'path-type'; n.id = 'trim-note';
  n.textContent = `${mine.length} section${mine.length > 1 ? 's' : ''} trimmed — non-destructive: ` +
    `the ${(TYPE_NAMES[path.type] || 'path').toLowerCase()} itself is unchanged.` +
    (bad.length ? ` ${bad.length} ${st[bad[0].id].status} (shown untrimmed): ${st[bad[0].id].reason}.` : '');
  if (bad.length) n.style.color = 'var(--warn)';
  panel.appendChild(n);
  _addButton(panel, 'Restore trimmed sections', () => {
    layer.trims = (layer.trims || []).filter(t => t.source_path_id !== path.id);
    routeResult = null; scheduleRefresh(); updatePathList(); updatePropPanel(); repaint();
  });
}

// ---------------------------------------------------------------------------
// Tool management
// ---------------------------------------------------------------------------

function setTool(t) {
  if (moveStartMode) toggleMoveStart(false);     // a tool choice leaves Move Start
  tool = t;
  drawPts = [];
  snapHint = null;
  _shapeDown = null;
  if (t !== 'trim') trimHover = null;
  for (const name of ['edit', 'draw', 'curve', 'opening', 'trim', 'line', 'rect', 'circle', 'ellipse']) {
    const btn = document.getElementById('tool-' + name);
    if (btn) btn.classList.toggle('active', t === name);
  }
  // Curve uses crosshair like draw; edit uses default arrow
  canvas.className = t === 'edit' ? 'tool-edit' : 'tool-draw';
  updateHint();
  repaint();
}

function updateHint() {
  const hint = document.getElementById('hint');
  if (moveStartMode) {
    hint.textContent = 'Move Start: drag a green start marker along its route, or click a closed route to start it ' +
                       'there. Paths and openings are not edited in this mode — click Move Start again to leave.';
    return;
  }
  if (tool === 'draw') {
    hint.textContent = 'Click to place control points (they snap onto walls to connect — Alt for free placement). Click start point (≥3 pts) to close. Double-click or Enter to finish open path.';
    return;
  }
  if (tool === 'curve') {
    if (drawPts.length === 0) hint.textContent = 'Click to place curve start point (snaps onto walls to connect).';
    else if (drawPts.length === 1) hint.textContent = 'Click to place curve end point (snaps onto walls to connect).';
    else hint.textContent = 'Click to set bend/control point — curve will be placed.';
    return;
  }
  if (tool === 'opening') {
    hint.textContent = 'Click on a wall to place a 12 in opening centred there.';
    return;
  }
  if (tool === 'trim') {
    hint.textContent = TRIM_HINT;
    return;
  }
  if (SHAPE_TOOLS[tool]) {
    const first = { line: 'start point', rect: 'first corner', circle: 'centre', ellipse: 'centre' }[tool];
    const second = { line: 'end point', rect: 'opposite corner', circle: 'a point on the circle',
                     ellipse: 'a corner of its bounding box' }[tool];
    hint.textContent = drawPts.length === 0
      ? `Click (or press and drag) to place the ${first}. Points snap onto walls (Alt = free). Esc cancels.`
      : `Click (or release) to place the ${second}. Esc cancels.`;
    return;
  }
  if (selectedOpeningId) {
    hint.textContent = 'Drag the opening to slide it along the wall. Drag □ ends to resize. Del to delete.';
    return;
  }
  if (selectedJunction) {
    hint.textContent = 'Junction selected: set its corner treatment in the Design sidebar (independent of Corner R).';
    return;
  }
  if (!selectedId) {
    hint.textContent = 'Click a path to select and edit it, or a ◆ junction to treat its corner. Add primitives from the toolbar or use Draw Path.';
    return;
  }
  const path = layer.source_paths.find(p => p.id === selectedId);
  if (!path) { hint.textContent = ''; return; }
  switch (path.type) {
    case 'CirclePath':
      hint.textContent = 'Drag center ✛ to move. Drag □ handle to resize. Edit values in the Design sidebar.'; break;
    case 'EllipsePath':
      hint.textContent = 'Drag center ✛ to move. Drag □ rx/ry handles to resize.'; break;
    case 'RectanglePath':
      hint.textContent = 'Drag □ corner handles to resize. Drag body to move.'; break;
    case 'LinePath':
      hint.textContent = 'Drag ● endpoints to reshape — they snap onto other walls to connect (Alt = free). Drag body to move, ◯ to rotate (Shift = 15°). ⌘Z / ⇧⌘Z undo / redo · ⌘C ⌘V ⌘D copy / paste / duplicate.'; break;
    case 'QuadBezierPath':
      hint.textContent = 'Drag ● start/end handles to reshape. Drag □ bend handle to adjust curvature. Drag body to move.'; break;
    default:
      hint.textContent = 'Drag ● control points to reshape. Drag path body to move. Del to delete.';
  }
}

// ---------------------------------------------------------------------------
// Canvas mouse events
// ---------------------------------------------------------------------------

canvas.addEventListener('mousedown', onMouseDown);
canvas.addEventListener('mousemove', onMouseMove);
canvas.addEventListener('wheel', onWheel, { passive: false });
canvas.addEventListener('auxclick', e => { if (e.button === 1) e.preventDefault(); });

// ---- view navigation: wheel / pinch = zoom at the pointer; Space + drag
// or middle-drag = pan. View only — handled before any tool. -------------
let panDrag = null;          // { x, y } last client position while panning
let spaceDown = false;

function onWheel(e) {
  if (e.preventDefault) e.preventDefault();
  const r = canvas.getBoundingClientRect();
  const k = e.ctrlKey ? 0.01 : 0.0015;              // pinch gestures arrive as ctrl+wheel
  zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-(e.deltaY || 0) * k));
}
document.addEventListener('mouseup', onMouseUp);
canvas.addEventListener('dblclick', onDblClick);

function onMouseDown(e) {
  if (e.button === 1 || spaceDown) {                // pan: never a tool action
    panDrag = { x: e.clientX, y: e.clientY };
    if (e.preventDefault) e.preventDefault();
    return;
  }
  const [wx, wy] = canvasFromEvent(e);

  if (moveStartMode) {                              // route starts first — nothing else
    moveStartMouseDown(wx, wy);
    return;
  }

  if (tool === 'draw') {
    // Snap-to-first: if ≥3 points placed and near first point, close
    if (drawPts.length >= 3) {
      const [fx, fy] = drawPts[0];
      if (Math.hypot(wx - fx, wy - fy) < SNAP_RADIUS) {
        finishDraw(true);
        return;
      }
    }
    const s = findSnap(wx, wy, null, e.altKey);
    drawPts.push(s ? s.pt : [wx, wy]);
    repaint();
    return;
  }

  if (tool === 'curve') {
    // Start and end snap onto walls; the bend (3rd click) never does.
    const s = drawPts.length < 2 ? findSnap(wx, wy, null, e.altKey) : null;
    drawPts.push(s ? s.pt : [wx, wy]);
    if (drawPts.length === 3) {
      finishCurve();
    } else {
      updateHint();
      repaint();
    }
    return;
  }

  if (tool === 'opening') {
    placeOpening(wx, wy);
    return;
  }

  if (tool === 'trim') {
    const sec = hitTestTrimSection(wx, wy);
    if (sec && !sec.trimmed_by) trimSection(sec);
    else _updateTrimHover(wx, wy);
    return;
  }

  if (SHAPE_TOOLS[tool]) {
    _mousePosW = [wx, wy];
    const pt = _shapePoint(wx, wy, e.altKey);
    if (drawPts.length === 0) {
      drawPts = [pt];
      _shapeDown = pt;
      updateHint();
      repaint();
    } else {
      createShape(SHAPE_TOOLS[tool], drawPts[0], pt);
    }
    return;
  }

  if (tool === 'edit') {
    // INTERACTION PRIORITY: editable design handles outrank the toolpath
    // overlay. START / END markers (and arrows) are diagnostics only — they
    // never take the pointer; a closed route's ORIGIN is draggable, but only
    // after the selected opening's / path's handles and opening gaps.
    // 0. Resize handles of the selected opening
    if (selectedOpeningId) {
      const op = layer.openings.find(o => o.id === selectedOpeningId);
      const end = op && findOpeningHandle(op, wx, wy);
      if (end) {
        openingDrag = { id: op.id, mode: end, s0: 0,
                        orig: { center_s: op.center_s, width: op.width } };
        return;
      }
    }
    // 1. Handle on selected path takes priority (rotation handle first)
    if (selectedId) {
      const path = layer.source_paths.find(p => p.id === selectedId);
      const rh = path && _rotHandle(path);
      if (rh && Math.hypot(wx - rh.wx, wy - rh.wy) < HIT_DIST * 0.8) {
        rotateDrag = { pathId: path.id, pivot: rh.pivot,
                       a0: Math.atan2(wy - rh.pivot[1], wx - rh.pivot[0]),
                       orig: capturePathState(path) };
        return;
      }
      if (path) {
        const h = findHandle(path, wx, wy);
        if (h) {
          dragging = { pathId: selectedId, handleKey: h.key,
                       originalState: capturePathState(path) };
          return;
        }
      }
    }

    // 1a. A network junction diamond
    const jn = hitTestJunction(wx, wy);
    if (jn && !selectedId) { selectJunction(jn); return; }
    if (jn && selectedId) {
      const path = layer.source_paths.find(p => p.id === selectedId);
      if (!(path && findHandle(path, wx, wy))) { selectJunction(jn); return; }
    }

    // 1b. An opening gap (slide it) — before the wall body under it
    const oid = hitTestOpening(wx, wy);
    if (oid) {
      const op = layer.openings.find(o => o.id === oid);
      const path = layer.source_paths.find(p => p.id === op.source_path_id);
      if (oid !== selectedOpeningId) selectOpening(oid);
      const cum = _cumLen(path.points, !!path.closed);
      openingDrag = { id: oid, mode: 'slide',
                      s0: _projectS(path.points, cum, !!path.closed, wx, wy).s,
                      orig: { center_s: op.center_s, width: op.width } };
      return;
    }

    // 1c. The route origin of a closed route (Toolpath ON): drag it along
    // its printable route (START / END of an open route: not interactive)
    const rm = hitTestRouteMarker(wx, wy);
    if (rm && rm.kind === 'origin') { startOriginDrag(rm); return; }

    // 2. Hit test any path
    const pid = hitTestPath(wx, wy);
    if (pid) {
      const switching = pid !== selectedId || selectedOpeningId !== null || selectedJunction !== null;
      selectedId = pid;
      selectedOpeningId = null;
      selectedJunction = null;
      const path = layer.source_paths.find(p => p.id === pid);
      if (switching) {
        updatePathList();
        updatePropPanel();
        updateHint();
      }
      if (path && (path.type === 'InsetPath' || _isDriven(path))) {
        setStatus(path.type === 'InsetPath'
          ? `${path.label || path.id} follows its parent — move the parent, or Detach it.`
          : `${path.label || path.id} follows its wall relationship — edit the driving boundary, or break the link.`);
      } else if (path) {
        bodyDragging = { pathId: pid, startWX: wx, startWY: wy,
                         originalState: capturePathState(path) };
      }
      repaint();
      return;
    }

    // 3. Empty click — deselect
    if (selectedId || selectedOpeningId || selectedJunction) {
      selectedId = null;
      selectedOpeningId = null;
      selectedJunction = null;
      updatePathList();
      updatePropPanel();
      updateHint();
      repaint();
    }
  }
}

function onMouseMove(e) {
  if (panDrag) {
    panBy(e.clientX - panDrag.x, e.clientY - panDrag.y);
    panDrag = { x: e.clientX, y: e.clientY };
    return;
  }
  _mousePosW = canvasFromEvent(e);

  if (originDrag) {
    moveOriginDrag(_mousePosW[0], _mousePosW[1]);
    return;
  }
  if (openingDrag) {
    applyOpeningDrag(_mousePosW[0], _mousePosW[1]);
    return;
  }
  if (rotateDrag) {
    const path = layer.source_paths.find(p => p.id === rotateDrag.pathId);
    if (path) {
      const [wx, wy] = _mousePosW;
      let d = Math.atan2(wy - rotateDrag.pivot[1], wx - rotateDrag.pivot[0]) - rotateDrag.a0;
      if (e.shiftKey) d = Math.round(d / (Math.PI / 12)) * (Math.PI / 12);   // 15° steps
      _restorePathState(path, rotateDrag.orig);
      _transformPath(path, _rotMap(rotateDrag.pivot, d), d);
      syncPropPanel(path);
      routeResult = null;
      repaint();
    }
    return;
  }
  if (dragging) {
    let [wx, wy] = _mousePosW;
    const path = layer.source_paths.find(p => p.id === dragging.pathId);
    if (path) {
      snapHint = _snappableHandle(path, dragging.handleKey)
        ? findSnap(wx, wy, path.id, e.altKey) : null;
      if (snapHint) [wx, wy] = snapHint.pt;
      applyHandleDrag(path, dragging.handleKey, wx, wy, dragging.originalState);
      syncPropPanel(path);
      routeResult = null;
      repaint();
    }
    return;
  }
  if (bodyDragging) {
    const [wx, wy] = _mousePosW;
    let dx = wx - bodyDragging.startWX;
    let dy = wy - bodyDragging.startWY;
    if (Math.hypot(dx, dy) < 1) return;
    const path = layer.source_paths.find(p => p.id === bodyDragging.pathId);
    if (path) {
      [dx, dy, snapHint] = _snapBodyDrag(path, dx, dy, bodyDragging.originalState, e.altKey);
      applyBodyDrag(path, dx, dy, bodyDragging.originalState);
      syncPropPanel(path);
      routeResult = null;
      repaint();
    }
    return;
  }

  if (tool === 'trim') {
    _updateTrimHover(_mousePosW[0], _mousePosW[1]);
    return;
  }
  // Shape tools: live preview + snap indicator.
  if (SHAPE_TOOLS[tool]) {
    snapHint = findSnap(_mousePosW[0], _mousePosW[1], null, e.altKey);
    repaint();
    return;
  }
  // Draw / curve: preview where the next click would snap (and repaint
  // for the close-path and live-curve previews).
  if (tool === 'draw' || tool === 'curve') {
    snapHint = (tool === 'draw' || drawPts.length < 2)
      ? findSnap(_mousePosW[0], _mousePosW[1], null, e.altKey) : null;
    repaint();
  }
}

function onMouseUp(e) {
  // Press-drag-release with a shape tool places the shape on release.
  const up = (e && e.clientX !== undefined) ? canvasFromEvent(e) : _mousePosW;
  if (SHAPE_TOOLS[tool] && drawPts.length === 1 && _shapeDown && up &&
      Math.hypot(up[0] - _shapeDown[0], up[1] - _shapeDown[1]) > 3) {
    const pt = _shapePoint(up[0], up[1], e && e.altKey);
    _shapeDown = null;
    createShape(SHAPE_TOOLS[tool], drawPts[0], pt);
    return;
  }
  if (panDrag) { panDrag = null; return; }
  _shapeDown = null;
  if (originDrag) { commitOriginDrag(); return; }
  const wasDragging = dragging !== null || bodyDragging !== null || openingDrag !== null ||
                      rotateDrag !== null;
  snapHint = null;
  dragging = null;
  bodyDragging = null;
  openingDrag = null;
  rotateDrag = null;
  if (wasDragging) { scheduleRefresh(); updatePropPanel(); }   // ONE undo step per drag
}

function onDblClick(e) {
  if (tool === 'draw') finishDraw(false);
}

function _isTyping() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = (el.tagName || '').toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!el.isContentEditable;
}

document.addEventListener('keyup', e => {
  if (e.key === ' ') { spaceDown = false; canvas.style.cursor = ''; }
});

document.addEventListener('keydown', e => {
  if (currentWorkspace !== 'designer') return;     // Designer shortcuts only in the Designer
  if (e.key === ' ' && !_isTyping()) {             // Space held: pan mode
    if (e.preventDefault) e.preventDefault();
    spaceDown = true;
    canvas.style.cursor = 'grab';
    return;
  }
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !_isTyping()) {
    const k = (e.key || '').toLowerCase();
    if (k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (k === 'y') { e.preventDefault(); redo(); return; }
    if (k === 'c') { if (copySelected()) e.preventDefault(); return; }
    if (k === 'v') { if (_clipboard) { e.preventDefault(); pasteClipboard(); } return; }
    if (k === 'd') { e.preventDefault(); duplicateSelected(); return; }
  }
  if (mod) return;          // Cmd-C is copy, never the draw tool's 'c'
  if (e.key === 'c' || e.key === 'C') {
    if (tool === 'draw' && drawPts.length >= 3) finishDraw(true);
  }
  if (e.key === 'Enter') {
    if (tool === 'draw' && drawPts.length >= 2) finishDraw(false);
  }
  if (e.key === 'Escape') {
    if (tool === 'draw') { drawPts = []; repaint(); }
    else if (tool === 'curve') { drawPts = []; setTool('edit'); }
    else if (tool === 'opening' || tool === 'trim' || SHAPE_TOOLS[tool]) { setTool('edit'); }
    else { selectedId = null; selectedOpeningId = null; selectedJunction = null; updatePathList(); updatePropPanel(); updateHint(); repaint(); }
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && !_isTyping()) {
    if (selectedOpeningId) deleteOpening(selectedOpeningId);
    else if (selectedId) deletePath(selectedId);
  }
});

function canvasFromEvent(e) {
  const r = canvas.getBoundingClientRect();
  return canvasToWorld(e.clientX - r.left, e.clientY - r.top);
}

// ---------------------------------------------------------------------------
// Hit testing
// ---------------------------------------------------------------------------

let HIT_DIST = 10; // world inches at 100 % (follows the zoom: _applyViewTolerances)

// The VISIBLE design source: the parametric source minus its trimmed
// sections (the Trim tool's section data). Selection, hover highlight, hit
// testing and outline handles use it, so trimmed-away parts never come back
// as ghost geometry. The parametric source itself is unchanged; when no
// trim applies (or after Undo) this is simply the whole source.
function _visibleSourcePolys(p) {
  const secs = ((networkInfo && networkInfo.trim_sections) || []).filter(q => q.source === p.id);
  if (p.id !== _draggedPathId() && secs.some(q => q.trimmed_by)) {
    return secs.filter(q => !q.trimmed_by).map(q => ({ pts: q.pts, closed: false }));
  }
  return [{ pts: p.points || [], closed: !!p.closed }];
}

function _isTrimmedSource(p) {
  return ((networkInfo && networkInfo.trim_sections) || []).some(q => q.source === p.id && q.trimmed_by)
         && p.id !== _draggedPathId();
}

function _distToPolys(wx, wy, polys) {
  let best = Infinity;
  for (const { pts, closed } of polys) {
    for (let j = 0; j < pts.length - 1; j++) best = Math.min(best, distToSeg(wx, wy, pts[j], pts[j + 1]));
    if (closed && pts.length >= 2) best = Math.min(best, distToSeg(wx, wy, pts[pts.length - 1], pts[0]));
  }
  return best;
}

// Handles on visible geometry only: an outline handle (a rectangle corner,
// a line end …) lying only on trimmed-away geometry is not offered; the
// circle's radius handle moves to the visible arc (its drag only uses the
// distance to the centre). Off-outline handles (centre, bend) stay.
function visibleHandles(path) {
  const hs = getHandles(path);
  if (!_isTrimmedSource(path)) return hs;
  const vis = _visibleSourcePolys(path);
  const full = [{ pts: path.points || [], closed: !!path.closed }];
  const out = [];
  for (const h of hs) {
    if (path.type === 'CirclePath' && h.key === 'radius') {
      const longest = vis.reduce((a, b) => (b.pts.length > a.pts.length ? b : a), vis[0]);
      if (longest && longest.pts.length) {
        const q = longest.pts[Math.floor(longest.pts.length / 2)];
        out.push({ ...h, wx: q[0], wy: q[1] });
      }
      continue;
    }
    const onOutline = _distToPolys(h.wx, h.wy, full) < 0.5;
    if (onOutline && _distToPolys(h.wx, h.wy, vis) > 0.5) continue;    // trimmed away
    out.push(h);
  }
  return out;
}

function hitTestPath(wx, wy) {
  for (let i = layer.source_paths.length - 1; i >= 0; i--) {
    const p = layer.source_paths[i];
    if (_distToPolys(wx, wy, _visibleSourcePolys(p)) < HIT_DIST) return p.id;
  }
  return null;
}

function findHandle(path, wx, wy) {
  for (const h of visibleHandles(path)) {
    if (Math.hypot(wx - h.wx, wy - h.wy) < HIT_DIST) return h;
  }
  return null;
}

function distToSeg(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-10) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

// ---------------------------------------------------------------------------
// Draw tool — finish path
// ---------------------------------------------------------------------------

function finishDraw(closed) {
  if (drawPts.length < 2) { drawPts = []; repaint(); return; }
  const pts = [...drawPts];
  if (pts.length >= 2 && !closed) {
    const a = pts[pts.length - 1], b = pts[pts.length - 2];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 8) pts.pop();
  }
  if (pts.length < 2) { drawPts = []; repaint(); return; }

  const id = newId();
  layer.source_paths.push({
    id, type: 'ExplicitPath',
    label: _nextLabel('ExplicitPath'),
    closed, role: 'free', visible: true,
    points: pts, control_points: pts,
  });
  selectedId = id;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  setTool('edit'); // auto-return to edit (also calls updateHint + repaint)
}

// ---------------------------------------------------------------------------
// Curve tool — 3-click: start, end, bend/control
// ---------------------------------------------------------------------------

function finishCurve() {
  if (drawPts.length < 3) { drawPts = []; repaint(); return; }
  const [start, end, control] = drawPts;
  const id = newId();
  const path = {
    id, type: 'QuadBezierPath',
    label: _nextLabel('QuadBezierPath'),
    closed: false, role: 'free', visible: true,
    start, end, control,
  };
  _computePrimitivePoints(path);
  layer.source_paths.push(path);
  selectedId = id;
  routeResult = null;
  drawPts = [];
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  setTool('edit');
}

function drawCurveInProgress() {
  const mouse = _mousePosW;
  if (drawPts.length === 0) {
    if (mouse) {
      const [mcx, mcy] = worldToCanvas(mouse[0], mouse[1]);
      drawDot(mcx, mcy, 4, 'rgba(74,158,255,0.4)');
    }
    return;
  }
  if (drawPts.length === 1) {
    const [x0, y0] = worldToCanvas(drawPts[0][0], drawPts[0][1]);
    drawDot(x0, y0, 4, '#4a9eff');
    if (mouse) {
      const [mcx, mcy] = worldToCanvas(mouse[0], mouse[1]);
      ctx.save();
      ctx.strokeStyle = '#4a9eff';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(mcx, mcy); ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
      drawDot(mcx, mcy, 3, 'rgba(74,158,255,0.5)');
    }
    return;
  }
  if (drawPts.length === 2) {
    const p0 = drawPts[0], p2 = drawPts[1];
    const p1 = mouse || p2;
    const [x0, y0] = worldToCanvas(p0[0], p0[1]);
    const [x2, y2] = worldToCanvas(p2[0], p2[1]);
    const [xc, yc] = worldToCanvas(p1[0], p1[1]);
    // Dashed control structure lines
    ctx.save();
    ctx.strokeStyle = 'rgba(74,158,255,0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(xc, yc); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(xc, yc); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
    // Control point ghost
    drawDot(xc, yc, 3, 'rgba(74,158,255,0.55)');
    // Endpoint dots
    drawDot(x0, y0, 4, '#4a9eff');
    drawDot(x2, y2, 4, '#4a9eff');
    // Live bezier
    ctx.save();
    ctx.strokeStyle = '#4a9eff';
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.quadraticCurveTo(xc, yc, x2, y2);
    ctx.stroke();
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Interactive shape tools — nothing is created until the shape is placed.
//   Line:    start → end          Rect:    corner → opposite corner
//   Circle:  centre → radius      Ellipse: centre → bounding-box corner
// Two clicks, or press-drag-release. Points snap onto walls (Alt = free).
// Escape cancels. After placing, back to Edit with the new shape selected.
// ---------------------------------------------------------------------------

const SHAPE_TOOLS = { line: 'LinePath', rect: 'RectanglePath',
                      circle: 'CirclePath', ellipse: 'EllipsePath' };
let _shapeDown = null;        // first point when it was a press (for drag)

function _shapeFields(type, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  switch (type) {
    case 'LinePath': return { start: [a[0], a[1]], end: [b[0], b[1]] };
    case 'RectanglePath': return { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]),
                                   w: Math.max(1, Math.abs(dx)), h: Math.max(1, Math.abs(dy)) };
    case 'CirclePath': return { cx: a[0], cy: a[1], radius: Math.max(1, Math.hypot(dx, dy)) };
    case 'EllipsePath': return { cx: a[0], cy: a[1], rx: Math.max(1, Math.abs(dx)),
                                 ry: Math.max(1, Math.abs(dy)), rotation: 0 };
  }
  return {};
}

function createShape(type, a, b) {
  if (type === 'LinePath' && Math.hypot(b[0] - a[0], b[1] - a[1]) < 1) return null;
  const id = newId();
  const path = { id, type, label: _nextLabel(type), closed: type !== 'LinePath',
                 role: 'free', visible: true, ..._shapeFields(type, a, b) };
  _computePrimitivePoints(path);
  layer.source_paths.push(path);
  selectedId = id;
  selectedOpeningId = null;
  selectedJunction = null;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  setTool('edit');
  return path;
}

function _shapePoint(wx, wy, altKey) {
  const s = findSnap(wx, wy, null, altKey);
  return s ? s.pt : [wx, wy];
}

function drawShapeInProgress() {
  const type = SHAPE_TOOLS[tool];
  if (!type || drawPts.length !== 1 || !_mousePosW) return;
  const b = snapHint ? snapHint.pt : _mousePosW;
  const ghost = { type, closed: type !== 'LinePath', ..._shapeFields(type, drawPts[0], b) };
  _computePrimitivePoints(ghost);
  const cp = (ghost.points || []).map(([x, y]) => worldToCanvas(x, y));
  drawPolyline(cp, '#4a9eff', 1.5, true, ghost.closed);
  const [x0, y0] = worldToCanvas(drawPts[0][0], drawPts[0][1]);
  drawDot(x0, y0, 4, '#4a9eff');
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

const PRIM_DEFAULTS = {
  LinePath:      () => ({ start: [140, 200], end: [260, 200] }),
  CirclePath:    () => ({ cx: 200, cy: 200, radius: 60 }),
  EllipsePath:   () => ({ cx: 200, cy: 200, rx: 80, ry: 50, rotation: 0 }),
  RectanglePath: () => ({ x: 140, y: 140, w: 120, h: 120 }),
};

// Auto-numbered default labels (Line 1, Line 2, Rect 1 …); a label the
// user typed is never touched.
const TYPE_NAMES = { LinePath: 'Line', RectanglePath: 'Rect', CirclePath: 'Circle',
                     EllipsePath: 'Ellipse', QuadBezierPath: 'Curve', ExplicitPath: 'Path',
                     InsetPath: 'Inset' };

function _nextLabel(type) {
  const base = TYPE_NAMES[type] || 'Path';
  const re = new RegExp('^' + base + ' (\\d+)$');
  let n = 0;
  for (const p of layer.source_paths) {
    const m = (p.label || '').match(re);
    if (m) n = Math.max(n, +m[1]);
  }
  return `${base} ${n + 1}`;
}

function addPrimitive(type) {
  const id = newId();
  const path = {
    id, type,
    label: _nextLabel(type),
    closed: type !== 'LinePath',
    role: 'free', visible: true,
    ...PRIM_DEFAULTS[type](),
  };
  _computePrimitivePoints(path);
  layer.source_paths.push(path);
  selectedId = id;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  setTool('edit'); // auto-return to edit
}

function _applyCornerRounding(pts, radius, closed) {
  if (radius <= 0 || pts.length < 3) return pts.slice();
  const n = pts.length;
  function filletVertex(A, B, C) {
    const dBAx = A[0]-B[0], dBAy = A[1]-B[1];
    const dBCx = C[0]-B[0], dBCy = C[1]-B[1];
    const lenBA = Math.hypot(dBAx,dBAy), lenBC = Math.hypot(dBCx,dBCy);
    if (lenBA < 1e-12 || lenBC < 1e-12) return null;
    const uBAx=dBAx/lenBA, uBAy=dBAy/lenBA, uBCx=dBCx/lenBC, uBCy=dBCy/lenBC;
    const dot = Math.max(-1,Math.min(1, uBAx*uBCx+uBAy*uBCy));
    const theta = Math.acos(dot);
    if (theta < 1e-6 || Math.PI-theta < 1e-6) return null;
    const half = theta/2, tanH = Math.tan(half);
    if (Math.abs(tanH) < 1e-12) return null;
    let t = radius/tanH;
    t = Math.min(t, lenBA/2, lenBC/2);
    if (t < 1e-9) return null;
    const aR = t*tanH;
    const T1=[B[0]+t*uBAx, B[1]+t*uBAy], T2=[B[0]+t*uBCx, B[1]+t*uBCy];
    const bx=uBAx+uBCx, by=uBAy+uBCy, bL=Math.hypot(bx,by);
    if (bL < 1e-12) return [T1,T2];
    const bux=bx/bL, buy=by/bL;
    const dC=aR/Math.sin(half);
    const Cx=B[0]+dC*bux, Cy=B[1]+dC*buy;
    const t1x=T1[0]-Cx,t1y=T1[1]-Cy, t2x=T2[0]-Cx,t2y=T2[1]-Cy;
    const crossZ=t1x*t2y-t1y*t2x;
    const sa=Math.atan2(t1y,t1x);
    let sw=Math.PI-theta; if (crossZ<0) sw=-sw;
    const nP=Math.max(3, Math.round(Math.abs(sw)*36/Math.PI)+2);
    const arc=[];
    for (let i=0;i<nP;i++){const f=i/(nP-1),a=sa+f*sw;arc.push([Cx+aR*Math.cos(a),Cy+aR*Math.sin(a)]);}
    return arc;
  }
  const result=[];
  if (closed) {
    for (let i=0;i<n;i++){
      const arc=filletVertex(pts[(i+n-1)%n],pts[i],pts[(i+1)%n]);
      if (arc) result.push(...arc); else result.push(pts[i]);
    }
  } else {
    result.push(pts[0]);
    for (let i=1;i<n-1;i++){
      const arc=filletVertex(pts[i-1],pts[i],pts[i+1]);
      if (arc) result.push(...arc); else result.push(pts[i]);
    }
    result.push(pts[n-1]);
  }
  return result;
}

// A path's OWN Corner R (rounds its original corners only — never the
// corners a wall network creates at junctions). Legacy fallback: layer.
function _cornerR(path) {
  if (path.corner_radius != null) return Math.max(0, +path.corner_radius || 0);
  return (typeof layer !== 'undefined' && layer.corner_radius) ? layer.corner_radius : 0;
}

function _computePrimitivePoints(path) {
  if (path.type === 'LinePath') {
    path.points = [path.start, path.end];
  } else if (path.type === 'CirclePath') {
    const n = 128;   // = backend _processed_source_pts sampling
    path.points = Array.from({ length: n }, (_, i) => {
      const a = 2 * Math.PI * i / n;
      return [path.cx + path.radius * Math.cos(a), path.cy + path.radius * Math.sin(a)];
    });
  } else if (path.type === 'EllipsePath') {
    const n = 128;
    const cr = Math.cos(path.rotation || 0), sr = Math.sin(path.rotation || 0);
    path.points = Array.from({ length: n }, (_, i) => {
      const a = 2 * Math.PI * i / n;
      const lx = path.rx * Math.cos(a), ly = path.ry * Math.sin(a);
      return [path.cx + lx * cr - ly * sr, path.cy + lx * sr + ly * cr];
    });
  } else if (path.type === 'InsetPath') {
    // derived: points come from the parent (preview here, backend exact)
  } else if (path.type === 'RectanglePath') {
    const corners = _rectCorners(path);
    const r = _cornerR(path);
    path.points = r > 0 ? _applyCornerRounding(corners, r, true) : corners;
  } else if (path.type === 'QuadBezierPath') {
    const n = 128;
    path.points = Array.from({ length: n }, (_, i) => {
      const t = i / (n - 1);
      const mt = 1 - t;
      return [
        mt*mt * path.start[0] + 2*mt*t * path.control[0] + t*t * path.end[0],
        mt*mt * path.start[1] + 2*mt*t * path.control[1] + t*t * path.end[1],
      ];
    });
  } else {
    const raw = path.control_points || path.points || [];
    const r = _cornerR(path);
    path.points = (r > 0 && raw.length >= 3)
      ? _applyCornerRounding(raw, r, !!path.closed)
      : raw;
  }
}

// ---------------------------------------------------------------------------
// Path list UI
// ---------------------------------------------------------------------------

// Wall-network membership: source id → { label: 'N1', members: [...] }.
// Wall-network membership: source paths that touch / cross (the networks
// the designer builds). source id → { label: 'N1', ids, names, wall }.
function _networkOf() {
  const out = {};
  ((networkInfo && networkInfo.source_networks) || []).forEach(c => {
    const names = c.sources.map(id => {
      const p = layer.source_paths.find(s => s.id === id);
      return p ? (p.label || p.id) : id;
    });
    for (const id of c.sources) out[id] = { label: c.id, ids: c.sources, names, wall: c.wall };
  });
  return out;
}

function updatePathList() {
  const el = document.getElementById('path-list');
  el.innerHTML = '';
  const nets = _networkOf();
  for (const p of layer.source_paths) {
    const item = document.createElement('div');
    item.className = 'path-item' + (p.id === selectedId ? ' selected' : '');
    item.onclick = () => { selectedId = p.id; selectedOpeningId = null; selectedJunction = null; updatePathList(); updatePropPanel(); updateHint(); repaint(); };
    item.onmouseenter = () => setHighlight(p.id);
    item.onmouseleave = () => setHighlight(null);

    const dot = document.createElement('div');
    dot.className = 'role-dot';
    dot.style.background = ROLE_COLORS[p.role] || '#aaa';

    const label = document.createElement('div');
    label.className = 'path-label';
    label.textContent = p.label || p.id;

    const type = document.createElement('div');
    type.className = 'path-type';
    type.textContent = (p.type || 'Path').replace('Path', '').toLowerCase();

    item.append(dot, label, type);
    const net = nets[p.id];
    if (net) {
      const badge = document.createElement('div');
      badge.className = 'path-type';
      badge.style.cssText = `color:${JUNCTION_COLOR};margin-left:6px`;
      badge.textContent = `⛓ ${net.label}`;
      badge.title = `Connected wall network ${net.label}: ${net.names.join(', ')}`;
      item.appendChild(badge);
    }
    const ws = _wsOf(p.id);
    if (ws) {
      const b = document.createElement('div');
      b.className = 'path-type wallsys-badge';
      b.style.cssText = 'color:var(--accent);margin-left:6px;max-width:78px;overflow:hidden;' +
                        'text-overflow:ellipsis;white-space:nowrap';
      b.textContent = `▣ ${_wsLabel(ws)}`;
      b.title = `Wall System ${_wsLabel(ws)} (${WALL_SYSTEMS[ws.type] ? WALL_SYSTEMS[ws.type].label : ws.type})`;
      item.appendChild(b);
    }
    el.appendChild(item);

    for (const op of _openingsOf(p.id)) {
      const oi = document.createElement('div');
      oi.className = 'path-item' + (op.id === selectedOpeningId ? ' selected' : '');
      oi.style.paddingLeft = '18px';
      oi.onclick = () => selectOpening(op.id);
      const od = document.createElement('div');
      od.className = 'role-dot';
      od.style.background = OPENING_COLOR;
      const ol = document.createElement('div');
      ol.className = 'path-label';
      ol.textContent = `opening ${_fmtIn(op.width)}`;
      const ot = document.createElement('div');
      ot.className = 'path-type';
      ot.textContent = `@ ${op.center_s.toFixed(0)}`;
      oi.append(od, ol, ot);
      el.appendChild(oi);
    }
  }
}

// ---------------------------------------------------------------------------
// Property panel
// ---------------------------------------------------------------------------

function updatePropPanel() {
  updateNetworkSection();
  updateWallSystemSection();
  const section = document.getElementById('path-props-section');
  const panel   = document.getElementById('path-props');
  // A junction is a corner where walls of the network meet: its treatment is
  // physical wall geometry, shown in the Design sidebar next to the layer
  // junction default (Wall Geometry).
  const jSection = document.getElementById('junction-props-section');
  const jPanel   = document.getElementById('junction-props');
  jSection.style.display = 'none';

  if (selectedOpeningId) {
    const op = layer.openings.find(o => o.id === selectedOpeningId);
    if (op) {
      section.style.display = '';
      panel.innerHTML = '';
      updateOpeningPropPanel(panel, op);
      return;
    }
  }
  if (selectedJunction) {
    section.style.display = 'none';
    jSection.style.display = '';
    jPanel.innerHTML = '';
    updateJunctionPropPanel(jPanel);
    return;
  }
  if (!selectedId) { section.style.display = 'none'; return; }
  const path = layer.source_paths.find(p => p.id === selectedId);
  if (!path) { section.style.display = 'none'; return; }
  section.style.display = '';
  panel.innerHTML = '';

  const head = document.createElement('div');
  head.className = 'prop-header';
  head.textContent = `${path.label || path.id}`;
  const kind = document.createElement('span');
  kind.className = 'prop-header-kind';
  kind.textContent = `  ${(TYPE_NAMES[path.type] || 'Path').toLowerCase()}`;
  head.appendChild(kind);
  head.onmouseenter = () => setHighlight(path.id);
  head.onmouseleave = () => setHighlight(null);
  panel.appendChild(head);
  addPropRowText(panel, 'Label', path.label || '', v => { path.label = v; historyCheckpoint(); updatePathList(); updatePropPanel(); });
  if (path.type !== 'QuadBezierPath') {
    addPropRowCheck(panel, 'Closed', path.closed, v => {
      path.closed = v; routeResult = null; scheduleRefresh(); repaint();
    });
  }

  switch (path.type) {
    case 'CirclePath':
      addPropRowNum(panel, 'Center X', 'cx', path.cx, v => { path.cx = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Center Y', 'cy', path.cy, v => { path.cy = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Radius',   'radius', path.radius, v => { path.radius = Math.max(1, v); _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      break;
    case 'EllipsePath':
      addPropRowNum(panel, 'Center X', 'cx', path.cx, v => { path.cx = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Center Y', 'cy', path.cy, v => { path.cy = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Radius X', 'rx', path.rx, v => { path.rx = Math.max(1, v); _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Radius Y', 'ry', path.ry, v => { path.ry = Math.max(1, v); _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      break;
    case 'RectanglePath':
      addPropRowNum(panel, 'X',      'x', path.x, v => { path.x = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Y',      'y', path.y, v => { path.y = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Width',  'w', path.w, v => { path.w = Math.max(1, v); _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Length', 'h', path.h, v => { path.h = Math.max(1, v); _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      _addCornerRRow(panel, path);
      break;
    case 'LinePath':
      addPropRowNum(panel, 'Start X', 'x0', path.start[0], v => { path.start[0] = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Start Y', 'y0', path.start[1], v => { path.start[1] = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'End X',   'x1', path.end[0],   v => { path.end[0]   = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'End Y',   'y1', path.end[1],   v => { path.end[1]   = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      break;
    case 'QuadBezierPath':
      addPropRowNum(panel, 'Start X', 'x0', path.start[0],   v => { path.start[0]   = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Start Y', 'y0', path.start[1],   v => { path.start[1]   = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'End X',   'x1', path.end[0],     v => { path.end[0]     = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'End Y',   'y1', path.end[1],     v => { path.end[1]     = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Bend X',  'bx', path.control[0], v => { path.control[0] = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Bend Y',  'by', path.control[1], v => { path.control[1] = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      break;
  }

  if (path.type === 'ExplicitPath' || !path.type) _addCornerRRow(panel, path);
  if (path.type === 'InsetPath') _addInsetChildRows(panel, path);
  else if (!_isDriven(path)) _addTransformRows(panel, path);
  _addWallRows(panel, path);                // PRIMARY: Wall Thickness + Alignment
  _addAdvancedGeometryRows(panel, path);    // special relationships / CAD operations
  _addTrimRows(panel, path);                // non-destructive trims of its sections

  // Delete button
  const delBtn = document.createElement('button');
  delBtn.className = 'add-btn';
  delBtn.style.cssText = 'margin-top:10px; color:var(--bad); border-color:#553333;';
  delBtn.textContent = '× Delete path';
  delBtn.onclick = () => deletePath(path.id);
  panel.appendChild(delBtn);
  _addNetworkPanel(panel, path);
}

// --- Wall model: the source path is the REFERENCE geometry. Wall Thickness
// turns it into a printable wall region; Wall Alignment says where the
// thickness lies relative to the reference. A Centered wall's reference is
// a construction line (not printed unless asked) — the faces and the infill
// are what print. Network Wall Thickness applies to every path of the
// connected network unless a path overrides it.
const ALIGN_UI = {           // UI label ↔ stored value
  Inside: 'inside', Centered: 'center', Outside: 'outside', Left: 'left', Right: 'right',
};
function _alignOptions(closed) { return closed ? ['Inside', 'Centered', 'Outside'] : ['Centered', 'Left', 'Right']; }
function _alignLabel(align, closed) {
  const a = (!align || align === 'auto') ? (closed ? 'inside' : 'center') : align;
  return Object.keys(ALIGN_UI).find(k => ALIGN_UI[k] === a) || (closed ? 'Inside' : 'Centered');
}
// Wall Thickness semantics: > 0 = a thick architectural wall; 0 = a SINGLE-
// BEAD path (physical rules: its return-lane solution; never lattice). A path
// in a thick-walled network inherits the network wall (path.wall = null) until
// it overrides it — `{ thickness: 0 }` is the explicit single-bead override.
function _effectiveWall(path) {
  const net = _networkOf()[path.id];
  const sys = _wsOf(path.id);
  if (sys && sys.type === 'single') return { wall: null, from: 'system', net, sys };
  if (sys && sys.thickness != null) {
    // the WALL SYSTEM owns the envelope
    return sys.thickness > 0
      ? { wall: { thickness: sys.thickness, align: sys.align || 'auto', print_reference: !!sys.print_reference },
          from: 'system', net, sys }
      : { wall: null, from: 'system', net, sys };
  }
  const nw = net ? _netWallFor(net.ids) : null;
  if (path.wall && path.wall.thickness > 0) return { wall: path.wall, from: 'path', net, nw };
  if (path.wall && nw) return { wall: null, from: 'single', net, nw };
  return nw ? { wall: nw, from: 'network', net, nw } : { wall: null, from: null, net, nw };
}

function _addSubTitle(panel, text, color) {
  const t = document.createElement('div');
  t.className = 'section-title';
  t.style.cssText = 'margin-top:12px;' + (color ? 'color:' + color : '');
  t.textContent = text;
  panel.appendChild(t);
}

function _addNote(panel, text) {
  const n = document.createElement('div');
  n.className = 'path-type';
  n.textContent = text;
  panel.appendChild(n);
}

function _addButton(panel, text, onclick, disabled) {
  const b = document.createElement('button');
  b.className = 'add-btn';
  b.style.marginTop = '4px';
  b.textContent = text;
  b.disabled = !!disabled;
  b.onclick = onclick;
  panel.appendChild(b);
}

// WALL SYSTEMS (wall_systems.py; model.WallSystem) — the CONSTRUCTION of an
// explicit, user-authored group of Design paths (its own sidebar):
//   layer.wall_systems = [{ id, name, type, thickness, align, print_reference,
//                           params, members, web: { pattern, params, variation_index } }]
// A path belongs to at most ONE system and members need not touch: the Wall
// Network is geometric connectivity only (junctions / envelopes), never
// construction. A path in no system is a single bead. Skin + Web's web
// lattice is materialised as infill records OWNED by the system (one per
// connected group of members; ids kept, so Layer-Design lineage still keys
// the lattice on them). Legacy walls (path.wall, Network Walls, wall infills,
// the earlier membership-only systems) are migrated once by the backend.
const WALL_SYSTEMS = {
  single: { label: 'Single / Out-and-Back Wall', params: [], noEnvelope: true },
  hollow: { label: 'Hollow / Skins Only', params: [] },
  skin_web: { label: 'Skin + Web', params: [] },
  parallel: { label: 'Parallel Walls', params: [
    { name: 'walls', label: 'Number of Walls', default: 3, min: 1, max: 12, int: true, unit: '',
      hint: 'Evenly spread across the Wall Thickness (outermost half a bead inside the faces). ' +
            'An even number closes into one route at wall ends / openings.' }] },
  interleaved: { label: 'Interleaved Waves', params: [
    { name: 'pattern', label: 'Waveform', options: ['wave', 'zigzag'], default: 'wave' },
    { name: 'paths', label: 'Number of Paths', default: 3, min: 1, max: 12, int: true, unit: '' },
    { name: 'period', label: 'Period', default: 30, min: 2 },
    { name: 'depth', label: 'Depth', default: 0, min: 0, hint: '0 = the full envelope' }] },
  linked: { label: 'Linked Waves', params: [
    { name: 'pattern', label: 'Waveform', options: ['wave', 'zigzag'], default: 'wave' },
    { name: 'paths', label: 'Number of Paths', default: 3, min: 2, max: 12, int: true, unit: '' },
    { name: 'period', label: 'Period', default: 24, min: 2 },
    { name: 'amplitude', label: 'Amplitude', default: 0, min: 0,
      hint: '0 = automatic: neighbours just meet at their extrema; more = deeper interlock' }] },
  chain: { label: 'Chained Loop', params: [
    { name: 'pitch', label: 'Pitch', default: 0, min: 0,
      hint: 'Upper loop → lower loop; 0 = 1.2 × Wall Thickness (any value down to 0.1 in)' },
    { name: 'loop_depth', label: 'Loop Depth', default: 0, min: 0, hint: '0 = the full envelope' },
    { name: 'loop_width', label: 'Loop Width', default: 0, min: 0, hint: '0 = 0.8 × Pitch' },
    { name: 'neck', label: 'Neck', default: 0, min: 0, max: 0.9, unit: '',
      hint: 'Where each loop crosses itself, as a share of Loop Depth (0 = 0.12: loops fill their half of the wall)' },
    { name: 'phase', label: 'Phase', default: 0, min: 0, max: 0.99, unit: '', hint: 'Fraction of a period (0.5 = start with a lower loop)' }] },
};
function _wallSystemType(wall) {          // (legacy wall.system)
  const t = wall && wall.system && wall.system.type;
  return WALL_SYSTEMS[t] && t !== 'skin_web' ? t : 'skin_web';
}
function _wsOf(pid) { return (layer.wall_systems || []).find(s => s.members.includes(pid)) || null; }
function _wsLabel(s) { return s.name || s.id; }
function _newWallSystemId() {
  let k = 1;
  while ((layer.wall_systems || []).some(s => s.id === `WS${k}`)) k++;
  return `WS${k}`;
}
function _wsSetMember(pid, s) {             // at most ONE system per path
  for (const x of layer.wall_systems || []) x.members = x.members.filter(m => m !== pid);
  if (s && !s.members.includes(pid)) s.members.push(pid);
}
function _wsSetType(s, v) {
  s.type = v;
  s.params = {};
  for (const prm of WALL_SYSTEMS[v].params) s.params[prm.name] = prm.default;
  if (v === 'interleaved' || v === 'linked') s.params.paths = 4;   // even: closes at wall ends
  if (v === 'parallel') s.params.walls = 4;
  if (v === 'skin_web' && (!s.web || !s.web.pattern || s.web.pattern === 'none'))
    s.web = { pattern: 'zigzag', params: { spacing: 20 }, variation_index: 0 };
}
let wallSystemPick = null;                  // the system shown in the Wall System panel
function _wsNew(pids = []) {
  layer.wall_systems = layer.wall_systems || [];
  let k = layer.wall_systems.length + 1;
  while (layer.wall_systems.some(s => s.name === `Wall System ${k}`)) k++;
  const s = { id: _newWallSystemId(), name: `Wall System ${k}`, type: 'skin_web', thickness: 10, align: 'auto',
              print_reference: false, params: {}, members: [],
              web: { pattern: 'zigzag', params: { spacing: 20 }, variation_index: 0 } };
  layer.wall_systems.push(s);
  for (const pid of pids) _wsSetMember(pid, s);
  wallSystemPick = s.id;
  return s;
}
// the construction that fills a path's wall ('skin_web' for a plain / no wall)
function _wallSystemOf(path) {
  const s = _wsOf(path.id);
  if (s && s.thickness != null) return s.thickness > 0 ? s.type : 'skin_web';
  const eff = _effectiveWall(path);
  return eff.wall ? _wallSystemType(eff.wall) : 'skin_web';
}

// SKIN + WEB: materialise each system's web as OWNED infill records — one per
// connected group of members (Wall Network = connectivity), reusing an owned
// record already anchored in that group (its id carries the lattice lineage).
// Inactive webs (another construction / 'none') keep their records.
function _syncSystemWebs() {
  let changed = false;
  const nets = (networkInfo && networkInfo.source_networks) || [];
  const groupOf = pid => { const n = nets.find(x => x.sources.includes(pid)); return n ? n.id : 'p:' + pid; };
  const keep = new Set();
  layer.infills = layer.infills || [];
  for (const s of layer.wall_systems || []) {
    if (s.thickness == null) continue;      // (legacy: migrated first)
    const web = s.web || (s.web = { pattern: 'none', params: {}, variation_index: 0 });
    if (s.type === 'skin_web' && (!web.pattern || web.pattern === 'none')) {
      // (earlier designs: Skin + Web with web "None" IS Hollow / Skins Only)
      s.type = 'hollow';
      Object.assign(web, { pattern: 'zigzag', params: { spacing: 20, ...web.params }, variation_index: 0 });
      changed = true;
    }
    const active = s.type === 'skin_web' && web.pattern && web.pattern !== 'none';
    const owned = layer.infills.filter(f => f.owner === s.id);
    const groups = new Map();
    for (const pid of s.members) {
      if (!layer.source_paths.some(p => p.id === pid)) continue;
      const g = groupOf(pid);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(pid);
    }
    for (const pids of groups.values()) {
      let f = owned.find(x => !keep.has(x) && pids.includes(x.path_id));
      if (!f) {
        if (!active) continue;
        f = { id: `${s.id}~web~${pids[0]}`, path_id: pids[0], kind: 'wall', owner: s.id,
              pattern: web.pattern, params: { ...web.params }, variation_index: web.variation_index || 0 };
        layer.infills.push(f);
        changed = true;
      }
      keep.add(f);
      if (active && (f.pattern !== web.pattern || JSON.stringify(f.params) !== JSON.stringify(web.params) ||
                     (f.variation_index || 0) !== (web.variation_index || 0))) {
        f.pattern = web.pattern; f.params = { ...web.params }; f.variation_index = web.variation_index || 0;
        changed = true;
      }
    }
  }
  const n = layer.infills.length;
  layer.infills = layer.infills.filter(f => !f.owner || keep.has(f) ||
                                       (layer.wall_systems || []).some(s => s.id === f.owner && s.thickness == null));
  return changed || layer.infills.length !== n;
}

// MIGRATION (once per legacy design; not an undo step): the backend groups
// every path's effective legacy wall into Wall Systems.
let _wsMigrating = false;
function _legacyWalls() {
  return layer.source_paths.some(p => p.wall) || (layer.network_walls || []).length > 0 ||
         (layer.wall_systems || []).some(s => s.thickness == null);
}
async function _maybeMigrateWallSystems() {
  if (_wsMigrating || !_legacyWalls()) return;
  _wsMigrating = true;
  try {
    const res = await _post('/api/migrate_wall_systems', buildPayload());
    if (!res || !res.migrated || !_legacyWalls()) return;
    layer.wall_systems = res.wall_systems.map(w => ({ ...w, params: { ...(w.params || {}) },
      members: [...(w.members || [])], web: { pattern: 'none', params: {}, variation_index: 0, ...(w.web || {}) } }));
    for (const f of layer.infills || []) if (res.owners[f.id]) f.owner = res.owners[f.id];
    for (const p of layer.source_paths) p.wall = null;
    layer.network_walls = [];
    if (_hist.current) _hist.current.state = _histState();   // the migrated design IS the current state
    routeResult = null;
    updatePathList(); updatePropPanel(); updateInfillList();
    scheduleRefresh();
    setStatus('Walls migrated to Wall Systems (same geometry).');
  } catch (e) { /* keep the legacy design; it still resolves */ } finally { _wsMigrating = false; }
}

let _wsReportEls = [];            // live readouts of the shown panels: { el, id | sys, owner }
function _wsReports(r) {
  const all = (networkInfo && networkInfo.wall_systems) || [];
  return r.sys ? all.filter(w => w.system_id === r.sys) : all.filter(w => (w.sources || []).includes(r.id));
}
function _fillWallSystemReport(d, r) {
  if (typeof r === 'string') r = { id: r };
  const reps = _wsReports(r);
  d.style.color = '';
  const vals = reps.filter(w => w.min_effective_thickness != null);
  if (reps.some(w => w.status === 'mixed')) {
    d.style.color = 'var(--warn)';
    d.textContent = 'A wall of another construction spans between walls of this system (a partition) — ' +
                    'that region prints as Skin + Web (V1).';
  } else if (vals.length) {
    const worst = vals.reduce((a, b) => (b.min_effective_thickness < a.min_effective_thickness ? b : a));
    d.textContent = `Minimum effective thickness: ${worst.min_effective_thickness.toFixed(1)} in ` +
                    `(envelope ${worst.envelope.toFixed(1)} in, bead ${worst.bead} in)`;
    if (vals.some(w => w.closable === false)) {
      // odd path count + free wall ends (openings): one strand end must stay
      // at each end — a closed route is impossible there
      d.textContent += ' · Odd Number of Paths: at its wall ends / openings the route cannot close ' +
                       '(start and end stay at the jambs) — choose an even number for one closed route.';
      d.style.color = 'var(--warn)';
    }
  } else {
    d.textContent = 'Minimum effective thickness: —';
  }
}
function _wallSystemReport(id) { return _wsReports({ id })[0] || null; }

// construction parameters (Interleaved / Linked / Chained Loop)
function _addWallSystemParams(panel, s) {
  // a PARAMETER edit only regenerates the geometry (coalesced requests); the
  // panel is not rebuilt — the input keeps focus and the readout updates in
  // place when the result arrives
  const onParam = () => { routeResult = null; scheduleRefresh(); repaint(); };
  for (const prm of WALL_SYSTEMS[s.type].params) {
    if (prm.options) {
      addPropRowSelectTo(panel, prm.label, s.params[prm.name] ?? prm.default, prm.options, v => {
        s.params[prm.name] = v; onParam();
      }, { wave: 'Wave', zigzag: 'Zigzag' });
      continue;
    }
    addPropRowNumTo(panel, prm.label, s.params[prm.name] ?? prm.default, v => {
      let x = Math.max(prm.min ?? 0, Math.min(prm.max ?? 1e9, v));
      if (prm.int) x = Math.round(x);
      s.params[prm.name] = x; onParam();
    }, prm.unit ?? 'in');
    if (prm.hint) _addNote(panel, prm.hint);
  }
}

// SKIN + WEB: the web lattice (formerly the Wall Infill panel) — edits go to
// the system's web and are synced into its owned infill records
function _addWebRows(panel, s, rebuild) {
  const web = s.web || (s.web = { pattern: 'none', params: {}, variation_index: 0 });
  const onParam = () => { _syncSystemWebs(); routeResult = null; scheduleRefresh(); repaint(); };
  addPropRowSelectTo(panel, 'Web', web.pattern, _patternsFor('wall'), v => {
    web.pattern = v;
    for (const prm of _paramsFor({ pattern: v, kind: 'wall' }))
      if (web.params[prm.name] == null) web.params[prm.name] = prm.default;
    if (v === 'truss' && web.params.seed == null) web.params.seed = 0;
    _syncSystemWebs(); rebuild();
  }, PATTERN_LABELS);
  for (const prm of _paramsFor({ pattern: web.pattern, kind: 'wall' })) {
    addPropRowNumTo(panel, prm.label, web.params[prm.name] ?? prm.default, v => {
      web.params[prm.name] = Math.max(prm.min ?? -1e9, Math.min(prm.max ?? 1e9, v));
      onParam();
    }, prm.unit ?? 'in');
  }
  // readouts of the materialised web (the first owned record)
  const f = (layer.infills || []).find(x => x.owner === s.id);
  if (f) {
    const st = _infillStatus(f);
    if (st && !/already filled/.test(st)) { _addNote(panel, st); panel.lastChild.style.color = 'var(--warn)'; }
    const lin = _infillLineage(f);
    if (lin) {
      const n = document.createElement('div');
      n.className = 'path-type infill-lineage';
      n.textContent = lin.text;
      if (lin.warn) n.style.color = 'var(--warn)';
      panel.appendChild(n);
    }
    const act = _latticeActual(f);
    if (act) _addNote(panel, act);
  }
  if (web.pattern === 'truss') {
    // REGENERATE: another valid solution under the SAME parameters (a
    // deterministic solution seed; undoable; lineage-wide like the pattern)
    const row = document.createElement('div');
    row.className = 'prop-row truss-regenerate';
    const lbl = document.createElement('span');
    lbl.className = 'prop-label';
    lbl.textContent = `Solution ${(web.params.seed || 0) + 1}`;
    const btn = document.createElement('button');
    btn.className = 'add-btn';
    btn.textContent = 'Regenerate';
    btn.title = 'Pick another valid lattice with the same parameters';
    btn.onclick = () => { web.params.seed = (web.params.seed || 0) + 1; _syncSystemWebs(); rebuild(); };
    row.append(lbl, btn);
    panel.appendChild(row);
    _addNote(panel, 'Pitch follows from Brace Angle and the local wall cavity (prototype defaults, not calibrated).');
    return;
  }
  const adv = document.createElement('details');
  adv.className = 'advanced';
  const sm = document.createElement('summary');
  sm.textContent = 'Advanced';
  adv.appendChild(sm);
  const advPanel = document.createElement('div');
  addPropRowNumTo(advPanel, 'Max unsupported', web.params.max_unsupported || 0, v => {
    web.params.max_unsupported = Math.max(0, v);           // 0 = automatic
    onParam();
  }, 'in');
  _addNote(advPanel, '0 = automatic (1.375 × target). Wins over Target Spacing.');
  adv.appendChild(advPanel);
  panel.appendChild(adv);
}

// WALL SYSTEM PANEL (its own Designer sidebar): the systems, then the picked
// one's construction, web / parameters and members.
let _wsLastSel = null;
function updateWallSystemSection() {
  const list = document.getElementById('wallsys-list');
  const panel = document.getElementById('wallsys-props');
  const edit = document.getElementById('wallsys-edit-section');
  if (!list || !panel) return;
  list.innerHTML = ''; panel.innerHTML = '';
  _wsReportEls = _wsReportEls.filter(r => r.owner !== 'sys');
  const all = layer.wall_systems || (layer.wall_systems = []);
  const rebuild = () => { routeResult = null; scheduleRefresh(); updatePropPanel(); updatePathList(); repaint(); };
  const sel = selectedId && layer.source_paths.find(p => p.id === selectedId);
  if (selectedId !== _wsLastSel) {          // selecting a path shows its system
    _wsLastSel = selectedId;
    const ss = sel && _wsOf(sel.id);
    if (ss) wallSystemPick = ss.id;
  }
  const cur = all.find(s => s.id === wallSystemPick) || all[0] || null;
  wallSystemPick = cur ? cur.id : null;
  for (const s of all) {
    const item = document.createElement('div');
    item.className = 'path-item wallsys-item' + (s === cur ? ' selected' : '');
    item.onclick = () => { wallSystemPick = s.id; updateWallSystemSection(); };
    const label = document.createElement('div');
    label.className = 'path-label';
    label.textContent = _wsLabel(s);
    const type = document.createElement('div');
    type.className = 'path-type';
    type.textContent = `${WALL_SYSTEMS[s.type] ? WALL_SYSTEMS[s.type].label : s.type} · ${s.members.length}`;
    item.append(label, type);
    list.appendChild(item);
  }
  const loose = layer.source_paths.filter(p => !_wsOf(p.id)).map(p => _pathName(p.id));
  if (loose.length) _addNote(list, `No Wall System (single bead): ${loose.join(', ')}`);
  _addButton(list, sel && !_wsOf(sel.id) ? `+ New Wall System with ${_pathName(sel.id)}` : '+ New Wall System',
             () => { _wsNew(sel && !_wsOf(sel.id) ? [sel.id] : []); rebuild(); });
  if (!cur) { if (edit) edit.style.display = 'none'; return; }
  if (edit) edit.style.display = '';
  const title = document.getElementById('wallsys-edit-title');
  if (title) title.textContent = _wsLabel(cur);
  // MEMBERS first: only this system's paths (compact ×), then Add path…
  _addSubTitle(panel, 'Members');
  if (!cur.members.length) _addNote(panel, 'No paths yet.');
  for (const pid of cur.members) {
    const r = document.createElement('div');
    r.className = 'prop-row wallsys-member';
    const nm = document.createElement('span');
    nm.className = 'prop-label';
    nm.style.cssText = 'flex:1;cursor:pointer';
    nm.textContent = _pathName(pid);
    nm.onclick = () => { selectedId = pid; updatePathList(); updatePropPanel(); repaint(); };
    nm.onmouseenter = () => setHighlight(pid);
    nm.onmouseleave = () => setHighlight(null);
    const x = document.createElement('button');
    x.className = 'remove-btn';
    x.textContent = '×';
    x.title = `Remove ${_pathName(pid)} from ${_wsLabel(cur)}`;
    x.onclick = () => { _wsSetMember(pid, null); _syncSystemWebs(); rebuild(); };
    r.append(nm, x);
    panel.appendChild(r);
  }
  const cand = layer.source_paths.filter(p => !cur.members.includes(p.id));
  if (cand.length) {
    const ADD = '__add';
    addPropRowSelectTo(panel, 'Add path…', ADD, [ADD, ...cand.map(p => p.id)], v => {
      if (v === ADD) return;
      _wsSetMember(v, cur);                 // (moves it out of another system)
      _syncSystemWebs(); rebuild();
    }, { [ADD]: '— choose a path —', ...Object.fromEntries(cand.map(p => {
      const o = _wsOf(p.id);
      return [p.id, _pathName(p.id) + (o ? ` (from ${_wsLabel(o)})` : '')];
    })) });
  }
  _addSubTitle(panel, 'Construction');
  addPropRowText(panel, 'Name', cur.name || '', v => { cur.name = v.trim() || cur.id; rebuild(); });
  const types = Object.keys(WALL_SYSTEMS);
  addPropRowSelectTo(panel, 'Construction', cur.type, types, v => { _wsSetType(cur, v); _syncSystemWebs(); rebuild(); },
                     Object.fromEntries(types.map(k => [k, WALL_SYSTEMS[k].label])));
  if (cur.type === 'single') {
    _addNote(panel, 'The ordinary single-line wall: one bead along each path. With Physical rules an open ' +
                    'path prints as separated outbound + return lanes (Material / Bead: Return-lane overlap).');
  } else {
    addPropRowNumTo(panel, 'Wall Thickness', cur.thickness ?? 10, v => {
      cur.thickness = Math.max(0.5, v); routeResult = null; scheduleRefresh(); repaint();
    });
    const alignOpts = ['Default', 'Centered', 'Inside', 'Outside'];
    addPropRowSelect(panel, 'Wall Alignment', !cur.align || cur.align === 'auto' ? 'Default' : _alignLabel(cur.align, true),
                     alignOpts, v => { cur.align = v === 'Default' ? 'auto' : ALIGN_UI[v]; rebuild(); });
    _addNote(panel, 'Default: Centered on lines, Inside closed shapes.');
    if (cur.align === 'center') {
      addPropRowCheck(panel, 'Print reference line', !!cur.print_reference, v => { cur.print_reference = v; rebuild(); });
    }
  }
  if (cur.type === 'single') {
    // (no envelope parameters)
  } else if (cur.type === 'hollow') {
    _addNote(panel, 'The inner and outer skins only — no structural web between them.');
  } else if (cur.type === 'skin_web') {
    _addSubTitle(panel, 'Skin + Web');
    _addWebRows(panel, cur, rebuild);
  } else {
    _addSubTitle(panel, WALL_SYSTEMS[cur.type].label);
    _addWallSystemParams(panel, cur);
    const d = document.createElement('div');
    d.className = 'path-type wall-system-report';
    _wsReportEls.push({ el: d, sys: cur.id, owner: 'sys' });
    _fillWallSystemReport(d, { sys: cur.id });
    panel.appendChild(d);
    _addNote(panel, 'No separate skins: the paths fill the Wall Thickness envelope.');
  }
  _addNote(panel, 'Members need not touch. Connected walls still join geometrically (junctions); ' +
                  'a path in no Wall System prints as a single bead.');
  _addButton(panel, `Delete ${_wsLabel(cur)}`, () => {
    layer.wall_systems = layer.wall_systems.filter(s => s !== cur);
    wallSystemPick = null; _syncSystemWebs(); rebuild();
  });
}

// Path Properties: which Wall System builds this path (construction lives
// in the Wall System panel)
function _addWallRows(panel, path) {
  _wsReportEls = _wsReportEls.filter(r => r.owner !== 'path');
  _addSubTitle(panel, 'Wall System');
  const refresh = () => { routeResult = null; scheduleRefresh(); updatePropPanel(); updatePathList(); repaint(); };
  const all = layer.wall_systems || [];
  const s = _wsOf(path.id);
  const NONE = 'none';
  addPropRowSelectTo(panel, 'Wall System', s ? s.id : NONE, [NONE, ...all.map(x => x.id)], v => {
    const t = all.find(x => x.id === v) || null;
    _wsSetMember(path.id, t);
    if (t) wallSystemPick = t.id;
    _syncSystemWebs();
    refresh();
  }, { [NONE]: 'None (single bead)', ...Object.fromEntries(all.map(x => [x.id, _wsLabel(x)])) });
  if (s && s.type === 'single') {
    _addNote(panel, 'Single / Out-and-Back Wall: one bead (with Physical rules, separated outbound + return lanes).');
  } else if (s && s.thickness != null) {
    _addNote(panel, `${s.thickness} in wall · ${s.align && s.align !== 'auto' ? _alignLabel(s.align, path.closed) : _alignLabel('auto', path.closed)}` +
                    ` · ${WALL_SYSTEMS[s.type].label} — set in the Wall System panel.`);
    if (s.type !== 'skin_web') {
      const d = document.createElement('div');
      d.className = 'path-type wall-system-report';
      _wsReportEls.push({ el: d, id: path.id, owner: 'path' });
      _fillWallSystemReport(d, path.id);
      panel.appendChild(d);
    }
  } else if (_legacyWalls() && _effectiveWall(path).wall) {
    _addNote(panel, `${_effectiveWall(path).wall.thickness} in wall (older design) — being converted to a Wall System.`);
  } else {
    _addNote(panel, 'A single bead (with Physical rules, an out-and-back pair when open). ' +
                    'Add it to a Wall System to make it a wall.');
    _addButton(panel, '+ New Wall System with this path', () => { _wsNew([path.id]); _syncSystemWebs(); refresh(); });
  }
}

// (legacy) Network Wall of a network — read only by the migration / old designs
function _netWallFor(ids) {
  return (layer.network_walls || []).find(w => ids.includes(w.path_id)) || null;
}

// WALL NETWORK — geometric CONNECTIVITY only (paths that touch / cross; the
// engine joins their walls at junctions). It owns no construction.
function updateNetworkSection() {
  const sec = document.getElementById('network-section');
  const panel = document.getElementById('network-props');
  const nets = ((networkInfo && networkInfo.source_networks) || []).filter(n => n.sources.length > 1);
  if (!nets.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';
  panel.innerHTML = '';
  for (const n of nets) _addNote(panel, `${n.id}: ${n.sources.map(_pathName).join(', ')}`);
  _addNote(panel, 'Connected geometry (junctions, shared wall envelopes). Construction — thickness, system, web — ' +
                  'is set per Wall System, independently of connectivity.');
}

function _addNetworkPanel(panel, path) {
  const net = _networkOf()[path.id];
  if (!net || net.ids.length < 2) return;
  _addNote(panel, `Connected to ${net.ids.filter(id => id !== path.id).map(_pathName).join(', ')} (wall network ${net.label}).`);
}

function _pathName(id) {
  const p = layer.source_paths.find(s => s.id === id);
  return p ? (p.label || p.id) : id;
}

// ---------------------------------------------------------------------------
// Path identification — hovering / focusing a path name (picker items,
// path list) highlights that path on the canvas; nothing is labelled
// permanently.
// ---------------------------------------------------------------------------

let highlightPathId = null;
const HIGHLIGHT_COLOR = '#ffd84a';

function setHighlight(id) {
  if (highlightPathId === id) return;
  highlightPathId = id;
  repaint();
}

function drawHighlight() {
  const p = highlightPathId && layer.source_paths.find(s => s.id === highlightPathId);
  if (!p || !(p.points || []).length) return;
  const vis = _visibleSourcePolys(p).filter(v => v.pts.length >= 2);
  if (!vis.length) return;
  const cps = vis.map(v => ({ cp: v.pts.map(([x, y]) => worldToCanvas(x, y)), closed: v.closed }));
  ctx.save();
  ctx.globalAlpha = 0.35;
  for (const { cp, closed } of cps) drawPolyline(cp, HIGHLIGHT_COLOR, 9, false, closed);
  ctx.globalAlpha = 1;
  for (const { cp, closed } of cps) drawPolyline(cp, HIGHLIGHT_COLOR, 2.5, false, closed);
  const cp = cps.reduce((a, b) => (b.cp.length > a.cp.length ? b : a), cps[0]).cp;
  const mid = cp[Math.floor(cp.length / 2)];
  const label = p.label || p.id;
  ctx.font = 'bold 11px monospace';
  const w = label.length * 7 + 8;
  ctx.fillStyle = 'rgba(30,25,5,0.9)';
  ctx.fillRect(mid[0] + 8, mid[1] - 20, w, 16);
  ctx.fillStyle = HIGHLIGHT_COLOR;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(label, mid[0] + 12, mid[1] - 12);
  ctx.restore();
}

// A source-path picker: like a select, but hovering or arrow-keying over an
// item highlights that path on the canvas.
function addPathPickerRow(panel, label, currentId, onChange, excludeId, filterFn) {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
  const btn = document.createElement('button');
  btn.className = 'prop-input path-picker';
  btn.textContent = _pathName(currentId) + ' ▾';
  btn.onmouseenter = () => setHighlight(currentId);
  let isOpen = false;
  btn.onmouseleave = () => { if (!isOpen) setHighlight(null); };
  const list = document.createElement('div');
  list.className = 'path-picker-list';
  // Candidates are read when the list OPENS (paths drawn after this panel
  // was built must be selectable too).
  let items = [], active = 0, nodes = [];
  const refreshItems = () => {
    items = layer.source_paths.filter(p => p.id !== excludeId && (!filterFn || filterFn(p)));
    active = Math.max(0, items.findIndex(p => p.id === currentId));
  };
  refreshItems();
  const close = (commit) => {
    if (!isOpen) return;
    isOpen = false;
    if (list.parentNode) list.parentNode.removeChild(list);
    setHighlight(null);
    if (commit && items[active]) onChange(items[active].id);
  };
  const mark = () => nodes.forEach((it, i) => {
    it.className = 'path-picker-item' + (i === active ? ' active' : '') +
                   (items[i].id === currentId ? ' current' : '');
  });
  // Nodes are built once per opening; hovering only moves the 'active'
  // class (rebuilding the node under the pointer could swallow the click).
  const render = () => {
    list.innerHTML = '';
    if (!items.length) {
      const it = document.createElement('div');
      it.className = 'path-picker-item empty';
      it.textContent = 'no valid choice';
      list.appendChild(it);
    }
    nodes = items.map((p, i) => {
      const it = document.createElement('div');
      it.textContent = `${p.label || p.id}  ·  ${(TYPE_NAMES[p.type] || 'Path').toLowerCase()}`;
      it.onmouseenter = () => { active = i; setHighlight(p.id); mark(); };
      it.onmousedown = (ev) => { ev.preventDefault(); active = i; close(true); };
      list.appendChild(it);
      return it;
    });
    mark();
  };
  btn.onclick = () => {
    if (isOpen) { close(false); return; }
    isOpen = true;
    refreshItems();
    render();
    row.appendChild(list);
    setHighlight(items[active] ? items[active].id : null);
    btn.focus();
  };
  btn.onkeydown = (ev) => {
    if (!isOpen) return;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      active = (active + (ev.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      setHighlight(items[active].id); mark(); ev.preventDefault();
    } else if (ev.key === 'Enter') { close(true); ev.preventDefault(); }
    else if (ev.key === 'Escape') { close(false); ev.preventDefault(); }
  };
  btn.onblur = () => close(false);
  row.append(lbl, btn);
  panel.appendChild(row);
  return { row, btn, list, get items() { return items; }, open: () => { if (!isOpen) btn.onclick(); }, close,
           get active() { return active; }, isOpen: () => isOpen };
}

function _addCornerRRow(panel, path) {
  addPropRowNum(panel, 'Corner R', 'corner-r', _cornerR(path), v => {
    path.corner_radius = Math.max(0, v);
    _computePrimitivePoints(path);
    routeResult = null; scheduleRefresh(); repaint();
  });
}

function addPropRowNum(panel, label, fieldKey, value, onChange, unitText = 'in') {
  const row = document.createElement('div');
  row.className = 'prop-row';
  const lbl = document.createElement('span');
  lbl.className = 'prop-label';
  lbl.textContent = label;
  const input = document.createElement('input');
  input.type = 'number';
  input.className = 'prop-input';
  input.id = 'prop-' + fieldKey;
  input.value = typeof value === 'number' ? +value.toFixed(2) : value;
  input.onchange = () => onChange(+input.value);
  const unit = document.createElement('span');
  unit.className = 'prop-unit';
  unit.textContent = unitText;
  row.append(lbl, input, unit);
  panel.appendChild(row);
}

function addPropRowText(panel, label, value, onChange) {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
  const input = document.createElement('input');
  input.type = 'text'; input.className = 'prop-input'; input.value = value;
  input.onchange = () => onChange(input.value);
  row.append(lbl, input); panel.appendChild(row);
}

function addPropRowSelect(panel, label, value, options, onChange) {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
  const sel = document.createElement('select'); sel.className = 'prop-input';
  for (const o of options) {
    const opt = document.createElement('option');
    opt.value = o; opt.textContent = o;
    if (o === value) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.onchange = () => onChange(sel.value);
  row.append(lbl, sel); panel.appendChild(row);
}

function addPropRowCheck(panel, label, value, onChange) {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
  const input = document.createElement('input');
  input.type = 'checkbox'; input.checked = !!value;
  input.onchange = () => onChange(input.checked);
  row.append(lbl, input); panel.appendChild(row);
}

// ---------------------------------------------------------------------------
// Offset treatments UI (direction + positive distance)
// ---------------------------------------------------------------------------

function updateOffsetList() {
  const el = document.getElementById('offset-list');
  el.innerHTML = '';
  for (const ot of layer.offset_treatments) {
    const block = document.createElement('div');
    block.className = 'treatment-block';

    const hdr = document.createElement('div');
    hdr.className = 'treatment-header';
    const nm = document.createElement('span'); nm.className = 'treatment-name'; nm.textContent = 'Extra Offset';
    const rm = document.createElement('button'); rm.className = 'remove-btn'; rm.textContent = '×';
    rm.onclick = () => {
      const removedId = ot.id;
      layer.offset_treatments = layer.offset_treatments.filter(x => x.id !== removedId);
      layer.lattice_instances = layer.lattice_instances.filter(
        li => li.path_a_id !== removedId && li.path_b_id !== removedId
      );
      routeResult = null; scheduleRefresh(); updateOffsetList(); updateInfillList(); repaint();
    };
    hdr.append(nm, rm);
    block.appendChild(hdr);

    const panel = document.createElement('div');
    panel.className = 'prop-panel';

    // Source: a picker that highlights each candidate path on hover
    addPathPickerRow(panel, 'Source', ot.source_path_id, id => {
      ot.source_path_id = id;
      const np = layer.source_paths.find(p => p.id === id);
      const valid = np && np.closed ? ['inside', 'outside'] : ['left', 'right'];
      if (!valid.includes(ot.direction)) ot.direction = valid[0];
      routeResult = null; scheduleRefresh(); updateOffsetList(); repaint();
    });

    // Direction: Inside/Outside for closed, Left/Right for open
    const srcPath = layer.source_paths.find(p => p.id === ot.source_path_id);
    const isClosed = srcPath ? srcPath.closed : true;
    const dirOptions = isClosed ? ['inside', 'outside'] : ['left', 'right'];
    addPropRowSelectTo(panel, 'Direction', ot.direction || 'inside', dirOptions, v => {
      ot.direction = v; routeResult = null; scheduleRefresh(); repaint();
    });

    // Distance — always positive
    addPropRowNumTo(panel, 'Distance', Math.abs(ot.distance != null ? ot.distance : 10),
      v => { ot.distance = Math.max(0.1, v); routeResult = null; scheduleRefresh(); repaint(); }, 'in');

    block.appendChild(panel);
    el.appendChild(block);
  }
}

function addOffset() {
  if (layer.source_paths.length === 0) { setStatus('Add at least one path first.'); return; }
  // Source = the selected path (previously always the FIRST path, which
  // made every new offset land on Rect 1 / Line 1 whatever was selected).
  const src = layer.source_paths.find(p => p.id === selectedId) || layer.source_paths[0];
  const closed = !!src.closed;
  layer.offset_treatments.push({
    id: newId(),
    source_path_id: src.id,
    distance: 10,
    direction: closed ? 'inside' : 'left',
    role: 'inner',
    label: '',
  });
  routeResult = null;
  scheduleRefresh();
  updateOffsetList();
}

// ---------------------------------------------------------------------------
// Infill (wall-region lattice) UI
// ---------------------------------------------------------------------------

// Infill fills the printable WALL REGION of a wall network (or a single
// wall) — all its branches, junctions, holes and openings — as ONE
// coherent field. It is anchored to a source path (the wall it was added
// to); the backend fills every wall-material region bordering that path.
function _infillTargetLabel(f) {
  const nets = _networkOf();
  const p = layer.source_paths.find(s => s.id === f.path_id);
  const name = p ? (p.label || p.id) : f.path_id;
  if (f.kind === 'solid') return `the area of ${name}`;
  const net = nets[f.path_id];
  return net ? `wall network ${net.label} (${net.names.join(', ')})` : `wall of ${name}`;
}

function _infillStatus(f) {
  const info = ((networkInfo && networkInfo.infills) || []).find(i => i.id === f.id);
  if (!info) return '';
  if (info.status === 'ok') return info.regions > 1 ? `${info.regions} wall regions` : '';
  if (info.status === 'shadowed') return 'already filled by another infill';
  if (info.status === 'no wall material')
    return 'no wall material here — give the wall a Wall Thickness (or close the path)';
  return info.status;
}

// The lineage's SHARED lattice (Layer Designs): who shares it, where its
// definition lives, and any jamb that cannot close (reported, never hidden).
function _infillLineage(f) {
  const info = ((networkInfo && networkInfo.infills) || []).find(i => i.id === f.id);
  const lin = info && info.lattice && info.lattice.lineage;
  if (!lin) return null;
  const names = (lin.members || []).map(_designName).join(', ');
  let text = `Shared lattice: ${names} print one vertically registered lattice. ` +
             `Pattern and spacing apply to the whole lineage (defined in ${_designName(lin.owner)}).`;
  let warn = false;
  const un = (lin.jambs && lin.jambs.unresolved) || [];
  if (un.length) {
    warn = true;
    text += ` ${un.length} opening end wall${un.length > 1 ? 's' : ''} could not be crossed locally ` +
            `(${un.map(u => u.why).join('; ')}) — that design's route stays open there.`;
  }
  return { text, warn };
}

function updateInfillList() {
  const el = document.getElementById('infill-list');
  if (!el) return;
  el.innerHTML = '';
  for (const f of (layer.infills || []).filter(x => !x.owner)) {   // (wall webs: Wall System panel)
    const block = document.createElement('div');
    block.className = 'treatment-block';
    const hdr = document.createElement('div');
    hdr.className = 'treatment-header';
    const nm = document.createElement('span'); nm.className = 'treatment-name';
    nm.textContent = (f.kind === 'solid' ? 'Solid Infill' : 'Wall Infill');
    const rm = document.createElement('button'); rm.className = 'remove-btn'; rm.textContent = '×';
    rm.onclick = () => {
      layer.infills = layer.infills.filter(x => x.id !== f.id);
      routeResult = null; scheduleRefresh(); updateInfillList(); repaint();
    };
    hdr.append(nm, rm);
    block.appendChild(hdr);
    const panel = document.createElement('div');
    panel.className = 'prop-panel';
    addPropRowSelectTo(panel, 'Type', f.kind === 'solid' ? 'Solid' : 'Wall', ['Wall', 'Solid'], v => {
      _setInfillKind(f, v === 'Solid' ? 'solid' : 'wall');
      routeResult = null; scheduleRefresh(); updateInfillList(); repaint();
    });
    addPathPickerRow(panel, 'Region', f.path_id, id => {
      f.path_id = id;                // explicit choice (e.g. an inner shape)
      routeResult = null; scheduleRefresh(); updateInfillList(); repaint();
    });
    const target = document.createElement('div');
    target.className = 'path-type';
    target.textContent = 'Fills ' + _infillTargetLabel(f);
    panel.appendChild(target);
    const vtxt = _infillVoidsLabel(f);
    if (vtxt) {
      const v = document.createElement('div');
      v.className = 'path-type';
      v.textContent = vtxt;
      panel.appendChild(v);
    }
    const st = _infillStatus(f);
    if (st) {
      const s = document.createElement('div');
      s.className = 'path-type';
      s.style.color = 'var(--warn)';
      s.textContent = st;
      panel.appendChild(s);
    }
    const lin = _infillLineage(f);
    if (lin) {
      const n = document.createElement('div');
      n.className = 'path-type infill-lineage';
      n.textContent = lin.text;
      if (lin.warn) n.style.color = 'var(--warn)';
      panel.appendChild(n);
    }
    const hostPath = layer.source_paths.find(s => s.id === f.path_id);
    if (f.kind !== 'solid' && hostPath && _wallSystemOf(hostPath) !== 'skin_web') {
      // only Skin + Web walls are filled by a Wall Infill
      _addNote(panel, `This wall uses the ${WALL_SYSTEMS[_wallSystemOf(hostPath)].label} wall system — ` +
                      'Wall Infill applies to Skin + Web walls only (kept, not printed).');
      block.appendChild(panel);
      el.appendChild(block);
      continue;
    }
    const names = _patternsFor(f.kind);
    addPropRowSelectTo(panel, 'Pattern', f.pattern, names, v => {
      f.pattern = v;
      // the new pattern's own parameters (existing values kept)
      for (const prm of _paramsFor(f)) if (f.params[prm.name] == null) f.params[prm.name] = prm.default;
      if (v === 'truss' && f.params.seed == null) f.params.seed = 0;
      routeResult = null; scheduleRefresh(); updateInfillList(); repaint();
    }, PATTERN_LABELS);
    for (const prm of _paramsFor(f)) {
      addPropRowNumTo(panel, prm.label, f.params[prm.name] ?? prm.default, v => {
        let x = Math.max(prm.min ?? -1e9, Math.min(prm.max ?? 1e9, v));
        if (prm.name === 'perimeters') x = Math.round(x);
        f.params[prm.name] = x;
        routeResult = null; scheduleRefresh(); repaint();
      }, prm.unit ?? (prm.name === 'angle' ? '°' : prm.name === 'perimeters' ? '' : 'in'));
    }
    if (f.kind === 'solid') {
      const sact = _solidActual(f);
      if (sact) {
        const d = document.createElement('div');
        d.className = 'path-type';
        d.textContent = sact;
        panel.appendChild(d);
      }
      // Advanced: boundary support bound (Pass 8)
      const sadv = document.createElement('details');
      sadv.className = 'advanced';
      const ssm = document.createElement('summary');
      ssm.textContent = 'Advanced';
      sadv.appendChild(ssm);
      const sp = document.createElement('div');
      addPropRowNumTo(sp, 'Max unsupported', f.params.max_unsupported || 0, v => {
        f.params.max_unsupported = Math.max(0, v);         // 0 = automatic
        routeResult = null; scheduleRefresh(); repaint();
      }, 'in');
      const sh = document.createElement('div');
      sh.className = 'path-type';
      sh.textContent = '0 = off (diagnostic only). A limit bends the nearest passes onto the boundary where needed.';
      sp.appendChild(sh);
      sadv.appendChild(sp);
      panel.appendChild(sadv);
      block.appendChild(panel);
      el.appendChild(block);
      continue;                       // no wall-field phase variation
    }
    const act = _latticeActual(f);
    if (act) {
      const d = document.createElement('div');
      d.className = 'path-type';
      d.textContent = act;
      panel.appendChild(d);
    }
    if (f.pattern === 'truss') {
      // REGENERATE: another valid solution under the SAME parameters — a
      // deterministic solution seed (geometry + parameters + seed reproduce
      // the lattice; undoable like any edit; lineage-wide like the pattern)
      const row = document.createElement('div');
      row.className = 'prop-row truss-regenerate';
      const lbl = document.createElement('span');
      lbl.className = 'prop-label';
      lbl.textContent = `Solution ${(f.params.seed || 0) + 1}`;
      const btn = document.createElement('button');
      btn.className = 'add-btn';
      btn.textContent = 'Regenerate';
      btn.title = 'Pick another valid lattice with the same parameters';
      btn.onclick = () => {
        f.params.seed = (f.params.seed || 0) + 1;
        routeResult = null; scheduleRefresh(); updateInfillList(); repaint();
      };
      row.append(lbl, btn);
      panel.appendChild(row);
      const hint = document.createElement('div');
      hint.className = 'path-type';
      hint.textContent = 'Pitch follows from Brace Angle and the local wall cavity (prototype defaults, not calibrated).';
      panel.appendChild(hint);
      block.appendChild(panel);
      el.appendChild(block);
      continue;                       // no Target Spacing / V1–V2 phase for the truss
    }
    // Advanced: structural bound (target spacing stays the main control)
    const adv = document.createElement('details');
    adv.className = 'advanced';
    const sm = document.createElement('summary');
    sm.textContent = 'Advanced';
    adv.appendChild(sm);
    const advPanel = document.createElement('div');
    addPropRowNumTo(advPanel, 'Max unsupported', f.params.max_unsupported || 0, v => {
      f.params.max_unsupported = Math.max(0, v);           // 0 = automatic
      routeResult = null; scheduleRefresh(); repaint();
    }, 'in');
    const hint = document.createElement('div');
    hint.className = 'path-type';
    hint.textContent = '0 = automatic (1.375 × target). Wins over Target Spacing.';
    advPanel.appendChild(hint);
    adv.appendChild(advPanel);
    panel.appendChild(adv);
    if (!_variationEffective(f)) {
      block.appendChild(panel);
      el.appendChild(block);
      continue;                       // V1 / V2 would do nothing here
    }
    const varLabel = document.createElement('div');
    varLabel.style.cssText = 'color:#555;font-size:10px;margin-top:4px;';
    varLabel.textContent = 'Variation (phase)';
    panel.appendChild(varLabel);
    const varRow = document.createElement('div');
    varRow.className = 'variation-row';
    for (let i = 0; i < 2; i++) {
      const btn = document.createElement('button');
      btn.className = 'var-btn' + (i === (f.variation_index || 0) ? ' active' : '');
      btn.textContent = `V${i + 1}`;
      btn.onclick = () => { f.variation_index = i; routeResult = null; scheduleRefresh(); updateInfillList(); repaint(); };
      varRow.appendChild(btn);
    }
    panel.appendChild(varRow);
    block.appendChild(panel);
    el.appendChild(block);
  }
}

// Add infill to the region of the selected path (else the outermost closed
// boundary, else the first path).
function addInfill() {
  // a WALL SYSTEM member: its web is configured on the system (Skin + Web)
  const ws = selectedId && _wsOf(selectedId);
  if (ws && ws.thickness != null) {
    if (ws.type === 'skin_web' && (!ws.web || ws.web.pattern === 'none'))
      ws.web = { pattern: 'zigzag', params: { spacing: 20 }, variation_index: 0 };
    wallSystemPick = ws.id;
    _syncSystemWebs();
    routeResult = null; scheduleRefresh(); updatePropPanel(); updateInfillList();
    setStatus(ws.type === 'skin_web' ? `The web of ${_wsLabel(ws)} is set in the Wall System panel.`
                                     : `${_wsLabel(ws)} is ${WALL_SYSTEMS[ws.type].label}: no web (change its Construction).`);
    return;
  }
  // REGION: the selected path if the designer picked one (an inner shape
  // too — explicit wins); otherwise the OUTERMOST closed boundary (closed
  // paths nested inside it become voids, by geometry not creation order).
  const p = layer.source_paths.find(s => s.id === selectedId) || _outermostClosed() ||
            layer.source_paths[0];
  if (!p) { setStatus('Add a wall first.'); return; }
  // KIND: a closed single-bead boundary is a SOLID area; a path with wall
  // thickness (or in a wall network) gets WALL infill between its faces.
  const kind = (p.closed && !_effectiveWall(p).wall) ? 'solid' : 'wall';
  const f = { id: newId(), path_id: p.id, kind, pattern: 'zigzag', params: {}, variation_index: 0 };
  _setInfillKind(f, kind);
  layer.infills.push(f);
  routeResult = null;
  scheduleRefresh();
  updateInfillList();
}

// Helpers for building treatment panels
function addPropRowNumTo(panel, label, value, onChange, unit = 'in') {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
  const input = document.createElement('input');
  input.type = 'number'; input.className = 'prop-input';
  input.value = typeof value === 'number' ? +value.toFixed(2) : value;
  input.onchange = () => onChange(+input.value);
  row.append(lbl, input);
  if (unit) {
    const unitEl = document.createElement('span'); unitEl.className = 'prop-unit'; unitEl.textContent = unit;
    row.append(unitEl);
  }
  panel.appendChild(row);
}

function addPropRowSelectTo(panel, label, value, options, onChange, labels = null) {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
  const sel = document.createElement('select'); sel.className = 'prop-input';
  for (const o of options) {
    const opt = document.createElement('option');
    opt.value = o; opt.textContent = (labels && labels[o]) || o;
    if (o === value) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.onchange = () => onChange(sel.value);
  row.append(lbl, sel); panel.appendChild(row);
}

// ---------------------------------------------------------------------------
// Live derived-path refresh + auto-routing
// ---------------------------------------------------------------------------

// Any wall (own or network) using a non-default Wall System?
function _anyWallSystem() {
  return (layer.wall_systems || []).some(s => s.type !== 'skin_web') ||
         layer.source_paths.some(p => p.wall && p.wall.thickness > 0 && _wallSystemType(p.wall) !== 'skin_web') ||
         (layer.network_walls || []).some(w => _wallSystemType(w) !== 'skin_web');
}

// REQUEST COALESCING: at most one resolve in flight; edits arriving meanwhile
// collapse into ONE follow-up request with the latest state (a slow resolve
// never queues a backlog of stale ones).
let _reqBusy = false, _reqNext = null;
async function _coalesced(fn) {
  if (_reqBusy) { _reqNext = fn; return; }
  _reqBusy = true;
  try { await fn(); } finally {
    _reqBusy = false;
    if (_reqNext) { const f = _reqNext; _reqNext = null; _coalesced(f); }
  }
}

function scheduleRefresh() {
  _syncRelations();
  _syncSystemWebs();
  _maybeMigrateWallSystems();
  historyCheckpoint();
  clearTimeout(_refreshTimer);
  if (layer.source_paths.length === 0) {
    derivedPaths = [];
    networkInfo = null;
    routeResult = null;
    printable = [];
    repaint();
    return;
  }
  const ws = _anyWallSystem();
  if (showToolpath) {
    // Auto-route: also updates derivedPaths
    _refreshTimer = setTimeout(() => _coalesced(runRoute), ws ? 300 : 200);
  } else {
    // ≥ 2 paths may form a wall network (junction markers, trimmed faces)
    const hasDerived = layer.offset_treatments.length > 0 || layer.lattice_instances.length > 0 ||
                       (layer.infills || []).length > 0 ||
                       (layer.openings || []).length > 0 || (layer.trims || []).length > 0 ||
                       layer.source_paths.length > 1 || ws;
    if (hasDerived || showBeads) {           // beads need the resolved printable set
      _refreshTimer = setTimeout(() => _coalesced(fetchEffectivePaths), ws ? 250 : 120);
    } else {
      derivedPaths = [];
      repaint();
    }
  }
}

async function fetchEffectivePaths() {
  if (layer.source_paths.length === 0) {
    derivedPaths = [];
    repaint();
    return;
  }
  try {
    const sent = _histState();
    const res = await fetch('/api/effective_paths', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPayload()),
    });
    const data = await res.json();
    if (data.paths) {
      const sourceIds = new Set(layer.source_paths.map(p => p.id));
      derivedPaths = data.paths.filter(p => !sourceIds.has(p.id));
      networkInfo = data.network || null;
      printable = data.printable || [];
      _netState = sent;
      _applyDerivedSources();
      updatePathList();
      updateInfillList();
      _afterNetworkUpdate();
    }
  } catch (e) {
    console.warn('fetchEffectivePaths failed', e);
  }
  repaint();
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

async function runRoute() {
  if (layer.source_paths.length === 0) {
    routeResult = null;
    derivedPaths = [];
    document.getElementById('metrics-section').style.display = 'none';
    repaint();
    return;
  }
  setStatus('Routing…');
  try {
    const sent = _histState();
    const res = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPayload()),
    });
    const data = await res.json();
    if (data.error) {
      setStatus('Routing error — see console.');
      console.error(data.error);
      return;
    }
    routeResult = data;
    const sourceIds = new Set(layer.source_paths.map(p => p.id));
    derivedPaths = (data.layer.paths || []).filter(p => !sourceIds.has(p.id));
    networkInfo = data.network || null;
    printable = data.printable || [];
    _netState = sent;
    _applyDerivedSources();
    updatePathList();
    updateInfillList();
    _afterNetworkUpdate();
    _setupPlayback(data.moves);
    _showTransport(true);
    updateMetrics(data);
    document.getElementById('metrics-section').style.display = '';
    repaint();
  } catch (e) {
    setStatus('Routing failed: ' + e.message);
  }
}

function buildPayload() {
  return {
    id: layer.id,
    label: layer.label,
    source_paths: layer.source_paths.map(p => ({ ...p })),
    offset_treatments: layer.offset_treatments.map(ot => {
      // Convert direction + positive distance → signed distance for backend
      const dir = ot.direction || 'inside';
      const sign = (dir === 'inside' || dir === 'left') ? 1 : -1;
      return {
        id: ot.id,
        source_path_id: ot.source_path_id,
        distance: sign * Math.abs(ot.distance != null ? ot.distance : 10),
        role: ot.role,
        label: ot.label || '',
      };
    }),
    lattice_instances: layer.lattice_instances.map(li => ({ ...li })),
    infills: (layer.infills || []).map(f => ({ ...f, params: { ...f.params } })),
    trims: (layer.trims || []).map(t => ({ ...t, start: [...t.start], end: [...t.end], inside: { ...t.inside } })),
    material: { ...(layer.material || { bead_width: BEAD_DEFAULT }) },
    route_origins: (layer.route_origins || []).map(o => ({ ...o })),
    junction_style: layer.junction_style || 'miter',
    junction_radius: layer.junction_radius || 0,
    junction_overrides: (layer.junction_overrides || []).map(o => ({ ...o })),
    network_walls: (layer.network_walls || []).map(w => ({ ...w })),
    wall_systems: (layer.wall_systems || []).map(w => ({ ...w, params: { ...(w.params || {}) },
                                                          members: [...w.members], web: { ...(w.web || {}) } })),
    wall_relations: (layer.wall_relations || []).map(w => ({ ...w })),
    return_paths: layer.return_paths !== false,
    prefer_closed: layer.prefer_closed !== false,
    constraints: layer.constraints,
    corner_radius: layer.corner_radius || 0,
    cap_style: layer.cap_style || 'flat',
    cap_corner_radius: layer.cap_corner_radius || 0,
    openings: (layer.openings || []).map(o => ({ ...o })),
    region_overrides: (layer.region_overrides || []).map(r => ({ ...r })),
    ..._lineagePayload(),
  };
}

// With several Layer Designs the backend resolves this design's wall lattice
// through the lineage's SHARED scaffold (layer_design.py) — the same lattice
// the Assembly prints. One design: nothing is sent (unchanged behaviour).
function _lineagePayload() {
  if (typeof project === 'undefined' || !project || project.designs.length < 2) return {};
  return { lineage: { designs: _designsPayload(), id: project.active,
                      document: JSON.parse(JSON.stringify(layer)) } };
}

function updateMetrics(data) {
  const m = data.metrics;
  const g = data.graph;

  document.getElementById('m-runs').textContent = m.print_runs;

  const tEl = document.getElementById('m-travel');
  tEl.textContent = m.travel_moves;
  tEl.className = 'metric-value ' + (m.travel_moves === 0 ? 'good' : 'warn');

  document.getElementById('m-tdist').textContent = m.travel_distance.toFixed(1) + ' in';

  const pEl = document.getElementById('m-pct');
  pEl.textContent = m.pct_printing + '%';
  pEl.className = 'metric-value ' + (m.pct_printing >= 95 ? 'good' : m.pct_printing >= 80 ? 'warn' : 'bad');

  document.getElementById('m-pdist').textContent = m.print_distance.toFixed(1) + ' in';
  document.getElementById('m-rdist').textContent = (m.retrace_distance || 0).toFixed(1) + ' in';
  const cl = data.closure;
  const cEl = document.getElementById('m-closed');
  if (cEl) {
    cEl.textContent = cl && cl.physical ? `${cl.closed} / ${cl.components}` : '—';
    cEl.className = 'metric-value' + (cl && cl.physical ? (cl.open.length ? ' bad' : ' good') : '');
    cEl.title = cl && cl.open.length ? `${cl.open.length} component(s) cannot close: their geometry has odd ends (shown as travel)` : '';
  }

  // Routing quality badge
  const badgeEl = document.getElementById('euler-badge-container');
  let badge, cls, title;
  if (g.component_count > 1) {
    badge = `${g.component_count} disconnected sections`;
    cls = 'multi';
    title = 'Travel moves required between sections';
  } else if (g.odd_degree_nodes === 0) {
    badge = 'Single continuous loop';
    cls = 'circuit';
    title = 'All geometry connected — prints as one continuous loop with no travel';
  } else if (g.odd_degree_nodes === 2) {
    badge = 'Single continuous path';
    cls = 'path';
    title = 'Prints as one continuous path — one start, one end, no travel moves';
  } else if (!(m.retrace_distance > 0) && m.travel_moves > 0) {
    // one connected section whose leftover odd ends (e.g. solid infill
    // split by a void) are joined by a short travel instead of a retrace
    badge = `Connected, ${m.travel_moves} short hop${m.travel_moves === 1 ? '' : 's'}`;
    cls = 'multi';
    title = `One connected section with ${g.odd_degree_nodes} odd junctions: joined by ` +
            `${m.travel_moves} short travel move(s) rather than printing anything twice`;
  } else {
    badge = 'Continuous, with retrace';
    cls = 'augmented';
    title = `One connected section with ${g.odd_degree_nodes} odd junctions: no single ` +
            `pass covers every edge, so ${(m.retrace_distance || 0).toFixed(1)} in is ` +
            `retraced (printed again) instead of travelling`;
  }
  badgeEl.innerHTML = `<span class="euler-badge ${cls}" title="${title}">${badge}</span>`;

  const runStr = `${m.print_runs} run${m.print_runs === 1 ? '' : 's'}`;
  const travelStr = m.travel_moves === 0 ? 'no travel' : `${m.travel_moves} travel`;
  const nets = ((data.network && data.network.components) || []);
  const netStr = nets.length
    ? ` · ${nets.map((c, i) => `wall network N${i + 1}: ${c.sources.length} walls`).join(', ')}`
    : '';
  const ends = data.route_ends;
  const loopStr = ends ? (ends.closed ? ' · closed loop (start = end)' : ' · open route (layers can alternate)') : '';
  const plan = data.network && data.network.route_plan;
  const retStr = plan && plan.repaired_pairs
    ? ` · ${plan.repaired_pairs} local infill edit${plan.repaired_pairs === 1 ? '' : 's'} for continuity` +
      (plan.left_to_retrace ? ` · ${plan.left_to_retrace} junction(s) left to retrace` : '')
    : '';
  setStatus(`${runStr}, ${travelStr}, ${m.pct_printing}% printing${loopStr}${retStr}${netStr}`);
}

// ---------------------------------------------------------------------------
// Dimensions overlay — purely visual, no effect on geometry or routing
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Material / Bead — the PHYSICAL mud bead deposited around each printable
// centerline (material.py). Bead Width is a material property of the layer
// (stored, undoable, sent to the backend for future geometry rules such as
// Contact Overlap); for now it is VISUAL ONLY. The footprint is the
// centerline swept by a disk of the bead width: a strip of half-width w/2
// with round ends and round joins (a straight open line is a capsule).
// Only the backend's resolved PRINTABLE centerlines (the router's strands)
// get a bead; all beads are drawn into one offscreen layer and composited
// once, so overlapping beads union with no seams.
// ---------------------------------------------------------------------------
const BEAD_DEFAULT = 3.0;
const BEAD_STEP = 0.25;
const BEAD_CAP = 'round';
const BEAD_JOIN = 'round';
const BEAD_COLOR = '#4a9eff';      // the print colour: the bead IS the printed line (opaque, once)
const BEAD_ALPHA = 1.0;
let _beadCanvas = null;

function _beadWidth() {
  const w = layer.material && +layer.material.bead_width;
  return w > 0 ? w : BEAD_DEFAULT;
}

// The strokes that make up the bead footprint, in canvas pixels.
function _beadStrokes() {
  const scale = _viewScale();                       // px per inch (zoom included)
  const width = _beadWidth() * scale;
  return (printable || []).filter(c => (c.pts || []).length >= 2 && !_staleStrand(c.id)).map(c => ({
    id: c.id, closed: !!c.closed, width,
    pts: c.pts.map(([x, y]) => worldToCanvas(x, y)),
  }));
}

// strands of the path being dragged are stale until the refresh: not drawn
function _staleStrand(id) {
  const d = _draggedPathId();
  return !!d && (id === d || /^[.~_]/.test(id.slice(d.length)) && id.startsWith(d));
}

// Beads OFF, no toolpath shown: each printable centreline = ONE thin blue line
function drawPrintableLines() {
  for (const c of printable || []) {
    if ((c.pts || []).length < 2 || _staleStrand(c.id)) continue;
    drawPolyline(c.pts.map(([x, y]) => worldToCanvas(x, y)), MOVE_COLORS.print, 2.0, false, !!c.closed);
  }
}

function drawBeads() {
  if (!showBeads) return;
  const strokes = _beadStrokes();
  if (!strokes.length) return;
  if (!_beadCanvas) _beadCanvas = document.createElement('canvas');
  _beadCanvas.width = canvas.width;
  _beadCanvas.height = canvas.height;
  const b = _beadCanvas.getContext('2d');
  b.clearRect(0, 0, _beadCanvas.width, _beadCanvas.height);
  b.strokeStyle = BEAD_COLOR;                      // opaque: overlaps union
  b.lineCap = BEAD_CAP;
  b.lineJoin = BEAD_JOIN;
  for (const st of strokes) {
    b.lineWidth = st.width;
    b.beginPath();
    b.moveTo(st.pts[0][0], st.pts[0][1]);
    for (const [x, y] of st.pts.slice(1)) b.lineTo(x, y);
    if (st.closed) b.closePath();
    b.stroke();
  }
  ctx.save();
  ctx.globalAlpha = BEAD_ALPHA;    // opaque: overlaps never darken (one union)
  ctx.drawImage(_beadCanvas, 0, 0);
  ctx.restore();
}

function toggleBeads(on) {
  showBeads = on === undefined ? !showBeads : !!on;
  document.getElementById('btn-beads').classList.toggle('toggle-on', showBeads);
  const cb = document.getElementById('bead-show');
  if (cb) cb.checked = showBeads;
  if (showBeads && layer.source_paths.length && !printable.length) scheduleRefresh();
  repaint();
}

function onBeadWidthChange() {
  const el = document.getElementById('bead-width');
  const w = setProjectBeadWidth(el.value);
  el.value = w.toFixed(2);
}

// BEAD WIDTH is ONE project-wide MATERIAL value (2026-10-06): it lives in the
// lineage ROOT's document (Base's `material.bead_width`); every Layer Design
// inherits it and none stores its own (the backend moves a derived design's
// edit to the root: /api/layer_designs/delta → material_moved). The Designer's
// Material panel and the Assembly's Physical support both read and write it
// through these two functions (window.Designer.beadWidth / setBeadWidth).
function _rootIsActive() {
  const d = project.designs.find(x => x.id === project.active);
  return !d || d.parent == null;
}

function projectBeadWidth() {
  const m = _rootIsActive() ? layer.material : (project.baseDoc || {}).material;
  const w = m && +m.bead_width;
  return w > 0 ? w : BEAD_DEFAULT;
}

// set it (snapped to the bead step); invalidates the bead rendering and, with
// physical rules on, every design's geometry (one Designer undo step)
function setProjectBeadWidth(v) {
  const x = parseFloat(v);
  const w = Math.max(BEAD_STEP, Math.round((Number.isFinite(x) ? x : projectBeadWidth()) / BEAD_STEP) * BEAD_STEP);
  if (!_rootIsActive() && project.baseDoc)
    project.baseDoc.material = { ...(project.baseDoc.material || {}), bead_width: w };
  layer.material = { ...(layer.material || {}), bead_width: w };      // the live view (inherits it)
  _applyMaterial();
  return w;
}

// PHYSICAL RULES (material.py): Contact Overlap and Return-Lane Overlap are
// two DISTINCT physical operations (centreline separation W − O at a side
// contact / W − R beside a return lane); both 0 ≤ overlap ≤ W, the return
// lane < W (R = W would be an exact retrace). With physical rules on they
// shape generated geometry, so every change regenerates.
const RETURN_MIN = BEAD_STEP;

function _material() {
  const m = layer.material || {};
  return { bead_width: _beadWidth(),
           contact_overlap: m.contact_overlap ?? 0.75,
           return_overlap: m.return_overlap ?? 0.75,
           physical: m.physical !== false };
}

function onMaterialChange(field) {
  const m = _material();
  if (field === 'physical') {
    m.physical = !!document.getElementById('phys-rules').checked;
  } else {
    const el = document.getElementById(field === 'contact_overlap' ? 'contact-overlap' : 'return-overlap');
    const v = parseFloat(el.value);
    m[field] = Number.isFinite(v) ? Math.round(v / BEAD_STEP) * BEAD_STEP : m[field];
  }
  layer.material = m;
  _applyMaterial();
}

// clamp the overlaps to the bead width (visibly), sync the controls, regenerate
function _applyMaterial() {
  const m = _material();
  const notes = [];
  const cMax = m.bead_width, rMax = m.bead_width - RETURN_MIN;
  if (m.contact_overlap > cMax) { m.contact_overlap = cMax; notes.push(`Contact overlap reduced to ${cMax.toFixed(2)} in (≤ bead width)`); }
  if (m.contact_overlap < 0) m.contact_overlap = 0;
  if (m.return_overlap > rMax) { m.return_overlap = rMax; notes.push(`Return-lane overlap reduced to ${rMax.toFixed(2)} in (a full overlap would be an exact retrace)`); }
  if (m.return_overlap < 0) m.return_overlap = 0;
  layer.material = m;
  _syncMaterialControls();
  if (notes.length) setStatus(notes.join(' · '));
  if (m.physical) { routeResult = null; scheduleRefresh(); }   // geometry depends on it
  else { historyCheckpoint(); updateUndoButtons(); }           // legacy: visual only
  repaint();
}

function _syncMaterialControls() {
  const m = _material();
  const set = (id, prop, v) => { const el = document.getElementById(id); if (el) el[prop] = v; };
  set('bead-width', 'value', m.bead_width.toFixed(2));
  set('contact-overlap', 'value', m.contact_overlap.toFixed(2));
  set('return-overlap', 'value', m.return_overlap.toFixed(2));
  set('phys-rules', 'checked', m.physical);
  const W = m.bead_width;
  const rs = Math.max(RETURN_MIN, W - m.return_overlap);
  set('material-derived', 'textContent', m.physical
    ? `Contact: centrelines ${(W - m.contact_overlap).toFixed(2)} in apart · return lane: ${rs.toFixed(2)} in apart ` +
      `(single-line wall ≈ ${(W + rs).toFixed(2)} in wide, printed out and back). No pass prints over another.`
    : 'Physical rules off: zero-width centrelines (beads are visual only).');
}

function toggleDimensions() {
  showDimensions = !showDimensions;
  document.getElementById('btn-dims').classList.toggle('toggle-on', showDimensions);
  repaint();
}

function _dimLabel(cx, cy, text) {
  const w = text.length * 6 + 10;
  const h = 14;
  ctx.fillStyle = 'rgba(30,25,10,0.82)';
  ctx.fillRect(cx - w / 2, cy - h / 2, w, h);
  ctx.fillStyle = '#e8cc55';
  ctx.font = '10px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, cx, cy);
}

function _pathArcLength(pts, closed) {
  let len = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    len += Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
  }
  if (closed && pts.length >= 2) {
    len += Math.hypot(pts[0][0] - pts[pts.length - 1][0],
                      pts[0][1] - pts[pts.length - 1][1]);
  }
  return len;
}

function drawDimensions() {
  ctx.save();

  // Source paths — type-specific labels
  for (const p of layer.source_paths) {
    if (!p.visible) continue;
    _drawSourceDim(p);
  }

  // Derived offset paths (not lattice, caps, or opening-cut wall pieces)
  for (const dp of derivedPaths) {
    if (dp.role === 'lattice' || dp.role === 'cap') continue;
    if (String(dp.id).includes('~')) continue;
    _drawDerivedDim(dp);
  }

  drawOpeningDims();

  ctx.restore();
}

function _drawSourceDim(p) {
  switch (p.type) {
    case 'LinePath': {
      const dx = p.end[0] - p.start[0], dy = p.end[1] - p.start[1];
      const len = Math.hypot(dx, dy);
      const [mx, my] = worldToCanvas((p.start[0] + p.end[0]) / 2,
                                     (p.start[1] + p.end[1]) / 2);
      _dimLabel(mx, my - 14, `${len.toFixed(1)} in`);
      break;
    }
    case 'QuadBezierPath': {
      const pts = p.points || [];
      if (pts.length < 2) break;
      // Arc length — label at bezier midpoint t=0.5
      const len = _pathArcLength(pts, false);
      const mmx = 0.25 * p.start[0] + 0.5 * p.control[0] + 0.25 * p.end[0];
      const mmy = 0.25 * p.start[1] + 0.5 * p.control[1] + 0.25 * p.end[1];
      const [lcx, lcy] = worldToCanvas(mmx, mmy);
      _dimLabel(lcx, lcy - 14, `${len.toFixed(1)} in`);
      // Chord — faint dotted line from start to end + chord length label
      const [sx, sy] = worldToCanvas(p.start[0], p.start[1]);
      const [ex, ey] = worldToCanvas(p.end[0],   p.end[1]);
      const chordLen = Math.hypot(p.end[0] - p.start[0], p.end[1] - p.start[1]);
      ctx.save();
      ctx.strokeStyle = 'rgba(232,204,85,0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
      _dimLabel((sx + ex) / 2, (sy + ey) / 2 + 14, `chord ${chordLen.toFixed(1)} in`);
      break;
    }
    case 'CirclePath': {
      const [cx, cy] = worldToCanvas(p.cx, p.cy + p.radius * 0.6);
      _dimLabel(cx, cy, `R ${p.radius.toFixed(1)} in`);
      break;
    }
    case 'EllipsePath': {
      const [rx_cx, rx_cy] = worldToCanvas(p.cx + p.rx * 0.6, p.cy);
      const [ry_cx, ry_cy] = worldToCanvas(p.cx, p.cy + p.ry * 0.6);
      _dimLabel(rx_cx, rx_cy - 12, `Rx ${p.rx.toFixed(1)} in`);
      _dimLabel(ry_cx - 28, ry_cy, `Ry ${p.ry.toFixed(1)} in`);
      break;
    }
    case 'RectanglePath': {
      const [x0, y0] = worldToCanvas(p.x, p.y + p.h);
      const [x1, y1] = worldToCanvas(p.x + p.w, p.y + p.h);
      const [xr0, yr0] = worldToCanvas(p.x + p.w, p.y);
      // Width label below bottom edge
      _dimLabel((x0 + x1) / 2, Math.max(y0, y1) + 14, `${p.w.toFixed(1)} in`);
      // Height label right of right edge
      const [xr1, yr1] = worldToCanvas(p.x + p.w, p.y + p.h);
      _dimLabel(Math.max(xr0, xr1) + 28, (yr0 + yr1) / 2, `${p.h.toFixed(1)} in`);
      break;
    }
    default: {
      // ExplicitPath: arc length at centroid
      const pts = p.points || [];
      if (pts.length < 2) break;
      const len = _pathArcLength(pts, p.closed);
      const cx = pts.reduce((s, q) => s + q[0], 0) / pts.length;
      const cy = pts.reduce((s, q) => s + q[1], 0) / pts.length;
      const [scx, scy] = worldToCanvas(cx, cy);
      _dimLabel(scx, scy - 12, `~${len.toFixed(1)} in`);
    }
  }
}

function _drawDerivedDim(dp) {
  const pts = dp.points || [];
  if (pts.length < 2) return;

  // Try to infer source type for better label
  const ot = layer.offset_treatments.find(x => x.id === dp.id);
  const srcPath = ot ? layer.source_paths.find(p => p.id === ot.source_path_id) : null;

  if (srcPath && srcPath.type === 'CirclePath') {
    // Estimate radius from arc length
    const arcLen = _pathArcLength(pts, dp.closed);
    const estR = arcLen / (2 * Math.PI);
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    const [scx, scy] = worldToCanvas(cx, cy);
    _dimLabel(scx, scy - 12, `R~${estR.toFixed(1)} in`);
    return;
  }

  if (srcPath && srcPath.type === 'RectanglePath') {
    // Derived offset rect — compute bounding box from 4 corner points
    const xs = pts.map(p => p[0]);
    const ys = pts.map(p => p[1]);
    const w = Math.max(...xs) - Math.min(...xs);
    const h = Math.max(...ys) - Math.min(...ys);
    const [bx0, by0] = worldToCanvas(Math.min(...xs), Math.min(...ys) + h);
    const [bx1, by1] = worldToCanvas(Math.max(...xs), Math.min(...ys) + h);
    const [brx, bry] = worldToCanvas(Math.max(...xs), Math.min(...ys));
    const [brx2, bry2] = worldToCanvas(Math.max(...xs), Math.min(...ys) + h);
    _dimLabel((bx0 + bx1) / 2, Math.max(by0, by1) + 14, `${w.toFixed(1)} in`);
    _dimLabel(Math.max(brx, brx2) + 28, (bry + bry2) / 2, `${h.toFixed(1)} in`);
    return;
  }

  // Default: arc length
  const arcLen = _pathArcLength(pts, dp.closed);
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const [scx, scy] = worldToCanvas(cx, cy);
  _dimLabel(scx, scy - 12, `~${arcLen.toFixed(1)} in`);
}

// ---------------------------------------------------------------------------
// Toolpath toggles
// ---------------------------------------------------------------------------

function toggleToolpath() {
  showToolpath = !showToolpath;
  const btn = document.getElementById('btn-toolpath');
  btn.textContent = showToolpath ? 'Toolpath ON' : 'Toolpath OFF';
  btn.classList.toggle('toggle-on', showToolpath);
  document.getElementById('btn-arrows').disabled = !showToolpath;
  if (showToolpath) {
    if (layer.source_paths.length > 0) runRoute();
    else repaint();
  } else {
    if (_playbackAF) { cancelAnimationFrame(_playbackAF); _playbackAF = null; }
    playbackPlaying = false;
    _updatePlayBtn();
    _showTransport(false);
    routeResult = null;
    document.getElementById('metrics-section').style.display = 'none';
    if (moveStartMode) toggleMoveStart(false);
    repaint();
  }
}

function toggleArrows() {
  showArrows = !showArrows;
  document.getElementById('btn-arrows').classList.toggle('toggle-on', showArrows);
  repaint();
}

// ---------------------------------------------------------------------------
// Routing overrides
// ---------------------------------------------------------------------------

function onOverrideChange() {
  layer.constraints.reverse_direction = document.getElementById('override-reverse').checked;
  const rp = document.getElementById('route-returns');
  const pc = document.getElementById('route-closed');
  if (rp) layer.return_paths = rp.checked;
  if (pc) layer.prefer_closed = pc.checked;
  routeResult = null;
  scheduleRefresh();
}

function onWallGeometryChange() {
  const csEl = document.getElementById('wg-cap-style');
  const ccrEl = document.getElementById('wg-cap-corner-radius');
  const jsEl = document.getElementById('wg-junction-style');
  const jrEl = document.getElementById('wg-junction-radius');
  if (jsEl) layer.junction_style = jsEl.value || 'miter';
  if (jrEl) layer.junction_radius = Math.max(0, parseFloat(jrEl.value) || 0);
  const jrow = document.getElementById('wg-junction-radius-row');
  if (jrow) jrow.style.display = layer.junction_style === 'round' ? '' : 'none';
  if (csEl) layer.cap_style = csEl.value || 'flat';
  if (ccrEl) layer.cap_corner_radius = Math.max(0, parseFloat(ccrEl.value) || 0);
  const row = document.getElementById('wg-cap-radius-row');
  if (row) row.style.display = (layer.cap_style === 'rounded_corners') ? '' : 'none';
  // Recompute all path.points so canvas rendering matches new rounding
  for (const p of layer.source_paths) _computePrimitivePoints(p);
  routeResult = null;
  scheduleRefresh();
  repaint();
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function deletePath(id) {
  // Parametric insets of this path DETACH (keep their current shape as an
  // ordinary closed path) — deleting a parent never silently deletes the
  // geometry derived from it. Undo restores the relationship.
  for (const c of layer.source_paths)
    if (c.type === 'InsetPath' && c.parent_id === id) _detachInset(c);
  layer.wall_relations = (layer.wall_relations || []).filter(r => r.outer_id !== id && r.inner_id !== id);
  layer.source_paths = layer.source_paths.filter(p => p.id !== id);
  layer.offset_treatments = layer.offset_treatments.filter(ot => ot.source_path_id !== id);
  layer.lattice_instances = layer.lattice_instances.filter(
    li => li.path_a_id !== id && li.path_b_id !== id
  );
  layer.openings = (layer.openings || []).filter(o => o.source_path_id !== id);
  // its own trims go with it; trims of OTHER paths bounded by it are kept
  // (they become unresolved and suppress nothing — Undo brings them back)
  layer.trims = (layer.trims || []).filter(t => t.source_path_id !== id);
  layer.region_overrides = (layer.region_overrides || []).filter(r => r.path_id !== id);
  layer.infills = (layer.infills || []).filter(f => f.path_id !== id);
  // A network wall anchored to the deleted path moves to another member.
  const net = _networkOf()[id];
  for (const w of layer.network_walls || []) {
    if (w.path_id !== id) continue;
    const other = net && net.ids.find(x => x !== id && layer.source_paths.some(p => p.id === x));
    w.path_id = other || null;
  }
  layer.network_walls = (layer.network_walls || []).filter(w => w.path_id);
  for (const s of layer.wall_systems || []) s.members = s.members.filter(m => m !== id);
  _syncSystemWebs();
  selectedId = null;
  selectedOpeningId = null;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  updateHint();
  repaint();
}

function clearAll() {
  layer.source_paths = [];
  layer.offset_treatments = [];
  layer.lattice_instances = [];
  layer.openings = [];
  layer.trims = [];
  trimHover = null;
  layer.route_origins = [];
  originDrag = null;
  layer.region_overrides = [];
  layer.infills = [];
  layer.network_walls = [];
  layer.wall_systems = [];
  layer.wall_relations = [];
  layer.return_paths = true;
  layer.prefer_closed = true;
  for (const [elId, v] of [['route-returns', true], ['route-closed', true]]) {
    const el = document.getElementById(elId);
    if (el) el.checked = v;
  }
  layer.junction_overrides = [];
  layer.junction_style = 'miter';
  layer.junction_radius = 2;
  selectedJunction = null;
  const jsEl = document.getElementById('wg-junction-style');
  const jrEl = document.getElementById('wg-junction-radius');
  const jrow = document.getElementById('wg-junction-radius-row');
  if (jsEl) jsEl.value = 'miter';
  if (jrEl) jrEl.value = 2;
  if (jrow) jrow.style.display = 'none';
  networkInfo = null;
  snapHint = null;
  selectedOpeningId = null;
  openingDrag = null;
  layer.constraints = { start_path_id: null, start_t: null,
                         reverse_direction: false, component_order: null };
  layer.corner_radius = 0;
  layer.cap_style = 'flat';
  layer.cap_corner_radius = 0;
  const csEl = document.getElementById('wg-cap-style');
  const ccrEl = document.getElementById('wg-cap-corner-radius');
  const ccrRow = document.getElementById('wg-cap-radius-row');
  if (csEl) csEl.value = 'flat';
  if (ccrEl) ccrEl.value = 0;
  if (ccrRow) ccrRow.style.display = 'none';
  selectedId = null;
  routeResult = null;
  derivedPaths = [];
  printable = [];
  drawPts = [];
  if (_playbackAF) { cancelAnimationFrame(_playbackAF); _playbackAF = null; }
  playbackPlaying = false;
  playbackPos = 0.0;
  _routeCumDists = null;
  _routeTotalDist = 0.0;
  _showTransport(false);
  _updatePlayBtn();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  updateHint();
  document.getElementById('metrics-section').style.display = 'none';
  historyCheckpoint();          // Clear All is undoable
  repaint();
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

// ---------------------------------------------------------------------------
// Undo / redo — SNAPSHOT history of the editable design state
//
// The history is a list of serialised `layer` states (every source path,
// wall spec, offset, infill, opening, inset relationship, junction and
// routing setting) plus the selection at that moment. Every edit funnels
// through scheduleRefresh() → historyCheckpoint(): if the serialised layer
// differs from the last committed state, that state becomes an undo step
// and the redo stack is cleared. No checkpoint is taken while a pointer
// gesture is in progress (handle / body / rotation / opening drag), so a
// continuous drag is ONE step. Undo restores the whole snapshot, so
// everything DERIVED from geometry — snap connections, wall networks,
// junctions, inset children, infill regions / voids — comes back with it;
// there are no per-operation inverse functions to keep in sync.
// Memory safeguard: oldest steps are dropped beyond HISTORY_MAX_STEPS or
// HISTORY_MAX_BYTES of stored snapshots.
// ---------------------------------------------------------------------------
const HISTORY_MAX_STEPS = 2000;
const HISTORY_MAX_BYTES = 64 * 1024 * 1024;
const _hist = { undo: [], redo: [], current: null, bytes: 0 };

function _histState() { return JSON.stringify(layer); }
function _gestureActive() { return !!(dragging || bodyDragging || openingDrag || rotateDrag || originDrag); }
function _histSize(e) { return e.state.length * 2; }

function historyCheckpoint() {
  if (_gestureActive()) return false;
  const s = _histState();
  if (!_hist.current) { _hist.current = { state: s, sel: selectedId }; return false; }
  if (s === _hist.current.state) { _hist.current.sel = selectedId; return false; }
  _hist.undo.push(_hist.current);
  _hist.bytes += _histSize(_hist.current);
  for (const r of _hist.redo) _hist.bytes -= _histSize(r);
  _hist.redo = [];                         // a new edit after undo clears redo
  _hist.current = { state: s, sel: selectedId };
  while (_hist.undo.length > HISTORY_MAX_STEPS ||
         (_hist.bytes > HISTORY_MAX_BYTES && _hist.undo.length > 1))
    _hist.bytes -= _histSize(_hist.undo.shift());
  updateUndoButtons();
  return true;
}

function canUndo() { return _hist.undo.length > 0 || (!!_hist.current && _histState() !== _hist.current.state); }
function canRedo() { return _hist.redo.length > 0; }

function undo() {
  if (_gestureActive()) return false;
  historyCheckpoint();                     // pending edits become a step first
  if (!_hist.undo.length) return false;
  const prev = _hist.undo.pop();
  _hist.redo.push(_hist.current);
  _hist.current = prev;
  _histRestore(prev);
  setStatus('Undo');
  return true;
}

function redo() {
  if (_gestureActive()) return false;
  historyCheckpoint();
  if (!_hist.redo.length) return false;
  const next = _hist.redo.pop();
  _hist.undo.push(_hist.current);
  _hist.current = next;
  _histRestore(next);
  setStatus('Redo');
  return true;
}

function _histRestore(entry) {
  const st = JSON.parse(entry.state);
  for (const k of Object.keys(layer)) delete layer[k];
  Object.assign(layer, st);
  selectedId = layer.source_paths.some(p => p.id === entry.sel) ? entry.sel : null;
  if (selectedOpeningId && !(layer.openings || []).some(o => o.id === selectedOpeningId))
    selectedOpeningId = null;
  selectedJunction = null;
  snapHint = null;
  drawPts = [];
  highlightPathId = null;
  routeResult = null;
  _syncLayerControls();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  updateHint();
  scheduleRefresh();                       // state == current: no new step
  updateUndoButtons();
  repaint();
}

function updateUndoButtons() {
  const u = document.getElementById('btn-undo');
  const r = document.getElementById('btn-redo');
  if (u) u.disabled = !canUndo();
  if (r) r.disabled = !canRedo();
}

// Layer-level controls mirror the (restored) layer state.
function _syncLayerControls() {
  const set = (id, prop, v) => { const el = document.getElementById(id); if (el) el[prop] = v; };
  set('route-returns', 'checked', layer.return_paths !== false);
  set('route-closed', 'checked', layer.prefer_closed !== false);
  set('override-reverse', 'checked', !!(layer.constraints && layer.constraints.reverse_direction));
  set('wg-junction-style', 'value', layer.junction_style || 'miter');
  set('wg-junction-radius', 'value', layer.junction_radius ?? 2);
  const jrow = document.getElementById('wg-junction-radius-row');
  if (jrow) jrow.style.display = layer.junction_style === 'round' ? '' : 'none';
  set('wg-cap-style', 'value', layer.cap_style || 'flat');
  set('wg-cap-corner-radius', 'value', layer.cap_corner_radius || 0);
  _syncMaterialControls();
}

_hist.current = { state: _histState(), sel: null };

// ---------------------------------------------------------------------------
// Transforms — one function maps any source path through a point map
// (translation, rotation; later: scale / mirror). Parametric shapes keep
// their identity: a rotated rectangle is still a RectanglePath (x, y, w, h
// + rotation about its centre), an ellipse adds to its rotation, a circle
// moves its centre; lines / curves / drawn paths map their points.
// ---------------------------------------------------------------------------
function _rotMap(pivot, a) {
  let c = Math.cos(a), s = Math.sin(a);
  if (Math.abs(c) < 1e-12) c = 0;          // exact quarter turns
  if (Math.abs(s) < 1e-12) s = 0;
  return p => [pivot[0] + (p[0] - pivot[0]) * c - (p[1] - pivot[1]) * s,
               pivot[1] + (p[0] - pivot[0]) * s + (p[1] - pivot[1]) * c];
}

function _normAngle(a) {
  a = Math.atan2(Math.sin(a), Math.cos(a));
  return Math.abs(a) < 1e-12 ? 0 : a;
}

function _rectCorners(path) {
  const pts = [[path.x, path.y], [path.x + path.w, path.y],
               [path.x + path.w, path.y + path.h], [path.x, path.y + path.h]];
  if (!path.rotation) return pts;
  return pts.map(_rotMap([path.x + path.w / 2, path.y + path.h / 2], path.rotation));
}

// Rotation pivot: a shape's own centre (closed primitives), else the
// centre of the bounding box (a line's midpoint).
function _pathPivot(path) {
  if (path.type === 'CirclePath' || path.type === 'EllipsePath') return [path.cx, path.cy];
  if (path.type === 'RectanglePath') return [path.x + path.w / 2, path.y + path.h / 2];
  const src = path.type === 'LinePath' ? [path.start, path.end]
    : path.type === 'QuadBezierPath' ? path.points
    : (path.control_points || path.points || []);
  if (!src || !src.length) return [0, 0];
  const xs = src.map(q => q[0]), ys = src.map(q => q[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

function _transformPath(path, map, dAngle) {
  switch (path.type) {
    case 'InsetPath':
      return false;                          // follows its parent
    case 'CirclePath':
      [path.cx, path.cy] = map([path.cx, path.cy]); break;
    case 'EllipsePath':
      [path.cx, path.cy] = map([path.cx, path.cy]);
      path.rotation = _normAngle((path.rotation || 0) + dAngle);
      break;
    case 'RectanglePath': {
      const c = map([path.x + path.w / 2, path.y + path.h / 2]);
      let r = _normAngle((path.rotation || 0) + dAngle);
      // quarter turns keep the rectangle axis-aligned (w / h swap)
      const q = Math.round(r / (Math.PI / 2));
      if (Math.abs(r - q * Math.PI / 2) < 1e-9) {
        if (q % 2) [path.w, path.h] = [path.h, path.w];
        r = 0;
      }
      path.rotation = r;
      path.x = c[0] - path.w / 2; path.y = c[1] - path.h / 2;
      break;
    }
    case 'LinePath':
      path.start = map(path.start); path.end = map(path.end); break;
    case 'QuadBezierPath':
      path.start = map(path.start); path.end = map(path.end); path.control = map(path.control);
      break;
    default: {
      const pts = path.control_points || path.points || [];
      path.control_points = pts.map(map);
      path.points = path.control_points;
    }
  }
  _computePrimitivePoints(path);
  _refreshInsetChildren(path.id);
  return true;
}

function _restorePathState(path, s) {
  for (const k of Object.keys(s)) {
    if (k === 'type' || s[k] === undefined) continue;
    path[k] = JSON.parse(JSON.stringify(s[k]));
  }
  _computePrimitivePoints(path);
}

function rotatePath(id, degrees) {
  const path = layer.source_paths.find(p => p.id === id);
  if (!path || !degrees) return false;
  if (path.type === 'InsetPath') { setStatus('An inset follows its parent — rotate the parent.'); return false; }
  const a = degrees * Math.PI / 180;
  _transformPath(path, _rotMap(_pathPivot(path), a), a);
  routeResult = null;
  scheduleRefresh();
  updatePropPanel();
  repaint();
  return true;
}

function translatePath(id, dx, dy) {
  const path = layer.source_paths.find(p => p.id === id);
  if (!path || path.type === 'InsetPath') return false;
  _transformPath(path, q => [q[0] + dx, q[1] + dy], 0);
  routeResult = null;
  scheduleRefresh();
  updatePropPanel();
  repaint();
  return true;
}

// Rotation handle: above the selection's bounding box, about its pivot.
const ROT_HANDLE_GAP = 16;   // world inches
function _rotHandle(path) {
  if (!path || path.type === 'InsetPath' || _isDriven(path) || !(path.points || []).length) return null;
  const pivot = _pathPivot(path);
  const ys = path.points.map(q => q[1]);
  return { pivot, wx: pivot[0], wy: Math.max(...ys) + ROT_HANDLE_GAP };
}

function _addTransformRows(panel, path) {
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = 'Rotate';
  const input = document.createElement('input');
  input.type = 'number'; input.className = 'prop-input'; input.value = 15; input.step = 5;
  input.style.minWidth = '42px';
  input.title = 'Degrees (counter-clockwise) about the shape centre / midpoint';
  const go = document.createElement('button');
  const deg = document.createElement('span');
  deg.className = 'prop-unit'; deg.textContent = '°';
  go.className = 'mini-btn'; go.textContent = '↺';
  go.title = 'Rotate by this many degrees';
  go.onclick = () => rotatePath(path.id, +input.value || 0);
  const cw = document.createElement('button');
  cw.className = 'mini-btn'; cw.textContent = '↻';
  cw.title = 'Rotate the other way';
  cw.onclick = () => rotatePath(path.id, -(+input.value || 0));
  row.append(lbl, input, deg, go, cw);
  panel.appendChild(row);
  if (path.type === 'RectanglePath' || path.type === 'EllipsePath') {
    addPropRowNum(panel, 'Angle', 'rot', +((path.rotation || 0) * 180 / Math.PI).toFixed(3), v => {
      rotatePath(path.id, v - (path.rotation || 0) * 180 / Math.PI);
    }, '°');
  }
}

// ---------------------------------------------------------------------------
// Copy / paste / duplicate
// A copy is a new source path: new id, next auto label (Line 2 …), offset
// slightly so it is visible; its ends are ordinary snap targets / snapping
// handles. Wall spec and Corner R travel with it. A copy of an inset is
// ordinary (frozen) geometry — the relationship belongs to the original.
// ---------------------------------------------------------------------------
let _clipboard = null;
let _pasteCount = 0;
const PASTE_OFFSET = 10;   // world inches (+x, −y)

function copySelected() {
  const p = layer.source_paths.find(s => s.id === selectedId);
  if (!p) return false;
  _clipboard = JSON.parse(JSON.stringify(p));
  _pasteCount = 0;
  setStatus(`Copied ${p.label || p.id}`);
  return true;
}

function _pasteCopy(src, d) {
  const c = JSON.parse(JSON.stringify(src));
  if (c.type === 'InsetPath') {
    c.type = 'ExplicitPath';
    c.control_points = (c.points || []).map(q => [...q]);
    delete c.parent_id; delete c.distance; delete c.mode;
    c.closed = true;
  }
  c.id = newId();
  const auto = new RegExp('^' + (TYPE_NAMES[src.type] || 'Path') + ' \\d+$');
  c.label = (!src.label || auto.test(src.label)) ? _nextLabel(c.type) : `${src.label} copy`;
  _transformPath(c, q => [q[0] + d, q[1] - d], 0);
  layer.source_paths.push(c);
  selectedId = c.id;
  selectedOpeningId = null;
  selectedJunction = null;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  updateHint();
  repaint();
  return c;
}

function pasteClipboard() {
  if (!_clipboard) return null;
  _pasteCount += 1;
  return _pasteCopy(_clipboard, PASTE_OFFSET * _pasteCount);
}

function duplicateSelected() {
  const p = layer.source_paths.find(s => s.id === selectedId);
  if (!p) { setStatus('Select a path to duplicate.'); return null; }
  return _pasteCopy(p, PASTE_OFFSET);
}

// ---------------------------------------------------------------------------
// Parametric inset / outset (DESIGN GEOMETRY, not a toolpath treatment)
// An InsetPath is a real closed source path whose shape is derived from
// its parent: { type: 'InsetPath', parent_id, distance, mode: inset|outset }.
// The backend recomputes it from the parent every evaluation (exact,
// trimmed); the frontend previews it while the parent is dragged. It can
// carry a wall thickness, bound an infill region or be a void. Deleting
// the parent DETACHES it (keeps the current shape as a plain path).
// ---------------------------------------------------------------------------
function _offsetClosedPreview(pts, d) {
  // miter offset of a closed polyline (+d = to the left of travel)
  const n = pts.length;
  if (n < 3) return [];
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = pts[(i + n - 1) % n], b = pts[i], c = pts[(i + 1) % n];
    const n1 = _leftNormal(a, b), n2 = _leftNormal(b, c);
    let mx = n1[0] + n2[0], my = n1[1] + n2[1];
    const ml = Math.hypot(mx, my);
    if (ml < 1e-9) { out.push([b[0] + n1[0] * d, b[1] + n1[1] * d]); continue; }
    mx /= ml; my /= ml;
    const k = Math.min(10, 1 / Math.max(0.1, mx * n1[0] + my * n1[1]));
    out.push([b[0] + mx * d * k, b[1] + my * d * k]);
  }
  return out;
}
function _leftNormal(a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1;
  return [-dy / L, dx / L];
}
function _signedArea(pts) {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return s / 2;
}
function _insetPreview(child, parent) {
  const pts = parent.points || [];
  if (!parent.closed || pts.length < 3) return [];
  const inward = _signedArea(pts) > 0 ? 1 : -1;
  const sign = child.mode === 'outset' ? -inward : inward;
  return _offsetClosedPreview(pts, sign * child.distance);
}
function _refreshInsetChildren(parentId, depth = 0) {
  if (depth > 8 || typeof layer === 'undefined') return;
  const parent = layer.source_paths.find(p => p.id === parentId);
  if (!parent) return;
  for (const rel of (layer.wall_relations || [])) {
    const info = _relInfo(rel);
    if (info.drv === parentId) {
      const dep = layer.source_paths.find(p => p.id === info.dep);
      if (dep && _applyRelation(rel, parent, dep)) {
        _computePrimitivePoints(dep);
        _refreshInsetChildren(dep.id, depth + 1);
      }
    }
  }
  for (const c of layer.source_paths) {
    if (c.type !== 'InsetPath' || c.parent_id !== parentId) continue;
    c.points = _insetPreview(c, parent);
    _refreshInsetChildren(c.id, depth + 1);
  }
}

function createInset(parentId, distance, mode = 'inset') {
  const parent = layer.source_paths.find(p => p.id === parentId);
  if (!parent || !parent.closed) { setStatus('Inset / outset needs a closed path.'); return null; }
  const c = { id: newId(), type: 'InsetPath', label: _nextLabel('InsetPath'), closed: true,
              role: 'free', visible: true, parent_id: parentId,
              distance: Math.max(0.1, +distance || 10), mode, points: [] };
  c.points = _insetPreview(c, parent);
  layer.source_paths.push(c);
  selectedId = c.id;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  repaint();
  return c;
}

function _detachInset(c) {
  c.type = 'ExplicitPath';
  c.control_points = (c.points || []).map(q => [...q]);
  c.points = c.control_points;
  c.closed = true;
  delete c.parent_id; delete c.distance; delete c.mode;
}

function detachInset(id) {
  const c = layer.source_paths.find(p => p.id === id && p.type === 'InsetPath');
  if (!c) return false;
  _detachInset(c);
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  repaint();
  return true;
}

// Backend-evaluated inset shapes (exact). Not an edit: if nothing else is
// pending, the current history state absorbs them (no phantom undo step).
function _applyDerivedSources() {
  const ds = networkInfo && networkInfo.derived_sources;
  if (!ds) return;
  const clean = _hist.current && _histState() === _hist.current.state;
  let changed = false;
  for (const p of layer.source_paths) {
    if (p.type !== 'InsetPath' || !(p.id in ds)) continue;
    if (JSON.stringify(p.points) !== JSON.stringify(ds[p.id])) { p.points = ds[p.id]; changed = true; }
  }
  if (changed && clean) _hist.current.state = _histState();
  if (changed) repaint();
}

// Advanced (collapsed): the special-case wall relationship between two
// separately drawn boundaries, and the general geometry operation Inset /
// Outset. The normal way to make a wall is Wall Thickness (above).
function _addAdvancedGeometryRows(panel, path) {
  const relOK = path.closed && RELATION_TYPES.includes(path.type);
  if (!relOK && !path.closed) return;
  const adv = document.createElement('details');
  adv.className = 'advanced';
  adv.id = 'adv-geometry';
  const sm = document.createElement('summary');
  sm.textContent = 'Advanced: linked boundaries · inset / outset';
  adv.appendChild(sm);
  const body = document.createElement('div');
  adv.appendChild(body);
  if (relOK) _addWallRelationRows(body, path);
  if (path.closed) _addInsetCreateRows(body, path);
  if (relOK && _relationOf(path.id)) adv.open = true;     // an active link stays visible
  panel.appendChild(adv);
}

function _addInsetCreateRows(panel, path) {
  _addSubTitle(panel, 'Create inset / outset path');
  _addNote(panel, 'Geometry operation: makes a NEW closed path that follows this one at a distance. ' +
                  'For a wall, use Wall Thickness.');
  const row = document.createElement('div'); row.className = 'prop-row';
  row.title = 'Parametric: a closed path kept this far inside / outside this one; it follows every edit';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = 'Distance';
  const input = document.createElement('input');
  input.type = 'number'; input.className = 'prop-input'; input.value = 10; input.min = 0.1;
  input.id = 'inset-distance';
  input.style.minWidth = '42px';
  const bi = document.createElement('button');
  bi.className = 'mini-btn'; bi.textContent = 'Inset'; bi.id = 'inset-create';
  bi.onclick = () => createInset(path.id, +input.value, 'inset');
  const bo = document.createElement('button');
  bo.className = 'mini-btn'; bo.textContent = 'Outset'; bo.id = 'outset-create';
  bo.onclick = () => createInset(path.id, +input.value, 'outset');
  row.append(lbl, input, bi, bo);
  panel.appendChild(row);
}

function _addInsetChildRows(panel, path) {
  const parent = layer.source_paths.find(p => p.id === path.parent_id);
  const info = document.createElement('div');
  info.className = 'path-type';
  info.textContent = parent
    ? `${path.mode === 'outset' ? 'Outset' : 'Inset'} of ${parent.label || parent.id} — follows it`
    : 'Parent deleted';
  info.onmouseenter = () => parent && setHighlight(parent.id);
  info.onmouseleave = () => setHighlight(null);
  panel.appendChild(info);
  if (!(path.points || []).length) {
    const w = document.createElement('div');
    w.className = 'path-type'; w.style.color = 'var(--warn)';
    w.textContent = 'no shape at this distance (inset larger than the parent)';
    panel.appendChild(w);
  }
  addPropRowSelectTo(panel, 'Mode', path.mode === 'outset' ? 'Outset' : 'Inset', ['Inset', 'Outset'], v => {
    path.mode = v === 'Outset' ? 'outset' : 'inset';
    _refreshInsetChildren(path.parent_id);
    routeResult = null; scheduleRefresh(); repaint();
  });
  addPropRowNumTo(panel, 'Distance', path.distance, v => {
    path.distance = Math.max(0.1, v);
    _refreshInsetChildren(path.parent_id);
    routeResult = null; scheduleRefresh(); repaint();
  }, 'in');
  const det = document.createElement('button');
  det.className = 'add-btn';
  det.textContent = 'Detach (keep as plain path)';
  det.onclick = () => detachInset(path.id);
  panel.appendChild(det);
}

// ---------------------------------------------------------------------------
// Infill kind / region helpers
// ---------------------------------------------------------------------------
const _SOLID_PARAMS = [
  { name: 'spacing', label: 'Spacing', default: 20, min: 2 },
  { name: 'angle', label: 'Angle', default: 45, min: -90, max: 180 },
  { name: 'perimeters', label: 'Perimeters', default: 1, min: 1, max: 4 }];
const SOLID_FALLBACK = { rectilinear: { kind: 'solid', parameters: _SOLID_PARAMS },
                         serpentine: { kind: 'solid', parameters: _SOLID_PARAMS } };

const PATTERN_LABELS = { zigzag: 'Zigzag', wave: 'Wave', truss: 'Adaptive Truss',
                         rectilinear: 'Rectilinear', serpentine: 'Serpentine' };

function _patternsFor(kind) {
  const names = Object.keys(infillPatterns).filter(k => (infillPatterns[k].kind || 'wall') === kind);
  if (names.length) return names;
  return kind === 'solid' ? Object.keys(SOLID_FALLBACK) : ['zigzag', 'wave'];
}
function _paramsFor(f) {
  const pat = infillPatterns[f.pattern] || SOLID_FALLBACK[f.pattern];
  return (pat && pat.parameters) ||
    [{ name: 'spacing', label: f.kind === 'solid' ? 'Spacing' : 'Target Spacing', default: 20 }];
}
function _setInfillKind(f, kind) {
  f.kind = kind;
  const names = _patternsFor(kind);
  if (!names.includes(f.pattern)) f.pattern = names[0];
  const old = f.params || {};
  f.params = {};
  for (const prm of _paramsFor(f)) f.params[prm.name] = old[prm.name] ?? prm.default;
}

function _pointInPoly(pt, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi)
      inside = !inside;
  }
  return inside;
}
// Closed paths not inside any other closed path (geometry, not order).
function _outermostClosed() {
  const closed = layer.source_paths.filter(p => p.closed && (p.points || []).length >= 3);
  const inside = (a, b) => Math.abs(_signedArea(b.points)) > Math.abs(_signedArea(a.points)) &&
                           a.points.every((q, i) => i % 8 || _pointInPoly(q, b.points));
  const outer = closed.filter(a => !closed.some(b => b !== a && inside(a, b)));
  return outer[0] || null;
}

// Wall lattice: the spacing is a target; the stitches are redistributed
// evenly per wall run. Read-only diagnostic of the actual pitch.
function _latticeActual(f) {
  const info = ((networkInfo && networkInfo.infills) || []).find(i => i.id === f.id);
  const lat = info && info.lattice;
  if (!lat || lat.pitch_min == null) return '';
  const a = lat.pitch_min.toFixed(1), b = lat.pitch_max.toFixed(1);
  if (lat.truss) {
    // Adaptive Truss: pitch is derived (brace angle × cavity + bond)
    const st = lat.truss.stitches || {};
    const reduced = (st.truss_short || 0) + (st.truss_plain || 0);
    let t = `Pitch: ${a === b ? a : a + '–' + b} in (straight-wall ${lat.truss.effective_pitch.toFixed(1)} in)`;
    if (lat.max_unsupported != null)
      t += ` · Longest station gap ${lat.max_unsupported.toFixed(1)} in (≤ ${lat.max_unsupported_limit.toFixed(1)}: each skin within the span)`;
    if (reduced) t += ` · Bond reduced at ${reduced} stitch${reduced > 1 ? 'es' : ''}`;
    return t;
  }
  let txt = `Actual: ${a === b ? a : a + '–' + b} in`;
  if (lat.max_unsupported != null)
    txt += ` · Max unsupported: ${lat.max_unsupported.toFixed(1)} in (limit ${lat.max_unsupported_limit.toFixed(1)})`;
  return txt;
}

// Solid infill: boundary support and topology (read-only diagnostic).
function _solidActual(f) {
  const info = ((networkInfo && networkInfo.infills) || []).find(i => i.id === f.id);
  const so = info && info.solid;
  if (!so || so.max_unsupported == null) return '';
  let txt = `Longest boundary stretch without a tie: ${so.max_unsupported.toFixed(1)} in`;
  txt += so.max_unsupported_limit ? ` (limit ${so.max_unsupported_limit.toFixed(1)})` : '';
  if (so.web_contacts) txt += ` · web contacts: ${so.web_contacts}`;
  return txt;
}

// V1 / V2 shift the phase only where it is free (closed loops, lone
// walls); in wall networks the junctions fix it — the control is hidden.
function _variationEffective(f) {
  const info = ((networkInfo && networkInfo.infills) || []).find(i => i.id === f.id);
  const lat = info && info.lattice;
  if (lat && lat.lineage) return lat.lineage.variation_effective !== false;   // the shared lattice
  if (!lat || lat.motif === false) return true;            // field fallback: phase applies
  return lat.variation_effective !== false;
}

function _infillVoidsLabel(f) {
  const info = ((networkInfo && networkInfo.infills) || []).find(i => i.id === f.id);
  const p = layer.source_paths.find(s => s.id === f.path_id);
  if (!info || !p || !p.closed) return '';
  if (f.kind !== 'solid' && _effectiveWall(p).wall) return '';
  const nm = id => _pathName(id);
  const v = (info.voids || []).map(nm);
  let s = `Region: ${nm(f.path_id)} · Voids: ${v.length ? v.join(', ') : 'none'}`;
  if ((info.islands || []).length) s += ` · Islands: ${info.islands.map(nm).join(', ')}`;
  return s;
}

// ---------------------------------------------------------------------------
// Wall relationship between two nested closed boundaries (WALL / REGION
// semantics): "these two boundaries define a wall `thickness` thick". The
// driver (default: outer) is edited; the dependent follows. Mirrors
// model.WallRelation (backend authoritative); rect ↔ rect, circle ↔ circle,
// ellipse ↔ ellipse only — anything else is refused, never distorted.
// ---------------------------------------------------------------------------
const RELATION_TYPES = ['RectanglePath', 'CirclePath', 'EllipsePath'];

// Every driven boundary follows its driver (numeric edits, undo, …).
function _syncRelations() {
  for (const rel of (layer.wall_relations || [])) {
    const { drv, dep } = _relInfo(rel);
    const D = layer.source_paths.find(p => p.id === drv), P = layer.source_paths.find(p => p.id === dep);
    if (D && P && _applyRelation(rel, D, P)) _computePrimitivePoints(P);
  }
}

function _relInfo(rel) {
  return rel.driver === 'inner' ? { drv: rel.inner_id, dep: rel.outer_id }
                                : { drv: rel.outer_id, dep: rel.inner_id };
}
function _relationOf(id) {
  for (const rel of (typeof layer !== 'undefined' && layer.wall_relations) || []) {
    if (rel.outer_id === id || rel.inner_id === id) return Object.assign({ rel }, _relInfo(rel));
  }
  return null;
}
function _isDriven(path) {
  const r = path && _relationOf(path.id);
  return !!(r && r.dep === path.id);
}

// dependent = driver ∓ thickness (outer drives: −, inner drives: +)
function _applyRelation(rel, drv, dep) {
  if (drv.type !== dep.type || !RELATION_TYPES.includes(drv.type)) return false;
  const t = Math.max(0, +rel.thickness || 0);
  const sgn = rel.driver === 'inner' ? 1 : -1;
  if (drv.type === 'RectanglePath') {
    const w = drv.w + 2 * sgn * t, h = drv.h + 2 * sgn * t;
    if (w <= 0 || h <= 0) return false;
    const cx = drv.x + drv.w / 2, cy = drv.y + drv.h / 2;
    Object.assign(dep, { w, h, x: cx - w / 2, y: cy - h / 2, rotation: drv.rotation || 0 });
    const r = _cornerR(drv);
    dep.corner_radius = r > 0 ? Math.max(0, r + sgn * t) : 0;
    return true;
  }
  if (drv.type === 'CirclePath') {
    const rad = drv.radius + sgn * t;
    if (rad <= 0) return false;
    Object.assign(dep, { cx: drv.cx, cy: drv.cy, radius: rad });
    return true;
  }
  const rx = drv.rx + sgn * t, ry = drv.ry + sgn * t;
  if (rx <= 0 || ry <= 0) return false;
  Object.assign(dep, { cx: drv.cx, cy: drv.cy, rx, ry, rotation: drv.rotation || 0 });
  return true;
}

function _contains(outer, inner) {
  const op = outer.points || [], ip = inner.points || [];
  if (op.length < 3 || ip.length < 3) return false;
  return Math.abs(_signedArea(op)) > Math.abs(_signedArea(ip)) &&
         ip.every((q, i) => i % 8 || _pointInPoly(q, op));
}

function createWallRelation(aId, bId, thickness = 10) {
  const a = layer.source_paths.find(p => p.id === aId);
  const b = layer.source_paths.find(p => p.id === bId);
  if (!a || !b || a === b) return null;
  if (a.type !== b.type || !RELATION_TYPES.includes(a.type)) {
    setStatus('A wall relationship needs two rectangles, two circles or two ellipses (use Inset / Outset for other shapes).');
    return null;
  }
  const [outer, inner] = _contains(a, b) ? [a, b] : _contains(b, a) ? [b, a] : [null, null];
  if (!outer) { setStatus('The two boundaries must be nested (one inside the other).'); return null; }
  if (_relationOf(a.id) || _relationOf(b.id)) { setStatus('A boundary can be in one wall relationship.'); return null; }
  const rel = { id: newId(), outer_id: outer.id, inner_id: inner.id,
                thickness: Math.max(0.1, +thickness || 10), driver: 'outer' };
  if (!_applyRelation(rel, outer, inner)) { setStatus('Thickness too large for these boundaries.'); return null; }
  layer.wall_relations.push(rel);
  _computePrimitivePoints(inner);
  _refreshInsetChildren(inner.id);
  routeResult = null;
  scheduleRefresh();
  updatePropPanel();
  repaint();
  return rel;
}

function breakWallRelation(relId) {
  const n = layer.wall_relations.length;
  layer.wall_relations = layer.wall_relations.filter(r => r.id !== relId);   // geometry stays
  if (layer.wall_relations.length === n) return false;
  routeResult = null;
  scheduleRefresh();
  updatePropPanel();
  repaint();
  return true;
}

function setRelationThickness(relId, t) {
  const rel = layer.wall_relations.find(r => r.id === relId);
  if (!rel) return false;
  const old = rel.thickness;
  rel.thickness = Math.max(0.1, +t || rel.thickness);
  const { drv, dep } = _relInfo(rel);
  const D = layer.source_paths.find(p => p.id === drv), P = layer.source_paths.find(p => p.id === dep);
  if (!_applyRelation(rel, D, P)) { rel.thickness = old; setStatus('Thickness too large.'); return false; }
  _computePrimitivePoints(P);
  _refreshInsetChildren(P.id);
  routeResult = null; scheduleRefresh(); updatePropPanel(); repaint();
  return true;
}

function setRelationDriver(relId, driver) {
  const rel = layer.wall_relations.find(r => r.id === relId);
  if (!rel) return false;
  rel.driver = driver === 'inner' ? 'inner' : 'outer';
  routeResult = null; scheduleRefresh(); updatePropPanel(); repaint();
  return true;
}

// Candidate partners for linking: closed paths of the same shape type
// nested inside / around this one and not linked yet.
function _relationCandidates(path) {
  return layer.source_paths.filter(p => p.id !== path.id && p.type === path.type && p.closed &&
                                        !_relationOf(p.id) && (_contains(p, path) || _contains(path, p)));
}

function _addWallRelationRows(panel, path) {
  _addSubTitle(panel, 'Link two boundaries as one wall');
  const r = _relationOf(path.id);
  if (r) {
    const rel = r.rel;
    const line = (txt) => { const d = document.createElement('div'); d.className = 'path-type'; d.textContent = txt; panel.appendChild(d); return d; };
    line(`Outer boundary: ${_pathName(rel.outer_id)}${rel.driver === 'outer' ? ' (drives)' : ''}`);
    line(`Inner boundary: ${_pathName(rel.inner_id)}${rel.driver === 'inner' ? ' (drives)' : ''}`);
    addPropRowNum(panel, 'Thickness', 'rel-t', rel.thickness, v => setRelationThickness(rel.id, v));
    addPropRowSelectTo(panel, 'Driver', rel.driver === 'inner' ? 'Inner' : 'Outer', ['Outer', 'Inner'],
                       v => setRelationDriver(rel.id, v === 'Inner' ? 'inner' : 'outer'));
    const st = ((networkInfo && networkInfo.wall_relations) || {})[rel.id];
    if (st && st.status !== 'ok') { const w = line(st.status); w.style.color = 'var(--warn)'; }
    else if (st && st.spread > 0.01) line(`Gap ${st.min_gap}–${st.max_gap} in (ellipses: exact on the axes)`);
    const br = document.createElement('button');
    br.className = 'add-btn';
    br.textContent = 'Break relationship (keep geometry)';
    br.onclick = () => breakWallRelation(rel.id);
    panel.appendChild(br);
    return;
  }
  const kind = (TYPE_NAMES[path.type] || 'path').toLowerCase();
  if (!_relationCandidates(path).length) {
    // nothing to link with: say so instead of a dead-looking control
    const n = document.createElement('div');
    n.className = 'path-type'; n.id = 'relation-none';
    n.textContent = `For two separately drawn boundaries that should stay one wall apart. ` +
                    `Draw another ${kind} inside or around this one to link them.`;
    panel.appendChild(n);
    return;
  }
  _addNote(panel, `This ${kind} and another ${kind} drawn inside / around it become the two faces of ` +
                  `one wall that keeps its thickness. Hover a choice to see it on the canvas.`);
  let other = null;
  const pk = addPathPickerRow(panel, 'Other boundary', null, id => { other = id; pk.btn.textContent = _pathName(id) + ' ▾'; },
                              path.id, p => _relationCandidates(path).some(c => c.id === p.id));
  pk.btn.textContent = 'choose ▾';
  pk.btn.id = 'relation-picker';
  const row = document.createElement('div'); row.className = 'prop-row';
  const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = 'Thickness';
  const input = document.createElement('input');
  input.type = 'number'; input.className = 'prop-input'; input.value = 10; input.min = 0.1;
  input.style.minWidth = '42px';
  const go = document.createElement('button');
  go.className = 'mini-btn'; go.textContent = 'Link'; go.id = 'relation-link';
  go.onclick = () => { if (other) createWallRelation(path.id, other, +input.value); else setStatus('Choose the other boundary first.'); };
  row.append(lbl, input, go);
  panel.appendChild(row);
}

let _idCounter = 1;
function newId() { return 'p' + (_idCounter++); }

// ---------------------------------------------------------------------------
// WORKSPACES + PROJECT LAYER DESIGNS
// One application, two workspaces: [Designer] [Assembly]. Switching is a
// view change; neither workspace's state is touched.
// The project's LAYER DESIGNS (design_proto/layer_design.py): Base is a full
// Designer document; a derived design stores only its delta from its
// parent ({patch, settings}). The Designer edits ONE design at a time (its
// resolved document is `layer`); leaving a derived design stores its delta
// (computed Designer-side against the LIVE parent), so inheritance stays
// live and nothing is copied. Each design keeps its own undo history.
// Not persisted across page reloads (neither is the Designer).
// ---------------------------------------------------------------------------
let currentWorkspace = 'designer';
const _workspaceHooks = {};       // name → onShow()

function setWorkspace(name) {
  currentWorkspace = name;
  for (const w of ['designer', 'assembly']) {
    const el = document.getElementById('ws-' + w);
    if (el) el.style.display = w === name ? '' : 'none';
    const tab = document.getElementById('ws-tab-' + w);
    if (tab) tab.classList.toggle('active', w === name);
  }
  renderDesignTabs();
  if (name === 'designer') resizeCanvas();
  if (_workspaceHooks[name]) _workspaceHooks[name]();
}

const project = {
  designs: [{ id: 'base', name: 'Base', parent: null }],
  active: 'base',
  baseDoc: null,            // Base's document while another design is edited
  deltas: {},               // derived id → { patch, settings }
  hist: {},                 // design id → its undo history
};
let _designCounter = 1;

async function _post(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(body) });
  const data = await res.json();
  if (data && data.error) throw new Error(data.error);
  return data;
}

// The Layer Design list in the form the backend takes (layer_design.py).
function _designsPayload() {
  return project.designs.map(d => d.parent == null
    ? { id: d.id, name: d.name, parent: null,
        document: JSON.parse(JSON.stringify(project.active === d.id ? layer : project.baseDoc)) }
    : { id: d.id, name: d.name, parent: d.parent,
        patch: (project.deltas[d.id] || {}).patch || {}, settings: _withoutBeadWidth((project.deltas[d.id] || {}).settings) });
}

// a derived design never carries its own bead width (one project value)
function _withoutBeadWidth(settings) {
  const s = { ...(settings || {}) };
  if (s.material && 'bead_width' in s.material) {
    const { bead_width, ...m } = s.material;
    if (Object.keys(m).length) s.material = m; else delete s.material;
  }
  return s;
}

// store the delta of the derived design being edited (vs its LIVE parent)
async function syncActiveDesign() {
  const d = project.designs.find(x => x.id === project.active);
  if (!d || d.parent == null) return;
  const r = await _post('/api/layer_designs/delta',
                        { designs: _designsPayload(), parent: d.parent, id: d.id,
                          document: JSON.parse(JSON.stringify(layer)) });
  project.deltas[d.id] = { patch: r.patch, settings: r.settings };
  // LATTICE DEFINITION edits (pattern, spacing, variation …) of an inherited
  // wall infill belong to the LINEAGE: the backend moved them to the design
  // that owns the infill — take the updated owner(s) back
  // a BEAD WIDTH edited while viewing the variant belongs to the project (root)
  const owners = Object.keys(r.lattice_moved || {});
  if (r.material_moved && !owners.includes(r.material_moved)) owners.push(r.material_moved);
  for (const owner of owners) {
    const od = (r.designs || []).find(x => x.id === owner);
    if (!od) continue;
    if (od.parent == null) project.baseDoc = od.document;
    else project.deltas[owner] = { patch: od.patch, settings: od.settings };
  }
}

async function designsForBackend() {
  await syncActiveDesign();
  return _designsPayload();
}

function createDerivedDesign(name, parent) {
  if (!project.designs.some(d => d.id === parent)) throw new Error('unknown parent ' + parent);
  const id = 'd' + (_designCounter++);
  project.designs.push({ id, name: (name || '').trim() || `Variant ${_designCounter - 1}`, parent });
  project.deltas[id] = { patch: {}, settings: {} };
  renderDesignTabs();
  return id;
}

// open another Layer Design in the Designer
async function editDesign(id) {
  if (id === project.active || !project.designs.some(d => d.id === id)) return;
  await syncActiveDesign();
  project.hist[project.active] = { undo: _hist.undo, redo: _hist.redo, current: _hist.current, bytes: _hist.bytes };
  if (project.active === 'base' || project.designs.find(d => d.id === project.active).parent == null)
    project.baseDoc = JSON.parse(JSON.stringify(layer));
  const target = project.designs.find(d => d.id === id);
  const doc = target.parent == null ? project.baseDoc
            : (await _post('/api/layer_designs/document', { designs: _designsPayload(), id })).document;
  project.active = id;
  for (const k of Object.keys(layer)) delete layer[k];
  Object.assign(layer, JSON.parse(JSON.stringify(doc)));
  const h = project.hist[id] || { undo: [], redo: [], current: null, bytes: 0 };
  Object.assign(_hist, h);
  selectedId = null; selectedOpeningId = null; selectedJunction = null;
  snapHint = null; drawPts = []; highlightPathId = null;
  routeResult = null; networkInfo = null; derivedPaths = []; printable = [];
  _syncLayerControls();
  updatePathList(); updatePropPanel(); updateOffsetList(); updateInfillList(); updateHint();
  renderDesignTabs();
  scheduleRefresh();
  updateUndoButtons();
  repaint();
  setStatus(target.parent == null ? `Editing ${target.name}.`
            : `Editing ${target.name} — derived from ${_designName(target.parent)}: only its differences are stored.`);
}

// rename: the user-facing name only — the stable id (referenced by every
// assembly section and by derived designs) never changes
function renameDesign(id, name) {
  const d = project.designs.find(x => x.id === id);
  const n = (name || '').trim();
  if (!d) return false;
  if (!n) { setStatus('A Layer Design needs a name — kept "' + d.name + '".'); return false; }
  d.name = n;
  renderDesignTabs();
  return true;
}

function _designName(id) {
  const d = project.designs.find(x => x.id === id);
  return d ? d.name : id;
}

// Designer header: which Layer Design is being edited (only once there are several)
function renderDesignTabs() {
  const el = document.getElementById('design-tabs');
  if (!el) return;
  el.innerHTML = '';
  if (Array.isArray(el.children)) el.children.length = 0;   // (test DOM stub)
  const show = currentWorkspace === 'designer' && project.designs.length > 1;
  el.style.display = show ? '' : 'none';
  if (!show) return;
  for (const d of project.designs) {
    const b = document.createElement('button');
    b.className = 'design-tab' + (d.id === project.active ? ' active' : '');
    b.textContent = d.name;
    b.title = d.parent == null ? 'Base design' : `Derived from ${_designName(d.parent)}`;
    b.onclick = () => editDesign(d.id);
    el.appendChild(b);
  }
}

// The narrow interface the Assembly workspace uses (static/assembly.js).
window.Designer = {
  listDesigns: () => project.designs.map(d => ({ ...d, active: d.id === project.active })),
  designsForBackend, createDerivedDesign, editDesign, renameDesign, setWorkspace,
  beadWidth: projectBeadWidth, setBeadWidth: setProjectBeadWidth,   // ONE project material value
  onWorkspaceShow: (name, fn) => { _workspaceHooks[name] = fn; },
};

// ---------------------------------------------------------------------------
// Init — blank canvas, auto-routes when first path is added
// ---------------------------------------------------------------------------

async function init() {
  resizeCanvas();

  try {
    const res = await fetch('/api/infill_patterns');
    const pats = await res.json();
    for (const p of pats) infillPatterns[p.name] = p;
  } catch (e) {
    console.warn('Could not load infill patterns', e);
  }

  _syncMaterialControls();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateInfillList();
  updateHint();
  updateUndoButtons();
  repaint();
  setStatus('Ready — add paths from the toolbar or use Draw Path. Toolpath auto-routes when enabled.');
}

init();
