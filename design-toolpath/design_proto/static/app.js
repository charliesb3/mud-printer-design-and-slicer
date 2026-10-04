'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let tool = 'select';           // 'select' | 'draw'
let drawPts = [];              // points accumulated while drawing
let selectedId = null;         // selected path id

let showToolpath = true;
let showArrows = true;
let showNumbers = true;

let routeResult = null;        // last /api/route response

// The layer model (serialised as JSON to/from server)
const layer = {
  id: 'design',
  label: '',
  source_paths: [],
  offset_treatments: [],
  lattice_instances: [],
  constraints: {
    start_path_id: null,
    start_t: null,
    reverse_direction: false,
    component_order: null,
  },
};

let generators = {};           // name -> { parameters }

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
// The canvas uses a fixed 400×400 world coordinate space
// ---------------------------------------------------------------------------

const WORLD = 400;

function worldToCanvas(x, y) {
  const s = canvas.width / WORLD;
  return [x * s, (WORLD - y) * s];
}

function canvasToWorld(cx, cy) {
  const s = WORLD / canvas.width;
  return [cx * s, WORLD - cy * s];
}

// ---------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------

const ROLE_COLORS = {
  outer:   '#4a9eff',
  inner:   '#44ccaa',
  lattice: '#aa88ff',
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

function drawArrowhead(cx, cy, angle, size, color) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(size, 0);
  ctx.lineTo(-size * 0.6, size * 0.5);
  ctx.lineTo(-size * 0.6, -size * 0.5);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();
}

function drawNumber(x, y, n, color) {
  const s = String(n);
  const w = s.length * 6 + 6;
  const h = 12;
  ctx.save();
  ctx.fillStyle = 'rgba(0,0,0,0.65)';
  ctx.fillRect(x - w / 2, y - h / 2, w, h);
  ctx.fillStyle = color;
  ctx.font = '9px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(s, x, y);
  ctx.restore();
}

function drawDot(x, y, r, color) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

// ---------------------------------------------------------------------------
// Repaint
// ---------------------------------------------------------------------------

function repaint() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Draw grid (faint)
  drawGrid();

  // Draw effective paths (source + derived)
  drawEffectivePaths();

  // Draw toolpath overlay
  if (showToolpath && routeResult) {
    drawToolpath(routeResult.moves);
  }

  // Draw in-progress freehand
  if (tool === 'draw' && drawPts.length > 0) {
    drawInProgress();
  }

  // Draw node handles for selected path
  if (selectedId) {
    drawHandles(selectedId);
  }
}

