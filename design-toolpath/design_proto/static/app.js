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
let _refreshTimer = null;

// Drag state
let dragging = null;         // { pathId, handleKey, originalState }
let bodyDragging = null;     // { pathId, startWX, startWY, originalState }

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

let generators = {};

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

function worldToCanvas(x, y) {
  const s = canvas.width / WORLD;
  return [x * s, (WORLD - y) * s];
}

function canvasToWorld(cx, cy) {
  const s = WORLD / canvas.width;
  return [cx * s, WORLD - cy * s];
}

// ---------------------------------------------------------------------------
// Semantic handle system
// ---------------------------------------------------------------------------

function getHandles(path) {
  switch (path.type) {
    case 'CirclePath':
      return [
        { key: 'center', wx: path.cx, wy: path.cy, shape: 'cross' },
        { key: 'radius', wx: path.cx + path.radius, wy: path.cy, shape: 'square' },
      ];
    case 'EllipsePath':
      return [
        { key: 'center', wx: path.cx, wy: path.cy, shape: 'cross' },
        { key: 'rx', wx: path.cx + path.rx, wy: path.cy, shape: 'square' },
        { key: 'ry', wx: path.cx, wy: path.cy + path.ry, shape: 'square' },
      ];
    case 'LinePath':
      return [
        { key: 'start', wx: path.start[0], wy: path.start[1], shape: 'point' },
        { key: 'end',   wx: path.end[0],   wy: path.end[1],   shape: 'point' },
      ];
    case 'RectanglePath':
      return [
        { key: 'tl', wx: path.x,           wy: path.y,           shape: 'square' },
        { key: 'tr', wx: path.x + path.w,  wy: path.y,           shape: 'square' },
        { key: 'br', wx: path.x + path.w,  wy: path.y + path.h,  shape: 'square' },
        { key: 'bl', wx: path.x,           wy: path.y + path.h,  shape: 'square' },
      ];
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
      if (handleKey === 'rx') { path.rx = Math.max(1, Math.abs(wx - path.cx)); }
      if (handleKey === 'ry') { path.ry = Math.max(1, Math.abs(wy - path.cy)); }
      break;
    case 'LinePath':
      if (handleKey === 'start') { path.start = [wx, wy]; }
      if (handleKey === 'end')   { path.end   = [wx, wy]; }
      break;
    case 'RectanglePath': {
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
}

function capturePathState(path) {
  const s = {
    type: path.type,
    cx: path.cx, cy: path.cy, radius: path.radius,
    rx: path.rx, ry: path.ry, rotation: path.rotation,
    x: path.x, y: path.y, w: path.w, h: path.h,
  };
  if (path.start) s.start = [...path.start];
  if (path.end)   s.end   = [...path.end];
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

function drawSeamMarker(cx, cy) {
  const r = 5;
  ctx.save();
  ctx.strokeStyle = '#ffdd00';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx + r, cy);
  ctx.lineTo(cx, cy + r);
  ctx.lineTo(cx - r, cy);
  ctx.closePath();
  ctx.stroke();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// Repaint
// ---------------------------------------------------------------------------

function repaint() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawGrid();
  drawEffectivePaths();
  if (showToolpath && routeResult) {
    if (playbackPos > 0.0 || playbackPlaying) {
      drawToolpathWithPlayback(routeResult.moves);
    } else {
      drawToolpath(routeResult.moves);
    }
  }
  if (showDimensions) drawDimensions();
  if (tool === 'draw' && drawPts.length > 0) drawInProgress();
  if (selectedId) drawHandles(selectedId);
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

function drawEffectivePaths() {
  for (const p of layer.source_paths) {
    if (!p.visible) continue;
    const pts = _pathCanvasPts(p);
    const isSel = p.id === selectedId;
    const color = isSel ? '#ffffff' : (ROLE_COLORS[p.role] || '#aaa');
    drawPolyline(pts, color, isSel ? 2.5 : 1.8, false, p.closed);
    if (!p.closed && pts.length >= 2) {
      drawDot(pts[0][0], pts[0][1], 3, '#666');
      drawDot(pts[pts.length - 1][0], pts[pts.length - 1][1], 3, '#666');
    }
  }

  for (const p of derivedPaths) {
    const pts = _pathCanvasPts(p);
    if (pts.length < 2) continue;
    const color = ROLE_COLORS[p.role] || '#aa88ff';
    drawPolyline(pts, color, 1.5, p.role === 'lattice', p.closed);
  }
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

  // Pass 1: draw all move lines
  for (const m of moves) {
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

    // Seam marker where a closed run rejoins its start
    const dx = run.endPos[0] - run.startPos[0];
    const dy = run.endPos[1] - run.startPos[1];
    if (Math.hypot(dx, dy) < 3) {
      const [sx, sy] = worldToCanvas(run.startPos[0], run.startPos[1]);
      drawSeamMarker(sx, sy);
    }
  });

  // Start / end dots
  const first = moves[0], last = moves[moves.length - 1];
  const [sx, sy] = worldToCanvas(first.start[0], first.start[1]);
  const [ex, ey] = worldToCanvas(last.end[0], last.end[1]);
  drawDot(sx, sy, 6, '#33cc66');
  drawDot(ex, ey, 6, '#cc3333');
  ctx.font = '9px monospace';
  ctx.fillStyle = '#33cc66'; ctx.textAlign = 'left';
  ctx.fillText('START', sx + 8, sy - 6);
  ctx.fillStyle = '#cc3333';
  ctx.fillText('END', ex + 8, ey + 12);
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

  // Pass 1: all moves dim (future / unprinted)
  ctx.save();
  ctx.globalAlpha = 0.18;
  for (const m of moves) _drawMoveLine(m);
  ctx.restore();

  // Pass 2: printed portion at full opacity
  for (const { ds, de, m } of _routeCumDists) {
    if (ds >= targetDist) break;
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
      if (Math.hypot(run.endPos[0]-run.startPos[0], run.endPos[1]-run.startPos[1]) < 3) {
        const [sx,sy] = worldToCanvas(run.startPos[0], run.startPos[1]);
        drawSeamMarker(sx, sy);
      }
    });
  }

  // Start / end dots
  const first = moves[0], last = moves[moves.length - 1];
  const [sx, sy] = worldToCanvas(first.start[0], first.start[1]);
  const [ex, ey] = worldToCanvas(last.end[0], last.end[1]);
  drawDot(sx, sy, 6, '#33cc66');
  drawDot(ex, ey, 6, '#cc3333');

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

const SNAP_RADIUS = 12; // world inches

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
  for (const h of getHandles(path)) {
    drawHandle(h.wx, h.wy, h.shape || 'point');
  }
  if (path.type === 'CirclePath') {
    const [cx, cy] = worldToCanvas(path.cx, path.cy);
    const [rx, ry] = worldToCanvas(path.cx + path.radius, path.cy);
    ctx.save();
    ctx.strokeStyle = 'rgba(74,158,255,0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(rx, ry); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Tool management
// ---------------------------------------------------------------------------

function setTool(t) {
  tool = t;
  drawPts = [];
  document.getElementById('tool-edit').classList.toggle('active', t === 'edit');
  document.getElementById('tool-draw').classList.toggle('active', t === 'draw');
  canvas.className = `tool-${t}`;
  updateHint();
  repaint();
}

function updateHint() {
  const hint = document.getElementById('hint');
  if (tool === 'draw') {
    hint.textContent = 'Click to place control points. Click start point (≥3 pts) to close. Double-click or Enter to finish open path.';
    return;
  }
  if (!selectedId) {
    hint.textContent = 'Click a path to select and edit it. Add primitives from the toolbar or use Draw Path.';
    return;
  }
  const path = layer.source_paths.find(p => p.id === selectedId);
  if (!path) { hint.textContent = ''; return; }
  switch (path.type) {
    case 'CirclePath':
      hint.textContent = 'Drag center ✛ to move. Drag □ handle to resize. Edit values in sidebar.'; break;
    case 'EllipsePath':
      hint.textContent = 'Drag center ✛ to move. Drag □ rx/ry handles to resize.'; break;
    case 'RectanglePath':
      hint.textContent = 'Drag □ corner handles to resize. Drag body to move.'; break;
    case 'LinePath':
      hint.textContent = 'Drag ● endpoints to reshape. Drag body to move.'; break;
    default:
      hint.textContent = 'Drag ● control points to reshape. Drag path body to move. Del to delete.';
  }
}

// ---------------------------------------------------------------------------
// Canvas mouse events
// ---------------------------------------------------------------------------

canvas.addEventListener('mousedown', onMouseDown);
canvas.addEventListener('mousemove', onMouseMove);
document.addEventListener('mouseup', onMouseUp);
canvas.addEventListener('dblclick', onDblClick);

function onMouseDown(e) {
  const [wx, wy] = canvasFromEvent(e);

  if (tool === 'draw') {
    // Snap-to-first: if ≥3 points placed and near first point, close
    if (drawPts.length >= 3) {
      const [fx, fy] = drawPts[0];
      if (Math.hypot(wx - fx, wy - fy) < SNAP_RADIUS) {
        finishDraw(true);
        return;
      }
    }
    drawPts.push([wx, wy]);
    repaint();
    return;
  }

  if (tool === 'edit') {
    // 1. Handle on selected path takes priority
    if (selectedId) {
      const path = layer.source_paths.find(p => p.id === selectedId);
      if (path) {
        const h = findHandle(path, wx, wy);
        if (h) {
          dragging = { pathId: selectedId, handleKey: h.key,
                       originalState: capturePathState(path) };
          return;
        }
      }
    }

    // 2. Hit test any path
    const pid = hitTestPath(wx, wy);
    if (pid) {
      const switching = pid !== selectedId;
      selectedId = pid;
      const path = layer.source_paths.find(p => p.id === pid);
      if (switching) {
        updatePathList();
        updatePropPanel();
        updateHint();
      }
      if (path) {
        bodyDragging = { pathId: pid, startWX: wx, startWY: wy,
                         originalState: capturePathState(path) };
      }
      repaint();
      return;
    }

    // 3. Empty click — deselect
    if (selectedId) {
      selectedId = null;
      updatePathList();
      updatePropPanel();
      updateHint();
      repaint();
    }
  }
}

function onMouseMove(e) {
  _mousePosW = canvasFromEvent(e);

  if (dragging) {
    const [wx, wy] = _mousePosW;
    const path = layer.source_paths.find(p => p.id === dragging.pathId);
    if (path) {
      applyHandleDrag(path, dragging.handleKey, wx, wy, dragging.originalState);
      syncPropPanel(path);
      routeResult = null;
      repaint();
    }
    return;
  }
  if (bodyDragging) {
    const [wx, wy] = _mousePosW;
    const dx = wx - bodyDragging.startWX;
    const dy = wy - bodyDragging.startWY;
    if (Math.hypot(dx, dy) < 1) return;
    const path = layer.source_paths.find(p => p.id === bodyDragging.pathId);
    if (path) {
      applyBodyDrag(path, dx, dy, bodyDragging.originalState);
      syncPropPanel(path);
      routeResult = null;
      repaint();
    }
    return;
  }

  // During draw: repaint to update snap preview
  if (tool === 'draw' && drawPts.length >= 3) repaint();
}

function onMouseUp() {
  const wasDragging = dragging !== null || bodyDragging !== null;
  dragging = null;
  bodyDragging = null;
  if (wasDragging) scheduleRefresh();
}

function onDblClick(e) {
  if (tool === 'draw') finishDraw(false);
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
    else { selectedId = null; updatePathList(); updatePropPanel(); updateHint(); repaint(); }
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && document.activeElement === document.body) {
    if (selectedId) deletePath(selectedId);
  }
});

