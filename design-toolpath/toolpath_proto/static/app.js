'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentCase = 'D';
let routeData = null;

const COLORS = {
  print:   '#4a9eff',
  travel:  '#ff6644',
  retrace: '#ffaa44',
  strand:  '#444',
  start:   '#33cc66',
  end:     '#cc3333',
  number:  '#ffffff',
};

// ---------------------------------------------------------------------------
// Canvas setup
// ---------------------------------------------------------------------------
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');

function resizeCanvas() {
  const area = canvas.parentElement;
  const size = Math.min(area.clientWidth - 40, area.clientHeight - 40, 700);
  canvas.width = size;
  canvas.height = size;
  if (routeData) render(routeData);
}

window.addEventListener('resize', resizeCanvas);

// ---------------------------------------------------------------------------
// Coordinate transform (world → canvas)
// ---------------------------------------------------------------------------
function makeTransform(moves, strands) {
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;

  const addPt = (x, y) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  };

  for (const m of moves) {
    addPt(m.start[0], m.start[1]);
    addPt(m.end[0], m.end[1]);
  }
  for (const s of strands) {
    for (const p of s.points) addPt(p[0], p[1]);
  }

  const pad = 50;
  const W = canvas.width - 2 * pad;
  const H = canvas.height - 2 * pad;
  const worldW = maxX - minX || 1;
  const worldH = maxY - minY || 1;
  const scale = Math.min(W / worldW, H / worldH);

  // Center the geometry
  const drawW = worldW * scale;
  const drawH = worldH * scale;
  const offX = pad + (W - drawW) / 2;
  const offY = pad + (H - drawH) / 2;

  return (x, y) => [
    offX + (x - minX) * scale,
    // Flip Y so that world Y+ is up on screen
    canvas.height - offY - (y - minY) * scale,
  ];
}

// ---------------------------------------------------------------------------
// Drawing helpers
// ---------------------------------------------------------------------------
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

function drawSegment(x0, y0, x1, y1, color, dashed, lineWidth) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth || 2;
  ctx.setLineDash(dashed ? [6, 4] : []);
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawNumber(x, y, n, color) {
  const s = String(n);
  const pad = 3;
  const w = s.length * 6 + pad * 2;
  const h = 13;
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
// Render
// ---------------------------------------------------------------------------
function render(data) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const { layer, moves } = data;
  const toCanvas = makeTransform(moves, layer.strands);

  // -- Draw strands (background geometry) --
  for (const strand of layer.strands) {
    const pts = strand.points;
    if (pts.length < 2) continue;
    ctx.save();
    ctx.strokeStyle = COLORS.strand;
    ctx.lineWidth = 3;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    const [sx, sy] = toCanvas(pts[0][0], pts[0][1]);
    ctx.moveTo(sx, sy);
    for (let i = 1; i < pts.length; i++) {
      const [px, py] = toCanvas(pts[i][0], pts[i][1]);
      ctx.lineTo(px, py);
    }
    if (strand.closed) ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }

  // -- Draw moves with direction arrows and sequence numbers --
  for (let i = 0; i < moves.length; i++) {
    const m = moves[i];
    const [x0, y0] = toCanvas(m.start[0], m.start[1]);
    const [x1, y1] = toCanvas(m.end[0], m.end[1]);

    const color = COLORS[m.kind] || COLORS.print;
    const dashed = m.kind === 'travel';
    const lw = m.kind === 'travel' ? 1.5 : 2.5;

    drawSegment(x0, y0, x1, y1, color, dashed, lw);

    // Arrow at midpoint
    const mx = (x0 + x1) / 2;
    const my = (y0 + y1) / 2;
    const angle = Math.atan2(y1 - y0, x1 - x0);
    drawArrowhead(mx, my, angle, 6, color);

    // Sequence number at 1/3 of the segment
    const nx = x0 + (x1 - x0) * 0.25;
    const ny = y0 + (y1 - y0) * 0.25;
    drawNumber(nx, ny, i + 1, color);
  }

  // -- Start and end markers --
  if (moves.length > 0) {
    const first = moves[0];
    const last = moves[moves.length - 1];
    const [sx, sy] = toCanvas(first.start[0], first.start[1]);
    const [ex, ey] = toCanvas(last.end[0], last.end[1]);

    drawDot(sx, sy, 7, COLORS.start);
    drawDot(ex, ey, 7, COLORS.end);

    // Labels
    ctx.font = '10px monospace';
    ctx.fillStyle = COLORS.start;
    ctx.textAlign = 'left';
    ctx.fillText('START', sx + 10, sy - 8);
    ctx.fillStyle = COLORS.end;
    ctx.fillText('END', ex + 10, ey + 14);
  }
}