function drawGrid() {
  const step = 40; // world units
  ctx.save();
  ctx.strokeStyle = '#282828';
  ctx.lineWidth = 1;
  for (let wx = 0; wx <= WORLD; wx += step) {
    const [cx] = worldToCanvas(wx, 0);
    const [, cy0] = worldToCanvas(wx, 0);
    const [, cy1] = worldToCanvas(wx, WORLD);
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

function drawEffectivePaths() {
  // Collect all paths: source + offset-derived + lattice-derived
  // We recompute locally from layer state (no server round-trip for display)
  for (const p of layer.source_paths) {
    if (!p.visible) continue;
    const pts = _pathCanvasPts(p);
    const color = p.id === selectedId ? '#ffffff' : (ROLE_COLORS[p.role] || '#aaa');
    drawPolyline(pts, color, p.id === selectedId ? 2.5 : 1.8, false, p.closed);
    // Endpoint dots for open paths
    if (!p.closed && pts.length >= 2) {
      drawDot(pts[0][0], pts[0][1], 3, '#888');
      drawDot(pts[pts.length - 1][0], pts[pts.length - 1][1], 3, '#888');
    }
  }

  // Draw derived paths from last effective_paths result if available
  if (routeResult && routeResult.layer && routeResult.layer.paths) {
    for (const p of routeResult.layer.paths) {
      // Skip paths already shown as source
      if (layer.source_paths.find(sp => sp.id === p.id)) continue;
      const pts = _pathCanvasPts(p);
      const color = ROLE_COLORS[p.role] || '#aa88ff';
      drawPolyline(pts, color, 1.5, p.role === 'lattice', p.closed);
    }
  }
}

function drawToolpath(moves) {
  for (let i = 0; i < moves.length; i++) {
    const m = moves[i];
    const [x0, y0] = worldToCanvas(m.start[0], m.start[1]);
    const [x1, y1] = worldToCanvas(m.end[0], m.end[1]);
    const color = MOVE_COLORS[m.kind] || MOVE_COLORS.print;
    const dashed = m.kind === 'travel';
    const lw = m.kind === 'travel' ? 1.2 : 2.0;
    drawLine(x0, y0, x1, y1, color, lw, dashed);

    if (showArrows) {
      const mx = (x0 + x1) / 2;
      const my = (y0 + y1) / 2;
      const angle = Math.atan2(y1 - y0, x1 - x0);
      drawArrowhead(mx, my, angle, 5, color);
    }

    if (showNumbers) {
      const nx = x0 + (x1 - x0) * 0.25;
      const ny = y0 + (y1 - y0) * 0.25;
      drawNumber(nx, ny, i + 1, color);
    }
  }

  if (moves.length > 0) {
    const first = moves[0];
    const last = moves[moves.length - 1];
    const [sx, sy] = worldToCanvas(first.start[0], first.start[1]);
    const [ex, ey] = worldToCanvas(last.end[0], last.end[1]);
    drawDot(sx, sy, 6, '#33cc66');
    drawDot(ex, ey, 6, '#cc3333');
    ctx.font = '9px monospace';
    ctx.fillStyle = '#33cc66';
    ctx.textAlign = 'left';
    ctx.fillText('START', sx + 8, sy - 6);
    ctx.fillStyle = '#cc3333';
    ctx.fillText('END', ex + 8, ey + 12);
  }
}

function drawInProgress() {
  const cpts = drawPts.map(([wx, wy]) => worldToCanvas(wx, wy));
  drawPolyline(cpts, '#4a9eff', 1.5, false, false);
  for (const [cx, cy] of cpts) drawDot(cx, cy, 3, '#4a9eff');
}

function drawHandles(pid) {
  const path = layer.source_paths.find(p => p.id === pid);
  if (!path) return;
  for (const [wx, wy] of (path.points || [])) {
    const [cx, cy] = worldToCanvas(wx, wy);
    ctx.save();
    ctx.strokeStyle = '#4a9eff';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Tool management
// ---------------------------------------------------------------------------

function setTool(t) {
  tool = t;
  drawPts = [];
  document.getElementById('tool-select').classList.toggle('active', t === 'select');
  document.getElementById('tool-draw').classList.toggle('active', t === 'draw');
  canvas.className = `tool-${t}`;
  updateHint();
  repaint();
}

function updateHint() {
  const hint = document.getElementById('hint');
  if (tool === 'draw') {
    hint.textContent = 'Click to place points. Double-click or Enter to finish. Press C to close path.';
  } else {
    hint.textContent = selectedId
      ? 'Drag nodes to edit. Click empty area to deselect.'
      : 'Click a path to select it.';
  }
}

// ---------------------------------------------------------------------------
// Canvas mouse events
// ---------------------------------------------------------------------------

let dragging = null;  // { pathId, ptIdx }
let lastClick = 0;

canvas.addEventListener('mousedown', onMouseDown);
canvas.addEventListener('mousemove', onMouseMove);
canvas.addEventListener('mouseup', onMouseUp);
canvas.addEventListener('dblclick', onDblClick);

function onMouseDown(e) {
  const [wx, wy] = canvasFromEvent(e);
  if (tool === 'draw') {
    // Check double-click handled by dblclick
    drawPts.push([wx, wy]);
    repaint();
    return;
  }
  if (tool === 'select') {
    // Try to start dragging a handle node
    const handle = findHandle(wx, wy);
    if (handle) {
      dragging = handle;
      return;
    }
    // Try to select a path
    const pid = hitTestPath(wx, wy);
    selectedId = pid;
    updatePathList();
    updatePropPanel();
    updateHint();
    repaint();
  }
}

function onMouseMove(e) {
  if (dragging) {
    const [wx, wy] = canvasFromEvent(e);
    const path = layer.source_paths.find(p => p.id === dragging.pathId);
    if (path) {
      path.points[dragging.ptIdx] = [wx, wy];
      if (path.type === 'RectanglePath' || path.type === 'CirclePath' || path.type === 'EllipsePath') {
        _syncPrimitiveFromPoints(path);
      }
      routeResult = null; // invalidate
      repaint();
    }
  }
}

function onMouseUp(e) {
  dragging = null;
}

function onDblClick(e) {
  if (tool === 'draw') {
    finishDraw(false);
  }
}

document.addEventListener('keydown', e => {
  if (e.key === 'c' || e.key === 'C') {
    if (tool === 'draw' && drawPts.length >= 3) finishDraw(true);
  }
  if (e.key === 'Enter') {
    if (tool === 'draw' && drawPts.length >= 2) finishDraw(false);
  }
  if (e.key === 'Escape') {
    if (tool === 'draw') { drawPts = []; repaint(); }
    selectedId = null;
    updatePathList();
    updatePropPanel();
    repaint();
  }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selectedId && document.activeElement === document.body) {
      deletePath(selectedId);
    }
  }
});

function canvasFromEvent(e) {
  const r = canvas.getBoundingClientRect();
  return canvasToWorld(e.clientX - r.left, e.clientY - r.top);
}

// ---------------------------------------------------------------------------
// Hit testing
// ---------------------------------------------------------------------------

const HIT_DIST = 12; // world units

function hitTestPath(wx, wy) {
  for (let i = layer.source_paths.length - 1; i >= 0; i--) {
    const p = layer.source_paths[i];
    const pts = p.points || [];
    for (let j = 0; j < pts.length - 1; j++) {
      if (distToSegment(wx, wy, pts[j], pts[j + 1]) < HIT_DIST) return p.id;
    }
    if (p.closed && pts.length >= 2) {
      if (distToSegment(wx, wy, pts[pts.length - 1], pts[0]) < HIT_DIST) return p.id;
    }
  }
  return null;
}

function findHandle(wx, wy) {
  if (!selectedId) return null;
  const path = layer.source_paths.find(p => p.id === selectedId);
  if (!path) return null;
  for (let i = 0; i < (path.points || []).length; i++) {
    const [px, py] = path.points[i];
    if (Math.hypot(wx - px, wy - py) < 10) {
      return { pathId: path.id, ptIdx: i };
    }
  }
  return null;
}

function distToSegment(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-10) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

// ---------------------------------------------------------------------------
// Drawing — finish path
// ---------------------------------------------------------------------------

function finishDraw(closed) {
  if (drawPts.length < 2) { drawPts = []; repaint(); return; }
  const pts = [...drawPts];
  // Remove last point if it's the same as second-to-last (double-click adds duplicate)
  if (pts.length >= 2) {
    const a = pts[pts.length - 1], b = pts[pts.length - 2];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 8) pts.pop();
  }
  if (pts.length < 2) { drawPts = []; repaint(); return; }

  const id = newId();
  layer.source_paths.push({
    id,
    type: 'ExplicitPath',
    label: `Path ${layer.source_paths.length + 1}`,
    closed,
    role: 'free',
    visible: true,
    points: pts,
    control_points: pts,
  });
  drawPts = [];
  selectedId = id;
  routeResult = null;
  updatePathList();
  updatePropPanel();
  updateHint();
  repaint();
}

// ---------------------------------------------------------------------------
// Primitives — add at canvas centre
// ---------------------------------------------------------------------------

const PRIM_DEFAULTS = {
  LinePath:      () => ({ start: [150, 200], end: [250, 200] }),
  CirclePath:    () => ({ cx: 200, cy: 200, radius: 60 }),
  EllipsePath:   () => ({ cx: 200, cy: 200, rx: 80, ry: 50, rotation: 0 }),
  RectanglePath: () => ({ x: 140, y: 140, w: 120, h: 120 }),
};

function addPrimitive(type) {
  const id = newId();
  const base = PRIM_DEFAULTS[type]();
  const path = {
    id,
    type,
    label: type.replace('Path', ''),
    closed: type !== 'LinePath',
    role: 'free',
    visible: true,
    ...base,
  };
  // Compute points for display
  _computePrimitivePoints(path);
  layer.source_paths.push(path);
  selectedId = id;
  routeResult = null;
  updatePathList();
  updatePropPanel();
  repaint();
}

function _computePrimitivePoints(path) {
  if (path.type === 'LinePath') {
    path.points = [path.start, path.end];
  } else if (path.type === 'CirclePath') {
    const n = 48;
    path.points = Array.from({ length: n }, (_, i) => {
      const a = 2 * Math.PI * i / n;
      return [path.cx + path.radius * Math.cos(a), path.cy + path.radius * Math.sin(a)];
    });
  } else if (path.type === 'EllipsePath') {
    const n = 48;
    const cr = Math.cos(path.rotation || 0), sr = Math.sin(path.rotation || 0);
    path.points = Array.from({ length: n }, (_, i) => {
      const a = 2 * Math.PI * i / n;
      const lx = path.rx * Math.cos(a), ly = path.ry * Math.sin(a);
      return [path.cx + lx * cr - ly * sr, path.cy + lx * sr + ly * cr];
    });
  } else if (path.type === 'RectanglePath') {
    path.points = [
      [path.x, path.y],
      [path.x + path.w, path.y],
      [path.x + path.w, path.y + path.h],
      [path.x, path.y + path.h],
    ];
  }
}

function _syncPrimitiveFromPoints(path) {
  // When a handle is dragged, sync back primitive parameters
  // (only for handles that directly correspond to parameters)
  if (path.type === 'LinePath') {
    if (path.points.length >= 2) {
      path.start = path.points[0];
      path.end = path.points[1];
    }
  } else if (path.type === 'RectanglePath') {
    // Top-left corner drag
    if (path.points.length >= 4) {
      path.x = path.points[0][0];
      path.y = path.points[0][1];
      path.w = path.points[1][0] - path.x;
      path.h = path.points[3][1] - path.y;
      _computePrimitivePoints(path);
    }
  } else if (path.type === 'CirclePath') {
    // Drag control points resamples — just update centre from point average
    // (simplified: don't change radius when dragging)
    _computePrimitivePoints(path);
  }
}

// ---------------------------------------------------------------------------
// Path list UI
// ---------------------------------------------------------------------------

function updatePathList() {
  const el = document.getElementById('path-list');
  el.innerHTML = '';
  for (const p of layer.source_paths) {
    const item = document.createElement('div');
    item.className = 'path-item' + (p.id === selectedId ? ' selected' : '');
    item.onclick = () => { selectedId = p.id; updatePathList(); updatePropPanel(); repaint(); };

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
    el.appendChild(item);
  }
}

// ---------------------------------------------------------------------------
// Property panel
// ---------------------------------------------------------------------------

function updatePropPanel() {
  const section = document.getElementById('path-props-section');
  const panel = document.getElementById('path-props');

  if (!selectedId) {
    section.style.display = 'none';
    return;
  }
  const path = layer.source_paths.find(p => p.id === selectedId);
  if (!path) { section.style.display = 'none'; return; }
  section.style.display = '';

  panel.innerHTML = '';
  addPropRow(panel, 'Label', 'text', path.label || '', v => {
    path.label = v; updatePathList();
  });
  addPropRow(panel, 'Role', 'select', path.role, v => {
    path.role = v; updatePathList(); repaint();
  }, ['free', 'outer', 'inner', 'lattice']);
  addPropRow(panel, 'Closed', 'checkbox', path.closed, v => {
    path.closed = v; routeResult = null; repaint();
  });

  // Primitive-specific fields
  if (path.type === 'CirclePath') {
    addPropRow(panel, 'CX', 'number', path.cx, v => { path.cx = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'CY', 'number', path.cy, v => { path.cy = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'Radius', 'number', path.radius, v => { path.radius = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
  }
  if (path.type === 'EllipsePath') {
    addPropRow(panel, 'CX', 'number', path.cx, v => { path.cx = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'CY', 'number', path.cy, v => { path.cy = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'RX', 'number', path.rx, v => { path.rx = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'RY', 'number', path.ry, v => { path.ry = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
  }
  if (path.type === 'RectanglePath') {
    addPropRow(panel, 'X', 'number', path.x, v => { path.x = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'Y', 'number', path.y, v => { path.y = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'W', 'number', path.w, v => { path.w = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'H', 'number', path.h, v => { path.h = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
  }
  if (path.type === 'LinePath') {
    addPropRow(panel, 'X0', 'number', path.start[0], v => { path.start[0] = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'Y0', 'number', path.start[1], v => { path.start[1] = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'X1', 'number', path.end[0], v => { path.end[0] = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
    addPropRow(panel, 'Y1', 'number', path.end[1], v => { path.end[1] = +v; _computePrimitivePoints(path); routeResult = null; repaint(); });
  }
}

function addPropRow(panel, label, type, value, onChange, options) {
  const row = document.createElement('div');
  row.className = 'prop-row';

  const lbl = document.createElement('span');
  lbl.className = 'prop-label';
  lbl.textContent = label;

  let input;
  if (type === 'select') {
    input = document.createElement('select');
    input.className = 'prop-input';
    for (const o of (options || [])) {
      const opt = document.createElement('option');
      opt.value = o; opt.textContent = o;
      if (o === value) opt.selected = true;
      input.appendChild(opt);
    }
    input.onchange = () => onChange(input.value);
  } else if (type === 'checkbox') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!value;
    input.onchange = () => onChange(input.checked);
  } else {
    input = document.createElement('input');
    input.type = type;
    input.className = 'prop-input';
    input.value = value;
    input.onchange = () => onChange(input.value);
  }

  row.append(lbl, input);
  panel.appendChild(row);
}

// ---------------------------------------------------------------------------
// Offset treatments UI
// ---------------------------------------------------------------------------

function updateOffsetList() {
  const el = document.getElementById('offset-list');
  el.innerHTML = '';
  for (const ot of layer.offset_treatments) {
    const block = document.createElement('div');
    block.className = 'treatment-block';

    block.innerHTML = `
      <div class="treatment-header">
        <span class="treatment-name">Offset</span>
        <button class="remove-btn" title="Remove">×</button>
      </div>`;

    block.querySelector('.remove-btn').onclick = () => {
      layer.offset_treatments = layer.offset_treatments.filter(x => x.id !== ot.id);
      routeResult = null;
      updateOffsetList();
      repaint();
    };

    const panel = document.createElement('div');
    panel.className = 'prop-panel';

    // Source path selector
    const srcRow = document.createElement('div'); srcRow.className = 'prop-row';
    const srcLbl = document.createElement('span'); srcLbl.className = 'prop-label'; srcLbl.textContent = 'Source';
    const srcSel = document.createElement('select'); srcSel.className = 'prop-input';
    for (const p of layer.source_paths) {
      const opt = document.createElement('option');
      opt.value = p.id; opt.textContent = p.label || p.id;
      if (p.id === ot.source_path_id) opt.selected = true;
      srcSel.appendChild(opt);
    }
    srcSel.onchange = () => { ot.source_path_id = srcSel.value; routeResult = null; repaint(); };
    srcRow.append(srcLbl, srcSel);
    panel.appendChild(srcRow);

    addPropRowTo(panel, 'Distance', 'number', ot.distance, v => { ot.distance = +v; routeResult = null; repaint(); });
    addPropRowTo(panel, 'Role', 'select', ot.role, v => { ot.role = v; routeResult = null; repaint(); }, ['inner', 'outer', 'free', 'lattice']);

    block.appendChild(panel);
    el.appendChild(block);
  }
}

function addOffset() {
  if (layer.source_paths.length === 0) { setStatus('Add at least one path first.'); return; }
  layer.offset_treatments.push({
    id: newId(),
    source_path_id: layer.source_paths[0].id,
    distance: -10,
    role: 'inner',
    label: '',
  });
  routeResult = null;
  updateOffsetList();
}

// ---------------------------------------------------------------------------
// Lattice UI
// ---------------------------------------------------------------------------

function updateLatticeList() {
  const el = document.getElementById('lattice-list');
  el.innerHTML = '';
  for (const li of layer.lattice_instances) {
    const block = document.createElement('div');
    block.className = 'treatment-block';

    const genInfo = generators[li.generator] || { parameters: [] };

    block.innerHTML = `
      <div class="treatment-header">
        <span class="treatment-name">${li.generator}</span>
        <button class="remove-btn" title="Remove">×</button>
      </div>`;

    block.querySelector('.remove-btn').onclick = () => {
      layer.lattice_instances = layer.lattice_instances.filter(x => x.id !== li.id);
      routeResult = null;
      updateLatticeList();
      repaint();
    };

    const panel = document.createElement('div');
    panel.className = 'prop-panel';

    // Path A / Path B selectors
    for (const [key, label] of [['path_a_id', 'Path A'], ['path_b_id', 'Path B']]) {
      const row = document.createElement('div'); row.className = 'prop-row';
      const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
      const sel = document.createElement('select'); sel.className = 'prop-input';
      for (const p of layer.source_paths) {
        const opt = document.createElement('option');
        opt.value = p.id; opt.textContent = p.label || p.id;
        if (p.id === li[key]) opt.selected = true;
        sel.appendChild(opt);
      }
      sel.onchange = () => { li[key] = sel.value; routeResult = null; repaint(); };
      row.append(lbl, sel);
      panel.appendChild(row);
    }

    // Generator parameters
    for (const param of genInfo.parameters) {
      addPropRowTo(panel, param.label, 'number', li.params[param.name] ?? param.default, v => {
        li.params[param.name] = +v;
        routeResult = null; repaint();
      });
    }

    // Variation buttons
    const varRow = document.createElement('div');
    varRow.className = 'variation-row';
    const varCount = 2; // default; updated after first route
    for (let i = 0; i < varCount; i++) {
      const btn = document.createElement('button');
      btn.className = 'var-btn' + (i === (li.variation_index || 0) ? ' active' : '');
      btn.textContent = `V${i}`;
      btn.onclick = () => {
        li.variation_index = i;
        routeResult = null;
        updateLatticeList();
        repaint();
      };
      varRow.appendChild(btn);
    }
    panel.appendChild(varRow);

    block.appendChild(panel);
    el.appendChild(block);
  }
}

function addLattice() {
  if (layer.source_paths.length < 2) { setStatus('Add at least two paths first.'); return; }
  const genName = Object.keys(generators)[0] || 'zigzag';
  const gen = generators[genName] || { parameters: [] };
  const params = {};
  for (const p of gen.parameters) params[p.name] = p.default;

  layer.lattice_instances.push({
    id: newId(),
    generator: genName,
    path_a_id: layer.source_paths[0].id,
    path_b_id: layer.source_paths[1] ? layer.source_paths[1].id : layer.source_paths[0].id,
    params,
    variation_index: 0,
    label: '',
  });
  routeResult = null;
  updateLatticeList();
}

// Helper for addPropRow that adds to an arbitrary element
function addPropRowTo(panel, label, type, value, onChange, options) {
  const row = document.createElement('div');
  row.className = 'prop-row';
  const lbl = document.createElement('span');
  lbl.className = 'prop-label';
  lbl.textContent = label;
  let input;
  if (type === 'select') {
    input = document.createElement('select');
    input.className = 'prop-input';
    for (const o of (options || [])) {
      const opt = document.createElement('option');
      opt.value = o; opt.textContent = o;
      if (o === value) opt.selected = true;
      input.appendChild(opt);
    }
    input.onchange = () => onChange(input.value);
  } else {
    input = document.createElement('input');
    input.type = type;
    input.className = 'prop-input';
    input.value = value;
    input.onchange = () => onChange(input.value);
  }
  row.append(lbl, input);
  panel.appendChild(row);
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

async function runRoute() {
  if (layer.source_paths.length === 0) { setStatus('No paths to route.'); return; }
  setStatus('Routing…');
  try {
    // Build server payload — convert local path format to model format
    const payload = buildPayload();
    const res = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
      setStatus('Route error: ' + data.error.split('\n').pop());
      console.error(data.error);
      return;
    }
    routeResult = data;
    updateMetrics(data);
    setStatus(`Routed: ${data.moves.length} moves, ${data.metrics.pct_printing}% printing`);
    document.getElementById('metrics-section').style.display = '';
    repaint();
  } catch (e) {
    setStatus('Route failed: ' + e.message);
  }
}

function buildPayload() {
  // Convert local path objects to model.py format
  const source_paths = layer.source_paths.map(p => {
    const d = { ...p };
    // control_points = editable points for ExplicitPath
    if (p.type === 'ExplicitPath' || !p.type) {
      d.control_points = p.points;
    }
    return d;
  });
  return {
    id: layer.id,
    label: layer.label,
    source_paths,
    offset_treatments: layer.offset_treatments,
    lattice_instances: layer.lattice_instances.map(li => ({
      ...li,
      generator: li.generator,
    })),
    constraints: layer.constraints,
  };
}

function updateMetrics(data) {
  const m = data.metrics;
  const g = data.graph;

  document.getElementById('m-runs').textContent = m.print_runs;
  const tEl = document.getElementById('m-travel');
  tEl.textContent = m.travel_moves;
  tEl.className = 'metric-value ' + (m.travel_moves === 0 ? 'good' : 'warn');

  const pEl = document.getElementById('m-pct');
  pEl.textContent = m.pct_printing + '%';
  pEl.className = 'metric-value ' + (m.pct_printing >= 95 ? 'good' : m.pct_printing >= 80 ? 'warn' : 'bad');

  document.getElementById('m-pdist').textContent = m.print_distance.toFixed(1);
  document.getElementById('m-tdist').textContent = m.travel_distance.toFixed(1);
  document.getElementById('m-rdist').textContent = (m.retrace_distance || 0).toFixed(1);

  document.getElementById('g-nodes').textContent = g.node_count;
  document.getElementById('g-edges').textContent = g.edge_count;
  document.getElementById('g-comps').textContent = g.component_count;
  document.getElementById('g-odd').textContent = g.odd_degree_nodes;

  const badgeEl = document.getElementById('euler-badge-container');
  let badge, cls;
  if (g.component_count > 1) {
    badge = `${g.component_count} components`; cls = 'multi';
  } else if (g.odd_degree_nodes === 0) {
    badge = 'Eulerian circuit'; cls = 'circuit';
  } else if (g.odd_degree_nodes === 2) {
    badge = 'Eulerian path'; cls = 'path';
  } else {
    badge = `${g.odd_degree_nodes} odd nodes`; cls = 'augmented';
  }
  badgeEl.innerHTML = `<span class="euler-badge ${cls}">${badge}</span>`;
}

// ---------------------------------------------------------------------------
// Toolpath overlay toggles
// ---------------------------------------------------------------------------

function toggleToolpath() {
  showToolpath = !showToolpath;
  const btn = document.getElementById('btn-toolpath');
  if (showToolpath) {
    btn.textContent = 'Toolpath ON';
    btn.classList.add('toggle-on');
  } else {
    btn.textContent = 'Toolpath OFF';
    btn.classList.remove('toggle-on');
  }
  repaint();
}

function toggleArrows() {
  showArrows = !showArrows;
  document.getElementById('btn-arrows').classList.toggle('toggle-on', showArrows);
  repaint();
}

function toggleNumbers() {
  showNumbers = !showNumbers;
  document.getElementById('btn-numbers').classList.toggle('toggle-on', showNumbers);
  repaint();
}

// ---------------------------------------------------------------------------
// Routing overrides
// ---------------------------------------------------------------------------

function onOverrideChange() {
  layer.constraints.reverse_direction = document.getElementById('override-reverse').checked;
  routeResult = null;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function deletePath(id) {
  layer.source_paths = layer.source_paths.filter(p => p.id !== id);
  layer.offset_treatments = layer.offset_treatments.filter(ot => ot.source_path_id !== id);
  layer.lattice_instances = layer.lattice_instances.filter(
    li => li.path_a_id !== id && li.path_b_id !== id
  );
  selectedId = null;
  routeResult = null;
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateLatticeList();
  repaint();
}

function clearAll() {
  layer.source_paths = [];
  layer.offset_treatments = [];
  layer.lattice_instances = [];
  layer.constraints = { start_path_id: null, start_t: null, reverse_direction: false, component_order: null };
  selectedId = null;
  routeResult = null;
  drawPts = [];
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateLatticeList();
  document.getElementById('metrics-section').style.display = 'none';
  repaint();
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

let _idCounter = 1;
function newId() {
  return 'p' + (_idCounter++);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  resizeCanvas();

  // Load generators
  try {
    const res = await fetch('/api/generators');
    const gens = await res.json();
    for (const g of gens) generators[g.name] = g;
  } catch (e) {
    console.warn('Could not load generators', e);
  }

  // Seed with Case D from toolpath_proto as default demonstration
  seedCaseD();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateLatticeList();
  repaint();
  setStatus('Ready. Draw paths or use primitives. Press Route to compute toolpath.');
}

function seedCaseD() {
  // Wall perimeter (closed) — points matching toolpath_proto Case D scaled to world coords
  // Original: 0..100 x 0..10 → scaled to world 400×400
  const scale = 3.0;
  const ox = 20, oy = 160;
  const perimPts = [
    [0,0],[25,0],[50,0],[75,0],[100,0],
    [100,10],[75,10],[50,10],[25,10],[0,10],
  ].map(([x, y]) => [ox + x * scale, oy + y * scale]);

  const webPts = [
    [25,10],[25,0],[50,10],[50,0],[75,10],[75,0],
  ].map(([x, y]) => [ox + x * scale, oy + y * scale]);

  layer.source_paths.push({
    id: newId(),
    type: 'ExplicitPath',
    label: 'Wall perimeter',
    closed: true,
    role: 'outer',
    visible: true,
    points: perimPts,
    control_points: perimPts,
  });
  layer.source_paths.push({
    id: newId(),
    type: 'ExplicitPath',
    label: 'Web',
    closed: false,
    role: 'lattice',
    visible: true,
    points: webPts,
    control_points: webPts,
  });
}

init();