function canvasFromEvent(e) {
  const r = canvas.getBoundingClientRect();
  return canvasToWorld(e.clientX - r.left, e.clientY - r.top);
}

// ---------------------------------------------------------------------------
// Hit testing
// ---------------------------------------------------------------------------

const HIT_DIST = 10; // world inches

function hitTestPath(wx, wy) {
  for (let i = layer.source_paths.length - 1; i >= 0; i--) {
    const p = layer.source_paths[i];
    const pts = p.points || [];
    for (let j = 0; j < pts.length - 1; j++) {
      if (distToSeg(wx, wy, pts[j], pts[j + 1]) < HIT_DIST) return p.id;
    }
    if (p.closed && pts.length >= 2) {
      if (distToSeg(wx, wy, pts[pts.length - 1], pts[0]) < HIT_DIST) return p.id;
    }
  }
  return null;
}

function findHandle(path, wx, wy) {
  for (const h of getHandles(path)) {
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
    label: `Path ${layer.source_paths.length + 1}`,
    closed, role: 'free', visible: true,
    points: pts, control_points: pts,
  });
  selectedId = id;
  routeResult = null;
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateLatticeList();
  setTool('edit'); // auto-return to edit (also calls updateHint + repaint)
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

function addPrimitive(type) {
  const id = newId();
  const path = {
    id, type,
    label: type.replace('Path', ''),
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
  updateLatticeList();
  setTool('edit'); // auto-return to edit
}

function _computePrimitivePoints(path) {
  if (path.type === 'LinePath') {
    path.points = [path.start, path.end];
  } else if (path.type === 'CirclePath') {
    const n = 64;
    path.points = Array.from({ length: n }, (_, i) => {
      const a = 2 * Math.PI * i / n;
      return [path.cx + path.radius * Math.cos(a), path.cy + path.radius * Math.sin(a)];
    });
  } else if (path.type === 'EllipsePath') {
    const n = 64;
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
  } else {
    path.points = path.control_points || path.points;
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
    item.onclick = () => { selectedId = p.id; updatePathList(); updatePropPanel(); updateHint(); repaint(); };

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
  const panel   = document.getElementById('path-props');

  if (!selectedId) { section.style.display = 'none'; return; }
  const path = layer.source_paths.find(p => p.id === selectedId);
  if (!path) { section.style.display = 'none'; return; }
  section.style.display = '';
  panel.innerHTML = '';

  addPropRowText(panel, 'Label', path.label || '', v => { path.label = v; updatePathList(); });
  addPropRowCheck(panel, 'Closed', path.closed, v => {
    path.closed = v; routeResult = null; scheduleRefresh(); repaint();
  });

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
      break;
    case 'LinePath':
      addPropRowNum(panel, 'Start X', 'x0', path.start[0], v => { path.start[0] = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'Start Y', 'y0', path.start[1], v => { path.start[1] = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'End X',   'x1', path.end[0],   v => { path.end[0]   = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      addPropRowNum(panel, 'End Y',   'y1', path.end[1],   v => { path.end[1]   = v; _computePrimitivePoints(path); scheduleRefresh(); repaint(); });
      break;
  }

  // Delete button
  const delBtn = document.createElement('button');
  delBtn.className = 'add-btn';
  delBtn.style.cssText = 'margin-top:10px; color:var(--bad); border-color:#553333;';
  delBtn.textContent = '× Delete path';
  delBtn.onclick = () => deletePath(path.id);
  panel.appendChild(delBtn);
}

function addPropRowNum(panel, label, fieldKey, value, onChange) {
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
  unit.textContent = 'in';
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
    const nm = document.createElement('span'); nm.className = 'treatment-name'; nm.textContent = 'Wall Offset';
    const rm = document.createElement('button'); rm.className = 'remove-btn'; rm.textContent = '×';
    rm.onclick = () => {
      const removedId = ot.id;
      layer.offset_treatments = layer.offset_treatments.filter(x => x.id !== removedId);
      layer.lattice_instances = layer.lattice_instances.filter(
        li => li.path_a_id !== removedId && li.path_b_id !== removedId
      );
      routeResult = null; scheduleRefresh(); updateOffsetList(); updateLatticeList(); repaint();
    };
    hdr.append(nm, rm);
    block.appendChild(hdr);

    const panel = document.createElement('div');
    panel.className = 'prop-panel';

    // Source selector
    const srcRow = document.createElement('div'); srcRow.className = 'prop-row';
    const srcLbl = document.createElement('span'); srcLbl.className = 'prop-label'; srcLbl.textContent = 'Source';
    const srcSel = document.createElement('select'); srcSel.className = 'prop-input';
    for (const p of layer.source_paths) {
      const opt = document.createElement('option');
      opt.value = p.id; opt.textContent = p.label || p.id;
      if (p.id === ot.source_path_id) opt.selected = true;
      srcSel.appendChild(opt);
    }
    srcSel.onchange = () => {
      ot.source_path_id = srcSel.value;
      routeResult = null; scheduleRefresh(); updateOffsetList(); repaint();
    };
    srcRow.append(srcLbl, srcSel);
    panel.appendChild(srcRow);

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
  layer.offset_treatments.push({
    id: newId(),
    source_path_id: layer.source_paths[0].id,
    distance: 10,
    direction: 'inside',
    role: 'inner',
    label: '',
  });
  routeResult = null;
  scheduleRefresh();
  updateOffsetList();
}

// ---------------------------------------------------------------------------
// Connecting geometry (lattice) UI
// ---------------------------------------------------------------------------

function allBoundaries() {
  const items = [];
  for (const p of layer.source_paths) {
    items.push({ id: p.id, label: p.label || p.id });
  }
  for (const ot of layer.offset_treatments) {
    const src = layer.source_paths.find(p => p.id === ot.source_path_id);
    const srcLabel = src ? (src.label || src.id) : ot.source_path_id;
    const dir = ot.direction || 'inside';
    const dirCap = dir.charAt(0).toUpperCase() + dir.slice(1);
    const dist = ot.distance != null ? ot.distance : 10;
    items.push({ id: ot.id, label: `${dirCap} offset of ${srcLabel} — ${dist.toFixed(0)} in` });
  }
  return items;
}

function updateLatticeList() {
  const el = document.getElementById('lattice-list');
  el.innerHTML = '';
  for (const li of layer.lattice_instances) {
    const block = document.createElement('div');
    block.className = 'treatment-block';

    const genInfo = generators[li.generator] || { parameters: [] };

    const hdr = document.createElement('div');
    hdr.className = 'treatment-header';
    const nm = document.createElement('span'); nm.className = 'treatment-name';
    nm.textContent = li.generator.charAt(0).toUpperCase() + li.generator.slice(1);
    const rm = document.createElement('button'); rm.className = 'remove-btn'; rm.textContent = '×';
    rm.onclick = () => {
      layer.lattice_instances = layer.lattice_instances.filter(x => x.id !== li.id);
      routeResult = null; scheduleRefresh(); updateLatticeList(); repaint();
    };
    hdr.append(nm, rm);
    block.appendChild(hdr);

    const panel = document.createElement('div');
    panel.className = 'prop-panel';

    // Boundary A and Boundary B selectors — includes source paths and offset-derived paths
    const boundaries = allBoundaries();
    for (const [key, label] of [['path_a_id', 'Boundary A'], ['path_b_id', 'Boundary B']]) {
      const row = document.createElement('div'); row.className = 'prop-row';
      const lbl = document.createElement('span'); lbl.className = 'prop-label'; lbl.textContent = label;
      const sel = document.createElement('select'); sel.className = 'prop-input';
      for (const { id, label: bLabel } of boundaries) {
        const opt = document.createElement('option');
        opt.value = id; opt.textContent = bLabel;
        if (id === li[key]) opt.selected = true;
        sel.appendChild(opt);
      }
      sel.onchange = () => { li[key] = sel.value; routeResult = null; scheduleRefresh(); repaint(); };
      row.append(lbl, sel);
      panel.appendChild(row);
    }

    // Generator type selector
    const genRow = document.createElement('div'); genRow.className = 'prop-row';
    const genLbl = document.createElement('span'); genLbl.className = 'prop-label'; genLbl.textContent = 'Pattern';
    const genSel = document.createElement('select'); genSel.className = 'prop-input';
    for (const name of Object.keys(generators)) {
      const opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      if (name === li.generator) opt.selected = true;
      genSel.appendChild(opt);
    }
    genSel.onchange = () => {
      li.generator = genSel.value;
      const newGen = generators[li.generator] || { parameters: [] };
      for (const p of newGen.parameters) {
        if (!(p.name in li.params)) li.params[p.name] = p.default;
      }
      li.variation_index = 0;
      routeResult = null; scheduleRefresh(); updateLatticeList(); repaint();
    };
    genRow.append(genLbl, genSel);
    panel.appendChild(genRow);

    for (const param of genInfo.parameters) {
      addPropRowNumTo(panel, param.label,
        li.params[param.name] ?? param.default,
        v => { li.params[param.name] = v; routeResult = null; scheduleRefresh(); repaint(); },
        getParamUnit(param.name));
    }

    // Variation buttons
    const varLabel = document.createElement('div');
    varLabel.style.cssText = 'color:#555;font-size:10px;margin-top:4px;';
    varLabel.textContent = 'Variation';
    panel.appendChild(varLabel);
    const varRow = document.createElement('div');
    varRow.className = 'variation-row';
    const varCount = genInfo.variation_count || 2;
    for (let i = 0; i < varCount; i++) {
      const btn = document.createElement('button');
      btn.className = 'var-btn' + (i === (li.variation_index || 0) ? ' active' : '');
      btn.textContent = `V${i + 1}`;
      btn.onclick = () => {
        li.variation_index = i;
        routeResult = null; scheduleRefresh(); updateLatticeList(); repaint();
      };
      varRow.appendChild(btn);
    }
    panel.appendChild(varRow);

    block.appendChild(panel);
    el.appendChild(block);
  }
}

function addLattice() {
  const boundaries = allBoundaries();
  if (boundaries.length < 2) {
    setStatus('Add at least two paths (or a path and an offset) first.');
    return;
  }
  const genName = Object.keys(generators)[0] || 'zigzag';
  const gen = generators[genName] || { parameters: [] };
  const params = {};
  for (const p of gen.parameters) params[p.name] = p.default;
  layer.lattice_instances.push({
    id: newId(),
    generator: genName,
    path_a_id: boundaries[0].id,
    path_b_id: boundaries[1].id,
    params,
    variation_index: 0,
    label: '',
  });
  routeResult = null;
  scheduleRefresh();
  updateLatticeList();
}

function getParamUnit(paramName) {
  const noUnit = ['segments', 'connect_ends', 'cycles'];
  return noUnit.includes(paramName) ? '' : 'in';
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

function addPropRowSelectTo(panel, label, value, options, onChange) {
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

// ---------------------------------------------------------------------------
// Live derived-path refresh + auto-routing
// ---------------------------------------------------------------------------

function scheduleRefresh() {
  clearTimeout(_refreshTimer);
  if (layer.source_paths.length === 0) {
    derivedPaths = [];
    routeResult = null;
    repaint();
    return;
  }
  if (showToolpath) {
    // Auto-route: also updates derivedPaths
    _refreshTimer = setTimeout(runRoute, 200);
  } else {
    const hasDerived = layer.offset_treatments.length > 0 || layer.lattice_instances.length > 0;
    if (hasDerived) {
      _refreshTimer = setTimeout(fetchEffectivePaths, 120);
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
    const res = await fetch('/api/effective_paths', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPayload()),
    });
    const data = await res.json();
    if (data.paths) {
      const sourceIds = new Set(layer.source_paths.map(p => p.id));
      derivedPaths = data.paths.filter(p => !sourceIds.has(p.id));
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

  document.getElementById('m-tdist').textContent = m.travel_distance.toFixed(1) + ' in';

  const pEl = document.getElementById('m-pct');
  pEl.textContent = m.pct_printing + '%';
  pEl.className = 'metric-value ' + (m.pct_printing >= 95 ? 'good' : m.pct_printing >= 80 ? 'warn' : 'bad');

  document.getElementById('m-pdist').textContent = m.print_distance.toFixed(1) + ' in';
  document.getElementById('m-rdist').textContent = (m.retrace_distance || 0).toFixed(1) + ' in';

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
  } else {
    badge = 'Requires backtracking';
    cls = 'augmented';
    title = 'Some geometry must be retraced to maintain continuity';
  }
  badgeEl.innerHTML = `<span class="euler-badge ${cls}" title="${title}">${badge}</span>`;

  const runStr = `${m.print_runs} run${m.print_runs === 1 ? '' : 's'}`;
  const travelStr = m.travel_moves === 0 ? 'no travel' : `${m.travel_moves} travel`;
  setStatus(`${runStr}, ${travelStr}, ${m.pct_printing}% printing`);
}

// ---------------------------------------------------------------------------
// Dimensions overlay — purely visual, no effect on geometry or routing
// ---------------------------------------------------------------------------

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

  // Derived offset paths (not lattice or caps)
  for (const dp of derivedPaths) {
    if (dp.role === 'lattice' || dp.role === 'cap') continue;
    _drawDerivedDim(dp);
  }

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
  if (showToolpath) scheduleRefresh();
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
  scheduleRefresh();
  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateLatticeList();
  updateHint();
  repaint();
}

function clearAll() {
  layer.source_paths = [];
  layer.offset_treatments = [];
  layer.lattice_instances = [];
  layer.constraints = { start_path_id: null, start_t: null,
                         reverse_direction: false, component_order: null };
  selectedId = null;
  routeResult = null;
  derivedPaths = [];
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
  updateLatticeList();
  updateHint();
  document.getElementById('metrics-section').style.display = 'none';
  repaint();
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

let _idCounter = 1;
function newId() { return 'p' + (_idCounter++); }

// ---------------------------------------------------------------------------
// Init — blank canvas, auto-routes when first path is added
// ---------------------------------------------------------------------------

async function init() {
  resizeCanvas();

  try {
    const res = await fetch('/api/generators');
    const gens = await res.json();
    for (const g of gens) {
      generators[g.name] = g;
      g.variation_count = 2;
    }
  } catch (e) {
    console.warn('Could not load generators', e);
  }

  updatePathList();
  updatePropPanel();
  updateOffsetList();
  updateLatticeList();
  updateHint();
  repaint();
  setStatus('Ready — add paths from the toolbar or use Draw Path. Toolpath auto-routes when enabled.');
}

init();
