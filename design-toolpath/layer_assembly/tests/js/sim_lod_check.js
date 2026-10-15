// Runs the Print Simulation's drag / scrub LOD (static/simulation.js:
// _simLodMask + the drawing rule of _simDrawItems) on streams given on stdin
// and reports, per stream, the tolerance used, the vertices kept and the
// largest distance from ANY vertex to the drawn polyline. Used by
// test_simulation_lod.py (2026-10-15 corner-cutting fix).
const fs = require('fs');
const stub = new Proxy(function () {}, { get: () => stub, apply: () => stub });
global.window = stub; global.document = stub; global.requestAnimationFrame = () => 0;
const src = fs.readFileSync(process.argv[2], 'utf8');
const api = new Function('window', 'document', src +
  '\n;return { _simLodMask, SIM_LOD_VERTICES, SIM_LOD_TOLERANCE_IN, SIM_LOD_MAX_TOLERANCE_IN };')(stub, stub);
const streams = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = streams.map(st => {
  const xy = Float64Array.from(st.xy), off = Int32Array.from(st.off), n = Int32Array.from(st.n);
  const D = { xy, off, n, N: off.length, V: xy.length / 2 };
  const t = Date.now();
  api._simLodMask(D);
  const ms = Date.now() - t;
  let worst = 0, corners = 0;
  for (let i = 0; i < D.N; i++) {
    const o = off[i], m = n[i];
    // the drawn polyline of the item = vertices with mask 1 (first / last always)
    let a = o;
    for (let k = o + 1; k < o + m; k++) {
      if (!D.lodMask[k]) continue;
      for (let q = a + 1; q < k; q++) {          // every skipped vertex vs the drawn chord a→k
        const ax = xy[2 * a], ay = xy[2 * a + 1], dx = xy[2 * k] - ax, dy = xy[2 * k + 1] - ay, l2 = dx * dx + dy * dy;
        let tt = l2 ? ((xy[2 * q] - ax) * dx + (xy[2 * q + 1] - ay) * dy) / l2 : 0;
        tt = Math.max(0, Math.min(1, tt));
        worst = Math.max(worst, Math.hypot(xy[2 * q] - ax - tt * dx, xy[2 * q + 1] - ay - tt * dy));
      }
      a = k;
    }
  }
  for (const c of (st.corners || [])) if (D.lodMask[c]) corners++;
  return { tol: D.lodTol, kept: D.lodKept, V: D.V, worst, corners_kept: corners, ms,
           budget: api.SIM_LOD_VERTICES, base_tol: api.SIM_LOD_TOLERANCE_IN, max_tol: api.SIM_LOD_MAX_TOLERANCE_IN };
});
process.stdout.write(JSON.stringify(out));