// ---------------------------------------------------------------------------
// Metrics display
// ---------------------------------------------------------------------------
function updateMetrics(data) {
  const m = data.metrics;
  const g = data.graph;

  document.getElementById('m-runs').textContent = m.print_runs;
  document.getElementById('m-travel').textContent = m.travel_moves;

  const pctEl = document.getElementById('m-pct');
  pctEl.textContent = m.pct_printing + '%';
  pctEl.className = 'metric-value ' +
    (m.pct_printing >= 95 ? 'good' : m.pct_printing >= 80 ? 'warn' : 'bad');

  const travelEl = document.getElementById('m-travel');
  travelEl.className = 'metric-value ' + (m.travel_moves === 0 ? 'good' : 'warn');

  document.getElementById('m-pdist').textContent = m.print_distance.toFixed(1);
  document.getElementById('m-tdist').textContent = m.travel_distance.toFixed(1);
  document.getElementById('m-rdist').textContent = m.retrace_distance.toFixed(1);

  document.getElementById('g-nodes').textContent = g.node_count;
  document.getElementById('g-edges').textContent = g.edge_count;
  document.getElementById('g-comps').textContent = g.component_count;
  document.getElementById('g-odd').textContent = g.odd_degree_nodes;

  // Euler badge
  const badgeEl = document.getElementById('euler-badge-container');
  let badge, cls, title;
  if (g.component_count > 1) {
    badge = `${g.component_count} components`;
    cls = 'multi';
    title = 'Multiple disconnected components — travel moves required between them';
  } else if (g.odd_degree_nodes === 0) {
    badge = 'Eulerian circuit';
    cls = 'circuit';
    title = 'All nodes even degree — single continuous loop possible';
  } else if (g.odd_degree_nodes === 2) {
    badge = 'Eulerian path';
    cls = 'path';
    title = '2 odd-degree nodes — single continuous path possible (no circuit)';
  } else {
    badge = `${g.odd_degree_nodes} odd nodes — augmented`;
    cls = 'augmented';
    title = `${g.odd_degree_nodes} odd-degree nodes — travel edges added to minimise discontinuities`;
  }
  badgeEl.innerHTML = `<span class="euler-badge ${cls}" title="${title}">${badge}</span>`;
}

// ---------------------------------------------------------------------------
// Load and display a test case
// ---------------------------------------------------------------------------
async function loadCase(caseId) {
  currentCase = caseId;

  // Update button states
  for (const btn of document.querySelectorAll('.case-btn')) {
    btn.classList.toggle('active', btn.dataset.case === caseId);
  }

  const res = await fetch(`/api/route/${caseId}`, { method: 'POST' });
  const data = await res.json();
  routeData = data;

  document.getElementById('case-label').textContent = data.layer.label;
  render(data);
  updateMetrics(data);
}

// ---------------------------------------------------------------------------
// Build case buttons from API
// ---------------------------------------------------------------------------
async function init() {
  resizeCanvas();

  const res = await fetch('/api/cases');
  const cases = await res.json();

  const container = document.getElementById('case-buttons');
  for (const c of cases) {
    const btn = document.createElement('button');
    btn.className = 'case-btn';
    btn.dataset.case = c.id;
    btn.textContent = c.id;
    btn.title = c.label;
    btn.addEventListener('click', () => loadCase(c.id));
    container.appendChild(btn);
  }

  await loadCase(currentCase);
}

init();
